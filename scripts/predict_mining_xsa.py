#!/usr/bin/env python3
"""Where would we look for undiscovered artisanal mining in XSA_Study_Area?

    python3 scripts/predict_mining_xsa.py          # -> data/eval/xsa_mining/prediction.json

THE QUESTION, STATED HONESTLY

We hold 89 REPORTED mine sites inside the study area (mining_anchors: OSM tags,
Crisis Tracker attacked sites, UCDP GED, USGS). Every one is there because
someone could reach or attack it - the list's blind spot is exactly the ground
we want to predict into. Optical detection is retired (chance-level, see
docs/agents/mining.md). What remains is CONTEXT: graded geology contact lines,
rivers (alluvial ground), the 1930s survey's hills/tracks/mine notes, and the
modern settlement / cropland / deforestation fabric.

METHOD - MEASURE EACH SIGNAL BEFORE IT MAY VOTE (invariant 12)

1. Grid the study area (0.05 deg ~ 5.5 km cells).
2. Cluster the 89 anchors at 10 km single-link into SITE CLUSTERS (an incident
   list holds duplicates; invariant 7 - sites, not reports).
3. For each candidate signal, compute per-cell distance, then score:
   capture = fraction of truth clusters within the signal's near-threshold,
   baseline = fraction of the study area within it, lift = ratio,
   p = permutation (truth clusters re-thrown uniformly on the grid, 5000x),
   plus the minimum lift this n could have detected (a null without its power
   is a shrug wearing a result's clothes).
4. Signals that pass (p<0.05, lift>1) vote with equal weight in a composite
   percentile rank. No fitted weights - 60 clusters cannot support fitting,
   and a fitted model here would be laundering the truth set's reach bias.
5. Rank cells >=25 km from every known anchor, merge peaks 25 km apart,
   emit the top candidates with names, context and QGIS filters.

Historic-map signals are only evaluated where the 1930s sheets actually reach
(coverage mask from label density): silence off-sheet is absence of a map,
not of a hill (the report's own caveat).

Truth caveat carried into the output: the anchors are community reports, OSM
tags and attack records - coordinates may be village label positions, and the
list is reachability-biased. A lift here says "reported mines sit on this
ground", not "mines do".
"""
import json
import math
import gzip
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import shape, Point, box
from shapely.strtree import STRtree
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "eval" / "xsa_mining" / "prediction.json"
DB = ROOT / "db.sqlite3"
AOI = "XSA_Study_Area"
CELL = 0.05          # degrees
NEAR_KM = 5.0        # "near" threshold for line/point signals
PERMS = 5000
RNG = np.random.default_rng(20260816)
KM_PER_DEG = 111.32


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def db():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


# ---------------------------------------------------------------- geometry
def load_aoi():
    c = db()
    g = json.loads(c.execute(
        "SELECT geometry FROM aois WHERE id=?", (AOI,)).fetchone()[0])
    return shape(g)


def make_grid(poly):
    minx, miny, maxx, maxy = poly.bounds
    xs = np.arange(minx + CELL / 2, maxx, CELL)
    ys = np.arange(miny + CELL / 2, maxy, CELL)
    XX, YY = np.meshgrid(xs, ys)
    pts = np.column_stack([XX.ravel(), YY.ravel()])
    # containment test via prepared geometry
    from shapely.prepared import prep
    pp = prep(poly)
    keep = np.array([pp.contains(Point(x, y)) for x, y in pts])
    return pts[keep]


def proj(lonlat, lat0):
    """Equirectangular km projection about lat0 - fine at study-area scale."""
    a = np.asarray(lonlat, dtype=float).reshape(-1, 2)
    x = a[:, 0] * KM_PER_DEG * math.cos(math.radians(lat0))
    y = a[:, 1] * KM_PER_DEG
    return np.column_stack([x, y])


def nearest_dist_km(cells_km, geoms_deg, lat0):
    """Distance from each cell (km coords) to nearest of shapely geoms (deg)."""
    from shapely import affinity
    kx = KM_PER_DEG * math.cos(math.radians(lat0))
    ky = KM_PER_DEG
    gk = [affinity.scale(g, xfact=kx, yfact=ky, origin=(0, 0)) for g in geoms_deg]
    tree = STRtree(gk)
    out = np.empty(len(cells_km))
    for i, (x, y) in enumerate(cells_km):
        p = Point(x, y)
        j = tree.nearest(p)
        out[i] = p.distance(gk[j])
        # nearest() returns nearest by geometry; done.
    return out


def nearest_dist_km_points(cells_km, pts_km):
    from scipy.spatial import cKDTree  # noqa
    t = cKDTree(pts_km)
    d, _ = t.query(cells_km)
    return d


# ---------------------------------------------------------------- truth
def load_anchors(poly):
    d = json.load(open(ROOT / "data/geology_truth/mining_anchors.geojson"))
    rows = []
    for f in d["features"]:
        lon, lat = f["geometry"]["coordinates"]
        if poly.contains(Point(lon, lat)):
            p = f["properties"]
            rows.append(dict(lon=lon, lat=lat, source=p.get("source"),
                             resource=p.get("resource")))
    return rows


def cluster_sites(anchors, lat0, link_km=10.0):
    pts = proj([[a["lon"], a["lat"]] for a in anchors], lat0)
    n = len(pts)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if np.hypot(*(pts[i] - pts[j])) <= link_km:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    clusters = []
    for idx in groups.values():
        sub = pts[idx]
        clusters.append(dict(
            km=sub.mean(axis=0),
            n=len(idx),
            sources=sorted({anchors[i]["source"] for i in idx}),
            members=idx))
    return clusters


# ---------------------------------------------------------------- signals
def gold_contact_geoms(poly):
    """Gold-graded (w>=2) contact lines from every served sheet, clipped."""
    cat = json.loads(subprocess.check_output(
        ["curl", "-fsS", "http://localhost:8000/api/geomap?pwd=test2026"]))
    geoms = []
    for s in cat["sheets"]:
        c = s["catalogue"]
        lith = {x["code"]: (x.get("lith") or "mixed") for x in c["classes"]}
        rules = {r["pair"]: r for r in c["std"]["contact_rules"]}
        fn = ROOT / f"data/geomaps/{s['id']}_contacts.geojson"
        if not fn.exists():
            continue
        for f in json.load(open(fn))["features"]:
            pr = f["properties"]
            pair = "|".join(sorted([lith.get(pr["code_a"], "mixed"),
                                    lith.get(pr["code_b"], "mixed")]))
            w = 0
            for a in (rules.get(pair) or {}).get("affinity", []):
                if a["commodity"] == "gold":
                    w = a["weight"]
            if w >= 2:
                g = shape(f["geometry"]).intersection(poly)
                if not g.is_empty:
                    geoms.append(g)
    return geoms


