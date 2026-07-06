"""Sample Sentinel-2 water color along the Chinko mainstem to find turbidity onset (mining plumes)."""
import json, math, sys, urllib.request
import numpy as np
import rasterio
from rasterio.warp import transform as rio_transform

# 1. Get ordered Chinko mainstem points (OSM), headwaters->downstream (N->S)
d=json.load(open('data/osm_raw/caf_rivers.geojson'))
segs=[f['geometry']['coordinates'] for f in d['features']
      if f['properties'].get('name')=='Chinko' and f['geometry']['type']=='LineString']
# chain segments greedily
def km(a,b): return math.hypot((a[1]-b[1])*111,(a[0]-b[0])*111*math.cos(math.radians(a[1])))
# start from northernmost segment end
pts=[]
remaining=[list(s) for s in segs]
# pick seg containing max lat
start=max(remaining,key=lambda s:max(p[1] for p in s))
if start[0][1]<start[-1][1]: start.reverse()
pts+=start; remaining.remove(start)
while remaining:
    end=pts[-1]
    best=min(remaining,key=lambda s:min(km(end,s[0]),km(end,s[-1])))
    dgap=min(km(end,best[0]),km(end,best[-1]))
    if dgap>10: break
    if km(end,best[-1])<km(end,best[0]): best.reverse()
    pts+=best; remaining.remove(best)
print(f"chained {len(pts)} pts, lat {pts[0][1]:.3f} -> {pts[-1][1]:.3f}", file=sys.stderr)

# downsample to ~200m spacing, cumulative distance
sampled=[pts[0]]; dist=[0.0]
for p in pts[1:]:
    dd=km(sampled[-1],p)
    if dd>=0.2:
        sampled.append(p); dist.append(dist[-1]+dd)
print(f"{len(sampled)} sample pts over {dist[-1]:.0f} km", file=sys.stderr)

# 2. Find covering S2 scenes
import subprocess
req=json.dumps({"collections":["sentinel-2-l2a"],
 "bbox":[min(p[0] for p in sampled)-0.05,min(p[1] for p in sampled)-0.05,
         max(p[0] for p in sampled)+0.05,max(p[1] for p in sampled)+0.05],
 "datetime":"2026-04-15T00:00:00Z/2026-07-06T00:00:00Z",
 "query":{"eo:cloud_cover":{"lt":40}},"limit":100,
 "sortby":[{"field":"properties.eo:cloud_cover","direction":"asc"}]}).encode()
r=urllib.request.Request("https://earth-search.aws.element84.com/v1/search",data=req,
                          headers={'Content-Type':'application/json'})
scenes=json.load(urllib.request.urlopen(r))['features']
print(f"{len(scenes)} scenes", file=sys.stderr)

# 3. Sample red/green/blue/nir at each river point from best scene covering it
results=[None]*len(sampled)
from shapely.geometry import shape, Point
for sc in scenes:
    geom=shape(sc['geometry'])
    todo=[i for i,rr in enumerate(results) if rr is None and geom.contains(Point(sampled[i]))]
    if not todo: continue
    print(sc['id'], sc['properties']['eo:cloud_cover'], len(todo), file=sys.stderr)
    assets=sc['assets']
    try:
        with rasterio.open(assets['red']['href']) as R, \
             rasterio.open(assets['green']['href']) as G, \
             rasterio.open(assets['nir']['href']) as N, \
             rasterio.open(assets['scl']['href']) as S:
            xs=[sampled[i][0] for i in todo]; ys=[sampled[i][1] for i in todo]
            tx,ty=rio_transform('EPSG:4326',R.crs,xs,ys)
            red=list(R.sample(zip(tx,ty)))
            grn=list(G.sample(zip(tx,ty)))
            nir=list(N.sample(zip(tx,ty)))
            scl=list(S.sample(zip(tx,ty)))
            for j,i in enumerate(todo):
                results[i]={'red':int(red[j][0]),'green':int(grn[j][0]),'nir':int(nir[j][0]),
                            'scl':int(scl[j][0]),'scene':sc['id'],'date':sc['properties']['datetime'][:10]}
    except Exception as e:
        print('ERR',sc['id'],e,file=sys.stderr)
    if all(r is not None for r in results): break

out=[]
for i,rr in enumerate(results):
    if rr is None: continue
    rr.update({'lon':round(sampled[i][0],5),'lat':round(sampled[i][1],5),'dist_km':round(dist[i],1)})
    out.append(rr)
json.dump(out,open('/tmp/chinko_river_color.json','w'))
print(f"wrote {len(out)} samples", file=sys.stderr)
