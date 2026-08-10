#!/usr/bin/env bash
# Vectorized geology sheet -> MBTiles of vector tiles, served by srv/geomap.go.
#
#   scripts/geomaps/tiles.sh sudan
#   scripts/geomaps/tiles.sh            # both sheets
#
# Why vector and not raster (unlike the 1:250k topographic scans in
# scripts/histmaps/): the units are *data*, not a picture.  The UI has to
# recolour them, hide individual ones, isolate a commodity's hosts and set
# opacity, and a raster mosaic can do none of that without a tileset per
# combination.  50 classes is 2^50 combinations; one vector tileset is one
# build.
#
# Every class is kept at every zoom.  --drop-densest-as-needed and friends are
# deliberately NOT used: a formation silently absent at z4 and present at z8
# reads as a rendering bug, and a tileset missing a unit is indistinguishable
# from a sheet that never mapped it.  What gives instead is *geometry* detail
# (--simplification, and coalescing neighbours of the same class), which is
# visible as a coarser outline and never as a missing formation.
set -euo pipefail

cd "$(dirname "$0")/../.."
OUT=data/geomaps

build() {
  local sheet="$1"
  local src="$OUT/${sheet}_units.geojson"
  [ -f "$src" ] || { echo "$src missing - run vectorize.py $sheet first" >&2; return 1; }
  local tmp="$OUT/.${sheet}.mbtiles.tmp"
  rm -f "$tmp"
  # -z10: at 1:1.5M-1:2M the source line work is ~500 m, which z10 already
  # over-resolves; beyond it the client overzooms and shows the same edges
  # bigger, which is honest about the sheet's precision.
  tippecanoe -q -o "$tmp" -l units -n "${sheet} geology" \
    -Z0 -z10 \
    --coalesce --reorder --detect-shared-borders \
    --simplification=4 --no-tiny-polygon-reduction \
    --no-feature-limit --no-tile-size-limit \
    --attribution "$(python3 -c "import json,sys;d=json.load(open('$OUT/${sheet}_classes.json'));print(d['publisher']+', '+str(d['year'])+', '+d['scale'])")" \
    "$src"
  mv -f "$tmp" "$OUT/${sheet}.mbtiles"
  echo "$sheet -> $OUT/${sheet}.mbtiles ($(du -h "$OUT/${sheet}.mbtiles" | cut -f1))"
}

if [ $# -gt 0 ]; then
  for s in "$@"; do build "$s"; done
else
  for s in sudan car; do build "$s"; done
fi
