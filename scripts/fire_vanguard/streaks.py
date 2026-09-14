"""Vanguard streaks: connected linear features of cells that burned >=EARLY days
before their 60-km surroundings. Per streak: along-axis ordering vs first-burn
date (Spearman rho) => direction, speed; null = days shuffled within month."""
import numpy as np, sys, json, datetime
from scipy import ndimage, stats
z=np.load("/tmp/fx/xsa.npz"); LAT,LON,DAY=z['lat'],z['lon'],z['day']
d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
def ds(n): return (d0+datetime.timedelta(int(n))).isoformat()
LAT0=7.6; KX=111.32*np.cos(np.radians(LAT0)); KY=110.57; X0,Y0=22.7,4.25
CELL=2.5; EARLY=15; MINCELLS=10; REGWIN=int(60/CELL)|1

BLOCK=int(__import__('os').environ.get('BLOCK','0'))  # 0 = calendar month
def shuffled_days(day,seed):
    rnd=np.random.RandomState(seed)
    rng=np.arange(day.min(),day.max()+1)
    months=np.array([ds(d)[:7] for d in rng]) if not BLOCK else (rng-rng.min())//BLOCK
    remap=rng.copy()
    for m in np.unique(months):
        idx=np.where(months==m)[0]; remap[idx]=remap[rnd.permutation(idx)]
    return remap[day-day.min()]

def onset_grid(a,b,seed=0):
    day=DAY if not seed else shuffled_days(DAY,seed)
    m=(day>=D(a))&(day<D(b))
    x=(LON[m]-X0)*KX; y=(LAT[m]-Y0)*KY; ix=(x/CELL).astype(int); iy=(y/CELL).astype(int)
    nx=int(8.6*KX/CELL)+2; ny=int(6.75*KY/CELL)+2
    dd=day[m]-D(a)
    onset=np.full((ny,nx),np.nan); o=np.argsort(-dd); onset[iy[o],ix[o]]=dd[o]
    cnt=np.zeros((ny,nx)); np.add.at(cnt,(iy,ix),1)
    return onset,cnt

def streaks(onset,cnt,min_det=2):
    tmp=np.where(np.isnan(onset),9999,onset)
    reg=ndimage.percentile_filter(tmp,20,size=REGWIN); reg=np.where(reg>=9999,np.nan,reg)
    anom=onset-reg
    van=(anom<=-EARLY)&(cnt>=min_det)
    # bridge 1-cell gaps
    lab,n=ndimage.label(ndimage.binary_dilation(van,iterations=1),structure=np.ones((3,3)))
    lab=lab*van
    out=[]
    idx=ndimage.find_objects(lab)
    for i,sl in enumerate(idx,1):
        if sl is None: continue
        yy,xx=np.where(lab[sl]==i); yy=yy+sl[0].start; xx=xx+sl[1].start
        if len(yy)<MINCELLS: continue
        pts=np.c_[xx*CELL,yy*CELL]; c=pts.mean(0); u,s,vt=np.linalg.svd(pts-c,full_matrices=False)
        elong=s[0]/max(s[1],1e-9); length=(pts-c)@vt[0]; L=length.max()-length.min()
        if L<25: continue
        t=onset[yy,xx]; ta=anom[yy,xx]
        rho,p=stats.spearmanr(length,ta)
        if np.isnan(rho): continue
        # speed: robust slope km/day along axis
        slope=stats.theilslopes(length,t)[0] if len(t)>=5 else np.nan
        if rho<0: vt0=-vt[0]
        else: vt0=vt[0]
        # ordered path: sort by projection, thin to ~10 km steps
        order=np.argsort(length*np.sign(rho) if rho!=0 else length)
        out.append(dict(n=int(len(yy)),length_km=float(L),elong=float(elong),rho=float(rho),p=float(p),
            speed_kmd=float(abs(slope)) if not np.isnan(slope) else None,
            t0=float(np.percentile(t,10)),t1=float(np.percentile(t,90)),
            lon=float(c[0]/KX+X0),lat=float(c[1]/KY+Y0),dir_deg=float(np.degrees(np.arctan2(vt0[0],vt0[1]))%360),
            path=[[float(pts[j,0]/KX+X0),float(pts[j,1]/KY+Y0),float(t[j])] for j in order],
            anom_med=float(np.nanmedian(anom[yy,xx]))))
    return out,anom

if __name__=="__main__":
    season=sys.argv[1]; seed=int(sys.argv[2]) if len(sys.argv)>2 else 0
    a,b=f"{season}-08-01",f"{int(season)+1}-08-01"
    onset,cnt=onset_grid(a,b,seed); S,anom=streaks(onset,cnt)
    json.dump(dict(season=season,seed=seed,day0=a,streaks=S),open(f"streaks_{season}_s{seed}.json","w"))
    np.save(f"anom_{season}_s{seed}.npy",anom)
    rho=np.array([s['rho'] for s in S]); p=np.array([s['p'] for s in S]); L=np.array([s['length_km'] for s in S])
    print(f"season {season} seed {seed}: streaks {len(S)}, >=50km {int((L>=50).sum())}, median|rho| {np.median(np.abs(rho)):.2f}, p<0.01: {(p<0.01).sum()}, p<0.001: {(p<0.001).sum()}, |rho|>0.5&n>=20: {((np.abs(rho)>0.5)&(np.array([s['n'] for s in S])>=20)).sum()}")
