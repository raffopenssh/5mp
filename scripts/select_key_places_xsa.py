#!/usr/bin/env python3
"""Key monitoring places for XSA mining prediction.

The composite surface says WHERE the mining-context ground is; this script
answers "which few named places do you stand in to have all of it on your
radar?"  Facility-location greedy max-coverage:

  universe  = top-20% composite cells (the only cuts individually
              significant under the reach null are top05 & top20), each
              weighted by its composite score
  stations  = the 2,337 dedup'd start places (footprint settlements + OSM
              places) whose full OSRM travel-time rows we hold
  covered   = cell within DAY_MIN minutes of the place by the best of
              car / foot / bush-walk (people here walk ~40 km/day; the
              foot profile is 5 km/h, bush 4 km/h -> 480 min ~ one day)
  greedy    = repeatedly take the place covering the most uncovered mass;
              stop when marginal gain < MIN_GAIN of total or at MAX_PLACES

Coverage %, per-place gain, and what fraction of the 43 known truth
clusters fall in covered ground are all MEASURED and shipped.  This is
logistics, not inference: accessibility measured 2026-08-16 as a
reporting proxy (osrm_accessibility_eval.json), so it selects where to
STAND, it does not score where to LOOK.

Writes data/eval/xsa_mining/key_places.json.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from predict_mining_xsa import load_aoi, make_grid, load_anchors, \
    cluster_sites, proj  # noqa: E402

OUTDIR = ROOT / "data/eval/xsa_mining"
DAY_MIN = 480           # one travel day
ALT_MIN = 240           # half day, reported as sensitivity
MAX_PLACES = 20
MIN_GAIN = 0.01         # stop when a pick adds <1% of total mass


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def main():
    z = np.load(OUTDIR / "osrm_times.npz")
    t = z["t_min"].astype(np.float32)              # (stations, cells)
    meta = json.load(open(OUTDIR / "osrm_times_meta.json"))
    stations = meta["stations"]
    poly = load_aoi()
    grid = make_grid(poly)
    assert np.allclose(z["cells"], grid.astype(np.float32))

    # hot cells + weights from the shipped prediction
    gj = json.load(open(OUTDIR / "prediction.geojson"))["features"]
    cell_ix = {(round(float(x), 4), round(float(y), 4)): i
               for i, (x, y) in enumerate(grid)}
    hot, w = [], []
    scores = np.full(len(grid), np.nan)
    pctiles = np.full(len(grid), np.nan)
    for f in gj:
        pr = f["properties"]
        if pr.get("layer") != "composite_graduated":
            continue
        i = cell_ix.get((round(f["geometry"]["coordinates"][0], 4),
                         round(f["geometry"]["coordinates"][1], 4)))
        if i is None:
            continue
        scores[i] = pr["score"]; pctiles[i] = pr["pctile"]
        if pr["tier"] in ("top05", "top10", "top20"):
            hot.append(i); w.append(pr["score"])
    hot = np.array(hot); w = np.array(w, np.float64)
    total = w.sum()
    log(f"{len(hot)} hot cells (top-20% surface), {len(stations)} stations")

    th = t[:, hot]                                  # (stations, hot)
    cov = th <= DAY_MIN

    # truth clusters (descriptive only)
    anchors = load_anchors(poly)
    lat0 = poly.centroid.y
    clusters = cluster_sites(anchors, lat0)
    from scipy.spatial import cKDTree
    tree = cKDTree(proj(grid, lat0))
    _, tidx = tree.query(np.array([c["km"] for c in clusters]))

    chosen, covered = [], np.zeros(len(hot), bool)
    gains = cov @ w                                  # initial gain per station
    while len(chosen) < MAX_PLACES:
        s = int(np.argmax(gains))
        g = float(gains[s])
        if g < MIN_GAIN * total:
            break
        newly = cov[s] & ~covered
        covered |= cov[s]
        st = stations[s]
        # truth clusters within DAY_MIN of this place
        tr_near = int(np.sum(t[s, tidx] <= DAY_MIN))
        chosen.append(dict(
            rank=len(chosen) + 1,
            name=st["name"] or "(unnamed)",
            kind=st["kind"], cls=st["cls"], pop=st["pop"],
            lon=st["lon"], lat=st["lat"],
            gain_pct=round(100 * g / total, 1),
            cum_coverage_pct=round(100 * float(w[covered].sum()) / total, 1),
            hot_cells_newly=int(newly.sum()),
            truth_clusters_within_day=tr_near,
            median_min_to_covered=round(float(np.median(th[s][cov[s]])), 0)
            if cov[s].any() else None))
        log(f"  #{len(chosen)} {st['name'] or '(unnamed)'} "
            f"+{chosen[-1]['gain_pct']}% -> {chosen[-1]['cum_coverage_pct']}%")
        gains = np.where(cov[:, ~covered].any(axis=1) if (~covered).any()
                         else False,
                         cov[:, ~covered] @ w[~covered] if (~covered).any()
                         else 0.0, 0.0)
        if not (~covered).any():
            break

    # truth coverage of the FINAL set (descriptive)
    sel = [i for i, _ in enumerate(stations)
           if any(c["lon"] == stations[i]["lon"] and
                  c["lat"] == stations[i]["lat"] for c in chosen)]
    t_sel = t[sel][:, tidx].min(axis=0)
    truth_cov = float(np.mean(t_sel <= DAY_MIN))
    # sensitivity at half a day
    cov_alt = (t[sel][:, hot] <= ALT_MIN)
    alt_mass = float(w[cov_alt.any(axis=0)].sum() / total)

    out = dict(
        generated_by="scripts/select_key_places_xsa.py",
        method=("Greedy max-coverage facility location over the top-20% "
                "composite surface, weight = composite score, coverage = "
                f"within {DAY_MIN} min by best of car/foot(5km/h)/"
                "bush-walk(4km/h) from a local OSRM "
                "(CAR+S.Sudan+Sudan extract, 2026-08-16). Greedy is within "
                "(1-1/e)~63% of the optimal set for this objective "
                "(Nemhauser 1978); ranks are the marginal-gain order, so "
                "the list reads highest-to-lower importance by "
                "construction."),
        caveat=("Selection is monitoring logistics, NOT inference: "
                "accessibility measured as a reporting proxy, not a mining "
                "signal (osrm_accessibility_eval.json, verdict "
                "reporting_proxy_only). The surface says where to look; "
                "these places say where to stand."),
        day_minutes=DAY_MIN,
        n_hot_cells=len(hot),
        places=chosen,
        final=dict(
            n_places=len(chosen),
            coverage_pct_of_top20_mass=chosen[-1]["cum_coverage_pct"]
            if chosen else 0.0,
            truth_clusters_within_day_pct=round(100 * truth_cov, 1),
            coverage_pct_at_half_day=round(100 * alt_mass, 1)))
    json.dump(out, open(OUTDIR / "key_places.json", "w"), indent=1)
    log(f"wrote key_places.json: {len(chosen)} places, "
        f"{out['final']['coverage_pct_of_top20_mass']}% of hot mass, "
        f"{out['final']['truth_clusters_within_day_pct']}% of truth "
        f"clusters within a day")


if __name__ == "__main__":
    main()
