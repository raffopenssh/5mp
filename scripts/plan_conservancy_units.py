#!/usr/bin/env python3
"""Bootstrap a zoning of the whole study area from what we already measured,
then check it against the zones we drew by hand.

WHY.  A conservancy, a wilderness zone or a corridor is only as good as the
sentence that explains it: "from the Bo River up to the Marra Hills". So the
planner does not draw buffers or Voronoi cells. It builds a LEGIBILITY SURFACE
— every named/order-4+ river (HydroRIVERS; unnamed reaches take the 1930s
sheet's water label by vertex vote), every hill ridge (histmap peak/trig
symbols + "J. …"/"… HILLS" labels chained ≤15 km), every country border —
floods it from community seeds (settlement clusters) and from empty-land
seeds, and lets the floods meet where the land already has an edge. Units are
then merged across their LEAST legible shared edge until every unit is at
least --min-ha, so what survives as a boundary is what was easiest to see.

WHAT EACH UNIT CARRIES  (2 km cells, equal-area projection):
  people     clusters, GHSL population lower bound, new-since-2015, built km2
  fire       detections 2024-25 per 1,000 km2, Nov–Feb share, fronts touching,
             % transhumance, long (≥150 km) fronts, dominant direction
  land       GLAD cropland % 2003→2019, verified clearing km2 (all / ≥2020)
  mining     reported sites, model candidates, top-5% cells (imagery targets,
             not mines — composite skill in prediction.json)
  boundary   the feature on each side with km, and % of perimeter that
             follows nothing nameable (a boundary nobody can see is a cost)
  class      core | wilderness | community | corridor, from thresholds
             (--core-pop-km2 etc.) so the same rule is applied everywhere.

VALIDATE.  `validate` re-draws every hand-drawn plan KML and every WDPA
polygon in data/plan_zones/existing_pas/ as the union of units with majority
inside, and reports IoU, how much of the reference perimeter itself follows a
legible feature, and what class mix the planner assigns to the reference
(e.g. a corridor KML that the planner calls 80% corridor is corroboration; one
it calls community is a disagreement worth a look).

    python3 scripts/plan_conservancy_units.py build     [--cell-km 2 --min-ha 2000 --seed-pop 300]
    python3 scripts/plan_conservancy_units.py validate
    python3 scripts/plan_conservancy_units.py rank --class community --exclude "Pongo-Wau,Southern NP" --country SSD --top 20
    python3 scripts/plan_conservancy_units.py show 17
    python3 scripts/plan_conservancy_units.py export 17,23,41

Outputs → data/plan_zones/conservancy_units/{units.geojson,units.json,units.kml,
RANK.txt,VALIDATION.txt}. Populations are satellite lower bounds; names are
HydroRIVERS / 1930s sheets and must be verified on the ground.
"""
import argparse, json, math, os, pickle, re, sqlite3, sys, time
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np, pyproj
from shapely.geometry import Polygon, MultiPolygon, LineString, Point, shape, mapping
from shapely.ops import unary_union, transform
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
DB, HDB = ROOT / "db.sqlite3", ROOT / "data/histmaps/labels.sqlite3"
ZONES = ROOT / "data/plan_zones"
OUT = ROOT / "data/plan_zones" / ("conservancy_units" if os.environ.get("PLAN_AOI", "XSA_Study_Area") == "XSA_Study_Area" else f"conservancy_units_{os.environ['PLAN_AOI']}")
AOI = os.environ.get("PLAN_AOI", "XSA_Study_Area")          # any AOI/park id with settlements, fire groups, rivers
CEA = "+proj=cea +lon_0=27 +lat_ts=7.5"
FWD = pyproj.Transformer.from_crs(4326, CEA, always_xy=True).transform
INV = pyproj.Transformer.from_crs(CEA, 4326, always_xy=True).transform
km2 = lambda g: transform(FWD, g).area / 1e6
RIDGE_LINK_KM, RIDGE_NAME_KM, RIVER_NAME_KM, RIDGE_MIN_PTS = 15.0, 6.0, 2.0, 3
HILL_RE = re.compile(r"^(J\.|JEBEL|JABAL)\s|\bHILLS?\b|\bMTS?\b|\bMOUNT", re.I)
COUNTRIES = ("SSD", "CAF", "SDN", "COD")
CLASSES = ["core", "wilderness", "community", "corridor"]
INVISIBLE_KINDS = ("geology", "frame")     # legible to the planner, not to a person standing there

def dkm(a, b):
    return math.hypot((a[0]-b[0])*111*math.cos(math.radians((a[1]+b[1])/2)), (a[1]-b[1])*111)
def log(*a): print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)

# ============================================================ inputs
def _valid_poly(pts):
    """A hand-drawn ring may self-intersect (the Numatina grazing zone does: 93 points, one crossing). buffer(0)
    silently keeps one lobe; make_valid keeps every lobe as polygons — take those, drop slivers."""
    from shapely.validation import make_valid
    p = Polygon(pts)
    if p.is_valid: return p
    mv = make_valid(p); parts = [g for g in getattr(mv, "geoms", [mv]) if g.geom_type == "Polygon" and g.area > 1e-4]
    return unary_union(parts) if parts else p.buffer(0)
def read_kml(path):
    t = path.read_text(encoding="utf-8", errors="replace"); out = {}
    for pm in re.findall(r"<Placemark>(.*?)</Placemark>", t, re.S):
        nm = re.search(r"<name>(.*?)</name>", pm, re.S)
        nm = (nm.group(1) if nm else path.stem).replace("&apos;", "'").strip()
        polys = [_valid_poly([tuple(map(float, c.split(",")[:2])) for c in m.group(1).split()])
                 for m in re.finditer(r"<coordinates>(.*?)</coordinates>", pm, re.S) if len(m.group(1).split()) >= 4]
        pts = [Point(*map(float, m.group(1).split()[0].split(",")[:2])) for m in re.finditer(r"<Point>.*?<coordinates>(.*?)</coordinates>", pm, re.S)]
        if polys: out[nm] = unary_union(polys)
        elif pts: out[nm] = pts[0]
    return out
def zones():
    z = {}
    for f in sorted(ZONES.glob("*.kml")): z.update(read_kml(f))
    return z
def existing_pas():
    out = {}
    for f in sorted((ZONES / "existing_pas").glob("*.json")):
        pa = json.load(open(f)).get("protected_area") or {}
        g = (pa.get("geojson") or {}).get("geometry")
        des = pa.get("designation"); des = des.get("name") if isinstance(des, dict) else des
        if not g: continue
        geom = shape(g)
        if geom.geom_type == "Point":     # WDPA holds only a centroid (Radom): stand in a circle of the reported area, and say so in the name
            km2_ = float(pa.get("reported_area") or 0)
            if km2_ <= 0: continue
            geom = transform(INV, transform(FWD, geom).buffer(math.sqrt(km2_*1e6/math.pi))); des = f"{des}, approx. circle of reported {km2_:,.0f} km2"
        out[f"{pa.get('name')} ({des})"] = geom.buffer(0)
    return out
def references():
    if AOI == "XSA_Study_Area":
        r = {f"PLAN {k}": g for k, g in zones().items()}
        r.update({f"WDPA {k}": g for k, g in existing_pas().items()})
        return r
    pk = park_geom(AOI)
    r = {f"PARK {AOI}": pk} if pk is not None else {}
    for k, g in existing_pas().items():
        if pk is not None and g.distance(pk) < 1.5: r[f"WDPA {k}"] = g
    return r
PARK_KEY = "Pongo" if AOI == "XSA_Study_Area" else f"PARK {AOI}"
def park_geom(pid):
    """Boundary of a keystone park from data/keystones_with_boundaries.json (what the server serves at /api/park/{id}/boundary)."""
    for k in json.load(open(ROOT / "data/keystones_with_boundaries.json")):
        if k.get("id") == pid and k.get("geometry"): return shape(k["geometry"]).buffer(0)
    return None
def aoi_geom(con):
    """The planning area. For a user AOI its polygon; for a park, the park plus the buffer its per-park tables
    already cover (settlements/fire/rivers are ingested ~50 km around a park), i.e. the land the data can speak for."""
    row = con.execute("SELECT geometry FROM aois WHERE id=?", (AOI,)).fetchone()
    if row: return shape(json.loads(row[0]))
    pk = park_geom(AOI)
    if pk is None: raise SystemExit(f"no AOI or keystone park {AOI}")
    x0, y0, x1, y1 = con.execute("SELECT min(lon), min(lat), max(lon), max(lat) FROM park_settlements WHERE park_id=?", (AOI,)).fetchone()
    from shapely.geometry import box
    hull = pk.buffer(0.45).union(box(x0, y0, x1, y1)) if x0 is not None else pk.buffer(0.45)
    return hull.convex_hull.buffer(0.05)
def countries():
    d = json.load(open(ROOT / "data/world_countries.geojson")); aoi = aoi_geom(sqlite3.connect(DB)); out = {}
    for f in d["features"]:
        g = shape(f["geometry"]).buffer(0)
        if g.intersects(aoi): out[f["properties"]["iso3"]] = g
    return out

def rivers(con, hcon, min_order):
    rows = con.execute("SELECT COALESCE(name,''), stream_order, geojson FROM park_rivers_hydro WHERE park_id=? AND (stream_order>=? OR name!='')", (AOI, min_order)).fetchall()
    wl = [(re.sub(r"\s+", " ", t).strip(), lo, la) for t, lo, la in hcon.execute("SELECT text, lon, lat FROM labels_dedup WHERE category='water'")]
    wl = [w for w in wl if len(w[0]) > 2 and "?" not in w[0] and "=" not in w[0]]
    tree = STRtree([Point(lo, la) for _, lo, la in wl]); by = defaultdict(list); nsheet = 0
    for name, order, gj in rows:
        g = shape(json.loads(gj))
        if not name:
            votes = Counter()
            for x, y in (c for part in getattr(g, "geoms", [g]) for c in part.coords):
                for i in tree.query(Point(x, y).buffer(RIVER_NAME_KM/111)):
                    if dkm((x, y), (wl[i][1], wl[i][2])) <= RIVER_NAME_KM: votes[wl[i][0]] += 1
            if votes and votes.most_common(1)[0][1] >= 2: name = votes.most_common(1)[0][0] + " (1930s sheet)"; nsheet += 1
        by[name or f"unnamed river (order {order})"].append((g, order))
    log(f"rivers: {len(rows)} reaches, {len(by)} names, {nsheet} named from sheets")
    return [(unary_union([g for g, _ in gs]), n, "river", max(o for _, o in gs) + (2 if "unnamed" not in n else 0)) for n, gs in by.items()]

def ridges(hcon, aoi):
    x0, y0, x1, y1 = aoi.bounds; B = (x0, x1, y0, y1)
    pts = [(lo, la) for lo, la in hcon.execute("SELECT lon, lat FROM symbols WHERE category IN ('peak','trig_point') AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?", B)]
    labs = [(re.sub(r"\s+", " ", t).strip(), lo, la) for t, lo, la in hcon.execute("SELECT text, lon, lat FROM labels_dedup WHERE category='terrain' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?", B) if HILL_RE.search(t)]
    if not pts: log("ridges: no hilltop marks inside the area (no 1930s sheet coverage)"); return [], []
    pts += [(lo, la) for _, lo, la in labs]
    n = len(pts); parent = list(range(n)); tree = STRtree([Point(p) for p in pts])
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, p in enumerate(pts):
        for j in tree.query(Point(p).buffer(RIDGE_LINK_KM/111)):
            if j > i and dkm(p, pts[j]) <= RIDGE_LINK_KM: parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in range(n): groups[find(i)].append(pts[i])
    ltree = STRtree([Point(lo, la) for _, lo, la in labs]); lines, beacons = [], []
    for g in groups.values():
        names = []
        for p in g:
            for k in ltree.query(Point(p).buffer(RIDGE_NAME_KM/111)):
                if dkm(p, labs[k][1:]) <= RIDGE_NAME_KM and labs[k][0] not in names: names.append(labs[k][0])
        name = " – ".join(names[:3]) if names else "unnamed hills"
        if len(g) < RIDGE_MIN_PTS: beacons += [(p, name) for p in g]; continue
        xy = np.array(g); c = xy.mean(0); _, _, vt = np.linalg.svd(xy - c)
        lines.append((LineString(xy[np.argsort((xy - c) @ vt[0])]), name + " ridge", "ridge", 6 if names else 4))
    log(f"ridges: {len(lines)} chains from {n} hilltop marks, {len(beacons)} lone beacons")
    return lines, beacons

def hist_boundaries(hcon, aoi):
    """1930s tribal / sub-tribal / district boundaries from the traced sheets. These are the
    customary edges the Land Act 2009 calls community land — the most defensible conservancy edge there is."""
    out = []
    for name, style, pts in hcon.execute("SELECT name, style, pts FROM lines_stitched WHERE kind='boundary' AND minlon>? AND minlat>? AND maxlon<? AND maxlat<?", aoi.bounds):
        nm = (name or "").strip()
        if "Internat" in nm or re.match(r"^[\d°'\s]+$", nm): continue
        tribal = bool(re.search(r"tribal|Tribal|District|Distr|Province|Prov\.|Bound", nm)) or style == "dotted"
        if not tribal: continue
        try: xy = json.loads(pts)
        except Exception: continue
        if len(xy) < 2: continue
        g = LineString([(p[0], p[1]) for p in xy]).intersection(aoi)
        if g.is_empty: continue
        label = nm if nm else ("sub-tribal boundary (1930s dotted)" if style == "dotted" else "boundary (1930s sheet)")
        out.append((g, f"1930s {label}", "hist_boundary", 5))
    log(f"1930s tribal/district boundaries: {len(out)}")
    return out

def hist_watercourses(hcon, aoi, min_km=15):
    """Named 1930s watercourses (khors) the HydroRIVERS net lacks — a khor a herder names is a boundary he can keep."""
    out = []
    for name, pts, L in hcon.execute("SELECT name, pts, length_km FROM lines_stitched WHERE kind='watercourse' AND name!='' AND length_km>=? AND minlon>? AND minlat>? AND maxlon<? AND maxlat<?", (min_km, *aoi.bounds)):
        if "?" in name or len(name) < 3: continue
        try: g = LineString([(p[0], p[1]) for p in json.loads(pts)])
        except Exception: continue
        out.append((g, f"{name.strip()} (1930s sheet)", "hist_water", 4))
    log(f"1930s named watercourses >= {min_km} km: {len(out)}")
    return out

def roads(con):
    """Primary/secondary/tertiary roads (OSM via HeiGIT). A road is the one boundary everyone already uses."""
    by = defaultdict(list)
    for name, ht, gj in con.execute("SELECT COALESCE(name,''), highway_type, geojson FROM roads_heigit WHERE park_id=? AND highway_type IN ('trunk','primary','secondary','tertiary')", (AOI,)):
        by[(name or f"{ht} road")].append(shape(json.loads(gj)))
    log(f"roads: {sum(len(v) for v in by.values())} segments, {len(by)} names")
    return [(unary_union(gs), n if "road" in n.lower() else f"{n} road", "road", 5) for n, gs in by.items()]

