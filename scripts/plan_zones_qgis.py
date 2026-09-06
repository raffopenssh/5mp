#!/usr/bin/env python3
"""Finish the zoning/deployment GeoPackage with PyQGIS: one self-contained file that opens as the DEPLOYMENT map.

    QT_QPA_PLATFORM=offscreen python3 -W ignore scripts/plan_zones_qgis.py [--gpkg reports/ZONING_PLAN_<date>.gpkg]

plan_zones_package.py writes the planner/solver/deploy layers and their styles; this step
  1. copies the "on the ground" and gold layers (settlements, fire fronts, rivers, gold cells/targets/reported, Southern NP)
     from the EASY PIP GeoPackage, styles included, so the file needs no sibling;
  2. builds a QGIS project — grouped, ordered top→bottom exactly as build_map.py draws, every layer present, the ones the
     sheet does not draw switched OFF — and stores it INSIDE the GeoPackage (QGIS: Project ▸ Open from ▸ GeoPackage)
     and beside it as .qgs;
  3. renders the project to a PNG so the result is checked by the same engine a reader will use.

Groups (top of the draw order first):
  Deployment          deploy_teams (on) · deploy_zones outline (on) · deploy_fill RGBA raster (on, the sheet's evidence fill) · deploy_reach (off)
  Gold                31 candidates · 33 reported · 32 watchlist · 30 top-5 % cells   (all on)
  Zoning found        solver_zones outlines (on) · units · proposals · corridor_branches · corridor_axes · support · teams(greedy)  (off)
  Drawn by the authors drawn_boundaries (on) · 04_existing_pa (on)
  On the ground       20_settlements (on) · 40_rivers (on) · clearing + built-up rasters (on) · 21_fire_fronts (on) · solve_class raster (off)
  Reference mesh      mesh_features · beacons  (off)
"""
import argparse, os, sqlite3, sys
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]

FROM_EASY = ["04_existing_pa", "20_settlements", "21_fire_fronts", "30_gold_top5_cells", "31_gold_candidates", "32_gold_watchlist", "33_gold_reported", "40_rivers"]

# (group, layer, title, visible, opacity) top → bottom
TREE = [
 ("Deployment", "deploy_teams", "Teams — T TANGO · E ECHO · F focal point (hollow = year 2)", True, 1.0),
 ("Deployment", "deploy_zones", "Zones the teams work (outline; the fill is the layer below)", True, 1.0),
 ("Deployment", "raster:deploy_fill", "Team-zone fill: deeper = stronger evidence for the class", True, 1.0),
 ("Deployment", "deploy_reach", "25 km working disc per team (reach, not a finding)", False, 1.0),
 ("Gold", "31_gold_candidates", "Imagery target — a place to look, never a mine", True, 1.0),
 ("Gold", "33_gold_reported", "Reported working (OSM / Crisis Tracker)", True, 1.0),
 ("Gold", "32_gold_watchlist", "1930s village on gold, empty today", True, 1.0),
 ("Gold", "30_gold_top5_cells", "Gold model top 5 % (target, not mine)", True, 0.7),
 ("Zoning found", "solver_zones", "Zoning: core / wilderness / community / corridor (solved)", True, 1.0),
 ("Zoning found", "units", "Planning units with class + rationale", False, 1.0),
 ("Zoning found", "proposals", "Greedy proposals (superseded by the solver)", False, 1.0),
 ("Zoning found", "corridor_branches", "Corridor branches (greedy)", False, 1.0),
 ("Zoning found", "corridor_axes", "Corridor least-cost axes", False, 1.0),
 ("Zoning found", "support", "Bootstrap support", False, 1.0),
 ("Zoning found", "teams", "Team placements (greedy planner — superseded by Deployment)", False, 1.0),
 ("Drawn by the authors", "drawn_boundaries", "Boundaries as drawn by the authors (KML) + WDPA", True, 0.6),
 ("Drawn by the authors", "04_existing_pa", "Southern National Park — gazetted", True, 0.6),
 ("On the ground", "20_settlements", "Towns ≥ 500 people, size ∝ people", True, 1.0),
 ("On the ground", "40_rivers", "Trunk rivers", True, 1.0),
 ("On the ground", "raster:clear", "Clearing km² per 2 km cell (Hansen/GLAD, verified)", True, 0.85),
 ("On the ground", "raster:built", "Built-up km² per 2 km cell (GHSL)", True, 0.75),
 ("On the ground", "21_fire_fronts", "Fire fronts 2024–26, one hairline each", True, 1.0),
 ("On the ground", "raster:solve_class", "Solver class per cell (1 core … 4 corridor)", False, 0.5),
 ("On the ground", "raster:solve_intensity", "Solver evidence per cell 0–1", False, 0.7),
 ("Reference mesh", "mesh_features", "Legible mesh: rivers, swamp edges, ridges, roads, 1930s lines", False, 1.0),
 ("Reference mesh", "beacons", "Landmarks (1930s villages / water / hills)", False, 1.0),
]


