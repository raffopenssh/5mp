"""Self-derived daily visibility (exposure) map from the detections alone.
Cell-day is 'expected active' if it burned in d-3..d-1 AND d+1..d+3 (fire in progress around d).
Visibility V(d,cell) = smoothed fraction of expected-active cells that actually reported on d.
A cloud is a spatially coherent block of expected-active cells reporting nothing."""
import numpy as np, datetime, json
from scipy import ndimage
z=np.load("/tmp/fx/xsa.npz"); lat,lon,day=z['lat'],z['lon'],z['day']
d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
A=D("2024-08-01"); B=D("2025-04-01"); ND=B-A
res=0.1; x0,y0=22.7,4.25; nx=int((31.3-x0)/res)+1; ny=int((11.0-y0)/res)+1
m=(day>=A)&(day<B)
ix=((lon[m]-x0)/res).astype(int); iy=((lat[m]-y0)/res).astype(int); dd=day[m]-A
cnt=np.zeros((ND,ny,nx),np.int32)
np.add.at(cnt,(dd,iy,ix),1)
pres=(cnt>0).astype(np.int8)
# expected active: presence in both the 3 days before and the 3 days after
before=np.zeros_like(pres); after=np.zeros_like(pres)
for k in (1,2,3):
    before[k:]|=pres[:-k]; after[:-k]|=pres[k:]
exp_act=(before&after).astype(bool)
dark=exp_act&(cnt==0)
# regional daily statistic
n_exp=exp_act.reshape(ND,-1).sum(1); n_dark=dark.reshape(ND,-1).sum(1)
frac=np.where(n_exp>20,n_dark/np.maximum(n_exp,1),np.nan)
# spatially smoothed visibility per day (sigma ~0.5 deg)
V=np.full((ND,ny,nx),np.nan,np.float32)
for d in range(ND):
    w=exp_act[d].astype(float); s=(exp_act[d]&(cnt[d]>0)).astype(float)
    ws=ndimage.gaussian_filter(w,5); ss=ndimage.gaussian_filter(s,5)
    v=ss/np.maximum(ws,1e-9); v[ws<0.02]=np.nan; V[d]=v
np.savez("/tmp/fx/vis_2024.npz",V=V,frac=frac,n_exp=n_exp,n_dark=n_dark,cnt_day=cnt.reshape(ND,-1).sum(1),res=res,x0=x0,y0=y0,A=A)
print("days",ND,"cells",ny*nx)
print("regional dark fraction of expected-active cells, by month:")
for mo in range(8):
    lo,hi=mo*30,(mo+1)*30
    f=frac[lo:hi]; print(f"  {(d0+datetime.timedelta(days=A+lo)).isoformat()}  median {np.nanmedian(f):.2f}  p90 {np.nanpercentile(f,90):.2f}  days>0.5: {(f>0.5).sum()}")
bad=np.where(frac>0.5)[0]
print("cloud-like days (>50% of expected-active cells dark):",len(bad))
print(" ", [ (d0+datetime.timedelta(days=int(A+d))).isoformat() for d in bad[:40]])
