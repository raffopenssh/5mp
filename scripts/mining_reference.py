#!/usr/bin/env python3
"""Build the unified mining reference for the geology affinity eval, and label
every site with the ADM1 conflict context it sits in.

THE POINT OF THIS SCRIPT
------------------------
`scripts/geomaps/eval_affinity.py` scores our lithology-based mining-affinity
model against occurrence lists. Every one of those lists is reachability-limited
in a different way (IPIS: a surveyor's road; Tearline: a licence boundary; OSM:
one mapper's campaign; GST/GMIS: the survey's own programme). If the ground a
list could not reach is systematically different ground, then a good score can
mean "the model found the roads" rather than "the model found the rocks".

So each site gets the conflict intensity of its ADM1 attached, and the eval can
report a score together with the exposure of the truth set that produced it.
Conflict is used here ONLY as a property of our own coverage. It is never
evidence that a place is mined, and it is never a layer we serve.

Sources merged (all four countries; a country with no list for a source is
reported UNMEASURED, never silently skipped):

  ipis_caf     IPIS artisanal-mine field visits, CAR (914) -- commodity observed
               on site, plus armed-actor presence AT the mine
  ipis_tza     IPIS ASM site survey, Tanzania (447) -- same survey family
  gmis_tza     Geological Survey of Tanzania occurrence register (480)
  tearline_caf NGA Tearline 2021 imagery census inside Lobaye Invest permits (40)
  osm          OSM mine/quarry tags across the Sudan/South Sudan envelope
  tang_werner  Tang & Werner (2023) satellite mine footprints (197 in our four)
  icmm         ICMM/S&P global mine database subset (11)

Inputs:  data/eval/{ipis_tza,gmis,tearline,osm_mines,tang_werner_2023,icmm}/...
         data/ipis/caf_mines_ipis.csv
         data/eval/adm1/<ISO3>_adm1.geojson       (scripts/fetch_adm1.py)
         data/eval/acled/adm1_conflict.json       (scripts/acled_adm1.py)
Output:  data/eval/mining_reference.json          (sites + adm1 rollup)

Usage: python3 scripts/mining_reference.py
"""
import csv
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "data" / "eval"
OUT = EVAL / "mining_reference.json"

ISOS = ["CAF", "SSD", "SDN", "TZA"]
ENVELOPE = {  # generous per-country sanity boxes; an axis swap must not pass
    "CAF": (14.0, 2.0, 28.0, 11.5),
    "SSD": (23.0, 3.0, 36.0, 12.5),
    "SDN": (21.0, 8.0, 39.5, 23.5),
    "TZA": (29.0, -12.0, 41.0, 0.5),
}

OSM_COMMODITY = {
    "gold": {"gold", "au", "or"},
    "diamond": {"diamond", "diamonds"},
    "copper": {"copper", "cu"},
    "iron": {"iron", "iron_ore", "fe"},
    "chromium": {"chromite", "chromium"},
    "manganese": {"manganese"},
}


# geoBoundaries and ACLED disagree on some ADM1 names -- Swahili vs English on
# Zanzibar, and Gezira vs Al Jazirah. Folding accents is not enough, so the
# remaining pairs are named here. Keyed geoBoundaries-name -> ACLED-name; an
# unmatched name is reported, never silently dropped.
ADM1_ALIAS = {
    ("SDN", "gezira"): "Al Jazirah",
    ("SDN", "abyei pca"): "Abyei",
    ("TZA", "zanzibar north"): "Kaskazini Unguja",
    ("TZA", "zanzibar south & central"): "Kusini Unguja",
    ("TZA", "zanzibar urban/west"): "Mjini Magharibi",
    ("TZA", "north pemba"): "Kaskazini Pemba",
    ("TZA", "south pemba"): "Kusini Pemba",
}


def fold(s):
    s = unicodedata.normalize("NFKD", (s or "").strip())
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


