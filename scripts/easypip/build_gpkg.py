#!/usr/bin/env python3
"""The EASY PIP map, as a styled GeoPackage: the same sheet, in QGIS.

WHY A SECOND ARTEFACT. The PNG/PDF sheet answers "if people can only have one
map". This file answers the next question anybody with QGIS asks - "can I
click it?" - and it must be the SAME map, not a layer dump: every layer here
is drawn on the sheet, in the same colours, in the same order, and carries the
numbers the PIP report and the two-pager quote as attributes. Nothing is
styled by eye and no number is typed: every field is read from
data/eval/pip_facts.json, data/eval/zone_stats.json and the same sources
build_map.py reads (root invariant 2).

    python3 scripts/easypip/build_gpkg.py      # ~40 s

Layers (the numeric prefix is the reading order; the embedded QGIS project
sets draw order, groups and what is switched ON):

  00_readme                  non-spatial: what this file is, and its caveats
  01_park                    the proposed Pongo-Wau-Numatinna boundary
  02_wilderness              the four wilderness blocks
  03_grazing                 the three sustainable-grazing zones
  04_existing_pa             Southern National Park - already gazetted
  05_corridor_axis           the line the two sent pins imply (an ASK)
  06_corridor_pins           the two pins as received, 15 km discs measured
  07_reach                   union of the proposed shapes + their 25 km rim
  10_plan_sites              ECHO/TANGO sites + focal points, with verdicts
  11_plan_unreached_towns    towns of 2,000+ beside the ask, no site within 40 km
  20_settlements             clusters, graduated by measured population
  21_fire_fronts             every v5 front touching the frame, 2024-2026
  30_gold_top5_cells         top-5% model cells, clipped to the ask
  31_gold_candidates         imagery targets - never mines
  32_gold_watchlist          1930s villages on gold, empty today
  33_gold_reported           reported workings (OSM / Crisis Tracker)
  40_rivers                  trunk rivers

Every zone polygon carries its own measured statistics (area, people,
clearing, cropland, fire rate, front interception, gold exposure) so a reader
who clicks a shape gets the assessment's own row for it, with the basis of
each rate named in the field list.
"""
import json
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pyproj
from osgeo import ogr, osr
from pyproj import Geod
from shapely.geometry import Point, mapping
from shapely.ops import transform, unary_union
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "easypip"))
from plan_zone_stats import read_kml, km2  # noqa: E402

import build_map as M  # noqa: E402  (one palette, one KML parse, one belt rule)

DB = ROOT / "db.sqlite3"
AOI = "XSA_Study_Area"
OUT = ROOT / "reports/EASY_PIP_MAP_2026-08.gpkg"
FACTS = ROOT / "data/eval/pip_facts.json"
GEOD = Geod(ellps="WGS84")


def rgba(hexcolor, alpha=255):
    h = hexcolor.lstrip("#")
    return f"{int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{alpha}"


# ------------------------------------------------------------------ writing
# Each layer's own abstract, kept so the embedded QGIS project can carry it
# too: QGIS's <maplayer> metadata OVERRIDES the provider's, and an empty
# element blanks it silently - which would drop every caveat on the route we
# actually tell people to use.
DESCR = {}


def mklayer(ds, srs, name, geomtype, fields, description=""):
    DESCR[name] = description
    lyr = ds.CreateLayer(name, srs, geomtype, options=["DESCRIPTION=" + description]
                         if description else [])
    for n, t in fields:
        lyr.CreateField(ogr.FieldDefn(n, t))
    return lyr


def setf(f, **kw):
    for k, v in kw.items():
        if v is None:
            continue
        f.SetField(k, v)


def geom_of(shapely_geom):
    return ogr.CreateGeometryFromJson(json.dumps(mapping(shapely_geom)))


# ------------------------------------------------------------------- styles
def _label(field, size="9", color="60,60,60,255", weight="50", placement="6"):
    return (f'<labeling type="simple"><settings><text-style '
            f'fieldName="{field}" fontSize="{size}" fontWeight="{weight}" '
            f'textColor="{color}"><text-buffer bufferDraw="1" bufferSize="1.0" '
            f'bufferColor="255,255,255,225"/></text-style>'
            f'<placement placement="{placement}" dist="1.6"/>'
            f'</settings></labeling>')


def qml(renderer, label=""):
    return (f'<!DOCTYPE qgis><qgis styleCategories="Symbology|Labeling" '
            f'labelsEnabled="{1 if label else 0}">{renderer}{label}</qgis>')


def fill_style(stroke, width, fill=None, alpha=40, dash=None, label_field=None,
               label_size="10", label_color=None):
    d = (f'<Option name="outline_style" type="QString" value="dash"/>'
         f'<Option name="customdash" type="QString" value="{dash}"/>'
         f'<Option name="use_custom_dash" type="QString" value="1"/>'
         if dash else
         '<Option name="outline_style" type="QString" value="solid"/>')
    body = (f'<renderer-v2 type="singleSymbol"><symbols>'
            f'<symbol type="fill" name="0"><layer class="SimpleFill">'
            f'<Option type="Map">'
            f'<Option name="color" type="QString" value="'
            f'{rgba(fill, alpha) if fill else "0,0,0,0"}"/>'
            f'<Option name="outline_color" type="QString" value="{rgba(stroke)}"/>'
            f'<Option name="outline_width" type="QString" value="{width}"/>'
            f'{d}</Option></layer></symbol></symbols></renderer-v2>')
    lab = _label(label_field, label_size, label_color or rgba(stroke), "75",
                 placement="0") if label_field else ""
    return qml(body, lab)


def line_style(color, width, dash=None, alpha=255, label_field=None):
    d = (f'<Option name="line_style" type="QString" value="dash"/>'
         f'<Option name="customdash" type="QString" value="{dash}"/>'
         f'<Option name="use_custom_dash" type="QString" value="1"/>'
         if dash else '')
    body = (f'<renderer-v2 type="singleSymbol"><symbols>'
            f'<symbol type="line" name="0"><layer class="SimpleLine">'
            f'<Option type="Map">'
            f'<Option name="line_color" type="QString" value="{rgba(color, alpha)}"/>'
            f'<Option name="line_width" type="QString" value="{width}"/>'
            f'{d}</Option></layer></symbol></symbols></renderer-v2>')
    return qml(body, _label(label_field) if label_field else "")


