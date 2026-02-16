#!/usr/bin/env python3
"""
Precompute Narratives v4 - Limited to Jun 2020 onwards

Uses fire data from 2020-06-01 onwards to reduce storage requirements.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'
SETTLE_DIR = DATA_DIR / 'settlement_events'
DEFOREST_DIR = DATA_DIR / 'deforestation_events'
EXPORT_DIR = DATA_DIR / 'export'

# Date filter - only process fire data from this date onwards
MIN_DATE = '2020-06-01'

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

class NarrativeGeneratorV4:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        self._load_context()
        self._load_park_info()
    
    def _load_context(self):
        """Load all context data"""
        log("Loading context data...")
        
        self.climate = {}
        climate_file = DATA_DIR / 'climate' / 'park_climate.json'
        if climate_file.exists():
            with open(climate_file) as f:
                self.climate = json.load(f)
        log(f"  Climate: {len(self.climate)} parks")
        
        self.rivers = defaultdict(list)
        try:
            cursor = self.conn.execute('''
                SELECT park_id, name, stream_order
                FROM park_rivers_hydro
                WHERE name IS NOT NULL AND name != ''
            ''')
            for row in cursor:
                self.rivers[row['park_id']].append({'name': row['name'], 'order': row['stream_order']})
        except: pass
        log(f"  Rivers: {len(self.rivers)} parks")
        
        self.lakes = defaultdict(list)
        try:
            cursor = self.conn.execute('SELECT park_id, name, area_km2 FROM park_lakes_hydro WHERE name IS NOT NULL')
            for row in cursor:
                self.lakes[row['park_id']].append({'name': row['name'], 'area_km2': row['area_km2']})
        except: pass
        log(f"  Lakes: {len(self.lakes)} parks")
        
        self.places = defaultdict(list)
        try:
            cursor = self.conn.execute('SELECT park_id, name, place_type FROM osm_places WHERE name IS NOT NULL')
            for row in cursor:
                self.places[row['park_id']].append({'name': row['name'], 'type': row['place_type']})
        except: pass
        log(f"  Places: {len(self.places)} parks")
    
    def _load_park_info(self):
        self.parks = {}
        try:
            with open(DATA_DIR / 'keystones_with_boundaries.json') as f:
                for p in json.load(f):
                    self.parks[p['id']] = {'name': p.get('name', p['id']), 'country': p.get('country', ''), 'area_km2': p.get('area_km2', 10000)}
        except: pass
        log(f"Loaded info for {len(self.parks)} parks")
    
    def generate_fire_narratives(self):
        """Generate fire narratives from feature_geometries (filtered by date)"""
        log("=" * 70)
        log(f"FIRE NARRATIVES (from {MIN_DATE} onwards)")
        log("=" * 70)
        
        # Query trajectories from DB with date filter
        cursor = self.conn.execute('''
            SELECT park_id, feature_id, start_date, end_date, properties_json
            FROM feature_geometries
            WHERE feature_type = 'fire_trajectory' AND start_date >= ?
            ORDER BY park_id, start_date
        ''', (MIN_DATE,))
        
        by_park = defaultdict(list)
        for row in cursor:
            props = json.loads(row['properties_json']) if row['properties_json'] else {}
            by_park[row['park_id']].append({
                'feature_id': row['feature_id'],
                'start_date': row['start_date'],
                'end_date': row['end_date'],
                **props
            })
        
        log(f"Loaded trajectories for {len(by_park)} parks")
        
        narratives = {}
        parks = sorted(by_park.keys())
        
        for idx, park_id in enumerate(parks, 1):
            trajectories = by_park[park_id]
            if not trajectories:
                continue
            
            park_info = self.parks.get(park_id, {})
            park_name = park_info.get('name', park_id.split('_', 1)[1].replace('_', ' ') if '_' in park_id else park_id)
            park_climate = self.climate.get(park_id, {})
            
            # Build narratives list
            traj_narratives = []
            for t in trajectories:
                traj_narratives.append({
                    'feature_id': t.get('feature_id'),
                    'year': t.get('year'),
                    'start_date': t.get('start_date'),
                    'end_date': t.get('end_date'),
                    'days': t.get('days'),
                    'fires': t.get('fires'),
                    'distance_km': t.get('distance_km'),
                    'speed_km_day': t.get('speed_km_day'),
                    'direction': t.get('direction'),
                    'group_type': t.get('group_type'),
                    'primary_type': t.get('primary_type'),
                    'pct_inside': t.get('pct_inside'),
                    'cross_border': t.get('cross_border'),
                    'nearest_river': t.get('nearest_river'),
                    'nearest_place': t.get('nearest_place'),
                    'season': t.get('season'),
                    'narrative': t.get('narrative', '')
                })
            
            # Aggregates
            by_year = defaultdict(list)
            for t in trajectories:
                by_year[t.get('year', 0)].append(t)
            
            total_fires = sum(t.get('fires', 0) for t in trajectories)
            total_groups = len(trajectories)
            
            type_counts = defaultdict(int)
            for t in trajectories:
                type_counts[t.get('group_type', 'unknown')] += 1
            
            management_count = sum(1 for t in trajectories if 'management' in (t.get('group_type', '') or '').lower())
            cross_border_count = sum(1 for t in trajectories if t.get('cross_border'))
            
            # Years summary
            years_summary = []
            for year in sorted(by_year.keys()):
                year_trajs = by_year[year]
                years_summary.append({
                    'year': year,
                    'total_groups': len(year_trajs),
                    'total_fires': sum(t.get('fires', 0) for t in year_trajs)
                })
            
            # Peak month
            month_counts = defaultdict(int)
            for t in trajectories:
                sd = t.get('start_date', '')
                if sd:
                    try:
                        month_counts[datetime.strptime(sd, '%Y-%m-%d').strftime('%B')] += 1
                    except: pass
            peak_month = max(month_counts, key=month_counts.get) if month_counts else None
            
            # Summary
            year_range = f"{min(by_year.keys())}-{max(by_year.keys())}" if by_year else "N/A"
            summary = f"From {year_range}, {park_name} experienced {total_fires:,} fire detections across {total_groups} groups."
            if management_count > 0:
                summary += f" {management_count} appear to be management burns."
            if peak_month:
                summary += f" Peak activity: {peak_month}."
            
            narratives[park_id] = {
                'park_id': park_id,
                'park_name': park_name,
                'summary': summary,
                'total_fires': total_fires,
                'total_groups': total_groups,
                'management_fires': management_count,
                'cross_border_groups': cross_border_count,
                'peak_month': peak_month,
                'group_types': dict(type_counts),
                'trend': {'years': years_summary},
                'climate': {'dry_season': park_climate.get('dry_season'), 'rainy_season': park_climate.get('rainy_season')},
                'narratives': traj_narratives
            }
            
            if idx % 20 == 0:
                log(f"[{idx}/{len(parks)}] {park_id}: {total_groups} groups")
        
        # Export
        EXPORT_DIR.mkdir(exist_ok=True)
        fire_dir = EXPORT_DIR / 'fire_narratives'
        fire_dir.mkdir(exist_ok=True)
        
        for park_id, data in narratives.items():
            with open(fire_dir / f'{park_id}.json', 'w') as f:
                json.dump(data, f)
        
        # Update cache
        log("Updating fire_narrative_cache...")
        for park_id, data in narratives.items():
            self.conn.execute('INSERT OR REPLACE INTO fire_narrative_cache (park_id, narrative_json, computed_at) VALUES (?, ?, ?)',
                              (park_id, json.dumps(data), datetime.now().isoformat()))
        self.conn.commit()
        
        log(f"Fire narratives: {len(narratives)} parks")
        return narratives
    
    def generate_settlement_narratives(self):
        """Generate settlement narratives"""
        log("\n" + "=" * 70)
        log("SETTLEMENT NARRATIVES")
        log("=" * 70)
        
        narratives = {}
        for json_file in sorted(SETTLE_DIR.glob('*.json')):
            park_id = json_file.stem
            with open(json_file) as f:
                settlements = json.load(f)
            if not settlements:
                continue
            
            park_name = self.parks.get(park_id, {}).get('name', park_id)
            
            class_counts = defaultdict(int)
            total_pop = sum(s.get('population_est') or 0 for s in settlements)
            total_area = sum(s.get('area_m2') or 0 for s in settlements)
            for s in settlements:
                class_counts[s.get('classification') or 'unknown'] += 1
            
            settlement_list = [{
                'lat': s.get('lat'), 'lon': s.get('lon'),
                'area_ha': round((s.get('area_m2') or 0) / 10000, 2),
                'population': s.get('population_est'),
                'nearest_place': s.get('nearest_place'),
                'classification': s.get('classification'),
                'narrative': s.get('narrative'),
                'polygon_ids': s.get('polygon_ids')
            } for s in settlements]
            
            narratives[park_id] = {
                'park_id': park_id,
                'park_name': park_name,
                'summary': f"{park_name} has {len(settlements)} settlements, pop ~{total_pop:,}, {total_area/10000:.1f} ha.",
                'settlement_count': len(settlements),
                'total_population': total_pop,
                'classification_breakdown': dict(class_counts),
                'settlements': settlement_list
            }
        
        with open(EXPORT_DIR / 'classified_settlements.json', 'w') as f:
            json.dump(narratives, f)
        
        log(f"Settlement narratives: {len(narratives)} parks")
        return narratives
    
    def generate_deforestation_narratives(self):
        """Generate deforestation narratives"""
        log("\n" + "=" * 70)
        log("DEFORESTATION NARRATIVES")
        log("=" * 70)
        
        narratives = {}
        for json_file in sorted(DEFOREST_DIR.glob('*.json')):
            park_id = json_file.stem
            with open(json_file) as f:
                events = json.load(f)
            if not events:
                continue
            
            park_name = self.parks.get(park_id, {}).get('name', park_id)
            
            by_year = defaultdict(list)
            class_counts = defaultdict(int)
            total_area = 0
            for e in events:
                by_year[e.get('year', 0)].append(e)
                class_counts[e.get('classification') or 'unknown'] += 1
                total_area += e.get('area_km2') or 0
            
            years_data = [{'year': y, 'events': len(evts), 'area_km2': round(sum(e.get('area_km2') or 0 for e in evts), 4)}
                          for y, evts in sorted(by_year.items())]
            
            event_list = [{
                'year': e.get('year'), 'lat': e.get('lat'), 'lon': e.get('lon'),
                'area_km2': e.get('area_km2'), 'classification': e.get('classification'),
                'narrative': e.get('narrative'), 'polygon_ids': e.get('polygon_ids')
            } for e in events]
            
            narratives[park_id] = {
                'park_id': park_id,
                'park_name': park_name,
                'summary': f"{park_name}: {total_area:.2f} km² forest loss across {len(by_year)} years.",
                'total_events': len(events),
                'total_area_km2': round(total_area, 4),
                'classification_breakdown': dict(class_counts),
                'yearly_data': years_data,
                'events': event_list
            }
        
        with open(EXPORT_DIR / 'classified_deforestation.json', 'w') as f:
            json.dump(narratives, f)
        
        log(f"Deforestation narratives: {len(narratives)} parks")
        return narratives
    
    def run(self):
        fire = self.generate_fire_narratives()
        settlement = self.generate_settlement_narratives()
        deforest = self.generate_deforestation_narratives()
        
        log("\n" + "=" * 70)
        log("COMPLETE")
        log(f"Fire: {len(fire)} parks, Settlement: {len(settlement)} parks, Deforestation: {len(deforest)} parks")
        self.conn.close()

def main():
    log("=" * 70)
    log(f"Precompute Narratives v4 (from {MIN_DATE})")
    log("=" * 70)
    NarrativeGeneratorV4().run()

if __name__ == "__main__":
    main()
