#!/usr/bin/env python3
"""
STEP 3: Precompute Fire Narratives for Cache

Reads Step 2 output (data/fire_trajectories/*.json) and builds
fire_narrative_cache entries in the format expected by the Go API.
"""

import json
import sqlite3
import argparse
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / 'data'
TRAJ_DIR = DATA_DIR / 'fire_trajectories'
DB_PATH = BASE_DIR / 'db.sqlite3'

MIN_DATE = '2020-01-01'

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def load_parks():
    """Load park info from keystones file."""
    parks = {}
    ks_file = DATA_DIR / 'keystones_with_boundaries.json'
    if ks_file.exists():
        with open(ks_file) as f:
            for p in json.load(f):
                parks[p['id']] = {
                    'name': p.get('name', p['id']),
                    'area_km2': p.get('area_km2', 0),
                    'country': p.get('country', '')
                }
    return parks

def build_narrative_cache(traj_data, park_info):
    """Build fire_narrative_cache JSON from trajectory data."""
    
    park_id = traj_data['park_id']
    park_name = traj_data.get('park_name', park_info.get('name', park_id))
    trajectories = traj_data.get('trajectories', [])
    
    if not trajectories:
        return None
    
    # Group trajectories by year
    by_year = defaultdict(list)
    for t in trajectories:
        start = t.get('start_date', '')
        if start:
            year = int(start[:4])
            by_year[year].append(t)
    
    # Build summary stats
    total_fires = sum(t.get('fires', 0) for t in trajectories)
    total_groups = len(trajectories)
    
    # Count by type
    type_counts = defaultdict(int)
    for t in trajectories:
        gtype = t.get('group_type', 'unknown')
        type_counts[gtype] += 1
    
    # Cross-border count
    cross_border = sum(1 for t in trajectories if t.get('cross_border', False))
    
    # Build narratives list (FireGroupStory format)
    narratives = []
    for idx, t in enumerate(trajectories):
        start_date = t.get('start_date', '')
        end_date = t.get('end_date', '')
        
        # Skip if before MIN_DATE
        if start_date < MIN_DATE:
            continue
        
        year = int(start_date[:4]) if start_date else None
        
        # Origin/destination descriptions
        origin = t.get('origin', {})
        dest = t.get('destination', {})
        origin_place = origin.get('place', {})
        dest_place = dest.get('place', {})
        
        origin_desc = ""
        if origin_place:
            origin_desc = f"Near {origin_place.get('name', 'unknown')} ({origin_place.get('type', '')}, {origin_place.get('dist_km', 0):.1f} km)"
        
        dest_desc = ""
        if dest_place:
            dest_desc = f"Near {dest_place.get('name', 'unknown')} ({dest_place.get('type', '')}, {dest_place.get('dist_km', 0):.1f} km)"
        
        # Days calculation
        days = t.get('days', 1)
        fires = t.get('fires', 0)
        
        story = {
            'group_num': idx + 1,
            'feature_id': f"{park_id}_grp_{idx}",
            'year': year,
            'origin_desc': origin_desc,
            'dest_desc': dest_desc,
            'entry_date': start_date,
            'last_inside': end_date,
            'days_inside': days,
            'fires_inside': fires,
            'outcome': t.get('group_type', 'unknown'),
            'narrative': t.get('narrative', ''),
            'nearby_places': [],
            'rivers_crossed': []
        }
        
        # Add nearby places
        if origin_place.get('name'):
            story['nearby_places'].append(origin_place['name'])
        if dest_place.get('name') and dest_place.get('name') != origin_place.get('name'):
            story['nearby_places'].append(dest_place['name'])
        
        narratives.append(story)
    
    # Build year summaries for trend
    years = []
    for year in sorted(by_year.keys()):
        year_trajs = by_year[year]
        year_fires = sum(t.get('fires', 0) for t in year_trajs)
        year_groups = len(year_trajs)
        
        # Count managed fires
        managed = sum(1 for t in year_trajs if 'management' in t.get('group_type', ''))
        
        years.append({
            'year': year,
            'total_fires': year_fires,
            'total_groups': year_groups,
            'managed_groups': managed,
            'response_rate': round(managed / year_groups * 100, 1) if year_groups > 0 else 0
        })
    
    # Find worst/best years
    worst_year = max(years, key=lambda y: y['total_groups']) if years else None
    best_year = min(years, key=lambda y: y['total_groups']) if years else None
    
    # Calculate trend
    if len(years) >= 2:
        first_half = years[:len(years)//2]
        second_half = years[len(years)//2:]
        avg_first = sum(y['total_groups'] for y in first_half) / len(first_half)
        avg_second = sum(y['total_groups'] for y in second_half) / len(second_half)
        if avg_second > avg_first * 1.1:
            trend_direction = 'increasing'
        elif avg_second < avg_first * 0.9:
            trend_direction = 'decreasing'
        else:
            trend_direction = 'stable'
    else:
        trend_direction = 'stable'
    
    # Build summary text
    date_range = traj_data.get('date_range', {})
    from_date = date_range.get('from', MIN_DATE)
    to_date = date_range.get('to', datetime.now().strftime('%Y-%m-%d'))
    
    summary_parts = [
        f"From {from_date[:7]} to {to_date[:7]}",
        f"{park_name} experienced {total_fires:,} fire detections across {total_groups:,} fire groups."
    ]
    
    # Add type breakdown
    if type_counts:
        type_str = []
        for gtype, count in sorted(type_counts.items(), key=lambda x: -x[1])[:3]:
            pct = count / total_groups * 100
            type_str.append(f"{count} {gtype} ({pct:.1f}%)")
        if type_str:
            summary_parts.append("Fire types: " + ", ".join(type_str) + ".")
    
    if cross_border > 0:
        summary_parts.append(f"{cross_border} groups ({cross_border/total_groups*100:.1f}%) crossed park boundaries.")
    
    summary = " ".join(summary_parts)
    
    # Build final cache structure
    cache_data = {
        'park_id': park_id,
        'park_name': park_name,
        'year': 0,  # All years
        'summary': summary,
        'narratives': narratives,
        'key_places': [],
        'hotspots': [],
        'trend': {
            'years': years,
            'trend_direction': trend_direction,
            'avg_response_rate': sum(y['response_rate'] for y in years) / len(years) if years else 0,
            'worst_year': worst_year['year'] if worst_year else 0,
            'worst_year_groups': worst_year['total_groups'] if worst_year else 0,
            'best_year': best_year['year'] if best_year else 0,
            'best_year_rate': best_year['response_rate'] if best_year else 0,
            'narrative': f"Fire activity is {trend_direction} over the period."
        },
        'response_rate': sum(y['response_rate'] for y in years) / len(years) if years else 0,
        'total_fires': total_fires,
        'peak_month': ''
    }
    
    return cache_data

def main():
    global MIN_DATE
    
    parser = argparse.ArgumentParser(description='Step 3: Precompute Fire Narratives')
    parser.add_argument('--park', help='Process single park')
    parser.add_argument('--from-date', default=MIN_DATE, help='Minimum date (YYYY-MM-DD)')
    args = parser.parse_args()
    
    MIN_DATE = args.from_date
    
    log("=" * 60)
    log(f"STEP 3: PRECOMPUTE FIRE NARRATIVES - from {MIN_DATE}")
    log("=" * 60)
    
    # Load parks
    parks = load_parks()
    log(f"Loaded {len(parks)} parks")
    
    # Connect to DB
    conn = sqlite3.connect(str(DB_PATH))
    
    # Get trajectory files
    if args.park:
        traj_files = [TRAJ_DIR / f"{args.park}.json"]
    else:
        traj_files = sorted(TRAJ_DIR.glob("*.json"))
    
    log(f"Processing {len(traj_files)} parks...")
    
    processed = 0
    total_narratives = 0
    
    for i, traj_file in enumerate(traj_files):
        park_id = traj_file.stem
        
        if not traj_file.exists():
            log(f"  [{i+1}/{len(traj_files)}] {park_id}: No trajectory file")
            continue
        
        try:
            with open(traj_file) as f:
                traj_data = json.load(f)
            
            park_info = parks.get(park_id, {'name': park_id})
            cache_data = build_narrative_cache(traj_data, park_info)
            
            if cache_data:
                # Store in database
                narrative_json = json.dumps(cache_data)
                
                conn.execute("""
                    INSERT OR REPLACE INTO fire_narrative_cache 
                    (park_id, narrative_json, computed_at, from_year, to_year)
                    VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
                """, (park_id, narrative_json, 2020, 2026))
                conn.commit()
                
                num_narratives = len(cache_data.get('narratives', []))
                total_narratives += num_narratives
                processed += 1
                
                log(f"  [{i+1}/{len(traj_files)}] {park_id}: {num_narratives} narratives")
            else:
                log(f"  [{i+1}/{len(traj_files)}] {park_id}: No data")
                
        except Exception as e:
            log(f"  [{i+1}/{len(traj_files)}] {park_id}: ERROR - {e}")
    
    conn.close()
    
    log("")
    log(f"COMPLETE: {processed} parks, {total_narratives} narratives cached")

if __name__ == '__main__':
    main()