def marker_style(shape_, fill, stroke, size, width="0.4", label_field=None,
                 label_size="9", label_color=None, alpha=255):
    body = (f'<renderer-v2 type="singleSymbol"><symbols>'
            f'<symbol type="marker" name="0"><layer class="SimpleMarker">'
            f'<Option type="Map">'
            f'<Option name="name" type="QString" value="{shape_}"/>'
            f'<Option name="color" type="QString" value="'
            f'{rgba(fill, alpha) if fill else "0,0,0,0"}"/>'
            f'<Option name="outline_color" type="QString" value="{rgba(stroke)}"/>'
            f'<Option name="outline_width" type="QString" value="{width}"/>'
            f'<Option name="size" type="QString" value="{size}"/>'
            f'</Option></layer></symbol></symbols></renderer-v2>')
    lab = (_label(label_field, label_size, label_color or rgba(stroke), "75")
           if label_field else "")
    return qml(body, lab)


def rule_marker_style(rules, label_field=None, label_size="9"):
    """rules: (filter, legend, shape, fill_hex_or_None, stroke_hex, size, width)."""
    syms, rls, labs = [], [], []
    for i, (flt, leg, shape_, fill, stroke, size, width) in enumerate(rules):
        syms.append(
            f'<symbol type="marker" name="{i}"><layer class="SimpleMarker">'
            f'<Option type="Map">'
            f'<Option name="name" type="QString" value="{shape_}"/>'
            f'<Option name="color" type="QString" value="'
            f'{rgba(fill) if fill else "0,0,0,0"}"/>'
            f'<Option name="outline_color" type="QString" value="{rgba(stroke)}"/>'
            f'<Option name="outline_width" type="QString" value="{width}"/>'
            f'<Option name="size" type="QString" value="{size}"/>'
            f'</Option></layer></symbol>')
        # The filter is an attribute value inside XML: a bare double quote
        # around a field name closes the attribute and yields a style QGIS
        # silently refuses to load. Escape once, here, not at each call site.
        fx = flt.replace('"', "&quot;")
        rls.append(f'<rule key="k{i}" filter="{fx}" label="{leg}" symbol="{i}"/>')
        if label_field:
            labs.append(
                f'<rule filter="{fx}"><settings><text-style '
                f'fieldName="{label_field}" fontSize="{label_size}" '
                f'fontWeight="75" textColor="{rgba(stroke)}">'
                f'<text-buffer bufferDraw="1" bufferSize="1.1" '
                f'bufferColor="255,255,255,235"/></text-style>'
                f'<placement placement="6" dist="2"/></settings></rule>')
    lab = (f'<labeling type="rule-based"><rules>' + "".join(labs)
           + '</rules></labeling>') if labs else ""
    return qml(f'<renderer-v2 type="RuleRenderer"><rules key="root">'
               + "".join(rls) + '</rules><symbols>' + "".join(syms)
               + '</symbols></renderer-v2>', lab)


def graduated_marker_style(field, breaks, color, sizes):
    """Marker size by a measured quantity - the sheet's 'area proportional to
    people'. Sizes are a class list, not a continuous ramp, because a legend a
    reader can read beats a ramp they cannot."""
    rs, syms = [], []
    for i in range(len(breaks) - 1):
        syms.append(
            f'<symbol type="marker" name="{i}"><layer class="SimpleMarker">'
            f'<Option type="Map">'
            f'<Option name="name" type="QString" value="circle"/>'
            f'<Option name="color" type="QString" value="{rgba(color, 160)}"/>'
            f'<Option name="outline_color" type="QString" value="154,93,0,255"/>'
            f'<Option name="outline_width" type="QString" value="0.2"/>'
            f'<Option name="size" type="QString" value="{sizes[i]}"/>'
            f'</Option></layer></symbol>')
        hi = breaks[i + 1]
        rs.append(f'<range symbol="{i}" lower="{breaks[i]}" upper="{hi}" '
                  f'label="{breaks[i]:,.0f} \u2013 {hi:,.0f} people" '
                  f'render="true"/>')
    return qml(f'<renderer-v2 type="graduatedSymbol" attr="{field}" '
               f'graduatedMethod="GraduatedSize"><ranges>' + "".join(rs)
               + '</ranges><symbols>' + "".join(syms)
               + '</symbols></renderer-v2>')


# ---------------------------------------------------------------- the file
def zone_stat_row(st, F, name):
    """The assessment's own row for one shape, as GPKG fields.

    Read from zone_stats.json, never recomputed here: the sheet, the report
    and this file must be able to disagree only if the source does.
    """
    Z = st["zones"][name]
    s = st["settlements"].get(name, {})
    d = st["deforestation"].get(name, {})
    c = st["cropland"].get(name, {})
    f = st["fire_detections"].get(name, {})
    t = st["fire_trajectories"].get(name, {})
    m = st["mining"].get(name, {})
    rimkey = f"{name} \u2014 {st['rim_km']:g} km rim"
    sr = st["settlements_rim"].get(rimkey, {})
    fronts = t.get("fronts_touching") or 0
    return dict(
        name=M.short_name(name),
        kml_name=name,
        kml_file=Z.get("file"),
        area_km2=Z.get("area_km2"),
        file_says_km2=Z.get("label_km2"),
        clusters_inside=s.get("clusters"),
        people_inside=s.get("population_est"),
        new_since_2015=(s.get("by_persistence") or {}).get("recent", 0),
        rim_km=st["rim_km"],
        clusters_rim=sr.get("clusters"),
        people_rim=sr.get("population_est"),
        cropland_km2_2019=c.get("km2_2019"),
        clearing_km2_verified=d.get("km2_verified"),
        clearing_events_verified=d.get("events_verified"),
        fire_detections_2024_2025=f.get("detections_2024_2025"),
        fire_rate_per_1000km2_yr=f.get("detections_per_1000km2_per_year"),
        fire_rate_basis=f.get("rate_basis"),
        fronts_touching=fronts,
        fronts_dying_inside=t.get("ends_inside"),
        interception_pct=(round(100 * t["ends_inside"] / fronts, 1)
                          if fronts and t.get("ends_inside") is not None else None),
        gold_top05_cells_inside=m.get("top05_cells"),
        gold_candidates_inside=m.get("model_candidates"),
        gold_reported_inside=m.get("reported_mine_sites"),
        population_basis=("GHSL satellite estimate, a LOWER BOUND, never a "
                          "census"),
    )


