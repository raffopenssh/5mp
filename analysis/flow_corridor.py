"""Drainage corridor from DEM flow accumulation (replaces OSM waterways).

Why: `data/osm_raw/waterways/{park}.geojson` is OSM waterways clipped to the
park bbox. In the Chinko headwaters the nearest cached waterway vertex to the 8
field-confirmed artisanal pits is **48.9 km** away — OSM simply has no mapped
stream there (docs/MINING_FINDINGS_2026-08.md §1, §7). D8 flow accumulation on
Copernicus GLO-30 puts those same 8 pits at flow-accumulation percentile
93.9–99.6 of their local window (§6). Terrain knows where the water goes even
when no mapper has been there.

Design notes
------------
* Copernicus DEM is read as COGs straight off the open S3 bucket
  (`AWS_NO_SIGN_REQUEST=YES` is set here, not left to the caller).
* Work is done per 1° DEM tile with a HALO_PX buffer so drainage entering the
  tile is not truncated; only the un-haloed interior is returned.
* Accumulation is scored as a **local percentile** (rank within the tile after
  masking no-data), because absolute accumulation is meaningless across
  climates and basin sizes — and because that is exactly the statistic the 8
  truth pits were measured against.
* Results cached as GeoTIFF under data/flowacc/ (float32 percentile 0-100),
  so repeat scans and the evaluator are free.

Public API:
    acc_percentile(lon, lat)               -> float 0-100 (single point)
    tile_percentile(lon, lat)              -> (arr, transform)  cached
    corridor_cells(geom, res, min_pct)     -> {(xi, yi)} scan tiles
    corridor_points(geom, min_pct, ...)    -> [(lon, lat), ...] drainage pixels

CLI:
    python3 analysis/flow_corridor.py --validate           # 8 truth pits
    python3 analysis/flow_corridor.py --park CAF_Chinko    # corridor stats
"""
import argparse, json, math, os, sqlite3, sys

import numpy as np

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_CACHE_SIZE", "200000000")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db.sqlite3")
CACHE = os.path.join(BASE, "data", "flowacc")
GLO30 = ("https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/"
         "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
         "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")
GLO90 = ("https://copernicus-dem-90m.s3.eu-central-1.amazonaws.com/"
         "Copernicus_DSM_COG_30_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
         "Copernicus_DSM_COG_30_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")

SUBTILE = 0.25        # deg; D8 is run on 0.25-deg blocks (~28 km)
HALO = 0.05           # deg of overlap so drainage is not cut at block edges
DEFAULT_PCT = 94.0    # min flow-acc percentile to count as drainage corridor
                      # (lower bound of the measured truth-pit range 93.9-99.6)
SNAP_PX = 2           # ~60 m: a pit is dug BESIDE the channel, so score the
                      # best drainage cell within 2 px. Measured on the 8 truth
                      # pits: snap 0 px -> 20-98 pct (median 67, useless);
                      # 1 px -> 69-99.6; 2 px -> 94.2-99.7 (median 97.8), which
                      # reproduces the 93.9-99.6 in the findings doc; 8 px ->
                      # 99.6+ for everything, i.e. the statistic stops
                      # discriminating. 2 px it is.


def _dem_url(lon, lat, res30=True):
    lo, la = math.floor(lon), math.floor(lat)
    t = GLO30 if res30 else GLO90
    return t.format(ns="N" if la >= 0 else "S", lat=abs(la),
                    ew="E" if lo >= 0 else "W", lon=abs(lo))


def _block_key(lon, lat, res30):
    xi = math.floor(lon / SUBTILE)
    yi = math.floor(lat / SUBTILE)
    return (xi, yi, 30 if res30 else 90)


def _read_dem(bbox, res30=True):
    """DEM window for bbox=(w,s,e,n). Stitches across 1-deg tile seams."""
    import rasterio
    from rasterio.windows import from_bounds
    w, s, e, n = bbox
    urls = {}
    for lo in range(math.floor(w), math.floor(e) + 1):
        for la in range(math.floor(s), math.floor(n) + 1):
            urls[(lo, la)] = _dem_url(lo + 0.5, la + 0.5, res30)
    out = None
    tr = None
    for (lo, la), url in sorted(urls.items()):
        try:
            d = rasterio.open(url)
        except Exception as ex:
            print(f"  DEM miss {lo},{la}: {str(ex)[:60]}", file=sys.stderr)
            continue
        with d:
            px = d.transform.a
            if out is None:
                W = int(round((e - w) / px))
                H = int(round((n - s) / px))
                out = np.full((H, W), np.nan, np.float32)
                from affine import Affine
                tr = Affine(px, 0, w, 0, -px, n)
            win = from_bounds(max(w, lo), max(s, la), min(e, lo + 1),
                              min(n, la + 1), d.transform)
            a = d.read(1, window=win, boundless=True, fill_value=-32768.0)
            a = a.astype(np.float32)
            a[a < -1000] = np.nan
            # paste into out at the right offset
            c0 = int(round((max(w, lo) - w) / px))
            r0 = int(round((n - min(n, la + 1)) / px))
            h, wd = a.shape
            r1, c1 = min(r0 + h, out.shape[0]), min(c0 + wd, out.shape[1])
            if r1 > r0 and c1 > c0:
                sub = a[:r1 - r0, :c1 - c0]
                tgt = out[r0:r1, c0:c1]
                m = np.isfinite(sub)
                tgt[m] = sub[m]
    return out, tr


