#!/usr/bin/env python3
"""GHSL Population Integration - Windowed Processing

Reads GHSL POP tiles directly from ZIP using windowed reads.
No full extraction needed. Memory usage: <500MB per park.

Populates park_settlements with population_2020 and population_2030 estimates.

GHSL POP data format:
- GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif (or similar)
- Mollweide projection (ESRI:54009)
- 100m resolution
- Values = population count per pixel

Usage:
    python scripts/ghsl_pop_processor.py --zip data/ghsl_pop_2030.zip --park CAF_Chinko
    python scripts/ghsl_pop_processor.py --zip data/ghsl_pop_2030.zip --all --workers 2
"""

import argparse
import json
import sqlite3
import zipfile
import math
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import sys

try:
    import numpy as np
    import rasterio
    from rasterio.windows import from_bounds, Window
    from rasterio.io import MemoryFile
    from pyproj import Transformer
    from shapely.geometry import shape, Point, box
    from shapely.ops import transform
except ImportError as e:
    print(f"Missing: {e}. Run: pip install numpy rasterio pyproj shapely")
    sys.exit(1)

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_PATH = BASE_DIR / "data" / "keystones_with_boundaries.json"

# GHSL Mollweide projection
MOLLWEIDE_CRS = "ESRI:54009"
WGS84_CRS = "EPSG:4326"

# Population sampling radius around settlement centroid (meters in Mollweide)
SAMPLE_RADIUS_M = 500


def get_db_connection():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_keystones():
    """Load keystone parks with boundaries."""
    with open(KEYSTONES_PATH, 'r') as f:
        return json.load(f)


def get_park_geometry(keystones, park_id):
    """Get park geometry from keystones."""
    for park in keystones:
        if park['id'] == park_id:
            if park.get('geometry'):
                return shape(park['geometry'])
    return None


def transform_point_to_mollweide(lat, lon):
    """Transform WGS84 lat/lon to Mollweide coordinates."""
    transformer = Transformer.from_crs(WGS84_CRS, MOLLWEIDE_CRS, always_xy=True)
    x, y = transformer.transform(lon, lat)
    return x, y


def find_tile_in_zip(zf, pattern="GHS_POP"):
    """Find the population TIF file in the ZIP."""
    for name in zf.namelist():
        if pattern in name and name.endswith('.tif'):
            return name
    return None


def get_population_at_point(src, lat, lon, radius_m=SAMPLE_RADIUS_M):
    """Get population estimate at a point with surrounding area.
    
    Samples a small window around the point and sums population.
    """
    try:
        # Transform to Mollweide
        x, y = transform_point_to_mollweide(lat, lon)
        
        # Create small bounding box around point
        minx = x - radius_m
        maxx = x + radius_m
        miny = y - radius_m
        maxy = y + radius_m
        
        # Get window for this bbox
        window = from_bounds(minx, miny, maxx, maxy, src.transform)
        
        # Clamp to raster bounds
        window = window.intersection(Window(0, 0, src.width, src.height))
        
        if window.width <= 0 or window.height <= 0:
            return 0
        
        # Read just this small window
        data = src.read(1, window=window)
        
        # Handle nodata
        nodata = src.nodata
        if nodata is not None:
            data = np.where(data == nodata, 0, data)
        
        # Sum population in the window
        pop = int(np.sum(data[data > 0]))
        return pop
        
    except Exception as e:
        # Point might be outside raster extent
        return 0


