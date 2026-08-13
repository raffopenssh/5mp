"""Measure the commodity-affinity model against an occurrence dataset.

The geology panel offers two choosers: pick a COMMODITY and it isolates the
rock units that can host it, or open the Junctions tab and it draws the
contact lines whose two lithologies make a deposit setting.  Both are
inferences over lithology (`srv/geomap_std.go`, `srv/geomap_contacts.go`) and
neither has ever been scored.  An inference nobody scores is a story, and a
story that is drawn on a map reads as a measurement.

This script scores it, on the only ground where we hold an independent
occurrence list: the **CAR sheet** against **IPIS artisanal-mine visits**
(`data/ipis/caf_mines_ipis.csv`, 914 visited sites, each flagged for gold
and/or diamond).  Two questions, kept separate because they have different
answers:

  UNITS      of the sites that produce commodity X, what fraction fall inside
             a unit the model grades for X, against the fraction of the map's
             AREA those units cover?  Ratio > 1 = the chooser concentrates.
  JUNCTIONS  how much closer are those sites to the graded contact lines than
             a uniform random point on the same sheet, and than the sites of
             the OTHER commodity (the control that rules out "mines are near
             every line", which they are: 77% sit within 5 km of *some*
             contact)?

Two guards against a flattering number:

  * The random baseline is drawn inside the sheet's own union of units, not
    its bounding box - a box includes the unmapped corners, where nothing is,
    and would make any line look selective.
  * The cross-commodity control is gold-only vs diamond-only sites (a site
    flagged for both tells us nothing about which rock it came for).

A third section, --continental, scores the layers we do NOT have: the JRC
Africa Knowledge Platform's WFS (LithoMap Africa, cratons, Macgregor active
faults) against the 7,163 IPIS visits in DRC, where no sheet of ours reaches.
It exists to answer "would another dataset do better than our sheet does",
with the same baseline discipline, before anyone spends a week ingesting one.

Usage:  python3 scripts/geomaps/eval_affinity.py [--json out.json]
                                                 [--continental]
Needs the server running (the catalogue's lithologies and contact rules are
server-owned; re-deriving them here would score a second implementation).
"""

import argparse, collections, csv, json, subprocess, sys
import numpy as np
import pyproj
from shapely.geometry import shape, Point
from shapely.ops import unary_union, nearest_points
from shapely.strtree import STRtree

SHEET = "car"
IPIS = "data/ipis/caf_mines_ipis.csv"
# The second occurrence dataset, and the only one that reaches the other two
# sheets: 969 major nonfuel deposits, USGS OFR 2005-1294-E, fetched off the JRC
# Africa Knowledge Platform's WFS and reprojected to WGS84 once (the WFS serves
# EPSG:3857 whatever you ask for). Committed, because an eval whose truth set is
# a cached download is not reproducible.
USGS = "data/geology_truth/usgs_africa_deposits.geojson"
# IPIS CAR columns are per-commodity flags, not one mineral string.
FLAGS = {"gold": "minerals_or", "diamond": "minerals_diamant"}
GEOD = pyproj.Geod(ellps="WGS84")
NEAR_KM = 5.0
N_RANDOM = 3000
SEED = 7


def catalogue(sheet, pwd="test2026"):
    raw = subprocess.check_output(
        ["curl", "-fsS", f"http://localhost:8000/api/geomap?pwd={pwd}"])
    sheets = {s["id"]: s for s in json.loads(raw)["sheets"]}
    if sheet not in sheets:
        sys.exit(f"sheet {sheet} not served; is the server running?")
    return sheets[sheet]["catalogue"]


def pair_key(a, b):
    return f"{a}|{b}" if a < b else f"{b}|{a}"


def dist_km(pt, geom):
    q = nearest_points(pt, geom)[1]
    return GEOD.inv(pt.x, pt.y, q.x, q.y)[2] / 1000.0


# ---------------------------------------------------------------------------
# The continental cross-check.
#
# Layers off the JRC Africa Knowledge Platform's GeoServer (open WFS, GeoJSON,
# whole continent in one request) scored against the 7,163 IPIS visits in DRC,
# which no sheet of ours covers. Cached under data/eval/akp/ because the point
# of the section is to be re-run.
AKP = ("https://africa-knowledge-platform.ec.europa.eu/geoserver/akp/wfs"
       "?service=WFS&version=2.0.0&outputFormat=application/json"
       "&request=GetFeature&typeNames=akp:")
AKP_CACHE = "data/eval/akp"
IPIS_COD = "data/ipis/cod_mines_ipis.csv"


