#!/usr/bin/env python3
"""
NRT -> SP reconciliation for fire_detections: audit, and dedupe-safe apply.

WHY
---
FIRMS serves each detection twice: once as NRT (minutes-old, provisional
geolocation/confidence) and weeks later as SP (Standard Processing, reprocessed
with definitive ephemeris). The nightly job ingests NRT and never re-fetches,
so in principle our older rows keep provisional values forever
(docs/FIRE_PIPELINE.md, "NRT detections are never revised").

A naive SP re-ingest would DUPLICATE rather than update: the uniqueness key is
`UNIQUE(latitude, longitude, acq_date, acq_time, satellite)` over raw REALs, so
any revised field forks a second row for the same fire. Hence: measure first,
and if you do write, write through a matcher rather than an INSERT.

WHAT WE MEASURED (2026-08, six genuinely-NRT windows -- see
data/eval/nrt_sp/*.json and docs/FIRE_PIPELINE.md)
---------------------------------------------------------------------------
Coordinates: 100% byte-identical. FRP: identical. Confidence: identical.
The ONLY field SP revises is `acq_time`, by 1-2 minutes, on 0-86% of rows
depending on the overpass. Day-level clustering cannot see a 2-minute shift,
so trajectories are unaffected -- but that shift is exactly what would fork the
UNIQUE key, so an SP re-ingest would be all cost and no benefit.

=> Reconciliation is a NO-OP TODAY. What is worth having is a cheap watchdog:
   if FIRMS ever changes this (new VIIRS collection, ephemeris fix), we must
   find out from the pipeline, not from a user reporting a fire in the wrong
   place. That is `--audit`, wired into the nightly cron (monthly, step 2e).

MODES
-----
  --audit   (default) read-only. Re-fetch one window from SP into a scratch
            table, match it against our rows, report shift/FRP/confidence/
            add/drop distributions and a verdict. Never touches
            fire_detections.

  --apply   dedupe-safe write. Matches SP rows to existing rows the same way
            the audit does and UPDATEs them in place (acq_time, coords, frp,
            confidence, brightness); genuinely-new SP rows are INSERTed with
            canonical park assignment. Because it matches before writing, it
            cannot create the duplicate-row class described above. Requires
            --yes; --apply alone is a dry run that prints what it would do.

USAGE
-----
    python3 scripts/reconcile_nrt_sp.py --dry-run           # show the plan
    python3 scripts/reconcile_nrt_sp.py --from 2026-05-13 --days 5 \
        --bbox 20,-16,32,-8 --json data/eval/nrt_sp/kafue_2026-05.json

    # what the nightly cron runs (monthly): a fixed sample window, JSON out,
    # exit 4 if the drift is material
    python3 scripts/reconcile_nrt_sp.py --audit --watchdog \
        --json data/nrt_sp_audit.json

    # only if the audit ever says material:
    python3 scripts/reconcile_nrt_sp.py --apply --from 2026-05-13 --days 5 \
        --bbox 20,-16,32,-8 --yes

CAVEATS
-------
- Only windows whose DB copy really came from NRT are informative. SNPP and
  NOAA-21 history was pulled straight from SP by
  `scripts/backfill_viirs_sensors.py`, so those match at zero shift by
  construction. The script recovers provenance from AUTOINCREMENT id ordering
  and warns when a window looks backfilled.
- SP and NRT coverage for one sensor never overlap (NOAA-20 SP ends 2026-05-31,
  NRT starts 06-01), so you can never fetch the same day both ways; the
  comparison is always our-DB-copy vs the SP archive.
- Add/drop rates across the whole bbox are NOT a drift signal: our ingest scope
  changed over the years (per-park bbox buffers -> ParkAssigner 100km -> keep
  everything), FIRMS has no such notion. The verdict runs on a symmetric
  subset, both sides clipped with the same assigner.
"""

