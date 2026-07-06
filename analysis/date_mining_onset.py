"""Date mining onset via monthly Sentinel-2 series at a pit location.

Samples a small window (default 300m) of red/green/nir/SCL around the pit for
the least-cloudy scene of each month, tracking bare-earth fraction
(high red, low NDVI) — pit clearings appear as a step-change.

Usage: python3 analysis/date_mining_onset.py --lat 7.446 --lon 24.030 \
         --start 2024-01 --end 2026-07 [--out /tmp/onset_series.json]
"""
import argparse, json, sys, urllib.request
import numpy as np
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds

STAC = "https://earth-search.aws.element84.com/v1/search"

def months(start, end):
    y, m = map(int, start.split("-"))
    ye, me = map(int, end.split("-"))
    while (y, m) <= (ye, me):
        nm, ny = (m % 12) + 1, y + (m // 12)
        yield f"{y:04d}-{m:02d}-01", f"{ny:04d}-{nm:02d}-01"
        y, m = ny, nm

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--start", default="2024-01")
    ap.add_argument("--end", default="2026-07")
    ap.add_argument("--half-m", type=float, default=150, help="half window, meters")
    ap.add_argument("--out", default="/tmp/onset_series.json")
    args = ap.parse_args()

    dd = args.half_m / 111000.0
    bbox = [args.lon - dd, args.lat - dd, args.lon + dd, args.lat + dd]
    series = []
    for m0, m1 in months(args.start, args.end):
        body = json.dumps({"collections": ["sentinel-2-l2a"], "bbox": bbox,
            "datetime": f"{m0}T00:00:00Z/{m1}T00:00:00Z",
            "query": {"eo:cloud_cover": {"lt": 60}}, "limit": 20,
            "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}]}).encode()
        req = urllib.request.Request(STAC, data=body,
                                     headers={"Content-Type": "application/json"})
        scenes = json.load(urllib.request.urlopen(req))["features"]
        rec = None
        for sc in scenes:
            try:
                with rasterio.open(sc["assets"]["red"]["href"]) as R, \
                     rasterio.open(sc["assets"]["nir"]["href"]) as N, \
                     rasterio.open(sc["assets"]["scl"]["href"]) as S:
                    b = transform_bounds("EPSG:4326", R.crs, *bbox)
                    red = R.read(1, window=from_bounds(*b, R.transform)).astype(float)
                    nir = N.read(1, window=from_bounds(*b, N.transform)).astype(float)
                    scl = S.read(1, window=from_bounds(*b, S.transform))
                    # SCL is 20m; upsample check via fraction of clear pixels
                    clear = np.isin(scl, (4, 5, 6, 7)).mean()
                    if clear < 0.7 or red.size == 0:
                        continue
                    ndvi = (nir - red) / np.maximum(nir + red, 1)
                    bare = float(((red > 1400) & (ndvi < 0.35)).mean())
                    rec = {"month": m0[:7], "date": sc["properties"]["datetime"][:10],
                           "scene": sc["id"],
                           "cloud": round(sc["properties"]["eo:cloud_cover"], 1),
                           "bare_frac": round(bare, 3),
                           "mean_red": int(red.mean()),
                           "mean_ndvi": round(float(ndvi.mean()), 3)}
                    break
            except Exception as ex:
                print(f"  ERR {sc['id']}: {ex}", file=sys.stderr)
        if rec:
            series.append(rec)
            print(f"{rec['month']} {rec['date']} bare={rec['bare_frac']:.2f} "
                  f"ndvi={rec['mean_ndvi']:.2f} red={rec['mean_red']}", file=sys.stderr)
        else:
            print(f"{m0[:7]} no usable scene", file=sys.stderr)
    json.dump({"lat": args.lat, "lon": args.lon, "series": series},
              open(args.out, "w"), indent=1)
    # onset = first month where bare_frac exceeds 2x the pre-period median and >0.15
    if len(series) > 4:
        base = np.median([s["bare_frac"] for s in series[:4]])
        for s in series:
            if s["bare_frac"] > max(0.15, 2 * base):
                print(f"ONSET: {s['month']} (bare_frac {s['bare_frac']} vs baseline {base:.2f})")
                break

if __name__ == "__main__":
    main()
