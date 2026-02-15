#!/usr/bin/env python3
"""
Rebuild deforestation_events and park_settlements from polygon data.

Uses polygon geometries + contextual data (rivers, climate, fires, roads) 
to classify and generate rich narratives.
"""

import json
import sqlite3
import math
from pathlib import Path
from collections import defaultdict
from datetime import datetime

DB_PATH = Path('db.sqlite3')
CLIMATE_FILE = Path('data/climate/park_climate.json')
RIVERS_DIR = Path('data/rivers')

class EventRebuilder:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self.climate = self._load_climate()
        self.rivers = {}
        self.osm_places = {}
        self.fire_data = {}
        
    def _load_climate(self):
        if CLIMATE_FILE.exists():
            with open(CLIMATE_FILE) as f:
                return json.load(f)
        return {}
    
    def _load_park_rivers(self, park_id):
        """Load major rivers for a park"""
        if park_id in self.rivers:
            return self.rivers[park_id]
        
        rivers = []
        cursor = self.conn.execute("""
            SELECT r.name, r.discharge_cms, r.stream_order, 
                   json_extract(r.geojson, '$.coordinates') as coords
            FROM park_rivers pr
            JOIN rivers r ON r.hyriv_id = pr.hyriv_id
            WHERE pr.park_id = ? AND r.name != '' AND r.stream_order >= 4
            ORDER BY r.discharge_cms DESC
            LIMIT 10
        """, (park_id,))
        for row in cursor:
            rivers.append({
                'name': row['name'],
                'discharge': row['discharge_cms'],
                'order': row['stream_order']
            })
        self.rivers[park_id] = rivers
        return rivers
    
    def _load_park_places(self, park_id):
        """Load OSM places for a park"""
        if park_id in self.osm_places:
            return self.osm_places[park_id]
        
        places = []
        cursor = self.conn.execute("""
            SELECT name, place_type, lat, lon
            FROM osm_places
            WHERE park_id = ? AND name != ''
            ORDER BY place_type
        """, (park_id,))
        for row in cursor:
            places.append({
                'name': row['name'],
                'type': row['place_type'],
                'lat': row['lat'],
                'lon': row['lon']
            })
        self.osm_places[park_id] = places
        return places
    
    def _get_fire_density(self, park_id, year, lat, lon, radius_km=5):
        """Get fire count near a location for a given year"""
        # Convert radius to degrees (approximate)
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
        """Find nearest named place"""
        if not places:
            return None, None
        
        min_dist = float('inf')
        nearest = None
        for p in places:
            dist = self._haversine(lat, lon, p['lat'], p['lon'])
            if dist < min_dist:
                min_dist = dist
                nearest = p
        
        return nearest, min_dist
    
    def _get_nearest_river(self, lat, lon, rivers):
        """Find nearest major river (simplified - by name only)"""
        if not rivers:
            return None
        return rivers[0]['name'] if rivers else None
    
    def _haversine(self, lat1, lon1, lat2, lon2):
        """Distance in km between two points"""
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))
    
    def _classify_deforestation(self, polygons, park_id, year, fires_near, places, rivers, climate):
        """Classify deforestation based on polygon patterns and context"""
        
        total_area = sum(p['area_km2'] for p in polygons)
        num_polygons = len(polygons)
        
        # Calculate spatial spread
        if num_polygons > 1:
            lats = [p['lat'] for p in polygons]
            lons = [p['lon'] for p in polygons]
            spread_km = self._haversine(min(lats), min(lons), max(lats), max(lons))
        else:
            spread_km = 0
        
        # Average polygon size
        avg_size = total_area / num_polygons if num_polygons > 0 else 0
        
        # Check for linear pattern (logging roads)
        is_linear = spread_km > 5 and avg_size < 0.1 and num_polygons > 5
        
        # Get season from climate
        climate_data = climate.get(park_id, {})
        dry_months = climate_data.get('dry_season', 'Dec-Feb')
        
        # Classification logic
        classification = 'unknown'
        confidence = 0.5
        pattern = 'scattered'
        
        # High fire correlation = slash and burn agriculture
        fire_ratio = fires_near / max(total_area, 0.01)
        
        if fire_ratio > 50:
            classification = 'slash_burn'
            confidence = 0.8
            pattern = 'fire_associated'
        elif is_linear:
            classification = 'logging'
            confidence = 0.7
            pattern = 'linear'
        elif avg_size > 0.5 and num_polygons < 3:
            classification = 'large_clearing'
            confidence = 0.6
            pattern = 'concentrated'
        elif spread_km < 2 and num_polygons > 3:
            classification = 'encroachment'
            confidence = 0.65
            pattern = 'clustered'
        elif fires_near == 0 and total_area < 1:
            classification = 'natural'
            confidence = 0.5
            pattern = 'scattered'
        else:
            classification = 'mixed'
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
            'fire_ratio': fire_ratio
        }
    
    def _generate_deforestation_narrative(self, park_name, year, classification, polygons, 
                                           nearest_place, nearest_river, climate_data):
        """Generate rich narrative for deforestation event"""
        
        total_area = classification['total_area_km2']
        num_poly = classification['num_polygons']
        pattern = classification['pattern']
        class_type = classification['classification']
        
        # Build narrative
        parts = []
        
        # Opening
        if total_area > 5:
            severity = "significant"
        elif total_area > 1:
            severity = "moderate"
        else:
            severity = "minor"
        
        parts.append(f"In {year}, {park_name} experienced {severity} forest loss of {total_area:.2f} km² across {num_poly} distinct patches.")
        
        # Classification description
        class_desc = {
            'slash_burn': f"Pattern strongly suggests slash-and-burn agriculture, with {classification['fires_nearby']} fire detections in the vicinity.",
            'logging': "Linear clearing pattern indicates possible logging activity or road construction.",
            'large_clearing': "Large concentrated clearing suggests planned land conversion.",
            'encroachment': "Clustered small clearings near boundaries suggest gradual encroachment.",
            'natural': "Low fire association and scattered pattern may indicate natural disturbance.",
            'mixed': "Mixed patterns suggest multiple drivers of forest loss."
        }
        parts.append(class_desc.get(class_type, ""))
        
        # Location context
        if nearest_place and nearest_place[0] and nearest_place[1] is not None:
            parts.append(f"Activity centered {nearest_place[1]:.1f}km from {nearest_place[0]['name']} ({nearest_place[0]['type']}).")
        
        if nearest_river:
            parts.append(f"Near {nearest_river} watershed.")
        
        # Season context
        dry_season = climate_data.get('dry_season', '')
        if dry_season:
            parts.append(f"Peak clearing typically occurs during dry season ({dry_season}).")
        
        return ' '.join(p for p in parts if p)
    
    def rebuild_deforestation_events(self):
        """Rebuild deforestation_events from polygon data with spatial clustering.
        
        Creates multiple events per park-year, one for each distinct spatial cluster.
        """
        
        print("Rebuilding deforestation events from polygons (with clustering)...")
        
        # Get all deforestation polygons grouped by park and year
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
        
        # Group by park and year first
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
        
        # Process each park-year with spatial clustering
        count = 0
        for (park_id, year), polygons in sorted(park_year_polygons.items()):
            # Cluster polygons spatially (5km threshold for deforestation)
            clusters = self._cluster_polygons(polygons, max_dist_km=5)
            
            # Load context once per park-year
            places = self._load_park_places(park_id)
            rivers = self._load_park_rivers(park_id)
            climate = self.climate.get(park_id, {})
            park_name = park_names.get(park_id, park_id)
            
            # Create event for each cluster
            for cluster in clusters:
                # Calculate centroid
                avg_lat = sum(p['lat'] for p in cluster) / len(cluster)
                avg_lon = sum(p['lon'] for p in cluster) / len(cluster)
                
                # Get fire density
                fires_near = self._get_fire_density(park_id, year, avg_lat, avg_lon, radius_km=10)
                
                # Classify
                classification = self._classify_deforestation(
                    cluster, park_id, year, fires_near, places, rivers, climate
                )
                
                # Get nearest place
                nearest_place = self._get_nearest_place(avg_lat, avg_lon, places)
                nearest_river = self._get_nearest_river(avg_lat, avg_lon, rivers)
                
                # Generate narrative
                narrative = self._generate_deforestation_narrative(
                    park_name, year, classification, cluster,
                    nearest_place, nearest_river, climate
                )
                
                # Get polygon IDs
                polygon_ids = ','.join(p['feature_id'] for p in cluster)
                
                # Insert event
                self.conn.execute("""
                    INSERT INTO deforestation_events 
                    (park_id, year, area_km2, lat, lon, pattern_type, classification,
                     classification_confidence, narrative, fires_same_year, fire_ratio, 
                     polygon_ids, pixel_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    park_id, year, classification['total_area_km2'],
                    avg_lat, avg_lon, classification['pattern'],
                    classification['classification'], classification['confidence'],
                    narrative, fires_near, classification['fire_ratio'],
                    polygon_ids, classification['num_polygons']
                ))
                
                count += 1
            
            if count % 500 == 0:
                print(f"  Processed {count} events...")
                self.conn.commit()
        
        self.conn.commit()
        print(f"Created {count} deforestation events (clustered)")
        return count
    
    def _classify_settlement(self, polygons, park_id, fires_nearby, places, rivers, climate):
        """Classify settlement based on polygon patterns and context"""
        
        total_area = sum(p['area_m2'] for p in polygons)
        total_pop = sum(p.get('population_est', 0) for p in polygons)
        num_polygons = len(polygons)
        
        # Calculate spread
        if num_polygons > 1:
            lats = [p['lat'] for p in polygons]
            lons = [p['lon'] for p in polygons]
            spread_km = self._haversine(min(lats), min(lons), max(lats), max(lons))
        else:
            spread_km = 0
        
        # Classification logic
        classification = 'unknown'
        confidence = 0.5
        
        # Fire pattern indicates activity type
        fire_ratio = fires_nearby / max(total_area / 1000000, 0.001)  # fires per km²
        
        if total_area > 500000 and total_pop > 500:
            classification = 'town'
            confidence = 0.8
        elif total_area > 100000 and total_pop > 100:
            classification = 'village'
            confidence = 0.75
        elif fire_ratio > 100 and total_area < 50000:
            classification = 'agricultural'
            confidence = 0.7
        elif fire_ratio > 50 and spread_km > 2:
            classification = 'pastoral'
            confidence = 0.65
        elif total_area < 20000 and num_polygons <= 2:
            classification = 'temporary_camp'
            confidence = 0.6
        elif num_polygons > 5 and spread_km < 1:
            classification = 'compound'
            confidence = 0.6
        else:
            classification = 'settlement'
            confidence = 0.5
        
        return {
            'classification': classification,
            'confidence': confidence,
            'total_area_m2': total_area,
            'total_population': total_pop,
            'num_polygons': num_polygons,
            'spread_km': spread_km,
            'fires_nearby': fires_nearby,
            'fire_ratio': fire_ratio
        }
    
    def _generate_settlement_narrative(self, park_name, classification, polygons,
                                        nearest_place, nearest_river, climate_data):
        """Generate rich narrative for settlement"""
        
        total_area = classification['total_area_m2']
        total_pop = classification['total_population']
        class_type = classification['classification']
        num_poly = classification['num_polygons']
        
        parts = []
        
        # Opening based on classification
        class_desc = {
            'town': f"Major settlement with approximately {total_pop:,} residents",
            'village': f"Established village with approximately {total_pop:,} residents",
            'agricultural': "Agricultural settlement with significant farming activity",
            'pastoral': "Pastoral community with seasonal grazing patterns",
            'temporary_camp': "Temporary or seasonal camp",
            'compound': "Residential compound or homestead cluster",
            'settlement': f"Settlement with {num_poly} distinct built-up areas"
        }
        parts.append(class_desc.get(class_type, f"Settlement ({class_type})") + ".")
        
        # Size context
        area_ha = total_area / 10000
        if area_ha > 10:
            parts.append(f"Covers {area_ha:.1f} hectares of built-up area.")
        elif area_ha > 1:
            parts.append(f"Covers {area_ha:.2f} hectares.")
        
        # Location
        if nearest_place and nearest_place[0] and nearest_place[1] is not None and nearest_place[1] < 20:
            parts.append(f"Located {nearest_place[1]:.1f}km from {nearest_place[0]['name']}.")
        
        if nearest_river:
            parts.append(f"Near {nearest_river}.")
        
        # Fire activity context
        if classification['fires_nearby'] > 50:
            parts.append(f"High fire activity ({classification['fires_nearby']} detections) suggests active land management.")
        elif classification['fires_nearby'] > 10:
            parts.append("Moderate fire activity in surrounding area.")
        
        return ' '.join(p for p in parts if p)
    
    def rebuild_settlement_events(self):
        """Rebuild park_settlements from polygon data"""
        
        print("\nRebuilding settlement records from polygons...")
        
        # Get all settlement polygons grouped by park
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
        
        # Group by park - we'll cluster nearby polygons
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
            # Cluster nearby polygons (within 2km)
            clusters = self._cluster_polygons(polygons, max_dist_km=2)
            
            # Load context
            places = self._load_park_places(park_id)
            rivers = self._load_park_rivers(park_id)
            climate = self.climate.get(park_id, {})
            
            for cluster in clusters:
                # Calculate centroid
                avg_lat = sum(p['lat'] for p in cluster) / len(cluster)
                avg_lon = sum(p['lon'] for p in cluster) / len(cluster)
                total_area = sum(p['area_m2'] for p in cluster)
                total_pop = sum(p['population_est'] for p in cluster)
                
                # Get fires nearby
                fires_near = self._get_fire_density(park_id, 2024, avg_lat, avg_lon, radius_km=5)
                
                # Classify
                classification = self._classify_settlement(
                    cluster, park_id, fires_near, places, rivers, climate
                )
                
                # Get nearest place
                nearest_place = self._get_nearest_place(avg_lat, avg_lon, places)
                nearest_river = self._get_nearest_river(avg_lat, avg_lon, rivers)
                
                # Generate narrative
                park_name = park_names.get(park_id, park_id)
                narrative = self._generate_settlement_narrative(
                    park_name, classification, cluster,
                    nearest_place, nearest_river, climate
                )
                
                # Get nearest place name for the record
                place_name = nearest_place[0]['name'] if nearest_place and nearest_place[0] else ''
                place_dist = nearest_place[1] if nearest_place else 0
                
                # Polygon IDs
                polygon_ids = ','.join(p['feature_id'] for p in cluster)
                
                # Map classification to settlement_type (temporary/permanent)
                sett_type_map = {
                    'town': 'permanent',
                    'village': 'permanent', 
                    'compound': 'permanent',
                    'settlement': 'permanent',
                    'agricultural': 'permanent',
                    'pastoral': 'temporary',
                    'temporary_camp': 'temporary',
                    'unknown': 'permanent'
                }
                sett_type = sett_type_map.get(classification['classification'], 'permanent')
                
                # Insert
                self.conn.execute("""
                    INSERT INTO park_settlements 
                    (park_id, lat, lon, area_m2, population_est, households_est,
                     nearest_place, distance_to_place_km, settlement_type,
                     classification, classification_confidence, narrative, polygon_ids)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    park_id, avg_lat, avg_lon, total_area, total_pop,
                    int(total_pop / 4.5),  # Estimate households
                    place_name, place_dist, sett_type,
                    classification['classification'], classification['confidence'],
                    narrative, polygon_ids
                ))
                
                count += 1
            
            if count % 100 == 0:
                print(f"  Processed {count} settlements...")
                self.conn.commit()
        
        self.conn.commit()
        print(f"Created {count} settlement records")
        return count
    
    def _cluster_polygons(self, polygons, max_dist_km=2):
        """Cluster nearby polygons together"""
        if not polygons:
            return []
        
        # Simple clustering - assign each to nearest cluster or create new
        clusters = []
        used = set()
        
        for i, p in enumerate(polygons):
            if i in used:
                continue
            
            # Start new cluster
            cluster = [p]
            used.add(i)
            
            # Find all nearby polygons
            for j, other in enumerate(polygons):
                if j in used:
                    continue
                
                # Check distance to any polygon in cluster
                for cp in cluster:
                    dist = self._haversine(cp['lat'], cp['lon'], other['lat'], other['lon'])
                    if dist < max_dist_km:
                        cluster.append(other)
                        used.add(j)
                        break
            
            clusters.append(cluster)
        
        return clusters
    
    def run(self):
        """Run full rebuild"""
        print("=" * 60)
        print("Rebuilding Events from Polygon Data")
        print("=" * 60)
        
        defo_count = self.rebuild_deforestation_events()
        sett_count = self.rebuild_settlement_events()
        
        print("\n" + "=" * 60)
        print(f"Summary:")
        print(f"  Deforestation events: {defo_count}")
        print(f"  Settlement records: {sett_count}")
        print("=" * 60)
        
        self.conn.close()

if __name__ == '__main__':
    rebuilder = EventRebuilder()
    rebuilder.run()
