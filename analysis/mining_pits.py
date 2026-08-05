"""Mining pit scanner — detects bright-bare pit/camp clusters along river corridors.

Complements analysis/river_turbidity.py (plume detection): finds the mines
themselves as clusters of bright bare earth (Sentinel-2 red > 1400, NDVI <
0.35) within a corridor around OSM waterways. This is what a human eye does
on Google Earth. Validated on the confirmed Chinko pit (7.44644N 24.02958E,
49 px): detected at identical coords in 2022-2026 scenes.

Method:
  1. Corridor = 0.05-deg tiles touched by any OSM waterway (cache from
     river_turbidity's data/osm_raw/waterways/{park}.geojson).
  2. Detection pass: newest clear scene per tile -> bright-bare clusters
     (>= 8 px = 0.08 ha), keeping those within `--water-dist-m` of a waterway.
  3. Persistence pass: re-check each candidate in an earlier scene (>= 10
     days older). Drops clouds, floodplains-of-the-day, freshly burned scars.
  4. Score: size, waterway proximity, pond presence (NDWI > 0 pixels within
     cluster bbox — mining ponds), isolation from known OSM places.

Output: data/mining_pits/{park}.json  {sites: [...], tiles_scanned, ...}

Usage:
  python3 analysis/mining_pits.py --park CAF_Chinko
  python3 analysis/mining_pits.py --park SSD_Boma --bbox 34.2,6.2,35.3,8.2  # Akobo corridor only
"""
import argparse, datetime, json, math, os, sys, urllib.request

import numpy as np

STAC = "https://earth-search.aws.element84.com/v1/search"
OUT_DIR = "data/mining_pits"
WATERWAY_CACHE = "data/osm_raw/waterways"
TILE = 0.05           # deg, detection tile size
MIN_PX = 8            # >= 0.08 ha at 10m
MAX_PX = 3000         # skip towns / huge bare areas
RED_MIN = 1400
NDVI_MAX = 0.35

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_CACHE_SIZE", "200000000")


def km(a, b):
    return math.hypot((a[1]-b[1])*111, (a[0]-b[0])*111*math.cos(math.radians(a[1])))


def load_corridor(park_id, bbox=None, min_pct=None, use_osm=False,
                  scope="basin"):
    """Return (tiles, waterway_pts) for the scan corridor.

    Corridor comes from **DEM flow accumulation**, not OSM waterways.

    OSM waterways were the original definition and they are structurally wrong
    for this problem: the 8 field-confirmed pits in the Chinko headwaters are
    48.9 km from the nearest cached OSM waterway vertex, because nobody has
    mapped 1st-order streams in CAR. D8 flow accumulation on Copernicus GLO-30
    puts those same pits at accumulation percentile 93.7-99.5 of their local
    10x8 km window (analysis/flow_corridor.py --validate), so terrain finds the
    drainage lines that OSM does not have.

    `scope`:
      basin  - the park's contributing watershed (park_basins, migration 039).
               This is the correct extent: mining pressure is a watershed
               phenomenon and the truth pits are 123 km OUTSIDE the park.
      park   - park polygon only (fallback when no basin has been fetched).

    `use_osm=True` restores the legacy behaviour for A/B comparison only.
    """
    from shapely.geometry import shape, box
    if use_osm:
        return _load_corridor_osm(park_id, bbox)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import flow_corridor as fc

    geom = fc.basin_geom(park_id) if scope == "basin" else None
    if geom is None:
        parks = json.load(open("data/keystones_with_boundaries.json"))
        p = next((x for x in parks if x["id"] == park_id), None)
        if not p or not p.get("geometry"):
            return set(), []
        geom = shape(p["geometry"])
        print(f"{park_id}: no cached basin, falling back to park polygon "
              f"(run scripts/fetch_park_basins.py --park {park_id})",
              file=sys.stderr)
    if bbox:
        geom = geom.intersection(box(*bbox))
        if geom.is_empty:
            return set(), []

    pct = fc.DEFAULT_PCT if min_pct is None else min_pct
    cells = fc.corridor_cells(geom, res=TILE, min_pct=pct)
    tiles = set(cells.keys())
    wpts = fc.corridor_points(geom.bounds, min_pct=pct, stride=3)
    return tiles, wpts


