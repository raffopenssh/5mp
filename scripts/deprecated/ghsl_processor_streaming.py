#!/usr/bin/env python3
"""
GHSL Processor - Streaming from ZIP files

Processes Global Human Settlement Layer data directly from ZIP archives.
Memory-efficient: processes one tile at a time, deletes after use.

Usage:
    python scripts/ghsl_processor_streaming.py
    python scripts/ghsl_processor_streaming.py --zip /path/to/tile.zip
"""

import json
import sqlite3
import zipfile
import tempfile
import shutil
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import numpy as np

try:
    from pyproj import Transformer
    import rasterio
    from rasterio.mask import mask
    from shapely.geometry import shape, mapping
    from shapely.ops import transform
except ImportError as e:
    print(f"Missing: {e}. Run: pip install pyproj rasterio shapely")
    exit(1)

# Configuration
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data" / "ghsl"
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_PATH = BASE_DIR / "data" / "keystones_with_boundaries.json"

# GHSL grid parameters (Mollweide projection)
TILE_SIZE_M = 1000000
GRID_ORIGIN_X = -18041000
GRID_ORIGIN_Y = 9000000

# Detection parameters
MIN_BUILT_UP = 1  # Minimum m² to count
BUFFER_M = 10000  # 10km buffer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class GHSLStreamingProcessor:
    """Process GHSL data from ZIP files without persistent extraction"""
    
    def __init__(self):
        self.wgs84_to_moll = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True)
        self.keystones = self._load_keystones()
        self._init_db()
        
    def _load_keystones(self) -> List[Dict]:
        with open(KEYSTONES_PATH) as f:
            return [p for p in json.load(f) if p.get('geometry')]
    
    def _init_db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""CREATE TABLE IF NOT EXISTS ghsl_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL UNIQUE,
            year INTEGER DEFAULT 2018,
            built_up_area_km2 REAL DEFAULT 0,
            built_up_percentage REAL DEFAULT 0,
            settlement_count INTEGER DEFAULT 0,
            buffer_built_up_km2 REAL DEFAULT 0,
            buffer_settlement_count INTEGER DEFAULT 0,
            tiles_required INTEGER DEFAULT 0,
            tiles_available INTEGER DEFAULT 0,
            park_area_km2 REAL,
            raw_data_json TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()
        conn.close()
    
    def get_tile_id(self, zip_name: str) -> Optional[Tuple[int, int]]:
        """Extract (row, col) from tile ZIP filename"""
        # GHS_BUILT_S_E2018_GLOBE_R2023A_54009_10_V1_0_R8_C19.zip
        try:
            parts = zip_name.replace('.zip', '').split('_')
            row_part = next(p for p in parts if p.startswith('R') and p[1:].isdigit())
            col_part = next(p for p in parts if p.startswith('C') and p[1:].isdigit())
            return (int(row_part[1:]), int(col_part[1:]))
        except:
            return None
    
    def get_parks_for_tile(self, row: int, col: int) -> List[Dict]:
        """Find parks that overlap with this tile"""
        # Tile bounds in Mollweide
        tile_min_x = GRID_ORIGIN_X + col * TILE_SIZE_M
        tile_max_x = tile_min_x + TILE_SIZE_M
        tile_max_y = GRID_ORIGIN_Y - row * TILE_SIZE_M
        tile_min_y = tile_max_y - TILE_SIZE_M
        
        matching_parks = []
        for park in self.keystones:
            geom = shape(park['geometry'])
            # Transform park to Mollweide
            project = lambda x, y: self.wgs84_to_moll.transform(x, y)
            geom_moll = transform(project, geom)
            bounds = geom_moll.bounds
            
            # Check overlap
            if (bounds[0] < tile_max_x and bounds[2] > tile_min_x and
                bounds[1] < tile_max_y and bounds[3] > tile_min_y):
                matching_parks.append(park)
        
        return matching_parks
    
    def process_zip(self, zip_path: Path) -> Dict:
        """Process a single GHSL tile ZIP"""
        tile_id = self.get_tile_id(zip_path.name)
        if not tile_id:
            logger.error(f"Could not parse tile ID from {zip_path.name}")
            return {'error': 'Invalid filename'}
        
        row, col = tile_id
        logger.info(f"Processing tile R{row}_C{col} from {zip_path.name}")
        
        # Find parks that overlap this tile
        parks = self.get_parks_for_tile(row, col)
        logger.info(f"  Found {len(parks)} overlapping parks")
        
        if not parks:
            return {'parks_processed': 0}
        
        # Extract TIF to temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            with zipfile.ZipFile(zip_path, 'r') as zf:
                tif_files = [n for n in zf.namelist() if n.endswith('.tif')]
                if not tif_files:
                    return {'error': 'No TIF file in ZIP'}
                
                zf.extract(tif_files[0], tmpdir)
                tif_path = tmpdir / tif_files[0]
            
            # Process each park
            results = {'parks_processed': 0, 'settlements_found': 0}
            conn = sqlite3.connect(DB_PATH)
            
            for park in parks:
                try:
                    stats = self.analyze_park_tile(park, tif_path)
                    if stats:
                        self.save_park_stats(conn, park['id'], stats)
                        results['parks_processed'] += 1
                        results['settlements_found'] += stats.get('settlement_count', 0)
                except Exception as e:
                    logger.warning(f"  Error processing {park['id']}: {e}")
            
            conn.commit()
            conn.close()
        
        # TIF is automatically cleaned up when tempdir exits
        return results
    
    def analyze_park_tile(self, park: Dict, tif_path: Path) -> Optional[Dict]:
        """Analyze GHSL data for a park from a single tile"""
        park_id = park['id']
        geom = shape(park['geometry'])
        
        # Transform to Mollweide
        project = lambda x, y: self.wgs84_to_moll.transform(x, y)
        geom_moll = transform(project, geom)
        
        # Get park area
        park_area_km2 = park.get('area_km2') or (geom_moll.area / 1e6)
        
        with rasterio.open(tif_path) as src:
            try:
                out_image, out_transform = mask(src, [mapping(geom_moll)], crop=True, nodata=0)
            except Exception as e:
                logger.debug(f"  {park_id}: no overlap with tile")
                return None
            
            # Calculate built-up area
            valid_data = out_image[out_image >= MIN_BUILT_UP]
            built_up_m2 = np.sum(valid_data)
            built_up_km2 = built_up_m2 / 1e6
            
            # Count settlements (connected components)
            settlement_count = 0
            if built_up_m2 > 0:
                binary = (out_image[0] >= MIN_BUILT_UP).astype(np.uint8)
                from scipy import ndimage
                labeled, num_features = ndimage.label(binary)
                settlement_count = num_features
        
        return {
            'built_up_area_km2': built_up_km2,
            'built_up_percentage': (built_up_km2 / park_area_km2 * 100) if park_area_km2 > 0 else 0,
            'settlement_count': settlement_count,
            'park_area_km2': park_area_km2,
        }
    
    def save_park_stats(self, conn, park_id: str, stats: Dict):
        """Save or update park GHSL stats"""
        # Check if exists
        existing = conn.execute("SELECT built_up_area_km2, settlement_count FROM ghsl_data WHERE park_id = ?", 
                               (park_id,)).fetchone()
        
        if existing:
            # Add to existing (multiple tiles may cover same park)
            conn.execute("""UPDATE ghsl_data SET 
                built_up_area_km2 = built_up_area_km2 + ?,
                settlement_count = settlement_count + ?,
                tiles_available = tiles_available + 1,
                processed_at = ?
                WHERE park_id = ?""",
                (stats['built_up_area_km2'], stats['settlement_count'], 
                 datetime.now().isoformat(), park_id))
        else:
            conn.execute("""INSERT INTO ghsl_data 
                (park_id, built_up_area_km2, built_up_percentage, settlement_count, 
                 park_area_km2, tiles_available, processed_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (park_id, stats['built_up_area_km2'], stats['built_up_percentage'],
                 stats['settlement_count'], stats['park_area_km2'], datetime.now().isoformat()))
    
    def process_directory(self, data_dir: Path = DATA_DIR):
        """Process all GHSL ZIPs in a directory"""
        zips = list(data_dir.glob("*.zip"))
        logger.info(f"Found {len(zips)} ZIP files to process")
        
        for zip_path in zips:
            result = self.process_zip(zip_path)
            logger.info(f"  Result: {result}")
            
            # Delete ZIP after successful processing
            if 'error' not in result:
                zip_path.unlink()
                logger.info(f"  Deleted {zip_path.name}")


def main():
    parser = argparse.ArgumentParser(description='Process GHSL settlement data')
    parser.add_argument('--zip', type=Path, help='Process single ZIP file')
    parser.add_argument('--dir', type=Path, default=DATA_DIR, help='Process all ZIPs in directory')
    parser.add_argument('--keep', action='store_true', help='Keep ZIP files after processing')
    args = parser.parse_args()
    
    processor = GHSLStreamingProcessor()
    
    if args.zip:
        result = processor.process_zip(args.zip)
        logger.info(f"Result: {result}")
        if not args.keep and 'error' not in result:
            args.zip.unlink()
            logger.info(f"Deleted {args.zip}")
    else:
        processor.process_directory(args.dir)


if __name__ == '__main__':
    main()
