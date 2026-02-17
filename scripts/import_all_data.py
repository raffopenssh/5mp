#!/usr/bin/env python3
"""
Import all data from JSON/CSV files to database tables.
Processes sequentially and removes files after import to save space.
"""

import json
import sqlite3
import gzip
import csv
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
DATA_DIR = BASE_DIR / "data"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def create_tables(conn):
    """Create missing tables."""
    
    # park_climate
    conn.execute('''CREATE TABLE IF NOT EXISTS park_climate (
        park_id TEXT PRIMARY KEY,
        temp_annual_c REAL,
        temp_max_c REAL,
        temp_min_c REAL,
        precip_annual_mm REAL,
        precip_wettest_mm REAL,
        precip_driest_mm REAL,
        climate_zone TEXT,
        rainy_season TEXT,
        dry_season TEXT
    )''')
    
    # park_species
    conn.execute('''CREATE TABLE IF NOT EXISTS park_species (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        park_id TEXT NOT NULL,
        binomial TEXT NOT NULL,
        common_name TEXT,
        status TEXT,
        species_order TEXT,
        family TEXT,
        UNIQUE(park_id, binomial)
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ps_park ON park_species(park_id)')
    
    # park_rivers_hydro
    conn.execute('''CREATE TABLE IF NOT EXISTS park_rivers_hydro (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        park_id TEXT NOT NULL,
        hyriv_id INTEGER NOT NULL,
        name TEXT,
        stream_order INTEGER,
        ord_flow INTEGER,
        length_km REAL,
        lat REAL,
        lon REAL,
        geojson TEXT,
        UNIQUE(park_id, hyriv_id)
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_prh_park ON park_rivers_hydro(park_id)')
    
    # park_lakes_hydro
    conn.execute('''CREATE TABLE IF NOT EXISTS park_lakes_hydro (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        park_id TEXT NOT NULL,
        hylak_id INTEGER NOT NULL,
        name TEXT,
        lake_type INTEGER,
        area_km2 REAL,
        centroid_lon REAL,
        centroid_lat REAL,
        geojson TEXT,
        UNIQUE(park_id, hylak_id)
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_plh_park ON park_lakes_hydro(park_id)')
    
    # roads_heigit
    conn.execute('''CREATE TABLE IF NOT EXISTS roads_heigit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        park_id TEXT NOT NULL,
        osm_id TEXT,
        name TEXT,
        highway_type TEXT,
        surface TEXT,
        length_km REAL,
        geojson TEXT,
        dl_class_2024 TEXT,
        passability TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rh_park ON roads_heigit(park_id)')
    
    # osm_places
    conn.execute('''CREATE TABLE IF NOT EXISTS osm_places (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        park_id TEXT NOT NULL,
        place_type TEXT NOT NULL,
        name TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        osm_id TEXT,
        osm_tags TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_op_park ON osm_places(park_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_op_type ON osm_places(place_type)')
    
    # deforestation_events
    conn.execute('''CREATE TABLE IF NOT EXISTS deforestation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        park_id TEXT NOT NULL,
        year INTEGER NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        area_km2 REAL,
        pattern_type TEXT,
        classification TEXT,
        narrative TEXT,
        polygon_ids TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_de_park ON deforestation_events(park_id)')
    
    # fire_narrative_cache
    conn.execute('''CREATE TABLE IF NOT EXISTS fire_narrative_cache (
        park_id TEXT PRIMARY KEY,
        narrative_json TEXT,
        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        from_year INTEGER,
        to_year INTEGER
    )''')
    
    # legal_documents
    conn.execute('''CREATE TABLE IF NOT EXISTS legal_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        park_id TEXT,
        country_iso TEXT,
        faolex_id TEXT UNIQUE,
        title TEXT,
        title_of_text TEXT,
        date_of_text TEXT,
        type_of_text TEXT,
        subject TEXT,
        keyword TEXT,
        abstract TEXT,
        link TEXT,
        relevance_score REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    conn.commit()
    log("Tables created/verified")

def import_climate(conn):
    """Import park_climate from CSV."""
    csv_file = DATA_DIR / "climate" / "park_climate.csv.gz"
    if not csv_file.exists():
        log("Climate CSV not found, skipping")
        return
    
    log("Importing climate data...")
    conn.execute("DELETE FROM park_climate")
    
    with gzip.open(csv_file, 'rt') as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            conn.execute('''INSERT OR REPLACE INTO park_climate 
                (park_id, temp_annual_c, temp_max_c, temp_min_c, precip_annual_mm,
                 precip_wettest_mm, precip_driest_mm, climate_zone, rainy_season, dry_season)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (row['park_id'],
                 float(row['temp_annual_c']) if row['temp_annual_c'] else None,
                 float(row['temp_max_c']) if row['temp_max_c'] else None,
                 float(row['temp_min_c']) if row['temp_min_c'] else None,
                 float(row['precip_annual_mm']) if row['precip_annual_mm'] else None,
                 float(row['precip_wettest_mm']) if row['precip_wettest_mm'] else None,
                 float(row['precip_driest_mm']) if row['precip_driest_mm'] else None,
                 row['climate_zone'] or None,
                 row['rainy_season'] or None,
                 row['dry_season'] or None))
            count += 1
    conn.commit()
    log(f"  Imported {count} climate records")

def import_species(conn):
    """Import park_species from CSV."""
    csv_file = DATA_DIR / "species" / "park_species.csv.gz"
    if not csv_file.exists():
        log("Species CSV not found, skipping")
        return
    
    log("Importing species data...")
    conn.execute("DELETE FROM park_species")
    
    with gzip.open(csv_file, 'rt') as f:
        reader = csv.DictReader(f)
        count = 0
        batch = []
        for row in reader:
            batch.append((
                row['park_id'], row['binomial'], row.get('common_name', ''),
                row.get('status', ''), row.get('species_order', ''), row.get('family', '')
            ))
            if len(batch) >= 1000:
                conn.executemany('''INSERT OR REPLACE INTO park_species 
                    (park_id, binomial, common_name, status, species_order, family)
                    VALUES (?, ?, ?, ?, ?, ?)''', batch)
                count += len(batch)
                batch = []
        if batch:
            conn.executemany('''INSERT OR REPLACE INTO park_species 
                (park_id, binomial, common_name, status, species_order, family)
                VALUES (?, ?, ?, ?, ?, ?)''', batch)
            count += len(batch)
    conn.commit()
    log(f"  Imported {count} species records")

def import_rivers_hydro(conn, delete_after=False):
    """Import park_rivers_hydro from JSON files."""
    river_dir = DATA_DIR / "rivers_hydro"
    if not river_dir.exists():
        log("rivers_hydro directory not found, skipping")
        return
    
    log("Importing rivers_hydro data...")
    conn.execute("DELETE FROM park_rivers_hydro")
    
    count = 0
    for json_file in sorted(river_dir.glob("*.json")):
        with open(json_file) as f:
            data = json.load(f)
        
        park_id = data.get('park_id', json_file.stem)
        rivers = data.get('rivers', [])
        
        batch = []
        for r in rivers:
            geom = r.get('geometry', {})
            coords = geom.get('coordinates', [[0,0]])
            # Get centroid (middle point of line)
            lat, lon = 0, 0
            if coords and len(coords) > 0:
                mid = coords[len(coords)//2]
                # Handle MultiLineString (list of lists of coords)
                while isinstance(mid, list) and len(mid) > 0 and isinstance(mid[0], list):
                    mid = mid[0]
                if isinstance(mid, list) and len(mid) >= 2:
                    lon, lat = float(mid[0]), float(mid[1])
            
            batch.append((
                park_id, r.get('hyriv_id'), r.get('name'),
                r.get('stream_order'), r.get('ord_flow'), r.get('length_km'),
                lat, lon,
                json.dumps(geom) if geom else None
            ))
        
        if batch:
            conn.executemany('''INSERT OR REPLACE INTO park_rivers_hydro 
                (park_id, hyriv_id, name, stream_order, ord_flow, length_km, lat, lon, geojson)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', batch)
            count += len(batch)
        
        if delete_after:
            json_file.unlink()
    
    conn.commit()
    log(f"  Imported {count} river records")
    if delete_after:
        log("  Deleted JSON files")

def import_lakes_hydro(conn, delete_after=False):
    """Import park_lakes_hydro from JSON files."""
    lake_dir = DATA_DIR / "lakes_hydro"
    if not lake_dir.exists():
        log("lakes_hydro directory not found, skipping")
        return
    
    log("Importing lakes_hydro data...")
    conn.execute("DELETE FROM park_lakes_hydro")
    
    count = 0
    for json_file in sorted(lake_dir.glob("*.json")):
        with open(json_file) as f:
            data = json.load(f)
        
        park_id = data.get('park_id', json_file.stem)
        lakes = data.get('lakes', [])
        
        batch = []
        for l in lakes:
            geom = l.get('geometry', {})
            batch.append((
                park_id, l.get('hylak_id'), l.get('name'),
                l.get('lake_type'), l.get('area_km2'),
                l.get('centroid_lon', l.get('lon')), l.get('centroid_lat', l.get('lat')),
                json.dumps(geom) if geom else None
            ))
        
        if batch:
            conn.executemany('''INSERT OR REPLACE INTO park_lakes_hydro 
                (park_id, hylak_id, name, lake_type, area_km2, centroid_lon, centroid_lat, geojson)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', batch)
            count += len(batch)
        
        if delete_after:
            json_file.unlink()
    
    conn.commit()
    log(f"  Imported {count} lake records")

def import_roads_heigit(conn, delete_after=False):
    """Import roads_heigit from JSON files."""
    road_dir = DATA_DIR / "roads_heigit"
    if not road_dir.exists():
        log("roads_heigit directory not found, skipping")
        return
    
    log("Importing roads_heigit data...")
    conn.execute("DELETE FROM roads_heigit")
    
    count = 0
    for json_file in sorted(road_dir.glob("*.json")):
        with open(json_file) as f:
            data = json.load(f)
        
        park_id = data.get('park_id', json_file.stem)
        roads = data.get('roads', data.get('features', []))
        
        batch = []
        for r in roads:
            props = r.get('properties', r)
            geom = r.get('geometry', {})
            batch.append((
                park_id, props.get('osm_id'), props.get('name'),
                props.get('highway_type', props.get('highway')),
                props.get('surface'), props.get('length_km', props.get('osm_length')),
                json.dumps(geom) if geom else None,
                props.get('dl_class_2024'), props.get('passability', props.get('passability_desc'))
            ))
        
        if batch:
            conn.executemany('''INSERT INTO roads_heigit 
                (park_id, osm_id, name, highway_type, surface, length_km, geojson, dl_class_2024, passability)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', batch)
            count += len(batch)
        
        if delete_after:
            json_file.unlink()
    
    conn.commit()
    log(f"  Imported {count} road records")

def import_osm_places(conn, delete_after=False):
    """Import osm_places from JSON files."""
    osm_dir = DATA_DIR / "osm_places"
    if not osm_dir.exists():
        log("osm_places directory not found, skipping")
        return
    
    log("Importing osm_places data...")
    conn.execute("DELETE FROM osm_places")
    
    count = 0
    for json_file in sorted(osm_dir.glob("*.json")):
        with open(json_file) as f:
            data = json.load(f)
        
        park_id = data.get('park_id', json_file.stem)
        places = data.get('places', [])
        
        batch = []
        for p in places:
            batch.append((
                park_id, p.get('place_type', 'unknown'), p.get('name', ''),
                p.get('lat'), p.get('lon'), p.get('osm_id'),
                json.dumps(p.get('osm_tags')) if p.get('osm_tags') else None
            ))
        
        if batch:
            conn.executemany('''INSERT INTO osm_places 
                (park_id, place_type, name, lat, lon, osm_id, osm_tags)
                VALUES (?, ?, ?, ?, ?, ?, ?)''', batch)
            count += len(batch)
        
        if delete_after:
            json_file.unlink()
    
    conn.commit()
    log(f"  Imported {count} place records")

def import_deforestation(conn, delete_after=False):
    """Import deforestation_events from JSON files."""
    defo_dir = DATA_DIR / "deforestation_events"
    if not defo_dir.exists():
        log("deforestation_events directory not found, skipping")
        return
    
    log("Importing deforestation_events data...")
    conn.execute("DELETE FROM deforestation_events")
    
    count = 0
    for json_file in sorted(defo_dir.glob("*.json")):
        with open(json_file) as f:
            events = json.load(f)
        
        park_id = json_file.stem
        
        batch = []
        for e in events:
            batch.append((
                park_id, e.get('year'), e.get('lat'), e.get('lon'),
                e.get('area_km2'), e.get('pattern_type'),
                e.get('classification'), e.get('narrative'),
                json.dumps(e.get('polygon_ids')) if e.get('polygon_ids') else None
            ))
        
        if batch:
            conn.executemany('''INSERT INTO deforestation_events 
                (park_id, year, lat, lon, area_km2, pattern_type, classification, narrative, polygon_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', batch)
            count += len(batch)
        
        if delete_after:
            json_file.unlink()
    
    conn.commit()
    log(f"  Imported {count} deforestation events")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--delete-json', action='store_true', help='Delete JSON files after import')
    parser.add_argument('--table', help='Import only specific table')
    args = parser.parse_args()
    
    log("=" * 60)
    log("IMPORT ALL DATA TO DATABASE")
    log("=" * 60)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    
    create_tables(conn)
    
    if args.table:
        tables = [args.table]
    else:
        tables = ['climate', 'species', 'rivers_hydro', 'lakes_hydro', 'roads_heigit', 'osm_places', 'deforestation']
    
    for t in tables:
        if t == 'climate':
            import_climate(conn)
        elif t == 'species':
            import_species(conn)
        elif t == 'rivers_hydro':
            import_rivers_hydro(conn, args.delete_json)
        elif t == 'lakes_hydro':
            import_lakes_hydro(conn, args.delete_json)
        elif t == 'roads_heigit':
            import_roads_heigit(conn, args.delete_json)
        elif t == 'osm_places':
            import_osm_places(conn, args.delete_json)
        elif t == 'deforestation':
            import_deforestation(conn, args.delete_json)
    
    conn.close()
    log("\nIMPORT COMPLETE")

if __name__ == '__main__':
    main()
