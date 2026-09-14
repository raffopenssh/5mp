import sqlite3, json
conn=sqlite3.connect("file:/home/exedev/5mp/db.sqlite3?mode=ro",uri=True)
rows=conn.execute("""select feature_id,geojson,start_date,end_date,traj_days,properties_json from feature_geometries
 where park_id='XSA_Study_Area' and feature_type='fire_trajectory' and start_date>='2024-08-01' and start_date<'2025-08-01'""").fetchall()
out=[]
for fid,gj,sd,ed,td,pj in rows:
    g=json.loads(gj); p=json.loads(pj)
    coords=g['coordinates'] if g['type']=='LineString' else (g['coordinates'][0] if g['type']=='MultiLineString' else None)
    if not coords: continue
    offs=json.loads(td) if td else None
    if not offs or len(offs)!=len(coords): offs=[0]*len(coords)
    out.append(dict(id=fid,coords=coords,offs=offs,start=sd,end=ed,fires=p.get('fires_total'),days=p.get('days'),dist=p.get('distance_km'),gtype=p.get('group_type')))
json.dump(out,open("/tmp/fx/lines_2024.json","w")); print(len(out))
