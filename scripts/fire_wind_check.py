#!/usr/bin/env python3
"""Harmattan check: are the long (>=150 km) transhumance fire fronts steered by the daily wind?

Answers reviewer TA (AP-RCA, Oct 2026): "fire direction in Jan-Feb is set by the Harmattan, not by herds".
Every number is measured against a null in the pipeline's own style (eval_fire_null.py): the wind each step
sees is replaced by the wind at the SAME place on another day of the SAME calendar month and season (the
seasonal wind climatology is kept, the day-to-day wind is destroyed). If a front is pushed by the wind, the
real day must beat the shuffled day; if it only shares the Harmattan's general direction, it will not.

    python3 -W ignore scripts/fire_wind_check.py            # -> data/eval/fire_wind_check.json
    .venv/bin/python scripts/fire_wind_check.py --figure    # -> reports/HARMATTAN_CHECK_2026-10.png from the json

Units: a STEP is one dated move of a front between consecutive daily centroids (the scale wind acts at);
a FRONT is the whole trajectory. Bundles are movement.json's 14 undirected corridors (nearest-centroid, as
`plan_solver.movement()` assigns held-out fronts). Nothing in the fire tracker is touched.
"""
import argparse, json, math, os, sys
from collections import Counter, defaultdict
from datetime import date
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from gzjson import gz
from wind_field import WindField, ang_diff, bearing, dist_km, wind_to_deg, steps

AOI = "XSA_Study_Area"
SOLVER = os.path.join(ROOT, "data/plan_zones/solver")
OUT_JSON = os.path.join(ROOT, "data/eval/fire_wind_check.json")
FIG = os.path.join(ROOT, "reports/HARMATTAN_CHECK_2026-10.png")
MONTHS = [10, 11, 12, 1, 2, 3]
MN = {10: "Oct", 11: "Nov", 12: "Dec", 1: "Jan", 2: "Feb", 3: "Mar"}
TANGO_ZONES = [255, 267, 264, 251]
N_NULL = 200
HARMATTAN_TO = 225.0   # NE -> SW, the reviewer's premise; the measured wind-to is reported beside it


def season_of(d):
    y, m = int(d[:4]), int(d[5:7]); return y if m >= 8 else y - 1


def classify(diff):
    a = np.abs(diff); return np.where(a <= 45, 0, np.where(a >= 135, 2, 1))   # 0 down, 1 cross, 2 up


def shares(cls):
    n = len(cls); return [round(float((cls == k).sum() / n), 3) for k in (0, 1, 2)] if n else [None] * 3


def circ_mean(deg):
    r = np.radians(deg); return float(np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360), float(np.hypot(np.sin(r).mean(), np.cos(r).mean()))


def spearman(a, b):
    from scipy.stats import spearmanr
    if len(a) < 10: return None
    r = spearmanr(a, b); return [round(float(r.correlation), 3), float(r.pvalue)]


def load_fronts(G):
    raw = gz.load(os.path.join(ROOT, "data/fire_groups_v5", f"{AOI}.json"))
    T = []
    for g in raw:
        t = g.get("trajectory") or []
        if len(t) < 2 or g.get("group_type") != "transhumance" or float(g.get("distance_km") or 0) < 150: continue
        rr, cc = G.rc_arr([p[0] for p in t], [p[1] for p in t])
        if not G.inside(rr, cc).any(): continue
        T.append(g)
    return T


def assign_bundles(T, mv, G):
    kx = 111 * math.cos(math.radians(G.aoi.centroid.y))
    C = np.array([[b["start"][0] * kx, b["start"][1] * 111, b["end"][0] * kx, b["end"][1] * 111] for b in mv["bundles"]])
    X = np.array([[g["trajectory"][0][0] * kx, g["trajectory"][0][1] * 111, g["trajectory"][-1][0] * kx, g["trajectory"][-1][1] * 111] for g in T])
    d1 = ((X[:, None, :] - C[None]) ** 2).sum(2); d2 = ((X[:, [2, 3, 0, 1]][:, None, :] - C[None]) ** 2).sum(2)
    lab = np.argmin(np.minimum(d1, d2), 1); flip = d2[np.arange(len(X)), lab] < d1[np.arange(len(X)), lab]
    return lab, flip, kx


