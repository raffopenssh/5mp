"""Seismology probe (ETAS / Hawkes idea): is a detection an INDEPENDENT ignition
(background) or TRIGGERED by fire nearby in the last days (spread)?

Kernel taken from pair.py's measured two-point function: real excess only at
0-15 km, 1-2 d. Here: a detection is 'background' if NO detection burned within
R km in the previous D days. Then, with the same harness as the vanguard test,
the tracker is run on background detections real vs day-shuffled, split by
season position (ahead of front / in season). If background-in-season has
skill, Hawkes extends the scouts' population past the front: the seam.

    python3 scripts/fire_vanguard/hawkes.py XSA_Study_Area 2024/25 [R_km] [D_days]
"""
import sys, sqlite3, statistics as st, warnings, time
warnings.filterwarnings("ignore")
sys.path.insert(0, "/home/exedev/5mp/scripts")
import numpy as np
from scipy import ndimage
import fire_front as FF, aoi_lib
from fire_source import DB_PATH, load_aoi_fires
from eval_fire_null import shuffle_days, metrics, SKILL
import rebuild_fire_trajectories_v5 as B
from multiprocessing import Pool
from datetime import date

area, season = sys.argv[1], sys.argv[2]
R_KM = float(sys.argv[3]) if len(sys.argv) > 3 else 10
D = int(sys.argv[4]) if len(sys.argv) > 4 else 3
conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
fs = FF.FrontSet(conn, area); S = fs.seasons[season]; g = S["grid"]
s0, s1 = FF.season_bounds(season, fs.start_month)
pk = aoi_lib.inject_aoi({}, area); B.AOI_IDS.add(area)
fires = load_aoi_fires(area, s0.isoformat(), s1.isoformat())
lon = np.array([f["longitude"] for f in fires]); lat = np.array([f["latitude"] for f in fires])
dos = np.array([date.fromisoformat(f["acq_date"]).toordinal() for f in fires]) - s0.toordinal()
lead = fs.lead_array(lon, lat, [f["acq_date"] for f in fires])
iy, ix = g.index(lon, lat)
nd = int(dos.max()) + 1
# daily occupancy grid, dilated by R km, summed over the previous D days
occ = np.zeros((nd, g.ny, g.nx), bool)
occ[dos, iy, ix] = True
r_cells = max(1, int(round(R_KM / (g.res * 111.0))))
yy, xx = np.ogrid[-r_cells:r_cells + 1, -r_cells:r_cells + 1]
disk = (xx**2 + yy**2) <= r_cells**2
recent = np.zeros((nd, g.ny, g.nx), bool)
for d in range(nd):
    prev = occ[max(0, d - D):d].any(axis=0) if d > 0 else np.zeros((g.ny, g.nx), bool)
    recent[d] = ndimage.binary_dilation(prev, disk) if prev.any() else prev
background = ~recent[dos, iy, ix]
print(f"{area} {season}: {len(fires):,} det; background (no fire within {R_KM:g} km in prev {D} d): "
      f"{background.sum():,} ({100*background.mean():.1f}%)")
ahead = np.isfinite(lead) & (lead >= 10)
print(f"  lead>=10: {ahead.sum():,} of which background {(ahead & background).sum():,} "
      f"({100*(ahead&background).sum()/max(1,ahead.sum()):.0f}%); in-season background {(~ahead & background).sum():,}")

def run(job):
    tag, sel = job
    return tag, B.process_park_fires(sel, area, pk["geometry"])

def test(name, mask):
    sel = [f for f, m in zip(fires, mask) if m]
    if len(sel) < 500:
        print(f"[{name}] {len(sel)} det: too few"); return
    jobs = [("real", sel), ("null0", shuffle_days(sel, 7)), ("null1", shuffle_days(sel, 8))]
    with Pool(3) as p:
        res = dict(p.imap_unordered(run, jobs))
    real = metrics(res["real"]); null = {k: st.mean(metrics(res[t])[k] for t in ("null0", "null1")) for k in real}
    sk = {k: 1 - null[k] / real[k] if real[k] else 0 for k in SKILL}
    print(f"[{name}] {len(sel):,} det  groups {real['groups']}  links {real['links']} vs {null['links']:.0f} "
          f"({sk['links']:+.2f})  long {real['long_fronts']} vs {null['long_fronts']:.0f}  "
          f"fires_in_long {real['fires_in_long']} vs {null['fires_in_long']:.0f}  mean skill {st.mean(sk.values()):+.2f}")

test("background, all", background)
test("background, in-season (lead<10)", background & ~ahead)
test("background, lead 0..10 (the seam)", background & np.isfinite(lead) & (lead >= 0) & (lead < 10))
test("background, lead -20..0", background & np.isfinite(lead) & (lead >= -20) & (lead < 0))
test("lead>=10 (vanguard, reference)", ahead)
test("lead 0..10, all (control)", np.isfinite(lead) & (lead >= 0) & (lead < 10))
