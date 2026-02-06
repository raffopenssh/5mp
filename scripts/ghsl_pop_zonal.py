#!/usr/bin/env python3
"""GHSL Population - Proper Zonal Statistics

Uses settlement area_m2 to define sampling region around centroid.
Sums all population pixels within the settlement footprint.

GHSL POP raster: 100m pixels, values are absolute population count per pixel.
"""

import argparse
import sqlite3
import sys
import math
from pathlib import Path

try:
    import rasterio
    from rasterio.windows import Window
    from pyproj import Transformer
    import numpy as np
except ImportError as e:
    print(f"Missing: {e}. Run: pip install rasterio pyproj numpy")
    exit(1)

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"

WGS84 = "EPSG:4326"
MOLLWEIDE = "ESRI:54009"
PIXEL_SIZE_M = 100  # GHSL resolution


def get_vsizip_path(zip_path):
    """Get GDAL vsizip path to TIF inside ZIP."""
    return f"/vsizip/{zip_path}/GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif"


def get_population_zonal(dataset, lat, lon, area_m2, transformer, inv_transform):
    """Get population using zonal statistics based on settlement area.
    
    Approximates settlement as square region centered on centroid.
    Sums all population pixels within that region.
    """
    try:
        # Transform centroid to Mollweide
        x, y = transformer.transform(lon, lat)
        
        # Convert to pixel coordinates
        col, row = inv_transform * (x, y)
        col, row = int(col), int(row)
        
        # Calculate window size based on area
        # area_m2 -> side length -> pixels
        # Assume square: side = sqrt(area)
        side_m = math.sqrt(area_m2) if area_m2 > 0 else 100
        radius_pixels = max(1, int(side_m / PIXEL_SIZE_M / 2))
        
        # Create window around centroid
        col_off = max(0, col - radius_pixels)
        row_off = max(0, row - radius_pixels)
        width = min(radius_pixels * 2 + 1, dataset.width - col_off)
        height = min(radius_pixels * 2 + 1, dataset.height - row_off)
        
        if width <= 0 or height <= 0:
            return 0.0
        
        # Check bounds
        if col_off >= dataset.width or row_off >= dataset.height:
            return 0.0
        
        window = Window(col_off, row_off, width, height)
        data = dataset.read(1, window=window)
        
        # Handle nodata (-200)
        nodata = dataset.nodata
        if nodata is not None:
            data = np.where(data == nodata, 0, data)
        
        # Sum all positive population values
        pop = float(data[data > 0].sum()) if (data > 0).any() else 0.0
        return pop
        
    except Exception as e:
        print(f"    Error at {lat},{lon}: {e}")
        return 0.0


def process_park(conn, dataset, park_id, transformer, inv_transform):
    """Process all settlements for a park."""
    cursor = conn.execute('''
        SELECT id, lat, lon, area_m2 FROM park_settlements WHERE park_id = ?
    ''', (park_id,))
    settlements = list(cursor)
    
    if not settlements:
        print(f"  No settlements for {park_id}")
        return 0
    
    print(f"  Processing {len(settlements)} settlements...")
    
    processed = 0
    total_pop = 0.0
    
    for s_id, s_lat, s_lon, area_m2 in settlements:
        pop = get_population_zonal(dataset, s_lat, s_lon, area_m2 or 10000, transformer, inv_transform)
        
        # Round to nearest integer for storage
        pop_int = int(round(pop))
        
        conn.execute('''
            UPDATE park_settlements SET population_est = ? WHERE id = ?
        ''', (pop_int, s_id))
        
        total_pop += pop
        processed += 1
        
        if processed % 100 == 0:
            conn.commit()
            print(f"    {processed}/{len(settlements)} ({total_pop:,.0f} total pop)")
    
    conn.commit()
    print(f"  {park_id}: {processed} settlements, {total_pop:,.0f} total population")
    return processed


def get_all_park_ids(conn):
    """Get list of all park IDs with settlements."""
    cursor = conn.execute('SELECT DISTINCT park_id FROM park_settlements ORDER BY park_id')
    return [row[0] for row in cursor]


def main():
    sys.stdout.reconfigure(line_buffering=True)
    
    parser = argparse.ArgumentParser(description='GHSL Population - Zonal Statistics')
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
    
    conn = sqlite3.connect(DB_PATH)
    
    if args.park:
        park_ids = [args.park]
    else:
        park_ids = get_all_park_ids(conn)
    
    print(f"Parks to process: {len(park_ids)}")
    
    if args.dry_run:
        # Show sample area sizes
        cursor = conn.execute('''
            SELECT park_id, AVG(area_m2), MIN(area_m2), MAX(area_m2), COUNT(*)
            FROM park_settlements GROUP BY park_id LIMIT 10
        ''')
        print("\nSample area stats (m²):")
        for row in cursor:
            print(f"  {row[0]}: avg={row[1]:.0f}, min={row[2]:.0f}, max={row[3]:.0f}, n={row[4]}")
        return 0
    
    transformer = Transformer.from_crs(WGS84, MOLLWEIDE, always_xy=True)
    
    try:
        with rasterio.open(vsizip_path) as dataset:
            print(f"Raster: {dataset.width}x{dataset.height}, {PIXEL_SIZE_M}m pixels")
            print(f"Bounds: {dataset.bounds}")
            
            inv_transform = ~dataset.transform
            
            total_processed = 0
            for i, park_id in enumerate(park_ids):
                print(f"\n[{i+1}/{len(park_ids)}] {park_id}")
                count = process_park(conn, dataset, park_id, transformer, inv_transform)
                total_processed += count
            
            print(f"\n{'='*50}")
            print(f"Total settlements processed: {total_processed}")
    
    except Exception as e:
        print(f"Error opening raster: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    conn.close()
    print("Done!")
    return 0


if __name__ == '__main__':
    exit(main())
