"""Does the tracker have skill on vanguard detections (>=15 d ahead of the local season front)?
Run production builder on real vs day-shuffled vanguard-only detections, XSA 2024/25."""
import sys, json, numpy as np, datetime, warnings, time; warnings.filterwarnings("ignore")
sys.path.insert(0,"/home/exedev/5mp/scripts")
import rebuild_fire_trajectories_v5 as B, aoi_lib
from fire_source import load_aoi_fires, date_num
from eval_fire_null import shuffle_days, metrics, ORDER
from scipy import ndimage
LEAD=float(sys.argv[1]) if len(sys.argv)>1 else 15
d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
reg=np.load("/tmp/fx/reg_2024.npy"); fill=np.where(np.isnan(reg),0,reg); w=(~np.isnan(reg)).astype(float)
regs=ndimage.gaussian_filter(fill,10)/np.maximum(ndimage.gaussian_filter(w,10),1e-6); regs[ndimage.gaussian_filter(w,10)<0.5]=np.nan
ny,nx=reg.shape; res=0.025
parks={}; pk=aoi_lib.inject_aoi(parks,"XSA_Study_Area"); B.AOI_IDS.add("XSA_Study_Area")
fires=load_aoi_fires("XSA_Study_Area","2024-09-01","2025-03-31")
lon=np.array([f['longitude'] for f in fires]); lat=np.array([f['latitude'] for f in fires]); day=np.array([D(f['acq_date']) for f in fires])-D("2024-08-01")
ix=np.clip(((lon-22.7)/res).astype(int),0,nx-1); iy=np.clip(((lat-4.25)/res).astype(int),0,ny-1)
lead=regs[iy,ix]-day
sel=lead>=LEAD
van=[f for f,s in zip(fires,sel) if s]
print(f"detections {len(fires):,}; vanguard (>= {LEAD:.0f} d ahead of front): {len(van):,} ({100*len(van)/len(fires):.1f}%)")
res_={}
for tag,fs in [("real",van)]+[(f"null{s}",shuffle_days(van,s)) for s in (7,11)]:
    t=time.time(); g=B.process_park_fires(fs,"XSA_Study_Area",pk["geometry"]); m=metrics(g); res_[tag]=m
    print(tag,f"{time.time()-t:.0f}s",{k:(round(m[k],2) if isinstance(m[k],float) else m[k]) for k in ("groups","links","fires_per_grp","mean_days","p90_days","med_dist_km","long_fronts","fires_in_long")})
    if tag=="real": json.dump(g,open(f"/tmp/fx/van_groups_{int(LEAD)}.json","w"),default=str)
r=res_["real"]; n={k:np.mean([res_[t][k] for t in res_ if t!="real"]) for k in ORDER}
print("skill = 1 - null/real:",{k:round(1-n[k]/r[k],2) for k in ("links","fires_per_grp","mean_days","p90_days","long_fronts","fires_in_long") if r[k]})
