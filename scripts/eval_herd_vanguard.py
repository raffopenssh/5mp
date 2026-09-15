#!/usr/bin/env python3
"""Herd-model × vanguard/tier probe (2026-09-15; results in docs/agents/fire.md "Herd model × vanguard").

Re-run 2026-09-15 pm on the KF set: the vanguard population is now the Kalman seed-ahead chains
(data/fire_vanguard_kf/XSA_Study_Area.json, the same rows /api/fire-vanguard serves for the XSA); the plain
groups' `vanguard` flag is kept as a second column (`van_groups`) so the earlier numbers reproduce. Adds runs
F/G: a vanguard-ONLY fit (all KF chains / KF chains >= 150 km of the fit seasons) — the question the KF layer
was shipped to make answerable.

    python3 -W ignore scripts/eval_herd_vanguard.py     # ~15 min, prints the tables, writes /tmp/herdv/res.json

Does NOT touch data/plan_zones/solver/ — the report's movement model is unchanged; this measures what vanguard
and evidence tiers would add. Reproduces plan_solver.movement() bundling on the CURRENT groups file,
then asks: (1) do the fitted bands capture the held-out season's VANGUARD fronts (the only lines whose day order
is measured trustworthy)?  (2) does fitting on `supported` tiers help or hurt?  (3) does the bundle's direction
(from day order) agree with its vanguard chains' direction?  (4) timing relative to the season front per bundle.
Writes /tmp/herdv only."""
import json, math, pickle, sys, warnings; warnings.filterwarnings("ignore")
from collections import Counter
import numpy as np
from scipy import ndimage
from skimage.draw import line as skline
from pathlib import Path; ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "scripts")); OUTD = Path("/tmp/herdv"); OUTD.mkdir(exist_ok=True)
import plan_conservancy_units as P
import __main__; __main__.Grid = P.Grid
st = pickle.load(open(str(ROOT / "data/plan_zones/conservancy_units/state.pkl"), "rb")); G = st["G"]
raw = json.load(open(str(ROOT / "data/fire_groups_v5/XSA_Study_Area.json")))
def season_of(d): y, m = int(d[:4]), int(d[5:7]); return y if m >= 8 else y - 1
T = []
for g in raw:
    t = g.get("trajectory") or []
    if len(t) < 2: continue
    rr, cc = G.rc_arr([p[0] for p in t], [p[1] for p in t])
    if not G.inside(rr, cc).any(): continue
    if g.get("group_type") == "transhumance" and float(g.get("distance_km") or 0) >= 150:
        T.append(dict(t=t, s=season_of(g["start_date"]), tier=g.get("evidence_tier"), van=bool(g.get("vanguard")), lead=g.get("lead_start"), bits=g.get("evidence_bits"), fires=g["fire_count"], days=g["days"], src="groups", long=True, hd=None))
n_plain = len(T)
for g in json.load(open(str(ROOT / "data/fire_vanguard_kf/XSA_Study_Area.json"))):
    t = g.get("trajectory") or []
    if len(t) < 2: continue
    rr, cc = G.rc_arr([p[0] for p in t], [p[1] for p in t])
    if not G.inside(rr, cc).any(): continue
    T.append(dict(t=t, s=season_of(g["start_date"]), tier=g.get("evidence_tier"), van=True, lead=g.get("lead_start"), bits=g.get("evidence_bits"), fires=g["fire_count"], days=g["days"], src="kf", long=float(g.get("distance_km") or 0) >= 150, hd=g.get("heading_deg")))
