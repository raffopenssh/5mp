#!/usr/bin/env python3
"""Grind the AOI ingest queue (aoi_datasets), a slice at a time.

The product decision this implements (docs/PLAN_AOI_OVERLAY.md §4): a user
draws an area and the answer arrives over days. So there is no long-lived
process and no burst of quota — a dedicated midday cron takes a lease on one
dataset, works until a budget or a wall-clock deadline, commits its cursor
after every unit, and stops. Kill it at any moment and the next run resumes
where it stopped.

Deliberately its own cron, not a slice smuggled into the 03:00 fire job: that
job is time-critical and was until 2026-08-06 silently broken by a FIRMS window
cap, and the last thing it needs is a second consumer of its window and its
quota.

    python3 scripts/aoi_runner.py --daily
    python3 scripts/aoi_runner.py --aoi XSA_Study_Area --dataset fire_gap --budget 20
    python3 scripts/aoi_runner.py --status

Kill switch: UPDATE aoi_datasets SET enabled=0 WHERE aoi_id='...'
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

import aoi_lib  # noqa: E402
from cron_notify import notify_status  # noqa: E402

STATUS_FILE = BASE_DIR / "data" / "aoi_status.json"
LEASE_HOURS = 6
DEFAULT_DEADLINE_MIN = 90


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------ graceful stop
#
# Running out of time is the NORMAL exit of this program, not an error: a slice
# is meant to stop and let tomorrow's cron continue. Three things have to be
# true for "just resume tomorrow" to actually hold, and each one bit us on
# 2026-08-07:
#
#   1. a stop must not look like a crash    -> STOP flag + stopped_error()
#      (a crash parks the dataset for 24 h via retry_hours, so an operator
#       Ctrl-C used to cost a day)
#   2. a stop must release the lease         -> finally: in run_once
#   3. a lease whose process is gone must heal -> heal_leases()
#      (otherwise the row sits in 'running' for the full 6 h LEASE_HOURS and
#       the queue looks busy while nothing runs)

STOP = {"why": None}
_CHILD = {"proc": None}


class Interrupted(Exception):
    """Out of time / signalled. Not a failure: resume on the next slice."""


def stopping():
    return STOP["why"] is not None


def check_stop(deadline=None):
    """Raise Interrupted if we were signalled or the deadline passed.

    Runners call this between units. Everything already committed by
    progress() survives; the cursor is the resume point.
    """
    if deadline is not None and time.time() >= deadline and not stopping():
        STOP["why"] = "deadline"
    if stopping():
        raise Interrupted(STOP["why"])


def install_signal_handlers():
    import signal

    def handler(signum, _frame):
        name = signal.Signals(signum).name
        if stopping():          # second signal: the operator means it
            log(f"{name} again -- hard exit")
            os._exit(130)
        STOP["why"] = f"signal:{name}"
        log(f"{name} received -- finishing the current unit, then stopping")
        p = _CHILD["proc"]
        if p and p.poll() is None:
            # A step of a shelled-out chain (the v5 scripts) is itself
            # restartable from its own outputs, so passing the signal on is
            # safe and stops us waiting an hour for it.
            try:
                p.terminate()
            except Exception:
                pass

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, handler)
        except (ValueError, OSError):
            pass


def transient(ex):
    """Worth retrying in an hour rather than parking the dataset for a day.

    A locked database or a flaky download says nothing about whether the unit
    can ever succeed; a KeyError in a runner does.
    """
    s = str(ex).lower()
    return any(k in s for k in (
        "locked", "busy", "timed out", "timeout", "connection", "temporarily",
        "502", "503", "504", "read operation", "ssl", "broken pipe"))


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- lease/queue

def lease_name():
    return f"{socket.gethostname()}:{os.getpid()}"


def heal_leases(conn, blocking=True):
    """Reclaim leases whose process is provably gone.

    A crash between the last progress() and release() leaves the row in
    'running' with a lease valid for LEASE_HOURS. On this host we can do much
    better than waiting: lease_owner is 'hostname:pid', so if the hostname is
    ours and the pid is dead, the unit is dead. (A foreign hostname is left
    alone -- only the lease clock can judge that.)

    blocking=False for read-mostly callers like --status: another unit may hold
    the write lock for minutes and a status query must never hang on it.
    """
    import sqlite3
    me = socket.gethostname()
    healed = []
    if not blocking:
        # --status must not wait out a 60 s busy_timeout per stranded row.
        try:
            conn.execute("PRAGMA busy_timeout=250")
        except sqlite3.Error:
            pass
    for r in conn.execute("SELECT aoi_id, dataset, lease_owner FROM aoi_datasets "
                          "WHERE state='running' AND lease_owner IS NOT NULL"):
        host, _, pid = (r["lease_owner"] or "").rpartition(":")
        if host != me or not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            os.kill(int(pid), 0)
            continue            # still alive
        except ProcessLookupError:
            pass
        except PermissionError:
            continue            # exists, not ours
        healed.append((r["aoi_id"], r["dataset"]))
    done = []
    for aoi_id, dataset in healed:
        def go(a=aoi_id, d=dataset):
            conn.execute(
                "UPDATE aoi_datasets SET state='pending', lease_owner=NULL, "
                "lease_until=NULL WHERE aoi_id=? AND dataset=? AND state='running'",
                (a, d))
            conn.commit()
        try:
            retry_write(go, tries=8 if blocking else 1)
        except sqlite3.OperationalError:
            continue            # someone else holds the lock; next run heals it
        log(f"  healed stranded lease {aoi_id}/{dataset}")
        done.append((aoi_id, dataset))
    if not blocking:
        try:
            conn.execute("PRAGMA busy_timeout=60000")
        except sqlite3.Error:
            pass
    return done


def claim(conn, aoi_id=None, dataset=None):
    """Take a lease on the highest-priority runnable dataset, or None.

    Runnable = enabled, not done, dependency satisfied, next_run_at passed,
    and not leased by a live run. Leases expire after LEASE_HOURS so a killed
    process cannot wedge the queue.
    """
    now = utcnow()
    stale = (now - timedelta(hours=LEASE_HOURS)).isoformat(" ", "seconds")
    sql = """
        SELECT d.* FROM aoi_datasets d
        WHERE d.enabled = 1
          AND d.state IN ('pending', 'running')
          AND (d.next_run_at IS NULL OR d.next_run_at <= ?)
          AND (d.lease_until IS NULL OR d.lease_until <= ?)
          AND (d.depends_on IS NULL OR EXISTS (
                SELECT 1 FROM aoi_datasets p
                WHERE p.aoi_id = d.aoi_id AND p.dataset = d.depends_on
                  AND p.state = 'done'))
    """
    params = [now.isoformat(" ", "seconds"), stale]
    if aoi_id:
        sql += " AND d.aoi_id = ?"
        params.append(aoi_id)
    if dataset:
        sql += " AND d.dataset = ?"
        params.append(dataset)
    sql += " ORDER BY d.priority, d.dataset LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    if not row:
        return None
    until = (now + timedelta(hours=LEASE_HOURS)).isoformat(" ", "seconds")

    def go():
        conn.execute("""UPDATE aoi_datasets SET state='running', lease_owner=?,
                        lease_until=?, last_run_at=? WHERE aoi_id=? AND dataset=?""",
                     (lease_name(), until, now.isoformat(" ", "seconds"),
                      row["aoi_id"], row["dataset"]))
        conn.commit()
    # Taking the lease must not fail on a busy database either -- an unclaimed
    # claim would run the unit with nobody recorded as owning it.
    retry_write(go, total_wait=BOOKKEEPING_WAIT_S)
    return row


def retry_write(fn, tries=8, total_wait=None):
    """Run a write, waiting out SQLite's single write lock.

    Units run concurrently by design and the v5 fire chain holds the write lock
    for minutes, which is longer than any sane busy_timeout. Losing a
    bookkeeping write here is worse than waiting: `release()` failing is what
    strands a unit in 'running' until its lease expires.

    total_wait (seconds) overrides the try count for exactly that case -- a
    2026-08-07 run lost a completed 30-minute v5 step because the eight-try
    ladder tops out around four minutes and the other unit's transaction was
    longer than that.
    """
    import sqlite3
    deadline = time.time() + total_wait if total_wait else None
    attempt = 0
    while True:
        try:
            return fn()
        except sqlite3.OperationalError as ex:
            last = (time.time() >= deadline) if deadline else (attempt >= tries - 1)
            if ("locked" not in str(ex) and "busy" not in str(ex)) or last:
                raise
            time.sleep(min(30, 5 * 2 ** min(attempt, 4)))
            attempt += 1


# Bookkeeping must survive an arbitrarily long foreign write transaction:
# a lost release() strands the unit, a lost progress() redoes finished work.
BOOKKEEPING_WAIT_S = 45 * 60


def release(conn, aoi_id, dataset, state, detail=None, retry_hours=None):
    nxt = None
    if retry_hours:
        nxt = (utcnow() + timedelta(hours=retry_hours)).isoformat(" ", "seconds")
    def go():
        conn.execute("""UPDATE aoi_datasets SET state=?, lease_owner=NULL,
                        lease_until=NULL, detail=?, next_run_at=?
                        WHERE aoi_id=? AND dataset=?""",
                     (state, (detail or "")[:400], nxt, aoi_id, dataset))
        conn.commit()
    retry_write(go, total_wait=BOOKKEEPING_WAIT_S)


def progress(conn, aoi_id, dataset, cursor, done, total, detail=None):
    """Commit after EVERY unit. This is the resumability guarantee."""
    cov = (done / total) if total else None
    def go():
        conn.execute("""UPDATE aoi_datasets SET cursor=?, units_done=?, units_total=?,
                        coverage=?, detail=COALESCE(?, detail)
                        WHERE aoi_id=? AND dataset=?""",
                     (json.dumps(cursor) if cursor is not None else None,
                      done, total, cov, (detail or None), aoi_id, dataset))
        conn.commit()
    retry_write(go, total_wait=BOOKKEEPING_WAIT_S)


def load_cursor(row):
    try:
        return json.loads(row["cursor"]) if row["cursor"] else None
    except Exception:
        return None


# ------------------------------------------------------------------ datasets
#
# Each runner takes (conn, aoi_row, ds_row, deadline, budget) and returns
# (finished: bool, detail: str). They must be resumable and must call
# progress() after every unit.


def run_fire_gap(conn, aoi, ds, deadline, budget):
    """FIRMS backfill over the AOI bbox, in 5-day windows per sensor.

    Requests the WHOLE bbox rather than tiling the concave gap: INSERT OR
    IGNORE on UNIQUE(lat, lon, acq_date, acq_time, satellite) makes overlap
    with rows we already hold free, and one big rectangle is far fewer
    requests than chasing a polygon.

    The rows land in fire_detections with normal park assignment, so the four
    overlapped parks gain real detections in their outer buffers — rule 2:
    ingest is keyed by geography, not by owner.
    """
    import firms_api
    from park_assigner import ParkAssigner

    area = aoi_lib.firms_area(aoi)
    start = aoi["from_date"] or "2024-01-01"
    end = aoi["to_date"] or date.today().isoformat()

    cur = load_cursor(ds)
    if not cur:
        units = [[s.isoformat(), n, sensor]
                 for sensor in firms_api.SENSORS
                 for s, n in firms_api.day_windows(start, end)]
        cur = {"i": 0, "units": units, "rows": 0}
    units = cur["units"]
    total = len(units)

    assigner = ParkAssigner()
    wconn = aoi_lib.connect()
    done = cur["i"]
    used = 0
    while cur["i"] < total and used < budget and time.time() < deadline:
        if stopping():
            break   # cursor is committed; this dataset stays pending
        day, n, sensor = units[cur["i"]]
        src = firms_api.pick_source(sensor, day)
        if src is None:
            # No FIRMS product covers this date for this sensor (NOAA21 has no
            # SP archive and starts 2024-01-17). Not a failure: consume the
            # unit without spending a request or recording a hole.
            cur["i"] += 1
            done = cur["i"]
            progress(wconn, aoi["id"], ds["dataset"], cur, done, total)
            continue
        rows = firms_api.fetch(src, area, day, n, log=log)
        used += 1
        if rows is None:
            # A failed window is left in place and retried on the next slice
            # rather than skipped: a silent hole in a backfill is worse than
            # a slow one.
            cur["i"] += 1
            cur.setdefault("failed", []).append([day, n, sensor])
            progress(wconn, aoi["id"], ds["dataset"], cur, done, total)
            continue
        ins = insert_detections(wconn, rows, assigner)
        cur["rows"] += ins
        cur["i"] += 1
        done = cur["i"]
        progress(wconn, aoi["id"], ds["dataset"], cur, done, total,
                 detail=f"{cur['rows']:,} new detections")
    finished = cur["i"] >= total
    if finished and cur.get("failed"):
        # A failed window is retried on a later slice rather than skipped: a
        # silent hole in a backfill is worse than a slow one. Requeue them as
        # a fresh (smaller) unit list instead of declaring the dataset done.
        # retries counts passes, not windows, so a permanently-400ing window
        # cannot loop forever.
        if cur.get("retries", 0) < 3:
            log(f"  requeueing {len(cur['failed'])} failed windows "
                f"(pass {cur.get('retries', 0) + 1}/3)")
            cur = {"i": 0, "units": cur["failed"], "rows": cur["rows"],
                   "retries": cur.get("retries", 0) + 1}
            progress(wconn, aoi["id"], ds["dataset"], cur, 0, len(cur["units"]))
            finished = False
        else:
            log(f"  giving up on {len(cur['failed'])} windows after 3 passes")
    if finished:
        # Membership cache + animator aggregates must follow the new rows, or
        # the AOI sees fires the map does not (AGENTS.md).
        log("  refreshing aoi_fires membership")
        import build_aoi_fires
        build_aoi_fires.build(aoi["id"], verbose=False)
        log("  rebuilding fire grid aggregates")
        sh(["python3", "scripts/build_fire_grid_agg.py", "--since", start])
    wconn.close()
    return finished, f"{cur['rows']:,} detections, {cur['i']}/{total} windows"


def insert_detections(conn, rows, assigner):
    """Insert FIRMS rows with canonical single-park assignment.

    protected_area_id is a PARK or NULL — never the AOI. The AOI selects by
    polygon through aoi_fires (docs/PLAN_AOI_OVERLAY.md §3).
    """
    ins = 0
    cur = conn.cursor()
    for f in rows:
        try:
            lat, lon = float(f.get("latitude", 0)), float(f.get("longitude", 0))
        except (TypeError, ValueError):
            continue
        if lat == 0.0 or lon == 0.0:
            continue
        park_id, dist_km = assigner.assign(lon, lat)
        sat = f.get("satellite") or "unknown"   # part of the UNIQUE key
        try:
            cur.execute("""
                INSERT OR IGNORE INTO fire_detections
                (latitude, longitude, brightness, scan, track, acq_date, acq_time,
                 satellite, instrument, confidence, frp, daynight,
                 in_protected_area, protected_area_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (lat, lon, float(f.get("bright_ti4") or 0),
                 float(f.get("scan") or 0), float(f.get("track") or 0),
                 f.get("acq_date"), f.get("acq_time"), sat, "VIIRS",
                 f.get("confidence", ""), float(f.get("frp") or 0),
                 f.get("daynight", ""), 1 if dist_km == 0.0 else 0, park_id))
            ins += cur.rowcount
        except Exception:
            continue
    conn.commit()
    return ins


