import json, math
from collections import Counter

g = json.load(open('/home/exedev/5mp/data/fire_groups_v5/XSA_Study_Area.json'))

def seg_pass(traj, inside):
    """indices of traj points inside zone"""
    return [i for i,p in enumerate(traj) if inside(p[0],p[1])]

def bearing(p1,p2):
    dy=(p2[1]-p1[1]); dx=(p2[0]-p1[0])*math.cos(math.radians((p1[1]+p2[1])/2))
    return (math.degrees(math.atan2(dx,dy)))%360

def octant(b):
    return ["N","NE","E","SE","S","SW","W","NW"][int(((b+22.5)%360)//45)]

def analyze(name, inside, min_pts=2):
    thru=[]
    for grp in g:
        t=grp.get('trajectory') or []
        if len(t)<2: continue
        idx=seg_pass(t, inside)
        if not idx: continue
        # direction across the zone: entry->exit of the in-zone stretch (extend one point either side if exists)
        i0=max(0,idx[0]-1); i1=min(len(t)-1, idx[-1]+1)
        if i0==i1: continue
        p1,p2=t[i0],t[i1]
        d=math.hypot((p2[1]-p1[1])*111,(p2[0]-p1[0])*111*math.cos(math.radians(p1[1])))
        if d<3: continue
        thru.append((grp,bearing(p1,p2),d))
    dirs=Counter(octant(b) for _,b,_ in thru)
    types=Counter(grp['group_type'] for grp,_,_ in thru)
    n=len(thru)
    print(f"\n== {name}: {n} trajectories pass through (>=3km within zone)")
    if n:
        print("  direction across zone:", ", ".join(f"{k} {v} ({100*v//n}%)" for k,v in dirs.most_common()))
        print("  types:", dict(types.most_common(4)))
        dists=[d for _,_,d in thru]
        print(f"  median in-zone travel {sorted(dists)[n//2]:.0f} km")
        # months
        mon=Counter(grp['start_date'][5:7] for grp,_,_ in thru)
        print("  start months:", dict(sorted(mon.items())))
    return thru

# Zone A: livestock corridor belt along CAR-SSD border, as drawn: a diagonal band.
# Approximate border belt: points within ~40km of CAR-SSD border line from (9.2N,25.0E) down to (5.6N,27.4E)
border=[(25.0,9.2),(25.8,8.4),(26.2,7.9),(26.5,7.4),(27.0,6.6),(27.2,6.0),(27.4,5.6)]
def dist_to_polyline(lon,lat):
    best=1e9
    for (x1,y1),(x2,y2) in zip(border,border[1:]):
        # project
        dx,dy=x2-x1,y2-y1
        t=max(0,min(1,((lon-x1)*dx+(lat-y1)*dy)/(dx*dx+dy*dy)))
        px,py=x1+t*dx,y1+t*dy
        d=math.hypot((lat-py)*111,(lon-px)*111*math.cos(math.radians(lat)))
        best=min(best,d)
    return best
analyze("Border livestock belt (within 40km of CAR-SSD border line)", lambda lo,la: dist_to_polyline(lo,la)<=40)

# Zone B: proposed Pongo-Wau-Numatinna NP bbox
analyze("Proposed Pongo-Wau-Numatinna NP (6.6-7.9N 26.9-27.9E)", lambda lo,la: 6.6<=la<=7.9 and 26.9<=lo<=27.9)

# Zone C: old road line (10km buffer) - re-check road report claim
A=(27.073,6.001); B=(26.195,7.715)
def dist_to_line(lon,lat):
    dx,dy=B[0]-A[0],B[1]-A[1]
    t=max(0,min(1,((lon-A[0])*dx+(lat-A[1])*dy)/(dx*dx+dy*dy)))
    px,py=A[0]+t*dx,A[1]+t*dy
    return math.hypot((lat-py)*111,(lon-px)*111*math.cos(math.radians(lat)))
thru=analyze("Old Tamboura-Deim Zubeir road line (10km buffer)", lambda lo,la: dist_to_line(lo,la)<=10)
# along-axis share: road bearing
rb=bearing((A[0],A[1]),(B[0],B[1]))
along=sum(1 for _,b,_ in thru if min(abs((b-rb)%360),abs((b-rb-180)%360),abs((rb-b)%360),abs((rb-b-180)%360))<=30 or abs(((b-rb+180)%360)-180)<=30)
al=sum(1 for _,b,_ in thru if min((b-rb)%360,(rb-b)%360)<=30 or min((b-rb+180)%360,(rb-b+180)%360)<=30)
print(f"  road bearing {rb:.0f}deg; within +-30deg of axis (either way): {al} of {len(thru)} = {100*al/len(thru):.0f}%")
