#!/usr/bin/env python3
"""Deforestation Analysis using Hansen Global Forest Change data - Multi-tile version.

Analyzes forest loss within ALL keystone protected areas using Hansen GFC-2024 data.
Automatically detects which tile(s) each park needs based on its coordinates.
"""

import json
import sqlite3
import numpy as np
import rasterio
from rasterio.mask import mask
from shapely.geometry import shape, mapping, box
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
PIXEL_SIZE_DEG = 0.00025

def get_tile_name(lat, lon):
    """Convert lat/lon to Hansen tile name.
    
    Tiles are 10x10 degree blocks. Names indicate the corner closest to 0,0.
    Examples:
    - Point at 5°N, 25°E -> tile 00N_020E (covers 0-10°N, 20-30°E)
    - Point at 15°N, 5°W -> tile 10N_010W (covers 10-20°N, 10-0°W)
    - Point at -15°S, 25°E -> tile 20S_020E (covers 10-20°S, 20-30°E)
    """
    # Determine latitude band
    if lat >= 0:
        lat_band = int(lat // 10) * 10
        lat_str = f"{lat_band:02d}N"
    else:
        lat_band = int((-lat) // 10 + 1) * 10 if lat % 10 != 0 else int(-lat // 10) * 10
        lat_str = f"{lat_band:02d}S"
    
    # Determine longitude band
    if lon >= 0:
        lon_band = int(lon // 10) * 10
        lon_str = f"{lon_band:03d}E"
    else:
        lon_band = int((-lon) // 10 + 1) * 10 if lon % 10 != 0 else int(-lon // 10) * 10
        lon_str = f"{lon_band:03d}W"
    
    return f"{lat_str}_{lon_str}"

def get_tile_bounds(tile_name):
    """Get bounding box for a tile name."""
    lat_part, lon_part = tile_name.split('_')
    
    lat_val = int(lat_part[:-1])
    lat_hem = lat_part[-1]
    lon_val = int(lon_part[:-1])
    lon_hem = lon_part[-1]
    
    if lat_hem == 'N':
        min_lat, max_lat = lat_val, lat_val + 10
    else:
        min_lat, max_lat = -lat_val, -lat_val + 10
    
    if lon_hem == 'E':
        min_lon, max_lon = lon_val, lon_val + 10
    else:
        min_lon, max_lon = -lon_val, -lon_val + 10
    
    return box(min_lon, min_lat, max_lon, max_lat)

def get_tiles_for_geometry(geom):
    """Get list of tile names that intersect with a geometry."""
    bounds = geom.bounds
    minx, miny, maxx, maxy = bounds
    
    tiles = set()
    # Check corners and intermediate points
    for lat in [miny, (miny+maxy)/2, maxy]:
        for lon in [minx, (minx+maxx)/2, maxx]:
            tiles.add(get_tile_name(lat, lon))
    
    # Filter to tiles that actually intersect
    valid_tiles = []
    for tile in tiles:
        tile_bounds = get_tile_bounds(tile)
        if geom.intersects(tile_bounds):
            valid_tiles.append(tile)
    
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
    lon_km = 111.32 * math.cos(math.radians(lat)) * PIXEL_SIZE_DEG
    return lat_km * lon_km

def load_keystones():
    """Load keystones with boundaries."""
    with open(KEYSTONES_PATH, 'r') as f:
        return json.load(f)

def classify_pattern(loss_array, threshold_pixels=50):
    """Classify deforestation pattern based on spatial characteristics."""
    if loss_array.sum() < threshold_pixels:
        return 'minor', []
    
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

def analyze_park_with_tiles(park, tile_datasets):
    """Analyze deforestation for a park using multiple tile datasets."""
    geom = park['geometry']
    all_stats = {}
    
    for tile_name, dataset in tile_datasets.items():
        tile_bounds = get_tile_bounds(tile_name)
        clipped_geom = geom.intersection(tile_bounds)
        
        if clipped_geom.is_empty:
            continue
        
        try:
            geom_geojson = [mapping(clipped_geom)]
            out_image, out_transform = mask(dataset, geom_geojson, crop=True, nodata=0)
            loss_data = out_image[0]
            
            park_bounds = clipped_geom.bounds
            minx, miny, maxx, maxy = park_bounds
            rows, cols = loss_data.shape
            center_lat = (miny + maxy) / 2
            
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
                if len(loss_rows) > 0:
                    center_row = loss_rows.mean()
                    center_col = loss_cols.mean()
                    lat = maxy - (center_row / rows) * (maxy - miny) if rows > 0 else center_lat
                    lon = minx + (center_col / cols) * (maxx - minx) if cols > 0 else (minx + maxx) / 2
                else:
                    lat, lon = center_lat, (minx + maxx) / 2
                
                if area_km2 > 10:
                    event_type = 'major'
                elif area_km2 > 1:
                    event_type = 'moderate'
                else:
                    event_type = 'minor'
                
                # Merge with existing year stats if any
                if actual_year in all_stats:
                    existing = all_stats[actual_year]
                    existing['pixel_count'] += int(pixel_count)
                    existing['area_km2'] += area_km2
                    existing['clusters'].extend(clusters)
                else:
                    all_stats[actual_year] = {
                        'year': actual_year,
                        'pixel_count': int(pixel_count),
                        'area_km2': area_km2,
                        'pattern_type': pattern,
                        'event_type': event_type,
                        'lat': round(lat, 5),
                        'lon': round(lon, 5),
                        'clusters': clusters
                    }
        except Exception as e:
            print(f"      Error with tile {tile_name}: {e}")
    
    # Round and update event types
    for year, stats in all_stats.items():
        stats['area_km2'] = round(stats['area_km2'], 4)
        if stats['area_km2'] > 10:
            stats['event_type'] = 'major'
        elif stats['area_km2'] > 1:
            stats['event_type'] = 'moderate'
        else:
            stats['event_type'] = 'minor'
    
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
                park['id'], year, stats['area_km2'], stats['event_type'],
                stats['lat'], stats['lon'], geojson, description,
                stats['pattern_type'], stats['pixel_count']
            ))
            
            for cluster in stats.get('clusters', []):
                if cluster['pixels'] < 20:
                    continue
                cursor.execute('''
                    INSERT INTO deforestation_clusters
                    (park_id, year, cluster_id, area_km2, lat, lon, pattern_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    park['id'], year, cluster['id'],
                    round(cluster['pixels'] * get_pixel_area_km2(stats['lat']), 4),
                    stats['lat'], stats['lon'], cluster['pattern']
                ))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"      DB error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def main():
    """Main analysis - process all parks."""
    import argparse
    parser = argparse.ArgumentParser(description='Analyze deforestation in ALL protected areas')
    parser.add_argument('--park', '-p', help='Analyze specific park ID only')
    parser.add_argument('--skip-existing', action='store_true', help='Skip parks that already have data')
    parser.add_argument('--list-tiles', action='store_true', help='List available tiles')
    args = parser.parse_args()
    
    print("=" * 60)
    print("Deforestation Analysis - Multi-Tile Processing")
    print("=" * 60)
    
    # Get available tiles
    available_tiles = get_available_tiles()
    print(f"\nAvailable Hansen tiles: {len(available_tiles)}")
    
    if args.list_tiles:
        for tile, path in sorted(available_tiles.items()):
            size = os.path.getsize(path) / (1024*1024)
            print(f"  {tile}: {size:.1f} MB")
        return
    
    # Load keystones
    keystones = load_keystones()
    print(f"Total keystone parks: {len(keystones)}")
    
    # Get existing parks if skipping
    existing_parks = set()
    if args.skip_existing:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT park_id FROM deforestation_events")
        existing_parks = {row[0] for row in cursor.fetchall()}
        conn.close()
        print(f"Parks with existing data: {len(existing_parks)}")
    
    # Build list of parks to process
    parks_to_process = []
    for park in keystones:
        if not park.get('geometry'):
            continue
        if args.park and park['id'] != args.park:
            continue
        if args.skip_existing and park['id'] in existing_parks:
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
        except Exception as e:
            print(f"Error processing {park.get('id')}: {e}")
    
    print(f"Parks to process: {len(parks_to_process)}")
    
    if not parks_to_process:
        print("No parks to process!")
        return
    
    # Group parks by tile to minimize file opens
    tile_parks = defaultdict(list)
    for park in parks_to_process:
        for tile in park['tiles']:
            tile_parks[tile].append(park)
    
    print(f"\nProcessing by tile to optimize memory...")
    processed = 0
    failed = 0
    
    for tile_name in sorted(tile_parks.keys()):
        parks = tile_parks[tile_name]
        tile_path = available_tiles[tile_name]
        
        print(f"\n[Tile {tile_name}] Processing {len(parks)} parks...")
        
        try:
            with rasterio.open(tile_path) as dataset:
                for park in parks:
                    # Skip if already processed by another tile
                    if park.get('_processed'):
                        continue
                    
                    # Get all needed tile datasets for this park
                    tile_datasets = {tile_name: dataset}
                    
                    # If park spans multiple tiles, open them all
                    other_tiles = [t for t in park['tiles'] if t != tile_name]
                    other_datasets = {}
                    for other_tile in other_tiles:
                        if other_tile in available_tiles:
                            other_datasets[other_tile] = rasterio.open(available_tiles[other_tile])
                            tile_datasets[other_tile] = other_datasets[other_tile]
                    
                    try:
                        print(f"  {park['id']}...", end=' ', flush=True)
                        stats = analyze_park_with_tiles(park, tile_datasets)
                        
                        if stats:
                            if save_to_database(park, stats):
                                total_loss = sum(s['area_km2'] for s in stats.values())
                                print(f"OK ({len(stats)} years, {total_loss:.2f} km²)")
                                processed += 1
                            else:
                                print("DB ERROR")
                                failed += 1
                        else:
                            print("No loss detected")
                            processed += 1
                        
                        park['_processed'] = True
                        
                    except Exception as e:
                        print(f"ERROR: {e}")
                        failed += 1
                    finally:
                        for ds in other_datasets.values():
                            ds.close()
                            
        except Exception as e:
            print(f"  Failed to open tile: {e}")
            failed += len(parks)
    
    print("\n" + "=" * 60)
    print(f"COMPLETE: {processed} processed, {failed} failed")
    print("=" * 60)

if __name__ == '__main__':
    main()
