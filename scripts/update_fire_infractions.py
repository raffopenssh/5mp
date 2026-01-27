#!/usr/bin/env python3
"""
Update fire analysis with infraction data for all parks.
Adds: total_infractions, infraction_rate, monthly_stats_json
"""

import json
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime

try:
    from shapely.geometry import shape, Point
    from shapely.prepared import prep
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False
    print("ERROR: shapely required. Run: pip install shapely")
    exit(1)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
FIRE_DIR = DATA_DIR / "fire"
DB_PATH = BASE_DIR / "db.sqlite3"

# Country code to FIRMS filename mapping
COUNTRY_MAP = {
    "AGO": "Angola",
    "BWA": "Botswana",
    "CAF": "Central_African_Republic",
    "CMR": "Cameroon",
    "COD": "Democratic_Republic_of_the_Congo",
    "COG": "Republic_of_the_Congo",
    "ETH": "Ethiopia",
    "GAB": "Gabon",
    "KEN": "Kenya",
    "MOZ": "Mozambique",
    "MWI": "Malawi",
    "NAM": "Namibia",
    "RWA": "Rwanda",
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

def load_keystones_with_boundaries():
    path = DATA_DIR / "keystones_with_boundaries.json"
    with open(path) as f:
        return json.load(f)

def get_park_boundary(park):
    geom = park.get('geometry')
    if not geom:
        return None
    try:
        return prep(shape(geom))
    except Exception as e:
        print(f"  Error parsing geometry for {park['id']}: {e}")
        return None

def get_park_bbox(park, buffer_km=50):
    lat = park['coordinates']['lat']
    lon = park['coordinates']['lon']
    buffer_deg = buffer_km / 111.0
    return (lat - buffer_deg, lat + buffer_deg, lon - buffer_deg, lon + buffer_deg)

def find_fire_file(country_code, year):
    """Find fire data file for a country and year."""
    country_name = COUNTRY_MAP.get(country_code)
    if not country_name:
        return None
    
    # Try new nested structure first
    path = FIRE_DIR / "viirs-jpss1" / str(year) / f"viirs-jpss1_{year}_{country_name}.csv"
    if path.exists():
        return path
    
    # Fall back to flat structure
    path = FIRE_DIR / f"viirs-jpss1_{year}_{country_name}.csv"
    if path.exists():
        return path
    
    return None

def analyze_infractions(park, fire_path, year):
    park_id = park['id']
    bbox = get_park_bbox(park)
    boundary = get_park_boundary(park)
    
    if boundary is None:
        return None
    
    try:
        df = pd.read_csv(fire_path)
        lat_min, lat_max, lon_min, lon_max = bbox
        df = df[(df['latitude'] >= lat_min) & (df['latitude'] <= lat_max) &
                (df['longitude'] >= lon_min) & (df['longitude'] <= lon_max)]
        
        if len(df) == 0:
            return None
        
        df['date'] = pd.to_datetime(df['acq_date'])
        dry = df[df['date'].dt.month.isin([1, 2, 3, 11, 12])]
        
        if len(dry) == 0:
            return {'total_infractions': 0, 'infraction_rate': 0, 'peak_day': None, 'peak_count': 0, 'monthly_stats': []}
        
        infraction_count = 0
        daily_infractions = {}
        monthly_stats = {m: {'total': 0, 'infractions': 0} for m in [1, 2, 3, 11, 12]}
        
        for _, row in dry.iterrows():
            pt = Point(row['longitude'], row['latitude'])
            month = row['date'].month
            date_str = row['acq_date']
            
            if month in monthly_stats:
                monthly_stats[month]['total'] += 1
            
            if boundary.contains(pt):
                infraction_count += 1
                daily_infractions[date_str] = daily_infractions.get(date_str, 0) + 1
                if month in monthly_stats:
                    monthly_stats[month]['infractions'] += 1
        
        peak_day = None
        peak_count = 0
        if daily_infractions:
            peak_day = max(daily_infractions, key=daily_infractions.get)
            peak_count = daily_infractions[peak_day]
        
        monthly_list = []
        for m in [11, 12, 1, 2, 3]:
            stats = monthly_stats[m]
            rate = (stats['infractions'] / stats['total'] * 100) if stats['total'] > 0 else 0
            monthly_list.append({
                'month': m,
                'total': stats['total'],
                'infractions': stats['infractions'],
                'rate': round(rate, 1)
            })
        
        return {
            'total_infractions': infraction_count,
            'infraction_rate': round(infraction_count / len(dry) * 100, 1) if len(dry) > 0 else 0,
            'peak_day': peak_day,
            'peak_count': peak_count,
            'monthly_stats': monthly_list
        }
        
    except Exception as e:
        print(f"  Error analyzing {park_id}: {e}")
        import traceback
        traceback.print_exc()
        return None

def update_park_analysis(park_id, year, results):
    conn = get_db()
    
    cursor = conn.execute("PRAGMA table_info(park_fire_analysis)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if 'total_infractions' not in columns:
        conn.execute("ALTER TABLE park_fire_analysis ADD COLUMN total_infractions INTEGER DEFAULT 0")
    if 'infraction_rate' not in columns:
        conn.execute("ALTER TABLE park_fire_analysis ADD COLUMN infraction_rate REAL DEFAULT 0")
    if 'peak_infraction_day' not in columns:
        conn.execute("ALTER TABLE park_fire_analysis ADD COLUMN peak_infraction_day TEXT")
    if 'peak_infraction_count' not in columns:
        conn.execute("ALTER TABLE park_fire_analysis ADD COLUMN peak_infraction_count INTEGER DEFAULT 0")
    if 'monthly_stats_json' not in columns:
        conn.execute("ALTER TABLE park_fire_analysis ADD COLUMN monthly_stats_json TEXT")
    
    conn.execute("""
        UPDATE park_fire_analysis
        SET total_infractions = ?,
            infraction_rate = ?,
            peak_infraction_day = ?,
            peak_infraction_count = ?,
            monthly_stats_json = ?
        WHERE park_id = ? AND year = ?
    """, (
        results['total_infractions'],
        results['infraction_rate'],
        results['peak_day'],
        results['peak_count'],
        json.dumps(results['monthly_stats']),
        park_id, year
    ))
    conn.commit()
    conn.close()

def main():
    print("Loading keystones with boundaries...")
    keystones = load_keystones_with_boundaries()
    print(f"Loaded {len(keystones)} parks\n")
    
    years = [2022, 2023, 2024]
    total_updated = 0
    
    # Process by country to load fire data once per country/year
    parks_by_country = {}
    for park in keystones:
        cc = park['country_code']
        if cc not in parks_by_country:
            parks_by_country[cc] = []
        parks_by_country[cc].append(park)
    
    for country_code in sorted(parks_by_country.keys()):
        parks = parks_by_country[country_code]
        
        for year in years:
            fire_path = find_fire_file(country_code, year)
            if not fire_path:
                continue
            
            print(f"{country_code} {year}: {len(parks)} parks, file={fire_path.name}")
            
            for park in parks:
                results = analyze_infractions(park, fire_path, year)
                if results and results['total_infractions'] >= 0:
                    update_park_analysis(park['id'], year, results)
                    if results['total_infractions'] > 0:
                        print(f"  {park['id']}: {results['total_infractions']} infractions ({results['infraction_rate']}%)")
                    total_updated += 1
    
    print(f"\n✓ Updated {total_updated} park-year records with infraction data")

if __name__ == '__main__':
    main()
