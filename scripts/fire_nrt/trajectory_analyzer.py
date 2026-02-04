#!/usr/bin/env python3
"""
Fire Trajectory Analyzer for Near Real-Time Data

Analyzes fire detections from the database to:
1. Detect and track fire groups
2. Assign anonymous names (Alpha, Bravo, etc.)
3. Identify groups currently inside park boundaries
4. Generate narratives for active incursions

Uses a 4-week analysis window for proper trajectory detection.
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Optional, Tuple, Any
import math

# Anonymous names for fire groups (NATO phonetic alphabet)
GROUP_NAMES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
    "India", "Juliet", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
    "Quebec", "Romeo", "Sierra", "Tango", "Uniform", "Victor", "Whiskey",
    "X-ray", "Yankee", "Zulu"
]

# Extend with numbered names if we have more than 26 groups
def get_group_name(index: int) -> str:
    """Get anonymous name for a fire group."""
    if index < len(GROUP_NAMES):
        return GROUP_NAMES[index]
    else:
        # Extend with numbered names: Alpha-2, Bravo-2, etc.
        cycle = index // len(GROUP_NAMES) + 1
        base_idx = index % len(GROUP_NAMES)
        return f"{GROUP_NAMES[base_idx]}-{cycle}"

BASE_DIR = Path(__file__).parent.parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
DATA_DIR = BASE_DIR / "data"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two points."""
    lat_diff = abs(lat2 - lat1) * 111
    lon_diff = abs(lon2 - lon1) * 111 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.sqrt(lat_diff**2 + lon_diff**2)


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate bearing from point 1 to point 2 in degrees."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
    
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def bearing_to_direction(bearing: float) -> str:
    """Convert bearing to compass direction."""
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = int((bearing + 11.25) / 22.5) % 16
    return directions[idx]


def load_park_data() -> List[Dict]:
    """Load keystones data."""
    try:
        with open(DATA_DIR / "keystones_with_boundaries.json") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load keystones: {e}")
        return []


def get_park_boundary(park: Dict) -> Optional[Dict]:
    """Get park boundary info including bbox."""
    geom = park.get('geometry')
    coords = park.get('coordinates', {})
    
    if geom and geom.get('type') == 'MultiPolygon':
        all_lons = []
        all_lats = []
        for polygon in geom['coordinates']:
            for ring in polygon:
                for c in ring:
                    all_lons.append(c[0])
                    all_lats.append(c[1])
        if all_lons and all_lats:
            return {
                'min_lon': min(all_lons),
                'max_lon': max(all_lons),
                'min_lat': min(all_lats),
                'max_lat': max(all_lats),
                'geometry': geom
            }
    
    # Fallback to point + buffer
    lat = coords.get('lat', 0)
    lon = coords.get('lon', 0)
    buffer = 0.5  # ~55km
    return {
        'min_lon': lon - buffer,
        'max_lon': lon + buffer,
        'min_lat': lat - buffer,
        'max_lat': lat + buffer,
        'geometry': None
    }


def point_in_polygon(lat: float, lon: float, geometry: Dict) -> bool:
    """Check if point is inside a MultiPolygon geometry."""
    if not geometry or geometry.get('type') != 'MultiPolygon':
        return False
    
    try:
        from shapely.geometry import shape, Point
        shp = shape(geometry)
        pt = Point(lon, lat)
        return shp.contains(pt)
    except ImportError:
        # Simple bbox check fallback
        return True


