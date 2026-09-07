#!/usr/bin/env python3
"""Region-wide (CAF + SSD + SDN) feature grid for the mining-context model.

    python3 scripts/regional_mining/build_features.py   # -> data/eval/regional_mining/features.npz

Same SIGNALS as scripts/predict_mining_xsa.py (that model is the thing that
worked; this is the same model given more truth to be scored against), but
built from region-wide inputs instead of the AOI-scoped park tables:

  gold contacts     data/geomaps/{car,sudan}_contacts.geojson + affinity rules
  gold units        data/geomaps/{car,sudan}_units.geojson (gold weight of the
                    unit under the cell - a NEW candidate signal, measured
                    before it may vote)
  rivers            OSM waterway=river (regional; XSA used HydroRIVERS ord>=3
                    - the harness checks the two agree on XSA)
  settlements       GHSL built-up E2015 / E2000 / POP E2030 summed to the
                    0.05 deg grid (the same rasters park_settlements is
                    measured from)
  1930s sheets      data/histmaps/sudan250k_labels.gpkg (labels, tracks,
                    water symbols) - region-wide, coverage-masked
  modern roads      OSM highway=* (for "abandoned track")
  reach             OSM places + UCDP GED (prec<=3) + Crisis Tracker - KDE

Everything is stored per cell as a distance (km) or a value, plus the AOI
membership mask, so the scorer never touches the inputs again.
"""
import json, gzip, sqlite3, sys, zipfile, csv, io, subprocess, os
from pathlib import Path
import numpy as np
from shapely.geometry import shape, Point, box, LineString, MultiLineString
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree
from scipy.spatial import cKDTree
import rasterio
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[2]
W = ROOT / "data/eval/regional_mining/work"
OUT = ROOT / "data/eval/regional_mining/features.npz"
CELL = 0.05
KM = 111.32
ISO = ("CAF", "SSD", "SDN")
OSM = {"CAF": "central-african-republic", "SSD": "south-sudan", "SDN": "sudan"}


def log(*a): print(*a, file=sys.stderr, flush=True)


def region_polys():
    polys = {}
    for iso, fn in (("CAF", "caf_admin2"), ("SSD", "ssd_admin2"), ("SDN", "sdn_admin2")):
        d = json.load(open(ROOT / f"data/admin_cod/{fn}.geojson"))
        polys[iso] = unary_union([shape(f["geometry"]) for f in d["features"]]).buffer(0)
    return polys


def proj(lonlat, lat0):
    a = np.asarray(lonlat, float).reshape(-1, 2)
    return np.c_[a[:, 0] * KM * np.cos(np.radians(lat0)), a[:, 1] * KM]


def line_pts(geoms, step_km, lat0):
    """Densify lines to points for KD-tree distance (max error ~step/2)."""
    pts = []
    for g in geoms:
        if g.is_empty: continue
        parts = g.geoms if hasattr(g, "geoms") else [g]
        for p in parts:
            if p.geom_type != "LineString": continue
            L = p.length * KM  # deg->km approx
            n = max(2, int(L / step_km) + 1)
            for t in np.linspace(0, 1, n):
                q = p.interpolate(t, normalized=True); pts.append((q.x, q.y))
    return proj(pts, lat0) if pts else np.zeros((0, 2))


def dist(gk, pts):
    if len(pts) == 0: return np.full(len(gk), 1e9)
    d, _ = cKDTree(pts).query(gk); return d


def gold_geoms():
    pwd = None
    for line in open(ROOT / "secrets.env"):
        if line.startswith("AOI_OWNER_PWD="): pwd = line.split("=", 1)[1].strip().strip('"')
    cat = json.loads(subprocess.check_output(["curl", "-fsS", f"http://localhost:8000/api/geomap?pwd={pwd}"]))
    contacts, units = [], []
    for s in cat["sheets"]:
        if s["id"] not in ("car", "sudan"): continue
        c = s["catalogue"]
        lith = {x["code"]: (x.get("lith") or "mixed") for x in c["classes"]}
        rules = {r["pair"]: r for r in c["std"]["contact_rules"]}
        for f in json.load(open(ROOT / f"data/geomaps/{s['id']}_contacts.geojson"))["features"]:
            pr = f["properties"]
            pair = "|".join(sorted([lith.get(pr["code_a"], "mixed"), lith.get(pr["code_b"], "mixed")]))
            w = max([a["weight"] for a in (rules.get(pair) or {}).get("affinity", []) if a["commodity"] == "gold"] or [0])
            if w >= 2: contacts.append(shape(f["geometry"]))
        for f in json.load(open(ROOT / f"data/geomaps/{s['id']}_units.geojson"))["features"]:
            w = max([a["weight"] for a in f["properties"].get("affinity", []) if a["commodity"] == "gold"] or [0])
            units.append((shape(f["geometry"]), w))
    return contacts, units


