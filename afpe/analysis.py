"""Cross-check of AlphaFold confidence (pLDDT) against a predicted pocket."""
import statistics

BANDS = [(90, "very high"), (70, "high"), (50, "low"), (0, "very low")]
DRUGGABLE = 0.5          # fpocket's usual druggability cut-off
CONFIDENT_MEAN = 70.0
MAX_FRAC_BELOW_70 = 0.25
GAP_WARN = 10.0


def band(v):
    for cut, name in BANDS:
        if v > cut or cut == 0:
            return name


def pocket_stats(plddt, lining, druggability):
    vals = [plddt[i - 1] for i in lining if 0 < i <= len(plddt)]
    g = round(statistics.fmean(plddt), 1) if plddt else None
    if not vals:
        return {"n": 0, "global_mean": g, "verdict": "No lining residues: cannot judge.", "agree": None, "kind": "empty"}
    mean = statistics.fmean(vals)
    below70 = [i for i in lining if plddt[i - 1] < 70]
    below50 = [i for i in lining if plddt[i - 1] < 50]
    frac70 = len(below70) / len(vals)
    confident = mean >= CONFIDENT_MEAN and frac70 <= MAX_FRAC_BELOW_70 and not below50
    druggable = druggability >= DRUGGABLE
    if confident and druggable:
        kind, agree = "agree-druggable", True
        verdict = "Confidence and pocket AGREE: druggable-looking pocket on confidently modelled residues. A reasonable starting point (still validate)."
    elif confident and not druggable:
        kind, agree = "agree-undruggable", True
        verdict = "Geometry is confident, but the pocket scores as poorly druggable. High pLDDT does not make a pocket druggable."
    elif druggable:
        kind, agree = "disagree", False
        verdict = "Confidence and pocket DISAGREE: the pocket scores druggable, but its lining is low-confidence. Its shape may be an artefact; treat as a hypothesis."
    else:
        kind, agree = "low-both", None
        verdict = "Low confidence and low druggability: most likely noise from a poorly modelled region."
    gap = round(mean - g, 1)
    if abs(gap) >= GAP_WARN:
        contrast = (f"The global mean ({g}) {'under' if gap > 0 else 'over'}states this pocket's confidence "
                    f"by {abs(gap)} points: judge pockets by their own residues, not the whole model.")
    else:
        contrast = f"The global mean ({g}) happens to be close to this pocket's mean here."
    return {
        "n": len(vals), "mean": round(mean, 1), "min": round(min(vals), 1),
        "median": round(statistics.median(vals), 1),
        "below70": below70, "below50": below50, "frac_below70": round(frac70, 2),
        "druggability": druggability, "druggable": druggable, "confident": confident,
        "global_mean": g, "gap": gap, "verdict": verdict, "contrast": contrast,
        "agree": agree, "kind": kind,
        "bands": {name: sum(1 for v in vals if band(v) == name) for _, name in BANDS},
    }


def enrich(rec):
    """Attach stats to every pocket of a structure record (server is the single source of truth)."""
    pl = rec["plddt"]
    out = dict(rec)
    out["global_mean"] = round(statistics.fmean(pl), 1) if pl else None
    out["global_bands"] = {name: sum(1 for v in pl if band(v) == name) for _, name in BANDS}
    out["pockets"] = [dict(p, stats=pocket_stats(pl, p["lining"], p["druggability"])) for p in rec["pockets"]]
    return out