def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_fire_data_for_park(
    park_id: str,
    start_date: str,
    end_date: str,
    buffer_km: float = 300
) -> List[Dict]:
    """
    Get fire detections for a park and surrounding buffer region.
    
    Args:
        park_id: Park identifier (e.g., 'COD_Virunga')
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        buffer_km: Buffer around park for trajectory detection
    
    Returns:
        List of fire detection records
    """
    parks = load_park_data()
    park = None
    for p in parks:
        if p.get('id') == park_id:
            park = p
            break
    
    if not park:
        logger.warning(f"Park {park_id} not found")
        return []
    
    boundary = get_park_boundary(park)
    if not boundary:
        return []
    
    # Expand bbox by buffer
    buffer_deg = buffer_km / 111.0
    min_lat = boundary['min_lat'] - buffer_deg
    max_lat = boundary['max_lat'] + buffer_deg
    min_lon = boundary['min_lon'] - buffer_deg
    max_lon = boundary['max_lon'] + buffer_deg
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT latitude, longitude, acq_date, acq_time, brightness, frp, confidence
        FROM fire_detections
        WHERE latitude BETWEEN ? AND ?
          AND longitude BETWEEN ? AND ?
          AND acq_date BETWEEN ? AND ?
        ORDER BY acq_date, acq_time
    """, (min_lat, max_lat, min_lon, max_lon, start_date, end_date))
    
    fires = []
    for row in cursor.fetchall():
        fires.append({
            'lat': row['latitude'],
            'lon': row['longitude'],
            'date': row['acq_date'],
            'time': row['acq_time'] or '',
            'brightness': row['brightness'] or 0,
            'frp': row['frp'] or 0,
            'confidence': row['confidence'] or 'n'
        })
    
    conn.close()
    return fires


def detect_daily_clusters(
    fires: List[Dict],
    eps_km: float = 15,
    min_fires: int = 5
) -> Dict[str, List[Dict]]:
    """
    Detect spatial fire clusters for each day.
    
    Uses simple grid-based clustering instead of DBSCAN for speed.
    """
    daily_clusters = {}
    
    # Group fires by date
    by_date = defaultdict(list)
    for f in fires:
        by_date[f['date']].append(f)
    
    eps_deg = eps_km / 111.0
    
    for date, day_fires in by_date.items():
        if len(day_fires) < min_fires:
            continue
        
        # Simple grid-based clustering
        grid = defaultdict(list)
        for f in day_fires:
            cell = (int(f['lat'] / eps_deg), int(f['lon'] / eps_deg))
            grid[cell].append(f)
        
        clusters = []
        cid = 0
        
        for cell, cell_fires in grid.items():
            if len(cell_fires) < min_fires // 2:
                continue
            
            # Merge adjacent cells
            merged = list(cell_fires)
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx == 0 and dy == 0:
                        continue
                    adj = (cell[0] + dx, cell[1] + dy)
                    if adj in grid:
                        merged.extend(grid[adj])
            
            if len(merged) >= min_fires:
                lats = [f['lat'] for f in merged]
                lons = [f['lon'] for f in merged]
                frps = [f['frp'] for f in merged if f['frp'] > 0]
                
                clusters.append({
                    'date': date,
                    'cid': cid,
                    'lat': sum(lats) / len(lats),
                    'lon': sum(lons) / len(lons),
                    'lat_min': min(lats),
                    'lat_max': max(lats),
                    'lon_min': min(lons),
                    'lon_max': max(lons),
                    'fires': len(merged),
                    'frp': sum(frps) if frps else 0,
                    'spread_km': max(
                        (max(lats) - min(lats)) * 111,
                        (max(lons) - min(lons)) * 111 * math.cos(math.radians(sum(lats)/len(lats)))
                    )
                })
                cid += 1
        
        if clusters:
            daily_clusters[date] = clusters
    
    return daily_clusters


def track_clusters(
    daily_clusters: Dict[str, List[Dict]],
    max_link_km: float = 25,
    max_gap_days: int = 3
) -> List[List[Dict]]:
    """
    Track clusters across days to build trajectories.
    """
    trajectories = []
    used = set()
    sorted_dates = sorted(daily_clusters.keys())
    
    if not sorted_dates:
        return []
    
    for start_idx, start_date in enumerate(sorted_dates):
        for cluster in daily_clusters[start_date]:
            key = (start_date, cluster['cid'])
            if key in used:
                continue
            
            traj = [cluster]
            used.add(key)
            current = cluster
            
            for next_idx in range(start_idx + 1, len(sorted_dates)):
                next_date = sorted_dates[next_idx]
                
                # Calculate date gap
                d1 = datetime.strptime(current['date'], '%Y-%m-%d')
                d2 = datetime.strptime(next_date, '%Y-%m-%d')
                date_gap = (d2 - d1).days
                
                if date_gap > max_gap_days:
                    break
                
                best = None
                best_dist = float('inf')
                
                for nc in daily_clusters[next_date]:
                    nkey = (next_date, nc['cid'])
                    if nkey in used:
                        continue
                    
                    dist = distance_km(current['lat'], current['lon'], nc['lat'], nc['lon'])
                    if dist <= max_link_km and dist < best_dist:
                        best = nc
                        best_dist = dist
                
                if best:
                    traj.append(best)
                    used.add((next_date, best['cid']))
                    current = best
            
            # Only keep trajectories with at least 3 days
            if len(traj) >= 3:
                trajectories.append(traj)
    
    return trajectories


def classify_trajectory(traj: List[Dict]) -> Tuple[str, Dict]:
    """
    Classify trajectory based on movement pattern.
    """
    if len(traj) < 3:
        return 'unknown', {}
    
    start, end = traj[0], traj[-1]
    
    total_fires = sum(c['fires'] for c in traj)
    days = len(traj)
    
    # Net movement
    net_south = (start['lat'] - end['lat']) * 111
    net_east = (end['lon'] - start['lon']) * 111
    total_distance = distance_km(start['lat'], start['lon'], end['lat'], end['lon'])
    
    # Daily movements
    movements = []
    for i in range(1, len(traj)):
        d = distance_km(traj[i-1]['lat'], traj[i-1]['lon'], 
                       traj[i]['lat'], traj[i]['lon'])
        movements.append(d)
    
    avg_speed = sum(movements) / len(movements) if movements else 0
    max_speed = max(movements) if movements else 0
    avg_spread = sum(c['spread_km'] for c in traj) / len(traj)
    
    # Bearing
    brg = bearing_deg(start['lat'], start['lon'], end['lat'], end['lon'])
    direction = bearing_to_direction(brg)
    
    metrics = {
        'days': days,
        'fires': total_fires,
        'net_south_km': round(net_south, 1),
        'net_east_km': round(net_east, 1),
        'total_distance_km': round(total_distance, 1),
        'avg_speed_km_day': round(avg_speed, 1),
        'max_speed_km_day': round(max_speed, 1),
        'avg_spread_km': round(avg_spread, 1),
        'bearing': round(brg, 1),
        'direction': direction,
        'start_date': start['date'],
        'end_date': end['date'],
        'start_lat': round(start['lat'], 4),
        'start_lon': round(start['lon'], 4),
        'end_lat': round(end['lat'], 4),
        'end_lon': round(end['lon'], 4)
    }
    
    # Classification rules
    if avg_speed > 30:
        group_type = 'management_fast'  # Aircraft
    elif avg_speed > 15:
        group_type = 'management_vehicle' if avg_spread > 30 else 'herder_fast'
    elif avg_speed > 5:
        group_type = 'transhumance' if net_south > 20 else 'herder_local'
    elif avg_speed > 2:
        group_type = 'transhumance_slow' if (days > 10 and net_south > 15) else 'local_burning'
    else:
        group_type = 'village_persistent' if days > 7 else 'local_stationary'
    
    return group_type, metrics


def analyze_trajectory_status(
    traj: List[Dict],
    park_boundary: Dict,
    today: str = None
) -> Dict:
    """
    Analyze trajectory status relative to park boundary.
    
    Returns status info including whether group is currently inside.
    """
    if not today:
        today = datetime.now().strftime('%Y-%m-%d')
    
    result = {
        'is_active': False,
        'is_inside': False,
        'status': 'unknown',
        'last_seen': None,
        'days_since_last': None,
        'entry_date': None,
        'days_inside': 0,
        'points_inside': []
    }
    
    if not traj:
        return result
    
    last_point = traj[-1]
    result['last_seen'] = last_point['date']
    
    # Calculate days since last detection
    last_date = datetime.strptime(last_point['date'], '%Y-%m-%d')
    today_date = datetime.strptime(today, '%Y-%m-%d')
    result['days_since_last'] = (today_date - last_date).days
    
    # Group is "active" if seen in last 3 days
    result['is_active'] = result['days_since_last'] <= 3
    
    # Check which points are inside park
    geometry = park_boundary.get('geometry')
    min_lat = park_boundary['min_lat']
    max_lat = park_boundary['max_lat']
    min_lon = park_boundary['min_lon']
    max_lon = park_boundary['max_lon']
    
    for i, pt in enumerate(traj):
        # Simple bbox check first
        inside_bbox = (min_lat <= pt['lat'] <= max_lat and 
                      min_lon <= pt['lon'] <= max_lon)
        
        if inside_bbox:
            # Check actual geometry if available
            if geometry:
                inside = point_in_polygon(pt['lat'], pt['lon'], geometry)
            else:
                inside = True
            
            if inside:
                result['points_inside'].append({
                    'date': pt['date'],
                    'lat': pt['lat'],
                    'lon': pt['lon'],
                    'fires': pt['fires']
                })
                if not result['entry_date']:
                    result['entry_date'] = pt['date']
    
    result['days_inside'] = len(result['points_inside'])
    
    # Determine current status
    if result['points_inside']:
        last_inside = result['points_inside'][-1]
        if last_inside['date'] == last_point['date']:
            result['is_inside'] = True
            if result['is_active']:
                result['status'] = 'ACTIVE_INSIDE'
            else:
                result['status'] = 'STOPPED_INSIDE'
        else:
            result['status'] = 'EXITED'
    else:
        result['status'] = 'OUTSIDE'
    
    return result


def analyze_park_trajectories(
    park_id: str,
    analysis_window_days: int = 28,
    end_date: str = None
) -> Dict:
    """
    Full trajectory analysis for a park.
    
    Args:
        park_id: Park identifier
        analysis_window_days: Days of data to analyze (default 28 for 4 weeks)
        end_date: End date for analysis (default today)
    
    Returns:
        Analysis results including active groups and narratives
    """
    if not end_date:
        end_date = datetime.now().strftime('%Y-%m-%d')
    
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    start_dt = end_dt - timedelta(days=analysis_window_days)
    start_date = start_dt.strftime('%Y-%m-%d')
    
    logger.info(f"Analyzing {park_id} from {start_date} to {end_date}")
    
    # Load park data
    parks = load_park_data()
    park = None
    for p in parks:
        if p.get('id') == park_id:
            park = p
            break
    
    if not park:
        return {'error': f'Park {park_id} not found'}
    
    park_name = park.get('name', park_id)
    boundary = get_park_boundary(park)
    
    # Get fire data
    fires = get_fire_data_for_park(park_id, start_date, end_date)
    logger.info(f"Found {len(fires)} fire detections")
    
    if len(fires) < 10:
        return {
            'park_id': park_id,
            'park_name': park_name,
            'analysis_period': f"{start_date} to {end_date}",
            'total_fires': len(fires),
            'groups': [],
            'active_groups': [],
            'narrative': f"Minimal fire activity detected near {park_name} in the past {analysis_window_days} days."
        }
    
    # Detect clusters and track trajectories
    daily_clusters = detect_daily_clusters(fires)
    trajectories = track_clusters(daily_clusters)
    logger.info(f"Detected {len(trajectories)} fire group trajectories")
    
    # Analyze each trajectory
    groups = []
    for i, traj in enumerate(trajectories):
        group_name = get_group_name(i)
        group_type, metrics = classify_trajectory(traj)
        status = analyze_trajectory_status(traj, boundary, end_date)
        
        groups.append({
            'name': group_name,
            'type': group_type,
            'metrics': metrics,
            'status': status,
            'trajectory': [
                {'date': pt['date'], 'lat': round(pt['lat'], 4), 'lon': round(pt['lon'], 4), 'fires': pt['fires']}
                for pt in traj
            ]
        })
    
    # Sort by activity and inside status
    groups.sort(key=lambda g: (
        -int(g['status']['is_active']),
        -int(g['status']['is_inside']),
        -g['status']['days_inside']
    ))
    
    # Filter active groups
    active_groups = [g for g in groups if g['status']['is_active']]
    active_inside = [g for g in active_groups if g['status']['is_inside']]
    
    # Generate narrative
    narrative = generate_narrative(park_name, groups, active_groups, active_inside, start_date, end_date)
    
    return {
        'park_id': park_id,
        'park_name': park_name,
        'analysis_period': f"{start_date} to {end_date}",
        'total_fires': len(fires),
        'total_groups': len(groups),
        'active_groups_count': len(active_groups),
        'groups_inside_count': len(active_inside),
        'groups': groups,
        'active_groups': active_groups,
        'groups_inside': active_inside,
        'narrative': narrative
    }


def generate_narrative(
    park_name: str,
    all_groups: List[Dict],
    active_groups: List[Dict],
    groups_inside: List[Dict],
    start_date: str,
    end_date: str
) -> str:
    """Generate human-readable narrative for fire group activity."""
    
    parts = []
    
    # Opening
    period_desc = f"the past {(datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days} days"
    
    if not all_groups:
        return f"No significant fire group activity detected near {park_name} over {period_desc}."
    
    # Summary
    parts.append(f"Over {period_desc}, {len(all_groups)} distinct fire groups were tracked near {park_name}.")
    
    # Active groups inside - PRIORITY
    if groups_inside:
        parts.append("")
        parts.append("⚠️ **ACTIVE INCURSIONS:**")
        for g in groups_inside:
            name = g['name']
            metrics = g['metrics']
            status = g['status']
            days_inside = status['days_inside']
            last_seen = status['last_seen']
            days_since = status['days_since_last']
            
            direction = metrics.get('direction', 'unknown')
            speed = metrics.get('avg_speed_km_day', 0)
            
            freshness = "TODAY" if days_since == 0 else f"{days_since} day{'s' if days_since > 1 else ''} ago"
            
            parts.append(f"• **Group {name}**: Active inside park, last detected {freshness}. "
                        f"Tracked for {metrics['days']} days moving {direction} at ~{speed:.1f} km/day. "
                        f"Inside park for {days_inside} days with {status['points_inside'][-1]['fires']} fires at last detection.")
    
    # Other active groups (approaching or nearby)
    approaching = [g for g in active_groups if not g['status']['is_inside']]
    if approaching:
        parts.append("")
        parts.append("**Nearby Active Groups:**")
        for g in approaching[:5]:  # Top 5
            name = g['name']
            metrics = g['metrics']
            direction = metrics.get('direction', 'unknown')
            parts.append(f"• **Group {name}**: Moving {direction}, {metrics['days']} days tracked, "
                        f"~{metrics['avg_speed_km_day']:.1f} km/day. Last seen {g['status']['days_since_last']} day(s) ago.")
    
    # Groups that transited
    transited = [g for g in all_groups if g['status']['status'] == 'EXITED' and g['status']['days_inside'] > 0]
    if transited:
        parts.append("")
        parts.append(f"**Recent Transits:** {len(transited)} groups passed through the park this period.")
        for g in transited[:3]:
            name = g['name']
            days_inside = g['status']['days_inside']
            parts.append(f"• Group {name}: Spent {days_inside} days inside before exiting.")
    
    # Groups that stopped inside
    stopped_inside = [g for g in all_groups if g['status']['status'] == 'STOPPED_INSIDE' and not g['status']['is_active']]
    if stopped_inside:
        parts.append("")
        parts.append(f"**Potential Staff Contact:** {len(stopped_inside)} groups stopped burning inside the park "
                    "(possible ranger intervention or end of activity).")
    
    return "\n".join(parts)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Analyze fire trajectories for a park')
    parser.add_argument('--park', type=str, help='Park ID (e.g., COD_Virunga)')
    parser.add_argument('--all', action='store_true', help='Analyze all parks')
    parser.add_argument('--days', type=int, default=28, help='Analysis window in days (default 28)')
    parser.add_argument('--output', type=str, help='Output JSON file')
    parser.add_argument('--active-only', action='store_true', help='Only show parks with active incursions')
    
    args = parser.parse_args()
    
    if args.park:
        result = analyze_park_trajectories(args.park, args.days)
        print(json.dumps(result, indent=2))
        
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(result, f, indent=2)
    
    elif args.all:
        parks = load_park_data()
        results = []
        
        for park in parks:
            park_id = park.get('id')
            if not park_id:
                continue
            
            try:
                result = analyze_park_trajectories(park_id, args.days)
                
                if args.active_only and result.get('groups_inside_count', 0) == 0:
                    continue
                
                results.append(result)
                
                if result.get('groups_inside_count', 0) > 0:
                    print(f"\n🔥 {park_id}: {result['groups_inside_count']} active group(s) inside!")
                    print(result['narrative'])
            except Exception as e:
                logger.error(f"Error analyzing {park_id}: {e}")
        
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(results, f, indent=2)
        
        # Summary
        total_active = sum(r.get('groups_inside_count', 0) for r in results)
        print(f"\n=== SUMMARY ===")
        print(f"Parks analyzed: {len(results)}")
        print(f"Parks with active incursions: {sum(1 for r in results if r.get('groups_inside_count', 0) > 0)}")
        print(f"Total active groups inside parks: {total_active}")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
