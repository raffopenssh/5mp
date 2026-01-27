#!/usr/bin/env python3
"""
Background job to analyze fire data for all parks.
Stores results in SQLite database.
"""

import os
import sys
import json
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent))
from fire_group_detection import (
    load_fire_data, detect_daily_clusters, 
    track_clusters, classify_trajectory, distance_km
)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
FIRE_DIR = DATA_DIR / "fire"
DB_PATH = BASE_DIR / "db.sqlite3"

# Country code to FIRMS country name mapping
COUNTRY_MAP = {
    "AGO": "Angola",
    "BEN": "Benin",
    "BWA": "Botswana",
    "CAF": "Central_African_Republic",
    "CIV": "Cote_dIvoire",
    "CMR": "Cameroon",
    "COD": "Democratic_Republic_of_the_Congo",
    "COG": "Republic_of_the_Congo",
    "ETH": "Ethiopia",
    "GAB": "Gabon",
    "GHA": "Ghana",
    "KEN": "Kenya",
    "MOZ": "Mozambique",
    "MWI": "Malawi",
    "NAM": "Namibia",
    "NGA": "Nigeria",
    "SDN": "Sudan",
    "SSD": "South_Sudan",
    "TCD": "Chad",
    "TZA": "Tanzania",
    "UGA": "Uganda",
    "ZAF": "South_Africa",
    "ZMB": "Zambia",
    "ZWE": "Zimbabwe",
}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_fire_analysis_table():
    """Create table to store fire analysis results."""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS park_fire_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            year INTEGER NOT NULL,
            total_fires INTEGER,
            dry_season_fires INTEGER,
            transhumance_groups INTEGER,
            transhumance_fires INTEGER,
            avg_transhumance_speed REAL,
            herder_groups INTEGER,
            management_groups INTEGER,
            village_groups INTEGER,
            peak_month INTEGER,
            analysis_json TEXT,
            analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(park_id, year)
        )
    """)
    conn.commit()
    conn.close()

def load_keystones():
    """Load keystone parks data."""
    with open(DATA_DIR / "keystones_basic.json") as f:
        return json.load(f)

def get_park_bbox(park, buffer_km=50):
    """Get bounding box for park with buffer."""
    lat = park['coordinates']['lat']
    lon = park['coordinates']['lon']
    # Rough: 1 degree ≈ 111km
    buffer_deg = buffer_km / 111.0
    return (lat - buffer_deg, lat + buffer_deg, lon - buffer_deg, lon + buffer_deg)

def analyze_park(park, year):
    """Run fire analysis for a single park and year."""
    country_code = park['country_code']
    country_name = COUNTRY_MAP.get(country_code)
    
    if not country_name:
        return None
    
    filepath = FIRE_DIR / f"viirs-jpss1_{year}_{country_name}.csv"
    if not filepath.exists():
        return None
    
    # Load fire data for park area
    bbox = get_park_bbox(park)
    lat_min, lat_max, lon_min, lon_max = bbox
    
    try:
        df = pd.read_csv(filepath)
        park_fires = df[
            (df['latitude'] >= lat_min) & (df['latitude'] <= lat_max) &
            (df['longitude'] >= lon_min) & (df['longitude'] <= lon_max)
        ].copy()
        
        if len(park_fires) < 10:
            return {
                'total_fires': len(park_fires),
                'dry_season_fires': 0,
                'groups': {}
            }
        
        park_fires['date'] = pd.to_datetime(park_fires['acq_date'])
        
        # Dry season varies by hemisphere
        lat = park['coordinates']['lat']
        if lat > 0:  # Northern hemisphere
            dry_months = [1, 2, 3, 11, 12]
        else:  # Southern hemisphere
            dry_months = [5, 6, 7, 8, 9, 10]
        
        dry = park_fires[park_fires['date'].dt.month.isin(dry_months)]
        
        # Run group detection
        daily_clusters = detect_daily_clusters(dry)
        trajectories = track_clusters(daily_clusters)
        
        groups = defaultdict(list)
        for traj in trajectories:
            typ, metrics = classify_trajectory(traj)
            groups[typ].append(metrics)
        
        # Calculate summary stats
        trans = groups.get('transhumance', []) + groups.get('transhumance_slow', [])
        herder = groups.get('herder_local', []) + groups.get('herder_fast', [])
        mgmt = groups.get('management_fast', []) + groups.get('management_vehicle', [])
        village = groups.get('village_persistent', []) + groups.get('local_stationary', [])
        
        # Peak month
        if len(dry) > 0:
            monthly = dry.groupby(dry['date'].dt.month).size()
            peak_month = monthly.idxmax() if len(monthly) > 0 else None
        else:
            peak_month = None
        
        return {
            'total_fires': len(park_fires),
            'dry_season_fires': len(dry),
            'transhumance_groups': len(trans),
            'transhumance_fires': sum(g['fires'] for g in trans),
            'avg_transhumance_speed': np.mean([g['avg_speed_km_day'] for g in trans]) if trans else 0,
            'herder_groups': len(herder),
            'management_groups': len(mgmt),
            'village_groups': len(village),
            'peak_month': peak_month,
            'groups': dict(groups)
        }
        
    except Exception as e:
        print(f"Error analyzing {park['id']} for {year}: {e}")
        return None

def save_analysis(park_id, year, results):
    """Save analysis results to database."""
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO park_fire_analysis
        (park_id, year, total_fires, dry_season_fires, transhumance_groups,
         transhumance_fires, avg_transhumance_speed, herder_groups,
         management_groups, village_groups, peak_month, analysis_json, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        park_id, year,
        results.get('total_fires'),
        results.get('dry_season_fires'),
        results.get('transhumance_groups'),
        results.get('transhumance_fires'),
        results.get('avg_transhumance_speed'),
        results.get('herder_groups'),
        results.get('management_groups'),
        results.get('village_groups'),
        results.get('peak_month'),
        json.dumps(results.get('groups', {})),
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

def run_analysis(park_ids=None, years=None):
    """Run fire analysis for specified parks and years."""
    init_fire_analysis_table()
    
    keystones = load_keystones()
    if park_ids:
        keystones = [p for p in keystones if p['id'] in park_ids]
    
    years = years or [2022, 2023, 2024]
    
    total = len(keystones) * len(years)
    done = 0
    
    for park in keystones:
        for year in years:
            done += 1
            print(f"[{done}/{total}] Analyzing {park['id']} for {year}...")
            
            results = analyze_park(park, year)
            if results:
                save_analysis(park['id'], year, results)
                print(f"  -> {results.get('total_fires', 0)} fires, "
                      f"{results.get('transhumance_groups', 0)} transhumance groups")
            else:
                print(f"  -> No data available")
    
    print("\nAnalysis complete!")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--park', help='Specific park ID')
    parser.add_argument('--year', type=int, help='Specific year')
    args = parser.parse_args()
    
    park_ids = [args.park] if args.park else None
    years = [args.year] if args.year else None
    
    run_analysis(park_ids, years)
