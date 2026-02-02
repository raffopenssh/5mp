#!/usr/bin/env python3
"""Classify settlements and deforestation using spatial cross-reference.

Classifies features based on proximity to:
- Roads (from osm_roadless_data.roads_json)
- Rivers/streams (from osm_places where place_type IN ('river', 'stream'))
- Other settlements
- Deforestation clusters
- Park boundaries

Settlement Classifications:
- village: large (>50 buildings equivalent), named, road access
- hamlet: 5+ nearby settlements, >2km from roads  
- roadside_settlement: <500m from road, linear arrangement
- park_infrastructure: inside park, small, near known facilities
- logging_camp: near forest edge, near track/tertiary road
- sawmill_compound: large footprint, near road, rectangular
- agricultural_compound: small area, isolated, near cleared land
- pastoral_camp: near grassland, circular arrangement
- artisanal_mining_camp: near river + deforestation
- fishing_camp: near river/lake, small
- charcoal_camp: near forest edge, 2-10km from road
- unclassified: default

Deforestation Classifications:
- road_clearing: linear, parallel to road <200m
- agricultural_expansion: near settlements <5km, incremental
- smallholder_farming: small patches <0.5km², scattered
- industrial_logging: large rectangular blocks >1km²
- selective_logging: scattered gaps, near logging roads
- forestry_plantation: regular geometric shape
- charcoal_production: circular 0.1-0.5km², 2-10km from road
- slash_and_burn: irregular, fire scars, near settlements
- artisanal_mining: near river <1km
- infrastructure_clearing: linear/geometric, near roads
- management_firebreak: linear, along boundary
- natural_disturbance: far from everything
- riverbank_erosion: along river, narrow strip
- unclassified: default

Usage:
    python scripts/classify_features.py --type settlement --park CAF_Chinko
    python scripts/classify_features.py --type deforestation --all
    python scripts/classify_features.py --all-types --all
"""

