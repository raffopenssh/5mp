#!/usr/bin/env python3
"""Styled GPKG of the XSA mining prediction - one layer per information
class, symbology carried by attributes, so the method reads off the layer
panel top-to-bottom:

  00_plan__key_places       WHERE TO STAND: 11 bases + 8 remote outposts
                            (greedy max-coverage of the plan score within
                            one travel day; key_places_plan.json)
  01_evidence__known_mines  the 43-cluster truth set (report anchors)
  02_action__field_candidates  32 places worth a field check (in_report=1
                            for the ones the EASY report names)
  03_action__watchlist_villages 61 abandoned 1930s villages on gold rock
  10_places__scored         all 3,138 scored named places in ONE layer,
                            viridis by composite percentile, shape by kind
  20_surface__context       the top-35% composite surface, viridis by
                            percentile (measured tier skill in fields)
  21_surface__plan          the plan score (context+gravity+impunity votes)
                            on the top-20% surface, viridis
  22_surface__travel_time   minutes to the nearest start place over the
                            WHOLE grid (car/foot/bush best, local OSRM)
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
plan = json.load(open(SRC / "key_places_plan.json"))
axg = json.load(open(SRC / "access_x_geology_eval.json"))
zs = np.load(SRC / "plan_surface.npz")
cells = zs["cells"]; plan_v = zs["plan"]; conj_v = zs["conj"]
access_v = zs["access_min"]; ttown_v = zs["t_town_min"]
cell = float(pred["cell_deg"]); h = cell / 2
grad = pred["graduated"]

conj_q = min(s["q_bh_reach"] for s in axg["signals"] if s.get("q_bh_reach"))
conj_lift = max(s["lift_reach"] for s in axg["signals"]
                if s.get("q_bh_reach"))

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


# ---------------------------------------------------------------- 00 plan
# enrichment sources: nearest graduated settlement (persistence/crop/pop)
# and nearest 1930s place name
setts = grad["features"]["settlements"]
hists = grad["features"]["hist_places"]


def nearest(rows, lon, lat, max_km):
    best, bd = None, max_km
    for r in rows:
        d = km(lat, lon, r["lat"], r["lon"])
        if d < bd:
            best, bd = r, d
    return best, (round(bd, 1) if best else None)


lyr = mklayer("00_plan__key_places", ogr.wkbPoint, [
    ("rank", ogr.OFTInteger), ("name", ogr.OFTString),
    ("role", ogr.OFTString), ("basin", ogr.OFTString),
    ("kind", ogr.OFTString), ("place_class", ogr.OFTString),
    ("population", ogr.OFTInteger),
    ("gain_pct", ogr.OFTReal), ("cum_coverage_pct", ogr.OFTReal),
    ("known_mine_clusters_within_day", ogr.OFTInteger),
    ("composite_pctile", ogr.OFTReal),
    ("minutes_from_town_5k", ogr.OFTReal),
    ("gold_and_reachable_cell", ogr.OFTInteger),
    ("nearest_1930s_name", ogr.OFTString),
    ("nearest_1930s_km", ogr.OFTReal),
    ("settlement_persistence", ogr.OFTString),
    ("settlement_cropland_frac", ogr.OFTReal),
    ("method_note", ogr.OFTString)])
NOTE = ("greedy max-coverage of plan score (context+gravity+impunity, "
        "equal votes) within 480 min travel; see key_places_plan.json")
for p_ in plan["places"] + plan["remote_outposts"]:
    i = cell_of(p_["lon"], p_["lat"])
    ns, _ = nearest(setts, p_["lon"], p_["lat"], 6.0)
    nh, nhd = nearest(hists, p_["lon"], p_["lat"], 12.0)
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("rank", p_["rank"])
    f.SetField("name", p_["name"])
    f.SetField("role", "base" if p_ in plan["places"] else "outpost")
    f.SetField("basin", p_["basin"])
    f.SetField("kind", p_["kind"]); f.SetField("place_class", p_["cls"] or "")
    if p_["pop"]: f.SetField("population", int(p_["pop"]))
    f.SetField("gain_pct", p_["gain_pct"])
    f.SetField("cum_coverage_pct", p_["cum_coverage_pct"])
    f.SetField("known_mine_clusters_within_day",
               p_["truth_clusters_within_day"])
    pc = plan_v[i]
    if np.isfinite(pc):
        f.SetField("composite_pctile", round(float(pc) * 100, 1))
    f.SetField("minutes_from_town_5k", round(float(ttown_v[i]), 0))
    f.SetField("gold_and_reachable_cell", int(conj_v[i]))
    if nh:
        f.SetField("nearest_1930s_name", nh["name"] or "(unnamed)")
        f.SetField("nearest_1930s_km", nhd)
    if ns:
        f.SetField("settlement_persistence", ns.get("persistence") or "")
        if ns.get("cropland_frac_2019") is not None:
            f.SetField("settlement_cropland_frac", ns["cropland_frac_2019"])
    f.SetField("method_note", NOTE)
    f.SetGeometry(pt(p_["lon"], p_["lat"])); lyr.CreateFeature(f)

# ---------------------------------------------------------- 01 known mines
lyr = mklayer("01_evidence__known_mines", ogr.wkbPoint,
              [("source", ogr.OFTString), ("resource", ogr.OFTString)])
for a in pred["anchors"]:
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("source", a["source"] or "")
    f.SetField("resource", a["resource"] or "")
    f.SetGeometry(pt(a["lon"], a["lat"])); lyr.CreateFeature(f)

# ----------------------------------------------------------- 02 candidates
lyr = mklayer("02_action__field_candidates", ogr.wkbPoint, [
    ("name", ogr.OFTString), ("basin", ogr.OFTString),
    ("character", ogr.OFTString),
    ("settlement_population_est", ogr.OFTInteger),
    ("settlement_cropland_frac", ogr.OFTReal),
    ("in_report", ogr.OFTInteger), ("snap_source", ogr.OFTString),
    ("snap_km", ogr.OFTReal), ("tier", ogr.OFTString),
    ("composite_pctile", ogr.OFTReal), ("km_to_known_anchor", ogr.OFTReal),
    ("gold_contact_km", ogr.OFTReal), ("river_km", ogr.OFTReal),
    ("settlement_km", ogr.OFTReal), ("deforestation_km", ogr.OFTReal),
    ("travel_minutes_best", ogr.OFTReal),
    ("minutes_from_town_5k", ogr.OFTReal),
    ("hist_label", ogr.OFTString)])
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
    f.SetField("name", name); f.SetField("basin", c.get("basin") or "")
    f.SetField("character", c.get("character") or "")
    if c.get("settlement_population_est") is not None:
        f.SetField("settlement_population_est",
                   int(c["settlement_population_est"]))
    if c.get("settlement_cropland_frac") is not None:
        f.SetField("settlement_cropland_frac", c["settlement_cropland_frac"])
    f.SetField("in_report",
               1 if (round(c["lat"], 4), round(c["lon"], 4))
               in REPORT_CAND_CELLS else 0)
    f.SetField("snap_source", src)
    f.SetField("snap_km", round(km(lat, lon, c["lat"], c["lon"]), 1))
    for k in ("tier", "composite_pctile", "km_to_known_anchor",
              "gold_contact_km", "river_km", "settlement_km",
              "deforestation_km"):
        f.SetField(k, c[k])
    ci_ = cell_of(c["lon"], c["lat"])
    f.SetField("travel_minutes_best", round(float(access_v[ci_]), 0))
    f.SetField("minutes_from_town_5k", round(float(ttown_v[ci_]), 0))
    if hl: f.SetField("hist_label", f'{hl["text"]} ({hl["dist_km"]} km)')
    f.SetGeometry(pt(lon, lat)); lyr.CreateFeature(f)

# ------------------------------------------------------------ 03 watchlist
lyr = mklayer("03_action__watchlist_villages", ogr.wkbPoint,
              [("rank", ogr.OFTInteger), ("name", ogr.OFTString),
               ("in_report", ogr.OFTInteger), ("basin", ogr.OFTString),
               ("composite_pctile", ogr.OFTReal),
               ("gold_contact_km", ogr.OFTReal),
               ("river_km", ogr.OFTReal),
               ("travel_minutes_best", ogr.OFTReal),
               ("minutes_from_town_5k", ogr.OFTReal)])
for w in pred["abandoned_village_gold_watchlist"]["places"]:
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("rank", w["rank"])
    f.SetField("name", w["name"] or "(unnamed)")
    f.SetField("in_report", 1 if w["name"] in REPORT_WATCH_NAMES else 0)
    f.SetField("basin", w.get("basin") or "")
    f.SetField("composite_pctile", w["composite_pctile"])
    f.SetField("gold_contact_km", w["gold_contact_km"])
    f.SetField("river_km", w["river_km"])
    wi_ = cell_of(w["lon"], w["lat"])
    f.SetField("travel_minutes_best", round(float(access_v[wi_]), 0))
    f.SetField("minutes_from_town_5k", round(float(ttown_v[wi_]), 0))
    f.SetGeometry(pt(w["lon"], w["lat"])); lyr.CreateFeature(f)

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

lyr = mklayer("21_surface__plan", ogr.wkbPolygon,
              [("plan_pctile", ogr.OFTReal),
               ("minutes_to_start_place", ogr.OFTReal),
               ("minutes_from_town", ogr.OFTReal)])
ok = np.isfinite(plan_v)
ranks = np.full(len(plan_v), np.nan)
r = np.argsort(np.argsort(plan_v[ok]))
ranks[ok] = 100.0 * r / max(len(r) - 1, 1)
n_plan = 0
for i in np.where(ok)[0]:
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("plan_pctile", round(float(ranks[i]), 1))
    f.SetField("minutes_to_start_place", round(float(access_v[i]), 0))
    f.SetField("minutes_from_town_5k", round(float(ttown_v[i]), 0))
    f.SetGeometry(cellpoly(*map(float, cells[i]))); lyr.CreateFeature(f)
    n_plan += 1

lyr = mklayer("22_surface__travel_time", ogr.wkbPolygon,
              [("minutes_to_start_place", ogr.OFTReal),
               ("minutes_from_town", ogr.OFTReal)])
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
print(f"places {n_places}, surface {n_surf}, plan {n_plan}, conj {n_conj}")

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
styles = {
    "00_plan__key_places": rule_marker(
        [(f"{AMP}role{AMP}='base'", "base (rank 1-11)", "star",
          "20,120,60,255", "0,60,25,255", "7", "75"),
         (f"{AMP}role{AMP}='outpost'", "remote outpost (12-19)",
          "pentagon", "110,190,120,255", "20,90,45,255", "5", "50")],
        label_field="name"),
    "01_evidence__known_mines": marker("cross2", "0,0,0,255",
                                       "255,255,255,255", "3.2"),
    "02_action__field_candidates": rule_marker(
        [(f"{AMP}in_report{AMP}=1", "named in report", "star",
          "178,0,29,255", "40,0,8,255", "6.5", "75"),
         (f"{AMP}in_report{AMP}=0", "other candidate", "star",
          "227,26,28,255", "255,255,255,255", "4.5", "50")],
        label_field="name"),
    "03_action__watchlist_villages": rule_marker(
        [(f"{AMP}in_report{AMP}=1", "named in report", "triangle",
          "230,120,0,255", "90,45,0,255", "5", "75"),
         (f"{AMP}in_report{AMP}=0", "other village", "triangle",
          "255,170,0,255", "120,70,0,255", "3.2", "50")],
        label_field="name", label_size="8"),
    "10_places__scored": graduated("pctile", 65, 100, VIRIDIS, "235",
                                   "marker"),
    "20_surface__context": graduated("pctile", 65, 100, VIRIDIS, "110"),
    "21_surface__plan": graduated("plan_pctile", 0, 100, VIRIDIS, "150"),
    "22_surface__travel_time": graduated("minutes_to_start_place", 0, 1000,
                                         BLUES_R, "120"),
    "23_surface__gold_reachable": fill_single("214,137,16,90"),
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
