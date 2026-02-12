#!/usr/bin/env python3
"""
Unified JSON to Database Import Script

Imports all JSON data files to database, ensuring exact match with source files.
Safe to run multiple times - uses INSERT OR REPLACE and cleans orphan records.

Usage:
    python3 scripts/import_json_to_db.py
"""

import json
import sqlite3
import os
import sys
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def import_feature_geometries(conn, feature_type, json_dir, id_extractor):
    """
    Import feature geometries from JSON files, ensuring exact match.
    Deletes records not in JSON, inserts/updates records from JSON.
    """
    if not json_dir.exists():
        log(f"  Skipping {feature_type}: directory not found")
        return 0
    
    # Collect all features from JSON
    json_features = {}
    for f in json_dir.glob('*.json'):
        park_id = f.stem
        with open(f) as fp:
            data = json.load(fp)
        
        items = data if isinstance(data, list) else data.get('features', [])
        for item in items:
            feature_id = id_extractor(park_id, item)
            if feature_id:
                json_features[feature_id] = (park_id, item)
    
    log(f"  {feature_type}: {len(json_features)} features in JSON")
    
    # Get existing feature_ids from DB
    cursor = conn.execute(
        "SELECT feature_id FROM feature_geometries WHERE feature_type = ?",
        (feature_type,)
    )
    db_ids = set(r[0] for r in cursor.fetchall())
    
    # Delete orphans (in DB but not in JSON)
    orphans = db_ids - set(json_features.keys())
    if orphans:
        log(f"  Deleting {len(orphans)} orphan records...")
        for i in range(0, len(orphans), 500):
            batch = list(orphans)[i:i+500]
            placeholders = ','.join('?' * len(batch))
            conn.execute(
                f"DELETE FROM feature_geometries WHERE feature_type = ? AND feature_id IN ({placeholders})",
                [feature_type] + batch
            )
        conn.commit()
    
    # Insert/update features
    new_ids = set(json_features.keys()) - db_ids
    if new_ids:
        log(f"  Inserting {len(new_ids)} new records...")
    
    for feature_id, (park_id, item) in json_features.items():
        if feature_id not in new_ids:
            continue
        
        geojson = item.get('geojson') or item.get('geometry')
        bbox = item.get('bbox', [0, 0, 0, 0])
        if len(bbox) < 4:
            bbox = [0, 0, 0, 0]
        
        properties = item.get('properties', {})
        
        conn.execute('''
            INSERT OR REPLACE INTO feature_geometries
            (feature_type, feature_id, park_id, geojson,
             bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
             start_date, end_date, properties_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            feature_type,
            feature_id,
            park_id,
            json.dumps(geojson) if geojson else None,
            bbox[0], bbox[1], bbox[2], bbox[3],
            item.get('start_date'),
            item.get('end_date'),
            json.dumps(properties) if properties else None
        ))
    
    conn.commit()
    
    # Verify count
    cursor = conn.execute(
        "SELECT COUNT(*) FROM feature_geometries WHERE feature_type = ?",
        (feature_type,)
    )
    db_count = cursor.fetchone()[0]
    
    if db_count != len(json_features):
        log(f"  ⚠ MISMATCH: DB={db_count}, JSON={len(json_features)}")
    else:
        log(f"  ✓ Verified: {db_count} records")
    
    return len(json_features)


def import_fire_trajectories(conn):
    """Import fire trajectories with proper feature_id extraction."""
    log("\n=== Fire Trajectories ===")
    
    def id_extractor(park_id, item):
        return item.get('feature_id')
    
    return import_feature_geometries(
        conn, 'fire_trajectory',
        DATA_DIR / 'fire_trajectories',
        id_extractor
    )


def import_settlements(conn):
    """Import settlement polygons."""
    log("\n=== Settlement Polygons ===")
    
    def id_extractor(park_id, item):
        return item.get('feature_id')
    
    return import_feature_geometries(
        conn, 'settlement',
        DATA_DIR / 'feature_geometries' / 'settlement',
        id_extractor
    )


def import_deforestation(conn):
    """Import deforestation polygons."""
    log("\n=== Deforestation Polygons ===")
    
    def id_extractor(park_id, item):
        return item.get('feature_id')
    
    return import_feature_geometries(
        conn, 'deforestation',
        DATA_DIR / 'feature_geometries' / 'deforestation',
        id_extractor
    )


def import_roads(conn):
    """Import roads from HeiGIT data."""
    log("\n=== Roads (HeiGIT) ===")
    
    roads_dir = DATA_DIR / 'roads_heigit'
    if not roads_dir.exists():
        log("  Skipping: directory not found")
        return 0
    
    # Collect all roads from JSON
    json_roads = {}
    for f in roads_dir.glob('*.json'):
        park_id = f.stem
        with open(f) as fp:
            roads = json.load(fp)
        
        for road in roads:
            osm_id = road.get('osm_id')
            if osm_id:
                feature_id = f"{park_id}_{osm_id}"
                json_roads[feature_id] = (park_id, road)
    
    log(f"  roads: {len(json_roads)} features in JSON")
    
    # Get existing
    cursor = conn.execute(
        "SELECT feature_id FROM feature_geometries WHERE feature_type = 'road'"
    )
    db_ids = set(r[0] for r in cursor.fetchall())
    
    # Delete orphans
    orphans = db_ids - set(json_roads.keys())
    if orphans:
        log(f"  Deleting {len(orphans)} orphan records...")
        for i in range(0, len(orphans), 500):
            batch = list(orphans)[i:i+500]
            placeholders = ','.join('?' * len(batch))
            conn.execute(
                f"DELETE FROM feature_geometries WHERE feature_type = 'road' AND feature_id IN ({placeholders})",
                batch
            )
        conn.commit()
    
    # Insert new
    new_ids = set(json_roads.keys()) - db_ids
    if new_ids:
        log(f"  Inserting {len(new_ids)} new records...")
    
    for feature_id, (park_id, road) in json_roads.items():
        if feature_id not in new_ids:
            continue
        
        geojson = road.get('geometry')
        
        # Calculate bbox from LineString
        bbox = [0, 0, 0, 0]
        if geojson and geojson.get('type') == 'LineString':
            coords = geojson.get('coordinates', [])
            if coords:
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                bbox = [min(lons), min(lats), max(lons), max(lats)]
        
        properties = {
            'osm_id': road.get('osm_id'),
            'highway': road.get('highway'),
            'surface': road.get('surface'),
            'osm_surface_class': road.get('osm_surface_class'),
            'dl_class_2024': road.get('dl_class_2024'),
            'passability_code': road.get('passability_code'),
            'passability_desc': road.get('passability_desc'),
            'passability_risk': road.get('passability_risk'),
        }
        
        conn.execute('''
            INSERT OR REPLACE INTO feature_geometries
            (feature_type, feature_id, park_id, geojson,
             bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, properties_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            'road',
            feature_id,
            park_id,
            json.dumps(geojson),
            bbox[0], bbox[1], bbox[2], bbox[3],
            json.dumps(properties)
        ))
    
    conn.commit()
    
    # Verify
    cursor = conn.execute("SELECT COUNT(*) FROM feature_geometries WHERE feature_type = 'road'")
    db_count = cursor.fetchone()[0]
    
    if db_count != len(json_roads):
        log(f"  ⚠ MISMATCH: DB={db_count}, JSON={len(json_roads)}")
    else:
        log(f"  ✓ Verified: {db_count} records")
    
    return len(json_roads)


