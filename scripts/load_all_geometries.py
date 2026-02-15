#!/usr/bin/env python3
"""
Load all geometry data from JSON files into feature_geometries table
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'

def load_settlement_geometries(conn):
    """Load settlement polygons from feature_geometries/settlement/"""
    geom_dir = DATA_DIR / 'feature_geometries' / 'settlement'
    if not geom_dir.exists():
        return 0
    
    count = 0
    for json_file in geom_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                features = json.load(f)
            
            for feat in features:
                conn.execute('''
                    INSERT OR REPLACE INTO feature_geometries 
                    (feature_type, feature_id, park_id, geojson, 
                     bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                     start_date, end_date, properties_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    'settlement',
                    feat.get('feature_id'),
                    park_id,
                    json.dumps(feat.get('geojson')),
                    feat.get('bbox', [None]*4)[0],
                    feat.get('bbox', [None]*4)[1],
                    feat.get('bbox', [None]*4)[2],
                    feat.get('bbox', [None]*4)[3],
                    feat.get('start_date'),
                    feat.get('end_date'),
                    json.dumps(feat.get('properties', {}))
                ))
                count += 1
        except Exception as e:
            print(f"  Error {park_id}: {e}")
    
    conn.commit()
    return count

def load_deforestation_geometries(conn):
    """Load deforestation polygons from feature_geometries/deforestation/"""
    geom_dir = DATA_DIR / 'feature_geometries' / 'deforestation'
    if not geom_dir.exists():
        return 0
    
    count = 0
    for json_file in geom_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                features = json.load(f)
            
            for feat in features:
                conn.execute('''
                    INSERT OR REPLACE INTO feature_geometries 
                    (feature_type, feature_id, park_id, geojson,
                     bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                     start_date, end_date, properties_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    'deforestation',
                    feat.get('feature_id'),
                    park_id,
                    json.dumps(feat.get('geojson')),
                    feat.get('bbox', [None]*4)[0],
                    feat.get('bbox', [None]*4)[1],
                    feat.get('bbox', [None]*4)[2],
                    feat.get('bbox', [None]*4)[3],
                    feat.get('start_date'),
                    feat.get('end_date'),
                    json.dumps(feat.get('properties', {}))
                ))
                count += 1
        except Exception as e:
            print(f"  Error {park_id}: {e}")
    
    conn.commit()
    return count

def load_fire_trajectories(conn):
    """Load fire trajectories from fire_trajectories/"""
    traj_dir = DATA_DIR / 'fire_trajectories'
    if not traj_dir.exists():
        return 0
    
    count = 0
    for json_file in traj_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                trajectories = json.load(f)
            
            for t in trajectories:
                coords = t.get('coordinates', [])
                geojson = json.dumps({"type": "LineString", "coordinates": coords}) if coords else None
                
                # Calculate bbox
                if coords:
                    lons = [c[0] for c in coords]
                    lats = [c[1] for c in coords]
                    bbox = [min(lons), min(lats), max(lons), max(lats)]
                else:
                    bbox = [None, None, None, None]
                
                props = {k: v for k, v in t.items() if k not in ['feature_id', 'park_id', 'coordinates', 'start_date', 'end_date']}
                
                conn.execute('''
                    INSERT OR REPLACE INTO feature_geometries 
                    (feature_type, feature_id, park_id, geojson,
                     bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                     start_date, end_date, properties_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    'fire_trajectory',
                    t.get('feature_id'),
                    park_id,
                    geojson,
                    bbox[0], bbox[1], bbox[2], bbox[3],
                    t.get('start_date'),
                    t.get('end_date'),
                    json.dumps(props)
                ))
                count += 1
        except Exception as e:
            print(f"  Error {park_id}: {e}")
    
    conn.commit()
    return count

def load_roads_as_geometries(conn):
    """Load roads from roads_heigit/ into feature_geometries"""
    roads_dir = DATA_DIR / 'roads_heigit'
    if not roads_dir.exists():
        return 0
    
    count = 0
    for json_file in roads_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                roads = json.load(f)
            
            for i, r in enumerate(roads):
                geom = r.get('geometry', {})
                props = r.get('properties', r)
                
                feature_id = f"road_{park_id}_{props.get('osm_id', i)}"
                
                conn.execute('''
                    INSERT OR REPLACE INTO feature_geometries 
                    (feature_type, feature_id, park_id, geojson, properties_json)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    'road',
                    feature_id,
                    park_id,
                    json.dumps(geom) if geom else None,
                    json.dumps(props)
                ))
                count += 1
        except Exception as e:
            print(f"  Error {park_id}: {e}")
    
    conn.commit()
    return count

def main():
    print("=" * 60)
    print("Loading all geometries from JSON files")
    print("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    
    print("\n[1] Loading settlement polygons...")
    settlements = load_settlement_geometries(conn)
    print(f"    Loaded {settlements} settlement polygons")
    
    print("\n[2] Loading deforestation polygons...")
    deforest = load_deforestation_geometries(conn)
    print(f"    Loaded {deforest} deforestation polygons")
    
    print("\n[3] Loading fire trajectories...")
    fires = load_fire_trajectories(conn)
    print(f"    Loaded {fires} fire trajectories")
    
    print("\n[4] Loading road geometries...")
    roads = load_roads_as_geometries(conn)
    print(f"    Loaded {roads} road segments")
    
    # Summary
    print("\n" + "=" * 60)
    print("Summary by feature type:")
    cursor = conn.execute("""
        SELECT feature_type, COUNT(*) 
        FROM feature_geometries 
        GROUP BY feature_type
    """)
    for row in cursor:
        print(f"  {row[0]}: {row[1]}")
    
    print("=" * 60)
    conn.close()

if __name__ == '__main__':
    main()
