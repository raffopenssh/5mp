#!/usr/bin/env python3
"""Pre-registered model selection for the regional mining-context composite.

    python3 scripts/regional_mining/nested_cv.py   # -> data/eval/regional_mining/nested_cv.json

harness.py showed that several composites can be built from the same
region-trained signal set and that they differ on XSA (46 clusters) by more
than their bootstrap CIs are narrow. Picking the best of them BY its XSA
score would be tuning on the test set - the thing the old model refused to
do. So the choice is made here, on the training region only:

  * spatial 5-fold CV over 2 deg blocks of the non-XSA region (50 km moat);
  * in each fold, signals are selected (reach null, BH q<.05, lift_reach>1)
    on the 4 training folds and each composite RECIPE is scored on the held
    -out fold by top-5/10/20 % capture, ranking within that fold's cells;
  * the recipe with the best mean held-out top-10 % capture (top-5 % is too
    granular for ~100 clusters/fold) is the pre-registered choice, fitted
    once on the whole training region and applied ONCE to XSA.

Recipes (all use the same signal library, build_signals in harness.py):
  equal_factors      mean of factor means (each factor = mean rank of its
                     passing signals)                    -- the shipped design
  best_per_factor    one signal per factor (highest train lift_reach)
  loglift_weights    signals weighted by log(lift_reach) within factor
  core_x_fabric      geology+historic factors only
  top3_signals       three highest-lift signals, equal weight, no factors
  noisy_or           1 - prod(1 - rank) over best-per-factor  (any-of)
"""
import json, sys, argparse
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/eval/regional_mining/nested_cv.json"
RNG = np.random.default_rng(7)
BLOCK = 2.0
PERMS_CV = 600


from scipy.stats import rankdata


def rank_pct(cd, mask, cov, ncell):
    """closeness rank in [0,1]; ties share a rank (a conjunction distance is
    1e9 on 90% of cells - argsort would hand those arbitrary ranks = noise)"""
    r = np.full(ncell, np.nan); m = mask & (cov if cov is not None else True)
    if m.sum() == 0: return r
    r[m] = 1 - (rankdata(cd[m], method="average") - 1) / max(1, m.sum() - 1)
    return r


def evidence(sig, keys, ncell):
    """binary evidence layers: +1 inside thr, -1 outside, 0 where unmapped"""
    E = np.zeros((len(keys), ncell), dtype=np.int8)
    for i, k in enumerate(keys):
        cd, cov, thr, fac = sig[k]
        b = cd <= thr
        E[i] = np.where(b, 1, -1)
        if cov is not None: E[i][~cov] = 0
    return E


def wofe(E, pos_cells, bg_mask, reach):
    """Weights of Evidence (Bonham-Carter 1994) with a reach-weighted
    background: W+ = ln P(B|D)/P(B|~D), W- likewise for ~B; posterior
    log-odds = sum of the weight the cell's evidence earns (0 if unmapped).
    ~D is the reach-weighted cell population so 'near a reported mine' is
    measured against 'where reports come from', as every other number here."""
    D = np.zeros(E.shape[1], bool); D[pos_cells] = True
    w = reach * bg_mask; w = w / w.sum()
    Wp = np.zeros(E.shape[0]); Wm = np.zeros(E.shape[0])
    for i in range(E.shape[0]):
        e = E[i]
        pbd = (np.sum(e[D] == 1) + 0.5) / (np.sum(e[D] != 0) + 1)
        pbn = (np.sum(w[(e == 1) & ~D]) + 1e-6) / (np.sum(w[(e != 0) & ~D]) + 2e-6)
        Wp[i] = np.log(pbd / pbn); Wm[i] = np.log((1 - pbd) / (1 - pbn))
    logit = np.zeros(E.shape[1])
    for i in range(E.shape[0]):
        logit += np.where(E[i] == 1, Wp[i], np.where(E[i] == -1, Wm[i], 0.0))
    return logit, Wp, Wm


