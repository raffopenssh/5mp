#!/usr/bin/env python3
"""
Backfill historical VIIRS fires (FIRMS SP archive) into fire_detections for
ONE park's bbox(+buffer). Needed because fire_detections only holds 2026+ NRT
data, but GFW-era deforestation events (2024+) need fires_same_year context.

Usage:
  python3 scripts/backfill_fire_history_park.py --park CAF_Chinko --start 2024-01-01 --end 2025-12-31

Idempotent: INSERT OR IGNORE on the (lat,lon,date,time,satellite) unique key.
Fires are assigned to nearest park (<=100km) same as daily_fire_update.py.
"""
import argparse, json, sqlite3, sys, time
from datetime import datetime, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / 'scripts'))
from park_assigner import ParkAssigner
sys.path.insert(0, str(BASE_DIR / 'analysis'))

DB_PATH = BASE_DIR / 'db.sqlite3'
KEYSTONES = BASE_DIR / 'data' / 'keystones_with_boundaries.json'
NASA_API_KEY = "REDACTED_FIRMS_KEY"
SP_SOURCE = "VIIRS_NOAA20_SP"
URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
BUFFER_DEG = 0.1  # ~10km, matches GFW cell buffer

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def park_bbox(park_id):
    with open(KEYSTONES) as f:
        parks = json.load(f)
    for p in parks:
        if p['id'] != park_id:
            continue
        g = p['geometry']
        coords = []
        if g['type'] == 'Polygon':
            for ring in g['coordinates']: coords.extend(ring)
        else:
            for poly in g['coordinates']:
                for ring in poly: coords.extend(ring)
        lons = [c[0] for c in coords]; lats = [c[1] for c in coords]
        return (min(lons)-BUFFER_DEG, min(lats)-BUFFER_DEG,
                max(lons)+BUFFER_DEG, max(lats)+BUFFER_DEG)
    raise SystemExit(f"park {park_id} not found")

def parse_csv(text):
    lines = text.strip().split('\n')
    if len(lines) < 2:
        return []
    header = lines[0].split(',')
    return [dict(zip(header, l.split(','))) for l in lines[1:]]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--park', required=True)
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    args = ap.parse_args()

    w, s, e, n = park_bbox(args.park)
    area = f"{w:.3f},{s:.3f},{e:.3f},{n:.3f}"
    log(f"{args.park} bbox: {area}")

    assigner = ParkAssigner()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    start = datetime.strptime(args.start, '%Y-%m-%d')
    end = datetime.strptime(args.end, '%Y-%m-%d')
    total_ins = total_seen = 0
    d = start
    while d <= end:
        span = min(5, (end - d).days + 1)  # SP API max is 5 days
        url = f"{URL}/{NASA_API_KEY}/{SP_SOURCE}/{area}/{span}/{d.strftime('%Y-%m-%d')}"
        for attempt in range(3):
            try:
                r = requests.get(url, timeout=120)
                r.raise_for_status()
                if r.text.startswith('Invalid') or r.text.startswith('Error'):
                    raise RuntimeError(r.text[:100])
                fires = parse_csv(r.text)
                break
            except Exception as ex:
                log(f"  {d.date()} attempt {attempt+1} failed: {str(ex)[:80]}")
                time.sleep(10 * (attempt + 1))
        else:
            log(f"  SKIPPING window {d.date()} after 3 failures")
            d += timedelta(days=span)
            continue

        ins = 0
        for f in fires:
            try:
                lat = float(f['latitude']); lon = float(f['longitude'])
                if lat == 0.0 or lon == 0.0: continue
                park_id, dist = assigner.assign(lon, lat)
                cur.execute('''INSERT OR IGNORE INTO fire_detections
                    (latitude, longitude, brightness, scan, track, acq_date, acq_time,
                     satellite, instrument, confidence, frp, daynight,
                     in_protected_area, protected_area_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (lat, lon, float(f.get('bright_ti4') or 0), float(f.get('scan') or 0),
                     float(f.get('track') or 0), f['acq_date'], f.get('acq_time',''),
                     f.get('satellite','1'), 'VIIRS', f.get('confidence',''),
                     float(f.get('frp') or 0), f.get('daynight',''),
                     1 if dist == 0.0 else 0, park_id))
                ins += cur.rowcount
            except Exception:
                pass
        conn.commit()
        total_ins += ins; total_seen += len(fires)
        log(f"  {d.date()} +{span}d: {len(fires)} fires, {ins} new")
        d += timedelta(days=span)
        time.sleep(2)  # be nice to FIRMS

    log(f"done: seen {total_seen}, inserted {total_ins}")
    conn.close()

if __name__ == '__main__':
    main()
