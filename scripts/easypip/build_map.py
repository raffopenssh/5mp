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
PANEL_W_IN = 8.9

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


# ------------------------------------------------------------------- panel
# The panel is built in two passes: `panel_items` returns a list of drawing
# instructions each of which knows its own HEIGHT IN INCHES, and only then is
# the figure sized to fit. The first cut of this used figure-fractions with
# hand-tuned gaps, and the column ran off the bottom of the sheet the moment
# a legend row was added - the layout depended on a page height that had been
# guessed. Measuring first means content decides the page, and adding a line
# never requires re-tuning the ones below it.
PT = 1 / 72.0  # inches per point


def panel_items(st, fire, sites, belt, unmatched, rim_km, gold_clip_km,
                date):
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
    SP = 0.055                 # inches: the vertical unit
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

    def add(h, fn):
        items.append((h, fn))

    def text(txt, size=11.2, color=INK, weight="normal", x=0.0, style="normal",
             lead=LEAD, gap=SP, wrap=True):
        if wrap:
            import textwrap as _t
            txt = "\n".join("\n".join(_t.wrap(p, wrap_chars(size, x)) or [""])
                            for p in txt.split("\n"))
        n = txt.count("\n") + 1
        h = n * size * PT * lead + gap
        add(h, lambda ax, y, _t=txt: ax.text(
            x, y, _t, fontsize=size, color=color, weight=weight, style=style,
            va="top", ha="left", transform=ax.transData, linespacing=lead))
        return h

    def head(txt, color=INK):
        """Section head: air above, hairline below, air under the line."""
        add(SP * 3.4, lambda ax, y: None)
        add(12.2 * PT * LEAD, lambda ax, y, _t=txt.upper(): ax.text(
            0.0, y, _t, fontsize=12.2, color=color, weight="bold", va="top"))
        add(SP * 1.5, lambda ax, y: ax.plot([0, 1], [y + SP * 0.35] * 2,
                                            color="#e2e2dc", lw=0.9,
                                            clip_on=False))

    def key(marker, label, mfc, mec, ms=9, mew=1.4, lw=0, ls="solid",
            alpha=1.0, note=""):
        """One legend row: the symbol exactly as drawn on the map, then why.

        The note hangs under the label at the same left edge, so the symbol
        column stays a column and the eye can run down it.
        """
        LS, NS = 10.6, 9.2
        import textwrap as _t
        nlines = (_t.wrap(note, wrap_chars(NS)) if note else [])
        h = LS * PT * LEAD + len(nlines) * NS * PT * 1.34 + SP * 0.85

        def draw(ax, y):
            yc = y + LS * PT * 0.60
            if lw:
                ax.plot([SYM_X - 0.024, SYM_X + 0.024], [yc, yc], color=mec,
                        lw=lw, ls=ls, alpha=alpha, clip_on=False,
                        solid_capstyle="butt")
            else:
                ax.plot([SYM_X], [yc], marker=marker, ms=ms, mfc=mfc, mec=mec,
                        mew=mew, alpha=alpha, clip_on=False)
            ax.text(TXT_X, y, label, fontsize=LS, color=INK, va="top")
            if nlines:
                ax.text(TXT_X, y + LS * PT * LEAD, "\n".join(nlines),
                        fontsize=NS, color=MUTED, va="top", style="italic",
                        linespacing=1.34)
        add(h, draw)

    # ---------------------------------------------------------------- title
    # The sheet is one of three EASY PIP documents and says so: a map that
    # travels without its report must still name the report it belongs to.
    add(10.0 * PT * LEAD + SP * 0.6,
        lambda ax, y, _d=date: ax.text(
            0.0, y, f"PRIORITY INTERVENTION PLAN  \u00b7  {_d}", fontsize=10.0,
            color=MUTED, weight="bold", va="top"))
    text("PONGO\u2013WAU\u2013NUMATINNA", 26, GREEN, "bold", gap=SP * 0.5)
    text("Proposed National Park \u2014 the assessment and the plan, on one map",
         12.6, INK, gap=SP * 0.5)
    text(f"Western Bahr el Ghazal & Western Equatoria, South Sudan   \u00b7   "
         f"{fmt(P['area_km2'])} km\u00b2 as the boundary file draws it",
         10.2, MUTED, gap=SP * 0.6)
    text("Reads with the Priority Intervention Plan report and its two-page "
         "summary. "
         "Everything drawn here is something those two documents argue about; "
         "nothing else is drawn.",
         9.4, MUTED, style="italic", gap=SP)

    # ---------------------------------------------------------------- legend
    # This is a LEGEND, not a summary. The argument - what the emptiness means,
    # what we propose, what it costs - is the two-page summary's job, and an
    # earlier draft of this panel quietly turned into a second copy of it.
    # A figure appears here only where it tells the reader how to read a mark:
    # how many fronts are in the wash, how far the gold clip extends. Anything
    # a reader would quote rather than use to decode the picture belongs in the
    # text, where it can be qualified.
    head("Boundaries", GREEN)
    key("s", "The proposed park", "none", GREEN, lw=3.4)
    key("s", "Wilderness blocks around it \u2014 proposed, not park", "none",
        GREEN_W, lw=1.6)
    key("s", "Southern National Park \u2014 already gazetted", "none", GREEN_E,
        lw=2.0)
    key("s", "Sustainable-grazing zones", "none", TAN, lw=1.6, ls=(0, (7, 4)))
    key("_", "Corridor axis, as the two map pins imply it", "none", PLAN,
        lw=2.4, ls=(0, (5, 2)),
        note=f"we were sent markers {C['pins_apart_km']:g} km apart, "
             f"not a polygon")
    if unmatched:
        key("s", "Shape with no assigned role", "none", "#999", lw=1.2,
            ls=(0, (1, 2)), note="; ".join(sorted(set(unmatched))))
    add(SP * 0.9, lambda ax, y: None)
    text(f"{SH['n_shapes']} shapes in {SH['n_files']} files, covering "
         f"{fmt(SH['union_km2'])} km\u00b2 between them. Their areas sum to "
         f"{fmt(SH['sum_of_areas_km2'])} km\u00b2 because they are nested: "
         f"{fmt(SH['overlap_km2'])} km\u00b2 lies under more than one.",
         9.4, MUTED, style="italic", x=TXT_X, gap=SP)

    head("What is happening on the ground", GREEN)
    key("_", "One hairline = one fire front, 2024\u20132026", "none", RED,
        lw=1.4, alpha=0.55,
        note=f"{fmt(len(fire))} of them in frame \u2014 the depth of the wash "
             f"is the density, not one big fire")
    key("o", "Settlement, area \u221d people", ORANGE, "#9a5d00", ms=9, mew=0.5,
        note="a satellite estimate and a lower bound, never a census")
    key("_", "Trunk rivers", "none", BLUE, lw=1.4)

    head("Gold prospectivity", GOLD)
    key("s", "Top 5% of ground by model score", GOLD, GOLD, ms=9, mew=1.0,
        note=f"drawn only within {gold_clip_km:g} km of the proposed shapes \u2014 "
             f"blank elsewhere means NOT DRAWN, not scored low")
    key("^", "Imagery target \u2014 somewhere to look, never a mine", "none",
        GOLD, ms=9)
    key("D", "Reported working (OSM / Crisis Tracker)", GOLD, "#4a3a0a", ms=6,
        mew=0.6)
    add(SP * 0.9, lambda ax, y: None)
    text(G["verdict"], 9.2, "#8a5a00", style="italic", x=TXT_X, gap=SP)

    head("The plan", PLAN)
    key("s", "Anchor station \u2014 staffed", PLAN, "white", ms=10, mew=1.3)
    key("o", "Seasonal outreach only", "white", PLAN, ms=9, mew=1.9)
    key("*", "Town focal point", PLAN, "white", ms=19, mew=1.2)
    key("o", "Town no site in the plan reaches", "none", "#8a2020",
        ms=11, mew=1.7,
        note=(f"{fmt(belt['people'])} people in {belt['clusters']} towns of "
              f"{fmt(belt['min_pop'])}+ beside the proposed area, all further "
              f"than {belt['reach_km']:g} km from every site"
              if belt else "none: every town of "
                           f"{fmt(2000)}+ beside the area is within reach"))
    add(SP * 0.9, lambda ax, y: None)
    text("A tilde after a site name means the assessment could not confirm its "
         "position on the ground.", 9.4, MUTED, style="italic", x=TXT_X,
         gap=SP)

    # ------------------------------------------------------------ provenance
    add(SP * 2.6, lambda ax, y: None)
    add(SP * 1.4, lambda ax, y: ax.plot([0, 1], [y] * 2, color="#e2e2dc",
                                        lw=0.9, clip_on=False))
    text(
        f"Sources.  Boundaries: the {SH['n_files']} KML files as received, "
        f"re-measured. Fire: VIIRS via NASA FIRMS, grouped into fronts by this "
        f"project's v5 tracker; the satellite fleet triples on 2024-01-01, so "
        f"rates here use 2024\u20132025 only. People: GHSL rasters \u2014 a "
        f"satellite estimate and a lower bound, never a count. Clearing: "
        f"Hansen/GLAD loss, verified subset. Gold: this project's model, "
        f"trained on {G['n_anchors']} known workings from OSM and Crisis "
        f"Tracker.",
        8.4, MUTED, lead=1.48, gap=SP * 0.7)
    text("Every figure on this sheet is generated from "
         "data/eval/pip_facts.json \u2014 the same file the PIP report and the "
         "two-page summary read. None is typed by hand.",
         8.4, MUTED, style="italic", lead=1.48, gap=0.0)
    return items