def _d8_accumulation(dem):
    """D8 flow accumulation (in cells) on `dem` (NaN = nodata).

    Own implementation rather than pysheds: pysheds 0.5 calls `np.in1d`, which
    NumPy 2 removed, and its Grid wants a CRS-carrying viewfinder. The pieces
    here are the standard ones:

      1. depression filling by morphological reconstruction-by-erosion
         (skimage, C-speed) — the fast equivalent of priority-flood fill;
      2. flat resolution: on filled flats every neighbour is equal, so D8 is
         undefined. We subtract EPS * (distance to the nearest cell that does
         have a lower neighbour), which creates a monotone downhill gradient
         across the flat toward its outlet;
      3. steepest-descent (D8) receivers, vectorised over the 8 offsets;
      4. accumulation by a single numba pass over cells in descending
         elevation, which is a valid topological order for a DAG of receivers.
    """
    from skimage.morphology import reconstruction
    from scipy import ndimage
    valid = np.isfinite(dem)
    if not valid.any():
        return np.zeros_like(dem, np.float32)
    z = dem.astype(np.float64)
    lo = np.nanmin(z)
    z = np.where(valid, z, lo - 1000.0)

    # 1. fill depressions: seed = +max inside, DEM on the border
    seed = np.full(z.shape, z.max(), np.float64)
    seed[0, :] = z[0, :]
    seed[-1, :] = z[-1, :]
    seed[:, 0] = z[:, 0]
    seed[:, -1] = z[:, -1]
    seed[~valid] = z[~valid]
    filled = reconstruction(seed, z, method="erosion")

    # 2. resolve flats
    mn = ndimage.minimum_filter(filled, size=3, mode="nearest")
    has_lower = mn < filled
    if (~has_lower).any() and has_lower.any():
        d = ndimage.distance_transform_edt(~has_lower)
        span = max(filled.max() - filled.min(), 1.0)
        filled = filled - (span * 1e-9) * d

    # 3. D8 receivers
    H, W = filled.shape
    big = -1e18
    pad = np.full((H + 2, W + 2), big)
    pad[1:-1, 1:-1] = filled
    best_slope = np.zeros((H, W))
    recv = np.full((H, W), -1, np.int64)
    for dr, dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1),
                   (1, -1), (1, 0), (1, 1)):
        nb = pad[1 + dr:1 + dr + H, 1 + dc:1 + dc + W]
        slope = (filled - nb) / math.hypot(dr, dc)
        better = (slope > best_slope) & (nb > big / 2)
        best_slope = np.where(better, slope, best_slope)
        rr = np.arange(H)[:, None] + dr
        cc = np.arange(W)[None, :] + dc
        idx = rr * W + cc
        recv = np.where(better, idx, recv)
    recv[~valid] = -1

    # 4. accumulate in descending elevation order
    order = np.argsort(-np.where(valid, filled, -np.inf).ravel(),
                       kind="stable").astype(np.int64)
    acc = valid.astype(np.float32).ravel()
    _accumulate(order, recv.ravel(), acc, int(valid.sum()))
    return acc.reshape(H, W)


try:
    from numba import njit

    @njit(cache=True)
    def _accumulate(order, recv, acc, n):
        for i in range(n):
            c = order[i]
            r = recv[c]
            if r >= 0:
                acc[r] += acc[c]
except Exception:                                     # pragma: no cover
    def _accumulate(order, recv, acc, n):
        for i in range(n):
            c = order[i]
            r = recv[c]
            if r >= 0:
                acc[r] += acc[c]


_mem = {}


