#!/usr/bin/env python3
"""Fetch the two JRC AKP structural layers the eval said to ingest.

    scripts/geomaps/fetch_akp.py

Writes data/akp/active_faults.geojson and data/akp/craton_edges.geojson,
both COMMITTED: they are small (<1 MB together), openly licensed, and the
server reads them at startup (srv/geomap_structural.go).

WHY THESE TWO AND NOTHING ELSE

docs/agents/overlays.md "Other geology data, weighed" measured the whole AKP
catalogue against IPIS's 7,163 DRC visits (eval_affinity.py --continental):

  * akp:active_faults (406 lines, Macgregor 2014): 1.6-2.7x on gold /
    cassiterite / coltan at 25 km — the single most valuable new layer.
  * akp:cratons (9 polygons): the EDGE scores 2.9x on gold; the interior
    covers 60% of the hull and scores ~1.0, i.e. means nothing. So what is
    written here is the boundary as linework, one feature per craton, and the
    polygons are deliberately not shipped — a filled craton layer would claim
    the interior.
  * LithoMap_Africa: weaker than our sheets — skipped.
  * gem_active_faults: California/Europe — already discarded by the eval.

INVARIANT 1: the WFS answering with fewer features than the layer is known to
hold is an unfinished fetch, not a smaller collection; this script then exits
non-zero and writes nothing.
"""
import datetime
import json
import os
import subprocess
import sys

WFS = ("https://africa-knowledge-platform.ec.europa.eu/geoserver/akp/wfs"
       "?service=WFS&version=2.0.0&outputFormat=application/json"
       "&request=GetFeature&typeNames=akp:")
OUT_DIR = "data/akp"

# The layer is known to hold at least this many features (counted 2026-08-13).
# `>=` not `==`: the JRC may add a fault; it will not silently drop 200.
MIN_FAULTS = 406
MIN_CRATONS = 9

ATTRIB = {
    "source": "JRC Africa Knowledge Platform (akp GeoServer WFS)",
    "citation": ("Macgregor, D. (2014) history of the development of the "
                 "East African Rift System — active faults; craton outlines "
                 "as compiled by the EC JRC Africa Knowledge Platform"),
    "terms": ("European Commission reuse policy (Decision 2011/833/EU), "
              "reuse permitted with attribution"),
    "landing": "https://africa-knowledge-platform.ec.europa.eu/",
}


def fetch(layer):
    raw = subprocess.check_output(
        ["curl", "-fsS", "--compressed", "--max-time", "300", WFS + layer])
    return json.loads(raw)["features"]


def write(path, features, notice):
    fc = {
        "type": "FeatureCollection",
        # R7: attribution rides in the committed artefact itself.
        **ATTRIB,
        "accessed": datetime.date.today().isoformat(),
        "notice": notice,
        "features": features,
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(fc, f)
    os.replace(tmp, path)
    print(f"  {path}: {len(features)} features, "
          f"{os.path.getsize(path) / 1024:.0f} KB")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    faults = fetch("active_faults")
    if len(faults) < MIN_FAULTS:
        sys.exit(f"UNFINISHED: akp:active_faults returned {len(faults)} "
                 f"features, expected >= {MIN_FAULTS}; nothing written")
    slim = []
    for f in faults:
        p = f.get("properties") or {}
        slim.append({"type": "Feature", "geometry": f["geometry"],
                     "properties": {"type": p.get("Type"),
                                    "reference": p.get("REFERENCE")}})
    write(os.path.join(OUT_DIR, "active_faults.geojson"), slim,
          "Active fault traces (Macgregor 2014). Structural context only: "
          "proximity to a fault is an inference about setting, not a record "
          "of any deposit.")

    cratons = fetch("cratons")
    if len(cratons) < MIN_CRATONS:
        sys.exit(f"UNFINISHED: akp:cratons returned {len(cratons)} features, "
                 f"expected >= {MIN_CRATONS}; nothing written")
    # The EDGE, not the polygon: the interior covers 60% of the measured hull
    # and scores ~1.0 — shipping the fill would claim ground the measurement
    # says nothing about. shapely turns each (multi)polygon boundary into
    # linework; one feature per craton so the name survives.
    from shapely.geometry import shape, mapping
    edges = []
    for f in cratons:
        geom = shape(f["geometry"]).boundary
        edges.append({"type": "Feature", "geometry": mapping(geom),
                      "properties": {"name": (f.get("properties") or {}).get("Name"),
                                     "source": (f.get("properties") or {}).get("Source")}})
    write(os.path.join(OUT_DIR, "craton_edges.geojson"), edges,
          "Craton margins, derived as the boundary linework of the AKP craton "
          "polygons. The interiors are deliberately not shipped: only the edge "
          "has measured skill (2.9x on DRC gold visits at 25 km); 'on a craton' "
          "is true of 60% of the ground and means nothing.")


if __name__ == "__main__":
    main()