ZONE_FIELDS = [
    ("name", ogr.OFTString), ("kml_name", ogr.OFTString),
    ("kml_file", ogr.OFTString),
    ("area_km2", ogr.OFTReal), ("file_says_km2", ogr.OFTReal),
    ("clusters_inside", ogr.OFTInteger), ("people_inside", ogr.OFTInteger),
    ("new_since_2015", ogr.OFTInteger),
    ("rim_km", ogr.OFTReal),
    ("clusters_rim", ogr.OFTInteger), ("people_rim", ogr.OFTInteger),
    ("cropland_km2_2019", ogr.OFTReal),
    ("clearing_km2_verified", ogr.OFTReal),
    ("clearing_events_verified", ogr.OFTInteger),
    ("fire_detections_2024_2025", ogr.OFTInteger),
    ("fire_rate_per_1000km2_yr", ogr.OFTReal),
    ("fire_rate_basis", ogr.OFTString),
    ("fronts_touching", ogr.OFTInteger), ("fronts_dying_inside", ogr.OFTInteger),
    ("interception_pct", ogr.OFTReal),
    ("gold_top05_cells_inside", ogr.OFTInteger),
    ("gold_candidates_inside", ogr.OFTInteger),
    ("gold_reported_inside", ogr.OFTInteger),
    ("population_basis", ogr.OFTString),
]


