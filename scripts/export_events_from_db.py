#!/usr/bin/env python3
"""
Export settlement and deforestation events from database to JSON.

Creates:
- data/settlement_events/<park_id>.json
- data/deforestation_events/<park_id>.json

These files include the auto-increment ID from the source database.
When importing to another database, use coordinate/year matching, not ID matching.
"""

import json
import sqlite3
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / 'db.sqlite3'
SETTLEMENT_DIR = BASE_DIR / 'data' / 'settlement_events'
DEFOREST_DIR = BASE_DIR / 'data' / 'deforestation_events'

def export_settlements(conn):
    """Export all settlements with classifications and polygon_ids"""
    SETTLEMENT_DIR.mkdir(parents=True, exist_ok=True)
    
    cursor = conn.execute('''
        SELECT id, park_id, lat, lon, area_m2, population_est, households_est,
               nearest_place, distance_to_place_km, direction_from_place,
               settlement_type, in_buffer, tile_row, tile_col, detected_at,
               classification, classification_confidence, narrative,
               fires_5km, fire_seasonality, deforest_nearby_km2, classified_at,
               polygon_ids
        FROM park_settlements
        ORDER BY park_id, id
    ''')
    
    by_park = defaultdict(list)
    
    for row in cursor:
        by_park[row[1]].append({
            'id': row[0],
            'park_id': row[1],
            'lat': row[2],
            'lon': row[3],
            'area_m2': row[4],
            'population_est': row[5],
            'households_est': row[6],
            'nearest_place': row[7],
            'distance_to_place_km': row[8],
            'direction_from_place': row[9],
            'settlement_type': row[10],
            'in_buffer': row[11],
            'tile_row': row[12],
            'tile_col': row[13],
            'detected_at': row[14],
            'classification': row[15],
            'classification_confidence': row[16],
            'narrative': row[17],
            'fires_5km': row[18],
            'fire_seasonality': row[19],
            'deforest_nearby_km2': row[20],
            'classified_at': row[21],
            'polygon_ids': row[22]
        })
    
    total = 0
    for park_id, settlements in by_park.items():
        output_file = SETTLEMENT_DIR / f'{park_id}.json'
        with open(output_file, 'w') as f:
            json.dump(settlements, f, indent=2)
        total += len(settlements)
        print(f"  {park_id}: {len(settlements)} settlements")
    
    return len(by_park), total

def export_deforestation(conn):
    """Export all deforestation events with classifications and polygon_ids"""
    DEFOREST_DIR.mkdir(parents=True, exist_ok=True)
    
    cursor = conn.execute('''
        SELECT id, park_id, year, area_km2, event_type, lat, lon,
               geojson, description, pattern_type, pixel_count, created_at,
               classification, classification_confidence, narrative,
               fires_same_year, fire_ratio, nearest_settlement_km, classified_at,
               polygon_ids
        FROM deforestation_events
        ORDER BY park_id, year
    ''')
    
    by_park = defaultdict(list)
    
    for row in cursor:
        by_park[row[1]].append({
            'id': row[0],
            'park_id': row[1],
            'year': row[2],
            'area_km2': row[3],
            'event_type': row[4],
            'lat': row[5],
            'lon': row[6],
            'geojson': row[7],
            'description': row[8],
            'pattern_type': row[9],
            'pixel_count': row[10],
            'created_at': row[11],
            'classification': row[12],
            'classification_confidence': row[13],
            'narrative': row[14],
            'fires_same_year': row[15],
            'fire_ratio': row[16],
            'nearest_settlement_km': row[17],
            'classified_at': row[18],
            'polygon_ids': row[19]
        })
    
    total = 0
    for park_id, events in by_park.items():
        output_file = DEFOREST_DIR / f'{park_id}.json'
        with open(output_file, 'w') as f:
            json.dump(events, f, indent=2)
        total += len(events)
        print(f"  {park_id}: {len(events)} events")
    
    return len(by_park), total

def main():
    print("Exporting events from database to JSON...")
    print("=" * 60)
    
    conn = sqlite3.connect(str(DB_PATH))
    
    print("\n[1] Exporting settlements...")
    parks, settlements = export_settlements(conn)
    print(f"\n    Total: {settlements} settlements across {parks} parks")
    
    print("\n[2] Exporting deforestation events...")
    parks, events = export_deforestation(conn)
    print(f"\n    Total: {events} events across {parks} parks")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("Export complete!")
    print("\nNote: JSON files include auto-increment IDs from this database.")
    print("When importing to another database, use coordinate/year matching.")

if __name__ == '__main__':
    main()
