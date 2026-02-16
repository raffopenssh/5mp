#!/usr/bin/env python3
"""
Enhanced Fire Analysis v2 - Memory-Safe Batched Processing

Key change: Smaller batches (400k) and streaming merge to avoid OOM during merge phase.
"""

import os
import sys
import json
import sqlite3
import math
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import gc

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
BUFFER_DIR = BASE_DIR / "data" / "fire_additional_buffer"
KEYSTONES_FILE = BASE_DIR / "data" / "keystones_with_boundaries.json"
OUTPUT_DIR = BASE_DIR / "data" / "fire_groups_v2"

GRID_SIZE = 0.03
CLUSTER_DIST_KM = 3.0
TIME_GAP_DAYS = 2
CHUNK_SIZE = 5.0
CHUNK_OVERLAP = 0.5
MAX_GROUPS_PER_BATCH = 400000  # Reduced for merge safety

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

BUFFER_FIRES_BY_GRID = None

def load_parks():
    with open(KEYSTONES_FILE) as f:
        data = json.load(f)
    parks = {}
    for p in data:
        park_id = p['id']
        if 'geometry' not in p:
            continue
        bbox = get_bbox_from_geometry(p['geometry'])
        if not bbox:
            continue
        parks[park_id] = {
            'id': park_id, 'name': p.get('name', park_id),
            'country': p.get('country', ''), 'geometry': p['geometry'],
            'bbox': bbox, 'bbox_extended': extend_bbox(bbox, 50)
        }
    return parks

def get_bbox_from_geometry(geom):
    coords = []
    def extract_coords(obj):
        if isinstance(obj, list):
            if len(obj) >= 2 and isinstance(obj[0], (int, float)):
                coords.append((obj[0], obj[1]))
            else:
                for item in obj:
                    extract_coords(item)
    extract_coords(geom.get('coordinates', []))
    if not coords:
        return None
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return {'west': min(lons), 'east': max(lons), 'south': min(lats), 'north': max(lats)}

def extend_bbox(bbox, km):
    deg = km / 111.0
    return {'west': bbox['west'] - deg, 'east': bbox['east'] + deg,
            'south': bbox['south'] - deg, 'north': bbox['north'] + deg}

def point_in_bbox(lon, lat, bbox):
    return bbox['west'] <= lon <= bbox['east'] and bbox['south'] <= lat <= bbox['north']

def point_in_polygon(lon, lat, geometry):
    coords = geometry.get('coordinates', [])
    if geometry['type'] == 'MultiPolygon':
        for polygon in coords:
            if point_in_polygon_ring(lon, lat, polygon[0]):
                return True
        return False
    elif geometry['type'] == 'Polygon':
        return point_in_polygon_ring(lon, lat, coords[0])
    return False

def point_in_polygon_ring(lon, lat, ring):
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

def haversine(lon1, lat1, lon2, lat2):
    R = 6371
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def fire_key(f):
    return (round(f['lat'], 4), round(f['lon'], 4), f['date'])

def coarse_grid_key(lon, lat, size=1.0):
    return (int(lon / size), int(lat / size))

def fine_grid_key(lon, lat):
    return (int(lon / GRID_SIZE), int(lat / GRID_SIZE))

def neighbor_cells(gx, gy):
    for dx in [-1, 0, 1]:
        for dy in [-1, 0, 1]:
            yield (gx + dx, gy + dy)

def load_buffer_fires_index():
    global BUFFER_FIRES_BY_GRID
    if BUFFER_FIRES_BY_GRID is not None:
        return
    
    log("Pre-indexing buffer fires...")
    BUFFER_FIRES_BY_GRID = defaultdict(list)
    count = 0
    
    for buffer_file in sorted(BUFFER_DIR.glob("*_buffer.json")):
        try:
            with open(buffer_file) as f:
                data = json.load(f)
            for f_data in data.get('fires', []):
                lat = f_data.get('latitude') or f_data.get('lat')
                lon = f_data.get('longitude') or f_data.get('lon')
                date = f_data.get('acq_date') or f_data.get('date')
                if lat and lon and date:
                    gkey = coarse_grid_key(lon, lat)
                    BUFFER_FIRES_BY_GRID[gkey].append({
                        'lat': lat, 'lon': lon, 'date': date,
                        'time': str(f_data.get('acq_time', '')),
                        'frp': f_data.get('frp', 0)
                    })
                    count += 1
        except:
            pass
    
    log(f"  Indexed {count} buffer fires in {len(BUFFER_FIRES_BY_GRID)} grid cells")

