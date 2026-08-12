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


def render(project, name, extent, size=(1600, 1200)):
    ms = QgsMapSettings()
    ms.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    ms.setLayers(list(project.mapLayers().values()))
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
    layer = list(project.mapLayers().values())[0]
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
    layers = list(proj.mapLayers().values())
    print(f"== {pname}")
    for l in layers:
        print(f"   layer {l.name()} valid={l.isValid()} features={l.featureCount()}")
        r = l.renderer()
        print(f"   renderer {type(r).__name__} categories={len(r.categories()) if hasattr(r,'categories') else '-'}")
    layer = layers[0]
    for sheet in sheets:
        # One file, so a per-sheet view is a SUBSET STRING, not another
        # datasource. Reset it afterwards or the next sheet renders through the
        # previous filter and the full extent comes out one country short.
        layer.setSubsetString(f"sheet = '{sheet}'")
        ext = layer.extent()
        render(proj, f"{sheet}_full",
               (ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()))
        for name, box in ZOOMS.get(sheet, []):
            render(proj, f"{sheet}_{name}", box)
        swatches(proj, sheet)
    layer.setSubsetString("")
    # And the whole thing, which is the picture the file is FOR: two sheets in
    # one legend, and the seam between them is the thing to look at.
    ext = layer.extent()
    render(proj, "combined_full",
           (ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()))
    app.exitQgis()


if __name__ == "__main__":
    main()
