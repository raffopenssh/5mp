#!/usr/bin/env python3
"""
GHSL Enhanced Processor - Settlement Detection with Population

Reads GHSL data directly from ZIP file, combines built-up surface with
population estimates, and stores settlement locations with GPS coordinates.

Features:
- Reads TIF files from ZIP without full extraction
- Combines BUILT_S (built-up surface) with POP (population)
- Detects settlement clusters and estimates households
- Queries Overpass API for nearby village names
- Memory efficient: one tile at a time

Usage:
    source .venv/bin/activate
    python scripts/ghsl_enhanced_processor.py
    python scripts/ghsl_enhanced_processor.py --park AGO_Cameia
    python scripts/ghsl_enhanced_processor.py --dry-run
"""

import json
import sqlite3
import zipfile
import tempfile
import argparse
import logging
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from io import BytesIO
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

# API settings
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 30
API_SLEEP = 2  # Seconds between Overpass requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


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
        """Create park_settlements table"""
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""CREATE TABLE IF NOT EXISTS park_settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            area_m2 REAL DEFAULT 0,
            population_estimate REAL DEFAULT 0,
            households_estimate REAL DEFAULT 0,
            nearest_village_name TEXT,
            distance_to_village_km REAL,
            building_type TEXT,  -- 'temporary', 'small', 'medium', 'large'
            in_buffer INTEGER DEFAULT 0,  -- 1 if in 10km buffer, 0 if in park
            tile_row INTEGER,
            tile_col INTEGER,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(park_id, lat, lon)
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
            
            # Classify building type by area
            if area_m2 < 200:
                building_type = 'temporary'
            elif area_m2 < 1000:
                building_type = 'small'
            elif area_m2 < 5000:
                building_type = 'medium'
            else:
                building_type = 'large'
            
            settlements.append({
                'lat': lat,
                'lon': lon,
                'area_m2': area_m2,
                'population_estimate': population,
                'households_estimate': households,
                'building_type': building_type,
                'in_buffer': 0 if in_park else 1
            })
        
        return settlements
    
    def query_village_name(self, lat: float, lon: float, radius_km: float = 10) -> Optional[Tuple[str, float]]:
        """Query Overpass API for nearest village/town name"""
        query = f"""
        [out:json][timeout:{OVERPASS_TIMEOUT}];
        (
          node["place"~"village|town|hamlet|locality"](around:{radius_km*1000},{lat},{lon});
          way["place"~"village|town|hamlet|locality"](around:{radius_km*1000},{lat},{lon});
        );
        out center;
        """
        
        try:
            resp = requests.post(OVERPASS_URL, data={'data': query}, timeout=OVERPASS_TIMEOUT)
            if resp.status_code != 200:
                return None
            
            data = resp.json()
            elements = data.get('elements', [])
            
            if not elements:
                return None
            
            # Find nearest with a name
            best = None
            best_dist = float('inf')
            
            for el in elements:
                name = el.get('tags', {}).get('name')
                if not name:
                    continue
                
                # Get coordinates
                if el['type'] == 'node':
                    el_lat, el_lon = el['lat'], el['lon']
                elif 'center' in el:
                    el_lat, el_lon = el['center']['lat'], el['center']['lon']
                else:
                    continue
                
                # Calculate distance (approximate)
                dist_km = ((lat - el_lat)**2 + (lon - el_lon)**2)**0.5 * 111
                
                if dist_km < best_dist:
                    best_dist = dist_km
                    best = (name, dist_km)
            
            return best
            
        except Exception as e:
            logger.warning(f"Overpass query failed: {e}")
            return None
    
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
        
        # Query village names for settlements (limit to avoid API overload)
        for i, s in enumerate(all_settlements[:20]):  # Max 20 queries per park
            village_info = self.query_village_name(s['lat'], s['lon'])
            if village_info:
                s['nearest_village_name'] = village_info[0]
                s['distance_to_village_km'] = village_info[1]
            time.sleep(API_SLEEP)
        
        if dry_run:
            logger.info(f"[DRY RUN] Would insert {len(all_settlements)} settlements for {park_id}")
            for s in all_settlements[:5]:
                logger.info(f"  - {s['lat']:.4f}, {s['lon']:.4f}: {s['area_m2']:.0f}m², "
                           f"{s['population_estimate']:.0f} people, type={s['building_type']}")
            return len(all_settlements)
        
        # Insert into database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        inserted = 0
        for s in all_settlements:
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO park_settlements 
                    (park_id, lat, lon, area_m2, population_estimate, households_estimate,
                     nearest_village_name, distance_to_village_km, building_type, in_buffer,
                     tile_row, tile_col)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    park_id, s['lat'], s['lon'], s['area_m2'],
                    s['population_estimate'], s['households_estimate'],
                    s.get('nearest_village_name'), s.get('distance_to_village_km'),
                    s['building_type'], s['in_buffer'],
                    s.get('tile_row'), s.get('tile_col')
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
                
                # Sleep between parks to avoid memory buildup
                time.sleep(1)
                
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