def get_buffer_fires_for_bbox(bbox):
    fires = []
    for gx in range(int(bbox['west']), int(bbox['east']) + 2):
        for gy in range(int(bbox['south']), int(bbox['north']) + 2):
            for f in BUFFER_FIRES_BY_GRID.get((gx, gy), []):
                if bbox['south'] <= f['lat'] <= bbox['north'] and bbox['west'] <= f['lon'] <= bbox['east']:
                    fires.append(f)
    return fires

def load_fires_for_region(conn, bbox):
    fires = []
    cursor = conn.execute("""
        SELECT latitude, longitude, acq_date, acq_time, frp
        FROM fire_detections
        WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
    """, (bbox['south'], bbox['north'], bbox['west'], bbox['east']))
    
    for row in cursor:
        lat, lon, date, time, frp = row
        if date:
            fires.append({'lat': lat, 'lon': lon, 'date': date, 'time': time or '', 'frp': frp or 0})
    return fires

def cluster_fires_gridded(fires):
    if not fires:
        return []
    
    fires = sorted(fires, key=lambda f: f['date'])
    n = len(fires)
    parent = list(range(n))
    
    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root
    
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
    
    grid_index = defaultdict(lambda: defaultdict(list))
    for i, f in enumerate(fires):
        gkey = fine_grid_key(f['lon'], f['lat'])
        grid_index[gkey][f['date']].append(i)
    
    all_dates = sorted(set(f['date'] for f in fires))
    date_to_idx = {d: i for i, d in enumerate(all_dates)}
    
    for i, fire in enumerate(fires):
        gx, gy = fine_grid_key(fire['lon'], fire['lat'])
        fire_date_idx = date_to_idx[fire['date']]
        
        for neighbor in neighbor_cells(gx, gy):
            if neighbor not in grid_index:
                continue
            for d_offset in range(TIME_GAP_DAYS + 1):
                check_date_idx = fire_date_idx - d_offset
                if check_date_idx < 0:
                    continue
                check_date = all_dates[check_date_idx]
                for j in grid_index[neighbor][check_date]:
                    if j >= i:
                        continue
                    other = fires[j]
                    dist = haversine(fire['lon'], fire['lat'], other['lon'], other['lat'])
                    if dist <= CLUSTER_DIST_KM:
                        union(i, j)
    
    clusters_dict = defaultdict(list)
    for i in range(n):
        clusters_dict[find(i)].append(fires[i])
    
    return list(clusters_dict.values())

def process_chunk(conn, bbox):
    db_fires = load_fires_for_region(conn, bbox)
    buffer_fires = get_buffer_fires_for_bbox(bbox)
    
    seen = set()
    fires = []
    for f in db_fires + buffer_fires:
        key = fire_key(f)
        if key not in seen:
            seen.add(key)
            fires.append(f)
    
    if len(fires) < 10:
        return []
    
    clusters = cluster_fires_gridded(fires)
    
    # Return only fire lists (not keys) to save memory
    result = [c for c in clusters if len(c) >= 2]
    
    del fires, db_fires, buffer_fires, clusters
    gc.collect()
    
    return result

