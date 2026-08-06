#!/usr/bin/env python3
"""Phase A: make an AOI real *now* by clipping data we already hold.

The ingest queue (aoi_runner.py) answers an AOI properly, but it answers over
days. Meanwhile the parks the polygon overlaps already carry classified
settlements, deforestation events, rivers, roads and placenames for the part of
the AOI that lies inside them. Intersecting those with the polygon and the
window costs zero quota and about a second, and it is the difference between an
AOI that shows nothing for a week and one that is immediately useful.

It is a PREVIEW, and must be labelled as one: only 10.5% of XSA_Study_Area is
inside a park and 53.6% inside the 100 km ingest buffer, so a clipped layer is
a lower bound with a hard, arbitrary edge at the buffer boundary. The coverage
fraction is written to aoi_datasets.detail so the UI can say so.

Rows are copied, not referenced. Three reasons: the AOI window differs from the
park's, the park rows keep their own ids so nothing upstream changes, and the
copies are keyed by the AOI id so aoiExcludeSQL() already keeps them out of
every global query. Re-running deletes and rebuilds — idempotent by
construction, because the sources are re-classified by other crons.

Fire is deliberately NOT clipped: fire has its own membership path
(aoi_fires -> the --aoi v5 chain), which is exact rather than a preview.

    python3 scripts/aoi_clip.py --aoi XSA_Study_Area
    python3 scripts/aoi_clip.py --aoi XSA_Study_Area --dry-run
"""

import argparse
import json
import sys
import time
from pathlib import Path

from shapely import prepare
from shapely.geometry import shape

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))
import aoi_lib  # noqa: E402


def log(m):
    print(m, flush=True)


def intersecting_parks(conn, aoi_id, geom, persist=True):
    """Parks whose boundary meets the AOI, with both overlap fractions.

    frac_of_aoi is the honest part of the preview: it is exactly how much of
    the answer a clip can possibly contain. frac_of_park says whether the park
    is contained or merely clipped at the edge. Both are stored in aoi_parks
    (migration 041) because this is the AOI's most-used fact and a real
    polygon intersection is far too expensive to redo per request — the popup,
    the report and this script all read it back.
    """
    out = []
    with open(BASE_DIR / "data" / "keystones_with_boundaries.json") as f:
        parks = json.load(f)
    for p in parks:
        if not p.get("geometry"):
            continue
        try:
            g = shape(p["geometry"])
            if not g.intersects(geom):
                continue
            inter = g.intersection(geom).area
        except Exception:
            continue
        out.append((p["id"],
                    inter / geom.area if geom.area else 0.0,
                    inter / g.area if g.area else 0.0))
    out.sort(key=lambda t: -t[1])
    if persist:
        # Replace wholesale: a park that no longer intersects (edited polygon)
        # must disappear, not linger.
        conn.execute("DELETE FROM aoi_parks WHERE aoi_id = ?", (aoi_id,))
        conn.executemany(
            "INSERT INTO aoi_parks (aoi_id, park_id, frac_of_aoi, frac_of_park) "
            "VALUES (?,?,?,?)", [(aoi_id, a, b, c) for a, b, c in out])
        conn.commit()
    return out


def in_window(row_date, lo, hi):
    if not row_date:
        return True          # undated rows (most settlements) always pass
    if lo and row_date < lo:
        return False
    if hi and row_date > hi:
        return False
    return True


# Point-keyed tables: (table, columns to copy, date column or None).
# park_id is replaced by the AOI id; the primary key is dropped so SQLite
# assigns a fresh one.
POINT_TABLES = [
    ("park_settlements", None, None),
    ("deforestation_events", None, "year"),
    ("osm_places", None, None),
]
# Line tables have no lat/lon: they are clipped by intersecting the stored
# geojson with the polygon. park_rivers_hydro DOES carry a centroid so it goes
# through the point path (cheaper, and a reach stub is small enough that its
# centroid is a fair proxy); roads can be tens of km long, so a centroid test
# would drop every road that merely crosses the boundary.
GEOM_TABLES = [
    ("park_rivers_hydro", None, None),
]
LINE_TABLES = ["roads_heigit"]


def columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def clip_table(conn, aoi_id, table, geom, park_ids, lo, hi, dry=False):
    cols = columns(conn, table)
    if "lat" not in cols or "lon" not in cols:
        return 0, 0
    copy = [c for c in cols if c != "id"]
    date_col = "year" if "year" in cols else None
    src_sel = ", ".join(copy)
    marks = ",".join("?" for _ in park_ids)
    rows = conn.execute(
        f"SELECT {src_sel} FROM {table} WHERE park_id IN ({marks})",
        park_ids).fetchall()
    lat_i, lon_i = copy.index("lat"), copy.index("lon")
    pid_i = copy.index("park_id")
    lo_y = int(lo[:4]) if (lo and date_col == "year") else None
    hi_y = int(hi[:4]) if (hi and date_col == "year") else None

    keep = []
    for r in rows:
        vals = list(r)
        if not geom.contains(shape({"type": "Point",
                                    "coordinates": [vals[lon_i], vals[lat_i]]})):
            continue
        if date_col:
            y = vals[copy.index(date_col)]
            if (lo_y and y and y < lo_y) or (hi_y and y and y > hi_y):
                continue
        vals[pid_i] = aoi_id
        keep.append(vals)

    if dry:
        return len(rows), len(keep)
    conn.execute(f"DELETE FROM {table} WHERE park_id = ?", (aoi_id,))
    if keep:
        ph = ",".join("?" for _ in copy)
        conn.executemany(
            f"INSERT OR IGNORE INTO {table} ({src_sel}) VALUES ({ph})", keep)
    conn.commit()
    return len(rows), len(keep)


def clip_line_table(conn, aoi_id, table, geom, park_ids, lo, hi, dry=False):
    """Clip a geojson-only table by real geometric intersection.

    Kept whole rather than cut at the polygon edge: these are OSM ways with an
    osm_id and a length_km, and a truncated way with its original length_km
    would be a lie. A road that crosses the boundary is in.
    """
    cols = columns(conn, table)
    copy = [c for c in cols if c != "id"]
    gi, pid_i = copy.index("geojson"), copy.index("park_id")
    marks = ",".join("?" for _ in park_ids)
    rows = conn.execute(f"SELECT {', '.join(copy)} FROM {table} "
                        f"WHERE park_id IN ({marks})", park_ids).fetchall()
    keep = []
    for r in rows:
        vals = list(r)
        try:
            if not geom.intersects(shape(json.loads(vals[gi]))):
                continue
        except Exception:
            continue
        vals[pid_i] = aoi_id
        keep.append(vals)
    if dry:
        return len(rows), len(keep)
    conn.execute(f"DELETE FROM {table} WHERE park_id = ?", (aoi_id,))
    if keep:
        ph = ",".join("?" for _ in copy)
        conn.executemany(f"INSERT OR IGNORE INTO {table} ({', '.join(copy)}) "
                         f"VALUES ({ph})", keep)
    conn.commit()
    return len(rows), len(keep)