import sys
import os
import json
import time
import sqlite3
import argparse
import statistics
import requests
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from secrets_config import secret

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
NASA_API_KEY = secret('NASA_FIRMS_KEY')
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
CHUNK_DAYS = 5           # FIRMS hard cap per dated request
VIIRS_PIXEL_M = 375.0    # nominal nadir pixel; the "does this matter" yardstick
SCRATCH = "fire_detections_sp_scratch"

# Watchdog sample: Kafue/Zambia in peak fire season is the densest window we
# can count on, and it sits comfortably inside the NOAA-20 SP archive.
WATCHDOG_BBOX = "20,-16,32,-8"
WATCHDOG_BACKOFF_DAYS = 12

# SP source + archive coverage per satellite code (FIRMS availability endpoint).
# NOAA-21 has no SP archive at all, so it can never be reconciled.
SP_SOURCE = {
    'N':   ("VIIRS_SNPP_SP",   "2012-01-20", "2026-04-27"),
    'N20': ("VIIRS_NOAA20_SP", "2018-04-01", "2026-05-31"),
}
# Whether our DB copy of that sensor came from the nightly NRT job. NOAA-20 has
# been ingested nightly as NRT since the beginning and was never re-fetched, so
# any NOAA-20 date inside the SP archive is a genuine NRT-vs-SP comparison.
# SNPP and NOAA-21 history was pulled straight from SP by
# backfill_viirs_sensors.py, so SNPP would match at ~0 shift by construction.
DB_COPY_IS_NRT = {'N': False, 'N20': True}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_firms_csv(text):
    lines = text.strip().split('\n')
    if len(lines) < 2:
        return []
    header = lines[0].split(',')
    out = []
    for line in lines[1:]:
        vals = line.split(',')
        if len(vals) >= len(header):
            out.append(dict(zip(header, vals)))
    return out


def fetch(source, area, start, days):
    url = f"{FIRMS_URL}/{NASA_API_KEY}/{source}/{area}/{days}/{start}"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=180)
            if r.status_code == 429:
                wait = 30 * (attempt + 1)
                log(f"    rate limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return parse_firms_csv(r.text)
        except Exception as e:
            log(f"    attempt {attempt+1}/3 failed: {str(e)[:120]}")
            time.sleep(10 * (attempt + 1))
    return None


def nrt_provenance(conn, sat, start, end):
    """Fraction of window rows that were inserted BEFORE rows acquired later.

    fire_detections.id is a plain AUTOINCREMENT, so insertion order is
    recoverable. A row ingested by the nightly NRT job always has a smaller id
    than any row acquired a month later; a row written by a bulk historical
    backfill (which read the SP archive) has a larger one. This is the only way
    to tell, after the fact, whether a given window's DB copy is really NRT --
    and comparing an SP-sourced window against SP is a tautology.

    Returns (nrt_fraction, n_rows).
    """
    later = conn.execute(
        "SELECT MIN(id) FROM fire_detections WHERE satellite = ? "
        "AND acq_date BETWEEN ? AND ?",
        (sat, (end + timedelta(days=30)).strftime('%Y-%m-%d'),
         (end + timedelta(days=40)).strftime('%Y-%m-%d'))).fetchone()[0]
    row = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN id > ? THEN 1 ELSE 0 END) "
        "FROM fire_detections WHERE satellite = ? AND acq_date >= ? AND acq_date < ?",
        (later if later is not None else 1 << 62, sat,
         start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))).fetchone()
    total, backfilled = row[0], (row[1] or 0)
    if later is None or not total:
        return None, total
    return 1.0 - backfilled / total, total


def hhmm(t):
    """acq_time is stored unpadded ('1', '11', '1246' all occur)."""
    return str(t or '').strip().zfill(4)


def km_per_deg_lon(lat):
    return 111.32 * max(0.05, np.cos(np.radians(lat)))


