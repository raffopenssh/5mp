#!/usr/bin/env python3
"""Extract HydroRIVERS, HydroLAKES for each park with 50km buffer.

Merges OSM place names (river, lake types) via spatial join.
Outputs JSON per park to data/rivers_hydro/ and data/lakes_hydro/

Requires:
- data/hydro_source/HydroRIVERS_v10_af.gdb (download from HydroSHEDS)
- data/hydro_source/HydroLAKES_polys_v10.gdb (download from HydroSHEDS)
- data/keystones_with_boundaries.json (park geometries)

Note: HeiGIT roads are already processed in data/roads_heigit/ with geometry.
"""

import json
import os
import sys
from pathlib import Path
import fiona
from shapely.geometry import shape, mapping, Point, LineString, MultiLineString, Polygon, MultiPolygon
from shapely.ops import transform, unary_union
import pyproj
from functools import partial
import sqlite3
from collections import defaultdict

# Constants
BUFFER_KM = 50
DATA_DIR = Path("data")
OUTPUT_RIVERS_DIR = DATA_DIR / "rivers_hydro"
OUTPUT_LAKES_DIR = DATA_DIR / "lakes_hydro"
HYDRO_SOURCE_DIR = DATA_DIR / "hydro_source"

# Create output directories
OUTPUT_RIVERS_DIR.mkdir(exist_ok=True)
OUTPUT_LAKES_DIR.mkdir(exist_ok=True)

def load_parks():
    """Load park boundaries from keystones_with_boundaries.json"""
    with open(DATA_DIR / "keystones_with_boundaries.json") as f:
        parks = json.load(f)
    return {p['id']: p for p in parks if p.get('geometry')}

def create_buffer(geometry, buffer_km):
    """Create a buffer around geometry in km."""
    # Project to UTM for accurate buffering
    centroid = geometry.centroid
    
    # Use UTM zone based on centroid
    utm_zone = int((centroid.x + 180) / 6) + 1
    hemisphere = 'north' if centroid.y >= 0 else 'south'
    
    # Create projection
    wgs84 = pyproj.CRS('EPSG:4326')
    utm = pyproj.CRS(f'+proj=utm +zone={utm_zone} +{hemisphere} +datum=WGS84')
    
    project_to_utm = pyproj.Transformer.from_crs(wgs84, utm, always_xy=True).transform
    project_to_wgs = pyproj.Transformer.from_crs(utm, wgs84, always_xy=True).transform
    
    # Buffer in meters
    geom_utm = transform(project_to_utm, geometry)
    buffered_utm = geom_utm.buffer(buffer_km * 1000)
    buffered_wgs = transform(project_to_wgs, buffered_utm)
    
    return buffered_wgs

