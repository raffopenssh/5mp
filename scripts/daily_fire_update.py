#!/usr/bin/env python3
"""
Daily Fire Update Pipeline (v5)

Incremental update for fire data:
1. Download new fires from FIRMS NRT API (last N days)
2. Insert into fire_detections (no deletions, upsert)
3. Rebuild fire groups for affected parks (incremental)
4. Update feature_geometries for new groups
5. Update fire_narrative_cache for affected parks

Run via cron at 3am UTC:
  0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py >> logs/daily_fire.log 2>&1
"""

import os
import sys
import json
import sqlite3
import requests
import hashlib
import math
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
DATA_DIR = BASE_DIR / "data"
GROUPS_DIR = DATA_DIR / "fire_groups_v5"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

NASA_API_KEY = "REDACTED_FIRMS_KEY"
FIRMS_NRT_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# Default: fetch last 7 days (overlaps ensure no gaps)
DEFAULT_DAYS = 7

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def haversine(lon1, lat1, lon2, lat2):
    """Distance in km between two points"""
    R = 6371
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


class DailyFireUpdater:
    def __init__(self, days=DEFAULT_DAYS):
        self.days = days
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        self.parks = self._load_parks()
        self.affected_parks = set()
        
    def _load_parks(self):
        """Load park boundaries"""
        parks = {}
        try:
            with open(DATA_DIR / 'keystones_with_boundaries.json') as f:
                for p in json.load(f):
                    parks[p['id']] = {
                        'name': p.get('name', p['id']),
                        'geometry': p.get('geometry'),
                        'bbox': self._get_bbox(p.get('geometry'))
                    }
        except Exception as e:
            log(f"Warning: Could not load parks: {e}")
        return parks
    
    def _get_bbox(self, geometry):
        """Extract bounding box from geometry"""
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
        """Download NRT fires for Africa bbox"""
        log(f"Downloading NRT fires for last {self.days} days...")
        
        # Africa bounding box (with buffer)
        area = "-20,-35,55,40"  # W,S,E,N
        
        url = f"{FIRMS_NRT_URL}/{NASA_API_KEY}/VIIRS_NOAA20_NRT/{area}/{self.days}"
        
        try:
            response = requests.get(url, timeout=300)
            response.raise_for_status()
            
            lines = response.text.strip().split('\n')
            if len(lines) < 2:
                log("No fires found in NRT data")
                return []
            
            header = lines[0].split(',')
            fires = []
            for line in lines[1:]:
                values = line.split(',')
                if len(values) >= len(header):
                    fire = dict(zip(header, values))
                    fires.append(fire)
            
            log(f"Downloaded {len(fires)} fire detections")
            return fires
            
        except Exception as e:
            log(f"Error downloading NRT fires: {e}")
            return []
    
    def insert_fires(self, fires):
        """Insert fires into fire_detections table (upsert, no deletions)"""
        if not fires:
            return 0
        
        log(f"Inserting {len(fires)} fires into database...")
        
        inserted = 0
        cursor = self.conn.cursor()
        
        for fire in fires:
            try:
                lat = float(fire.get('latitude', 0))
                lon = float(fire.get('longitude', 0))
                acq_date = fire.get('acq_date', '')
                acq_time = fire.get('acq_time', '')
                bright_ti4 = float(fire.get('bright_ti4', 0))
                frp = float(fire.get('frp', 0))
                confidence = fire.get('confidence', '')
                
                # Find which park this fire belongs to
                park_id = self._find_park(lon, lat)
                
                satellite = fire.get('satellite', '1')  # Default NOAA-20
                scan = float(fire.get('scan', 0))
                track = float(fire.get('track', 0))
                daynight = fire.get('daynight', '')
                in_pa = 1 if park_id else 0
                
                # Upsert (INSERT OR IGNORE to avoid duplicates based on UNIQUE constraint)
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
                pass  # Skip malformed records
        
        self.conn.commit()
        log(f"Inserted {inserted} new fire records")
        log(f"Affected parks: {len(self.affected_parks)}")
        return inserted
    
    def _find_park(self, lon, lat):
        """Find which park a point belongs to (using bbox for speed)"""
        for park_id, park in self.parks.items():
            bbox = park.get('bbox')
            if bbox:
                minx, miny, maxx, maxy = bbox
                # Add 50km buffer (~0.5 degrees)
                if minx - 0.5 <= lon <= maxx + 0.5 and miny - 0.5 <= lat <= maxy + 0.5:
                    return park_id
        return None
    
    def rebuild_affected_groups(self):
        """Rebuild fire groups for affected parks only"""
        if not self.affected_parks:
            log("No parks affected, skipping group rebuild")
            return
        
        log(f"Rebuilding fire groups for {len(self.affected_parks)} affected parks...")
        
        # Import the v5 rebuild function
        sys.path.insert(0, str(BASE_DIR / 'scripts'))
        from rebuild_fire_trajectories_v5 import FireTrajectoryRebuilder
        
        rebuilder = FireTrajectoryRebuilder()
        
        for park_id in sorted(self.affected_parks):
            try:
                # Get current year fires for this park
                current_year = datetime.now().year
                
                # Rebuild only current year groups for this park
                rebuilder.process_park(park_id, years=[current_year])
                log(f"  Rebuilt groups for {park_id}")
                
            except Exception as e:
                log(f"  Error rebuilding {park_id}: {e}")
    
    def update_feature_geometries(self):
        """Update feature_geometries for affected parks"""
        if not self.affected_parks:
            return
        
        log(f"Updating feature_geometries for {len(self.affected_parks)} parks...")
        
        sys.path.insert(0, str(BASE_DIR / 'scripts'))
        from load_fire_groups_to_db import FireGroupLoader
        
        loader = FireGroupLoader()
        
        for park_id in sorted(self.affected_parks):
            try:
                # Load the updated JSON and insert to DB
                json_file = GROUPS_DIR / f"{park_id}.json"
                if json_file.exists():
                    loader.load_park(park_id, force=True)
                    log(f"  Updated geometries for {park_id}")
            except Exception as e:
                log(f"  Error updating geometries for {park_id}: {e}")
    
    def update_narrative_cache(self):
        """Update fire narrative cache for affected parks"""
        if not self.affected_parks:
            return
        
        log(f"Updating narrative cache for {len(self.affected_parks)} parks...")
        
        sys.path.insert(0, str(BASE_DIR / 'scripts'))
        from precompute_narratives_v5 import NarrativeGeneratorV5
        
        # The v5 generator reads from DB, so we just need to regenerate for affected parks
        generator = NarrativeGeneratorV5()
        
        for park_id in sorted(self.affected_parks):
            try:
                # Update cache for this park
                cursor = self.conn.execute('''
                    SELECT properties_json, start_date
                    FROM feature_geometries
                    WHERE feature_type = 'fire_trajectory' 
                    AND park_id = ? AND start_date >= '2020-06-01'
                    ORDER BY start_date
                ''', (park_id,))
                
                trajectories = []
                for row in cursor:
                    props = json.loads(row['properties_json']) if row['properties_json'] else {}
                    trajectories.append({
                        'start_date': row['start_date'],
                        **props
                    })
                
                if trajectories:
                    # Generate narrative and update cache (simplified)
                    narrative_json = json.dumps({
                        'park_id': park_id,
                        'total_groups': len(trajectories),
                        'updated_at': datetime.now().isoformat()
                    })
                    
                    self.conn.execute('''
                        UPDATE fire_narrative_cache 
                        SET narrative_json = ?, computed_at = ?
                        WHERE park_id = ?
                    ''', (narrative_json, datetime.now().isoformat(), park_id))
                    
                log(f"  Updated cache for {park_id}")
                
            except Exception as e:
                log(f"  Error updating cache for {park_id}: {e}")
        
        self.conn.commit()
    
    def run(self):
        """Run the full daily update pipeline"""
        log("=" * 60)
        log("DAILY FIRE UPDATE PIPELINE (v5)")
        log(f"Date: {datetime.now().isoformat()}")
        log(f"Days to fetch: {self.days}")
        log("=" * 60)
        
        # Step 1: Download NRT fires
        fires = self.download_nrt_fires()
        
        # Step 2: Insert into database
        self.insert_fires(fires)
        
        # Step 3: Rebuild affected groups
        self.rebuild_affected_groups()
        
        # Step 4: Update feature geometries
        self.update_feature_geometries()
        
        # Step 5: Update narrative cache
        self.update_narrative_cache()
        
        log("=" * 60)
        log("COMPLETE")
        log(f"Affected parks: {sorted(self.affected_parks)}")
        log("=" * 60)
        
        self.conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Daily fire update pipeline')
    parser.add_argument('--days', type=int, default=DEFAULT_DAYS,
                        help=f'Days of NRT data to fetch (default: {DEFAULT_DAYS})')
    args = parser.parse_args()
    
    updater = DailyFireUpdater(days=args.days)
    updater.run()


if __name__ == '__main__':
    main()
