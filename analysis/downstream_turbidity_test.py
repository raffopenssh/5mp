"""Does the confirmed cluster of 8 manual pits produce a detectable downstream
turbidity signal? Trace the MERIT downstream path from the pits and sample
Sentinel-2 red on water pixels above/below the site."""
import json,os,math,urllib.request,datetime
import numpy as np, rasterio
from rasterio.warp import transform as rio_tr
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN","EMPTY_DIR")
STAC="https://earth-search.aws.element84.com/v1/search"
SITE=(24.4062,8.1346)

path=json.load(urllib.request.urlopen(
  f"https://merit.internetofwater.app/processes/river-runner/execution?lat={SITE[1]}&lng={SITE[0]}",
  timeout=180))["value"]["features"]
up=json.load(urllib.request.urlopen(
  f"https://mghydro.com/app/upstream_rivers_api?lat={SITE[1]}&lng={SITE[0]}",timeout=180))["features"]
def dens(cs,step=0.1):
    out=[];last=None
    for c in cs:
        if last is None or math.hypot((c[0]-last[0])*111*math.cos(math.radians(c[1])),(c[1]-last[1])*111)>=step:
            out.append(tuple(c[:2]));last=c
    return out
dn=[];  # downstream, ordered, cumulative km
cum=0
for f in path[:12]:
    for p in dens(f["geometry"]["coordinates"]):
        dn.append((p,cum))
    cum+=f["properties"]["lengthkm"]
upp=[]
for f in up:
    upp+= [(p,-1) for p in dens(f["geometry"]["coordinates"])]
print("downstream sample pts",len(dn),"upstream",len(upp))
pts=[p for p,_ in upp]+[p for p,_ in dn]
lo0=min(p[0] for p in pts)-0.02; lo1=max(p[0] for p in pts)+0.02
la0=min(p[1] for p in pts)-0.02; la1=max(p[1] for p in pts)+0.02
body=json.dumps({"collections":["sentinel-2-l2a"],"bbox":[lo0,la0,lo1,la1],
  "datetime":"2026-06-01T00:00:00Z/2026-08-05T00:00:00Z",
  "query":{"eo:cloud_cover":{"lt":20}},"limit":30,
  "sortby":[{"field":"properties.datetime","direction":"desc"}]}).encode()
r=urllib.request.Request(STAC,data=body,headers={"Content-Type":"application/json"})
scs=json.load(urllib.request.urlopen(r))["features"]
print("scenes",len(scs))
for sc in scs[:3]:
    with rasterio.open(sc["assets"]["red"]["href"]) as R, \
         rasterio.open(sc["assets"]["scl"]["href"]) as S, \
         rasterio.open(sc["assets"]["nir"]["href"]) as N:
        xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
        tx,ty=rio_tr("EPSG:4326",R.crs,xs,ys)
        red=[int(v[0]) for v in R.sample(list(zip(tx,ty)))]
        scl=[int(v[0]) for v in S.sample(list(zip(tx,ty)))]
    nw=sum(1 for s in scl if s==6)
    print(f"{sc['id']} {sc['properties']['datetime'][:10]}: water px on network = {nw}/{len(pts)}")
    if nw:
        w=[(pts[i],red[i]) for i in range(len(pts)) if scl[i]==6]
        print("  red on water:", sorted(x[1] for x in w)[:10], "...", sorted(x[1] for x in w)[-5:])
