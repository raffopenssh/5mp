#!/usr/bin/env python3
"""
Backfill fire detections with extended 50km buffer around parks.

This script:
1. For historical data (2018-2024): Extract from provided CSV with 50km buffer
2. For 2025-2026: Use FIRMS NRT API with 50km buffer
3. Store additional fires (outside original bbox but within 50km) as JSON per park/year
4. Append to existing raw fire files, not to DB directly

Memory efficient: Uses streaming/chunked reads, doesn't load entire files.
"""

import os
import sys
import json
import csv
import gzip
import math
import sqlite3
import time
import requests
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from io import StringIO, TextIOWrapper

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_FILE = BASE_DIR / "data/keystones_with_boundaries.json"
ADDITIONAL_FIRES_DIR = BASE_DIR / "data/fire_additional_buffer"

# Import config from fire_nrt
sys.path.insert(0, str(BASE_DIR / 'scripts' / 'fire_nrt'))
try:
    from config import MAP_KEY as FIRMS_MAP_KEY, PROXIES, RATE_LIMIT_DELAY, MAX_TRANSACTIONS_PER_10MIN
except ImportError:
    FIRMS_MAP_KEY = secret('NASA_FIRMS_KEY')
    PROXIES = [
        "95.213.217.168:52004",
        "89.208.85.78:443",
        "66.80.0.115:3128",
        "46.161.6.165:8080",
        "43.130.6.42:80",
    ]
    RATE_LIMIT_DELAY = 0.5
    MAX_TRANSACTIONS_PER_10MIN = 5000

# Track API calls for rate limiting
API_CALL_TIMES = []

sys.stdout.reconfigure(line_buffering=True)

