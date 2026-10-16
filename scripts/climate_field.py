#!/usr/bin/env python3
"""ERA5-Land daily fuel/soil fields the fire pipeline can query per point and day (sibling of wind_field.py).

  ensure(aoi, start, end, months, python=.venv/bin/python)  -> fetch missing months (fetch_era5.py --product land) + fold
  ClimateField.load(aoi)      -> data/climate/era5/<aoi>_land_daily.npz (float16, 0.1 deg grid, incremental, .nc prunable)
  cf.rain(lons, lats, days)               mm/day            (ERA5-Land tp accumulates from 00 UTC; the 00 UTC value of day d+1 is day d's total)
  cf.dry_days(lons, lats, days, thr_mm=1) days since the last day with rain >= thr_mm (NaN if no such day inside the record)
  cf.rain_window(lons, lats, days, n=30)  mm summed over the n days ending on `day` (NaN if the window is not fully covered)
  cf.vpd(lons, lats, days)                kPa at 12 UTC (Tetens, from t2m/d2m) — afternoon atmospheric dryness
  cf.soil(lons, lats, days)               volumetric soil water, layer 1 (0-7 cm), mean of 00 and 12 UTC (m3/m3)
  cured(lons, lats, days, n_dry=N, thr_mm=X) -> bool: fuel 'cured' = at least N days since rain >= X mm

Missing = NaN, never 0 (invariant 1). Fields are stored per day of the UTC calendar day; grid lookup is nearest cell.
The use is a MEASURED covariate: a cured-fuel prior for the Kalman/season-front ladder (eval_fire_baseline.py --xsa),
a rain-gated season close, and soil-moisture drying as a herd-movement covariate (fire_wind_check.py --climate).
Nothing here is adopted by eye.

  python3 scripts/climate_field.py --aoi XSA_Study_Area --build [--prune-nc]
"""
import argparse, glob, os, subprocess, sys
from datetime import date
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERA5 = os.path.join(ROOT, "data", "climate", "era5")


def _read_nc(path):
    """(times s, lats, lons, dict var->array[t, lat, lon]) via h5py (system python) or xarray (venv)"""
    names = ("tp", "t2m", "d2m", "swvl1")
    try:
        import h5py
        with h5py.File(path, "r") as f:
            V = {}
            for n in names:
                d = f[n]; a = d[:].astype(np.float32)
                sf, ao, fv = d.attrs.get("scale_factor"), d.attrs.get("add_offset"), d.attrs.get("_FillValue")
                if fv is not None: a[d[:] == fv] = np.nan
                if sf is not None: a = a * float(sf) + float(ao or 0)
                V[n] = a
            return f["valid_time"][:].astype(np.int64), f["latitude"][:], f["longitude"][:], V
    except ImportError:
        import xarray as xr
        ds = xr.open_dataset(path)
        return ds["valid_time"].values.astype("datetime64[s]").astype(np.int64), ds["latitude"].values, ds["longitude"].values, {n: ds[n].values.astype(np.float32) for n in names}


def _es_kpa(t_k):
    t = t_k - 273.15; return 0.6108 * np.exp(17.27 * t / (t + 237.3))


