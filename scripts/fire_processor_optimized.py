#!/usr/bin/env python3
"""Optimized fire processor - stores only inside-park fires + group centroids."""

import json
import sqlite3
import pandas as pd
import numpy as np
import time
from pathlib import Path
from glob import glob
from datetime import datetime
from sklearn.cluster import DBSCAN

try:
    from shapely.geometry import shape, Point
    from shapely.prepared import prep
except ImportError:
    print("ERROR: shapely required"); exit(1)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
FIRE_DIR = DATA_DIR / "fire" / "viirs-jpss"
DB_PATH = BASE_DIR / "db.sqlite3"
PROGRESS_FILE = BASE_DIR / "logs" / "fire_progress.json"
BUFFER_KM = 300
YEARS = [2018, 2019, 2020, 2021, 2022, 2023, 2024]

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def distance_km(lat1, lon1, lat2, lon2):
    lat_diff = abs(lat2 - lat1) * 111
    lon_diff = abs(lon2 - lon1) * 111 * np.cos(np.radians((lat1 + lat2) / 2))
    return np.sqrt(lat_diff**2 + lon_diff**2)

def load_keystones():
    with open(DATA_DIR / "keystones_with_boundaries.json") as f:
        return json.load(f)

def get_park_boundary(park):
    geom = park.get('geometry')
    if not geom: return None, None
    try:
        shp = shape(geom)
        return shp, prep(shp)
    except: return None, None

def get_park_bbox(park, buffer_km=BUFFER_KM):
    lat, lon = park['coordinates']['lat'], park['coordinates']['lon']
    buf = buffer_km / 111.0
    return {'lat_min': lat - buf, 'lat_max': lat + buf, 'lon_min': lon - buf, 'lon_max': lon + buf}

def load_fires_for_region(bbox, year):
    all_fires = []
    for fpath in glob(str(FIRE_DIR / str(year) / "*.csv")):
        try:
            df = pd.read_csv(fpath)
            f = df[(df['latitude'] >= bbox['lat_min']) & (df['latitude'] <= bbox['lat_max']) &
                   (df['longitude'] >= bbox['lon_min']) & (df['longitude'] <= bbox['lon_max'])]
            if len(f) > 0: all_fires.append(f)
        except: continue
    if not all_fires: return None
    combined = pd.concat(all_fires, ignore_index=True)
    combined['date'] = pd.to_datetime(combined['acq_date'])
    return combined[combined['date'].dt.month.isin([1,2,3,11,12])]

