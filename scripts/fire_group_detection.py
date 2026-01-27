#!/usr/bin/env python3
"""
Fire Group Detection Algorithm for Conservation Areas

Detects and classifies fire groups based on movement patterns:
- Transhumance: Sustained southward movement, 5-15 km/day
- Herder local: Short-range movement around an area
- Management burns: Fast spread (vehicle/aircraft)
- Village/persistent: Stationary locations
"""

import pandas as pd
import numpy as np
from sklearn.cluster import DBSCAN
from collections import defaultdict
import json
import sys

def distance_km(lat1, lon1, lat2, lon2):
    """Calculate distance in km between two points."""
    lat_diff = abs(lat2 - lat1) * 111
    lon_diff = abs(lon2 - lon1) * 111 * np.cos(np.radians((lat1 + lat2) / 2))
    return np.sqrt(lat_diff**2 + lon_diff**2)

def load_fire_data(filepath, bbox=None):
    """Load fire data, optionally filtering to bounding box."""
    df = pd.read_csv(filepath)
    if bbox:
        lat_min, lat_max, lon_min, lon_max = bbox
        df = df[(df['latitude'] >= lat_min) & (df['latitude'] <= lat_max) & 
                (df['longitude'] >= lon_min) & (df['longitude'] <= lon_max)]
    df['date'] = pd.to_datetime(df['acq_date'])
    return df

