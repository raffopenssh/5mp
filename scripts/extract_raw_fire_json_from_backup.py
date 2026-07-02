#!/usr/bin/env python3
"""
Extract per-park raw fire JSONs from a backup DB's fire_detections table.

Merges with existing data/raw-fire-viirs-*/ files (which hold recent NRT
fires) and dedupes on (lat, lon, date, time). Never removes existing fires.

Usage: python3 scripts/extract_raw_fire_json_from_backup.py --db /tmp/db_backup_20260401.sqlite3
"""
import json, sqlite3, math, argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
KEYSTONES_FILE = BASE_DIR / "data/keystones_with_boundaries.json"
RAW_DIR = BASE_DIR / "data/raw-fire-viirs-20200101-20260222"
BUFFER_DEG = 0.45  # ~50km, same as extract_buffer_fires_from_db.py

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_park_bboxes():
    with open(KEYSTONES_FILE) as f:
        data = json.load(f)
    parks = {}
    for area in (data if isinstance(data, list) else data.get('features', [])):
        park_id = area.get('id')
        geom = area.get('geometry')
        if not park_id or not geom:
            continue
        coords = []
        if geom['type'] == 'Polygon':
            coords = geom['coordinates'][0]
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['coordinates']:
                coords.extend(poly[0])
        if not coords:
            continue
        lons = [c[0] for c in coords]; lats = [c[1] for c in coords]
        parks[park_id] = (min(lons)-BUFFER_DEG, min(lats)-BUFFER_DEG,
                          max(lons)+BUFFER_DEG, max(lats)+BUFFER_DEG)
    return parks

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--max-date', default='2026-01-01',
                    help='Only take backup fires BEFORE this date (current raw files cover after)')
    args = ap.parse_args()

    parks = load_park_bboxes()
    log(f"{len(parks)} parks")

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    cur = con.cursor()
    n = cur.execute("SELECT count(*) FROM fire_detections WHERE acq_date < ?",
                    (args.max_date,)).fetchone()[0]
    log(f"Backup DB fires before {args.max_date}: {n:,}")

    total_added = 0
    for i, (park_id, bbox) in enumerate(sorted(parks.items())):
        minx, miny, maxx, maxy = bbox
        rows = cur.execute("""
            SELECT latitude, longitude, acq_date, acq_time, frp, confidence
            FROM fire_detections
            WHERE acq_date < ?
              AND longitude BETWEEN ? AND ? AND latitude BETWEEN ? AND ?
              AND longitude != 0 AND latitude != 0
        """, (args.max_date, minx, maxx, miny, maxy)).fetchall()
        if not rows:
            continue

        raw_file = RAW_DIR / f"{park_id}.json"
        if raw_file.exists():
            with open(raw_file) as f:
                data = json.load(f)
            fires = data.get('fires', [])
        else:
            data = {'park_id': park_id, 'fires': []}
            fires = data['fires'] if 'fires' in data else []
            data['fires'] = fires

        existing = {(f['latitude'], f['longitude'], f['acq_date'], str(f.get('acq_time',''))) for f in fires}
        added = 0
        for lat, lon, d, t, frp, conf in rows:
            key = (lat, lon, d, str(t or ''))
            if key in existing:
                continue
            existing.add(key)
            fires.append({'latitude': lat, 'longitude': lon, 'acq_date': d,
                          'acq_time': str(t or ''), 'frp': frp or 0,
                          'confidence': conf or 'n'})
            added += 1
        if added:
            data['fires'] = fires
            tmp = raw_file.with_suffix('.json.tmp')
            with open(tmp, 'w') as f:
                json.dump(data, f)
            tmp.rename(raw_file)
        total_added += added
        log(f"[{i+1}/{len(parks)}] {park_id}: +{added:,} (total in file: {len(fires):,})")

    log(f"Done. Added {total_added:,} historical fires")

if __name__ == '__main__':
    main()
