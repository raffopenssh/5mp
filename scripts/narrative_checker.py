#!/usr/bin/env python3
"""
Narrative Data Checker

Checks database and JSON files to ensure all narrative data is properly loaded.
Run this to diagnose missing data issues.

Usage:
    python scripts/narrative_checker.py
    python scripts/narrative_checker.py --fix  # Also attempt to fix issues
"""

import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = "db.sqlite3"
DATA_DIR = Path("data")

# Test parks
TEST_PARKS = ["CAF_Chinko", "TCD_Zakouma", "COD_Virunga", "TZA_Serengeti"]

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def ok(msg):
    print(f"{Colors.GREEN}✅ {msg}{Colors.END}")

def fail(msg):
    print(f"{Colors.RED}❌ {msg}{Colors.END}")

def warn(msg):
    print(f"{Colors.YELLOW}⚠️  {msg}{Colors.END}")

def info(msg):
    print(f"{Colors.BLUE}ℹ️  {msg}{Colors.END}")

def check_database_tables(conn):
    """Check that required tables exist and have data"""
    print("\n" + "="*50)
    print("DATABASE TABLES")
    print("="*50)
    
    tables = {
        'fire_narrative_cache': 'Fire narrative cache',
        'feature_geometries': 'Feature geometries (fire, deforest, settlement)',
        'park_fire_analysis': 'Park fire analysis by year',
        'deforestation_events': 'Deforestation events',
        'park_settlements': 'Park settlements (GHSL)',
        'osm_places': 'OSM place names',
        'park_climate': 'Park climate data',
        'park_species': 'IUCN species data',
    }
    
    cursor = conn.cursor()
    results = {}
    
    for table, desc in tables.items():
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            results[table] = count
            if count > 0:
                ok(f"{desc}: {count:,} rows")
            else:
                fail(f"{desc}: 0 rows")
        except sqlite3.OperationalError as e:
            fail(f"{desc}: TABLE MISSING - {e}")
            results[table] = -1
    
    return results

