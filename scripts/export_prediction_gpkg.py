#!/usr/bin/env python3
"""Styled GPKG of the XSA mining assessment - one layer per information
class, symbology carried by attributes, so the method reads off the layer
panel top-to-bottom.  Layers are designed to STACK: surfaces are
semi-transparent with distinct ramps, points sit on top with light halos.

  01_evidence__known_mines  the 43-cluster truth set (report anchors)
  02_candidates             ONE layer for every named place worth a look:
                            32 field candidates + 61 watchlist villages,
                            colour-coded by group (fill), the ones the
                            EASY report names in prose get a dark outline
                            + bold label (in_report=1)
  10_places__scored         all scored named places in ONE layer,
                            viridis by composite percentile
  20_surface__context       the top-35% composite evidence surface,
                            viridis by percentile (tier skill in fields)
  21_surface__probability   relative likelihood of unreported mining,
                            percentile-ranked, inverted Spectral
                            (blue = low, red = high); components and
                            caveat in method_note - NOT calibrated
                            probability, a ranking for readers to plan by
  22_surface__travel_time   minutes to the nearest start place over the
                            WHOLE grid (car/foot/bush best, local OSRM
                            incl. 1930s tracks at 15-20 km/h)
  23_surface__gold_reachable  cells gold-contact<=5km AND <=4h reachable -
                            the strongest measured conjunction (q=0.04)
  30_context__basins        HydroBASINS outlines (grouping unit)

Every scored layer carries its measured lift/p (or q) as constant fields:
a graduated map without its skill beside it reads as a ranking
(AGENTS.md invariant 12).
"""
import json
import math
import sqlite3
from pathlib import Path

import numpy as np
from osgeo import ogr, osr

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/eval/xsa_mining"
OUT = SRC / "xsa_mining_prediction.gpkg"

pred = json.load(open(SRC / "prediction.json"))
gj = json.load(open(SRC / "prediction.geojson"))
axg = json.load(open(SRC / "access_x_geology_eval.json"))
zs = np.load(SRC / "plan_surface.npz")
cells = zs["cells"]; plan_v = zs["plan"]; conj_v = zs["conj"]
access_v = zs["access_min"]; ttown_v = zs["t_town_min"]
cell = float(pred["cell_deg"]); h = cell / 2
grad = pred["graduated"]

# the layer IS gold5_acc240 - it must carry that signal's own score,
# not the best score among its siblings (invariant 12)
_conj_row = next(s for s in axg["signals"]
                 if s["signal"] == axg["best_conjunction"])
conj_q = _conj_row["q_bh_reach"]
conj_lift = _conj_row["lift_reach"]

# cell index for point->cell attribute joins
cix = {(round(float(x), 4), round(float(y), 4)): i
       for i, (x, y) in enumerate(cells)}


def cell_of(lon, lat):
    i = cix.get((round(cell * round(lon / cell) if False else lon, 4),
                 round(lat, 4)))
    if i is not None:
        return i
    d2 = (cells[:, 0] - lon) ** 2 + (cells[:, 1] - lat) ** 2
    return int(np.argmin(d2))


# Places the EASY report names in prose. in_report=1 -> bold symbol+label.
# MUST stay in sync with reports/XSA_CONSERVATION_REVIEW_2026-08_EASY.txt.
REPORT_CAND_CELLS = {
    (8.4270, 27.5288), (7.9270, 27.8288), (6.1770, 27.7788),
    (8.9770, 22.9788),
    (7.6770, 28.0288), (7.2770, 28.6788), (5.8270, 30.8288),
    (5.3270, 25.4288), (6.5270, 23.8788), (7.2270, 28.3788),
    (7.5270, 28.5288), (8.4770, 24.5288),
}
REPORT_WATCH_NAMES = {
    "Toich", "Tup Weng", "Tambora A.M. & C.C.", "Kyango", "Ibrahim",
    "(TIDI)",
}

srs = osr.SpatialReference(); srs.ImportFromEPSG(4326)
if OUT.exists(): OUT.unlink()
ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(OUT))


def km(lat1, lon1, lat2, lon2):
    ky = 111.32
    kx = ky * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat1 - lat2) * ky, (lon1 - lon2) * kx)