def geology(aoi):
    """Published geological maps (CAR 1964, Sudan 2004; data/geomaps/*_units.geojson).
    Returns (units: [(geom, code, name, group, commodities, affinity)], contacts: legible features).
    Two uses. (1) A lithological CONTACT is a line on the ground more often than not — ironstone plateau
    edge, alluvium/basement break, granite inselberg foot — so it enters the legibility surface at low weight
    (2 of 8): it can steer a boundary through open bush, it cannot overrule a river. (2) A unit's rock is its
    mining PRIOR: where no detection model exists, the commodity affinity of the rock (docs/GEOLOGY.md,
    measured skill only for CAR junctions) says which conservancy is a licence target."""
    units, contacts = [], []
    for f in sorted((ROOT / "data/geomaps").glob("*_units.geojson")):
        for ft in json.load(open(f))["features"]:
            g = shape(ft["geometry"]).buffer(0)
            if not g.intersects(aoi): continue
            p = ft["properties"]; units.append((g.intersection(aoi), p["code"], p["name"], p.get("group"), p.get("commodities") or [], p.get("affinity") or []))
    for f in sorted((ROOT / "data/geomaps").glob("*_contacts.geojson")):
        for ft in json.load(open(f))["features"]:
            g = shape(ft["geometry"]).intersection(aoi)
            if g.is_empty or g.length*111 < 10: continue
            p = ft["properties"]; contacts.append((g, f"geological contact {p['code_a']}|{p['code_b']} ({p['sheet']})", "geology", 2))
    log(f"geology: {len(units)} rock units, {len(contacts)} contacts >= 10 km in the area")
    return units, contacts

def osm_towns(con, hcon=None):
    """Named places for honest town names: OSM city/town/village, plus 1930s sheet place labels in CAPITALS
    (the sheets set district towns in capitals — WAU, RAGA — which OSM lacks north of Wau)."""
    out = [(n, lo, la, t) for n, lo, la, t in con.execute("SELECT name, lon, lat, place_type FROM osm_places WHERE place_type IN ('city','town','village') AND name IS NOT NULL")]
    tn = ZONES / "town_names.json"          # towns OSM lacks (Wau, Raga, Deim Zubeir, Koko) — verified against the sheets, see the file's _note
    if tn.exists(): out += [(t["name"], t["lon"], t["lat"], "verified_town") for t in json.load(open(tn))["towns"]]
    hcon = hcon or sqlite3.connect(HDB)
    for t, lo, la in hcon.execute("SELECT text, lon, lat FROM labels_dedup WHERE category='place'"):
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) < 3 or "?" in t or re.search(r"FORT|MISSION|STA\b|W\.?S\.?|POLICE|POST|REST|CAMP|HILLS?|QOZ|JEBEL|^J\.|KHOR|^K\.|^R\.|RIVER|BAHR|WELLS?|POOL|RAPIDS|ISLAND|DESERTED|UNINHABITED|SITE OF", t, re.I): continue
        if not re.search(r"[a-z]", t) and re.match(r"^[A-Z][A-Z .'-]+$", t): out.append((t.title() + " (1930s)", lo, la, "hist_town"))
        elif re.match(r"^[A-Z][A-Za-z' .-]{2,}$", t): out.append((t + " (1930s village)", lo, la, "hist_place"))
    return out
TOWN_REACH_KM = {"city": 8.0, "town": 8.0, "village": 8.0, "verified_town": 8.0, "hist_town": 4.0, "hist_place": 3.0}   # how far a name may travel to a GHSL cluster
TOWN_PENALTY_KM = {"hist_town": 4.0, "hist_place": 6.0}

