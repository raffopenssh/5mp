import numpy as np, datetime, matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from scipy import ndimage
d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
a=np.load("xsa.npz"); h=np.load("xsa_hist.npz")
LAT=np.r_[h['lat'],a['lat']]; LON=np.r_[h['lon'],a['lon']]; DAY=np.r_[h['day'],a['day']]
LAT0=7.6; KX=111.32*np.cos(np.radians(LAT0)); KY=110.57; X0,Y0=22.7,4.25
CELL=5.0; nx=int(8.6*KX/CELL)+2; ny=int(6.75*KY/CELL)+2
EARLY=15; REGWIN=int(80/CELL)|1
van_sum=np.zeros((ny,nx)); cov_sum=np.zeros((ny,nx)); anoms={}
for yr in range(2018,2026):
    m=(DAY>=D(f"{yr}-08-01"))&(DAY<D(f"{yr+1}-08-01"))
    x=(LON[m]-X0)*KX; y=(LAT[m]-Y0)*KY; ix=(x/CELL).astype(int); iy=(y/CELL).astype(int); dd=DAY[m]-D(f"{yr}-08-01")
    onset=np.full((ny,nx),np.nan); o=np.argsort(-dd); onset[iy[o],ix[o]]=dd[o]
    cnt=np.zeros((ny,nx)); np.add.at(cnt,(iy,ix),1)
    # coverage: cells within footprint = dilate burned cells by 15 km
    cov=ndimage.binary_dilation(cnt>0,iterations=3)
    tmp=np.where(np.isnan(onset),9999,onset); reg=ndimage.percentile_filter(tmp,20,size=REGWIN); reg=np.where(reg>=9999,np.nan,reg)
    anom=onset-reg; van=(anom<=-EARLY)&(cnt>=2)
    van_sum+=van; cov_sum+=cov; anoms[yr]=anom
    print(yr,"det",m.sum(),"burned cells",(cnt>0).sum(),"vanguard",van.sum())
rec=np.where(cov_sum>=3,van_sum/np.maximum(cov_sum,1),np.nan)
np.save("recur.npy",rec); np.save("cov.npy",cov_sum); np.save("van_sum.npy",van_sum)
fig,ax=plt.subplots(figsize=(14,10),facecolor='#111'); ax.set_facecolor('#111')
im=ax.imshow(rec,origin='lower',extent=[22.7,31.3,4.25,11.0],cmap='magma',vmin=0,vmax=0.8,aspect='auto')
ax.set_title("fraction of covered seasons (2018/19–2025/26) in which the cell burned >=15 d ahead of its 80-km surroundings",color='w'); ax.tick_params(colors='w')
plt.colorbar(im,fraction=0.02).ax.tick_params(colors='w'); plt.savefig("recur.png",dpi=60,bbox_inches='tight',facecolor='#111')
# how non-random is recurrence? compare to binomial with per-season vanguard rate
p=np.nanmean([ (anoms[y]<=-EARLY).sum()/np.isfinite(anoms[y]).sum() for y in anoms]); print("mean vanguard rate per season",round(p,3))
ok=cov_sum>=6
from scipy import stats
obs=van_sum[ok]; n=cov_sum[ok]
print("cells covered>=6 seasons:",ok.sum(),"observed >=4x vanguard:",(obs>=4).sum(),"expected if independent:",round(sum(stats.binom.sf(3,int(k),p) for k in n),1))