def check_fire_narrative_cache(conn, park_id):
    """Check fire narrative cache for a specific park"""
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT park_id, computed_at, 
               json_extract(narrative_json, '$.narratives') as narratives,
               json_extract(narrative_json, '$.trend.seasonality') as seasonality,
               json_extract(narrative_json, '$.trend.months') as months
        FROM fire_narrative_cache 
        WHERE park_id = ?
    """, (park_id,))
    
    row = cursor.fetchone()
    if not row:
        fail(f"No fire narrative cache for {park_id}")
        return False
    
    park_id, computed_at, narratives_json, seasonality, months_json = row
    
    narratives = json.loads(narratives_json) if narratives_json else []
    months = json.loads(months_json) if months_json else []
    
    ok(f"Cache exists, computed: {computed_at}")
    
    if len(narratives) > 0:
        ok(f"Narratives: {len(narratives)}")
        sample = narratives[0].get('narrative', '')[:80] if narratives else ''
        if sample:
            print(f"      Sample: \"{sample}...\"")
    else:
        fail(f"Narratives: 0")
    
    if seasonality:
        ok(f"Seasonality: {seasonality}")
    else:
        fail(f"Seasonality: missing")
    
    if len(months) > 0:
        ok(f"Monthly trend data: {len(months)} months")
    else:
        fail(f"Monthly trend data: missing")
    
    return len(narratives) > 0 and seasonality is not None

def check_feature_geometries(conn, park_id):
    """Check feature geometries for a park"""
    cursor = conn.cursor()
    
    types = ['fire_trajectory', 'deforestation', 'settlement']
    results = {}
    
    for ftype in types:
        cursor.execute("""
            SELECT COUNT(*), 
                   COUNT(DISTINCT json_extract(properties_json, '$.narrative'))
            FROM feature_geometries 
            WHERE park_id = ? AND feature_type = ?
        """, (park_id, ftype))
        
        count, with_narrative = cursor.fetchone()
        results[ftype] = {'count': count, 'with_narrative': with_narrative}
        
        if count > 0:
            ok(f"{ftype}: {count} features, {with_narrative} with narratives")
        else:
            warn(f"{ftype}: 0 features") if ftype != 'deforestation' else fail(f"{ftype}: 0 features")
    
    return results

def check_json_files(park_id):
    """Check JSON data files for a park"""
    print(f"\n--- JSON Files for {park_id} ---")
    
    files = {
        'fire_analysis': DATA_DIR / 'fire_analysis' / f'{park_id}.json',
        'fire_trajectories': DATA_DIR / 'fire_trajectories' / f'{park_id}.json',
        'settlement_events': DATA_DIR / 'settlement_events' / f'{park_id}.json',
        'deforestation_events': DATA_DIR / 'deforestation_events' / f'{park_id}.json',
        'fire_narratives': DATA_DIR / 'export' / 'fire_narratives' / f'{park_id}.json',
    }
    
    results = {}
    for name, path in files.items():
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, list):
                    count = len(data)
                elif isinstance(data, dict):
                    count = len(data.get('narratives', data.get('events', data.get('groups', [data]))))
                else:
                    count = 1
                ok(f"{name}: {path.name} ({count} items)")
                results[name] = count
            except Exception as e:
                fail(f"{name}: Error reading - {e}")
                results[name] = -1
        else:
            warn(f"{name}: {path.name} not found")
            results[name] = 0
    
    return results

def check_classified_data(conn, park_id):
    """Check for classified settlement/deforestation data"""
    cursor = conn.cursor()
    
    # Check deforestation with classification
    cursor.execute("""
        SELECT COUNT(*), 
               COUNT(DISTINCT json_extract(properties_json, '$.classification'))
        FROM feature_geometries 
        WHERE park_id = ? AND feature_type = 'deforestation'
          AND json_extract(properties_json, '$.classification') IS NOT NULL
    """, (park_id,))
    
    defo_count, defo_classes = cursor.fetchone()
    if defo_count > 0:
        ok(f"Classified deforestation: {defo_count} events, {defo_classes} classes")
    else:
        fail(f"Classified deforestation: 0 (need to run classification)")
    
    # Check settlements with classification
    cursor.execute("""
        SELECT COUNT(*),
               COUNT(DISTINCT json_extract(properties_json, '$.classification'))
        FROM feature_geometries 
        WHERE park_id = ? AND feature_type = 'settlement'
          AND json_extract(properties_json, '$.classification') IS NOT NULL
    """, (park_id,))
    
    settl_count, settl_classes = cursor.fetchone()
    if settl_count > 0:
        ok(f"Classified settlements: {settl_count} events, {settl_classes} classes")
    else:
        fail(f"Classified settlements: 0 (need to run classification)")

def main():
    fix_mode = '--fix' in sys.argv
    
    print("="*60)
    print("NARRATIVE DATA CHECKER")
    print("="*60)
    print(f"Database: {DB_PATH}")
    print(f"Data dir: {DATA_DIR}")
    print(f"Fix mode: {fix_mode}")
    
    if not Path(DB_PATH).exists():
        fail(f"Database not found: {DB_PATH}")
        sys.exit(1)
    
    conn = sqlite3.connect(DB_PATH)
    
    # Check database tables
    table_results = check_database_tables(conn)
    
    # Check each test park
    all_passed = True
    for park_id in TEST_PARKS:
        print(f"\n{'='*60}")
        print(f"PARK: {park_id}")
        print("="*60)
        
        print("\n--- Fire Narrative Cache ---")
        if not check_fire_narrative_cache(conn, park_id):
            all_passed = False
        
        print("\n--- Feature Geometries ---")
        check_feature_geometries(conn, park_id)
        
        print("\n--- Classified Data ---")
        check_classified_data(conn, park_id)
        
        # Check JSON files
        check_json_files(park_id)
    
    conn.close()
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    if all_passed:
        ok("All critical checks passed")
    else:
        fail("Some checks failed")
        print("\nTo fix, run these scripts in order:")
        print("  1. python scripts/analyze_fire_trajectories_v3.py")
        print("  2. python scripts/precompute_narratives_v3.py")
        print("  3. Restart the server to clear caches")
    
    return 0 if all_passed else 1

if __name__ == '__main__':
    sys.exit(main())
