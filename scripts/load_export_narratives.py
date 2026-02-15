#!/usr/bin/env python3
"""
Load settlement and deforestation narratives from export files
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data' / 'export'

def load_settlement_narratives(conn):
    """Load settlement narratives and update park_settlements"""
    file_path = DATA_DIR / 'settlement_narratives.json'
    if not file_path.exists():
        return 0
    
    with open(file_path) as f:
        data = json.load(f)
    
    count = 0
    for park_id, park_data in data.items():
        for s in park_data.get('settlements', []):
            lat = s.get('lat')
            lon = s.get('lon')
            if lat is None or lon is None:
                continue
            
            # Match by coordinates (rounded)
            cursor = conn.execute('''
                UPDATE park_settlements 
                SET classification = ?,
                    classification_confidence = ?,
                    narrative = ?
                WHERE park_id = ?
                  AND ROUND(lat, 4) = ROUND(?, 4)
                  AND ROUND(lon, 4) = ROUND(?, 4)
            ''', (
                s.get('classification'),
                s.get('confidence'),
                s.get('narrative'),
                park_id,
                lat,
                lon
            ))
            count += cursor.rowcount
    
    conn.commit()
    return count

def load_deforestation_narratives(conn):
    """Load deforestation narratives"""
    file_path = DATA_DIR / 'deforestation_narratives.json'
    if not file_path.exists():
        return 0
    
    with open(file_path) as f:
        data = json.load(f)
    
    count = 0
    for park_id, park_data in data.items():
        for e in park_data.get('events', []):
            lat = e.get('lat')
            lon = e.get('lon')
            year = e.get('year')
            if lat is None or lon is None or year is None:
                continue
            
            cursor = conn.execute('''
                UPDATE deforestation_events 
                SET classification = ?,
                    classification_confidence = ?,
                    narrative = ?
                WHERE park_id = ?
                  AND year = ?
                  AND ROUND(lat, 3) = ROUND(?, 3)
                  AND ROUND(lon, 3) = ROUND(?, 3)
            ''', (
                e.get('classification'),
                e.get('confidence'),
                e.get('narrative'),
                park_id,
                year,
                lat,
                lon
            ))
            count += cursor.rowcount
    
    conn.commit()
    return count

def main():
    print("Loading narratives from export files...")
    conn = sqlite3.connect(DB_PATH)
    
    print("\n[1] Loading settlement narratives...")
    settlements = load_settlement_narratives(conn)
    print(f"    Updated {settlements} settlements")
    
    print("\n[2] Loading deforestation narratives...")
    deforest = load_deforestation_narratives(conn)
    print(f"    Updated {deforest} deforestation events")
    
    # Verify
    cursor = conn.execute("SELECT COUNT(*) FROM park_settlements WHERE classification IS NOT NULL")
    print(f"\n  Total classified settlements: {cursor.fetchone()[0]}")
    cursor = conn.execute("SELECT COUNT(*) FROM deforestation_events WHERE classification IS NOT NULL")
    print(f"  Total classified deforestation: {cursor.fetchone()[0]}")
    
    # Sample
    cursor = conn.execute("SELECT park_id, classification, narrative FROM park_settlements WHERE classification IS NOT NULL LIMIT 1")
    row = cursor.fetchone()
    if row:
        print(f"\n  Sample settlement: {row[0]} - {row[1]}")
        print(f"    {row[2][:100]}...")
    
    conn.close()

if __name__ == '__main__':
    main()
