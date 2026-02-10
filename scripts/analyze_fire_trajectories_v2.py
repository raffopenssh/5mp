#!/usr/bin/env python3
"""
Enhanced Fire Trajectory Analysis v2

Includes:
- Climate context (season, precipitation, temperature)
- River crossing/parallel analysis
- Settlement proximity
- Better classification using climate patterns
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

class EnhancedTrajectoryAnalyzer:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row
        
        # Load context data
        self._load_climate()
        self._load_rivers()
        self._load_settlements()
        
    def _load_climate(self):
        """Load climate data for season detection"""
        self.climate = {}
        climate_file = DATA_DIR / 'climate' / 'park_climate.json'
        if climate_file.exists():
            with open(climate_file) as f:
                self.climate = json.load(f)
        print(f"Loaded climate data for {len(self.climate)} parks")
    
    def _load_rivers(self):
        """Load major rivers for each park"""
        self.park_rivers = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT pr.park_id, r.hyriv_id, r.name, r.stream_order, r.discharge_cms,
                   r.centroid_lat, r.centroid_lon, r.geojson
            FROM park_rivers pr
            JOIN rivers r ON r.hyriv_id = pr.hyriv_id
            WHERE r.stream_order >= 4 OR r.discharge_cms > 10
            ORDER BY pr.park_id, r.discharge_cms DESC
        ''')
        for row in cursor:
            self.park_rivers[row['park_id']].append({
                'id': row['hyriv_id'],
                'name': row['name'] or f"River-{row['hyriv_id']}",
                'order': row['stream_order'],
                'discharge': row['discharge_cms'],
                'lat': row['centroid_lat'],
                'lon': row['centroid_lon'],
                'geojson': row['geojson']
            })
        print(f"Loaded rivers for {len(self.park_rivers)} parks")
    
    def _load_settlements(self):
        """Load settlements for proximity analysis"""
        self.park_settlements = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT park_id, lat, lon, area_m2, population_est, 
                   nearest_place, classification
            FROM park_settlements
            WHERE population_est > 100 OR area_m2 > 50000
        ''')
        for row in cursor:
            self.park_settlements[row['park_id']].append({
                'lat': row['lat'],
                'lon': row['lon'],
                'area': row['area_m2'],
                'pop': row['population_est'],
                'name': row['nearest_place'],
                'class': row['classification']
            })
        print(f"Loaded settlements for {len(self.park_settlements)} parks")
    
    def get_climate_context(self, park_id, date_str):
        """Get full climate context for a date"""
        climate = self.climate.get(park_id, {})
        
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            month = dt.month
            month_idx = month - 1
        except:
            return {
                'season': 'unknown',
                'month_precip_mm': None,
                'climate_zone': climate.get('climate_zone', 'unknown'),
                'is_peak_dry': False
            }
        
        # Get monthly precipitation
        monthly_precip = climate.get('monthly_precip_mm', [])
        month_precip = monthly_precip[month_idx] if month_idx < len(monthly_precip) else None
        
        # Determine season
        dry_season = climate.get('dry_season', '')
        rainy_season = climate.get('rainy_season', '')
        
        season = 'unknown'
        is_peak_dry = False
        
        if dry_season:
            dry_months = self._parse_season_months(dry_season)
            rainy_months = self._parse_season_months(rainy_season)
            
            if month in dry_months:
                season = 'dry'
                # Check if peak dry (driest 2 months)
                if monthly_precip:
                    sorted_months = sorted(range(12), key=lambda i: monthly_precip[i] if i < len(monthly_precip) else 999)
                    if month_idx in sorted_months[:2]:
                        is_peak_dry = True
            elif month in rainy_months:
                season = 'wet'
            else:
                # Transition period
                season = 'transition'
        
        return {
            'season': season,
            'month_precip_mm': month_precip,
            'climate_zone': climate.get('climate_zone', 'unknown'),
            'is_peak_dry': is_peak_dry,
            'temp_max_c': climate.get('temp_max_c'),
            'precip_annual_mm': climate.get('precip_annual_mm')
        }
    
    def _parse_season_months(self, season_str):
        """Parse season string like 'Dec-Feb' or 'Jun-Sep' to month numbers"""
        if not season_str or '-' not in season_str:
            return []
        
        month_map = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                    'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
        
        try:
            parts = season_str.split('-')
            start = month_map.get(parts[0][:3], 0)
            end = month_map.get(parts[1][:3], 0)
            
            if start == 0 or end == 0:
                return []
            
            if start <= end:
                return list(range(start, end + 1))
            else:
                # Wraps around year (e.g., Nov-Mar)
                return list(range(start, 13)) + list(range(1, end + 1))
        except:
            return []
    
    def find_nearby_river(self, park_id, lat, lon, max_dist_km=20):
        """Find nearest significant river to a point"""
        rivers = self.park_rivers.get(park_id, [])
        nearest = None
        min_dist = float('inf')
        
        for r in rivers:
            if r['lat'] and r['lon']:
                dist = self._haversine(lat, lon, r['lat'], r['lon'])
                if dist < min_dist and dist < max_dist_km:
                    min_dist = dist
                    nearest = {
                        'name': r['name'],
                        'distance_km': round(dist, 1),
                        'discharge': r['discharge']
                    }
        
        return nearest
    
    def find_river_crossings(self, park_id, coords):
        """Detect river crossings along trajectory"""
        rivers = self.park_rivers.get(park_id, [])
        crossings = []
        
        for i in range(len(coords) - 1):
            lat1, lon1 = coords[i][1], coords[i][0]
            lat2, lon2 = coords[i+1][1], coords[i+1][0]
            
            for r in rivers[:20]:  # Top 20 rivers
                if not r['geojson']:
                    continue
                try:
                    geom = json.loads(r['geojson'])
                    if geom.get('type') == 'LineString':
                        river_coords = geom['coordinates']
                        if self._segment_intersects_line(lon1, lat1, lon2, lat2, river_coords):
                            crossings.append({
                                'river': r['name'],
                                'segment': i,
                                'discharge': r['discharge']
                            })
                            break
                except:
                    pass
        
        return crossings
    
    def find_river_parallels(self, park_id, coords, threshold_km=5):
        """Detect trajectory parallel to river"""
        rivers = self.park_rivers.get(park_id, [])
        parallels = []
        
        if len(coords) < 3:
            return parallels
        
        # Sample trajectory points
        sample_points = coords[::max(1, len(coords)//5)]
        
        for r in rivers[:10]:  # Top 10 rivers
            if not r['geojson']:
                continue
            try:
                geom = json.loads(r['geojson'])
                if geom.get('type') == 'LineString':
                    river_coords = geom['coordinates']
                    
                    # Check if multiple trajectory points are near river
                    near_count = 0
                    for pt in sample_points:
                        for rc in river_coords[::max(1, len(river_coords)//10)]:
                            dist = self._haversine(pt[1], pt[0], rc[1], rc[0])
                            if dist < threshold_km:
                                near_count += 1
                                break
                    
                    if near_count >= len(sample_points) * 0.5:  # 50% near river
                        parallels.append({
                            'river': r['name'],
                            'proximity_km': threshold_km
                        })
            except:
                pass
        
        return parallels
    
    def find_nearest_settlement(self, park_id, lat, lon):
        """Find nearest settlement to fire origin"""
        settlements = self.park_settlements.get(park_id, [])
        nearest = None
        min_dist = float('inf')
        
        for s in settlements:
            dist = self._haversine(lat, lon, s['lat'], s['lon'])
            if dist < min_dist:
                min_dist = dist
                nearest = {
                    'name': s['name'],
                    'distance_km': round(dist, 1),
                    'population': s['pop'],
                    'classification': s['class']
                }
        
        return nearest if min_dist < 50 else None
    
    def classify_trajectory(self, traj_data, park_id, coords, climate_ctx, analysis_data=None):
        """Enhanced classification using climate and context data"""
        props = traj_data
        
        # Get analysis data if available
        avg_speed = 0
        avg_spread = 0
        net_south = 0
        
        if analysis_data:
            avg_speed = analysis_data.get('avg_speed_km_day', 0)
            avg_spread = analysis_data.get('avg_spread_km', 0)
            net_south = analysis_data.get('net_south_km', 0)
        else:
            # Estimate from coordinates
            if len(coords) >= 2:
                days = props.get('days_inside', 1) or 1
                total_dist = sum(
                    self._haversine(coords[i][1], coords[i][0], 
                                   coords[i+1][1], coords[i+1][0])
                    for i in range(len(coords)-1)
                )
                avg_speed = total_dist / days
                
                # Net south movement
                net_south = (coords[0][1] - coords[-1][1]) * 111  # degrees to km
        
        # Find nearby settlement
        origin = coords[0] if coords else [0, 0]
        nearest_settlement = self.find_nearest_settlement(
            park_id, origin[1], origin[0]
        )
        
        # Climate affects classification
        season = climate_ctx.get('season', 'unknown')
        is_peak_dry = climate_ctx.get('is_peak_dry', False)
        climate_zone = climate_ctx.get('climate_zone', '')
        
        # Classification logic
        classification = 'local_burning'
        confidence = 0.5
        
        # Settlement proximity affects classification
        if nearest_settlement:
            dist = nearest_settlement['distance_km']
            sett_class = nearest_settlement.get('classification', '')
            
            if dist < 3:
                if sett_class == 'agricultural':
                    classification = 'slash_burn_agriculture'
                    confidence = 0.8
                elif sett_class == 'pastoral':
                    classification = 'herder_pastoral'
                    confidence = 0.7
                else:
                    classification = 'village_domestic'
                    confidence = 0.6
            elif dist < 10 and avg_speed > 5:
                classification = 'herder_local'
                confidence = 0.6
        
        # Speed and movement patterns
        if avg_speed > 30:
            classification = 'management_fast'
            confidence = 0.7
        elif avg_speed > 15:
            if avg_spread > 30:
                classification = 'management_controlled'
                confidence = 0.7
            else:
                classification = 'herder_fast'
                confidence = 0.6
        elif avg_speed > 5:
            if net_south > 20:
                classification = 'transhumance'
                confidence = 0.7
            elif not nearest_settlement or nearest_settlement['distance_km'] > 15:
                classification = 'herder_local'
                confidence = 0.6
        elif avg_speed < 2:
            fires = props.get('fires_inside', 0)
            days = props.get('days_inside', 1) or 1
            if fires / days > 20:
                classification = 'persistent_local'
                confidence = 0.6
            else:
                classification = 'local_stationary'
                confidence = 0.5
        
        # Adjust confidence based on season
        if season == 'dry' and classification in ['herder_local', 'transhumance', 'herder_pastoral']:
            confidence = min(confidence + 0.1, 0.9)  # More likely during dry season
        
        if is_peak_dry and classification == 'management_controlled':
            confidence = min(confidence + 0.1, 0.9)  # Management burns often in peak dry
        
        return classification, confidence, nearest_settlement
    
    def generate_narrative(self, park_id, traj, classification, climate_ctx,
                          river_crossings, river_parallels, origin_river, dest_river,
                          nearest_settlement):
        """Generate rich narrative with climate context"""
        parts = []
        
        # Classification description
        class_desc = {
            'transhumance': 'Transhumance corridor fire',
            'herder_local': 'Local herder fires',
            'herder_fast': 'Fast-moving herder fires',
            'herder_pastoral': 'Pastoral grazing fires',
            'management_controlled': 'Controlled management burn',
            'management_fast': 'Rapid management burn',
            'slash_burn_agriculture': 'Slash-and-burn agriculture',
            'village_domestic': 'Village-origin fire',
            'local_burning': 'Local burning activity',
            'local_stationary': 'Stationary local fire',
            'persistent_local': 'Persistent local burning'
        }
        parts.append(class_desc.get(classification, classification.replace('_', ' ').title()))
        
        # Timing with climate context
        start = traj.get('start_date', '')
        days = traj.get('days_inside', 0)
        
        season = climate_ctx.get('season', 'unknown')
        month_precip = climate_ctx.get('month_precip_mm')
        is_peak_dry = climate_ctx.get('is_peak_dry', False)
        climate_zone = climate_ctx.get('climate_zone', '')
        
        # Build timing description
        timing = f"starting {start}"
        if days:
            timing += f" ({days} day{'s' if days != 1 else ''})"
        
        # Add climate context
        if season == 'dry':
            if is_peak_dry:
                timing += f" during peak dry season"
            else:
                timing += f" during dry season"
        elif season == 'wet':
            timing += f" during wet season"
        elif season == 'transition':
            timing += f" during seasonal transition"
        
        if month_precip is not None:
            if month_precip < 20:
                timing += f" ({month_precip:.0f}mm rainfall)"
            elif month_precip > 150:
                timing += f" (high rainfall: {month_precip:.0f}mm)"
        
        parts.append(timing)
        
        # Add climate zone context for unusual fires
        if climate_zone and season == 'wet' and classification not in ['management_controlled', 'slash_burn_agriculture']:
            parts.append(f"in {climate_zone} zone")
        
        # River context
        if origin_river:
            parts.append(f"originating {origin_river['distance_km']}km from {origin_river['name']}")
        
        if river_crossings:
            rivers = list(set(c['river'] for c in river_crossings))
            if len(rivers) == 1:
                parts.append(f"crossing {rivers[0]}")
            else:
                parts.append(f"crossing {', '.join(rivers[:2])}")
        
        if river_parallels and not river_crossings:
            parts.append(f"moving parallel to {river_parallels[0]['river']}")
        
        if dest_river and dest_river != origin_river:
            parts.append(f"ending near {dest_river['name']}")
        
        # Settlement context
        if nearest_settlement:
            parts.append(f"near {nearest_settlement['name']} ({nearest_settlement['distance_km']}km)")
        
        # Build narrative
        narrative = parts[0]
        if len(parts) > 1:
            narrative += ' ' + ' '.join(parts[1:])
        narrative += '.'
        
        return narrative
    
    def process_park(self, park_id):
        """Process all trajectories for a park"""
        # Get trajectories from feature_geometries
        cursor = self.conn.execute('''
            SELECT feature_id, geojson, properties_json, start_date, end_date
            FROM feature_geometries
            WHERE park_id = ? AND feature_type = 'fire_trajectory'
            ORDER BY start_date
        ''', (park_id,))
        
        # Get analysis data if available
        analysis_cursor = self.conn.execute('''
            SELECT year, analysis_json FROM park_fire_analysis
            WHERE park_id = ?
        ''', (park_id,))
        
        analysis_by_year = {}
        for row in analysis_cursor:
            if row[1]:
                try:
                    analysis_by_year[row[0]] = json.loads(row[1])
                except:
                    pass
        
        trajectories = []
        
        for row in cursor:
            try:
                geom = json.loads(row['geojson'])
                props = json.loads(row['properties_json']) if row['properties_json'] else {}
            except:
                continue
            
            coords = geom.get('coordinates', [])
            if len(coords) < 2:
                continue
            
            year = props.get('year')
            group_num = props.get('group_num')
            
            # Get date for climate context
            fire_date = row['start_date'] or props.get('entry_date', '')
            climate_ctx = self.get_climate_context(park_id, fire_date)
            
            # Find matching analysis data
            analysis_data = None
            if year in analysis_by_year:
                year_data = analysis_by_year[year]
                for group_type in ['herder_local', 'herder_fast', 'transhumance', 
                                  'transhumance_slow', 'management_fast', 
                                  'management_controlled', 'local']:
                    groups = year_data.get(group_type, [])
                    if isinstance(groups, list):
                        for g in groups:
                            if isinstance(g, dict) and g.get('group_num') == group_num:
                                analysis_data = g
                                break
                    if analysis_data:
                        break
            
            # River analysis
            origin = coords[0]
            dest = coords[-1]
            origin_river = self.find_nearby_river(park_id, origin[1], origin[0])
            dest_river = self.find_nearby_river(park_id, dest[1], dest[0])
            river_crossings = self.find_river_crossings(park_id, coords)
            river_parallels = self.find_river_parallels(park_id, coords)
            
            # Classification with climate
            classification, confidence, nearest_settlement = self.classify_trajectory(
                props, park_id, coords, climate_ctx, analysis_data
            )
            
            # Calculate trajectory metrics
            total_dist = sum(
                self._haversine(coords[i][1], coords[i][0], 
                               coords[i+1][1], coords[i+1][0])
                for i in range(len(coords)-1)
            )
            
            # Direction
            if len(coords) >= 2:
                bearing = self._bearing(origin[1], origin[0], dest[1], dest[0])
                direction = self._bearing_to_direction(bearing)
            else:
                bearing, direction = 0, 'unknown'
            
            # Generate narrative
            narrative = self.generate_narrative(
                park_id, props, classification, climate_ctx,
                river_crossings, river_parallels, origin_river, dest_river,
                nearest_settlement
            )
            
            trajectories.append({
                'feature_id': row['feature_id'],
                'year': year,
                'group_num': group_num,
                'start_date': fire_date,
                'end_date': row['end_date'] or props.get('last_inside'),
                'outcome': props.get('outcome'),
                'fires_inside': props.get('fires_inside'),
                'days_inside': props.get('days_inside'),
                'classification': classification,
                'confidence': round(confidence, 2),
                'climate': {
                    'season': climate_ctx['season'],
                    'month_precip_mm': climate_ctx['month_precip_mm'],
                    'climate_zone': climate_ctx['climate_zone'],
                    'is_peak_dry': climate_ctx['is_peak_dry']
                },
                'total_distance_km': round(total_dist, 1),
                'direction': direction,
                'bearing': round(bearing),
                'origin_river': origin_river,
                'dest_river': dest_river,
                'river_crossings': river_crossings,
                'river_parallels': river_parallels,
                'nearest_settlement': nearest_settlement,
                'narrative': narrative,
                'coordinates': coords
            })
        
        return trajectories
    
    def _haversine(self, lat1, lon1, lat2, lon2):
        """Calculate distance between two points in km"""
        R = 6371
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        return 2 * R * math.asin(math.sqrt(a))
    
    def _bearing(self, lat1, lon1, lat2, lon2):
        """Calculate bearing between two points"""
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlon = lon2 - lon1
        x = math.sin(dlon) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360) % 360
    
    def _bearing_to_direction(self, bearing):
        """Convert bearing to compass direction"""
        dirs = ['north', 'northeast', 'east', 'southeast', 
                'south', 'southwest', 'west', 'northwest']
        idx = round(bearing / 45) % 8
        return dirs[idx]
    
    def _segment_intersects_line(self, x1, y1, x2, y2, line_coords):
        """Check if a segment intersects a polyline"""
        for i in range(len(line_coords) - 1):
            x3, y3 = line_coords[i]
            x4, y4 = line_coords[i + 1]
            if self._segments_intersect(x1, y1, x2, y2, x3, y3, x4, y4):
                return True
        return False
    
    def _segments_intersect(self, x1, y1, x2, y2, x3, y3, x4, y4):
        """Check if two line segments intersect"""
        def ccw(A, B, C):
            return (C[1]-A[1]) * (B[0]-A[0]) > (B[1]-A[1]) * (C[0]-A[0])
        
        A, B, C, D = (x1,y1), (x2,y2), (x3,y3), (x4,y4)
        return ccw(A,C,D) != ccw(B,C,D) and ccw(A,B,C) != ccw(A,B,D)
    
    def run(self):
        """Process all parks"""
        OUTPUT_DIR.mkdir(exist_ok=True)
        
        # Get parks with trajectories
        cursor = self.conn.execute('''
            SELECT DISTINCT park_id FROM park_fire_analysis
            
            ORDER BY park_id
        ''')
        parks = [row[0] for row in cursor]
        
        print()
        print("Enhanced Fire Trajectory Analysis v2")
        print("=" * 50)
        print(f"Processing {len(parks)} parks with climate and river context...")
        print()
        
        total_trajectories = 0
        classifications = defaultdict(int)
        
        for i, park_id in enumerate(parks, 1):
            trajectories = self.process_park(park_id)
            
            if trajectories:
                # Save to JSON
                output_file = OUTPUT_DIR / f"{park_id}.json"
                with open(output_file, 'w') as f:
                    json.dump(trajectories, f, indent=2)
                
                for t in trajectories:
                    classifications[t['classification']] += 1
                
                total_trajectories += len(trajectories)
            
            print(f"[{i}/{len(parks)}] {park_id}... {len(trajectories)} trajectories")
        
        print()
        print("=" * 50)
        print(f"Exported {total_trajectories} trajectories")
        print()
        print("Classification breakdown:")
        for cls, count in sorted(classifications.items(), key=lambda x: -x[1]):
            print(f"  {cls}: {count}")

if __name__ == '__main__':
    analyzer = EnhancedTrajectoryAnalyzer()
    analyzer.run()

# Patch: Add timestamps to coordinates
def add_timestamps_to_coords(coords, start_date, end_date):
    """Add timestamps to coordinate array"""
    if not coords or len(coords) < 2:
        return coords
    
    try:
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
    except:
        return coords
    
    n = len(coords)
    if n == 1:
        return [{'lon': coords[0][0], 'lat': coords[0][1], 'timestamp': f"{start_date}T12:00:00Z"}]
    
    # Distribute timestamps evenly, alternating AM/PM
    total_hours = (end - start).total_seconds() / 3600
    result = []
    
    for i, coord in enumerate(coords):
        # Calculate time offset
        progress = i / (n - 1) if n > 1 else 0
        offset_hours = progress * total_hours
        dt = start + timedelta(hours=offset_hours)
        
        # Alternate between 6am and 6pm for better Google Earth visualization
        if i % 2 == 0:
            hour = 6
        else:
            hour = 18
        dt = dt.replace(hour=hour, minute=0, second=0)
        
        result.append({
            'lon': coord[0],
            'lat': coord[1],
            'timestamp': dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        })
    
    return result

# Monkey-patch the process_park method
_original_process_park = EnhancedTrajectoryAnalyzer.process_park

def patched_process_park(self, park_id):
    trajectories = _original_process_park(self, park_id)
    for t in trajectories:
        if 'coordinates' in t and t.get('start_date') and t.get('end_date'):
            t['coordinates_with_time'] = add_timestamps_to_coords(
                t['coordinates'], 
                t['start_date'], 
                t['end_date']
            )
    return trajectories

EnhancedTrajectoryAnalyzer.process_park = patched_process_park
