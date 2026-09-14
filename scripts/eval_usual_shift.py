#!/usr/bin/env python3
"""Is a shift-calibrated live `usual` front better than the plain one?

The eikonal probe (fire.md "Season front & vanguard") found on XSA 2024/25
that `usual` shifted by the median offset observed so far beat plain `usual`
(6.7 vs 7.8 d MAE at day 45-60), and fire.md listed "shift-calibrate the live
usual" as a cheap win. This script asks the same question on PARKS, where the
grids are 10-100x smaller, by replaying complete seasons: at day-of-season d,
estimate the shift from the cells the front has reached by then, and compare
it with the season's final median (front - usual) over all cells with both.

Two estimators:
  median   median(front - usual) over reached cells (what the API's
           `usual_offset_days` prints)
  quantile day d minus the day the usual grid reaches as many cells as this
           season has by d (the S-curve reading the UI does at the playhead)

Result (2026-09-14, 7 areas, 44 complete seasons; see fire.md): both are
WORSE than no shift at 45/60/90 d on parks -- the cells reached first are
exactly the cells that run earliest against usual (selection bias), so the
early estimate is biased early by tens of days (Kafue 2024: -173 d at day 45
against a final -25). Only the XSA AOI (12k+ cells) behaves. The live
`usual` therefore stays UNshifted; do not re-add the shift without beating
the plain column here.

    python3 scripts/eval_usual_shift.py [area ...]
"""
import sys, sqlite3
import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import fire_front as ff  # noqa: E402

AREAS = ["TZA_Serengeti", "COD_Virunga", "CAF_Chinko", "XSA_Study_Area",
         "ZMB_Kafue", "MOZ_Niassa", "ZWE_Hwange"]
DAYS = (45, 60, 90)
MIN_CELLS = 20


def est_median(f, u, d):
    m = np.isfinite(f) & np.isfinite(u) & (f <= d)
    return (float(np.median(f[m] - u[m])), int(m.sum())) if m.sum() >= MIN_CELLS else (None, int(m.sum()))


def est_quantile(f, u, d):
    m = np.isfinite(u)
    n = int((m & (f <= d)).sum())
    if n < MIN_CELLS or n > m.sum():
        return None, n
    return d - float(np.sort(u[m])[n - 1]), n


def main():
    areas = sys.argv[1:] or AREAS
    conn = sqlite3.connect(str(ff.BASE_DIR / "db.sqlite3"))
    errs = {k: {d: [] for d in DAYS} for k in ("median", "quantile", "none")}
    n_seasons = 0
    for a in areas:
        fs = ff.FrontSet(conn, a)
        for s, v in sorted(fs.seasons.items()):
            if not v["complete"]:
                continue
            f, u = v["front"], v["usual"]
            both = np.isfinite(f) & np.isfinite(u)
            if both.sum() < 200:
                continue
            n_seasons += 1
            full = float(np.median(f[both] - u[both]))
            cells = []
            for d in DAYS:
                em, n = est_median(f, u, d)
                eq, _ = est_quantile(f, u, d)
                if em is not None:
                    errs["median"][d].append(abs(em - full))
                    errs["none"][d].append(abs(full))
                if eq is not None:
                    errs["quantile"][d].append(abs(eq - full))
                cells.append(f"{d}d: med {em:+.0f} q {eq:+.0f} ({n})" if em is not None and eq is not None else f"{d}d: -")
            print(f"{a:16s} {s:8s} final {full:+4.0f}  " + "  ".join(cells))
    print(f"\n{n_seasons} complete seasons. MAE of the estimated shift against the season's final median:")
    print("  day   none(=0)  median  quantile   n")
    for d in DAYS:
        n = len(errs["none"][d])
        if not n:
            continue
        print(f"  {d:3d}   {np.mean(errs['none'][d]):6.1f}   {np.mean(errs['median'][d]):6.1f}   "
              f"{np.mean(errs['quantile'][d]) if errs['quantile'][d] else float('nan'):6.1f}   {n}")
    print("A shift is only worth applying where its column beats `none`.")


if __name__ == "__main__":
    main()
