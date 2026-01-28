#!/usr/bin/env python3
"""
OSM Roadless Wilderness Analysis for 5MP Conservation Globe

Analyzes "roadless wilderness" for each protected area using OpenStreetMap data.
- Downloads roads from Overpass API
- Buffers roads by 1km
- Calculates what percentage of each park is >1km from any road

Usage:
    python scripts/osm_roadless_analysis.py [--park PARK_ID] [--limit N]
"""

import json
import sqlite3
import time
import logging
import argparse
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

try:
    from shapely.geometry import shape, Point, LineString, MultiLineString, box
    from shapely.ops import unary_union
    from shapely.prepared import prep
    import pyproj
    from functools import partial
    HAS_GEO = True
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: source .venv/bin/activate && pip install shapely pyproj")
    HAS_GEO = False

# Configuration
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "db.sqlite3"

# Overpass API endpoint
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Road types to include (OSM highway tags)
# Exclude tracks, paths, footways for "real roads" analysis
ROAD_TYPES = [
    'motorway', 'motorway_link',
    'trunk', 'trunk_link', 
    'primary', 'primary_link',
    'secondary', 'secondary_link',
    'tertiary', 'tertiary_link',
    'unclassified',
    'residential',
    'service',  # Include service roads (access roads)
    # Optionally include tracks for more conservative estimate:
    # 'track',
]

