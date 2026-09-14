"""Auto-vanguard v2 experiment: production tracker + (a) visibility-aware gap budget,
(b) seeding only ahead of the front, continuation allowed into the season. Real vs day-shuffled."""
import sys, json, numpy as np, datetime, warnings, time, math; warnings.filterwarnings("ignore")
sys.path.insert(0,"/home/exedev/5mp/scripts")
import rebuild_fire_trajectories_v5 as B, aoi_lib
from fire_source import load_aoi_fires, date_num
TA=None
from eval_fire_null import shuffle_days, metrics, ORDER
from scipy import ndimage
from collections import defaultdict
LEAD=10; d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
A=D("2024-08-01")
reg=np.load("/tmp/fx/reg_2024.npy"); fill=np.where(np.isnan(reg),0,reg); w=(~np.isnan(reg)).astype(float)
regs=ndimage.gaussian_filter(fill,10)/np.maximum(ndimage.gaussian_filter(w,10),1e-6); regs[ndimage.gaussian_filter(w,10)<0.5]=np.nan
ny,nx=reg.shape; res=0.025
def lead_of(lon,lat,day):
    ix=np.clip(((lon-22.7)/res).astype(int),0,nx-1); iy=np.clip(((lat-4.25)/res).astype(int),0,ny-1); return regs[iy,ix]-day

# ---- visibility map from a fire list (self-derived exposure map) ----
VRES=0.1; VX0,VY0=22.7,4.25; VNX=int((31.3-VX0)/VRES)+1; VNY=int((11.0-VY0)/VRES)+1
def visibility(fires, ND=243):
    lon=np.array([f['longitude'] for f in fires]); lat=np.array([f['latitude'] for f in fires]); dd=np.array([D(f['acq_date']) for f in fires])-A
    ok=(dd>=0)&(dd<ND); lon,lat,dd=lon[ok],lat[ok],dd[ok]
    cnt=np.zeros((ND,VNY,VNX),np.int32); np.add.at(cnt,(dd,((lat-VY0)/VRES).astype(int),((lon-VX0)/VRES).astype(int)),1)
    pres=cnt>0; before=np.zeros_like(pres); after=np.zeros_like(pres)
    for k in (1,2,3): before[k:]|=pres[:-k]; after[:-k]|=pres[k:]
    ea=before&after; V=np.ones((ND,VNY,VNX),np.float32)
    for d in range(ND):
        ws=ndimage.gaussian_filter(ea[d].astype(float),5); ss=ndimage.gaussian_filter((ea[d]&pres[d]).astype(float),5)
        v=ss/np.maximum(ws,1e-9); v[ws<0.02]=1.0; V[d]=v    # where nothing was expected we assume the sky was seen (conservative)
    return V

# ---- patched build_tracks ----
CFG=dict(vis=None, seed_lead=None, max_cal_gap=10, dark=0.35, gate_dark=8.0)
def build_tracks_v2(day_clusters):
    by_t=defaultdict(list)
    for dc in day_clusters: by_t[dc['t']].append(dc)
    active=[]; closed=[]; lr_model=B.load_link_lr(); llr_max=max(lr_model[1]) if lr_model else 0.0
    V=CFG['vis']
    def visible(t,lon,lat):
        if V is None: return True
        d=int(round(t))-TA
        if d<0 or d>=V.shape[0]: return True
        return V[d,min(max(int((lat-VY0)/VRES),0),VNY-1),min(max(int((lon-VX0)/VRES),0),VNX-1)]>=CFG['dark']
    for t in sorted(by_t.keys()):
        still=[]
        for tr in active:
            gap=t-tr.last_t
            if V is None: stale = gap>B.MAX_GAP_DAYS; tr.seen_gap=gap
            else:
                # gap budget counts only days on which the sky over the track was actually seen
                seen=sum(1 for k in range(1,int(gap)+1) if visible(tr.last_t+k,*tr.predict(tr.last_t+k)[:2]))
                stale = seen>B.MAX_GAP_DAYS or gap>CFG['max_cal_gap']; tr.seen_gap=seen
            (closed if stale else still).append(tr)
        active=still
        clusters=by_t[t]; costs,llrs={},{}
        for ti,tr in enumerate(active):
            gap=t-tr.last_t; pred=tr.predict(t)
            seen=getattr(tr,'seen_gap',gap); dark_days=gap-seen
            gate=B.BASE_LINK_KM+max(seen,B.GATE_MIN_GAP_DAYS)*B.SPREAD_KM_PER_DAY+dark_days*CFG['gate_dark']
            lon0,lat0=tr.points[-1][0],tr.points[-1][1]
            for ci,dc in enumerate(clusters):
                lon1,lat1=dc['centroid']; dist=B.haversine(pred[0],pred[1],lon1,lat1)
                if dist>gate: continue
                step=B.haversine(lon0,lat0,lon1,lat1)
                if tr.last_bearing is not None and tr.moved_km>3.0 and step>B.TURN_MIN_STEP_KM:
                    if B.bearing_diff(tr.last_bearing,B.bearing(lon0,lat0,lon1,lat1))>B.TURN_LIMIT_DEG: continue
                mp=B._min_pair_km(tr.last_dc,dc)/max(gap,B.GATE_MIN_GAP_DAYS); llr=B.link_llr(mp,lr_model)
                llrs[(ti,ci)]=llr; costs[(ti,ci)]=dist+B._mass_penalty(tr.last_n,dc['n'])
        pairs=B._solve_assignment(costs,len(active),len(clusters)); margins=B._link_margins(costs,pairs)
        used=set()
        for ti,ci in pairs: active[ti].extend(clusters[ci],margins[(ti,ci)],llrs[(ti,ci)]); used.add(ci)
        for ci,dc in enumerate(clusters):
            if ci in used: continue
            if CFG['seed_lead'] is not None:
                lo,la=dc['centroid']; d=int(round(t))-TA
                if not (lead_of(np.array([lo]),np.array([la]),d)[0]>=CFG['seed_lead']): continue   # in-season clusters may join, never seed
            active.append(B.Track(dc))
    closed.extend(active); return closed
