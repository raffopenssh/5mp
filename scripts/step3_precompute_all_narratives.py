#!/usr/bin/env python3
"""
STEP 3: Precompute ALL Narratives - Fire, Settlement, Deforestation

Reads from:
- data/fire_trajectories/*.json (fire narratives)
- park_settlements table (settlement narratives)
- deforestation_events table (deforestation narratives)

Writes to:
- fire_narrative_cache table (fire narratives JSON)
- park_settlements table (classification, narrative columns)
- deforestation_events table (classification, narrative columns)
"""

import json
import sqlite3
import argparse
import math
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
FIRE_TRAJ_DIR = BASE_DIR / "data" / "fire_trajectories"
MIN_DATE = "2020-01-01"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

class NarrativePrecomputer:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.load_context_data()
    
    def load_context_data(self):
        """Load rivers, places, roads into memory for fast lookup"""
        # Rivers by park
        self.rivers = {}
        for row in self.conn.execute("SELECT park_id, name, lat, lon FROM park_rivers_hydro WHERE name IS NOT NULL AND name != ''"):
            pid = row['park_id']
            if pid not in self.rivers:
                self.rivers[pid] = []
            self.rivers[pid].append({'name': row['name'], 'lat': row['lat'], 'lon': row['lon']})
        
        # Places by park
        self.places = {}
        for row in self.conn.execute("SELECT park_id, name, lat, lon, place_type FROM osm_places WHERE name != ''"):
            pid = row['park_id']
            if pid not in self.places:
                self.places[pid] = []
            self.places[pid].append({'name': row['name'], 'lat': row['lat'], 'lon': row['lon'], 'type': row['place_type']})
        
        # Settlements by park
        self.settlements = {}
        for row in self.conn.execute("SELECT park_id, id, lat, lon, nearest_place, population_est, area_m2 FROM park_settlements"):
            pid = row['park_id']
            if pid not in self.settlements:
                self.settlements[pid] = []
            self.settlements[pid].append({
                'id': row['id'], 'lat': row['lat'], 'lon': row['lon'],
                'name': row['nearest_place'] or 'Unknown', 
                'pop': row['population_est'] or 0,
                'area': row['area_m2'] or 0
            })
        
        # Fire counts by park/year
        self.fire_counts = {}
        for row in self.conn.execute("""
            SELECT park_id, substr(start_date, 1, 4) as year, COUNT(*) as cnt
            FROM feature_geometries 
            WHERE feature_type = 'fire_trajectory' AND start_date IS NOT NULL
            GROUP BY park_id, year
        """):
            key = f"{row['park_id']}_{row['year']}"
            self.fire_counts[key] = row['cnt']
        
        log(f"Context loaded: {sum(len(v) for v in self.rivers.values())} rivers, {sum(len(v) for v in self.places.values())} places")
    
    def find_nearest(self, lat, lon, items, max_dist=50):
        """Find nearest item within max distance"""
        nearest = None
        min_dist = max_dist
        for item in items:
            dist = haversine(lat, lon, item['lat'], item['lon'])
            if dist < min_dist:
                min_dist = dist
                nearest = {'item': item, 'dist': dist}
        return nearest
    
    def precompute_fire_narratives(self, park_id=None):
        """Precompute fire narratives from JSON files"""
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
                
                # Build narratives list
                narratives = []
                for i, t in enumerate(trajectories):
                    start_date = t.get('start_date', '')
                    if start_date < MIN_DATE:
                        continue
                    
                    year = int(start_date[:4]) if start_date else 0
                    
                    narratives.append({
                        'group_num': i + 1,
                        'feature_id': f"{pid}_grp_{i}",
                        'year': year,
                        'origin_desc': t.get('origin', {}).get('desc', ''),
                        'dest_desc': t.get('destination', {}).get('desc', ''),
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
                self.conn.execute("""
                    INSERT OR REPLACE INTO fire_narrative_cache (park_id, narrative_json, computed_at, from_year, to_year)
                    VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
                """, (pid, json.dumps(narrative_json), 2020, 2026))
                count += 1
                
            except Exception as e:
                log(f"Error processing {json_file.name}: {e}")
        
        self.conn.commit()
        return count
    
    def precompute_settlement_narratives(self, park_id=None):
        """Compute and store settlement classifications and narratives"""
        query = "SELECT DISTINCT park_id FROM park_settlements"
        if park_id:
            query += f" WHERE park_id = '{park_id}'"
        
        parks = [row[0] for row in self.conn.execute(query)]
        count = 0
        
        for pid in parks:
            settlements = list(self.conn.execute("""
                SELECT id, lat, lon, area_m2, population_est, nearest_place, distance_to_place_km
                FROM park_settlements WHERE park_id = ?
            """, (pid,)))
            
            rivers = self.rivers.get(pid, [])
            places = self.places.get(pid, [])
            
            for s in settlements:
                lat, lon = s['lat'], s['lon']
                area = s['area_m2'] or 0
                pop = s['population_est'] or 0
                nearest_place = s['nearest_place'] or 'Unknown'
                dist_place = s['distance_to_place_km'] or 0
                
                # Classification based on area and population
                if area > 50000 or pop > 500:
                    classification = 'village'
                    conf = 0.8
                elif area > 10000 or pop > 100:
                    classification = 'semi_permanent_camp'
                    conf = 0.7
                elif area > 1000:
                    classification = 'temporary_camp'
                    conf = 0.6
                else:
                    classification = 'unknown'
                    conf = 0.4
                
                # Find nearest river
                river_near = self.find_nearest(lat, lon, rivers, max_dist=20)
                river_name = river_near['item']['name'] if river_near else None
                
                # Build narrative
                parts = [f"{classification.replace('_', ' ').title()} in {nearest_place.split(',')[0]}"]
                parts.append(f"Area: {area/10000:.2f} ha, estimated population: {pop}.")
                if dist_place > 0:
                    parts.append(f"Located {dist_place:.1f}km from {nearest_place}.")
                if river_name:
                    parts.append(f"Near {river_name} river.")
                
                narrative = " ".join(parts)
                
                # Count fires nearby (simplified - just estimate)
                fire_key = f"{pid}_2024"
                fires_5km = self.fire_counts.get(fire_key, 0) // 100  # Rough estimate
                
                # Update database
                self.conn.execute("""
                    UPDATE park_settlements SET
                        classification = ?,
                        classification_confidence = ?,
                        narrative = ?,
                        fires_5km = ?
                    WHERE id = ?
                """, (classification, conf, narrative, fires_5km, s['id']))
                count += 1
        
        self.conn.commit()
        return count
    
    def precompute_deforestation_narratives(self, park_id=None):
        """Compute and store deforestation classifications and narratives"""
        query = "SELECT DISTINCT park_id FROM deforestation_events"
        if park_id:
            query += f" WHERE park_id = '{park_id}'"
        
        parks = [row[0] for row in self.conn.execute(query)]
        count = 0
        
        for pid in parks:
            events = list(self.conn.execute("""
                SELECT id, year, lat, lon, area_km2, pattern_type
                FROM deforestation_events WHERE park_id = ?
            """, (pid,)))
            
            rivers = self.rivers.get(pid, [])
            settlements_list = self.settlements.get(pid, [])
            
            for e in events:
                lat, lon = e['lat'], e['lon']
                area = e['area_km2'] or 0
                year = e['year']
                pattern = e['pattern_type'] or 'unknown'
                
                # Classification based on pattern and area
                if 'agriculture' in pattern.lower() or 'slash' in pattern.lower():
                    classification = 'agricultural_expansion'
                    conf = 0.7
                elif 'logging' in pattern.lower() or 'linear' in pattern.lower():
                    classification = 'logging'
                    conf = 0.7
                elif 'natural' in pattern.lower():
                    classification = 'natural'
                    conf = 0.6
                else:
                    classification = 'unknown'
                    conf = 0.4
                
                # Find nearest river
                river_near = self.find_nearest(lat, lon, rivers, max_dist=20)
                river_name = river_near['item']['name'] if river_near else None
                
                # Find nearest settlement
                settle_near = self.find_nearest(lat, lon, settlements_list, max_dist=30)
                settle_dist = settle_near['dist'] if settle_near else 0
                settle_name = settle_near['item']['name'] if settle_near else None
                
                # Determine sector
                # (simplified - would need park centroid)
                sector = "central sector"
                
                # Build narrative
                size_desc = "minor" if area < 0.01 else "moderate" if area < 0.1 else "significant"
                parts = [f"{classification.replace('_', ' ').title()} ({area*100:.1f} ha in {year}) at {lat:.2f}°N, {lon:.2f}°E in {sector}."]
                
                if river_name and settle_name:
                    parts.append(f"Near {river_name} river and {settle_name} settlement.")
                elif river_name:
                    parts.append(f"Near {river_name} river.")
                elif settle_name:
                    parts.append(f"Near {settle_name} settlement ({settle_dist:.1f}km).")
                
                # Add contextual interpretation
                if classification == 'agricultural_expansion':
                    parts.append("The scattered pattern suggests smallholder agricultural expansion.")
                elif classification == 'logging':
                    parts.append("Linear pattern indicates road-based logging activity.")
                elif classification == 'natural':
                    parts.append("Isolated natural clearing, possibly caused by flooding or disease.")
                
                narrative = " ".join(parts)
                
                # Update database
                self.conn.execute("""
                    UPDATE deforestation_events SET
                        classification = ?,
                        classification_confidence = ?,
                        narrative = ?,
                        nearest_settlement_km = ?
                    WHERE id = ?
                """, (classification, conf, narrative, settle_dist, e['id']))
                count += 1
        
        self.conn.commit()
        return count

def main():
    global MIN_DATE
    
    parser = argparse.ArgumentParser(description='Step 3: Precompute ALL Narratives')
    parser.add_argument('--park', help='Process single park')
    parser.add_argument('--from-date', default=MIN_DATE, help='Min date filter')
    parser.add_argument('--fire-only', action='store_true', help='Only fire narratives')
    parser.add_argument('--settlement-only', action='store_true', help='Only settlement narratives')
    parser.add_argument('--deforest-only', action='store_true', help='Only deforestation narratives')
    args = parser.parse_args()
    
    MIN_DATE = args.from_date
    
    log("=" * 60)
    log(f"STEP 3: PRECOMPUTE ALL NARRATIVES - from {MIN_DATE}")
    log("=" * 60)
    
    precomputer = NarrativePrecomputer(DB_PATH)
    
    do_all = not (args.fire_only or args.settlement_only or args.deforest_only)
    
    if do_all or args.fire_only:
        log("Computing fire narratives...")
        fire_count = precomputer.precompute_fire_narratives(args.park)
        log(f"  Fire: {fire_count} parks")
    
    if do_all or args.settlement_only:
        log("Computing settlement narratives...")
        settle_count = precomputer.precompute_settlement_narratives(args.park)
        log(f"  Settlement: {settle_count} narratives")
    
    if do_all or args.deforest_only:
        log("Computing deforestation narratives...")
        deforest_count = precomputer.precompute_deforestation_narratives(args.park)
        log(f"  Deforestation: {deforest_count} narratives")
    
    log("")
    log("COMPLETE")

if __name__ == '__main__':
    main()
