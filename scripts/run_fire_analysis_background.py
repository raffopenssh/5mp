#!/usr/bin/env python3
"""
Background Fire Analysis Job
Runs analysis for all parks and years, saving progress to resume.
Can run for hours. Monitor with: tail -f /tmp/fire_analysis_bg.log
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
import logging
import traceback

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('/tmp/fire_analysis_bg.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
FIRE_DIR = DATA_DIR / "fire"
DB_PATH = BASE_DIR / "db.sqlite3"

# Country code to VIIRS filename mapping
COUNTRY_MAP = {
    "AGO": "Angola", "BEN": "Benin", "BWA": "Botswana",
    "CAF": "Central_African_Republic", "CMR": "Cameroon",
    "COD": "Democratic_Republic_of_the_Congo",
    "COG": "Republic_of_the_Congo", "ETH": "Ethiopia",
    "GAB": "Gabon", "GHA": "Ghana", "KEN": "Kenya",
    "MOZ": "Mozambique", "MWI": "Malawi", "NAM": "Namibia",
    "NGA": "Nigeria", "RWA": "Rwanda", "SDN": "Sudan",
    "SSD": "South_Sudan", "TCD": "Chad", "TZA": "Tanzania",
    "UGA": "Uganda", "ZAF": "South_Africa", "ZMB": "Zambia",
    "ZWE": "Zimbabwe",
}

YEARS = [2018, 2019, 2020, 2021, 2022, 2023, 2024]

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def load_keystones():
    with open(DATA_DIR / "keystones_basic.json") as f:
        return json.load(f)

def get_park_bbox(park, buffer_km=50):
    lat = park['coordinates']['lat']
    lon = park['coordinates']['lon']
    buffer_deg = buffer_km / 111.0
    return (lat - buffer_deg, lat + buffer_deg, lon - buffer_deg, lon + buffer_deg)

def already_processed(park_id, year):
    """Check if this park-year already has analysis"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM park_fire_analysis WHERE park_id = ? AND year = ?",
        (park_id, year)
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0

def load_fire_data(country_code, year):
    """Load fire CSV for a country-year"""
    country_name = COUNTRY_MAP.get(country_code)
    if not country_name:
        return None
    
    filepath = FIRE_DIR / f"viirs-jpss1_{year}_{country_name}.csv"
    if not filepath.exists():
        return None
    
    try:
        df = pd.read_csv(filepath)
        df['date'] = pd.to_datetime(df['acq_date'])
        return df
    except Exception as e:
        logger.error(f"Error loading {filepath}: {e}")
        return None

def analyze_park_fires(df, park, year):
    """Analyze fire data for a park"""
    bbox = get_park_bbox(park)
    lat_min, lat_max, lon_min, lon_max = bbox
    
    park_fires = df[
        (df['latitude'] >= lat_min) & (df['latitude'] <= lat_max) &
        (df['longitude'] >= lon_min) & (df['longitude'] <= lon_max)
    ]
    
    if len(park_fires) < 10:
        return {
            'total_fires': len(park_fires),
            'dry_season_fires': 0,
            'transhumance_groups': 0,
            'transhumance_fires': 0,
            'avg_transhumance_speed': 0,
            'herder_groups': 0,
            'management_groups': 0,
            'village_groups': 0,
            'peak_month': None,
            'groups': {}
        }
    
    # Determine dry season based on hemisphere
    lat = park['coordinates']['lat']
    if lat > 0:  # Northern hemisphere
        dry_months = [1, 2, 3, 11, 12]
    else:  # Southern hemisphere
        dry_months = [5, 6, 7, 8, 9, 10]
    
    dry = park_fires[park_fires['date'].dt.month.isin(dry_months)]
    
    # Simple monthly stats (skip complex group detection for speed)
    monthly = dry.groupby(dry['date'].dt.month).size().to_dict()
    peak_month = max(monthly, key=monthly.get) if monthly else None
    
    return {
        'total_fires': len(park_fires),
        'dry_season_fires': len(dry),
        'transhumance_groups': 0,  # TODO: Run full group detection
        'transhumance_fires': 0,
        'avg_transhumance_speed': 0,
        'herder_groups': 0,
        'management_groups': 0,
        'village_groups': 0,
        'peak_month': peak_month,
        'groups': {}
    }

def save_analysis(park_id, year, results):
    """Save analysis to database"""
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

def main():
    logger.info("="*60)
    logger.info("Starting Fire Analysis Background Job")
    logger.info("="*60)
    
    keystones = load_keystones()
    logger.info(f"Loaded {len(keystones)} parks")
    
    # Group parks by country for efficient data loading
    parks_by_country = defaultdict(list)
    for park in keystones:
        parks_by_country[park['country_code']].append(park)
    
    total_tasks = len(keystones) * len(YEARS)
    done = 0
    skipped = 0
    errors = 0
    
    for country_code, parks in parks_by_country.items():
        logger.info(f"\n--- Processing {country_code} ({len(parks)} parks) ---")
        
        for year in YEARS:
            # Load country data once per year
            df = load_fire_data(country_code, year)
            
            for park in parks:
                done += 1
                park_id = park['id']
                
                # Skip if already processed
                if already_processed(park_id, year):
                    skipped += 1
                    continue
                
                try:
                    if df is None:
                        # No data for this country-year
                        save_analysis(park_id, year, {
                            'total_fires': 0, 'dry_season_fires': 0,
                            'transhumance_groups': 0, 'transhumance_fires': 0,
                            'avg_transhumance_speed': 0, 'herder_groups': 0,
                            'management_groups': 0, 'village_groups': 0,
                            'peak_month': None, 'groups': {}
                        })
                    else:
                        results = analyze_park_fires(df, park, year)
                        save_analysis(park_id, year, results)
                        
                        if results['total_fires'] > 0:
                            logger.info(f"[{done}/{total_tasks}] {park_id} {year}: "
                                       f"{results['total_fires']} fires, peak month {results['peak_month']}")
                
                except Exception as e:
                    errors += 1
                    logger.error(f"Error processing {park_id} {year}: {e}")
                    traceback.print_exc()
    
    logger.info("="*60)
    logger.info(f"Fire Analysis Complete!")
    logger.info(f"Processed: {done - skipped}, Skipped: {skipped}, Errors: {errors}")
    logger.info("="*60)

if __name__ == '__main__':
    main()
