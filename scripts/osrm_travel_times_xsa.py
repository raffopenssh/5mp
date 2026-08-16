#!/usr/bin/env python3
"""Travel-time matrix for the XSA mining grid, via a LOCAL OSRM.

Question: how long does it take to reach each 0.05-deg grid cell from the
nearest *place people actually start from* (settlements with observed GHSL
footprints + OSM city/town/village/hamlet)?  Two profiles:

  car   (localhost:5001, osrm car.lua on the merged CAR/S.Sudan/Sudan extract)
  foot  (localhost:5002, osrm foot.lua, 5 km/h on the path/track network)

People in this landscape walk long distances (up to ~40 km/day), so foot is
a first-class mode, not a fallback.  For pairs the network cannot connect
(disconnected components, unmapped bush) we use straight-line distance at
WALK_BUSH_KMH = 4 km/h - a stated assumption, printed into the metadata,
not a hidden default.  Snap gaps (point to nearest network edge) are walked
at the same bush pace and added to the network time.

Effective time per (station, cell) = min over modes of:
  car:  snap_gap_walk + car_duration      (you still walk to the road)
  foot: snap_gap_walk + foot_duration
  bush: straight_line / WALK_BUSH_KMH     (always available)

Output:
  data/eval/xsa_mining/osrm_times.npz
     t_min        uint16 minutes, (n_stations, n_cells)  effective best time
     mode         uint8  (0=car,1=foot,2=bush), argmin mode per pair
     stations     float32 (n,2) lon,lat  + station kind/name in meta json
     cells        float32 (m,2) lon,lat
  data/eval/xsa_mining/osrm_times_meta.json  (assumptions, station list,
     per-cell best station + time percentiles)

Run in tmux: ~1h.  Read-only against db.sqlite3.
"""
import json
import math
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from predict_mining_xsa import load_aoi, make_grid, AOI  # noqa: E402

OUTDIR = ROOT / "data/eval/xsa_mining"
CAR_URL = "http://localhost:5001/table/v1/driving/"
FOOT_URL = "http://localhost:5002/table/v1/foot/"
WALK_BUSH_KMH = 4.0     # straight-line pace where no network path exists
DEDUPE_KM = 2.0         # stations closer than this collapse to one
SRC_CHUNK = 25          # sources per /table request
DST_CHUNK = 2000        # destinations per /table request
UINT16_CAP = 65534      # minutes; 65535 = unreachable sentinel (unused: bush
                        # fallback means every pair has a finite time)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def km(lat1, lon1, lat2, lon2):
    ky = 111.32
    kx = ky * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat1 - lat2) * ky, (lon1 - lon2) * kx)


def load_stations():
    """Places people start from: footprint-backed settlements + OSM places.
    Dedupe at DEDUPE_KM keeping the larger (population, then area)."""
    c = sqlite3.connect(ROOT / "db.sqlite3")
    rows = []
    for lat, lon, name, cls, pop, area in c.execute(
            "SELECT lat, lon, nearest_place, classification, "
            "population_est, area_m2 FROM park_settlements "
            "WHERE park_id=? AND polygon_ids<>''", (AOI,)):
        rows.append(dict(lat=lat, lon=lon, name=name or "", kind="settlement",
                         cls=cls, pop=pop or 0, area=area or 0))
    for name, ptype, lat, lon in c.execute(
            "SELECT name, place_type, lat, lon FROM osm_places "
            "WHERE park_id=? AND place_type IN "
            "('city','town','village','hamlet')", (AOI,)):
        rows.append(dict(lat=lat, lon=lon, name=name or "", kind="osm_place",
                         cls=ptype, pop=0, area=0))
    rows.sort(key=lambda r: (-(r["pop"]), -(r["area"])))
    kept = []
    for r in rows:
        if any(km(r["lat"], r["lon"], k["lat"], k["lon"]) < DEDUPE_KM
               for k in kept):
            continue
        kept.append(r)
    return kept


def table(url_base, src_pts, dst_pts):
    """One OSRM /table call. Returns (durations s, src_snap m, dst_snap m).
    None durations stay None."""
    coords = ";".join(f"{x:.5f},{y:.5f}" for x, y in src_pts + dst_pts)
    ns, nd = len(src_pts), len(dst_pts)
    url = (url_base + coords
           + "?sources=" + ";".join(map(str, range(ns)))
           + "&destinations=" + ";".join(map(str, range(ns, ns + nd)))
           + "&annotations=duration")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                d = json.load(r)
            break
        except Exception as e:
            if attempt == 2:
                raise
            log("  retry", attempt + 1, repr(e)[:120])
            time.sleep(5)
    dur = d["durations"]
    ssnap = [s.get("distance", 0.0) for s in d["sources"]]
    dsnap = [s.get("distance", 0.0) for s in d["destinations"]]
    return dur, ssnap, dsnap


