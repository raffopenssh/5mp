#!/usr/bin/env python3
"""Fetch ADM1 polygons for CAF/SSD/SDN/TZA from geoBoundaries (gbOpen).

Why polygons and not the ACLED centroid: the aggregated file carries one lat/lon
per admin1, and a CAR prefecture is ~50,000 km2 -- that point is a label
position, not a location. To ask "which admin1 is this mine in?" we need the
boundary. geoBoundaries gbOpen is ODbL/CC-BY and already the ADM0 source used by
data/eval/tang_werner_2023.

Output: data/eval/adm1/<ISO3>_adm1.geojson  (gitignored; ~MBs of boundary)

Usage: python3 scripts/fetch_adm1.py
"""
import json
import sys
import urllib.request
from pathlib import Path

API = "https://www.geoboundaries.org/api/current/gbOpen/{iso}/ADM1/"
OUT = Path(__file__).resolve().parent.parent / "data" / "eval" / "adm1"
ISOS = ["CAF", "SSD", "SDN", "TZA"]
UA = "5mp-conservation-monitor/1.0"


def get(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for iso in ISOS:
        meta = json.loads(get(API.format(iso=iso)))
        if isinstance(meta, list):
            meta = meta[0]
        url = meta.get("gjDownloadURL")
        if not url:
            raise SystemExit(f"{iso}: no gjDownloadURL in geoBoundaries reply")
        gj = json.loads(get(url, timeout=600))
        feats = gj.get("features") or []
        # A short download reads as a small country, never as a broken fetch
        # (AGENTS.md invariant 1) -- so assert the shape we were promised.
        if len(feats) < 2:
            raise SystemExit(f"{iso}: {len(feats)} ADM1 features -- truncated?")
        names = [f["properties"].get("shapeName") for f in feats]
        if any(n is None for n in names):
            raise SystemExit(f"{iso}: a feature has no shapeName")
        dest = OUT / f"{iso}_adm1.geojson"
        dest.write_text(json.dumps(gj))
        print(f"  {iso}: {len(feats)} ADM1  src={meta.get('boundarySource')}  "
              f"lic={meta.get('boundaryLicense')}  -> {dest.name} "
              f"({dest.stat().st_size // 1024} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
