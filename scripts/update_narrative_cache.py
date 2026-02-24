#!/usr/bin/env python3
"""Update fire_narrative_cache from JSON files in data/export/fire_narratives/"""
import json
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "db.sqlite3"
NARRATIVES_DIR = Path(__file__).parent.parent / "data/export/fire_narratives"

def main():
    conn = sqlite3.connect(str(DB_PATH))
    count = 0
    
    for f in NARRATIVES_DIR.glob('*.json'):
        data = json.load(open(f))
        park_id = f.stem
        years = data.get('trend', {}).get('years', [])
        year_nums = [y.get('year', 2020) if isinstance(y, dict) else y for y in years]
        from_year = min(year_nums) if year_nums else 2020
        to_year = max(year_nums) if year_nums else 2025
        
        conn.execute('''
            INSERT OR REPLACE INTO fire_narrative_cache 
            (park_id, narrative_json, computed_at, from_year, to_year) 
            VALUES (?, ?, ?, ?, ?)
        ''', (park_id, json.dumps(data), datetime.now().isoformat(), from_year, to_year))
        count += 1
    
    conn.commit()
    conn.close()
    print(f"Updated {count} narrative cache entries")

if __name__ == '__main__':
    main()
