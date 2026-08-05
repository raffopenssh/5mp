"""Fetch Sentinel-2 chips at manually-identified mining sites and test detector features."""
import json, math, os, sys, urllib.request, datetime
import numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN","EMPTY_DIR")
STAC="https://earth-search.aws.element84.com/v1/search"

PTS=[(24.406168931811,8.13455861179787),(24.4053800212228,8.13528408778103),
 (24.4069523944088,8.13363286402229),(24.4062334627161,8.13656888828994),
 (24.4055192105214,8.1340903042821),(24.4045076414196,8.13565320251528),
 (24.4027168656442,8.13893296707871),(24.4015028368395,8.13948304650826)]

def scenes(bbox, dt, cloud=40, limit=60):
    body=json.dumps({"collections":["sentinel-2-l2a"],"bbox":list(bbox),"datetime":dt,
      "query":{"eo:cloud_cover":{"lt":cloud}},"limit":limit,
      "sortby":[{"field":"properties.datetime","direction":"desc"}]}).encode()
    req=urllib.request.Request(STAC,data=body,headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req))["features"]

def chip(sc, bbox, bands=("red","green","blue","nir","swir16","scl")):
    out={}
    for b in bands:
        a=sc["assets"].get(b)
        if not a: continue
        with rasterio.open(a["href"]) as d:
            bb=transform_bounds("EPSG:4326", d.crs, *bbox)
            w=from_bounds(*bb, d.transform)
            arr=d.read(1, window=w, boundless=True, fill_value=0,
                       out_shape=(int(round((bbox[3]-bbox[1])/ (10/111000))), int(round((bbox[2]-bbox[0])/(10/111000/math.cos(math.radians(bbox[1]))))))) if False else d.read(1,window=w,boundless=True,fill_value=0)
            out[b]=arr.astype(np.float32)
    return out

def main():
    lon0=min(p[0] for p in PTS); lon1=max(p[0] for p in PTS)
    lat0=min(p[1] for p in PTS); lat1=max(p[1] for p in PTS)
    pad=0.012
    bbox=(lon0-pad,lat0-pad,lon1+pad,lat1+pad)
    print("AOI bbox",bbox, file=sys.stderr)
    today=datetime.date.today()
    results=[]
    for label, (d0,d1) in {
        "recent": (today-datetime.timedelta(days=120), today),
        "y1":     (today-datetime.timedelta(days=480), today-datetime.timedelta(days=365)),
        "y3":     (today-datetime.timedelta(days=1200), today-datetime.timedelta(days=1080)),
        "y6":     (today-datetime.timedelta(days=2300), today-datetime.timedelta(days=2180)),
    }.items():
        dt=f"{d0}T00:00:00Z/{d1}T23:59:59Z"
        scs=scenes(bbox,dt)
        print(f"{label}: {len(scs)} scenes", file=sys.stderr)
        for sc in scs[:6]:
            try: c=chip(sc,bbox)
            except Exception as e:
                print("  err",str(e)[:80],file=sys.stderr); continue
            scl=c["scl"]
            clear=np.isin(scl,(4,5,6,7,11)).mean()
            if clear<0.7: 
                print(f"  {sc['id']} clear={clear:.2f} skip",file=sys.stderr); continue
            np.save(f"analysis/out/{label}_{sc['id']}.npy", np.stack([c[b][:scl.shape[0],:scl.shape[1]] for b in ("red","green","blue","nir","swir16","scl")] ) if all(c[b].shape==scl.shape for b in ("red","green","blue","nir")) else np.stack([c[b] for b in ("red","green","blue","nir")]))
            red,nir,green,blue=c["red"],c["nir"],c["green"],c["blue"]
            ndvi=(nir-red)/np.maximum(nir+red,1)
            ndwi=(green-nir)/np.maximum(green+nir,1)
            mask=(red>1400)&(ndvi<0.35)
            print(f"  {sc['id']} {sc['properties']['datetime'][:10]} clear={clear:.2f} "
                  f"red_med={np.median(red):.0f} red_p95={np.percentile(red,95):.0f} "
                  f"mask_px={int(mask.sum())} ndvi_med={np.median(ndvi):.2f} ndwi_max={ndwi.max():.2f}",
                  file=sys.stderr)
            results.append({"label":label,"scene":sc["id"],"date":sc["properties"]["datetime"][:10],
                "clear":round(float(clear),3),"red_med":float(np.median(red)),
                "red_p95":float(np.percentile(red,95)),"mask_px":int(mask.sum()),
                "shape":list(red.shape)})
            break
    json.dump({"bbox":bbox,"results":results},open("analysis/out/truth_eval.json","w"),indent=1)

main()