def import_fire_analysis(conn):
    """Import fire analysis data."""
    log("\n=== Fire Analysis ===")
    
    fire_dir = DATA_DIR / 'fire_analysis'
    if not fire_dir.exists():
        log("  Skipping: directory not found")
        return 0
    
    # Get existing
    cursor = conn.execute("SELECT park_id, year FROM park_fire_analysis")
    existing = set((r[0], r[1]) for r in cursor.fetchall())
    
    imported = 0
    total = 0
    for f in fire_dir.glob('*.json'):
        with open(f) as fp:
            data = json.load(fp)
        
        park_id = data.get('park_id', f.stem)
        for yr in data.get('years', []):
            total += 1
            key = (park_id, yr['year'])
            if key in existing:
                continue
            
            conn.execute('''
                INSERT OR REPLACE INTO park_fire_analysis
                (park_id, year, total_fires, dry_season_fires, transhumance_groups,
                 transhumance_fires, avg_transhumance_speed, herder_groups,
                 management_groups, village_groups, peak_month, analysis_json, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                park_id,
                yr['year'],
                yr.get('total_fires', 0),
                yr.get('dry_season_fires', 0),
                yr.get('transhumance_groups', 0),
                yr.get('transhumance_fires', 0),
                yr.get('avg_transhumance_speed', 0),
                yr.get('herder_groups', 0),
                yr.get('management_groups', 0),
                yr.get('village_groups', 0),
                yr.get('peak_month', 0),
                json.dumps(yr.get('analysis', {})),
                yr.get('analyzed_at')
            ))
            imported += 1
    
    conn.commit()
    
    cursor = conn.execute("SELECT COUNT(DISTINCT park_id), COUNT(*) FROM park_fire_analysis")
    parks, records = cursor.fetchone()
    log(f"  ✓ {records} records for {parks} parks (imported {imported} new)")
    
    return total


def import_osm_places(conn):
    """Import OSM places."""
    log("\n=== OSM Places ===")
    
    osm_dir = DATA_DIR / 'osm_places'
    if not osm_dir.exists():
        log("  Skipping: directory not found")
        return 0
    
    cursor = conn.execute("SELECT COUNT(*) FROM osm_places")
    existing = cursor.fetchone()[0]
    
    if existing > 50000:
        log(f"  Already populated ({existing} records), skipping")
        return existing
    
    imported = 0
    for f in osm_dir.glob('*.json'):
        park_id = f.stem
        with open(f) as fp:
            places = json.load(fp)
        
        for p in places:
            conn.execute('''
                INSERT OR REPLACE INTO osm_places
                (park_id, osm_id, name, place_type, lat, lon, population, is_inside, distance_km, direction)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                park_id,
                str(p.get('osm_id', '')),
                p.get('name', ''),
                p.get('type', ''),
                p.get('lat'),
                p.get('lon'),
                p.get('population'),
                1 if p.get('is_inside', True) else 0,
                p.get('distance_km'),
                p.get('direction')
            ))
            imported += 1
    
    conn.commit()
    
    cursor = conn.execute("SELECT COUNT(DISTINCT park_id), COUNT(*) FROM osm_places")
    parks, records = cursor.fetchone()
    log(f"  ✓ {records} records for {parks} parks")
    
    return imported


