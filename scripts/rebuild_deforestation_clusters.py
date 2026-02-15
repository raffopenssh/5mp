#!/usr/bin/env python3
"""
Rebuild deforestation_events with spatial clustering.

Instead of one event per (park, year), creates separate events for each
distinct spatial cluster of deforestation polygons within a year.
"""

import json
import sqlite3
import math
from pathlib import Path
from collections import defaultdict
from datetime import datetime

DB_PATH = Path('db.sqlite3')
CLIMATE_FILE = Path('data/climate/park_climate.json')

# Clustering parameters
CLUSTER_DISTANCE_KM = 5.0  # Polygons within 5km are same cluster

def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance in km between two points"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def cluster_polygons(polygons, max_distance_km=CLUSTER_DISTANCE_KM):
    """
    Cluster polygons by proximity using simple greedy clustering.
    Returns list of clusters, each cluster is a list of polygons.
    """
    if not polygons:
        return []
    
    remaining = list(polygons)
    clusters = []
    
    while remaining:
        # Start new cluster with first remaining polygon
        seed = remaining.pop(0)
        cluster = [seed]
        
        # Find all polygons within distance of any cluster member
        changed = True
        while changed:
            changed = False
            still_remaining = []
            for p in remaining:
                # Check distance to any cluster member
                in_cluster = False
                for c in cluster:
                    dist = haversine(p['lat'], p['lon'], c['lat'], c['lon'])
                    if dist <= max_distance_km:
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

