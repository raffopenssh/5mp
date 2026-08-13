#!/usr/bin/env python3
"""VNP46A3 Black Marble monthly nightlight composites — fetch + extract.

WP3 of docs/PLAN_NEW_DATA_LAYERS.md. This module owns the LAADS side only:
given a VIIRS tile (h20v08-style) and a month list, download the monthly HDF5
granules and extract small per-site windows. The skill measurement and any
serving decision live in scripts/nightlight_sites.py.

Auth: NASA Earthdata Login bearer token, EARTHDATA_TOKEN (secrets.env).
Absent => this module raises MissingToken naming the variable; callers skip
with that name (AGENTS.md "Secrets": an absent credential fails loudly with
its name, and a live-API test skips rather than fails).

Endpoints, verified 2026-08-13:
  * The classic LAADS /archive/allData/... URLs NO LONGER accept bearer
    tokens — they 303 into an interactive OAuth/EULA page. Do not use them.
  * CMR granule search (no auth) lists granules:
      https://cmr.earthdata.nasa.gov/search/granules.json?short_name=VNP46A3...
  * Earthdata Cloud serves the bytes (bearer accepted, 303 -> signed S3 URL,
    so the HTTP client MUST follow redirects; a no-redirect GET "succeeds"
    with 0 bytes):
      https://data.laadsdaac.earthdatacloud.nasa.gov/prod-lads/VNP46A3/<name>

File facts (h20v08 sample): 2400x2400 grid, 10x10 degrees, ~64 MB HDF5,
north-west origin (lat descends), dataset
HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/AllAngle_Composite_Snow_Free
in nW/(cm^2 sr), _FillValue -999.9, plus *_Num (uint16, fill 65535) counting
the cloud-free nights behind each pixel. The Num band travels with every
radiance we keep: a radiance from 2 nights is a different claim than from 25.
A fill radiance is NULL, never 0 — a dark month and an unobserved month are
different states (plan WP3 trap list).

Whole granules are deleted after extraction (disk is 76% full; the corpus
would be ~11 GB/tile). What we keep, per (tile, month): a small .npz of 5x5
pixel windows around each requested site, in data/nightlights/extracts/.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from secrets_config import secret  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
EXTRACT_DIR = BASE_DIR / "data" / "nightlights" / "extracts"
TMP_DIR = BASE_DIR / "data" / "nightlights" / "tmp"

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
CLOUD_BASE = "https://data.laadsdaac.earthdatacloud.nasa.gov/prod-lads/VNP46A3/"
PRODUCT = "VNP46A3"
VERSION = "2"

H5_GRID = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/"
RAD_DS = H5_GRID + "AllAngle_Composite_Snow_Free"
NUM_DS = H5_GRID + "AllAngle_Composite_Snow_Free_Num"
Q_DS = H5_GRID + "AllAngle_Composite_Snow_Free_Quality"
RAD_FILL = -999.9
NUM_FILL = 65535
UNITS = "nW/(cm^2 sr)"
WIN = 5  # extraction window (pixels), median taken over centre 3x3 downstream

# Radiance grid: 2400 px per 10 degrees, NW origin.
PX = 2400
DEG = 10.0


class MissingToken(RuntimeError):
    pass


def token():
    t = secret("EARTHDATA_TOKEN")
    if not t:
        raise MissingToken(
            "EARTHDATA_TOKEN is not set (secrets.env) — cannot reach LAADS. "
            "Generate at urs.earthdata.nasa.gov (60-day expiry).")
    return t


def tile_for(lon, lat):
    """VIIRS linear-lat/lon tile id for a WGS84 point."""
    return f"h{int((lon + 180) // 10):02d}v{int((90 - lat) // 10):02d}"


def tile_origin(tile):
    h = int(tile[1:3])
    v = int(tile[4:6])
    return (h * 10 - 180, 90 - v * 10)  # (west, north)


def pixel_of(tile, lon, lat):
    """(row, col) of a point inside its tile. Row 0 is the NORTH edge."""
    west, north = tile_origin(tile)
    col = int((lon - west) / DEG * PX)
    row = int((north - lat) / DEG * PX)
    if not (0 <= row < PX and 0 <= col < PX):
        raise ValueError(f"({lon},{lat}) not in {tile}")
    return row, col


def list_granules(tile):
    """All VNP46A3 granules for a tile from CMR, oldest first.

    Returns [(month 'YYYY-MM', granule_name)]. Asserts the corpus is not
    absurdly short (R1: a filter that matches nothing must not read as a
    small archive) — the record starts 2012-01, so any live tile has >100.
    """
    q = urllib.parse.urlencode({
        "short_name": PRODUCT, "version": VERSION,
        "producer_granule_id[]": f"*.{tile}.*",
        "options[producer_granule_id][pattern]": "true",
        "page_size": "2000", "sort_key": "start_date",
    })
    with urllib.request.urlopen(f"{CMR_URL}?{q}", timeout=120) as r:
        entries = json.load(r)["feed"]["entry"]
    out = []
    for e in entries:
        name = e["producer_granule_id"]          # VNP46A3.A2024001.h20v08...
        yday = name.split(".")[1][1:]            # 2024001
        dt = datetime.strptime(yday, "%Y%j")
        out.append((dt.strftime("%Y-%m"), name))
    if len(out) < 100:
        raise RuntimeError(
            f"CMR returned only {len(out)} granules for {tile} — the archive "
            f"runs from 2012, so this is a failed listing, not a small tile. "
            f"UNFINISHED.")
    return out


def download(name, log=print):
    """Fetch one granule into TMP_DIR; returns the local path.

    The cloud endpoint 303s to a pre-signed S3 URL. Two traps, both hit:
    a client that does not follow the redirect gets 0 bytes with a success
    code; and a client that follows it WHILE re-sending the bearer header
    gets S3's 400 "Only one auth mechanism allowed" (urllib's default
    redirect handler forwards headers cross-host). So: one no-redirect
    request with the bearer, then a clean request to the signed URL.
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    dest = TMP_DIR / name

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(
        CLOUD_BASE + name, headers={"Authorization": f"Bearer {token()}"})
    signed = None
    try:
        r = opener.open(req, timeout=120)
        r.close()  # 200 without redirect would be unexpected but harmless
        signed = CLOUD_BASE + name
    except urllib.error.HTTPError as ex:
        if ex.code in (301, 302, 303, 307, 308):
            signed = ex.headers["Location"]
        else:
            raise
    t0 = time.time()
    with urllib.request.urlopen(signed, timeout=600) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    size = dest.stat().st_size
    if size < 1 << 20:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"{name}: downloaded {size} bytes — truncated or "
                           f"auth bounce, UNFINISHED")
    log(f"    {name}: {size/1e6:.0f} MB in {time.time()-t0:.0f}s")
    return dest


