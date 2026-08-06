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


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- lease/queue

def lease_name():
    return f"{socket.gethostname()}:{os.getpid()}"


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
    conn.execute("""UPDATE aoi_datasets SET state='running', lease_owner=?,
                    lease_until=?, last_run_at=? WHERE aoi_id=? AND dataset=?""",
                 (lease_name(), until, now.isoformat(" ", "seconds"),
                  row["aoi_id"], row["dataset"]))
    conn.commit()
    return row


def release(conn, aoi_id, dataset, state, detail=None, retry_hours=None):
    nxt = None
    if retry_hours:
        nxt = (utcnow() + timedelta(hours=retry_hours)).isoformat(" ", "seconds")
    conn.execute("""UPDATE aoi_datasets SET state=?, lease_owner=NULL,
                    lease_until=NULL, detail=?, next_run_at=?
                    WHERE aoi_id=? AND dataset=?""",
                 (state, (detail or "")[:400], nxt, aoi_id, dataset))
    conn.commit()


def progress(conn, aoi_id, dataset, cursor, done, total, detail=None):
    """Commit after EVERY unit. This is the resumability guarantee."""
    cov = (done / total) if total else None
    conn.execute("""UPDATE aoi_datasets SET cursor=?, units_done=?, units_total=?,
                    coverage=?, detail=COALESCE(?, detail)
                    WHERE aoi_id=? AND dataset=?""",
                 (json.dumps(cursor) if cursor is not None else None,
                  done, total, cov, (detail or None), aoi_id, dataset))
    conn.commit()


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
               "alerts": 0}
    tiles, total = cur["tiles"], len(cur["tiles"])
    used = 0
    while cur["i"] < total and used < budget and time.time() < deadline:
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
    return cur["i"] >= total, f"{cur['alerts']:,} alerts, {cur['i']}/{total} tiles"


def run_osm(conn, aoi, ds, deadline, budget):
    """One country PBF per unit: download, extract the AOI bbox, fill the
    AOI's places/roads, then — while the PBF is on disk — backfill every park
    of that country that still has none, and delete it.

    That last clause is the point of §6: the AOI's download pays for park data
    too, and it is what keeps the enrichment mechanism alive now that the
    turbidity cron that used to call it is retired.
    """
    import osm_pbf

    cur = load_cursor(ds)
    if not cur:
        cur = {"i": 0, "countries": aoi_countries(conn, aoi)}
    countries, total = cur["countries"], len(cur["countries"])
    scope = f"aoi:{aoi['id']}"
    x0, y0, x1, y1 = aoi_lib.aoi_bbox(aoi)
    while cur["i"] < total and time.time() < deadline:
        iso = countries[cur["i"]]
        try:
            pbf, temporary = osm_pbf.ensure_pbf(iso)
            try:
                area = osm_pbf.extract_bbox(pbf, f"{scope}_{iso}", (x0, y0, x1, y1))
                try:
                    # force=False so several countries append rather than
                    # overwrite each other's rows for the same AOI scope.
                    osm_pbf.enrich_park_infra(area, scope)
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


def run_clip(conn, aoi, ds, deadline, budget):
    """Phase A preview. One unit, sub-second; aoi_clip writes its own
    aoi_datasets row (coverage = fraction of the polygon inside a park), which
    release() then confirms."""
    import aoi_clip
    stats = aoi_clip.run(aoi["id"])
    return True, ", ".join(f"{k} {v:,}" for k, v in stats.items() if v)


def run_basin(conn, aoi, ds, deadline, budget):
    sh(["python3", "scripts/fetch_park_basins.py", "--park", aoi["id"]],
       check=False)
    n = conn.execute("SELECT COUNT(*) FROM park_basins WHERE park_id=?",
                     (aoi["id"],)).fetchone()[0]
    return True, f"{n} basin rows"


RUNNERS = {
    "clip": run_clip,
    "fire_gap": run_fire_gap,
    "fire_v5": run_fire_v5,
    "gfw": run_gfw,
    "osm": run_osm,
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
    r = subprocess.run(cmd, cwd=str(BASE_DIR))
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd[1] if len(cmd) > 1 else cmd[0]} exit {r.returncode}")
    return r.returncode


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
    except Exception as ex:
        traceback.print_exc()
        release(conn, ds["aoi_id"], ds["dataset"], "pending",
                f"error: {ex}"[:400], retry_hours=24)
        return {"aoi": ds["aoi_id"], "dataset": ds["dataset"],
                "error": str(ex)[:200]}
    release(conn, ds["aoi_id"], ds["dataset"],
            "done" if finished else "pending", detail)
    log(f"   -> {'done' if finished else 'partial'}: {detail}")
    return {"aoi": ds["aoi_id"], "dataset": ds["dataset"],
            "state": "done" if finished else "partial", "detail": detail}


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
            if r.get("state") == "partial":
                break   # budget for this slice is spent
    except Exception as ex:
        fatal = ex
        traceback.print_exc()
    write_status(results, started, fatal)
    # One throttled notification per run, and only when something moved.
    moved = [r for r in results if r.get("state") in ("done", "partial")]
    if moved:
        notify_status("aoi_progress", "AOI ingest progress",
                      "; ".join(f"{r['aoi']}/{r['dataset']}: {r.get('detail','')}"
                                for r in moved)[:400])
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
    args = ap.parse_args()

    conn = aoi_lib.connect()
    if args.status:
        status(conn)
        return
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
