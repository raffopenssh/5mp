#!/bin/bash
# Merge the georeferenced Sudan 1:250k sheets into ONE offline map product:
#   data/histmaps/sudan250k.mbtiles   (z0-14, PNG RGBA, transparent-ink overlay)
#
# Why per-block then merge (rather than one gdal_translate over the whole
# 22x16 degree series): the series is sparse -- 8 of 22 1:1M blocks are present
# -- so tiling the full envelope would walk millions of empty tiles. Each block
# is dense, so it tiles cheaply; the z14 tile tables are then unioned and the
# z0-13 pyramid is built once over the merged file with gdaladdo.
set -u
cd "$(dirname "$0")"
GEO=/home/exedev/5mp/data/histmaps/geo
WORK=/home/exedev/5mp/data/histmaps/work
OUT=/home/exedev/5mp/data/histmaps/sudan250k.mbtiles
LOG=/home/exedev/5mp/data/histmaps/mosaic.log
mkdir -p "$WORK"
export GDAL_CACHEMAX=512

say(){ echo "[$(date +%H:%M:%S)] $*"; }

# 0. wait for any in-flight georeferencing (the 3 retried sheets)
while tmux has-session -t histfix 2>/dev/null; do say "waiting for histfix..."; sleep 60; done

# the retry run (histfix) writes into the script dir and has no sweeper
mv cs*_geo.tif cs*_geo.tif.points "$GEO"/ 2>/dev/null
rm -f cs*.jp2
# qa.json from the retry run only covers those 3 sheets -- merge, don't clobber
python3 - <<'PY'
import json,os
full='/tmp/qa_full_backup.json'; new='/home/exedev/5mp/scripts/histmaps/qa.json'
dst='/home/exedev/5mp/data/histmaps/qa.json'
a=json.load(open(full)) if os.path.exists(full) else {'ok':[],'failed':[]}
b=json.load(open(new))
ok={x['id']:x for x in a['ok']}; ok.update({x['id']:x for x in b['ok']})
fid={x['id'] for x in b['ok']}
fail=[x for x in a['failed']+b['failed'] if x['id'] not in fid]
seen=set(); fail=[x for x in fail if not (x['id'] in seen or seen.add(x['id']))]
json.dump({'ok':sorted(ok.values(),key=lambda x:x['id']),'failed':fail}, open(dst,'w'), indent=1)
print('qa merged: ok',len(ok),'failed',len(fail))
PY
say "sheets: $(ls "$GEO"/*_geo.tif | wc -l)"

# 1. group sheets by 1:1M block via the catalogue
python3 - <<'PY'
import os,sys,json,collections
sys.path.insert(0,'/home/exedev/5mp/scripts/histmaps')
from sudan250k import catalogue
cat={c['id']:c for c in catalogue()}
GEO='/home/exedev/5mp/data/histmaps/geo'; W='/home/exedev/5mp/data/histmaps/work'
g=collections.defaultdict(list)
for f in sorted(os.listdir(GEO)):
    if f.endswith('_geo.tif'):
        g[cat[f[:8]]['sheet'].split('-')[0]].append(os.path.join(GEO,f))
# A block's tile set is a pure function of its sheet list, so write the list and
# drop any cached .mbtiles whose list has changed. Without this, step 2's
# "exists, skip" silently keeps the OLD tiles for a block that has gained sheets
# -- which is exactly how a rerun after fixing the catalogue would appear to
# work while shipping the same partial coverage again.
for b,fs in g.items():
    txt='\n'.join(fs)+'\n'
    p=f'{W}/blk{b}.txt'
    old=open(p).read() if os.path.exists(p) else None
    if old != txt:
        open(p,'w').write(txt)
        for junk in (f'{W}/blk{b}.mbtiles', f'{W}/blk{b}.vrt', f'{W}/blk{b}_3857.vrt'):
            if os.path.exists(junk):
                os.remove(junk); print('invalidated', junk)
# a block that no longer has sheets must not leave a stale list behind
for f in os.listdir(W):
    if f.startswith('blk') and f.endswith('.txt') and f[3:-4] not in g:
        os.remove(f'{W}/{f}'); print('dropped stale', f)
json.dump({b:len(v) for b,v in g.items()}, open(f'{W}/blocks.json','w'))
print('blocks', {b:len(v) for b,v in sorted(g.items())})
PY

