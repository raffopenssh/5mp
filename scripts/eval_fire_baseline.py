#!/usr/bin/env python3
"""Baseline ladder for the herd heuristic: "where the fires are is where the herds are" (2026-09-15).

Why this exists. Every fire null in this repo (eval_fire_null, eval_fire_vanguard, plan_solver.movement) shuffles
calendar days WITHIN a month or compares a band to equal-area all-fire density. Both keep the spatial field and the
monthly march fixed - i.e. they hold constant exactly the two things the field heuristic *is*. "Real ~ shuffled" there
says day ORDER cannot be read; it says nothing for or against the heuristic. This script scores the heuristic itself
against baselines that do NOT already contain the answer, on every area that has a season front (162 parks + AOIs,
8-9 seasons each), rolling origin: for held-out season s, fit on seasons < s only.

Predictors (one score per cell of the area's front grid, RES_DEG 0.025 ~ 2.8 km, smoothed sigma 4 km):
    uniform       area share over the cells known burnable before the season (skill 0 by construction)
    persist       last season's detection density               -- "look at last year's NASA fires"
    clim          mean density of all prior seasons               -- the climatology / the map's heat field
    clim_early    prior seasons' density of the FIRST 45 d of each cell's local season (front lead >= -45 d)
    (XSA only)    the report's movement bands (movement_ud.npy), river proximity, low people, open grassland

Targets (held-out season s):
    all           every detection of the season
    vanguard      detections that fall 10-60 d AHEAD of the season front at their cell (the leading edge the
                  leaflet flights go to; the front is causal, known the day it happens)
    (XSA only)    long transhumance fronts >= 150 km and KF vanguard chains (inside-share >= 0.5)

Metric: capture curve - share of the target inside the top-a fraction of the area's cells by predictor score,
a in {5,10,20,30,50} %; skill(a) = capture - a. AUC = area under the capture curve minus 0.5 (Gini). A predictor
that merely knows the area's outline scores 0. Median and IQR across (area, season) pairs are the result; nothing
is fitted, nothing is tuned.

    python3 -W ignore scripts/eval_fire_baseline.py                 # all areas, ~10 min, writes data/eval/fire_baseline.json
    python3 -W ignore scripts/eval_fire_baseline.py --area CAF_Chinko XSA_Study_Area
    python3 -W ignore scripts/eval_fire_baseline.py --xsa            # the XSA ladder incl. the report's bands + covariates

Reads the DB only. Writes data/eval/fire_baseline.json and prints the tables (docs/agents/fire.md "Baseline ladder")."""
import argparse, json, math, sqlite3, sys, time, warnings; warnings.filterwarnings("ignore")
from datetime import date
from pathlib import Path
import numpy as np
from scipy import ndimage
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "scripts"))
import fire_front as FF

A_FRACS = [0.05, 0.10, 0.20, 0.30, 0.50]
SIGMA_KM = 4.0
LEAD_LO, LEAD_HI = FF.VANGUARD_LEAD_DAYS, FF.VANGUARD_LEAD_MAX
EARLY_D = 45


def capture_curve(score, target, mask, fracs=A_FRACS):
    """share of `target` mass inside the top-a fraction of `mask` cells ranked by `score`; returns (caps, gini)."""
    s = score[mask].astype(float); t = target[mask].astype(float)
    if t.sum() <= 0: return None, None
    order = np.argsort(-s, kind="stable"); ct = np.cumsum(t[order]) / t.sum(); n = len(s)
    caps = [float(ct[min(int(round(a * n)) - 1, n - 1)]) if int(round(a * n)) >= 1 else 0.0 for a in fracs]
    gini = float(2 * (ct.mean() - 0.5))  # area under capture curve vs. the diagonal, in [-1, 1]
    return caps, gini


def smooth(a, sig_cells): return ndimage.gaussian_filter(a.astype(np.float32), sig_cells)


