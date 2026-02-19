#!/usr/bin/env python3
"""
Enhanced Fire Analysis v2 - Memory-Safe Batched Processing

Supports:
- Full rebuild (default): Process all fires from 2020-06-01
- Incremental (--incremental): Process only last 14 days, merge with existing

Usage:
    python rebuild_park_fire_analysis_v2.py          # Full rebuild
    python rebuild_park_fire_analysis_v2.py --incremental  # Last 14 days only
    python rebuild_park_fire_analysis_v2.py --days 7       # Custom day range
"""

import os
import sys
import json
import sqlite3
import math
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import gc

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
BUFFER_DIR = BASE_DIR / "data" / "fire_additional_buffer"
NRT_DIR = BASE_DIR / "data" / "fire_nrt"  # NRT fire data from daily download
KEYSTONES_FILE = BASE_DIR / "data" / "keystones_with_boundaries.json"
OUTPUT_DIR = BASE_DIR / "data" / "fire_groups_v2"

GRID_SIZE = 0.03
CLUSTER_DIST_KM = 3.0
TIME_GAP_DAYS = 2
CHUNK_SIZE = 5.0
CHUNK_OVERLAP = 0.5
MAX_GROUPS_PER_BATCH = 400000

# Minimum date for fire data (UI slider start)
MIN_DATE = "2020-06-01"

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
        for poly in coords:
            if point_in_ring(lon, lat, poly[0]):
                return True
        return False
    elif geometry['type'] == 'Polygon':
        return point_in_ring(lon, lat, coords[0])
    return False

def point_in_ring(x, y, ring):
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-10) + xi):
            inside = not inside
        j = i
    return inside