# --------------------------------------------------------------------------
# ADM1 geometry: which admin unit is a point in?
# --------------------------------------------------------------------------
class Admin:
    def __init__(self):
        self.by_iso = {}
        for iso in ISOS:
            path = EVAL / "adm1" / f"{iso}_adm1.geojson"
            if not path.exists():
                raise SystemExit(f"missing {path} -- run scripts/fetch_adm1.py")
            feats = json.loads(path.read_text())["features"]
            geoms = [shape(f["geometry"]) for f in feats]
            names = [f["properties"]["shapeName"] for f in feats]
            self.by_iso[iso] = (STRtree(geoms), geoms, names)

    def locate(self, iso, lon, lat):
        tree, geoms, names = self.by_iso[iso]
        p = Point(lon, lat)
        for i in tree.query(p):
            if geoms[i].covers(p):
                return names[i]
        # near-miss: coastline/border generalisation, snap to the nearest unit
        # within ~10 km rather than dropping a real site on a rounding error
        best, bestd = None, None
        for i in tree.query(p.buffer(0.1)):
            d = geoms[i].distance(p)
            if bestd is None or d < bestd:
                best, bestd = names[i], d
        return best


def country_of(lon, lat):
    """Which of our four countries does this point fall in (by envelope)?"""
    hits = [iso for iso, (x0, y0, x1, y1) in ENVELOPE.items()
            if x0 <= lon <= x1 and y0 <= lat <= y1]
    return hits[0] if len(hits) == 1 else (hits if hits else None)


# --------------------------------------------------------------------------
# Sources. Each returns a list of site dicts, or None if the input is absent
# (absent != empty: UNMEASURED must be distinguishable from "no sites here").
# --------------------------------------------------------------------------
def src_ipis_caf():
    path = ROOT / "data" / "ipis" / "caf_mines_ipis.csv"
    if not path.exists():
        return None
    out = []
    for r in csv.DictReader(path.open()):
        try:
            lon, lat = float(r["longitude"]), float(r["latitude"])
        except (TypeError, ValueError):
            continue
        coms = []
        if r.get("minerals_or") == "1":
            coms.append("gold")
        if r.get("minerals_diamant") == "1":
            coms.append("diamond")
        # Armed presence recorded AT the site, by the surveyor who was standing
        # in it. This is mining-specific and independent of ACLED.
        actor = (r.get("actor_type") or "").strip()
        out.append({
            "source": "ipis_caf", "iso3": "CAF",
            "lon": lon, "lat": lat,
            "commodities": coms,
            "observed": "field_visit",
            "site_armed_actor": actor if actor not in ("", "0") else None,
            "site_actor_name": (r.get("actor1name") or "").strip() or None,
            "site_conflict": (r.get("conflict_category") or "").strip() or None,
            "roadblock": r.get("roadblocks") == "1",
            "workers": int(r["workers_numb"]) if (r.get("workers_numb") or
                                                  "").isdigit() else None,
            "reported_admin1": (r.get("prefecture") or "").strip() or None,
        })
    return out


def src_ipis_tza():
    path = EVAL / "ipis_tza" / "ipis_tza_asm_sites.csv"
    if not path.exists():
        return None
    out = []
    for r in csv.DictReader(path.open()):
        try:
            lon, lat = float(r["lon"]), float(r["lat"])
        except (TypeError, ValueError):
            continue
        coms = [(r.get(f"mineral{i}") or "").strip().lower()
                for i in (1, 2, 3)]
        out.append({
            "source": "ipis_tza", "iso3": "TZA",
            "lon": lon, "lat": lat,
            "commodities": [c for c in coms if c],
            "observed": "field_visit",
            "site_conflict": (r.get("incidents") or "").strip() or None,
            "workers": int(r["workers"]) if (r.get("workers") or
                                             "").isdigit() else None,
            "reported_admin1": (r.get("region") or "").strip() or None,
        })
    return out


def src_gmis_tza():
    path = EVAL / "gmis" / "mines.json"
    if not path.exists():
        return None
    out = []
    for f in json.loads(path.read_text())["features"]:
        g = f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        lon, lat = g["coordinates"][:2]
        com = (f["properties"].get("commodity") or "").strip().lower()
        out.append({
            "source": "gmis_tza", "iso3": "TZA", "lon": lon, "lat": lat,
            "commodities": [com] if com else [],
            "observed": "survey_register",
            "reported_admin1": None,
        })
    return out


