#!/usr/bin/env python3
"""The one EASY PIP map: assessment and plan on a single sheet.

The brief was "if people can only have one map on assessment and most important
the plan". So this is NOT a layer dump. Everything drawn here is something the
PIP report or the two-pager makes an argument about, and nothing else is drawn:

  the nested shapes        the park inside a wilderness block inside a grazing
                           zone - the structural finding of section 3b, drawn
                           as a hierarchy (weight, not eleven equal outlines)
  the fire mass            every v5 front 2024-2026 in frame, one hairline each
                           at low alpha, so DENSITY is the quantity - this is
                           the transhumance system the plan must govern
  people                   settlement clusters, area by population: empty
                           interiors, populated rims, the one cattle camp
  the gold flank           top-5% prediction cells, candidates, watchlist
                           villages - imagery targets, labelled as such
  the plan                 ECHO/TANGO sites and focal points with the verdict
                           the assessment gave each, the corridor axis, the
                           1932 road alignment that is gone, the s.24 ask

Every number in the side panel is READ from data/eval/zone_stats.json, the
budget generator and the database - none is typed here (root invariant 2), so
re-running after a boundary edit reprints the sheet rather than inviting a
hand-patch. A layer that measures nothing says so rather than drawing nothing.

    python3 scripts/easypip/build_map.py --out reports/EASY_PIP_MAP_2026-08.png

Writes PNG (300 dpi) and, with --pdf, a vector PDF of the same figure.
"""
import argparse
import json
import math
import sqlite3
import sys
from datetime import date
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pyproj
from pyproj import Geod
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyArrow, Patch, PathPatch, Rectangle
from matplotlib.path import Path as MplPath
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import transform, unary_union
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from plan_zone_stats import read_kml, km2  # noqa: E402  (one KML parser, not two)

DB = ROOT / "db.sqlite3"
AOI = "XSA_Study_Area"
ZONE_JSON = ROOT / "data/eval/zone_stats.json"
GROUPS = ROOT / "data/fire_groups_v5" / f"{AOI}.json"
PRED = ROOT / "data/eval/xsa_mining/prediction.json"
PRED_GEO = ROOT / "data/eval/xsa_mining/prediction.geojson"
WORLD = ROOT / "data/world_countries.geojson"
# The panel's numbers come from the SAME file the PIP report and the two-pager
# read. That is the whole point: a map whose legend is typed by hand is a third
# opinion, and this project already had two documents disagreeing about the
# size of the park.
FACTS = ROOT / "data/eval/pip_facts.json"

# Frame: the union of every proposed shape, padded. Derived, never typed -
# a new KML moves the frame instead of falling off it.
FRAME_PAD_DEG = 0.35

# Page geometry, in inches. The panel measures its own text against
# PANEL_W_IN, so widening the column re-wraps every note instead of letting
# it run off the sheet.
MAP_W_IN = 15.5
PANEL_W_IN = 2.75          # one legend COLUMN; the legend is an inset card in the map's empty east strip
PANEL_SCALE = 0.80           # legend row text = 10.6 × 0.80 ≈ 8.5 pt, the scale-bar size
LEGEND_COMPACT = True      # rows = symbol + a few words; every explanation lives in the report / docs, not on the sheet
LANDSCAPE_ASPECT = 1.414   # A-series landscape: the map frame is widened (east, where the AOI is blank) to this ratio         # every legend font size is multiplied by this (2026-09-06: the map carries the page, the key serves it)

# ---------------------------------------------------------------- palette
# Light theme, matching the report's own SVG maps (srv/templates/globe.html
# _buildParkMapSvgUncached): cream paper, green boundary, red fire, orange
# settlements, magenta clearing, grey roads, blue rivers.
PAPER = "#fdfdfa"
INK = "#2b2b2b"
MUTED = "#6b6b6b"
FAINT = "#d8d8d2"
GREEN = "#1a7a3a"          # the proposed park - the subject of the sheet
GREEN_W = "#7fae8b"        # wilderness blocks - the frame around it
GREEN_E = "#2f7f6f"        # Southern NP - existing, someone else's ground
USER_GREY = "#8c8c86"      # planner sheet: every hand-drawn boundary, one quiet colour
TAN = "#b08d57"            # pastoral / grazing zones
RED = "#c62828"            # fire
ORANGE = "#e08a1e"         # settlements
GOLD = "#8a6d1f"           # the gold flank
BLUE = "#7cb8e8"           # rivers
PLAN = "#1f4e9c"           # the plan's own furniture: sites, axis, asks


def eqa():
    return pyproj.Transformer.from_crs(4326, "+proj=cea", always_xy=True).transform


def fmt(n):
    return f"{n:,.0f}"


# ------------------------------------------------------------------ inputs
def load_zones(kml_dir):
    """Same parse as plan_zone_stats: a Placemark is the unit, not a file."""
    zones = {}
    for p in sorted(Path(kml_dir).glob("*.kml")):
        for nm, geom, kind, lab in read_kml(p):
            key = nm if nm not in zones else f"{nm} [{p.stem}]"
            zones[key] = dict(geom=geom, kind=kind, file=p.name,
                              area_km2=round(km2(geom), 0), label_km2=lab)
    if not zones:
        sys.exit(f"no KML zones in {kml_dir}")
    return zones


# Which drawn role each shape plays. Keys are substrings of the Placemark name
# so a renamed file still lands; an unmatched shape is drawn as 'other' and
# LISTED in the caption, never silently dropped.
ROLES = [
    ("Pongo-Wau-Numatinna", "park"),
    ("Southern NP", "existing"),
    ("Wilderness", "wilderness"),
    ("headwaters", "wilderness"),
    ("pâturage", "grazing"),
    ("paturage", "grazing"),
    ("Ecological-Corridor", "pin"),
]


def role_of(name):
    for frag, role in ROLES:
        if frag.lower() in name.lower():
            return role
    return "other"


def short_name(name):
    """Human label for a shape, from its own Placemark name."""
    n = name.split("[")[0].strip()
    for junk in ("_1587922ha",):
        n = n.replace(junk, "")
    n = n.replace("zone-pâturage-durable_", "").replace("zone-paturage-durable_", "")
    n = n.replace("Ecological-Corridor_Wau-SouthernNP_", "corridor pin ")
    n = n.replace("_", " ")
    # strip a trailing area claim ("Boro Wilderness 3,180km2")
    import re
    n = re.sub(r"\s*[\d',\.]+\s*(km2|sqkm|ha)\s*$", "", n, flags=re.I)
    return n.strip()


def load_stats():
    if not ZONE_JSON.exists():
        sys.exit(f"{ZONE_JSON} missing - run scripts/plan_zone_stats.py first")
    return json.load(open(ZONE_JSON))


def load_fire(frame):
    """v5 fronts whose path enters the frame, as polylines + their type mix."""
    if not GROUPS.exists():
        return [], {}, "unmeasured: fire_groups_v5 missing"
    x0, y0, x1, y1 = frame
    segs, types, years = [], Counter(), Counter()
    for g in json.load(open(GROUPS)):
        tr = g.get("trajectory") or []
        if len(tr) < 2:
            continue
        pts = [(p[0], p[1]) for p in tr]
        if not any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in pts):
            continue
        segs.append(pts)
        types[g.get("group_type") or "unclassified"] += 1
        years[g.get("year")] += 1
    return segs, dict(types.most_common()), dict(sorted(years.items()))


def load_settlements(frame):
    x0, y0, x1, y1 = frame
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        """SELECT lat, lon, population_est, nearest_place, classification, persistence
           FROM park_settlements
           WHERE park_id = ? AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?""",
        (AOI, x0, x1, y0, y1)).fetchall()
    con.close()
    return rows


def load_rivers(frame, min_order):
    """Named trunk rivers only - the map is about ground, not hydrography."""
    x0, y0, x1, y1 = frame
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        """SELECT geojson FROM park_rivers_hydro
           WHERE park_id = ? AND stream_order >= ?
             AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?""",
        (AOI, min_order, x0, x1, y0, y1)).fetchall()
    con.close()
    out = []
    for (gj,) in rows:
        if not gj:
            continue
        try:
            g = json.loads(gj)
        except Exception:
            continue
        cs = g.get("coordinates") or []
        if g.get("type") == "LineString":
            out.append([(c[0], c[1]) for c in cs])
        elif g.get("type") == "MultiLineString":
            out += [[(c[0], c[1]) for c in part] for part in cs]
    return out


def load_gold(reach):
    """Model output on the ground the plan is asking to close, and only there.

    The prediction covers the whole 481,567 km2 study area; drawn frame-wide it
    is a rash of several hundred squares that says nothing. What section 7 and
    action 3 argue about is specific: which of the PROPOSED shapes and their
    rims carry exposure. So the layer is clipped to `reach` (the zone union
    plus its rim) and the legend says the clip out loud - a reader must not
    read "no square here" as "the model scored this ground low" when the truth
    is "we did not draw outside the ask".

    These are imagery targets with a measured, modest skill, never mines
    (root invariant 12).
    """
    if not (PRED.exists() and PRED_GEO.exists()):
        return None, "unmeasured: prediction outputs missing"
    pred = json.load(open(PRED))
    feats = json.load(open(PRED_GEO))["features"]
    pr = prep(reach)

    def keep(lon, lat):
        return pr.contains(Point(lon, lat))

    top5 = [(f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1])
            for f in feats if f["properties"].get("tier") == "top05"]
    skill = (pred.get("composite_skill") or [{}])
    sk = next((s for s in skill if s.get("top_frac") == 0.2), skill[0])
    return dict(
        top5=[p for p in top5 if keep(*p)],
        candidates=[(c["lon"], c["lat"], c.get("character"))
                    for c in pred.get("candidates", []) if keep(c["lon"], c["lat"])],
        watchlist=[(w["lon"], w["lat"], w.get("name"))
                   for w in pred.get("abandoned_village_gold_watchlist", {}).get("places", [])
                   if keep(w["lon"], w["lat"])],
        anchors=[(a["lon"], a["lat"], a.get("source")) for a in pred.get("anchors", [])
                 if a.get("lat") is not None and keep(a["lon"], a["lat"])],
        skill=sk,
    ), None


