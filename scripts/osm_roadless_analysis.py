#!/usr/bin/env python3
"""
OSM Roadless Wilderness Analysis for 5MP Conservation Globe

Memory-efficient version that:
- Processes roads in chunks
- Uses simplified geometries
- Stores road data as JSON in the database
- Cleans up memory after each park

Usage:
    python scripts/osm_roadless_analysis.py [--park PARK_ID] [--limit N]
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
    from shapely.geometry import shape, LineString, MultiLineString, mapping
    from shapely.ops import unary_union
    import pyproj
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

# Road types to include
ROAD_TYPES = [
    'motorway', 'motorway_link',
    'trunk', 'trunk_link', 
    'primary', 'primary_link',
    'secondary', 'secondary_link',
    'tertiary', 'tertiary_link',
    'unclassified',
    'residential',
    'service',
]

# Buffer distance in meters
ROAD_BUFFER_M = 1000

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OSMRoadlessAnalyzer:
    """Memory-efficient roadless wilderness analyzer"""
    
    def __init__(self, db_path=DB_PATH, road_types=None, buffer_m=ROAD_BUFFER_M):
        self.db_path = db_path
        self.road_types = road_types or ROAD_TYPES
        self.buffer_m = buffer_m
        self.keystones = self._load_keystones()
        self._init_db()
        
        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 5
        self.park_sleep_interval = 30
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
        """Initialize database tables"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        
        # Add roads_json column if not exists
        cursor.execute("""CREATE TABLE IF NOT EXISTS osm_roadless_data (
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
            roads_json TEXT,
            buffer_roads_json TEXT,
            osm_query_timestamp DATETIME,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            error_message TEXT
        )""")
        
        # Try to add new columns if table exists but columns don't
        try:
            cursor.execute("ALTER TABLE osm_roadless_data ADD COLUMN roads_json TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE osm_roadless_data ADD COLUMN buffer_roads_json TEXT")
        except sqlite3.OperationalError:
            pass
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_osm_roadless_park_id ON osm_roadless_data(park_id)")
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    
    def _get_processed_parks(self) -> set:
        """Get set of already processed park IDs"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()
            cursor.execute("SELECT park_id FROM osm_roadless_data WHERE error_message IS NULL")
            result = set(row[0] for row in cursor.fetchall())
            conn.close()
            return result
        except Exception:
            return set()
    
    def _get_park_bbox(self, park: Dict) -> Tuple[float, float, float, float]:
        """Get bounding box (min_lon, min_lat, max_lon, max_lat)"""
        geom = park.get('geometry')
        if not geom:
            coords = park.get('coordinates', {})
            lat, lon = coords.get('lat', 0), coords.get('lon', 0)
            area_km2 = park.get('area_km2', 1000)
            radius_deg = (area_km2 ** 0.5) / 111 / 2 * 1.5
            return (lon - radius_deg, lat - radius_deg, lon + radius_deg, lat + radius_deg)
        
        park_shape = shape(geom)
        bounds = park_shape.bounds
        buffer_deg = 0.1  # 10km buffer for roads near boundary
        return (bounds[0] - buffer_deg, bounds[1] - buffer_deg, 
                bounds[2] + buffer_deg, bounds[3] + buffer_deg)
    
    def _query_overpass(self, bbox: Tuple[float, float, float, float], retries: int = 3) -> Optional[Dict]:
        """Query Overpass API for roads"""
        min_lon, min_lat, max_lon, max_lat = bbox
        
        # Build highway filter
        highway_filter = '|'.join(self.road_types)
        
        # Overpass query - get ways only, not full geometry
        query = f"""
        [out:json][timeout:180];
        (
          way["highway"~"^({highway_filter})$"]({min_lat},{min_lon},{max_lat},{max_lon});
        );
        out geom;
        """
        
        for attempt in range(1, retries + 1):
            # Rate limit
            elapsed = time.time() - self.last_request_time
            if elapsed < self.min_request_interval:
                time.sleep(self.min_request_interval - elapsed)
            
            logger.info(f"  Querying Overpass API (attempt {attempt}/{retries})...")
            
            try:
                self.last_request_time = time.time()
                resp = requests.post(
                    OVERPASS_URL,
                    data={'data': query},
                    timeout=200
                )
                
                if resp.status_code == 429:  # Too many requests
                    logger.warning("  Rate limited, waiting 60s...")
                    time.sleep(60)
                    continue
                
                resp.raise_for_status()
                return resp.json()
                
            except requests.exceptions.Timeout:
                logger.warning(f"  Timeout on attempt {attempt}")
                time.sleep(30)
            except requests.exceptions.RequestException as e:
                logger.warning(f"  Request error: {e}")
                time.sleep(10)
        
        return None
    
    def _extract_roads_simplified(self, osm_data: Dict, park_shape, buffer_shape) -> Tuple[List, List, float]:
        """
        Extract roads as simplified GeoJSON, separating inside park vs buffer.
        Returns (roads_inside, roads_buffer, total_length_km)
        """
        roads_inside = []
        roads_buffer = []
        total_length_km = 0
        
        for element in osm_data.get('elements', []):
            if element.get('type') != 'way':
                continue
            
            geom = element.get('geometry', [])
            if len(geom) < 2:
                continue
            
            coords = [(pt['lon'], pt['lat']) for pt in geom]
            
            try:
                line = LineString(coords)
                
                # Simplify to reduce memory (tolerance ~100m in degrees)
                line_simple = line.simplify(0.001, preserve_topology=True)
                
                # Use geodesic length
                geod = pyproj.Geod(ellps='WGS84')
                length_m = geod.geometry_length(line)
                total_length_km += length_m / 1000
                
                # Classify road
                road_data = {
                    'type': element.get('tags', {}).get('highway', 'unknown'),
                    'coords': list(line_simple.coords),
                    'length_km': round(length_m / 1000, 3)
                }
                
                if park_shape.intersects(line):
                    roads_inside.append(road_data)
                elif buffer_shape.intersects(line):
                    roads_buffer.append(road_data)
                    
            except Exception:
                continue
        
        return roads_inside, roads_buffer, total_length_km
    
    def _calculate_roaded_area_chunked(self, roads: List[Dict], park_shape, utm_crs: str) -> float:
        """
        Calculate roaded area in chunks to save memory.
        Returns roaded area in km².
        """
        if not roads:
            return 0.0
        
        from shapely.geometry import Polygon
        
        # Project to UTM
        transformer_to_utm = pyproj.Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)
        
        # Project park (handle both Polygon and MultiPolygon)
        from shapely.geometry import MultiPolygon
        
        def project_polygon(poly, transformer):
            exterior_coords = [transformer.transform(x, y) for x, y in poly.exterior.coords]
            interior_coords = [[transformer.transform(x, y) for x, y in ring.coords] for ring in poly.interiors]
            return Polygon(exterior_coords, interior_coords)
        
        if isinstance(park_shape, MultiPolygon):
            projected_polys = [project_polygon(poly, transformer_to_utm) for poly in park_shape.geoms]
            park_utm = MultiPolygon(projected_polys)
        else:
            park_utm = project_polygon(park_shape, transformer_to_utm)
        
        # Process roads in chunks of 100
        chunk_size = 100
        total_roaded = None
        
        for i in range(0, len(roads), chunk_size):
            chunk = roads[i:i+chunk_size]
            
            buffers = []
            for road in chunk:
                try:
                    coords = road['coords']
                    utm_coords = [transformer_to_utm.transform(x, y) for x, y in coords]
                    line_utm = LineString(utm_coords)
                    buffers.append(line_utm.buffer(self.buffer_m))
                except Exception:
                    continue
            
            if buffers:
                chunk_union = unary_union(buffers)
                chunk_clipped = chunk_union.intersection(park_utm)
                
                if total_roaded is None:
                    total_roaded = chunk_clipped
                else:
                    total_roaded = total_roaded.union(chunk_clipped)
            
            # Clean up
            del buffers
            gc.collect()
        
        if total_roaded is None or total_roaded.is_empty:
            return 0.0
        
        return total_roaded.area / 1_000_000  # m² to km²
    
    def analyze_park(self, park: Dict) -> Dict:
        """Analyze a single park for roadless wilderness"""
        park_id = park['id']
        park_name = park.get('name', park_id)
        logger.info(f"Analyzing {park_id} ({park_name})")
        
        result = {
            'park_id': park_id,
            'total_area_km2': park.get('area_km2'),
            'roaded_area_km2': None,
            'roadless_area_km2': None,
            'roadless_percentage': None,
            'road_length_km': None,
            'road_density_km_per_km2': None,
            'buffer_distance_m': self.buffer_m,
            'road_types_used': ','.join(self.road_types),
            'roads_json': None,
            'buffer_roads_json': None,
            'osm_query_timestamp': datetime.now(timezone.utc).isoformat(),
            'error_message': None
        }
        
        geom = park.get('geometry')
        if not geom:
            result['error_message'] = "No geometry available"
            return result
        
        try:
            park_shape = shape(geom)
            if not park_shape.is_valid:
                park_shape = park_shape.buffer(0)
        except Exception as e:
            result['error_message'] = f"Invalid geometry: {e}"
            return result
        
        # Create buffer zone (10km around park)
        # Rough conversion: 0.1 degrees ~ 10km
        buffer_shape = park_shape.buffer(0.1)
        
        # Get bounding box and query roads
        bbox = self._get_park_bbox(park)
        osm_data = self._query_overpass(bbox)
        
        if not osm_data:
            result['error_message'] = "Failed to query Overpass API"
            return result
        
        # Extract and classify roads
        roads_inside, roads_buffer, total_length_km = self._extract_roads_simplified(
            osm_data, park_shape, buffer_shape
        )
        
        logger.info(f"  Found {len(roads_inside)} roads inside, {len(roads_buffer)} in buffer")
        
        # Store road data as JSON (limit size)
        if len(roads_inside) <= 500:
            result['roads_json'] = json.dumps(roads_inside)
        else:
            # Store summary for large parks
            result['roads_json'] = json.dumps({
                'count': len(roads_inside),
                'sample': roads_inside[:50],
                'total_length_km': sum(r['length_km'] for r in roads_inside)
            })
        
        if len(roads_buffer) <= 500:
            result['buffer_roads_json'] = json.dumps(roads_buffer)
        else:
            result['buffer_roads_json'] = json.dumps({
                'count': len(roads_buffer),
                'sample': roads_buffer[:50],
                'total_length_km': sum(r['length_km'] for r in roads_buffer)
            })
        
        result['road_length_km'] = round(total_length_km, 2)
        
        # Calculate roaded area
        total_area_km2 = park.get('area_km2') or (park_shape.area * 12321)  # rough deg² to km²
        result['total_area_km2'] = round(total_area_km2, 2)
        
        # Determine UTM zone
        centroid = park_shape.centroid
        utm_zone = int((centroid.x + 180) / 6) + 1
        hemisphere = 'north' if centroid.y >= 0 else 'south'
        utm_crs = f"EPSG:{32600 + utm_zone if hemisphere == 'north' else 32700 + utm_zone}"
        
        try:
            roaded_area_km2 = self._calculate_roaded_area_chunked(roads_inside, park_shape, utm_crs)
            result['roaded_area_km2'] = round(roaded_area_km2, 2)
            result['roadless_area_km2'] = round(total_area_km2 - roaded_area_km2, 2)
            result['roadless_percentage'] = round((total_area_km2 - roaded_area_km2) / total_area_km2 * 100, 1) if total_area_km2 > 0 else 0
            result['road_density_km_per_km2'] = round(total_length_km / total_area_km2, 4) if total_area_km2 > 0 else 0
            
            logger.info(f"  Roadless: {result['roadless_percentage']}% ({result['roadless_area_km2']} km²)")
            
        except Exception as e:
            result['error_message'] = f"Area calculation failed: {e}"
            logger.warning(f"  Error: {e}")
        
        # Clean up
        del osm_data, roads_inside, roads_buffer
        gc.collect()
        
        return result
    
    def save_result(self, result: Dict):
        """Save result to database"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO osm_roadless_data (
                    park_id, total_area_km2, roaded_area_km2, roadless_area_km2,
                    roadless_percentage, road_length_km, road_density_km_per_km2,
                    buffer_distance_m, road_types_used, roads_json, buffer_roads_json,
                    osm_query_timestamp, processed_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
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
                result['roads_json'],
                result['buffer_roads_json'],
                result['osm_query_timestamp'],
                result['error_message']
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to save result: {e}")
    
    def _save_progress(self, current: int, total: int, last_park: str):
        """Save progress for monitoring"""
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
        except Exception:
            pass
    
    def run_analysis(self, park_id: str = None, limit: int = None, skip_processed: bool = True):
        """Run roadless analysis on parks"""
        if not HAS_GEO:
            logger.error("Missing required libraries (shapely, pyproj)")
            return
        
        parks = self.keystones
        
        if park_id:
            parks = [p for p in parks if p['id'] == park_id]
            if not parks:
                logger.error(f"Park not found: {park_id}")
                return
        
        if skip_processed:
            processed = self._get_processed_parks()
            original_count = len(parks)
            parks = [p for p in parks if p['id'] not in processed]
            logger.info(f"Skipping {original_count - len(parks)} already processed parks")
        
        if limit:
            parks = parks[:limit]
        
        logger.info(f"Analyzing {len(parks)} parks")
        logger.info(f"Estimated time: {len(parks) * self.park_sleep_interval / 3600:.1f} hours")
        
        self._save_progress(0, len(parks), "starting")
        
        for i, park in enumerate(parks, 1):
            logger.info(f"Progress: {i}/{len(parks)}")
            
            try:
                result = self.analyze_park(park)
                self.save_result(result)
                self._save_progress(i, len(parks), park['id'])
                
            except Exception as e:
                logger.error(f"Failed to analyze {park['id']}: {e}")
                self.save_result({
                    'park_id': park['id'],
                    'total_area_km2': None,
                    'roaded_area_km2': None,
                    'roadless_area_km2': None,
                    'roadless_percentage': None,
                    'road_length_km': None,
                    'road_density_km_per_km2': None,
                    'buffer_distance_m': self.buffer_m,
                    'road_types_used': ','.join(self.road_types),
                    'roads_json': None,
                    'buffer_roads_json': None,
                    'osm_query_timestamp': datetime.now(timezone.utc).isoformat(),
                    'error_message': str(e)
                })
            
            # Force garbage collection
            gc.collect()
            
            # Sleep between parks
            if i < len(parks):
                time.sleep(self.park_sleep_interval)
        
        logger.info("Analysis complete")


def main():
    parser = argparse.ArgumentParser(description='OSM Roadless Analysis')
    parser.add_argument('--park', type=str, help='Analyze specific park')
    parser.add_argument('--limit', type=int, help='Limit number of parks')
    parser.add_argument('--no-skip', action='store_true', help='Re-analyze already processed parks')
    args = parser.parse_args()
    
    analyzer = OSMRoadlessAnalyzer()
    analyzer.run_analysis(
        park_id=args.park,
        limit=args.limit,
        skip_processed=not args.no_skip
    )


if __name__ == '__main__':
    main()
