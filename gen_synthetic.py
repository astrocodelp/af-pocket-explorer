"""Generate SYNTHETIC demo proteins, made-up pLDDT traces and fake pocket predictions.

Nothing here is a real protein, a real AlphaFold model or a real fpocket run.
Coordinates are built from ideal helix geometry (NeRF) and spline loops; the
pLDDT traces and druggability scores are designed to show four typical
situations (see SCENARIOS). Output: data/seed/*.json (plain, made-up data),
which server.py encrypts into its store on first start.
"""
import json
import math
import os
import random

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "seed")

HYDRO = ["LEU", "ILE", "VAL", "PHE", "MET", "ALA"]
POLAR = ["GLU", "LYS", "GLN", "ARG", "ASP", "SER", "THR", "ASN"]
LOOP = ["GLY", "SER", "PRO", "ASN", "ASP", "GLY", "THR"]


# ---------------------------------------------------------------- geometry
def _unit(v):
    return v / np.linalg.norm(v)


def nerf(a, b, c, bond, angle, torsion):
    """Place atom d so that |cd|=bond, angle(b,c,d)=angle, dihedral(a,b,c,d)=torsion (degrees)."""
    angle, torsion = math.radians(angle), math.radians(torsion)
    bc = _unit(c - b)
    n = _unit(np.cross(b - a, bc))
    m = np.cross(n, bc)
    d2 = np.array([-bond * math.cos(angle), bond * math.sin(angle) * math.cos(torsion),
                   bond * math.sin(angle) * math.sin(torsion)])
    return c + d2[0] * bc + d2[1] * m + d2[2] * n


def ideal_helix(n):
    """Backbone (N, CA, C) of an ideal alpha helix, phi=-57 psi=-47."""
    N = np.array([0.0, 1.458, 0.0]); CA = np.zeros(3); C = np.array([1.525, 0.0, 0.0])
    res = [[N, CA, C]]
    for _ in range(n - 1):
        pN, pCA, pC = res[-1]
        nN = nerf(pN, pCA, pC, 1.329, 116.2, -47.0)
        nCA = nerf(pCA, pC, nN, 1.458, 121.7, 180.0)
        nC = nerf(pC, nN, nCA, 1.525, 111.2, -57.0)
        res.append([nN, nCA, nC])
    return [list(r) for r in res]


def rot_to(u, v):
    """Rotation matrix taking unit vector u onto unit vector v."""
    u, v = _unit(u), _unit(v)
    w = np.cross(u, v); s = np.linalg.norm(w); c = float(np.dot(u, v))
    if s < 1e-8:
        if c > 0:
            return np.eye(3)
        p = _unit(np.cross(u, [1, 0, 0] if abs(u[0]) < 0.9 else [0, 1, 0]))
        return 2 * np.outer(p, p) - np.eye(3)
    k = w / s
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + s * K + (1 - c) * K @ K


def placed_helix(n, center, direction, spin=0.0):
    bb = ideal_helix(n)
    cas = np.array([r[1] for r in bb])
    _, _, vt = np.linalg.svd(cas - cas.mean(0))
    axis = vt[0]
    if np.dot(axis, cas[-1] - cas[0]) < 0:
        axis = -axis
    R = rot_to(axis, np.array(direction, float))
    Rs = rot_to(np.array([1.0, 0, 0]), np.array([1.0, 0, 0]))
    if spin:
        a = math.radians(spin); d = _unit(np.array(direction, float))
        K = np.array([[0, -d[2], d[1]], [d[2], 0, -d[0]], [-d[1], d[0], 0]])
        Rs = np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * K @ K
    m = cas.mean(0)
    return [[Rs @ (R @ (x - m)) + np.array(center, float) for x in r] for r in bb]


def loop_cas(p0, p1, bulge, n):
    """n CA positions on a quadratic Bezier from p0 to p1 bulging toward `bulge`."""
    out = []
    for i in range(1, n + 1):
        t = i / (n + 1)
        out.append((1 - t) ** 2 * p0 + 2 * (1 - t) * t * bulge + t ** 2 * p1)
    return out


def tail_cas(start, away, n, rng):
    pts, p, d = [], np.array(start, float), _unit(np.array(away, float))
    for _ in range(n):
        d = _unit(d + 0.55 * rng.normal(size=3))
        d = _unit(d + 0.35 * _unit(np.array(away, float)))
        p = p + 3.8 * d
        pts.append(p.copy())
    return pts