def clip_features(conn, aoi_id, geom, park_ids, lo, hi, dry=False):
    """feature_geometries rows of type settlement / deforestation.

    These are keyed 'settlement_<park>_<n>' / 'deforest_<park>_<year>_<n>' and
    the API resolves them by (park_id, feature_type) plus that id, so the copy
    only has to re-key park_id and keep feature_id unique under
    UNIQUE(feature_type, feature_id).

    The id becomes '<aoi>_<srcpark>' in place of '<park>':
    settlement_XSA_Study_Area_CAF_Chinko_0. Substituting the AOI id alone is
    not enough — the per-park counters restart at 0, so four parks' _0 rows
    would collide and INSERT OR IGNORE would silently drop 106 of 373. Keeping
    the source park in the id also makes the copy traceable and the re-key
    deterministic, so a re-run produces byte-identical ids. The 'settlement_'
    prefix is preserved because api.go keys a fallback lookup off it.

    (Do not be tempted by the 'settlement_' || s.id join in
    narrative_handlers.go: it matches zero rows in production. The real ids
    were never numeric.)
    """
    marks = ",".join("?" for _ in park_ids)
    x0, y0, x1, y1 = geom.bounds
    rows = conn.execute(f"""
        SELECT feature_type, feature_id, park_id, geojson, bbox_minx, bbox_miny,
               bbox_maxx, bbox_maxy, start_date, end_date, properties_json
        FROM feature_geometries
        WHERE feature_type IN ('settlement', 'deforestation')
          AND park_id IN ({marks})
          AND bbox_maxx >= ? AND bbox_minx <= ?
          AND bbox_maxy >= ? AND bbox_miny <= ?""",
        park_ids + [x0, x1, y0, y1]).fetchall()
    out = []
    for r in rows:
        if not in_window(r["start_date"], lo, hi):
            continue
        try:
            if not geom.intersects(shape(json.loads(r["geojson"]))):
                continue
        except Exception:
            continue
        out.append((r["feature_type"],
                    r["feature_id"].replace(r["park_id"],
                                            f'{aoi_id}_{r["park_id"]}', 1),
                    aoi_id, r["geojson"], r["bbox_minx"], r["bbox_miny"],
                    r["bbox_maxx"], r["bbox_maxy"], r["start_date"],
                    r["end_date"], r["properties_json"]))
    if dry:
        return len(rows), len(out)
    # Delete before insert and before any early return: a clip that now yields
    # nothing must not leave the previous run's rows immortal (AGENTS.md,
    # "a park with zero groups is a real state"). fire_trajectory is NOT
    # touched here — it belongs to the v5 chain.
    conn.execute("DELETE FROM feature_geometries WHERE park_id = ? AND "
                 "feature_type IN ('settlement', 'deforestation')", (aoi_id,))
    if out:
        conn.executemany("""
            INSERT OR IGNORE INTO feature_geometries
            (feature_type, feature_id, park_id, geojson, bbox_minx, bbox_miny,
             bbox_maxx, bbox_maxy, start_date, end_date, properties_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", out)
    conn.commit()
    return len(rows), len(out)


def run(aoi_id, dry=False):
    conn = aoi_lib.connect()
    aoi = aoi_lib.load_aoi(conn, aoi_id)
    geom = aoi_lib.aoi_geom(aoi)
    prepare(geom)
    lo, hi = aoi["from_date"], aoi["to_date"]

    parks = intersecting_parks(conn, aoi_id, geom, persist=not dry)
    if not parks:
        log("no intersecting parks — nothing to clip")
        return {}
    park_ids = [p for p, _, _ in parks]
    covered = sum(f for _, f, _ in parks)
    log(f"{len(parks)} intersecting parks, {100*covered:.1f}% of the polygon:")
    for p, f, fp in parks[:8]:
        log(f"  {p:<34} {100*f:5.1f}% of AOI, {100*fp:5.1f}% of park")

    t0 = time.time()
    stats = {}
    for table, _, _ in POINT_TABLES + GEOM_TABLES:
        src, kept = clip_table(conn, aoi_id, table, geom, park_ids, lo, hi, dry)
        stats[table] = kept
        log(f"  {table:<22} {kept:>7,} / {src:,}")
    for table in LINE_TABLES:
        src, kept = clip_line_table(conn, aoi_id, table, geom, park_ids, lo, hi, dry)
        stats[table] = kept
        log(f"  {table:<22} {kept:>7,} / {src:,}")
    src, kept = clip_features(conn, aoi_id, geom, park_ids, lo, hi, dry)
    stats["feature_geometries"] = kept
    log(f"  {'feature_geometries':<22} {kept:>7,} / {src:,}")
    log(f"done in {time.time()-t0:.1f}s{' (dry run)' if dry else ''}")

    if not dry:
        detail = (f"preview: {100*covered:.0f}% of polygon inside parks; " +
                  ", ".join(f"{k.replace('park_','').replace('_events','')} "
                            f"{v:,}" for k, v in stats.items() if v))
        conn.execute("""
            INSERT INTO aoi_datasets (aoi_id, dataset, priority, state,
                                      coverage, detail, units_done, units_total)
            VALUES (?, 'clip', 5, 'done', ?, ?, 1, 1)
            ON CONFLICT(aoi_id, dataset) DO UPDATE SET
              state='done', coverage=excluded.coverage, detail=excluded.detail,
              units_done=1, units_total=1,
              last_run_at=datetime('now')""", (aoi_id, covered, detail[:400]))
        conn.commit()
    conn.close()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.aoi, a.dry_run)


if __name__ == "__main__":
    main()
