"""Trace turbidity upstream through the OSM waterway network to locate source."""
import json, math, sys, urllib.request
import numpy as np, rasterio
from rasterio.warp import transform as rio_transform

SCENE="S2C_34NHN_20260619_0_L2A"
req=json.dumps({"collections":["sentinel-2-l2a"],"ids":[SCENE],"limit":1}).encode()
r=urllib.request.Request("https://earth-search.aws.element84.com/v1/search",data=req,headers={'Content-Type':'application/json'})
sc=json.load(urllib.request.urlopen(r))['features'][0]

d=json.load(open('data/osm_raw/caf_rivers.geojson'))
# all waterway segments in scene bbox area of interest
segs=[]
for f in d['features']:
    if f['geometry']['type']!='LineString': continue
    cs=f['geometry']['coordinates']
    if any(6.55<=la<=7.35 and 23.75<=lo<=24.45 for lo,la in cs[::4]):
        segs.append(cs)
print(len(segs),'segments in AOI',file=sys.stderr)

# sample every ~150m
pts=[]
def km(a,b): return math.hypot((a[1]-b[1])*111,(a[0]-b[0])*111*math.cos(math.radians(a[1])))
for s in segs:
    last=None
    for p in s:
        if not (6.55<=p[1]<=7.35 and 23.75<=p[0]<=24.45): continue
        if last is None or km(last,p)>=0.15:
            pts.append(p); last=p
print(len(pts),'sample pts',file=sys.stderr)

with rasterio.open(sc['assets']['red']['href']) as R, \
     rasterio.open(sc['assets']['scl']['href']) as S:
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    tx,ty=rio_transform('EPSG:4326',R.crs,xs,ys)
    red=[int(v[0]) for v in R.sample(zip(tx,ty))]
    scl=[int(v[0]) for v in S.sample(zip(tx,ty))]

out=[{'lon':round(p[0],5),'lat':round(p[1],5),'red':red[i],'scl':scl[i]}
     for i,p in enumerate(pts)]
json.dump(out,open('/tmp/trib_trace.json','w'))
w=[o for o in out if o['scl']==6]
turb=[o for o in w if o['red']>1200]
print(f"{len(w)} water px, {len(turb)} turbid",file=sys.stderr)
# cluster turbid points, print extremes
turb.sort(key=lambda o:-o['lat'])
print("northernmost turbid water:",file=sys.stderr)
for o in turb[:15]: print(o,file=sys.stderr)
