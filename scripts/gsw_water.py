#!/usr/bin/env python3
"""JRC Global Surface Water occurrence -> park_waterbodies polygons, streamed.

Why this exists (docs/AOI_HANDOVER_2.md §4.2): the `gsw` unit was blocked on
"3 missing 10x10 degree occurrence tiles". Measured 2026-08-07, the download is
not needed at all -- the 2021 v1.4 tiles are public COGs on GCS and rasterio
reads them through /vsicurl with HTTP range requests: opening a tile is 0.9 s
and a 1-degree window is **0.55 s**, a few MB. The same trade Hansen made.

Vocabulary is deliberately the parks': `waterbody_type` is 'Inland perennial'
or 'Inland intermittent', the two values already in park_waterbodies (2,573
rows), so the KML/Locus exports, the popup and enhanced_narratives.go read an
AOI's water exactly like a park's rather than needing a second code path.

  occurrence >= PERENNIAL_PCT   water present most of 1984-2021 -> perennial
  INTERMITTENT_PCT .. that      seasonal / episodic            -> intermittent
  below INTERMITTENT_PCT        noise, cloud shadow, one wet year -> dropped

    python3 scripts/gsw_water.py --aoi XSA_Study_Area
    python3 scripts/gsw_water.py --park COD_Virunga --dry-run
"""

import argparse
import json
import math
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

# v1.4, 2021 edition (Pekel et al. 2016) — the release docs/MINING_DATA_SOURCES.md
# §4.4 verified. Tiles are 10x10 degrees, named by their TOP-LEFT corner.
BASE_URL = ("https://storage.googleapis.com/global-surface-water/downloads2021/"
            "occurrence/occurrence_{lon}_{lat}v1_4_2021.tif")

PERENNIAL_PCT = 75
INTERMITTENT_PCT = 25

# Smaller than one Landsat pixel would be a lie about a 30 m product; this is
# ~4 pixels, the same order as the parks' smallest stored waterbodies.
MIN_AREA_KM2 = 0.004

# 1 degree measured at ~0.55 s. Kept smaller than Hansen's 2 degrees because
# the vectorising, not the read, is the cost and water is denser than loss.
WINDOW_DEG = 1.0

PREFIX = "gsw_"


def tile_for(lon, lat):
    """GSW tile id parts for (lon, lat). Named by TOP-LEFT corner, extending
    10 degrees south and east: floor the longitude, ceil the latitude — the
    same off-by-one-tile trap Hansen has."""
    lon0 = int(math.floor(lon / 10.0) * 10)
    lat0 = int(math.ceil(lat / 10.0) * 10)
    return (f"{abs(lon0)}{'E' if lon0 >= 0 else 'W'}",
            f"{abs(lat0)}{'N' if lat0 >= 0 else 'S'}")


def windows_for_bbox(x0, y0, x1, y1, step=WINDOW_DEG):
    """Split the bbox into read-sized windows, each clipped to one tile."""
    out = []
    y = y0
    while y < y1:
        x = x0
        while x < x1:
            wx1, wy1 = min(x + step, x1), min(y + step, y1)
            tl_lon = math.floor(x / 10.0) * 10
            tl_lat = math.ceil((wy1 - 1e-9) / 10.0) * 10
            wx1 = min(wx1, tl_lon + 10)
            wy0 = max(y, tl_lat - 10)
            if wx1 > x and wy1 > wy0:
                out.append((tile_for(x + 1e-9, wy1 - 1e-9), x, wy0, wx1, wy1))
            x += step
        y += step
    return out


def _open(tile, cache_dir=None):
    """Open a tile, preferring a local copy. data/gsw/ holds occ_20E_10N.tif
    from an earlier partial download; using it when present is free, and
    nothing is ever written — downloading 17 MB to serve one 0.55 s window
    would be a worse trade than the read it replaces."""
    import rasterio
    lon, lat = tile
    local = (cache_dir or (BASE_DIR / "data" / "gsw")) / f"occ_{lon}_{lat}.tif"
    if local.exists():
        return rasterio.open(local)
    return rasterio.open("/vsicurl/" + BASE_URL.format(lon=lon, lat=lat))


