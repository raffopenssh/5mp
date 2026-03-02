#!/usr/bin/env python3
"""Build unified fire dataset - 4 quadrants with SE split into 4."""

import json, csv, zipfile, gc
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
KEYSTONES_FILE = BASE_DIR / "data/keystones_with_boundaries.json"
ARCHIVE_PATH = BASE_DIR / "fire_archive.zip"
DETECTIONS_2025_2026 = BASE_DIR / "data/fire_detections_2025_2026"
NRT_DIR = BASE_DIR / "data/fire_nrt"
PROGRESS_FILE = BASE_DIR / "build_fire_progress.json"

MIN_DATE = "2020-01-01"
BUFFER_DEG = 30 / 111.0

CHUNKS = [
    {'name': 'NW', 'west': -20, 'south': 0, 'east': 25, 'north': 40},
    {'name': 'NE', 'west': 15, 'south': 0, 'east': 55, 'north': 40},
    {'name': 'SW', 'west': -20, 'south': -40, 'east': 25, 'north': 10},
    {'name': 'SE_NW', 'west': 15, 'south': -15, 'east': 35, 'north': 10},
    {'name': 'SE_NE', 'west': 30, 'south': -15, 'east': 55, 'north': 10},
    {'name': 'SE_SW', 'west': 15, 'south': -40, 'east': 35, 'north': -10},
    {'name': 'SE_SE', 'west': 30, 'south': -40, 'east': 55, 'north': -10},
]

def log(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_parks():
    with open(KEYSTONES_FILE) as f: data = json.load(f)
    parks = {}
    for area in data:
        park_id, geom = area.get('id'), area.get('geometry')
        if not park_id or not geom: continue
        coords = []
        if geom['type'] == 'Polygon': coords = geom['coordinates'][0]
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['coordinates']: coords.extend(poly[0])
        if not coords: continue
        lons, lats = [c[0] for c in coords], [c[1] for c in coords]
        parks[park_id] = {'west': min(lons)-BUFFER_DEG, 'east': max(lons)+BUFFER_DEG,
                          'south': min(lats)-BUFFER_DEG, 'north': max(lats)+BUFFER_DEG}
    return parks

def bbox_overlap(b1, b2):
    return not (b1['east'] < b2['west'] or b1['west'] > b2['east'] or
                b1['north'] < b2['south'] or b1['south'] > b2['north'])

def point_in_bbox(lon, lat, b): return b['west'] <= lon <= b['east'] and b['south'] <= lat <= b['north']
def fire_key(f): return (f['latitude'], f['longitude'], f['acq_date'], f.get('acq_time', ''))

def load_park_fires(fp):
    if not fp.exists(): return {}
    try:
        with open(fp) as f: return {fire_key(x): x for x in json.load(f).get('fires', [])}
    except: return {}

def save_park_fires(fp, fd):
    with open(fp, 'w') as f: json.dump({'fires': list(fd.values())}, f)

def load_progress():
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE) as f: return json.load(f)
        except: pass
    return {'chunks_done': [], 'detections_done': False, 'nrt_done': False}

def save_progress(p):
    with open(PROGRESS_FILE, 'w') as f: json.dump(p, f)

