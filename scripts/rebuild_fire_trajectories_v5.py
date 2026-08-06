#!/usr/bin/env python3
"""
Fire Trajectory Builder v7 - per-overpass clustering + multi-day track matching

Replaces the v5 global spatio-temporal DBSCAN, which produced too many
splits and zigzag trajectories. Root causes fixed here:

1. v5 picked the single fire "furthest along the overall bearing" as the
   daily trajectory point -> jumped between flanks of the burn scar (zigzag).
   v6+ uses FRP-weighted centroids + light smoothing.
2. v5 clustered all fires at once with a hard 2-day temporal eps -> one
   moving front fragmented into many short groups.
   v6+ clusters each time-slice spatially (DBSCAN), then TRACKS clusters
   slice-to-slice with velocity prediction and direction-continuity gating.
3. Fragments that still split (cloud cover, satellite gaps) are CHAINED
   post-hoc when collinear and within reach (gap <= 4 days).
4. Incremental mode walks the cutoff back so groups spanning the window
   boundary are fully re-formed instead of chopped.

v7 changes:
5. SOURCE IS SQLite (`fire_source.py`). The old data/raw-fire-viirs-*/ files
   were a rolling ~6-month window, so full rebuilds used to discard years of
   history. Those files (and --source) are gone as of 2026-08.
6. Optional per-overpass slicing (--overpass), plus real overpass times in the
   trajectory's 4th element instead of the old '1200' stub, and speed computed
   from true elapsed time. Overpass slicing is OFF by default: with only
   NOAA-20 ingested the night pass is ~12x sparser than the day pass, so it
   measurably regresses tracking. See the USE_OVERPASS comment.
7. Slice-to-slice assignment is globally optimal (Hungarian) instead of
   greedy nearest-first, which could hand a cluster to the wrong track and
   cross two parallel fronts. Measured: fires_per_grp +4.2%, frag_pct -18.8%,
   zigzag_bad -19.2%, coverage +1.2%.
8. Match cost is mass-aware: a 1-fire DBSCAN noise point can no longer outbid
   a 400-fire front for an established track. Measured (with 7): zigzag_bad
   -36.2%, fires_per_grp +5.9%.
9. pct_inside honours polygon HOLES (v6 only tested ring[0], so donut-shaped
   parks counted enclave fires as inside) and uses a prepared geometry.

Output (unchanged schema): data/fire_groups_v5/{park_id}.json
"""

import json
import math
import sys
import bisect
import hashlib
import argparse
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from sklearn.cluster import DBSCAN

sys.path.insert(0, str(Path(__file__).parent))
from fire_source import (load_park_fires as _load_fires,
                         load_aoi_fires as _load_aoi_fires,
                         earliest_fire_date, date_num, parse_acq_time)

try:
    from scipy.optimize import linear_sum_assignment
    HAVE_SCIPY = True
except ImportError:  # fall back to greedy assignment
    HAVE_SCIPY = False

BASE_DIR = Path(__file__).parent.parent
KEYSTONES_FILE = BASE_DIR / "data" / "keystones_with_boundaries.json"
OUTPUT_DIR = BASE_DIR / "data" / "fire_groups_v5"
TRENDS_DIR = BASE_DIR / "data" / "fire_trends_v5"

# --- Per-slice spatial clustering ---
DAY_EPS_KM = 3.0           # DBSCAN eps within a single time slice
DAY_MIN_SAMPLES = 3        # min fires to form a slice-cluster

# --- Overpass slicing ---
# Fires whose acq_dt fall within this many hours of each other belong to the
# same overpass. A single VIIRS swath spans minutes and day/night passes are
# ~12h apart, so 4h separates passes cleanly even with 3 satellites.
OVERPASS_GAP_H = 4.0
# DEFAULT OFF - measured regression, see docs/FIRE_PIPELINE.md.
# With a single satellite (NOAA-20) the two daily passes are wildly asymmetric:
# on ZMB_Kafue the day pass averages 763 fires at mean FRP 10.6, the night pass
# 63 fires at FRP 1.7. Treating them as equal temporal samples makes every
# other trajectory vertex a sparse, low-FRP, spatially-biased estimate, which
# injects an oscillation that trips the turn gate: mean_days -21%,
# dup_pairs +23%, coverage -2.4%.
# Revisit once VIIRS_SNPP + NOAA-21 are ingested (~6 passes/day): each slice
# then has enough detections to stand on its own. Enable with --overpass.
USE_OVERPASS = False