def import_climate(conn):
    """Import climate data."""
    log("\n=== Climate Data ===")
    
    climate_file = DATA_DIR / 'climate' / 'park_climate.json'
    if not climate_file.exists():
        log("  Skipping: file not found")
        return 0
    
    cursor = conn.execute("SELECT COUNT(*) FROM park_climate")
    existing = cursor.fetchone()[0]
    
    if existing >= 160:
        log(f"  Already populated ({existing} records), skipping")
        return existing
    
    with open(climate_file) as f:
        data = json.load(f)
    
    for park_id, climate in data.items():
        monthly = climate.get('monthly', {})
        conn.execute('''
            INSERT OR REPLACE INTO park_climate
            (park_id, annual_precip_mm, precip_jan, precip_feb, precip_mar,
             precip_apr, precip_may, precip_jun, precip_jul, precip_aug,
             precip_sep, precip_oct, precip_nov, precip_dec,
             dry_season_start, dry_season_end, wet_season_start, wet_season_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            park_id,
            climate.get('annual_precip_mm', 0),
            monthly.get('1', 0), monthly.get('2', 0), monthly.get('3', 0),
            monthly.get('4', 0), monthly.get('5', 0), monthly.get('6', 0),
            monthly.get('7', 0), monthly.get('8', 0), monthly.get('9', 0),
            monthly.get('10', 0), monthly.get('11', 0), monthly.get('12', 0),
            climate.get('dry_season_start'),
            climate.get('dry_season_end'),
            climate.get('wet_season_start'),
            climate.get('wet_season_end')
        ))
    
    conn.commit()
    
    cursor = conn.execute("SELECT COUNT(*) FROM park_climate")
    log(f"  ✓ {cursor.fetchone()[0]} records")
    
    return len(data)


def import_waterbodies(conn):
    """Import waterbodies."""
    log("\n=== Waterbodies ===")
    
    wb_dir = DATA_DIR / 'waterbodies'
    if not wb_dir.exists():
        log("  Skipping: directory not found")
        return 0
    
    cursor = conn.execute("SELECT COUNT(*) FROM park_waterbodies")
    existing = cursor.fetchone()[0]
    
    if existing > 2000:
        log(f"  Already populated ({existing} records), skipping")
        return existing
    
    imported = 0
    for f in wb_dir.glob('*.json'):
        park_id = f.stem
        with open(f) as fp:
            wbs = json.load(fp)
        
        for wb in wbs:
            conn.execute('''
                INSERT OR REPLACE INTO park_waterbodies
                (park_id, wb_id, name, wb_type, area_km2, perimeter_km,
                 elevation_m, is_inside, geojson)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                park_id,
                wb.get('wb_id', ''),
                wb.get('name', ''),
                wb.get('type', ''),
                wb.get('area_km2'),
                wb.get('perimeter_km'),
                wb.get('elevation_m'),
                1 if wb.get('is_inside', True) else 0,
                json.dumps(wb.get('geojson')) if wb.get('geojson') else None
            ))
            imported += 1
    
    conn.commit()
    
    cursor = conn.execute("SELECT COUNT(DISTINCT park_id), COUNT(*) FROM park_waterbodies")
    parks, records = cursor.fetchone()
    log(f"  ✓ {records} records for {parks} parks")
    
    return imported