def merge_groups_streaming(chunk_groups):
    """Memory-efficient merge using streaming approach."""
    if not chunk_groups:
        return []
    
    log(f"    Building fire index for {len(chunk_groups)} groups...")
    
    # Build fire -> group index incrementally
    fire_to_group = {}  # fire_key -> canonical group index
    
    n = len(chunk_groups)
    parent = list(range(n))
    
    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root
    
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
    
    # Process each group
    for gi, group in enumerate(chunk_groups):
        group_fires = set()
        for f in group:
            key = fire_key(f)
            group_fires.add(key)
            
            if key in fire_to_group:
                # This fire already in another group - merge
                union(gi, fire_to_group[key])
            else:
                fire_to_group[key] = gi
        
        if gi % 100000 == 0 and gi > 0:
            log(f"      Indexed {gi}/{n} groups...")
    
    # Clear fire_to_group to save memory
    del fire_to_group
    gc.collect()
    
    log(f"    Collecting merged groups...")
    
    # Collect by root
    merged_dict = defaultdict(list)
    for i in range(n):
        merged_dict[find(i)].append(i)
    
    # Build final merged clusters
    merged = []
    for group_indices in merged_dict.values():
        all_fires = {}
        for gi in group_indices:
            for f in chunk_groups[gi]:
                key = fire_key(f)
                if key not in all_fires:
                    all_fires[key] = f
        merged.append(list(all_fires.values()))
    
    log(f"    {n} -> {len(merged)} merged groups")
    return merged

