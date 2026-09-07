#!/usr/bin/env python3
"""Harness: train the mining-context composite on CAF+SSD+SDN, score it on XSA.

    python3 scripts/regional_mining/harness.py            # -> data/eval/regional_mining/harness.json

THE ONE NUMBER THIS EXISTS TO MOVE, HONESTLY
    "the model's top 5% of XSA cells captures 16% of the 89 known workings
    (43 site clusters), 3.2x chance"  -- scripts/predict_mining_xsa.py

The XSA model selected its signals ON the 43 XSA clusters, so 16% is an
in-sample number and the model could not be tuned without fitting the noise
of 43 points. Here the truth is every reported working in the three
countries (mining_anchors + ACLED-v8 sites), the signals are the SAME
family (geology contacts, rivers, settlement fabric, 1930s sheets), and the
protocol is:

  TRAIN  = clusters outside XSA (spatially disjoint, with a 50 km moat)
  TEST   = the XSA clusters, scored by top-5/10/20% capture INSIDE XSA
           (rank taken within XSA, as the shipped model does)

Every variant is scored the same way; the baseline row is the shipped
XSA composite rebuilt from the same features so the comparison is fair.
The reach null (target-group background) is kept: signals are admitted on
q_bh_reach < .05, lift_reach > 1, measured on TRAIN.
"""
import json, sys, csv, zipfile, io
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import shape, Point

ROOT = Path(__file__).resolve().parents[2]
F = ROOT / "data/eval/regional_mining/features.npz"
OUT = ROOT / "data/eval/regional_mining/harness.json"
KM = 111.32
NEAR = 5.0
PERMS = 3000
RNG = np.random.default_rng(20260907)
MOAT_KM = 50.0


def log(*a): print(*a, file=sys.stderr, flush=True)


def proj(lonlat, lat0):
    a = np.asarray(lonlat, float).reshape(-1, 2)
    return np.c_[a[:, 0] * KM * np.cos(np.radians(lat0)), a[:, 1] * KM]


def load_truth(lat0):
    pts, src = [], []
    d = json.load(open(ROOT / "data/geology_truth/mining_anchors.geojson"))
    for f in d["features"]:
        p = f["properties"]
        if p["iso3"] in ("CAF", "SSD", "SDN"):
            pts.append(f["geometry"]["coordinates"]); src.append(p["source"])
    a = json.load(open(ROOT / "data/eval/acled_legacy/mine_sites.json"))
    for s in a["sites"]:
        if s["iso3"] in ("CAF", "SSD", "SDN"):
            pts.append([s["lon"], s["lat"]]); src.append("acled_v8")
    return np.array(pts), np.array(src)


def cluster(pk, link_km=10.0):
    """single-link at link_km -> cluster centres (mean) + member index lists"""
    n = len(pk); parent = list(range(n))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    t = cKDTree(pk)
    for i, j in t.query_pairs(link_km):
        ri, rj = find(i), find(j)
        if ri != rj: parent[ri] = rj
    groups = {}
    for i in range(n): groups.setdefault(find(i), []).append(i)
    return [dict(km=pk[m].mean(0), members=m) for m in groups.values()]


def acled_reach_points():
    pts = []
    with open(ROOT / "data/acled_legacy/african_conflicts.csv", encoding="utf8", errors="replace") as f:
        for r in csv.DictReader(f):
            if r["COUNTRY"] in ("Central African Republic", "South Sudan", "Sudan"):
                try:
                    if int(r["GEO_PRECISION"]) <= 2: pts.append([float(r["LONGITUDE"]), float(r["LATITUDE"])])
                except ValueError: pass
    return pts


def kde(gk, pk, bw=20.0):
    w = np.zeros(len(gk)); tr = cKDTree(pk)
    for i in range(0, len(gk), 2000):
        for j, idx in enumerate(tr.query_ball_point(gk[i:i + 2000], r=3 * bw)):
            if idx:
                d2 = ((pk[idx] - gk[i + j]) ** 2).sum(1); w[i + j] = np.exp(-d2 / (2 * bw * bw)).sum()
    return w + 0.01 * w.mean()


