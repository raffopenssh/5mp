#!/usr/bin/env python3
"""
Backfill VIIRS SNPP + NOAA-21 history so per-overpass tracking can be tested.

WHY
---
Until 2026-08 only VIIRS_NOAA20 was ingested (~2 overpasses/day). The v7
trajectory builder can slice by satellite overpass instead of calendar day
(`--overpass`), which should roughly triple trajectory resolution -- but it is
OFF by default because with one satellite the day/night passes are wildly
asymmetric (ZMB_Kafue: day pass 763 fires @ FRP 10.6, night pass 63 @ FRP 1.7),
so alternating them injects an oscillation that trips the turn gate
(mean_days -21%, dup_pairs +23%). See docs/FIRE_PIPELINE.md § v7.

With SNPP (2012-) and NOAA-21 (2023-) backfilled there are ~6 passes/day, so
each overpass slice should have enough detections to stand on its own. This
script fetches that history; then re-run the A/B to decide whether to flip
USE_OVERPASS on.

USAGE
-----
    # See what would be fetched
    python3 scripts/backfill_viirs_sensors.py --dry-run

    # Backfill one sensor, one year (5-day chunks)
    python3 scripts/backfill_viirs_sensors.py --sensor SNPP --from 2025-01-01 --to 2025-12-31

    # Everything the animator/eval window needs (2020->now, both sensors).
    # Long-running: run under tmux, it is resumable (INSERT OR IGNORE + state file).
    python3 scripts/backfill_viirs_sensors.py --sensor SNPP,NOAA21 --from 2020-01-01

THEN
----
    python3 scripts/build_fire_grid_agg.py                  # animator aggregates
    python3 scripts/eval_fire_trajectories.py --snapshot data/eval/pre_overpass
    for p in CAF_Chinko ZMB_Kafue COD_Virunga TZA_Serengeti CMR_Nki MOZ_Niassa; do
      python3 scripts/rebuild_fire_trajectories_v5.py --park $p \
        --overpass --output-dir data/eval/overpass
    done
    python3 scripts/eval_fire_trajectories.py \
        --baseline data/eval/pre_overpass --candidate data/eval/overpass
    # Flip USE_OVERPASS=True in rebuild_fire_trajectories_v5.py only if
    # fires_per_grp / frag_pct / mean_days / coverage all hold or improve.

NOTES
-----
- Coverage per sensor is NOT uniform and is hardcoded in SENSORS from the FIRMS
  availability endpoint. NOAA-21 has NO SP archive (NRT only, from 2024-01-17),
  and each sensor has its own SP->NRT cutover date. Guessing source names gives
  HTTP 400. Refresh with:
    curl "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/$KEY/ALL"
- The API caps a dated request at 5 days, hence the chunking.
- Realistic depth: SNPP back to 2020 (2012 if wanted), NOAA-21 only to
  2024-01-17. So an overpass A/B is best run on 2024+ where all three
  sensors overlap.
- fire_detections UNIQUE(lat, lon, acq_date, acq_time, satellite) makes this
  idempotent: re-running never duplicates.
- Park assignment reuses ParkAssigner (nearest boundary within 100km), same as
  the nightly pipeline, so rows are directly comparable.
- Expect volume: SNPP is comparable to NOAA-20 (~500k-1M rows/year for the
  Africa bbox), NOAA-21 similar from 2023. Check disk before a full run --
  db.sqlite3 was already 9.8GB in 2026-08.
"""

import sys
import os
import json
import time
import sqlite3
import argparse
import requests
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from secrets_config import secret

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
STATE_FILE = BASE_DIR / "data" / "viirs_backfill_state.json"

NASA_API_KEY = secret('NASA_FIRMS_KEY')
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
AREA = "-20,-35,55,40"   # Africa bbox, same as daily_fire_update
# FIRMS per-request maximum is 5 days, NOT 10. Asking for 10 returns
# HTTP 400 "Invalid day range. Expects [1..5]." (daily_fire_update's
# DEFAULT_DAYS=10 works because it uses the /{days} form without a date.)
CHUNK_DAYS = 5

# Sensor -> list of (source, min_date, max_date) segments, oldest first.
#
# These ranges come from the FIRMS availability endpoint and are NOT uniform -
# each sensor has its own SP/NRT cutover, and NOAA-21 has NO SP archive at all
# (NRT only, back to 2024-01-17). Guessing "<sensor>_SP" gives HTTP 400.
# Refresh with:
#   curl "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/$KEY/ALL"
SENSORS = {
    'SNPP': [
        ("VIIRS_SNPP_SP",  "2012-01-20", "2026-04-27"),
        ("VIIRS_SNPP_NRT", "2026-04-28", None),
    ],
    'NOAA20': [
        ("VIIRS_NOAA20_SP",  "2018-04-01", "2026-05-31"),
        ("VIIRS_NOAA20_NRT", "2026-06-01", None),
    ],
    'NOAA21': [
        # No SP archive exists for NOAA-21.
        ("VIIRS_NOAA21_NRT", "2024-01-17", None),
    ],
}


def segments_for(sensor, start, end):
    """Split [start, end] into (source, seg_start, seg_end) by availability."""
    out = []
    for source, smin, smax in SENSORS[sensor]:
        lo = max(start, smin)
        hi = min(end, smax) if smax else end
        if lo < hi:
            out.append((source, lo, hi))
    return out


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def parse_firms_csv(text):
    lines = text.strip().split('\n')
    if len(lines) < 2:
        return []
    header = lines[0].split(',')
    out = []
    for line in lines[1:]:
        vals = line.split(',')
        if len(vals) >= len(header):
            out.append(dict(zip(header, vals)))
    return out


