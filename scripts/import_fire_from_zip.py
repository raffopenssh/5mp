#!/usr/bin/env python3
"""
Import fire data from Google Drive zip archive directly (streaming).
Only extracts African country files and filters to park buffers.
"""

import json
import csv
import io
import zipfile
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import math

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_FILE = BASE_DIR / "data/keystones_with_boundaries.json"

# African countries in our parks
AFRICAN_COUNTRIES = {
    'Algeria', 'Angola', 'Benin', 'Botswana', 'Cameroon', 
    'Central_African_Republic', 'Chad', 'Cote_d_Ivoire', 
    'Democratic_Republic_of_the_Congo', 'Equatorial_Guinea',
    'Ethiopia', 'Gabon', 'Ghana', 'Kenya', 'Lesotho', 'Liberia',
    'Malawi', 'Mali', 'Mozambique', 'Namibia', 'Niger', 'Nigeria',
    'Republic_of_Congo', 'Rwanda', 'Senegal', 'South_Africa',
    'South_Sudan', 'Sudan', 'Tanzania', 'Togo', 'Uganda', 
    'Zambia', 'Zimbabwe'
}

MIN_DATE = '2020-01-01'
BUFFER_KM = 30

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(min(1, a)))

def load_park_bounds():
    """Load park bounding boxes with buffer."""
    with open(KEYSTONES_FILE) as f:
        keystones = json.load(f)
    
    parks = {}
    for k in keystones:
        if k.get('geometry'):
            # Get bounds from geometry
            coords = []
            geom = k['geometry']
            if geom['type'] == 'Polygon':
                coords = geom['coordinates'][0]
            elif geom['type'] == 'MultiPolygon':
                for poly in geom['coordinates']:
                    coords.extend(poly[0])
            
            if coords:
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                buffer_deg = BUFFER_KM / 111.0
                parks[k['id']] = {
                    'min_lat': min(lats) - buffer_deg,
                    'max_lat': max(lats) + buffer_deg,
                    'min_lon': min(lons) - buffer_deg,
                    'max_lon': max(lons) + buffer_deg,
                    'center_lat': sum(lats) / len(lats),
                    'center_lon': sum(lons) / len(lons)
                }
    return parks

def point_in_park_buffer(lat, lon, parks):
    """Find which park(s) a point falls within (buffer zone)."""
    matches = []
    for park_id, bounds in parks.items():
        if (bounds['min_lat'] <= lat <= bounds['max_lat'] and
            bounds['min_lon'] <= lon <= bounds['max_lon']):
            matches.append(park_id)
    return matches

def process_zip(zip_path, parks, conn):
    """Process fire CSV files directly from zip."""
    log(f"Opening {zip_path}...")
    
    total_fires = 0
    fires_by_park = defaultdict(list)
    
    with zipfile.ZipFile(zip_path, 'r') as zf:
        # List all CSV files
        csv_files = [f for f in zf.namelist() 
                     if f.endswith('.csv') and 'viirs-jpss' in f
                     and any(c in f for c in AFRICAN_COUNTRIES)]
        
        log(f"Found {len(csv_files)} African country CSV files")
        
        for i, csv_file in enumerate(csv_files):
            # Extract year from path
            parts = csv_file.split('/')
            year = None
            for p in parts:
                if p.isdigit() and len(p) == 4:
                    year = int(p)
                    break
            
            if year and year < 2020:
                continue  # Skip pre-2020 data
            
            country = csv_file.split('_')[-1].replace('.csv', '')
            log(f"  [{i+1}/{len(csv_files)}] Processing {csv_file}...")
            
            try:
                with zf.open(csv_file) as f:
                    # Read CSV
                    text = io.TextIOWrapper(f, encoding='utf-8')
                    reader = csv.DictReader(text)
                    
                    file_fires = 0
                    for row in reader:
                        try:
                            lat = float(row.get('latitude', 0))
                            lon = float(row.get('longitude', 0))
                            date = row.get('acq_date', '')
                            
                            if date < MIN_DATE:
                                continue
                            
                            # Check if in any park buffer
                            park_matches = point_in_park_buffer(lat, lon, parks)
                            if not park_matches:
                                continue
                            
                            fire = {
                                'lat': lat,
                                'lon': lon,
                                'date': date,
                                'time': row.get('acq_time', '0000'),
                                'brightness': float(row.get('bright_ti4', 0) or 0),
                                'frp': float(row.get('frp', 0) or 0),
                                'confidence': row.get('confidence', '')
                            }
                            
                            for park_id in park_matches:
                                fires_by_park[park_id].append(fire)
                            
                            file_fires += 1
                            total_fires += 1
                            
                        except (ValueError, KeyError):
                            continue
                    
                    if file_fires > 0:
                        log(f"    -> {file_fires} fires in park buffers")
                        
            except Exception as e:
                log(f"    Error: {e}")
                continue
    
    log(f"\nTotal: {total_fires} fires across {len(fires_by_park)} parks")
    
    # Save to JSON files for Step 1
    output_dir = BASE_DIR / 'data' / 'fire_archive_extracted'
    output_dir.mkdir(exist_ok=True)
    
    for park_id, fires in fires_by_park.items():
        out_file = output_dir / f'{park_id}.json'
        with open(out_file, 'w') as f:
            json.dump(fires, f)
        log(f"  {park_id}: {len(fires)} fires")
    
    return fires_by_park

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('zip_path', help='Path to fire_archive.zip')
    args = parser.parse_args()
    
    log("=" * 70)
    log("IMPORT FIRE DATA FROM ZIP ARCHIVE")
    log("=" * 70)
    
    parks = load_park_bounds()
    log(f"Loaded {len(parks)} park boundaries")
    
    conn = sqlite3.connect(str(DB_PATH))
    
    fires_by_park = process_zip(args.zip_path, parks, conn)
    
    conn.close()
    
    log("\n" + "=" * 70)
    log("COMPLETE")
    log("=" * 70)

if __name__ == '__main__':
    main()
