"""Auto-vanguard v3: particle-physics style track following on the daily clusters.
 - Kalman filter state (x,y,vx,vy) in km; process noise = herder/front changing heading;
 - gate = Mahalanobis distance to the PREDICTED position (tightens as the track establishes);
 - a day with no hit costs a HOLE only where the sky was seen (self-derived visibility map);
   a cloudy day (dead layer) costs nothing;
 - a track is lost when holes > MAX_HOLES or its position uncertainty exceeds LOST_KM;
 - seeding only ahead of the season front (lead >= SEED_LEAD); in-season clusters may only continue a track.
Everything downstream (chain_tracks, group emission, evidence) is production code, untouched."""
import sys, json, numpy as np, datetime, warnings, time, math; warnings.filterwarnings("ignore")
sys.path.insert(0,"/home/exedev/5mp/scripts")
import rebuild_fire_trajectories_v5 as B, aoi_lib
from fire_source import load_aoi_fires, date_num
from eval_fire_null import shuffle_days, metrics, ORDER
from scipy import ndimage
from collections import defaultdict
d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
A=D("2024-08-01"); TA=date_num("2024-08-01")
reg=np.load("/tmp/fx/reg_2024.npy"); fill=np.where(np.isnan(reg),0,reg); w=(~np.isnan(reg)).astype(float)
regs=ndimage.gaussian_filter(fill,10)/np.maximum(ndimage.gaussian_filter(w,10),1e-6); regs[ndimage.gaussian_filter(w,10)<0.5]=np.nan
ny,nx=reg.shape; res=0.025
def lead_of(lon,lat,day):
    ix=np.clip(((np.asarray(lon)-22.7)/res).astype(int),0,nx-1); iy=np.clip(((np.asarray(lat)-4.25)/res).astype(int),0,ny-1); return regs[iy,ix]-day
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
        v=ss/np.maximum(ws,1e-9); v[ws<0.02]=1.0; V[d]=v
    return V
CFG=dict(vis=None, seed_lead=None, dark=0.35, max_holes=3, max_cal_gap=10, lost_km=30.0,
         sig_v0=5.0, q_v=1.5, r_floor=1.5, chi2_gate=9.21, use_holes=True, gap_in_season=None)
STATS=defaultdict(int)
KM_LAT=110.57
class KTrack(B.Track):
    __slots__=('x','P','holes','hits','lat0','kmlon')
    def __init__(self,dc):
        super().__init__(dc); lon,lat=dc['centroid']; self.lat0=lat; self.kmlon=111.32*math.cos(math.radians(lat))
        self.x=np.array([lon*self.kmlon,lat*KM_LAT,0.0,0.0]); s=CFG['sig_v0']
        self.P=np.diag([CFG['r_floor']**2,CFG['r_floor']**2,s*s,s*s]); self.holes=0; self.hits=1
    def km(self,lon,lat): return np.array([lon*self.kmlon,lat*KM_LAT])
    def propagate(self,g):
        F=np.eye(4); F[0,2]=F[1,3]=g; q=CFG['q_v']**2*g
        Q=np.zeros((4,4)); Q[2,2]=Q[3,3]=q; Q[0,0]=Q[1,1]=q*g*g/3; Q[0,2]=Q[2,0]=Q[1,3]=Q[3,1]=q*g/2
        return F@self.x, F@self.P@F.T+Q
    def kf_update(self,dc):
        g=max(dc['t']-self.last_t,1/24); xp,Pp=self.propagate(g)
        z=self.km(*dc['centroid']); R=np.eye(2)*meas_var(dc)
        H=np.zeros((2,4)); H[0,0]=H[1,1]=1; S=H@Pp@H.T+R; K=Pp@H.T@np.linalg.inv(S)
        self.x=xp+K@(z-H@xp); self.P=(np.eye(4)-K@H)@Pp; self.hits+=1; self.holes=0
def meas_var(dc):
    n=dc['n']
    if n<3: return CFG['r_floor']**2*2
    xs=np.array([[f['longitude'],f['latitude']] for f in dc['fires']]); sp=np.std(xs,axis=0).mean()*105
    return max(CFG['r_floor']**2, sp*sp/n+1.0)