def print_summary(conn):
    """Print final database summary."""
    log("\n" + "="*50)
    log("DATABASE SUMMARY")
    log("="*50)
    
    # Feature geometries
    cursor = conn.execute("""
        SELECT feature_type, COUNT(*) 
        FROM feature_geometries 
        GROUP BY feature_type 
        ORDER BY COUNT(*) DESC
    """)
    log("\nfeature_geometries:")
    for row in cursor:
        log(f"  {row[0]}: {row[1]:,}")
    
    # Other tables
    tables = [
        ('park_fire_analysis', 'park_id'),
        ('park_rivers', 'park_id'),
        ('osm_places', 'park_id'),
        ('park_climate', 'park_id'),
        ('park_waterbodies', 'park_id'),
        ('park_species', 'park_id'),
        ('fire_narrative_cache', None),
        ('fire_detections', None),
    ]
    
    log("\nOther tables:")
    for table, park_col in tables:
        try:
            if park_col:
                cursor = conn.execute(f"SELECT COUNT(DISTINCT {park_col}), COUNT(*) FROM {table}")
                parks, total = cursor.fetchone()
                log(f"  {table}: {total:,} ({parks} parks)")
            else:
                cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                log(f"  {table}: {cursor.fetchone()[0]:,}")
        except Exception as e:
            log(f"  {table}: error - {e}")



