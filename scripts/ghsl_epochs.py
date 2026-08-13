#!/usr/bin/env python3
"""Measure settlement PERSISTENCE from GHSL back-epochs (E2000, E2015).

`settlement_type` was retired to NULL because size cannot say whether a camp is
seasonal; inter-epoch persistence can (docs/agents/settlements.md F12,
docs/PLAN_NEW_DATA_LAYERS.md WP1). GHS_BUILT_S R2023A publishes the same 100 m
Mollweide tile grid for epochs back to 1975, so "was this ground built in
2000?" is a raster read. Two back-epochs are enough for three words:

    permanent     built surface in E2000 >= 25% of today's (E2030) surface
    established   >= 25% in E2015 but not in E2000
    recent        below 25% in both (NOT "temporary": a 2021 village is
                  recent, not seasonal -- the word claims only what the
                  measurement supports)
    NULL          unmeasured. A pixel absent from an old epoch and a tile
                  absent from the download are DIFFERENT STATES (invariant 1):
                  a missing tile leaves persistence NULL with
                  persistence_source='tile_missing'.

THE SAME PIXELS, BY CONSTRUCTION. Each epoch raster is read through
`ghsl_tiles._read_window_like` with the E2030 window's affine as the
authority -- the exact defence against the one-pixel-offset bug that moved 12%
of Comoé's population (settlements.md "Two reader bugs"). Each footprint's
E2030 surface is re-derived alongside and compared to the value stored in
properties_json: a mismatch means the mask moved and the run reports
UNFINISHED rather than writing epoch surfaces for different ground.

Called per area by scripts/backfill_settlement_surface.py after every
recluster (the rebuild deletes+reinserts cluster rows, so persistence must be
re-derived each time), and runnable standalone:

    python3 scripts/ghsl_epochs.py --area CAF_Chinko
    python3 scripts/ghsl_epochs.py --area XSA_Study_Area

State: data/ghsl_epoch_state.json -- per area: when, version, per-tile
sha256 of every epoch raster used (R3: a derived flag names its input and a
db test compares them), and the persistence breakdown.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

import ghsl_tiles

STATE_FILE = BASE_DIR / "data" / "ghsl_epoch_state.json"

BACK_EPOCHS = ("E2000", "E2015")
PERSISTENCE_SOURCE = "ghsl_" + "+".join(BACK_EPOCHS)
TILE_MISSING = "tile_missing"
# Fraction of today's built surface that must already exist in a back-epoch
# for the cluster to count as present then. 25% rather than >0 because the
# 100 m fractional raster puts a few m² of noise in many pixels; a quarter of
# today's surface is unambiguously "the settlement was there".
PERSIST_FRACTION = 0.25
# Bump when a change HERE would give a different word for the same ground.
EPOCHS_VERSION = "2026-08-13a"


def epoch_product(epoch):
    return f"GHS_BUILT_S_{epoch}_GLOBE_R2023A_54009_100_V1_0"


def epoch_base_url(epoch):
    return ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
            "GHS_BUILT_S_GLOBE_R2023A/"
            f"GHS_BUILT_S_{epoch}_GLOBE_R2023A_54009_100/V1-0/tiles")


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


def _footprint_epoch_surfaces(conn, area_id, geom_wgs84, log=print):
    """{feature_id: {'e2030': m2, 'E2000': m2|None, 'E2015': m2|None}}.

    Per-tile: one window over the area geometry from the E2030 raster (the
    authority for offsets), the two back-epochs read boundless against it,
    then a zonal sum per footprint over the same rasterized mask
    `polygons_in` uses. None for an epoch whose tile is not published.
    """
    import numpy as np
    import rasterio  # noqa: F401  (ghsl_tiles helpers need it importable)
    from rasterio.features import geometry_window, rasterize
    from rasterio.windows import Window
    from shapely.geometry import shape

    rows = conn.execute(
        "SELECT feature_id, geojson FROM feature_geometries "
        "WHERE feature_type='settlement' AND park_id=?", (area_id,)).fetchall()
    if not rows:
        return {}, {}
    feats = [(r[0], ghsl_tiles.to_mollweide(shape(json.loads(r[1]))))
             for r in rows]

    out = {}
    tile_shas = {}
    for tile in ghsl_tiles.tiles_for_geom(geom_wgs84):
        base_path = ghsl_tiles.ensure_tile(tile, log=log)
        if base_path is None:
            continue  # ocean tile: no footprints came from it either
        geom_moll = ghsl_tiles.to_mollweide(geom_wgs84)
        data, transform = ghsl_tiles._read_window(base_path, geom_moll)
        if data is None:
            continue
        binary = (data > ghsl_tiles.PIXEL_THRESHOLD_M2)
        # Native dtypes throughout: an AOI window can be 10000x10000 and a
        # float64 copy per raster is 800 MB each.
        surface = np.where(binary, data, 0)

        epoch_arrays = {}
        shas = {"E2030": _sha256(base_path)}
        for epoch in BACK_EPOCHS:
            p = ghsl_tiles.ensure_tile(tile, log=log,
                                       product=epoch_product(epoch),
                                       base_url=epoch_base_url(epoch))
            if p is None:
                log(f"  ghsl-epochs: tile {tile} has no {epoch} raster; "
                    f"its footprints stay unmeasured")
                epoch_arrays[epoch] = None
                continue
            arr = ghsl_tiles._read_window_like(p, transform, data.shape)
            if arr is None:
                log(f"  ghsl-epochs: tile {tile} {epoch} not on the E2030 "
                    f"grid; treating as missing")
                epoch_arrays[epoch] = None
                continue
            epoch_arrays[epoch] = arr
            shas[epoch] = _sha256(p)
        tile_shas[tile] = shas

        ds = ghsl_tiles._FakeDS(data.shape, transform)
        h, w = data.shape
        minx, maxy = transform * (0, 0)
        maxx, miny = transform * (w, h)
        for fid, poly in feats:
            b = poly.bounds
            if b[2] < minx or b[0] > maxx or b[3] < miny or b[1] > maxy:
                continue
            try:
                win = geometry_window(ds, [poly])
                win = win.intersection(Window(0, 0, w, h))
            except Exception:
                continue
            r0, r1 = int(win.row_off), int(win.row_off + win.height)
            c0, c1 = int(win.col_off), int(win.col_off + win.width)
            if r1 <= r0 or c1 <= c0:
                continue
            sub_t = ghsl_tiles._window_transform(transform, r0, c0)
            mask = rasterize([(poly, 1)], out_shape=(r1 - r0, c1 - c0),
                             transform=sub_t, fill=0, dtype="uint8",
                             all_touched=False)
            if mask.sum() == 0:
                mask = binary[r0:r1, c0:c1].astype("uint8")
            e2030 = float((surface[r0:r1, c0:c1] * mask).sum())
            if e2030 == 0:
                continue  # this tile's window does not hold the footprint
            entry = out.setdefault(fid, {"e2030": 0.0})
            entry["e2030"] += e2030
            for epoch in BACK_EPOCHS:
                arr = epoch_arrays[epoch]
                if arr is None:
                    entry[epoch] = None  # missing tile poisons the footprint
                elif entry.get(epoch, 0.0) is not None:
                    entry[epoch] = entry.get(epoch, 0.0) + float(
                        (arr[r0:r1, c0:c1] * mask).sum())
        del data, binary, surface, epoch_arrays
    return out, tile_shas


def classify(today_m2, e2000, e2015):
    """(persistence, source) for one cluster. None-in means tile_missing."""
    if e2000 is None or e2015 is None:
        return None, TILE_MISSING
    if today_m2 <= 0:
        return None, TILE_MISSING
    if e2000 >= PERSIST_FRACTION * today_m2:
        return "permanent", PERSISTENCE_SOURCE
    if e2015 >= PERSIST_FRACTION * today_m2:
        return "established", PERSISTENCE_SOURCE
    return "recent", PERSISTENCE_SOURCE


def derive_for_area(conn, area_id, geom_wgs84, log=print):
    """Measure + write persistence for one area's clusters.

    Returns (ok, summary). ok=False is UNFINISHED: nothing was stamped, the
    caller must not record the area as done (invariant 1).
    """
    clusters = conn.execute(
        "SELECT id, polygon_ids, area_m2 FROM park_settlements "
        "WHERE park_id=? AND polygon_ids IS NOT NULL AND polygon_ids != ''",
        (area_id,)).fetchall()
    if not clusters:
        return True, {"clusters": 0, "note": "no ghsl clusters"}

    fp, tile_shas = _footprint_epoch_surfaces(conn, area_id, geom_wgs84, log=log)
    if not fp:
        log(f"  ghsl-epochs UNFINISHED: no footprint could be read for "
            f"{area_id} ({len(clusters)} clusters exist)")
        return False, {"error": "no footprints measured"}

    # Alignment check: the re-derived E2030 surface must reproduce what the
    # ingest stored, or the epoch values describe different ground. Small
    # per-footprint drift (simplify(0.0001) on the stored polygon) is fine;
    # a systematic offset is not.
    import sqlite3  # noqa: F401
    stored = dict(conn.execute(
        "SELECT feature_id, json_extract(properties_json,'$.area_m2') "
        "FROM feature_geometries WHERE feature_type='settlement' AND park_id=?",
        (area_id,)).fetchall())
    tot_stored = sum(v or 0 for k, v in stored.items() if k in fp)
    tot_derived = sum(v["e2030"] for v in fp.values())
    if tot_stored > 0 and abs(tot_derived - tot_stored) > 0.05 * tot_stored:
        log(f"  ghsl-epochs UNFINISHED: re-derived E2030 surface "
            f"{tot_derived:,.0f} m² vs stored {tot_stored:,.0f} m² "
            f"(>5% apart) -- mask offset, refusing to write")
        return False, {"error": "e2030 surface mismatch",
                       "derived_m2": tot_derived, "stored_m2": tot_stored}

    updates, breakdown = [], {}
    for cid, ids, today in clusters:
        e2000, e2015 = 0.0, 0.0
        missing = False
        for pid in ids.split(","):
            pid = pid.strip()
            f = fp.get(pid)
            if f is None:
                # Footprint yielded no measurable pixels (e.g. thinner than a
                # pixel centre in every window): treat its epochs as 0, today's
                # surface still comes from the stored cluster value.
                continue
            if f.get("E2000") is None or f.get("E2015") is None:
                missing = True
                break
            e2000 += f["E2000"]
            e2015 += f["E2015"]
        if missing:
            p, src = None, TILE_MISSING
            e2000v = e2015v = None
        else:
            p, src = classify(today or 0, e2000, e2015)
            e2000v, e2015v = round(e2000, 2), round(e2015, 2)
        breakdown[p or f"unmeasured:{src}"] = breakdown.get(
            p or f"unmeasured:{src}", 0) + 1
        updates.append((p, src, e2000v, e2015v, cid))

    ghsl_tiles.write_rows(conn, """
        UPDATE park_settlements SET persistence=?, persistence_source=?,
               surface_e2000_m2=?, surface_e2015_m2=? WHERE id=?""", updates)
    log(f"  ghsl-epochs: {area_id}: {len(updates)} clusters -> {breakdown}")
    return True, {"clusters": len(updates), "breakdown": breakdown,
                  "tiles": tile_shas}


def stamp(area_id, summary):
    state = load_state()
    entry = dict(summary)
    entry["version"] = EPOCHS_VERSION
    entry["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry["persistence_source"] = PERSISTENCE_SOURCE
    entry["citation"] = ("Pesaresi M., Politis P. (2023): GHS-BUILT-S R2023A. "
                         "European Commission JRC. "
                         "doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA")
    entry["terms"] = "CC BY 4.0"
    state[area_id] = entry
    save_state(state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True, help="park or AOI id")
    a = ap.parse_args()

    sys.path.insert(0, str(BASE_DIR / "scripts"))
    from backfill_settlement_surface import connect, targets
    conn = connect()
    match = [t for t in targets(conn) if t[0] == a.area]
    if not match:
        print(f"no such area: {a.area}")
        return 2
    aid, geom, _is_aoi = match[0]
    ok, summary = derive_for_area(conn, aid, geom)
    if ok:
        stamp(aid, summary)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