def main():
    import plan_solver as S
    st = S.load_state(); G = st["G"]
    mv = json.load(open(os.path.join(SOLVER, "movement.json"))); masks = np.load(os.path.join(SOLVER, "movement_bundle_masks.npy"))
    wf = WindField.load(AOI); cov = wf.covered(); print("wind covered", cov, len(wf.days), "days")
    T = load_fronts(G); lab, flip, kx = assign_bundles(T, mv, G); nb = len(mv["bundles"])
    print(f"{len(T)} long transhumance fronts; bundle counts (ours vs movement.json fit+holdout):")
    assign_check = []
    for b in range(nb):
        ours = int((lab == b).sum()); ref = mv["bundles"][b]["fronts_fit"] + mv["bundles"][b]["fronts_holdout"]
        assign_check.append(dict(bundle=b + 1, ours=ours, ref=ref)); print(f"  b{b + 1}: {ours} vs {ref}")
    # zones (TANGO corridor zones) rasterised to the planner grid
    from shapely.geometry import shape
    from shapely.ops import transform as sh_transform
    import plan_conservancy_units as P
    zs = json.load(open(os.path.join(SOLVER, "zones.geojson")))["features"]
    zone_r = {}
    for f in zs:
        if f["properties"]["uid"] in TANGO_ZONES:
            zone_r[f["properties"]["uid"]] = G.rasterize([(sh_transform(P.FWD, shape(f["geometry"])), 1)]).astype(bool)
    print("zone cells", {z: int(m_.sum()) for z, m_ in zone_r.items()})
    zone_bundles = {f["properties"]["uid"]: [d["bundle"] for d in json.loads(f["properties"]["herd_bundles"])] for f in zs if f["properties"]["uid"] in TANGO_ZONES}

    # ---------------------------------------------------------------- step table
    rows = []   # per step
    fr = []     # per front
    for i, g in enumerate(T):
        S_ = steps(g["trajectory"]); sd = g["start_date"]; se = season_of(sd)
        for lo, la, d0, d1, b, km, gap in S_:
            rows.append((i, lo, la, WindField.daynum(d0), gap, int(d0[5:7]), se, b, km, lab[i]))
        t = g["trajectory"]
        fr.append(dict(i=i, bundle=int(lab[i]), flip=bool(flip[i]), season=se, month=int(sd[5:7]), days=g["days"], km=g["distance_km"], speed=g["speed_km_day"],
                       bearing=float(bearing(t[0][0], t[0][1], t[-1][0], t[-1][1])), start=sd, lon=[p[0] for p in t], lat=[p[1] for p in t], dates=[p[2] for p in t]))
    R = np.array([(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]) for r in rows], dtype=float)
    fi, lon, lat, d0, gap, mon, sea, brg, km, bun = R.T; fi = fi.astype(int); d0 = d0.astype(int); gap = gap.astype(int); mon = mon.astype(int); sea = sea.astype(int); bun = bun.astype(int)
    speed = km / gap
    # wind over the days the step was under way (vector mean), day-mean and pm
    W = {}
    for which in ("day", "pm"):
        su = np.zeros(len(R)); sv = np.zeros(len(R)); cnt = np.zeros(len(R))
        for k in range(int(gap.max())):
            m = gap > k
            u, v = wf.vec(lon[m], lat[m], d0[m] + k, which); ok = np.isfinite(u)
            idx = np.flatnonzero(m)[ok]; su[idx] += u[ok]; sv[idx] += v[ok]; cnt[idx] += 1
        with np.errstate(invalid="ignore", divide="ignore"):
            W[which] = (su / cnt, sv / cnt, cnt > 0)
    u, v, has = W["day"]; up, vp, _ = W["pm"]
    wto = wind_to_deg(u, v); wspd = np.hypot(u, v); diff = ang_diff(brg, wto); cls = classify(diff)
    wto_pm = wind_to_deg(up, vp); diff_pm = ang_diff(brg, wto_pm); cls_pm = classify(diff_pm)
    keep = has & np.isin(mon, MONTHS)
    print(f"{len(R)} steps, {keep.sum()} with wind in Oct-Mar")
    rr, cc = G.rc_arr(lon, lat); gin = G.inside(rr, cc)
    in_zone = {z: np.zeros(len(R), bool) for z in zone_r}
    for z, m_ in zone_r.items(): in_zone[z][gin] = m_[rr[gin], cc[gin]]

    # ---------------------------------------------------------------- null: shuffle wind days within (season, month) at the step's own place
    rng = np.random.default_rng(0)
    def null_draw():
        """each step keeps its place and gap but takes the wind of a random other step-day of the same season-month"""
        nd = d0.copy()
        for key in set(zip(sea[keep], mon[keep])):
            m = keep & (sea == key[0]) & (mon == key[1]); idx = np.flatnonzero(m)
            nd[idx] = d0[rng.permutation(idx)]
        su = np.zeros(len(R)); sv = np.zeros(len(R)); cnt = np.zeros(len(R))
        for k in range(int(gap[keep].max())):
            m = keep & (gap > k)
            uu, vv = wf.vec(lon[m], lat[m], nd[m] + k, "day"); ok = np.isfinite(uu)
            idx = np.flatnonzero(m)[ok]; su[idx] += uu[ok]; sv[idx] += vv[ok]; cnt[idx] += 1
        with np.errstate(invalid="ignore", divide="ignore"):
            nwto = wind_to_deg(su / cnt, sv / cnt)
        return classify(ang_diff(brg, nwto)), cnt > 0
    nulls = [null_draw() for _ in range(N_NULL)]

    def real_vs_null(mask):
        """downwind share real, null mean/sd, z, skill = real - null, p (one-sided, null >= real)"""
        m = mask & keep; n = int(m.sum())
        if n < 20: return dict(n=n)
        real = float((cls[m] == 0).mean()); up_ = float((cls[m] == 2).mean())
        nv = np.array([float((c[m & ok] == 0).mean()) for c, ok in nulls]); nu = np.array([float((c[m & ok] == 2).mean()) for c, ok in nulls])
        return dict(n=n, down=round(real, 3), cross=round(float((cls[m] == 1).mean()), 3), up=round(up_, 3),
                    down_pm=round(float((cls_pm[m] == 0).mean()), 3),
                    null_down=round(float(nv.mean()), 3), null_down_sd=round(float(nv.std()), 4), null_up=round(float(nu.mean()), 3),
                    skill_down=round(real - float(nv.mean()), 3), z=round(float((real - nv.mean()) / max(nv.std(), 1e-9)), 1),
                    p=round(float((nv >= real).mean()), 3),
                    wind_to_deg=round(circ_mean(wto[m])[0]), wind_R=round(circ_mean(wto[m])[1], 2), wind_ms=round(float(wspd[m].mean()), 2),
                    step_km_day=round(float(np.median(speed[m])), 2), align_deg=round(circ_mean(diff[m])[0]))
    res = dict(aoi=AOI, wind_covered=cov, wind_days=int(len(wf.days)), n_fronts=len(T), n_steps=int(len(R)), n_steps_wind=int(keep.sum()), n_null=N_NULL,
               assign_check=assign_check, zone_bundles=zone_bundles, harmattan_to_assumed=HARMATTAN_TO)
    # Q1 by month, by bundle x month, by period
    res["by_month"] = {MN[m]: real_vs_null(mon == m) for m in MONTHS}
    jf = np.isin(mon, [1, 2]); od = np.isin(mon, [10, 11, 12]); mr = mon == 3
    res["by_period"] = {"Oct-Dec": real_vs_null(od), "Jan-Feb": real_vs_null(jf), "Mar": real_vs_null(mr), "Oct-Mar": real_vs_null(np.isin(mon, MONTHS))}
    # strong-wind days only (top quartile of wind speed within the period): if wind steers fire, alignment must rise here
    q75 = {k: np.percentile(wspd[keep & m_], 75) for k, m_ in (("Oct-Dec", od), ("Jan-Feb", jf)) if (keep & m_).sum() > 20}
    res["by_period_strong_wind"] = {k: dict(wind_ms_min=round(float(q75[k]), 2), **real_vs_null(m_ & (wspd >= q75[k]))) for k, m_ in (("Oct-Dec", od), ("Jan-Feb", jf)) if k in q75}
    res["by_season_period"] = {f"{s}/{s + 1 - 2000:02d}": {"Oct-Dec": real_vs_null(od & (sea == s)), "Jan-Feb": real_vs_null(jf & (sea == s)), "Mar": real_vs_null(mr & (sea == s))} for s in sorted(set(sea[keep]))}
    res["rose"] = {k: np.histogram(diff[keep & m_] % 360, bins=np.arange(0, 361, 20))[0].tolist() for k, m_ in (("Oct-Dec", od), ("Jan-Feb", jf), ("Mar", mr))}
    res["by_bundle"] = {}
    for b in range(nb):
        res["by_bundle"][b + 1] = {"Oct-Dec": real_vs_null(od & (bun == b)), "Jan-Feb": real_vs_null(jf & (bun == b)), "Mar": real_vs_null(mr & (bun == b)),
                                   "by_month": {MN[m]: real_vs_null((mon == m) & (bun == b)) for m in MONTHS}}
    res["by_zone"] = {z: {"Oct-Dec": real_vs_null(od & in_zone[z]), "Jan-Feb": real_vs_null(jf & in_zone[z]), "Mar": real_vs_null(mr & in_zone[z])} for z in zone_r}

    # Q2 speed: step speed by alignment class and month; Spearman(step speed, wind speed); front-level duration/speed
    res["speed"] = {}
    for m in MONTHS:
        mm = keep & (mon == m)
        if mm.sum() < 20: continue
        res["speed"][MN[m]] = dict(n=int(mm.sum()),
                                   step_km_day_median={"down": round(float(np.median(speed[mm & (cls == 0)])), 2), "cross": round(float(np.median(speed[mm & (cls == 1)])), 2), "up": round(float(np.median(speed[mm & (cls == 2)])), 2)},
                                   step_km_day_p90=round(float(np.percentile(speed[mm], 90)), 1), step_gt20_share=round(float((speed[mm] > 20).mean()), 3),
                                   spearman_speed_vs_wind=spearman(speed[mm], wspd[mm]),
                                   spearman_speed_vs_cos=spearman(speed[mm], np.cos(np.radians(diff[mm]))),
                                   wind_ms_median=round(float(np.median(wspd[mm])), 2), wind_ms_p90=round(float(np.percentile(wspd[mm], 90)), 2))
    F_month = defaultdict(list)
    for f in fr: F_month[f["month"]].append(f)
    res["fronts_by_month"] = {MN[m]: dict(n=len(F_month[m]), days_median=float(np.median([f["days"] for f in F_month[m]])), speed_km_day_median=round(float(np.median([f["speed"] for f in F_month[m]])), 1),
                                         speed_gt20_share=round(float(np.mean([f["speed"] > 20 for f in F_month[m]])), 3), days_lt7_share=round(float(np.mean([f["days"] < 7 for f in F_month[m]])), 3),
                                         km_median=round(float(np.median([f["km"] for f in F_month[m]])))) for m in MONTHS if F_month[m]}
    # front-level alignment: whole-front bearing vs the vector-mean wind over its steps
    res["fronts_align"] = {}
    for m in MONTHS:
        c = Counter(); n = 0
        for f in F_month[m]:
            ms = keep & (fi == f["i"])
            if ms.sum() < 2: continue
            w_to = wind_to_deg(u[ms].mean(), v[ms].mean()); d = abs(float(ang_diff(f["bearing"], w_to))); c["down" if d <= 45 else "up" if d >= 135 else "cross"] += 1; n += 1
        if n: res["fronts_align"][MN[m]] = dict(n=n, down=round(c["down"] / n, 3), cross=round(c["cross"] / n, 3), up=round(c["up"] / n, 3))

    # Q3 reversal: per bundle, share of fronts read start->end by month; Jan-Feb bearings vs Harmattan vs axis
    res["reversal"] = {}
    for b in range(nb):
        B = mv["bundles"][b]; ax = float(bearing(B["start"][0], B["start"][1], B["end"][0], B["end"][1]))
        fb = [f for f in fr if f["bundle"] == b]
        bym = {}
        for m in MONTHS:
            fm = [f for f in fb if f["month"] == m]
            if len(fm) >= 5:
                br = np.array([f["bearing"] for f in fm]); da = np.abs(ang_diff(br, ax))
                bym[MN[m]] = dict(n=len(fm), forward=round(float(np.mean([not f["flip"] for f in fm])), 2), on_axis=round(float(((da <= 45) | (da >= 135)).mean()), 2))
        fjf = [f for f in fb if f["month"] in (1, 2)]
        mb = jf & (bun == b) & keep
        w_to_local = circ_mean(wto[mb])[0] if mb.sum() else None
        br = np.array([f["bearing"] for f in fjf]) if fjf else np.array([])
        res["reversal"][b + 1] = dict(axis_deg=round(ax), axis_back_deg=round((ax + 180) % 360), share_forward_all=B["share_forward"], by_month=bym,
                                      jf_n=len(fjf), jf_wind_to_deg=None if w_to_local is None else round(w_to_local),
                                      jf_within45_axis_either=None if not len(br) else round(float(((np.abs(ang_diff(br, ax)) <= 45) | (np.abs(ang_diff(br, ax + 180)) <= 45)).mean()), 2),
                                      jf_within45_wind=None if (w_to_local is None or not len(br)) else round(float((np.abs(ang_diff(br, w_to_local)) <= 45).mean()), 2),
                                      jf_within45_harmattan225=None if not len(br) else round(float((np.abs(ang_diff(br, HARMATTAN_TO)) <= 45).mean()), 2),
                                      jf_forward=None if not fjf else round(float(np.mean([not f["flip"] for f in fjf])), 2),
                                      axis_vs_harmattan_deg=round(min(abs(float(ang_diff(ax, HARMATTAN_TO))), abs(float(ang_diff(ax + 180, HARMATTAN_TO))))))

    # Q5 hold-out capture by period (band masks as fitted; no solver re-run) + axis without Jan-Feb starts
    hold = mv["hold_season"]
    def inside_share(f, band):
        r_, c_ = G.rc_arr(f["lon"], f["lat"]); ok = G.inside(r_, c_); return float(band[r_[ok], c_[ok]].mean()) if ok.any() else 0.0
    res["holdout"] = {}; res["axis_shift"] = {}
    for b in range(nb):
        band = masks[b]; fb = [f for f in fr if f["bundle"] == b]
        ho = [f for f in fb if f["season"] == hold]; fit = [f for f in fb if f["season"] != hold and f["season"] <= hold + 1]
        def cap(fs): return None if not fs else round(float(np.mean([inside_share(f, band) >= 0.5 for f in fs])), 2)
        res["holdout"][b + 1] = dict(all=cap(ho), n_all=len(ho), oct_dec_mar=cap([f for f in ho if f["month"] in (10, 11, 12, 3)]), n_oct_dec_mar=len([f for f in ho if f["month"] in (10, 11, 12, 3)]),
                                     jan_feb=cap([f for f in ho if f["month"] in (1, 2)]), n_jan_feb=len([f for f in ho if f["month"] in (1, 2)]), ref=mv["bundles"][b]["holdout_capture"])
        def axis(fs):
            if not fs: return None
            P_ = np.array([[f["lon"][0] * kx, f["lat"][0] * 111, f["lon"][-1] * kx, f["lat"][-1] * 111] for f in fs]); fl = np.array([f["flip"] for f in fs])
            P_ = np.where(fl[:, None], P_[:, [2, 3, 0, 1]], P_); return P_.mean(0)
        a_all = axis(fit); a_no = axis([f for f in fit if f["month"] not in (1, 2)])
        if a_all is not None and a_no is not None:
            res["axis_shift"][b + 1] = dict(n_fit=len(fit), n_fit_no_jf=len([f for f in fit if f["month"] not in (1, 2)]),
                                            start_shift_km=round(float(np.hypot(*(a_no[:2] - a_all[:2]))), 1), end_shift_km=round(float(np.hypot(*(a_no[2:] - a_all[2:]))), 1),
                                            bearing_change_deg=round(float(abs(ang_diff(math.degrees(math.atan2(a_no[2] - a_no[0], a_no[3] - a_no[1])), math.degrees(math.atan2(a_all[2] - a_all[0], a_all[3] - a_all[1]))))), 1))
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    json.dump(res, open(OUT_JSON, "w"), indent=1, default=float)
    print(json.dumps({k: res[k] for k in ("by_period", "fronts_by_month")}, indent=1))
    print("wrote", OUT_JSON)