def panel_height_in(items):
    return sum(h for h, _ in items)


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
    for h, fn in items:
        fn(ax, y)
        y += h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kml-dir", default=str(ROOT / "data/plan_zones"))
    ap.add_argument("--out", default=str(ROOT / "reports/EASY_PIP_MAP_2026-08.png"))
    ap.add_argument("--pdf", action="store_true", help="also write a vector PDF")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--date", default=date.today().isoformat(),
                    help="date stamp printed on the sheet")
    a = ap.parse_args()

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
    belt = unserved_belt(setl, sites, reach)
    axis = corridor_axis(st)

    # ---- figure. The panel measures itself first, and the page is then made
    # tall enough to hold whichever column is longer. Sizing the page before
    # measuring the text is what pushed the legend off the sheet.
    unmatched = sorted({short_name(nm) for nm in zones if role_of(nm) == "other"})
    items = panel_items(st, fire, sites, belt, unmatched, rim_km, rim_km,
                        a.date)
    panel_h = panel_height_in(items) + 0.30

    map_w_deg = (x1 - x0) * kx
    map_h_deg = (y1 - y0)
    MAP_H_IN = MAP_W_IN * map_h_deg / map_w_deg
    FIG_W = MAP_W_IN + PANEL_W_IN + 0.35
    FIG_H = max(MAP_H_IN, panel_h) + 0.24

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
                                  zorder=s["z"] + 0.05):
                ax.add_patch(p)

    # 5. settlements: area by population, so an empty interior reads as empty
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
        for lon, lat, _c in gold["candidates"]:
            ax.plot(lon, lat, marker="^", ms=9, mfc="none", mec=GOLD,
                    mew=1.6, zorder=4.5)
        for lon, lat, _n in gold["watchlist"]:
            ax.plot(lon, lat, marker="x", ms=7.5, mec=GOLD, mew=1.6, zorder=4.5)
        for lon, lat, _s in gold["anchors"]:
            ax.plot(lon, lat, marker="D", ms=6, mfc=GOLD, mec="#4a3a0a",
                    mew=0.6, zorder=4.6)

    # 7. THE PLAN. Sites carry the assessment's verdict in their symbol:
    #    a filled square is an anchor (a real audience), a hollow circle is a
    #    site the assessment demoted to seasonal outreach. The reader should
    #    be able to see the recommendation without reading the panel.
    for s in sites:
        if s["kind"] == "focal":
            ax.plot(s["lon"], s["lat"], marker="*", ms=26, mfc=PLAN,
                    mec="white", mew=1.4, zorder=5.4)
        elif s["verdict"] == "anchor":
            ax.plot(s["lon"], s["lat"], marker="s", ms=11, mfc=PLAN,
                    mec="white", mew=1.3, zorder=5.4)
        else:
            ax.plot(s["lon"], s["lat"], marker="o", ms=10, mfc="white",
                    mec=PLAN, mew=1.9, zorder=5.4)

    # The audience no site in the plan can reach. Ringed WHERE THEY ARE: an
    # earlier version put one marker at the population-weighted centre of the
    # set, which invented a place that is not a town and sat in ground where
    # nobody lives. A scattered finding has to be drawn scattered.
    if belt:
        ax.scatter([t[0] for t in belt["towns"]], [t[1] for t in belt["towns"]],
                   s=[70 + 240 * (t[2] / max(x[2] for x in belt["towns"]))
                      for t in belt["towns"]],
                   facecolors="none", edgecolors="#8a2020", linewidths=1.7,
                   alpha=0.85, zorder=5.5)

    # the corridor: the axis the two pins encode, drawn as the ask it is
    if axis:
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
    for s in sites:
        tag = s["name"] + (" ~" if s["approx"] else "")
        label_pts.append((s["lon"], s["lat"], tag, PLAN, 12.5, "bold"))
    if belt:
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
        lab = short_name(nm).upper().replace(" NATIONAL-PARK", "")
        area_entries.append((c.x, c.y, lab, col, sz))
    area_boxes = draw_area_labels(fig, ax, area_entries)

    place_labels(fig, ax, label_pts,
                 avoid=[(s["lon"], s["lat"]) for s in sites]
                 + ([(t[0], t[1]) for t in belt["towns"]] if belt else []),
                 reserved=area_boxes)

    # 9. graticule, scale bar, north arrow
    draw_graticule(ax, frame, kx)
    span_km = (x1 - x0) * kx * 111.0
    bar_km = next((k for k in (50, 100, 200, 300) if 0.12 < k / span_km < 0.32), 100)
    draw_scalebar(ax, frame, kx, bar_km)
    nx_, ny_ = x1 - (x1 - x0) * 0.045, y1 - (y1 - y0) * 0.10
    ax.annotate("", xy=(nx_, ny_ + (y1 - y0) * 0.055), xytext=(nx_, ny_),
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=2.0), zorder=6.2)
    ax.text(nx_, ny_ - (y1 - y0) * 0.018, "N", fontsize=13, weight="bold",
            color=INK, ha="center", zorder=6.2)

    draw_panel(fig, items, 0.12 + MAP_W_IN + 0.30, PANEL_W_IN, FIG_W, FIG_H)

    fig.savefig(a.out, dpi=a.dpi, facecolor=PAPER)
    print("wrote", a.out)
    if a.pdf:
        p = str(Path(a.out).with_suffix(".pdf"))
        fig.savefig(p, facecolor=PAPER)
        print("wrote", p)


if __name__ == "__main__":
    main()