def hist_features(poly):
    """1930s sheet content inside the AOI: labels by class, lines, symbols."""
    gp = sqlite3.connect(ROOT / "data/histmaps/sudan250k_labels.gpkg")
    # gpkg geometry blob: parse header (little-endian, envelope flag) -> WKB
    def gpkg_pt(blob):
        flags = blob[3]
        env = (flags >> 1) & 0x07
        off = 8 + (32 if env == 1 else 48 if env in (2, 3) else 64 if env == 4 else 0)
        from shapely import wkb
        return wkb.loads(bytes(blob[off:]))

    minx, miny, maxx, maxy = poly.bounds
    from shapely.prepared import prep
    pp = prep(poly)

    def pull(sql, args=()):
        out = []
        for row in gp.execute(sql, args):
            g = gpkg_pt(row[0])
            if g.geom_type == "Point":
                if minx <= g.x <= maxx and miny <= g.y <= maxy and pp.contains(g):
                    out.append((g, row[1:]))
            else:
                if g.intersects(poly):
                    out.append((g.intersection(poly), row[1:]))
        return out

    labels_all = pull("SELECT geom, text, category, note_topic FROM sudan250k_labels")
    MIN_WORDS = ("mine", "mines", "workings", "diggings", "goldwash",
                 "gold", "copper", "iron work", "iron mines")
    hist_mines = [(g, t) for g, t in labels_all
                  if t[0] and any(w in t[0].lower() for w in MIN_WORDS)
                  and t[1] in ("place", "note", "terrain")]
    # drop pure hill-name false hits like 'Iron stone' terrain? keep - ironstone
    # ground burned +20% and is a mineral note in its own right.
    hills = [(g, t) for g, t in labels_all if t[1] == "terrain"]
    places = [(g, t) for g, t in labels_all if t[1] == "place"]
    tracks = [g for g, t in pull(
        "SELECT geom, kind FROM sudan250k_lines WHERE kind IN ('track','road')")]
    water = [g for g, _ in pull(
        "SELECT geom, category FROM sudan250k_symbols WHERE category='water'")]
    water += [g for g, _ in pull(
        "SELECT geom, text FROM sudan250k_labels WHERE note_topic='water_supply'")]
    return dict(labels_all=labels_all, hist_mines=hist_mines,
                hills=hills, places=places, tracks=tracks, water=water)


def modern_context(poly):
    c = db()
    setts = c.execute(
        "SELECT lat, lon, area_m2, surface_e2015_m2, cropland_frac_2019, "
        "cropland_frac_2003, nearest_place, classification, "
        "population_est, population_source, persistence "
        "FROM park_settlements WHERE park_id=? AND polygon_ids<>''",
        (AOI,)).fetchall()
    defor = c.execute(
        "SELECT lat, lon, area_km2, year, classification, "
        "cropland_conversion_frac FROM deforestation_events "
        "WHERE park_id=? AND needs_review=0", (AOI,)).fetchall()
    rivers = c.execute(
        "SELECT lat, lon FROM park_rivers_hydro "
        "WHERE park_id=? AND stream_order>=3", (AOI,)).fetchall()
    roads = [json.loads(r[0]) for r in c.execute(
        "SELECT geojson FROM roads_heigit WHERE park_id=? "
        "AND geojson IS NOT NULL", (AOI,))]
    return setts, defor, rivers, roads


# ---------------------------------------------------------------- scoring
def score_signal(name, cell_d, truth_d, area_mask_frac, thr=NEAR_KM,
                 coverage=None, subset=None, perms=PERMS, weights=None):
    """capture/baseline/lift + permutation p on the grid.

    coverage: boolean per-cell mask limiting where the signal is defined
    (historic sheets). Truth clusters outside coverage are excluded from
    capture AND the baseline is computed inside coverage only - capture and
    baseline must answer the same question.
    """
    cells_ok = coverage if coverage is not None else np.ones(len(cell_d), bool)
    base = float(np.mean(cell_d[cells_ok] <= thr))
    t_ok = (np.ones(len(truth_d["d"]), bool) if coverage is None
            else truth_d["cov"])
    if subset is not None:
        t_ok = t_ok & subset
    td = truth_d["d"][t_ok]
    n = int(t_ok.sum())
    if n < 8:
        return dict(signal=name, verdict="too_few", n=n)
    cap = float(np.mean(td <= thr))
    lift = cap / base if base > 0 else None
    # permutation: rethrow n cluster centres uniformly over covered cells
    pool = cell_d[cells_ok]
    sims = np.mean(
        pool[RNG.integers(0, len(pool), size=(perms, n))] <= thr, axis=1)
    p = float(np.mean(sims >= cap)) if lift and lift >= 1 else float(np.mean(sims <= cap))
    # power: smallest lift detectable at alpha=.05 with this n and base
    crit = np.quantile(sims, 0.95)
    min_lift = float(crit / base) if base > 0 else None
    rec = dict(signal=name, n=n, threshold_km=thr, capture=round(cap, 3),
               baseline=round(base, 3),
               lift=round(lift, 2) if lift is not None else None,
               p=round(p, 4), min_detectable_lift=round(min_lift, 2)
               if min_lift else None,
               verdict="signal" if (p < 0.05 and lift and lift > 1)
               else "null")
    if weights is not None:
        # reach-corrected null (target-group background): rethrow the
        # pseudo-anchors with probability proportional to reporting reach.
        # Beating THIS null means the signal concentrates reported mines
        # beyond what mere reportability of the ground explains.
        wp = weights[cells_ok]
        wp = wp / wp.sum()
        base_w = float(np.sum(wp * (pool <= thr)))
        idx = RNG.choice(len(pool), size=(perms, n), p=wp)
        sims_w = np.mean(pool[idx] <= thr, axis=1)
        lift_w = cap / base_w if base_w > 0 else None
        p_w = (float(np.mean(sims_w >= cap)) if lift_w and lift_w >= 1
               else float(np.mean(sims_w <= cap)))
        rec["baseline_reach"] = round(base_w, 3)
        rec["lift_reach"] = round(lift_w, 2) if lift_w is not None else None
        rec["p_reach"] = round(p_w, 4)
    return rec