# --- Slice-to-slice track matching ---
MAX_GAP_DAYS = 3           # max days a track survives without detections
BASE_LINK_KM = 5.0         # base matching radius (slice-cluster -> predicted pos)
SPREAD_KM_PER_DAY = 8.0    # extra radius per day of gap (fast grass fires)
TURN_LIMIT_DEG = 100       # reject match if established track turns harder than this
TURN_MIN_STEP_KM = 3.0     # ...unless the step is small (stationary flicker)
VELOCITY_ALPHA = 0.5       # EMA weight for track velocity update
# Match gate has a floor of one day's spread: a day and a night pass sit ~12h
# apart, and the FRP centroid genuinely jumps a few km between them (night
# passes detect more small fires). Scaling the gate down with the real sub-day
# gap fragments tracks badly - measured frag_pct +79%, mean_days -22%.
GATE_MIN_GAP_DAYS = 1.0
# Mass-similarity penalty (km-equivalent) for size mismatch between a track's
# last cluster and a candidate. Stops noise singletons hijacking large fronts.
# 3.0 chosen by ablation (scripts/eval_fire_trajectories.py): best zigzag_bad
# (-36%) and the only value that improved geometry on all 6 golden parks.
MASS_PENALTY_KM = 3.0

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

# --- Persistent hotspot mask ---
# Cell size must match scripts/build_persistent_hotspots.py (one VIIRS pixel).
HOTSPOT_CELL_DEG = 0.0034


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
    """Correct point-in-polygon INCLUDING holes.

    v6 only tested ring[0] of each polygon, so any park mapped as a donut
    (excluded enclave, inner village) counted enclave fires as inside.
    """
    coords = geometry.get('coordinates', [])
    if geometry['type'] == 'MultiPolygon':
        return any(_point_in_poly_rings(lon, lat, poly) for poly in coords)
    elif geometry['type'] == 'Polygon':
        return _point_in_poly_rings(lon, lat, coords)
    return False


def _point_in_poly_rings(lon, lat, rings):
    """rings[0] = outer, rings[1:] = holes."""
    if not rings or not _point_in_ring(lon, lat, rings[0]):
        return False
    for hole in rings[1:]:
        if _point_in_ring(lon, lat, hole):
            return False
    return True


def make_inside_test(geometry, park_shape=None):
    """Return f(lons, lats) -> bool ndarray.

    Prefers shapely's prepared geometry, which handles holes natively and is
    far faster than the pure-python ray cast that ran once per fire per group.
    Falls back to point_in_polygon if shapely is unavailable.
    """
    try:
        from shapely.geometry import shape as _shape, Point
        from shapely.prepared import prep
        geom = park_shape
        if geom is None:
            geom = _shape(geometry)
            if not geom.is_valid:
                geom = geom.buffer(0)
        pgeom = prep(geom)

        def _fast(lons, lats):
            return np.fromiter(
                (pgeom.contains(Point(x, y)) for x, y in zip(lons, lats)),
                dtype=bool, count=len(lons))
        return _fast
    except Exception:
        def _slow(lons, lats):
            return np.array([point_in_polygon(x, y, geometry)
                             for x, y in zip(lons, lats)], dtype=bool)
        return _slow


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


def load_park_fires(park_id, min_date, conn=None):
    """Load fires from the canonical source (fire_detections in SQLite).

    For an AOI (--aoi) the selection is by polygon via the aoi_fires membership
    cache instead of protected_area_id: an AOI is not a park and must never own
    a detection (docs/PLAN_AOI_OVERLAY.md §3). Still one fire source.
    """
    if park_id in AOI_IDS:
        return _load_aoi_fires(park_id, min_date, conn=conn)
    return _load_fires(park_id, min_date, conn=conn)


# AOI ids present in this run; see --aoi in main().
AOI_IDS = set()


# ---------------------------------------------------------------------------
# Stage 1: per-overpass spatial clustering
# ---------------------------------------------------------------------------