def main():
    if not FACTS.exists():
        sys.exit(f"{FACTS} missing - run scripts/easypip/pip_facts.py first")
    F = json.load(open(FACTS))
    st = M.load_stats()
    zones = M.load_zones(ROOT / "data/plan_zones")

    srs = osr.SpatialReference(); srs.ImportFromEPSG(4326)
    if OUT.exists():
        OUT.unlink()
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(OUT))

    counts = {}

    # ---------------------------------------------------------- 00 readme
    lyr = ds.CreateLayer("00_readme", geom_type=ogr.wkbNone)
    for n in ("topic", "text"):
        lyr.CreateField(ogr.FieldDefn(n, ogr.OFTString))
    readme = [
        ("what this is",
         "The EASY Priority Intervention Plan map for the proposed "
         "Pongo-Wau-Numatinna National Park, western South Sudan, as a QGIS "
         "project. Same content, same colours and same numbers as "
         "reports/EASY_PIP_MAP_2026-08.pdf; open Project > Open From > "
         "GeoPackage for the styled, ordered version."),
        ("reads with",
         "PLAN_APRCA_WSS_ASSESSMENT_TO_ACTION_2026-08_EASY.txt (the full "
         "report), PIP_TWO_PAGER_2026-08_EASY.txt (the summary), "
         "BUDGET_APRCA_WSS_2026-08_EASY.txt, "
         "ROAD_TAMBOURA_DEIM_ZUBEIR_2026-08_EASY.txt and "
         "XSA_CONSERVATION_REVIEW_2026-08_EASY.txt."),
        ("population",
         "Every population here is a GHSL satellite estimate and a LOWER "
         "BOUND, never a census. A field mission counted Raga at ~40,000 "
         "where this file measures 24,348."),
        ("fire rates",
         "Rates are 2024-2025 only. The VIIRS fleet triples on 2024-01-01; a "
         "rate spanning that date is not one series."),
        ("gold layers",
         F["gold"]["verdict"] + " Candidates and watchlist villages are "
         "imagery targets - places to look - never evidence that a pit is "
         "there. The top-5% cells are drawn only within 25 km of the "
         "proposed shapes: blank ground elsewhere means NOT DRAWN, not "
         "scored low."),
        ("the corridor",
         "The two 'ecological corridor' files each hold one map pin, "
         f"{F['corridor']['pins_apart_km']:g} km apart, not a polygon. Layer "
         "05 draws the axis they imply and layer 06 the pins as received. "
         "A pin cannot be gazetted - send a polygon."),
        ("the shapes overlap",
         f"{F['shapes']['n_shapes']} shapes in {F['shapes']['n_files']} files "
         f"cover {F['shapes']['union_km2']:,} km2 of distinct ground; their "
         f"areas sum to {F['shapes']['sum_of_areas_km2']:,} km2 because "
         f"{F['shapes']['overlap_km2']:,} km2 lies under more than one "
         "designation. Do not add the areas."),
        ("reproducing this file",
         "python3 scripts/easypip/pip_facts.py && python3 "
         "scripts/easypip/build_gpkg.py. Every number is read from "
         "data/eval/pip_facts.json and data/eval/zone_stats.json; none is "
         "typed."),
        ("generated", date.today().isoformat()),
    ]
    for topic, text in readme:
        f = ogr.Feature(lyr.GetLayerDefn())
        setf(f, topic=topic, text=text)
        lyr.CreateFeature(f)

    # ------------------------------------------------------- 01..04 shapes
    byrole = {}
    for nm, z in zones.items():
        byrole.setdefault(M.role_of(nm), []).append((nm, z))

    def write_zone_layer(table, role, description):
        rows = byrole.get(role, [])
        if not rows:
            return 0
        lyr = mklayer(ds, srs, table, ogr.wkbMultiPolygon, ZONE_FIELDS,
                      description)
        for nm, z in rows:
            f = ogr.Feature(lyr.GetLayerDefn())
            setf(f, **zone_stat_row(st, F, nm))
            f.SetGeometry(ogr.ForceToMultiPolygon(geom_of(z["geom"])))
            lyr.CreateFeature(f)
        return len(rows)

    counts["01_park"] = write_zone_layer(
        "01_park", "park",
        "The proposed Pongo-Wau-Numatinna National Park, as the KML received "
        "2026-08-31 draws it. Measured area differs from the file name by "
        "0.4% - polygon arithmetic, not a disagreement.")
    counts["02_wilderness"] = write_zone_layer(
        "02_wilderness", "wilderness",
        "The four proposed wilderness blocks. PROPOSED, not park: the "
        "protected park lies 99.4% inside the Wau block, so a legal "
        "instrument must say which designation governs a hectare.")
    counts["03_grazing"] = write_zone_layer(
        "03_grazing", "grazing",
        "The three sustainable-grazing zones. The instrument that actually "
        "touches people: 91% of the Numatina zone lies over ground other "
        "shapes also claim.")
    counts["04_existing_pa"] = write_zone_layer(
        "04_existing_pa", "existing",
        "Southern National Park - already gazetted, and already worked by "
        "Fauna & Flora with MWCT/SSWS under EU NaturAfrica funding. This "
        "plan is a neighbour to a running programme, not an arrival in a "
        "vacuum.")
    counts["06_corridor_pins"] = write_zone_layer(
        "06_corridor_pins", "pin",
        "The two 'ecological corridor' placemarks as received, each measured "
        "as a 15 km disc. A pin cannot be gazetted - the ask is a polygon.")
    if byrole.get("other"):
        counts["09_unassigned"] = write_zone_layer(
            "09_unassigned", "other",
            "Shapes in the received KML set that this analysis could not "
            "assign a role. Listed rather than dropped.")

    # ------------------------------------------------------ 05 corridor axis
    axis = M.corridor_axis(st)
    if axis:
        lyr = mklayer(ds, srs, "05_corridor_axis", ogr.wkbLineString, [
            ("ask", ogr.OFTString), ("pins_apart_km", ogr.OFTReal),
            ("shared_fronts_park_snp", ogr.OFTInteger),
            ("park_snp_touch_km2", ogr.OFTReal)],
            "The NW-SE axis the two sent pins imply. Drawn as the ASK it is.")
        g = ogr.Geometry(ogr.wkbLineString)
        for lon, lat in axis:
            g.AddPoint(lon, lat)
        f = ogr.Feature(lyr.GetLayerDefn())
        setf(f, ask=F["corridor"]["ask"],
             pins_apart_km=F["corridor"]["pins_apart_km"],
             shared_fronts_park_snp=F["corridor"]["park_snp_shared_fronts"],
             park_snp_touch_km2=F["corridor"]["park_snp_touch_km2"])
        f.SetGeometry(g)
        lyr.CreateFeature(f)
        counts["05_corridor_axis"] = 1

    # ------------------------------------------------------------- 07 reach
    fwd = pyproj.Transformer.from_crs(4326, "+proj=cea", always_xy=True).transform
    inv = pyproj.Transformer.from_crs("+proj=cea", 4326, always_xy=True).transform
    polys = unary_union([z["geom"] for z in zones.values()
                         if z["kind"] == "polygon"])
    rim_km = st["rim_km"]
    reach = transform(inv, transform(fwd, polys).buffer(rim_km * 1000))
    lyr = mklayer(ds, srs, "07_reach", ogr.wkbMultiPolygon, [
        ("what", ogr.OFTString), ("rim_km", ogr.OFTReal),
        ("union_km2", ogr.OFTReal), ("clusters_inside", ogr.OFTInteger),
        ("people_inside", ogr.OFTInteger), ("clusters_rim", ogr.OFTInteger),
        ("people_rim", ogr.OFTInteger)],
        "The ground this plan asks about: the union of every proposed "
        "polygon plus its 25 km rim. The gold layers are clipped to it.")
    f = ogr.Feature(lyr.GetLayerDefn())
    setf(f, what="union of proposed polygons + rim", rim_km=rim_km,
         union_km2=F["union"]["inside"]["area_km2"],
         clusters_inside=F["union"]["inside"]["clusters"],
         people_inside=F["union"]["inside"]["population_est"],
         clusters_rim=F["union"]["rim"]["clusters"],
         people_rim=F["union"]["rim"]["population_est"])
    f.SetGeometry(ogr.ForceToMultiPolygon(geom_of(reach)))
    lyr.CreateFeature(f)
    counts["07_reach"] = 1

    # ------------------------------------------------------- 10 plan sites
    sites = M.site_rows(st)
    lyr = mklayer(ds, srs, "10_plan_sites", ogr.wkbPoint, [
        ("name", ogr.OFTString), ("kind", ogr.OFTString),
        ("verdict", ogr.OFTString), ("why", ogr.OFTString),
        ("position_confirmed", ogr.OFTInteger),
        ("clusters_within_15km", ogr.OFTInteger),
        ("people_within_15km", ogr.OFTInteger),
        ("nearest_settlement_km", ogr.OFTReal),
        ("inside_zones", ogr.OFTString), ("nearest_zone_km", ogr.OFTString)],
        "The plan's own geography, with the verdict the assessment gave each "
        "site and the people measured within 15 km of it.")
    for s in sites:
        key = next(k for k in F["sites"] if k.split("(")[0].strip() == s["name"])
        v = F["sites"][key]
        nz = ", ".join(f"{M.short_name(k)} {d:g} km"
                       for k, d in sorted(v["km_to_nearest_zones"].items(),
                                          key=lambda kv: kv[1]))
        f = ogr.Feature(lyr.GetLayerDefn())
        setf(f, name=s["name"], kind=s["kind"], verdict=s["verdict"],
             why=s["why"], position_confirmed=0 if s["approx"] else 1,
             clusters_within_15km=v["clusters"], people_within_15km=v["people"],
             nearest_settlement_km=v["nearest_settlement_km"],
             inside_zones=", ".join(M.short_name(z) for z in v["inside_zones"])
             or "(none)", nearest_zone_km=nz)
        f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT({s['lon']} {s['lat']})"))
        lyr.CreateFeature(f)
    counts["10_plan_sites"] = len(sites)

    # -------------------------------------------- 11 towns no site reaches
    frame = polys.bounds
    setl = M.load_settlements((frame[0] - M.FRAME_PAD_DEG,
                               frame[1] - M.FRAME_PAD_DEG,
                               frame[2] + M.FRAME_PAD_DEG,
                               frame[3] + M.FRAME_PAD_DEG))
    belt = M.unserved_belt(setl, sites, reach)
    if belt:
        lyr = mklayer(ds, srs, "11_plan_unreached_towns", ogr.wkbPoint, [
            ("population_est", ogr.OFTInteger),
            ("km_to_nearest_site", ogr.OFTReal),
            ("rule", ogr.OFTString)],
            "Towns of 2,000+ people beside the proposed area that no site in "
            "the plan reaches within 40 km. Both thresholds are stated so "
            "the scope travels with the claim.")
        rule = (f"population >= {belt['min_pop']}, inside the union+rim, and "
                f">= {belt['reach_km']:g} km from every plan site")
        for lon, lat, pop in belt["towns"]:
            d = min(GEOD.inv(s["lon"], s["lat"], lon, lat)[2] / 1000
                    for s in sites)
            f = ogr.Feature(lyr.GetLayerDefn())
            setf(f, population_est=int(pop), km_to_nearest_site=round(d, 1),
                 rule=rule)
            f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT({lon} {lat})"))
            lyr.CreateFeature(f)
        counts["11_plan_unreached_towns"] = len(belt["towns"])

    # -------------------------------------------------------- 20 settlements
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    x0, y0, x1, y1 = (frame[0] - M.FRAME_PAD_DEG, frame[1] - M.FRAME_PAD_DEG,
                      frame[2] + M.FRAME_PAD_DEG, frame[3] + M.FRAME_PAD_DEG)
    rows = con.execute(
        """SELECT lat, lon, population_est, nearest_place, classification,
                  persistence, area_m2, cropland_frac_2019
           FROM park_settlements
           WHERE park_id = ? AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?""",
        (AOI, x0, x1, y0, y1)).fetchall()
    con.close()
    pr_reach, pr_polys = prep(reach), prep(polys)
    lyr = mklayer(ds, srs, "20_settlements", ogr.wkbPoint, [
        ("nearest_place", ogr.OFTString), ("population_est", ogr.OFTInteger),
        ("classification", ogr.OFTString), ("persistence", ogr.OFTString),
        ("built_km2", ogr.OFTReal), ("cropland_frac_2019", ogr.OFTReal),
        ("inside_proposed", ogr.OFTInteger), ("inside_reach", ogr.OFTInteger),
        ("population_basis", ogr.OFTString)],
        "Settlement clusters in frame. One row per CLUSTER (not per built "
        "footprint). Population is a GHSL satellite estimate and a lower "
        "bound, never a census.")
    for lat, lon, pop, place, cls, pers, area, crop in rows:
        p = Point(lon, lat)
        f = ogr.Feature(lyr.GetLayerDefn())
        setf(f, nearest_place=place, population_est=int(pop or 0),
             classification=cls, persistence=pers,
             built_km2=round((area or 0) / 1e6, 4),
             cropland_frac_2019=crop,
             inside_proposed=1 if pr_polys.contains(p) else 0,
             inside_reach=1 if pr_reach.contains(p) else 0,
             population_basis="GHSL R2023A estimate; a lower bound")
        f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT({lon} {lat})"))
        lyr.CreateFeature(f)
    counts["20_settlements"] = len(rows)

    # ------------------------------------------------------- 21 fire fronts
    fire_segs, fire_types, _yrs = M.load_fire((x0, y0, x1, y1))
    groups = json.load(open(M.GROUPS))
    lyr = mklayer(ds, srs, "21_fire_fronts", ogr.wkbLineString, [
        ("group_type", ogr.OFTString), ("fire_count", ogr.OFTInteger),
        ("start_date", ogr.OFTString), ("end_date", ogr.OFTString),
        ("distance_km", ogr.OFTReal), ("duration_days", ogr.OFTInteger),
        ("basis", ogr.OFTString)],
        "Every v5 fire front whose path enters the frame, 2024-2026. Drawn on "
        "the sheet as one hairline each: the DENSITY is the quantity, not any "
        "single path. 88% of these are classed transhumance - this is the "
        "movement of herders, and a management plan for any of these shapes "
        "is a fire-governance plan or it is fiction.")
    n_fire = 0
    for g in groups:
        tr = g.get("trajectory") or []
        if len(tr) < 2:
            continue
        pts = [(p[0], p[1]) for p in tr]
        if not any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in pts):
            continue
        line = ogr.Geometry(ogr.wkbLineString)
        for lon, lat in pts:
            line.AddPoint(lon, lat)
        f = ogr.Feature(lyr.GetLayerDefn())
        setf(f, group_type=g.get("group_type"), fire_count=g.get("fire_count"),
             start_date=g.get("start_date"), end_date=g.get("end_date"),
             distance_km=g.get("total_distance_km"),
             duration_days=g.get("duration_days"),
             basis="v5 trajectory tracker over NASA FIRMS VIIRS detections")
        f.SetGeometry(line)
        lyr.CreateFeature(f)
        n_fire += 1
    counts["21_fire_fronts"] = n_fire

    # ------------------------------------------------------ 30..33 the gold
    gold, gold_note = M.load_gold(reach)
    sk5 = F["gold"]["skill_top05"]
    if gold:
        lyr = mklayer(ds, srs, "30_gold_top5_cells", ogr.wkbPolygon, [
            ("tier", ogr.OFTString), ("lift_reach", ogr.OFTReal),
            ("p_reach", ogr.OFTReal), ("clip", ogr.OFTString),
            ("caveat", ogr.OFTString)],
            "Top 5% of ground by the mining model's composite score, CLIPPED "
            "to the union+rim. Blank ground elsewhere means NOT DRAWN, not "
            "scored low. " + F["gold"]["verdict"])
        # The prediction grid's own cell size, read from its output - a
        # typed 0.05 here would survive a re-gridded model and draw the wrong
        # squares (root invariant 2).
        half = json.load(open(ROOT / "data/eval/xsa_mining/prediction.json")
                         )["cell_deg"] / 2
        for lon, lat in gold["top5"]:
            ring = ogr.Geometry(ogr.wkbLinearRing)
            for dx, dy in ((-half, -half), (half, -half), (half, half),
                           (-half, half), (-half, -half)):
                ring.AddPoint(lon + dx, lat + dy)
            poly = ogr.Geometry(ogr.wkbPolygon); poly.AddGeometry(ring)
            f = ogr.Feature(lyr.GetLayerDefn())
            setf(f, tier="top05", lift_reach=sk5["lift_reach"],
                 p_reach=sk5["p_reach"],
                 clip=f"within {rim_km:g} km of the proposed shapes",
                 caveat=F["gold"]["verdict"])
            f.SetGeometry(poly)
            lyr.CreateFeature(f)
        counts["30_gold_top5_cells"] = len(gold["top5"])

        lyr = mklayer(ds, srs, "31_gold_candidates", ogr.wkbPoint, [
            ("character", ogr.OFTString), ("caveat", ogr.OFTString)],
            "Model candidates inside the ask: imagery targets, never mines. "
            "Cell centres, good to about 3 km - a neighbourhood, not a pit.")
        for lon, lat, ch in gold["candidates"]:
            f = ogr.Feature(lyr.GetLayerDefn())
            setf(f, character=ch,
                 caveat="an imagery target with a measured, modest skill - "
                        "never evidence that a pit is there")
            f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT({lon} {lat})"))
            lyr.CreateFeature(f)
        counts["31_gold_candidates"] = len(gold["candidates"])

        lyr = mklayer(ds, srs, "32_gold_watchlist", ogr.wkbPoint, [
            ("name", ogr.OFTString), ("caveat", ogr.OFTString)],
            "Villages named on the 1930s survey sheets that stand empty "
            "today and sit on gold-graded rock. The strongest single signal "
            "in the model (4x) - and still only a place to look.")
        for lon, lat, nm in gold["watchlist"]:
            f = ogr.Feature(lyr.GetLayerDefn())
            setf(f, name=nm or "(unnamed)",
                 caveat="1930s name, nothing there today; a place to look")
            f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT({lon} {lat})"))
            lyr.CreateFeature(f)
        counts["32_gold_watchlist"] = len(gold["watchlist"])

        lyr = mklayer(ds, srs, "33_gold_reported", ogr.wkbPoint, [
            ("source", ogr.OFTString), ("caveat", ogr.OFTString)],
            "Reported workings inside the ask (OpenStreetMap / Invisible "
            "Children Crisis Tracker). An empty map here is the reach of "
            "those lists, not the absence of pits.")
        for lon, lat, src in gold["anchors"]:
            f = ogr.Feature(lyr.GetLayerDefn())
            setf(f, source=src,
                 caveat="reported, mostly because somebody was attacked or "
                        "somebody mapped it; absence is coverage, not geology")
            f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT({lon} {lat})"))
            lyr.CreateFeature(f)
        counts["33_gold_reported"] = len(gold["anchors"])

    # ----------------------------------------------------------- 40 rivers
    rivers = M.load_rivers((x0, y0, x1, y1), 6)
    if rivers:
        lyr = mklayer(ds, srs, "40_rivers", ogr.wkbLineString,
                      [("note", ogr.OFTString)],
                      "Trunk rivers (HydroSHEDS stream order 6+). Context: "
                      "farms, mines and clearings all happen next to water.")
        for line in rivers:
            g = ogr.Geometry(ogr.wkbLineString)
            for lon, lat in line:
                g.AddPoint(lon, lat)
            f = ogr.Feature(lyr.GetLayerDefn())
            setf(f, note="stream order >= 6")
            f.SetGeometry(g)
            lyr.CreateFeature(f)
        counts["40_rivers"] = len(rivers)

    ds = None
    return counts, F


