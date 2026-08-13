#!/usr/bin/env python3
"""Render the shipped geology GeoPackage headlessly, THROUGH ITS OWN EMBEDDED
QGIS PROJECT, and write PNGs.

ONE FILE, EVERY SHEET (since 2026-08-12). The export used to be one GeoPackage
per scan; it is now data/geomaps/geology.gpkg with the sheet as a COLUMN, in one
`geology_units` layer, because the map is one layer and rock does not stop at a
border. A `sheet` argument here therefore selects a VIEW of that file (its zoom
windows and its swatch rows), not a file.

WHY IT LOADS THE EMBEDDED PROJECT AND NOT A STYLE OF ITS OWN
------------------------------------------------------------
srv/geomap_gpkg.go writes FGDC-style pattern fills (LinePatternFill /
PointPatternFill sub-symbols) into the file's `layer_styles` table and into an
embedded QGIS project. Until 2026-08-12 the only thing testing any of that was
a byte-level Go test: it asserted the XML we *wrote*, which cannot notice that
QGIS silently ignored an option, dropped a duplicate sub-symbol name, or
rendered nine ornament families as nine identical solid fills. A renderer that
applied its own QML would have the same blind spot one level up. This one opens
what a user opens.

    sudo apt-get install -y python3-qgis            # 3.34, big download
    QT_QPA_PLATFORM=offscreen python3 scripts/geomaps/render_gpkg.py

Writes /tmp/geomap_render/<sheet>_full.png and <sheet>_zoom*.png. The zooms
matter more than the full sheet: at 1:1.5M a 2 mm hatch spacing is sub-pixel,
so a full-country view cannot tell a cross-hatch from a solid fill. Judge the
ornament at the zoomed scale.

EVERY LAYER, NOT ONLY THE UNITS (since 2026-08-13)
--------------------------------------------------
The file gained two more layers — `geology_contacts` (graded amber ramp) and
`mining_anchors` (the evidence the model was scored against) — and they arrived
with the same blind spot the units had: a Go test asserting the XML we wrote.
So this script now renders the project's WHOLE layer list, in the project's own
order (`layerTreeRoot().layerOrder()`, top-first, which is exactly what
QgsMapSettings.setLayers wants), because contacts and anchors drawn UNDER the
unit fills is the same failure as a style QGIS ignored: correct in the file,
invisible on screen.

Four passes, each answering something the others cannot:

* `<sheet>_full`, `<sheet>_zoom_*` — in situ, all three layers stacked.
* `<sheet>_swatches` — the nine lithology ornaments, side by side (above).
* `<sheet>_contact_grades` — the FOUR CONTACT GRADES side by side, at map
  scale and magnified. The map views physically cannot answer this: the widths
  differ by 0.16-0.4 mm, which at 1:1.5M is a fraction of a pixel, the same
  argument this docstring already makes about hatch spacing. What it answers:
  are classic/likely/weak/ungraded distinguishable from EACH OTHER, does the
  amber ramp run the same direction the map's does (classic strongest), and is
  `ungraded` — which carries NULL, not 0, and means "the model says nothing" —
  visibly OFF the ramp rather than drawn as its weakest step, where it would
  read as a measurement.
* `<sheet>_anchor_symbols` — one marker per source, drawn OVER a real unit fill
  taken off the units renderer. The anchors are the evidence layer; a point
  symbol that vanishes into a busy polygon fill is useless, and no legend
  swatch on white can show that.

Plus `detail_*`: a few tens of km across, where anchors are densest, which is
the only scale at which individual contact lines and individual points are
separable at all.

Everything here still comes off the renderer QGIS built from the file's own
layer_styles/embedded project. Nothing in this script writes a symbol.
"""
import os, sys, glob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (
    QgsApplication, QgsProject, QgsMapSettings, QgsMapRendererParallelJob,
    QgsRectangle, QgsCoordinateReferenceSystem,
)
from qgis.PyQt.QtCore import QSize, QEventLoop
from qgis.PyQt.QtGui import QColor

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = "/tmp/geomap_render"
GPKG = os.path.join(REPO, "data", "geomaps", "geology.gpkg")

