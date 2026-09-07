#!/usr/bin/env python3
"""Cropland fraction per 0.05 deg cell, CAF+SSD+SDN, from ESA WorldCover 2021
(10 m, class 40 = cropland; CC BY 4.0; Zanaga et al. 2022 doi:10.5281/zenodo.7254221).

    python3 scripts/regional_mining/worldcover_crop.py
    -> data/eval/regional_mining/work/WC2021_cropland_frac_005.tif

Reads each 3x3 deg COG at the 2250x2250 overview (~133 m) via /vsicurl and
block-averages (class==40) to 0.05 deg (30x30 overview px per cell).
The GLAD 2003/2019 clips used by park_settlements.cropland_frac_* exist only
per area (single-strip source, no overviews - a region clip stalled at
<1 KB/s); WorldCover is the region-wide stand-in for the "camp without
fields" signal and is scored against GLAD inside XSA by the harness.
"""
import sys, subprocess
from pathlib import Path
import numpy as np, rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/eval/regional_mining/work/WC2021_cropland_frac_005.tif"
URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{lat}{lon}_Map.tif"
LON0, LON1, LAT0, LAT1 = 12, 39, 0, 18   # tile-aligned bbox
CELL = 0.05
W, H = int((LON1 - LON0) / CELL), int((LAT1 - LAT0) / CELL)
OV = 2250; PER = int(round(OV / (3 / CELL)))  # 37.5 -> not integer; use 2250 -> 60 cells -> 37.5 px. Use overview 4500 (75 px/cell) instead.
OV = 4500; PER = 75


def main():
    out = np.full((H, W), -1, dtype=np.float32)
    done = 0
    for lat in range(LAT0, LAT1, 3):
        for lon in range(LON0, LON1, 3):
            url = URL.format(lat=f"N{lat:02d}", lon=f"E{lon:03d}")
            try:
                with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="5"):
                    with rasterio.open("/vsicurl/" + url) as r:
                        a = r.read(1, out_shape=(OV, OV), resampling=rasterio.enums.Resampling.nearest)
            except Exception as e:
                print(f"  {url.split('/')[-1]}: {str(e)[:80]}", file=sys.stderr); continue
            crop = (a == 40).reshape(OV // PER, PER, OV // PER, PER).mean(axis=(1, 3)).astype(np.float32)
            nod = (a == 0).reshape(OV // PER, PER, OV // PER, PER).mean(axis=(1, 3))
            crop[nod > 0.5] = -1
            r0 = int((LAT1 - (lat + 3)) / CELL); c0 = int((lon - LON0) / CELL)
            out[r0:r0 + OV // PER, c0:c0 + OV // PER] = crop
            done += 1; print(f"  {lat},{lon} mean crop {crop[crop>=0].mean():.3f}", file=sys.stderr, flush=True)
    with rasterio.open(OUT, "w", driver="GTiff", width=W, height=H, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(LON0, LAT1, CELL, CELL), nodata=-1, compress="lzw") as d:
        d.write(out, 1)
    print(f"wrote {OUT} ({done} tiles)", file=sys.stderr)


if __name__ == "__main__":
    main()