def extract_windows(h5path, tile, sites):
    """Read 5x5 windows for [(site_id, lon, lat)] from one granule.

    Returns {site_id: {radiance: 5x5 float (NaN=fill), nights: 5x5 int}}.
    """
    import h5py
    out = {}
    with h5py.File(h5path, "r") as f:
        rad, num = f[RAD_DS], f[NUM_DS]
        for sid, lon, lat in sites:
            row, col = pixel_of(tile, lon, lat)
            half = WIN // 2
            r0, c0 = max(0, row - half), max(0, col - half)
            r1, c1 = min(PX, row + half + 1), min(PX, col + half + 1)
            a = rad[r0:r1, c0:c1].astype(np.float32)
            n = num[r0:r1, c0:c1].astype(np.int32)
            a[np.isclose(a, RAD_FILL)] = np.nan
            n[n == NUM_FILL] = -1
            out[sid] = {"radiance": a, "nights": n}
    return out


def extract_path(tile, month):
    return EXTRACT_DIR / f"{tile}_{month}.npz"


def fetch_months(tile, sites, months=None, log=print, keep_granule=False):
    """Ensure extracts exist for every requested month of a tile.

    sites: [(site_id, lon, lat)] — ALL sites wanted for this tile, because the
    extract file is per (tile, month) and re-downloading a 64 MB granule to add
    one site defeats the point. If the site set grows, delete the extracts for
    that tile and re-run.

    months=None means every month CMR lists. Skips months whose extract file
    already exists AND contains every requested site. Returns
    (done_months, failed_months); a failed month is reported, not swallowed —
    the caller decides whether the run counts as finished (R1).
    """
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    granules = list_granules(tile)
    if months is not None:
        want = set(months)
        granules = [(m, n) for m, n in granules if m in want]
        missing_months = want - {m for m, _ in granules}
        if missing_months:
            log(f"  {tile}: months not in CMR (not yet published): "
                f"{sorted(missing_months)}")
    site_ids = [s[0] for s in sites]
    done, failed = [], []
    for month, name in granules:
        path = extract_path(tile, month)
        if path.exists():
            have = set(np.load(path, allow_pickle=True)["site_ids"])
            if have >= set(site_ids):
                done.append(month)
                continue
        try:
            h5 = download(name, log=log)
        except MissingToken:
            raise
        except Exception as ex:  # noqa: BLE001
            log(f"  {tile} {month}: download failed: {ex}")
            failed.append(month)
            continue
        try:
            wins = extract_windows(h5, tile, sites)
            np.savez_compressed(
                path,
                site_ids=np.array(site_ids),
                radiance=np.stack([_pad(wins[s]["radiance"]) for s in site_ids]),
                nights=np.stack([_pad(wins[s]["nights"], -1) for s in site_ids]),
                granule=name, units=UNITS,
                extracted=datetime.now(timezone.utc).isoformat())
            done.append(month)
        except Exception as ex:  # noqa: BLE001
            log(f"  {tile} {month}: extract failed: {ex}")
            path.unlink(missing_ok=True)
            failed.append(month)
        finally:
            if not keep_granule:
                h5.unlink(missing_ok=True)
    return done, failed


