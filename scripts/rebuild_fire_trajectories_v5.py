#!/usr/bin/env python3
"""
Fire Trajectory Builder v6 - per-day clustering + multi-day track matching

Replaces the v5 global spatio-temporal DBSCAN, which produced too many
splits and zigzag trajectories. Root causes fixed here:

1. v5 picked the single fire "furthest along the overall bearing" as the
   daily trajectory point -> jumped between flanks of the burn scar (zigzag).
   v6 uses FRP-weighted daily centroids + light smoothing.
2. v5 clustered all fires at once with a hard 2-day temporal eps -> one
   moving front fragmented into many short groups.
   v6 clusters each day spatially (DBSCAN), then TRACKS clusters day-to-day
   with velocity prediction and direction-continuity gating.
3. Fragments that still split (cloud cover, satellite gaps) are CHAINED
   post-hoc when collinear and within reach (gap <= 4 days).
4. Incremental mode walks the cutoff back so groups spanning the window
   boundary are fully re-formed instead of chopped.

Output (unchanged schema): data/fire_groups_v5/{park_id}.json
"""

import json
import math
import hashlib
import argparse
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from sklearn.cluster import DBSCAN

BASE_DIR = Path(__file__).parent.parent
KEYSTONES_FILE = BASE_DIR / "data" / "keystones_with_boundaries.json"
FIRE_DIR = BASE_DIR / "data" / "raw-fire-viirs-20200101-20260222"
OUTPUT_DIR = BASE_DIR / "data" / "fire_groups_v5"
TRENDS_DIR = BASE_DIR / "data" / "fire_trends_v5"

# --- Per-day spatial clustering ---
DAY_EPS_KM = 3.0           # DBSCAN eps within a single day
DAY_MIN_SAMPLES = 3        # min fires to form a day-cluster

# --- Day-to-day track matching ---
MAX_GAP_DAYS = 3           # max days a track survives without detections
BASE_LINK_KM = 5.0         # base matching radius (day-cluster -> predicted pos)
SPREAD_KM_PER_DAY = 8.0    # extra radius per day of gap (fast grass fires)
TURN_LIMIT_DEG = 100       # reject match if established track turns harder than this
TURN_MIN_STEP_KM = 3.0     # ...unless the step is small (stationary flicker)
VELOCITY_ALPHA = 0.5       # EMA weight for track velocity update

# --- Post-hoc chaining of track fragments ---
CHAIN_MAX_GAP_DAYS = 4     # A ends, B starts within N days
CHAIN_BASE_KM = 5.0        # chaining radius: CHAIN_BASE_KM + gap*SPREAD_KM_PER_DAY
CHAIN_TURN_LIMIT_DEG = 75  # bearing continuity between fragments

# --- Output filtering / quality ---
MIN_FIRES = 10             # min fires per emitted group
MIN_DAYS_FOR_TRAJECTORY = 2
ZIGZAG_THRESHOLD = 90      # degrees, for quality metric
MAX_ZIGZAG_RATIO = 0.3
SPIKE_TURN_DEG = 120       # single-point spike removal

