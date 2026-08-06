#!/usr/bin/env python3
"""Extract settlement polygons from GHSL built-up surface.

Rewritten 2026-08-07: this used to read a single hardcoded
`data/ghsl/ghsl_pop_2030.zip`, which does not exist on this machine, so the
GHSL step of park onboarding was silently skipped. Tiles are now fetched on
demand and cached per tile by `scripts/ghsl_tiles.py` — see the module docstring
there for why the cache is keyed by tile and not by the park that asked.

Writes feature_geometries rows of type 'settlement'. Turning those into
park_settlements clusters is a separate step (rebuild_events_enhanced.py, or
scripts/aoi_settlements.py for an AOI).

    python3 scripts/process_settlement_polygons.py --park CAF_Chinko
    python3 scripts/process_settlement_polygons.py            # all parks
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from shapely.geometry import shape

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))
import ghsl_tiles  # noqa: E402

DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_PATH = BASE_DIR / "data" / "keystones_with_boundaries.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--park', help="process a single park id; only that park's "
                    "settlement features are cleared+rebuilt (safe for onboarding)")
    args = ap.parse_args()

    print("=== GHSL Settlement Polygon Processor ===")
    print(f"Started: {datetime.now()}  product: {ghsl_tiles.PRODUCT}")

    keystones = json.load(open(KEYSTONES_PATH))
    if args.park:
        keystones = [p for p in keystones if p['id'] == args.park]
        if not keystones:
            print(f"ERROR: park {args.park} not found in keystones")
            return 1

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")

    total_features = total_parks = 0
    for i, park in enumerate(keystones):
        if not park.get('geometry'):
            continue
        pid = park['id']
        geom = shape(park['geometry'])
        # Delete before the loop, not after a successful vectorisation: a park
        # that now yields nothing must lose its old rows too.
        conn.execute("DELETE FROM feature_geometries WHERE feature_type='settlement' "
                     "AND park_id = ?", (pid,))
        conn.commit()
        n = 0
        try:
            for tile in ghsl_tiles.tiles_for_geom(geom):
                n += ghsl_tiles.ingest_tile(conn, pid, tile, geom, start_index=n)
        except Exception as ex:
            print(f"  {pid}: error - {ex}")
            continue
        if n:
            print(f"  [{i+1}/{len(keystones)}] {pid}: {n} polygons")
            total_features += n
            total_parks += 1

    conn.close()
    print(f"\n=== Complete: {total_parks} parks, {total_features} polygons ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
