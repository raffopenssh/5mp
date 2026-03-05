#!/usr/bin/env python3
"""
Extract WorldClim precipitation data for grid cells (OPTIMIZED).
Reads each monthly TIF once and extracts all points.
"""
import numpy as np
from osgeo import gdal
import json
import sqlite3
import os

def extract_precip_for_all_points(tif_path, points):
    """Extract precipitation for multiple points from a single TIF file"""
    ds = gdal.Open(tif_path)
    if ds is None:
        return [None] * len(points)
    
    gt = ds.GetGeoTransform()
    band = ds.GetRasterBand(1)
    
    results = []
    for lon, lat in points:
        # Convert lat/lon to pixel coordinates
        x = int((lon - gt[0]) / gt[1])
        y = int((lat - gt[3]) / gt[5])
        
        # Check bounds
        if x < 0 or x >= ds.RasterXSize or y < 0 or y >= ds.RasterYSize:
            results.append(None)
            continue
        
        # Read pixel value
        try:
            value = band.ReadAsArray(x, y, 1, 1)[0, 0]
            results.append(float(value) if value > -9999 else None)
        except:
            results.append(None)
    
    ds = None
    return results

def generate_africa_grid_cells(resolution=0.5):
    """Generate all grid cells covering Africa at specified resolution (degrees)"""
    min_lon, max_lon = -25.0, 56.0
    min_lat, max_lat = -35.0, 38.0
    
    grid_cells = []
    lon = min_lon + resolution / 2
    while lon < max_lon:
        lat = min_lat + resolution / 2
        while lat < max_lat:
            cell_id = f"{lon:.1f}_{lat:.1f}"
            grid_cells.append((cell_id, lat, lon))
            lat += resolution
        lon += resolution
    
    return grid_cells

def main():
    zip_path = '../data/worldclim/wc2.1_2.5m_prec.zip'
    output_path = '../data/worldclim/grid_precip.json'
    
    print("Generating Africa grid cells at 0.5° resolution...")
    africa_grid_cells = generate_africa_grid_cells(resolution=0.5)
    print(f"Generated {len(africa_grid_cells)} grid cells covering Africa")
    
    # Prepare data structures
    grid_precip = {cell_id: [] for cell_id, lat, lon in africa_grid_cells}
    points = [(lon, lat) for cell_id, lat, lon in africa_grid_cells]
    
    # Use absolute path for ZIP file
    abs_zip_path = os.path.abspath(zip_path)
    
    # Process each month (read TIF once, extract all points)
    for month in range(1, 13):
        tif_name = f'wc2.1_2.5m_prec_{month:02d}.tif'
        tif_path = f'/vsizip/{abs_zip_path}/{tif_name}'
        
        print(f"Processing month {month}/12...")
        monthly_values = extract_precip_for_all_points(tif_path, points)
        
        # Assign values to grid cells
        for i, (cell_id, lat, lon) in enumerate(africa_grid_cells):
            precip = monthly_values[i]
            grid_precip[cell_id].append(precip if precip is not None else 0)
    
    # Save to JSON
    with open(output_path, 'w') as f:
        json.dump(grid_precip, f, indent=2)
    
    print(f"\nSaved precipitation data for {len(grid_precip)} grid cells to {output_path}")
    print(f"File size: {os.path.getsize(output_path) / 1024:.1f} KB")

if __name__ == '__main__':
    main()