import argparse
import json
import sqlite3
import math
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in meters."""
    R = 6371000  # Earth's radius in meters
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    
    a = math.sin(dlat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    return R * c


def point_to_line_distance(point_lat, point_lon, line_coords):
    """Calculate minimum distance from point to line in meters."""
    min_dist = float('inf')
    
    for i in range(len(line_coords) - 1):
        # Line segment from coords[i] to coords[i+1]
        lon1, lat1 = line_coords[i]
        lon2, lat2 = line_coords[i + 1]
        
        # Simple perpendicular distance approximation
        # For more accuracy, would need proper geodesic calculation
        dist_to_start = haversine_distance(point_lat, point_lon, lat1, lon1)
        dist_to_end = haversine_distance(point_lat, point_lon, lat2, lon2)
        
        # Use minimum of distances to endpoints
        # (simplified - proper perpendicular distance is more complex)
        seg_dist = min(dist_to_start, dist_to_end)
        
        if seg_dist < min_dist:
            min_dist = seg_dist
    
    return min_dist


def load_roads_for_park(conn, park_id):
    """Load road coordinates for a park."""
    cursor = conn.execute('''
        SELECT roads_json FROM osm_roadless_data WHERE park_id = ?
    ''', (park_id,))
    row = cursor.fetchone()
    
    if not row or not row[0]:
        return []
    
    try:
        roads = json.loads(row[0])
        return [(r.get('type', 'unknown'), r['coords']) for r in roads if 'coords' in r]
    except (json.JSONDecodeError, KeyError):
        return []


def load_rivers_for_park(conn, park_id):
    """Load river/stream locations for a park."""
    cursor = conn.execute('''
        SELECT lat, lon, name FROM osm_places 
        WHERE park_id = ? AND place_type IN ('river', 'stream')
    ''', (park_id,))
    return [(row[0], row[1], row[2]) for row in cursor]


def load_settlements_for_park(conn, park_id):
    """Load settlement locations for a park."""
    cursor = conn.execute('''
        SELECT id, lat, lon, area_m2 FROM park_settlements WHERE park_id = ?
    ''', (park_id,))
    return [(row[0], row[1], row[2], row[3]) for row in cursor]


def load_deforestation_for_park(conn, park_id):
    """Load deforestation cluster locations for a park."""
    cursor = conn.execute('''
        SELECT id, lat, lon, area_km2, year FROM deforestation_clusters WHERE park_id = ?
    ''', (park_id,))
    return [(row[0], row[1], row[2], row[3], row[4]) for row in cursor]


def min_distance_to_roads(lat, lon, roads):
    """Calculate minimum distance to any road."""
    if not roads:
        return float('inf'), None
    
    min_dist = float('inf')
    nearest_type = None
    
    for road_type, coords in roads:
        dist = point_to_line_distance(lat, lon, coords)
        if dist < min_dist:
            min_dist = dist
            nearest_type = road_type
    
    return min_dist, nearest_type


def min_distance_to_rivers(lat, lon, rivers):
    """Calculate minimum distance to any river/stream point."""
    if not rivers:
        return float('inf')
    
    return min(haversine_distance(lat, lon, r[0], r[1]) for r in rivers)


def count_nearby_settlements(lat, lon, settlements, radius_m, exclude_id=None):
    """Count settlements within radius."""
    count = 0
    for s_id, s_lat, s_lon, _ in settlements:
        if s_id == exclude_id:
            continue
        if haversine_distance(lat, lon, s_lat, s_lon) <= radius_m:
            count += 1
    return count


def min_distance_to_deforestation(lat, lon, deforestation):
    """Calculate minimum distance to any deforestation cluster."""
    if not deforestation:
        return float('inf')
    
    return min(haversine_distance(lat, lon, d[1], d[2]) for d in deforestation)


def classify_settlement(settlement_id, lat, lon, area_m2, roads, rivers, settlements, deforestation):
    """Classify a single settlement based on spatial context.
    
    Returns: (classification, confidence, dist_to_road, dist_to_river)
    """
    
    dist_to_road, road_type = min_distance_to_roads(lat, lon, roads)
    dist_to_river = min_distance_to_rivers(lat, lon, rivers)
    nearby_count = count_nearby_settlements(lat, lon, settlements, 2000, settlement_id)
    nearby_count_5km = count_nearby_settlements(lat, lon, settlements, 5000, settlement_id)
    dist_to_deforestation = min_distance_to_deforestation(lat, lon, deforestation)
    
    # Classification rules (in priority order)
    
    # 1. Village: large settlement with road access
    if area_m2 > 50000 and dist_to_road < 1000:
        return 'village', 0.90, dist_to_road, dist_to_river
    
    # 2. Sawmill compound: large footprint, near road
    if area_m2 > 5000 and dist_to_road < 500 and road_type in ('tertiary', 'track', 'unclassified'):
        return 'sawmill_compound', 0.65, dist_to_road, dist_to_river
    
    # 3. Roadside settlement: close to road, linear
    if dist_to_road < 500:
        return 'roadside_settlement', 0.85, dist_to_road, dist_to_river
    
    # 4. Logging camp: near track/tertiary road, medium distance
    if 500 < dist_to_road < 2000 and road_type in ('tertiary', 'track', 'unclassified'):
        if dist_to_deforestation < 3000:
            return 'logging_camp', 0.60, dist_to_road, dist_to_river
    
    # 5. Hamlet: many nearby settlements, away from roads
    if nearby_count >= 5 and dist_to_road > 2000:
        return 'hamlet', 0.80, dist_to_road, dist_to_river
    
    # 6. Charcoal camp: 2-10km from road, near forest (deforestation)
    if 2000 < dist_to_road < 10000 and dist_to_deforestation < 2000:
        if area_m2 < 5000:  # Small temporary structures
            return 'charcoal_camp', 0.55, dist_to_road, dist_to_river
    
    # 7. Artisanal mining camp: near river AND near deforestation
    if dist_to_river < 1000 and dist_to_deforestation < 2000:
        return 'artisanal_mining_camp', 0.50, dist_to_road, dist_to_river
    
    # 8. Fishing camp: near river, no deforestation nearby
    if dist_to_river < 500 and dist_to_deforestation > 5000:
        return 'fishing_camp', 0.55, dist_to_road, dist_to_river
    
    # 9. Pastoral camp: isolated, not near rivers or deforestation
    if nearby_count < 2 and dist_to_river > 2000 and dist_to_deforestation > 5000:
        if area_m2 < 10000:
            return 'pastoral_camp', 0.45, dist_to_road, dist_to_river
    
    # 10. Agricultural compound: small, isolated, near some cleared land
    if area_m2 < 10000 and dist_to_road > 5000 and nearby_count < 2:
        if dist_to_deforestation < 5000:
            return 'agricultural_compound', 0.60, dist_to_road, dist_to_river
    
    # 11. Park infrastructure: small, very isolated (likely ranger post)
    if area_m2 < 2000 and nearby_count_5km == 0 and dist_to_road > 10000:
        return 'park_infrastructure', 0.40, dist_to_road, dist_to_river
    
    return 'unclassified', 0.0, dist_to_road, dist_to_river


def classify_deforestation_cluster(cluster_id, lat, lon, area_km2, pattern_type, 
                                   roads, rivers, settlements):
    """Classify a single deforestation cluster based on spatial context.
    
    Returns: (classification, confidence, dist_to_road, dist_to_settlement, dist_to_river)
    """
    
    dist_to_road, road_type = min_distance_to_roads(lat, lon, roads)
    dist_to_river = min_distance_to_rivers(lat, lon, rivers)
    dist_to_settlement = float('inf')
    if settlements:
        dist_to_settlement = min(haversine_distance(lat, lon, s[1], s[2]) for s in settlements)
    
    # Convert area to numeric for comparisons
    area = area_km2 or 0
    
    # Classification rules (in priority order)
    
    # 1. Road clearing: linear pattern, very close to road
    if pattern_type == 'strip' and dist_to_road < 200:
        return 'road_clearing', 0.90, dist_to_road, dist_to_settlement, dist_to_river
    
    # 2. Infrastructure clearing: geometric, near road, moderate size
    if pattern_type in ('strip', 'cluster') and dist_to_road < 500 and 0.01 < area < 0.5:
        return 'infrastructure_clearing', 0.75, dist_to_road, dist_to_settlement, dist_to_river
    
    # 3. Industrial logging: large rectangular blocks
    if area > 1.0 and pattern_type == 'cluster':
        if dist_to_road < 5000:  # Need road access for trucks
            return 'industrial_logging', 0.80, dist_to_road, dist_to_settlement, dist_to_river
    
    # 4. Selective logging: scattered pattern, near logging roads
    if pattern_type == 'scattered' and dist_to_road < 3000:
        if road_type in ('track', 'tertiary', 'unclassified'):
            return 'selective_logging', 0.65, dist_to_road, dist_to_settlement, dist_to_river
    
    # 5. Riverbank erosion: very close to river, narrow
    if dist_to_river < 200 and area < 0.1:
        return 'riverbank_erosion', 0.60, dist_to_road, dist_to_settlement, dist_to_river
    
    # 6. Artisanal mining: near river, irregular
    if dist_to_river < 1000 and pattern_type in ('scattered', 'cluster'):
        return 'artisanal_mining', 0.55, dist_to_road, dist_to_settlement, dist_to_river
    
    # 7. Smallholder farming: small patches near villages
    if area < 0.5 and dist_to_settlement < 3000:
        return 'smallholder_farming', 0.80, dist_to_road, dist_to_settlement, dist_to_river
    
    # 8. Slash and burn: near settlements, scattered pattern
    if dist_to_settlement < 5000 and pattern_type == 'scattered':
        return 'slash_and_burn', 0.70, dist_to_road, dist_to_settlement, dist_to_river
    
    # 9. Agricultural expansion: near settlements, larger areas
    if dist_to_settlement < 5000 and area > 0.5:
        return 'agricultural_expansion', 0.75, dist_to_road, dist_to_settlement, dist_to_river
    
    # 10. Charcoal production: medium distance from road, clustered
    if 2000 < dist_to_road < 10000 and dist_to_river > 2000:
        if 0.1 < area < 0.5 and pattern_type == 'cluster':
            return 'charcoal_production', 0.60, dist_to_road, dist_to_settlement, dist_to_river
    
    # 11. Management firebreak: linear, could be anywhere
    if pattern_type == 'strip' and area < 0.1:
        return 'management_firebreak', 0.50, dist_to_road, dist_to_settlement, dist_to_river
    
    # 12. Natural disturbance: far from everything
    if dist_to_road > 10000 and dist_to_settlement > 10000:
        return 'natural_disturbance', 0.45, dist_to_road, dist_to_settlement, dist_to_river
    
    # 13. Forestry plantation: regular shape, road access (harder to detect without shape analysis)
    if pattern_type == 'cluster' and dist_to_road < 2000 and 0.5 < area < 5.0:
        return 'forestry_plantation', 0.40, dist_to_road, dist_to_settlement, dist_to_river
    
    return 'unclassified', 0.0, dist_to_road, dist_to_settlement, dist_to_river


def classify_settlements_for_park(conn, park_id):
    """Classify all settlements for a park."""
    print(f"  Loading spatial data for {park_id}...")
    
    roads = load_roads_for_park(conn, park_id)
    rivers = load_rivers_for_park(conn, park_id)
    settlements = load_settlements_for_park(conn, park_id)
    deforestation = load_deforestation_for_park(conn, park_id)
    
    print(f"    Roads: {len(roads)}, Rivers: {len(rivers)}, Settlements: {len(settlements)}, Deforestation: {len(deforestation)}")
    
    if not settlements:
        print(f"    No settlements to classify")
        return 0
    
    # Ensure columns exist
    try:
        conn.execute('ALTER TABLE park_settlements ADD COLUMN classification TEXT DEFAULT "unclassified"')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE park_settlements ADD COLUMN confidence REAL DEFAULT 0.0')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE park_settlements ADD COLUMN distance_to_road_m REAL')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE park_settlements ADD COLUMN distance_to_river_m REAL')
    except sqlite3.OperationalError:
        pass
    
    classified = 0
    classifications = defaultdict(int)
    
    for s_id, s_lat, s_lon, s_area in settlements:
        classification, confidence, dist_road, dist_river = classify_settlement(
            s_id, s_lat, s_lon, s_area or 0,
            roads, rivers, settlements, deforestation
        )
        
        conn.execute('''
            UPDATE park_settlements 
            SET classification = ?, confidence = ?, 
                distance_to_road_m = ?, distance_to_river_m = ?
            WHERE id = ?
        ''', (classification, confidence, dist_road, dist_river, s_id))
        
        classifications[classification] += 1
        classified += 1
        
        if classified % 100 == 0:
            conn.commit()
            print(f"    Classified {classified}/{len(settlements)}...")
    
    conn.commit()
    
    print(f"  {park_id} classification breakdown:")
    for cls, count in sorted(classifications.items(), key=lambda x: -x[1]):
        print(f"    {cls}: {count}")
    
    return classified


def classify_deforestation_for_park(conn, park_id):
    """Classify all deforestation clusters for a park."""
    print(f"  Loading spatial data for {park_id}...")
    
    roads = load_roads_for_park(conn, park_id)
    rivers = load_rivers_for_park(conn, park_id)
    settlements = load_settlements_for_park(conn, park_id)
    deforestation = load_deforestation_for_park(conn, park_id)
    
    print(f"    Roads: {len(roads)}, Rivers: {len(rivers)}, Settlements: {len(settlements)}, Clusters: {len(deforestation)}")
    
    if not deforestation:
        print(f"    No deforestation clusters to classify")
        return 0
    
    # Ensure columns exist
    try:
        conn.execute('ALTER TABLE deforestation_clusters ADD COLUMN classification TEXT DEFAULT "unclassified"')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE deforestation_clusters ADD COLUMN confidence REAL DEFAULT 0.0')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE deforestation_clusters ADD COLUMN distance_to_road_m REAL')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE deforestation_clusters ADD COLUMN distance_to_settlement_m REAL')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE deforestation_clusters ADD COLUMN distance_to_river_m REAL')
    except sqlite3.OperationalError:
        pass
    
    # Get pattern types
    cursor = conn.execute('''
        SELECT id, pattern_type FROM deforestation_clusters WHERE park_id = ?
    ''', (park_id,))
    pattern_types = {row[0]: row[1] for row in cursor}
    
    classified = 0
    classifications = defaultdict(int)
    
    for d_id, d_lat, d_lon, d_area, d_year in deforestation:
        pattern = pattern_types.get(d_id, 'unknown')
        
        classification, confidence, dist_road, dist_settlement, dist_river = classify_deforestation_cluster(
            d_id, d_lat, d_lon, d_area or 0, pattern,
            roads, rivers, settlements
        )
        
        conn.execute('''
            UPDATE deforestation_clusters 
            SET classification = ?, confidence = ?, 
                distance_to_road_m = ?, distance_to_settlement_m = ?, distance_to_river_m = ?
            WHERE id = ?
        ''', (classification, confidence, dist_road, dist_settlement, dist_river, d_id))
        
        classifications[classification] += 1
        classified += 1
        
        if classified % 100 == 0:
            conn.commit()
            print(f"    Classified {classified}/{len(deforestation)}...")
    
    conn.commit()
    
    print(f"  {park_id} classification breakdown:")
    for cls, count in sorted(classifications.items(), key=lambda x: -x[1]):
        print(f"    {cls}: {count}")
    
    return classified


def get_all_park_ids(conn):
    """Get list of all park IDs."""
    cursor = conn.execute('SELECT DISTINCT park_id FROM park_settlements ORDER BY park_id')
    return [row[0] for row in cursor]


def main():
    parser = argparse.ArgumentParser(description='Classify features using spatial cross-reference')
    parser.add_argument('--type', choices=['settlement', 'deforestation'],
                        help='Feature type to classify')
    parser.add_argument('--all-types', action='store_true', help='Classify all feature types')
    parser.add_argument('--park', help='Specific park ID')
    parser.add_argument('--all', action='store_true', help='Process all parks')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    
    args = parser.parse_args()
    
    if not args.type and not args.all_types:
        parser.error('Either --type or --all-types required')
    
    if not args.park and not args.all:
        parser.error('Either --park or --all required')
    
    conn = sqlite3.connect(DB_PATH)
    
    if args.park:
        park_ids = [args.park]
    else:
        park_ids = get_all_park_ids(conn)
    
    if args.dry_run:
        print(f"Would classify: type={args.type or 'all'}, parks={len(park_ids)}")
        return
    
    types_to_process = ['settlement', 'deforestation'] if args.all_types else [args.type]
    
    for feature_type in types_to_process:
        print(f"\n{'='*50}")
        print(f"Classifying {feature_type}s...")
        print('='*50)
        
        total = 0
        for park_id in park_ids:
            if feature_type == 'settlement':
                total += classify_settlements_for_park(conn, park_id)
            elif feature_type == 'deforestation':
                total += classify_deforestation_for_park(conn, park_id)
        
        print(f"\nTotal {feature_type}s classified: {total}")
    
    conn.close()
    print("\nDone!")


if __name__ == '__main__':
    main()
