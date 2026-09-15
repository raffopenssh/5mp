#!/usr/bin/env python3
"""
fire_vanguard_kf.py — the KALMAN SEED-AHEAD TRACKER ("auto-vanguard").

WHAT IT DOES (plain words)

At the start of the dry season a few people move ahead of everyone else,
laying fire along the route the herds will follow. Their fires are the only
ones on an otherwise dark landscape, so following them day to day is
possible — measured, not assumed: ahead of the season front the tracker's
links beat the day-shuffled null (skill ~0.5), inside the season they do
not (scripts/fire_vanguard/README.md, docs/agents/fire.md).

The ordinary trajectories (rebuild_fire_trajectories_v5.py) run over the
WHOLE field, so a chain that began ahead of the season is cut the moment
the season's own fires arrive around it — 43 % of vanguard chains ended
that way. This tracker fixes that with two rules borrowed from particle
physics track finding:

  1. A track may only be BORN ahead of the front (seed lead 10–60 d; the
     same window the vanguard flag uses). Nothing inside the season starts
     a track.
  2. Once born, a track is FOLLOWED by a Kalman filter (state x, y, vx, vy;
     the gate is the Mahalanobis distance to the predicted position, so it
     tightens as the track establishes its heading) into the arriving
     season, down to 10 d after the front passed. Then it is left alone:
     one more fire among the field's.

Measured on XSA 2024/25 against the day-shuffled null (van3.py SEED):
km per chain 76 vs 48 for the plain tracker, fires in >= 150 km chains
24,567 vs 9,714 (skill 0.60), links skill 0.29. Cloud coasting (letting a
track survive unseen days without cost) was measured and REJECTED: beyond
two missed days the next sighting is somebody else's fire. Gap budget is
the production 3 days; a missed day is drawn dashed, never bridged silently.

Everything downstream of the linking — SPRT split, fragment chaining, group
emission, evidence bits, season tagging — is the production code, untouched.

WHAT IT WRITES

  data/fire_vanguard_kf/{area}.json      the chains (group dicts + kf fields)
  feature_geometries                     feature_type='fire_vanguard',
                                         vanguard=1, lead_start, traj_days,
                                         properties_json (same shape as a
                                         trajectory + tracker/end_cause/
                                         heading_deg/speed_kmd/seed_lead)
  fire_vanguard_kf                       one row per area: when, which
                                         seasons, how many chains — the
                                         server swaps the vanguard layer
                                         from plain groups to these chains
                                         for every area listed here.

USAGE

  python3 scripts/fire_vanguard_kf.py --area XSA_Study_Area          # all seasons
  python3 scripts/fire_vanguard_kf.py --areas A,B --current --quiet  # live season (nightly)
  python3 scripts/fire_vanguard_kf.py --rotate 25 --quiet            # cron catch-up
"""
import argparse
import json
import math
import sqlite3
import sys
import time
import warnings
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

import fire_front as FF  # noqa: E402
import rebuild_fire_trajectories_v5 as B  # noqa: E402
from fire_source import DB_PATH, load_aoi_fires, load_park_fires_db  # noqa: E402

BASE_DIR = Path(__file__).parent.parent
OUT_DIR = BASE_DIR / "data" / "fire_vanguard_kf"

# --- the measured configuration (scripts/fire_vanguard/van3.py KF_seed_gap3)
FIELD_LEAD_MIN = -10.0     # the tracker sees detections from this lead upward
SEED_LEAD_MIN = float(FF.VANGUARD_LEAD_DAYS)   # a track is born only >= this far ahead
SEED_LEAD_MAX = float(FF.VANGUARD_LEAD_MAX)    # ...and no further (wet-season fires are not scouts)
MAX_GAP_DAYS = 3           # calendar days a track survives unseen (production budget)
SIG_V0_KMD = 5.0           # prior speed uncertainty of a new track, km/d
Q_V_KMD = 1.5              # heading/speed change per sqrt(day), km/d
R_FLOOR_KM = 1.5           # measurement noise floor (one VIIRS pixel-ish)
CHI2_GATE = 9.21           # 99 % gate for 2 d.o.f.
LOST_KM = 30.0             # position sigma beyond which the track is lost
MIN_FIELD_DETECTIONS = 30  # fewer detections ahead of the front: nothing to track
TRACKER_VERSION = "kf1"    # bump when any constant above changes
KM_LAT = 110.57


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Kalman track
# ---------------------------------------------------------------------------

