#!/usr/bin/env python3
"""WHERE-TO-ACT plan score for XSA + key-place selection under it.

Three components, one vote each (mean of within-universe ranks - the same
factor-vote method as the shipped composite; no fitted weights):

  context    the shipped composite score (where mining context is;
             measured skill in prediction.json composite_skill)
  pressure   miner-supply gravity  G_i = sum_j pop_j / max(t_ij,30min)^2
             over the 971 stations with MEASURED population (pop is never
             assumed; stations without a measured pop cast no mass).
             Classic market-access form (Harris 1954), beta=2.
  impunity   remoteness from oversight = travel time from the nearest
             town-class place (classification town/city or pop>=5000) -
             long time = less watched. This is a PLAN preference, not an
             evidence claim: it is where acting adds the most eyes.

Plan universe: the top-20% composite surface, refined by the measured
gold∧access conjunction (access_x_geology_eval.json: q_bh_reach 0.04,
lift 1.9-2.4 vs 1.8 gold alone): cells that are BOTH gold-graded-contact
<=5 km AND reachable <=240 min get their conjunction flag carried; the
flag does not change any score, it is drawn so the reader sees where the
strongest measured signal sits.

Selection: same greedy max-coverage as select_key_places_xsa.py, but the
mass covered is the PLAN score, and every pick names its basin; a basin
cap (max 3 picks/basin, the shipped candidate rule) keeps the set
spatially representative - beauty of one consistent method over ad-hoc
trimming.

Writes data/eval/xsa_mining/key_places_plan.json.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from predict_mining_xsa import (load_aoi, make_grid, load_anchors,  # noqa
                                cluster_sites, proj, load_basins,
                                make_region_of)

OUTDIR = ROOT / "data/eval/xsa_mining"
DAY_MIN = 480
MAX_PLACES = 20
MIN_GAIN = 0.01
BASIN_CAP = 3
TOWN_POP = 5000
GRAV_FLOOR_MIN = 30.0   # gravity denominator floor: below half an hour
                        # everything is "here"; stops division blow-ups


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rank01(v):
    """Rank-percentile in [0,1] over finite entries."""
    out = np.full(len(v), np.nan)
    ok = np.isfinite(v)
    r = np.argsort(np.argsort(v[ok]))
    out[ok] = r / max(len(r) - 1, 1)
    return out


def main():
    z = np.load(OUTDIR / "osrm_times.npz")
    t = z["t_min"].astype(np.float32)               # (stations, cells)
    meta = json.load(open(OUTDIR / "osrm_times_meta.json"))
    stations = meta["stations"]
    poly = load_aoi()
    grid = make_grid(poly)
    assert np.allclose(z["cells"], grid.astype(np.float32))
    region_of = make_region_of(load_basins(poly))

    # shipped composite surface
    gj = json.load(open(OUTDIR / "prediction.geojson"))["features"]
    cell_ix = {(round(float(x), 4), round(float(y), 4)): i
               for i, (x, y) in enumerate(grid)}
    score = np.full(len(grid), np.nan)
    tier = {}
    for f in gj:
        pr = f["properties"]
        if pr.get("layer") != "composite_graduated":
            continue
        i = cell_ix.get((round(f["geometry"]["coordinates"][0], 4),
                         round(f["geometry"]["coordinates"][1], 4)))
        if i is not None:
            score[i] = pr["score"]; tier[i] = pr["tier"]
    hot = np.array([i for i, tr in tier.items()
                    if tr in ("top05", "top10", "top20")])
    log(f"{len(hot)} hot cells")

    # pressure: population gravity (measured pops only)
    pops = np.array([s["pop"] or 0 for s in stations], np.float64)
    has_pop = pops > 0
    tt = np.maximum(t, GRAV_FLOOR_MIN) / 60.0       # hours
    grav = (pops[has_pop, None] / tt[has_pop] ** 2).sum(axis=0)
    log(f"gravity from {int(has_pop.sum())} measured-pop stations; "
        f"median {np.median(grav):.0f}")

    # impunity: time from nearest town-class place
    town = np.array([(s["cls"] in ("town", "city")) or
                     ((s["pop"] or 0) >= TOWN_POP) for s in stations])
    t_town = t[town].min(axis=0)
    log(f"{int(town.sum())} town-class stations; median time-from-town "
        f"{np.median(t_town):.0f} min")

    # conjunction flag (drawn, not scored)
    axg = json.load(open(OUTDIR / "access_x_geology_eval.json"))
    from predict_mining_xsa import gold_contact_geoms, nearest_dist_km
    lat0 = poly.centroid.y
    gk = proj(grid, lat0)
    d_gold = nearest_dist_km(gk, gold_contact_geoms(poly), lat0)
    access = t.min(axis=0)
    conj = (d_gold <= 5.0) & (access <= 240)

    # plan score on the hot universe: equal-vote mean of ranks
    comp_r = rank01(score[hot])
    grav_r = rank01(np.log10(grav[hot] + 1))
    imp_r = rank01(t_town[hot])
    plan = (comp_r + grav_r + imp_r) / 3.0
    w = plan / plan.sum()

    # greedy max-coverage with basin cap
    th = t[:, hot]
    cov = th <= DAY_MIN
    anchors = load_anchors(poly)
    clusters = cluster_sites(anchors, lat0)
    from scipy.spatial import cKDTree
    _, tidx = cKDTree(gk).query(np.array([c["km"] for c in clusters]))

    covered = np.zeros(len(hot), bool)
    per_basin = {}
    chosen = []
    total = w.sum()
    while len(chosen) < MAX_PLACES:
        gains = np.where(covered.all(), 0.0, cov[:, ~covered] @ w[~covered])
        order = np.argsort(-gains)
        s = None
        for cand in order:
            b = region_of(stations[cand]["lon"], stations[cand]["lat"])
            if per_basin.get(b, 0) < BASIN_CAP:
                s = int(cand); basin = b
                break
        if s is None or gains[s] < MIN_GAIN * total:
            break
        newly = cov[s] & ~covered
        covered |= cov[s]
        per_basin[basin] = per_basin.get(basin, 0) + 1
        st = stations[s]
        chosen.append(dict(
            rank=len(chosen) + 1,
            name=st["name"] or "(unnamed)", basin=basin,
            kind=st["kind"], cls=st["cls"], pop=st["pop"],
            lon=st["lon"], lat=st["lat"],
            gain_pct=round(100 * float(gains[s]) / total, 1),
            cum_coverage_pct=round(100 * float(w[covered].sum()) / total, 1),
            hot_cells_newly=int(newly.sum()),
            conj_cells_newly=int((conj[hot] & newly).sum()),
            truth_clusters_within_day=int(np.sum(t[s, tidx] <= DAY_MIN))))
        log(f"  #{len(chosen)} {chosen[-1]['name']} [{basin}] "
            f"+{chosen[-1]['gain_pct']}% -> {chosen[-1]['cum_coverage_pct']}%")
        if covered.all():
            break

    # ---- tier 2: remote outposts. The 1%-gain stop leaves the remotest
    # hot mass uncovered (headwaters ground - exactly where the reach null
    # says reporting is blind). SAME greedy, SAME cap, run on the residual
    # mass until 95% of it or MAX_OUTPOSTS; a pick here is an outpost you
    # reach less often, not a lesser place.
    MAX_OUTPOSTS = 8
    outposts = []
    while len(outposts) < MAX_OUTPOSTS and not covered.all():
        resid = w[~covered].sum()
        if resid < 0.05 * total:
            break
        gains = cov[:, ~covered] @ w[~covered]
        order = np.argsort(-gains)
        s = None
        for cand in order:
            b = region_of(stations[cand]["lon"], stations[cand]["lat"])
            if per_basin.get(b, 0) < BASIN_CAP:
                s = int(cand); basin = b
                break
        if s is None or gains[s] <= 0:
            break
        newly = cov[s] & ~covered
        covered |= cov[s]
        per_basin[basin] = per_basin.get(basin, 0) + 1
        st = stations[s]
        outposts.append(dict(
            rank=len(chosen) + len(outposts) + 1,
            name=st["name"] or "(unnamed)", basin=basin,
            kind=st["kind"], cls=st["cls"], pop=st["pop"],
            lon=st["lon"], lat=st["lat"],
            gain_pct=round(100 * float(gains[s]) / total, 1),
            cum_coverage_pct=round(100 * float(w[covered].sum()) / total, 1),
            hot_cells_newly=int(newly.sum()),
            conj_cells_newly=int((conj[hot] & newly).sum()),
            truth_clusters_within_day=int(np.sum(t[s, tidx] <= DAY_MIN))))
        log(f"  outpost #{outposts[-1]['rank']} {outposts[-1]['name']} "
            f"[{basin}] +{outposts[-1]['gain_pct']}% -> "
            f"{outposts[-1]['cum_coverage_pct']}%")

    sel = [i for i in range(len(stations))
           if any(c["lon"] == stations[i]["lon"] and
                  c["lat"] == stations[i]["lat"]
                  for c in chosen + outposts)]
    truth_cov = float(np.mean(t[sel][:, tidx].min(axis=0) <= DAY_MIN))
    conj_hot = conj[hot]
    conj_cov = (float(w[covered & conj_hot].sum() / w[conj_hot].sum())
                if conj_hot.any() else None)

    out = dict(
        generated_by="scripts/plan_score_xsa.py",
        method=("Plan score = equal-vote mean of rank-percentiles of "
                "(composite context, log population gravity "
                "pop/max(t,30min)^2 beta=2 Harris-1954, time-from-town "
                "impunity) on the top-20% surface - the same "
                "factor-vote form as the shipped composite, no fitted "
                "weights. Selection = greedy max-coverage of plan mass "
                f"within {DAY_MIN} min (car/foot/bush best), capped at "
                f"{BASIN_CAP}/basin (the shipped candidate rule)."),
        caveat=("The plan score is a DEPLOYMENT preference, not evidence: "
                "context has measured skill (prediction.json), the "
                "gold-access conjunction has measured skill "
                "(access_x_geology_eval.json, q_bh_reach 0.04), but "
                "gravity and impunity are stated planning preferences. "
                "They change where you stand, never what is claimed."),
        components=dict(
            gravity_stations=int(has_pop.sum()),
            town_stations=int(town.sum()),
            conjunction=dict(cells=int(conj.sum()),
                             source="access_x_geology_eval.json",
                             q_bh_reach=min(s.get("q_bh_reach", 1)
                                            for s in axg["signals"]
                                            if s.get("q_bh_reach")))),
        day_minutes=DAY_MIN,
        places=chosen,
        remote_outposts=outposts,
        final=dict(n_places=len(chosen), n_outposts=len(outposts),
                   plan_mass_covered_pct=round(100 * float(w[covered].sum()) / total, 1),
                   conjunction_mass_covered_pct=round(100 * conj_cov, 1)
                   if conj_cov is not None else None,
                   truth_clusters_within_day_pct=round(100 * truth_cov, 1)))
    json.dump(out, open(OUTDIR / "key_places_plan.json", "w"), indent=1)
    # per-cell surfaces for the GPKG exporter (same grid order as
    # osrm_times.npz cells): plan score is NaN off the hot universe.
    plan_full = np.full(len(grid), np.nan, np.float32)
    plan_full[hot] = plan
    np.savez_compressed(
        OUTDIR / "plan_surface.npz",
        cells=grid.astype(np.float32),
        plan=plan_full,
        conj=conj.astype(np.uint8),
        access_min=access.astype(np.float32),
        t_town_min=t_town.astype(np.float32))
    log("wrote key_places_plan.json + plan_surface.npz")


if __name__ == "__main__":
    main()