# 2. per-block z14 mbtiles
for T in "$WORK"/blk*.txt; do
  B=$(basename "$T" .txt)
  MB="$WORK/$B.mbtiles"
  [ -s "$MB" ] && { say "$B: exists, skip"; continue; }
  say "$B: vrt"
  gdalbuildvrt -input_file_list "$T" "$WORK/$B.vrt" -resolution highest -q
  gdalwarp -t_srs EPSG:3857 -r cubic -of VRT -overwrite -q "$WORK/$B.vrt" "$WORK/${B}_3857.vrt"
  say "$B: tiling z14"
  gdal_translate -of MBTILES "$WORK/${B}_3857.vrt" "$MB.part" \
     -co TILE_FORMAT=PNG -co ZOOM_LEVEL_STRATEGY=UPPER -co RESAMPLING=CUBIC -q
  mv "$MB.part" "$MB"
  say "$B: $(sqlite3 "$MB" 'select count(*) from tiles') tiles, $(du -h "$MB"|cut -f1)"
done

# 3. union the z14 tiles into one file.
# Resumable: the pyramid step (4) takes ~40 min and has been interrupted before;
# re-merging 155k tiles just to redo it would be pointless. Skip if the union is
# already complete, measured against the per-block tile counts.
WANT=0
for MB in "$WORK"/blk*.mbtiles; do
  WANT=$(( WANT + $(sqlite3 "$MB" 'select count(*) from tiles') ))
done
HAVE=0
[ -s "$OUT" ] && HAVE=$(sqlite3 "$OUT" "select count(*) from tiles where zoom_level=(select max(zoom_level) from tiles)")
if [ "$HAVE" = "$WANT" ]; then
  say "merge already complete ($HAVE tiles), skipping"
else
say "merging (have $HAVE of $WANT)"
rm -f "$OUT" "$OUT-journal"
FIRST=$(ls "$WORK"/blk*.mbtiles | head -1)
cp "$FIRST" "$OUT"
sqlite3 "$OUT" "delete from tiles;"
for MB in "$WORK"/blk*.mbtiles; do
  sqlite3 "$OUT" "attach '$MB' as s; insert or replace into tiles select * from s.tiles; detach s;"
  say "  + $(basename "$MB") -> $(sqlite3 "$OUT" 'select count(*) from tiles') tiles"
done
fi

# 4. bounds/metadata over the union, then the z0-13 pyramid
python3 - <<'PY'
import sqlite3, math
p='/home/exedev/5mp/data/histmaps/sudan250k.mbtiles'
c=sqlite3.connect(p)
# Any partial pyramid from an interrupted gdaladdo must go: it would be kept as-is
# and the file would ship with a half-populated zoom level.
c.execute("delete from tiles where zoom_level < (select max(zoom_level) from tiles)")
c.commit(); c.execute("vacuum"); c.commit()
z,x0,x1,y0,y1=c.execute("select max(zoom_level),min(tile_column),max(tile_column),min(tile_row),max(tile_row) from tiles").fetchone()
n=2**z
# MBTiles tile_row is TMS (y increases NORTHWARD), so the minimum row is the
# SOUTH edge. Getting this backwards writes a bounds string with south>north,
# which GDAL rejects with "Invalid value for 'bounds' metadata".
def lon(xt): return xt/n*360.0-180.0
def lat(rt): return math.degrees(math.atan(math.sinh(math.pi*(2.0*rt/n-1.0))))
w,e,s_,nn = lon(x0), lon(x1+1), lat(y0), lat(y1+1)
meta={'name':'Sudan Survey 1:250,000 (1908-1944)','type':'overlay','version':'1.1',
 'format':'png','minzoom':'0','maxzoom':str(z),
 'bounds':f'{w:.6f},{s_:.6f},{e:.6f},{nn:.6f}',
 'center':f'{(w+e)/2:.6f},{(s_+nn)/2:.6f},7',
 'attribution':'Sudan Survey Dept., Khartoum / Library of Congress g8310m.gct00289 (no known copyright restrictions)',
 'description':'Anglo-Egyptian Sudan 1:250,000 series, 76 sheets, one edition per sheet cell. Transparent traced-ink overlay, TPS-warped to the printed 15-arcmin graticule and clipped to the neatline. Interior geometry is the 1900s-1940s route-traverse survey, not modern truth.'}
c.execute('delete from metadata')
c.executemany('insert into metadata values (?,?)', meta.items())
c.commit(); print('metadata', meta['bounds'], 'maxzoom', z)
PY

say "building overviews"
gdaladdo -r average "$OUT" 2 4 8 16 32 64 128 256 512 1024 -q
sqlite3 "$OUT" "select zoom_level, count(*), sum(length(tile_data))/1048576||' MB' from tiles group by 1;"
say "DONE $OUT $(du -h "$OUT"|cut -f1)"
# The per-block intermediates (~670 MB) are kept: they make step 2 resumable and
# a re-merge cheap. Delete them once the mosaic is verified:
#   rm -rf data/histmaps/work
say "intermediates kept in $WORK ($(du -sh "$WORK"|cut -f1)); rm -rf when verified"
echo MOSAIC_DONE