def ensure_scratch(conn):
    conn.execute(f"DROP TABLE IF EXISTS {SCRATCH}")
    conn.execute(f"""
        CREATE TABLE {SCRATCH} (
            latitude REAL, longitude REAL, acq_date TEXT, acq_time TEXT,
            satellite TEXT, confidence TEXT, frp REAL, brightness REAL,
            daynight TEXT, dist_km REAL
        )
    """)
    conn.commit()


def match_day(nrt, sp, max_shift_km, time_tol_min):
    """Greedy one-to-one nearest-neighbour match within one acq_date.

    Matching is 3D: metric-km x/y plus acquisition time scaled so that
    `time_tol_min` costs exactly `max_shift_km`. SP revises acq_time by a
    minute or two (DB 1208 vs SP 1207 on the same overpass), so an exact
    (date, time) bucket key throws away ~85% of the true pairs -- which reads
    as a catastrophic drop/add rate that is pure artefact.

    Returns (pairs, unmatched_nrt, unmatched_sp), pairs = [(n, s, dist_km)].
    """
    if not nrt or not sp:
        return [], list(nrt), list(sp)

    from scipy.spatial import cKDTree

    lat0 = float(np.mean([r['lat'] for r in nrt]))
    kx = km_per_deg_lon(lat0)
    ky = 110.57
    tw = max_shift_km / max(1e-6, time_tol_min)   # km per minute

    def xyz(rows):
        return np.array([[r['lon'] * kx, r['lat'] * ky, r['tmin'] * tw]
                         for r in rows])

    n_xyz, s_xyz = xyz(nrt), xyz(sp)
    tree = cKDTree(s_xyz)
    k = min(6, len(sp))
    dists, idxs = tree.query(n_xyz, k=k, distance_upper_bound=max_shift_km)
    if k == 1:
        dists = dists.reshape(-1, 1)
        idxs = idxs.reshape(-1, 1)

    cands = []
    for i in range(len(nrt)):
        for j in range(k):
            if np.isfinite(dists[i][j]):
                cands.append((dists[i][j], i, int(idxs[i][j])))
    cands.sort()

    used_n, used_s, pairs = set(), set(), []
    for _, i, j in cands:
        if i in used_n or j in used_s:
            continue
        a, b = nrt[i], sp[j]
        dx = (a['lon'] - b['lon']) * kx
        dy = (a['lat'] - b['lat']) * ky
        used_n.add(i)
        used_s.add(j)
        pairs.append((a, b, float(np.hypot(dx, dy))))
    return (pairs,
            [nrt[i] for i in range(len(nrt)) if i not in used_n],
            [sp[j] for j in range(len(sp)) if j not in used_s])