def score_signal(cell_d, truth_d, mask, thr, cov, weights, perms=PERMS):
    """lift + permutation p under uniform and reach-weighted nulls, on cells
    in `mask` (and inside `cov` if given), truth = truth_d rows flagged ok."""
    cells_ok = mask & (cov if cov is not None else True)
    pool = cell_d[cells_ok]
    t_ok = truth_d["ok"] & (truth_d["cov"] if cov is not None else True)
    td = truth_d["d"][t_ok]; n = int(t_ok.sum())
    if n < 8 or len(pool) == 0: return dict(n=n, verdict="too_few")
    cap = float(np.mean(td <= thr)); base = float(np.mean(pool <= thr))
    lift = cap / base if base > 0 else None
    wp = weights[cells_ok]; wp = wp / wp.sum()
    base_w = float(np.sum(wp * (pool <= thr)))
    idx = RNG.choice(len(pool), size=(perms, n), p=wp)
    sims_w = np.mean(pool[idx] <= thr, axis=1)
    lift_w = cap / base_w if base_w > 0 else None
    p_w = float(np.mean(sims_w >= cap)) if lift_w and lift_w >= 1 else float(np.mean(sims_w <= cap))
    return dict(n=n, capture=round(cap, 3), baseline=round(base, 3), lift=round(lift, 2) if lift else None,
                baseline_reach=round(base_w, 3), lift_reach=round(lift_w, 2) if lift_w else None, p_reach=round(p_w, 4))


def bh(pvals):
    p = np.asarray(pvals); m = len(p); order = np.argsort(p); q = np.empty(m); prev = 1.0
    for r in range(m - 1, -1, -1):
        i = order[r]; prev = min(prev, p[i] * m / (r + 1)); q[i] = prev
    return q


def capture_at(comp, mask, tidx, fracs=(0.05, 0.10, 0.20)):
    """rank comp WITHIN mask; fraction of truth clusters (cell idx tidx) in top X%"""
    c = comp[mask]; ok = np.isfinite(c)
    ranks = np.full(len(c), np.nan); ranks[ok] = np.argsort(np.argsort(-c[ok])) / ok.sum()
    full = np.full(len(comp), np.nan); full[np.nonzero(mask)[0]] = ranks
    out = {}
    for fr in fracs:
        r = full[tidx]; out[f"top{int(fr*100):02d}"] = round(float(np.mean(r[np.isfinite(r)] < fr)), 3)
    return out


