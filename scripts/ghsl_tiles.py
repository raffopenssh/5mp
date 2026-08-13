#!/usr/bin/env python3
"""GHSL built-up-surface tiles: fetch once, share by tile (AOI rule 2).

`process_settlement_polygons.py` used to read one hardcoded
`data/ghsl/ghsl_pop_2030.zip` that no longer exists on this machine, so the
GHSL step of both park onboarding and the AOI queue was dead. This module
replaces it with the published R2023A 100 m tile grid, cached under
`data/ghsl/tiles/` **keyed by tile, not by the park or AOI that asked for it** —
the next consumer over the same ground pays nothing (docs/PLAN_AOI_OVERLAY.md
§0 rule 2).

The 10 m product is not published as tiles (404s); 100 m built-up surface is
what exists, and it is the same source the existing park settlement polygons
came from, so numbers stay comparable.

    python3 scripts/ghsl_tiles.py --list-for-park CAF_Chinko
    python3 scripts/ghsl_tiles.py --fetch R7_C20
"""

import io
import os
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
TILE_DIR = BASE_DIR / "data" / "ghsl" / "tiles"

# ⚠️ E2030 is a PROJECTION, not an observation. Every settlement figure derived
# from it is a modelled 2030 state, which is why the epoch travels with the row
# (`epoch` in properties_json, `population_source` in park_settlements) instead
# of being a constant only this file knows.
EPOCH = "E2030"
PRODUCT = f"GHS_BUILT_S_{EPOCH}_GLOBE_R2023A_54009_100_V1_0"
BASE_URL = ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
            "GHS_BUILT_S_GLOBE_R2023A/"
            f"GHS_BUILT_S_{EPOCH}_GLOBE_R2023A_54009_100/V1-0/tiles")

# GHS_POP: same release, same epoch, same 100 m Mollweide tile grid, so a tile
# id computed for BUILT_S addresses the identical ground in POP. Pixel values
# are absolute population counts, so the zonal SUM over a settlement's pixels
# is its population — no assumed density anywhere (F2).
POP_PRODUCT = f"GHS_POP_{EPOCH}_GLOBE_R2023A_54009_100_V1_0"
POP_BASE_URL = ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
                "GHS_POP_GLOBE_R2023A/"
                f"GHS_POP_{EPOCH}_GLOBE_R2023A_54009_100/V1-0/tiles")
POP_SOURCE = f"ghsl_{POP_PRODUCT}"

# GHSL Mollweide tile grid: 1000 km squares, origin at the top-left of R1_C1.
# Rows and columns are 1-INDEXED. Verified against the tile's own affine:
# R7_C20's transform origin is (959000, 3000000) = ORIGIN + (19, 6) tiles.
# Off-by-one here does not fail loudly -- it silently reads a window 2000 km
# away, or raises "Intersection is empty" if you are lucky.
TILE_SIZE = 1_000_000
ORIGIN_X = -18_041_000
ORIGIN_Y = 9_000_000

MOLLWEIDE = "ESRI:54009"
WGS84 = "EPSG:4326"

# m² of built-up surface per 100 m pixel below which the pixel is not a
# settlement. Same threshold the original script used.
PIXEL_THRESHOLD_M2 = 50
MIN_AREA_M2 = 5000


def to_mollweide(geom):
    import pyproj
    from shapely.ops import transform
    t = pyproj.Transformer.from_crs(WGS84, MOLLWEIDE, always_xy=True)
    return transform(lambda x, y: t.transform(x, y), geom)


def to_wgs84(geom):
    import pyproj
    from shapely.ops import transform
    t = pyproj.Transformer.from_crs(MOLLWEIDE, WGS84, always_xy=True)
    return transform(lambda x, y: t.transform(x, y), geom)