def load_borders(frame):
    if not WORLD.exists():
        return []
    x0, y0, x1, y1 = frame
    box = Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    out = []
    for f in json.load(open(WORLD))["features"]:
        g = shape(f["geometry"])
        if not g.intersects(box):
            continue
        out.append((f["properties"].get("iso3"), g.intersection(box)))
    return out


# ---------------------------------------------------------------- the plan
# The ECHO/TANGO sites and Focal Points ARE the plan's geography, and the
# assessment gave each a verdict. Coordinates and verdicts come from
# zone_stats.json's plan_sites block and the assessment's section 5 table;
# the people/cluster counts beside them are read from the database at draw
# time, never typed. `approx` marks the three sites whose position the
# assessment says must be verified on the ground.
SITE_VERDICT = {
    "Boro-Medina":     ("seasonal", "seasonal post; twin with Raga"),
    "Raga":            ("anchor", "the only real town in the north-west"),
    "Deim Zubeir":     ("anchor", "corridor gate; SSWS post exists"),
    "Ali Golo":        ("seasonal", "almost nobody: outreach, not a station"),
    "Faraj Allah":     ("seasonal", "scattered hamlets, no centre"),
    "Raffili Mission": ("seasonal", "empty: nearest settlement 15.8 km"),
    "Nagero":          ("anchor", "small but real; SE approach"),
    "M'Bittima":       ("seasonal", "corridor's south gate"),
    "Wau":             ("focal", "herder leadership seated here"),
    "Tambura":         ("focal", "dry-season herding circuit"),
}
APPROX = ("Boro-Medina", "Ali Golo", "Faraj Allah")

# GEOD for the "who can no site reach" measurement below.
GEOD = Geod(ellps="WGS84")


def site_rows(st):
    """Plan sites with their verdicts. Position from zone_stats' plan_sites."""
    out = []
    for key, v in st["plan_sites"].items():
        base = key.split("(")[0].strip()
        kind = "focal" if "Focal" in key else "echo"
        verdict, why = SITE_VERDICT.get(base, ("anchor", ""))
        out.append(dict(name=base, lat=v["lat"], lon=v["lon"], kind=kind,
                        verdict=verdict, why=why, approx=base in APPROX))
    return out


def unserved_belt(setl, sites, reach, min_pop=2000, reach_km=40.0):
    """Towns beside the proposed area that no site in the plan can reach.

    Derived, not typed, and scoped twice on purpose.

    The first version looked for "the boom towns north-east of the park" in a
    hand-drawn lat/lon box and labelled the winner with its `nearest_place`,
    which returned "Busseri River" - these towns are unnamed in the gazetteer
    and that column holds the nearest FEATURE, not a settlement name. It would
    have put a river on the map as a town.

    The second version dropped the box and asked "who is more than 60 km from
    every site", which matched 45 towns and 734,579 people spread over the
    whole 480,000 km2 study area - a number that sounds like a damning finding
    and is really just a description of South Sudan. A filter that matches
    almost everything is not a finding (root invariant 1).

    So the question is asked at the scale the plan is answerable for: of the
    people living beside the ground being proposed - inside `reach`, the same
    union-plus-rim used everywhere else in this analysis - which towns of
    `min_pop` or more sit further than `reach_km` from every site in the plan?
    Both thresholds and the resulting counts are reported to the reader so the
    scope travels with the claim.
    """
    pr = prep(reach)
    far = []
    for lat, lon, pop, place, cls, pers in setl:
        if not pop or pop < min_pop:
            continue
        if not pr.contains(Point(lon, lat)):
            continue
        d = min(GEOD.inv(s["lon"], s["lat"], lon, lat)[2] / 1000 for s in sites)
        if d >= reach_km:
            far.append((lon, lat, pop))
    if not far:
        return None
    return dict(towns=far, people=int(sum(t[2] for t in far)),
                clusters=len(far), min_pop=min_pop, reach_km=reach_km)

def load_density(pdir):
    """Print-scale LOD: the built-up km² and reviewed-clearing km² per 2 km cell that the planner's cells() computed
    (state.pkl). At this sheet's scale a settlement footprint or a GLAD patch is 0.7 px — the sub-pixel stipple lod.md
    warns about — so the honest print layer is DENSITY, not polygons. Returns dict(built=2D km², clear=2D km²,
    extent=(x0,x1,y0,y1) in lon/lat, tr) or None if the planner has not been built."""
    import pickle
    sp = pdir / "state.pkl"
    if not sp.exists():
        return None
    import plan_conservancy_units as P   # for Grid unpickling
    import __main__
    __main__.Grid = P.Grid
    st = pickle.load(open(sp, "rb")); G = st["G"]; C = G.C
    # re-project the CEA grid to lon/lat by nearest sampling onto a lon/lat grid of the same cell count
    inv = pyproj.Transformer.from_crs(P.CEA, 4326, always_xy=True).transform
    xs = G.x0 + (np.arange(G.w) + 0.5) * G.res; ys = G.y1 - (np.arange(G.h) + 0.5) * G.res
    lon_edges = inv(np.array([G.x0, G.x0 + G.w * G.res]), np.array([ys[0], ys[0]]))[0]
    lat_edges = inv(np.array([xs[0], xs[0]]), np.array([G.y1, G.y1 - G.h * G.res]))[1]
    # CEA: lon is linear in x; lat is not linear in y → resample rows onto an even lat grid
    lat_rows = inv(np.full(G.h, xs[0]), ys)[1]
    even = np.linspace(lat_rows[0], lat_rows[-1], G.h)
    idx = np.abs(lat_rows[None, :] - even[:, None]).argmin(1)
    out = {k: np.asarray(C[k])[idx, :] for k in ("built", "clear", "clear20")}
    out["clusters"] = np.asarray(C["clusters"])[idx, :]
    out["extent"] = (float(lon_edges[0]), float(lon_edges[1]), float(lat_edges[1]), float(lat_edges[0]))
    out["cell_km"] = G.res / 1000
    return out


def load_teams(pdir):
    tp = pdir / "teams.geojson"
    if not tp.exists():
        return []
    return [dict(f["properties"], lon=f["geometry"]["coordinates"][0], lat=f["geometry"]["coordinates"][1])
            for f in json.load(open(tp))["features"]]


def load_deploy(pdir, arg):
    """plan_deploy.py output: footprints (served zones + 25 km disc per team) and team points, or None."""
    if arg == "" or pdir is None:
        return None
    fp = Path(arg) if arg != "auto" else pdir / "deploy_footprint.geojson"
    if not fp.exists():
        return None
    tp = fp.with_name(fp.name.replace("_footprint", "_teams"))
    foot = [dict(f["properties"], geom=shape(f["geometry"])) for f in json.load(open(fp))["features"]]
    teams = [dict(f["properties"], lon=f["geometry"]["coordinates"][0], lat=f["geometry"]["coordinates"][1])
             for f in json.load(open(tp))["features"]] if tp.exists() else []
    for t in foot + teams:
        t["year"] = int(t.get("year") or 2)
        t["place_short"] = str(t.get("place", "")).replace("near ", "").split(" (")[0].replace("*", "").strip()
    return dict(footprints=foot, teams=teams, path=fp)


def solver_raster(pdir):
    """The solver's per-pixel class + intensity rasters (solve_lab.npy / solve_intensity.npy on the planner's CEA grid),
    resampled onto an even lon/lat row grid so imshow can place them. Returns dict(lab, inten, extent) or None."""
    lab_p, int_p, st_p = pdir / "solve_lab.npy", pdir / "solve_intensity.npy", pdir.parent / "conservancy_units" / "state.pkl"
    if not (lab_p.exists() and int_p.exists() and st_p.exists()):
        return None
    import pickle
    sys.path.insert(0, str(ROOT / "scripts"))
    import plan_conservancy_units as P; import __main__ as _m; _m.Grid = P.Grid
    G = pickle.load(open(st_p, "rb"))["G"]; lab = np.load(lab_p); inten = np.load(int_p)
    lon0, _ = P.INV(G.x0, G.y1); lon1, _ = P.INV(G.x0 + G.w * G.res, G.y1)
    lats = np.array([P.INV(G.x0, G.y1 - (r + 0.5) * G.res)[1] for r in range(G.h)])
    ylin = np.linspace(lats[0], lats[-1], G.h * 2); idx = np.clip(np.searchsorted(-lats, -ylin), 0, G.h - 1)
    return dict(lab=lab[idx], inten=inten[idx], extent=(lon0, lon1, lats[-1], lats[0]), G=G)


def corridor_axis(st):
    """The NW-SE spine, drawn from the two corridor pins the authors sent.

    A pin cannot be gazetted (section 3b), so the map draws the AXIS the pins
    encode and labels it as an ask for a polygon, rather than drawing two
    discs as though they were the corridor.
    """
    pins = [(z["bounds"][0] + z["bounds"][2]) / 2 for n, z in st["zones"].items()
            if z.get("kind") == "marker"]
    lats = [(z["bounds"][1] + z["bounds"][3]) / 2 for n, z in st["zones"].items()
            if z.get("kind") == "marker"]
    if len(pins) < 2:
        return None
    return list(zip(pins, lats))


# ------------------------------------------------------------------ drawing
def poly_patches(geom, **kw):
    """Matplotlib patches for a (Multi)Polygon, holes honoured."""
    geoms = geom.geoms if geom.geom_type.startswith("Multi") else [geom]
    out = []
    for g in geoms:
        if g.is_empty:
            continue
        verts, codes = [], []
        for ring in [g.exterior] + list(g.interiors):
            cs = list(ring.coords)
            verts += cs
            codes += [MplPath.MOVETO] + [MplPath.LINETO] * (len(cs) - 2) + [MplPath.CLOSEPOLY]
        out.append(PathPatch(MplPath(verts, codes), **kw))
    return out