def build_signals(feats):
    """name -> (distance_km array, coverage mask or None, threshold_km, factor)"""
    # ---- signal family (same as predict_mining_xsa.py, region-wide inputs)
    d_gold = feats["d_gold"]
    d_ab_gold = np.maximum(feats["d_hist_abandoned_village"], d_gold)
    d_abt_gold = np.maximum(feats["d_hist_abandoned_track"], d_gold)
    d_abany_gold = np.minimum(d_ab_gold, d_abt_gold)
    d_water_gold = np.maximum(feats["d_hist_water"], d_gold)
    # pop>=500 AND no farmland is not reproducible region-wide without the
    # cropland clip; pop500 alone is the labour-pool signal.
    unit_gold = np.where(feats["unit_gold_w"] >= 2, 0.0, 1e9)   # "on a gold unit" as a distance
    unit_gold1 = np.where(feats["unit_gold_w"] >= 1, 0.0, 1e9)
    cov_h, cov_g = feats["cov_hist"], feats["cov_geology"]
    d_gold_sett = np.maximum(d_gold, feats["d_settlement"])
    d_gold_pop500 = np.maximum(d_gold, feats["d_pop500"])
    d_gold_river = np.maximum(d_gold, feats["d_river_osm"])
    d_unit_sett = np.maximum(unit_gold, feats["d_settlement"])
    d_gold_croppoor = np.maximum(d_gold, feats["d_sett1k_croppoor"])
    d_gold_pop500poor = np.maximum(d_gold, feats["d_sett1k_pop500_croppoor"])
    d_unit_croppoor = np.maximum(unit_gold, feats["d_sett1k_croppoor"])
    d_ab_croppoor = np.maximum(feats["d_hist_abandoned_village"], feats["d_sett1k_croppoor"])
    sig = {  # name: (dist, coverage, thr, factor)
        "sett1k": (feats["d_sett1k"], None, NEAR, "settlement_fabric"),
        "sett1k_pop500": (feats["d_sett1k_pop500"], None, NEAR, "settlement_fabric"),
        "sett1k_cropland_poor": (feats["d_sett1k_croppoor"], None, NEAR, "settlement_fabric"),
        "sett1k_pop500_cropland_poor": (feats["d_sett1k_pop500_croppoor"], None, NEAR, "settlement_fabric"),
        "sett1k_recent": (feats["d_sett1k_recent"], None, NEAR, "settlement_fabric"),
        "sett1k_recent_cropland_poor": (feats["d_sett1k_recent_croppoor"], None, NEAR, "settlement_fabric"),
        "sett1k_grew_cropland_poor": (feats["d_sett1k_grew_croppoor"], None, NEAR, "settlement_fabric"),
        "gold_contact_x_cropland_poor_sett": (d_gold_croppoor, cov_g, NEAR, "geology_x_settlement"),
        "gold_contact_x_pop500_cropland_poor": (d_gold_pop500poor, cov_g, NEAR, "geology_x_settlement"),
        "gold_unit_x_cropland_poor_sett": (d_unit_croppoor, cov_g, NEAR, "geology_x_settlement"),
        "hist_abandoned_village_x_cropland_poor_sett": (d_ab_croppoor, cov_h, NEAR, "historic_x_settlement"),
        "gold_contact_w2": (d_gold, cov_g, NEAR, "geology"),
        "gold_unit_w2": (unit_gold, cov_g, 0.5, "geology"),
        "gold_unit_w1": (unit_gold1, cov_g, 0.5, "geology"),
        "river_osm": (feats["d_river_osm"], None, NEAR, "hydro"),
        "gold_contact_x_river": (d_gold_river, cov_g, NEAR, "geology_x_hydro"),
        "gold_contact_x_settlement": (d_gold_sett, cov_g, NEAR, "geology_x_settlement"),
        "gold_contact_x_pop500": (d_gold_pop500, cov_g, NEAR, "geology_x_settlement"),
        "gold_unit_x_settlement": (d_unit_sett, cov_g, NEAR, "geology_x_settlement"),
        "settlement": (feats["d_settlement"], None, NEAR, "settlement_fabric"),
        "settlement_grew_33pct": (feats["d_settlement_grew"], None, NEAR, "settlement_fabric"),
        "settlement_pop500": (feats["d_pop500"], None, NEAR, "settlement_fabric"),
        "settlement_recent_ghsl": (feats["d_settlement_recent"], None, NEAR, "settlement_fabric"),
        "osm_place": (feats["d_osm_place"], None, NEAR, "settlement_fabric"),
        "road_osm": (feats["d_road_osm"], None, 3.0, "access"),
        "hist_track_1930s": (feats["d_hist_track"], cov_h, 3.0, "historic"),
        "hist_settlement_1930s": (feats["d_hist_place"], cov_h, NEAR, "historic"),
        "hist_abandoned_village_1930s": (feats["d_hist_abandoned_village"], cov_h, NEAR, "historic"),
        "hist_abandoned_village_on_gold_contact": (d_ab_gold, cov_h, NEAR, "historic"),
        "hist_abandoned_track_1930s": (feats["d_hist_abandoned_track"], cov_h, 3.0, "historic"),
        "hist_abandoned_track_on_gold_contact": (d_abt_gold, cov_h, NEAR, "historic"),
        "hist_abandoned_any_on_gold_contact": (d_abany_gold, cov_h, NEAR, "historic"),
        "hist_water_point_1930s": (feats["d_hist_water"], cov_h, NEAR, "historic"),
        "hist_water_point_on_gold_contact": (d_water_gold, cov_h, NEAR, "historic"),
        "hist_hill_terrain_1930s": (feats["d_hist_hill"], cov_h, NEAR, "historic"),
        "hist_mine_note_1930s": (feats["d_hist_mine_note"], cov_h, 10.0, "historic"),
    }

    return sig


