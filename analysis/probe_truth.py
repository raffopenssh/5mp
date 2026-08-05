"""Per-pixel spectral probe at manually-identified mining sites vs local background.
Answers: which index/threshold actually separates the truth pixels?"""
import json,math,os,sys,urllib.request,datetime
import numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds, transform as rio_tr
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN","EMPTY_DIR")
STAC="https://earth-search.aws.element84.com/v1/search"
PTS=[(24.406168931811,8.13455861179787),(24.4053800212228,8.13528408778103),
 (24.4069523944088,8.13363286402229),(24.4062334627161,8.13656888828994),
 (24.4055192105214,8.1340903042821),(24.4045076414196,8.13565320251528),
 (24.4027168656442,8.13893296707871),(24.4015028368395,8.13948304650826)]
BANDS=("blue","green","red","rededge1","rededge2","rededge3","nir","nir08","swir16","swir22","scl")

def scenes(bbox,dt,cloud=25,limit=100):
    body=json.dumps({"collections":["sentinel-2-l2a"],"bbox":list(bbox),"datetime":dt,
      "query":{"eo:cloud_cover":{"lt":cloud}},"limit":limit,
      "sortby":[{"field":"properties.datetime","direction":"desc"}]}).encode()
    r=urllib.request.Request(STAC,data=body,headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(r))["features"]

def read(sc,bbox,shape=None):
    out={};ref=shape
    for b in BANDS:
        a=sc["assets"].get(b)
        if not a: return None
        with rasterio.open(a["href"]) as d:
            bb=transform_bounds("EPSG:4326",d.crs,*bbox)
            w=from_bounds(*bb,d.transform)
            if ref is None:
                arr=d.read(1,window=w,boundless=True,fill_value=0); ref=arr.shape
                out["_crs"]=d.crs; out["_tr"]=d.window_transform(w)
            else:
                arr=d.read(1,window=w,boundless=True,fill_value=0,out_shape=ref,
                    resampling=rasterio.enums.Resampling.bilinear)
            out[b]=arr.astype(np.float32)
    return out

def pix_of(c,tr,crs,lon,lat):
    x,y=rio_tr("EPSG:4326",crs,[lon],[lat])
    col,row=(~tr)*(x[0],y[0]); return int(row),int(col)

def main():
    pad=0.006
    bbox=(min(p[0] for p in PTS)-pad,min(p[1] for p in PTS)-pad,
          max(p[0] for p in PTS)+pad,max(p[1] for p in PTS)+pad)
    today=datetime.date.today()
    rows=[]
    for label,(d0,d1) in {
      "2026-02":(today-datetime.timedelta(days=200),today-datetime.timedelta(days=130)),
      "2026-06":(today-datetime.timedelta(days=60),today),
      "2025-02":(datetime.date(2025,1,15),datetime.date(2025,3,15)),
      "2019-02":(datetime.date(2019,1,15),datetime.date(2019,3,15)),
    }.items():
        for sc in scenes(bbox,f"{d0}T00:00:00Z/{d1}T23:59:59Z"):
            try: c=read(sc,bbox)
            except Exception as e: print("err",str(e)[:60],file=sys.stderr); continue
            if c is None: continue
            if float(np.isin(c["scl"],(4,5,6,7,11)).mean())<0.95: continue
            tr,crs=c["_tr"],c["_crs"]
            red,nir,grn,blu,sw1,sw2=c["red"],c["nir"],c["green"],c["blue"],c["swir16"],c["swir22"]
            ndvi=(nir-red)/np.maximum(nir+red,1)
            ndwi=(grn-nir)/np.maximum(grn+nir,1)
            mndwi=(grn-sw1)/np.maximum(grn+sw1,1)
            bsi=((sw1+red)-(nir+blu))/np.maximum((sw1+red)+(nir+blu),1)
            ci=red/np.maximum(blu,1)   # iron/clay redness
            claymin=sw1/np.maximum(sw2,1)
            stack={"red":red,"ndvi":ndvi,"ndwi":ndwi,"mndwi":mndwi,"bsi":bsi,"redblue":ci,"clay":claymin}
            for i,(lo,la) in enumerate(PTS):
                r,cc=pix_of(c,tr,crs,lo,la)
                rec={"label":label,"scene":sc["id"],"date":sc["properties"]["datetime"][:10],"pt":i}
                for k,v in stack.items():
                    if 0<=r<v.shape[0] and 0<=cc<v.shape[1]:
                        rec[k]=round(float(v[r,cc]),3)
                        rec[k+"_pct"]=round(float((v<v[r,cc]).mean()*100),1)
                rows.append(rec)
            # scene-level percentile refs
            rows.append({"label":label,"scene":sc["id"],"date":sc["properties"]["datetime"][:10],"pt":"BG",
              **{k+"_p50":round(float(np.percentile(v,50)),3) for k,v in stack.items()},
              **{k+"_p99":round(float(np.percentile(v,99)),3) for k,v in stack.items()}})
            break
    json.dump(rows,open("analysis/out/probe_truth.json","w"),indent=1)
    for r in rows: print(json.dumps(r))
main()
