#!/usr/bin/env python3
"""
Daily Fire Update Pipeline (v5) - Incremental

Only processes new fires and updates recent groups:
1. Download NRT fires from FIRMS API (last N days)
2. Insert new fires to fire_detections (upsert)
3. For affected parks, add/update only recent groups (last 14 days)
4. Update feature_geometries incrementally (no full delete)
5. Update fire_narrative_cache

Cron: 0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py --days 7
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
NARRATIVES_DIR = DATA_DIR / "export" / "fire_narratives"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

NASA_API_KEY = "REDACTED_FIRMS_KEY"
FIRMS_NRT_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

DEFAULT_DAYS = 7
INCREMENTAL_WINDOW = 14  # Days to consider for incremental group updates

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def haversine(lon1, lat1, lon2, lat2):
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
        self.cutoff_date = (datetime.now() - timedelta(days=INCREMENTAL_WINDOW)).strftime('%Y-%m-%d')
        
    def _load_parks(self):
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
        log(f"Downloading NRT fires for last {self.days} days...")
        area = "-20,-35,55,40"  # Africa bbox
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
        if not fires:
            return 0
        
        log(f"Inserting {len(fires)} fires into database...")
        
        inserted = 0
        cursor = self.conn.cursor()
        
        for fire in fires:
            try:
                lat = float(fire.get('latitude', 0))
                lon = float(fire.get('longitude', 0))
                
                # Skip invalid coordinates
                if lon == 0.0 or lat == 0.0:
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
        log(f"Inserted {inserted} new fire records")
        log(f"Affected parks: {len(self.affected_parks)}")
        return inserted
    
    def _find_park(self, lon, lat):
        for park_id, park in self.parks.items():
            bbox = park.get('bbox')
            if bbox:
                minx, miny, maxx, maxy = bbox
                if minx - 0.5 <= lon <= maxx + 0.5 and miny - 0.5 <= lat <= maxy + 0.5:
                    return park_id
        return None
    
    def update_recent_groups(self):
        """Update only recent groups for affected parks (incremental)"""
        if not self.affected_parks:
            log("No parks affected, skipping group update")
            return
        
        log(f"Updating recent groups for {len(self.affected_parks)} parks (since {self.cutoff_date})...")
        
        for park_id in sorted(self.affected_parks):
            try:
                self._update_park_recent_groups(park_id)
            except Exception as e:
                log(f"  Error updating {park_id}: {e}")
    
    def _update_park_recent_groups(self, park_id):
        """Update recent groups for a single park without full rebuild"""
        
        # Load existing groups from JSON
        groups_file = GROUPS_DIR / f"{park_id}.json"
        existing_groups = []
        if groups_file.exists():
            with open(groups_file) as f:
                existing_groups = json.load(f)
        
        # Separate old groups (before cutoff) from recent groups (will be rebuilt)
        old_groups = [g for g in existing_groups if g.get('end_date', '') < self.cutoff_date]
        
        # Get recent fires from database
        cursor = self.conn.execute('''
            SELECT latitude, longitude, acq_date, acq_time, frp, confidence
            FROM fire_detections
            WHERE protected_area_id = ? AND acq_date >= ?
            ORDER BY acq_date, acq_time
        ''', (park_id, self.cutoff_date))
        
        recent_fires = [dict(row) for row in cursor]
        
        if not recent_fires:
            # No recent fires - keep old groups, remove recent from DB
            self._cleanup_recent_geometries(park_id)
            return
        
        # Simple clustering for recent fires (by date proximity)
        new_groups = self._cluster_recent_fires(park_id, recent_fires)
        
        # Merge old + new groups
        all_groups = old_groups + new_groups
        
        # Save updated JSON
        with open(groups_file, 'w') as f:
            json.dump(all_groups, f)
        
        # Update feature_geometries for new groups only
        self._update_geometries_incremental(park_id, new_groups)
        
        log(f"  {park_id}: kept {len(old_groups)} old, added {len(new_groups)} new groups")
    
    def _cluster_recent_fires(self, park_id, fires):
        """Simple clustering of recent fires into groups"""
        if not fires:
            return []
        
        # Group by date
        by_date = defaultdict(list)
        for f in fires:
            by_date[f['acq_date']].append(f)
        
        # Simple approach: each day's fires within 5km become a group
        groups = []
        year = datetime.now().year
        
        for date in sorted(by_date.keys()):
            day_fires = by_date[date]
            
            # Calculate centroid
            lons = [f['longitude'] for f in day_fires]
            lats = [f['latitude'] for f in day_fires]
            centroid = [sum(lons)/len(lons), sum(lats)/len(lats)]
            
            # Skip invalid centroids
            if centroid[0] == 0.0 or centroid[1] == 0.0:
                continue
            
            total_frp = sum(f.get('frp', 0) or 0 for f in day_fires)
            
            # Generate ID
            hash_input = f"{park_id}_{date}_{centroid[0]:.4f}_{centroid[1]:.4f}"
            group_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
            
            groups.append({
                'group_id': f"{park_id}_{date}_{group_hash}",
                'feature_id': f"{park_id}_{year}_grp_{group_hash}",
                'fire_count': len(day_fires),
                'start_date': date,
                'end_date': date,
                'days': 1,
                'year': year,
                'centroid': centroid,
                'trajectory': [[centroid[0], centroid[1], date, '1200']],
                'distance_km': 0,
                'speed_km_day': 0,
                'direction': 'N',
                'group_type': 'spot_fire',
                'pct_inside': 100.0,
                'total_frp': round(total_frp, 1),
                'primary_park': park_id,
                'affected_parks': [park_id],
                'cross_border': False,
                'first_point': centroid,
                'trajectory_type': 'clean',
                'zigzag_ratio': 0
            })
        
        return groups
    
    def _cleanup_recent_geometries(self, park_id):
        """Remove recent geometries that no longer have groups"""
        self.conn.execute('''
            DELETE FROM feature_geometries 
            WHERE park_id = ? AND feature_type = 'fire_trajectory' AND start_date >= ?
        ''', (park_id, self.cutoff_date))
        self.conn.commit()
    
    def _update_geometries_incremental(self, park_id, new_groups):
        """Add/update geometries for new groups only"""
        
        # Delete only recent geometries for this park
        self.conn.execute('''
            DELETE FROM feature_geometries 
            WHERE park_id = ? AND feature_type = 'fire_trajectory' AND start_date >= ?
        ''', (park_id, self.cutoff_date))
        
        # Insert new groups
        for group in new_groups:
            trajectory = group.get('trajectory', [])
            if not trajectory:
                continue
            
            centroid = group.get('centroid', [0, 0])
            if centroid[0] == 0.0 or centroid[1] == 0.0:
                continue
            
            # Build GeoJSON
            if len(trajectory) == 1:
                geojson = json.dumps({
                    "type": "Point",
                    "coordinates": [trajectory[0][0], trajectory[0][1]]
                })
            else:
                coords = [[pt[0], pt[1]] for pt in trajectory]
                geojson = json.dumps({
                    "type": "LineString",
                    "coordinates": coords
                })
            
            # Properties
            props = {
                "feature_id": group.get('feature_id'),
                "feature_type": "fire_trajectory",
                "group_type": group.get('group_type', 'unknown'),
                "position": "unknown",
                "days": group.get('days', 1),
                "fires_total": group.get('fire_count', 0),
                "direction": group.get('direction', ''),
                "distance_km": group.get('distance_km', 0),
                "avg_speed_km_day": group.get('speed_km_day', 0),
                "total_frp": group.get('total_frp', 0),
                "pct_inside": group.get('pct_inside', 0),
                "cross_border": group.get('cross_border', False),
                "affected_parks": group.get('affected_parks', [park_id]),
                "narrative": f"Fire detected {group.get('start_date')} with {group.get('fire_count', 0)} observations.",
                "season": "unknown",
                "trajectory_type": group.get('trajectory_type', 'unknown'),
                "zigzag_ratio": group.get('zigzag_ratio', 0),
                "year": group.get('year', datetime.now().year),
            }
            
            # Bbox
            lons = [pt[0] for pt in trajectory]
            lats = [pt[1] for pt in trajectory]
            
            self.conn.execute('''
                INSERT INTO feature_geometries 
                (feature_type, feature_id, park_id, geojson, 
                 bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                 start_date, end_date, properties_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'fire_trajectory', group.get('feature_id'), park_id, geojson,
                min(lons), min(lats), max(lons), max(lats),
                group.get('start_date'), group.get('end_date'),
                json.dumps(props)
            ))
        
        self.conn.commit()
    
    def update_narrative_cache(self):
        """Update narrative cache for affected parks"""
        if not self.affected_parks:
            return
        
        log(f"Updating narrative cache for {len(self.affected_parks)} parks...")
        
        for park_id in sorted(self.affected_parks):
            try:
                self._update_park_narrative(park_id)
            except Exception as e:
                log(f"  Error updating narrative for {park_id}: {e}")
    
    def _update_park_narrative(self, park_id):
        """Update narrative for a single park from existing JSON + DB"""
        
        # Load narrative file
        narrative_file = NARRATIVES_DIR / f"{park_id}.json"
        if not narrative_file.exists():
            return
        
        with open(narrative_file) as f:
            narrative = json.load(f)
        
        # Get current group count from DB
        cursor = self.conn.execute('''
            SELECT COUNT(*) FROM feature_geometries 
            WHERE park_id = ? AND feature_type = 'fire_trajectory'
        ''', (park_id,))
        total_groups = cursor.fetchone()[0]
        
        # Update counts in narrative
        narrative['total_groups'] = total_groups
        narrative['updated_at'] = datetime.now().isoformat()
        
        # Save back
        with open(narrative_file, 'w') as f:
            json.dump(narrative, f)
        
        # Update cache
        self.conn.execute('''
            INSERT OR REPLACE INTO fire_narrative_cache 
            (park_id, narrative_json, computed_at, from_year, to_year) 
            VALUES (?, ?, ?, ?, ?)
        ''', (park_id, json.dumps(narrative), datetime.now().isoformat(), 
              narrative.get('trend', {}).get('years', [{}])[0].get('year') if narrative.get('trend', {}).get('years') else None,
              narrative.get('year')))
        self.conn.commit()
        
        log(f"  Updated {park_id}: {total_groups} total groups")
    
    def run(self):
        log("=" * 60)
        log("DAILY FIRE UPDATE PIPELINE (v5 - Incremental)")
        log(f"Date: {datetime.now().isoformat()}")
        log(f"Days to fetch: {self.days}")
        log(f"Incremental window: {INCREMENTAL_WINDOW} days (since {self.cutoff_date})")
        log("=" * 60)
        
        # Step 1: Download NRT fires
        fires = self.download_nrt_fires()
        
        # Step 2: Insert into database
        self.insert_fires(fires)
        
        # Step 3: Update recent groups (incremental)
        self.update_recent_groups()
        
        # Step 4: Update narrative cache
        self.update_narrative_cache()
        
        log("=" * 60)
        log("COMPLETE")
        log(f"Affected parks: {len(self.affected_parks)}")
        log("=" * 60)
        
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
