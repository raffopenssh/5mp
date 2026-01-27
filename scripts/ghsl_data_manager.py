#!/usr/bin/env python3
"""
GHSL Data Manager for 5MP Conservation Globe

Downloads and processes Global Human Settlement Layer data:
- GHS_BUILT_S: Built-up surface area (m²)
- GHS_POP: Population estimates

Tiles use Mollweide projection (ESRI:54009).
Resolution: 100m (available for 2018, 2020, 2025, 2030)
"""

import os
import sys
import json
import sqlite3
import requests
import zipfile
import logging
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import numpy as np

try:
    from pyproj import Transformer
    import rasterio
    from rasterio.mask import mask
    from shapely.geometry import shape, box, mapping
    from shapely.ops import transform
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install pyproj rasterio shapely")
    sys.exit(1)

# Configuration
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data" / "ghsl"
DB_PATH = BASE_DIR / "db.sqlite3"

# GHSL tile grid parameters (Mollweide projection)
# Based on GHSL documentation - tiles are 1000x1000 pixels at 100m = 100km x 100km
TILE_SIZE_M = 100000  # 100km in meters
GRID_ORIGIN_X = -18041000  # Western edge of Mollweide grid (meters)
GRID_ORIGIN_Y = 9000000    # Northern edge of Mollweide grid (meters)

