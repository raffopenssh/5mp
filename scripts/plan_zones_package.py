#!/usr/bin/env python3
"""Package the zoning planner's outputs for QGIS and for a spreadsheet reader.

    python3 -W ignore scripts/plan_zones_package.py [--out reports/ZONING_PLAN_<date>]

Writes ONE GeoPackage with every layer the map draws plus every attribute the planner measured, and ONE Excel
workbook with the same rows (one sheet per layer + a README sheet that defines every column). Nothing here is
computed: every number is read from data/plan_zones/conservancy_units/ (the planner's outputs), so the map, the
GPKG, the workbook and AREAS.txt cannot disagree. QGIS styles are embedded in the GeoPackage's `layer_styles`
table (QGIS reads them as the default style), so opening the .gpkg in QGIS gives the map's colours.

Layers:
  proposals            polygons  every optimize_* proposal (core / community / community_towns), full attributes
  corridor_branches    polygons  the corridor network: one band per origin–destination bundle + the through-route
  corridor_axes        lines     the least-cost axis of each branch (start → end), same attributes
  teams                points    FP / ECHO / TANGO placements with size, water, season and the reason
  drawn_boundaries     polygons  what the authors sent (KML) + WDPA references, as the planner re-measured them
  mesh_features        lines     the legible mesh (rivers, swamp edges, ridges, roads, 1930s district lines …)
  beacons              points    point landmarks (1930s villages / water / hills, OSM villages) the boundary text cites
  support              polygons  bootstrap support per fine unit for each run (0–1)
  units                polygons  the 506 coarse planning units with class and rationale (the assessment layer)
  solver_zones         polygons  plan_solver.py zones.geojson — the ILP plan (core / wilderness / community / corridor), every zone
                                 measured by the same assessor, with the conservancy RANK (rank, rank_score, in_budget, shield …)
  deploy_zones         polygons  plan_deploy.py: the solver zones a team WORKS (served zones), joined to the team's id, kind,
                                 year, site, urgency, reach, brief — the filled shapes on the DEPLOYMENT map
  deploy_teams         points    plan_deploy.py team placements (T1/E1/F1 …), year 1 filled / year 2 hollow, full brief
  deploy_reach         polygons  the 25 km working disc per team (a reach geometry, not a finding; off by default on the map)
Rasters (built-up km²/cell, clearing km²/cell, solver class + intensity = the map's fill depth) are written beside it as GeoTIFFs when rasterio is available.
"""
import argparse, json, sqlite3, sys, time, datetime as dt
from pathlib import Path
import numpy as np
from shapely.geometry import shape, mapping, LineString
import fiona
from fiona.crs import CRS

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data/plan_zones/conservancy_units"

def log(*a): print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)

def flat(v):
    """GPKG/Excel cells hold scalars: dicts and lists become compact JSON text, None stays None."""
    if v is None or isinstance(v, (int, float, str)): return v
    if isinstance(v, bool): return int(v)
    return json.dumps(v, ensure_ascii=False)

def rows_from(gj, extra=None):
    out = []
    for f in json.load(open(gj))["features"]:
        p = {k: flat(v) for k, v in f["properties"].items()}
        if extra: p.update(extra)
        out.append((f["geometry"], p))
    return out

def areas_text():
    """The narrated assessment (AREAS.txt paragraph) per proposal / drawn boundary, keyed by name."""
    return {a["name"]: a for a in json.load(open(D / "areas.json"))}