def run_fire_v5(conn, aoi, ds, deadline, budget):
    """The v5 chain, AOI-scoped. One unit; it is long but restartable."""
    aid = aoi["id"]
    steps = [
        ["python3", "scripts/build_aoi_fires.py", "--aoi", aid],
        ["python3", "scripts/rebuild_fire_trajectories_v5.py", "--aoi", aid],
        ["python3", "scripts/load_fire_groups_to_db.py", "--aoi", aid, "--force"],
        # Single writer of fire_narrative_cache (AGENTS.md) — always shelled
        # out to, never reimplemented here.
        ["python3", "scripts/precompute_narratives_v5.py", "--aoi", aid],
    ]
    cur = load_cursor(ds) or {"i": 0}
    while cur["i"] < len(steps):
        log("  " + " ".join(steps[cur["i"]]))
        sh(steps[cur["i"]])
        cur["i"] += 1
        progress(conn, aid, ds["dataset"], cur, cur["i"], len(steps))
    n = conn.execute("SELECT COUNT(*) FROM feature_geometries WHERE park_id=? "
                     "AND feature_type='fire_trajectory'", (aid,)).fetchone()[0]
    return True, f"{n:,} fire trajectories"


def run_gfw(conn, aoi, ds, deadline, budget):
    """GFW integrated alerts over the AOI, one 0.5-deg tile per unit.

    Writes through the per-tile cache (data/gfw_tiles/), so the ~250 tiles this
    fetches are immediately reusable by the park rotation and by any other AOI
    over the same ground — rule 2 again.
    """
    sys.path.insert(0, str(BASE_DIR / "analysis"))
    import gfw_alerts

    since = (aoi["from_date"] or (date.today() - timedelta(days=400)).isoformat())
    x0, y0, x1, y1 = aoi_lib.aoi_bbox(aoi)
    cur = load_cursor(ds)
    if not cur:
        cur = {"i": 0, "tiles": gfw_alerts.tiles_for_bbox(x0, y0, x1, y1, 0.5),
               "alerts": 0, "since": since}
    since = cur.get("since", since)   # a resumed scan keeps its cutoff, or the
                                      # cache keys stop matching mid-scan
    tiles, total = cur["tiles"], len(cur["tiles"])
    used = 0
    while cur["i"] < total and used < budget and time.time() < deadline:
        if stopping():
            break
        w, s, e, n = tiles[cur["i"]]
        try:
            rows = gfw_alerts.fetch_tile(w, s, e, n, since)
            cur["alerts"] += len(rows)
        except Exception as ex:
            cur.setdefault("failed", []).append([w, s, str(ex)[:80]])
        cur["i"] += 1
        used += 1
        progress(conn, aoi["id"], ds["dataset"], cur, cur["i"], total,
                 detail=f"{cur['alerts']:,} alerts")
    finished = cur["i"] >= total
    if finished:
        # Collate the scan file the deforestation step (and srv/turbidity.go's
        # GFW reader) expect. Re-reading every tile is cheap because they are
        # all cache hits by now -- and it is the only way to assemble a scan
        # that was fetched across several days of slices without holding
        # hundreds of thousands of alert rows in the cursor.
        alerts = []
        for (w, s, e, n) in tiles:
            try:
                alerts += gfw_alerts.fetch_tile(w, s, e, n, since)
            except Exception:
                continue
        write_gfw_scan(aoi, alerts, since, (x0, y0, x1, y1))
        log(f"  wrote data/gfw_alerts/{aoi['id']}.json ({len(alerts):,} alerts)")
    return finished, f"{cur['alerts']:,} alerts, {cur['i']}/{total} tiles"


