#!/usr/bin/env python3
"""
Fire Trajectory Builder v5 - DBSCAN spatio-temporal clustering

Uses DBSCAN with combined space+time distance to find actual fire fronts
that spread over multiple days. Designed for transhumance fires burning
tall grass (3m+) that move 2-10 km/day.

Approach:
1. DBSCAN cluster fires using spatio-temporal distance
2. For each cluster, build trajectory from daily centroids
3. Filter to keep only multi-day moving groups
4. Clean zigzag artifacts

Output: data/fire_groups_v5/{park_id}.json
"""

import json
import math
import hashlib
import argparse
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from sklearn.cluster import DBSCAN

BASE_DIR = Path(__file__).parent.parent
KEYSTONES_FILE = BASE_DIR / "data" / "keystones_with_boundaries.json"
FIRE_DIR = BASE_DIR / "data" / "raw-fire-viirs-20200101-20260222"
OUTPUT_DIR = BASE_DIR / "data" / "fire_groups_v5"
TRENDS_DIR = BASE_DIR / "data" / "fire_trends_v5"

# DBSCAN parameters
SPATIAL_EPS_KM = 5.0  # Max spatial distance to be in same cluster
TEMPORAL_EPS_DAYS = 2  # Max temporal gap
MIN_FIRES = 10  # Minimum fires per cluster

# Trajectory parameters  
WINDOW_HOURS = 12
MIN_DAYS_FOR_TRAJECTORY = 2  # Need 2+ days to show movement
ZIGZAG_THRESHOLD = 90  # Degrees
MAX_ZIGZAG_RATIO = 0.3