def load_state():
    if STATE_FILE.exists():
        try:
            return json.load(open(STATE_FILE))
        except Exception:
            pass
    return {}


def save_state(st):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(st, open(STATE_FILE, 'w'), indent=2)


def fetch(source, start, days):
    """One FIRMS SP/NRT request. Returns list of dicts, or None on failure."""
    url = f"{FIRMS_URL}/{NASA_API_KEY}/{source}/{AREA}/{days}/{start}"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=180)
            if r.status_code == 429:
                wait = 30 * (attempt + 1)
                log(f"    rate limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return parse_firms_csv(r.text)
        except Exception as e:
            log(f"    attempt {attempt+1}/3 failed: {str(e)[:100]}")
            time.sleep(10 * (attempt + 1))
    return None


def main():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument('--sensor', default='SNPP,NOAA21',
                    help='Comma-separated: SNPP,NOAA20,NOAA21')
    ap.add_argument('--from', dest='date_from', default='2020-01-01')
    ap.add_argument('--to', dest='date_to',
                    default=(datetime.now() - timedelta(days=11)).strftime('%Y-%m-%d'),
                    help='Default: 11 days ago (the daily NRT job covers the rest)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--sleep', type=float, default=2.0,
                    help='Seconds between requests (be polite to FIRMS)')
    args = ap.parse_args()

    sensors = [s.strip().upper() for s in args.sensor.split(',') if s.strip()]
    for s in sensors:
        if s not in SENSORS:
            ap.error(f"unknown sensor {s}; choose from {list(SENSORS)}")

    state = load_state()

    if args.dry_run:
        for s in sensors:
            start = args.date_from
            done = state.get(s, {}).get('through')
            if done and done > start:
                start = done
            segs = segments_for(s, start, args.date_to)
            if not segs:
                log(f"{s}: nothing to do (no availability in "
                    f"{start}..{args.date_to})")
                continue
            for source, lo, hi in segs:
                n = (datetime.strptime(hi, '%Y-%m-%d')
                     - datetime.strptime(lo, '%Y-%m-%d')).days
                log(f"{s}/{source}: {lo} -> {hi} = {n} days "
                    f"= {(n + CHUNK_DAYS - 1) // CHUNK_DAYS} requests"
                    + (f"  (resuming from {done})" if done else ""))
        log("Dry run only; nothing fetched.")
        return

    from park_assigner import ParkAssigner
    log("Loading ParkAssigner (100km nearest-boundary assignment)...")
    assigner = ParkAssigner()

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    grand_total = 0
    for sensor in sensors:
        start = args.date_from
        done_through = state.get(sensor, {}).get('through')
        if done_through and done_through > start:
            log(f"{sensor}: resuming from {done_through}")
            start = done_through

        segs = segments_for(sensor, start, args.date_to)
        if not segs:
            log(f"{sensor}: no data available in {start}..{args.date_to}, skipping")
            continue

        sensor_total = 0
        aborted = False
        for source, seg_lo, seg_hi in segs:
            if aborted:
                break
            log(f"{sensor}: {source} {seg_lo} -> {seg_hi}")
            cur_dt = datetime.strptime(seg_lo, '%Y-%m-%d')
            end_dt = datetime.strptime(seg_hi, '%Y-%m-%d')

            while cur_dt < end_dt:
                days = min(CHUNK_DAYS, (end_dt - cur_dt).days)
                chunk_start = cur_dt.strftime('%Y-%m-%d')
                fires = fetch(source, chunk_start, days)
                if fires is None:
                    log(f"  {sensor} {chunk_start}: FAILED after retries, "
                        f"stopping this sensor so it can be resumed")
                    aborted = True
                    break

                inserted = 0
                errors = 0
                cur = conn.cursor()
                for f in fires:
                    try:
                        lat = float(f.get('latitude', 0))
                        lon = float(f.get('longitude', 0))
                        if lat == 0.0 or lon == 0.0:
                            continue
                        park_id, dist_km = assigner.assign(lon, lat)
                        if not park_id:
                            continue  # >100km from any park: not useful here
                        cur.execute('''
                            INSERT OR IGNORE INTO fire_detections
                            (latitude, longitude, brightness, scan, track,
                             acq_date, acq_time, satellite, instrument,
                             confidence, frp, daynight, in_protected_area,
                             protected_area_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ''', (lat, lon, float(f.get('bright_ti4') or 0),
                              float(f.get('scan') or 0),
                              float(f.get('track') or 0),
                              f.get('acq_date', ''), f.get('acq_time', ''),
                              f.get('satellite') or 'unknown', 'VIIRS',
                              f.get('confidence', ''), float(f.get('frp') or 0),
                              f.get('daynight', ''),
                              1 if dist_km == 0.0 else 0, park_id))
                        inserted += cur.rowcount
                    except Exception:
                        errors += 1
                conn.commit()
                sensor_total += inserted
                log(f"  {sensor} {chunk_start} +{days}d: {len(fires):,} fetched, "
                    f"{inserted:,} new" + (f", {errors} errors" if errors else ""))

                cur_dt += timedelta(days=days)
                state.setdefault(sensor, {})['through'] = cur_dt.strftime('%Y-%m-%d')
                save_state(state)
                time.sleep(args.sleep)

        log(f"{sensor}: {sensor_total:,} rows inserted")
        grand_total += sensor_total

    conn.close()
    log(f"DONE. {grand_total:,} rows total.")
    log("Next: python3 scripts/build_fire_grid_agg.py   (refresh animator aggregates)")
    log("Then re-run the overpass A/B - see this script's docstring.")


if __name__ == '__main__':
    main()
