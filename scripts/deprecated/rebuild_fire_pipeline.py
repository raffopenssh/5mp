#!/usr/bin/env python3
"""
Unified Fire Pipeline - Consolidate data, build daily-centroid trajectories, generate narratives.

Uses daily cluster centroids to avoid zigzag trajectories.
Memory-efficient windowed processing with deduplication.

Steps:
1. Load all fire sources (DB, NRT JSON, buffer JSON)
2. Deduplicate by (lat, lon, date, time)
3. Build daily clusters per park
4. Link clusters into trajectories
5. Add context (rivers, roads, places)
6. Generate narratives
7. Export to feature_geometries and fire_narrative_cache
"""

import json
import sqlite3
import os
import sys
import glob
import gzip
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from math import radians, sin, cos, sqrt, atan2, degrees
import argparse

# Configuration
DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'
KEYSTONES_FILE = DATA_DIR / 'keystones_with_boundaries.json'

MIN_DATE = '2020-01-01'
BUFFER_KM = 30  # km buffer for fire detection
CLUSTER_DIST_KM = 5  # km for daily clustering
MAX_LINK_KM = 25  # km max link between days
MAX_GAP_DAYS = 3  # max days gap for trajectory continuity
MIN_TRAJ_DAYS = 2  # minimum days for a valid trajectory

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def haversine(lat1, lon1, lat2, lon2):
    """Distance in km between two points."""
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def bearing(lat1, lon1, lat2, lon2):
    """Bearing from point 1 to point 2."""
    dlon = radians(lon2 - lon1)
    x = sin(dlon) * cos(radians(lat2))
    y = cos(radians(lat1)) * sin(radians(lat2)) - sin(radians(lat1)) * cos(radians(lat2)) * cos(dlon)
    return (degrees(atan2(x, y)) + 360) % 360

def bearing_to_direction(b):
    """Convert bearing to cardinal direction."""
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 
            'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    return dirs[int((b + 11.25) / 22.5) % 16]

