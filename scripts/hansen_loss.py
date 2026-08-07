#!/usr/bin/env python3
"""Hansen Global Forest Change lossyear -> deforestation polygons, streamed.

Why this exists (and why docs/PLAN_AOI_OVERLAY.md §3a used to say the opposite):

The AOI's deforestation was derived from GFW integrated alerts alone, on the
grounds that Hansen would be "tens of GB of tiles for one polygon". Measured
2026-08-07, that is simply false:

  * a lossyear tile is 45-116 MB, not GB, and
  * it never has to be downloaded at all. The tiles are public COGs on GCS and
    rasterio reads them through /vsicurl with HTTP range requests: a 2x2 degree
    window over 10N_020E takes **0.6 s** and transfers a few MB.

And the cost of *not* using it is large and specific: GFW integrated alerts
only start in 2024 (`MIN_GFW_YEAR`), so an alerts-only AOI has no history at
all before then, while every park it overlaps has 2001-2024 Hansen polygons
(`deforest_{park}_{year}_{n}`, 221,277 rows). The AOI was the odd one out --
the *comparability* argument the old note made actually points the other way.

There is no API and no quota here, so this is also the one deforestation source
that cannot silently return an empty answer because a rate limit was hit.

Cutover, deliberately identical to the parks' so the numbers stay comparable:

    <= 2023   Hansen (this file)          feature_id  deforest_hansen_{id}_{year}_{n}
    >= 2024   GFW integrated alerts       feature_id  deforest_gfw_{id}_{year}_{lat}_{lon}

Hansen GFC-2024 does contain a 2024 band, but using it would double count
against the alerts, and the alerts are both more current and more precise about
*when*. So HANSEN_MAX_YEAR stops at 2023 and the two never overlap.

    python3 scripts/hansen_loss.py --aoi XSA_Study_Area
    python3 scripts/hansen_loss.py --park CAF_Chinko --dry-run
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

# GFC-2024 v1.12 is the release the parks' existing rows came from
# (scripts/download_hansen_tiles.py). Keep them on the same release or the
# AOI's polygons will not line up with its parks'.
RELEASE = "GFC-2024-v1.12"
BASE_URL = ("https://storage.googleapis.com/earthenginepartners-hansen/"
            f"{RELEASE}/Hansen_{RELEASE}_lossyear_{{tile}}.tif")

# Where the parks' Hansen history stops and the GFW alerts take over.
HANSEN_MIN_YEAR = 2001
HANSEN_MAX_YEAR = 2023

# Same threshold the park processor used, so polygon counts are comparable.
MIN_AREA_KM2 = 0.005

# Read in windows rather than whole tiles: a tile is 40000x40000 and the point
# of /vsicurl is to never materialise it. 2 degrees measured at ~0.6 s.
WINDOW_DEG = 2.0

PREFIX = "deforest_hansen_"


def tile_for(lon, lat):
    """Hansen tile id containing (lon, lat). Tiles are named by their TOP-LEFT
    corner and extend 10 degrees south and east, which is the off-by-one-tile
    trap in this dataset: floor for longitude, ceil for latitude."""
    lon0 = int(math.floor(lon / 10.0) * 10)
    lat0 = int(math.ceil(lat / 10.0) * 10)
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lon0 >= 0 else "W"
    return f"{abs(lat0):02d}{ns}_{abs(lon0):03d}{ew}"


def tiles_for_bbox(x0, y0, x1, y1):
    out = []
    lat = math.ceil(y1 / 10.0) * 10
    while lat - 10 < y1 and lat > y0:
        lon = math.floor(x0 / 10.0) * 10
        while lon < x1:
            out.append(tile_for(lon + 0.5, lat - 0.5))
            lon += 10
        lat -= 10
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def windows_for_bbox(x0, y0, x1, y1, step=WINDOW_DEG):
    """Split the bbox into read-sized windows, each tagged with its tile."""
    out = []
    y = y0
    while y < y1:
        x = x0
        while x < x1:
            wx1, wy1 = min(x + step, x1), min(y + step, y1)
            # A window must not straddle a tile edge: clip it to the tile that
            # contains its lower-left corner and let the next window take the
            # remainder.
            t = tile_for(x + 1e-6, wy1 - 1e-6)
            tl_lon = math.floor(x / 10.0) * 10
            tl_lat = math.ceil((wy1 - 1e-6) / 10.0) * 10
            wx1 = min(wx1, tl_lon + 10)
            wy0 = max(y, tl_lat - 10)
            if wx1 > x and wy1 > wy0:
                out.append((t, x, wy0, wx1, wy1))
            x += step
        y += step
    return out


def _open(tile, cache_dir=None):
    """Open a tile, preferring a local copy if one exists.

    data/hansen/ may already hold downloaded tiles from the original park run.
    Using them when present is free; otherwise stream. Nothing is written --
    downloading a 116 MB tile to serve one 0.6 s window would be a worse trade
    than the read it replaces.
    """
    import rasterio
    local = (cache_dir or (BASE_DIR / "data" / "hansen")) / f"lossyear_{tile}.tif"
    if local.exists():
        return rasterio.open(local)
    return rasterio.open("/vsicurl/" + BASE_URL.format(tile=tile))


def loss_polygons(geom, x0, y0, x1, y1, log=print, deadline=None,
                  start_window=0, on_window=None):
    """Yield (year, shapely polygon, area_km2) for loss inside geom.

    Resumable by window index: the caller passes start_window and gets a
    callback after each one, so an AOI spanning 15 windows can be spread over
    slices like every other unit.
    """
    import time
    import numpy as np
    import rasterio
    from rasterio.features import shapes
    from rasterio.windows import from_bounds
    from shapely.geometry import shape

    wins = windows_for_bbox(x0, y0, x1, y1)
    total = len(wins)
    open_tiles = {}
    try:
        for wi in range(start_window, total):
            if deadline and time.time() > deadline:
                return
            tile, wx0, wy0, wx1, wy1 = wins[wi]
            if tile not in open_tiles:
                try:
                    open_tiles[tile] = _open(tile)
                except rasterio.RasterioIOError as ex:
                    # An ocean tile simply does not exist upstream. That is a
                    # normal state for a coastal bbox, not a failure.
                    log(f"  tile {tile} unavailable ({str(ex)[:60]}) - skipping")
                    open_tiles[tile] = None
            src = open_tiles[tile]
            if src is None:
                if on_window:
                    on_window(wi + 1, total, 0)
                continue
            try:
                win = from_bounds(wx0, wy0, wx1, wy1, src.transform)
                data = src.read(1, window=win)
                transform = src.window_transform(win)
            except Exception as ex:
                log(f"  window {wi} read failed: {str(ex)[:80]}")
                if on_window:
                    on_window(wi + 1, total, 0)
                continue
            n = 0
            if data.size and int(data.max()) > 0:
                for code in range(HANSEN_MIN_YEAR - 2000, HANSEN_MAX_YEAR - 2000 + 1):
                    mask = (data == code)
                    if not mask.any():
                        continue
                    m8 = mask.astype("uint8")
                    for gj, val in shapes(m8, mask=mask, transform=transform):
                        if not val:
                            continue
                        poly = shape(gj)
                        try:
                            clipped = poly.intersection(geom)
                        except Exception:
                            continue
                        if clipped.is_empty:
                            continue
                        lat_c = (clipped.bounds[1] + clipped.bounds[3]) / 2
                        deg_km = 111.0 * math.cos(math.radians(lat_c))
                        area = clipped.area * (deg_km ** 2)
                        if area < MIN_AREA_KM2:
                            continue
                        simp = clipped.simplify(0.0001, preserve_topology=True)
                        if simp.is_empty:
                            continue
                        yield 2000 + code, simp, area
                        n += 1
                    del m8, mask
                del data
            if on_window:
                on_window(wi + 1, total, n)
    finally:
        for s in open_tiles.values():
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass


def ingest(conn, target_id, geom, bbox, log=print, deadline=None,
           start_window=0, dry_run=False, progress_cb=None):
    """Write Hansen loss polygons for one park or AOI into feature_geometries.

    Fifth writer of (park_id=<aoi>, feature_type) -- and like the other four it
    is only safe because it owns a disjoint feature_id prefix
    (deforest_hansen_). It must never touch deforest_gfw_% (the alerts unit),
    deforest_% without the hansen infix (the original park run), or the
    settlement/fire prefixes. See docs/PLAN_AOI_OVERLAY.md §4.
    """
    from shapely.geometry import mapping

    prefix = f"{PREFIX}{target_id}_"
    if start_window == 0 and not dry_run:
        # Fresh scan: drop our own previous rows first, so a scan that now
        # yields nothing cannot leave the old ones immortal (the empty-result
        # trap that bit load_fire_groups_to_db).
        conn.execute("DELETE FROM feature_geometries WHERE park_id = ? AND "
                     "feature_type='deforestation' AND feature_id LIKE ?",
                     (target_id, prefix + "%"))
        conn.commit()

    x0, y0, x1, y1 = bbox
    rows, written, per_year = [], 0, {}

    def flush():
        nonlocal rows, written
        if not rows or dry_run:
            rows = []
            return
        conn.executemany("""
            INSERT OR REPLACE INTO feature_geometries
            (feature_type, feature_id, park_id, geojson, start_date, end_date,
             properties_json, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy)
            VALUES ('deforestation',?,?,?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
        written += len(rows)
        rows = []

    def on_window(done, total, n):
        flush()
        if progress_cb:
            progress_cb(done, total, written)

    for year, poly, area in loss_polygons(geom, x0, y0, x1, y1, log=log,
                                          deadline=deadline,
                                          start_window=start_window,
                                          on_window=on_window):
        idx = per_year.get(year, 0)
        per_year[year] = idx + 1
        c = poly.centroid
        # Coordinate-free sequential index is fine here (unlike the AOI fire
        # groups) because the whole prefix is deleted and rewritten as one
        # scan; ids are not persisted anywhere outside this table.
        fid = f"{prefix}{year}_{idx}"
        b = poly.bounds
        rows.append((fid, target_id, json.dumps(mapping(poly)),
                     f"{year}-01-01", f"{year}-12-31",
                     json.dumps({"year": year, "area_km2": round(area, 4),
                                 "lat": round(c.y, 6), "lon": round(c.x, 6),
                                 "source": f"hansen_{RELEASE}"}),
                     b[0], b[1], b[2], b[3]))
        if len(rows) >= 2000:
            flush()
    flush()
    return written, per_year


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi")
    ap.add_argument("--park")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--no-events", action="store_true",
                    help="write polygons only, skip clustering into events")
    args = ap.parse_args()
    if not (args.aoi or args.park):
        ap.error("need --aoi or --park")

    import time
    import aoi_lib
    from shapely.geometry import shape

    conn = aoi_lib.connect()
    if args.aoi:
        row = aoi_lib.load_aoi(conn, args.aoi)
        target, geom = row["id"], shape(json.loads(row["geometry"]))
    else:
        with open(BASE_DIR / "data" / "keystones_with_boundaries.json") as f:
            parks = {p["id"]: p for p in json.load(f)}
        p = parks[args.park]
        target, geom = p["id"], shape(p["geometry"])

    n, per_year = ingest(conn, target, geom, geom.bounds,
                         deadline=time.time() + args.minutes * 60,
                         dry_run=args.dry_run,
                         progress_cb=lambda d, t, w: print(
                             f"  window {d}/{t}, {w:,} polygons", flush=True))
    print(f"{target}: {n:,} Hansen loss polygons "
          f"({HANSEN_MIN_YEAR}-{HANSEN_MAX_YEAR})")
    for y in sorted(per_year):
        print(f"   {y}: {per_year[y]:,}")

    if args.dry_run or args.no_events:
        return
    # Polygons on their own are invisible: the popup, the narratives and the
    # star report all read deforestation_events. Cluster + classify through the
    # canonical EventRebuilder -- prefix-scoped, so a Hansen rerun cannot touch
    # the GFW-alert unit's >=2024 events for the same park (AGENTS.md: several
    # writers, one table, disjoint prefixes).
    from rebuild_events_enhanced import EventRebuilder
    rebuilder = EventRebuilder()
    try:
        ev = rebuilder.rebuild_deforestation_for_park(
            target, id_prefix=f"{PREFIX}{target}_")
    finally:
        try:
            rebuilder.conn.close()
        except Exception:
            pass
    print(f"{target}: {ev:,} deforestation events from those polygons")


if __name__ == "__main__":
    main()