def mklayer(name, geomtype, fields):
    lyr = ds.CreateLayer(name, srs, geomtype)
    for n, t in fields:
        lyr.CreateField(ogr.FieldDefn(n, t))
    return lyr


def pt(lon, lat):
    g = ogr.Geometry(ogr.wkbPoint); g.AddPoint(lon, lat); return g


def cellpoly(lon, lat):
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for dx, dy in ((-h, -h), (h, -h), (h, h), (-h, h), (-h, -h)):
        ring.AddPoint(lon + dx, lat + dy)
    p = ogr.Geometry(ogr.wkbPolygon); p.AddGeometry(ring); return p


# ---------------------------------------------------------- 01 known mines
lyr = mklayer("01_evidence__known_mines", ogr.wkbPoint,
              [("source", ogr.OFTString), ("resource", ogr.OFTString)])
for a in pred["anchors"]:
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("source", a["source"] or "")
    f.SetField("resource", a["resource"] or "")
    f.SetGeometry(pt(a["lon"], a["lat"])); lyr.CreateFeature(f)

# ------------------------------------------------------------ 02 candidates
# ONE layer for everything worth a look: 32 field candidates (grouped by
# what makes each suspicious) + the 61-village watchlist. Colour = group,
# report-named entries get a dark outline + bold label (in_report=1).
lyr = mklayer("02_candidates", ogr.wkbPoint, [
    ("name", ogr.OFTString), ("group", ogr.OFTString),
    ("watch_rank", ogr.OFTInteger), ("basin", ogr.OFTString),
    ("in_report", ogr.OFTInteger),
    ("composite_pctile", ogr.OFTReal),
    ("gold_contact_km", ogr.OFTReal), ("river_km", ogr.OFTReal),
    ("settlement_km", ogr.OFTReal), ("deforestation_km", ogr.OFTReal),
    ("km_to_known_anchor", ogr.OFTReal),
    ("settlement_population_est", ogr.OFTInteger),
    ("settlement_cropland_frac", ogr.OFTReal),
    ("snap_source", ogr.OFTString), ("snap_km", ogr.OFTReal),
    ("hist_label", ogr.OFTString),
    ("travel_minutes_best", ogr.OFTReal),
    ("minutes_from_town_5k", ogr.OFTReal)])
n_cand = 0
for c in pred["candidates"]:
    ns_, hl = c.get("nearest_settlement"), c.get("nearest_hist_label")
    if hl:
        lat, lon, name, src = hl["lat"], hl["lon"], hl["text"], "hist_label_1930s"
    elif ns_:
        nm = ns_["place"] if ns_["place"] and len(ns_["place"]) > 2 \
            else "(unnamed settlement)"
        lat, lon, name, src = ns_["lat"], ns_["lon"], nm, "settlement"
    else:
        lat, lon, name, src = c["lat"], c["lon"], "(cell centre)", "cell_centre"
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("name", name)
    f.SetField("group", c.get("character") or "candidate")
    f.SetField("basin", c.get("basin") or "")
    f.SetField("in_report",
               1 if (round(c["lat"], 4), round(c["lon"], 4))
               in REPORT_CAND_CELLS else 0)
    for k in ("composite_pctile", "gold_contact_km", "river_km",
              "settlement_km", "deforestation_km", "km_to_known_anchor"):
        f.SetField(k, c[k])
    if c.get("settlement_population_est") is not None:
        f.SetField("settlement_population_est",
                   int(c["settlement_population_est"]))
    if c.get("settlement_cropland_frac") is not None:
        f.SetField("settlement_cropland_frac", c["settlement_cropland_frac"])
    f.SetField("snap_source", src)
    f.SetField("snap_km", round(km(lat, lon, c["lat"], c["lon"]), 1))
    if hl: f.SetField("hist_label", f'{hl["text"]} ({hl["dist_km"]} km)')
    ci_ = cell_of(c["lon"], c["lat"])
    f.SetField("travel_minutes_best", round(float(access_v[ci_]), 0))
    f.SetField("minutes_from_town_5k", round(float(ttown_v[ci_]), 0))
    f.SetGeometry(pt(lon, lat)); lyr.CreateFeature(f)
    n_cand += 1