class FirePipeline:
    def __init__(self, min_date=MIN_DATE, buffer_km=BUFFER_KM):
        self.min_date = min_date
        self.buffer_km = buffer_km
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        self.parks = {}
        self.park_shapes = {}
        self.climate = {}
        self.rivers = defaultdict(list)
        self.places = defaultdict(list)
        
    def load_parks(self):
        """Load park boundaries."""
        log("Loading parks...")
        with open(KEYSTONES_FILE) as f:
            keystones = json.load(f)
        
        for k in keystones:
            park_id = k['id']
            self.parks[park_id] = {
                'name': k.get('name', park_id),
                'country': k.get('country', ''),
                'area_km2': k.get('area_km2', 10000),
                'centroid': k.get('centroid', [0, 0])
            }
            if k.get('geometry'):
                self.park_shapes[park_id] = k['geometry']
        
        log(f"  Loaded {len(self.parks)} parks")
    
    def load_context(self):
        """Load rivers, places, climate for narratives."""
        log("Loading context data...")
        
        # Climate
        climate_file = DATA_DIR / 'climate' / 'park_climate.json'
        if climate_file.exists():
            with open(climate_file) as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self.climate = data  # Already keyed by park_id
                else:
                    self.climate = {c['park_id']: c for c in data}
        
        # Try CSV if JSON not available
        if not self.climate:
            try:
                cur = self.conn.execute('SELECT * FROM park_climate')
                for row in cur:
                    self.climate[row['park_id']] = dict(row)
            except: pass
        log(f"  Climate: {len(self.climate)} parks")
        
        # Rivers
        try:
            cur = self.conn.execute('''
                SELECT park_id, name, stream_order, lat, lon 
                FROM park_rivers_hydro 
                WHERE name IS NOT NULL AND name != ""
            ''')
            for row in cur:
                self.rivers[row['park_id']].append({
                    'name': row['name'], 
                    'order': row['stream_order'],
                    'lat': row['lat'],
                    'lon': row['lon']
                })
        except: pass
        log(f"  Rivers: {len(self.rivers)} parks")
        
        # Places
        try:
            cur = self.conn.execute('''
                SELECT park_id, name, place_type, lat, lon 
                FROM osm_places 
                WHERE name IS NOT NULL
            ''')
            for row in cur:
                self.places[row['park_id']].append({
                    'name': row['name'], 
                    'type': row['place_type'],
                    'lat': row['lat'],
                    'lon': row['lon']
                })
        except: pass
        log(f"  Places: {len(self.places)} parks")
    
    def load_fires_for_park(self, park_id):
        """Load all fire detections for a park from all sources."""
        fires = []
        seen = set()  # For deduplication
        
        park_info = self.parks.get(park_id, {})
        centroid = park_info.get('centroid', [0, 0])
        
        def add_fire(lat, lon, date, time_str='0000', brightness=0, frp=0, source=''):
            if date < self.min_date:
                return
            key = f"{lat:.4f}_{lon:.4f}_{date}_{time_str}"
            if key in seen:
                return
            seen.add(key)
            fires.append({
                'lat': lat, 'lon': lon, 'date': date, 
                'time': time_str, 'brightness': brightness, 
                'frp': frp, 'source': source
            })
        
        # Source 1: fire_detections table
        try:
            cur = self.conn.execute('''
                SELECT latitude, longitude, acq_date, acq_time, bright_ti4, frp
                FROM fire_detections 
                WHERE park_id = ? AND acq_date >= ?
            ''', (park_id, self.min_date))
            for row in cur:
                add_fire(row['latitude'], row['longitude'], row['acq_date'],
                        str(row['acq_time'] or '0000'), row['bright_ti4'] or 0, 
                        row['frp'] or 0, 'db')
        except: pass
        
        # Source 2: NRT JSON files (2025-2026)
        nrt_file = DATA_DIR / 'fire_detections_2025_2026' / f'{park_id}.json'
        if nrt_file.exists():
            try:
                with open(nrt_file) as f:
                    for fire in json.load(f):
                        add_fire(fire['lat'], fire['lng'], fire['date'],
                                fire.get('time', '0000'), fire.get('brightness', 0),
                                fire.get('frp', 0), 'nrt')
            except: pass
        
        # Source 3: Buffer JSON files (all years)
        for buffer_file in glob.glob(str(DATA_DIR / 'fire_additional_buffer' / f'{park_id}_*_buffer.json')):
            try:
                with open(buffer_file) as f:
                    for fire in json.load(f):
                        add_fire(fire['lat'], fire['lng'], fire['date'],
                                fire.get('time', '0000'), fire.get('brightness', 0),
                                fire.get('frp', 0), 'buffer')
            except: pass
        
        return sorted(fires, key=lambda f: (f['date'], f['time']))
    
    def cluster_fires_by_day(self, fires):
        """Group fires by day and cluster spatially."""
        by_date = defaultdict(list)
        for f in fires:
            by_date[f['date']].append(f)
        
        daily_clusters = {}
        for date, day_fires in by_date.items():
            clusters = []
            used = [False] * len(day_fires)
            
            for i, fire in enumerate(day_fires):
                if used[i]:
                    continue
                
                # Start new cluster
                cluster = [fire]
                used[i] = True
                
                # Find nearby fires
                for j, other in enumerate(day_fires):
                    if used[j]:
                        continue
                    dist = haversine(fire['lat'], fire['lon'], other['lat'], other['lon'])
                    if dist <= CLUSTER_DIST_KM:
                        cluster.append(other)
                        used[j] = True
                
                # Compute centroid
                lats = [f['lat'] for f in cluster]
                lons = [f['lon'] for f in cluster]
                clusters.append({
                    'date': date,
                    'lat': sum(lats) / len(lats),
                    'lon': sum(lons) / len(lons),
                    'fires': len(cluster),
                    'brightness': max(f['brightness'] for f in cluster) if cluster else 0,
                    'frp': sum(f['frp'] for f in cluster)
                })
            
            if clusters:
                daily_clusters[date] = clusters
        
        return daily_clusters
    
    def link_trajectories(self, daily_clusters):
        """Link daily clusters into trajectories."""
        sorted_dates = sorted(daily_clusters.keys())
        if not sorted_dates:
            return []
        
        used = set()
        trajectories = []
        
        for start_idx, start_date in enumerate(sorted_dates):
            for cluster in daily_clusters[start_date]:
                key = f"{start_date}_{cluster['lat']:.4f}_{cluster['lon']:.4f}"
                if key in used:
                    continue
                
                traj = [cluster]
                used.add(key)
                current = cluster
                
                for next_idx in range(start_idx + 1, len(sorted_dates)):
                    next_date = sorted_dates[next_idx]
                    
                    # Check date gap
                    d1 = datetime.strptime(current['date'], '%Y-%m-%d')
                    d2 = datetime.strptime(next_date, '%Y-%m-%d')
                    gap_days = (d2 - d1).days
                    
                    if gap_days > MAX_GAP_DAYS:
                        break
                    
                    # Find closest cluster
                    best = None
                    best_dist = MAX_LINK_KM + 1
                    
                    for nc in daily_clusters[next_date]:
                        nkey = f"{next_date}_{nc['lat']:.4f}_{nc['lon']:.4f}"
                        if nkey in used:
                            continue
                        
                        dist = haversine(current['lat'], current['lon'], nc['lat'], nc['lon'])
                        if dist <= MAX_LINK_KM and dist < best_dist:
                            best = nc
                            best_dist = dist
                    
                    if best:
                        traj.append(best)
                        used.add(f"{best['date']}_{best['lat']:.4f}_{best['lon']:.4f}")
                        current = best
                
                if len(traj) >= MIN_TRAJ_DAYS:
                    trajectories.append(traj)
        
        return trajectories
    
    def classify_trajectory(self, traj):
        """Classify trajectory based on movement patterns."""
        start, end = traj[0], traj[-1]
        
        total_fires = sum(c['fires'] for c in traj)
        days = len(traj)
        net_south = (start['lat'] - end['lat']) * 111
        net_east = (end['lon'] - start['lon']) * 111
        total_dist = haversine(start['lat'], start['lon'], end['lat'], end['lon'])
        
        # Speed calculations
        movements = []
        for i in range(1, len(traj)):
            d = haversine(traj[i-1]['lat'], traj[i-1]['lon'], traj[i]['lat'], traj[i]['lon'])
            t = (datetime.strptime(traj[i]['date'], '%Y-%m-%d') - 
                 datetime.strptime(traj[i-1]['date'], '%Y-%m-%d')).days
            if t > 0:
                movements.append(d / t)
        
        avg_speed = sum(movements) / len(movements) if movements else 0
        max_speed = max(movements) if movements else 0
        
        # Spread
        avg_spread = sum(c['fires'] for c in traj) / days if days > 0 else 0
        
        # Direction
        if total_dist > 0:
            b = bearing(start['lat'], start['lon'], end['lat'], end['lon'])
            direction = bearing_to_direction(b)
        else:
            b = 0
            direction = 'N'
        
        # Classify
        if avg_spread > 50 and avg_speed > 10:
            group_type = 'management_fast'
        elif avg_speed > 15:
            group_type = 'management_vehicle' if avg_spread > 30 else 'herder_fast'
        elif avg_speed > 5:
            group_type = 'transhumance' if net_south > 20 else 'herder_local'
        elif avg_speed > 2:
            group_type = 'transhumance_slow' if days > 10 and net_south > 15 else 'local_burning'
        else:
            group_type = 'village_persistent' if days > 7 else 'local_stationary'
        
        # Build trajectory points (daily centroids)
        trajectory_points = [
            {'date': pt['date'], 'lat': round(pt['lat'], 5), 'lon': round(pt['lon'], 5), 'fires': pt['fires']}
            for pt in traj
        ]
        
        return {
            'group_type': group_type,
            'days': days,
            'fires': total_fires,
            'net_south_km': round(net_south, 1),
            'net_east_km': round(net_east, 1),
            'total_distance_km': round(total_dist, 1),
            'avg_speed_km_day': round(avg_speed, 1),
            'max_speed_km_day': round(max_speed, 1),
            'avg_spread': round(avg_spread, 1),
            'direction': direction,
            'bearing': round(b, 0),
            'start_date': start['date'],
            'end_date': end['date'],
            'start_lat': round(start['lat'], 5),
            'start_lon': round(start['lon'], 5),
            'end_lat': round(end['lat'], 5),
            'end_lon': round(end['lon'], 5),
            'trajectory': trajectory_points
        }
    
    def find_nearest_place(self, lat, lon, park_id, max_dist_km=20):
        """Find nearest place to a point."""
        places = self.places.get(park_id, [])
        best = None
        best_dist = max_dist_km + 1
        
        for p in places:
            dist = haversine(lat, lon, p['lat'], p['lon'])
            if dist < best_dist:
                best = p
                best_dist = dist
        
        if best and best_dist <= max_dist_km:
            return {'name': best['name'], 'type': best['type'], 'distance_km': round(best_dist, 1)}
        return None
    
    def find_nearest_river(self, lat, lon, park_id, max_dist_km=15):
        """Find nearest river to a point."""
        rivers = self.rivers.get(park_id, [])
        best = None
        best_dist = max_dist_km + 1
        
        for r in rivers:
            dist = haversine(lat, lon, r['lat'], r['lon'])
            if dist < best_dist:
                best = r
                best_dist = dist
        
        if best and best_dist <= max_dist_km:
            return {'name': best['name'], 'order': best['order'], 'distance_km': round(best_dist, 1)}
        return None
    
    def get_season(self, date_str, park_id):
        """Get season for a date based on park climate."""
        climate = self.climate.get(park_id, {})
        rainy = climate.get('rainy_season', '')
        dry = climate.get('dry_season', '')
        
        if not rainy or rainy == 'None':
            return 'unknown'
        
        try:
            month = datetime.strptime(date_str, '%Y-%m-%d').month
        except:
            return 'unknown'
        
        # Parse rainy season (e.g., "Nov-Mar" or "May-Sep")
        try:
            parts = rainy.split('-')
            if len(parts) == 2:
                months = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                         'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
                start_m = months.get(parts[0], 1)
                end_m = months.get(parts[1], 12)
                
                if start_m <= end_m:
                    if start_m <= month <= end_m:
                        return 'wet'
                else:  # Wraps around year (e.g., Nov-Mar)
                    if month >= start_m or month <= end_m:
                        return 'wet'
                return 'dry'
        except:
            pass
        
        return 'unknown'
    
    def build_narrative(self, traj_data, park_id):
        """Build narrative text for a trajectory."""
        parts = []
        
        park_name = self.parks.get(park_id, {}).get('name', park_id)
        
        # Origin
        origin_place = self.find_nearest_place(traj_data['start_lat'], traj_data['start_lon'], park_id)
        origin_river = self.find_nearest_river(traj_data['start_lat'], traj_data['start_lon'], park_id)
        
        if origin_place:
            parts.append(f"Fire group originated {origin_place['distance_km']}km from {origin_place['name']} ({origin_place['type']})")
        elif origin_river:
            parts.append(f"Fire group originated {origin_river['distance_km']}km from {origin_river['name']} river")
        else:
            parts.append(f"Fire group originated at ({traj_data['start_lat']:.2f}°, {traj_data['start_lon']:.2f}°)")
        
        # Direction and date
        parts.append(f"moving {traj_data['direction']} (bearing {traj_data['bearing']:.0f}°)")
        parts.append(f"on {traj_data['start_date']}")
        
        season = self.get_season(traj_data['start_date'], park_id)
        if season != 'unknown':
            parts.append(f"({season} season)")
        
        # Duration and fires
        parts.append(f"Burned for {traj_data['days']} days ({traj_data['fires']} fire detections).")
        
        # Destination
        dest_place = self.find_nearest_place(traj_data['end_lat'], traj_data['end_lon'], park_id)
        dest_river = self.find_nearest_river(traj_data['end_lat'], traj_data['end_lon'], park_id)
        
        if dest_place:
            parts.append(f"Ended {dest_place['distance_km']}km from {dest_place['name']}.")
        elif dest_river:
            parts.append(f"Ended near {dest_river['name']} river.")
        
        return ' '.join(parts)
    
    def process_park(self, park_id):
        """Process a single park - load fires, build trajectories, generate narratives."""
        fires = self.load_fires_for_park(park_id)
        if not fires:
            return None
        
        daily_clusters = self.cluster_fires_by_day(fires)
        trajectories = self.link_trajectories(daily_clusters)
        
        if not trajectories:
            return None
        
        results = []
        for i, traj in enumerate(trajectories):
            traj_data = self.classify_trajectory(traj)
            traj_data['feature_id'] = f"{park_id}_grp_{i}"
            traj_data['park_id'] = park_id
            traj_data['narrative'] = self.build_narrative(traj_data, park_id)
            traj_data['season'] = self.get_season(traj_data['start_date'], park_id)
            
            # Add context
            traj_data['origin_place'] = self.find_nearest_place(traj_data['start_lat'], traj_data['start_lon'], park_id)
            traj_data['origin_river'] = self.find_nearest_river(traj_data['start_lat'], traj_data['start_lon'], park_id)
            
            results.append(traj_data)
        
        return {
            'park_id': park_id,
            'park_name': self.parks.get(park_id, {}).get('name', park_id),
            'total_fires': len(fires),
            'total_groups': len(results),
            'groups': results
        }
    
    def save_to_db(self, park_data):
        """Save trajectories to feature_geometries and fire_narrative_cache."""
        if not park_data:
            return
        
        park_id = park_data['park_id']
        groups = park_data['groups']
        
        # Delete existing fire_trajectory for this park
        self.conn.execute('''
            DELETE FROM feature_geometries 
            WHERE park_id = ? AND feature_type = 'fire_trajectory'
        ''', (park_id,))
        
        # Insert new trajectories
        for g in groups:
            # Build GeoJSON LineString from daily centroids
            coords = [[pt['lon'], pt['lat']] for pt in g['trajectory']]
            if len(coords) < 2:
                continue
            
            geojson = json.dumps({
                'type': 'LineString',
                'coordinates': coords
            })
            
            properties = {
                'fires': g['fires'],
                'days': g['days'],
                'distance_km': g['total_distance_km'],
                'speed_km_day': g['avg_speed_km_day'],
                'direction': g['direction'],
                'group_type': g['group_type'],
                'season': g.get('season'),
                'narrative': g.get('narrative', '')
            }
            
            self.conn.execute('''
                INSERT INTO feature_geometries 
                (feature_type, feature_id, park_id, geojson, start_date, end_date, properties_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', ('fire_trajectory', g['feature_id'], park_id, geojson,
                  g['start_date'], g['end_date'], json.dumps(properties)))
        
        # Update fire_narrative_cache
        narratives = [{
            'group_num': i + 1,
            'feature_id': g['feature_id'],
            'start_date': g['start_date'],
            'end_date': g['end_date'],
            'days': g['days'],
            'fires_total': g['fires'],
            'distance_km': g['total_distance_km'],
            'avg_speed_km_day': g['avg_speed_km_day'],
            'direction': g['direction'],
            'group_type': g['group_type'],
            'season': g.get('season'),
            'narrative': g.get('narrative', ''),
            'origin': {
                'nearest_place': g.get('origin_place'),
                'nearest_river': g.get('origin_river')
            }
        } for i, g in enumerate(groups)]
        
        # Build summary
        by_type = defaultdict(int)
        for g in groups:
            by_type[g['group_type']] += 1
        
        cache_data = {
            'park_id': park_id,
            'park_name': park_data['park_name'],
            'total_fires': park_data['total_fires'],
            'total_groups': park_data['total_groups'],
            'by_type': dict(by_type),
            'narratives': narratives
        }
        
        self.conn.execute('''
            INSERT OR REPLACE INTO fire_narrative_cache 
            (park_id, narrative_json, computed_at)
            VALUES (?, ?, ?)
        ''', (park_id, json.dumps(cache_data), datetime.now().isoformat()))
        
        self.conn.commit()
    
    def run(self, parks=None, save_json=True):
        """Run the full pipeline."""
        log("=" * 70)
        log(f"FIRE PIPELINE - Min date: {self.min_date}, Buffer: {self.buffer_km}km")
        log("=" * 70)
        
        self.load_parks()
        self.load_context()
        
        if parks:
            park_ids = parks
        else:
            park_ids = sorted(self.parks.keys())
        
        log(f"\nProcessing {len(park_ids)} parks...")
        
        total_groups = 0
        total_fires = 0
        
        for i, park_id in enumerate(park_ids):
            result = self.process_park(park_id)
            
            if result:
                total_groups += result['total_groups']
                total_fires += result['total_fires']
                
                # Save to DB
                self.save_to_db(result)
                
                # Save JSON
                if save_json:
                    out_dir = DATA_DIR / 'fire_analysis_v3'
                    out_dir.mkdir(exist_ok=True)
                    with open(out_dir / f'{park_id}.json', 'w') as f:
                        json.dump(result, f)
                
                log(f"  [{i+1}/{len(park_ids)}] {park_id}: {result['total_groups']} groups, {result['total_fires']} fires")
            else:
                log(f"  [{i+1}/{len(park_ids)}] {park_id}: No data")
        
        log(f"\n{'=' * 70}")
        log(f"COMPLETE: {total_groups} groups, {total_fires} fires across {len(park_ids)} parks")
        log(f"{'=' * 70}")

def main():
    parser = argparse.ArgumentParser(description='Rebuild fire pipeline with daily centroids')
    parser.add_argument('--from-date', default=MIN_DATE, help=f'Start date (default: {MIN_DATE})')
    parser.add_argument('--park', help='Process single park')
    parser.add_argument('--no-json', action='store_true', help='Skip JSON export')
    args = parser.parse_args()
    
    pipeline = FirePipeline(min_date=args.from_date)
    
    if args.park:
        pipeline.run(parks=[args.park], save_json=not args.no_json)
    else:
        pipeline.run(save_json=not args.no_json)

if __name__ == '__main__':
    main()
