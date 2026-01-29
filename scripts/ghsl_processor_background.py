#!/usr/bin/env python3
"""
GHSL Background Processor - Efficient Settlement Detection

Designed to run safely overnight for all parks.

Key features:
- Windowed reads instead of mask operations (memory efficient)
- Processes one tile at a time
- Checkpointing: skips already-processed parks
- Progress logging to file
- Graceful error handling per park

Usage:
    nohup python scripts/ghsl_processor_background.py > logs/ghsl.log 2>&1 &
    
    # Or for specific parks:
    python scripts/ghsl_processor_background.py --park COD_Virunga
    
    # Dry run:
    python scripts/ghsl_processor_background.py --dry-run --limit 5
"""

import json
import sqlite3
import zipfile
import argparse
import math
import time
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import numpy as np

# Force unbuffered output for logging
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)

try:
    from pyproj import Transformer
    import rasterio
    from rasterio.windows import from_bounds, Window
    from rasterio.io import MemoryFile
    from shapely.geometry import shape, box
    from shapely.ops import transform as shp_transform
    from scipy import ndimage
except ImportError as e:
    print(f"Missing: {e}. Run: pip install pyproj rasterio shapely scipy")
    sys.exit(1)

# Paths
BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_PATH = BASE_DIR / "data" / "keystones_with_boundaries.json"
GHSL_ZIP_PATH = BASE_DIR / "data" / "ghsl_examples.zip"

# Detection parameters
MIN_BUILT_UP_M2 = 500  # Minimum m² to count as settlement
MIN_CLUSTER_PIXELS = 5  # Minimum pixels for a cluster
HOUSEHOLD_SIZE = 5.2  # Average people per household
PIXEL_SIZE_M = 100  # 100m resolution

# Coordinate transformers
wgs84_to_moll = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True)
moll_to_wgs84 = Transformer.from_crs("ESRI:54009", "EPSG:4326", always_xy=True)

# Cardinal directions
CARDINALS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