def akp_layer(name):
    import os
    os.makedirs(AKP_CACHE, exist_ok=True)
    path = f"{AKP_CACHE}/{name}.json"
    if not os.path.exists(path):
        subprocess.check_call(["curl", "-fsS", "--compressed", "--max-time",
                               "300", AKP + name, "-o", path])
    return json.load(open(path))["features"]


def continental():
    """Score continental layers against the DRC visit list.

    The random baseline is drawn inside the CONVEX HULL of the visits, not the
    country: IPIS surveys the east, so a country-wide baseline would credit any
    layer that merely happens to be eastern - including a layer of the survey's
    own footprint.

    A craton is scored on its EDGE, not its interior: 60% of the hull is inside
    one, so "the site is on a craton" is true of the random points too.
    """
    rows = [r for r in csv.DictReader(open(IPIS_COD)) if r.get("longitude")]
    sites = collections.defaultdict(list)
    for r in rows:
        sites[(r.get("mineral1") or "?")].append(
            Point(float(r["longitude"]), float(r["latitude"])))
    hull = unary_union([p for v in sites.values() for p in v]).convex_hull
    rng = np.random.default_rng(SEED)
    minx, miny, maxx, maxy = hull.bounds
    rand = []
    while len(rand) < 2000:
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if hull.contains(p):
            rand.append(p)
    named = [("gold", sites["Or"]), ("cassiterite", sites["Cassit\u00e9rite"]),
             ("coltan", sites["Coltan"]), ("random", rand)]
    out = {"hull_deg2": hull.area, "sites": {n: len(v) for n, v in named}}
    print("\nCONTINENTAL CROSS-CHECK - JRC AKP layers vs IPIS DRC visits")
    for n, v in named:
        print(f"  {n:12} {len(v):5}")

    litho = akp_layer("LithoMap_Africa")
    geoms = [shape(f["geometry"]) for f in litho]
    glg = [f["properties"]["GLG"] for f in litho]
    tree = STRtree(geoms)
    area = collections.Counter()
    for g, c in zip(geoms, glg):
        if g.intersects(hull):
            area[c] += g.intersection(hull).area
    tot = sum(area.values())

    def at(p):
        for i in tree.query(p):
            if geoms[i].contains(p):
                return int(i)
        return None

    print("\n  LithoMap_Africa - class density for gold visits")
    print(f"  {'GLG':>6} {'sites':>6} {'share':>7} {'area':>7} {'lift':>6}")
    rec = {}
    idx = [i for i in (at(p) for p in sites["Or"]) if i is not None]
    cnt = collections.Counter(glg[i] for i in idx)
    for c, k in cnt.most_common(6):
        sh = area[c] / tot if tot else 0.0
        lift = (k / len(idx)) / sh if sh else float("nan")
        rec[c] = {"sites": k, "share": k / len(idx),
                  "area_share": sh, "lift": lift}
        print(f"  {c:>6} {k:>6} {k/len(idx):>6.1%} {sh:>6.1%} {lift:>6.2f}")
    out["lithomap_gold"] = rec

    layers = {
        "craton_edge": (unary_union([shape(f["geometry"]).boundary
                                     for f in akp_layer("cratons")]), 25.0),
        "active_faults": (unary_union([shape(f["geometry"]) for f
                                       in akp_layer("active_faults")]), 25.0),
    }
    print("\n  proximity layers")
    dist = {}
    for name, (geom, thr) in layers.items():
        print(f"  {name} (within {thr:g} km):")
        d = {}
        for n, pts in named:
            near = float(np.mean([dist_km(p, geom) < thr for p in pts]))
            med = float(np.median([dist_km(p, geom) for p in pts]))
            d[n] = {"near": near, "median_km": med}
            print(f"    {n:12} P {near:.3f}  median {med:7.1f} km")
        for n in ("gold", "cassiterite", "coltan"):
            d[n]["lift"] = (d[n]["near"] / d["random"]["near"]
                            if d["random"]["near"] else float("nan"))
        dist[name] = d
    out["proximity"] = dist
    return out


# ---------------------------------------------------------------------------
# The other two sheets.
#
# IPIS covers CAR and DRC; Sudan and Tanzania have no artisanal survey we hold.
# The USGS major-deposit list does reach them - 969 points continent-wide - and
# it is a DIFFERENT kind of truth: industrial-scale named deposits, not visited
# artisanal pits. That difference is the point of running both. A rule that
# finds artisanal workings and a rule that finds Bulyanhulu are not the same
# claim, and a model scored on only one of them is being credited with the
# other.
#
# The sample is tiny per sheet (Tanzania 18 deposits, 3 gold-bearing) and the
# script says so rather than printing a lift: three points cannot distinguish
# 1.0 from 3.0, and a number computed from them would be quoted anyway.
MIN_N = 8