def water_polygons(geom, x0, y0, x1, y1, log=print, deadline=None,
                   start_window=0, on_window=None):
    """Yield (waterbody_type, shapely polygon, area_km2) inside geom.

    Resumable by window index, like hansen_loss.loss_polygons: the caller
    passes start_window and gets a callback after each one.
    """
    import time
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
                    # An all-ocean tile is simply not published. A normal
                    # state for a coastal bbox, not a failure.
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
            if data.size and int(data.max()) >= INTERMITTENT_PCT:
                # 255 is GSW's no-data. Classify most-permanent first and
                # subtract, so a lake's perennial core is not also emitted as
                # an intermittent ring twice over.
                bands = [("Inland perennial",
                          (data >= PERENNIAL_PCT) & (data <= 100)),
                         ("Inland intermittent",
                          (data >= INTERMITTENT_PCT) & (data < PERENNIAL_PCT))]
                for wtype, mask in bands:
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
                        simp = clipped.simplify(0.0002, preserve_topology=True)
                        if simp.is_empty:
                            continue
                        yield wtype, simp, area
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
    """Write GSW waterbodies for one park or AOI into park_waterbodies.

    Scoped by the `gsw_` waterbody_id prefix, so it can only ever delete its
    own rows: a park that already has HydroLAKES-derived waterbodies keeps
    them, and re-running is idempotent (AGENTS.md: several writers, one table,
    disjoint prefixes).
    """
    from shapely.geometry import mapping

    prefix = f"{PREFIX}"
    if start_window == 0 and not dry_run:
        # Fresh scan: drop our own previous rows FIRST, so a scan that now
        # yields nothing cannot leave them immortal.
        conn.execute("DELETE FROM park_waterbodies WHERE park_id = ? AND "
                     "waterbody_id LIKE ?", (target_id, prefix + "%"))
        conn.commit()

    x0, y0, x1, y1 = bbox
    rows, written, per_type = [], 0, {}

    def flush():
        nonlocal rows, written
        if not rows or dry_run:
            rows = []
            return
        conn.executemany("""
            INSERT OR REPLACE INTO park_waterbodies
            (park_id, waterbody_id, name, waterbody_type, lat, lon, geojson)
            VALUES (?,?,?,?,?,?,?)""", rows)
        conn.commit()
        written += len(rows)
        rows = []

    def on_window(done, total, n):
        flush()
        if progress_cb:
            progress_cb(done, total, written)

    idx = 0
    for wtype, poly, area in water_polygons(geom, x0, y0, x1, y1, log=log,
                                            deadline=deadline,
                                            start_window=start_window,
                                            on_window=on_window):
        c = poly.centroid
        per_type[wtype] = per_type.get(wtype, 0) + 1
        # Coordinate-keyed rather than sequential: windows are processed across
        # slices and a counter restarting at 0 on resume would collide under
        # UNIQUE(park_id, waterbody_id) and silently drop rows.
        wid = f"{prefix}{c.y:.5f}_{c.x:.5f}"
        idx += 1
        rows.append((target_id, wid, "", wtype, round(c.y, 6), round(c.x, 6),
                     json.dumps(mapping(poly))))
        if len(rows) >= 2000:
            flush()
    flush()
    return written, per_type


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi")
    ap.add_argument("--park")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--minutes", type=float, default=60)
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

    n, per_type = ingest(conn, target, geom, geom.bounds,
                         deadline=time.time() + args.minutes * 60,
                         dry_run=args.dry_run,
                         progress_cb=lambda d, t, w: print(
                             f"  window {d}/{t}, {w:,} waterbodies", flush=True))
    print(f"{target}: {n:,} GSW waterbodies")
    for t in sorted(per_type):
        print(f"   {t}: {per_type[t]:,}")


if __name__ == "__main__":
    main()