def load_area(conn, area_id):
    """per-season front grids + detection cells. Returns dict or None."""
    rows = conn.execute("SELECT season, season_start, season_end, complete, res, x0, y0, nx, ny, front FROM fire_season_front "
                        "WHERE area_id=? ORDER BY season", (area_id,)).fetchall()
    if not rows: return None
    # one grid per area: seasons stored on another grid (area re-gridded between builds) are skipped, not re-indexed
    from collections import Counter
    key = Counter(tuple(r[4:9]) for r in rows).most_common(1)[0][0]; rows = [r for r in rows if tuple(r[4:9]) == key]
    res, x0, y0, nx, ny = key
    g = FF.Grid(x0, y0, nx, ny, res)
    lon, lat, day = FF.load_points(conn, area_id)
    if lon.size == 0: return None
    iy, ix = g.index(lon, lat)
    # seasons the front table does not hold but the detections do (an AOI's front starts with its data year;
    # the same calendar rule is shifted back a year at a time) enter as PRIOR seasons only (no front -> no vanguard target)
    rows = [list(r) for r in rows]
    first = rows[0]
    while True:
        s0 = date.fromordinal(date.fromisoformat(first[1]).toordinal() - 365); s1 = date.fromisoformat(first[1])
        if ((day >= s0.toordinal()) & (day < s1.toordinal())).sum() < FF.MIN_SEASON_DETECTIONS: break
        label = f"{s0.year}/{str(s0.year + 1)[2:]}" if s0.year != s1.year or True else ""
        first = [label, s0.isoformat(), s1.isoformat(), 1, res, x0, y0, nx, ny, None]; rows.insert(0, first)
    if len(rows) < 3: return None
    seasons = []
    for season, s0, s1, complete, *_r, front in rows:
        o0, o1 = date.fromisoformat(s0).toordinal(), date.fromisoformat(s1).toordinal()
        sel = (day >= o0) & (day < o1)
        if sel.sum() < FF.MIN_SEASON_DETECTIONS: continue
        dens = np.zeros((ny, nx), np.float32); np.add.at(dens, (iy[sel], ix[sel]), 1)
        fr = FF.unpack(front, ny, nx) if front is not None else np.full((ny, nx), np.nan)
        dos = (day[sel] - o0).astype(float); lead = fr[iy[sel], ix[sel]] - dos  # front day - detection day
        van = np.zeros((ny, nx), np.float32); vs = (lead >= LEAD_LO) & (lead <= LEAD_HI); np.add.at(van, (iy[sel][vs], ix[sel][vs]), 1)
        early = np.zeros((ny, nx), np.float32); es = lead >= -EARLY_D; np.add.at(early, (iy[sel][es], ix[sel][es]), 1)
        seasons.append(dict(season=season, complete=bool(complete), synthetic=front is None, n=int(sel.sum()), n_van=int(vs.sum()), dens=dens, van=van, early=early,
                            has_front=bool(np.isfinite(fr).any())))
    if len(seasons) < 3: return None
    cell_km = res * 111 * math.cos(math.radians(y0 + ny * res / 2))
    mask = np.ones((ny, nx), bool)
    return dict(area_id=area_id, grid=g, mask=mask, seasons=seasons, sig=SIGMA_KM / max(cell_km, 0.5), cell_km=cell_km)


def ladder_area(A, extra=None):
    """rolling-origin ladder for one area. extra: dict name->score grid (static predictors, XSA). Returns list of rows."""
    S = A["seasons"]; sig = A["sig"]; mask = A["mask"]; out = []
    for k in range(2, len(S)):
        hold = S[k]
        if not hold["complete"]: continue
        prior = S[:k]
        # score only the cells known burnable BEFORE the held-out season (any prior detection), inside the area's mask:
        # the baseline then already knows the outline of the fire field, and skill is what density adds on top
        mask = A["mask"] & (np.sum([p["dens"] for p in prior], 0) > 0)
        preds = {
            "uniform": None,
            "persist": smooth(prior[-1]["dens"], sig),
            "clim": smooth(np.mean([p["dens"] for p in prior], 0), sig),
            "clim_early": smooth(np.mean([p["early"] for p in prior if p["has_front"]] or [np.zeros(mask.shape)], 0), sig),
        }
        if extra: preds.update(extra)
        for tname, tgt in (("all", hold["dens"]), ("vanguard", hold["van"])):
            if tgt.sum() < 50: continue
            for pname, sc in preds.items():
                if sc is None:  # uniform over the burnable cells: analytic diagonal (a random order has this expectation)
                    caps, gini = list(A_FRACS), 0.0
                else:
                    caps, gini = capture_curve(sc, tgt, mask)
                if caps is None: continue
                out.append(dict(area=A["area_id"], season=hold["season"], n_prior=len(prior), target=tname, n_target=float(tgt.sum()),
                                n_cells=int(mask.sum()), outside_share=float(1 - tgt[mask].sum() / tgt.sum()), pred=pname, caps=caps, gini=gini))
    return out