# ------------------------------------------------------- furniture (insets)
def draw_graticule(ax, frame, kx, step=1.0):
    """Whole-degree lines, ticked in the margin.

    Every coordinate in the PIP and the two-pager is decimal degrees; without
    a graticule the reader cannot put "8.41 N 27.54 E" on the sheet.
    """
    x0, y0, x1, y1 = frame
    for lon in np.arange(math.ceil(x0), x1, step):
        ax.plot([lon, lon], [y0, y1], color="#c9c9c2", lw=0.5, ls=(0, (1, 4)),
                zorder=1.0)
        ax.text(lon, y0 + (y1 - y0) * 0.004, f"{lon:g}\u00b0E", fontsize=7.5,
                color="#a5a59d", ha="center", va="bottom", zorder=1.0)
    for lat in np.arange(math.ceil(y0), y1, step):
        ax.plot([x0, x1], [lat, lat], color="#c9c9c2", lw=0.5, ls=(0, (1, 4)),
                zorder=1.0)
        ax.text(x0 + (x1 - x0) * 0.003, lat, f"{lat:g}\u00b0N", fontsize=7.5,
                color="#a5a59d", ha="left", va="bottom", zorder=1.0)


# --------------------------------------------------------------- labelling
# Eight candidate offsets around a marker, in points, best first: right of
# the mark reads most naturally, then left, then above/below.
LABEL_OFFSETS = [(11, 5), (-11, 5), (11, -13), (-11, -13),
                 (0, 13), (0, -20), (20, 0), (-20, 0)]


def place_labels(fig, ax, labels, avoid=(), marker_pad_px=9.0, reserved=()):
    """Place point labels so none overlaps another label or a marker.

    A map whose labels collide is not a dense map, it is a wrong one: the
    reader cannot tell which name belongs to which dot. Placement is greedy -
    longest label first, each taking the first candidate offset whose rendered
    box is free. Nothing is dropped: if every candidate collides the least-bad
    one is used, because a missing name reads as "no site here" (root
    invariant 1) and a crowded name does not.
    """
    fig.canvas.draw()  # a renderer must exist before any extent is measurable
    rend = fig.canvas.get_renderer()
    taken = list(reserved)

    def hit(a, b, pad=2.0):
        return not (a[2] + pad < b[0] or b[2] + pad < a[0]
                    or a[3] + pad < b[1] or b[3] + pad < a[1])

    for lon, lat in avoid:  # marker keep-out squares, in display pixels
        px, py = ax.transData.transform((lon, lat))
        taken.append((px - marker_pad_px, py - marker_pad_px,
                      px + marker_pad_px, py + marker_pad_px))

    for i in sorted(range(len(labels)), key=lambda i: -len(labels[i][2])):
        lon, lat, txt, col, sz, wt = labels[i]
        best, best_cost, best_box = LABEL_OFFSETS[0], None, None
        for k, (dx, dy) in enumerate(LABEL_OFFSETS):
            t = ax.annotate(txt, (lon, lat), xytext=(dx, dy),
                            textcoords="offset points", fontsize=sz,
                            ha="left" if dx >= 0 else "right",
                            va="bottom" if dy >= 0 else "top")
            bb = t.get_window_extent(renderer=rend)
            t.remove()
            box = (bb.x0, bb.y0, bb.x1, bb.y1)
            cost = sum(1 for b in taken if hit(box, b)) * 100 + k
            if best_cost is None or cost < best_cost:
                best, best_cost, best_box = (dx, dy), cost, box
            if cost < 100:
                break
        dx, dy = best
        ax.annotate(txt, (lon, lat), xytext=(dx, dy),
                    textcoords="offset points", fontsize=sz, color=col,
                    weight=wt, zorder=6.0,
                    ha="left" if dx >= 0 else "right",
                    va="bottom" if dy >= 0 else "top",
                    path_effects=[pe.withStroke(linewidth=3.2,
                                                foreground="white")])
        taken.append(best_box)
    return taken


def draw_area_labels(fig, ax, entries):
    """Shape names, centred in their own polygon, returned as keep-out boxes.

    These are placed FIRST and never moved: a zone name belongs inside its
    zone. The site labels then route around them, which is the right
    precedence - a reader can find the park without its caption, but not a
    station without its name.
    """
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    boxes = []
    for lon, lat, txt, col, sz in entries:
        t = ax.text(lon, lat, txt, fontsize=sz, color=col, ha="center",
                    va="center", weight="bold", alpha=0.9, zorder=3.6,
                    path_effects=[pe.withStroke(linewidth=3.4,
                                                foreground=PAPER)])
        bb = t.get_window_extent(renderer=rend)
        boxes.append((bb.x0, bb.y0, bb.x1, bb.y1))
    return boxes