def gpkg_geom(blob):
    from shapely import wkb
    flags = blob[3]; env = (flags >> 1) & 0x07
    off = 8 + (32 if env == 1 else 48 if env in (2, 3) else 64 if env == 4 else 0)
    return wkb.loads(bytes(blob[off:]))


def main():
    log("region polygons...")
    polys = region_polys()
    region = unary_union(list(polys.values()))
    minx, miny, maxx, maxy = region.bounds
    lat0 = region.centroid.y
    xs = np.arange(np.floor(minx / CELL) * CELL + CELL / 2, maxx, CELL)
    ys = np.arange(np.floor(miny / CELL) * CELL + CELL / 2, maxy, CELL)
    gx, gy = np.meshgrid(xs, ys); grid = np.c_[gx.ravel(), gy.ravel()]
    iso_of = np.zeros(len(grid), dtype="U3")
    for iso, p in polys.items():
        pp = prep(p); bx = p.bounds
        m = (grid[:, 0] >= bx[0]) & (grid[:, 0] <= bx[2]) & (grid[:, 1] >= bx[1]) & (grid[:, 1] <= bx[3])
        for i in np.nonzero(m)[0]:
            if iso_of[i] == "" and pp.contains(Point(grid[i])): iso_of[i] = iso
    keep = iso_of != ""
    grid, iso_of = grid[keep], iso_of[keep]
    gk = proj(grid, lat0)
    log(f"{len(grid)} cells: " + ", ".join(f"{i}={int((iso_of==i).sum())}" for i in ISO))

    aoi = shape(json.loads(sqlite3.connect(ROOT / "db.sqlite3").execute(
        "SELECT geometry FROM aois WHERE id='XSA_Study_Area'").fetchone()[0]))
    pa = prep(aoi); bx = aoi.bounds
    in_aoi = np.array([(bx[0] <= x <= bx[2] and bx[1] <= y <= bx[3] and pa.contains(Point(x, y))) for x, y in grid])
    log(f"  {in_aoi.sum()} cells in XSA")

    feats = {}
    log("gold contacts + units...")
    contacts, units = gold_geoms()
    feats["d_gold"] = dist(gk, line_pts(contacts, 1.0, lat0))
    unit_w = np.zeros(len(grid))
    tree = STRtree([u[0] for u in units]); uw = [u[1] for u in units]
    pts = [Point(x, y) for x, y in grid]
    for i, pt in enumerate(pts):
        for j in tree.query(pt):
            if units[j][0].contains(pt): unit_w[i] = max(unit_w[i], uw[j])
    feats["unit_gold_w"] = unit_w
    # geology coverage: a cell under any mapped unit
    cov_geo = np.zeros(len(grid), bool)
    for i, pt in enumerate(pts):
        cov_geo[i] = any(units[j][0].contains(pt) for j in tree.query(pt))
    feats["cov_geology"] = cov_geo
    log(f"  contacts w>=2: {len(contacts)}; cells under mapped geology: {cov_geo.sum()}")

    log("OSM rivers / roads / places / mining...")
    riv, roads, places, place_pop = [], [], [], []
    for iso in ISO:
        for f in json.load(open(W / f"{OSM[iso]}_rivers.geojson"))["features"]:
            if f["geometry"]["type"] in ("LineString", "MultiLineString"): riv.append(shape(f["geometry"]))
        for f in json.load(open(W / f"{OSM[iso]}_roads.geojson"))["features"]:
            if f["geometry"]["type"] in ("LineString", "MultiLineString"): roads.append(shape(f["geometry"]))
        for f in json.load(open(W / f"{OSM[iso]}_places.geojson"))["features"]:
            if f["geometry"]["type"] == "Point": places.append(f["geometry"]["coordinates"])
    feats["d_river_osm"] = dist(gk, line_pts(riv, 1.0, lat0))
    feats["d_road_osm"] = dist(gk, line_pts(roads, 1.0, lat0))
    place_k = proj(places, lat0)
    feats["d_osm_place"] = dist(gk, place_k)
    log(f"  rivers {len(riv)}, roads {len(roads)}, places {len(places)}")

    log("GHSL...")
    def sample(fn):
        with rasterio.open(W / fn) as r:
            a = r.read(1); rows, cols = rasterio.transform.rowcol(r.transform, grid[:, 0], grid[:, 1])
            rows = np.clip(rows, 0, a.shape[0] - 1); cols = np.clip(cols, 0, a.shape[1] - 1)
            v = a[rows, cols]; v[v < 0] = 0; return v
    b15 = sample("GHS_BUILT_S_E2015_005.tif"); b00 = sample("GHS_BUILT_S_E2000_005.tif"); pop = sample("GHS_POP_E2030_005.tif")
    feats["built_2015_m2"] = b15; feats["built_2000_m2"] = b00; feats["pop_2030"] = pop
    # settlement presence: a cell with any built surface >= 2,500 m2 (~25 huts)
    sett = b15 >= 2500
    feats["d_settlement"] = dist(gk, gk[sett])
    feats["d_pop500"] = dist(gk, gk[pop >= 500])
    grew = sett & (b15 > 1.333 * np.maximum(b00, 1))
    feats["d_settlement_grew"] = dist(gk, gk[grew])
    recent = sett & (b00 < 0.25 * b15)
    feats["d_settlement_recent"] = dist(gk, gk[recent])
    log(f"  settled cells {sett.sum()}, pop>=500 {int((pop>=500).sum())}, grew {grew.sum()}, recent {recent.sum()}")

    log("1 km fabric (GHSL + WorldCover cropland)...")
    def r1(fn):
        with rasterio.open(W / fn) as r:
            return r.read(1), r.transform
    b15k, tr1 = r1("GHS_BUILT_S_E2015_001.tif"); b00k, _ = r1("GHS_BUILT_S_E2000_001.tif"); popk, _ = r1("GHS_POP_E2030_001.tif"); cropk, _ = r1("WC2021_cropland_frac_001.tif")
    b15k[b15k < 0] = 0; b00k[b00k < 0] = 0; popk[popk < 0] = 0
    # 1 km-box cropland fraction (3x3 of 0.01 deg) around each 1 km cell
    from scipy.ndimage import uniform_filter
    cropv = np.where(cropk >= 0, cropk, 0.0); cropn = (cropk >= 0).astype(float)
    crop3 = uniform_filter(cropv, 3, mode="constant") / np.maximum(uniform_filter(cropn, 3, mode="constant"), 1e-6)
    crop3[uniform_filter(cropn, 3, mode="constant") < 0.5] = np.nan
    settk = b15k >= 2500                       # >= 2,500 m2 built in 1 km2 (~25 huts)
    popk500 = popk >= 500
    poor = crop3 < 0.02
    def cells_xy(mask):
        rr, cc = np.nonzero(mask)
        xs_, ys_ = rasterio.transform.xy(tr1, rr, cc)
        return proj(np.c_[xs_, ys_], lat0)
    feats["d_sett1k"] = dist(gk, cells_xy(settk))
    feats["d_sett1k_pop500"] = dist(gk, cells_xy(settk & popk500))
    feats["d_sett1k_croppoor"] = dist(gk, cells_xy(settk & poor))
    feats["d_sett1k_pop500_croppoor"] = dist(gk, cells_xy(settk & popk500 & poor))
    recentk = settk & (b00k < 0.25 * b15k)
    feats["d_sett1k_recent"] = dist(gk, cells_xy(recentk))
    feats["d_sett1k_recent_croppoor"] = dist(gk, cells_xy(recentk & poor))
    grewk = settk & (b15k > 1.333 * np.maximum(b00k, 1))
    feats["d_sett1k_grew_croppoor"] = dist(gk, cells_xy(grewk & poor))
    # cell-level cropland fraction (for "no farmland" as a standalone context)
    rows, cols = rasterio.transform.rowcol(tr1, grid[:, 0], grid[:, 1])
    rows = np.clip(rows, 0, crop3.shape[0] - 1); cols = np.clip(cols, 0, crop3.shape[1] - 1)
    feats["crop_frac_wc"] = crop3[rows, cols]
    log(f"  1km settled {settk.sum()}, pop500 {int((settk&popk500).sum())}, crop-poor {int((settk&poor).sum())}, pop500&poor {int((settk&popk500&poor).sum())}, recent {recentk.sum()}, recent&poor {int((recentk&poor).sum())}")

    log("land-cover composition (environmental analogue covariates)...")
    with rasterio.open(W / "WC2021_landcover_frac_005.tif") as r:
        rows, cols = rasterio.transform.rowcol(r.transform, grid[:, 0], grid[:, 1])
        rows = np.clip(rows, 0, r.height - 1); cols = np.clip(cols, 0, r.width - 1)
        lc = r.read()[:, rows, cols]
    lc[lc < 0] = np.nan
    for i, nm in enumerate(("tree", "shrub", "grass", "crop", "built", "bare", "water", "wetland")):
        feats[f"lc_{nm}"] = lc[i]

    log("1930s sheets...")
    gp = sqlite3.connect(ROOT / "data/histmaps/sudan250k_labels.gpkg")
    labels = [(gpkg_geom(r[0]), r[1], r[2], r[3]) for r in gp.execute("SELECT geom, text, category, note_topic FROM sudan250k_labels")]
    labels = [l for l in labels if l[0].geom_type == "Point"]
    lab_k = proj([(l[0].x, l[0].y) for l in labels], lat0)
    d_any = dist(gk, lab_k); feats["cov_hist"] = d_any <= 15.0
    feats["d_hist_hill"] = dist(gk, proj([(l[0].x, l[0].y) for l in labels if l[2] == "terrain"], lat0))
    hp = [l for l in labels if l[2] == "place"]
    hp_k = proj([(l[0].x, l[0].y) for l in hp], lat0)
    feats["d_hist_place"] = dist(gk, hp_k)
    MIN_WORDS = ("mine", "mines", "workings", "diggings", "goldwash", "gold", "copper", "iron work", "iron mines")
    hm = [l for l in labels if l[1] and any(w in l[1].lower() for w in MIN_WORDS) and l[2] in ("place", "note", "terrain")]
    feats["d_hist_mine_note"] = dist(gk, proj([(l[0].x, l[0].y) for l in hm], lat0))
    tracks = [gpkg_geom(r[0]) for r in gp.execute("SELECT geom FROM sudan250k_lines WHERE kind IN ('track','road')")]
    feats["d_hist_track"] = dist(gk, line_pts(tracks, 1.0, lat0))
    water = [gpkg_geom(r[0]) for r in gp.execute("SELECT geom FROM sudan250k_symbols WHERE category='water'")]
    water += [l[0] for l in labels if l[3] == "water_supply"]
    feats["d_hist_water"] = dist(gk, proj([(g.x, g.y) for g in water if g.geom_type == "Point"], lat0))
    # abandoned 1930s village: no settled cell within 3 km of the label
    if sett.any():
        hp_to_sett, _ = cKDTree(gk[sett]).query(hp_k)
        ab = hp_k[hp_to_sett > 3.0]
    else:
        ab = np.zeros((0, 2))
    feats["d_hist_abandoned_village"] = dist(gk, ab)
    feats["d_hist_abandoned_track"] = np.where(feats["d_road_osm"] > 2.0, feats["d_hist_track"], 1e9)
    log(f"  labels {len(labels)}, places {len(hp)}, abandoned {len(ab)}, tracks {len(tracks)}, water {len(water)}, mine notes {len(hm)}")

    log("reach points...")
    reach = list(places)
    ct = json.load(open(ROOT / "data/crisistracker/incidents.json"))
    reach += [[r["longitude"], r["latitude"]] for r in ct["records"] if r.get("longitude") and r.get("latitude")]
    with zipfile.ZipFile(ROOT / "data/ucdp/ged261-csv.zip") as z, z.open("GEDEvent_v26_1.csv") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, "utf8")):
            try:
                if int(row["where_prec"]) <= 3 and row["country_id"] in ("475", "626", "625"):
                    reach.append([float(row["longitude"]), float(row["latitude"])])
            except (ValueError, KeyError): pass
    rk = proj(reach, lat0)
    rk = rk[(rk[:, 0] >= gk[:, 0].min() - 50) & (rk[:, 0] <= gk[:, 0].max() + 50) & (rk[:, 1] >= gk[:, 1].min() - 50) & (rk[:, 1] <= gk[:, 1].max() + 50)]
    bw = 20.0; w = np.zeros(len(gk)); tr = cKDTree(rk)
    for i in range(0, len(gk), 2000):
        nb = tr.query_ball_point(gk[i:i + 2000], r=3 * bw)
        for j, idx in enumerate(nb):
            if idx:
                d2 = ((rk[idx] - gk[i + j]) ** 2).sum(1); w[i + j] = np.exp(-d2 / (2 * bw * bw)).sum()
    feats["reach_w"] = w + 0.01 * w.mean()
    log(f"  reach points {len(rk)}")

    np.savez_compressed(OUT, grid=grid, gk=gk, iso=iso_of, in_aoi=in_aoi, lat0=lat0, **feats)
    log(f"wrote {OUT}")


if __name__ == "__main__":
    main()
