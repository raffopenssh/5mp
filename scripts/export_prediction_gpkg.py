#!/usr/bin/env python3
"""Export data/eval/xsa_mining/prediction.{json,geojson} to a styled GPKG.

Three layers, each with a QGIS style embedded in layer_styles so the file
opens in QGIS looking like a map, not a grid of dots:

- candidates       : the 12 report candidates snapped to their real named
                     location (nearest settlement, else nearest historic
                     label, else the cell centre). Fields keep the cell
                     centre + snap distance so precision is never implied.
- watchlist_villages : abandoned 1930s villages on gold contacts (real
                     georeferenced map positions already).
- composite_surface: the top-20% scored 0.05 deg cells as square polygons,
                     graduated heat, semi-transparent.
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

srs = osr.SpatialReference(); srs.ImportFromEPSG(4326)
if OUT.exists(): OUT.unlink()
drv = ogr.GetDriverByName("GPKG")
ds = drv.CreateDataSource(str(OUT))

def km(lat1, lon1, lat2, lon2):
    ky = 111.32
    kx = ky * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat1 - lat2) * ky, (lon1 - lon2) * kx)

# ---- candidates at real locations
lyr = ds.CreateLayer("candidates", srs, ogr.wkbPoint)
fields = [("name", ogr.OFTString), ("snap_source", ogr.OFTString),
          ("snap_km", ogr.OFTReal), ("tier", ogr.OFTString),
          ("composite_pctile", ogr.OFTReal),
          ("km_to_known_anchor", ogr.OFTReal),
          ("gold_contact_km", ogr.OFTReal), ("river_km", ogr.OFTReal),
          ("settlement_km", ogr.OFTReal), ("deforestation_km", ogr.OFTReal),
          ("cell_lat", ogr.OFTReal), ("cell_lon", ogr.OFTReal),
          ("hist_label", ogr.OFTString)]
for n, t in fields: lyr.CreateField(ogr.FieldDefn(n, t))
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
    f.SetField("name", name); f.SetField("snap_source", src)
    f.SetField("snap_km", round(km(lat, lon, c["lat"], c["lon"]), 1))
    for k in ("tier", "composite_pctile", "km_to_known_anchor",
              "gold_contact_km", "river_km", "settlement_km",
              "deforestation_km"):
        f.SetField(k, c[k])
    f.SetField("cell_lat", c["lat"]); f.SetField("cell_lon", c["lon"])
    if hl: f.SetField("hist_label", f'{hl["text"]} ({hl["dist_km"]} km)')
    g = ogr.Geometry(ogr.wkbPoint); g.AddPoint(lon, lat); f.SetGeometry(g)
    lyr.CreateFeature(f)

# ---- watchlist villages (real positions already)
lyr = ds.CreateLayer("watchlist_villages", srs, ogr.wkbPoint)
for n, t in [("name", ogr.OFTString), ("gold_contact_km", ogr.OFTReal),
             ("river_km", ogr.OFTReal)]:
    lyr.CreateField(ogr.FieldDefn(n, t))
for w in pred["abandoned_village_gold_watchlist"]["places"]:
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("name", w["name"] or "(unnamed)")
    f.SetField("gold_contact_km", w["gold_contact_km"])
    f.SetField("river_km", w["river_km"])
    g = ogr.Geometry(ogr.wkbPoint); g.AddPoint(w["lon"], w["lat"])
    f.SetGeometry(g); lyr.CreateFeature(f)

# ---- composite surface as cell polygons
lyr = ds.CreateLayer("composite_surface", srs, ogr.wkbPolygon)
for n, t in [("score", ogr.OFTReal), ("pctile", ogr.OFTReal)]:
    lyr.CreateField(ogr.FieldDefn(n, t))
h = cell / 2
for ft in gj["features"]:
    if ft["properties"].get("layer") != "composite_top20": continue
    lon, lat = ft["geometry"]["coordinates"]
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for dx, dy in ((-h,-h),(h,-h),(h,h),(-h,h),(-h,-h)):
        ring.AddPoint(lon+dx, lat+dy)
    poly = ogr.Geometry(ogr.wkbPolygon); poly.AddGeometry(ring)
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("score", ft["properties"]["score"])
    f.SetField("pctile", ft["properties"]["pctile"])
    f.SetGeometry(poly); lyr.CreateFeature(f)
ds = None

# ---- embed QGIS styles
QML_CAND = """<!DOCTYPE qgis><qgis styleCategories="Symbology|Labeling" labelsEnabled="1">
<renderer-v2 type="singleSymbol"><symbols><symbol type="marker" name="0">
<layer class="SimpleMarker"><Option type="Map">
<Option name="name" type="QString" value="star"/>
<Option name="color" type="QString" value="227,26,28,255"/>
<Option name="outline_color" type="QString" value="255,255,255,255"/>
<Option name="outline_width" type="QString" value="0.4"/>
<Option name="size" type="QString" value="5"/>
</Option></layer></symbol></symbols></renderer-v2>
<labeling type="simple"><settings><text-style fieldName="name" fontSize="9" textColor="120,10,10,255">
<text-buffer bufferDraw="1" bufferSize="1" bufferColor="255,255,255,220"/>
</text-style><placement placement="6" dist="2"/></settings></labeling></qgis>"""

QML_WATCH = """<!DOCTYPE qgis><qgis styleCategories="Symbology|Labeling" labelsEnabled="1">
<renderer-v2 type="singleSymbol"><symbols><symbol type="marker" name="0">
<layer class="SimpleMarker"><Option type="Map">
<Option name="name" type="QString" value="triangle"/>
<Option name="color" type="QString" value="255,170,0,255"/>
<Option name="outline_color" type="QString" value="120,70,0,255"/>
<Option name="outline_width" type="QString" value="0.3"/>
<Option name="size" type="QString" value="3.4"/>
</Option></layer></symbol></symbols></renderer-v2>
<labeling type="simple"><settings><text-style fieldName="name" fontSize="8" textColor="120,70,0,255">
<text-buffer bufferDraw="1" bufferSize="0.9" bufferColor="255,255,255,200"/>
</text-style><placement placement="6" dist="1.5"/></settings></labeling></qgis>"""

QML_SURF = """<!DOCTYPE qgis><qgis styleCategories="Symbology">
<renderer-v2 type="graduatedSymbol" attr="score" graduatedMethod="GraduatedColor">
<ranges>
<range lower="0.0" upper="0.55" symbol="0" label="low"/>
<range lower="0.55" upper="0.65" symbol="1" label="med"/>
<range lower="0.65" upper="0.75" symbol="2" label="high"/>
<range lower="0.75" upper="1.01" symbol="3" label="top"/>
</ranges><symbols>
<symbol type="fill" name="0"><layer class="SimpleFill"><Option type="Map"><Option name="color" type="QString" value="255,255,178,90"/><Option name="outline_style" type="QString" value="no"/></Option></layer></symbol>
<symbol type="fill" name="1"><layer class="SimpleFill"><Option type="Map"><Option name="color" type="QString" value="254,204,92,110"/><Option name="outline_style" type="QString" value="no"/></Option></layer></symbol>
<symbol type="fill" name="2"><layer class="SimpleFill"><Option type="Map"><Option name="color" type="QString" value="253,141,60,130"/><Option name="outline_style" type="QString" value="no"/></Option></layer></symbol>
<symbol type="fill" name="3"><layer class="SimpleFill"><Option type="Map"><Option name="color" type="QString" value="227,26,28,150"/><Option name="outline_style" type="QString" value="no"/></Option></layer></symbol>
</symbols></renderer-v2></qgis>"""

con = sqlite3.connect(OUT)
con.execute("""CREATE TABLE IF NOT EXISTS layer_styles (
 id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT, f_table_schema TEXT,
 f_table_name TEXT, f_geometry_column TEXT, styleName TEXT, styleQML TEXT,
 styleSLD TEXT, useAsDefault BOOLEAN, description TEXT, owner TEXT,
 ui TEXT, update_time DATETIME DEFAULT CURRENT_TIMESTAMP)""")
try:
    con.execute("INSERT INTO gpkg_contents(table_name,data_type,identifier) VALUES('layer_styles','attributes','layer_styles')")
except sqlite3.IntegrityError:
    pass
for name, qml in [("candidates", QML_CAND),
                  ("watchlist_villages", QML_WATCH),
                  ("composite_surface", QML_SURF)]:
    con.execute("INSERT INTO layer_styles(f_table_catalog,f_table_schema,"
                "f_table_name,f_geometry_column,styleName,styleQML,styleSLD,"
                "useAsDefault,description,owner) VALUES('','',?,?,?,?,'',1,"
                "'default','')",
                (name, "geom", name + "_default", qml))
con.commit(); con.close()
print("wrote", OUT)
