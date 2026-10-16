#!/usr/bin/env python3
"""Input rasters clipped to an AOI for the TA/QGIS archive (feedback §G item 3).

Writes, into --out, GeoTIFFs (LZW, tiled, CRS as stored upstream):

  <AOI>_glad_cropland_2003_clip.tif        GLAD cropland extent 2000-03 (EPSG:4326, 30 m)
  <AOI>_glad_cropland_2019_clip.tif        GLAD cropland extent 2016-19
  <AOI>_ghsl_built_s_E2000_clip.tif        GHS_BUILT_S R2023A (ESRI:54009 Mollweide, 100 m)
  <AOI>_ghsl_built_s_E2015_clip.tif
  <AOI>_ghsl_built_s_E2030_clip.tif
  <AOI>_hansen_lossyear_2001-2024_clip.tif Hansen GFC-2024-v1.12 lossyear (0 = no loss,
                                            1..24 = year of loss, EPSG:4326), read through
                                            /vsicurl -- the tiles are never stored
  <AOI>_gfw_alert_cells_2024-26_0p5deg.tif GFW integrated alerts 2024-26 summed on 0.5-deg
                                            cells, band 1 alerts, band 2 high-confidence,
                                            band 3 COVERAGE (1 = measured, 0 = < MIN_ALERTS
                                            alerts -> no coverage, G1), band 4 Hansen loss
                                            2015-23 km2 in the same cell (what the
                                            alert-silent cells actually contain)
  <AOI>_gfw_alert_cells_2024-26_0p5deg.gpkg the same cells as polygons (QGIS labels)

Invariant 1: a clip that produced zero valid pixels is a failure, not an empty
file -- every output is counted after it is written and the run aborts on 0.

    python3 scripts/ta_archive_rasters.py --aoi XSA_Study_Area --out reports/TA_ARCHIVE_XSA_2026-10
    python3 scripts/ta_archive_rasters.py --aoi XSA_Study_Area --out ... --only gfw,ghsl
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))
from hansen_loss import BASE_URL as HANSEN_URL, RELEASE as HANSEN_RELEASE, tiles_for_bbox  # noqa: E402

MIN_ALERTS = 50          # feedback G1: a 0.5-deg cell with fewer alerts 2024-26 is unmeasured
CELL_DEG = 0.5
GHSL_EPOCHS = ("E2000", "E2015", "E2030")
LZW = ["-co", "COMPRESS=LZW", "-co", "TILED=YES", "-co", "BIGTIFF=IF_SAFER"]


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def sh(cmd, env=None):
    log("  $ " + " ".join(str(c) for c in cmd))
    e = dict(os.environ)
    if env:
        e.update(env)
    subprocess.run([str(c) for c in cmd], check=True, env=e)


def aoi_geom(aoi_id):
    p = BASE_DIR / "data" / "study_areas" / f"{aoi_id}.geojson"
    g = json.load(open(p))
    if g.get("type") == "FeatureCollection":
        g = g["features"][0]["geometry"]
    elif g.get("type") == "Feature":
        g = g["geometry"]
    return p, g


def bbox_of(geom):
    xs, ys = [], []
    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for d in c:
                walk(d)
    walk(geom["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


def count_valid(path):
    """Valid (non-nodata) pixel count of band 1, read block-wise."""
    import rasterio
    n = 0
    with rasterio.open(path) as ds:
        nd = ds.nodata
        for _, win in ds.block_windows(1):
            a = ds.read(1, window=win)
            n += int(a.size if nd is None else (a != nd).sum())
    return n


def check(path):
    n = count_valid(path)
    mb = os.path.getsize(path) / 1e6
    if n == 0:
        raise SystemExit(f"INVARIANT 1: {path} has 0 valid pixels -- clip failed")
    log(f"  ok {Path(path).name}: {n:,} valid px, {mb:.1f} MB")
    return n


def warp_clip(src, dst, cutline, dstnodata, extra=()):
    if Path(dst).exists():
        Path(dst).unlink()
    sh(["gdalwarp", "-q", "-cutline", cutline, "-crop_to_cutline",
        "-dstnodata", str(dstnodata), "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
        *LZW, *extra, src, dst],
       env={"GDAL_CACHEMAX": "1024", "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif", "VSI_CACHE": "TRUE",
            "VSI_CACHE_SIZE": str(256 * 1024 * 1024)})
    return check(dst)


# ----------------------------------------------------------------------------
def do_cropland(aoi, cutline, out):
    for yr in ("2003", "2019"):
        src = BASE_DIR / "data" / "cropland" / "clips" / f"{aoi}_{yr}.tif"
        if not src.exists():
            raise SystemExit(f"missing {src} -- run scripts/cropland.py for {aoi} first")
        warp_clip(src, out / f"{aoi}_glad_cropland_{yr}_clip.tif", cutline, 255)


def do_ghsl(aoi, cutline, out):
    tdir = BASE_DIR / "data" / "ghsl" / "tiles"
    for ep in GHSL_EPOCHS:
        tiles = sorted(tdir.glob(f"GHS_BUILT_S_{ep}_GLOBE_R2023A_54009_100_V1_0_R*_C*.tif"))
        if not tiles:
            raise SystemExit(f"no GHSL {ep} tiles in {tdir}")
        vrt = out / f"_ghsl_{ep}.vrt"
        sh(["gdalbuildvrt", "-q", vrt, *tiles])
        warp_clip(vrt, out / f"{aoi}_ghsl_built_s_{ep}_clip.tif", cutline, 65535,
                  extra=["-co", "PREDICTOR=2"])
        vrt.unlink()


def do_hansen(aoi, cutline, bbox, out):
    tiles = tiles_for_bbox(*bbox)
    local = BASE_DIR / "data" / "hansen"
    srcs = []
    for t in tiles:
        lp = local / f"lossyear_{t}.tif"
        srcs.append(str(lp) if lp.exists() else "/vsicurl/" + HANSEN_URL.format(tile=t))
    log(f"  Hansen {HANSEN_RELEASE} tiles {tiles} ({sum(s.startswith('/vsicurl') for s in srcs)} via /vsicurl)")
    vrt = out / "_hansen.vrt"
    sh(["gdalbuildvrt", "-q", vrt, *srcs],
       env={"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif"})
    dst = out / f"{aoi}_hansen_lossyear_2001-2024_clip.tif"
    warp_clip(vrt, dst, cutline, 255)
    vrt.unlink()
    return dst


def do_gfw(aoi, geom, bbox, out, hansen_tif):
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.features import geometry_mask
    from shapely.geometry import shape, box

    scan = BASE_DIR / "data" / "gfw_alerts" / f"{aoi}.json"
    d = json.load(open(scan))
    if not d.get("clusters"):
        raise SystemExit(f"{scan}: no clusters -- nothing to rasterise")
    x0 = math.floor(bbox[0] / CELL_DEG) * CELL_DEG
    y1 = math.ceil(bbox[3] / CELL_DEG) * CELL_DEG
    nx = int(math.ceil((bbox[2] - x0) / CELL_DEG))
    ny = int(math.ceil((y1 - bbox[1]) / CELL_DEG))
    tr = from_origin(x0, y1, CELL_DEG, CELL_DEG)
    alerts = np.zeros((ny, nx), np.int32)
    high = np.zeros((ny, nx), np.int32)
    first = np.full((ny, nx), "9999-99-99", dtype="U10")
    last = np.full((ny, nx), "0000-00-00", dtype="U10")
    for c in d["clusters"]:
        col = int((c["lon"] - x0) // CELL_DEG)
        row = int((y1 - c["lat"]) // CELL_DEG)
        if 0 <= row < ny and 0 <= col < nx:
            alerts[row, col] += c["n"]
            high[row, col] += c.get("high_conf", 0)
            first[row, col] = min(first[row, col], c["first"])
            last[row, col] = max(last[row, col], c["last"])
    poly = shape(geom)
    # Cells that touch the AOI at all; everything else is nodata (-1).
    inside = ~geometry_mask([geom], (ny, nx), tr, all_touched=True, invert=False)
    cover = np.where(alerts >= MIN_ALERTS, 1, 0).astype(np.int32)

    # Hansen loss 2015-23 per cell, km2 -- the G1 reconciliation number.
    hkm2 = np.zeros((ny, nx), np.float32)
    with rasterio.open(hansen_tif) as h:
        res = h.res[0]
        for r in range(ny):
            for cidx in range(nx):
                if not inside[r, cidx]:
                    continue
                w, s = x0 + cidx * CELL_DEG, y1 - (r + 1) * CELL_DEG
                win = rasterio.windows.from_bounds(w, s, w + CELL_DEG, s + CELL_DEG, h.transform)
                win = win.round_offsets().round_lengths()
                a = h.read(1, window=win, boundless=True, fill_value=255)
                n = int(((a >= 15) & (a <= 23)).sum())
                latc = s + CELL_DEG / 2
                px_km2 = (res * 111.32) * (res * 111.32 * math.cos(math.radians(latc)))
                hkm2[r, cidx] = n * px_km2

    tif = out / f"{aoi}_gfw_alert_cells_2024-26_0p5deg.tif"
    with rasterio.open(tif, "w", driver="GTiff", width=nx, height=ny, count=4,
                       dtype="float32", crs="EPSG:4326", transform=tr, nodata=-1,
                       compress="lzw") as dst:
        for i, a in enumerate((alerts, high, cover, hkm2), 1):
            dst.write(np.where(inside, a.astype(np.float32), -1), i)
        dst.set_band_description(1, "gfw_alerts_2024_26")
        dst.set_band_description(2, "gfw_alerts_high_conf")
        dst.set_band_description(3, f"coverage_1_measured_0_unmeasured_lt{MIN_ALERTS}")
        dst.set_band_description(4, "hansen_loss_2015_23_km2")
        dst.update_tags(SOURCE_SCAN=str(scan.name), SCANNED_AT=d.get("scanned_at", ""),
                        SINCE=d.get("since", ""), N_ALERTS=str(d.get("n_alerts", "")),
                        MIN_ALERTS_FOR_COVERAGE=str(MIN_ALERTS),
                        HANSEN=HANSEN_RELEASE)
    check(tif)

    # Same cells as polygons for labelling in QGIS.
    import fiona
    gp = out / f"{aoi}_gfw_alert_cells_2024-26_0p5deg.gpkg"
    if gp.exists():
        gp.unlink()
    schema = {"geometry": "Polygon", "properties": {
        "lon_w": "float", "lat_s": "float", "alerts_2024_26": "int", "high_conf": "int",
        "first_alert": "str", "last_alert": "str", "coverage": "int",
        "coverage_label": "str", "hansen_loss_2015_23_km2": "float", "aoi_frac": "float"}}
    n_cells = n_unmeasured = 0
    area_in = area_unm = 0.0
    with fiona.open(gp, "w", driver="GPKG", crs="EPSG:4326", schema=schema,
                    layer="gfw_alert_cells_0p5deg") as f:
        for r in range(ny):
            for cidx in range(nx):
                if not inside[r, cidx]:
                    continue
                w, s = x0 + cidx * CELL_DEG, y1 - (r + 1) * CELL_DEG
                cell = box(w, s, w + CELL_DEG, s + CELL_DEG)
                frac = cell.intersection(poly).area / cell.area
                if frac <= 0:
                    continue
                cov = int(cover[r, cidx])
                n_cells += 1; area_in += frac
                if not cov:
                    n_unmeasured += 1; area_unm += frac
                f.write({"geometry": cell.__geo_interface__, "properties": {
                    "lon_w": w, "lat_s": s, "alerts_2024_26": int(alerts[r, cidx]),
                    "high_conf": int(high[r, cidx]),
                    "first_alert": None if alerts[r, cidx] == 0 else str(first[r, cidx]),
                    "last_alert": None if alerts[r, cidx] == 0 else str(last[r, cidx]),
                    "coverage": cov,
                    "coverage_label": "measured" if cov else f"unmeasured (< {MIN_ALERTS} alerts)",
                    "hansen_loss_2015_23_km2": round(float(hkm2[r, cidx]), 3),
                    "aoi_frac": round(frac, 4)}})
    if n_cells == 0:
        raise SystemExit("INVARIANT 1: no GFW cells intersect the AOI")
    pct = 100 * area_unm / area_in
    unm_with_hansen = int(((cover == 0) & inside & (hkm2 > 0.5)).sum())
    log(f"  ok {gp.name}: {n_cells} cells, {n_unmeasured} unmeasured (< {MIN_ALERTS} alerts) = "
        f"{pct:.1f} % of AOI area; {unm_with_hansen} unmeasured cells hold > 0.5 km2 Hansen loss 2015-23")
    return {"cells": n_cells, "unmeasured": n_unmeasured, "unmeasured_area_pct": round(pct, 1),
            "unmeasured_with_hansen_loss": unm_with_hansen, "scanned_at": d.get("scanned_at"),
            "since": d.get("since"), "n_alerts": d.get("n_alerts")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="XSA_Study_Area")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="cropland,ghsl,hansen,gfw")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cutline, geom = aoi_geom(a.aoi)
    bbox = bbox_of(geom)
    only = set(a.only.split(","))
    log(f"{a.aoi} bbox {bbox} -> {out}")
    if "cropland" in only:
        do_cropland(a.aoi, cutline, out)
    if "ghsl" in only:
        do_ghsl(a.aoi, cutline, out)
    hansen = out / f"{a.aoi}_hansen_lossyear_2001-2024_clip.tif"
    if "hansen" in only:
        hansen = do_hansen(a.aoi, cutline, bbox, out)
    if "gfw" in only:
        if not hansen.exists():
            raise SystemExit("gfw cells need the Hansen clip (band 4); run hansen first")
        stats = do_gfw(a.aoi, geom, bbox, out, hansen)
        (out / f"_gfw_cells_stats.json").write_text(json.dumps(stats, indent=1))
    log("done")


if __name__ == "__main__":
    main()
