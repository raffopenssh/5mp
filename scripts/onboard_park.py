#!/usr/bin/env python3
"""
On-the-fly park onboarding worker.

Processes pending rows in park_onboarding_requests (created by
POST /api/parks/request-onboard when a logged-in user searched for a park we
don't have but WDPA does). For each pending prod request:

 1. Fetch boundary + metadata from Protected Planet API (WDPA id).
 2. Assign park_id = {ISO3}_{SanitizedName}; append to
    data/keystones_with_boundaries.json (backup written first).
 3. Backfill fires from FIRMS archive API for park bbox+100km buffer:
    window = all-time (2018-04-01, global dataset start) .. today.
    ~600 five-day FIRMS requests, roughly 25-30 min per park.
 4. Backfill GFW deforestation alerts (same window) via analysis/gfw_alerts.py
    --park (rotation state untouched -> new park also becomes top rotation
    priority for future scans automatically, since never-scanned sorts first).
    Alerts only start in 2024, so scripts/hansen_loss.py then streams Hansen
    lossyear 2001-2023 for the same park (public COGs via /vsicurl, no local
    tiles needed) and clusters it into deforestation_events -- otherwise a new
    park shows two years of loss next to 161 parks showing twenty-four.
 5. GHSL settlements + HydroRIVERS/lakes: processed only if the source
    datasets are present locally (data/ghsl/, data/hydro_source/); otherwise
    recorded in `detail` as skipped — rerun later after fetching sources.
 6. Run v5 fire pipeline for the park (trajectories -> DB load -> narratives).
 7. Restart 5mp so the new keystone loads; write a 'park is live' notification.

Cron (run before the 3am fire update):
  30 2 * * * cd /home/exedev/5mp && python3 scripts/onboard_park.py >> logs/onboard_park.log 2>&1

Manual: python3 scripts/onboard_park.py [--request-id N] [--dry-run]
"""

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / 'scripts'))
DB_PATH = BASE_DIR / 'db.sqlite3'
KEYSTONES = BASE_DIR / 'data' / 'keystones_with_boundaries.json'

PP_API = "https://api.protectedplanet.net/v3"


def pp_token():
    """Protected Planet API token, from secrets.env — never a literal here.

    The same variable the Go client reads (srv/protectedplanet/client.go).
    ⚠️ The token that used to be written on this line is in the repository's
    history and must be treated as public; rotate it upstream. Absent is not
    refused: Protected Planet answers a missing token with 401, and an
    unexplained 401 gets debugged as an outage, so this raises with the name of
    the variable to set.
    """
    from secrets_config import secret
    tok = secret('PROTECTEDPLANET_TOKEN')
    if not tok:
        raise SystemExit(
            "PROTECTEDPLANET_TOKEN is unset. Add it to secrets.env "
            "(see secrets.env.example) and re-run.")
    return tok

FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
BUFFER_KM = 100  # matches park_assigner.ASSIGN_MAX_DIST_KM


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def firms_key():
    from secrets_config import secret
    return secret('NASA_FIRMS_KEY')


def http_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': '5mp-onboard/1.0'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_pp_area(wdpa_id):
    """Fetch PA metadata + geometry from Protected Planet."""
    url = f"{PP_API}/protected_areas/{wdpa_id}?token={pp_token()}&with_geometry=true"
    data = http_json(url)
    pa = data.get('protected_area')
    if not pa:
        raise RuntimeError(f"WDPA {wdpa_id} not found on Protected Planet")
    geom = (pa.get('geojson') or {}).get('geometry')
    if not geom:
        raise RuntimeError(f"WDPA {wdpa_id} has no geometry")
    countries = pa.get('countries') or []
    iso3 = countries[0].get('iso_3') if countries else 'XXX'
    cname = countries[0].get('name') if countries else ''
    return {
        'name': pa.get('name'), 'iso3': iso3, 'country': cname,
        'area_km2': float(pa.get('reported_area') or 0), 'geometry': geom,
        'iucn': pa.get('iucn_category', {}).get('name') if isinstance(pa.get('iucn_category'), dict) else '',
    }


def make_park_id(iso3, name):
    clean = re.sub(r'[^A-Za-z0-9 _-]', '', name)
    clean = re.sub(r'[ _-]+', '_', clean).strip('_')
    return f"{(iso3 or 'XXX').upper()}_{clean}"


def geom_bbox(geometry):
    lons, lats = [], []
    def walk(c):
        if isinstance(c[0], (int, float)):
            lons.append(c[0]); lats.append(c[1])
        else:
            for x in c:
                walk(x)
    walk(geometry['coordinates'])
    return min(lons), min(lats), max(lons), max(lats)


