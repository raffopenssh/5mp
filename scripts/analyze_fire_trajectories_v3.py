#!/usr/bin/env python3
"""
Enhanced Fire Trajectory Analysis v3

Uses REAL trajectory timestamps from fire analysis.
Includes all context data:
- HydroRIVERS for river crossings/parallels
- HeiGIT roads for road proximity
- OSM places for location names
- Settlement/deforestation context
- Climate data for season/weather patterns
"""

import json
import sqlite3
import math
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'
OUTPUT_DIR = DATA_DIR / 'fire_trajectories'

def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance in km between two points"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def bearing(lat1, lon1, lat2, lon2):
    """Calculate bearing in degrees from point 1 to point 2"""
    dlon = math.radians(lon2 - lon1)
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360

def bearing_to_direction(b):
    """Convert bearing to compass direction"""
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 
            'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    idx = int((b + 11.25) / 22.5) % 16
    return dirs[idx]

class TrajectoryAnalyzerV3:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        
        # Load all context data
        self._load_climate()
        self._load_rivers()
        self._load_roads()
        self._load_places()
        self._load_settlements()
        self._load_deforestation()
        
    def _load_climate(self):
        """Load climate data"""
        self.climate = {}
        climate_file = DATA_DIR / 'climate' / 'park_climate.json'
        if climate_file.exists():
            with open(climate_file) as f:
                self.climate = json.load(f)
        print(f"Loaded climate data for {len(self.climate)} parks")
    
    def _load_rivers(self):
        """Load HydroRIVERS data"""
        self.park_rivers = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT pr.park_id, r.hyriv_id, r.name, r.stream_order, r.discharge_cms,
                   r.centroid_lat, r.centroid_lon, r.length_km
            FROM park_rivers pr
            JOIN rivers r ON r.hyriv_id = pr.hyriv_id
            WHERE r.name IS NOT NULL AND r.name != ''
            ORDER BY pr.park_id, r.discharge_cms DESC
        ''')
        for row in cursor:
            self.park_rivers[row['park_id']].append({
                'id': row['hyriv_id'],
                'name': row['name'],
                'order': row['stream_order'],
                'discharge': row['discharge_cms'],
                'lat': row['centroid_lat'],
                'lon': row['centroid_lon'],
                'length_km': row['length_km']
            })
        print(f"Loaded rivers for {len(self.park_rivers)} parks")
    
    def _load_roads(self):
        """Load HeiGIT road data"""
        self.park_roads = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT park_id, feature_id, properties_json,
                   json_extract(geojson, '$.coordinates[0][0]') as lon,
                   json_extract(geojson, '$.coordinates[0][1]') as lat
            FROM feature_geometries
            WHERE feature_type = 'road_heigit'
        ''')
        for row in cursor:
            try:
                props = json.loads(row['properties_json']) if row['properties_json'] else {}
                self.park_roads[row['park_id']].append({
                    'id': row['feature_id'],
                    'lat': row['lat'],
                    'lon': row['lon'],
                    'highway': props.get('highway'),
                    'surface': props.get('surface'),
                    'surface_class': props.get('osm_surface_class'),
                    'dl_class_2024': props.get('dl_class_2024'),
                    'passability': props.get('passability_score')
                })
            except:
                pass
        print(f"Loaded roads for {len(self.park_roads)} parks")
    
    def _load_places(self):
        """Load OSM places"""
        self.park_places = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT park_id, name, place_type, lat, lon
            FROM osm_places
            WHERE name IS NOT NULL AND name != ''
        ''')
        for row in cursor:
            self.park_places[row['park_id']].append({
                'name': row['name'],
                'type': row['place_type'],
                'lat': row['lat'],
                'lon': row['lon']
            })
        print(f"Loaded places for {len(self.park_places)} parks")
    
    def _load_settlements(self):
        """Load settlement data with classifications"""
        self.park_settlements = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT park_id, lat, lon, area_m2, population_est,
                   nearest_place, classification, narrative
            FROM park_settlements
        ''')
        for row in cursor:
            self.park_settlements[row['park_id']].append({
                'lat': row['lat'],
                'lon': row['lon'],
                'area': row['area_m2'],
                'pop': row['population_est'],
                'name': row['nearest_place'],
                'class': row['classification'],
                'narrative': row['narrative']
            })
        print(f"Loaded settlements for {len(self.park_settlements)} parks")
    
    def _load_deforestation(self):
        """Load deforestation patterns"""
        self.park_deforestation = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT park_id, year, lat, lon, area_km2, classification
            FROM deforestation_events
        ''')
        for row in cursor:
            self.park_deforestation[row['park_id']].append({
                'year': row['year'],
                'lat': row['lat'],
                'lon': row['lon'],
                'area_km2': row['area_km2'],
                'class': row['classification']
            })
        print(f"Loaded deforestation for {len(self.park_deforestation)} parks")
    
    def get_season(self, park_id, date_str):
        """Determine season from climate data"""
        climate = self.climate.get(park_id, {})
        try:
            month = int(date_str[5:7])
        except:
            return 'unknown'
        
        dry_season = climate.get('dry_season', '')
        rainy_season = climate.get('rainy_season', '')
        
        # Parse season months
        def parse_months(s):
            if not s:
                return set()
            months = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                     'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
            result = set()
            for part in s.split('-'):
                part = part.strip()[:3]
                if part in months:
                    result.add(months[part])
            # Handle ranges
            if '-' in s:
                parts = s.split('-')
                if len(parts) == 2:
                    start = months.get(parts[0].strip()[:3])
                    end = months.get(parts[1].strip()[:3])
                    if start and end:
                        if start <= end:
                            result = set(range(start, end + 1))
                        else:
                            result = set(range(start, 13)) | set(range(1, end + 1))
            return result
        
        dry_months = parse_months(dry_season)
        rainy_months = parse_months(rainy_season)
        
        if month in dry_months:
            return 'dry'
        elif month in rainy_months:
            return 'wet'
        else:
            return 'transition'
    
    def find_nearest_river(self, park_id, lat, lon, max_dist_km=50):
        """Find nearest river to a point"""
        rivers = self.park_rivers.get(park_id, [])
        nearest = None
        min_dist = float('inf')
        
        for river in rivers:
            dist = haversine(lat, lon, river['lat'], river['lon'])
            if dist < min_dist and dist < max_dist_km:
                min_dist = dist
                nearest = {
                    'name': river['name'],
                    'distance_km': round(dist, 1),
                    'order': river['order'],
                    'discharge_cms': river['discharge']
                }
        
        return nearest
    
    def find_nearest_road(self, park_id, lat, lon, max_dist_km=10):
        """Find nearest road to a point"""
        roads = self.park_roads.get(park_id, [])
        nearest = None
        min_dist = float('inf')
        
        for road in roads:
            if road['lat'] and road['lon']:
                try:
                    rlat = float(road['lat'])
                    rlon = float(road['lon'])
                    dist = haversine(lat, lon, rlat, rlon)
                except:
                    continue
                if dist < min_dist and dist < max_dist_km:
                    min_dist = dist
                    nearest = {
                        'distance_km': round(dist, 1),
                        'highway': road['highway'],
                        'surface': road['surface'] or road['surface_class'],
                        'passability': road['passability']
                    }
        
        return nearest
    
    def find_nearest_place(self, park_id, lat, lon, max_dist_km=50):
        """Find nearest named place"""
        places = self.park_places.get(park_id, [])
        nearest = None
        min_dist = float('inf')
        
        for place in places:
            dist = haversine(lat, lon, place['lat'], place['lon'])
            if dist < min_dist and dist < max_dist_km:
                min_dist = dist
                nearest = {
                    'name': place['name'],
                    'type': place['type'],
                    'distance_km': round(dist, 1)
                }
        
        return nearest
    
    def find_nearest_settlement(self, park_id, lat, lon, max_dist_km=30):
        """Find nearest settlement"""
        settlements = self.park_settlements.get(park_id, [])
        nearest = None
        min_dist = float('inf')
        
        for sett in settlements:
            dist = haversine(lat, lon, sett['lat'], sett['lon'])
            if dist < min_dist and dist < max_dist_km:
                min_dist = dist
                nearest = {
                    'distance_km': round(dist, 1),
                    'name': sett['name'],
                    'class': sett['class'],
                    'population': sett['pop']
                }
        
        return nearest
    
    def detect_river_crossing(self, park_id, trajectory):
        """Detect if trajectory crosses a river"""
        rivers = self.park_rivers.get(park_id, [])
        if not rivers or len(trajectory) < 2:
            return []
        
        crossings = []
        for i in range(1, len(trajectory)):
            p1 = trajectory[i-1]
            p2 = trajectory[i]
            
            for river in rivers[:20]:  # Check top 20 rivers
                # Check if river centroid is between the two points
                river_lat, river_lon = river['lat'], river['lon']
                if river_lat and river_lon:
                    # Simple bounding box check
                    min_lat = min(p1['lat'], p2['lat']) - 0.1
                    max_lat = max(p1['lat'], p2['lat']) + 0.1
                    min_lon = min(p1['lon'], p2['lon']) - 0.1
                    max_lon = max(p1['lon'], p2['lon']) + 0.1
                    
                    if min_lat <= river_lat <= max_lat and min_lon <= river_lon <= max_lon:
                        crossings.append({
                            'river': river['name'],
                            'date': p2['date'],
                            'order': river['order']
                        })
                        break  # One crossing per segment
        
        return crossings
    
    def analyze_trajectory_direction(self, trajectory):
        """Analyze trajectory direction from actual points"""
        if len(trajectory) < 2:
            return None
        
        start = trajectory[0]
        end = trajectory[-1]
        
        total_dist = haversine(start['lat'], start['lon'], end['lat'], end['lon'])
        b = bearing(start['lat'], start['lon'], end['lat'], end['lon'])
        direction = bearing_to_direction(b)
        
        # Calculate per-day movements
        daily_movements = []
        for i in range(1, len(trajectory)):
            p1, p2 = trajectory[i-1], trajectory[i]
            dist = haversine(p1['lat'], p1['lon'], p2['lat'], p2['lon'])
            daily_movements.append({
                'date': p2['date'],
                'distance_km': round(dist, 1),
                'bearing': round(bearing(p1['lat'], p1['lon'], p2['lat'], p2['lon']), 0)
            })
        
        return {
            'net_distance_km': round(total_dist, 1),
            'bearing': round(b, 0),
            'direction': direction,
            'net_south_km': round((start['lat'] - end['lat']) * 111, 1),
            'net_east_km': round((end['lon'] - start['lon']) * 111, 1),
            'daily_movements': daily_movements
        }
    
    def classify_movement_pattern(self, direction_data, group_type):
        """Classify movement pattern from direction data"""
        if not direction_data:
            return 'unknown'
        
        net_south = direction_data.get('net_south_km', 0)
        daily_movements = direction_data.get('daily_movements', [])
        
        if not daily_movements:
            return group_type
        
        avg_daily = sum(m['distance_km'] for m in daily_movements) / len(daily_movements)
        
        # Classify based on patterns
        if abs(net_south) > 30 and avg_daily > 5:
            return 'transhumance'
        elif avg_daily > 15:
            return 'management_vehicle'
        elif avg_daily > 8:
            return 'herder_mobile'
        elif avg_daily > 3:
            return 'local_burning'
        else:
            return 'stationary_fire'
    
    def process_park(self, park_id):
        """Process all trajectories for a park"""
        # Load fire analysis data
        analysis_file = DATA_DIR / 'fire_analysis' / f'{park_id}.json'
        if not analysis_file.exists():
            return []
        
        with open(analysis_file) as f:
            analysis = json.load(f)
        
        trajectories = []
        
        for year_data in analysis.get('years', []):
            year = year_data['year']
            groups = year_data.get('analysis', {})
            
            group_num = 0
            for group_type, group_list in groups.items():
                if not isinstance(group_list, list):
                    continue
                    
                for group in group_list:
                    group_num += 1
                    
                    # Get trajectory points with REAL timestamps
                    traj_points = group.get('trajectory', [])
                    if not traj_points:
                        # Fallback to start/end if no trajectory
                        traj_points = [
                            {'date': group['start_date'], 'lat': group['start_lat'], 'lon': group['start_lon'], 'fires': 1},
                            {'date': group['end_date'], 'lat': group['end_lat'], 'lon': group['end_lon'], 'fires': 1}
                        ]
                    
                    # Build coordinates with actual timestamps
                    coords_with_time = []
                    for pt in traj_points:
                        coords_with_time.append({
                            'lon': pt['lon'],
                            'lat': pt['lat'],
                            'date': pt['date'],
                            'fires': pt.get('fires', 1),
                            'timestamp': f"{pt['date']}T12:00:00Z"
                        })
                    
                    # Analyze direction from actual trajectory
                    direction = self.analyze_trajectory_direction(traj_points)
                    
                    # Get climate context
                    start_date = traj_points[0]['date']
                    season = self.get_season(park_id, start_date)
                    
                    # Find context at start point
                    start_lat, start_lon = traj_points[0]['lat'], traj_points[0]['lon']
                    origin_river = self.find_nearest_river(park_id, start_lat, start_lon)
                    origin_road = self.find_nearest_road(park_id, start_lat, start_lon)
                    origin_place = self.find_nearest_place(park_id, start_lat, start_lon)
                    origin_settlement = self.find_nearest_settlement(park_id, start_lat, start_lon)
                    
                    # Find context at end point
                    end_lat, end_lon = traj_points[-1]['lat'], traj_points[-1]['lon']
                    dest_river = self.find_nearest_river(park_id, end_lat, end_lon)
                    dest_place = self.find_nearest_place(park_id, end_lat, end_lon)
                    
                    # Detect river crossings
                    river_crossings = self.detect_river_crossing(park_id, traj_points)
                    
                    # Refine classification based on context
                    refined_type = self.classify_movement_pattern(direction, group_type)
                    
                    # Build trajectory record
                    traj_record = {
                        'feature_id': f"{park_id}_{year}_grp_{group_num}",
                        'park_id': park_id,
                        'year': year,
                        'group_num': group_num,
                        'group_type': group_type,
                        'refined_type': refined_type,
                        
                        # Temporal data with REAL timestamps
                        'start_date': group['start_date'],
                        'end_date': group['end_date'],
                        'days': group['days'],
                        'fires_total': group['fires'],
                        'season': season,
                        
                        # Trajectory with actual timestamps
                        'coordinates': [[pt['lon'], pt['lat']] for pt in traj_points],
                        'coordinates_with_time': coords_with_time,
                        
                        # Direction analysis from real trajectory
                        'direction': direction,
                        
                        # Movement metrics
                        'avg_speed_km_day': group.get('avg_speed_km_day', 0),
                        'max_speed_km_day': group.get('max_speed_km_day', 0),
                        'total_distance_km': group.get('total_distance_km', 0),
                        
                        # Context at origin
                        'origin': {
                            'lat': start_lat,
                            'lon': start_lon,
                            'nearest_river': origin_river,
                            'nearest_road': origin_road,
                            'nearest_place': origin_place,
                            'nearest_settlement': origin_settlement
                        },
                        
                        # Context at destination
                        'destination': {
                            'lat': end_lat,
                            'lon': end_lon,
                            'nearest_river': dest_river,
                            'nearest_place': dest_place
                        },
                        
                        # River crossings along trajectory
                        'river_crossings': river_crossings,
                        'rivers_crossed': list(set(c['river'] for c in river_crossings))
                    }
                    
                    trajectories.append(traj_record)
        
        return trajectories
    
    def run(self):
        """Process all parks"""
        OUTPUT_DIR.mkdir(exist_ok=True)
        
        # Get parks from fire analysis
        analysis_dir = DATA_DIR / 'fire_analysis'
        park_files = list(analysis_dir.glob('*.json'))
        
        print(f"\nEnhanced Fire Trajectory Analysis v3")
        print("=" * 60)
        print(f"Processing {len(park_files)} parks with full context data...")
        print()
        
        total_trajectories = 0
        type_counts = defaultdict(int)
        
        for i, park_file in enumerate(sorted(park_files)):
            park_id = park_file.stem
            trajectories = self.process_park(park_id)
            
            if trajectories:
                # Save to JSON
                output_file = OUTPUT_DIR / f'{park_id}.json'
                with open(output_file, 'w') as f:
                    json.dump(trajectories, f)
                
                total_trajectories += len(trajectories)
                for t in trajectories:
                    type_counts[t['group_type']] += 1
            
            print(f"[{i+1}/{len(park_files)}] {park_id}... {len(trajectories)} trajectories")
        
        print()
        print("=" * 60)
        print(f"Total: {total_trajectories} trajectories")
        print()
        print("By type:")
        for gtype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"  {gtype}: {count}")

if __name__ == '__main__':
    analyzer = TrajectoryAnalyzerV3()
    analyzer.run()
