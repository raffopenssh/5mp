#!/usr/bin/env python3
"""Fetch ERA5 10 m wind (u/v) for an AOI bbox from the Copernicus CDS.

Auth: ~/.cdsapirc (url + personal access token from https://cds.climate.copernicus.eu/profile).
Licence for 'reanalysis-era5-single-levels' must be accepted once on the CDS web site.

Usage:
  .venv/bin/python scripts/fetch_era5_wind.py --aoi XSA_Study_Area --start 2026-01 --end 2026-02
  .venv/bin/python scripts/fetch_era5_wind.py --aoi XSA_Study_Area --start 2019-12 --end 2026-03 --months 12,1,2,3

Writes data/climate/era5/<aoi>_<YYYY-MM>.nc (one file per month, skipped if present)
and data/climate/era5/<aoi>_daily.csv with daily mean u, v, speed, direction-from (deg) per grid cell.
"""
import argparse, csv, math, os, sqlite3, sys
from datetime import date
import cdsapi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "climate", "era5")
DATASET = "reanalysis-era5-single-levels"
HOURS = [f"{h:02d}:00" for h in range(0, 24, 3)]   # 3-hourly is plenty for daily means


def aoi_bbox(aoi_id):
    con = sqlite3.connect(os.path.join(ROOT, "db.sqlite3"))
    r = con.execute("select bbox_minx,bbox_miny,bbox_maxx,bbox_maxy from aois where id=?", (aoi_id,)).fetchone()
    if not r:
        sys.exit(f"AOI {aoi_id} not found")
    x0, y0, x1, y1 = r
    pad = 0.5
    # CDS area order: North, West, South, East
    return [math.ceil(y1 + pad), math.floor(x0 - pad), math.floor(y0 - pad), math.ceil(x1 + pad)]


def month_range(start, end):
    y, m = map(int, start.split("-")); ye, me = map(int, end.split("-"))
    while (y, m) <= (ye, me):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def fetch_month(client, aoi, area, y, m):
    path = os.path.join(OUT, f"{aoi}_{y}-{m:02d}.nc")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    ndays = (date(y + (m == 12), m % 12 + 1, 1) - date(y, m, 1)).days
    req = {
        "product_type": ["reanalysis"],
        "variable": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
        "year": [str(y)], "month": [f"{m:02d}"],
        "day": [f"{d:02d}" for d in range(1, ndays + 1)],
        "time": HOURS,
        "area": area,
        "data_format": "netcdf", "download_format": "unarchived",
    }
    print(f"[era5] requesting {y}-{m:02d} area={area}", flush=True)
    client.retrieve(DATASET, req, path + ".part")
    os.replace(path + ".part", path)
    return path


def summarise(aoi, files):
    import xarray as xr
    import numpy as np
    out = os.path.join(OUT, f"{aoi}_daily.csv")
    rows = 0
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "lat", "lon", "u10", "v10", "speed_ms", "dir_from_deg"])
        for p in files:
            ds = xr.open_dataset(p)
            tdim = "valid_time" if "valid_time" in ds.dims else "time"
            d = ds[["u10", "v10"]].resample({tdim: "1D"}).mean()
            u, v = d["u10"].values, d["v10"].values
            spd = np.hypot(u, v)
            # meteorological convention: direction the wind blows FROM
            dirn = (270 - np.degrees(np.arctan2(v, u))) % 360
            times = d[tdim].values.astype("datetime64[D]").astype(str)
            lats, lons = d["latitude"].values, d["longitude"].values
            for ti, t in enumerate(times):
                for li, la in enumerate(lats):
                    for lo_i, lo in enumerate(lons):
                        w.writerow([t, f"{la:.2f}", f"{lo:.2f}", f"{u[ti,li,lo_i]:.2f}", f"{v[ti,li,lo_i]:.2f}",
                                    f"{spd[ti,li,lo_i]:.2f}", f"{dirn[ti,li,lo_i]:.0f}"])
                        rows += 1
    print(f"[era5] wrote {out} ({rows} rows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="XSA_Study_Area")
    ap.add_argument("--start", required=True, help="YYYY-MM")
    ap.add_argument("--end", required=True, help="YYYY-MM")
    ap.add_argument("--months", help="comma list of calendar months to keep, e.g. 12,1,2,3")
    ap.add_argument("--no-summary", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    keep = {int(x) for x in a.months.split(",")} if a.months else None
    area = aoi_bbox(a.aoi)
    client = cdsapi.Client(quiet=True)
    files = []
    for y, m in month_range(a.start, a.end):
        if keep and m not in keep:
            continue
        files.append(fetch_month(client, a.aoi, area, y, m))
    if not a.no_summary:
        summarise(a.aoi, files)


if __name__ == "__main__":
    main()
