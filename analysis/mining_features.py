"""Spectral core for mining-pit ranking: dry-season median composites, the four
measured-useful features, and scene-percentile calibration.

Shared deliberately between the scanner (analysis/mining_pits.py) and the
evaluator (scripts/eval_mining_detector.py) so that "what the detector sees" and
"what we measure precision on" cannot drift apart.

Why these features, and why percentiles
---------------------------------------
Measured AUC on two independent truth sets (docs/MINING_FINDINGS_2026-08.md §2 —
8 manual Chinko pits, and 40 IPIS field-visited gold sites):

    median red/blue over 5 dates   0.806      <- multi-date median wins
    BSI                            0.771
    red/blue (iron oxide)          0.758
    -NDVI                          0.754
    red                            0.727
    local-z variants               0.66-0.72  <- worse; do NOT reintroduce
    trend red 2026-2019            0.427      <- useless

So: multi-date **median** composite, the four plain features, and NO local-z.

Absolute thresholds are gone. `RED_MIN=1400 & NDVI_MAX=0.35` gave recall 0/40 on
the manual pits across 5 dates: they are 1-3 px hand-dug shafts in grass
(red 911-1242) not multi-hectare bare wash-plains (red 2317). A detector tuned
on regime 2 cannot see regime 1 at all. But naive local contrast fails too
(the local-z AUCs above), so we use the middle ground the doc identifies:
**scene-percentile** ranking. Feature values are mapped to their percentile
within a calibration sample drawn from the park's own basin in the same season,
then combined into one score. That is scale-free across climates and dates, and
it makes the output a *ranking* - which is all an AUC~0.8 signal supports.
"""
import datetime, json, math, os, sys, urllib.request

import numpy as np

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_CACHE_SIZE", "200000000")

STAC = "https://earth-search.aws.element84.com/v1/search"
BANDS = ("blue", "green", "red", "nir", "swir16", "swir22", "scl")
CLEAR_SCL = (4, 5, 6, 7, 11)      # veg, bare, water, unclassified, snow

# Feature weights from the measured AUCs above: w = AUC - 0.5, normalised.
# Kept explicit (not tuned) so any change is traceable to a measurement.
FEATURE_AUC = {"rb": 0.806, "bsi": 0.771, "neg_ndvi": 0.754, "red": 0.727}
FEATURES = tuple(FEATURE_AUC)
_w = {k: v - 0.5 for k, v in FEATURE_AUC.items()}
WEIGHTS = {k: v / sum(_w.values()) for k, v in _w.items()}


def dry_season_windows(lat, n_years=3, end=None):
    """Dry-season date ranges, hemisphere-aware.

    Bare ground is only separable from vegetation in the dry season, and the
    composite must not mix hemispheres' seasons. North of the equator the dry
    season is ~Dec-Mar; south of it, ~Jun-Sep.
    """
    end = end or datetime.date.today()
    out = []
    for k in range(n_years):
        y = end.year - k
        if lat >= 0:
            a, b = datetime.date(y - 1, 12, 1), datetime.date(y, 3, 25)
        else:
            a, b = datetime.date(y, 6, 1), datetime.date(y, 9, 25)
        if a > end:
            continue
        b = min(b, end)
        if b <= a:
            continue
        out.append(f"{a}T00:00:00Z/{b}T23:59:59Z")
    return out


def search_scenes(bbox, dt_range, max_cloud=25, limit=40, epsg=None):
    body = json.dumps({
        "collections": ["sentinel-2-l2a"], "bbox": list(bbox),
        "datetime": dt_range, "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": limit,
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
    }).encode()
    req = urllib.request.Request(STAC, data=body,
                                headers={"Content-Type": "application/json"})
    feats = json.load(urllib.request.urlopen(req, timeout=120))["features"]
    if epsg is not None:
        # Drop other-UTM-zone scenes from the *metadata*, before paying for any
        # COG range requests. A bbox near a zone boundary otherwise returns
        # mostly unusable scenes and we would discover that after reading 7
        # bands each (measured: 29 wasted scene reads on one Chinko tile).
        feats = [f for f in feats
                 if f["properties"].get("proj:epsg") in (None, epsg)]
    return feats


def scene_epsg(bbox, dt_range, max_cloud=25):
    """Modal proj:epsg over candidate scenes: the UTM zone to composite in."""
    try:
        feats = search_scenes(bbox, dt_range, max_cloud=max_cloud)
    except Exception:
        return None
    counts = {}
    for f in feats:
        e = f["properties"].get("proj:epsg")
        if e:
            counts[e] = counts.get(e, 0) + 1
    return max(counts, key=counts.get) if counts else None


