#!/usr/bin/env python3
"""Deforestation Analysis using Hansen Global Forest Change data.

Analyzes forest loss within keystone protected areas using Hansen GFC-2024 data.
Values: 0 = no loss, 1-24 = year of loss (2001-2024)

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

# Constants
HANSEN_TILE_PATH = 'data/hansen_lossyear_10N_020E.tif'
KEYSTONES_PATH = 'data/keystones_with_boundaries.json'
DB_PATH = 'db.sqlite3'

# Tile coverage bounds (20E-30E, 0N-10N)
TILE_BOUNDS = box(20, 0, 30, 10)

# Pixel area at equator (approx) - Hansen is 30m resolution but stored as 0.00025 degrees
# At equator, 1 degree ~ 111km, so 0.00025 degrees ~ 27.75m
PIXEL_SIZE_DEG = 0.00025

def get_pixel_area_km2(lat):
    """Calculate pixel area in km2 accounting for latitude."""
    # 1 degree longitude = 111.32 * cos(lat) km
    # 1 degree latitude = 110.574 km
    lat_km = 110.574 * PIXEL_SIZE_DEG  # km per pixel in latitude
    lon_km = 111.32 * math.cos(math.radians(lat)) * PIXEL_SIZE_DEG  # km per pixel in longitude
    return lat_km * lon_km

def load_keystones():
    """Load keystones with boundaries."""
    with open(KEYSTONES_PATH, 'r') as f:
        keystones = json.load(f)
    return keystones

def get_parks_in_tile(keystones):
    """Filter parks that intersect with the Hansen tile bounds."""
    parks_in_tile = []
    for park in keystones:
        if not park.get('geometry'):
            continue
        try:
            geom = shape(park['geometry'])
            if geom.intersects(TILE_BOUNDS):
                # Clip geometry to tile bounds
                clipped = geom.intersection(TILE_BOUNDS)
                if not clipped.is_empty:
                    park_info = {
                        'id': park['id'],
                        'name': park['name'],
                        'country': park['country'],
                        'geometry': clipped,
                        'original_geometry': geom,
                        'lat': park['coordinates']['lat'],
                        'lon': park['coordinates']['lon']
                    }
                    parks_in_tile.append(park_info)
        except Exception as e:
            print(f"Error processing {park.get('id', 'unknown')}: {e}")
    return parks_in_tile

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
        
        # Get cluster bounding box
        rows, cols = np.where(cluster_mask)
        if len(rows) == 0:
            continue
            
        height = rows.max() - rows.min() + 1
        width = cols.max() - cols.min() + 1
        
        # Calculate aspect ratio
        aspect_ratio = max(height, width) / max(min(height, width), 1)
        
        # Calculate fill ratio (compactness)
        bbox_area = height * width
        fill_ratio = cluster_pixels / bbox_area if bbox_area > 0 else 0
        
        # Classify individual cluster
        if aspect_ratio > 5:
            cluster_pattern = 'strip'  # Linear - likely road
        elif fill_ratio > 0.5 and cluster_pixels > 100:
            cluster_pattern = 'cluster'  # Compact - likely mining/clearing
        else:
            cluster_pattern = 'scattered'  # Diffuse - likely farming
        
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
    
    # Determine dominant pattern
    if not pattern_votes:
        return 'minor', cluster_info
    
    dominant_pattern = max(pattern_votes, key=pattern_votes.get)
    return dominant_pattern, cluster_info

def analyze_park(park, dataset):
    """Analyze deforestation for a single park."""
    print(f"\n  Analyzing {park['name']}...")
    
    try:
        # Create GeoJSON-like geometry for masking
        geom_geojson = [mapping(park['geometry'])]
        
        # Mask raster to park boundary
        out_image, out_transform = mask(dataset, geom_geojson, crop=True, nodata=0)
        loss_data = out_image[0]  # Single band
        
        # Get bounds for coordinate conversion
        park_bounds = park['geometry'].bounds
        minx, miny, maxx, maxy = park_bounds
        
        # Calculate pixel dimensions
        rows, cols = loss_data.shape
        
        # Analyze by year
        yearly_stats = {}
        
        for year_code in range(1, 25):  # 1-24 for 2001-2024
            actual_year = 2000 + year_code
            year_mask = loss_data == year_code
            pixel_count = year_mask.sum()
            
            if pixel_count == 0:
                continue
            
            # Calculate area (account for latitude)
            center_lat = (miny + maxy) / 2
            pixel_area = get_pixel_area_km2(center_lat)
            area_km2 = pixel_count * pixel_area
            
            # Classify pattern
            pattern, clusters = classify_pattern(year_mask)
            
            # Find centroid of all loss
            loss_rows, loss_cols = np.where(year_mask)
            if len(loss_rows) > 0:
                center_row = loss_rows.mean()
                center_col = loss_cols.mean()
                
                # Convert to lat/lon
                lat = maxy - (center_row / rows) * (maxy - miny)
                lon = minx + (center_col / cols) * (maxx - minx)
            else:
                lat, lon = center_lat, (minx + maxx) / 2
            
            # Determine event type based on area
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
            
            print(f"    {actual_year}: {area_km2:.2f} km² ({pixel_count} px) - {pattern}")
        
        return yearly_stats
        
    except Exception as e:
        print(f"    Error analyzing {park['name']}: {e}")
        import traceback
        traceback.print_exc()
        return {}

def generate_description(park, stats, nearby_places=None):
    """Generate narrative description for deforestation event."""
    area = stats['area_km2']
    pattern = stats['pattern_type']
    year = stats['year']
    
    # Pattern descriptions
    pattern_desc = {
        'strip': 'linear clearing suggesting road construction or infrastructure development',
        'cluster': 'concentrated clearing suggesting mining activity or logging operation',
        'scattered': 'dispersed clearing suggesting agricultural expansion or smallholder farming',
        'edge': 'boundary clearing suggesting encroachment from surrounding areas',
        'minor': 'minimal forest disturbance'
    }
    
    desc = f"In {year}, {park['name']} ({park['country']}) experienced {area:.2f} km² of forest loss. "
    desc += f"The pattern shows {pattern_desc.get(pattern, 'forest disturbance')}. "
    
    # Add location context
    desc += f"Centered at approximately {abs(stats['lat']):.2f}°{'N' if stats['lat'] >= 0 else 'S'}, "
    desc += f"{abs(stats['lon']):.2f}°{'E' if stats['lon'] >= 0 else 'W'}. "
    
    # Add nearby places if available (from Task 8 data)
    if nearby_places:
        desc += f"Near {nearby_places}. "
    
    return desc

def save_to_database(park, yearly_stats):
    """Save deforestation analysis results to database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')
    cursor = conn.cursor()
    
    try:
        for year, stats in yearly_stats.items():
            description = generate_description(park, stats)
            
            # Create simple GeoJSON point for centroid
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
                stats['area_km2'],
                stats['event_type'],
                stats['lat'],
                stats['lon'],
                geojson,
                description,
                stats['pattern_type'],
                stats['pixel_count']
            ))
            
            # Save individual clusters
            for cluster in stats.get('clusters', []):
                # Skip very small clusters
                if cluster['pixels'] < 20:
                    continue
                    
                # Approximate cluster centroid lat/lon (simplified)
                park_bounds = park['geometry'].bounds
                minx, miny, maxx, maxy = park_bounds
                # This is approximate - would need transform info for exact coords
                
                cursor.execute('''
                    INSERT INTO deforestation_clusters
                    (park_id, year, cluster_id, area_km2, lat, lon, pattern_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    park['id'],
                    year,
                    cluster['id'],
                    round(cluster['pixels'] * get_pixel_area_km2(stats['lat']), 4),
                    stats['lat'],  # Using event centroid as approximation
                    stats['lon'],
                    cluster['pattern']
                ))
        
        conn.commit()
        print(f"    Saved {len(yearly_stats)} years of data for {park['name']}")
        
    except Exception as e:
        print(f"    Database error: {e}")
        conn.rollback()
    finally:
        conn.close()

def analyze_all_parks(single_park_id=None):
    """Main analysis function."""
    print("Deforestation Analysis using Hansen GFC-2024")
    print("=" * 50)
    
    # Load keystones
    print("\nLoading keystone parks...")
    keystones = load_keystones()
    parks = get_parks_in_tile(keystones)
    print(f"Found {len(parks)} parks within Hansen tile coverage")
    
    # Filter to single park if specified
    if single_park_id:
        parks = [p for p in parks if p['id'] == single_park_id]
        if not parks:
            print(f"Park {single_park_id} not found in tile coverage")
            return
    
    # Open Hansen raster
    print(f"\nOpening Hansen data: {HANSEN_TILE_PATH}")
    if not os.path.exists(HANSEN_TILE_PATH):
        print(f"ERROR: Hansen tile not found at {HANSEN_TILE_PATH}")
        return
    
    with rasterio.open(HANSEN_TILE_PATH) as dataset:
        print(f"  Raster size: {dataset.width} x {dataset.height}")
        print(f"  CRS: {dataset.crs}")
        print(f"  Bounds: {dataset.bounds}")
        
        total_stats = {}
        
        for park in parks:
            stats = analyze_park(park, dataset)
            if stats:
                save_to_database(park, stats)
                total_stats[park['id']] = stats
    
    # Summary
    print("\n" + "=" * 50)
    print("Analysis Summary")
    print("=" * 50)
    
    for park_id, stats in total_stats.items():
        total_area = sum(s['area_km2'] for s in stats.values())
        years_with_loss = len(stats)
        print(f"\n{park_id}:")
        print(f"  Years with loss: {years_with_loss}")
        print(f"  Total loss: {total_area:.2f} km²")
        
        # Find worst year
        if stats:
            worst_year = max(stats.items(), key=lambda x: x[1]['area_km2'])
            print(f"  Worst year: {worst_year[0]} ({worst_year[1]['area_km2']:.2f} km²)")

def main():
    """Entry point."""
    import argparse
    parser = argparse.ArgumentParser(description='Analyze deforestation in protected areas')
    parser.add_argument('--park', '-p', help='Analyze specific park ID only')
    parser.add_argument('--list', '-l', action='store_true', help='List parks in tile coverage')
    args = parser.parse_args()
    
    if args.list:
        keystones = load_keystones()
        parks = get_parks_in_tile(keystones)
        print("Parks within Hansen tile coverage (20E-30E, 0N-10N):")
        for park in parks:
            print(f"  {park['id']}: {park['name']} ({park['country']})")
        return
    
    analyze_all_parks(single_park_id=args.park)

if __name__ == '__main__':
    main()