# ============================================================ raster planner
class Grid:
    def __init__(self, aoi, cell_km):
        m = transform(FWD, aoi); x0, y0, x1, y1 = m.bounds; self.res = cell_km*1000
        self.x0, self.y1 = x0, y1; self.w = int((x1-x0)/self.res)+1; self.h = int((y1-y0)/self.res)+1
        import rasterio
        self.tr = rasterio.Affine(self.res, 0, x0, 0, -self.res, y1)
        self.mask = self.rasterize([(m, 1)], fill=0).astype(bool); self.aoi = aoi
    def rasterize(self, shapes, fill=0, all_touched=False, dtype="int32"):
        from rasterio import features
        return features.rasterize(shapes, out_shape=(self.h, self.w), transform=self.tr, fill=fill, all_touched=all_touched, dtype=dtype)
    def rc(self, lon, lat):
        x, y = FWD(lon, lat); return int((self.y1-y)//self.res), int((x-self.x0)//self.res)
    def rc_arr(self, lon, lat):
        x, y = FWD(np.asarray(lon), np.asarray(lat)); return ((self.y1-y)//self.res).astype(int), ((x-self.x0)//self.res).astype(int)
    def inside(self, r, c): return (r >= 0) & (r < self.h) & (c >= 0) & (c < self.w)
    def cell_km2(self): return (self.res/1000)**2

def legibility(G, feats):
    """surf: 0..1, 1 on a legible feature decaying to 0 by ~3 cells, weighted by feature strength.
    Also sets G.fid (nearest feature index per cell) and G.fdist (km to it) for boundary descriptions."""
    from scipy import ndimage
    surf = np.zeros((G.h, G.w), np.float32); fid = np.full((G.h, G.w), -1, np.int32)
    for j, (g, n, k, w) in sorted(enumerate(feats), key=lambda t: t[1][3]):   # strong features overwrite weak
        if g.is_empty: continue
        r = G.rasterize([(transform(FWD, g), 1)], all_touched=True).astype(bool)
        d = ndimage.distance_transform_edt(~r)
        surf = np.maximum(surf, (min(w, 8)/8.0) * np.exp(-d/1.5)); fid[r] = j
    d, (ir, ic) = ndimage.distance_transform_edt(fid < 0, return_indices=True)
    G.fid = fid[ir, ic]; G.fdist = d * G.res/1000
    return surf

def describe_mask(G, mask, feats, max_km=2.5):
    """Boundary cells of mask → nearest legible feature within max_km, by side. Returns (parts, unattributed_km, total_km)."""
    from scipy import ndimage
    edge = mask & ~ndimage.binary_erosion(mask, border_value=0)
    rr, cc = np.nonzero(edge)
    if not len(rr): return [], 0.0, 0.0
    cr, cc0 = rr.mean(), cc.mean(); ang = np.degrees(np.arctan2(-(rr-cr), cc-cc0)) % 360
    sides = np.array(["E", "NE", "N", "NW", "W", "SW", "S", "SE"])[(((ang+22.5) % 360)//45).astype(int)]
    ok = G.fdist[rr, cc] <= max_km; ids = G.fid[rr, cc]; step = G.res/1000
    tot = len(rr)*step
    # a boundary a villager, herder or ranger can walk: rivers, khors, ridges, 1930s customary lines, roads, borders.
    # A geological contact steers the mesh (weight 2) but nobody can see it on the ground — it counts as UNNAMED here
    # and is reported separately so the reader knows that stretch needs beacons.
    vis = np.array([feats[j][2] not in INVISIBLE_KINDS for j in ids]) & ok
    un = (~vis).sum()*step; geo_km = (ok & ~vis).sum()*step
    by = Counter(); side = defaultdict(Counter)
    for j, sd in zip(ids[vis], sides[vis]): by[feats[j][1]] += step; side[feats[j][1]][sd] += step
    parts = [f"{nm} on the {'/'.join(k for k, _ in side[nm].most_common(2))} ({k:.0f} km)" for nm, k in by.most_common(6)]
    if geo_km >= step: parts.append(f"{geo_km:.0f} km along a geological contact only (not visible on the ground — needs beacons)")
    return parts, un, tot

def seeds(con, G, seed_pop, empty_km):
    """Community seeds: settlement clusters (single-linkage 10 km, pop>=seed_pop) → 1 marker at pop-weighted centre.
    Empty-land seeds: every empty_km on the lattice of cells > empty_km/2 from any community seed."""
    rows = con.execute("SELECT lat, lon, population_est, nearest_place FROM park_settlements WHERE park_id=? AND population_est>0", (AOI,)).fetchall()
    n = len(rows); parent = list(range(n)); tree = STRtree([Point(r[1], r[0]) for r in rows])
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, r in enumerate(rows):
        for j in tree.query(Point(r[1], r[0]).buffer(10/111)):
            if j > i and dkm((r[1], r[0]), (rows[j][1], rows[j][0])) <= 10: parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in range(n): groups[find(i)].append(rows[i])
    markers = np.zeros((G.h, G.w), np.int32); names = {}; k = 0
    for g in groups.values():
        pop = sum(r[2] for r in g)
        if pop < seed_pop: continue
        lon = sum(r[1]*r[2] for r in g)/pop; lat = sum(r[0]*r[2] for r in g)/pop
        r_, c_ = G.rc(lon, lat)
        if not G.inside(np.array([r_]), np.array([c_]))[0] or not G.mask[r_, c_] or markers[r_, c_]: continue
        k += 1; markers[r_, c_] = k; names[k] = max(g, key=lambda r: r[2])[3]
    ncomm = k
    from scipy import ndimage
    d = ndimage.distance_transform_edt(markers == 0) * G.res/1000
    step = int(empty_km*1000/G.res)
    for r_ in range(step//2, G.h, step):
        for c_ in range(step//2, G.w, step):
            if G.mask[r_, c_] and d[r_, c_] > empty_km/2:
                k += 1; markers[r_, c_] = k; names[k] = f"empty land {c_},{r_}"
    log(f"seeds: {ncomm} community clusters (pop>={seed_pop}), {k-ncomm} empty-land markers")
    return markers, names, ncomm

def segment(G, surf, markers):
    from skimage.segmentation import watershed
    lab = watershed(surf, markers=markers, mask=G.mask, compactness=0.0)
    return lab

def edge_table(lab):
    """(a,b) -> [n_boundary_pixels, sum legibility] over 4-neighbour label changes."""
    E = defaultdict(lambda: [0, 0.0]); return E
def edges(lab, surf):
    E = defaultdict(lambda: [0, 0.0])
    for a, b, s in ((lab[:, :-1], lab[:, 1:], np.maximum(surf[:, :-1], surf[:, 1:])), (lab[:-1, :], lab[1:, :], np.maximum(surf[:-1, :], surf[1:, :]))):
        m = (a != b) & (a > 0) & (b > 0)
        lo, hi = np.minimum(a[m], b[m]), np.maximum(a[m], b[m]); ss = s[m]
        key = lo.astype(np.int64)*10_000_000 + hi
        for kk, cnt, sm in zip(*np.unique(key, return_counts=True), np.bincount(np.unique(key, return_inverse=True)[1], weights=ss)):
            E[(int(kk//10_000_000), int(kk % 10_000_000))] = [int(cnt), float(sm)]
    return E

def merge_units(lab, surf, G, min_ha, cls=None, target_ha=0, weak=0.35, max_ha=0):
    """Region merging on the label image.
    Pass 1 (cls None): absorb units < min_ha into the neighbour across the least legible edge.
    Pass 2 (cls given): a unit merges only with a neighbour of the SAME class — below min_ha always
    (best same-class neighbour, else any), below target_ha only across a WEAK edge (mean legibility
    < weak: nothing on the ground separates them), never into a unit above max_ha.
    A river or ridge between two like units keeps them apart; that is the point."""
    cell_ha = G.cell_km2()*100
    while True:
        cnt = np.bincount(lab.ravel()); ha = cnt*cell_ha
        small = [i for i in np.flatnonzero((cnt > 0) & (ha < max(min_ha, target_ha))) if i > 0]
        if not small: return lab
        E = edges(lab, surf); nb = defaultdict(list)
        for (a, b), (n, sm) in E.items(): nb[a].append((b, n, sm)); nb[b].append((a, n, sm))
        remap = {}; done = set()
        for i in sorted(small, key=lambda i: cnt[i]):
            if i in done or not nb[i]: continue
            same = [(b, n, sm) for b, n, sm in nb[i] if cls is None or cls.get(b) == cls.get(i)]
            fit = lambda L: [(b, n, sm) for b, n, sm in L if not max_ha or ha[i]+ha[b] <= max_ha]
            if ha[i] < min_ha: cands = fit(same) or fit(nb[i]) or (same or nb[i])
            else: cands = [(b, n, sm) for b, n, sm in fit(same) if sm/n < weak]
            if not cands: continue
            b, n, sm = min(cands, key=lambda t: (t[2]/t[1], -t[1]))
            while b in remap: b = remap[b]
            if b == i or b in done: continue
            remap[i] = b; done.add(i); done.add(b)
        if not remap: return lab
        lut = np.arange(lab.max()+1)
        for a, b in remap.items():
            while b in remap: b = remap[b]
            lut[a] = b
        lab = lut[lab]
        log(f"  merge pass: {len(remap)} absorbed, {len(np.unique(lab))-1} units")

# ============================================================ evidence: per-cell rasters, computed ONCE
def cells(con, G):
    """Everything the report measures, as 2-D rasters on the grid, computed once per AOI and cached on G.
    Any label image (fine mesh, merged units, a conservancy, a corridor band, the park itself) is then
    measured by the same bincount in milliseconds — the recursion the user asked for: the planner
    assesses what it proposes with exactly the machinery 5MP uses for a park."""
    if getattr(G, "C", None) is not None: return G.C
    from skimage.draw import line as skline
    Z = lambda: np.zeros((G.h, G.w), np.float64)
    C = {k: Z() for k in ["clusters", "pop", "new15", "camps", "built", "built00", "built15",
                          "fire", "fire_nf", "clear_ev", "clear", "clear20", "encroach",
                          "mine_rep", "mine_cand", "mine_top05", "mine_watch", "crop03", "crop19", "cropn", "osm_places"]}
    def add(key, lon, lat, w=1):
        r, c = G.rc_arr(lon, lat); ok = G.inside(r, c)
        w = np.broadcast_to(np.asarray(w, float), ok.shape)[ok]
        np.add.at(C[key], (r[ok], c[ok]), w)
    # settlements (GHSL clusters; population is a lower bound)
    S = con.execute("SELECT lat, lon, population_est, classification, persistence, nearest_place, area_m2, surface_e2000_m2, surface_e2015_m2 FROM park_settlements WHERE park_id=?", (AOI,)).fetchall()
    G.settlements = S
    if S:
        la = np.array([x[0] for x in S]); lo = np.array([x[1] for x in S]); pop = np.array([x[2] or 0 for x in S], float)
        add("clusters", lo, la); add("pop", lo, la, pop)
        add("new15", lo, la, np.array([x[4] == "recent" for x in S], float)); add("camps", lo, la, np.array([x[3] == "temporary_camp" for x in S], float))
        add("built", lo, la, np.array([(x[6] or 0)/1e6 for x in S])); add("built00", lo, la, np.array([(x[7] or 0)/1e6 for x in S])); add("built15", lo, la, np.array([(x[8] or 0)/1e6 for x in S]))
    # OSM named places (independent check on GHSL: a hamlet OSM names is people even if GHSL saw no roofs)
    P = con.execute("SELECT lon, lat FROM osm_places WHERE park_id=? AND place_type IN ('city','town','village','hamlet')", (AOI,)).fetchall()
    if P: add("osm_places", np.array([x[0] for x in P]), np.array([x[1] for x in P]))
    # fire grid 2024-25 (3-satellite fleet; never compare to pre-2024 counts)
    x0, y0, x1, y1 = G.aoi.bounds
    rows = con.execute("SELECT d, xi, yi, n FROM fire_grid_month WHERE d >= '2024-01-01' AND d < '2026-01-01' AND xi BETWEEN ? AND ? AND yi BETWEEN ? AND ?",
                       (int(x0*10)-1, int(x1*10)+1, int(y0*10)-1, int(y1*10)+1)).fetchall()
    if rows:
        lon = np.array([r[1] for r in rows])*0.1+0.05; lat = np.array([r[2] for r in rows])*0.1+0.05; n = np.array([r[3] for r in rows], float)
        nf = np.array([r[0][5:7] in ("11", "12", "01", "02") for r in rows])
        add("fire", lon, lat, n); add("fire_nf", lon[nf], lat[nf], n[nf])
    # fronts: each trajectory → the set of cells it crosses, kept as index arrays (a front touching a unit counts once)
    G.longdens = Z().astype(np.float32); G.alldens = Z().astype(np.float32); F = []
    for g in json.load(open(ROOT / "data/fire_groups_v5" / f"{AOI}.json")):
        t = g.get("trajectory") or []
        if len(t) < 2: continue
        rr, cc = G.rc_arr([p[0] for p in t], [p[1] for p in t]); cl = []
        for i in range(len(t)-1):
            lr, lc = skline(int(rr[i]), int(cc[i]), int(rr[i+1]), int(cc[i+1])); ok = G.inside(lr, lc); cl.append(lr[ok]*G.w + lc[ok])
        cl = np.unique(np.concatenate(cl)) if cl else np.zeros(0, int)
        if not len(cl): continue
        th = g.get("group_type") == "transhumance"; lg = (g.get("distance_km") or 0) >= 150
        G.alldens.ravel()[cl] += 1
        if th and lg: G.longdens.ravel()[cl] += 1
        F.append((cl, th, lg, g.get("direction")))
    G.fronts = F
    # clearing (reviewed events only)
    D = con.execute("SELECT lat, lon, year, area_km2, classification FROM deforestation_events WHERE park_id=? AND needs_review=0", (AOI,)).fetchall()
    if D:
        la = np.array([d[0] for d in D]); lo = np.array([d[1] for d in D]); ar = np.array([d[3] or 0 for d in D]); yr = np.array([d[2] or 0 for d in D]); enc = np.array([d[4] in ("encroachment", "slash_burn") for d in D])
        add("clear_ev", lo, la); add("clear", lo, la, ar); add("clear20", lo[yr >= 2020], la[yr >= 2020], ar[yr >= 2020]); add("encroach", lo[enc], la[enc])
    # mining (model exists for XSA only; elsewhere unmeasured)
    pj, gj = ROOT / "data/eval/xsa_mining/prediction.json", ROOT / "data/eval/xsa_mining/prediction.geojson"
    if AOI == "XSA_Study_Area" and pj.exists() and gj.exists():
        pred = json.load(open(pj))
        for key, pts in (("mine_rep", [(a["lon"], a["lat"]) for a in pred.get("anchors", []) if a.get("lat") is not None]),
                         ("mine_cand", [(c["lon"], c["lat"]) for c in pred.get("candidates", [])]),
                         ("mine_watch", [(w["lon"], w["lat"]) for w in pred.get("abandoned_village_gold_watchlist", {}).get("places", [])]),
                         ("mine_top05", [tuple(f["geometry"]["coordinates"][:2]) for f in json.load(open(gj))["features"] if f["properties"].get("tier") == "top05"])):
            if pts: add(key, *map(np.array, zip(*pts)))
        G.mining_note = f"model composite skill: top-5% cells capture {pred['composite_skill'][0]['capture']:.0%} of known workings (lift {pred['composite_skill'][0]['lift']}); imagery targets, not mines"
        G.mining_measured = True
    else: G.mining_note = "mining: unmeasured (no detection model for this area)"; G.mining_measured = False
    # cropland (GLAD 30 m clips) → fraction per cell
    C["crop03"][:] = C["crop19"][:] = np.nan
    try:
        import rasterio
        from rasterio.warp import reproject, Resampling
        for ep, key in ((2003, "crop03"), (2019, "crop19")):
            p = ROOT / "data/cropland/clips" / f"{AOI}_{ep}.tif"
            if not p.exists(): continue
            with rasterio.open(p) as src:
                dec = 8; arr = (src.read(1, out_shape=(src.height//dec, src.width//dec), resampling=Resampling.nearest) > 0).astype(np.float32)
                str_ = src.transform * rasterio.Affine.scale(src.width/arr.shape[1], src.height/arr.shape[0])
                dst = np.full((G.h, G.w), np.nan, np.float32)
                reproject(arr, dst, src_transform=str_, src_crs=src.crs, dst_transform=G.tr, dst_crs=CEA, resampling=Resampling.average, dst_nodata=np.nan)
            C[key] = dst.astype(np.float64)
        C["cropn"] = (~np.isnan(C["crop19"])).astype(np.float64)
    except ImportError: pass
    # geology: rock unit per cell + summed commodity affinity weight (a prior, not a detection)
    GU = getattr(G, "geo_units", None) or []
    if GU:
        C["geo_id"] = G.rasterize([(transform(FWD, g), i+1) for i, (g, *_) in enumerate(GU)]).astype(np.float64)
        aff = np.zeros(len(GU)+1)
        for i, (g, code, name, grp, comm, af) in enumerate(GU): aff[i+1] = sum(a.get("weight", 0) for a in af)
        C["geo_aff"] = aff[C["geo_id"].astype(int)] * G.mask
        G.geo_meta = {i+1: dict(code=code, name=name, group=grp, commodities=comm) for i, (g, code, name, grp, comm, af) in enumerate(GU)}
    else: C["geo_id"] = Z(); C["geo_aff"] = Z(); G.geo_meta = {}
    G.C = C; log("evidence rasters: " + ", ".join(f"{k} {np.nansum(v):,.0f}" for k, v in C.items() if k not in ("crop03", "crop19", "cropn")))
    return C

def attributes(con, G, lab, surf, feats, names, ncomm, refs, ctry, park, thresholds, light=False, freeze=None):
    """Measure every unit of a label image. freeze: thresholds dict from an earlier call whose relative
    thresholds (corridor quantile) must be reused, so a merged unit is judged by the same bar as a fine one."""
    from scipy import ndimage
    C = cells(con, G)
    ids = [int(i) for i in np.unique(lab) if i > 0]; idx = {u: k for k, u in enumerate(ids)}; N = len(ids)
    ML = lab.max()+1; cnt = np.bincount(lab.ravel(), minlength=ML)
    def agg(key, nan=False):
        v = C[key]; m = (lab > 0) & ~np.isnan(v) if nan else (lab > 0)
        return np.bincount(lab[m], weights=v[m], minlength=ML)
    A = {k: agg(k) for k in C if k not in ("crop03", "crop19", "cropn", "geo_id")}
    geo_mix = {}
    if G.geo_meta:
        gid = C["geo_id"].astype(int); m_ = lab > 0
        pair = np.bincount((lab[m_].astype(np.int64)*(len(G.geo_meta)+1) + gid[m_]), minlength=ML*(len(G.geo_meta)+1)).reshape(ML, -1)
        for u in ids:
            row = pair[u]; tot_ = row.sum()
            geo_mix[u] = [(G.geo_meta[i]["code"], G.geo_meta[i]["name"][:40], round(100*row[i]/tot_), G.geo_meta[i]["commodities"]) for i in np.argsort(-row)[:3] if i > 0 and row[i] > 0.05*tot_]
    A["crop03"], A["crop19"] = agg("crop03", True), agg("crop19", True); A["cropn"] = np.bincount(lab[(lab > 0) & ~np.isnan(C["crop19"])], minlength=ML)
    # fronts: a front counts once per unit it touches
    fr, fth, flg = np.zeros(ML), np.zeros(ML), np.zeros(ML); dirs = defaultdict(Counter); flat = lab.ravel()
    for cl, th, lg, dr in G.fronts:
        us = np.unique(flat[cl]); us = us[us > 0]
        fr[us] += 1; fth[us] += th; flg[us] += lg
        if dr:
            for u in us: dirs[int(u)][dr] += 1
    # towns
    towns = defaultdict(list)
    OT = osm_towns(con); ottree = STRtree([Point(lo, la) for _, lo, la, _ in OT])
    def town_name(lon, lat, fallback):
        best, bd = None, 99.0
        for j in ottree.query(Point(lon, lat).buffer(0.08)):
            raw = dkm((lon, lat), OT[j][1:3]); t_ = OT[j][3]
            if raw > TOWN_REACH_KM.get(t_, 8.0): continue
            d = raw + TOWN_PENALTY_KM.get(t_, 0.0)
            if d < bd: best, bd = OT[j], d
        # `nearest_place` in park_settlements can be 100+ km off (the 24,018-person "Koko" is Raga town) — never trust it silently
        return f"{best[0]}" if best else f"{fallback}* (nearest_place, unverified)"
    for lat, lon, pop, cls, per, place, *_ in G.settlements:
        if (pop or 0) < 1000: continue
        r, c = G.rc(lon, lat)
        if 0 <= r < G.h and 0 <= c < G.w and lab[r, c] > 0: towns[int(lab[r, c])].append((town_name(lon, lat, place), pop))
    polys = {} if light else vectorize(G, lab)
    def edge_legibility(m):
        e = m & ~ndimage.binary_erosion(m, border_value=0)
        return round(float(surf[e].mean()), 2) if e.any() else None
    cmask = {iso: G.rasterize([(transform(FWD, g), 1)]) for iso, g in ctry.items()}
    rmask = {nm: G.rasterize([(transform(FWD, g), 1)]) for nm, g in refs.items() if g.geom_type != "Point"}
    pk = ndimage.distance_transform_edt(G.rasterize([(transform(FWD, park), 1)]) == 0) * G.res/1000 if park is not None else None
    areas = cnt*G.cell_km2(); dens = np.divide(flg*10, np.sqrt(areas), out=np.zeros(ML), where=areas > 0)   # long fronts per 10 km of unit width (scale-free)
    if freeze and "_corridor_long_fronts_abs" in freeze: thresholds["_corridor_long_fronts_abs"] = freeze["_corridor_long_fronts_abs"]
    else:
        big = np.array([u for u in ids if areas[u] > 100])
        thresholds["_corridor_long_fronts_abs"] = float(np.quantile(dens[big], thresholds["corridor_q"])) if len(big) else 1e9
    U = []
    for u in ids:
        m = lab == u; area = areas[u]; yrs = 2
        d = dict(uid=u, seed=names.get(u, "?"), seed_kind="community" if u <= ncomm else "empty-land",
                 area_km2=round(area, 1), area_ha=round(area*100),
                 country=max(cmask, key=lambda iso: cmask[iso][m].sum()) if cmask else "?",
                 in_zones=[nm for nm, r in rmask.items() if r[m].sum() > 0.5*cnt[u]],
                 overlaps=[f"{nm} {100*r[m].sum()/cnt[u]:.0f}%" for nm, r in rmask.items() if r[m].sum() > 0.1*cnt[u]],
                 km_to_park=round(float(pk[m].min()), 1) if pk is not None else None,
                 clusters=int(A["clusters"][u]), population_est=int(A["pop"][u]), pop_per_km2=round(A["pop"][u]/area, 3),
                 osm_places=int(A["osm_places"][u]),
                 new_since_2015=int(A["new15"][u]), camps=int(A["camps"][u]),
                 built_km2=round(A["built"][u], 2), built_2000_km2=round(A["built00"][u], 2), built_2015_km2=round(A["built15"][u], 2),
                 towns=[f"{p} {n:,}" for p, n in sorted(towns[u], key=lambda t: -t[1])[:4]],
                 fire_det_2024_25=int(A["fire"][u]), fire_per_1000km2_yr=round(A["fire"][u]/yrs/area*1000),
                 fire_nov_feb_share=round(A["fire_nf"][u]/A["fire"][u], 2) if A["fire"][u] else None,
                 fronts=int(fr[u]), fronts_per_1000km2=round(fr[u]/area*1000, 1),
                 fronts_transhumance_pct=round(100*fth[u]/fr[u]) if fr[u] else None,
                 fronts_long=int(flg[u]), fronts_long_per_1000km2=round(flg[u]/area*1000, 2), long_front_intensity=round(dens[u], 2),
                 front_dirs=dict(dirs[u].most_common(3)), front_dirs_all=dict(dirs[u]),
                 clearing_events=int(A["clear_ev"][u]), clearing_km2=round(A["clear"][u], 2), clearing_since_2020_km2=round(A["clear20"][u], 2), clearing_encroach_slash=int(A["encroach"][u]),
                 mine_reported=int(A["mine_rep"][u]), mine_candidates=int(A["mine_cand"][u]), mine_top05_cells=int(A["mine_top05"][u]), mine_watchlist=int(A["mine_watch"][u]),
                 mining_measured=G.mining_measured,
                 geology=[f"{c} {n} {p}%" + (f" [{','.join(cm)}]" if cm else "") for c, n, p, cm in geo_mix.get(u, [])],
                 geo_affinity_mean=round(A["geo_aff"][u]/cnt[u], 2) if G.geo_meta and cnt[u] else None,   # mean commodity-affinity weight of the rock (prior; skill measured only for CAR junctions)
                 cropland_2003_pct=round(100*A["crop03"][u]/A["cropn"][u], 2) if A["cropn"][u] else None,
                 cropland_2019_pct=round(100*A["crop19"][u]/A["cropn"][u], 2) if A["cropn"][u] else None,
                 boundary_legibility=edge_legibility(m))
        d["built_growth_x"] = round(d["built_km2"]/d["built_2000_km2"], 1) if d["built_2000_km2"] > 0.01 else None
        d["cls"] = classify(d, thresholds)
        if light: U.append(d); continue
        parts, un, tot = describe_mask(G, m, feats)
        d.update(boundary=parts, perimeter_km=round(tot), unattributed_pct=round(100*un/tot) if tot else None)
        U.append(d)
    return U, polys, G.mining_note

AXIS = {"N": 0, "S": 0, "NE": 1, "SW": 1, "E": 2, "W": 2, "SE": 3, "NW": 3}
def axis_coherence(dirs):
    """Share of fronts moving along the modal axis (N-S, NE-SW, E-W, SE-NW). A migration corridor is
    a line the herds walk both ways; a burnt landscape has fronts going everywhere."""
    n = sum(dirs.values())
    if not n: return 0.0, None
    ax = Counter()
    for d, k in dirs.items():
        if d in AXIS: ax[AXIS[d]] += k
    a, k = ax.most_common(1)[0]
    return round(k/n, 2), ["N–S", "NE–SW", "E–W", "SE–NW"][a]

def classify(d, T):
    """One rule for the whole area, in this order:
    corridor  — long (≥150 km) transhumance-front density in the top quantile of the area's units, fronts
                ≥ corridor_th_pct transhumance-typed, ≥ corridor_axis_coh of them along one axis, few people.
                Judged first: an empty unit the herds cross every year is a corridor, not a core — that is
                Chinko's own lesson.
    core      — nobody (≤ core_pop_km2), no fields, no clearing since 2020, no reported workings.
    wilderness — sparse people, negligible fields.
    community — the rest: settled, farmed, or mining-exposed.
    Every rule fired or missed is recorded in d['rationale'] with the number and the threshold."""
    crop = d["cropland_2019_pct"] or 0
    coh, axis = axis_coherence(d.get("front_dirs_all") or d.get("front_dirs") or {})
    d["front_axis_coherence"], d["front_axis"] = coh, axis
    R = []
    def test(name, val, op, thr):
        ok = (val >= thr) if op == ">=" else (val <= thr)
        R.append(f"{name} {val} {op} {thr}: {'yes' if ok else 'no'}"); return ok
    corr = (test("long fronts per 10 km width", d["long_front_intensity"], ">=", round(T["_corridor_long_fronts_abs"], 2)) &
            test("transhumance share %", d["fronts_transhumance_pct"] or 0, ">=", T["corridor_th_pct"]) &
            test("axis coherence (0.25 = random)", coh, ">=", T.get("corridor_axis_coh", 0.4)) &
            test("people/km2", d["pop_per_km2"], "<=", T["corridor_pop_km2"]))
    if corr: d["rationale"] = ["corridor"] + R; return "corridor"
    R.append("— not a corridor; core?")
    core = (test("people/km2", d["pop_per_km2"], "<=", T["core_pop_km2"]) & test("cropland 2019 %", crop, "<=", T["core_crop_pct"]) &
            test("clearing since 2020 km2/1000km2", round(1000*d["clearing_since_2020_km2"]/d["area_km2"], 3), "<=", T["core_clear_km2"]) & test("reported workings", d["mine_reported"], "<=", 0))
    if core: d["rationale"] = ["core"] + R; return "core"
    R.append("— not core; wilderness?")
    wild = test("people/km2", d["pop_per_km2"], "<=", T["wild_pop_km2"]) & test("cropland 2019 %", crop, "<=", T["wild_crop_pct"])
    if wild: d["rationale"] = ["wilderness"] + R; return "wilderness"
    d["rationale"] = ["community (settled / farmed / mining-exposed — none of the above held)"] + R; return "community"

def vectorize(G, lab):
    from rasterio import features
    out = {}
    for geom, v in features.shapes(lab.astype(np.int32), mask=lab > 0, transform=G.tr):
        g = transform(INV, shape(geom)).buffer(0)
        out[int(v)] = unary_union([out[int(v)], g]) if int(v) in out else g
    for k, g in out.items():
        # keep EVERY part (a corridor band or a rim conservancy can be multi-part; dropping the small parts lost 13% of the
        # corridor silently — invariant 8: serve whole or say you truncated). Holes are filled: a unit is its outline.
        parts = [Polygon(p.exterior).simplify(0.004) for p in (g.geoms if isinstance(g, MultiPolygon) else [g]) if p.area > 0]
        out[k] = unary_union(parts) if len(parts) > 1 else parts[0]
    return out

def describe_boundary(poly, feats, ftree, step_km=2.0):
    m = transform(FWD, poly.exterior); tot = m.length/1000; n = max(6, int(tot/step_km)); c = poly.centroid
    by = Counter(); side = defaultdict(Counter); un = 0.0
    for k in range(n):
        p = transform(INV, m.interpolate(k/n, normalized=True)); best, bd = None, 2.5
        for j in ftree.query(p.buffer(0.03)):
            g = feats[j][0]
            if g.is_empty: continue
            d = dkm((p.x, p.y), g.interpolate(g.project(p)).coords[0])
            if d < bd: best, bd = j, d
        ang = math.degrees(math.atan2(p.y-c.y, (p.x-c.x)*math.cos(math.radians(c.y)))) % 360
        s = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"][int(((ang+22.5) % 360)//45)]
        if best is None: un += tot/n; continue
        by[feats[best][1]] += tot/n; side[feats[best][1]][s] += tot/n
    return [f"{nm} on the {'/'.join(k for k, _ in side[nm].most_common(2))} ({k:.0f} km)" for nm, k in by.most_common(6)], un, tot

# ============================================================ corridor from the fronts themselves
def corridor_axis(G, refs, popkm2, width_km=12, closed=(), smooth=3, expo=1.5, pop_w=2.0, rng=None):
    """The herd corridor is not drawn, it is walked: least-cost path over the long-transhumance-front density
    (smoothed), penalised by people, from the Radom side to the Garamba side. Returns (band mask, axis rc list, stats)."""
    from scipy import ndimage
    from skimage.graph import route_through_array
    dens = ndimage.gaussian_filter(G.longdens, smooth)
    dn = dens/(np.percentile(dens[G.mask], 99) or 1)
    cost = 1.0/(0.02 + np.clip(dn, 0, 1))**expo * (1 + popkm2/pop_w)
    if rng is not None: cost *= rng.uniform(0.85, 1.15, cost.shape)   # bootstrap: jitter the cost field
    for sub in closed:   # land the plan closes to herds: the path must go round it
        g = next((g for k, g in refs.items() if sub in k and g.geom_type != "Point"), None)
        if g is not None: cost[G.rasterize([(transform(FWD, g), 1)]).astype(bool)] *= 50
    cost[~G.mask] = 1e6
    def anchor(sub):
        g = next((g for k, g in refs.items() if sub in k), None)
        if g is None: return None
        r = G.rasterize([(transform(FWD, g), 1)]).astype(bool) & G.mask
        if not r.any(): return None
        rr, cc = np.nonzero(r); i = np.argmin(cost[rr, cc]); return int(rr[i]), int(cc[i])
    a, b = anchor("Radom"), anchor("Garamba (National")
    if a is None or b is None:
        # generic AOI: anchors are where the long fronts themselves enter and leave — the two densest
        # rim segments of long-front density on opposite sides of the area
        rim = G.mask & ~ndimage.binary_erosion(G.mask, iterations=3)
        rr, cc = np.nonzero(rim); v = dens[rr, cc]
        if not len(rr) or v.max() <= 0: return None, [], dict(note="no long fronts at the rim")
        i = np.argmax(v); a = (int(rr[i]), int(cc[i]))
        far = np.hypot(rr - a[0], cc - a[1]) > 0.5*max(G.h, G.w)
        if not far.any(): return None, [], dict(note="rim too small")
        j = np.flatnonzero(far)[np.argmax(v[far])]; b = (int(rr[j]), int(cc[j]))
    path, tot = route_through_array(cost, a, b, fully_connected=True, geometric=True)
    axis = np.zeros((G.h, G.w), bool)
    for r, c in path: axis[r, c] = True
    band = ndimage.binary_dilation(axis, iterations=int(width_km*1000/G.res)) & G.mask
    return band, path, dict(cells=int(band.sum()), km2=round(band.sum()*G.cell_km2()), axis_km=round(len(path)*G.res/1000*1.2))

# ============================================================ gazetteer: what the 1930s sheets say about a piece of land
_GAZ = None
def _gaz_load():
    """All named/annotated sheet content in the AOI as point lists, once."""
    global _GAZ
    if _GAZ is not None: return _GAZ
    h = sqlite3.connect(HDB); x0, y0, x1, y1 = aoi_geom(sqlite3.connect(DB)).bounds
    q = lambda sql: h.execute(sql, (x0, x1, y0, y1)).fetchall()
    G_ = dict(
        peaks=q("SELECT name, lon, lat FROM symbols WHERE category IN ('peak') AND name IS NOT NULL AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?"),
        trig=q("SELECT COALESCE(name,''), lon, lat FROM symbols WHERE category='trig_point' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?"),
        built=q("SELECT category || ': ' || COALESCE(name, descr, ''), lon, lat FROM symbols WHERE category IN ('fort','church','station','enclosure','ruin','grave','landmark') AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?"),
        terrain=q("SELECT text, lon, lat FROM labels_dedup WHERE category='terrain' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?"),
        water=q("SELECT text, lon, lat FROM labels_dedup WHERE category='water' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?"),
        veg=q("SELECT text, lon, lat FROM labels_dedup WHERE category='vegetation' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?"),
        places=q("SELECT text, lon, lat FROM labels_dedup WHERE category='place' AND length(text)>=3 AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?"),
        boundary=q("SELECT text, lon, lat FROM labels_dedup WHERE category='boundary' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?"),
        notes=h.execute("SELECT COALESCE(note_topic,'')||' | '||text, lon, lat FROM labels_dedup WHERE category='note' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?", (x0, x1, y0, y1)).fetchall(),
    )
    _GAZ = G_; return G_

def gazetteer(G, m, cap=40):
    """Sheet content falling inside raster mask m, grouped; every list capped so a prompt stays small.
    A name in CAPITALS on these sheets is a district town or a tribe/area name; 'J.' = Jebel (hill),
    'K.' = Khor (seasonal stream), 'R.' = river, 'Qoz' = sand ridge, 'Hagar' = rock, 'W.S.' = wireless station."""
    out = {}
    for k, rows in _gaz_load().items():
        if not rows: out[k] = []; continue
        rr, cc = G.rc_arr([r[1] for r in rows], [r[2] for r in rows]); ok = G.inside(rr, cc)
        hit = np.zeros(len(rows), bool); hit[ok] = m[rr[ok], cc[ok]]
        vals = [re.sub(r"\s+", " ", str(rows[i][0])).strip() for i in np.flatnonzero(hit)]
        vals = [v for v in vals if v and v not in ("junk",)]
        c = Counter(vals); out[k] = [f"{v} (x{n})" if n > 1 else v for v, n in c.most_common(cap)]
    out["counts"] = {k: len(v) for k, v in out.items() if isinstance(v, list)}
    return out

# ============================================================ conservancies
def conservancies(con, st, refs, ctry, T, exclude, rim_km, reach_km, min_ha, target_ha, min_pop, country="SSD", mining=True):
    """Community conservancy candidates from the FINE units (pass 1, >= min_ha, edges on legible features).
    used land = fine units holding a settlement cluster or within reach_km of one, outside the excluded
    polygons (the park, gazetted PAs), within rim_km of the park. Neighbouring used units are agglomerated
    across their weakest shared edge (a river between two villages keeps them apart; open bush joins them)
    until target_ha. Each conservancy is then measured like any unit, and described by its edges."""
    from scipy import ndimage
    G, lab1, surf, feats = st["G"], st["lab1"], st["surf"], st["feats"]
    park = next((g for k, g in refs.items() if PARK_KEY in k), None)
    ex = [e.strip().lower() for e in exclude.split(",") if e.strip()]
    exm = np.zeros((G.h, G.w), bool)
    for k, g in refs.items():
        if g.geom_type != "Point" and any(e in k.lower() for e in ex): exm |= G.rasterize([(transform(FWD, g), 1)]).astype(bool)
    rows = con.execute("SELECT lat, lon, population_est FROM park_settlements WHERE park_id=? AND population_est>=20", (AOI,)).fetchall()
    sm = np.zeros((G.h, G.w), bool)
    r, c = G.rc_arr([x[1] for x in rows], [x[0] for x in rows]); ok = G.inside(r, c); sm[r[ok], c[ok]] = True
    used = ndimage.distance_transform_edt(~sm)*G.res/1000 <= reach_km
    if rim_km and park is not None:
        pm = G.rasterize([(transform(FWD, park), 1)]).astype(bool)
        used &= ndimage.distance_transform_edt(~pm)*G.res/1000 <= rim_km
    if country and country != "ALL" and country in ctry:
        used &= G.rasterize([(transform(FWD, ctry[country]), 1)]).astype(bool)
    mined = np.zeros((G.h, G.w), bool)
    if mining:
        # the model's top-5% cells + candidates + reported workings: what a licence would be applied for
        pj = ROOT / "data/eval/xsa_mining/prediction.geojson"
        if AOI == "XSA_Study_Area" and pj.exists():
            pts = []
            for f in json.load(open(pj))["features"]:
                pr = f["properties"]; g = shape(f["geometry"]).centroid
                if pr.get("top05") or pr.get("candidate") or pr.get("reported") or pr.get("anchor"): pts.append((g.x, g.y))
            if pts:
                r_, c_ = G.rc_arr([p[0] for p in pts], [p[1] for p in pts]); ok = G.inside(r_, c_); mined[r_[ok], c_[ok]] = True
                mined = ndimage.binary_dilation(mined, iterations=int(6000/G.res))
        used |= mined & G.mask
    used &= ~exm & G.mask
    # a FINE mesh of its own: every village cluster is a seed, plus a 10 km lattice, so the watershed
    # falls on the nearest legible feature between neighbouring villages (not on a 60 km empty-land cell)
    from skimage.segmentation import watershed
    markers = np.zeros((G.h, G.w), np.int32); k = 0
    for lat, lon, pop in sorted(rows, key=lambda x: -x[2]):
        r_, c_ = G.rc(lon, lat)
        if 0 <= r_ < G.h and 0 <= c_ < G.w and used[r_, c_] and not markers[max(0, r_-2):r_+3, max(0, c_-2):c_+3].any():
            k += 1; markers[r_, c_] = k
    step = int(10000/G.res)
    for r_ in range(step//2, G.h, step):
        for c_ in range(step//2, G.w, step):
            if G.mask[r_, c_] and not markers[max(0, r_-2):r_+3, max(0, c_-2):c_+3].any(): k += 1; markers[r_, c_] = k
    fine = watershed(surf, markers=markers, mask=G.mask & ~exm)
    fine = merge_units(fine, surf, G, min_ha)
    cnt = np.bincount(fine.ravel()); hit = np.bincount(fine[used], minlength=len(cnt))
    cand = {int(u) for u in np.flatnonzero(hit > 0.3*cnt) if u > 0}          # a fine unit is 'used' if >30% of it is
    lab = np.where(np.isin(fine, list(cand)), fine, 0)
    log(f"conservancies: {len(cand)} used fine units (of {len(cnt)-1}) within {rim_km} km of the park")
    cell_ha = G.cell_km2()*100
    # agglomerate: repeatedly merge the pair of adjacent candidate units with the weakest edge, if the union <= target
    while True:
        E = edges(lab, surf); best = None
        cnt = np.bincount(lab.ravel(), minlength=lab.max()+1)
        for (a_, b_), (n, sm_) in E.items():
            if (cnt[a_]+cnt[b_])*cell_ha > target_ha: continue
            key = sm_/n - 0.02*min(n, 20)     # weak and long edges first
            if best is None or key < best[0]: best = (key, a_, b_)
        if best is None: break
        lab[lab == best[2]] = best[1]
    ids = [int(u) for u in np.unique(lab) if u > 0]
    # measure like any unit
    names = {u: "" for u in ids}
    U, polys, _ = attributes(con, G, lab, surf, feats, names, 0, refs, ctry, park, T, freeze=T)
    for u in U:
        u["seed"] = (u["towns"][0].split(" ")[0] if u["towns"] else "")
        u["seed_kind"] = "conservancy"
    U = [u for u in U if u["area_ha"] >= min_ha and (u["population_est"] >= min_pop or (mining and (u["mine_top05_cells"] + u["mine_candidates"] + u["mine_reported"]) > 0))]
    for u in U:
        m = lab == u["uid"]
        u["gazetteer"] = gazetteer(G, m)
        u["mining_exposed_pct"] = round(100*float((mined & m).sum()/max(m.sum(), 1)))
        u["reason"] = ("people+mining" if u["population_est"] >= min_pop and u["mining_exposed_pct"] > 0 else "people" if u["population_est"] >= min_pop else "mining exposure")
    # name from the biggest cluster's nearest OSM/1930s place
    OT = osm_towns(con); ottree = STRtree([Point(lo, la) for _, lo, la, _ in OT])
    for u in U:
        m = lab == u["uid"]; rr, cc = np.nonzero(m)
        sub = [x for x in rows if G.inside(*[np.array([v]) for v in G.rc(x[1], x[0])])[0] and m[G.rc(x[1], x[0])]]
        if sub:
            big = max(sub, key=lambda x: x[2]); bestn, bd = None, 12
            for j in ottree.query(Point(big[1], big[0]).buffer(0.12)):
                d = dkm((big[1], big[0]), OT[j][1:3])
                if d < bd: bestn, bd = OT[j][0], d
            u["seed"] = bestn or u["seed"] or f"unit {u['uid']}"
        if not sub or u["seed"].startswith("unit"):
            gz = u["gazetteer"]; nm = (gz["peaks"] or gz["places"] or gz["water"] or [f"unit {u['uid']}"])[0]
            u["seed"] = re.sub(r" \(x\d+\)$", "", nm)
        # compass sector of the park
        if park is not None:
            pc = park.centroid; c0 = polys[u["uid"]].centroid
            ang = math.degrees(math.atan2(c0.y-pc.y, (c0.x-pc.x)*math.cos(math.radians(pc.y)))) % 360
            u["park_sector"] = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"][int(((ang+22.5) % 360)//45)]
    U.sort(key=lambda u: -u["population_est"])
    return U, polys, lab

# ============================================================ optimiser: grow the best area of a class from fine units, with bootstrap support
class UnitTable:
    """Per-fine-unit sums of every evidence raster + sparse front×unit incidence, so any set of fine units
    is measured in O(members) and classified with the same classify(). The engine under optimize/bootstrap."""
    def __init__(self, con, G, lab1, surf, T):
        import scipy.sparse as sp
        C = cells(con, G); self.G, self.lab, self.T = G, lab1, T
        ML = lab1.max()+1; self.ML = ML; self.cnt = np.bincount(lab1.ravel(), minlength=ML)
        self.S = {k: np.bincount(lab1[(lab1 > 0) & ~np.isnan(C[k])], weights=C[k][(lab1 > 0) & ~np.isnan(C[k])], minlength=ML) for k in C if k != "geo_id"}
        self.S["cropn"] = np.bincount(lab1[(lab1 > 0) & ~np.isnan(C["crop19"])], minlength=ML).astype(float)
        flat = lab1.ravel(); rows, cols = [], []; self.fmeta = []
        for j, (cl, th, lg, dr) in enumerate(G.fronts):
            us = np.unique(flat[cl]); us = us[us > 0]; rows += [j]*len(us); cols += us.tolist(); self.fmeta.append((th, lg, dr))
        self.M = sp.csc_matrix((np.ones(len(rows), np.int8), (rows, cols)), shape=(len(G.fronts), ML))
        self.fth = np.array([m[0] for m in self.fmeta], bool); self.flg = np.array([m[1] for m in self.fmeta], bool)
        self.fdir = np.array([m[2] or "" for m in self.fmeta])
        E = edges(lab1, surf); self.nb = defaultdict(dict)
        for (a, b), (n, sm) in E.items(): self.nb[a][b] = (n, sm); self.nb[b][a] = (n, sm)
        self.edge_len = defaultdict(float)
        for (a, b), (n, sm) in E.items(): self.edge_len[a] += n; self.edge_len[b] += n
        # unit-level classes for seeding
        self.unit_cls = {}
        for u in range(1, ML):
            if self.cnt[u]: self.unit_cls[u] = classify(self.measure([u]), T)
    def measure(self, members):
        """The same dict attributes() builds, for the union of fine units `members`."""
        m = np.fromiter(members, int); area = self.cnt[m].sum()*self.G.cell_km2(); S = {k: v[m].sum() for k, v in self.S.items()}
        touch = np.asarray(self.M[:, m].sum(axis=1)).ravel() > 0
        fr = int(touch.sum()); fth = int((touch & self.fth).sum()); flg = int((touch & self.flg).sum())
        dirs = Counter(self.fdir[touch & (self.fdir != "")].tolist())
        d = dict(area_km2=round(area, 1), area_ha=round(area*100), clusters=int(S["clusters"]), population_est=int(S["pop"]), pop_per_km2=round(S["pop"]/area, 3) if area else 0,
                 osm_places=int(S["osm_places"]), new_since_2015=int(S["new15"]), camps=int(S["camps"]),
                 fire_det_2024_25=int(S["fire"]), fire_per_1000km2_yr=round(S["fire"]/2/area*1000) if area else 0,
                 fronts=fr, fronts_transhumance_pct=round(100*fth/fr) if fr else None, fronts_long=flg, fronts_long_per_1000km2=round(flg/area*1000, 2) if area else 0, long_front_intensity=round(flg*10/math.sqrt(area), 2) if area else 0,
                 front_dirs=dict(dirs.most_common(3)), front_dirs_all=dict(dirs),
                 clearing_since_2020_km2=round(S["clear20"], 2), clearing_encroach_slash=int(S["encroach"]), clearing_km2=round(S["clear"], 2),
                 mine_reported=int(S["mine_rep"]), mine_candidates=int(S["mine_cand"]), mine_top05_cells=int(S["mine_top05"]),
                 cropland_2019_pct=round(100*S["crop19"]/S["cropn"], 2) if S["cropn"] else None, cropland_2003_pct=round(100*S["crop03"]/S["cropn"], 2) if S["cropn"] else None,
                 geo_affinity_mean=round(S["geo_aff"]/self.cnt[m].sum(), 2) if "geo_aff" in S and self.cnt[m].sum() else None)
        return d
    def legibility_of(self, members):
        """Mean legibility of the OUTER edge of the union (inner edges between members do not count)."""
        ms = set(members); n = sm = 0.0
        for u in members:
            for b, (k, s_) in self.nb[u].items():
                if b not in ms: n += k; sm += s_
        return (sm/n if n else 0.0), n

OBJ_TARGET_HA = {"community": 150_000}
def compactness(d, P, cell_km):
    """Polsby–Popper-like: 1 for a disc, → 0 for a tendril. P = outer edge in cells."""
    per = P*cell_km; return min(1.0, 4*math.pi*d["area_km2"]/max(per*per, 1e-9))

OBJECTIVES = {
    # value of a candidate (measured dict d, outer legibility L, perimeter cells P) — bigger is better.
    # Every objective is area-like × fence quality × shape, so growth stops where the next unit would cost more
    # unfenced perimeter than it adds; the max-ha cap is a safety, not the stopping rule.
    "core":       lambda d, L, P, c: d["area_km2"] * (0.3 + L) * (0.3 + c)**0.5 * (1 + d["fronts_long_per_1000km2"]/50),   # big, fenced, compact, and on the herds' way
    # community: people served × what there is to protect (frontier clearing, licence exposure), fenced and compact,
    # with a GOVERNANCE size term: a conservancy is one community's institution (Wildlife Act s.14) — past ~target ha
    # the value of another village falls off (exp(-(ha/target)^2)), so growth stops at a size a committee can run.
    "community":  lambda d, L, P, c: (d["population_est"] + 200*d["clusters"] + 50*d["osm_places"]) * (0.3 + L) * (0.3 + c)**0.5 * (1 + d["clearing_encroach_slash"]/10 + d["mine_top05_cells"]/10 + (d.get("geo_affinity_mean") or 0)/3) * math.exp(-(d["area_ha"]/OBJ_TARGET_HA["community"])**2),
    "corridor":   lambda d, L, P, c: d["fronts_long"] * (0.3 + L) * (d.get("front_axis_coherence") or 0.5),
    "wilderness": lambda d, L, P, c: d["area_km2"] * (0.3 + L) * (0.3 + c)**0.5,
}
def objective(want, UT, d, L, P): return OBJECTIVES[want](d, L, P, compactness(d, P, UT.G.res/1000))

def grow(UT, seed, want, T, max_ha, rng=None, forbid=frozenset()):
    """Greedy region growing over the fine units: from `seed`, repeatedly add the neighbour whose addition
    keeps the WHOLE candidate in class `want` (classify on the union) and best raises the objective.
    Prefers crossing weak edges (nothing on the ground) and stops at strong ones unless the gain is large.
    Returns (members, measured dict, objective)."""
    members = [seed]; ms = {seed}; d = UT.measure(members); d["cls"] = classify(d, T)
    if d["cls"] != want: return None
    L, P = UT.legibility_of(members); best = objective(want, UT, d, L, P)
    while True:
        cand = {}
        for u in members:
            for b, (n, sm) in UT.nb[u].items():
                if b in ms or b in forbid: continue
                cand[b] = cand.get(b, 0) + n
        if not cand: break
        pick = None
        for b in cand:
            if (UT.cnt[b] + sum(UT.cnt[m] for m in members))*UT.G.cell_km2()*100 > max_ha: continue
            d2 = UT.measure(members + [b]); d2["cls"] = classify(d2, T)
            if d2["cls"] != want: continue
            L2, P2 = UT.legibility_of(members + [b]); v = objective(want, UT, d2, L2, P2)
            if rng is not None: v *= rng.uniform(0.9, 1.1)          # bootstrap: jitter the greedy path
            if v > best and (pick is None or v > pick[0]): pick = (v, b, d2)
        if pick is None: break
        best, b, d = pick; members.append(b); ms.add(b)
    return members, d, best

def bootstrap(UT, want, T, max_ha, draws, perturb, seeds, forbid=frozenset(), rng_seed=0):
    """Support for every fine unit: the share of perturbed runs (thresholds ±perturb, jittered greedy path,
    shuffled seed order) in which it ends up inside the grown area of class `want`. Support ≥ 0.5 is the
    proposal; 0.25–0.5 is contested land to walk with the community; the run-to-run spread IS the uncertainty."""
    rng = np.random.default_rng(rng_seed); sup = np.zeros(UT.ML); results = []
    keys = [k for k in T if not k.startswith("_") and isinstance(T[k], (int, float))]
    for i in range(draws):
        Ti = dict(T); 
        for k in keys: Ti[k] = T[k]*rng.uniform(1-perturb, 1+perturb)
        Ti["_corridor_long_fronts_abs"] = T["_corridor_long_fronts_abs"]*rng.uniform(1-perturb, 1+perturb)
        order = list(seeds); rng.shuffle(order); taken = set(forbid); best = None
        for sd in order:
            if sd in taken: continue
            g = grow(UT, sd, want, Ti, max_ha, rng, frozenset(taken))
            if g and (best is None or g[2] > best[2]): best = g
        if best:
            sup[best[0]] += 1; results.append(dict(draw=i, n_units=len(best[0]), area_ha=best[1]["area_ha"], objective=round(best[2]), seed=int(order[0])))
    return sup/max(draws, 1), results

# ============================================================ LLM reading of the sheets
LLM_URL = os.environ.get("HISTMAP_LLM_URL", "https://llm.int.exe.xyz/v1/chat/completions")
LLM_MODEL = os.environ.get("HISTMAP_OCR_MODEL", "fireworks/muse-glimmer-30b")
DESCRIBE_SYS = """You are a conservation planner and toponymist working on the Western Equatoria / Western Bahr el Ghazal border of South Sudan.
You read 1930s Sudan Survey 1:250,000 sheet content (Arabic and local-language transcriptions: J. = Jebel hill/mountain, K. = Khor seasonal stream, R. = river, Qoz = sand ridge, Hagar = rock, Bahr = large river, Hafir = dug water tank, W.S. = wireless station, CAPITALS = district town or tribe/area name, '(Deserted)/(Uninhabited)' = empty in the 1930s) together with today's satellite measurements.
Law: South Sudan Wildlife Act 2026 s.9 (corridors), s.14 (community conservancies; s.14(4) community veto), s.15-16 (community scouts), Mining Act s.24/s.27 (no licence in a protected/community conservation area without consent), Land Act 2009 s.66-67 (customary land).
Answer ONLY a JSON object with keys:
 name (a legible local name for the unit, using the sheet names, e.g. 'Jebel X – Khor Y conservancy'),
 name_meaning (what the chosen names mean, EN/AR/local, one line),
 designation (one of: community_conservancy, protected_area_extension, livestock_corridor, no_designation),
 confidence (0-1),
 why (<=3 sentences, cite the measured numbers you used),
 boundary_story (<=2 sentences: which rivers/hills a villager would recognise as the edge, from the boundary list),
 mining (one sentence: does the mining exposure justify a conservancy as a licence veto? use the numbers; note the model's skill is lift 3.25 over 5% of cells — targets, not mines),
 people_land (one sentence: what the 1930s notes say about habitation then vs. GHSL people now),
 verify_on_ground (<=3 short items).
No prose outside the JSON."""

def llm_json(sys_prompt, user, max_tokens=3000):
    import urllib.request
    body = json.dumps({"model": LLM_MODEL, "max_tokens": max_tokens, "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]}).encode()
    for attempt in range(2):
        r = json.load(urllib.request.urlopen(urllib.request.Request(LLM_URL, data=body, headers={"Content-Type": "application/json"}), timeout=600))
        ch = r["choices"][0]; txt = ch["message"].get("content") or ""
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            try: return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
            except Exception: pass
        body = json.dumps({"model": LLM_MODEL, "max_tokens": max_tokens*2, "messages": json.loads(body)["messages"]}).encode()
    return {"error": "no JSON", "raw": txt[:400]}

def describe_unit(u):
    gz = u["gazetteer"]
    facts = dict(area_ha=u["area_ha"], country=u["country"], km_to_proposed_park=u["km_to_park"], sector_of_park=u.get("park_sector"),
                 clusters=u["clusters"], people_GHSL_lower_bound=u["population_est"], people_per_km2=u["pop_per_km2"], new_clusters_since_2015=u["new_since_2015"], towns=u["towns"][:5],
                 fire_detections_2024_25=u["fire_det_2024_25"], fire_nov_feb_share=u["fire_nov_feb_share"], fronts=u["fronts"], fronts_transhumance_pct=u["fronts_transhumance_pct"], fronts_long_ge150km=u["fronts_long"], front_dirs=u["front_dirs"],
                 in_walked_herd_corridor_pct=u.get("in_walked_corridor_pct"), clearing_km2_since_2020=u["clearing_since_2020_km2"], clearing_encroach_slash_events=u["clearing_encroach_slash"],
                 cropland_pct_2003_2019=[u["cropland_2003_pct"], u["cropland_2019_pct"]], mining_reported_workings=u["mine_reported"], mining_candidates=u["mine_candidates"], mining_top5pct_cells=u["mine_top05_cells"], mining_exposed_land_pct=u.get("mining_exposed_pct"),
                 planner_class=u["cls"], boundary=u["boundary"], boundary_unnamed_pct=u["unattributed_pct"], overlaps_hand_drawn_plan=u["overlaps"])
    sheet = {k: gz[k][:25] for k in ("peaks", "trig", "terrain", "water", "veg", "places", "boundary", "notes", "built")}
    return llm_json(DESCRIBE_SYS, "MEASURED TODAY:\n" + json.dumps(facts, ensure_ascii=False) + "\n\n1930s SHEET CONTENT INSIDE THE UNIT:\n" + json.dumps(sheet, ensure_ascii=False))

# ============================================================ validation
def assess(con, G, geoms, refs, ctry, park, T, feats, surf):
    """Measure arbitrary polygons (a park, a WDPA PA, a hand-drawn zone, a proposal) with the SAME evidence
    rasters and the SAME classify() as the mesh units — one label image, one call. Corridor bar frozen from
    the mesh. This is how a proposal is judged by the machinery that judged the land it came from."""
    lab = np.zeros((G.h, G.w), np.int32); names = {}
    for i, (nm, g) in enumerate(geoms, 1):
        m = G.rasterize([(transform(FWD, g), 1)]).astype(bool) & G.mask
        lab[m & (lab == 0)] = i; names[i] = nm
    U, _, _ = attributes(con, G, lab, surf, feats, names, 0, refs, ctry, park, T, freeze=T)
    for u in U: u["name"] = names[u["uid"]]
    return U

def validate(G, lab, U, refs, feats, con=None, ctry=None, park=None, T=None, surf=None):
    byid = {u["uid"]: u for u in U}
    cnt = np.bincount(lab.ravel(), minlength=lab.max()+1)
    L = ["VALIDATION — does the planner, fed only rivers/ridges/borders + measurements, re-find the zones we drew by hand and the gazetted PAs?",
         "  IoU: re-drawn (union of units with >50% of their cells inside) vs reference.  ref-legible: % of the reference's own perimeter within 2.5 km of a nameable feature.",
         "  class mix: what the planner calls the land inside the reference, by area.  Agreement = the reference's intended class dominates.", ""]
    rows = []
    for nm, g in refs.items():
        if g.geom_type == "Point": continue
        r = G.rasterize([(transform(FWD, g), 1)]).astype(bool) & G.mask
        if r.sum()*G.cell_km2() < 20: continue
        inside = np.bincount(lab[r], minlength=lab.max()+1)
        sel = [u for u in np.flatnonzero(inside) if u > 0 and inside[u] > 0.5*cnt[u]]
        red = np.isin(lab, sel) if sel else np.zeros_like(r)
        iou = (red & r).sum()/max((red | r).sum(), 1)
        mix = Counter()
        for u in np.flatnonzero(inside):
            if u > 0: mix[byid[int(u)]["cls"]] += inside[u]
        tot = sum(mix.values()) or 1
        parts, un, ptot = describe_mask(G, r, feats)
        rows.append(dict(name=nm, ref_km2=round(r.sum()*G.cell_km2()), iou=round(iou, 2), n_units=len(sel), redrawn_km2=round(red.sum()*G.cell_km2()),
                         ref_legible_pct=round(100*(1-un/ptot)) if ptot else None, ref_boundary=parts[:4],
                         class_mix={c: round(100*mix[c]/tot) for c in CLASSES if mix[c]},
                         units=[int(u) for u in sel]))
    for r in sorted(rows, key=lambda r: -r["iou"]):
        L.append(r["name"]); L.append(f"  ref {r['ref_km2']:>8,} km2  ref-legible {r['ref_legible_pct']}%  :: " + "; ".join(r["ref_boundary"]))
        L.append(f"  re-drawn {r['redrawn_km2']:>7,} km2 from {r['n_units']} units  IoU {r['iou']:.2f}   planner class mix {r['class_mix']}"); L.append("")
    tot = Counter()
    for u in U: tot[u["cls"]] += u["area_km2"]
    L.append("Whole study area by planner class (km2): " + ", ".join(f"{c} {tot[c]:,.0f}" for c in CLASSES))
    # the references themselves, measured whole with the same rasters and the same rule
    if con is not None:
        geoms = [(nm, g) for nm, g in refs.items() if g.geom_type != "Point"]
        A = assess(con, G, geoms, refs, ctry, park, T, feats, surf)
        L.append(""); L.append("REFERENCES MEASURED WHOLE — the same evidence and the same classify() applied to each drawn polygon as one unit")
        L.append("  (what the planner would call the land as drawn; the rationale lists every test with its number and threshold)")
        for u in sorted(A, key=lambda u: -u["area_km2"]):
            L.append(f"  {u['name'][:58]:<58} {u['area_ha']:>10,} ha → {u['cls']:<10} people {u['population_est']:>7,} ({u['pop_per_km2']}/km2) crop {u['cropland_2019_pct']}% clear20 {u['clearing_since_2020_km2']} km2 "
                     f"fronts {u['fronts']} ({u['fronts_transhumance_pct']}% herd, {u['fronts_long']} long, {u['fronts_long_per_1000km2']}/1000km2, axis {u['front_axis']} {u['front_axis_coherence']}) legible {u['boundary_legibility']} edge-unnamed {u['unattributed_pct']}%")
            L.append("      " + " | ".join(u["rationale"][1:]))
        rows.append(dict(assessed=[{k: v for k, v in u.items() if k not in ("front_dirs_all",)} for u in A]))
    # corridor: the hand-drawn band vs the band the fronts walk
    for band, title in ((getattr(G, "corr_band", None), "as the herds walk TODAY (least-cost over long transhumance fronts, Radom → Garamba, 12 km half-width)"),
                        (getattr(G, "div_band", None), "DIVERTED: same, with the proposed park, Southern NP and the wilderness zones closed to herds")):
      if band is None: continue
      if True:
        L.append(""); L.append("CORRIDOR " + title)
        for nm, g in refs.items():
            if ("pâturage" not in nm and "paturage" not in nm) or g.geom_type == "Point": continue
            r = G.rasterize([(transform(FWD, g), 1)]).astype(bool) & G.mask
            inter = (r & band).sum()
            L.append(f"  {nm[:60]:<60} ref {r.sum()*G.cell_km2():>7,.0f} km2  covered by walked band {100*inter/max(r.sum(),1):3.0f}%  band inside ref {100*inter/max(band.sum(),1):3.0f}%  IoU {inter/max((r|band).sum(),1):.2f}")
        pins = {k: g for k, g in refs.items() if "Corridor" in k and g.geom_type in ("Point", "Polygon")}
        for k, g in pins.items():
            c = g.centroid; rr, cc = G.rc(c.x, c.y)
            from scipy import ndimage
            d = ndimage.distance_transform_edt(~band)[rr, cc]*G.res/1000 if G.inside(np.array([rr]), np.array([cc]))[0] else None
            L.append(f"  gate {k[:50]:<50} {d:.0f} km from walked band" if d is not None else f"  gate {k} outside grid")
        dens_in = G.longdens[band].mean(); dens_out = G.longdens[G.mask & ~band].mean()
        L.append(f"  long-front density inside walked band {dens_in:.2f}/cell vs elsewhere {dens_out:.2f}/cell ({dens_in/max(dens_out,1e-9):.1f}x)")
    return "\n".join(L), rows

# ============================================================ rank / io
def rank(U, want, cls, exclude, country, min_ha, max_km2):
    ex = [e.strip().lower() for e in exclude.split(",") if e.strip()]
    sel = [u for u in U if u["area_ha"] >= min_ha and (not max_km2 or u["area_km2"] <= max_km2) and (not country or u["country"] == country)
           and (not cls or u["cls"] == cls) and not any(any(e in zn.lower() for e in ex) for zn in u["in_zones"])]
    if not sel: return []
    def z(key, lg=True):
        v = np.array([(u.get(key) or 0) for u in sel], float); v = np.log1p(v) if lg else v
        return (v - v.mean())/(v.std() or 1)
    people = z("population_est") + 0.5*z("clusters")
    pressure = z("fronts_long") + z("clearing_since_2020_km2") + z("mine_top05_cells") + z("mine_candidates") + z("new_since_2015") + 0.5*z("fire_det_2024_25")
    rim = -np.array([min((u["km_to_park"] or 999), 150) for u in sel])/50
    legible = np.array([(u["boundary_legibility"] or 0) for u in sel])*2 - np.array([(u["unattributed_pct"] or 0) for u in sel])/25
    s = {"people": people, "pressure": pressure, "rim": rim + 0.5*people, "balanced": people + 0.7*pressure + 0.5*rim}[want] + legible
    for u, v in zip(sel, s): u["score"] = round(float(v), 2)
    return sorted(sel, key=lambda u: -u["score"])

def fmt(u, full=False):
    L = [f"#{u['uid']:<4} {u['cls']:<10} {u['country']} {u['area_ha']:>9,} ha  {u['clusters']:>3} cl {u['population_est']:>8,} ppl ({u['pop_per_km2']}/km2) new15 {u['new_since_2015']:>2} | "
         f"fire/1000km2 {u['fire_per_1000km2_yr']:>6,} fronts {u['fronts']:>4} ({u['fronts_transhumance_pct'] or 0}% herd, {u['fronts_long']} long) | clear20 {u['clearing_since_2020_km2']:>5} km2 crop {u['cropland_2003_pct']}→{u['cropland_2019_pct']}% | mine r/c/t5 {u['mine_reported']}/{u['mine_candidates']}/{u['mine_top05_cells']} | park {u['km_to_park']} km | legible {u['boundary_legibility']} score {u.get('score', '')}"]
    L.append(f"      seed {u['seed']} ({u['seed_kind']}); towns: {', '.join(u['towns']) or '-'}; overlaps: {', '.join(re.sub(r"_?[0-9'’]+ ?(ha|km2|sqkm)|\[.*?\]", "", o).strip() for o in u['overlaps']) or '-'}")
    L.append(f"      boundary ({u['perimeter_km']} km, {u['unattributed_pct']}% unnamed): " + "; ".join(u["boundary"]))
    if full:
        L.append(f"      built {u['built_2000_km2']} → {u['built_2015_km2']} → {u['built_km2']} km2 (x{u['built_growth_x']}); camps {u['camps']}; front dirs {u['front_dirs']}; nov-feb {u['fire_nov_feb_share']}; clearing all {u['clearing_km2']} km2/{u['clearing_events']} ev, encroach+slash {u['clearing_encroach_slash']}; watchlist {u['mine_watchlist']}")
        if u.get("geology"): L.append(f"      rock: {'; '.join(u['geology'])} — affinity prior {u.get('geo_affinity_mean')}")
    return "\n".join(L)


def narrate(u, gz=None, kind="", legal=""):
    """One honest paragraph per area — proposed park, gazetted PA, hand-drawn zone, planner proposal — built only
    from the measured dict (and the 1930s gazetteer inside it). No adjective without a number behind it."""
    n = lambda x, d=0: (f"{x:,.{d}f}" if isinstance(x, (int, float)) and x is not None else "—")
    nm = u.get("name") or u.get("seed") or f"unit {u['uid']}"
    L = [f"{nm}" + (f" — {kind}" if kind else "") + (f" [{legal}]" if legal else "")]
    L.append(f"  Size {n(u['area_ha'])} ha ({n(u['area_km2'])} km²), {u.get('country','?')}." + (f" {n(u['km_to_park'],1)} km from the proposed park." if u.get('km_to_park') not in (None, 0) else ""))
    cls = u.get("cls"); R = u.get("rationale") or []
    L.append(f"  Planner class as one unit: {cls}. " + ("; ".join(r for r in R[1:] if not r.startswith("—")) if R else ""))
    ppl = u["population_est"]; cl = u["clusters"]
    ptxt = (f"{cl} settlement clusters, about {n(ppl)} people (GHSL lower bound; {u['pop_per_km2']}/km²), {u['new_since_2015']} founded since 2015, {u['camps']} cattle camps"
            if cl else "no settlement cluster at all")
    if u.get("towns"): ptxt += "; largest: " + ", ".join(u["towns"][:4])
    if u.get("built_km2") is not None and u["built_km2"] > 0: ptxt += f". Built-up surface {u.get('built_2000_km2')} → {u.get('built_2015_km2')} → {u['built_km2']} km² (2000 → 2015 → today" + (f", ×{u['built_growth_x']})" if u.get('built_growth_x') else ")")
    L.append("  People: " + ptxt + ".")
    ft = u.get("fronts") or 0
    ftxt = f"{n(u['fire_det_2024_25'])} detections in 2024–25 ({n(u['fire_per_1000km2_yr'])}/1,000 km²/yr" + (f", {round(100*u['fire_nov_feb_share'])}% in Nov–Feb" if u.get('fire_nov_feb_share') is not None else "") + ")"
    if ft: ftxt += f"; {ft} tracked fire fronts, {u.get('fronts_transhumance_pct') or 0}% typed transhumance, {u['fronts_long']} of them ≥150 km" + (f", dominant axis {u['front_axis']} (coherence {u['front_axis_coherence']}; 0.25 = random)" if u.get('front_axis') else "") + (f", most common headings {', '.join(f'{k} {v}' for k, v in list(u.get('front_dirs', {}).items())[:3])}" if u.get('front_dirs') else "")
    L.append("  Fire: " + ftxt + ".")
    L.append(f"  Land use: cropland {u.get('cropland_2003_pct')}% (2003) → {u.get('cropland_2019_pct')}% (2019) of the area; reviewed clearing {u['clearing_km2']} km² in {u.get('clearing_events', '—')} events since 2000, {u['clearing_since_2020_km2']} km² since 2020, {u['clearing_encroach_slash']} classed encroachment/slash-and-burn.")
    if u.get("mining_measured", True):
        L.append(f"  Mining exposure: {u['mine_reported']} reported workings, {u['mine_candidates']} modelled candidates, {u['mine_top05_cells']} top-5% target cells, {u.get('mine_watchlist', 0)} abandoned-village-on-gold watchlist places (targets, not mines)." + (f" Rock: {'; '.join(u['geology'])} (commodity prior {u.get('geo_affinity_mean')}, unmeasured skill here)." if u.get("geology") else ""))
    else: L.append("  Mining exposure: unmeasured (no model for this area)." + (f" Rock: {'; '.join(u['geology'])}." if u.get("geology") else ""))
    if u.get("boundary") is not None:
        L.append(f"  Boundary ({u.get('perimeter_km')} km; {u.get('unattributed_pct')}% on no feature a person can point at): " + ("; ".join(u["boundary"]) or "—") + ".")
    if gz:
        bits = []
        if gz["peaks"]: bits.append("hills " + ", ".join(gz["peaks"][:6]))
        if gz["terrain"]: bits.append("terrain " + ", ".join(gz["terrain"][:4]))
        if gz["water"]: bits.append("waters " + ", ".join(gz["water"][:6]))
        des = [p for p in gz["places"] if re.search(r"deserted|uninhab|site of|old site", p, re.I)] + [x for x in gz["notes"] if re.search(r"deserted|uninhab|abandon", x, re.I)]
        if gz["places"]: bits.append(f"{gz['counts']['places']} named places in the 1930s" + (f", {len(des)} of them marked deserted" if des else "") + (": " + ", ".join(gz["places"][:6]) if gz["places"] else ""))
        if gz["boundary"]: bits.append("customary/district boundary notes " + ", ".join(gz["boundary"][:3]))
        if gz["built"]: bits.append("built marks " + ", ".join(gz["built"][:4]))
        if bits: L.append("  1930s Sudan Survey sheets inside: " + " | ".join(bits) + ".")
    if u.get("support_mean") is not None:
        L.append(f"  Bootstrap: support {u['support_mean']} (share of perturbed runs that keep each unit), size across runs p10/p50/p90 {u.get('area_ha_p10_p50_p90')} ha, {u.get('contested_units', 0)} contested units to walk with the community.")
    if u.get("overlaps"): L.append("  Overlaps: " + ", ".join(u["overlaps"]) + ".")
    return "\n".join(L)

AREA_KINDS = (   # (name-substring, kind, legal hook) — how each reference is introduced
    ("Pongo-Wau", "proposed national park (hand-drawn boundary, EASY plan)", "Wildlife Act 2026 national park; founding instrument must state the mining ban (Mining Act 2012)"),
    ("Wilderness", "proposed wilderness / buffer zone (hand-drawn)", "Wildlife Act 2026 s.9"),
    ("headwaters", "proposed headwater protection zone (hand-drawn)", "Wildlife Act 2026 s.9"),
    ("pâturage", "proposed grazing zone (hand-drawn)", "Wildlife Act 2026 s.9 corridor"),
    ("PLAN Southern NP", "Southern National Park as drawn in the plan", "gazetted 1939"),
    ("National Park", "gazetted national park (WDPA)", "existing"),
    ("Game Reserve", "gazetted game reserve (WDPA)", "existing"),
    ("Faunal Reserve", "gazetted faunal reserve (WDPA)", "existing"),
    ("Hunting Area", "gazetted hunting area (WDPA)", "existing"),
    ("Conservation Area", "gazetted conservation area (WDPA)", "existing"),
)
def area_kind(name):
    for sub, kind, legal in AREA_KINDS:
        if sub in name: return kind, legal
    return "reference polygon", ""

KML_COL = {"core": "7f00a000", "wilderness": "7f00d0d0", "community": "7f0080ff", "corridor": "7fd000d0"}
def kml_doc(items):
    """items: (name, description, polygon, class)"""
    pms = []
    for nm, desc, g, c in items:
        polys = "".join(f'<Polygon><outerBoundaryIs><LinearRing><coordinates>{" ".join(f"{x:.5f},{y:.5f},0" for x, y in p.exterior.coords)}</coordinates></LinearRing></outerBoundaryIs></Polygon>'
                        for p in (g.geoms if isinstance(g, MultiPolygon) else [g]))
        geom = f"<MultiGeometry>{polys}</MultiGeometry>" if isinstance(g, MultiPolygon) else polys
        pms.append(f'<Placemark><name>{nm}</name><description><![CDATA[{desc}]]></description><styleUrl>#{c}</styleUrl>{geom}</Placemark>')
    styles = "".join(f'<Style id="{c}"><LineStyle><color>ff{v[2:]}</color><width>1.5</width></LineStyle><PolyStyle><color>{v}</color></PolyStyle></Style>' for c, v in KML_COL.items())
    return f'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>{styles}{"".join(pms)}</Document></kml>'

def desc_of(u):
    return (f"{u['cls']} · {u['area_ha']:,} ha · {u['clusters']} clusters, {u['population_est']:,} people (GHSL lower bound), {u['new_since_2015']} new since 2015<br/>"
            f"fire {u['fire_per_1000km2_yr']:,}/1000km2/yr, {u['fronts']} fronts ({u['fronts_transhumance_pct']}% transhumance, {u['fronts_long']} ≥150 km)<br/>"
            f"clearing since 2020 {u['clearing_since_2020_km2']} km2; cropland {u['cropland_2003_pct']}→{u['cropland_2019_pct']}%; mining reported/candidates/top5% {u['mine_reported']}/{u['mine_candidates']}/{u['mine_top05_cells']}<br/>"
            f"boundary: {'; '.join(u['boundary'])} — {u['unattributed_pct']}% follows no nameable feature")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["build", "validate", "rank", "show", "export", "conservancies", "optimize", "describe"]); ap.add_argument("arg", nargs="?")
    ap.add_argument("--cell-km", type=float, default=2); ap.add_argument("--min-ha", type=float, default=2000); ap.add_argument("--min-order", type=int, default=4)
    ap.add_argument("--seed-pop", type=int, default=150, help="build: a settlement cluster needs this many people to seed a unit")
    ap.add_argument("--empty-km", type=float, default=20, help="build: lattice spacing of empty-land seeds")
    ap.add_argument("--target-ha", type=float, default=300000, help="build: below this a unit merges across a weak edge; above it only if < min-ha")
    ap.add_argument("--max-ha", type=float, default=600000, help="build: never merge into a unit above this")
    ap.add_argument("--weak", type=float, default=0.35, help="build: mean edge legibility under which an edge is 'nothing to see'")
    ap.add_argument("--core-pop-km2", type=float, default=0.02); ap.add_argument("--core-crop-pct", type=float, default=0.05); ap.add_argument("--core-clear-km2", type=float, default=0.5, help="core: reviewed clearing since 2020, km2 per 1000 km2 (scale-invariant)")
    ap.add_argument("--wild-pop-km2", type=float, default=0.5); ap.add_argument("--wild-crop-pct", type=float, default=0.5)
    ap.add_argument("--corridor-q", type=float, default=0.75, help="corridor: long-front density at or above this quantile of all units")
    ap.add_argument("--corridor-th-pct", type=float, default=50); ap.add_argument("--corridor-pop-km2", type=float, default=2)
    ap.add_argument("--corridor-axis-coh", type=float, default=0.4, help="corridor: share of fronts along the modal axis (N-S, NE-SW, E-W, SE-NW)")
    ap.add_argument("--max-km2", type=float, default=0)
    ap.add_argument("--rim-km", type=float, default=0, help="conservancies: only land within this distance of the proposed park (0 = whole area)")
    ap.add_argument("--mining", action="store_true", default=True, help="conservancies: land the mining model flags (top-5%% cells / candidates / reports) is a candidate even without a village — a conservancy under Wildlife Act s.14 + Mining Act s.24 is the community's veto on a licence")
    ap.add_argument("--workers", type=int, default=8, help="conservancies --describe: parallel LLM calls")
    ap.add_argument("--describe", action="store_true", help="conservancies: ask the LLM (HISTMAP_LLM_URL) to read each candidate's sheet names, symbols and notes and classify/justify it")
    ap.add_argument("--want-class", default="core", choices=CLASSES, help="optimize: class of area to grow")
    ap.add_argument("--opt-max-ha", type=float, default=2_000_000, help="optimize: stop growing past this")
    ap.add_argument("--draws", type=int, default=40, help="optimize: bootstrap draws"); ap.add_argument("--perturb", type=float, default=0.25, help="optimize: ±fraction on every threshold per draw")
    ap.add_argument("--n-areas", type=int, default=3, help="optimize: how many disjoint areas to propose")
    ap.add_argument("--seed-near", default="", help="optimize: grow only from the fine units containing these points, 'lon,lat[;lon,lat…]' (e.g. the boom towns the rim run cannot reach)")
    ap.add_argument("--tag", default="", help="optimize: suffix for the output files (optimize_<class>_<tag>.*)")
    ap.add_argument("--reach-km", type=float, default=8, help="conservancies: land within this distance of a settlement cluster counts as used")
    ap.add_argument("--cons-target-ha", type=float, default=150000, help="conservancies: stop growing a conservancy past this")
    ap.add_argument("--cons-min-pop", type=int, default=100, help="conservancies: drop candidates with fewer people than this"); ap.add_argument("--want", default="balanced", choices=["people", "pressure", "rim", "balanced"])
    ap.add_argument("--class", dest="cls", default=""); ap.add_argument("--exclude", default=""); ap.add_argument("--country", default=""); ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args(); OUT.mkdir(parents=True, exist_ok=True)
    T = dict(core_pop_km2=a.core_pop_km2, core_crop_pct=a.core_crop_pct, core_clear_km2=a.core_clear_km2, wild_pop_km2=a.wild_pop_km2, wild_crop_pct=a.wild_crop_pct,
             corridor_q=a.corridor_q, corridor_th_pct=a.corridor_th_pct, corridor_pop_km2=a.corridor_pop_km2, corridor_axis_coh=a.corridor_axis_coh)
    if a.mode == "build":
        con, hcon = sqlite3.connect(DB), sqlite3.connect(HDB)
        aoi = aoi_geom(con); G = Grid(aoi, a.cell_km); log(f"grid {G.w}x{G.h} @ {a.cell_km} km")
        feats = rivers(con, hcon, a.min_order); rl, beacons = ridges(hcon, aoi); feats += rl
        feats += hist_boundaries(hcon, aoi) + hist_watercourses(hcon, aoi) + roads(con)
        gunits, gcont = geology(aoi); feats += gcont; G.geo_units = gunits
        ctry = countries()
        for iso, g in ctry.items(): feats.append((g.boundary.intersection(aoi.buffer(0.05)), f"{iso} border", "border", 8))
        feats.append((aoi.boundary, "study-area edge", "frame", 8))
        surf = legibility(G, feats); log("legibility surface done")
        markers, names, ncomm = seeds(con, G, a.seed_pop, a.empty_km)
        lab = segment(G, surf, markers); log(f"watershed: {len(np.unique(lab))-1} units")
        refs = references(); park = next((g for k, g in refs.items() if PARK_KEY in k), None)
        lab = merge_units(lab, surf, G, a.min_ha)                       # pass 1: nothing under min-ha
        lab1 = lab.copy()
        # a FINE mesh for growing conservancies: every village (≥20 people) and a 10 km lattice seed their own basin,
        # so the smallest legible unit between two neighbouring villages exists to be chosen
        rowsS = con.execute("SELECT lat, lon, population_est FROM park_settlements WHERE park_id=? AND population_est>=20", (AOI,)).fetchall()
        mk = np.zeros((G.h, G.w), np.int32); kk = 0
        for la_, lo_, pop_ in sorted(rowsS, key=lambda x: -x[2]):
            r_, c_ = G.rc(lo_, la_)
            if 0 <= r_ < G.h and 0 <= c_ < G.w and G.mask[r_, c_] and not mk[max(0, r_-2):r_+3, max(0, c_-2):c_+3].any(): kk += 1; mk[r_, c_] = kk
        stp = max(1, int(10000/G.res))
        for r_ in range(stp//2, G.h, stp):
            for c_ in range(stp//2, G.w, stp):
                if G.mask[r_, c_] and not mk[max(0, r_-2):r_+3, max(0, c_-2):c_+3].any(): kk += 1; mk[r_, c_] = kk
        labf = merge_units(segment(G, surf, mk), surf, G, a.min_ha); log(f"fine mesh: {len(np.unique(labf))-1} units from {kk} seeds")
        U0, _, _ = attributes(con, G, lab, surf, feats, names, ncomm, refs, ctry, park, T, light=True)
        cls = {u["uid"]: u["cls"] for u in U0}; log("raw classes: " + str(Counter(cls.values())))
        lab = merge_units(lab, surf, G, a.min_ha, cls, a.target_ha, a.weak, a.max_ha)   # pass 2: like with like, across weak edges only
        U, polys, mnote = attributes(con, G, lab, surf, feats, names, ncomm, refs, ctry, park, T, freeze=T)
        popkm2 = np.zeros((G.h, G.w), np.float32)
        for u in U: popkm2[lab == u["uid"]] = u["pop_per_km2"]
        band, path, cstat = corridor_axis(G, refs, popkm2); G.corr_band = band; G.corr_path = path; log(f"corridor today: {cstat}")
        closed = ("Pongo-Wau", "Southern NP", "Southern (National", "Wilderness", "headwaters") if AOI == "XSA_Study_Area" else (PARK_KEY,)
        band2, path2, cstat2 = corridor_axis(G, refs, popkm2, closed=closed); G.div_band = band2; G.div_path = path2; log(f"corridor with park+wilderness closed: {cstat2}")
        from rasterio import features as rfeat
        fc = []
        for b, kind, cs in ((band, "walked_today", cstat), (band2, "diverted_park_closed", cstat2)):
            if b is None: continue
            cg = unary_union([transform(INV, shape(g)) for g, v in rfeat.shapes(b.astype(np.uint8), mask=b, transform=G.tr)])
            fc.append({"type": "Feature", "properties": dict(kind=kind, **cs), "geometry": mapping(cg)})
        json.dump({"type": "FeatureCollection", "features": fc}, open(OUT / "corridor_walked.geojson", "w"))
        if band is not None:
            for u in U: u["in_walked_corridor_pct"] = round(100*float((band & (lab == u["uid"])).sum()/max((lab == u["uid"]).sum(), 1)))
        if band2 is not None:
            for u in U: u["in_diverted_corridor_pct"] = round(100*float((band2 & (lab == u["uid"])).sum()/max((lab == u["uid"]).sum(), 1)))
        pickle.dump(dict(G=G, lab=lab, lab1=lab1, labf=labf, surf=surf, feats=feats, polys=polys, T=T, names=names, ncomm=ncomm), open(OUT / "state.pkl", "wb"))
        json.dump(dict(units=U, thresholds=T, mining_note=mnote, cell_km=a.cell_km, min_ha=a.min_ha), open(OUT / "units.json", "w"))
        json.dump({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in u.items()}, "geometry": mapping(polys[u["uid"]])} for u in U if u["uid"] in polys]}, open(OUT / "units.geojson", "w"))
        (OUT / "units.kml").write_text(kml_doc([(f"#{u['uid']} {u['cls']} {u['seed']} ({u['area_ha']:,} ha)", desc_of(u), polys[u["uid"]], u["cls"]) for u in U if u["uid"] in polys]))
        json.dump({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"kind": k, "name": n, "w": w}, "geometry": mapping(g)} for g, n, k, w in feats if not g.is_empty]}, open(OUT / "mesh_features.geojson", "w"))
        tot = Counter()
        for u in U: tot[u["cls"]] += u["area_km2"]
        print(f"{len(U)} units, median {np.median([u['area_km2'] for u in U]):,.0f} km2; by class km2: " + ", ".join(f"{c} {tot[c]:,.0f}" for c in CLASSES))
        print(f"{sum(1 for u in U if (u['unattributed_pct'] or 0) > 25)} units have >25% of boundary on no nameable feature; median legibility {np.median([u['boundary_legibility'] or 0 for u in U]):.2f}")
        return
    st = pickle.load(open(OUT / "state.pkl", "rb")); J = json.load(open(OUT / "units.json")); U = J["units"]
    if a.mode == "validate":
        refs = references(); park = next((g for k, g in refs.items() if PARK_KEY in k), None)
        txt, rows = validate(st["G"], st["lab"], U, refs, st["feats"], sqlite3.connect(DB), countries(), park, st["T"], st["surf"])
        print(txt); (OUT / "VALIDATION.txt").write_text(txt + "\n"); json.dump(rows, open(OUT / "validation.json", "w"), indent=1)
    elif a.mode == "rank":
        R = rank(U, a.want, a.cls, a.exclude, a.country, a.min_ha, a.max_km2)
        hdr = f"RANK want={a.want} class={a.cls or 'any'} exclude=[{a.exclude}] country={a.country or 'any'} min {a.min_ha:,.0f} ha — {len(R)} eligible of {len(U)}\n{J['mining_note']}\n"
        body = "\n".join(fmt(u) for u in R[:a.top]); print(hdr + body)
        (OUT / "RANK.txt").write_text(hdr + body + "\n\nPopulations: GHSL lower bounds. Fire: 2024-25, 3-satellite fleet. Boundary names: HydroRIVERS / 1930s sheets — verify on the ground.\n")
    elif a.mode == "conservancies":
        con = sqlite3.connect(DB); refs = references()
        default_ex = "Pongo-Wau,Southern NP,Southern (National" if AOI == "XSA_Study_Area" else PARK_KEY
        C, polys, lab = conservancies(con, st, refs, countries(), st["T"], a.exclude or default_ex, a.rim_km, a.reach_km, a.min_ha, a.cons_target_ha, a.cons_min_pop, a.country or "ALL", a.mining)
        tag = f"rim{int(a.rim_km)}" if a.rim_km else "all"
        if a.describe:
            import concurrent.futures, threading
            cache = OUT / f"descriptions_{tag}.json"; D = json.load(open(cache)) if cache.exists() else {}; lock = threading.Lock()
            keyof = lambda u: f"{u['seed']}:{u['area_ha']}:{u['population_est']}:{u['mine_top05_cells']}"
            todo = [u for u in C if keyof(u) not in D]; log(f"describe: {len(todo)} of {len(C)} to read with {LLM_MODEL} x{a.workers}")
            def work(u):
                d = describe_unit(u)
                with lock:
                    D[keyof(u)] = d; json.dump(D, open(cache, "w"), ensure_ascii=False, indent=1)
                return u["seed"], d.get("designation") or d.get("error")
            with concurrent.futures.ThreadPoolExecutor(a.workers) as ex:
                for nm, r in ex.map(work, todo): log(f"  {nm}: {r}")
            for u in C: u["llm"] = D[keyof(u)]
        L = [f"CONSERVANCY CANDIDATES ({tag}; used land = fine units with a cluster or within {a.reach_km} km of one; grown across weakest edges to <= {a.cons_target_ha:,.0f} ha; min {a.min_ha:,.0f} ha, min {a.cons_min_pop} people)",
             J["mining_note"], ""]
        for u in C:
            L.append(f"[{u.get('park_sector','')}] " + fmt(u, full=True))
            gz = u["gazetteer"]; L.append(f"      reason: {u['reason']} (mining-exposed land {u.get('mining_exposed_pct',0)}%)")
            L.append("      1930s sheets: peaks " + "; ".join(gz["peaks"][:8]) + " | terrain " + "; ".join(gz["terrain"][:6]) + " | water " + "; ".join(gz["water"][:6]) + " | places " + "; ".join(gz["places"][:8]) + " | notes " + "; ".join(gz["notes"][:6]) + " | built " + "; ".join(gz["built"][:4]))
            if u.get("llm"):
                d = u["llm"]
                if "error" in d: L.append(f"      LLM: {d}")
                else:
                    L.append(f"      LLM → {d.get('designation')} ({d.get('confidence')}): {d.get('name')} — {d.get('name_meaning')}")
                    for k in ("why", "boundary_story", "mining", "people_land"): L.append(f"        {k}: {d.get(k)}")
                    L.append(f"        verify: {d.get('verify_on_ground')}")
            L.append("")
        tot = sum(u["area_ha"] for u in C); pop = sum(u["population_est"] for u in C)
        L.append(f"{len(C)} candidates, {tot:,} ha, {pop:,} people (GHSL lower bound). Populations are satellite lower bounds; boundary names are HydroRIVERS / 1930s sheets — verify on the ground.")
        txt = "\n".join(L); print(txt); (OUT / f"CONSERVANCIES_{tag}.txt").write_text(txt + "\n")
        json.dump({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in u.items()}, "geometry": mapping(polys[u["uid"]])} for u in C]}, open(OUT / f"conservancies_{tag}.geojson", "w"))
        (OUT / f"conservancies_{tag}.kml").write_text(kml_doc([(f"{u.get('park_sector','')} {u['seed']} ({u['area_ha']:,} ha, {u['population_est']:,} ppl)", desc_of(u), polys[u["uid"]], "community") for u in C]))
    elif a.mode == "optimize":
        con = sqlite3.connect(DB); refs = references(); ctry = countries(); park = next((g for k, g in refs.items() if PARK_KEY in k), None)
        G, lab1, surf, T = st["G"], st["lab1"], st["surf"], st["T"]; want = a.want_class
        if want == "corridor":
            # a corridor is a PATH, not a blob: bootstrap the least-cost path itself (cost field jitter, smoothing 2–5,
            # people penalty, width 8–16 km); support = share of draws a cell lies in the band. Proposal = support ≥ 0.5.
            popkm2 = np.zeros((G.h, G.w), np.float32)
            for u in U: popkm2[st["lab"] == u["uid"]] = u["pop_per_km2"]
            rng = np.random.default_rng(0); sup = np.zeros((G.h, G.w)); n = 0; widths = []
            for i in range(a.draws):
                band, path, cs = corridor_axis(G, refs, popkm2, width_km=rng.uniform(8, 16), smooth=rng.uniform(2, 5), expo=rng.uniform(1.0, 2.0), pop_w=rng.uniform(1, 4), rng=rng)
                if band is None: continue
                sup += band; n += 1; widths.append(cs["km2"])
            sup /= max(n, 1)
            geoms = []
            from rasterio import features as rfeat
            for lvl in (0.25, 0.5, 0.75):
                m = sup >= lvl
                if m.any(): geoms.append((lvl, unary_union([transform(INV, shape(g)) for g, v in rfeat.shapes(m.astype(np.uint8), mask=m, transform=G.tr)])))
            json.dump({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": dict(support=l, want=want), "geometry": mapping(g)} for l, g in geoms]}, open(OUT / "optimize_corridor_support.geojson", "w"))
            m50 = sup >= 0.5
            lab = m50.astype(np.int32); names = {1: "corridor proposal (support ≥ 0.5)"}
            U2, polys, _ = attributes(con, G, lab, surf, st["feats"], names, 0, refs, ctry, park, T, freeze=T)
            u = U2[0]; u["seed"] = names[1]; u["seed_kind"] = "optimized"
            L = [f"OPTIMIZE corridor: {n} bootstrap draws of the least-cost herd path (cost jitter ±15%, smoothing 2–5 cells, people penalty 1–4, half-width 8–16 km)",
                 f"band area across draws p10/p50/p90 {[int(np.percentile(widths, q)) for q in (10, 50, 90)]} km2; support ≥0.5 band {m50.sum()*G.cell_km2():,.0f} km2; ≥0.75 {(sup >= 0.75).sum()*G.cell_km2():,.0f} km2", ""]
            L.append(fmt(u, full=True)); L.append("      rationale: " + " | ".join(u["rationale"][1:]))
            L.append(f"      long-front density inside band {G.longdens[m50].mean():.2f}/cell vs outside {G.longdens[G.mask & ~m50].mean():.2f}/cell")
            txt = "\n".join(L); print(txt); (OUT / "OPTIMIZE_corridor.txt").write_text(txt + "\n")
            json.dump({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in u.items()}, "geometry": mapping(polys[1])}]}, open(OUT / "optimize_corridor.geojson", "w"))
            (OUT / "optimize_corridor.kml").write_text(kml_doc([(f"corridor proposal ({u['area_ha']:,} ha)", desc_of(u), polys[1], "corridor")]))
            return
        OBJ_TARGET_HA["community"] = a.cons_target_ha
        mesh = st.get("labf", lab1) if want == "community" else lab1     # conservancies grow from the village-scale mesh
        if a.rim_km and park is not None:      # rim: only fine units within rim_km of the park may be used
            from scipy import ndimage
            far = ndimage.distance_transform_edt(~G.rasterize([(transform(FWD, park), 1)]).astype(bool))*G.res/1000 > a.rim_km
            mesh = np.where(far, 0, mesh)
        exm = np.zeros((G.h, G.w), bool)     # exclusions: the park and gazetted PAs are not conservancy land
        for k_, g_ in refs.items():
            if g_.geom_type != "Point" and any(e.strip().lower() in k_.lower() for e in (a.exclude or ("Pongo-Wau,Southern NP,Southern (National" if AOI == "XSA_Study_Area" else PARK_KEY)).split(",") if e.strip()):
                exm |= G.rasterize([(transform(FWD, g_), 1)]).astype(bool)
        if want == "community": mesh = np.where(exm, 0, mesh)
        UT = UnitTable(con, G, mesh, surf, T); log(f"unit table: {len(UT.unit_cls)} fine units; classes {Counter(UT.unit_cls.values())}")
        # seeds: fine units already of the wanted class — for community the most populated, else the largest (top 40)
        seeds_ = sorted([u for u, c in UT.unit_cls.items() if c == want], key=lambda u: -(UT.S["pop"][u] if want == "community" else UT.cnt[u]))[:40]
        if a.seed_near:
            seeds_ = []
            for pt in a.seed_near.split(";"):
                lo_, la_ = map(float, pt.split(",")); r_, c_ = G.rc(lo_, la_); u_ = int(mesh[r_, c_]) if G.inside(np.array([r_]), np.array([c_]))[0] else 0
                if u_ and UT.unit_cls.get(u_) == want: seeds_.append(u_)
                else: log(f"seed-near {pt}: fine unit {u_} is {UT.unit_cls.get(u_)} — not '{want}', skipped")
        if not seeds_: sys.exit(f"no fine unit is '{want}' — nothing to grow from")
        forbid = set(); proposals = []; sup_all = np.zeros(UT.ML)
        for k in range(a.n_areas):
            sup, res = bootstrap(UT, want, T, a.opt_max_ha, a.draws, a.perturb, [s_ for s_ in seeds_ if s_ not in forbid], frozenset(forbid), rng_seed=k)
            core_set = [u for u in range(1, UT.ML) if sup[u] >= 0.5]
            if not core_set: log(f"area {k+1}: no unit reaches 50% support; stop"); break
            d = UT.measure(core_set); d["cls"] = classify(d, T); L, P = UT.legibility_of(core_set)
            ha = [r["area_ha"] for r in res]
            proposals.append(dict(rank=k+1, want=want, members=core_set, support_mean=round(float(sup[core_set].mean()), 2), contested_units=[u for u in range(1, UT.ML) if 0.25 <= sup[u] < 0.5],
                                  measured=d, outer_legibility=round(L, 2), draws=len(res), area_ha_p10_p50_p90=[int(np.percentile(ha, q)) for q in (10, 50, 90)] if ha else None))
            sup_all = np.maximum(sup_all, sup); forbid |= set(core_set) | set(proposals[-1]["contested_units"])
            log(f"area {k+1}: {len(core_set)} units, {d['area_ha']:,} ha, class as a whole = {d['cls']}, support {proposals[-1]['support_mean']}, size p10/p50/p90 {proposals[-1]['area_ha_p10_p50_p90']}")
        # measure each proposal like a park (names + boundary description) and write
        lab = np.zeros_like(lab1); names = {}
        for p_ in proposals: lab[np.isin(mesh, p_["members"])] = p_["rank"]; names[p_["rank"]] = f"{want} proposal {p_['rank']}"
        U2, polys, _ = attributes(con, G, lab, surf, st["feats"], names, 0, refs, ctry, park, T, freeze=T)
        byr = {u["uid"]: u for u in U2}
        L = [f"OPTIMIZE want={want} max {a.opt_max_ha:,.0f} ha; {a.draws} bootstrap draws per area, thresholds ±{a.perturb:.0%}, greedy path jittered ±10%",
             "support = share of draws a fine unit lies inside the best-grown area; proposal = support ≥ 0.5; contested = 0.25–0.5", G.mining_note, ""]
        for p_ in proposals:
            u = byr.get(p_["rank"])
            if not u: continue
            u["seed"] = names[p_["rank"]]; u["seed_kind"] = "optimized"; u["support_mean"] = p_["support_mean"]; u["contested_units"] = len(p_["contested_units"]); u["area_ha_p10_p50_p90"] = p_["area_ha_p10_p50_p90"]
            L.append(fmt(u, full=True)); L.append(f"      as a whole → {u['cls']}; support {p_['support_mean']}; size across draws p10/p50/p90 {p_['area_ha_p10_p50_p90']} ha; {len(p_['contested_units'])} contested fine units")
            L.append("      rationale: " + " | ".join(u["rationale"][1:])); L.append("")
        txt = "\n".join(L); print(txt); tag = f"{want}" + (f"_{a.tag}" if a.tag else "")
        (OUT / f"OPTIMIZE_{tag}.txt").write_text(txt + "\n")
        json.dump({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in byr[p_["rank"]].items()}, "geometry": mapping(polys[p_["rank"]])} for p_ in proposals if p_["rank"] in polys]}, open(OUT / f"optimize_{tag}.geojson", "w"))
        # support raster as polygons at 0.25/0.5/0.75 for the map
        from rasterio import features as rfeat
        fc = []
        for lvl in (0.25, 0.5, 0.75):
            m = np.isin(mesh, [u for u in range(1, UT.ML) if sup_all[u] >= lvl])
            if m.any():
                gg = unary_union([transform(INV, shape(g)) for g, v in rfeat.shapes(m.astype(np.uint8), mask=m, transform=G.tr)])
                fc.append({"type": "Feature", "properties": dict(support=lvl, want=want), "geometry": mapping(gg)})
        json.dump({"type": "FeatureCollection", "features": fc}, open(OUT / f"optimize_{tag}_support.geojson", "w"))
        (OUT / f"optimize_{tag}.kml").write_text(kml_doc([(f"{want} proposal {p_['rank']} ({byr[p_['rank']]['area_ha']:,} ha, support {p_['support_mean']})", desc_of(byr[p_["rank"]]), polys[p_["rank"]], want) for p_ in proposals if p_["rank"] in polys]))
    elif a.mode == "describe":
        # every area the plan talks about — hand-drawn zones, gazetted PAs, and the planner's own proposals — measured with
        # the same rasters and the same rule, then narrated. Output AREAS.txt / areas.json; the KMLs get the same text.
        con = sqlite3.connect(DB); refs = references(); ctry = countries(); park = next((g for k, g in refs.items() if PARK_KEY in k), None)
        G, surf, T, feats = st["G"], st["surf"], st["T"], st["feats"]
        geoms = [(nm, g) for nm, g in refs.items() if g.geom_type != "Point"]
        props = []
        for f in sorted(OUT.glob("optimize_*.geojson")):
            if "support" in f.name: continue
            for ft in json.load(open(f))["features"]:
                pp = ft["properties"]; nm = f"PROPOSAL {pp.get('seed', f.stem)}" + (f" ({f.stem.split('optimize_', 1)[1]})" if a.tag or "_" in f.stem.split("optimize_", 1)[1] else "")
                geoms.append((nm, shape(ft["geometry"]))); props.append((nm, pp))
        # one assess() per polygon: areas overlap (park inside wilderness inside grazing zone; proposals over all of them),
        # and a shared label image would give each cell to whichever came first
        A = []
        for nm, g in geoms:
            u_ = assess(con, G, [(nm, g)], refs, ctry, park, T, feats, surf)
            if u_: A.append(u_[0])
            else: log(f"describe: {nm} has no cell inside the grid")
        extra = {nm: pp for nm, pp in props}
        L = ["AREAS — every zone, gazetted protected area and planner proposal in the study area, measured with one yardstick and described in plain language",
             "Populations are GHSL lower bounds; fire is the 2024–25 three-satellite fleet; boundary names are HydroRIVERS / 1930s Sudan Survey sheets and must be verified on the ground.", G.mining_note, ""]
        out = []
        for u in sorted(A, key=lambda u: (0 if u["name"].startswith("PROPOSAL") else 1 if u["name"].startswith("PLAN") else 2, -u["area_km2"])):
            pp = extra.get(u["name"], {})
            for k in ("support_mean", "area_ha_p10_p50_p90", "contested_units"):
                if k in pp: u[k] = json.loads(pp[k]) if isinstance(pp[k], str) and pp[k].startswith("[") else pp[k]
            kind, legal = area_kind(u["name"])
            if u["name"].startswith("PROPOSAL"):
                w = next((c for c in CLASSES if c in u["name"]), "")
                kind, legal = {"core": ("planner proposal: core / park extension", "national park or s.9 reserve"), "community": ("planner proposal: community conservancy", "Wildlife Act 2026 s.14 (s.14(4) veto; Mining Act s.24 consent)"),
                               "corridor": ("planner proposal: livestock / wildlife corridor (support ≥ 0.5 band)", "Wildlife Act 2026 s.9"), "wilderness": ("planner proposal: wilderness", "s.9")}.get(w, (kind, legal))
            m = G.rasterize([(transform(FWD, dict(geoms)[u["name"]]), 1)]).astype(bool) & G.mask
            gz = gazetteer(G, m)
            txt = narrate(u, gz, kind, legal); L.append(txt); L.append("")
            out.append(dict(name=u["name"], kind=kind, legal=legal, text=txt, measured={k: v for k, v in u.items() if k not in ("front_dirs_all",)}))
        txt = "\n".join(L); print(txt); (OUT / "AREAS.txt").write_text(txt + "\n"); json.dump(out, open(OUT / "areas.json", "w"), ensure_ascii=False, indent=1)
    elif a.mode == "show": print(fmt(next(u for u in U if u["uid"] == int(a.arg)), full=True))
    elif a.mode == "export":
        ids = [int(x) for x in a.arg.split(",")]; by = {u["uid"]: u for u in U}
        items = [(f"#{i} {by[i]['seed']} ({by[i]['area_ha']:,} ha)", desc_of(by[i]), st["polys"][i], by[i]["cls"]) for i in ids]
        p = OUT / ("units_" + "_".join(map(str, ids[:6])) + ".kml"); p.write_text(kml_doc(items)); print("wrote", p)
        for i in ids: print(fmt(by[i], full=True))

if __name__ == "__main__": main()
