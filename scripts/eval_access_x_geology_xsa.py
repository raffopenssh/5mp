#!/usr/bin/env python3
"""Second look, prompted by the user: accessibility alone is a reporting
proxy (osrm_accessibility_eval.json) - but is the CONJUNCTION
"gold-graded contact AND reachable ground" a signal beyond reach?

Physical rationale (stated before testing): artisanal miners walk in like
everyone else; graded rock a day's travel from any settlement cannot be
worked seasonally. So gold∧accessible should concentrate reported mines
more than gold alone if access adds real information; if the conjunction's
reach-null lift is the same as gold's alone, access added nothing that the
reporting-reach KDE didn't already carry.

Tests (all vs uniform AND reach nulls, PERMS=5000, BH across the family):
  gold5          gold contact <=5 km                       (replicates main)
  gold5_acc60/120/240   gold∧access (three cuts)
  gold5_faracc   gold contact <=5km AND access > 240 min   (the complement:
                 if access is pure reporting bias, remote gold should show
                 capture ~0 even where sheets/ground are good - which is
                 unfalsifiable from report data. Reported for honesty, not
                 votable.)

Writes data/eval/xsa_mining/access_x_geology_eval.json.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from predict_mining_xsa import (load_aoi, make_grid, load_anchors,  # noqa
                                cluster_sites, proj, reach_weights,
                                gold_contact_geoms, nearest_dist_km, RNG)

OUTDIR = ROOT / "data/eval/xsa_mining"
PERMS = 5000


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def score_mask(name, cell_mask, truth_mask, wp, n):
    """capture/lift/p of a boolean per-cell condition, uniform + reach."""
    cap = float(np.mean(truth_mask))
    base_u = float(np.mean(cell_mask))
    base_r = float(np.sum(wp * cell_mask))
    sims_u = np.mean(cell_mask[RNG.integers(0, len(cell_mask),
                                            size=(PERMS, n))], axis=1)
    idx = RNG.choice(len(cell_mask), size=(PERMS, n), p=wp)
    sims_r = np.mean(cell_mask[idx], axis=1)
    lift_u = cap / base_u if base_u else None
    lift_r = cap / base_r if base_r else None
    p_u = float(np.mean(sims_u >= cap) if (lift_u or 0) >= 1
                else np.mean(sims_u <= cap))
    p_r = float(np.mean(sims_r >= cap) if (lift_r or 0) >= 1
                else np.mean(sims_r <= cap))
    return dict(signal=name, capture=round(cap, 3),
                baseline_uniform=round(base_u, 4),
                lift_uniform=round(lift_u, 2) if lift_u else None,
                p_uniform=round(p_u, 4),
                baseline_reach=round(base_r, 4),
                lift_reach=round(lift_r, 2) if lift_r else None,
                p_reach=round(p_r, 4))


def main():
    z = np.load(OUTDIR / "osrm_times.npz")
    access = z["t_min"].astype(np.float32).min(axis=0)
    poly = load_aoi()
    grid = make_grid(poly)
    assert np.allclose(z["cells"], grid.astype(np.float32))
    lat0 = poly.centroid.y
    gk = proj(grid, lat0)

    log("gold contacts...")
    d_gold = nearest_dist_km(gk, gold_contact_geoms(poly), lat0)

    anchors = load_anchors(poly)
    clusters = cluster_sites(anchors, lat0)
    ck = np.array([c["km"] for c in clusters])
    from scipy.spatial import cKDTree
    _, tidx = cKDTree(gk).query(ck)
    n = len(clusters)

    reach_w, reach_meta = reach_weights(gk, poly, lat0)
    wp = reach_w / reach_w.sum()

    gold5 = d_gold <= 5.0
    conds = [
        ("gold5", gold5),
        ("gold5_acc60", gold5 & (access <= 60)),
        ("gold5_acc120", gold5 & (access <= 120)),
        ("gold5_acc240", gold5 & (access <= 240)),
        ("gold5_faracc240", gold5 & (access > 240)),
    ]
    rows = []
    for name, m in conds:
        r = score_mask(name, m, m[tidx], wp, n)
        rows.append(r)
        log(f"  {name}: capture {r['capture']}, reach lift "
            f"{r['lift_reach']} (p={r['p_reach']}), uniform lift "
            f"{r['lift_uniform']} (p={r['p_uniform']})")

    # BH within the three VOTABLE conjunctions (gold5 replicates the main
    # run; faracc is a diagnostic, not a candidate signal)
    votable = [r for r in rows if r["signal"].startswith("gold5_acc")]
    srt = sorted(range(len(votable)), key=lambda i: votable[i]["p_reach"])
    prev = 1.0
    for rank in range(len(votable) - 1, -1, -1):
        i = srt[rank]
        prev = min(prev, votable[i]["p_reach"] * len(votable) / (rank + 1))
        votable[i]["q_bh_reach"] = round(prev, 4)

    best = min(votable, key=lambda r: r["p_reach"])
    gold_only = rows[0]
    adds = (best.get("q_bh_reach", 1) < 0.05
            and (best["lift_reach"] or 0) > (gold_only["lift_reach"] or 0))
    out = dict(
        generated_by="scripts/eval_access_x_geology_xsa.py",
        question=("Does gold-contact ∧ accessibility concentrate reported "
                  "mines beyond reporting reach, more than gold alone?"),
        n_truth_clusters=n, permutations=PERMS,
        reach_sources=reach_meta,
        signals=rows,
        best_conjunction=best["signal"],
        verdict=("conjunction_adds_signal" if adds else
                 "conjunction_does_not_beat_gold_alone"),
        note=("gold5_faracc240 is a diagnostic: report-based truth cannot "
              "falsify 'remote gold is mined too' - its capture is bounded "
              "by where reporters go. Absence of remote captures is NOT "
              "evidence of absence of remote mining."))
    json.dump(out, open(OUTDIR / "access_x_geology_eval.json", "w"),
              indent=1)
    log("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