def logit_binary(E, pos_cells, bg_mask, reach, l2=1.0, iters=300, sw=None):
    """L2 logistic regression on the evidence layers (the standard fix for
    WofE's conditional-independence assumption; Agterberg 1989). Negatives
    are all background cells, weighted by reach; positives weighted to match
    total negative mass. Newton/IRLS in numpy (sklearn is broken here)."""
    X = np.c_[np.ones(E.shape[1]), E.T.astype(float)]
    y = np.zeros(E.shape[1]); y[pos_cells] = 1
    wt = np.where(y == 1, 1.0, reach * bg_mask)
    if sw is not None: wt = wt * sw                        # environmental-analogue weights
    wt[y == 0] /= wt[y == 0].sum(); wt[y == 1] /= wt[y == 1].sum()
    beta = np.zeros(X.shape[1]); R = l2 * np.eye(X.shape[1]); R[0, 0] = 0
    use = (y == 1) | bg_mask
    Xu, yu, wu = X[use], y[use], wt[use]
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Xu @ beta))
        g = Xu.T @ (wu * (p - yu)) + R @ beta
        Hm = (Xu * (wu * p * (1 - p))[:, None]).T @ Xu + R
        step = np.linalg.solve(Hm, g); beta -= step
        if np.abs(step).max() < 1e-6: break
    return X @ beta, beta


def select(sig, cidx, train_c, mask, reach):
    res = {}
    for name, (cd, cov, thr, fac) in sig.items():
        td = dict(d=cd[cidx], ok=train_c, cov=(cov[cidx] if cov is not None else np.ones(len(cidx), bool)))
        r = H.score_signal(cd, td, mask, thr, cov, reach, perms=PERMS_CV); r["factor"] = fac; res[name] = r
    names = [k for k, v in res.items() if "p_reach" in v]
    q = H.bh([res[k]["p_reach"] for k in names])
    passing = [k for k, qq in zip(names, q) if qq < 0.05 and (res[k]["lift_reach"] or 0) > 1]
    return res, passing


def recipes(sig, res, passing, mask, ncell, pos_cells=None, bg_mask=None, reach=None):
    def fac_means(keys, weights=None):
        by = {}
        for k in keys: by.setdefault(sig[k][3], []).append(k)
        out = []
        for fac, ks in by.items():
            A = np.vstack([rank_pct(sig[k][0], mask, sig[k][1], ncell) for k in ks])
            if weights is None: out.append(np.nanmean(A, 0))
            else:
                w = np.array([weights[k] for k in ks])[:, None]; ok = np.isfinite(A)
                out.append(np.where(ok.any(0), np.nansum(np.nan_to_num(A) * w, 0) / np.maximum((ok * w).sum(0), 1e-9), np.nan))
        return np.vstack(out) if out else None
    def best_per_factor(keys):
        b = {}
        for k in keys:
            f = sig[k][3]
            if f not in b or res[k]["lift_reach"] > res[b[f]]["lift_reach"]: b[f] = k
        return sorted(b.values())
    out = {}
    if not passing: return {r: np.full(ncell, np.nan) for r in ("equal_factors", "best_per_factor", "loglift_weights", "core_x_fabric", "top3_signals", "noisy_or", "wofe", "logit_binary")}
    F = fac_means(passing); out["equal_factors"] = np.nanmean(F, 0)
    bpf = best_per_factor(passing); out["best_per_factor"] = np.nanmean(fac_means(bpf), 0)
    out["loglift_weights"] = np.nanmean(fac_means(passing, {k: float(np.log(res[k]["lift_reach"])) for k in passing}), 0)
    core = [k for k in passing if sig[k][3] in ("geology", "historic")]
    out["core_x_fabric"] = np.nanmean(fac_means(core), 0) if core else np.full(ncell, np.nan)
    top3 = sorted(passing, key=lambda k: -res[k]["lift_reach"])[:3]
    out["top3_signals"] = np.nanmean(np.vstack([rank_pct(sig[k][0], mask, sig[k][1], ncell) for k in top3]), 0)
    A = fac_means(bpf); out["noisy_or"] = 1 - np.nanprod(1 - np.nan_to_num(A, nan=0.0), 0)
    E = evidence(sig, passing, ncell)
    out["wofe"], Wp, Wm = wofe(E, pos_cells, bg_mask, reach)
    out["logit_binary"], beta = logit_binary(E, pos_cells, bg_mask, reach)
    out["_fit"] = dict(wofe={k: [round(float(a), 3), round(float(b), 3)] for k, a, b in zip(passing, Wp, Wm)},
                       logit={k: round(float(b), 3) for k, b in zip(["intercept"] + passing, beta)})
    return out


