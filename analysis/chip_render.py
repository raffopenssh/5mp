"""Render RGB + index chips for an AOI across seasons/years (mining site inspection)."""
import json,math,os,sys,urllib.request,datetime,argparse
import numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
from PIL import Image
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN","EMPTY_DIR")
STAC="https://earth-search.aws.element84.com/v1/search"

def scenes(bbox,dt,cloud=35,limit=80):
    body=json.dumps({"collections":["sentinel-2-l2a"],"bbox":list(bbox),"datetime":dt,
      "query":{"eo:cloud_cover":{"lt":cloud}},"limit":limit,
      "sortby":[{"field":"properties.datetime","direction":"desc"}]}).encode()
    r=urllib.request.Request(STAC,data=body,headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(r))["features"]

def read(sc,bbox,bands):
    out={}
    ref=None
    for b in bands:
        a=sc["assets"].get(b)
        if not a: return None
        with rasterio.open(a["href"]) as d:
            bb=transform_bounds("EPSG:4326",d.crs,*bbox)
            w=from_bounds(*bb,d.transform)
            if ref is None:
                arr=d.read(1,window=w,boundless=True,fill_value=0)
                ref=arr.shape
            else:
                arr=d.read(1,window=w,boundless=True,fill_value=0,out_shape=ref,
                           resampling=rasterio.enums.Resampling.nearest)
            out[b]=arr.astype(np.float32)
    return out

def stretch(a,lo=2,hi=98):
    p0,p1=np.percentile(a,[lo,hi]);  return np.clip((a-p0)/max(p1-p0,1),0,1)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--bbox",required=True)
    ap.add_argument("--out",default="analysis/out")
    ap.add_argument("--windows",default="")  # label:YYYY-MM-DD:YYYY-MM-DD,...
    a=ap.parse_args()
    bbox=tuple(float(x) for x in a.bbox.split(","))
    os.makedirs(a.out,exist_ok=True)
    wins=[w.split(":") for w in a.windows.split(",") if w]
    summary=[]
    for label,d0,d1 in wins:
        scs=scenes(bbox,f"{d0}T00:00:00Z/{d1}T23:59:59Z")
        picked=None
        for sc in scs:
            try: c=read(sc,bbox,("red","green","blue","nir","swir16","scl"))
            except Exception as e:
                print(" err",str(e)[:70],file=sys.stderr); continue
            if c is None: continue
            scl=c["scl"]; clear=float(np.isin(scl,(4,5,6,7,11)).mean())
            if clear<0.9: continue
            picked=(sc,c,clear); break
        if not picked:
            print(f"{label}: no clear scene",file=sys.stderr); continue
        sc,c,clear=picked
        red,grn,blu,nir,swir=c["red"],c["green"],c["blue"],c["nir"],c["swir16"]
        rgb=np.dstack([stretch(red),stretch(grn),stretch(blu)])
        Image.fromarray((rgb*255).astype(np.uint8)).resize((red.shape[1]*3,red.shape[0]*3),Image.NEAREST)\
            .save(f"{a.out}/{label}_rgb.png")
        ndvi=(nir-red)/np.maximum(nir+red,1); ndwi=(grn-nir)/np.maximum(grn+nir,1)
        bsi=((swir+red)-(nir+blu))/np.maximum((swir+red)+(nir+blu),1)
        fc=np.dstack([stretch(swir),stretch(nir),stretch(red)])
        Image.fromarray((fc*255).astype(np.uint8)).resize((red.shape[1]*3,red.shape[0]*3),Image.NEAREST)\
            .save(f"{a.out}/{label}_swir.png")
        s={"label":label,"scene":sc["id"],"date":sc["properties"]["datetime"][:10],"clear":round(clear,3),
           "red_med":float(np.median(red)),"red_p99":float(np.percentile(red,99)),
           "ndvi_med":float(np.median(ndvi)),"ndvi_p05":float(np.percentile(ndvi,5)),
           "bsi_p99":float(np.percentile(bsi,99)),"ndwi_p99":float(np.percentile(ndwi,99)),
           "bare_1400":int(((red>1400)&(ndvi<0.35)).sum()),
           "bare_rel":int(((ndvi<np.percentile(ndvi,5))&(bsi>np.percentile(bsi,95))).sum()),
           "shape":list(red.shape)}
        print(json.dumps(s),file=sys.stderr)
        summary.append(s)
    json.dump(summary,open(f"{a.out}/chip_summary.json","w"),indent=1)
main()