# Buffer distance in meters
ROAD_BUFFER_M = 1000

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OSMRoadlessAnalyzer:
    """Analyzes roadless wilderness using OpenStreetMap data"""
    
    def __init__(self, db_path=DB_PATH, road_types=None, buffer_m=ROAD_BUFFER_M):
        self.db_path = db_path
        self.road_types = road_types or ROAD_TYPES
        self.buffer_m = buffer_m
        self.keystones = self._load_keystones()
        self._init_db()
        
        # Rate limiting for Overpass API - be gentle!
        self.last_request_time = 0
        self.min_request_interval = 5  # seconds between requests
        self.park_sleep_interval = 30  # seconds between parks (~1.5 hours for 162 parks)
        self.progress_file = DATA_DIR / "osm_roadless_progress.json"
    
    def _load_keystones(self) -> List[Dict]:
        """Load keystone protected areas with boundaries"""
        keystones_path = DATA_DIR / "keystones_with_boundaries.json"
        if keystones_path.exists():
            with open(keystones_path) as f:
                return json.load(f)
        logger.error(f"Keystones file not found: {keystones_path}")
        return []
    
    def _init_db(self):
        """Initialize database tables for roadless analysis"""
        self.db_available = False
        self.json_results_path = DATA_DIR / "osm_roadless_results.json"
        
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()
            
            # Enable WAL mode for concurrent access
            cursor.execute("PRAGMA journal_mode=WAL")
            
            # Test if we can write
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS osm_roadless_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    park_id TEXT NOT NULL UNIQUE,
                    total_area_km2 REAL,
                    roaded_area_km2 REAL,
                    roadless_area_km2 REAL,
                    roadless_percentage REAL,
                    road_length_km REAL,
                    road_density_km_per_km2 REAL,
                    buffer_distance_m INTEGER,
                    road_types_used TEXT,
                    osm_query_timestamp DATETIME,
                    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    error_message TEXT
                )
            """)
            
            # Index for faster lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_osm_roadless_park_id 
                ON osm_roadless_data(park_id)
            """)
            
            conn.commit()
            
            # Verify table was created
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='osm_roadless_data'")
            if cursor.fetchone():
                self.db_available = True
                logger.info("Database initialized successfully")
            else:
                logger.warning("Table creation appeared to succeed but table not found - using JSON fallback")
            
            conn.close()
            
        except sqlite3.OperationalError as e:
            logger.warning(f"Database unavailable ({e}) - will save results to JSON file: {self.json_results_path}")
    
    def _get_processed_parks(self) -> set:
        """Get set of park IDs that have already been processed"""
        processed = set()
        
        # Check database
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT park_id FROM osm_roadless_data 
                WHERE error_message IS NULL
            """)
            processed.update(row[0] for row in cursor.fetchall())
            conn.close()
        except sqlite3.OperationalError:
            pass
        
        # Also check JSON file
        json_results = self._load_json_results()
        for park_id, result in json_results.items():
            if result.get('error_message') is None:
                processed.add(park_id)
        
        return processed
    
    def _get_park_bounding_box(self, park: Dict) -> Tuple[float, float, float, float]:
        """Get bounding box for a park (min_lon, min_lat, max_lon, max_lat)"""
        geom = park.get('geometry')
        if not geom:
            # Fall back to coordinates with buffer
            coords = park.get('coordinates', {})
            lat, lon = coords.get('lat', 0), coords.get('lon', 0)
            # Rough estimate: 1 degree ~ 111 km
            area_km2 = park.get('area_km2', 1000)
            radius_deg = (area_km2 ** 0.5) / 111 / 2 * 1.5  # 1.5x buffer
            return (lon - radius_deg, lat - radius_deg, 
                    lon + radius_deg, lat + radius_deg)
        
        park_shape = shape(geom)
        bounds = park_shape.bounds  # (minx, miny, maxx, maxy)
        
        # Add small buffer for roads near boundary
        buffer_deg = 0.01  # ~1km at equator
        return (
            bounds[0] - buffer_deg,
            bounds[1] - buffer_deg, 
            bounds[2] + buffer_deg,
            bounds[3] + buffer_deg
        )
    
    def _query_overpass(self, bbox: Tuple[float, float, float, float], retries: int = 3) -> Optional[Dict]:
        """Query Overpass API for roads within bounding box with retries"""
        min_lon, min_lat, max_lon, max_lat = bbox
        
        # Build highway filter
        highway_filter = '|'.join(self.road_types)
        
        # Overpass query - get ways with their geometry
        query = f"""
        [out:json][timeout:180];
        (
          way["highway"~"^({highway_filter})$"]
            ({min_lat},{min_lon},{max_lat},{max_lon});
        );
        out geom;
        """
        
        for attempt in range(retries):
            # Rate limiting - be gentle
            elapsed = time.time() - self.last_request_time
            if elapsed < self.min_request_interval:
                time.sleep(self.min_request_interval - elapsed)
            
            try:
                logger.info(f"  Querying Overpass API (attempt {attempt + 1}/{retries})...")
                response = requests.post(
                    OVERPASS_URL,
                    data={'data': query},
                    timeout=240  # 4 minute timeout
                )
                self.last_request_time = time.time()
                
                if response.status_code == 429:
                    # Rate limited - exponential backoff
                    wait_time = 60 * (2 ** attempt)  # 60, 120, 240 seconds
                    logger.warning(f"Rate limited (429), waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                    continue
                
                if response.status_code == 504:
                    # Gateway timeout - try again
                    wait_time = 30 * (attempt + 1)
                    logger.warning(f"Gateway timeout (504), waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                    continue
                
                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.Timeout:
                wait_time = 30 * (attempt + 1)
                logger.warning(f"Request timeout, waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
                continue
            except requests.exceptions.RequestException as e:
                logger.error(f"Overpass API error: {e}")
                if attempt < retries - 1:
                    wait_time = 30 * (attempt + 1)
                    logger.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
                return None
        
        logger.error("All Overpass API retries exhausted")
        return None
    
    def _osm_to_linestrings(self, osm_data: Dict) -> List[LineString]:
        """Convert OSM response to list of LineStrings"""
        lines = []
        
        for element in osm_data.get('elements', []):
            if element.get('type') != 'way':
                continue
            
            geom = element.get('geometry', [])
            if len(geom) < 2:
                continue
            
            coords = [(pt['lon'], pt['lat']) for pt in geom]
            try:
                line = LineString(coords)
                if line.is_valid and not line.is_empty:
                    lines.append(line)
            except Exception:
                continue
        
        return lines
    
    def _create_utm_transformer(self, lon: float, lat: float):
        """Create transformer to local UTM zone for accurate area calculations"""
        # Determine UTM zone
        utm_zone = int((lon + 180) / 6) + 1
        hemisphere = 'north' if lat >= 0 else 'south'
        
        if hemisphere == 'north':
            utm_crs = f"EPSG:{32600 + utm_zone}"
        else:
            utm_crs = f"EPSG:{32700 + utm_zone}"
        
        to_utm = pyproj.Transformer.from_crs(
            "EPSG:4326", utm_crs, always_xy=True
        )
        from_utm = pyproj.Transformer.from_crs(
            utm_crs, "EPSG:4326", always_xy=True
        )
        
        return to_utm, from_utm
    
    def _transform_geometry(self, geom, transformer):
        """Transform geometry using a pyproj transformer"""
        from shapely.ops import transform as shapely_transform
        return shapely_transform(transformer.transform, geom)
    
    def analyze_park(self, park: Dict) -> Dict:
        """Analyze roadless wilderness for a single park"""
        park_id = park['id']
        park_name = park.get('name', park_id)
        
        logger.info(f"Analyzing {park_id} ({park_name})")
        
        result = {
            'park_id': park_id,
            'total_area_km2': None,
            'roaded_area_km2': None,
            'roadless_area_km2': None,
            'roadless_percentage': None,
            'road_length_km': None,
            'road_density_km_per_km2': None,
            'buffer_distance_m': self.buffer_m,
            'road_types_used': ','.join(self.road_types),
            'osm_query_timestamp': datetime.now(timezone.utc).isoformat(),
            'error_message': None
        }
        
        # Get park geometry
        geom_data = park.get('geometry')
        if not geom_data:
            result['error_message'] = "No geometry available"
            return result
        
        try:
            park_shape = shape(geom_data)
            if not park_shape.is_valid:
                park_shape = park_shape.buffer(0)  # Fix invalid geometry
        except Exception as e:
            result['error_message'] = f"Invalid geometry: {e}"
            return result
        
        # Get centroid for UTM zone
        centroid = park_shape.centroid
        
        # Create UTM transformer
        try:
            to_utm, from_utm = self._create_utm_transformer(
                centroid.x, centroid.y
            )
        except Exception as e:
            result['error_message'] = f"Transformer error: {e}"
            return result
        
        # Transform park to UTM
        park_utm = self._transform_geometry(park_shape, to_utm)
        total_area_m2 = park_utm.area
        total_area_km2 = total_area_m2 / 1_000_000
        result['total_area_km2'] = round(total_area_km2, 2)
        
        # Query roads from OSM
        bbox = self._get_park_bounding_box(park)
        osm_data = self._query_overpass(bbox)
        
        if osm_data is None:
            result['error_message'] = "Failed to query Overpass API"
            return result
        
        # Convert to LineStrings
        road_lines = self._osm_to_linestrings(osm_data)
        logger.info(f"  Found {len(road_lines)} road segments")
        
        if len(road_lines) == 0:
            # No roads = 100% roadless
            result['roaded_area_km2'] = 0.0
            result['roadless_area_km2'] = round(total_area_km2, 2)
            result['roadless_percentage'] = 100.0
            result['road_length_km'] = 0.0
            result['road_density_km_per_km2'] = 0.0
            return result
        
        # Combine all roads
        try:
            all_roads = unary_union(road_lines)
        except Exception as e:
            result['error_message'] = f"Failed to union roads: {e}"
            return result
        
        # Transform roads to UTM
        roads_utm = self._transform_geometry(all_roads, to_utm)
        
        # Calculate total road length
        if isinstance(roads_utm, MultiLineString):
            road_length_m = sum(line.length for line in roads_utm.geoms)
        else:
            road_length_m = roads_utm.length
        road_length_km = road_length_m / 1000
        result['road_length_km'] = round(road_length_km, 2)
        
        # Clip roads to park boundary first (only count roads inside park)
        try:
            roads_in_park = roads_utm.intersection(park_utm)
            if roads_in_park.is_empty:
                # No roads inside park
                result['roaded_area_km2'] = 0.0
                result['roadless_area_km2'] = round(total_area_km2, 2)
                result['roadless_percentage'] = 100.0
                result['road_length_km'] = 0.0
                result['road_density_km_per_km2'] = 0.0
                return result
        except Exception:
            # If intersection fails, use all roads
            roads_in_park = roads_utm
        
        # Create buffer around roads
        try:
            road_buffer = roads_utm.buffer(self.buffer_m)
        except Exception as e:
            result['error_message'] = f"Failed to buffer roads: {e}"
            return result
        
        # Clip buffer to park boundary
        try:
            roaded_area = road_buffer.intersection(park_utm)
        except Exception as e:
            result['error_message'] = f"Failed to clip buffer: {e}"
            return result
        
        # Calculate areas
        roaded_area_m2 = roaded_area.area if not roaded_area.is_empty else 0
        roaded_area_km2 = roaded_area_m2 / 1_000_000
        roadless_area_km2 = total_area_km2 - roaded_area_km2
        roadless_percentage = (roadless_area_km2 / total_area_km2) * 100 if total_area_km2 > 0 else 0
        
        # Road density
        road_density = road_length_km / total_area_km2 if total_area_km2 > 0 else 0
        
        result['roaded_area_km2'] = round(roaded_area_km2, 2)
        result['roadless_area_km2'] = round(roadless_area_km2, 2)
        result['roadless_percentage'] = round(roadless_percentage, 2)
        result['road_density_km_per_km2'] = round(road_density, 4)
        
        logger.info(f"  Total: {total_area_km2:.0f} km², Roadless: {roadless_percentage:.1f}%")
        
        return result
    
    def _load_json_results(self) -> Dict:
        """Load existing results from JSON file"""
        if self.json_results_path.exists():
            try:
                with open(self.json_results_path) as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}
    
    def _save_json_results(self, results: Dict):
        """Save results to JSON file"""
        with open(self.json_results_path, 'w') as f:
            json.dump(results, f, indent=2)
    
    def _save_progress(self, current: int, total: int, last_park: str):
        """Save progress to file for monitoring"""
        progress = {
            'current': current,
            'total': total,
            'last_park': last_park,
            'percentage': round(current / total * 100, 1) if total > 0 else 0,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        try:
            with open(self.progress_file, 'w') as f:
                json.dump(progress, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save progress: {e}")
    
    def save_result(self, result: Dict):
        """Save analysis result to database or JSON file"""
        if self.db_available:
            try:
                conn = sqlite3.connect(self.db_path, timeout=5)
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT OR REPLACE INTO osm_roadless_data (
                        park_id, total_area_km2, roaded_area_km2, roadless_area_km2,
                        roadless_percentage, road_length_km, road_density_km_per_km2,
                        buffer_distance_m, road_types_used, osm_query_timestamp,
                        processed_at, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                """, (
                    result['park_id'],
                    result['total_area_km2'],
                    result['roaded_area_km2'],
                    result['roadless_area_km2'],
                    result['roadless_percentage'],
                    result['road_length_km'],
                    result['road_density_km_per_km2'],
                    result['buffer_distance_m'],
                    result['road_types_used'],
                    result['osm_query_timestamp'],
                    result['error_message']
                ))
                
                conn.commit()
                conn.close()
                return
                
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower():
                    logger.warning(f"Database locked, saving {result['park_id']} to JSON")
                    self.db_available = False
                else:
                    raise
        
        # Fallback: save to JSON file
        all_results = self._load_json_results()
        result['processed_at'] = datetime.now(timezone.utc).isoformat()
        all_results[result['park_id']] = result
        self._save_json_results(all_results)
        logger.info(f"  Saved to {self.json_results_path}")
    
    def run_analysis(self, park_id: str = None, limit: int = None, 
                     skip_processed: bool = True):
        """Run roadless analysis on parks"""
        if not HAS_GEO:
            logger.error("Missing required libraries (shapely, pyproj)")
            return
        
        parks = self.keystones
        
        # Filter to specific park if requested
        if park_id:
            parks = [p for p in parks if p['id'] == park_id]
            if not parks:
                logger.error(f"Park not found: {park_id}")
                return
        
        # Skip already processed parks
        if skip_processed:
            processed = self._get_processed_parks()
            parks = [p for p in parks if p['id'] not in processed]
            logger.info(f"Skipping {len(processed)} already processed parks")
        
        # Apply limit
        if limit:
            parks = parks[:limit]
        
        logger.info(f"Analyzing {len(parks)} parks")
        logger.info(f"Estimated time: {len(parks) * self.park_sleep_interval / 3600:.1f} hours (with {self.park_sleep_interval}s sleep between parks)")
        
        results = []
        self._save_progress(0, len(parks), "starting")
        for i, park in enumerate(parks, 1):
            logger.info(f"Progress: {i}/{len(parks)}")
            
            try:
                result = self.analyze_park(park)
                self.save_result(result)
                results.append(result)
                
                if result['error_message']:
                    logger.warning(f"  Error: {result['error_message']}")
                    
            except Exception as e:
                logger.error(f"Failed to analyze {park['id']}: {e}")
                # Save error result
                error_result = {
                    'park_id': park['id'],
                    'total_area_km2': None,
                    'roaded_area_km2': None,
                    'roadless_area_km2': None,
                    'roadless_percentage': None,
                    'road_length_km': None,
                    'road_density_km_per_km2': None,
                    'buffer_distance_m': self.buffer_m,
                    'road_types_used': ','.join(self.road_types),
                    'osm_query_timestamp': datetime.now(timezone.utc).isoformat(),
                    'error_message': str(e)
                }
                self.save_result(error_result)
            
            # Be nice to Overpass API - 90 second sleep between parks
            # 162 parks × 90s = ~4 hours total
            if i < len(parks):
                logger.info(f"  Sleeping {self.park_sleep_interval}s before next park...")
                self._save_progress(i, len(parks), park['id'])
                time.sleep(self.park_sleep_interval)
        
        # Summary
        successful = [r for r in results if r['error_message'] is None]
        if successful:
            avg_roadless = sum(r['roadless_percentage'] or 0 for r in successful) / len(successful)
            logger.info(f"\nCompleted {len(successful)}/{len(results)} parks")
            logger.info(f"Average roadless percentage: {avg_roadless:.1f}%")
        
        self._save_progress(len(parks), len(parks), "completed")
        return results