def backbone_from_ca(cas):
    """Approximate N/C atoms for loop residues defined only by CA."""
    out = []
    for i, ca in enumerate(cas):
        prev = cas[i - 1] if i > 0 else ca - (cas[i + 1] - ca)
        nxt = cas[i + 1] if i + 1 < len(cas) else ca + (ca - cas[i - 1])
        N = ca + 1.458 * _unit(prev - ca + 0.3 * np.cross(prev - ca, nxt - ca) / (np.linalg.norm(np.cross(prev - ca, nxt - ca)) + 1e-6))
        C = ca + 1.525 * _unit(nxt - ca)
        out.append([N, ca, C])
    return out


# ------------------------------------------------------------- model build
class Builder:
    def __init__(self, seed):
        self.rng = np.random.default_rng(seed)
        self.res = []  # dicts: bb=[N,CA,C], ss, kind

    def helix(self, n, center, direction, spin=0.0, tag="core"):
        for i, bb in enumerate(placed_helix(n, center, direction, spin)):
            self.res.append({"bb": bb, "ss": "H", "kind": tag, "hpos": i})

    def connect(self, bulge, n=None, tag="loop"):
        """Loop from the last residue to the next helix (call before adding that helix)."""
        self._pending = (bulge, n, tag)

    def helix_after_loop(self, n, center, direction, spin=0.0, tag="core"):
        bulge, nl, ltag = self._pending
        new = placed_helix(n, center, direction, spin)
        p0, p1 = self.res[-1]["bb"][1], new[0][1]
        if nl is None:
            nl = max(2, int(np.linalg.norm(p1 - p0) / 3.3))
        cas = loop_cas(p0, p1, np.array(bulge, float), nl)
        for bb in backbone_from_ca([p0] + cas + [p1])[1:-1]:
            self.res.append({"bb": bb, "ss": "L", "kind": ltag})
        for i, bb in enumerate(new):
            self.res.append({"bb": bb, "ss": "H", "kind": tag, "hpos": i})

    def tails(self, n_term, c_term, away_n, away_c):
        if c_term:
            cas = tail_cas(self.res[-1]["bb"][1], away_c, c_term, self.rng)
            full = [self.res[-1]["bb"][1]] + cas
            for bb in backbone_from_ca(full)[1:]:
                self.res.append({"bb": bb, "ss": "L", "kind": "tail"})
        if n_term:
            cas = tail_cas(self.res[0]["bb"][1], away_n, n_term, self.rng)[::-1]
            full = cas + [self.res[0]["bb"][1]]
            new = [{"bb": bb, "ss": "L", "kind": "tail"} for bb in backbone_from_ca(full)[:-1]]
            self.res = new + self.res


def side_chains(res, centroid, rng):
    """Pick residue names (hydrophobic inward, polar outward) and add CB / CG pseudo-atoms."""
    for r in res:
        N, CA, C = r["bb"]
        b = CA - N; c = C - CA; a = np.cross(b, c)
        CB = -0.58273431 * a + 0.56802827 * b - 0.54067466 * c + CA
        inward = np.dot(_unit(CB - CA), _unit(centroid - CA)) > 0.2
        if r["kind"] == "tail" or r["ss"] == "L":
            name = LOOP[rng.integers(len(LOOP))] if r["kind"] != "tail" else ["SER", "GLY", "PRO", "GLU", "LYS", "ASN"][rng.integers(6)]
        else:
            name = HYDRO[rng.integers(len(HYDRO))] if inward else POLAR[rng.integers(len(POLAR))]
        r["resn"] = name
        sc = {}
        if name != "GLY":
            sc["CB"] = CB
            if name not in ("ALA", "SER", "THR", "PRO"):
                sc["CG"] = CB + 1.52 * _unit(CB - CA + 0.3 * _unit(CB - N))
            if name in ("PHE", "LEU", "MET", "LYS", "ARG", "GLU", "GLN"):
                sc["CD"] = sc["CG"] + 1.52 * _unit(sc["CG"] - CA)
        r["sc"] = sc
        nxt_dir = _unit(C - CA)
        r["O"] = C + 1.231 * _unit(np.cross(nxt_dir, a) * 0.0 + _unit(C - CA) * 0.4 + _unit(np.cross(a, nxt_dir)))


def atoms_of(res):
    out = []
    for i, r in enumerate(res):
        N, CA, C = r["bb"]
        for nm, xyz in [("N", N), ("CA", CA), ("C", C), ("O", r["O"])] + list(r["sc"].items()):
            out.append((i, nm, xyz))
    return out