MIN_DATE = '2020-01-01'


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def haversine(lon1, lat1, lon2, lat2):
    R = 6371
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def bearing(lon1, lat1, lon2, lat2):
    dlon = math.radians(lon2 - lon1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def bearing_diff(b1, b2):
    diff = abs(b1 - b2) % 360
    return min(diff, 360 - diff)


def point_in_polygon(lon, lat, geometry):
    coords = geometry.get('coordinates', [])
    if geometry['type'] == 'MultiPolygon':
        for poly in coords:
            if _point_in_ring(lon, lat, poly[0]):
                return True
        return False
    elif geometry['type'] == 'Polygon':
        return _point_in_ring(lon, lat, coords[0])
    return False


def _point_in_ring(lon, lat, ring):
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def load_parks():
    with open(KEYSTONES_FILE) as f:
        data = json.load(f)
    parks = {}
    for p in data:
        park_id = p.get('id')
        if not park_id or 'geometry' not in p:
            continue
        parks[park_id] = {'id': park_id, 'name': p.get('name', park_id),
                          'country': p.get('country', ''), 'geometry': p['geometry']}
    return parks


def load_park_fires(park_id, min_date):
    fire_file = FIRE_DIR / f"{park_id}.json"
    if not fire_file.exists():
        return []
    with open(fire_file) as f:
        data = json.load(f)
    fires = data.get('fires', data) if isinstance(data, dict) else data
    valid_fires = []
    for f in fires:
        if f.get('acq_date', '') < min_date:
            continue
        lon = f.get('longitude', 0)
        lat = f.get('latitude', 0)
        if lon == 0.0 or lat == 0.0:  # data errors
            continue
        valid_fires.append(f)
    return valid_fires


# ---------------------------------------------------------------------------
# Stage 1: per-day spatial clustering
# ---------------------------------------------------------------------------

def daily_clusters(fires):
    """Cluster fires within each day spatially.
    Returns list of dicts: {date, fires, centroid (FRP-weighted), n}.
    """
    by_date = defaultdict(list)
    for f in fires:
        by_date[f['acq_date']].append(f)

    out = []
    for date in sorted(by_date.keys()):
        day_fires = by_date[date]
        if len(day_fires) < DAY_MIN_SAMPLES:
            out.append(_make_day_cluster(date, day_fires))
            continue
        coords = np.array([[f['longitude'], f['latitude']] for f in day_fires])
        lat0 = math.radians(float(np.mean(coords[:, 1])))
        # local km projection (fixes v5's 1deg==111km assumption for lon)
        coords_km = np.column_stack([coords[:, 0] * 111.32 * math.cos(lat0),
                                     coords[:, 1] * 110.57])
        labels = DBSCAN(eps=DAY_EPS_KM, min_samples=DAY_MIN_SAMPLES).fit_predict(coords_km)
        # collect clusters
        for label in sorted(set(labels)):
            if label < 0:
                continue
            cf = [day_fires[i] for i, l in enumerate(labels) if l == label]
            out.append(_make_day_cluster(date, cf))
        # noise points become singleton clusters (can still join tracks)
        for f, l in zip(day_fires, labels):
            if l < 0:
                out.append(_make_day_cluster(date, [f]))
    return out


def _make_day_cluster(date, cf):
    wsum = sum(max(f.get('frp') or 0.0, 0.1) for f in cf)
    cx = sum(f['longitude'] * max(f.get('frp') or 0.0, 0.1) for f in cf) / wsum
    cy = sum(f['latitude'] * max(f.get('frp') or 0.0, 0.1) for f in cf) / wsum
    return {'date': date, 'fires': cf, 'centroid': (cx, cy), 'n': len(cf)}


# ---------------------------------------------------------------------------
# Stage 2: day-to-day track matching
# ---------------------------------------------------------------------------

class Track:
    __slots__ = ('points', 'fires', 'vel', 'last_date', 'last_bearing', 'moved_km')

    def __init__(self, dc):
        self.points = [(dc['centroid'][0], dc['centroid'][1], dc['date'])]
        self.fires = list(dc['fires'])
        self.vel = (0.0, 0.0)          # deg/day (lon, lat)
        self.last_date = dc['date']
        self.last_bearing = None
        self.moved_km = 0.0

    def predict(self, date):
        gap = _days_between(self.last_date, date)
        lon, lat = self.points[-1][0], self.points[-1][1]
        return (lon + self.vel[0] * gap, lat + self.vel[1] * gap)

    def extend(self, dc):
        lon0, lat0, d0 = self.points[-1]
        lon1, lat1 = dc['centroid']
        gap = max(_days_between(d0, dc['date']), 1)
        step_km = haversine(lon0, lat0, lon1, lat1)
        if step_km > 0.5:
            self.last_bearing = bearing(lon0, lat0, lon1, lat1)
        self.moved_km += step_km
        vlon = (lon1 - lon0) / gap
        vlat = (lat1 - lat0) / gap
        self.vel = (VELOCITY_ALPHA * vlon + (1 - VELOCITY_ALPHA) * self.vel[0],
                    VELOCITY_ALPHA * vlat + (1 - VELOCITY_ALPHA) * self.vel[1])
        self.points.append((lon1, lat1, dc['date']))
        self.fires.extend(dc['fires'])
        self.last_date = dc['date']


_date_cache = {}

def _parse_date(s):
    d = _date_cache.get(s)
    if d is None:
        d = datetime.strptime(s, '%Y-%m-%d')
        _date_cache[s] = d
    return d


def _days_between(d0, d1):
    return (_parse_date(d1) - _parse_date(d0)).days


def build_tracks(day_clusters):
    """Greedy nearest-match tracking of day-clusters into multi-day tracks."""
    by_date = defaultdict(list)
    for dc in day_clusters:
        by_date[dc['date']].append(dc)

    active = []
    closed = []

    for date in sorted(by_date.keys()):
        # retire stale tracks
        still_active = []
        for t in active:
            if _days_between(t.last_date, date) > MAX_GAP_DAYS:
                closed.append(t)
            else:
                still_active.append(t)
        active = still_active

        clusters = by_date[date]
        candidates = []
        for ti, t in enumerate(active):
            gap = _days_between(t.last_date, date)
            pred = t.predict(date)
            gate = BASE_LINK_KM + gap * SPREAD_KM_PER_DAY
            for ci, dc in enumerate(clusters):
                lon1, lat1 = dc['centroid']
                dist = haversine(pred[0], pred[1], lon1, lat1)
                if dist > gate:
                    continue
                # direction continuity for established, actually-moving tracks
                lon0, lat0 = t.points[-1][0], t.points[-1][1]
                step_km = haversine(lon0, lat0, lon1, lat1)
                if (t.last_bearing is not None and t.moved_km > 3.0
                        and step_km > TURN_MIN_STEP_KM):
                    turn = bearing_diff(t.last_bearing, bearing(lon0, lat0, lon1, lat1))
                    if turn > TURN_LIMIT_DEG:
                        continue
                candidates.append((dist, ti, ci))

        candidates.sort(key=lambda c: c[0])
        used_tracks, used_clusters = set(), set()
        for dist, ti, ci in candidates:
            if ti in used_tracks or ci in used_clusters:
                continue
            active[ti].extend(clusters[ci])
            used_tracks.add(ti)
            used_clusters.add(ci)

        for ci, dc in enumerate(clusters):
            if ci not in used_clusters:
                active.append(Track(dc))

    closed.extend(active)
    return closed


# ---------------------------------------------------------------------------
# Stage 3: chain collinear track fragments
# ---------------------------------------------------------------------------

def _track_exit_bearing(t):
    pts = t.points
    if len(pts) < 2:
        return None
    i = max(0, len(pts) - 3)
    if haversine(pts[i][0], pts[i][1], pts[-1][0], pts[-1][1]) < 1.0:
        return None
    return bearing(pts[i][0], pts[i][1], pts[-1][0], pts[-1][1])


def _track_entry_bearing(t):
    pts = t.points
    if len(pts) < 2:
        return None
    i = min(len(pts) - 1, 2)
    if haversine(pts[0][0], pts[0][1], pts[i][0], pts[i][1]) < 1.0:
        return None
    return bearing(pts[0][0], pts[0][1], pts[i][0], pts[i][1])


def chain_tracks(tracks):
    """Merge fragments: A ends, B starts nearby a few days later, collinear."""
    n = len(tracks)
    if n < 2:
        return tracks

    # bucket track starts by date for fast lookup
    starts_by_date = defaultdict(list)
    for i, t in enumerate(tracks):
        starts_by_date[t.points[0][2]].append(i)

    links = []
    for a in range(n):
        ta = tracks[a]
        end_dt = _parse_date(ta.last_date)
        for gap in range(1, CHAIN_MAX_GAP_DAYS + 1):
            d = (end_dt + timedelta(days=gap)).strftime('%Y-%m-%d')
            for b in starts_by_date.get(d, []):
                if a == b:
                    continue
                tb = tracks[b]
                dist = haversine(ta.points[-1][0], ta.points[-1][1],
                                 tb.points[0][0], tb.points[0][1])
                if dist > CHAIN_BASE_KM + gap * SPREAD_KM_PER_DAY:
                    continue
                jump_b = None
                if dist > 1.0:
                    jump_b = bearing(ta.points[-1][0], ta.points[-1][1],
                                     tb.points[0][0], tb.points[0][1])
                exit_b = _track_exit_bearing(ta)
                entry_b = _track_entry_bearing(tb)
                ok = True
                for b1, b2 in ((exit_b, jump_b), (jump_b, entry_b), (exit_b, entry_b)):
                    if b1 is not None and b2 is not None and bearing_diff(b1, b2) > CHAIN_TURN_LIMIT_DEG:
                        ok = False
                        break
                if not ok:
                    continue
                links.append((dist + gap * 2.0, a, b))

    links.sort(key=lambda l: l[0])
    next_of, prev_of = {}, {}
    for cost, a, b in links:
        if a in next_of or b in prev_of:
            continue
        # avoid cycles: walk back from a; if we reach b, skip
        head = a
        seen = {a}
        cyclic = False
        while head in prev_of:
            head = prev_of[head]
            if head == b or head in seen:
                cyclic = head == b
                break
            seen.add(head)
        if cyclic:
            continue
        next_of[a] = b
        prev_of[b] = a

    merged = []
    consumed = set()
    order = sorted(range(n), key=lambda i: tracks[i].points[0][2])
    for i in order:
        if i in prev_of or i in consumed:
            continue
        t = tracks[i]
        consumed.add(i)
        j = i
        while j in next_of:
            j = next_of[j]
            nxt = tracks[j]
            t.points.extend(nxt.points)
            t.fires.extend(nxt.fires)
            t.last_date = nxt.last_date
            consumed.add(j)
        merged.append(t)
    return merged


# ---------------------------------------------------------------------------
# Stage 4: trajectory smoothing + quality
# ---------------------------------------------------------------------------

def smooth_trajectory(points):
    """points: [(lon,lat,date)]. Remove single-point spikes, then 3-pt moving
    average on interior points. Endpoints preserved."""
    pts = list(points)
    if len(pts) >= 3:
        for _ in range(3):  # up to 3 spike-removal passes
            changed = False
            for i in range(1, len(pts) - 1):
                b_in = bearing(pts[i-1][0], pts[i-1][1], pts[i][0], pts[i][1])
                b_out = bearing(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1])
                d_in = haversine(pts[i-1][0], pts[i-1][1], pts[i][0], pts[i][1])
                d_out = haversine(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1])
                d_skip = haversine(pts[i-1][0], pts[i-1][1], pts[i+1][0], pts[i+1][1])
                if (d_in > 1.0 and d_out > 1.0 and bearing_diff(b_in, b_out) > SPIKE_TURN_DEG
                        and d_skip < 0.7 * (d_in + d_out)):
                    pts[i] = ((pts[i-1][0] + pts[i+1][0]) / 2,
                              (pts[i-1][1] + pts[i+1][1]) / 2, pts[i][2])
                    changed = True
            if not changed:
                break
    if len(pts) >= 3:
        sm = [pts[0]]
        for i in range(1, len(pts) - 1):
            sm.append(((pts[i-1][0] + pts[i][0] + pts[i+1][0]) / 3,
                       (pts[i-1][1] + pts[i][1] + pts[i+1][1]) / 3, pts[i][2]))
        sm.append(pts[-1])
        pts = sm
    return pts