# ---------------------------------------------------------------- column definitions (the README sheet + QGIS aliases)
COLS = {
 "uid": "planner unit id (fine-mesh union) — internal", "seed": "what this proposal grew from", "seed_kind": "seed type",
 "run": "optimize run (core / corridor / community / community_towns)", "rank": "rank within its run (1 = grown first)",
 "cls": "planner class as ONE unit: core | wilderness | community | corridor", "legal": "instrument the class maps to (Wildlife Act 2026)",
 "area_km2": "area km²", "area_ha": "area ha", "country": "country of the largest share", "in_zones": "drawn plan zones it lies in",
 "overlaps": "% overlap with each drawn / WDPA reference", "km_to_park": "km from the drawn Pongo–Wau–Numatinna NP (0 = touches)",
 "clusters": "GHSL settlement clusters (a cluster is a place, not a building)", "population_est": "people, GHSL lower bound — a satellite estimate, never a census",
 "pop_per_km2": "people per km²", "osm_places": "OSM named places inside", "new_since_2015": "clusters with no built-up in 2015 (founded since)",
 "camps": "cattle-camp-shaped clusters (small, seasonal)", "built_km2": "built-up surface km² today", "built_2000_km2": "built-up km² 2000", "built_2015_km2": "built-up km² 2015",
 "built_growth_x": "built-up growth factor 2000 → today", "towns": "largest places inside with people (‘*’ = named by nearest feature, unverified)",
 "fire_det_2024_25": "VIIRS detections 2024–25 inside", "fire_per_1000km2_yr": "detections per 1,000 km² per year", "fire_nov_feb_share": "share of detections in Nov–Feb (dry-season burning)",
 "fronts": "tracked fire fronts touching the area (this project's v5 tracker)", "fronts_per_1000km2": "fronts per 1,000 km²", "fronts_transhumance_pct": "% of fronts typed transhumance (herd burning)",
 "fronts_long": "fronts ≥150 km (a herd on the move, not a field)", "fronts_long_per_1000km2": "long fronts per 1,000 km²", "long_front_intensity": "long fronts per 10 km of width (the corridor test statistic)",
 "front_dirs": "three most common front headings with counts", "front_dirs_all": "all headings with counts", "front_axis": "dominant axis (e.g. SE–NW)", "front_axis_coherence": "axial mean resultant length: 1 = one axis, 0.25 = random",
 "clearing_events": "reviewed Hansen/GLAD clearing events since 2000", "clearing_km2": "reviewed clearing km² since 2000", "clearing_since_2020_km2": "reviewed clearing km² since 2020", "clearing_encroach_slash": "events classed encroachment / slash-and-burn",
 "mine_reported": "reported workings (OSM / Crisis Tracker)", "mine_candidates": "modelled candidates", "mine_top05_cells": "cells in the gold model's top 5% (targets, not mines)", "mine_watchlist": "abandoned-village-on-gold watchlist places", "mining_measured": "whether the mining model has measured skill here",
 "geology": "rock units by share, with commodity affinity", "geo_affinity_mean": "mean commodity affinity prior (unmeasured skill — a prior, not a score)", "cropland_2003_pct": "cropland % 2003", "cropland_2019_pct": "cropland % 2019",
 "boundary_legibility": "share of the outer boundary on a feature a person can point at (0–1)", "boundary": "the boundary described: which river / ridge / road / 1930s line on which side, and landmarks on the unnamed stretches",
 "perimeter_km": "perimeter km", "unattributed_pct": "% of the perimeter on no nameable feature", "rationale": "every threshold test the class rests on, with the measured value",
 "name": "display name", "support_mean": "bootstrap support: share of perturbed runs keeping the unit (≥0.5 = proposal)", "area_ha_p10_p50_p90": "size across bootstrap runs, ha", "contested_units": "fine units at 0.25–0.5 support — walk these with the community",
 "assessment": "the narrated assessment paragraph (AREAS.txt)", "legal_basis": "legal instrument", 
 "from_place": "corridor: nearest named place to where the bundle's fronts start", "to_place": "corridor: nearest named place to where they end", "bundle_fronts": "corridor: long fronts in this origin–destination bundle",
 "onset": "corridor: first month of movement (Oct–Nov = early dry season)", "months": "corridor: fronts by month", "straight_km": "corridor: straight-line start → end km", "start": "corridor: bundle start lon,lat", "end": "corridor: bundle end lon,lat",
 "km2": "corridor: band km² (support ≥ 0.5)", "km2_p10_p50_p90": "corridor: band km² across bootstrap draws", "draws": "bootstrap draws", "long_density_ratio": "corridor: long-front density inside ÷ outside the band",
 "all_fire_ratio": "corridor: ALL-front density inside ÷ outside (the null: is it herds or just burning?)", "excess_over_burning": "corridor: long ÷ all ratio — 1.0 = no more herds than the burning predicts, >1 = a herd route", "coherence_in": "corridor: axial coherence inside the band",
 "kind": "team kind: FP (focal point, 1 person) | ECHO (community team, 2) | TANGO (transhumance team, 2)", "team_size": "people", "place": "the place the team sits at", "zone": "the proposal it serves", "season": "when it works", "water": "water source and distance (a site without water is flagged UNVERIFIED)", "why": "the reason for this placement, from the zone's numbers",
 "ref_km2": "reference polygon km²", "iou": "intersection-over-union between the drawn shape and its redraw on the legible mesh", "n_units": "planning units the redraw uses", "redrawn_km2": "km² of the redraw", "ref_legible_pct": "% of the drawn boundary on a nameable feature", "ref_boundary": "the drawn boundary described", "class_mix": "planner classes inside the drawn shape by km²",
 "solver_class": "solver class: core | wilderness | community | corridor (plan_solver.py)", "rank_score": "rank: weighted mean of rank-percentiles of the measured terms (RANK.txt)", "in_budget": "rank: 1 = among the first --budget-n conservancies", "shield": "rank: share of perimeter touching core/corridor", "pressure_on_core": "rank: people × threat P10, edge-scaled", "herd_conflict": "rank: herd UD per 1,000 km²", "governance": "rank: committee-size term", "threat_p10_mean": "P(converted by 2035), mean", "people_x_threat": "people × threat P10", "herd_bundles": "herd bundles crossing the zone (share of band, fronts, onset, hold-out capture)",
 "w": "mesh feature weight (5 = river/border, 4 = swamp edge/district line, 3 = ridge/road)", "support": "bootstrap support 0–1", "team_id": "deploy team id (T = TANGO, E = ECHO, F = focal point; number = urgency rank within kind)", "sym": "symbol key: kind + deployment year", "staff": "people on the team", "urgency": "deploy urgency 0–1 (ECHO: gold-target rank, people × threat, shield; TANGO: herd UD, UD near residents, gold in band)", "footprint_km2": "area of the zones the team works, km²", "site_people": "GHSL people at the team's base settlement", "road_km": "km from the base to the nearest trunk…tertiary road", "reach": "OSRM reach of the base: share of the zone's targets within 4 h, car share, median minutes", "brief": "≤120-word brief for the team (muse-glimmer, from the served zones' stored descriptions)", "first_season": "what the team does in its first season", "short": "one-sentence brief", "adjudication": "why this base among the shortlist of 3", "alternatives": "the shortlist the base was chosen from", "in_walked_corridor_pct": "% of the unit inside the walked corridor band", "in_diverted_corridor_pct": "% inside the corridor with park + wilderness closed",
}
LEGAL = {"core": "national park or s.9 reserve (Wildlife Act 2026)", "wilderness": "wilderness / s.9 reserve", "community": "community conservancy, Wildlife Act 2026 s.14 (s.14(4) veto; Mining Act s.24 consent)", "corridor": "wildlife/livestock corridor — s.14 conservancy strip or gazetted corridor"}

