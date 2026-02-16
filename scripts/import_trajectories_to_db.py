#!/usr/bin/env python3
"""
Import fire trajectories v2 to feature_geometries table.

Stores essential fields for map display. Full context/narrative in JSON files.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
INPUT_DIR = BASE_DIR / "data" / "fire_trajectories_v2"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def trajectory_to_geojson(trajectory):
    """Convert trajectory points to GeoJSON LineString."""
    if not trajectory or len(trajectory) < 2:
        return None
    
    coords = []
    for pt in trajectory:
        if isinstance(pt, dict):
            coords.append([pt['lon'], pt['lat']])
        elif isinstance(pt, (list, tuple)) and len(pt) >= 2:
            coords.append([pt[0], pt[1]])
    
    if len(coords) < 2:
        return None
    
    return {"type": "LineString", "coordinates": coords}

def get_bbox(coords):
    if not coords:
        return None, None, None, None
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return min(lons), min(lats), max(lons), max(lats)

def main():
    log("=" * 60)
    log("Import Fire Trajectories to Database (Compact)")
    log("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    
    # Delete existing
    cursor = conn.execute("SELECT COUNT(*) FROM feature_geometries WHERE feature_type = 'fire_trajectory'")
    existing = cursor.fetchone()[0]
    log(f"Deleting {existing} existing fire_trajectory records...")
    conn.execute("DELETE FROM feature_geometries WHERE feature_type = 'fire_trajectory'")
    conn.commit()
    
    park_files = list(INPUT_DIR.glob("*.json"))
    log(f"Processing {len(park_files)} parks...")
    
    total_imported = 0
    batch = []
    batch_size = 5000
    
    for i, park_file in enumerate(sorted(park_files), 1):
        park_id = park_file.stem
        
        with open(park_file) as f:
            trajectories = json.load(f)
        
        for j, traj in enumerate(trajectories):
            year = traj.get('year', 2024)
            feature_id = f"{park_id}_{year}_grp_{j}"
            
            trajectory_pts = traj.get('trajectory', [])
            geojson = trajectory_to_geojson(trajectory_pts)
            if not geojson:
                continue
            
            coords = geojson.get('coordinates', [])
            minx, miny, maxx, maxy = get_bbox(coords)
            
            # Compact properties - essential fields only
            # Full data in JSON files, narrative via API
            classification = traj.get('classification', {})
            context = traj.get('context', {})
            
            properties = {
                'fires': traj.get('fires'),
                'days': traj.get('days'),
                'distance_km': traj.get('distance_km'),
                'speed_km_day': traj.get('speed_km_day'),
                'direction': traj.get('direction'),
                'group_type': traj.get('group_type'),
                'pct_inside': traj.get('pct_inside'),
                'cross_border': traj.get('cross_border'),
                'affected_parks': traj.get('affected_parks'),
                'year': year,
                # Classification summary
                'primary_type': classification.get('primary_type'),
                'confidence': classification.get('confidence'),
                'factors': classification.get('factors', []),
                # Key context (compact)
                'nearest_river': context.get('nearest_river', {}).get('name') if context.get('nearest_river') else None,
                'nearest_place': context.get('nearest_place', {}).get('name') if context.get('nearest_place') else None,
                'season': context.get('season'),
                # Short narrative
                'narrative': traj.get('narrative', '')[:500]  # Truncate to save space
            }
            
            batch.append((
                'fire_trajectory',
                feature_id,
                park_id,
                json.dumps(geojson),
                minx, miny, maxx, maxy,
                traj.get('start_date'),
                traj.get('end_date'),
                json.dumps(properties)
            ))
            
            total_imported += 1
            
            if len(batch) >= batch_size:
                conn.executemany("""
                    INSERT INTO feature_geometries 
                    (feature_type, feature_id, park_id, geojson, 
                     bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                     start_date, end_date, properties_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch)
                conn.commit()
                log(f"  Imported {total_imported} trajectories...")
                batch = []
        
        if i % 20 == 0:
            log(f"[{i}/{len(park_files)}] {park_id}")
    
    if batch:
        conn.executemany("""
            INSERT INTO feature_geometries 
            (feature_type, feature_id, park_id, geojson, 
             bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
             start_date, end_date, properties_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, batch)
        conn.commit()
    
    # Verify
    cursor = conn.execute("SELECT COUNT(*) FROM feature_geometries WHERE feature_type = 'fire_trajectory'")
    final_count = cursor.fetchone()[0]
    
    log("")
    log(f"Complete! Imported {final_count} trajectories")
    
    conn.close()

if __name__ == "__main__":
    main()
