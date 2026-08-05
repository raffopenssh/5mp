"""Quantitative A/B of candidate mining-pit detection rules against the
manually-identified truth points (8 pits, Chinko headwaters, CAR).

For each of several dates we build a chip, apply each rule, cluster, and score:
  recall  = truth pts with a detection within TOL_M
  n_det   = total detections in the chip (false-positive proxy; AOI is 1.3x1.3 km)
Rules mix absolute thresholds (current production) with LOCAL-RELATIVE ones.
"""
import json,os,sys,datetime,math,urllib.request
import numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds, transform as rio_tr
from scipy import ndimage
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN","EMPTY_DIR")
STAC="https://earth-search.aws.element84.com/v1/search"
PTS=[(24.406168931811,8.13455861179787),(24.4053800212228,8.13528408778103),
 (24.4069523944088,8.13363286402229),(24.4062334627161,8.13656888828994),
 (24.4055192105214,8.1340903042821),(24.4045076414196,8.13565320251528),
 (24.4027168656442,8.13893296707871),(24.4015028368395,8.13948304650826)]
TOL_M=120
BANDS=("blue","green","red","nir","swir16","swir22","scl")
PAD=0.020   # ~2.2 km AOI so the local background is meaningful

def scenes(bbox,dt,cloud=25,limit=100):
    body=json.dumps({"collections":["sentinel-2-l2a"],"bbox":list(bbox),"datetime":dt,
      "query":{"eo:cloud_cover":{"lt":cloud}},"limit":limit,
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

def idx(c):
    red,nir,grn,blu,s1,s2=c["red"],c["nir"],c["green"],c["blue"],c["swir16"],c["swir22"]
    return {"red":red,
      "ndvi":(nir-red)/np.maximum(nir+red,1),
      "bsi":((s1+red)-(nir+blu))/np.maximum((s1+red)+(nir+blu),1),
      "rb":red/np.maximum(blu,1),
      "mndwi":(grn-s1)/np.maximum(grn+s1,1)}

def local_z(a,size=61):
    """(a - local median) / local MAD-ish, size in px (~600m at 10m)."""
    med=ndimage.uniform_filter(a,size=size)
    sd=np.sqrt(np.maximum(ndimage.uniform_filter(a*a,size=size)-med*med,1e-6))
    return (a-med)/sd

def clusters(mask,minpx,tr,crs):
    lab,n=ndimage.label(mask)
    out=[]
    for i in range(1,n+1):
        m=lab==i; s=int(m.sum())
        if s<minpx: continue
        cy,cx=ndimage.center_of_mass(m)
        x,y=tr*(cx,cy)
        lo,la=rio_tr(crs,"EPSG:4326",[x],[y])
        out.append((lo[0],la[0],s))
    return out

def m(a,b):
    return math.hypot((a[0]-b[0])*111320*math.cos(math.radians(a[1])),(a[1]-b[1])*110540)

def rules(I):
    red,ndvi,bsi,rb,mndwi=I["red"],I["ndvi"],I["bsi"],I["rb"],I["mndwi"]
    zr=local_z(red); zn=local_z(ndvi); zrb=local_z(rb)
    pr=lambda a,q: a>np.percentile(a,q)
    return {
      "prod_abs (red>1400 & ndvi<0.35)": ((red>1400)&(ndvi<0.35), 8),
      "abs_relaxed (red>1100 & ndvi<0.45)": ((red>1100)&(ndvi<0.45), 6),
      "pct (red>p97 & ndvi<p10)": (pr(red,97)&(ndvi<np.percentile(ndvi,10)), 3),
      "localz (zred>2 & zndvi<-1.5)": ((zr>2.0)&(zn<-1.5), 3),
      "localz_iron (zred>1.5 & zrb>1.5)": ((zr>1.5)&(zrb>1.5), 3),
      "iron_pct (rb>p97)": (pr(rb,97), 3),
      "bare_bsi (bsi>p97 & ndvi<p15)": (pr(bsi,97)&(ndvi<np.percentile(ndvi,15)), 3),
      "combo (zred>1.5 & zrb>1 & ndvi<p25)": ((zr>1.5)&(zrb>1.0)&(ndvi<np.percentile(ndvi,25)), 2),
    }

def main():
    bbox=(min(p[0] for p in PTS)-PAD,min(p[1] for p in PTS)-PAD,
          max(p[0] for p in PTS)+PAD,max(p[1] for p in PTS)+PAD)
    wins={"2026dry":(datetime.date(2026,1,10),datetime.date(2026,3,20)),
          "2026wet":(datetime.date(2026,6,10),datetime.date(2026,8,4)),
          "2025dry":(datetime.date(2025,1,10),datetime.date(2025,3,20)),
          "2023dry":(datetime.date(2023,1,10),datetime.date(2023,3,20)),
          "2019dry":(datetime.date(2019,1,10),datetime.date(2019,3,20))}
    agg={}
    for label,(d0,d1) in wins.items():
        picked=None
        for sc in scenes(bbox,f"{d0}T00:00:00Z/{d1}T23:59:59Z"):
            try: c=read(sc,bbox)
            except Exception as e: continue
            if c is None: continue
            if float(np.isin(c["scl"],(4,5,6,7,11)).mean())<0.97: continue
            picked=(sc,c); break
        if not picked:
            print(f"{label}: no clear scene",file=sys.stderr); continue
        sc,c=picked; I=idx(c)
        print(f"\n== {label} {sc['id']} shape={I['red'].shape}")
        print(f"{'rule':42s} {'recall':>7s} {'n_det':>6s} {'det/km2':>8s}")
        km2=(bbox[2]-bbox[0])*111*(bbox[3]-bbox[1])*111
        for name,(mask,minpx) in rules(I).items():
            cl=clusters(mask,minpx,c["_tr"],c["_crs"])
            hit=sum(1 for p in PTS if any(m((x,y),p)<=TOL_M for x,y,_ in cl))
            print(f"{name:42s} {hit}/8{'':>3s} {len(cl):6d} {len(cl)/km2:8.1f}")
            a=agg.setdefault(name,{"hit":0,"n":0,"det":0})
            a["hit"]+=hit; a["n"]+=8; a["det"]+=len(cl)
    print("\n== AGGREGATE across dates")
    print(f"{'rule':42s} {'recall':>8s} {'mean_det':>9s}")
    for k,v in sorted(agg.items(),key=lambda kv:-kv[1]["hit"]/max(kv[1]["n"],1)):
        print(f"{k:42s} {v['hit']}/{v['n']:<4d} {v['det']/ (v['n']/8):9.0f}")
main()