def main():
    poly = load_aoi()
    grid = make_grid(poly)                      # (m,2) lon,lat
    stations = load_stations()
    log(f"{len(stations)} stations, {len(grid)} cells -> "
        f"{len(stations)*len(grid)/1e6:.1f}M pairs x2 profiles")

    s_lonlat = [(s["lon"], s["lat"]) for s in stations]
    g_lonlat = [(float(x), float(y)) for x, y in grid]
    n, m = len(stations), len(grid)

    # straight-line km matrix in blocks (bush fallback + sanity floor)
    lat0 = float(np.mean(grid[:, 1]))
    ky, kx = 111.32, 111.32 * math.cos(math.radians(lat0))
    sk = np.array([[x * kx, y * ky] for x, y in s_lonlat], np.float32)
    gk = np.array([[x * kx, y * ky] for x, y in g_lonlat], np.float32)

    t_min = np.zeros((n, m), np.float32)
    mode = np.full((n, m), 2, np.uint8)         # start as bush
    # bush baseline
    for i0 in range(0, n, 200):
        i1 = min(i0 + 200, n)
        dd = np.sqrt(((sk[i0:i1, None, :] - gk[None, :, :]) ** 2).sum(-1))
        t_min[i0:i1] = dd / WALK_BUSH_KMH * 60.0
    log("bush baseline done")

    walk_ms = WALK_BUSH_KMH * 1000 / 60.0       # metres per minute
    for prof, base in (("car", CAR_URL), ("foot", FOOT_URL)):
        pcode = 0 if prof == "car" else 1
        t0 = time.time()
        req = 0
        for i0 in range(0, n, SRC_CHUNK):
            i1 = min(i0 + SRC_CHUNK, n)
            for j0 in range(0, m, DST_CHUNK):
                j1 = min(j0 + DST_CHUNK, m)
                dur, ssnap, dsnap = table(base, s_lonlat[i0:i1],
                                          g_lonlat[j0:j1])
                req += 1
                dsn = np.array(dsnap, np.float32) / 1000 / WALK_BUSH_KMH * 60
                for a, row in enumerate(dur):
                    ssn = ssnap[a] / 1000 / WALK_BUSH_KMH * 60
                    rr = np.array([v if v is not None else np.inf
                                   for v in row], np.float32) / 60.0
                    eff = rr + ssn + dsn
                    upd = eff < t_min[i0 + a, j0:j1]
                    t_min[i0 + a, j0:j1][upd] = eff[upd]
                    mode[i0 + a, j0:j1][upd] = pcode
            if (i0 // SRC_CHUNK) % 10 == 0:
                done = i1 / n
                log(f"{prof}: {i1}/{n} stations "
                    f"({req} req, {time.time()-t0:.0f}s, "
                    f"eta {((time.time()-t0)/max(done,1e-9))*(1-done):.0f}s)")
        log(f"{prof} done in {time.time()-t0:.0f}s")

    t_u16 = np.minimum(np.round(t_min), UINT16_CAP).astype(np.uint16)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUTDIR / "osrm_times.npz",
        t_min=t_u16, mode=mode,
        stations=np.array(s_lonlat, np.float32),
        cells=np.array(g_lonlat, np.float32))

    best = t_min.min(axis=0)
    argb = t_min.argmin(axis=0)
    meta = dict(
        generated_by="scripts/osrm_travel_times_xsa.py",
        aoi=AOI,
        osrm=dict(
            extract="geofabrik CAR + South Sudan + Sudan, bbox 22.3,4.1,"
                    "31.4,11.2, downloaded 2026-08-16",
            profiles=dict(car="car.lua @ localhost:5001",
                          foot="foot.lua (5 km/h) @ localhost:5002")),
        assumptions=dict(
            walk_bush_kmh=WALK_BUSH_KMH,
            bush_rule="straight-line at walk_bush_kmh where no network "
                      "path exists; snap gaps walked at the same pace and "
                      "added to network time",
            effective_time="min(car, foot, bush) per (station, cell)",
            station_dedupe_km=DEDUPE_KM),
        n_stations=n, n_cells=m,
        stations=[dict(lon=round(s["lon"], 5), lat=round(s["lat"], 5),
                       name=s["name"], kind=s["kind"], cls=s["cls"],
                       pop=s["pop"]) for s in stations],
        cell_best_minutes_percentiles={
            str(q): round(float(np.percentile(best, q)), 1)
            for q in (5, 25, 50, 75, 95, 99)},
        mode_share_at_best={
            ["car", "foot", "bush"][k]:
                round(float(np.mean(mode[argb, np.arange(m)] == k)), 3)
            for k in (0, 1, 2)},
    )
    json.dump(meta, open(OUTDIR / "osrm_times_meta.json", "w"), indent=1)
    log("wrote osrm_times.npz + osrm_times_meta.json;",
        "median cell reach:", meta["cell_best_minutes_percentiles"]["50"],
        "min; mode share:", meta["mode_share_at_best"])


if __name__ == "__main__":
    main()