def read_bands(sc, bbox, bands=BANDS, out_shape=None):
    """Read `bands` for bbox=(w,s,e,n) from one scene, co-registered to the
    10 m grid of the first band. Returns dict or None.

    `out_shape` forces the output grid, which matters when compositing: the same
    lat/lon bbox lands on a different pixel count in a different UTM zone (and
    even 1 px off in the same zone from rounding), and np.stack then refuses.
    """
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.warp import transform_bounds
    out, ref = {}, out_shape
    for b in bands:
        a = sc["assets"].get(b)
        if not a:
            return None
        with rasterio.open(a["href"]) as d:
            bb = transform_bounds("EPSG:4326", d.crs, *bbox)
            win = from_bounds(*bb, d.transform)
            rs = rasterio.enums.Resampling.nearest if b == "scl" \
                else rasterio.enums.Resampling.bilinear
            if ref is None:
                arr = d.read(1, window=win, boundless=True, fill_value=0)
                ref = arr.shape
            else:
                arr = d.read(1, window=win, boundless=True, fill_value=0,
                             out_shape=ref, resampling=rs)
            if "_crs" not in out:
                out["_crs"] = d.crs
                # transform must describe the grid we actually returned; when
                # out_shape forced a resample, the native window transform is
                # the wrong scale and every pixel->lon/lat is offset.
                h, w = arr.shape
                out["_tr"] = d.window_transform(win) * rasterio.Affine.scale(
                    (win.width or w) / w, (win.height or h) / h)
            out[b] = arr.astype(np.float32)
    return out


def median_composite(bbox, dt_ranges, max_scenes=6, min_clear=0.80,
                     max_cloud=25, verbose=False):
    """Per-pixel median over up to `max_scenes` clear dry-season scenes.

    Cloud/shadow pixels are excluded per scene via SCL before the median, so a
    single bad date cannot poison a pixel. Multi-date median measured 0.806 AUC
    vs 0.758 single-date - this is the single biggest detector-side win
    available (docs/MINING_FINDINGS_2026-08.md §2).
    """
    stacks, dates = {b: [] for b in BANDS if b != "scl"}, []
    geom = None
    shape_ref = None
    epsg = scene_epsg(bbox, dt_ranges[0], max_cloud) if dt_ranges else None
    for dt in dt_ranges:
        try:
            scenes = search_scenes(bbox, dt, max_cloud=max_cloud, epsg=epsg)
        except Exception as ex:
            if verbose:
                print(f"    STAC fail {dt}: {str(ex)[:60]}", file=sys.stderr)
            continue
        for sc in scenes:
            if len(dates) >= max_scenes:
                break
            try:
                c = read_bands(sc, bbox, out_shape=shape_ref)
            except Exception as ex:
                if verbose:
                    print(f"    read fail {sc['id']}: {str(ex)[:60]}",
                          file=sys.stderr)
                continue
            if c is None:
                continue
            # A bbox can straddle UTM zones / MGRS tiles; scenes in a different
            # CRS would need reprojection, and a per-pixel median across two
            # different grids is meaningless. Keep the first CRS, skip the rest
            # (the neighbouring 0.05-deg tile will be scanned in its own zone).
            if geom is not None and c["_crs"] != geom[0]:
                if verbose:
                    print(f"    skip {sc['id']}: CRS {c['_crs']} != {geom[0]}",
                          file=sys.stderr)
                continue
            valid = np.isin(c["scl"], CLEAR_SCL)
            if valid.mean() < min_clear or (c["red"] > 0).mean() < 0.5:
                continue
            for b in stacks:
                a = c[b].copy()
                a[~valid] = np.nan
                stacks[b].append(a)
            dates.append(sc["properties"]["datetime"][:10])
            geom = (c["_crs"], c["_tr"])
            shape_ref = c["red"].shape
        if len(dates) >= max_scenes:
            break
    if not dates:
        return None
    comp = {}
    with np.errstate(invalid="ignore"):
        for b, arrs in stacks.items():
            comp[b] = np.nanmedian(np.stack(arrs), axis=0)
    comp["_dates"] = dates
    comp["_crs"], comp["_tr"] = geom
    comp["_n"] = len(dates)
    return comp


def features(comp):
    """The four measured-useful features. No local-z (measured 0.66-0.72)."""
    red, nir = comp["red"], comp["nir"]
    blu, grn = comp["blue"], comp["green"]
    s1, s2 = comp["swir16"], comp["swir22"]
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = (nir - red) / np.maximum(nir + red, 1)
        bsi = ((s1 + red) - (nir + blu)) / np.maximum((s1 + red) + (nir + blu), 1)
        rb = red / np.maximum(blu, 1)
        mndwi = (grn - s1) / np.maximum(grn + s1, 1)
    return {"red": red, "neg_ndvi": -ndvi, "bsi": bsi, "rb": rb,
            "_mndwi": mndwi, "_ndvi": ndvi}


