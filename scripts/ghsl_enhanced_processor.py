#!/usr/bin/env python3
"""
GHSL Enhanced Processor - Settlement Detection with Population

Reads GHSL data directly from ZIP file, combines built-up surface with
population estimates, and stores settlement locations with GPS coordinates.

Features:
- Reads TIF files from ZIP without full extraction (memory efficient)
- Combines BUILT_S (built-up surface) with POP (population) data
- Detects settlement clusters and estimates households
- Uses local osm_places table for nearby place lookups (no API calls)
- Calculates bearing/direction from nearest place
- One tile at a time for memory efficiency

Output format:
  "Building cluster 150m², ~16 people, 50 km north-northeast of Yalinga"

Usage:
    source .venv/bin/activate
    python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
    python scripts/ghsl_enhanced_processor.py --park CAF_Chinko --dry-run
"""

import json
import sqlite3
import zipfile
import argparse
import logging
import math
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import numpy as np

try:
    from pyproj import Transformer
    import rasterio
    from rasterio.mask import mask
    from rasterio.io import MemoryFile
    from shapely.geometry import shape, Point, mapping
    from shapely.ops import transform
    from scipy import ndimage
except ImportError as e:
    print(f"Missing: {e}. Run: pip install pyproj rasterio shapely scipy")
    exit(1)

# Configuration
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_PATH = BASE_DIR / "data" / "keystones_with_boundaries.json"
GHSL_ZIP_PATH = BASE_DIR / "data" / "ghsl_examples.zip"

# GHSL grid parameters (Mollweide projection ESRI:54009)
TILE_SIZE_M = 1000000  # 1000km tiles
GRID_ORIGIN_X = -18041000
GRID_ORIGIN_Y = 9000000

# Detection parameters
MIN_BUILT_UP_M2 = 100  # Minimum m² to count as built-up
MIN_CLUSTER_PIXELS = 3  # Minimum pixels for a settlement cluster
HOUSEHOLD_SIZE = 5.2  # Average people per household in Africa
BUILDING_SIZE_M2 = 50  # Average building footprint

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Cardinal direction names (16-point compass)
CARDINAL_DIRECTIONS = [
    "north", "north-northeast", "northeast", "east-northeast",
    "east", "east-southeast", "southeast", "south-southeast",
    "south", "south-southwest", "southwest", "west-southwest",
    "west", "west-northwest", "northwest", "north-northwest"
]