def _load_corridor_osm(park_id, bbox=None):
    """LEGACY corridor: 0.05-deg tiles touched by an OSM waterway. Kept only so
    `--corridor osm` can reproduce the old output for A/B."""
    path = f"{WATERWAY_CACHE}/{park_id}.geojson"
    d = json.load(open(path))
    tiles, wpts = set(), []
    for f in d["features"]:
        g = f["geometry"]
        if g["type"] != "LineString":
            continue
        cs = g["coordinates"]
        if bbox and not any(bbox[0] <= c[0] <= bbox[2] and bbox[1] <= c[1] <= bbox[3]
                            for c in cs[::5]):
            continue
        prev = None
        for c in cs:
            if bbox and not (bbox[0] <= c[0] <= bbox[2] and bbox[1] <= c[1] <= bbox[3]):
                prev = c
                continue
            tiles.add((int(c[0] / TILE), int(c[1] / TILE)))
            if prev is not None:
                n = max(1, int(km(prev, c) / 0.1))
                for t in range(n):
                    wpts.append((prev[0] + (c[0]-prev[0])*t/n,
                                 prev[1] + (c[1]-prev[1])*t/n))
            prev = c
        if prev is not None:
            wpts.append(tuple(prev[:2]))
    return tiles, wpts


def stac_scenes(bbox, dt_range, max_cloud=60, limit=200):
    body = json.dumps({"collections": ["sentinel-2-l2a"], "bbox": list(bbox),
        "datetime": dt_range, "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": limit,
        "sortby": [{"field": "properties.datetime", "direction": "desc"}]}).encode()
    req = urllib.request.Request(STAC, data=body,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))["features"]


def read_tile(sc, tb):
    """Read red/nir/scl for tile bounds tb=(w,s,e,n). Returns dict or None
    (cloudy/empty). Uses one rasterio env; caller keeps scene datasets open."""
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.warp import transform_bounds
    R, N, S = sc["_ds"]
    b = transform_bounds("EPSG:4326", R.crs, *tb)
    win = from_bounds(*b, R.transform)
    red = R.read(1, window=win, boundless=True, fill_value=0).astype(np.float32)
    if red.size == 0 or (red > 0).mean() < 0.5:
        return None
    nir = N.read(1, window=from_bounds(*b, N.transform),
                 boundless=True, fill_value=0).astype(np.float32)
    scl = S.read(1, window=from_bounds(*b, S.transform),
                 boundless=True, fill_value=0)
    clear = np.isin(scl, (4, 5, 6, 7, 11)).mean()
    if clear < 0.85:
        return None
    h = min(red.shape[0], nir.shape[0]); w = min(red.shape[1], nir.shape[1])
    from rasterio.windows import Window
    tr = R.window_transform(win)
    return {"red": red[:h, :w], "nir": nir[:h, :w], "transform": tr, "crs": R.crs}


def clusters_in_tile(t):
    """Bright-bare clusters -> [(px, lat, lon, pond_px)]."""
    from scipy import ndimage
    from rasterio.warp import transform as rio_transform
    red, nir = t["red"], t["nir"]
    ndvi = (nir - red) / np.maximum(nir + red, 1)
    mask = (red > RED_MIN) & (ndvi < NDVI_MAX)
    if not mask.any():
        return []
    # NDWI-ish proxy for ponds: very low NIR + not bright (turbid water has
    # nir < red); mining ponds sit next to pits
    pond = (nir < 1200) & (red > 600) & (red < 2500) & (ndvi < 0)
    lab, n = ndimage.label(mask)
    out = []
    for i in range(1, n + 1):
        m = lab == i
        s = int(m.sum())
        if s < MIN_PX or s > MAX_PX:
            continue
        cy, cx = ndimage.center_of_mass(m)
        x, y = t["transform"] * (cx, cy)
        lon, lat = rio_transform(t["crs"], "EPSG:4326", [x], [y])
        ys, xs = np.where(m)
        pad = 15  # 150m
        y0, y1 = max(0, ys.min()-pad), min(mask.shape[0], ys.max()+pad)
        x0, x1 = max(0, xs.min()-pad), min(mask.shape[1], xs.max()+pad)
        pond_px = int(pond[y0:y1, x0:x1].sum())
        out.append({"px": s, "lat": round(lat[0], 5), "lon": round(lon[0], 5),
                    "pond_px": pond_px})
    return out