# ---------------------------------------------------------------- QGIS styles (SLD-ish via QML), embedded in layer_styles
def qml_categorized(field, cats, geom="polygon", label_expr=None):
    """cats: list of (value, label, fill, stroke, width, dash)."""
    syms = []; catxml = []
    for i, (val, lab, fill, stroke, width, dash) in enumerate(cats):
        if geom == "polygon":
            layer = f'''<layer class="SimpleFill" enabled="1" locked="0"><Option type="Map">
<Option name="color" type="QString" value="{fill}"/><Option name="outline_color" type="QString" value="{stroke}"/>
<Option name="outline_width" type="QString" value="{width}"/><Option name="outline_width_unit" type="QString" value="MM"/>
<Option name="outline_style" type="QString" value="{dash if isinstance(dash, str) else ('dash' if dash else 'solid')}"/><Option name="style" type="QString" value="{'no' if fill.endswith(',0') else 'solid'}"/></Option></layer>'''
        elif geom == "line":
            layer = f'''<layer class="SimpleLine" enabled="1" locked="0"><Option type="Map">
<Option name="line_color" type="QString" value="{stroke}"/><Option name="line_width" type="QString" value="{width}"/><Option name="line_width_unit" type="QString" value="MM"/>
<Option name="line_style" type="QString" value="{'dash' if dash else 'solid'}"/></Option></layer>'''
        else:
            layer = f'''<layer class="SimpleMarker" enabled="1" locked="0"><Option type="Map">
<Option name="color" type="QString" value="{fill}"/><Option name="outline_color" type="QString" value="{stroke}"/><Option name="outline_width" type="QString" value="{width}"/>
<Option name="name" type="QString" value="{(dash or 'circle').split('@')[0]}"/><Option name="size" type="QString" value="{(dash or '@4').split('@')[1] if '@' in (dash or '') else '4'}"/><Option name="size_unit" type="QString" value="MM"/></Option></layer>'''
        syms.append(f'<symbol type="{ {"polygon": "fill", "line": "line", "point": "marker"}[geom] }" name="{i}" alpha="1" clip_to_extent="1">{layer}</symbol>')
        catxml.append(f'<category render="true" symbol="{i}" value="{val}" label="{lab}" type="string"/>')
    lab = ""
    if label_expr:
        from xml.sax.saxutils import escape
        label_expr = escape(label_expr, {'"': "&quot;"})
        lab = f'''<labeling type="simple"><settings calloutType="simple"><text-style fontSize="8" fontFamily="DejaVu Sans" textColor="40,40,40,255" isExpression="1" fieldName="{label_expr}">
<text-buffer bufferDraw="1" bufferSize="0.8" bufferColor="255,255,255,220"/></text-style><placement placement="0" dist="1"/><rendering scaleVisibility="0" obstacle="1"/></settings></labeling>'''
    return f'''<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'><qgis version="3.34" styleCategories="Symbology|Labeling|Fields">
<renderer-v2 type="categorizedSymbol" attr="{field}" forceraster="0" enableorderby="0"><categories>{''.join(catxml)}</categories><symbols>{''.join(syms)}</symbols></renderer-v2>{lab}
<layerGeometryType>{ {"polygon":2,"line":1,"point":0}[geom] }</layerGeometryType></qgis>'''