def write_gfw_scan(aoi, alerts, since, bbox):
    """Same shape as analysis/gfw_alerts.py scan_park() writes for a park."""
    sys.path.insert(0, str(BASE_DIR / "analysis"))
    import gfw_alerts
    out = BASE_DIR / "data" / "gfw_alerts" / f"{aoi['id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "park_id": aoi["id"], "is_aoi": True,
        "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since": since, "buffer_km": 0,
        "bbox": [round(v, 3) for v in bbox],
        "n_alerts": len(alerts),
        "clusters": gfw_alerts.cluster(alerts),
    }
    tmp = out.with_suffix(".json.tmp")
    json.dump(payload, open(tmp, "w"))
    tmp.replace(out)


def run_deforestation(conn, aoi, ds, deadline, budget):
    """Deforestation events for the AOI, derived from the GFW alerts the `gfw`
    unit already fetched -- deliberately NOT a Hansen download.

    Hansen would be tens of GB of tiles for one polygon and stops at 2023;
    the integrated alerts are already on disk, already quality-gated, and are
    the same source the parks' own 2024+ events come from, so the AOI's
    numbers are comparable to its parks' instead of being a second method.

    One unit: the classifier is the canonical EventRebuilder, so an AOI event
    reads exactly like a park event.
    """
    sys.path.insert(0, str(BASE_DIR / "scripts"))
    import daily_park_refresh as dpr
    from rebuild_events_enhanced import EventRebuilder

    geom = aoi_lib.aoi_geom(aoi)
    rebuilder = EventRebuilder()
    try:
        n = dpr.ingest_gfw_deforestation(
            conn, rebuilder, aoi["id"],
            bbox=aoi_lib.aoi_bbox(aoi), clip_geom=geom)
    finally:
        try:
            rebuilder.conn.close()
        except Exception:
            pass
    progress(conn, aoi["id"], ds["dataset"], {"i": 1}, 1, 1)
    return True, f"{n:,} deforestation events from GFW alerts"


