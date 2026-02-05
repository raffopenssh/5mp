#!/usr/bin/env python3
"""
Process Hansen GFC lossyear data to extract deforestation polygons.

Hansen tiles are named by their TOP-LEFT corner:
- 00N_020E means tile covers lon 20-30, lat -10 to 0 (extends south from 0°N)
- 10S_020E means tile covers lon 20-30, lat -20 to -10

Uses windowed reads and processes tiles in sequence.
Generates actual polygon geometries from raster data.
"""

import json
import sqlite3
import numpy as np
import sys
import os
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import math

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)

try:
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.features import shapes
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union
except ImportError as e:
    print(f"Missing dependency: {e}")
    sys.exit(1)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
HANSEN_DIR = DATA_DIR / "hansen"
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_PATH = DATA_DIR / "keystones_with_boundaries.json"

MIN_AREA_KM2 = 0.005  # 0.005 km2 = 5,000 m2

def load_keystones():
    with open(KEYSTONES_PATH, 'r') as f:
        return json.load(f)

def get_tile_bounds(tile_name):
    """Get the actual bounds of a Hansen tile from its filename.
    
    Tile naming: lossyear_XXY_ZZZW.tif
    where XX = latitude, Y = N/S, ZZZ = longitude, W = E/W
    
    The coordinates indicate the TOP-LEFT corner.
    Each tile is 10° x 10°.
    """
    # Parse tile name: lossyear_00N_020E.tif
    parts = tile_name.replace('lossyear_', '').replace('.tif', '').split('_')
    lat_str, lon_str = parts[0], parts[1]
    
    # Parse latitude (top of tile)
    lat_val = int(lat_str[:-1])
    if lat_str[-1] == 'S':
        lat_val = -lat_val
    
    # Parse longitude (left of tile)
    lon_val = int(lon_str[:-1])
    if lon_str[-1] == 'W':
        lon_val = -lon_val
    
    # Tile extends 10° south and 10° east from top-left
    return {
        'lon_min': lon_val,
        'lon_max': lon_val + 10,
        'lat_min': lat_val - 10,  # South from top
        'lat_max': lat_val        # Top
    }

def get_parks_in_tile(keystones, tile_bounds):
    """Find parks that intersect with the tile bounds."""
    parks = []
    for park in keystones:
        if not park.get('geometry'):
            continue
        
        # Check if park centroid is in tile
        lat = park['coordinates']['lat']
        lon = park['coordinates']['lon']
        
        if (tile_bounds['lon_min'] <= lon <= tile_bounds['lon_max'] and
            tile_bounds['lat_min'] <= lat <= tile_bounds['lat_max']):
            parks.append(park)
            continue
        
        # Also check if park geometry intersects tile
        try:
            park_geom = shape(park['geometry'])
            from shapely.geometry import box
            tile_box = box(tile_bounds['lon_min'], tile_bounds['lat_min'],
                          tile_bounds['lon_max'], tile_bounds['lat_max'])
            if park_geom.intersects(tile_box):
                parks.append(park)
        except:
            pass
    
    return parks

