#!/usr/bin/env python3
"""
Fast JSON to Database Import Script
Imports all JSON data with batch operations. Runs in ~2-3 minutes.

Usage:
    python scripts/import_json_to_db.py                    # Run all imports
    python scripts/import_json_to_db.py rivers roads       # Run only rivers and roads
    python scripts/import_json_to_db.py --list             # List available imports
    python scripts/import_json_to_db.py --skip rivers      # Skip rivers
"""

import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def batch_insert(conn, table, columns, rows, on_conflict='IGNORE'):
    if not rows: return 0
    placeholders = ','.join(['?' for _ in columns])
    cols = ','.join(columns)
    conn.executemany(f"INSERT OR {on_conflict} INTO {table} ({cols}) VALUES ({placeholders})", rows)
    return len(rows)

def safe_bbox(coords):
    """Extract bbox from coordinates, handling LineString and MultiLineString."""
    if not coords:
        return 0.0, 0.0, 0.0, 0.0
    try:
        all_coords = []
        if coords and isinstance(coords[0], list):
            if coords[0] and isinstance(coords[0][0], list):
                for line in coords:
                    all_coords.extend(line)
            else:
                all_coords = coords
        if all_coords:
            lons = [c[0] for c in all_coords if isinstance(c, (list, tuple)) and len(c) >= 2]
            lats = [c[1] for c in all_coords if isinstance(c, (list, tuple)) and len(c) >= 2]
            if lons and lats:
                return float(min(lons)), float(min(lats)), float(max(lons)), float(max(lats))
    except:
        pass
    return 0.0, 0.0, 0.0, 0.0

# =============================================================================
# IMPORT FUNCTIONS
# =============================================================================

def import_rivers(conn):
    log("\n=== Rivers (HydroRIVERS) ===")
    rivers_dir = DATA_DIR / 'rivers'
    if not rivers_dir.exists():
        log("  No rivers directory"); return
    conn.execute("DELETE FROM park_rivers")
    rows = []
    for f in sorted(rivers_dir.glob('*.json')):
        park_id = f.stem
        with open(f) as fp:
            rivers = json.load(fp)
        for r in rivers:
            centroid = r.get('centroid') or [None, None]
            rows.append((park_id, r.get('hyriv_id'), r.get('name'), r.get('length_km'),
                        r.get('discharge_cms'), r.get('stream_order'), r.get('relation'),
                        r.get('distance_km'), 
                        centroid[0] if isinstance(centroid, list) and len(centroid) > 0 else None,
                        centroid[1] if isinstance(centroid, list) and len(centroid) > 1 else None))
    columns = ['park_id', 'hyriv_id', 'river_name', 'length_km', 'discharge_cms',
               'stream_order', 'relation', 'distance_km', 'centroid_lon', 'centroid_lat']
    batch_insert(conn, 'park_rivers', columns, rows, 'REPLACE')
    conn.commit()
    parks = conn.execute("SELECT COUNT(DISTINCT park_id) FROM park_rivers").fetchone()[0]
    log(f"  ✓ {len(rows):,} rivers for {parks} parks")

def import_roads(conn):
    log("\n=== Roads (HeiGIT) ===")
    roads_dir = DATA_DIR / 'roads_heigit'
    if not roads_dir.exists():
        log("  No roads_heigit directory"); return
    conn.execute("DELETE FROM roads_heigit")
    conn.execute("DELETE FROM feature_geometries WHERE feature_type = 'road'")
    road_rows, geom_rows = [], []
    for f in sorted(roads_dir.glob('*.json')):
        park_id = f.stem
        with open(f) as fp:
            roads = json.load(fp)
        for r in roads:
            osm_id = str(r.get('osm_id', ''))
            geom = r.get('geometry') or {}
            road_rows.append((park_id, osm_id, r.get('highway'), r.get('surface'),
                             r.get('osm_surface_class'), r.get('osm_length'),
                             r.get('dl_class_2024'), r.get('dl_class_2020'), r.get('surface_change'),
                             r.get('passability_code'), r.get('passability_desc'),
                             r.get('passability_risk'), r.get('rw_class'),
                             json.dumps(geom) if geom else None))
            feature_id = f"{park_id}_{osm_id}"
            coords = geom.get('coordinates', []) if isinstance(geom, dict) else []
            minx, miny, maxx, maxy = safe_bbox(coords)
            props = {'osm_id': osm_id, 'highway': r.get('highway'), 'surface': r.get('surface'),
                    'passability': r.get('passability_desc'), 'dl_class': r.get('dl_class_2024')}
            geom_rows.append(('road', feature_id, park_id, json.dumps(geom) if geom else '{}',
                             minx, miny, maxx, maxy, None, None, json.dumps(props)))
    road_cols = ['park_id', 'osm_id', 'highway_type', 'surface', 'osm_surface_class',
                 'osm_length', 'dl_class_2024', 'dl_class_2020', 'surface_change',
                 'passability_code', 'passability_desc', 'passability_risk', 'rw_class', 'geojson']
    batch_insert(conn, 'roads_heigit', road_cols, road_rows, 'REPLACE')
    geom_cols = ['feature_type', 'feature_id', 'park_id', 'geojson',
                 'bbox_minx', 'bbox_miny', 'bbox_maxx', 'bbox_maxy',
                 'start_date', 'end_date', 'properties_json']
    batch_insert(conn, 'feature_geometries', geom_cols, geom_rows, 'REPLACE')
    conn.commit()
    log(f"  ✓ {len(road_rows):,} roads (both tables)")