def usgs_sheets(sheets=("sudan", "car", "tanzania")):
    dep = json.load(open(USGS))["features"]
    pts = [(Point(*f["geometry"]["coordinates"]), f["properties"]) for f in dep]
    out = {}
    print("\nUSGS MAJOR DEPOSITS - the industrial-scale control")
    for sh in sheets:
        try:
            units = json.load(
                open(f"data/geomaps/{sh}_units.geojson"))["features"]
        except FileNotFoundError:
            print(f"  {sh}: units file absent, skipped")
            continue
        geoms = [shape(f["geometry"]) for f in units]
        props = [f["properties"] for f in units]
        land = unary_union(geoms)
        tree = STRtree(geoms)
        areas = np.array([p.get("area_km2") or 0.0 for p in props])

        def at(p):
            for i in tree.query(p):
                if geoms[i].contains(p):
                    return int(i)
            return None

        inside = [(p, q) for p, q in pts if land.contains(p)]
        gold = [(p, q) for p, q in inside if "Gold" in q["commodity"]]
        rec = {"deposits": len(inside), "gold": len(gold)}
        print(f"  {sh}: {len(inside)} deposits on the sheet, "
              f"{len(gold)} gold-bearing")
        if len(gold) < MIN_N:
            # Not a zero and not a lift: an n this small has no resolving
            # power, and a number printed here would be quoted as one.
            rec["verdict"] = "sample too small"
            names = ", ".join(q["dep_name"] if "dep_name" in q else q["name"]
                              for _, q in gold)
            print(f"    n<{MIN_N}: no lift computed{' (' + names + ')' if names else ''}")
            out[sh] = rec
            continue
        W = {}
        for p in props:
            w = 0
            for a in p.get("affinity") or []:
                if a["commodity"] == "gold":
                    w = a["weight"]
            W[p["code"]] = w
        idx = [i for i in (at(p) for p, _ in gold) if i is not None]
        for mw in (1, 2, 3):
            sel = [c for c, w in W.items() if w >= mw]
            ar = sum(a for p, a in zip(props, areas)
                     if p["code"] in sel) / areas.sum()
            cap = sum(1 for i in idx if W[props[i]["code"]] >= mw) / len(idx)
            rec[mw] = {"capture": cap, "area_share": ar,
                       "lift": cap / ar if ar else float("nan")}
            print(f"    units w_gold>={mw}: capture {cap:>6.1%} "
                  f"area {ar:>6.1%} lift {cap/ar if ar else float('nan'):>5.2f}")
        out[sh] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--continental", action="store_true",
                    help="also score JRC AKP continental layers vs IPIS DRC")
    args = ap.parse_args()

    cat = catalogue(SHEET)
    lith = {c["code"]: c.get("lith") or "mixed" for c in cat["classes"]}
    rules = {r["pair"]: r for r in cat["std"]["contact_rules"]}

    units = json.load(open(f"data/geomaps/{SHEET}_units.geojson"))["features"]
    geoms = [shape(f["geometry"]) for f in units]
    props = [f["properties"] for f in units]
    tree = STRtree(geoms)
    areas = np.array([p.get("area_km2") or 0.0 for p in props])
    land = unary_union(geoms)

    contacts = json.load(
        open(f"data/geomaps/{SHEET}_contacts.geojson"))["features"]

    def unit_weight(p, com):
        for a in p.get("affinity") or []:
            if a["commodity"] == com:
                return a["weight"]
        return 0

    def junction_weight(f, com):
        r = rules.get(pair_key(lith.get(f["properties"]["code_a"], "mixed"),
                               lith.get(f["properties"]["code_b"], "mixed")))
        for a in (r or {}).get("affinity", []):
            if a["commodity"] == com:
                return a["weight"]
        return 0

    def unit_at(x, y):
        pt = Point(x, y)
        for i in tree.query(pt):
            if geoms[i].contains(pt):
                return int(i)
        return None

    # --- the occurrence list -------------------------------------------
    rows = [r for r in csv.DictReader(open(IPIS)) if r.get("longitude")]

    def flagged(r, com):
        return (r.get(FLAGS[com]) or "0").strip() in ("1", "1.0")

    sites, only = {}, {}
    for com in FLAGS:
        other = [c for c in FLAGS if c != com][0]
        pts = [(Point(float(r["longitude"]), float(r["latitude"])), r)
               for r in rows if flagged(r, com)]
        sites[com] = [p for p, _ in pts if land.contains(p)]
        only[com] = [p for p, r in pts
                     if land.contains(p) and not flagged(r, other)]

    rng = np.random.default_rng(SEED)
    minx, miny, maxx, maxy = land.bounds
    rand = []
    while len(rand) < N_RANDOM:
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if land.contains(p):
            rand.append(p)

    out = {"sheet": SHEET, "near_km": NEAR_KM, "n_random": N_RANDOM,
           "sites": {c: len(v) for c, v in sites.items()},
           "sites_exclusive": {c: len(v) for c, v in only.items()},
           "units": {}, "junctions": {}}

    print(f"sheet {SHEET}: {len(units)} units, {len(contacts)} contact pairs")
    for c in FLAGS:
        print(f"  IPIS {c}: {len(sites[c])} sites in-sheet "
              f"({len(only[c])} not also flagged for the other)")

    # --- UNITS ----------------------------------------------------------
    print("\nUNITS - capture of sites vs share of mapped area")
    print(f"{'commodity':>9} {'w>=':>4} {'capture':>8} {'area':>7} {'lift':>6}")
    for com in FLAGS:
        idx = [unit_at(p.x, p.y) for p in sites[com]]
        idx = [i for i in idx if i is not None]
        W = np.array([unit_weight(p, com) for p in props])
        rec = {}
        for mw in (1, 2, 3):
            sel = W >= mw
            area = areas[sel].sum() / areas.sum() if areas.sum() else 0.0
            cap = (sum(1 for i in idx if W[i] >= mw) / len(idx)) if idx else 0.0
            lift = cap / area if area else float("nan")
            rec[mw] = {"capture": cap, "area_share": area, "lift": lift,
                       "n_units": int(sel.sum())}
            print(f"{com:>9} {mw:>4} {cap:>7.1%} {area:>6.1%} {lift:>6.2f}")
        out["units"][com] = rec

    # --- JUNCTIONS ------------------------------------------------------
    print(f"\nJUNCTIONS - P(within {NEAR_KM:g} km of a graded contact)")
    print(f"{'commodity':>9} {'w>=':>4} {'lines':>6} {'sites':>7} {'random':>7}"
          f" {'lift':>6} {'other':>7} {'ctrl':>6}")
    anyline = unary_union([shape(f["geometry"]) for f in contacts])
    d_any = np.array([dist_km(p, anyline) for p in rand])
    out["any_contact_random_near"] = float(np.mean(d_any < NEAR_KM))
    for com in FLAGS:
        other = [c for c in FLAGS if c != com][0]
        rec = {}
        for mw in (1, 2, 3):
            gs = [shape(f["geometry"]) for f in contacts
                  if junction_weight(f, com) >= mw]
            if not gs:
                continue
            L = unary_union(gs)
            near = lambda pts: float(np.mean(
                [dist_km(p, L) < NEAR_KM for p in pts])) if pts else 0.0
            s, r, o = near(only[com]), near(rand), near(only[other])
            med = float(np.median([dist_km(p, L) for p in only[com]]))
            rec[mw] = {"n_lines": len(gs), "site_near": s, "random_near": r,
                       "other_near": o, "lift": s / r if r else float("nan"),
                       "control_ratio": s / o if o else float("inf"),
                       "median_km": med}
            print(f"{com:>9} {mw:>4} {len(gs):>6} {s:>6.1%} {r:>6.1%}"
                  f" {s/r if r else float('nan'):>6.2f} {o:>6.1%}"
                  f" {s/o if o else float('inf'):>6.2f}")
        out["junctions"][com] = rec

    print(f"\nbaseline: a random point on this sheet is within {NEAR_KM:g} km of"
          f" SOME contact {out['any_contact_random_near']:.1%} of the time -"
          " proximity to a line only means something graded.")
    out["usgs"] = usgs_sheets()
    if args.continental:
        out["continental"] = continental()
    if args.json:
        # NaN/Infinity are not JSON, and json.dump writes them anyway - which
        # makes the file unreadable to every consumer except Python, including
        # the Go test that pins the shipped scores to it. A ratio with a zero
        # denominator is UNDEFINED, so it ships as null: absent, not zero.
        def jsonable(v):
            if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
                return None
            if isinstance(v, dict):
                return {k: jsonable(x) for k, x in v.items()}
            if isinstance(v, list):
                return [jsonable(x) for x in v]
            return v
        json.dump(jsonable(out), open(args.json, "w"), indent=1, allow_nan=False)
        print("wrote", args.json)


if __name__ == "__main__":
    main()
