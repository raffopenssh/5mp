#!/usr/bin/env python3
"""Refresh the aoi_fires membership cache for an AOI.

Point-in-polygon over the AOI's bbox slice of fire_detections (3.7M rows for
XSA_Study_Area) takes seconds with shapely's vectorised contains_xy, but doing
it per query would not be. So it is materialised once and refreshed
incrementally: --since only re-tests detections added after a date, which is
what the nightly/aoi cron needs after a fire_gap slice lands.

This does NOT copy detections and does NOT touch protected_area_id — an AOI is
not a park (docs/PLAN_AOI_OVERLAY.md §3). aoi_fires holds ids only.

    python3 scripts/build_aoi_fires.py --aoi XSA_Study_Area
    python3 scripts/build_aoi_fires.py --aoi XSA_Study_Area --since 2026-08-01
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from shapely import contains_xy, prepare

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aoi_lib  # noqa: E402

CHUNK = 500_000


def build(aoi_id, since=None, rebuild=False, verbose=True):
    conn = aoi_lib.connect()
    row = aoi_lib.load_aoi(conn, aoi_id)
    geom = aoi_lib.aoi_geom(row)
    prepare(geom)
    x0, y0, x1, y1 = aoi_lib.aoi_bbox(row)

    if rebuild:
        conn.execute("DELETE FROM aoi_fires WHERE aoi_id = ?", (aoi_id,))
        conn.commit()

    sql = ("SELECT id, latitude, longitude FROM fire_detections "
           "WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?")
    params = [y0, y1, x0, x1]
    # The analysis window bounds membership too: an AOI's fire layers are
    # defined over its window, so rows outside it are not worth caching.
    lo = since or row["from_date"]
    if lo:
        sql += " AND acq_date >= ?"
        params.append(lo)
    if row["to_date"]:
        sql += " AND acq_date <= ?"
        params.append(row["to_date"])

    t0 = time.time()
    cur = conn.execute(sql, params)
    tested = inserted = 0
    while True:
        rows = cur.fetchmany(CHUNK)
        if not rows:
            break
        ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
        lat = np.fromiter((r[1] for r in rows), dtype=float, count=len(rows))
        lon = np.fromiter((r[2] for r in rows), dtype=float, count=len(rows))
        hit = contains_xy(geom, lon, lat)
        tested += len(rows)
        keep = ids[hit]
        if len(keep):
            conn.executemany(
                "INSERT OR IGNORE INTO aoi_fires (aoi_id, fire_id) VALUES (?,?)",
                ((aoi_id, int(i)) for i in keep))
            inserted += len(keep)
        conn.commit()
        if verbose:
            print(f"  tested {tested:,}  inside {inserted:,}  "
                  f"({time.time()-t0:.0f}s)", flush=True)

    total = conn.execute("SELECT COUNT(*) FROM aoi_fires WHERE aoi_id=?",
                         (aoi_id,)).fetchone()[0]
    if verbose:
        print(f"{aoi_id}: {tested:,} tested in bbox, {inserted:,} inside "
              f"this pass, {total:,} cached total ({time.time()-t0:.0f}s)")
    conn.close()
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True)
    ap.add_argument("--since", help="only test detections with acq_date >= this")
    ap.add_argument("--rebuild", action="store_true",
                    help="drop the cached membership first")
    args = ap.parse_args()
    build(args.aoi, args.since, args.rebuild)


if __name__ == "__main__":
    main()
