#!/usr/bin/env python3
"""
Import HydroRIVERS data for African protected areas.
Memory-efficient streaming approach.
"""

import json
import sqlite3
import os
from pathlib import Path

try:
    import fiona
    from shapely.geometry import shape, mapping
    from shapely.prepared import prep
except ImportError:
    print("Installing dependencies...")
    os.system("pip install fiona shapely -q")
    import fiona
    from shapely.geometry import shape, mapping
    from shapely.prepared import prep

BASE_DIR = Path("/home/exedev/5mpglobe")
SHAPEFILE = BASE_DIR / "data/hydrorivers/HydroRIVERS_v10_af_shp/HydroRIVERS_v10_af.shp"
DB_PATH = BASE_DIR / "db.sqlite3"
OUTPUT_DIR = BASE_DIR / "data/rivers"
KEYSTONES_FILE = BASE_DIR / "data/keystones_with_boundaries.json"

BUFFER_DEG = 0.5  # ~50km

def create_tables(conn):
    conn.executescript("""
        DROP TABLE IF EXISTS rivers;
        DROP TABLE IF EXISTS park_rivers;
        
        CREATE TABLE rivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hyriv_id INTEGER UNIQUE,
            name TEXT,
            length_km REAL,
            discharge_cms REAL,
            stream_order INTEGER,
            geojson TEXT,
            centroid_lat REAL,
            centroid_lon REAL
        );
        CREATE INDEX idx_rivers_hyriv ON rivers(hyriv_id);
        CREATE INDEX idx_rivers_order ON rivers(stream_order);
        
        CREATE TABLE park_rivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            hyriv_id INTEGER NOT NULL,
            distance_km REAL,
            relation TEXT,
            UNIQUE(park_id, hyriv_id)
        );
        CREATE INDEX idx_park_rivers_park ON park_rivers(park_id);
    """)
    conn.commit()

def load_parks():
    with open(KEYSTONES_FILE) as f:
        keystones = json.load(f)
    
    parks = []
    for k in keystones:
        if k.get('geometry'):
            try:
                geom = shape(k['geometry'])
                buffered = geom.buffer(BUFFER_DEG)
                parks.append({
                    'id': k['id'],
                    'name': k['name'],
                    'geometry': geom,
                    'buffered': buffered,
                    'prepared': prep(buffered),
                    'bounds': buffered.bounds
                })
            except:
                pass
    return parks

def load_osm_rivers(conn):
    """Load OSM river names indexed by approximate location."""
    cursor = conn.execute("""
        SELECT name, lat, lon FROM osm_places 
        WHERE place_type IN ('river', 'stream', 'waterway') AND name != ''
    """)
    # Index by grid cell (0.1 degree ~ 10km)
    rivers = {}
    for name, lat, lon in cursor:
        key = (round(lat, 1), round(lon, 1))
        if key not in rivers:
            rivers[key] = []
        rivers[key].append({'name': name, 'lat': lat, 'lon': lon})
    return rivers

def find_river_name(osm_index, lat, lon):
    key = (round(lat, 1), round(lon, 1))
    if key in osm_index:
        for r in osm_index[key]:
            if abs(r['lat'] - lat) < 0.05 and abs(r['lon'] - lon) < 0.05:
                return r['name']
    return None

