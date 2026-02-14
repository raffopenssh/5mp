#!/usr/bin/env python3
"""
Precompute all narratives with verbose logging.
No caching - processes everything fresh.
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

class NarrativeGenerator:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        self._load_context()
    
    def _load_context(self):
        """Load all context data"""
        print("Loading context data...")
        
        # Climate
        self.climate = {}
        climate_file = DATA_DIR / 'climate' / 'park_climate.json'
        if climate_file.exists():
            with open(climate_file) as f:
                self.climate = json.load(f)
        print(f"  Climate: {len(self.climate)} parks")
        
        # Rivers
        self.rivers = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT pr.park_id, r.name, r.stream_order, r.discharge_cms
            FROM park_rivers pr
            JOIN rivers r ON r.hyriv_id = pr.hyriv_id
            WHERE r.name IS NOT NULL AND r.name != ''
            ORDER BY r.discharge_cms DESC
        ''')
        for row in cursor:
            self.rivers[row['park_id']].append({
                'name': row['name'],
                'order': row['stream_order'],
                'discharge': row['discharge_cms']
            })
        print(f"  Rivers: {len(self.rivers)} parks")
        
        # Places
        self.places = defaultdict(list)
        cursor = self.conn.execute('''
            SELECT park_id, name, place_type FROM osm_places
            WHERE name IS NOT NULL
        ''')
        for row in cursor:
            self.places[row['park_id']].append({
                'name': row['name'],
                'type': row['place_type']
            })
        print(f"  Places: {len(self.places)} parks")
        
        print("Context loaded.\n")
    
    def generate_fire_narratives(self):
        """Generate fire narratives from trajectory JSONs"""
        print("=" * 70)
        print("[FIRE NARRATIVES]")
        print("=" * 70)
        
        narratives = {}
        json_files = sorted(TRAJ_DIR.glob('*.json'))
        total_files = len(json_files)
        
        for idx, json_file in enumerate(json_files, 1):
            park_id = json_file.stem
            
            with open(json_file) as f:
                trajectories = json.load(f)
            
            if not trajectories:
                print(f"[FIRE {idx}/{total_files}] {park_id}: SKIP (no trajectories)")
                continue
            
            # Process each trajectory
            traj_narratives = []
            for t in trajectories:
                narrative = self._build_fire_narrative(t, park_id)
                traj_narratives.append({
                    'feature_id': t['feature_id'],
                    'year': t['year'],
                    'group_type': t['group_type'],
                    'refined_type': t.get('refined_type'),
                    'start_date': t['start_date'],
                    'end_date': t['end_date'],
                    'days': t['days'],
                    'fires': t['fires_total'],
                    'season': t.get('season'),
                    'direction': t.get('direction', {}).get('direction'),
                    'distance_km': t.get('total_distance_km'),
                    'avg_speed': t.get('avg_speed_km_day'),
                    'rivers_crossed': t.get('rivers_crossed', []),
                    'origin_place': t.get('origin', {}).get('nearest_place', {}).get('name') if t.get('origin', {}).get('nearest_place') else None,
                    'origin_river': t.get('origin', {}).get('nearest_river', {}).get('name') if t.get('origin', {}).get('nearest_river') else None,
                    'narrative': narrative,
                    'coordinates_with_time': t.get('coordinates_with_time', [])
                })
            
            # Group stats
            by_year = defaultdict(list)
            for t in trajectories:
                by_year[t.get('year', 0)].append(t)
            
            total_fires = sum(t.get('fires_total', 0) for t in trajectories)
            total_groups = len(trajectories)
            
            type_counts = defaultdict(int)
            for t in trajectories:
                type_counts[t.get('group_type', 'unknown')] += 1
            
            all_rivers = set()
            for t in trajectories:
                all_rivers.update(t.get('rivers_crossed', []))
            
            directions = defaultdict(int)
            for t in trajectories:
                d = t.get('direction', {})
                if d and d.get('direction'):
                    directions[d['direction']] += 1
            
            seasons = defaultdict(int)
            for t in trajectories:
                seasons[t.get('season', 'unknown')] += 1
            
            park_climate = self.climate.get(park_id, {})
            
            # Compute yearly statistics for trend
            years_summary = []
            park_area_km2 = 10000  # default
            try:
                with open(DATA_DIR / 'keystones_with_boundaries.json') as kf:
                    for p in json.load(kf):
                        if p['id'] == park_id:
                            park_area_km2 = p.get('area_km2', 10000)
                            break
            except:
                pass
            
            for year in sorted(by_year.keys()):
                year_trajs = by_year[year]
                total_year_groups = len(year_trajs)
                stopped = sum(1 for t in year_trajs if t.get('outcome') == 'stopped_inside')
                total_year_fires = sum(t.get('fires_total', 0) for t in year_trajs)
                avg_days = sum(t.get('days', 0) for t in year_trajs) / max(1, total_year_groups)
                
                years_summary.append({
                    'year': year,
                    'total_groups': total_year_groups,
                    'groups_per_km2': round(total_year_groups / park_area_km2 * 1000, 4) if park_area_km2 > 0 else 0,
                    'stopped_inside': stopped,
                    'transited': total_year_groups - stopped,
                    'response_rate': round(stopped / max(1, total_year_groups) * 100, 1),
                    'total_fires': total_year_fires,
                    'avg_days_burning': round(avg_days, 1)
                })
            
            # Determine trend direction
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
            else:
                trend_direction = 'insufficient_data'
            
            # Peak month
            month_counts = defaultdict(int)
            for t in trajectories:
                sd = t.get('start_date', '')
                if sd:
                    try:
                        from datetime import datetime
                        dt = datetime.strptime(sd, '%Y-%m-%d')
                        month_counts[dt.strftime('%B')] += 1
                    except:
                        pass
            peak_month = max(month_counts, key=month_counts.get) if month_counts else None
            
            # Get park name
            park_name = park_id.split('_', 1)[1].replace('_', ' ') if '_' in park_id else park_id
            
            # Summary text
            response_rate = sum(1 for t in trajectories if t.get('outcome') == 'stopped_inside') / max(1, total_groups) * 100
            summary = f"From {min(by_year.keys())}-{max(by_year.keys())}, {park_name} experienced {total_fires:,} fire detections across {total_groups} fire groups."
            if response_rate > 50:
                summary += f" {round(response_rate)}% of groups were stopped inside the park."
            if peak_month:
                summary += f" Peak fire activity occurs in {peak_month}."
            if park_climate.get('dry_season'):
                summary += f" Dry season: {park_climate.get('dry_season')}."
            
            narratives[park_id] = {
                'park_id': park_id,
                'park_name': park_name,
                'summary': summary,
                'total_fires': total_fires,
                'total_groups': total_groups,
                'response_rate': round(response_rate, 1),
                'peak_month': peak_month,
                'group_types': dict(type_counts),
                'seasons': dict(seasons),
                'directions': dict(directions),
                'rivers_crossed': list(all_rivers),
                'trend': {
                    'years': years_summary,
                    'trend_direction': trend_direction,
                    'avg_response_rate': round(sum(y['response_rate'] for y in years_summary) / max(1, len(years_summary)), 1) if years_summary else 0
                },
                'climate': {
                    'climate_zone': park_climate.get('climate_zone'),
                    'dry_season': park_climate.get('dry_season'),
                    'rainy_season': park_climate.get('rainy_season'),
                    'temp_annual_c': park_climate.get('temp_annual_c'),
                    'precip_annual_mm': park_climate.get('precip_annual_mm')
                },
                'narratives': traj_narratives
            }
            
            print(f"[FIRE {idx}/{total_files}] {park_id}: {total_groups} groups, {total_fires} fires, {len(traj_narratives)} narratives")
        
        # Export
        EXPORT_DIR.mkdir(exist_ok=True)
        with open(EXPORT_DIR / 'fire_narratives.json', 'w') as f:
            json.dump(narratives, f)
        
        print(f"\nFire narratives exported: {len(narratives)} parks")
        return narratives
    
    def _build_fire_narrative(self, traj, park_id):
        """Build narrative text for a single trajectory"""
        parts = []
        
        origin = traj.get('origin', {})
        origin_place = origin.get('nearest_place')
        origin_river = origin.get('nearest_river')
        origin_settlement = origin.get('nearest_settlement')
        
        if origin_place:
            parts.append(f"Fire group originated {origin_place['distance_km']}km from {origin_place['name']} ({origin_place['type']})")
        elif origin_river:
            parts.append(f"Fire group originated {origin_river['distance_km']}km from {origin_river['name']}")
        else:
            parts.append(f"Fire group originated at ({traj['coordinates'][0][1]:.2f}°, {traj['coordinates'][0][0]:.2f}°)")
        
        direction = traj.get('direction', {})
        if direction:
            parts.append(f"moving {direction.get('direction', 'unknown')} (bearing {direction.get('bearing', 0):.0f}°)")
        
        parts.append(f"on {traj['start_date']}")
        if traj.get('season'):
            parts.append(f"({traj['season']} season)")
        
        parts.append(f"Burned for {traj['days']} days ({traj['fires_total']} fire detections).")
        
        rivers = traj.get('rivers_crossed', [])
        if rivers:
            parts.append(f"Crossed {', '.join(rivers)}.")
        
        if origin_settlement and origin_settlement.get('distance_km', 999) < 15:
            parts.append(f"Near {origin_settlement.get('name', 'settlement')} ({origin_settlement.get('class', 'unknown')}, pop ~{origin_settlement.get('population', 0)}).")
        
        return ' '.join(parts)
    
    def generate_settlement_narratives(self):
        """Generate settlement narratives"""
        print("\n" + "=" * 70)
        print("[SETTLEMENT NARRATIVES]")
        print("=" * 70)
        
        cursor = self.conn.execute('''
            SELECT park_id, id, lat, lon, area_m2, population_est,
                   nearest_place, classification, classification_confidence, 
                   narrative
            FROM park_settlements
            ORDER BY park_id, population_est DESC
        ''')
        
        by_park = defaultdict(list)
        for row in cursor:
            by_park[row['park_id']].append({
                'id': row['id'],
                'lat': row['lat'],
                'lon': row['lon'],
                'area_m2': row['area_m2'],
                'population': row['population_est'],
                'nearest_place': row['nearest_place'],
                'classification': row['classification'],
                'confidence': row['classification_confidence'],
                'narrative': row['narrative']
            })
        
        narratives = {}
        parks = sorted(by_park.keys())
        total_parks = len(parks)
        
        for idx, park_id in enumerate(parks, 1):
            settlements = by_park[park_id]
            
            class_counts = defaultdict(int)
            total_pop = 0
            total_area = 0
            
            for s in settlements:
                class_counts[s['classification'] or 'unknown'] += 1
                total_pop += s['population'] or 0
                total_area += s['area_m2'] or 0
            
            narratives[park_id] = {
                'park_id': park_id,
                'settlement_count': len(settlements),
                'total_population': total_pop,
                'total_area_m2': total_area,
                'classification_breakdown': dict(class_counts),
                'settlements': settlements
            }
            
            print(f"[SETTLEMENT {idx}/{total_parks}] {park_id}: {len(settlements)} settlements, pop {total_pop}")
        
        with open(EXPORT_DIR / 'settlement_narratives.json', 'w') as f:
            json.dump(narratives, f)
        
        print(f"\nSettlement narratives exported: {len(narratives)} parks")
        return narratives
    
    def generate_deforestation_narratives(self):
        """Generate deforestation narratives"""
        print("\n" + "=" * 70)
        print("[DEFORESTATION NARRATIVES]")
        print("=" * 70)
        
        cursor = self.conn.execute('''
            SELECT park_id, id, year, lat, lon, area_km2,
                   classification, classification_confidence, 
                   narrative
            FROM deforestation_events
            ORDER BY park_id, year DESC
        ''')
        
        by_park = defaultdict(list)
        for row in cursor:
            by_park[row['park_id']].append({
                'id': row['id'],
                'year': row['year'],
                'lat': row['lat'],
                'lon': row['lon'],
                'area_km2': row['area_km2'],
                'classification': row['classification'],
                'confidence': row['classification_confidence'],
                'narrative': row['narrative']
            })
        
        narratives = {}
        parks = sorted(by_park.keys())
        total_parks = len(parks)
        
        for idx, park_id in enumerate(parks, 1):
            events = by_park[park_id]
            
            total_area = sum(e['area_km2'] or 0 for e in events)
            years = sorted(set(e['year'] for e in events))
            
            class_counts = defaultdict(int)
            class_area = defaultdict(float)
            for e in events:
                cls = e['classification'] or 'unknown'
                class_counts[cls] += 1
                class_area[cls] += e['area_km2'] or 0
            
            if len(years) > 2:
                mid = len(years) // 2
                early_years = set(years[:mid])
                late_years = set(years[mid:])
                early_area = sum(e['area_km2'] or 0 for e in events if e['year'] in early_years)
                late_area = sum(e['area_km2'] or 0 for e in events if e['year'] in late_years)
                trend_pct = ((late_area - early_area) / early_area) * 100 if early_area > 0 else 0
            else:
                trend_pct = 0
            
            narratives[park_id] = {
                'park_id': park_id,
                'event_count': len(events),
                'total_area_km2': round(total_area, 2),
                'years': years,
                'classification_breakdown': dict(class_counts),
                'area_by_classification': {k: round(v, 2) for k, v in class_area.items()},
                'trend_pct': round(trend_pct, 1),
                'events': events
            }
            
            print(f"[DEFORESTATION {idx}/{total_parks}] {park_id}: {len(events)} events, {total_area:.1f} km²")
        
        with open(EXPORT_DIR / 'deforestation_narratives.json', 'w') as f:
            json.dump(narratives, f)
        
        print(f"\nDeforestation narratives exported: {len(narratives)} parks")
        return narratives
    
    def generate_summary(self, fire, settlement, deforestation):
        """Generate summary file"""
        summary = {
            'generated_at': datetime.now().isoformat(),
            'fire': {
                'parks': len(fire),
                'total_groups': sum(n['total_groups'] for n in fire.values()),
                'total_fires': sum(n['total_fires'] for n in fire.values())
            },
            'settlement': {
                'parks': len(settlement),
                'total_settlements': sum(n['settlement_count'] for n in settlement.values()),
                'total_population': sum(n['total_population'] for n in settlement.values())
            },
            'deforestation': {
                'parks': len(deforestation),
                'total_events': sum(n['event_count'] for n in deforestation.values()),
                'total_area_km2': round(sum(n['total_area_km2'] for n in deforestation.values()), 2)
            }
        }
        
        with open(EXPORT_DIR / 'narrative_summary.json', 'w') as f:
            json.dump(summary, f, indent=2)
        
        print("\n" + "=" * 70)
        print("[SUMMARY]")
        print("=" * 70)
        print(f"Fire: {summary['fire']['parks']} parks, {summary['fire']['total_groups']} groups, {summary['fire']['total_fires']} fires")
        print(f"Settlement: {summary['settlement']['parks']} parks, {summary['settlement']['total_settlements']} settlements")
        print(f"Deforestation: {summary['deforestation']['parks']} parks, {summary['deforestation']['total_area_km2']} km²")
        return summary
    
    def run(self):
        """Run all narrative generation"""
        print("=" * 70)
        print("NARRATIVE PRECOMPUTATION (NO CACHE)")
        print(f"Started: {datetime.now()}")
        print("=" * 70 + "\n")
        
        fire = self.generate_fire_narratives()
        settlement = self.generate_settlement_narratives()
        deforestation = self.generate_deforestation_narratives()
        
        self.generate_summary(fire, settlement, deforestation)
        
        print("\n" + "=" * 70)
        print(f"Completed: {datetime.now()}")
        print("=" * 70)

if __name__ == '__main__':
    generator = NarrativeGenerator()
    generator.run()
