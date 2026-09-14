import sqlite3, numpy as np, time, sys
t=time.time()
conn=sqlite3.connect("file:/home/exedev/5mp/db.sqlite3?mode=ro",uri=True)
rows=conn.execute("""select f.latitude,f.longitude,f.acq_date,f.acq_time,f.frp,f.satellite,f.confidence
 from aoi_fires a join fire_detections f on f.id=a.fire_id where a.aoi_id='XSA_Study_Area'""").fetchall()
print(len(rows), time.time()-t)
import datetime
lat=np.array([r[0] for r in rows],dtype=np.float64); lon=np.array([r[1] for r in rows],dtype=np.float64)
d0=datetime.date(2020,1,1)
day=np.array([(datetime.date.fromisoformat(r[2])-d0).days for r in rows],dtype=np.int32)
hhmm=np.array([int(r[3]) if r[3] not in (None,'') else 1200 for r in rows],dtype=np.int16)
frp=np.array([r[4] or 0 for r in rows],dtype=np.float32)
sat=np.array([r[5] or '' for r in rows])
np.savez("/tmp/fx/xsa.npz",lat=lat,lon=lon,day=day,hhmm=hhmm,frp=frp,sat=sat)
print("saved",time.time()-t)