def main():
    print("HydroRIVERS Import")
    print("="*50)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    
    print("Creating tables...")
    create_tables(conn)
    
    print("Loading parks...")
    parks = load_parks()
    print(f"  {len(parks)} parks loaded")
    
    print("Loading OSM rivers...")
    osm_index = load_osm_rivers(conn)
    print(f"  {sum(len(v) for v in osm_index.values())} OSM rivers indexed")
    
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    # Track per-park rivers for JSON output
    park_rivers_data = {p['id']: [] for p in parks}
    imported = set()
    batch_rivers = []
    batch_park_rivers = []
    
    print("Processing shapefile...")
    with fiona.open(str(SHAPEFILE)) as src:
        total = len(src)
        for i, feature in enumerate(src):
            if i % 50000 == 0:
                print(f"  {i:,}/{total:,} ({100*i/total:.1f}%) - {len(imported)} rivers")
                # Batch insert
                if batch_rivers:
                    conn.executemany("""
                        INSERT OR IGNORE INTO rivers 
                        (hyriv_id, name, length_km, discharge_cms, stream_order, geojson, centroid_lat, centroid_lon)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, batch_rivers)
                    batch_rivers = []
                if batch_park_rivers:
                    conn.executemany("""
                        INSERT OR IGNORE INTO park_rivers (park_id, hyriv_id, distance_km, relation)
                        VALUES (?, ?, ?, ?)
                    """, batch_park_rivers)
                    batch_park_rivers = []
                conn.commit()
            
            props = feature['properties']
            stream_order = props.get('ORD_STRA') or 0
            discharge = props.get('DIS_AV_CMS') or 0
            length_km = props.get('LENGTH_KM') or 0
            
            # Filter: only significant rivers
            if stream_order < 3 and discharge < 1 and length_km < 5:
                continue
            
            hyriv_id = props['HYRIV_ID']
            
            try:
                geom = shape(feature['geometry'])
                if not geom.is_valid:
                    continue
                
                rbounds = geom.bounds
                centroid = geom.centroid
                clat, clon = centroid.y, centroid.x
                
                for park in parks:
                    pb = park['bounds']
                    # Quick bounds check
                    if rbounds[2] < pb[0] or rbounds[0] > pb[2] or rbounds[3] < pb[1] or rbounds[1] > pb[3]:
                        continue
                    
                    # Detailed check
                    if park['prepared'].intersects(geom):
                        # Determine relation
                        if geom.intersects(park['geometry']):
                            if park['geometry'].contains(geom):
                                relation = 'inside'
                                distance = 0
                            else:
                                relation = 'crosses'
                                distance = 0
                        else:
                            relation = 'nearby'
                            distance = round(geom.distance(park['geometry']) * 111, 1)
                        
                        name = find_river_name(osm_index, clat, clon)
                        
                        # Add to batch
                        if hyriv_id not in imported:
                            geojson_str = json.dumps(mapping(geom))
                            batch_rivers.append((hyriv_id, name, length_km, discharge, stream_order, geojson_str, clat, clon))
                            imported.add(hyriv_id)
                        
                        batch_park_rivers.append((park['id'], hyriv_id, distance, relation))
                        
                        # Store for JSON (simplified)
                        park_rivers_data[park['id']].append({
                            'hyriv_id': hyriv_id,
                            'name': name,
                            'length_km': length_km,
                            'discharge_cms': discharge,
                            'stream_order': stream_order,
                            'relation': relation,
                            'distance_km': distance,
                            'centroid': [clon, clat]
                        })
            except:
                continue
    
    # Final batch
    if batch_rivers:
        conn.executemany("""
            INSERT OR IGNORE INTO rivers 
            (hyriv_id, name, length_km, discharge_cms, stream_order, geojson, centroid_lat, centroid_lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, batch_rivers)
    if batch_park_rivers:
        conn.executemany("""
            INSERT OR IGNORE INTO park_rivers (park_id, hyriv_id, distance_km, relation)
            VALUES (?, ?, ?, ?)
        """, batch_park_rivers)
    conn.commit()
    
    # Write JSON files (without geometry to save space)
    print("\nWriting JSON files...")
    parks_with_rivers = 0
    for park_id, rivers in park_rivers_data.items():
        if rivers:
            parks_with_rivers += 1
            rivers.sort(key=lambda r: (-r['stream_order'], -r['discharge_cms']))
            with open(OUTPUT_DIR / f"{park_id}.json", 'w') as f:
                json.dump(rivers[:200], f)  # Top 200 rivers per park
    
    print(f"\n" + "="*50)
    print(f"Done: {len(imported)} rivers, {parks_with_rivers} parks")
    conn.close()

if __name__ == "__main__":
    main()
