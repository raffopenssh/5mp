#!/usr/bin/env python3
"""
Precompute Narratives v5 - Enhanced with v5 fire group fields

New v5 fields: trajectory_type (clean/zigzag/erratic), zigzag_ratio
Combines all complexity from v1-v4 plus new trajectory analysis.
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

class NarrativeGeneratorV5:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        self._load_context()
        self._load_park_info()
    
    def _load_context(self):
        log("Loading context data...")
        
        # Climate
        self.climate = {}
        climate_file = DATA_DIR / 'climate' / 'park_climate.json'
        if climate_file.exists():
            with open(climate_file) as f:
                self.climate = json.load(f)
        log(f"  Climate: {len(self.climate)} parks")
        
        # Rivers (from HydroRIVERS)
        self.rivers = defaultdict(list)
        try:
            cursor = self.conn.execute('''
                SELECT park_id, name, stream_order FROM park_rivers_hydro 
                WHERE name IS NOT NULL AND name != ""
            ''')
            for row in cursor:
                self.rivers[row['park_id']].append({
                    'name': row['name'], 'order': row['stream_order']
                })
        except: pass
        log(f"  Rivers: {len(self.rivers)} parks")
        
        # Places (OSM)
        self.places = defaultdict(list)
        try:
            cursor = self.conn.execute('''
                SELECT park_id, name, place_type, lat, lon FROM osm_places 
                WHERE name IS NOT NULL
            ''')
            for row in cursor:
                self.places[row['park_id']].append({
                    'name': row['name'], 'type': row['place_type'],
                    'lat': row['lat'], 'lon': row['lon']
                })
        except: pass
        log(f"  Places: {len(self.places)} parks")
    
    def _load_park_info(self):
        self.parks = {}
        try:
            with open(DATA_DIR / 'keystones_with_boundaries.json') as f:
                for p in json.load(f):
                    self.parks[p['id']] = {
                        'name': p.get('name', p['id']),
                        'country': p.get('country', ''),
                        'area_km2': p.get('area_km2', 10000)
                    }
        except: pass
        log(f"Loaded info for {len(self.parks)} parks")
    
    def generate_fire_narratives(self):
        """Generate fire narratives with v5 trajectory analysis"""
        log("=" * 70)
        log(f"FIRE NARRATIVES V5 (from {MIN_DATE} onwards)")
        log("=" * 70)
        
        # Query trajectories from DB
        cursor = self.conn.execute('''
            SELECT park_id, feature_id, start_date, end_date, properties_json
            FROM feature_geometries
            WHERE feature_type = 'fire_trajectory' AND start_date >= ?
              AND (dist_to_park_km IS NULL OR dist_to_park_km <= 20)
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
            
            # Build individual narratives
            traj_narratives = []
            for i, t in enumerate(trajectories):
                traj_narratives.append({
                    'group_num': i + 1,
                    'feature_id': t.get('feature_id'),
                    'year': t.get('year'),
                    'start_date': t.get('start_date'),
                    'end_date': t.get('end_date'),
                    'days': t.get('days'),
                    'fires_total': t.get('fires_total', t.get('fires', 0)),
                    'total_frp': t.get('total_frp', 0),
                    'distance_km': t.get('distance_km'),
                    'avg_speed_km_day': t.get('avg_speed_km_day', t.get('speed_km_day', 0)),
                    'direction': t.get('direction'),
                    'group_type': t.get('group_type'),
                    'pct_inside': t.get('pct_inside'),
                    'position': t.get('position'),
                    'cross_border': t.get('cross_border'),
                    'affected_parks': t.get('affected_parks'),
                    'season': t.get('season'),
                    # V5 fields
                    'trajectory_type': t.get('trajectory_type', 'unknown'),
                    'zigzag_ratio': t.get('zigzag_ratio', 0),
                    'origin': {
                        'nearest_place': {'name': t.get('nearest_place'), 'distance_km': t.get('nearest_place_dist')} if t.get('nearest_place') else None,
                        'nearest_river': {'name': t.get('nearest_river'), 'distance_km': t.get('nearest_river_dist')} if t.get('nearest_river') else None
                    },
                    'narrative': t.get('narrative', '')
                })
            
            # Aggregate by year
            by_year = defaultdict(list)
            for t in trajectories:
                year = t.get('year') or (int(t['start_date'][:4]) if t.get('start_date') else 0)
                by_year[year].append(t)
            
            total_fires = sum(t.get('fires_total', t.get('fires', 0)) or 0 for t in trajectories)
            total_frp = sum(t.get('total_frp', 0) or 0 for t in trajectories)
            total_groups = len(trajectories)
            
            # Type breakdowns
            type_counts = defaultdict(int)
            traj_type_counts = defaultdict(int)
            for t in trajectories:
                type_counts[t.get('group_type', 'unknown')] += 1
                traj_type_counts[t.get('trajectory_type', 'unknown')] += 1
            
            # Position analysis
            management_count = sum(1 for t in trajectories if 'management' in (t.get('group_type', '') or '').lower())
            cross_border_count = sum(1 for t in trajectories if t.get('cross_border'))
            outside_count = sum(1 for t in trajectories if t.get('position') == 'entirely_outside')
            stopped_inside_count = sum(1 for t in trajectories if t.get('position') in ('ends_inside', 'contained'))
            transited_count = sum(1 for t in trajectories if t.get('position') == 'transits')
            
            # V5: Trajectory pattern analysis
            erratic_count = sum(1 for t in trajectories if t.get('trajectory_type') == 'erratic')
            zigzag_count = sum(1 for t in trajectories if t.get('trajectory_type') == 'zigzag')
            clean_count = sum(1 for t in trajectories if t.get('trajectory_type') == 'clean')
            avg_zigzag = sum(t.get('zigzag_ratio', 0) for t in trajectories) / max(1, total_groups)
            
            # Yearly summaries
            years_summary = []
            for year in sorted(by_year.keys()):
                yt = by_year[year]
                year_fires = sum(t.get('fires_total', 0) or 0 for t in yt)
                year_frp = sum(t.get('total_frp', 0) or 0 for t in yt)
                year_stopped = sum(1 for t in yt if t.get('position') in ('ends_inside', 'contained'))
                year_transited = sum(1 for t in yt if t.get('position') == 'transits')
                year_mgmt = sum(1 for t in yt if 'management' in (t.get('group_type', '') or '').lower())
                year_erratic = sum(1 for t in yt if t.get('trajectory_type') == 'erratic')
                avg_days = sum(t.get('days', 0) for t in yt) / max(1, len(yt))
                avg_speed = sum(t.get('avg_speed_km_day', 0) or 0 for t in yt) / max(1, len(yt))
                response_rate = (year_stopped / len(yt) * 100) if yt else 0
                
                years_summary.append({
                    'year': year,
                    'total_groups': len(yt),
                    'groups_per_km2': round(len(yt) / park_area * 1000, 4) if park_area > 0 else 0,
                    'stopped_inside': year_stopped,
                    'transited': year_transited,
                    'response_rate': round(response_rate, 1),
                    'total_fires': year_fires,
                    'total_frp': round(year_frp, 1),
                    'avg_days_burning': round(avg_days, 1),
                    'avg_speed_km_day': round(avg_speed, 1),
                    'management_fires': year_mgmt,
                    # V5 additions
                    'erratic_trajectories': year_erratic,
                })
            
            # Trend analysis
            trend_direction = 'insufficient_data'
            if len(years_summary) >= 3:
                recent = years_summary[-2:]
                older = years_summary[:-2]
                recent_avg = sum(y['total_groups'] for y in recent) / len(recent)
                older_avg = sum(y['total_groups'] for y in older) / len(older)
                if recent_avg > older_avg * 1.2:
                    trend_direction = 'increasing'
                elif recent_avg < older_avg * 0.8:
                    trend_direction = 'decreasing'
                else:
                    trend_direction = 'stable'
            
            # Worst/best years
            worst_year = worst_year_groups = best_year = best_year_rate = 0
            if years_summary:
                worst = max(years_summary, key=lambda y: y['total_groups'])
                best = min(years_summary, key=lambda y: y['total_groups'])
                worst_year = worst['year']
                worst_year_groups = worst['total_groups']
                best_year = best['year']
                best_year_rate = best.get('response_rate', 0)
            
            # Peak month
            month_counts = defaultdict(int)
            for t in trajectories:
                sd = t.get('start_date', '')
                if sd:
                    try:
                        month_counts[datetime.strptime(sd, '%Y-%m-%d').strftime('%B')] += 1
                    except: pass
            peak_month = max(month_counts, key=month_counts.get) if month_counts else None
            
            # Season breakdown
            season_counts = defaultdict(int)
            for t in trajectories:
                season_counts[t.get('season', 'unknown')] += 1
            
            # Direction breakdown
            direction_counts = defaultdict(int)
            for t in trajectories:
                direction_counts[t.get('direction', 'unknown')] += 1
            
            # Response rate
            stopped = sum(1 for t in trajectories if (t.get('pct_inside') or 0) > 80)
            response_rate = (stopped / total_groups * 100) if total_groups > 0 else 0
            
            # Key places
            park_places = self.places.get(park_id, [])[:10]
            key_places = [{'name': p['name'], 'type': p['type'], 'lat': p['lat'], 'lon': p['lon']} for p in park_places]
            
            # Build summary text
            year_range = f"{min(by_year.keys())}-{max(by_year.keys())}" if by_year else "N/A"
            summary_parts = [f"From {year_range}, {park_name} experienced {total_fires:,} fire detections across {total_groups} fire groups."]
            
            if management_count > 0:
                summary_parts.append(f"{management_count} ({management_count*100//total_groups}%) appear to be management burns.")
            if cross_border_count > 0:
                summary_parts.append(f"{cross_border_count} ({cross_border_count*100//total_groups}%) crossed boundaries.")
            if stopped_inside_count > 0:
                summary_parts.append(f"{stopped_inside_count} ({stopped_inside_count*100//total_groups}%) stopped inside.")
            if erratic_count > 0:
                summary_parts.append(f"{erratic_count} erratic trajectories detected.")
            if peak_month:
                summary_parts.append(f"Peak: {peak_month}.")
            if park_climate.get('dry_season'):
                summary_parts.append(f"Dry season: {park_climate['dry_season']}.")
            
            # Trend narrative
            trend_parts = []
            if trend_direction == 'increasing':
                trend_parts.append("Fire activity has increased in recent years.")
            elif trend_direction == 'decreasing':
                trend_parts.append("Fire activity has decreased in recent years.")
            else:
                trend_parts.append("Fire activity has remained relatively stable.")
            if worst_year:
                trend_parts.append(f"Worst year: {worst_year} ({worst_year_groups} groups).")
            
            # Build final narrative object
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
                    'narrative': ' '.join(trend_parts),
                    'avg_groups_per_km2': round(total_groups / max(1, len(years_summary)) / park_area * 1000, 4) if park_area > 0 else 0,
                    'peak_months': [peak_month] if peak_month else [],
                    'seasonality': f"dry season peaks {park_climate.get('dry_season', 'unknown')}" if park_climate.get('dry_season') else None
                },
                'response_rate': round(response_rate, 1),
                'total_fires': total_fires,
                'total_frp': round(total_frp, 1),
                'peak_month': peak_month,
                'total_groups': total_groups,
                'management_fires': management_count,
                'cross_border_groups': cross_border_count,
                'outside_park_groups': outside_count,
                'stopped_inside_groups': stopped_inside_count,
                'transited_groups': transited_count,
                'group_types': dict(type_counts),
                'seasons': dict(season_counts),
                'directions': dict(direction_counts),
                # V5 additions
                'trajectory_types': dict(traj_type_counts),
                'erratic_count': erratic_count,
                'zigzag_count': zigzag_count,
                'clean_count': clean_count,
                'avg_zigzag_ratio': round(avg_zigzag, 2),
                'climate': {
                    'dry_season': park_climate.get('dry_season'),
                    'rainy_season': park_climate.get('rainy_season'),
                    'climate_zone': park_climate.get('climate_zone')
                }
            }
            
            if idx % 20 == 0:
                log(f"[{idx}/{len(parks)}] {park_id}: {total_groups} groups, {erratic_count} erratic")
        
        # Export individual files
        EXPORT_DIR.mkdir(exist_ok=True)
        fire_dir = EXPORT_DIR / 'fire_narratives'
        fire_dir.mkdir(exist_ok=True)
        
        for park_id, data in narratives.items():
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
            self.conn.execute('''
                INSERT OR REPLACE INTO fire_narrative_cache 
                (park_id, narrative_json, computed_at, from_year, to_year) 
                VALUES (?, ?, ?, ?, ?)
            ''', (park_id, json.dumps(data), datetime.now().isoformat(), from_year, to_year))
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
            fires_total = sum(s.get('fires_5km') or 0 for s in settlements)
            deforest_total = sum(s.get('deforest_nearby_km2') or 0 for s in settlements)
            
            for s in settlements:
                class_counts[s.get('classification') or 'unknown'] += 1
            
            settlement_list = [{
                'id': s.get('id'),
                'lat': s.get('lat'), 'lon': s.get('lon'),
                'area_ha': round((s.get('area_m2') or 0) / 10000, 2),
                'population': s.get('population_est'),
                'households': s.get('households_est'),
                'nearest_place': s.get('nearest_place'),
                'distance_to_place_km': s.get('distance_to_place_km'),
                'settlement_type': s.get('settlement_type'),
                'in_buffer': s.get('in_buffer'),
                'classification': s.get('classification'),
                'confidence': s.get('classification_confidence'),
                'narrative': s.get('narrative'),
                'fires_5km': s.get('fires_5km'),
                'fire_seasonality': s.get('fire_seasonality'),
                'deforest_nearby_km2': s.get('deforest_nearby_km2'),
                'polygon_ids': s.get('polygon_ids')
            } for s in settlements]
            
            narratives[park_id] = {
                'park_id': park_id,
                'park_name': park_name,
                'summary': f"{park_name} has {len(settlements)} settlements, pop ~{total_pop:,}, {total_area/10000:.1f} ha total.",
                'settlement_count': len(settlements),
                'total_population': total_pop,
                'total_area_m2': total_area,
                'fires_nearby': fires_total,
                'deforestation_nearby_km2': round(deforest_total, 2),
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
            class_area = defaultdict(float)
            total_area = 0
            
            for e in events:
                by_year[e.get('year', 0)].append(e)
                cls = e.get('classification') or 'unknown'
                class_counts[cls] += 1
                class_area[cls] += e.get('area_km2') or 0
                total_area += e.get('area_km2') or 0
            
            # Trend calculation
            years = sorted(by_year.keys())
            trend_pct = 0
            if len(years) > 2:
                mid = len(years) // 2
                early_area = sum(e.get('area_km2') or 0 for y in years[:mid] for e in by_year[y])
                late_area = sum(e.get('area_km2') or 0 for y in years[mid:] for e in by_year[y])
                trend_pct = ((late_area - early_area) / early_area) * 100 if early_area > 0 else 0
            
            years_data = [{'year': y, 'events': len(evts), 'area_km2': round(sum(e.get('area_km2') or 0 for e in evts), 4)}
                          for y, evts in sorted(by_year.items())]
            
            event_list = [{
                'id': e.get('id'),
                'year': e.get('year'), 'lat': e.get('lat'), 'lon': e.get('lon'),
                'area_km2': e.get('area_km2'), 'pattern_type': e.get('pattern_type'),
                'classification': e.get('classification'),
                'confidence': e.get('classification_confidence'),
                'narrative': e.get('narrative'),
                'fires_same_year': e.get('fires_same_year'),
                'fire_ratio': e.get('fire_ratio'),
                'nearest_settlement_km': e.get('nearest_settlement_km'),
                'polygon_ids': e.get('polygon_ids')
            } for e in events]
            
            narratives[park_id] = {
                'park_id': park_id,
                'park_name': park_name,
                'summary': f"{park_name}: {total_area:.2f} km² forest loss across {len(years)} years ({min(years)}-{max(years)}).",
                'total_events': len(events),
                'total_area_km2': round(total_area, 4),
                'years': years,
                'trend_pct': round(trend_pct, 1),
                'classification_breakdown': dict(class_counts),
                'area_by_classification': {k: round(v, 4) for k, v in class_area.items()},
                'yearly_data': years_data,
                'events': event_list
            }
        
        with open(EXPORT_DIR / 'classified_deforestation.json', 'w') as f:
            json.dump(narratives, f)
        
        log(f"Deforestation narratives: {len(narratives)} parks")
        return narratives
    
    def generate_summary(self, fire, settlement, deforestation):
        """Generate summary file"""
        summary = {
            'generated_at': datetime.now().isoformat(),
            'version': 'v5',
            'fire': {
                'parks': len(fire),
                'total_groups': sum(n['total_groups'] for n in fire.values()),
                'total_fires': sum(n['total_fires'] for n in fire.values()),
                'erratic_trajectories': sum(n.get('erratic_count', 0) for n in fire.values()),
            },
            'settlement': {
                'parks': len(settlement),
                'total_settlements': sum(n['settlement_count'] for n in settlement.values()),
                'total_population': sum(n['total_population'] for n in settlement.values())
            },
            'deforestation': {
                'parks': len(deforestation),
                'total_events': sum(n['total_events'] for n in deforestation.values()),
                'total_area_km2': round(sum(n['total_area_km2'] for n in deforestation.values()), 2)
            }
        }
        
        with open(EXPORT_DIR / 'narrative_summary.json', 'w') as f:
            json.dump(summary, f, indent=2)
        
        log("\n" + "=" * 70)
        log("[SUMMARY]")
        log("=" * 70)
        log(f"Fire: {summary['fire']['parks']} parks, {summary['fire']['total_groups']} groups")
        log(f"Settlement: {summary['settlement']['parks']} parks, {summary['settlement']['total_settlements']} settlements")
        log(f"Deforestation: {summary['deforestation']['parks']} parks, {summary['deforestation']['total_area_km2']} km²")
        return summary
    
    def run(self):
        """Run all narrative generation"""
        log("=" * 70)
        log("NARRATIVE PRECOMPUTATION V5")
        log(f"Started: {datetime.now()}")
        log("=" * 70 + "\n")
        
        fire = self.generate_fire_narratives()
        settlement = self.generate_settlement_narratives()
        deforestation = self.generate_deforestation_narratives()
        
        self.generate_summary(fire, settlement, deforestation)
        
        self.conn.close()
        log("\n" + "=" * 70)
        log(f"Completed: {datetime.now()}")
        log("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="Precompute Narratives v5")
    parser.add_argument('--incremental', action='store_true', help='Incremental mode: only updated parks')
    parser.add_argument('--days', type=int, default=60, help='Days window for incremental (default: 60)')
    args = parser.parse_args()
    
    log("=" * 70)
    log(f"Precompute Narratives v5 (from {MIN_DATE})")
    log("=" * 70)
    
    NarrativeGeneratorV5().run()

if __name__ == "__main__":
    main()
