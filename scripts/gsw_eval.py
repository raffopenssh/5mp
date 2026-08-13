#!/usr/bin/env python3
"""WP4 skill measurement: do reported mine sites have new-water candidates
nearby more often than random ground? (docs/PLAN_NEW_DATA_LAYERS.md R4.)

Capture = % of reported mine sites inside the study AOI with a GSW new-water
candidate within CAPTURE_KM. Baseline = the same for random points drawn in
the sites' OWN convex hull (acled.md discipline: a site series without a
random-ground series beside it is a number with no denominator; the hull, not
the whole AOI, so the model is not credited for merely being where reporting
happened). p by label permutation; the power ceiling prints beside any null
("a null without its power is a shrug wearing a result's clothes").

Output: data/eval/gsw_new_water.json (committed).
Invoked via  python3 scripts/gsw_new_water.py --eval
"""
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CAPTURE_KM = 2.0
N_RANDOM = 500
N_PERM = 20000
AOI_ID = "XSA_Study_Area"


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def captured(pt, cands):
    return any(haversine_km(pt[0], pt[1], c["lat"], c["lon"]) <= CAPTURE_KM
               for c in cands)


def run_eval(conn):
    from shapely.geometry import shape as shp, Point, MultiPoint
    import gsw_new_water as gnw

    cand_path = gnw.OUT_DIR / f"{AOI_ID}.json"
    if not cand_path.exists():
        raise SystemExit(f"no candidates yet — run --aoi {AOI_ID} first")
    area = json.load(open(cand_path))
    if area.get("status") != "finished":
        raise SystemExit(f"{AOI_ID} extraction is {area.get('status')} — "
                         "refusing to measure skill on a partial layer (R1)")
    cands = area["candidates"]

    row = conn.execute("SELECT geometry FROM aois WHERE id=?", (AOI_ID,)).fetchone()
    aoi_geom = shp(json.loads(row["geometry"]))

    sites = [s for s in gnw.load_mine_sites()
             if aoi_geom.contains(Point(s["lon"], s["lat"]))]
    if not sites:
        raise SystemExit("0 mine sites inside the AOI — UNFINISHED, not a null (R1)")
    from collections import Counter
    observed_mix = dict(Counter(s["observed"] for s in sites))

    # The sites' own hull (see module docstring).
    hull = MultiPoint([(s["lon"], s["lat"]) for s in sites]).convex_hull
    hull = hull.intersection(aoi_geom)
    minx, miny, maxx, maxy = hull.bounds
    rng = random.Random(54)  # fixed seed: the eval must reproduce
    rand_pts = []
    while len(rand_pts) < N_RANDOM:
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if hull.contains(p):
            rand_pts.append((p.y, p.x))

    site_hits = [captured((s["lat"], s["lon"]), cands) for s in sites]
    rand_hits = [captured(p, cands) for p in rand_pts]
    cap_sites = sum(site_hits) / len(site_hits)
    cap_rand = sum(rand_hits) / len(rand_hits)
    lift = (cap_sites / cap_rand) if cap_rand > 0 else None

    # Label permutation over the pooled points: does the site/random split
    # explain the capture difference?
    pooled = site_hits + rand_hits
    ns = len(site_hits)
    obs = cap_sites - cap_rand
    ge = 0
    for _ in range(N_PERM):
        rng.shuffle(pooled)
        d = sum(pooled[:ns]) / ns - sum(pooled[ns:]) / (len(pooled) - ns)
        if d >= obs:
            ge += 1
    p = (ge + 1) / (N_PERM + 1)

    # Power ceiling: with ns sites and base rate cap_rand, the smallest
    # capture delta a one-sided alpha=0.05 binomial test could detect at 80%
    # power. Normal approximation is fine at these ns; the point is the
    # order of magnitude beside a null.
    if cap_rand in (0.0, 1.0):
        min_delta = None
    else:
        se0 = math.sqrt(cap_rand * (1 - cap_rand) / ns)
        min_delta = round((1.645 + 0.84) * se0, 3)

    result = {
        "generated_by": "scripts/gsw_eval.py (via gsw_new_water.py --eval)",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **{k: area[k] for k in ("source", "citation", "terms", "notice",
                                "raster_period", "raster_end_year",
                                "end_year_caveat", "constants")},
        "question": (f"do reported mine sites in {AOI_ID} have a GSW "
                     f"new-water candidate within {CAPTURE_KM} km more often "
                     "than random points in the sites' own hull?"),
        "aoi": AOI_ID,
        "candidates_evaluated": len(cands),
        "capture_km": CAPTURE_KM,
        "n_sites": len(sites),
        "site_observed_mix": observed_mix,
        "site_mix_caveat": ("XSA truth rows are community reports and OSM "
                            "tags, not field visits; coordinates may be "
                            "village label positions (acled.md trap)"),
        "n_random": N_RANDOM,
        "baseline_frame": "convex hull of the truth sites, clipped to the AOI",
        "capture_sites": round(cap_sites, 4),
        "capture_random": round(cap_rand, 4),
        "lift": round(lift, 2) if lift is not None else None,
        "lift_note": None if lift is not None else
            "UNMEASURED as a ratio: zero random-ground captures",
        "permutation_p": round(p, 5),
        "n_permutations": N_PERM,
        "min_detectable_delta": min_delta,
        "power_note": (f"with {len(sites)} sites this eval can only detect a "
                       f"capture delta >= {min_delta}; a smaller true effect "
                       "reads as null" if min_delta is not None else
                       "power ceiling incomputable at this base rate"),
    }
    # Ship/stop verdict, derived not typed.
    signal = p < 0.05 and (lift or 0) > 1
    result["verdict"] = (
        "SIGNAL: candidates concentrate at reported mine sites — a map layer "
        "is justified" if signal else
        "NO SIGNAL at this power: commit this eval, ship no map layer "
        "(WP4 stop gate; mirrors mining.md)")
    out = BASE_DIR / "data" / "eval" / "gsw_new_water.json"
    with open(out, "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("citation", "terms", "notice")}, indent=1))
    print(f"-> {out}")
    return result
