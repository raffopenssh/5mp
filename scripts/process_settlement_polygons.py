#!/usr/bin/env python3
"""
Process GHSL Built-up Surface data to extract settlement polygons.

Uses windowed reads and processes tiles from ZIP without full extraction.
Generates actual polygon geometries from raster data.

Usage:
    python scripts/process_settlement_polygons.py
"""

import json
import sqlite3
import numpy as np
import sys
import os
import zipfile
import tempfile
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Force unbuffered output
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)

try:
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.features import shapes
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union
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

# GHSL Mollweide projection
MOLLWEIDE = "ESRI:54009"
WGS84 = "EPSG:4326"

# Minimum area threshold
MIN_AREA_M2 = 5000  # 5000 m2 = 0.5 hectare

def load_keystones():
    """Load keystones with boundaries."""
    with open(KEYSTONES_PATH, 'r') as f:
        return json.load(f)

def transform_park_to_mollweide(park_geom):
    """Transform park geometry from WGS84 to Mollweide."""
    transformer = Transformer.from_crs(WGS84, MOLLWEIDE, always_xy=True)
    from shapely.ops import transform
    return transform(lambda x, y: transformer.transform(x, y), park_geom)

def transform_geom_to_wgs84(geom):
    """Transform geometry from Mollweide to WGS84."""
    transformer = Transformer.from_crs(MOLLWEIDE, WGS84, always_xy=True)
    from shapely.ops import transform
    return transform(lambda x, y: transformer.transform(x, y), geom)

def get_tile_for_park(park):
    """Determine which GHSL tile(s) a park intersects."""
    # GHSL tiles are 1000km x 1000km in Mollweide
    # Grid origin: (-18041000, 9000000)
    TILE_SIZE = 1000000
    ORIGIN_X = -18041000
    ORIGIN_Y = 9000000
    
    park_geom = shape(park['geometry'])
    park_moll = transform_park_to_mollweide(park_geom)
    
    minx, miny, maxx, maxy = park_moll.bounds
    
    tiles = set()
    # Find all tiles that intersect
    col_start = int((minx - ORIGIN_X) // TILE_SIZE)
    col_end = int((maxx - ORIGIN_X) // TILE_SIZE) + 1
    row_start = int((ORIGIN_Y - maxy) // TILE_SIZE)
    row_end = int((ORIGIN_Y - miny) // TILE_SIZE) + 1
    
    for row in range(row_start, row_end):
        for col in range(col_start, col_end):
            tiles.add((row, col))
    
    return list(tiles)

def process_park_from_tile(zip_path, tile_name, park, conn, temp_dir):
    """Process a park's settlements from a single GHSL tile."""
    park_id = park['id']
    park_geom = shape(park['geometry'])
    park_moll = transform_park_to_mollweide(park_geom)
    
    try:
        # Extract tile from ZIP
        with zipfile.ZipFile(zip_path, 'r') as zf:
            matching = [n for n in zf.namelist() if tile_name in n and n.endswith('.tif')]
            if not matching:
                return 0
            
            tile_file = matching[0]
            tile_path = Path(temp_dir) / tile_file.split('/')[-1]
            
            with zf.open(tile_file) as zf_file:
                with open(tile_path, 'wb') as f:
                    f.write(zf_file.read())
        
        with rasterio.open(tile_path) as src:
            # Get park bounds in Mollweide
            minx, miny, maxx, maxy = park_moll.bounds
            
            # Create window from bounds
            try:
                window = from_bounds(minx, miny, maxx, maxy, src.transform)
            except Exception:
                return 0
            
            # Clamp window
            window = window.intersection(rasterio.windows.Window(
                0, 0, src.width, src.height
            ))
            
            if window.width < 1 or window.height < 1:
                return 0
            
            # Read windowed data
            data = src.read(1, window=window)
            transform = src.window_transform(window)
            
            # GHSL data: values represent built-up area in m2 per pixel
            # Threshold to binary
            threshold = 50  # m2 minimum built-up per pixel
            binary = (data > threshold).astype(np.uint8)
            
            if binary.sum() == 0:
                return 0
            
            # Vectorize to polygons
            total_features = 0
            cur = conn.cursor()
            
            for geom_dict, value in shapes(binary, mask=binary > 0, transform=transform):
                if value == 0:
                    continue
                
                poly = shape(geom_dict)
                
                # Clip to park boundary (in Mollweide)
                try:
                    clipped = poly.intersection(park_moll)
                    if clipped.is_empty:
                        continue
                except Exception:
                    continue
                
                # Calculate area in Mollweide (already in meters)
                area_m2 = clipped.area
                
                if area_m2 < MIN_AREA_M2:
                    continue
                
                # Transform back to WGS84
                clipped_wgs84 = transform_geom_to_wgs84(clipped)
                
                # Simplify for storage
                simplified = clipped_wgs84.simplify(0.0001, preserve_topology=True)
                
                if simplified.is_empty:
                    continue
                
                # Get centroid
                centroid = simplified.centroid
                
                # Estimate population (rough: 200 people per hectare urban density)
                area_ha = area_m2 / 10000
                pop_est = int(area_ha * 200)
                
                # Create feature
                feature_id = f"settlement_{park_id}_{total_features}"
                geojson = json.dumps(mapping(simplified))
                
                properties = {
                    "area_m2": round(area_m2, 2),
                    "population_est": pop_est,
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
                
                total_features += 1
            
            conn.commit()
            return total_features
            
    except Exception as e:
        print(f"    {park_id}: Error - {e}")
        return 0
    finally:
        # Clean up temp file
        if tile_path.exists():
            tile_path.unlink()

def main():
    print(f"=== GHSL Settlement Polygon Processor ===")
    print(f"Started: {datetime.now()}")
    print()
    
    # Check for GHSL ZIP
    if not GHSL_ZIP.exists():
        print(f"ERROR: GHSL ZIP not found at {GHSL_ZIP}")
        return 1
    
    print(f"GHSL ZIP: {GHSL_ZIP}")
    
    # List tiles in ZIP
    with zipfile.ZipFile(GHSL_ZIP, 'r') as zf:
        tif_files = [n for n in zf.namelist() if n.endswith('.tif')]
        print(f"Found {len(tif_files)} TIF files in ZIP")
    
    # Load keystones
    keystones = load_keystones()
    print(f"Loaded {len(keystones)} parks")
    
    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    
    # Clear existing settlement polygons
    print("Clearing existing settlement features...")
    conn.execute("DELETE FROM feature_geometries WHERE feature_type = 'settlement'")
    conn.commit()
    
    total_features = 0
    total_parks = 0
    
    # Create temp directory
    with tempfile.TemporaryDirectory() as temp_dir:
        # Process each park
        for i, park in enumerate(keystones):
            if not park.get('geometry'):
                continue
            
            park_id = park['id']
            tiles = get_tile_for_park(park)
            
            park_features = 0
            for row, col in tiles:
                tile_name = f"R{row}_C{col}"
                features = process_park_from_tile(GHSL_ZIP, tile_name, park, conn, temp_dir)
                park_features += features
            
            if park_features > 0:
                print(f"  [{i+1}/{len(keystones)}] {park_id}: {park_features} polygons")
                total_features += park_features
                total_parks += 1
            
            # Progress every 20 parks
            if (i + 1) % 20 == 0:
                print(f"  Progress: {i+1}/{len(keystones)} parks processed")
    
    conn.close()
    
    print()
    print(f"=== Complete ===")
    print(f"Processed: {total_parks} parks with settlements")
    print(f"Created: {total_features} settlement polygons")
    print(f"Finished: {datetime.now()}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
