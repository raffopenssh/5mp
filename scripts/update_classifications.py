#!/usr/bin/env python3
"""
Update classifications by matching on coordinates instead of IDs
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'

def update_settlements(conn):
    """Update settlements matching by park_id and coordinates"""
    settle_dir = DATA_DIR / 'settlement_events'
    if not settle_dir.exists():
        return 0
    
    count = 0
    for json_file in settle_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                settlements = json.load(f)
            
            for s in settlements:
                # Match by park_id and approximate coordinates
                lat = s.get('lat')
                lon = s.get('lon')
                if lat is None or lon is None:
                    continue
                    
                cursor = conn.execute('''
                    UPDATE park_settlements 
                    SET classification = ?,
                        classification_confidence = ?,
                        narrative = ?
                    WHERE park_id = ? 
                      AND ABS(lat - ?) < 0.0001
                      AND ABS(lon - ?) < 0.0001
                ''', (
                    s.get('classification'),
                    s.get('classification_confidence'),
                    s.get('narrative'),
                    park_id,
                    lat,
                    lon
                ))
                if cursor.rowcount > 0:
                    count += cursor.rowcount
        except Exception as e:
            print(f"  Error: {park_id}: {e}")
    
    conn.commit()
    return count

def update_deforestation(conn):
    """Update deforestation matching by park_id, year, and coordinates"""
    defo_dir = DATA_DIR / 'deforestation_events'
    if not defo_dir.exists():
        return 0
    
    count = 0
    for json_file in defo_dir.glob('*.json'):
        park_id = json_file.stem
        try:
            with open(json_file) as f:
                events = json.load(f)
            
            for e in events:
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
                      AND ABS(lat - ?) < 0.0001
                      AND ABS(lon - ?) < 0.0001
                ''', (
                    e.get('classification'),
                    e.get('classification_confidence'),
                    e.get('narrative'),
                    park_id,
                    year,
                    lat,
                    lon
                ))
                if cursor.rowcount > 0:
                    count += cursor.rowcount
        except Exception as e:
            print(f"  Error: {park_id}: {e}")
    
    conn.commit()
    return count

def main():
    print("Updating classifications by coordinate matching...")
    conn = sqlite3.connect(DB_PATH)
    
    print("\n[1] Updating settlement classifications...")
    settlements = update_settlements(conn)
    print(f"    Updated {settlements} settlements")
    
    print("\n[2] Updating deforestation classifications...")
    deforest = update_deforestation(conn)
    print(f"    Updated {deforest} deforestation events")
    
    # Verify
    cursor = conn.execute("SELECT COUNT(*) FROM park_settlements WHERE classification IS NOT NULL")
    print(f"\n  Total classified settlements: {cursor.fetchone()[0]}")
    cursor = conn.execute("SELECT COUNT(*) FROM deforestation_events WHERE classification IS NOT NULL")
    print(f"  Total classified deforestation: {cursor.fetchone()[0]}")
    
    conn.close()

if __name__ == '__main__':
    main()
