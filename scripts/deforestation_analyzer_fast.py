#!/usr/bin/env python3
"""Optimized Deforestation Analysis using Hansen Global Forest Change data.

Key optimizations:
1. Windowed reads instead of full raster masking
2. Vectorized year processing
3. Simplified pattern classification
4. Progress output with buffering disabled
"""

import json
import sqlite3
import numpy as np
import rasterio
from rasterio.windows import from_bounds
from rasterio.transform import rowcol
from shapely.geometry import shape, box
from scipy import ndimage
from collections import defaultdict
import sys
import os
import math
import time

# Force unbuffered output
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)

# Constants
HANSEN_TILE_PATH = 'data/hansen_lossyear_10N_020E.tif'
KEYSTONES_PATH = 'data/keystones_with_boundaries.json'
DB_PATH = 'db.sqlite3'

TILE_BOUNDS = box(20, 0, 30, 10)
PIXEL_SIZE_DEG = 0.00025

def get_pixel_area_km2(lat):
    """Calculate pixel area in km2 accounting for latitude."""
    lat_km = 110.574 * PIXEL_SIZE_DEG
    lon_km = 111.32 * math.cos(math.radians(lat)) * PIXEL_SIZE_DEG
    return lat_km * lon_km

def load_keystones():
    """Load keystones with boundaries."""
    with open(KEYSTONES_PATH, 'r') as f:
        return json.load(f)

def get_parks_in_tile(keystones):
    """Filter parks that intersect with the Hansen tile bounds."""
    parks = []
    for park in keystones:
        if not park.get('geometry'):
            continue
        try:
            geom = shape(park['geometry'])
            if geom.intersects(TILE_BOUNDS):
                clipped = geom.intersection(TILE_BOUNDS)
                if not clipped.is_empty:
                    parks.append({
                        'id': park['id'],
                        'name': park['name'],
                        'country': park['country'],
                        'geometry': clipped,
                        'lat': park['coordinates']['lat'],
                        'lon': park['coordinates']['lon']
                    })
        except Exception as e:
            print(f"Error processing {park.get('id', 'unknown')}: {e}")
    return parks

def classify_pattern_fast(loss_array, threshold=50, min_cluster_pixels=20):
    """Fast pattern classification - analyzes all significant clusters.
    
    Returns:
        pattern: dominant pattern type
        clusters: list of cluster info dicts with centroid_row, centroid_col, pixels, pattern
    """
    total = loss_array.sum()
    if total < threshold:
        return 'minor', []
    
    # Only do expensive labeling for significant events
    if total < 500:
        return 'scattered', []
    
    # Use sampling for very large arrays to speed up labeling
    downsample = 1
    if loss_array.size > 10_000_000:
        downsample = 4
        work_array = loss_array[::4, ::4]
    else:
        work_array = loss_array
    
    labeled, num = ndimage.label(work_array > 0)
    
    if num == 0:
        return 'scattered', []
    
    # Analyze all clusters, not just the largest
    clusters = []
    pattern_votes = defaultdict(int)
    
    # Get sizes for all clusters (limit to first 100 to avoid huge memory)
    max_clusters = min(num + 1, 100)
    cluster_sizes = ndimage.sum(work_array > 0, labeled, range(1, max_clusters))
    
    for idx, size in enumerate(cluster_sizes, 1):
        if size < min_cluster_pixels:
            continue
        
        cluster_mask = labeled == idx
        rows, cols = np.where(cluster_mask)
        
        if len(rows) < 5:
            continue
        
        # Get bounding box
        height = rows.max() - rows.min() + 1
        width = cols.max() - cols.min() + 1
        aspect = max(height, width) / max(min(height, width), 1)
        
        # Calculate fill ratio (compactness)
        bbox_area = height * width
        fill_ratio = size / bbox_area if bbox_area > 0 else 0
        
        # Classify cluster pattern
        if aspect > 5:
            cluster_pattern = 'strip'  # Linear - likely road
        elif fill_ratio > 0.4 and size > 100:
            cluster_pattern = 'cluster'  # Compact - likely mining/clearing
        else:
            cluster_pattern = 'scattered'  # Diffuse - likely farming
        
        # Weight votes by cluster size
        pattern_votes[cluster_pattern] += size
        
        # Store cluster info (adjust coordinates for downsampling)
        clusters.append({
            'id': idx,
            'pixels': int(size * (downsample ** 2)),  # Scale back to full resolution
            'centroid_row': float(rows.mean() * downsample),
            'centroid_col': float(cols.mean() * downsample),
            'pattern': cluster_pattern,
            'aspect_ratio': float(aspect),
            'fill_ratio': float(fill_ratio)
        })
    
    # Determine dominant pattern
    if not pattern_votes:
        return 'scattered', clusters
    
    dominant_pattern = max(pattern_votes, key=pattern_votes.get)
    return dominant_pattern, clusters

