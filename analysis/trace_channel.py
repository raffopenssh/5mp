"""Detect turbid (golden) channel pixels in TCI and find upstream-most extent."""
import json, urllib.request, sys
import numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds, transform as rio_t

SCENE="S2C_34NHN_20260619_0_L2A"
req=json.dumps({"collections":["sentinel-2-l2a"],"ids":[SCENE],"limit":1}).encode()
r=urllib.request.Request("https://earth-search.aws.element84.com/v1/search",data=req,headers={'Content-Type':'application/json'})
sc=json.load(urllib.request.urlopen(r))['features'][0]
href=sc['assets']['visual']['href']

with rasterio.open(href) as src:
    b=transform_bounds('EPSG:4326',src.crs,23.75,6.55,24.45,7.35)
    w=from_bounds(*b,src.transform)
    img=src.read(window=w).astype(int)
    wt=src.window_transform(w)
    crs=src.crs
r_,g_,b_=img[0],img[1],img[2]
# golden turbid water: bright warm, r>g>b strongly
mask=(r_>140)&(r_<255)&((r_-b_)>60)&((r_-g_)>20)&(g_>b_)
print(mask.sum(),'turbid px',file=sys.stderr)
ys,xs=np.nonzero(mask)
# to lon/lat
from rasterio.transform import xy
X,Y=xy(wt,ys,xs)
lons,lats=rio_t(crs,'EPSG:4326',X,Y)
lats=np.array(lats);lons=np.array(lons)
json.dump({'lats':lats[::3].round(5).tolist(),'lons':lons[::3].round(5).tolist()},open('/tmp/turbid_px.json','w'))
# northern/western extremes (upstream candidates)
idx=np.argsort(lats)[-40:]
print('northernmost turbid pixels:',file=sys.stderr)
seen=set()
for i in idx[::-1]:
    k=(round(lats[i],3),round(lons[i],3))
    if k in seen: continue
    seen.add(k)
    print(k,file=sys.stderr)
