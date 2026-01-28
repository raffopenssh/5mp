#!/usr/bin/env python3
"""
Regenerate fire trajectories from existing fire_detections table.

This script analyzes existing fire detections to generate trajectory data
without needing to re-process the raw ZIP files.

Usage:
    python scripts/regenerate_trajectories.py [--park PARK_ID] [--year YEAR]
"""

import json
import sqlite3
import logging
import argparse
import gc
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import numpy as np

try:
    from shapely.geometry import shape, Point
    from shapely.prepared import prep
    from sklearn.cluster import DBSCAN
    HAS_GEO = True
except ImportError as e:
    print(f"Missing: {e}. Run: pip install shapely scikit-learn numpy")
    HAS_GEO = False

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_PATH = BASE_DIR / "data" / "keystones_with_boundaries.json"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_keystones() -> List[Dict]:
    """Load park boundaries"""
    with open(KEYSTONES_PATH) as f:
        return [p for p in json.load(f) if p.get('geometry')]


def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km"""
    from math import radians, sin, cos, sqrt, atan2
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))


def get_park_fires(conn, park_id: str, year: int) -> List[Dict]:
    """Get all fires for a park in a given year"""
    cursor = conn.execute("""
        SELECT latitude, longitude, acq_date, acq_time, frp, confidence
        FROM fire_detections
        WHERE protected_area_id = ? 
        AND strftime('%Y', acq_date) = ?
        ORDER BY acq_date, acq_time
    """, (park_id, str(year)))
    
    return [dict(row) for row in cursor.fetchall()]


def get_buffer_fires(conn, park_geom, year: int, buffer_km: float = 200) -> List[Dict]:
    """Get fires in buffer zone around park"""
    # Get bounding box with buffer
    bounds = park_geom.bounds
    buffer_deg = buffer_km / 111  # rough conversion
    
    min_lon = bounds[0] - buffer_deg
    min_lat = bounds[1] - buffer_deg
    max_lon = bounds[2] + buffer_deg
    max_lat = bounds[3] + buffer_deg
    
    cursor = conn.execute("""
        SELECT latitude, longitude, acq_date, acq_time, frp, confidence
        FROM fire_detections
        WHERE strftime('%Y', acq_date) = ?
        AND latitude BETWEEN ? AND ?
        AND longitude BETWEEN ? AND ?
        AND (protected_area_id IS NULL OR protected_area_id = '')
        ORDER BY acq_date, acq_time
    """, (str(year), min_lat, max_lat, min_lon, max_lon))
    
    return [dict(row) for row in cursor.fetchall()]


def cluster_daily_fires(fires: List[Dict], eps_km: float = 5.0) -> Dict[str, List]:
    """Cluster fires by day and spatial proximity"""
    by_date = defaultdict(list)
    for f in fires:
        by_date[f['acq_date']].append(f)
    
    daily_clusters = {}
    for date, day_fires in by_date.items():
        if len(day_fires) < 2:
            # Single fire = own cluster
            for i, f in enumerate(day_fires):
                daily_clusters[f"{date}_c{i}"] = [f]
            continue
        
        # DBSCAN clustering
        coords = np.array([[f['latitude'], f['longitude']] for f in day_fires])
        # Convert eps from km to degrees (rough)
        eps_deg = eps_km / 111
        
        db = DBSCAN(eps=eps_deg, min_samples=1, metric='euclidean')
        labels = db.fit_predict(coords)
        
        clusters = defaultdict(list)
        for fire, label in zip(day_fires, labels):
            clusters[label].append(fire)
        
        for label, cluster_fires in clusters.items():
            cluster_id = f"{date}_c{label}"
            daily_clusters[cluster_id] = cluster_fires
    
    return daily_clusters


def track_clusters(daily_clusters: Dict[str, List], max_gap_days: int = 3, max_dist_km: float = 50) -> List[Dict]:
    """Link daily clusters into trajectories"""
    # Sort clusters by date
    sorted_clusters = sorted(daily_clusters.items(), key=lambda x: x[0])
    
    trajectories = []
    used_clusters = set()
    
    for cluster_id, fires in sorted_clusters:
        if cluster_id in used_clusters:
            continue
        
        date_str = cluster_id.split('_')[0]
        centroid_lat = np.mean([f['latitude'] for f in fires])
        centroid_lon = np.mean([f['longitude'] for f in fires])
        
        traj = {
            'clusters': [cluster_id],
            'dates': [date_str],
            'centroids': [(centroid_lat, centroid_lon)],
            'fires': fires.copy()
        }
        used_clusters.add(cluster_id)
        
        # Try to extend trajectory forward
        current_date = datetime.strptime(date_str, '%Y-%m-%d')
        current_lat, current_lon = centroid_lat, centroid_lon
        
        for _ in range(100):  # Max trajectory length
            found_next = False
            
            for gap in range(1, max_gap_days + 1):
                next_date = current_date + timedelta(days=gap)
                next_date_str = next_date.strftime('%Y-%m-%d')
                
                # Find closest unused cluster on this date
                best_cluster = None
                best_dist = float('inf')
                
                for cid, cfs in sorted_clusters:
                    if cid in used_clusters:
                        continue
                    if not cid.startswith(next_date_str):
                        continue
                    
                    c_lat = np.mean([f['latitude'] for f in cfs])
                    c_lon = np.mean([f['longitude'] for f in cfs])
                    dist = distance_km(current_lat, current_lon, c_lat, c_lon)
                    
                    if dist < max_dist_km and dist < best_dist:
                        best_dist = dist
                        best_cluster = (cid, cfs, c_lat, c_lon)
                
                if best_cluster:
                    cid, cfs, c_lat, c_lon = best_cluster
                    traj['clusters'].append(cid)
                    traj['dates'].append(next_date_str)
                    traj['centroids'].append((c_lat, c_lon))
                    traj['fires'].extend(cfs)
                    used_clusters.add(cid)
                    current_date = next_date
                    current_lat, current_lon = c_lat, c_lon
                    found_next = True
                    break
            
            if not found_next:
                break
        
        trajectories.append(traj)
    
    return trajectories


def analyze_trajectory(traj: Dict, park_geom, park_prep) -> Optional[Dict]:
    """Analyze a trajectory for park infraction"""
    fires_inside = [f for f in traj['fires'] 
                    if park_prep.contains(Point(f['longitude'], f['latitude']))]
    
    if not fires_inside:
        return None
    
    # Get dates
    dates_inside = sorted(set(f['acq_date'] for f in fires_inside))
    all_dates = sorted(traj['dates'])
    
    if not dates_inside:
        return None
    
    entry_date = dates_inside[0]
    last_inside_date = dates_inside[-1]
    origin_date = all_dates[0]
    dest_date = all_dates[-1]
    
    # Determine outcome
    entry_idx = all_dates.index(entry_date) if entry_date in all_dates else 0
    last_inside_idx = all_dates.index(last_inside_date) if last_inside_date in all_dates else len(all_dates) - 1
    
    # Did they continue burning after leaving?
    continued_after = last_inside_idx < len(all_dates) - 1
    
    if continued_after:
        outcome = 'TRANSITED'
    else:
        outcome = 'STOPPED_INSIDE'
    
    # Get origin and destination centroids
    origin_lat, origin_lon = traj['centroids'][0]
    dest_lat, dest_lon = traj['centroids'][-1]
    
    return {
        'origin_lat': origin_lat,
        'origin_lon': origin_lon,
        'origin_date': origin_date,
        'dest_lat': dest_lat,
        'dest_lon': dest_lon,
        'dest_date': dest_date,
        'entry_date': entry_date,
        'last_inside_date': last_inside_date,
        'days_burning_inside': len(dates_inside),
        'fires_inside': len(fires_inside),
        'outcome': outcome,
        'centroids': traj['centroids'][:20]  # Limit for storage
    }


def process_park_year(park: Dict, year: int, conn) -> Dict:
    """Process a single park-year combination"""
    park_id = park['id']
    logger.info(f"Processing {park_id} {year}")
    
    try:
        park_geom = shape(park['geometry'])
        if not park_geom.is_valid:
            park_geom = park_geom.buffer(0)
        park_prep = prep(park_geom)
    except Exception as e:
        logger.warning(f"  Invalid geometry: {e}")
        return {'error': str(e)}
    
    # Get fires inside park
    inside_fires = get_park_fires(conn, park_id, year)
    if not inside_fires:
        return {'inside_fires': 0, 'groups': 0}
    
    logger.info(f"  {len(inside_fires)} fires inside park")
    
    # Get fires in buffer zone
    buffer_fires = get_buffer_fires(conn, park_geom, year, buffer_km=200)
    logger.info(f"  {len(buffer_fires)} fires in buffer zone")
    
    # Combine and cluster
    all_fires = inside_fires + buffer_fires
    daily_clusters = cluster_daily_fires(all_fires)
    
    # Track trajectories
    trajectories = track_clusters(daily_clusters)
    
    # Analyze for infractions
    infraction_trajs = []
    for traj in trajectories:
        result = analyze_trajectory(traj, park_geom, park_prep)
        if result:
            infraction_trajs.append(result)
    
    if not infraction_trajs:
        return {'inside_fires': len(inside_fires), 'groups': 0}
    
    # Calculate stats
    stopped = len([t for t in infraction_trajs if t['outcome'] == 'STOPPED_INSIDE'])
    transited = len([t for t in infraction_trajs if t['outcome'] == 'TRANSITED'])
    avg_days = np.mean([t['days_burning_inside'] for t in infraction_trajs])
    
    # Store trajectory summary
    traj_summary = [{
        'origin': {'lat': t['origin_lat'], 'lon': t['origin_lon'], 'date': t['origin_date']},
        'dest': {'lat': t['dest_lat'], 'lon': t['dest_lon'], 'date': t['dest_date']},
        'entry_date': t['entry_date'],
        'last_inside': t['last_inside_date'],
        'days_inside': t['days_burning_inside'],
        'fires_inside': t['fires_inside'],
        'outcome': t['outcome'],
        'path': t['centroids']
    } for t in infraction_trajs]
    
    # Update database
    conn.execute("""INSERT OR REPLACE INTO park_group_infractions 
        (park_id, year, total_groups, groups_stopped_inside, groups_transited, avg_days_burning, analyzed_at, trajectories_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (park_id, year, len(infraction_trajs), stopped, transited, round(avg_days, 2), 
         datetime.now().isoformat(), json.dumps(traj_summary)))
    conn.commit()
    
    logger.info(f"  {len(infraction_trajs)} groups, {stopped} stopped, {transited} transited")
    
    return {
        'inside_fires': len(inside_fires), 
        'groups': len(infraction_trajs), 
        'stopped': stopped, 
        'transited': transited
    }


def main():
    parser = argparse.ArgumentParser(description='Regenerate fire trajectories')
    parser.add_argument('--park', type=str, help='Process specific park only')
    parser.add_argument('--year', type=int, help='Process specific year only')
    args = parser.parse_args()
    
    if not HAS_GEO:
        logger.error("Missing required libraries")
        return
    
    conn = get_db()
    keystones = load_keystones()
    
    # Get years with fire data
    years_result = conn.execute(
        "SELECT DISTINCT strftime('%Y', acq_date) as year FROM fire_detections WHERE protected_area_id IS NOT NULL"
    ).fetchall()
    years = [int(r[0]) for r in years_result if r[0]]
    
    if args.year:
        years = [args.year]
    
    if args.park:
        keystones = [p for p in keystones if p['id'] == args.park]
    
    logger.info(f"Processing {len(keystones)} parks for years {years}")
    
    total_groups = 0
    for park in keystones:
        for year in years:
            try:
                result = process_park_year(park, year, conn)
                total_groups += result.get('groups', 0)
            except Exception as e:
                logger.error(f"Error processing {park['id']} {year}: {e}")
            
            gc.collect()
    
    conn.close()
    logger.info(f"Completed. Total groups found: {total_groups}")


if __name__ == '__main__':
    main()
