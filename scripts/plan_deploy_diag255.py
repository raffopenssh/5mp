import sys, json, math, sqlite3, numpy as np
sys.path.insert(0, '/home/exedev/5mp/scripts')
import plan_deploy as D, plan_conservancy_units as P
from shapely.geometry import shape, Point
from scipy import ndimage
st, G, zs, lab, p10, bud, mv, rk = D.load("")
con = sqlite3.connect(str(P.DB)); IX = D.site_index(con, G); C = IX["C"]; pop_r = np.nan_to_num(C["pop"])
near_people = ndimage.maximum_filter(pop_r, size=int(25000 / G.res) * 2 + 1)
f = next(f for f in zs if f["properties"]["uid"] == 255); p = f["properties"]
m = G.rasterize([(D.transform(P.FWD, shape(f["geometry"])), 1)]).astype(bool)
udr = np.zeros(lab.shape, np.float32)
for h in p["herd_bundles"]: udr += bud[h["bundle"] - 1]
AC = D.Access(G)
sc = ndimage.gaussian_filter(udr * m * (near_people > 0), 2); near = ndimage.binary_dilation(m, iterations=int(D.SITE_REACH_KM * 1000 / G.res))
cands = []
for x in IX["S"]:
    r, c = G.rc(x[1], x[0])
    if 0 <= r < G.h and 0 <= c < G.w and near[r, c]: cands.append(x)
def herd_at(x):
    r, c = G.rc(x[1], x[0]); return 0.05 + float(sc[r, c]) / max(float(sc.max()), 1e-12)
sl = D.shortlist(cands, IX, AC, G, m, udr * m, prefer=herd_at, need_road=False, k=40)
for o in sl:
    x=o["row"]; print(f'{o["score"]:6.3f} {o["name"][:30]:30s} pop={int(x[2] or 0):6d} lon={x[1]:.3f} lat={x[0]:.3f} road={o["road_km"]} reach={o["reach"]} herd={herd_at(x):.3f}')
print("--- named places")
tgt = {"Raja": (25.68, 8.46), "Deim Zubeir": (26.21, 7.72), "Boro Medina": (25.03, 8.73)}
for n,(lo,la) in tgt.items():
    rows = sorted(cands, key=lambda x: P.dkm((lo,la),(x[1],x[0])))[:3]
    print(n, "dist to zone km", round(shape(f["geometry"]).distance(Point(lo,la))*111,1))
    for x in rows:
        j = AC.station(x[1], x[0]); cids = AC.cell_ids(m, G); rr, cc = G.rc_arr(AC.cells[cids,0], AC.cells[cids,1]); w=(udr*m)[rr,cc]
        rc = AC.reach(j, cids, w, D.REACH_MIN) if j is not None else None
        print(f'   {IX["name_at"](x[1],x[0],x[5])[:30]:30s} pop={int(x[2] or 0):6d} cls={x[3]} d={P.dkm((lo,la),(x[1],x[0])):.1f}km road={IX["road_km"](x[1],x[0],D.ROAD_FAR_KM)} reach={rc} herd={herd_at(x):.3f}')
print("--- marginal coverage of a second site given Bisellia (weighted share of zone targets within 4 h by either base)")
cids = AC.cell_ids(m, G); rr, cc = G.rc_arr(AC.cells[cids,0], AC.cells[cids,1]); w=(udr*m)[rr,cc]
def within(lon,lat):
    j=AC.station(lon,lat); return (AC.t[j][cids] <= D.REACH_MIN) if j is not None else np.zeros(len(cids),bool)
b = within(27.7252, 7.7155); print("Bisellia alone", round(float(w[b].sum()/w.sum()),3))
for n,(lo,la) in {"Busseri River 27.51/8.55":(27.506,8.552),"Busseri 26.70/7.73":(26.699,7.726),"Raja 25.67/8.48":(25.670,8.477),"Deim Zubeir":(26.195,7.715),"Sopo":(26.038,8.033),"Aluk":(27.508,8.457)}.items():
    s = within(lo,la); print(f"{n:28s} alone {float(w[s].sum()/w.sum()):.3f}  union {float(w[b|s].sum()/w.sum()):.3f}  marginal {float(w[s&~b].sum()/w.sum()):.3f}  cells-only union {float((b|s).mean()):.3f}")
