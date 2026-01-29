#!/usr/bin/env python3
"""Optimized deforestation analysis - uses windowed reading for large tiles."""

import json
import sqlite3
import numpy as np
import rasterio
from rasterio.mask import mask
from rasterio.windows import from_bounds
from shapely.geometry import shape, mapping, box
from scipy import ndimage
from collections import defaultdict
import os
import glob
import math
import sys

HANSEN_DIR = 'data/hansen'
KEYSTONES_PATH = 'data/keystones_with_boundaries.json'
DB_PATH = 'db.sqlite3'
PIXEL_SIZE_DEG = 0.00025


def get_tile_name(lat, lon):
    if lat >= 0:
        lat_band = (int(lat) // 10 + 1) * 10
        lat_str = f"{lat_band:02d}N"
    elif lat > -10:
        lat_str = "00N"
    else:
        lat_band = (int(-lat) // 10) * 10
        lat_str = f"{lat_band:02d}S"
    
    if lon >= 0:
        lon_band = (int(lon) // 10) * 10
        lon_str = f"{lon_band:03d}E"
    else:
        lon_band = (int(-lon - 0.0001) // 10 + 1) * 10
        lon_str = f"{lon_band:03d}W"
    
    return f"{lat_str}_{lon_str}"


def get_tile_bounds(tile_name):
    lat_part, lon_part = tile_name.split('_')
    lat_val = int(lat_part[:-1])
    lat_hem = lat_part[-1]
    lon_val = int(lon_part[:-1])
    lon_hem = lon_part[-1]
    
    if lat_hem == 'N':
        max_lat, min_lat = lat_val, lat_val - 10
    else:
        max_lat, min_lat = -lat_val, -lat_val - 10
    
    if lon_hem == 'E':
        min_lon, max_lon = lon_val, lon_val + 10
    else:
        min_lon, max_lon = -lon_val, -lon_val + 10
    
    return box(min_lon, min_lat, max_lon, max_lat)


def get_tiles_for_geometry(geom):
    bounds = geom.bounds
    minx, miny, maxx, maxy = bounds
    tiles = set()
    for lat in np.arange(miny, maxy + 0.1, 5):
        for lon in np.arange(minx, maxx + 0.1, 5):
            tiles.add(get_tile_name(lat, lon))
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
    tiles = {}
    pattern = os.path.join(HANSEN_DIR, 'Hansen_GFC-2024-v1.12_lossyear_*.tif')
    for filepath in glob.glob(pattern):
        filename = os.path.basename(filepath)
        parts = filename.replace('.tif', '').split('_')
        tile_name = f"{parts[-2]}_{parts[-1]}"
        tiles[tile_name] = filepath
    return tiles


def get_pixel_area_km2(lat):
    lat_km = 110.574 * PIXEL_SIZE_DEG
    lon_km = 111.32 * math.cos(math.radians(abs(lat))) * PIXEL_SIZE_DEG
    return lat_km * lon_km


def classify_pattern(loss_array, threshold_pixels=50):
    if loss_array.sum() < threshold_pixels:
        return 'minor', []
    
    labeled, num_features = ndimage.label(loss_array > 0)
    if num_features == 0:
        return 'none', []
    
    cluster_info = []
    pattern_votes = defaultdict(int)
    
    for cluster_id in range(1, min(num_features + 1, 100)):  # Limit clusters
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
            'pixels': int(cluster_pixels),
            'pattern': cluster_pattern,
        })
    
    if not pattern_votes:
        return 'minor', cluster_info
    return max(pattern_votes, key=pattern_votes.get), cluster_info


def analyze_park(park_id, park_name, country, geom, tile_paths):
    """Analyze a single park using windowed reading."""
    all_stats = {}
    
    for tile_name, tile_path in tile_paths.items():
        tile_bounds = get_tile_bounds(tile_name)
        clipped_geom = geom.intersection(tile_bounds)
        if clipped_geom.is_empty:
            continue
        
        try:
            with rasterio.open(tile_path) as ds:
                # Use windowed reading based on geometry bounds
                geom_bounds = clipped_geom.bounds
                window = from_bounds(*geom_bounds, ds.transform)
                
                # Read only the window we need
                data = ds.read(1, window=window)
                win_transform = ds.window_transform(window)
                
                # Create mask for the geometry within the window
                from rasterio.features import geometry_mask
                geom_mask = geometry_mask(
                    [mapping(clipped_geom)], 
                    out_shape=data.shape,
                    transform=win_transform,
                    invert=True
                )
                
                # Apply mask
                loss_data = np.where(geom_mask, data, 0)
                
                minx, miny, maxx, maxy = geom_bounds
                center_lat = (miny + maxy) / 2
                rows, cols = loss_data.shape
                
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
                    
                    if actual_year in all_stats:
                        all_stats[actual_year]['pixel_count'] += int(pixel_count)
                        all_stats[actual_year]['area_km2'] += area_km2
                        all_stats[actual_year]['clusters'].extend(clusters)
                    else:
                        all_stats[actual_year] = {
                            'year': actual_year,
                            'pixel_count': int(pixel_count),
                            'area_km2': area_km2,
                            'lat': lat,
                            'lon': lon,
                            'pattern_type': pattern,
                            'clusters': clusters
                        }
                        
        except Exception as e:
            print(f"      Error with tile {tile_name}: {e}")
    
    # Finalize stats
    for year, stats in all_stats.items():
        stats['area_km2'] = round(stats['area_km2'], 4)
        if stats['area_km2'] > 10:
            stats['event_type'] = 'major'
        elif stats['area_km2'] > 1:
            stats['event_type'] = 'moderate'
        else:
            stats['event_type'] = 'minor'
    
    return all_stats


def save_to_database(park_id, park_name, country, yearly_stats):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')
    cursor = conn.cursor()
    
    try:
        for year, stats in yearly_stats.items():
            area = stats['area_km2']
            pattern = stats['pattern_type']
            
            pattern_desc = {
                'strip': 'linear clearing suggesting road construction',
                'cluster': 'concentrated clearing suggesting mining or logging',
                'scattered': 'dispersed clearing suggesting agricultural expansion',
                'minor': 'minimal forest disturbance'
            }
            
            desc = f"In {year}, {park_name} ({country}) experienced {area:.2f} km² of forest loss. "
            desc += f"The pattern shows {pattern_desc.get(pattern, 'forest disturbance')}."
            
            geojson = json.dumps({
                'type': 'Point',
                'coordinates': [stats['lon'], stats['lat']]
            })
            
            cursor.execute('''
                INSERT OR REPLACE INTO deforestation_events 
                (park_id, year, area_km2, event_type, lat, lon, geojson, description, pattern_type, pixel_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                park_id, year, stats['area_km2'], stats['event_type'],
                round(stats['lat'], 5), round(stats['lon'], 5),
                geojson, desc, stats['pattern_type'], stats['pixel_count']
            ))
            
            for cluster in stats.get('clusters', [])[:20]:  # Limit clusters saved
                if cluster['pixels'] < 20:
                    continue
                cursor.execute('''
                    INSERT INTO deforestation_clusters
                    (park_id, year, cluster_id, area_km2, lat, lon, pattern_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    park_id, year, cluster['id'],
                    round(cluster['pixels'] * get_pixel_area_km2(stats['lat']), 4),
                    round(stats['lat'], 5), round(stats['lon'], 5),
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


def main():
    print("=" * 60)
    print("Deforestation Analysis - Optimized")
    print("=" * 60)
    
    available_tiles = get_available_tiles()
    print(f"\nAvailable tiles: {len(available_tiles)}")
    
    with open(KEYSTONES_PATH, 'r') as f:
        keystones = json.load(f)
    print(f"Total parks: {len(keystones)}")
    
    # Get existing
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT park_id FROM deforestation_events")
    existing = {row[0] for row in cursor.fetchall()}
    conn.close()
    print(f"Already processed: {len(existing)}")
    
    processed = 0
    failed = 0
    
    for i, park in enumerate(keystones):
        if not park.get('geometry'):
            continue
        if park['id'] in existing:
            continue
        
        try:
            geom = shape(park['geometry'])
            needed_tiles = get_tiles_for_geometry(geom)
            tile_paths = {t: available_tiles[t] for t in needed_tiles if t in available_tiles}
            
            if not tile_paths:
                continue
            
            print(f"[{i+1}/{len(keystones)}] {park['id']}...", end=' ', flush=True)
            
            stats = analyze_park(park['id'], park['name'], park['country'], geom, tile_paths)
            
            if stats:
                if save_to_database(park['id'], park['name'], park['country'], stats):
                    total_loss = sum(s['area_km2'] for s in stats.values())
                    print(f"OK ({len(stats)} years, {total_loss:.2f} km²)")
                    processed += 1
                else:
                    print("DB ERROR")
                    failed += 1
            else:
                print("No loss")
                processed += 1
                
        except Exception as e:
            print(f"ERROR: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"COMPLETE: {processed} processed, {failed} failed")
    
    # Summary
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(DISTINCT park_id), COUNT(*), ROUND(SUM(area_km2), 2)
        FROM deforestation_events
    """)
    parks, events, total_km2 = cursor.fetchone()
    print(f"Total: {parks} parks, {events} events, {total_km2} km² loss")
    conn.close()


if __name__ == '__main__':
    main()
