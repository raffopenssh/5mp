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

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from secrets_config import secret, app_password
import os
import sys
import re
import json
import sqlite3
import requests
import subprocess
import math
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# Import Webshare proxy helper - use absolute path for cron reliability
BASE_DIR = Path(__file__).parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

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
    print(f"  Scripts dir: {SCRIPTS_DIR}")
    print(f"  sys.path: {sys.path[:3]}")
    WEBSHARE_AVAILABLE = False

DB_PATH = BASE_DIR / "db.sqlite3"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Heartbeat file. A cron that never runs is indistinguishable from a successful
# run if the only evidence is an append-only log, so every run (success or
# failure) rewrites this. Surfaced by GET /api/pipeline-status.
STATUS_FILE = DATA_DIR / "pipeline_status.json"

NASA_API_KEY = secret('NASA_FIRMS_KEY')
FIRMS_NRT_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# Fetch window. Must be >= the late-arrival delay of FIRMS NRT data, and
# should not exceed INCREMENTAL_DAYS or a detection older than the fetch window
# but newer than the rebuild window can never be picked up. Was 5, which meant
# any detection arriving >5 days late was lost permanently. INSERT OR IGNORE
# makes the overlap free.
DEFAULT_DAYS = 10
INCREMENTAL_DAYS = 14  # Days window for incremental rebuild

# All three operational VIIRS sensors. Previously only NOAA-20 was ingested,
# giving ~2 overpasses/day; with SNPP and NOAA-21 it is ~6, which is the single
# biggest quality lever on trajectory continuity (fewer gap-retirements, fewer
# spurious "gone dark" statuses). Their FIRMS `satellite` codes are distinct
# (N / N20 / N21) so the fire_detections UNIQUE constraint keeps them apart.
FIRMS_SOURCES = ["VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT", "VIIRS_NOAA21_NRT"]

# Max fire_alert notifications per park per run; the rest are rolled up into a
# single "N more active fire groups" entry. Peak-season parks legitimately have
# 150-280 active groups, so an uncapped feed drowns everything else.
MAX_ALERTS_PER_PARK = 5
FIRMS_SP_SOURCES = ["VIIRS_NOAA20_SP", "VIIRS_SNPP_SP", "VIIRS_NOAA21_SP"]

# FIRMS API has different modes:
# - NRT (Near Real-Time): 1-10 days, use /days parameter
# - SP (Standard Processing): historical data, use /1/YYYY-MM-DD parameter

def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)


def parse_firms_csv(text):
    """Parse FIRMS CSV text into a list of dicts (one per detection)."""
    lines = text.strip().split('\n')
    if len(lines) < 2:
        return []
    header = lines[0].split(',')
    fires = []
    for line in lines[1:]:
        values = line.split(',')
        if len(values) >= len(header):
            fires.append(dict(zip(header, values)))
    return fires


