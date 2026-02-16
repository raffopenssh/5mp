#!/usr/bin/env python3
"""
FIRMS NRT Fire Data Downloader

Downloads Near Real-Time fire detection data from NASA FIRMS API
using a proxy to bypass network restrictions.

Usage:
    python download_nrt.py --park COD_Virunga --days 5
    python download_nrt.py --all --days 5
    python download_nrt.py --backfill --start 2025-01-01 --end 2025-01-31
"""

import argparse
import csv
import io
import json
import logging
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import requests

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

# Cache for park data
_park_cache = None


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
    """Get bounding box for a park from keystones data.
    
    Args:
        park_id: Park identifier (e.g., 'COD_Virunga')
        buffer_km: Buffer in kilometers to extend bbox (default 0, use 50 for NRT)
    
    Returns:
        (min_lon, min_lat, max_lon, max_lat) or None
    """
    parks = load_park_data()
    
    # Convert buffer_km to approximate degrees (1 degree ≈ 111km at equator)
    buffer_deg = buffer_km / 111.0 if buffer_km > 0 else 0
    
    for park in parks:
        # Match by id field (e.g., "COD_Virunga")
        if park.get('id') == park_id:
            geom = park.get('geometry', {})
            if geom.get('type') == 'Polygon':
                coords = geom['coordinates'][0]
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                return (min(lons) - buffer_deg, min(lats) - buffer_deg, 
                        max(lons) + buffer_deg, max(lats) + buffer_deg)
            elif geom.get('type') == 'MultiPolygon':
                all_lons = []
                all_lats = []
                for polygon in geom['coordinates']:
                    for ring in polygon:
                        for c in ring:
                            all_lons.append(c[0])
                            all_lats.append(c[1])
                if all_lons and all_lats:
                    return (min(all_lons) - buffer_deg, min(all_lats) - buffer_deg,
                            max(all_lons) + buffer_deg, max(all_lats) + buffer_deg)
            
            # Fallback to coordinates if no geometry
            coords = park.get('coordinates')
            if coords:
                lon, lat = coords.get("lon", 0), coords.get("lat", 0)
                half = 0.5 + buffer_deg
                return (lon - half, lat - half, lon + half, lat + half)
    
    logger.warning(f"Park {park_id} not found in keystones data")
    return None


def get_all_park_ids() -> List[str]:
    """Get all park IDs from keystones data."""
    parks = load_park_data()
    return [p.get('id') for p in parks if p.get('id')]


def fetch_fire_data(
    bbox: Tuple[float, float, float, float],
    proxy: str,
    days: int = 5,
    source: str = "VIIRS_SNPP_NRT",
    date: Optional[str] = None,
    retry_proxies: bool = True
) -> List[Dict]:
    """
    Fetch fire data from FIRMS API.
    
    Args:
        bbox: (west, south, east, north) bounding box
        proxy: HTTP proxy to use
        days: Number of days of data (1-10 for NRT)
        source: FIRMS data source
        date: Specific date for historical data (YYYY-MM-DD)
        retry_proxies: If True, try other proxies on failure
    
    Returns:
        List of fire detection dictionaries
    """
    west, south, east, north = bbox
    
    if date:
        # Historical data request
        url = f"{BASE_URL}/area/csv/{MAP_KEY}/{source}/{west},{south},{east},{north}/1/{date}"
    else:
        # NRT data request (last N days)
        days = min(days, 10)  # API limit
        url = f"{BASE_URL}/area/csv/{MAP_KEY}/{source}/{west},{south},{east},{north}/{days}"
    
    # Try with provided proxy first, then others if retry enabled
    proxies_to_try = [proxy]
    if retry_proxies:
        proxies_to_try.extend([p for p in PROXIES if p != proxy])
    
    last_error = None
    for try_proxy in proxies_to_try[:5]:  # Try up to 5 proxies
        try:
            response = requests.get(
                url,
                proxies={"http": f"http://{try_proxy}", "https": f"http://{try_proxy}"},
                timeout=60
            )
            
            if not response.ok:
                logger.warning(f"API error with {try_proxy}: {response.status_code}")
                last_error = f"HTTP {response.status_code}"
                time.sleep(1)
                continue
            
            # Check for error messages
            if "Invalid" in response.text or "Error" in response.text:
                logger.warning(f"API returned error: {response.text[:100]}")
                return []
            
            # Parse CSV
            fires = []
            reader = csv.DictReader(io.StringIO(response.text))
            for row in reader:
                fires.append(row)
            
            if try_proxy != proxy:
                logger.info(f"Success with alternate proxy: {try_proxy}")
            
            return fires
            
        except Exception as e:
            logger.warning(f"Proxy {try_proxy} failed: {type(e).__name__}")
            last_error = str(e)
            time.sleep(1)
            continue
    
    logger.error(f"All proxies failed. Last error: {last_error}")
    return []