class DeforestationRebuilder:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self.climate = self._load_climate()
        
    def _load_climate(self):
        if CLIMATE_FILE.exists():
            with open(CLIMATE_FILE) as f:
                return json.load(f)
        return {}
    
    def _load_park_rivers(self, park_id):
        """Load major rivers for a park"""
        rivers = []
        cursor = self.conn.execute("""
            SELECT r.name, r.discharge_cms, r.stream_order
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
        return rivers
    
    def _load_park_places(self, park_id):
        """Load OSM places for a park"""
        places = []
        cursor = self.conn.execute("""
            SELECT name, place_type, lat, lon
            FROM osm_places
            WHERE park_id = ?
            ORDER BY CASE place_type 
                WHEN 'city' THEN 1 WHEN 'town' THEN 2 
                WHEN 'village' THEN 3 ELSE 4 END
            LIMIT 50
        """, (park_id,))
        for row in cursor:
            places.append({
                'name': row['name'],
                'type': row['place_type'],
                'lat': row['lat'],
                'lon': row['lon']
            })
        return places
    
    def _get_fire_density(self, park_id, year, lat, lon, radius_km=10):
        """Get fire detection count near a point in a given year"""
        # Approximate degree distance
        lat_range = radius_km / 111.0
        lon_range = radius_km / (111.0 * math.cos(math.radians(lat)))
        
        cursor = self.conn.execute("""
            SELECT COUNT(*) FROM fire_detections
            WHERE strftime('%Y', acq_date) = ?
              AND latitude BETWEEN ? AND ?
              AND longitude BETWEEN ? AND ?
        """, (str(year), lat - lat_range, lat + lat_range,
              lon - lon_range, lon + lon_range))
        return cursor.fetchone()[0]
    
    def _get_nearest_place(self, lat, lon, places):
        """Find nearest place"""
        nearest = None
        min_dist = float('inf')
        for p in places:
            dist = haversine(lat, lon, p['lat'], p['lon'])
            if dist < min_dist:
                min_dist = dist
                nearest = (p, dist)
        return nearest
    
    def _get_nearest_river(self, lat, lon, rivers):
        """Find nearest named river (simplified - just returns first)"""
        if rivers:
            return rivers[0]
        return None
    
    def _classify_cluster(self, polygons, park_id, year, fires_near, places, rivers, climate):
        """Classify a deforestation cluster based on patterns and context"""
        
        total_area = sum(p['area_km2'] for p in polygons)
        num_polygons = len(polygons)
        
        # Calculate spread/density
        if num_polygons > 1:
            lats = [p['lat'] for p in polygons]
            lons = [p['lon'] for p in polygons]
            spread_km = haversine(min(lats), min(lons), max(lats), max(lons))
        else:
            spread_km = 0
        
        # Fire ratio
        fire_ratio = fires_near / max(total_area, 0.1) if fires_near else 0
        
        # Classification logic
        classification = 'unknown'
        confidence = 0.5
        pattern = 'scattered'
        
        # Determine pattern based on polygon distribution
        if num_polygons == 1:
            pattern = 'single_patch'
        elif spread_km < 2:
            pattern = 'concentrated'
        elif spread_km < 10:
            pattern = 'clustered'
        else:
            pattern = 'scattered'
        
        # Classify based on fire correlation and pattern
        if fire_ratio > 5:
            classification = 'slash_burn'
            confidence = min(0.9, 0.6 + fire_ratio / 50)
        elif fire_ratio > 1:
            classification = 'agricultural_clearing'
            confidence = 0.7
        elif pattern == 'single_patch' and total_area > 0.5:
            classification = 'logging'
            confidence = 0.65
        elif pattern == 'concentrated':
            classification = 'logging'
            confidence = 0.6
        elif pattern == 'scattered' and total_area < 0.2:
            classification = 'natural'
            confidence = 0.5
        else:
            classification = 'encroachment'
            confidence = 0.55
        
        return {
            'classification': classification,
            'confidence': confidence,
            'pattern': pattern,
            'total_area_km2': total_area,
            'num_polygons': num_polygons,
            'spread_km': spread_km,
            'fire_ratio': fire_ratio
        }
    
    def _generate_narrative(self, park_name, year, classification, polygons, 
                           nearest_place, nearest_river, climate):
        """Generate narrative for a deforestation cluster"""
        
        parts = []
        
        # Classification and area
        class_desc = {
            'slash_burn': 'Slash-and-burn clearing',
            'agricultural_clearing': 'Agricultural expansion',
            'logging': 'Logging activity',
            'encroachment': 'Forest encroachment',
            'natural': 'Natural forest loss',
            'unknown': 'Forest loss'
        }
        
        total_area = sum(p['area_km2'] for p in polygons)
        parts.append(f"{class_desc.get(classification['classification'], 'Forest loss')} detected in {year}.")
        parts.append(f"Affected area: {total_area:.2f} km² across {classification['num_polygons']} {'patch' if classification['num_polygons'] == 1 else 'patches'}.")
        
        # Pattern
        if classification['pattern'] == 'concentrated':
            parts.append("Concentrated clearing pattern suggests organized activity.")
        elif classification['pattern'] == 'scattered':
            parts.append("Scattered pattern across multiple locations.")
        
        # Fire correlation
        if classification['fire_ratio'] > 5:
            parts.append("Strong fire correlation indicates burning for land clearing.")
        elif classification['fire_ratio'] > 1:
            parts.append("Moderate fire activity in the area.")
        
        # Location context
        if nearest_place:
            place, dist = nearest_place
            parts.append(f"Located {dist:.1f}km from {place['name']}.")
        
        if nearest_river:
            parts.append(f"Near {nearest_river['name']} river.")
        
        # Season
        if climate:
            dry_months = climate.get('dry_months', [])
            if dry_months:
                parts.append(f"Peak activity likely during dry season ({', '.join(dry_months[:3])}).")
        
        return ' '.join(parts)
    
    def rebuild(self):
        """Rebuild all deforestation events with spatial clustering"""
        
        print("=" * 60)
        print("Rebuilding deforestation events with spatial clustering")
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
        
        # Drop and recreate table without UNIQUE(park_id, year)
        print("\nUpdating schema (removing UNIQUE constraint)...")
        self.conn.execute("DROP TABLE IF EXISTS deforestation_events_new")
        self.conn.execute("""
            CREATE TABLE deforestation_events_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id TEXT NOT NULL,
                year INTEGER NOT NULL,
                area_km2 REAL NOT NULL,
                event_type TEXT,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                geojson TEXT,
                description TEXT,
                pattern_type TEXT,
                pixel_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                classification TEXT,
                classification_confidence REAL,
                narrative TEXT,
                fires_same_year INTEGER DEFAULT 0,
                fire_ratio REAL DEFAULT 0,
                nearest_settlement_km REAL,
                classified_at TIMESTAMP,
                polygon_ids TEXT
            )
        """)
        
        # Process each park-year with clustering
        total_events = 0
        total_clusters = 0
        
        for (park_id, year), polygons in sorted(park_year_polygons.items()):
            # Cluster polygons spatially
            clusters = cluster_polygons(polygons)
            total_clusters += len(clusters)
            
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
                classification = self._classify_cluster(
                    cluster, park_id, year, fires_near, places, rivers, climate
                )
                
                # Location context
                nearest_place = self._get_nearest_place(avg_lat, avg_lon, places)
                nearest_river = self._get_nearest_river(avg_lat, avg_lon, rivers)
                
                # Generate narrative
                narrative = self._generate_narrative(
                    park_name, year, classification, cluster,
                    nearest_place, nearest_river, climate
                )
                
                # Polygon IDs
                polygon_ids = ','.join(p['feature_id'] for p in cluster)
                
                # Insert
                self.conn.execute("""
                    INSERT INTO deforestation_events_new
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
                total_events += 1
            
            if total_events % 500 == 0:
                print(f"  Processed {total_events} events...")
                self.conn.commit()
        
        self.conn.commit()
        
        # Replace old table
        print("\nReplacing old table...")
        self.conn.execute("DROP TABLE IF EXISTS deforestation_events")
        self.conn.execute("ALTER TABLE deforestation_events_new RENAME TO deforestation_events")
        self.conn.execute("CREATE INDEX idx_deforest_park ON deforestation_events(park_id)")
        self.conn.execute("CREATE INDEX idx_deforest_year ON deforestation_events(year)")
        self.conn.execute("CREATE INDEX idx_deforest_coords ON deforestation_events(lat, lon)")
        self.conn.commit()
        
        print(f"\n{'=' * 60}")
        print(f"Created {total_events} deforestation events from {total_clusters} clusters")
        print(f"Average {total_events / len(park_year_polygons):.1f} events per park-year")
        
        # Stats by classification
        cursor = self.conn.execute("""
            SELECT classification, COUNT(*), SUM(area_km2)
            FROM deforestation_events
            GROUP BY classification
            ORDER BY COUNT(*) DESC
        """)
        print("\nBy classification:")
        for row in cursor:
            print(f"  {row[0]}: {row[1]} events, {row[2]:.1f} km²")
        
        return total_events

def main():
    rebuilder = DeforestationRebuilder()
    rebuilder.rebuild()

if __name__ == '__main__':
    main()
