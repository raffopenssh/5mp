#!/usr/bin/env python3
"""Deforestation Analysis using Hansen Global Forest Change data.

Analyzes forest loss within keystone protected areas using Hansen GFC-2024 data.
Values: 0 = no loss, 1-24 = year of loss (2001-2024)

Supports multiple tiles - auto-detects which tile(s) each park needs.

Classifies deforestation patterns:
- strip: linear patterns suggesting road construction
- cluster: concentrated patterns suggesting mining
- scattered: dispersed patterns suggesting farming/agriculture
- edge: patterns along park boundaries suggesting encroachment
"""

import json
import sqlite3
import numpy as np
import rasterio
from rasterio.mask import mask
from shapely.geometry import shape, mapping, box, Point
from shapely.ops import unary_union
from scipy import ndimage
from collections import defaultdict
import sys
import os
from datetime import datetime
import math
import glob

# Constants
HANSEN_DIR = 'data/hansen'
KEYSTONES_PATH = 'data/keystones_with_boundaries.json'
DB_PATH = 'db.sqlite3'

# Pixel area at equator (approx) - Hansen is 30m resolution but stored as 0.00025 degrees
PIXEL_SIZE_DEG = 0.00025


def get_tile_name(lat, lon):
    """Convert lat/lon to Hansen tile name.
    
    Tile naming convention:
    - N tiles: name = upper latitude boundary (e.g., 10N covers 0-10N)
    - S tiles: name = lower latitude boundary (e.g., 10S covers -20 to -10)
    - E tiles: name = left longitude boundary (e.g., 010E covers 10-20E)
    - W tiles: name = right longitude boundary (e.g., 010W covers -10 to 0)
    
    Examples:
    - Point at 5°N, 15°E -> tile 10N_010E (covers 0-10°N, 10-20°E)
    - Point at -5°, 15°E -> tile 00N_010E (covers -10-0°, 10-20°E)
    - Point at -15°, 25°E -> tile 10S_020E (covers -20 to -10°, 20-30°E)
    """
    # Latitude
    if lat >= 0:
        lat_band = (int(lat) // 10 + 1) * 10
        lat_str = f"{lat_band:02d}N"
    elif lat > -10:
        lat_str = "00N"
    else:
        # S tiles: 10S = -20 to -10, 20S = -30 to -20
        lat_band = (int(-lat) // 10) * 10
        lat_str = f"{lat_band:02d}S"
    
    # Longitude  
    if lon >= 0:
        lon_band = (int(lon) // 10) * 10
        lon_str = f"{lon_band:03d}E"
    else:
        lon_band = (int(-lon - 0.0001) // 10 + 1) * 10
        lon_str = f"{lon_band:03d}W"
    
    return f"{lat_str}_{lon_str}"


def get_tile_bounds(tile_name):
    """Get bounding box for a tile name.
    
    Verified against actual tile bounds from rasterio.
    """
    lat_part, lon_part = tile_name.split('_')
    
    lat_val = int(lat_part[:-1])
    lat_hem = lat_part[-1]
    lon_val = int(lon_part[:-1])
    lon_hem = lon_part[-1]
    
    # Latitude bounds - N tiles: name is upper bound, S tiles: name is lower bound
    if lat_hem == 'N':
        max_lat = lat_val
        min_lat = lat_val - 10
    else:  # S
        max_lat = -lat_val
        min_lat = -lat_val - 10
    
    # Longitude bounds - E tiles: name is left bound, W tiles: name is right bound
    if lon_hem == 'E':
        min_lon = lon_val
        max_lon = lon_val + 10
    else:  # W
        min_lon = -lon_val
        max_lon = -lon_val + 10
    
    return box(min_lon, min_lat, max_lon, max_lat)


def get_tiles_for_geometry(geom):
    """Get list of tile names that intersect with a geometry."""
    bounds = geom.bounds
    minx, miny, maxx, maxy = bounds
    
    tiles = set()
    # Check grid of points across the bounding box
    for lat in np.arange(miny, maxy + 0.1, 5):  # Every 5 degrees
        for lon in np.arange(minx, maxx + 0.1, 5):
            tiles.add(get_tile_name(lat, lon))
    
    # Filter to tiles that actually intersect
    valid_tiles = []
    for tile in tiles:
        try:
            tile_bounds = get_tile_bounds(tile)
            if geom.intersects(tile_bounds):
                valid_tiles.append(tile)
        except:
            pass
    
    return valid_tiles


def get_available_tiles():
    """Scan hansen directory for available tiles."""
    tiles = {}
    pattern = os.path.join(HANSEN_DIR, 'Hansen_GFC-2024-v1.12_lossyear_*.tif')
    for filepath in glob.glob(pattern):
        filename = os.path.basename(filepath)
        # Extract tile name: Hansen_GFC-2024-v1.12_lossyear_10N_020E.tif -> 10N_020E
        parts = filename.replace('.tif', '').split('_')
        tile_name = f"{parts[-2]}_{parts[-1]}"
        tiles[tile_name] = filepath
    return tiles


def get_pixel_area_km2(lat):
    """Calculate pixel area in km2 accounting for latitude."""
    lat_km = 110.574 * PIXEL_SIZE_DEG
    lon_km = 111.32 * math.cos(math.radians(abs(lat))) * PIXEL_SIZE_DEG
    return lat_km * lon_km


def load_keystones():
    """Load keystones with boundaries."""
    with open(KEYSTONES_PATH, 'r') as f:
        keystones = json.load(f)
    return keystones


def classify_pattern(loss_array, threshold_pixels=50):
    """Classify deforestation pattern based on spatial characteristics.
    
    Returns: pattern_type, cluster_info
    """
    if loss_array.sum() < threshold_pixels:
        return 'minor', []
    
    # Label connected components
    labeled, num_features = ndimage.label(loss_array > 0)
    
    if num_features == 0:
        return 'none', []
    
    cluster_info = []
    pattern_votes = defaultdict(int)
    
    for cluster_id in range(1, num_features + 1):
        cluster_mask = labeled == cluster_id
        cluster_pixels = cluster_mask.sum()
        
        if cluster_pixels < 10:
            continue
        
        rows, cols = np.where(cluster_mask)
        if len(rows) == 0:
            continue
            
        height = rows.max() - rows.min() + 1
        width = cols.max() - cols.min() + 1
        
        aspect_ratio = max(height, width) / max(min(height, width), 1)
        bbox_area = height * width
        fill_ratio = cluster_pixels / bbox_area if bbox_area > 0 else 0
        
        if aspect_ratio > 5:
            cluster_pattern = 'strip'
        elif fill_ratio > 0.5 and cluster_pixels > 100:
            cluster_pattern = 'cluster'
        else:
            cluster_pattern = 'scattered'
        
        pattern_votes[cluster_pattern] += cluster_pixels
        
        cluster_info.append({
            'id': cluster_id,
            'pixels': cluster_pixels,
            'centroid_row': rows.mean(),
            'centroid_col': cols.mean(),
            'pattern': cluster_pattern,
            'aspect_ratio': aspect_ratio,
            'fill_ratio': fill_ratio
        })
    
    if not pattern_votes:
        return 'minor', cluster_info
    
    dominant_pattern = max(pattern_votes, key=pattern_votes.get)
    return dominant_pattern, cluster_info


def analyze_park_with_tile(park_geom, tile_bounds, dataset):
    """Analyze deforestation for park geometry within one tile."""
    # Clip geometry to tile bounds
    clipped_geom = park_geom.intersection(tile_bounds)
    if clipped_geom.is_empty:
        return {}
    
    try:
        geom_geojson = [mapping(clipped_geom)]
        out_image, out_transform = mask(dataset, geom_geojson, crop=True, nodata=0)
        loss_data = out_image[0]
        
        park_bounds = clipped_geom.bounds
        minx, miny, maxx, maxy = park_bounds
        rows, cols = loss_data.shape
        center_lat = (miny + maxy) / 2
        
        yearly_stats = {}
        
        for year_code in range(1, 25):
            actual_year = 2000 + year_code
            year_mask = loss_data == year_code
            pixel_count = year_mask.sum()
            
            if pixel_count == 0:
                continue
            
            pixel_area = get_pixel_area_km2(center_lat)
            area_km2 = pixel_count * pixel_area
            pattern, clusters = classify_pattern(year_mask)
            
            loss_rows, loss_cols = np.where(year_mask)
            if len(loss_rows) > 0 and rows > 0 and cols > 0:
                center_row = loss_rows.mean()
                center_col = loss_cols.mean()
                lat = maxy - (center_row / rows) * (maxy - miny)
                lon = minx + (center_col / cols) * (maxx - minx)
            else:
                lat, lon = center_lat, (minx + maxx) / 2
            
            if area_km2 > 10:
                event_type = 'major'
            elif area_km2 > 1:
                event_type = 'moderate'
            else:
                event_type = 'minor'
            
            yearly_stats[actual_year] = {
                'year': actual_year,
                'pixel_count': int(pixel_count),
                'area_km2': area_km2,
                'pattern_type': pattern,
                'event_type': event_type,
                'lat': lat,
                'lon': lon,
                'clusters': clusters
            }
        
        return yearly_stats
        
    except Exception as e:
        return {}


def merge_yearly_stats(all_stats, new_stats):
    """Merge stats from multiple tiles."""
    for year, stats in new_stats.items():
        if year in all_stats:
            existing = all_stats[year]
            existing['pixel_count'] += stats['pixel_count']
            existing['area_km2'] += stats['area_km2']
            existing['clusters'].extend(stats.get('clusters', []))
        else:
            all_stats[year] = stats.copy()
            all_stats[year]['clusters'] = list(stats.get('clusters', []))
    return all_stats


def generate_description(park, stats):
    """Generate narrative description for deforestation event."""
    area = stats['area_km2']
    pattern = stats['pattern_type']
    year = stats['year']
    
    pattern_desc = {
        'strip': 'linear clearing suggesting road construction or infrastructure development',
        'cluster': 'concentrated clearing suggesting mining activity or logging operation',
        'scattered': 'dispersed clearing suggesting agricultural expansion or smallholder farming',
        'edge': 'boundary clearing suggesting encroachment from surrounding areas',
        'minor': 'minimal forest disturbance'
    }
    
    desc = f"In {year}, {park['name']} ({park['country']}) experienced {area:.2f} km² of forest loss. "
    desc += f"The pattern shows {pattern_desc.get(pattern, 'forest disturbance')}. "
    desc += f"Centered at approximately {abs(stats['lat']):.2f}°{'N' if stats['lat'] >= 0 else 'S'}, "
    desc += f"{abs(stats['lon']):.2f}°{'E' if stats['lon'] >= 0 else 'W'}. "
    
    return desc


def save_to_database(park, yearly_stats):
    """Save deforestation analysis results to database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')
    cursor = conn.cursor()
    
    try:
        for year, stats in yearly_stats.items():
            description = generate_description(park, stats)
            
            geojson = json.dumps({
                'type': 'Point',
                'coordinates': [stats['lon'], stats['lat']]
            })
            
            cursor.execute('''
                INSERT OR REPLACE INTO deforestation_events 
                (park_id, year, area_km2, event_type, lat, lon, geojson, description, pattern_type, pixel_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                park['id'],
                year,
                round(stats['area_km2'], 4),
                stats['event_type'],
                round(stats['lat'], 5),
                round(stats['lon'], 5),
                geojson,
                description,
                stats['pattern_type'],
                stats['pixel_count']
            ))
            
            for cluster in stats.get('clusters', []):
                if cluster['pixels'] < 20:
                    continue
                cursor.execute('''
                    INSERT INTO deforestation_clusters
                    (park_id, year, cluster_id, area_km2, lat, lon, pattern_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    park['id'],
                    year,
                    cluster['id'],
                    round(cluster['pixels'] * get_pixel_area_km2(stats['lat']), 4),
                    round(stats['lat'], 5),
                    round(stats['lon'], 5),
                    cluster['pattern']
                ))
        
        conn.commit()
        return True
        
    except Exception as e:
        print(f"      DB error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def analyze_all_parks(single_park_id=None, skip_existing=False):
    """Main analysis function - processes all parks across all available tiles."""
    print("Deforestation Analysis using Hansen GFC-2024")
    print("=" * 60)
    
    # Get available tiles
    available_tiles = get_available_tiles()
    print(f"\nAvailable Hansen tiles: {len(available_tiles)}")
    for tile in sorted(available_tiles.keys()):
        print(f"  {tile}")
    
    # Load keystones
    print("\nLoading keystone parks...")
    keystones = load_keystones()
    print(f"Total parks: {len(keystones)}")
    
    # Get existing parks if skipping
    existing_parks = set()
    if skip_existing:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT park_id FROM deforestation_events")
        existing_parks = {row[0] for row in cursor.fetchall()}
        conn.close()
        print(f"Parks with existing data (will skip): {len(existing_parks)}")
    
    # Build list of parks to process
    parks_to_process = []
    parks_no_tiles = []
    
    for park in keystones:
        if not park.get('geometry'):
            continue
        if single_park_id and park['id'] != single_park_id:
            continue
        if skip_existing and park['id'] in existing_parks:
            continue
        
        try:
            geom = shape(park['geometry'])
            needed_tiles = get_tiles_for_geometry(geom)
            tiles_available = [t for t in needed_tiles if t in available_tiles]
            
            if tiles_available:
                parks_to_process.append({
                    'id': park['id'],
                    'name': park['name'],
                    'country': park['country'],
                    'geometry': geom,
                    'tiles': tiles_available
                })
            else:
                parks_no_tiles.append(park['id'])
        except Exception as e:
            print(f"Error processing {park.get('id')}: {e}")
    
    print(f"\nParks to process: {len(parks_to_process)}")
    if parks_no_tiles:
        print(f"Parks with no available tiles: {len(parks_no_tiles)}")
    
    if not parks_to_process:
        print("No parks to process!")
        return
    
    # Process parks - group by tile to minimize file opens
    tile_parks = defaultdict(list)
    for park in parks_to_process:
        primary_tile = park['tiles'][0]  # Use first tile as primary
        tile_parks[primary_tile].append(park)
    
    print(f"\nProcessing by tile to optimize memory...")
    processed = 0
    failed = 0
    processed_ids = set()
    
    for tile_name in sorted(tile_parks.keys()):
        parks = tile_parks[tile_name]
        tile_path = available_tiles[tile_name]
        tile_bounds = get_tile_bounds(tile_name)
        
        print(f"\n[Tile {tile_name}] {len(parks)} parks")
        
        try:
            with rasterio.open(tile_path) as dataset:
                for park in parks:
                    if park['id'] in processed_ids:
                        continue
                    
                    print(f"  {park['id']}...", end=' ', flush=True)
                    
                    try:
                        # Analyze with all tiles this park needs
                        all_stats = {}
                        
                        # First, analyze with current tile
                        stats = analyze_park_with_tile(park['geometry'], tile_bounds, dataset)
                        all_stats = merge_yearly_stats(all_stats, stats)
                        
                        # If park needs other tiles, process them too
                        other_tiles = [t for t in park['tiles'] if t != tile_name]
                        for other_tile in other_tiles:
                            if other_tile in available_tiles:
                                other_bounds = get_tile_bounds(other_tile)
                                with rasterio.open(available_tiles[other_tile]) as other_ds:
                                    other_stats = analyze_park_with_tile(park['geometry'], other_bounds, other_ds)
                                    all_stats = merge_yearly_stats(all_stats, other_stats)
                        
                        # Finalize stats
                        for year, stats in all_stats.items():
                            stats['area_km2'] = round(stats['area_km2'], 4)
                            if stats['area_km2'] > 10:
                                stats['event_type'] = 'major'
                            elif stats['area_km2'] > 1:
                                stats['event_type'] = 'moderate'
                            else:
                                stats['event_type'] = 'minor'
                        
                        if all_stats:
                            if save_to_database(park, all_stats):
                                total_loss = sum(s['area_km2'] for s in all_stats.values())
                                print(f"OK ({len(all_stats)} years, {total_loss:.2f} km²)")
                                processed += 1
                            else:
                                print("DB ERROR")
                                failed += 1
                        else:
                            print("No loss detected")
                            processed += 1
                        
                        processed_ids.add(park['id'])
                        
                    except Exception as e:
                        print(f"ERROR: {e}")
                        failed += 1
                        
        except Exception as e:
            print(f"  Failed to open tile: {e}")
            failed += len(parks)
    
    print("\n" + "=" * 60)
    print(f"COMPLETE: {processed} processed, {failed} failed")
    print("=" * 60)


def main():
    """Entry point."""
    import argparse
    parser = argparse.ArgumentParser(description='Analyze deforestation in protected areas')
    parser.add_argument('--park', '-p', help='Analyze specific park ID only')
    parser.add_argument('--skip-existing', '-s', action='store_true', help='Skip parks that already have data')
    parser.add_argument('--list', '-l', action='store_true', help='List available tiles')
    args = parser.parse_args()
    
    if args.list:
        tiles = get_available_tiles()
        print(f"Available Hansen tiles ({len(tiles)}):")
        for tile, path in sorted(tiles.items()):
            size = os.path.getsize(path) / (1024*1024)
            print(f"  {tile}: {size:.1f} MB")
        return
    
    analyze_all_parks(single_park_id=args.park, skip_existing=args.skip_existing)


if __name__ == '__main__':
    main()
