#!/usr/bin/env python3
"""Build data/geofabrik_countries.json + data/world_countries.geojson.

Inputs (fetched live):
  - Geofabrik index-v1-nogeom.json: region id -> PBF url, tagged with ISO2.
  - Natural Earth 50m admin-0: country polygons with ISO2/ISO3 (the *_EH
    fields, because plain ISO_A2/A3 are -99 for France, Norway, ...).

Outputs:
  - data/geofabrik_countries.json: {iso3: {"region": id, "url": pbf}} for the
    191 countries Geofabrik serves as a single extract. osm_pbf.ensure_pbf
    consults it when the ISO is not in the hand-maintained African GEOFABRIK
    dict (which stays authoritative for the parks: local staging dirs and the
    senegal-and-gambia style groupings live there).
  - data/world_countries.geojson: NE 50m polygons trimmed to {iso3, name},
    used by aoi_runner.aoi_countries() to answer "which countries does this
    AOI polygon touch" anywhere on Earth, not just where we have parks.

Re-run when Geofabrik reshuffles regions (rare). Countries Geofabrik only
serves as part of a multi-country extract (e.g. SEN+GMB) resolve through the
African dict or not at all -- a missing mapping fails loudly in ensure_pbf.
"""
import json, sys, urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
IDX = "https://download.geofabrik.de/index-v1-nogeom.json"
NE = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
      "master/geojson/ne_50m_admin_0_countries.geojson")

def fetch(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)

idx = fetch(IDX)
ne = fetch(NE)

# iso2 -> iso3 (+ trimmed world polygons)
iso2to3, feats = {}, []
for f in ne["features"]:
    p = f["properties"]
    a2 = p.get("ISO_A2_EH") or p.get("ISO_A2")
    a3 = p.get("ISO_A3_EH") or p.get("ADM0_A3")
    if not a3 or a3 == "-99":
        continue
    if a2 and a2 != "-99":
        iso2to3[a2] = a3
    feats.append({"type": "Feature",
                  "properties": {"iso3": a3, "name": p.get("NAME")},
                  "geometry": f["geometry"]})

out = {}
for f in idx["features"]:
    p = f["properties"]
    isos = p.get("iso3166-1:alpha2") or []
    url = (p.get("urls") or {}).get("pbf")
    if len(isos) != 1 or not url:
        continue  # multi-country groupings + continents: not a country unit
    a3 = iso2to3.get(isos[0])
    if a3:
        out[a3] = {"region": p["id"], "url": url}

(BASE / "data" / "geofabrik_countries.json").write_text(
    json.dumps(out, indent=1, sort_keys=True))
(BASE / "data" / "world_countries.geojson").write_text(
    json.dumps({"type": "FeatureCollection", "features": feats}))
print(f"{len(out)} countries mapped, {len(feats)} world polygons", file=sys.stderr)
