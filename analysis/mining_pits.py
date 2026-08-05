"""Mining-pit RANKER — scores drainage-corridor pixels across a park's basin.

Not a detector with a yes/no threshold. Measured AUC of the best available
spectral evidence is ~0.8 (docs/MINING_FINDINGS_2026-08.md §2), which supports
a ranked worklist for a human to check and nothing stronger. Output must not be
surfaced in the UI as "mining sites" until scripts/eval_mining_detector.py
reports defensible precision — the previous version's 7,725 "sites" were 0.1%
consistent with visited-mine truth, and its top hits were sandbanks and rice
paddies.

What changed from the version that produced data/mining_pits/*.json:

  extent    park bbox            -> contributing basin U park, clipped 200 km
                                    (flow_corridor.scan_geom; the 8 confirmed
                                    Chinko pits are 123 km OUTSIDE the park)
  corridor  OSM waterways        -> D8 flow accumulation on GLO-30
                                    (nearest OSM waterway to those pits: 49 km)
  evidence  1 newest scene       -> 3-year dry-season median composite
                                    (AUC 0.758 -> 0.806)
  decision  red>1400 & ndvi<0.35 -> score >= Nth percentile of THIS basin's own
                                    seasonal distribution (absolute thresholds
                                    had recall 0/8 on the manual truth pits)
  score     hand-set bonuses inc. -> landscape percentile of the weighted
            +0.20 "new since"       feature score; history is metadata only
  output    kept[:400] + cap      -> top-N of the whole basin, spatially even

Spectral core (composites, features, calibration) lives in mining_features.py so
the scanner and the evaluator cannot drift apart.

Output: data/mining_candidates/{park}.json  (NOT data/mining_pits/, which is the
old discredited output that srv/turbidity.go still reads; do not overwrite it.)

Usage:
  python3 analysis/mining_pits.py --park CAF_Chinko
  python3 analysis/mining_pits.py --park CAF_Chinko --max-tiles 60 --verbose
  python3 analysis/mining_pits.py --park CAF_Chinko --scope park --corridor osm
"""
import argparse, datetime, json, math, os, sys, urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mining_features as mf

VERSION = "pits-v2-basin-ranker"
STAC = "https://earth-search.aws.element84.com/v1/search"
OUT_DIR = "data/mining_candidates"
LEGACY_OUT_DIR = "data/mining_pits"     # discredited; read-only reference
WATERWAY_CACHE = "data/osm_raw/waterways"
TILE = 0.05           # deg, scoring tile size
MIN_PX = 4            # 0.04 ha at 10 m; truth pits are 1-3 px shafts in a
                      # cluster, so this is as low as connected-components
                      # can go before single-pixel noise dominates
MAX_PX = 3000         # skip towns / big natural bare pans
MAX_PER_TILE = 12     # a 0.05-deg tile with more "top 0.5%" blobs than this is
                      # a bare landscape, not a set of pits
