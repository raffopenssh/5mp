#!/usr/bin/env python3
"""Cropland context from GLAD global cropland extent (30 m, Landsat).

Source: Potapov et al. 2021, Nature Food (doi:10.1038/s43016-021-00429-z),
https://glad.umd.edu/dataset/croplands — binary cropland extent per four-year
epoch; we use 2003 (2000-2003) and 2019 (2016-2019). Cropland there means
annual/perennial herbaceous crops; PASTURE AND SHIFTING CULTIVATION ARE
EXCLUDED by the source's definition. In a transhumance landscape that
exclusion is the point: a pastoral camp measuring 0 cropland is the dataset
working, not missing data.

Measured on XSA_Study_Area before any of this was built (2026-08-14):
settlement centroids sit on 5.8x the background cropland fraction (8.6% vs
1.5% mean within 1 km; 1,000 random in-bbox points as background), the
fraction discriminates the existing classes (towns 57%, villages 36%,
temporary camps 19% any-cropland) and persistence ('recent' 53% vs
'permanent' 22%) — so it earns columns (migration 058) rather than a note.

What is written, per area:

  park_settlements.cropland_frac_2019 / _2003   mean cropland fraction of the
      30 m pixels within ~1 km of the cluster centroid, in [0,1]. The radius
      is part of the definition: this measures the settlement's immediate
      landscape, not its rooftops. Both epochs ride on the row so the TREND
      is auditable without re-reading rasters (same shape as
      surface_e2000_m2).
  deforestation_events.cropland_frac_2019       same ~1 km box over the event
      centroid — the CONTEXT question, "is this clearing in a farming
      landscape". This is what a cropland vector layer would otherwise show.
  deforestation_events.cropland_event_frac_2019  fraction of the event's own
      CLEARED PIXELS (its polygons in feature_geometries, rasterized onto the
      GLAD grid) mapped cropland in 2016-2019 — "how much of this clearing is
      cropped now".
  deforestation_events.cropland_conversion_frac  fraction of the cleared
      pixels cropped in 2019 AND NOT in 2003 — new cropland. area_km2 *
      conversion_frac sums to "deforestation attributable to cropland
      expansion". For year > 2016 the 2019 epoch may PREDATE the clearing, so
      consumers gate outcome language on the year (Go narrative does).
  *.cropland_source   'glad_cropland_30m' when measured, 'clip_missing' when
      the source raster could not be fetched. NULL fraction + clip_missing is
      UNMEASURED; a field with no crops and a raster that never downloaded
      are different states (invariant 1).

Mechanics: the GLAD mosaics are four global quadrant GeoTIFFs (NE/NW/SE/SW,
~2 GB each, single-strip — no overviews, so remote windowed reads cost a
whole 680k-px row). We therefore clip each area's bbox (+1 km margin) ONCE
via gdalbuildvrt+gdal_translate over /vsicurl into data/cropland/clips/ and
do all zonal reads locally (XSA, the largest area: ~40 s/epoch to clip,
~10 MB, then 2,121 settlements in ~5 s). The VRT handles bboxes straddling
the equator/prime-meridian quadrant seams.

Called per area by scripts/backfill_settlement_surface.py (after
ghsl_epochs, because the recluster deletes+reinserts cluster rows), and
runnable standalone:

    python3 scripts/cropland.py --area XSA_Study_Area
    python3 scripts/cropland.py --rotate 4      # nightly cron: stalest areas

State: data/cropland_state.json — per area: when, version, sha256 of each
clip used, and the summary. An area whose entry is missing or older than
CROPLAND_VERSION counts as pending; unknown is not clean.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

STATE_FILE = BASE_DIR / "data" / "cropland_state.json"
CLIP_DIR = BASE_DIR / "data" / "cropland" / "clips"

EPOCHS = (2003, 2019)
SOURCE = "glad_cropland_30m"
CLIP_MISSING = "clip_missing"
# ~1 km half-width in degrees; part of the measurement's definition (see
# module docstring), not a tunable.
BOX_DEG = 1.0 / 111.0
# Bump when a change HERE would give a different number for the same ground.
CROPLAND_VERSION = "2026-08-14b"

GLAD_URL = ("https://glad.geog.umd.edu/Potapov/Global_Crop/Data/"
            "Global_cropland_{quad}_{epoch}.tif")
# Quadrant bounds (lon_min, lat_min, lon_max, lat_max) as published.
QUADRANTS = {
    "NE": (0, 0, 170, 70), "NW": (-170, 0, 0, 70),
    "SE": (0, -50, 180, 0), "SW": (-90, -60, -30, 0),
}


def _sha256(path, _cache={}):
    key = str(path)
    if key not in _cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _cache[key] = h.hexdigest()
    return _cache[key]


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE_FILE)


def ensure_clip(area_id, epoch, bounds, log=print):
    """Local clip GeoTIFF for one area+epoch, or None if it cannot be made.

    bounds = (minx, miny, maxx, maxy) WGS84, already margin-padded. Cached in
    CLIP_DIR; a cached file is trusted (the upstream mosaics are versioned by
    publication, not mutated). A failed download leaves nothing behind — a
    half-written clip must not be mistaken for a whole one.
    """
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    out = CLIP_DIR / f"{area_id}_{epoch}.tif"
    if out.exists() and out.stat().st_size > 0:
        return out
    minx, miny, maxx, maxy = bounds
    quads = [q for q, (x0, y0, x1, y1) in QUADRANTS.items()
             if minx < x1 and maxx > x0 and miny < y1 and maxy > y0]
    if not quads:
        log(f"  cropland: {area_id} bbox outside GLAD coverage")
        return None
    urls = ["/vsicurl/" + GLAD_URL.format(quad=q, epoch=epoch) for q in quads]
    vrt = out.with_suffix(".vrt")
    tmp = out.with_suffix(".tif.tmp")
    # UMD's server sometimes accepts the connection then stops sending
    # (observed 2026-08-14: ESTAB socket, tmp file frozen for minutes). Abort
    # a transfer below 1 KB/s for 60 s instead of riding it to the hard
    # timeout — the area reports UNFINISHED and the rotation retries later.
    env = dict(os.environ,
               GDAL_HTTP_LOW_SPEED_LIMIT="1024",
               GDAL_HTTP_LOW_SPEED_TIME="60",
               GDAL_HTTP_MAX_RETRY="3",
               GDAL_HTTP_RETRY_DELAY="5")
    try:
        subprocess.run(["gdalbuildvrt", "-q", str(vrt)] + urls,
                       check=True, capture_output=True, timeout=300, env=env)
        subprocess.run(["gdal_translate", "-q",
                        "-projwin", str(minx), str(maxy), str(maxx), str(miny),
                        str(vrt), str(tmp), "-of", "GTiff",
                        "-co", "COMPRESS=LZW", "-co", "TILED=YES"],
                       check=True, capture_output=True, timeout=1800, env=env)
        tmp.replace(out)
        return out
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        err = getattr(e, "stderr", b"") or b""
        log(f"  cropland: clip {area_id}/{epoch} failed: "
            f"{err.decode(errors='replace').strip()[:200]}")
        tmp.unlink(missing_ok=True)
        return None
    finally:
        vrt.unlink(missing_ok=True)


def _zonal(src, lat, lon):
    """Mean cropland fraction of the pixels within BOX_DEG of (lat, lon)."""
    from rasterio.windows import from_bounds
    w = from_bounds(lon - BOX_DEG, lat - BOX_DEG,
                    lon + BOX_DEG, lat + BOX_DEG, src.transform)
    a = src.read(1, window=w, boundless=True, fill_value=0)
    if a.size == 0:
        return None
    return float(a.mean())


def _event_fracs(r19, r03, geojsons, lat, lon):
    """(frac_2019, conversion_frac) over an event's cleared pixels.

    Rasterizes the event's polygons onto the GLAD 30 m grid (window read of
    both epochs), then: frac_2019 = share of cleared pixels cropped in the
    2016-2019 epoch; conversion = share cropped in 2019 AND NOT in 2003.
    Polygons too small to cover a pixel centre fall back to the centroid
    pixel -- one pixel is a poor sample but it is the event's OWN ground,
    which a 1 km box is not.
    """
    import numpy as np
    from rasterio import features
    from rasterio.windows import from_bounds, transform as win_transform
    from shapely.geometry import shape
    from shapely.ops import unary_union

    geom = None
    if geojsons:
        try:
            geom = unary_union([shape(json.loads(g)) for g in geojsons])
        except Exception:
            geom = None
    if geom is None or geom.is_empty:
        minx, miny, maxx, maxy = (lon - 0.00027, lat - 0.00027,
                                  lon + 0.00027, lat + 0.00027)
    else:
        minx, miny, maxx, maxy = geom.bounds
    w = from_bounds(minx, miny, maxx, maxy, r19.transform)
    w = w.round_lengths(op="ceil").round_offsets(op="floor")
    a19 = r19.read(1, window=w, boundless=True, fill_value=0)
    if a19.size == 0:
        return None, None
    a03 = r03.read(1, window=w, boundless=True, fill_value=0)
    if geom is not None and not geom.is_empty:
        mask = features.geometry_mask([geom.__geo_interface__],
                                      out_shape=a19.shape,
                                      transform=win_transform(w, r19.transform),
                                      invert=True, all_touched=True)
        n = int(mask.sum())
        if n == 0:
            mask = np.ones_like(a19, dtype=bool)
            n = a19.size
    else:
        mask = np.ones_like(a19, dtype=bool)
        n = a19.size
    c19 = a19[mask] > 0
    c03 = a03[mask] > 0
    return float(c19.mean()), float((c19 & ~c03).mean())


def derive_for_area(conn, area_id, geom_wgs84, log=print):
    """Measure + write cropland context for one area.

    Returns (ok, summary). ok=False is UNFINISHED: the caller must not stamp
    the area (invariant 1). A missing clip for either epoch is UNFINISHED —
    rows get NULL + 'clip_missing' so a reader sees unmeasured, and the
    rotation retries the area.
    """
    import rasterio

    setts = conn.execute(
        "SELECT id, lat, lon FROM park_settlements "
        "WHERE park_id=? AND polygon_ids IS NOT NULL AND polygon_ids != ''",
        (area_id,)).fetchall()
    defos = conn.execute(
        "SELECT id, lat, lon, COALESCE(polygon_ids,''), "
        "COALESCE(area_km2,0) FROM deforestation_events WHERE park_id=?",
        (area_id,)).fetchall()
    if not setts and not defos:
        return True, {"settlements": 0, "deforestation_events": 0,
                      "note": "nothing to measure"}

    minx, miny, maxx, maxy = geom_wgs84.bounds
    pad = BOX_DEG * 1.5
    bounds = (minx - pad, miny - pad, maxx + pad, maxy + pad)

    clips = {}
    for epoch in EPOCHS:
        clips[epoch] = ensure_clip(area_id, epoch, bounds, log=log)
    if any(c is None for c in clips.values()):
        # Mark what we can as unmeasured-with-reason, then report UNFINISHED.
        conn.execute("UPDATE park_settlements SET cropland_source=? "
                     "WHERE park_id=? AND polygon_ids IS NOT NULL "
                     "AND cropland_frac_2019 IS NULL", (CLIP_MISSING, area_id))
        conn.execute("UPDATE deforestation_events SET cropland_source=? "
                     "WHERE park_id=? AND cropland_frac_2019 IS NULL",
                     (CLIP_MISSING, area_id))
        conn.commit()
        return False, {"error": "clip missing",
                       "epochs_missing": [e for e, c in clips.items()
                                          if c is None]}

    shas = {str(e): _sha256(clips[e]) for e in EPOCHS}
    s_updates, d_updates = [], []
    with rasterio.open(clips[2019]) as r19, rasterio.open(clips[2003]) as r03:
        for sid, lat, lon in setts:
            f19 = _zonal(r19, lat, lon)
            f03 = _zonal(r03, lat, lon)
            if f19 is None or f03 is None:
                s_updates.append((None, None, CLIP_MISSING, sid))
            else:
                s_updates.append((round(f19, 4), round(f03, 4), SOURCE, sid))
        # Deforestation: zonal over the event's OWN cleared polygons. An
        # event with no polygons (shouldn't exist; all 24,072 have them) or
        # whose polygons rasterize to zero pixels on the 30 m grid gets the
        # centroid PIXEL as a 1-pixel fallback rather than silence.
        fid_rows = conn.execute(
            "SELECT feature_id, geojson FROM feature_geometries "
            "WHERE park_id=? AND feature_type='deforestation'",
            (area_id,)).fetchall()
        geoms = {fr[0]: fr[1] for fr in fid_rows}
        for did, lat, lon, pids, _akm2 in defos:
            polys = [geoms[p.strip()] for p in pids.split(",")
                     if p.strip() in geoms]
            ctx19 = _zonal(r19, lat, lon)          # ~1 km context box
            ef19, conv = _event_fracs(r19, r03, polys, lat, lon)
            if ctx19 is None or ef19 is None:
                d_updates.append((None, None, None, CLIP_MISSING, did))
            else:
                d_updates.append((round(ctx19, 4), round(ef19, 4),
                                  round(conv, 4), SOURCE, did))

    # Batched, committed writes so SQLite's single writer stays available
    # (invariant 16); write_rows retries on lock.
    import ghsl_tiles
    if s_updates:
        ghsl_tiles.write_rows(conn, """
            UPDATE park_settlements SET cropland_frac_2019=?,
                   cropland_frac_2003=?, cropland_source=? WHERE id=?""",
                              s_updates)
    if d_updates:
        ghsl_tiles.write_rows(conn, """
            UPDATE deforestation_events SET cropland_frac_2019=?,
                   cropland_event_frac_2019=?, cropland_conversion_frac=?,
                   cropland_source=? WHERE id=?""", d_updates)

    measured = [u for u in s_updates if u[0] is not None]
    mean19 = (sum(u[0] for u in measured) / len(measured)) if measured else 0
    mean03 = (sum(u[1] for u in measured) / len(measured)) if measured else 0
    conv_by_id = {u[4]: u[2] for u in d_updates if u[0] is not None}
    conv_km2 = sum(akm2 * conv_by_id[did]
                   for did, _lat, _lon, _p, akm2 in defos
                   if did in conv_by_id)
    summary = {"settlements": len(s_updates),
               "settlements_measured": len(measured),
               "deforestation_events": len(d_updates),
               "defo_conversion_km2": round(conv_km2, 2),
               "mean_frac_2019": round(mean19, 4),
               "mean_frac_2003": round(mean03, 4),
               "clips": shas}
    log(f"  cropland: {area_id}: {len(measured)}/{len(s_updates)} settlements"
        f" measured (mean {mean03:.1%} -> {mean19:.1%}),"
        f" {len(d_updates)} deforestation events")
    return True, summary


def stamp(area_id, summary):
    state = load_state()
    entry = dict(summary)
    entry["version"] = CROPLAND_VERSION
    entry["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry["source"] = SOURCE
    entry["citation"] = ("Potapov P. et al. (2021) Global maps of cropland "
                         "extent and change... Nature Food. "
                         "doi:10.1038/s43016-021-00429-z")
    entry["terms"] = "CC BY 4.0"
    state[area_id] = entry
    save_state(state)


def pending(conn):
    """[area ids] whose stamp is missing/older than CROPLAND_VERSION, or that
    have deforestation events nothing ever measured (NULL fraction AND NULL
    source) -- a re-ingest (e.g. a healed GFW scan, see
    scripts/gfw_find_truncated.py) creates new events after the stamp, and a
    current stamp must not hide them (invariant 1: unmeasured is not clean).
    Settlement rows are covered the same way via polygon_ids."""
    from backfill_settlement_surface import targets
    state = load_state()
    unmeasured = {r[0] for r in conn.execute(
        "SELECT DISTINCT park_id FROM deforestation_events "
        "WHERE cropland_frac_2019 IS NULL AND cropland_source IS NULL")}
    unmeasured |= {r[0] for r in conn.execute(
        "SELECT DISTINCT park_id FROM park_settlements "
        "WHERE polygon_ids IS NOT NULL AND polygon_ids != '' "
        "AND cropland_frac_2019 IS NULL AND cropland_source IS NULL")}
    out = []
    for aid, geom, _aoi in targets(conn):
        e = state.get(aid)
        if not e or e.get("version") != CROPLAND_VERSION or aid in unmeasured:
            out.append((aid, geom))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", help="park or AOI id")
    ap.add_argument("--rotate", type=int, help="derive N stalest areas")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    from backfill_settlement_surface import connect, targets
    conn = connect()

    if a.list:
        p = pending(conn)
        print(f"{len(p)} area(s) pending")
        for aid, _ in p[:20]:
            print(f"  {aid}")
        return 0

    if a.area:
        match = [t for t in targets(conn) if t[0] == a.area]
        if not match:
            print(f"no such area: {a.area}")
            return 2
        aid, geom, _ = match[0]
        ok, summary = derive_for_area(conn, aid, geom)
        if ok:
            stamp(aid, summary)
            return 0
        return 1

    if a.rotate:
        p = pending(conn)
        if not p:
            print("cropland: queue empty")
            return 0
        done = 0
        for aid, geom in p[:a.rotate]:
            t0 = time.time()
            ok, summary = derive_for_area(conn, aid, geom)
            if ok:
                stamp(aid, summary)
                done += 1
            print(f"cropland: {aid}: {'ok' if ok else 'UNFINISHED'} "
                  f"({time.time()-t0:.0f}s)")
        print(f"cropland: {done}/{min(a.rotate, len(p))} done, "
              f"{len(p)-done} still pending")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