def import_osm_places(conn):
    log("\n=== OSM Places ===")
    places_dir = DATA_DIR / 'osm_places'
    if not places_dir.exists():
        log("  No osm_places directory"); return
    conn.execute("DELETE FROM osm_places")
    rows = []
    for f in sorted(places_dir.glob('*.json')):
        park_id = f.stem
        with open(f) as fp:
            data = json.load(fp)
        places = data.get('places', []) if isinstance(data, dict) else data
        for p in places:
            tags = p.get('osm_tags') or {}
            rows.append((park_id, str(p.get('osm_id', '')), p.get('name'), p.get('place_type'),
                        p.get('lat'), p.get('lon'), tags.get('population'),
                        tags.get('admin_level'), json.dumps(tags) if tags else None))
    columns = ['park_id', 'osm_id', 'name', 'place_type', 'lat', 'lon',
               'population', 'admin_level', 'tags_json']
    batch_insert(conn, 'osm_places', columns, rows, 'REPLACE')
    conn.commit()
    parks = conn.execute("SELECT COUNT(DISTINCT park_id) FROM osm_places").fetchone()[0]
    log(f"  ✓ {len(rows):,} places for {parks} parks")

def import_fire_trajectories(conn):
    log("\n=== Fire Trajectories ===")
    traj_dir = DATA_DIR / 'fire_trajectories'
    if not traj_dir.exists():
        log("  No fire_trajectories directory"); return
    conn.execute("DELETE FROM feature_geometries WHERE feature_type = 'fire_trajectory'")
    rows = []
    for f in sorted(traj_dir.glob('*.json')):
        park_id = f.stem
        with open(f) as fp:
            trajs = json.load(fp)
        for t in trajs:
            feature_id = t.get('feature_id') or f"{park_id}_{t.get('year')}_{t.get('group_num')}"
            coords = t.get('coordinates') or []
            minx, miny, maxx, maxy = safe_bbox(coords)
            geojson = {'type': 'LineString', 'coordinates': coords}
            props = {k: t.get(k) for k in ['year', 'group_num', 'group_type', 'refined_type',
                     'days', 'fires_total', 'season', 'direction', 'avg_speed_km_day',
                     'coordinates_with_time', 'rivers_crossed', 'roads_crossed',
                     'nearest_places', 'narrative']}
            rows.append(('fire_trajectory', feature_id, park_id, json.dumps(geojson),
                        minx, miny, maxx, maxy,
                        t.get('start_date'), t.get('end_date'), json.dumps(props)))
    columns = ['feature_type', 'feature_id', 'park_id', 'geojson',
               'bbox_minx', 'bbox_miny', 'bbox_maxx', 'bbox_maxy',
               'start_date', 'end_date', 'properties_json']
    batch_insert(conn, 'feature_geometries', columns, rows, 'REPLACE')
    conn.commit()
    log(f"  ✓ {len(rows):,} fire trajectories")

