#!/usr/bin/env python3
"""ERA5-Land fuel/soil vs the long transhumance fronts — the two movement questions wind_field cannot answer.

  (1) Do the fronts move DOWN the soil-moisture gradient (toward wetter ground = toich/wetlands drying later)?
      Per daily step: dSoil = swvl1(destination, day) - swvl1(origin, day). Null = the same step with a random bearing
      from the SAME origin and SAME length on the SAME day (200 draws): keeps place, time and distance, destroys direction.
      skill = share(real dSoil > 0) - share(null dSoil > 0); z against the null sd. By month and by bundle.
  (2) Is a step's origin cured fuel? share of step-days with >= 10 dry days (< 1 mm) at the origin, vs ALL VIIRS detections
      of the same season-month in the AOI (the fire field's base rate) — a front is a fire; this says whether the fuel state
      at a front differs from the fuel state at any fire.
  (3) Rain-gated season close: per 0.1 deg cell and season, last detection day vs first rain day >= 10 mm after 15 Feb;
      Spearman across cells, null = rain-day shuffled across cells (200 draws).

    python3 -W ignore scripts/fire_climate_check.py     -> data/eval/fire_climate_check.json
Numbers only; adoption is decided by eval_fire_baseline.py --xsa --climate (leading-edge Gini), never here.
"""
import json, math, os, sqlite3, sys
from collections import defaultdict
from datetime import date
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, "scripts"))
from gzjson import gz
from climate_field import ClimateField
from wind_field import steps, bearing, dist_km
AOI = "XSA_Study_Area"; OUT = os.path.join(ROOT, "data/eval/fire_climate_check.json"); MONTHS = [10, 11, 12, 1, 2, 3]
MN = {10: "Oct", 11: "Nov", 12: "Dec", 1: "Jan", 2: "Feb", 3: "Mar"}; N_NULL = 200


def season_of(d): y, m = int(d[:4]), int(d[5:7]); return y if m >= 8 else y - 1