def carve_pocket(res, center, radius):
    """Remove side-chain atoms (beyond CB) inside the pocket, make a cavity."""
    c = np.array(center, float)
    for r in res:
        for nm in list(r["sc"].keys()):
            if nm != "CB" and np.linalg.norm(r["sc"][nm] - c) < radius:
                del r["sc"][nm]


def pocket_spheres(res, center, rng, n=60, ball=5.5):
    c = np.array(center, float)
    xyz = np.array([a[2] for a in atoms_of(res)])
    spheres = []
    for _ in range(n * 6):
        p = c + rng.normal(size=3) * ball / 2.2
        d = np.linalg.norm(xyz - p, axis=1).min()
        if 3.0 <= d <= 4.6:
            spheres.append([*map(float, p), float(d)])
        if len(spheres) >= n:
            break
    return spheres


def lining(res, spheres):
    xyz_atoms = atoms_of(res)
    lin = set()
    for (i, _, p) in xyz_atoms:
        for s in spheres:
            if np.linalg.norm(p - np.array(s[:3])) <= s[3] + 0.6:
                lin.add(i)
                break
    return sorted(lin)


def to_pdb(res, plddt, title):
    lines = [f"REMARK   1 SYNTHETIC DEMO MODEL - {title}",
             "REMARK   2 NOT A REAL PROTEIN. B-FACTOR COLUMN = MADE-UP pLDDT."]
    serial = 1
    for i, r in enumerate(res):
        N, CA, C = r["bb"]
        for nm, xyz in [("N", N), ("CA", CA), ("C", C), ("O", r["O"])] + list(r["sc"].items()):
            el = nm[0]
            lines.append("ATOM  %5d  %-3s %3s A%4d    %8.3f%8.3f%8.3f%6.2f%6.2f          %2s" % (
                serial, nm, r["resn"], i + 1, xyz[0], xyz[1], xyz[2], 1.0, plddt[i], el))
            serial += 1
    helix_runs, start = [], None
    for i, r in enumerate(res + [{"ss": "L"}]):
        if r["ss"] == "H" and start is None:
            start = i
        if r["ss"] != "H" and start is not None:
            helix_runs.append((start, i - 1)); start = None
    hdr = []
    for k, (s, e) in enumerate(helix_runs, 1):
        hdr.append("HELIX  %3d %3d %3s A %4d  %3s A %4d  1%36d" % (k, k, res[s]["resn"], s + 1, res[e]["resn"], e + 1, e - s + 1))
    return "\n".join(lines[:2] + hdr + lines[2:] + ["TER", "END"]) + "\n"


# ----------------------------------------------------------- pLDDT design
def base_plddt(res, rng):
    vals = []
    for r in res:
        if r["kind"] == "tail":
            v = rng.normal(34, 7)
        elif r["kind"] == "linker":
            v = rng.normal(42, 6)
        elif r["kind"] == "lid":
            v = rng.normal(56, 4)
        elif r["ss"] == "L":
            v = rng.normal(80, 4)
        else:
            v = rng.normal(92, 2.5)
        vals.append(v)
    # smooth a little (real traces are autocorrelated)
    v = np.array(vals)
    k = np.array([0.25, 0.5, 0.25])
    sm = np.convolve(np.pad(v, 1, mode="edge"), k, mode="valid")
    return np.clip(sm, 18, 98.5)


def pull_toward(plddt, residues, target, spread=3, jitter=3.0, rng=None):
    """Lower (or raise) pLDDT of given residues toward `target`, fading over `spread` neighbours."""
    p = plddt.copy(); n = len(p)
    w = np.zeros(n)
    for i in residues:
        for d in range(-spread, spread + 1):
            j = i + d
            if 0 <= j < n:
                w[j] = max(w[j], 1 - abs(d) / (spread + 1))
    noise = rng.normal(0, jitter, n) if rng is not None else 0
    return np.clip(p * (1 - w) + (target + noise) * w, 18, 98.5)


# ------------------------------------------------------------- scenarios
def bundle(b, k, R, L, ctr=(0, 0, 0), tag="core", start_dir=1, loop_out=9.0, spin0=0.0):
    ctr = np.array(ctr, float)
    for h in range(k):
        ang = 2 * math.pi * h / k
        c = ctr + np.array([R * math.cos(ang), R * math.sin(ang), 0.0])
        d = [0, 0, start_dir if h % 2 == 0 else -start_dir]
        if h == 0 and not b.res:
            b.helix(L, c, d, spin=spin0 + 40 * h, tag=tag)
        else:
            prev = b.res[-1]["bb"][1]
            mid = (prev + c) / 2
            out = _unit(np.array([mid[0] - ctr[0], mid[1] - ctr[1], 0.0]) + 1e-6)
            bul = mid + out * 4.0 + np.array([0, 0, np.sign(prev[2] - ctr[2]) * loop_out])
            b.connect(bul)
            b.helix_after_loop(L, c, d, spin=spin0 + 40 * h, tag=tag)