def import_settlements(conn):
    log("\n=== Settlements ===")
    geom_dir = DATA_DIR / 'feature_geometries' / 'settlement'
    events_dir = DATA_DIR / 'settlement_events'
    if not geom_dir.exists():
        log("  No settlement geometry directory"); return
    classifications = {}
    if events_dir.exists():
        for f in events_dir.glob('*.json'):
            with open(f) as fp:
                for e in json.load(fp):
                    pid = e.get('polygon_ids')
                    if pid:
                        pids = [pid] if isinstance(pid, str) else (pid or [])
                        for p in pids:
                            classifications[p] = {'classification': e.get('classification'),
                                                 'narrative': e.get('narrative')}
    log(f"  Loaded {len(classifications)} classifications")
    conn.execute("DELETE FROM feature_geometries WHERE feature_type = 'settlement'")
    rows = []
    for f in sorted(geom_dir.glob('*.json')):
        park_id = f.stem
        with open(f) as fp:
            features = json.load(fp)
        for feat in features:
            feature_id = feat.get('feature_id', '')
            geojson = feat.get('geojson') or {}
            bbox = feat.get('bbox') or [0,0,0,0]
            props = feat.get('properties') or {}
            if feature_id in classifications:
                props.update(classifications[feature_id])
            rows.append(('settlement', feature_id, park_id, json.dumps(geojson),
                        bbox[0] if len(bbox)>0 else 0, bbox[1] if len(bbox)>1 else 0,
                        bbox[2] if len(bbox)>2 else 0, bbox[3] if len(bbox)>3 else 0,
                        feat.get('start_date'), feat.get('end_date'), json.dumps(props)))
    columns = ['feature_type', 'feature_id', 'park_id', 'geojson',
               'bbox_minx', 'bbox_miny', 'bbox_maxx', 'bbox_maxy',
               'start_date', 'end_date', 'properties_json']
    batch_insert(conn, 'feature_geometries', columns, rows, 'REPLACE')
    conn.commit()
    classified = conn.execute("""SELECT COUNT(*) FROM feature_geometries 
        WHERE feature_type='settlement' AND json_extract(properties_json, '$.classification') IS NOT NULL""").fetchone()[0]
    log(f"  ✓ {len(rows):,} settlements ({classified:,} classified)")

def import_deforestation(conn):
    log("\n=== Deforestation ===")
    geom_dir = DATA_DIR / 'feature_geometries' / 'deforestation'
    events_dir = DATA_DIR / 'deforestation_events'
    if not geom_dir.exists():
        log("  No deforestation geometry directory"); return
    classifications = {}
    if events_dir.exists():
        for f in events_dir.glob('*.json'):
            with open(f) as fp:
                for e in json.load(fp):
                    pid = e.get('polygon_ids')
                    if pid:
                        pids = [pid] if isinstance(pid, str) else (pid or [])
                        for p in pids:
                            classifications[p] = {'classification': e.get('classification'),
                                                 'narrative': e.get('narrative')}
    log(f"  Loaded {len(classifications)} classifications")
    conn.execute("DELETE FROM feature_geometries WHERE feature_type = 'deforestation'")
    rows = []
    for f in sorted(geom_dir.glob('*.json')):
        park_id = f.stem
        with open(f) as fp:
            features = json.load(fp)
        for feat in features:
            feature_id = feat.get('feature_id', '')
            geojson = feat.get('geojson') or {}
            bbox = feat.get('bbox') or [0,0,0,0]
            props = feat.get('properties') or {}
            if feature_id in classifications:
                props.update(classifications[feature_id])
            rows.append(('deforestation', feature_id, park_id, json.dumps(geojson),
                        bbox[0] if len(bbox)>0 else 0, bbox[1] if len(bbox)>1 else 0,
                        bbox[2] if len(bbox)>2 else 0, bbox[3] if len(bbox)>3 else 0,
                        feat.get('start_date'), feat.get('end_date'), json.dumps(props)))
    columns = ['feature_type', 'feature_id', 'park_id', 'geojson',
               'bbox_minx', 'bbox_miny', 'bbox_maxx', 'bbox_maxy',
               'start_date', 'end_date', 'properties_json']
    batch_insert(conn, 'feature_geometries', columns, rows, 'REPLACE')
    conn.commit()
    classified = conn.execute("""SELECT COUNT(*) FROM feature_geometries 
        WHERE feature_type='deforestation' AND json_extract(properties_json, '$.classification') IS NOT NULL""").fetchone()[0]
    log(f"  ✓ {len(rows):,} deforestation ({classified:,} classified)")