def print_summary():
    """Print summary of roadless analysis results"""
    results = []
    
    # Try to load from database
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT park_id, roadless_percentage, total_area_km2, error_message
            FROM osm_roadless_data
        """)
        for row in cursor.fetchall():
            results.append({
                'park_id': row[0],
                'roadless_percentage': row[1],
                'total_area_km2': row[2],
                'error_message': row[3]
            })
        conn.close()
    except sqlite3.OperationalError:
        pass
    
    # Also load from JSON file
    json_path = DATA_DIR / "osm_roadless_results.json"
    if json_path.exists():
        try:
            with open(json_path) as f:
                json_data = json.load(f)
            # Add JSON results (avoid duplicates)
            existing_ids = {r['park_id'] for r in results}
            for park_id, data in json_data.items():
                if park_id not in existing_ids:
                    results.append(data)
        except Exception:
            pass
    
    if not results:
        print("No roadless analysis data found")
        return
    
    successful = [r for r in results if r.get('error_message') is None]
    
    print(f"\n=== Roadless Analysis Summary ===")
    print(f"Total parks processed: {len(results)}")
    print(f"Successful: {len(successful)}")
    
    if successful:
        roadless_vals = [r['roadless_percentage'] for r in successful if r.get('roadless_percentage') is not None]
        if roadless_vals:
            avg_roadless = sum(roadless_vals) / len(roadless_vals)
            print(f"Average roadless: {avg_roadless:.1f}%")
            print(f"Range: {min(roadless_vals):.1f}% - {max(roadless_vals):.1f}%")
            
            # Top 10 most roadless
            sorted_parks = sorted(successful, 
                                  key=lambda x: x.get('roadless_percentage') or 0, 
                                  reverse=True)
            print(f"\nTop 10 Most Roadless:")
            for r in sorted_parks[:10]:
                pct = r.get('roadless_percentage', 0)
                area = r.get('total_area_km2', 0)
                print(f"  {r['park_id']}: {pct:.1f}% ({area:.0f} km²)")


def main():
    parser = argparse.ArgumentParser(
        description='Analyze roadless wilderness using OpenStreetMap data'
    )
    parser.add_argument(
        '--park', '-p',
        help='Analyze specific park by ID'
    )
    parser.add_argument(
        '--limit', '-l', type=int,
        help='Limit number of parks to process'
    )
    parser.add_argument(
        '--no-skip', action='store_true',
        help='Re-process already completed parks'
    )
    parser.add_argument(
        '--summary', '-s', action='store_true',
        help='Print summary of existing results'
    )
    parser.add_argument(
        '--include-tracks', action='store_true',
        help='Include track roads in analysis (more conservative)'
    )
    parser.add_argument(
        '--buffer', '-b', type=int, default=ROAD_BUFFER_M,
        help=f'Road buffer distance in meters (default: {ROAD_BUFFER_M})'
    )
    
    args = parser.parse_args()
    
    if args.summary:
        print_summary()
        return
    
    # Configure road types
    road_types = ROAD_TYPES.copy()
    if args.include_tracks:
        road_types.append('track')
    
    analyzer = OSMRoadlessAnalyzer(
        road_types=road_types,
        buffer_m=args.buffer
    )
    
    analyzer.run_analysis(
        park_id=args.park,
        limit=args.limit,
        skip_processed=not args.no_skip
    )
    
    print_summary()


if __name__ == '__main__':
    main()