def overpass_slices(fires):
    """Split fires into satellite overpasses by gap-clustering acq_dt.

    Only active with USE_OVERPASS (--overpass), which is OFF and expected to
    stay off. The idea was that v6's calendar-date bucketing merges the day and
    night pass (~12h apart) into one FRP-weighted centroid, averaging away the
    movement we want to measure.

    Measured 2026-08-06 with all three VIIRS sensors ingested: it does not pay
    off, and cannot. SNPP/NOAA-20/NOAA-21 share one sun-synchronous ~13:30
    orbit plane, so their overpasses coincide (all modal at 10-12 UTC) instead
    of spreading through the day - 1.71 slices/day, not the ~6 that was hoped
    for. The night pass stays ~17x sparser at ~6x lower FRP, so alternating
    slices still injects an oscillation: fires_per_grp -10.6%, mean_days -22.4%,
    dup_pairs +16.0%, coverage -3.3%. See docs/FIRE_PIPELINE.md.
    """
    if not fires:
        return []
    if not USE_OVERPASS:
        by_date = defaultdict(list)
        for f in fires:
            by_date[f['acq_date']].append(f)
        return [by_date[d] for d in sorted(by_date)]
    ordered = sorted(fires, key=lambda f: f['acq_dt'])
    gap = OVERPASS_GAP_H / 24.0
    slices = [[ordered[0]]]
    for f in ordered[1:]:
        if f['acq_dt'] - slices[-1][-1]['acq_dt'] > gap:
            slices.append([f])
        else:
            slices[-1].append(f)
    return slices


def _absorb(masked, seeds):
    """Masked detections close enough (DAY_EPS_KM) to a seed to count as part
    of the same fire. Used on the tiny-slice path where there is no DBSCAN."""
    if not masked:
        return []
    out = []
    for f in masked:
        if any(haversine(f['longitude'], f['latitude'],
                         g['longitude'], g['latitude']) <= DAY_EPS_KM
               for g in seeds):
            out.append(f)
    return out


def _is_masked(f, mask):
    """True if this detection sits in a persistent-hotspot cell."""
    return (int(f['longitude'] / HOTSPOT_CELL_DEG),
            int(f['latitude'] / HOTSPOT_CELL_DEG)) in mask