def figure():
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    res = json.load(open(OUT_JSON)); fig, ax = plt.subplots(2, 2, figsize=(13, 9.5))
    # (a) downwind share by month, real vs null
    a = ax[0, 0]; ms = [m for m in MN.values() if res["by_month"][m].get("down") is not None]
    real = [res["by_month"][m]["down"] for m in ms]; nul = [res["by_month"][m]["null_down"] for m in ms]; sd = [res["by_month"][m]["null_down_sd"] for m in ms]
    upr = [res["by_month"][m]["up"] for m in ms]
    x = np.arange(len(ms)); a.bar(x - 0.2, real, 0.4, label="downwind (|Δ|≤45°), real", color="#c44"); a.bar(x + 0.2, upr, 0.4, label="upwind (|Δ|≥135°), real", color="#48c")
    a.errorbar(x - 0.2, nul, yerr=np.array(sd) * 2, fmt="k_", ms=14, label="downwind, day-shuffled wind (±2 sd)"); a.axhline(0.25, ls=":", c="grey", label="uniform bearing 0.25")
    a.set_xticks(x); a.set_xticklabels([f"{m}\nn={res['by_month'][m]['n']:,}" for m in ms]); a.set_ylim(0, 0.6); a.set_ylabel("share of daily steps"); a.legend(fontsize=8, loc="upper left")
    a.set_title("(a) Step bearing vs same-day ERA5 10 m wind, long transhumance fronts", fontsize=10)
    # (b) rose of step bearing - wind-to, Jan-Feb vs Oct-Dec
    ax[0, 1].remove(); b = fig.add_subplot(2, 2, 2, projection="polar"); b.set_theta_zero_location("N"); b.set_theta_direction(-1)
    hist = res.get("rose", {})
    for key, col in (("Oct-Dec", "#48c"), ("Jan-Feb", "#c44"), ("Mar", "#4a4")):
        if key in hist and sum(hist[key]) > 0:
            h = np.array(hist[key]); th = np.radians(np.arange(0, 360, 360 / len(h)) + 180 / len(h)); b.bar(th, h / h.sum(), width=2 * np.pi / len(h), alpha=0.45, color=col, label=key)
    b.set_title("(b) step bearing minus wind-to bearing (0 = downwind)", fontsize=10); b.legend(fontsize=8, loc="lower left", bbox_to_anchor=(-0.1, -0.1))
    # (c) speed vs wind: medians by class per month
    c = ax[1, 0]; sp = res["speed"]; ms2 = [m for m in MN.values() if m in sp]
    for k, col in (("down", "#c44"), ("cross", "#888"), ("up", "#48c")):
        c.plot(ms2, [sp[m]["step_km_day_median"][k] for m in ms2], "o-", color=col, label=f"{k}wind steps, median km/day")
    c.plot(ms2, [sp[m]["step_km_day_p90"] for m in ms2], "k--", label="all steps, p90 km/day")
    c2 = c.twinx(); c2.plot(ms2, [sp[m]["wind_ms_median"] for m in ms2], "s:", color="#a60", label="median wind m/s"); c2.set_ylabel("10 m wind (m/s)", color="#a60")
    c.set_ylabel("step speed (km/day)"); c.legend(fontsize=8, loc="upper left"); c2.legend(fontsize=8, loc="upper right")
    c.set_title("(c) Step speed by wind alignment; ρ(speed, wind) in table", fontsize=10)
    # (d) per bundle: Jan-Feb downwind skill and hold-out capture split
    d = ax[1, 1]; bs = sorted(res["by_bundle"], key=int)
    sk = [res["by_bundle"][k]["Jan-Feb"].get("skill_down", 0) or 0 for k in bs]; sk_od = [res["by_bundle"][k]["Oct-Dec"].get("skill_down", 0) or 0 for k in bs]
    xb = np.arange(len(bs)); d.bar(xb - 0.2, sk_od, 0.4, color="#48c", label="Oct–Dec: downwind share − null"); d.bar(xb + 0.2, sk, 0.4, color="#c44", label="Jan–Feb: downwind share − null")
    d.axhline(0, c="k", lw=0.8); d.set_xticks(xb); d.set_xticklabels([f"b{k}" for k in bs]); d.set_ylabel("skill vs day-shuffled wind")
    d2 = d.twinx(); ho = res["holdout"]
    d2.plot(xb, [ho[k]["oct_dec_mar"] for k in bs], "D", color="#246", label="hold-out capture, Oct–Dec+Mar fronts"); d2.plot(xb, [ho[k]["jan_feb"] for k in bs], "x", color="#a22", ms=9, label="hold-out capture, Jan–Feb fronts")
    d2.set_ylim(0, 1); d2.set_ylabel("2025/26 hold-out capture"); d.legend(fontsize=8, loc="upper left"); d2.legend(fontsize=8, loc="upper right")
    d.set_title("(d) Per corridor bundle", fontsize=10)
    fig.suptitle(f"Harmattan check — {res['n_fronts']:,} long (≥150 km) transhumance fronts, {res['n_steps_wind']:,} daily steps with ERA5 wind ({res['wind_covered'][0]} … {res['wind_covered'][1]}); null = {res['n_null']} day-shuffles within season-month", fontsize=10)
    fig.tight_layout(); fig.savefig(FIG, dpi=130); print("wrote", FIG)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--figure", action="store_true"); a = ap.parse_args()
    figure() if a.figure else main()