def build_tracks_v3(day_clusters):
    by_t=defaultdict(list)
    for dc in day_clusters: by_t[dc['t']].append(dc)
    active=[]; closed=[]; lr_model=B.load_link_lr(); V=CFG['vis']
    def visible(t,xkm,tr):
        if V is None or not CFG['use_holes']: return True
        d=int(round(t))-TA
        if d<0 or d>=V.shape[0]: return True
        lon=xkm[0]/tr.kmlon; lat=xkm[1]/KM_LAT
        return V[d,min(max(int((lat-VY0)/VRES),0),VNY-1),min(max(int((lon-VX0)/VRES),0),VNX-1)]>=CFG['dark']
    last_t=None
    for t in sorted(by_t.keys()):
        still=[]
        for tr in active:
            gap=t-tr.last_t
            # holes accumulated on the days between last hit and today (count each day once)
            if last_t is not None and tr.last_t<last_t:
                pass
            xp,_=tr.propagate(gap)
            nh=0
            for k in range(1,int(round(gap))):
                xk,_=tr.propagate(k)
                if visible(tr.last_t+k,xk,tr): nh+=1
            _,Pp=tr.propagate(gap); pos_sig=math.sqrt(max(Pp[0,0],Pp[1,1]))
            stale = nh>CFG['max_holes'] or gap>CFG['max_cal_gap'] or pos_sig>CFG['lost_km']
            if CFG['gap_in_season'] is not None and not stale and gap>CFG['gap_in_season']:
                ld=lead_of(tr.points[-1][0],tr.points[-1][1],int(round(tr.last_t))-TA)
                if not (ld>=CFG['seed_lead']): stale=True      # in the dense field only consecutive-day links carry information
            (closed if stale else still).append(tr)
        active=still; clusters=by_t[t]; costs,llrs={},{}
        C=np.array([dc['centroid'] for dc in clusters]) if clusters else np.zeros((0,2))
        MV=np.array([meas_var(dc) for dc in clusters])
        for ti,tr in enumerate(active):
            gap=t-tr.last_t; xp,Pp=tr.propagate(gap); lon0,lat0=tr.points[-1][0],tr.points[-1][1]
            Z=np.c_[C[:,0]*tr.kmlon,C[:,1]*KM_LAT]-xp[:2]
            # Mahalanobis with S = Pp[:2,:2] + R_i (R isotropic -> closed form 2x2 inverse per cluster)
            a=Pp[0,0]+MV; b=Pp[0,1]; d=Pp[1,1]+MV; det=a*d-b*b
            M2=(d*Z[:,0]**2-2*b*Z[:,0]*Z[:,1]+a*Z[:,1]**2)/det
            for ci in np.where(M2<=CFG['chi2_gate'])[0]:
                dc=clusters[ci]; m2=float(M2[ci])
                lon1,lat1=dc['centroid']; step=B.haversine(lon0,lat0,lon1,lat1)
                if tr.last_bearing is not None and tr.moved_km>3.0 and step>B.TURN_MIN_STEP_KM:
                    if B.bearing_diff(tr.last_bearing,B.bearing(lon0,lat0,lon1,lat1))>B.TURN_LIMIT_DEG: continue
                mp=B._min_pair_km(tr.last_dc,dc)/max(gap,B.GATE_MIN_GAP_DAYS); llrs[(ti,ci)]=B.link_llr(mp,lr_model)
                costs[(ti,ci)]=m2+0.02*B._mass_penalty(tr.last_n,dc['n'])
        pairs=B._solve_assignment(costs,len(active),len(clusters)); margins=B._link_margins(costs,pairs); used=set()
        for ti,ci in pairs:
            tr=active[ti]; g=int(round(t-tr.last_t)); STATS['links']+=1; STATS[f'g{min(g,5)}']+=1
            if g>=2: STATS['links_gap2+']+=1
            if g>B.MAX_GAP_DAYS: STATS['links_gap>3']+=1
            tr.kf_update(clusters[ci]); tr.extend(clusters[ci],margins[(ti,ci)],llrs[(ti,ci)]); used.add(ci)
        for ci,dc in enumerate(clusters):
            if ci in used: continue
            if CFG['seed_lead'] is not None:
                lo,la=dc['centroid']
                if not (lead_of(lo,la,int(round(t))-TA)>=CFG['seed_lead']): continue
            active.append(KTrack(dc))
        last_t=t
    closed.extend(active); return closed
