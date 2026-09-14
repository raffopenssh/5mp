"""Seismology probe 2 (first-arrival travel time): the season front T(x,y) is an
arrival-time surface. Its gradient is slowness: speed = 1/|grad T| km/day, the
map of where the season runs and where it stalls. And a travel-time model
(Dijkstra on the slowness grid = discrete Eikonal) can PREDICT this season's
arrival from (a) last season's speed map and (b) the first days of this season
-- the live use case, where lead_basis='usual' stands in today.

Test: predict 2025/26 arrival for cells whose front is > 30 d into the season,
from the 2024/25 speed map seeded with 2025/26 cells that had arrived by day
30/45/60. Compare MAE (days) with the 'usual' predictor (2024/25 front) and
with usual shifted by the median offset observed so far (the honest baseline).

    python3 scripts/fire_vanguard/eikonal.py XSA_Study_Area 2024/25 2025/26
"""
import sys, sqlite3, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "/home/exedev/5mp/scripts")
import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
import fire_front as FF
from fire_source import DB_PATH

area, prev, cur = sys.argv[1:4]
conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
fs = FF.FrontSet(conn, area)
P, C = fs.seasons[prev], fs.seasons[cur]
g = P["grid"]; ny, nx = g.ny, g.nx
km_x = g.res * 111.0 * np.cos(np.radians(g.y0 + g.res * ny / 2)); km_y = g.res * 111.0
Tp, Tc = P["front"], C["front"]

# --- speed map from last season -------------------------------------------
Tps = ndimage.gaussian_filter(np.where(np.isfinite(Tp), Tp, 0), 2) / np.maximum(ndimage.gaussian_filter(np.isfinite(Tp).astype(float), 2), 1e-6)
gy, gx = np.gradient(Tps, km_y, km_x)            # d/km
slow = np.hypot(gx, gy)                            # day/km
slow[~np.isfinite(Tp)] = np.nan
speed = 1 / np.maximum(slow, 1e-3)
ok = np.isfinite(slow)
print(f"{area}: speed of the {prev} season front, km/day: p10 {np.nanpercentile(speed[ok],10):.1f} "
      f"median {np.nanmedian(speed[ok]):.1f} p90 {np.nanpercentile(speed[ok],90):.1f}  "
      f"(cells with a front: {ok.sum():,})")

# --- Dijkstra travel time on the slowness grid ------------------------------
def travel_time(slow, seeds_t):
    """seeds_t: array (ny,nx) with arrival day where known, NaN elsewhere."""
    s = np.where(np.isfinite(slow), slow, np.nan)
    s = np.where(np.isfinite(s), s, np.nanmax(s) * 3)  # unburnable = very slow
    idx = np.arange(ny * nx).reshape(ny, nx)
    rows, cols, w = [], [], []
    for dy, dx, dist in ((0, 1, km_x), (1, 0, km_y), (1, 1, np.hypot(km_x, km_y)), (1, -1, np.hypot(km_x, km_y))):
        a = idx[max(0, -dy):ny - max(0, dy), max(0, -dx):nx - max(0, dx)]
        b = idx[max(0, dy):ny - max(0, -dy) if dy else ny, max(0, dx):nx - max(0, -dx) if dx else nx]
        sa, sb = s.ravel()[a.ravel()], s.ravel()[b.ravel()]
        cost = 0.5 * (sa + sb) * dist
        rows += [a.ravel(), b.ravel()]; cols += [b.ravel(), a.ravel()]; w += [cost, cost]
    rows = np.concatenate(rows); cols = np.concatenate(cols); w = np.concatenate(w)
    # virtual source node connects to every seed with its arrival day as cost
    src = ny * nx
    sm = np.isfinite(seeds_t)
    rows = np.concatenate([rows, np.full(sm.sum(), src)]); cols = np.concatenate([cols, idx[sm]]); w = np.concatenate([w, seeds_t[sm]])
    G = coo_matrix((w, (rows, cols)), shape=(src + 1, src + 1)).tocsr()
    return dijkstra(G, indices=src)[:src].reshape(ny, nx)

print(f"\npredicting {cur} arrival (cells with front > seed day) -- MAE in days, lower is better")
print(f"{'seen to day':>12}{'cells':>9}{'usual':>9}{'usual+shift':>13}{'eikonal':>9}{'eik+shift':>11}")
for seed_day in (30, 45, 60, 90):
    known = np.where(np.isfinite(Tc) & (Tc <= seed_day), Tc, np.nan)
    target = np.isfinite(Tc) & (Tc > seed_day) & np.isfinite(Tp)
    if np.isfinite(known).sum() < 20:
        print(f"{seed_day:>12}  too few arrived cells"); continue
    # honest baseline: last season's front, shifted by the median offset seen so far
    seen = np.isfinite(known) & np.isfinite(Tp)
    shift = np.nanmedian(Tc[seen] - Tp[seen]) if seen.sum() else 0.0
    usual = Tp; usual_s = Tp + shift
    eik = travel_time(slow, known)
    eik = np.maximum(eik, seed_day)  # can't have arrived earlier than we'd have seen
    eik_s = travel_time(slow * 1.0, known)  # same field; shift test = calibrate speed by shift sign
    def mae(pred): return np.nanmean(np.abs(pred[target] - Tc[target]))
    print(f"{seed_day:>12}{target.sum():>9,}{mae(usual):>9.1f}{mae(usual_s):>13.1f}{mae(eik):>9.1f}{'':>11}")