TOP_PCT = 99.5        # score cut, as a percentile of the basin's own season
TOP_N = 200           # candidates reported per basin

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

    Extent is `flow_corridor.scan_geom(park_id, scope)` = the park's
    contributing watershed (park_basins, migration 039) clipped to 200 km from
    the park, UNION the park polygon. Basin-only would skip most of a
    divide-top or endorheic park (see that docstring); park-only cannot express
    upstream pressure at all, which was the original bug.

    `use_osm=True` restores the legacy behaviour for A/B comparison only.
    """
    from shapely.geometry import box
    if use_osm:
        return _load_corridor_osm(park_id, bbox)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import flow_corridor as fc

    geom = fc.scan_geom(park_id, scope)
    if geom is None:
        return set(), []
    if fc.basin_geom(park_id) is None:
        print(f"{park_id}: no cached basin, park polygon only "
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


def clusters_in_tile(comp, cal, cut_pct=TOP_PCT):
    """Top-scoring connected blobs in one composite -> candidate dicts.

    This is the ranker. There is no absolute brightness threshold: a blob is
    "the top `100 - cut_pct`% of scores in this landscape/season", where the
    reference distribution is the basin-wide calibration sample. RED_MIN=1400 /
    NDVI_MAX=0.35 used to live here and had recall 0/8 on the manual truth pits
    (docs/MINING_FINDINGS_2026-08.md §2): those are 1-3 px hand-dug shafts with
    red 911-1242, not multi-hectare wash plains.
    """
    from scipy import ndimage
    from rasterio.warp import transform as rio_transform
    F = mf.features(comp)
    score = cal.score(F)
    if score is None:
        return []
    thr = cal.score_threshold(cut_pct)
    mask = np.isfinite(score) & (score >= thr)
    # water is bright in rb but is not a pit; MNDWI > 0.2 is open water
    mask &= ~(F["_mndwi"] > 0.2)
    if not mask.any():
        return []
    # ponds: a working alluvial pit has standing water beside it
    pond = (F["_mndwi"] > 0.0) & (F["_ndvi"] < 0.2)
    lab, n = ndimage.label(mask)
    if n == 0:
        return []
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    out = []
    for i in np.argsort(-sizes) + 1:
        m = lab == i
        s = int(m.sum())
        if s < MIN_PX:
            break            # sizes are descending: nothing smaller qualifies
        if s > MAX_PX:
            continue         # town / big natural bare pan
        cy, cx = ndimage.center_of_mass(m)
        x, y = comp["_tr"] * (cx, cy)
        lon, lat = rio_transform(comp["_crs"], "EPSG:4326", [x], [y])
        ys, xs = np.where(m)
        pad = 15  # 150 m
        y0, y1 = max(0, ys.min() - pad), min(mask.shape[0], ys.max() + pad)
        x0, x1 = max(0, xs.min() - pad), min(mask.shape[1], xs.max() + pad)
        sv = score[m]
        out.append({
            "px": s, "lat": round(lat[0], 5), "lon": round(lon[0], 5),
            "pond_px": int(pond[y0:y1, x0:x1].sum()),
            "score_raw": round(float(np.nanmean(sv)), 2),
            "score_max_raw": round(float(np.nanmax(sv)), 2),
            "feat_pct": {k: round(float(np.nanmedian(cal.pct(k, F[k][m]))), 1)
                         for k in mf.FEATURES},
        })
        if len(out) >= MAX_PER_TILE:
            break
    return out


def tile_bounds(t):
    return (t[0] * TILE, t[1] * TILE, (t[0] + 1) * TILE, (t[1] + 1) * TILE)


def history_px(comp_hist, cal, lon, lat, cut_pct=TOP_PCT, half_deg=0.003):
    """How many top-scoring px sat at (lon, lat) in an older composite."""
    if comp_hist is None:
        return None
    from rasterio.warp import transform as rio_transform
    F = mf.features(comp_hist)
    score = cal.score(F)
    thr = cal.score_threshold(cut_pct)
    x, y = rio_transform("EPSG:4326", comp_hist["_crs"], [lon], [lat])
    col, row = ~comp_hist["_tr"] * (x[0], y[0])
    r, c = int(row), int(col)
    d = int(half_deg * 111000 / 10)          # ~33 px
    sub = score[max(0, r - d):r + d + 1, max(0, c - d):c + d + 1]
    if sub.size == 0:
        return None
    return int((np.isfinite(sub) & (sub >= thr)).sum())


def scan(park_id, bbox_filter=None, top_n=TOP_N, cut_pct=TOP_PCT,
         scope="basin", use_osm=False, min_pct=None, years=3,
         calib_tiles=25, max_tiles=None, history=True, verbose=False):
    """Rank drainage-corridor tiles in the park's basin by mining-pit score.

    Shape of the answer changed with the rescope: this returns the top `top_n`
    candidates of the whole basin, ordered, with the numbers that produced the
    order. It does NOT return "the mines" - measured AUC is ~0.8, which supports
    a ranked worklist and nothing stronger (docs/MINING_FINDINGS_2026-08.md §6).
    Precision must be established by scripts/eval_mining_detector.py before any
    of this is shown in the UI.
    """
    from shapely.geometry import Point
    from shapely.strtree import STRtree

    tiles, wpts = load_corridor(park_id, bbox_filter, min_pct=min_pct,
                                use_osm=use_osm, scope=scope)
    print(f"{park_id}: {len(tiles)} corridor tiles, {len(wpts)} drainage pts",
          file=sys.stderr)
    if not tiles:
        return None
    tiles = sorted(tiles)
    if max_tiles and len(tiles) > max_tiles:
        # deterministic thinning, not truncation: a spatially even subsample so
        # a partial scan still covers the whole basin (the old code kept
        # kept[:400], which biased everything to one corner)
        step = len(tiles) / float(max_tiles)
        tiles = [tiles[int(i * step)] for i in range(max_tiles)]
        print(f"  thinned to {len(tiles)} tiles (--max-tiles)", file=sys.stderr)
    wtree = STRtree([Point(p) for p in wpts]) if wpts else None

    lat0 = sum(t[1] for t in tiles) / len(tiles) * TILE
    dts = mf.dry_season_windows(lat0, n_years=years)
    hist_dts = mf.dry_season_windows(lat0, n_years=1,
                                     end=datetime.date.today()
                                     - datetime.timedelta(days=365 * 3))
    print(f"  dry-season windows: {dts}", file=sys.stderr)

    # ---- calibration: what does this landscape look like, this season?
    cal = mf.calibrate([tile_bounds(t) for t in tiles], dts,
                       max_tiles=min(calib_tiles, len(tiles)), verbose=verbose)
    if cal is None or not cal.breaks:
        print(f"{park_id}: calibration failed (no clear scenes)", file=sys.stderr)
        return None
    thr = cal.score_threshold(cut_pct)
    print(f"  calibrated on {cal.n} px, {len(cal.dates)} dates; "
          f"score cut at pct {cut_pct} = {thr:.1f}", file=sys.stderr)

    # ---- scoring pass
    cands, scanned = [], 0
    for k, t in enumerate(tiles, 1):
        tb = tile_bounds(t)
        try:
            comp = mf.median_composite(tb, dts, verbose=verbose)
        except Exception as ex:
            print(f"  ERR tile {t}: {str(ex)[:70]}", file=sys.stderr)
            continue
        if comp is None:
            continue
        scanned += 1
        got = clusters_in_tile(comp, cal, cut_pct)
        for c in got:
            c["tile"] = list(t)
            c["dates"] = comp["_dates"]
            if wtree is not None:
                idx = int(wtree.nearest(Point(c["lon"], c["lat"])))
                c["water_km"] = round(km((c["lon"], c["lat"]), wpts[idx]), 2)
            cands.append(c)
        if verbose or k % 25 == 0:
            print(f"  [{k}/{len(tiles)}] {t} +{len(got)} "
                  f"(total {len(cands)})", file=sys.stderr)

    # dedupe at 200 m, best score wins
    cands.sort(key=lambda c: -c["score_raw"])
    kept = []
    for c in cands:
        if any(km((c["lon"], c["lat"]), (k2["lon"], k2["lat"])) < 0.2
               for k2 in kept):
            continue
        kept.append(c)
    print(f"scoring: {scanned}/{len(tiles)} tiles composited, "
          f"{len(kept)} distinct candidates", file=sys.stderr)

    # ---- rank, then keep the top N. Primary key is the score's percentile in
    # this landscape; the two modifiers are deliberately sub-percentile-point
    # tiebreakers, so they can reorder near-ties but can never promote a weak
    # candidate. (An earlier version added +0.02 to a 0-1 score and clamped at
    # 1.0, which made the whole top of the list read "1.0" - the same failure as
    # the old +0.20 "new since" bonus that put 90% of sites above 0.7.)
    for c in kept:
        c["score_pct"] = round(float(cal.score_pct(c["score_raw"])), 3)
        r = c["score_pct"]
        if c.get("water_km") is not None and c["water_km"] <= 0.3:
            r += 0.02
        if c.get("pond_px", 0) >= 10:
            r += 0.02
        c["rank_score"] = round(r, 4)
        c["area_ha"] = round(c["px"] * 0.01, 2)
    # score_pct saturates at 100 (Calibration.QS tops out there and its finest
    # step is 0.05), so break ties with the raw score, which is unbounded.
    kept.sort(key=lambda c: (-c["rank_score"], -c["score_max_raw"],
                             -c["score_raw"], -c["px"]))
    sites = kept[:top_n]

    # ---- history: reported as metadata, never as score. "Bare 3 years ago
    # too" distinguishes an outcrop/village from a new working, but we have not
    # measured how well, so it does not move the ranking.
    if history and sites:
        by_tile = {}
        for c in sites:
            by_tile.setdefault(tuple(c["tile"]), []).append(c)
        for t, cs in by_tile.items():
            try:
                hc = mf.median_composite(tile_bounds(t), hist_dts,
                                         max_scenes=4, verbose=verbose)
            except Exception:
                hc = None
            for c in cs:
                c["historical_px"] = history_px(hc, cal, c["lon"], c["lat"],
                                                cut_pct)
                c["historical_dates"] = (hc or {}).get("_dates")
                h = c["historical_px"]
                c["new_since"] = (None if h is None
                                  else h < max(MIN_PX, c["px"] * 0.3))

    return {"park_id": park_id, "version": VERSION,
            "scanned_at": datetime.datetime.now(datetime.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scope": scope, "corridor": "osm" if use_osm else "flowacc",
            "dry_season_windows": dts, "historical_windows": hist_dts,
            "cut_pct": cut_pct, "score_cut": round(thr, 2),
            "bbox_filter": bbox_filter,
            "tiles_total": len(tiles), "tiles_scanned": scanned,
            "candidates_before_topn": len(kept),
            "calibration": {"n_px": int(cal.n), "dates": cal.dates,
                            "weights": mf.WEIGHTS},
            "sites": sites}


STATE_FILE = f"{OUT_DIR}/state.json"


def main():
    ap = argparse.ArgumentParser(
        description="Rank mining-pit candidates in a park's contributing basin")
    ap.add_argument("--park")
    ap.add_argument("--parks", help="comma separated")
    ap.add_argument("--rotate", action="store_true",
                    help="scan the most-stale park that has a cached basin")
    ap.add_argument("--bbox", help="lon0,lat0,lon1,lat1 filter within the basin")
    ap.add_argument("--scope", choices=("basin", "park", "strict"),
                    default="basin",
                    help="basin U park (default) | park only | basin only")
    ap.add_argument("--corridor", choices=("flowacc", "osm"), default="flowacc",
                    help="osm = legacy corridor, for A/B only")
    ap.add_argument("--min-pct", type=float, default=None,
                    help="flow-accumulation percentile cut (default 94)")
    ap.add_argument("--cut-pct", type=float, default=TOP_PCT,
                    help="score percentile cut within the basin's season")
    ap.add_argument("--top-n", type=int, default=TOP_N)
    ap.add_argument("--years", type=int, default=3,
                    help="dry seasons in the median composite")
    ap.add_argument("--calib-tiles", type=int, default=25)
    ap.add_argument("--max-tiles", type=int, default=None,
                    help="thin the corridor evenly to this many tiles")
    ap.add_argument("--no-history", dest="history", action="store_false")
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    want = []
    if args.park:
        want.append(args.park)
    if args.parks:
        want += [x for x in args.parks.split(",") if x]
    if not want and args.rotate:
        state = {}
        try:
            state = json.load(open(f"{args.out_dir}/state.json"))
        except Exception:
            pass
        import sqlite3
        con = sqlite3.connect("db.sqlite3")
        cand = [r[0] for r in con.execute(
            "SELECT park_id FROM park_basins WHERE kind='upstream' "
            "ORDER BY park_id")]
        con.close()
        if not cand:
            print("no basins yet: scripts/fetch_park_basins.py --all",
                  file=sys.stderr)
            return
        cand.sort(key=lambda pid: state.get(pid, {}).get("scanned_at", ""))
        want = [cand[0]]
    if not want:
        ap.error("need --park/--parks or --rotate")

    bbox = [float(x) for x in args.bbox.split(",")] if args.bbox else None
    os.makedirs(args.out_dir, exist_ok=True)
    for park in want:
        res = scan(park, bbox, top_n=args.top_n, cut_pct=args.cut_pct,
                   scope=args.scope, use_osm=args.corridor == "osm",
                   min_pct=args.min_pct, years=args.years,
                   calib_tiles=args.calib_tiles, max_tiles=args.max_tiles,
                   history=args.history, verbose=args.verbose)
        if not res:
            print(f"{park}: nothing scanned", file=sys.stderr)
            continue
        path = f"{args.out_dir}/{park}.json"
        # No merging with previous output: this is a ranking of one scan under
        # one calibration, and merging rankings from different calibrations
        # (different seasons, different reference distributions) produces an
        # order that means nothing. Use --bbox for exploration, not accretion.
        json.dump(res, open(path, "w"), indent=1)
        state = {}
        try:
            state = json.load(open(f"{args.out_dir}/state.json"))
        except Exception:
            pass
        state[park] = {"scanned_at": res["scanned_at"],
                       "version": res["version"],
                       "n_sites": len(res["sites"]),
                       "tiles_scanned": res["tiles_scanned"],
                       "tiles_total": res["tiles_total"]}
        json.dump(state, open(f"{args.out_dir}/state.json", "w"), indent=1)
        print(f"{len(res['sites'])} ranked candidates -> {path}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
