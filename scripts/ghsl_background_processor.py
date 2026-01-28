#!/usr/bin/env python3
"""
GHSL Background Processor for 5MP Conservation Globe

IMPROVED VERSION:
- Vectorizes raster to count distinct settlements
- Analyzes 10km buffer around each park
- Lower detection threshold (>=1 built-up m²)
- Reports settlement counts in addition to areas

Usage:
    source venv/bin/activate
    python scripts/ghsl_background_processor.py

Monitor:
    tail -f logs/ghsl_processor.log
    sqlite3 db.sqlite3 "SELECT * FROM ghsl_data ORDER BY settlement_count DESC"
"""

import os
import sys
import json
import sqlite3
import logging
import time
import traceback
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import numpy as np

try:
    from pyproj import Transformer
    import rasterio
    from rasterio.mask import mask
    from rasterio.features import shapes
    from shapely.geometry import shape, box, mapping
    from shapely.ops import transform, unary_union
    from scipy import ndimage
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: source venv/bin/activate && pip install pyproj rasterio shapely scipy")
    sys.exit(1)

# Configuration
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data" / "ghsl"
DB_PATH = BASE_DIR / "db.sqlite3"
LOG_DIR = BASE_DIR / "logs"
KEYSTONES_PATH = BASE_DIR / "data" / "keystones_with_boundaries.json"

# Ensure log directory exists
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / 'ghsl_processor.log')
    ]
)
logger = logging.getLogger(__name__)

# GHSL tile grid parameters (Mollweide projection)
TILE_SIZE_M = 1000000  # 1000km in meters
GRID_ORIGIN_X = -18041000
GRID_ORIGIN_Y = 9000000

# Detection parameters
BUILT_UP_THRESHOLD = 1  # Minimum built-up m² per pixel to count (lowered from implicit >0)
MIN_SETTLEMENT_PIXELS = 1  # Minimum connected pixels to count as settlement
BUFFER_DISTANCE_M = 10000  # 10km buffer around parks


