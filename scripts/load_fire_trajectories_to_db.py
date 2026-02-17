#!/usr/bin/env python3
"""Load fire trajectories from JSON files into feature_geometries table."""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
FIRE_TRAJ_V2_DIR = BASE_DIR / "data" / "fire_trajectories_v2"  # Primary source (has feature_id)
FIRE_TRAJ_DIR = BASE_DIR / "data" / "fire_trajectories"  # Fallback source

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def load_fire_trajectories(park_id=None, force=False):
    conn = sqlite3.connect(DB_PATH)
    
    # Get existing fire trajectory count
    existing = conn.execute("SELECT COUNT(*) FROM feature_geometries WHERE feature_type='fire_trajectory'").fetchone()[0]
    log(f"Existing fire_trajectory records: {existing}")
    
    if existing > 0 and not park_id and not force:
        log("Fire trajectories already loaded. Use --park to reload specific park or --force for full reload.")
        conn.close()
        return
    
    # Delete existing fire trajectories for specified park or all
    if park_id:
        conn.execute("DELETE FROM feature_geometries WHERE feature_type='fire_trajectory' AND park_id=?", (park_id,))
    elif force:
        conn.execute("DELETE FROM feature_geometries WHERE feature_type='fire_trajectory'")
    
    # Try v2 first, then fall back to v1
    if FIRE_TRAJ_V2_DIR.exists():
        json_files = list(FIRE_TRAJ_V2_DIR.glob("*.json"))
        source = "v2"
    else:
        json_files = list(FIRE_TRAJ_DIR.glob("*.json"))
        source = "v1"
    
    if park_id:
        json_files = [f for f in json_files if park_id in f.stem]
    
    log(f"Processing {len(json_files)} parks from {source}...")
    
    total_count = 0
    for json_file in json_files:
        try:
            with open(json_file) as f:
                data = json.load(f)
            
            # v2 format: list of trajectories directly
            # v1 format: {'trajectories': [...]}
            if isinstance(data, list):
                trajectories = data
                pid = json_file.stem
            else:
                trajectories = data.get('trajectories', [])
                pid = data.get('park_id', json_file.stem)
            
            count = 0
            for i, t in enumerate(trajectories):
                # Get trajectory coordinates
                trajectory = t.get('trajectory', t.get('trajectory_with_time', []))
                if not trajectory or len(trajectory) < 2:
                    continue
                
                # Build LineString from trajectory
                if isinstance(trajectory[0], dict):
                    coords = [[pt['lon'], pt['lat']] for pt in trajectory]
                else:
                    coords = [[pt[0], pt[1]] for pt in trajectory]
                
                geojson = json.dumps({
                    "type": "LineString",
                    "coordinates": coords
                })
                
                # Get feature_id
                feature_id = t.get('feature_id', f"{pid}_grp_{i}")
                
                # Build properties
                props = {
                    "feature_id": feature_id,
                    "feature_type": "fire_trajectory",
                    "group_type": t.get('classification', {}).get('type', t.get('group_type', 'unknown')),
                    "days": t.get('days', 1),
                    "fires_total": t.get('fires', t.get('fires_total', 0)),
                    "direction": t.get('direction', ''),
                    "distance_km": round(t.get('distance_km', 0), 1),
                    "avg_speed_km_day": round(t.get('speed_km_day', t.get('avg_speed_km_day', 0)), 1),
                    "cross_border": len(t.get('affected_parks', [])) > 1,
                    "affected_parks": t.get('affected_parks', [pid]),
                    "narrative": t.get('narrative', '')
                }
                
                # Date range
                start_date = t.get('start_date', '')
                end_date = t.get('end_date', '')
                
                # Calculate bbox
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                
                conn.execute("""
                    INSERT OR REPLACE INTO feature_geometries 
                    (feature_type, feature_id, park_id, geojson, 
                     bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                     start_date, end_date, properties_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    'fire_trajectory', feature_id, pid, geojson,
                    min(lons), min(lats), max(lons), max(lats),
                    start_date, end_date, json.dumps(props)
                ))
                count += 1
            
            total_count += count
            if count > 0:
                log(f"  {pid}: {count} trajectories")
            
        except Exception as e:
            log(f"Error processing {json_file}: {e}")
            continue
    
    conn.commit()
    conn.close()
    log(f"Total: {total_count} fire trajectories loaded")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--park", help="Load specific park only")
    parser.add_argument("--force", action="store_true", help="Force reload all")
    args = parser.parse_args()
    
    load_fire_trajectories(args.park, args.force)