def tile_percentile(lon, lat, res30=True, verbose=False):
    """Flow-accumulation percentile raster for the 0.25-deg block holding
    (lon, lat). Returns (arr float32 0-100, affine transform). Cached on disk."""
    key = _block_key(lon, lat, res30)
    if key in _mem:
        return _mem[key]
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "accpct_%s_%d_%d.tif" % (key[2], key[0], key[1]))
    import rasterio
    if os.path.exists(path):
        with rasterio.open(path) as d:
            _mem[key] = (d.read(1), d.transform)
        return _mem[key]
    xi, yi, _ = key
    w, s = xi * SUBTILE, yi * SUBTILE
    bbox = (w - HALO, s - HALO, w + SUBTILE + HALO, s + SUBTILE + HALO)
    dem, tr = _read_dem(bbox, res30)
    if dem is None:
        _mem[key] = None
        return None
    if verbose:
        print(f"  D8 on {dem.shape} block {key}", file=sys.stderr)
    acc = _d8_accumulation(dem)
    # percentile rank within the block (valid cells only)
    valid = np.isfinite(dem)
    flat = acc[valid]
    pct = np.zeros_like(acc, np.float32)
    if flat.size:
        ranks = flat.argsort().argsort().astype(np.float32)
        pct[valid] = ranks / max(1, flat.size - 1) * 100.0
    # crop the halo away
    px = tr.a
    off = int(round(HALO / px))
    pct = pct[off:pct.shape[0] - off, off:pct.shape[1] - off]
    from affine import Affine
    tr2 = Affine(px, 0, w, 0, -px, s + SUBTILE)
    with rasterio.open(path, "w", driver="GTiff", height=pct.shape[0],
                       width=pct.shape[1], count=1, dtype="float32",
                       crs="EPSG:4326", transform=tr2, compress="deflate",
                       predictor=2, tiled=True) as d:
        d.write(pct, 1)
    _mem[key] = (pct, tr2)
    return _mem[key]


def acc_percentile(lon, lat, res30=True, snap_px=SNAP_PX):
    """Best (max) flow-accumulation percentile within snap_px of the point."""
    t = tile_percentile(lon, lat, res30)
    if not t:
        return None
    arr, tr = t
    col, row = ~tr * (lon, lat)
    r, c = int(row), int(col)
    if not (0 <= r < arr.shape[0] and 0 <= c < arr.shape[1]):
        return None
    if snap_px <= 0:
        return float(arr[r, c])
    sub = arr[max(0, r - snap_px):r + snap_px + 1,
              max(0, c - snap_px):c + snap_px + 1]
    return float(sub.max()) if sub.size else None


def window_percentile(lon, lat, half_km=(5.0, 4.0), res30=True,
                      snap_px=SNAP_PX):
    """Percentile of the point's accumulation within a local ~10x8 km window —
    the exact statistic quoted in docs/MINING_FINDINGS_2026-08.md §6."""
    t = tile_percentile(lon, lat, res30)
    if not t:
        return None
    arr, tr = t
    col, row = ~tr * (lon, lat)
    r, c = int(row), int(col)
    if not (0 <= r < arr.shape[0] and 0 <= c < arr.shape[1]):
        return None
    px = tr.a
    dr = int(half_km[1] / 111.0 / px)
    dc = int(half_km[0] / (111.0 * math.cos(math.radians(lat))) / px)
    sub = arr[max(0, r - dr):r + dr + 1, max(0, c - dc):c + dc + 1]
    snap = arr[max(0, r - snap_px):r + snap_px + 1,
               max(0, c - snap_px):c + snap_px + 1]
    v = float(snap.max()) if snap.size else float(arr[r, c])
    return float((sub <= v).mean() * 100.0)


# ------------------------------------------------------------ corridor lookups
def basin_geom(park_id):
    """Contributing basin polygon from park_basins (migration 039)."""
    from shapely.geometry import shape
    con = sqlite3.connect(DB)
    row = con.execute("SELECT geojson FROM park_basins WHERE park_id=? AND "
                      "kind='upstream'", (park_id,)).fetchone()
    con.close()
    if not row:
        return None
    return shape(json.loads(row[0]))


def corridor_cells(geom, res=0.05, min_pct=DEFAULT_PCT, res30=True,
                   verbose=False):
    """Scan tiles of size `res` deg that contain drainage above min_pct.

    Returns {(xi, yi): max_pct}. Blocks are visited only where they intersect
    `geom`, so a 30,000 km2 basin costs ~500 D8 solves at 0.25 deg.
    """
    from shapely.geometry import box
    w, s, e, n = geom.bounds
    out = {}
    bx0, by0 = math.floor(w / SUBTILE), math.floor(s / SUBTILE)
    bx1, by1 = math.floor(e / SUBTILE), math.floor(n / SUBTILE)
    nblocks = (bx1 - bx0 + 1) * (by1 - by0 + 1)
    done = 0
    for bxi in range(bx0, bx1 + 1):
        for byi in range(by0, by1 + 1):
            bb = box(bxi * SUBTILE, byi * SUBTILE,
                     (bxi + 1) * SUBTILE, (byi + 1) * SUBTILE)
            if not geom.intersects(bb):
                continue
            done += 1
            t = tile_percentile((bxi + 0.5) * SUBTILE, (byi + 0.5) * SUBTILE,
                                res30, verbose)
            if not t:
                continue
            arr, tr = t
            rows, cols = np.where(arr >= min_pct)
            if verbose:
                print(f"  block {bxi},{byi} [{done}/{nblocks}] "
                      f"{len(rows)} drainage px", file=sys.stderr)
            for r, c in zip(rows, cols):
                lon, lat = tr * (float(c) + 0.5, float(r) + 0.5)
                cell = (int(math.floor(lon / res)), int(math.floor(lat / res)))
                v = float(arr[r, c])
                if v > out.get(cell, 0):
                    out[cell] = v
    # keep only tiles whose centre is in the basin (or that the basin crosses)
    keep = {}
    for (xi, yi), v in out.items():
        if geom.intersects(box(xi * res, yi * res, (xi + 1) * res,
                               (yi + 1) * res)):
            keep[(xi, yi)] = v
    return keep