print("long fronts (plain groups)", n_plain, Counter(x["s"] for x in T[:n_plain]))
print("KF vanguard chains", len(T) - n_plain, Counter((x["s"], x["long"]) for x in T[n_plain:]))
kx = 111 * math.cos(math.radians(G.aoi.centroid.y)); cell_km = G.res / 1000; sig_c = 4.0 / cell_km; HOLD = 2025; K = 12
X = np.array([[x["t"][0][0] * kx, x["t"][0][1] * 111, x["t"][-1][0] * kx, x["t"][-1][1] * 111] for x in T])
season = np.array([x["s"] for x in T]); tier = np.array([x["tier"] for x in T])
plain = np.array([x["src"] == "groups" for x in T]); kf = ~plain; kf_long = kf & np.array([x["long"] for x in T])
van_groups = plain & np.array([x["van"] for x in T])  # the earlier definition (plain chain flagged vanguard)
van = kf                                                # the vanguard population as the app now defines it
lead = np.array([np.nan if x["lead"] is None else x["lead"] for x in T])
alld_s = ndimage.gaussian_filter(G.alldens, sig_c)
def bridge_ud(idx, w=None):
    ud = np.zeros((G.h, G.w), np.float32)
    for n, j in enumerate(idx):
        t = T[j]["t"]; rr, cc = G.rc_arr([p[0] for p in t], [p[1] for p in t]); wj = (1.0 if w is None else w[n]) / max(len(t) - 1, 1)
        for i in range(len(t) - 1):
            lr, lc = skline(int(rr[i]), int(cc[i]), int(rr[i + 1]), int(cc[i + 1])); ok = G.inside(lr, lc)
            if ok.any(): ud[lr[ok], lc[ok]] += wj / max(ok.sum(), 1)
    return ndimage.gaussian_filter(ud, sig_c)
def isopleth(ud, q):
    flat = np.sort(ud.ravel())[::-1]; cs = np.cumsum(flat); thr = flat[np.searchsorted(cs, q * cs[-1])]; return ud >= max(thr, 1e-12)
def top_n(arr, n):
    v = np.where(G.mask, arr, -1).ravel(); idx = np.argpartition(-v, n)[:n]; m = np.zeros(v.shape, bool); m[idx] = True; return m.reshape(arr.shape)
def inside_share(j, band):
    t = T[j]["t"]; rr, cc = G.rc_arr([p[0] for p in t], [p[1] for p in t]); ok = G.inside(rr, cc); return float(band[rr[ok], cc[ok]].mean()) if ok.any() else 0.0
def capture(idx, band): return float(np.mean([inside_share(j, band) >= 0.5 for j in idx])) if len(idx) else float("nan")
def kmeans(fit):
    rng = np.random.default_rng(0); C = X[fit][rng.choice(fit.sum(), K, replace=False)]
    for _ in range(60):
        lab = np.argmin(((X[:, None, :] - C[None]) ** 2).sum(2), 1)
        C2 = np.array([X[fit & (lab == i)].mean(0) if (fit & (lab == i)).any() else C[i] for i in range(K)])
        if np.allclose(C2, C): break
        C = C2
    return np.argmin(((X[:, None, :] - C[None]) ** 2).sum(2), 1)
