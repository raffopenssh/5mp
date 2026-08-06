#!/usr/bin/env python3
"""Populate park_settlements.direction_from_place.

The column has existed since the table was created but was never written, so
`GET /api/parks/{id}/settlement-narrative` emitted `"direction": ""` for all
9,933 named settlements. Fire narratives already say "184.2km SE ... near Mbari
(2.1km)"; settlements only said "20km from Yakamale". This closes that gap.

Bearing is measured FROM the named place TO the settlement, i.e. the direction
you would travel to reach the settlement -- matching the existing
formatPlaceWithDirection() wording used in the deforestation narratives.

Name matching: osm_places has duplicate names within a park, so we pick the
candidate whose distance to the settlement best agrees with the already-stored
distance_to_place_km (that value came from the original nearest-place join).

Idempotent. Safe to re-run; only writes rows whose direction is NULL/'' unless
--force.
"""
import argparse
import math
import sqlite3
import sys
from collections import defaultdict

DB = "db.sqlite3"
CARDINALS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def bearing(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing from point 1 to point 2, degrees."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def cardinal(deg):
    return CARDINALS[int((deg + 11.25) % 360 / 22.5)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--park", help="single park id")
    ap.add_argument("--force", action="store_true",
                    help="recompute even where a direction is already stored")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    where = ["nearest_place IS NOT NULL", "nearest_place <> ''"]
    params = []
    if not args.force:
        where.append("(direction_from_place IS NULL OR direction_from_place = '')")
    if args.park:
        where.append("park_id = ?")
        params.append(args.park)
    rows = con.execute(
        f"SELECT id, park_id, lat, lon, nearest_place, distance_to_place_km "
        f"FROM park_settlements WHERE {' AND '.join(where)}", params).fetchall()
    print(f"{len(rows)} settlements to process")
    if not rows:
        return

    parks = sorted({r["park_id"] for r in rows})
    places = defaultdict(list)
    q = ",".join("?" * len(parks))
    for p in con.execute(
            f"SELECT park_id, name, lat, lon FROM osm_places WHERE park_id IN ({q})",
            parks):
        places[(p["park_id"], p["name"])].append((p["lat"], p["lon"]))

    updates = []
    unmatched = 0
    for r in rows:
        cands = places.get((r["park_id"], r["nearest_place"]))
        if not cands:
            unmatched += 1
            continue
        stored = r["distance_to_place_km"] or 0.0
        # Prefer the same-named place whose distance matches what was recorded;
        # falls back to plain nearest when no distance was stored.
        best = min(cands, key=lambda c: abs(
            haversine(c[0], c[1], r["lat"], r["lon"]) - stored) if stored
            else haversine(c[0], c[1], r["lat"], r["lon"]))
        updates.append((cardinal(bearing(best[0], best[1], r["lat"], r["lon"])),
                        r["id"]))

    print(f"computed {len(updates)}, unmatched place name: {unmatched}")
    if args.dry_run:
        for d, i in updates[:10]:
            print(" ", i, d)
        return
    con.executemany(
        "UPDATE park_settlements SET direction_from_place = ? WHERE id = ?",
        updates)
    con.commit()
    print("committed")


if __name__ == "__main__":
    sys.exit(main())