# Available years for 100m resolution
AVAILABLE_YEARS = [2020, 2025, 2030]  # 2018 has different structure

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class GHSLDataManager:
    """Manages GHSL population and built-up surface data"""
    
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Coordinate transformers
        self.wgs84_to_moll = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True)
        self.moll_to_wgs84 = Transformer.from_crs("ESRI:54009", "EPSG:4326", always_xy=True)
        
        self.keystones = self._load_keystones()
        self._init_db()
    
    def _load_keystones(self):
        """Load keystone protected areas"""
        keystones_path = BASE_DIR / "data" / "keystones_with_boundaries.json"
        if keystones_path.exists():
            with open(keystones_path) as f:
                return json.load(f)
        return []
    
    def _init_db(self):
        """Initialize database tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS park_ghsl_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id TEXT NOT NULL,
                year INTEGER NOT NULL,
                built_up_km2 REAL,
                population_estimate REAL,
                data_source TEXT DEFAULT 'GHSL_R2023A',
                processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(park_id, year)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ghsl_tiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tile_id TEXT NOT NULL UNIQUE,
                row INTEGER NOT NULL,
                col INTEGER NOT NULL,
                product TEXT NOT NULL,
                year INTEGER NOT NULL,
                resolution INTEGER NOT NULL,
                filepath TEXT,
                downloaded_at DATETIME
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("Database tables initialized")
    
    def get_db_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    # ==================== TILE CALCULATION ====================
    
    def bbox_to_mollweide(self, bbox_wgs84: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        """
        Convert WGS84 bbox to Mollweide coordinates.
        bbox_wgs84: (west, south, east, north) in degrees
        Returns: (min_x, min_y, max_x, max_y) in meters
        """
        west, south, east, north = bbox_wgs84
        
        # Transform corners
        corners = [
            self.wgs84_to_moll.transform(west, south),
            self.wgs84_to_moll.transform(west, north),
            self.wgs84_to_moll.transform(east, south),
            self.wgs84_to_moll.transform(east, north),
        ]
        
        min_x = min(c[0] for c in corners)
        max_x = max(c[0] for c in corners)
        min_y = min(c[1] for c in corners)
        max_y = max(c[1] for c in corners)
        
        return (min_x, min_y, max_x, max_y)
    
    def get_tiles_for_bbox(self, bbox_wgs84: Tuple[float, float, float, float]) -> List[Tuple[int, int]]:
        """
        Get list of (row, col) tile indices that cover the given bbox.
        """
        moll_bbox = self.bbox_to_mollweide(bbox_wgs84)
        min_x, min_y, max_x, max_y = moll_bbox
        
        # Calculate tile indices
        # Column increases from west to east
        col_min = int((min_x - GRID_ORIGIN_X) / TILE_SIZE_M)
        col_max = int((max_x - GRID_ORIGIN_X) / TILE_SIZE_M)
        
        # Row increases from north to south (origin is top-left)
        row_min = int((GRID_ORIGIN_Y - max_y) / TILE_SIZE_M)
        row_max = int((GRID_ORIGIN_Y - min_y) / TILE_SIZE_M)
        
        tiles = []
        for row in range(max(0, row_min), row_max + 1):
            for col in range(max(0, col_min), col_max + 1):
                tiles.append((row, col))
        
        return tiles
    
    def get_park_bbox(self, park_id: str) -> Optional[Tuple[float, float, float, float]]:
        """Get bounding box for a park from its geometry."""
        park = next((p for p in self.keystones if p['id'] == park_id), None)
        if not park or 'geometry' not in park:
            return None
        
        geom = shape(park['geometry'])
        return geom.bounds  # (minx, miny, maxx, maxy)
    
    # ==================== TILE DOWNLOAD ====================
    
    def get_tile_url(self, product: str, year: int, row: int, col: int, resolution: int = 100) -> str:
        """
        Get download URL for a GHSL tile.
        
        Products:
        - GHS_BUILT_S: Built-up surface
        - GHS_POP: Population
        """
        res_str = f"{resolution}" if resolution >= 100 else f"{resolution}m"
        
        if product == "GHS_BUILT_S":
            base = f"https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A"
            folder = f"GHS_BUILT_S_E{year}_GLOBE_R2023A_54009_{res_str}"
            filename = f"GHS_BUILT_S_E{year}_GLOBE_R2023A_54009_{res_str}_V1_0_R{row}_C{col}.zip"
        elif product == "GHS_POP":
            base = f"https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A"
            folder = f"GHS_POP_E{year}_GLOBE_R2023A_54009_{res_str}"
            filename = f"GHS_POP_E{year}_GLOBE_R2023A_54009_{res_str}_V1_0_R{row}_C{col}.zip"
        else:
            raise ValueError(f"Unknown product: {product}")
        
        return f"{base}/{folder}/V1-0/tiles/{filename}"
    
    def download_tile(self, product: str, year: int, row: int, col: int, 
                      resolution: int = 100, force: bool = False) -> Optional[Path]:
        """
        Download a GHSL tile and extract the TIF file.
        Returns path to the extracted TIF file.
        """
        tile_dir = self.data_dir / product / str(year)
        tile_dir.mkdir(parents=True, exist_ok=True)
        
        # Expected TIF filename after extraction
        tif_name = f"{product}_E{year}_R{row}_C{col}.tif"
        tif_path = tile_dir / tif_name
        
        if tif_path.exists() and not force:
            logger.info(f"Tile already exists: {tif_path}")
            return tif_path
        
        url = self.get_tile_url(product, year, row, col, resolution)
        zip_path = tile_dir / f"R{row}_C{col}.zip"
        
        logger.info(f"Downloading {url}...")
        
        try:
            response = requests.get(url, stream=True, timeout=300)  # 5 min timeout
            response.raise_for_status()
            
            with open(zip_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            logger.info(f"Downloaded {zip_path.stat().st_size / 1024 / 1024:.1f} MB")
            
            # Extract TIF
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for name in zf.namelist():
                    if name.endswith('.tif'):
                        # Extract and rename
                        zf.extract(name, tile_dir)
                        extracted = tile_dir / name
                        if extracted != tif_path:
                            extracted.rename(tif_path)
                        break
            
            # Clean up zip
            zip_path.unlink()
            
            # Record in database
            self._record_tile(product, year, row, col, resolution, str(tif_path))
            
            return tif_path
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Tile not found (no data for this area): R{row}_C{col}")
            else:
                logger.error(f"HTTP error downloading tile: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to download tile: {e}")
            return None
    
    def _record_tile(self, product: str, year: int, row: int, col: int, 
                     resolution: int, filepath: str):
        """Record downloaded tile in database"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        tile_id = f"{product}_{year}_R{row}_C{col}"
        
        cursor.execute("""
            INSERT OR REPLACE INTO ghsl_tiles 
            (tile_id, row, col, product, year, resolution, filepath, downloaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (tile_id, row, col, product, year, resolution, filepath))
        
        conn.commit()
        conn.close()
    
    # ==================== DATA EXTRACTION ====================
    
    def extract_stats_for_park(self, park_id: str, year: int = 2020) -> Dict:
        """
        Extract GHSL statistics for a single park.
        Returns dict with built_up_km2 and population_estimate.
        """
        park = next((p for p in self.keystones if p['id'] == park_id), None)
        if not park or 'geometry' not in park:
            logger.error(f"Park not found or no geometry: {park_id}")
            return {}
        
        # Get park geometry and transform to Mollweide
        geom_wgs84 = shape(park['geometry'])
        
        # Transform geometry to Mollweide
        project = lambda x, y: self.wgs84_to_moll.transform(x, y)
        geom_moll = transform(project, geom_wgs84)
        
        bbox = self.get_park_bbox(park_id)
        tiles = self.get_tiles_for_bbox(bbox)
        
        logger.info(f"Park {park_id} covers {len(tiles)} tiles: {tiles}")
        
        # Download required tiles
        built_tiles = []
        pop_tiles = []
        
        for row, col in tiles:
            built_path = self.download_tile("GHS_BUILT_S", year, row, col)
            pop_path = self.download_tile("GHS_POP", year, row, col)
            
            if built_path:
                built_tiles.append(built_path)
            if pop_path:
                pop_tiles.append(pop_path)
        
        results = {
            'park_id': park_id,
            'year': year,
            'built_up_km2': 0.0,
            'population_estimate': 0.0,
            'tiles_processed': len(tiles)
        }
        
        # Extract built-up surface
        for tif_path in built_tiles:
            try:
                with rasterio.open(tif_path) as src:
                    # Mask raster with park geometry
                    out_image, out_transform = mask(src, [mapping(geom_moll)], crop=True, nodata=0)
                    
                    # GHS_BUILT_S values are in m² per pixel
                    # Sum and convert to km²
                    built_sum = np.sum(out_image[out_image > 0])
                    results['built_up_km2'] += built_sum / 1_000_000  # m² to km²
                    
            except Exception as e:
                logger.warning(f"Error processing {tif_path}: {e}")
        
        # Extract population
        for tif_path in pop_tiles:
            try:
                with rasterio.open(tif_path) as src:
                    out_image, out_transform = mask(src, [mapping(geom_moll)], crop=True, nodata=-200)
                    
                    # GHS_POP values are population count per pixel
                    # Filter out nodata values (typically -200 or similar)
                    valid_data = out_image[out_image > 0]
                    results['population_estimate'] += np.sum(valid_data)
                    
            except Exception as e:
                logger.warning(f"Error processing {tif_path}: {e}")
        
        # Save to database
        self._save_park_stats(results)
        
        return results
    
    def _save_park_stats(self, stats: Dict):
        """Save park statistics to database"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO park_ghsl_data 
            (park_id, year, built_up_km2, population_estimate, processed_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (stats['park_id'], stats['year'], stats['built_up_km2'], stats['population_estimate']))
        
        conn.commit()
        conn.close()
        logger.info(f"Saved stats for {stats['park_id']}: {stats}")
    
    # ==================== BATCH PROCESSING ====================
    
    def get_all_required_tiles(self, year: int = 2020) -> List[Tuple[int, int]]:
        """Get all unique tiles needed to cover all keystones."""
        all_tiles = set()
        
        for park in self.keystones:
            if 'geometry' not in park:
                continue
            bbox = self.get_park_bbox(park['id'])
            if bbox:
                tiles = self.get_tiles_for_bbox(bbox)
                all_tiles.update(tiles)
        
        return sorted(list(all_tiles))
    
    def process_all_parks(self, year: int = 2020, limit: int = None):
        """Process all keystone parks."""
        parks = self.keystones[:limit] if limit else self.keystones
        
        for i, park in enumerate(parks):
            if 'geometry' not in park:
                logger.warning(f"Skipping {park['id']} - no geometry")
                continue
            
            logger.info(f"Processing {i+1}/{len(parks)}: {park['id']}")
            try:
                stats = self.extract_stats_for_park(park['id'], year)
                logger.info(f"  Built-up: {stats.get('built_up_km2', 0):.2f} km²")
                logger.info(f"  Population: {stats.get('population_estimate', 0):.0f}")
            except Exception as e:
                logger.error(f"  Failed: {e}")


# ==================== CLI ====================

def main():
    import argparse
    parser = argparse.ArgumentParser(description='GHSL Data Manager')
    subparsers = parser.add_subparsers(dest='command')
    
    # List tiles needed
    tiles_cmd = subparsers.add_parser('tiles', help='List tiles needed for keystones')
    tiles_cmd.add_argument('--year', type=int, default=2020)
    
    # Process single park
    park_cmd = subparsers.add_parser('park', help='Process a single park')
    park_cmd.add_argument('park_id', help='Park ID')
    park_cmd.add_argument('--year', type=int, default=2020)
    
    # Process all parks
    all_cmd = subparsers.add_parser('all', help='Process all parks')
    all_cmd.add_argument('--year', type=int, default=2020)
    all_cmd.add_argument('--limit', type=int, help='Limit number of parks')
    
    # Test with a specific bbox
    test_cmd = subparsers.add_parser('test', help='Test with a bbox')
    
    # Info about a park (no download)
    info_cmd = subparsers.add_parser('info', help='Show tile info for a park')
    info_cmd.add_argument('park_id', help='Park ID')
    
    args = parser.parse_args()
    manager = GHSLDataManager()
    
    if args.command == 'tiles':
        tiles = manager.get_all_required_tiles(args.year)
        print(f"Total unique tiles needed: {len(tiles)}")
        print(f"Tiles: {tiles[:20]}..." if len(tiles) > 20 else f"Tiles: {tiles}")
    
    elif args.command == 'park':
        stats = manager.extract_stats_for_park(args.park_id, args.year)
        print(json.dumps(stats, indent=2))
    
    elif args.command == 'all':
        manager.process_all_parks(args.year, args.limit)
    
    elif args.command == 'test':
        # Test tile calculation and database without downloading
        print("Testing GHSL tile calculation (no download)...")
        test_parks = ['CAF_Dzanga_Park', 'CAF_Chinko', 'KEN_Tsavo']
        for park_id in test_parks:
            bbox = manager.get_park_bbox(park_id)
            if bbox:
                tiles = manager.get_tiles_for_bbox(bbox)
                print(f"\n{park_id}:")
                print(f"  BBox: {bbox}")
                print(f"  Tiles: {len(tiles)} - {tiles[:4]}{'...' if len(tiles) > 4 else ''}")
        
        # Show summary
        all_tiles = manager.get_all_required_tiles(2020)
        print(f"\nTotal tiles for all 162 keystones: {len(all_tiles)}")
        print("\nNote: JRC server may be slow/blocked. For production, pre-download tiles.")
        print("Download manually: wget [url] -O tile.zip")
    
    elif args.command == 'info':
        # Show tile info without downloading
        park_id = args.park_id
        bbox = manager.get_park_bbox(park_id)
        if bbox:
            tiles = manager.get_tiles_for_bbox(bbox)
            moll_bbox = manager.bbox_to_mollweide(bbox)
            print(f"Park: {park_id}")
            print(f"WGS84 bbox: {bbox}")
            print(f"Mollweide bbox: {moll_bbox}")
            print(f"Tiles needed: {len(tiles)}")
            print(f"Tile list: {tiles}")
            # Show URLs
            print(f"\nSample download URLs for year 2020:")
            for r, c in tiles[:2]:
                print(f"  Built-up: {manager.get_tile_url('GHS_BUILT_S', 2020, r, c)}")
                print(f"  Population: {manager.get_tile_url('GHS_POP', 2020, r, c)}")
        else:
            print(f"Park {park_id} not found or has no geometry")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
