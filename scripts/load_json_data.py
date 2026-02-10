#!/usr/bin/env python3
"""
Load data from JSON files into the database.
Updates: species, settlements, deforestation, rivers
"""

import json
import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'

def load_species(conn):
    """Load IUCN species data from JSON"""
    species_file = DATA_DIR / 'species' / 'park_mammals.json'
    if not species_file.exists():
        print("No species file found")
        return 0
    
    # Create table if needed
    conn.execute('''
        CREATE TABLE IF NOT EXISTS park_species (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            binomial TEXT NOT NULL,
            common_name TEXT,
            status TEXT,
            species_order TEXT,
            family TEXT,
            UNIQUE(park_id, binomial)
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ps_park ON park_species(park_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ps_status ON park_species(status)')
    
    with open(species_file) as f:
        data = json.load(f)
    
    count = 0
    for park_id, species_list in data.items():
        for sp in species_list:
            try:
                conn.execute('''
                    INSERT OR REPLACE INTO park_species 
                    (park_id, binomial, common_name, status, species_order, family)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    park_id,
                    sp.get('binomial', ''),
                    sp.get('common_name', ''),
                    sp.get('status', ''),
                    sp.get('order', ''),
                    sp.get('family', '')
                ))
                count += 1
            except Exception as e:
                print(f"Error loading species {sp.get('binomial')}: {e}")
    
    conn.commit()
    return count

def load_rivers_from_json(conn):
    """Verify rivers loaded from HydroRIVERS"""
    cursor = conn.execute('SELECT COUNT(*) FROM rivers')
    count = cursor.fetchone()[0]
    
    cursor = conn.execute('SELECT COUNT(*) FROM park_rivers')
    park_rivers = cursor.fetchone()[0]
    
    print(f"    Rivers: {count}, Park-river links: {park_rivers}")
    return count

def load_settlements_from_json(conn):
    """Verify settlements in DB"""
    cursor = conn.execute('SELECT COUNT(*) FROM park_settlements')
    count = cursor.fetchone()[0]
    
    cursor = conn.execute('SELECT COUNT(*) FROM park_settlements WHERE classification IS NOT NULL')
    classified = cursor.fetchone()[0]
    
    print(f"    Settlements: {count}, Classified: {classified}")
    return count

def load_deforestation_from_json(conn):
    """Verify deforestation in DB"""
    cursor = conn.execute('SELECT COUNT(*) FROM deforestation_events')
    count = cursor.fetchone()[0]
    
    cursor = conn.execute('SELECT COUNT(*) FROM deforestation_events WHERE classification IS NOT NULL')
    classified = cursor.fetchone()[0]
    
    print(f"    Deforestation events: {count}, Classified: {classified}")
    return count

def export_to_json(conn):
    """Export current DB data to JSON for production backup"""
    export_dir = DATA_DIR / 'export'
    export_dir.mkdir(exist_ok=True)
    
    # Export species
    cursor = conn.execute('SELECT park_id, binomial, common_name, status, species_order, family FROM park_species')
    species_data = {}
    for row in cursor:
        park_id = row[0]
        if park_id not in species_data:
            species_data[park_id] = []
        species_data[park_id].append({
            'binomial': row[1],
            'common_name': row[2],
            'status': row[3],
            'order': row[4],
            'family': row[5]
        })
    
    with open(export_dir / 'park_species.json', 'w') as f:
        json.dump(species_data, f)
    print(f"    Exported species for {len(species_data)} parks")
    
    # Export classified settlements
    cursor = conn.execute('''
        SELECT park_id, id, lat, lon, area_m2, population_est, nearest_place, 
               classification, classification_confidence, narrative
        FROM park_settlements WHERE classification IS NOT NULL
    ''')
    settlements_data = {}
    for row in cursor:
        park_id = row[0]
        if park_id not in settlements_data:
            settlements_data[park_id] = []
        settlements_data[park_id].append({
            'id': row[1],
            'lat': row[2],
            'lon': row[3],
            'area_m2': row[4],
            'population_est': row[5],
            'nearest_place': row[6],
            'classification': row[7],
            'confidence': row[8],
            'narrative': row[9]
        })
    
    with open(export_dir / 'classified_settlements.json', 'w') as f:
        json.dump(settlements_data, f)
    print(f"    Exported classified settlements for {len(settlements_data)} parks")
    
    # Export classified deforestation
    cursor = conn.execute('''
        SELECT park_id, id, year, area_km2, lat, lon,
               classification, classification_confidence, narrative
        FROM deforestation_events WHERE classification IS NOT NULL
    ''')
    defo_data = {}
    for row in cursor:
        park_id = row[0]
        if park_id not in defo_data:
            defo_data[park_id] = []
        defo_data[park_id].append({
            'id': row[1],
            'year': row[2],
            'area_km2': row[3],
            'lat': row[4],
            'lon': row[5],
            'classification': row[6],
            'confidence': row[7],
            'narrative': row[8]
        })
    
    with open(export_dir / 'classified_deforestation.json', 'w') as f:
        json.dump(defo_data, f)
    print(f"    Exported classified deforestation for {len(defo_data)} parks")

def main():
    print("Loading JSON data into database...")
    print("=" * 50)
    
    conn = sqlite3.connect(DB_PATH)
    
    # Load species
    print("\n[1] Loading IUCN species...")
    species_count = load_species(conn)
    print(f"    Loaded {species_count} species records")
    
    # Verify rivers
    print("\n[2] Checking rivers...")
    load_rivers_from_json(conn)
    
    # Verify settlements
    print("\n[3] Checking settlements...")
    load_settlements_from_json(conn)
    
    # Verify deforestation
    print("\n[4] Checking deforestation...")
    load_deforestation_from_json(conn)
    
    # Export to JSON
    print("\n[5] Exporting to JSON for production...")
    export_to_json(conn)
    
    conn.close()
    
    print("\n" + "=" * 50)
    print("Data loading complete!")

if __name__ == '__main__':
    main()
