#!/usr/bin/env python3
"""
Import all JSON data files into the database.
"""

import json
import sqlite3
import os
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'

def create_tables(conn):
    """Create or update tables for new data"""
    
    # Rivers table (legacy - point data only)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS rivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hyriv_id INTEGER UNIQUE,
            name TEXT,
            length_km REAL,
            discharge_cms REAL,
            stream_order INTEGER,
            centroid_lon REAL,
            centroid_lat REAL
        )
    ''')
    
    # Park-river links (legacy)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS park_rivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            hyriv_id INTEGER NOT NULL,
            relation TEXT,
            distance_km REAL,
            UNIQUE(park_id, hyriv_id)
        )
    ''')
    
    # HydroRIVERS with geometry (new - 50km buffer)
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
    
    # HydroLAKES with geometry (new - 50km buffer)
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
    
    # Roads table (HeiGIT) - updated with more fields
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
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rivers_name ON rivers(name)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_park_rivers_park ON park_rivers(park_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rivers_hydro_park ON park_rivers_hydro(park_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rivers_hydro_order ON park_rivers_hydro(stream_order)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_lakes_hydro_park ON park_lakes_hydro(park_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_roads_heigit_park ON roads_heigit(park_id)')
    
    conn.commit()

def load_rivers(conn):
    """Load HydroRIVERS data"""
    rivers_dir = DATA_DIR / 'rivers'
    if not rivers_dir.exists():
        print("  No rivers directory found")
        return 0, 0
    
    river_count = 0
    link_count = 0
    
    for json_file in rivers_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                rivers = json.load(f)
            
            for r in rivers:
                # Insert river if not exists
                conn.execute('''
                    INSERT OR IGNORE INTO rivers 
                    (hyriv_id, name, length_km, discharge_cms, stream_order, centroid_lon, centroid_lat)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    r.get('hyriv_id'),
                    r.get('name'),
                    r.get('length_km'),
                    r.get('discharge_cms'),
                    r.get('stream_order'),
                    r.get('centroid', [None, None])[0],
                    r.get('centroid', [None, None])[1]
                ))
                river_count += 1
                
                # Link to park
                conn.execute('''
                    INSERT OR REPLACE INTO park_rivers (park_id, hyriv_id, relation, distance_km)
                    VALUES (?, ?, ?, ?)
                ''', (
                    park_id,
                    r.get('hyriv_id'),
                    r.get('relation'),
                    r.get('distance_km')
                ))
                link_count += 1
        except Exception as e:
            print(f"  Error loading rivers for {park_id}: {e}")
    
    conn.commit()
    return river_count, link_count

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
    """Load HeiGIT road data from processed directory (with geometry)"""
    # Prefer processed roads (with 50km buffer clipping and names)
    roads_dir = DATA_DIR / 'roads_processed'
    if not roads_dir.exists():
        # Fall back to original roads_heigit
        roads_dir = DATA_DIR / 'roads_heigit'
        if not roads_dir.exists():
            print("  No roads directory found")
            return 0
    
    # Clear existing data
    conn.execute("DELETE FROM roads_heigit")
    
    count = 0
    for json_file in sorted(roads_dir.glob('*.json')):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                data = json.load(f)
            
            # Handle both formats: list or {roads: [...]}
            roads = data.get('roads', data) if isinstance(data, dict) else data
            
            for r in roads:
                props = r.get('properties', r)
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
                    props.get('osm_id') or props.get('@id'),
                    props.get('name'),
                    props.get('highway') or props.get('highway_type'),
                    props.get('surface'),
                    props.get('smoothness'),
                    props.get('width'),
                    props.get('lanes'),
                    props.get('passability'),
                    props.get('length_km'),
                    json.dumps(geom) if geom else None,
                    props.get('osm_surface_class'),
                    props.get('dl_class_2024'),
                    props.get('dl_class_2020'),
                    props.get('surface_change'),
                    props.get('passability_code'),
                    props.get('passability_desc'),
                    props.get('passability_risk'),
                    props.get('rw_class')
                ))
                count += 1
        except Exception as e:
            print(f"  Error loading roads for {park_id}: {e}")
    
    conn.commit()
    return count

def load_osm_places(conn):
    """Load OSM places data - handles new nested format"""
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
                conn.execute('''
                    INSERT OR REPLACE INTO osm_places 
                    (park_id, place_type, name, lat, lon, osm_id, geojson)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    park_id,
                    p.get('place_type') or p.get('type'),
                    p.get('name'),
                    p.get('lat'),
                    p.get('lon'),
                    p.get('osm_id'),
                    json.dumps(p.get('geojson')) if p.get('geojson') else None
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
    """Load classified settlement events"""
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
                # Update park_settlements with classification
                conn.execute('''
                    UPDATE park_settlements 
                    SET classification = ?,
                        classification_confidence = ?,
                        narrative = ?
                    WHERE id = ?
                ''', (
                    s.get('classification'),
                    s.get('classification_confidence'),
                    s.get('narrative'),
                    s.get('id')
                ))
                if conn.total_changes:
                    count += 1
        except Exception as e:
            print(f"  Error loading settlements for {park_id}: {e}")
    
    conn.commit()
    return count

def load_deforestation_events(conn):
    """Load classified deforestation events"""
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
                # Update deforestation_events with classification
                conn.execute('''
                    UPDATE deforestation_events 
                    SET classification = ?,
                        classification_confidence = ?,
                        narrative = ?
                    WHERE id = ?
                ''', (
                    e.get('classification'),
                    e.get('classification_confidence'),
                    e.get('narrative'),
                    e.get('id')
                ))
                count += 1
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
    
    # Load rivers (legacy - point data)
    print("\n[1] Loading legacy HydroRIVERS data (point data)...")
    rivers, links = load_rivers(conn)
    print(f"    Loaded {rivers} river segments, {links} park-river links")
    
    # Load HydroRIVERS with geometry (new)
    print("\n[2] Loading HydroRIVERS with geometry (50km buffer)...")
    rivers_hydro = load_rivers_hydro(conn)
    print(f"    Loaded {rivers_hydro} river segments with geometry")
    
    # Load HydroLAKES with geometry (new)
    print("\n[3] Loading HydroLAKES with geometry (50km buffer)...")
    lakes_hydro = load_lakes_hydro(conn)
    print(f"    Loaded {lakes_hydro} lakes with geometry")
    
    # Load roads
    print("\n[4] Loading HeiGIT road data (with geometry)...")
    roads = load_roads_heigit(conn)
    print(f"    Loaded {roads} road segments")
    
    # Load OSM places
    print("\n[5] Loading OSM places...")
    places = load_osm_places(conn)
    print(f"    Loaded {places} places")
    
    # Load fire trajectories
    print("\n[6] Loading enhanced fire trajectories...")
    trajectories = load_fire_trajectories(conn)
    print(f"    Loaded {trajectories} trajectories")
    
    # Load settlement events
    print("\n[7] Updating settlement classifications...")
    settlements = load_settlement_events(conn)
    print(f"    Updated {settlements} settlement records")
    
    # Load deforestation events
    print("\n[8] Updating deforestation classifications...")
    deforest = load_deforestation_events(conn)
    print(f"    Updated {deforest} deforestation records")
    
    # Load fire narratives
    print("\n[9] Loading fire narratives to cache...")
    narratives = load_fire_narratives(conn)
    print(f"    Loaded {narratives} narrative caches")
    
    # Summary
    print("\n" + "=" * 60)
    print("Import complete! Summary:")
    cursor = conn.execute("SELECT COUNT(*) FROM rivers")
    print(f"  Rivers (legacy): {cursor.fetchone()[0]}")
    cursor = conn.execute("SELECT COUNT(*) FROM park_rivers")
    print(f"  Park-river links (legacy): {cursor.fetchone()[0]}")
    cursor = conn.execute("SELECT COUNT(*) FROM park_rivers_hydro")
    print(f"  Rivers with geometry (HydroRIVERS): {cursor.fetchone()[0]}")
    cursor = conn.execute("SELECT COUNT(*) FROM park_lakes_hydro")
    print(f"  Lakes with geometry (HydroLAKES): {cursor.fetchone()[0]}")
    cursor = conn.execute("SELECT COUNT(*) FROM roads_heigit")
    print(f"  Road segments (HeiGIT): {cursor.fetchone()[0]}")
    cursor = conn.execute("SELECT COUNT(*) FROM osm_places")
    print(f"  OSM places: {cursor.fetchone()[0]}")
    cursor = conn.execute("SELECT COUNT(*) FROM feature_geometries WHERE feature_type='fire_trajectory'")
    print(f"  Fire trajectories: {cursor.fetchone()[0]}")
    cursor = conn.execute("SELECT COUNT(*) FROM park_settlements WHERE classification IS NOT NULL")
    print(f"  Classified settlements: {cursor.fetchone()[0]}")
    cursor = conn.execute("SELECT COUNT(*) FROM deforestation_events WHERE classification IS NOT NULL")
    print(f"  Classified deforestation: {cursor.fetchone()[0]}")
    cursor = conn.execute("SELECT COUNT(*) FROM fire_narrative_cache")
    print(f"  Fire narrative caches: {cursor.fetchone()[0]}")
    print("=" * 60)
    
    conn.close()

if __name__ == '__main__':
    main()