class Calibration:
    """Empirical per-feature CDF for one park/basin/season.

    Built from pixels sampled across the park's own basin in the same season, so
    a percentile means "bright/bare/ferruginous compared with the rest of this
    landscape right now" - not compared with a savanna somewhere else in a
    different year, which is what absolute thresholds did.
    """
    QS = np.concatenate([np.arange(0, 99, 1.0), np.arange(99, 100.0, 0.05)])

    def __init__(self, breaks=None, n=0, dates=None, score_breaks=None):
        self.breaks = breaks or {}
        self.n = n
        self.dates = dates or []
        # CDF of the combined score over the same sample, so a caller can ask
        # "which score value is the top 0.5% of this landscape?" without
        # re-deriving it per tile (which would make each tile its own reference
        # frame and destroy comparability between tiles).
        self.score_breaks = score_breaks

    @classmethod
    def from_samples(cls, samples, dates=None):
        breaks = {}
        n = 0
        for k, v in samples.items():
            a = np.asarray(v, np.float32)
            a = a[np.isfinite(a)]
            if a.size < 500:
                continue
            breaks[k] = np.percentile(a, cls.QS).astype(np.float32)
            n = max(n, a.size)
        c = cls(breaks, n, dates)
        if len(breaks) == len(FEATURES):
            sc = c.score(samples)          # row-aligned by contract, see calibrate()
            sc = np.asarray(sc, np.float32)
            sc = sc[np.isfinite(sc)]
            if sc.size >= 500:
                c.score_breaks = np.percentile(sc, cls.QS).astype(np.float32)
        return c

    def score_threshold(self, q):
        """Score value at percentile `q` of the calibration landscape."""
        if self.score_breaks is None:
            return float(q)   # degenerate: score is itself ~a percentile
        return float(np.interp(q, self.QS, self.score_breaks))

    def score_pct(self, s):
        """Inverse of score_threshold: score value -> landscape percentile."""
        if self.score_breaks is None:
            return np.asarray(s, np.float32)
        return np.interp(s, self.score_breaks, self.QS).astype(np.float32)

    def pct(self, name, arr):
        """Map values to percentile 0-100 by interpolating the sampled CDF."""
        b = self.breaks.get(name)
        if b is None:
            return np.full(np.shape(arr), np.nan, np.float32)
        return np.interp(arr, b, self.QS).astype(np.float32)

    def score(self, F):
        """Weighted mean of feature percentiles -> 0-100 ranking score."""
        tot = None
        for k, w in WEIGHTS.items():
            p = self.pct(k, F[k]) * w
            tot = p if tot is None else tot + p
        return tot

    def to_json(self):
        return {"n": int(self.n), "dates": self.dates,
                "quantiles": [float(q) for q in self.QS],
                "breaks": {k: [float(x) for x in v]
                           for k, v in self.breaks.items()},
                "score_breaks": (None if self.score_breaks is None else
                                 [float(x) for x in self.score_breaks])}

    @classmethod
    def from_json(cls, d):
        sb = d.get("score_breaks")
        return cls({k: np.asarray(v, np.float32) for k, v in d["breaks"].items()},
                   d.get("n", 0), d.get("dates"),
                   None if sb is None else np.asarray(sb, np.float32))


def calibrate(bbox_list, dt_ranges, stride=6, max_tiles=25, verbose=False):
    """Draw a calibration sample from `max_tiles` of the given tile bboxes."""
    import random
    rnd = random.Random(17)
    picks = list(bbox_list)
    rnd.shuffle(picks)
    samples = {k: [] for k in FEATURES}
    dates, used = [], 0
    for bb in picks:
        if used >= max_tiles:
            break
        comp = median_composite(bb, dt_ranges, verbose=verbose)
        if comp is None:
            continue
        F = features(comp)
        # ONE mask for all features so the per-feature samples stay row-aligned:
        # score_breaks is computed from these arrays as if they were pixels, and
        # masking each feature independently would silently pair feature k's
        # pixel 7 with feature j's pixel 9.
        sub = {k: F[k][::stride, ::stride] for k in FEATURES}
        m = np.ones_like(next(iter(sub.values())), bool)
        for v in sub.values():
            m &= np.isfinite(v)
        if not m.any():
            continue
        for k in FEATURES:
            samples[k].append(sub[k][m])
        used += 1
        dates += [d for d in comp["_dates"] if d not in dates]
        if verbose:
            print(f"  calib tile {used}/{max_tiles} "
                  f"({int(m.sum())} px, {comp['_n']} dates)",
                  file=sys.stderr)
    if not used:
        return None
    return Calibration.from_samples(
        {k: np.concatenate(v) for k, v in samples.items() if v},
        sorted(dates))
