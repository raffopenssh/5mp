#!/usr/bin/env python3
"""Rivers and lakes from an OSM country PBF -> park_rivers_hydro / park_lakes_hydro.

Why not HydroSHEDS (docs/AOI_HANDOVER.md §5 named it as the source): as of
2026-08-07 data.hydrosheds.org answers every request with a Cloudflare 403
challenge, browser UA or not, so HydroRIVERS_v10_af and HydroLAKES cannot be
fetched unattended at all. The handover already listed "PBF waterways from the
`osm` unit" as the stopgap; this is it, and it has two properties the download
does not: the PBF is already being fetched by the `osm` unit for the same
country, and OSM waterways are *named* far more often than HydroRIVERS reaches.

Why not mghydro either, since that is the hydrology API this repo already uses:
mghydro/MERIT-Hydro is our **watershed** source (`scripts/fetch_park_basins.py`,
the `basin` unit) and it stays that. It is the wrong instrument for this layer
for two measured reasons: it is outlet-anchored and an order of magnitude
sparser inside the area (CAF_Chinko: 417 MERIT reaches in `park_basin_rivers`
vs 3,510 HydroRIVERS reaches in `park_rivers_hydro`), and its reaches carry a
`comid` and a Strahler order but **no name** — while "Near <X> river" in the
narratives, the KML folder names and the settlement classifier all key off the
name. The two layers are complementary and both ship: MERIT answers "what
drains through here", OSM answers "what is this river called".

The rows land in the parks' own tables with the parks' own column meanings, so
`loadMergedRivers()`, the popup, the narratives and the KML/Locus exports read
an AOI's rivers exactly like a park's. Two conventions make that safe:

  * **ids are negated OSM ids.** hyriv_id/hylak_id are integers with a UNIQUE
    (park_id, id); HydroSHEDS ids are all positive (10.1M-11.5M for reaches,
    16-1.4M for lakes), so a negative id is provably ours and a delete scoped
    to `< 0` can never touch imported HydroSHEDS rows. Several writers, one
    table, disjoint key spaces — the same rule as the feature_id prefixes.
  * **stream_order is estimated, not invented.** OSM has no Strahler order, so
    we map the waterway tag to the band the parks' consumers filter on
    (river 4, canal 3, stream 2, ditch/drain 1) and say so in the name of the
    function. Anything reading `stream_order >= 4` therefore still gets
    "the big named ones" and nothing silently claims a hydrological order it
    did not compute.

    python3 scripts/osm_hydro.py --park CAF_Chinko
    python3 scripts/osm_hydro.py --aoi XSA_Study_Area --iso CAF
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

import osm_pbf  # noqa: E402

# waterway tag -> the stream_order band consumers filter on. NOT Strahler:
# OSM does not carry it. See the module docstring.
WATERWAY_ORDER = {"river": 4, "canal": 3, "stream": 2, "ditch": 1, "drain": 1}
WATERWAY_FILTER = ["w/waterway=river,canal,stream"]
# natural=water covers lakes, ponds, lagoons and reservoirs mapped as water.
LAKE_FILTER = ["w/natural=water", "w/landuse=reservoir", "r/natural=water"]

MIN_LAKE_KM2 = 0.01


def _shoelace_km2(ring):
    """Area of a lon/lat ring in km2, flat-earth at its own latitude. Good to
    a fraction of a percent at lake scale and avoids a pyproj dependency in
    the runner's hot path."""
    if len(ring) < 4:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx = 111.32 * math.cos(math.radians(lat0))
    ky = 110.57
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        s += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
    return abs(s) / 2.0


def _osm_int(fid):
    """'w123'/'r123'/'123' -> int. osmium's -u type_id prefixes the type; a
    relation and a way can share a number, so relations are offset into their
    own range rather than colliding under UNIQUE(park_id, id)."""
    s = str(fid)
    kind, digits = (s[0], s[1:]) if s and s[0].isalpha() else ("w", s)
    try:
        n = int(digits)
    except ValueError:
        return None
    return n + 10_000_000_000 if kind == "r" else n