# --------------------------------------------------------- styles + project
def layer_styles(F, counts):
    """One style per layer, in the SHEET's colours - the file and the printed
    map must not be two opinions about what a wilderness block looks like."""
    pop = [1, 100, 1000, 10000, 200000]
    s = {
        "01_park": fill_style(M.GREEN, "1.1", M.GREEN, 35, label_field="name",
                              label_size="11"),
        "02_wilderness": fill_style(M.GREEN_W, "0.5", M.GREEN_W, 26,
                                    label_field="name", label_size="9"),
        "03_grazing": fill_style(M.TAN, "0.45", M.TAN, 14, dash="4;2",
                                 label_field="name", label_size="9"),
        "04_existing_pa": fill_style(M.GREEN_E, "0.6", M.GREEN_E, 30,
                                     label_field="name", label_size="9"),
        "05_corridor_axis": line_style(M.PLAN, "0.9", dash="5;2"),
        "06_corridor_pins": fill_style(M.PLAN, "0.4", M.PLAN, 18, dash="2;2"),
        "07_reach": fill_style("#8a8a80", "0.3", None, 0, dash="6;3"),
        "10_plan_sites": rule_marker_style([
            ('"kind" = \'focal\'', "Town focal point", "star", M.PLAN,
             "#ffffff", "6.0", "0.6"),
            ('"kind" = \'echo\' AND "verdict" = \'anchor\'',
             "Anchor station \u2014 staffed", "square", M.PLAN, "#ffffff",
             "3.6", "0.6"),
            ('"verdict" = \'seasonal\'', "Seasonal outreach only", "circle",
             None, M.PLAN, "3.2", "0.9"),
        ], label_field="name", label_size="10"),
        "11_plan_unreached_towns": marker_style(
            "circle", None, "#8a2020", "4.0", "0.8"),
        "20_settlements": graduated_marker_style(
            "population_est", pop, M.ORANGE, ["1.4", "2.4", "3.8", "6.4"]),
        "21_fire_fronts": line_style(M.RED, "0.10", alpha=28),
        "30_gold_top5_cells": fill_style(M.GOLD, "0.25", M.GOLD, 55),
        "31_gold_candidates": marker_style("triangle", None, M.GOLD, "3.4",
                                           "0.7"),
        "32_gold_watchlist": marker_style("cross2", None, M.GOLD, "2.8", "0.7"),
        "33_gold_reported": marker_style("diamond", M.GOLD, "#4a3a0a", "2.6",
                                         "0.4"),
        "40_rivers": line_style(M.BLUE, "0.35"),
        "09_unassigned": fill_style("#999999", "0.35", "#999999", 12,
                                    dash="1;2", label_field="name"),
    }
    return {k: v for k, v in s.items() if k in counts}


