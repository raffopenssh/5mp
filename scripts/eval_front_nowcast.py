#!/usr/bin/env python3
"""
eval_front_nowcast.py — how good is the LIVE lead, and can it be better?

The season front is causal where it has ARRIVED. Where it has not, a
detection's lead (front - day) needs a date in the future, and production
substitutes `usual` (median front of previous complete seasons; MAE of that
against the season that then happens is ~15 d, eval_usual_shift.py) — larger
than the 10 d vanguard threshold it is compared with. This harness replays
complete seasons AS IF LIVE (rolling origin: for season s only seasons < s
are seen, and of s only days <= d) and scores, for every cell on the day of
its first burn d while the front had not yet arrived there:

  usual      lead = usual - d                      (production today)
  censored   usual conditioned on what is known: the front is not here yet,
             so only prior seasons with front > d count (median of those)
  staged     (what ships; staged_cond is the per-season conditional variant
             described here, staged the plain all-season median floored at 1 d)
             progress-aligned analog. The same causal sweep that finds the
             front (20 % of burnable land within 60 km burned) also yields
             the days the fraction passed the earlier STAGES (1 … 15 %). Take the highest
             stage already passed here this season (day t_j); prior seasons
             say how long stage j -> front usually takes (rem_k). lead =
             median(rem_k - (d - t_j) | rem_k > d - t_j). Aligns seasons by
             PROGRESS, not by calendar, so an early/late year needs no shift
             (the shift estimators eval_usual_shift.py rejected were calendar
             shifts fitted on a biased early sample). Falls back to
             `censored` where no stage has passed.
  causal q   no date at all: vanguard = burning while < q of the burnable
             land within 60 km has burned (known the day it happens)

Truth: the season's final front. vanguard = 10 <= lead <= 60 (production).
A cell the season never reaches has no lead and is a NEGATIVE for vanguard.

    python3 scripts/eval_front_nowcast.py                 # default sample
    python3 scripts/eval_front_nowcast.py CAF_Chinko XSA_Study_Area --json out.json
"""
import argparse
import json
import math
import sqlite3
import sys
import time
import warnings
from multiprocessing import Pool
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import fire_front as FF  # noqa: E402

STAGES = FF.STAGE_FRACTIONS + (FF.FRONT_FRACTION,)
DEFAULT = ["CAF_Chinko", "TZA_Serengeti", "ZMB_Kafue", "MOZ_Niassa", "ZWE_Hwange", "TZA_Selous", "TZA_Ruaha",
           "COD_Garamba", "CAF_Manovo_Gounda_St_Floris", "TCD_Zakouma", "BEN_Pendjari", "AGO_Luengue-Luiana",
           "ZMB_North_Luangwa", "CMR_Faro", "NGA_Gashaka-Gumti", "COD_Upemba", "BWA_Chobe", "SSD_Boma",
           "UGA_Murchison_Falls", "MWI_Nyika", "SEN_Niokolo_Koba", "CAF_Bamingui-Bangoran", "GHA_Mole", "CIV_Comoe",
           "SDN_Dinder", "ZAF_Kruger", "SSD_Badingilo", "TZA_Katavi", "ZMB_Liuwa_Plain", "MOZ_Gorongosa"]


def stage_grids(onset, burnable, lat_mid, fractions=None, smooth=True):
    return FF.stage_grids(onset, burnable, lat_mid, STAGES if fractions is None else fractions, smooth=smooth)


def cond_median(stack, floor):
    """Per-sample median of stack[k] over the k with stack[k] > floor (NaN if none)."""
    v = np.where(np.isfinite(stack) & (stack > floor[None]), stack, np.nan)
    return np.nanmedian(v, axis=0)


def staged_lead(d, T_now, T_prev):
    """d: (n,) day now; T_now: (J, n) this season's stage days (NaN/after d = not
    passed, strictly causal input); T_prev: (K, J, n) previous seasons' stage
    days, the last stage being the front. Returns (lead, stage_index or -1)."""
    J = T_now.shape[0]
    lead = np.full(d.shape, np.nan)
    used = np.full(d.shape, -1)
    for j in range(J - 2, -1, -1):                      # highest passed stage first
        passed = np.isfinite(T_now[j]) & (T_now[j] <= d) & (used < 0)
        if not passed.any():
            continue
        rem = T_prev[:, J - 1] - T_prev[:, j]            # (K, n) stage j -> front, previous seasons
        el = d - T_now[j]
        est = cond_median(rem, el) - el
        take = passed & np.isfinite(est)
        lead[take] = est[take]
        used[take] = j
    return lead, used


