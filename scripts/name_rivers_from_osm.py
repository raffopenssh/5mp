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
