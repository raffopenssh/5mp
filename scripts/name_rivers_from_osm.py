#!/usr/bin/env python3
"""
Name unnamed HydroRIVERS segments (park_rivers_hydro) from OSM waterway
name points stored in osm_places (place_type river/stream).

Why: OSM waterway names were downloaded as *points* into osm_places, so the
frontend rendered river names ("Chinko") as purple place markers. The place
layer now excludes waterways; instead we push those names onto the actual
river LINES so MapLibre draws them as line labels (river pin layer already
has a symbol layer with text-field=name, minzoom 10).

Matching: for each named OSM waterway point, find hydro segments with any
vertex within MATCH_KM. A segment takes the name of the closest point.
Only fills previously-empty names by default (never overwrites HydroRIVERS'
own names unless --overwrite-osm re-applies names set by this script).

Usage:
  python3 scripts/name_rivers_from_osm.py --park CAF_Chinko
  python3 scripts/name_rivers_from_osm.py --all
Called per-park by scripts/daily_park_refresh.py.
"""

import argparse
import json
import math
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'db.sqlite3'
MATCH_KM = 1.5  # OSM name point must be this close to a segment vertex


def dist_km(lat1, lon1, lat2, lon2):
    dlat = (lat2 - lat1) * 111.0
    dlon = (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


def good_name(name):
    # Skip junk OSM names like "Possible stream in forest???"
    if '?' in name or len(name) > 40:
        return False
    low = name.lower()
    return not any(w in low for w in ('possible', 'unknown', 'unnamed', 'stream in', 'river in'))


def name_park(conn, park_id, verbose=True):
    pts = conn.execute("""
        SELECT name, place_type, lat, lon FROM osm_places
        WHERE park_id = ? AND place_type IN ('river','stream') AND name != ''
    """, (park_id,)).fetchall()
    pts = [p for p in pts if good_name(p[0])]
    if not pts:
        return 0

    segs = conn.execute("""
        SELECT id, geojson FROM park_rivers_hydro
        WHERE park_id = ? AND (name IS NULL OR name = '') AND geojson IS NOT NULL
    """, (park_id,)).fetchall()
    if not segs:
        return 0

    # Bucket name points into a coarse grid for fast lookup
    cell = MATCH_KM / 111.0
    grid = {}
    for name, ptype, lat, lon in pts:
        grid.setdefault((int(lat / cell), int(lon / cell)), []).append((name, lat, lon))

    updates = []
    for seg_id, gj in segs:
        try:
            g = json.loads(gj)
            coords = g['coordinates']
            if g.get('type') == 'MultiLineString':
                coords = [c for line in coords for c in line]
        except Exception:
            continue
        best = (MATCH_KM, None)
        for c in coords:
            lon, lat = c[0], c[1]
            ci, cj = int(lat / cell), int(lon / cell)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for name, plat, plon in grid.get((ci + di, cj + dj), ()):
                        d = dist_km(lat, lon, plat, plon)
                        if d < best[0]:
                            best = (d, name)
        if best[1]:
            updates.append((best[1], seg_id))

    if updates:
        conn.executemany("UPDATE park_rivers_hydro SET name = ? WHERE id = ?", updates)
        conn.commit()

    propagated = propagate_names(conn, park_id)
    if verbose:
        print(f"{park_id}: seeded {len(updates)} segments from {len(pts)} OSM "
              f"waterway points, propagated {propagated} more along connectivity")
    return len(updates) + propagated


LINE_MATCH_M = 500.0   # HydroRIVERS is traced on a 15" (~450 m) grid
LINE_STEP_M = 200.0    # both lines are compared at this spacing
LINE_MIN_FRAC = 0.5    # share of a reach that must lie along ONE named river


def _densify(coords, step_m=LINE_STEP_M):
    """Points along a lon/lat line no further apart than step_m. A straight
    reach is two vertices 5 km apart; comparing vertices alone misses it."""
    out = []
    for a, b in zip(coords, coords[1:]):
        d = dist_km(a[1], a[0], b[1], b[0]) * 1000.0
        n = max(1, int(math.ceil(d / step_m)))
        for i in range(n):
            t = i / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    if coords:
        out.append((coords[-1][0], coords[-1][1]))
    return out


def match_reach_names(reaches, lines):
    """{reach_id: name} for the reaches that run along a named OSM waterway.

    reaches: [(id, coords)], lines: [(name, coords)], coords lon/lat.
    A reach takes a name only when at least LINE_MIN_FRAC of it lies within
    LINE_MATCH_M of that ONE river -- so a tributary is not named after the
    river it joins (only its last few hundred metres are close), which is
    what a single name point with a 1.5 km radius did.
    """
    cell = LINE_MATCH_M / 111000.0
    names, grid = [], {}
    idx = {}
    for name, coords in lines:
        if not good_name(name):
            continue
        k = idx.setdefault(name, len(names))
        if k == len(names):
            names.append(name)
        for lon, lat in _densify(coords):
            grid.setdefault((int(math.floor(lat / cell)), int(math.floor(lon / cell))), []).append((lat, lon, k))
    out = {}
    if not grid:
        return out
    for rid, coords in reaches:
        pts = _densify(coords)
        if len(pts) < 2:
            continue
        votes = {}
        for lon, lat in pts:
            ci, cj = int(math.floor(lat / cell)), int(math.floor(lon / cell))
            best, bk = LINE_MATCH_M / 1000.0, None
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for plat, plon, k in grid.get((ci + di, cj + dj), ()):
                        d = dist_km(lat, lon, plat, plon)
                        if d <= best:
                            best, bk = d, k
            if bk is not None:
                votes[bk] = votes.get(bk, 0) + 1
        if votes:
            k, v = max(votes.items(), key=lambda kv: kv[1])
            if v >= LINE_MIN_FRAC * len(pts):
                out[rid] = names[k]
    return out


def name_park_from_lines(conn, park_id, lines):
    """Name a park's HydroRIVERS reaches from full OSM waterway lines.

    Called by scripts/osm_pbf.py while the country PBF is on disk: the lines
    are used and dropped, nothing but the name is stored. Only positive
    hyriv_id rows (HydroRIVERS, which carries no names of its own); OSM rows
    (negative ids) arrive named. A line match overwrites an older name -- that
    one came from a point. Unmatched reaches keep what they had, then gaps
    are filled along connectivity.
    """
    rows = conn.execute("""
        SELECT id, COALESCE(name,''), geojson FROM park_rivers_hydro
        WHERE park_id = ? AND hyriv_id > 0 AND geojson IS NOT NULL
    """, (park_id,)).fetchall()
    before = sum(1 for r in rows if r[1])
    reaches, cur = [], {}
    for rid, name, gj in rows:
        try:
            g = json.loads(gj)
        except Exception:
            continue
        coords = g.get('coordinates') or []
        if g.get('type') == 'MultiLineString':
            coords = [c for line in coords for c in line]
        reaches.append((rid, coords))
        cur[rid] = name
    matched = match_reach_names(reaches, lines)
    updates = [(n, rid) for rid, n in matched.items() if cur.get(rid) != n]
    if updates:
        conn.executemany("UPDATE park_rivers_hydro SET name = ? WHERE id = ?", updates)
        conn.commit()
    if rows:
        propagate_names(conn, park_id)
    after = conn.execute("""SELECT COUNT(*) FROM park_rivers_hydro
        WHERE park_id = ? AND hyriv_id > 0 AND name IS NOT NULL AND name != ''""",
                         (park_id,)).fetchone()[0]
    return {'reaches': len(rows), 'before': before, 'after': after,
            'matched': len(matched), 'changed': len(updates)}


def propagate_names(conn, park_id):
    """Flood-fill names along touching segments of the same stream order.

    HydroRIVERS splits one river into many short segments; OSM gives us a
    name for only a few. Segments that share an endpoint (within ~150m) and
    have the same stream_order are almost certainly the same river.
    """
    rows = conn.execute("""
        SELECT id, COALESCE(name,''), stream_order, geojson
        FROM park_rivers_hydro
        WHERE park_id = ? AND geojson IS NOT NULL
    """, (park_id,)).fetchall()

    TOL = 0.0015  # ~150m endpoint snap
    endpoints = {}  # (order, qlat, qlon) -> [seg ids]
    seg = {}
    for sid, name, order, gj in rows:
        try:
            g = json.loads(gj)
            coords = g['coordinates']
            if g.get('type') == 'MultiLineString':
                ends = [coords[0][0], coords[-1][-1]]
            else:
                ends = [coords[0], coords[-1]]
        except Exception:
            continue
        seg[sid] = {'name': name, 'order': order, 'ends': ends}
        for lon, lat in [(e[0], e[1]) for e in ends]:
            key = (order, round(lat / TOL), round(lon / TOL))
            endpoints.setdefault(key, []).append(sid)

    # BFS from named segments
    from collections import deque
    queue = deque(sid for sid, s in seg.items() if s['name'])
    updates = []
    while queue:
        sid = queue.popleft()
        s = seg[sid]
        for e in s['ends']:
            lon, lat = e[0], e[1]
            base = (round(lat / TOL), round(lon / TOL))
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for nid in endpoints.get((s['order'], base[0] + di, base[1] + dj), ()):
                        n = seg[nid]
                        if not n['name']:
                            n['name'] = s['name']
                            updates.append((s['name'], nid))
                            queue.append(nid)

    if updates:
        conn.executemany("UPDATE park_rivers_hydro SET name = ? WHERE id = ?", updates)
        conn.commit()
    return len(updates)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--park')
    ap.add_argument('--all', action='store_true')
    args = ap.parse_args()
    if not args.park and not args.all:
        ap.error('need --park or --all')

    conn = sqlite3.connect(DB_PATH)
    if args.park:
        parks = [args.park]
    else:
        parks = [r[0] for r in conn.execute(
            "SELECT DISTINCT park_id FROM park_rivers_hydro ORDER BY park_id")]
    total = 0
    for p in parks:
        total += name_park(conn, p)
    print(f"total segments named: {total}")
    conn.close()


if __name__ == '__main__':
    main()
