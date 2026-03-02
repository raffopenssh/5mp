#!/usr/bin/env python3
"""Link settlement IDs to feature geometries (one-off fix).

NOTE: This functionality is now integrated into import_events_from_json.py
This script is kept for historical reference and one-off repairs.

Updates properties_json in feature_geometries to include settlement_id
based on the polygon_ids stored in park_settlements.

This allows the UI to pin individual settlements by their database ID.
"""

import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / 'db.sqlite3'

def link_settlement_ids():
    """Update feature_geometries with settlement_id from park_settlements"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Get all settlements with polygon_ids
    settlements = conn.execute('''
        SELECT id, park_id, polygon_ids
        FROM park_settlements
        WHERE polygon_ids IS NOT NULL AND polygon_ids != ''
    ''').fetchall()
    
    updated = 0
    errors = 0
    
    for s in settlements:
        settlement_id = s['id']
        park_id = s['park_id']
        polygon_ids = s['polygon_ids']
        
        if not polygon_ids:
            continue
            
        # Split comma-separated polygon IDs
        feature_ids = [fid.strip() for fid in polygon_ids.split(',')]
        
        for feature_id in feature_ids:
            try:
                # Get current properties
                row = conn.execute('''
                    SELECT properties_json
                    FROM feature_geometries
                    WHERE feature_id = ? AND park_id = ?
                ''', (feature_id, park_id)).fetchone()
                
                if not row:
                    print(f"Warning: Feature {feature_id} not found in feature_geometries")
                    errors += 1
                    continue
                
                # Parse properties
                props = json.loads(row['properties_json']) if row['properties_json'] else {}
                
                # Add settlement_id
                props['settlement_id'] = settlement_id
                
                # Update
                conn.execute('''
                    UPDATE feature_geometries
                    SET properties_json = ?
                    WHERE feature_id = ? AND park_id = ?
                ''', (json.dumps(props), feature_id, park_id))
                
                updated += 1
                
            except Exception as e:
                print(f"Error updating {feature_id}: {e}")
                errors += 1
    
    conn.commit()
    conn.close()
    
    print(f"Updated {updated} settlement feature geometries")
    if errors > 0:
        print(f"Errors: {errors}")
    
    return updated

if __name__ == '__main__':
    link_settlement_ids()