def reach_weights(gk, poly, lat0):
    """Target-group background (Phillips et al. 2009): the truth set is not
    a random sample of mines but a sample of REPORTS, so the honest null
    rethrows pseudo-anchors where the reporting process demonstrably
    reaches, not uniformly. Reach is estimated from every record the same
    observers produced regardless of topic: all Crisis Tracker incidents
    (overwhelmingly LRA/violence, not mining), all UCDP GED events with
    location precision <=3, and every OSM place node. A Gaussian KDE
    (bw 20 km) over those points, plus a 1%-of-mean floor so no cell is
    strictly unreachable, gives the per-cell sampling weight."""
    pts = []
    ct = json.load(open(ROOT / "data/crisistracker/incidents.json"))
    for r in ct["records"]:
        lo, la = r.get("longitude"), r.get("latitude")
        if lo and la and poly.contains(Point(lo, la)):
            pts.append([lo, la])
    n_ct = len(pts)
    import zipfile, csv, io
    minx, miny, maxx, maxy = poly.bounds
    with zipfile.ZipFile(ROOT / "data/ucdp/ged261-csv.zip") as z:
        with z.open("GEDEvent_v26_1.csv") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, "utf8")):
                try:
                    lo, la = float(row["longitude"]), float(row["latitude"])
                    if int(row["where_prec"]) > 3:
                        continue
                except (ValueError, KeyError):
                    continue
                if minx <= lo <= maxx and miny <= la <= maxy and \
                        poly.contains(Point(lo, la)):
                    pts.append([lo, la])
    n_ucdp = len(pts) - n_ct
    c = db()
    for la, lo in c.execute(
            "SELECT lat, lon FROM osm_places WHERE park_id=?", (AOI,)):
        pts.append([lo, la])
    n_osm = len(pts) - n_ct - n_ucdp
    pk = proj(pts, lat0)
    bw = 20.0
    w = np.zeros(len(gk))
    for i in range(0, len(gk), 500):
        d2 = ((gk[i:i + 500, None, :] - pk[None, :, :]) ** 2).sum(-1)
        w[i:i + 500] = np.exp(-d2 / (2 * bw * bw)).sum(1)
    w = w + 0.01 * w.mean()
    meta = dict(crisistracker=n_ct, ucdp_ged=n_ucdp, osm_places=n_osm,
                bandwidth_km=bw, floor="1% of mean density")
    return w, meta