def src_tearline_caf():
    path = EVAL / "tearline" / "tearline_car_mines_points.geojson"
    if not path.exists():
        return None
    out = []
    for f in json.loads(path.read_text())["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        out.append({
            "source": "tearline_caf", "iso3": "CAF", "lon": lon, "lat": lat,
            # commodity is inherited from the permit, not seen at the pit
            "commodities": ["gold"], "observed": "imagery_census",
            "reported_admin1": None,
            "permit": f["properties"].get("permit"),
        })
    return out


def src_osm():
    files = sorted((EVAL / "osm_mines").glob("raw_*.json"))
    if not files:
        return None
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
            iso = country_of(lon, lat)
            if not isinstance(iso, str):
                continue
            tags = el.get("tags") or {}
            raw = (tags.get("resource") or tags.get("mineral")
                   or tags.get("raw_material") or "")
            coms = set()
            for t in raw.split(";"):
                t = t.strip().lower()
                for com, names in OSM_COMMODITY.items():
                    if t in names:
                        coms.add(com)
            out.append({
                "source": "osm", "iso3": iso, "lon": lon, "lat": lat,
                "commodities": sorted(coms), "observed": "osm_tag",
                "osm_user": el.get("user"), "reported_admin1": None,
            })
    return out


def src_tang_werner():
    path = (EVAL / "tang_werner_2023" /
            "tang_werner_footprint_car_tza_ssd_sdn_centroids.csv")
    if not path.exists():
        return None
    out = []
    for r in csv.DictReader(path.open()):
        lon, lat = float(r["lon"]), float(r["lat"])
        out.append({
            "source": "tang_werner", "iso3": r["iso3"], "lon": lon, "lat": lat,
            # the dataset carries OBJECTID/Name/Shape_Area only: no commodity
            "commodities": [], "observed": "imagery_footprint",
            "footprint_m2": float(r.get("shape_area_m2_ease") or 0) or None,
            "reported_admin1": None,
        })
    return out


