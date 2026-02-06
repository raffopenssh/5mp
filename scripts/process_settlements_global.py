#!/usr/bin/env python3
"""
Process GHSL Population data to extract settlement polygons.

Uses GDAL vsizip to read directly from ZIP, with windowed reads per park.
Generates polygon geometries from population raster data.

Usage:
    python scripts/process_settlements_global.py
    python scripts/process_settlements_global.py --park COD_Virunga
"""

import json
import sqlite3
import numpy as np
import sys
import os
import argparse
from pathlib import Path
from datetime import datetime

# Force unbuffered output
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)

try:
    import rasterio
    from rasterio.windows import from_bounds, Window
    from rasterio.features import shapes
    from shapely.geometry import shape, mapping
    from shapely.ops import transform as shapely_transform
    from pyproj import Transformer
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install rasterio shapely pyproj")
    sys.exit(1)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
GHSL_ZIP = DATA_DIR / "ghsl" / "ghsl_pop_2030.zip"
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_PATH = DATA_DIR / "keystones_with_boundaries.json"

# GHSL uses Mollweide projection
MOLLWEIDE = "ESRI:54009"
WGS84 = "EPSG:4326"

# Population threshold (people per 100m pixel)
POP_THRESHOLD = 1  # At least 1 person per pixel to be considered settlement

# Minimum area threshold
MIN_AREA_M2 = 10000  # 1 hectare minimum

def get_vsizip_path():
    """Get GDAL vsizip path to TIF inside ZIP."""
    return f"/vsizip/{GHSL_ZIP}/GHS_POP_E2030_GLOBE_R2023A_54009_100_V1_0.tif"

def load_keystones():
    """Load keystones with boundaries."""
    with open(KEYSTONES_PATH, 'r') as f:
        return json.load(f)

def transform_to_mollweide(geom):
    """Transform geometry from WGS84 to Mollweide."""
    transformer = Transformer.from_crs(WGS84, MOLLWEIDE, always_xy=True)
    return shapely_transform(lambda x, y: transformer.transform(x, y), geom)

def transform_to_wgs84(geom):
    """Transform geometry from Mollweide to WGS84."""
    transformer = Transformer.from_crs(MOLLWEIDE, WGS84, always_xy=True)
    return shapely_transform(lambda x, y: transformer.transform(x, y), geom)

