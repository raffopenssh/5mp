#!/usr/bin/env python3
"""Fetch ERA5 / ERA5-Land monthly files for an AOI bbox from the Copernicus CDS (one request per month).

Two products, same credentials (~/.cdsapirc), same filename scheme, one .nc per month:
  wind  reanalysis-era5-single-levels  10m u/v, 3-hourly (0.25 deg)   -> data/climate/era5/<aoi>_<YYYY-MM>.nc        (old name kept)
  land  reanalysis-era5-land           tp, t2m, d2m, swvl1 at 00 + 12 UTC (0.1 deg) -> data/climate/era5/<aoi>_land_<YYYY-MM>.nc

Bandwidth/disk: only the AOI bbox (+0.5 deg), only the hours the daily products need (wind: 8/day for day + pm means;
land: 00 UTC = previous day's accumulated precipitation, 12 UTC = afternoon T/Td for VPD, soil moisture both), netcdf,
unarchived. A month is skipped when its .nc exists OR when it is already folded into the daily npz
(wind_field.py / climate_field.py record their sources), so pruned .nc files are never re-fetched.

Usage:
  .venv/bin/python scripts/fetch_era5.py --aoi XSA_Study_Area --product wind --start 2025-10 --end 2026-03
  .venv/bin/python scripts/fetch_era5.py --aoi XSA_Study_Area --product land --start 2023-10 --end 2026-03 --months 10,11,12,1,2,3

Licences for both datasets must be accepted once on the CDS web site. ~5 min/month on the CDS queue: run in tmux.
"""
import argparse, math, os, sqlite3, sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "climate", "era5")

PRODUCTS = {
    "wind": dict(dataset="reanalysis-era5-single-levels", tag="", product_type=["reanalysis"],
                 variable=["10m_u_component_of_wind", "10m_v_component_of_wind"],
                 time=[f"{h:02d}:00" for h in range(0, 24, 3)]),
    "land": dict(dataset="reanalysis-era5-land", tag="land_", product_type=None,
                 variable=["total_precipitation", "2m_temperature", "2m_dewpoint_temperature", "volumetric_soil_water_layer_1"],
                 time=["00:00", "12:00"]),
}


def nc_path(aoi, product, y, m):
    return os.path.join(OUT, f"{aoi}_{PRODUCTS[product]['tag']}{y}-{m:02d}.nc")


def aoi_bbox(aoi_id, pad=0.5):
    con = sqlite3.connect(os.path.join(ROOT, "db.sqlite3"))
    r = con.execute("select bbox_minx,bbox_miny,bbox_maxx,bbox_maxy from aois where id=?", (aoi_id,)).fetchone()
    if not r:
        r = con.execute("select bbox_minx,bbox_miny,bbox_maxx,bbox_maxy from parks where id=?", (aoi_id,)).fetchone()
    if not r:
        sys.exit(f"area {aoi_id} not found in aois/parks")
    x0, y0, x1, y1 = r
    return [math.ceil(y1 + pad), math.floor(x0 - pad), math.floor(y0 - pad), math.ceil(x1 + pad)]   # N, W, S, E


def month_range(start, end):
    y, m = map(int, start.split("-")); ye, me = map(int, end.split("-"))
    while (y, m) <= (ye, me):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def folded_sources(aoi, product):
    """basenames of .nc files already folded into the daily npz (so a pruned .nc is not re-fetched)"""
    import numpy as np
    npz = os.path.join(OUT, f"{aoi}_{'wind' if product == 'wind' else 'land'}_daily.npz")
    if not os.path.exists(npz):
        return set()
    try:
        return set(np.load(npz, allow_pickle=False)["sources"].tolist())
    except Exception:
        return set()


def fetch_month(client, aoi, product, area, y, m, folded):
    path = nc_path(aoi, product, y, m)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, "cached"
    if os.path.basename(path) in folded:
        return path, "folded"
    if (y, m) > (date.today().year, date.today().month):
        return path, "future"
    P = PRODUCTS[product]
    ndays = (date(y + (m == 12), m % 12 + 1, 1) - date(y, m, 1)).days
    req = {"variable": P["variable"], "year": [str(y)], "month": [f"{m:02d}"],
           "day": [f"{d:02d}" for d in range(1, ndays + 1)], "time": P["time"], "area": area,
           "data_format": "netcdf", "download_format": "unarchived"}
    if P["product_type"]:
        req["product_type"] = P["product_type"]
    print(f"[era5:{product}] requesting {y}-{m:02d} area={area}", flush=True)
    try:
        client.retrieve(P["dataset"], req, path + ".part")
    except Exception as e:   # a month not yet in the archive (ERA5T lag) is a state, not a crash
        if os.path.exists(path + ".part"): os.remove(path + ".part")
        print(f"[era5:{product}] {y}-{m:02d} unavailable: {str(e)[:160]}", flush=True)
        return path, "unavailable"
    os.replace(path + ".part", path)
    return path, "fetched"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="XSA_Study_Area")
    ap.add_argument("--product", choices=sorted(PRODUCTS), default="wind")
    ap.add_argument("--start", required=True, help="YYYY-MM")
    ap.add_argument("--end", required=True, help="YYYY-MM")
    ap.add_argument("--months", help="comma list of calendar months to keep, e.g. 10,11,12,1,2,3")
    ap.add_argument("--no-summary", action="store_true", help="(kept for the old wind CLI; summaries live in the npz now)")
    a = ap.parse_args()
    import cdsapi
    os.makedirs(OUT, exist_ok=True)
    keep = {int(x) for x in a.months.split(",")} if a.months else None
    area = aoi_bbox(a.aoi)
    client = cdsapi.Client(quiet=True)
    folded = folded_sources(a.aoi, a.product)
    status = {}
    for y, m in month_range(a.start, a.end):
        if keep and m not in keep:
            continue
        _, s = fetch_month(client, a.aoi, a.product, area, y, m, folded)
        status[s] = status.get(s, 0) + 1
    print(f"[era5:{a.product}] done {status}")
    return 0 if status.get("fetched") or not status.get("unavailable") or len(status) > 1 else 2


if __name__ == "__main__":
    sys.exit(main())
