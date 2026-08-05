"""Amazon Mining Watch CNN ensemble, run on our own Sentinel-2 L1C stacks.

Why this module exists
----------------------
Every hand-picked spectral index in `analysis/mining_features.py` measures at
chance against confusers (docs/MINING_FINDINGS_2026-08.md §8.1: rb 0.450,
bsi 0.534, -ndvi 0.555, red 0.517 on IPIS-mine vs village/burn-scar/water/
bare-savanna). A learned model trained on exactly this phenomenon is the only
remaining candidate, so handover action #1 is: evaluate
`earthrise-media/mining-detector` (Amazon Mining Watch, MIT code / CC-BY data,
repo commit bbbcb2d) on our imagery. This module is the inference half; the
scoring half is `scripts/eval_amw_model.py`.

What the model expects (reproduced from the upstream repo, not guessed)
----------------------------------------------------------------------
* `models/48px_v4.10b-18d-20g-21a-22bc-ensemble.h5`, input `(None, 48, 48, 13)`,
  output `(None, 6)` = six ensemble members; the published confidence is their
  **mean** (README "Member scores are averaged").
* 13 bands in GEE `S2L1C` order — `core/gee.py::DataConfig._BAND_IDS`:
      B1 B2 B3 B4 B5 B6 B7 B8A B8 B9 B10 B11 B12
  note **B8A before B8**, and B10 (cirrus) is present. Getting this order wrong
  silently produces plausible-looking garbage, so `BANDS_GEE` is the single
  source of truth here.
* Reflectance scaling: `core/inference_engine.py` does
  `np.clip(pixels / 10000.0, 0, 1)` on values from `COPERNICUS/S2_HARMONIZED`.
  "Harmonized" means processing baseline >= 04.00 scenes are shifted back by
  -1000 DN. Earth Search's own STAC metadata states the same thing as
  `scale 1e-4, offset -0.1`, i.e. reflectance = (DN - 1000)/10000. We apply that
  shift per scene from `s2:processing_baseline` (see `_harmonize`).
* 48 px at 10 m = 480 m patch. Coarser bands are resampled to the 10 m grid,
  which is what GEE's `clipToBoundsAndScale` did.
* Multi-month **median** composite of cloud-masked scenes.

Deliberate deviations, and why they are acceptable
--------------------------------------------------
1. **Cloud mask**: upstream uses Cloud Score+ (`cs_cdf >= 0.6`), which is a GEE
   asset with no public COG equivalent. We mask with the L2A scene classification
   layer (SCL) of the *same* granule — same MGRS tile, same acquisition — read at
   the same 48x48 window. Both are per-pixel cloud/shadow rejections before a
   median; the median over 4+ dates absorbs the difference.
2. **Imagery host**: Earth Search's L1C assets are `s3://sentinel-s2-l1c`
   (requester-pays, so unusable unsigned). The identical granule is free on
   Google's `gcp-public-data-sentinel-2` bucket, so we resolve each L1C scene to
   its GCS JP2s via `s2:product_uri`. L2A COGs still come from AWS unsigned.
3. We score **points** (patch centred on a truth point), not a sliding window
   over a whole region. The evaluation question is "does the score separate mines
   from confusers", which needs one patch per labelled site. Upstream's
   half-patch-stride tiling is a scanning concern, and `--jitter` covers the
   "mine not exactly centred" case the same way their overlap does.

The first thing any user of this module should do is run the upstream-label
sanity check in `scripts/eval_amw_model.py --sanity`: if our reproduction of the
input pipeline were wrong, Amazon mines from the model's own training/holdout
sets would not score high, and any Africa number would be meaningless.
"""
import concurrent.futures as cf
import datetime
import hashlib
import json
import os
import sqlite3
import sys
import threading
import urllib.parse
import urllib.request

import numpy as np

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_CACHE_SIZE", "200000000")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAC = "https://earth-search.aws.element84.com/v1/search"
STAC_ITEM = ("https://earth-search.aws.element84.com/v1/collections/"
             "sentinel-2-l1c/items/{}")
GCS = "https://storage.googleapis.com/gcp-public-data-sentinel-2/"
GCS_LIST = ("https://storage.googleapis.com/storage/v1/b/"
            "gcp-public-data-sentinel-2/o?prefix={}&delimiter=/")
MODEL = os.path.join(BASE, "data", "models", "amw",
                     "48px_v4.10b-18d-20g-21a-22bc-ensemble.h5")
# Upstream's recommended single-period operating point (README July 2026:
# t_main=0.43 at the peak of the F0.5 curve).
THRESHOLD = 0.43
PATCH = 48
RES_M = 10.0

