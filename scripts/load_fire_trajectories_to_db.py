#!/usr/bin/env python3
"""
Load fire trajectories from JSON files into feature_geometries table.
This enables pinning individual fires on the map.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
FIRE_ANALYSIS_DIR = BASE_DIR / "data" / "fire_analysis"
FIRE_TRAJ_DIR = BASE_DIR / "data" / "fire_trajectories"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def load_fire_trajectories(park_id=None):
    conn = sqlite3.connect(DB_PATH)
    
    # Get existing fire trajectory count
    existing = conn.execute("SELECT COUNT(*) FROM feature_geometries WHERE feature_type='fire_trajectory'").fetchone()[0]
    log(f"Existing fire_trajectory records: {existing}")
    
    if existing > 0 and not park_id:
        log("Fire trajectories already loaded. Use --park to reload specific park.")
        conn.close()
        return
    
    # Delete existing fire trajectories for specified park or all
    if park_id:
        conn.execute("DELETE FROM feature_geometries WHERE feature_type='fire_trajectory' AND park_id=?", (park_id,))
    else:
        conn.execute("DELETE FROM feature_geometries WHERE feature_type='fire_trajectory'")
    
    # Load from fire_analysis JSON files (has trajectory coordinates)
    json_files = list(FIRE_ANALYSIS_DIR.glob("*.json"))
    if park_id:
        json_files = [f for f in json_files if park_id in f.name]
    
    log(f"Processing {len(json_files)} parks...")
    
    total_count = 0
    for json_file in json_files:
        try:
            with open(json_file) as f:
                data = json.load(f)
            
            pid = data.get('park_id', json_file.stem)
            groups = data.get('groups', [])
            
            # Also load narrative from fire_trajectories if available
            narratives = {}
            traj_file = FIRE_TRAJ_DIR / f"{pid}.json"
            if traj_file.exists():
                with open(traj_file) as f:
                    traj_data = json.load(f)
                for i, t in enumerate(traj_data.get('trajectories', [])):
                    narratives[i] = t.get('narrative', '')
            
            count = 0
            for i, g in enumerate(groups):
                trajectory = g.get('trajectory', [])
                if not trajectory or len(trajectory) < 2:
                    continue
                
                # Build LineString from trajectory
                coords = [[pt['lon'], pt['lat']] for pt in trajectory]
                geojson = json.dumps({
                    "type": "LineString",
                    "coordinates": coords
                })
                
                # Build properties
                props = {
                    "feature_id": f"{pid}_grp_{i}",
                    "feature_type": "fire_trajectory",
                    "group_type": g.get('group_type', 'unknown'),
                    "days": g.get('days', 0),
                    "fires_total": g.get('fires', 0),
                    "direction": g.get('direction', ''),
                    "distance_km": g.get('total_distance_km', 0),
                    "avg_speed_km_day": g.get('avg_speed_km_day', 0),
                    "cross_border": g.get('cross_border', False),
                    "affected_parks": g.get('affected_parks', [pid]),
                    "narrative": narratives.get(i, '')
                }
                
                start_date = g.get('start_date', '')
                end_date = g.get('end_date', '')
                
                # Get bounding box
                lats = [pt['lat'] for pt in trajectory]
                lons = [pt['lon'] for pt in trajectory]
                
                conn.execute("""
                    INSERT INTO feature_geometries 
                    (feature_type, feature_id, park_id, geojson, properties_json, 
                     bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, start_date, end_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    'fire_trajectory',
                    f"{pid}_grp_{i}",
                    pid,
                    geojson,
                    json.dumps(props),
                    min(lons), min(lats), max(lons), max(lats),
                    start_date, end_date
                ))
                count += 1
            
            total_count += count
            if count > 0:
                log(f"  {pid}: {count} trajectories")
                
        except Exception as e:
            log(f"Error processing {json_file.name}: {e}")
    
    conn.commit()
    conn.close()
    log(f"Total: {total_count} fire trajectories loaded")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--park', help='Load specific park only')
    parser.add_argument('--force', action='store_true', help='Force reload all')
    args = parser.parse_args()
    
    if args.force:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM feature_geometries WHERE feature_type='fire_trajectory'")
        conn.commit()
        conn.close()
    
    load_fire_trajectories(args.park)