def daily_clusters(fires, persistent_mask=None):
    """Cluster fires spatially within each overpass.
    Returns list of dicts: {t, date, fires, centroid (FRP-weighted), n}.

    All clusters from one slice share the slice's `t` so that build_tracks
    considers them together in a single assignment step. (Using each cluster's
    own mean time would put every cluster in its own time slice and defeat the
    joint matching entirely.)

    `persistent_mask` is a set of (cell_x, cell_y) from fire_persistent_cells
    (gas flares, lava lakes, kilns - see build_persistent_hotspots.py). Those
    detections may NOT seed a cluster: they are excluded from DBSCAN and from
    the singleton-noise fallback, otherwise each one spawns an immortal
    trajectory group that never ends. They are still merged into a real cluster
    whose centroid is within DAY_EPS_KM, so a genuine front sweeping over a
    flare site keeps its full fire count.
    """
    mask = persistent_mask or set()
    out = []
    for sl in overpass_slices(fires):
        # Slice-level timestamp + representative date, shared by all clusters.
        # In calendar mode t must be the integer day number, otherwise the mean
        # detection time makes consecutive days differ by != 1.0 and tracks get
        # retired early against MAX_GAP_DAYS (this silently shortened tracks).
        if USE_OVERPASS:
            slice_t = sum(f['acq_dt'] for f in sl) / len(sl)
            slice_date = min(sl, key=lambda f: abs(f['acq_dt'] - slice_t))['acq_date']
            slice_hhmm = None  # derived from slice_t
        else:
            slice_date = sl[0]['acq_date']
            slice_t = float(date_num(slice_date))
            # Keep the real median detection time for display/animation even
            # though matching uses the integer day number.
            fracs = sorted(f['acq_dt'] - math.floor(f['acq_dt']) for f in sl)
            slice_hhmm = _hhmm(fracs[len(fracs) // 2])
        # Persistent hotspots never seed; they only join.
        if mask:
            seeds = [f for f in sl if not _is_masked(f, mask)]
            masked = [f for f in sl if _is_masked(f, mask)]
        else:
            seeds, masked = sl, []
        if not seeds:
            continue
        if len(seeds) < DAY_MIN_SAMPLES:
            out.append(_make_day_cluster(
                seeds + _absorb(masked, seeds), slice_t, slice_date, slice_hhmm))
            continue
        coords = np.array([[f['longitude'], f['latitude']] for f in seeds])
        lat0 = math.radians(float(np.mean(coords[:, 1])))
        # local km projection (fixes v5's 1deg==111km assumption for lon)
        coords_km = np.column_stack([coords[:, 0] * 111.32 * math.cos(lat0),
                                     coords[:, 1] * 110.57])
        labels = DBSCAN(eps=DAY_EPS_KM, min_samples=DAY_MIN_SAMPLES).fit_predict(coords_km)
        clusters = []
        for label in sorted(set(labels)):
            if label < 0:
                continue
            clusters.append([seeds[i] for i, l in enumerate(labels) if l == label])
        # noise points become singleton clusters (can still join tracks, but
        # the mass penalty stops them stealing established fronts)
        for f, l in zip(seeds, labels):
            if l < 0:
                clusters.append([f])
        # Masked detections are attached to the nearest cluster within
        # DAY_EPS_KM, or dropped for this slice.
        for f in masked:
            best, best_d = None, DAY_EPS_KM
            for cf in clusters:
                d = min(haversine(f['longitude'], f['latitude'],
                                  g['longitude'], g['latitude']) for g in cf)
                if d < best_d:
                    best, best_d = cf, d
            if best is not None:
                best.append(f)
        for cf in clusters:
            out.append(_make_day_cluster(cf, slice_t, slice_date, slice_hhmm))
    out.sort(key=lambda c: c['t'])
    return out


def _make_day_cluster(cf, slice_t, slice_date, slice_hhmm=None):
    wsum = sum(max(f.get('frp') or 0.0, 0.1) for f in cf)
    cx = sum(f['longitude'] * max(f.get('frp') or 0.0, 0.1) for f in cf) / wsum
    cy = sum(f['latitude'] * max(f.get('frp') or 0.0, 0.1) for f in cf) / wsum
    # slice_date is the date of the overpass midpoint, so a 23:5x pass is not
    # attributed to the following day.
    return {'t': slice_t, 'date': slice_date, 'fires': cf,
            'centroid': (cx, cy), 'n': len(cf), 'hhmm': slice_hhmm}


# ---------------------------------------------------------------------------
# Stage 2: day-to-day track matching
# ---------------------------------------------------------------------------

class Track:
    __slots__ = ('points', 'fires', 'vel', 'last_t', 'last_date', 'last_bearing',
                 'moved_km', 'last_n')

    def __init__(self, dc):
        self.points = [(dc['centroid'][0], dc['centroid'][1], dc['date'], dc['t'],
                        dc.get('hhmm'))]
        self.fires = list(dc['fires'])
        self.vel = (0.0, 0.0)          # deg/day (lon, lat)
        self.last_t = dc['t']
        self.last_date = dc['date']
        self.last_bearing = None
        self.moved_km = 0.0
        self.last_n = dc['n']

    def predict(self, t):
        gap = t - self.last_t
        lon, lat = self.points[-1][0], self.points[-1][1]
        return (lon + self.vel[0] * gap, lat + self.vel[1] * gap)

    def extend(self, dc):
        lon0, lat0 = self.points[-1][0], self.points[-1][1]
        lon1, lat1 = dc['centroid']
        # Real elapsed time between overpasses, floored so a same-pass merge
        # cannot explode the velocity estimate.
        gap = max(dc['t'] - self.last_t, 1.0 / 24.0)
        step_km = haversine(lon0, lat0, lon1, lat1)
        if step_km > 0.5:
            self.last_bearing = bearing(lon0, lat0, lon1, lat1)
        self.moved_km += step_km
        vlon = (lon1 - lon0) / gap
        vlat = (lat1 - lat0) / gap
        self.vel = (VELOCITY_ALPHA * vlon + (1 - VELOCITY_ALPHA) * self.vel[0],
                    VELOCITY_ALPHA * vlat + (1 - VELOCITY_ALPHA) * self.vel[1])
        self.points.append((lon1, lat1, dc['date'], dc['t'], dc.get('hhmm')))
        self.fires.extend(dc['fires'])
        self.last_t = dc['t']
        self.last_date = dc['date']
        self.last_n = dc['n']


_date_cache = {}

def _parse_date(s):
    d = _date_cache.get(s)
    if d is None:
        d = datetime.strptime(s, '%Y-%m-%d')
        _date_cache[s] = d
    return d


def _days_between(d0, d1):
    return (_parse_date(d1) - _parse_date(d0)).days


def _hhmm(t):
    """Fractional-day time -> 'HHMM' for the trajectory point's 4th element."""
    frac = t - math.floor(t)
    mins = int(round(frac * 1440)) % 1440
    return f"{mins // 60:02d}{mins % 60:02d}"


def _mass_penalty(n_track, n_cand):
    """km-equivalent cost for cluster-size mismatch.

    A 1-fire noise point and a 400-fire front used to compete on pure distance,
    letting noise hijack an established track (and truncate the real front).
    Log-ratio keeps this scale-free: 2x mismatch is cheap, 100x is expensive.
    """
    a, b = max(n_track, 1), max(n_cand, 1)
    return MASS_PENALTY_KM * abs(math.log10(a / b))


def build_tracks(day_clusters):
    """Track slice-clusters into multi-day tracks.

    Assignment within each time slice is globally optimal (Hungarian) rather
    than greedy nearest-first: with two parallel fronts a few km apart, greedy
    could hand the nearer cluster to the wrong track and cross the two.
    """
    by_t = defaultdict(list)
    for dc in day_clusters:
        by_t[dc['t']].append(dc)

    active = []
    closed = []

    for t in sorted(by_t.keys()):
        # retire stale tracks
        still_active = []
        for tr in active:
            if t - tr.last_t > MAX_GAP_DAYS:
                closed.append(tr)
            else:
                still_active.append(tr)
        active = still_active

        clusters = by_t[t]
        # Build sparse cost list, applying hard gates (radius / turn).
        costs = {}
        for ti, tr in enumerate(active):
            gap = t - tr.last_t
            pred = tr.predict(t)
            gate = BASE_LINK_KM + max(gap, GATE_MIN_GAP_DAYS) * SPREAD_KM_PER_DAY
            lon0, lat0 = tr.points[-1][0], tr.points[-1][1]
            for ci, dc in enumerate(clusters):
                lon1, lat1 = dc['centroid']
                dist = haversine(pred[0], pred[1], lon1, lat1)
                if dist > gate:
                    continue
                step_km = haversine(lon0, lat0, lon1, lat1)
                if (tr.last_bearing is not None and tr.moved_km > 3.0
                        and step_km > TURN_MIN_STEP_KM):
                    turn = bearing_diff(tr.last_bearing, bearing(lon0, lat0, lon1, lat1))
                    if turn > TURN_LIMIT_DEG:
                        continue
                costs[(ti, ci)] = dist + _mass_penalty(tr.last_n, dc['n'])

        pairs = _solve_assignment(costs, len(active), len(clusters))

        used_clusters = set()
        for ti, ci in pairs:
            active[ti].extend(clusters[ci])
            used_clusters.add(ci)

        for ci, dc in enumerate(clusters):
            if ci not in used_clusters:
                active.append(Track(dc))

    closed.extend(active)
    return closed


def _solve_assignment(costs, n_tracks, n_clusters):
    """Min-cost 1:1 matching over the gated (track, cluster) pairs."""
    if not costs:
        return []
    if not HAVE_SCIPY:
        # Greedy fallback (previous v6 behaviour).
        out, ut, uc = [], set(), set()
        for (ti, ci), c in sorted(costs.items(), key=lambda kv: kv[1]):
            if ti in ut or ci in uc:
                continue
            ut.add(ti)
            uc.add(ci)
            out.append((ti, ci))
        return out

    # Restrict the matrix to rows/cols that actually have a candidate; a dense
    # matrix over all tracks x clusters would be huge in peak season.
    rows = sorted({ti for ti, _ in costs})
    cols = sorted({ci for _, ci in costs})
    ri = {t: i for i, t in enumerate(rows)}
    cj = {c: j for j, c in enumerate(cols)}
    BIG = 1e6
    m = np.full((len(rows), len(cols)), BIG, dtype=float)
    for (ti, ci), c in costs.items():
        m[ri[ti], cj[ci]] = c
    r_idx, c_idx = linear_sum_assignment(m)
    return [(rows[r], cols[c]) for r, c in zip(r_idx, c_idx)
            if m[r, c] < BIG]


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

    # Sort by start time so we only ever look forward.
    order_by_start = sorted(range(n), key=lambda i: tracks[i].points[0][3])
    starts = [tracks[i].points[0][3] for i in order_by_start]

    links = []
    for a in range(n):
        ta = tracks[a]
        t_end = ta.last_t
        # candidates: tracks starting in (t_end, t_end + CHAIN_MAX_GAP_DAYS]
        lo = _bisect_right(starts, t_end)
        for k in range(lo, len(starts)):
            gap = starts[k] - t_end
            if gap > CHAIN_MAX_GAP_DAYS:
                break
            b = order_by_start[k]
            if a == b:
                continue
            tb = tracks[b]
            dist = haversine(ta.points[-1][0], ta.points[-1][1],
                             tb.points[0][0], tb.points[0][1])
            if dist > CHAIN_BASE_KM + max(gap, GATE_MIN_GAP_DAYS) * SPREAD_KM_PER_DAY:
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
            links.append((dist + gap * 2.0 + _mass_penalty(ta.last_n, tb.last_n), a, b))

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
    for i in order_by_start:
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
            t.last_t = nxt.last_t
            t.last_date = nxt.last_date
            consumed.add(j)
        merged.append(t)
    return merged


def _bisect_right(arr, x):
    return bisect.bisect_right(arr, x)


# ---------------------------------------------------------------------------
# Stage 4: trajectory smoothing + quality
# ---------------------------------------------------------------------------

def smooth_trajectory(points):
    """points: [(lon,lat,date,t,hhmm)]. Remove single-point spikes, then 3-pt moving
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
                              (pts[i-1][1] + pts[i+1][1]) / 2,
                              pts[i][2], pts[i][3], pts[i][4])
                    changed = True
            if not changed:
                break
    if len(pts) >= 3:
        sm = [pts[0]]
        for i in range(1, len(pts) - 1):
            sm.append(((pts[i-1][0] + pts[i][0] + pts[i+1][0]) / 3,
                       (pts[i-1][1] + pts[i][1] + pts[i+1][1]) / 3,
                       pts[i][2], pts[i][3], pts[i][4]))
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


def track_to_group(track, park_id, park_geometry, park_shape=None, inside_test=None):
    fires = track.fires
    if len(fires) < MIN_FIRES:
        return None

    sorted_fires = sorted(fires, key=lambda f: f['acq_dt'])
    start_date = sorted_fires[0]['acq_date']
    end_date = sorted_fires[-1]['acq_date']
    days = _days_between(start_date, end_date) + 1
    # Real elapsed duration, floored at one day: speed stays in km/day and
    # comparable to the thresholds classify_group() was tuned against, while
    # still using true elapsed time for multi-day groups.
    span_days = max(sorted_fires[-1]['acq_dt'] - sorted_fires[0]['acq_dt'], 1.0)

    lons = [f['longitude'] for f in fires]
    lats = [f['latitude'] for f in fires]
    centroid = [sum(lons)/len(lons), sum(lats)/len(lats)]

    pts = sorted(track.points, key=lambda p: p[3])
    if len(pts) >= MIN_DAYS_FOR_TRAJECTORY:
        pts = smooth_trajectory(pts)
        # 4th element is the real detection time (HHMM), not the old '1200' stub.
        trajectory = [[round(p[0], 5), round(p[1], 5), p[2], p[4] or _hhmm(p[3])]
                      for p in pts]
    else:
        p0 = pts[0] if pts else None
        trajectory = [[round(centroid[0], 5), round(centroid[1], 5), start_date,
                       (p0[4] or _hhmm(p0[3])) if p0 else '1200']]

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

    if inside_test is not None:
        inside = int(np.count_nonzero(inside_test(lons, lats)))
    else:
        inside = sum(1 for f in fires
                     if point_in_polygon(f['longitude'], f['latitude'], park_geometry))
    pct_inside = 100 * inside / len(fires)
    total_frp = sum(f.get('frp', 0) or 0 for f in fires)
    speed = distance_km / span_days
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


def dedupe_feature_ids(groups, park_id):
    """Ensure feature_id is unique within a park.

    The id hashes park + start_date + first trajectory point, which is NOT
    unique: a burn that reignites at the same spot on the same calendar date
    (and, in incremental mode, a rebuilt group landing next to an older one
    that ended before the cutoff) yields an identical hash. Verified: 722
    duplicate feature_ids across 181,711 groups in data/fire_groups_v5/ (worst
    offender GHA_Mole). Duplicates mean duplicate map features, duplicate
    notifications and ambiguous ?notif_fire= share links.

    Why not simply add end_date to the hash for everyone: that renames every
    group in the archive and orphans every persisted friendly name in
    fire_group_names. So the primary hash is unchanged and only true
    collisions get a discriminated hash (end_date + fire_count), touching
    ~0.4% of groups. Deterministic: the group with the earliest
    (start_date, end_date, -fire_count) keeps the original id, so reruns
    reproduce the same assignment.
    """
    seen = {}
    renamed = 0
    for g in sorted(groups, key=lambda g: (g.get('start_date', ''),
                                           g.get('end_date', ''),
                                           -g.get('fire_count', 0))):
        fid = g['feature_id']
        if fid not in seen:
            seen[fid] = g
            continue
        start_date = g.get('start_date', '')
        year = g.get('year') or (int(start_date[:4]) if start_date[:4].isdigit() else 0)
        h = None
        for salt in range(1, 100):
            extra = f"{g.get('end_date', '')}_{g.get('fire_count', 0)}_{salt}"
            h = hashlib.md5(f"{fid}_{extra}".encode()).hexdigest()[:8]
            new_fid = f"{park_id}_{year}_grp_{h}"
            if new_fid not in seen:
                break
        else:
            raise RuntimeError(f"could not disambiguate {fid} in {park_id}")
        g['feature_id'] = new_fid
        g['group_id'] = f"{park_id}_{start_date}_{h}"
        seen[new_fid] = g
        renamed += 1

    # Fail loudly rather than shipping duplicates downstream.
    ids = [g['feature_id'] for g in groups]
    if len(set(ids)) != len(ids):
        raise RuntimeError(f"{park_id}: feature_id still not unique after dedupe")
    return renamed


def process_park_fires(fires, park_id, park_geometry, persistent_mask=None):
    dcs = daily_clusters(fires, persistent_mask)
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
    inside_test = make_inside_test(park_geometry, park_shape)
    groups = []
    for t in tracks:
        g = track_to_group(t, park_id, park_geometry, park_shape, inside_test)
        if g:
            groups.append(g)
    return groups


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--park', help='Single park')
    parser.add_argument('--parks', help='Comma-separated park list. Preferred over '
                                        'repeated --park calls: one process reuses '
                                        'the keystone load, the DB connection and '
                                        'the sklearn/scipy import.')
    parser.add_argument('--aoi', help='Build for an AOI overlay instead of a park: '
                                      'geometry comes from the aois table and fires '
                                      'from the aoi_fires polygon membership. The AOI '
                                      'is NEVER written to keystones_with_boundaries.json '
                                      '(docs/PLAN_AOI_OVERLAY.md §3).')
    parser.add_argument('--incremental', action='store_true',
                        help='Incremental mode: only process recent fires')
    parser.add_argument('--days', type=int, default=14, help='Days window for incremental mode')
    parser.add_argument('--output-dir', help='Override output directory (for A/B testing)')
    # Ablation switches, for scripts/eval_fire_trajectories.py A/B runs.
    parser.add_argument('--overpass', action='store_true',
                        help='Slice by satellite overpass instead of calendar day. '
                             'Currently a measured regression with one satellite; '
                             'revisit with SNPP+NOAA-21 ingested.')
    parser.add_argument('--no-overpass', action='store_true',
                        help='(default) Bucket by calendar day')
    parser.add_argument('--no-mass-penalty', action='store_true',
                        help='Disable mass-similarity term in match cost')
    parser.add_argument('--no-hungarian', action='store_true',
                        help='Use greedy nearest-first assignment (v6 behaviour)')
    parser.add_argument('--no-hotspot-mask', action='store_true',
                        help='Let persistent hotspots (flares/lava/kilns) seed '
                             'clusters again - reproduces pre-v7.1 output')
    parser.add_argument('--set', action='append', metavar='NAME=VALUE', default=[],
                        help='Override a tuning constant, e.g. --set MASS_PENALTY_KM=3')
    args = parser.parse_args()

    global USE_OVERPASS, MASS_PENALTY_KM, HAVE_SCIPY
    if args.overpass:
        USE_OVERPASS = True
    if args.no_overpass:
        USE_OVERPASS = False
    if args.no_mass_penalty:
        MASS_PENALTY_KM = 0.0
    if args.no_hungarian:
        HAVE_SCIPY = False
    use_mask = not args.no_hotspot_mask
    for kv in args.set:
        name, _, val = kv.partition('=')
        name = name.strip().upper()
        if name not in globals():
            parser.error(f'unknown constant {name}')
        globals()[name] = type(globals()[name])(val)
        log(f'  override {name}={globals()[name]}')

    out_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR

    log("Fire Trajectory Builder v7 (per-overpass clustering + track matching)")
    log("Source: fire_detections (SQLite)")
    if not HAVE_SCIPY:
        log("  scipy unavailable -> greedy assignment fallback")

    min_date = MIN_DATE
    if args.incremental:
        min_date = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
        log(f"INCREMENTAL MODE: processing fires since {min_date}")

    log("Loading parks...")
    parks = load_parks()
    log(f"Loaded {len(parks)} parks")
    if args.aoi:
        sys.path.insert(0, str(Path(__file__).parent))
        import aoi_lib
        aoi = aoi_lib.inject_aoi(parks, args.aoi)
        AOI_IDS.add(args.aoi)
        log(f"AOI overlay: {args.aoi} ({aoi['name']}) — fires by polygon membership")
    log(f"Params: day_eps={DAY_EPS_KM}km, overpass_gap={OVERPASS_GAP_H}h, "
        f"link={BASE_LINK_KM}km+{SPREAD_KM_PER_DAY}km/day, "
        f"max_gap={MAX_GAP_DAYS}d, chain_gap<={CHAIN_MAX_GAP_DAYS}d, min_fires={MIN_FIRES}")

    out_dir.mkdir(exist_ok=True, parents=True)
    TRENDS_DIR.mkdir(exist_ok=True, parents=True)

    # One shared read-only connection for all parks in this run.
    import sqlite3
    from fire_source import DB_PATH
    shared_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    # Persistent-hotspot mask: cells detected in >=30 distinct months are
    # flares / lava lakes / kilns, not wildfires. They must not seed tracks or
    # each one becomes an immortal group. Built by
    # scripts/build_persistent_hotspots.py; absent table = mask inactive.
    hotspot_masks = {}
    if use_mask:
        import sqlite3 as _sq
        from fire_source import DB_PATH as _DBP
        try:
            _mc = _sq.connect(f"file:{_DBP}?mode=ro", uri=True)
            for _pid, _cx, _cy in _mc.execute(
                    "SELECT park_id, cell_x, cell_y FROM fire_persistent_cells"):
                hotspot_masks.setdefault(_pid, set()).add((_cx, _cy))
            _mc.close()
            log(f"Persistent hotspot mask: {sum(len(v) for v in hotspot_masks.values())} "
                f"cells in {len(hotspot_masks)} parks")
            # cell_x/cell_y are GLOBAL 0.0034deg indices, only *keyed* by park,
            # so an AOI can safely take the union: cells outside its polygon
            # simply never match a detection it holds.
            if args.aoi:
                hotspot_masks[args.aoi] = set().union(*hotspot_masks.values()) \
                    if hotspot_masks else set()
        except Exception as e:
            log(f"Persistent hotspot mask unavailable ({e}); seeding unmasked")
    else:
        log("Persistent hotspot mask DISABLED (--no-hotspot-mask)")

    if args.aoi:
        park_ids = [args.aoi]
    elif args.parks:
        park_ids = [p.strip() for p in args.parks.split(',') if p.strip()]
    elif args.park:
        park_ids = [args.park]
    else:
        park_ids = sorted(parks.keys())
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
            raw_min = earliest_fire_date(park_id, conn=shared_conn)
            if raw_min and effective_min_date < raw_min:
                effective_min_date = raw_min

        fires = load_park_fires(park_id, effective_min_date, conn=shared_conn)
        if not fires and not existing_groups:
            continue

        new_groups = process_park_fires(fires, park_id, parks[park_id]['geometry'],
                                        hotspot_masks.get(park_id))

        if args.incremental:
            old_groups = [g for g in existing_groups
                          if g.get('end_date', '') < effective_min_date]
            groups = old_groups + new_groups
            log(f"  Incremental ({park_id}): cutoff {effective_min_date}, "
                f"kept {len(old_groups)} old, rebuilt {len(new_groups)}")
        else:
            groups = new_groups

        groups.sort(key=lambda g: g['start_date'], reverse=True)

        renamed = dedupe_feature_ids(groups, park_id)
        if renamed:
            log(f"  {park_id}: disambiguated {renamed} duplicate feature_id(s)")

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

    # A partial run (subset of parks) must not clobber the global trend file
    # with only its own parks' data.
    partial = bool(args.park or args.parks or args.aoi)
    if partial:
        log("Partial run: skipping global trend/summary rewrite")
        if shared_conn:
            shared_conn.close()
        log(f"\nDone! {total_fires_processed:,} fires -> {total_groups} groups")
        return

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
        'algorithm': 'v7_overpass_track_matching',
        'source': 'db',
        'params': {
            'day_eps_km': DAY_EPS_KM,
            'day_min_samples': DAY_MIN_SAMPLES,
            'overpass_gap_h': OVERPASS_GAP_H,
            'max_gap_days': MAX_GAP_DAYS,
            'base_link_km': BASE_LINK_KM,
            'spread_km_per_day': SPREAD_KM_PER_DAY,
            'turn_limit_deg': TURN_LIMIT_DEG,
            'mass_penalty_km': MASS_PENALTY_KM,
            'assignment': 'hungarian' if HAVE_SCIPY else 'greedy',
            'chain_max_gap_days': CHAIN_MAX_GAP_DAYS,
            'chain_turn_limit_deg': CHAIN_TURN_LIMIT_DEG,
            'min_fires': MIN_FIRES,
            'hotspot_mask': bool(hotspot_masks),
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
