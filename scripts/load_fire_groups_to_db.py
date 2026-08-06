#!/usr/bin/env python3
"""
Step 3: Load Fire Groups to Database (v3)

Loads fire groups from rebuild_park_fire_analysis_v3.py output into:
1. feature_geometries - GeoJSON LineStrings with properties
2. park_group_infractions - yearly stats per park
3. park_fire_weekly - weekly fire counts

Adds context from:
- park_rivers_hydro (HydroRIVERS)
- park_lakes_hydro (HydroLAKES)  
- roads in feature_geometries
- osm_places
- park_settlements
- deforestation_events
- park_climate

Classifies trajectory position:
- starts_inside: First point inside park
- ends_inside: Last point inside park (stopped)
- transits: Passes through without stopping
- entirely_outside: Never enters park

Usage:
    python scripts/load_fire_groups_v3.py [--park PARK_ID] [--force]

DB Changes for production migration:
    -- No schema changes required, uses existing tables:
    -- feature_geometries (feature_type='fire_trajectory')
    -- park_group_infractions (park_id, year stats)
    -- park_fire_weekly (park_id, week_start, fire_count)
"""

import json
import argparse
import sqlite3
import math
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
INPUT_DIR = BASE_DIR / "data" / "fire_groups_v5"  # Updated for v5 trajectory output

# Groups are 'relevant' to a park (counted in stats, eligible for notifications)
# if any part of the trajectory is inside or within RELEVANCE_KM of the boundary.
# The FULL trajectory geometry is always stored and displayed - never clipped:
# a transect that reaches the park shows its entire path from wherever it started.
RELEVANCE_KM = 20
TRENDS_DIR = BASE_DIR / "data" / "fire_trends"
KEYSTONES_FILE = BASE_DIR / "data" / "keystones_with_boundaries.json"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def haversine(lon1, lat1, lon2, lat2):
    """Distance in km between two points"""
    R = 6371
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def point_in_polygon(lon, lat, geometry):
    """Ray casting algorithm for point-in-polygon"""
    coords = geometry.get('coordinates', [])
    if geometry['type'] == 'MultiPolygon':
        for poly in coords:
            if _point_in_ring(lon, lat, poly[0]):
                return True
        return False
    elif geometry['type'] == 'Polygon':
        return _point_in_ring(lon, lat, coords[0])
    return False

def _point_in_ring(lon, lat, ring):
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


