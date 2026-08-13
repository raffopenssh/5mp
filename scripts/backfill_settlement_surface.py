#!/usr/bin/env python3
"""Re-derive built-up SURFACE, EXTENT and MEASURED population, one area per run.

Migration 055 (db/migrations/055-settlement-surface-and-provenance.sql) did not
fix any number: it *labelled* the wrong ones. Every settlement row written
before 2026-08-13 carries the GHSL mask's footprint in `area_m2` (~24x the
surface actually built on it) and a 200 people/ha constant in `population_est`,
and now says so —

    area_source       = 'ghsl_mask_extent'
    population_source = 'legacy_density_200_per_ha'

— which srv/settlement_provenance.go then declines to serve as a population.
Those two labels are this script's work queue. It re-ingests an area's GHSL
tiles through the fixed `ghsl_tiles.polygons_in` (zonal sum of the fractional
BUILT_S raster for surface, zonal sum of GHS_POP for population, mask area kept
separately as `extent_m2`) and re-clusters through the canonical
`EventRebuilder.rebuild_settlements_for_park`, which also applies the diameter
cap (F3), the exact nearest-place index (F4), the real nearest-river query (F5)
and the settlement fire context (F6).

WHY ONE AREA PER RUN. 160 areas share 31 Mollweide tiles, but the cost is not
the download — it is vectorising a 1000 km tile (minutes) and holding SQLite's
single writer while tens of thousands of polygons land. So an area is the unit
of work, `--rotate N` does N of them, and the rebuilder's `on_batch` hook
commits and sleeps between batches so the live app and the cron jobs keep
getting write slots (AGENTS.md invariant 16).

WHY IT REPORTS UNFINISHED RATHER THAN ZERO. An area that previously had
polygons and now yields none is a *failure*, not a pristine park: the tile may
have 404'd, the geometry may be empty, rasterio may have thrown. Recording that
as success would freeze an emptied park as a finished one — the exact shape of
AGENTS.md invariant 1. Such a run leaves the state file untouched, so the next
rotation retries it, and exits non-zero.

    python3 scripts/backfill_settlement_surface.py --list
    python3 scripts/backfill_settlement_surface.py --park CAF_Chinko --dry-run
    python3 scripts/backfill_settlement_surface.py --park CAF_Chinko
    python3 scripts/backfill_settlement_surface.py --rotate 2      # cron

State: data/settlement_backfill_state.json (per area: when, tiles, polygons,
clusters, and the before/after numbers, so a regression is visible later).
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES = BASE_DIR / "data" / "keystones_with_boundaries.json"
STATE_FILE = BASE_DIR / "data" / "settlement_backfill_state.json"

# The two labels migration 055 wrote. A row still carrying either of them has
# not been through this script. Derived from the migration, never a count
# (invariant 2): the queue is a query, not a list.
LEGACY_AREA = "ghsl_mask_extent"
LEGACY_POP = "legacy_density_200_per_ha"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def fmt_count(n):
    """A SUM over no rows is NULL, and NULL here means "no settlements to ask
    about" -- which is not the number 0 (AGENTS.md invariant 12: an absent
    measurement must print the word)."""
    return "none" if n is None else f"{n:,}"


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE_FILE)


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    return conn


def targets(conn):
    """[(id, geometry, is_aoi)] for every area that has settlement rows or
    polygons, parks from the keystones file and AOIs from the aois table.

    An AOI is not a park (invariant 6) and the difference matters twice here:
    its geometry comes from a table rather than the JSON, and its polygons are
    keyed with ghsl_tiles.AOI_PREFIX, which aoi_clip.py protects from deletion.
    """
    from shapely.geometry import shape
    out = []
    for p in json.load(open(KEYSTONES)):
        if p.get("geometry"):
            out.append((p["id"], shape(p["geometry"]), False))
    for r in conn.execute("SELECT id, geometry FROM aois WHERE geometry IS NOT NULL"):
        out.append((r["id"], shape(json.loads(r["geometry"])), True))
    return out


def area_stats(conn, area_id):
    """What this area currently claims, so the run can be diffed afterwards.

    Scoped to rows with polygon_ids: the others are retired detector output
    (invariant 5) whose `area_m2` came from a different instrument entirely and
    has no extent beside it. Summing the two together made Chinko report 7.3
    km² of "surface" against 0.5 km² of "extent" — extent is never smaller
    than surface, and the impossible ordering was the tell that one number was
    two things added up.
    """
    row = conn.execute("""
        SELECT COUNT(*) n,
               COALESCE(SUM(area_m2), 0) area_m2,
               COALESCE(SUM(extent_m2), 0) extent_m2,
               SUM(CASE WHEN population_source IS NOT NULL
                         AND population_source != ?
                        THEN population_est END) pop_measured,
               SUM(CASE WHEN population_source = ? THEN 1 ELSE 0 END) legacy_pop,
               SUM(CASE WHEN area_source = ? THEN 1 ELSE 0 END) legacy_area
          FROM park_settlements WHERE park_id = ?
           AND polygon_ids IS NOT NULL AND polygon_ids != ''
    """, (LEGACY_POP, LEGACY_POP, LEGACY_AREA, area_id)).fetchone()
    detector = conn.execute(
        "SELECT COUNT(*) FROM park_settlements WHERE park_id = ? AND "
        "(polygon_ids IS NULL OR polygon_ids = '')", (area_id,)).fetchone()[0]
    polys = conn.execute("""
        SELECT COUNT(*) n,
               SUM(CASE WHEN json_extract(properties_json, '$.extent_m2')
                        IS NOT NULL THEN 1 ELSE 0 END) with_extent
          FROM feature_geometries
         WHERE feature_type = 'settlement' AND park_id = ?""",
        (area_id,)).fetchone()
    classes = {r[0] or "unclassified": r[1] for r in conn.execute(
        "SELECT classification, COUNT(*) FROM park_settlements "
        "WHERE park_id = ? AND polygon_ids IS NOT NULL AND polygon_ids != '' "
        "GROUP BY 1", (area_id,))}
    return {"clusters": row["n"], "area_m2": round(row["area_m2"], 1),
            "extent_m2": round(row["extent_m2"], 1),
            "population_measured": row["pop_measured"],
            "legacy_pop_rows": row["legacy_pop"],
            "legacy_area_rows": row["legacy_area"],
            "detector_rows": detector,
            "polygons": polys["n"], "polygons_with_extent": polys["with_extent"],
            "classes": classes}


def pending(conn, area_id, state=None):
    """True if this area still holds a legacy label, or was converted by an
    older version of the reader (ghsl_tiles.PIPELINE_VERSION).

    The second clause exists because two real bugs lived in the *reader*, not
    in the rasters: a label like `ghsl_GHS_POP_E2030_...` stayed byte-identical
    while the number under it changed by 12%. A pipeline whose output cannot be
    invalidated is a pipeline that can only be re-run by hand, from memory.
    """
    import ghsl_tiles
    if state is not None:
        st = state.get(area_id) or {}
        if st.get("pipeline") == ghsl_tiles.PIPELINE_VERSION:
            return False
        converted = conn.execute(
            "SELECT COUNT(*) FROM park_settlements WHERE park_id = ? AND "
            "area_source = 'ghsl_built_s_surface'", (area_id,)).fetchone()[0]
        if converted:
            # Converted, but not provably by THIS reader. Unknown is not clean:
            # a lost state file must make the work look undone, never done
            # (AGENTS.md invariant 12 -- unmeasured says so).
            return True
    r = conn.execute("""
        SELECT COUNT(*) FROM park_settlements
         WHERE park_id = ? AND (area_source = ? OR population_source = ?)""",
        (area_id, LEGACY_AREA, LEGACY_POP)).fetchone()[0]
    if r:
        return True
    # A park with polygons but no clusters at all is also unconverted: the
    # polygons carry no extent_m2 until they are re-ingested.
    return conn.execute("""
        SELECT COUNT(*) FROM feature_geometries
         WHERE feature_type = 'settlement' AND park_id = ?
           AND json_extract(properties_json, '$.extent_m2') IS NULL
         LIMIT 1""", (area_id,)).fetchone()[0] > 0


def queue(conn, state, limit=None):
    """Areas still to convert, stalest first, smallest first.

    Smallest first on purpose: the AOI is 74,904 polygons and four tiles, and
    the first runs are the ones most likely to expose a bug. Size is the
    polygon count already in the table, which is exactly the work to redo.
    """
    rows = []
    for aid, geom, is_aoi in targets(conn):
        if not pending(conn, aid, state):
            continue
        n = conn.execute("SELECT COUNT(*) FROM feature_geometries WHERE "
                         "feature_type='settlement' AND park_id=?",
                         (aid,)).fetchone()[0]
        m = conn.execute("SELECT COUNT(*) FROM park_settlements WHERE park_id=?",
                         (aid,)).fetchone()[0]
        if n == 0 and m == 0:
            continue          # never had GHSL data; nothing to re-derive
        rows.append({"id": aid, "geom": geom, "aoi": is_aoi,
                     "polygons": n, "clusters": m,
                     "done_at": state.get(aid, {}).get("at", "")})
    rows.sort(key=lambda r: (r["done_at"], r["polygons"]))
    return rows[:limit] if limit else rows


def reingest(conn, area_id, geom, is_aoi, sleep=0.0, dry=False):
    """Re-vectorise every tile covering this area. (polygons, tiles, stale).

    Ids become coordinate-keyed so the run is idempotent and so a shorter
    second run cannot leave the tail of a longer first one behind as orphans:
    the ids written are collected and everything else of this area's is
    deleted. Parks use ghsl_tiles.PARK_PREFIX, not AOI_PREFIX — see the comment
    there; using the AOI prefix on a park would make aoi_clip.py's copies
    undeletable and double-counted.
    """
    import ghsl_tiles
    prefix = ghsl_tiles.AOI_PREFIX if is_aoi else ghsl_tiles.PARK_PREFIX
    tiles = ghsl_tiles.tiles_for_geom(geom)
    log(f"  {len(tiles)} tile(s): {' '.join(tiles)}")
    if dry:
        return None, tiles, None
    seen, total = set(), 0
    for i, tile in enumerate(tiles, 1):
        t0 = time.time()
        n = ghsl_tiles.ingest_tile(conn, area_id, tile, geom, coord_ids=True,
                                   prefix=prefix, seen=seen, log=log)
        total += n
        log(f"  tile {i}/{len(tiles)} {tile}: {n:,} polygons "
            f"({time.time() - t0:.0f}s)")
        if sleep:
            time.sleep(sleep)
    # Delete AFTER the writes, not before: a park is live while this runs, and
    # an empty window between delete and insert is a park that briefly has no
    # settlements. Correct because the ids are deterministic, so a re-ingest
    # overwrites in place (INSERT OR REPLACE) and only genuinely-absent
    # polygons are removed.
    stale = 0
    if seen:
        keep = list(seen)
        cur = conn.execute(
            "SELECT feature_id FROM feature_geometries WHERE feature_type="
            "'settlement' AND park_id = ?", (area_id,))
        drop = [(r[0],) for r in cur if r[0] not in seen]
        if drop:
            conn.executemany("DELETE FROM feature_geometries WHERE "
                             "feature_type='settlement' AND feature_id = ?",
                             drop)
            conn.commit()
        stale = len(drop)
        del keep
    return total, tiles, stale


def recluster(conn, area_id, sleep=0.0):
    from rebuild_events_enhanced import EventRebuilder
    rb = EventRebuilder()
    try:
        def on_batch(n):
            log(f"    clustering: {n:,} settlements")
            if sleep:
                time.sleep(sleep)
        return rb.rebuild_settlements_for_park(area_id, on_batch=on_batch)
    finally:
        try:
            rb.conn.close()
        except Exception:
            pass


def convert(conn, t, sleep, dry):
    """One area, end to end. Returns (ok, summary dict)."""
    aid = t["id"]
    before = area_stats(conn, aid)
    log(f"{aid}: {before['polygons']:,} polygons, {before['clusters']:,} "
        f"clusters, {before['legacy_area_rows'] or 0} legacy-area rows"
        + (" [AOI]" if t["aoi"] else ""))
    polys, tiles, stale = reingest(conn, aid, t["geom"], t["aoi"],
                                   sleep=sleep, dry=dry)
    if dry:
        log(f"  [dry-run] would re-ingest {len(tiles)} tile(s) and re-cluster")
        return True, {"before": before, "dry": True, "tiles": tiles}
    if polys == 0 and before["polygons"] > 0:
        # Invariant 1: a filter that matched nothing must not read as an answer.
        log(f"  UNFINISHED: 0 polygons where {before['polygons']:,} existed; "
            f"leaving the old rows and the queue entry in place")
        return False, {"before": before, "polygons": 0,
                       "error": "reingest yielded nothing"}
    clusters = recluster(conn, aid, sleep=sleep)
    after = area_stats(conn, aid)
    if after["clusters"] == 0 and before["clusters"] > 0:
        log(f"  UNFINISHED: 0 clusters where {before['clusters']:,} existed")
        return False, {"before": before, "after": after,
                       "error": "recluster yielded nothing"}
    ratio = (after["extent_m2"] / after["area_m2"]) if after["area_m2"] else None
    log(f"  {aid}: {polys:,} polygons ({stale:,} stale removed), "
        f"{clusters:,} clusters"
        + (f", {after['detector_rows']:,} detector rows untouched"
           if after['detector_rows'] else ""))
    log(f"    surface {after['area_m2']/1e6:,.3f} km² (legacy area_m2 was "
        f"{before['area_m2']/1e6:,.3f} km², i.e. mask extent)")
    log(f"    extent  {after['extent_m2']/1e6:,.3f} km²"
        + (f" = {ratio:.1f}x the surface" if ratio else ""))
    log(f"    population measured: {fmt_count(after['population_measured'])} "
        f"(was {fmt_count(before['population_measured'])} measured, "
        f"{before['legacy_pop_rows'] or 0} rows on the density constant)")
    if after["classes"] != before["classes"]:
        log(f"    classes {before['classes']} -> {after['classes']}")
    return True, {"before": before, "after": after, "polygons": polys,
                  "stale_removed": stale, "clusters": clusters,
                  "tiles": tiles, "extent_over_surface": ratio}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--park", help="one area id (park or AOI)")
    ap.add_argument("--rotate", type=int, default=0,
                    help="convert N queued areas, stalest+smallest first")
    ap.add_argument("--list", action="store_true", help="show the queue")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float,
                    default=float(os.environ.get("BACKFILL_SLEEP", "0.5")),
                    help="seconds to yield the write lock between batches/tiles")
    a = ap.parse_args()

    conn = connect()
    state = load_state()

    if a.list:
        q = queue(conn, state)
        log(f"{len(q)} area(s) pending")
        for r in q:
            log(f"  {r['id']:<40} {r['polygons']:>7,} polygons "
                f"{r['clusters']:>6,} clusters"
                + (f"  last {r['done_at']}" if r["done_at"] else ""))
        done = [k for k in state if state[k].get("ok")]
        log(f"{len(done)} converted so far")
        return 0

    if a.park:
        from shapely.geometry import shape  # noqa: F401  (targets uses it)
        match = [t for t in targets(conn) if t[0] == a.park]
        if not match:
            log(f"no such area: {a.park}")
            return 2
        aid, geom, is_aoi = match[0]
        work = [{"id": aid, "geom": geom, "aoi": is_aoi,
                 "polygons": conn.execute(
                     "SELECT COUNT(*) FROM feature_geometries WHERE "
                     "feature_type='settlement' AND park_id=?",
                     (aid,)).fetchone()[0]}]
    else:
        work = queue(conn, state, limit=max(1, a.rotate))

    if not work:
        log("nothing pending")
        return 0

    failures = 0
    for t in work:
        try:
            ok, summary = convert(conn, t, a.sleep, a.dry_run)
        except Exception as ex:
            log(f"  {t['id']}: ERROR {type(ex).__name__}: {ex}")
            ok, summary = False, {"error": f"{type(ex).__name__}: {ex}"}
        if a.dry_run:
            continue
        entry = dict(summary)
        entry["ok"] = ok
        import ghsl_tiles
        entry["pipeline"] = ghsl_tiles.PIPELINE_VERSION
        entry["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry.pop("before", None) if ok else None
        if ok:
            state[t["id"]] = entry
            save_state(state)
        else:
            # Do NOT record a timestamp for a failed area: the state file is
            # the rotation's memory, and a failure that stamps itself is a
            # failure that never gets retried.
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