def zigzag_metrics(traj):
    if len(traj) < 3:
        return 0.0, 0.0
    bearings = []
    for i in range(1, len(traj)):
        if haversine(traj[i-1][0], traj[i-1][1], traj[i][0], traj[i][1]) < 0.3:
            continue
        bearings.append(bearing(traj[i-1][0], traj[i-1][1], traj[i][0], traj[i][1]))
    if len(bearings) < 2:
        return 0.0, 0.0
    changes = [bearing_diff(bearings[i], bearings[i-1]) for i in range(1, len(bearings))]
    zigzags = sum(1 for c in changes if c > ZIGZAG_THRESHOLD)
    return zigzags / len(changes), sum(changes) / len(changes)


# ---------------------------------------------------------------------------
# Group assembly (same output schema as v5)
# ---------------------------------------------------------------------------

def classify_group(days, distance_km, pct_inside, speed, fire_count):
    if fire_count < 20 and days <= 2:
        return 'spot_fire'
    elif pct_inside > 80 and speed < 2 and days <= 3:
        return 'management_controlled'
    elif days >= 5 and distance_km > 20 and speed > 2:
        return 'transhumance'
    elif days >= 3 and distance_km > 10:
        return 'spreading_fire'
    elif pct_inside < 20:
        return 'external_fire'
    else:
        return 'local_fire'


