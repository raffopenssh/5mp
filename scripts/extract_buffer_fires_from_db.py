#!/usr/bin/env python3
"""
Extract buffer fires (50km outside parks) from existing fire_detections database.

Much more efficient than re-downloading - data is already in the DB.
"""

import json
import sqlite3
import math
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_FILE = BASE_DIR / "data/keystones_with_boundaries.json"
ADDITIONAL_FIRES_DIR = BASE_DIR / "data/fire_additional_buffer"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_parks():
    """Load parks with bbox extended by 50km."""
    with open(KEYSTONES_FILE) as f:
        data = json.load(f)
    
    parks = {}
    items = data if isinstance(data, list) else data.get('features', [])
    
    for area in items:
        park_id = area.get('id')
        if not park_id:
            continue
        
        geom = area.get('geometry')
        if not geom:
            continue
        
        coords = []
        if geom['type'] == 'Polygon':
            coords = geom['coordinates'][0]
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['coordinates']:
                coords.extend(poly[0])
        
        if not coords:
            continue
        
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        bbox = [min(lons), min(lats), max(lons), max(lats)]
        
        buffer_deg = 0.45  # ~50km
        parks[park_id] = {
            'name': area.get('name', park_id),
            'bbox': bbox,
            'bbox_extended': [
                bbox[0] - buffer_deg,
                bbox[1] - buffer_deg,
                bbox[2] + buffer_deg,
                bbox[3] + buffer_deg
            ],
            'geometry': geom
        }
    
    return parks

def point_in_polygon(lon, lat, geometry):
    """Check if point is inside polygon."""
    def point_in_ring(lon, lat, ring):
        n = len(ring)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside
    
    gtype = geometry['type']
    if gtype == 'Polygon':
        return point_in_ring(lon, lat, geometry['coordinates'][0])
    elif gtype == 'MultiPolygon':
        for poly in geometry['coordinates']:
            if point_in_ring(lon, lat, poly[0]):
                return True
    return False

def main():
    log("=" * 60)
    log("Extract Buffer Fires from Database (2018-2024)")
    log("=" * 60)
    
    parks = load_parks()
    log(f"Loaded {len(parks)} parks")
    
    ADDITIONAL_FIRES_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Check what we have
    row = conn.execute("SELECT MIN(acq_date), MAX(acq_date), COUNT(*) FROM fire_detections WHERE acq_date < '2025-01-01'").fetchone()
    log(f"Database has {row[2]:,} fires from {row[0]} to {row[1]}")
    
    total_buffer = 0
    
    for i, (park_id, park) in enumerate(sorted(parks.items())):
        log(f"[{i+1}/{len(parks)}] {park_id}...")
        
        bbox_ext = park['bbox_extended']
        bbox = park['bbox']
        geom = park['geometry']
        
        # Query fires in extended bbox but NOT in original bbox (rough filter)
        # This gets fires that are definitely outside the park's bbox
        cursor = conn.execute("""
            SELECT latitude, longitude, acq_date, acq_time, frp, confidence
            FROM fire_detections
            WHERE latitude BETWEEN ? AND ?
              AND longitude BETWEEN ? AND ?
              AND acq_date < '2025-01-01'
              AND NOT (latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?)
        """, (
            bbox_ext[1], bbox_ext[3], bbox_ext[0], bbox_ext[2],  # extended bbox
            bbox[1], bbox[3], bbox[0], bbox[2]  # exclude original bbox
        ))
        
        # Group by year
        fires_by_year = defaultdict(list)
        
        for row in cursor:
            lat, lon = row['latitude'], row['longitude']
            
            # Double-check it's not inside the polygon
            if point_in_polygon(lon, lat, geom):
                continue
            
            year = int(row['acq_date'][:4])
            fires_by_year[year].append({
                'lat': lat,
                'lon': lon,
                'date': row['acq_date'],
                'time': row['acq_time'] or '',
                'frp': row['frp'] or 0,
                'confidence': row['confidence'] or ''
            })
        
        # Save per year
        park_total = 0
        for year, fires in fires_by_year.items():
            if not fires:
                continue
            
            out_file = ADDITIONAL_FIRES_DIR / f"{park_id}_{year}_buffer.json"
            
            # Merge with existing (from 2025-2026 backfill)
            existing = []
            if out_file.exists():
                try:
                    with open(out_file) as f:
                        data = json.load(f)
                        existing = data.get('fires', [])
                except:
                    pass
            
            # Dedupe
            all_fires = existing + fires
            seen = set()
            unique = []
            for f in all_fires:
                key = (f['lat'], f['lon'], f['date'])
                if key not in seen:
                    seen.add(key)
                    unique.append(f)
            
            with open(out_file, 'w') as f:
                json.dump({
                    'park_id': park_id,
                    'year': year,
                    'buffer_km': 50,
                    'fires': unique,
                    'count_buffer': len(unique)
                }, f)
            
            park_total += len(unique)
        
        if park_total > 0:
            log(f"    {park_total:,} buffer fires")
            total_buffer += park_total
    
    conn.close()
    
    log("\n" + "=" * 60)
    log(f"Complete! Total buffer fires: {total_buffer:,}")
    log("=" * 60)

if __name__ == '__main__':
    main()