def tiles_for_geom(geom_wgs84):
    """['R7_C20', ...] covering a lon/lat geometry."""
    minx, miny, maxx, maxy = to_mollweide(geom_wgs84).bounds
    out = []
    for row in range(int((ORIGIN_Y - maxy) // TILE_SIZE) + 1,
                     int((ORIGIN_Y - miny) // TILE_SIZE) + 2):
        for col in range(int((minx - ORIGIN_X) // TILE_SIZE) + 1,
                         int((maxx - ORIGIN_X) // TILE_SIZE) + 2):
            out.append(f"R{row}_C{col}")
    return out


def tile_path(tile, product=PRODUCT):
    return TILE_DIR / f"{product}_{tile}.tif"


def ensure_tile(tile, log=print, product=PRODUCT, base_url=BASE_URL):
    """Local path of a tile, downloading it once. None if the tile is ocean
    (the grid is global but only land tiles are published — a 404 is a normal
    answer, not a failure).

    `product`/`base_url` select BUILT_S (default) or POP; both are cached in
    the same directory keyed by product+tile, so the two rasters of one tile
    are two files and neither can be mistaken for the other."""
    p = tile_path(tile, product)
    if p.exists() and p.stat().st_size > 0:
        return p
    TILE_DIR.mkdir(parents=True, exist_ok=True)
    url = f"{base_url}/{product}_{tile}.zip"
    log(f"  ghsl: downloading {tile} ({product.split('_GLOBE')[0]})")
    try:
        with urlopen(url, timeout=300) as r:
            blob = r.read()
    except Exception as ex:
        if "404" in str(ex):
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = [n for n in zf.namelist()
                 if n.endswith(".tif") and tile in n]
        if not names:
            return None
        tmp = p.with_suffix(".tif.tmp")
        with zf.open(names[0]) as src, open(tmp, "wb") as dst:
            dst.write(src.read())
        tmp.replace(p)
    return p


def _read_window(path, geom_moll):
    """(array, transform) for the geometry's bounds in one tile, or (None, None)."""
    import rasterio
    from rasterio.windows import Window, from_bounds

    minx, miny, maxx, maxy = geom_moll.bounds
    with rasterio.open(path) as src:
        try:
            window = from_bounds(minx, miny, maxx, maxy, src.transform)
            window = window.intersection(Window(0, 0, src.width, src.height))
        except Exception:
            # Includes rasterio's WindowError for a disjoint window: the tile
            # grid is computed from bounds, so a corner tile can legitimately
            # miss the geometry itself.
            return None, None
        if window.width < 1 or window.height < 1:
            return None, None
        return src.read(1, window=window), src.window_transform(window)


def polygons_in(tile, geom_wgs84, min_area_m2=MIN_AREA_M2, log=print,
                with_pop=True):
    """Built-up polygons of one tile inside a lon/lat geometry.

    Yields (shapely polygon in WGS84, stats dict) where stats carries:

      extent_m2   area of the vectorised MASK — the ground the cluster covers
      area_m2     SUM of the raster's own values inside it — built-up SURFACE
      population  SUM of GHS_POP inside it, or None if POP is unavailable

    ⚠️ The two areas are different quantities and must never share a name
    (AGENTS.md invariant 7). GHS_BUILT_S is a *fractional* surface raster: a
    100 m pixel holding 60 m² of building is one whole pixel of the mask. Until
    2026-08-13 this function reported the mask's area as `area_m2`, which over
    XSA_Study_Area was 6,798 km² of "built-up area" against 181 km² of actual
    surface — a 24x overstatement that then became the population estimate
    (docs/AOI_STRUCTURAL_FIXES.md F1/F2).

    Population is a zonal SUM of GHS_POP (absolute counts per pixel), not a
    density constant. If the POP tile cannot be fetched, `population` is None —
    an unmeasured quantity says so rather than being invented (invariant 1).
    """
    from rasterio.features import shapes
    from shapely.geometry import shape

    path = ensure_tile(tile, log=log)
    if path is None:
        return
    geom_moll = to_mollweide(geom_wgs84)
    data, transform = _read_window(path, geom_moll)
    if data is None:
        return

    pop = None
    if with_pop:
        try:
            pop_path = ensure_tile(tile, log=log, product=POP_PRODUCT,
                                   base_url=POP_BASE_URL)
        except Exception as ex:
            log(f"  ghsl: POP tile {tile} unavailable ({ex}); "
                f"population will be null")
            pop_path = None
        if pop_path is not None:
            pop, pop_transform = _read_window(pop_path, geom_moll)
            # Same grid, same window bounds => same shape. If it is not, the
            # rasters are not aligned and a zonal sum would be nonsense: say
            # nothing rather than something wrong.
            if pop is not None and pop.shape != data.shape:
                log(f"  ghsl: POP window {pop.shape} != BUILT_S {data.shape} "
                    f"for {tile}; population will be null")
                pop = None

    binary = (data > PIXEL_THRESHOLD_M2).astype(np.uint8)
    if binary.sum() == 0:
        return
    surface = np.where(binary > 0, data, 0).astype(np.float64)
    popvals = None if pop is None else np.where(binary > 0, pop, 0).astype(np.float64)

    # `shapes` labels connected components; re-rasterising each polygon to sum
    # its pixels would be O(polygons x window). Instead label once and sum by
    # label, then match a polygon to its label through the pixel under its
    # representative point. Cheaper and exact.
    from rasterio.features import geometry_window, rasterize
    from rasterio.windows import Window

    for geom_dict, value in shapes(binary, mask=binary > 0, transform=transform):
        if not value:
            continue
        poly = shape(geom_dict)
        try:
            clipped = poly.intersection(geom_moll)
        except Exception:
            continue
        if clipped.is_empty or clipped.area < min_area_m2:
            continue
        # Zonal sums over the CLIPPED polygon, in the raster's own window.
        try:
            w = geometry_window(_FakeDS(data.shape, transform), [clipped])
            w = w.intersection(Window(0, 0, data.shape[1], data.shape[0]))
            r0, r1 = int(w.row_off), int(w.row_off + w.height)
            c0, c1 = int(w.col_off), int(w.col_off + w.width)
            if r1 <= r0 or c1 <= c0:
                continue
            sub_transform = _window_transform(transform, r0, c0)
            mask = rasterize([(clipped, 1)], out_shape=(r1 - r0, c1 - c0),
                             transform=sub_transform, fill=0, dtype="uint8",
                             all_touched=False)
            if mask.sum() == 0:
                # A polygon thinner than a pixel centre: fall back to the whole
                # component's pixels rather than reporting zero surface.
                mask = binary[r0:r1, c0:c1]
            area_m2 = float((surface[r0:r1, c0:c1] * mask).sum())
            population = (None if popvals is None
                          else float((popvals[r0:r1, c0:c1] * mask).sum()))
        except Exception:
            continue
        simplified = to_wgs84(clipped).simplify(0.0001, preserve_topology=True)
        if simplified.is_empty:
            continue
        yield simplified, {"extent_m2": clipped.area,
                           "area_m2": area_m2,
                           "population": population}


class _FakeDS:
    """Minimal dataset shim so rasterio.features.geometry_window can be used on
    an in-memory array + transform (it only reads .transform/.height/.width)."""

    def __init__(self, shape, transform):
        self.height, self.width = shape
        self.transform = transform


def _window_transform(transform, row_off, col_off):
    from rasterio.transform import Affine
    return transform * Affine.translation(col_off, row_off)


# Feature ids. A park is rebuilt in one pass, so a per-park counter is fine and
# is what every existing row uses (park_settlements.polygon_ids references
# them) — do not change it. An AOI is built one tile per queue unit across
# days, where a counter would renumber on resume, so its ids are keyed by
# coordinate: deterministic, and re-running a tile is a no-op. The distinct
# 'settlement_ghsl_' prefix also keeps these rows out of aoi_clip.py's delete
# (the fourth writer of (park_id=<aoi>, feature_type), see its DELETE_EXCLUDE).
AOI_PREFIX = "settlement_ghsl_"


def write_rows(conn, sql, rows, tries=8):
    """executemany that waits out a long-running writer.

    The AOI queue runs several units concurrently by design, and the v5 fire
    chain holds SQLite's single write lock for minutes at a time. A 60 s
    busy_timeout is not enough: a GHSL tile is a 40k-row insert that arrives
    right in the middle of it, and losing it costs a 4-minute vectorise. Back
    off and retry instead of failing the unit.
    """
    import sqlite3
    import time as _time
    for attempt in range(tries):
        try:
            conn.executemany(sql, rows)
            conn.commit()
            return
        except sqlite3.OperationalError as ex:
            if "locked" not in str(ex) and "busy" not in str(ex):
                raise
            if attempt == tries - 1:
                raise
            _time.sleep(min(60, 5 * 2 ** attempt))


def ingest_tile(conn, target_id, tile, geom_wgs84, coord_ids=False,
                start_index=0, log=print):
    """Vectorise one tile into feature_geometries. Returns rows written."""
    import json
    from shapely.geometry import mapping

    n = 0
    rows = []
    for poly, st in polygons_in(tile, geom_wgs84, log=log):
        c = poly.centroid
        if coord_ids:
            fid = f"{AOI_PREFIX}{target_id}_{c.y:.5f}_{c.x:.5f}"
        else:
            fid = f"settlement_{target_id}_{start_index + n}"
        # `area_m2` is built-up SURFACE (the raster's own values); `extent_m2`
        # is the footprint the mask covers. Both are wanted -- extent to draw,
        # surface to count -- and they differ by ~24x, so they must not share a
        # name (AGENTS.md invariant 7; docs/AOI_STRUCTURAL_FIXES.md F1).
        # `population_est` is a GHS_POP zonal sum, or ABSENT when POP could not
        # be read: no constant-density fallback, because an unmeasured
        # population must say so rather than be invented (invariant 1 / F2).
        props = {"area_m2": round(st["area_m2"], 2),
                 "extent_m2": round(st["extent_m2"], 2),
                 "lat": round(c.y, 6), "lon": round(c.x, 6),
                 "source": f"ghsl_{PRODUCT}", "epoch": EPOCH, "tile": tile}
        if st["population"] is not None:
            props["population_est"] = int(round(st["population"]))
            props["population_source"] = POP_SOURCE
        b = poly.bounds
        rows.append(('settlement', fid, target_id, json.dumps(mapping(poly)),
                     json.dumps(props), b[0], b[1], b[2], b[3]))
        n += 1
    if rows:
        write_rows(conn, """
            INSERT OR REPLACE INTO feature_geometries
            (feature_type, feature_id, park_id, geojson, properties_json,
             bbox_minx, bbox_miny, bbox_maxx, bbox_maxy)
            VALUES (?,?,?,?,?,?,?,?,?)""", rows)
    return n


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", help="tile id, e.g. R7_C20")
    ap.add_argument("--list-for-park")
    ap.add_argument("--list-for-aoi")
    a = ap.parse_args()
    if a.fetch:
        print(ensure_tile(a.fetch))
    if a.list_for_park:
        from shapely.geometry import shape
        parks = json.load(open(BASE_DIR / "data" / "keystones_with_boundaries.json"))
        p = next(x for x in parks if x["id"] == a.list_for_park)
        print(" ".join(tiles_for_geom(shape(p["geometry"]))))
    if a.list_for_aoi:
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        import aoi_lib
        conn = aoi_lib.connect(readonly=True)
        print(" ".join(tiles_for_geom(aoi_lib.aoi_geom(
            aoi_lib.load_aoi(conn, a.list_for_aoi)))))


if __name__ == "__main__":
    main()
