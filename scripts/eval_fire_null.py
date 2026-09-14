#!/usr/bin/env python3
"""
Null-model skill test for the fire trajectory tracker.

The A/B harness (eval_fire_trajectories.py) says whether a change links MORE;
this one says whether the links are REAL. It runs the production builder twice
on the same detections: as recorded, and with the calendar days randomly
permuted within each month (the spatial fire field and the seasonal march are
kept; every day-to-day continuity is destroyed). A tracker that measures fire
movement must produce clearly longer, more numerous multi-day fronts on the
real ordering. If it produces the same on both, it is drawing noise.

Measured 2026-09-14 on XSA 2024 with the v7 tracker: 9,500 real vs 10,468
null groups, median 16 days on both, 2,912 vs 3,675 fronts >=150 km. That
is a skill of ~0 and it is why the v8 gates exist.

    skill = 1 - null / real        (per metric; 0 = indistinguishable from
                                    noise, 1 = noise produces nothing)

Usage:
    python3 scripts/eval_fire_null.py --aoi XSA_Study_Area --from 2024-01-01 --to 2024-06-30
    python3 scripts/eval_fire_null.py --park CAF_Chinko --from 2023-11-01 --to 2024-05-31 --v7
    # fast iteration on a sub-box (lon_min,lat_min,lon_max,lat_max):
    python3 scripts/eval_fire_null.py --aoi XSA_Study_Area --from 2024-01-01 --to 2024-03-31 \
        --bbox 26,7,28,9 --set AMBIGUITY_RATIO=2
"""
import argparse
import json
import math
import random
import statistics as st
import sys
import time
import warnings
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

LONG_KM = 150.0        # what plan_solver.movement() calls a long front


def _load(args):
    import rebuild_fire_trajectories_v5 as B
    from fire_source import load_aoi_fires, load_park_fires_db
    import aoi_lib
    parks = {}
    if args.aoi:
        pk = aoi_lib.inject_aoi(parks, args.aoi)
        B.AOI_IDS.add(args.aoi)
        fires = load_aoi_fires(args.aoi, args.date_from, args.date_to)
        pid = args.aoi
    else:
        parks = B.load_parks()
        pk = parks[args.park]
        fires = load_park_fires_db(args.park, args.date_from, args.date_to)
        pid = args.park
    if args.bbox:
        x0, y0, x1, y1 = [float(v) for v in args.bbox.split(",")]
        fires = [f for f in fires if x0 <= f["longitude"] <= x1 and y0 <= f["latitude"] <= y1]
    return pid, pk["geometry"], fires


def shuffle_days(fires, seed):
    """Permute calendar days within each month; keep time-of-day."""
    from fire_source import date_num
    rnd = random.Random(seed)
    by_m = defaultdict(set)
    for f in fires:
        by_m[f["acq_date"][:7]].add(f["acq_date"])
    remap = {}
    for m, ds in by_m.items():
        ds = sorted(ds)
        perm = ds[:]
        rnd.shuffle(perm)
        remap.update(zip(ds, perm))
    out = []
    for f in fires:
        g = dict(f)
        g["acq_date"] = remap[f["acq_date"]]
        g["acq_dt"] = date_num(g["acq_date"]) + (f["acq_dt"] - math.floor(f["acq_dt"]))
        out.append(g)
    return out


def _run(job):
    tag, pid, geom, fires, overrides, v7 = job
    import rebuild_fire_trajectories_v5 as B
    if v7:
        for k in ("AMBIGUITY_RATIO", "CONTIGUITY_KM", "DENSITY_GATE_K",
                  "LLR_COST_BITS_KM", "SPRT_CUT_BITS"):
            setattr(B, k, 0.0)
    for k, v in overrides.items():
        setattr(B, k, type(getattr(B, k))(v))
    t = time.time()
    groups = B.process_park_fires(fires, pid, geom)
    return tag, groups, time.time() - t


