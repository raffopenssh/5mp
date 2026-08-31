import json, math
from collections import Counter
g = json.load(open('/home/exedev/5mp/data/fire_groups_v5/XSA_Study_Area.json'))

def bearing(p1,p2):
    dy=(p2[1]-p1[1]); dx=(p2[0]-p1[0])*math.cos(math.radians((p1[1]+p2[1])/2))
    return (math.degrees(math.atan2(dx,dy)))%360

border=[(25.0,9.2),(25.8,8.4),(26.2,7.9),(26.5,7.4),(27.0,6.6),(27.2,6.0),(27.4,5.6)]
def belt(lon,lat,w=40):
    best=1e9; bb=None
    for (x1,y1),(x2,y2) in zip(border,border[1:]):
        dx,dy=x2-x1,y2-y1
        t=max(0,min(1,((lon-x1)*dx+(lat-y1)*dy)/(dx*dx+dy*dy)))
        px,py=x1+t*dx,y1+t*dy
        d=math.hypot((lat-py)*111,(lon-px)*111*math.cos(math.radians(lat)))
        if d<best: best=d; bb=bearing((x1,y1),(x2,y2))
    return best<=w, bb

# For long transhumance fronts passing the belt: axis-aligned vs crossing, weighted by distance
along=cross=0; alongN=crossN=0
big=[]
for grp in g:
    t=grp.get('trajectory') or []
    if len(t)<3 or grp['group_type']!='transhumance': continue
    idx=[i for i,p in enumerate(t) if belt(p[0],p[1])[0]]
    if len(idx)<2: continue
    i0,i1=max(0,idx[0]-1),min(len(t)-1,idx[-1]+1)
    p1,p2=t[i0],t[i1]
    d=math.hypot((p2[1]-p1[1])*111,(p2[0]-p1[0])*111*math.cos(math.radians(p1[1])))
    if d<10: continue
    b=bearing(p1,p2)
    _,bb=belt((p1[0]+p2[0])/2,(p1[1]+p2[1])/2)
    diff=abs((b-bb+180)%360-180); diff=min(diff,180-diff)  # angle to axis either direction
    if diff<=30: along+=1; alongN+=d
    elif diff>=60: cross+=1; crossN+=d
    if d>150: big.append((d,b,grp['start_date'],grp['distance_km'],grp['days']))
n=along+cross
print(f"belt passers with >=10km net move: along-axis(+-30) {along}, crossing(>=60deg) {cross}, mid {0}, ratio cross/along={cross/max(along,1):.2f}")
print(f"km-weighted: along {alongN:.0f} km, cross {crossN:.0f} km")
# expected by chance: along band=60/180=33%, cross=90/180=50% -> ratio 1.5
print("chance ratio cross/along = 1.5")
big.sort(reverse=True)
print(f"\n{len(big)} big movers (>150km net through belt). Their overall headings:")
c=Counter(["N","NE","E","SE","S","SW","W","NW"][int(((b+22.5)%360)//45)] for _,b,_,_,_ in big)
print(c.most_common())
for d,b,s,dist,days in big[:8]:
    print(f"  net {d:.0f}km heading {b:.0f}deg, start {s}, total path {dist}km in {days}d")
