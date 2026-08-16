#!/usr/bin/env python3
"""Does OSRM travel-time accessibility carry signal for reported mining,
beyond reporting reach?  Decision script: keep or drop (user's framing -
"if it doesn't help, we just drop it").

Two questions, both against the measured nulls of predict_mining_xsa:

1. SIGNAL: do reported mine clusters sit on more-accessible ground than
   (a) uniform cells, (b) reach-weighted cells?  Accessibility is close to
   the *definition* of reporting reach, so passing (a) but failing (b)
   means "accessibility only predicts where reporters go" -> not a mining
   signal, do not add to the composite.

2. COMPOSITE UTILITY: does adding an accessibility factor change the
   composite's top-5/10/20% capture under the reach null?  Measured, not
   assumed.

Writes data/eval/xsa_mining/osrm_accessibility_eval.json with a one-word
verdict per question.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from predict_mining_xsa import (load_aoi, make_grid, load_anchors,   # noqa
                                cluster_sites, proj, reach_weights, RNG)

OUTDIR = ROOT / "data/eval/xsa_mining"
PERMS = 5000
THRESHOLDS_MIN = [60, 120, 240, 480]   # "within X minutes of a start place"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def main():
    z = np.load(OUTDIR / "osrm_times.npz")
    t = z["t_min"].astype(np.float32)          # (stations, cells)
    access = t.min(axis=0)                     # best minutes per cell
    poly = load_aoi()
    grid = make_grid(poly)
    assert np.allclose(z["cells"], grid.astype(np.float32))
    lat0 = poly.centroid.y
    gk = proj(grid, lat0)

    anchors = load_anchors(poly)
    clusters = cluster_sites(anchors, lat0)
    ck = np.array([c["km"] for c in clusters])
    from scipy.spatial import cKDTree
    tree = cKDTree(gk)
    _, idx = tree.query(ck)
    truth_access = access[idx]
    n = len(clusters)

    log(f"{n} truth clusters; cell accessibility median "
        f"{np.median(access):.0f} min, truth median "
        f"{np.median(truth_access):.0f} min")

    reach_w, reach_meta = reach_weights(gk, poly, lat0)
    wp = reach_w / reach_w.sum()

    rows = []
    for thr in THRESHOLDS_MIN:
        cap = float(np.mean(truth_access <= thr))
        base_u = float(np.mean(access <= thr))
        base_r = float(np.sum(wp * (access <= thr)))
        sims_u = np.mean(access[RNG.integers(0, len(access),
                                             size=(PERMS, n))] <= thr, axis=1)
        idx_r = RNG.choice(len(access), size=(PERMS, n), p=wp)
        sims_r = np.mean(access[idx_r] <= thr, axis=1)
        lift_u = cap / base_u if base_u else None
        lift_r = cap / base_r if base_r else None
        p_u = float(np.mean(sims_u >= cap) if (lift_u or 0) >= 1
                    else np.mean(sims_u <= cap))
        p_r = float(np.mean(sims_r >= cap) if (lift_r or 0) >= 1
                    else np.mean(sims_r <= cap))
        rows.append(dict(threshold_min=thr, capture=round(cap, 3),
                         baseline_uniform=round(base_u, 3),
                         lift_uniform=round(lift_u, 2),
                         p_uniform=round(p_u, 4),
                         baseline_reach=round(base_r, 3),
                         lift_reach=round(lift_r, 2),
                         p_reach=round(p_r, 4)))
        log(f"  <= {thr} min: capture {cap:.2f}, uniform lift "
            f"{lift_u:.2f} (p={p_u:.4f}), reach lift {lift_r:.2f} "
            f"(p={p_r:.4f})")

    # BH across the four thresholds - "any p<0.05 of four tried" is
    # cherry-picking, the same sin the main script's q_bh exists to stop.
    def bh(key):
        m = len(rows)
        srt = sorted(range(m), key=lambda i: rows[i][key])
        prev = 1.0
        qs = [None] * m
        for r in range(m - 1, -1, -1):
            i = srt[r]
            prev = min(prev, rows[i][key] * m / (r + 1))
            qs[i] = round(prev, 4)
        return qs
    for i, (qu, qr) in enumerate(zip(bh("p_uniform"), bh("p_reach"))):
        rows[i]["q_bh_uniform"] = qu
        rows[i]["q_bh_reach"] = qr
    signal_reach = any(r["q_bh_reach"] < 0.05 and r["lift_reach"] > 1
                       for r in rows)
    signal_uniform = any(r["q_bh_uniform"] < 0.05 and r["lift_uniform"] > 1
                         for r in rows)

    # ---- question 2: composite utility. Rebuild the shipped composite's
    # rank surface from prediction.json's passing signals is heavy; instead
    # measure the MARGINAL question directly: among cells in the shipped
    # top-20% surface, does further sorting by accessibility concentrate
    # truth?  (Spearman of access rank vs truth-cell membership inside the
    # hot surface, permutation p.)
    pred = json.load(open(OUTDIR / "prediction.json"))
    hot = set()
    gjf = json.load(open(OUTDIR / "prediction.geojson"))["features"]
    for f in gjf:
        pr = f["properties"]
        if pr.get("layer") == "composite_graduated" and \
           pr["tier"] in ("top05", "top10", "top20"):
            hot.add((round(f["geometry"]["coordinates"][0], 4),
                     round(f["geometry"]["coordinates"][1], 4)))
    hot_mask = np.array([(round(float(x), 4), round(float(y), 4)) in hot
                         for x, y in grid])
    truth_cells = np.zeros(len(grid), bool)
    truth_cells[idx] = True
    h_acc = access[hot_mask]
    h_tru = truth_cells[hot_mask]
    k = int(h_tru.sum())
    if k >= 8:
        med = np.median(h_acc)
        cap_acc = float(np.mean(h_acc[h_tru] <= med))
        sims = np.mean(h_acc[RNG.integers(0, len(h_acc),
                                          size=(PERMS, k))] <= med, axis=1)
        p_marg = float(np.mean(sims >= cap_acc) if cap_acc >= 0.5
                       else np.mean(sims <= cap_acc))
        marginal = dict(hot_cells=int(hot_mask.sum()), truth_in_hot=k,
                        median_split_capture=round(cap_acc, 3),
                        p=round(p_marg, 4),
                        helps=bool(p_marg < 0.05 and cap_acc > 0.5))
    else:
        marginal = dict(verdict="too_few_truth_in_hot", truth_in_hot=k)

    verdict = ("signal_beyond_reach" if signal_reach else
               ("reporting_proxy_only" if signal_uniform else "no_signal"))
    out = dict(
        generated_by="scripts/eval_osrm_accessibility.py",
        question=("Is travel-time accessibility a mining signal, or only a "
                  "measurement of where reports come from?"),
        n_truth_clusters=n, permutations=PERMS,
        accessibility_by_threshold=rows,
        marginal_within_hot_surface=marginal,
        reach_sources=reach_meta,
        verdict=verdict,
        decision=("add accessibility factor to composite" if signal_reach
                  else "do NOT add to composite; use travel time only for "
                       "monitoring logistics (key-place selection), where "
                       "reachability is the point, not a bias"))
    json.dump(out, open(OUTDIR / "osrm_accessibility_eval.json", "w"),
              indent=1)
    log("verdict:", verdict, "| marginal:", marginal)


if __name__ == "__main__":
    main()
