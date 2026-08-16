#!/usr/bin/env python3
"""Embed default QGIS styles in the labels GPKG (layer_styles table) so the
layers render meaningfully on first open: lines colored by kind (matching the
sheets' own conventions where possible), symbols by category, labels as small
text markers. Usage: make_gpkg_styles.py <gpkg>"""
import sqlite3
import sys

LINE_COLORS = {  # echo the sheet conventions: water blue-ish, rail dark, etc.
    "watercourse": ("31,120,180", "0.5", "solid"),
    "track": ("140,81,10", "0.4", "dash"),
    "road": ("227,26,28", "0.7", "solid"),
    "railway": ("0,0,0", "0.8", "solid"),
    "telegraph": ("255,127,0", "0.4", "dot"),
    "boundary": ("106,61,154", "0.6", "dash dot"),
}
SYM_COLORS = {
    "water": ("31,120,180", "circle"), "settlement": ("0,0,0", "square"),
    "peak": ("140,81,10", "triangle"), "trig_point": ("227,26,28", "triangle"),
    "tree": ("51,160,44", "circle"), "enclosure": ("178,223,138", "circle"),
    "grave": ("106,61,154", "cross2"), "fort": ("227,26,28", "star"),
    "church": ("106,61,154", "cross"), "station": ("255,127,0", "square"),
    "ruin": ("128,128,128", "square"), "landmark": ("128,128,128", "circle"),
    "unknown": ("190,190,190", "circle"),
}


def line_qml():
    cats, syms = [], []
    for i, (kind, (rgb, w, style)) in enumerate(LINE_COLORS.items()):
        cats.append(f'<category render="true" value="{kind}" symbol="{i}" label="{kind}"/>')
        syms.append(f'''<symbol type="line" name="{i}"><layer class="SimpleLine">
<Option type="Map"><Option type="QString" name="line_color" value="{rgb},255"/>
<Option type="QString" name="line_style" value="{style}"/>
<Option type="QString" name="line_width" value="{w}"/></Option></layer></symbol>''')
    return f'''<!DOCTYPE qgis><qgis version="3.28"><renderer-v2 type="categorizedSymbol" attr="kind">
<categories>{''.join(cats)}</categories><symbols>{''.join(syms)}</symbols></renderer-v2></qgis>'''


def sym_qml():
    cats, syms = [], []
    for i, (cat, (rgb, shape)) in enumerate(SYM_COLORS.items()):
        cats.append(f'<category render="true" value="{cat}" symbol="{i}" label="{cat}"/>')
        syms.append(f'''<symbol type="marker" name="{i}"><layer class="SimpleMarker">
<Option type="Map"><Option type="QString" name="color" value="{rgb},255"/>
<Option type="QString" name="name" value="{shape}"/>
<Option type="QString" name="size" value="2.2"/>
<Option type="QString" name="outline_color" value="255,255,255,255"/>
<Option type="QString" name="outline_width" value="0.2"/></Option></layer></symbol>''')
    return f'''<!DOCTYPE qgis><qgis version="3.28"><renderer-v2 type="categorizedSymbol" attr="category">
<categories>{''.join(cats)}</categories><symbols>{''.join(syms)}</symbols></renderer-v2></qgis>'''


def label_qml():
    return '''<!DOCTYPE qgis><qgis version="3.28">
<renderer-v2 type="singleSymbol"><symbols><symbol type="marker" name="0">
<layer class="SimpleMarker"><Option type="Map">
<Option type="QString" name="color" value="90,90,90,180"/>
<Option type="QString" name="name" value="circle"/>
<Option type="QString" name="size" value="0.8"/>
<Option type="QString" name="outline_style" value="no"/></Option></layer>
</symbol></symbols></renderer-v2>
<labeling type="simple"><settings><text-style fieldName="text" fontSize="8">
<text-buffer bufferDraw="1" bufferSize="0.8" bufferColor="255,255,255,220"/>
</text-style><rendering scaleVisibility="1" maximumScale="1" minimumScale="500000"/></settings></labeling></qgis>'''


gpkg = sys.argv[1]
c = sqlite3.connect(gpkg)
c.execute("""CREATE TABLE IF NOT EXISTS layer_styles (
  id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT, f_table_schema TEXT,
  f_table_name TEXT, f_geometry_column TEXT, styleName TEXT, styleQML TEXT,
  styleSLD TEXT, useAsDefault BOOLEAN, description TEXT, owner TEXT,
  ui TEXT, update_time DATETIME DEFAULT CURRENT_TIMESTAMP)""")
c.execute("DELETE FROM layer_styles")
for tbl, qml, desc in (
        ("sudan250k_lines", line_qml(), "lines by kind"),
        ("sudan250k_symbols", sym_qml(), "symbols by category"),
        ("sudan250k_labels", label_qml(), "label text, buffered")):
    c.execute("INSERT INTO layer_styles (f_table_catalog,f_table_schema,"
              "f_table_name,f_geometry_column,styleName,styleQML,styleSLD,"
              "useAsDefault,description,owner) VALUES ('','',?,'geom',?,?,'',1,?,'')",
              (tbl, tbl + "_default", qml, desc))
c.commit()
n = c.execute("SELECT count(*) FROM layer_styles").fetchone()[0]
print(f"{n} styles embedded")
