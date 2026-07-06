#!/usr/bin/env python3
"""
Extract WorldClim precipitation data for grid cells.
Creates a simple JSON lookup: grid_cell_id -> [monthly precipitation mm]

Strategy:
1. Pre-generate all Africa grid cells (0.5° resolution)
2. Service will fall back to on-demand lookup for cells outside Africa

Uses GDAL's /vsizip/ virtual file system to read directly from ZIP without extraction.
"""
import numpy as np
from osgeo import gdal
import json
import sqlite3
import os

def get_precip_at_point(tif_path, lon, lat):
    """Get precipitation value at a specific lat/lon from GeoTIFF"""
    ds = gdal.Open(tif_path)
    if ds is None:
        return None
    
    # Get geotransform
    gt = ds.GetGeoTransform()
    
    # Convert lat/lon to pixel coordinates
    x = int((lon - gt[0]) / gt[1])
    y = int((lat - gt[3]) / gt[5])
    
    # Check bounds
    if x < 0 or x >= ds.RasterXSize or y < 0 or y >= ds.RasterYSize:
        ds = None
        return None
    
    # Read pixel value
    band = ds.GetRasterBand(1)
    value = band.ReadAsArray(x, y, 1, 1)[0, 0]
    
    ds = None
    # WorldClim uses -32768 as nodata typically
    return float(value) if value > -9999 else None

def generate_africa_grid_cells(resolution=0.5):
    """Generate all grid cells covering Africa at specified resolution (degrees)"""
    # Africa bounding box (approximate, inclusive of islands)
    # West: Cape Verde (-25°), East: Seychelles (55°)
    # North: Tunisia (37°), South: South Africa (-35°)
    min_lon, max_lon = -25.0, 56.0
    min_lat, max_lat = -35.0, 38.0
    
    grid_cells = []
    
    # Generate grid cell centers at 0.5° resolution
    lon = min_lon + resolution / 2
    while lon < max_lon:
        lat = min_lat + resolution / 2
        while lat < max_lat:
            # Grid cell ID format: "lon_lat" rounded to 1 decimal
            cell_id = f"{lon:.1f}_{lat:.1f}"
            grid_cells.append((cell_id, lat, lon))
            lat += resolution
        lon += resolution
    
    return grid_cells

def main():
    db_path = '../db.sqlite3'
    zip_path = '../data/worldclim/wc2.1_2.5m_prec.zip'
    output_path = '../data/worldclim/grid_precip.json'
    
    # Get existing grid cells from database (for logging)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT id, lat_center, lon_center FROM grid_cells")
        db_grid_cells = cursor.fetchall()
        print(f"Found {len(db_grid_cells)} grid cells in database")
        conn.close()
    except Exception as e:
        print(f"Warning: Could not read database: {e}")
        db_grid_cells = []
    
    # Generate all Africa grid cells at 0.5° resolution
    print("Generating Africa grid cells at 0.5° resolution...")
    africa_grid_cells = generate_africa_grid_cells(resolution=0.5)
    print(f"Generated {len(africa_grid_cells)} grid cells covering Africa")
    
    # Extract monthly precipitation for each grid cell
    grid_precip = {}
    
    # Use absolute path for ZIP file
    abs_zip_path = os.path.abspath(zip_path)
    
    for i, (cell_id, lat, lon) in enumerate(africa_grid_cells):
        monthly_precip = []
        
        for month in range(1, 13):
            tif_name = f'wc2.1_2.5m_prec_{month:02d}.tif'
            
            # Read directly from ZIP using GDAL's virtual file system
            # No extraction to disk needed!
            tif_path = f'/vsizip/{abs_zip_path}/{tif_name}'
            
            # Get precipitation at this point
            precip = get_precip_at_point(tif_path, lon, lat)
            monthly_precip.append(precip if precip is not None else 0)
        
        grid_precip[cell_id] = monthly_precip
        
        if (i + 1) % 500 == 0:
            print(f"Processed {i + 1}/{len(africa_grid_cells)} grid cells...")
    
    # Save to JSON
    with open(output_path, 'w') as f:
        json.dump(grid_precip, f, indent=2)
    
    print(f"\nSaved precipitation data for {len(grid_precip)} grid cells to {output_path}")
    print(f"File size: {os.path.getsize(output_path) / 1024:.1f} KB")
    
    # Report coverage of database grid cells
    if db_grid_cells:
        covered = sum(1 for cell_id, lat, lon in db_grid_cells if cell_id in grid_precip)
        print(f"\nDatabase grid cells covered: {covered}/{len(db_grid_cells)}")

if __name__ == '__main__':
    main()