_prod_build=B.build_tracks
B.build_tracks=build_tracks_v3
def run(tag,fs,pk,allfs=None,**cfg):
    CFG.update(vis=None,seed_lead=None,use_holes=True); CFG.update(cfg)
    if cfg.get('use_vis'): CFG['vis']=visibility(allfs)
    STATS.clear()
    t=time.time(); g=B.process_park_fires(fs,"XSA_Study_Area",pk["geometry"]); m=metrics(g); m['bridge2']=STATS['links_gap2+']; m['bridge4']=STATS['links_gap>3']; m['gaphist']=[STATS[f'g{k}'] for k in range(1,6)]
    print(f"  {tag:8s} {time.time()-t:4.0f}s",{k:(round(m[k],2) if isinstance(m[k],float) else m[k]) for k in ("groups","links","bridge2","bridge4","fires_per_grp","mean_days","p90_days","med_dist_km","long_fronts","fires_in_long")},flush=True)
    return g,m
def experiment(name,fires,idx,pk,**cfg):
    out={}
    for tag,allf in [("real",fires)]+[(f"null{s}",shuffle_days(fires,s)) for s in (7,11)]:
        fs=[allf[i] for i in idx]; g,m=run(tag,fs,pk,allfs=allf,**cfg); out[tag]=m
        if tag=="real": json.dump(g,open(f"/tmp/fx/v3_{name}_groups.json","w"),default=str)
    r=out["real"]; n={k:np.mean([out[t][k] for t in out if t!="real"]) for k in list(ORDER)+['bridge2','bridge4']}
    print("   links by gap days 1..5+  real:",r['gaphist']," null:",[int(np.mean([out[t]['gaphist'][k] for t in out if t!='real'])) for k in range(5)])
    print(f"RESULT {name}: bridge>=2d {r['bridge2']} vs {n['bridge2']:.0f}, bridge>3d {r['bridge4']} vs {n['bridge4']:.0f} | links {r['links']} vs {n['links']:.0f} skill {1-n['links']/r['links']:.2f} | groups {r['groups']} | med_km {r['med_dist_km']:.0f} vs {n['med_dist_km']:.0f} | mean_days {r['mean_days']:.1f} vs {n['mean_days']:.1f} | long {r['long_fronts']} vs {n['long_fronts']:.0f} | fires_in_long {r['fires_in_long']} vs {n['fires_in_long']:.0f}",flush=True)
if __name__=="__main__":
    parks={}; pk=aoi_lib.inject_aoi(parks,"XSA_Study_Area"); B.AOI_IDS.add("XSA_Study_Area")
    fires=load_aoi_fires("XSA_Study_Area","2024-09-01","2025-03-31")
    lon=np.array([f['longitude'] for f in fires]); lat=np.array([f['latitude'] for f in fires]); day=np.array([D(f['acq_date']) for f in fires])-A
    lead=lead_of(lon,lat,day); LEAD=10
    idx=[i for i,l in enumerate(lead) if l>=LEAD]
    which=sys.argv[1:] or ["KF","KFV"]
    if "KF" in which:  experiment("KF_noholes",fires,idx,pk,use_vis=False)            # KF gate only, gaps counted as production (every day a hole)
    if "KFV" in which: experiment("KF_vis",fires,idx,pk,use_vis=True)                  # + cloud days are dead layers
    if "KFV6" in which: experiment("KF_vis_h6",fires,idx,pk,use_vis=True,max_holes=6)
    if "G2" in which: experiment("KF_gap2",fires,idx,pk,use_vis=False,max_cal_gap=2)
    if "PROD" in which:
        B.build_tracks=_prod_build
        experiment("PROD_gap3",fires,idx,pk,use_vis=False)
        B.MAX_GAP_DAYS=2; experiment("PROD_gap2",fires,idx,pk,use_vis=False); B.MAX_GAP_DAYS=3
        B.build_tracks=build_tracks_v3
    if "SEED" in which:
        idx2=[i for i,l in enumerate(lead) if l>=-10]
        experiment("KF_seed_regime",fires,idx2,pk,use_vis=False,max_cal_gap=3,seed_lead=LEAD,gap_in_season=1)