def create_park_mask(geometry, window, transform):
    """Create a boolean mask for park boundary within window."""
    from rasterio.features import geometry_mask
    return ~geometry_mask([geometry.__geo_interface__], 
                          out_shape=(window.height, window.width),
                          transform=transform,
                          all_touched=True)

def analyze_park_fast(park, dataset):
    """Analyze deforestation for a single park using windowed reads."""
    start = time.time()
    print(f"  {park['name']}...", end=' ', flush=True)
    
    try:
        geom = park['geometry']
        bounds = geom.bounds  # minx, miny, maxx, maxy
        
        # Get window for park bounds
        window = from_bounds(*bounds, transform=dataset.transform)
        
        # Clamp window to raster bounds
        window = window.intersection(rasterio.windows.Window(0, 0, dataset.width, dataset.height))
        
        if window.width <= 0 or window.height <= 0:
            print("outside raster bounds")
            return {}
        
        # Read just this window
        loss_data = dataset.read(1, window=window)
        
        # Get transform for this window
        win_transform = dataset.window_transform(window)
        
        # Create park mask
        try:
            from rasterio.features import geometry_mask
            park_mask = ~geometry_mask([geom.__geo_interface__],
                                       out_shape=loss_data.shape,
                                       transform=win_transform,
                                       all_touched=True)
        except Exception:
            # Fallback: use all data in window
            park_mask = np.ones(loss_data.shape, dtype=bool)
        
        # Apply park mask
        loss_data = np.where(park_mask, loss_data, 0)
        
        # Quick check if any loss
        if loss_data.max() == 0:
            print("no loss detected")
            return {}
        
        # Analyze by year - vectorized
        yearly_stats = {}
        center_lat = (bounds[1] + bounds[3]) / 2
        pixel_area = get_pixel_area_km2(center_lat)
        
        # Process all years at once
        for year_code in range(1, 25):
            year_mask = loss_data == year_code
            pixel_count = year_mask.sum()
            
            if pixel_count == 0:
                continue
            
            actual_year = 2000 + year_code
            area_km2 = pixel_count * pixel_area
            
            # Find centroid
            rows, cols = np.where(year_mask)
            center_row = rows.mean()
            center_col = cols.mean()
            
            # Convert to lat/lon using window transform
            lon = win_transform.c + center_col * win_transform.a
            lat = win_transform.f + center_row * win_transform.e
            
            # Pattern classification with cluster detection
            pattern, clusters = classify_pattern_fast(year_mask)
            
            # Convert cluster centroids to lat/lon
            for cluster in clusters:
                c_lon = win_transform.c + cluster['centroid_col'] * win_transform.a
                c_lat = win_transform.f + cluster['centroid_row'] * win_transform.e
                cluster['lat'] = round(c_lat, 5)
                cluster['lon'] = round(c_lon, 5)
                cluster['area_km2'] = round(cluster['pixels'] * pixel_area, 4)
            
            # Event type
            if area_km2 > 10:
                event_type = 'major'
            elif area_km2 > 1:
                event_type = 'moderate'
            else:
                event_type = 'minor'
            
            yearly_stats[actual_year] = {
                'year': actual_year,
                'pixel_count': int(pixel_count),
                'area_km2': round(area_km2, 4),
                'pattern_type': pattern,
                'event_type': event_type,
                'lat': round(lat, 5),
                'lon': round(lon, 5),
                'clusters': clusters
            }
        
        elapsed = time.time() - start
        total_area = sum(s['area_km2'] for s in yearly_stats.values())
        print(f"{len(yearly_stats)} years, {total_area:.1f} km² loss ({elapsed:.1f}s)")
        
        return yearly_stats
        
    except Exception as e:
        print(f"error: {e}")
        import traceback
        traceback.print_exc()
        return {}