def store_fire_data(db_path: str, park_id: str, fires: List[Dict]) -> int:
    """
    Store fire data in the database.
    
    Returns number of new records inserted.
    """
    if not fires:
        return 0
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    inserted = 0
    for fire in fires:
        try:
            # Calculate grid cell ID (0.1 degree cells)
            lat = float(fire.get('latitude', 0))
            lon = float(fire.get('longitude', 0))
            grid_lat = round(lat * 10) / 10
            grid_lon = round(lon * 10) / 10
            grid_cell_id = f"{grid_lat}_{grid_lon}"
            
            cursor.execute("""
                INSERT OR IGNORE INTO fire_detections 
                (latitude, longitude, brightness, bright_t31, scan, track, 
                 acq_date, acq_time, satellite, instrument, confidence, 
                 version, frp, daynight, grid_cell_id, in_protected_area, protected_area_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (
                lat,
                lon,
                float(fire.get('bright_ti4', 0) or fire.get('brightness', 0) or 0),
                float(fire.get('bright_ti5', 0) or fire.get('bright_t31', 0) or 0),
                float(fire.get('scan', 0) or 0),
                float(fire.get('track', 0) or 0),
                fire.get('acq_date', ''),
                fire.get('acq_time', ''),
                fire.get('satellite', ''),
                fire.get('instrument', 'VIIRS'),
                fire.get('confidence', ''),
                fire.get('version', 'NRT'),
                float(fire.get('frp', 0) or 0),
                fire.get('daynight', ''),
                grid_cell_id,
                park_id
            ))
            if cursor.rowcount > 0:
                inserted += 1
        except Exception as e:
            logger.debug(f"Insert error: {e}")
            continue
    
    conn.commit()
    conn.close()
    
    return inserted


def download_park_fires(
    park_id: str,
    db_path: str = DB_PATH,
    days: int = 5,
    proxy: Optional[str] = None,
    buffer_km: float = 0
) -> int:
    """Download NRT fire data for a single park.
    
    Args:
        park_id: Park identifier
        db_path: Database path
        days: Days of NRT data (1-10)
        proxy: HTTP proxy to use
        buffer_km: Buffer around park in km (0 = park boundary only, 50 = include 50km buffer)
    """
    
    if not proxy:
        proxy = get_working_proxy()
        if not proxy:
            logger.error("No working proxy found")
            return 0
    
    bbox = get_park_bbox(park_id, buffer_km=buffer_km)
    if not bbox:
        logger.warning(f"No bbox found for park {park_id}")
        return 0
    
    buffer_str = f" (+{buffer_km}km buffer)" if buffer_km > 0 else ""
    logger.info(f"Downloading fires for {park_id}{buffer_str}, bbox={[f'{x:.2f}' for x in bbox]}, days={days}")
    
    fires = fetch_fire_data(bbox, proxy, days=days)
    
    if fires:
        inserted = store_fire_data(db_path, park_id, fires)
        logger.info(f"Park {park_id}: {len(fires)} fires fetched, {inserted} new records")
        return inserted
    else:
        logger.info(f"Park {park_id}: No fires in last {days} days")
    
    return 0


def download_all_parks(days: int = 5, db_path: str = DB_PATH, buffer_km: float = 0) -> Dict[str, int]:
    """Download NRT fire data for all parks.
    
    Args:
        days: Days of NRT data (1-10)
        db_path: Database path
        buffer_km: Buffer around each park in km (0 = park only, 50 = include 50km buffer)
    """
    
    proxy = get_working_proxy()
    if not proxy:
        logger.error("No working proxy found")
        return {}
    
    park_ids = get_all_park_ids()
    buffer_str = f" with {buffer_km}km buffer" if buffer_km > 0 else ""
    logger.info(f"Downloading fires for {len(park_ids)} parks{buffer_str}")
    
    results = {}
    for i, park_id in enumerate(park_ids):
        try:
            inserted = download_park_fires(park_id, db_path, days, proxy, buffer_km=buffer_km)
            results[park_id] = inserted
            
            # Rate limiting
            time.sleep(RATE_LIMIT_DELAY)
            
            # Progress
            if (i + 1) % 10 == 0:
                logger.info(f"Progress: {i + 1}/{len(park_ids)} parks")
                
        except Exception as e:
            logger.error(f"Error processing {park_id}: {e}")
            results[park_id] = 0
    
    total = sum(results.values())
    logger.info(f"Total: {total} new fire records across {len(park_ids)} parks")
    
    return results


def backfill_date_range(
    start_date: str,
    end_date: str,
    db_path: str = DB_PATH,
    buffer_km: float = 0
) -> int:
    """Backfill fire data for a date range.
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        db_path: Database path
        buffer_km: Buffer around each park in km (0 = park only, 50 = include 50km buffer)
    """
    
    proxy = get_working_proxy()
    if not proxy:
        logger.error("No working proxy found")
        return 0
    
    park_ids = get_all_park_ids()
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    
    buffer_str = f" with {buffer_km}km buffer" if buffer_km > 0 else ""
    logger.info(f"Backfilling {start_date} to {end_date}{buffer_str}")
    
    total_inserted = 0
    current = start
    
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        logger.info(f"Backfilling {date_str}")
        
        for park_id in park_ids:
            bbox = get_park_bbox(park_id, buffer_km=buffer_km)
            if not bbox:
                continue
            
            fires = fetch_fire_data(
                bbox, proxy, days=1, 
                source="VIIRS_SNPP_SP",  # Use standard processing for historical
                date=date_str
            )
            
            if fires:
                inserted = store_fire_data(db_path, park_id, fires)
                total_inserted += inserted
            
            time.sleep(RATE_LIMIT_DELAY)
        
        current += timedelta(days=1)
    
    logger.info(f"Backfill complete: {total_inserted} records")
    return total_inserted


def main():
    parser = argparse.ArgumentParser(description="Download FIRMS NRT fire data")
    parser.add_argument("--park", help="Specific park ID (e.g., COD_Virunga)")
    parser.add_argument("--all", action="store_true", help="Download for all parks")
    parser.add_argument("--days", type=int, default=5, help="Days of data (1-10)")
    parser.add_argument("--buffer", type=float, default=0, help="Buffer in km around park (default 0, use 50 for full coverage)")
    parser.add_argument("--backfill", action="store_true", help="Backfill historical data")
    parser.add_argument("--start", help="Start date for backfill (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date for backfill (YYYY-MM-DD)")
    parser.add_argument("--db", default=DB_PATH, help="Database path")
    
    args = parser.parse_args()
    
    # Store buffer in global for use by get_park_bbox
    global _buffer_km
    _buffer_km = args.buffer
    
    if args.backfill:
        if not args.start or not args.end:
            parser.error("--backfill requires --start and --end dates")
        backfill_date_range(args.start, args.end, args.db, buffer_km=args.buffer)
    elif args.all:
        download_all_parks(args.days, args.db, buffer_km=args.buffer)
    elif args.park:
        download_park_fires(args.park, args.db, args.days, buffer_km=args.buffer)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