L2_GRID = (0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0)
ENV_KEYS = ("lc_tree", "lc_shrub", "lc_grass", "lc_crop", "lc_bare")


def analogue_weights(feats, in_aoi, train_mask, temper=1.0):
    """Environmental-analogue (covariate-shift) weights for the training
    region: w(x) = p_XSA(x) / p_train(x), estimated by a logistic classifier
    XSA-cells vs training-cells on WorldCover land-cover composition
    (Sugiyama et al. 2007; Elith et al. 2010 for the ecological-transfer
    framing). Nubian-shield desert cells (bare>>tree) have ~no analogue in
    XSA and receive ~0 weight; Guinea-Sudan savanna woodland receives ~1.
    Weights are tempered (w**temper) and clipped at the 99th percentile so
    no single block dominates; `temper` is tuned by inner CV."""
    X = np.c_[[np.nan_to_num(feats[k], nan=0.0) for k in ENV_KEYS]].T
    X = np.c_[X, X ** 2]                                  # mild non-linearity
    mu, sd = X[train_mask | in_aoi].mean(0), X[train_mask | in_aoi].std(0) + 1e-9
    X = (X - mu) / sd
    sel = train_mask | in_aoi; y = in_aoi[sel].astype(float); Xs = np.c_[np.ones(sel.sum()), X[sel]]
    wt = np.where(y == 1, 0.5 / y.sum(), 0.5 / (len(y) - y.sum()))   # class-balanced
    beta = np.zeros(Xs.shape[1]); R = 1e-3 * np.eye(len(beta)); R[0, 0] = 0
    for _ in range(100):
        pr = 1 / (1 + np.exp(-Xs @ beta)); g = Xs.T @ (wt * (pr - y)) + R @ beta
        Hm = (Xs * (wt * pr * (1 - pr))[:, None]).T @ Xs + R
        step = np.linalg.solve(Hm, g); beta -= step
        if np.abs(step).max() < 1e-7: break
    logit = np.c_[np.ones(len(X)), X] @ beta
    w = np.exp(logit)                                     # balanced odds = density ratio
    w = w ** temper
    cap = np.quantile(w[train_mask], 0.99); w = np.minimum(w, cap)
    w = w / w[train_mask].mean()
    auc = None
    try:
        from scipy.stats import rankdata
        rk = rankdata(logit[sel]); n1 = y.sum(); auc = float((rk[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * (len(y) - n1)))
    except Exception: pass
    return w, dict(coef={k: round(float(b), 3) for k, b in zip(["intercept"] + list(ENV_KEYS) + [k + "^2" for k in ENV_KEYS], beta)}, separability_auc=auc)


TEMPER_GRID = (0.0, 0.5, 1.0)


def tune_logit(sig, all_keys, passing, cidx, pos_c, bg_mask, fold_cell, fold_cl, inner_folds, reach, ncell, aw=None):
    """inner CV over (regularisation strength, feature set) for the binary
    logistic recipe. Feature sets: `passing` (reach-null-selected signals)
    and `all` (every signal in the library; the penalty does the selecting).
    Score = cluster-weighted mean held-out top-10 % capture over inner folds."""
    best = None; table = {}
    for fs_name, keys in (("passing", passing), ("all", all_keys)):
        if not keys: continue
        E = evidence(sig, keys, ncell)
        for l2 in L2_GRID:
          for tp in (TEMPER_GRID if aw is not None else (0.0,)):
            sw = None if tp == 0.0 else aw ** tp
            vals, ws = [], []
            for k in inner_folds:
                tr_c = pos_c & (fold_cl != k); te_c = pos_c & (fold_cl == k)
                tr_m = bg_mask & (fold_cell != k); te_m = bg_mask & (fold_cell == k)
                if te_c.sum() < 5: continue
                comp, _ = logit_binary(E, cidx[tr_c], tr_m, reach, l2=l2, sw=sw)
                # held-out fold scored with the same analogue weights: the
                # target is XSA-like ground, so XSA-like held-out clusters count more
                cap = H.capture_at(comp, te_m, cidx[te_c])
                v = cap["top10"]
                if v is not None: vals.append(v); ws.append(float((aw[cidx[te_c]] if sw is not None else np.ones(te_c.sum())).sum()))
            m = float(np.average(vals, weights=ws)) if vals else 0.0
            table[f"{fs_name}|l2={l2}|temper={tp}"] = round(m, 3)
            if best is None or m > best[0]: best = (m, fs_name, l2, tp)
    return best, table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--belt", action="store_true", help="savanna belt only: train cells/clusters south of 16.3N. "
                    "GHSL tiles do not cover east of 31.3E north of 16.3N (126 of 559 train clusters sit in that hole and "
                    "read as 'no settlement'), and the Nubian-shield desert gold regime is a poor analogue for XSA.")
    args = ap.parse_args()
    global OUT
    if args.belt: OUT = OUT.with_name("nested_cv_belt.json")
    f = np.load(H.F, allow_pickle=True)
    grid, gk, iso, in_aoi, lat0 = f["grid"], f["gk"], f["iso"], f["in_aoi"], float(f["lat0"])
    feats = {k: f[k] for k in f.files if k not in ("grid", "gk", "iso", "in_aoi", "lat0")}
    ncell = len(gk)
    sig = H.build_signals(feats)
    tp, tsrc = H.load_truth(lat0); tk = H.proj(tp, lat0); cl = H.cluster(tk)
    ck = np.array([c["km"] for c in cl]); _, cidx = cKDTree(gk).query(ck)
    c_in_aoi = in_aoi[cidx]; d_to_aoi = cKDTree(gk[in_aoi]).query(ck)[0]
    train_c = ~c_in_aoi & (d_to_aoi > H.MOAT_KM)
    train_mask = ~in_aoi & (cKDTree(gk[in_aoi]).query(gk)[0] > H.MOAT_KM)
    if args.belt:
        train_mask &= grid[:, 1] < 16.3; train_c &= grid[cidx, 1] < 16.3
    reach = feats["reach_w"] + H.kde(gk, H.proj(H.acled_reach_points(), lat0))
    H.log(f"{train_c.sum()} train clusters, {c_in_aoi.sum()} XSA")
    aw, aw_info = analogue_weights(feats, in_aoi, train_mask)
    aw_cl = aw[cidx]
    H.log(f"analogue weights: env separability AUC={aw_info['separability_auc']:.3f}; "
          f"train-cluster weight by country: " + ", ".join(f"{i}={aw_cl[train_c & (iso[cidx]==i)].mean():.2f}" for i in ("CAF", "SSD", "SDN"))
          + f"; SDN<16.3N={aw_cl[train_c & (iso[cidx]=='SDN') & (grid[cidx,1]<16.3)].mean():.2f} SDN>=16.3N={aw_cl[train_c & (iso[cidx]=='SDN') & (grid[cidx,1]>=16.3)].mean():.2f}")

    # 2-deg blocks -> 5 folds (blocks shuffled, assigned round-robin so folds have similar cluster counts)
    blk_cell = (np.floor(grid[:, 0] / BLOCK).astype(int) * 1000 + np.floor(grid[:, 1] / BLOCK).astype(int))
    blk_cl = blk_cell[cidx]
    blocks = np.unique(blk_cl[train_c]); RNG.shuffle(blocks)
    counts = {b: int((blk_cl[train_c] == b).sum()) for b in blocks}
    order = sorted(blocks, key=lambda b: -counts[b]); fold_of = {}; load = [0] * 5
    for b in order:
        i = int(np.argmin(load)); fold_of[b] = i; load[i] += counts[b]
    fold_cell = np.array([fold_of.get(b, -1) for b in blk_cell]); fold_cl = fold_cell[cidx]
    H.log("fold cluster counts", load)

    names_r = ("equal_factors", "best_per_factor", "loglift_weights", "core_x_fabric", "top3_signals", "noisy_or", "wofe", "logit_binary", "logit_tuned")
    per_fold = []
    for k in range(5):
        tr_c = train_c & (fold_cl != k); te_c = train_c & (fold_cl == k)
        tr_m = train_mask & (fold_cell != k); te_m = train_mask & (fold_cell == k)
        res, passing = select(sig, cidx, tr_c, tr_m, reach)
        comps = recipes(sig, res, passing, te_m, ncell, pos_cells=cidx[tr_c], bg_mask=tr_m, reach=reach)
        # nested: tune (l2, feature set) on the 4 training folds only
        inner = [j for j in range(5) if j != k]
        (m_in, fs, l2, tp), table = tune_logit(sig, list(sig), passing, cidx, tr_c, tr_m, fold_cell, fold_cl, inner, reach, ncell, aw=aw)
        E = evidence(sig, passing if fs == "passing" else list(sig), ncell)
        comps["logit_tuned"], _ = logit_binary(E, cidx[tr_c], tr_m, reach, l2=l2, sw=None if tp == 0 else aw ** tp)
        row = dict(fold=k, n_test=int(te_c.sum()), n_train=int(tr_c.sum()), passing=passing, scores={},
                   tuned=dict(feature_set=fs, l2=l2, temper=tp, inner_top10=round(m_in, 3), inner_table=table))
        for r in names_r:
            row["scores"][r] = H.capture_at(comps[r], te_m, cidx[te_c])
        per_fold.append(row)
        H.log(f"fold {k}: n={row['n_test']} " + " ".join(f"{r}={row['scores'][r]['top10']}" for r in names_r))

    summary = {}
    for r in names_r:
        for m in ("top05", "top10", "top20"):
            vals = [pf["scores"][r][m] for pf in per_fold if pf["scores"][r][m] is not None]
            w = [pf["n_test"] for pf in per_fold if pf["scores"][r][m] is not None]
            summary.setdefault(r, {})[m] = round(float(np.average(vals, weights=w)), 3) if vals else None
    chosen = max(names_r, key=lambda r: (summary[r]["top10"] or 0, summary[r]["top05"] or 0))
    H.log("CV summary", summary, "chosen:", chosen)

    # fit once on all train, apply once to XSA
    res, passing = select(sig, cidx, train_c, train_mask, reach)
    xsa_mask = in_aoi; xcid = cidx[c_in_aoi]
    comps = recipes(sig, res, passing, xsa_mask, ncell, pos_cells=cidx[train_c], bg_mask=train_mask, reach=reach)
    (m_in, fs, l2, tp), table = tune_logit(sig, list(sig), passing, cidx, train_c, train_mask, fold_cell, fold_cl, list(range(5)), reach, ncell, aw=aw)
    keys_t = passing if fs == "passing" else list(sig)
    E = evidence(sig, keys_t, ncell)
    comps["logit_tuned"], beta_t = logit_binary(E, cidx[train_c], train_mask, reach, l2=l2, sw=None if tp == 0 else aw ** tp)
    comps["_fit"]["logit_tuned"] = dict(feature_set=fs, l2=l2, temper=tp, cv_top10=round(m_in, 3), cv_table=table,
                                        analogue_weights=aw_info,
                                        coef={k: round(float(b), 3) for k, b in zip(["intercept"] + keys_t, beta_t)})
    H.log(f"final tuned logit: {fs} l2={l2} temper={tp} cv_top10={m_in:.3f}")
    final = {}
    cells = np.nonzero(xsa_mask)[0]; w = reach[cells] / reach[cells].sum()
    for r in names_r:
        v = H.capture_at(comps[r], xsa_mask, xcid)
        c = comps[r][cells]; ok = np.isfinite(c)
        if ok.any() and v["top05"] is not None:
            rk = np.full(len(c), np.nan); rk[ok] = np.argsort(np.argsort(-c[ok])) / ok.sum()
            sims = np.nanmean(rk[RNG.choice(len(cells), size=(H.PERMS, len(xcid)), p=w)] < 0.05, axis=1)
            v["top05_p_reach"] = round(float(np.mean(sims >= v["top05"])), 4)
            sims10 = np.nanmean(rk[RNG.choice(len(cells), size=(H.PERMS, len(xcid)), p=w)] < 0.10, axis=1)
            v["top10_p_reach"] = round(float(np.mean(sims10 >= v["top10"])), 4)
            full = np.full(ncell, np.nan); full[cells] = rk; rr = full[xcid]; rr = rr[np.isfinite(rr)]
            bs = rr[RNG.integers(0, len(rr), size=(2000, len(rr)))]
            v["top05_ci90"] = [round(float(np.quantile((bs < .05).mean(1), q)), 3) for q in (.05, .95)]
            v["top10_ci90"] = [round(float(np.quantile((bs < .10).mean(1), q)), 3) for q in (.05, .95)]
        final[r] = v
    # robustness of the chosen recipe by truth source (a source-specific
    # artefact would show as one source carrying the capture) and by
    # cluster size (singletons vs multi-report sites)
    comp = comps[chosen]; c = comp[cells]; ok = np.isfinite(c)
    rk = np.full(len(c), np.nan); rk[ok] = np.argsort(np.argsort(-c[ok])) / ok.sum()
    full = np.full(ncell, np.nan); full[cells] = rk
    xcl = [cl[i] for i in np.nonzero(c_in_aoi)[0]]
    robust = {}
    for src in sorted({tsrc[m] for cc in xcl for m in cc["members"]}):
        idx = [cidx[i] for i, cc in zip(np.nonzero(c_in_aoi)[0], xcl) if src in {tsrc[m] for m in cc["members"]}]
        r = full[idx]; r = r[np.isfinite(r)]
        robust[src] = dict(n_clusters=len(idx), top05=round(float((r < .05).mean()), 3), top10=round(float((r < .10).mean()), 3), top20=round(float((r < .20).mean()), 3))
    for lab, sel in (("singleton", [len(cc["members"]) == 1 for cc in xcl]), ("multi_report", [len(cc["members"]) > 1 for cc in xcl])):
        idx = cidx[np.nonzero(c_in_aoi)[0][np.array(sel)]]
        r = full[idx]; r = r[np.isfinite(r)]
        robust[lab] = dict(n_clusters=int(len(idx)), top05=round(float((r < .05).mean()), 3), top10=round(float((r < .10).mean()), 3), top20=round(float((r < .20).mean()), 3))
    H.log("robustness", robust)
    np.savez_compressed(OUT.with_suffix(".xsa_scores.npz"), lon=grid[cells, 0], lat=grid[cells, 1], score=c, rank_pct=rk, recipe=chosen)
    H.log("XSA (all recipes, for the record; only `chosen` is the claim):", json.dumps(final, indent=0))

    out = dict(generated_by="scripts/regional_mining/nested_cv.py" + (" --belt" if args.belt else ""), protocol=__doc__.strip(),
               region="savanna belt (<16.3N)" if args.belt else "full CAF+SSD+SDN (NB: GHSL hole >31.3E,>16.3N)",
               n_train_clusters=int(train_c.sum()), n_xsa_clusters=int(c_in_aoi.sum()), block_deg=BLOCK, folds=per_fold,
               cv_summary=summary, chosen_recipe=chosen, final_signals=passing,
               final_signal_stats={k: {x: v.get(x) for x in ("n", "capture", "lift", "lift_reach", "p_reach", "factor")} for k, v in res.items()},
               fits=comps.get("_fit"), xsa=final, robustness_chosen=robust, xsa_claim=dict(recipe=chosen, **final[chosen]))
    OUT.write_text(json.dumps(out, indent=1, default=float))
    H.log("wrote", OUT)


if __name__ == "__main__":
    main()
