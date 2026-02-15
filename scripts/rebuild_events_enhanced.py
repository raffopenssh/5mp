#!/usr/bin/env python3
"""
Rebuild deforestation_events and park_settlements from polygon data.
Enhanced version with road proximity detection for linear clearing patterns.

Uses:
- HydroRIVERS (park_rivers_hydro) for river context
- HydroLAKES (park_lakes_hydro) for lake context  
- OSM places (osm_places) for settlement context
- HeiGIT roads (roads_heigit) for road proximity / linear patterns
- Climate data for seasonality
"""

import json
import sqlite3
import math
from pathlib import Path
from collections import defaultdict
from datetime import datetime

DB_PATH = Path('db.sqlite3')
CLIMATE_FILE = Path('data/climate/park_climate.json')
SETTLEMENT_DIR = Path('data/settlement_events')
DEFOREST_DIR = Path('data/deforestation_events')

# Clustering parameters
SETTLEMENT_CLUSTER_KM = 2.0
DEFORESTATION_CLUSTER_KM = 5.0

def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance in km between two points"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def point_to_line_distance(px, py, x1, y1, x2, y2):
    """Distance from point (px,py) to line segment (x1,y1)-(x2,y2) in degrees"""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.sqrt((px - x1)**2 + (py - y1)**2)
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx*dx + dy*dy)))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.sqrt((px - proj_x)**2 + (py - proj_y)**2)