def main():
    cf = ClimateField.load(AOI); print("land covered", cf.covered(), len(cf.days), "days")
    mv = json.load(open(os.path.join(ROOT, "data/plan_zones/solver/movement.json")))
    raw = gz.load(os.path.join(ROOT, "data/fire_groups_v5", f"{AOI}.json"))
    T = [g for g in raw if g.get("group_type") == "transhumance" and float(g.get("distance_km") or 0) >= 150 and len(g.get("trajectory") or []) >= 2]
    lat0 = float(np.mean([g["trajectory"][0][1] for g in T])); kx = 111 * math.cos(math.radians(lat0))
    C = np.array([[b["start"][0] * kx, b["start"][1] * 111, b["end"][0] * kx, b["end"][1] * 111] for b in mv["bundles"]])
    X = np.array([[g["trajectory"][0][0] * kx, g["trajectory"][0][1] * 111, g["trajectory"][-1][0] * kx, g["trajectory"][-1][1] * 111] for g in T])
    d1 = ((X[:, None, :] - C[None]) ** 2).sum(2); d2 = ((X[:, [2, 3, 0, 1]][:, None, :] - C[None]) ** 2).sum(2); lab = np.argmin(np.minimum(d1, d2), 1)
    rows = []
    for i, g in enumerate(T):
        t = g["trajectory"]
        for p, q in zip(t, t[1:]):
            km = float(dist_km(p[0], p[1], q[0], q[1]))
            if km < 1: continue
            rows.append((i, p[0], p[1], q[0], q[1], cf.daynum(p[2]), int(p[2][5:7]), season_of(g["start_date"]), km, lab[i]))
    R = np.array(rows, float); fi, lo0, la0, lo1, la1, dn, mon, sea, km, bun = R.T; dn = dn.astype(int); mon = mon.astype(int); sea = sea.astype(int); bun = bun.astype(int)
    s0 = cf.soil(lo0, la0, dn); s1 = cf.soil(lo1, la1, dn); ok = np.isfinite(s0) & np.isfinite(s1) & np.isin(mon, MONTHS)
    dS = s1 - s0; print(f"{len(R)} steps, {ok.sum()} with soil moisture")
    rng = np.random.default_rng(0); cosl = np.cos(np.radians(la0))
    nulls = []
    for _ in range(N_NULL):
        th = rng.uniform(0, 2 * np.pi, len(R)); lo_n = lo0 + km * np.sin(th) / (111 * cosl); la_n = la0 + km * np.cos(th) / 111
        sn = cf.soil(lo_n, la_n, dn); nulls.append(sn - s0)
    nulls = np.array(nulls)   # [draw, step]
    def q1(mask):
        m = mask & ok; n = int(m.sum())
        if n < 20: return dict(n=n)
        real = float((dS[m] > 0).mean()); nv = np.array([np.nanmean(nulls[k, m] > 0) for k in range(N_NULL)])
        return dict(n=n, toward_wetter=round(real, 3), null=round(float(nv.mean()), 3), skill=round(real - float(nv.mean()), 3), z=round(float((real - nv.mean()) / max(nv.std(), 1e-9)), 1),
                    dsoil_mean=round(float(dS[m].mean()), 4), null_dsoil_mean=round(float(np.nanmean(nulls[:, m])), 4), soil_origin_mean=round(float(s0[m].mean()), 3))
    res = dict(aoi=AOI, land_covered=cf.covered(), n_fronts=len(T), n_steps=int(len(R)), n_steps_soil=int(ok.sum()), n_null=N_NULL)
    res["soil_gradient_by_month"] = {MN[m]: q1(mon == m) for m in MONTHS}
    res["soil_gradient_by_period"] = {"Oct-Dec": q1(np.isin(mon, [10, 11, 12])), "Jan-Feb": q1(np.isin(mon, [1, 2])), "Mar": q1(mon == 3)}
    res["soil_gradient_by_bundle"] = {b + 1: q1(bun == b) for b in range(len(mv["bundles"]))}
    # (2) cured fuel at the step origin vs all detections of the same season-month
    con = sqlite3.connect(os.path.join(ROOT, "db.sqlite3")); r = con.execute("select bbox_minx,bbox_miny,bbox_maxx,bbox_maxy from aois where id=?", (AOI,)).fetchone()
    dry_f = cf.dry_days(lo0, la0, dn); okd = np.isfinite(dry_f) & np.isin(mon, MONTHS)
    res["cured_at_front"] = {}
    for m in MONTHS:
        mm = okd & (mon == m)
        if mm.sum() < 20: continue
        seasons = sorted(set(sea[mm])); base = []
        for s in seasons:
            y = s if m >= 8 else s + 1
            det = con.execute("select longitude, latitude, acq_date from fire_detections where acq_date between ? and ? and longitude between ? and ? and latitude between ? and ? order by random() limit 4000",
                              (f"{y}-{m:02d}-01", f"{y}-{m:02d}-31", r[0], r[2], r[1], r[3])).fetchall()
            if det:
                dd = cf.dry_days([x[0] for x in det], [x[1] for x in det], [x[2] for x in det]); dd = dd[np.isfinite(dd)]
                if len(dd): base.append((len(dd), float((dd >= 10).mean()), float(np.median(dd))))
        if not base: continue
        wsum = sum(b[0] for b in base)
        res["cured_at_front"][MN[m]] = dict(n_steps=int(mm.sum()), front_cured10_share=round(float((dry_f[mm] >= 10).mean()), 3), front_dry_days_median=float(np.median(dry_f[mm])),
                                            allfire_cured10_share=round(sum(b[0] * b[1] for b in base) / wsum, 3), allfire_dry_days_median=round(sum(b[0] * b[2] for b in base) / wsum, 1), n_allfire=wsum)
    # (3) rain-gated close: per 0.1 deg cell & season, last detection day vs first >= 10 mm rain day after 15 Feb
    res["season_close"] = {}
    for s in sorted(set(sea)):
        y = s + 1
        if cf.day_index.get(cf.daynum(date(y, 2, 15))) is None or cf.day_index.get(cf.daynum(date(y, 4, 30))) is None: continue
        det = con.execute("select round(longitude,1), round(latitude,1), max(acq_date) from fire_detections where acq_date between ? and ? and longitude between ? and ? and latitude between ? and ? group by 1,2 having count(*) >= 20",
                          (f"{y}-01-01", f"{y}-05-31", r[0], r[2], r[1], r[3])).fetchall()
        if len(det) < 50: continue
        lon_c = np.array([d[0] for d in det]); lat_c = np.array([d[1] for d in det]); last = np.array([cf.daynum(d[2]) for d in det])
        d_from = cf.daynum(date(y, 2, 15)); first_rain = np.full(len(det), np.nan)
        rr, cc, okc = cf._rc(lon_c, lat_c)
        for k in range(0, 75):
            j = cf.day_index.get(d_from + k)
            if j is None: break
            v = cf.rain_d[j, rr, cc]; hit = np.isnan(first_rain) & okc & (v >= 10); first_rain[hit] = d_from + k
        m = np.isfinite(first_rain) & (last >= d_from)
        if m.sum() < 50: continue
        from scipy.stats import spearmanr
        rho = spearmanr(first_rain[m], last[m]).correlation; nv = np.array([spearmanr(rng.permutation(first_rain[m]), last[m]).correlation for _ in range(N_NULL)])
        res["season_close"][f"{s}/{s + 1 - 2000:02d}"] = dict(n_cells=int(m.sum()), rho=round(float(rho), 3), null_rho_sd=round(float(nv.std()), 3), z=round(float((rho - nv.mean()) / max(nv.std(), 1e-9)), 1),
                                                                 last_minus_first_rain_days_median=float(np.median(last[m] - first_rain[m])), share_last_fire_before_first_rain=round(float((last[m] < first_rain[m]).mean()), 3))
    os.makedirs(os.path.dirname(OUT), exist_ok=True); json.dump(res, open(OUT, "w"), indent=1, default=float)
    print(json.dumps({k: res[k] for k in ("soil_gradient_by_period", "cured_at_front", "season_close")}, indent=1)); print("wrote", OUT)


if __name__ == "__main__": main()
