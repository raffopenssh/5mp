#!/usr/bin/env python3
"""
Fire Analysis v3 - Memory Efficient Park-by-Park Processing

Processes one park at a time instead of loading all fires into memory.
"""

import os
import sys
import json
import math
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
BUFFER_DIR = next(BASE_DIR.glob("data/raw-fire-viirs-*"), BASE_DIR / "data" / "fire_additional_buffer")
NRT_DIR = BASE_DIR / "data" / "fire_nrt"
KEYSTONES_FILE = BASE_DIR / "data" / "keystones_with_boundaries.json"
OUTPUT_DIR = BASE_DIR / "data" / "fire_groups_20200101_20260222"

GRID_SIZE = 0.03
CLUSTER_DIST_KM = 3.0
TIME_GAP_DAYS = 2
MIN_DATE = "2020-01-01"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_parks():
    with open(KEYSTONES_FILE) as f:
        data = json.load(f)
    parks = {}
    for p in data:
        park_id = p.get('id')
        if not park_id or 'geometry' not in p:
            continue
        parks[park_id] = {
            'id': park_id,
            'name': p.get('name', park_id),
            'country': p.get('country', ''),
            'geometry': p['geometry']
        }
    return parks

def haversine(lon1, lat1, lon2, lat2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def grid_key(lon, lat):
    return (int(lon / GRID_SIZE), int(lat / GRID_SIZE))

def point_in_polygon(lon, lat, geometry):
    coords = geometry.get('coordinates', [])
    if geometry['type'] == 'MultiPolygon':
        for poly in coords:
            if point_in_ring(lon, lat, poly[0]):
                return True
        return False
    elif geometry['type'] == 'Polygon':
        return point_in_ring(lon, lat, coords[0])
    return False

def point_in_ring(x, y, ring):
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-10) + xi):
            inside = not inside
        j = i
    return inside

def load_park_fires(park_id):
    """Load fires for a single park from buffer and NRT files."""
    fires = []
    
    # Load from unified buffer file
    buffer_file = BUFFER_DIR / f"{park_id}.json"
    if buffer_file.exists():
        try:
            with open(buffer_file) as f:
                data = json.load(f)
                park_fires = data.get('fires', data) if isinstance(data, dict) else data
                for fire in park_fires:
                    date = fire.get('acq_date', fire.get('date', ''))
                    if date >= MIN_DATE:
                        fires.append({
                            'latitude': fire.get('latitude', fire.get('lat')),
                            'longitude': fire.get('longitude', fire.get('lng')),
                            'acq_date': date,
                            'acq_time': fire.get('acq_time', fire.get('time', '')),
                            'frp': fire.get('frp', 0),
                            'confidence': fire.get('confidence', ''),
                        })
        except Exception as e:
            log(f"  Error loading {buffer_file}: {e}")
    
    # Load from NRT
    nrt_file = NRT_DIR / f"{park_id}_nrt.json"
    if nrt_file.exists():
        try:
            with open(nrt_file) as f:
                data = json.load(f)
                nrt_fires = data.get('fires', [])
                for fire in nrt_fires:
                    date = fire.get('date', '')
                    if date >= MIN_DATE:
                        fires.append({
                            'latitude': fire.get('lat'),
                            'longitude': fire.get('lon'),
                            'acq_date': date,
                            'acq_time': fire.get('time', ''),
                            'frp': fire.get('frp', 0),
                            'confidence': fire.get('confidence', ''),
                        })
        except:
            pass
    
    return fires

def cluster_fires(fires):
    """Cluster fires using union-find with spatial-temporal constraints."""
    if not fires:
        return []
    
    sorted_fires = sorted(fires, key=lambda f: (f['acq_date'], f.get('acq_time', '0000')))
    grid = defaultdict(list)
    for i, f in enumerate(sorted_fires):
        key = grid_key(f['longitude'], f['latitude'])
        grid[key].append(i)
    
    parent = list(range(len(sorted_fires)))
    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
    
    for i, f in enumerate(sorted_fires):
        gx, gy = grid_key(f['longitude'], f['latitude'])
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                for j in grid.get((gx+dx, gy+dy), []):
                    if j <= i:
                        continue
                    f2 = sorted_fires[j]
                    d1 = datetime.strptime(f['acq_date'], '%Y-%m-%d')
                    d2 = datetime.strptime(f2['acq_date'], '%Y-%m-%d')
                    if abs((d2 - d1).days) > TIME_GAP_DAYS:
                        continue
                    dist = haversine(f['longitude'], f['latitude'], f2['longitude'], f2['latitude'])
                    if dist <= CLUSTER_DIST_KM:
                        union(i, j)
    
    clusters = defaultdict(list)
    for i, f in enumerate(sorted_fires):
        clusters[find(i)].append(f)
    
    return list(clusters.values())