def metrics(groups):
    if not groups:
        return {k: 0 for k in ORDER}
    q = lambda v, p: sorted(v)[int(p * (len(v) - 1))]
    fc = [g["fire_count"] for g in groups]
    days = [g["days"] for g in groups]
    long_ = [g for g in groups if g["distance_km"] >= LONG_KM]
    multi = [g for g in groups if g["days"] >= 2]
    links = sum(len(g["trajectory"]) - 1 for g in groups)
    return {
        "groups": len(groups),
        "fires_captured": sum(fc),
        "links": links,
        "fires_per_grp": st.mean(fc),
        "med_days": q(days, .5),
        "mean_days": st.mean(days),
        "p90_days": q(days, .9),
        "med_dist_km": q([g["distance_km"] for g in groups], .5),
        "long_fronts": len(long_),
        "fires_in_long": sum(g["fire_count"] for g in long_),
        "transhumance": sum(1 for g in groups if g["group_type"] == "transhumance"),
        "link_margin": st.mean(g.get("link_margin", 1.0) for g in multi) if multi else 0,
        "evidence_bits": st.mean(g.get("evidence_bits", 0.0) for g in multi) if multi else 0,
        "long_ev_ge6": sum(1 for g in long_ if g.get("evidence_bits", 0) >= 6),
    }


ORDER = ["groups", "fires_captured", "links", "fires_per_grp", "med_days", "mean_days",
         "p90_days", "med_dist_km", "long_fronts", "fires_in_long", "transhumance",
         "link_margin", "evidence_bits", "long_ev_ge6"]
# Metrics where "real >> null" is the claim of skill (the rest are context).
SKILL = ["links", "fires_per_grp", "mean_days", "p90_days", "long_fronts", "fires_in_long", "long_ev_ge6"]


def fmt(v):
    return f"{v:,}" if isinstance(v, int) else f"{v:,.2f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aoi")
    ap.add_argument("--park")
    ap.add_argument("--from", dest="date_from", required=True)
    ap.add_argument("--to", dest="date_to", required=True)
    ap.add_argument("--bbox", help="lon_min,lat_min,lon_max,lat_max sub-box")
    ap.add_argument("--seeds", type=int, default=1, help="number of null permutations")
    ap.add_argument("--v7", action="store_true", help="disable v8 gates")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--json", help="write metrics + skill here")
    ap.add_argument("--dump", help="write real/null groups here (for inspection)")
    args = ap.parse_args()
    if bool(args.aoi) == bool(args.park):
        ap.error("exactly one of --aoi / --park")
    overrides = dict(kv.split("=", 1) for kv in args.set)
    overrides = {k.strip().upper(): v for k, v in overrides.items()}

    pid, geom, fires = _load(args)
    print(f"{pid} {args.date_from}..{args.date_to}"
          + (f" bbox {args.bbox}" if args.bbox else "")
          + f": {len(fires):,} detections; gates {'v7' if args.v7 else 'v8'} {overrides or ''}")
    if len(fires) < 1000:
        print("too few detections for a meaningful null test")
        sys.exit(2)

    jobs = [("real", pid, geom, fires, overrides, args.v7)]
    for s in range(args.seeds):
        jobs.append((f"null{s}", pid, geom, shuffle_days(fires, 1 + s), overrides, args.v7))
    with Pool(min(len(jobs), 4)) as pool:
        res = {tag: (g, dt) for tag, g, dt in pool.imap_unordered(_run, jobs)}

    real = metrics(res["real"][0])
    nulls = [metrics(res[f"null{s}"][0]) for s in range(args.seeds)]
    null = {k: st.mean(n[k] for n in nulls) for k in ORDER}

    print(f"\n{'metric':16}{'real':>14}{'null':>14}{'skill':>9}   (skill = 1 - null/real)")
    print("-" * 60)
    skill = {}
    for k in ORDER:
        r, n = real[k], null[k]
        if k in SKILL:
            skill[k] = (1 - n / r) if r else 0.0
            tag = f"{skill[k]:+.2f}"
        else:
            tag = ""
        print(f"{k:16}{fmt(r):>14}{fmt(n):>14}{tag:>9}")
    print("-" * 60)
    agg = st.mean(skill.values())
    print(f"mean skill {agg:+.2f}   ({res['real'][1]:.0f}s real)")
    print("0 = the tracker's output is indistinguishable from day-shuffled noise;")
    print("1 = noise produces nothing. Judge a change on skill AND on real links kept.")
    if args.json:
        json.dump({"real": real, "null": null, "skill": skill, "mean_skill": agg,
                   "detections": len(fires), "overrides": overrides, "v7": args.v7},
                  open(args.json, "w"), indent=1)
    if args.dump:
        json.dump({"real": res["real"][0], "null": res["null0"][0]}, open(args.dump, "w"))


if __name__ == "__main__":
    main()
