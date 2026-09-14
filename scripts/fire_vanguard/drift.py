import numpy as np, sys, json
sys.argv=['x','2.5','2024-10-01','2025-02-28','0']
import synth as S
from scipy.spatial import cKDTree
SEASON=sys.argv[2] if False else None
R=20.0; G=25.0  # neighbour radius km, grid km
def field(seed, d_from, d_to):
    ex,ey,ed,nx,ny=S.events(S.D(d_from),S.D(d_to),seed)
    x=(ex+0.5)*2.5; y=(ey+0.5)*2.5
    gx=int(x.max()/G)+1; gy=int(y.max()/G)+1
    months=sorted(set(S.ds(d)[:7] for d in range(S.D(d_from),S.D(d_to)+1)))
    F={m:np.zeros((gy,gx,3)) for m in months}   # sum vx, sum vy, count(sources with nbrs)
    for d in np.unique(ed):
        a=ed==d; b=ed==d+1
        if not b.any(): continue
        pa=np.c_[x[a],y[a]]; pb=np.c_[x[b],y[b]]
        tb=cKDTree(pb); lst=tb.query_ball_point(pa,R)
        m=S.ds(d)[:7]
        for i,nb in enumerate(lst):
            if not nb: continue
            dv=(pb[nb]-pa[i]).mean(0)
            cx,cy=int(pa[i,0]/G),int(pa[i,1]/G)
            F[m][cy,cx,0]+=dv[0]; F[m][cy,cx,1]+=dv[1]; F[m][cy,cx,2]+=1
    return F
d_from,d_to=sys.argv_season if hasattr(sys,'argv_season') else ("2024-10-01","2025-03-31")
import os
season=os.environ.get("SEASON","2024")
d_from,d_to=(f"{season}-10-01",f"{int(season)+1}-03-31")
real=field(0,d_from,d_to)
nulls=[field(s,d_from,d_to) for s in (7,11,13,17)]
out={}
for m in real:
    r=real[m]; c=r[...,2]; 
    mv=np.stack([r[...,:2]/np.maximum(c,1)[...,None]]+[n[m][...,:2]/np.maximum(n[m][...,2],1)[...,None] for n in nulls])
    bias=mv[1:].mean(0); sig=mv[1:].std(0).mean(-1)+1e-9
    drift=mv[0]-bias
    z=np.linalg.norm(drift,axis=-1)/sig
    out[m]=dict(count=c.tolist(),vx=drift[...,0].tolist(),vy=drift[...,1].tolist(),z=z.tolist(),raw_vx=mv[0][...,0].tolist(),raw_vy=mv[0][...,1].tolist())
    ok=c>=30
    print(m,"cells",ok.sum(),"median|drift| km/day",np.median(np.linalg.norm(drift[ok],axis=-1)).round(2),"median z",np.median(z[ok]).round(2),"z>3:",(z[ok]>3).sum(),
          "null-run z>3:", int(np.mean([ (np.linalg.norm(mv[k]-np.delete(mv[1:],k-1,0).mean(0),axis=-1)/sig)[ok].__gt__(3).sum() for k in range(1,5)])))
json.dump(dict(G=G,R=R,months=out),open(f"drift_{season}.json","w"))