class GHSLBackgroundProcessor:
    """Background processor for GHSL data extraction with vectorization"""
    
    def __init__(self):
        self.db_path = DB_PATH
        self.data_dir = DATA_DIR
        
        # Coordinate transformers
        self.wgs84_to_moll = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True)
        self.moll_to_wgs84 = Transformer.from_crs("ESRI:54009", "EPSG:4326", always_xy=True)
        
        # Load keystones
        self.keystones = self._load_keystones()
        
        # Index tiles
        self.tile_index = self._index_available_tiles()
        
        # Initialize database
        self._init_database()
        
        logger.info(f"Initialized with {len(self.keystones)} parks and {len(self.tile_index)} tiles")
    
    def _load_keystones(self) -> List[Dict]:
        """Load keystone protected areas with boundaries"""
        if not KEYSTONES_PATH.exists():
            logger.error(f"Keystones file not found: {KEYSTONES_PATH}")
            return []
        
        with open(KEYSTONES_PATH) as f:
            parks = json.load(f)
        
        return [p for p in parks if p.get('geometry')]
    
    def _index_available_tiles(self) -> Dict[Tuple[int, int], Path]:
        """Index available GHSL tiles by (row, col)"""
        tile_index = {}
        
        for tile_dir in self.data_dir.glob("GHS_BUILT_S_*"):
            if not tile_dir.is_dir():
                continue
            
            name = tile_dir.name
            try:
                parts = name.split('_')
                row_part = next(p for p in parts if p.startswith('R') and p[1:].isdigit())
                col_part = next(p for p in parts if p.startswith('C') and p[1:].isdigit())
                row = int(row_part[1:])
                col = int(col_part[1:])
                
                tif_files = list(tile_dir.glob("*.tif"))
                if tif_files:
                    tile_index[(row, col)] = tif_files[0]
            except (StopIteration, ValueError):
                logger.warning(f"Could not parse tile: {tile_dir.name}")
        
        return tile_index
    
    def _init_database(self):
        """Initialize ghsl_data table with improved schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ghsl_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id TEXT NOT NULL,
                year INTEGER NOT NULL,
                
                -- Park interior statistics
                built_up_area_km2 REAL DEFAULT 0,
                built_up_percentage REAL DEFAULT 0,
                settlement_count INTEGER DEFAULT 0,
                
                -- Buffer zone statistics (10km around park)
                buffer_built_up_km2 REAL DEFAULT 0,
                buffer_settlement_count INTEGER DEFAULT 0,
                
                -- Coverage info
                tiles_required INTEGER DEFAULT 0,
                tiles_available INTEGER DEFAULT 0,
                park_area_km2 REAL,
                
                -- Metadata
                raw_data_json TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                
                UNIQUE(park_id, year)
            )
        """)
        
        # Create index for fast lookups
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ghsl_park ON ghsl_data(park_id)")
        
        conn.commit()
        conn.close()
        logger.info("Database initialized with ghsl_data table")
    
    def get_db_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn
    
    def bbox_to_mollweide(self, bbox_wgs84: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        """Convert WGS84 bbox to Mollweide coordinates"""
        west, south, east, north = bbox_wgs84
        corners = [
            self.wgs84_to_moll.transform(west, south),
            self.wgs84_to_moll.transform(west, north),
            self.wgs84_to_moll.transform(east, south),
            self.wgs84_to_moll.transform(east, north),
        ]
        return (
            min(c[0] for c in corners),
            min(c[1] for c in corners),
            max(c[0] for c in corners),
            max(c[1] for c in corners)
        )
    
    def get_tiles_for_bbox(self, bbox_wgs84: Tuple[float, float, float, float]) -> List[Tuple[int, int]]:
        """Get list of (row, col) tile indices for bbox"""
        moll_bbox = self.bbox_to_mollweide(bbox_wgs84)
        min_x, min_y, max_x, max_y = moll_bbox
        
        col_min = int((min_x - GRID_ORIGIN_X) / TILE_SIZE_M)
        col_max = int((max_x - GRID_ORIGIN_X) / TILE_SIZE_M)
        row_min = int((GRID_ORIGIN_Y - max_y) / TILE_SIZE_M)
        row_max = int((GRID_ORIGIN_Y - min_y) / TILE_SIZE_M)
        
        tiles = []
        for row in range(max(0, row_min), row_max + 1):
            for col in range(max(0, col_min), col_max + 1):
                tiles.append((row, col))
        return tiles
    
    def count_settlements_vectorized(self, raster_data: np.ndarray, transform_affine) -> Tuple[int, float]:
        """
        Vectorize raster to count distinct settlement polygons.
        
        Returns: (settlement_count, total_built_up_m2)
        """
        # Create binary mask of built-up pixels
        built_mask = raster_data >= BUILT_UP_THRESHOLD
        
        if not np.any(built_mask):
            return 0, 0.0
        
        # Label connected components (8-connectivity for diagonal connections)
        labeled, num_features = ndimage.label(built_mask, structure=np.ones((3, 3)))
        
        # Count features meeting minimum size
        settlement_count = 0
        for label_id in range(1, num_features + 1):
            component_size = np.sum(labeled == label_id)
            if component_size >= MIN_SETTLEMENT_PIXELS:
                settlement_count += 1
        
        # Total built-up area (sum of pixel values = m² built-up)
        total_built_m2 = float(np.sum(raster_data[raster_data >= BUILT_UP_THRESHOLD]))
        
        return settlement_count, total_built_m2
    
    def extract_stats_for_geometry(self, geom_moll, available_tiles: List[Tuple[int, int]]) -> Tuple[float, int]:
        """
        Extract built-up stats for a geometry from available tiles.
        
        Returns: (built_up_km2, settlement_count)
        """
        total_built_m2 = 0.0
        total_settlements = 0
        
        for row, col in available_tiles:
            tif_path = self.tile_index.get((row, col))
            if not tif_path:
                continue
            
            try:
                with rasterio.open(tif_path) as src:
                    # Mask raster with geometry
                    out_image, out_transform = mask(src, [mapping(geom_moll)], crop=True, nodata=0, all_touched=True)
                    
                    if out_image.size == 0:
                        continue
                    
                    # Count settlements and built-up area
                    data = out_image[0]  # First band
                    settlements, built_m2 = self.count_settlements_vectorized(data, out_transform)
                    
                    total_settlements += settlements
                    total_built_m2 += built_m2
                    
            except Exception as e:
                logger.warning(f"Error processing tile R{row}_C{col}: {e}")
        
        return total_built_m2 / 1e6, total_settlements  # Convert to km²
    
    def extract_stats_for_park(self, park: Dict, year: int = 2018) -> Dict:
        """Extract GHSL statistics for a park and its buffer zone"""
        park_id = park['id']
        park_name = park.get('name', park_id)
        
        # Get park geometry and transform to Mollweide
        geom_wgs84 = shape(park['geometry'])
        project_to_moll = lambda x, y: self.wgs84_to_moll.transform(x, y)
        geom_moll = transform(project_to_moll, geom_wgs84)
        
        # Create 10km buffer around park (in Mollweide meters)
        buffer_geom_moll = geom_moll.buffer(BUFFER_DISTANCE_M)
        # Buffer zone = buffer minus park interior
        buffer_zone_moll = buffer_geom_moll.difference(geom_moll)
        
        # Get park area
        park_area_km2 = park.get('area_km2') or (geom_moll.area / 1e6)
        
        # Determine required tiles (for buffered geometry)
        project_to_wgs84 = lambda x, y: self.moll_to_wgs84.transform(x, y)
        buffer_geom_wgs84 = transform(project_to_wgs84, buffer_geom_moll)
        bbox = buffer_geom_wgs84.bounds
        required_tiles = self.get_tiles_for_bbox(bbox)
        available_tiles = [(r, c) for r, c in required_tiles if (r, c) in self.tile_index]
        
        logger.info(f"Park {park_id}: requires {len(required_tiles)} tiles, {len(available_tiles)} available")
        
        # Initialize results
        results = {
            'park_id': park_id,
            'year': year,
            'built_up_area_km2': 0.0,
            'built_up_percentage': 0.0,
            'settlement_count': 0,
            'buffer_built_up_km2': 0.0,
            'buffer_settlement_count': 0,
            'tiles_required': len(required_tiles),
            'tiles_available': len(available_tiles),
            'park_area_km2': park_area_km2
        }
        
        if not available_tiles:
            logger.info(f"  No tiles available for {park_id}")
            results['raw_data_json'] = json.dumps({
                'status': 'no_tiles',
                'tiles_required': required_tiles
            })
            return results
        
        # Extract stats for park interior
        built_km2, settlements = self.extract_stats_for_geometry(geom_moll, available_tiles)
        results['built_up_area_km2'] = built_km2
        results['settlement_count'] = settlements
        
        if park_area_km2 > 0:
            results['built_up_percentage'] = (built_km2 / park_area_km2) * 100
        
        # Extract stats for buffer zone
        if buffer_zone_moll.is_valid and not buffer_zone_moll.is_empty:
            buffer_km2, buffer_settlements = self.extract_stats_for_geometry(buffer_zone_moll, available_tiles)
            results['buffer_built_up_km2'] = buffer_km2
            results['buffer_settlement_count'] = buffer_settlements
        
        # Store metadata
        results['raw_data_json'] = json.dumps({
            'tiles_required': required_tiles,
            'tiles_available': available_tiles,
            'park_area_km2': park_area_km2,
            'buffer_distance_km': BUFFER_DISTANCE_M / 1000,
            'detection_threshold': BUILT_UP_THRESHOLD,
            'processing_note': 'Vectorized settlement detection with 10km buffer'
        })
        
        return results
    
    def save_park_stats(self, stats: Dict):
        """Save park statistics to ghsl_data table"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO ghsl_data 
            (park_id, year, built_up_area_km2, built_up_percentage, settlement_count,
             buffer_built_up_km2, buffer_settlement_count, tiles_required, tiles_available,
             park_area_km2, raw_data_json, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            stats['park_id'],
            stats['year'],
            stats['built_up_area_km2'],
            stats['built_up_percentage'],
            stats['settlement_count'],
            stats['buffer_built_up_km2'],
            stats['buffer_settlement_count'],
            stats['tiles_required'],
            stats['tiles_available'],
            stats['park_area_km2'],
            stats.get('raw_data_json', '{}')
        ))
        
        conn.commit()
        conn.close()
    
    def process_all_parks(self, year: int = 2018, delay_seconds: float = 0.5):
        """Process all parks with improved detection"""
        logger.info(f"="*60)
        logger.info(f"Starting IMPROVED GHSL processing for year {year}")
        logger.info(f"Available tiles: {sorted(self.tile_index.keys())}")
        logger.info(f"Detection threshold: {BUILT_UP_THRESHOLD} m²")
        logger.info(f"Buffer distance: {BUFFER_DISTANCE_M/1000} km")
        logger.info(f"="*60)
        
        processed = 0
        failed = 0
        parks_with_data = 0
        
        for i, park in enumerate(self.keystones):
            park_id = park['id']
            logger.info(f"Processing {i+1}/{len(self.keystones)}: {park_id}")
            
            try:
                stats = self.extract_stats_for_park(park, year)
                self.save_park_stats(stats)
                
                if stats['tiles_available'] > 0:
                    parks_with_data += 1
                    logger.info(f"  Park: {stats['built_up_area_km2']:.4f} km², {stats['settlement_count']} settlements")
                    logger.info(f"  Buffer: {stats['buffer_built_up_km2']:.4f} km², {stats['buffer_settlement_count']} settlements")
                else:
                    logger.info(f"  No tile coverage")
                
                processed += 1
                
            except Exception as e:
                logger.error(f"Failed to process {park_id}: {e}")
                logger.debug(traceback.format_exc())
                failed += 1
            
            time.sleep(delay_seconds)
        
        logger.info(f"="*60)
        logger.info(f"Processing complete:")
        logger.info(f"  Processed: {processed}")
        logger.info(f"  Failed: {failed}")
        logger.info(f"  Parks with tile coverage: {parks_with_data}")
        logger.info(f"="*60)
        
        return {'processed': processed, 'failed': failed, 'with_data': parks_with_data}
    
    def get_summary(self) -> str:
        """Get summary of processed data"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN tiles_available > 0 THEN 1 ELSE 0 END) as with_coverage,
                SUM(settlement_count) as total_settlements,
                SUM(buffer_settlement_count) as total_buffer_settlements,
                SUM(built_up_area_km2) as total_built_km2
            FROM ghsl_data
        """)
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return (f"Total parks: {row['total']}, "
                    f"With coverage: {row['with_coverage']}, "
                    f"Park settlements: {row['total_settlements']}, "
                    f"Buffer settlements: {row['total_buffer_settlements']}, "
                    f"Built-up: {row['total_built_km2']:.2f} km²")
        return "No data"


def main():
    import argparse
    parser = argparse.ArgumentParser(description='GHSL Background Processor (Improved)')
    parser.add_argument('--year', type=int, default=2018, help='Year to process')
    parser.add_argument('--summary', action='store_true', help='Show summary only')
    parser.add_argument('--delay', type=float, default=0.1, help='Delay between parks')
    args = parser.parse_args()
    
    processor = GHSLBackgroundProcessor()
    
    if args.summary:
        print(processor.get_summary())
    else:
        processor.process_all_parks(year=args.year, delay_seconds=args.delay)
        print("\nFinal summary:")
        print(processor.get_summary())


if __name__ == '__main__':
    main()
