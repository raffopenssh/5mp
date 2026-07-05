#!/usr/bin/env python3
"""
STEP 1: Rebuild Fire Analysis - Daily Centroids with Cross-Park Detection

Reads from all fire sources, creates daily centroid trajectories.
Cross-park detection only at trajectory level for efficiency.
"""

import json, sqlite3, math, sys, glob, zipfile, io, csv, time
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import argparse

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_FILE = BASE_DIR / "data/keystones_with_boundaries.json"
OUTPUT_DIR = BASE_DIR / "data" / "fire_analysis"

MIN_DATE = '2020-01-01'
BUFFER_KM = 30
CLUSTER_DIST_KM = 5
MAX_LINK_KM = 25
MAX_GAP_DAYS = 3
MIN_TRAJ_DAYS = 2

sys.stdout.reconfigure(line_buffering=True)

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(min(1, a)))

def bearing_to_dir(b):
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    return dirs[int((b + 11.25) / 22.5) % 16]

def point_in_ring(x, y, ring):
    n, inside, j = len(ring), False, len(ring) - 1
    for i in range(n):
        xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

def point_in_polygon(lon, lat, geometry):
    coords = geometry.get('coordinates', [])
    if geometry['type'] == 'MultiPolygon':
        return any(point_in_ring(lon, lat, poly[0]) for poly in coords)
    elif geometry['type'] == 'Polygon':
        return point_in_ring(lon, lat, coords[0])
    return False

# Country mapping for archive
COUNTRY_MAP = {
    'AGO': 'Angola', 'BEN': 'Benin', 'BWA': 'Botswana', 'CMR': 'Cameroon',
    'CAF': 'Central_African_Republic', 'TCD': 'Chad', 'COD': 'Democratic_Republic_of_the_Congo',
    'COG': 'Republic_of_Congo', 'CIV': 'Cote_d_Ivoire', 'DZA': 'Algeria',
    'GNQ': 'Equatorial_Guinea', 'ETH': 'Ethiopia', 'GAB': 'Gabon', 'GHA': 'Ghana',
    'KEN': 'Kenya', 'LSO': 'Lesotho', 'LBR': 'Liberia', 'MWI': 'Malawi',
    'MLI': 'Mali', 'MOZ': 'Mozambique', 'NAM': 'Namibia', 'NER': 'Niger',
    'NGA': 'Nigeria', 'RWA': 'Rwanda', 'SEN': 'Senegal', 'ZAF': 'South_Africa',
    'SSD': 'South_Sudan', 'SDN': 'Sudan', 'TZA': 'Tanzania', 'TGO': 'Togo',
    'UGA': 'Uganda', 'ZMB': 'Zambia', 'ZWE': 'Zimbabwe'
}

_archive = None
def get_archive(path):
    global _archive
    if path and Path(path).exists() and _archive is None:
        _archive = zipfile.ZipFile(path, 'r')
    return _archive

_parks = None
def load_parks():
    global _parks
    if _parks: return _parks
    with open(KEYSTONES_FILE) as f:
        keystones = json.load(f)
    _parks = {}
    for k in keystones:
        bounds = None
        if k.get('geometry'):
            coords = []
            geom = k['geometry']
            if geom['type'] == 'Polygon': coords = geom['coordinates'][0]
            elif geom['type'] == 'MultiPolygon':
                for poly in geom['coordinates']: coords.extend(poly[0])
            if coords:
                lons, lats = [c[0] for c in coords], [c[1] for c in coords]
                buf = BUFFER_KM / 111.0
                bounds = {'min_lat': min(lats)-buf, 'max_lat': max(lats)+buf, 'min_lon': min(lons)-buf, 'max_lon': max(lons)+buf}
        _parks[k['id']] = {'id': k['id'], 'name': k.get('name', k['id']), 'geometry': k.get('geometry'), 'bounds': bounds}
    return _parks

def find_parks_for_point(lon, lat, parks):
    """Find parks containing point (polygon check)."""
    matches = []
    for pid, p in parks.items():
        b = p.get('bounds')
        if not b: continue
        if not (b['min_lat'] <= lat <= b['max_lat'] and b['min_lon'] <= lon <= b['max_lon']): continue
        if p.get('geometry') and point_in_polygon(lon, lat, p['geometry']):
            matches.append(pid)
    return matches