def run_ghsl(conn, aoi, ds, deadline, budget):
    """GHSL built-up surface -> settlement polygons + classified settlements.

    One 1000 km Mollweide tile per unit (4 for XSA). Tiles are cached by tile
    id under data/ghsl/tiles/, so a second AOI or a park onboarding over the
    same ground reuses the download (rule 2) -- and the same tiles finally make
    scripts/process_settlement_polygons.py work again, which had been pointing
    at a hardcoded zip that does not exist here.

    The last unit clusters the polygons into park_settlements through the
    *canonical* EventRebuilder, so an AOI settlement reads exactly like a park
    settlement instead of being a second classifier.
    """
    import ghsl_tiles

    aid = aoi["id"]
    geom = aoi_lib.aoi_geom(aoi)
    cur = load_cursor(ds)
    if not cur:
        cur = {"i": 0, "tiles": ghsl_tiles.tiles_for_geom(geom), "polys": 0}
        # Fresh scan: drop the previous run's rows before writing any, so a
        # scan that now yields nothing cannot leave them immortal.
        conn.execute("DELETE FROM feature_geometries WHERE park_id = ? AND "
                     "feature_type='settlement' AND feature_id LIKE ?",
                     (aid, ghsl_tiles.AOI_PREFIX + "%"))
        conn.commit()
    tiles, total = cur["tiles"], len(cur["tiles"])
    while cur["i"] < total and time.time() < deadline:
        if stopping():
            break
        cur["polys"] += ghsl_tiles.ingest_tile(
            conn, aid, tiles[cur["i"]], geom, coord_ids=True, log=log)
        cur["i"] += 1
        progress(conn, aid, ds["dataset"], cur, cur["i"], total,
                 detail=f"{cur['polys']:,} built-up polygons")
    if cur["i"] < total:
        return False, f"{cur['polys']:,} polygons, {cur['i']}/{total} tiles"

    from rebuild_events_enhanced import EventRebuilder
    # Hand over from the Phase A preview atomically: the clip's copies covered
    # only the ~10% of the polygon inside a park, and these tiles cover all of
    # it, so leaving both would double count. aoi_clip won't re-create them
    # (SUPERSEDED_BY) but it may not run again for a day.
    conn.execute("DELETE FROM feature_geometries WHERE park_id = ? AND "
                 "feature_type='settlement' AND feature_id NOT LIKE ?",
                 (aid, ghsl_tiles.AOI_PREFIX + "%"))
    conn.commit()
    rebuilder = EventRebuilder()
    try:
        def on_batch(n):
            progress(conn, aid, ds["dataset"], cur, cur["i"], total,
                     detail=f"{cur['polys']:,} built-up polygons, "
                            f"clustering: {n:,} settlements")
            check_stop(deadline)
        n = rebuilder.rebuild_settlements_for_park(aid, on_batch=on_batch)
    finally:
        try:
            rebuilder.conn.close()
        except Exception:
            pass
    return True, f"{cur['polys']:,} built-up polygons, {n:,} settlements"