def summarise(rows, label):
    import collections
    by = collections.defaultdict(list)
    for r in rows: by[(r["target"], r["pred"])].append(r)
    print(f"\n== {label}: {len({(r['area'], r['season']) for r in rows})} (area, season) pairs, {len({r['area'] for r in rows})} areas")
    print(f"   {'target':9} {'predictor':11} {'n':>5}  " + "  ".join(f"cap@{int(a*100):>2}%" for a in A_FRACS) + "   gini (median [IQR])")
    summ = {}
    for t in ("all", "vanguard"):
        o = [r["outside_share"] for r in rows if r["target"] == t and r["pred"] == "clim"]
        if o: print(f"   {t:9} share of the held-out target on cells with NO prior detection (unseen by every predictor): median {np.median(o):.1%}"); summ[f"{t}/outside_share_median"] = float(np.median(o))
    for (t, p), rs in sorted(by.items()):
        C = np.array([r["caps"] for r in rs]); G = np.array([r["gini"] for r in rs])
        med = np.median(C, 0); q1, q3 = np.percentile(G, 25), np.percentile(G, 75)
        print(f"   {t:9} {p:11} {len(rs):>5}  " + "  ".join(f"{m:6.2f}" for m in med) + f"   {np.median(G):+.2f} [{q1:+.2f},{q3:+.2f}]")
        summ[f"{t}/{p}"] = dict(n=len(rs), cap_median=[float(x) for x in med], gini_median=float(np.median(G)), gini_q1=float(q1), gini_q3=float(q3))
    # paired differences (same area+season): persist - clim, clim - uniform
    def paired(t, p1, p2):
        d = {}
        for r in rows:
            if r["target"] == t and r["pred"] in (p1, p2): d.setdefault((r["area"], r["season"]), {})[r["pred"]] = r["gini"]
        v = np.array([x[p1] - x[p2] for x in d.values() if p1 in x and p2 in x])
        return (float(np.median(v)), float(np.mean(v > 0)), len(v)) if len(v) else (float("nan"), float("nan"), 0)
    for t in ("all", "vanguard"):
        for p1, p2 in (("clim", "persist"), ("clim_early", "clim"), ("clim", "uniform")):
            m, w, n = paired(t, p1, p2)
            if n: print(f"   paired gini {t:9} {p1} - {p2}: median {m:+.3f}, {p1} better in {w:.0%} of {n}"); summ[f"paired/{t}/{p1}-{p2}"] = dict(median=m, share_better=w, n=n)
    return summ


def xsa_extra(conn, A):
    """static, non-fire (and the report's) predictors on the XSA front grid."""
    import pickle, rasterio
    from shapely.geometry import shape
    from shapely import STRtree
    import plan_conservancy_units as P, __main__; __main__.Grid = P.Grid
    st = pickle.load(open(ROOT / "data/plan_zones/conservancy_units/state.pkl", "rb")); G = st["G"]
    g = A["grid"]; ny, nx = g.ny, g.nx
    lon = g.x0 + (np.arange(nx) + 0.5) * g.res; lat = g.y0 + (np.arange(ny) + 0.5) * g.res
    LON, LAT = np.meshgrid(lon, lat)
    rr, cc = G.rc_arr(LON.ravel(), LAT.ravel()); inside = G.inside(rr, cc)
    def from_G(arr):
        out = np.zeros(ny * nx, np.float32); out[inside] = arr[rr[inside], cc[inside]]; return out.reshape(ny, nx)
    ex = {}
    ud = np.load(ROOT / "data/plan_zones/solver/movement_ud.npy").astype(np.float32)
    ex["report_bands_ud"] = from_G(ud)
    band = np.load(ROOT / "data/plan_zones/solver/movement_band.npy").astype(np.float32)
    ex["report_bands"] = from_G(band)
    # people: fewer people -> higher score (herds avoid farms); rivers: nearer -> higher; grassland from imagery chips
    pop = ndimage.gaussian_filter(G.C["pop"].astype(np.float32), 2.0); ex["few_people"] = from_G(-np.log1p(pop))
    rivers = [f[0] for f in st["feats"] if f[2] == "river"]
    if rivers:
        tree = STRtree(rivers); pts = [P.Point(x, y) for x, y in zip(LON.ravel()[inside], LAT.ravel()[inside])] if hasattr(P, "Point") else None
        from shapely.geometry import Point
        pts = [Point(x, y) for x, y in zip(LON.ravel()[inside], LAT.ravel()[inside])]
        near = tree.nearest(pts); d = np.array([pts[i].distance(rivers[j]) for i, j in enumerate(near)]) * 111
        arr = np.zeros(ny * nx, np.float32); arr[inside] = -d; ex["near_river"] = arr.reshape(ny, nx)
    gp = ROOT / "data/plan_zones/solver/imagery_open_grassland.npy"
    if gp.exists(): ex["open_grassland"] = from_G(np.load(gp).astype(np.float32))
    A["mask"] = inside.reshape(ny, nx)  # score inside the AOI only, like the report
    return ex


