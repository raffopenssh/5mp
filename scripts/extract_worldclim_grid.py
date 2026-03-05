#!/usr/bin/env python3
"""
Extract WorldClim precipitation data for grid cells.
Creates a simple JSON lookup: grid_cell_id -> [monthly precipitation mm]
"""
import zipfile
import numpy as np
from osgeo import gdal
import json
import sqlite3

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

def main():
    db_path = '../db.sqlite3'
    zip_path = '../data/worldclim/wc2.1_2.5m_prec.zip'
    output_path = '../data/worldclim/grid_precip.json'
    
    # Get all grid cells
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT id, lat_center, lon_center FROM grid_cells")
    grid_cells = cursor.fetchall()
    print(f"Found {len(grid_cells)} grid cells")
    
    # Extract monthly precipitation for each grid cell
    grid_precip = {}
    
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for grid_id, lat, lon in grid_cells:
            monthly_precip = []
            
            for month in range(1, 13):
                tif_name = f'wc2.1_2.5m_prec_{month:02d}.tif'
                
                # Extract to temp location
                zf.extract(tif_name, '/tmp/')
                tif_path = f'/tmp/{tif_name}'
                
                # Get precipitation at this point
                precip = get_precip_at_point(tif_path, lon, lat)
                monthly_precip.append(precip if precip is not None else 0)
            
            grid_precip[grid_id] = monthly_precip
            
            if len(grid_precip) % 100 == 0:
                print(f"Processed {len(grid_precip)} grid cells...")
    
    # Save to JSON
    with open(output_path, 'w') as f:
        json.dump(grid_precip, f)
    
    print(f"Saved precipitation data for {len(grid_precip)} grid cells to {output_path}")

if __name__ == '__main__':
    main()
