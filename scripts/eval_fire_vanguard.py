#!/usr/bin/env python3
"""
Null test for the VANGUARD claim on production trajectories.

Claim: AHEAD of the season front the tracker's day-to-day links carry
information (the scouts' ignition chains); inside the season they do not
(real ~ day-shuffled, docs/agents/fire.md). So, per season of an area:

  1. every detection gets its lead against the STORED season front
     (fire_front.FrontSet, the same grid the app tags groups with);
  2. the detections with lead >= L are tracked as recorded and with their
     days shuffled within each month (eval_fire_null.shuffle_days);
  3. skill = 1 - null/real on links, fires per group, days, long chains.

The field as a whole is the control (L = -inf): skill ~ 0 there. What must
NOT be read as skill is the COUNT of vanguard groups after shuffling the
whole field -- shuffling moves detections onto pre-front days and the null
gets more of them (measured 2026-09-14: 749 real vs 1,117 null at L=10).

    python3 scripts/eval_fire_vanguard.py --area XSA_Study_Area --season 2024/25
    python3 scripts/eval_fire_vanguard.py --area XSA_Study_Area --season 2025/26 --leads 10 --seeds 2
"""
import argparse
import json
import sqlite3
import statistics as st
import sys
import time
import warnings
from multiprocessing import Pool
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

import fire_front as FF  # noqa: E402
from eval_fire_null import shuffle_days, metrics, SKILL  # noqa: E402
from fire_source import DB_PATH, load_aoi_fires, load_park_fires_db  # noqa: E402

SHOW = ["groups", "links", "fires_per_grp", "mean_days", "p90_days", "med_dist_km",
        "long_fronts", "fires_in_long"]


def _run(job):
    tag, pid, geom, fires = job
    import rebuild_fire_trajectories_v5 as B
    B.AOI_IDS.add(pid)  # harmless for parks (only consulted for AOI ids)
    t = time.time()
    return tag, B.process_park_fires(fires, pid, geom), time.time() - t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--area", required=True)
    ap.add_argument("--season", required=True, help="e.g. 2024/25 (a stored fire_season_front row)")
    ap.add_argument("--leads", default="5,10,15", help="lead thresholds in days; 'all' = whole field control")
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--json")
    a = ap.parse_args()
    leads = [None if x == "all" else float(x) for x in a.leads.split(",")]

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    fs = FF.FrontSet(conn, a.area)
    if a.season not in fs.seasons:
        sys.exit(f"no front stored for {a.area} {a.season}; run fire_front.py --area {a.area}")
    S = fs.seasons[a.season]
    s0, s1 = FF.season_bounds(a.season, fs.start_month)
    import rebuild_fire_trajectories_v5 as B
    import aoi_lib
    if FF.area_kind(conn, a.area) == "aoi":
        pk = aoi_lib.inject_aoi({}, a.area)
        fires = load_aoi_fires(a.area, s0.isoformat(), s1.isoformat())
    else:
        pk = B.load_parks()[a.area]
        fires = load_park_fires_db(a.area, s0.isoformat(), s1.isoformat())
    conn.close()
    lead = fs.lead_array([f["longitude"] for f in fires], [f["latitude"] for f in fires],
                         [f["acq_date"] for f in fires])
    print(f"{a.area} {a.season} ({s0}..{s1}): {len(fires):,} detections, "
          f"front {'complete' if S['complete'] else 'live'}", flush=True)

    out = {}
    for L in leads:
        sel = fires if L is None else [f for f, v in zip(fires, lead) if v >= L]
        tag = "all" if L is None else f"L>={L:g}"
        print(f"\n[{tag}] {len(sel):,} detections ({100 * len(sel) / max(1, len(fires)):.1f}%)", flush=True)
        if len(sel) < 500:
            print("  too few detections")
            continue
        jobs = [("real", a.area, pk["geometry"], sel)]
        for s in range(a.seeds):
            jobs.append((f"null{s}", a.area, pk["geometry"], shuffle_days(sel, 7 + s)))
        with Pool(min(len(jobs), 3)) as pool:
            res = {t: (g, dt) for t, g, dt in pool.imap_unordered(_run, jobs)}
        real = metrics(res["real"][0])
        null = {k: st.mean(metrics(res[f"null{s}"][0])[k] for s in range(a.seeds)) for k in real}
        skill = {k: (1 - null[k] / real[k]) if real[k] else 0.0 for k in SKILL}
        print(f"  {'metric':14}{'real':>10}{'null':>10}{'skill':>8}")
        for k in SHOW:
            r, n = real[k], null[k]
            fm = (lambda v: f"{v:,.0f}") if isinstance(r, int) else (lambda v: f"{v:,.2f}")
            sk = f"{skill[k]:+.2f}" if k in skill else ""
            print(f"  {k:14}{fm(r):>10}{fm(n):>10}{sk:>8}")
        ms = st.mean(skill.values())
        print(f"  mean skill {ms:+.2f}  ({res['real'][1]:.0f}s)")
        out[tag] = {"detections": len(sel), "real": real, "null": null, "skill": skill, "mean_skill": ms}
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