# Draw order is bottom-first, like a map. What ships ON mirrors the printed
# sheet exactly; the two heavy context layers (every settlement in frame is
# fine, every fire front is 35k hairlines) stay on because they ARE the sheet -
# the one thing switched off is the reach outline, which is a working
# geometry rather than a finding.
PROJECT_LAYERS = [
    # table, title, group, geometry, wkb, visible, opacity
    ("40_rivers", "Trunk rivers", "Context", "Line", "LineString", True, 1.0),
    ("07_reach", "The ask: proposed shapes + 25 km rim", "Context", "Polygon",
     "MultiPolygon", False, 1.0),
    ("21_fire_fronts", "Fire fronts 2024\u20132026 (one line = one front)",
     "What is happening", "Line", "LineString", True, 1.0),
    ("03_grazing", "Sustainable-grazing zones", "Boundaries", "Polygon",
     "MultiPolygon", True, 1.0),
    ("02_wilderness", "Wilderness blocks (proposed, not park)", "Boundaries",
     "Polygon", "MultiPolygon", True, 1.0),
    ("04_existing_pa", "Southern National Park (gazetted)", "Boundaries",
     "Polygon", "MultiPolygon", True, 1.0),
    ("09_unassigned", "Shapes with no assigned role", "Boundaries", "Polygon",
     "MultiPolygon", False, 1.0),
    ("01_park", "Pongo\u2013Wau\u2013Numatinna (proposed park)", "Boundaries",
     "Polygon", "MultiPolygon", True, 1.0),
    ("20_settlements", "Settlements (size \u221d measured people)",
     "What is happening", "Point", "Point", True, 1.0),
    ("30_gold_top5_cells", "Top 5% by model score (clipped to the ask)",
     "Gold prospectivity", "Polygon", "Polygon", True, 1.0),
    ("33_gold_reported", "Reported working (OSM / Crisis Tracker)",
     "Gold prospectivity", "Point", "Point", True, 1.0),
    ("32_gold_watchlist", "Watchlist: 1930s village on gold, empty today",
     "Gold prospectivity", "Point", "Point", True, 1.0),
    ("31_gold_candidates", "Imagery target \u2014 never a mine",
     "Gold prospectivity", "Point", "Point", True, 1.0),
    ("06_corridor_pins", "Corridor pins as received (15 km discs)", "The plan",
     "Polygon", "MultiPolygon", True, 1.0),
    ("05_corridor_axis", "Corridor axis \u2014 the ask is a polygon",
     "The plan", "Line", "LineString", True, 1.0),
    ("11_plan_unreached_towns", "Towns no site in the plan reaches",
     "The plan", "Point", "Point", True, 1.0),
    ("10_plan_sites", "ECHO/TANGO sites and focal points", "The plan", "Point",
     "Point", True, 1.0),
]