class FireGroupLoader:
    def __init__(self, aoi_id=None):
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        self.parks = self._load_parks()
        # An AOI is injected into the in-memory parks dict only; the keystones
        # FILE is never touched, because that is what park_assigner reads
        # (docs/PLAN_AOI_OVERLAY.md §3). Its feature_geometries rows carry
        # park_id = <aoi_id>, an id space the park routes 404 on.
        self.aoi_id = aoi_id
        if aoi_id:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent))
            import aoi_lib
            aoi_lib.inject_aoi(self.parks, aoi_id)
        
        # Context data (loaded lazily per park)
        self.climate = {}
        self.park_rivers = defaultdict(list)
        self.park_lakes = defaultdict(list)
        self.park_roads = defaultdict(list)
        self.park_places = defaultdict(list)
        self.park_settlements = defaultdict(list)
        self.park_deforestation = defaultdict(list)
        
        self._load_climate()
    
    def _load_parks(self):
        """Load park geometries"""
        with open(KEYSTONES_FILE) as f:
            data = json.load(f)
        parks = {}
        for p in data:
            park_id = p.get('id')
            if not park_id or 'geometry' not in p:
                continue
            parks[park_id] = {
                'id': park_id, 
                'name': p.get('name', park_id),
                'country': p.get('country', ''),
                'geometry': p['geometry']
            }
        return parks
    
    def _load_climate(self):
        """Load climate data for all parks"""
        climate_file = BASE_DIR / 'data' / 'climate' / 'park_climate.json'
        if climate_file.exists():
            with open(climate_file) as f:
                self.climate = json.load(f)
        log(f"  Climate: {len(self.climate)} parks")
    
    def _load_park_context(self, park_id):
        """Load context data for a specific park (lazy loading)"""
        if park_id in self.park_rivers:
            return  # Already loaded
        
        # Rivers from park_rivers_hydro
        cursor = self.conn.execute('''
            SELECT hyriv_id, name, stream_order, length_km,
                   json_extract(geojson, '$.coordinates[0][0]') as lon,
                   json_extract(geojson, '$.coordinates[0][1]') as lat
            FROM park_rivers_hydro WHERE park_id = ? AND name IS NOT NULL
        ''', (park_id,))
        for row in cursor:
            try:
                self.park_rivers[park_id].append({
                    'name': row['name'], 'order': row['stream_order'],
                    'lat': float(row['lat']) if row['lat'] else None,
                    'lon': float(row['lon']) if row['lon'] else None
                })
            except: pass
        
        # Lakes from park_lakes_hydro
        cursor = self.conn.execute('''
            SELECT name, area_km2, centroid_lat, centroid_lon
            FROM park_lakes_hydro WHERE park_id = ? AND name IS NOT NULL
        ''', (park_id,))
        for row in cursor:
            try:
                self.park_lakes[park_id].append({
                    'name': row['name'], 'area_km2': row['area_km2'],
                    'lat': row['centroid_lat'], 'lon': row['centroid_lon']
                })
            except: pass
        
        # Roads from feature_geometries
        cursor = self.conn.execute('''
            SELECT properties_json,
                   (bbox_minx + bbox_maxx) / 2 as lon,
                   (bbox_miny + bbox_maxy) / 2 as lat
            FROM feature_geometries WHERE park_id = ? AND feature_type = 'road'
        ''', (park_id,))
        for row in cursor:
            try:
                props = json.loads(row['properties_json']) if row['properties_json'] else {}
                self.park_roads[park_id].append({
                    'surface': props.get('surface', 'unknown'),
                    'lat': row['lat'], 'lon': row['lon']
                })
            except: pass
        
        # OSM places
        cursor = self.conn.execute('''
            SELECT name, place_type, lat, lon FROM osm_places WHERE park_id = ?
        ''', (park_id,))
        for row in cursor:
            self.park_places[park_id].append({
                'name': row['name'], 'type': row['place_type'],
                'lat': row['lat'], 'lon': row['lon']
            })
        
        # Settlements
        cursor = self.conn.execute('''
            SELECT id, lat, lon, classification, fires_1km
            FROM park_settlements WHERE park_id = ?
        ''', (park_id,))
        for row in cursor:
            self.park_settlements[park_id].append({
                'id': row['id'], 'type': row['classification'],
                'fires': row['fires_1km'] or 0,
                'lat': row['lat'], 'lon': row['lon']
            })
        
        # Deforestation events
        cursor = self.conn.execute('''
            SELECT id, lat, lon, year, classification, fires_same_year
            FROM deforestation_events WHERE park_id = ?
        ''', (park_id,))
        for row in cursor:
            self.park_deforestation[park_id].append({
                'id': row['id'], 'year': row['year'],
                'type': row['classification'],
                'fire_correlated': (row['fires_same_year'] or 0) > 0,
                'lat': row['lat'], 'lon': row['lon']
            })

    def find_nearest(self, items, lat, lon, max_dist_km=30):
        """Find nearest item from list with lat/lon"""
        best = None
        best_dist = max_dist_km
        for item in items:
            if item.get('lat') is None or item.get('lon') is None:
                continue
            dist = haversine(lon, lat, item['lon'], item['lat'])
            if dist < best_dist:
                best_dist = dist
                best = {**item, 'distance_km': round(dist, 1)}
        return best
    
    def get_season(self, park_id, date_str):
        """Get season for date based on park climate"""
        if park_id not in self.climate:
            return 'unknown'
        try:
            month = int(date_str[5:7])
            seasons = self.climate[park_id].get('seasons', {})
            dry_months = seasons.get('dry_months', [])
            wet_months = seasons.get('wet_months', [])
            if month in dry_months:
                return 'dry'
            elif month in wet_months:
                return 'wet'
            return 'transition'
        except:
            return 'unknown'
    
    def classify_trajectory_position(self, group, park_geometry):
        """
        Classify trajectory based on park boundary relationship:
        - starts_inside: First observation inside park
        - ends_inside: Last observation inside park (fire stopped there)
        - transits: Passed through park without stopping
        - entirely_outside: Never entered park boundary
        """
        trajectory = group.get('trajectory', [])
        if not trajectory:
            return 'unknown'
        
        # Check each point
        points_inside = []
        for i, pt in enumerate(trajectory):
            lon, lat = pt[0], pt[1]
            inside = point_in_polygon(lon, lat, park_geometry)
            points_inside.append(inside)
        
        first_inside = points_inside[0] if points_inside else False
        last_inside = points_inside[-1] if points_inside else False
        any_inside = any(points_inside)
        
        if not any_inside:
            return 'entirely_outside'
        elif first_inside and last_inside:
            return 'contained'  # Started and ended inside
        elif first_inside:
            return 'started_inside'  # Started inside, moved out
        elif last_inside:
            return 'ends_inside'  # Entered and stopped inside
        else:
            return 'transits'  # Passed through
    
    def build_context(self, group, park_id):
        """Build context dict for narrative generation"""
        self._load_park_context(park_id)
        
        centroid = group.get('centroid', [0, 0])
        lon, lat = centroid[0], centroid[1]
        start_date = group.get('start_date', '')
        year = group.get('year', 2024)
        
        context = {
            'nearest_river': self.find_nearest(self.park_rivers[park_id], lat, lon),
            'nearest_lake': self.find_nearest(self.park_lakes[park_id], lat, lon),
            'nearest_road': self.find_nearest(self.park_roads[park_id], lat, lon, 15),
            'nearest_place': self.find_nearest(self.park_places[park_id], lat, lon, 50),
            'nearest_settlement': self.find_nearest(self.park_settlements[park_id], lat, lon, 20),
            'season': self.get_season(park_id, start_date)
        }
        
        # Find nearby deforestation (same year or year before)
        nearby_deforest = []
        for d in self.park_deforestation[park_id]:
            if d['year'] in [year, year - 1]:
                dist = haversine(lon, lat, d['lon'], d['lat'])
                if dist < 10:  # 10km radius
                    nearby_deforest.append({**d, 'distance_km': round(dist, 1)})
        context['nearby_deforestation'] = nearby_deforest[:3]  # Top 3
        
        return context
    
    def generate_narrative(self, group, context, position):
        """Generate human-readable narrative for fire group"""
        parts = []
        
        group_type = group.get('group_type', 'unknown')
        days = group.get('days', 1)
        distance = group.get('distance_km', 0)
        direction = group.get('direction', '')
        start_date = group.get('start_date', '')
        end_date = group.get('end_date', '')
        
        # Type description
        type_desc = {
            'management_controlled': 'Controlled burn',
            'herder_local': 'Local herder fire activity',
            'transhumance': 'Transhumance fire pattern',
            'transhumance_fast': 'Rapid transhumance movement',
            'external_fire': 'External fire approaching park',
            'spot_fire': 'Isolated fire event',
            'spreading_fire': 'Multi-day spreading fire',
            'local_fire': 'Localized fire activity',
            'wildfire': 'Wildfire activity'
        }.get(group_type, 'Fire activity')
        
        # Position description. With the 100km ingest buffer, "outside"
        # spans 0-100km - always say how far so 2km and 90km read differently.
        dist_to_park = group.get('dist_to_park_km')
        pos_desc = {
            'starts_inside': 'originated inside park',
            'ends_inside': 'entered and stopped inside park',
            'transits': 'passed through park',
            'entirely_outside': 'detected outside park boundary',
            'contained': 'contained within park'
        }.get(position, '')
        if position == 'entirely_outside' and dist_to_park:
            if dist_to_park >= 1:
                pos_desc = f"detected ~{dist_to_park:.0f}km outside park boundary"
            else:
                pos_desc = "detected just outside park boundary"
        
        # Main sentence
        if days == 1:
            parts.append(f"{type_desc} detected {start_date}")
        else:
            parts.append(f"{type_desc} {start_date} to {end_date} ({days} days)")
        
        if pos_desc:
            parts.append(pos_desc)
        
        # Movement
        if distance > 0:
            parts.append(f"Traveled {distance:.1f}km {direction}")
        
        # Context
        if context.get('nearest_place'):
            p = context['nearest_place']
            parts.append(f"near {p['name']} ({p['distance_km']}km)")
        
        if context.get('nearest_river'):
            r = context['nearest_river']
            parts.append(f"Near {r['name']} river")
        
        if context.get('nearest_road') and context['nearest_road']['distance_km'] < 5:
            r = context['nearest_road']
            surface = r.get('surface', 'unknown')
            if surface != 'unknown':
                parts.append(f"Near {surface} road")
            else:
                parts.append("Near road")
        
        if context.get('nearby_deforestation'):
            parts.append("Deforestation activity nearby")
        
        # Season
        season = context.get('season', '')
        if season in ['dry', 'wet']:
            parts.append(f"({season} season)")
        
        return '. '.join(parts) + '.'

    def process_park(self, park_id, force=False):
        """Process and load fire groups for one park"""
        if park_id not in self.parks:
            return 0, {}
        
        input_file = INPUT_DIR / f"{park_id}.json"
        if not input_file.exists():
            return 0, {}
        
        with open(input_file) as f:
            groups = json.load(f)
        
        if not groups:
            # A park whose rebuild legitimately produced zero groups (too few
            # fires to seed a cluster) must still have its old rows dropped,
            # or they linger forever as stale_in_db drift and the map keeps
            # serving trajectories the builder no longer believes in.
            # (TZA_Rungwa: 45 fires -> 0 groups, 111 rows left behind.)
            self.conn.execute(
                "DELETE FROM feature_geometries WHERE park_id = ? AND feature_type = 'fire_trajectory'",
                (park_id,)
            )
            self.conn.commit()
            return 0, {}
        
        park_geometry = self.parks[park_id]['geometry']
        
        # Delete existing trajectories for this park
        self.conn.execute(
            "DELETE FROM feature_geometries WHERE park_id = ? AND feature_type = 'fire_trajectory'",
            (park_id,)
        )
        
        # Stats collectors
        yearly_stats = defaultdict(lambda: {
            'total_groups': 0, 'starts_inside': 0, 'ends_inside': 0,
            'transits': 0, 'entirely_outside': 0, 'contained': 0,
            'started_inside': 0, 'unknown': 0, 'total_days': 0
        })
        weekly_counts = defaultdict(int)
        
        count = 0
        for group in groups:
            trajectory = group.get('trajectory', [])
            if len(trajectory) < 1:
                continue
            
            # Incremental mode: skip old groups
            if self.cutoff_date and group.get('end_date', '9999-99-99') < self.cutoff_date:
                continue
            
            # Skip groups with invalid coordinates (lon=0 or lat=0)
            centroid = group.get('centroid', [0, 0])
            if centroid[0] == 0.0 or centroid[1] == 0.0:
                continue
            if trajectory[0][0] == 0.0 or trajectory[0][1] == 0.0:
                continue
            
            # Build GeoJSON
            if len(trajectory) == 1:
                # Single point - make a Point
                geojson = json.dumps({
                    "type": "Point",
                    "coordinates": [trajectory[0][0], trajectory[0][1]]
                })
                coords = [[trajectory[0][0], trajectory[0][1]]]
            else:
                # LineString
                coords = [[pt[0], pt[1]] for pt in trajectory]
                geojson = json.dumps({
                    "type": "LineString",
                    "coordinates": coords
                })
            
            # Classify position relative to park
            position = self.classify_trajectory_position(group, park_geometry)
            
            # Build context and narrative
            context = self.build_context(group, park_id)
            narrative = self.generate_narrative(group, context, position)
            
            # Build properties
            feature_id = group.get('feature_id', f"{park_id}_grp_{count}")
            props = {
                "feature_id": feature_id,
                "feature_type": "fire_trajectory",
                "group_type": group.get('group_type', 'unknown'),
                "position": position,
                "days": group.get('days', 1),
                "fires_total": group.get('fire_count', 0),
                "direction": group.get('direction', ''),
                "distance_km": round(group.get('distance_km', 0), 1),
                "avg_speed_km_day": round(group.get('speed_km_day', 0), 1),
                "total_frp": round(group.get('total_frp', 0), 1),
                "pct_inside": group.get('pct_inside', 0),
                "dist_to_park_km": group.get('dist_to_park_km'),
                "cross_border": group.get('cross_border', False),
                "affected_parks": group.get('affected_parks', [park_id]),
                "narrative": narrative,
                # Flat context fields for precompute_narratives_v4 compatibility
                "season": context.get('season', ''),
                "nearest_place": (context.get('nearest_place') or {}).get('name', ''),
                "nearest_river": (context.get('nearest_river') or {}).get('name', ''),
                "nearest_place_dist": (context.get('nearest_place') or {}).get('distance_km'),
                "nearest_river_dist": (context.get('nearest_river') or {}).get('distance_km'),
                # V5 fields
                "trajectory_type": group.get('trajectory_type', 'unknown'),
                "zigzag_ratio": group.get('zigzag_ratio', 0),
                "year": group.get('year', 2024),
            }
            
            # Date range
            start_date = group.get('start_date', '')
            end_date = group.get('end_date', '')
            year = group.get('year', 2024)
            
            # Calculate bbox
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            
            # Distance to park boundary (0 = inside). Fallback: 0 for groups
            # partially inside; unknown (None) treated as relevant.
            dist_to_park = group.get('dist_to_park_km')
            if dist_to_park is None and group.get('pct_inside', 0) > 0:
                dist_to_park = 0.0
            
            # Insert into feature_geometries (OR REPLACE for duplicates)
            self.conn.execute("""
                INSERT OR REPLACE INTO feature_geometries 
                (feature_type, feature_id, park_id, geojson, 
                 bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                 start_date, end_date, properties_json, dist_to_park_km)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                'fire_trajectory', feature_id, park_id, geojson,
                min(lons), min(lats), max(lons), max(lats),
                start_date, end_date, json.dumps(props), dist_to_park
            ))
            
            # Per-park stats: only count groups relevant to the park
            # (inside, or within 20km of the boundary). Groups further out
            # are kept in feature_geometries for map display only.
            relevant = group.get('pct_inside', 0) > 0 or (dist_to_park is not None and dist_to_park <= RELEVANCE_KM)
            if relevant:
                yearly_stats[year]['total_groups'] += 1
                yearly_stats[year][position] += 1
                yearly_stats[year]['total_days'] += group.get('days', 1)
                
                # Weekly count (by start date)
                if start_date:
                    try:
                        dt = datetime.strptime(start_date, '%Y-%m-%d')
                        week_start = (dt - timedelta(days=dt.weekday())).strftime('%Y-%m-%d')
                        weekly_counts[week_start] += group.get('fire_count', 1)
                    except:
                        pass
            
            count += 1
        
        return count, {'yearly': dict(yearly_stats), 'weekly': dict(weekly_counts)}

    def update_park_stats(self, park_id, stats):
        """Update park_group_infractions and park_fire_weekly tables"""
        yearly = stats.get('yearly', {})
        weekly = stats.get('weekly', {})
        
        # Update park_group_infractions (yearly stats)
        for year, ystats in yearly.items():
            total = ystats['total_groups']
            stopped = ystats['ends_inside'] + ystats.get('contained', 0)
            transited = ystats['transits']
            avg_days = ystats['total_days'] / total if total > 0 else 0
            
            self.conn.execute("""
                INSERT OR REPLACE INTO park_group_infractions
                (park_id, year, total_groups, groups_stopped_inside, groups_transited, avg_days_burning, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (park_id, year, total, stopped, transited, round(avg_days, 2), 
                  datetime.now().isoformat()))
        
        # Update park_fire_weekly
        for week_start, fire_count in weekly.items():
            self.conn.execute("""
                INSERT OR REPLACE INTO park_fire_weekly (park_id, week_start, fire_count)
                VALUES (?, ?, ?)
            """, (park_id, week_start, fire_count))
    
    def run(self, park_id=None, force=False, incremental=False, days=60):
        """Run the loader"""
        log("Loading fire groups into database...")
        log(f"Input: {INPUT_DIR}")
        
        if incremental:
            from datetime import datetime, timedelta
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            log(f"INCREMENTAL MODE: Only processing groups since {cutoff_date}")
            self.cutoff_date = cutoff_date
        else:
            self.cutoff_date = None
        
        if park_id:
            park_ids = [park_id]
        elif self.aoi_id:
            park_ids = [self.aoi_id]
        else:
            # AOI outputs live in the same directory but must not be swept up
            # by a full park run.
            park_ids = sorted([f.stem for f in INPUT_DIR.glob("*.json")
                               if f.stem in self.parks])
        
        log(f"Processing {len(park_ids)} parks...")
        
        total_loaded = 0
        total_yearly = defaultdict(lambda: {'groups': 0, 'stopped': 0, 'transited': 0})
        
        for i, pid in enumerate(park_ids):
            count, stats = self.process_park(pid, force)
            if count > 0:
                self.update_park_stats(pid, stats)
                total_loaded += count
                
                # Aggregate yearly stats
                for year, ystats in stats.get('yearly', {}).items():
                    total_yearly[year]['groups'] += ystats['total_groups']
                    total_yearly[year]['stopped'] += ystats['ends_inside'] + ystats.get('contained', 0)
                    total_yearly[year]['transited'] += ystats['transits']
                
                if (i + 1) % 10 == 0:
                    log(f"  [{i+1}/{len(park_ids)}] {pid}: {count} groups")
                    self.conn.commit()
        
        self.conn.commit()
        self.conn.close()
        
        log(f"\nTotal: {total_loaded} fire trajectories loaded")
        log("\nYearly summary:")
        for year in sorted(total_yearly.keys()):
            ys = total_yearly[year]
            log(f"  {year}: {ys['groups']} groups, {ys['stopped']} stopped inside, {ys['transited']} transited")


def main():
    parser = argparse.ArgumentParser(description="Load fire groups to database")
    parser.add_argument('--park', help='Process specific park only')
    parser.add_argument('--force', action='store_true', help='Force reload all')
    parser.add_argument('--aoi', help='Load an AOI overlay instead of parks '
                                      '(geometry from the aois table)')
    parser.add_argument('--incremental', action='store_true', help='Incremental mode: only updated parks')
    parser.add_argument('--days', type=int, default=60, help='Days window for incremental (default: 60)')
    args = parser.parse_args()
    
    loader = FireGroupLoader(aoi_id=args.aoi)
    loader.run(args.park, args.force, args.incremental, args.days)


if __name__ == '__main__':
    main()
