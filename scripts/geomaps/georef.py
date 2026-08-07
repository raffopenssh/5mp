#!/usr/bin/env python3
"""Georeference a scanned geology sheet from its own printed graticule.

    python3 scripts/geomaps/georef.py sudan
    python3 scripts/geomaps/georef.py car --preview

Same discipline as scripts/histmaps: the graticule is the control, a TPS
absorbs paper shrinkage and the sheet's own projection, and the result is
clipped to the country boundary so two sheets sit side by side with no collar,
no legend box and no neighbouring-country fill overlapping each other.

Output: data/geomaps/work/<id>_geo.tif  (EPSG:4326, RGBA, nodata outside cutline)
        data/geomaps/work/<id>_gcps.json (every measured point + residuals)
"""
import argparse, json, os, subprocess, sys
import numpy as np
import rasterio
import shapely.geometry as sg
from shapely.ops import unary_union

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gridfit import PolyModel, measure_grid          # noqa: E402
from sheets import SHEETS                            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "data/geomaps/work")
GADM = os.path.join(ROOT, "scripts/histmaps")        # gadm_XXX.json live there


def seed_model(sheet):
    """Degree-1 PolyModel from the sheet's declared seed, as a starting guess."""
    s = sheet["seed"]
    m = PolyModel(deg=2)
    if "dx" in s:                                     # affine geotransform seed
        # invert (lon,lat) = A @ (x,y) + t  ->  (x,y) = A^-1 @ (lon-lon0, lat-lat0)
        A = np.array([[s["dx"], s["dxy"]], [s["dyx"], s["dy"]]])
        Ai = np.linalg.inv(A)
        lon0, lat0 = s["x0"], s["y0"]
        # PolyModel deg 2 coeff order: 1, lon, lat, lon^2, lon*lat, lat^2
        m.cx = np.array([-(Ai[0, 0] * lon0 + Ai[0, 1] * lat0), Ai[0, 0], Ai[0, 1], 0, 0, 0])
        m.cy = np.array([-(Ai[1, 0] * lon0 + Ai[1, 1] * lat0), Ai[1, 0], Ai[1, 1], 0, 0, 0])
    else:                                             # two printed labels per axis
        m.cx = np.array([s["x0"] - s["lon0"] * s["xdeg"], s["xdeg"], 0, 0, 0, 0])
        m.cy = np.array([s["y0"] + s["lat0"] * s["ydeg"], 0, -s["ydeg"], 0, 0, 0])
    return m


def country_geom(iso_list):
    geoms = []
    for iso in iso_list:
        p = os.path.join(GADM, f"gadm_{iso}.json")
        d = json.load(open(p))
        for f in d["features"]:
            geoms.append(sg.shape(f["geometry"]))
    return unary_union(geoms)


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def georef(sid, preview=False, buffer_km=0.0):
    sheet = SHEETS[sid]
    src_path = os.path.join(ROOT, sheet["src"])
    os.makedirs(WORK, exist_ok=True)
    src = rasterio.open(src_path)
    print(f"{sid}: {src.width}x{src.height}")

    g = sheet["grid"]
    model = seed_model(sheet)
    gcps, model, stats = measure_grid(src, model, g["lons"], g["lats"])
    print(f"  {stats['n_gcps']} control points  rms {stats['rms_px']:.2f}px  max {stats['max_px']:.2f}px")

    # degrees-per-pixel at sheet centre, for a residual in metres
    lon_c = float(np.mean(g["lons"])); lat_c = float(np.mean(g["lats"]))
    px0 = np.array(model.predict([lon_c], [lat_c])).ravel()
    px1 = np.array(model.predict([lon_c + 0.01], [lat_c])).ravel()
    deg_per_px = 0.01 / np.hypot(*(px1 - px0))
    rms_m = stats["rms_px"] * deg_per_px * 111320 * np.cos(np.radians(lat_c))
    print(f"  ~{rms_m:.0f} m rms on the ground")

    json.dump(dict(sheet=sid, stats=stats, rms_m=float(rms_m),
                   gcps=[dict(x=x, y=y, lon=lo, lat=la) for x, y, lo, la in gcps]),
              open(os.path.join(WORK, f"{sid}_gcps.json"), "w"), indent=1)

    cut = country_geom(sheet["countries"])
    if buffer_km:
        cut = cut.buffer(buffer_km / 111.32)
    cut_path = os.path.join(WORK, f"{sid}_cut.geojson")
    json.dump(dict(type="FeatureCollection", features=[
        dict(type="Feature", properties={}, geometry=sg.mapping(cut))]),
        open(cut_path, "w"))

    tmp = os.path.join(WORK, f"{sid}_gcp.vrt")
    out = os.path.join(WORK, f"{sid}_geo.tif")
    gcp_args = []
    for x, y, lo, la in gcps:
        gcp_args += ["-gcp", f"{x:.3f}", f"{y:.3f}", f"{lo}", f"{la}"]
    run(["gdal_translate", "-q", "-of", "VRT", "-a_srs", "EPSG:4326"] + gcp_args + [src_path, tmp])

    res = deg_per_px * (4 if preview else 1)
    run(["gdalwarp", "-q", "-overwrite", "-tps", "-r", "near",
         "-t_srs", "EPSG:4326", "-tr", f"{res}", f"{res}",
         "-cutline", cut_path, "-crop_to_cutline",
         "-dstalpha", "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE",
         "-co", "BIGTIFF=IF_SAFER", "-wo", "NUM_THREADS=ALL_CPUS",
         "-multi", tmp, out])
    print("  ->", out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet", choices=sorted(SHEETS))
    ap.add_argument("--preview", action="store_true", help="quarter resolution")
    ap.add_argument("--buffer-km", type=float, default=0.0)
    a = ap.parse_args()
    georef(a.sheet, a.preview, a.buffer_km)