Q = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
def run(name, fit_sel, weights=None, lab=None, capture_min=0.5):
    """fit_sel: bool mask of fronts used to build UDs (subset of non-hold seasons). Bands chosen exactly as movement():
    smallest isopleth with hold-out capture (ALL held-out fronts of the bundle) >= capture_min."""
    fit = (season != HOLD) & fit_sel; hold = (season == HOLD) & plain; hold_kf = (season == HOLD) & kf
    if lab is None: lab = kmeans(fit)
    n_fit = int(fit.sum()); band_all = np.zeros((G.h, G.w), bool); rows = []
    for b in range(K):
        fi = np.flatnonzero(fit & (lab == b)); hi = np.flatnonzero(hold & (lab == b)); hv = np.flatnonzero(hold_kf & (lab == b)); hvl = np.flatnonzero(hold_kf & kf_long & (lab == b)); hg = np.flatnonzero(hold & (lab == b) & van_groups)
        if len(fi) < 0.03 * n_fit: continue
        ud = bridge_ud(fi, None if weights is None else weights[fi]); curve = []
        for q in Q:
            bq = isopleth(ud, q) & G.mask; n_ = int(bq.sum()); hit = capture(hi, bq); nul = capture(hi, top_n(alld_s, n_))
            curve.append(dict(q=q, km2=n_ * G.cell_km2(), hit=hit, nul=nul, skill=hit - max(nul, n_ / G.mask.sum()), band=bq))
        ok = [c for c in curve if c["hit"] >= capture_min]; best = min(ok, key=lambda c: c["km2"]) if ok else max(curve, key=lambda c: c["skill"])
        band = best["band"]; band_all |= band
        rows.append(dict(b=b, fit=len(fi), hold=len(hi), hold_van=len(hv), km2=best["km2"], q=best["q"], cap=best["hit"], nul=best["nul"], skill=best["skill"],
                         cap_van=capture(hv, band), nul_van=capture(hv, top_n(alld_s, int(band.sum()))), cap_van_long=capture(hvl, band), hold_van_long=len(hvl),
                         cap_groups_van=capture(hg, band), cap_nonvan=capture(np.flatnonzero(hold & (lab == b) & ~van_groups), band)))
    hi_all = np.flatnonzero(hold); hv_all = np.flatnonzero(hold_kf); hvl_all = np.flatnonzero(hold_kf & kf_long); n_ = int(band_all.sum()); null_all = top_n(alld_s, n_)
    net = dict(name=name, fit=n_fit, bundles=len(rows), km2=n_ * G.cell_km2(), share=n_ / G.mask.sum(), cap=capture(hi_all, band_all), nul=capture(hi_all, null_all),
               cap_van=capture(hv_all, band_all), nul_van=capture(hv_all, null_all), cap_van_long=capture(hvl_all, band_all), nul_van_long=capture(hvl_all, null_all),
               cap_groups_van=capture(np.flatnonzero(hold & van_groups), band_all), cap_nonvan=capture(np.flatnonzero(hold & ~van_groups), band_all),
               sum_band_km2=sum(r["km2"] for r in rows), mean_skill=np.mean([r["skill"] for r in rows]), mean_cap_van=np.nanmean([r["cap_van"] for r in rows]), mean_nul_van=np.nanmean([r["nul_van"] for r in rows]))
    print(f"\n== {name}: fit {n_fit} fronts, {len(rows)} bundles, network {net['km2']:,.0f} km2 ({net['share']:.0%} of AOI)")
    print(f"   hold-out ALL {len(hi_all)}: capture {net['cap']:.2f} (all-fire null {net['nul']:.2f}) | KF VANGUARD {len(hv_all)}: {net['cap_van']:.2f} (null {net['nul_van']:.2f}), >=150 km {len(hvl_all)}: {net['cap_van_long']:.2f} (null {net['nul_van_long']:.2f}) | plain-flag vanguard {net['cap_groups_van']:.2f} | non-vanguard {net['cap_nonvan']:.2f}")
    print(f"   per-bundle: Σ band {net['sum_band_km2']:,.0f} km2, mean skill {net['mean_skill']:+.2f}; vanguard capture mean {net['mean_cap_van']:.2f} vs null {net['mean_nul_van']:.2f}")
    for r in rows: print(f"   b{r['b']:>2} fit {r['fit']:>4} hold {r['hold']:>4} (kf van {r['hold_van']:>3}/{r['hold_van_long']:>3} long) q{r['q']} {r['km2']:>7,.0f} km2 cap {r['cap']:.2f} null {r['nul']:.2f} skill {r['skill']:+.2f} | kf van cap {r['cap_van']:.2f} (null {r['nul_van']:.2f}) long {r['cap_van_long']:.2f} | plain-flag van {r['cap_groups_van']:.2f} non-van {r['cap_nonvan']:.2f}")
    return net, rows, lab
