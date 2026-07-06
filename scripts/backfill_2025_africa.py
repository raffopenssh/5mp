#!/usr/bin/env python3
"""One-off: backfill 2025 VIIRS SP fires, Africa-wide, into fire_detections.
Same assignment logic as daily_fire_update.py (nearest park <=100km).
Only rows assigned to a park are inserted. INSERT OR IGNORE = idempotent."""
import sqlite3, sys, time, requests
from datetime import datetime, timedelta
from pathlib import Path
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / 'scripts'))
from park_assigner import ParkAssigner

KEY = "REDACTED_FIRMS_KEY"
URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
AREA = "-20,-35,55,40"

def log(m): print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

assigner = ParkAssigner()
conn = sqlite3.connect(BASE_DIR / 'db.sqlite3', timeout=120)
cur = conn.cursor()
cache = {}
d = datetime(2025, 1, 1)
end = datetime(2025, 12, 31)
total = 0
while d <= end:
    span = min(5, (end - d).days + 1)
    url = f"{URL}/{KEY}/VIIRS_NOAA20_SP/{AREA}/{span}/{d.strftime('%Y-%m-%d')}"
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            if r.text[:7] in ('Invalid', 'Error: '): raise RuntimeError(r.text[:80])
            lines = r.text.strip().split('\n')
            break
        except Exception as ex:
            log(f"  {d.date()} try{attempt+1}: {str(ex)[:70]}"); time.sleep(15*(attempt+1))
    else:
        log(f"  GIVING UP window {d.date()}"); d += timedelta(days=span); continue
    hdr = lines[0].split(','); rows = []
    for l in lines[1:]:
        f = dict(zip(hdr, l.split(',')))
        try:
            lat = float(f['latitude']); lon = float(f['longitude'])
            if lat == 0.0 or lon == 0.0: continue
            k = (round(lat,3), round(lon,3))
            pa = cache.get(k)
            if pa is None: pa = assigner.assign(lon, lat); cache[k] = pa
            park_id, dist = pa
            if park_id is None: continue
            rows.append((lat, lon, float(f.get('bright_ti4') or 0), float(f.get('scan') or 0),
                         float(f.get('track') or 0), f['acq_date'], f.get('acq_time',''),
                         f.get('satellite','1'), 'VIIRS', f.get('confidence',''),
                         float(f.get('frp') or 0), f.get('daynight',''),
                         1 if dist == 0.0 else 0, park_id))
        except Exception: pass
    cur.executemany('''INSERT OR IGNORE INTO fire_detections
        (latitude, longitude, brightness, scan, track, acq_date, acq_time,
         satellite, instrument, confidence, frp, daynight, in_protected_area, protected_area_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows)
    conn.commit()
    total += len(rows)
    log(f"  {d.date()}+{span}d: {len(lines)-1} fires, {len(rows)} assigned")
    d += timedelta(days=span); time.sleep(1)
log(f"done, {total} assigned rows")
