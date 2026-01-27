#!/usr/bin/env python3
"""
Fire group infraction analysis with response effectiveness metrics.

Key insight: Groups may STOP BURNING when approached by rangers, so:
- "Fires stopped inside PA" could mean staff contact (GOOD response)
- "Fires resumed outside PA" means group just transited (NO contact)

Metrics:
- days_burning_inside: How long fires detected inside
- fires_stopped_inside: Last detection was inside PA (potential staff contact)
- fires_resumed_outside: Group continued burning after exit (no contact)
- abrupt_stop: Fires stopped suddenly (gap > 3 days before next detection)
"""

import json
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from glob import glob

try:
    from shapely.geometry import shape, Point
    from shapely.prepared import prep
except ImportError:
    print("ERROR: shapely required")
    exit(1)

import sys
sys.path.insert(0, str(Path(__file__).parent))
from fire_group_detection import (
    detect_daily_clusters, track_clusters, classify_trajectory, distance_km
)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
FIRE_DIR = DATA_DIR / "fire"
DB_PATH = BASE_DIR / "db.sqlite3"

BUFFER_KM = 300

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def load_keystones():
    with open(DATA_DIR / "keystones_with_boundaries.json") as f:
        return json.load(f)

def get_park_boundary(park):
    geom = park.get('geometry')
    if not geom:
        return None, None
    try:
        shp = shape(geom)
        return shp, prep(shp)
    except:
        return None, None

def get_park_bbox(park, buffer_km=BUFFER_KM):
    lat = park['coordinates']['lat']
    lon = park['coordinates']['lon']
    buffer_deg = buffer_km / 111.0
    return {
        'lat_min': lat - buffer_deg, 'lat_max': lat + buffer_deg,
        'lon_min': lon - buffer_deg, 'lon_max': lon + buffer_deg
    }

def load_fire_data_for_region(bbox, year):
    all_fires = []
    patterns = [
        FIRE_DIR / "viirs-jpss1" / str(year) / "*.csv",
        FIRE_DIR / f"viirs-jpss1_{year}_*.csv"
    ]
    for pattern in patterns:
        for fpath in glob(str(pattern)):
            try:
                df = pd.read_csv(fpath)
                filtered = df[
                    (df['latitude'] >= bbox['lat_min']) & (df['latitude'] <= bbox['lat_max']) &
                    (df['longitude'] >= bbox['lon_min']) & (df['longitude'] <= bbox['lon_max'])
                ]
                if len(filtered) > 0:
                    all_fires.append(filtered)
            except:
                continue
    
    if not all_fires:
        return None
    
    combined = pd.concat(all_fires, ignore_index=True)
    combined['date'] = pd.to_datetime(combined['acq_date'])
    combined = combined[combined['date'].dt.month.isin([1, 2, 3, 11, 12])]
    return combined