# GEE S2L1C band order -> (JP2 band code as it appears in filenames, common name)
BANDS_GEE = [
    ("B01", "coastal"), ("B02", "blue"), ("B03", "green"), ("B04", "red"),
    ("B05", "rededge1"), ("B06", "rededge2"), ("B07", "rededge3"),
    ("B8A", "nir08"), ("B08", "nir"), ("B09", "nir09"), ("B10", "cirrus"),
    ("B11", "swir16"), ("B12", "swir22"),
]
RED_IDX = 3   # index of B04 in BANDS_GEE, used as the clear-pixel reference
CLEAR_SCL = (4, 5, 6, 7, 11)   # veg, bare, water, unclassified, snow

_MODEL_CACHE = {}
_LOCK = threading.Lock()


# --------------------------------------------------------------- http caching
def _cache_con():
    """Reuse the repo's `http_cache` table (same one fetch_park_basins.py uses).

    STAC/GCS listing responses are immutable for a given granule, and a scan
    re-runs the same queries constantly; caching keeps re-runs free and keeps us
    polite to Element 84.
    """
    p = os.path.join(BASE, "db.sqlite3")
    con = sqlite3.connect(p, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("""CREATE TABLE IF NOT EXISTS http_cache(
        url TEXT PRIMARY KEY, status INTEGER, body BLOB, fetched_at TEXT)""")
    return con


def _get_json(url, body=None, timeout=120, cache=True):
    key = url if body is None else url + "#" + json.dumps(body, sort_keys=True)
    if cache:
        try:
            con = _cache_con()
            row = con.execute("SELECT status, body FROM http_cache WHERE url=?",
                              (key,)).fetchone()
            con.close()
            if row and row[0] == 200:
                return json.loads(row[1])
        except Exception:
            pass
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    if cache:
        try:
            con = _cache_con()
            con.execute("INSERT OR REPLACE INTO http_cache(url, status, body, "
                        "fetched_at) VALUES (?,?,?,?)",
                        (key, 200, raw,
                         datetime.datetime.utcnow().isoformat(timespec="seconds")))
            con.commit()
            con.close()
        except Exception:
            pass
    return json.loads(raw)


# ------------------------------------------------------------ scene discovery
def search_l2a(bbox, dt_range, max_cloud=40, limit=40, epsg=None):
    """L2A items (cloud metadata + SCL) sorted least-cloudy first.

    We search L2A rather than L1C because only L2A carries `eo:cloud_cover` we
    trust and the SCL asset we mask with; the matching L1C granule is derived
    from the item id.
    """
    body = {
        "collections": ["sentinel-2-l2a"], "bbox": list(bbox),
        "datetime": dt_range, "limit": limit,
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
    }
    feats = _get_json(STAC, body)["features"]
    if epsg is not None:
        feats = [f for f in feats
                 if f["properties"].get("proj:epsg") in (None, epsg)]
    return feats


def modal_epsg(bbox, dt_range, max_cloud=40):
    try:
        feats = search_l2a(bbox, dt_range, max_cloud=max_cloud)
    except Exception:
        return None
    c = {}
    for f in feats:
        e = f["properties"].get("proj:epsg")
        if e:
            c[e] = c.get(e, 0) + 1
    return max(c, key=c.get) if c else None


def l1c_for(l2a_item):
    """Matching L1C STAC item, or None. Ids differ only in the suffix."""
    iid = l2a_item["id"]
    if not iid.endswith("_L2A"):
        return None
    try:
        return _get_json(STAC_ITEM.format(iid[:-4] + "_L1C"))
    except Exception:
        return None


def gcs_band_urls(l1c_item):
    """GCS JP2 URLs per GEE band name for one L1C granule.

    Earth Search points L1C at `s3://sentinel-s2-l1c` (requester-pays); the same
    granule is free on Google's public bucket, but the granule sub-directory name
    is not derivable from the product URI, so it needs one (cached) list call.
    """
    p = l1c_item["properties"]
    uri = p["s2:product_uri"].rstrip("/")
    tile = p["grid:code"].replace("MGRS-", "")            # e.g. 34PHQ
    zone, band_lat, sq = tile[:2], tile[2], tile[3:]
    dt = uri.split("_")[2]                               # 20260224T083839
    # bucket paths use the unpadded UTM zone: tiles/34/P/HQ/, tiles/1/N/AA/
    pref = f"tiles/{zone.lstrip('0')}/{band_lat}/{sq}/{uri}/GRANULE/"
    try:
        d = _get_json(GCS_LIST.format(urllib.parse.quote(pref, safe="")))
    except Exception:
        return None
    prefixes = d.get("prefixes") or []
    if not prefixes:
        return None
    img = prefixes[0] + "IMG_DATA/"
    return {gee: f"{GCS}{img}T{tile}_{dt}_{gee}.jp2" for gee, _ in BANDS_GEE}


def _harmonize(l1c_item):
    """DN offset to match GEE's COPERNICUS/S2_HARMONIZED (see module docstring)."""
    b = str(l1c_item["properties"].get("s2:processing_baseline", "00.00"))
    try:
        return -1000.0 if float(b) >= 4.0 else 0.0
    except ValueError:
        return 0.0


# --------------------------------------------------------------- patch reading
def _patch_bounds(lon, lat, epsg, px=PATCH, res=RES_M, dx=0.0, dy=0.0):
    """Projected bounds of a px*res metre box centred on lon/lat (+ offset)."""
    from rasterio.warp import transform
    x, y = transform("EPSG:4326", f"EPSG:{epsg}", [lon], [lat])
    x, y = x[0] + dx, y[0] + dy
    h = px * res / 2.0
    return (x - h, y - h, x + h, y + h)


def read_patch(l2a_item, lon, lat, px=PATCH, dx=0.0, dy=0.0, verbose=False):
    """One (px, px, 13) TOA-reflectance patch, cloud-masked to NaN, or None.

    All 13 bands are resampled onto the same px*px 10 m grid (bilinear; SCL
    nearest), mirroring GEE's clipToBoundsAndScale.

    Bands are fetched **concurrently**: each JP2 window read is ~2-3 s of mostly
    HTTP latency, so serial reads cost ~40 s per scene and made a 50-point
    evaluation take hours. This is pure I/O wait, so it does not compete with
    other work on the box for CPU.
    """
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.enums import Resampling

    epsg = l2a_item["properties"].get("proj:epsg")
    if not epsg:
        return None
    l1c = l1c_for(l2a_item)
    if not l1c:
        return None
    urls = gcs_band_urls(l1c)
    if not urls:
        return None
    bnds = _patch_bounds(lon, lat, epsg, px, dx=dx, dy=dy)
    off = _harmonize(l1c)

    def one(url, nearest=False):
        with rasterio.open(url) as d:
            w = from_bounds(*bnds, d.transform)
            return d.read(1, window=w, boundless=True, fill_value=0,
                          out_shape=(px, px),
                          resampling=(Resampling.nearest if nearest
                                      else Resampling.bilinear))

    scl_href = (l2a_item["assets"].get("scl") or {}).get("href")
    jobs = [(i, urls[gee], False) for i, (gee, _) in enumerate(BANDS_GEE)]
    if scl_href:
        jobs.append((-1, scl_href, True))

    out = np.empty((px, px, len(BANDS_GEE)), np.float32)
    scl = None
    with cf.ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = {ex.submit(one, u, n): i for i, u, n in jobs}
        for f in cf.as_completed(futs):
            i = futs[f]
            try:
                a = f.result()
            except Exception as e:
                if verbose:
                    print(f"    band {i} fail: {str(e)[:70]}", file=sys.stderr)
                if i >= 0:
                    return None
                continue
            if i < 0:
                scl = a
                continue
            a = a.astype(np.float32)
            a[a == 0] = np.nan               # JP2 nodata
            out[:, :, i] = a + off
    if scl is not None:
        out[~np.isin(scl, CLEAR_SCL)] = np.nan
    return out


PATCH_CACHE = os.path.join(BASE, "data", "amw_cache")


def _patch_key(lon, lat, dt_ranges, px, dx, dy, max_scenes, max_cloud):
    h = hashlib.sha1(json.dumps(
        [round(float(lon), 6), round(float(lat), 6), list(dt_ranges), px,
         round(dx, 1), round(dy, 1), max_scenes, max_cloud],
        sort_keys=True).encode()).hexdigest()[:16]
    return os.path.join(PATCH_CACHE, h + ".npz")


def median_patch(lon, lat, dt_ranges, max_scenes=4, min_clear=0.75,
                 max_cloud=40, px=PATCH, dx=0.0, dy=0.0, verbose=False,
                 cache=True):
    """Cloud-masked median patch over up to `max_scenes` scenes.

    Returns `(array(px,px,13) in DN, [dates])` or `(None, [])`. Scenes in a
    different UTM zone than the first accepted one are skipped rather than
    reprojected: a per-pixel median across two grids is meaningless.

    Composites are cached under `data/amw_cache/` (gitignored) keyed by all
    inputs that affect the pixels: fetching 13 bands x 4 dates costs ~5-10 s of
    network per patch, and every threshold/ablation re-run would otherwise pay it
    again. Also caches misses (empty arrays) so a coverage hole is not retried
    forever.
    """
    kp = _patch_key(lon, lat, dt_ranges, px, dx, dy, max_scenes, max_cloud)
    if cache and os.path.exists(kp):
        try:
            z = np.load(kp, allow_pickle=False)
            if z["a"].size == 0:
                return None, []
            return z["a"], [str(d) for d in z["dates"]]
        except Exception:
            pass
    bbox = (lon - 0.004, lat - 0.004, lon + 0.004, lat + 0.004)
    epsg = modal_epsg(bbox, dt_ranges[0], max_cloud) if dt_ranges else None
    stack, dates = [], []
    for dt in dt_ranges:
        try:
            scenes = search_l2a(bbox, dt, max_cloud=max_cloud, epsg=epsg)
        except Exception as ex:
            if verbose:
                print(f"    STAC fail {dt}: {str(ex)[:60]}", file=sys.stderr)
            continue
        for sc in scenes:
            if len(dates) >= max_scenes:
                break
            if epsg and sc["properties"].get("proj:epsg") != epsg:
                continue
            a = read_patch(sc, lon, lat, px=px, dx=dx, dy=dy, verbose=verbose)
            if a is None:
                continue
            clear = np.isfinite(a[:, :, RED_IDX]).mean()
            if clear < min_clear:
                if verbose:
                    print(f"    skip {sc['id']}: clear {clear:.2f}",
                          file=sys.stderr)
                continue
            stack.append(a)
            dates.append(sc["properties"]["datetime"][:10])
        if len(dates) >= max_scenes:
            break
    med = None
    if stack:
        with np.errstate(invalid="ignore"):
            med = np.nanmedian(np.stack(stack), axis=0).astype(np.float32)
    if cache:
        try:
            os.makedirs(PATCH_CACHE, exist_ok=True)
            np.savez_compressed(
                kp, a=(med if med is not None else np.zeros(0, np.float32)),
                dates=np.array(dates, dtype="U10"))
        except Exception:
            pass
    if med is None:
        return None, []
    return med, dates


def dry_season_windows(lat, n_years=2, end=None):
    """Same hemisphere-aware dry-season windows the index scanner uses.

    Bare ground is separable from vegetation only in the dry season, and the
    composite must not mix hemispheres' seasons
    (`analysis/mining_features.dry_season_windows`, kept in sync deliberately so
    the CNN and the index ranker see the same imagery).
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


def window_from_dates(start, end):
    """Single explicit STAC range, for scoring upstream-labelled patches whose
    own `start_date`/`end_date` must be honoured."""
    return [f"{str(start)[:10]}T00:00:00Z/{str(end)[:10]}T23:59:59Z"]


# ------------------------------------------------------------------ the model
def load_model(path=MODEL):
    with _LOCK:
        if path not in _MODEL_CACHE:
            import tf_keras
            _MODEL_CACHE[path] = tf_keras.models.load_model(path, compile=False)
        return _MODEL_CACHE[path]


def predict(patches, path=MODEL):
    """Mean ensemble confidence per patch. `patches` = (N, 48, 48, 13) in DN.

    Normalisation is exactly upstream's `np.clip(pixels / 10000, 0, 1)`; NaNs
    (cloud/nodata after the median) become 0, which is what a masked GEE
    composite pixel also becomes once `computePixels` fills it.
    """
    x = np.asarray(patches, np.float32)
    x = np.nan_to_num(x, nan=0.0)
    x = np.clip(x / 10000.0, 0, 1)
    p = load_model(path).predict(x, verbose=0)
    return np.asarray(p, np.float32).mean(axis=1)


def score_point(lon, lat, dt_ranges=None, jitter=0, verbose=False, **kw):
    """Score one location. Returns dict with `score`, `dates`, `n_patches`.

    `jitter=1` also scores the 4 patches offset by a quarter patch (120 m) and
    reports the max, which plays the role of upstream's half-patch inference
    stride: a mine near the edge of a centred patch would otherwise be diluted
    by 480 m of surrounding forest.
    """
    lat_f = float(lat)
    dt_ranges = dt_ranges or dry_season_windows(lat_f)
    offs = [(0.0, 0.0)]
    if jitter:
        q = PATCH * RES_M / 4.0
        offs += [(q, 0.0), (-q, 0.0), (0.0, q), (0.0, -q)]
    pats, dates = [], []
    for dx, dy in offs:
        a, ds = median_patch(float(lon), lat_f, dt_ranges, dx=dx, dy=dy,
                             verbose=verbose, **kw)
        if a is None:
            continue
        pats.append(a)
        dates = dates or ds
    if not pats:
        return None
    sc = predict(np.stack(pats))
    return {"score": float(sc.max()), "score_center": float(sc[0]),
            "n_patches": len(pats), "dates": dates}