def _group_dist_to_park_km(trajectory, pct_inside, park_shape):
    """Min distance (km) from trajectory points to park boundary; 0 if any inside."""
    if pct_inside > 0 or park_shape is None:
        return 0.0
    try:
        from shapely.geometry import Point
        best = None
        for p in trajectory:
            d_deg = park_shape.distance(Point(p[0], p[1]))
            lat_scale = max(math.cos(math.radians(p[1])), 0.3)
            d_km = d_deg * 111.0 * ((1 + lat_scale) / 2)
            if best is None or d_km < best:
                best = d_km
        return round(best, 1) if best is not None else 0.0
    except Exception:
        return 0.0


def track_to_group(track, park_id, park_geometry, park_shape=None):
    fires = track.fires
    if len(fires) < MIN_FIRES:
        return None

    sorted_fires = sorted(fires, key=lambda f: f['acq_date'])
    start_date = sorted_fires[0]['acq_date']
    end_date = sorted_fires[-1]['acq_date']
    days = _days_between(start_date, end_date) + 1

    lons = [f['longitude'] for f in fires]
    lats = [f['latitude'] for f in fires]
    centroid = [sum(lons)/len(lons), sum(lats)/len(lats)]

    pts = sorted(track.points, key=lambda p: p[2])
    if len(pts) >= MIN_DAYS_FOR_TRAJECTORY:
        pts = smooth_trajectory(pts)
        trajectory = [[round(p[0], 5), round(p[1], 5), p[2], '1200'] for p in pts]
    else:
        trajectory = [[round(centroid[0], 5), round(centroid[1], 5), start_date, '1200']]

    zigzag_ratio, _avg = zigzag_metrics(trajectory)
    if len(trajectory) < 2:
        trajectory_type = 'cluster'
    elif zigzag_ratio <= MAX_ZIGZAG_RATIO:
        trajectory_type = 'clean'
    else:
        trajectory_type = 'cleaned'

    distance_km = sum(haversine(trajectory[i][0], trajectory[i][1],
                                trajectory[i+1][0], trajectory[i+1][1])
                      for i in range(len(trajectory)-1)) if len(trajectory) >= 2 else 0

    if len(trajectory) >= 2:
        overall_bearing = bearing(trajectory[0][0], trajectory[0][1],
                                  trajectory[-1][0], trajectory[-1][1])
        dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
        direction = dirs[int((overall_bearing + 22.5) / 45) % 8]
    else:
        direction = 'N'

    inside = sum(1 for f in fires
                 if point_in_polygon(f['longitude'], f['latitude'], park_geometry))
    pct_inside = 100 * inside / len(fires)
    total_frp = sum(f.get('frp', 0) or 0 for f in fires)
    speed = distance_km / max(days, 1)
    group_type = classify_group(days, distance_km, pct_inside, speed, len(fires))

    first_point = trajectory[0] if trajectory else centroid
    hash_input = f"{park_id}_{start_date}_{first_point[0]:.4f}_{first_point[1]:.4f}"
    group_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
    group_id = f"{park_id}_{start_date}_{group_hash}"
    year = int(start_date[:4])
    feature_id = f"{park_id}_{year}_grp_{group_hash}"

    return {
        'group_id': group_id,
        'feature_id': feature_id,
        'fire_count': len(fires),
        'start_date': start_date,
        'end_date': end_date,
        'days': days,
        'year': year,
        'centroid': centroid,
        'trajectory': trajectory,
        'distance_km': round(distance_km, 2),
        'speed_km_day': round(speed, 2),
        'direction': direction,
        'group_type': group_type,
        'pct_inside': round(pct_inside, 1),
        'dist_to_park_km': _group_dist_to_park_km(trajectory, pct_inside, park_shape),
        'total_frp': round(total_frp, 1),
        'primary_park': park_id,
        'affected_parks': [park_id],
        'cross_border': False,
        'first_point': first_point[:2],
        'trajectory_type': trajectory_type,
        'zigzag_ratio': round(zigzag_ratio, 2),
    }


