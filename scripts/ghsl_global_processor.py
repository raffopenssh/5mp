#!/usr/bin/env python3
"""
GHSL Global Processor - Settlement Detection from Global GHSL File

Processes the global GHSL built-up surface file (ESRI:54009 Mollweide projection)
to detect settlements within keystone parks.

Features:
- Windowed reads for memory efficiency (only reads park bounding box)
- Processes all 162 parks from a single global file
- Checkpointing: skips already-processed parks
- Progress logging for overnight runs

Usage:
    nohup python scripts/ghsl_global_processor.py > logs/ghsl_global.log 2>&1 &
    python scripts/ghsl_global_processor.py --park COD_Virunga --dry-run
"""

import json
import sqlite3
import argparse
import math
import time
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import numpy as np

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)

try:
    from pyproj import Transformer
    import rasterio
    from rasterio.windows import from_bounds, Window
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform
    from scipy import ndimage
except ImportError as e:
    print(f"Missing: {e}. Run: pip install pyproj rasterio shapely scipy")
    sys.exit(1)

# Paths
BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_PATH = BASE_DIR / "data" / "keystones_with_boundaries.json"
GHSL_GLOBAL_PATH = BASE_DIR / "data" / "ghsl_global" / "GHS_BUILT_S_E2030_GLOBE_R2023A_54009_100_V1_0.tif"

# Detection parameters
MIN_BUILT_UP_M2 = 500  # m² threshold
MIN_CLUSTER_PIXELS = 5
HOUSEHOLD_SIZE = 5.2
PIXEL_SIZE_M = 100

# Transformers
wgs84_to_moll = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True)
moll_to_wgs84 = Transformer.from_crs("ESRI:54009", "EPSG:4326", always_xy=True)

CARDINALS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def bearing_to_cardinal(bearing: float) -> str:
    idx = int((bearing + 11.25) / 22.5) % 16
    return CARDINALS[idx]

def calc_bearing(lat1, lon1, lat2, lon2) -> float:
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