def append_keystone(park_id, meta, dry_run):
    parks = json.load(open(KEYSTONES))
    if any(p['id'] == park_id for p in parks):
        log(f"  keystone {park_id} already present")
        return False
    minx, miny, maxx, maxy = geom_bbox(meta['geometry'])
    entry = {
        'id': park_id,
        'country_code': meta['iso3'],
        'country': meta['country'],
        'name': meta['name'],
        'partner': '', 'staff': None, 'budget': None, 'donor': '',
        'performance': None,
        'wdpa_id': str(meta['wdpa_id']),
        'area_km2': meta['area_km2'],
        'coordinates': {'lat': (miny + maxy) / 2, 'lon': (minx + maxx) / 2},
        'geometry': meta['geometry'],
        'onboarded_at': datetime.now(timezone.utc).isoformat(),
    }
    if dry_run:
        log(f"  [dry-run] would append keystone {park_id}")
        return True
    shutil.copy(KEYSTONES, str(KEYSTONES) + '.bak')
    parks.append(entry)
    tmp = str(KEYSTONES) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(parks, f)
    Path(tmp).rename(KEYSTONES)
    log(f"  keystone appended ({len(parks)} parks total)")
    return True


# Global VIIRS coverage in fire_detections starts here; all-time backfill
# for a park bbox is ~600 five-day FIRMS requests ≈ 25-30 min. Cron runs at
# 01:00 so even a slow park finishes well before the 03:00 daily fire update.
FIRE_DATA_START = datetime(2018, 4, 1, tzinfo=timezone.utc).date()


def backfill_window():
    """All-time: global fire dataset start .. today."""
    today = datetime.now(timezone.utc).date()
    return FIRE_DATA_START, today


