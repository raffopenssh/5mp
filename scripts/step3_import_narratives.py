#!/usr/bin/env python3
"""
STEP 3: Import Pre-computed Narratives from JSON files

Imports the rich narrative data from:
- data/settlement_events/*.json -> park_settlements table
- data/deforestation_events/*.json -> deforestation_events table
- data/fire_trajectories/*.json -> fire_narrative_cache table

This preserves the detailed classifications and narratives from the JSON files.
"""

import json
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
SETTLEMENT_DIR = BASE_DIR / "data" / "settlement_events"
DEFOREST_DIR = BASE_DIR / "data" / "deforestation_events"
FIRE_TRAJ_DIR = BASE_DIR / "data" / "fire_trajectories"
MIN_DATE = "2020-01-01"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def import_settlement_narratives(conn, park_id=None):
    """Import settlement narratives from JSON files"""
    json_files = list(SETTLEMENT_DIR.glob("*.json"))
    if park_id:
        json_files = [f for f in json_files if park_id in f.name]
    
    count = 0
    for json_file in json_files:
        try:
            with open(json_file) as f:
                settlements = json.load(f)
            
            for s in settlements:
                # Update existing record with rich data from JSON
                conn.execute("""
                    UPDATE park_settlements SET
                        classification = ?,
                        classification_confidence = ?,
                        narrative = ?,
                        fires_5km = ?,
                        fire_seasonality = ?,
                        deforest_nearby_km2 = ?,
                        polygon_ids = ?
                    WHERE park_id = ? AND ABS(lat - ?) < 0.0001 AND ABS(lon - ?) < 0.0001
                """, (
                    s.get('classification'),
                    s.get('classification_confidence', 0),
                    s.get('narrative', ''),
                    s.get('fires_5km', 0),
                    s.get('fire_seasonality'),
                    s.get('deforest_nearby_km2', 0),
                    s.get('polygon_ids', ''),
                    s.get('park_id'),
                    s.get('lat'),
                    s.get('lon')
                ))
                count += 1
        except Exception as e:
            log(f"Error processing {json_file.name}: {e}")
    
    conn.commit()
    return count

def import_deforestation_narratives(conn, park_id=None):
    """Import deforestation narratives from JSON files"""
    json_files = list(DEFOREST_DIR.glob("*.json"))
    if park_id:
        json_files = [f for f in json_files if park_id in f.name]
    
    count = 0
    for json_file in json_files:
        try:
            with open(json_file) as f:
                events = json.load(f)
            
            for e in events:
                # Update existing record with rich data from JSON
                conn.execute("""
                    UPDATE deforestation_events SET
                        classification = ?,
                        classification_confidence = ?,
                        narrative = ?,
                        fires_same_year = ?,
                        fire_ratio = ?,
                        nearest_settlement_km = ?,
                        polygon_ids = ?
                    WHERE park_id = ? AND year = ? AND ABS(lat - ?) < 0.0001 AND ABS(lon - ?) < 0.0001
                """, (
                    e.get('classification'),
                    e.get('classification_confidence', 0),
                    e.get('narrative', ''),
                    e.get('fires_same_year', 0),
                    e.get('fire_ratio', 0),
                    e.get('nearest_settlement_km'),
                    e.get('polygon_ids', ''),
                    e.get('park_id'),
                    e.get('year'),
                    e.get('lat'),
                    e.get('lon')
                ))
                count += 1
        except Exception as e:
            log(f"Error processing {json_file.name}: {e}")
    
    conn.commit()
    return count

