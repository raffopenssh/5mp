#!/usr/bin/env python3
"""
One-off backfill: rewrite per-park raw fire JSONs with canonical single-park
assignment (ParkAssigner: nearest park boundary within 100km).

Fixes the overlap artifacts (rectangular bbox seams + duplicated trajectories,
e.g. Lomami/Upemba/Kundelungu region): the old bbox-buffer extraction put the
same fire into EVERY overlapping park's raw JSON.

Two data sources, one rule (one fire -> one park):
1. Live fire_detections (covers >= DB_MIN_DATE): re-assigned with ParkAssigner.
2. Existing raw JSON fires OLDER than DB_MIN_DATE (not in the live DB anymore):
   kept only if ParkAssigner assigns them to this file's park (dedup +
   100km-buffer trim applied retroactively).

Dedup key: (lat, lon, acq_date, normalized acq_time). acq_time is normalized
by stripping leading zeros ('0002' == '2').

Usage: python3 scripts/backfill_raw_fire_json_100km.py [--dry-run]
"""
import json, sqlite3, argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import sys
sys.path.insert(0, str(Path(__file__).parent))
from park_assigner import ParkAssigner

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
RAW_DIR = BASE_DIR / "data/raw-fire-viirs-20200101-20260222"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def norm_time(t):
    s = str(t or '').strip()
    if s.isdigit():
        return str(int(s))
    return s

def key_of(f):
    return (round(float(f['latitude']), 5), round(float(f['longitude']), 5),
            f['acq_date'], norm_time(f.get('acq_time')))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    log("Loading ParkAssigner...")
    pa = ParkAssigner()
    log(f"  {len(pa.park_ids)} parks")

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = con.cursor()
    db_min = cur.execute("SELECT MIN(acq_date) FROM fire_detections").fetchone()[0]
    n = cur.execute("SELECT COUNT(*) FROM fire_detections").fetchone()[0]
    log(f"fire_detections: {n:,} rows, DB coverage starts {db_min}")

    # --- Pass 1: assign all DB fires -------------------------------------
    log("Assigning DB fires (streaming)...")
    by_park = defaultdict(list)
    done = 0
    for lat, lon, d, t, frp, conf, sat in cur.execute("""
            SELECT latitude, longitude, acq_date, acq_time, frp, confidence, satellite
            FROM fire_detections
            WHERE latitude != 0 AND longitude != 0"""):
        park_id, _ = pa.assign(lon, lat)
        if park_id:
            by_park[park_id].append({
                'latitude': lat, 'longitude': lon, 'acq_date': d,
                'acq_time': str(t or ''), 'frp': frp or 0,
                'confidence': conf or 'n', 'satellite': sat or 'N20'})
        done += 1
        if done % 250000 == 0:
            log(f"  {done:,}/{n:,} assigned...")
    con.close()
    assigned_total = sum(len(v) for v in by_park.values())
    log(f"  {assigned_total:,}/{n:,} fires within 100km of a park ({len(by_park)} parks)")

    # --- Pass 2: rewrite each park file -----------------------------------
    all_parks = sorted(set(list(by_park.keys()) +
                           [f.stem for f in RAW_DIR.glob('*.json')]))
    total_before = total_after = kept_old_total = dropped_old_total = 0

    for i, park_id in enumerate(all_parks):
        raw_file = RAW_DIR / f"{park_id}.json"
        old_fires = []
        if raw_file.exists():
            with open(raw_file) as f:
                old_fires = json.load(f).get('fires', [])
        total_before += len(old_fires)

        new_fires = []
        seen = set()

        # Historical fires predating the live DB: keep only if this park is
        # their canonical assignment.
        kept_old = dropped_old = 0
        for f in old_fires:
            if f['acq_date'] >= db_min:
                continue  # covered by DB pass (re-assigned)
            try:
                lat, lon = float(f['latitude']), float(f['longitude'])
            except (TypeError, ValueError):
                continue
            pid, _ = pa.assign(lon, lat)
            if pid != park_id:
                dropped_old += 1
                continue
            k = key_of(f)
            if k in seen:
                continue
            seen.add(k)
            new_fires.append(f)
            kept_old += 1

        # DB fires assigned to this park
        for f in by_park.get(park_id, []):
            k = key_of(f)
            if k in seen:
                continue
            seen.add(k)
            new_fires.append(f)

        kept_old_total += kept_old
        dropped_old_total += dropped_old
        total_after += len(new_fires)

        log(f"[{i+1}/{len(all_parks)}] {park_id}: {len(old_fires):,} -> {len(new_fires):,} "
            f"(old kept {kept_old:,}, old dropped {dropped_old:,}, db {len(by_park.get(park_id, [])):,})")

        if not args.dry_run:
            tmp = raw_file.with_suffix('.json.tmp')
            with open(tmp, 'w') as fh:
                json.dump({'park_id': park_id, 'fires': new_fires}, fh)
            tmp.rename(raw_file)

    log(f"DONE. Total fires: {total_before:,} -> {total_after:,} "
        f"(pre-DB kept {kept_old_total:,} / dropped {dropped_old_total:,})"
        + (" [DRY RUN - nothing written]" if args.dry_run else ""))

if __name__ == '__main__':
    main()
