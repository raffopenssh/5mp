"""Compare the previously-validated large Chinko pit (7.44644N 24.02958E, 49 px)
with the 8 newly-supplied manual sites, in the same feature space.
Goal: confirm they are two different detection regimes."""
import json,os,datetime,urllib.request
import numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds, transform as rio_tr
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN","EMPTY_DIR")
STAC="https://earth-search.aws.element84.com/v1/search"
GROUPS={
 "known_big_pit":[(24.02958,7.44644)],
 "manual_8":[(24.406168931811,8.13455861179787),(24.4053800212228,8.13528408778103),
  (24.4069523944088,8.13363286402229),(24.4062334627161,8.13656888828994),
  (24.4055192105214,8.1340903042821),(24.4045076414196,8.13565320251528),
  (24.4027168656442,8.13893296707871),(24.4015028368395,8.13948304650826)],
}
BANDS=("blue","red","nir","swir16","scl")
def scenes(bbox,dt,cloud=25):
    body=json.dumps({"collections":["sentinel-2-l2a"],"bbox":list(bbox),"datetime":dt,
      "query":{"eo:cloud_cover":{"lt":cloud}},"limit":100,
      "sortby":[{"field":"properties.datetime","direction":"desc"}]}).encode()
    r=urllib.request.Request(STAC,data=body,headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(r))["features"]
def read(sc,bbox):
    out={};ref=None
    for b in BANDS:
        a=sc["assets"].get(b)
        if not a: return None
        with rasterio.open(a["href"]) as d:
            bb=transform_bounds("EPSG:4326",d.crs,*bbox)
            w=from_bounds(*bb,d.transform)
            if ref is None:
                arr=d.read(1,window=w,boundless=True,fill_value=0);ref=arr.shape
                out["_crs"]=d.crs;out["_tr"]=d.window_transform(w)
            else:
                arr=d.read(1,window=w,boundless=True,fill_value=0,out_shape=ref,
                  resampling=rasterio.enums.Resampling.bilinear)
            out[b]=arr.astype(np.float32)
    return out
for gname,pts in GROUPS.items():
    PAD=0.03
    bbox=(min(p[0] for p in pts)-PAD,min(p[1] for p in pts)-PAD,
          max(p[0] for p in pts)+PAD,max(p[1] for p in pts)+PAD)
    for sc in scenes(bbox,"2026-01-05T00:00:00Z/2026-03-25T23:59:59Z"):
        c=read(sc,bbox)
        if c is None: continue
        if np.isin(c["scl"],(4,5,6,7,11)).mean()<0.95: continue
        red,nir,blu,s1=c["red"],c["nir"],c["blue"],c["swir16"]
        ndvi=(nir-red)/np.maximum(nir+red,1)
        bsi=((s1+red)-(nir+blu))/np.maximum((s1+red)+(nir+blu),1)
        tr,crs=c["_tr"],c["_crs"]
        def wmax(a,lon,lat,k=2):
            x,y=rio_tr("EPSG:4326",crs,[lon],[lat]);col,row=(~tr)*(x[0],y[0])
            r,cc=int(round(row)),int(round(col))
            return a[max(0,r-k):r+k+1,max(0,cc-k):cc+k+1]
        print(f"\n{gname}  scene={sc['id']} date={sc['properties']['datetime'][:10]}")
        print(f"  bg red p50={np.percentile(red,50):.0f} p99={np.percentile(red,99):.0f} "
              f"| >1400 px in AOI = {int((red>1400).sum())}")
        for lo,la in pts:
            w=wmax(red,lo,la); wn=wmax(ndvi,lo,la)
            print(f"  {la:.5f},{lo:.5f}  red_max={w.max():.0f} red_mean={w.mean():.0f} "
                  f"ndvi_min={wn.min():.2f} bare1400_px={int((w>1400).sum())} "
                  f"pct_red={(red<w.max()).mean()*100:.1f}")
        break