def run_hansen(conn, aoi, ds, deadline, budget):
    """Hansen lossyear (<=2023) -> deforestation polygons + events, one 2-deg
    window per unit.

    Why this exists at all: GFW integrated alerts start in 2024, so without it
    an AOI has no deforestation history before then while every park it
    overlaps has 2001-2024 polygons. Tiles are public COGs read through
    /vsicurl -- no download, no quota, and therefore the one deforestation
    source that cannot silently return empty because a rate limit was hit
    (scripts/hansen_loss.py).

    Fifth writer of (park_id=<aoi>, feature_type) and safe only because it
    owns the disjoint `deforest_hansen_` prefix: it must never touch
    deforest_gfw_% (the alerts unit), plain deforest_% (the clip preview) or
    the settlement/fire prefixes. The events it clusters are scoped by the
    same prefix, so the alerts unit's >=2024 events survive a Hansen rerun and
    vice versa.
    """
    import hansen_loss

    aid = aoi["id"]
    geom = aoi_lib.aoi_geom(aoi)
    bbox = aoi_lib.aoi_bbox(aoi)
    cur = load_cursor(ds) or {"i": 0, "polys": 0}
    total = len(hansen_loss.windows_for_bbox(*bbox))

    def on_window(done, _t, written):
        # ingest() flushes before each callback, so the rows for windows
        # [start_window, cur['i']) are committed -- the cursor is a real
        # resume point, not an optimistic one.
        cur["i"] = start + done
        cur["polys"] = base + written
        progress(conn, aid, ds["dataset"], cur, cur["i"], total,
                 detail=f"{cur['polys']:,} loss polygons, "
                        f"{cur['i']}/{total} windows")
        check_stop(deadline)

    start, base = cur["i"], cur["polys"]
    try:
        hansen_loss.ingest(conn, aid, geom, bbox, log=log, deadline=deadline,
                           start_window=start, progress_cb=on_window)
    except Interrupted:
        raise
    if cur["i"] < total:
        return False, f"{cur['polys']:,} loss polygons, {cur['i']}/{total} windows"

    # Cluster + classify through the canonical EventRebuilder, prefix-scoped so
    # this only ever owns its own events (the same shape as the ghsl unit
    # handing settlement polygons to rebuild_settlements_for_park).
    #
    # on_batch is what keeps this from being a two-hour write lock: the
    # rebuilder commits every 200 events and calls back, so SQLite's single
    # writer is free between batches and Ctrl-C/SIGTERM/out-of-time is a normal
    # exit here too (a re-run re-derives the same clusters).
    from rebuild_events_enhanced import EventRebuilder
    rebuilder = EventRebuilder()
    try:
        def on_batch(n):
            progress(conn, aid, ds["dataset"], cur, cur["i"], total,
                     detail=f"{cur['polys']:,} loss polygons, clustering: "
                            f"{n:,} events")
            check_stop(deadline)
        n = rebuilder.rebuild_deforestation_for_park(
            aid, id_prefix=f"{hansen_loss.PREFIX}{aid}_", on_batch=on_batch)
    finally:
        try:
            rebuilder.conn.close()
        except Exception:
            pass
    return True, (f"{cur['polys']:,} loss polygons "
                  f"({hansen_loss.HANSEN_MIN_YEAR}-{hansen_loss.HANSEN_MAX_YEAR}), "
                  f"{n:,} events")