res = {}
res["A_current"] = run("A current model (all long fronts)", plain)
labA = res["A_current"][2]
res["B_supported"] = run("B supported tier only (fire.md 'downstream to revisit')", plain & (tier == "supported"), lab=labA)
res["C_supp_weak"] = run("C supported+weak", plain & np.isin(tier, ["supported", "weak"]), lab=labA)
# lead-weighted: fronts starting ahead of the front weigh more (w = 1 + clip(lead,0,60)/20); season 2023 has no front -> w=1
w = np.where(np.isnan(lead), 1.0, 1.0 + np.clip(lead, 0, 60) / 20.0)
res["D_leadw"] = run("D lead-weighted UD (ahead-of-front fronts up to 4x)", plain, weights=w, lab=labA)
res["E_prefront"] = run("E fit only fronts starting ahead of the front (lead>=0) + 2023 (no front)", plain & ((lead >= 0) | np.isnan(lead)), lab=labA)
# the KF question: is a vanguard-ONLY fit feasible now? Fit seasons hold only 2024/25 KF chains (the front exists from 2024).
res["F_kf_all"] = run("F vanguard-only fit: ALL KF chains of the fit seasons", kf, lab=labA)
res["G_kf_long"] = run("G vanguard-only fit: KF chains >= 150 km", kf_long, lab=labA)
res["H_plain_plus_kf"] = run("H all long fronts + KF chains (KF weighted 3x)", plain | kf, weights=np.where(kf, 3.0, 1.0), lab=labA)
# tier composition of held-out vanguard fronts
print("\nheld-out 2025 KF vanguard chains by tier:", Counter(tier[(season == HOLD) & kf]), "| >=150 km:", Counter(tier[(season == HOLD) & kf_long]))
print("held-out 2025 plain-flag vanguard fronts by tier:", Counter(tier[(season == HOLD) & van_groups]))
print("held-out 2025 all long fronts by tier:", Counter(tier[(season == HOLD) & plain]))
# (3) direction agreement: bundle mean heading (from all fit fronts, start->end) vs vanguard fronts' heading in that bundle
def heading(j): x = T[j]["t"]; return math.degrees(math.atan2((x[-1][0] - x[0][0]) * kx, (x[-1][1] - x[0][1]) * 111)) % 360
def circ_mean(hs): a = np.radians(hs); return math.degrees(math.atan2(np.sin(a).mean(), np.cos(a).mean())) % 360, float(np.hypot(np.sin(a).mean(), np.cos(a).mean()))
for vname, vmask in [("KF vanguard chains", kf), ("KF chains >= 150 km", kf_long), ("plain-flag vanguard (earlier definition)", van_groups)]:
    print(f"\n== direction: bundle heading from ALL plain fronts (day order) vs its {vname}")
    agree_tot = n_tot = agree_kf_tot = n_kf = 0
    for b in range(K):
        ai = np.flatnonzero((labA == b) & plain); vi = np.flatnonzero((labA == b) & vmask)
        if len(ai) < 0.03 * ((season != HOLD) & plain).sum() or len(vi) < 5: continue
        ha, ra = circ_mean([heading(j) for j in ai]); hv_, rv = circ_mean([heading(j) for j in vi])
        diff = abs((hv_ - ha + 180) % 360 - 180); agree = np.mean([abs((heading(j) - ha + 180) % 360 - 180) < 90 for j in vi]); agree_all = np.mean([abs((heading(j) - ha + 180) % 360 - 180) < 90 for j in ai])
        agree_tot += agree * len(vi); n_tot += len(vi)
        # KF filter heading at the end (heading_deg) is a second, independent direction reading
        hk = [T[j]["hd"] for j in vi if T[j]["hd"] is not None]
        kf_note = ""
        if len(hk) >= 5:
            ak = np.mean([abs((h - ha + 180) % 360 - 180) < 90 for h in hk]); agree_kf_tot += ak * len(hk); n_kf += len(hk); kf_note = f", filter heading {ak:.0%}"
        print(f"   b{b:>2}: all {len(ai):>4} heading {ha:5.0f}° (R {ra:.2f}, {agree_all:.0%} within 90°) | {len(vi):>3} chains heading {hv_:5.0f}° (R {rv:.2f}) Δ {diff:3.0f}°, {agree:.0%} within 90° of bundle{kf_note}")
    if n_tot: print(f"   overall: {agree_tot/n_tot:.0%} of {n_tot} chains head within 90° of their bundle's mean heading (50% = no agreement)" + (f"; by KF filter heading {agree_kf_tot/n_kf:.0%} of {n_kf}" if n_kf else ""))
# (4) timing relative to the front per bundle
print("\n== timing: lead_start (days ahead of the season front) per bundle, seasons 2024+2025 (2023 has no front)")
for b in range(K):
    li = lead[(labA == b) & plain & ~np.isnan(lead)]
    if len(li) < 50: continue
    print(f"   b{b:>2}: n {len(li):>4} lead_start p10/p50/p90 {np.percentile(li,10):6.0f}/{np.percentile(li,50):6.0f}/{np.percentile(li,90):6.0f} d; ahead of front {np.mean(li>0):.0%}; vanguard {np.mean((li>=10)&(li<=60)):.1%}")
json.dump({k: dict(net=v[0], rows=v[1]) for k, v in res.items()}, open(OUTD / "res.json", "w"), indent=1, default=float)
