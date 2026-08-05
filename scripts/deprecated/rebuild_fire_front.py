#!/usr/bin/env python3
"""
Fire Front Trajectory Builder

Instead of centroid paths, tracks the LEADING EDGE of fire movement.
For each time window, finds the point furthest in the direction of overall movement.

Output: data/fire_groups_front/{park_id}.json
"""

import json
import math
import hashlib
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
KEYSTONES_FILE = BASE_DIR / "data" / "keystones_with_boundaries.json"
FIRE_DIR = BASE_DIR / "data" / "raw-fire-viirs-20200101-20260222"
OUTPUT_DIR = BASE_DIR / "data" / "fire_groups_front"

# Tighter parameters for better clustering
WINDOW_HOURS = 12
CLUSTER_KM = 3.0  # Reduced from 5km
BASE_LINK_KM = 5.0  # Reduced from 10km
LINK_KM_PER_DAY = 3.0  # Reduced from 5km
MAX_GAP_DAYS = 2  # Reduced from 3
MIN_DATE = '2020-01-01'
MAX_DIRECTION_CHANGE = 60  # Max degrees change in direction to link

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def haversine(lon1, lat1, lon2, lat2):
    R = 6371
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def bearing(lon1, lat1, lon2, lat2):
    """Calculate bearing in degrees from point 1 to point 2"""
    dlon = math.radians(lon2 - lon1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def bearing_diff(b1, b2):
    """Smallest angle between two bearings"""
    diff = abs(b1 - b2) % 360
    return min(diff, 360 - diff)

def get_window_key(date_str, time_str):
    try:
        hour = int(time_str[:2]) if time_str else 0
    except:
        hour = 0
    window = hour // WINDOW_HOURS
    return (date_str, window)

def point_in_polygon(lon, lat, geometry):
    coords = geometry.get('coordinates', [])
    if geometry['type'] == 'MultiPolygon':
        for poly in coords:
            if _point_in_ring(lon, lat, poly[0]):
                return True
        return False
    elif geometry['type'] == 'Polygon':
        return _point_in_ring(lon, lat, coords[0])
    return False

def _point_in_ring(lon, lat, ring):
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

def load_parks():
    with open(KEYSTONES_FILE) as f:
        data = json.load(f)
    parks = {}
    for p in data:
        park_id = p.get('id')
        if not park_id or 'geometry' not in p:
            continue
        parks[park_id] = {'id': park_id, 'name': p.get('name', park_id),
                          'country': p.get('country', ''), 'geometry': p['geometry']}
    return parks

def load_park_fires(park_id):
    fire_file = FIRE_DIR / f"{park_id}.json"
    if not fire_file.exists():
        return []
    with open(fire_file) as f:
        data = json.load(f)
    fires = data.get('fires', data) if isinstance(data, dict) else data
    return [f for f in fires if f.get('acq_date', '') >= MIN_DATE]

def cluster_fires_windowed(fires):
    """Cluster fires with tighter parameters and direction consistency."""
    if not fires:
        return []
    
    fires_by_date = defaultdict(list)
    for f in fires:
        fires_by_date[f['acq_date']].append(f)
    
    dates = sorted(fires_by_date.keys())
    all_clusters = []
    
    for date in dates:
        day_fires = fires_by_date[date]
        # Simple spatial clustering within day
        day_clusters = []
        used = set()
        
        for i, f in enumerate(day_fires):
            if i in used:
                continue
            cluster = [f]
            used.add(i)
            
            for j, f2 in enumerate(day_fires):
                if j in used:
                    continue
                for cf in cluster:
                    if haversine(f2['longitude'], f2['latitude'], cf['longitude'], cf['latitude']) < CLUSTER_KM:
                        cluster.append(f2)
                        used.add(j)
                        break
            
            day_clusters.append({'date': date, 'fires': cluster})
        
        all_clusters.extend(day_clusters)
    
    # Link clusters across days with direction consistency
    parent = list(range(len(all_clusters)))
    cluster_direction = [None] * len(all_clusters)  # Track dominant direction
    
    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    
    def union(x, y, direction=None):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
            if direction is not None:
                cluster_direction[py] = direction
    
    # Sort clusters by date
    all_clusters.sort(key=lambda c: c['date'])
    
    for i, c1 in enumerate(all_clusters):
        c1_fires = c1['fires']
        c1_lon = sum(f['longitude'] for f in c1_fires) / len(c1_fires)
        c1_lat = sum(f['latitude'] for f in c1_fires) / len(c1_fires)
        c1_date = datetime.strptime(c1['date'], '%Y-%m-%d')
        
        for j in range(i+1, len(all_clusters)):
            c2 = all_clusters[j]
            c2_date = datetime.strptime(c2['date'], '%Y-%m-%d')
            gap = (c2_date - c1_date).days
            
            if gap > MAX_GAP_DAYS:
                break
            if gap == 0:
                continue
            
            c2_fires = c2['fires']
            c2_lon = sum(f['longitude'] for f in c2_fires) / len(c2_fires)
            c2_lat = sum(f['latitude'] for f in c2_fires) / len(c2_fires)
            
            dist = haversine(c1_lon, c1_lat, c2_lon, c2_lat)
            max_link = BASE_LINK_KM + LINK_KM_PER_DAY * gap
            
            if dist <= max_link:
                # Check direction consistency
                new_bearing = bearing(c1_lon, c1_lat, c2_lon, c2_lat)
                root_dir = cluster_direction[find(i)]
                
                if root_dir is None or bearing_diff(root_dir, new_bearing) < MAX_DIRECTION_CHANGE:
                    union(i, j, new_bearing)
    
    # Group by root
    groups = defaultdict(list)
    for i, c in enumerate(all_clusters):
        groups[find(i)].extend(c['fires'])
    
    return list(groups.values())

def get_fire_front(fires, overall_bearing):
    """Find the fire furthest in the direction of movement."""
    if not fires:
        return None
    if len(fires) == 1:
        f = fires[0]
        return (f['longitude'], f['latitude'])
    
    # Calculate centroid as reference
    cx = sum(f['longitude'] for f in fires) / len(fires)
    cy = sum(f['latitude'] for f in fires) / len(fires)
    
    # Find fire furthest in the direction of overall_bearing
    best_fire = None
    best_score = -float('inf')
    
    bearing_rad = math.radians(overall_bearing)
    dx = math.sin(bearing_rad)  # East component
    dy = math.cos(bearing_rad)  # North component
    
    for f in fires:
        # Project fire position onto bearing direction
        fx = f['longitude'] - cx
        fy = f['latitude'] - cy
        score = fx * dx + fy * dy  # Dot product
        
        if score > best_score:
            best_score = score
            best_fire = f
    
    return (best_fire['longitude'], best_fire['latitude']) if best_fire else (cx, cy)

def build_fire_front_trajectory(fires):
    """Build trajectory tracking the leading edge of fire movement."""
    if not fires:
        return []
    
    fires_by_window = defaultdict(list)
    for f in fires:
        key = get_window_key(f['acq_date'], f.get('acq_time', ''))
        fires_by_window[key].append(f)
    
    sorted_windows = sorted(fires_by_window.keys())
    
    if len(sorted_windows) < 2:
        # Single window - just use centroid
        wf = fires_by_window[sorted_windows[0]]
        lon = sum(f['longitude'] for f in wf) / len(wf)
        lat = sum(f['latitude'] for f in wf) / len(wf)
        date, window = sorted_windows[0]
        return [[lon, lat, date, f"{window * WINDOW_HOURS + WINDOW_HOURS // 2:02d}00"]]
    
    # Calculate overall direction from first to last window centroids
    first_fires = fires_by_window[sorted_windows[0]]
    last_fires = fires_by_window[sorted_windows[-1]]
    
    first_lon = sum(f['longitude'] for f in first_fires) / len(first_fires)
    first_lat = sum(f['latitude'] for f in first_fires) / len(first_fires)
    last_lon = sum(f['longitude'] for f in last_fires) / len(last_fires)
    last_lat = sum(f['latitude'] for f in last_fires) / len(last_fires)
    
    overall_bearing = bearing(first_lon, first_lat, last_lon, last_lat)
    
    # Build trajectory using fire front for each window
    trajectory = []
    for key in sorted_windows:
        wf = fires_by_window[key]
        front = get_fire_front(wf, overall_bearing)
        date, window = key
        mid_hour = window * WINDOW_HOURS + WINDOW_HOURS // 2
        trajectory.append([front[0], front[1], date, f"{mid_hour:02d}00"])
    
    return trajectory

def classify_group(days, distance_km, pct_inside, speed):
    if pct_inside > 80 and speed < 2:
        return 'management_controlled'
    elif pct_inside > 50 and days <= 3:
        return 'herder_local'
    elif days >= 5 and distance_km > 20:
        return 'transhumance'
    elif pct_inside < 20:
        return 'external_fire'
    elif days == 1 and distance_km < 5:
        return 'spot_fire'
    else:
        return 'herder_local'

def analyze_group(cluster, park_id, park_geometry):
    if not cluster:
        return None
    
    sorted_fires = sorted(cluster, key=lambda f: (f['acq_date'], f.get('acq_time', '')))
    start_date, end_date = sorted_fires[0]['acq_date'], sorted_fires[-1]['acq_date']
    days = (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days + 1
    
    lats = [f['latitude'] for f in cluster]
    lons = [f['longitude'] for f in cluster]
    centroid = [sum(lons)/len(lons), sum(lats)/len(lats)]
    
    trajectory = build_fire_front_trajectory(sorted_fires)
    
    distance_km = sum(haversine(trajectory[i][0], trajectory[i][1],
                                trajectory[i+1][0], trajectory[i+1][1])
                     for i in range(len(trajectory)-1)) if len(trajectory) >= 2 else 0
    
    if len(trajectory) >= 2:
        dx, dy = trajectory[-1][0] - trajectory[0][0], trajectory[-1][1] - trajectory[0][1]
        angle = math.atan2(dy, dx) * 180 / math.pi
        direction = ['E', 'NE', 'N', 'NW', 'W', 'SW', 'S', 'SE'][int((angle + 202.5) / 45) % 8]
    else:
        direction = 'N'
    
    inside = sum(1 for f in cluster if point_in_polygon(f['longitude'], f['latitude'], park_geometry))
    pct_inside = 100 * inside / len(cluster)
    total_frp = sum(f.get('frp', 0) or 0 for f in cluster)
    
    affected_parks = [park_id]
    cross_border = False
    
    speed = distance_km / max(days, 1)
    group_type = classify_group(days, distance_km, pct_inside, speed)
    
    first_point = trajectory[0] if trajectory else centroid
    hash_input = f"{park_id}_{start_date}_{first_point[0]:.4f}_{first_point[1]:.4f}"
    group_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
    group_id = f"{park_id}_{start_date}_{group_hash}"
    year = int(start_date[:4])
    feature_id = f"{park_id}_{year}_grp_{group_hash}"
    
    return {
        'group_id': group_id,
        'feature_id': feature_id,
        'fire_count': len(cluster), 'start_date': start_date, 'end_date': end_date,
        'days': days, 'year': year, 'centroid': centroid, 'trajectory': trajectory,
        'distance_km': round(distance_km, 2), 'speed_km_day': round(speed, 2),
        'direction': direction, 'group_type': group_type,
        'pct_inside': round(pct_inside, 1), 'total_frp': round(total_frp, 1),
        'primary_park': park_id, 'affected_parks': affected_parks,
        'cross_border': cross_border, 'first_point': first_point[:2] if isinstance(first_point, list) else first_point,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--park', help='Single park')
    args = parser.parse_args()
    
    log("Fire Front Trajectory Builder")
    log("Loading parks...")
    parks = load_parks()
    log(f"Loaded {len(parks)} parks")
    log(f"Params: {CLUSTER_KM}km cluster, {BASE_LINK_KM}+{LINK_KM_PER_DAY}km/day link, {MAX_GAP_DAYS}d gap, {MAX_DIRECTION_CHANGE}° max turn")
    
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    
    park_ids = [args.park] if args.park else sorted(parks.keys())
    total_groups = 0
    
    for i, park_id in enumerate(park_ids):
        if park_id not in parks:
            continue
        
        fires = load_park_fires(park_id)
        if not fires:
            continue
        
        clusters = cluster_fires_windowed(fires)
        groups = [g for g in (analyze_group(c, park_id, parks[park_id]['geometry']) for c in clusters) if g]
        groups.sort(key=lambda g: g['start_date'], reverse=True)
        
        with open(OUTPUT_DIR / f"{park_id}.json", 'w') as f:
            json.dump(groups, f)
        
        total_groups += len(groups)
        if (i + 1) % 20 == 0 or args.park:
            log(f"[{i+1}/{len(park_ids)}] {park_id}: {len(fires):,} fires -> {len(groups)} groups")
    
    log(f"Done! Total: {total_groups} groups")

if __name__ == '__main__':
    main()