def qml_graduated_support():
    return '''<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'><qgis version="3.34" styleCategories="Symbology">
<renderer-v2 type="graduatedSymbol" attr="support" graduatedMethod="GraduatedColor"><ranges>
<range render="true" symbol="0" lower="0.0" upper="0.25" label="&lt; 0.25 (rejected)"/><range render="true" symbol="1" lower="0.25" upper="0.5" label="0.25–0.5 contested — walk with the community"/>
<range render="true" symbol="2" lower="0.5" upper="0.75" label="0.5–0.75 proposal"/><range render="true" symbol="3" lower="0.75" upper="1.01" label="&gt; 0.75 proposal, robust"/></ranges><symbols>
<symbol type="fill" name="0" alpha="0.15"><layer class="SimpleFill"><Option type="Map"><Option name="color" type="QString" value="200,200,200,255"/><Option name="outline_style" type="QString" value="no"/></Option></layer></symbol>
<symbol type="fill" name="1" alpha="0.45"><layer class="SimpleFill"><Option type="Map"><Option name="color" type="QString" value="253,208,162,255"/><Option name="outline_style" type="QString" value="no"/></Option></layer></symbol>
<symbol type="fill" name="2" alpha="0.55"><layer class="SimpleFill"><Option type="Map"><Option name="color" type="QString" value="161,217,155,255"/><Option name="outline_style" type="QString" value="no"/></Option></layer></symbol>
<symbol type="fill" name="3" alpha="0.65"><layer class="SimpleFill"><Option type="Map"><Option name="color" type="QString" value="49,163,84,255"/><Option name="outline_style" type="QString" value="no"/></Option></layer></symbol>
</symbols></renderer-v2><layerGeometryType>2</layerGeometryType></qgis>'''

def rgba(hexcol, a=255):
    h = hexcol.lstrip("#"); return f"{int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{a}"

STYLES = {
 "proposals": qml_categorized("cls", [("core", "Core — park-grade (empty, herd-burnt, no clearing)", rgba("#1b5e20", 40), rgba("#1b5e20"), "0.7", False),
                                      ("wilderness", "Wilderness", rgba("#7fae8b", 40), rgba("#4f7a5c"), "0.5", False),
                                      ("community", "Community conservancy proposal (s.14)", rgba("#6a2c8f", 35), rgba("#6a2c8f"), "0.5", False),
                                      ("corridor", "Corridor", rgba("#b3261e", 35), rgba("#b3261e"), "0.5", False)],
                              label_expr="\"name\" || '\\n' || format_number(\"area_ha\",0) || ' ha · ' || format_number(\"population_est\",0) || ' ppl'"),
 "corridor_branches": qml_categorized("run", [("corridor", "Herd corridor branch (support ≥ 0.5 band)", rgba("#b3261e", 45), rgba("#b3261e"), "0.5", False)],
                              label_expr="regexp_replace(\"from_place\",' \\\\(.*','') || ' → ' || regexp_replace(\"to_place\",' \\\\(.*','') || ' · ' || \"bundle_fronts\" || ' herds · ' || \"onset\""),
 "corridor_axes": qml_categorized("run", [("corridor", "Corridor least-cost axis", "", rgba("#b3261e"), "0.6", True)], geom="line"),
 "teams": qml_categorized("kind", [("FP", "Focal point (1 person)", rgba("#0b7285"), rgba("#ffffff"), "0.3", "star"),
                                   ("ECHO", "ECHO team (2) — community conservancy", rgba("#0b7285"), rgba("#ffffff"), "0.3", "square"),
                                   ("TANGO", "TANGO team (2) — herd corridor", rgba("#0b7285"), rgba("#ffffff"), "0.3", "triangle")], geom="point",
                          label_expr="\"kind\" || ' · ' || regexp_replace(replace(\"place\",'near ',''),' \\\\(.*','')"),
 "drawn_boundaries": qml_categorized("source", [("PLAN", "Boundary as drawn by the authors (KML)", "0,0,0,0", rgba("#8c8c86"), "0.5", True),
                                                ("WDPA", "WDPA protected area", rgba("#2f7f6f", 25), rgba("#2f7f6f"), "0.4", False)], label_expr="\"name\""),
 "mesh_features": qml_categorized("kind", [("river", "River (HydroRIVERS, 1930s name where unnamed)", "", rgba("#4a7fb5"), "0.35", False), ("swamp", "Swamp / lake edge", "", rgba("#2a8f9a"), "0.35", False),
                                           ("ridge", "Ridge chain of 1930s hill marks", "", rgba("#8a6b3a"), "0.35", False), ("hist_water", "1930s watercourse (traced — verify)", "", rgba("#9ab8d6"), "0.2", True),
                                           ("hist_boundary", "1930s district line", "", rgba("#5a5a5a"), "0.3", True), ("road", "Road", "", rgba("#8c8c86"), "0.3", False), ("border", "International border", "", rgba("#333333"), "0.5", True),
                                           ("geology", "Geological contact (not visible on the ground)", "", rgba("#c9b8a8"), "0.2", True), ("beacon", "Landmark (point beacon)", "", rgba("#8a6b3a"), "0.2", False), ("frame", "Study-area frame", "", rgba("#999999"), "0.2", True)], geom="line"),
 "beacons": qml_categorized("kind", [("beacon", "Landmark (1930s village / water / hill, OSM village)", rgba("#8a6b3a"), rgba("#ffffff"), "0.2", "circle")], geom="point"),
 "support": qml_graduated_support(),
 "units": qml_categorized("cls", [("core", "core", rgba("#1b5e20", 60), rgba("#ffffff"), "0.15", False), ("wilderness", "wilderness", rgba("#7fae8b", 60), rgba("#ffffff"), "0.15", False),
                                  ("community", "community", rgba("#6a2c8f", 60), rgba("#ffffff"), "0.15", False), ("corridor", "corridor", rgba("#b3261e", 60), rgba("#ffffff"), "0.15", False)]),
 "solver_zones": qml_categorized("solver_class", [("core", "Zoning: core", "0,0,0,0", rgba("#1b5e20", 140), "0.3", "dash dot"), ("wilderness", "Zoning: wilderness", "0,0,0,0", rgba("#4f7a5c", 140), "0.3", "dash dot"),
                                  ("community", "Zoning: community conservancy", "0,0,0,0", rgba("#6a2c8f", 140), "0.3", "dash dot"), ("corridor", "Zoning: herd corridor", "0,0,0,0", rgba("#b3261e", 140), "0.3", "dash dot")],
                                 label_expr="CASE WHEN \"rank\" IS NOT NULL THEN 'C' || \"rank\" || ' · ' ELSE '' END || format_number(\"area_ha\",0) || ' ha · ' || format_number(\"population_est\",0) || ' ppl'"), "deploy_zones": qml_categorized("solver_class", [("community", "Community zone — ECHO team works here (fill: deploy_fill raster)", "0,0,0,0", rgba("#6a2c8f"), "0.55", False),
                                                 ("corridor", "Corridor zone — TANGO team works here (fill: deploy_fill raster)", "0,0,0,0", rgba("#b3261e"), "0.55", False)],
                                label_expr="\"team_id\" || ' · ' || format_number(\"area_ha\",0) || ' ha · ' || format_number(\"population_est\",0) || ' ppl'"),
 "deploy_teams": qml_categorized("sym", [("FP1", "Focal point (1), year 1", rgba("#0b7285"), rgba("#ffffff"), "0.3", "star@5"), ("FP2", "Focal point (1), year 2", rgba("#ffffff"), rgba("#0b7285"), "0.45", "star@5"),
                                        ("ECHO1", "ECHO team (5), year 1", rgba("#0b7285"), rgba("#ffffff"), "0.3", "square@3.2"), ("ECHO2", "ECHO team (5), year 2", rgba("#ffffff"), rgba("#0b7285"), "0.45", "square@3.2"),
                                        ("TANGO1", "TANGO team (5), year 1", rgba("#0b7285"), rgba("#ffffff"), "0.3", "triangle@3.8"), ("TANGO2", "TANGO team (5), year 2", rgba("#ffffff"), rgba("#0b7285"), "0.45", "triangle@3.8")], geom="point",
                                label_expr="\"id\" || ' · ' || regexp_replace(replace(\"place\",'near ',''),' \\\\(.*','')"),
 "deploy_reach": qml_categorized("kind", [("ECHO", "ECHO 25 km working disc", "0,0,0,0", rgba("#0b7285", 90), "0.25", "dot"), ("TANGO", "TANGO 25 km working disc", "0,0,0,0", rgba("#0b7285", 90), "0.25", "dot"), ("FP", "FP 25 km disc", "0,0,0,0", rgba("#0b7285", 60), "0.2", "dot")]),
}

