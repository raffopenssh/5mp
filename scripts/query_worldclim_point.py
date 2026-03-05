#!/usr/bin/env python3
"""
Query WorldClim precipitation data for a single point on-demand.
Returns JSON with monthly precipitation values.
"""
import sys
import json
import os
from osgeo import gdal

def get_precip_at_point(tif_path, lon, lat):
    """Get precipitation value at a specific lat/lon from GeoTIFF"""
    ds = gdal.Open(tif_path)
    if ds is None:
        return None
    
    gt = ds.GetGeoTransform()
    x = int((lon - gt[0]) / gt[1])
    y = int((lat - gt[3]) / gt[5])
    
    if x < 0 or x >= ds.RasterXSize or y < 0 or y >= ds.RasterYSize:
        ds = None
        return None
    
    band = ds.GetRasterBand(1)
    value = band.ReadAsArray(x, y, 1, 1)[0, 0]
    ds = None
    
    return float(value) if value > -9999 else None

def main():
    if len(sys.argv) != 3:
        print(json.dumps({"error": "Usage: query_worldclim_point.py <lat> <lon>"}))
        sys.exit(1)
    
    try:
        lat = float(sys.argv[1])
        lon = float(sys.argv[2])
    except ValueError:
        print(json.dumps({"error": "Invalid lat/lon values"}))
        sys.exit(1)
    
    # Path to WorldClim ZIP
    script_dir = os.path.dirname(os.path.abspath(__file__))
    zip_path = os.path.join(script_dir, '../data/worldclim/wc2.1_2.5m_prec.zip')
    abs_zip_path = os.path.abspath(zip_path)
    
    if not os.path.exists(abs_zip_path):
        print(json.dumps({"error": f"WorldClim ZIP not found: {abs_zip_path}"}))
        sys.exit(1)
    
    # Query each month
    monthly_precip = []
    for month in range(1, 13):
        tif_name = f'wc2.1_2.5m_prec_{month:02d}.tif'
        tif_path = f'/vsizip/{abs_zip_path}/{tif_name}'
        
        precip = get_precip_at_point(tif_path, lon, lat)
        monthly_precip.append(precip if precip is not None else 0)
    
    # Return JSON result
    print(json.dumps({"precip": monthly_precip}))

if __name__ == '__main__':
    # Suppress GDAL warnings
    gdal.UseExceptions()
    try:
        main()
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