def eval_area(area_id):
    t0 = time.time()
    conn = sqlite3.connect(f"file:{FF.DB_PATH}?mode=ro", uri=True)
    lon, lat, day = FF.load_points(conn, area_id)
    if lon.size < FF.MIN_SEASON_DETECTIONS:
        return area_id, [], 0.0
    from datetime import date, timedelta
    latest = date.fromordinal(int(day.max()))
    first = date.fromordinal(int(day.min()))
    grid = FF.Grid.around(lon, lat)
    lat_mid = float((lat.min() + lat.max()) / 2)
    pad = FF.pad_cells(lat_mid)
    pg = FF.Grid(grid.x0 - pad * grid.res, grid.y0 - pad * grid.res, grid.nx + 2 * pad, grid.ny + 2 * pad, grid.res)
    sm = FF.season_start_month(FF.load_climatology(conn, pg))
    seasons = sorted({FF.season_of(date.fromordinal(int(d)), sm) for d in np.unique(day)})
    onsets, burnable = FF.load_onsets(conn, pg, sm, seasons)
    conn.close()
    crop = (slice(None), slice(pad, pad + grid.ny), slice(pad, pad + grid.nx))
    done = []       # (label, smoothed stages, raw stages, onset) of complete seasons, in order
    rows = []
    for s in seasons:
        s0, s1 = FF.season_bounds(s, sm)
        if (latest - s0).days < FF.COMPLETE_AFTER_DAYS or first > s0 + timedelta(days=15) or s not in onsets:
            continue
        m = (day >= s0.toordinal()) & (day <= s1.toordinal())
        if m.sum() < FF.MIN_SEASON_DETECTIONS:
            continue
        raw = stage_grids(onsets[s], burnable, lat_mid, smooth=False)[crop]
        smo = stage_grids(onsets[s], burnable, lat_mid, smooth=True)[crop]
        on = onsets[s][crop[1:]]
        if len(done) >= 2:
            rows.append(score(area_id, s, on, raw, smo, done))
        done.append((s, smo, raw, on))
    return area_id, [r for r in rows if r], time.time() - t0


def prf(pred, truth):
    tp = int((pred & truth).sum()); fp = int((pred & ~truth).sum()); fn = int((~pred & truth).sum())
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    f = 2 * p * r / (p + r) if tp else 0.0
    return dict(p=p, r=r, f1=f, n=int(pred.sum()))


def score(area_id, s, onset, raw, smo, done):
    J = len(STAGES)
    front = smo[J - 1]                                   # truth: what production stores
    # the sample: each cell on the day of its first burn, while the (causal, raw)
    # front had not arrived there — exactly the cells production reads `usual` for
    arrived = np.isfinite(raw[J - 1]) & (raw[J - 1] <= onset)
    sel = np.isfinite(onset) & ~arrived
    if sel.sum() < 100:
        return None
    d = onset[sel]
    truth = front[sel] - d                               # NaN = the season never came
    van = np.isfinite(truth) & (truth >= FF.VANGUARD_LEAD_DAYS) & (truth <= FF.VANGUARD_LEAD_MAX)
    Fprev = np.stack([p[1][J - 1][sel] for p in done])   # (K, n)
    Tprev = np.stack([np.stack([p[1][j][sel] for j in range(J)]) for p in done])  # (K, J, n)
    Tnow = np.stack([raw[j][sel] for j in range(J)])
    est = {}
    est["usual"] = np.nanmedian(Fprev, axis=0) - d
    est["censored"] = cond_median(Fprev, d) - d
    st_lead, used = staged_lead(d, Tnow, Tprev)
    est["staged_cond"] = np.where(np.isfinite(st_lead), st_lead, est["censored"])
    # What fire_front.py ships: the plain median of rem over ALL previous seasons
    # (one small grid per stage per area, no per-season history to keep), floored
    # at one day because the front is known not to be here yet; `usual` where no
    # stage has passed.
    pl = np.full(d.shape, np.nan)
    for j in range(J - 2, -1, -1):
        passed = np.isfinite(Tnow[j]) & (Tnow[j] <= d) & ~np.isfinite(pl)
        rem = np.nanmedian(Tprev[:, J - 1] - Tprev[:, j], axis=0)
        e = np.maximum(rem - (d - Tnow[j]), 1.0)
        pl = np.where(passed & np.isfinite(e), e, pl)
    est["staged"] = np.where(np.isfinite(pl), pl, est["usual"])
    out = dict(area=area_id, season=s, n=int(sel.sum()), n_truth=int(np.isfinite(truth).sum()),
               vanguard_share=float(van.mean()), staged_used=float((used >= 0).mean()), prior=len(done))
    for k, e in est.items():
        both = np.isfinite(e) & np.isfinite(truth)
        err = (e - truth)[both]
        pred = np.isfinite(e) & (e >= FF.VANGUARD_LEAD_DAYS) & (e <= FF.VANGUARD_LEAD_MAX)
        out[k] = dict(mae=float(np.abs(err).mean()) if err.size else None,
                      bias=float(err.mean()) if err.size else None,
                      cover=float(both.sum() / max(1, np.isfinite(truth).sum())),
                      neg=float((e[np.isfinite(e)] < 0).mean()) if np.isfinite(e).any() else None,
                      **prf(pred, van))
    for j, q in enumerate(STAGES[:-1]):
        pred = ~(np.isfinite(Tnow[j]) & (Tnow[j] <= d))    # stage q not yet passed here
        out[f"causal{q:g}"] = prf(pred, van)
    return out