def write_layer(gpkg, name, geom_type, rows, ordered_cols):
    schema_props = {}
    for c in ordered_cols:
        vals = [r[1].get(c) for r in rows if r[1].get(c) is not None]
        t = "str"
        if vals and all(isinstance(v, bool) or isinstance(v, int) for v in vals): t = "int"
        elif vals and all(isinstance(v, (int, float)) for v in vals): t = "float"
        schema_props[c] = t
    with fiona.open(gpkg, "w", driver="GPKG", layer=name, crs=CRS.from_epsg(4326), schema={"geometry": geom_type, "properties": schema_props}) as dst:
        for g, p in rows:
            gg = shape(g)
            if geom_type == "MultiPolygon" and gg.geom_type == "Polygon": g = mapping(gg.buffer(0)) if False else {"type": "MultiPolygon", "coordinates": [g["coordinates"]]}
            if geom_type == "MultiLineString" and gg.geom_type == "LineString": g = {"type": "MultiLineString", "coordinates": [g["coordinates"]]}
            dst.write({"geometry": g, "properties": {c: (str(p.get(c)) if schema_props[c] == "str" and p.get(c) is not None else p.get(c)) for c in ordered_cols}})
    log(f"  {name}: {len(rows)} features, {len(ordered_cols)} columns")