def main():
    f = np.load(F, allow_pickle=True)
    grid, gk, iso, in_aoi, lat0 = f["grid"], f["gk"], f["iso"], f["in_aoi"], float(f["lat0"])
    feats = {k: f[k] for k in f.files if k not in ("grid", "gk", "iso", "in_aoi", "lat0")}
    ncell = len(gk)
    log(f"{ncell} cells, {in_aoi.sum()} in XSA")

    # ---- truth
    tp, tsrc = load_truth(lat0)
    tk = proj(tp, lat0)
    cl = cluster(tk)
    ck = np.array([c["km"] for c in cl])
    _, cidx = cKDTree(gk).query(ck)               # cell under each cluster
    c_in_aoi = in_aoi[cidx]
    d_to_aoi = cKDTree(gk[in_aoi]).query(ck)[0]
    train_c = ~c_in_aoi & (d_to_aoi > MOAT_KM)
    log(f"{len(tp)} truth points -> {len(cl)} clusters; XSA {c_in_aoi.sum()}, train {train_c.sum()}")
    src_by_cluster = [sorted({tsrc[m] for m in c["members"]}) for c in cl]

    # ---- reach (with ACLED events)
    log("reach with ACLED...")
    base_reach = feats["reach_w"]
    ak = proj(acled_reach_points(), lat0)
    reach = base_reach + kde(gk, ak)
    train_mask = ~in_aoi & (cKDTree(gk[in_aoi]).query(gk)[0] > MOAT_KM)
    xsa_mask = in_aoi

    sig = build_signals(feats)

    def truth_d(cd, cov, which):
        return dict(d=cd[cidx], ok=which, cov=(cov[cidx] if cov is not None else np.ones(len(cidx), bool)))

    def score_all(train_clusters, cell_mask, label):
        res = {}
        for name, (cd, cov, thr, fac) in sig.items():
            r = score_signal(cd, truth_d(cd, cov, train_clusters), cell_mask, thr, cov, reach); r["factor"] = fac
            res[name] = r
        names = [k for k, v in res.items() if "p_reach" in v]
        q = bh([res[k]["p_reach"] for k in names])
        for k, qq in zip(names, q):
            res[k]["q_bh_reach"] = round(float(qq), 4)
            res[k]["passes"] = bool(qq < 0.05 and (res[k]["lift_reach"] or 0) > 1)
        log(f"[{label}] passing: {[k for k in names if res[k]['passes']]}")
        return res

    # cluster country + a country-BALANCED train set: CAF holds 914 IPIS
    # field-visited sites, SDN/SSD a few hundred OSM tags - an unweighted
    # selection is a CAR model wearing a regional name.
    iso_c = iso[cidx]
    results = {"train": score_all(train_c, train_mask, "train-all"),
               "xsa_insample": score_all(c_in_aoi, xsa_mask, "xsa-insample")}
    per_iso = {i: int((train_c & (iso_c == i)).sum()) for i in ("CAF", "SSD", "SDN")}
    nmin = min(v for v in per_iso.values() if v > 0)
    bal = np.zeros(len(cl), bool)
    for i in ("CAF", "SSD", "SDN"):
        pool = np.nonzero(train_c & (iso_c == i))[0]
        if len(pool): bal[RNG.choice(pool, size=min(len(pool), nmin), replace=False)] = True
    results["train_balanced"] = score_all(bal, train_mask, "train-balanced")
    log(f"train clusters per country {per_iso}; balanced n={int(bal.sum())}")
    # leave-one-country-out: signals selected on two countries, scored on the third
    loco = {}
    for held in ("CAF", "SSD", "SDN"):
        tr = train_c & (iso_c != held); te = train_c & (iso_c == held)
        if te.sum() < 8: continue
        r = score_all(tr, train_mask & (iso != held), f"loco-{held}")
        loco[held] = dict(n_test=int(te.sum()), signals=[k for k, v in r.items() if v.get("passes")], results=r)
    results["loco"] = loco

    # ---- composites (rank within a mask, factor-grouped)
    def rank_pct(cd, mask, cov):
        """closeness rank in [0,1] among mask cells; NaN outside coverage"""
        r = np.full(ncell, np.nan); m = mask & (cov if cov is not None else True)
        v = -cd[m]; r[m] = np.argsort(np.argsort(v)) / max(1, m.sum() - 1)
        return r

    def composite(passing, mask, weights=None):
        if not passing:
            return np.full(ncell, np.nan), []
        by_fac = {}
        for k in passing:
            cd, cov, thr, fac = sig[k]
            by_fac.setdefault(fac, []).append(rank_pct(cd, mask, cov))
        facs = []
        for fac, v in by_fac.items():
            A = np.vstack(v)
            if weights is None:
                facs.append(np.nanmean(A, axis=0))
            else:
                ws = np.array([weights[k] for k in passing if sig[k][3] == fac])[:, None]
                ok = np.isfinite(A)
                facs.append(np.where(ok.any(0), np.nansum(np.nan_to_num(A) * ws, axis=0) / np.maximum((ok * ws).sum(0), 1e-9), np.nan))
        return np.nanmean(np.vstack(facs), axis=0), sorted(by_fac)

    def evaluate(comp, mask, tidx, label):
        """capture at top 5/10/20 within mask + reach-null permutation p for
        top05 + bootstrap 90% CI over clusters"""
        v = capture_at(comp, mask, tidx)
        if v.get("top05") is None: return v
        cells = np.nonzero(mask)[0]; w = reach[cells] / reach[cells].sum()
        c = comp[cells]; ok = np.isfinite(c); rk = np.full(len(c), np.nan); rk[ok] = np.argsort(np.argsort(-c[ok])) / ok.sum()
        n = len(tidx)
        sims = np.nanmean(rk[RNG.choice(len(cells), size=(PERMS, n), p=w)] < 0.05, axis=1)
        v["top05_p_reach"] = round(float(np.mean(sims >= v["top05"])), 4)
        v["top05_lift"] = round(v["top05"] / 0.05, 2)
        # bootstrap over truth clusters
        full = np.full(ncell, np.nan); full[cells] = rk; r = full[tidx]; r = r[np.isfinite(r)]
        bs = np.mean(r[RNG.integers(0, len(r), size=(2000, len(r)))] < 0.05, axis=1)
        v["top05_ci90"] = [round(float(np.quantile(bs, .05)), 3), round(float(np.quantile(bs, .95)), 3)]
        v["n_clusters"] = int(n)
        log(f"{label}: {v}")
        return v

    def best_per_factor(res, passing):
        best = {}
        for k in passing:
            fac = sig[k][3]
            if fac not in best or res[k]["lift_reach"] > res[best[fac]]["lift_reach"]: best[fac] = k
        return sorted(best.values())

    xsa_cidx = cidx[c_in_aoi]
    variants = {}
    def add(name, passing, res=None, weights=None, note=""):
        comp, facs = composite(passing, xsa_mask, weights)
        v = dict(signals=passing, factors=facs, note=note, xsa=evaluate(comp, xsa_mask, xsa_cidx, name))
        if weights: v["weights"] = {k: round(w, 3) for k, w in weights.items()}
        # the same composite, scored on the TRAIN clusters (in-sample for the
        # region variants, out-of-sample for A) - the reader sees both
        comp_t, _ = composite(passing, train_mask, weights)
        v["train"] = capture_at(comp_t, train_mask, cidx[train_c])
        variants[name] = v
        return comp

    passA = [k for k, v in results["xsa_insample"].items() if v.get("passes")]
    add("A_xsa_insample_selection", passA, note="signals chosen on the 46 XSA clusters themselves (the shipped 16% protocol, rebuilt on region-wide inputs)")
    passB = [k for k, v in results["train"].items() if v.get("passes")]
    add("B_region_equal_factors", passB, note="signals chosen on the 559 non-XSA clusters (50 km moat), equal factor vote")
    add("C_region_loglift_weights", passB, weights={k: float(np.log(results["train"][k]["lift_reach"])) for k in passB}, note="as B, signals weighted by log lift_reach within factor")
    add("D_region_best_per_factor", best_per_factor(results["train"], passB), note="as B, one signal per factor (highest lift_reach)")
    passF = [k for k, v in results["train_balanced"].items() if v.get("passes")]
    add("F_balanced_equal_factors", passF, note=f"signals chosen on a country-balanced train set ({nmin} clusters per country)")
    add("G_balanced_best_per_factor", best_per_factor(results["train_balanced"], passF), note="as F, one signal per factor")
    # H: signals that pass in EVERY leave-one-country-out fold - the
    # transportable core
    core = None
    for held, l in loco.items():
        core = set(l["signals"]) if core is None else core & set(l["signals"])
    add("H_loco_core", sorted(core or []), note="signals passing in every leave-one-country-out selection")

    # I: the transportable core (H) plus whichever settlement-fabric /
    # conjunction signals pass region-wide - "geology and 1930s sheets first,
    # the modern fabric on top"
    fab = [k for k in passB if sig[k][3] in ("settlement_fabric", "geology_x_settlement", "historic_x_settlement")]
    add("I_core_plus_fabric", sorted(set(core or []) | set(fab)), note="LOCO core + region-passing settlement-fabric and conjunction signals")
    add("J_core_plus_fabric_best", best_per_factor(results["train"], sorted(set(core or []) | set(fab))), note="as I, one signal per factor")

    # E. logistic regression on TRAIN cells (reach-weighted negatives)
    try:
        from sklearn.linear_model import LogisticRegression
        names = [k for k in sig]
        X = np.column_stack([np.minimum(sig[k][0], 100.0) / 100.0 for k in names])
        cov_all = np.column_stack([(sig[k][1] if sig[k][1] is not None else np.ones(ncell, bool)) for k in names]).astype(float)
        X = np.column_stack([X, cov_all])
        y = np.zeros(ncell); pos = cidx[train_c]; y[pos] = 1
        neg_pool = np.nonzero(train_mask)[0]
        neg = RNG.choice(neg_pool, size=min(len(neg_pool), 20 * len(pos)), replace=False, p=reach[neg_pool] / reach[neg_pool].sum())
        idx = np.r_[pos, neg]
        clf = LogisticRegression(C=0.3, max_iter=2000).fit(X[idx], y[idx])
        compE = clf.decision_function(X)
        variants["E_logreg_region_reachneg"] = dict(signals=names, coef={k: round(float(c), 3) for k, c in zip(names + [n + "_cov" for n in names], clf.coef_[0])},
                                                   note="fitted weights, reach-weighted negatives - the thing the shipped model refused to do", xsa=evaluate(compE, xsa_mask, xsa_cidx, "E_logreg"),
                                                   train=capture_at(compE, train_mask, cidx[train_c]))
    except Exception as e:
        variants["E_logreg_region_reachneg"] = dict(error=str(e))

    out = dict(generated_by="scripts/regional_mining/harness.py", protocol=__doc__.strip(),
               n_truth_points=int(len(tp)), n_clusters=len(cl), n_train_clusters=int(train_c.sum()), n_xsa_clusters=int(c_in_aoi.sum()), train_clusters_per_country=per_iso,
               truth_sources=dict(zip(*np.unique(tsrc, return_counts=True))) and {k: int(v) for k, v in zip(*np.unique(tsrc, return_counts=True))},
               cells=dict(total=ncell, train=int(train_mask.sum()), xsa=int(xsa_mask.sum())),
               signals=results, variants=variants)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    log(f"wrote {OUT}")


if __name__ == "__main__":
    main()
