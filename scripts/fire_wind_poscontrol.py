import sys, os, numpy as np
from collections import Counter
sys.path.insert(0, 'scripts'); from gzjson import gz
from wind_field import WindField, ang_diff, bearing, wind_to_deg, steps
raw = gz.load('data/fire_groups_v5/XSA_Study_Area.json'); wf = WindField.load('XSA_Study_Area')
print('types', Counter(g.get('group_type') for g in raw).most_common(8))
rng = np.random.default_rng(0)
def run(label, sel):
    rows = []
    for g in raw:
        if not sel(g): continue
        for lo, la, d0, d1, b, km, gap in steps(g.get('trajectory') or []):
            if int(d0[5:7]) in (1, 2): rows.append((lo, la, WindField.daynum(d0), b, km / gap))
    if not rows: print(label, 'no steps'); return
    R = np.array(rows); lon, lat, d0, brg, spd = R.T; d0 = d0.astype(int)
    u, v = wf.vec(lon, lat, d0); ok = np.isfinite(u)
    lon, lat, d0, brg, spd, u, v = (a[ok] for a in (lon, lat, d0, brg, spd, u, v))
    diff = np.abs(ang_diff(brg, wind_to_deg(u, v)))
    down = (diff <= 45).mean(); up = (diff >= 135).mean()
    nulls = []
    for _ in range(100):
        nd = d0[rng.permutation(len(d0))]; uu, vv = wf.vec(lon, lat, nd); o = np.isfinite(uu)
        nulls.append((np.abs(ang_diff(brg[o], wind_to_deg(uu[o], vv[o]))) <= 45).mean())
    nulls = np.array(nulls)
    for name, m in (('all', np.ones(len(brg), bool)), ('>20 km/d', spd > 20), ('>40 km/d', spd > 40)):
        if m.sum() < 50: continue
        dn = (diff[m] <= 45).mean(); upn = (diff[m] >= 135).mean()
        print(f'{label:28s} {name:9s} n={m.sum():7d} down={dn:.3f} up={upn:.3f}')
    print(f'{label:28s} null(all)={nulls.mean():.3f}±{nulls.std():.4f} z={(down-nulls.mean())/max(nulls.std(),1e-9):.1f}')
run('transh >=150km', lambda g: g.get('group_type') == 'transhumance' and float(g.get('distance_km') or 0) >= 150)
run('transh <150km', lambda g: g.get('group_type') == 'transhumance' and float(g.get('distance_km') or 0) < 150)
run('non-transhumance', lambda g: g.get('group_type') != 'transhumance')
run('non-transh, days<=5', lambda g: g.get('group_type') != 'transhumance' and (g.get('days') or 99) <= 5)