class KTrack(B.Track):
    """A production Track plus the filter state: position + velocity in km
    (local equirectangular about the seed latitude) and its covariance."""
    __slots__ = ('x', 'P', 'hits', 'seed_lead')

    def __init__(self, dc):
        super().__init__(dc)
        lon, lat = dc['centroid']
        self.x = np.array([lon * _kmlon(lat), lat * KM_LAT, 0.0, 0.0])
        self.P = np.diag([R_FLOOR_KM ** 2, R_FLOOR_KM ** 2, SIG_V0_KMD ** 2, SIG_V0_KMD ** 2])
        self.hits = 1
        self.seed_lead = dc.get('lead')

    @property
    def kmlon(self):
        return _kmlon(self.points[0][1])

    def propagate(self, g):
        F = np.eye(4)
        F[0, 2] = F[1, 3] = g
        q = Q_V_KMD ** 2 * g
        Q = np.zeros((4, 4))
        Q[2, 2] = Q[3, 3] = q
        Q[0, 0] = Q[1, 1] = q * g * g / 3
        Q[0, 2] = Q[2, 0] = Q[1, 3] = Q[3, 1] = q * g / 2
        return F @ self.x, F @ self.P @ F.T + Q

    def kf_update(self, dc):
        g = max(dc['t'] - self.last_t, 1 / 24)
        xp, Pp = self.propagate(g)
        lon, lat = dc['centroid']
        z = np.array([lon * self.kmlon, lat * KM_LAT])
        R = np.eye(2) * meas_var(dc)
        H = np.zeros((2, 4))
        H[0, 0] = H[1, 1] = 1
        S = H @ Pp @ H.T + R
        K = Pp @ H.T @ np.linalg.inv(S)
        self.x = xp + K @ (z - H @ xp)
        self.P = (np.eye(4) - K @ H) @ Pp
        self.hits += 1

    def velocity(self):
        """(speed km/d, heading deg clockwise from north) of the filtered state."""
        vx, vy = float(self.x[2]), float(self.x[3])
        sp = math.hypot(vx, vy)
        hd = (math.degrees(math.atan2(vx, vy)) + 360.0) % 360.0 if sp > 0.05 else None
        return sp, hd


def _kmlon(lat):
    return 111.32 * math.cos(math.radians(lat))


def meas_var(dc):
    n = dc['n']
    if n < 3:
        return R_FLOOR_KM ** 2 * 2
    xs = np.array([[f['longitude'], f['latitude']] for f in dc['fires']])
    sp = np.std(xs, axis=0).mean() * 105
    return max(R_FLOOR_KM ** 2, sp * sp / n + 1.0)


def kf_build_tracks(day_clusters):
    """Seed-ahead Kalman tracking over clusters that carry a 'lead' (days
    ahead of the season front at their centroid, NaN = unknown)."""
    by_t = defaultdict(list)
    for dc in day_clusters:
        by_t[dc['t']].append(dc)
    active, closed = [], []
    lr_model = B.load_link_lr()
    for t in sorted(by_t):
        still = []
        for tr in active:
            gap = t - tr.last_t
            _, Pp = tr.propagate(gap)
            pos_sig = math.sqrt(max(Pp[0, 0], Pp[1, 1]))
            (closed if (gap > MAX_GAP_DAYS or pos_sig > LOST_KM) else still).append(tr)
        active = still
        clusters = by_t[t]
        costs, llrs = {}, {}
        if active and clusters:
            C = np.array([dc['centroid'] for dc in clusters])
            MV = np.array([meas_var(dc) for dc in clusters])
            for ti, tr in enumerate(active):
                gap = t - tr.last_t
                xp, Pp = tr.propagate(gap)
                lon0, lat0 = tr.points[-1][0], tr.points[-1][1]
                Z = np.c_[C[:, 0] * tr.kmlon, C[:, 1] * KM_LAT] - xp[:2]
                a = Pp[0, 0] + MV
                b = Pp[0, 1]
                d = Pp[1, 1] + MV
                det = a * d - b * b
                M2 = (d * Z[:, 0] ** 2 - 2 * b * Z[:, 0] * Z[:, 1] + a * Z[:, 1] ** 2) / det
                for ci in np.where(M2 <= CHI2_GATE)[0]:
                    dc = clusters[ci]
                    lon1, lat1 = dc['centroid']
                    step = B.haversine(lon0, lat0, lon1, lat1)
                    if tr.last_bearing is not None and tr.moved_km > 3.0 and step > B.TURN_MIN_STEP_KM:
                        if B.bearing_diff(tr.last_bearing, B.bearing(lon0, lat0, lon1, lat1)) > B.TURN_LIMIT_DEG:
                            continue
                    mp = B._min_pair_km(tr.last_dc, dc) / max(gap, B.GATE_MIN_GAP_DAYS)
                    llrs[(ti, ci)] = B.link_llr(mp, lr_model)
                    costs[(ti, ci)] = float(M2[ci]) + 0.02 * B._mass_penalty(tr.last_n, dc['n'])
        pairs = B._solve_assignment(costs, len(active), len(clusters))
        margins = B._link_margins(costs, pairs)
        used = set()
        for ti, ci in pairs:
            tr = active[ti]
            tr.kf_update(clusters[ci])
            tr.extend(clusters[ci], margins[(ti, ci)], llrs[(ti, ci)])
            used.add(ci)
        for ci, dc in enumerate(clusters):
            if ci in used:
                continue
            L = dc.get('lead')
            if L is None or not (SEED_LEAD_MIN <= L <= SEED_LEAD_MAX):
                continue          # inside the season a cluster may only continue a track
            active.append(KTrack(dc))
    closed.extend(active)
    return closed


