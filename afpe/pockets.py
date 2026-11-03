"""PDB parsing and a small LIGSITE-style grid pocket finder (toy, numpy only).

Used for structures imported by the user (uploaded AlphaFold PDB files or an
AlphaFold DB download). It is NOT fpocket: its 'druggability' is a simple
made-up logistic score of volume, buriedness and hydrophobicity, labelled as
such in the UI.
"""
import math
import re

import numpy as np

HYD = {"LEU", "ILE", "VAL", "PHE", "MET", "ALA", "TRP", "PRO", "CYS", "TYR"}


def parse_pdb(text, max_atoms=60000):
    atoms, res_order, plddt, resn = [], [], {}, {}
    chain0 = None
    for line in text.splitlines():
        if not line.startswith("ATOM"):
            continue
        ch = line[21]
        if chain0 is None:
            chain0 = ch
        if ch != chain0:
            continue  # first chain only
        try:
            name = line[12:16].strip(); rn = line[17:20].strip(); ri = int(line[22:26])
            x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
            b = float(line[60:66])
        except ValueError:
            continue
        atoms.append((ri, name, rn, x, y, z))
        if ri not in plddt:
            res_order.append(ri); resn[ri] = rn
        if name == "CA" or ri not in plddt:
            plddt[ri] = b
        if len(atoms) > max_atoms:
            raise ValueError("structure too large for the demo")
    if not atoms:
        raise ValueError("no ATOM records found")
    return atoms, res_order, plddt, resn


def find_pockets(atoms, spacing=1.0, probe_clear=3.0, min_psp=5, min_points=30, top=5):
    xyz = np.array([[a[3], a[4], a[5]] for a in atoms])
    lo = xyz.min(0) - 6; hi = xyz.max(0) + 6
    shape = np.ceil((hi - lo) / spacing).astype(int) + 1
    if np.prod(shape) > 6_000_000:
        spacing *= (np.prod(shape) / 6_000_000) ** (1 / 3)
        shape = np.ceil((hi - lo) / spacing).astype(int) + 1
    idx = np.round((xyz - lo) / spacing).astype(int)

    def stamp(radius):
        g = np.zeros(shape, bool)
        r = int(math.ceil(radius / spacing))
        rng = range(-r, r + 1)
        for dx in rng:
            for dy in rng:
                for dz in rng:
                    if (dx * dx + dy * dy + dz * dz) * spacing ** 2 <= radius ** 2:
                        p = idx + (dx, dy, dz)
                        ok = np.all((p >= 0) & (p < shape), axis=1)
                        g[p[ok, 0], p[ok, 1], p[ok, 2]] = True
        return g

    protein = stamp(1.8)        # occupied by protein
    blocked = stamp(probe_clear)  # too close to an atom for a ligand-atom probe
    free = ~blocked
    # LIGSITE: count directions (7 axes) where protein is hit on BOTH sides
    dirs = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1), (1, 1, -1), (1, -1, 1), (-1, 1, 1)]
    maxstep = int(round(10.0 / spacing))
    psp = np.zeros(shape, np.int8)
    for d in dirs:
        both = np.ones(shape, bool)
        for sgn in (1, -1):
            hit = np.zeros(shape, bool)
            for s in range(1, maxstep + 1):
                hit |= _shift(protein, tuple(-sgn * s * c for c in d))
            both &= hit
        psp += both
    cand = free & (psp >= min_psp)
    labels, n = _label(cand)
    pockets = []
    for k in range(1, n + 1):
        pts = np.argwhere(labels == k)
        if len(pts) < min_points:
            continue
        coords = pts * spacing + lo
        bur = psp[tuple(pts.T)].mean()
        pockets.append((len(pts), coords, bur))
    pockets.sort(key=lambda t: -t[0])
    out = []
    resinfo = [(a[0], a[2]) for a in atoms]
    for j, (npts, coords, bur) in enumerate(pockets[:top], 1):
        sel = coords if len(coords) <= 400 else coords[np.linspace(0, len(coords) - 1, 400).astype(int)]
        d = np.sqrt(((sel[:, None, :] - xyz[None, :, :]) ** 2).sum(-1))
        rad = np.clip(d.min(1), 3.0, 4.5)
        near = np.where((d <= rad[:, None] + 0.8).any(0))[0]
        lining = sorted({resinfo[i][0] for i in near})
        names = {resinfo[i][0]: resinfo[i][1] for i in near}
        hyd = sum(1 for r in lining if names[r] in HYD) / max(1, len(lining))
        vol = npts * spacing ** 3
        z = 0.004 * (vol - 350) + 4.0 * (hyd - 0.45) + 0.6 * (bur - 5.5)
        drug = round(1 / (1 + math.exp(-z)), 2)
        show = sel[np.linspace(0, len(sel) - 1, min(len(sel), 90)).astype(int)]
        srad = np.clip(np.sqrt(((show[:, None, :] - xyz[None, :, :]) ** 2).sum(-1)).min(1), 3.0, 4.5)
        out.append({"id": f"P{j}", "name": f"Grid pocket {j}", "druggability": drug,
                    "volume": round(vol), "hydrophobic_frac": round(hyd, 2),
                    "center": [round(float(c), 2) for c in coords.mean(0)],
                    "spheres": [[round(float(a), 2), round(float(b), 2), round(float(c), 2), round(float(r) * 0.6, 2)]
                                for (a, b, c), r in zip(show, srad)],
                    "lining": lining,
                    "note": f"toy grid detector: {npts} grid points, mean buriedness {bur:.1f}/7"})
    return out