def backfill_fires(conn, park_id, geometry, dry_run):
    """FIRMS archive backfill for the park bbox + 100km, upserted with
    canonical park assignment (park_assigner)."""
    minx, miny, maxx, maxy = geom_bbox(geometry)
    d = BUFFER_KM / 111.0
    area = f"{max(minx-d,-180):.3f},{max(miny-d,-90):.3f},{min(maxx+d,180):.3f},{min(maxy+d,90):.3f}"
    start, today = backfill_window()
    log(f"  fire backfill {start} .. {today} bbox={area}")
    if dry_run:
        return 0

    import csv as csvmod
    import io
    import requests
    from park_assigner import ParkAssigner
    assigner = ParkAssigner()
    key = firms_key()
    inserted = 0
    cur = conn.cursor()

    # FIRMS area API allows max 5-day ranges ("Invalid day range. Expects [1..5]");
    # SP for archive, NRT for the recent tail.
    day = start
    while day <= today:
        span = min(5, (today - day).days + 1)
        age = (today - day).days
        source = "VIIRS_NOAA20_NRT" if age <= 60 else "VIIRS_NOAA20_SP"
        url = f"{FIRMS_URL}/{key}/{source}/{area}/{span}/{day.strftime('%Y-%m-%d')}"
        try:
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            rows = list(csvmod.DictReader(io.StringIO(resp.text)))
        except Exception as ex:
            log(f"    {day} +{span}d: FIRMS error: {ex}")
            day += timedelta(days=span)
            time.sleep(2)
            continue
        for fire in rows:
            try:
                lat, lon = float(fire['latitude']), float(fire['longitude'])
            except (KeyError, ValueError):
                continue
            if lat == 0.0 or lon == 0.0:
                continue
            pid, dist = assigner.assign(lon, lat)
            cur.execute('''INSERT OR IGNORE INTO fire_detections
                (latitude, longitude, brightness, scan, track, acq_date, acq_time,
                 satellite, instrument, confidence, frp, daynight,
                 in_protected_area, protected_area_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (lat, lon, float(fire.get('bright_ti4') or 0),
                 float(fire.get('scan') or 0), float(fire.get('track') or 0),
                 fire.get('acq_date',''), fire.get('acq_time',''),
                 fire.get('satellite','1'), 'VIIRS', fire.get('confidence',''),
                 float(fire.get('frp') or 0), fire.get('daynight',''),
                 1 if dist == 0.0 else 0, pid))
            inserted += cur.rowcount
        conn.commit()
        log(f"    {day} +{span}d: {len(rows)} rows, {inserted} inserted so far")
        day += timedelta(days=span)
        time.sleep(2)  # be polite to FIRMS
    return inserted


# export_raw_fire_json() was here. It wrote data/raw-fire-viirs-*/{park}.json
# because the v5 builder used to read those files. The builder now reads
# fire_detections via scripts/fire_source.py, so backfilled fires are visible
# to it immediately and the duplicate JSON is gone.


def run(cmd, dry_run, cwd=BASE_DIR, ok_fail=False):
    log(f"  $ {' '.join(cmd)}")
    if dry_run:
        return True
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"    FAILED: {r.stderr[-400:] or r.stdout[-400:]}")
        if not ok_fail:
            raise RuntimeError(f"{cmd[0]} failed")
        return False
    return True


def notify(conn, park_id, title, msg, env='prod'):
    conn.execute('''INSERT INTO notifications (park_id, notification_type, title, message, env)
                    VALUES (?, 'park_onboarding', ?, ?, ?)''', (park_id, title, msg, env))
    conn.commit()


def process_request(conn, req, dry_run):
    wdpa_id, name = req['wdpa_id'], req['name']
    log(f"=== onboarding WDPA {wdpa_id}: {name} ===")
    conn.execute("UPDATE park_onboarding_requests SET status='processing' WHERE id=?", (req['id'],))
    conn.commit()
    notes = []

    meta = fetch_pp_area(wdpa_id)
    meta['wdpa_id'] = wdpa_id
    park_id = make_park_id(meta['iso3'], meta['name'])
    log(f"  park_id={park_id} area={meta['area_km2']}km2")

    append_keystone(park_id, meta, dry_run)

    # Fire backfill (all-time) + v5 pipeline
    n = backfill_fires(conn, park_id, meta['geometry'], dry_run)
    notes.append(f"fires backfilled: {n}")

    py = sys.executable or 'python3'
    run([py, 'scripts/rebuild_fire_trajectories_v5.py', '--park', park_id], dry_run, ok_fail=True)
    run([py, 'scripts/load_fire_groups_to_db.py', '--park', park_id, '--force'], dry_run, ok_fail=True)
    run([py, 'scripts/precompute_narratives_v5.py', '--park', park_id], dry_run, ok_fail=True)
    run([py, 'scripts/build_fire_grid_agg.py', '--since',
         backfill_window()[0].strftime('%Y-%m-%d')], dry_run, ok_fail=True)

    # GFW deforestation alerts (script scans one park; also seeds rotation state)
    if not run([py, 'analysis/gfw_alerts.py', '--park', park_id], dry_run, ok_fail=True):
        notes.append('gfw: failed (will retry via rotation)')

    # Hansen forest loss 2001-2023 — the history the alerts do not have.
    #
    # GFW integrated alerts only start in 2024, so an alerts-only park sits on
    # the globe next to 161 parks with a 2001-2024 record and looks pristine.
    # This used to need the 26-tile data/hansen/ download that is not on this
    # machine; hansen_loss.py streams the same public COGs through /vsicurl
    # (~50 s per 2-degree window, no quota), so onboarding can just do it. Same
    # cutover as everywhere else: Hansen <=2023, alerts >=2024, never both.
    if not run([py, 'scripts/hansen_loss.py', '--park', park_id,
                '--minutes', '45'], dry_run, ok_fail=True):
        notes.append('hansen: failed (rerun scripts/hansen_loss.py --park ' + park_id + ')')

    # GHSL settlements — needs local source raster
    if (BASE_DIR / 'data' / 'ghsl').exists():
        run([py, 'scripts/process_settlement_polygons.py', '--park', park_id], dry_run, ok_fail=True)
    else:
        notes.append('settlements: skipped (no data/ghsl source; fetch tiles + rerun)')

    # HydroRIVERS/lakes — needs local source GDB
    if (BASE_DIR / 'data' / 'hydro_source').exists():
        run([py, 'scripts/extract_hydro_data.py', '--park', park_id], dry_run, ok_fail=True)
        run([py, 'scripts/import_all_data.py', '--table', 'rivers_hydro'], dry_run, ok_fail=True)
        run([py, 'scripts/import_all_data.py', '--table', 'lakes_hydro'], dry_run, ok_fail=True)
    else:
        notes.append('rivers/lakes: skipped (no data/hydro_source; fetch HydroSHEDS + rerun)')

    detail = '; '.join(notes)
    if dry_run:
        log(f"  [dry-run] done: {detail}")
        return

    conn.execute('''UPDATE park_onboarding_requests
                    SET status='ready', park_id=?, detail=?, processed_at=?
                    WHERE id=?''',
                 (park_id, detail, datetime.now(timezone.utc).isoformat(), req['id']))
    conn.commit()
    notify(conn, park_id, f"New park live: {meta['name']}",
           f"{meta['name']} ({meta['country']}) is now on the globe as {park_id}. "
           f"{detail}. Daily scans will prioritise this park for deforestation "
           f"and turbidity coverage over the coming days.")
    log(f"=== {park_id} ready: {detail} ===")


def remove_keystone(park_id, dry_run):
    parks = json.load(open(KEYSTONES))
    keep = [p for p in parks if p['id'] != park_id]
    if len(keep) == len(parks):
        log(f"  keystone {park_id} not in file")
        return False
    # Safety: only parks added by onboarding may be removed.
    victim = next(p for p in parks if p['id'] == park_id)
    if not victim.get('onboarded_at'):
        raise RuntimeError(f"{park_id} is an original keystone; refusing to remove")
    if dry_run:
        log(f"  [dry-run] would remove keystone {park_id}")
        return True
    shutil.copy(KEYSTONES, str(KEYSTONES) + '.bak')
    tmp = str(KEYSTONES) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(keep, f)
    Path(tmp).rename(KEYSTONES)
    log(f"  keystone removed ({len(keep)} parks remain)")
    return True


def process_removal(conn, req, dry_run):
    park_id = req['park_id']
    log(f"=== removing park {park_id} (WDPA {req['wdpa_id']}) ===")
    remove_keystone(park_id, dry_run)
    if dry_run:
        return
    cur = conn.cursor()
    # Park-scoped derived data. fire_detections rows are kept but unassigned
    # (they're raw satellite observations, not park products).
    for table, col in [
        ('feature_geometries', 'park_id'), ('fire_narrative_cache', 'park_id'),
        ('park_settlements', 'park_id'), ('deforestation_events', 'park_id'),
        ('park_rivers_hydro', 'park_id'), ('park_lakes_hydro', 'park_id'),
        ('park_waterbodies', 'park_id'), ('osm_places', 'park_id'),
        ('roads_heigit', 'park_id'), ('park_climate', 'park_id'),
        ('park_species', 'park_id'), ('notifications', 'park_id'),
    ]:
        try:
            cur.execute(f"DELETE FROM {table} WHERE {col} = ?", (park_id,))
            if cur.rowcount:
                log(f"    {table}: {cur.rowcount} rows deleted")
        except sqlite3.OperationalError:
            pass
    cur.execute("UPDATE fire_detections SET protected_area_id=NULL, in_protected_area=0 "
                "WHERE protected_area_id = ?", (park_id,))
    log(f"    fire_detections: {cur.rowcount} rows unassigned")
    conn.commit()
    for d in ['fire_groups_v5', 'export/fire_narratives', 'deforestation_events',
              'settlement_events', 'gfw_alerts']:
        f = BASE_DIR / 'data' / d / f'{park_id}.json'
        if f.exists():
            f.unlink()
    conn.execute("UPDATE park_onboarding_requests SET status='removed', processed_at=? WHERE id=?",
                 (datetime.now(timezone.utc).isoformat(), req['id']))
    conn.commit()
    notify(conn, park_id, f"Park removed: {req['name']}",
           f"{req['name']} ({park_id}) and its derived data layers were removed as requested.")
    log(f"=== {park_id} removed ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--request-id', type=int, help='process a single request')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    q = ("SELECT * FROM park_onboarding_requests "
         # Any real tenant may request an onboarding (a park is global data);
         # only the shared demo sandbox is skipped.
         "WHERE status IN ('pending','failed','remove_requested') AND env <> 'test'")
    params = ()
    if args.request_id:
        q = "SELECT * FROM park_onboarding_requests WHERE id=?"
        params = (args.request_id,)
    reqs = conn.execute(q + " ORDER BY requested_at", params).fetchall()
    if not reqs:
        log("no pending onboarding requests")
        return

    # Deadline guard: the 03:00 daily fire update must not overlap with a
    # long all-time backfill. Stop starting new requests after 02:30 UTC;
    # leftovers stay 'pending' and run next night. (No guard for --request-id.)
    def past_deadline():
        if args.request_id:
            return False
        now = datetime.now(timezone.utc)
        return now.hour == 2 and now.minute >= 30 or 3 <= now.hour < 5

    any_ok = False
    for req in reqs:
        if past_deadline():
            log(f"deadline (02:30 UTC) reached; deferring request {req['id']} ({req['name']}) to next run")
            continue
        try:
            if req['status'] == 'remove_requested':
                process_removal(conn, dict(req), args.dry_run)
            else:
                process_request(conn, dict(req), args.dry_run)
            any_ok = True
        except Exception as ex:
            log(f"FAILED: {ex}")
            if not args.dry_run:
                conn.execute("UPDATE park_onboarding_requests SET status='failed', detail=? WHERE id=?",
                             (str(ex)[:400], req['id']))
                conn.commit()

    conn.close()
    if any_ok and not args.dry_run:
        log("restarting 5mp to load new keystones")
        subprocess.run(['sudo', 'systemctl', 'restart', '5mp'])


if __name__ == '__main__':
    main()