def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate bearing from point 1 to point 2.
    Returns bearing in degrees (0-360, where 0=north, 90=east).
    """
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlon_rad = math.radians(lon2 - lon1)
    
    x = math.sin(dlon_rad) * math.cos(lat2_rad)
    y = math.cos(lat1_rad) * math.sin(lat2_rad) - \
        math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dlon_rad)
    
    bearing = math.atan2(x, y)
    bearing_deg = math.degrees(bearing)
    return (bearing_deg + 360) % 360


def bearing_to_cardinal(bearing: float) -> str:
    """
    Convert bearing (0-360) to cardinal direction string.
    Uses 16-point compass (e.g., "north-northeast").
    """
    # Each direction covers 22.5 degrees
    index = int((bearing + 11.25) / 22.5) % 16
    return CARDINAL_DIRECTIONS[index]


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in kilometers."""
    R = 6371  # Earth's radius in km
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    
    a = math.sin(dlat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    return R * c


def find_nearest_place(cursor, park_id: str, lat: float, lon: float) -> Optional[Dict]:
    """
    Find nearest place from osm_places table for a given park.
    Returns dict with name, distance_km, and direction.
    """
    # Use squared distance approximation for sorting (faster than haversine for ordering)
    cursor.execute('''
        SELECT name, lat, lon, place_type,
               (lat - ?) * (lat - ?) + (lon - ?) * (lon - ?) as dist_sq
        FROM osm_places 
        WHERE park_id = ?
        ORDER BY dist_sq
        LIMIT 1
    ''', (lat, lat, lon, lon, park_id))
    
    row = cursor.fetchone()
    if not row:
        return None
    
    place_name, place_lat, place_lon, place_type, _ = row
    
    # Calculate actual distance
    distance_km = haversine_distance(lat, lon, place_lat, place_lon)
    
    # Calculate bearing FROM the place TO the settlement
    # (so we can say "X km north of PlaceName")
    bearing = calculate_bearing(place_lat, place_lon, lat, lon)
    direction = bearing_to_cardinal(bearing)
    
    return {
        'name': place_name,
        'distance_km': distance_km,
        'direction': direction,
        'place_type': place_type
    }


class GHSLEnhancedProcessor:
    """Process GHSL data to detect settlements with population estimates"""
    
    def __init__(self, zip_path: Path = GHSL_ZIP_PATH):
        self.zip_path = zip_path
        self.wgs84_to_moll = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True)
        self.moll_to_wgs84 = Transformer.from_crs("ESRI:54009", "EPSG:4326", always_xy=True)
        self.keystones = self._load_keystones()
        self.tile_index = self._build_tile_index()
        self._init_db()
        
    def _load_keystones(self) -> List[Dict]:
        """Load parks with geometry"""
        with open(KEYSTONES_PATH) as f:
            return [p for p in json.load(f) if p.get('geometry')]
    
    def _build_tile_index(self) -> Dict[str, Dict]:
        """Index available tiles by type and location, with actual bounds"""
        index = {'BUILT_S_10m': {}, 'BUILT_S_100m': {}, 'POP_100m': {}, 'bounds': {}}
        
        with zipfile.ZipFile(self.zip_path, 'r') as zf:
            for name in zf.namelist():
                if not name.endswith('.tif') or name.startswith('__MACOSX'):
                    continue
                
                # Parse tile info from path
                parts = name.split('/')[0].split('_')
                
                try:
                    # Find row/col from filename
                    row = col = None
                    for p in parts:
                        if p.startswith('R') and p[1:].isdigit():
                            row = int(p[1:])
                        elif p.startswith('C') and p[1:].isdigit():
                            col = int(p[1:])
                    
                    if row is None or col is None:
                        continue
                    
                    key = f"R{row}_C{col}"
                    
                    # Determine product type and store path
                    if 'BUILT_S' in name and '_10_' in name:
                        index['BUILT_S_10m'][key] = name
                    elif 'BUILT_S' in name and '_100_' in name:
                        index['BUILT_S_100m'][key] = name
                        # Read actual bounds for 100m tiles (smaller files)
                        if key not in index['bounds']:
                            try:
                                with zf.open(name) as f:
                                    data = f.read()
                                with MemoryFile(data) as memfile:
                                    with memfile.open() as src:
                                        index['bounds'][key] = src.bounds
                            except:
                                pass
                    elif 'POP' in name:
                        index['POP_100m'][key] = name
                        
                except (ValueError, IndexError):
                    continue
        
        logger.info(f"Tile index: {len(index['BUILT_S_10m'])} BUILT_S_10m, "
                   f"{len(index['BUILT_S_100m'])} BUILT_S_100m, {len(index['POP_100m'])} POP_100m")
        return index
    
    def _init_db(self):
        """Create park_settlements table with required schema"""
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""CREATE TABLE IF NOT EXISTS park_settlements (
            id INTEGER PRIMARY KEY,
            park_id TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            area_m2 REAL,
            population_est INTEGER,
            households_est INTEGER,
            nearest_place TEXT,
            distance_to_place_km REAL,
            direction_from_place TEXT,
            settlement_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_settlements_park ON park_settlements(park_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_settlements_location ON park_settlements(lat, lon)")
        conn.commit()
        conn.close()
        logger.info("Database table park_settlements initialized")
    
    def get_tile_for_point(self, lon: float, lat: float) -> Tuple[int, int]:
        """Get tile row/col for a WGS84 point"""
        x, y = self.wgs84_to_moll.transform(lon, lat)
        col = int((x - GRID_ORIGIN_X) / TILE_SIZE_M)
        row = int((GRID_ORIGIN_Y - y) / TILE_SIZE_M)
        return row, col
    
    def get_tiles_for_park(self, park: Dict) -> List[str]:
        """Get all tile keys that overlap with a park"""
        from shapely.geometry import box
        
        geom = shape(park['geometry'])
        park_moll = transform(lambda x, y: self.wgs84_to_moll.transform(x, y), geom)
        
        overlapping = []
        for key, bounds in self.tile_index.get('bounds', {}).items():
            tile_box = box(*bounds)
            if park_moll.intersects(tile_box):
                overlapping.append(key)
        
        return overlapping
    
    def read_tif_from_zip(self, tif_path: str) -> Optional[Tuple[np.ndarray, dict]]:
        """Read a TIF file from the ZIP into memory"""
        try:
            with zipfile.ZipFile(self.zip_path, 'r') as zf:
                with zf.open(tif_path) as f:
                    data = f.read()
                    
            with MemoryFile(data) as memfile:
                with memfile.open() as src:
                    arr = src.read(1)
                    meta = {
                        'transform': src.transform,
                        'crs': src.crs,
                        'nodata': src.nodata,
                        'width': src.width,
                        'height': src.height,
                        'bounds': src.bounds
                    }
            return arr, meta
        except Exception as e:
            logger.error(f"Failed to read {tif_path}: {e}")
            return None
    
    def extract_park_data(self, park: Dict, built_arr: np.ndarray, built_meta: dict,
                          pop_arr: Optional[np.ndarray] = None, pop_meta: Optional[dict] = None
                         ) -> List[Dict]:
        """Extract settlement clusters for a park from raster data"""
        settlements = []
        
        # Get park geometry in Mollweide
        park_geom = shape(park['geometry'])
        park_moll = transform(lambda x, y: self.wgs84_to_moll.transform(x, y), park_geom)
        
        # Get raster bounds
        left, bottom, right, top = built_meta['bounds']
        transform_matrix = built_meta['transform']
        
        # Check if park overlaps with this tile
        from shapely.geometry import box
        tile_box = box(left, bottom, right, top)
        if not park_moll.intersects(tile_box):
            return []
        
        # Create mask for park area
        height, width = built_arr.shape
        nodata = built_meta.get('nodata', 0)
        
        # Find built-up pixels
        if nodata is not None:
            valid_mask = (built_arr != nodata) & (built_arr >= MIN_BUILT_UP_M2)
        else:
            valid_mask = built_arr >= MIN_BUILT_UP_M2
        
        # Label connected components (settlement clusters)
        labeled, num_features = ndimage.label(valid_mask)
        
        if num_features == 0:
            return []
        
        # Process each cluster
        for cluster_id in range(1, num_features + 1):
            cluster_mask = labeled == cluster_id
            pixel_count = np.sum(cluster_mask)
            
            if pixel_count < MIN_CLUSTER_PIXELS:
                continue
            
            # Get cluster centroid in pixel coordinates
            rows, cols = np.where(cluster_mask)
            center_row = int(np.mean(rows))
            center_col = int(np.mean(cols))
            
            # Convert to Mollweide coordinates
            x = left + center_col * transform_matrix.a
            y = top + center_row * transform_matrix.e  # e is negative
            
            # Convert to WGS84
            lon, lat = self.moll_to_wgs84.transform(x, y)
            
            # Check if centroid is in park (or buffer)
            point_moll = Point(x, y)
            in_park = park_moll.contains(point_moll)
            in_buffer = park_moll.buffer(10000).contains(point_moll)  # 10km buffer
            
            if not in_buffer:
                continue
            
            # Calculate area (sum of built-up m² in cluster)
            area_m2 = float(np.sum(built_arr[cluster_mask]))
            
            # Estimate population from POP layer if available
            population = 0
            if pop_arr is not None and pop_meta is not None:
                # POP is at 100m, BUILT might be at 10m - need to handle resolution difference
                # For simplicity, sample population at cluster centroid
                pop_transform = pop_meta['transform']
                pop_col = int((x - pop_meta['bounds'][0]) / pop_transform.a)
                pop_row = int((pop_meta['bounds'][3] - y) / abs(pop_transform.e))
                
                if 0 <= pop_row < pop_arr.shape[0] and 0 <= pop_col < pop_arr.shape[1]:
                    pop_val = pop_arr[pop_row, pop_col]
                    if pop_val > 0 and pop_val != pop_meta.get('nodata'):
                        population = float(pop_val) * pixel_count  # Scale by cluster size
            
            # Fallback: estimate from building area
            if population <= 0:
                buildings_est = area_m2 / BUILDING_SIZE_M2
                population = buildings_est * HOUSEHOLD_SIZE
            
            households = population / HOUSEHOLD_SIZE
            
            # Classify settlement type by area
            if area_m2 < 200:
                settlement_type = 'temporary'
            elif area_m2 < 1000:
                settlement_type = 'small'
            elif area_m2 < 5000:
                settlement_type = 'medium'
            else:
                settlement_type = 'large'
            
            settlements.append({
                'lat': lat,
                'lon': lon,
                'area_m2': area_m2,
                'population_est': int(round(population)),
                'households_est': int(round(households)),
                'settlement_type': settlement_type,
                'in_park': in_park  # Track for filtering if needed
            })
        
        return settlements
    
    def _format_settlement_description(self, settlement: Dict) -> str:
        """Format settlement as human-readable description."""
        area = settlement.get('area_m2', 0)
        pop = settlement.get('population_est', 0)
        
        desc = f"Building cluster {area:.0f}m², ~{pop} people"
        
        if settlement.get('nearest_place') and settlement.get('distance_to_place_km'):
            dist = settlement['distance_to_place_km']
            direction = settlement.get('direction_from_place', '')
            place = settlement['nearest_place']
            desc += f", {dist:.0f} km {direction} of {place}"
        
        return desc
    
    def process_park(self, park: Dict, dry_run: bool = False) -> int:
        """Process a single park, return number of settlements found"""
        park_id = park['id']
        logger.info(f"Processing {park_id}...")
        
        tile_keys = self.get_tiles_for_park(park)
        all_settlements = []
        
        for key in tile_keys:
            
            # Try to get built-up surface (prefer 100m for speed, fall back to 10m)
            built_path = self.tile_index['BUILT_S_100m'].get(key)
            if not built_path:
                built_path = self.tile_index['BUILT_S_10m'].get(key)
            
            if not built_path:
                logger.debug(f"No BUILT_S tile for {key}")
                continue
            
            # Read built-up data
            logger.info(f"Reading tile {key} from ZIP...")
            result = self.read_tif_from_zip(built_path)
            if result is None:
                continue
            built_arr, built_meta = result
            
            # Try to get population data
            pop_arr, pop_meta = None, None
            pop_path = self.tile_index['POP_100m'].get(key)
            if pop_path:
                pop_result = self.read_tif_from_zip(pop_path)
                if pop_result:
                    pop_arr, pop_meta = pop_result
            
            # Extract settlements
            settlements = self.extract_park_data(park, built_arr, built_meta, pop_arr, pop_meta)
            
            # Add tile info
            parts = key.split('_')
            tile_row = int(parts[0][1:]) if len(parts) >= 2 else None
            tile_col = int(parts[1][1:]) if len(parts) >= 2 else None
            for s in settlements:
                s['tile_row'] = tile_row
                s['tile_col'] = tile_col
            
            all_settlements.extend(settlements)
            
            # Clean up memory
            del built_arr, pop_arr
            
            logger.info(f"Found {len(settlements)} settlements in tile {key}")
        
        if not all_settlements:
            logger.info(f"No settlements found for {park_id}")
            return 0
        
        # Open database connection for both reading osm_places and writing settlements
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Find nearest place for each settlement using local osm_places table
        for s in all_settlements:
            place_info = find_nearest_place(cursor, park_id, s['lat'], s['lon'])
            if place_info:
                s['nearest_place'] = place_info['name']
                s['distance_to_place_km'] = place_info['distance_km']
                s['direction_from_place'] = place_info['direction']
        
        if dry_run:
            conn.close()
            logger.info(f"[DRY RUN] Would insert {len(all_settlements)} settlements for {park_id}")
            for s in all_settlements[:5]:
                desc = self._format_settlement_description(s)
                logger.info(f"  - {desc}")
            return len(all_settlements)
        
        # Insert into database
        inserted = 0
        for s in all_settlements:
            try:
                cursor.execute("""
                    INSERT INTO park_settlements 
                    (park_id, lat, lon, area_m2, population_est, households_est,
                     nearest_place, distance_to_place_km, direction_from_place, settlement_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    park_id, s['lat'], s['lon'], s['area_m2'],
                    s['population_est'], s['households_est'],
                    s.get('nearest_place'), s.get('distance_to_place_km'),
                    s.get('direction_from_place'), s['settlement_type']
                ))
                inserted += 1
            except sqlite3.IntegrityError:
                pass
        
        conn.commit()
        conn.close()
        
        logger.info(f"Inserted {inserted} settlements for {park_id}")
        return inserted
    
    def process_all_parks(self, dry_run: bool = False, limit: int = None):
        """Process all parks with available GHSL data"""
        total_settlements = 0
        parks_processed = 0
        
        for i, park in enumerate(self.keystones):
            if limit and i >= limit:
                break
            
            try:
                count = self.process_park(park, dry_run=dry_run)
                total_settlements += count
                parks_processed += 1
                
            except Exception as e:
                logger.error(f"Error processing {park['id']}: {e}")
                continue
        
        logger.info(f"Completed: {parks_processed} parks, {total_settlements} settlements")
        return total_settlements


def main():
    parser = argparse.ArgumentParser(description='GHSL Enhanced Processor')
    parser.add_argument('--park', help='Process single park by ID')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    parser.add_argument('--limit', type=int, help='Limit number of parks to process')
    parser.add_argument('--zip', default=str(GHSL_ZIP_PATH), help='Path to GHSL ZIP file')
    args = parser.parse_args()
    
    processor = GHSLEnhancedProcessor(Path(args.zip))
    
    if args.park:
        # Find specific park
        park = next((p for p in processor.keystones if p['id'] == args.park), None)
        if not park:
            logger.error(f"Park not found: {args.park}")
            return 1
        processor.process_park(park, dry_run=args.dry_run)
    else:
        processor.process_all_parks(dry_run=args.dry_run, limit=args.limit)
    
    return 0


if __name__ == '__main__':
    exit(main())
