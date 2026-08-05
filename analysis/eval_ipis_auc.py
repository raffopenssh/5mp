"""Calibrate mining-pit features against IPIS visited-ASM-site truth (CAR+DRC).

IPIS Research publishes GPS coordinates of ~8,000 physically visited artisanal
mining sites (CAR n=914, DRC n=7,163). This is far better calibration data than
a handful of hand-digitised points, and it spans forest (DRC) and savanna (CAR).

For a random sample of sites we pull one clear dry-season Sentinel-2 scene,
compute candidate features, and report AUC (site pixels vs local background).
Positional accuracy of IPIS points is village/pit-cluster level, so we take the
best value in a +/-k pixel window and use a large negative exclusion radius.
"""
import csv,json,os,sys,math,random,datetime,urllib.request
import numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds, transform as rio_tr
from scipy import ndimage
os.environ["AWS_NO_SIGN_REQUEST"]="YES"
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN","EMPTY_DIR")
STAC="https://earth-search.aws.element84.com/v1/search"
BANDS=("blue","green","red","nir","swir16","swir22","scl")
PAD=0.020          # ~2.2 km AOI
WIN=3              # +/-3 px = +/-30 m around the GPS point
EXCL=30            # background must be >300 m from the site

def scenes(bbox,dt,cloud=25,limit=60):
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
    ndwi=(grn-nir)/np.maximum(grn+nir,1)
    mndwi=(grn-s1)/np.maximum(grn+s1,1)
    rb=red/np.maximum(blu,1)
    f={"red":red,"neg_ndvi":-ndvi,"bsi":bsi,"rb":rb,"swir1":s1,
       "mndwi":mndwi,"ndwi":ndwi,
       "z_red":local_z(red),"z_neg_ndvi":-local_z(ndvi),"z_bsi":local_z(bsi),
       "z_rb":local_z(rb),"z_swir1":local_z(s1)}
    f["z_bsi+z_rb"]=f["z_bsi"]+f["z_rb"]
    f["z_red+z_neg_ndvi"]=f["z_red"]+f["z_neg_ndvi"]
    f["z_bsi+z_neg_ndvi"]=f["z_bsi"]+f["z_neg_ndvi"]
    f["water_turbid"]=np.where(mndwi>0,red,np.nan)
    return f

def auc(pos,neg):
    pos=pos[np.isfinite(pos)];neg=neg[np.isfinite(neg)]
    if len(pos)<3 or len(neg)<50: return None
    a=np.concatenate([pos,neg]);r=a.argsort().argsort().astype(float)+1
    n1,n0=len(pos),len(neg)
    return (r[:n1].sum()-n1*(n1+1)/2)/(n1*n0)

def load_sites():
    out=[]
    for f,cc in (("caf","CAF"),("cod","COD")):
        for r in csv.DictReader(open(f"data/ipis/{f}_mines_ipis.csv")):
            try: lo,la=float(r["longitude"]),float(r["latitude"])
            except Exception: continue
            mins=r.get("minerals") or r.get("mineral1") or ""
            out.append({"cc":cc,"lon":lo,"lat":la,"name":r.get("name"),
                        "minerals":mins,"date":r.get("visit_date"),
                        "workers":r.get("workers_numb")})
    return out

def main():
    n=int(sys.argv[1]) if len(sys.argv)>1 else 40
    sites=load_sites()
    random.seed(11)
    # prefer recent visits and gold (alluvial -> river impact)
    sites=[s for s in sites if (s["date"] or "")>="2015" and "Or" in (s["minerals"] or "")]
    random.shuffle(sites)
    rng=np.random.default_rng(5)
    accum={}
    done=0
    for s in sites:
        if done>=n: break
        bbox=(s["lon"]-PAD,s["lat"]-PAD,s["lon"]+PAD,s["lat"]+PAD)
        yr=datetime.date.today().year
        picked=None
        for y in (yr,yr-1,yr-2):
            for sc in scenes(bbox,f"{y}-01-05T00:00:00Z/{y}-03-25T23:59:59Z"):
                try: c=read(sc,bbox)
                except Exception: continue
                if c is None: continue
                if float(np.isin(c["scl"],(4,5,6,7,11)).mean())<0.95: continue
                picked=(sc,c);break
            if picked: break
        if not picked:
            print(f"skip {s['name']} (no clear scene)",file=sys.stderr); continue
        sc,c=picked
        F=feats(c);H,W=c["red"].shape
        x,y=rio_tr("EPSG:4326",c["_crs"],[s["lon"]],[s["lat"]])
        col,row=(~c["_tr"])*(x[0],y[0]);row,col=int(round(row)),int(round(col))
        if not(WIN<=row<H-WIN and WIN<=col<W-WIN): continue
        pm=np.zeros((H,W),bool);pm[row-WIN:row+WIN+1,col-WIN:col+WIN+1]=True
        valid=np.isin(c["scl"],(4,5,6,7,11))
        nm=valid&~ndimage.binary_dilation(pm,iterations=EXCL)
        ni=np.flatnonzero(nm.ravel());ni=rng.choice(ni,size=min(8000,len(ni)),replace=False)
        rec={}
        for k,v in F.items():
            a=auc(v.ravel()[pm.ravel()],v.ravel()[ni])
            if a is not None: accum.setdefault(k,[]).append(a); rec[k]=round(a,2)
        done+=1
        print(f"[{done}/{n}] {s['cc']} {s['name']} {sc['properties']['datetime'][:10]} "
              +" ".join(f"{k}={v}" for k,v in sorted(rec.items(),key=lambda kv:-kv[1])[:4]),flush=True)
    print("\n== mean AUC over",done,"IPIS gold sites")
    for k,v in sorted(accum.items(),key=lambda kv:-np.mean(kv[1])):
        arr=np.array(v)
        print(f"{k:22s} mean={arr.mean():.3f} median={np.median(arr):.3f} "
              f"frac>0.6={np.mean(arr>0.6):.2f} n={len(arr)}")
    json.dump({k:list(map(float,v)) for k,v in accum.items()},
              open("analysis/out/ipis_auc.json","w"),indent=1)
main()
