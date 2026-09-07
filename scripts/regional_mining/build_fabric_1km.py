#!/usr/bin/env python3
"""0.01 deg (~1.1 km) settlement fabric for CAF+SSD+SDN: GHSL built/pop +
WorldCover 2021 cropland, so "populous settlement without fields" can be
defined at the scale the XSA model defined it (1 km box around a
settlement), region-wide.

    python3 scripts/regional_mining/build_fabric_1km.py
    -> work/{GHS_BUILT_S_E2000,GHS_BUILT_S_E2015,GHS_POP_E2030}_001.tif
       work/WC2021_cropland_frac_001.tif   (bbox 14-39E, 2-18N)
"""
import glob, os, subprocess, sys
from pathlib import Path
import numpy as np, rasterio
from rasterio.transform import Affine, from_origin

ROOT = Path(__file__).resolve().parents[2]
W = ROOT / "data/eval/regional_mining/work"
LON0, LON1, LAT0, LAT1 = 14, 39, 2, 18
CELL = 0.01


def log(*a): print(*a, file=sys.stderr, flush=True)


def ghsl():
    for p in ("GHS_BUILT_S_E2000", "GHS_BUILT_S_E2015", "GHS_POP_E2030"):
        out = W / f"{p}_001.tif"
        if out.exists(): continue
        tiles = []
        for t in sorted(glob.glob(str(ROOT / f"data/ghsl/tiles/{p}_GLOBE_R2023A_54009_100_V1_0_R*_C*.tif"))):
            rc = t.split("V1_0_")[1][:-4]; R = int(rc[1:rc.index("_")]); C = int(rc[rc.index("_C") + 2:])
            if not (6 <= R <= 10 and 19 <= C <= 23): continue
            with rasterio.open(t) as r:
                a = r.read(1).astype("float32"); a[a < 0] = 0
                h, w = a.shape; k = 5   # 500 m blocks, then -r sum to 0.01 deg
                b = a[:h // k * k, :w // k * k].reshape(h // k, k, w // k, k).sum((1, 3))
                o = W / f"{p}_500m_{rc}.tif"
                with rasterio.open(o, "w", driver="GTiff", height=b.shape[0], width=b.shape[1], count=1, dtype="float32", crs=r.crs, transform=r.transform * Affine.scale(k), nodata=-1) as d: d.write(b, 1)
                tiles.append(str(o))
        vrt = W / f"{p}_500m.vrt"
        subprocess.run(["gdalbuildvrt", "-q", str(vrt)] + tiles, check=True)
        subprocess.run(["gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:4326", "-te", str(LON0), str(LAT0), str(LON1), str(LAT1), "-tr", str(CELL), str(CELL), "-r", "sum", "-ot", "Float32", "-co", "COMPRESS=DEFLATE", "-wm", "500", str(vrt), str(out)], check=True)
        for t in tiles: os.remove(t)
        os.remove(vrt)
        a = rasterio.open(out).read(1); log(p, a.shape, "total", a[a > 0].sum() / 1e6)
        # 0.05 deg companion used by build_features.py (5x5 block sum of the 1 km grid)
        a[a < 0] = 0; h, w = a.shape; b = a[:h // 5 * 5, :w // 5 * 5].reshape(h // 5, 5, w // 5, 5).sum((1, 3))
        with rasterio.open(out) as r:
            with rasterio.open(W / f"{p}_005.tif", "w", driver="GTiff", height=b.shape[0], width=b.shape[1], count=1, dtype="float32", crs=r.crs, transform=r.transform * Affine.scale(5), nodata=-1, compress="deflate") as d: d.write(b, 1)


def worldcover():
    out = W / "WC2021_cropland_frac_001.tif"
    out_lc = W / "WC2021_landcover_frac_005.tif"   # bands: tree10, shrub20, grass30, crop40, built50, bare60, water80, wetland90
    if out.exists() and out_lc.exists(): return
    URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{lat}{lon}_Map.tif"
    OV, PER = 9000, 30   # 3 deg / 9000 px = 33 m; 30 px = 0.01 deg
    Wd, H = int((LON1 - LON0) / CELL), int((LAT1 - LAT0) / CELL)
    res = np.full((H, Wd), -1, dtype=np.float32)
    CLS = (10, 20, 30, 40, 50, 60, 80, 90); H5, W5 = H // 5, Wd // 5
    lc = np.full((len(CLS), H5, W5), -1, dtype=np.float32)
    lat_t = range(int(np.floor(LAT0 / 3) * 3), LAT1, 3); lon_t = range(int(np.floor(LON0 / 3) * 3), LON1, 3)
    for lat in lat_t:
        for lon in lon_t:
            url = URL.format(lat=f"N{lat:02d}", lon=f"E{lon:03d}")
            try:
                with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="5"):
                    with rasterio.open("/vsicurl/" + url) as r:
                        a = r.read(1, out_shape=(OV, OV), resampling=rasterio.enums.Resampling.nearest)
            except Exception as e:
                log(f"  {url.split('/')[-1]}: {str(e)[:80]}"); continue
            n = OV // PER
            crop = (a == 40).reshape(n, PER, n, PER).mean(axis=(1, 3)).astype(np.float32)
            nod = (a == 0).reshape(n, PER, n, PER).mean(axis=(1, 3)); crop[nod > 0.5] = -1
            # place into the output (clip to bbox)
            r0 = int(round((LAT1 - (lat + 3)) / CELL)); c0 = int(round((lon - LON0) / CELL))
            rs, cs = max(r0, 0), max(c0, 0); re_, ce = min(r0 + n, H), min(c0 + n, Wd)
            if rs < re_ and cs < ce:
                res[rs:re_, cs:ce] = crop[rs - r0:re_ - r0, cs - c0:ce - c0]
            # 0.05 deg composition (150 px blocks)
            n5 = OV // 150
            r5 = int(round((LAT1 - (lat + 3)) / 0.05)); c5 = int(round((lon - LON0) / 0.05))
            rs5, cs5 = max(r5, 0), max(c5, 0); re5, ce5 = min(r5 + n5, H5), min(c5 + n5, W5)
            if rs5 < re5 and cs5 < ce5:
                valid = (a != 0).reshape(n5, 150, n5, 150).mean(axis=(1, 3))
                for bi, cval in enumerate(CLS):
                    fr = (a == cval).reshape(n5, 150, n5, 150).mean(axis=(1, 3)).astype(np.float32)
                    fr = np.where(valid > 0.5, fr / np.maximum(valid, 1e-6), -1)
                    lc[bi, rs5:re5, cs5:ce5] = fr[rs5 - r5:re5 - r5, cs5 - c5:ce5 - c5]
            log(f"  WC {lat},{lon} crop {crop[crop >= 0].mean():.3f}")
    with rasterio.open(out, "w", driver="GTiff", width=Wd, height=H, count=1, dtype="float32", crs="EPSG:4326", transform=from_origin(LON0, LAT1, CELL, CELL), nodata=-1, compress="lzw") as d:
        d.write(res, 1)
    with rasterio.open(out_lc, "w", driver="GTiff", width=W5, height=H5, count=len(CLS), dtype="float32", crs="EPSG:4326", transform=from_origin(LON0, LAT1, 0.05, 0.05), nodata=-1, compress="lzw") as d:
        d.write(lc)
        d.descriptions = tuple(f"class{c}" for c in CLS)
    log("wrote", out, out_lc)


if __name__ == "__main__":
    ghsl(); worldcover()
