#!/usr/bin/env python3
"""
Download OSM Place Names for Conservation Areas

Uses Overpass API to download villages, towns, rivers, and other named places
around protected areas to provide geographic context for fire trajectory descriptions.

Usage:
    python scripts/download_osm_places.py [--park PARK_ID] [--limit N] [--buffer-km 50]
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
    from shapely.geometry import shape, Point, mapping
    from shapely.ops import unary_union
    HAS_GEO = True
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: source .venv/bin/activate && pip install shapely")
    HAS_GEO = False

# Configuration
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "db.sqlite3"

# Overpass API endpoint
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Place types to download (OSM tags)
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


class OSMPlacesDownloader:
    """Download and store OSM place names around protected areas"""
    
    def __init__(self, db_path=DB_PATH, buffer_km: float = 50):
        self.db_path = db_path
        self.buffer_km = buffer_km
        self.keystones = self._load_keystones()
        self._init_db()
        
        # Rate limiting for Overpass API
        self.last_request_time = 0
        self.min_request_interval = 5  # seconds between requests
        self.park_sleep_interval = 30  # seconds between parks
    
    def _load_keystones(self) -> List[Dict]:
        """Load keystone protected areas with boundaries"""
        keystones_path = DATA_DIR / "keystones_with_boundaries.json"
        if keystones_path.exists():
            with open(keystones_path) as f:
                return json.load(f)
        logger.error(f"Keystones file not found: {keystones_path}")
        return []
    
    def _init_db(self):
        """Initialize database tables for OSM places"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        
        # Create osm_places table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS osm_places (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id TEXT NOT NULL,
                place_type TEXT NOT NULL,
                name TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                geojson TEXT,
                osm_id TEXT,
                osm_tags TEXT,
                UNIQUE(park_id, osm_id, place_type)
            )
        """)
        
        # Create indexes for efficient querying
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_osm_places_park 
            ON osm_places(park_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_osm_places_type 
            ON osm_places(place_type)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_osm_places_location 
            ON osm_places(lat, lon)
        """)
        
        # Create sync tracking table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS osm_places_sync (
                park_id TEXT PRIMARY KEY,
                last_sync TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                place_count INTEGER DEFAULT 0,
                buffer_km REAL,
                error_message TEXT
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("Database tables initialized")
    
    def _rate_limit(self):
        """Ensure we don't exceed Overpass API rate limits"""
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.min_request_interval:
            sleep_time = self.min_request_interval - elapsed
            logger.debug(f"Rate limiting: sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)
        self.last_request_time = time.time()
    
    def _get_bbox(self, park: Dict) -> Optional[Tuple[float, float, float, float]]:
        """Get bounding box for park with buffer"""
        if not park.get('geometry'):
            return None
        
        try:
            geom = shape(park['geometry'])
            bounds = geom.bounds  # (minx, miny, maxx, maxy)
            
            # Add buffer in degrees (approximate)
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
    
    def _build_overpass_query(self, bbox: Tuple[float, float, float, float],
                               place_types: List[str] = None) -> str:
        """Build Overpass QL query for place names"""
        south, west, north, east = bbox
        
        if place_types is None:
            place_types = list(PLACE_TYPES.keys())
        
        # Build query for each place type
        queries = []
        for ptype in place_types:
            if ptype not in PLACE_TYPES:
                continue
            tag = PLACE_TYPES[ptype]
            key, value = tag.split('=')
            
            # Query for nodes (most settlements)
            queries.append(f'node["{key}"="{value}"]["name"]({south},{west},{north},{east});')
            # Query for ways (rivers, streams, some settlements)
            queries.append(f'way["{key}"="{value}"]["name"]({south},{west},{north},{east});')
        
        query = f"""
[out:json][timeout:180];
(
{chr(10).join(queries)}
);
out center tags;
"""
        return query
    
    def _query_overpass(self, query: str) -> Optional[Dict]:
        """Execute Overpass API query with rate limiting"""
        self._rate_limit()
        
        try:
            response = requests.post(
                OVERPASS_URL,
                data={'data': query},
                timeout=300,
                headers={'User-Agent': '5MPGlobe/1.0 (Conservation Research)'}
            )
            
            if response.status_code == 429:
                logger.warning("Rate limited by Overpass API, waiting 60s...")
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
        """Parse OSM elements into place records"""
        places = []
        
        for element in data.get('elements', []):
            tags = element.get('tags', {})
            name = tags.get('name')
            if not name:
                continue
            
            # Determine place type from tags
            place_type = None
            for ptype, tag in PLACE_TYPES.items():
                key, value = tag.split('=')
                if tags.get(key) == value:
                    place_type = ptype
                    break
            
            if not place_type:
                continue
            
            # Get coordinates
            if element['type'] == 'node':
                lat = element.get('lat')
                lon = element.get('lon')
                geojson = json.dumps({
                    'type': 'Point',
                    'coordinates': [lon, lat]
                })
            elif element['type'] == 'way':
                # Use center point for ways
                center = element.get('center', {})
                lat = center.get('lat')
                lon = center.get('lon')
                geojson = json.dumps({
                    'type': 'Point',
                    'coordinates': [lon, lat]
                }) if lat and lon else None
            else:
                continue
            
            if lat is None or lon is None:
                continue
            
            places.append({
                'place_type': place_type,
                'name': name,
                'lat': lat,
                'lon': lon,
                'geojson': geojson,
                'osm_id': f"{element['type']}/{element['id']}",
                'osm_tags': json.dumps(tags)
            })
        
        return places
    
    def _save_places(self, conn, park_id: str, places: List[Dict]):
        """Save places to database"""
        cursor = conn.cursor()
        
        # Delete existing places for this park (to refresh)
        cursor.execute("DELETE FROM osm_places WHERE park_id = ?", (park_id,))
        
        # Insert new places
        for place in places:
            try:
                cursor.execute("""
                    INSERT INTO osm_places 
                    (park_id, place_type, name, lat, lon, geojson, osm_id, osm_tags)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    park_id,
                    place['place_type'],
                    place['name'],
                    place['lat'],
                    place['lon'],
                    place['geojson'],
                    place['osm_id'],
                    place['osm_tags']
                ))
            except sqlite3.IntegrityError:
                pass  # Duplicate, skip
        
        conn.commit()
    
    def download_park_places(self, park_id: str) -> Optional[int]:
        """Download OSM places for a single park"""
        park = next((p for p in self.keystones if p['id'] == park_id), None)
        if not park:
            logger.error(f"Park not found: {park_id}")
            return None
        
        bbox = self._get_bbox(park)
        if not bbox:
            logger.error(f"Could not get bbox for {park_id}")
            return None
        
        logger.info(f"Downloading OSM places for {park_id} (bbox: {bbox})")
        
        # Build and execute query
        query = self._build_overpass_query(bbox)
        data = self._query_overpass(query)
        
        if data is None:
            self._update_sync_status(park_id, 0, "Query failed")
            return None
        
        # Parse results
        places = self._parse_osm_elements(data)
        logger.info(f"Found {len(places)} places for {park_id}")
        
        # Save to database
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        self._save_places(conn, park_id, places)
        self._update_sync_status(park_id, len(places))
        conn.close()
        
        # Clean up memory
        gc.collect()
        
        return len(places)
    
    def _update_sync_status(self, park_id: str, place_count: int, error: str = None):
        """Update sync status for a park"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO osm_places_sync 
            (park_id, last_sync, place_count, buffer_km, error_message)
            VALUES (?, datetime('now'), ?, ?, ?)
        """, (park_id, place_count, self.buffer_km, error))
        conn.commit()
        conn.close()
    
    def download_all_parks(self, limit: int = None, skip_existing: bool = True):
        """Download OSM places for all parks"""
        parks_to_process = []
        
        # Get existing sync status
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()
        
        for park in self.keystones:
            if not park.get('geometry'):
                continue
            
            park_id = park['id']
            
            if skip_existing:
                cursor.execute(
                    "SELECT place_count FROM osm_places_sync WHERE park_id = ?",
                    (park_id,)
                )
                row = cursor.fetchone()
                if row and row[0] > 0:
                    logger.debug(f"Skipping {park_id} (already has {row[0]} places)")
                    continue
            
            parks_to_process.append(park)
        
        conn.close()
        
        if limit:
            parks_to_process = parks_to_process[:limit]
        
        logger.info(f"Processing {len(parks_to_process)} parks")
        
        for i, park in enumerate(parks_to_process):
            logger.info(f"\n[{i+1}/{len(parks_to_process)}] Processing {park['id']}")
            
            try:
                count = self.download_park_places(park['id'])
                if count is not None:
                    logger.info(f"Saved {count} places for {park['id']}")
            except Exception as e:
                logger.error(f"Error processing {park['id']}: {e}")
                self._update_sync_status(park['id'], 0, str(e))
            
            # Sleep between parks to avoid overloading Overpass
            if i < len(parks_to_process) - 1:
                logger.info(f"Waiting {self.park_sleep_interval}s before next park...")
                time.sleep(self.park_sleep_interval)
    
    def get_stats(self) -> Dict:
        """Get download statistics"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()
        
        stats = {}
        
        # Total places
        cursor.execute("SELECT COUNT(*) FROM osm_places")
        stats['total_places'] = cursor.fetchone()[0]
        
        # Parks with data
        cursor.execute("SELECT COUNT(DISTINCT park_id) FROM osm_places")
        stats['parks_with_data'] = cursor.fetchone()[0]
        
        # Places by type
        cursor.execute("""
            SELECT place_type, COUNT(*) 
            FROM osm_places 
            GROUP BY place_type
        """)
        stats['by_type'] = dict(cursor.fetchall())
        
        conn.close()
        return stats


# Utility functions for use by other modules

def get_db_connection(db_path=DB_PATH):
    """Get database connection with WAL mode"""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_nearest_place(lat: float, lon: float, place_type: str = None, 
                      park_id: str = None, max_distance_km: float = 100,
                      db_path=DB_PATH) -> Optional[Dict]:
    """
    Find the nearest named place to a given location.
    
    Args:
        lat: Latitude
        lon: Longitude
        place_type: Optional filter by type (village, town, river, etc.)
        park_id: Optional filter by park (only search places near this park)
        max_distance_km: Maximum distance to search (default 100km)
        db_path: Path to database
    
    Returns:
        Dict with place info and distance_km, or None if not found
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # Build query based on filters
    conditions = []
    params = []
    
    if place_type:
        conditions.append("place_type = ?")
        params.append(place_type)
    
    if park_id:
        conditions.append("park_id = ?")
        params.append(park_id)
    
    # Add bounding box filter for efficiency
    # (rough degrees for max_distance_km)
    buffer_deg = max_distance_km / 111.0
    conditions.append("lat BETWEEN ? AND ?")
    params.extend([lat - buffer_deg, lat + buffer_deg])
    conditions.append("lon BETWEEN ? AND ?")
    params.extend([lon - buffer_deg, lon + buffer_deg])
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    # Query with distance calculation (approximate using equirectangular projection)
    # More accurate than just Euclidean, less complex than full Haversine
    query = f"""
        SELECT 
            id, park_id, place_type, name, lat, lon, geojson, osm_id,
            (
                (({lat} - lat) * 111.0) * (({lat} - lat) * 111.0) +
                (({lon} - lon) * 111.0 * COS(RADIANS({lat}))) * 
                (({lon} - lon) * 111.0 * COS(RADIANS({lat})))
            ) as dist_sq
        FROM osm_places
        WHERE {where_clause}
        ORDER BY dist_sq
        LIMIT 1
    """
    
    # SQLite doesn't have RADIANS or COS, so use simpler approximation
    cos_lat = __import__('math').cos(__import__('math').radians(lat))
    
    query = f"""
        SELECT 
            id, park_id, place_type, name, lat, lon, geojson, osm_id,
            (
                (({lat} - lat) * 111.0) * (({lat} - lat) * 111.0) +
                (({lon} - lon) * 111.0 * {cos_lat}) * 
                (({lon} - lon) * 111.0 * {cos_lat})
            ) as dist_sq
        FROM osm_places
        WHERE {where_clause}
        ORDER BY dist_sq
        LIMIT 1
    """
    
    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
    
    distance_km = row['dist_sq'] ** 0.5
    
    if distance_km > max_distance_km:
        return None
    
    return {
        'id': row['id'],
        'park_id': row['park_id'],
        'place_type': row['place_type'],
        'name': row['name'],
        'lat': row['lat'],
        'lon': row['lon'],
        'distance_km': round(distance_km, 2)
    }


def get_nearest_places_by_type(lat: float, lon: float, 
                                park_id: str = None,
                                max_distance_km: float = 100,
                                db_path=DB_PATH) -> Dict[str, Dict]:
    """
    Get the nearest place of each type to a given location.
    
    Returns:
        Dict mapping place_type -> nearest place info
    """
    result = {}
    for place_type in PLACE_TYPES.keys():
        place = get_nearest_place(
            lat, lon, 
            place_type=place_type,
            park_id=park_id,
            max_distance_km=max_distance_km,
            db_path=db_path
        )
        if place:
            result[place_type] = place
    return result


def get_places_in_bbox(south: float, west: float, north: float, east: float,
                       place_type: str = None, db_path=DB_PATH) -> List[Dict]:
    """
    Get all places within a bounding box.
    
    Args:
        south, west, north, east: Bounding box coordinates
        place_type: Optional filter by type
    
    Returns:
        List of place dicts
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    conditions = [
        "lat BETWEEN ? AND ?",
        "lon BETWEEN ? AND ?"
    ]
    params = [south, north, west, east]
    
    if place_type:
        conditions.append("place_type = ?")
        params.append(place_type)
    
    query = f"""
        SELECT id, park_id, place_type, name, lat, lon, geojson, osm_id
        FROM osm_places
        WHERE {' AND '.join(conditions)}
    """
    
    cursor.execute(query, params)
    places = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return places


def generate_trajectory_description(trajectory: Dict, park_id: str = None,
                                    db_path=DB_PATH) -> str:
    """
    Generate a human-readable description of a fire trajectory using real place names.
    
    Args:
        trajectory: Dict containing trajectory data with points (lat, lon, date)
        park_id: Park ID for context-aware place lookup
    
    Returns:
        String description like "Fire group came from near [village], 
        crossed [river], entered park near [landmark]"
    """
    points = trajectory.get('points', [])
    if len(points) < 2:
        return "Insufficient trajectory data"
    
    description_parts = []
    
    # Get starting location context
    start = points[0]
    start_places = get_nearest_places_by_type(
        start['lat'], start['lon'],
        park_id=park_id,
        max_distance_km=50,
        db_path=db_path
    )
    
    if 'village' in start_places:
        village = start_places['village']
        description_parts.append(
            f"Fire group originated near {village['name']} ({village['distance_km']:.0f}km away)"
        )
    elif 'town' in start_places:
        town = start_places['town']
        description_parts.append(
            f"Fire group originated near {town['name']} ({town['distance_km']:.0f}km away)"
        )
    else:
        description_parts.append(
            f"Fire group originated at coordinates ({start['lat']:.3f}, {start['lon']:.3f})"
        )
    
    # Check for river crossings along the path
    rivers_crossed = set()
    for i, point in enumerate(points[:-1]):
        next_point = points[i + 1]
        
        # Check midpoint for rivers
        mid_lat = (point['lat'] + next_point['lat']) / 2
        mid_lon = (point['lon'] + next_point['lon']) / 2
        
        river = get_nearest_place(
            mid_lat, mid_lon,
            place_type='river',
            park_id=park_id,
            max_distance_km=10,
            db_path=db_path
        )
        
        if river and river['name'] not in rivers_crossed:
            rivers_crossed.add(river['name'])
    
    if rivers_crossed:
        if len(rivers_crossed) == 1:
            description_parts.append(f"crossed {list(rivers_crossed)[0]} River")
        else:
            rivers_list = list(rivers_crossed)[:3]  # Limit to 3
            description_parts.append(f"crossed {', '.join(rivers_list[:-1])} and {rivers_list[-1]} rivers")
    
    # Get ending location context
    end = points[-1]
    end_places = get_nearest_places_by_type(
        end['lat'], end['lon'],
        park_id=park_id,
        max_distance_km=50,
        db_path=db_path
    )
    
    if 'village' in end_places:
        village = end_places['village']
        description_parts.append(
            f"and was last detected near {village['name']}"
        )
    
    # Calculate overall movement direction
    total_lat_change = end['lat'] - start['lat']
    total_lon_change = end['lon'] - start['lon']
    
    direction = ""
    if abs(total_lat_change) > abs(total_lon_change):
        direction = "southward" if total_lat_change < 0 else "northward"
    else:
        direction = "westward" if total_lon_change < 0 else "eastward"
    
    # Calculate distance
    import math
    distance_km = math.sqrt(
        (total_lat_change * 111) ** 2 + 
        (total_lon_change * 111 * math.cos(math.radians(start['lat']))) ** 2
    )
    
    description_parts.append(
        f"moving {direction} approximately {distance_km:.0f}km"
    )
    
    return ", ".join(description_parts) + "."


def main():
    parser = argparse.ArgumentParser(description='Download OSM places for parks')
    parser.add_argument('--park', type=str, help='Process specific park ID')
    parser.add_argument('--limit', type=int, help='Limit number of parks to process')
    parser.add_argument('--buffer-km', type=float, default=50, 
                        help='Buffer distance in km (default: 50)')
    parser.add_argument('--refresh', action='store_true',
                        help='Re-download even if already exists')
    parser.add_argument('--stats', action='store_true',
                        help='Show statistics only')
    args = parser.parse_args()
    
    downloader = OSMPlacesDownloader(buffer_km=args.buffer_km)
    
    if args.stats:
        stats = downloader.get_stats()
        print(f"\nOSM Places Statistics:")
        print(f"  Total places: {stats['total_places']}")
        print(f"  Parks with data: {stats['parks_with_data']}")
        print(f"  By type:")
        for ptype, count in sorted(stats['by_type'].items()):
            print(f"    {ptype}: {count}")
        return
    
    if args.park:
        count = downloader.download_park_places(args.park)
        if count is not None:
            print(f"\nDownloaded {count} places for {args.park}")
    else:
        downloader.download_all_parks(
            limit=args.limit,
            skip_existing=not args.refresh
        )
        stats = downloader.get_stats()
        print(f"\nDownload complete. Total places: {stats['total_places']}")


if __name__ == '__main__':
    main()