for w in pred["abandoned_village_gold_watchlist"]["places"]:
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("name", w["name"] or "(unnamed)")
    f.SetField("group", "watchlist: abandoned 1930s village on gold")
    f.SetField("watch_rank", w["rank"])
    f.SetField("in_report", 1 if w["name"] in REPORT_WATCH_NAMES else 0)
    f.SetField("basin", w.get("basin") or "")
    f.SetField("composite_pctile", w["composite_pctile"])
    f.SetField("gold_contact_km", w["gold_contact_km"])
    f.SetField("river_km", w["river_km"])
    wi_ = cell_of(w["lon"], w["lat"])
    f.SetField("travel_minutes_best", round(float(access_v[wi_]), 0))
    f.SetField("minutes_from_town_5k", round(float(ttown_v[wi_]), 0))
    f.SetGeometry(pt(w["lon"], w["lat"])); lyr.CreateFeature(f)
    n_cand += 1

# ------------------------------------------------- 10 scored places (one)
TIER_STATS = {t: grad["tiers"][t] for t in ("top05", "top10", "top20",
                                            "top35")}
lyr = mklayer("10_places__scored", ogr.wkbPoint, [
    ("name", ogr.OFTString), ("kind", ogr.OFTString),
    ("score", ogr.OFTReal), ("pctile", ogr.OFTReal),
    ("tier", ogr.OFTString), ("basin", ogr.OFTString),
    ("classification", ogr.OFTString), ("persistence", ogr.OFTString),
    ("population_est", ogr.OFTInteger),
    ("cropland_frac_2019", ogr.OFTReal),
    ("place_type", ogr.OFTString),
    ("travel_minutes_best", ogr.OFTReal),
    ("minutes_from_town_5k", ogr.OFTReal),
    ("tier_lift_reach", ogr.OFTReal), ("tier_p_reach", ogr.OFTReal)])
n_places = 0
for kind in ("settlements", "hist_places", "osm_places"):
    for x in grad["features"][kind]:
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("name", x.get("name") or "(unnamed)")
        f.SetField("kind", {"settlements": "settlement_footprint",
                            "hist_places": "place_1930s",
                            "osm_places": "place_osm"}[kind])
        f.SetField("score", x["score"]); f.SetField("pctile", x["pctile"])
        f.SetField("tier", x["tier"]); f.SetField("basin", x.get("basin") or "")
        ts = TIER_STATS[x["tier"]]
        if ts.get("lift_reach") is not None:
            f.SetField("tier_lift_reach", ts["lift_reach"])
        if ts.get("p_reach") is not None:
            f.SetField("tier_p_reach", ts["p_reach"])
        xi_ = cell_of(x["lon"], x["lat"])
        f.SetField("travel_minutes_best", round(float(access_v[xi_]), 0))
        f.SetField("minutes_from_town_5k", round(float(ttown_v[xi_]), 0))
        for k in ("classification", "persistence", "population_est",
                  "cropland_frac_2019", "place_type"):
            if x.get(k) is not None:
                f.SetField(k, x[k])
        f.SetGeometry(pt(x["lon"], x["lat"])); lyr.CreateFeature(f)
        n_places += 1

# --------------------------------------------------------- 20..23 surfaces
lyr = mklayer("20_surface__context", ogr.wkbPolygon,
              [("score", ogr.OFTReal), ("pctile", ogr.OFTReal),
               ("tier", ogr.OFTString),
               ("tier_lift_reach", ogr.OFTReal),
               ("tier_p_reach", ogr.OFTReal)])
n_surf = 0
for ft in gj["features"]:
    pr = ft["properties"]
    if pr.get("layer") != "composite_graduated":
        continue
    lon, lat = ft["geometry"]["coordinates"]
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("score", pr["score"]); f.SetField("pctile", pr["pctile"])
    f.SetField("tier", pr["tier"])
    ts = TIER_STATS[pr["tier"]]
    if ts.get("lift_reach") is not None:
        f.SetField("tier_lift_reach", ts["lift_reach"])
    if ts.get("p_reach") is not None:
        f.SetField("tier_p_reach", ts["p_reach"])
    f.SetGeometry(cellpoly(lon, lat)); lyr.CreateFeature(f)
    n_surf += 1

PROB_NOTE = ("percentile rank of equal votes: tested context composite + "
             "population/travel-time pressure + distance-from-oversight; "
             "a relative ranking for planning, NOT a calibrated "
             "probability - see key_places_plan.json")
