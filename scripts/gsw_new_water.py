#!/usr/bin/env python3
"""WP4 (docs/PLAN_NEW_DATA_LAYERS.md): JRC GSW new-water = pit-lake candidates.

A NEW permanent/seasonal water body (GSW v1.4 `transitions` classes 2 and 5)
at pit-lake scale, away from rivers, is a much narrower claim than the retired
"turbid pixel" detector (docs/agents/mining.md — read it before extending
this). Nothing here writes park_settlements or any narrative; the deliverable
is candidate polygons + a measured skill vs the mining reference sets.

Modes:
  --aoi XSA_Study_Area          extract candidates for one AOI
  --park CAF_Chinko             … or one park
  --eval                        skill eval over the XSA candidates
                                -> data/eval/gsw_new_water.json (committed)
  --rotate N                    cron: extract the N stalest areas
                                (state: data/gsw_new_water/state.json)

Per-area candidates land in data/gsw_new_water/<id>.json (committed — small).

Invariants honoured:
  R1  a window that fails to read, or a deadline hit, marks the area
      UNFINISHED; the rotation does not record it as scanned.
  R2  the raster's end year is derived from the tile URL, never typed, and is
      written into every output ("no candidate after 2021" is unknowable).
  R5  settlement distances use footprint-bearing, non-scanner rows only.
  R6  candidates are flushed to the JSON per window batch; DB is read-only.
  R7  source/accessed/citation/terms ride in every artefact.
  I15 the pit-lake area bounds are constants calibrated at AOI scale and are
      named in the output.
"""
import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

BASE_URL = ("https://storage.googleapis.com/global-surface-water/downloads2021/"
            "transitions/transitions_{lon}_{lat}v1_4_2021.tif")
# R2: derive the observation end year from the release string in the URL.
RASTER_END_YEAR = int(re.search(r"v\d+_\d+_(\d{4})", BASE_URL).group(1))
RASTER_PERIOD = f"1984-{RASTER_END_YEAR}"

# GSW v1.4 transitions classes (Pekel et al. 2016, data users guide v4).
CLASS_NEW_PERMANENT = 2
CLASS_NEW_SEASONAL = 5
CLASS_NAMES = {CLASS_NEW_PERMANENT: "new_permanent",
               CLASS_NEW_SEASONAL: "new_seasonal"}

# Invariant 15: constants calibrated at AOI scale, named in every output.
MIN_AREA_HA = 0.1     # ~1 Landsat pixel; below is noise
MAX_AREA_HA = 50.0    # pit lakes are small; a new reservoir is not a pit
RIVER_EXCLUDE_M = 200  # river migration is the confuser
WINDOW_DEG = 1.0

OUT_DIR = BASE_DIR / "data" / "gsw_new_water"
STATE_PATH = OUT_DIR / "state.json"
EVAL_PATH = BASE_DIR / "data" / "eval" / "gsw_new_water.json"

