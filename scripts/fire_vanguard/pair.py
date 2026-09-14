import numpy as np, sys
sys.argv=['x','2.5','2024-10-01','2025-02-28','0']
import synth as S
from scipy.spatial import cKDTree
def pairs(seed):
    ex,ey,ed,nx,ny=S.events(S.D("2024-10-01"),S.D("2025-02-28"),seed)
    x=(ex+0.5)*2.5; y=(ey+0.5)*2.5
    bins=np.array([0,3,6,10,15,20,30,40,60,80])
    out={}
    for lag in [1,2,3,5,8,13]:
        cnt=np.zeros(len(bins)-1)
        for d in np.unique(ed):
            a=ed==d; b=ed==d+lag
            if not b.any(): continue
            ta=cKDTree(np.c_[x[a],y[a]]); tb=cKDTree(np.c_[x[b],y[b]])
            # count pairs within each radius
            cum=np.array([ta.count_neighbors(tb,r) for r in bins])
            cnt+=np.diff(cum)
        out[lag]=cnt
    return bins,out,len(ex)
bins,real,n=pairs(0); print("events",n)
nulls=[pairs(s)[1] for s in (7,11)]
print("lag  r-bin(km)      real     null    ratio")
for lag in real:
    for i in range(len(bins)-1):
        nv=np.mean([nl[lag][i] for nl in nulls])
        print(f"{lag:3d} {bins[i]:3.0f}-{bins[i+1]:3.0f}  {real[lag][i]:9.0f} {nv:9.0f}  {real[lag][i]/max(nv,1):.2f}")
