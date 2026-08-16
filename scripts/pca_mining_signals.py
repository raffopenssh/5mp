#!/usr/bin/env python3
"""PCA of the XSA mining-prediction signal space — exploratory, read-only.

Question: how do the 18 per-cell signal distances co-vary, and where do the
truth clusters sit in that space? This informs (a) whether the equal-weight
composite is double-counting one underlying factor several times, and
(b) how to grade a finer-scale output.

Reuses the loaders from predict_mining_xsa.py; writes
data/eval/xsa_mining/pca.png + pca_loadings.json. No pipeline output changes.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import predict_mining_xsa as P  # noqa: E402

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = P.ROOT / "data/eval/xsa_mining"


def build():
    poly = P.load_aoi()
    lat0 = poly.centroid.y
    grid = P.make_grid(poly)
    gk = P.proj(grid, lat0)
    anchors = P.load_anchors(poly)
    clusters = P.cluster_sites(anchors, lat0)
    ck = np.array([c["km"] for c in clusters])

    P.log("signals...")
    gold = P.gold_contact_geoms(poly)
    d_gold = P.nearest_dist_km(gk, gold, lat0)
    hist = P.hist_features(poly)
    lab_pts = P.proj([[g.x, g.y] for g, _ in hist["labels_all"]], lat0)
    d_anylabel = P.nearest_dist_km_points(gk, lab_pts)
    coverage = d_anylabel <= 15.0
    hill_pts = P.proj([[g.x, g.y] for g, _ in hist["hills"]], lat0)
    d_hill = P.nearest_dist_km_points(gk, hill_pts)
    hp_pts = P.proj([[g.x, g.y] for g, _ in hist["places"]], lat0)
    d_hplace = P.nearest_dist_km_points(gk, hp_pts)
    d_track = (P.nearest_dist_km(gk, hist["tracks"], lat0)
               if hist["tracks"] else np.full(len(gk), 1e9))
    setts, defor, rivers, roads = P.modern_context(poly)
    sett_pts = P.proj([[s[1], s[0]] for s in setts], lat0)
    d_sett = P.nearest_dist_km_points(gk, sett_pts)
    riv_pts = P.proj([[r[1], r[0]] for r in rivers], lat0)
    d_riv = P.nearest_dist_km_points(gk, riv_pts)
    def_pts = P.proj([[d[1], d[0]] for d in defor], lat0)
    d_def = P.nearest_dist_km_points(gk, def_pts)
    croppoor = [s for s in setts if s[4] is not None and s[4] < 0.02]
    cp = P.proj([[s[1], s[0]] for s in croppoor], lat0)
    d_croppoor = P.nearest_dist_km_points(gk, cp)
    pop_ok = [s for s in setts if s[8] is not None and s[9] and s[8] >= 500]
    d_labour = P.nearest_dist_km_points(
        gk, P.proj([[s[1], s[0]] for s in pop_ok], lat0))
    pop_nofarm = [s for s in pop_ok if s[4] is not None and s[4] < 0.02]
    d_pop_nofarm = P.nearest_dist_km_points(
        gk, P.proj([[s[1], s[0]] for s in pop_nofarm], lat0))
    # NEW since last run: persistence + conversion (migrations 057/059)
    recent = [s for s in setts]  # placeholder replaced below by SQL pull
    c = P.db()
    rec_rows = c.execute(
        "SELECT lat, lon FROM park_settlements WHERE park_id=? AND "
        "polygon_ids<>'' AND persistence='recent'", (P.AOI,)).fetchall()
    d_recent = P.nearest_dist_km_points(
        gk, P.proj([[r[1], r[0]] for r in rec_rows], lat0))
    rnf_rows = c.execute(
        "SELECT lat, lon FROM park_settlements WHERE park_id=? AND "
        "polygon_ids<>'' AND persistence='recent' AND "
        "cropland_frac_2019 IS NOT NULL AND cropland_frac_2019<0.02",
        (P.AOI,)).fetchall()
    d_recent_nofarm = P.nearest_dist_km_points(
        gk, P.proj([[r[1], r[0]] for r in rnf_rows], lat0))
    conv_rows = c.execute(
        "SELECT lat, lon FROM deforestation_events WHERE park_id=? AND "
        "needs_review=0 AND cropland_conversion_frac IS NOT NULL AND "
        "cropland_conversion_frac<0.1", (P.AOI,)).fetchall()
    d_def_noconv = P.nearest_dist_km_points(
        gk, P.proj([[r[1], r[0]] for r in conv_rows], lat0))

    feats = {
        "gold_contact": d_gold,
        "river_o3": d_riv,
        "settlement": d_sett,
        "sett_croppoor": d_croppoor,
        "sett_pop500": d_labour,
        "sett_pop500_nofarm": d_pop_nofarm,
        "sett_recent": d_recent,
        "sett_recent_nofarm": d_recent_nofarm,
        "defor": d_def,
        "defor_not_cropland": d_def_noconv,
        "hist_track": d_track,
        "hist_place": d_hplace,
        "hist_hill": d_hill,
    }
    return grid, gk, ck, coverage, feats, clusters


def pca_panel(ax, X, names, tmask, title):
    # standardize
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1
    Z = (X - mu) / sd
    C = np.cov(Z.T)
    w, V = np.linalg.eigh(C)
    o = np.argsort(w)[::-1]
    w, V = w[o], V[:, o]
    pc = Z @ V[:, :2]
    ax.scatter(pc[~tmask, 0], pc[~tmask, 1], s=2, c="#bbb", alpha=0.3,
               rasterized=True, label="cells")
    ax.scatter(pc[tmask, 0], pc[tmask, 1], s=28, c="crimson",
               edgecolors="k", linewidths=0.4, label="truth cells", zorder=5)
    sc = 3.2
    for i, n in enumerate(names):
        ax.annotate("", xy=(V[i, 0] * sc * w[0]**.5, V[i, 1] * sc * w[1]**.5),
                    xytext=(0, 0),
                    arrowprops=dict(arrowstyle="->", color="steelblue", lw=1))
        ax.text(V[i, 0] * sc * w[0]**.5 * 1.08, V[i, 1] * sc * w[1]**.5 * 1.08,
                n, fontsize=7, color="steelblue")
    ev = w / w.sum()
    ax.set_xlabel(f"PC1 ({ev[0]:.0%})"); ax.set_ylabel(f"PC2 ({ev[1]:.0%})")
    ax.set_title(title, fontsize=10)
    ax.legend(fontsize=7, loc="lower right")
    return dict(explained=ev[:4].round(3).tolist(),
                loadings={n: V[i, :3].round(3).tolist()
                          for i, n in enumerate(names)})


def main():
    grid, gk, ck, coverage, feats, clusters = build()
    from scipy.spatial import cKDTree
    _, tidx = cKDTree(gk).query(ck)
    tmask = np.zeros(len(gk), bool)
    tmask[tidx] = True

    # transform: proximity = exp(-d / 10 km), so "far" saturates instead of
    # letting a 300 km distance dominate the variance.
    def T(d):
        return np.exp(-np.minimum(d, 1e6) / 10.0)

    modern = [k for k in feats if not k.startswith("hist_")]
    allk = list(feats)
    Xm = np.column_stack([T(feats[k]) for k in modern])
    Xa = np.column_stack([T(feats[k]) for k in allk])[coverage]
    tm_cov = tmask[coverage]

    fig, axes = plt.subplots(1, 3, figsize=(17, 5.5))
    out = {}
    out["modern_all_cells"] = pca_panel(
        axes[0], Xm, modern, tmask,
        f"Modern signals, all {len(gk)} cells")
    out["all_signals_covered_cells"] = pca_panel(
        axes[1], Xa, allk, tm_cov,
        f"All signals, {int(coverage.sum())} 1930s-covered cells")
    # geographic view of PC1 (modern)
    mu, sd = Xm.mean(0), Xm.std(0); sd[sd == 0] = 1
    Z = (Xm - mu) / sd
    w, V = np.linalg.eigh(np.cov(Z.T))
    o = np.argsort(w)[::-1]
    pc1 = Z @ V[:, o[0]]
    s = axes[2].scatter(grid[:, 0], grid[:, 1], c=pc1, s=4, cmap="viridis")
    axes[2].scatter(grid[tmask, 0], grid[tmask, 1], s=30, facecolors="none",
                    edgecolors="crimson", linewidths=1)
    axes[2].set_title("PC1 (modern) on the map; truth circled", fontsize=10)
    axes[2].set_aspect(1 / np.cos(np.radians(grid[:, 1].mean())))
    plt.colorbar(s, ax=axes[2], shrink=0.8)
    fig.tight_layout()
    fig.savefig(OUT / "pca.png", dpi=140)
    json.dump(out, open(OUT / "pca_loadings.json", "w"), indent=1)
    print("wrote", OUT / "pca.png")
    for panel, rec in out.items():
        print(panel, "explained:", rec["explained"])


if __name__ == "__main__":
    main()
