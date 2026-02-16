#!/usr/bin/env python3
"""
Process historical fire data (2018-2024) from Google Drive ZIP with 50km buffer.

Streams CSV files directly from ZIP without full extraction.
Skips already-processed country/year files.
"""

import os
import sys
import json
import csv
import math
import zipfile
import requests
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from io import TextIOWrapper

BASE_DIR = Path(__file__).parent.parent
KEYSTONES_FILE = BASE_DIR / "data/keystones_with_boundaries.json"
ADDITIONAL_FIRES_DIR = BASE_DIR / "data/fire_additional_buffer"
PROGRESS_FILE = BASE_DIR / "data/fire_additional_buffer/.progress.json"

GDRIVE_FILE_ID = "1w59TvLxsOjTSRQWeQx3XYEdzeSTydUXP"

AFRICAN_COUNTRIES = [
    'Angola', 'Benin', 'Botswana', 'Burkina_Faso', 'Cameroon', 
    'Central_African_Republic', 'Chad', 'Democratic_Republic_of_the_Congo',
    'Republic_of_Congo', 'Cote_d_Ivoire', 'Djibouti', 'Egypt', 'Equatorial_Guinea',
    'Eritrea', 'Ethiopia', 'Gabon', 'Ghana', 'Guinea', 'Guinea-Bissau',
    'Kenya', 'Lesotho', 'Liberia', 'Libya', 'Madagascar', 'Malawi', 'Mali',
    'Mauritania', 'Morocco', 'Mozambique', 'Namibia', 'Niger', 'Nigeria',
    'Rwanda', 'Senegal', 'Sierra_Leone', 'Somalia', 'South_Africa', 
    'South_Sudan', 'Sudan', 'Tanzania', 'The_Gambia', 'Togo', 'Tunisia', 
    'Uganda', 'Zambia', 'Zimbabwe'
]

sys.stdout.reconfigure(line_buffering=True)

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_progress():
    """Load set of processed CSV files."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return set(json.load(f))
    return set()

def save_progress(processed):
    """Save set of processed CSV files."""
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(list(processed), f)

def download_gdrive_file(file_id, dest_path):
    """Download file from Google Drive."""
    log(f"Downloading from Google Drive...")
    
    session = requests.Session()
    url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
    response = session.get(url, stream=True)
    
    total_size = int(response.headers.get('content-length', 0))
    
    with open(dest_path, 'wb') as f:
        downloaded = 0
        for chunk in response.iter_content(chunk_size=8192*1024):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if total_size:
                    pct = (downloaded / total_size) * 100
                    print(f"\r  {downloaded/(1024*1024):.0f}MB ({pct:.1f}%)", end="", flush=True)
    print()
    return dest_path

def load_parks_with_bbox():
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
        buffer_deg = 0.45
        
        parks[park_id] = {
            'bbox_extended': [bbox[0]-buffer_deg, bbox[1]-buffer_deg, bbox[2]+buffer_deg, bbox[3]+buffer_deg],
            'geometry': geom
        }
    return parks

def point_in_polygon(lon, lat, geometry):
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
    
    if geometry['type'] == 'Polygon':
        return point_in_ring(lon, lat, geometry['coordinates'][0])
    elif geometry['type'] == 'MultiPolygon':
        for poly in geometry['coordinates']:
            if point_in_ring(lon, lat, poly[0]):
                return True
    return False

def process_zip_file(zip_path, parks):
    log(f"Processing ZIP: {zip_path}")
    ADDITIONAL_FIRES_DIR.mkdir(parents=True, exist_ok=True)
    
    processed = load_progress()
    log(f"Already processed: {len(processed)} CSV files")
    
    park_fires = {pid: defaultdict(list) for pid in parks}
    
    with zipfile.ZipFile(zip_path, 'r') as zf:
        csv_files = [f for f in zf.namelist() 
                     if f.endswith('.csv') and '__MACOSX' not in f
                     and any(c in f for c in AFRICAN_COUNTRIES)]
        
        # Filter out already processed
        to_process = [f for f in csv_files if f not in processed]
        log(f"Found {len(csv_files)} African CSVs, {len(to_process)} remaining")
        
        for idx, csv_name in enumerate(to_process):
            parts = Path(csv_name).stem.split('_')
            year = None
            for p in parts:
                if p.isdigit() and len(p) == 4:
                    year = int(p)
                    break
            
            if not year or year >= 2025:
                processed.add(csv_name)
                continue
            
            log(f"[{idx+1}/{len(to_process)}] {Path(csv_name).name}...")
            
            row_count = 0
            match_count = 0
            
            try:
                with zf.open(csv_name) as f:
                    reader = csv.DictReader(TextIOWrapper(f, encoding='utf-8', errors='replace'))
                    
                    for row in reader:
                        row_count += 1
                        try:
                            lat = float(row.get('latitude', 0))
                            lon = float(row.get('longitude', 0))
                            if not lat or not lon:
                                continue
                            
                            for park_id, park in parks.items():
                                bbox = park['bbox_extended']
                                if not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
                                    continue
                                if point_in_polygon(lon, lat, park['geometry']):
                                    continue
                                
                                park_fires[park_id][year].append({
                                    'lat': lat, 'lon': lon,
                                    'date': row.get('acq_date', ''),
                                    'time': row.get('acq_time', ''),
                                    'frp': float(row.get('frp', 0) or 0),
                                    'confidence': row.get('confidence', '')
                                })
                                match_count += 1
                        except:
                            continue
                
                log(f"    {row_count:,} rows, {match_count:,} buffer fires")
                processed.add(csv_name)
                save_progress(processed)
                
            except Exception as e:
                log(f"    Error: {e}")
    
    # Save per park/year
    log("\nSaving per park...")
    total = 0
    for park_id, years in park_fires.items():
        for year, fires in years.items():
            if not fires:
                continue
            out_file = ADDITIONAL_FIRES_DIR / f"{park_id}_{year}_buffer.json"
            
            existing = []
            if out_file.exists():
                try:
                    with open(out_file) as f:
                        existing = json.load(f).get('fires', [])
                except:
                    pass
            
            all_fires = existing + fires
            seen = set()
            unique = [f for f in all_fires if (f['lat'], f['lon'], f['date']) not in seen and not seen.add((f['lat'], f['lon'], f['date']))]
            
            with open(out_file, 'w') as f:
                json.dump({'park_id': park_id, 'year': year, 'buffer_km': 50, 'fires': unique, 'count_buffer': len(unique)}, f)
            total += len(unique)
    
    log(f"Total buffer fires saved: {total:,}")
    return total

def main():
    log("=" * 60)
    log("Historical Fire Buffer (2018-2024) - RESUMABLE")
    log("=" * 60)
    
    parks = load_parks_with_bbox()
    log(f"Loaded {len(parks)} parks")
    
    zip_path = Path("/tmp/viirs_fire_2018_2024.zip")
    
    if not zip_path.exists() or zip_path.stat().st_size < 2000000000:
        download_gdrive_file(GDRIVE_FILE_ID, zip_path)
    else:
        log(f"Using cached ZIP: {zip_path.stat().st_size/(1024*1024):.0f}MB")
    
    process_zip_file(zip_path, parks)
    
    log("Cleaning up ZIP...")
    zip_path.unlink(missing_ok=True)
    log("Done!")

if __name__ == '__main__':
    main()
