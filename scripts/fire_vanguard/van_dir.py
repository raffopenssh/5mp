import json, numpy as np
d=json.load(open("/tmp/fx/van_tier_groups.json")); real=d['real']; nulls=d['nulls']
G=0.75  # deg cells
def vecs(gs):
    S={}
    for g in gs:
        t=g['trajectory']; 
        if len(t)<3 or g['distance_km']<15: continue
        x0,y0=t[0][0],t[0][1]; x1,y1=t[-1][0],t[-1][1]
        dx=(x1-x0)*110; dy=(y1-y0)*110; n=np.hypot(dx,dy); 
        if n<1: continue
        k=(int(x0/G),int(y0/G)); S.setdefault(k,[]).append((dx/n,dy/n))
    return {k:(np.mean(v,0),len(v)) for k,v in S.items()}
R=vecs(real); N=[vecs(g) for g in nulls]
print("cell        n  real-dir  |R|   null|R|mean  z   (unit vectors averaged; |R|=1 all same direction)")
rows=[]
for k,(v,n) in sorted(R.items()):
    if n<8: continue
    nv=[nn[k][0] for nn in N if k in nn and nn[k][1]>=4]
    if len(nv)<3: continue
    nv=np.array(nv); bias=nv.mean(0); sd=np.sqrt(((nv-bias)**2).sum(1).mean())
    r=v-bias; z=np.linalg.norm(r)/max(sd,1e-6)
    ang=np.degrees(np.arctan2(r[0],r[1]))%360
    rows.append((k,n,ang,np.linalg.norm(v),np.linalg.norm(nv,axis=1).mean(),z))
    print(f"{k[0]*G:5.2f},{k[1]*G:5.2f} {n:3d}  {ang:5.0f}°  {np.linalg.norm(v):.2f}  {np.linalg.norm(nv,axis=1).mean():.2f}   {z:.1f}")
json.dump([dict(lon=k[0]*G+G/2,lat=k[1]*G+G/2,n=n,dir=a,R=R_,z=z) for k,n,a,R_,nr,z in rows],open("/tmp/fx/van_dir.json","w"))