def xsa_lines(conn, A, preds_by_season):
    """capture of long fronts / KF vanguard chains (inside-share >= 0.5) by each predictor's top-a mask, per held-out season."""
    g = A["grid"]; mask = A["mask"]
    def season_of(d): y, m = int(d[:4]), int(d[5:7]); return f"{y}/{str(y+1)[2:]}" if m >= 8 else f"{y-1}/{str(y)[2:]}"
    sets = {}
    for gr in json.load(open(ROOT / "data/fire_groups_v5/XSA_Study_Area.json")):
        t = gr.get("trajectory") or []
        if len(t) >= 2 and gr.get("group_type") == "transhumance" and float(gr.get("distance_km") or 0) >= 150:
            sets.setdefault(("long_fronts", season_of(gr["start_date"])), []).append(t)
    for gr in json.load(open(ROOT / "data/fire_vanguard_kf/XSA_Study_Area.json")):
        t = gr.get("trajectory") or []
        if len(t) >= 2: sets.setdefault(("kf_vanguard", season_of(gr["start_date"])), []).append(t)
    rows = []
    for (tname, season), trs in sorted(sets.items()):
        if season not in preds_by_season: continue
        idx = [g.index([p[0] for p in t], [p[1] for p in t]) for t in trs]
        for pname, sc in preds_by_season[season].items():
            s = np.where(mask, sc, -np.inf); order = np.argsort(-s.ravel(), kind="stable"); n = int(mask.sum())
            caps = []
            for a in A_FRACS:
                top = np.zeros(s.size, bool); top[order[:max(1, int(round(a * n)))]] = True; top = top.reshape(s.shape)
                caps.append(float(np.mean([top[iy, ix].mean() >= 0.5 for iy, ix in idx])))
            rows.append(dict(target=tname, season=season, n=len(trs), pred=pname, caps=caps))
    print("\n== XSA line targets (share of lines with >= 50 % of vertices inside the predictor's top-a cells)")
    print(f"   {'target':12} {'season':7} {'n':>4} {'predictor':16} " + "  ".join(f"cap@{int(a*100):>2}%" for a in A_FRACS))
    for r in rows: print(f"   {r['target']:12} {r['season']:7} {r['n']:>4} {r['pred']:16} " + "  ".join(f"{c:6.2f}" for c in r["caps"]))
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--area", nargs="*"); ap.add_argument("--xsa", action="store_true"); ap.add_argument("--db", default=str(ROOT / "db.sqlite3"))
    a = ap.parse_args(); conn = sqlite3.connect(a.db); conn.execute("PRAGMA query_only=1")
    out = dict(computed_at=time.strftime("%Y-%m-%dT%H:%M"), a_fracs=A_FRACS, sigma_km=SIGMA_KM, lead_days=[LEAD_LO, LEAD_HI], early_days=EARLY_D)
    if a.xsa:
        A = load_area(conn, "XSA_Study_Area"); ex = xsa_extra(conn, A)
        rows = ladder_area(A, ex); out["xsa"] = summarise(rows, "XSA_Study_Area (inside the AOI)")
        # per-season predictor grids for the line targets
        S = A["seasons"]; pbs = {}
        for k in range(2, len(S)):
            prior = S[:k]; pbs[S[k]["season"]] = dict(persist=smooth(prior[-1]["dens"], A["sig"]), clim=smooth(np.mean([p["dens"] for p in prior], 0), A["sig"]), **ex)
        # the report's fit seasons are 2023/24 + 2024/25, hold-out 2025/26 -> only that season is a fair line test
        out["xsa_lines"] = xsa_lines(conn, A, {s: v for s, v in pbs.items() if s == "2025/26"})
    else:
        ids = a.area or [r[0] for r in conn.execute("SELECT DISTINCT area_id FROM fire_season_front ORDER BY 1")]
        rows, skipped, t0 = [], [], time.time()
        for i, aid in enumerate(ids):
            A = load_area(conn, aid)
            if A is None: skipped.append(aid); continue
            r = ladder_area(A); rows += r
            print(f"[{i+1}/{len(ids)}] {aid}: {len(A['seasons'])} seasons, {len(r)} rows, {time.time()-t0:.0f}s", file=sys.stderr, flush=True)
        out["all"] = summarise(rows, "all areas, rolling origin"); out["skipped"] = skipped; out["n_rows"] = len(rows)
        # by area size of the fire field: dense vs sparse (median n_target on 'all')
        big = {r["area"] for r in rows if r["target"] == "all" and r["n_target"] >= 20000}
        out["dense_areas"] = summarise([r for r in rows if r["area"] in big], f"areas with >= 20k detections in a season ({len(big)})")
        json.dump(rows, open(ROOT / "data/eval/fire_baseline_rows.json", "w"), default=float)  # per (area, season, pred) — gitignored
        print(f"\nskipped ({len(skipped)}, < 3 seasons or no detections): {' '.join(skipped)}")
    p = ROOT / "data/eval" / ("fire_baseline_xsa.json" if a.xsa else "fire_baseline.json"); p.parent.mkdir(exist_ok=True)
    json.dump(out, open(p, "w"), default=float); print(f"\nwrote {p}")


if __name__ == "__main__": main()