def haversine(lon1, lat1, lon2, lat2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def grid_key(lon, lat):
    return (int(lon / GRID_SIZE), int(lat / GRID_SIZE))

def preindex_buffer_fires(min_date=MIN_DATE):
    global BUFFER_FIRES_BY_GRID
    log("Pre-indexing buffer fires...")
    BUFFER_FIRES_BY_GRID = defaultdict(list)
    count = 0
    
    # Load from historical buffer files
    for f in BUFFER_DIR.glob("*_buffer.json"):
        with open(f) as fp:
            try:
                data = json.load(fp)
                fires = data.get('fires', data) if isinstance(data, dict) else data
            except:
                continue
        for fire in fires:
            date = fire.get('acq_date', fire.get('date', ''))
            if date < min_date:
                continue
            lon = fire.get('longitude', fire.get('lon'))
            lat = fire.get('latitude', fire.get('lat'))
            if lon and lat:
                key = grid_key(lon, lat)
                # Normalize fire format
                BUFFER_FIRES_BY_GRID[key].append({
                    'latitude': lat, 'longitude': lon,
                    'acq_date': date,
                    'acq_time': fire.get('acq_time', fire.get('time', '')),
                    'frp': fire.get('frp', 0),
                    'confidence': fire.get('confidence', ''),
                    'brightness': fire.get('brightness', 0)
                })
                count += 1
    
    # Load from NRT daily download files
    if NRT_DIR.exists():
        for f in NRT_DIR.glob("*_nrt.json"):
            with open(f) as fp:
                try:
                    data = json.load(fp)
                    fires = data.get('fires', [])
                except:
                    continue
            for fire in fires:
                date = fire.get('date', '')
                if date < min_date:
                    continue
                lon, lat = fire.get('lon'), fire.get('lat')
                if lon and lat:
                    key = grid_key(lon, lat)
                    BUFFER_FIRES_BY_GRID[key].append({
                        'latitude': lat, 'longitude': lon,
                        'acq_date': date,
                        'acq_time': fire.get('time', ''),
                        'frp': fire.get('frp', 0),
                        'confidence': fire.get('confidence', ''),
                        'brightness': 0
                    })
                    count += 1
    
    log(f"  Indexed {count/1e6:.1f}M buffer fires")
    return count

def load_chunk_fires(conn, chunk_bbox, min_date=MIN_DATE, max_date=None):
    """Load fires for a geographic chunk with date filter."""
    west, south, east, north = chunk_bbox
    fires = []
    
    # Build date filter
    date_filter = f"acq_date >= '{min_date}'"
    if max_date:
        date_filter += f" AND acq_date <= '{max_date}'"
    
    # From database
    cursor = conn.execute(f"""
        SELECT latitude, longitude, acq_date, acq_time, confidence, frp, brightness
        FROM fire_detections
        WHERE longitude >= ? AND longitude < ? AND latitude >= ? AND latitude < ?
        AND {date_filter}
    """, (west, east, south, north))
    
    for row in cursor:
        fires.append({
            'latitude': row[0], 'longitude': row[1], 'acq_date': row[2],
            'acq_time': row[3] or '0000', 'confidence': row[4] or 'n',
            'frp': row[5] or 0, 'brightness': row[6] or 0
        })
    
    # From buffer index
    if BUFFER_FIRES_BY_GRID:
        for gx in range(int(west/GRID_SIZE), int(east/GRID_SIZE)+1):
            for gy in range(int(south/GRID_SIZE), int(north/GRID_SIZE)+1):
                for fire in BUFFER_FIRES_BY_GRID.get((gx, gy), []):
                    lon, lat = fire['longitude'], fire['latitude']
                    if west <= lon < east and south <= lat < north:
                        acq_date = fire.get('acq_date', '')
                        if acq_date >= min_date and (not max_date or acq_date <= max_date):
                            fires.append(fire)
    
    # Deduplicate
    seen = set()
    unique = []
    for f in fires:
        key = (round(f['latitude'], 5), round(f['longitude'], 5), f['acq_date'])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    
    return unique

def cluster_fires(fires):
    if not fires:
        return []
    
    sorted_fires = sorted(fires, key=lambda f: (f['acq_date'], f.get('acq_time', '0000')))
    grid = defaultdict(list)
    for i, f in enumerate(sorted_fires):
        key = grid_key(f['longitude'], f['latitude'])
        grid[key].append(i)
    
    parent = list(range(len(sorted_fires)))
    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
    
    for i, f in enumerate(sorted_fires):
        gx, gy = grid_key(f['longitude'], f['latitude'])
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                for j in grid.get((gx+dx, gy+dy), []):
                    if j <= i:
                        continue
                    f2 = sorted_fires[j]
                    d1 = datetime.strptime(f['acq_date'], '%Y-%m-%d')
                    d2 = datetime.strptime(f2['acq_date'], '%Y-%m-%d')
                    if abs((d2 - d1).days) > TIME_GAP_DAYS:
                        continue
                    dist = haversine(f['longitude'], f['latitude'], f2['longitude'], f2['latitude'])
                    if dist <= CLUSTER_DIST_KM:
                        union(i, j)
    
    clusters = defaultdict(list)
    for i, f in enumerate(sorted_fires):
        clusters[find(i)].append(f)
    
    return list(clusters.values())

def analyze_group(cluster, parks):
    if not cluster:
        return None
    
    sorted_fires = sorted(cluster, key=lambda f: f['acq_date'])
    start_date = sorted_fires[0]['acq_date']
    end_date = sorted_fires[-1]['acq_date']
    days = (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days + 1
    
    lats = [f['latitude'] for f in cluster]
    lons = [f['longitude'] for f in cluster]
    centroid = [sum(lons)/len(lons), sum(lats)/len(lats)]
    
    trajectory = []
    for f in sorted_fires:
        trajectory.append([f['longitude'], f['latitude'], f['acq_date']])
    
    if len(trajectory) >= 2:
        distance_km = sum(haversine(trajectory[i][0], trajectory[i][1], 
                                    trajectory[i+1][0], trajectory[i+1][1]) 
                         for i in range(len(trajectory)-1))
    else:
        distance_km = 0
    
    speed = distance_km / max(days, 1)
    
    if len(trajectory) >= 2:
        dx = trajectory[-1][0] - trajectory[0][0]
        dy = trajectory[-1][1] - trajectory[0][1]
        angle = math.atan2(dy, dx) * 180 / math.pi
        dirs = ['E', 'NE', 'N', 'NW', 'W', 'SW', 'S', 'SE']
        direction = dirs[int((angle + 202.5) / 45) % 8]
    else:
        direction = 'N'
    
    # Find parks
    inside_counts = defaultdict(int)
    total_fires = len(cluster)
    
    for f in cluster:
        for park_id, park in parks.items():
            if point_in_bbox(f['longitude'], f['latitude'], park['bbox_extended']):
                if point_in_polygon(f['longitude'], f['latitude'], park['geometry']):
                    inside_counts[park_id] += 1
    
    if inside_counts:
        primary_park = max(inside_counts.keys(), key=lambda p: inside_counts[p])
        affected_parks = list(inside_counts.keys())
    else:
        # Find nearest park
        min_dist = float('inf')
        primary_park = None
        for park_id, park in parks.items():
            if point_in_bbox(centroid[0], centroid[1], park['bbox_extended']):
                park_center = [(park['bbox']['west']+park['bbox']['east'])/2,
                              (park['bbox']['south']+park['bbox']['north'])/2]
                dist = haversine(centroid[0], centroid[1], park_center[0], park_center[1])
                if dist < min_dist:
                    min_dist = dist
                    primary_park = park_id
        affected_parks = [primary_park] if primary_park else []
    
    if not primary_park:
        return None
    
    pct_inside = 100 * inside_counts.get(primary_park, 0) / total_fires
    cross_border = len(affected_parks) > 1
    
    # Classify
    if pct_inside > 80 and speed < 2:
        group_type = 'management_controlled'
    elif pct_inside > 50 and speed < 5:
        group_type = 'herder_local'
    elif speed >= 10:
        group_type = 'transhumance'
    elif pct_inside < 20 and speed >= 3:
        group_type = 'external_fire'
    elif total_fires <= 5 and days <= 2:
        group_type = 'spot_fire'
    else:
        group_type = 'herder_local'
    
    return {
        'fires': total_fires, 'start_date': start_date, 'end_date': end_date,
        'days': days, 'centroid': centroid, 'distance_km': round(distance_km, 2),
        'speed_km_day': round(speed, 2), 'direction': direction,
        'primary_park': primary_park, 'affected_parks': affected_parks,
        'cross_border': cross_border, 'group_type': group_type,
        'pct_inside': round(pct_inside, 1), 'trajectory': trajectory
    }

def merge_groups_streaming(groups):
    if not groups:
        return []
    
    parent = list(range(len(groups)))
    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
    
    # Build fire->group index (streaming, limited memory)
    fire_to_groups = defaultdict(list)
    for i, g in enumerate(groups):
        for fire in g[:100]:  # Sample for merge detection
            key = (round(fire['latitude'], 4), round(fire['longitude'], 4), fire['acq_date'])
            fire_to_groups[key].append(i)
    
    for key, group_ids in fire_to_groups.items():
        for j in range(1, len(group_ids)):
            union(group_ids[0], group_ids[j])
    
    del fire_to_groups
    gc.collect()
    
    merged = defaultdict(list)
    for i, g in enumerate(groups):
        root = find(i)
        merged[root].extend(g)
    
    return list(merged.values())

def load_existing_groups(park_id):
    """Load existing groups for a park (for incremental mode)."""
    output_file = OUTPUT_DIR / f"{park_id}.json"
    if output_file.exists():
        with open(output_file) as f:
            return json.load(f)
    return []

def merge_incremental_groups(existing_groups, new_groups, cutoff_date):
    """Merge new groups with existing, removing stale overlapping groups."""
    # Keep existing groups that ended before cutoff
    kept = [g for g in existing_groups if g['end_date'] < cutoff_date]
    
    # Add all new groups
    kept.extend(new_groups)
    
    return kept

def main():
    parser = argparse.ArgumentParser(description="Fire Analysis v2")
    parser.add_argument("--incremental", action="store_true", 
                        help="Incremental mode: process last 14 days and merge")
    parser.add_argument("--days", type=int, default=14,
                        help="Days to process in incremental mode (default: 14)")
    args = parser.parse_args()
    
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    
    parks = load_parks()
    log(f"Loaded {len(parks)} parks")
    
    conn = sqlite3.connect(DB_PATH)
    
    # Determine date range
    if args.incremental:
        max_date = datetime.now().strftime('%Y-%m-%d')
        min_date = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
        cutoff_date = min_date
        log(f"INCREMENTAL MODE: Processing {min_date} to {max_date}")
    else:
        min_date = MIN_DATE
        max_date = None
        cutoff_date = None
        log(f"FULL REBUILD: Processing from {min_date}")
    
    # Index buffer fires
    preindex_buffer_fires(min_date)
    
    log("")
    log("=" * 60)
    log("Enhanced Fire Analysis v2 - Optimized")
    log("=" * 60)
    
    # Generate chunks
    chunks = []
    for lon in range(-20, 55, int(CHUNK_SIZE)):
        for lat in range(-35, 40, int(CHUNK_SIZE)):
            chunks.append((lon - CHUNK_OVERLAP, lat - CHUNK_OVERLAP,
                          lon + CHUNK_SIZE + CHUNK_OVERLAP, lat + CHUNK_SIZE + CHUNK_OVERLAP))
    
    groups_by_park = defaultdict(list)
    weekly_counts = defaultdict(lambda: defaultdict(int))
    all_groups = []
    batch_num = 0
    
    for i, chunk_bbox in enumerate(chunks):
        fires = load_chunk_fires(conn, chunk_bbox, min_date, max_date)
        if not fires:
            continue
        
        clusters = cluster_fires(fires)
        log(f"[{i+1}/{len(chunks)}] {len(fires)} fires -> {len(clusters)} groups (batch: {len(all_groups)})")
        
        all_groups.extend(clusters)
        
        if len(all_groups) >= MAX_GROUPS_PER_BATCH:
            batch_num += 1
            log(f"Batch {batch_num}: Merging {len(all_groups)} groups...")
            
            batch_groups = all_groups
            all_groups = []
            gc.collect()
            
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
            log(f"  Batch {batch_num} complete")
    
    # Final batch
    if all_groups:
        batch_num += 1
        log(f"Final batch {batch_num}: Processing {len(all_groups)} groups...")
        
        merged = merge_groups_streaming(all_groups)
        del all_groups
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
    
    # Save - with incremental merge if needed
    log("Saving groups to JSON...")
    for park_id in parks:
        new_groups = groups_by_park.get(park_id, [])
        
        if args.incremental:
            existing = load_existing_groups(park_id)
            final_groups = merge_incremental_groups(existing, new_groups, cutoff_date)
        else:
            final_groups = new_groups
        
        output_file = OUTPUT_DIR / f"{park_id}.json"
        # Atomic write: write to temp file then rename
        temp_file = output_file.with_suffix('.json.tmp')
        with open(temp_file, 'w') as f:
            json.dump(final_groups, f)
        temp_file.rename(output_file)
    
    log("Saving weekly counts to database...")
    conn.execute("CREATE TABLE IF NOT EXISTS park_fire_weekly (park_id TEXT, week_start TEXT, fire_count INTEGER, PRIMARY KEY (park_id, week_start))")
    
    if args.incremental:
        # Only update weeks we processed
        for park_id, weeks in weekly_counts.items():
            for week, count in weeks.items():
                conn.execute("INSERT OR REPLACE INTO park_fire_weekly VALUES (?, ?, ?)", 
                            (park_id, week, count))
    else:
        conn.execute("DELETE FROM park_fire_weekly")
        for park_id, weeks in weekly_counts.items():
            for week, count in weeks.items():
                conn.execute("INSERT INTO park_fire_weekly VALUES (?, ?, ?)", 
                            (park_id, week, count))
    conn.commit()
    
    total_groups = sum(len(load_existing_groups(p) if args.incremental else groups_by_park.get(p, [])) 
                       for p in parks)
    new_count = sum(len(g) for g in groups_by_park.values())
    
    log("")
    log("=" * 60)
    log(f"Complete!")
    log(f"  Mode: {'INCREMENTAL' if args.incremental else 'FULL'}")
    log(f"  Batches: {batch_num}")
    log(f"  New groups: {new_count}")
    log(f"  Parks: {len([p for p in parks if groups_by_park.get(p)])}")
    
    conn.close()

if __name__ == "__main__":
    main()
