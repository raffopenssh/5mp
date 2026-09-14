import numpy as np, datetime, matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
z=np.load("/tmp/fx/xsa.npz"); lat,lon,day=z['lat'],z['lon'],z['day']
d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
res=0.05
x0,x1,y0,y1=22.7,31.3,4.25,11.0
nx=int((x1-x0)/res)+1; ny=int((y1-y0)/res)+1
fig,axs=plt.subplots(1,3,figsize=(24,7))
for ax,(a,b,title) in zip(axs,[("2023-08-01","2024-08-01","2023/24 (aoi from 2024-01)"),("2024-08-01","2025-08-01","2024/25"),("2025-08-01","2026-08-01","2025/26")]):
    m=(day>=D(a))&(day<D(b))
    ix=((lon[m]-x0)/res).astype(int); iy=((lat[m]-y0)/res).astype(int); dd=day[m]-D(a)
    onset=np.full((ny,nx),np.nan)
    order=np.argsort(-dd)  # descending so min wins with assignment
    onset[iy[order],ix[order]]=dd[order]
    im=ax.imshow(onset,origin='lower',extent=[x0,x1,y0,y1],cmap='turbo',vmin=60,vmax=200)
    ax.set_title(title+" first-burn day (day 60=Oct1, 200=Feb17)")
plt.colorbar(im,ax=axs,fraction=0.02)
plt.savefig("/tmp/fx/onset.png",dpi=60,bbox_inches='tight')