def build(spec):
    rng = np.random.default_rng(spec["seed"])
    b = Builder(spec["seed"])
    spec["fold"](b)
    b.tails(*spec.get("tails", (3, 3, [0, 0, -1], [0, 0, 1])))
    res = b.res
    cas = np.array([r["bb"][1] for r in res if r["kind"] != "tail"])
    side_chains(res, cas.mean(0), rng)
    pockets = []
    for pk in spec["pockets"]:
        if callable(pk["center"]):
            pk["center"] = pk["center"](res)
        carve_pocket(res, pk["center"], pk.get("carve", 4.5))
    for pk in spec["pockets"]:
        sph = pocket_spheres(res, pk["center"], rng, n=pk.get("n", 50), ball=pk.get("ball", 5.5))
        pockets.append({"id": pk["id"], "name": pk["name"], "druggability": pk["drug"],
                        "spheres": [[round(x, 2) for x in s] for s in sph],
                        "lining": [i + 1 for i in lining(res, sph)],
                        "center": [round(float(x), 2) for x in pk["center"]],
                        "volume": round(len(sph) * 9.5 + float(rng.normal(0, 20)), 0),
                        "note": pk["note"]})
    plddt = base_plddt(res, rng)
    for pk, spk in zip(pockets, spec["pockets"]):
        if "pocket_plddt" in spk:
            plddt = pull_toward(plddt, [i - 1 for i in pk["lining"]], spk["pocket_plddt"], spread=2, rng=rng)
    for fn in spec.get("post", []):
        plddt = fn(res, plddt, rng)
    plddt = np.round(plddt, 1)
    pdb = to_pdb(res, plddt, spec["name"])
    hyd = {"LEU", "ILE", "VAL", "PHE", "MET", "ALA"}
    for pk in pockets:
        names = [res[i - 1]["resn"] for i in pk["lining"]]
        pk["hydrophobic_frac"] = round(sum(n in hyd for n in names) / max(1, len(names)), 2)
    return {
        "id": spec["id"], "name": spec["name"], "synthetic": True,
        "scenario": spec["scenario"], "description": spec["description"],
        "pocket_method": "fake fpocket-style predictions (made up for the demo)",
        "pdb": pdb, "plddt": [float(x) for x in plddt],
        "residues": [{"resi": i + 1, "resn": r["resn"], "ss": r["ss"], "region": r["kind"]} for i, r in enumerate(res)],
        "pockets": pockets,
    }


def fold_kin(b):
    bundle(b, 6, 9.5, 20)


def fold_gpcr(b):
    bundle(b, 4, 7.4, 22)


def fold_ppi(b):
    bundle(b, 4, 7.6, 20)
    # long flexible lid loop folding back over the top of the bundle, then a 5th helix
    prev = b.res[-1]["bb"][1]
    b.connect(np.array([0.0, 0.0, 22.0]), n=14, tag="lid")
    b.helix_after_loop(18, [16.0, 2.0, 0.0], [0, 0, 1], tag="core")


def fold_link(b):
    bundle(b, 3, 6.2, 16, ctr=(-15, 0, 0))
    b.connect(np.array([0.0, 9.0, 13.0]), n=12, tag="linker")
    first = len(b.res)
    tmp = Builder(0)
    # second domain: 3 helices around (+15,0,0)
    ctr = np.array([15.0, 0, 0])
    for h in range(3):
        ang = 2 * math.pi * h / 3 + math.pi
        c = ctr + np.array([6.2 * math.cos(ang), 6.2 * math.sin(ang), 0.0])
        d = [0, 0, 1 if h % 2 == 0 else -1]
        if h == 0:
            b.helix_after_loop(16, c, d, tag="core")
        else:
            prev = b.res[-1]["bb"][1]; mid = (prev + c) / 2
            b.connect(mid + np.array([4.0, 0, np.sign(prev[2]) * 8.0]))
            b.helix_after_loop(16, c, d, tag="core")