def load_fires(conn, park_id, min_date, bounds, zip_path):
    """Load fires from all sources."""
    fires, seen = [], set()
    
    def add(lat, lon, date, tm='0000', br=0, frp=0):
        if date < min_date: return
        k = f"{lat:.4f}_{lon:.4f}_{date}_{tm}"
        if k in seen: return
        seen.add(k)
        fires.append({'lat': lat, 'lon': lon, 'date': date, 'time': tm, 'brightness': br, 'frp': frp})
    
    # DB
    try:
        for r in conn.execute('SELECT latitude, longitude, acq_date, acq_time, bright_ti4, frp FROM fire_detections WHERE park_id=? AND acq_date>=?', (park_id, min_date)):
            add(r[0], r[1], r[2], str(r[3] or '0000'), r[4] or 0, r[5] or 0)
    except: pass
    
    # NRT JSON
    nrt = BASE_DIR / 'data/fire_detections_2025_2026' / f'{park_id}.json'
    if nrt.exists():
        try:
            for f in json.load(open(nrt)):
                add(f['lat'], f['lng'], f['date'], f.get('time', '0000'), f.get('brightness', 0), f.get('frp', 0))
        except: pass
    
    # Buffer JSON
    for bf in glob.glob(str(BASE_DIR / f'data/fire_additional_buffer/{park_id}_*_buffer.json')):
        try:
            d = json.load(open(bf))
            fl = d.get('fires', d) if isinstance(d, dict) else d
            for f in fl:
                add(f['lat'], f.get('lon', f.get('lng')), f['date'], f.get('time', '0000'), f.get('brightness', 0), f.get('frp', 0))
        except: pass
    
    # Archive
    if zip_path:
        cc = park_id.split('_')[0]
        cn = COUNTRY_MAP.get(cc)
        if cn:
            zf = get_archive(zip_path)
            if zf:
                for name in zf.namelist():
                    if '__MACOSX' in name or not name.endswith('.csv') or cn not in name: continue
                    yr = None
                    for p in name.split('/'):
                        if p.isdigit() and len(p) == 4: yr = int(p); break
                    if yr and yr < 2020: continue
                    try:
                        with zf.open(name) as f:
                            for row in csv.DictReader(io.TextIOWrapper(f, 'utf-8')):
                                try:
                                    lat, lon = float(row.get('latitude', 0)), float(row.get('longitude', 0))
                                    dt = row.get('acq_date', '')
                                    if dt < min_date: continue
                                    if not (bounds['min_lat'] <= lat <= bounds['max_lat'] and bounds['min_lon'] <= lon <= bounds['max_lon']): continue
                                    add(lat, lon, dt, str(row.get('acq_time', '0000')), float(row.get('bright_ti4', 0) or 0), float(row.get('frp', 0) or 0))
                                except: continue
                    except: continue
    
    return sorted(fires, key=lambda x: (x['date'], x['time']))

GRID_SIZE = 0.05  # ~5.5km grid for spatial indexing

def cluster_by_day(fires):
    """Create daily clusters with centroids using grid-based spatial indexing."""
    by_date = defaultdict(list)
    for f in fires: by_date[f['date']].append(f)
    
    daily = {}
    for date, df in by_date.items():
        n = len(df)
        if n == 0: continue
        
        # Build spatial grid
        grid = defaultdict(list)
        for i, f in enumerate(df):
            gx, gy = int(f['lon'] / GRID_SIZE), int(f['lat'] / GRID_SIZE)
            grid[(gx, gy)].append(i)
        
        # Cluster using grid
        used = [False] * n
        clusters = []
        for i in range(n):
            if used[i]: continue
            cl, stack = [i], [i]
            used[i] = True
            while stack:
                ci = stack.pop()
                cf = df[ci]
                gx, gy = int(cf['lon'] / GRID_SIZE), int(cf['lat'] / GRID_SIZE)
                for dx in range(-1, 2):
                    for dy in range(-1, 2):
                        for j in grid.get((gx+dx, gy+dy), []):
                            if used[j]: continue
                            if haversine(cf['lat'], cf['lon'], df[j]['lat'], df[j]['lon']) <= CLUSTER_DIST_KM:
                                cl.append(j); used[j] = True; stack.append(j)
            pts = [df[idx] for idx in cl]
            clat, clon = sum(p['lat'] for p in pts)/len(pts), sum(p['lon'] for p in pts)/len(pts)
            spread = max(haversine(clat, clon, p['lat'], p['lon']) for p in pts) if len(pts) > 1 else 0
            clusters.append({'date': date, 'lat': clat, 'lon': clon, 'fires': len(pts), 'spread_km': spread})
        if clusters: daily[date] = clusters
    return daily

