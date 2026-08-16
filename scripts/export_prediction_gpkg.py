#!/usr/bin/env python3
"""Export data/eval/xsa_mining/prediction.{json,geojson} to a styled GPKG
plus a QGIS layer-tree (.qlr is not portable across paths, so we emit a
project-loadable structure via layer NAME prefixes and embedded styles;
QGIS groups by the double-underscore prefix when you sort, and the numeric
prefix keeps folders in tier order).

Layer plan ("folders" = one group of layers per graduated tier):

  00_known__anchors            reported mine sites (truth, black crosses)
  01_candidates__report_points the 12 report candidates (red stars)
  02_watchlist__villages       abandoned 1930s villages on gold contacts
  10_top05__settlements        features standing on top-5% ground
  10_top05__hist_places          (capture/lift/p of each band is measured;
  10_top05__osm_places            see prediction.json graduated.tiers)
  11_top10__...                three layers per band, four bands
  12_top20__...
  13_top35__...
  20_surface__top05 ... top35  the scored cells themselves, one layer per
                               band so each band is a folder entry too.

Every tier layer carries score/pctile + the tier's measured lift and p as
constant fields, so the number travels with the download (invariant 7:
a graduated map without its skill beside it reads as a ranking).
"""
import json
import math
import sqlite3
from pathlib import Path

from osgeo import ogr, osr

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/eval/xsa_mining"
OUT = SRC / "xsa_mining_prediction.gpkg"

pred = json.load(open(SRC / "prediction.json"))
gj = json.load(open(SRC / "prediction.geojson"))
cell = float(pred["cell_deg"])

# Places the EASY report (section 4b) names in prose. Flagged in the GPKG
# (in_report=1, distinct symbol) so the text and the map point at the same
# things. MUST be kept in sync with
# reports/XSA_CONSERVATION_REVIEW_2026-08_EASY.txt.
REPORT_CAND_CELLS = {
    (8.4270, 27.5288), (7.9270, 27.8288), (6.1770, 27.7788),
    (8.9770, 22.9788),                       # abandoned-village group
    (7.6770, 28.0288), (7.2770, 28.6788), (5.8270, 30.8288),
    # big-village-no-farmland group
    (5.3270, 25.4288), (6.5270, 23.8788), (7.2270, 28.3788),
    # active-clearing group
    (7.5270, 28.5288),                       # settled riverside
    (8.4770, 24.5288),                       # empty ground
}
REPORT_WATCH_NAMES = {
    "Toich", "Tup Weng", "Tambora A.M. & C.C.", "Kyango", "Ibrahim",
    "(TIDI)",
}
grad = pred["graduated"]
TIERS = ["top05", "top10", "top20", "top35"]
TIER_PREFIX = {"top05": "10", "top10": "11", "top20": "12", "top35": "13"}

srs = osr.SpatialReference(); srs.ImportFromEPSG(4326)
if OUT.exists(): OUT.unlink()
drv = ogr.GetDriverByName("GPKG")
ds = drv.CreateDataSource(str(OUT))

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

# ---- 00 known anchors (truth)
lyr = mklayer("00_known__anchors", ogr.wkbPoint,
              [("source", ogr.OFTString), ("resource", ogr.OFTString)])
for a in pred["anchors"]:
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("source", a["source"] or "")
    f.SetField("resource", a["resource"] or "")
    f.SetGeometry(pt(a["lon"], a["lat"])); lyr.CreateFeature(f)

# ---- 01 candidates at real locations
lyr = mklayer("01_candidates__report_points", ogr.wkbPoint, [
    ("name", ogr.OFTString), ("basin", ogr.OFTString),
    ("character", ogr.OFTString),
    ("settlement_population_est", ogr.OFTInteger),
    ("settlement_cropland_frac", ogr.OFTReal),
    ("in_report", ogr.OFTInteger),
    ("snap_source", ogr.OFTString),
    ("snap_km", ogr.OFTReal), ("tier", ogr.OFTString),
    ("composite_pctile", ogr.OFTReal), ("km_to_known_anchor", ogr.OFTReal),
    ("gold_contact_km", ogr.OFTReal), ("river_km", ogr.OFTReal),
    ("settlement_km", ogr.OFTReal), ("deforestation_km", ogr.OFTReal),
    ("cell_lat", ogr.OFTReal), ("cell_lon", ogr.OFTReal),
    ("hist_label", ogr.OFTString)])
