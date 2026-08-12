"""Tanzania geology sheet: fetch the GST/GMIS 1:1.5M polygons and build the catalogue.

WHY THIS SCRIPT LOOKS NOTHING LIKE vectorize.py
-----------------------------------------------
Sudan and CAR are *scans*.  Everything in scripts/geomaps/ up to now exists
because those two sheets are halftone screens that had to be georeferenced,
classified pixel by pixel and polygonised, with a hold-out to prove a formation
had not silently vanished.  Tanzania is not a scan: the Geological Survey of
Tanzania publishes the Minerogenic Map of Tanzania (2015, 1:1,500,000) as
**vector data** through the GMIS GeoServer, one polygon per mapped body with
the survey's own attribute table (code, legend text, tectonic unit,
chronostratigraphy, age in Ma, lithology, metamorphic grade).

So there is no classifier here, no legend measured off paper, no claim rate and
no merged classes: a class is a `leg_id` of the publisher's own legend, and its
colour is the publisher's own ink read out of the GeoServer SLD.  What this
script does instead is (a) download without truncating, (b) prove the
coordinates are where Tanzania is, and (c) write the same two files every other
sheet writes, so nothing downstream needs to know the difference:

    data/geomaps/tanzania_units.geojson   gitignored derived output
    data/geomaps/tanzania_classes.json    committed catalogue, read by srv/geomap.go

TRUNCATION IS THE FAILURE MODE THAT HAS ALREADY COST THIS PROJECT DAYS
----------------------------------------------------------------------
`scripts/histmaps/README.md`: a curl that stopped at 264 of 770 lines produced a
mosaic that built cleanly, passed QA and then *documented the missing half of
the country as absent from the archive*.  A short download reads as a small
collection, never as a broken fetch.  Here the same shape would be a WFS reply
cut off mid-FeatureCollection, or a GeoServer `count=` default silently capping
the page.  Guards, all of which ABORT rather than write a partial sheet:

  * the feature count is cross-checked against the server's own
    `resultType=hits` (`numberMatched`) before anything is written;
  * the reply must parse as JSON *and* end with a complete FeatureCollection --
    a truncated body fails the parse, which is the point of parsing before
    caching;
  * every geometry must fall inside Tanzania's envelope.  GeoServer honours
    `srsName=EPSG:4326` but WFS 1.0.0 hands back lat/lon axis order, which
    yields a country in the Indian Ocean off Somalia that still *looks* like a
    map.  We ask 2.0.0 and then verify the numbers rather than trusting the
    version note.
  * `--reproject-local` is the fallback if the server's reprojection is ever
    wrong: download native EPSG:21036 (Arc 1960 / UTM zone 36S) and let
    ogr2ogr do it.  Both paths are verified by the same envelope check.

The raw download is cached under data/geomaps/src/ so a re-run that only
changes the catalogue does not re-fetch; `--refetch` forces it.

NOTHING HERE COUNTS OR LOCATES A DEPOSIT
----------------------------------------
GMIS also publishes mineral occurrences and licences.  We deliberately do not
ship those (docs/agents/mining.md).  The `affinity` table below is the same
kind of statement the other two sheets make -- "rocks of this kind host X",
an inference over lithology, keyed by (sheet, code) and carrying its reason in
words.  It is never a record of a deposit, and every surface that shows it says
so.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(ROOT, "data", "geomaps")
SRC_DIR = os.path.join(OUT_DIR, "src")

SHEET = "tanzania"
OWS = "https://gmis-tanzania.com/geoserver/gmis/ows"
LAYER = "gmis:minerogenicgeology"

# The publisher's own description of what this layer is.  `year` is the
# publication year of the Minerogenic Map of Tanzania (GST/Beak, 2015), of
# which this geology layer is the base map.
SHEET_META = dict(
    sheet=SHEET,
    title="Geological map of Tanzania (Minerogenic Map of Tanzania, 2015)",
    short="Tanzania geology",
    year=2015,
    # Attribution: recorded as published, not as we would like it to read.  The
    # site states the publisher and the implementer and states no licence
    # (WFS GetCapabilities: Fees NONE, AccessConstraints NONE), so we attribute
    # in full and link the portal rather than claim a licence we did not see.
    publisher="Geological Survey of Tanzania (GST), Dodoma - Geological and Mineral "
              "Information System (GMIS), implemented by Beak Consultants GmbH; "
              "Minerogenic Map of Tanzania 1:1,500,000 (2015)",
    scale="1:1,500,000",
    source_url="https://gmis-tanzania.com/",
    countries=["TZA"],
)

# Tanzania's envelope, as the server's own capabilities document states it for
# THIS layer (LatLonBoundingBox 29.476..40.535 E, -11.838..-0.921 N), padded a
# little.  Used as an assertion, not as a filter: a feature outside it means
# the reprojection went wrong (lat/lon axis order is the classic one), and the
# right response is to stop, not to clip a country into shape.
TZ_BOX = (28.5, -12.6, 41.5, -0.5)

# WFS pages.  GeoServer's default maxFeatures can be finite, and a page that
# comes back exactly full is indistinguishable from a complete answer, so we
# page explicitly and stop on the server's own numberMatched.
PAGE = 500

# ---------------------------------------------------------------------------
# commodity affinity
# ---------------------------------------------------------------------------
# INFERENCE OVER LITHOLOGY, NEVER AN OCCURRENCE DATASET.  Same contract as
# legend.AFFINITY for the two scanned sheets, and the same disclaimer on every
# surface that shows it: "rocks of this kind host X".  Nothing here counts,
# ranks or locates a deposit, and no entry names a mine.  Where a why-string
# mentions a named geological entity it is naming the ROCK UNIT the survey
# itself names (the Nyanzian greenstones, the Kabanga-Musongati layered
# intrusion, the Karoo Supergroup), not a discovery.
#
# weight 1-3, as on the other sheets: 3 = classic host, 2 = plausible host,
# 1 = weak or derived association.
#
# Keyed by the GST's own unit abbreviation, and used only for the tanzania
# sheet -- codes collide across sheets (see legend.AFFINITY's note).
AFFINITY = {
    # --- Archaean Tanzania Craton -----------------------------------------
    "gsNA": [("gold", 3, "Archaean greenstone belt (Nyanzian mafic volcanics, meta-basalts, "
                         "phyllite): the classic orogenic-gold host lithology"),
             ("iron", 3, "banded iron formation within the greenstone succession"),
             ("copper", 2, "mafic metavolcanic pile: volcanogenic massive sulphide setting"),
             ("cobalt", 1, "komatiitic and mafic volcanics: nickel-cobalt affinity")],
    "miNA": [("gold", 2, "granitoid-migmatite basement enclosing the greenstone belts; "
                         "its late structures host lode gold"),
             ("lithium", 1, "granitoid basement: pegmatite affinity"),
             ("diamond", 1, "Archaean cratonic basement: the thick, cold lithosphere "
                            "kimberlite emplacement requires")],
    "?NA": [("gold", 1, "synorogenic granitoids of the greenstone-belt basement"),
            ("lithium", 1, "granitoid: pegmatite affinity")],
    "?dNA-PP": [("lithium", 2, "late-postorogenic granite: pegmatite and greisen affinity"),
                ("rare_earth", 1, "evolved granite association"),
                ("gold", 1, "late granites drive the hydrothermal systems of the craton margin")],
    "gn-grMA-NA": [("diamond", 2, "Meso-Neoarchaean cratonic core (Dodoman): the thick, cold "
                                  "lithosphere kimberlite emplacement requires"),
                   ("gold", 1, "Archaean gneiss-granite-migmatite basement of the goldfield")],
    # --- Palaeoproterozoic mobile belts (Ubendian, Usagaran) --------------
    "msPPUB": [("gold", 2, "Palaeoproterozoic meta-sedimentary and meta-igneous belt rocks: "
                           "orogenic gold affinity"),
               ("graphite", 2, "granulite-facies meta-sediments: flake-graphite affinity")],
    "msPPUS": [("gold", 2, "Palaeoproterozoic meta-igneous and meta-sedimentary belt rocks: "
                           "orogenic gold affinity"),
               ("graphite", 2, "granulite-facies meta-sediments: flake-graphite affinity")],
    "?PP": [("gold", 1, "felsic igneous formation of the Ubendian belt"),
            ("rare_earth", 1, "felsic igneous association")],
    "gtPP": [("graphite", 3, "granulite-facies meta-sedimentary complex: flake-graphite affinity"),
             ("gemstone", 2, "high-grade meta-sediments with eclogite: garnet and corundum "
                             "gem affinity")],
    "gtPP-NP": [("cobalt", 1, "charnockite-enderbite: mafic granulite affinity"),
                ("graphite", 2, "granulite-facies gneiss: flake-graphite affinity")],
    "gnPP-MP": [("cobalt", 2, "gneiss with meta-gabbro, anorthosite and ultramafic rocks: "
                              "magmatic nickel-cobalt affinity"),
                ("copper", 1, "mafic-hosted base metals")],
    # --- Mesoproterozoic Kibaran / Karagwe-Ankolean ------------------------
    "s?MP": [("cobalt", 3, "layered mafic-ultramafic intrusion (peridotite, dunite, norite): "
                           "the classic magmatic nickel-copper-cobalt host"),
             ("copper", 2, "magmatic sulphide affinity in the same intrusion")],
    "sMP": [("cobalt", 2, "gabbroic lopolith: mafic intrusion-hosted nickel-cobalt affinity"),
            ("copper", 1, "mafic intrusion-hosted base metals")],
    "?MP-NP": [("lithium", 3, "Kibaran tin granite (Sn-G4): the type setting for cassiterite- "
                              "and columbite-tantalite-bearing pegmatite and greisen"),
               ("rare_earth", 2, "evolved Sn granite: niobium-tantalum affinity"),
               ("gold", 1, "granite-driven hydrothermal systems")],
    "?dPP-MP": [("lithium", 2, "late-orogenic granite and granodiorite: pegmatite affinity"),
                ("gold", 1, "late-orogenic intrusions drive hydrothermal systems"),
                ("rare_earth", 1, "evolved granite association")],
    "dMP": [("lithium", 1, "Kibaran meta-sediments intruded by the tin granites: "
                           "cassiterite-bearing vein and greisen affinity")],
    "pqMP": [("lithium", 1, "phyllitic-quartzitic meta-sediments of the tin-granite aureole: "
                            "cassiterite and wolframite affinity"),
             ("gold", 1, "quartzitic meta-sediment: quartz-vein gold affinity")],
    "?-miMP": [("gold", 1, "granitoid and migmatite complex: granite-driven hydrothermal systems")],
    # --- Neoproterozoic East African Orogen (Mozambique Belt) -------------
    "miNP": [("graphite", 3, "granulite- to amphibolite-facies para-gneiss of the Mozambique "
                             "Belt: the classic flake-graphite host"),
             ("gemstone", 3, "marble and calc-silicate within the same complex: the host "
                             "lithology for corundum, spinel and garnet gems")],
    "mbNP": [("gemstone", 3, "crystalline limestone (marble): corundum and spinel gem affinity")],
    "gtNA-NP": [("graphite", 3, "mafic-felsic granulite complex with meta-sediments: "
                                "flake-graphite affinity"),
                ("gemstone", 2, "granulite with marble and calc-silicate: gem corundum and "
                                "garnet affinity")],
    "msNA-NP": [("graphite", 2, "meta-sedimentary complex of the Mozambique Belt: "
                                "flake-graphite affinity"),
                ("gold", 1, "meta-igneous belt rocks: orogenic gold affinity")],
    "s?NA-NP": [("cobalt", 2, "meta-gabbro and anorthosite: magmatic nickel-cobalt affinity"),
                ("copper", 1, "mafic intrusion-hosted base metals")],
    "xNP": [("rare_earth", 3, "alkaline syenite-gabbro ring complex: the carbonatite-syenite "
                              "setting that carries niobium and rare earths"),
            ("cobalt", 1, "pyroxenite and gabbro of the same complex")],
    # --- Neoproterozoic tabular cover (Bukoban) ---------------------------
    "cNP": [("copper", 1, "detrital red beds: sediment-hosted copper affinity")],
    "vNP": [("copper", 1, "amygdaloidal basalt with dolomite: basalt-hosted copper affinity")],
    # --- Late Palaeozoic - Mesozoic cover ---------------------------------
    "tC2-J1K": [("coal", 3, "Karoo Supergroup terrestrial clastics with coal measures: "
                            "the country's coal-bearing sequence"),
                ("uranium", 2, "Karoo fluviatile sandstone: roll-front uranium host")],
    "mK": [("uranium", 1, "Cretaceous continental sandstone: roll-front uranium affinity")],
    # --- Cenozoic ----------------------------------------------------------
    "vN-Q": [("rare_earth", 2, "alkaline nephelinite-phonolite lavas: the volcanic expression "
                               "of the carbonatite province that carries niobium and rare earths")],
    "vpN-Q": [("rare_earth", 1, "alkaline pyroclastics of the same volcanic province")],
    "lN-Q": [("lithium", 1, "closed-basin lacustrine sediments and evaporites: brine affinity")],
    "tN-Q": [("cobalt", 1, "laterite and alterite over mafic-ultramafic ground: "
                           "nickel-cobalt laterite affinity"),
             ("gold", 1, "eluvial cover over the basement: residual placer affinity")],
    "aQ": [("gold", 1, "alluvial and eluvial sediments: placer ground downstream of lode hosts"),
           ("diamond", 1, "alluvial gravels: secondary diamond affinity"),
           ("gemstone", 1, "alluvial gem gravels shed off the gem-bearing metamorphics")],
}


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------
def _get(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": "5mp-geomaps/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def wfs_url(**kw):
    from urllib.parse import urlencode
    p = dict(service="WFS", version="2.0.0", request="GetFeature", typeNames=LAYER)
    p.update(kw)
    return OWS + "?" + urlencode(p)


def hits():
    """The server's own count of the layer, used as the truth to compare against.

    This is the whole anti-truncation contract: the number of features we write
    has to equal a number the SERVER said, not a number we observed.  An
    observed count is exactly what a truncated download also produces.
    """
    xml = _get(OWS + "?service=WFS&version=2.0.0&request=GetFeature&typeNames=%s&resultType=hits" % LAYER)
    root = ET.fromstring(xml)
    n = root.get("numberMatched")
    if n is None or not n.isdigit():
        raise SystemExit("WFS hits gave no numberMatched: %r" % xml[:400])
    return int(n)


def fetch_features(expect, verbose=True):
    """Page through the layer in WGS84 and return the features.

    Paged even though the whole layer is a few MB: a single unpaged request is
    at the mercy of whatever `maxFeatures` the server is configured with, and a
    reply that comes back exactly at the cap looks identical to a complete one.
    Paging plus the `expect` cross-check makes the cap visible instead.
    """
    feats = []
    start = 0
    while len(feats) < expect:
        url = wfs_url(outputFormat="application/json", srsName="EPSG:4326",
                      count=PAGE, startIndex=start)
        blob = _get(url)
        # Parse before trusting: a body cut off mid-array fails here, which is
        # the only cheap way to tell a short read from a small layer.
        try:
            page = json.loads(blob)
        except json.JSONDecodeError as e:
            raise SystemExit("WFS page at startIndex=%d did not parse (%s) - "
                             "%d bytes, almost certainly a truncated read. "
                             "Nothing was written." % (start, e, len(blob)))
        got = page.get("features") or []
        if not got:
            break
        feats.extend(got)
        start += len(got)
        if verbose:
            print("  fetched %d/%d" % (len(feats), expect))
        if len(got) < PAGE:
            break
    if len(feats) != expect:
        raise SystemExit(
            "SHORT DOWNLOAD: %d of %d features (the server's own numberMatched). "
            "This is the failure that shipped half of Sudan once already "
            "(scripts/histmaps/README.md) - refusing to write a partial sheet."
            % (len(feats), expect))
    return feats


def fetch_native_and_reproject(expect, verbose=True):
    """Fallback path: download in the layer's native EPSG:21036 and let ogr2ogr
    reproject.

    Kept because the WFS's own reprojection is a black box we do not control:
    if a future GeoServer upgrade starts handing back lat/lon axis order, or a
    different datum shift, the envelope check below fires and this is the way
    out that does not involve trusting the same black box twice.  Arc 1960 ->
    WGS84 is a real datum shift (~200 m), so it must be done by something that
    knows the TOWGS84 parameters, never by swapping numbers by hand.
    """
    os.makedirs(SRC_DIR, exist_ok=True)
    native = os.path.join(SRC_DIR, "tanzania_minerogenicgeology_21036.json")
    url = wfs_url(outputFormat="application/json")
    blob = _get(url)
    doc = json.loads(blob)
    if len(doc.get("features") or []) != expect:
        raise SystemExit("SHORT DOWNLOAD (native): %d of %d features"
                         % (len(doc.get("features") or []), expect))
    with open(native, "w") as fh:
        json.dump(doc, fh)
    out = native.replace("_21036.json", "_4326.json")
    if os.path.exists(out):
        os.remove(out)
    subprocess.run(["ogr2ogr", "-f", "GeoJSON", "-s_srs", "EPSG:21036",
                    "-t_srs", "EPSG:4326", out, native], check=True)
    with open(out) as fh:
        return json.load(fh)["features"]


def check_envelope(feats):
    """Every vertex must be inside Tanzania.  Not a filter - an assertion.

    WFS 1.0.0 returns lat/lon for EPSG:4326 (the axis-order trap), which puts
    Tanzania in the Indian Ocean somewhere off Somalia and still renders as a
    perfectly plausible map, just in the wrong place.  A degrees-vs-metres
    mix-up is the other one.  Both are caught here, and the answer is to stop.
    """
    x0, y0, x1, y1 = TZ_BOX
    minx = miny = 1e18
    maxx = maxy = -1e18
    for f in feats:
        g = f.get("geometry") or {}
        stack = [g.get("coordinates")]
        while stack:
            c = stack.pop()
            if c is None:
                continue
            if c and isinstance(c[0], (int, float)):
                minx, maxx = min(minx, c[0]), max(maxx, c[0])
                miny, maxy = min(miny, c[1]), max(maxy, c[1])
            else:
                stack.extend(c)
    if not (x0 <= minx and maxx <= x1 and y0 <= miny and maxy <= y1):
        raise SystemExit(
            "COORDINATES ARE NOT IN TANZANIA: bbox %.4f,%.4f .. %.4f,%.4f, expected "
            "within %s. Either the WFS returned lat/lon axis order, or the "
            "reprojection failed. Re-run with --reproject-local." %
            (minx, miny, maxx, maxy, TZ_BOX))
    return (minx, miny, maxx, maxy)


# ---------------------------------------------------------------------------
# the publisher's own ink
# ---------------------------------------------------------------------------
def fetch_sld_colors():
    """leg_id -> the fill colour the GST's own GeoServer style paints it.

    The other two sheets' `color` is measured off the printed swatch; here the
    publisher hands us the ink directly, so we take it and do not invent one.
    If a class has no rule in the SLD we leave `color` absent rather than
    substituting a plausible grey: the catalogue's `color` means "this is how
    the survey prints it", and a made-up value would quietly become that claim.
    (The map does not depend on it - colour on screen is ICS age; `color` is
    only used by the "as printed" mode and the GeoPackage's ink_color.)
    """
    xml = _get(OWS + "?service=WMS&version=1.1.1&request=GetStyles&layers=" + LAYER)
    ns = {"sld": "http://www.opengis.net/sld", "ogc": "http://www.opengis.net/ogc"}
    root = ET.fromstring(xml)
    out = {}
    for rule in root.findall(".//sld:Rule", ns):
        lit = rule.find(".//ogc:Literal", ns)
        if lit is None or not (lit.text or "").strip().isdigit():
            continue
        for css in rule.findall(".//sld:PolygonSymbolizer/sld:Fill/sld:CssParameter", ns):
            if css.get("name") == "fill" and css.text:
                out[int(lit.text)] = css.text.strip().lower()
                break
    if not out:
        raise SystemExit("GetStyles returned no per-class fill colours; refusing to "
                         "invent inks (see the docstring).")
    return out


# ---------------------------------------------------------------------------
# age
# ---------------------------------------------------------------------------
# ICS period/era boundaries in Ma, used ONLY for the Cenozoic units, which are
# the ones where the survey leaves `chronostra` empty and states the age purely
# as a span in `age_strat` ("23 - 0 Ma").  Turning that span into the period
# name it falls in is a lookup in the same chart srv/geomap_std.go encodes, not
# a guess: the sheet has stated an age, in numbers instead of words.  Every
# class also carries the sheet's own `age_ma` string verbatim, so the
# derivation is checkable and nothing is hidden behind it.
ICS_SPANS = [
    ("Quaternary", 2.58, 0.0),
    ("Neogene", 23.03, 2.58),
    ("Paleogene", 66.0, 23.03),
    ("Cretaceous", 145.0, 66.0),
    ("Jurassic", 201.4, 145.0),
    ("Triassic", 251.9, 201.4),
    ("Permian", 298.9, 251.9),
    ("Carboniferous", 358.9, 298.9),
]


def group_from_age_strat(age_strat):
    """"23 - 0 Ma" -> "Neogene - Quaternary".  None if it cannot be read."""
    if not age_strat:
        return None
    txt = age_strat.replace("Ma", "").replace(",", "").strip()
    parts = [p.strip() for p in txt.split("-")]
    try:
        old, young = float(parts[0]), float(parts[1])
    except (ValueError, IndexError):
        return None
    # A tolerance, because the survey rounds its own boundaries: "2.6 - 0 Ma"
    # is the Quaternary (base 2.58 Ma), and a bare interval test would also
    # claim 0.02 Ma of Neogene and print "Neogene - Quaternary" for alluvium.
    tol = 0.02 * max(1.0, old - young)
    names = [n for n, a, b in ICS_SPANS if old > b + tol and young < a - tol]
    if not names:
        return None
    # ICS_SPANS is youngest-first; a span reads oldest-first, as the sheet
    # writes it.
    names = list(reversed(names))
    if len(names) == 1:
        return names[0]
    return "%s - %s" % (names[0], names[-1])


def strip_codes(s):
    """Drop the parenthetical ICS sub-era codes from a chronostratigraphy.

    "Neoarchaean (NA) - Neoproterozoic (NP1)" -> "Neoarchaean - Neoproterozoic".

    The `group` is the string srv/geomap_std.go's first-match age scan reads, and
    that scan matches substrings: an interpolated "(NA)" breaks the span term
    "neoarchaean - neoproterozoic" in half and the scan then answers from
    whichever endpoint sits higher in the rule list, which is rule order wearing
    a decision's face.  The codes are not thrown away - every class keeps the
    survey's verbatim string in `chronostrat`.
    """
    out, depth = [], 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return " ".join("".join(out).split()).strip(" -")


# ---------------------------------------------------------------------------
# classes
# ---------------------------------------------------------------------------
def area_km2(geom):
    """Geodesic area on the WGS84 ellipsoid.

    Not a planar area in degrees times a cosine: Tanzania spans 11 degrees of
    latitude, and the point of reporting km2 at all is that the numbers are
    summable against a park or a country total (~885,800 km2 of land).
    """
    from pyproj import Geod
    import shapely.geometry as sg
    geod = Geod(ellps="WGS84")
    a, _ = geod.geometry_area_perimeter(sg.shape(geom))
    return abs(a) / 1e6


def build_classes(feats, inks):
    """One class per legend entry (`leg_id`) of the publisher's own legend.

    No merging happens here and none should: merging on the two scanned sheets
    is a statement about what a PRINT SCREEN cannot separate, measured by the
    hold-out.  This sheet is vector data with the survey's own legend id on
    every polygon, so every class it declares is a class we can draw, and
    `merged` is false for all of them.

    Features with no legend entry are dropped and COUNTED (see `quality`):
    the layer includes water bodies (`remarks: "Water"`, no abbreviation) and
    they are not rock.  Dropping them silently is the shape this codebase keeps
    paying for, so the number is in the catalogue.
    """
    by_leg = {}
    dropped = 0
    for f in feats:
        p = f["properties"]
        if not (p.get("abbr") or "").strip():
            dropped += 1
            continue
        by_leg.setdefault(p["leg_id"], []).append(f)

    classes = []
    for leg_id in sorted(by_leg):
        fs = by_leg[leg_id]
        p = fs[0]["properties"]
        code = p["abbr"].strip()
        chrono = (p.get("chronostra") or "").strip()
        if chrono:
            group = strip_codes(chrono)
        else:
            # The Cenozoic units carry no chronostratigraphic WORD, only the
            # numeric span. That is still the sheet stating an age, so it is
            # read rather than reported as undated - and `age_ma` keeps the
            # sheet's own string beside it.
            group = group_from_age_strat(p.get("age_strat"))
        if not group:
            raise SystemExit(
                "class %s (leg_id %s) has neither chronostra nor a readable "
                "age_strat (%r). An undated class would render grey and read as "
                "a cartographic statement rather than as this bug." %
                (code, leg_id, p.get("age_strat")))
        aff = [dict(commodity=c, weight=w, why=why) for c, w, why in AFFINITY.get(code, [])]
        aff.sort(key=lambda a: (-a["weight"], a["commodity"]))
        cls = dict(
            sheet=SHEET,
            code=code,
            codes=[code],
            name=(p.get("text") or p.get("legendtext") or "").strip(),
            group=group,
            merged=False,
            commodities=[a["commodity"] for a in aff],
            affinity=aff,
            area_km2=round(sum(area_km2(f["geometry"]) for f in fs), 1),
            # --- the survey's own words, kept verbatim ---------------------
            # `group` above is DERIVED (codes stripped, or read off the numeric
            # span). Anyone checking that derivation needs the original, and
            # the lithology column is the input srv/geomap_std.go's FGDC
            # pattern scan reads when the printed name does not name a rock.
            lithology=(p.get("lithology") or "").strip() or None,
            chronostrat=chrono or None,
            age_ma=(p.get("age") or "").strip() or None,
            age_strat=(p.get("age_strat") or "").strip() or None,
            tectonic_unit=(p.get("tectonic_u") or "").strip() or None,
            tectonic_setting=(p.get("tectonic_n") or "").strip() or None,
            stratigraphy=(p.get("stratigrap") or "").strip() or None,
            metamorphism=(p.get("metamorphi") or "").strip() or None,
            leg_id=leg_id,
            n_polygons=len(fs),
        )
        if leg_id in inks:
            cls["color"] = inks[leg_id]
        classes.append(cls)
    return classes, dropped


def write_outputs(feats, classes, dropped, bbox, expect):
    """The two files every sheet writes, in the schema srv/geomap.go reads.

    The tile attribute contract is `code` - that is the only property the
    vector tiles need to carry, because the catalogue says what a code means
    (which is why a legend change never invalidates a tile).
    """
    by_leg = {c["leg_id"]: c for c in classes}
    out_feats = []
    for f in feats:
        p = f["properties"]
        c = by_leg.get(p.get("leg_id"))
        if c is None or not (p.get("abbr") or "").strip():
            continue
        out_feats.append(dict(
            type="Feature",
            properties=dict(
                sheet=SHEET, code=c["code"], codes=c["codes"], name=c["name"],
                group=c["group"], color=c.get("color"), merged=False,
                commodities=c["commodities"], affinity=c["affinity"],
                # The survey's own rock-description column, carried through so
                # the GeoPackage's `lithology` can be derived from it for the
                # units whose NAME is pure geography ("Mafic complex
                # Nyabuyonza"). See geoLithResolveHint for why it is a last
                # resort and not the first thing read.
                lithology=c["lithology"],
                # Per-POLYGON area, not the class total: the GeoPackage sums
                # what is in it, and a class total repeated on 89 alluvium
                # polygons would multiply the country by 89.
                area_km2=round(area_km2(f["geometry"]), 1),
            ),
            geometry=f["geometry"],
        ))
    if not out_feats:
        raise SystemExit("no features survived class assignment - refusing to write "
                         "an empty sheet")
    os.makedirs(OUT_DIR, exist_ok=True)
    gj = os.path.join(OUT_DIR, "%s_units.geojson" % SHEET)
    with open(gj, "w") as fh:
        json.dump(dict(type="FeatureCollection", features=out_feats), fh)

    commodities = {}
    for c in classes:
        for a in c["affinity"]:
            commodities.setdefault(a["commodity"], []).append(
                dict(code=c["code"], weight=a["weight"], why=a["why"],
                     area_km2=c["area_km2"]))
    for v in commodities.values():
        v.sort(key=lambda t: (-t["weight"], -t["area_km2"]))
    groups = list(dict.fromkeys(c["group"] for c in classes))

    # `quality` on the scanned sheets records what the CLASSIFIER measured.
    # There is no classifier here, and saying nothing would let a reader assume
    # the same hold-out numbers apply. So it records what this build actually
    # checked instead: the count the server itself declared, the count written,
    # the envelope, and the non-rock features dropped.
    quality = dict(
        source="vector WFS, not a vectorized scan - no classification step",
        wfs_matched=expect,
        features_written=len(out_feats),
        dropped_non_geology=dropped,
        bbox=[round(v, 4) for v in bbox],
        area_km2_total=round(sum(c["area_km2"] for c in classes), 1),
        fetched=time.strftime("%Y-%m-%d"),
    )
    cat = dict(SHEET_META)
    cat.update(dict(
        n_classes=len(classes), n_units=len(classes),
        quality=quality, groups=groups,
        commodities={k: commodities[k] for k in sorted(commodities)},
        classes=classes,
    ))
    path = os.path.join(OUT_DIR, "%s_classes.json" % SHEET)
    with open(path, "w") as fh:
        json.dump(cat, fh, indent=1)
    return gj, path, quality


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refetch", action="store_true",
                    help="ignore the cached raw download and hit the WFS again")
    ap.add_argument("--reproject-local", action="store_true",
                    help="download the layer in its native EPSG:21036 and reproject "
                         "with ogr2ogr instead of asking the WFS to do it")
    a = ap.parse_args(argv)

    os.makedirs(SRC_DIR, exist_ok=True)
    cache = os.path.join(SRC_DIR, "tanzania_minerogenicgeology_4326.json")

    print("asking the WFS how many features it has...")
    expect = hits()
    print("  numberMatched = %d" % expect)

    feats = None
    if os.path.exists(cache) and not a.refetch:
        try:
            with open(cache) as fh:
                doc = json.load(fh)
            if len(doc.get("features") or []) == expect:
                feats = doc["features"]
                print("using cached download (%d features)" % len(feats))
            else:
                # A cache that disagrees with the server is either a truncated
                # earlier run or an upstream update; either way it is not the
                # sheet, so it is refetched rather than trusted.
                print("cached download has %d of %d features - refetching"
                      % (len(doc.get("features") or []), expect))
        except (json.JSONDecodeError, OSError) as e:
            print("cached download unusable (%s) - refetching" % e)

    if feats is None:
        t0 = time.time()
        if a.reproject_local:
            feats = fetch_native_and_reproject(expect)
        else:
            feats = fetch_features(expect)
        print("fetched %d features in %.1fs" % (len(feats), time.time() - t0))
        with open(cache, "w") as fh:
            json.dump(dict(type="FeatureCollection", features=feats), fh)

    bbox = check_envelope(feats)
    print("bbox %.4f,%.4f .. %.4f,%.4f - inside Tanzania" % bbox)

    inks = fetch_sld_colors()
    print("read %d per-class inks from the GMIS SLD" % len(inks))

    classes, dropped = build_classes(feats, inks)
    missing_ink = [c["code"] for c in classes if "color" not in c]
    if missing_ink:
        print("WARNING: no SLD ink for %s - `color` left absent rather than invented"
              % ", ".join(missing_ink))
    gj, cat, quality = write_outputs(feats, classes, dropped, bbox, expect)
    print("%d classes, %d polygons written (%d non-geology dropped: water bodies)"
          % (len(classes), quality["features_written"], dropped))
    print("mapped area %.0f km2 (Tanzania is ~947,300 km2 incl. inland water)"
          % quality["area_km2_total"])
    print(gj)
    print(cat)
    print("next: scripts/geomaps/tiles.sh %s" % SHEET)
    return 0


if __name__ == "__main__":
    sys.exit(main())