class EventRebuilder:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self.climate = self._load_climate()
        self.rivers_cache = {}
        self.lakes_cache = {}
        self.places_cache = {}
        self.roads_cache = {}
        
    def _load_climate(self):
        if CLIMATE_FILE.exists():
            with open(CLIMATE_FILE) as f:
                return json.load(f)
        return {}
    
    def _load_park_rivers(self, park_id):
        """Load rivers from park_rivers_hydro"""
        if park_id in self.rivers_cache:
            return self.rivers_cache[park_id]
        
        rivers = []
        cursor = self.conn.execute("""
            SELECT name, stream_order, length_km, geojson
            FROM park_rivers_hydro
            WHERE park_id = ? AND name IS NOT NULL AND name != ''
            ORDER BY stream_order DESC, length_km DESC
            LIMIT 20
        """, (park_id,))
        for row in cursor:
            rivers.append({
                'name': row['name'],
                'order': row['stream_order'],
                'length_km': row['length_km'],
                'geojson': json.loads(row['geojson']) if row['geojson'] else None
            })
        self.rivers_cache[park_id] = rivers
        return rivers
    
    def _load_park_lakes(self, park_id):
        """Load lakes from park_lakes_hydro"""
        if park_id in self.lakes_cache:
            return self.lakes_cache[park_id]
        
        lakes = []
        cursor = self.conn.execute("""
            SELECT name, area_km2, centroid_lat, centroid_lon
            FROM park_lakes_hydro
            WHERE park_id = ? AND name IS NOT NULL AND name != ''
            ORDER BY area_km2 DESC
            LIMIT 10
        """, (park_id,))
        for row in cursor:
            lakes.append({
                'name': row['name'],
                'area_km2': row['area_km2'],
                'lat': row['centroid_lat'],
                'lon': row['centroid_lon']
            })
        self.lakes_cache[park_id] = lakes
        return lakes
    
    def _load_park_places(self, park_id):
        """Load OSM places"""
        if park_id in self.places_cache:
            return self.places_cache[park_id]
        
        places = []
        cursor = self.conn.execute("""
            SELECT name, place_type, lat, lon
            FROM osm_places
            WHERE park_id = ? AND name IS NOT NULL AND name != ''
            ORDER BY CASE place_type 
                WHEN 'city' THEN 1 WHEN 'town' THEN 2 
                WHEN 'village' THEN 3 ELSE 4 END
            LIMIT 100
        """, (park_id,))
        for row in cursor:
            places.append({
                'name': row['name'],
                'type': row['place_type'],
                'lat': row['lat'],
                'lon': row['lon']
            })
        self.places_cache[park_id] = places
        return places
    
    def _load_park_roads(self, park_id):
        """Load road geometries for road proximity detection"""
        if park_id in self.roads_cache:
            return self.roads_cache[park_id]
        
        roads = []
        cursor = self.conn.execute("""
            SELECT osm_id, name, highway_type, geojson
            FROM roads_heigit
            WHERE park_id = ? AND geojson IS NOT NULL
        """, (park_id,))
        for row in cursor:
            geojson = json.loads(row['geojson']) if row['geojson'] else None
            if geojson and geojson.get('type') == 'LineString':
                roads.append({
                    'osm_id': row['osm_id'],
                    'name': row['name'],
                    'type': row['highway_type'],
                    'coords': geojson.get('coordinates', [])
                })
        self.roads_cache[park_id] = roads
        return roads
    
    def _get_nearest_road_distance(self, lat, lon, roads):
        """Get distance to nearest road in km"""
        if not roads:
            return None, None
        
        min_dist_deg = float('inf')
        nearest_road = None
        
        for road in roads:
            coords = road.get('coords', [])
            for i in range(len(coords) - 1):
                x1, y1 = coords[i]
                x2, y2 = coords[i + 1]
                dist = point_to_line_distance(lon, lat, x1, y1, x2, y2)
                if dist < min_dist_deg:
                    min_dist_deg = dist
                    nearest_road = road
        
        # Convert degrees to km (approximate)
        dist_km = min_dist_deg * 111.0 if min_dist_deg < float('inf') else None
        return dist_km, nearest_road
    
    def _check_linear_pattern(self, polygons, roads):
        """Check if deforestation follows a linear pattern along roads"""
        if len(polygons) < 3 or not roads:
            return False, 0, None
        
        # Count how many polygons are within 500m of a road
        near_road_count = 0
        total_near_road_dist = 0
        nearest_road = None
        
        for p in polygons:
            dist, road = self._get_nearest_road_distance(p['lat'], p['lon'], roads)
            if dist is not None and dist < 0.5:  # 500m
                near_road_count += 1
                total_near_road_dist += dist
                if nearest_road is None:
                    nearest_road = road
        
        fraction_near_road = near_road_count / len(polygons)
        avg_dist = total_near_road_dist / near_road_count if near_road_count > 0 else None
        
        # Linear if >60% of polygons within 500m of road
        is_linear = fraction_near_road > 0.6
        
        return is_linear, fraction_near_road, nearest_road
    
    def _get_fire_density(self, park_id, year, lat, lon, radius_km=5):
        """Get fire count near a location for a given year"""
        radius_deg = radius_km / 111.0
        
        cursor = self.conn.execute("""
            SELECT COUNT(*) as cnt
            FROM fire_detections
            WHERE ABS(latitude - ?) < ? AND ABS(longitude - ?) < ?
            AND acq_date LIKE ?
        """, (lat, radius_deg, lon, radius_deg, f"{year}%"))
        
        row = cursor.fetchone()
        return row['cnt'] if row else 0
    
    def _get_nearest_place(self, lat, lon, places):
        """Find nearest place"""
        if not places:
            return None, None
        
        nearest = None
        min_dist = float('inf')
        for p in places:
            dist = haversine(lat, lon, p['lat'], p['lon'])
            if dist < min_dist:
                min_dist = dist
                nearest = p
        return nearest, min_dist
    
    def _get_nearest_river(self, lat, lon, rivers):
        """Find nearest named river"""
        if rivers:
            return rivers[0]  # Already sorted by importance
        return None
    
    def _cluster_polygons(self, polygons, max_dist_km):
        """Cluster nearby polygons together"""
        if not polygons:
            return []
        
        remaining = list(polygons)
        clusters = []
        
        while remaining:
            seed = remaining.pop(0)
            cluster = [seed]
            
            changed = True
            while changed:
                changed = False
                still_remaining = []
                for p in remaining:
                    in_cluster = False
                    for c in cluster:
                        dist = haversine(p['lat'], p['lon'], c['lat'], c['lon'])
                        if dist <= max_dist_km:
                            in_cluster = True
                            break
                    if in_cluster:
                        cluster.append(p)
                        changed = True
                    else:
                        still_remaining.append(p)
                remaining = still_remaining
            
            clusters.append(cluster)
        
        return clusters
    
    def _classify_deforestation(self, polygons, park_id, year, fires_near, roads):
        """Classify deforestation with enhanced road detection"""
        
        total_area = sum(p['area_km2'] for p in polygons)
        num_polygons = len(polygons)
        
        # Calculate spatial spread
        if num_polygons > 1:
            lats = [p['lat'] for p in polygons]
            lons = [p['lon'] for p in polygons]
            spread_km = haversine(min(lats), min(lons), max(lats), max(lons))
        else:
            spread_km = 0
        
        avg_size = total_area / num_polygons if num_polygons > 0 else 0
        fire_ratio = fires_near / max(total_area, 0.01)
        
        # Check for linear road pattern
        is_linear, road_fraction, nearest_road = self._check_linear_pattern(polygons, roads)
        
        # Classification logic
        classification = 'unknown'
        confidence = 0.5
        pattern = 'scattered'
        
        if fire_ratio > 50:
            classification = 'slash_burn'
            confidence = 0.85
            pattern = 'fire_associated'
        elif is_linear and road_fraction > 0.7:
            classification = 'logging'
            confidence = 0.8
            pattern = 'linear_road'
        elif is_linear:
            classification = 'logging'
            confidence = 0.7
            pattern = 'linear'
        elif spread_km > 5 and avg_size < 0.1 and num_polygons > 5:
            classification = 'logging'
            confidence = 0.65
            pattern = 'linear'
        elif avg_size > 0.5 and num_polygons < 3:
            classification = 'encroachment'
            confidence = 0.6
            pattern = 'concentrated'
        elif spread_km < 2 and num_polygons > 3:
            classification = 'encroachment'
            confidence = 0.65
            pattern = 'clustered'
        elif fires_near == 0 and total_area < 0.5:
            classification = 'natural'
            confidence = 0.5
            pattern = 'scattered'
        else:
            classification = 'encroachment'
            confidence = 0.4
            pattern = 'scattered'
        
        return {
            'classification': classification,
            'confidence': confidence,
            'pattern': pattern,
            'total_area_km2': total_area,
            'num_polygons': num_polygons,
            'spread_km': spread_km,
            'avg_polygon_size': avg_size,
            'fires_nearby': fires_near,
            'fire_ratio': fire_ratio,
            'is_linear': is_linear,
            'road_fraction': road_fraction,
            'nearest_road': nearest_road.get('name') if nearest_road else None
        }
    
    def _generate_deforestation_narrative(self, park_name, year, classification, 
                                           nearest_place, nearest_river, climate_data):
        """Generate rich narrative for deforestation event"""
        
        parts = []
        cls = classification['classification']
        pattern = classification['pattern']
        total_area = classification['total_area_km2']
        num_poly = classification['num_polygons']
        
        # Main description
        cls_desc = {
            'slash_burn': 'Slash-and-burn clearing',
            'logging': 'Logging activity',
            'encroachment': 'Forest encroachment',
            'natural': 'Natural forest loss',
            'unknown': 'Forest loss'
        }
        parts.append(f"{cls_desc.get(cls, 'Forest loss')} detected in {year}.")
        parts.append(f"Affected {total_area:.2f} km² across {num_poly} {'patch' if num_poly == 1 else 'patches'}.")
        
        # Pattern description
        if pattern == 'linear_road':
            road_name = classification.get('nearest_road')
            if road_name:
                parts.append(f"Linear pattern following {road_name} road suggests logging access route.")
            else:
                parts.append("Linear pattern along road suggests logging access route.")
        elif pattern == 'linear':
            parts.append("Linear clearing pattern indicates organized logging activity.")
        elif pattern == 'fire_associated':
            parts.append("Strong fire correlation indicates agricultural burning.")
        elif pattern == 'clustered':
            parts.append("Clustered pattern near settlement suggests encroachment.")
        elif pattern == 'concentrated':
            parts.append("Concentrated clearing suggests single development event.")
        
        # Location context
        if nearest_place:
            place, dist = nearest_place
            parts.append(f"Located {dist:.1f}km from {place['name']}.")
        
        if nearest_river:
            parts.append(f"Near {nearest_river['name']} river.")
        
        return ' '.join(parts)
    
    def rebuild_deforestation(self):
        """Rebuild deforestation_events with enhanced classification"""
        
        print("=" * 60)
        print("Rebuilding deforestation events (enhanced)")
        print("=" * 60)
        
        # Get all deforestation polygons
        cursor = self.conn.execute("""
            SELECT park_id, feature_id,
                   json_extract(properties_json, '$.year') as year,
                   json_extract(properties_json, '$.area_km2') as area_km2,
                   json_extract(properties_json, '$.lat') as lat,
                   json_extract(properties_json, '$.lon') as lon
            FROM feature_geometries
            WHERE feature_type = 'deforestation'
            ORDER BY park_id, year
        """)
        
        # Group by park and year
        park_year_polygons = defaultdict(list)
        for row in cursor:
            year = int(row['year']) if row['year'] else 0
            if year == 0:
                continue
            key = (row['park_id'], year)
            park_year_polygons[key].append({
                'feature_id': row['feature_id'],
                'area_km2': float(row['area_km2']) if row['area_km2'] else 0,
                'lat': float(row['lat']) if row['lat'] else 0,
                'lon': float(row['lon']) if row['lon'] else 0
            })
        
        print(f"Found {len(park_year_polygons)} park-year combinations")
        
        # Get park names
        park_names = {}
        for row in self.conn.execute("SELECT DISTINCT park_id FROM feature_geometries WHERE feature_type = 'deforestation'"):
            parts = row['park_id'].split('_')
            park_names[row['park_id']] = ' '.join(parts[1:]).replace('_', ' ')
        
        # Clear existing events
        self.conn.execute("DELETE FROM deforestation_events")
        
        # Process each park-year with clustering
        count = 0
        linear_count = 0
        
        processed_parks = set()
        for (park_id, year), polygons in sorted(park_year_polygons.items()):
            if park_id not in processed_parks:
                processed_parks.add(park_id)
                print(f"  Processing {park_id}...")
            
            # Cluster polygons spatially
            clusters = self._cluster_polygons(polygons, DEFORESTATION_CLUSTER_KM)
            
            # Load context
            places = self._load_park_places(park_id)
            rivers = self._load_park_rivers(park_id)
            roads = self._load_park_roads(park_id)
            climate = self.climate.get(park_id, {})
            park_name = park_names.get(park_id, park_id)
            
            # Create event for each cluster
            for cluster in clusters:
                avg_lat = sum(p['lat'] for p in cluster) / len(cluster)
                avg_lon = sum(p['lon'] for p in cluster) / len(cluster)
                
                fires_near = self._get_fire_density(park_id, year, avg_lat, avg_lon, radius_km=10)
                
                classification = self._classify_deforestation(
                    cluster, park_id, year, fires_near, roads
                )
                
                if classification['is_linear']:
                    linear_count += 1
                
                nearest_place = self._get_nearest_place(avg_lat, avg_lon, places)
                nearest_river = self._get_nearest_river(avg_lat, avg_lon, rivers)
                
                narrative = self._generate_deforestation_narrative(
                    park_name, year, classification,
                    nearest_place, nearest_river, climate
                )
                
                polygon_ids = ','.join(p['feature_id'] for p in cluster)
                
                self.conn.execute("""
                    INSERT INTO deforestation_events
                    (park_id, year, area_km2, lat, lon, pattern_type, classification,
                     classification_confidence, narrative, fires_same_year, fire_ratio,
                     polygon_ids, pixel_count, classified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    park_id, year, classification['total_area_km2'],
                    avg_lat, avg_lon, classification['pattern'],
                    classification['classification'], classification['confidence'],
                    narrative, fires_near, classification['fire_ratio'],
                    polygon_ids, classification['num_polygons'],
                    datetime.now().isoformat()
                ))
                count += 1
            
            if count % 50 == 0:
                print(f"  Processed {count} events ({linear_count} linear)...")
                self.conn.commit()
        
        self.conn.commit()
        
        # Print stats
        print(f"\nCreated {count} deforestation events")
        print(f"  Linear patterns (road-associated): {linear_count}")
        
        cursor = self.conn.execute("""
            SELECT classification, COUNT(*), SUM(area_km2)
            FROM deforestation_events
            GROUP BY classification
            ORDER BY COUNT(*) DESC
        """)
        print("\nBy classification:")
        for row in cursor:
            print(f"  {row[0]}: {row[1]} events, {row[2]:.1f} km²")
        
        return count
    
    def rebuild_settlements(self):
        """Rebuild park_settlements with clustering"""
        
        print("\n" + "=" * 60)
        print("Rebuilding settlement events")
        print("=" * 60)
        
        # Get all settlement polygons
        cursor = self.conn.execute("""
            SELECT park_id, feature_id,
                   json_extract(properties_json, '$.area_m2') as area_m2,
                   json_extract(properties_json, '$.population_est') as population_est,
                   json_extract(properties_json, '$.lat') as lat,
                   json_extract(properties_json, '$.lon') as lon
            FROM feature_geometries
            WHERE feature_type = 'settlement'
            ORDER BY park_id
        """)
        
        # Group by park
        park_polygons = defaultdict(list)
        for row in cursor:
            park_polygons[row['park_id']].append({
                'feature_id': row['feature_id'],
                'area_m2': float(row['area_m2']) if row['area_m2'] else 0,
                'population_est': int(row['population_est']) if row['population_est'] else 0,
                'lat': float(row['lat']) if row['lat'] else 0,
                'lon': float(row['lon']) if row['lon'] else 0
            })
        
        print(f"Found polygons for {len(park_polygons)} parks")
        
        # Get park names
        park_names = {}
        for park_id in park_polygons.keys():
            parts = park_id.split('_')
            park_names[park_id] = ' '.join(parts[1:]).replace('_', ' ')
        
        # Clear existing settlements
        self.conn.execute("DELETE FROM park_settlements")
        
        count = 0
        for park_id, polygons in sorted(park_polygons.items()):
            print(f"  Processing {park_id} ({len(polygons)} polygons)...")
            clusters = self._cluster_polygons(polygons, SETTLEMENT_CLUSTER_KM)
            
            places = self._load_park_places(park_id)
            rivers = self._load_park_rivers(park_id)
            climate = self.climate.get(park_id, {})
            
            for cluster in clusters:
                avg_lat = sum(p['lat'] for p in cluster) / len(cluster)
                avg_lon = sum(p['lon'] for p in cluster) / len(cluster)
                total_area = sum(p['area_m2'] for p in cluster)
                total_pop = sum(p['population_est'] for p in cluster)
                
                # Simple classification based on size and population
                if total_pop > 1000:
                    classification = 'town'
                    confidence = 0.7
                elif total_pop > 200:
                    classification = 'village'
                    confidence = 0.7
                elif total_area > 50000:
                    classification = 'agricultural'
                    confidence = 0.6
                elif total_area < 5000:
                    classification = 'temporary_camp'
                    confidence = 0.5
                else:
                    classification = 'settlement'
                    confidence = 0.5
                
                nearest_place, place_dist = self._get_nearest_place(avg_lat, avg_lon, places)
                nearest_river = self._get_nearest_river(avg_lat, avg_lon, rivers)
                
                # Generate narrative
                park_name = park_names.get(park_id, park_id)
                narrative_parts = [f"{classification.replace('_', ' ').title()} in {park_name}."]
                narrative_parts.append(f"Area: {total_area/10000:.2f} ha, estimated population: {total_pop}.")
                if nearest_place:
                    narrative_parts.append(f"Located {place_dist:.1f}km from {nearest_place['name']}.")
                if nearest_river:
                    narrative_parts.append(f"Near {nearest_river['name']} river.")
                narrative = ' '.join(narrative_parts)
                
                polygon_ids = ','.join(p['feature_id'] for p in cluster)
                place_name = nearest_place['name'] if nearest_place else ''
                
                sett_type = 'temporary' if classification in ('temporary_camp', 'pastoral') else 'permanent'
                
                self.conn.execute("""
                    INSERT INTO park_settlements
                    (park_id, lat, lon, area_m2, population_est, households_est,
                     nearest_place, distance_to_place_km, settlement_type,
                     classification, classification_confidence, narrative, polygon_ids)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    park_id, avg_lat, avg_lon, total_area, total_pop,
                    int(total_pop / 4.5),
                    place_name, place_dist if place_dist else 0, sett_type,
                    classification, confidence, narrative, polygon_ids
                ))
                count += 1
            
            if count % 50 == 0:
                print(f"  Processed {count} settlements...")
                self.conn.commit()
        
        self.conn.commit()
        print(f"Created {count} settlement records")
        
        cursor = self.conn.execute("""
            SELECT classification, COUNT(*)
            FROM park_settlements
            GROUP BY classification
            ORDER BY COUNT(*) DESC
        """)
        print("\nBy classification:")
        for row in cursor:
            print(f"  {row[0]}: {row[1]}")
        
        return count
    
    def export_json(self):
        """Export events to JSON files"""
        print("\n" + "=" * 60)
        print("Exporting to JSON files")
        print("=" * 60)
        
        SETTLEMENT_DIR.mkdir(parents=True, exist_ok=True)
        DEFOREST_DIR.mkdir(parents=True, exist_ok=True)
        
        # Export settlements
        cursor = self.conn.execute("""
            SELECT * FROM park_settlements ORDER BY park_id, id
        """)
        
        settlements_by_park = defaultdict(list)
        columns = [desc[0] for desc in cursor.description]
        for row in cursor:
            d = dict(zip(columns, row))
            settlements_by_park[d['park_id']].append(d)
        
        for park_id, settlements in settlements_by_park.items():
            with open(SETTLEMENT_DIR / f'{park_id}.json', 'w') as f:
                json.dump(settlements, f, indent=2)
        print(f"  Exported settlements for {len(settlements_by_park)} parks")
        
        # Export deforestation
        cursor = self.conn.execute("""
            SELECT * FROM deforestation_events ORDER BY park_id, year, id
        """)
        
        deforest_by_park = defaultdict(list)
        columns = [desc[0] for desc in cursor.description]
        for row in cursor:
            d = dict(zip(columns, row))
            deforest_by_park[d['park_id']].append(d)
        
        for park_id, events in deforest_by_park.items():
            with open(DEFOREST_DIR / f'{park_id}.json', 'w') as f:
                json.dump(events, f, indent=2)
        print(f"  Exported deforestation for {len(deforest_by_park)} parks")
    
    def run(self):
        """Run full rebuild"""
        self.rebuild_deforestation()
        self.rebuild_settlements()
        self.export_json()
        self.conn.close()
        print("\n" + "=" * 60)
        print("Complete!")

if __name__ == '__main__':
    rebuilder = EventRebuilder()
    rebuilder.run()
