#!/usr/bin/env python3
"""Import all JSON data files into the database.

Data sources:
- data/rivers_hydro/*.json - HydroRIVERS with geometry (50km buffer)
- data/lakes_hydro/*.json - HydroLAKES with geometry (50km buffer)  
- data/roads_heigit/*.json - HeiGIT roads with geometry (50km buffer)
- data/osm_places/*.json - OSM place names
- data/fire_trajectories/*.json - Enhanced fire trajectories
- data/settlement_events/*.json - Classified settlements
- data/deforestation_events/*.json - Classified deforestation
- data/export/fire_narratives.json - Pre-computed fire narratives
"""

import json
import sqlite3
import os
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'

def create_tables(conn):
    """Create or update tables"""
    
    # HydroRIVERS with geometry (50km buffer)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS park_rivers_hydro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            hyriv_id INTEGER NOT NULL,
            name TEXT,
            stream_order INTEGER,
            ord_flow INTEGER,
            length_km REAL,
            geojson TEXT,
            UNIQUE(park_id, hyriv_id)
        )
    ''')
    
    # HydroLAKES with geometry (50km buffer)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS park_lakes_hydro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            hylak_id INTEGER NOT NULL,
            name TEXT,
            lake_type INTEGER,
            elevation INTEGER,
            area_km2 REAL,
            centroid_lon REAL,
            centroid_lat REAL,
            geojson TEXT,
            UNIQUE(park_id, hylak_id)
        )
    ''')
    
    # Roads table (HeiGIT) with full attributes
    conn.execute('''
        CREATE TABLE IF NOT EXISTS roads_heigit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            osm_id TEXT,
            name TEXT,
            highway_type TEXT,
            surface TEXT,
            smoothness TEXT,
            width REAL,
            lanes INTEGER,
            passability TEXT,
            length_km REAL,
            geojson TEXT,
            osm_surface_class TEXT,
            osm_length REAL,
            dl_class_2024 TEXT,
            dl_class_2020 TEXT,
            surface_change TEXT,
            passability_code TEXT,
            passability_desc TEXT,
            passability_risk TEXT,
            rw_class TEXT,
            UNIQUE(park_id, osm_id)
        )
    ''')
    
    # Create indexes
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rivers_hydro_park ON park_rivers_hydro(park_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rivers_hydro_order ON park_rivers_hydro(stream_order)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_lakes_hydro_park ON park_lakes_hydro(park_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_roads_heigit_park ON roads_heigit(park_id)')
    
    conn.commit()

def load_rivers_hydro(conn):
    """Load HydroRIVERS data with geometry (50km buffer)"""
    rivers_dir = DATA_DIR / 'rivers_hydro'
    if not rivers_dir.exists():
        print("  No rivers_hydro directory found")
        return 0
    
    # Clear existing data
    conn.execute("DELETE FROM park_rivers_hydro")
    
    count = 0
    for json_file in sorted(rivers_dir.glob('*.json')):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                data = json.load(f)
            
            for r in data.get('rivers', []):
                conn.execute('''
                    INSERT OR REPLACE INTO park_rivers_hydro 
                    (park_id, hyriv_id, name, stream_order, ord_flow, length_km, geojson)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    park_id,
                    r.get('hyriv_id'),
                    r.get('name'),
                    r.get('stream_order'),
                    r.get('ord_flow'),
                    r.get('length_km'),
                    json.dumps(r.get('geometry')) if r.get('geometry') else None
                ))
                count += 1
            
            if count % 50000 == 0:
                print(f"    Imported {count} rivers...")
                conn.commit()
        except Exception as e:
            print(f"  Error loading rivers for {park_id}: {e}")
    
    conn.commit()
    return count

def load_lakes_hydro(conn):
    """Load HydroLAKES data with geometry (50km buffer)"""
    lakes_dir = DATA_DIR / 'lakes_hydro'
    if not lakes_dir.exists():
        print("  No lakes_hydro directory found")
        return 0
    
    # Clear existing data
    conn.execute("DELETE FROM park_lakes_hydro")
    
    count = 0
    for json_file in sorted(lakes_dir.glob('*.json')):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                data = json.load(f)
            
            for lake in data.get('lakes', []):
                conn.execute('''
                    INSERT OR REPLACE INTO park_lakes_hydro 
                    (park_id, hylak_id, name, lake_type, elevation, area_km2,
                     centroid_lon, centroid_lat, geojson)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    park_id,
                    lake.get('hylak_id'),
                    lake.get('name'),
                    lake.get('lake_type'),
                    lake.get('elevation'),
                    lake.get('area_km2'),
                    lake.get('centroid_lon'),
                    lake.get('centroid_lat'),
                    json.dumps(lake.get('geometry')) if lake.get('geometry') else None
                ))
                count += 1
        except Exception as e:
            print(f"  Error loading lakes for {park_id}: {e}")
    
    conn.commit()
    return count

def load_roads_heigit(conn):
    """Load HeiGIT road data with geometry (from roads_heigit)"""
    roads_dir = DATA_DIR / 'roads_heigit'
    if not roads_dir.exists():
        print("  No roads_heigit directory found")
        return 0
    
    # Clear existing data
    conn.execute("DELETE FROM roads_heigit")
    
    count = 0
    for json_file in sorted(roads_dir.glob('*.json')):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                data = json.load(f)
            
            roads = data.get('roads', []) if isinstance(data, dict) else data
            
            for r in roads:
                geom = r.get('geometry', {})
                
                conn.execute('''
                    INSERT OR REPLACE INTO roads_heigit 
                    (park_id, osm_id, name, highway_type, surface, smoothness, 
                     width, lanes, passability, length_km, geojson,
                     osm_surface_class, dl_class_2024, dl_class_2020,
                     surface_change, passability_code, passability_desc,
                     passability_risk, rw_class)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    park_id,
                    r.get('osm_id'),
                    r.get('name'),
                    r.get('highway'),
                    r.get('surface'),
                    r.get('smoothness'),
                    r.get('width'),
                    r.get('lanes'),
                    r.get('passability'),
                    r.get('length_km'),
                    json.dumps(geom) if geom else None,
                    r.get('osm_surface_class'),
                    r.get('dl_class_2024'),
                    r.get('dl_class_2020'),
                    r.get('surface_change'),
                    r.get('passability_code'),
                    r.get('passability_desc'),
                    r.get('passability_risk'),
                    r.get('rw_class')
                ))
                count += 1
        except Exception as e:
            print(f"  Error loading roads for {park_id}: {e}")
    
    conn.commit()
    return count

def load_osm_places(conn):
    """Load OSM places data - handles nested format"""
    places_dir = DATA_DIR / 'osm_places'
    if not places_dir.exists():
        print("  No osm_places directory found")
        return 0
    
    count = 0
    for json_file in places_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                data = json.load(f)
            
            # Handle nested format: { park_id, places: [...] }
            if isinstance(data, dict) and 'places' in data:
                places = data['places']
            elif isinstance(data, list):
                places = data
            else:
                continue
            
            for p in places:
                if not isinstance(p, dict):
                    continue
                
                # Handle osm_tags - can be dict or string
                osm_tags = p.get('osm_tags')
                if isinstance(osm_tags, dict):
                    osm_tags = json.dumps(osm_tags)
                
                conn.execute('''
                    INSERT OR REPLACE INTO osm_places 
                    (park_id, place_type, name, lat, lon, osm_id, geojson, osm_tags)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    park_id,
                    p.get('place_type') or p.get('type'),
                    p.get('name'),
                    p.get('lat'),
                    p.get('lon'),
                    p.get('osm_id'),
                    json.dumps(p.get('geojson')) if p.get('geojson') else None,
                    osm_tags
                ))
                count += 1
        except Exception as e:
            print(f"  Error loading places for {park_id}: {e}")
    
    conn.commit()
    return count