# Zoom windows, in EPSG:4326 degrees, chosen to sit inside the mapped body of
# each sheet and to catch as many different ornament families as one frame can.
ZOOMS = {
    "car":   [("zoom_basement", (20.0, 4.5, 23.5, 7.0)),
              ("zoom_gres",     (15.0, 3.5, 18.5, 6.0))],
    "sudan": [("zoom_redsea",   (33.5, 17.0, 37.5, 21.0)),
              ("zoom_basement", (23.0, 9.0, 27.0, 13.0))],
}

# Tens of km across, over the two densest anchor clusters (CAR: 262 points in
# half a degree around 22E 7N; Sudan: 115 around 35.5E 18.5N). At the full-sheet
# scale 3,687 points are a smear and 882 lines are a mesh; here one point is one
# point and one contact is one line, which is the only way to see whether the
# marker survives the fill under it.
DETAIL = [
    ("detail_car_bria",   (21.75, 6.4, 22.45, 6.9)),
    ("detail_sudan_nile", (35.2, 18.25, 35.9, 18.75)),
]


def project_layers(project):
    """The project's own draw order, top-first - which is what setLayers wants.

    `project.mapLayers()` is a DICT keyed by layer id: iterating it gives an
    arbitrary order, and an arbitrary order over units/contacts/anchors is a
    coin flip on whether the hairlines and the evidence points end up UNDER an
    opaque-enough polygon fill. The layer tree is where the order the writer
    chose actually lives.
    """
    order = list(project.layerTreeRoot().layerOrder())
    if not order:
        order = list(project.mapLayers().values())
    return order


def sheet_extent(table, sheet=None):
    """The extent of one sheet, read off the file rather than from the layer.

    `layer.extent()` on a layer that (a) came out of the embedded project and
    (b) has a subset string returns a NULL rectangle here, with GDAL printing
    "unable to open database file" on stderr. That is not a harmless warning:
    the null extent goes straight into QgsMapSettings, and the render job then
    NEVER FINISHES — the whole script hangs before its first PNG, having
    printed a plausible layer summary first. Cost 15 minutes of wall clock once.
    The R-tree already holds the answer, so ask it.
    """
    import sqlite3
    con = sqlite3.connect(GPKG)
    q = (f"SELECT MIN(r.minx), MIN(r.miny), MAX(r.maxx), MAX(r.maxy) "
         f"FROM rtree_{table}_geom r")
    args = ()
    if sheet:
        q += f" JOIN {table} t ON t.fid = r.id WHERE t.sheet = ?"
        args = (sheet,)
    box = con.execute(q, args).fetchone()
    con.close()
    if box is None or box[0] is None:
        raise SystemExit(f"!! no extent for {table} sheet={sheet}")
    return box


def render(project, name, extent, size=(1600, 1200)):
    ms = QgsMapSettings()
    ms.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    ms.setLayers(project_layers(project))
    ms.setBackgroundColor(QColor(255, 255, 255))
    ms.setOutputSize(QSize(*size))
    ms.setExtent(QgsRectangle(*extent))
    # The pattern spacings are in MM, so the DPI is not cosmetic: it decides
    # how many pixels a 1.6 mm hatch interval gets. 96 is what a screen gives.
    ms.setOutputDpi(96)
    job = QgsMapRendererParallelJob(ms)
    loop = QEventLoop()
    job.finished.connect(loop.quit)
    job.start()
    loop.exec_()
    img = job.renderedImage()
    path = os.path.join(OUT, name + ".png")
    img.save(path)
    print("  wrote", path, img.width(), "x", img.height())
    return path


def layer_by_table(project, table):
    """Find a layer by its GeoPackage table name, not by position.

    `layers[0]` used to mean "the units" because there was one layer. There are
    three now and the dict order is arbitrary, so index 0 is a lottery between
    the polygons, the hairlines and the points.
    """
    for l in project.mapLayers().values():
        # NOT endswith(): once a subset string is set, the source becomes
        # "...|layername=geology_units|subset=sheet = 'car'", and the layer the
        # caller is asking for stops being found halfway through the run.
        if "layername=" + table in l.source() or l.name() == table:
            return l
    return None