def import_fire_analysis(conn):
    log("\n=== Fire Analysis & Group Infractions ===")
    analysis_dir = DATA_DIR / 'fire_analysis'
    traj_dir = DATA_DIR / 'fire_trajectories'
    
    if analysis_dir.exists():
        rows = []
        for f in sorted(analysis_dir.glob('*.json')):
            park_id = f.stem
            with open(f) as fp:
                data = json.load(fp)
            for yr in data.get('years', []):
                rows.append((park_id, yr.get('year'), yr.get('total_fires'), yr.get('dry_season_fires'),
                            yr.get('transhumance_groups'), yr.get('transhumance_fires'),
                            yr.get('avg_transhumance_speed'), yr.get('herder_groups'),
                            yr.get('management_groups'), yr.get('village_groups'),
                            yr.get('peak_month'), yr.get('analyzed_at')))
        conn.execute("DELETE FROM park_fire_analysis")
        columns = ['park_id', 'year', 'total_fires', 'dry_season_fires', 'transhumance_groups',
                   'transhumance_fires', 'avg_transhumance_speed', 'herder_groups',
                   'management_groups', 'village_groups', 'peak_month', 'analyzed_at']
        batch_insert(conn, 'park_fire_analysis', columns, rows, 'REPLACE')
        conn.commit()
        log(f"  ✓ {len(rows):,} park_fire_analysis records")
    
    if traj_dir.exists():
        conn.execute("DELETE FROM park_group_infractions")
        stats = defaultdict(lambda: defaultdict(lambda: {'total':0, 'stopped':0, 'transited':0, 'fires':0, 'days_sum':0, 'trajs':[]}))
        for f in sorted(traj_dir.glob('*.json')):
            park_id = f.stem
            with open(f) as fp:
                trajs = json.load(fp)
            for t in trajs:
                year = t.get('year')
                if not year: continue
                s = stats[park_id][year]
                s['total'] += 1
                s['fires'] += t.get('fires_total') or 0
                s['days_sum'] += t.get('days') or 0
                stopped = (t.get('days') or 0) <= 3 or (t.get('group_type') or '').startswith('local')
                s['stopped' if stopped else 'transited'] += 1
                coords = t.get('coordinates') or []
                origin = {'lat': coords[0][1], 'lon': coords[0][0]} if coords else {}
                dest = {'lat': coords[-1][1], 'lon': coords[-1][0]} if coords else {}
                s['trajs'].append({'entry_date': t.get('start_date'), 'last_inside': t.get('end_date'),
                                  'days_inside': t.get('days'), 'fires_inside': t.get('fires_total'),
                                  'outcome': 'STOPPED_INSIDE' if stopped else 'TRANSITED',
                                  'group_type': t.get('group_type'), 'direction': t.get('direction'),
                                  'speed_km_day': t.get('avg_speed_km_day'), 'origin': origin, 'destination': dest})
        rows = []
        for park_id, years in stats.items():
            for year, data in years.items():
                avg_days = data['days_sum'] / data['total'] if data['total'] > 0 else 0
                rows.append((park_id, year, data['total'], data['stopped'], data['transited'],
                            avg_days, avg_days, data['total'], data['fires'], 0, json.dumps(data['trajs'])))
        columns = ['park_id', 'year', 'total_groups', 'groups_stopped_inside', 'groups_transited',
                   'avg_days_burning', 'median_days_burning', 'max_days_burning',
                   'total_fires_inside', 'resumed_outside', 'trajectories_json']
        batch_insert(conn, 'park_group_infractions', columns, rows, 'REPLACE')
        conn.commit()
        log(f"  ✓ {len(rows):,} park_group_infractions records")

def import_climate(conn):
    log("\n=== Climate ===")
    climate_file = DATA_DIR / 'climate' / 'park_climate.json'
    if not climate_file.exists():
        log("  No climate file"); return
    with open(climate_file) as f:
        data = json.load(f)
    conn.execute("DELETE FROM park_climate")
    rows = [(park_id, c.get('climate_zone'), c.get('annual_precip_mm') or c.get('precip_annual_mm'),
            c.get('dry_season'), c.get('rainy_season'),
            json.dumps(c.get('monthly_precip') or [])) for park_id, c in data.items()]
    columns = ['park_id', 'climate_zone', 'precip_annual_mm', 'dry_season', 'rainy_season', 'monthly_precip']
    batch_insert(conn, 'park_climate', columns, rows, 'REPLACE')
    conn.commit()
    log(f"  ✓ {len(rows)} parks")

