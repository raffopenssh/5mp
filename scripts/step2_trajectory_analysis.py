#!/usr/bin/env python3
"""
STEP 2: Fire Trajectory Analysis - Add context and generate narratives

Reads Step 1 output (data/fire_analysis/*.json)
Adds context from DB: rivers, roads, places, settlements
Generates narratives for each trajectory
Outputs to data/fire_trajectories/*.json (API reads from here)

Memory efficient: processes one park at a time, uses generators
"""

import json
import argparse
import sqlite3
import math
import gc
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'
INPUT_DIR = DATA_DIR / 'fire_analysis'
OUTPUT_DIR = DATA_DIR / 'fire_trajectories'

MIN_DATE = '2020-01-01'

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def bearing_to_dir(b):
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 
            'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    return dirs[int((b + 11.25) / 22.5) % 16]

class Step2Analyzer:
    def __init__(self, conn):
        self.conn = conn
        
    def load_park_context(self, park_id):
        """Load context data for a single park"""
        ctx = {'rivers': [], 'roads': [], 'places': [], 'settlements': []}
        
        # Rivers (top 50 by stream order)
        for row in self.conn.execute('''
            SELECT name, stream_order, lat, lon FROM park_rivers_hydro
            WHERE park_id = ? ORDER BY stream_order DESC LIMIT 50
        ''', (park_id,)):
            if row[2] and row[3]:
                ctx['rivers'].append({
                    'name': row[0] or 'Unnamed',
                    'order': row[1],
                    'lat': row[2], 'lon': row[3]
                })
        
        # Roads (extract first coord from geojson)
        for row in self.conn.execute('''
            SELECT highway_type, surface, 
                   json_extract(geojson, '$.coordinates[0][0]') as lon,
                   json_extract(geojson, '$.coordinates[0][1]') as lat
            FROM roads_heigit WHERE park_id = ? AND geojson IS NOT NULL LIMIT 100
        ''', (park_id,)):
            if row[2] and row[3]:
                try:
                    ctx['roads'].append({
                        'type': row[0], 'surface': row[1],
                        'lat': float(row[3]), 'lon': float(row[2])
                    })
                except (ValueError, TypeError):
                    pass
        
        # Places
        for row in self.conn.execute('''
            SELECT name, place_type, lat, lon FROM osm_places
            WHERE park_id = ? LIMIT 200
        ''', (park_id,)):
            ctx['places'].append({
                'name': row[0], 'type': row[1],
                'lat': row[2], 'lon': row[3]
            })
        
        # Settlements
        for row in self.conn.execute('''
            SELECT nearest_place, classification, lat, lon, population_est
            FROM park_settlements WHERE park_id = ? LIMIT 100
        ''', (park_id,)):
            ctx['settlements'].append({
                'name': row[0], 'type': row[1],
                'lat': row[2], 'lon': row[3], 'pop': row[4]
            })
        
        return ctx
    
    def find_nearest(self, lat, lon, items, max_dist_km=50):
        """Find nearest item within max distance"""
        nearest = None
        min_dist = max_dist_km
        for item in items:
            dist = haversine(lat, lon, item['lat'], item['lon'])
            if dist < min_dist:
                min_dist = dist
                nearest = (item, dist)
        return nearest
    
    def analyze_trajectory(self, group, ctx):
        """Analyze a single fire group trajectory"""
        traj = group.get('trajectory', [])
        if not traj:
            return None
        
        # Get start/end points
        start = traj[0]
        end = traj[-1]
        start_lat, start_lon = start['lat'], start['lon']
        end_lat, end_lon = end['lat'], end['lon']
        
        # Find context for start point
        origin = {}
        nr = self.find_nearest(start_lat, start_lon, ctx['rivers'])
        if nr: origin['river'] = {'name': nr[0]['name'], 'dist_km': round(nr[1], 1)}
        np = self.find_nearest(start_lat, start_lon, ctx['places'])
        if np: origin['place'] = {'name': np[0]['name'], 'type': np[0]['type'], 'dist_km': round(np[1], 1)}
        nrd = self.find_nearest(start_lat, start_lon, ctx['roads'])
        if nrd: origin['road'] = {'type': nrd[0]['type'], 'surface': nrd[0].get('surface'), 'dist_km': round(nrd[1], 1)}
        
        # Find context for end point
        destination = {}
        nr = self.find_nearest(end_lat, end_lon, ctx['rivers'])
        if nr: destination['river'] = {'name': nr[0]['name'], 'dist_km': round(nr[1], 1)}
        np = self.find_nearest(end_lat, end_lon, ctx['places'])
        if np: destination['place'] = {'name': np[0]['name'], 'type': np[0]['type'], 'dist_km': round(np[1], 1)}
        
        # Check for settlement proximity
        ns = self.find_nearest(start_lat, start_lon, ctx['settlements'], max_dist_km=10)
        near_settlement = ns[0] if ns else None
        
        # Find rivers crossed by trajectory
        rivers_crossed = []
        if len(traj) >= 2 and ctx['rivers']:
            seen_rivers = set()
            for i in range(len(traj) - 1):
                pt = traj[i]
                # Check rivers near each trajectory point
                nr = self.find_nearest(pt['lat'], pt['lon'], ctx['rivers'], max_dist_km=2)
                if nr and nr[0]['name'] and nr[0]['name'] not in seen_rivers:
                    seen_rivers.add(nr[0]['name'])
                    rivers_crossed.append(nr[0]['name'])
        
        # Build narrative
        narrative = self.build_narrative(group, origin, destination, near_settlement, rivers_crossed)
        
        return {
            'group_type': group.get('group_type'),
            'start_date': group.get('start_date'),
            'end_date': group.get('end_date'),
            'days': group.get('days', len(traj)),
            'fires': group.get('fires', sum(t.get('fires', 1) for t in traj)),
            'direction': group.get('direction'),
            'distance_km': group.get('total_distance_km', 0),
            'avg_speed_km_day': group.get('avg_speed_km_day', 0),
            'cross_border': group.get('cross_border', False),
            'affected_parks': group.get('affected_parks', []),
            'origin': origin,
            'destination': destination,
            'near_settlement': near_settlement,
            'rivers_crossed': rivers_crossed if rivers_crossed else None,
            'narrative': narrative,
            'trajectory': traj  # Keep for GeoJSON generation
        }
    
    def build_narrative(self, group, origin, destination, near_settlement, rivers_crossed=None):
        """Build human-readable narrative"""
        parts = []
        
        gtype = group.get('group_type', 'unknown')
        days = group.get('days', 1)
        fires = group.get('fires', 0)
        direction = group.get('direction', '')
        distance = group.get('total_distance_km', 0)
        
        # Opening
        if gtype == 'transhumance':
            parts.append(f"Transhumance fire pattern moving {direction}")
        elif gtype == 'herder_local':
            parts.append(f"Local herder burning activity")
        elif gtype == 'herder_fast':
            parts.append(f"Fast-moving herder fires heading {direction}")
        elif gtype in ('local_burning', 'local_stationary'):
            parts.append(f"Localized burning activity")
        elif gtype == 'village_persistent':
            parts.append(f"Persistent village-associated fires")
        else:
            parts.append(f"Fire activity ({gtype})")
        
        # Duration and extent
        if days > 1:
            parts.append(f"over {days} days")
        if distance > 1:
            parts.append(f"covering {distance:.1f} km")
        
        parts.append(f"with {fires} detections.")
        
        # Origin context
        if origin.get('place'):
            p = origin['place']
            parts.append(f"Started near {p['name']} ({p['type']}, {p['dist_km']} km).")
        elif origin.get('river'):
            r = origin['river']
            parts.append(f"Started near {r['name']} river ({r['dist_km']} km).")
        
        # Destination context
        if destination.get('place') and destination['place'] != origin.get('place'):
            p = destination['place']
            parts.append(f"Ended near {p['name']}.")
        
        # Rivers crossed
        if rivers_crossed:
            if len(rivers_crossed) == 1:
                parts.append(f"Crossed {rivers_crossed[0]} river.")
            elif len(rivers_crossed) <= 3:
                parts.append(f"Crossed rivers: {', '.join(rivers_crossed)}.")
            else:
                parts.append(f"Crossed {len(rivers_crossed)} rivers including {rivers_crossed[0]}.")
        
        # Settlement warning
        if near_settlement:
            parts.append(f"⚠️ Near settlement: {near_settlement.get('name', 'Unknown')}.")
        
        return ' '.join(parts)
    
    def process_park(self, park_id):
        """Process a single park"""
        input_file = INPUT_DIR / f'{park_id}.json'
        if not input_file.exists():
            return None
        
        with open(input_file) as f:
            data = json.load(f)
        
        groups = data.get('groups', [])
        if not groups:
            return None
        
        # Load context once per park
        ctx = self.load_park_context(park_id)
        
        # Process each group
        analyzed = []
        for i, group in enumerate(groups):
            # Skip if before min date
            start_date = group.get('start_date', '')
            if start_date < MIN_DATE:
                continue
            
            result = self.analyze_trajectory(group, ctx)
            if result:
                result['group_num'] = i
                result['feature_id'] = f"{park_id}_grp_{i}"
                analyzed.append(result)
        
        return {
            'park_id': park_id,
            'park_name': data.get('park_name', park_id),
            'total_groups': len(analyzed),
            'date_range': data.get('date_range'),
            'trajectories': analyzed
        }

