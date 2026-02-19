#!/usr/bin/env python3
"""
Precompute Narratives v4 - Compatible with Go API structure

Outputs JSON in format expected by Go FireNarrative struct.
Filters fire data to 2020-06-01 onwards.
"""

import json
import argparse
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
DATA_DIR = Path(__file__).parent.parent / 'data'
SETTLE_DIR = DATA_DIR / 'settlement_events'
DEFOREST_DIR = DATA_DIR / 'deforestation_events'
EXPORT_DIR = DATA_DIR / 'export'

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
        log("Loading context data...")
        
        self.climate = {}
        climate_file = DATA_DIR / 'climate' / 'park_climate.json'
        if climate_file.exists():
            with open(climate_file) as f:
                self.climate = json.load(f)
        log(f"  Climate: {len(self.climate)} parks")
        
        self.rivers = defaultdict(list)
        try:
            cursor = self.conn.execute('SELECT park_id, name, stream_order FROM park_rivers_hydro WHERE name IS NOT NULL AND name != ""')
            for row in cursor:
                self.rivers[row['park_id']].append({'name': row['name'], 'order': row['stream_order']})
        except: pass
        log(f"  Rivers: {len(self.rivers)} parks")
        
        self.places = defaultdict(list)
        try:
            cursor = self.conn.execute('SELECT park_id, name, place_type, lat, lon FROM osm_places WHERE name IS NOT NULL')
            for row in cursor:
                self.places[row['park_id']].append({'name': row['name'], 'type': row['place_type'], 'lat': row['lat'], 'lon': row['lon']})
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
        """Generate fire narratives in Go API compatible format"""
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
            park_area = park_info.get('area_km2', 10000)
            park_climate = self.climate.get(park_id, {})
            
            # Build narratives list (FireGroupStory format)
            traj_narratives = []
            for i, t in enumerate(trajectories):
                traj_narratives.append({
                    'group_num': i + 1,
                    'feature_id': t.get('feature_id'),
                    'year': t.get('year'),
                    'start_date': t.get('start_date'),
                    'end_date': t.get('end_date'),
                    'days': t.get('days'),
                    'fires_total': t.get('fires'),
                    'distance_km': t.get('distance_km'),
                    'avg_speed_km_day': t.get('speed_km_day'),
                    'direction': t.get('direction'),
                    'group_type': t.get('group_type'),
                    'refined_type': t.get('primary_type'),
                    'pct_inside': t.get('pct_inside'),
                    'cross_border': t.get('cross_border'),
                    'affected_parks': t.get('affected_parks'),
                    'season': t.get('season'),
                    'origin': {
                        'nearest_place': {'name': t.get('nearest_place')} if t.get('nearest_place') else None,
                        'nearest_river': {'name': t.get('nearest_river')} if t.get('nearest_river') else None
                    },
                    'narrative': t.get('narrative', '')
                })
            
            # Aggregates by year
            by_year = defaultdict(list)
            for t in trajectories:
                # Extract year from start_date if year not present
                year = t.get('year')
                if not year and t.get('start_date'):
                    try:
                        year = int(t['start_date'][:4])
                    except:
                        year = 0
                by_year[year or 0].append(t)
            
            total_fires = sum(t.get('fires', 0) for t in trajectories)
            total_groups = len(trajectories)
            
            # Type breakdown
            type_counts = defaultdict(int)
            for t in trajectories:
                type_counts[t.get('group_type', 'unknown')] += 1
            
            management_count = sum(1 for t in trajectories if 'management' in (t.get('group_type', '') or '').lower() or 'management' in (t.get('primary_type', '') or '').lower())
            cross_border_count = sum(1 for t in trajectories if t.get('cross_border'))
            outside_count = sum(1 for t in trajectories if (t.get('pct_inside') or 0) < 50)
            
            # Build FireYearSummary list
            years_summary = []
            for year in sorted(by_year.keys()):
                year_trajs = by_year[year]
                year_fires = sum(t.get('fires', 0) for t in year_trajs)
                year_management = sum(1 for t in year_trajs if 'management' in (t.get('group_type', '') or '').lower())
                year_stopped = sum(1 for t in year_trajs if (t.get('pct_inside') or 0) > 80)
                year_transited = len(year_trajs) - year_stopped
                response_rate = (year_stopped / len(year_trajs) * 100) if year_trajs else 0
                avg_days = sum(t.get('days', 0) for t in year_trajs) / max(1, len(year_trajs))
                
                years_summary.append({
                    'year': year,
                    'total_groups': len(year_trajs),
                    'groups_per_km2': round(len(year_trajs) / park_area * 1000, 4) if park_area > 0 else 0,
                    'stopped_inside': year_stopped,
                    'transited': year_transited,
                    'response_rate': round(response_rate, 1),
                    'total_fires': year_fires,
                    'avg_days_burning': round(avg_days, 1),
                    'management_fires': year_management
                })
            
            # Trend analysis
            if len(years_summary) >= 3:
                recent = years_summary[-2:]
                older = years_summary[:-2]
                recent_avg = sum(y['total_groups'] for y in recent) / len(recent)
                older_avg = sum(y['total_groups'] for y in older) / max(1, len(older))
                if recent_avg > older_avg * 1.2:
                    trend_direction = 'increasing'
                elif recent_avg < older_avg * 0.8:
                    trend_direction = 'decreasing'
                else:
                    trend_direction = 'stable'
            else:
                trend_direction = 'insufficient_data'
            
            # Worst/best year
            if years_summary:
                worst = max(years_summary, key=lambda y: y['total_groups'])
                best = min(years_summary, key=lambda y: y['total_groups'])
                worst_year = worst['year']
                worst_year_groups = worst['total_groups']
                best_year = best['year']
                best_year_rate = best.get('response_rate', 0)
            else:
                worst_year = worst_year_groups = best_year = 0
                best_year_rate = 0
            
            # Peak month
            month_counts = defaultdict(int)
            for t in trajectories:
                sd = t.get('start_date', '')
                if sd:
                    try:
                        month_counts[datetime.strptime(sd, '%Y-%m-%d').strftime('%B')] += 1
                    except: pass
            peak_month = max(month_counts, key=month_counts.get) if month_counts else None
            
            # Response rate (groups that stayed mostly inside)
            stopped = sum(1 for t in trajectories if (t.get('pct_inside') or 0) > 80)
            response_rate = (stopped / total_groups * 100) if total_groups > 0 else 0
            
            # Key places
            park_places = self.places.get(park_id, [])[:10]
            key_places = [{'name': p['name'], 'type': p['type'], 'lat': p['lat'], 'lon': p['lon']} for p in park_places]
            
            # Summary text
            year_range = f"{min(by_year.keys())}-{max(by_year.keys())}" if by_year else "N/A"
            summary_parts = [f"From {year_range}, {park_name} experienced {total_fires:,} fire detections across {total_groups} fire groups."]
            
            if management_count > 0:
                mgmt_pct = round(management_count / total_groups * 100, 1)
                summary_parts.append(f"{management_count} groups ({mgmt_pct}%) appear to be management/controlled burns.")
            
            if cross_border_count > 0:
                cross_pct = round(cross_border_count / total_groups * 100, 1)
                summary_parts.append(f"{cross_border_count} groups ({cross_pct}%) crossed park boundaries.")
            
            if outside_count > 0:
                outside_pct = round(outside_count / total_groups * 100, 1)
                summary_parts.append(f"{outside_count} groups ({outside_pct}%) originated outside the park.")
            
            if response_rate > 50:
                summary_parts.append(f"{round(response_rate)}% of groups were contained inside the park.")
            
            if peak_month:
                summary_parts.append(f"Peak activity: {peak_month}.")
            
            if park_climate.get('dry_season'):
                summary_parts.append(f"Dry season: {park_climate.get('dry_season')}.")
            
            # Build trend narrative
            trend_narrative_parts = []
            if trend_direction == 'increasing':
                trend_narrative_parts.append("Fire activity has increased in recent years.")
            elif trend_direction == 'decreasing':
                trend_narrative_parts.append("Fire activity has decreased in recent years.")
            else:
                trend_narrative_parts.append("Fire activity has remained relatively stable.")
            
            if worst_year:
                trend_narrative_parts.append(f"Worst year: {worst_year} with {worst_year_groups} fire groups.")
            
            # FireNarrative structure (matching Go struct)
            narratives[park_id] = {
                'park_id': park_id,
                'park_name': park_name,
                'year': max(by_year.keys()) if by_year else 2024,
                'summary': ' '.join(summary_parts),
                'narratives': traj_narratives,
                'key_places': key_places,
                'trend': {
                    'years': years_summary,
                    'trend_direction': trend_direction,
                    'avg_response_rate': round(sum(y['response_rate'] for y in years_summary) / max(1, len(years_summary)), 1),
                    'worst_year': worst_year,
                    'worst_year_groups': worst_year_groups,
                    'best_year': best_year,
                    'best_year_rate': best_year_rate,
                    'narrative': ' '.join(trend_narrative_parts),
                    'avg_groups_per_km2': round(total_groups / park_area * 1000, 4) if park_area > 0 else 0,
                    'peak_months': [peak_month] if peak_month else [],
                    'seasonality': f"dry season peaks {park_climate.get('dry_season', 'unknown')}" if park_climate.get('dry_season') else None
                },
                'response_rate': round(response_rate, 1),
                'total_fires': total_fires,
                'peak_month': peak_month,
                # Extra fields for enhanced analysis
                'total_groups': total_groups,
                'management_fires': management_count,
                'cross_border_groups': cross_border_count,
                'outside_park_groups': outside_count,
                'group_types': dict(type_counts),
                'climate': {
                    'dry_season': park_climate.get('dry_season'),
                    'rainy_season': park_climate.get('rainy_season'),
                    'climate_zone': park_climate.get('climate_zone')
                }
            }
            
            if idx % 20 == 0:
                log(f"[{idx}/{len(parks)}] {park_id}: {total_groups} groups, {management_count} mgmt, {cross_border_count} cross-border")
        
        # Export
        EXPORT_DIR.mkdir(exist_ok=True)
        fire_dir = EXPORT_DIR / 'fire_narratives'
        fire_dir.mkdir(exist_ok=True)
        
        for park_id, data in narratives.items():
            # Atomic write: write to temp file then rename
            output_file = fire_dir / f'{park_id}.json'
            temp_file = output_file.with_suffix('.json.tmp')
            with open(temp_file, 'w') as f:
                json.dump(data, f)
            temp_file.rename(output_file)
        
        # Update cache
        log("Updating fire_narrative_cache...")
        for park_id, data in narratives.items():
            years = data.get('trend', {}).get('years', [])
            from_year = min(y['year'] for y in years) if years else None
            to_year = max(y['year'] for y in years) if years else None
            self.conn.execute('INSERT OR REPLACE INTO fire_narrative_cache (park_id, narrative_json, computed_at, from_year, to_year) VALUES (?, ?, ?, ?, ?)',
                              (park_id, json.dumps(data), datetime.now().isoformat(), from_year, to_year))
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
    
    def run(self, incremental=False, days=14):
        """Run narrative generation.
        
        Args:
            incremental: If True, only update parks with recent changes
            days: Days to consider recent in incremental mode
        """
        self.incremental = incremental
        self.incremental_days = days
        
        if incremental:
            self.cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            log(f"INCREMENTAL: Only parks with changes since {self.cutoff_date}")
        
        fire = self.generate_fire_narratives()
        settlement = self.generate_settlement_narratives()
        deforest = self.generate_deforestation_narratives()
        
        log("\n" + "=" * 70)
        log("COMPLETE")
        log(f"Fire: {len(fire)} parks, Settlement: {len(settlement)} parks, Deforestation: {len(deforest)} parks")
        self.conn.close()

def main():
    parser = argparse.ArgumentParser(description="Precompute Narratives v4")
    parser.add_argument("--incremental", action="store_true",
                        help="Only update parks with recent fire data")
    parser.add_argument("--days", type=int, default=14,
                        help="Days to consider recent in incremental mode")
    args = parser.parse_args()
    
    log("=" * 70)
    log(f"Precompute Narratives v4 (from {MIN_DATE})")
    if args.incremental:
        log(f"INCREMENTAL MODE: {args.days} days")
    log("=" * 70)
    
    NarrativeGeneratorV4().run(incremental=args.incremental, days=args.days)

if __name__ == "__main__":
    main()
