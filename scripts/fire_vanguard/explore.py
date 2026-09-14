import numpy as np, datetime
z=np.load("/tmp/fx/xsa.npz"); lat,lon,day=z['lat'],z['lon'],z['day']
d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
# season 2024-25: Aug 2024 - Jul 2025
m=(day>=D("2024-08-01"))&(day<D("2025-08-01"))
print("season 24/25 detections",m.sum())
dd=day[m]
# daily counts, first 120 days from Oct 1
for d in range(D("2024-10-01"),D("2025-01-15"),3):
    c=((dd>=d)&(dd<d+3)).sum(); print((d0+datetime.timedelta(d)).isoformat(), c, '#'*int(np.log2(c+1)*3))