def open_scene(sc):
    import rasterio
    R = rasterio.open(sc["assets"]["red"]["href"])
    N = rasterio.open(sc["assets"]["nir"]["href"])
    S = rasterio.open(sc["assets"]["scl"]["href"])
    sc["_ds"] = (R, N, S)


def close_scene(sc):
    for d in sc.pop("_ds", ()) or ():
        try: d.close()
        except Exception: pass


def scan(park_id, bbox_filter, days, verify_days):
    from shapely.geometry import shape, Point
    from shapely.strtree import STRtree

    tiles, wpts = load_corridor(park_id, bbox_filter)
    print(f"{park_id}: {len(tiles)} corridor tiles, {len(wpts)} waterway pts",
          file=sys.stderr)
    if not tiles:
        return None
    lons = [xi*TILE for xi, yi in tiles]; lats = [yi*TILE for xi, yi in tiles]
    bbox = (min(lons)-TILE, min(lats)-TILE, max(lons)+2*TILE, max(lats)+2*TILE)
    wtree = STRtree([Point(p) for p in wpts])

    end = datetime.date.today()
    dt = f"{end - datetime.timedelta(days=days)}T00:00:00Z/{end}T23:59:59Z"
    scenes = stac_scenes(bbox, dt)
    print(f"{len(scenes)} scenes in {dt}", file=sys.stderr)
    for sc in scenes:
        sc["_geom"] = shape(sc["geometry"])

    # ---- detection pass: newest clear data per tile
    todo = dict.fromkeys(sorted(tiles))     # tile -> None until read OK
    cands = []
    for sc in scenes:
        mine = [t for t, v in todo.items() if v is None and
                sc["_geom"].contains(Point((t[0]+0.5)*TILE, (t[1]+0.5)*TILE))]
        if not mine:
            continue
        open_scene(sc)
        got = 0
        date = sc["properties"]["datetime"][:10]
        for t in mine:
            tb = (t[0]*TILE, t[1]*TILE, (t[0]+1)*TILE, (t[1]+1)*TILE)
            try:
                r = read_tile(sc, tb)
            except Exception as ex:
                print(f"  ERR tile {t}: {str(ex)[:60]}", file=sys.stderr)
                continue
            if r is None:
                continue
            todo[t] = date
            got += 1
            for c in clusters_in_tile(r):
                idx = int(wtree.nearest(Point(c["lon"], c["lat"])))
                c["water_km"] = round(km((c["lon"], c["lat"]), wpts[idx]), 2)
                c["scene"] = sc["id"]; c["date"] = date
                cands.append(c)
        close_scene(sc)
        print(f"  {sc['id']} tiles={got} cands_total={len(cands)}", file=sys.stderr)
        if all(v is not None for v in todo.values()):
            break
    scanned = sum(1 for v in todo.values() if v is not None)

    # near-water filter + dedupe (200m)
    cands = [c for c in cands if c["water_km"] <= 1.0]
    cands.sort(key=lambda c: -c["px"])
    kept = []
    for c in cands:
        if any(km((c["lon"], c["lat"]), (k["lon"], k["lat"])) < 0.2 for k in kept):
            continue
        kept.append(c)
    print(f"detection: {scanned}/{len(tiles)} tiles read, {len(kept)} near-water candidates",
          file=sys.stderr)

    kept = kept[:400]   # cap verification work; largest clusters first

    def recheck_pass(cands, dt_range, result_key, date_key, skip_same=True):
        """For each candidate, find max bright-bare px in an older scene.
        Groups work per scene: one scene open serves all its candidates."""
        scs = stac_scenes(bbox, dt_range)
        for sc in scs:
            sc["_geom"] = shape(sc["geometry"])
        pending = {id(c): c for c in cands}
        for sc in scs:
            if not pending:
                break
            date = sc["properties"]["datetime"][:10]
            mine = [c for c in pending.values()
                    if sc["_geom"].contains(Point(c["lon"], c["lat"]))
                    and not (skip_same and (sc["id"] == c["scene"] or date >= c["date"]))]
            if not mine:
                continue
            open_scene(sc)
            for c in mine:
                d = 0.003
                tb = (c["lon"]-d, c["lat"]-d, c["lon"]+d, c["lat"]+d)
                try:
                    r = read_tile(sc, tb)
                except Exception:
                    r = None
                if r is None:
                    continue
                cl = clusters_in_tile(r)
                c[result_key] = max((x["px"] for x in cl), default=0)
                c[date_key] = date
                del pending[id(c)]
            close_scene(sc)

    # ---- persistence pass: verify each candidate in an older scene
    verify_end = end - datetime.timedelta(days=10)
    dtv = f"{verify_end - datetime.timedelta(days=verify_days)}T00:00:00Z/{verify_end}T23:59:59Z"
    recheck_pass(kept, dtv, "verify_px", "verify_date")
    sites = []
    for c in kept:
        vp = c.get("verify_px")
        if vp is None:
            c["persistent"] = None    # couldn't verify (clouds) — keep, low conf
        elif vp >= MIN_PX // 2:
            c["persistent"] = True
        else:
            c["persistent"] = False
        if c["persistent"] is not False:
            sites.append(c)
    print(f"persistence: {len(sites)}/{len(kept)} kept", file=sys.stderr)

    # ---- history pass: was this bare a year+ ago? (village/outcrop vs new mine)
    yr_end = end - datetime.timedelta(days=330)
    dth = f"{yr_end - datetime.timedelta(days=270)}T00:00:00Z/{yr_end}T23:59:59Z"
    for c in sites:
        c["historical_px"] = None
    recheck_pass(sites, dth, "historical_px", "historical_date", skip_same=False)

    # ---- score
    for c in sites:
        score = 0.30
        if c["px"] >= 25: score += 0.10
        if c["px"] >= 80: score += 0.10
        if c["water_km"] <= 0.3: score += 0.10
        if c.get("pond_px", 0) >= 10: score += 0.10
        if c["persistent"]: score += 0.10
        h = c.get("historical_px")
        if h is not None:
            if h < max(MIN_PX, c["px"] * 0.3):
                score += 0.20               # new since last year — strong mining signal
                c["new_since"] = c.get("historical_date")
            elif h >= c["px"] * 0.8:
                score -= 0.15               # long-standing bare (village/outcrop)
        c["score"] = round(min(max(score, 0.05), 0.95), 2)
        c["area_ha"] = round(c["px"] * 0.01, 2)
    sites.sort(key=lambda c: -c["score"])
    return {"park_id": park_id,
            "scanned_at": datetime.datetime.now(datetime.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "datetime_range": dt, "bbox_filter": bbox_filter,
            "tiles_total": len(tiles), "tiles_scanned": scanned,
            "sites": sites}


STATE_FILE = f"{OUT_DIR}/state.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--park")
    ap.add_argument("--rotate", action="store_true",
                    help="scan most-stale park with a cached waterway extract")
    ap.add_argument("--bbox", help="lon0,lat0,lon1,lat1 corridor filter")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--verify-days", type=int, default=120,
                    help="lookback for the persistence pass")
    args = ap.parse_args()
    park = args.park
    if not park and args.rotate:
        state = {}
        try: state = json.load(open(STATE_FILE))
        except Exception: pass
        cand = [f[:-8] for f in sorted(os.listdir(WATERWAY_CACHE))
                if f.endswith(".geojson")]
        cand.sort(key=lambda pid: state.get(pid, {}).get("scanned_at", ""))
        if not cand:
            print("no waterway extracts yet", file=sys.stderr); return
        park = cand[0]
    if not park:
        ap.error("need --park or --rotate")
    bbox = [float(x) for x in args.bbox.split(",")] if args.bbox else None
    res = scan(park, bbox, args.days, args.verify_days)
    if not res:
        print("no corridor tiles", file=sys.stderr); return
    os.makedirs(OUT_DIR, exist_ok=True)
    if args.rotate or not args.bbox:
        state = {}
        try: state = json.load(open(STATE_FILE))
        except Exception: pass
        state[park] = {"scanned_at": res["scanned_at"],
                       "n_sites": len(res["sites"])}
        json.dump(state, open(STATE_FILE, "w"), indent=1)
    path = f"{OUT_DIR}/{park}.json"
    # merge: keep sites from previous scans of other bbox areas
    if os.path.exists(path) and bbox:
        try:
            old = json.load(open(path))
            keep = [s for s in old.get("sites", [])
                    if not (bbox[0] <= s["lon"] <= bbox[2] and bbox[1] <= s["lat"] <= bbox[3])]
            res["sites"] = sorted(res["sites"] + keep, key=lambda c: -c["score"])
        except Exception:
            pass
    json.dump(res, open(path, "w"), indent=1)
    print(f"{len(res['sites'])} pit sites -> {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