def detect_daily_clusters(fires_df, eps_km=15, min_fires=8):
    """
    Detect spatial fire clusters for each day.
    Returns dict of date -> list of cluster info.
    """
    daily_clusters = {}
    eps_deg = eps_km / 111.0
    
    for date in sorted(fires_df['acq_date'].unique()):
        day_fires = fires_df[fires_df['acq_date'] == date]
        if len(day_fires) < min_fires:
            continue
        
        coords = day_fires[['latitude', 'longitude']].values
        clustering = DBSCAN(eps=eps_deg, min_samples=min_fires//2).fit(coords)
        
        clusters = []
        for cid in set(clustering.labels_):
            if cid == -1:
                continue
            mask = clustering.labels_ == cid
            cf = day_fires[mask]
            
            clusters.append({
                'date': date,
                'cid': cid,
                'lat': float(cf['latitude'].mean()),
                'lon': float(cf['longitude'].mean()),
                'lat_min': float(cf['latitude'].min()),
                'lat_max': float(cf['latitude'].max()),
                'fires': int(len(cf)),
                'frp': float(cf['frp'].sum()),
                'spread_km': float(max(
                    (cf['latitude'].max() - cf['latitude'].min()) * 111,
                    (cf['longitude'].max() - cf['longitude'].min()) * 111
                ))
            })
        
        if clusters:
            daily_clusters[date] = clusters
    
    return daily_clusters

def track_clusters(daily_clusters, max_link_km=25, max_gap_days=3):
    """
    Track clusters across days to build trajectories.
    """
    trajectories = []
    used = set()
    sorted_dates = sorted(daily_clusters.keys())
    
    for start_idx, start_date in enumerate(sorted_dates):
        for cluster in daily_clusters[start_date]:
            key = (start_date, cluster['cid'])
            if key in used:
                continue
            
            traj = [cluster]
            used.add(key)
            current = cluster
            
            for next_idx in range(start_idx + 1, len(sorted_dates)):
                next_date = sorted_dates[next_idx]
                date_gap = (pd.to_datetime(next_date) - pd.to_datetime(current['date'])).days
                
                if date_gap > max_gap_days:
                    break
                
                best = None
                best_score = float('inf')
                
                for nc in daily_clusters[next_date]:
                    nkey = (next_date, nc['cid'])
                    if nkey in used:
                        continue
                    
                    dist = distance_km(current['lat'], current['lon'], nc['lat'], nc['lon'])
                    if dist > max_link_km:
                        continue
                    
                    lat_move = current['lat'] - nc['lat']
                    size_ratio = max(current['fires'], nc['fires']) / max(1, min(current['fires'], nc['fires']))
                    score = dist - lat_move * 5 + size_ratio * 2
                    
                    if score < best_score:
                        best = nc
                        best_score = score
                
                if best:
                    traj.append(best)
                    used.add((next_date, best['cid']))
                    current = best
            
            if len(traj) >= 5:
                trajectories.append(traj)
    
    return trajectories

def classify_trajectory(traj):
    """
    Classify trajectory based on movement pattern.
    
    Returns (type, metrics_dict)
    
    Types:
    - transhumance: Sustained southward movement, 5-15 km/day
    - transhumance_slow: Slow southward movement, >10 days
    - herder_local: Short movements, not clearly directional
    - herder_fast: Fast local movement
    - management_fast: Very fast (>30 km/day) - aircraft
    - management_vehicle: Fast with large spread - vehicle
    - local_burning: Short duration local burns
    - local_stationary: Very slow, short duration
    - village_persistent: Very slow, long duration
    """
    if len(traj) < 3:
        return 'unknown', {}
    
    start, end = traj[0], traj[-1]
    
    total_fires = sum(c['fires'] for c in traj)
    days = len(traj)
    net_south = (start['lat'] - end['lat']) * 111
    net_east = (end['lon'] - start['lon']) * 111
    
    movements = []
    for i in range(1, len(traj)):
        d = distance_km(traj[i-1]['lat'], traj[i-1]['lon'], 
                       traj[i]['lat'], traj[i]['lon'])
        movements.append(d)
    
    avg_speed = np.mean(movements) if movements else 0
    max_speed = max(movements) if movements else 0
    avg_spread = np.mean([c['spread_km'] for c in traj])
    
    metrics = {
        'days': days,
        'fires': total_fires,
        'net_south_km': round(net_south, 1),
        'net_east_km': round(net_east, 1),
        'avg_speed_km_day': round(avg_speed, 1),
        'max_speed_km_day': round(max_speed, 1),
        'avg_spread_km': round(avg_spread, 1),
        'start_date': start['date'],
        'end_date': end['date'],
        'start_lat': round(start['lat'], 3),
        'start_lon': round(start['lon'], 3),
        'end_lat': round(end['lat'], 3),
        'end_lon': round(end['lon'], 3)
    }
    
    # Classification rules
    if avg_speed > 30:
        return 'management_fast', metrics
    elif avg_speed > 15:
        if avg_spread > 30:
            return 'management_vehicle', metrics
        else:
            return 'herder_fast', metrics
    elif avg_speed > 5:
        if net_south > 20:
            return 'transhumance', metrics
        else:
            return 'herder_local', metrics
    elif avg_speed > 2:
        if days > 10 and net_south > 15:
            return 'transhumance_slow', metrics
        else:
            return 'local_burning', metrics
    else:
        if days > 7:
            return 'village_persistent', metrics
        else:
            return 'local_stationary', metrics

def analyze_park(fire_data, park_name, year):
    """
    Full analysis pipeline for a park.
    """
    daily_clusters = detect_daily_clusters(fire_data)
    trajectories = track_clusters(daily_clusters)
    
    results = {
        'park': park_name,
        'year': year,
        'total_fires': len(fire_data),
        'active_days': len(daily_clusters),
        'trajectories_detected': len(trajectories),
        'groups': defaultdict(list)
    }
    
    for traj in trajectories:
        typ, metrics = classify_trajectory(traj)
        results['groups'][typ].append(metrics)
    
    # Convert to regular dict for JSON
    results['groups'] = dict(results['groups'])
    
    return results

if __name__ == '__main__':
    # Example: analyze Chinko 2023
    bbox = (5.5, 7.5, 23.0, 25.0)  # lat_min, lat_max, lon_min, lon_max
    
    for year in [2022, 2023, 2024]:
        filepath = f'data/fire/viirs-jpss1_{year}_Central_African_Republic.csv'
        try:
            df = load_fire_data(filepath, bbox)
            dry = df[df['date'].dt.month.isin([1, 2, 3, 11, 12])]
            results = analyze_park(dry, 'Chinko', year)
            
            print(f"\n=== {results['park']} {year} ===")
            print(f"Total fires: {results['total_fires']:,}")
            print(f"Trajectories: {results['trajectories_detected']}")
            
            for typ, groups in sorted(results['groups'].items(), key=lambda x: -len(x[1])):
                print(f"  {typ}: {len(groups)}")
                
        except FileNotFoundError:
            print(f"File not found: {filepath}")
