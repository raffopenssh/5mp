import sqlite3, json, numpy as np
conn=sqlite3.connect("file:/home/exedev/5mp/db.sqlite3?mode=ro",uri=True)
pts=[]
for gj,pj in conn.execute("select geojson,properties_json from feature_geometries where park_id='XSA_Study_Area' and feature_type='settlement'"):
    g=json.loads(gj); p=json.loads(pj or '{}')
    if p.get('narrative','').startswith('Mining') or p.get('retired'): continue
    c=g['coordinates']
    while isinstance(c[0],list): c=c[0]
    pts.append((c[0],c[1]))
np.save("/tmp/fx/settle_xy.npy",np.array(pts)); print("settlements",len(pts))
ks=json.load(open("/home/exedev/5mp/data/keystones_with_boundaries.json"))
parks=[]
items=ks if isinstance(ks,list) else ks.get('parks',ks.get('features',[]))
for k in items:
    g=k.get('geometry') or k.get('boundary'); 
    if not g: continue
    b=json.dumps(g)
    parks.append(dict(id=k.get('id') or k.get('park_id'),geom=g))
json.dump(parks,open("/tmp/fx/parks.json","w")); print("parks",len(parks))