def med(rows, key, sub):
    v = [r[key][sub] for r in rows if r.get(key) and r[key].get(sub) is not None and not (isinstance(r[key][sub], float) and math.isnan(r[key][sub]))]
    return (float(np.median(v)), len(v)) if v else (float("nan"), 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("areas", nargs="*")
    ap.add_argument("--json")
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--stages", help="comma-separated fractions below the front's, e.g. 0.01,0.05,0.1 (ablation)")
    a = ap.parse_args()
    global STAGES
    if a.stages:
        STAGES = tuple(sorted(float(x) for x in a.stages.split(","))) + (FF.FRONT_FRACTION,)
    areas = a.areas or DEFAULT
    rows = []
    with Pool(a.procs) as pool:
        for aid, rs, dt in pool.imap_unordered(eval_area, areas):
            rows += rs
            print(f"{aid}: {len(rs)} held-out season(s) in {dt:.0f}s", flush=True)
    if not rows:
        sys.exit("no area-season with >= 2 prior complete seasons — nothing measured")
    print(f"\n{len(rows)} area-seasons over {len({r['area'] for r in rows})} areas; medians across area-seasons")
    print(f"cells scored (first burn before the front arrived): median {int(np.median([r['n'] for r in rows])):,}; "
          f"true vanguard share {np.median([r['vanguard_share'] for r in rows]):.2f}; "
          f"a stage had passed for {np.median([r['staged_used'] for r in rows]):.2f} of them")
    print(f"\n  {'estimator':12}{'MAE d':>8}{'bias d':>8}{'cover':>7}{'lead<0':>8}{'prec':>7}{'recall':>8}{'F1':>6}")
    summary = {}
    for k in ("usual", "censored", "staged_cond", "staged"):
        v = {x: med(rows, k, x)[0] for x in ("mae", "bias", "cover", "neg", "p", "r", "f1")}
        summary[k] = v
        print(f"  {k:12}{v['mae']:8.1f}{v['bias']:+8.1f}{v['cover']:7.2f}{v['neg']:8.2f}{v['p']:7.2f}{v['r']:8.2f}{v['f1']:6.2f}")
    for q in STAGES[:-1]:
        k = f"causal{q:g}"
        v = {x: med(rows, k, x)[0] for x in ("p", "r", "f1")}
        summary[k] = v
        print(f"  {'<' + format(q * 100, 'g') + '% burned':12}{'':8}{'':8}{'':7}{'':8}{v['p']:7.2f}{v['r']:8.2f}{v['f1']:6.2f}")
    # paired: staged vs usual, per area-season
    pm = [r["usual"]["mae"] - r["staged"]["mae"] for r in rows if r["usual"]["mae"] is not None and r["staged"]["mae"] is not None]
    pf = [r["staged"]["f1"] - r["usual"]["f1"] for r in rows]
    summary["paired"] = dict(mae_gain_median=float(np.median(pm)), mae_better_share=float(np.mean(np.array(pm) > 0)),
                             f1_gain_median=float(np.median(pf)), f1_better_share=float(np.mean(np.array(pf) > 0)), n=len(pm))
    print(f"\npaired staged − usual: MAE {-np.median(pm):+.1f} d (better in {100 * np.mean(np.array(pm) > 0):.0f} % of {len(pm)}), "
          f"F1 {np.median(pf):+.2f} (better in {100 * np.mean(np.array(pf) > 0):.0f} %)")
    if a.json:
        json.dump(dict(stages=STAGES, areas=areas, summary=summary, rows=rows), open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
