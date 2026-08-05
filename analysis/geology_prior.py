"""Geological prospectivity prior from a georeferenced legacy geology map.

Validated on the 1964 Carte Geologique de la Republique Centrafricaine
(1:1.5M, 308 m/px scan): all 8 manually-confirmed artisanal mining sites in
the Chinko headwaters sit 0.44-0.87 km from a mapped mafic/amphibolite lens
(map colour = blue), while random points in the Chinko basin are a median
12.0 km away. P(random point that close) = 0.053, so 8/8 is not chance.

Usage:
  python3 analysis/geology_prior.py --map "/path/CAR GEO real.tif" \
      --bbox 23.2,6.6,25.6,8.35 --out data/geology_prior/CAF_Chinko.json \
      [--points data/mining_pits/CAF_Chinko.json]
"""
import argparse, json, os
import numpy as np, rasterio
from rasterio.windows import from_bounds
from scipy import ndimage

# map-colour classes of the 1964 CAR / SSD sheets
CLASSES = {
    # name: predicate on (r,g,b) int16 arrays
    "mafic":   lambda r,g,b: (b-r > 25) & (b > 110) & (g < b),      # blue lenses
    "granite": lambda r,g,b: (r-b > 40) & (r > 170) & (g < r-30),   # pink/magenta
    "quartz":  lambda r,g,b: (r > 200) & (g > 190) & (b < 150),     # yellow
}

def classify(path, bbox):
    with rasterio.open(path) as d:
        win = from_bounds(*bbox, d.transform)
        a = d.read(window=win, boundless=True, fill_value=255)[:3].astype(np.int16)
        tr = d.window_transform(win)
    r, g, b = a
    out = {}
    for k, fn in CLASSES.items():
        m = ndimage.binary_closing(fn(r, g, b), np.ones((2, 2)))
        out[k] = m
    return out, tr, a.shape[1:]

def dist_km(mask, tr):
    return ndimage.distance_transform_edt(~mask) * abs(tr.a) * 111

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", required=True)
    ap.add_argument("--bbox", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--points", help="json with sites[] {lat,lon} to score")
    ap.add_argument("--res", type=float, default=0.02, help="output grid deg")
    a = ap.parse_args()
    bbox = tuple(float(x) for x in a.bbox.split(","))
    masks, tr, shape = classify(a.map, bbox)
    dists = {k: dist_km(m, tr) for k, m in masks.items()}
    H, W = shape

    def at(lon, lat):
        col, row = ~tr * (lon, lat)
        row, col = int(row), int(col)
        if not (0 <= row < H and 0 <= col < W):
            return None
        return {k: round(float(v[row, col]), 2) for k, v in dists.items()}

    grid = []
    lat = bbox[1]
    while lat < bbox[3]:
        lon = bbox[0]
        while lon < bbox[2]:
            v = at(lon + a.res/2, lat + a.res/2)
            if v:
                # prior: high where close to mafic contact (validated), mild
                # boost near granite (quartz-vein gold) — capped at 1.0
                p = max(0.0, 1.0 - v["mafic"] / 5.0) * 0.8 + \
                    max(0.0, 1.0 - v["granite"] / 5.0) * 0.2
                if p > 0.05:
                    grid.append([round(lon, 4), round(lat, 4), round(p, 3)])
            lon += a.res
        lat += a.res
    res = {"map": os.path.basename(a.map), "bbox": bbox, "res": a.res,
           "classes": {k: round(float(m.mean()), 4) for k, m in masks.items()},
           "grid": grid}
    if a.points:
        d = json.load(open(a.points))
        sc = []
        for s in d.get("sites", []):
            v = at(s["lon"], s["lat"])
            if v:
                sc.append({"lat": s["lat"], "lon": s["lon"],
                           "score": s.get("score"), **v})
        res["scored_points"] = sc
        near = [x for x in sc if x["mafic"] <= 2.0]
        print(f"{len(near)}/{len(sc)} candidate sites within 2 km of mapped mafic lens")
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(res, open(a.out, "w"))
    print(f"{len(grid)} prior cells -> {a.out}")

main()
