#!/usr/bin/env python3
"""
Bulk-import FIRMS country archive CSVs (data/fire/*.csv) into fire_detections.

- Vectorized bbox prefilter (park bbox + 50km) with pandas/numpy
- Survivors assigned to nearest park (<=100km) via ParkAssigner (same as
  daily_fire_update.py) -> protected_area_id / in_protected_area
- INSERT OR IGNORE on (lat,lon,date,time,satellite) unique key: idempotent,
  never clobbers NRT rows.

Usage: python3 scripts/import_fire_country_csvs.py [--glob 'viirs-jpss1_2024_*']
"""
import argparse, glob, json, sqlite3, sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / 'scripts'))
from park_assigner import ParkAssigner

DB_PATH = BASE_DIR / 'db.sqlite3'
FIRE_DIR = BASE_DIR / 'data' / 'fire'
KEYSTONES = BASE_DIR / 'data' / 'keystones_with_boundaries.json'
PREFILTER_DEG = 50 / 111.0  # bbox buffer for cheap prefilter

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_bboxes():
    with open(KEYSTONES) as f:
        parks = json.load(f)
    boxes = []
    for p in parks:
        g = p.get('geometry')
        if not g: continue
        coords = []
        if g['type'] == 'Polygon':
            for ring in g['coordinates']: coords.extend(ring)
        else:
            for poly in g['coordinates']:
                for ring in poly: coords.extend(ring)
        lons = [c[0] for c in coords]; lats = [c[1] for c in coords]
        boxes.append((min(lons)-PREFILTER_DEG, min(lats)-PREFILTER_DEG,
                      max(lons)+PREFILTER_DEG, max(lats)+PREFILTER_DEG))
    return boxes

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--glob', default='viirs-jpss1_*.csv')
    args = ap.parse_args()

    files = sorted(glob.glob(str(FIRE_DIR / args.glob)))
    log(f"{len(files)} CSV files")
    boxes = load_bboxes()
    assigner = ParkAssigner()
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()
    assign_cache = {}
    total_new = 0

    for fi, path in enumerate(files):
        name = Path(path).name
        try:
            df = pd.read_csv(path, dtype={'acq_time': str})
        except Exception as ex:
            log(f"  SKIP {name}: {ex}"); continue
        if df.empty: continue
        lat = df['latitude'].values; lon = df['longitude'].values
        mask = np.zeros(len(df), dtype=bool)
        for (w, s, e, n) in boxes:
            mask |= (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
        sub = df[mask]
        ins = 0
        rows = []
        for r in sub.itertuples(index=False):
            key = (round(r.latitude, 3), round(r.longitude, 3))
            pa = assign_cache.get(key)
            if pa is None:
                pa = assigner.assign(r.longitude, r.latitude)
                assign_cache[key] = pa
            park_id, dist = pa
            if park_id is None:
                continue
            rows.append((r.latitude, r.longitude, float(getattr(r, 'bright_ti4', 0) or 0),
                         float(getattr(r, 'scan', 0) or 0), float(getattr(r, 'track', 0) or 0),
                         r.acq_date, str(getattr(r, 'acq_time', '') or ''),
                         str(getattr(r, 'satellite', '1')), 'VIIRS',
                         str(getattr(r, 'confidence', '') or ''),
                         float(getattr(r, 'frp', 0) or 0), str(getattr(r, 'daynight', '') or ''),
                         1 if dist == 0.0 else 0, park_id))
        if rows:
            cur.executemany('''INSERT OR IGNORE INTO fire_detections
                (latitude, longitude, brightness, scan, track, acq_date, acq_time,
                 satellite, instrument, confidence, frp, daynight,
                 in_protected_area, protected_area_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows)
            ins = conn.total_changes
            conn.commit()
        log(f"  [{fi+1}/{len(files)}] {name}: {len(df)} rows, {len(sub)} in bbox, {len(rows)} assigned")
        total_new += len(rows)

    log(f"done. candidate rows: {total_new}")
    conn.close()

if __name__ == '__main__':
    main()