def import_species(conn):
    log("\n=== Species ===")
    species_file = DATA_DIR / 'species' / 'park_species.json'
    if not species_file.exists():
        log("  No species file"); return
    with open(species_file) as f:
        data = json.load(f)
    conn.execute("DELETE FROM park_species")
    rows = [(park_id, sp.get('scientific_name'), sp.get('common_name'), sp.get('taxon_class'),
            sp.get('category'), sp.get('population_trend'))
           for park_id, species_list in data.items() for sp in species_list]
    columns = ['park_id', 'scientific_name', 'common_name', 'taxon_class', 'iucn_category', 'population_trend']
    batch_insert(conn, 'park_species', columns, rows, 'REPLACE')
    conn.commit()
    log(f"  ✓ {len(rows):,} species records")

def import_waterbodies(conn):
    log("\n=== Waterbodies ===")
    water_dir = DATA_DIR / 'waterbodies'
    if not water_dir.exists():
        log("  No waterbodies directory"); return
    conn.execute("DELETE FROM park_waterbodies")
    rows = []
    for f in sorted(water_dir.glob('*.json')):
        if f.name == 'summary.json':
            continue  # Skip summary file
        park_id = f.stem
        with open(f) as fp:
            data = json.load(fp)
        # Handle both list and dict formats
        if isinstance(data, list):
            bodies = data
        elif isinstance(data, dict):
            bodies = data.get('waterbodies', [])
        else:
            continue
        for wb in bodies:
            if not isinstance(wb, dict):
                continue
            rows.append((park_id, wb.get('id', ''), wb.get('name'), wb.get('type'),
                        wb.get('lat'), wb.get('lon'),
                        json.dumps(wb.get('geojson')) if wb.get('geojson') else None))
    columns = ['park_id', 'waterbody_id', 'name', 'waterbody_type', 'lat', 'lon', 'geojson']
    batch_insert(conn, 'park_waterbodies', columns, rows, 'REPLACE')
    conn.commit()
    log(f"  ✓ {len(rows):,} waterbodies")

# =============================================================================
# MAIN
# =============================================================================

IMPORTS = {
    'rivers': import_rivers,
    'roads': import_roads,
    'osm_places': import_osm_places,
    'fire_trajectories': import_fire_trajectories,
    'settlements': import_settlements,
    'deforestation': import_deforestation,
    'fire_analysis': import_fire_analysis,
    'climate': import_climate,
    'species': import_species,
    'waterbodies': import_waterbodies,
}

def print_summary(conn):
    log("\n" + "="*50)
    log("DATABASE SUMMARY")
    log("="*50)
    log("\nfeature_geometries:")
    for row in conn.execute("""SELECT feature_type, COUNT(*), 
           SUM(CASE WHEN json_extract(properties_json, '$.classification') IS NOT NULL THEN 1 ELSE 0 END)
        FROM feature_geometries GROUP BY feature_type ORDER BY COUNT(*) DESC"""):
        classified = f" ({row[2]:,} classified)" if row[2] and row[2] > 0 else ""
        log(f"  {row[0]}: {row[1]:,}{classified}")
    log("\nOther tables:")
    for table, pk in [('park_rivers','park_id'),('roads_heigit','park_id'),('osm_places','park_id'),
                      ('park_fire_analysis','park_id'),('park_group_infractions','park_id'),
                      ('park_climate','park_id'),('park_species','park_id'),('park_waterbodies','park_id'),
                      ('fire_narrative_cache',None)]:
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if pk:
                parks = conn.execute(f"SELECT COUNT(DISTINCT {pk}) FROM {table}").fetchone()[0]
                log(f"  {table}: {total:,} ({parks} parks)")
            else:
                log(f"  {table}: {total:,}")
        except: log(f"  {table}: (missing)")

def main():
    args = sys.argv[1:]
    
    # Handle --list
    if '--list' in args:
        print("Available imports:")
        for name in IMPORTS:
            print(f"  {name}")
        return
    
    # Handle --skip
    skip = set()
    if '--skip' in args:
        idx = args.index('--skip')
        skip = set(args[idx+1:])
        args = args[:idx]
    
    # Determine which imports to run
    if args:
        to_run = [a for a in args if a in IMPORTS]
        if not to_run:
            print(f"Unknown imports: {args}")
            print("Use --list to see available imports")
            return
    else:
        to_run = list(IMPORTS.keys())
    
    # Remove skipped
    to_run = [i for i in to_run if i not in skip]
    
    log("Starting JSON to Database Import")
    log(f"Database: {DB_PATH}")
    log(f"Data directory: {DATA_DIR}")
    log(f"Imports: {', '.join(to_run)}")
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=10000")
    
    try:
        for name in to_run:
            IMPORTS[name](conn)
        print_summary(conn)
        log("\n✓ Import complete")
    except Exception as e:
        log(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    main()