# ---------------------------------------------------------------------------
# One season of one area
# ---------------------------------------------------------------------------

def build_groups(fires, leads, area_id, geometry, latest_day=None):
    """fires: detection dicts (fire_source schema); leads: per-detection lead
    (float, NaN unknown). Returns production group dicts with kf fields."""
    leads = np.asarray(leads, float)
    keep = np.isfinite(leads) & (leads >= FIELD_LEAD_MIN)
    field = [f for f, k in zip(fires, keep) if k]
    if len(field) < MIN_FIELD_DETECTIONS:
        return []
    dcs = B.daily_clusters(field)
    # Lead per cluster at its centroid on its day; the seed rule reads it.
    if dcs:
        fs = build_groups.frontset
        L = fs.lead_array([dc['centroid'][0] for dc in dcs], [dc['centroid'][1] for dc in dcs],
                          [dc['date'] for dc in dcs])
        for dc, l in zip(dcs, L):
            dc['lead'] = None if not np.isfinite(l) else float(l)
    tracks = kf_build_tracks(dcs)
    # Filter state by LAST cluster: after chain_tracks the tail fragment's
    # state says where the chain was going when it ended.
    by_tail = {id(tr.dcs[-1]): tr for tr in tracks}
    seed_of = {id(tr.dcs[0]): tr.seed_lead for tr in tracks}
    tracks = B.split_tracks_sprt(tracks)
    tracks = B.chain_tracks(tracks)
    park_shape = None
    try:
        from shapely.geometry import shape as _shape
        park_shape = _shape(geometry)
        if not park_shape.is_valid:
            park_shape = park_shape.buffer(0)
    except Exception:
        pass
    inside_test = B.make_inside_test(geometry, park_shape)
    groups = []
    for tr in tracks:
        g = B.track_to_group(tr, area_id, geometry, park_shape, inside_test)
        if not g:
            continue
        # The filter state of the LAST original track in this chain: heading
        # and speed at the end (chain_tracks may have joined fragments; the
        # tail is the one that says where it was going).
        src = by_tail.get(id(tr.dcs[-1]))
        sp, hd = src.velocity() if src is not None else (None, None)
        g['tracker'] = 'kf'
        g['tracker_version'] = TRACKER_VERSION
        g['seed_lead'] = seed_of.get(id(tr.dcs[0]))
        g['speed_kmd'] = round(sp, 1) if sp is not None else None
        g['heading_deg'] = round(hd) if hd is not None else None
        g['kf_hits'] = src.hits if src is not None else None
        groups.append(g)
    B.dedupe_feature_ids(groups, area_id)
    for g in groups:
        g['feature_id'] = 'kf_' + g['feature_id']
        FF.tag_group(g, build_groups.frontset)
        g['vanguard'] = True       # born ahead of the front, by construction
        g['end_cause'] = end_cause(g, latest_day)
    return groups


build_groups.frontset = None


def end_cause(g, latest_day):
    """Why the chain ends, in one word the tip can say:
    'ongoing'  — last seen within the gap budget of the newest data we hold
    'season'   — the season front had arrived where it stopped
    'lost'     — still ahead of the front, but no fire within reach for
                 MAX_GAP_DAYS (cloud, a fire too small to see, or they stopped)"""
    end = g.get('end_date') or ''
    if latest_day and end and (date.fromisoformat(latest_day) - date.fromisoformat(end[:10])).days <= MAX_GAP_DAYS:
        return 'ongoing'
    leads = g.get('leads') or []
    known = [l for l in leads if l is not None]
    if known and known[-1] < 0:
        return 'season'
    return 'lost'