def src_icmm():
    path = EVAL / "icmm" / "icmm_gmd15_car_tza_ssd_sdn.geojson"
    if not path.exists():
        return None
    out = []
    for f in json.loads(path.read_text())["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        p = f["properties"]
        com = (p.get("primary_commodity") or "").strip().lower()
        out.append({
            "source": "icmm", "iso3": p["iso3"], "lon": lon, "lat": lat,
            "commodities": [com] if com else [],
            "observed": "industry_database",
            "confidence": p.get("confidence_factor"),
            "reported_admin1": None,
        })
    return out


SOURCES = [src_ipis_caf, src_ipis_tza, src_gmis_tza, src_tearline_caf,
           src_osm, src_tang_werner, src_icmm]


def main():
    conflict_path = EVAL / "acled" / "adm1_conflict.json"
    if not conflict_path.exists():
        raise SystemExit("missing data/eval/acled/adm1_conflict.json -- run "
                         "scripts/acled_download.py then scripts/acled_adm1.py")
    conflict = json.loads(conflict_path.read_text())
    # ACLED admin1 -> scalars, keyed accent-folded, per ISO3
    iso_of_country = {"Central African Republic": "CAF", "South Sudan": "SSD",
                      "Sudan": "SDN", "Tanzania": "TZA"}
    cmap = defaultdict(dict)
    for cname, c in conflict["countries"].items():
        for name, u in c["adm1"].items():
            cmap[iso_of_country[cname]][u["key"]] = (name, u)

    admin = Admin()
    sites, unmeasured, outside = [], [], []
    for fn in SOURCES:
        name = fn.__name__.replace("src_", "")
        recs = fn()
        if recs is None:
            unmeasured.append(name)
            print(f"  {name:14} UNMEASURED (input file absent)")
            continue
        kept = 0
        for s in recs:
            iso = s["iso3"]
            if iso not in ENVELOPE:
                continue
            x0, y0, x1, y1 = ENVELOPE[iso]
            if not (x0 <= s["lon"] <= x1 and y0 <= s["lat"] <= y1):
                outside.append((name, iso, s["lon"], s["lat"]))
                continue
            s["adm1"] = admin.locate(iso, s["lon"], s["lat"])
            sites.append(s)
            kept += 1
        print(f"  {name:14} {kept:>4} sites in envelope "
              f"({len(recs) - kept} outside our four countries)")

    # An axis swap or a bad clip must abort, not quietly shrink the truth set.
    if outside:
        for row in outside[:5]:
            print(f"  ! {row[0]}: {row[1]} point at {row[2]:.3f},{row[3]:.3f} "
                  f"outside its own country envelope", file=sys.stderr)
        raise SystemExit(f"{len(outside)} sites fall outside the country they "
                         "claim -- check for an axis swap or a bad ISO tag")
    if not sites:
        raise SystemExit("no sites at all -- inputs missing or all filtered")

    # ---- attach conflict context, and roll up per ADM1 -------------------
    unmatched_adm1 = Counter()
    for s in sites:
        key = fold(s["adm1"])
        alias = ADM1_ALIAS.get((s["iso3"], key))
        hit = cmap[s["iso3"]].get(fold(alias) if alias else key)
        if hit:
            _, u = hit
            s["adm1_events"] = u["events"]
            s["adm1_fatalities"] = u["fatalities"]
        else:
            s["adm1_events"] = None      # unmeasured, and it must say so
            s["adm1_fatalities"] = None
            if s["adm1"]:
                unmatched_adm1[(s["iso3"], s["adm1"])] += 1

    rollup = defaultdict(lambda: {"sites": 0, "by_source": Counter(),
                                  "commodities": Counter(),
                                  "sites_with_armed_actor": 0})
    for s in sites:
        r = rollup[(s["iso3"], s["adm1"])]
        r["sites"] += 1
        r["by_source"][s["source"]] += 1
        for c in s["commodities"]:
            r["commodities"][c] += 1
        if s.get("site_armed_actor"):
            r["sites_with_armed_actor"] += 1

    regions = []
    for iso in ISOS:
        for key, (name, u) in sorted(cmap[iso].items()):
            r = rollup.get((iso, name))
            # every ADM1 appears, including the ones with no known mine: a unit
            # nobody surveyed is the whole subject of this file
            regions.append({
                "iso3": iso, "adm1": name,
                "sites": r["sites"] if r else 0,
                "by_source": dict(r["by_source"]) if r else {},
                "commodities": dict(r["commodities"]) if r else {},
                "sites_with_armed_actor": r["sites_with_armed_actor"] if r else 0,
                "acled_events": u["events"],
                "acled_fatalities": u["fatalities"],
                "acled_events_by_year": u["events_by_year"],
            })

    out = {
        "generated_by": "scripts/mining_reference.py",
        "purpose": "mining occurrence reference for the geology affinity eval, "
                   "with per-ADM1 conflict context describing the COVERAGE of "
                   "the reference itself",
        "conflict_context": {
            "source": conflict["source"],
            "accessed": conflict["accessed"],
            "citation": conflict["citation"],
            "terms": conflict["terms"],
            "notice": conflict["notice"],
            "week_from": conflict["week_from"],
            "week_to": conflict["week_to"],
        },
        "adm1_boundaries": "geoBoundaries gbOpen ADM1 (see scripts/fetch_adm1.py)",
        "unmeasured_sources": unmeasured,
        "sites": sites,
        "adm1": regions,
    }
    # derived counts only (invariant 2)
    out["totals"] = {
        "sites": len(sites),
        "by_source": dict(Counter(s["source"] for s in sites)),
        "by_country": dict(Counter(s["iso3"] for s in sites)),
        "with_commodity": sum(1 for s in sites if s["commodities"]),
        "adm1_units": len(regions),
        "adm1_with_sites": sum(1 for r in regions if r["sites"]),
    }
    OUT.write_text(json.dumps(out, indent=1))

    t = out["totals"]
    print(f"\n{t['sites']} sites, {t['with_commodity']} with a commodity; "
          f"{t['adm1_with_sites']}/{t['adm1_units']} ADM1 units hold one")
    for iso in ISOS:
        n = t["by_country"].get(iso, 0)
        units = [r for r in regions if r["iso3"] == iso]
        held = sum(1 for r in units if r["sites"])
        print(f"  {iso}: {n:>4} sites across {held}/{len(units)} ADM1")
    if unmatched_adm1:
        print("\n! ADM1 names with no ACLED match (conflict context unmeasured):")
        for (iso, name), n in unmatched_adm1.most_common():
            print(f"    {iso} {name}: {n} sites")
    print(f"-> {OUT} ({OUT.stat().st_size // 1024} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