def import_fire_narratives(conn, park_id=None):
    """Import fire narratives to cache from trajectory JSON files"""
    json_files = list(FIRE_TRAJ_DIR.glob("*.json"))
    if park_id:
        json_files = [f for f in json_files if park_id in f.name]
    
    count = 0
    for json_file in json_files:
        try:
            with open(json_file) as f:
                data = json.load(f)
            
            pid = data.get('park_id', json_file.stem)
            park_name = data.get('park_name', pid)
            trajectories = data.get('trajectories', [])
            
            if not trajectories:
                continue
            
            # Build summary
            total_fires = sum(t.get('fires', 0) for t in trajectories)
            total_groups = len(trajectories)
            
            by_type = {}
            cross_border = 0
            for t in trajectories:
                gt = t.get('group_type', 'unknown')
                by_type[gt] = by_type.get(gt, 0) + 1
                if t.get('cross_border'):
                    cross_border += 1
            
            sorted_types = sorted(by_type.items(), key=lambda x: -x[1])[:3]
            type_desc = ", ".join([f"{cnt} {gt} ({100*cnt/total_groups:.1f}%)" for gt, cnt in sorted_types])
            
            dates = [t['start_date'] for t in trajectories if t.get('start_date')]
            from_date = min(dates)[:7] if dates else '2020-01'
            to_date = max(dates)[:7] if dates else '2026-02'
            
            summary = f"From {from_date} to {to_date} {park_name} experienced {total_fires:,} fire detections across {total_groups:,} fire groups. Fire types: {type_desc}."
            if cross_border > 0:
                summary += f" {cross_border} groups ({100*cross_border/total_groups:.1f}%) crossed park boundaries."
            
            # Build narratives list (preserve rich data from JSON)
            narratives = []
            for i, t in enumerate(trajectories):
                start_date = t.get('start_date', '')
                if start_date < MIN_DATE:
                    continue
                
                year = int(start_date[:4]) if start_date else 0
                
                # Get origin/destination descriptions
                origin = t.get('origin', {})
                dest = t.get('destination', {})
                origin_desc = origin.get('desc', '')
                dest_desc = dest.get('desc', '')
                
                narratives.append({
                    'group_num': i + 1,
                    'feature_id': f"{pid}_grp_{i}",
                    'year': year,
                    'origin_desc': origin_desc,
                    'dest_desc': dest_desc,
                    'entry_date': start_date,
                    'last_inside': t.get('end_date', ''),
                    'days_inside': t.get('days', 0),
                    'fires_total': t.get('fires', 0),
                    'fires_inside': t.get('fires', 0),
                    'distance_km': t.get('distance_km', 0),
                    'direction': t.get('direction', ''),
                    'speed_km_day': t.get('avg_speed_km_day', 0),
                    'group_type': t.get('group_type', 'unknown'),
                    'pct_inside': 100.0,
                    'cross_border': t.get('cross_border', False),
                    'affected_parks': t.get('affected_parks', [pid]),
                    'rivers_crossed': t.get('rivers_crossed', []),
                    'near_settlement': t.get('near_settlement'),
                    'narrative': t.get('narrative', '')
                })
            
            # Build narrative JSON
            narrative_json = {
                'park_id': pid,
                'park_name': park_name,
                'year': 0,
                'summary': summary,
                'total_fires': total_fires,
                'total_groups': total_groups,
                'by_type': by_type,
                'weekly_data': [],
                'narratives': narratives
            }
            
            # Store in cache
            conn.execute("""
                INSERT OR REPLACE INTO fire_narrative_cache (park_id, narrative_json, computed_at, from_year, to_year)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
            """, (pid, json.dumps(narrative_json), 2020, 2026))
            count += 1
            
        except Exception as e:
            log(f"Error processing {json_file.name}: {e}")
    
    conn.commit()
    return count

def main():
    global MIN_DATE
    
    parser = argparse.ArgumentParser(description='Step 3: Import Narratives from JSON')
    parser.add_argument('--park', help='Process single park')
    parser.add_argument('--from-date', default=MIN_DATE, help='Min date filter')
    parser.add_argument('--fire-only', action='store_true', help='Only fire narratives')
    parser.add_argument('--settlement-only', action='store_true', help='Only settlement narratives')
    parser.add_argument('--deforest-only', action='store_true', help='Only deforestation narratives')
    args = parser.parse_args()
    
    MIN_DATE = args.from_date
    
    log("=" * 60)
    log(f"STEP 3: IMPORT NARRATIVES FROM JSON - from {MIN_DATE}")
    log("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    
    do_all = not (args.fire_only or args.settlement_only or args.deforest_only)
    
    if do_all or args.fire_only:
        log("Importing fire narratives...")
        fire_count = import_fire_narratives(conn, args.park)
        log(f"  Fire: {fire_count} parks")
    
    if do_all or args.settlement_only:
        log("Importing settlement narratives...")
        settle_count = import_settlement_narratives(conn, args.park)
        log(f"  Settlement: {settle_count} records updated")
    
    if do_all or args.deforest_only:
        log("Importing deforestation narratives...")
        deforest_count = import_deforestation_narratives(conn, args.park)
        log(f"  Deforestation: {deforest_count} records updated")
    
    conn.close()
    
    log("")
    log("COMPLETE")

if __name__ == '__main__':
    main()