def analyze_group(cluster, parks):
    if not cluster or len(cluster) < 2:
        return None
    
    cluster = sorted(cluster, key=lambda f: (f['date'], f.get('time', '')))
    start_date, end_date = cluster[0]['date'], cluster[-1]['date']
    days = (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days + 1
    
    lats = [f['lat'] for f in cluster]
    lons = [f['lon'] for f in cluster]
    centroid = (sum(lons)/len(lons), sum(lats)/len(lats))
    
    total_dist = sum(haversine(cluster[i-1]['lon'], cluster[i-1]['lat'], 
                               cluster[i]['lon'], cluster[i]['lat']) for i in range(1, len(cluster)))
    speed = total_dist / max(days, 1)
    
    if len(cluster) >= 2:
        dx, dy = cluster[-1]['lon'] - cluster[0]['lon'], cluster[-1]['lat'] - cluster[0]['lat']
        angle = math.degrees(math.atan2(dy, dx))
        if angle < 0: angle += 360
        direction = ['E', 'NE', 'N', 'NW', 'W', 'SW', 'S', 'SE'][int((angle + 22.5) / 45) % 8]
    else:
        direction = 'stationary'
    
    affected = {}
    for f in cluster:
        for park_id, park in parks.items():
            if not point_in_bbox(f['lon'], f['lat'], park['bbox_extended']):
                continue
            if park_id not in affected:
                affected[park_id] = {'inside': 0, 'buffer': 0}
            if point_in_polygon(f['lon'], f['lat'], park['geometry']):
                affected[park_id]['inside'] += 1
            else:
                affected[park_id]['buffer'] += 1
    
    if not affected:
        return None
    
    primary_park = max(affected.keys(), key=lambda p: (affected[p]['inside'], -affected[p]['buffer']))
    pct_inside = (affected[primary_park]['inside'] / len(cluster)) * 100
    
    if days <= 2 and speed < 2: group_type = 'spot_fire'
    elif speed > 15: group_type = 'transhumance_fast'
    elif speed > 8: group_type = 'transhumance'
    elif speed > 4: group_type = 'herder_local'
    elif pct_inside > 80 and days <= 3: group_type = 'management_controlled'
    elif pct_inside > 80: group_type = 'management_extended'
    elif pct_inside > 50: group_type = 'mixed_origin'
    else: group_type = 'external_fire'
    
    return {
        'fires': len(cluster), 'start_date': start_date, 'end_date': end_date,
        'days': days, 'centroid': centroid, 'distance_km': round(total_dist, 2),
        'speed_km_day': round(speed, 2), 'direction': direction,
        'primary_park': primary_park, 'affected_parks': list(affected.keys()),
        'cross_border': len(affected) > 1, 'group_type': group_type,
        'pct_inside': round(pct_inside, 1),
        'trajectory': [(f['lon'], f['lat'], f['date']) for f in cluster]
    }

def main():
    log("=" * 60)
    log("Enhanced Fire Analysis v2 - Memory-Safe Batches")
    log("=" * 60)
    
    parks = load_parks()
    log(f"Loaded {len(parks)} parks")
    
    load_buffer_fires_index()
    
    conn = sqlite3.connect(DB_PATH)
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    for f in OUTPUT_DIR.glob("*.json"):
        f.unlink()
    
    chunks = []
    for lon_start in range(-20, 55, int(CHUNK_SIZE)):
        for lat_start in range(-35, 40, int(CHUNK_SIZE)):
            if lon_start < -15 and lat_start < -10:
                continue
            if lon_start < -10 and lat_start > 20:
                continue
            chunks.append({
                'west': lon_start - CHUNK_OVERLAP,
                'east': lon_start + CHUNK_SIZE + CHUNK_OVERLAP,
                'south': lat_start - CHUNK_OVERLAP,
                'north': lat_start + CHUNK_SIZE + CHUNK_OVERLAP
            })
    
    log(f"Processing {len(chunks)} chunks (batch limit: {MAX_GROUPS_PER_BATCH})")
    
    groups_by_park = defaultdict(list)
    weekly_counts = defaultdict(lambda: defaultdict(int))
    
    batch_num = 0
    batch_groups = []
    
    for i, chunk in enumerate(chunks, 1):
        chunk_groups = process_chunk(conn, chunk)
        
        if chunk_groups:
            log(f"[{i}/{len(chunks)}] {len(chunk_groups)} groups (batch: {len(batch_groups) + len(chunk_groups)})")
            batch_groups.extend(chunk_groups)
        
        if len(batch_groups) >= MAX_GROUPS_PER_BATCH:
            batch_num += 1
            log(f"Batch {batch_num}: Processing {len(batch_groups)} groups...")
            
            merged = merge_groups_streaming(batch_groups)
            
            # Clear batch groups first
            del batch_groups
            gc.collect()
            batch_groups = []
            
            # Analyze
            log(f"  Analyzing {len(merged)} merged clusters...")
            for cluster in merged:
                result = analyze_group(cluster, parks)
                if result:
                    groups_by_park[result['primary_park']].append(result)
                    for park_id in result['affected_parks']:
                        fire_date = datetime.strptime(result['start_date'], '%Y-%m-%d')
                        week_start = fire_date - timedelta(days=fire_date.weekday())
                        weekly_counts[park_id][week_start.strftime('%Y-%m-%d')] += result['fires']
            
            del merged
            gc.collect()
            log(f"  Batch {batch_num} complete")
    
    # Final batch
    if batch_groups:
        batch_num += 1
        log(f"Final batch {batch_num}: Processing {len(batch_groups)} groups...")
        
        merged = merge_groups_streaming(batch_groups)
        del batch_groups
        gc.collect()
        
        log(f"  Analyzing {len(merged)} merged clusters...")
        for cluster in merged:
            result = analyze_group(cluster, parks)
            if result:
                groups_by_park[result['primary_park']].append(result)
                for park_id in result['affected_parks']:
                    fire_date = datetime.strptime(result['start_date'], '%Y-%m-%d')
                    week_start = fire_date - timedelta(days=fire_date.weekday())
                    weekly_counts[park_id][week_start.strftime('%Y-%m-%d')] += result['fires']
        
        del merged
        gc.collect()
    
    # Save
    log("Saving groups to JSON...")
    for park_id, park_groups in groups_by_park.items():
        output_file = OUTPUT_DIR / f"{park_id}.json"
        with open(output_file, 'w') as f:
            json.dump(park_groups, f)
    
    log("Saving weekly counts to database...")
    conn.execute("CREATE TABLE IF NOT EXISTS park_fire_weekly (park_id TEXT, week_start TEXT, fire_count INTEGER, PRIMARY KEY (park_id, week_start))")
    conn.execute("DELETE FROM park_fire_weekly")
    for park_id, weeks in weekly_counts.items():
        for week, count in weeks.items():
            conn.execute("INSERT INTO park_fire_weekly VALUES (?, ?, ?)", (park_id, week, count))
    conn.commit()
    
    total_groups = sum(len(g) for g in groups_by_park.values())
    cross_border = sum(1 for groups in groups_by_park.values() for g in groups if g['cross_border'])
    
    log("")
    log("=" * 60)
    log(f"Complete!")
    log(f"  Batches: {batch_num}")
    log(f"  Groups: {total_groups}")
    log(f"  Cross-border: {cross_border} ({100*cross_border/max(total_groups,1):.1f}%)")
    log(f"  Parks: {len(groups_by_park)}")
    
    conn.close()

if __name__ == "__main__":
    main()