SPECS = [
    {
        "id": "SYN-KIN1", "name": "Synthetic kinase-like 6-helix bundle", "seed": 11,
        "fold": fold_kin, "tails": (3, 4, [0, 0, -1], [1, 0, 1]),
        "scenario": "agree",
        "description": "Well-folded model, short tails. The deep core pocket is druggable AND confidently modelled: here pLDDT and the pocket call agree. A second shallow groove is confident but scores low on druggability: confidence alone does not make a pocket druggable.",
        "pockets": [
            {"id": "P1", "name": "Core pocket", "center": [0, 0, 11.0], "drug": 0.86, "carve": 6.0, "n": 60, "ball": 7,
             "note": "Deep, enclosed, hydrophobic"},
            {"id": "P2", "name": "Surface groove", "center": [15.5, 9.0, -4.0], "drug": 0.21, "carve": 2.5, "n": 30, "ball": 6,
             "note": "Shallow, solvent-exposed"},
        ],
    },
    {
        "id": "SYN-GPCR2", "name": "Synthetic receptor-like bundle with long disordered tails", "seed": 23,
        "fold": fold_gpcr, "tails": (38, 34, [-1, -0.4, -1], [1, 0.5, 1]),
        "scenario": "global-mean-misleads",
        "description": "About 40% of the chain is a disordered N/C tail (pLDDT ~35), so the GLOBAL mean pLDDT is only in the 60s and the whole model looks 'low confidence'. The ligand pocket in the folded bundle is lined by residues at ~90. Judging this target by its global mean would wrongly discard it.",
        "pockets": [
            {"id": "P1", "name": "Orthosteric-like pocket", "center": [0, 0, 12.0], "drug": 0.78, "carve": 6.0, "n": 55, "ball": 7,
             "note": "Enclosed pocket in the folded bundle"},
            {"id": "P2", "name": "Tail crevice", "center": lambda res: np.mean([r["bb"][1] for r in res[8:20]], axis=0), "drug": 0.31, "carve": 3.0, "n": 25, "ball": 8,
             "note": "Crevice formed by a floppy tail: likely spurious"},
        ],
    },
    {
        "id": "SYN-PPI3", "name": "Synthetic domain with a flexible lid over its pocket", "seed": 37,
        "fold": fold_ppi, "tails": (3, 3, [0, 0, -1], [1, 0, -1]),
        "scenario": "disagree",
        "description": "The global mean pLDDT looks reassuring, but the best-scoring pocket sits under a 14-residue lid loop modelled at pLDDT 50-60. The pocket detector calls it druggable, yet its shape depends on a part AlphaFold is unsure about: confidence and pocket DISAGREE. Treat as a hypothesis (MD / experimental structure needed).",
        "pockets": [
            {"id": "P1", "name": "Lid pocket", "center": [0, 0, 14.5], "drug": 0.81, "carve": 6.0, "n": 55, "ball": 7, "pocket_plddt": 57,
             "note": "Formed partly by the low-confidence lid"},
            {"id": "P2", "name": "Helix-5 cleft", "center": [10.5, 4.5, -2.0], "drug": 0.44, "carve": 4.0, "n": 35, "ball": 5,
             "note": "Between the bundle and helix 5"},
        ],
    },
    {
        "id": "SYN-LINK4", "name": "Synthetic two-domain protein with a floppy linker", "seed": 41,
        "fold": fold_link, "tails": (4, 4, [-1, 0, -1], [1, 0, 1]),
        "scenario": "spurious-interdomain",
        "description": "Two confident domains joined by a low-confidence linker. The pocket detector finds a large 'pocket' in the gap between domains: its druggability score looks fine, but the domain arrangement itself is uncertain (low pLDDT, and in a real model you would also check PAE), so the pocket may not exist. The intra-domain pocket is the trustworthy one.",
        "pockets": [
            {"id": "P1", "name": "Domain-1 pocket", "center": [-15, 0, 9.5], "drug": 0.72, "carve": 5.5, "n": 50, "ball": 6, "pocket_plddt": 90,
             "note": "Inside domain 1"},
            {"id": "P2", "name": "Interdomain gap", "center": [0, 2.0, 2.0], "drug": 0.66, "carve": 3.0, "n": 60, "ball": 9, "pocket_plddt": 52,
             "note": "Exists only because of the domain arrangement"},
        ],
    },
]


def main():
    os.makedirs(OUT, exist_ok=True)
    for spec in SPECS:
        d = build(spec)
        with open(os.path.join(OUT, spec["id"] + ".json"), "w") as f:
            json.dump(d, f)
        pl = np.array(d["plddt"])
        print(spec["id"], len(d["residues"]), "res, global", round(pl.mean(), 1),
              [(p["id"], len(p["lining"]), len(p["spheres"]), round(float(np.mean([pl[i - 1] for i in p["lining"]])) if p["lining"] else 0, 1)) for p in d["pockets"]])


if __name__ == "__main__":
    main()
