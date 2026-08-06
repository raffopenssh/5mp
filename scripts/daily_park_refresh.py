#!/usr/bin/env python3
"""
Daily per-park refresh: wire the rotation scans (GFW alerts 04:30, turbidity
06:00 — each scans ONE park/day and opportunistically enriches roads/rivers/
osm_places) to the park's fire + deforestation alerts/narratives.

Cron: 30 7 * * *  (after both scans)

Per freshly-scanned park:
 1. GFW integrated alerts -> deforestation events for years >= 2024
    (Hansen pipeline ends 2023, so no double counting). Deterministic
    feature_ids (deforest_gfw_{park}_...) — idempotent delete+reinsert of
    ONLY gfw-sourced rows; Hansen rows/IDs are never touched.
 2. Reclassify the park's deforestation events in PYTHON, reusing
    EventRebuilder from scripts/rebuild_events_enhanced.py (same
    classification + narrative code = identical text style as the canonical
    2026-02-15 run). Rows are UPDATEd in place (id, polygon_ids, lat/lon,
    year, area preserved) so polygon mapping is unchanged; new roads/rivers/
    places flow into linear-pattern detection and location text.
 3. Reload fire trajectories with fresh context:
    load_fire_groups_to_db.py --park X --force (re-enriches
    nearest_place/river/road in properties_json).
 4. POST /api/refresh-park (localhost, RequireAdminOrLocal): Go force-
    reclassifies settlements (Go = canonical style for settlements) and
    recomputes fire_narrative_cache.
 5. Re-export the park's data/deforestation_events/{park}.json +
    data/settlement_events/{park}.json so JSON mirrors DB.

Usage:
  python3 scripts/daily_park_refresh.py --rotate          # cron: parks scanned since last refresh
  python3 scripts/daily_park_refresh.py --park CAF_Chinko # force one park
  python3 scripts/daily_park_refresh.py --park CAF_Chinko --dry-run
"""

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from secrets_config import secret, app_password
import argparse
import json
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / 'scripts'))

DB_PATH = BASE_DIR / 'db.sqlite3'
GFW_DIR = BASE_DIR / 'data' / 'gfw_alerts'
TURBIDITY_STATE = BASE_DIR / 'data' / 'turbidity' / 'state.json'
STATE_FILE = BASE_DIR / 'data' / 'daily_refresh_state.json'
SERVER_URL = 'http://localhost:8000'
PWD = app_password()

# GFW cluster quality gate (0.01-deg cells): require >=3 alert pixels and at
# least one high/highest-confidence pixel to suppress single-pixel noise.
MIN_ALERTS_PER_CELL = 3
MIN_HIGH_CONF = 1
# Only ingest GFW cells within park bbox + this buffer (scan buffer is 100km,
# but deforestation events are a park-scoped product like the Hansen ones).
BBOX_BUFFER_KM = 10.0
# GFW starts where Hansen ends
MIN_GFW_YEAR = 2024
# ~10m pixels: 1 alert ~ 0.0001 km2
KM2_PER_ALERT = 0.0001
CELL_HALF_DEG = 0.005  # cell is 0.01 deg


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def parks_to_refresh(force_park=None):
    """Parks scanned by either rotation since our last refresh of them."""
    if force_park:
        return [force_park]
    state = load_json(STATE_FILE, {})
    scans = {}
    for pid, info in load_json(GFW_DIR / 'state.json', {}).items():
        scans[pid] = max(scans.get(pid, ''), info.get('scanned_at', ''))
    for pid, info in load_json(TURBIDITY_STATE, {}).items():
        scans[pid] = max(scans.get(pid, ''), info.get('scanned_at', ''))
    due = []
    for pid, scanned_at in scans.items():
        if scanned_at > state.get(pid, {}).get('refreshed_at', ''):
            due.append(pid)
    return sorted(due)


def park_bbox(conn, park_id):
    """Park bbox from existing deforestation geometries, falling back to boundary file."""
    row = conn.execute("""
        SELECT MIN(bbox_minx), MIN(bbox_miny), MAX(bbox_maxx), MAX(bbox_maxy)
        FROM feature_geometries
        WHERE park_id = ? AND feature_type = 'deforestation'
          AND feature_id NOT LIKE 'deforest_gfw_%'
    """, (park_id,)).fetchone()
    if row and row[0] is not None:
        return row
    # fall back to park boundary
    parks = load_json(BASE_DIR / 'data' / 'keystones_with_boundaries.json', [])
    for p in parks:
        if p.get('id') == park_id and p.get('geometry'):
            lons, lats = [], []
            def walk(c):
                if isinstance(c[0], (int, float)):
                    lons.append(c[0]); lats.append(c[1])
                else:
                    for x in c:
                        walk(x)
            walk(p['geometry']['coordinates'])
            return min(lons), min(lats), max(lons), max(lats)
    return None