def log(msg):
    """Log with timestamp"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def bearing_to_cardinal(bearing: float) -> str:
    """Convert bearing (0-360) to cardinal direction"""
    idx = int((bearing + 11.25) / 22.5) % 16
    return CARDINALS[idx]

def calc_bearing(lat1, lon1, lat2, lon2) -> float:
    """Calculate bearing from point 1 to point 2"""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Distance in km between two points"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


class GHSLProcessor:
    def __init__(self, zip_path: Path = GHSL_ZIP_PATH):
        self.zip_path = zip_path
        self.tile_index = self._build_tile_index()
        self.keystones = self._load_keystones()
        self._init_db()
        
    def _load_keystones(self) -> List[Dict]:
        """Load keystones with geometry"""
        with open(KEYSTONES_PATH) as f:
            return [p for p in json.load(f) if p.get('geometry')]
    
    def _build_tile_index(self) -> Dict:
        """Index tiles in ZIP file with actual bounds"""
        index = {'BUILT_S': {}, 'POP': {}, 'bounds': {}}
        
        with zipfile.ZipFile(self.zip_path, 'r') as zf:
            for name in zf.namelist():
                if not name.endswith('.tif') or '__MACOSX' in name:
                    continue
                
                # Parse R{row}_C{col} from path
                folder = name.split('/')[0]
                row = col = None
                for part in folder.split('_'):
                    if part.startswith('R') and part[1:].isdigit():
                        row = int(part[1:])
                    elif part.startswith('C') and part[1:].isdigit():
                        col = int(part[1:])
                
                if row is None or col is None:
                    continue
                
                key = f"R{row}_C{col}"
                
                # Prefer 100m resolution for efficiency
                if 'BUILT_S' in name and '_100_' in name:
                    index['BUILT_S'][key] = name
                    # Read actual bounds from file
                    if key not in index['bounds']:
                        try:
                            with zf.open(name) as f:
                                data = f.read()
                            with MemoryFile(data) as memfile:
                                with memfile.open() as src:
                                    index['bounds'][key] = src.bounds
                        except:
                            pass
                elif 'POP' in name and '_100_' in name:
                    index['POP'][key] = name
        
        log(f"Tile index: {len(index['BUILT_S'])} BUILT_S, {len(index['POP'])} POP tiles")
        log(f"Tile bounds: {list(index['bounds'].keys())}")
        return index
    
    def _init_db(self):
        """Initialize database table"""
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS park_settlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                area_m2 REAL,
                population_estimate REAL,
                nearest_place TEXT,
                distance_km REAL,
                direction TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_settlements_park ON park_settlements(park_id)')
        conn.commit()
        conn.close()
    
    def _get_tiles_for_park(self, park: Dict) -> List[str]:
        """Find tiles that overlap with park using actual tile bounds"""
        geom = shape(park['geometry'])
        geom_moll = shp_transform(lambda x, y: wgs84_to_moll.transform(x, y), geom)
        
        overlapping = []
        for key, bounds in self.tile_index.get('bounds', {}).items():
            # bounds is a BoundingBox(left, bottom, right, top)
            tile_box = box(bounds.left, bounds.bottom, bounds.right, bounds.top)
            if geom_moll.intersects(tile_box):
                overlapping.append(key)
        
        return overlapping
    
    def _read_tile_windowed(self, tif_path: str, park_bounds_moll: tuple) -> Optional[Tuple[np.ndarray, dict]]:
        """Read only the park area from a tile using windowed read"""
        try:
            with zipfile.ZipFile(self.zip_path, 'r') as zf:
                with zf.open(tif_path) as f:
                    data = f.read()
            
            with MemoryFile(data) as memfile:
                with memfile.open() as src:
                    # Get window for park bounds
                    minx, miny, maxx, maxy = park_bounds_moll
                    
                    # Clamp to tile bounds
                    tile_bounds = src.bounds
                    minx = max(minx, tile_bounds.left)
                    miny = max(miny, tile_bounds.bottom)
                    maxx = min(maxx, tile_bounds.right)
                    maxy = min(maxy, tile_bounds.top)
                    
                    if minx >= maxx or miny >= maxy:
                        return None
                    
                    # Create window from bounds
                    window = from_bounds(minx, miny, maxx, maxy, src.transform)
                    
                    # Clamp window to valid range
                    window = Window(
                        max(0, int(window.col_off)),
                        max(0, int(window.row_off)),
                        min(int(window.width), src.width - max(0, int(window.col_off))),
                        min(int(window.height), src.height - max(0, int(window.row_off)))
                    )
                    
                    if window.width <= 0 or window.height <= 0:
                        return None
                    
                    # Read windowed data
                    arr = src.read(1, window=window)
                    win_transform = src.window_transform(window)
                    
                    return arr, {
                        'transform': win_transform,
                        'crs': src.crs,
                        'nodata': src.nodata,
                        'bounds': (minx, miny, maxx, maxy)
                    }
        except Exception as e:
            log(f"  Error reading tile: {e}")
            return None
    
    def _find_nearest_place(self, lat: float, lon: float, park_id: str) -> Optional[Tuple[str, float, str]]:
        """Find nearest place from osm_places table"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Search within 100km
        cursor.execute('''
            SELECT name, lat, lon, place_type
            FROM osm_places
            WHERE park_id = ?
            ORDER BY (lat - ?)*(lat - ?) + (lon - ?)*(lon - ?)
            LIMIT 1
        ''', (park_id, lat, lat, lon, lon))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            name, place_lat, place_lon, place_type = row
            dist = haversine_km(lat, lon, place_lat, place_lon)
            bearing = calc_bearing(place_lat, place_lon, lat, lon)
            direction = bearing_to_cardinal(bearing)
            return (name, dist, direction)
        return None
    
    def _extract_settlements(self, park: Dict, built_arr: np.ndarray, meta: dict, 
                            pop_arr: Optional[np.ndarray]) -> List[Dict]:
        """Extract settlement clusters from built-up array"""
        if built_arr is None or built_arr.size == 0:
            return []
        
        # Threshold: pixels with >500 m² built-up area
        threshold = MIN_BUILT_UP_M2
        binary = (built_arr > threshold).astype(np.uint8)
        
        if binary.sum() == 0:
            return []
        
        # Label connected components
        labeled, num_features = ndimage.label(binary)
        
        settlements = []
        transform = meta['transform']
        
        for cluster_id in range(1, min(num_features + 1, 500)):  # Limit to 500 clusters
            mask = labeled == cluster_id
            pixel_count = mask.sum()
            
            if pixel_count < MIN_CLUSTER_PIXELS:
                continue
            
            # Calculate centroid
            rows, cols = np.where(mask)
            center_row = rows.mean()
            center_col = cols.mean()
            
            # Convert to Mollweide coordinates
            x = transform.c + center_col * transform.a
            y = transform.f + center_row * transform.e
            
            # Convert to WGS84
            lon, lat = moll_to_wgs84.transform(x, y)
            
            # Calculate area
            area_m2 = pixel_count * PIXEL_SIZE_M * PIXEL_SIZE_M
            
            # Estimate population if pop data available
            pop_estimate = 0
            if pop_arr is not None and pop_arr.shape == built_arr.shape:
                pop_estimate = pop_arr[mask].sum()
            else:
                # Estimate from built area
                buildings = area_m2 / 50  # Assume 50m² per building
                pop_estimate = buildings * HOUSEHOLD_SIZE
            
            settlements.append({
                'lat': round(lat, 6),
                'lon': round(lon, 6),
                'area_m2': round(area_m2, 1),
                'population_estimate': round(pop_estimate, 0)
            })
        
        return settlements
    
    def process_park(self, park: Dict, dry_run: bool = False) -> int:
        """Process a single park, return settlement count"""
        park_id = park['id']
        park_name = park['name']
        
        # Check if already processed
        if not dry_run:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM park_settlements WHERE park_id = ?', (park_id,))
            existing = cursor.fetchone()[0]
            conn.close()
            if existing > 0:
                log(f"  {park_id}: already has {existing} settlements, skipping")
                return existing
        
        # Get park bounds in Mollweide
        geom = shape(park['geometry'])
        geom_moll = shp_transform(lambda x, y: wgs84_to_moll.transform(x, y), geom)
        park_bounds = geom_moll.bounds
        
        # Find overlapping tiles
        tiles = self._get_tiles_for_park(park)
        if not tiles:
            log(f"  {park_id}: no tiles available")
            return 0
        
        all_settlements = []
        
        for tile_key in tiles:
            built_path = self.tile_index['BUILT_S'].get(tile_key)
            pop_path = self.tile_index['POP'].get(tile_key)
            
            if not built_path:
                continue
            
            # Read built-up data (windowed)
            result = self._read_tile_windowed(built_path, park_bounds)
            if result is None:
                continue
            built_arr, meta = result
            
            # Read population data if available
            pop_arr = None
            if pop_path:
                pop_result = self._read_tile_windowed(pop_path, park_bounds)
                if pop_result:
                    pop_arr = pop_result[0]
            
            # Extract settlements
            settlements = self._extract_settlements(park, built_arr, meta, pop_arr)
            all_settlements.extend(settlements)
            
            # Free memory
            del built_arr, pop_arr
        
        if not all_settlements:
            log(f"  {park_id}: no settlements found")
            return 0
        
        # Add place context for each settlement
        for s in all_settlements:
            place_info = self._find_nearest_place(s['lat'], s['lon'], park_id)
            if place_info:
                name, dist, direction = place_info
                s['nearest_place'] = name
                s['distance_km'] = round(dist, 1)
                s['direction'] = direction
                s['description'] = f"{s['area_m2']:.0f}m², ~{s['population_estimate']:.0f} people, {dist:.0f}km {direction} of {name}"
            else:
                s['description'] = f"{s['area_m2']:.0f}m², ~{s['population_estimate']:.0f} people at ({s['lat']:.4f}, {s['lon']:.4f})"
        
        if dry_run:
            log(f"  {park_id}: would insert {len(all_settlements)} settlements")
            for s in all_settlements[:3]:
                log(f"    - {s['description']}")
            return len(all_settlements)
        
        # Save to database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        for s in all_settlements:
            cursor.execute('''
                INSERT INTO park_settlements 
                (park_id, lat, lon, area_m2, population_estimate, nearest_place, distance_km, direction, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                park_id, s['lat'], s['lon'], s['area_m2'], s['population_estimate'],
                s.get('nearest_place'), s.get('distance_km'), s.get('direction'), s['description']
            ))
        
        conn.commit()
        conn.close()
        
        log(f"  {park_id}: inserted {len(all_settlements)} settlements")
        return len(all_settlements)
    
    def process_all(self, dry_run: bool = False, limit: int = None, park_id: str = None):
        """Process all parks"""
        log(f"Starting GHSL processing (dry_run={dry_run}, limit={limit})")
        
        parks = self.keystones
        if park_id:
            parks = [p for p in parks if p['id'] == park_id]
        if limit:
            parks = parks[:limit]
        
        log(f"Processing {len(parks)} parks...")
        
        total_settlements = 0
        processed = 0
        errors = 0
        
        for i, park in enumerate(parks, 1):
            try:
                log(f"[{i}/{len(parks)}] {park['id']}...")
                count = self.process_park(park, dry_run)
                total_settlements += count
                processed += 1
            except Exception as e:
                log(f"  ERROR processing {park['id']}: {e}")
                errors += 1
        
        log(f"\nComplete: {processed} parks, {total_settlements} settlements, {errors} errors")
        return total_settlements


def main():
    parser = argparse.ArgumentParser(description='GHSL Background Processor')
    parser.add_argument('--park', help='Process single park by ID')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    parser.add_argument('--limit', type=int, help='Limit number of parks')
    args = parser.parse_args()
    
    processor = GHSLProcessor()
    processor.process_all(dry_run=args.dry_run, limit=args.limit, park_id=args.park)


if __name__ == '__main__':
    main()
