#!/bin/bash
# Fetch + georeference every LOC g8310m.gct00289 sheet with a known extent.
# Source JP2s are deleted after warping (see --keep-jp2); outputs land in data/histmaps/geo.
set -u
cd "$(dirname "$0")"
OUT=/home/exedev/5mp/data/histmaps/geo
LOG=/home/exedev/5mp/data/histmaps/run_all.log
mkdir -p "$OUT"
python3 -u sudan250k.py all --all --method tps > "$LOG" 2>&1
mv cs*_geo.tif* "$OUT"/ 2>/dev/null
cp qa.json /home/exedev/5mp/data/histmaps/ 2>/dev/null
echo ALL_DONE >> "$LOG"