def link_trajectories(daily):
    """Link daily clusters into trajectories."""
    dates = sorted(daily.keys())
    if not dates: return []
    used, trajs = set(), []
    for si, sd in enumerate(dates):
        for c in daily[sd]:
            k = f"{sd}_{c['lat']:.4f}_{c['lon']:.4f}"
            if k in used: continue
            traj = [c]; used.add(k); cur = c
            for ni in range(si+1, len(dates)):
                nd = dates[ni]
                d1, d2 = datetime.strptime(cur['date'], '%Y-%m-%d'), datetime.strptime(nd, '%Y-%m-%d')
                if (d2-d1).days > MAX_GAP_DAYS: break
                best, bd = None, MAX_LINK_KM + 1
                for nc in daily[nd]:
                    nk = f"{nd}_{nc['lat']:.4f}_{nc['lon']:.4f}"
                    if nk in used: continue
                    d = haversine(cur['lat'], cur['lon'], nc['lat'], nc['lon'])
                    if d <= MAX_LINK_KM and d < bd: best, bd = nc, d
                if best:
                    traj.append(best); used.add(f"{best['date']}_{best['lat']:.4f}_{best['lon']:.4f}"); cur = best
            if len(traj) >= MIN_TRAJ_DAYS: trajs.append(traj)
    return trajs