def rivers_from_pbf(conn, target_id, area_pbf, replace=True, log=print):
    """Fill park_rivers_hydro from the waterways in an already-extracted PBF.

    One row per OSM way, geometry kept whole: rivers_merged.go chains touching
    segments itself, which is exactly what these are.
    """
    rows = []
    seen = set()
    for f in osm_pbf._export_filtered(area_pbf, f"{target_id}_ww",
                                      WATERWAY_FILTER, "linestring"):
        g = f.get("geometry") or {}
        if g.get("type") != "LineString":
            continue
        p = f["properties"]
        oid = _osm_int(f.get("id", ""))
        if oid is None or oid in seen:
            continue
        seen.add(oid)
        coords = g["coordinates"]
        lon, lat, length = osm_pbf._line_centroid_len(coords)
        rows.append((target_id, -oid, p.get("name") or None,
                     WATERWAY_ORDER.get(p.get("waterway"), 1),
                     0, round(length, 3), lat, lon, json.dumps(g)))

    if replace:
        # Scoped to our own negative id space: an imported HydroRIVERS row for
        # the same park survives. Deleted BEFORE the empty-input return so a
        # scan that now yields nothing cannot leave the old rows immortal.
        conn.execute("DELETE FROM park_rivers_hydro WHERE park_id = ? AND "
                     "hyriv_id < 0", (target_id,))
        conn.commit()
    if rows:
        conn.executemany("""INSERT OR REPLACE INTO park_rivers_hydro
            (park_id, hyriv_id, name, stream_order, ord_flow, length_km,
             lat, lon, geojson) VALUES (?,?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
    log(f"  osm rivers: {target_id} +{len(rows)}")
    return len(rows)


def lakes_from_pbf(conn, target_id, area_pbf, replace=True, log=print):
    """Fill park_lakes_hydro from natural=water / landuse=reservoir polygons."""
    rows = []
    seen = set()
    for f in osm_pbf._export_filtered(area_pbf, f"{target_id}_lk",
                                      LAKE_FILTER, "polygon"):
        g = f.get("geometry") or {}
        if g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        p = f["properties"]
        oid = _osm_int(f.get("id", ""))
        if oid is None or oid in seen:
            continue
        rings = ([g["coordinates"][0]] if g["type"] == "Polygon"
                 else [pl[0] for pl in g["coordinates"] if pl])
        area = sum(_shoelace_km2(r) for r in rings)
        if area < MIN_LAKE_KM2:
            continue
        seen.add(oid)
        pts = [pt for r in rings for pt in r]
        lon = sum(q[0] for q in pts) / len(pts)
        lat = sum(q[1] for q in pts) / len(pts)
        rows.append((target_id, -oid, p.get("name") or None, 1,
                     round(area, 4), lon, lat, json.dumps(g)))

    if replace:
        conn.execute("DELETE FROM park_lakes_hydro WHERE park_id = ? AND "
                     "hylak_id < 0", (target_id,))
        conn.commit()
    if rows:
        conn.executemany("""INSERT OR REPLACE INTO park_lakes_hydro
            (park_id, hylak_id, name, lake_type, area_km2, centroid_lon,
             centroid_lat, geojson) VALUES (?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
    log(f"  osm lakes: {target_id} +{len(rows)}")
    return len(rows)


def ingest_country(conn, target_id, iso, bbox, replace=True, log=print):
    """Download (or reuse) one country PBF, extract the bbox, fill both tables.

    replace=False for the second and later countries of a multi-country AOI,
    or each would wipe the previous one's rows.
    """
    pbf, temporary = osm_pbf.ensure_pbf(iso)
    key = f"{target_id}_{iso}".replace(":", "_").replace("/", "_")
    area = osm_pbf.extract_bbox(pbf, key, bbox)
    try:
        n_r = rivers_from_pbf(conn, target_id, area, replace=replace, log=log)
        n_l = lakes_from_pbf(conn, target_id, area, replace=replace, log=log)
    finally:
        for path, drop in ((area, True), (pbf, temporary)):
            if drop:
                try:
                    os.remove(path)
                except OSError:
                    pass
    return n_r, n_l


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi")
    ap.add_argument("--park")
    ap.add_argument("--iso", help="ISO3 override (AOIs span several)")
    ap.add_argument("--buffer-km", type=float, default=10.0)
    args = ap.parse_args()
    if not (args.aoi or args.park):
        ap.error("need --aoi or --park")

    import aoi_lib
    conn = aoi_lib.connect()
    if args.aoi:
        row = aoi_lib.load_aoi(conn, args.aoi)
        target, bbox = row["id"], aoi_lib.aoi_bbox(row)
        isos = [args.iso] if args.iso else []
    else:
        with open(BASE_DIR / "data" / "keystones_with_boundaries.json") as f:
            parks = {p["id"]: p for p in json.load(f)}
        p = parks[args.park]
        target = p["id"]
        bbox = osm_pbf.park_bbox(p, args.buffer_km)
        isos = [args.iso or target.split("_")[0]]
    if not isos:
        ap.error("--iso is required for an AOI")
    for i, iso in enumerate(isos):
        n_r, n_l = ingest_country(conn, target, iso, bbox, replace=(i == 0))
        print(f"{target}/{iso}: {n_r:,} rivers, {n_l:,} lakes")


if __name__ == "__main__":
    main()