def build(aoi, prune=False):
    out = os.path.join(ERA5, f"{aoi}_land_daily.npz")
    files = sorted(glob.glob(os.path.join(ERA5, f"{aoi}_land_????-??.nc")))
    lats = lons = None; days = []; TP00 = []; VPD = []; T12 = []; SW = []; sources = []
    if os.path.exists(out):
        z = np.load(out, allow_pickle=False); lats, lons = z["lats"], z["lons"]; sources = z["sources"].tolist()
        days = z["days"].tolist(); TP00 = list(z["tp00"]); VPD = list(z["vpd12"]); T12 = list(z["t12"]); SW = list(z["swvl1"])
    new = [p for p in files if os.path.basename(p) not in sources]
    if not new and not days:
        raise SystemExit(f"no ERA5-Land files for {aoi}; run fetch_era5.py --product land first")
    for p in new:
        t, la, lo, V = _read_nc(p)
        if lats is None: lats, lons = la, lo
        assert np.allclose(la, lats) and np.allclose(lo, lons), p
        d = (t // 86400).astype(np.int64); h = (t % 86400) // 3600
        for dd in np.unique(d):
            if int(dd) in days: continue
            m0 = (d == dd) & (h == 0); m12 = (d == dd) & (h == 12); m = d == dd
            days.append(int(dd))
            TP00.append(V["tp"][m0].mean(0) * 1000.0 if m0.any() else np.full(V["tp"].shape[1:], np.nan, np.float32))   # m -> mm
            if m12.any():
                VPD.append(np.clip(_es_kpa(V["t2m"][m12].mean(0)) - _es_kpa(V["d2m"][m12].mean(0)), 0, None)); T12.append(V["t2m"][m12].mean(0) - 273.15)
            else:
                VPD.append(np.full(V["tp"].shape[1:], np.nan, np.float32)); T12.append(np.full(V["tp"].shape[1:], np.nan, np.float32))
            SW.append(V["swvl1"][m].mean(0))
        sources.append(os.path.basename(p))
    order = np.argsort(days); days = np.asarray(days)[order]
    np.savez_compressed(out, days=days, lats=lats, lons=lons, tp00=np.asarray(TP00, np.float16)[order], vpd12=np.asarray(VPD, np.float16)[order],
                        t12=np.asarray(T12, np.float16)[order], swvl1=np.asarray(SW, np.float16)[order], sources=np.asarray(sorted(sources)))
    print(f"[land] {out}: {len(days)} days, grid {len(lats)}x{len(lons)}, {len(sources)} months (+{len(new)} new)")
    if prune:
        for p in new: os.remove(p)
    return out


def ensure(aoi, start, end, months=None, python=None, prune=False):
    py = python or sys.executable
    cmd = [py, os.path.join(ROOT, "scripts", "fetch_era5.py"), "--product", "land", "--aoi", aoi, "--start", start, "--end", end]
    if months: cmd += ["--months", ",".join(str(m) for m in months)]
    subprocess.run(cmd, check=True)
    return build(aoi, prune=prune)


class ClimateField:
    def __init__(self, npz):
        z = np.load(npz, allow_pickle=False)
        self.days = z["days"]; self.lats = z["lats"]; self.lons = z["lons"]; self.sources = z["sources"].tolist()
        self.day_index = {int(d): i for i, d in enumerate(self.days)}
        self.tp00 = z["tp00"].astype(np.float32); self.vpd12 = z["vpd12"].astype(np.float32); self.t12 = z["t12"].astype(np.float32); self.swvl1 = z["swvl1"].astype(np.float32)
        # daily rain of day d = accumulated value at 00 UTC of day d+1 (NaN where d+1 is not in the record)
        self.rain_d = np.full_like(self.tp00, np.nan)
        for i, d in enumerate(self.days):
            j = self.day_index.get(int(d) + 1)
            if j is not None: self.rain_d[i] = self.tp00[j]
        self.dlat = float(abs(self.lats[1] - self.lats[0])); self.dlon = float(abs(self.lons[1] - self.lons[0]))
        self.lat0 = float(self.lats[0]); self.lon0 = float(self.lons[0]); self.lat_desc = self.lats[1] < self.lats[0]

    @classmethod
    def load(cls, aoi, rebuild=False):
        npz = os.path.join(ERA5, f"{aoi}_land_daily.npz"); ncs = glob.glob(os.path.join(ERA5, f"{aoi}_land_????-??.nc"))
        folded = set(np.load(npz, allow_pickle=False)["sources"].tolist()) if os.path.exists(npz) else set()
        if rebuild or not os.path.exists(npz) or any(os.path.basename(p) not in folded for p in ncs): build(aoi)
        return cls(npz)

    @staticmethod
    def daynum(d):
        if isinstance(d, (int, np.integer)): return int(d)
        if isinstance(d, str): d = date.fromisoformat(d[:10])
        return (d - date(1970, 1, 1)).days

    def covered(self):
        return [str(date.fromordinal(int(d) + date(1970, 1, 1).toordinal())) for d in (self.days.min(), self.days.max())]

    def _rc(self, lons, lats):
        r = np.rint((self.lat0 - np.asarray(lats)) / self.dlat if self.lat_desc else (np.asarray(lats) - self.lat0) / self.dlat).astype(int)
        c = np.rint((np.asarray(lons) - self.lon0) / self.dlon).astype(int)
        ok = (r >= 0) & (r < len(self.lats)) & (c >= 0) & (c < len(self.lons)); return r, c, ok

    def _dn(self, days):
        return np.asarray(days) if isinstance(days, np.ndarray) and days.dtype.kind == "i" else np.asarray([self.daynum(d) for d in days])

    def _lookup(self, arr, lons, lats, days):
        dn = self._dn(days); ti = np.asarray([self.day_index.get(int(d), -1) for d in dn]); r, c, ok = self._rc(lons, lats); ok &= ti >= 0
        o = np.full(len(ti), np.nan, np.float32); o[ok] = arr[ti[ok], r[ok], c[ok]]; return o

    def rain(self, lons, lats, days): return self._lookup(self.rain_d, lons, lats, days)
    def vpd(self, lons, lats, days): return self._lookup(self.vpd12, lons, lats, days)
    def temp(self, lons, lats, days): return self._lookup(self.t12, lons, lats, days)
    def soil(self, lons, lats, days): return self._lookup(self.swvl1, lons, lats, days)

    def rain_window(self, lons, lats, days, n=30):
        """mm over the n days ending on day (inclusive); NaN unless every day of the window is in the record"""
        dn = self._dn(days); r, c, ok = self._rc(lons, lats); out = np.full(len(dn), np.nan, np.float32)
        for i in np.flatnonzero(ok):
            idx = [self.day_index.get(int(dn[i]) - k, -1) for k in range(n)]
            if min(idx) < 0: continue
            v = self.rain_d[idx, r[i], c[i]]
            if np.isfinite(v).all(): out[i] = v.sum()
        return out

    def dry_days(self, lons, lats, days, thr_mm=1.0, max_back=180):
        """days since the last day with rain >= thr_mm (0 = it rained that day); NaN if the record does not reach back to one"""
        dn = self._dn(days); r, c, ok = self._rc(lons, lats); out = np.full(len(dn), np.nan, np.float32)
        for i in np.flatnonzero(ok):
            for k in range(max_back):
                j = self.day_index.get(int(dn[i]) - k)
                if j is None: break
                v = self.rain_d[j, r[i], c[i]]
                if not np.isfinite(v): break
                if v >= thr_mm: out[i] = k; break
        return out

    def cured(self, lons, lats, days, n_dry=10, thr_mm=1.0):
        d = self.dry_days(lons, lats, days, thr_mm); return np.where(np.isfinite(d), d >= n_dry, False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--aoi", default="XSA_Study_Area"); ap.add_argument("--build", action="store_true")
    ap.add_argument("--ensure", nargs=2, metavar=("START", "END")); ap.add_argument("--months"); ap.add_argument("--prune-nc", action="store_true")
    a = ap.parse_args()
    if a.ensure: ensure(a.aoi, *a.ensure, [int(m) for m in a.months.split(",")] if a.months else None, prune=a.prune_nc)
    elif a.build: build(a.aoi, prune=a.prune_nc)
    else:
        cf = ClimateField.load(a.aoi); print("covered", cf.covered(), "days", len(cf.days), "grid", len(cf.lats), "x", len(cf.lons), "months", len(cf.sources))