def main():
    global MIN_DATE
    parser = argparse.ArgumentParser(description='Step 2: Trajectory Analysis')
    parser.add_argument('--park', help='Process single park')
    parser.add_argument('--from-date', default=MIN_DATE)
    args = parser.parse_args()
    
    MIN_DATE = args.from_date
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    log("=" * 60)
    log(f"STEP 2: TRAJECTORY ANALYSIS - from {MIN_DATE}")
    log("=" * 60)
    
    conn = sqlite3.connect(str(DB_PATH))
    analyzer = Step2Analyzer(conn)
    
    # Get parks to process
    if args.park:
        parks = [args.park]
    else:
        parks = sorted([f.stem for f in INPUT_DIR.glob('*.json')])
    
    log(f"Processing {len(parks)} parks...")
    
    total_trajectories = 0
    for i, park_id in enumerate(parks):
        result = analyzer.process_park(park_id)
        
        if result:
            output_file = OUTPUT_DIR / f'{park_id}.json'
            with open(output_file, 'w') as f:
                json.dump(result, f)
            
            count = result['total_groups']
            total_trajectories += count
            log(f"  [{i+1}/{len(parks)}] {park_id}: {count} trajectories")
        else:
            log(f"  [{i+1}/{len(parks)}] {park_id}: skipped (no data)")
        
        # Memory cleanup every 20 parks
        if (i + 1) % 20 == 0:
            gc.collect()
    
    conn.close()
    log(f"\nCOMPLETE: {total_trajectories} trajectories across {len(parks)} parks")

if __name__ == '__main__':
    main()
