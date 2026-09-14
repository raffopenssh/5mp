"""Synthetic tracking (shift-and-stack) of fresh-ignition events.
Frames = daily fresh-ignition images (cell burns after >=QUIET days without fire).
For each sliding window of W days and velocity hypothesis v, stack frames shifted
by -v*k. A source moving at v piles its ignitions into one cell; a random field
does not. Significance = Poisson tail vs the same stack of a smoothed background.
"""
import numpy as np, datetime, sys, json, time
from scipy import ndimage, stats
from scipy.special import gammainc
z=np.load("/tmp/fx/xsa.npz"); LAT,LON,DAY=z['lat'],z['lon'],z['day']
d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
def ds(n): return (d0+datetime.timedelta(int(n))).isoformat()

CELL=float(sys.argv[1]) if len(sys.argv)>1 else 5.0     # km
QUIET=14; W=7; TOL=1   # tolerance = (2*TOL+1) cells
SPEEDS=np.arange(6,42,2.0)   # km/day
SEASON=(sys.argv[2] if len(sys.argv)>2 else "2024-10-01", sys.argv[3] if len(sys.argv)>3 else "2025-02-28")
SHUFFLE=int(sys.argv[4]) if len(sys.argv)>4 else 0
LAT0=7.6; KX=111.32*np.cos(np.radians(LAT0)); KY=110.57
X0,Y0=22.7,4.25

def to_xy(lon,lat): return (lon-X0)*KX,(lat-Y0)*KY
def events(day_from,day_to,shuffle_seed=0):
    x,y=to_xy(LON,LAT); ix=(x/CELL).astype(np.int32); iy=(y/CELL).astype(np.int32)
    nx,ny=ix.max()+1,iy.max()+1
    cid=iy.astype(np.int64)*nx+ix
    day=DAY.copy()
    if shuffle_seed:
        rnd=np.random.RandomState(shuffle_seed)
        # permute calendar days within each month (same null as eval_fire_null)
        months=np.array([ds(d)[:7] for d in range(day.min(),day.max()+1)])
        remap=np.arange(day.min(),day.max()+1)
        for m in np.unique(months):
            idx=np.where(months==m)[0]; remap[idx]=remap[rnd.permutation(idx)]
        day=remap[day-day.min()]
    key=np.unique(cid*100000+day)            # unique (cell,day)
    c=key//100000; d=key%100000
    prev=np.empty_like(d); prev[0]=-10**6; same=c[1:]==c[:-1]
    prev[1:]=np.where(same,d[:-1],-10**6)
    fresh=(d-prev)>=QUIET
    m=fresh&(d>=day_from)&(d<=day_to)
    return c[m]%nx, c[m]//nx, d[m], nx, ny

def poisson_logsf(n,lam):
    # -log10 P(N>=n) ; gammaincc(n,lam)=P(N>=n) for Poisson
    p=gammainc(np.maximum(n,1e-9),lam); return -np.log10(np.maximum(p,1e-300))

def run(shuffle_seed=0):
    df,dt=D(SEASON[0]),D(SEASON[1])
    ex,ey,ed,nx,ny=events(df,dt,shuffle_seed)
    print("fresh-ignition events",len(ex),"grid",nx,ny,file=sys.stderr)
    frames={}
    for d in range(df,dt+1):
        img=np.zeros((ny,nx),np.float32); m=ed==d; np.add.at(img,(ey[m],ex[m]),1); frames[d]=np.minimum(img,1)
    box=np.ones((2*TOL+1,2*TOL+1),np.float32)
    # background rate per cell-day: Gaussian-smoothed 15-day mean, sigma ~ 30 km
    sig=30.0/CELL
    hyps=[]
    for s in SPEEDS:
        nd=int(np.clip(round(2*np.pi*s*W/(CELL*(2*TOL+1))),12,200))
        for k in range(nd):
            th=2*np.pi*k/nd; hyps.append((s,th,s*np.cos(th),s*np.sin(th)))
    print("hypotheses",len(hyps),file=sys.stderr)
    cands=[]
    t0=time.time()
    for w0 in range(df,dt-W+2,3):
        days=list(range(w0,w0+W))
        bg_days=range(max(df,w0-4),min(dt,w0+W+4)+1)
        bg=sum(frames[d] for d in bg_days)/len(list(bg_days))
        bg=ndimage.gaussian_filter(bg,sig)*box.sum()     # expected per (tolerance box)-day
        bg=np.maximum(bg,1e-4)
        best_score=np.zeros((ny,nx),np.float32); best_h=np.full((ny,nx),-1,np.int32); best_n=np.zeros((ny,nx),np.float32)
        for hi,(s,th,vx,vy) in enumerate(hyps):
            st=np.zeros((ny,nx),np.float32)
            for k,d in enumerate(days):
                dx=int(round(-vx*k/CELL)); dy=int(round(-vy*k/CELL))
                f=frames[d]
                # shift with zero fill
                sh=np.zeros_like(f)
                ys=slice(max(0,dy),ny+min(0,dy)); xs=slice(max(0,dx),nx+min(0,dx))
                yd=slice(max(0,-dy),ny+min(0,-dy)); xd=slice(max(0,-dx),nx+min(0,-dx))
                sh[ys,xs]=f[yd,xd]
                st+=sh
            n=ndimage.convolve(st,box,mode='constant')
            lam=bg*W
            sc=np.zeros_like(n); m5=n>=5
            if m5.any(): sc[m5]=poisson_logsf(n[m5],lam[m5])
            upd=sc>best_score
            best_score[upd]=sc[upd]; best_h[upd]=hi; best_n[upd]=n[upd]
        # local maxima
        mx=ndimage.maximum_filter(best_score,size=7)
        pk=np.where((best_score>=4)&(best_score==mx))
        for yy,xx in zip(*pk):
            s,th,vx,vy=hyps[best_h[yy,xx]]
            cands.append(dict(w0=int(w0),x=float((xx+0.5)*CELL),y=float((yy+0.5)*CELL),speed=float(s),dir=float(np.degrees(th)),
                              vx=float(vx),vy=float(vy),n=int(best_n[yy,xx]),score=float(best_score[yy,xx]),lam=float(bg[yy,xx]*W)))
        print(ds(w0),"cands so far",len(cands),f"{time.time()-t0:.0f}s",file=sys.stderr)
    return cands,(ex,ey,ed)

if __name__=="__main__":
    cands,(ex,ey,ed)=run(SHUFFLE)
    tag=f"{SEASON[0][:4]}_c{int(CELL)}_s{SHUFFLE}"
    json.dump(cands,open(f"/tmp/fx/cands_{tag}.json","w"))
    np.savez(f"/tmp/fx/events_{tag}.npz",ex=ex,ey=ey,ed=ed)
    sc=np.array([c['score'] for c in cands])
    print(tag,"candidates",len(cands),"score>=6:",(sc>=6).sum(),"score>=8:",(sc>=8).sum(),">=10:",(sc>=10).sum())