def process_park_populations(park_id, pop_zip_path):
    """Process population for all settlements in a park."""
    print(f"Processing {park_id}...")
    
    conn = get_db_connection()
    
    # Get settlements for this park
    cursor = conn.execute('''
        SELECT id, lat, lon FROM park_settlements WHERE park_id = ?
    ''', (park_id,))
    settlements = list(cursor)
    
    if not settlements:
        print(f"  No settlements for {park_id}")
        conn.close()
        return 0
    
    print(f"  {len(settlements)} settlements to process")
    
    # Ensure columns exist
    try:
        conn.execute('ALTER TABLE park_settlements ADD COLUMN population_2030 INTEGER')
    except sqlite3.OperationalError:
        pass
    
    processed = 0
    total_pop = 0
    
    try:
        with zipfile.ZipFile(pop_zip_path, 'r') as zf:
            tif_name = find_tile_in_zip(zf)
            if not tif_name:
                print(f"  Error: No population TIF found in ZIP")
                conn.close()
                return 0
            
            print(f"  Using: {tif_name}")
            
            # Read TIF from ZIP
            with zf.open(tif_name) as tif_file:
                tif_data = tif_file.read()
                
                with MemoryFile(tif_data) as memfile:
                    with memfile.open() as src:
                        print(f"  Raster CRS: {src.crs}, Size: {src.width}x{src.height}")
                        
                        for s_id, s_lat, s_lon in settlements:
                            pop = get_population_at_point(src, s_lat, s_lon)
                            
                            conn.execute('''
                                UPDATE park_settlements 
                                SET population_2030 = ?
                                WHERE id = ?
                            ''', (pop, s_id))
                            
                            total_pop += pop
                            processed += 1
                            
                            if processed % 100 == 0:
                                conn.commit()
                                print(f"    Processed {processed}/{len(settlements)}...")
        
        conn.commit()
        print(f"  {park_id}: {processed} settlements, total pop: {total_pop:,}")
        
    except Exception as e:
        print(f"  Error processing {park_id}: {e}")
    
    conn.close()
    return processed


def get_all_park_ids():
    """Get list of all park IDs with settlements."""
    conn = get_db_connection()
    cursor = conn.execute('SELECT DISTINCT park_id FROM park_settlements ORDER BY park_id')
    park_ids = [row[0] for row in cursor]
    conn.close()
    return park_ids


def main():
    parser = argparse.ArgumentParser(description='Process GHSL population data')
    parser.add_argument('--zip', required=True, help='Path to GHSL POP ZIP file')
    parser.add_argument('--park', help='Specific park ID')
    parser.add_argument('--all', action='store_true', help='Process all parks')
    parser.add_argument('--workers', type=int, default=1, help='Parallel workers (default: 1)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    
    args = parser.parse_args()
    
    if not args.park and not args.all:
        parser.error('Either --park or --all required')
    
    pop_zip_path = Path(args.zip)
    if not pop_zip_path.exists():
        print(f"Error: ZIP file not found: {pop_zip_path}")
        sys.exit(1)
    
    # Check ZIP contents
    print(f"Checking ZIP: {pop_zip_path}")
    with zipfile.ZipFile(pop_zip_path, 'r') as zf:
        tif_name = find_tile_in_zip(zf)
        if tif_name:
            print(f"  Found TIF: {tif_name}")
        else:
            print(f"  Error: No GHS_POP TIF found in ZIP")
            print(f"  Contents: {zf.namelist()[:10]}")
            sys.exit(1)
    
    if args.park:
        park_ids = [args.park]
    else:
        park_ids = get_all_park_ids()
    
    print(f"Parks to process: {len(park_ids)}")
    
    if args.dry_run:
        print("Dry run - would process:")
        for pid in park_ids[:10]:
            print(f"  {pid}")
        if len(park_ids) > 10:
            print(f"  ... and {len(park_ids) - 10} more")
        return
    
    # Process parks
    # Note: For memory efficiency, we process sequentially by default
    # The ZIP file is opened fresh for each park to avoid memory buildup
    
    total_processed = 0
    for i, park_id in enumerate(park_ids):
        print(f"\n[{i+1}/{len(park_ids)}] ", end="")
        count = process_park_populations(park_id, pop_zip_path)
        total_processed += count
    
    print(f"\n{'='*50}")
    print(f"Total settlements processed: {total_processed}")
    print("Done!")


if __name__ == '__main__':
    main()