def main():
    log("Loading parks...")
    parks = load_parks()
    log(f"Loaded {len(parks)} parks")
    
    today = datetime.now().strftime('%Y%m%d')
    OUTPUT_DIR = BASE_DIR / f"data/raw-fire-viirs-20200101-{today}"
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    progress = load_progress()
    chunks_done = set(progress.get('chunks_done', []))
    
    csv_files = []
    if ARCHIVE_PATH.exists():
        with zipfile.ZipFile(ARCHIVE_PATH, 'r') as zf:
            csv_files = [f for f in zf.namelist() if f.endswith('.csv') and 'MACOSX' not in f]
    log(f"Archive: {len(csv_files)} CSVs, {len(CHUNKS)} chunks")
    
    for chunk in CHUNKS:
        if chunk['name'] in chunks_done:
            log(f"Skip {chunk['name']} (done)")
            continue
        
        cb = {'west': chunk['west'], 'east': chunk['east'], 'south': chunk['south'], 'north': chunk['north']}
        chunk_parks = [p for p, b in parks.items() if bbox_overlap(cb, b)]
        if not chunk_parks: chunks_done.add(chunk['name']); continue
        
        log(f"Chunk {chunk['name']}: {len(chunk_parks)} parks")
        park_fires = {p: load_park_fires(OUTPUT_DIR / f"{p}.json") for p in chunk_parks}
        added = 0
        
        with zipfile.ZipFile(ARCHIVE_PATH, 'r') as zf:
            for i, cp in enumerate(csv_files):
                try:
                    with zf.open(cp) as f:
                        for row in csv.DictReader(f.read().decode('utf-8', errors='ignore').splitlines()):
                            try:
                                lat, lon = float(row.get('latitude',0)), float(row.get('longitude',0))
                                if not (cb['west']<=lon<=cb['east'] and cb['south']<=lat<=cb['north']): continue
                                ad = row.get('acq_date','')
                                if ad < MIN_DATE: continue
                                fire = {'latitude':lat,'longitude':lon,'acq_date':ad,'acq_time':row.get('acq_time',''),
                                        'frp':float(row.get('frp',0)or 0),'confidence':row.get('confidence',''),'satellite':row.get('satellite','N')}
                                for p in chunk_parks:
                                    if point_in_bbox(lon, lat, parks[p]):
                                        k = fire_key(fire)
                                        if k not in park_fires[p]: park_fires[p][k] = fire; added += 1
                            except: continue
                except: continue
                if (i+1) % 100 == 0: log(f"  {i+1}/{len(csv_files)}, {added:,} fires")
        
        for p in chunk_parks:
            if park_fires[p]: save_park_fires(OUTPUT_DIR / f"{p}.json", park_fires[p])
        log(f"  {chunk['name']} done: {added:,}")
        chunks_done.add(chunk['name']); progress['chunks_done'] = list(chunks_done); save_progress(progress)
        del park_fires; gc.collect()
    
    if DETECTIONS_2025_2026.exists() and not progress.get('detections_done'):
        log("Processing 2025-2026...")
        for f in DETECTIONS_2025_2026.glob('*.json'):
            pid = f.stem
            if pid not in parks: continue
            ex = load_park_fires(OUTPUT_DIR / f"{pid}.json")
            try:
                for r in json.load(open(f)):
                    fire = {'latitude':r.get('lat'),'longitude':r.get('lng'),'acq_date':r.get('date',''),
                            'acq_time':r.get('time',''),'frp':float(r.get('frp',0)or 0),'confidence':r.get('confidence',''),'satellite':r.get('satellite','N')}
                    if fire['acq_date']>=MIN_DATE: k=fire_key(fire); ex[k]=ex.get(k,fire)
                save_park_fires(OUTPUT_DIR / f"{pid}.json", ex)
            except: continue
        progress['detections_done'] = True; save_progress(progress)
    
    if NRT_DIR.exists() and not progress.get('nrt_done'):
        log("Processing NRT...")
        for f in NRT_DIR.glob('*.json'):
            pid = f.stem.replace('_nrt','')
            if pid not in parks: continue
            ex = load_park_fires(OUTPUT_DIR / f"{pid}.json")
            try:
                data = json.load(open(f)); fires = data.get('fires',data) if isinstance(data,dict) else data
                for r in fires:
                    fire = {'latitude':float(r.get('latitude',r.get('lat',0))),'longitude':float(r.get('longitude',r.get('lng',0))),
                            'acq_date':r.get('acq_date',r.get('date','')),'acq_time':r.get('acq_time',r.get('time','')),
                            'frp':float(r.get('frp',0)or 0),'confidence':r.get('confidence',''),'satellite':r.get('satellite','N')}
                    if fire['acq_date']>=MIN_DATE: k=fire_key(fire); ex[k]=ex.get(k,fire)
                save_park_fires(OUTPUT_DIR / f"{pid}.json", ex)
            except: continue
        progress['nrt_done'] = True; save_progress(progress)
    
    total = sum(len(load_park_fires(f)) for f in OUTPUT_DIR.glob('*.json'))
    log(f"Done! {total:,} fires, {len(list(OUTPUT_DIR.glob('*.json')))} parks")

if __name__ == '__main__': main()
