#!/usr/bin/env python3
"""
Export settlement and deforestation events from database to JSON.

Creates:
- data/settlement_events/<park_id>.json
- data/deforestation_events/<park_id>.json

These files include the auto-increment ID from the source database.
When importing to another database, use coordinate/year matching, not ID matching.

Usage:
  python3 scripts/export_events_from_db.py             # all parks
  python3 scripts/export_events_from_db.py --park CAF_Chinko
"""

import argparse
import json
import sqlite3
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / 'db.sqlite3'
SETTLEMENT_DIR = BASE_DIR / 'data' / 'settlement_events'
DEFOREST_DIR = BASE_DIR / 'data' / 'deforestation_events'

def export_settlements(conn, park_id=None):
    """Export settlements with classifications and polygon_ids"""
    SETTLEMENT_DIR.mkdir(parents=True, exist_ok=True)

    query = '''
        SELECT id, park_id, lat, lon, area_m2, population_est, households_est,
               nearest_place, distance_to_place_km, direction_from_place,
               settlement_type, in_buffer, tile_row, tile_col, detected_at,
               classification, classification_confidence, narrative,
               fires_5km, fire_seasonality, deforest_nearby_km2, classified_at,
               polygon_ids
        FROM park_settlements
    '''
    params = ()
    if park_id:
        query += ' WHERE park_id = ?'
        params = (park_id,)
    query += ' ORDER BY park_id, id'
    cursor = conn.execute(query, params)

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
    for pid, settlements in by_park.items():
        output_file = SETTLEMENT_DIR / f'{pid}.json'
        with open(output_file, 'w') as f:
            json.dump(settlements, f, indent=2)
        total += len(settlements)
        print(f"  {pid}: {len(settlements)} settlements")

    return len(by_park), total

def export_deforestation(conn, park_id=None):
    """Export deforestation events with classifications and polygon_ids.

    Note: event_type/geojson/description/pixel_count/created_at no longer
    exist in the deforestation_events schema; kept as null in the JSON for
    backwards compatibility with earlier exports.
    """
    DEFOREST_DIR.mkdir(parents=True, exist_ok=True)

    query = '''
        SELECT id, park_id, year, area_km2, lat, lon,
               pattern_type,
               classification, classification_confidence, narrative,
               fires_same_year, fire_ratio, nearest_settlement_km, classified_at,
               polygon_ids
        FROM deforestation_events
    '''
    params = ()
    if park_id:
        query += ' WHERE park_id = ?'
        params = (park_id,)
    query += ' ORDER BY park_id, year'
    cursor = conn.execute(query, params)

    by_park = defaultdict(list)

    for row in cursor:
        by_park[row[1]].append({
            'id': row[0],
            'park_id': row[1],
            'year': row[2],
            'area_km2': row[3],
            'event_type': None,
            'lat': row[4],
            'lon': row[5],
            'geojson': None,
            'description': None,
            'pattern_type': row[6],
            'pixel_count': None,
            'created_at': None,
            'classification': row[7],
            'classification_confidence': row[8],
            'narrative': row[9],
            'fires_same_year': row[10],
            'fire_ratio': row[11],
            'nearest_settlement_km': row[12],
            'classified_at': row[13],
            'polygon_ids': row[14]
        })

    total = 0
    for pid, events in by_park.items():
        output_file = DEFOREST_DIR / f'{pid}.json'
        with open(output_file, 'w') as f:
            json.dump(events, f, indent=2)
        total += len(events)
        print(f"  {pid}: {len(events)} events")

    return len(by_park), total

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--park', help='Export a single park only')
    args = parser.parse_args()

    print("Exporting events from database to JSON...")
    print("=" * 60)

    conn = sqlite3.connect(str(DB_PATH))

    print("\n[1] Exporting settlements...")
    parks, settlements = export_settlements(conn, args.park)
    print(f"\n    Total: {settlements} settlements across {parks} parks")

    print("\n[2] Exporting deforestation events...")
    parks, events = export_deforestation(conn, args.park)
    print(f"\n    Total: {events} events across {parks} parks")

    conn.close()

    print("\n" + "=" * 60)
    print("Export complete!")
    print("\nNote: JSON files include auto-increment IDs from this database.")
    print("When importing to another database, use coordinate/year matching.")

if __name__ == '__main__':
    main()
