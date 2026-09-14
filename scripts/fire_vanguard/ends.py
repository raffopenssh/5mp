"""Why do chains end? For each chain end: (a) visibility on the following days at that spot,
(b) fraction of already-burned land around it, (c) is there a restart candidate ahead."""
import numpy as np, json, datetime
from scipy.spatial import cKDTree
d0=datetime.date(2020,1,1); A=(datetime.date(2024,8,1)-d0).days
def D(s): return (datetime.date.fromisoformat(s)-d0).days-A
v=np.load("/tmp/fx/vis_2024.npz"); V=v['V']; res=float(v['res']); x0=float(v['x0']); y0=float(v['y0']); ND=V.shape[0]
onset=np.load("/tmp/fx/onset_2024.npy"); ores=0.025
def vis(d,lon,lat):
    if d<0 or d>=ND: return np.nan
    return V[d,int((lat-y0)/res),int((lon-x0)/res)]
def scar_frac(d,lon,lat,km=10):
    r=int(km/2.5); iy=int((lat-4.25)/ores); ix=int((lon-22.7)/ores)
    w=onset[max(iy-r,0):iy+r+1,max(ix-r,0):ix+r+1]
    return np.mean(w<d) if w.size else np.nan   # nan onset (never burned) counts as not burned
def run(name,chains):
    # chains: list of (lon,lat,start,end, coords)
    ends=np.array([[c[0],c[1]] for c in chains]); ed=np.array([c[3] for c in chains]); sd=np.array([c[2] for c in chains])
    starts=np.array([c[4][0][:2] for c in chains])
    vnext=np.array([np.nanmean([vis(e+k,x,y) for k in (1,2,3)]) for (x,y),e in zip(ends,ed)])
    vself=np.array([np.nanmean([vis(s+k,x,y) for k in range(0,e-s+1)]) for (x,y),s,e in zip(ends,sd,ed)])
    scar=np.array([scar_frac(e,x,y) for (x,y),e in zip(ends,ed)])
    scar0=np.array([scar_frac(s,x,y) for (x,y),s in zip(starts,sd)])
    # restart candidates: another chain starting 1..6 days after this end, within 5+8*gap km
    tree=cKDTree(np.c_[starts[:,0]*111*np.cos(np.radians(7.5)),starts[:,1]*111])
    ek=np.c_[ends[:,0]*111*np.cos(np.radians(7.5)),ends[:,1]*111]
    restart=np.zeros(len(chains),bool); restart_dark=np.zeros(len(chains),bool)
    for i in range(len(chains)):
        for j in tree.query_ball_point(ek[i],5+8*6):
            gap=sd[j]-ed[i]
            if 1<=gap<=6 and np.hypot(*(ek[i]-tree.data[j]))<=5+8*gap:
                restart[i]=True
                if np.nanmean([vis(ed[i]+k,*ends[i]) for k in range(1,gap)] or [1])<0.35: restart_dark[i]=True
                break
    print(f"\n{name}: n={len(chains)}")
    print(f"  visibility in the 3 days AFTER the end: median {np.nanmedian(vnext):.2f}  vs during the chain {np.nanmedian(vself):.2f}")
    print(f"  ends followed by a dark spell (V<0.35): {np.nanmean(vnext<0.35)*100:.0f}%   (own days <0.35: {np.nanmean(vself<0.35)*100:.0f}%)")
    print(f"  already-burned land within 10 km of the END: median {np.nanmedian(scar)*100:.0f}%  (>50%: {np.nanmean(scar>0.5)*100:.0f}%)   at the START: median {np.nanmedian(scar0)*100:.0f}%")
    print(f"  a chain restarts within reach 1-6 d later: {restart.mean()*100:.0f}%; of which across a dark spell: {restart_dark.sum()} ({restart_dark.mean()*100:.0f}% of all)")
    return vnext,scar,restart
van=json.load(open("/tmp/fx/van_groups_10.json"))
ch=[(g['trajectory'][-1][0],g['trajectory'][-1][1],D(g['start_date']),D(g['end_date']),g['trajectory']) for g in van]
run("VANGUARD chains (lead>=10)",ch)
lines=json.load(open("/tmp/fx/lines_2024.json"))
ch2=[(l['coords'][-1][0],l['coords'][-1][1],D(l['start']),D(l['end']),l['coords']) for l in lines if D(l['start'])>=30 and D(l['end'])<ND-4]
run("ALL app lines 2024/25",ch2)
run("app lines Oct-Nov (vanguard months)",[c for c in ch2 if 60<=c[2]<120])
run("app lines Dec-Jan",[c for c in ch2 if 120<=c[2]<180])