def generate_description(park, stats):
    """Generate narrative description."""
    area = stats['area_km2']
    pattern = stats['pattern_type']
    year = stats['year']
    
    pattern_desc = {
        'strip': 'linear clearing suggesting road construction',
        'cluster': 'concentrated clearing suggesting mining or logging',
        'scattered': 'dispersed clearing suggesting agricultural expansion',
        'minor': 'minimal forest disturbance'
    }
    
    desc = f"In {year}, {park['name']} ({park['country']}) experienced {area:.2f} km² of forest loss. "
    desc += f"The pattern shows {pattern_desc.get(pattern, 'forest disturbance')}. "
    desc += f"Centered at approximately {abs(stats['lat']):.2f}°{'N' if stats['lat'] >= 0 else 'S'}, "
    desc += f"{abs(stats['lon']):.2f}°{'E' if stats['lon'] >= 0 else 'W'}. "
    
    return desc

def save_to_database(park, yearly_stats):
    """Save results to database including clusters."""
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
            
            # Save individual clusters
            for cluster in stats.get('clusters', []):
                if cluster.get('area_km2', 0) < 0.01:  # Skip tiny clusters
                    continue
                cursor.execute('''
                    INSERT OR REPLACE INTO deforestation_clusters
                    (park_id, year, cluster_id, area_km2, lat, lon, pattern_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    park['id'], year, cluster['id'],
                    cluster.get('area_km2', 0),
                    cluster.get('lat', stats['lat']),
                    cluster.get('lon', stats['lon']),
                    cluster.get('pattern', stats['pattern_type'])
                ))
        
        conn.commit()
    except Exception as e:
        print(f"    DB error: {e}")
        conn.rollback()
    finally:
        conn.close()

def analyze_all_parks(single_park_id=None):
    """Main analysis function."""
    print("Optimized Deforestation Analysis")
    print("=" * 50)
    
    keystones = load_keystones()
    parks = get_parks_in_tile(keystones)
    print(f"Found {len(parks)} parks in Hansen tile coverage")
    
    if single_park_id:
        parks = [p for p in parks if p['id'] == single_park_id]
        if not parks:
            print(f"Park {single_park_id} not found")
            return
    
    if not os.path.exists(HANSEN_TILE_PATH):
        print(f"ERROR: Hansen tile not found at {HANSEN_TILE_PATH}")
        return
    
    print(f"\nOpening {HANSEN_TILE_PATH}...")
    
    with rasterio.open(HANSEN_TILE_PATH) as dataset:
        print(f"Raster: {dataset.width}x{dataset.height}, CRS: {dataset.crs}")
        print(f"\nProcessing {len(parks)} parks:\n")
        
        start_all = time.time()
        total_stats = {}
        
        for i, park in enumerate(parks, 1):
            print(f"[{i}/{len(parks)}]", end=' ')
            stats = analyze_park_fast(park, dataset)
            if stats:
                save_to_database(park, stats)
                total_stats[park['id']] = stats
        
        elapsed_all = time.time() - start_all
        
    # Summary
    print(f"\n{'=' * 50}")
    print(f"Complete in {elapsed_all:.1f}s ({elapsed_all/60:.1f} min)")
    print(f"Parks processed: {len(total_stats)}")
    
    total_loss = sum(sum(s['area_km2'] for s in stats.values()) for stats in total_stats.values())
    print(f"Total forest loss detected: {total_loss:.2f} km²")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--park', '-p', help='Analyze specific park ID')
    parser.add_argument('--list', '-l', action='store_true', help='List parks')
    args = parser.parse_args()
    
    if args.list:
        keystones = load_keystones()
        parks = get_parks_in_tile(keystones)
        print("Parks in Hansen tile (20E-30E, 0N-10N):")
        for p in parks:
            print(f"  {p['id']}: {p['name']}")
        return
    
    analyze_all_parks(single_park_id=args.park)

if __name__ == '__main__':
    main()
