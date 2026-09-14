import numpy as np, json
for season in ["2024","2025"]:
    D=json.load(open(f"drift_{season}.json"))
    for m,F in D['months'].items():
        vx=np.array(F['vx']); vy=np.array(F['vy']); c=np.array(F['count']); ok=c>=30
        rvx=np.array(F['raw_vx']); rvy=np.array(F['raw_vy'])
        def coh(vx,vy):
            u=np.stack([vx,vy],-1); n=np.linalg.norm(u,axis=-1,keepdims=True); u=u/np.maximum(n,1e-9)
            nb=np.zeros_like(u); cnt=np.zeros(u.shape[:2])
            for dy,dx in [(0,1),(0,-1),(1,0),(-1,0)]:
                sh=np.roll(np.roll(u,dy,0),dx,1); okn=np.roll(ok,dy,0)&np.roll(ok,dx,1)
                nb+=sh*okn[...,None]; cnt+=okn
            m2=ok&(cnt>=2); nbn=nb/np.maximum(np.linalg.norm(nb,axis=-1,keepdims=True),1e-9)
            return (u[m2]*nbn[m2]).sum(-1).mean(), m2.sum()
        print(season,m,"cells",ok.sum(),"neighbour coherence (debiased) %.3f"%coh(vx,vy)[0],"raw %.3f"%coh(rvx,rvy)[0])