B.build_tracks=build_tracks_v2

def run(tag,fs,pk,allfs=None,**cfg):
    CFG.update(vis=None,seed_lead=None); CFG.update(cfg)
    if cfg.get('use_vis'): CFG['vis']=visibility(allfs)
    t=time.time(); g=B.process_park_fires(fs,"XSA_Study_Area",pk["geometry"]); m=metrics(g)
    print(f"  {tag:8s} {time.time()-t:4.0f}s",{k:(round(m[k],2) if isinstance(m[k],float) else m[k]) for k in ("groups","links","fires_per_grp","mean_days","p90_days","med_dist_km","long_fronts","fires_in_long")},flush=True)
    return g,m

TA=date_num("2024-08-01")
if __name__=="__main__":
    parks={}; pk=aoi_lib.inject_aoi(parks,"XSA_Study_Area"); B.AOI_IDS.add("XSA_Study_Area")
    fires=load_aoi_fires("XSA_Study_Area","2024-09-01","2025-03-31")
    lon=np.array([f['longitude'] for f in fires]); lat=np.array([f['latitude'] for f in fires]); day=np.array([D(f['acq_date']) for f in fires])-A
    lead=lead_of(lon,lat,day)
    which=sys.argv[1] if len(sys.argv)>1 else "R0"
    band=float(sys.argv[2]) if len(sys.argv)>2 else None   # field = lead >= band (continuation depth)
    thr=LEAD if which in ("R0","R1","SWEEP") else band
    idx=[i for i,l in enumerate(lead) if l>=thr]
    print(which,"field:",len(idx),"detections")
    if which=="SWEEP":
        for mcg in (5,7,10):
            for gd in (0.0,2.0,4.0,8.0):
                out={}
                for tag,allf in [("real",fires)]+[(f"null{s}",shuffle_days(fires,s)) for s in (7,11)]:
                    fs=[allf[i] for i in idx]; g,m=run(tag,fs,pk,allfs=allf,use_vis=True,max_cal_gap=mcg,gate_dark=gd); out[tag]=m
                r=out["real"]; n={k:np.mean([out[t][k] for t in out if t!="real"]) for k in ORDER}
                print(f"SWEEP max_cal_gap={mcg} gate_dark={gd}: links {r['links']} vs {n['links']:.0f} skill {1-n['links']/r['links']:.2f} | med_km {r['med_dist_km']:.0f} vs {n['med_dist_km']:.0f} | long {r['long_fronts']} vs {n['long_fronts']:.0f} | mean_days {r['mean_days']:.1f} vs {n['mean_days']:.1f}",flush=True)
        sys.exit()
    cfg={"R0":dict(),"R1":dict(use_vis=True),"R2":dict(seed_lead=LEAD),"R3":dict(use_vis=True,seed_lead=LEAD)}[which]
    out={}
    for tag,allf in [("real",fires)]+[(f"null{s}",shuffle_days(fires,s)) for s in (7,11)]:
        fs=[allf[i] for i in idx]
        g,m=run(tag,fs,pk,allfs=allf,**cfg); out[tag]=m
        if tag=="real": json.dump(g,open(f"/tmp/fx/v2_{which}_groups.json","w"),default=str)
    r=out["real"]; n={k:np.mean([out[t][k] for t in out if t!="real"]) for k in ORDER}
    print("  skill:",{k:round(1-n[k]/r[k],2) for k in ("links","mean_days","med_dist_km","long_fronts","fires_in_long") if r[k]})
