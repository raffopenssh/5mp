#!/usr/bin/env python3
"""
Fire Processor - Streaming from ZIP files

Processes VIIRS fire data directly from ZIP archives without extracting.
Memory-efficient: processes one country/year at a time.

Usage:
    python scripts/fire_processor_streaming.py [--park PARK_ID] [--year YEAR]
    
For bulk upload:
    python scripts/fire_processor_streaming.py --zip /path/to/viirs_data.zip
"""

import json
import sqlite3
import zipfile
import io
import csv
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Iterator
import numpy as np

try:
    from shapely.geometry import shape, Point
    from shapely.prepared import prep
    from sklearn.cluster import DBSCAN
except ImportError as e:
    print(f"Missing: {e}. Run: pip install shapely scikit-learn")
    exit(1)

# Configuration
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "db.sqlite3"
PROGRESS_FILE = BASE_DIR / "logs" / "fire_progress_streaming.json"
KEYSTONES_PATH = DATA_DIR / "keystones_with_boundaries.json"

YEARS = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]

# African countries in the ZIP (filename format)
AFRICAN_COUNTRIES = [
    'Algeria', 'Angola', 'Benin', 'Botswana', 'Cameroon',
    'Central_African_Republic', 'Chad', 'Cote_d_Ivoire',
    'Democratic_Republic_of_the_Congo', 'Equatorial_Guinea',
    'Ethiopia', 'Gabon', 'Ghana', 'Kenya', 'Lesotho', 'Liberia',
    'Malawi', 'Mali', 'Mozambique', 'Namibia', 'Niger', 'Nigeria',
    'Republic_of_Congo', 'Rwanda', 'Senegal', 'South_Africa',
    'South_Sudan', 'Sudan', 'Tanzania', 'Togo', 'Uganda', 'Zambia', 'Zimbabwe'
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_keystones() -> List[Dict]:
    """Load park boundaries"""
    with open(KEYSTONES_PATH) as f:
        return [p for p in json.load(f) if p.get('geometry')]


def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def stream_fires_from_zip(zip_path: Path, year: int = None, africa_only: bool = True) -> Iterator[Dict]:
    """
    Stream fire records from a ZIP file without extracting.
    Yields one record at a time to minimize memory usage.
    
    Args:
        zip_path: Path to the ZIP file
        year: Filter by year (optional)
        africa_only: If True, only process African countries (much faster)
    """
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for name in zf.namelist():
            # Skip macOS metadata files
            if '__MACOSX' in name or name.startswith('._'):
                continue
            if not name.endswith('.csv'):
                continue
            # Filter by year if specified
            if year and str(year) not in name:
                continue
            
            # Filter by African countries if specified
            if africa_only:
                is_african = any(country in name for country in AFRICAN_COUNTRIES)
                if not is_african:
                    continue
                
            logger.info(f"  Processing {name}...")
            try:
                with zf.open(name) as f:
                    # Read CSV from zip with error handling for encoding
                    text_wrapper = io.TextIOWrapper(f, encoding='utf-8', errors='ignore')
                    reader = csv.DictReader(text_wrapper)
                    for row in reader:
                        try:
                            yield {
                                'latitude': float(row.get('latitude', 0)),
                                'longitude': float(row.get('longitude', 0)),
                                'acq_date': row.get('acq_date', ''),
                                'acq_time': row.get('acq_time', ''),
                                'bright_ti4': float(row.get('bright_ti4', 0)) if row.get('bright_ti4') else None,
                                'scan': float(row.get('scan', 0)) if row.get('scan') else None,
                                'track': float(row.get('track', 0)) if row.get('track') else None,
                                'satellite': row.get('satellite', ''),
                                'instrument': row.get('instrument', ''),
                                'confidence': row.get('confidence', ''),
                                'version': row.get('version', ''),
                                'bright_ti5': float(row.get('bright_ti5', 0)) if row.get('bright_ti5') else None,
                                'frp': float(row.get('frp', 0)) if row.get('frp') else None,
                                'daynight': row.get('daynight', ''),
                            }
                        except (ValueError, TypeError):
                            continue
            except Exception as e:
                logger.warning(f"  Error reading {name}: {e}")
                continue


def stream_fires_from_directory(data_dir: Path, year: int, bbox: Tuple[float, float, float, float]) -> Iterator[Dict]:
    """
    Stream fires from directory of CSVs or ZIPs, filtering by bbox.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    year_dir = data_dir / "fire" / "viirs-jpss" / str(year)
    
    # Check for ZIP files first
    for zip_path in data_dir.glob("fire/*.zip"):
        for fire in stream_fires_from_zip(zip_path, year):
            lat, lon = fire['latitude'], fire['longitude']
            if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                yield fire
    
    # Also check year directory for CSVs
    if year_dir.exists():
        for csv_path in year_dir.glob("*.csv"):
            logger.debug(f"  Reading {csv_path.name}")
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        lat = float(row.get('latitude', 0))
                        lon = float(row.get('longitude', 0))
                        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                            continue
                        yield {
                            'latitude': lat,
                            'longitude': lon,
                            'acq_date': row.get('acq_date', ''),
                            'acq_time': row.get('acq_time', ''),
                            'bright_ti4': float(row.get('bright_ti4', 0)) if row.get('bright_ti4') else None,
                            'scan': float(row.get('scan', 0)) if row.get('scan') else None,
                            'track': float(row.get('track', 0)) if row.get('track') else None,
                            'satellite': row.get('satellite', ''),
                            'instrument': row.get('instrument', ''),
                            'confidence': row.get('confidence', ''),
                            'version': row.get('version', ''),
                            'bright_ti5': float(row.get('bright_ti5', 0)) if row.get('bright_ti5') else None,
                            'frp': float(row.get('frp', 0)) if row.get('frp') else None,
                            'daynight': row.get('daynight', ''),
                        }
                    except (ValueError, TypeError):
                        continue


def get_park_bbox(park: Dict, buffer_km: float = 300) -> Tuple[float, float, float, float]:
    """Get bounding box for park with buffer (for fire group tracking)"""
    geom = shape(park['geometry'])
    bounds = geom.bounds  # (minx, miny, maxx, maxy)
    # Add buffer (~3 degrees for 300km at equator)
    buffer_deg = buffer_km / 111
    return (
        bounds[0] - buffer_deg,
        bounds[1] - buffer_deg,
        bounds[2] + buffer_deg,
        bounds[3] + buffer_deg
    )


def detect_fire_groups(fires: List[Dict], eps_km: float = 5, min_samples: int = 3) -> Dict[str, List[Dict]]:
    """
    Cluster fires by date using DBSCAN.
    Returns dict of date -> list of cluster centroids
    """
    # Group by date
    by_date = {}
    for f in fires:
        d = f['acq_date']
        if d not in by_date:
            by_date[d] = []
        by_date[d].append(f)
    
    daily_clusters = {}
    for date, day_fires in by_date.items():
        if len(day_fires) < min_samples:
            continue
        
        coords = np.array([[f['latitude'], f['longitude']] for f in day_fires])
        # DBSCAN with haversine-like distance (approximate)
        db = DBSCAN(eps=eps_km/111, min_samples=min_samples)
        labels = db.fit_predict(coords)
        
        clusters = []
        for label in set(labels):
            if label == -1:
                continue
            mask = labels == label
            cluster_fires = [day_fires[i] for i in range(len(day_fires)) if mask[i]]
            centroid_lat = np.mean([f['latitude'] for f in cluster_fires])
            centroid_lon = np.mean([f['longitude'] for f in cluster_fires])
            clusters.append({
                'cid': label,
                'lat': centroid_lat,
                'lon': centroid_lon,
                'fires': len(cluster_fires),
                'date': date,
                'avg_frp': np.mean([f['frp'] or 0 for f in cluster_fires])
            })
        
        if clusters:
            daily_clusters[date] = clusters
    
    return daily_clusters


def track_fire_groups(daily_clusters: Dict, max_dist_km: float = 20, max_gap_days: int = 3) -> List[List[Dict]]:
    """Link daily clusters into trajectories"""
    if not daily_clusters:
        return []
    
    trajectories = []
    used = set()
    sorted_dates = sorted(daily_clusters.keys())
    
    for start_idx, start_date in enumerate(sorted_dates):
        for cluster in daily_clusters[start_date]:
            key = (start_date, cluster['cid'])
            if key in used:
                continue
            
            # Start new trajectory
            traj = [cluster]
            used.add(key)
            current = cluster
            
            # Follow forward
            for next_idx in range(start_idx + 1, len(sorted_dates)):
                next_date = sorted_dates[next_idx]
                
                # Check gap
                try:
                    d1 = datetime.strptime(current['date'], '%Y-%m-%d')
                    d2 = datetime.strptime(next_date, '%Y-%m-%d')
                    gap = (d2 - d1).days
                except:
                    continue
                
                if gap > max_gap_days:
                    break
                
                # Find closest cluster
                best = None
                best_dist = float('inf')
                for c in daily_clusters[next_date]:
                    nkey = (next_date, c['cid'])
                    if nkey in used:
                        continue
                    dist = np.sqrt((c['lat'] - current['lat'])**2 + (c['lon'] - current['lon'])**2) * 111
                    if dist < best_dist and dist < max_dist_km:
                        best_dist = dist
                        best = c
                
                if best:
                    traj.append(best)
                    used.add((next_date, best['cid']))
                    current = best
            
            if len(traj) >= 3:
                trajectories.append(traj)
    
    return trajectories


def analyze_trajectory(traj: List[Dict], boundary_prep) -> Optional[Dict]:
    """Analyze a fire group trajectory relative to park boundary"""
    if not traj or len(traj) < 3:
        return None
    
    inside_pts = [c for c in traj if boundary_prep.contains(Point(c['lon'], c['lat']))]
    if not inside_pts:
        return None
    
    # Determine outcome
    last_inside_idx = max(i for i, c in enumerate(traj) if boundary_prep.contains(Point(c['lon'], c['lat'])))
    if last_inside_idx == len(traj) - 1:
        outcome = 'STOPPED_INSIDE'
    else:
        outcome = 'TRANSITED'
    
    return {
        'origin_lat': round(traj[0]['lat'], 4),
        'origin_lon': round(traj[0]['lon'], 4),
        'origin_date': traj[0]['date'],
        'entry_date': inside_pts[0]['date'],
        'days_burning_inside': len(inside_pts),
        'fires_inside': sum(c['fires'] for c in inside_pts),
        'last_inside_date': inside_pts[-1]['date'],
        'outcome': outcome,
        'dest_lat': round(traj[-1]['lat'], 4),
        'dest_lon': round(traj[-1]['lon'], 4),
        'dest_date': traj[-1]['date'],
        'trajectory_days': len(traj),
        'centroids': [{'date': c['date'], 'lat': round(c['lat'], 4), 'lon': round(c['lon'], 4), 'fires': c['fires']} for c in traj]
    }


def process_park_year(park: Dict, year: int, conn, data_dir: Path = DATA_DIR) -> Optional[Dict]:
    """Process fire data for a single park and year"""
    park_id = park['id']
    
    # Get boundary
    try:
        boundary = shape(park['geometry'])
        boundary_prep = prep(boundary)
    except Exception as e:
        logger.warning(f"Bad geometry for {park_id}: {e}")
        return None
    
    bbox = get_park_bbox(park)
    
    # Stream fires for this region/year
    fires = list(stream_fires_from_directory(data_dir, year, bbox))
    if len(fires) < 50:
        return {'inside_fires': 0, 'groups': 0}
    
    # Save inside-park fires to database
    inside_fires = []
    for f in fires:
        if boundary_prep.contains(Point(f['longitude'], f['latitude'])):
            inside_fires.append((
                f['latitude'], f['longitude'], f['bright_ti4'], f['scan'],
                f['track'], f['acq_date'], f['acq_time'], f['satellite'],
                f['instrument'], f['confidence'], f['version'],
                f['bright_ti5'], f['frp'], f['daynight'], 1, park_id
            ))
    
    if inside_fires:
        conn.executemany("""INSERT OR IGNORE INTO fire_detections 
            (latitude, longitude, brightness, scan, track, acq_date, acq_time, satellite,
             instrument, confidence, version, bright_t31, frp, daynight, in_protected_area, protected_area_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", inside_fires)
    
    # Detect and track fire groups
    daily_clusters = detect_fire_groups(fires)
    if len(daily_clusters) < 5:
        return {'inside_fires': len(inside_fires), 'groups': 0}
    
    trajectories = track_fire_groups(daily_clusters)
    
    # Analyze trajectories for infractions
    infraction_trajs = []
    for traj in trajectories:
        result = analyze_trajectory(traj, boundary_prep)
        if result:
            infraction_trajs.append(result)
    
    if not infraction_trajs:
        return {'inside_fires': len(inside_fires), 'groups': 0}
    
    # Calculate summary stats
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
        'path': t['centroids'][:20]
    } for t in infraction_trajs]
    
    conn.execute("""INSERT OR REPLACE INTO park_group_infractions 
        (park_id, year, total_groups, groups_stopped_inside, groups_transited, avg_days_burning, analyzed_at, trajectories_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (park_id, year, len(infraction_trajs), stopped, transited, round(avg_days, 2), 
         datetime.now().isoformat(), json.dumps(traj_summary)))
    
    return {'inside_fires': len(inside_fires), 'groups': len(infraction_trajs), 'stopped': stopped, 'transited': transited}


def process_uploaded_zip(zip_path: Path, conn) -> Dict:
    """Process an uploaded ZIP file of VIIRS data"""
    logger.info(f"Processing uploaded ZIP: {zip_path}")
    
    keystones = load_keystones()
    results = {'parks_processed': 0, 'fires_added': 0, 'groups_found': 0}
    
    # Determine years in the ZIP
    years_found = set()
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for name in zf.namelist():
            for y in YEARS:
                if str(y) in name:
                    years_found.add(y)
    
    logger.info(f"Years found in ZIP: {sorted(years_found)}")
    
    for park in keystones:
        for year in years_found:
            try:
                # Create temp directory structure for streaming
                result = process_park_year_from_zip(park, year, conn, zip_path)
                if result:
                    results['parks_processed'] += 1
                    results['fires_added'] += result.get('inside_fires', 0)
                    results['groups_found'] += result.get('groups', 0)
                conn.commit()
            except Exception as e:
                logger.warning(f"Error processing {park['id']}/{year}: {e}")
    
    return results


def process_park_year_from_zip(park: Dict, year: int, conn, zip_path: Path) -> Optional[Dict]:
    """Process a park/year directly from a ZIP file"""
    park_id = park['id']
    
    try:
        boundary = shape(park['geometry'])
        boundary_prep = prep(boundary)
    except:
        return None
    
    bbox = get_park_bbox(park)
    min_lon, min_lat, max_lon, max_lat = bbox
    
    # Stream fires from ZIP, filtering by bbox
    fires = []
    for fire in stream_fires_from_zip(zip_path, year):
        lat, lon = fire['latitude'], fire['longitude']
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            fires.append(fire)
    
    if len(fires) < 50:
        return {'inside_fires': 0, 'groups': 0}
    
    # Same processing as regular method...
    inside_fires = []
    for f in fires:
        if boundary_prep.contains(Point(f['longitude'], f['latitude'])):
            inside_fires.append((
                f['latitude'], f['longitude'], f['bright_ti4'], f['scan'],
                f['track'], f['acq_date'], f['acq_time'], f['satellite'],
                f['instrument'], f['confidence'], f['version'],
                f['bright_ti5'], f['frp'], f['daynight'], 1, park_id
            ))
    
    if inside_fires:
        conn.executemany("""INSERT OR IGNORE INTO fire_detections 
            (latitude, longitude, brightness, scan, track, acq_date, acq_time, satellite,
             instrument, confidence, version, bright_t31, frp, daynight, in_protected_area, protected_area_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", inside_fires)
    
    daily_clusters = detect_fire_groups(fires)
    if len(daily_clusters) < 5:
        return {'inside_fires': len(inside_fires), 'groups': 0}
    
    trajectories = track_fire_groups(daily_clusters)
    infraction_trajs = [r for t in trajectories if (r := analyze_trajectory(t, boundary_prep))]
    
    if not infraction_trajs:
        return {'inside_fires': len(inside_fires), 'groups': 0}
    
    stopped = len([t for t in infraction_trajs if t['outcome'] == 'STOPPED_INSIDE'])
    transited = len([t for t in infraction_trajs if t['outcome'] == 'TRANSITED'])
    avg_days = np.mean([t['days_burning_inside'] for t in infraction_trajs])
    
    traj_summary = [{
        'origin': {'lat': t['origin_lat'], 'lon': t['origin_lon'], 'date': t['origin_date']},
        'dest': {'lat': t['dest_lat'], 'lon': t['dest_lon'], 'date': t['dest_date']},
        'entry_date': t['entry_date'],
        'last_inside': t['last_inside_date'],
        'days_inside': t['days_burning_inside'],
        'fires_inside': t['fires_inside'],
        'outcome': t['outcome'],
        'path': t['centroids'][:20]
    } for t in infraction_trajs]
    
    conn.execute("""INSERT OR REPLACE INTO park_group_infractions 
        (park_id, year, total_groups, groups_stopped_inside, groups_transited, avg_days_burning, analyzed_at, trajectories_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (park_id, year, len(infraction_trajs), stopped, transited, round(avg_days, 2),
         datetime.now().isoformat(), json.dumps(traj_summary)))
    
    return {'inside_fires': len(inside_fires), 'groups': len(infraction_trajs)}


def main():
    parser = argparse.ArgumentParser(description='Process VIIRS fire data')
    parser.add_argument('--zip', type=Path, help='Process uploaded ZIP file')
    parser.add_argument('--park', type=str, help='Process specific park only')
    parser.add_argument('--year', type=int, help='Process specific year only')
    args = parser.parse_args()
    
    conn = get_db()
    
    if args.zip:
        # Process uploaded ZIP
        results = process_uploaded_zip(args.zip, conn)
        logger.info(f"Processed: {results}")
        # Delete ZIP after processing to save space
        args.zip.unlink()
        logger.info(f"Deleted processed ZIP: {args.zip}")
    else:
        # Normal batch processing
        keystones = load_keystones()
        years = [args.year] if args.year else YEARS
        parks = [p for p in keystones if p['id'] == args.park] if args.park else keystones
        
        for park in parks:
            for year in years:
                logger.info(f"Processing {park['id']} {year}...")
                result = process_park_year(park, year, conn)
                conn.commit()
                if result:
                    logger.info(f"  fires={result.get('inside_fires', 0)} groups={result.get('groups', 0)}")
                time.sleep(1)
    
    conn.close()


if __name__ == '__main__':
    main()