for c in pred["candidates"]:
    ns, hl = c.get("nearest_settlement"), c.get("nearest_hist_label")
    if hl:
        lat, lon, name, src = hl["lat"], hl["lon"], hl["text"], "hist_label_1930s"
    elif ns:
        nm = ns["place"] if ns["place"] and len(ns["place"]) > 2 else "(unnamed settlement)"
        lat, lon, name, src = ns["lat"], ns["lon"], nm, "settlement"
    else:
        lat, lon, name, src = c["lat"], c["lon"], "(cell centre)", "cell_centre"
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("name", name); f.SetField("basin", c.get("basin") or "")
    f.SetField("character", c.get("character") or "")
    if c.get("settlement_population_est") is not None:
        f.SetField("settlement_population_est",
                   int(c["settlement_population_est"]))
    if c.get("settlement_cropland_frac") is not None:
        f.SetField("settlement_cropland_frac",
                   c["settlement_cropland_frac"])
    f.SetField("in_report",
               1 if (round(c["lat"], 4), round(c["lon"], 4))
               in REPORT_CAND_CELLS else 0)
    f.SetField("snap_source", src)
    f.SetField("snap_km", round(km(lat, lon, c["lat"], c["lon"]), 1))
    for k in ("tier", "composite_pctile", "km_to_known_anchor",
              "gold_contact_km", "river_km", "settlement_km",
              "deforestation_km"):
        f.SetField(k, c[k])
    f.SetField("cell_lat", c["lat"]); f.SetField("cell_lon", c["lon"])
    if hl: f.SetField("hist_label", f'{hl["text"]} ({hl["dist_km"]} km)')
    f.SetGeometry(pt(lon, lat)); lyr.CreateFeature(f)

# ---- 02 watchlist villages
lyr = mklayer("02_watchlist__villages", ogr.wkbPoint,
              [("rank", ogr.OFTInteger), ("name", ogr.OFTString),
               ("in_report", ogr.OFTInteger),
               ("basin", ogr.OFTString),
               ("composite_pctile", ogr.OFTReal),
               ("gold_contact_km", ogr.OFTReal),
               ("river_km", ogr.OFTReal)])
for w in pred["abandoned_village_gold_watchlist"]["places"]:
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("rank", w["rank"])
    f.SetField("name", w["name"] or "(unnamed)")
    f.SetField("in_report", 1 if w["name"] in REPORT_WATCH_NAMES else 0)
    f.SetField("basin", w.get("basin") or "")
    f.SetField("composite_pctile", w["composite_pctile"])
    f.SetField("gold_contact_km", w["gold_contact_km"])
    f.SetField("river_km", w["river_km"])
    f.SetGeometry(pt(w["lon"], w["lat"])); lyr.CreateFeature(f)

# ---- 03 drainage basins (grouping unit; HydroBASINS level 5)
basin_meta = {b["pfaf_id"]: b for b in pred["basins"]["list"]}
lyr = mklayer("03_basins__hydrobasins_lev5", ogr.wkbMultiPolygon,
              [("name", ogr.OFTString), ("pfaf_id", ogr.OFTInteger),
               ("level", ogr.OFTInteger), ("aoi_share", ogr.OFTReal)])
for fn in ("hybas5_xsa.json", "hybas6_xsa.json"):
    for ft in json.load(open(ROOT / "data" / "hydrobasins" / fn))["features"]:
        pid = int(ft["properties"]["PFAF_ID"])
        b = basin_meta.get(pid)
        if not b or b["level"] != (5 if fn == "hybas5_xsa.json" else 6):
            continue
        g = ogr.CreateGeometryFromJson(json.dumps(ft["geometry"]))
        g = ogr.ForceToMultiPolygon(g)
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("name", b["name"])
        f.SetField("pfaf_id", pid)
        f.SetField("level", b["level"])
        f.SetField("aoi_share", b["aoi_share"])
        f.SetGeometry(g); lyr.CreateFeature(f)