def load_fire_trajectories(conn):
    """Load enhanced fire trajectory data"""
    traj_dir = DATA_DIR / 'fire_trajectories'
    if not traj_dir.exists():
        print("  No fire_trajectories directory found")
        return 0
    
    count = 0
    for json_file in traj_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                trajectories = json.load(f)
            
            for t in trajectories:
                feature_id = t.get('feature_id')
                coords = t.get('coordinates', [])
                
                # Build GeoJSON LineString
                if coords:
                    geojson = json.dumps({
                        "type": "LineString",
                        "coordinates": coords
                    })
                else:
                    geojson = None
                
                # Properties
                props = {
                    'year': t.get('year'),
                    'group_num': t.get('group_num'),
                    'group_type': t.get('group_type'),
                    'refined_type': t.get('refined_type'),
                    'days': t.get('days'),
                    'fires_total': t.get('fires_total'),
                    'season': t.get('season'),
                    'near_rivers': t.get('near_rivers', []),
                    'near_roads': t.get('near_roads', []),
                    'near_places': t.get('near_places', []),
                    'near_settlements': t.get('near_settlements', []),
                    'narrative': t.get('narrative')
                }
                
                # Insert/update feature_geometries
                conn.execute('''
                    INSERT OR REPLACE INTO feature_geometries 
                    (feature_type, feature_id, park_id, geojson, start_date, end_date, properties_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    'fire_trajectory',
                    feature_id,
                    park_id,
                    geojson,
                    t.get('start_date'),
                    t.get('end_date'),
                    json.dumps(props)
                ))
                count += 1
        except Exception as e:
            print(f"  Error loading trajectories for {park_id}: {e}")
    
    conn.commit()
    return count

def load_settlement_events(conn):
    """Load classified settlement events.
    
    Matches by (park_id, lat, lon) instead of auto-increment ID,
    since IDs can differ between database instances.
    """
    settle_dir = DATA_DIR / 'settlement_events'
    if not settle_dir.exists():
        print("  No settlement_events directory found")
        return 0
    
    count = 0
    for json_file in settle_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                settlements = json.load(f)
            
            for s in settlements:
                lat = s.get('lat')
                lon = s.get('lon')
                if lat is None or lon is None:
                    continue
                
                # Match by park_id and coordinates (natural key)
                # Also update polygon_ids for UI polygon display
                cursor = conn.execute('''
                    UPDATE park_settlements 
                    SET classification = ?,
                        classification_confidence = ?,
                        narrative = ?,
                        polygon_ids = ?
                    WHERE park_id = ?
                      AND ABS(lat - ?) < 0.0001
                      AND ABS(lon - ?) < 0.0001
                ''', (
                    s.get('classification'),
                    s.get('classification_confidence'),
                    s.get('narrative'),
                    s.get('polygon_ids'),
                    park_id,
                    lat,
                    lon
                ))
                if cursor.rowcount > 0:
                    count += cursor.rowcount
        except Exception as e:
            print(f"  Error loading settlements for {park_id}: {e}")
    
    conn.commit()
    return count

def load_deforestation_events(conn):
    """Load classified deforestation events.
    
    Matches by (park_id, year, lat, lon) since multiple events can exist
    per park-year (each is a distinct spatial cluster).
    """
    defo_dir = DATA_DIR / 'deforestation_events'
    if not defo_dir.exists():
        print("  No deforestation_events directory found")
        return 0
    
    count = 0
    for json_file in defo_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                events = json.load(f)
            
            for e in events:
                lat = e.get('lat')
                lon = e.get('lon')
                year = e.get('year')
                if lat is None or lon is None or year is None:
                    continue
                
                # Match by (park_id, year, lat, lon) - coordinates identify the cluster
                # Also update polygon_ids for UI polygon display
                cursor = conn.execute('''
                    UPDATE deforestation_events 
                    SET classification = ?,
                        classification_confidence = ?,
                        narrative = ?,
                        polygon_ids = ?
                    WHERE park_id = ?
                      AND year = ?
                      AND ABS(lat - ?) < 0.0001
                      AND ABS(lon - ?) < 0.0001
                ''', (
                    e.get('classification'),
                    e.get('classification_confidence'),
                    e.get('narrative'),
                    e.get('polygon_ids'),
                    park_id,
                    year,
                    lat,
                    lon
                ))
                if cursor.rowcount > 0:
                    count += cursor.rowcount
        except Exception as e:
            print(f"  Error loading deforestation for {park_id}: {e}")
    
    conn.commit()
    return count

def load_fire_narratives(conn):
    """Load pre-computed fire narratives to cache"""
    narratives_dir = DATA_DIR / 'export' / 'fire_narratives'
    if not narratives_dir.exists():
        # Try single file
        narratives_file = DATA_DIR / 'export' / 'fire_narratives.json'
        if narratives_file.exists():
            with open(narratives_file) as f:
                narratives = json.load(f)
            
            count = 0
            for park_id, narrative in narratives.items():
                conn.execute('''
                    INSERT OR REPLACE INTO fire_narrative_cache 
                    (park_id, narrative_json, computed_at, from_year, to_year)
                    VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
                ''', (
                    park_id,
                    json.dumps(narrative),
                    narrative.get('from_year', 2000),
                    narrative.get('to_year', 2026)
                ))
                count += 1
            conn.commit()
            return count
        return 0
    
    count = 0
    for json_file in narratives_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                narrative = json.load(f)
            
            conn.execute('''
                INSERT OR REPLACE INTO fire_narrative_cache 
                (park_id, narrative_json, computed_at, from_year, to_year)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
            ''', (
                park_id,
                json.dumps(narrative),
                narrative.get('from_year', 2000),
                narrative.get('to_year', 2026)
            ))
            count += 1
        except Exception as e:
            print(f"  Error loading narrative for {park_id}: {e}")
    
    conn.commit()
    return count

def main():
    print("=" * 60)
    print("Importing all JSON data into database")
    print(f"Database: {DB_PATH}")
    print(f"Data directory: {DATA_DIR}")
    print("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    
    # Create tables
    print("\n[0] Creating/updating tables...")
    create_tables(conn)
    
    # Load HydroRIVERS with geometry
    print("\n[1] Loading HydroRIVERS with geometry (50km buffer)...")
    rivers_hydro = load_rivers_hydro(conn)
    print(f"    Loaded {rivers_hydro} river segments with geometry")
    
    # Load HydroLAKES with geometry
    print("\n[2] Loading HydroLAKES with geometry (50km buffer)...")
    lakes_hydro = load_lakes_hydro(conn)
    print(f"    Loaded {lakes_hydro} lakes with geometry")
    
    # Load roads
    print("\n[3] Loading HeiGIT roads with geometry (50km buffer)...")
    roads = load_roads_heigit(conn)
    print(f"    Loaded {roads} road segments")
    
    # Load OSM places
    print("\n[4] Loading OSM places...")
    places = load_osm_places(conn)
    print(f"    Loaded {places} places")
    
    # Load fire trajectories
    print("\n[5] Loading enhanced fire trajectories...")
    trajectories = load_fire_trajectories(conn)
    print(f"    Loaded {trajectories} trajectories")
    
    # Load settlement events
    print("\n[6] Updating settlement classifications...")
    settlements = load_settlement_events(conn)
    print(f"    Updated {settlements} settlement records")
    
    # Load deforestation events
    print("\n[7] Updating deforestation classifications...")
    deforest = load_deforestation_events(conn)
    print(f"    Updated {deforest} deforestation records")
    
    # Load fire narratives
    print("\n[8] Loading fire narratives to cache...")
    narratives = load_fire_narratives(conn)
    print(f"    Loaded {narratives} narrative caches")
    
    # Summary
    print("\n" + "=" * 60)
    print("Import complete! Summary:")
    cursor = conn.execute("SELECT COUNT(*), COUNT(DISTINCT park_id) FROM park_rivers_hydro")
    r = cursor.fetchone()
    print(f"  Rivers (HydroRIVERS): {r[0]:,} segments across {r[1]} parks")
    cursor = conn.execute("SELECT COUNT(*), COUNT(DISTINCT park_id) FROM park_lakes_hydro")
    r = cursor.fetchone()
    print(f"  Lakes (HydroLAKES): {r[0]:,} lakes across {r[1]} parks")
    cursor = conn.execute("SELECT COUNT(*), COUNT(DISTINCT park_id) FROM roads_heigit")
    r = cursor.fetchone()
    print(f"  Roads (HeiGIT): {r[0]:,} segments across {r[1]} parks")
    cursor = conn.execute("SELECT COUNT(*) FROM osm_places")
    print(f"  OSM places: {cursor.fetchone()[0]:,}")
    cursor = conn.execute("SELECT COUNT(*) FROM feature_geometries WHERE feature_type='fire_trajectory'")
    print(f"  Fire trajectories: {cursor.fetchone()[0]:,}")
    cursor = conn.execute("SELECT COUNT(*) FROM park_settlements WHERE classification IS NOT NULL")
    print(f"  Classified settlements: {cursor.fetchone()[0]:,}")
    cursor = conn.execute("SELECT COUNT(*) FROM deforestation_events WHERE classification IS NOT NULL")
    print(f"  Classified deforestation: {cursor.fetchone()[0]:,}")
    cursor = conn.execute("SELECT COUNT(*) FROM fire_narrative_cache")
    print(f"  Fire narrative caches: {cursor.fetchone()[0]}")
    print("=" * 60)
    
    conn.close()

if __name__ == '__main__':
    main()