def run_osm(conn, aoi, ds, deadline, budget):
    """One country PBF per unit: download, extract the AOI bbox, fill the
    AOI's places/roads, then — while the PBF is on disk — backfill every park
    of that country that still has none, and delete it.

    That last clause is the point of §6: the AOI's download pays for park data
    too, and it is what keeps the enrichment mechanism alive now that the
    turbidity cron that used to call it is retired.

    ⚠️ It writes `osm_places`/`roads_heigit` under the **bare AOI id**, like
    every other unit (ghsl, hansen, gsw, hydro). It used to use an `aoi:<id>`
    scope key on the theory that AOI rows should stay out of the park id space
    — but that is already what aoiExcludeSQL() is for, and no read path knows
    about the prefix. The result (found 2026-08-07) was that the AOI's real OSM
    ingest — 12,956 roads and 432 placenames — was invisible to
    /infrastructure, /features, the narratives and the KML/Locus exports, all
    of which key on the bare id; the popup showed only the 141 roads the clip
    preview had copied from neighbouring parks. Nothing but a leak-prevention
    filter ever read the prefix, so there is no second consumer to keep happy.
    """
    import osm_pbf

    cur = load_cursor(ds)
    if not cur:
        cur = {"i": 0, "countries": aoi_countries(conn, aoi)}
    countries, total = cur["countries"], len(cur["countries"])
    scope = aoi["id"]
    x0, y0, x1, y1 = aoi_lib.aoi_bbox(aoi)
    while cur["i"] < total and time.time() < deadline:
        if stopping():
            break
        iso = countries[cur["i"]]
        try:
            pbf, temporary = osm_pbf.ensure_pbf(iso)
            try:
                area = osm_pbf.extract_bbox(pbf, f"{scope}_{iso}", (x0, y0, x1, y1))
                try:
                    # First country replaces (superseding the clip preview's
                    # copied park rows); the rest append, deduped by osm_id.
                    # Neither may use the default "skip if rows exist" mode:
                    # after the clip there are always rows, so the whole unit
                    # was a no-op end to end.
                    first = (cur["i"] == 0)
                    osm_pbf.enrich_park_infra(area, scope, replace=first,
                                              append=not first)
                finally:
                    try: os.remove(area)
                    except OSError: pass
                for p, _miss in osm_pbf.parks_missing_infra(iso):
                    pa = osm_pbf.extract_bbox(pbf, p["id"], osm_pbf.park_bbox(p, 50))
                    try:
                        osm_pbf.enrich_park_infra(pa, p["id"])
                    finally:
                        try: os.remove(pa)
                        except OSError: pass
            finally:
                if temporary:
                    try: os.remove(pbf)
                    except OSError: pass
        except Exception as ex:
            cur.setdefault("failed", []).append([iso, str(ex)[:80]])
        cur["i"] += 1
        progress(conn, aoi["id"], ds["dataset"], cur, cur["i"], total)
    return cur["i"] >= total, f"{cur['i']}/{total} countries"


def run_gsw(conn, aoi, ds, deadline, budget):
    """JRC Global Surface Water occurrence -> park_waterbodies, one 1-deg
    window per unit.

    This unit was "blocked: needs occurrence tiles we do not hold" until
    2026-08-07, when the tiles turned out not to need holding: they are public
    COGs and /vsicurl reads a 1-degree window in 0.55 s, exactly the trade
    Hansen made (scripts/gsw_water.py).

    Sixth writer into a park-shaped table for this AOI, and safe for the same
    reason as the rest: it owns the `gsw_` waterbody_id prefix and deletes
    nothing else.
    """
    import gsw_water

    aid = aoi["id"]
    geom = aoi_lib.aoi_geom(aoi)
    bbox = aoi_lib.aoi_bbox(aoi)
    cur = load_cursor(ds) or {"i": 0, "bodies": 0}
    total = len(gsw_water.windows_for_bbox(*bbox))
    start, base = cur["i"], cur["bodies"]

    def on_window(done, _t, written):
        # ingest() flushes before each callback, so everything up to cur['i']
        # is committed and the cursor is a real resume point.
        cur["i"] = start + done
        cur["bodies"] = base + written
        progress(conn, aid, ds["dataset"], cur, cur["i"], total,
                 detail=f"{cur['bodies']:,} waterbodies, {cur['i']}/{total} windows")
        check_stop(deadline)

    gsw_water.ingest(conn, aid, geom, bbox, log=log, deadline=deadline,
                     start_window=start, progress_cb=on_window)
    if cur["i"] < total:
        return False, f"{cur['bodies']:,} waterbodies, {cur['i']}/{total} windows"
    return True, f"{cur['bodies']:,} waterbodies (JRC GSW 1984-2021)"


def run_hydro(conn, aoi, ds, deadline, budget):
    """Rivers and lakes for the AOI, one country PBF per unit.

    HydroSHEDS was the intended source and cannot be used: data.hydrosheds.org
    serves a Cloudflare 403 to every unattended request (checked 2026-08-07).
    The handover's own stopgap -- OSM waterways from the PBF the `osm` unit
    already downloads -- is what runs here (scripts/osm_hydro.py), writing
    park_rivers_hydro/park_lakes_hydro under a negative-id key space so an
    imported HydroSHEDS row could never be clobbered if the download revives.

    Note this is NOT the `basin` unit: that one is mghydro/MERIT and answers
    "what drains through here". This one answers "what is this river called",
    which is what the narratives and the KML folder names need.
    """
    import osm_hydro

    aid = aoi["id"]
    cur = load_cursor(ds)
    if not cur:
        cur = {"i": 0, "countries": aoi_countries(conn, aoi),
               "rivers": 0, "lakes": 0}
    countries, total = cur["countries"], len(cur["countries"])
    bbox = aoi_lib.aoi_bbox(aoi)
    while cur["i"] < total and time.time() < deadline:
        if stopping():
            break
        iso = countries[cur["i"]]
        try:
            # replace only on the first country, or each would wipe the
            # previous one's rows for the same AOI.
            n_r, n_l = osm_hydro.ingest_country(
                conn, aid, iso, bbox, replace=(cur["i"] == 0), log=log)
            cur["rivers"] += n_r
            cur["lakes"] += n_l
        except Exception as ex:
            cur.setdefault("failed", []).append([iso, str(ex)[:80]])
        cur["i"] += 1
        progress(conn, aid, ds["dataset"], cur, cur["i"], total,
                 detail=f"{cur['rivers']:,} rivers, {cur['lakes']:,} lakes")
    return (cur["i"] >= total,
            f"{cur['rivers']:,} rivers, {cur['lakes']:,} lakes "
            f"({cur['i']}/{total} countries, OSM)")


