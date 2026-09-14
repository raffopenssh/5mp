import sys, json, numpy as np, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0,"/home/exedev/5mp/scripts"); sys.argv=['x','10']
exec(open('/tmp/fx/van_null.py').read().split("res_={}")[0])   # reuse loading -> van, pk
import rebuild_fire_trajectories_v5 as B
from eval_fire_null import shuffle_days
real=B.process_park_fires(van,"XSA_Study_Area",pk["geometry"])
nulls=[B.process_park_fires(shuffle_days(van,s),"XSA_Study_Area",pk["geometry"]) for s in (7,11,13,17)]
def cnt(gs,dmin,kmin): return sum(1 for g in gs if g['days']>=dmin and g['distance_km']>=kmin)
print("days>= km>=   real   null   FDR")
for dmin in (3,5,7,10):
    for kmin in (15,25,40,60):
        r=cnt(real,dmin,kmin); n=np.mean([cnt(g,dmin,kmin) for g in nulls]); print(f"{dmin:5d} {kmin:4d} {r:6d} {n:6.1f}  {n/max(r,1):.2f}")
json.dump(dict(real=real,nulls=nulls),open("/tmp/fx/van_tier_groups.json","w"),default=str)