def process_park_fires(fires, park_id, park_geometry):
    dcs = daily_clusters(fires)
    tracks = build_tracks(dcs)
    tracks = chain_tracks(tracks)
    park_shape = None
    try:
        from shapely.geometry import shape as _shape
        park_shape = _shape(park_geometry)
        if not park_shape.is_valid:
            park_shape = park_shape.buffer(0)
    except Exception:
        pass
    groups = []
    for t in tracks:
        g = track_to_group(t, park_id, park_geometry, park_shape)
        if g:
            groups.append(g)
    return groups


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--park', help='Single park')
    parser.add_argument('--incremental', action='store_true',
                        help='Incremental mode: only process recent fires')
    parser.add_argument('--days', type=int, default=14, help='Days window for incremental mode')
    parser.add_argument('--output-dir', help='Override output directory (for A/B testing)')
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR

    log("Fire Trajectory Builder v6 (per-day clustering + track matching)")

    min_date = MIN_DATE
    if args.incremental:
        min_date = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
        log(f"INCREMENTAL MODE: processing fires since {min_date}")

    log("Loading parks...")
    parks = load_parks()
    log(f"Loaded {len(parks)} parks")
    log(f"Params: day_eps={DAY_EPS_KM}km, link={BASE_LINK_KM}km+{SPREAD_KM_PER_DAY}km/day, "
        f"max_gap={MAX_GAP_DAYS}d, chain_gap<={CHAIN_MAX_GAP_DAYS}d, min_fires={MIN_FIRES}")

    out_dir.mkdir(exist_ok=True, parents=True)
    TRENDS_DIR.mkdir(exist_ok=True, parents=True)

    park_ids = [args.park] if args.park else sorted(parks.keys())
    total_groups = 0
    total_fires_processed = 0
    stats = {'clean': 0, 'cleaned': 0, 'cluster': 0}

    daily_counts = defaultdict(lambda: defaultdict(lambda: {'groups': 0, 'fires': 0}))
    monthly_counts = defaultdict(lambda: defaultdict(lambda: {'groups': 0, 'fires': 0}))

    for i, park_id in enumerate(park_ids):
        if park_id not in parks:
            continue

        effective_min_date = min_date

        # Incremental: walk cutoff back so groups spanning the boundary are
        # re-formed in full instead of being chopped at the window edge.
        existing_groups = []
        if args.incremental:
            existing_file = out_dir / f"{park_id}.json"
            if existing_file.exists():
                with open(existing_file) as f:
                    existing_groups = json.load(f)
            spanning = [g for g in existing_groups
                        if g.get('end_date', '') >= effective_min_date
                        and g.get('start_date', '') < effective_min_date]
            if spanning:
                effective_min_date = min(g['start_date'] for g in spanning)
            # Never walk back before the raw data actually starts, or we'd
            # drop old groups that cannot be rebuilt from available fires.
            all_raw = load_park_fires(park_id, '0000-00-00')
            if all_raw:
                raw_min = min(f['acq_date'] for f in all_raw)
                if effective_min_date < raw_min:
                    effective_min_date = raw_min

        fires = load_park_fires(park_id, effective_min_date)
        if not fires and not existing_groups:
            continue

        new_groups = process_park_fires(fires, park_id, parks[park_id]['geometry'])

        if args.incremental:
            old_groups = [g for g in existing_groups
                          if g.get('end_date', '') < effective_min_date]
            groups = old_groups + new_groups
            log(f"  Incremental ({park_id}): cutoff {effective_min_date}, "
                f"kept {len(old_groups)} old, rebuilt {len(new_groups)}")
        else:
            groups = new_groups

        groups.sort(key=lambda g: g['start_date'], reverse=True)

        park_stats = {'clean': 0, 'cleaned': 0, 'cluster': 0}
        for g in groups:
            ttype = g.get('trajectory_type', 'cluster')
            stats[ttype] = stats.get(ttype, 0) + 1
            park_stats[ttype] = park_stats.get(ttype, 0) + 1
            daily_counts[park_id][g['start_date']]['groups'] += 1
            daily_counts[park_id][g['start_date']]['fires'] += g['fire_count']
            monthly_counts[park_id][g['start_date'][:7]]['groups'] += 1
            monthly_counts[park_id][g['start_date'][:7]]['fires'] += g['fire_count']

        multi_day = len([g for g in groups if g['days'] >= 2])
        avg_fires = sum(g['fire_count'] for g in new_groups) / len(new_groups) if new_groups else 0

        with open(out_dir / f"{park_id}.json", 'w') as f:
            json.dump(groups, f)

        total_groups += len(groups)
        total_fires_processed += len(fires)

        log(f"[{i+1}/{len(park_ids)}] {park_id}: {len(fires):,} fires -> {len(groups)} groups "
            f"(avg {avg_fires:.0f}/grp, {multi_day} multi-day, "
            f"{park_stats['clean']} clean, {park_stats['cleaned']} zigzaggy)")

    log("Writing trend stats...")
    trends = {'daily': {k: dict(v) for k, v in daily_counts.items()},
              'monthly': {k: dict(v) for k, v in monthly_counts.items()}}
    with open(TRENDS_DIR / "park_fire_trends.json", 'w') as f:
        json.dump(trends, f)

    summary = {
        'total_groups': total_groups,
        'total_fires': total_fires_processed,
        'total_parks': len([p for p in park_ids if p in parks]),
        'date_range': {'start': min_date, 'end': datetime.now().strftime('%Y-%m-%d')},
        'algorithm': 'v6_track_matching',
        'params': {
            'day_eps_km': DAY_EPS_KM,
            'day_min_samples': DAY_MIN_SAMPLES,
            'max_gap_days': MAX_GAP_DAYS,
            'base_link_km': BASE_LINK_KM,
            'spread_km_per_day': SPREAD_KM_PER_DAY,
            'turn_limit_deg': TURN_LIMIT_DEG,
            'chain_max_gap_days': CHAIN_MAX_GAP_DAYS,
            'chain_turn_limit_deg': CHAIN_TURN_LIMIT_DEG,
            'min_fires': MIN_FIRES,
            'min_days_trajectory': MIN_DAYS_FOR_TRAJECTORY,
            'zigzag_threshold': ZIGZAG_THRESHOLD,
            'max_zigzag_ratio': MAX_ZIGZAG_RATIO,
        },
        'trajectory_types': dict(stats)
    }
    with open(TRENDS_DIR / "fire_trends_summary.json", 'w') as f:
        json.dump(summary, f, indent=2)

    log(f"\nDone! {total_fires_processed:,} fires -> {total_groups} groups")
    if total_groups:
        log(f"Average: {total_fires_processed/total_groups:.0f} fires/group")
    log(f"Trajectory types: {stats['clean']} clean, {stats['cleaned']} zigzaggy, {stats['cluster']} clusters")


if __name__ == '__main__':
    main()
