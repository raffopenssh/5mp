#!/usr/bin/env python3
"""
Enhanced Fire Trajectory Analysis v4

Uses fire_groups_v2 data with cross-park clustering.
Enhanced context from:
- park_rivers_hydro (HydroRIVERS with geometry)
- park_lakes_hydro (HydroLAKES with geometry)
- roads_heigit (HeiGIT roads with surface/passability)
- osm_places (villages, towns, landmarks)
- park_settlements (with polygon_ids for linking)
- deforestation_events (with polygon_ids for linking)
- Climate data for seasonality

Detailed classification based on:
- Trajectory speed and direction
- River/lake crossings and parallels
- Road proximity and surface type
- Settlement proximity and type
- Deforestation correlation
- Seasonal patterns
"""

import json
import argparse
import sqlite3
import math
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import gc

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'
INPUT_DIR = DATA_DIR / 'fire_groups_v2'
OUTPUT_DIR = DATA_DIR / 'fire_trajectories_v2'

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

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
    b = math.degrees(math.atan2(x, y))
    return (b + 360) % 360

def bearing_to_direction(b):
    """Convert bearing to compass direction"""
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 
            'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    return dirs[int((b + 11.25) / 22.5) % 16]

class TrajectoryAnalyzerV4:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        
        # Load all context data
        self._load_climate()
        self._load_rivers()
        self._load_lakes()
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
        log(f"  Climate: {len(self.climate)} parks")
    
    def _load_rivers(self):
        """Load HydroRIVERS data from park_rivers_hydro"""
        self.park_rivers = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT park_id, hyriv_id, name, stream_order, length_km,
                   json_extract(geojson, '$.coordinates[0][0]') as lon,
                   json_extract(geojson, '$.coordinates[0][1]') as lat
            FROM park_rivers_hydro
            WHERE name IS NOT NULL AND name != ''
        ''')
        for row in cursor:
            try:
                lat = float(row['lat']) if row['lat'] else None
                lon = float(row['lon']) if row['lon'] else None
                if lat and lon:
                    self.park_rivers[row['park_id']].append({
                        'id': row['hyriv_id'],
                        'name': row['name'],
                        'order': row['stream_order'],
                        'length_km': row['length_km'],
                        'lat': lat,
                        'lon': lon
                    })
            except:
                pass
        log(f"  Rivers: {sum(len(v) for v in self.park_rivers.values())} in {len(self.park_rivers)} parks")
    
    def _load_lakes(self):
        """Load HydroLAKES data from park_lakes_hydro"""
        self.park_lakes = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT park_id, hylak_id, name, lake_type, area_km2,
                   centroid_lat, centroid_lon
            FROM park_lakes_hydro
            WHERE name IS NOT NULL AND name != ''
        ''')
        for row in cursor:
            self.park_lakes[row['park_id']].append({
                'id': row['hylak_id'],
                'name': row['name'],
                'type': row['lake_type'],
                'area_km2': row['area_km2'],
                'lat': row['centroid_lat'],
                'lon': row['centroid_lon']
            })
        log(f"  Lakes: {sum(len(v) for v in self.park_lakes.values())} in {len(self.park_lakes)} parks")
    
    def _load_roads(self):
        """Load HeiGIT road data from roads_heigit"""
        self.park_roads = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT park_id, osm_id, name, highway_type, surface,
                   dl_class_2024, passability, length_km,
                   json_extract(geojson, '$.coordinates[0][0]') as lon,
                   json_extract(geojson, '$.coordinates[0][1]') as lat
            FROM roads_heigit
        ''')
        for row in cursor:
            try:
                lat = float(row['lat']) if row['lat'] else None
                lon = float(row['lon']) if row['lon'] else None
                if lat and lon:
                    self.park_roads[row['park_id']].append({
                        'id': row['osm_id'],
                        'name': row['name'],
                        'highway': row['highway_type'],
                        'surface': row['surface'],
                        'surface_2024': row['dl_class_2024'],
                        'passability': row['passability'],
                        'passability_risk': None,
                        'length_km': row['length_km'],
                        'lat': lat,
                        'lon': lon
                    })
            except:
                pass
        log(f"  Roads: {sum(len(v) for v in self.park_roads.values())} in {len(self.park_roads)} parks")
    
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
                'lon': row['lon'],
                'population': None
            })
        log(f"  Places: {sum(len(v) for v in self.park_places.values())} in {len(self.park_places)} parks")
    
    def _load_settlements(self):
        """Load settlement events with polygon links"""
        self.park_settlements = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT park_id, lat, lon, area_m2, population_est,
                   nearest_place, distance_to_place_km, classification, 
                   narrative, polygon_ids, fires_5km
            FROM park_settlements
        ''')
        for row in cursor:
            self.park_settlements[row['park_id']].append({
                'lat': row['lat'],
                'lon': row['lon'],
                'area_m2': row['area_m2'],
                'population': row['population_est'],
                'name': row['nearest_place'],
                'distance_km': row['distance_to_place_km'],
                'classification': row['classification'],
                'narrative': row['narrative'],
                'polygon_ids': row['polygon_ids'],
                'fires_5km': row['fires_5km']
            })
        log(f"  Settlements: {sum(len(v) for v in self.park_settlements.values())} in {len(self.park_settlements)} parks")
    
    def _load_deforestation(self):
        """Load deforestation events with polygon links"""
        self.park_deforestation = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT park_id, year, lat, lon, area_km2, classification,
                   narrative, polygon_ids, fires_same_year, fire_ratio
            FROM deforestation_events
        ''')
        for row in cursor:
            self.park_deforestation[row['park_id']].append({
                'year': row['year'],
                'lat': row['lat'],
                'lon': row['lon'],
                'area_km2': row['area_km2'],
                'classification': row['classification'],
                'narrative': row['narrative'],
                'polygon_ids': row['polygon_ids'],
                'fires_same_year': row['fires_same_year'],
                'fire_ratio': row['fire_ratio']
            })
        log(f"  Deforestation: {sum(len(v) for v in self.park_deforestation.values())} in {len(self.park_deforestation)} parks")
    
    def get_season(self, park_id, date_str):
        """Determine season from climate data"""
        climate = self.climate.get(park_id, {})
        try:
            month = int(date_str[5:7])
        except:
            return 'unknown'
        
        dry_season = climate.get('dry_season', '')
        rainy_season = climate.get('rainy_season', '')
        
        def parse_months(s):
            if not s:
                return set()
            months = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                     'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
            result = set()
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
    
    def find_nearest_river(self, park_id, lat, lon, max_dist_km=30):
        """Find nearest named river"""
        rivers = self.park_rivers.get(park_id, [])
        nearest = None
        min_dist = float('inf')
        
        for river in rivers:
            if river['lat'] and river['lon']:
                dist = haversine(lat, lon, river['lat'], river['lon'])
                if dist < min_dist and dist < max_dist_km:
                    min_dist = dist
                    nearest = {
                        'name': river['name'],
                        'distance_km': round(dist, 1),
                        'order': river['order']
                    }
        return nearest
    
    def find_nearest_lake(self, park_id, lat, lon, max_dist_km=30):
        """Find nearest named lake"""
        lakes = self.park_lakes.get(park_id, [])
        nearest = None
        min_dist = float('inf')
        
        for lake in lakes:
            if lake['lat'] and lake['lon']:
                dist = haversine(lat, lon, lake['lat'], lake['lon'])
                if dist < min_dist and dist < max_dist_km:
                    min_dist = dist
                    nearest = {
                        'name': lake['name'],
                        'distance_km': round(dist, 1),
                        'area_km2': lake['area_km2']
                    }
        return nearest
    
    def find_nearest_road(self, park_id, lat, lon, max_dist_km=15):
        """Find nearest road with surface info"""
        roads = self.park_roads.get(park_id, [])
        nearest = None
        min_dist = float('inf')
        
        for road in roads:
            if road['lat'] and road['lon']:
                dist = haversine(lat, lon, road['lat'], road['lon'])
                if dist < min_dist and dist < max_dist_km:
                    min_dist = dist
                    nearest = {
                        'distance_km': round(dist, 1),
                        'highway': road['highway'],
                        'surface': road['surface'],
                        'surface_2024': road['surface_2024'],
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
                    'distance_km': round(dist, 1),
                    'population': place['population']
                }
        return nearest
    
    def find_nearest_settlement(self, park_id, lat, lon, max_dist_km=20):
        """Find nearest settlement with polygon link"""
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
                    'classification': sett['classification'],
                    'population': sett['population'],
                    'polygon_ids': sett['polygon_ids'],
                    'fires_nearby': sett['fires_5km']
                }
        return nearest
    
    def find_nearby_deforestation(self, park_id, lat, lon, year, max_dist_km=10):
        """Find deforestation events near trajectory"""
        events = self.park_deforestation.get(park_id, [])
        nearby = []
        
        for evt in events:
            if abs(evt['year'] - year) <= 1:  # Same year or adjacent
                dist = haversine(lat, lon, evt['lat'], evt['lon'])
                if dist < max_dist_km:
                    nearby.append({
                        'distance_km': round(dist, 1),
                        'year': evt['year'],
                        'area_km2': evt['area_km2'],
                        'classification': evt['classification'],
                        'polygon_ids': evt['polygon_ids'],
                        'fire_correlated': evt['fires_same_year'] and evt['fires_same_year'] > 0
                    })
        
        return nearby[:3]  # Top 3 nearest
    
    def classify_trajectory(self, group, context):
        """Enhanced classification based on all context"""
        speed = group.get('speed_km_day', 0)
        days = group.get('days', 1)
        pct_inside = group.get('pct_inside', 0)
        group_type = group.get('group_type', 'unknown')
        
        # Get context info
        river = context.get('nearest_river')
        lake = context.get('nearest_lake')
        road = context.get('nearest_road')
        settlement = context.get('nearest_settlement')
        deforest = context.get('nearby_deforestation', [])
        season = context.get('season', 'unknown')
        
        # Build detailed classification
        classification = {
            'primary_type': group_type,
            'confidence': 0.5,
            'factors': []
        }
        
        # Speed-based classification
        if speed > 15:
            classification['primary_type'] = 'transhumance_fast'
            classification['factors'].append('high_speed')
            classification['confidence'] = 0.8
        elif speed > 8:
            classification['primary_type'] = 'transhumance'
            classification['factors'].append('moderate_speed')
            classification['confidence'] = 0.75
        elif speed > 4:
            classification['primary_type'] = 'herder_local'
            classification['factors'].append('local_movement')
            classification['confidence'] = 0.7
        
        # Settlement proximity
        if settlement and settlement['distance_km'] < 5:
            classification['factors'].append('near_settlement')
            if settlement['fires_nearby'] and settlement['fires_nearby'] > 10:
                classification['factors'].append('fire_prone_area')
                classification['confidence'] = min(classification['confidence'] + 0.1, 0.95)
        
        # Road proximity - suggests human access
        if road and road['distance_km'] < 3:
            classification['factors'].append('near_road')
            if road['passability'] and 'good' in road['passability'].lower():
                classification['factors'].append('accessible_road')
        
        # River/lake proximity - natural fire breaks
        if river and river['distance_km'] < 5:
            classification['factors'].append('near_river')
        if lake and lake['distance_km'] < 5:
            classification['factors'].append('near_lake')
        
        # Deforestation correlation
        if deforest:
            classification['factors'].append('deforestation_nearby')
            for d in deforest:
                if d.get('fire_correlated'):
                    classification['factors'].append('fire_deforestation_link')
                    classification['confidence'] = min(classification['confidence'] + 0.1, 0.95)
                    break
        
        # Season-based refinement
        if season == 'dry':
            classification['factors'].append('dry_season')
            if pct_inside > 80 and days <= 3:
                classification['primary_type'] = 'management_controlled'
                classification['confidence'] = 0.8
        elif season == 'wet':
            classification['factors'].append('wet_season')
            # Fires in wet season are unusual
            classification['factors'].append('unusual_timing')
        
        # Cross-border fires
        if group.get('cross_border'):
            classification['factors'].append('cross_border')
            if speed > 5:
                classification['primary_type'] = 'transhumance'
                classification['confidence'] = min(classification['confidence'] + 0.1, 0.95)
        
        # Internal fires with high percentage inside
        if pct_inside > 90 and days <= 2 and speed < 3:
            classification['primary_type'] = 'management_spot'
            classification['factors'].append('contained_fire')
            classification['confidence'] = 0.85
        
        return classification
    
    def generate_narrative(self, group, context, classification):
        """Generate detailed narrative for trajectory"""
        parts = []
        
        # Basic info
        fires = group['fires']
        days = group['days']
        distance = group.get('distance_km', 0)
        direction = group.get('direction', 'unknown')
        start_date = group.get('start_date', '')
        end_date = group.get('end_date', '')
        
        # Opening
        type_desc = {
            'transhumance_fast': 'Fast-moving transhumance fire',
            'transhumance': 'Transhumance fire pattern',
            'herder_local': 'Local herder fire activity',
            'management_controlled': 'Controlled management burn',
            'management_spot': 'Spot management fire',
            'spot_fire': 'Isolated fire event',
            'external_fire': 'External fire approaching park',
            'mixed_origin': 'Mixed-origin fire activity'
        }.get(classification['primary_type'], 'Fire activity')
        
        parts.append(f"{type_desc} detected {start_date}")
        if days > 1:
            parts.append(f"to {end_date} ({days} days)")
        
        # Movement
        if distance > 1:
            parts.append(f"Traveled {distance:.1f}km {direction}")
        
        # Location context
        place = context.get('nearest_place')
        if place:
            parts.append(f"near {place['name']} ({place['distance_km']:.1f}km)")
        
        # Water features
        river = context.get('nearest_river')
        lake = context.get('nearest_lake')
        if river:
            parts.append(f"Near {river['name']} river")
        if lake:
            parts.append(f"Near {lake['name']} lake")
        
        # Human factors
        settlement = context.get('nearest_settlement')
        road = context.get('nearest_road')
        if settlement and settlement['distance_km'] < 10:
            parts.append(f"Settlement {settlement['distance_km']:.1f}km away")
        if road and road['distance_km'] < 5:
            surface = road.get('surface_2024') or road.get('surface') or 'unknown'
            parts.append(f"Near {surface} road")
        
        # Deforestation link
        deforest = context.get('nearby_deforestation', [])
        if deforest:
            total_area = sum(d['area_km2'] for d in deforest)
            parts.append(f"Deforestation nearby ({total_area:.2f}km²)")
        
        # Season
        season = context.get('season', 'unknown')
        if season != 'unknown':
            parts.append(f"({season} season)")
        
        # Cross-border
        if group.get('cross_border'):
            affected = group.get('affected_parks', [])
            if len(affected) > 1:
                parts.append(f"Crosses into {len(affected)} parks")
        
        return '. '.join(parts) + '.'
    
    def analyze_trajectory(self, group, park_id, group_index=0):
        """Analyze a single fire group trajectory"""
        # Extract trajectory points
        trajectory = group.get('trajectory', [])
        if not trajectory:
            return None
        
        # Get centroid for context lookup
        lons = [p[0] for p in trajectory]
        lats = [p[1] for p in trajectory]
        centroid_lon = sum(lons) / len(lons)
        centroid_lat = sum(lats) / len(lats)
        
        # Get year from start date
        start_date = group.get('start_date', '')
        try:
            year = int(start_date[:4])
        except:
            year = 2024
        
        # Build context
        context = {
            'nearest_river': self.find_nearest_river(park_id, centroid_lat, centroid_lon),
            'nearest_lake': self.find_nearest_lake(park_id, centroid_lat, centroid_lon),
            'nearest_road': self.find_nearest_road(park_id, centroid_lat, centroid_lon),
            'nearest_place': self.find_nearest_place(park_id, centroid_lat, centroid_lon),
            'nearest_settlement': self.find_nearest_settlement(park_id, centroid_lat, centroid_lon),
            'nearby_deforestation': self.find_nearby_deforestation(park_id, centroid_lat, centroid_lon, year),
            'season': self.get_season(park_id, start_date)
        }
        
        # Also check affected parks for cross-border context
        for affected_park in group.get('affected_parks', []):
            if affected_park != park_id:
                # Add context from affected parks
                river = self.find_nearest_river(affected_park, centroid_lat, centroid_lon)
                if river and (not context['nearest_river'] or river['distance_km'] < context['nearest_river']['distance_km']):
                    context['nearest_river'] = river
        
        # Classify
        classification = self.classify_trajectory(group, context)
        
        # Generate narrative
        narrative = self.generate_narrative(group, context, classification)
        
        # Generate feature_id if not present
        feature_id = group.get('feature_id')
        if not feature_id:
            group_id = group.get('group_id', group_index)
            feature_id = f"{park_id}_grp_{group_id}"
        
        # Build enhanced trajectory
        enhanced = {
            **group,  # Keep all original fields
            'feature_id': feature_id,
            'context': context,
            'classification': classification,
            'narrative': narrative,
            'year': year,
            # Add trajectory with timestamps
            'trajectory_with_time': [
                {'lon': p[0], 'lat': p[1], 'date': p[2]} 
                for p in trajectory
            ]
        }
        
        return enhanced
    
    def process_park(self, park_id, cutoff_date=None):
        """Process fire groups for a park
        
        Args:
            park_id: Park identifier
            cutoff_date: If set, only process groups ending after this date (incremental mode)
        """
        input_file = INPUT_DIR / f"{park_id}.json"
        if not input_file.exists():
            return None
        
        with open(input_file) as f:
            groups = json.load(f)
        
        enhanced = []
        for idx, group in enumerate(groups):
            # In incremental mode, skip old groups
            if cutoff_date and group.get('end_date', '') < cutoff_date:
                continue
            result = self.analyze_trajectory(group, park_id, group_index=idx)
            if result:
                enhanced.append(result)
        
        return enhanced
    
    def run(self, incremental=False, days=14):
        """Process all parks
        
        Args:
            incremental: If True, only process parks with recent fire data
            days: Days to consider "recent" in incremental mode
        """
        OUTPUT_DIR.mkdir(exist_ok=True)
        
        # Get list of parks from input
        park_files = list(INPUT_DIR.glob("*.json"))
        log(f"Processing {len(park_files)} parks...")
        
        cutoff_date = None
        if incremental:
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            log(f"INCREMENTAL: Only processing groups since {cutoff_date}")
        
        total_trajectories = 0
        new_trajectories = 0
        parks_processed = 0
        parks_skipped = 0
        
        for i, park_file in enumerate(sorted(park_files), 1):
            park_id = park_file.stem
            
            # In incremental mode, check if park has recent fires
            if incremental:
                with open(park_file) as f:
                    groups = json.load(f)
                
                recent_count = sum(1 for g in groups if g.get('end_date', '') >= cutoff_date)
                if recent_count == 0:
                    parks_skipped += 1
                    continue
            
            # Process only recent groups in incremental mode
            enhanced = self.process_park(park_id, cutoff_date if incremental else None)
            
            if enhanced:
                output_file = OUTPUT_DIR / f"{park_id}.json"
                
                # In incremental mode, merge with existing trajectories
                if incremental and output_file.exists():
                    with open(output_file) as f:
                        existing = json.load(f)
                    # Keep old trajectories, add new ones
                    existing_ids = {t.get('feature_id') for t in existing}
                    for t in enhanced:
                        if t.get('feature_id') not in existing_ids:
                            existing.append(t)
                            new_trajectories += 1
                    enhanced = existing
                else:
                    new_trajectories += len(enhanced)
                
                # Atomic write: write to temp file then rename
                temp_file = output_file.with_suffix('.json.tmp')
                with open(temp_file, 'w') as f:
                    json.dump(enhanced, f)
                temp_file.rename(output_file)
                
                total_trajectories += len(enhanced)
                parks_processed += 1
                
                if parks_processed % 20 == 0:
                    log(f"[{parks_processed}] {park_id}: {len(enhanced)} trajectories")
            
            # Memory management
            if i % 50 == 0:
                gc.collect()
        
        log(f"\nComplete!")
        log(f"  Parks processed: {parks_processed}")
        if incremental:
            log(f"  Parks skipped (no recent fires): {parks_skipped}")
            log(f"  New trajectories: {new_trajectories}")
        log(f"  Total trajectories: {total_trajectories}")
        log(f"  Output: {OUTPUT_DIR}")


def main():
    parser = argparse.ArgumentParser(description="Fire Trajectory Analysis v4")
    parser.add_argument("--incremental", action="store_true",
                        help="Only process parks with recent fire data")
    parser.add_argument("--days", type=int, default=14,
                        help="Days to consider recent in incremental mode")
    args = parser.parse_args()
    
    log("=" * 60)
    log("Enhanced Fire Trajectory Analysis v4")
    log("=" * 60)
    
    analyzer = TrajectoryAnalyzerV4()
    log("Context data loaded")
    
    analyzer.run(incremental=args.incremental, days=args.days)

if __name__ == "__main__":
    main()
