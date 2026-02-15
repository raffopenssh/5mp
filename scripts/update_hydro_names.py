#!/usr/bin/env python3
"""Update rivers_hydro and lakes_hydro JSON files with OSM place names.

Matches HydroRIVERS/HydroLAKES features to nearby OSM river/stream/lake places
by geographic proximity and assigns names.

Usage:
    python scripts/update_hydro_names.py [--park PARK_ID]
"""

import json
import argparse
from pathlib import Path

DATA_DIR = Path("data")
OSM_DIR = DATA_DIR / "osm_places"
RIVERS_DIR = DATA_DIR / "rivers_hydro"
LAKES_DIR = DATA_DIR / "lakes_hydro"


def load_osm_places(park_id):
    """Load OSM river/lake/stream places for a park"""
    osm_file = OSM_DIR / f"{park_id}.json"
    if not osm_file.exists():
        return {'rivers': [], 'lakes': []}
    
    with open(osm_file) as f:
        data = json.load(f)
    
    places = data.get('places', [])
    rivers = [p for p in places if p.get('place_type') in ('river', 'stream')]
    lakes = [p for p in places if p.get('place_type') == 'lake']
    
    return {'rivers': rivers, 'lakes': lakes}


def find_nearest_name(lat, lon, osm_places, max_dist_km=5):
    """Find nearest OSM place name within max distance"""
    best_name = None
    best_dist = max_dist_km
    
    for p in osm_places:
        # Simple distance in degrees (~111km per degree)
        dist = ((lon - p['lon'])**2 + (lat - p['lat'])**2)**0.5 * 111
        if dist < best_dist:
            best_dist = dist
            best_name = p['name']
    
    return best_name


def get_centroid(geometry):
    """Get centroid of a geometry"""
    if not geometry:
        return None, None
    
    geom_type = geometry.get('type', '')
    coords = geometry.get('coordinates', [])
    
    if geom_type == 'MultiLineString':
        all_coords = [c for line in coords for c in line]
    elif geom_type == 'LineString':
        all_coords = coords
    elif geom_type == 'MultiPolygon':
        all_coords = [c for poly in coords for ring in poly for c in ring]
    elif geom_type == 'Polygon':
        all_coords = [c for ring in coords for c in ring]
    else:
        return None, None
    
    if not all_coords:
        return None, None
    
    lons = [c[0] for c in all_coords]
    lats = [c[1] for c in all_coords]
    return sum(lats)/len(lats), sum(lons)/len(lons)


def update_park(park_id):
    """Update rivers and lakes for a single park"""
    osm_places = load_osm_places(park_id)
    
    if not osm_places['rivers'] and not osm_places['lakes']:
        return 0, 0
    
    rivers_named = 0
    lakes_named = 0
    
    # Update rivers
    rivers_file = RIVERS_DIR / f"{park_id}.json"
    if rivers_file.exists() and osm_places['rivers']:
        with open(rivers_file) as f:
            rivers_data = json.load(f)
        
        updated = False
        for river in rivers_data.get('rivers', []):
            if river.get('name'):
                continue
            
            lat, lon = get_centroid(river.get('geometry'))
            if lat and lon:
                name = find_nearest_name(lat, lon, osm_places['rivers'], max_dist_km=3)
                if name:
                    river['name'] = name
                    rivers_named += 1
                    updated = True
        
        if updated:
            with open(rivers_file, 'w') as f:
                json.dump(rivers_data, f)
    
    # Update lakes
    lakes_file = LAKES_DIR / f"{park_id}.json"
    if lakes_file.exists() and osm_places['lakes']:
        with open(lakes_file) as f:
            lakes_data = json.load(f)
        
        updated = False
        for lake in lakes_data.get('lakes', []):
            if lake.get('name'):
                continue
            
            lat = lake.get('centroid_lat')
            lon = lake.get('centroid_lon')
            if lat and lon:
                name = find_nearest_name(lat, lon, osm_places['lakes'], max_dist_km=5)
                if name:
                    lake['name'] = name
                    lakes_named += 1
                    updated = True
        
        if updated:
            with open(lakes_file, 'w') as f:
                json.dump(lakes_data, f)
    
    return rivers_named, lakes_named


def main():
    parser = argparse.ArgumentParser(description='Update hydro data with OSM names')
    parser.add_argument('--park', type=str, help='Process specific park ID')
    args = parser.parse_args()
    
    if args.park:
        rivers, lakes = update_park(args.park)
        print(f"{args.park}: {rivers} rivers named, {lakes} lakes named")
        return
    
    # Process all parks
    total_rivers = 0
    total_lakes = 0
    parks_updated = 0
    
    for rivers_file in sorted(RIVERS_DIR.glob("*.json")):
        park_id = rivers_file.stem
        rivers, lakes = update_park(park_id)
        
        if rivers or lakes:
            print(f"{park_id}: {rivers} rivers, {lakes} lakes")
            total_rivers += rivers
            total_lakes += lakes
            parks_updated += 1
    
    print(f"\n=== Summary ===")
    print(f"Parks updated: {parks_updated}")
    print(f"Rivers named: {total_rivers}")
    print(f"Lakes named: {total_lakes}")


if __name__ == "__main__":
    main()
