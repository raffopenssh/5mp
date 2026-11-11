#!/usr/bin/env python3
"""One-off: rewrite stored line geometry at 5 decimals, no separators.

roads_heigit was 3.4 GB, 2.6 GB of it GeoJSON text at 7 decimals with ", "
separators; HydroRIVERS reaches carried 15 decimals. `osm_pbf.compact_geom`
is what the writers use from 2026-11-11, so the nightly rotation produces
compact rows by itself -- this script just does the existing rows now instead
of over the next 17 days.

Yields: commits every --batch rows by primary key and sleeps briefly, so
SQLite's single writer stays available (AGENTS.md invariant 16). Resumable:
a compact row is skipped. The freed pages go to SQLite's freelist and are
reused by later inserts; the FILE only shrinks on VACUUM (needs free disk the
size of the database and blocks writers while it runs -- not done here).

    python3 scripts/compact_geojson.py --table roads_heigit
    python3 scripts/compact_geojson.py --table park_rivers_hydro
"""
import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from osm_pbf import DB_PATH, compact_geom  # noqa: E402

TABLES = {"roads_heigit": "id", "park_rivers_hydro": "id",
          "park_basin_rivers": "rowid", "park_lakes_hydro": "rowid"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", required=True, choices=sorted(TABLES))
    ap.add_argument("--batch", type=int, default=20000)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    key = TABLES[a.table]
    db = sqlite3.connect(DB_PATH, timeout=120)
    last = 0
    hi = db.execute(f"SELECT MAX({key}) FROM {a.table}").fetchone()[0] or 0
    seen = changed = before = after = 0
    while last < hi:
        rows = db.execute(f"SELECT {key}, geojson FROM {a.table} WHERE {key} > ? "
                          f"ORDER BY {key} LIMIT ?", (last, a.batch)).fetchall()
        if not rows:
            break
        ups = []
        for rid, gj in rows:
            last = rid
            seen += 1
            if not gj:
                continue
            try:
                out = compact_geom(json.loads(gj))
            except Exception:
                continue
            if len(out) < len(gj):
                before += len(gj)
                after += len(out)
                ups.append((out, rid))
        if ups and not a.dry_run:
            db.executemany(f"UPDATE {a.table} SET geojson = ? WHERE {key} = ?", ups)
            db.commit()
            time.sleep(0.2)
        changed += len(ups)
        print(f"{a.table}: {seen:,} rows, {changed:,} rewritten, "
              f"{before/1e6:,.0f} MB -> {after/1e6:,.0f} MB", file=sys.stderr)
    if seen == 0:
        sys.exit(f"{a.table}: no rows read -- nothing was compacted")


if __name__ == "__main__":
    main()
