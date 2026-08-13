#!/usr/bin/env python3
"""The reference workings, as ANCHORS a reader can resolve for themselves.

    python3 scripts/mining_anchors.py            # -> data/geology_truth/mining_anchors.geojson

WHAT THIS IS, AND WHY IT IS NOT mining_reference.json
-----------------------------------------------------
`scripts/mining_reference.py` builds the eval's working file: 3,494 sites with
worker counts, armed-actor fields, ADM1 conflict scalars. That file exists to
MEASURE the affinity model and it is not shippable -- half its columns are
somebody else's dataset and one of them (ACLED) is licensed in a way that
forbids putting rows in a public export at all.

This file is the other thing: the smallest honest statement that the model was
checked against real workings, in a form the reader can verify WITHOUT us.
Five fields, and only five:

    lon, lat        where the working is
    year            when it was observed (never "now", never blank-as-zero)
    resource        what it yields, in OUR vocabulary, or NULL for "not recorded"
    source_id       the id the ORIGINAL dataset uses
    source_url      where to resolve that id

Everything else is deliberately dropped. A worker count, an armed-actor flag or
a conflict scalar is the source's research, not our anchor, and shipping it
would be redistributing their dataset with our name on the file.

WHAT SHIPS, AND THE ONE THING THAT DOES NOT
-------------------------------------------
EVERY list we scored ships, because five fields per point is a CITATION, not a
dataset. What makes a mine inventory valuable -- the worker counts, the pit
counts, the armed-actor fields, the environmental scores, the incident prose --
is exactly what is dropped here, and without it these rows cannot substitute
for anybody's product. They can only do the one job they are here for: let a
reader take an id, open the publisher's own record, and confirm the anchor is
real.

So `terms` is recorded per source and travels on every feature, but it is not
a gate on the coordinate:

  open      a licence we can name (ODC-BY, ODbL, CC-BY, public domain)
  unstated  public, no licence document -- shipped with attribution and the
            word `unstated` on every row, so a reader redistributing it
            further knows they must check, and we have not claimed a grant
            nobody made
  restricted terms that forbid rows in a public export. ACLED is the only one,
            and it was never a mining list: it describes our truth sets' REACH.
            It stays in `withheld`, named, so the export can say what it is
            not showing rather than leaving a silence.

A withheld source is NAMED rather than omitted, because "we did not check
there" and "we checked and may not show you" are different statements and an
absence blurs them.

YEAR IS AN OBSERVATION DATE OR IT IS ABSENT. A dataset with no date field gets
NULL, never the publication year of the paper and never the year we fetched it:
"observed in 2019" and "published in 2023" are different claims and a reader
comparing an anchor with a satellite image needs the first one.
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "data" / "eval"
OUT = ROOT / "data" / "geology_truth" / "mining_anchors.geojson"

# The sheets' countries. An anchor outside them is not wrong, it is simply not
# about any ground this map draws, and shipping it would invite the reader to
# check the model somewhere it was never claimed to work.
ENVELOPE = {
    "CAF": (14.0, 2.0, 28.0, 11.5),
    "SSD": (23.0, 3.0, 36.0, 12.5),
    "SDN": (21.0, 8.0, 39.5, 23.5),
    "TZA": (29.0, -12.0, 41.0, 0.5),
}

# Our commodity vocabulary -- the same words the affinity model and the
# GeoPackage's w_<commodity> columns use, so an anchor and a unit can be
# compared without a lookup table.
OSM_COMMODITY = {
    "gold": {"gold", "au", "or"},
    "diamond": {"diamond", "diamonds"},
    "copper": {"copper", "cu"},
    "iron": {"iron", "iron_ore", "fe"},
    "coal": {"coal"},
    "graphite": {"graphite"},
    "uranium": {"uranium"},
    "lithium": {"lithium"},
    "cobalt": {"cobalt"},
}
USGS_COMMODITY = {
    "gold": "gold", "diamond": "diamond", "copper": "copper", "iron": "iron",
    "coal": "coal", "uranium": "uranium", "cobalt": "cobalt",
    "lithium": "lithium", "graphite": "graphite",
    "rare earth": "rare_earth", "rare earths": "rare_earth",
}
# The two Tanzanian lists spell minerals in the survey's own words. Mapped by
# NAME, never by substring: a substring scan reads "Garnet (gem)" correctly by
# luck and "Rare Earth Elements" as nothing. An unmapped term becomes an empty
# resource -- "not one of our commodities", which is honest -- rather than a
# guess.
TZA_COMMODITY = {
    "gold": "gold", "copper": "copper", "diamond": "diamond",
    "uranium": "uranium", "coal": "coal", "graphite": "graphite",
    "iron": "iron", "cobalt": "cobalt", "lithium": "lithium",
    "spodumene": "lithium", "nickel": "cobalt",
    "rare earth elements": "rare_earth", "niobium": "rare_earth",
    "thorium": "rare_earth",
    "sapphire": "gemstone", "ruby": "gemstone", "corundum": "gemstone",
    "emerald": "gemstone", "tsavorite": "gemstone", "tourmaline": "gemstone",
    "garnet (gem)": "gemstone", "opal": "gemstone", "almandine": "gemstone",
    "beryl": "gemstone", "alexandrite": "gemstone", "amethyst": "gemstone",
    "aquamarine": "gemstone", "rhodolite": "gemstone", "spinel": "gemstone",
    "peridot": "gemstone", "diopside": "gemstone", "chrysoprase": "gemstone",
}
ICMM_COMMODITY = {
    "gold": "gold", "copper": "copper", "coal": "coal", "iron": "iron",
    "iron ore": "iron", "diamond": "diamond", "lithium": "lithium",
    "uranium": "uranium", "cobalt": "cobalt", "graphite": "graphite",
    "nickel": "cobalt",
}


def in_scope(lon, lat):
    for iso, (x0, y0, x1, y1) in ENVELOPE.items():
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            return iso
    return None


def year_of(s):
    """The observation year, or None. A string that is not a year is None --
    never a guess and never today."""
    s = (s or "").strip()
    if len(s) >= 4 and s[:4].isdigit():
        y = int(s[:4])
        if 1900 <= y <= 2100:
            return y
    return None


# ---------------------------------------------------------------------------
# Sources. Each returns (meta, sites) or None when its input is absent --
# absent is UNMEASURED and must be distinguishable from "this list has no
# sites here" (invariant 1).
# ---------------------------------------------------------------------------
def src_ipis_caf():
    path = ROOT / "data" / "ipis" / "caf_mines_ipis.csv"
    if not path.exists():
        return None
    meta = {
        "source": "ipis_caf",
        "label": "IPIS artisanal mine visits, Central African Republic",
        "attribution": "International Peace Information Service (IPIS), "
                       "curated open data",
        "licence": "ODC-BY 1.0",
        "terms": "open",
        "landing": "https://ipisresearch.be/mapping-services/open-data/",
        "observed": "field_visit",
        "id_field": "pcode",
    }
    out = []
    for r in csv.DictReader(path.open()):
        try:
            lon, lat = float(r["longitude"]), float(r["latitude"])
        except (TypeError, ValueError):
            continue
        coms = []
        if (r.get("minerals_or") or "").strip() in ("1", "1.0"):
            coms.append("gold")
        if (r.get("minerals_diamant") or "").strip() in ("1", "1.0"):
            coms.append("diamond")
        out.append({
            "lon": lon, "lat": lat,
            "year": year_of(r.get("visit_date")),
            "resource": coms,
            # The site code, not the visit row: 914 rows are repeat visits to
            # 360 sites, and a reader resolving a visit id against IPIS's
            # public map would find nothing.
            "source_id": (r.get("pcode") or "").strip() or None,
        })
    return meta, out


def src_osm():
    files = sorted((EVAL / "osm_mines").glob("raw_*.json"))
    if not files:
        return None
    meta = {
        "source": "osm",
        "label": "OpenStreetMap mine, quarry, adit and mineshaft features",
        "attribution": "\u00a9 OpenStreetMap contributors",
        "licence": "ODbL 1.0",
        "terms": "open",
        "landing": "https://www.openstreetmap.org/copyright",
        "observed": "osm_tag",
        "id_field": "OSM type/id",
    }
    seen, out = set(), []
    for path in files:
        for el in json.loads(path.read_text()).get("elements", []):
            c = el.get("center") or el
            lon, lat = c.get("lon"), c.get("lat")
            if lon is None or lat is None:
                continue
            key = (el["type"], el["id"])
            if key in seen:
                continue
            seen.add(key)
            raw = (el.get("tags") or {})
            txt = (raw.get("resource") or raw.get("mineral")
                   or raw.get("raw_material") or "")
            coms = set()
            for t in txt.split(";"):
                t = t.strip().lower()
                for com, names in OSM_COMMODITY.items():
                    if t in names:
                        coms.add(com)
            out.append({
                "lon": lon, "lat": lat,
                # An OSM timestamp is when the feature was last EDITED, which
                # is the closest thing this source has to an observation and
                # must be labelled as that, not as a mining date.
                "year": year_of(el.get("timestamp")),
                "resource": sorted(coms),
                "source_id": f"{el['type']}/{el['id']}",
            })
    return meta, out


def src_tang_werner():
    path = (EVAL / "tang_werner_2023" /
            "tang_werner_footprint_car_tza_ssd_sdn_centroids.csv")
    if not path.exists():
        return None
    meta = {
        "source": "tang_werner",
        "label": "Tang & Werner (2023) global mining footprints, centroids",
        "attribution": "Tang, L. & Werner, T.T. (2023), Zenodo 6806817",
        "licence": "CC-BY 4.0",
        "terms": "open",
        "landing": "https://doi.org/10.5281/zenodo.6806817",
        "observed": "imagery_footprint",
        "id_field": "OBJECTID",
        # Stated, because the omission is the interesting part: this dataset
        # has no commodity and no date column at all, so every one of its
        # anchors is a working with an unknown mineral. A reader who does not
        # know that reads the NULLs as missing data rather than as the
        # dataset's shape.
        "note": "no commodity and no date field exist in this dataset; "
                "resource and year are NULL for every row, and one mine is "
                "several polygons",
    }
    out = []
    for r in csv.DictReader(path.open()):
        out.append({
            "lon": float(r["lon"]), "lat": float(r["lat"]),
            "year": None, "resource": [],
            "source_id": (r.get("objectid") or "").strip() or None,
        })
    return meta, out


def src_ipis_tza():
    path = EVAL / "ipis_tza" / "ipis_tza_asm_sites.csv"
    if not path.exists():
        return None
    meta = {
        "source": "ipis_tza",
        "label": "IPIS ASM site survey, Tanzania",
        "attribution": "International Peace Information Service (IPIS), "
                       "Tanzania ASM mapping, DGD 2017-2019",
        "licence": "not stated (published as open data)",
        "terms": "unstated",
        "landing": "https://ipisresearch.be/mapping-services/open-data/",
        "observed": "field_visit",
        "id_field": "pcode",
        # Said here because it changes what an anchor MEANS: 131 of these are
        # processing sites, i.e. a mill, whose location says nothing about the
        # rock under it. `observed` carries the distinction per row.
        "note": "includes processing sites as well as workings; sitetype is "
                "carried in `observed` so a mill is not read as a pit",
    }
    out = []
    for r in csv.DictReader(path.open()):
        try:
            lon, lat = float(r["lon"]), float(r["lat"])
        except (TypeError, ValueError):
            continue
        coms = []
        for i in (1, 2, 3):
            c = (r.get(f"mineral{i}") or "").strip().lower()
            if c in TZA_COMMODITY:
                coms.append(TZA_COMMODITY[c])
        kind = (r.get("sitetype") or "").strip().lower()
        out.append({
            "lon": lon, "lat": lat,
            "year": year_of(r.get("visit_date")),
            "resource": sorted(set(coms)),
            "source_id": (r.get("pcode") or "").strip() or None,
            # A processing site is not a working. Keeping it, labelled, is
            # better than dropping it: the reader can exclude it and can see
            # that we did not quietly pad the count.
            "observed": ("processing_site" if kind == "processing site"
                         else "field_visit"),
        })
    return meta, out


def src_gmis_tza():
    path = EVAL / "gmis" / "mines.json"
    if not path.exists():
        return None
    meta = {
        "source": "gmis_tza",
        "label": "Geological Survey of Tanzania occurrence register (GMIS)",
        "attribution": "Geological Survey of Tanzania, Geological and Mineral "
                       "Information System",
        "licence": "not stated (public WFS)",
        "terms": "unstated",
        "landing": "https://gmis-tanzania.com/",
        "observed": "survey_register",
        "id_field": "id (GMIS uuid)",
        # The reason this list cannot corroborate the Tanzania sheet the way
        # an outside survey could, stated where it will be read.
        "note": "the same minerogenic programme that drew the Tanzania sheet "
                "compiled this register, so it is not arm's length; no "
                "observation date exists in the register",
    }
    out = []
    for f in json.loads(path.read_text())["features"]:
        g = f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        lon, lat = g["coordinates"][:2]
        p = f.get("properties") or {}
        com = (p.get("commodity") or "").strip().lower()
        out.append({
            "lon": lon, "lat": lat, "year": None,
            "resource": [TZA_COMMODITY[com]] if com in TZA_COMMODITY else [],
            "source_id": (p.get("id") or "").strip() or None,
        })
    return meta, out


def src_tearline_caf():
    path = EVAL / "tearline" / "tearline_car_mines_points.geojson"
    if not path.exists():
        return None
    meta = {
        "source": "tearline_caf",
        "label": "NGA Tearline CAR mine census (Lobaye Invest permits)",
        "attribution": "NGA Tearline / geoLab, College of William & Mary, "
                       "published 2021-12-16",
        "licence": "not stated",
        "terms": "unstated",
        "landing": "https://www.tearline.mil/public_page/car-mines",
        "observed": "imagery_census",
        "id_field": "mine_id",
        # Both halves matter and both are easy to misread: the frame is a
        # licence boundary, and the mineral was never seen at the pit.
        "note": "an imagery census INSIDE eight mining permits, not a national "
                "survey; the resource is the mineral named in the permit, not "
                "one observed at the working, and no per-mine date exists",
    }
    out = []
    for f in json.loads(path.read_text())["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        p = f.get("properties") or {}
        coms = []
        for t in (p.get("commodity") or "").split(";"):
            t = t.strip().lower()
            if t in ("gold", "diamond"):
                coms.append(t)
        out.append({
            "lon": lon, "lat": lat, "year": None,
            "resource": sorted(set(coms)),
            "source_id": (p.get("mine_id") or "").strip() or None,
        })
    return meta, out


def src_icmm():
    path = EVAL / "icmm" / "icmm_gmd15_car_tza_ssd_sdn.geojson"
    if not path.exists():
        return None
    meta = {
        "source": "icmm",
        "label": "ICMM Global Mining Dataset v1.5",
        "attribution": "International Council on Mining and Metals, "
                       "Global Mining Dataset v1.5",
        "licence": "not stated (public dataset, attribution expected)",
        "terms": "unstated",
        "landing": "https://www.icmm.com/en-gb/research/social-performance/"
                   "2026/global-mining-dataset",
        "observed": "industry_database",
        "id_field": "ICMMID",
        "note": "industrial assets compiled from commercial registries, "
                "coordinates approximate; artisanal mining is absent by "
                "design, and closed mines carry no flag",
    }
    out = []
    for f in json.loads(path.read_text())["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        p = f.get("properties") or {}
        com = (p.get("primary_commodity") or "").strip().lower()
        out.append({
            "lon": lon, "lat": lat, "year": None,
            "resource": [ICMM_COMMODITY[com]] if com in ICMM_COMMODITY else [],
            "source_id": (p.get("icmm_id") or "").strip() or None,
        })
    return meta, out


def src_crisistracker():
    path = EVAL / "crisistracker" / "mine_sites.json"
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    meta = {
        "source": "crisistracker",
        "label": "Mine sites recovered from Crisis Tracker incident reports",
        "attribution": "Incident records: Crisis Tracker, a project of "
                       "Invisible Children. The mine labelling and clustering "
                       "are ours and must not be attributed to them.",
        "licence": "not stated (public map records)",
        "terms": "unstated",
        "landing": "https://crisistracker.org",
        "observed": "community_report",
        "id_field": "incident IRN(s)",
        # The one thing that must travel with these points: every site is here
        # because somebody was attacked and could report it, and the resource
        # is whatever a note happened to name.
        "note": "every site is on this list because an attack there was "
                "reported, so its coverage is attacks and not mining; the "
                "year is the last reported incident, not a survey date. Only "
                "materials a note says the site YIELDS are carried -- looted "
                "goods travel and are not evidence about the rock.",
    }
    out = []
    for s in doc.get("sites", []):
        lon, lat = s.get("lon"), s.get("lat")
        if lon is None or lat is None:
            continue
        # The id a reader can actually resolve is the INCIDENT reference, not
        # our cluster number: ct_003 exists only in our file.
        irns = s.get("incident_irns") or []
        out.append({
            "lon": lon, "lat": lat,
            "year": year_of(s.get("last_incident")),
            "resource": sorted(set(s.get("commodities") or [])),
            "source_id": ";".join(irns) or None,
        })
    return meta, out


def src_ucdp():
    path = EVAL / "ucdp" / "mine_sites.json"
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    meta = {
        "source": "ucdp_ged",
        "label": "Mine sites recovered from UCDP GED event descriptions",
        "attribution": "Event records: UCDP Georeferenced Event Dataset "
                       "(Uppsala Conflict Data Program). The mine labelling "
                       "and clustering are ours and must not be attributed "
                       "to UCDP.",
        "licence": "free to use with citation (Sundberg & Melander 2013); "
                   "no formal licence document",
        "terms": "unstated",
        "landing": "https://ucdp.uu.se",
        "observed": "conflict_report",
        "id_field": "GED event id(s)",
        # Same selection-rule caveat as Crisis Tracker, and the same commodity
        # provenance rule (extracted only, loot dropped).
        "note": "every site is on this list because organised violence there "
                "was recorded, so its coverage is conflict and not mining; "
                "the year is the last recorded event, not a survey date. "
                "Only materials the text says the site YIELDS are carried "
                "-- looted or taxed goods travel and are not evidence about "
                "the rock. Sites whose best GED location precision is an "
                "admin centroid were already excluded upstream "
                "(scripts/ucdp_mines.py).",
    }
    out = []
    for s in doc.get("sites", []):
        lon, lat = s.get("lon"), s.get("lat")
        if lon is None or lat is None:
            continue
        # The id a reader can resolve: GED event ids, searchable on
        # ucdp.uu.se. ged_042 exists only in our file.
        ids = s.get("event_ids") or []
        out.append({
            "lon": lon, "lat": lat,
            "year": year_of(s.get("last_event")),
            "resource": sorted(set(s.get("commodities") or [])),
            "source_id": ";".join(ids) or None,
        })
    return meta, out


def src_usgs():
    path = ROOT / "data" / "geology_truth" / "usgs_africa_deposits.geojson"
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    meta = {
        "source": "usgs_africa",
        "label": "USGS major mineral deposits of Africa",
        "attribution": "USGS OFR 2005-1294-E (Taylor et al. 2009), via the "
                       "JRC Africa Knowledge Platform",
        "licence": doc.get("licence") or "CC BY 4.0",
        "terms": "open",
        "landing": "https://pubs.usgs.gov/of/2005/1294/e/",
        "observed": "named_deposit",
        "id_field": "rec_id",
        "note": "industrial-scale NAMED deposits, a different kind of claim "
                "from a visited artisanal pit; no observation date",
    }
    out = []
    for f in doc.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        lon, lat = g["coordinates"][:2]
        p = f.get("properties") or {}
        com = (p.get("commodity") or "").strip().lower()
        out.append({
            "lon": lon, "lat": lat, "year": None,
            "resource": [USGS_COMMODITY[com]] if com in USGS_COMMODITY else [],
            "source_id": str(p.get("rec_id") or "") or None,
        })
    return meta, out


# ---------------------------------------------------------------------------
# WITHHELD, AND SAID OUT LOUD.
#
# One entry, and it is not a mining list. ACLED enters this project as a
# description of our own truth sets' REACH (docs/agents/acled.md); its Content
# Usage Terms forbid ACLED rows in a public export, and we would not ship them
# anyway because a conflict count is not evidence that a place is mined.
#
# It is NAMED rather than omitted so the export can state what the coverage
# analysis rested on. Anything else that cannot be built is reported as
# UNMEASURED by the main loop, which is a different fact again.
# ---------------------------------------------------------------------------
def withheld():
    return [
        {"source": "acled",
         "label": "ACLED conflict events (coverage context only)",
         "terms": "restricted",
         "why": "ACLED's Content Usage Terms forbid ACLED rows in a public "
                "export. It was never a mining list: it describes how far our "
                "truth sets could reach, and a conflict count is not evidence "
                "that a place is mined.",
         "landing": "https://acleddata.com/contentusage",
         "sites": None},
    ]


SOURCES = [src_ipis_caf, src_ipis_tza, src_gmis_tza, src_tearline_caf,
           src_icmm, src_crisistracker, src_ucdp, src_osm, src_tang_werner,
           src_usgs]


def main():
    feats, per_source, absent = [], [], []
    for fn in SOURCES:
        got = fn()
        name = fn.__name__.replace("src_", "")
        if got is None:
            absent.append(name)
            print(f"  {name:14} UNMEASURED (input absent)")
            continue
        meta, sites = got
        # The only refusal. `unstated` ships with the word on every row; a
        # source whose terms actually forbid redistribution must never reach
        # SOURCES, and this is the assertion that says so out loud rather than
        # letting a future edit slip one in.
        if meta["terms"] == "restricted":
            sys.exit(f"{meta['source']} is in SOURCES but its terms are "
                     "'restricted' -- it belongs in withheld()")
        if meta["terms"] not in ("open", "unstated"):
            sys.exit(f"{meta['source']}: unknown terms {meta['terms']!r}")
        kept = 0
        for s in sites:
            iso = in_scope(s["lon"], s["lat"])
            if not iso:
                continue
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [round(s["lon"], 6),
                                             round(s["lat"], 6)]},
                "properties": {
                    "source": meta["source"],
                    "source_id": s["source_id"],
                    "source_url": meta["landing"],
                    "iso3": iso,
                    "year": s["year"],
                    # One resource string, comma-joined, in our vocabulary.
                    # NULL means the source did not record one -- never "none".
                    "resource": ",".join(s["resource"]) or None,
                    # How the site was seen. Per ROW, not per source: IPIS
                    # Tanzania holds workings and mills in one file and the
                    # difference decides whether the point says anything about
                    # the rock under it.
                    "observed": s.get("observed") or meta["observed"],
                    "licence": meta["licence"],
                    # ON EVERY ROW, not only in the header: a reader who
                    # selects one source out of the layer and passes it on
                    # must carry the fact that nobody granted a licence.
                    "terms": meta["terms"],
                    "attribution": meta["attribution"],
                },
            })
            kept += 1
        if not kept:
            sys.exit(f"{meta['source']} produced 0 anchors in scope from "
                     f"{len(sites)} sites -- a filter that matched nothing is "
                     "not an answer (invariant 1)")
        per_source.append(dict(meta, sites=kept,
                               with_resource=sum(
                                   1 for f in feats
                                   if f["properties"]["source"] == meta["source"]
                                   and f["properties"]["resource"]),
                               with_year=sum(
                                   1 for f in feats
                                   if f["properties"]["source"] == meta["source"]
                                   and f["properties"]["year"])))
        print(f"  {meta['source']:14} {kept:>5} anchors in scope "
              f"({len(sites) - kept} outside the sheets' countries)")

    if not feats:
        sys.exit("no anchors at all -- every input is missing")

    doc = {
        "type": "FeatureCollection",
        "name": "mining_anchors",
        "generated_by": "scripts/mining_anchors.py",
        "purpose": "The independently-published workings the geology affinity "
                   "model was scored against, reduced to what a reader needs "
                   "to resolve each one: coordinate, observation year, "
                   "resource, the original dataset's own id, and where to "
                   "look it up. Not a mining inventory and not our data.",
        "notice": "These are OTHER ORGANISATIONS' observations, cited here "
                  "under the attribution and terms named on each feature -- "
                  "five fields per point, which is a citation and not a "
                  "substitute for anybody's dataset. `terms` = 'unstated' "
                  "means the publisher granted no licence: attribute them, "
                  "and check before redistributing further. They are evidence "
                  "that the affinity layer was checked against reality; they "
                  "are not a prediction, a ranking or a claim that any other "
                  "ground is barren.",
        "sources": per_source,
        "unmeasured_sources": absent,
        "withheld": withheld(),
        "totals": {
            "anchors": len(feats),
            "by_source": dict(Counter(f["properties"]["source"] for f in feats)),
            "by_country": dict(Counter(f["properties"]["iso3"] for f in feats)),
            "with_resource": sum(1 for f in feats
                                 if f["properties"]["resource"]),
            "with_year": sum(1 for f in feats if f["properties"]["year"]),
        },
        "features": feats,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1))
    t = doc["totals"]
    print(f"\n  {OUT}: {t['anchors']} anchors, {t['with_resource']} name a "
          f"resource, {t['with_year']} carry an observation year")
    print("  withheld: " + ", ".join(
        f"{w['source']} ({w['terms']})" for w in doc["withheld"]))


if __name__ == "__main__":
    main()