def name_rivers(conn, park_id, dry_run=False):
    """Push OSM waterway names (osm_places river/stream points) onto
    park_rivers_hydro line segments so the map labels rivers instead of
    showing their names as place markers."""
    if dry_run:
        log(f"  [dry-run] would name rivers from OSM for {park_id}")
        return
    from name_rivers_from_osm import name_park
    n = name_park(conn, park_id, verbose=False)
    log(f"  river naming: {n} segments named from OSM waterway points")


def ingest_gfw_deforestation(conn, rebuilder, park_id, dry_run=False,
                             bbox=None, clip_geom=None):
    """GFW alert cells -> feature_geometries + deforestation_events (years >= 2024).

    Idempotent: deletes and reinserts only rows marked with the
    deforest_gfw_{park}_ feature-id prefix. Hansen rows untouched.
    Returns number of events created.

    bbox/clip_geom exist for AOIs (docs/PLAN_AOI_OVERLAY.md §3a): an AOI has no
    row in the parks bbox source and is not a rectangle, so it passes its own
    bounds plus a shapely polygon that cells must fall inside. For a park both
    stay None and the behaviour is exactly as before.
    """
    scan = load_json(GFW_DIR / f'{park_id}.json', None)
    if not scan or not scan.get('clusters'):
        log(f"  no GFW scan data for {park_id}, skipping GFW ingest")
        return 0

    bbox = bbox or park_bbox(conn, park_id)
    if not bbox:
        log(f"  no bbox for {park_id}, skipping GFW ingest")
        return 0
    buf = BBOX_BUFFER_KM / 111.0
    w, s, e, n = bbox[0] - buf, bbox[1] - buf, bbox[2] + buf, bbox[3] + buf

    # Filter + bucket cells by year of last alert
    by_year = {}
    for c in scan['clusters']:
        if not (w <= c['lon'] <= e and s <= c['lat'] <= n):
            continue
        if clip_geom is not None:
            from shapely.geometry import Point
            if not clip_geom.contains(Point(c['lon'], c['lat'])):
                continue
        if c['n'] < MIN_ALERTS_PER_CELL or c['high_conf'] < MIN_HIGH_CONF:
            continue
        year = int(c['last'][:4])
        if year < MIN_GFW_YEAR:
            continue
        by_year.setdefault(year, []).append(c)

    n_cells = sum(len(v) for v in by_year.values())
    log(f"  GFW: {n_cells} quality cells in bbox+{BBOX_BUFFER_KM:.0f}km "
        f"(years: {sorted(by_year)})")
    if dry_run:
        return 0

    prefix = f'deforest_gfw_{park_id}_'
    conn.execute("DELETE FROM feature_geometries WHERE park_id = ? AND feature_type = 'deforestation' AND feature_id LIKE ?",
                 (park_id, prefix + '%'))
    conn.execute("DELETE FROM deforestation_events WHERE park_id = ? AND polygon_ids LIKE ?",
                 (park_id, prefix + '%'))

    # Context for classification (same loaders as canonical rebuild)
    places = rebuilder._load_park_places(park_id)
    rivers = rebuilder._load_park_rivers(park_id)
    roads = rebuilder._load_park_roads(park_id)
    climate = rebuilder.climate.get(park_id, {})
    park_name = ' '.join(park_id.split('_')[1:]).replace('_', ' ')

    events = 0
    for year, cells in sorted(by_year.items()):
        polygons = []
        for c in cells:
            lat, lon = c['lat'], c['lon']
            area_km2 = round(c['n'] * KM2_PER_ALERT, 4)
            feature_id = f"{prefix}{year}_{lat:.2f}_{lon:.2f}"
            geojson = json.dumps({
                "type": "Polygon",
                "coordinates": [[
                    [lon - CELL_HALF_DEG, lat - CELL_HALF_DEG],
                    [lon + CELL_HALF_DEG, lat - CELL_HALF_DEG],
                    [lon + CELL_HALF_DEG, lat + CELL_HALF_DEG],
                    [lon - CELL_HALF_DEG, lat + CELL_HALF_DEG],
                    [lon - CELL_HALF_DEG, lat - CELL_HALF_DEG],
                ]]
            })
            props = json.dumps({
                "year": year, "area_km2": area_km2, "lat": lat, "lon": lon,
                "source": "gfw_integrated_alerts", "alerts": c['n'],
                "high_conf": c['high_conf'], "first": c['first'], "last": c['last'],
            })
            conn.execute("""
                INSERT OR REPLACE INTO feature_geometries
                (feature_type, feature_id, park_id, geojson,
                 bbox_minx, bbox_miny, bbox_maxx, bbox_maxy,
                 start_date, end_date, properties_json)
                VALUES ('deforestation', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (feature_id, park_id, geojson,
                  lon - CELL_HALF_DEG, lat - CELL_HALF_DEG,
                  lon + CELL_HALF_DEG, lat + CELL_HALF_DEG,
                  c['first'], c['last'], props))
            polygons.append({'feature_id': feature_id, 'area_km2': area_km2,
                             'lat': lat, 'lon': lon})

        # Cluster and create events using the canonical rebuild code paths
        clusters = rebuilder._cluster_polygons(polygons, 5.0)
        for cluster in clusters:
            avg_lat = sum(p['lat'] for p in cluster) / len(cluster)
            avg_lon = sum(p['lon'] for p in cluster) / len(cluster)
            fires_near = rebuilder._get_fire_density(park_id, year, avg_lat, avg_lon, radius_km=10)
            classification = rebuilder._classify_deforestation(cluster, park_id, year, fires_near, roads)
            nearest_place = rebuilder._get_nearest_place(avg_lat, avg_lon, places)
            nearest_river = rebuilder._get_nearest_river(avg_lat, avg_lon, rivers)
            narrative = rebuilder._generate_deforestation_narrative(
                park_name, year, classification, nearest_place, nearest_river, climate)
            conn.execute("""
                INSERT INTO deforestation_events
                (park_id, year, area_km2, lat, lon, pattern_type, classification,
                 classification_confidence, narrative, fires_same_year, fire_ratio,
                 polygon_ids, classified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (park_id, year, classification['total_area_km2'], avg_lat, avg_lon,
                  classification['pattern'], classification['classification'],
                  classification['confidence'], narrative, fires_near,
                  classification['fire_ratio'],
                  ','.join(p['feature_id'] for p in cluster),
                  datetime.now().isoformat()))
            events += 1

    conn.commit()
    log(f"  GFW: created {events} deforestation events (years >= {MIN_GFW_YEAR})")
    return events


def reclassify_deforestation(conn, rebuilder, park_id, dry_run=False):
    """Re-run the canonical python classifier over the park's GFW-era events.

    ONLY year >= 2024 (GFW-sourced) rows are touched. Historical Hansen rows
    (<= 2023) were classified 2026-02-15 against a fire_detections table that
    contained full 2018+ history; that table now only holds 2026+ NRT data, so
    reclassifying old rows would zero fires_same_year and flip classifications
    (verified live: 56/231 flips on CAF_Chinko). Nearest-place snapshots have
    also drifted. Keep historical narratives byte-identical.

    UPDATE in place: id, polygon_ids, year, lat, lon, area preserved —
    only classification/confidence/pattern/narrative/fire stats change.
    """
    rows = conn.execute("""
        SELECT id, year, lat, lon, polygon_ids
        FROM deforestation_events WHERE park_id = ? AND year >= 2024
    """, (park_id,)).fetchall()
    if not rows:
        return 0

    # Bulk-load polygon metadata for the park
    poly = {}
    for r in conn.execute("""
        SELECT feature_id,
               json_extract(properties_json, '$.area_km2') as area_km2,
               json_extract(properties_json, '$.lat') as lat,
               json_extract(properties_json, '$.lon') as lon
        FROM feature_geometries
        WHERE park_id = ? AND feature_type = 'deforestation'
    """, (park_id,)):
        poly[r['feature_id']] = {'feature_id': r['feature_id'],
                                 'area_km2': float(r['area_km2'] or 0),
                                 'lat': float(r['lat'] or 0),
                                 'lon': float(r['lon'] or 0)}

    places = rebuilder._load_park_places(park_id)
    rivers = rebuilder._load_park_rivers(park_id)
    roads = rebuilder._load_park_roads(park_id)
    climate = rebuilder.climate.get(park_id, {})
    park_name = ' '.join(park_id.split('_')[1:]).replace('_', ' ')

    updated = 0
    for ev in rows:
        ids = [i for i in (ev['polygon_ids'] or '').split(',') if i]
        polygons = [poly[i] for i in ids if i in poly]
        if not polygons:
            continue
        fires_near = rebuilder._get_fire_density(park_id, ev['year'], ev['lat'], ev['lon'], radius_km=10)
        classification = rebuilder._classify_deforestation(polygons, park_id, ev['year'], fires_near, roads)
        nearest_place = rebuilder._get_nearest_place(ev['lat'], ev['lon'], places)
        nearest_river = rebuilder._get_nearest_river(ev['lat'], ev['lon'], rivers)
        narrative = rebuilder._generate_deforestation_narrative(
            park_name, ev['year'], classification, nearest_place, nearest_river, climate)
        if not dry_run:
            conn.execute("""
                UPDATE deforestation_events SET
                    pattern_type = ?, classification = ?, classification_confidence = ?,
                    narrative = ?, fires_same_year = ?, fire_ratio = ?, classified_at = ?
                WHERE id = ?
            """, (classification['pattern'], classification['classification'],
                  classification['confidence'], narrative, fires_near,
                  classification['fire_ratio'], datetime.now().isoformat(), ev['id']))
        updated += 1

    if not dry_run:
        conn.commit()
    log(f"  reclassified {updated} deforestation events (python canonical style)")
    return updated


def reload_fire_groups(park_id, dry_run=False):
    """Reload fire trajectories with fresh place/river/road context."""
    if dry_run:
        log("  [dry-run] would run load_fire_groups_to_db.py --park --force")
        return
    if not (BASE_DIR / 'data' / 'fire_groups_v5' / f'{park_id}.json').exists():
        log(f"  no fire groups file for {park_id}, skipping fire reload")
        return
    r = subprocess.run(
        [sys.executable, str(BASE_DIR / 'scripts' / 'load_fire_groups_to_db.py'),
         '--park', park_id, '--force'],
        cwd=BASE_DIR, capture_output=True, text=True, timeout=1800)
    tail = (r.stdout or r.stderr).strip().splitlines()[-3:]
    for line in tail:
        log(f"    {line}")
    if r.returncode != 0:
        raise RuntimeError(f"load_fire_groups_to_db failed for {park_id}")


def call_refresh_endpoint(park_id, dry_run=False):
    """Go server: force settlement reclassify + fire_narrative_cache recompute."""
    if dry_run:
        log("  [dry-run] would POST /api/refresh-park")
        return
    url = f"{SERVER_URL}/api/refresh-park?park={park_id}&pwd={PWD}"
    try:
        req = urllib.request.Request(url, method='POST')
        with urllib.request.urlopen(req, timeout=300) as resp:
            log(f"  refresh-park: {resp.read().decode()[:200]}")
    except Exception as ex:
        log(f"  WARNING: /api/refresh-park failed: {ex} (server down?)")


def export_park_json(park_id, dry_run=False):
    if dry_run:
        log("  [dry-run] would export park JSON")
        return
    r = subprocess.run(
        [sys.executable, str(BASE_DIR / 'scripts' / 'export_events_from_db.py'),
         '--park', park_id],
        cwd=BASE_DIR, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        log(f"  WARNING: export failed: {(r.stderr or '').strip()[:200]}")
    else:
        log(f"  exported data/{{deforestation,settlement}}_events/{park_id}.json")


def save_state(park_id):
    state = load_json(STATE_FILE, {})
    state[park_id] = {"refreshed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rotate', action='store_true',
                    help='refresh parks scanned since their last refresh (cron)')
    ap.add_argument('--park', help='force-refresh a single park')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if not args.rotate and not args.park:
        ap.error('need --rotate or --park')

    targets = parks_to_refresh(args.park)
    if not targets:
        log("nothing to refresh")
        return
    log(f"refreshing: {', '.join(targets)}")

    # Import lazily so cron logs a clear error if the module moves
    from rebuild_events_enhanced import EventRebuilder
    rebuilder = EventRebuilder()
    conn = rebuilder.conn
    conn.row_factory = sqlite3.Row

    from cron_notify import notify_status
    for park_id in targets:
        log(f"=== {park_id} ===")
        try:
            name_rivers(conn, park_id, args.dry_run)
            ingest_gfw_deforestation(conn, rebuilder, park_id, args.dry_run)
            reclassify_deforestation(conn, rebuilder, park_id, args.dry_run)
            reload_fire_groups(park_id, args.dry_run)
            call_refresh_endpoint(park_id, args.dry_run)
            export_park_json(park_id, args.dry_run)
        except Exception as ex:
            if not args.dry_run:
                notify_status("park_refresh_failed", "Daily Park Refresh Failed",
                              f"{park_id}: {str(ex)[:200]}")
            raise
        if not args.dry_run:
            save_state(park_id)
            notify_status("park_refresh_success", "Daily Park Refresh Complete",
                          f"{park_id}: deforestation ingested+reclassified, "
                          f"fire groups reloaded, narratives refreshed, JSON exported")
        log(f"=== {park_id} done ===")

    conn.close()


if __name__ == '__main__':
    main()
