#!/usr/bin/env python3
"""GHSL Population Integration - Memory Efficient Version

Reads GHSL POP data using GDAL's /vsizip/ virtual filesystem.
No extraction needed - reads directly from ZIP with windowed reads.

Usage:
    python scripts/ghsl_pop_processor_v2.py --zip data/ghsl_pop_2030_full.zip --park CAF_Chinko
    python scripts/ghsl_pop_processor_v2.py --zip data/ghsl_pop_2030_full.zip --all
"""

import argparse
import sqlite3
import math
from pathlib import Path

try:
    import rasterio
    from rasterio.windows import Window
    from pyproj import Transformer
except ImportError as e:
    print(f"Missing: {e}. Run: pip install rasterio pyproj")
    exit(1)

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"

# GHSL uses Mollweide projection (ESRI:54009)
WGS84 = "EPSG:4326"
MOLLWEIDE = "ESRI:54009"

# Sampling window size in pixels (100m per pixel, so 5 = 500m radius)
SAMPLE_RADIUS_PIXELS = 5


def get_vsizip_path(zip_path):
    """Get GDAL vsizip path to TIF inside ZIP."""
    return f"/vsizip/{zip_path}/GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif"


def latlon_to_mollweide(lat, lon):
    """Transform WGS84 lat/lon to Mollweide x/y."""
    transformer = Transformer.from_crs(WGS84, MOLLWEIDE, always_xy=True)
    x, y = transformer.transform(lon, lat)
    return x, y


def get_population_at_point(dataset, lat, lon, transformer_to_moll, radius_pixels=SAMPLE_RADIUS_PIXELS):
    """Get population estimate at a point using windowed read.
    
    Returns sum of population in a small window around the point.
    """
    try:
        # Transform to Mollweide
        x, y = transformer_to_moll.transform(lon, lat)
        
        # Convert to pixel coordinates
        # transform is (a, b, c, d, e, f) where:
        # x_geo = a + col * b + row * c
        # y_geo = d + col * e + row * f
        # For standard north-up: b=pixel_width, f=-pixel_height, c=e=0
        inv_transform = ~dataset.transform
        col, row = inv_transform * (x, y)
        
        col = int(col)
        row = int(row)
        
        # Check bounds
        if col < 0 or col >= dataset.width or row < 0 or row >= dataset.height:
            return 0
        
        # Create window around point
        col_off = max(0, col - radius_pixels)
        row_off = max(0, row - radius_pixels)
        width = min(radius_pixels * 2 + 1, dataset.width - col_off)
        height = min(radius_pixels * 2 + 1, dataset.height - row_off)
        
        if width <= 0 or height <= 0:
            return 0
        
        window = Window(col_off, row_off, width, height)
        
        # Read just this small window
        data = dataset.read(1, window=window)
        
        # Handle nodata
        nodata = dataset.nodata
        if nodata is not None:
            data = data[data != nodata]
        
        # Sum population (values are people per pixel)
        pop = int(data[data > 0].sum()) if len(data) > 0 else 0
        return pop
        
    except Exception as e:
        return 0


def process_park(conn, dataset, park_id, transformer):
    """Process all settlements for a park."""
    cursor = conn.execute('''
        SELECT id, lat, lon FROM park_settlements WHERE park_id = ?
    ''', (park_id,))
    settlements = list(cursor)
    
    if not settlements:
        print(f"  No settlements for {park_id}")
        return 0
    
    print(f"  Processing {len(settlements)} settlements...")
    
    processed = 0
    total_pop = 0
    
    for s_id, s_lat, s_lon in settlements:
        pop = get_population_at_point(dataset, s_lat, s_lon, transformer)
        
        conn.execute('''
            UPDATE park_settlements SET population_2030 = ? WHERE id = ?
        ''', (pop, s_id))
        
        total_pop += pop
        processed += 1
        
        if processed % 100 == 0:
            conn.commit()
            print(f"    {processed}/{len(settlements)} ({total_pop:,} total pop)")
    
    conn.commit()
    print(f"  {park_id}: {processed} settlements, {total_pop:,} total population")
    return processed


def get_all_park_ids(conn):
    """Get list of all park IDs with settlements."""
    cursor = conn.execute('SELECT DISTINCT park_id FROM park_settlements ORDER BY park_id')
    return [row[0] for row in cursor]


def ensure_column_exists(conn):
    """Ensure population_2030 column exists."""
    try:
        conn.execute('ALTER TABLE park_settlements ADD COLUMN population_2030 INTEGER')
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists


def main():
    parser = argparse.ArgumentParser(description='Process GHSL population data (memory efficient)')
    parser.add_argument('--zip', required=True, help='Path to GHSL POP ZIP file')
    parser.add_argument('--park', help='Specific park ID')
    parser.add_argument('--all', action='store_true', help='Process all parks')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    
    args = parser.parse_args()
    
    if not args.park and not args.all:
        parser.error('Either --park or --all required')
    
    zip_path = Path(args.zip)
    if not zip_path.exists():
        print(f"Error: ZIP file not found: {zip_path}")
        return 1
    
    vsizip_path = get_vsizip_path(zip_path)
    print(f"Opening: {vsizip_path}")
    
    # Open database
    conn = sqlite3.connect(DB_PATH)
    ensure_column_exists(conn)
    
    if args.park:
        park_ids = [args.park]
    else:
        park_ids = get_all_park_ids(conn)
    
    print(f"Parks to process: {len(park_ids)}")
    
    if args.dry_run:
        for pid in park_ids[:10]:
            print(f"  {pid}")
        if len(park_ids) > 10:
            print(f"  ... and {len(park_ids) - 10} more")
        return 0
    
    # Create transformer once (reuse for all points)
    transformer = Transformer.from_crs(WGS84, MOLLWEIDE, always_xy=True)
    
    # Open raster via vsizip (doesn't load into memory)
    try:
        with rasterio.open(vsizip_path) as dataset:
            print(f"Raster: {dataset.width}x{dataset.height}, CRS: {dataset.crs}")
            print(f"Bounds: {dataset.bounds}")
            
            total_processed = 0
            for i, park_id in enumerate(park_ids):
                print(f"\n[{i+1}/{len(park_ids)}] {park_id}")
                count = process_park(conn, dataset, park_id, transformer)
                total_processed += count
            
            print(f"\n{'='*50}")
            print(f"Total settlements processed: {total_processed}")
    
    except Exception as e:
        print(f"Error opening raster: {e}")
        return 1
    
    conn.close()
    print("Done!")
    return 0


if __name__ == '__main__':
    exit(main())