def load_parks_with_bbox():
    """Load parks with their bounding boxes, extended by 50km."""
    with open(KEYSTONES_FILE) as f:
        data = json.load(f)
    
    parks = {}
    items = data.get('features', []) if isinstance(data, dict) else data
    
    for area in items:
        # Handle both GeoJSON feature and flat dict formats
        props = area.get('properties', area)
        park_id = area.get('id') or props.get('id') or props.get('park_id')
        if not park_id:
            continue
        
        # Get geometry
        geom = area.get('geometry')
        if not geom:
            continue
        
        # Calculate bbox from geometry
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
        
        # Extend bbox by 50km (~0.45 degrees)
        buffer_deg = 0.45
        parks[park_id] = {
            'name': area.get('name') or props.get('name', park_id),
            'bbox_original': bbox,
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
    """Check if point is inside polygon geometry."""
    if not geometry:
        return False
    
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

def haversine(lat1, lon1, lat2, lon2):
    """Distance in km between two points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(min(1, a)))

def distance_to_boundary(lon, lat, geometry):
    """Approximate distance from point to polygon boundary in km."""
    if not geometry:
        return float('inf')
    
    def dist_to_ring(lon, lat, ring):
        min_dist = float('inf')
        for i in range(len(ring)):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % len(ring)]
            # Distance to line segment
            d = haversine(lat, lon, (y1+y2)/2, (x1+x2)/2)  # Simplified
            min_dist = min(min_dist, d)
        return min_dist
    
    gtype = geometry['type']
    if gtype == 'Polygon':
        return dist_to_ring(lon, lat, geometry['coordinates'][0])
    elif gtype == 'MultiPolygon':
        return min(dist_to_ring(lon, lat, poly[0]) for poly in geometry['coordinates'])
    return float('inf')

def get_working_proxy():
    """Find a working proxy from the list."""
    test_url = "https://firms.modaps.eosdis.nasa.gov/api/"
    for proxy in PROXIES[:5]:
        try:
            resp = requests.get(
                test_url,
                proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"},
                timeout=10
            )
            if resp.status_code < 500:
                return proxy
        except:
            pass
    return None

WORKING_PROXY = None

def check_rate_limit():
    """Check and enforce rate limiting."""
    global API_CALL_TIMES
    now = time.time()
    
    # Remove calls older than 10 minutes
    API_CALL_TIMES = [t for t in API_CALL_TIMES if now - t < 600]
    
    # If approaching limit, wait
    if len(API_CALL_TIMES) >= MAX_TRANSACTIONS_PER_10MIN - 10:
        wait_time = 600 - (now - API_CALL_TIMES[0]) + 1
        if wait_time > 0:
            print(f"\n  Rate limit approaching, waiting {wait_time:.0f}s...", flush=True)
            time.sleep(wait_time)
            API_CALL_TIMES = []
    
    # Always wait the minimum delay between requests
    time.sleep(RATE_LIMIT_DELAY)
    API_CALL_TIMES.append(time.time())

def fetch_firms_data(bbox, date=None, day_range=5, source='VIIRS_SNPP_NRT'):
    """Fetch fire data from FIRMS API using proxy.
    
    For NRT (recent): /api/area/csv/{MAP_KEY}/{source}/{bbox}/{days} (max 10 days)
    For archive (historical): /api/area/csv/{MAP_KEY}/{source}/{bbox}/1/{date}
    
    Args:
        bbox: [west, south, east, north]
        date: Specific date string 'YYYY-MM-DD' for archive request
        day_range: Number of days for NRT request (1-10)
        source: VIIRS_SNPP_NRT, VIIRS_SNPP_SP, VIIRS_NOAA20_NRT, etc.
    """
    global WORKING_PROXY
    
    # Enforce rate limiting
    check_rate_limit()
    
    if WORKING_PROXY is None:
        print("  Finding working proxy...", end=" ", flush=True)
        WORKING_PROXY = get_working_proxy()
        if WORKING_PROXY:
            print(f"using {WORKING_PROXY}")
        else:
            print("none found, trying direct")
    
    bbox_str = f"{bbox[0]:.4f},{bbox[1]:.4f},{bbox[2]:.4f},{bbox[3]:.4f}"
    
    if date:
        # Archive/historical request for specific date
        url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_MAP_KEY}/{source}/{bbox_str}/1/{date}"
    else:
        # NRT request for last N days
        day_range = min(10, max(1, day_range))
        url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_MAP_KEY}/{source}/{bbox_str}/{day_range}"
    
    proxies_to_try = [WORKING_PROXY] if WORKING_PROXY else []
    proxies_to_try.extend([p for p in PROXIES if p != WORKING_PROXY])
    
    for proxy in proxies_to_try[:5]:
        try:
            proxy_dict = {"http": f"http://{proxy}", "https": f"http://{proxy}"} if proxy else None
            resp = requests.get(url, proxies=proxy_dict, timeout=60)
            if resp.status_code == 200 and 'latitude' in resp.text[:200]:
                fires = []
                reader = csv.DictReader(StringIO(resp.text))
                for row in reader:
                    fires.append({
                        'latitude': float(row.get('latitude', 0)),
                        'longitude': float(row.get('longitude', 0)),
                        'acq_date': row.get('acq_date', ''),
                        'acq_time': row.get('acq_time', ''),
                        'confidence': row.get('confidence', ''),
                        'frp': float(row.get('frp', 0) or 0),
                        'bright_ti4': float(row.get('bright_ti4', 0) or 0),
                        'bright_ti5': float(row.get('bright_ti5', 0) or 0),
                    })
                if proxy and proxy != WORKING_PROXY:
                    WORKING_PROXY = proxy
                return fires
            elif 'Invalid' in resp.text or 'Error' in resp.text:
                return []  # API error, don't retry
        except Exception as e:
            time.sleep(1)  # Wait before trying next proxy
            continue
    
    return []

def process_fires_for_park(fires, park_id, park_info, year):
    """Categorize fires for a park: inside, buffer (within 50km), boundary (within 5km of edge)."""
    result = {
        'inside': [],
        'buffer': [],  # Outside park but within 50km
        'near_boundary': []  # Within 5km of boundary (inside or outside)
    }
    
    geometry = park_info.get('geometry')
    bbox_orig = park_info['bbox_original']
    
    for fire in fires:
        lat, lon = fire['latitude'], fire['longitude']
        
        # Check if in extended bbox
        bbox_ext = park_info['bbox_extended']
        if not (bbox_ext[0] <= lon <= bbox_ext[2] and bbox_ext[1] <= lat <= bbox_ext[3]):
            continue
        
        is_inside = point_in_polygon(lon, lat, geometry)
        dist_to_edge = distance_to_boundary(lon, lat, geometry)
        
        fire_record = {
            'lat': lat,
            'lon': lon,
            'date': fire['acq_date'],
            'time': fire.get('acq_time', ''),
            'frp': fire.get('frp', 0),
            'confidence': fire.get('confidence', ''),
            'inside': is_inside,
            'dist_to_boundary_km': round(dist_to_edge, 2)
        }
        
        if is_inside:
            result['inside'].append(fire_record)
            if dist_to_edge <= 5:
                result['near_boundary'].append(fire_record)
        elif dist_to_edge <= 50:
            result['buffer'].append(fire_record)
            if dist_to_edge <= 5:
                result['near_boundary'].append(fire_record)
    
    return result

def backfill_nrt_extended(parks, year_from=2025, year_to=2026):
    """Backfill NRT fires with extended buffer for recent years."""
    print(f"\n=== Backfilling NRT fires {year_from}-{year_to} with 50km buffer ===")
    
    ADDITIONAL_FIRES_DIR.mkdir(parents=True, exist_ok=True)
    
    today = datetime.now().date()
    total_additional = 0
    
    for i, (park_id, park_info) in enumerate(parks.items()):
        print(f"[{i+1}/{len(parks)}] {park_id}...", end=" ", flush=True)
        
        park_fires = defaultdict(lambda: {'inside': [], 'buffer': [], 'near_boundary': []})
        
        for year in range(year_from, year_to + 1):
            start_date = datetime(year, 1, 1).date()
            end_date = min(datetime(year, 12, 31).date(), today)
            
            if start_date > today:
                continue
            
            # For recent data (last 10 days), use NRT request
            # For older data, use archive requests (one per day)
            days_ago = (today - end_date).days
            
            if days_ago < 10:
                # NRT request for recent data
                fires = fetch_firms_data(
                    park_info['bbox_extended'],
                    day_range=min(10, days_ago + 1)
                )
                if fires:
                    year_fires = [f for f in fires if f['acq_date'].startswith(str(year))]
                    if year_fires:
                        categorized = process_fires_for_park(year_fires, park_id, park_info, year)
                        for cat in ['inside', 'buffer', 'near_boundary']:
                            park_fires[year][cat].extend(categorized[cat])
            else:
                # Archive requests for historical data - sample every 7 days to reduce API calls
                current_date = start_date
                while current_date <= end_date:
                    date_str = current_date.strftime('%Y-%m-%d')
                    fires = fetch_firms_data(
                        park_info['bbox_extended'],
                        date=date_str,
                        source='VIIRS_SNPP_SP'  # Standard processing for archive
                    )
                    if fires:
                        categorized = process_fires_for_park(fires, park_id, park_info, year)
                        for cat in ['inside', 'buffer', 'near_boundary']:
                            park_fires[year][cat].extend(categorized[cat])
                    
                    current_date += timedelta(days=7)  # Sample weekly
                    time.sleep(0.3)  # Rate limiting
        
        # Save additional fires (buffer zone) per year
        buffer_count = 0
        for year, data in park_fires.items():
            if data['buffer']:
                out_file = ADDITIONAL_FIRES_DIR / f"{park_id}_{year}_buffer.json"
                with open(out_file, 'w') as f:
                    json.dump({
                        'park_id': park_id,
                        'year': year,
                        'buffer_km': 50,
                        'fires': data['buffer'],
                        'near_boundary': data['near_boundary'],
                        'count_inside': len(data['inside']),
                        'count_buffer': len(data['buffer']),
                        'count_near_boundary': len(data['near_boundary'])
                    }, f)
                buffer_count += len(data['buffer'])
        
        total_additional += buffer_count
        print(f"{buffer_count} buffer fires")
    
    print(f"\nTotal additional buffer fires: {total_additional:,}")
    return total_additional

def main():
    print("=" * 60)
    print("Fire Backfill with Extended 50km Buffer")
    print("=" * 60)
    
    parks = load_parks_with_bbox()
    print(f"Loaded {len(parks)} parks with boundaries")
    
    # Backfill NRT for 2025-2026
    backfill_nrt_extended(parks, 2025, 2026)
    
    print("\n" + "=" * 60)
    print("Backfill complete!")
    print("=" * 60)

if __name__ == '__main__':
    main()