lyr = mklayer("21_surface__probability", ogr.wkbPolygon,
              [("probability_pctile", ogr.OFTReal),
               ("minutes_to_start_place", ogr.OFTReal),
               ("minutes_from_town_5k", ogr.OFTReal),
               ("method_note", ogr.OFTString)])
ok = np.isfinite(plan_v)
ranks = np.full(len(plan_v), np.nan)
r = np.argsort(np.argsort(plan_v[ok]))
ranks[ok] = 100.0 * r / max(len(r) - 1, 1)
n_plan = 0
for i in np.where(ok)[0]:
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("probability_pctile", round(float(ranks[i]), 1))
    f.SetField("minutes_to_start_place", round(float(access_v[i]), 0))
    f.SetField("minutes_from_town_5k", round(float(ttown_v[i]), 0))
    f.SetField("method_note", PROB_NOTE)
    f.SetGeometry(cellpoly(*map(float, cells[i]))); lyr.CreateFeature(f)
    n_plan += 1

lyr = mklayer("22_surface__travel_time", ogr.wkbPolygon,
              [("minutes_to_start_place", ogr.OFTReal),
               ("minutes_from_town_5k", ogr.OFTReal)])
for i in range(len(cells)):
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("minutes_to_start_place", round(float(access_v[i]), 0))
    f.SetField("minutes_from_town_5k", round(float(ttown_v[i]), 0))
    f.SetGeometry(cellpoly(*map(float, cells[i]))); lyr.CreateFeature(f)

lyr = mklayer("23_surface__gold_reachable", ogr.wkbPolygon,
              [("lift_reach", ogr.OFTReal), ("q_bh_reach", ogr.OFTReal),
               ("definition", ogr.OFTString)])
DEF = ("gold-graded contact <=5 km AND <=240 min travel; "
       "access_x_geology_eval.json")
n_conj = 0
for i in np.where(conj_v == 1)[0]:
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("lift_reach", conj_lift); f.SetField("q_bh_reach", conj_q)
    f.SetField("definition", DEF)
    f.SetGeometry(cellpoly(*map(float, cells[i]))); lyr.CreateFeature(f)
    n_conj += 1

# ------------------------------------------------------------- 30 basins
basin_meta = {b["pfaf_id"]: b for b in pred["basins"]["list"]}
lyr = mklayer("30_context__basins", ogr.wkbMultiPolygon,
              [("name", ogr.OFTString), ("pfaf_id", ogr.OFTInteger),
               ("level", ogr.OFTInteger), ("aoi_share", ogr.OFTReal)])
for fn in ("hybas5_xsa.json", "hybas6_xsa.json"):
    for ft in json.load(open(ROOT / "data/hydrobasins" / fn))["features"]:
        pid = int(ft["properties"]["PFAF_ID"])
        b = basin_meta.get(pid)
        if not b or b["level"] != (5 if fn == "hybas5_xsa.json" else 6):
            continue
        g = ogr.ForceToMultiPolygon(
            ogr.CreateGeometryFromJson(json.dumps(ft["geometry"])))
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("name", b["name"]); f.SetField("pfaf_id", pid)
        f.SetField("level", b["level"])
        f.SetField("aoi_share", b["aoi_share"])
        f.SetGeometry(g); lyr.CreateFeature(f)
ds = None
print(f"candidates {n_cand}, places {n_places}, surface {n_surf}, prob {n_plan}, conj {n_conj}")

# ================================================================= styles
VIRIDIS = ["68,1,84", "72,40,120", "62,74,137", "49,104,142", "38,130,142",
           "31,158,137", "53,183,121", "110,206,88", "181,222,43",
           "253,231,37"]
BLUES_R = ["247,251,255", "222,235,247", "198,219,239", "158,202,225",
           "107,174,214", "66,146,198", "33,113,181", "8,81,156",
           "8,48,107", "3,19,43"]