def main():
    log("loading AOI + grid...")
    poly = load_aoi()
    lat0 = poly.centroid.y
    grid = make_grid(poly)          # lon/lat
    gk = proj(grid, lat0)           # km
    log(f"{len(grid)} cells")

    anchors = load_anchors(poly)
    log("reach weights (target-group background)...")
    reach_w, reach_meta = reach_weights(gk, poly, lat0)
    log(f"  reach sources: {reach_meta}")
    clusters = cluster_sites(anchors, lat0)
    ck = np.array([c["km"] for c in clusters])
    log(f"{len(anchors)} anchors -> {len(clusters)} site clusters")

    # ---- signals -> per-cell distances
    log("gold contacts...")
    gold = gold_contact_geoms(poly)
    d_gold = nearest_dist_km(gk, gold, lat0)

    log("historic sheets...")
    hist = hist_features(poly)
    lab_pts = proj([[g.x, g.y] for g, _ in hist["labels_all"]], lat0)
    d_anylabel = nearest_dist_km_points(gk, lab_pts)
    coverage = d_anylabel <= 15.0   # a sheet that reached here named something
    hill_pts = proj([[g.x, g.y] for g, _ in hist["hills"]], lat0)
    d_hill = nearest_dist_km_points(gk, hill_pts)
    hm_pts = proj([[g.x, g.y] for g, _ in hist["hist_mines"]], lat0)
    d_hmine = (nearest_dist_km_points(gk, hm_pts)
               if len(hm_pts) else np.full(len(gk), 1e9))
    hp_pts = proj([[g.x, g.y] for g, _ in hist["places"]], lat0)
    d_hplace = (nearest_dist_km_points(gk, hp_pts)
                if len(hp_pts) else np.full(len(gk), 1e9))
    d_track = nearest_dist_km(gk, hist["tracks"], lat0) \
        if hist["tracks"] else np.full(len(gk), 1e9)

    log("modern context...")
    setts, defor, rivers, roads = modern_context(poly)
    sett_pts = proj([[s[1], s[0]] for s in setts], lat0)
    d_sett = nearest_dist_km_points(gk, sett_pts)
    riv_pts = proj([[r[1], r[0]] for r in rivers], lat0)
    d_riv = nearest_dist_km_points(gk, riv_pts)
    def_pts = proj([[d[1], d[0]] for d in defor], lat0) if defor else np.zeros((0, 2))
    d_def = (nearest_dist_km_points(gk, def_pts)
             if len(def_pts) else np.full(len(gk), 1e9))
    # settlement GROWTH points: clusters that grew >33% since 2015
    grow = [s for s in setts if (s[3] or 0) > 0 and s[2] and s[2] > 1.333 * s[3]]
    gr_pts = proj([[s[1], s[0]] for s in grow], lat0) if grow else np.zeros((0, 2))
    d_grow = (nearest_dist_km_points(gk, gr_pts)
              if len(gr_pts) else np.full(len(gk), 1e9))
    # cropland-poor settlements (subsistence absent - the "boom without farms"
    # signature the report ties to non-farm income)
    croppoor = [s for s in setts if s[4] is not None and s[4] < 0.02]
    cp_pts = proj([[s[1], s[0]] for s in croppoor], lat0) if croppoor else np.zeros((0, 2))
    d_croppoor = (nearest_dist_km_points(gk, cp_pts)
                  if len(cp_pts) else np.full(len(gk), 1e9))
    # persistence (migration 057, GHSL E2000/E2015): a RECENT settlement is
    # one with <25% of today's built surface in both back-epochs - the
    # boom-town signature. Sharpened by no-farmland: appeared after 2015
    # AND nobody farms there.
    recent = [s for s in setts if s[10] == "recent"]
    rc_pts = (proj([[s[1], s[0]] for s in recent], lat0)
              if recent else np.zeros((0, 2)))
    d_recent = (nearest_dist_km_points(gk, rc_pts)
                if len(rc_pts) else np.full(len(gk), 1e9))
    recent_nofarm = [s for s in recent if s[4] is not None and s[4] < 0.02]
    rn_pts = (proj([[s[1], s[0]] for s in recent_nofarm], lat0)
              if recent_nofarm else np.zeros((0, 2)))
    d_recent_nofarm = (nearest_dist_km_points(gk, rn_pts)
                       if len(rn_pts) else np.full(len(gk), 1e9))
    log(f"  persistence: {len(recent)} recent settlements, "
        f"{len(recent_nofarm)} of those cropland-poor")
    # cropland conversion (migration 059): forest loss NOT attributable to
    # cropland expansion (<10% converted) - the residue after farming is
    # accounted for, which is where digging would hide.
    def_noconv = [d for d in defor if d[5] is not None and d[5] < 0.10]
    dn_pts = (proj([[d[1], d[0]] for d in def_noconv], lat0)
              if def_noconv else np.zeros((0, 2)))
    d_def_noconv = (nearest_dist_km_points(gk, dn_pts)
                    if len(dn_pts) else np.full(len(gk), 1e9))
    log(f"  conversion: {len(def_noconv)}/{len(defor)} verified losses "
        f"not attributable to cropland")

    # population: GHSL-measured or absent, never a density constant
    # (invariant 15). The labour-shed hypothesis: mining crews come FROM
    # somewhere - a village big enough to spare working-age men. Two forms:
    # a plain labour pool (pop >= 500), and the sharp conjunction the
    # cropland-poor signal hints at - hundreds of people with no fields
    # to feed them (pop >= 500 AND cropland < 2%).
    pops = [s[8] for s in setts if s[8] is not None and s[9]]
    pop_ok = [s for s in setts if s[8] is not None and s[9] and s[8] >= 500]
    lp_pts = (proj([[s[1], s[0]] for s in pop_ok], lat0)
              if pop_ok else np.zeros((0, 2)))
    d_labour = (nearest_dist_km_points(gk, lp_pts)
                if len(lp_pts) else np.full(len(gk), 1e9))
    pop_nofarm = [s for s in pop_ok if s[4] is not None and s[4] < 0.02]
    pn_pts = (proj([[s[1], s[0]] for s in pop_nofarm], lat0)
              if pop_nofarm else np.zeros((0, 2)))
    d_pop_nofarm = (nearest_dist_km_points(gk, pn_pts)
                    if len(pn_pts) else np.full(len(gk), 1e9))
    log(f"  population: {len(pops)}/{len(setts)} settlements have a GHSL "
        f"estimate; {len(pop_ok)} with pop>=500, {len(pop_nofarm)} of those "
        f"cropland-poor")

    # "abandoned" 1930s villages: named on the survey sheets, no modern
    # settlement cluster within 3 km today. The report's finding three says
    # returnees land ON named villages - the ones nobody has returned to are
    # the residue, and the hypothesis worth testing is that a village that
    # died while its neighbours came back died for a reason, and on graded
    # rock that reason may be that it was a workings camp.
    from scipy.spatial import cKDTree as _KD
    if len(hp_pts) and len(sett_pts):
        hp_to_sett, _ = _KD(sett_pts).query(hp_pts)
        aband_idx = np.nonzero(hp_to_sett > 3.0)[0]
    else:
        aband_idx = np.array([], dtype=int)
    ab_pts = hp_pts[aband_idx] if len(aband_idx) else np.zeros((0, 2))
    ab_names = [hist["places"][int(j)][1][0] for j in aband_idx]
    ab_lonlat = [(hist["places"][int(j)][0].x, hist["places"][int(j)][0].y)
                 for j in aband_idx]
    d_aband = (nearest_dist_km_points(gk, ab_pts)
               if len(ab_pts) else np.full(len(gk), 1e9))
    # the conjunction the user asked about: abandoned village AND graded
    # contact - distance = max of the two, so "near" means near BOTH
    d_aband_gold = np.maximum(d_aband, d_gold)
    log(f"  1930s places: {len(hp_pts)}, abandoned (no modern settlement "
        f"within 3 km): {len(ab_pts)}")

    # "abandoned" 1930s tracks: same construction one signal over. A cell is
    # near an abandoned track if it is within the threshold of a surveyed
    # track/road AND more than 2 km from every modern road (HeiGIT OSM) -
    # the route the surveyors drew that nobody maintains today. Access
    # without observation: exactly where a working could operate unreported.
    d_road_modern = (nearest_dist_km(
        gk, [shape(g) for g in roads], lat0)
        if roads else np.full(len(gk), 1e9))
    d_aband_track = np.where(d_road_modern > 2.0, d_track, 1e9)
    d_aband_track_gold = np.maximum(d_aband_track, d_gold)
    # pooled "abandonment on gold" - the Hofrat el Nahas pattern stated in
    # report section 4 BEFORE testing: 1930s infrastructure (village OR
    # track) that nobody uses today, on gold-graded rock. The village and
    # track conjunctions are this one hypothesis split in two; splitting
    # halves the evidence and doubles the BH burden. The split versions
    # stay in the table for transparency.
    d_aband_any_gold = np.minimum(d_aband_gold, d_aband_track_gold)

    # old water points: the surveyors' wells, waterholes and dated
    # water-supply notes. Dry-season digging needs dry-season water; a
    # century-old watering point on graded rock is the Bir Andem pattern
    # (two modern OSM gold mines 1 km from a 1930s well - report section 4).
    hw_pts = (proj([[g.x, g.y] for g in hist["water"]], lat0)
              if hist["water"] else np.zeros((0, 2)))
    d_hwater = (nearest_dist_km_points(gk, hw_pts)
                if len(hw_pts) else np.full(len(gk), 1e9))
    d_hwater_gold = np.maximum(d_hwater, d_gold)
    log(f"  1930s tracks: {len(hist['tracks'])}, modern roads: {len(roads)}, "
        f"1930s water points: {len(hw_pts)}")

    # ---- truth distances (cluster centres against same signals)
    def truth_d(cell_d):
        # nearest grid cell to each cluster centre carries its value
        from scipy.spatial import cKDTree
        t = cKDTree(gk)
        _, idx = t.query(ck)
        return dict(d=cell_d[idx], cov=coverage[idx])

    signals = {}
    per_cell = {}
    # factor: the PCA (scripts/pca_mining_signals.py, data/eval/xsa_mining/
    # pca.png) shows the signal space is ~4 factors, not 21 signals: every
    # settlement variant loads ~0.4 on one axis (they are one measurement
    # taken seven ways), both deforestation variants load together on a
    # second, gold contacts + rivers sit on a third orthogonal axis, and
    # the 1930s signals form their own. An equal-weight vote over PASSING
    # SIGNALS would let one factor vote as many times as it has aliases -
    # so passing signals vote WITHIN their factor first (mean rank), then
    # factors vote equally.
    signal_defs = [
        ("gold_contact_w2", d_gold, None, NEAR_KM, "geology"),
        ("river_order3", d_riv, None, NEAR_KM, "hydro"),
        ("settlement", d_sett, None, NEAR_KM, "settlement_fabric"),
        ("settlement_grew_33pct", d_grow, None, NEAR_KM, "settlement_fabric"),
        ("settlement_cropland_poor", d_croppoor, None, NEAR_KM,
         "settlement_fabric"),
        ("settlement_pop500_labour_pool", d_labour, None, NEAR_KM,
         "settlement_fabric"),
        ("settlement_pop500_no_farmland", d_pop_nofarm, None, NEAR_KM,
         "settlement_fabric"),
        ("settlement_recent_ghsl", d_recent, None, NEAR_KM,
         "settlement_fabric"),
        ("settlement_recent_no_farmland", d_recent_nofarm, None, NEAR_KM,
         "settlement_fabric"),
        ("deforestation_verified", d_def, None, NEAR_KM, "deforestation"),
        ("deforestation_not_cropland", d_def_noconv, None, NEAR_KM,
         "deforestation"),
        ("hist_track_1930s", d_track, coverage, 3.0, "historic"),
        ("hist_settlement_1930s", d_hplace, coverage, NEAR_KM, "historic"),
        ("hist_abandoned_village_1930s", d_aband, coverage, NEAR_KM,
         "historic"),
        ("hist_abandoned_village_on_gold_contact", d_aband_gold, coverage,
         NEAR_KM, "historic"),
        ("hist_abandoned_track_1930s", d_aband_track, coverage, 3.0,
         "historic"),
        ("hist_abandoned_track_on_gold_contact", d_aband_track_gold,
         coverage, NEAR_KM, "historic"),
        ("hist_abandoned_any_on_gold_contact", d_aband_any_gold,
         coverage, NEAR_KM, "historic"),
        ("hist_water_point_1930s", d_hwater, coverage, NEAR_KM, "historic"),
        ("hist_water_point_on_gold_contact", d_hwater_gold, coverage,
         NEAR_KM, "historic"),
        ("hist_hill_terrain_1930s", d_hill, coverage, NEAR_KM, "historic"),
        ("hist_mine_note_1930s", d_hmine, coverage, 10.0, "historic"),
    ]
    factor_of = {name: fac for name, _, _, _, fac in signal_defs}
    for name, cd, cov, thr, _fac in signal_defs:
        rec = score_signal(name, cd, truth_d(cd), None, thr=thr, coverage=cov,
                           weights=reach_w)
        rec["factor"] = _fac
        signals[name] = rec
        per_cell[name] = cd
        log(f"  {name}: {rec}")

    # ---- multiple testing: len(signal_defs) signals were tried, so a raw
    # p<0.05 is not enough (with 10 nulls, at least one clears 0.05 by luck
    # ~40% of the time).
    # Benjamini-Hochberg FDR across every measured signal. Two nulls exist:
    # uniform (any ground) and reach-weighted (ground the reporting process
    # can see - target-group background). The reach null is the correctly
    # specified one for a truth set made of REPORTS, so the vote is decided
    # on it: a signal votes iff q_reach < 0.05 AND lift_reach > 1. Uniform
    # stats stay in the table - the comparison of the two columns IS the
    # measurement of reporting bias per signal.
    measured = [k for k, v in signals.items() if "p" in v]
    def bh(pvals):
        order_p = np.argsort(pvals)
        m = len(pvals)
        qs = np.empty(m)
        prev = 1.0
        for rank_i in range(m - 1, -1, -1):
            idx = order_p[rank_i]
            prev = min(prev, pvals[idx] * m / (rank_i + 1))
            qs[idx] = prev
        return qs
    qs_u = bh(np.array([signals[k]["p"] for k in measured]))
    qs_r = bh(np.array([signals[k]["p_reach"] for k in measured]))
    for k, qu, qr in zip(measured, qs_u, qs_r):
        signals[k]["q_bh"] = round(float(qu), 4)
        signals[k]["q_bh_reach"] = round(float(qr), 4)
        signals[k]["verdict"] = (
            "signal" if (qr < 0.05 and (signals[k].get("lift_reach") or 0) > 1)
            else "null")

    passing = [k for k, v in signals.items() if v.get("verdict") == "signal"]
    log("passing:", passing)

    def rank_of(k):
        cd = per_cell[k]
        r = 1.0 - (np.argsort(np.argsort(cd)) / (len(cd) - 1))  # near = high
        if k.startswith("hist"):
            r = np.where(coverage, r, np.nan)  # off-sheet: signal unknown
        return r

    def build_composite(sig_names):
        """Factor-grouped composite: mean rank within factor, then mean
        across factors. One factor cannot vote once per alias."""
        by_fac = {}
        for k in sig_names:
            by_fac.setdefault(factor_of[k], []).append(rank_of(k))
        fac_ranks = [np.nanmean(np.vstack(v), axis=0) for v in by_fac.values()]
        return np.nanmean(np.vstack(fac_ranks), axis=0), sorted(by_fac)

    if not passing:
        result_note = ("No signal passed - composite not built. "
                       "An empty candidate list is the honest output.")
        comp = None
        factors_voting = []
    else:
        comp, factors_voting = build_composite(passing)
        log("factors voting:", factors_voting)
        result_note = None

    # composite skill, measured the same way (top-X% of cells), plus a
    # per-source robustness split: OSM-tagged truth vs attack-record truth
    # (Crisis Tracker / UCDP). If the composite only captures one list's
    # clusters, it has learned that list's reporting reach, not mining.
    comp_skill = None
    robustness = None
    if comp is not None:
        from scipy.spatial import cKDTree
        t = cKDTree(gk)
        _, tidx = t.query(ck)
        for topq in (0.05, 0.10, 0.20, 0.35):
            thr_v = np.nanquantile(comp, 1 - topq)
            sel = comp >= thr_v
            cap = float(np.mean(sel[tidx]))
            base = float(np.mean(sel[~np.isnan(comp)]))
            pool = sel[~np.isnan(comp)]
            sims = np.mean(pool[RNG.integers(0, len(pool),
                                             size=(PERMS, len(ck)))], axis=1)
            p = float(np.mean(sims >= cap))
            crit = float(np.quantile(sims, 0.95))
            rec = dict(top_frac=topq, capture=round(cap, 3),
                       baseline=round(base, 3),
                       lift=round(cap / base, 2) if base else None,
                       p=round(p, 4),
                       min_detectable_lift=round(crit / base, 2) if base else None)
            # reach-corrected null for the same cut
            ok = ~np.isnan(comp)
            wp = reach_w[ok] / reach_w[ok].sum()
            base_w = float(np.sum(wp * pool))
            idxw = RNG.choice(len(pool), size=(PERMS, len(ck)), p=wp)
            sims_w = np.mean(pool[idxw], axis=1)
            rec["baseline_reach"] = round(base_w, 3)
            rec["lift_reach"] = round(cap / base_w, 2) if base_w else None
            rec["p_reach"] = round(float(np.mean(sims_w >= cap)), 4)
            comp_skill = comp_skill or []
            comp_skill.append(rec)
            log("  composite", rec)
        # robustness at top-20%
        thr_v = np.nanquantile(comp, 0.8)
        sel = comp >= thr_v
        robustness = {}
        for label, pred in [
                ("osm_only", lambda s: s == ["osm"]),
                ("attack_records",
                 lambda s: any(x in s for x in ("crisistracker", "ucdp_ged")))]:
            sub = np.array([pred(c["sources"]) for c in clusters])
            n = int(sub.sum())
            if n < 8:
                robustness[label] = dict(n=n, verdict="too_few")
                continue
            cap = float(np.mean(sel[tidx][sub]))
            robustness[label] = dict(n=n, capture_top20=round(cap, 3),
                                     baseline=0.2,
                                     lift=round(cap / 0.2, 2))
            log(f"  robustness {label}: {robustness[label]}")

    # ---- spatial block cross-validation.
    # The composite has no fitted weights, but SIGNAL SELECTION is fitted -
    # picking the voters on the same 43 clusters they are then scored on is
    # in-sample. And random CV leaks here: neighbouring clusters share their
    # ground (spatial autocorrelation), so folds must be spatial blocks
    # (Roberts et al. 2017; Ploton et al. 2020). Four longitude blocks with
    # roughly equal cluster counts; per fold, re-run selection (raw p<0.05,
    # lift>1 - BH across 4 folds x all signals would be too conservative for a
    # selection step) on the TRAINING blocks only, rebuild the composite,
    # and ask what fraction of HELD-OUT clusters land in its top 20%.
    cv = None
    if comp is not None:
        lons = np.array([grid[int(np.argmin(np.hypot(*(gk - c["km"]).T)))][0]
                         for c in clusters])
        edges = np.quantile(lons, [0.25, 0.5, 0.75])
        block = np.digitize(lons, edges)
        folds = []
        hits = 0
        tot = 0
        for b in range(4):
            train = block != b
            test = block == b
            sel_sigs = []
            for name, cd, cov, thr, _fac in signal_defs:
                rec = score_signal(name, cd, truth_d(cd), None, thr=thr,
                                   coverage=cov, subset=train, perms=1000,
                                   weights=reach_w)
                if (rec.get("p_reach", 1) < 0.05
                        and (rec.get("lift_reach") or 0) > 1):
                    sel_sigs.append(name)
            if not sel_sigs:
                folds.append(dict(block=b, n_test=int(test.sum()),
                                  lon_range=[round(float(lons[test].min()), 2),
                                             round(float(lons[test].max()), 2)],
                                  signals=[], captured=None,
                                  note="no signal selected on training blocks"))
                continue
            fc, _ = build_composite(sel_sigs)
            thr_v = np.nanquantile(fc, 0.8)
            from scipy.spatial import cKDTree
            _, ti = cKDTree(gk).query(ck[test])
            cap_i = int(np.sum(fc[ti] >= thr_v))
            hits += cap_i
            tot += int(test.sum())
            folds.append(dict(block=b, n_test=int(test.sum()),
                              lon_range=[round(float(lons[test].min()), 2),
                                         round(float(lons[test].max()), 2)],
                              signals=sel_sigs,
                              captured=cap_i))
        if tot:
            # pooled out-of-sample capture vs the 20% baseline: exact
            # binomial tail (clusters are the unit; blocks make them
            # closer to independent than raw sites are)
            from math import comb
            pk = sum(comb(tot, k) * 0.2**k * 0.8**(tot - k)
                     for k in range(hits, tot + 1))
            east = folds[-1]
            cv = dict(method=("4 spatial blocks by longitude; signal "
                              "selection redone per fold on training "
                              "blocks; capture of held-out clusters in "
                              "the fold composite's top 20%"),
                      folds=folds, pooled_captured=hits, pooled_n=tot,
                      pooled_capture=round(hits / tot, 3), baseline=0.2,
                      lift=round(hits / tot / 0.2, 2),
                      p_binomial=round(float(pk), 4),
                      extrapolation_proxy=dict(
                          note=("The easternmost block is the best available "
                                "proxy for extrapolation: its clusters were "
                                "predicted from signals selected on ground "
                                "entirely to the west of them."),
                          lon_range=east["lon_range"], n=east["n_test"],
                          captured=east["captured"],
                          capture=round((east["captured"] or 0) /
                                        east["n_test"], 3),
                          lift=round((east["captured"] or 0) /
                                     east["n_test"] / 0.2, 2)))
            # where does validation end? truth reaches only part of the AOI
            truth_max_lon = float(lons.max())
            beyond = float(np.mean(grid[:, 0] > truth_max_lon))
            cv["validation_limit"] = dict(
                truth_lon_range=[round(float(lons.min()), 2),
                                 round(truth_max_lon, 2)],
                aoi_share_beyond_truth=round(beyond, 3),
                note=("No reported mine site exists east of "
                      f"{truth_max_lon:.1f}E, so {beyond:.0%} of the study "
                      "area is ground the model can only extrapolate into. "
                      "Candidates there are unvalidatable until someone "
                      "reports a site - treat them as a lower tier."))
            log("  spatial CV:", {k: cv[k] for k in
                ("pooled_capture", "lift", "p_binomial")})
            log("  extrapolation proxy:", cv["extrapolation_proxy"])
            log("  validation limit:", cv["validation_limit"]["note"])

    # ---- candidates: high composite, >=25 km from every known anchor
    candidates = []
    if comp is not None:
        truth_max_lon = max(
            grid[int(np.argmin(np.hypot(*(gk - c["km"]).T)))][0]
            for c in clusters)
        anc_pts = proj([[a["lon"], a["lat"]] for a in anchors], lat0)
        d_anchor = nearest_dist_km_points(gk, anc_pts)
        order = np.argsort(np.where(np.isnan(comp), -1, comp))[::-1]
        chosen = []
        for i in order:
            if np.isnan(comp[i]) or d_anchor[i] < 25:
                continue
            if any(np.hypot(*(gk[i] - gk[j])) < 25 for j in chosen):
                continue
            chosen.append(i)
            if len(chosen) >= 12:
                break
        # context per candidate
        c = db()
        for i in chosen:
            lon, lat = grid[i]
            near_sett = c.execute(
                "SELECT nearest_place, lat, lon, area_m2, surface_e2015_m2 "
                "FROM park_settlements WHERE park_id=? AND polygon_ids<>'' "
                "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
                "ORDER BY (lat-?)*(lat-?)+(lon-?)*(lon-?) LIMIT 1",
                (AOI, lat - 0.5, lat + 0.5, lon - 0.5, lon + 0.5,
                 lat, lat, lon, lon)).fetchone()
            # nearest historic label (any name)
            hl = None
            if coverage[i] and len(lab_pts):
                dd = np.hypot(*(lab_pts - gk[i]).T)
                j = int(np.argmin(dd))
                g, t = hist["labels_all"][j]
                if t[1] in ("place", "terrain", "water", "note"):
                    hl = dict(text=t[0], category=t[1],
                              dist_km=round(float(dd[j]), 1),
                              lat=round(g.y, 4), lon=round(g.x, 4))
            candidates.append(dict(
                lat=round(float(lat), 4), lon=round(float(lon), 4),
                tier=("near_known_mining_ground" if d_anchor[i] < 100
                      else "beyond_validated_ground" if lon > truth_max_lon
                      else "extrapolated"),
                composite_pctile=round(float(np.nanmean(comp[i] >= comp[~np.isnan(comp)])) * 100, 1),
                km_to_known_anchor=round(float(d_anchor[i]), 1),
                gold_contact_km=round(float(d_gold[i]), 1),
                river_km=round(float(d_riv[i]), 1),
                settlement_km=round(float(d_sett[i]), 1),
                deforestation_km=round(float(d_def[i]), 1),
                cropland_poor_settlement_km=round(float(d_croppoor[i]), 1),
                hist_covered=bool(coverage[i]),
                hist_track_km=round(float(d_track[i]), 1) if coverage[i] else None,
                hist_settlement_km=round(float(d_hplace[i]), 1) if coverage[i] else None,
                hist_abandoned_village_km=round(float(d_aband[i]), 1) if coverage[i] else None,
                hist_abandoned_track_km=round(float(d_aband_track[i]), 1)
                if coverage[i] and d_aband_track[i] < 1e8 else None,
                hist_water_point_km=round(float(d_hwater[i]), 1) if coverage[i] else None,
                hist_hill_km=round(float(d_hill[i]), 1) if coverage[i] else None,
                hist_mine_note_km=round(float(d_hmine[i]), 1) if coverage[i] else None,
                nearest_settlement=dict(
                    place=near_sett[0], lat=near_sett[1], lon=near_sett[2])
                if near_sett else None,
                nearest_hist_label=hl,
            ))

    # ---- watchlist: the abandoned-village x gold-contact conjunction.
    # Raw p~0.04 but q>0.05 after BH across the tried signals, and its power
    # ceiling on n=19 covered clusters is ~2.2 - so it does not VOTE in the
    # composite. It is still the user's exact hypothesis, so name the places
    # instead of silently dropping it: every abandoned 1930s village within
    # NEAR_KM of a graded gold contact, for imagery follow-up.
    watchlist = []
    watch_total = 0
    if len(ab_pts) and gold:
        dg = nearest_dist_km(ab_pts, gold, lat0)
        idx = np.nonzero(dg <= NEAR_KM)[0]
        watch_total = len(idx)
        # 673 raw hits is a census, not a watchlist. Dedupe at 10 km
        # (villages share their ground) and RANK BY THE COMPOSITE SCORE OF
        # THE GROUND EACH VILLAGE STANDS ON, so the ranked list agrees with
        # the gridded map by construction (ties: closer contact first).
        # Note: under the reach null the abandoned-on-gold signal votes in
        # the composite (1/4 of one factor of four), so the ranking is not
        # fully independent of the hypothesis - it is the model's OWN
        # ordering of the hypothesis's members, which is what a watchlist is.
        sub_pts = ab_pts[idx]
        driv = nearest_dist_km_points(sub_pts, riv_pts)
        if comp is not None:
            from scipy.spatial import cKDTree
            _t = cKDTree(gk)
            _, ci = _t.query(sub_pts)
            vscore = np.where(np.isnan(comp[ci]), -1.0, comp[ci])
        else:
            vscore = np.zeros(len(sub_pts))
        order = np.lexsort((dg[idx], -vscore))
        taken = []
        for oi in order:
            p = sub_pts[oi]
            if any(np.hypot(*(p - q)) < 10.0 for q in taken):
                continue
            taken.append(p)
            j = int(idx[oi])
            lon_, lat_ = ab_lonlat[j]
            pctl = (round(float(np.mean(vscore[oi] >=
                                        comp[~np.isnan(comp)])) * 100, 1)
                    if comp is not None and vscore[oi] >= 0 else None)
            watchlist.append(dict(
                name=ab_names[j], lat=round(lat_, 4), lon=round(lon_, 4),
                rank=len(watchlist) + 1,
                composite_pctile=pctl,
                gold_contact_km=round(float(dg[j]), 1),
                river_km=round(float(driv[oi]), 1)))
            if len(watchlist) >= 25:
                break
        log(f"  watchlist: {watch_total} abandoned 1930s villages on "
            f"gold-graded contacts; reporting top {len(watchlist)} "
            f"after 10 km dedupe")

    # ---- graduated tiers + feature export.
    # The composite is a rank surface; instead of one top-20% cut, grade it
    # into nested bands whose skill is MEASURED above (comp_skill: capture/
    # lift/p at top 5/10/20/35%). Every settlement, 1930s place label and
    # OSM place standing on banded ground is exported with its band, so the
    # GPKG can show a graduated map of named things rather than 12 stars.
    # Anchors ship too (their own folder) - they are the truth, not a
    # prediction, and the map is unreadable without them.
    graduated = None
    if comp is not None:
        from scipy.spatial import cKDTree
        cell_tree = cKDTree(gk)
        fracs = [0.05, 0.10, 0.20, 0.35]
        qs_v = {f: float(np.nanquantile(comp, 1 - f)) for f in fracs}
        skill_by_frac = {rec["top_frac"]: rec for rec in (comp_skill or [])}

        def tier_of_cell(i):
            v = comp[i]
            if np.isnan(v):
                return None
            for f in fracs:
                if v >= qs_v[f]:
                    return f
            return None

        def tier_of_point(lon, lat):
            d, i = cell_tree.query(proj([[lon, lat]], lat0)[0])
            if d > 4.0:   # farther than a cell radius: off-grid
                return None, None, None
            t = tier_of_cell(int(i))
            pct = round(float(np.mean(comp[int(i)] >=
                                      comp[~np.isnan(comp)])) * 100, 1)
            return t, float(comp[int(i)]), pct

        def tier_name(f):
            return f"top{int(f*100):02d}"

        gfeats = dict(settlements=[], hist_places=[], osm_places=[])
        for s in setts:
            t, sc, pct = tier_of_point(s[1], s[0])
            if t is None:
                continue
            gfeats["settlements"].append(dict(
                lat=s[0], lon=s[1], tier=tier_name(t),
                score=round(sc, 4), pctile=pct,
                name=s[6], classification=s[7],
                persistence=s[10],
                population_est=s[8] if s[9] else None,
                cropland_frac_2019=s[4],
                area_m2=s[2]))
        for g, t_ in hist["places"]:
            t, sc, pct = tier_of_point(g.x, g.y)
            if t is None:
                continue
            gfeats["hist_places"].append(dict(
                lat=round(g.y, 5), lon=round(g.x, 5), tier=tier_name(t),
                score=round(sc, 4), pctile=pct, name=t_[0]))
        c = db()
        for name_, ptype, lat_, lon_ in c.execute(
                "SELECT name, place_type, lat, lon FROM osm_places "
                "WHERE park_id=? AND place_type IN "
                "('city','town','village','hamlet')", (AOI,)):
            t, sc, pct = tier_of_point(lon_, lat_)
            if t is None:
                continue
            gfeats["osm_places"].append(dict(
                lat=lat_, lon=lon_, tier=tier_name(t),
                score=round(sc, 4), pctile=pct,
                name=name_, place_type=ptype))
        tier_stats = {}
        for f in fracs:
            rec = skill_by_frac.get(f)
            tier_stats[tier_name(f)] = dict(
                top_frac=f,
                capture=rec["capture"] if rec else None,
                lift=rec["lift"] if rec else None,
                p=rec["p"] if rec else None,
                lift_reach=rec.get("lift_reach") if rec else None,
                p_reach=rec.get("p_reach") if rec else None)
        graduated = dict(
            note=("Band membership is the increment (a top10 feature is in "
                  "the top 10% but not the top 5%), while each tier's "
                  "capture/lift/p describes the CUMULATIVE top-X% cut, "
                  "measured against the same permutation null as everything "
                  "else (composite_skill). With 43 truth clusters each worth "
                  "2.3% capture, only the top05 and top20 cuts are "
                  "individually significant; top10 and top35 are drawn as "
                  "graduation, not claimed as evidence. A feature's band "
                  "describes the GROUND it stands on, not the feature: a "
                  "village in top05 is a village on ground that "
                  "concentrates reported mines, nothing more."),
            tiers=tier_stats,
            counts={k: {tn: sum(1 for x in v if x["tier"] == tn)
                        for tn in tier_stats}
                    for k, v in gfeats.items()},
            features=gfeats)
        log("  graduated counts:", graduated["counts"])


    out = dict(
        generated_by="scripts/predict_mining_xsa.py",
        aoi=AOI,
        question=("Do the context layers concentrate the 89 reported mine "
                  "sites, and if so where else does that context exist?"),
        truth_caveat=(
            "Anchors are OSM tags, Crisis Tracker attack records, UCDP GED "
            "and USGS points - reachability- and conflict-biased, coordinates "
            "sometimes village label positions. A lift means 'reported mines "
            "sit on this ground', never 'mines do'. Candidates are places to "
            "point imagery and questions, not a treasure map (AGENTS.md "
            "invariant 12)."),
        n_anchors=len(anchors),
        n_clusters=len(clusters),
        n_cells=len(grid),
        cell_deg=CELL,
        permutations=PERMS,
        reach_null=dict(
            method=("Target-group background (Phillips et al. 2009): the "
                    "truth set is reports, not mines, so each signal is "
                    "also tested against a null that rethrows pseudo-sites "
                    "where the reporting process demonstrably reaches - a "
                    "20 km KDE over ALL Crisis Tracker incidents, UCDP GED "
                    "events (precision<=3) and OSM places in the AOI, "
                    "regardless of topic. Votes are decided on q_bh_reach; "
                    "the uniform columns remain, and their difference is "
                    "the measured reporting bias of each signal."),
            sources=reach_meta),
        signals=signals,
        passing=passing,
        factors_voting=factors_voting if comp is not None else [],
        composite_method=(
            "Factor-grouped equal vote: passing signals are averaged within "
            "their factor (settlement_fabric / deforestation / hydro / "
            "geology / historic), then factors vote equally. Grouping "
            "follows the PCA (scripts/pca_mining_signals.py): the seven "
            "settlement variants load ~0.4 on a single component - one "
            "measurement taken seven ways must not cast seven votes."),
        composite_skill=comp_skill,
        robustness_by_truth_source=robustness,
        spatial_cv=cv,
        note=result_note,
        candidates=candidates,
        abandoned_village_gold_watchlist=dict(
            hypothesis=(
                "1930s villages with no modern settlement within 3 km, "
                "sitting within 5 km of a gold-graded geology contact - the "
                "Hofrat el Nahas pattern, stated in report section 4 before "
                "testing. Against the uniform null: lift "
                f"{signals['hist_abandoned_village_on_gold_contact'].get('lift')}, "
                f"q={signals['hist_abandoned_village_on_gold_contact'].get('q_bh')} "
                "(not significant - but that null lets pseudo-mines land on "
                "ground no reporter has ever reached). Against the "
                "reach-corrected null (reach_null above): lift "
                f"{signals['hist_abandoned_village_on_gold_contact'].get('lift_reach')}, "
                f"q={signals['hist_abandoned_village_on_gold_contact'].get('q_bh_reach')} "
                f"on n={signals['hist_abandoned_village_on_gold_contact'].get('n')} "
                "covered clusters - "
                + ("significant; the signal votes in the composite via the "
                   "historic factor, and this list names its members on the "
                   "hottest ground."
                   if signals['hist_abandoned_village_on_gold_contact'
                              ]['verdict'] == 'signal'
                   else "still not significant; listed for imagery "
                        "follow-up only.")),
            ranking=("Ranked by the composite score of the ground each "
                     "village stands on (ties: closer gold contact first), "
                     "deduped at 10 km - so the list and the gridded "
                     "surface agree by construction."),
            total_matches=watch_total,
            listed=len(watchlist),
            places=watchlist),
        graduated=graduated,
        anchors=[dict(lat=round(a["lat"], 5), lon=round(a["lon"], 5),
                      source=a["source"], resource=a["resource"])
                 for a in anchors],
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1)
    log(f"wrote {OUT}")

    # ---- GeoJSON for QGIS: three layers in one FeatureCollection,
    # distinguished by `layer` (filter in QGIS: "layer" = 'candidate').
    # - candidate: the 12 report points (cell centres, ~3 km precision)
    # - watchlist: abandoned 1930s villages on gold contacts (top 25)
    # - composite_top20: the scored surface itself as cell-centre points,
    #   so heat can be styled/interpolated without shipping 15,788 cells.
    feats = []
    for c in candidates:
        props = {k: v for k, v in c.items()
                 if k not in ("lat", "lon")}
        props["layer"] = "candidate"
        feats.append(dict(type="Feature", geometry=dict(
            type="Point", coordinates=[c["lon"], c["lat"]]),
            properties=props))
    for wl in watchlist:
        props = {k: v for k, v in wl.items() if k not in ("lat", "lon")}
        props["layer"] = "watchlist_abandoned_village_gold"
        feats.append(dict(type="Feature", geometry=dict(
            type="Point", coordinates=[wl["lon"], wl["lat"]]),
            properties=props))
    if comp is not None:
        thr_v = np.nanquantile(comp, 1 - 0.35)
        for i in np.nonzero(comp >= thr_v)[0]:
            t = tier_of_cell(int(i))
            feats.append(dict(type="Feature", geometry=dict(
                type="Point",
                coordinates=[round(float(grid[i][0]), 4),
                             round(float(grid[i][1]), 4)]),
                properties=dict(
                    layer="composite_graduated",
                    tier=tier_name(t) if t else None,
                    score=round(float(comp[i]), 4),
                    pctile=round(float(
                        np.mean(comp[i] >= comp[~np.isnan(comp)])) * 100, 1))))
    gj = dict(
        type="FeatureCollection",
        name="xsa_mining_prediction",
        description=(
            "Predicted artisanal-mining context, XSA_Study_Area. "
            "Layers: candidate (12 report points), "
            "watchlist_abandoned_village_gold (top 25), composite_graduated "
            "(scored 0.05 deg cell centres, tier=top05/top10/top20/top35). "
            "Candidates are cell centres "
            "good to ~3 km - neighbourhoods for imagery, not pits. Truth "
            "set is reachability/conflict-biased; see prediction.json. "
            "Generated by scripts/predict_mining_xsa.py."),
        features=feats)
    gj_out = OUT.with_name("prediction.geojson")
    json.dump(gj, open(gj_out, "w"))
    log(f"wrote {gj_out} ({len(feats)} features)")


if __name__ == "__main__":
    main()