def build_trajectory(fires):
    """Build trajectory using 4-hour time windows (zigzag fix)."""
    if not fires:
        return []
    
    fires_by_date = defaultdict(list)
    for f in fires:
        fires_by_date[f['acq_date']].append(f)
    
    trajectory = []
    for date in sorted(fires_by_date.keys()):
        day_fires = fires_by_date[date]
        
        # Group by 4-hour periods
        periods = defaultdict(list)
        for f in day_fires:
            time_str = f.get('acq_time', '0000') or '0000'
            try:
                hour = int(time_str[:2]) if len(time_str) >= 2 else 0
            except:
                hour = 0
            period = hour // 4
            periods[period].append(f)
        
        # Centroid per period
        for period in sorted(periods.keys()):
            pf = periods[period]
            lon = sum(f['longitude'] for f in pf) / len(pf)
            lat = sum(f['latitude'] for f in pf) / len(pf)
            trajectory.append([lon, lat, date, pf[0].get('acq_time', '0000')])
    
    return trajectory

def analyze_group(cluster, park_geometry):
    """Analyze a fire cluster/group."""
    if not cluster:
        return None
    
    sorted_fires = sorted(cluster, key=lambda f: f['acq_date'])
    start_date = sorted_fires[0]['acq_date']
    end_date = sorted_fires[-1]['acq_date']
    days = (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days + 1
    
    lats = [f['latitude'] for f in cluster]
    lons = [f['longitude'] for f in cluster]
    centroid = [sum(lons)/len(lons), sum(lats)/len(lats)]
    
    trajectory = build_trajectory(sorted_fires)
    
    # Calculate distance
    if len(trajectory) >= 2:
        distance_km = sum(haversine(trajectory[i][0], trajectory[i][1],
                                    trajectory[i+1][0], trajectory[i+1][1])
                         for i in range(len(trajectory)-1))
    else:
        distance_km = 0
    
    # Direction
    if len(trajectory) >= 2:
        dx = trajectory[-1][0] - trajectory[0][0]
        dy = trajectory[-1][1] - trajectory[0][1]
        angle = math.atan2(dy, dx) * 180 / math.pi
        dirs = ['E', 'NE', 'N', 'NW', 'W', 'SW', 'S', 'SE']
        direction = dirs[int((angle + 202.5) / 45) % 8]
    else:
        direction = 'N'
    
    # Inside/outside park
    inside = sum(1 for f in cluster if point_in_polygon(f['longitude'], f['latitude'], park_geometry))
    pct_inside = 100 * inside / len(cluster) if cluster else 0
    
    # Total FRP
    total_frp = sum(f.get('frp', 0) or 0 for f in cluster)
    
    return {
        'fire_count': len(cluster),
        'start_date': start_date,
        'end_date': end_date,
        'days': days,
        'centroid': centroid,
        'trajectory': trajectory,
        'distance_km': round(distance_km, 2),
        'direction': direction,
        'pct_inside': round(pct_inside, 1),
        'total_frp': round(total_frp, 1),
        'first_point': trajectory[0][:2] if trajectory else centroid,
    }

def main():
    parser = argparse.ArgumentParser(description='Fire Analysis v3 - Memory Efficient')
    parser.add_argument('--park', help='Process single park')
    args = parser.parse_args()
    
    log("Loading parks...")
    parks = load_parks()
    log(f"Loaded {len(parks)} parks")
    
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    
    park_ids = [args.park] if args.park else sorted(parks.keys())
    total_groups = 0
    
    for i, park_id in enumerate(park_ids):
        if park_id not in parks:
            continue
        
        park = parks[park_id]
        fires = load_park_fires(park_id)
        
        if not fires:
            continue
        
        clusters = cluster_fires(fires)
        groups = []
        
        for cluster in clusters:
            group = analyze_group(cluster, park['geometry'])
            if group:
                groups.append(group)
        
        # Sort by start date descending
        groups.sort(key=lambda g: g['start_date'], reverse=True)
        
        # Save
        output_file = OUTPUT_DIR / f"{park_id}.json"
        with open(output_file, 'w') as f:
            json.dump(groups, f)
        
        total_groups += len(groups)
        log(f"[{i+1}/{len(park_ids)}] {park_id}: {len(fires):,} fires -> {len(groups)} groups")
    
    log(f"Done! Total: {total_groups} groups")

if __name__ == '__main__':
    main()