class GHSLGlobalProcessor:
    def __init__(self, ghsl_path: Path = GHSL_GLOBAL_PATH):
        self.ghsl_path = ghsl_path
        self.keystones = self._load_keystones()
        self._init_db()
        
        # Open raster once, keep handle
        if not ghsl_path.exists():
            raise FileNotFoundError(f"GHSL file not found: {ghsl_path}")
        self.src = rasterio.open(ghsl_path)
        log(f"Opened GHSL: {self.src.width}x{self.src.height}, CRS={self.src.crs}")
        
    def __del__(self):
        if hasattr(self, 'src') and self.src:
            self.src.close()
    
    def _load_keystones(self) -> List[Dict]:
        with open(KEYSTONES_PATH) as f:
            return [p for p in json.load(f) if p.get('geometry')]
    
    def _init_db(self):
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
    
    def _find_nearest_place(self, lat: float, lon: float, park_id: str) -> Optional[tuple]:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
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
    
    def _read_park_window(self, park: Dict) -> Optional[tuple]:
        """Read GHSL data for a park using windowed read"""
        try:
            geom = shape(park['geometry'])
            geom_moll = shp_transform(lambda x, y: wgs84_to_moll.transform(x, y), geom)
            minx, miny, maxx, maxy = geom_moll.bounds
            
            # Add small buffer
            buffer = 1000  # 1km
            minx -= buffer
            miny -= buffer
            maxx += buffer
            maxy += buffer
            
            # Create window from bounds
            window = from_bounds(minx, miny, maxx, maxy, self.src.transform)
            
            # Clamp to raster bounds
            window = Window(
                max(0, int(window.col_off)),
                max(0, int(window.row_off)),
                min(int(window.width), self.src.width - max(0, int(window.col_off))),
                min(int(window.height), self.src.height - max(0, int(window.row_off)))
            )
            
            if window.width <= 0 or window.height <= 0:
                return None
            
            # Read windowed data
            arr = self.src.read(1, window=window)
            win_transform = self.src.window_transform(window)
            
            return arr, win_transform, (minx, miny, maxx, maxy)
        except Exception as e:
            log(f"  Error reading window: {e}")
            return None
    
    def _extract_settlements(self, park: Dict, arr: np.ndarray, transform, bounds: tuple) -> List[Dict]:
        """Extract settlement clusters from array"""
        if arr is None or arr.size == 0:
            return []
        
        # Threshold for built-up (value in m² per pixel)
        threshold = MIN_BUILT_UP_M2
        binary = (arr > threshold).astype(np.uint8)
        
        if binary.sum() == 0:
            return []
        
        # Label connected components
        labeled, num_features = ndimage.label(binary)
        
        settlements = []
        
        for cluster_id in range(1, min(num_features + 1, 1000)):
            mask = labeled == cluster_id
            pixel_count = mask.sum()
            
            if pixel_count < MIN_CLUSTER_PIXELS:
                continue
            
            # Calculate centroid
            rows, cols = np.where(mask)
            center_row = rows.mean()
            center_col = cols.mean()
            
            # Convert to Mollweide
            x = transform.c + center_col * transform.a
            y = transform.f + center_row * transform.e
            
            # Convert to WGS84
            lon, lat = moll_to_wgs84.transform(x, y)
            
            # Area and population
            area_m2 = pixel_count * PIXEL_SIZE_M * PIXEL_SIZE_M
            buildings = area_m2 / 50
            pop_estimate = buildings * HOUSEHOLD_SIZE
            
            settlements.append({
                'lat': round(lat, 6),
                'lon': round(lon, 6),
                'area_m2': round(area_m2, 1),
                'population_estimate': round(pop_estimate, 0)
            })
        
        return settlements
    
    def process_park(self, park: Dict, dry_run: bool = False) -> int:
        park_id = park['id']
        
        # Check if already processed
        if not dry_run:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM park_settlements WHERE park_id = ?', (park_id,))
            existing = cursor.fetchone()[0]
            conn.close()
            if existing > 0:
                return -1  # Already processed
        
        # Read park window
        result = self._read_park_window(park)
        if result is None:
            return 0
        
        arr, transform, bounds = result
        
        # Extract settlements
        settlements = self._extract_settlements(park, arr, transform, bounds)
        
        if not settlements:
            return 0
        
        # Add place context
        for s in settlements:
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
            log(f"  Would insert {len(settlements)} settlements")
            for s in settlements[:3]:
                log(f"    - {s['description']}")
            return len(settlements)
        
        # Save to database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        for s in settlements:
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
        
        return len(settlements)
    
    def process_all(self, dry_run: bool = False, limit: int = None, park_id: str = None):
        log(f"GHSL Global Processor")
        log(f"Processing from: {self.ghsl_path}")
        
        parks = self.keystones
        if park_id:
            parks = [p for p in parks if p['id'] == park_id]
        if limit:
            parks = parks[:limit]
        
        log(f"Processing {len(parks)} parks...")
        
        total_settlements = 0
        processed = 0
        skipped = 0
        errors = 0
        
        for i, park in enumerate(parks, 1):
            try:
                count = self.process_park(park, dry_run)
                if count == -1:
                    skipped += 1
                    log(f"[{i}/{len(parks)}] {park['id']}: already processed, skipping")
                elif count == 0:
                    log(f"[{i}/{len(parks)}] {park['id']}: no settlements")
                    processed += 1
                else:
                    log(f"[{i}/{len(parks)}] {park['id']}: {count} settlements")
                    total_settlements += count
                    processed += 1
            except Exception as e:
                log(f"[{i}/{len(parks)}] {park['id']}: ERROR - {e}")
                errors += 1
        
        log(f"\nComplete: {processed} processed, {skipped} skipped, {errors} errors")
        log(f"Total settlements: {total_settlements}")
        return total_settlements


def main():
    parser = argparse.ArgumentParser(description='GHSL Global Processor')
    parser.add_argument('--park', help='Process single park by ID')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    parser.add_argument('--limit', type=int, help='Limit number of parks')
    parser.add_argument('--clear', action='store_true', help='Clear existing data first')
    args = parser.parse_args()
    
    if args.clear:
        log("Clearing existing park_settlements data...")
        conn = sqlite3.connect(DB_PATH)
        conn.execute('DELETE FROM park_settlements')
        conn.commit()
        conn.close()
    
    processor = GHSLGlobalProcessor()
    processor.process_all(dry_run=args.dry_run, limit=args.limit, park_id=args.park)


if __name__ == '__main__':
    main()
