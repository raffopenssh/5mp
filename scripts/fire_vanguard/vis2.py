import numpy as np, datetime
from scipy import ndimage
z=np.load("/tmp/fx/xsa.npz"); day=z['day']; sat=z['sat']
v=np.load("/tmp/fx/vis_2024.npz"); A=int(v['A']); frac=v['frac']; cnt_day=v['cnt_day']
d0=datetime.date(2020,1,1)
# sensor completeness per day
sats=np.unique(sat); print("sensors",sats)
m=(day>=A)&(day<A+243)
for s in sats:
    c=np.bincount(day[m&(sat==s)]-A,minlength=243); print(s,"days with 0 detections:",(c==0).sum(),"of 243; median/day",int(np.median(c)))
# spatial coherence of dark cells: recompute quickly
res=0.1; x0,y0=22.7,4.25; nx=int((31.3-x0)/res)+1; ny=int((11.0-y0)/res)+1
lat,lon=z['lat'][m],z['lon'][m]; dd=day[m]-A
cnt=np.zeros((243,ny,nx),np.int32); np.add.at(cnt,(dd,((lat-y0)/res).astype(int),((lon-x0)/res).astype(int)),1)
pres=cnt>0; before=np.zeros_like(pres); after=np.zeros_like(pres)
for k in (1,2,3): before[k:]|=pres[:-k]; after[:-k]|=pres[k:]
ea=before&after; dark=ea&~pres
K=np.ones((7,7))
coh=[]
for d in range(243):
    if ea[d].sum()<50: coh.append(np.nan); continue
    nd=ndimage.convolve(dark[d].astype(float),K,mode='constant'); ne=ndimage.convolve(ea[d].astype(float),K,mode='constant')
    sel=dark[d]; local=(nd[sel]-1)/np.maximum(ne[sel]-1,1)   # neighbours' dark fraction around a dark cell
    coh.append(np.nanmean(local)/max(frac[d],1e-6))
coh=np.array(coh)
print("coherence ratio (neighbour dark frac / regional dark frac), 1.0 = independent intermittency:")
for lo,hi,name in [(30,60,'Sep'),(60,90,'Oct'),(90,120,'Nov'),(120,150,'Dec'),(150,180,'Jan'),(180,210,'Feb')]:
    print(f"  {name}: median {np.nanmedian(coh[lo:hi]):.2f}  p90 {np.nanpercentile(coh[lo:hi],90):.2f}")
# a daily total series with dips
print("daily XSA detections Oct 1..Nov 15:"); print(cnt_day[61:107].tolist())
