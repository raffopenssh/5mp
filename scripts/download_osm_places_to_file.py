#!/usr/bin/env python3
"""
Download OSM Place Names to JSON files (not directly to DB)

Downloads villages, towns, rivers, etc. around protected areas.
Writes to data/osm_places/<park_id>.json for review before DB import.

Usage:
    python scripts/download_osm_places_to_file.py [--park PARK_ID] [--limit N]
"""

import json
import sqlite3
import time
import logging
import argparse
import requests
import gc
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

try:
    from shapely.geometry import shape
    HAS_GEO = True
except ImportError:
    print("Missing shapely. Run: source .venv/bin/activate && pip install shapely")
    HAS_GEO = False
    exit(1)

# Configuration
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = DATA_DIR / "osm_places"
DB_PATH = BASE_DIR / "db.sqlite3"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

PLACE_TYPES = {
    'village': 'place=village',
    'town': 'place=town',
    'city': 'place=city',
    'hamlet': 'place=hamlet',
    'river': 'waterway=river',
    'stream': 'waterway=stream',
    'mountain': 'natural=peak',
    'hill': 'natural=hill',
    'lake': 'natural=water',
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OSMPlacesToFile:
    """Download OSM place names to JSON files"""
    
    def __init__(self, buffer_km: float = 50):
        self.buffer_km = buffer_km
        self.keystones = self._load_keystones()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        
        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 5
        self.park_sleep_interval = 30
    
    def _load_keystones(self) -> List[Dict]:
        keystones_path = DATA_DIR / "keystones_with_boundaries.json"
        if keystones_path.exists():
            with open(keystones_path) as f:
                return json.load(f)
        logger.error(f"Keystones file not found: {keystones_path}")
        return []
    
    def _get_parks_without_places(self) -> List[str]:
        """Get park IDs that don't have OSM places in the database"""
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()
        
        # Get parks that already have places
        cursor.execute("SELECT DISTINCT park_id FROM osm_places")
        parks_with_places = {row[0] for row in cursor.fetchall()}
        conn.close()
        
        # Get all park IDs from keystones
        all_parks = [p['id'] for p in self.keystones if p.get('geometry')]
        
        # Return parks without places
        missing = [p for p in all_parks if p not in parks_with_places]
        logger.info(f"Found {len(missing)} parks without OSM places (out of {len(all_parks)} total)")
        return missing
    
    def _rate_limit(self):
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.min_request_interval:
            sleep_time = self.min_request_interval - elapsed
            time.sleep(sleep_time)
        self.last_request_time = time.time()
    
    def _get_bbox(self, park: Dict) -> Optional[Tuple[float, float, float, float]]:
        if not park.get('geometry'):
            return None
        try:
            geom = shape(park['geometry'])
            bounds = geom.bounds
            buffer_deg = self.buffer_km / 111.0
            return (
                bounds[1] - buffer_deg,  # south
                bounds[0] - buffer_deg,  # west
                bounds[3] + buffer_deg,  # north
                bounds[2] + buffer_deg   # east
            )
        except Exception as e:
            logger.error(f"Error getting bbox for {park.get('id')}: {e}")
            return None
    
    def _build_overpass_query(self, bbox: Tuple[float, float, float, float]) -> str:
        south, west, north, east = bbox
        queries = []
        for ptype, tag in PLACE_TYPES.items():
            key, value = tag.split('=')
            queries.append(f'node["{key}"="{value}"]["name"]({south},{west},{north},{east});')
            queries.append(f'way["{key}"="{value}"]["name"]({south},{west},{north},{east});')
        
        return f"""
[out:json][timeout:180];
(
{chr(10).join(queries)}
);
out center tags;
"""
    
    def _query_overpass(self, query: str) -> Optional[Dict]:
        self._rate_limit()
        try:
            response = requests.post(
                OVERPASS_URL,
                data={'data': query},
                timeout=300,
                headers={'User-Agent': '5MPGlobe/1.0 (Conservation Research)'}
            )
            if response.status_code == 429:
                logger.warning("Rate limited, waiting 60s...")
                time.sleep(60)
                return self._query_overpass(query)
            response.raise_for_status()
            return response.json()
        except requests.Timeout:
            logger.error("Overpass API timeout")
            return None
        except requests.RequestException as e:
            logger.error(f"Overpass API error: {e}")
            return None
    
    def _parse_osm_elements(self, data: Dict) -> List[Dict]:
        places = []
        for element in data.get('elements', []):
            tags = element.get('tags', {})
            name = tags.get('name')
            if not name:
                continue
            
            place_type = None
            for ptype, tag in PLACE_TYPES.items():
                key, value = tag.split('=')
                if tags.get(key) == value:
                    place_type = ptype
                    break
            if not place_type:
                continue
            
            if element['type'] == 'node':
                lat = element.get('lat')
                lon = element.get('lon')
            elif element['type'] == 'way':
                center = element.get('center', {})
                lat = center.get('lat')
                lon = center.get('lon')
            else:
                continue
            
            if lat is None or lon is None:
                continue
            
            places.append({
                'place_type': place_type,
                'name': name,
                'lat': lat,
                'lon': lon,
                'osm_id': f"{element['type']}/{element['id']}",
                'osm_tags': tags
            })
        return places
    
    def download_park_places(self, park_id: str) -> Optional[int]:
        """Download OSM places for a single park and save to file"""
        park = next((p for p in self.keystones if p['id'] == park_id), None)
        if not park:
            logger.error(f"Park not found: {park_id}")
            return None
        
        bbox = self._get_bbox(park)
        if not bbox:
            logger.error(f"Could not get bbox for {park_id}")
            return None
        
        logger.info(f"Downloading OSM places for {park_id}")
        
        query = self._build_overpass_query(bbox)
        data = self._query_overpass(query)
        
        if data is None:
            # Write error file
            error_file = OUTPUT_DIR / f"{park_id}.error"
            error_file.write_text(f"Query failed at {datetime.now().isoformat()}")
            return None
        
        places = self._parse_osm_elements(data)
        logger.info(f"Found {len(places)} places for {park_id}")
        
        # Write to JSON file
        output_file = OUTPUT_DIR / f"{park_id}.json"
        output_data = {
            'park_id': park_id,
            'downloaded_at': datetime.now(timezone.utc).isoformat(),
            'buffer_km': self.buffer_km,
            'place_count': len(places),
            'places': places
        }
        
        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        logger.info(f"Saved to {output_file}")
        gc.collect()
        return len(places)
    
    def download_missing_parks(self, limit: int = None):
        """Download OSM places for all parks that don't have them yet"""
        missing_parks = self._get_parks_without_places()
        
        # Also skip parks that already have a JSON file
        parks_to_process = []
        for park_id in missing_parks:
            json_file = OUTPUT_DIR / f"{park_id}.json"
            if json_file.exists():
                logger.debug(f"Skipping {park_id} (JSON file exists)")
                continue
            parks_to_process.append(park_id)
        
        logger.info(f"Will process {len(parks_to_process)} parks (skipped {len(missing_parks) - len(parks_to_process)} with existing JSON)")
        
        if limit:
            parks_to_process = parks_to_process[:limit]
        
        for i, park_id in enumerate(parks_to_process):
            logger.info(f"\n[{i+1}/{len(parks_to_process)}] Processing {park_id}")
            
            try:
                count = self.download_park_places(park_id)
                if count is not None:
                    logger.info(f"Saved {count} places for {park_id}")
            except Exception as e:
                logger.error(f"Error processing {park_id}: {e}")
                error_file = OUTPUT_DIR / f"{park_id}.error"
                error_file.write_text(f"{e}\n{datetime.now().isoformat()}")
            
            if i < len(parks_to_process) - 1:
                logger.info(f"Waiting {self.park_sleep_interval}s before next park...")
                time.sleep(self.park_sleep_interval)
        
        # Summary
        json_files = list(OUTPUT_DIR.glob("*.json"))
        logger.info(f"\nComplete. {len(json_files)} JSON files in {OUTPUT_DIR}")


def main():
    parser = argparse.ArgumentParser(description='Download OSM places to JSON files')
    parser.add_argument('--park', type=str, help='Process specific park ID')
    parser.add_argument('--limit', type=int, help='Limit number of parks to process')
    parser.add_argument('--buffer-km', type=float, default=50, 
                        help='Buffer distance in km (default: 50)')
    parser.add_argument('--list-missing', action='store_true',
                        help='List parks without OSM places and exit')
    args = parser.parse_args()
    
    downloader = OSMPlacesToFile(buffer_km=args.buffer_km)
    
    if args.list_missing:
        missing = downloader._get_parks_without_places()
        print(f"\nParks without OSM places ({len(missing)}):")
        for park_id in sorted(missing):
            json_exists = (OUTPUT_DIR / f"{park_id}.json").exists()
            status = " [JSON exists]" if json_exists else ""
            print(f"  {park_id}{status}")
        return
    
    if args.park:
        count = downloader.download_park_places(args.park)
        if count is not None:
            print(f"\nDownloaded {count} places for {args.park}")
            print(f"File: {OUTPUT_DIR / args.park}.json")
    else:
        downloader.download_missing_parks(limit=args.limit)


if __name__ == '__main__':
    main()
