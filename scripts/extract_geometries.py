#!/usr/bin/env python3
"""Extract GeoJSON geometries from existing data for map display.

Extracts and stores GeoJSON in feature_geometries table:
- Fire trajectories from park_group_infractions.trajectories_json
- Roads from osm_roadless_data.roads_json  
- Settlement points (centroids for now)
- Deforestation event points

Usage:
    python scripts/extract_geometries.py --type fire_trajectory --park CAF_Chinko
    python scripts/extract_geometries.py --type fire_trajectory --all
    python scripts/extract_geometries.py --all-types --all
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"


def get_db_connection():
    """Get database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table_exists(conn):
    """Create feature_geometries table if not exists."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS feature_geometries (
            id INTEGER PRIMARY KEY,
            feature_type TEXT NOT NULL,
            feature_id TEXT NOT NULL,
            park_id TEXT NOT NULL,
            geojson TEXT NOT NULL,
            bbox_minx REAL,
            bbox_miny REAL,
            bbox_maxx REAL,
            bbox_maxy REAL,
            start_date TEXT,
            end_date TEXT,
            properties_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(feature_type, feature_id)
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_fg_park_type ON feature_geometries(park_id, feature_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_fg_dates ON feature_geometries(start_date, end_date)')
    conn.commit()


def extract_fire_trajectories(conn, park_id=None):
    """Extract fire trajectories as LineString GeoJSON."""
    print(f"Extracting fire trajectories...")
    
    query = '''
        SELECT park_id, year, trajectories_json
        FROM park_group_infractions
        WHERE trajectories_json IS NOT NULL
    '''
    params = []
    if park_id:
        query += ' AND park_id = ?'
        params.append(park_id)
    
    cursor = conn.execute(query, params)
    
    total_features = 0
    for row in cursor:
        park = row['park_id']
        year = row['year']
        
        try:
            trajectories = json.loads(row['trajectories_json'])
        except json.JSONDecodeError:
            print(f"  Warning: Invalid JSON for {park}/{year}")
            continue
        
        for i, traj in enumerate(trajectories):
            if 'path' not in traj or len(traj['path']) < 2:
                continue
            
            # Create LineString from path
            coords = [[p['lon'], p['lat']] for p in traj['path']]
            geojson = {
                'type': 'LineString',
                'coordinates': coords
            }
            
            # Calculate bounding box
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            
            feature_id = f"{park}_{year}_grp_{i+1}"
            
            properties = {
                'year': year,
                'group_num': i + 1,
                'outcome': traj.get('outcome', 'UNKNOWN'),
                'fires_inside': traj.get('fires_inside', 0),
                'days_inside': traj.get('days_inside', 0),
                'entry_date': traj.get('entry_date'),
                'last_inside': traj.get('last_inside')
            }
            
            try:
                conn.execute('''
                    INSERT OR REPLACE INTO feature_geometries
                    (feature_type, feature_id, park_id, geojson, 
                     bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                     start_date, end_date, properties_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    'fire_trajectory', feature_id, park,
                    json.dumps(geojson),
                    min(lons), min(lats), max(lons), max(lats),
                    traj.get('entry_date'), traj.get('last_inside'),
                    json.dumps(properties)
                ))
                total_features += 1
            except sqlite3.Error as e:
                print(f"  Error inserting {feature_id}: {e}")
        
        conn.commit()
        print(f"  {park}/{year}: {len(trajectories)} trajectories")
    
    print(f"Total fire trajectories extracted: {total_features}")
    return total_features


def extract_roads(conn, park_id=None):
    """Extract road segments as LineString GeoJSON."""
    print(f"Extracting road segments...")
    
    query = '''
        SELECT park_id, roads_json
        FROM osm_roadless_data
        WHERE roads_json IS NOT NULL
    '''
    params = []
    if park_id:
        query += ' AND park_id = ?'
        params.append(park_id)
    
    cursor = conn.execute(query, params)
    
    total_features = 0
    for row in cursor:
        park = row['park_id']
        
        try:
            roads = json.loads(row['roads_json'])
        except json.JSONDecodeError:
            print(f"  Warning: Invalid JSON for {park}")
            continue
        
        for i, road in enumerate(roads):
            if 'coords' not in road or len(road['coords']) < 2:
                continue
            
            # Coords are already [lon, lat] format
            coords = road['coords']
            geojson = {
                'type': 'LineString',
                'coordinates': coords
            }
            
            # Calculate bounding box
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            
            feature_id = f"{park}_road_{i+1}"
            
            properties = {
                'road_type': road.get('type', 'unknown'),
                'length_km': road.get('length_km', 0)
            }
            
            try:
                conn.execute('''
                    INSERT OR REPLACE INTO feature_geometries
                    (feature_type, feature_id, park_id, geojson,
                     bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                     properties_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    'road', feature_id, park,
                    json.dumps(geojson),
                    min(lons), min(lats), max(lons), max(lats),
                    json.dumps(properties)
                ))
                total_features += 1
            except sqlite3.Error as e:
                print(f"  Error inserting {feature_id}: {e}")
        
        conn.commit()
        print(f"  {park}: {len(roads)} road segments")
    
    print(f"Total road segments extracted: {total_features}")
    return total_features


def extract_settlements(conn, park_id=None):
    """Extract settlement centroids as Point GeoJSON."""
    print(f"Extracting settlement points...")
    
    query = '''
        SELECT id, park_id, lat, lon, area_m2, population_est, 
               nearest_place, distance_to_place_km, settlement_type
        FROM park_settlements
    '''
    params = []
    if park_id:
        query += ' WHERE park_id = ?'
        params.append(park_id)
    
    cursor = conn.execute(query, params)
    
    total_features = 0
    batch = []
    
    for row in cursor:
        geojson = {
            'type': 'Point',
            'coordinates': [row['lon'], row['lat']]
        }
        
        feature_id = f"settlement_{row['id']}"
        
        properties = {
            'area_m2': row['area_m2'],
            'population_est': row['population_est'],
            'nearest_place': row['nearest_place'],
            'distance_to_place_km': row['distance_to_place_km'],
            'settlement_type': row['settlement_type']
        }
        
        batch.append((
            'settlement', feature_id, row['park_id'],
            json.dumps(geojson),
            row['lon'], row['lat'], row['lon'], row['lat'],
            json.dumps(properties)
        ))
        
        if len(batch) >= 1000:
            conn.executemany('''
                INSERT OR REPLACE INTO feature_geometries
                (feature_type, feature_id, park_id, geojson,
                 bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                 properties_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', batch)
            conn.commit()
            total_features += len(batch)
            print(f"  Processed {total_features} settlements...")
            batch = []
    
    if batch:
        conn.executemany('''
            INSERT OR REPLACE INTO feature_geometries
            (feature_type, feature_id, park_id, geojson,
             bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
             properties_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', batch)
        conn.commit()
        total_features += len(batch)
    
    print(f"Total settlements extracted: {total_features}")
    return total_features


def extract_deforestation(conn, park_id=None):
    """Extract deforestation events as Point GeoJSON (with existing geojson if available)."""
    print(f"Extracting deforestation events...")
    
    query = '''
        SELECT id, park_id, year, area_km2, lat, lon, geojson, 
               pattern_type, description
        FROM deforestation_events
    '''
    params = []
    if park_id:
        query += ' WHERE park_id = ?'
        params.append(park_id)
    
    cursor = conn.execute(query, params)
    
    total_features = 0
    batch = []
    
    for row in cursor:
        # Use existing geojson if available, otherwise create Point
        if row['geojson']:
            try:
                geojson = json.loads(row['geojson'])
            except json.JSONDecodeError:
                geojson = {
                    'type': 'Point',
                    'coordinates': [row['lon'], row['lat']]
                }
        else:
            geojson = {
                'type': 'Point',
                'coordinates': [row['lon'], row['lat']]
            }
        
        feature_id = f"deforestation_{row['id']}"
        
        # Set date range (year-01-01 to year-12-31)
        start_date = f"{row['year']}-01-01"
        end_date = f"{row['year']}-12-31"
        
        properties = {
            'year': row['year'],
            'area_km2': row['area_km2'],
            'pattern_type': row['pattern_type'],
            'description': row['description']
        }
        
        batch.append((
            'deforestation', feature_id, row['park_id'],
            json.dumps(geojson),
            row['lon'], row['lat'], row['lon'], row['lat'],
            start_date, end_date,
            json.dumps(properties)
        ))
        
        if len(batch) >= 1000:
            conn.executemany('''
                INSERT OR REPLACE INTO feature_geometries
                (feature_type, feature_id, park_id, geojson,
                 bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                 start_date, end_date, properties_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', batch)
            conn.commit()
            total_features += len(batch)
            print(f"  Processed {total_features} deforestation events...")
            batch = []
    
    if batch:
        conn.executemany('''
            INSERT OR REPLACE INTO feature_geometries
            (feature_type, feature_id, park_id, geojson,
             bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
             start_date, end_date, properties_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', batch)
        conn.commit()
        total_features += len(batch)
    
    print(f"Total deforestation events extracted: {total_features}")
    return total_features


def main():
    parser = argparse.ArgumentParser(description='Extract GeoJSON geometries')
    parser.add_argument('--type', choices=['fire_trajectory', 'road', 'settlement', 'deforestation'],
                        help='Feature type to extract')
    parser.add_argument('--all-types', action='store_true', help='Extract all feature types')
    parser.add_argument('--park', help='Specific park ID')
    parser.add_argument('--all', action='store_true', help='Process all parks')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    
    args = parser.parse_args()
    
    if not args.type and not args.all_types:
        parser.error('Either --type or --all-types required')
    
    if not args.park and not args.all:
        parser.error('Either --park or --all required')
    
    park_id = args.park if args.park else None
    
    if args.dry_run:
        print(f"Would extract: type={args.type or 'all'}, park={park_id or 'all'}")
        return
    
    conn = get_db_connection()
    ensure_table_exists(conn)
    
    extractors = {
        'fire_trajectory': extract_fire_trajectories,
        'road': extract_roads,
        'settlement': extract_settlements,
        'deforestation': extract_deforestation
    }
    
    if args.all_types:
        for name, extractor in extractors.items():
            print(f"\n{'='*50}")
            print(f"Extracting {name}...")
            print('='*50)
            extractor(conn, park_id)
    else:
        extractors[args.type](conn, park_id)
    
    conn.close()
    print("\nDone!")


if __name__ == '__main__':
    main()
