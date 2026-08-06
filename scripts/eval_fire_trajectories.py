#!/usr/bin/env python3
"""
Fire trajectory A/B evaluation harness.

Compares two `fire_groups_v5`-schema output directories on a golden park set
and prints per-park + aggregate quality metrics, so algorithm/parameter
changes can be judged instead of guessed at.

Usage:
    # 1. snapshot current production output as the baseline
    python3 scripts/eval_fire_trajectories.py --snapshot data/eval/baseline

    # 2. build a candidate with whatever change you're testing
    for p in CAF_Chinko ZMB_Kafue COD_Virunga TZA_Serengeti; do
      python3 scripts/rebuild_fire_trajectories_v5.py --park $p \
        --output-dir data/eval/candidate
    done

    # 3. compare
    python3 scripts/eval_fire_trajectories.py \
        --baseline data/eval/baseline --candidate data/eval/candidate

    # single-dir metrics only
    python3 scripts/eval_fire_trajectories.py --candidate data/eval/candidate

Metrics (per park, and aggregated):
    groups              total emitted groups
    fires               total fires captured by groups
    fires_per_grp       mean group size (higher = less fragmentation)
    multiday_pct        % of groups spanning >=2 days
    mean_days           mean group duration
    zigzag_mean         mean zigzag_ratio (lower = cleaner geometry)
    zigzag_bad_pct      % of groups over MAX_ZIGZAG_RATIO (0.3)
    dup_pairs           near-duplicate group pairs: start within 3 days AND
                        first_point within 5km. Proxy for over-splitting -
                        two "different" fires that are really one.
    frag_pct            % of groups that look like fragments: <=2 days and
                        <20 fires
    stationary_pct      % of groups burning >=60 days inside a <3km box - i.e.
                        not fires at all but flares/lava/kilns. Lower is better.
    stationary_fire_pct % of captured detections locked up in those groups.
                        This is the metric the persistent-hotspot mask targets;
                        mean_days/traj_pts/coverage_pct all *drop* when such
                        artefacts are removed, so don't read them in isolation.
    coverage_pct        % of raw park fires (post MIN_DATE) inside some group
    traj_pts            mean trajectory vertices per multi-day group
    speed_p95           95th pct speed_km_day; runaway values signal bad links
"""

import json
import math
import argparse
import shutil
import statistics
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from fire_source import park_fire_count

BASE_DIR = Path(__file__).parent.parent
PROD_DIR = BASE_DIR / "data" / "fire_groups_v5"

# Golden set: deliberately diverse fire regimes.
#   CAF_Chinko      - long transhumance trajectories, the original design case
#   ZMB_Kafue       - extreme volume, peak-season miombo (7.5k groups)
#   COD_Virunga     - mixed, montane + agricultural edge
#   TZA_Serengeti   - grassland, fast-moving fronts
#   CMR_Nki         - rainforest, near-zero fire (regression canary)
#   MOZ_Niassa      - very high volume, big park
GOLDEN_PARKS = [
    "CAF_Chinko",
    "ZMB_Kafue",
    "COD_Virunga",
    "TZA_Serengeti",
    "CMR_Nki",
    "MOZ_Niassa",
]

MIN_DATE = "2020-01-01"
MAX_ZIGZAG_RATIO = 0.3
DUP_DAYS = 3
DUP_KM = 5.0
# A "stationary" group: burns for ages without going anywhere. Real fire fronts
# move; gas flares, lava lakes and brick kilns don't. See build_persistent_hotspots.py.
STATIONARY_DAYS = 60
STATIONARY_EXTENT_KM = 3.0

# Metrics where a larger value is better; used only for arrow direction.
HIGHER_IS_BETTER = {
    "fires", "fires_per_grp", "multiday_pct", "mean_days",
    "coverage_pct", "traj_pts",
}
# Metrics that are informational only (no better/worse direction).
NEUTRAL = {"groups", "speed_p95"}


def haversine(lon1, lat1, lon2, lat2):
    R = 6371
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


_raw_cache = {}


def raw_fire_count(park_id):
    """Total available detections for the park, from SQLite (canonical).

(It used to read data/raw-fire-viirs-*/, a rolling window, which made
    coverage_pct read >1000%. Those files are gone.)
    """
    if park_id not in _raw_cache:
        _raw_cache[park_id] = park_fire_count(park_id, MIN_DATE)
    return _raw_cache[park_id]