def detect_daily_clusters(fires_df, eps_km=15, min_fires=8):
    daily_clusters = {}
    eps_deg = eps_km / 111.0
    for date in sorted(fires_df['acq_date'].unique()):
        day_fires = fires_df[fires_df['acq_date'] == date]
        if len(day_fires) < min_fires: continue
        coords = day_fires[['latitude', 'longitude']].values
        clustering = DBSCAN(eps=eps_deg, min_samples=min_fires//2).fit(coords)
        clusters = []
        for cid in set(clustering.labels_):
            if cid == -1: continue
            cf = day_fires[clustering.labels_ == cid]
            clusters.append({'date': date, 'cid': cid, 'lat': float(cf['latitude'].mean()),
                           'lon': float(cf['longitude'].mean()), 'fires': int(len(cf)),
                           'frp': float(cf['frp'].sum())})
        if clusters: daily_clusters[date] = clusters
    return daily_clusters

def track_clusters(daily_clusters, max_link_km=25, max_gap_days=3):
    trajectories, used = [], set()
    sorted_dates = sorted(daily_clusters.keys())
    for start_idx, start_date in enumerate(sorted_dates):
        for cluster in daily_clusters[start_date]:
            key = (start_date, cluster['cid'])
            if key in used: continue
            traj = [cluster]; used.add(key); current = cluster
            for next_idx in range(start_idx + 1, len(sorted_dates)):
                next_date = sorted_dates[next_idx]
                date_gap = (pd.to_datetime(next_date) - pd.to_datetime(current['date'])).days
                if date_gap > max_gap_days: break
                best, best_score = None, float('inf')
                for nc in daily_clusters[next_date]:
                    nkey = (next_date, nc['cid'])
                    if nkey in used: continue
                    dist = distance_km(current['lat'], current['lon'], nc['lat'], nc['lon'])
                    if dist > max_link_km: continue
                    score = dist - (current['lat'] - nc['lat']) * 5
                    if score < best_score: best, best_score = nc, score
                if best:
                    traj.append(best); used.add((next_date, best['cid'])); current = best
            if len(traj) >= 5: trajectories.append(traj)
    return trajectories

def analyze_trajectory(traj, boundary_prep):
    if not traj or len(traj) < 3: return None
    inside_pts = [c for c in traj if boundary_prep.contains(Point(c['lon'], c['lat']))]
    if not inside_pts: return None
    first_in = next(i for i, c in enumerate(traj) if boundary_prep.contains(Point(c['lon'], c['lat'])))
    last_in = len(traj) - 1 - next(i for i, c in enumerate(reversed(traj)) if boundary_prep.contains(Point(c['lon'], c['lat'])))
    after_exit = [c for c in traj[last_in+1:] if not boundary_prep.contains(Point(c['lon'], c['lat']))]
    outcome = 'STOPPED_INSIDE' if not after_exit else 'TRANSITED'
    return {
        'trajectory_days': len(traj), 'total_fires': sum(c['fires'] for c in traj),
        'origin_lat': round(traj[0]['lat'], 4), 'origin_lon': round(traj[0]['lon'], 4),
        'origin_date': traj[0]['date'], 'entry_date': inside_pts[0]['date'],
        'days_burning_inside': len(inside_pts), 'fires_inside': sum(c['fires'] for c in inside_pts),
        'last_inside_date': inside_pts[-1]['date'], 'outcome': outcome,
        'dest_lat': round(traj[-1]['lat'], 4), 'dest_lon': round(traj[-1]['lon'], 4),
        'dest_date': traj[-1]['date'],
        'centroids': [{'date': c['date'], 'lat': round(c['lat'],4), 'lon': round(c['lon'],4), 'fires': c['fires']} for c in traj]
    }

def save_inside_fires(conn, park_id, fires_df, boundary_prep):
    inside = []
    for _, row in fires_df.iterrows():
        if boundary_prep.contains(Point(row['longitude'], row['latitude'])):
            inside.append((row['latitude'], row['longitude'], row.get('bright_ti4'), row.get('scan'),
                          row.get('track'), row['acq_date'], row.get('acq_time'), row.get('satellite'),
                          row.get('instrument'), row.get('confidence'), row.get('version'),
                          row.get('bright_ti5'), row.get('frp'), row.get('daynight'), 1, park_id))
    if inside:
        conn.executemany("""INSERT OR IGNORE INTO fire_detections 
            (latitude, longitude, brightness, scan, track, acq_date, acq_time, satellite,
             instrument, confidence, version, bright_t31, frp, daynight, in_protected_area, protected_area_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", inside)
    return len(inside)

def process_park_year(park, year, conn):
    park_id = park['id']
    boundary, boundary_prep = get_park_boundary(park)
    if boundary is None: return None
    bbox = get_park_bbox(park)
    fires = load_fires_for_region(bbox, year)
    if fires is None or len(fires) < 50: return None
    
    # Save inside fires
    inside_count = save_inside_fires(conn, park_id, fires, boundary_prep)
    
    # Detect and track groups
    daily_clusters = detect_daily_clusters(fires)
    if len(daily_clusters) < 5: return {'inside_fires': inside_count, 'groups': 0}
    trajectories = track_clusters(daily_clusters)
    
    infraction_trajs = []
    for traj in trajectories:
        result = analyze_trajectory(traj, boundary_prep)
        if result and 0.5 <= (result['trajectory_days'] / max(1, len(result['centroids']))) <= 30:
            infraction_trajs.append(result)
    
    if not infraction_trajs: return {'inside_fires': inside_count, 'groups': 0}
    
    stopped = len([t for t in infraction_trajs if t['outcome'] == 'STOPPED_INSIDE'])
    transited = len([t for t in infraction_trajs if t['outcome'] == 'TRANSITED'])
    avg_days = np.mean([t['days_burning_inside'] for t in infraction_trajs])
    
    conn.execute("""INSERT OR REPLACE INTO park_group_infractions 
        (park_id, year, total_groups, groups_stopped_inside, groups_transited, avg_days_burning, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (park_id, year, len(infraction_trajs), stopped, transited, round(avg_days, 2), datetime.now().isoformat()))
    
    return {'inside_fires': inside_count, 'groups': len(infraction_trajs), 'stopped': stopped, 'transited': transited}

def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f: return json.load(f)
    return {'completed': []}

def save_progress(progress):
    with open(PROGRESS_FILE, 'w') as f: json.dump(progress, f)

def main():
    print(f"Starting optimized fire processor at {datetime.now()}")
    keystones = load_keystones()
    progress = load_progress()
    completed = set(progress['completed'])
    
    conn = get_db()
    total_parks = len(keystones)
    total_inside = 0
    
    for pi, park in enumerate(keystones):
        for year in YEARS:
            key = f"{park['id']}_{year}"
            if key in completed:
                print(f"[{pi+1}/{total_parks}] {key} - skipped (already done)")
                continue
            
            print(f"[{pi+1}/{total_parks}] {key}...", end=" ", flush=True)
            try:
                result = process_park_year(park, year, conn)
                conn.commit()
                if result:
                    total_inside += result.get('inside_fires', 0)
                    print(f"fires={result.get('inside_fires',0)} groups={result.get('groups',0)}")
                else:
                    print("no data")
                completed.add(key)
                progress['completed'] = list(completed)
                save_progress(progress)
            except Exception as e:
                print(f"error: {e}")
            
            time.sleep(2)  # Slow down to avoid overload
    
    conn.close()
    print(f"\nDone! Total inside-park fires stored: {total_inside}")

if __name__ == '__main__':
    main()