def classify(traj, primary, parks):
    """Classify trajectory."""
    s, e = traj[0], traj[-1]
    fires, days = sum(c['fires'] for c in traj), len(traj)
    
    # Cross-park (check only start, mid, end)
    affected = {primary}
    for c in [s, e] + ([traj[len(traj)//2]] if len(traj) > 2 else []):
        affected.update(find_parks_for_point(c['lon'], c['lat'], parks))
    
    # Movement
    ns, ne = (s['lat']-e['lat'])*111, (e['lon']-s['lon'])*111
    dist = haversine(s['lat'], s['lon'], e['lat'], e['lon'])
    mvs = []
    for i in range(1, len(traj)):
        gap = max(1, (datetime.strptime(traj[i]['date'], '%Y-%m-%d') - datetime.strptime(traj[i-1]['date'], '%Y-%m-%d')).days)
        mvs.append(haversine(traj[i-1]['lat'], traj[i-1]['lon'], traj[i]['lat'], traj[i]['lon']) / gap)
    aspd, mspd = (sum(mvs)/len(mvs) if mvs else 0), (max(mvs) if mvs else 0)
    aspr = sum(c['spread_km'] for c in traj) / days
    
    # Direction
    if dist > 0.1:
        dlon = math.radians(e['lon'] - s['lon'])
        x = math.sin(dlon) * math.cos(math.radians(e['lat']))
        y = math.cos(math.radians(s['lat'])) * math.sin(math.radians(e['lat'])) - math.sin(math.radians(s['lat'])) * math.cos(math.radians(e['lat'])) * math.cos(dlon)
        b = (math.degrees(math.atan2(x, y)) + 360) % 360
        d = bearing_to_dir(b)
    else: b, d = 0, 'N'
    
    # Classify
    if aspr > 50 and aspd > 10: gt = 'management_fast'
    elif aspd > 15: gt = 'management_vehicle' if aspr > 30 else 'herder_fast'
    elif aspd > 5: gt = 'transhumance' if ns > 20 else 'herder_local'
    elif aspd > 2: gt = 'transhumance_slow' if days > 10 and ns > 15 else 'local_burning'
    elif fires <= 5 and days <= 2: gt = 'spot_fire'
    else: gt = 'village_persistent' if days > 7 else 'local_stationary'
    
    return {
        'group_type': gt, 'days': days, 'fires': fires, 'cross_border': len(affected) > 1,
        'affected_parks': list(affected), 'direction': d, 'bearing': round(b),
        'total_distance_km': round(dist, 1), 'avg_speed_km_day': round(aspd, 1), 'max_speed_km_day': round(mspd, 1),
        'avg_spread_km': round(aspr, 1), 'net_south_km': round(ns, 1), 'net_east_km': round(ne, 1),
        'start_date': s['date'], 'end_date': e['date'],
        'start_lat': round(s['lat'], 5), 'start_lon': round(s['lon'], 5),
        'end_lat': round(e['lat'], 5), 'end_lon': round(e['lon'], 5),
        'trajectory': [{'date': c['date'], 'lat': round(c['lat'], 5), 'lon': round(c['lon'], 5), 'fires': c['fires'], 'spread_km': round(c['spread_km'], 2)} for c in traj]
    }

def process_park(conn, pid, parks, min_date, zip_path):
    """Process one park."""
    p = parks.get(pid, {})
    b = p.get('bounds')
    if not b: return None
    
    t0 = time.time()
    fires = load_fires(conn, pid, min_date, b, zip_path)
    t_load = time.time() - t0
    if not fires: return None
    
    t0 = time.time()
    daily = cluster_by_day(fires)
    trajs = link_trajectories(daily)
    t_cluster = time.time() - t0
    if not trajs: return None
    
    t0 = time.time()
    groups, by_type = [], defaultdict(int)
    for i, tr in enumerate(trajs):
        g = classify(tr, pid, parks)
        g['group_num'] = i + 1
        groups.append(g)
        by_type[g['group_type']] += 1
    t_classify = time.time() - t0
    
    cross = sum(1 for g in groups if g['cross_border'])
    log(f"      Load:{t_load:.1f}s Cluster:{t_cluster:.1f}s Classify:{t_classify:.1f}s")
    
    return {
        'park_id': pid, 'park_name': p.get('name', pid), 'total_fires': len(fires),
        'total_groups': len(groups), 'cross_border_groups': cross,
        'date_range': {'from': min(f['date'] for f in fires), 'to': max(f['date'] for f in fires)},
        'by_type': dict(by_type), 'groups': groups
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-date', default=MIN_DATE)
    parser.add_argument('--park')
    parser.add_argument('--archive', default='/tmp/fire_archive.zip')
    args = parser.parse_args()
    
    log("=" * 70)
    log(f"STEP 1: FIRE ANALYSIS (Daily Centroids + Cross-Park)")
    log(f"  From: {args.from_date}, Buffer: {BUFFER_KM}km")
    log("=" * 70)
    
    zip_path = args.archive if Path(args.archive).exists() else None
    log(f"Archive: {zip_path or 'None'}")
    
    OUTPUT_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    parks = load_parks()
    log(f"Parks: {len(parks)}")
    
    pids = [args.park] if args.park else sorted(parks.keys())
    log(f"Processing {len(pids)} parks...")
    
    tg, tf, tc = 0, 0, 0
    for i, pid in enumerate(pids):
        r = process_park(conn, pid, parks, args.from_date, zip_path)
        if r:
            tg += r['total_groups']; tf += r['total_fires']; tc += r['cross_border_groups']
            with open(OUTPUT_DIR / f'{pid}.json', 'w') as f: json.dump(r, f)
            log(f"  [{i+1}/{len(pids)}] {pid}: {r['total_groups']} groups ({r['cross_border_groups']} cross), {r['total_fires']} fires")
        else:
            log(f"  [{i+1}/{len(pids)}] {pid}: No data")
    
    conn.close()
    if _archive: _archive.close()
    log(f"\nCOMPLETE: {tg} groups, {tf} fires, {tc} cross-border")

if __name__ == '__main__':
    main()