def load_fires(conn, area_id, kind, s0, s1):
    if kind == 'aoi':
        return load_aoi_fires(area_id, s0.isoformat(), s1.isoformat(), conn=conn)
    return load_park_fires_db(area_id, s0.isoformat(), s1.isoformat(), conn=conn)


def area_geometry(conn, area_id, kind):
    if kind == 'aoi':
        import aoi_lib
        return aoi_lib.inject_aoi({}, area_id)['geometry']
    return B.load_parks()[area_id]['geometry']


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS fire_vanguard_kf (
        area_id TEXT PRIMARY KEY,
        computed_at TEXT NOT NULL,
        tracker_version TEXT NOT NULL,
        seasons_json TEXT NOT NULL,
        chains INTEGER NOT NULL,
        stats_json TEXT)""")


def write_groups(conn, area_id, season, s0, s1, groups):
    """Replace the area's chains of one season. Scoped DELETE; batched."""
    from load_fire_groups_to_db import day_offsets
    conn.execute("DELETE FROM feature_geometries WHERE feature_type='fire_vanguard' AND park_id=? "
                 "AND start_date >= ? AND start_date <= ?", (area_id, s0.isoformat(), s1.isoformat()))
    n = 0
    for g in groups:
        traj = g.get('trajectory') or []
        coords = [[p[0], p[1]] for p in traj]
        if len(coords) < 2:
            continue
        geojson = json.dumps({"type": "LineString", "coordinates": coords}, separators=(',', ':'))
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        props = {
            "group_type": g.get('group_type'), "fires_total": g.get('fire_count'),
            "days": g.get('days'), "distance_km": g.get('distance_km'),
            "avg_speed_km_day": g.get('speed_km_day'), "total_frp": g.get('total_frp'),
            "pct_inside": g.get('pct_inside'), "start_date": g.get('start_date'),
            "end_date": g.get('end_date'), "trajectory_type": g.get('trajectory_type'),
            "evidence_bits": g.get('evidence_bits'), "evidence_tier": g.get('evidence_tier', 'unmeasured'),
            "link_margin": g.get('link_margin'),
            "fire_season": g.get('season'), "lead_start": g.get('lead_start'),
            "lead_basis": g.get('lead_basis'), "lead_max": g.get('lead_max'), "leads": g.get('leads'),
            "ahead_km": g.get('ahead_km'), "ahead_days": g.get('ahead_days'), "vanguard": True,
            "tracker": 'kf', "tracker_version": TRACKER_VERSION, "seed_lead": g.get('seed_lead'),
            "end_cause": g.get('end_cause'), "heading_deg": g.get('heading_deg'),
            "speed_kmd": g.get('speed_kmd'), "kf_hits": g.get('kf_hits'),
            "year": g.get('year'),
        }
        conn.execute("""INSERT OR REPLACE INTO feature_geometries
            (feature_type, feature_id, park_id, geojson, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
             start_date, end_date, properties_json, dist_to_park_km, traj_days, lead_start, vanguard)
            VALUES ('fire_vanguard', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                     (g['feature_id'], area_id, geojson, min(lons), min(lats), max(lons), max(lats),
                      g.get('start_date'), g.get('end_date'), json.dumps(props),
                      g.get('dist_to_park_km'), json.dumps(day_offsets(traj, g.get('start_date')), separators=(',', ':')),
                      g.get('lead_start')))
        n += 1
        if n % 400 == 0:
            conn.commit()
            time.sleep(0.02)
    conn.commit()
    return n


def run_area(conn, area_id, current_only=False, verbose=True):
    """Track every season of the area (or only the live one). Returns
    (seasons_done, chains) or None when the area has no front (unfinished —
    fire_front.py must run first; a caller must not read that as 'no
    vanguard')."""
    t0 = time.time()
    fs = FF.FrontSet(conn, area_id)
    if not fs:
        if verbose:
            log(f"{area_id}: no season front stored — skipped (run fire_front.py first)")
        return None
    kind = FF.area_kind(conn, area_id)
    geometry = area_geometry(conn, area_id, kind)
    if kind == 'aoi':
        B.AOI_IDS.add(area_id)
    build_groups.frontset = fs
    seasons = sorted(fs.seasons)
    if current_only:
        latest = max((S['latest'] for S in fs.seasons.values() if S['latest']), default=None)
        seasons = [s for s in seasons if fs.seasons[s]['latest'] == latest][-1:] if latest else []
    ensure_tables(conn)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{area_id}.json"
    held = []
    if out_path.exists() and current_only:
        try:
            held = json.loads(out_path.read_text())
        except Exception:
            held = []
    all_groups = [g for g in held if g.get('season') not in seasons]
    stats, chains = {}, 0
    for s in seasons:
        S = fs.seasons[s]
        s0, s1 = FF.season_bounds(s, fs.start_month)
        fires = load_fires(conn, area_id, kind, s0, s1)
        if not fires:
            stats[s] = {"detections": 0, "field": 0, "chains": 0}
            continue
        leads = fs.lead_array([f['longitude'] for f in fires], [f['latitude'] for f in fires],
                              [f['acq_date'] for f in fires])
        latest_day = S['latest'].isoformat() if (not S['complete'] and S['latest']) else None
        groups = build_groups(fires, leads, area_id, geometry, latest_day)
        n = write_groups(conn, area_id, s, s0, s1, groups)
        field = int(np.sum(np.isfinite(leads) & (leads >= FIELD_LEAD_MIN)))
        stats[s] = {"detections": len(fires), "field": field, "chains": n,
                    "ongoing": sum(1 for g in groups if g.get('end_cause') == 'ongoing'),
                    "km": round(sum(g.get('distance_km') or 0 for g in groups), 1)}
        chains += n
        all_groups.extend(groups)
        if verbose:
            log(f"{area_id} {s}{'' if S['complete'] else ' (live)'}: {len(fires):,} det, "
                f"{field:,} ahead/near the front → {n} chains"
                f"{', ' + str(stats[s]['ongoing']) + ' ongoing' if stats[s]['ongoing'] else ''}")
    tmp = out_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(all_groups, default=str))
    tmp.replace(out_path)
    total = conn.execute("SELECT COUNT(*) FROM feature_geometries WHERE feature_type='fire_vanguard' AND park_id=?",
                         (area_id,)).fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO fire_vanguard_kf (area_id, computed_at, tracker_version, seasons_json, chains, stats_json) "
                 "VALUES (?,?,?,?,?,?)",
                 (area_id, datetime.now().astimezone().isoformat(timespec='seconds'), TRACKER_VERSION,
                  json.dumps(sorted(fs.seasons)), total, json.dumps(stats)))
    conn.commit()
    if verbose:
        log(f"{area_id}: {len(seasons)} season(s), {chains} chains written, {total} held, {time.time() - t0:.1f}s")
    return len(seasons), chains


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--area")
    ap.add_argument("--areas", help="comma-separated ids")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--current", action="store_true", help="only the live season")
    ap.add_argument("--rotate", type=int, metavar="N", help="the N areas with a front whose chains are oldest/missing")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    ensure_tables(conn)
    with_front = [r[0] for r in conn.execute("SELECT DISTINCT area_id FROM fire_season_front")]
    if a.all:
        ids = sorted(with_front)
    elif a.areas:
        ids = [s.strip() for s in a.areas.split(",") if s.strip()]
    elif a.area:
        ids = [a.area]
    elif a.rotate:
        have = {r[0]: (r[1] if r[2] == TRACKER_VERSION else "") for r in conn.execute(
            "SELECT area_id, computed_at, tracker_version FROM fire_vanguard_kf")}
        ids = sorted(with_front, key=lambda i: have.get(i, ""))[:a.rotate]
    else:
        ap.error("--area, --areas, --all or --rotate N")
    done, skipped, failed, chains = 0, [], [], 0
    t0 = time.time()
    for aid in ids:
        try:
            r = run_area(conn, aid, current_only=a.current, verbose=not a.quiet)
            if r is None:
                skipped.append(aid)
            else:
                done += 1
                chains += r[1]
        except Exception as ex:  # one bad area must not stop the sweep
            log(f"{aid}: FAILED {ex!r}")
            failed.append(aid)
    log(f"done: {done}/{len(ids)} areas, {chains} chains" + (f"; no front yet: {len(skipped)}" if skipped else ""))
    if a.rotate:
        from cron_notify import notify_status
        n_have = conn.execute("SELECT COUNT(*) FROM fire_vanguard_kf WHERE tracker_version=?", (TRACKER_VERSION,)).fetchone()[0]
        msg = (f"{done}/{len(ids)} areas tracked, {chains:,} chains written, {time.time() - t0:.0f}s; "
               f"Kalman vanguard held for {n_have}/{len(with_front)} areas with a front")
        if failed or (done == 0 and ids):
            notify_status("fire_vanguard_kf_failed", "Vanguard (Kalman)", msg + (f"; failed: {', '.join(failed[:8])}" if failed else ""))
        else:
            notify_status("fire_vanguard_kf_success", "Vanguard (Kalman)", msg)


if __name__ == "__main__":
    main()