def load_osm_places(db_path="db.sqlite3"):
    """Load OSM places of type river, lake, stream, road from database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Get river/lake/stream/road places
    cur.execute("""
        SELECT park_id, name, place_type, lat, lon, osm_id
        FROM osm_places 
        WHERE place_type IN ('river', 'lake', 'stream', 'road')
    """)
    
    places = defaultdict(lambda: {'rivers': [], 'lakes': [], 'roads': []})
    for row in cur:
        place_data = {
            'name': row['name'],
            'type': row['place_type'],
            'lat': row['lat'],
            'lon': row['lon'],
            'osm_id': row['osm_id']
        }
        if row['place_type'] in ('river', 'stream'):
            places[row['park_id']]['rivers'].append(place_data)
        elif row['place_type'] == 'lake':
            places[row['park_id']]['lakes'].append(place_data)
        elif row['place_type'] == 'road':
            places[row['park_id']]['roads'].append(place_data)
    
    conn.close()
    return places

def extract_rivers_for_park(park_id, buffer_geom, rivers_path, osm_places):
    """Extract HydroRIVERS features within buffer."""
    rivers = []
    bounds = buffer_geom.bounds  # (minx, miny, maxx, maxy)
    
    # Get OSM places for name matching
    park_osm = osm_places.get(park_id, {'rivers': [], 'lakes': [], 'roads': []})
    osm_rivers = park_osm['rivers']
    
    with fiona.open(rivers_path) as src:
        # Use bounding box filter for efficiency
        for feat in src.filter(bbox=bounds):
            geom = shape(feat['geometry'])
            
            if buffer_geom.intersects(geom):
                props = dict(feat['properties'])
                
                # Get centroid for name matching
                centroid = geom.centroid
                
                # Try to find OSM name by proximity (within 5km)
                river_name = None
                min_dist = 5.0  # km threshold
                for osm in osm_rivers:
                    dist = ((centroid.x - osm['lon'])**2 + (centroid.y - osm['lat'])**2)**0.5 * 111  # rough km
                    if dist < min_dist:
                        min_dist = dist
                        river_name = osm['name']
                
                # Clip to buffer
                clipped = geom.intersection(buffer_geom)
                if clipped.is_empty:
                    continue
                
                # Calculate length
                length_km = calculate_length_km(clipped)
                
                rivers.append({
                    'hyriv_id': props.get('HYRIV_ID'),
                    'name': river_name,
                    'stream_order': props.get('ORD_STRA'),
                    'ord_flow': props.get('ORD_FLOW'),
                    'length_km': round(length_km, 2),
                    'geometry': mapping(clipped)
                })
    
    return rivers

def extract_lakes_for_park(park_id, buffer_geom, lakes_path, osm_places):
    """Extract HydroLAKES features within buffer."""
    lakes = []
    bounds = buffer_geom.bounds
    
    # Get OSM lake names
    park_osm = osm_places.get(park_id, {'rivers': [], 'lakes': [], 'roads': []})
    osm_lakes = park_osm['lakes']
    
    with fiona.open(lakes_path) as src:
        for feat in src.filter(bbox=bounds):
            geom = shape(feat['geometry'])
            
            if buffer_geom.intersects(geom):
                props = dict(feat['properties'])
                
                # Get centroid for name matching
                centroid = geom.centroid
                
                # Use HydroLAKES name if available, else try OSM
                lake_name = props.get('Lake_name')
                if not lake_name:
                    min_dist = 5.0
                    for osm in osm_lakes:
                        dist = ((centroid.x - osm['lon'])**2 + (centroid.y - osm['lat'])**2)**0.5 * 111
                        if dist < min_dist:
                            min_dist = dist
                            lake_name = osm['name']
                
                # Clip to buffer
                clipped = geom.intersection(buffer_geom)
                if clipped.is_empty:
                    continue
                
                # Calculate area
                area_km2 = calculate_area_km2(clipped)
                
                lakes.append({
                    'hylak_id': props.get('Hylak_id'),
                    'name': lake_name,
                    'lake_type': props.get('Lake_type'),
                    'elevation': props.get('Elevation'),
                    'area_km2': round(area_km2, 3),
                    'centroid_lon': round(centroid.x, 6),
                    'centroid_lat': round(centroid.y, 6),
                    'geometry': mapping(clipped)
                })
    
    return lakes


def calculate_length_km(geom):
    """Calculate approximate length in km for a line geometry."""
    if geom.is_empty:
        return 0
    
    # Simple approximation using degrees to km
    if hasattr(geom, 'geoms'):  # MultiLineString
        total = sum(calculate_length_km(g) for g in geom.geoms)
        return total
    
    coords = list(geom.coords)
    total = 0
    for i in range(len(coords) - 1):
        dx = (coords[i+1][0] - coords[i][0]) * 111 * abs(coords[i][1] / 90 - 1 + 0.5)  # rough lon correction
        dy = (coords[i+1][1] - coords[i][1]) * 111
        total += (dx**2 + dy**2)**0.5
    return total

def calculate_area_km2(geom):
    """Calculate approximate area in km2 for a polygon."""
    if geom.is_empty:
        return 0
    
    centroid = geom.centroid
    utm_zone = int((centroid.x + 180) / 6) + 1
    hemisphere = 'north' if centroid.y >= 0 else 'south'
    
    wgs84 = pyproj.CRS('EPSG:4326')
    utm = pyproj.CRS(f'+proj=utm +zone={utm_zone} +{hemisphere} +datum=WGS84')
    
    project = pyproj.Transformer.from_crs(wgs84, utm, always_xy=True).transform
    geom_utm = transform(project, geom)
    
    return geom_utm.area / 1e6  # m2 to km2

def main():
    print("Loading parks...")
    parks = load_parks()
    print(f"Loaded {len(parks)} parks with boundaries")
    
    print("Loading OSM places (rivers, lakes, roads)...")
    osm_places = load_osm_places()
    print(f"Loaded OSM places for {len(osm_places)} parks")
    
    rivers_path = HYDRO_SOURCE_DIR / "HydroRIVERS_v10_af.gdb"
    lakes_path = HYDRO_SOURCE_DIR / "HydroLAKES_polys_v10.gdb"
    
    if not rivers_path.exists():
        print(f"Error: {rivers_path} not found")
        sys.exit(1)
    if not lakes_path.exists():
        print(f"Error: {lakes_path} not found")
        sys.exit(1)
    
    # Process each park
    total_rivers = 0
    total_lakes = 0
    
    for i, (park_id, park) in enumerate(parks.items()):
        print(f"\n[{i+1}/{len(parks)}] Processing {park_id}...")
        
        try:
            # Create park geometry
            park_geom = shape(park['geometry'])
            
            # Create 50km buffer
            buffer_geom = create_buffer(park_geom, BUFFER_KM)
            print(f"  Buffer created (50km)")
            
            # Extract rivers
            rivers = extract_rivers_for_park(park_id, buffer_geom, rivers_path, osm_places)
            print(f"  Found {len(rivers)} rivers")
            total_rivers += len(rivers)
            
            # Save rivers JSON
            rivers_output = {
                'park_id': park_id,
                'buffer_km': BUFFER_KM,
                'river_count': len(rivers),
                'rivers': rivers
            }
            with open(OUTPUT_RIVERS_DIR / f"{park_id}.json", 'w') as f:
                json.dump(rivers_output, f)
            
            # Extract lakes
            lakes = extract_lakes_for_park(park_id, buffer_geom, lakes_path, osm_places)
            print(f"  Found {len(lakes)} lakes")
            total_lakes += len(lakes)
            
            # Save lakes JSON
            lakes_output = {
                'park_id': park_id,
                'buffer_km': BUFFER_KM,
                'lake_count': len(lakes),
                'lakes': lakes
            }
            with open(OUTPUT_LAKES_DIR / f"{park_id}.json", 'w') as f:
                json.dump(lakes_output, f)
            
                
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"\n=== Summary ===")
    print(f"Total rivers extracted: {total_rivers}")
    print(f"Total lakes extracted: {total_lakes}")
    print(f"Output: {OUTPUT_RIVERS_DIR}/, {OUTPUT_LAKES_DIR}/")

if __name__ == "__main__":
    main()