def _ranges(field, lo, hi, colors, alpha, symbol):
    step = (hi - lo) / len(colors)
    out = []
    for i, c in enumerate(colors):
        a, b = lo + i * step, lo + (i + 1) * step
        if symbol == "fill":
            sym = (f'<symbol type="fill" name="{i}"><layer class="SimpleFill">'
                   f'<Option type="Map">'
                   f'<Option name="color" type="QString" value="{c},{alpha}"/>'
                   f'<Option name="outline_style" type="QString" value="no"/>'
                   f'</Option></layer></symbol>')
        else:
            size = 1.6 + 2.4 * (i + 1) / len(colors)
            sym = (f'<symbol type="marker" name="{i}">'
                   f'<layer class="SimpleMarker"><Option type="Map">'
                   f'<Option name="name" type="QString" value="circle"/>'
                   f'<Option name="color" type="QString" value="{c},{alpha}"/>'
                   f'<Option name="outline_color" type="QString" '
                   f'value="40,40,40,120"/>'
                   f'<Option name="outline_width" type="QString" value="0.2"/>'
                   f'<Option name="size" type="QString" value="{size:.1f}"/>'
                   f'</Option></layer></symbol>')
        out.append((f'<range symbol="{i}" lower="{a:.2f}" upper="{b:.2f}" '
                    f'label="{a:.0f}-{b:.0f}" render="true"/>', sym))
    return out


def graduated(field, lo, hi, colors, alpha="255", symbol="fill",
              label_field=None, label_size="7"):
    rs = _ranges(field, lo, hi, colors, alpha, symbol)
    lab = ""
    if label_field:
        lab = (f'<labeling type="simple"><settings><text-style '
               f'fieldName="{label_field}" fontSize="{label_size}" '
               f'textColor="50,50,50,255"><text-buffer bufferDraw="1" '
               f'bufferSize="0.8" bufferColor="255,255,255,190"/>'
               f'</text-style><placement placement="6" dist="1.2"/>'
               f'</settings></labeling>')
    return (f'<!DOCTYPE qgis><qgis styleCategories="Symbology|Labeling" '
            f'labelsEnabled="{1 if label_field else 0}">'
            f'<renderer-v2 type="graduatedSymbol" attr="{field}" '
            f'graduatedMethod="GraduatedColor"><ranges>'
            + "".join(r for r, _ in rs) + '</ranges><symbols>'
            + "".join(s for _, s in rs)
            + '</symbols></renderer-v2>' + lab + '</qgis>')


def marker(shape_, color, outline_, size, label_field=None,
           label_color="60,60,60,255", label_size="8"):
    lab = ""
    if label_field:
        lab = (f'<labeling type="simple"><settings><text-style '
               f'fieldName="{label_field}" fontSize="{label_size}" '
               f'textColor="{label_color}"><text-buffer bufferDraw="1" '
               f'bufferSize="0.9" bufferColor="255,255,255,210"/>'
               f'</text-style><placement placement="6" dist="1.5"/>'
               f'</settings></labeling>')
    return (f'<!DOCTYPE qgis><qgis styleCategories="Symbology|Labeling" '
            f'labelsEnabled="{1 if label_field else 0}">'
            f'<renderer-v2 type="singleSymbol"><symbols>'
            f'<symbol type="marker" name="0"><layer class="SimpleMarker">'
            f'<Option type="Map">'
            f'<Option name="name" type="QString" value="{shape_}"/>'
            f'<Option name="color" type="QString" value="{color}"/>'
            f'<Option name="outline_color" type="QString" value="{outline_}"/>'
            f'<Option name="outline_width" type="QString" value="0.3"/>'
            f'<Option name="size" type="QString" value="{size}"/>'
            f'</Option></layer></symbol></symbols></renderer-v2>{lab}</qgis>')


def rule_marker(rules, label_field, label_size="9"):
    """rules: list of (filter, legend, shape, color, outline, size,
    fontweight)."""
    syms, rls, labs = [], [], []
    for i, (flt, leg, shape_, color, out, size, fw) in enumerate(rules):
        syms.append(f'<symbol type="marker" name="{i}">'
                    f'<layer class="SimpleMarker"><Option type="Map">'
                    f'<Option name="name" type="QString" value="{shape_}"/>'
                    f'<Option name="color" type="QString" value="{color}"/>'
                    f'<Option name="outline_color" type="QString" '
                    f'value="{out}"/>'
                    f'<Option name="outline_width" type="QString" '
                    f'value="0.5"/>'
                    f'<Option name="size" type="QString" value="{size}"/>'
                    f'</Option></layer></symbol>')
        rls.append(f'<rule key="k{i}" filter="{flt}" label="{leg}" '
                   f'symbol="{i}"/>')
        labs.append(f'<rule filter="{flt}"><settings><text-style '
                    f'fieldName="{label_field}" fontSize="{label_size}" '
                    f'fontWeight="{fw}" textColor="{out}">'
                    f'<text-buffer bufferDraw="1" bufferSize="1.1" '
                    f'bufferColor="255,255,255,235"/></text-style>'
                    f'<placement placement="6" dist="2"/></settings></rule>')
    return (f'<!DOCTYPE qgis><qgis styleCategories="Symbology|Labeling" '
            f'labelsEnabled="1">'
            f'<renderer-v2 type="RuleRenderer"><rules key="root">'
            + "".join(rls) + '</rules><symbols>' + "".join(syms)
            + '</symbols></renderer-v2>'
            f'<labeling type="rule-based"><rules>' + "".join(labs)
            + '</rules></labeling></qgis>')