def swatches(project, sheet):
    """Nine ornaments side by side, at a size a human can judge.

    Still the EMBEDDED style: every symbol here is pulled off the renderer QGIS
    built from the file's own layer_styles row, not written by this script. The
    map views answer "does it look right in situ"; this answers the question
    they physically cannot at 1:1.5M, where a 1.6 mm hatch is sub-pixel: are
    the nine families distinguishable FROM EACH OTHER, and is each cross-hatch
    complete rather than half of itself.
    """
    from qgis.core import QgsRenderContext, QgsSymbol
    from qgis.PyQt.QtGui import QImage, QPainter, QFont
    from qgis.PyQt.QtCore import Qt
    layer = layer_by_table(project, "geology_units")
    r = layer.renderer()
    # One representative category per lithology, read back off the file.
    import sqlite3, os
    con = sqlite3.connect(GPKG)
    # `key` = (sheet, code), which is what the renderer categorises on: a code
    # is unique only within its own sheet ("S" is Silurian sandstone on Sudan
    # and a gold-bearing schist belt on CAR), so matching on code alone would
    # pick the wrong sheet's symbol for half the swatches.
    rows = con.execute(
        "SELECT lithology, key, age_label FROM geology_units WHERE sheet=? GROUP BY lithology",
        (sheet,)).fetchall()
    con.close()
    cell, pad = 240, 34
    cols = 5
    rowsn = (len(rows) + cols - 1) // cols
    img = QImage(cols * (cell + pad) + pad, rowsn * (cell + pad + 26) + pad,
                 QImage.Format_ARGB32)
    img.fill(Qt.white)
    pnt = QPainter(img)
    pnt.setRenderHint(QPainter.Antialiasing, True)
    for i, (lith, key, agelbl) in enumerate(sorted(rows)):
        sym = None
        for c in r.categories():
            if c.value() == key:
                sym = c.symbol().clone()
        if sym is None:
            continue
        x = pad + (i % cols) * (cell + pad)
        y = pad + (i // cols) * (cell + pad + 26)
        pnt.save()
        pnt.translate(x, y)
        ctx = QgsRenderContext.fromQPainter(pnt)
        ctx.setScaleFactor(96 / 25.4)   # px per mm at 96 dpi, as the map view
        sym.startRender(ctx)
        from qgis.PyQt.QtCore import QPointF
        from qgis.PyQt.QtGui import QPolygonF
        poly = QPolygonF([QPointF(0, 0), QPointF(cell, 0),
                          QPointF(cell, cell), QPointF(0, cell)])
        sym.renderPolygon(poly, None, None, ctx)
        sym.stopRender(ctx)
        pnt.restore()
        pnt.setPen(Qt.black)
        f = QFont(); f.setPointSize(9); pnt.setFont(f)
        pnt.drawText(x, y + cell + 18, f"{lith}  ({key})")
    pnt.end()
    path = os.path.join(OUT, f"{sheet}_swatches.png")
    img.save(path)
    print("  wrote", path, img.width(), "x", img.height())


def contact_grades(project, sheet=None):
    """The four contact grades side by side, at map scale and magnified.

    The map views cannot answer this and never could: the four widths are
    0.9/0.66/0.5/0.4 mm, and at 1:1.5M the largest difference between two of
    them is a fraction of a pixel. Same argument as the hatch spacing above.
    Two strips per grade, both off the SAME symbol clone: the left one at 96 dpi
    (a map pixel is a map pixel) and the right one at 4x, where a human can see
    which of two ambers is which and how much thicker `classic` really is.

    Read it for three things: (1) four distinguishable strips, not four
    identical ones; (2) the amber ramp running the same direction as the map's
    (classic strongest / most saturated); (3) `ungraded` visibly OFF the amber
    ramp - it carries NULL, not weight 0, and a grey line says "the model said
    nothing" where a pale-amber one would say "we measured this and it is the
    weakest".
    """
    from qgis.core import QgsRenderContext
    from qgis.PyQt.QtGui import QImage, QPainter, QFont, QPolygonF
    from qgis.PyQt.QtCore import Qt, QPointF
    import sqlite3

    layer = layer_by_table(project, "geology_contacts")
    if layer is None:
        print("  !! no geology_contacts layer in the project")
        return
    r = layer.renderer()
    if not hasattr(r, "categories"):
        print("  !! contacts renderer is", type(r).__name__, "- not categorized")
        return

    con = sqlite3.connect(GPKG)
    q = "SELECT grade, COUNT(*), COUNT(weight), MIN(weight), MAX(weight) FROM geology_contacts"
    args = ()
    if sheet:
        q += " WHERE sheet=?"
        args = (sheet,)
    counts = {g: (n, nw, lo, hi) for g, n, nw, lo, hi in
              con.execute(q + " GROUP BY grade", args).fetchall()}
    con.close()

    cats = [c for c in r.categories() if c.value() != ""]
    W, rowh, pad = 1180, 128, 24
    img = QImage(W, pad + rowh * len(cats) + pad, QImage.Format_ARGB32)
    img.fill(Qt.white)
    pnt = QPainter(img)
    pnt.setRenderHint(QPainter.Antialiasing, True)
    for i, c in enumerate(cats):
        y = pad + i * rowh
        for mag, x0, x1 in ((1.0, 250, 640), (4.0, 700, 1150)):
            sym = c.symbol().clone()
            pnt.save()
            ctx = QgsRenderContext.fromQPainter(pnt)
            ctx.setScaleFactor(96 / 25.4 * mag)   # px per mm, as the map view
            sym.startRender(ctx)
            yy = y + 40
            sym.renderPolyline(QPolygonF([QPointF(x0, yy), QPointF(x1, yy)]),
                               None, ctx)
            sym.stopRender(ctx)
            pnt.restore()
            pnt.setPen(Qt.gray)
            f = QFont(); f.setPointSize(7); pnt.setFont(f)
            pnt.drawText(int(x0), yy + 22,
                         "96 dpi (map scale)" if mag == 1 else "x4")
        n, nw, lo, hi = counts.get(c.value(), (0, 0, None, None))
        wtxt = "weight NULL" if not nw else (
            f"weight {lo}" if lo == hi else f"weight {lo}-{hi}")
        pnt.setPen(Qt.black)
        f = QFont(); f.setPointSize(10); pnt.setFont(f)
        pnt.drawText(pad, y + 34, f"{c.value()}")
        f2 = QFont(); f2.setPointSize(8); pnt.setFont(f2)
        pnt.drawText(pad, y + 54, c.label())
        pnt.drawText(pad, y + 72, f"{n} lines, {wtxt}")
    pnt.end()
    path = os.path.join(OUT, (f"{sheet}_" if sheet else "") + "contact_grades.png")
    img.save(path)
    print("  wrote", path, img.width(), "x", img.height())


def anchor_symbols(project):
    """One marker per source, drawn OVER a real unit fill.

    The anchors are the evidence layer, so the question is not "is the symbol
    pretty on white" but "does it survive a busy polygon fill". A legend swatch
    on a white card cannot fail that test; this one can. The backdrop tiles are
    unit symbols cloned off the units renderer - still nothing written here - so
    the contrast shown is the contrast a reader gets.
    """
    from qgis.core import QgsRenderContext
    from qgis.PyQt.QtGui import QImage, QPainter, QFont, QPolygonF
    from qgis.PyQt.QtCore import Qt, QPointF
    import sqlite3

    alayer = layer_by_table(project, "mining_anchors")
    ulayer = layer_by_table(project, "geology_units")
    if alayer is None:
        print("  !! no mining_anchors layer in the project")
        return
    r = alayer.renderer()
    if not hasattr(r, "categories"):
        print("  !! anchors renderer is", type(r).__name__, "- not categorized")
        return
    con = sqlite3.connect(GPKG)
    counts = dict(con.execute(
        "SELECT source, COUNT(*) FROM mining_anchors GROUP BY source").fetchall())
    con.close()

    # Three backdrops: white, and two real unit fills off the units renderer
    # (the first and the middle category, so a light and a dark one).
    ur = ulayer.renderer() if ulayer is not None else None
    ucats = [c for c in ur.categories() if c.value() != ""] if ur else []
    backs = [None] + ([ucats[0], ucats[len(ucats) // 2]] if ucats else [])
    blabels = ["white"] + [c.label() for c in backs[1:]]

    cats = [c for c in r.categories() if c.value() != ""]
    cell, pad, labelh = 150, 18, 30
    W = pad + len(backs) * (cell + pad)
    H = pad + len(cats) * (cell + labelh)
    img = QImage(max(W, 700), H + pad, QImage.Format_ARGB32)
    img.fill(Qt.white)
    pnt = QPainter(img)
    pnt.setRenderHint(QPainter.Antialiasing, True)
    for i, c in enumerate(cats):
        y = pad + i * (cell + labelh)
        for j, back in enumerate(backs):
            x = pad + j * (cell + pad)
            pnt.save()
            pnt.translate(x, y)
            ctx = QgsRenderContext.fromQPainter(pnt)
            ctx.setScaleFactor(96 / 25.4)
            if back is not None:
                bs = back.symbol().clone()
                bs.startRender(ctx)
                bs.renderPolygon(QPolygonF([QPointF(0, 0), QPointF(cell, 0),
                                            QPointF(cell, cell), QPointF(0, cell)]),
                                 None, None, ctx)
                bs.stopRender(ctx)
            sym = c.symbol().clone()
            sym.startRender(ctx)
            # A row of points, not one: a single dot cannot show whether two
            # markers at map size are separable at all.
            for k in range(3):
                sym.renderPoint(QPointF(30 + k * 45, cell / 2), None, ctx)
            sym.stopRender(ctx)
            pnt.restore()
            pnt.setPen(Qt.darkGray)
            pnt.drawRect(x, y, cell, cell)
        pnt.setPen(Qt.black)
        f = QFont(); f.setPointSize(9); pnt.setFont(f)
        pnt.drawText(pad, y + cell + 20,
                     f"{c.value()}  ({counts.get(c.value(), 0)} points)   "
                     + "  |  ".join(blabels))
    pnt.end()
    path = os.path.join(OUT, "anchor_symbols.png")
    img.save(path)
    print("  wrote", path, img.width(), "x", img.height())


def main():
    os.makedirs(OUT, exist_ok=True)
    QgsApplication.setPrefixPath("/usr", True)
    app = QgsApplication([], False)
    app.initQgis()

    sheets = sys.argv[1:] or ["car", "sudan"]
    if not os.path.exists(GPKG):
        print(f"!! {GPKG} missing - request /api/geomap/geopackage first")
        app.exitQgis()
        return
    # The project name is REQUIRED and the path must be ABSOLUTE: without
    # either, read() returns False and then hangs. Already paid for once,
    # documented in docs/GEOLOGY.md.
    import sqlite3
    con = sqlite3.connect(GPKG)
    pname = con.execute("SELECT name FROM qgis_projects").fetchone()[0]
    con.close()
    uri = f"geopackage:{GPKG}?projectName={pname}"
    proj = QgsProject.instance()
    proj.clear()
    if not proj.read(uri):
        print("!! project read failed", uri)
        app.exitQgis()
        return
    layers = project_layers(proj)
    print(f"== {pname}")
    print("   draw order (top first):", " > ".join(l.name() for l in layers))
    for l in layers:
        print(f"   layer {l.name()} valid={l.isValid()} features={l.featureCount()}")
        r = l.renderer()
        print(f"   renderer {type(r).__name__} categories={len(r.categories()) if hasattr(r,'categories') else '-'}")
        md = l.metadata()
        print(f"   abstract: {(md.abstract() or '(none)')[:400]}")
    print("   project title:", proj.title())
    layer = layer_by_table(proj, "geology_units")
    contacts = layer_by_table(proj, "geology_contacts")
    for sheet in sheets:
        # One file, so a per-sheet view is a SUBSET STRING, not another
        # datasource. Reset it afterwards or the next sheet renders through the
        # previous filter and the full extent comes out one country short.
        # The contacts carry the same `sheet` column and must be filtered with
        # it: a CAR view with Sudan's 563 hairlines across it is not the sheet.
        layer.setSubsetString(f"sheet = '{sheet}'")
        if contacts is not None:
            contacts.setSubsetString(f"sheet = '{sheet}'")
        render(proj, f"{sheet}_full", sheet_extent("geology_units", sheet))
        for name, box in ZOOMS.get(sheet, []):
            render(proj, f"{sheet}_{name}", box)
        swatches(proj, sheet)
        contact_grades(proj, sheet)
    layer.setSubsetString("")
    if contacts is not None:
        contacts.setSubsetString("")
    # And the whole thing, which is the picture the file is FOR: two sheets in
    # one legend, and the seam between them is the thing to look at.
    render(proj, "combined_full", sheet_extent("geology_units"))
    # The two passes that need no sheet: they are about the SYMBOLS, and the
    # anchors have no sheet column at all (rule 2 in geomap_gpkg_layers.go -
    # they are never filtered to the reader's view).
    contact_grades(proj)
    anchor_symbols(proj)
    for name, box in DETAIL:
        render(proj, name, box)
    app.exitQgis()


if __name__ == "__main__":
    main()