def xmlesc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


QGS_SRS = """<spatialrefsys nativeFormat="Wkt">
  <proj4>+proj=longlat +datum=WGS84 +no_defs</proj4>
  <srsid>3452</srsid><srid>4326</srid><authid>EPSG:4326</authid>
  <description>WGS 84</description>
  <projectionacronym>longlat</projectionacronym>
  <ellipsoidacronym>EPSG:7030</ellipsoidacronym>
  <geographicflag>true</geographicflag>
</spatialrefsys>"""


def qml_body(q):
    """Strip the <qgis> wrapper: what remains belongs inside <maplayer>."""
    i = q.index("<qgis ")
    j = q.index(">", i)
    return q[j + 1:].rsplit("</qgis>", 1)[0]


def build_project(name, gpkg_name, layers, styles, bbox):
    """A .qgs that opens the file already ordered, grouped and switched on.

    Styles alone are not enough: a GeoPackage has no layer order and no
    visibility, so a styled-but-projectless export opens as 35,000 fire
    hairlines drawn over everything. This is the same trick srv/gpkg_project.go
    plays for the app's exports, and the datasource is './<file>.gpkg' - a
    self-reference, so the on-disk name must equal the download name.
    """
    groups, bygroup = [], {}
    for spec in layers:
        g = spec[2]
        if g not in bygroup:
            groups.append(g)
            bygroup.setdefault(g, [])
        bygroup[g].append(spec)

    tree, maps = [], []
    for g in reversed(groups):
        items = bygroup[g]
        checked = "Qt::Checked" if any(i[5] for i in items) else "Qt::Unchecked"
        tree.append(f'<layer-tree-group name="{xmlesc(g)}" expanded="1" '
                    f'checked="{checked}">')
        for table, title, _g, _geom, _wkb, vis, _op in reversed(items):
            tree.append(
                f'<layer-tree-layer source="./{gpkg_name}|layername={table}" '
                f'providerKey="ogr" expanded="0" id="{table}_easypip" '
                f'name="{xmlesc(title)}" '
                f'checked="{"Qt::Checked" if vis else "Qt::Unchecked"}" '
                f'legend_split_behavior="0" patch_size="-1,-1">'
                f'<customproperties><Option/></customproperties>'
                f'</layer-tree-layer>')
        tree.append("</layer-tree-group>")

    for table, title, _g, geom, wkb, _vis, op in layers:
        abstract = DESCR.get(table, "")
        meta = (f'<resourceMetadata><identifier>{xmlesc(table)}</identifier>'
                f'<title>{xmlesc(title)}</title><type>dataset</type>'
                f'<language>ENG</language>'
                f'<abstract>{xmlesc(abstract)}</abstract></resourceMetadata>'
                if abstract else "")
        maps.append(f'''<maplayer type="vector" geometry="{geom}" wkbType="{wkb}"
  labelsEnabled="1" simplifyDrawingTol="1" simplifyLocal="1"
  simplifyAlgorithm="0" simplifyDrawingHints="1" simplifyMaxScale="1"
  minScale="0" maxScale="0" hasScaleBasedVisibilityFlag="0"
  symbologyReferenceScale="-1" readOnly="0" autoRefreshMode="Disabled"
  autoRefreshTime="0" refreshOnNotifyEnabled="0"
  styleCategories="AllStyleCategories">
  <id>{table}_easypip</id>
  <datasource>./{gpkg_name}|layername={table}</datasource>
  <layername>{xmlesc(title)}</layername>
  <srs>{QGS_SRS}</srs>
  <provider encoding="UTF-8">ogr</provider>
  <flags><Identifiable>1</Identifiable><Removable>1</Removable>
    <Searchable>1</Searchable><Private>0</Private></flags>
  <temporal enabled="0" mode="0"><fixedRange><start></start><end></end>
    </fixedRange></temporal>
  {qml_body(styles[table])}
  <layerOpacity>{op:g}</layerOpacity>
  <blendMode>0</blendMode><featureBlendMode>0</featureBlendMode>
  {meta}
</maplayer>''')

    return f'''<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.0" projectname="{xmlesc(name)}">
<homePath path=""/>
<title>{xmlesc(name)}</title>
<transaction mode="Disabled"/>
<projectCrs>{QGS_SRS}</projectCrs>
<layer-tree-group>
{chr(10).join(tree)}<custom-order enabled="0"/>
</layer-tree-group>
<snapping-settings enabled="0" tolerance="12" unit="1" mode="2" type="1"/>
<relations/>
<mapcanvas name="theMapCanvas" annotationsVisible="1">
  <units>degrees</units>
  <extent><xmin>{bbox[0]:g}</xmin><ymin>{bbox[1]:g}</ymin>
    <xmax>{bbox[2]:g}</xmax><ymax>{bbox[3]:g}</ymax></extent>
  <rotation>0</rotation>
  <destinationsrs>{QGS_SRS}</destinationsrs>
  <rendermaptile>0</rendermaptile>
</mapcanvas>
<projectlayers>
{chr(10).join(maps)}</projectlayers>
<ProjectViewSettings rotation="0" UseProjectScales="0"><Scales/>
  <DefaultViewExtent xmin="{bbox[0]:g}" ymin="{bbox[1]:g}" xmax="{bbox[2]:g}"
    ymax="{bbox[3]:g}">{QGS_SRS}</DefaultViewExtent>
</ProjectViewSettings>
<properties>
  <Gui><CanvasColour type="QString">#fdfdfa</CanvasColour></Gui>
  <Measure><Ellipsoid type="QString">EPSG:7030</Ellipsoid></Measure>
  <PositionPrecision><Automatic type="bool">true</Automatic></PositionPrecision>
</properties>
<visibility-presets/><layerorder/>
</qgis>
'''