def process_park(dataset, park, conn):
    """Process settlements for a single park using windowed read."""
    park_id = park['id']
    
    if not park.get('geometry'):
        return 0
    
    park_geom = shape(park['geometry'])
    park_moll = transform_to_mollweide(park_geom)
    
    try:
        minx, miny, maxx, maxy = park_moll.bounds
        
        # Create window from park bounds
        try:
            window = from_bounds(minx, miny, maxx, maxy, dataset.transform)
        except Exception as e:
            print(f"    {park_id}: bounds error - {e}")
            return 0
        
        # Clamp window to raster bounds
        window = window.intersection(Window(0, 0, dataset.width, dataset.height))
        
        if window.width < 1 or window.height < 1:
            return 0
        
        # Ensure integer dimensions
        col_off = int(window.col_off)
        row_off = int(window.row_off)
        width = int(window.width)
        height = int(window.height)
        window = Window(col_off, row_off, width, height)
        
        # Read windowed data
        data = dataset.read(1, window=window)
        window_transform = dataset.window_transform(window)
        
        # Handle nodata
        nodata = dataset.nodata
        if nodata is not None:
            data = np.where(data == nodata, 0, data)
        
        # Threshold: population > threshold
        binary = (data > POP_THRESHOLD).astype(np.uint8)
        
        if binary.sum() == 0:
            return 0
        
        # Vectorize
        features_added = 0
        cur = conn.cursor()
        
        for geom_dict, value in shapes(binary, mask=binary > 0, transform=window_transform):
            if value == 0:
                continue
            
            poly = shape(geom_dict)
            
            # Clip to park boundary
            try:
                clipped = poly.intersection(park_moll)
                if clipped.is_empty:
                    continue
            except Exception:
                continue
            
            # Area in Mollweide (meters)
            area_m2 = clipped.area
            if area_m2 < MIN_AREA_M2:
                continue
            
            # Get population sum for this polygon
            # Create a mask for this polygon to sum population
            try:
                from rasterio.features import geometry_mask
                mask = geometry_mask([geom_dict], transform=window_transform, 
                                    out_shape=data.shape, invert=True)
                pop_sum = int(data[mask].sum())
            except:
                pop_sum = int(area_m2 / 10000 * 50)  # Fallback: 50 per hectare
            
            # Transform to WGS84
            clipped_wgs84 = transform_to_wgs84(clipped)
            
            # Simplify
            simplified = clipped_wgs84.simplify(0.0001, preserve_topology=True)
            if simplified.is_empty:
                continue
            
            centroid = simplified.centroid
            
            feature_id = f"settlement_{park_id}_{features_added}"
            geojson = json.dumps(mapping(simplified))
            
            properties = {
                "area_m2": round(area_m2, 2),
                "population_est": pop_sum,
                "lat": round(centroid.y, 6),
                "lon": round(centroid.x, 6)
            }
            
            cur.execute("""
                INSERT OR REPLACE INTO feature_geometries
                (feature_type, feature_id, park_id, geojson, properties_json,
                 bbox_minx, bbox_miny, bbox_maxx, bbox_maxy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                'settlement', feature_id, park_id,
                geojson, json.dumps(properties),
                simplified.bounds[0], simplified.bounds[1],
                simplified.bounds[2], simplified.bounds[3]
            ))
            
            features_added += 1
        
        conn.commit()
        return features_added
        
    except Exception as e:
        print(f"    {park_id}: Error - {e}")
        import traceback
        traceback.print_exc()
        return 0

def main():
    parser = argparse.ArgumentParser(description='Process GHSL settlements')
    parser.add_argument('--park', help='Process single park')
    parser.add_argument('--continue-from', help='Continue from park ID')
    args = parser.parse_args()
    
    print(f"=== GHSL Settlement Processor (Global TIF) ===")
    print(f"Started: {datetime.now()}")
    print()
    
    if not GHSL_ZIP.exists():
        print(f"ERROR: GHSL ZIP not found at {GHSL_ZIP}")
        return 1
    
    vsizip_path = get_vsizip_path()
    print(f"Opening: {vsizip_path}")
    
    # Load keystones
    keystones = load_keystones()
    print(f"Loaded {len(keystones)} parks")
    
    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    
    # Clear existing settlement polygons (only if not doing single park)
    if not args.park and not args.continue_from:
        print("Clearing existing settlement features...")
        conn.execute("DELETE FROM feature_geometries WHERE feature_type = 'settlement'")
        conn.commit()
    
    # Filter parks if needed
    if args.park:
        keystones = [p for p in keystones if p['id'] == args.park]
        if not keystones:
            print(f"Park not found: {args.park}")
            return 1
    elif args.continue_from:
        found = False
        new_keystones = []
        for p in keystones:
            if p['id'] == args.continue_from:
                found = True
            if found:
                new_keystones.append(p)
        keystones = new_keystones
        print(f"Continuing from {args.continue_from}, {len(keystones)} parks remaining")
    
    total_features = 0
    total_parks = 0
    
    try:
        with rasterio.open(vsizip_path) as dataset:
            print(f"Raster: {dataset.width}x{dataset.height}")
            print(f"CRS: {dataset.crs}")
            print()
            
            for i, park in enumerate(keystones):
                park_id = park['id']
                
                features = process_park(dataset, park, conn)
                
                if features > 0:
                    print(f"[{i+1}/{len(keystones)}] {park_id}: {features} polygons")
                    total_features += features
                    total_parks += 1
                elif (i + 1) % 20 == 0:
                    print(f"[{i+1}/{len(keystones)}] Progress checkpoint...")
                    
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    conn.close()
    
    print()
    print(f"=== Complete ===")
    print(f"Parks with settlements: {total_parks}")
    print(f"Total polygons: {total_features}")
    print(f"Finished: {datetime.now()}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
