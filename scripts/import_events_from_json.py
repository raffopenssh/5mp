#!/usr/bin/env python3
"""
Import deforestation and settlement events from JSON files.

Updates existing records or inserts new ones, preserving polygon_ids
for UI linking to feature_geometries.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
DEFOREST_DIR = BASE_DIR / "data" / "deforestation_events"
SETTLEMENT_DIR = BASE_DIR / "data" / "settlement_events"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def import_deforestation(conn):
    log("Importing deforestation events...")
    
    # Clear existing
    conn.execute("DELETE FROM deforestation_events")
    
    count = 0
    for json_file in sorted(DEFOREST_DIR.glob("*.json")):
        try:
            with open(json_file) as f:
                events = json.load(f)
            
            for e in events:
                conn.execute("""
                    INSERT INTO deforestation_events 
                    (park_id, year, area_km2, lat, lon, pattern_type, classification,
                     classification_confidence, narrative, fires_same_year, fire_ratio,
                     polygon_ids, pixel_count, event_type, nearest_settlement_km, classified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    e.get('park_id'),
                    e.get('year'),
                    e.get('area_km2'),
                    e.get('lat'),
                    e.get('lon'),
                    e.get('pattern_type'),
                    e.get('classification'),
                    e.get('classification_confidence'),
                    e.get('narrative'),
                    e.get('fires_same_year'),
                    e.get('fire_ratio'),
                    e.get('polygon_ids'),
                    e.get('pixel_count'),
                    e.get('event_type'),
                    e.get('nearest_settlement_km'),
                    e.get('classified_at')
                ))
                count += 1
        except Exception as ex:
            log(f"  Error loading {json_file.name}: {ex}")
    
    conn.commit()
    log(f"  Imported {count} deforestation events")
    return count

def import_settlements(conn):
    log("Importing settlement events...")
    
    # Clear existing
    conn.execute("DELETE FROM park_settlements")
    
    count = 0
    for json_file in sorted(SETTLEMENT_DIR.glob("*.json")):
        try:
            with open(json_file) as f:
                events = json.load(f)
            
            for e in events:
                conn.execute("""
                    INSERT OR REPLACE INTO park_settlements 
                    (park_id, lat, lon, area_m2, population_est, households_est,
                     nearest_place, distance_to_place_km, direction_from_place,
                     settlement_type, in_buffer, classification, classification_confidence,
                     narrative, polygon_ids, fires_5km, fire_seasonality, 
                     deforest_nearby_km2, classified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    e.get('park_id'),
                    e.get('lat'),
                    e.get('lon'),
                    e.get('area_m2'),
                    e.get('population_est'),
                    e.get('households_est'),
                    e.get('nearest_place'),
                    e.get('distance_to_place_km'),
                    e.get('direction_from_place'),
                    e.get('settlement_type'),
                    e.get('in_buffer', 0),
                    e.get('classification'),
                    e.get('classification_confidence'),
                    e.get('narrative'),
                    e.get('polygon_ids'),
                    e.get('fires_5km', 0),
                    e.get('fire_seasonality'),
                    e.get('deforest_nearby_km2', 0),
                    e.get('classified_at')
                ))
                count += 1
        except Exception as ex:
            log(f"  Error loading {json_file.name}: {ex}")
    
    conn.commit()
    log(f"  Imported {count} settlement events")
    return count

def main():
    log("=" * 60)
    log("Import Events from JSON")
    log("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    
    deforest_count = import_deforestation(conn)
    settlement_count = import_settlements(conn)
    
    # Verify polygon_ids link to feature_geometries
    log("\nVerifying polygon links...")
    
    cursor = conn.execute("""
        SELECT COUNT(*) FROM deforestation_events 
        WHERE polygon_ids IS NOT NULL AND polygon_ids != ''
    """)
    deforest_with_polygons = cursor.fetchone()[0]
    
    cursor = conn.execute("""
        SELECT COUNT(*) FROM park_settlements 
        WHERE polygon_ids IS NOT NULL AND polygon_ids != ''
    """)
    settlement_with_polygons = cursor.fetchone()[0]
    
    cursor = conn.execute("SELECT COUNT(DISTINCT feature_id) FROM feature_geometries WHERE feature_type = 'deforestation'")
    deforest_geometries = cursor.fetchone()[0]
    
    cursor = conn.execute("SELECT COUNT(DISTINCT feature_id) FROM feature_geometries WHERE feature_type = 'settlement'")
    settlement_geometries = cursor.fetchone()[0]
    
    log(f"  Deforestation: {deforest_with_polygons} events with polygon_ids, {deforest_geometries} geometries")
    log(f"  Settlement: {settlement_with_polygons} events with polygon_ids, {settlement_geometries} geometries")
    
    conn.close()
    
    log("\nComplete!")

if __name__ == "__main__":
    main()
