"""Annual dry-season Sentinel-2 composite time series at the 8 confirmed pits
vs local background percentile — does the signal have an onset we could alert on?"""
import json,os,sys,datetime,urllib.request
import numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds, transform as rio_tr
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN","EMPTY_DIR")
STAC="https://earth-search.aws.element84.com/v1/search"
PTS=[(24.406168931811,8.13455861179787),(24.4053800212228,8.13528408778103),
 (24.4069523944088,8.13363286402229),(24.4062334627161,8.13656888828994),
 (24.4055192105214,8.1340903042821),(24.4045076414196,8.13565320251528),
 (24.4027168656442,8.13893296707871),(24.4015028368395,8.13948304650826)]
BANDS=("blue","red","nir","swir16","scl")
PAD=0.03
def scenes(bbox,dt,cloud=30,limit=200):
    body=json.dumps({"collections":["sentinel-2-l2a"],"bbox":list(bbox),"datetime":dt,
      "query":{"eo:cloud_cover":{"lt":cloud}},"limit":limit,
      "sortby":[{"field":"properties.datetime","direction":"asc"}]}).encode()
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
bbox=(min(p[0] for p in PTS)-PAD,min(p[1] for p in PTS)-PAD,
      max(p[0] for p in PTS)+PAD,max(p[1] for p in PTS)+PAD)
rows=[]
for yr in range(2017,2027):
    stack={"red":[],"bsi":[],"ndvi":[]}
    meta=[]
    for sc in scenes(bbox,f"{yr}-01-05T00:00:00Z/{yr}-03-25T23:59:59Z"):
        try: c=read(sc,bbox)
        except Exception: continue
        if c is None: continue
        valid=np.isin(c["scl"],(4,5,6,7,11))
        if valid.mean()<0.9: continue
        red,nir,blu,s1=c["red"],c["nir"],c["blue"],c["swir16"]
        ndvi=(nir-red)/np.maximum(nir+red,1)
        bsi=((s1+red)-(nir+blu))/np.maximum((s1+red)+(nir+blu),1)
        stack["red"].append(np.where(valid,red,np.nan))
        stack["bsi"].append(np.where(valid,bsi,np.nan))
        stack["ndvi"].append(np.where(valid,ndvi,np.nan))
        meta.append((sc["id"],c["_tr"],c["_crs"]))
        if len(meta)>=4: break
    if not meta:
        print(yr,"no scene",flush=True); continue
    sh=min(x.shape for x in stack["red"])
    comp={k:np.nanmedian(np.stack([a[:sh[0],:sh[1]] for a in v]),0) for k,v in stack.items()}
    tr,crs=meta[0][1],meta[0][2]
    def at(a,lon,lat):
        x,y=rio_tr("EPSG:4326",crs,[lon],[lat]); col,row=(~tr)*(x[0],y[0])
        r,c2=int(round(row)),int(round(col))
        w=a[max(0,r-1):r+2,max(0,c2-1):c2+2]
        return float(np.nanmax(w)) if w.size else float("nan")
    rec={"year":yr,"n_scenes":len(meta)}
    for k,a in comp.items():
        v=[at(a,*p) for p in PTS]
        rec[k+"_truth_mean"]=round(float(np.nanmean(v)),3)
        flat=a[np.isfinite(a)]
        rec[k+"_bg_p50"]=round(float(np.percentile(flat,50)),3)
        rec[k+"_bg_p99"]=round(float(np.percentile(flat,99)),3)
        rec[k+"_truth_pctile"]=round(float(np.mean([(flat<x).mean() for x in v if np.isfinite(x)])*100),1)
    print(json.dumps(rec),flush=True)
    rows.append(rec)
json.dump(rows,open("analysis/out/truth_timeseries.json","w"),indent=1)