def embed_styles(gpkg, aliases):
    con = sqlite3.connect(gpkg)
    con.execute("""CREATE TABLE IF NOT EXISTS layer_styles (id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT(256), f_table_schema TEXT(256), f_table_name TEXT(256), f_geometry_column TEXT(256),
        styleName TEXT(30), styleQML TEXT, styleSLD TEXT, useAsDefault BOOLEAN, description TEXT, owner TEXT(30), ui TEXT(30), update_time DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    con.execute("DELETE FROM layer_styles")
    for lyr, qml in STYLES.items():
        # field aliases = the column definitions, so QGIS's attribute form explains every column
        al = "".join(f'<alias field="{c}" index="{i}" name="{COLS.get(c, c).replace("&","&amp;").replace(chr(34),"&quot;").replace("<","&lt;")}"/>' for i, c in enumerate(aliases.get(lyr, [])))
        q = qml.replace("<layerGeometryType>", f"<aliases>{al}</aliases><layerGeometryType>")
        con.execute("INSERT INTO layer_styles (f_table_catalog,f_table_schema,f_table_name,f_geometry_column,styleName,styleQML,styleSLD,useAsDefault,description,owner) VALUES ('','',?,'geom',?,?,'',1,?,'planner')",
                    (lyr, lyr + "_default", q, "Zoning planner default style — the colours of the report map"))
    con.commit(); con.close()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=str(ROOT / f"reports/ZONING_PLAN_{dt.date.today():%Y-%m}")); a = ap.parse_args()
    out = Path(a.out); gpkg = out.with_suffix(".gpkg"); xlsx = out.with_suffix(".xlsx")
    if gpkg.exists(): gpkg.unlink()
    A = areas_text()
    aliases = {}
    layers = {}

    # proposals (core / community / community_towns) + corridor branches
    prop, corr, axes = [], [], []
    for run in ("core", "community", "community_towns", "corridor"):
        gj = D / f"optimize_{run}.geojson"
        if not gj.exists(): continue
        for i, (g, p) in enumerate(rows_from(gj)):
            nm = p.get("name") or f"{run} proposal {i+1}"
            key = next((k for k in A if k.endswith(nm) or k == "PROPOSAL " + nm), None)
            p.update(run=run, rank=i + 1, name=nm, legal_basis=LEGAL.get(p.get("cls"), ""), assessment=(A[key]["text"] if key else None))
            if run == "corridor":
                corr.append((g, p))
                if p.get("start") and p.get("end"):
                    s_, e_ = json.loads(p["start"]) if isinstance(p["start"], str) else p["start"], json.loads(p["end"]) if isinstance(p["end"], str) else p["end"]
                    axes.append(({"type": "LineString", "coordinates": [list(s_), list(e_)]}, dict(p)))
            else: prop.append((g, p))
    front = ["name", "run", "rank", "cls", "legal_basis", "area_ha", "area_km2", "country", "population_est", "clusters", "pop_per_km2", "support_mean", "boundary_legibility", "boundary", "rationale", "assessment"]
    def cols(rows, first):
        seen = list(first); [seen.append(k) for r in rows for k in r[1] if k not in seen]; return seen
    layers["proposals"] = ("MultiPolygon", prop, cols(prop, front))
    cfront = ["name", "run", "rank", "cls", "legal_basis", "from_place", "to_place", "bundle_fronts", "onset", "months", "straight_km", "km2", "km2_p10_p50_p90", "long_density_ratio", "all_fire_ratio", "excess_over_burning", "coherence_in", "population_est", "boundary", "rationale", "assessment"]
    layers["corridor_branches"] = ("MultiPolygon", corr, cols(corr, cfront))
    layers["corridor_axes"] = ("LineString", axes, cols(axes, cfront))

    # teams
    teams = rows_from(D / "teams.geojson")
    layers["teams"] = ("Point", teams, cols(teams, ["kind", "team_size", "place", "zone", "season", "water", "why"]))

    # drawn boundaries + WDPA, from areas.json (geometry from the planner's references) and validation.json
    import plan_conservancy_units as P
    refs = P.references(); V = {v["name"]: v for v in json.load(open(D / "validation.json")) if "name" in v}   # last row is the {'assessed': …} summary
    drawn = []
    for k, g in refs.items():
        if g.geom_type == "Point": continue
        src = "PLAN" if k.startswith("PLAN") or "PLAN " + k in A else "WDPA"
        ak = next((n for n in A if n.endswith(k)), None); vk = next((n for n in V if n.endswith(k)), None)
        p = dict(name=k, source=src, assessment=A[ak]["text"] if ak else None)
        if ak: p.update({kk: flat(vv) for kk, vv in A[ak]["measured"].items()})
        if vk: p.update({kk: flat(V[vk][kk]) for kk in ("ref_km2", "iou", "n_units", "redrawn_km2", "ref_legible_pct", "ref_boundary", "class_mix")})
        drawn.append((mapping(g), p))
    layers["drawn_boundaries"] = ("MultiPolygon", drawn, cols(drawn, ["name", "source", "area_ha", "cls", "population_est", "iou", "ref_legible_pct", "class_mix", "boundary", "rationale", "assessment"]))

    mesh_all = rows_from(D / "mesh_features.geojson")
    mesh = [r for r in mesh_all if "Line" in r[0]["type"]]; layers["mesh_features"] = ("MultiLineString", mesh, cols(mesh, ["kind", "name", "w"]))
    beac = [({"type": "Point", "coordinates": c}, dict(r[1], kind="beacon")) for r in mesh_all if "Point" in r[0]["type"] for c in (r[0]["coordinates"] if r[0]["type"] == "MultiPoint" else [r[0]["coordinates"]])]
    if beac: layers["beacons"] = ("Point", beac, cols(beac, ["kind", "name", "w"]))
    sup = []
    for run in ("core", "community", "community_towns", "corridor"):
        gj = D / f"optimize_{run}_support.geojson"
        if gj.exists(): sup += rows_from(gj, extra={"run": run})
    layers["support"] = ("MultiPolygon", sup, cols(sup, ["run", "support"]))
    units = rows_from(D / "units.geojson"); layers["units"] = ("MultiPolygon", units, cols(units, ["uid", "cls", "area_ha", "population_est", "boundary_legibility", "boundary", "rationale"]))
    sz = D.parent / "solver" / "zones.geojson"                         # the ILP plan (plan_solver.py solve + rank), if solved
    if sz.exists():
        szr = rows_from(sz)
        for g, p_ in szr: p_["legal_basis"] = LEGAL.get(p_.get("solver_class"), "")
        layers["solver_zones"] = ("MultiPolygon", szr, cols(szr, ["uid", "solver_class", "legal_basis", "rank", "rank_score", "in_budget", "area_ha", "population_est", "pop_per_km2", "threat_p10_mean", "shield", "pressure_on_core", "herd_conflict", "governance", "boundary_legibility", "boundary", "rationale", "herd_bundles"]))

    dep_t = D.parent / "solver" / "deploy_teams.geojson"; dep_f = D.parent / "solver" / "deploy_footprint.geojson"
    if dep_t.exists() and sz.exists():
        import ast
        tm = rows_from(dep_t)
        for g, p_ in tm:
            p_["year"] = int(p_.get("year") or 2); p_["team_id"] = p_.get("id"); p_["sym"] = f"{p_['kind']}{p_['year']}"
            for k in ("reach", "alternatives", "towns", "zone_uids"):
                if not isinstance(p_.get(k), str): p_[k] = json.dumps(p_.get(k))
        layers["deploy_teams"] = ("Point", tm, cols(tm, ["id", "kind", "year", "staff", "place", "site_people", "urgency", "footprint_km2", "zone_uids", "road_km", "reach", "water", "season", "short", "brief", "first_season", "why", "adjudication", "alternatives", "sym"]))
        byuid = {}
        for g, p_ in tm:
            for u in ast.literal_eval(p_["zone_uids"]) if isinstance(p_["zone_uids"], str) else p_["zone_uids"]: byuid.setdefault(int(u), p_)
        dz = []
        for g, p_ in szr:
            t = byuid.get(int(p_["uid"]))
            if t is None: continue
            q = dict(p_); q.update(team_id=t["id"], team_kind=t["kind"], year=t["year"], team_place=t["place"], urgency=t["urgency"], staff=t["staff"], short=t.get("short"), brief=t.get("brief"), first_season=t.get("first_season"))
            dz.append((g, q))
        layers["deploy_zones"] = ("MultiPolygon", dz, cols(dz, ["uid", "solver_class", "team_id", "team_kind", "year", "team_place", "staff", "urgency", "area_ha", "population_est", "pop_per_km2", "threat_p10_mean", "short", "brief", "first_season", "rank", "in_budget", "boundary", "rationale", "herd_bundles"]))
        if dep_f.exists():
            # the footprint file = served zones ∪ 25 km disc; the disc alone is the working reach (the zones are deploy_zones)
            served = {u: shape(g) for g, p_ in szr for u in [int(p_["uid"])] if u in byuid}
            fr = []
            for g, p_ in rows_from(dep_f):
                from shapely.ops import unary_union
                zs = [served[int(u)] for u in ast.literal_eval(p_["zone_uids"]) if int(u) in served] if isinstance(p_.get("zone_uids"), str) else []
                disc = shape(g).difference(unary_union(zs).buffer(0)) if zs else shape(g)
                if disc.is_empty: continue
                fr.append((mapping(disc), dict(kind=p_["kind"], id=p_["id"], year=int(p_.get("year") or 2), place=p_.get("place"))))
            layers["deploy_reach"] = ("MultiPolygon", fr, ["id", "kind", "year", "place"])

    log(f"writing {gpkg}")
    for name, (gt, rows, cc) in layers.items():
        write_layer(str(gpkg), name, gt, rows, cc); aliases[name] = cc
    embed_styles(str(gpkg), aliases)

    # rasters
    try:
        import rasterio, pickle
        from rasterio.transform import from_origin
        import __main__; __main__.Grid = P.Grid
        st = pickle.load(open(D / "state.pkl", "rb")); G = st["G"]; C = P.cells(sqlite3.connect(str(ROOT / "db.sqlite3")), G)
        SD = D.parent / "solver"
        if (SD / "solve_lab.npy").exists():
            C["solve_class"] = np.load(SD / "solve_lab.npy").astype(np.float32); C["solve_intensity"] = np.load(SD / "solve_intensity.npy").astype(np.float32)
        if "solve_class" in C and "deploy_zones" in layers:
            # the DEPLOYMENT fill exactly as build_map.py draws it: class colour, opacity = lo + hi·intensity, only in the
            # served zones and only where the solver's own class agrees — one RGBA raster, so QGIS shows the same picture
            from shapely.ops import transform as _tf
            import matplotlib.colors as mc
            ZF = {"community": ("#8e5bb0", 0.12, 0.40), "corridor": ("#d4574d", 0.10, 0.34), "core": ("#2e7d32", 0.12, 0.40), "wilderness": ("#7fae8b", 0.12, 0.40)}
            zid = G.rasterize([(_tf(P.FWD, shape(g)), int(q["uid"])) for g, q in layers["deploy_zones"][1]], fill=0)
            rgba_ = np.zeros(C["solve_class"].shape + (4,), np.uint8)
            for g, q in layers["deploy_zones"][1]:
                cls = q["solver_class"]; col, lo, hi = ZF[cls]
                m = (zid == int(q["uid"])) & (C["solve_class"] == ("core", "wilderness", "community", "corridor").index(cls) + 1)
                rgba_[m, :3] = (np.array(mc.to_rgb(col)) * 255).astype(np.uint8); rgba_[m, 3] = (255 * (lo + hi * np.clip(C["solve_intensity"][m], 0, 1))).astype(np.uint8)
            tp = out.parent / f"{out.name}_deploy_fill.tif"
            with rasterio.open(tp, "w", driver="GTiff", height=G.h, width=G.w, count=4, dtype="uint8", crs=P.CEA + " +datum=WGS84 +units=m +no_defs", transform=from_origin(G.x0, G.y1, G.res, G.res), compress="deflate", photometric="RGB") as dst:
                for b in range(4): dst.write(rgba_[:, :, b], b + 1)
                dst.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green, rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]
                dst.update_tags(description="deployment fill: team-zone class colour, alpha = evidence for the class (the sheet's fill)")
            log(f"  raster {tp.name}: deployment fill (RGBA)")
        for key, desc in (("solve_class", "solver class per cell: 1 core, 2 wilderness, 3 community, 4 corridor, 0 none"), ("solve_intensity", "solver evidence for the class per cell 0-1 (the map's fill depth inside team zones)"),
                          ("built", "built-up km2 per cell (GHSL)"), ("clear", "reviewed clearing km2 per cell since 2000"), ("clear20", "reviewed clearing km2 per cell since 2020"), ("pop", "people per cell (GHSL lower bound)"), ("longdens", "long transhumance fronts per cell")):
            arr = C.get(key) if key in C else getattr(G, key, None)
            if arr is None: continue
            tp = out.parent / f"{out.name}_{key}.tif"
            with rasterio.open(tp, "w", driver="GTiff", height=G.h, width=G.w, count=1, dtype="float32", crs=P.CEA + " +datum=WGS84 +units=m +no_defs", transform=from_origin(G.x0, G.y1, G.res, G.res), nodata=-1, compress="deflate") as dst:
                dst.write(np.nan_to_num(np.asarray(arr, np.float32), nan=-1), 1); dst.update_tags(description=desc)
            log(f"  raster {tp.name}: {desc}")
    except Exception as e: log(f"rasters skipped: {e}")

    # workbook
    import openpyxl
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "README"
    ws.append(["Zoning plan package — " + dt.date.today().isoformat()]); ws["A1"].font = Font(bold=True, size=14)
    for line in __doc__.strip().split("\n")[2:]: ws.append([line])
    ws.append([]); ws.append(["Sheet", "Features", "What it holds"]); ws[ws.max_row][0].font = ws[ws.max_row][1].font = ws[ws.max_row][2].font = Font(bold=True)
    for name, (gt, rows, cc) in layers.items():
        if name in ("support", "mesh_features"): continue
        ws.append([name, len(rows), gt])
    ws.append([]); ws.append(["Column", "Meaning"]); ws[ws.max_row][0].font = ws[ws.max_row][1].font = Font(bold=True)
    used = sorted({c for (gt, rows, cc) in layers.values() for c in cc}, key=lambda c: list(COLS).index(c) if c in COLS else 999)
    for c in used: ws.append([c, COLS.get(c, "")])
    ws.column_dimensions["A"].width = 28; ws.column_dimensions["B"].width = 120
    for name, (gt, rows, cc) in layers.items():
        if name in ("support", "mesh_features"): continue           # thousands of rows a reader would never scroll; in the GPKG
        w = wb.create_sheet(name); w.append(cc); [setattr(c, "font", Font(bold=True)) for c in w[1]]
        w.append([COLS.get(c, "") for c in cc]); [setattr(c, "font", Font(italic=True, color="666666")) for c in w[2]]
        for g, p in rows:
            c_ = shape(g).representative_point() if g else None
            w.append([(p.get(c) if not isinstance(p.get(c), str) else p.get(c)[:32000]) for c in cc])
        w.freeze_panes = "B3"
        for i, c in enumerate(cc, 1):
            w.column_dimensions[get_column_letter(i)].width = 60 if c in ("assessment", "boundary", "rationale", "why", "ref_boundary") else 16
        for row in w.iter_rows(min_row=3):
            for cell in row: cell.alignment = Alignment(wrap_text=isinstance(cell.value, str) and len(cell.value) > 40, vertical="top")
    wb.save(xlsx); log(f"wrote {xlsx}")
    print(gpkg); print(xlsx)

if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "scripts")); main()