def group_extent_km(g):
    """Max pairwise span of the trajectory bbox, in km (cheap diagonal)."""
    traj = g.get("trajectory") or []
    if len(traj) < 2:
        return 0.0
    xs = [p[0] for p in traj]
    ys = [p[1] for p in traj]
    return haversine(min(xs), min(ys), max(xs), max(ys))


def count_dup_pairs(groups):
    """Near-duplicate pairs, bucketed by date to stay O(n * small)."""
    by_day = defaultdict(list)
    for g in groups:
        by_day[g.get("start_date", "")].append(g)
    days = sorted(by_day.keys())
    idx = {d: i for i, d in enumerate(days)}
    dups = 0
    for d in days:
        # compare against this day and the next DUP_DAYS calendar-adjacent keys
        i = idx[d]
        window = [d] + days[i + 1: i + 1 + DUP_DAYS + 2]
        for j, d2 in enumerate(window):
            for a_i, a in enumerate(by_day[d]):
                start = a_i + 1 if d2 == d else 0
                for b in by_day[d2][start:]:
                    if a is b:
                        continue
                    try:
                        if abs((_dnum(a["start_date"]) - _dnum(b["start_date"]))) > DUP_DAYS:
                            continue
                        pa, pb = a.get("first_point"), b.get("first_point")
                        if not pa or not pb:
                            continue
                        if haversine(pa[0], pa[1], pb[0], pb[1]) <= DUP_KM:
                            dups += 1
                    except Exception:
                        continue
    return dups


_dcache = {}


def _dnum(s):
    v = _dcache.get(s)
    if v is None:
        y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
        v = y * 372 + m * 31 + d  # monotonic enough for small deltas
        _dcache[s] = v
    return v


def percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def park_metrics(groups, park_id):
    n = len(groups)
    if n == 0:
        return {"groups": 0, "fires": 0, "fires_per_grp": 0.0, "multiday_pct": 0.0,
                "mean_days": 0.0, "zigzag_mean": 0.0, "zigzag_bad_pct": 0.0,
                "dup_pairs": 0, "frag_pct": 0.0, "coverage_pct": 0.0,
                "traj_pts": 0.0, "speed_p95": 0.0,
                "stationary_pct": 0.0, "stationary_fire_pct": 0.0}

    fires = sum(g.get("fire_count", 0) for g in groups)
    days = [g.get("days", 1) for g in groups]
    multiday = [g for g in groups if g.get("days", 1) >= 2]
    zz = [g.get("zigzag_ratio", 0.0) or 0.0 for g in groups]
    frag = [g for g in groups
            if g.get("days", 1) <= 2 and g.get("fire_count", 0) < 20]
    traj_pts = [len(g.get("trajectory", [])) for g in multiday]
    speeds = [g.get("speed_km_day", 0.0) or 0.0 for g in groups]
    stationary = [g for g in groups
                  if g.get("days", 1) >= STATIONARY_DAYS
                  and group_extent_km(g) < STATIONARY_EXTENT_KM]
    stationary_fires = sum(g.get("fire_count", 0) for g in stationary)
    raw = raw_fire_count(park_id)

    return {
        "groups": n,
        "fires": fires,
        "fires_per_grp": fires / n,
        "multiday_pct": 100.0 * len(multiday) / n,
        "mean_days": statistics.fmean(days),
        "zigzag_mean": statistics.fmean(zz),
        "zigzag_bad_pct": 100.0 * sum(1 for z in zz if z > MAX_ZIGZAG_RATIO) / n,
        "dup_pairs": count_dup_pairs(groups),
        "frag_pct": 100.0 * len(frag) / n,
        "coverage_pct": (100.0 * fires / raw) if raw else 0.0,
        "traj_pts": statistics.fmean(traj_pts) if traj_pts else 0.0,
        "speed_p95": percentile(speeds, 0.95),
        "stationary_pct": 100.0 * len(stationary) / n,
        "stationary_fire_pct": (100.0 * stationary_fires / fires) if fires else 0.0,
    }


ORDER = ["groups", "fires", "fires_per_grp", "multiday_pct", "mean_days",
         "traj_pts", "zigzag_mean", "zigzag_bad_pct", "dup_pairs",
         "frag_pct", "stationary_pct", "stationary_fire_pct",
         "coverage_pct", "speed_p95"]