class DailyFireUpdater:
    def __init__(self, days=DEFAULT_DAYS):
        self.days = days
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        self.parks = self._load_parks()
        self.affected_parks = set()
        self.started_at = datetime.now()
        # Heartbeat counters (written to STATUS_FILE at end of run)
        self.stats = {
            'fires_fetched': 0,
            'detections_inserted': 0,
            'parks_rebuilt': 0,
            'groups_loaded': 0,
            'alerts_created': 0,
            'notifications_created': 0,
            'errors': [],
        }
        # Canonical single-park assignment: nearest park boundary within
        # 100km (park_assigner.ASSIGN_MAX_DIST_KM). Replaces the old bbox
        # first-match _find_park which caused overlap duplicates.
        from park_assigner import ParkAssigner
        log("Loading ParkAssigner (100km nearest-boundary assignment)...")
        self.assigner = ParkAssigner()
        
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
        """Download from every VIIRS sensor and concatenate.

        A partial failure is tolerated (we still ingest what we got) but is
        reported, because silently running on one sensor is exactly the
        degradation that is hard to notice.
        """
        log(f"Step 1: Downloading fires for last {self.days} days "
            f"from {len(FIRMS_SOURCES)} sensors...")
        area = "-20,-35,55,40"  # Africa bbox
        nrt = self.days <= 10
        sources = FIRMS_SOURCES if nrt else FIRMS_SP_SOURCES

        all_fires = []
        failed = []
        for source in sources:
            if nrt:
                url = f"{FIRMS_NRT_URL}/{NASA_API_KEY}/{source}/{area}/{self.days}"
            else:
                start_date = datetime.now() - timedelta(days=self.days)
                url = (f"{FIRMS_NRT_URL}/{NASA_API_KEY}/{source}/{area}/1/"
                       f"{start_date.strftime('%Y-%m-%d')}")
            fires = self._fetch_one(source, url)
            if fires is None:
                failed.append(source)
            else:
                log(f"  {source}: {len(fires):,} detections")
                all_fires.extend(fires)

        log(f"  Total: {len(all_fires):,} detections from "
            f"{len(sources) - len(failed)}/{len(sources)} sensors")
        self.stats['fires_fetched'] = len(all_fires)
        if failed:
            self._fail('download', f"sources failed: {','.join(failed)}")

        if failed:
            msg = (f"Fire download incomplete: {', '.join(failed)} failed; "
                   f"ingested {len(all_fires)} detections from the rest.")
            log(f"  WARNING: {msg}")
            self._notify_system('fire_download_failed',
                               'Fire Download Partially Failed'
                               if all_fires else 'Fire Download Failed', msg)
        return all_fires

    def _fetch_one(self, source, url):
        """Fetch one FIRMS source. Returns list, or None on total failure."""
        # 1. Direct connection first (verified working from this VM, 2026-07-05)
        last_err = None
        try:
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            return parse_firms_csv(response.text)
        except Exception as e:
            last_err = e
            log(f"    {source}: direct failed: {str(e)[:80]}")

        # 2. Fallback: Webshare authenticated proxies
        if WEBSHARE_AVAILABLE:
            for ws_proxy in (get_webshare_proxies() or []):
                proxy_str = f"{ws_proxy['host']}:{ws_proxy['port']}"
                try:
                    response = requests.get(url, proxies=get_proxy_dict(ws_proxy),
                                            timeout=120)
                    response.raise_for_status()
                    log(f"    {source}: via Webshare {proxy_str}")
                    return parse_firms_csv(response.text)
                except Exception as e:
                    last_err = e
        log(f"    {source}: all paths failed: {str(last_err)[:80]}")
        return None

    def _fail(self, what, detail):
        """Record a step failure for the heartbeat (the log already has detail)."""
        self.stats['errors'].append(f"{what}: {str(detail)[:200]}")

    def write_status(self, fatal=None):
        """Rewrite the pipeline heartbeat file. Never raises."""
        try:
            finished = datetime.now()
            if fatal:
                status = 'failed'
            elif self.stats['errors']:
                status = 'degraded'
            else:
                status = 'ok'
            payload = {
                'pipeline': 'daily_fire_update',
                'status': status,
                'started_at': self.started_at.isoformat(),
                'finished_at': finished.isoformat(),
                'duration_sec': round((finished - self.started_at).total_seconds(), 1),
                'days_fetched': self.days,
                'affected_parks': len(self.affected_parks),
                'fatal_error': str(fatal)[:300] if fatal else None,
            }
            payload.update(self.stats)
            STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATUS_FILE.with_suffix('.json.tmp')
            json.dump(payload, open(tmp, 'w'), indent=2)
            tmp.replace(STATUS_FILE)
            log(f"  Heartbeat: {STATUS_FILE.name} status={status}")
        except Exception as e:
            log(f"  Failed to write heartbeat: {e}")

    def _notify_system(self, ntype, title, message):
        try:
            self.conn.execute("""
                INSERT INTO notifications (park_id, notification_type, title, message, created_at)
                VALUES ('SYSTEM', ?, ?, ?, datetime('now'))
            """, (ntype, title, message[:500]))
            self.conn.commit()
        except Exception as notif_err:
            log(f"  Failed to create notification: {notif_err}")
    
    def insert_fires(self, fires):
        if not fires:
            return 0
        
        log(f"Step 2: Inserting {len(fires)} fires into database...")
        
        inserted = 0
        skipped_invalid = 0
        errors = 0
        first_error = None
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
                # Do NOT default the satellite: it is part of the UNIQUE key,
                # so a wrong default would collapse distinct sensors' rows.
                satellite = fire.get('satellite') or 'unknown'
                scan = float(fire.get('scan', 0))
                track = float(fire.get('track', 0))
                daynight = fire.get('daynight', '')
                
                # Canonical single-park assignment (annotated by _assign_fires)
                park_id = fire.get('_park_id')
                in_pa = 1 if fire.get('_dist_km') == 0.0 else 0
                
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
                # Previously `pass` - parse and constraint failures were
                # completely invisible. Count them and surface the first.
                errors += 1
                if first_error is None:
                    first_error = f"{type(e).__name__}: {e}"
        
        self.conn.commit()
        log(f"  Inserted {inserted} new fire records")
        self.stats['detections_inserted'] = inserted
        if skipped_invalid > 0:
            log(f"  Skipped {skipped_invalid} records with invalid coordinates")
        if errors:
            log(f"  WARNING: {errors} records failed to insert; first: {first_error}")
            if errors > len(fires) * 0.01:
                self._notify_system(
                    'fire_ingest_errors', 'Fire Ingest Errors',
                    f"{errors} of {len(fires)} detections failed to insert. "
                    f"First error: {first_error}")
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
    
    def _assign_fires(self, fires):
        """Annotate each fire dict with _park_id/_dist_km via ParkAssigner.

        One fire -> at most one park (nearest boundary within 100km).
        Run once so insert_fires and the grid aggregates agree.
        """
        if not fires:
            return
        log(f"Step 1b: Assigning {len(fires)} fires to parks (nearest boundary, <=100km)...")
        assigned = 0
        for fire in fires:
            try:
                lat = float(fire.get('latitude', 0))
                lon = float(fire.get('longitude', 0))
            except (TypeError, ValueError):
                fire['_park_id'], fire['_dist_km'] = None, None
                continue
            if lat == 0.0 or lon == 0.0:
                fire['_park_id'], fire['_dist_km'] = None, None
                continue
            park_id, dist_km = self.assigner.assign(lon, lat)
            fire['_park_id'], fire['_dist_km'] = park_id, dist_km
            if park_id:
                assigned += 1
        log(f"  {assigned}/{len(fires)} fires within 100km of a park")

    
    def refresh_grid_agg(self):
        """Incrementally refresh fire_grid_day/week/month (time animator backend)."""
        import subprocess
        since = (datetime.now() - timedelta(days=self.days + 2)).strftime('%Y-%m-%d')
        log(f"Step 2c: Refreshing animation grid aggregates since {since}...")
        try:
            r = subprocess.run(
                [sys.executable, 'scripts/build_fire_grid_agg.py', '--since', since],
                capture_output=True, text=True, timeout=600,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if r.returncode != 0:
                log(f"  Grid agg refresh failed: {r.stderr.strip()[:300]}")
            else:
                log(f"  {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'done'}")
        except Exception as e:
            log(f"  Grid agg refresh error: {e}")

    def refresh_persistent_hotspots(self):
        """Monthly refresh of the persistent-hotspot mask (flares/lava/kilns).

        Cheap but not nightly-cheap (~70s full scan), and the criterion is
        "detected in >=30 distinct months", which cannot change materially in a
        day. Runs on the 1st of the month only.
        """
        if datetime.now().day != 1:
            return
        log("Step 2d: Refreshing persistent hotspot mask (monthly)...")
        try:
            r = subprocess.run(
                [sys.executable, 'scripts/build_persistent_hotspots.py'],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                log(f"  Hotspot mask refresh failed: {r.stderr.strip()[:300]}")
                self._fail('hotspot_mask', r.stderr.strip()[:200])
            else:
                for line in r.stdout.splitlines():
                    if 'Wrote' in line or 'persistent cells' in line:
                        log(f"  {line.strip()}")
        except Exception as e:
            log(f"  Hotspot mask refresh error: {e}")
            self._fail('hotspot_mask', e)

    def check_consistency(self):
        """Verify fire_groups_v5 JSON, feature_geometries and the narrative
        cache still agree. Drift here is invisible in the UI until a user
        clicks a fire and gets "Feature not found", so surface it."""
        log("Step 7: Consistency check (JSON vs features vs narratives)...")
        try:
            r = subprocess.run(
                [sys.executable, 'scripts/check_fire_consistency.py'],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=600)
            for line in r.stdout.splitlines():
                log(f"  {line}")
            self.stats['consistent'] = (r.returncode == 0)
            if r.returncode != 0:
                self._fail('consistency', 'drift detected; see '
                                          'scripts/fix_fire_consistency.py')
        except Exception as e:
            log(f"  Consistency check error: {e}")
            self._fail('consistency', e)

    def rebuild_groups_incremental(self):
        """Run rebuild_fire_trajectories_v5.py --incremental for affected parks"""
        if not self.affected_parks:
            log("Step 3: No parks affected, skipping group rebuild")
            return
        
        parks = sorted(self.affected_parks)
        log(f"Step 3: Rebuilding fire groups (incremental) for {len(parks)} parks...")

        # One process for all parks (--parks) instead of one subprocess each.
        # Spawning ~100 interpreters re-paid the sklearn/scipy import, the
        # keystone-boundary load and the DB connection every time; this took
        # ~6 min of the nightly run.
        try:
            result = subprocess.run(
                ['python3', 'scripts/rebuild_fire_trajectories_v5.py',
                 '--parks', ','.join(parks),
                 '--incremental', '--days', str(INCREMENTAL_DAYS)],
                cwd=str(BASE_DIR), capture_output=True, text=True,
                timeout=3600
            )
            for line in result.stdout.split('\n'):
                if 'fires ->' in line:
                    log(f"    {line.strip()}")
            if result.returncode == 0:
                self.stats['parks_rebuilt'] = len(parks)
            if result.returncode != 0:
                self._fail('rebuild', f"rc={result.returncode}")
                log(f"  ERROR: rebuild failed rc={result.returncode}: "
                    f"{result.stderr[-500:]}")
                self._notify_system('fire_rebuild_failed', 'Fire Rebuild Failed',
                                   f"rebuild_fire_trajectories exited "
                                   f"{result.returncode}: {result.stderr[-300:]}")
        except subprocess.TimeoutExpired:
            log("  ERROR: rebuild timed out after 3600s")
            self._fail('rebuild', 'timeout after 3600s')
            self._notify_system('fire_rebuild_failed', 'Fire Rebuild Timeout',
                               f"Rebuild of {len(parks)} parks exceeded 3600s")
        except Exception as e:
            log(f"  ERROR: {e}")
            self._fail('rebuild', e)
    
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
                    self.stats['groups_loaded'] += 1
                    # Extract count from output
                    for line in result.stdout.split('\n'):
                        if 'groups' in line.lower() and park_id in line:
                            log(f"    {line.strip()}")
                            break
                else:
                    log(f"    Error loading {park_id}: {result.stderr[:200]}")
                    self._fail('load', f"{park_id} rc={result.returncode}")
                    
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
    
    def update_fire_group_alerts(self):
        """Update fire_group_alerts table from feature_geometries.
        
        This syncs the alerts table with the latest pipeline output,
        ensuring group names and statuses match current data.
        """
        log("Step 6a: Updating fire_group_alerts from feature_geometries...")
        
        import requests
        try:
            response = requests.post(
                "http://localhost:8000/api/update-fire-alerts",
                params={'pwd': app_password()},
                timeout=60
            )
            if response.status_code == 200:
                log("  Successfully updated fire_group_alerts")
            else:
                log(f"  Error: {response.status_code} - {response.text}")
        except Exception as e:
            log(f"  Error calling update-fire-alerts API: {e}")
        
        # Clean up old alerts to prevent table pollution
        log("  Cleaning up old fire_group_alerts...")
        deleted = self.conn.execute("""
            DELETE FROM fire_group_alerts
            WHERE alert_type = 'left' AND left_at < datetime('now', '-7 days')
        """).rowcount
        
        deleted2 = self.conn.execute("""
            DELETE FROM fire_group_alerts
            WHERE alert_type = 'entered' AND last_updated_at < datetime('now', '-14 days')
        """).rowcount
        
        self.conn.commit()
        log(f"  Cleaned {deleted + deleted2} old alerts")
        try:
            self.stats['alerts_created'] = self.conn.execute(
                "SELECT COUNT(*) FROM fire_group_alerts "
                "WHERE last_updated_at > datetime('now', '-1 hour')").fetchone()[0]
        except Exception:
            pass
    
    def assign_friendly_names_to_new_groups(self):
        """Assign stable friendly names to new fire groups (like hurricane naming).
        
        Names are assigned chronologically (by start_date) and persist across days.
        This ensures managers can track the same fire over time.
        """
        log("Step 6b1: Assigning friendly names to new fire groups...")
        
        # NATO phonetic alphabet for friendly names
        group_names = [
            "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
            "India", "Juliet", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
            "Quebec", "Romeo", "Sierra", "Tango", "Uniform", "Victor", "Whiskey",
            "Xray", "Yankee", "Zulu"
        ]
        
        def get_friendly_name(index):
            if index < len(group_names):
                return group_names[index]
            cycle = index // len(group_names) + 1
            base_idx = index % len(group_names)
            return f"{group_names[base_idx]}-{cycle}"
        
        # Get current RELEVANT fire trajectories (inside park or <=20km from
        # boundary; NULL dist = unknown = treated relevant). Names are a
        # park-scoped resource - don't burn Alpha..Zulu on fires 80km out.
        # Groups already named keep their names (continuity); we just stop
        # naming new far-away groups.
        cursor = self.conn.execute("""
            SELECT park_id, feature_id, start_date, end_date
            FROM feature_geometries
            WHERE feature_type = 'fire_trajectory'
              AND feature_id LIKE '%_2026_grp_%'
              AND (dist_to_park_km IS NULL OR dist_to_park_km <= 20)
            ORDER BY park_id, start_date ASC
        """)
        
        park_groups = {}
        for row in cursor:
            park_id, feature_id, start_date, end_date = row
            if park_id not in park_groups:
                park_groups[park_id] = []
            park_groups[park_id].append({
                'feature_id': feature_id,
                'start_date': start_date,
                'end_date': end_date
            })
        
        new_names_assigned = 0
        
        for park_id, groups in park_groups.items():
            # Sort by start_date (chronological)
            groups.sort(key=lambda x: x['start_date'])
            
            for i, group in enumerate(groups):
                feature_id = group['feature_id']
                
                # Check if name already exists
                check = self.conn.execute(
                    "SELECT friendly_name FROM fire_group_names WHERE park_id = ? AND feature_id = ?",
                    (park_id, feature_id)
                ).fetchone()
                
                if not check:
                    # Assign new name based on chronological order
                    friendly_name = get_friendly_name(i)
                    
                    self.conn.execute("""
                        INSERT INTO fire_group_names
                        (park_id, feature_id, friendly_name, first_seen_date, last_seen_date)
                        VALUES (?, ?, ?, ?, ?)
                    """, (park_id, feature_id, friendly_name, group['start_date'], group['end_date']))
                    
                    new_names_assigned += 1
                else:
                    # Update last_seen_date for existing groups
                    self.conn.execute("""
                        UPDATE fire_group_names
                        SET last_seen_date = ?
                        WHERE park_id = ? AND feature_id = ?
                    """, (group['end_date'], park_id, feature_id))
        
        self.conn.commit()
        log(f"  Assigned {new_names_assigned} new friendly names")
    
    def analyze_fire_status(self, props, end_date):
        """Analyze fire status based on trajectory data.
        
        Returns: (status_emoji, status_text, detail_message)
        """
        from datetime import datetime
        
        position = props.get('position', 'unknown')
        direction = props.get('direction', '?')
        speed_km_day = props.get('avg_speed_km_day', 0)
        pct_inside = props.get('pct_inside', 0)
        cross_border = props.get('cross_border', False)
        days = props.get('days', 1)
        
        # Calculate days since last detection
        try:
            last_seen = datetime.strptime(end_date, '%Y-%m-%d')
            days_since = (datetime.now() - last_seen).days
        except:
            days_since = 0
        
        # Gone dark detection
        if days_since >= 3:
            return "🌙", "Gone dark", "No detections for 3+ days"
        elif days_since >= 2:
            return "❄️", "Cooling", "No new fires in 2 days"
        
        # Position-based status
        if position == 'contained':
            status_emoji = "📍"
            status_text = "Contained"
            detail = "Fully inside park"
        elif position == 'entirely_outside':
            if pct_inside == 0 and speed_km_day > 0:
                status_emoji = "⚠️"
                status_text = "Approaching"
                detail = f"Outside, moving {direction}"
            else:
                status_emoji = "🔥"
                status_text = "Outside"
                detail = "Outside park boundary"
        elif position == 'starts_inside':
            status_emoji = "🚨"
            status_text = "Leaving"
            detail = f"Started inside, moving {direction} toward boundary"
        elif position == 'ends_inside':
            status_emoji = "⚡"
            status_text = "Entering"
            detail = f"Crossing into park from {direction}"
        elif position == 'transits':
            if cross_border:
                status_emoji = "🌊"
                status_text = "Transiting"
                detail = f"Crossing park boundary, moving {direction}"
            else:
                status_emoji = "🔥"
                status_text = "Spreading"
                detail = f"Active inside, {pct_inside:.0f}% in park"
        else:
            status_emoji = "🔥"
            status_text = "Active"
            detail = f"Moving {direction}"
        
        # Add velocity info
        if speed_km_day > 2:
            detail += f" at {speed_km_day:.1f}km/day (fast)"
        elif speed_km_day > 0.5:
            detail += f" at {speed_km_day:.1f}km/day"
        elif speed_km_day > 0:
            detail += " (slow spread)"
        elif days == 1:
            detail += " (new detection)"
        else:
            detail += " (stationary)"
        
        return status_emoji, status_text, detail
    

    def create_fire_notifications(self):
        """Create notifications for active fire groups using stored friendly names.
        
        Friendly names are stable and persist across days (like hurricane tracking).
        """
        log("Step 6b2: Creating notifications for active fire groups...")
        
        # Priority mapping for sorting
        def get_priority(status):
            priority_map = {
                "Entering": 5,
                "Approaching": 10,
                "Transiting": 15,
                "Active": 40,
                "Contained": 50,
                "Leaving": 55,
                "Gone dark": 60,
                "Cooling": 70,
                "Outside": 80
            }
            return priority_map.get(status, 100)
        
        # Query active RELEVANT fire trajectories with their friendly names.
        # Gated to inside-park or <=20km from boundary (NULL = unknown =
        # relevant). Fires further out stay visible on the map layer but
        # don't generate notifications.
        cursor = self.conn.execute("""
            SELECT fg.park_id, fg.feature_id, fg.properties_json, fg.end_date, fgn.friendly_name
            FROM feature_geometries fg
            JOIN fire_group_names fgn ON fg.park_id = fgn.park_id AND fg.feature_id = fgn.feature_id
            WHERE fg.feature_type = 'fire_trajectory'
              AND fg.end_date >= date('now', '-3 days')
              AND (fg.dist_to_park_km IS NULL OR fg.dist_to_park_km <= 20)
            ORDER BY fg.end_date DESC
        """)
        
        # Sort by priority (lowest number first), then by park_id
        all_groups_with_priority = []
        for row in cursor:
            park_id, feature_id, props_json, end_date, friendly_name = row
            props = json.loads(props_json)
            
            # Get enhanced status
            status_emoji, status_text, status_detail = self.analyze_fire_status(props, end_date)
            priority = get_priority(status_text)
            
            all_groups_with_priority.append({
                'park_id': park_id,
                'feature_id': feature_id,
                'friendly_name': friendly_name,
                'props': props,
                'end_date': end_date,
                'status_emoji': status_emoji,
                'status_text': status_text,
                'status_detail': status_detail,
                'priority': priority
            })
        
        # Sort by priority (highest priority first), then by park
        all_groups_with_priority.sort(key=lambda x: (x['priority'], x['park_id']))
        
        notifications_created = 0
        parks_processed = set()
        suppressed_by_park = defaultdict(int)
        per_park_count = defaultdict(int)

        # Which (group, status) pairs have we already told the user about?
        # Notifying per-group-per-7-days produced 509-1132 alerts/night in peak
        # season (25k in five weeks, 93 for one park in one night), which makes
        # the panel useless. Now a group is only re-announced when its status
        # actually CHANGES, so "Active -> Entering" alerts but "Active -> Active"
        # is silent.
        last_status = {}
        for pid, ref, title in self.conn.execute("""
            SELECT park_id, reference_id, title FROM notifications
            WHERE notification_type = 'fire_alert'
              AND created_at > datetime('now', '-30 days')
              AND reference_id IS NOT NULL
            ORDER BY created_at ASC
        """):
            m = re.search(r'\(([^)]*)\)\s*$', title or '')
            if m:
                last_status[(pid, ref)] = m.group(1)

        # Create notifications in priority order
        for group in all_groups_with_priority:
            park_id = group['park_id']
            feature_id = group['feature_id']
            friendly_name = group['friendly_name']
            props = group['props']
            status_emoji = group['status_emoji']
            status_text = group['status_text']
            status_detail = group['status_detail']
            
            park_name = self.parks.get(park_id, {}).get('name', park_id)

            # Only announce genuinely new information.
            if last_status.get((park_id, feature_id)) == status_text:
                continue

            # Cap per park per run. Groups are pre-sorted by priority, so the
            # ones that survive the cap are the most urgent (Entering /
            # Approaching before Cooling / Outside).
            if per_park_count[park_id] >= MAX_ALERTS_PER_PARK:
                suppressed_by_park[park_id] += 1
                continue
            
            fires_total = props.get('fires_total', 0)
            days = props.get('days', 1)
            
            # Create notification with stable friendly name + enhanced status
            title = f"{status_emoji} {friendly_name} ({status_text})"
            message = f"{fires_total} fires, {days} days • {status_detail}"
            
            reference_data = json.dumps({
                'park_id': park_id,
                'park_name': park_name,
                'feature_id': feature_id,
                'type': 'fire_trajectory',
                'group_name': friendly_name,
                'status': status_text,
                'status_detail': status_detail
            })
            
            self.conn.execute("""
                INSERT INTO notifications
                (park_id, notification_type, title, message, reference_id, reference_data, created_at)
                VALUES (?, 'fire_alert', ?, ?, ?, ?, datetime('now'))
            """, (park_id, title, message, feature_id, reference_data))
            
            notifications_created += 1
            per_park_count[park_id] += 1
            parks_processed.add(park_id)

        # One rollup per park for everything the cap suppressed, so the
        # information is still available without flooding the panel.
        for park_id, n in sorted(suppressed_by_park.items()):
            park_name = self.parks.get(park_id, {}).get('name', park_id)
            self.conn.execute("""
                INSERT INTO notifications
                (park_id, notification_type, title, message, reference_id,
                 reference_data, created_at)
                VALUES (?, 'fire_alert', ?, ?, ?, ?, datetime('now'))
            """, (park_id,
                  f"🔥 {n} more active fire groups",
                  f"{n} additional fire groups changed status in {park_name}. "
                  f"Open the park to see all of them.",
                  f"{park_id}_rollup_{datetime.now().strftime('%Y%m%d')}",
                  json.dumps({'park_id': park_id, 'park_name': park_name,
                              'type': 'fire_rollup', 'suppressed': n})))
            notifications_created += 1
            parks_processed.add(park_id)
        
        self.conn.commit()
        
        # Log per-park counts
        if notifications_created > 0:
            cursor = self.conn.execute("""
                SELECT park_id, COUNT(*) as count
                FROM notifications
                WHERE notification_type = 'fire_alert'
                  AND created_at > datetime('now', '-1 minute')
                GROUP BY park_id
                ORDER BY count DESC
            """)
            
            for row in cursor:
                park_id, count = row
                log(f"  {park_id}: Created {count} notifications")
        
        log(f"  Total: {notifications_created} notifications across {len(parks_processed)} parks")
        self.stats['notifications_created'] = notifications_created
    

    
    def run(self):
        try:
            self._run()
        except Exception as e:
            log(f"FATAL: {e}")
            self.write_status(fatal=e)
            self._notify_system('fire_pipeline_failed', 'Fire Pipeline Failed',
                                f"daily_fire_update aborted: {e}")
            try:
                self.conn.close()
            except Exception:
                pass
            raise
        self.write_status()
        self.conn.close()

    def _run(self):
        log("=" * 70)
        log("DAILY FIRE UPDATE PIPELINE (v5 - Incremental)")
        log("=" * 70)
        log(f"Date: {datetime.now().isoformat()}")
        log(f"Days to fetch: {self.days}")
        log(f"Incremental window: {INCREMENTAL_DAYS} days")
        log("")
        
        # Step 1: Download NRT fires
        fires = self.download_nrt_fires()
        
        # Step 1b: Canonical park assignment (one park per fire, <=100km)
        self._assign_fires(fires)
        
        # Step 2: Insert into database
        self.insert_fires(fires)
        
        # Step 2c: Refresh pre-aggregated animation grids (fire_grid_day/week/month)
        self.refresh_grid_agg()
        
        # Step 2d: Persistent hotspot mask (monthly; no-op other days)
        self.refresh_persistent_hotspots()
        
        # Step 3: Rebuild groups (incremental)
        self.rebuild_groups_incremental()
        
        # Step 4: Load to database
        self.load_groups_incremental()
        
        # Step 5: Update narratives
        self.update_narratives()
        
        # Step 6a: Update fire_group_alerts table
        self.update_fire_group_alerts()
        
        # Step 6b1: Assign friendly names to new groups
        self.assign_friendly_names_to_new_groups()
        
        # Step 6b2: Create notifications with stable names
        self.create_fire_notifications()
        
        # Step 7: Consistency check (cheap, read-only, catches silent drift)
        self.check_consistency()
        
        log("")
        log("=" * 70)
        log("PIPELINE COMPLETE")
        log(f"Affected parks: {sorted(self.affected_parks)}")
        log("=" * 70)
        

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
