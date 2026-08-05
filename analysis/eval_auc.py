"""Per-pixel discrimination (AUC) of candidate features: truth mining pixels
vs random background in the same AOI, single-date and multi-date-composite.

The rule-recall test was uninformative (loose rules flood the AOI so a hit
within 120 m is nearly free). AUC on ranked pixel scores is honest."""
import json,os,sys,math,datetime,urllib.request
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
BANDS=("blue","green","red","nir","swir16","swir22","scl")
PAD=0.030

def scenes(bbox,dt,cloud=25,limit=200):
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

def local_z(a,size=61):
    med=ndimage.uniform_filter(a,size=size)
    sd=np.sqrt(np.maximum(ndimage.uniform_filter(a*a,size=size)-med*med,1e-6))
    return (a-med)/sd

def feats(c):
    red,nir,grn,blu,s1,s2=c["red"],c["nir"],c["green"],c["blue"],c["swir16"],c["swir22"]
    ndvi=(nir-red)/np.maximum(nir+red,1)
    bsi=((s1+red)-(nir+blu))/np.maximum((s1+red)+(nir+blu),1)
    rb=red/np.maximum(blu,1)
    f={"red":red,"ndvi":-ndvi,"bsi":bsi,"rb":rb,
       "swir1":s1,"clay":s1/np.maximum(s2,1),
       "z_red":local_z(red),"z_ndvi":-local_z(ndvi),"z_rb":local_z(rb),
       "z_bsi":local_z(bsi),"z_swir1":local_z(s1)}
    f["z_red+z_rb"]=f["z_red"]+f["z_rb"]
    f["z_red+z_ndvi"]=f["z_red"]+f["z_ndvi"]
    f["z_bsi+z_rb"]=f["z_bsi"]+f["z_rb"]
    return f

def auc(pos,neg):
    a=np.concatenate([pos,neg]); r=a.argsort().argsort().astype(float)+1
    n1,n0=len(pos),len(neg)
    return (r[:n1].sum()-n1*(n1+1)/2)/(n1*n0)

def rc_of(c,lon,lat):
    x,y=rio_tr("EPSG:4326",c["_crs"],[lon],[lat])
    col,row=(~c["_tr"])*(x[0],y[0]); return int(round(row)),int(round(col))

def main():
    bbox=(min(p[0] for p in PTS)-PAD,min(p[1] for p in PTS)-PAD,
          max(p[0] for p in PTS)+PAD,max(p[1] for p in PTS)+PAD)
    wins={"2026dry":(datetime.date(2026,1,10),datetime.date(2026,3,20)),
          "2026wet":(datetime.date(2026,6,10),datetime.date(2026,8,4)),
          "2025dry":(datetime.date(2025,1,10),datetime.date(2025,3,20)),
          "2023dry":(datetime.date(2023,1,10),datetime.date(2023,3,20)),
          "2019dry":(datetime.date(2019,1,10),datetime.date(2019,3,20))}
    rng=np.random.default_rng(7)
    per_date={}; stacks={}
    for label,(d0,d1) in wins.items():
        for sc in scenes(bbox,f"{d0}T00:00:00Z/{d1}T23:59:59Z"):
            try: c=read(sc,bbox)
            except Exception: continue
            if c is None: continue
            if float(np.isin(c["scl"],(4,5,6,7,11)).mean())<0.97: continue
            F=feats(c)
            # truth pixels: 3x3 max-window around each point (georef slop)
            H,W=F["red"].shape
            pmask=np.zeros((H,W),bool)
            for lo,la in PTS:
                r,cc=rc_of(c,lo,la)
                pmask[max(0,r-1):r+2,max(0,cc-1):cc+2]=True
            valid=np.isin(c["scl"],(4,5,6,7,11))
            nmask=valid & ~ndimage.binary_dilation(pmask,iterations=25)
            ni=np.flatnonzero(nmask.ravel()); ni=rng.choice(ni,size=min(20000,len(ni)),replace=False)
            res={}
            for k,v in F.items():
                res[k]=round(auc(v.ravel()[pmask.ravel()], v.ravel()[ni]),3)
            per_date[label]={"scene":sc["id"],"auc":res}
            stacks[label]={k:v for k,v in F.items() if k in ("red","rb","ndvi","bsi","z_red","z_rb")}
            stacks[label]["_pmask"]=pmask; stacks[label]["_neg"]=ni
            print(f"{label} {sc['id']}",file=sys.stderr)
            break
    keys=list(next(iter(per_date.values()))["auc"].keys())
    print(f"\n{'feature':16s}"+"".join(f"{l:>10s}" for l in per_date)+f"{'mean':>8s}")
    rows=[]
    for k in keys:
        vs=[per_date[l]["auc"][k] for l in per_date]
        rows.append((np.mean(vs),k,vs))
    for mu,k,vs in sorted(rows,reverse=True):
        print(f"{k:16s}"+"".join(f"{v:10.3f}" for v in vs)+f"{mu:8.3f}")

    # multi-date: median of z_red+z_rb across dates (persistence)
    labels=list(stacks)
    common=None
    for l in labels:
        sh=stacks[l]["red"].shape
        common=sh if common is None else (min(common[0],sh[0]),min(common[1],sh[1]))
    def crop(a): return a[:common[0],:common[1]]
    pm=crop(stacks[labels[0]]["_pmask"])
    negmask=np.ones(common,bool); negmask&=~ndimage.binary_dilation(pm,iterations=25)
    ni=np.flatnonzero(negmask.ravel()); ni=rng.choice(ni,size=20000,replace=False)
    print("\nmulti-date composites:")
    for name,fn in {
      "median_z_red":lambda: np.median(np.stack([crop(stacks[l]["z_red"]) for l in labels]),0),
      "median_z_red+z_rb":lambda: np.median(np.stack([crop(stacks[l]["z_red"])+crop(stacks[l]["z_rb"]) for l in labels]),0),
      "min_z_red (all dates bright)":lambda: np.min(np.stack([crop(stacks[l]["z_red"]) for l in labels]),0),
      "median_rb":lambda: np.median(np.stack([crop(stacks[l]["rb"]) for l in labels]),0),
      "trend_red(2026-2019)":lambda: crop(stacks["2026dry"]["z_red"])-crop(stacks["2019dry"]["z_red"]),
    }.items():
        v=fn()
        print(f"  {name:32s} AUC={auc(v.ravel()[pm.ravel()], v.ravel()[ni]):.3f}")
    json.dump(per_date,open("analysis/out/auc.json","w"),indent=1)
main()