def outline_style(color, label_field=None):
    lab = ""
    if label_field:
        lab = (f'<labeling type="simple"><settings><text-style '
               f'fieldName="{label_field}" fontSize="10" '
               f'textColor="{color}"><text-buffer bufferDraw="1" '
               f'bufferSize="1.2" bufferColor="255,255,255,210"/>'
               f'</text-style><placement placement="0"/></settings>'
               f'</labeling>')
    return (f'<!DOCTYPE qgis><qgis styleCategories="Symbology|Labeling" '
            f'labelsEnabled="{1 if label_field else 0}">'
            f'<renderer-v2 type="singleSymbol"><symbols>'
            f'<symbol type="fill" name="0"><layer class="SimpleFill">'
            f'<Option type="Map">'
            f'<Option name="style" type="QString" value="no"/>'
            f'<Option name="outline_color" type="QString" value="{color}"/>'
            f'<Option name="outline_width" type="QString" value="0.5"/>'
            f'<Option name="outline_style" type="QString" value="dash"/>'
            f'</Option></layer></symbol></symbols></renderer-v2>{lab}</qgis>')


def fill_single(color):
    return (f'<!DOCTYPE qgis><qgis styleCategories="Symbology">'
            f'<renderer-v2 type="singleSymbol"><symbols>'
            f'<symbol type="fill" name="0"><layer class="SimpleFill">'
            f'<Option type="Map">'
            f'<Option name="color" type="QString" value="{color}"/>'
            f'<Option name="outline_color" type="QString" '
            f'value="120,60,10,180"/>'
            f'<Option name="outline_width" type="QString" value="0.15"/>'
            f'</Option></layer></symbol></symbols></renderer-v2></qgis>')


AMP = "&quot;"


def cat_marker(field, cats, label_field, nolabel_vals=()):
    """Categorized fill-colour markers: cats = [(value, legend, rgba_fill,
    size)]. All same shape (circle); report-named entries (in_report=1)
    are drawn via a rule-based override with a dark outline + bold label,
    so colour codes the group and the outline codes 'named in report'.
    Groups listed in nolabel_vals get no labels (keeps the map readable
    when a large secondary list shares the layer)."""
    syms, rls, labs = [], [], []
    i = 0
    for val, leg, fill, size in cats:
        for rep in (0, 1):
            flt = (f"{AMP}{field}{AMP}='{val}' AND {AMP}in_report{AMP}={rep}")
            out = "30,30,30,255" if rep else "255,255,255,200"
            ow = "0.6" if rep else "0.25"
            sz = f"{float(size) + (1.2 if rep else 0):.1f}"
            syms.append(f'<symbol type="marker" name="{i}">'
                        f'<layer class="SimpleMarker"><Option type="Map">'
                        f'<Option name="name" type="QString" value="circle"/>'
                        f'<Option name="color" type="QString" value="{fill}"/>'
                        f'<Option name="outline_color" type="QString" '
                        f'value="{out}"/>'
                        f'<Option name="outline_width" type="QString" '
                        f'value="{ow}"/>'
                        f'<Option name="size" type="QString" value="{sz}"/>'
                        f'</Option></layer></symbol>')
            legend = leg + (" (named in report)" if rep else "")
            rls.append(f'<rule key="k{i}" filter="{flt}" label="{legend}" '
                       f'symbol="{i}"/>')
            if val in nolabel_vals:
                i += 1
                continue
            fw = "75" if rep else "50"
            fs = "9" if rep else "7.5"
            labs.append(f'<rule filter="{flt}"><settings><text-style '
                        f'fieldName="{label_field}" fontSize="{fs}" '
                        f'fontWeight="{fw}" textColor="40,40,40,255">'
                        f'<text-buffer bufferDraw="1" bufferSize="1.0" '
                        f'bufferColor="255,255,255,225"/></text-style>'
                        f'<placement placement="6" dist="1.6" '
                        f'overlapHandling="AllowOverlapIfRequired"/>'
                        f'<rendering displayAll="1" obstacle="0"/>'
                        f'</settings></rule>')
            i += 1
    return (f'<!DOCTYPE qgis><qgis styleCategories="Symbology|Labeling" '
            f'labelsEnabled="1">'
            f'<renderer-v2 type="RuleRenderer"><rules key="root">'
            + "".join(rls) + '</rules><symbols>' + "".join(syms)
            + '</symbols></renderer-v2>'
            f'<labeling type="rule-based"><rules>' + "".join(labs)
            + '</rules></labeling></qgis>')


