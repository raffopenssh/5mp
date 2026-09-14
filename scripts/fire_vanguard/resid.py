import numpy as np, datetime, matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from scipy import ndimage
z=np.load("/tmp/fx/xsa.npz"); lat,lon,day=z['lat'],z['lon'],z['day']
d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
res=0.025
x0,x1,y0,y1=22.7,31.3,4.25,11.0
nx=int((x1-x0)/res)+1; ny=int((y1-y0)/res)+1
fig,axs=plt.subplots(2,2,figsize=(22,16))
for row,(a,b,title) in enumerate([("2024-08-01","2025-08-01","2024/25"),("2025-08-01","2026-08-01","2025/26")]):
    m=(day>=D(a))&(day<D(b))
    ix=((lon[m]-x0)/res).astype(int); iy=((lat[m]-y0)/res).astype(int); dd=day[m]-D(a)
    onset=np.full((ny,nx),np.nan); order=np.argsort(-dd); onset[iy[order],ix[order]]=dd[order]
    # regional onset: 30th percentile in ~60km window (24 cells) of burned cells
    filled=np.where(np.isnan(onset),0,onset); cnt=(~np.isnan(onset)).astype(float)
    # use median via percentile filter on masked array approx: fill NaN with large then percentile
    tmp=np.where(np.isnan(onset),9999,onset)
    reg=ndimage.percentile_filter(tmp,20,size=25)
    reg=np.where(reg>=9999,np.nan,reg)
    resid=onset-reg
    im=axs[row,0].imshow(reg,origin='lower',extent=[x0,x1,y0,y1],cmap='turbo',vmin=60,vmax=200); axs[row,0].set_title(title+" regional onset (20th pct, 60km)")
    im2=axs[row,1].imshow(resid,origin='lower',extent=[x0,x1,y0,y1],cmap='RdBu',vmin=-40,vmax=40); axs[row,1].set_title(title+" onset anomaly (days; red = earlier than surroundings)")
    np.save(f"/tmp/fx/onset_{title[:4]}.npy",onset); np.save(f"/tmp/fx/reg_{title[:4]}.npy",reg)
plt.savefig("/tmp/fx/resid.png",dpi=55,bbox_inches='tight')
