#!/usr/bin/env python3
"""
Load deforestation and settlement polygons from JSON files into feature_geometries table.
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path('db.sqlite3')
DEFOREST_DIR = Path('data/feature_geometries/deforestation')
SETTLEMENT_DIR = Path('data/feature_geometries/settlement')

def load_polygons():
    conn = sqlite3.connect(DB_PATH)
    
    # Clear existing polygon data
    conn.execute("DELETE FROM feature_geometries WHERE feature_type IN ('deforestation', 'settlement')")
    
    deforest_count = 0
    settlement_count = 0
    
    # Load deforestation
    print("Loading deforestation polygons...")
    for json_file in sorted(DEFOREST_DIR.glob('*.json')):
        park_id = json_file.stem
        with open(json_file) as f:
            features = json.load(f)
        
        for feat in features:
            conn.execute('''
                INSERT INTO feature_geometries 
                (park_id, feature_id, feature_type, geojson, properties_json, start_date, end_date)
                VALUES (?, ?, 'deforestation', ?, ?, ?, ?)
            ''', (
                park_id,
                feat.get('feature_id'),
                json.dumps(feat.get('geojson')),
                json.dumps(feat.get('properties', {})),
                feat.get('start_date'),
                feat.get('end_date')
            ))
            deforest_count += 1
        
        if deforest_count % 1000 == 0:
            print(f"  {deforest_count} deforestation polygons...")
            conn.commit()
    
    conn.commit()
    print(f"  Loaded {deforest_count} deforestation polygons")
    
    # Load settlements
    print("Loading settlement polygons...")
    for json_file in sorted(SETTLEMENT_DIR.glob('*.json')):
        park_id = json_file.stem
        with open(json_file) as f:
            features = json.load(f)
        
        for feat in features:
            conn.execute('''
                INSERT INTO feature_geometries 
                (park_id, feature_id, feature_type, geojson, properties_json, start_date, end_date)
                VALUES (?, ?, 'settlement', ?, ?, ?, ?)
            ''', (
                park_id,
                feat.get('feature_id'),
                json.dumps(feat.get('geojson')),
                json.dumps(feat.get('properties', {})),
                feat.get('start_date'),
                feat.get('end_date')
            ))
            settlement_count += 1
        
        if settlement_count % 1000 == 0:
            print(f"  {settlement_count} settlement polygons...")
            conn.commit()
    
    conn.commit()
    print(f"  Loaded {settlement_count} settlement polygons")
    
    conn.close()
    print(f"\nTotal: {deforest_count} deforestation + {settlement_count} settlement polygons")

if __name__ == '__main__':
    load_polygons()