def analyze_trajectory_response(traj, boundary_prep):
    """
    Analyze trajectory with focus on what happened to the group.
    
    Outcomes:
    - TRANSITED: Entered and exited, continued burning outside
    - STOPPED_INSIDE: Last detection inside PA (possibly contacted by staff)
    - STOPPED_AFTER_EXIT: Exited then stopped (end of dry season or destination reached)
    """
    if not traj or len(traj) < 3:
        return None
    
    # Classify each point
    point_status = []
    for i, cluster in enumerate(traj):
        pt = Point(cluster['lon'], cluster['lat'])
        is_inside = boundary_prep.contains(pt)
        point_status.append({
            'idx': i,
            'inside': is_inside,
            'date': cluster['date'],
            'lat': cluster['lat'],
            'lon': cluster['lon'],
            'fires': cluster['fires']
        })
    
    # Find inside segments
    inside_points = [p for p in point_status if p['inside']]
    if not inside_points:
        return None
    
    first_inside_idx = inside_points[0]['idx']
    last_inside_idx = inside_points[-1]['idx']
    
    # Points before entry
    before_entry = [p for p in point_status if p['idx'] < first_inside_idx]
    # Points after last inside detection
    after_last_inside = [p for p in point_status if p['idx'] > last_inside_idx]
    
    # Determine outcome
    if not after_last_inside:
        # Last detection was inside PA
        outcome = 'STOPPED_INSIDE'
        outcome_detail = 'Fires stopped inside PA - possible staff contact or end of tracking'
    else:
        # Check if fires resumed outside
        outside_after = [p for p in after_last_inside if not p['inside']]
        if outside_after:
            outcome = 'TRANSITED'
            outcome_detail = f'Group transited PA, continued burning {len(outside_after)} days after exit'
        else:
            outcome = 'STOPPED_AFTER_EXIT'
            outcome_detail = 'Group exited but stopped burning'
    
    # Calculate time gaps to detect abrupt stops
    days_inside = len(inside_points)
    
    # Movement while inside
    inside_distance = 0
    for i in range(first_inside_idx, last_inside_idx):
        inside_distance += distance_km(
            traj[i]['lat'], traj[i]['lon'],
            traj[i+1]['lat'], traj[i+1]['lon']
        )
    speed_inside = inside_distance / days_inside if days_inside > 0 else 0
    
    result = {
        # Trajectory info
        'trajectory_days': len(traj),
        'total_fires': sum(c['fires'] for c in traj),
        
        # Origin
        'origin_lat': round(traj[0]['lat'], 4),
        'origin_lon': round(traj[0]['lon'], 4),
        'origin_date': traj[0]['date'],
        'days_tracked_before': len(before_entry),
        'origin_track': [
            {'date': p['date'], 'lat': round(p['lat'], 4), 'lon': round(p['lon'], 4), 'fires': p['fires']}
            for p in before_entry
        ],
        
        # Entry
        'entry_date': inside_points[0]['date'],
        'entry_lat': round(inside_points[0]['lat'], 4),
        'entry_lon': round(inside_points[0]['lon'], 4),
        
        # Inside PA - KEY METRICS
        'days_burning_inside': days_inside,
        'fires_inside': sum(p['fires'] for p in inside_points),
        'distance_inside_km': round(inside_distance, 1),
        'speed_inside_km_day': round(speed_inside, 1),
        
        # Exit / Last inside
        'last_inside_date': inside_points[-1]['date'],
        'last_inside_lat': round(inside_points[-1]['lat'], 4),
        'last_inside_lon': round(inside_points[-1]['lon'], 4),
        
        # OUTCOME - What happened to the group?
        'outcome': outcome,
        'outcome_detail': outcome_detail,
        'days_tracked_after': len(after_last_inside),
        
        # Destination (if tracked after)
        'dest_lat': round(traj[-1]['lat'], 4),
        'dest_lon': round(traj[-1]['lon'], 4),
        'dest_date': traj[-1]['date'],
        'dest_track': [
            {'date': p['date'], 'lat': round(p['lat'], 4), 'lon': round(p['lon'], 4), 'fires': p['fires']}
            for p in after_last_inside
        ],
        
        # Movement
        'net_south_km': round((traj[0]['lat'] - traj[-1]['lat']) * 111, 1),
    }
    
    return result

