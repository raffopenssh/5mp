#!/usr/bin/env python3
"""
GHSL Tiles Needed for 5MP Parks

This script calculates which GHSL tiles are needed for all 162 keystone parks
and generates download URLs.

The JRC/Copernicus servers are not reachable from this VM, so tiles must be
downloaded elsewhere and uploaded.

Download URLs follow this pattern:
https://human-settlement.emergency.copernicus.eu/download.php?ds=bu&level=S&kw=2018&re=R2023A&pr=54009&res=100&tile=R{row}_C{col}

Or from JRC FTP:
https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2030_GLOBE_R2023A_54009_100/V1-0/GHS_BUILT_S_E2030_GLOBE_R2023A_54009_100_V1_0_R{row}_C{col}.zip

Usage:
    python scripts/ghsl_tiles_needed.py
    python scripts/ghsl_tiles_needed.py --urls  # Generate download URLs
    python scripts/ghsl_tiles_needed.py --wget  # Generate wget commands
"""

import json
import argparse
from pathlib import Path
from collections import defaultdict

try:
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform
    from pyproj import Transformer
except ImportError:
    print("Required: pip install shapely pyproj")
    exit(1)

BASE_DIR = Path(__file__).parent.parent
KEYSTONES_PATH = BASE_DIR / "data" / "keystones_with_boundaries.json"

# Coordinate transformer
wgs84_to_moll = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True)

# GHSL tile grid parameters (Mollweide projection)
ORIGIN_X = -18041000
ORIGIN_Y = 9000000
TILE_SIZE = 1000000  # 1000km

def get_tile_for_point(x, y):
    """Get tile row/col for a Mollweide coordinate"""
    col = int((x - ORIGIN_X) / TILE_SIZE)
    row = int((ORIGIN_Y - y) / TILE_SIZE)
    return (row, col)

def get_tiles_needed():
    """Calculate all tiles needed for keystone parks"""
    with open(KEYSTONES_PATH) as f:
        keystones = json.load(f)
    
    tiles_needed = set()
    parks_per_tile = defaultdict(list)
    
    for ks in keystones:
        if not ks.get('geometry'):
            continue
        
        try:
            geom = shape(ks['geometry'])
            geom_moll = shp_transform(lambda x, y: wgs84_to_moll.transform(x, y), geom)
            
            minx, miny, maxx, maxy = geom_moll.bounds
            
            for x in [minx, maxx]:
                for y in [miny, maxy]:
                    row, col = get_tile_for_point(x, y)
                    tiles_needed.add((row, col))
                    if ks['id'] not in parks_per_tile[(row, col)]:
                        parks_per_tile[(row, col)].append(ks['id'])
        except:
            pass
    
    return tiles_needed, parks_per_tile

def get_download_url(row, col, product='BUILT_S', resolution=100):
    """Generate download URL for a tile"""
    # Copernicus emergency services URL
    return f"https://human-settlement.emergency.copernicus.eu/download.php?ds=bu&level=S&kw=2018&re=R2023A&pr=54009&res={resolution}&tile=R{row}_C{col}"

def get_jrc_url(row, col, product='BUILT_S', resolution=100):
    """Generate JRC FTP URL for a tile"""
    return f"https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2030_GLOBE_R2023A_54009_{resolution}/V1-0/GHS_BUILT_S_E2030_GLOBE_R2023A_54009_{resolution}_V1_0_R{row}_C{col}.zip"

def main():
    parser = argparse.ArgumentParser(description='GHSL Tiles Needed Calculator')
    parser.add_argument('--urls', action='store_true', help='Generate download URLs')
    parser.add_argument('--wget', action='store_true', help='Generate wget commands')
    parser.add_argument('--jrc', action='store_true', help='Use JRC URLs instead of Copernicus')
    args = parser.parse_args()
    
    tiles_needed, parks_per_tile = get_tiles_needed()
    
    # Currently available tiles
    available = {(9, 22), (8, 20), (8, 21)}  # R9_C22, R8_C20, R8_C21
    missing = tiles_needed - available
    
    print(f"Total tiles needed: {len(tiles_needed)}")
    print(f"Currently available: {len(available)}")
    print(f"Missing tiles: {len(missing)}")
    print()
    
    if args.urls or args.wget:
        print("# Download commands for missing tiles:")
        print("# Save files to data/ghsl_tiles/")
        print()
        
        for row, col in sorted(missing):
            tile_key = f"R{row}_C{col}"
            park_count = len(parks_per_tile[(row, col)])
            
            if args.jrc:
                url = get_jrc_url(row, col)
            else:
                url = get_download_url(row, col)
            
            if args.wget:
                print(f"# {tile_key} ({park_count} parks)")
                print(f"wget -O data/ghsl_tiles/{tile_key}_BUILT_S.zip '{url}'")
                # Also need POP data
                pop_url = url.replace('ds=bu&level=S', 'ds=pop').replace('kw=2018', 'kw=2030')
                print(f"wget -O data/ghsl_tiles/{tile_key}_POP.zip '{pop_url}'")
                print()
            else:
                print(f"{tile_key}: {url}")
    else:
        print("Tiles needed (sorted by park coverage):")
        for row, col in sorted(tiles_needed, key=lambda t: -len(parks_per_tile[t])):
            tile_key = f"R{row}_C{col}"
            park_count = len(parks_per_tile[(row, col)])
            status = "✓" if (row, col) in available else "✗"
            print(f"  {status} {tile_key}: {park_count} parks")

if __name__ == '__main__':
    main()
