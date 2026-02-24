#!/usr/bin/env python3
"""
FIRMS NRT Fire Data Downloader

Downloads Near Real-Time fire detection data from NASA FIRMS API
and saves to JSON files for the fire analysis pipeline.

Usage:
    python download_nrt.py --park COD_Virunga --days 5
    python download_nrt.py --all --days 5 --buffer 50
    python download_nrt.py --backfill --start 2025-01-01 --end 2025-01-31
"""

import argparse
import csv
import io
import json
import logging
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import requests
from shapely.geometry import Point, shape
from shapely.ops import nearest_points

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from config import (
    MAP_KEY, BASE_URL, PROXIES, SOURCES, 
    RATE_LIMIT_DELAY, DB_PATH
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Output directory for NRT fire JSON files
OUTPUT_DIR = Path(DB_PATH).parent / "data" / "fire_nrt"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Cache for park data
_park_cache = None
_park_boundaries = {}


def load_park_data() -> List[Dict]:
    """Load park data from keystones_with_boundaries.json."""
    global _park_cache
    if _park_cache is not None:
        return _park_cache
    
    keystones_path = Path(DB_PATH).parent / "data" / "keystones_with_boundaries.json"
    if not keystones_path.exists():
        logger.error(f"Keystones file not found: {keystones_path}")
        return []
    
    with open(keystones_path) as f:
        _park_cache = json.load(f)
    
    return _park_cache


def get_park_boundary(park_id: str):
    """Get park boundary as shapely geometry."""
    global _park_boundaries
    if park_id in _park_boundaries:
        return _park_boundaries[park_id]
    
    parks = load_park_data()
    for park in parks:
        if park.get('id') == park_id:
            geom_data = park.get('boundary') or park.get('geometry')
            if geom_data:
                try:
                    geom = shape(geom_data)
                    _park_boundaries[park_id] = geom
                    return geom
                except:
                    pass
    return None


def get_working_proxy(proxies: List[str] = PROXIES) -> Optional[str]:
    """Find a working proxy from the list."""
    random.shuffle(proxies)  # Distribute load
    
    for proxy in proxies[:5]:  # Try first 5
        try:
            test_url = f"{BASE_URL.replace('/api', '')}/mapserver/mapkey_status/?MAP_KEY={MAP_KEY}"
            response = requests.get(
                test_url,
                proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"},
                timeout=10
            )
            if response.ok and "current_transactions" in response.text:
                logger.info(f"Using proxy: {proxy}")
                return proxy
        except Exception as e:
            logger.debug(f"Proxy {proxy} failed: {e}")
            continue
    
    return None


def get_park_bbox(park_id: str, buffer_km: float = 0) -> Optional[Tuple[float, float, float, float]]:
    """Get bounding box for a park from keystones data."""
    parks = load_park_data()
    buffer_deg = buffer_km / 111.0 if buffer_km > 0 else 0
    
    for park in parks:
        if park.get('id') == park_id:
            # Try bounds first, then calculate from geometry
            bounds = park.get('bounds')
            if not bounds and park.get('geometry'):
                try:
                    geom = shape(park['geometry'])
                    b = geom.bounds  # (minx, miny, maxx, maxy)
                    bounds = [b[0], b[1], b[2], b[3]]
                except:
                    pass
            if bounds:
                return (
                    bounds[0] - buffer_deg,  # min_lon (west)
                    bounds[1] - buffer_deg,  # min_lat (south)
                    bounds[2] + buffer_deg,  # max_lon (east)
                    bounds[3] + buffer_deg   # max_lat (north)
                )
    return None


def download_fires_from_firms(bbox: Tuple[float, float, float, float], 
                              days: int, proxy: str,
                              source: str = None) -> List[Dict]:
    """Download fire data from FIRMS API.
    
    For historical data beyond 10 days, automatically uses Standard Processing (SP) dataset.
    NRT (Near Real-Time) only covers last 10 days.
    """
    west, south, east, north = bbox
    
    # Auto-select source based on days
    if source is None:
        source = "VIIRS_SNPP_NRT" if days <= 10 else "VIIRS_SNPP_SP"
    
    # Use date range for more than 10 days, otherwise use days parameter
    if days <= 10:
        url = f"{BASE_URL}/area/csv/{MAP_KEY}/{source}/{west},{south},{east},{north}/{days}"
    else:
        # For historical data, use date range with SP dataset
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        # Format: /source/bbox/1/YYYY-MM-DD
        url = f"{BASE_URL}/area/csv/{MAP_KEY}/{source}/{west},{south},{east},{north}/1/{start_date.strftime('%Y-%m-%d')}"
    
    proxies_dict = {"http": f"http://{proxy}", "https": f"http://{proxy}"} if proxy else None
    
    try:
        response = requests.get(url, proxies=proxies_dict, timeout=60)
        if not response.ok:
            logger.warning(f"FIRMS API error: {response.status_code}")
            return []
        
        if not response.text.strip() or 'latitude' not in response.text:
            return []
        
        reader = csv.DictReader(io.StringIO(response.text))
        fires = []
        for row in reader:
            try:
                fires.append({
                    'latitude': float(row.get('latitude', 0)),
                    'longitude': float(row.get('longitude', 0)),
                    'acq_date': row.get('acq_date', ''),
                    'acq_time': row.get('acq_time', ''),
                    'brightness': float(row.get('bright_ti4', 0) or row.get('brightness', 0) or 0),
                    'frp': float(row.get('frp', 0) or 0),
                    'confidence': row.get('confidence', ''),
                    'satellite': row.get('satellite', ''),
                    'daynight': row.get('daynight', ''),
                })
            except (ValueError, TypeError):
                continue
        
        return fires
    except Exception as e:
        logger.error(f"Download error: {e}")
        return []


def classify_fire_location(fire: Dict, park_id: str, boundary) -> Dict:
    """Classify if fire is inside park or in buffer, calculate distance."""
    lat, lon = fire['latitude'], fire['longitude']
    point = Point(lon, lat)
    
    inside = boundary.contains(point) if boundary else False
    
    # Calculate distance to boundary
    dist_km = 0
    if boundary:
        try:
            if inside:
                # Distance to edge (negative = inside)
                dist_km = -boundary.exterior.distance(point) * 111
            else:
                # Distance to boundary
                nearest = nearest_points(point, boundary)[1]
                dist_km = point.distance(nearest) * 111
        except:
            pass
    
    return {
        'lat': lat,
        'lon': lon,
        'date': fire['acq_date'],
        'time': fire['acq_time'],
        'frp': fire['frp'],
        'confidence': fire['confidence'],
        'inside': inside,
        'dist_to_boundary_km': round(dist_km, 2)
    }


def download_park_fires(park_id: str, days: int = 5, proxy: str = None, 
                        buffer_km: float = 50) -> Dict:
    """Download fires for a single park and return structured data."""
    bbox = get_park_bbox(park_id, buffer_km)
    if not bbox:
        logger.warning(f"No bbox for park {park_id}")
        return None
    
    west, south, east, north = bbox
    logger.info(f"Downloading fires for {park_id} (+{buffer_km}km buffer), "
                f"bbox=['{west:.2f}', '{south:.2f}', '{east:.2f}', '{north:.2f}'], days={days}")
    
    # Get fires from FIRMS
    fires = download_fires_from_firms(bbox, days, proxy)
    
    if not fires:
        logger.info(f"Park {park_id}: No fires in last {days} days")
        return {
            'park_id': park_id,
            'buffer_km': buffer_km,
            'download_date': datetime.now().isoformat(),
            'days': days,
            'fires': [],
            'count_inside': 0,
            'count_buffer': 0
        }
    
    # Get park boundary for classification
    boundary = get_park_boundary(park_id)
    
    # Classify each fire
    classified_fires = []
    count_inside = 0
    count_buffer = 0
    
    for fire in fires:
        classified = classify_fire_location(fire, park_id, boundary)
        classified_fires.append(classified)
        if classified['inside']:
            count_inside += 1
        else:
            count_buffer += 1
    
    logger.info(f"Park {park_id}: {len(fires)} fires fetched ({count_inside} inside, {count_buffer} buffer)")
    
    time.sleep(RATE_LIMIT_DELAY)
    
    return {
        'park_id': park_id,
        'buffer_km': buffer_km,
        'download_date': datetime.now().isoformat(),
        'days': days,
        'fires': classified_fires,
        'count_inside': count_inside,
        'count_buffer': count_buffer
    }


def save_park_fires(park_id: str, data: Dict):
    """Save fire data to JSON file."""
    if not data:
        return
    
    output_file = OUTPUT_DIR / f"{park_id}_nrt.json"
    
    # Load existing data if present
    existing_fires = []
    if output_file.exists():
        try:
            with open(output_file) as f:
                existing = json.load(f)
                existing_fires = existing.get('fires', [])
        except:
            pass
    
    # Merge fires (deduplicate by lat/lon/date/time)
    seen = set()
    for fire in existing_fires:
        key = (fire['lat'], fire['lon'], fire['date'], fire['time'])
        seen.add(key)
    
    new_fires = []
    for fire in data['fires']:
        key = (fire['lat'], fire['lon'], fire['date'], fire['time'])
        if key not in seen:
            new_fires.append(fire)
            seen.add(key)
    
    # Combine and sort by date
    all_fires = existing_fires + new_fires
    all_fires.sort(key=lambda f: (f['date'], f['time']), reverse=True)
    
    # Keep only last 60 days of data
    cutoff = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
    all_fires = [f for f in all_fires if f['date'] >= cutoff]
    
    # Update counts
    count_inside = sum(1 for f in all_fires if f['inside'])
    count_buffer = len(all_fires) - count_inside
    
    output_data = {
        'park_id': park_id,
        'buffer_km': data['buffer_km'],
        'last_updated': datetime.now().isoformat(),
        'fires': all_fires,
        'count_inside': count_inside,
        'count_buffer': count_buffer,
        'total_fires': len(all_fires)
    }
    
    with open(output_file, 'w') as f:
        json.dump(output_data, f)
    
    return len(new_fires)


def download_all_parks(days: int = 5, buffer_km: float = 50) -> Dict[str, int]:
    """Download fires for all parks and save to JSON files."""
    parks = load_park_data()
    proxy = get_working_proxy()
    
    if not proxy:
        logger.error("No working proxy found")
        return {}
    
    logger.info(f"Downloading fires for {len(parks)} parks with {buffer_km}km buffer")
    
    results = {}
    for i, park in enumerate(parks):
        park_id = park.get('id')
        if not park_id:
            continue
        
        try:
            data = download_park_fires(park_id, days, proxy, buffer_km)
            if data:
                new_count = save_park_fires(park_id, data)
                results[park_id] = new_count or 0
        except Exception as e:
            logger.error(f"Error processing {park_id}: {e}")
            continue
        
        # Progress log every 20 parks
        if (i + 1) % 20 == 0:
            logger.info(f"Progress: {i + 1}/{len(parks)} parks processed")
    
    total_new = sum(results.values())
    logger.info(f"Download complete: {total_new} new fires across {len(results)} parks")
    
    return results


def backfill_date_range(start_date: str, end_date: str, buffer_km: float = 50):
    """Backfill fire data for a date range."""
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    days = (end - start).days + 1
    
    logger.info(f"Backfilling {days} days from {start_date} to {end_date}")
    download_all_parks(days=days, buffer_km=buffer_km)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download FIRMS NRT fire data to JSON")
    parser.add_argument("--park", help="Single park ID to download")
    parser.add_argument("--all", action="store_true", help="Download all parks")
    parser.add_argument("--days", type=int, default=5, help="Number of days to fetch")
    parser.add_argument("--buffer", type=float, default=30, help="Buffer in km around parks")
    parser.add_argument("--backfill", action="store_true", help="Backfill date range")
    parser.add_argument("--start", help="Start date for backfill (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date for backfill (YYYY-MM-DD)")
    
    args = parser.parse_args()
    
    if args.backfill:
        if not args.start or not args.end:
            print("--backfill requires --start and --end dates")
            sys.exit(1)
        backfill_date_range(args.start, args.end, buffer_km=args.buffer)
    elif args.all:
        download_all_parks(args.days, buffer_km=args.buffer)
    elif args.park:
        proxy = get_working_proxy()
        data = download_park_fires(args.park, args.days, proxy, args.buffer)
        if data:
            new_count = save_park_fires(args.park, data)
            print(f"Saved {new_count} new fires for {args.park}")
    else:
        parser.print_help()