def draw_scalebar(ax, frame, kx, bar_km, x_frac=0.035):
    """Chequered bar: readable as a ruler, not just as a length."""
    x0, y0, x1, y1 = frame
    bx = x0 + (x1 - x0) * x_frac
    by = y0 + (y1 - y0) * 0.042
    bw = bar_km / 111.0 / kx
    hh = (y1 - y0) * 0.006
    n = 4
    for i in range(n):
        ax.add_patch(Rectangle((bx + i * bw / n, by), bw / n, hh,
                               facecolor=INK if i % 2 == 0 else PAPER,
                               edgecolor=INK, linewidth=0.7, zorder=6.2))
    for i in (0, n // 2, n):
        ax.text(bx + i * bw / n, by - hh * 1.9, f"{bar_km * i // n:g}",
                fontsize=8.5, color=INK, ha="center", va="top", zorder=6.2)
    ax.text(bx + bw + (x1 - x0) * 0.006, by, "km", fontsize=9.5, color=INK,
            ha="left", va="bottom", zorder=6.2)


def draw_north(ax, frame, x_frac=0.965, y_frac=0.10):
    """A slender needle, dark east half / paper west half, N set above it — the
    same ink and point size as the scale bar so the two read as one instrument."""
    from matplotlib.patches import Polygon as MPoly
    x0, y0, x1, y1 = frame
    cx, cy = x0 + (x1 - x0) * x_frac, y0 + (y1 - y0) * y_frac
    h = (y1 - y0) * 0.055; w = h * 0.28
    tip, base = (cx, cy + h), cy
    ax.add_patch(MPoly([tip, (cx - w, base), (cx, base + h * 0.28)], closed=True, facecolor=PAPER, edgecolor=INK, linewidth=0.8, zorder=6.2))
    ax.add_patch(MPoly([tip, (cx + w, base), (cx, base + h * 0.28)], closed=True, facecolor=INK, edgecolor=INK, linewidth=0.8, zorder=6.2))
    ax.text(cx, cy + h + (y1 - y0) * 0.006, "N", fontsize=9.5, weight="bold", color=INK, ha="center", va="bottom", zorder=6.2)


# ------------------------------------------------------------------- panel
# The panel is built in two passes: `panel_items` returns a list of drawing
# instructions each of which knows its own HEIGHT IN INCHES, and only then is
# the figure sized to fit. The first cut of this used figure-fractions with
# hand-tuned gaps, and the column ran off the bottom of the sheet the moment
# a legend row was added - the layout depended on a page height that had been
# guessed. Measuring first means content decides the page, and adding a line
# never requires re-tuning the ones below it.
PT = 1 / 72.0  # inches per point


ZONE_C = {"core": "#1b5e20", "wilderness": "#4f7a5c", "community": "#6a2c8f", "corridor": "#b3261e"}
ZONE_FILL = {"core": "#2e7d32", "wilderness": "#7fae8b", "community": "#8e5bb0", "corridor": "#d4574d"}
TEAM_C = "#0b7285"
DASHDOT = (0, (5, 2.2, 1.2, 2.2))     # the one dash-dot used for every "context, not the subject" outline


def panel_items(st, fire, sites, belt, unmatched, rim_km, gold_clip_km,
                date, planner_n=0, deploy=None):
    """Every row of the right-hand column, as (height_inches, render) pairs.

    Read top to bottom this is the argument of the whole PIP: what is here,
    what threatens it, what we propose, what it costs. Every figure comes from
    data/eval/pip_facts.json - the same file the report and the two-page
    summary read - so the three cannot disagree. None is typed here (root
    invariant 2), and a missing measurement prints "unmeasured" rather than a
    zero (root invariant 1).
    """
    if not FACTS.exists():
        sys.exit(f"{FACTS} missing - run scripts/easypip/pip_facts.py first")
    F = json.load(open(FACTS))
    P, G, C = F["park"], F["gold"], F["corridor"]
    SH, FI = F["shapes"], F["fire"]

    items = []

    # -------------------------------------------------- the spacing system
    # One base unit, and every gap is a multiple of it. The first draft tuned
    # each pad by eye and the column drifted out of rhythm the moment a note
    # wrapped to a second line. Column geometry is derived from PANEL_W_IN, so
    # a wider sheet re-wraps instead of overflowing (root invariant 2).
    SP = 0.055 * PANEL_SCALE   # inches: the vertical unit
    SYM_X = 0.030              # symbol centre, in axes fraction (0..1)
    TXT_X = 0.082              # label / note left edge
    LEAD = 1.38                # line leading

    # Mean advance width of DejaVu Sans, measured once rather than guessed:
    # 0.58 em over mixed-case prose. A wrap width that is optimistic by 10%
    # does not look tight, it runs off the sheet.
    EM = 0.58

    def wrap_chars(size, x=TXT_X):
        """How many characters fit on one line at this size and indent."""
        avail_in = PANEL_W_IN * (1.0 - x) - 0.12
        return max(24, int(avail_in / (EM * size * PT)))

    def add(h, fn, tag=""):
        items.append((h, fn, tag))

    def text(txt, size=11.2, color=INK, weight="normal", x=0.0, style="normal",
             lead=LEAD, gap=SP, wrap=True, tag=""):
        if LEGEND_COMPACT and tag == "blurb":
            return 0.0
        size *= PANEL_SCALE
        if wrap:
            import textwrap as _t
            wc = wrap_chars(size, x) if tag != "footer" else int(MAP_W_IN / (EM * size * PT))
            txt = "\n".join("\n".join(_t.wrap(p, wc) or [""])
                            for p in txt.split("\n"))
        n = txt.count("\n") + 1
        h = n * size * PT * lead + gap
        add(h, lambda ax, y, _t=txt: ax.text(
            x, y, _t, fontsize=size, color=color, weight=weight, style=style,
            va="top", ha="left", transform=ax.transData, linespacing=lead), tag)
        return h

    def head(txt, color=INK):
        """Section head: air above, hairline below, air under the line."""
        add(SP * (1.2 if LEGEND_COMPACT else 3.4), lambda ax, y: None, tag="head")
        add(12.2 * PANEL_SCALE * PT * LEAD, lambda ax, y, _t=txt.upper(): ax.text(
            0.0, y, _t, fontsize=12.2 * PANEL_SCALE, color=MUTED, weight="bold", va="top"))
        add(SP * 1.5, lambda ax, y: ax.plot([0, 1], [y + SP * 0.35] * 2,
                                            color="#e2e2dc", lw=0.9,
                                            clip_on=False))

    def key(marker, label, mfc, mec, ms=9, mew=1.4, lw=0, ls="solid",
            alpha=1.0, note="", short=""):
        if LEGEND_COMPACT:
            label, note = (short or label), ""
        """One legend row: the symbol exactly as drawn on the map, then why.

        The note hangs under the label at the same left edge, so the symbol
        column stays a column and the eye can run down it.
        """
        LS, NS = 10.6 * PANEL_SCALE, 9.2 * PANEL_SCALE
        ms *= PANEL_SCALE; lw = lw * PANEL_SCALE if lw else lw
        import textwrap as _t
        nlines = (_t.wrap(note, wrap_chars(NS)) if note else [])
        h = LS * PT * LEAD + len(nlines) * NS * PT * 1.34 + SP * 0.85

        def draw(ax, y):
            yc = y + LS * PT * 0.60
            if lw:
                ax.plot([SYM_X - 0.024, SYM_X + 0.024], [yc, yc], color=mec,
                        lw=lw, ls=ls, alpha=alpha, clip_on=False,
                        solid_capstyle="butt")
            elif ms:
                ax.plot([SYM_X], [yc], marker=marker, ms=ms, mfc=mfc, mec=mec,
                        mew=mew, alpha=alpha, clip_on=False)
            ax.text(TXT_X, y, label, fontsize=LS, color=INK if ms or lw else MUTED, va="top",
                    style="normal" if ms or lw else "italic")
            if nlines:
                ax.text(TXT_X, y + LS * PT * LEAD, "\n".join(nlines),
                        fontsize=NS, color=MUTED, va="top", style="italic",
                        linespacing=1.34)
        add(h, draw)

    # ---------------------------------------------------------------- title
    # The sheet is one of three EASY PIP documents and says so: a map that
    # travels without its report must still name the report it belongs to.
    if not LEGEND_COMPACT: add(10.0 * PANEL_SCALE * PT * LEAD + SP * 0.6,
        lambda ax, y, _d=date: ax.text(
            0.0, y, f"PRIORITY INTERVENTION PLAN  \u00b7  {_d}", fontsize=10.0 * PANEL_SCALE,
            color=MUTED, weight="bold", va="top"))
    text("PONGO\u2013WAU\u2013NUMATINNA", 22, GREEN, "bold", gap=SP * 0.4, tag="blurb")
    text("Proposed national park \u2014 assessment, plan and zoning proposals",
         11.0, INK, gap=SP * 0.6, tag="blurb")
    text(f"Western Bahr el Ghazal & Western Equatoria, South Sudan   \u00b7   "
         f"{fmt(P['area_km2'])} km\u00b2 as the boundary file draws it",
         10.2, MUTED, gap=SP * 0.6, tag="blurb")

    # ---------------------------------------------------------------- legend
    # This is a LEGEND, not a summary. The argument - what the emptiness means,
    # what we propose, what it costs - is the two-page summary's job, and an
    # earlier draft of this panel quietly turned into a second copy of it.
    # A figure appears here only where it tells the reader how to read a mark:
    # how many fronts are in the wash, how far the gold clip extends. Anything
    # a reader would quote rather than use to decode the picture belongs in the
    # text, where it can be qualified.
    head("Boundaries", GREEN)
    if deploy:
        key("s", "Boundaries as drawn by the authors", "none", USER_GREY, lw=1.0, ls=DASHDOT, alpha=0.8,
            short="Drawn by the authors")
        key("s", "Southern NP, gazetted", "none", USER_GREY, lw=1.0, alpha=0.8, short="Southern NP, gazetted")
        for c, w in (("core", "Core"), ("wilderness", "Wilderness"), ("community", "Community"), ("corridor", "Corridor")):
            key("s", w, "none", ZONE_C[c], lw=0.9, ls=DASHDOT, alpha=0.7, short=f"Zoning: {w.lower()}")
    elif planner_n:
        key("s", "Boundaries as drawn by the authors (park, wilderness, grazing, corridor pins)", "none", USER_GREY, lw=1.1, ls=(0, (6, 3)),
            short="Boundaries as drawn by the authors")
        key("s", "Southern National Park \u2014 already gazetted (tinted)", "none", USER_GREY, lw=1.1, short="Southern NP, gazetted (tinted)")
    else:
      key("s", "The proposed park", "none", GREEN, lw=3.4)
      key("s", "Wilderness blocks around it \u2014 proposed, not park", "none",
          GREEN_W, lw=1.6, short="Proposed wilderness")
      key("s", "Southern National Park \u2014 already gazetted", "none", GREEN_E,
          lw=2.0, short="Southern NP (gazetted)")
      key("s", "Sustainable-grazing zones", "none", TAN, lw=1.6, ls=(0, (7, 4)), short="Grazing zones (drawn)")
      key("_", "Corridor axis, as the two map pins imply it", "none", PLAN,
          lw=2.4, ls=(0, (5, 2)),
          note=f"we were sent markers {C['pins_apart_km']:g} km apart, "
               f"not a polygon", short="Corridor axis (two pins)")
    if unmatched and not planner_n:
        key("s", "Shape with no assigned role", "none", "#999", lw=1.2,
            ls=(0, (1, 2)), note="; ".join(sorted(set(unmatched))))
    add(SP * 0.9, lambda ax, y: None)
    text(f"{SH['n_shapes']} shapes in {SH['n_files']} files, covering "
         f"{fmt(SH['union_km2'])} km\u00b2 between them. Their areas sum to "
         f"{fmt(SH['sum_of_areas_km2'])} km\u00b2 because they are nested: "
         f"{fmt(SH['overlap_km2'])} km\u00b2 lies under more than one.",
         9.4, MUTED, style="italic", x=TXT_X, gap=SP, tag="blurb")

    head("On the ground", GREEN)
    key("_", "One hairline = one fire front, 2024\u20132026", "none", RED,
        lw=1.4, alpha=0.55,
        note=f"{fmt(len(fire))} of them in frame \u2014 the depth of the wash "
             f"is the density, not one big fire", short="Fire front 2024\u201326")
    if planner_n:
        key("s", "Built-up density, km\u00b2 per 2 km cell (amber wash)", ORANGE, ORANGE, ms=8, mew=0.5, alpha=0.6,
            note="GHSL footprints summed per cell \u2014 a footprint is 0.7 px at this scale, so density is drawn, not shapes; dots are towns \u2265 500 people", short="Built-up, per 2 km cell")
        key("s", "Clearing density, km\u00b2 per 2 km cell (magenta wash)", "#b0186b", "#b0186b", ms=8, mew=0.5, alpha=0.6,
            note="reviewed Hansen/GLAD loss events since 2000, area summed per cell", short="Clearing, per 2 km cell")
    key("o", "Settlement, area \u221d people", ORANGE, "#9a5d00", ms=9, mew=0.5,
        note="a satellite estimate and a lower bound, never a census", short="Town, size \u221d people")
    key("_", "Trunk rivers", "none", BLUE, lw=1.4)

    head("Gold", GOLD)
    key("s", "Top 5% of ground by model score", GOLD, GOLD, ms=9, mew=1.0,
        note=f"drawn only within {gold_clip_km:g} km of the proposed shapes \u2014 "
             f"blank elsewhere means NOT DRAWN, not scored low", short="Gold model top 5 %")
    key("^", "Imagery target \u2014 somewhere to look, never a mine", "none",
        GOLD, ms=9, short="Imagery target")
    key("D", "Reported working (OSM / Crisis Tracker)", GOLD, "#4a3a0a", ms=6,
        mew=0.6, short="Reported working")
    add(SP * 0.9, lambda ax, y: None)
    text(G["verdict"], 9.2, "#8a5a00", style="italic", x=TXT_X, gap=SP, tag="blurb")

    if deploy:
        # the DEPLOYMENT sheet: the zones the teams work are the subject; everything else is context
        n1 = sum(1 for t in deploy["teams"] if t["year"] == 1); n2 = sum(1 for t in deploy["teams"] if t["year"] == 2)
        head("Teams", TEAM_C)
        for c, w in (("community", "Community zone (ECHO)"), ("corridor", "Corridor zone (TANGO)")):
            key("s", w, ZONE_C[c], ZONE_C[c], ms=8, mew=0.6, alpha=0.55, short=w)
        key("s", "Fill depth = evidence for the class (people / herd use)", "none", "none", ms=0, mew=0,
            short="deeper fill = stronger evidence")
        key("*", "Focal point (1 person)", TEAM_C, "white", ms=13, mew=0.8, short="Focal point (1)")
        key("s", "ECHO team (5)", TEAM_C, "white", ms=7.5, mew=0.8, short="ECHO team (5)")
        key("^", "TANGO team (5)", TEAM_C, "white", ms=8.5, mew=0.8, short="TANGO team (5)")
        key("^", "Year-2 team: hollow", "white", TEAM_C, ms=8.5, mew=1.2, short=f"hollow = year 2 ({n1} year 1, +{n2} year 2)")
    elif planner_n:
        # planner layers are drawn only with --planner; the legend says what they are and how sure the machine is
        n_prop = planner_n
        head("Zoning proposals (machine)", "#6a2c8f")
        key("s", "Core \u2014 empty land the rule calls park-grade", "none", "#1b5e20", lw=2.4, short="Core proposal (park-grade)")
        key("s", "Corridor network the herds actually walk (support \u2265 0.5)", "none", "#b3261e", lw=1.4,
            note="one branch per origin\u2013destination bundle of the long transhumance fronts; each labelled from \u2192 to, fronts, onset month", short="Herd corridor branch (walked)")
        key("s", "Community conservancy proposal (s.14)", "none", "#6a2c8f", lw=1.6,
            note="hatched; grown from the village mesh on the park rim (80 km) and from the boom towns, "
                 "never inside the park or Southern NP", short="Conservancy proposal")
        key("_", "Legible mesh \u2014 rivers, swamp edges, ridges, khors, roads, 1930s district lines", "none", "#5a5a5a", lw=0.5, alpha=0.7, short="Legible mesh (rivers, ridges, swamps\u2026)")
        key("*", "Focal point (1 person) \u2014 county / boom town", "#0b7285", "white", ms=15, mew=1.0, short="Focal point (1)")
        key("s", "ECHO team (2) \u2014 in the conservancy's largest village with water", "#0b7285", "white", ms=9, mew=1.0, short="ECHO team (2), in the conservancy")
        key("^", "TANGO team (2) \u2014 where a herd branch meets villages and water", "#0b7285", "white", ms=10, mew=1.0,
            note="placements and their reasons: data/plan_zones/conservancy_units/TEAMS.txt", short="TANGO team (2), on the herd branch")
        add(SP * 0.9, lambda ax, y: None)
        text(f"{n_prop} proposals labelled with hectares, GHSL people and support = share of "
             f"perturbed runs (thresholds \u00b125%) that keep the land inside. Each is measured with the same "
             f"rasters as the drawn zones; the text is in data/plan_zones/conservancy_units/AREAS.txt.",
             9.2, MUTED, style="italic", x=TXT_X, gap=SP, tag="blurb")

    if not deploy:
        head("EASY plan sites", PLAN)
        key("s", "Anchor station \u2014 staffed", PLAN, "white", ms=10, mew=1.3, short="Plan site: anchor")
        key("o", "Seasonal outreach only", "white", PLAN, ms=9, mew=1.9, short="Plan site: seasonal")
        key("*", "Town focal point", PLAN, "white", ms=19, mew=1.2, short="Plan focal point")
    if deploy:
        key("s", "", "none", "none", ms=0, mew=0,
            short=(f"{belt['clusters']} towns, {fmt(belt['people'])} people, >{belt['reach_km']:g} km from a team"
                   if belt else "every town of 2,000+ within 40 km of a team"))
    else: key("o", "Town no site in the plan reaches", "none", "#8a2020",
        ms=11, mew=1.7,
        note=(f"{fmt(belt['people'])} people in {belt['clusters']} towns of "
              f"{fmt(belt['min_pop'])}+ beside the proposed area, all further "
              f"than {belt['reach_km']:g} km from every site"
              if belt else "none: every town of "
                           f"{fmt(2000)}+ beside the area is within reach"), short=("Town >40 km from any team" if deploy else "Town >40 km from any plan site"))
    add(SP * 0.9, lambda ax, y: None)
    text("A tilde after a site name means the assessment could not confirm its "
         "position on the ground.", 9.4, MUTED, style="italic", x=TXT_X,
         gap=SP, tag="blurb")

    # ------------------------------------------------------------ provenance
    # one line under the frame, at the legend's size: read after the map, never competing with it
    text(f"Sources \u00b7 boundaries: {SH['n_files']} KML files as received \u00b7 fire: VIIRS/FIRMS, v5 fronts 2024\u201326 \u00b7 people: GHSL estimate, "
         f"not a count \u00b7 clearing: Hansen/GLAD, verified \u00b7 gold: project model on {G['n_anchors']} known workings \u00b7 "
         f"all figures from data/eval/pip_facts.json \u00b7 {date}",
         7.2 / PANEL_SCALE, MUTED, lead=1.3, gap=0.0, wrap=False, tag="footer")
    return items


def panel_height_in(items):
    return sum(it[0] for it in items)


def draw_panel(fig, items, x_in, w_in, fig_w, fig_h, top_pad=0.10):
    """Render the measured items into a column whose data units are inches.

    The axes is set up so y runs downward in inches from the top of the
    column; every item then draws at its own cursor and the arithmetic in
    panel_items needs no knowledge of the page size.
    """
    h_in = panel_height_in(items) + top_pad * 2
    ax = fig.add_axes([x_in / fig_w, 1 - (top_pad + h_in) / fig_h,
                       w_in / fig_w, h_in / fig_h])
    ax.set_facecolor("none")
    ax.set_xlim(0, 1)
    ax.set_ylim(h_in, 0)
    ax.axis("off")
    y = top_pad
    for h, fn, *_ in items:
        fn(ax, y)
        y += h


def draw_inset_legend(fig, ax_map, items, n_cols=1, col_w_in=PANEL_W_IN, gutter_in=0.28, pad_in=0.18, x_in=0.30, y_in=0.30, anchor="ne"):
    """The legend as a MAP INSET, not a second document beside the map.

    The same measured items (each knows its height in inches) are flowed into n_cols columns, breaking only at section
    heads so a key never splits from its title, balanced so the columns end within one block of each other. The card
    sits in the lower-left of the map frame on a translucent paper panel with a hairline — a report figure keeps its
    margins, and the map fills the sheet. The title block spans the columns."""
    # split: the title block (everything before the first head) spans; the rest is flowed into columns
    footer = [it for it in items if len(it) > 2 and it[2] == "footer"]
    items = [it for it in items if not (len(it) > 2 and it[2] == "footer")]
    first_head = next((i for i, it in enumerate(items) if len(it) > 2 and it[2] == "head"), len(items))
    title, rest = items[:first_head], items[first_head:]
    blocks, cur = [], []
    for it in rest:
        if len(it) > 2 and it[2] == "head" and cur:
            blocks.append(cur); cur = []
        cur.append(it)
    if cur: blocks.append(cur)
    bh = [sum(it[0] for it in b) for b in blocks]
    # balanced greedy fill: each block goes to the column whose height would end up lowest
    cols = [[] for _ in range(n_cols)]; ch = [0.0] * n_cols
    for b, h in zip(blocks, bh):
        k = int(np.argmin(ch)); cols[k].append(b); ch[k] += h
    title_h = sum(it[0] for it in title)
    card_w = pad_in * 2 + n_cols * col_w_in + (n_cols - 1) * gutter_in
    card_h = pad_in * 2 + title_h + max(ch)
    fig_w, fig_h = fig.get_size_inches()
    bb = ax_map.get_position()                       # map axes in figure fraction
    if anchor == "ne":
        x0 = bb.x1 - (x_in + card_w) / fig_w; y0 = bb.y1 - (y_in + card_h) / fig_h
    else:
        x0 = bb.x0 + x_in / fig_w; y0 = bb.y0 + y_in / fig_h
    card = fig.add_axes([x0, y0, card_w / fig_w, card_h / fig_h])
    card.set_xlim(0, card_w); card.set_ylim(card_h, 0); card.axis("off")
    card.add_patch(Rectangle((0, 0), card_w, card_h, facecolor=PAPER, edgecolor="#c9c9c2", linewidth=0.8, alpha=0.96, zorder=0))
    card.add_patch(Rectangle((0, 0), card_w, 0.05, facecolor=GREEN, edgecolor="none", zorder=1))   # a thin brand rule on top
    def column(items_, x_left, y_top):
        # a nested axes per column so the items' 0..1 x-coordinates map onto the column width
        h = sum(it[0] for it in items_) or 0.01
        ax = fig.add_axes([x0 + x_left / fig_w, y0 + (card_h - y_top - h) / fig_h, col_w_in / fig_w, h / fig_h])
        ax.set_facecolor("none"); ax.set_xlim(0, 1); ax.set_ylim(h, 0); ax.axis("off")
        y = 0.0
        for hh, fn, *_ in items_:
            fn(ax, y); y += hh
    column(title, pad_in, pad_in + 0.05)
    for k in range(n_cols):
        flat = [it for b in cols[k] for it in b]
        if flat: column(flat, pad_in + k * (col_w_in + gutter_in), pad_in + 0.05 + title_h)
    # provenance as a footer strip under the map frame — read after the map, never competing with it
    if footer:
        fh = sum(it[0] for it in footer)
        fx = fig.add_axes([bb.x0, bb.y0 - (fh + 0.06) / fig_h, bb.width, fh / fig_h])
        fx.set_facecolor("none"); fx.set_xlim(0, 1); fx.set_ylim(fh, 0); fx.axis("off")
        y = 0.0
        for hh, fn, *_ in footer:
            fn(fx, y); y += hh
    return card_w, card_h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kml-dir", default=str(ROOT / "data/plan_zones"))
    ap.add_argument("--out", default=str(ROOT / "reports/EASY_PIP_MAP_2026-08.png"))
    ap.add_argument("--pdf", action="store_true", help="also write a vector PDF")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--date", default=date.today().isoformat(),
                    help="date stamp printed on the sheet")
    ap.add_argument("--planner", default="",
                    help="overlay scripts/plan_conservancy_units.py output: a conservancies_*.geojson "
                         "(candidates, hatched violet, labelled with ha + people) plus corridor_walked.geojson "
                         "and units.geojson from the same folder if present")
    ap.add_argument("--deploy", default="auto",
                    help="deploy_footprint.geojson from scripts/plan_deploy.py (default: the one beside --planner if it exists; "
                         "'' to switch the deploy sheet off). With it the sheet becomes the DEPLOYMENT map: solver zones as "
                         "quiet dash-dot outlines, the team zones filled by evidence intensity, footprints outlined per year, teams as points")
    a = ap.parse_args()
    deploy = load_deploy(Path(a.planner).parent if a.planner else None, a.deploy)

    zones = load_zones(a.kml_dir)
    st = load_stats()
    S, Z = st["settlements"], st["zones"]
    SR = st["settlements_rim"]
    FD, FT = st["fire_detections"], st["fire_trajectories"]
    rim_km = st["rim_km"]

    # ---- frame from the shapes themselves
    allg = unary_union([z["geom"] for z in zones.values()])
    x0, y0, x1, y1 = allg.bounds
    frame = (x0 - FRAME_PAD_DEG, y0 - FRAME_PAD_DEG,
             x1 + FRAME_PAD_DEG, y1 + FRAME_PAD_DEG)
    x0, y0, x1, y1 = frame
    midlat = (y0 + y1) / 2
    kx = math.cos(math.radians(midlat))
    # report format: widen the frame to a landscape A-series ratio. The extra width goes EAST, where the study
    # area's diagonal edge leaves the frame blank — that strip is where the legend card sits, off the map content.
    need_w = (y1 - y0) * LANDSCAPE_ASPECT / kx
    if need_w > (x1 - x0):
        x1 = x0 + need_w
        frame = (x0, y0, x1, y1)

    fire, fire_types, fire_years = load_fire(frame)
    setl = load_settlements(frame)
    rivers = load_rivers(frame, 6)
    borders = load_borders(frame)

    # The gold layer is clipped to the ground the plan asks to close: the union
    # of the polygons plus their 25 km rim (the same rim zone_stats measures).
    fwd, inv = eqa(), pyproj.Transformer.from_crs(
        "+proj=cea", 4326, always_xy=True).transform
    polys = unary_union([z["geom"] for z in zones.values()
                         if z["kind"] == "polygon"])
    reach = transform(inv, transform(fwd, polys).buffer(rim_km * 1000))
    gold, gold_note = load_gold(reach)
    sites = site_rows(st)
    teams_early = deploy["teams"] if deploy else (load_teams(Path(a.planner).parent) if a.planner else [])
    # with the planner drawn, the proposals' teams ARE sites: a town 10 km from an ECHO team is served, and the
    # "no site within 40 km" belt must be measured against everything the sheet proposes, or it contradicts itself
    belt = unserved_belt(setl, ([] if deploy else sites) + [dict(name=t["place"], lon=t["lon"], lat=t["lat"], approx=False, kind="team") for t in teams_early], reach)
    axis = corridor_axis(st)

    # ---- figure. The panel measures itself first, and the page is then made
    # tall enough to hold whichever column is longer. Sizing the page before
    # measuring the text is what pushed the legend off the sheet.
    unmatched = sorted({short_name(nm) for nm in zones if role_of(nm) == "other"})
    planner_n = 0
    if a.planner:
        planner_n = sum(len(json.load(open(f))["features"]) for f in Path(a.planner).parent.glob("optimize_*.geojson") if "support" not in f.name) or len(json.load(open(a.planner))["features"])
    items = panel_items(st, fire, sites, belt, unmatched, rim_km, rim_km,
                        a.date, planner_n, deploy)
    map_w_deg = (x1 - x0) * kx
    map_h_deg = (y1 - y0)
    MAP_H_IN = MAP_W_IN * map_h_deg / map_w_deg
    FIG_W = MAP_W_IN + 0.24
    FIG_H = MAP_H_IN + 0.24 + 0.30        # + one-line provenance footer under the frame

    fig = plt.figure(figsize=(FIG_W, FIG_H), facecolor=PAPER)
    ax = fig.add_axes([0.12 / FIG_W, 1 - (0.12 + MAP_H_IN) / FIG_H,
                       MAP_W_IN / FIG_W, MAP_H_IN / FIG_H])
    ax.set_facecolor(PAPER)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect(1 / kx)
    for s in ax.spines.values():
        s.set_color("#bbb")
        s.set_linewidth(0.8)
    ax.set_xticks([])
    ax.set_yticks([])

    # 1. countries: fill everything outside the study countries very faintly
    for iso, g in borders:
        for p in poly_patches(g, facecolor="none", edgecolor="#b9b9b2",
                              linewidth=1.1, linestyle=(0, (6, 3)), zorder=1.2):
            ax.add_patch(p)
    for iso, g in borders:
        c = g.representative_point()
        if x0 + 0.25 < c.x < x1 - 0.25 and y0 + 0.25 < c.y < y1 - 0.25:
            ax.text(c.x, c.y, iso, color="#b0b0a8", fontsize=17, weight="bold",
                    ha="center", va="center", zorder=1.3, alpha=0.55)

    # 2. trunk rivers
    if rivers:
        ax.add_collection(LineCollection(rivers, colors=BLUE, linewidths=0.9,
                                         alpha=0.75, zorder=1.6))

    # 2b. PRINT-SCALE LOD: built-up and clearing DENSITY per 2 km cell (planner rasters). Amber = built-up km²,
    #     magenta = reviewed clearing km². A footprint polygon would be 0.7 px here; a density cell is legible,
    #     and it is the same number the planner's rule reads.
    dens = load_density(Path(a.planner).parent) if a.planner else None
    if dens is not None:
        from matplotlib.colors import LinearSegmentedColormap, PowerNorm
        ext = dens["extent"]
        for key, col, vmax in (("clear", "#b0186b", None), ("built", ORANGE, None)):
            arr = np.array(dens[key], float); arr[arr <= 0] = np.nan
            if not np.isfinite(arr).any():
                continue
            vmax = vmax or float(np.nanpercentile(arr, 98))
            cmap = LinearSegmentedColormap.from_list(key, [(1, 1, 1, 0), col])
            ax.imshow(arr, extent=ext, origin="upper", cmap=cmap, norm=PowerNorm(0.5, vmin=0, vmax=vmax),
                      interpolation="nearest", alpha=0.85 if key == "clear" else 0.75, zorder=2.12 if key == "clear" else 2.08, aspect="auto")   # above the fire mass (z 2.0): a cell must not drown in hairlines
            dens[key + "_vmax"] = vmax

    # 3. THE FIRE MASS. One hairline per front at low alpha: the quantity the
    #    reader is meant to take away is DENSITY, not any single path.
    if fire:
        ax.add_collection(LineCollection(fire, colors=RED, linewidths=0.30,
                                         alpha=0.045, zorder=2.0,
                                         capstyle="round"))

    # 4. the shapes, drawn as the hierarchy they are
    order = ["grazing", "wilderness", "existing", "park", "pin", "other"]
    style = {
        "grazing":    dict(fc=TAN, fa=0.055, ec=TAN, lw=1.3, ls=(0, (7, 4)), z=2.4),
        "wilderness": dict(fc=GREEN_W, fa=0.10, ec=GREEN_W, lw=1.4, ls="solid", z=2.6),
        "existing":   dict(fc=GREEN_E, fa=0.13, ec=GREEN_E, lw=1.8, ls="solid", z=2.8),
        "park":       dict(fc=GREEN, fa=0.13, ec=GREEN, lw=3.4, ls="solid", z=3.4),
        "pin":        dict(fc=PLAN, fa=0.07, ec=PLAN, lw=1.2, ls=(0, (2, 2)), z=3.0),
        "other":      dict(fc="#999", fa=0.06, ec="#999", lw=1.0, ls=(0, (1, 2)), z=2.2),
    }
    if deploy:
        # the deployment sheet: what the authors drew is one quiet dash-dot grey at low opacity; the team zones carry the colour
        for r in ("grazing", "wilderness", "park", "pin", "other"):
            style[r] = dict(fc="none", fa=0.0, ec=USER_GREY, lw=1.0, ls=DASHDOT, z=2.3, a=0.55)
        style["existing"] = dict(fc=GREEN_E, fa=0.06, ec=USER_GREY, lw=1.0, ls="solid", z=2.3, a=0.55)
    elif a.planner:
        # the planner sheet is about the machine proposals; what the authors DREW is context and takes one quiet grey
        # style (gazetted Southern NP keeps a faint tint so "already a park" stays legible), one legend row
        for r in ("grazing", "wilderness", "park", "pin", "other"):
            style[r] = dict(fc="none", fa=0.0, ec=USER_GREY, lw=1.1, ls=(0, (6, 3)), z=2.3)
        style["existing"] = dict(fc=GREEN_E, fa=0.08, ec=USER_GREY, lw=1.1, ls="solid", z=2.3)
    for role in order:
        for nm, z in zones.items():
            if role_of(nm) != role:
                continue
            s = style[role]
            for p in poly_patches(z["geom"], facecolor=s["fc"], alpha=s["fa"],
                                  edgecolor="none", zorder=s["z"]):
                ax.add_patch(p)
            for p in poly_patches(z["geom"], facecolor="none", edgecolor=s["ec"],
                                  linewidth=s["lw"], linestyle=s["ls"],
                                  zorder=s["z"] + 0.05, alpha=s.get("a", 1.0)):
                ax.add_patch(p)

    # 4b. PLANNER OVERLAY (opt-in). The machine's answer drawn over the hand's:
    #     unit mesh as hairlines (where the legible edges are), the corridor the
    #     fronts actually walk, and the conservancy candidates with their measure.
    planner_labels = []
    if deploy:
        pdir = Path(a.planner).parent
        zpath = Path(a.planner) if Path(a.planner).name == "zones.geojson" else pdir / "zones.geojson"
        zfeat = json.load(open(zpath))["features"]
        served = {u for t in deploy["footprints"] for u in (t.get("zone_uids") or [])}
        # (i) every solved zone as a quiet dash-dot outline in its class colour — the zoning we found, as context
        for f in zfeat:
            pr = f["properties"]; cls = pr.get("solver_class") or pr.get("cls"); g = shape(f["geometry"])
            if int(pr["uid"]) in served:
                continue
            for p in poly_patches(g, facecolor="none", edgecolor=ZONE_C[cls], linewidth=0.7, linestyle=DASHDOT, zorder=2.5, alpha=0.5):
                ax.add_patch(p)
        # (ii) the zones the teams work: filled by the solver's per-pixel intensity (evidence for the class → opacity).
        #      The fill is the RASTER itself (the 2 km cells the solver decided on), masked to the served zones on the
        #      same grid — one honest pixel edge, no vector clip fighting the cells — and kept light enough that the
        #      fire hairlines, the built-up/clearing washes and the town dots read through it.
        R = solver_raster(pdir)
        served_geoms = {}
        for f in zfeat:
            pr = f["properties"]
            if int(pr["uid"]) in served:
                served_geoms[int(pr["uid"])] = (pr.get("solver_class") or pr.get("cls"), shape(f["geometry"]), pr)
        if R is not None and served_geoms:
            import plan_conservancy_units as P
            G = R["G"]
            zid = G.rasterize([(transform(P.FWD, g), uid) for uid, (cls, g, pr) in served_geoms.items()], fill=0)
            zid = zid[np.clip(np.searchsorted(-np.array([P.INV(G.x0, G.y1 - (r + 0.5) * G.res)[1] for r in range(G.h)]),
                                              -np.linspace(*[P.INV(G.x0, G.y1 - (r + 0.5) * G.res)[1] for r in (0, G.h - 1)], G.h * 2)), 0, G.h - 1)]
            rgba = np.zeros(R["lab"].shape + (4,))
            for uid, (cls, g, pr) in served_geoms.items():
                m = (zid == uid) & (R["lab"] == ("core", "wilderness", "community", "corridor").index(cls) + 1)
                lo, hi = (0.10, 0.34) if cls == "corridor" else (0.12, 0.40)     # the corridor sits ON the fire mass: keep the hairlines legible
                rgba[m, :3] = matplotlib.colors.to_rgb(ZONE_FILL[cls]); rgba[m, 3] = lo + hi * np.clip(R["inten"][m], 0, 1)
            ax.imshow(rgba, extent=R["extent"], origin="upper", interpolation="nearest", zorder=3.05)
        for uid, (cls, g, pr) in served_geoms.items():
            if R is None:
                for p in poly_patches(g, facecolor=ZONE_FILL[cls], alpha=0.22, edgecolor="none", zorder=3.05):
                    ax.add_patch(p)
            for p in poly_patches(g, facecolor="none", edgecolor=ZONE_C[cls], linewidth=1.3, zorder=3.2):
                ax.add_patch(p)
            kha = pr["area_ha"] / 1000
            size = f"{kha/1000:.2f} M ha" if kha >= 1000 else f"{kha:,.0f}k ha"
            ppl = pr['population_est']; ppl_s = f"{ppl/1000:,.0f}k" if ppl >= 10_000 else f"{ppl:,}"
            planner_labels.append((g.representative_point().x, g.representative_point().y,
                                   f"{size} · {ppl_s} ppl", ZONE_C[cls], 7.6, "normal"))
        # (iii) the 25 km discs in deploy_footprint.geojson are a working geometry (reach), not a finding: not drawn.
        #       The served zone is the footprint; the team point and its id say who and when.
    elif a.planner:
        pdir = Path(a.planner).parent
        VIOLET, WALK = "#6a2c8f", "#b3261e"
        upath = pdir / "units.geojson"
        if upath.exists():
            for f in json.load(open(upath))["features"]:
                for p in poly_patches(shape(f["geometry"]), facecolor="none",
                                      edgecolor="#5a5a5a", linewidth=0.35, alpha=0.5, zorder=2.35):
                    ax.add_patch(p)
        cpath = pdir / "corridor_walked.geojson"
        if cpath.exists():
            for f in json.load(open(cpath))["features"]:
                k = f["properties"].get("kind", "")
                ls = "solid" if k == "walked_today" else (0, (4, 3))
                for p in poly_patches(shape(f["geometry"]), facecolor=WALK, alpha=0.06,
                                      edgecolor=WALK, linewidth=1.1, linestyle=ls, zorder=2.45):
                    ax.add_patch(p)
        # optimiser proposals: core (dark green, solid), corridor (red band), community (violet hatch).
        # Any optimize_<class>.geojson in the folder is drawn; the --planner file itself is drawn last.
        OPT_STYLE = {"core": dict(fc="#1b5e20", ec="#1b5e20", hatch=None, lw=2.4),
                     "corridor": dict(fc=WALK, ec=WALK, hatch=None, lw=1.4),
                     "community": dict(fc=VIOLET, ec=VIOLET, hatch="///", lw=1.6),
                     "wilderness": dict(fc="#4f7a5c", ec="#4f7a5c", hatch=None, lw=1.4)}
        # optimize_<class>.geojson and any tagged run optimize_<class>_<tag>.geojson (e.g. community_towns); support files skipped
        files = sorted(f for f in pdir.glob("optimize_*.geojson") if "support" not in f.name)
        files.sort(key=lambda f: next((i for i, k in enumerate(("core", "corridor", "wilderness", "community")) if k in f.name), 9))
        if Path(a.planner) not in files: files.append(Path(a.planner))
        # the SOLVER plan (plan_solver.py) replaces the greedy optimize_* proposals when passed as --planner
        # (…/solver/zones.geojson): its per-pixel intensity raster is drawn as opacity, its zones outlined, budget conservancies C1..n
        if Path(a.planner).name == "zones.geojson" and "solver" in str(pdir):
            files = [Path(a.planner)]
            inten_p, lab_p, st_p = pdir / "solve_intensity.npy", pdir / "solve_lab.npy", pdir.parent / "conservancy_units" / "state.pkl"
            if inten_p.exists() and lab_p.exists() and st_p.exists():
                import pickle, sys as _sys; _sys.path.insert(0, str(ROOT / "scripts"))
                import plan_conservancy_units as _P; import __main__ as _m; _m.Grid = _P.Grid
                _G = pickle.load(open(st_p, "rb"))["G"]; _lab = np.load(lab_p); _int = np.load(inten_p)
                _rgba = np.zeros((_G.h, _G.w, 4))
                for _i, _c in enumerate(("core", "wilderness", "community", "corridor"), 1):
                    _mm = _lab == _i; _rgba[_mm, :3] = matplotlib.colors.to_rgb(OPT_STYLE[_c]["fc"]); _rgba[_mm, 3] = 0.06 + 0.5 * np.clip(_int[_mm], 0, 1)
                # CEA grid → lon/lat image: reproject corners; the grid is axis-aligned in CEA, lon is linear in x and lat ≈ monotone in y, so warp rows
                _lon0, _ = _P.INV(_G.x0, _G.y1); _lon1, _ = _P.INV(_G.x0 + _G.w * _G.res, _G.y1)
                _lats = np.array([_P.INV(_G.x0, _G.y1 - (r + 0.5) * _G.res)[1] for r in range(_G.h)])
                _ylin = np.linspace(_lats[0], _lats[-1], _G.h * 2); _idx = np.clip(np.searchsorted(-_lats, -_ylin), 0, _G.h - 1)
                ax.imshow(_rgba[_idx], extent=(_lon0, _lon1, _lats[-1], _lats[0]), origin="upper", interpolation="nearest", zorder=3.05)
        for fp in files:
            for f in json.load(open(fp))["features"]:
                pr = f["properties"]; g = shape(f["geometry"])
                cls = pr.get("solver_class") or pr.get("cls") or "community"
                if "corridor" in fp.name: cls = "corridor"
                st = OPT_STYLE.get(cls, OPT_STYLE["community"])
                if "solver_class" not in pr:                       # solver zones: the intensity raster is the fill
                    for p in poly_patches(g, facecolor=st["fc"], alpha=0.10, edgecolor="none", zorder=3.1):
                        ax.add_patch(p)
                solver_z = "solver_class" in pr
                for p in poly_patches(g, facecolor="none", edgecolor=st["ec"], linewidth=(0.5 if cls != "corridor" else 0.9) if solver_z else st["lw"],
                                      hatch=None if solver_z else st["hatch"], zorder=3.15):
                    p.set_alpha(0.6 if not solver_z else 0.8); ax.add_patch(p)
                c = g.representative_point()
                if cls == "corridor" and pr.get("from_place"):
                    # a corridor branch is labelled with what operations need: where from, where to, how many herds, when
                    nf = pr.get("bundle_fronts") or pr.get("fronts_long")
                    planner_labels.append((c.x, c.y, f"{pr['from_place'].split(' (')[0]} \u2192 {pr['to_place'].split(' (')[0]} · {nf:,} herds · {pr.get('onset')}", st["ec"], 8.2))
                    continue
                if "solver_class" in pr:                          # solver zone: label only budget conservancies (C<rank>) and core ≥ 300k ha
                    if pr.get("in_budget"): planner_labels.append((c.x, c.y, f"C{pr['rank']} · {pr['area_ha']/1000:,.0f}k ha · {pr['population_est']:,} ppl", st["ec"], 8.2))
                    elif cls == "core" and pr["area_ha"] >= 300_000: planner_labels.append((c.x, c.y, f"core · {pr['area_ha']/1000:,.0f}k ha", st["ec"], 7.8))
                    continue
                sup = f" · support {pr['support_mean']}" if pr.get("support_mean") is not None else ""
                towns = json.loads(pr["towns"]) if isinstance(pr.get("towns"), str) else (pr.get("towns") or [])
                # towns entries look like "Raga (Raja) 24,310" or "E. Kome* (nearest_place, unverified) 71,924": drop the trailing
                # count and any qualifier in parentheses, keep the name
                import re as _re
                town = (" · " + _re.sub(r"\s[\d,]+$", "", towns[0]).split(" (")[0].replace("*", "").strip()) if towns else ""
                nm = str(pr.get("seed", cls)).replace(" proposal", "") + ("" if "_" not in fp.stem.replace("optimize_", "") else f" ({fp.stem.split('_', 2)[2]})")
                kha = pr['area_ha'] / 1000
                planner_labels.append((c.x, c.y,
                                       f"{town.strip(' ·') or nm} · {kha:,.0f}k ha · {pr['population_est']:,} ppl",
                                       st["ec"], 8.6))

    # 5. settlements: area by population, so an empty interior reads as empty
    if setl and dens is not None:
        setl = [r for r in setl if (r[2] or 0) >= 500]    # the raster carries the hamlets; dots are towns only
    if setl:
        lons = np.array([r[1] for r in setl])
        lats = np.array([r[0] for r in setl])
        pops = np.array([max(r[2] or 0, 1) for r in setl], dtype=float)
        sizes = 3.0 + 62.0 * np.sqrt(pops / pops.max())
        ax.scatter(lons, lats, s=sizes, c=ORANGE, alpha=0.62,
                   edgecolors="#9a5d00", linewidths=0.25, zorder=4.0)

    # 6. THE GOLD FLANK, clipped to the ask (see load_gold)
    if gold:
        if gold["top5"]:
            ax.scatter([p[0] for p in gold["top5"]], [p[1] for p in gold["top5"]],
                       s=170, marker="s", facecolors=GOLD, alpha=0.20,
                       edgecolors=GOLD, linewidths=0.9, zorder=4.3)
        # the candidates are the sheet's mining subject: a paper halo under each mark so it stays legible on the
        # fire mass and inside the team-zone fills, and drawn above the team points (z 5.6) so nothing covers them
        gz = 5.8 if deploy else 4.5
        for lon, lat, _c in gold["candidates"]:
            ax.plot(lon, lat, marker="^", ms=10.5, mfc="none", mec=PAPER, mew=3.2, zorder=gz - 0.01)
            ax.plot(lon, lat, marker="^", ms=9, mfc="none", mec=GOLD, mew=1.6, zorder=gz)
        for lon, lat, _n in gold["watchlist"]:
            ax.plot(lon, lat, marker="x", ms=8.5, mec=PAPER, mew=3.0, zorder=gz - 0.01)
            ax.plot(lon, lat, marker="x", ms=7.5, mec=GOLD, mew=1.6, zorder=gz)
        for lon, lat, _s in gold["anchors"]:
            ax.plot(lon, lat, marker="D", ms=6, mfc=GOLD, mec="#4a3a0a",
                    mew=0.6, zorder=gz + 0.1)

    # 7. THE PLAN. Sites carry the assessment's verdict in their symbol:
    #    a filled square is an anchor (a real audience), a hollow circle is a
    #    site the assessment demoted to seasonal outreach. The reader should
    #    be able to see the recommendation without reading the panel.
    for s in ([] if deploy else sites):
        if s["kind"] == "focal":
            ax.plot(s["lon"], s["lat"], marker="*", ms=26, mfc=PLAN,
                    mec="white", mew=1.4, zorder=5.4)
        elif s["verdict"] == "anchor":
            ax.plot(s["lon"], s["lat"], marker="s", ms=11, mfc=PLAN,
                    mec="white", mew=1.3, zorder=5.4)
        else:
            ax.plot(s["lon"], s["lat"], marker="o", ms=10, mfc="white",
                    mec=PLAN, mew=1.9, zorder=5.4)

    # 7b. TEAMS as the planner places them (teams.geojson): FP = one person, star; ECHO = two, filled square in the
    #     conservancy's village; TANGO = two, triangle where the herd branch meets villages and water.
    teams = deploy["teams"] if deploy else (load_teams(Path(a.planner).parent) if a.planner else [])
    for t in teams:
        mk = {"FP": "*", "ECHO": "s", "TANGO": "^"}[t["kind"]]
        ms = ({"FP": 14, "ECHO": 7.5, "TANGO": 8.5} if deploy else {"FP": 20, "ECHO": 10, "TANGO": 11})[t["kind"]]
        if deploy and t["year"] == 2:      # year-2 team: hollow — same symbol, same place logic, one year later
            ax.plot(t["lon"], t["lat"], marker=mk, ms=ms, mfc="white", mec=TEAM_C, mew=1.3, zorder=5.6)
        else:
            ax.plot(t["lon"], t["lat"], marker=mk, ms=ms, mfc=TEAM_C, mec="white", mew=0.9, zorder=5.6)

    # The audience no site in the plan can reach. Ringed WHERE THEY ARE: an
    # earlier version put one marker at the population-weighted centre of the
    # set, which invented a place that is not a town and sat in ground where
    # nobody lives. A scattered finding has to be drawn scattered.
    if belt and not deploy:
        ax.scatter([t[0] for t in belt["towns"]], [t[1] for t in belt["towns"]],
                   s=[70 + 240 * (t[2] / max(x[2] for x in belt["towns"]))
                      for t in belt["towns"]],
                   facecolors="none", edgecolors="#8a2020", linewidths=1.7,
                   alpha=0.85, zorder=5.5)

    # the corridor: the axis the two pins encode, drawn as the ask it is
    if axis and not deploy:
        (ax0, ay0), (ax1_, ay1) = axis[0], axis[-1]
        dx, dy = ax1_ - ax0, ay1 - ay0
        n = math.hypot(dx, dy) or 1
        ext = 0.62
        ax.annotate("", xy=(ax1_ + dx / n * ext, ay1 + dy / n * ext),
                    xytext=(ax0 - dx / n * ext, ay0 - dy / n * ext),
                    arrowprops=dict(arrowstyle="-|>,head_width=0.34,head_length=0.7",
                                    color=PLAN, lw=2.6, alpha=0.9,
                                    linestyle=(0, (5, 2))), zorder=5.2)

    # 8. LABELS. Only the places an argument in the PIP names, and each one
    #    placed where it does not sit on another. The first version pinned
    #    every label up-and-right of its marker, which stacked "Ali Golo" on
    #    "Wau" and "Faraj Allah" on both - three sites the plan argues about,
    #    unreadable. Placement is now chosen per label from eight candidate
    #    offsets, scored against the labels already placed and against every
    #    marker on the sheet.
    label_pts = []
    for s in ([] if deploy else sites):
        tag = s["name"] + (" ~" if s["approx"] else "")
        label_pts.append((s["lon"], s["lat"], tag, PLAN, 12.5, "bold"))
    for t in teams:
        if deploy:
            label_pts.append((t["lon"], t["lat"], f"{t['id']} \u00b7 {t['place_short']}", TEAM_C, 8.8 if t["year"] == 1 else 8.0, "bold"))
        else:
            label_pts.append((t["lon"], t["lat"], f"{t['kind']} \u00b7 {t['place'].replace('near ', '').split(' (')[0].replace('*', '')}", TEAM_C, 9.4, "bold"))
    if belt and not deploy:
        big = max(belt["towns"], key=lambda t: t[2])
        label_pts.append((big[0], big[1],
                          f"{fmt(belt['people'])} people, {belt['clusters']} "
                          f"towns\nno site within {belt['reach_km']:g} km",
                          "#8a2020", 11.5, "bold"))
    # Shape names go down FIRST, centred in their own polygon, and the site
    # labels then route around them.
    area_entries = []
    for nm, z in zones.items():
        role = role_of(nm)
        if role in ("pin",):
            continue
        c = z["geom"].representative_point()
        col = {"park": GREEN, "wilderness": "#4f7a5c", "existing": GREEN_E,
               "grazing": "#8a6b3a"}.get(role, MUTED)
        sz = 15 if role == "park" else 11
        if a.planner:
            col, sz = USER_GREY, (11 if role == "park" else 8.5)
        if deploy:
            sz = 9.5 if role == "park" else 7.5
        lab = short_name(nm).upper().replace(" NATIONAL-PARK", "")
        area_entries.append((c.x, c.y, lab, col, sz))
    area_boxes = draw_area_labels(fig, ax, area_entries)
    # planner proposals are labelled like sites: one short line, routed around everything already placed
    for lon_, lat_, txt_, col_, sz_, *wt_ in planner_labels:
        label_pts.append((lon_, lat_, txt_, col_, sz_, wt_[0] if wt_ else "bold"))

    place_labels(fig, ax, label_pts,
                 avoid=[(s["lon"], s["lat"]) for s in ([] if deploy else sites)] + [(t["lon"], t["lat"]) for t in teams]
                 + ([(t[0], t[1]) for t in belt["towns"]] if belt and not deploy else [])
                 + ([(g[0], g[1]) for g in gold["candidates"] + gold["watchlist"] + gold["anchors"]] if deploy and gold else []),
                 reserved=area_boxes)

    # 9. graticule, scale bar, north arrow
    draw_graticule(ax, frame, kx)
    span_km = (x1 - x0) * kx * 111.0
    bar_km = next((k for k in (50, 100, 200, 300) if 0.12 < k / span_km < 0.32), 100)
    draw_scalebar(ax, frame, kx, bar_km, x_frac=0.74)     # lower-right; the legend card holds the lower-left
    draw_north(ax, frame)

    draw_inset_legend(fig, ax, items)

    fig.savefig(a.out, dpi=a.dpi, facecolor=PAPER)
    print("wrote", a.out)
    if a.pdf:
        p = str(Path(a.out).with_suffix(".pdf"))
        fig.savefig(p, facecolor=PAPER)
        print("wrote", p)


if __name__ == "__main__":
    main()