def load_dir(d, parks):
    out = {}
    for p in parks:
        f = Path(d) / f"{p}.json"
        if not f.exists():
            continue
        with open(f) as fh:
            out[p] = json.load(fh)
    return out


def fmt(v):
    if isinstance(v, int):
        return f"{v:,}"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:.2f}"


def arrow(metric, base, cand):
    if metric in NEUTRAL or base == cand:
        return ""
    better = (cand > base) if metric in HIGHER_IS_BETTER else (cand < base)
    return " ✓" if better else " ✗"


def pct_delta(base, cand):
    if base == 0:
        return "  n/a" if cand == 0 else "   new"
    return f"{100.0 * (cand - base) / abs(base):+6.1f}%"


def print_single(name, metrics_by_park):
    parks = list(metrics_by_park.keys())
    w = max([12] + [len(p) for p in parks])
    print(f"\n{name}")
    print("-" * (w + 2 + 13 * len(parks) + 13))
    hdr = "metric".ljust(16) + "".join(p[:12].rjust(13) for p in parks) + "TOTAL/MEAN".rjust(13)
    print(hdr)
    for m in ORDER:
        vals = [metrics_by_park[p][m] for p in parks]
        agg = sum(vals) if m in ("groups", "fires", "dup_pairs") else (statistics.fmean(vals) if vals else 0)
        print(m.ljust(16) + "".join(fmt(v).rjust(13) for v in vals) + fmt(agg).rjust(13))


def print_compare(base, cand):
    parks = [p for p in cand.keys() if p in base]
    if not parks:
        print("No overlapping parks between baseline and candidate.")
        return
    print(f"\nBASELINE vs CANDIDATE  ({len(parks)} parks: {', '.join(parks)})")
    print("=" * 78)
    print("metric".ljust(16) + "baseline".rjust(13) + "candidate".rjust(13)
          + "delta".rjust(13) + "  per-park")
    print("-" * 78)
    for m in ORDER:
        bv = [base[p][m] for p in parks]
        cv = [cand[p][m] for p in parks]
        if m in ("groups", "fires", "dup_pairs"):
            b, c = sum(bv), sum(cv)
        else:
            b, c = statistics.fmean(bv), statistics.fmean(cv)
        detail = " ".join(
            f"{p.split('_')[0]}:{pct_delta(base[p][m], cand[p][m]).strip()}"
            for p in parks)
        print(m.ljust(16) + fmt(b).rjust(13) + fmt(c).rjust(13)
              + (pct_delta(b, c) + arrow(m, b, c)).rjust(13) + "  " + detail)
    print("-" * 78)
    print("✓ = improved, ✗ = regressed. groups/speed_p95 are informational.")
    print("Watch for: fires_per_grp up + dup_pairs down = less fragmentation.")
    print("           coverage_pct down = fires being dropped (usually bad) -")
    print("           EXCEPT when the change deliberately drops junk detections")
    print("           (hotspot mask): then read stationary_pct/stationary_fire_pct,")
    print("           and expect mean_days/traj_pts/coverage to fall for good reasons.")


def main():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__)
    ap.add_argument("--baseline", help="Baseline output dir")
    ap.add_argument("--candidate", help="Candidate output dir (default: production)")
    ap.add_argument("--snapshot", metavar="DIR",
                    help="Copy golden-park production output into DIR and exit")
    ap.add_argument("--parks", help="Comma-separated park override")
    ap.add_argument("--json", metavar="FILE", help="Also write metrics as JSON")
    args = ap.parse_args()

    parks = args.parks.split(",") if args.parks else GOLDEN_PARKS

    if args.snapshot:
        dest = Path(args.snapshot)
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        for p in parks:
            src = PROD_DIR / f"{p}.json"
            if src.exists():
                shutil.copy2(src, dest / f"{p}.json")
                n += 1
            else:
                print(f"  warning: no production output for {p}")
        print(f"Snapshotted {n} parks -> {dest}")
        return

    cand_dir = args.candidate or PROD_DIR
    cand_groups = load_dir(cand_dir, parks)
    if not cand_groups:
        print(f"No park files found in {cand_dir}")
        return
    cand = {p: park_metrics(g, p) for p, g in cand_groups.items()}

    if args.baseline:
        base_groups = load_dir(args.baseline, parks)
        base = {p: park_metrics(g, p) for p, g in base_groups.items()}
        print_compare(base, cand)
    else:
        print_single(f"METRICS: {cand_dir}", cand)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(cand, fh, indent=2)
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