def _pad(a, fill=np.nan):
    """Edge sites yield short windows; pad to 5x5 so stacking works."""
    out = np.full((WIN, WIN), fill, dtype=a.dtype if a.dtype.kind == "f" else np.int32)
    out[:a.shape[0], :a.shape[1]] = a
    return out


def site_series(tile, site_id):
    """Monthly series for one site from the extracts on disk.

    Returns [(month, radiance_median_or_None, min_nights)] sorted by month.
    Median over the centre 3x3; NaN-only window => None (unobserved month —
    NULL, not 0). min_nights is the fewest cloud-free nights among the pixels
    that contributed, so the reader can weigh the claim.
    """
    rows = []
    for path in sorted(EXTRACT_DIR.glob(f"{tile}_*.npz")):
        month = path.stem.split("_")[1]
        z = np.load(path, allow_pickle=True)
        ids = list(z["site_ids"])
        if site_id not in ids:
            continue
        i = ids.index(site_id)
        c = WIN // 2
        rad = z["radiance"][i][c - 1:c + 2, c - 1:c + 2]
        nts = z["nights"][i][c - 1:c + 2, c - 1:c + 2]
        if np.all(np.isnan(rad)):
            rows.append((month, None, None))
        else:
            ok = ~np.isnan(rad)
            rows.append((month, float(np.nanmedian(rad)),
                         int(nts[ok].min()) if (nts[ok] >= 0).all() else None))
    return rows


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tile", required=True, help="e.g. h20v08")
    ap.add_argument("--sites", required=True,
                    help="JSON file: [[site_id, lon, lat], ...]")
    ap.add_argument("--months", nargs="*", help="YYYY-MM ...; default all")
    ap.add_argument("--keep-granule", action="store_true")
    args = ap.parse_args()
    sites = [tuple(s) for s in json.load(open(args.sites))]
    done, failed = fetch_months(args.tile, sites, months=args.months,
                                keep_granule=args.keep_granule)
    print(f"{args.tile}: {len(done)} months extracted, {len(failed)} failed")
    sys.exit(1 if failed else 0)
