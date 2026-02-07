#!/usr/bin/env python3
"""Load missing roads from osm_roadless_data into feature_geometries"""

import sqlite3
import json
import sys

DB_PATH = "db.sqlite3"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Find parks with roads_json but missing from feature_geometries
    missing = conn.execute("""
        SELECT o.park_id, o.roads_json 
        FROM osm_roadless_data o
        LEFT JOIN (
            SELECT DISTINCT park_id FROM feature_geometries WHERE feature_type = 'road'
        ) f ON o.park_id = f.park_id
        WHERE o.roads_json IS NOT NULL 
        AND o.roads_json != '' 
        AND f.park_id IS NULL
    """).fetchall()
    
    print(f"Found {len(missing)} parks with missing road features")
    
    total_inserted = 0
    for row in missing:
        park_id = row['park_id']
        try:
            data = json.loads(row['roads_json'])
            
            # Handle both formats: direct array or {count, sample} object
            if isinstance(data, dict):
                roads = data.get('sample', [])
            else:
                roads = data
                
            if not roads:
                print(f"  {park_id}: no road samples")
                continue
                
            # Insert each road as a feature
            for i, road in enumerate(roads):
                # Handle format: {type, coords, length_km}
                coords = road.get('coords', [])
                if not coords:
                    continue
                    
                geojson = json.dumps({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords
                    },
                    "properties": {
                        "highway": road.get('type', ''),
                        "length_km": road.get('length_km', 0)
                    }
                })
                
                feature_id = f"{park_id}_road_{i}"
                conn.execute("""
                    INSERT OR IGNORE INTO feature_geometries 
                    (feature_type, feature_id, park_id, geojson, created_at)
                    VALUES ('road', ?, ?, ?, datetime('now'))
                """, (feature_id, park_id, geojson))
                total_inserted += 1
            
            conn.commit()
            print(f"  {park_id}: {len(roads)} roads loaded")
            
        except Exception as e:
            print(f"  {park_id}: ERROR - {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\nTotal: {total_inserted} road features inserted")
    conn.close()

if __name__ == "__main__":
    main()