def run_clip(conn, aoi, ds, deadline, budget):
    """Phase A preview. One unit, sub-second; aoi_clip writes its own
    aoi_datasets row (coverage = fraction of the polygon inside a park), which
    release() then confirms."""
    import aoi_clip
    stats = aoi_clip.run(aoi["id"])
    return True, ", ".join(f"{k} {v:,}" for k, v in stats.items() if v)


def run_basin(conn, aoi, ds, deadline, budget):
    """Contributing watersheds + downstream traces, via mghydro/MERIT.

    This used to pass `--park <aoi-id>`, and fetch_park_basins.py resolved ids
    only against keystones_with_boundaries.json -- which an AOI is deliberately
    never in. So the filter matched zero areas, the loop body never ran, the
    script exited 0, and this unit recorded **"0 basin rows" as a successful
    `done`**. rev 5 of the handover even wrote that down as correct ("a 485,000
    km2 polygon has no single watershed"); the true answer is that it has 22,
    totalling 162,392 km2. A no-op that looks like a plausible answer is the
    worst failure mode this queue has.

    `--aoi` reads the geometry from the `aois` table. Outlet budget scales with
    area, so a large AOI is allowed the outlets it actually drains by rather
    than a park-sized 3.

    Courtesy-paced (~5 s between calls on a $25/mo shared host), so a big AOI is
    minutes of mostly-sleep. Every response is cached in http_cache, so a re-run
    or an interrupted run costs nothing.
    """
    aid = aoi["id"]
    sh(["python3", "scripts/fetch_park_basins.py", "--aoi", aid], check=False)
    n = conn.execute("SELECT COUNT(*) FROM park_basins WHERE park_id=?",
                     (aid,)).fetchone()[0]
    parts = conn.execute("SELECT COUNT(*) FROM park_basin_parts WHERE park_id=?",
                         (aid,)).fetchone()[0]
    reaches = conn.execute("SELECT COUNT(*) FROM park_basin_rivers WHERE"
                           " park_id=?", (aid,)).fetchone()[0]
    # Zero is a legitimate state for a park on a drainage divide, but for an
    # area this size it means the fetch failed -- report it as unfinished so the
    # queue retries rather than freezing a wrong answer as `done`.
    ok = n > 0
    return ok, (f"{parts} watersheds ({n} merged rows, {reaches} upstream "
                f"reaches)" if ok else "no basin rows -- fetch failed")


RUNNERS = {
    "clip": run_clip,
    "fire_gap": run_fire_gap,
    "fire_v5": run_fire_v5,
    "gfw": run_gfw,
    "ghsl": run_ghsl,
    "deforestation": run_deforestation,
    "hansen": run_hansen,
    "osm": run_osm,
    "gsw": run_gsw,
    "hydro": run_hydro,
    "basin": run_basin,
}


def aoi_countries(conn, aoi):
    """ISO3 codes the AOI spans, from the parks it touches plus its bbox.

    Cheap and good enough: the parks inside/near the polygon carry the country
    prefix, and GADM lookup is not worth a dependency here.
    """
    import osm_pbf
    from shapely.geometry import box, shape
    geom = aoi_lib.aoi_geom(aoi)
    isos = set()
    with open(BASE_DIR / "data" / "keystones_with_boundaries.json") as f:
        for p in json.load(f):
            if not p.get("geometry"):
                continue
            try:
                if shape(p["geometry"]).intersects(geom):
                    isos.add(p["id"].split("_")[0])
            except Exception:
                continue
    del box
    return sorted(i for i in isos if i in osm_pbf.GEOFABRIK)


def sh(cmd, check=True):
    """Run a child step, remembering it so a signal can pass through.

    Without the handoff, SIGTERM during the 30-minute v5 rebuild would be
    noticed only when the child finally exited.
    """
    check_stop()
    p = subprocess.Popen(cmd, cwd=str(BASE_DIR))
    _CHILD["proc"] = p
    try:
        rc = p.wait()
    finally:
        _CHILD["proc"] = None
    if stopping():
        raise Interrupted(STOP["why"])
    if check and rc != 0:
        raise RuntimeError(f"{cmd[1] if len(cmd) > 1 else cmd[0]} exit {rc}")
    return rc


# --------------------------------------------------------------------- driver

def write_status(results, started, fatal=None):
    """Own heartbeat key, deliberately separate from the fire pipeline's:
    an AOI failure must never make the nightly fire cron look degraded."""
    try:
        payload = {
            "pipeline": "aoi_runner",
            "status": "failed" if fatal else
                      ("degraded" if any(r.get("error") for r in results) else "ok"),
            "started_at": started.isoformat(),
            "finished_at": datetime.now().isoformat(),
            "stopped_early": STOP["why"],
            "fatal_error": str(fatal)[:300] if fatal else None,
            "results": results,
        }
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".json.tmp")
        json.dump(payload, open(tmp, "w"), indent=2)
        tmp.replace(STATUS_FILE)
    except Exception as ex:
        log(f"heartbeat write failed: {ex}")


