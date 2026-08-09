#!/usr/bin/env python3
"""Rewrite the mosaic's metadata table from what is actually on disk.

Split out of mosaic.sh step 4 so the description/name/bounds can be corrected
WITHOUT touching tiles. The step in mosaic.sh also deletes the overview pyramid
(deliberately -- an interrupted gdaladdo leaves a half-populated zoom level),
which makes it unsafe to re-run just to fix a string: on the 3.6 GB file that
DELETE takes minutes and would throw away ~40 min of gdaladdo.

Sheet count, block count and year span are derived from data/histmaps/geo,
never typed in. A literal "76 sheets" survived a rebuild that more than doubled
the coverage and shipped inside the layer's own description.
"""
import sqlite3, math, os, sys, glob

MB = '/home/exedev/5mp/data/histmaps/sudan250k.mbtiles'
GEO = '/home/exedev/5mp/data/histmaps/geo'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sudan250k import catalogue

c = sqlite3.connect(MB)
z, x0, x1, y0, y1 = c.execute(
    "select max(zoom_level),min(tile_column),max(tile_column),"
    "min(tile_row),max(tile_row) from tiles "
    "where zoom_level=(select max(zoom_level) from tiles)").fetchone()
n = 2 ** z
# MBTiles tile_row is TMS (y increases NORTHWARD): the minimum row is the SOUTH
# edge. Backwards writes south>north, which GDAL rejects as "Invalid value for
# 'bounds' metadata" -- a warning, not an error, so it ships unless you read it.
lon = lambda xt: xt / n * 360.0 - 180.0
lat = lambda rt: math.degrees(math.atan(math.sinh(math.pi * (2.0 * rt / n - 1.0))))
w, e, s, nn = lon(x0), lon(x1 + 1), lat(y0), lat(y1 + 1)

cat = {x['id']: x for x in catalogue()}
ids = [os.path.basename(f).split('_')[0] for f in glob.glob(f'{GEO}/*_geo.tif')]
yrs = sorted(cat[i]['year'] for i in ids if cat.get(i) and cat[i]['year'])
blocks = sorted({cat[i]['sheet'].split('-')[0] for i in ids if cat.get(i)}, key=int)
span = f"{yrs[0]}-{yrs[-1]}" if yrs else ""

meta = {
    'name': f'Sudan Survey 1:250,000 ({span})', 'type': 'overlay', 'version': '1.2',
    'format': 'png', 'minzoom': '0', 'maxzoom': str(z),
    'bounds': f'{w:.6f},{s:.6f},{e:.6f},{nn:.6f}',
    'center': f'{(w+e)/2:.6f},{(s+nn)/2:.6f},7',
    'attribution': 'Sudan Survey Dept., Khartoum / Library of Congress '
                   'g8310m.gct00289 (no known copyright restrictions)',
    'description': f'Anglo-Egyptian Sudan 1:250,000 series, {len(ids)} sheets '
                   f'across {len(blocks)} 1:1M blocks, one edition per sheet '
                   'cell. Transparent traced-ink overlay, TPS-warped to the '
                   'printed 15-arcmin graticule and clipped to the neatline. '
                   'Interior geometry is the 1900s-1940s route-traverse survey, '
                   'not modern truth.',
}
c.execute('delete from metadata')
c.executemany('insert into metadata values (?,?)', meta.items())
c.commit()
print(f"{len(ids)} sheets, {len(blocks)} blocks ({','.join(blocks)}), {span}")
print(f"bounds {meta['bounds']}  maxzoom {z}")