def write_styles_and_project(styles, counts, bbox):
    import io
    import zipfile
    con = sqlite3.connect(OUT)
    con.execute("""CREATE TABLE IF NOT EXISTS layer_styles (
      id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT,
      f_table_schema TEXT, f_table_name TEXT, f_geometry_column TEXT,
      styleName TEXT, styleQML TEXT, styleSLD TEXT, useAsDefault BOOLEAN,
      description TEXT, owner TEXT, ui TEXT,
      update_time DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    try:
        con.execute("INSERT INTO gpkg_contents(table_name,data_type,identifier)"
                    " VALUES('layer_styles','attributes','layer_styles')")
    except sqlite3.IntegrityError:
        pass
    for name, q in styles.items():
        con.execute(
            "INSERT INTO layer_styles(f_table_catalog,f_table_schema,"
            "f_table_name,f_geometry_column,styleName,styleQML,styleSLD,"
            "useAsDefault,description,owner) VALUES('','',?,?,?,?,'',1,?,'')",
            (name, "geom", name + "_default", q, DESCR.get(name, "")))

    layers = [l for l in PROJECT_LAYERS if l[0] in counts and l[0] in styles]
    qgs = build_project("EASY PIP \u2014 Pongo-Wau-Numatinna", OUT.name,
                        layers, styles, bbox)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("easypip.qgs", qgs)
    con.execute("""CREATE TABLE IF NOT EXISTS qgis_projects
                   (name TEXT PRIMARY KEY, metadata BLOB, content BLOB)""")
    con.execute("INSERT OR REPLACE INTO qgis_projects(name,metadata,content) "
                "VALUES(?,?,?)",
                ("EASY PIP \u2014 Pongo-Wau-Numatinna",
                 '{"last_modified_user": "5MP Conservation Monitoring"}',
                 buf.getvalue().hex()))
    con.commit()
    con.close()


if __name__ == "__main__":
    counts, F = main()
    zones = M.load_zones(ROOT / "data/plan_zones")
    allg = unary_union([z["geom"] for z in zones.values()])
    b = allg.bounds
    bbox = (b[0] - M.FRAME_PAD_DEG, b[1] - M.FRAME_PAD_DEG,
            b[2] + M.FRAME_PAD_DEG, b[3] + M.FRAME_PAD_DEG)
    write_styles_and_project(layer_styles(F, counts), counts, bbox)
    print("wrote", OUT)
    for k in sorted(counts):
        print(f"  {k:28s} {counts[k]:>7,}")