# ---- 10..13 graduated feature folders
def tier_meta(t):
    m = grad["tiers"][t]
    return (m.get("lift"), m.get("p"),
            m.get("lift_reach"), m.get("p_reach"))

COMMON = [("name", ogr.OFTString), ("score", ogr.OFTReal),
          ("pctile", ogr.OFTReal), ("tier_lift", ogr.OFTReal),
          ("tier_p", ogr.OFTReal), ("tier_lift_reach", ogr.OFTReal),
          ("tier_p_reach", ogr.OFTReal)]
SPEC = {
    "settlements": COMMON + [("classification", ogr.OFTString),
                             ("persistence", ogr.OFTString),
                             ("population_est", ogr.OFTInteger),
                             ("cropland_frac_2019", ogr.OFTReal),
                             ("area_m2", ogr.OFTReal)],
    "hist_places": COMMON,
    "osm_places": COMMON + [("place_type", ogr.OFTString)],
}
counts = {}
for t in TIERS:
    lift, p, lift_r, p_r = tier_meta(t)
    for kind in ("settlements", "hist_places", "osm_places"):
        rows = [x for x in grad["features"][kind] if x["tier"] == t]
        counts[f"{t}/{kind}"] = len(rows)
        lyr = mklayer(f"{TIER_PREFIX[t]}_{t}__{kind}", ogr.wkbPoint, SPEC[kind])
        for x in rows:
            f = ogr.Feature(lyr.GetLayerDefn())
            f.SetField("name", (x.get("name") or "(unnamed)"))
            f.SetField("score", x["score"]); f.SetField("pctile", x["pctile"])
            if lift is not None: f.SetField("tier_lift", lift)
            if p is not None: f.SetField("tier_p", p)
            if lift_r is not None: f.SetField("tier_lift_reach", lift_r)
            if p_r is not None: f.SetField("tier_p_reach", p_r)
            for extra in SPEC[kind][len(COMMON):]:
                k = extra[0]
                if x.get(k) is not None:
                    f.SetField(k, x[k])
            f.SetGeometry(pt(x["lon"], x["lat"])); lyr.CreateFeature(f)

# ---- 20 surface: one polygon layer per band
h = cell / 2
surf = {}
for ft in gj["features"]:
    pr = ft["properties"]
    if pr.get("layer") != "composite_graduated": continue
    surf.setdefault(pr["tier"], []).append(ft)
for t in TIERS:
    lift, p, lift_r, p_r = tier_meta(t)
    lyr = mklayer(f"20_surface__{t}", ogr.wkbPolygon,
                  [("score", ogr.OFTReal), ("pctile", ogr.OFTReal),
                   ("tier_lift", ogr.OFTReal), ("tier_p", ogr.OFTReal),
                   ("tier_lift_reach", ogr.OFTReal),
                   ("tier_p_reach", ogr.OFTReal)])
    for ft in surf.get(t, []):
        lon, lat = ft["geometry"]["coordinates"]
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for dx, dy in ((-h,-h),(h,-h),(h,h),(-h,h),(-h,-h)):
            ring.AddPoint(lon+dx, lat+dy)
        poly = ogr.Geometry(ogr.wkbPolygon); poly.AddGeometry(ring)
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("score", ft["properties"]["score"])
        f.SetField("pctile", ft["properties"]["pctile"])
        if lift is not None: f.SetField("tier_lift", lift)
        if p is not None: f.SetField("tier_p", p)
        if lift_r is not None: f.SetField("tier_lift_reach", lift_r)
        if p_r is not None: f.SetField("tier_p_reach", p_r)
        f.SetGeometry(poly); lyr.CreateFeature(f)
ds = None
print("layer counts:", counts)

