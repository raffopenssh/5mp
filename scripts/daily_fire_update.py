#!/usr/bin/env python3
"""
Daily Fire Update Pipeline (v5) - True Incremental

Uses the same pipeline scripts with --incremental flags:
1. Download NRT fires from FIRMS API
2. Insert new fires to fire_detections (upsert)  
3. Run rebuild_fire_trajectories_v5.py --incremental for affected parks
4. Run load_fire_groups_to_db.py --incremental for affected parks
5. Run precompute_narratives_v5.py --incremental for affected parks

Groups can continue to grow as fires keep burning - trajectories extend.

Cron: 0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py --days 7 >> logs/daily_fire_$(date +\%Y\%m\%d).log 2>&1
"""

import os
import sys
import json
import sqlite3
import requests
import subprocess
import math
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

NASA_API_KEY = "REDACTED_FIRMS_KEY"
FIRMS_NRT_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

DEFAULT_DAYS = 7
INCREMENTAL_DAYS = 14  # Days window for incremental rebuild

def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)


class DailyFireUpdater:
    def __init__(self, days=DEFAULT_DAYS):
        self.days = days
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        self.parks = self._load_parks()
        self.affected_parks = set()
        
    def _load_parks(self):
        parks = {}
        try:
            with open(DATA_DIR / 'keystones_with_boundaries.json') as f:
                for p in json.load(f):
                    parks[p['id']] = {
                        'name': p.get('name', p['id']),
                        'bbox': self._get_bbox(p.get('geometry'))
                    }
        except Exception as e:
            log(f"Warning: Could not load parks: {e}")
        return parks
    
    def _get_bbox(self, geometry):
        if not geometry:
            return None
        coords = geometry.get('coordinates', [])
        if geometry['type'] == 'MultiPolygon':
            all_coords = [c for poly in coords for ring in poly for c in ring]
        elif geometry['type'] == 'Polygon':
            all_coords = [c for ring in coords for c in ring]
        else:
            return None
        if not all_coords:
            return None
        lons = [c[0] for c in all_coords]
        lats = [c[1] for c in all_coords]
        return (min(lons), min(lats), max(lons), max(lats))
    
    def download_nrt_fires(self):
        log(f"Step 1: Downloading NRT fires for last {self.days} days...")
        area = "-20,-35,55,40"  # Africa bbox
        url = f"{FIRMS_NRT_URL}/{NASA_API_KEY}/VIIRS_NOAA20_NRT/{area}/{self.days}"
        
        try:
            response = requests.get(url, timeout=300)
            response.raise_for_status()
            
            lines = response.text.strip().split('\n')
            if len(lines) < 2:
                log("  No fires found in NRT data")
                return []
            
            header = lines[0].split(',')
            fires = []
            for line in lines[1:]:
                values = line.split(',')
                if len(values) >= len(header):
                    fire = dict(zip(header, values))
                    fires.append(fire)
            
            log(f"  Downloaded {len(fires)} fire detections")
            return fires
            
        except Exception as e:
            log(f"  Error downloading NRT fires: {e}")
            return []
    
    def insert_fires(self, fires):
        if not fires:
            return 0
        
        log(f"Step 2: Inserting {len(fires)} fires into database...")
        
        inserted = 0
        skipped_invalid = 0
        cursor = self.conn.cursor()
        
        for fire in fires:
            try:
                lat = float(fire.get('latitude', 0))
                lon = float(fire.get('longitude', 0))
                
                # Skip invalid coordinates
                if lon == 0.0 or lat == 0.0:
                    skipped_invalid += 1
                    continue
                    
                acq_date = fire.get('acq_date', '')
                acq_time = fire.get('acq_time', '')
                bright_ti4 = float(fire.get('bright_ti4', 0))
                frp = float(fire.get('frp', 0))
                confidence = fire.get('confidence', '')
                satellite = fire.get('satellite', '1')
                scan = float(fire.get('scan', 0))
                track = float(fire.get('track', 0))
                daynight = fire.get('daynight', '')
                
                # Find which park this fire belongs to
                park_id = self._find_park(lon, lat)
                in_pa = 1 if park_id else 0
                
                cursor.execute('''
                    INSERT OR IGNORE INTO fire_detections 
                    (latitude, longitude, brightness, scan, track, acq_date, acq_time,
                     satellite, instrument, confidence, frp, daynight,
                     in_protected_area, protected_area_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (lat, lon, bright_ti4, scan, track, acq_date, acq_time,
                      satellite, 'VIIRS', confidence, frp, daynight,
                      in_pa, park_id))
                
                if cursor.rowcount > 0:
                    inserted += 1
                    if park_id:
                        self.affected_parks.add(park_id)
                        
            except Exception as e:
                pass
        
        self.conn.commit()
        log(f"  Inserted {inserted} new fire records")
        if skipped_invalid > 0:
            log(f"  Skipped {skipped_invalid} records with invalid coordinates")
        log(f"  Affected parks: {len(self.affected_parks)}")
        return inserted
    
    def _find_park(self, lon, lat):
        for park_id, park in self.parks.items():
            bbox = park.get('bbox')
            if bbox:
                minx, miny, maxx, maxy = bbox
                if minx - 0.5 <= lon <= maxx + 0.5 and miny - 0.5 <= lat <= maxy + 0.5:
                    return park_id
        return None
    
    def rebuild_groups_incremental(self):
        """Run rebuild_fire_trajectories_v5.py --incremental for affected parks"""
        if not self.affected_parks:
            log("Step 3: No parks affected, skipping group rebuild")
            return
        
        log(f"Step 3: Rebuilding fire groups (incremental) for {len(self.affected_parks)} parks...")
        
        for park_id in sorted(self.affected_parks):
            try:
                log(f"  Processing {park_id}...")
                result = subprocess.run(
                    ['python3', 'scripts/rebuild_fire_trajectories_v5.py', 
                     '--park', park_id, '--incremental', '--days', str(INCREMENTAL_DAYS)],
                    cwd=str(BASE_DIR),
                    capture_output=True,
                    text=True,
                    timeout=600
                )
                if result.returncode == 0:
                    # Extract summary from output
                    for line in result.stdout.split('\n'):
                        if park_id in line and 'fires ->' in line:
                            log(f"    {line.strip()}")
                            break
                else:
                    log(f"    Error: {result.stderr[:200]}")
                    
            except subprocess.TimeoutExpired:
                log(f"    Timeout for {park_id}")
            except Exception as e:
                log(f"    Error: {e}")
    
    def load_groups_incremental(self):
        """Run load_fire_groups_to_db.py for affected parks"""
        if not self.affected_parks:
            log("Step 4: No parks affected, skipping DB load")
            return
        
        log(f"Step 4: Loading fire groups to database for {len(self.affected_parks)} parks...")
        
        for park_id in sorted(self.affected_parks):
            try:
                result = subprocess.run(
                    ['python3', 'scripts/load_fire_groups_to_db.py', 
                     '--park', park_id, '--force'],
                    cwd=str(BASE_DIR),
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if result.returncode == 0:
                    # Extract count from output
                    for line in result.stdout.split('\n'):
                        if 'groups' in line.lower() and park_id in line:
                            log(f"    {line.strip()}")
                            break
                else:
                    log(f"    Error loading {park_id}: {result.stderr[:200]}")
                    
            except subprocess.TimeoutExpired:
                log(f"    Timeout loading {park_id}")
            except Exception as e:
                log(f"    Error: {e}")
    
    def update_narratives(self):
        """Run precompute_narratives_v5.py for all affected parks"""
        if not self.affected_parks:
            log("Step 5: No parks affected, skipping narrative update")
            return
        
        log(f"Step 5: Updating fire narratives...")
        
        try:
            # Run full narrative precompute - it's fast enough
            result = subprocess.run(
                ['python3', 'scripts/precompute_narratives_v5.py'],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=600
            )
            if result.returncode == 0:
                # Extract summary
                for line in result.stdout.split('\n'):
                    if 'Fire narratives:' in line or 'COMPLETE' in line:
                        log(f"    {line.strip()}")
            else:
                log(f"    Error: {result.stderr[:300]}")
                
        except subprocess.TimeoutExpired:
            log(f"    Timeout updating narratives")
        except Exception as e:
            log(f"    Error: {e}")
    
    def run(self):
        log("=" * 70)
        log("DAILY FIRE UPDATE PIPELINE (v5 - Incremental)")
        log("=" * 70)
        log(f"Date: {datetime.now().isoformat()}")
        log(f"Days to fetch: {self.days}")
        log(f"Incremental window: {INCREMENTAL_DAYS} days")
        log("")
        
        # Step 1: Download NRT fires
        fires = self.download_nrt_fires()
        
        # Step 2: Insert into database
        self.insert_fires(fires)
        
        # Step 3: Rebuild groups (incremental)
        self.rebuild_groups_incremental()
        
        # Step 4: Load to database
        self.load_groups_incremental()
        
        # Step 5: Update narratives
        self.update_narratives()
        
        log("")
        log("=" * 70)
        log("PIPELINE COMPLETE")
        log(f"Affected parks: {sorted(self.affected_parks)}")
        log("=" * 70)
        
        self.conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Daily fire update pipeline (incremental)')
    parser.add_argument('--days', type=int, default=DEFAULT_DAYS,
                        help=f'Days of NRT data to fetch (default: {DEFAULT_DAYS})')
    args = parser.parse_args()
    
    updater = DailyFireUpdater(days=args.days)
    updater.run()


if __name__ == '__main__':
    main()
