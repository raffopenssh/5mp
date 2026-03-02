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

Cron: 0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py >> logs/daily_fire.log 2>&1
"""

import os
import sys
import json
import sqlite3
import requests
import subprocess
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# Import Webshare proxy helper
sys.path.insert(0, str(Path(__file__).parent))
try:
    from webshare_proxy import get_webshare_proxies, get_proxy_dict
    WEBSHARE_AVAILABLE = True
    # Verify we can actually get proxies
    test_proxies = get_webshare_proxies()
    if not test_proxies:
        print("WARNING: Webshare module loaded but no proxies available")
        WEBSHARE_AVAILABLE = False
except Exception as e:
    print(f"WARNING: Could not import Webshare proxies: {e}")
    WEBSHARE_AVAILABLE = False

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

NASA_API_KEY = "REDACTED_FIRMS_KEY"
FIRMS_NRT_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# Proxy list sources (ordered by reliability)
PROXY_SOURCES = [
    # ProxyScrape API (reliable, updated frequently)
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    # GitHub sources (community maintained)
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
    "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
]

DEFAULT_DAYS = 5  # Default: last 5 days
INCREMENTAL_DAYS = 14  # Days window for incremental rebuild

# FIRMS API has different modes:
# - NRT (Near Real-Time): 1-10 days, use /days parameter
# - SP (Standard Processing): historical data, use /1/YYYY-MM-DD parameter

def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)


def fetch_proxies(max_per_source=50):
    """Fetch proxies from GitHub sources."""
    all_proxies = []
    for source in PROXY_SOURCES:
        try:
            resp = requests.get(source, timeout=20)
            resp.raise_for_status()
            proxies = [p.strip() for p in resp.text.split('\n') 
                      if p.strip() and ':' in p and not p.startswith('#')]
            all_proxies.extend(proxies[:max_per_source])
        except Exception as e:
            log(f"  Failed to fetch from {source.split('/')[-2]}: {e}")
    # Deduplicate
    return list(set(all_proxies))


def test_proxy(proxy, test_url="https://firms.modaps.eosdis.nasa.gov", timeout=10):
    """Test if proxy works for given URL."""
    try:
        proxy_dict = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        resp = requests.get(
            test_url, 
            proxies=proxy_dict, 
            timeout=timeout,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        return resp.status_code < 400
    except:
        return False


def get_working_proxy(test_url="https://firms.modaps.eosdis.nasa.gov", max_test=50):
    """Get a working proxy for the target URL."""
    log("  Fetching proxy lists from GitHub...")
    proxies = fetch_proxies()
    
    if not proxies:
        log("  No proxies fetched from sources")
        return None
    
    log(f"  Testing up to {max_test} proxies for FIRMS API...")
    random.shuffle(proxies)
    
    for i, proxy in enumerate(proxies[:max_test]):
        if test_proxy(proxy, test_url, timeout=8):
            log(f"  Found working proxy: {proxy}")
            return proxy
        if (i + 1) % 10 == 0:
            log(f"    Tested {i + 1}/{min(len(proxies), max_test)}...")
    
    log("  No working proxy found after testing {min(len(proxies), max_test)} proxies")
    return None


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
        log(f"Step 1: Downloading fires for last {self.days} days...")
        area = "-20,-35,55,40"  # Africa bbox
        
        # Choose API mode based on days requested
        if self.days <= 10:
            # NRT mode: last 1-10 days
            source = "VIIRS_NOAA20_NRT"
            url = f"{FIRMS_NRT_URL}/{NASA_API_KEY}/{source}/{area}/{self.days}"
            log(f"  Using NRT API (last {self.days} days)")
        else:
            # SP mode: historical data with date range
            source = "VIIRS_NOAA20_SP"
            end_date = datetime.now()
            start_date = end_date - timedelta(days=self.days)
            url = f"{FIRMS_NRT_URL}/{NASA_API_KEY}/{source}/{area}/1/{start_date.strftime('%Y-%m-%d')}"
            log(f"  Using SP API (from {start_date.strftime('%Y-%m-%d')} to today)")
        
        # Try Webshare proxies first (reliable, authenticated)
        if WEBSHARE_AVAILABLE:
            log("  Trying Webshare proxies (reliable)...")
            webshare_proxies = get_webshare_proxies()
            if webshare_proxies:
                for ws_proxy in webshare_proxies:
                    try:
                        proxy_dict = get_proxy_dict(ws_proxy)
                        proxy_str = f"{ws_proxy['host']}:{ws_proxy['port']}"
                        log(f"  Trying Webshare proxy: {proxy_str} ({ws_proxy['city']}, {ws_proxy['country']})")
                        
                        response = requests.get(url, proxies=proxy_dict, timeout=120)
                        response.raise_for_status()
                        
                        # Success!
                        log(f"  ✓ Successfully downloaded via Webshare proxy: {proxy_str}")
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
                        log(f"    Webshare proxy failed: {str(e)[:60]}")
                        continue
        
        # Fall back to free proxies
        log("  Webshare proxies unavailable, trying free proxies...")
        log("  Fetching proxy lists from GitHub...")
        all_proxies = fetch_proxies()
        
        if all_proxies:
            random.shuffle(all_proxies)
            
            # Try up to 5 different proxies
            max_attempts = 5
            attempt = 0
            
            for proxy in all_proxies[:30]:  # Check first 30 proxies
                # Quick test
                if not test_proxy(proxy, "https://firms.modaps.eosdis.nasa.gov", timeout=5):
                    continue
                
                attempt += 1
                log(f"  Attempt {attempt}/{max_attempts}: Trying proxy {proxy}")
                
                try:
                    proxy_dict = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
                    response = requests.get(url, proxies=proxy_dict, timeout=120)
                    response.raise_for_status()
                    
                    # Success!
                    log(f"  ✓ Successfully downloaded via proxy: {proxy}")
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
                    log(f"    ✗ Failed: {str(e)[:80]}")
                    if attempt >= max_attempts:
                        break
                    continue
        
        # All proxies failed, try direct
        log("  All proxies failed, trying direct connection...")
        try:
            response = requests.get(url, timeout=120)
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
            
            log(f"  Downloaded {len(fires)} fire detections (direct)")
            return fires
            
        except Exception as e:
            log(f"  Error downloading NRT fires: {e}")
            # Create notification for critical failure
            try:
                import sqlite3
                conn = sqlite3.connect(str(DB_PATH))
                conn.execute("""
                    INSERT INTO notifications (park_id, notification_type, title, message, created_at)
                    VALUES ('SYSTEM', 'fire_download_failed', 'Fire Download Failed', ?, datetime('now'))
                """, (f"Failed to download NRT fires: {str(e)[:200]}",))
                conn.commit()
                conn.close()
                log("  Created notification for download failure")
            except Exception as notif_err:
                log(f"  Failed to create notification: {notif_err}")
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
        
        # Create success notification if we inserted significant data
        if inserted > 1000:
            try:
                self.conn.execute("""
                    INSERT INTO notifications (park_id, notification_type, title, message, created_at)
                    VALUES ('SYSTEM', 'fire_download_success', 'Fire Download Success', ?, datetime('now'))
                """, (f"Downloaded and processed {inserted} new fire detections from {len(self.affected_parks)} parks",))
                self.conn.commit()
                log("  Created notification for successful download")
            except Exception as notif_err:
                log(f"  Failed to create notification: {notif_err}")
        
        return inserted
    
    def _find_park(self, lon, lat):
        for park_id, park in self.parks.items():
            bbox = park.get('bbox')
            if bbox:
                minx, miny, maxx, maxy = bbox
                if minx - 0.5 <= lon <= maxx + 0.5 and miny - 0.5 <= lat <= maxy + 0.5:
                    return park_id
        return None
    
    def update_raw_json_files(self, fires):
        """Update raw JSON fire files with NRT data so trajectory builder can use them."""
        if not fires:
            log("Step 2b: No fires to add to raw JSON files")
            return
        
        RAW_DIR = DATA_DIR / "raw-fire-viirs-20200101-20260222"
        if not RAW_DIR.exists():
            log(f"  Creating raw fire directory: {RAW_DIR}")
            RAW_DIR.mkdir(parents=True, exist_ok=True)
        
        # Group fires by park
        fires_by_park = defaultdict(list)
        for fire in fires:
            try:
                lat = float(fire.get('latitude', 0))
                lon = float(fire.get('longitude', 0))
                if lon == 0.0 or lat == 0.0:
                    continue
                park_id = self._find_park(lon, lat)
                if park_id:
                    fires_by_park[park_id].append({
                        'latitude': lat,
                        'longitude': lon,
                        'acq_date': fire.get('acq_date', ''),
                        'acq_time': fire.get('acq_time', ''),
                        'frp': float(fire.get('frp', 0)),
                        'confidence': fire.get('confidence', 'n'),
                        'satellite': fire.get('satellite', 'N20')
                    })
            except:
                continue
        
        if not fires_by_park:
            log("Step 2b: No fires matched any parks")
            return
        
        log(f"Step 2b: Updating raw JSON files for {len(fires_by_park)} parks...")
        
        total_added = 0
        parks_updated = set()
        
        for park_id, park_fires in fires_by_park.items():
            raw_file = RAW_DIR / f"{park_id}.json"
            
            try:
                # Load existing or create new
                if raw_file.exists():
                    with open(raw_file) as f:
                        data = json.load(f)
                    existing_fires = data.get('fires', [])
                else:
                    data = {'park_id': park_id, 'fires': []}
                    existing_fires = []
                existing_keys = {(f['latitude'], f['longitude'], f['acq_date'], f.get('acq_time', '')) 
                                for f in existing_fires}
                
                added = 0
                for fire in park_fires:
                    key = (fire['latitude'], fire['longitude'], fire['acq_date'], fire['acq_time'])
                    if key not in existing_keys:
                        existing_fires.append(fire)
                        added += 1
                
                if added > 0:
                    data['fires'] = existing_fires
                    with open(raw_file, 'w') as f:
                        json.dump(data, f)
                    total_added += added
                    parks_updated.add(park_id)
            except Exception as e:
                log(f"  Error updating {park_id}: {e}")
        
        # Add parks with new fires to affected_parks for trajectory rebuild
        self.affected_parks.update(parks_updated)
        log(f"  Added {total_added} fires to {len(parks_updated)} raw JSON files")
    
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
                     '--park', park_id, '--incremental', '--days', str(INCREMENTAL_DAYS)],
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
            # Run incremental narrative precompute
            result = subprocess.run(
                ['python3', 'scripts/precompute_narratives_v5.py', '--incremental', '--days', str(INCREMENTAL_DAYS)],
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
    
    def create_fire_notifications(self):
        """Create one notification per active fire group (for click-to-pin functionality).
        
        Active groups are those in fire_group_alerts table with alert_type = 'active_inside' or 'entered'.
        The fire realtime API populates fire_group_alerts when called.
        """
        if not self.affected_parks:
            log("Step 6: No parks affected, skipping notifications")
            return
        
        log(f"Step 6: Creating notifications for active fire groups...")
        
        # Trigger the fire realtime endpoint to update fire_group_alerts
        import requests
        
        notifications_created = 0
        for park_id in self.affected_parks:
            try:
                # Call fire realtime API to update alerts (this populates fire_group_alerts table)
                response = requests.get(
                    f"http://localhost:8000/api/parks/{park_id}/fire-realtime",
                    params={'pwd': 'test2026', 'days': 28},
                    timeout=30
                )
                
                if response.status_code == 200:
                    data = response.json()
                    active_groups = data.get('active_groups', [])
                    
                    if active_groups:
                        park_name = self.parks.get(park_id, {}).get('name', park_id)
                        
                        # Create one notification per active group
                        for group in active_groups:
                            group_name = group.get('group_name', 'Unknown')
                            feature_id = group.get('feature_id', '')
                            fire_count = group.get('fire_count', 0)
                            days_active = group.get('days_active', 1)
                            direction = group.get('movement_direction', 'stationary')
                            
                            # Check if notification already exists for this group
                            cursor = self.conn.execute("""
                                SELECT id FROM notifications 
                                WHERE park_id = ? 
                                AND notification_type = 'fire_alert'
                                AND reference_id = ?
                                AND created_at > datetime('now', '-7 days')
                                LIMIT 1
                            """, (park_id, feature_id))
                            
                            if cursor.fetchone():
                                continue  # Skip if notification exists
                            
                            # Create notification
                            title = f"🔥 {park_name}: {group_name}"
                            message = f"{fire_count} fires, {days_active} days active, moving {direction}"
                            
                            # Store reference data for click-to-pin
                            reference_data = json.dumps({
                                'park_id': park_id,
                                'park_name': park_name,
                                'feature_id': feature_id,
                                'type': 'fire_trajectory',
                                'group_name': group_name
                            })
                            
                            self.conn.execute("""
                                INSERT INTO notifications 
                                (park_id, notification_type, title, message, reference_id, reference_data, created_at)
                                VALUES (?, 'fire_alert', ?, ?, ?, ?, datetime('now'))
                            """, (park_id, title, message, feature_id, reference_data))
                            
                            notifications_created += 1
                        
                        log(f"  {park_id}: Created {len(active_groups)} notifications")
                else:
                    log(f"  Error calling API for {park_id}: {response.status_code}")
                    
            except Exception as e:
                log(f"  Error processing {park_id}: {e}")
        
        self.conn.commit()
        log(f"  Total: {notifications_created} fire group notifications")
    
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
        
        # Step 2b: Update raw JSON files for trajectory builder
        self.update_raw_json_files(fires)
        
        # Step 3: Rebuild groups (incremental)
        self.rebuild_groups_incremental()
        
        # Step 4: Load to database
        self.load_groups_incremental()
        
        # Step 5: Update narratives
        self.update_narratives()
        
        # Step 6: Create notifications for significant fire activity
        self.create_fire_notifications()
        
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