def run_once(conn, aoi_id=None, dataset=None, budget=120, deadline=None):
    heal_leases(conn)
    ds = claim(conn, aoi_id, dataset)
    if not ds:
        return None
    aoi = aoi_lib.load_aoi(conn, ds["aoi_id"])
    fn = RUNNERS.get(ds["dataset"])
    if not fn:
        release(conn, ds["aoi_id"], ds["dataset"], "blocked",
                "no runner implemented yet")
        return {"aoi": ds["aoi_id"], "dataset": ds["dataset"], "state": "blocked"}
    log(f"== {ds['aoi_id']}/{ds['dataset']} (budget {budget})")
    try:
        finished, detail = fn(conn, aoi, ds, deadline or (time.time() + 3600), budget)
    except Interrupted as ex:
        # The designed exit. Straight back to 'pending' with no cooldown: the
        # cursor is committed, so the next slice picks up mid-dataset.
        done, total = ds["units_done"] or 0, ds["units_total"] or 0
        detail = f"stopped ({ex}) at {done}/{total} -- resumes next run"
        log(f"   -> {detail}")
        release(conn, ds["aoi_id"], ds["dataset"], "pending", detail)
        return {"aoi": ds["aoi_id"], "dataset": ds["dataset"],
                "state": "stopped", "detail": detail}
    except Exception as ex:
        traceback.print_exc()
        # Transient (locked db, flaky download) says nothing about whether the
        # unit can succeed, so it must not cost a whole day.
        release(conn, ds["aoi_id"], ds["dataset"], "pending",
                f"error: {ex}"[:400], retry_hours=1 if transient(ex) else 24)
        return {"aoi": ds["aoi_id"], "dataset": ds["dataset"],
                "error": str(ex)[:200]}
    # A runner may also notice the stop itself and return cleanly mid-dataset
    # (the polite path -- it finishes the unit in hand first). Same outcome as
    # Interrupted: pending, no cooldown, and say so.
    if not finished and stopping():
        detail = f"stopped ({STOP['why']}) at {detail} -- resumes next run"
    release(conn, ds["aoi_id"], ds["dataset"],
            "done" if finished else "pending", detail)
    state = "done" if finished else ("stopped" if stopping() else "partial")
    log(f"   -> {state}: {detail}")
    return {"aoi": ds["aoi_id"], "dataset": ds["dataset"],
            "state": state, "detail": detail}


def daily(conn, minutes, budget):
    started = datetime.now()
    deadline = time.time() + minutes * 60
    results = []
    fatal = None
    try:
        while time.time() < deadline:
            r = run_once(conn, budget=budget, deadline=deadline)
            if r is None:
                log("queue empty")
                break
            results.append(r)
            if r.get("state") in ("partial", "stopped"):
                break   # budget or wall clock for this slice is spent
            if stopping():
                break
    except Exception as ex:
        fatal = ex
        traceback.print_exc()
    write_status(results, started, fatal)
    # One throttled notification per run, and only when something moved.
    #
    # Keyed by park_id = the AOI id, NOT the default 'SYSTEM': the message
    # names the AOI, and aoiNotifSQLFilter() decides visibility from park_id.
    # A 'SYSTEM' row therefore announced every private AOI's id and progress to
    # every principal -- the fact that someone is watching an area is as much
    # the secret as the polygon (docs/AOI_HANDOVER.md §2). One row per AOI
    # rather than one per run, for the same reason.
    moved = [r for r in results if r.get("state") in ("done", "partial", "stopped")]
    by_aoi = {}
    for r in moved:
        by_aoi.setdefault(r["aoi"], []).append(r)
    for aid, rs in by_aoi.items():
        notify_status("aoi_progress", "AOI ingest progress",
                      "; ".join(f"{r['dataset']}: {r.get('detail','')}"
                                for r in rs)[:400], park_id=aid)
    return results


def status(conn):
    for a in conn.execute("SELECT id, name, state FROM aois ORDER BY id"):
        print(f"{a['id']} ({a['state']})")
        for d in conn.execute(
                "SELECT * FROM aoi_datasets WHERE aoi_id=? ORDER BY priority",
                (a["id"],)):
            tot, done = d["units_total"] or 0, d["units_done"] or 0
            pct = f"{100*done/tot:5.1f}%" if tot else "    -"
            lease = f" leased:{d['lease_owner']}" if d["lease_owner"] else ""
            print(f"  {d['dataset']:<14} {d['state']:<8} {done:>5}/{tot:<5} {pct}"
                  f"  {(d['detail'] or '')[:50]}{lease}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true", help="cron mode")
    ap.add_argument("--aoi")
    ap.add_argument("--dataset")
    ap.add_argument("--budget", type=int, default=120,
                    help="max API-ish units per slice")
    ap.add_argument("--minutes", type=int, default=DEFAULT_DEADLINE_MIN)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--heal", action="store_true",
                    help="reclaim leases whose process is gone, then exit")
    args = ap.parse_args()

    conn = aoi_lib.connect()
    if args.status:
        heal_leases(conn, blocking=False)
        status(conn)
        return
    if args.heal:
        healed = heal_leases(conn)
        log(f"healed {len(healed)} stranded lease(s)")
        return
    install_signal_handlers()
    if args.daily:
        daily(conn, args.minutes, args.budget)
        return
    if not (args.aoi or args.dataset):
        ap.error("need --daily, --status, or --aoi/--dataset")
    r = run_once(conn, args.aoi, args.dataset, args.budget,
                 time.time() + args.minutes * 60)
    if r is None:
        log("nothing runnable (check enabled/depends_on/lease)")


if __name__ == "__main__":
    main()