def apply_revisions(conn, assigner, sat, db_rows, sp_rows, args, commit):
    """Write SP revisions into fire_detections through the matcher.

    The whole hazard of an SP re-ingest is that
    `UNIQUE(latitude, longitude, acq_date, acq_time, satellite)` is built from
    raw REALs, so a revised acq_time or coordinate forks a *second* row for the
    same fire instead of updating it. We therefore never INSERT a row that the
    matcher paired with an existing one: pairs become UPDATE ... WHERE id = ?,
    and only genuinely-unpaired SP rows are inserted (with canonical park
    assignment, same rule as the nightly job).

    Unpaired *DB* rows are left alone. SP dropping a detection is usually our
    ingest scope differing from the FIRMS bbox, not a retraction, and deleting
    real history on that basis is not a trade worth making.
    """
    from collections import defaultdict as _dd

    def bucket(rows):
        b = _dd(list)
        for r in rows:
            t = hhmm(r['acq_time'])
            b[r['acq_date']].append({
                'id': r['id'] if 'id' in r.keys() else None,
                'lat': r['latitude'], 'lon': r['longitude'],
                'frp': r['frp'] or 0.0, 'conf': (r['confidence'] or '').strip(),
                'time': t, 'tmin': int(t[:2]) * 60 + int(t[2:]), 'raw': r,
            })
        return b

    nb, sb = bucket(db_rows), bucket(sp_rows)
    updates, inserts, unchanged = [], [], 0
    for key in set(nb) | set(sb):
        pairs, _un, us = match_day(nb.get(key, []), sb.get(key, []),
                                   args.max_shift_km, args.time_tol_min)
        for a, b, _d in pairs:
            sp = b['raw']
            new = (sp['latitude'], sp['longitude'], sp['acq_time'],
                   sp['confidence'], sp['frp'], sp['brightness'],
                   sp['daynight'])
            old = (a['raw']['latitude'], a['raw']['longitude'],
                   a['raw']['acq_time'], a['raw']['confidence'],
                   a['raw']['frp'], None, None)
            # brightness/daynight are not selected on the DB side; compare only
            # the fields we read back, and always write the full SP row.
            if new[:5] == old[:5]:
                unchanged += 1
                continue
            updates.append((*new, a['id']))
        inserts.extend(x['raw'] for x in us)

    log(f"Apply: {len(updates):,} rows to update, {len(inserts):,} to insert, "
        f"{unchanged:,} already identical"
        + ("" if commit else "   (DRY RUN - pass --yes to commit)"))

    if not commit:
        return {'committed': False, 'would_update': len(updates),
                'would_insert': len(inserts), 'unchanged': unchanged}

    cur = conn.cursor()
    # UPDATE OR IGNORE, not plain UPDATE: rewriting acq_time/coords can land a
    # row exactly on another existing row's UNIQUE key (two NRT rows collapsing
    # onto one SP row). Aborting the whole batch for that is worse than keeping
    # the older row, so let SQLite skip those and count them.
    cur.executemany("""
        UPDATE OR IGNORE fire_detections
           SET latitude = ?, longitude = ?, acq_time = ?, confidence = ?,
               frp = ?, brightness = ?, daynight = ?
         WHERE id = ?
    """, updates)
    updated = cur.rowcount
    skipped = len(updates) - updated

    inserted = 0
    for r in inserts:
        park_id, dist_km = (assigner.assign(r['longitude'], r['latitude'])
                            if assigner else (None, None))
        cur.execute("""
            INSERT OR IGNORE INTO fire_detections
            (latitude, longitude, brightness, acq_date, acq_time, satellite,
             instrument, confidence, frp, daynight, in_protected_area,
             protected_area_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (r['latitude'], r['longitude'], r['brightness'], r['acq_date'],
              r['acq_time'], sat, 'VIIRS', r['confidence'], r['frp'],
              r['daynight'], 1 if dist_km == 0.0 else 0, park_id))
        inserted += cur.rowcount
    conn.commit()
    log(f"Apply: committed {updated:,} updates, {inserted:,} inserts"
        + (f", {skipped:,} updates skipped (would collide with an existing "
           f"row's UNIQUE key)" if skipped else ""))
    log("Rerun scripts/build_fire_grid_agg.py --since <window start> and the "
        "v5 rebuild for affected parks; edited detections do not propagate "
        "on their own.")
    return {'committed': True, 'updated': updated, 'inserted': inserted,
            'skipped_collision': skipped, 'unchanged': unchanged}


def pct(arr, p):
    return float(np.percentile(arr, p)) if len(arr) else float('nan')


def main():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument('--from', dest='date_from', default='2025-07-01',
                    help='Start date; must be inside the SP archive window '
                         '(NOAA-20 SP: 2018-04-01..2026-05-31)')
    ap.add_argument('--days', type=int, default=5)
    ap.add_argument('--bbox', default='20,-16,32,-8',
                    help='west,south,east,north (default: Kafue/Zambia savanna)')
    ap.add_argument('--satellite', default='N20', choices=sorted(SP_SOURCE),
                    help='Satellite code to compare (default N20 = NOAA-20, '
                         'the sensor our nightly NRT job has always ingested)')
    ap.add_argument('--time-tol-min', type=float, default=6.0,
                    help='Max acq_time difference for a pair (SP re-timestamps '
                         'an overpass by a minute or two)')
    ap.add_argument('--max-shift-km', type=float, default=1.5,
                    help='Matching radius; beyond this a row counts as '
                         'NRT-only / SP-only rather than moved')
    ap.add_argument('--json', dest='json_out', default=None)
    ap.add_argument('--db', default=str(DB_PATH),
                    help='SQLite path (override for tests)')
    ap.add_argument('--keep-scratch', action='store_true')
    ap.add_argument('--park-filter', choices=('auto', 'on', 'off'),
                    default='auto',
                    help="Restrict SP rows to <=100km from a park. Our ingest "
                         "rule changed over time (older backfills dropped "
                         "far-from-park rows; the nightly job keeps them and "
                         "only annotates park_id), so 'auto' (default) "
                         "measures both and reports the one that matches how "
                         "that era was actually ingested.")
    ap.add_argument('--dry-run', action='store_true',
                    help='Print the fetch plan and exit; no network, no writes')
    ap.add_argument('--audit', action='store_true',
                    help='Read-only measurement (default mode)')
    ap.add_argument('--apply', action='store_true',
                    help='Write SP revisions back into fire_detections via the '
                         'matcher (dedupe-safe). Dry run unless --yes.')
    ap.add_argument('--yes', action='store_true',
                    help='Actually commit the --apply writes')
    ap.add_argument('--watchdog', action='store_true',
                    help='Cron mode: auto-pick a recent, genuinely-NRT, '
                         'fire-active window; terse output; exit 4 on '
                         'material drift so the caller can alert')
    args = ap.parse_args()

    if args.apply and args.watchdog:
        ap.error('--apply and --watchdog are mutually exclusive')

    if args.watchdog:
        # Latest SP-covered week, backed off 7d so the archive is complete.
        # WATCHDOG_BBOX is the Zambia/Kafue peak-fire box: it is the densest
        # reliable sample, so a real change in FIRMS processing shows up here
        # first and with enough rows to be significant.
        sp_end = datetime.strptime(SP_SOURCE[args.satellite][2], '%Y-%m-%d')
        args.date_from = (sp_end - timedelta(days=WATCHDOG_BACKOFF_DAYS)
                          ).strftime('%Y-%m-%d')
        args.days = 5
        args.bbox = WATCHDOG_BBOX

    sat = args.satellite
    source, sp_min, sp_max = SP_SOURCE[sat]
    start = datetime.strptime(args.date_from, '%Y-%m-%d')
    end = start + timedelta(days=args.days)

    if not (sp_min <= args.date_from <= sp_max):
        log(f"ERROR: {source} only covers {sp_min}..{sp_max}; "
            f"{args.date_from} is outside it (SP and NRT windows never overlap).")
        sys.exit(2)
    if not DB_COPY_IS_NRT[sat]:
        log(f"WARNING: our {sat} history was backfilled from SP, not NRT, so "
            f"it will match at ~0 shift by construction. Use --satellite N20.")

    log(f"Window {start:%Y-%m-%d} .. {end:%Y-%m-%d}  sat={sat}  source={source}")
    log(f"bbox={args.bbox}  match radius={args.max_shift_km} km")
    if args.dry_run:
        n_req = (args.days + CHUNK_DAYS - 1) // CHUNK_DAYS
        log(f"Dry run: would issue {n_req} FIRMS request(s) into {SCRATCH}.")
        return

    assigner = None
    if args.park_filter != 'off':
        from park_assigner import ParkAssigner
        log("Loading ParkAssigner (100km nearest-boundary, to tag SP rows)...")
        assigner = ParkAssigner()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    prov, prov_rows = nrt_provenance(conn, sat, start, end)
    if prov is None:
        log(f"Provenance: unknown ({prov_rows:,} rows; no rows 30-40d later "
            f"to compare insertion order against)")
    else:
        log(f"Provenance: {prov*100:.1f}% of the {prov_rows:,} DB rows in this "
            f"window were inserted before later-dated rows (= nightly NRT)")
        if prov < 0.9:
            log("WARNING: this window's DB copy looks BACKFILLED from the SP "
                "archive, so comparing it against SP is a tautology and will "
                "trivially report zero drift. Pick another window.")

    ensure_scratch(conn)

    # ---- 1. fetch SP into scratch -------------------------------------
    w, s, e, n = [float(x) for x in args.bbox.split(',')]
    area = f"{w},{s},{e},{n}"
    cur_dt = start
    fetched = 0
    while cur_dt < end:
        days = min(CHUNK_DAYS, (end - cur_dt).days)
        rows = fetch(source, area, cur_dt.strftime('%Y-%m-%d'), days)
        if rows is None:
            log("FIRMS fetch failed after retries; aborting")
            sys.exit(1)
        rows = [r for r in rows if (r.get('satellite') or '') == sat]
        def dist_km(r):
            """km to the nearest park boundary, or None if >100km."""
            if assigner is None:
                return None
            return assigner.assign(float(r['longitude']), float(r['latitude']))[1]
        conn.executemany(
            f"INSERT INTO {SCRATCH} VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(float(r['latitude']), float(r['longitude']), r['acq_date'],
              r['acq_time'], r['satellite'], r.get('confidence', ''),
              float(r.get('frp') or 0), float(r.get('bright_ti4') or 0),
              r.get('daynight', ''), dist_km(r)) for r in rows])
        conn.commit()
        fetched += len(rows)
        log(f"  {cur_dt:%Y-%m-%d} +{days}d: {len(rows):,} SP rows")
        cur_dt += timedelta(days=days)
        time.sleep(1.0)

    # ---- 2. load both sides, bucket by (date, hhmm, satellite) ---------
    def bucketize(rows):
        """Group by acq_date only; acq_time becomes a matching dimension."""
        b = defaultdict(list)
        for r in rows:
            t = hhmm(r['acq_time'])
            b[r['acq_date']].append({
                'id': r['id'] if 'id' in r.keys() else None,
                'lat': r['latitude'], 'lon': r['longitude'],
                'frp': r['frp'] or 0.0, 'conf': (r['confidence'] or '').strip(),
                'date': r['acq_date'], 'time': t,
                'tmin': int(t[:2]) * 60 + int(t[2:]),
                'raw': r,
            })
        return b

    db_all = conn.execute("""
        SELECT id, latitude, longitude, acq_date, acq_time, confidence, frp
        FROM fire_detections
        WHERE satellite = ? AND acq_date >= ? AND acq_date < ?
          AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
    """, (sat, start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'),
          s, n, w, e)).fetchall()
    if not db_all:
        log("No DB rows in that window/bbox - nothing to compare.")
        sys.exit(1)
    sp_all = conn.execute(
        f"SELECT latitude, longitude, acq_date, acq_time, confidence, frp, "
        f"brightness, daynight, dist_km FROM {SCRATCH}").fetchall()

    # Distance-to-park for the DB side too, computed with the *same* assigner
    # as the SP side. Do NOT use the stored in_protected_area / park_id: those
    # were written by whatever ingest rule was current at the time (per-park
    # bbox buffers before ParkAssigner), so mixing them in makes an ingest-rule
    # difference look like an SP revision.
    db_dist = ([assigner.assign(r['longitude'], r['latitude'])[1]
                for r in db_all] if assigner else [None] * len(db_all))

    def within(rows, dists, max_dist):
        if max_dist is None:
            return list(rows)
        return [r for r, d in zip(rows, dists)
                if d is not None and d <= max_dist]

    # ---- 3. compare -----------------------------------------------------
    def compare(nrt_rows, sp, label, max_dist):   # noqa: C901
        nb, sb = bucketize(nrt_rows), bucketize(sp)
        shifts, frp_rel, frp_abs, time_shifts = [], [], [], []
        conf_changes = defaultdict(int)
        exact = only_nrt = only_sp = dateless = 0
        for key in set(nb) | set(sb):
            nn, ss = nb.get(key, []), sb.get(key, [])
            if not ss:
                dateless += len(nn)
            pairs, un, us = match_day(nn, ss, args.max_shift_km,
                                      args.time_tol_min)
            only_nrt += len(un)
            only_sp += len(us)
            for a, b, d in pairs:
                shifts.append(d * 1000.0)   # metres
                if a['lat'] == b['lat'] and a['lon'] == b['lon']:
                    exact += 1
                if a['frp'] > 0:
                    frp_rel.append(abs(b['frp'] - a['frp']) / a['frp'])
                frp_abs.append(abs(b['frp'] - a['frp']))
                if a['conf'] != b['conf']:
                    conf_changes[f"{a['conf']}->{b['conf']}"] += 1
                time_shifts.append(abs(a['tmin'] - b['tmin']))

        matched = len(shifts)
        total = len(nrt_rows)
        if not total:
            return {'scope': label, 'sp_max_dist_km': max_dist,
                    'nrt_rows': 0, 'sp_rows': len(sp), 'matched': 0,
                    'matched_pct': None, 'drop_pct': None, 'add_pct': None,
                    'shift_m': {'median': None, 'over_pixel_pct': None},
                    'note': 'no DB rows in this scope'}
        return {
            'scope': label,
            'sp_max_dist_km': max_dist,
            'nrt_rows': total, 'sp_rows': len(sp),
            'matched': matched,
            'matched_pct': round(100.0 * matched / total, 2),
            'exact_coord_matches': exact,
            'exact_pct': round(100.0 * exact / matched, 2) if matched else None,
            'only_in_nrt': only_nrt,
            'only_in_sp': only_sp,
            'drop_pct': round(100.0 * only_nrt / total, 2),
            'add_pct': round(100.0 * only_sp / total, 2),
            'nrt_on_date_with_no_sp': dateless,
            'shift_m': {
                'median': round(pct(shifts, 50), 2),
                'p90': round(pct(shifts, 90), 2),
                'p99': round(pct(shifts, 99), 2),
                'max': round(max(shifts), 2) if shifts else None,
                'mean': round(float(np.mean(shifts)), 2) if shifts else None,
                'over_pixel_pct': round(
                    100.0 * sum(1 for x in shifts if x > VIIRS_PIXEL_M) / matched, 3)
                    if matched else None,
            },
            'acq_time_shift_min': {
                'median': round(pct(time_shifts, 50), 2),
                'p99': round(pct(time_shifts, 99), 2),
                'changed_pct': round(
                    100.0 * sum(1 for t in time_shifts if t) / matched, 2)
                    if matched else None,
            },
            'frp_abs_median': round(statistics.median(frp_abs), 3) if frp_abs else None,
            'frp_rel_median': round(statistics.median(frp_rel), 4) if frp_rel else None,
            'confidence_changes': dict(sorted(conf_changes.items(),
                                              key=lambda kv: -kv[1])[:10]),
            'confidence_change_pct': round(
                100.0 * sum(conf_changes.values()) / matched, 2) if matched else None,
        }

    # ---- scope: raw add/drop counts are NOT a drift signal --------------
    #
    # Our ingest scope changed over the years. The nightly job keeps every
    # Africa-bbox detection and merely annotates park_id; older backfills kept
    # only rows near a park (and pre-ParkAssigner, per-park bbox buffers, which
    # fit no single radius). FIRMS has no such notion, so an unfiltered SP
    # fetch "adds" tens of percent of rows that were simply never in scope.
    # That is an artefact of our ingest history, not an SP revision.
    #
    # The verdict therefore runs on a *symmetric* subset: both sides clipped to
    # the same distance-to-park with the same assigner. Inside the boundary
    # (0 km) every ingest era kept the rows, so that variant is the one that
    # can be trusted; the wider ones are context.
    def variant(max_dist, label):
        return compare(within(db_all, db_dist, max_dist),
                       within(sp_all, [r['dist_km'] for r in sp_all], max_dist),
                       label, max_dist)

    variants = []
    if assigner is not None:
        variants.append(variant(0.0, 'inside_park'))
        variants.append(variant(100.0, 'within_100km'))
    variants.append(variant(None, 'bbox_unfiltered'))

    # Prefer the tightest symmetric scope that has enough rows to say anything.
    best = next((v for v in variants if v['nrt_rows'] >= 200), variants[-1])
    res = {
        'window': [start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')],
        'bbox': args.bbox, 'satellite': sat, 'sp_source': source,
        'max_shift_km': args.max_shift_km, 'time_tol_min': args.time_tol_min,
        'nrt_provenance': None if prov is None else round(prov, 4),
        'verdict_scope': best['scope'],
        'chosen': best,
        'variants': variants,
    }

    if not args.watchdog:
        print()
        print(json.dumps(res, indent=2))
        print()

    # ---- 4. verdict ----------------------------------------------------
    med = best['shift_m']['median']
    over = best['shift_m']['over_pixel_pct'] or 0.0
    if med is None or not best['matched']:
        print("INCONCLUSIVE: no matched pairs in the verdict scope - widen "
              "--bbox, pick a busier fire week, or raise --max-shift-km.")
        if args.json_out:
            res['verdict'] = 'inconclusive'
            Path(args.json_out).write_text(json.dumps(res, indent=2))
        sys.exit(3)
    print(f"[verdict scope: {best['scope']} "
          f"({best['nrt_rows']:,} DB rows vs {best['sp_rows']:,} SP rows)]")
    print(f"median coordinate shift {med} m vs {VIIRS_PIXEL_M:.0f} m pixel; "
          f"{over}% of matches move more than a pixel")
    print(f"FRP median |delta| {best['frp_abs_median']}; "
          f"confidence revised on {best['confidence_change_pct']}% of matches")
    print(f"SP drops {best['drop_pct']}% of DB rows, "
          f"adds {best['add_pct']}% new ones")
    res['verdict'] = (
        'not_worth_building'
        if (med < VIIRS_PIXEL_M / 2 and over < 5.0
            and best['drop_pct'] < 5.0 and best['add_pct'] < 5.0)
        else 'material_drift')
    if res['verdict'] == 'not_worth_building':
        print("VERDICT: NO ACTION. Revisions are sub-pixel and the row sets "
              "agree; SP cannot move a trajectory (cluster eps is kilometres). "
              "This has been the answer since 2026-08 - see "
              "docs/FIRE_PIPELINE.md.")
    else:
        print("VERDICT: MATERIAL DRIFT. FIRMS processing has changed since the "
              "2026-08 measurement. Re-read the numbers above, then reconcile "
              "the affected span with --apply --yes and rebuild those parks.")

    # ---- 5. apply -------------------------------------------------------
    if args.apply:
        res['apply'] = apply_revisions(conn, assigner, sat, db_all, sp_all,
                                       args, commit=args.yes)

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(res, indent=2))
        log(f"wrote {args.json_out}")

    if not args.keep_scratch:
        conn.execute(f"DROP TABLE IF EXISTS {SCRATCH}")
        conn.commit()
    conn.close()

    # Exit 4 = material drift, so the nightly pipeline can raise it as a
    # failed step instead of burying it in the log.
    if args.watchdog and res['verdict'] != 'not_worth_building':
        sys.exit(4)


if __name__ == '__main__':
    main()
