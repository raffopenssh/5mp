#!/bin/bash
# One-shot: georeference the full 195-cell selection (AOI-first) and re-mosaic.
#
# Split in two on purpose: the mosaic must not start until every sheet that will
# ever be in it exists, because step 2 caches per-block tiles. runall.sh is
# resumable, so if this is interrupted, re-run it -- already-warped sheets are
# skipped and the mosaic picks up whatever is on disk at that moment.
set -u
cd "$(dirname "$0")"
D=/home/exedev/5mp/data/histmaps
date -u +'START %F %T' >> "$D/night.log"
JOBS=${JOBS:-3} ./runall.sh

# A failure list from one run mixes real defects with network noise: the first
# 195-sheet run lost cs000643 to a curl reset and cs000694 to a truncated JP2,
# both of which succeed on a second attempt. Retry before tiling, because a
# sheet missing at mosaic time is a hole in the product that nothing later
# notices. Two extra passes; anything still failing is a real defect (see the
# 45-M Eilai case in README) and is listed for the record.
for pass in 1 2; do
  MISSING=$(python3 - <<'PY'
import json, glob, os
sel = json.load(open('selection.json'))['selected']
have = {os.path.basename(p).split('_')[0]
        for p in glob.glob('/home/exedev/5mp/data/histmaps/geo/*_geo.tif')}
print(','.join(r['id'] for r in sel if r['id'] not in have))
PY
)
  [ -z "$MISSING" ] && break
  echo "retry pass $pass: $(echo $MISSING | tr ',' '\n' | wc -l) sheets" >> "$D/night.log"
  rm -f ./*.jp2
  python3 -u sudan250k.py all --ids "$MISSING" --method tps --jobs 1 --resume \
    >> "$D/run_all.log" 2>&1
  mv cs*_geo.tif cs*_geo.tif.points "$D/geo"/ 2>/dev/null
done
rm -f ./*.jp2

date -u +'GEO_DONE %F %T' >> "$D/night.log"
echo "sheets: $(ls $D/geo/*_geo.tif | wc -l)" >> "$D/night.log"
./mosaic.sh >> "$D/mosaic.log" 2>&1
date -u +'MOSAIC_DONE %F %T' >> "$D/night.log"
