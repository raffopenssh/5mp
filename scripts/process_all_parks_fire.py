#!/usr/bin/env python3
"""
Background job to process ALL fire data for ALL African parks.

This script:
1. Loads fire detections into the database (fire_detections table)
2. Runs group infraction analysis for all parks and years
3. Saves progress to resume if interrupted
4. Can run for hours as a background job

Usage:
    nohup python scripts/process_all_parks_fire.py > logs/fire_processing.log 2>&1 &

Monitor:
    tail -f logs/fire_processing.log
    sqlite3 db.sqlite3 "SELECT COUNT(*) FROM fire_detections"
    sqlite3 db.sqlite3 "SELECT park_id, year, total_groups FROM park_group_infractions ORDER BY analyzed_at DESC LIMIT 10"
"""

import json
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from glob import glob
import sys
import os
import traceback

# Add scripts to path
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

DATA_DIR = BASE_DIR / "data"
FIRE_DIR = DATA_DIR / "fire" / "viirs-jpss"
DB_PATH = BASE_DIR / "db.sqlite3"
PROGRESS_FILE = BASE_DIR / "logs" / "fire_progress.json"

# Years to process
YEARS = [2018, 2019, 2020, 2021, 2022, 2023, 2024]

def log(msg):
    """Print with timestamp."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Create tables if they don't exist."""
    conn = get_db()
    
    # Fire detections table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fire_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            acq_date TEXT NOT NULL,
            acq_time TEXT,
            frp REAL,
            confidence TEXT,
            bright_ti4 REAL,
            bright_ti5 REAL,
            satellite TEXT,
            country TEXT,
            year INTEGER,
            UNIQUE(latitude, longitude, acq_date, acq_time)
        )
    """)
    
    # Create indexes for fast querying
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fire_lat ON fire_detections(latitude)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fire_lon ON fire_detections(longitude)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fire_date ON fire_detections(acq_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fire_year ON fire_detections(year)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fire_country ON fire_detections(country)")
    
    # Park group infractions table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS park_group_infractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            year INTEGER NOT NULL,
            total_groups INTEGER,
            transhumance_groups INTEGER,
            herder_groups INTEGER,
            avg_days_burning REAL,
            median_days_burning REAL,
            max_days_burning INTEGER,
            total_fires_inside INTEGER,
            groups_transited INTEGER,
            groups_stopped_inside INTEGER,
            groups_stopped_after INTEGER,
            avg_days_tracked_before REAL,
            avg_days_tracked_after REAL,
            trajectories_json TEXT,
            analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(park_id, year)
        )
    """)
    
    # Progress tracking table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processing_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            task_key TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at DATETIME,
            completed_at DATETIME,
            error_message TEXT,
            UNIQUE(task_type, task_key)
        )
    """)
    
    conn.commit()
    conn.close()
    log("Database initialized")

def is_task_completed(task_type, task_key):
    """Check if a task has been completed."""
    conn = get_db()
    row = conn.execute(
        "SELECT status FROM processing_progress WHERE task_type=? AND task_key=?",
        (task_type, task_key)
    ).fetchone()
    conn.close()
    return row and row['status'] == 'completed'

def mark_task_started(task_type, task_key):
    """Mark a task as started."""
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO processing_progress (task_type, task_key, status, started_at)
        VALUES (?, ?, 'running', ?)
    """, (task_type, task_key, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def mark_task_completed(task_type, task_key):
    """Mark a task as completed."""
    conn = get_db()
    conn.execute("""
        UPDATE processing_progress SET status='completed', completed_at=?
        WHERE task_type=? AND task_key=?
    """, (datetime.now().isoformat(), task_type, task_key))
    conn.commit()
    conn.close()

def mark_task_failed(task_type, task_key, error):
    """Mark a task as failed."""
    conn = get_db()
    conn.execute("""
        UPDATE processing_progress SET status='failed', error_message=?
        WHERE task_type=? AND task_key=?
    """, (str(error)[:500], task_type, task_key))
    conn.commit()
    conn.close()

def extract_country_from_filename(filename):
    """Extract country name from filename like viirs-jpss1_2022_Angola.csv"""
    name = Path(filename).stem  # viirs-jpss1_2022_Angola
    parts = name.split('_')
    if len(parts) >= 3:
        return '_'.join(parts[2:])  # Handle multi-word countries
    return None

def load_fire_csv_to_db(csv_path, year):
    """Load a single CSV file into the database."""
    country = extract_country_from_filename(csv_path)
    task_key = f"{year}_{country}"
    
    if is_task_completed('load_fire', task_key):
        return 0
    
    mark_task_started('load_fire', task_key)
    
    try:
        df = pd.read_csv(csv_path)
        if len(df) == 0:
            mark_task_completed('load_fire', task_key)
            return 0
        
        df['country'] = country
        df['year'] = year
        
        # Prepare data for insertion
        records = df[['latitude', 'longitude', 'acq_date', 'acq_time', 'frp', 
                      'confidence', 'bright_ti4', 'bright_ti5', 'satellite', 
                      'country', 'year']].values.tolist()
        
        conn = get_db()
        inserted = 0
        batch_size = 10000
        
        for i in range(0, len(records), batch_size):
            batch = records[i:i+batch_size]
            try:
                conn.executemany("""
                    INSERT OR IGNORE INTO fire_detections 
                    (latitude, longitude, acq_date, acq_time, frp, confidence, 
                     bright_ti4, bright_ti5, satellite, country, year)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch)
                inserted += conn.total_changes
            except Exception as e:
                log(f"  Batch insert error: {e}")
        
        conn.commit()
        conn.close()
        
        mark_task_completed('load_fire', task_key)
        return len(records)
        
    except Exception as e:
        mark_task_failed('load_fire', task_key, e)
        raise

def load_all_fire_data():
    """Load all fire CSV files into database."""
    log("=" * 60)
    log("PHASE 1: Loading fire data into database")
    log("=" * 60)
    
    total_loaded = 0
    
    for year in YEARS:
        year_dir = FIRE_DIR / str(year)
        if not year_dir.exists():
            log(f"Year {year}: directory not found")
            continue
        
        csv_files = list(year_dir.glob("*.csv"))
        log(f"Year {year}: {len(csv_files)} files")
        
        for csv_path in sorted(csv_files):
            country = extract_country_from_filename(csv_path)
            task_key = f"{year}_{country}"
            
            if is_task_completed('load_fire', task_key):
                continue
            
            try:
                count = load_fire_csv_to_db(csv_path, year)
                if count > 0:
                    log(f"  {country}: {count:,} records")
                    total_loaded += count
            except Exception as e:
                log(f"  {country}: ERROR - {e}")
    
    # Get total count
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM fire_detections").fetchone()[0]
    conn.close()
    
    log(f"Total fire detections in database: {total:,}")
    return total

def run_park_analysis():
    """Run group infraction analysis for all parks."""
    log("=" * 60)
    log("PHASE 2: Running park analysis")
    log("=" * 60)
    
    # Import analysis module
    try:
        from analyze_group_infractions import analyze_park, save_results, load_keystones
    except ImportError as e:
        log(f"ERROR: Could not import analyze_group_infractions: {e}")
        return
    
    # Load parks
    keystones = load_keystones()
    log(f"Loaded {len(keystones)} parks")
    
    total_analyzed = 0
    total_skipped = 0
    total_errors = 0
    
    for park in keystones:
        park_id = park['id']
        
        for year in YEARS:
            task_key = f"{park_id}_{year}"
            
            if is_task_completed('analyze_park', task_key):
                total_skipped += 1
                continue
            
            mark_task_started('analyze_park', task_key)
            
            try:
                results = analyze_park(park, year)
                
                if results:
                    save_results(park_id, year, results)
                    s = results['summary']
                    log(f"{park_id} {year}: {s['total_groups']} groups, "
                        f"avg {s['avg_days_burning']:.1f}d burning")
                    total_analyzed += 1
                else:
                    log(f"{park_id} {year}: no groups")
                
                mark_task_completed('analyze_park', task_key)
                
            except Exception as e:
                log(f"{park_id} {year}: ERROR - {e}")
                mark_task_failed('analyze_park', task_key, e)
                total_errors += 1
    
    log(f"Analysis complete: {total_analyzed} analyzed, {total_skipped} skipped, {total_errors} errors")

def main():
    """Main entry point."""
    log("Starting fire data processing job")
    log(f"Fire data directory: {FIRE_DIR}")
    log(f"Database: {DB_PATH}")
    log(f"Years to process: {YEARS}")
    
    # Create logs directory
    (BASE_DIR / "logs").mkdir(exist_ok=True)
    
    # Initialize database
    init_database()
    
    # Phase 1: Load fire data
    try:
        load_all_fire_data()
    except Exception as e:
        log(f"FATAL ERROR in Phase 1: {e}")
        traceback.print_exc()
    
    # Phase 2: Run analysis
    try:
        run_park_analysis()
    except Exception as e:
        log(f"FATAL ERROR in Phase 2: {e}")
        traceback.print_exc()
    
    log("Job complete!")

if __name__ == '__main__':
    main()