def analyze_park(park, year):
    park_id = park['id']
    boundary, boundary_prep = get_park_boundary(park)
    
    if boundary is None:
        return None
    
    bbox = get_park_bbox(park)
    fires = load_fire_data_for_region(bbox, year)
    
    if fires is None or len(fires) < 100:
        return None
    
    daily_clusters = detect_daily_clusters(fires, eps_km=15, min_fires=8)
    if len(daily_clusters) < 5:
        return None
    
    trajectories = track_clusters(daily_clusters, max_link_km=25, max_gap_days=3)
    if not trajectories:
        return None
    
    infraction_trajs = []
    for traj in trajectories:
        typ, metrics = classify_trajectory(traj)
        
        # Skip management fires
        if 'management' in typ:
            continue
        
        interaction = analyze_trajectory_response(traj, boundary_prep)
        
        if interaction:
            interaction['group_type'] = typ
            interaction['avg_speed_km_day'] = round(metrics.get('avg_speed_km_day', 0), 1)
            
            # Plausibility: speed 0.5-30 km/day
            if 0.5 <= interaction['avg_speed_km_day'] <= 30:
                infraction_trajs.append(interaction)
    
    if not infraction_trajs:
        return None
    
    # Outcome counts
    outcomes = defaultdict(int)
    for t in infraction_trajs:
        outcomes[t['outcome']] += 1
    
    days_list = [t['days_burning_inside'] for t in infraction_trajs]
    
    return {
        'trajectories': infraction_trajs,
        'summary': {
            'total_groups': len(infraction_trajs),
            'transhumance_groups': len([t for t in infraction_trajs if 'transhumance' in t['group_type']]),
            'herder_groups': len([t for t in infraction_trajs if 'herder' in t['group_type']]),
            
            # Days burning inside
            'avg_days_burning': round(np.mean(days_list), 1),
            'median_days_burning': round(np.median(days_list), 1),
            'max_days_burning': max(days_list),
            'total_fires_inside': sum(t['fires_inside'] for t in infraction_trajs),
            
            # Outcomes
            'groups_transited': outcomes['TRANSITED'],  # Passed through, no contact
            'groups_stopped_inside': outcomes['STOPPED_INSIDE'],  # Possible staff contact
            'groups_stopped_after': outcomes['STOPPED_AFTER_EXIT'],
            
            # Tracking coverage
            'avg_days_tracked_before': round(np.mean([t['days_tracked_before'] for t in infraction_trajs]), 1),
            'avg_days_tracked_after': round(np.mean([t['days_tracked_after'] for t in infraction_trajs]), 1),
        }
    }

def save_results(park_id, year, data):
    conn = get_db()
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS park_group_infractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            year INTEGER NOT NULL,
            total_groups INTEGER,
            transhumance_groups INTEGER,
            herder_groups INTEGER,
            avg_days_burning REAL,
            median_days_burning REAL,
            max_days_burning INTEGER,
            total_fires_inside INTEGER,
            groups_transited INTEGER,
            groups_stopped_inside INTEGER,
            groups_stopped_after INTEGER,
            avg_days_tracked_before REAL,
            avg_days_tracked_after REAL,
            trajectories_json TEXT,
            analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(park_id, year)
        )
    """)
    
    s = data['summary']
    conn.execute("""
        INSERT OR REPLACE INTO park_group_infractions
        (park_id, year, total_groups, transhumance_groups, herder_groups,
         avg_days_burning, median_days_burning, max_days_burning, total_fires_inside,
         groups_transited, groups_stopped_inside, groups_stopped_after,
         avg_days_tracked_before, avg_days_tracked_after,
         trajectories_json, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        park_id, year, s['total_groups'], s['transhumance_groups'], s['herder_groups'],
        s['avg_days_burning'], s['median_days_burning'], s['max_days_burning'], s['total_fires_inside'],
        s['groups_transited'], s['groups_stopped_inside'], s['groups_stopped_after'],
        s['avg_days_tracked_before'], s['avg_days_tracked_after'],
        json.dumps(data['trajectories']),
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--park', help='Specific park ID')
    parser.add_argument('--year', type=int, help='Specific year')
    args = parser.parse_args()
    
    print("Loading keystones...")
    keystones = load_keystones()
    
    if args.park:
        keystones = [p for p in keystones if p['id'] == args.park]
    
    years = [args.year] if args.year else [2022, 2023, 2024]
    
    print(f"Analyzing {len(keystones)} parks for {years}\n")
    
    total_saved = 0
    
    for park in keystones:
        for year in years:
            print(f"{park['id']} {year}...", end=" ", flush=True)
            
            try:
                results = analyze_park(park, year)
                if results:
                    save_results(park['id'], year, results)
                    s = results['summary']
                    print(f"{s['total_groups']} groups | "
                          f"avg {s['avg_days_burning']:.1f}d burning | "
                          f"transited:{s['groups_transited']} stopped_inside:{s['groups_stopped_inside']} stopped_after:{s['groups_stopped_after']}")
                    total_saved += 1
                else:
                    print("no groups")
            except Exception as e:
                print(f"error: {e}")
    
    print(f"\n✓ Saved {total_saved} records")

if __name__ == '__main__':
    main()
