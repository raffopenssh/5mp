import sqlite3, numpy as np, time, datetime
t=time.time()
conn=sqlite3.connect("file:/home/exedev/5mp/db.sqlite3?mode=ro",uri=True)
rows=conn.execute("""select latitude,longitude,acq_date from fire_detections
 where latitude between 4.25 and 11 and longitude between 22.7 and 31.3 and acq_date < '2024-01-01'""").fetchall()
print(len(rows), time.time()-t)
d0=datetime.date(2020,1,1)
lat=np.array([r[0] for r in rows]); lon=np.array([r[1] for r in rows])
day=np.array([(datetime.date.fromisoformat(r[2])-d0).days for r in rows],dtype=np.int32)
np.savez("/tmp/fx/xsa_hist.npz",lat=lat,lon=lon,day=day)
import collections; print(sorted(collections.Counter(r[2][:4] for r in rows).items()))