MIN_DATE = '2020-01-01'

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def haversine(lon1, lat1, lon2, lat2):
    R = 6371
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def bearing(lon1, lat1, lon2, lat2):
    dlon = math.radians(lon2 - lon1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def bearing_diff(b1, b2):
    diff = abs(b1 - b2) % 360
    return min(diff, 360 - diff)

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
    # Filter: valid date, valid coordinates (lon=0.0 is invalid for African parks)
    valid_fires = []
    for f in fires:
        if f.get('acq_date', '') < MIN_DATE:
            continue
        lon = f.get('longitude', 0)
        lat = f.get('latitude', 0)
        # Skip fires with invalid coordinates (lon=0 or lat=0 are data errors)
        if lon == 0.0 or lat == 0.0:
            continue
        valid_fires.append(f)
    return valid_fires

def date_to_days(date_str, base_date):
    """Convert date string to days since base_date."""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    return (dt - base_date).days

def spatio_temporal_distance(f1, f2, base_date):
    """
    Combined spatio-temporal distance.
    Returns infinity if temporal gap > TEMPORAL_EPS_DAYS.
    """
    d1 = date_to_days(f1['acq_date'], base_date)
    d2 = date_to_days(f2['acq_date'], base_date)
    time_gap = abs(d1 - d2)
    
    if time_gap > TEMPORAL_EPS_DAYS:
        return 1e6  # Large value instead of inf
    
    spatial_dist = haversine(f1['longitude'], f1['latitude'], 
                             f2['longitude'], f2['latitude'])
    
    # Combined distance: spatial + scaled temporal
    # Scale: 1 day gap ~ SPATIAL_EPS_KM/2 spatial distance
    time_penalty = time_gap * (SPATIAL_EPS_KM / 2)
    
    return spatial_dist + time_penalty

def cluster_fires_dbscan(fires):
    """
    Use DBSCAN with precomputed spatio-temporal distance matrix.
    For large datasets, use chunked processing.
    """
    if not fires or len(fires) < MIN_FIRES:
        return []
    
    n = len(fires)
    
    # For very large datasets, process by time chunks
    if n > 50000:
        return cluster_fires_chunked(fires)
    
    # Find base date
    base_date = datetime.strptime(min(f['acq_date'] for f in fires), '%Y-%m-%d')
    
    # Build distance matrix (memory intensive but accurate)
    # Use approximate method for medium datasets
    if n > 10000:
        return cluster_fires_approximate(fires, base_date)
    
    # Full distance matrix for smaller datasets
    # Use large value instead of inf (DBSCAN doesn't handle inf)
    MAX_DIST = 1e6
    dist_matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(i+1, n):
            d = spatio_temporal_distance(fires[i], fires[j], base_date)
            d = min(d, MAX_DIST)  # Cap at MAX_DIST instead of inf
            dist_matrix[i, j] = d
            dist_matrix[j, i] = d
    
    # DBSCAN clustering
    db = DBSCAN(eps=SPATIAL_EPS_KM, min_samples=MIN_FIRES, metric='precomputed')
    labels = db.fit_predict(dist_matrix)
    
    # Group fires by cluster
    clusters = defaultdict(list)
    for i, label in enumerate(labels):
        if label >= 0:  # -1 is noise
            clusters[label].append(fires[i])
    
    return list(clusters.values())

def cluster_fires_approximate(fires, base_date):
    """
    Approximate clustering for medium-sized datasets.
    Uses spatial DBSCAN per time window, then merges overlapping clusters.
    """
    # Group fires by date
    by_date = defaultdict(list)
    for f in fires:
        by_date[f['acq_date']].append(f)
    
    dates = sorted(by_date.keys())
    all_clusters = []  # List of (date, cluster_fires, centroid)
    
    # Cluster each day separately
    for date in dates:
        day_fires = by_date[date]
        if len(day_fires) < 3:
            continue
            
        # Spatial coordinates
        coords = np.array([[f['longitude'], f['latitude']] for f in day_fires])
        
        # Convert to approximate km (at equator, 1 degree ~ 111km)
        coords_km = coords * 111
        
        db = DBSCAN(eps=SPATIAL_EPS_KM, min_samples=3)
        labels = db.fit_predict(coords_km)
        
        for label in set(labels):
            if label < 0:
                continue
            cluster_fires = [day_fires[i] for i, l in enumerate(labels) if l == label]
            cx = sum(f['longitude'] for f in cluster_fires) / len(cluster_fires)
            cy = sum(f['latitude'] for f in cluster_fires) / len(cluster_fires)
            all_clusters.append((date, cluster_fires, (cx, cy)))
    
    # Link clusters across days based on centroid proximity
    n = len(all_clusters)
    parent = list(range(n))
    
    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
    
    for i in range(n):
        date_i = datetime.strptime(all_clusters[i][0], '%Y-%m-%d')
        cx_i, cy_i = all_clusters[i][2]
        
        for j in range(i+1, n):
            date_j = datetime.strptime(all_clusters[j][0], '%Y-%m-%d')
            gap = abs((date_j - date_i).days)
            
            if gap > TEMPORAL_EPS_DAYS:
                continue
            
            cx_j, cy_j = all_clusters[j][2]
            dist = haversine(cx_i, cy_i, cx_j, cy_j)
            
            # Allow slightly larger linking distance for multi-day spread
            max_link = SPATIAL_EPS_KM + gap * 3  # 3km/day spread allowance
            
            if dist <= max_link:
                union(i, j)
    
    # Merge linked clusters
    merged = defaultdict(list)
    for i in range(n):
        root = find(i)
        merged[root].extend(all_clusters[i][1])
    
    # Filter by minimum size
    return [fires for fires in merged.values() if len(fires) >= MIN_FIRES]

def cluster_fires_chunked(fires):
    """
    Process very large datasets in time chunks.
    """
    # Sort by date
    fires = sorted(fires, key=lambda f: f['acq_date'])
    
    # Find date range
    dates = sorted(set(f['acq_date'] for f in fires))
    
    # Process in 30-day chunks with 5-day overlap
    chunk_days = 30
    overlap_days = 5
    
    all_clusters = []
    i = 0
    
    while i < len(dates):
        chunk_start = datetime.strptime(dates[i], '%Y-%m-%d')
        chunk_end = chunk_start + timedelta(days=chunk_days)
        chunk_end_str = chunk_end.strftime('%Y-%m-%d')
        
        # Get fires in this chunk
        chunk_fires = [f for f in fires 
                       if dates[i] <= f['acq_date'] <= chunk_end_str]
        
        if chunk_fires:
            base_date = chunk_start
            chunk_clusters = cluster_fires_approximate(chunk_fires, base_date)
            all_clusters.extend(chunk_clusters)
        
        # Move to next chunk (with overlap handling via deduplication later)
        i += chunk_days - overlap_days
        while i < len(dates) and dates[i] < chunk_end_str:
            i += 1
    
    # Deduplicate clusters that span chunk boundaries
    # (Simple approach: keep all, duplicates will have similar centroids)
    return all_clusters

def get_window_key(date_str, time_str):
    try:
        hour = int(time_str[:2]) if time_str else 0
    except:
        hour = 0
    return (date_str, hour // WINDOW_HOURS)

def build_trajectory(fires):
    """Build trajectory from daily fire front positions."""
    if not fires:
        return []
    
    # Group by date
    by_date = defaultdict(list)
    for f in fires:
        by_date[f['acq_date']].append(f)
    
    dates = sorted(by_date.keys())
    
    if len(dates) < MIN_DAYS_FOR_TRAJECTORY:
        # Single day - return centroid
        lon = sum(f['longitude'] for f in fires) / len(fires)
        lat = sum(f['latitude'] for f in fires) / len(fires)
        return [[lon, lat, dates[0], '1200']]
    
    # Calculate overall direction from first to last day
    first_fires = by_date[dates[0]]
    last_fires = by_date[dates[-1]]
    
    first_lon = sum(f['longitude'] for f in first_fires) / len(first_fires)
    first_lat = sum(f['latitude'] for f in first_fires) / len(first_fires)
    last_lon = sum(f['longitude'] for f in last_fires) / len(last_fires)
    last_lat = sum(f['latitude'] for f in last_fires) / len(last_fires)
    
    overall_bearing = bearing(first_lon, first_lat, last_lon, last_lat)
    
    # Build trajectory using fire front (furthest point in direction of movement)
    trajectory = []
    bearing_rad = math.radians(overall_bearing)
    dx = math.sin(bearing_rad)
    dy = math.cos(bearing_rad)
    
    for date in dates:
        day_fires = by_date[date]
        
        # Find centroid
        cx = sum(f['longitude'] for f in day_fires) / len(day_fires)
        cy = sum(f['latitude'] for f in day_fires) / len(day_fires)
        
        # Find fire front (furthest in direction of movement)
        best_fire = max(day_fires, 
                       key=lambda f: (f['longitude']-cx)*dx + (f['latitude']-cy)*dy)
        
        trajectory.append([best_fire['longitude'], best_fire['latitude'], date, '1200'])
    
    return trajectory

def is_trajectory_clean(trajectory):
    """Check trajectory quality."""
    if len(trajectory) < 2:
        return True, 0, 0
    
    bearings = []
    for i in range(1, len(trajectory)):
        b = bearing(trajectory[i-1][0], trajectory[i-1][1],
                   trajectory[i][0], trajectory[i][1])
        bearings.append(b)
    
    if len(bearings) < 2:
        return True, 0, 0
    
    changes = [bearing_diff(bearings[i], bearings[i-1]) 
               for i in range(1, len(bearings))]
    
    zigzags = sum(1 for c in changes if c > ZIGZAG_THRESHOLD)
    zigzag_ratio = zigzags / len(changes) if changes else 0
    avg_change = sum(changes) / len(changes) if changes else 0
    
    return zigzag_ratio <= MAX_ZIGZAG_RATIO, zigzag_ratio, avg_change

def clean_zigzag_trajectory(trajectory):
    """
    Remove zigzag sections from trajectory.
    Keeps only the longest consistent segment.
    If too fragmented, returns just the centroid as single point.
    """
    if len(trajectory) < 3:
        return trajectory, 'clean'
    
    # Calculate bearings between consecutive points
    bearings = []
    for i in range(1, len(trajectory)):
        b = bearing(trajectory[i-1][0], trajectory[i-1][1],
                   trajectory[i][0], trajectory[i][1])
        bearings.append(b)
    
    # Find consistent segments (direction change < ZIGZAG_THRESHOLD)
    segments = []  # List of (start_idx, end_idx) for trajectory points
    seg_start = 0
    
    for i in range(1, len(bearings)):
        diff = bearing_diff(bearings[i], bearings[i-1])
        if diff > ZIGZAG_THRESHOLD:
            # End current segment
            if i - seg_start >= 1:  # At least 2 points (1 bearing)
                segments.append((seg_start, i))  # i is the last point of segment
            seg_start = i
    
    # Add final segment
    if len(bearings) - seg_start >= 1:
        segments.append((seg_start, len(bearings)))
    
    if not segments:
        # All zigzag - return centroid
        lons = [p[0] for p in trajectory]
        lats = [p[1] for p in trajectory]
        return [[sum(lons)/len(lons), sum(lats)/len(lats), 
                 trajectory[0][2], trajectory[0][3]]], 'cluster'
    
    # Find longest segment
    longest = max(segments, key=lambda s: s[1] - s[0])
    start_idx, end_idx = longest
    
    # Return trajectory for longest segment (end_idx+1 to include last point)
    cleaned = trajectory[start_idx:end_idx + 1]
    
    if len(cleaned) < 2:
        lons = [p[0] for p in trajectory]
        lats = [p[1] for p in trajectory]
        return [[sum(lons)/len(lons), sum(lats)/len(lats), 
                 trajectory[0][2], trajectory[0][3]]], 'cluster'
    
    return cleaned, 'cleaned'

def classify_group(days, distance_km, pct_inside, speed, fire_count):
    """Classify fire group type."""
    if fire_count < 20 and days <= 2:
        return 'spot_fire'
    elif pct_inside > 80 and speed < 2 and days <= 3:
        return 'management_controlled'
    elif days >= 5 and distance_km > 20 and speed > 2:
        return 'transhumance'
    elif days >= 3 and distance_km > 10:
        return 'spreading_fire'
    elif pct_inside < 20:
        return 'external_fire'
    else:
        return 'local_fire'

def analyze_group(cluster, park_id, park_geometry):
    """Analyze a fire cluster and build trajectory."""
    if not cluster or len(cluster) < MIN_FIRES:
        return None
    
    sorted_fires = sorted(cluster, key=lambda f: f['acq_date'])
    start_date = sorted_fires[0]['acq_date']
    end_date = sorted_fires[-1]['acq_date']
    days = (datetime.strptime(end_date, '%Y-%m-%d') - 
            datetime.strptime(start_date, '%Y-%m-%d')).days + 1
    
    # Centroid
    lons = [f['longitude'] for f in cluster]
    lats = [f['latitude'] for f in cluster]
    centroid = [sum(lons)/len(lons), sum(lats)/len(lats)]
    
    # Build trajectory
    raw_trajectory = build_trajectory(sorted_fires)
    
    # Check quality and clean if needed
    is_clean, zigzag_ratio, avg_change = is_trajectory_clean(raw_trajectory)
    
    if is_clean:
        trajectory = raw_trajectory
        trajectory_type = 'clean'
    else:
        trajectory, trajectory_type = clean_zigzag_trajectory(raw_trajectory)
    
    # Calculate distance from cleaned trajectory
    distance_km = sum(haversine(trajectory[i][0], trajectory[i][1],
                                trajectory[i+1][0], trajectory[i+1][1])
                     for i in range(len(trajectory)-1)) if len(trajectory) >= 2 else 0
    
    # Direction
    if len(trajectory) >= 2:
        overall_bearing = bearing(trajectory[0][0], trajectory[0][1],
                                  trajectory[-1][0], trajectory[-1][1])
        dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
        direction = dirs[int((overall_bearing + 22.5) / 45) % 8]
    else:
        direction = 'N'
    
    # Inside park percentage
    inside = sum(1 for f in cluster 
                 if point_in_polygon(f['longitude'], f['latitude'], park_geometry))
    pct_inside = 100 * inside / len(cluster)
    
    # Total FRP
    total_frp = sum(f.get('frp', 0) or 0 for f in cluster)
    
    # Speed and classification
    speed = distance_km / max(days, 1)
    group_type = classify_group(days, distance_km, pct_inside, speed, len(cluster))
    
    # Generate IDs
    first_point = trajectory[0] if trajectory else centroid
    hash_input = f"{park_id}_{start_date}_{first_point[0]:.4f}_{first_point[1]:.4f}"
    group_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
    group_id = f"{park_id}_{start_date}_{group_hash}"
    year = int(start_date[:4])
    feature_id = f"{park_id}_{year}_grp_{group_hash}"
    
    return {
        'group_id': group_id,
        'feature_id': feature_id,
        'fire_count': len(cluster),
        'start_date': start_date,
        'end_date': end_date,
        'days': days,
        'year': year,
        'centroid': centroid,
        'trajectory': trajectory,
        'distance_km': round(distance_km, 2),
        'speed_km_day': round(speed, 2),
        'direction': direction,
        'group_type': group_type,
        'pct_inside': round(pct_inside, 1),
        'total_frp': round(total_frp, 1),
        'primary_park': park_id,
        'affected_parks': [park_id],
        'cross_border': False,
        'first_point': first_point[:2] if isinstance(first_point, list) else first_point,
        'trajectory_type': trajectory_type,
        'zigzag_ratio': round(zigzag_ratio, 2),
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--park', help='Single park')
    args = parser.parse_args()
    
    log("Fire Trajectory Builder v5 (DBSCAN spatio-temporal)")
    log("Loading parks...")
    parks = load_parks()
    log(f"Loaded {len(parks)} parks")
    log(f"Params: {SPATIAL_EPS_KM}km spatial eps, {TEMPORAL_EPS_DAYS}d temporal eps")
    log(f"        {MIN_FIRES} min fires, {MIN_DAYS_FOR_TRAJECTORY}+ days for trajectory")
    log(f"        Zigzag threshold: {ZIGZAG_THRESHOLD}°, max ratio: {MAX_ZIGZAG_RATIO}")
    
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    TRENDS_DIR.mkdir(exist_ok=True, parents=True)
    
    park_ids = [args.park] if args.park else sorted(parks.keys())
    total_groups = 0
    total_fires_processed = 0
    stats = {'clean': 0, 'cleaned': 0, 'cluster': 0}
    
    # Trend tracking
    daily_counts = defaultdict(lambda: defaultdict(lambda: {'groups': 0, 'fires': 0}))
    monthly_counts = defaultdict(lambda: defaultdict(lambda: {'groups': 0, 'fires': 0}))
    
    for i, park_id in enumerate(park_ids):
        if park_id not in parks:
            continue
        
        fires = load_park_fires(park_id)
        if not fires:
            continue
        
        clusters = cluster_fires_dbscan(fires)
        groups = [g for g in (analyze_group(c, park_id, parks[park_id]['geometry']) 
                              for c in clusters) if g]
        groups.sort(key=lambda g: g['start_date'], reverse=True)
        
        # Stats
        park_stats = {'clean': 0, 'cleaned': 0, 'cluster': 0}
        for g in groups:
            ttype = g.get('trajectory_type', 'cluster')
            stats[ttype] += 1
            park_stats[ttype] += 1
            
            # Track trends
            daily_counts[park_id][g['start_date']]['groups'] += 1
            daily_counts[park_id][g['start_date']]['fires'] += g['fire_count']
            monthly_counts[park_id][g['start_date'][:7]]['groups'] += 1
            monthly_counts[park_id][g['start_date'][:7]]['fires'] += g['fire_count']
        
        multi_day = len([g for g in groups if g['days'] >= 2])
        avg_fires = sum(g['fire_count'] for g in groups) / len(groups) if groups else 0
        
        with open(OUTPUT_DIR / f"{park_id}.json", 'w') as f:
            json.dump(groups, f)
        
        total_groups += len(groups)
        total_fires_processed += len(fires)
        
        log(f"[{i+1}/{len(park_ids)}] {park_id}: {len(fires):,} fires -> {len(groups)} groups "
            f"(avg {avg_fires:.0f}/grp, {multi_day} multi-day, {park_stats['clean']} clean, {park_stats['cleaned']} cleaned)")
    
    # Write trend summaries
    log("Writing trend stats...")
    trends = {'daily': {k: dict(v) for k, v in daily_counts.items()}, 
              'monthly': {k: dict(v) for k, v in monthly_counts.items()}}
    with open(TRENDS_DIR / "park_fire_trends.json", 'w') as f:
        json.dump(trends, f)
    
    # Summary stats
    summary = {
        'total_groups': total_groups,
        'total_fires': total_fires_processed,
        'total_parks': len([p for p in park_ids if p in parks]),
        'date_range': {'start': MIN_DATE, 'end': datetime.now().strftime('%Y-%m-%d')},
        'params': {
            'spatial_eps_km': SPATIAL_EPS_KM,
            'temporal_eps_days': TEMPORAL_EPS_DAYS,
            'min_fires': MIN_FIRES,
            'min_days_trajectory': MIN_DAYS_FOR_TRAJECTORY,
            'zigzag_threshold': ZIGZAG_THRESHOLD,
            'max_zigzag_ratio': MAX_ZIGZAG_RATIO
        },
        'trajectory_types': dict(stats)
    }
    with open(TRENDS_DIR / "fire_trends_summary.json", 'w') as f:
        json.dump(summary, f, indent=2)
    
    log(f"\nDone! {total_fires_processed:,} fires -> {total_groups} groups")
    log(f"Average: {total_fires_processed/total_groups:.0f} fires/group" if total_groups else "")
    log(f"Trajectory types: {stats['clean']} clean, {stats['cleaned']} cleaned, {stats['cluster']} clusters")

if __name__ == '__main__':
    main()