def _shift(a, s):
    out = np.zeros_like(a)
    src = tuple(slice(max(0, -k), a.shape[i] - max(0, k)) for i, k in enumerate(s))
    dst = tuple(slice(max(0, k), a.shape[i] - max(0, -k)) for i, k in enumerate(s))
    out[dst] = a[src]
    return out


def _label(mask):
    """26-connected component labelling (iterative flood fill)."""
    labels = np.zeros(mask.shape, np.int32)
    n = 0
    pts = np.argwhere(mask)
    offs = [(i, j, k) for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1) if (i, j, k) != (0, 0, 0)]
    for p in pts:
        p = tuple(p)
        if labels[p]:
            continue
        n += 1
        labels[p] = n
        stack = [p]
        while stack:
            x, y, z = stack.pop()
            for dx, dy, dz in offs:
                q = (x + dx, y + dy, z + dz)
                if 0 <= q[0] < mask.shape[0] and 0 <= q[1] < mask.shape[1] and 0 <= q[2] < mask.shape[2] \
                        and mask[q] and not labels[q]:
                    labels[q] = n
                    stack.append(q)
    return labels, n


def record_from_pdb(rid, name, text, source):
    atoms, order, plddt, resn = parse_pdb(text)
    vals = [plddt[r] for r in order]
    looks_plddt = all(0 <= v <= 100 for v in vals) and max(vals) > 20 and len(set(vals)) > 3
    # renumber residues 1..N so lining indices line up with the pLDDT list
    remap = {r: i + 1 for i, r in enumerate(order)}
    atoms2 = [(remap[a[0]], a[1], a[2], a[3], a[4], a[5]) for a in atoms]
    pockets = find_pockets(atoms2)
    lines = []
    for k, (ri, nm, rn, x, y, z) in enumerate(atoms2, 1):
        lines.append("ATOM  %5d  %-3s %3s A%4d    %8.3f%8.3f%8.3f%6.2f%6.2f          %2s" % (
            k, nm[:3], rn, ri, x, y, z, 1.0, plddt[order[ri - 1]], nm[0]))
    return {
        "id": rid, "name": name, "synthetic": False, "scenario": "imported",
        "description": ("Imported structure. pLDDT read from the B-factor column." if looks_plddt else
                        "Imported structure, but its B-factor column does not look like pLDDT (is this an experimental PDB entry?). The confidence layer is not meaningful."),
        "pocket_method": "toy LIGSITE-style grid detector (not fpocket; druggability is a made-up logistic score)",
        "source": source, "plddt_valid": looks_plddt,
        "pdb": "\n".join(lines) + "\nEND\n",
        "plddt": [round(v, 1) for v in vals],
        "residues": [{"resi": i + 1, "resn": resn[r], "ss": "?", "region": "-"} for i, r in enumerate(order)],
        "pockets": pockets,
    }


AF_ID = re.compile(r"^(AF-)?([A-NR-Z][0-9][A-Z0-9]{3}[0-9]|[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9][A-Z][A-Z0-9]{2}[0-9][A-Z][A-Z0-9]{2}[0-9])(-F1)?$")
PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")