PROVENANCE = {
    "source": "JRC Global Surface Water v1.4, `transitions` layer",
    "citation": ("Jean-Francois Pekel, Andrew Cottam, Noel Gorelick, "
                 "Alan S. Belward, \"High-resolution mapping of global "
                 "surface water and its long-term changes\", Nature 540, "
                 "418-422 (2016). doi:10.1038/nature20584"),
    "terms": ("https://global-surface-water.appspot.com/download — free for "
              "any use with attribution (EC JRC/Google)"),
    "notice": ("Candidate pit lakes are UNADJUDICATED shape/context "
               "inferences drawn by this repo from GSW transition classes; "
               "they are ours, not JRC's."),
    "raster_period": RASTER_PERIOD,
    "raster_end_year": RASTER_END_YEAR,
    "end_year_caveat": (f"the transitions layer ends {RASTER_END_YEAR}; "
                        "absence of a candidate says nothing about water "
                        f"appearing after {RASTER_END_YEAR}"),
    "constants": {"min_area_ha": MIN_AREA_HA, "max_area_ha": MAX_AREA_HA,
                  "river_exclude_m": RIVER_EXCLUDE_M,
                  "calibrated_at": "AOI scale (invariant 15)"},
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tile_for(lon, lat):
    lon0 = int(math.floor(lon / 10.0) * 10)
    lat0 = int(math.ceil(lat / 10.0) * 10)
    return (f"{abs(lon0)}{'E' if lon0 >= 0 else 'W'}",
            f"{abs(lat0)}{'N' if lat0 >= 0 else 'S'}")


def windows_for_bbox(x0, y0, x1, y1, step=WINDOW_DEG):
    """Same tiling discipline as gsw_water.py: windows clipped to one tile."""
    out = []
    y = y0
    while y < y1:
        x = x0
        while x < x1:
            wx1, wy1 = min(x + step, x1), min(y + step, y1)
            tl_lon = math.floor(x / 10.0) * 10
            tl_lat = math.ceil((wy1 - 1e-9) / 10.0) * 10
            wx1 = min(wx1, tl_lon + 10)
            wy0 = max(y, tl_lat - 10)
            if wx1 > x and wy1 > wy0:
                out.append((tile_for(x + 1e-9, wy1 - 1e-9), x, wy0, wx1, wy1))
            x += step
        y += step
    return out


def _open(tile):
    import rasterio
    lon, lat = tile
    local = BASE_DIR / "data" / "gsw" / f"transitions_{lon}_{lat}.tif"
    if local.exists():
        return rasterio.open(local)
    return rasterio.open("/vsicurl/" + BASE_URL.format(lon=lon, lat=lat))


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_rivers(conn, area_id):
    """River linework for the exclusion buffer: in-area reaches plus the
    basin's downstream traces. Missing rows are a fact, not an error — a park
    with no hydro ingest gets no exclusion, and the output says so."""
    from shapely.geometry import shape as shp
    geoms = []
    for sql in ("SELECT geojson FROM park_rivers_hydro WHERE park_id=? AND geojson IS NOT NULL",
                "SELECT geojson FROM park_basin_rivers WHERE park_id=?"):
        for (gj,) in conn.execute(sql, (area_id,)):
            try:
                geoms.append(shp(json.loads(gj)))
            except Exception:
                continue
    return geoms


def load_settlements(conn, area_id):
    """R5: footprint-bearing, non-scanner rows only (docs/agents/mining.md)."""
    return conn.execute(
        "SELECT lat, lon FROM park_settlements WHERE park_id=? "
        "AND COALESCE(polygon_ids,'') != '' "
        "AND COALESCE(narrative,'') NOT LIKE '[Pit detection %' "
        "AND COALESCE(narrative,'') NOT LIKE '[Turbidity %'",
        (area_id,)).fetchall()


def load_mine_sites():
    """Reported mine sites from both reference sets, deduped by rounded
    coordinate (crisistracker rows appear in mining_reference too)."""
    sites, seen = [], set()
    for path, kind in ((BASE_DIR / "data/eval/mining_reference.json", "reference"),
                       (BASE_DIR / "data/eval/crisistracker/mine_sites.json", "crisistracker")):
        if not path.exists():
            continue
        for s in json.load(open(path))["sites"]:
            key = (round(s["lat"], 3), round(s["lon"], 3))
            if key in seen:
                continue
            seen.add(key)
            sites.append({"lat": s["lat"], "lon": s["lon"],
                          "source": s.get("source", kind),
                          "observed": s.get("observed", "")})
    return sites


def extract_area(conn, area_id, geom, bbox, deadline=None, log=print):
    """Extract candidates for one area. Returns (result_dict, finished)."""
    import numpy as np
    import rasterio
    from rasterio.features import shapes
    from rasterio.windows import from_bounds
    from shapely.geometry import shape as shp, mapping
    from shapely.strtree import STRtree

    rivers = load_rivers(conn, area_id)
    river_tree = STRtree(rivers) if rivers else None
    settlements = load_settlements(conn, area_id)
    mines = load_mine_sites()
    excl_deg = RIVER_EXCLUDE_M / 111_000.0

    wins = windows_for_bbox(*bbox)
    total = len(wins)
    cands, open_tiles, failures = [], {}, 0
    finished = True
    t0 = time.time()
    try:
        for wi, (tile, wx0, wy0, wx1, wy1) in enumerate(wins):
            if deadline and time.time() > deadline:
                log(f"  deadline hit at window {wi}/{total} — UNFINISHED")
                finished = False
                break
            if tile not in open_tiles:
                try:
                    open_tiles[tile] = _open(tile)
                except rasterio.RasterioIOError as ex:
                    # R1: this corpus is inland Africa; an unreadable tile is
                    # a failure, not an ocean.
                    log(f"  tile {tile} unavailable ({str(ex)[:60]}) — UNFINISHED")
                    open_tiles[tile] = None
                    finished = False
                    continue
            src = open_tiles[tile]
            if src is None:
                continue
            try:
                win = from_bounds(wx0, wy0, wx1, wy1, src.transform)
                data = src.read(1, window=win)
                transform = src.window_transform(win)
            except Exception as ex:
                log(f"  window {wi} read failed: {str(ex)[:80]} — UNFINISHED")
                failures += 1
                finished = False
                continue
            mask = (data == CLASS_NEW_PERMANENT) | (data == CLASS_NEW_SEASONAL)
            if not mask.any():
                continue
            for cls in (CLASS_NEW_PERMANENT, CLASS_NEW_SEASONAL):
                cmask = data == cls
                if not cmask.any():
                    continue
                for gj, val in shapes(cmask.astype("uint8"), mask=cmask,
                                      transform=transform):
                    if not val:
                        continue
                    poly = shp(gj)
                    try:
                        clipped = poly.intersection(geom)
                    except Exception:
                        continue
                    if clipped.is_empty:
                        continue
                    c = clipped.centroid
                    deg_km = 111.0 * math.cos(math.radians(c.y))
                    area_ha = clipped.area * (deg_km * 111.0) * 100.0
                    if not (MIN_AREA_HA <= area_ha <= MAX_AREA_HA):
                        continue
                    river_km = None
                    if river_tree is not None:
                        near = river_tree.query(clipped.buffer(excl_deg * 5))
                        if len(near):
                            dmin = min(clipped.distance(rivers[i]) for i in near)
                            river_km = round(dmin * deg_km, 3)
                            if dmin <= excl_deg:
                                continue  # river migration, not a pit lake
                    mine_km = mine_src = None
                    if mines:
                        best = min(mines, key=lambda s: haversine_km(
                            c.y, c.x, s["lat"], s["lon"]))
                        mine_km = round(haversine_km(c.y, c.x, best["lat"],
                                                     best["lon"]), 2)
                        mine_src = f"{best['source']}/{best['observed']}"
                    stl_km = None
                    if settlements:
                        stl_km = round(min(haversine_km(c.y, c.x, r["lat"], r["lon"])
                                           for r in settlements), 2)
                    cands.append({
                        "lat": round(c.y, 5), "lon": round(c.x, 5),
                        "class": CLASS_NAMES[cls],
                        "area_ha": round(area_ha, 2),
                        "nearest_river_km": river_km,
                        "river_distance_basis": ("park_rivers_hydro+park_basin_rivers"
                                                 if rivers else "no river rows for this area — unfiltered"),
                        "nearest_reported_mine_km": mine_km,
                        "nearest_mine_source": mine_src,
                        "nearest_settlement_km": stl_km,
                        "geometry": mapping(clipped.simplify(0.0002, preserve_topology=True)),
                    })
            del data
    finally:
        for s in open_tiles.values():
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass

    cands.sort(key=lambda c: (c["nearest_reported_mine_km"] is None,
                              c["nearest_reported_mine_km"]))
    result = {
        "area_id": area_id,
        "generated_by": "scripts/gsw_new_water.py",
        "generated_at": now_iso(),
        "accessed": now_iso()[:10],
        **PROVENANCE,
        "status": "finished" if finished else "unfinished",
        "windows_total": total,
        "windows_failed": failures,
        "river_rows": len(rivers),
        "settlement_rows": len(settlements),
        "candidate_count": len(cands),
        "by_class": {n: sum(1 for c in cands if c["class"] == n)
                     for n in CLASS_NAMES.values()},
        "candidates": cands,
        "elapsed_s": round(time.time() - t0, 1),
    }
    return result, finished


def run_area(conn, area_id, log=print, minutes=45.0):
    from shapely.geometry import shape as shp
    if conn.execute("SELECT 1 FROM aois WHERE id=?", (area_id,)).fetchone():
        row = conn.execute("SELECT geometry FROM aois WHERE id=?",
                           (area_id,)).fetchone()
        geom = shp(json.loads(row["geometry"]))
    else:
        with open(BASE_DIR / "data" / "keystones_with_boundaries.json") as f:
            parks = {p["id"]: p for p in json.load(f)}
        if area_id not in parks:
            raise SystemExit(f"no such park or AOI: {area_id}")
        geom = shp(parks[area_id]["geometry"])
    log(f"{area_id}: bbox {tuple(round(v, 3) for v in geom.bounds)}")
    result, finished = extract_area(conn, area_id, geom, geom.bounds,
                                    deadline=time.time() + minutes * 60,
                                    log=log)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{area_id}.json"
    with open(out, "w") as f:
        json.dump(result, f, indent=1)
    log(f"{area_id}: {result['candidate_count']} candidates "
        f"({result['by_class']}), status={result['status']} -> {out}")
    return result, finished


# --------------------------------------------------------------- rotation ---

def all_area_ids(conn):
    with open(BASE_DIR / "data" / "keystones_with_boundaries.json") as f:
        ids = [p["id"] for p in json.load(f)]
    ids += [r["id"] for r in conn.execute("SELECT id FROM aois")]
    return ids


def run_rotate(conn, n, minutes):
    state = {}
    if STATE_PATH.exists():
        state = json.load(open(STATE_PATH))
    ids = all_area_ids(conn)
    # Stalest first; never-scanned and previously-unfinished areas lead.
    def key(aid):
        s = state.get(aid)
        if not s or s.get("status") != "finished":
            return ""
        return s["scanned_at"]
    todo = sorted(ids, key=key)[:n]
    ok, results = True, []
    for aid in todo:
        try:
            result, finished = run_area(conn, aid, minutes=minutes)
        except Exception as ex:
            print(f"{aid}: FAILED ({ex})")
            state[aid] = {"scanned_at": now_iso(), "status": "error",
                          "error": str(ex)[:200]}
            ok = False
            continue
        # R1: an unfinished area keeps its stale rank and is retried next night.
        state[aid] = {"scanned_at": now_iso(),
                      "status": result["status"],
                      "candidates": result["candidate_count"]}
        if not finished:
            ok = False
        results.append((aid, result["candidate_count"], result["status"]))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    done = sum(1 for s in state.values() if s.get("status") == "finished")
    summary = "; ".join(f"{a}: {c} ({s})" for a, c, s in results) or "nothing ran"
    try:
        from cron_notify import notify_status
        notify_status("gsw_water_scan_" + ("success" if ok else "failed"),
                      "GSW new-water scan",
                      f"{summary}. {done}/{len(ids)} areas scanned.")
    except Exception as ex:
        print(f"notify failed: {ex}")
    print(f"rotation: {summary}. {done}/{len(ids)} areas finished.")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi")
    ap.add_argument("--park")
    ap.add_argument("--rotate", type=int, metavar="N")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--minutes", type=float, default=45)
    args = ap.parse_args()

    import aoi_lib
    conn = aoi_lib.connect(readonly=True)

    if args.eval:
        from gsw_eval import run_eval
        run_eval(conn)
        return
    if args.rotate:
        sys.exit(0 if run_rotate(conn, args.rotate, args.minutes) else 1)
    target = args.aoi or args.park
    if not target:
        ap.error("need --aoi, --park, --rotate or --eval")
    _, finished = run_area(conn, target, minutes=args.minutes)
    sys.exit(0 if finished else 1)


if __name__ == "__main__":
    main()
