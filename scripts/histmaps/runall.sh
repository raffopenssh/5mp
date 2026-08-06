#!/bin/bash
# Fetch + georeference ONE scan per 1:250k sheet cell, chosen by select.py
# (most detailed edition, preferring 1924-1936). 76 sheets, not 254 -- see
# select.py for why the other 178 are near-duplicate editions of the same cells.
#
#   python3 select.py            # (re)build selection.json, ~7 min
#   ./runall.sh                  # georeference the selection
#
# Source JP2s are deleted after warping (see --keep-jp2); outputs land in
# data/histmaps/geo. Safe to re-run: --resume skips sheets already written.
set -u
cd "$(dirname "$0")"
OUT=/home/exedev/5mp/data/histmaps/geo
LOG=/home/exedev/5mp/data/histmaps/run_all.log
SEL=${SEL:-$PWD/selection.json}
JOBS=${JOBS:-2}          # 2 cores; potrace+warp are single-threaded per sheet
mkdir -p "$OUT"

if [ ! -e "$SEL" ]; then
  echo "no $SEL -- run: python3 select.py" >&2; exit 1
fi

# Sweep finished sheets into OUT *while* the run proceeds, not just at the end:
# a 264-sheet run is many hours and a crash at sheet 200 should not strand 199
# outputs in the source tree. Only move a .tif once its sidecar .points exists
# (written after the warp returns), so we never grab a file gdalwarp is still
# writing.
( while :; do
    sleep 60
    for t in cs*_geo.tif; do
      [ -e "$t" ] || continue
      [ -e "$t.points" ] || continue
      mv "$t" "$t.points" "$OUT"/ 2>/dev/null
      [ -e "${t%.tif}.svg" ] && mv "${t%.tif}.svg" "$OUT"/ 2>/dev/null
    done
  done ) &
SWEEP=$!
trap 'kill $SWEEP 2>/dev/null' EXIT

python3 -u sudan250k.py all --ids "@$SEL" --method tps --jobs "$JOBS" --resume >> "$LOG" 2>&1
RC=$?

kill $SWEEP 2>/dev/null
mv cs*_geo.tif cs*_geo.tif.points cs*_geo.svg "$OUT"/ 2>/dev/null
cp qa.json /home/exedev/5mp/data/histmaps/ 2>/dev/null
echo "ALL_DONE rc=$RC  $(ls "$OUT"/*_geo.tif 2>/dev/null | wc -l) sheets" >> "$LOG"