def import_group_infractions(conn):
    """Import park_group_infractions from fire_trajectories JSON files.
    
    Computes outcome (STOPPED_INSIDE vs TRANSITED) based on whether
    the trajectory's last point is inside the park boundary.
    """
    log("\n=== Park Group Infractions ===")
    
    traj_dir = DATA_DIR / 'fire_trajectories'
    keystones_file = DATA_DIR / 'keystones_with_boundaries.json'
    
    if not traj_dir.exists():
        log("  Skipping: fire_trajectories directory not found")
        return 0
    
    # Load park boundaries
    park_bounds = {}
    if keystones_file.exists():
        with open(keystones_file) as f:
            for park in json.load(f):
                pid = park.get('id')
                geom = park.get('geometry')
                if pid and geom:
                    park_bounds[pid] = geom
    
    # Try to use shapely for boundary checks
    try:
        from shapely.geometry import shape, Point
        HAS_SHAPELY = True
    except ImportError:
        HAS_SHAPELY = False
        log("  Warning: shapely not available, assuming all points inside")
    
    def point_in_park(lat, lon, park_id):
        if not HAS_SHAPELY or park_id not in park_bounds:
            return True
        try:
            geom = shape(park_bounds[park_id])
            return geom.contains(Point(lon, lat))
        except:
            return True
    
    def get_outcome(traj, park_id):
        coords = traj.get('coordinates', [])
        if not coords:
            cwt = traj.get('coordinates_with_time', [])
            if cwt:
                last = cwt[-1]
                lat, lon = last.get('lat'), last.get('lon')
            else:
                return 'UNKNOWN'
        else:
            last = coords[-1]
            lon, lat = last[0], last[1]
        
        if point_in_park(lat, lon, park_id):
            return 'STOPPED_INSIDE'
        return 'TRANSITED'
    
    # Clear existing and rebuild
    conn.execute("DELETE FROM park_group_infractions")
    
    # Process all trajectory files
    from collections import defaultdict
    stats = defaultdict(lambda: defaultdict(lambda: {
        'total': 0, 'stopped': 0, 'transited': 0, 'fires': 0, 'days_sum': 0
    }))
    
    file_count = 0
    for traj_file in sorted(traj_dir.glob('*.json')):
        park_id = traj_file.stem
        file_count += 1
        
        with open(traj_file) as f:
            trajectories = json.load(f)
        
        for traj in trajectories:
            year = traj.get('year')
            if not year:
                continue
            
            outcome = get_outcome(traj, park_id)
            s = stats[park_id][year]
            s['total'] += 1
            s['fires'] += traj.get('fires_total', 0)
            s['days_sum'] += traj.get('days', 0)
            
            if outcome == 'STOPPED_INSIDE':
                s['stopped'] += 1
            elif outcome == 'TRANSITED':
                s['transited'] += 1
    
    # Insert into database
    inserted = 0
    for park_id, years in stats.items():
        for year, data in years.items():
            avg_days = data['days_sum'] / data['total'] if data['total'] > 0 else 0
            conn.execute("""
                INSERT INTO park_group_infractions 
                (park_id, year, total_groups, groups_stopped_inside, groups_transited, 
                 total_fires_inside, avg_days_burning)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (park_id, year, data['total'], data['stopped'], data['transited'], 
                  data['fires'], avg_days))
            inserted += 1
    
    conn.commit()
    
    # Verify
    cursor = conn.execute("SELECT COUNT(DISTINCT park_id), COUNT(*), SUM(total_groups) FROM park_group_infractions")
    parks, records, total_groups = cursor.fetchone()
    log(f"  ✓ {records} records for {parks} parks ({total_groups:,} total groups)")
    
    return inserted


def main():
    log("Starting JSON to Database Import")
    log(f"Database: {DB_PATH}")
    log(f"Data directory: {DATA_DIR}")
    
    if not DB_PATH.exists():
        log("ERROR: Database not found!")
        sys.exit(1)
    
    conn = sqlite3.connect(DB_PATH)
    
    try:
        # Feature geometries (exact sync)
        import_fire_trajectories(conn)
        import_settlements(conn)
        import_deforestation(conn)
        import_roads(conn)
        
        # Other tables (additive)
        import_fire_analysis(conn)
        import_group_infractions(conn)
        import_osm_places(conn)
        import_climate(conn)
        import_waterbodies(conn)
        
        # Summary
        print_summary(conn)
        
    finally:
        conn.close()
    
    log("\n✓ Import complete")


if __name__ == '__main__':
    main()