def corridor_points(bbox, min_pct=DEFAULT_PCT, res30=True, stride=3):
    """Drainage pixel centres (lon, lat) in bbox — the replacement for OSM
    waterway vertices in distance-to-water checks."""
    w, s, e, n = bbox
    pts = []
    for bxi in range(math.floor(w / SUBTILE), math.floor(e / SUBTILE) + 1):
        for byi in range(math.floor(s / SUBTILE), math.floor(n / SUBTILE) + 1):
            t = tile_percentile((bxi + 0.5) * SUBTILE, (byi + 0.5) * SUBTILE,
                                res30)
            if not t:
                continue
            arr, tr = t
            sub = arr[::stride, ::stride]
            rows, cols = np.where(sub >= min_pct)
            for r, c in zip(rows, cols):
                lon, lat = tr * (float(c * stride) + 0.5,
                                 float(r * stride) + 0.5)
                if w <= lon <= e and s <= lat <= n:
                    pts.append((lon, lat))
    return pts


# ------------------------------------------------------------------------ CLI
def validate():
    """Reproduce the §6 measurement on the 8 manual truth pits, and contrast
    with the OSM-waterway corridor the old detector used."""
    truth = json.load(open(os.path.join(
        BASE, "data", "mining_truth", "chinko_headwaters_manual.json")))
    print("site        acc_pct  acc_pct(10x8km window)  raw(no snap)")
    wins = []
    for s in truth["sites"]:
        b = acc_percentile(s["lon"], s["lat"])
        wp = window_percentile(s["lon"], s["lat"])
        raw = acc_percentile(s["lon"], s["lat"], snap_px=0)
        wins.append(wp)
        print(f"{s['id']:11s} {b:7.1f} {wp:22.1f} {raw:13.1f}")
    wins = [w for w in wins if w is not None]
    print(f"\nwindow percentile: min={min(wins):.1f} max={max(wins):.1f} "
          f"median={float(np.median(wins)):.1f}  (doc says 93.9-99.6)")
    print(f"pits at >= {DEFAULT_PCT} pct: {sum(w >= DEFAULT_PCT for w in wins)}"
          f"/{len(wins)}")

    # OSM comparison
    try:
        from shapely.geometry import Point, MultiLineString
        d = json.load(open(os.path.join(BASE, "data", "osm_raw", "waterways",
                                        "CAF_Chinko.geojson")))
        lines = [f["geometry"]["coordinates"] for f in d["features"]
                 if f["geometry"]["type"] == "LineString"]
        ml = MultiLineString(lines)
        ds = [ml.distance(Point(s["lon"], s["lat"])) * 111
              for s in truth["sites"]]
        print(f"nearest OSM waterway: min={min(ds):.1f} km "
              f"median={float(np.median(ds)):.1f} km")
    except Exception as ex:
        print("OSM comparison skipped:", str(ex)[:80])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--park")
    ap.add_argument("--min-pct", type=float, default=DEFAULT_PCT)
    ap.add_argument("--res", type=float, default=0.05)
    ap.add_argument("--glo90", action="store_true", help="use 90 m DEM")
    a = ap.parse_args()
    if a.validate:
        validate()
        return
    if a.park:
        g = basin_geom(a.park)
        if g is None:
            print(f"no upstream basin for {a.park}; run "
                  f"scripts/fetch_park_basins.py --park {a.park}",
                  file=sys.stderr)
            sys.exit(1)
        cells = corridor_cells(g, a.res, a.min_pct, not a.glo90, verbose=True)
        import statistics
        lat0 = statistics.fmean([g.bounds[1], g.bounds[3]])
        area = g.area * 111.0 * 111.0 * math.cos(math.radians(lat0))
        print(f"{a.park}: basin {area:.0f} km2 -> {len(cells)} "
              f"corridor tiles of {a.res} deg")


if __name__ == "__main__":
    main()
