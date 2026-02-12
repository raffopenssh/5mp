#!/usr/bin/env python3
"""
Rebuild park_fire_analysis from fire_detections database.

Analyzes fire trajectories for all parks and all years (2018-2026).
Classifies into: transhumance, herder_local, herder_fast, management_*, village, etc.
"""

import json
import sqlite3
import math
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_FILE = BASE_DIR / "data/keystones_with_boundaries.json"

sys.stdout.reconfigure(line_buffering=True)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(min(1, a)))

def bearing(lat1, lon1, lat2, lon2):
    dlon = math.radians(lon2 - lon1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def detect_daily_clusters(fires_by_date, cluster_dist_km=5):
    """Group fires into spatial clusters per day."""
    daily_clusters = {}
    
    for date, fires in fires_by_date.items():
        clusters = []
        used = set()
        
        for i, fire in enumerate(fires):
            if i in used:
                continue
            
            cluster = [fire]
            used.add(i)
            
            for j, other in enumerate(fires):
                if j in used:
                    continue
                
                # Check if within cluster distance of any point in cluster
                for cf in cluster:
                    if haversine(fire['lat'], fire['lon'], other['lat'], other['lon']) < cluster_dist_km:
                        cluster.append(other)
                        used.add(j)
                        break
            
            if cluster:
                # Compute centroid
                clat = sum(f['lat'] for f in cluster) / len(cluster)
                clon = sum(f['lon'] for f in cluster) / len(cluster)
                
                # Compute spread (max dist from centroid)
                spread = max(haversine(clat, clon, f['lat'], f['lon']) for f in cluster) if len(cluster) > 1 else 0
                
                clusters.append({
                    'date': date,
                    'lat': clat,
                    'lon': clon,
                    'fires': len(cluster),
                    'spread_km': spread
                })
        
        if clusters:
            daily_clusters[date] = clusters
    
    return daily_clusters

def track_clusters(daily_clusters, max_link_km=25, max_gap_days=3):
    """Link clusters across days into trajectories."""
    sorted_dates = sorted(daily_clusters.keys())
    if not sorted_dates:
        return []
    
    used = set()
    trajectories = []
    
    for start_idx, start_date in enumerate(sorted_dates):
        for cluster in daily_clusters[start_date]:
            key = f"{start_date}_{cluster['lat']:.4f}_{cluster['lon']:.4f}"
            if key in used:
                continue
            
            traj = [cluster]
            used.add(key)
            current = cluster
            
            for next_idx in range(start_idx + 1, len(sorted_dates)):
                next_date = sorted_dates[next_idx]
                
                # Check date gap
                d1 = datetime.strptime(current['date'], '%Y-%m-%d')
                d2 = datetime.strptime(next_date, '%Y-%m-%d')
                gap_days = (d2 - d1).days
                
                if gap_days > max_gap_days:
                    break
                
                # Find closest cluster
                best = None
                best_dist = max_link_km + 1
                
                for nc in daily_clusters[next_date]:
                    nkey = f"{next_date}_{nc['lat']:.4f}_{nc['lon']:.4f}"
                    if nkey in used:
                        continue
                    
                    dist = haversine(current['lat'], current['lon'], nc['lat'], nc['lon'])
                    if dist <= max_link_km and dist < best_dist:
                        best = nc
                        best_dist = dist
                
                if best:
                    traj.append(best)
                    used.add(f"{best['date']}_{best['lat']:.4f}_{best['lon']:.4f}")
                    current = best
            
            if len(traj) >= 3:  # Minimum trajectory length
                trajectories.append(traj)
    
    return trajectories

def classify_trajectory(traj):
    """Classify trajectory based on movement patterns."""
    start, end = traj[0], traj[-1]
    
    # Metrics
    total_fires = sum(c['fires'] for c in traj)
    days = len(traj)
    net_south = (start['lat'] - end['lat']) * 111  # km southward
    net_east = (end['lon'] - start['lon']) * 111
    total_dist = haversine(start['lat'], start['lon'], end['lat'], end['lon'])
    
    # Speed
    movements = []
    for i in range(1, len(traj)):
        d = haversine(traj[i-1]['lat'], traj[i-1]['lon'], traj[i]['lat'], traj[i]['lon'])
        movements.append(d)
    
    avg_speed = sum(movements) / len(movements) if movements else 0
    max_speed = max(movements) if movements else 0
    avg_spread = sum(c['spread_km'] for c in traj) / len(traj)
    
    # Classification
    if avg_speed > 30:
        group_type = 'management_fast'
    elif avg_speed > 15:
        if avg_spread > 30:
            group_type = 'management_vehicle'
        else:
            group_type = 'herder_fast'
    elif avg_speed > 5:
        if net_south > 20:
            group_type = 'transhumance'
        else:
            group_type = 'herder_local'
    elif avg_speed > 2:
        if days > 10 and net_south > 15:
            group_type = 'transhumance_slow'
        else:
            group_type = 'local_burning'
    else:
        if days > 7:
            group_type = 'village_persistent'
        else:
            group_type = 'local_stationary'
    
    # Include full trajectory with timestamps
    trajectory_points = [
        {
            'date': pt['date'],
            'lat': round(pt['lat'], 5),
            'lon': round(pt['lon'], 5),
            'fires': pt['fires']
        }
        for pt in traj
    ]
    
    metrics = {
        'days': days,
        'fires': total_fires,
        'net_south_km': round(net_south, 1),
        'net_east_km': round(net_east, 1),
        'total_distance_km': round(total_dist, 1),
        'avg_speed_km_day': round(avg_speed, 1),
        'max_speed_km_day': round(max_speed, 1),
        'avg_spread_km': round(avg_spread, 1),
        'start_date': start['date'],
        'end_date': end['date'],
        'start_lat': round(start['lat'], 3),
        'start_lon': round(start['lon'], 3),
        'end_lat': round(end['lat'], 3),
        'end_lon': round(end['lon'], 3),
        'trajectory': trajectory_points  # Full daily trajectory with timestamps
    }
    
    return group_type, metrics

def load_parks():
    """Load park boundaries."""
    with open(KEYSTONES_FILE) as f:
        keystones = json.load(f)
    
    parks = []
    for k in keystones:
        if k.get('geometry'):
            try:
                from shapely.geometry import shape
                geom = shape(k['geometry'])
                bounds = geom.bounds  # (minx, miny, maxx, maxy)
                center = geom.centroid
                parks.append({
                    'id': k['id'],
                    'name': k['name'],
                    'lat': center.y,
                    'lon': center.x,
                    'bounds': bounds
                })
            except:
                pass
    return parks

def analyze_park_year(conn, park, year, buffer_km=50):
    """Analyze fire data for a park in a specific year."""
    bounds = park['bounds']
    buffer_deg = buffer_km / 111.0
    
    # Query fires in park buffer area
    cursor = conn.execute("""
        SELECT latitude, longitude, acq_date, brightness, confidence
        FROM fire_detections
        WHERE latitude BETWEEN ? AND ?
        AND longitude BETWEEN ? AND ?
        AND acq_date LIKE ?
        ORDER BY acq_date
    """, (
        bounds[1] - buffer_deg, bounds[3] + buffer_deg,
        bounds[0] - buffer_deg, bounds[2] + buffer_deg,
        f"{year}-%"
    ))
    
    # Group fires by date
    fires_by_date = defaultdict(list)
    for lat, lon, date, brightness, confidence in cursor:
        fires_by_date[date].append({
            'lat': lat, 'lon': lon, 'date': date,
            'brightness': brightness, 'confidence': confidence
        })
    
    if len(fires_by_date) < 5:
        return None
    
    total_fires = sum(len(f) for f in fires_by_date.values())
    
    # Detect clusters and trajectories
    daily_clusters = detect_daily_clusters(fires_by_date)
    trajectories = track_clusters(daily_clusters)
    
    # Classify trajectories
    groups = defaultdict(list)
    for traj in trajectories:
        group_type, metrics = classify_trajectory(traj)
        groups[group_type].append(metrics)
    
    # Dry season fires (hemisphere dependent)
    if park['lat'] > 0:  # Northern hemisphere
        dry_months = {1, 2, 3, 11, 12}
    else:  # Southern hemisphere
        dry_months = {5, 6, 7, 8, 9, 10}
    
    dry_fires = sum(
        len(fires) for date, fires in fires_by_date.items()
        if int(date[5:7]) in dry_months
    )
    
    # Summary stats
    trans = groups.get('transhumance', []) + groups.get('transhumance_slow', [])
    herder = groups.get('herder_local', []) + groups.get('herder_fast', [])
    mgmt = groups.get('management_fast', []) + groups.get('management_vehicle', [])
    village = groups.get('village_persistent', []) + groups.get('local_stationary', [])
    
    # Peak month
    monthly = defaultdict(int)
    for date, fires in fires_by_date.items():
        monthly[int(date[5:7])] += len(fires)
    peak_month = max(monthly, key=monthly.get) if monthly else None
    
    return {
        'total_fires': total_fires,
        'dry_season_fires': dry_fires,
        'transhumance_groups': len(trans),
        'transhumance_fires': sum(g['fires'] for g in trans),
        'avg_transhumance_speed': sum(g['avg_speed_km_day'] for g in trans) / len(trans) if trans else 0,
        'herder_groups': len(herder),
        'management_groups': len(mgmt),
        'village_groups': len(village),
        'peak_month': peak_month,
        'groups': dict(groups)
    }

def main():
    print("Rebuild Park Fire Analysis")
    print("=" * 50)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    
    # Create table if needed
    conn.execute("""
        CREATE TABLE IF NOT EXISTS park_fire_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            year INTEGER NOT NULL,
            total_fires INTEGER,
            dry_season_fires INTEGER,
            transhumance_groups INTEGER,
            transhumance_fires INTEGER,
            avg_transhumance_speed REAL,
            herder_groups INTEGER,
            management_groups INTEGER,
            village_groups INTEGER,
            peak_month INTEGER,
            analysis_json TEXT,
            analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(park_id, year)
        )
    """)
    
    parks = load_parks()
    years = list(range(2018, 2027))  # 2018-2026
    
    print(f"Analyzing {len(parks)} parks x {len(years)} years...")
    print()
    
    total = len(parks) * len(years)
    done = 0
    park_count = 0
    
    for park in parks:
        park_results = []
        for year in years:
            done += 1
            
            results = analyze_park_year(conn, park, year)
            if results:
                park_results.append((year, results))
                
                conn.execute("""
                    INSERT OR REPLACE INTO park_fire_analysis
                    (park_id, year, total_fires, dry_season_fires, transhumance_groups,
                     transhumance_fires, avg_transhumance_speed, herder_groups,
                     management_groups, village_groups, peak_month, analysis_json, analyzed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (
                    park['id'], year,
                    results['total_fires'],
                    results['dry_season_fires'],
                    results['transhumance_groups'],
                    results['transhumance_fires'],
                    results['avg_transhumance_speed'],
                    results['herder_groups'],
                    results['management_groups'],
                    results['village_groups'],
                    results['peak_month'],
                    json.dumps(results['groups'])
                ))
        
        if park_results:
            park_count += 1
            total_groups = sum(r['transhumance_groups'] + r['herder_groups'] + r['management_groups'] + r['village_groups'] for _, r in park_results)
            print(f"[{done//len(years)}/{len(parks)}] {park['id']}: {len(park_results)} years, {total_groups} groups")
        
        if done % 50 == 0:
            conn.commit()
    
    conn.commit()
    conn.close()
    
    print()
    print("=" * 50)
    print(f"Analyzed {park_count} parks")

if __name__ == "__main__":
    main()
