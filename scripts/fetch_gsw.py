#!/usr/bin/env python3
"""Download JRC Global Surface Water v1.4 `transitions` tiles for a bbox.

Optional accelerator for scripts/gsw_new_water.py, which reads through
/vsicurl when a local tile is absent (the gsw_water.py trade). A local tile
pays off only for the rotation, which re-reads the same four AOI-region tiles
nightly. Tiles land in data/gsw/ (gitignored), named transitions_{lon}_{lat}.tif.

R1: a tile that 404s over land is a FAILURE (exit 1), not a skip — only
all-ocean tiles are unpublished, and this corpus is inland Africa.

    python3 scripts/fetch_gsw.py --bbox 22.7 4.25 31.3 10.97
"""
import argparse
import math
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
URL = ("https://storage.googleapis.com/global-surface-water/downloads2021/"
       "transitions/transitions_{lon}_{lat}v1_4_2021.tif")
MIN_BYTES = 1_000_000  # smallest real land tile observed is ~11 MB


def tiles_for_bbox(x0, y0, x1, y1):
    """GSW tiles are 10x10 deg named by TOP-LEFT corner (see gsw_water.py)."""
    out = set()
    lon = math.floor(x0 / 10.0) * 10
    while lon < x1:
        lat = math.ceil(y1 / 10.0) * 10
        while lat > y0:
            out.add((lon, lat))
            lat -= 10
        lon += 10
    return sorted(out)


def tile_name(lon, lat):
    return (f"{abs(lon)}{'E' if lon >= 0 else 'W'}",
            f"{abs(lat)}{'N' if lat >= 0 else 'S'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("X0", "Y0", "X1", "Y1"))
    args = ap.parse_args()
    dest = BASE_DIR / "data" / "gsw"
    dest.mkdir(parents=True, exist_ok=True)
    tiles = tiles_for_bbox(*args.bbox)
    print(f"{len(tiles)} tiles for bbox {args.bbox}")
    failed = []
    for lon, lat in tiles:
        ln, lt = tile_name(lon, lat)
        out = dest / f"transitions_{ln}_{lt}.tif"
        if out.exists() and out.stat().st_size >= MIN_BYTES:
            print(f"  {out.name}: cached ({out.stat().st_size/1e6:.1f} MB)")
            continue
        url = URL.format(lon=ln, lat=lt)
        try:
            print(f"  {out.name}: downloading …", flush=True)
            urllib.request.urlretrieve(url, out)
            if out.stat().st_size < MIN_BYTES:
                raise IOError(f"only {out.stat().st_size} bytes")
            print(f"  {out.name}: {out.stat().st_size/1e6:.1f} MB")
        except Exception as ex:
            out.unlink(missing_ok=True)
            print(f"  {out.name}: FAILED ({ex})")
            failed.append(out.name)
    if failed:
        print(f"UNFINISHED: {len(failed)}/{len(tiles)} tiles failed: {failed}")
        sys.exit(1)
    print("done")


if __name__ == "__main__":
    main()