# ---- embed QGIS styles ------------------------------------------------
def marker(shape_, color, outline, size, label_field=None, label_color=None,
           label_size="8", buffer_alpha="200"):
    lab = ""
    if label_field:
        lab = (f'<labeling type="simple"><settings><text-style '
               f'fieldName="{label_field}" fontSize="{label_size}" '
               f'textColor="{label_color}">'
               f'<text-buffer bufferDraw="1" bufferSize="0.9" '
               f'bufferColor="255,255,255,{buffer_alpha}"/></text-style>'
               f'<placement placement="6" dist="1.5"/></settings></labeling>')
    return (f'<!DOCTYPE qgis><qgis styleCategories="Symbology|Labeling" '
            f'labelsEnabled="{1 if label_field else 0}">'
            f'<renderer-v2 type="singleSymbol"><symbols>'
            f'<symbol type="marker" name="0"><layer class="SimpleMarker">'
            f'<Option type="Map">'
            f'<Option name="name" type="QString" value="{shape_}"/>'
            f'<Option name="color" type="QString" value="{color}"/>'
            f'<Option name="outline_color" type="QString" value="{outline}"/>'
            f'<Option name="outline_width" type="QString" value="0.3"/>'
            f'<Option name="size" type="QString" value="{size}"/>'
            f'</Option></layer></symbol></symbols></renderer-v2>{lab}</qgis>')

def fill(color):
    return (f'<!DOCTYPE qgis><qgis styleCategories="Symbology">'
            f'<renderer-v2 type="singleSymbol"><symbols>'
            f'<symbol type="fill" name="0"><layer class="SimpleFill">'
            f'<Option type="Map">'
            f'<Option name="color" type="QString" value="{color}"/>'
            f'<Option name="outline_style" type="QString" value="no"/>'
            f'</Option></layer></symbol></symbols></renderer-v2></qgis>')

# graduated palette: hot -> cool with size/opacity stepping down
TIER_COLOR = {  # fill rgba for points
    "top05": ("227,26,28,255", "4.0"),
    "top10": ("253,141,60,235", "3.4"),
    "top20": ("254,204,92,215", "2.8"),
    "top35": ("255,255,178,195", "2.2"),
}
SURF_COLOR = {
    "top05": "227,26,28,140",
    "top10": "253,141,60,120",
    "top20": "254,204,92,100",
    "top35": "255,255,178,80",
}
KIND_SHAPE = {"settlements": "circle", "hist_places": "square",
              "osm_places": "diamond"}

def outline(color, width="0.5", label_field=None, label_color="60,60,60,255",
            label_size="10"):
    lab = ""
    if label_field:
        lab = (f'<labeling type="simple"><settings><text-style '
               f'fieldName="{label_field}" fontSize="{label_size}" '
               f'textColor="{label_color}">'
               f'<text-buffer bufferDraw="1" bufferSize="1.2" '
               f'bufferColor="255,255,255,210"/></text-style>'
               f'<placement placement="0"/></settings></labeling>')
    return (f'<!DOCTYPE qgis><qgis styleCategories="Symbology|Labeling" '
            f'labelsEnabled="{1 if label_field else 0}">'
            f'<renderer-v2 type="singleSymbol"><symbols>'
            f'<symbol type="fill" name="0"><layer class="SimpleFill">'
            f'<Option type="Map">'
            f'<Option name="style" type="QString" value="no"/>'
            f'<Option name="outline_color" type="QString" value="{color}"/>'
            f'<Option name="outline_width" type="QString" value="{width}"/>'
            f'<Option name="outline_style" type="QString" value="dash"/>'
            f'</Option></layer></symbol></symbols></renderer-v2>{lab}</qgis>')