def process_park_deforestation(raster_path, park, conn):
    """Process deforestation for a single park using windowed reads."""
    park_id = park['id']
    park_geom = shape(park['geometry'])
    
    try:
        with rasterio.open(raster_path) as src:
            # Get park bounds
            minx, miny, maxx, maxy = park_geom.bounds
            
            # Clip to raster bounds
            raster_bounds = src.bounds
            minx = max(minx, raster_bounds.left)
            maxx = min(maxx, raster_bounds.right)
            miny = max(miny, raster_bounds.bottom)
            maxy = min(maxy, raster_bounds.top)
            
            if minx >= maxx or miny >= maxy:
                return 0, "Outside bounds"
            
            # Create window from bounds
            window = from_bounds(minx, miny, maxx, maxy, src.transform)
            
            # Validate window
            if window.col_off < 0 or window.row_off < 0:
                return 0, "Invalid window"
            
            if window.width < 1 or window.height < 1:
                return 0, "Empty window"
            
            # Read windowed data
            data = src.read(1, window=window)
            transform = src.window_transform(window)
            
            if data.max() == 0:
                return 0, "No forest loss"
            
            # Process each year (1-24 = 2001-2024)
            total_features = 0
            cur = conn.cursor()
            
            for year_code in range(1, 25):
                year = 2000 + year_code
                
                # Create mask for this year
                year_mask = (data == year_code).astype(np.uint8)
                
                if year_mask.sum() == 0:
                    continue
                
                # Vectorize to polygons
                try:
                    for geom_dict, value in shapes(year_mask, mask=year_mask > 0, transform=transform):
                        if value == 0:
                            continue
                        
                        poly = shape(geom_dict)
                        
                        # Clip to park boundary
                        try:
                            clipped = poly.intersection(park_geom)
                            if clipped.is_empty:
                                continue
                        except:
                            continue
                        
                        # Calculate area
                        lat_center = (clipped.bounds[1] + clipped.bounds[3]) / 2
                        deg_to_km = 111 * math.cos(math.radians(lat_center))
                        area_km2 = clipped.area * (deg_to_km ** 2)
                        
                        if area_km2 < MIN_AREA_KM2:
                            continue
                        
                        # Simplify geometry
                        simplified = clipped.simplify(0.0001, preserve_topology=True)
                        if simplified.is_empty:
                            continue
                        
                        centroid = simplified.centroid
                        feature_id = f"deforest_{park_id}_{year}_{total_features}"
                        geojson = json.dumps(mapping(simplified))
                        
                        properties = {
                            "year": year,
                            "area_km2": round(area_km2, 4),
                            "lat": round(centroid.y, 6),
                            "lon": round(centroid.x, 6)
                        }
                        
                        cur.execute("""
                            INSERT OR REPLACE INTO feature_geometries
                            (feature_type, feature_id, park_id, geojson, 
                             start_date, end_date, properties_json,
                             bbox_minx, bbox_miny, bbox_maxx, bbox_maxy)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            'deforestation', feature_id, park_id,
                            geojson,
                            f"{year}-01-01", f"{year}-12-31",
                            json.dumps(properties),
                            simplified.bounds[0], simplified.bounds[1],
                            simplified.bounds[2], simplified.bounds[3]
                        ))
                        
                        total_features += 1
                        
                except Exception as e:
                    continue
            
            conn.commit()
            return total_features, "OK"
            
    except Exception as e:
        return 0, str(e)

def main():
    print(f"=== Hansen Deforestation Polygon Processor ===")
    print(f"Started: {datetime.now()}")
    print()
    
    tiles = sorted(HANSEN_DIR.glob("lossyear_*.tif"))
    print(f"Found {len(tiles)} Hansen tiles")
    
    if not tiles:
        print("ERROR: No Hansen tiles found")
        return 1
    
    keystones = load_keystones()
    print(f"Loaded {len(keystones)} parks")
    
    conn = sqlite3.connect(DB_PATH)
    
    # Clear existing deforestation polygons
    print("Clearing existing deforestation features...")
    conn.execute("DELETE FROM feature_geometries WHERE feature_type = 'deforestation'")
    conn.commit()
    
    total_features = 0
    parks_processed = 0
    
    for tile_path in tiles:
        tile_name = tile_path.name
        tile_bounds = get_tile_bounds(tile_name)
        
        parks = get_parks_in_tile(keystones, tile_bounds)
        
        if not parks:
            print(f"\n{tile_name}: No parks")
            continue
        
        print(f"\n{tile_name}: {len(parks)} parks (bounds: {tile_bounds['lat_min']}° to {tile_bounds['lat_max']}°N)")
        
        for park in parks:
            features, status = process_park_deforestation(tile_path, park, conn)
            if features > 0:
                print(f"    {park['id']}: {features} polygons")
                total_features += features
                parks_processed += 1
            elif status != "OK" and status != "No forest loss" and status != "Outside bounds":
                print(f"    {park['id']}: {status}")
    
    conn.close()
    
    print()
    print(f"=== Complete ===")
    print(f"Parks with deforestation: {parks_processed}")
    print(f"Total polygons: {total_features}")
    print(f"Finished: {datetime.now()}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