def copy_layers(dst, src):
    from osgeo import ogr
    ogr.UseExceptions()
    d = ogr.Open(str(dst), 1); s_ = ogr.Open(str(src))
    have = {d.GetLayer(i).GetName() for i in range(d.GetLayerCount())}
    for n in FROM_EASY:
        if n in have or s_.GetLayerByName(n) is None: continue
        d.CopyLayer(s_.GetLayerByName(n), n, ["OVERWRITE=YES"]); print("  copied", n)
    d = s_ = None
    cd, cs = sqlite3.connect(str(dst)), sqlite3.connect(str(src))
    cols = [r[1] for r in cd.execute("pragma table_info(layer_styles)")][1:]
    for n in FROM_EASY:
        cd.execute("DELETE FROM layer_styles WHERE f_table_name=?", (n,))
        for row in cs.execute(f"SELECT {','.join(cols)} FROM layer_styles WHERE f_table_name=?", (n,)):
            cd.execute(f"INSERT INTO layer_styles ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})", row)
    cd.commit(); cd.close(); cs.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpkg", default=str(sorted(ROOT.glob("reports/ZONING_PLAN_*.gpkg"))[-1]))
    ap.add_argument("--easy", default=str(sorted(ROOT.glob("reports/EASY_PIP_MAP_*.gpkg"))[-1]))
    ap.add_argument("--png", default="")
    a = ap.parse_args(); gpkg = Path(a.gpkg); stem = gpkg.with_suffix("")
    if Path(a.easy).exists(): copy_layers(gpkg, Path(a.easy))

    from qgis.core import (QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer, QgsLayerTreeGroup, QgsCoordinateReferenceSystem,
                           QgsMapSettings, QgsMapRendererParallelJob, QgsSingleBandPseudoColorRenderer, QgsColorRampShader, QgsRasterShader,
                           QgsRasterBandStats, QgsRectangle, QgsRasterMinMaxOrigin, QgsProjectViewSettings, QgsReferencedRectangle)
    from qgis.PyQt.QtGui import QColor
    from qgis.PyQt.QtCore import QSize, Qt
    QgsApplication.setPrefixPath("/usr", True); app = QgsApplication([], False); app.initQgis()
    prj = QgsProject.instance(); prj.clear(); prj.setTitle("Pongo–Wau–Numatinna — deployment map"); prj.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    prj.setBackgroundColor(QColor("#fdfdfa"))
    root = prj.layerTreeRoot(); groups = {}
    extent = None

    def ramp(layer, color, lo, hi):
        sh = QgsColorRampShader(lo, hi, None, QgsColorRampShader.Interpolated)
        c = QColor(color); c0 = QColor(color); c0.setAlpha(0)
        sh.setColorRampItemList([QgsColorRampShader.ColorRampItem(lo, c0, f"{lo:g}"), QgsColorRampShader.ColorRampItem(lo + (hi - lo) * 0.25, QColor(c.red(), c.green(), c.blue(), 120), ""), QgsColorRampShader.ColorRampItem(hi, c, f"{hi:g}")])
        rs = QgsRasterShader(); rs.setRasterShaderFunction(sh)
        r = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, rs); r.setClassificationMin(lo); r.setClassificationMax(hi); layer.setRenderer(r)

    for grp, name, title, vis, op in TREE:
        if grp not in groups:
            groups[grp] = root.addGroup(grp); groups[grp].setExpanded(grp == "Deployment")
        if name.startswith("raster:"):
            key = name.split(":")[1]; tp = stem.parent / f"{stem.name}_{key}.tif"
            if not tp.exists(): print("  missing", tp.name); continue
            lyr = QgsRasterLayer(str(tp), title)
            if not lyr.isValid(): print("  invalid", tp.name); continue
            st = lyr.dataProvider().bandStatistics(1, QgsRasterBandStats.All)
            if key == "deploy_fill":
                from qgis.core import QgsMultiBandColorRenderer
                r = QgsMultiBandColorRenderer(lyr.dataProvider(), 1, 2, 3); r.setAlphaBand(4); lyr.setRenderer(r)   # RGBA as written: the sheet's own pixels
            elif key == "solve_class":
                sh = QgsColorRampShader(0, 4, None, QgsColorRampShader.Exact)
                sh.setColorRampItemList([QgsColorRampShader.ColorRampItem(v, QColor(c), l) for v, c, l in ((1, "#2e7d32", "core"), (2, "#7fae8b", "wilderness"), (3, "#8e5bb0", "community"), (4, "#d4574d", "corridor"))])
                rs = QgsRasterShader(); rs.setRasterShaderFunction(sh); lyr.setRenderer(QgsSingleBandPseudoColorRenderer(lyr.dataProvider(), 1, rs))
            else:
                hi = {"solve_intensity": 1.0}.get(key) or max(st.maximumValue * 0.35, 1e-6)      # ~p98 of a heavy-tailed density: the sheet's PowerNorm cut
                ramp(lyr, {"clear": "#b0186b", "built": "#e08a1e", "solve_intensity": "#1b5e20"}[key], 0.0, hi)
            if key != "deploy_fill": lyr.renderer().setNodataColor(QColor(0, 0, 0, 0))
        else:
            lyr = QgsVectorLayer(f"{gpkg}|layername={name}", title, "ogr")
            if not lyr.isValid(): print("  missing layer", name); continue
            lyr.loadDefaultStyle()
            if name == "20_settlements":
                # the sheet: dots are towns ≥ 500 people, ~1/3 the EASY export's marker sizes at this frame
                lyr.setSubsetString('"population_est" >= 500')
                r = lyr.renderer()
                for i, rng in enumerate(r.ranges()):
                    sym = rng.symbol().clone(); sym.setSize(sym.size() * 0.38); r.updateRangeSymbol(i, sym)
            if name in ("solver_zones", "drawn_boundaries", "units", "proposals", "corridor_branches", "teams", "support"): lyr.setLabelsEnabled(name in ("deploy_zones",))
            if extent is None and name == "solver_zones": extent = lyr.extent()
        lyr.setOpacity(op)
        prj.addMapLayer(lyr, False); node = groups[grp].addLayer(lyr); node.setItemVisibilityChecked(vis); node.setExpanded(False)
    if extent is not None:
        extent.grow(0.4)
        vs = prj.viewSettings(); vs.setDefaultViewExtent(QgsReferencedRectangle(extent, prj.crs()))

    qgs = stem.with_suffix(".qgs"); prj.write(str(qgs)); print("wrote", qgs)
    ok = prj.write(f"geopackage:{gpkg}?projectName=deployment_map"); print("project in gpkg:", ok)

    png = Path(a.png) if a.png else stem.parent / f"{stem.name}_qgis_check.png"
    ms = QgsMapSettings(); ms.setDestinationCrs(prj.crs()); ms.setBackgroundColor(QColor("#fdfdfa"))
    vis_layers = [n.layer() for n in root.findLayers() if n.isVisible()]
    ms.setLayers(vis_layers); ms.setExtent(extent or ms.fullExtent()); ms.setOutputSize(QSize(2400, int(2400 * extent.height() / extent.width())))
    ms.setFlag(QgsMapSettings.DrawLabeling, True); ms.setFlag(QgsMapSettings.Antialiasing, True)
    job = QgsMapRendererParallelJob(ms); job.start(); job.waitForFinished(); job.renderedImage().save(str(png)); print("wrote", png)
    app.exitQgis()


if __name__ == "__main__":
    main()
