#!/usr/bin/env python3
"""
Precompute all narratives and export to JSON for production.
Uses trajectory JSON files as source.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'
TRAJ_DIR = DATA_DIR / 'fire_trajectories'
EXPORT_DIR = DATA_DIR / 'export'

def load_climate():
    climate_file = DATA_DIR / 'climate' / 'park_climate.json'
    if climate_file.exists():
        with open(climate_file) as f:
            return json.load(f)
    return {}

def precompute_fire_narratives(conn, climate_data):
    """Generate fire narratives from trajectory JSONs"""
    print("\n[1] Fire Narratives")
    print("-" * 40)
    
    narratives = {}
    
    for json_file in sorted(TRAJ_DIR.glob('*.json')):
        park_id = json_file.stem
        with open(json_file) as f:
            trajectories = json.load(f)
        
        if not trajectories:
            continue
        
        # Group by year
        by_year = defaultdict(list)
        for t in trajectories:
            by_year[t.get('year', 0)].append(t)
        
        # Build summary
        total_fires = sum(t.get('fires_inside', 0) for t in trajectories)
        total_groups = len(trajectories)
        
        stopped = sum(1 for t in trajectories if t.get('outcome') == 'STOPPED_INSIDE')
        transited = sum(1 for t in trajectories if t.get('outcome') == 'TRANSITED')
        
        # Classification breakdown
        class_counts = defaultdict(int)
        for t in trajectories:
            class_counts[t.get('classification', 'unknown')] += 1
        
        # Climate context
        park_climate = climate_data.get(park_id, {})
        
        narratives[park_id] = {
            'park_id': park_id,
            'total_fires': total_fires,
            'total_groups': total_groups,
            'stopped_inside': stopped,
            'transited': transited,
            'response_rate': round(stopped / total_groups * 100, 1) if total_groups else 0,
            'classification_breakdown': dict(class_counts),
            'climate_zone': park_climate.get('climate_zone'),
            'dry_season': park_climate.get('dry_season'),
            'years': sorted(by_year.keys()),
            'trajectories': trajectories
        }
        
        print(f"  {park_id}: {total_groups} groups, {total_fires} fires")
    
    # Export
    EXPORT_DIR.mkdir(exist_ok=True)
    with open(EXPORT_DIR / 'fire_narratives.json', 'w') as f:
        json.dump(narratives, f)
    
    print(f"\nExported fire narratives for {len(narratives)} parks")
    return narratives

def precompute_settlement_narratives(conn):
    """Generate settlement narratives"""
    print("\n[2] Settlement Narratives")
    print("-" * 40)
    
    cursor = conn.execute('''
        SELECT park_id, id, lat, lon, area_m2, population_est, 
               nearest_place, classification, classification_confidence, narrative
        FROM park_settlements
        ORDER BY park_id, population_est DESC
    ''')
    
    by_park = defaultdict(list)
    for row in cursor:
        by_park[row[0]].append({
            'id': row[1],
            'lat': row[2],
            'lon': row[3],
            'area_m2': row[4],
            'population': row[5],
            'nearest_place': row[6],
            'classification': row[7],
            'confidence': row[8],
            'narrative': row[9]
        })
    
    narratives = {}
    for park_id, settlements in by_park.items():
        total_pop = sum(s['population'] or 0 for s in settlements)
        total_area = sum(s['area_m2'] or 0 for s in settlements)
        
        class_counts = defaultdict(int)
        for s in settlements:
            class_counts[s.get('classification') or 'unknown'] += 1
        
        narratives[park_id] = {
            'park_id': park_id,
            'settlement_count': len(settlements),
            'total_population': total_pop,
            'total_area_m2': total_area,
            'classification_breakdown': dict(class_counts),
            'settlements': settlements
        }
        
        print(f"  {park_id}: {len(settlements)} settlements, pop {total_pop}")
    
    with open(EXPORT_DIR / 'settlement_narratives.json', 'w') as f:
        json.dump(narratives, f)
    
    print(f"\nExported settlement narratives for {len(narratives)} parks")
    return narratives

def precompute_deforestation_narratives(conn):
    """Generate deforestation narratives"""
    print("\n[3] Deforestation Narratives")
    print("-" * 40)
    
    cursor = conn.execute('''
        SELECT park_id, id, year, area_km2, lat, lon,
               classification, classification_confidence, narrative, pattern_type
        FROM deforestation_events
        ORDER BY park_id, year DESC
    ''')
    
    by_park = defaultdict(list)
    for row in cursor:
        by_park[row[0]].append({
            'id': row[1],
            'year': row[2],
            'area_km2': row[3],
            'lat': row[4],
            'lon': row[5],
            'classification': row[6],
            'confidence': row[7],
            'narrative': row[8],
            'pattern_type': row[9]
        })
    
    narratives = {}
    for park_id, events in by_park.items():
        total_area = sum(e['area_km2'] or 0 for e in events)
        years = sorted(set(e['year'] for e in events if e['year']))
        
        class_counts = defaultdict(int)
        area_by_class = defaultdict(float)
        for e in events:
            cls = e.get('classification') or 'unknown'
            class_counts[cls] += 1
            area_by_class[cls] += e.get('area_km2', 0)
        
        narratives[park_id] = {
            'park_id': park_id,
            'event_count': len(events),
            'total_area_km2': round(total_area, 2),
            'years': years,
            'classification_breakdown': dict(class_counts),
            'area_by_classification': {k: round(v, 2) for k, v in area_by_class.items()},
            'events': events
        }
        
        print(f"  {park_id}: {len(events)} events, {total_area:.1f} km²")
    
    with open(EXPORT_DIR / 'deforestation_narratives.json', 'w') as f:
        json.dump(narratives, f)
    
    print(f"\nExported deforestation narratives for {len(narratives)} parks")
    return narratives

def main():
    print("=" * 50)
    print("Precompute All Narratives")
    print("=" * 50)
    print(f"Started: {datetime.now().isoformat()}")
    
    conn = sqlite3.connect(DB_PATH)
    climate_data = load_climate()
    
    fire_narratives = precompute_fire_narratives(conn, climate_data)
    settlement_narratives = precompute_settlement_narratives(conn)
    deforestation_narratives = precompute_deforestation_narratives(conn)
    
    # Summary export
    summary = {
        'generated_at': datetime.now().isoformat(),
        'fire_parks': len(fire_narratives),
        'settlement_parks': len(settlement_narratives),
        'deforestation_parks': len(deforestation_narratives),
        'total_fire_groups': sum(n['total_groups'] for n in fire_narratives.values()),
        'total_settlements': sum(n['settlement_count'] for n in settlement_narratives.values()),
        'total_deforestation_events': sum(n['event_count'] for n in deforestation_narratives.values())
    }
    
    with open(EXPORT_DIR / 'narrative_summary.json', 'w') as f:
        json.dump(summary, f, indent=2)
    
    conn.close()
    
    print("\n" + "=" * 50)
    print("Summary:")
    print(f"  Fire narratives: {summary['fire_parks']} parks, {summary['total_fire_groups']} groups")
    print(f"  Settlement narratives: {summary['settlement_parks']} parks, {summary['total_settlements']} settlements")
    print(f"  Deforestation narratives: {summary['deforestation_parks']} parks, {summary['total_deforestation_events']} events")
    print(f"\nCompleted: {datetime.now().isoformat()}")

if __name__ == '__main__':
    main()