def marker_flagged(shape_, color, outline_, size,
                   flag_color, flag_outline, flag_size,
                   label_field, label_color, label_size="8",
                   buffer_alpha="200"):
    """Rule-based marker style: in_report=1 features draw bigger, in their
    own colour, with a bold label - the GPKG highlights exactly what the
    EASY report's prose names."""
    def sym(name, col, out, sz, outw):
        return (f'<symbol type="marker" name="{name}">'
                f'<layer class="SimpleMarker"><Option type="Map">'
                f'<Option name="name" type="QString" value="{shape_}"/>'
                f'<Option name="color" type="QString" value="{col}"/>'
                f'<Option name="outline_color" type="QString" value="{out}"/>'
                f'<Option name="outline_width" type="QString" value="{outw}"/>'
                f'<Option name="size" type="QString" value="{sz}"/>'
                f'</Option></layer></symbol>')
    lab = (f'<labeling type="rule-based"><rules>'
           f'<rule filter="&quot;in_report&quot;=1"><settings><text-style '
           f'fieldName="{label_field}" fontSize="{int(label_size)+2}" '
           f'fontWeight="75" textColor="{flag_outline}">'
           f'<text-buffer bufferDraw="1" bufferSize="1.1" '
           f'bufferColor="255,255,255,235"/></text-style>'
           f'<placement placement="6" dist="2"/></settings></rule>'
           f'<rule filter="&quot;in_report&quot;=0"><settings><text-style '
           f'fieldName="{label_field}" fontSize="{label_size}" '
           f'textColor="{label_color}">'
           f'<text-buffer bufferDraw="1" bufferSize="0.9" '
           f'bufferColor="255,255,255,{buffer_alpha}"/></text-style>'
           f'<placement placement="6" dist="1.5"/></settings></rule>'
           f'</rules></labeling>')
    return (f'<!DOCTYPE qgis><qgis styleCategories="Symbology|Labeling" '
            f'labelsEnabled="1">'
            f'<renderer-v2 type="RuleRenderer"><rules key="root">'
            f'<rule key="r1" filter="&quot;in_report&quot;=1" '
            f'label="named in report" symbol="0"/>'
            f'<rule key="r0" filter="&quot;in_report&quot;=0" '
            f'label="other" symbol="1"/></rules><symbols>'
            f'{sym("0", flag_color, flag_outline, flag_size, "0.6")}'
            f'{sym("1", color, outline_, size, "0.3")}'
            f'</symbols></renderer-v2>{lab}</qgis>')

styles = {
    "03_basins__hydrobasins_lev5": outline(
        "70,90,140,200", label_field="name",
        label_color="70,90,140,255"),
    "00_known__anchors": marker("cross2", "0,0,0,255", "255,255,255,255",
                                "3.2"),
    "01_candidates__report_points": marker_flagged(
        "star", "227,26,28,255", "255,255,255,255", "5",
        flag_color="178,0,29,255", flag_outline="40,0,8,255",
        flag_size="7",
        label_field="name", label_color="120,10,10,255", label_size="9",
        buffer_alpha="220"),
    "02_watchlist__villages": marker_flagged(
        "triangle", "255,170,0,255", "120,70,0,255", "3.4",
        flag_color="230,120,0,255", flag_outline="90,45,0,255",
        flag_size="5.2",
        label_field="name", label_color="120,70,0,255"),
}
for t in TIERS:
    color, size = TIER_COLOR[t]
    for kind in ("settlements", "hist_places", "osm_places"):
        # label only the two hottest bands - 2,000 labels is noise
        lf = "name" if t in ("top05", "top10") else None
        styles[f"{TIER_PREFIX[t]}_{t}__{kind}"] = marker(
            KIND_SHAPE[kind], color, "60,60,60,180", size,
            label_field=lf, label_color="80,40,10,255", label_size="7",
            buffer_alpha="180")
    styles[f"20_surface__{t}"] = fill(SURF_COLOR[t])

con = sqlite3.connect(OUT)
con.execute("""CREATE TABLE IF NOT EXISTS layer_styles (
 id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT, f_table_schema TEXT,
 f_table_name TEXT, f_geometry_column TEXT, styleName TEXT, styleQML TEXT,
 styleSLD TEXT, useAsDefault BOOLEAN, description TEXT, owner TEXT,
 ui TEXT, update_time DATETIME DEFAULT CURRENT_TIMESTAMP)""")
try:
    con.execute("INSERT INTO gpkg_contents(table_name,data_type,identifier) "
                "VALUES('layer_styles','attributes','layer_styles')")
except sqlite3.IntegrityError:
    pass
for name, qml in styles.items():
    con.execute("INSERT INTO layer_styles(f_table_catalog,f_table_schema,"
                "f_table_name,f_geometry_column,styleName,styleQML,styleSLD,"
                "useAsDefault,description,owner) VALUES('','',?,?,?,?,'',1,"
                "'default','')",
                (name, "geom", name + "_default", qml))
con.commit(); con.close()
print("wrote", OUT)