# candidate groups -> fill colours (colour codes the group; outline codes
# "named in report"). Qualitative, distinct from the surface ramps below.
CAND_CATS = [
    ("abandoned 1930s village on graded rock",
     "abandoned 1930s village on graded rock", "141,26,150,255", "4.2"),
    ("big village, hardly any farmland",
     "big village, hardly any farmland", "227,26,28,255", "4.2"),
    ("active clearing on graded rock",
     "active clearing on graded rock", "255,127,0,255", "4.2"),
    ("settled riverside on graded rock",
     "settled riverside on graded rock", "31,120,180,255", "4.2"),
    ("empty ground: rock and water only",
     "empty ground: rock and water only", "106,61,25,255", "4.2"),
    ("watchlist: abandoned 1930s village on gold",
     "watchlist village (1930s name, gold, empty today)",
     "251,154,153,255", "3.0"),
]

# inverted Spectral (blue=low -> red=high) for the probability surface;
# distinct from viridis (context) and blues (travel time) so stacked
# layers stay tellable-apart.
SPECTRAL_INV = ["94,79,162", "50,136,189", "102,194,165", "171,221,164",
                "230,245,152", "254,224,139", "253,174,97", "244,109,67",
                "213,62,79", "158,1,66"]

styles = {
    "01_evidence__known_mines": marker("cross2", "0,0,0,255",
                                       "255,255,255,255", "3.2"),
    "02_candidates": cat_marker(
        "group", CAND_CATS, "name",
        nolabel_vals=("watchlist: abandoned 1930s village on gold",)),
    "10_places__scored": graduated("pctile", 65, 100, VIRIDIS, "235",
                                   "marker"),
    "20_surface__context": graduated("pctile", 65, 100, VIRIDIS, "110"),
    "21_surface__probability": graduated("probability_pctile", 0, 100,
                                         SPECTRAL_INV, "130"),
    "22_surface__travel_time": graduated("minutes_to_start_place", 0, 1000,
                                         BLUES_R, "100"),
    "23_surface__gold_reachable": fill_single("214,137,16,80"),
    "30_context__basins": outline_style("70,90,140,200", label_field="name"),
}
con = sqlite3.connect(OUT)
con.execute("""CREATE TABLE IF NOT EXISTS layer_styles (
 id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT,
 f_table_schema TEXT, f_table_name TEXT, f_geometry_column TEXT,
 styleName TEXT, styleQML TEXT, styleSLD TEXT, useAsDefault BOOLEAN,
 description TEXT, owner TEXT, ui TEXT,
 update_time DATETIME DEFAULT CURRENT_TIMESTAMP)""")
try:
    con.execute("INSERT INTO gpkg_contents(table_name,data_type,identifier) "
                "VALUES('layer_styles','attributes','layer_styles')")
except sqlite3.IntegrityError:
    pass
for name, qml in styles.items():
    con.execute("INSERT INTO layer_styles(f_table_catalog,f_table_schema,"
                "f_table_name,f_geometry_column,styleName,styleQML,styleSLD,"
                "useAsDefault,description,owner) VALUES('','',?,?,?,?,'',1,"
                "'default','')", (name, "geom", name + "_default", qml))
con.commit(); con.close()
print("wrote", OUT)
