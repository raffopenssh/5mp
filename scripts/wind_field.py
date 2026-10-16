#!/usr/bin/env python3
"""ERA5 10 m wind as a daily field the fire pipeline can query per point and day.

One module, three jobs, so the herd model, the vanguard/Kalman evaluation and the
cron builders all read the SAME wind:

  ensure(aoi, months)        -> fetch missing monthly .nc via scripts/fetch_era5_wind.py (CDS creds in ~/.cdsapirc)
  WindField.load(aoi)        -> compact npz cache (data/climate/era5/<aoi>_wind_daily.npz), rebuilt when a .nc is newer
  wf.at(lon, lat, 'YYYY-MM-DD', which='day'|'pm')  -> (u, v) m/s, nearest 0.25 deg cell, daily mean or 12-15 UTC mean
  wf.vec(lons, lats, dates)  -> vectorised u, v arrays (NaN where no data)
  annotate_groups(groups, wf) -> adds per-group wind fields (see ANNOTATION) to fire_groups_v5 dicts

Conventions (documented once, used everywhere):
  * bearings are degrees clockwise from north, the direction of TRAVEL ("to"). ERA5 u,v are the
    direction the air moves, so wind_to = atan2(u, v); the meteorological "from" is wind_to + 180.
  * ang_diff(a, b) in (-180, 180]; |diff| <= 45 downwind, >= 135 upwind, else crosswind.
  * 'day' = mean of the 3-hourly samples of the calendar day (UTC); 'pm' = mean of 12:00 and 15:00 UTC
    (14:00-17:00 local, the hours a savanna fire front runs). Both are kept; the check reports both.
  * ERA5 has ~5 days of latency (ERA5T), so the NRT day in the cron never has wind. Missing = NaN, never 0.

Usage:
  python3 scripts/wind_field.py --aoi XSA_Study_Area --build            # (re)build the npz from cached .nc files
  .venv/bin/python scripts/wind_field.py --aoi XSA_Study_Area --ensure 2025-10 2026-03   # fetch + build
"""
import argparse, glob, math, os, re, subprocess, sys
from datetime import date, datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERA5 = os.path.join(ROOT, "data", "climate", "era5")
PM_HOURS = (12, 15)

ANNOTATION = {
    "wind_steps": "number of daily steps with wind available",
    "wind_downwind_share": "share of steps whose bearing is within 45 deg of the daily wind-to bearing",
    "wind_upwind_share": "share of steps >= 135 deg from the wind-to bearing",
    "wind_align_deg": "circular mean of (step bearing - wind-to bearing), deg; 0 = travelling with the wind",
    "wind_speed_ms": "mean 10 m wind speed over the front's steps (m/s)",
}


def ang_diff(a, b):
    """signed smallest angle a - b in (-180, 180]"""
    return (np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0


def bearing(lon1, lat1, lon2, lat2):
    """initial bearing deg (travel direction), equirectangular — fine at step scale"""
    lat1, lat2 = np.asarray(lat1), np.asarray(lat2)
    dy = lat2 - lat1
    dx = (np.asarray(lon2) - np.asarray(lon1)) * np.cos(np.radians((lat1 + lat2) / 2))
    return np.degrees(np.arctan2(dx, dy)) % 360.0


def dist_km(lon1, lat1, lon2, lat2):
    lat1, lat2 = np.asarray(lat1), np.asarray(lat2)
    return np.hypot((lat2 - lat1) * 111.0, (np.asarray(lon2) - np.asarray(lon1)) * 111.0 * np.cos(np.radians((lat1 + lat2) / 2)))


def wind_to_deg(u, v):
    return np.degrees(np.arctan2(u, v)) % 360.0


def _read_nc(path):
    """(times[s since epoch], lats, lons, u10, v10) via h5py (system python) or xarray (venv)"""
    try:
        import h5py
        with h5py.File(path, "r") as f:
            return f["valid_time"][:].astype(np.int64), f["latitude"][:], f["longitude"][:], f["u10"][:], f["v10"][:]
    except ImportError:
        import xarray as xr
        ds = xr.open_dataset(path)
        t = ds["valid_time"].values.astype("datetime64[s]").astype(np.int64)
        return t, ds["latitude"].values, ds["longitude"].values, ds["u10"].values, ds["v10"].values


def build(aoi):
    """Collapse every cached monthly .nc into one npz of daily fields (day mean + pm mean)."""
    files = sorted(glob.glob(os.path.join(ERA5, f"{aoi}_????-??.nc")))
    if not files:
        raise SystemExit(f"no ERA5 files for {aoi} under {ERA5}; run fetch_era5_wind.py first")
    lats = lons = None; days = []; U = []; V = []; Up = []; Vp = []
    for p in files:
        t, la, lo, u, v = _read_nc(p)
        if lats is None: lats, lons = la, lo
        assert np.allclose(la, lats) and np.allclose(lo, lons), p
        d = (t // 86400).astype(np.int64); h = (t % 86400) // 3600
        for dd in np.unique(d):
            m = d == dd; mp = m & np.isin(h, PM_HOURS)
            days.append(int(dd)); U.append(u[m].mean(0)); V.append(v[m].mean(0))
            Up.append(u[mp].mean(0) if mp.any() else u[m].mean(0)); Vp.append(v[mp].mean(0) if mp.any() else v[m].mean(0))
    order = np.argsort(days); days = np.asarray(days)[order]
    out = os.path.join(ERA5, f"{aoi}_wind_daily.npz")
    np.savez_compressed(out, days=days, lats=lats, lons=lons,
                        u_day=np.asarray(U, np.float32)[order], v_day=np.asarray(V, np.float32)[order],
                        u_pm=np.asarray(Up, np.float32)[order], v_pm=np.asarray(Vp, np.float32)[order],
                        sources=np.asarray([os.path.basename(p) for p in files]))
    print(f"[wind] {out}: {len(days)} days, grid {len(lats)}x{len(lons)}, {len(files)} files")
    return out


def ensure(aoi, start, end, months=None, python=None):
    """Fetch the months [start, end] (YYYY-MM) that are not cached yet, then rebuild the npz. Needs cdsapi (venv)."""
    py = python or sys.executable
    cmd = [py, os.path.join(ROOT, "scripts", "fetch_era5_wind.py"), "--aoi", aoi, "--start", start, "--end", end, "--no-summary"]
    if months: cmd += ["--months", ",".join(str(m) for m in months)]
    subprocess.run(cmd, check=True)
    return build(aoi)


class WindField:
    def __init__(self, npz):
        z = np.load(npz, allow_pickle=False)
        self.days = z["days"]; self.lats = z["lats"]; self.lons = z["lons"]
        self.u = {"day": z["u_day"], "pm": z["u_pm"]}; self.v = {"day": z["v_day"], "pm": z["v_pm"]}
        self.day_index = {int(d): i for i, d in enumerate(self.days)}
        self.dlat = float(abs(self.lats[1] - self.lats[0])); self.dlon = float(abs(self.lons[1] - self.lons[0]))
        self.lat0 = float(self.lats[0]); self.lon0 = float(self.lons[0]); self.lat_desc = self.lats[1] < self.lats[0]

    @classmethod
    def load(cls, aoi, rebuild=False):
        npz = os.path.join(ERA5, f"{aoi}_wind_daily.npz")
        ncs = glob.glob(os.path.join(ERA5, f"{aoi}_????-??.nc"))
        if rebuild or not os.path.exists(npz) or (ncs and max(os.path.getmtime(p) for p in ncs) > os.path.getmtime(npz)):
            build(aoi)
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
        ok = (r >= 0) & (r < len(self.lats)) & (c >= 0) & (c < len(self.lons))
        return r, c, ok

    def vec(self, lons, lats, days, which="day"):
        """u, v arrays for point/day pairs; days = iso strings or day numbers; NaN where missing"""
        dn = np.asarray([self.daynum(d) for d in days]) if not isinstance(days, np.ndarray) or days.dtype.kind != "i" else days
        ti = np.asarray([self.day_index.get(int(d), -1) for d in dn]); r, c, ok = self._rc(lons, lats); ok &= ti >= 0
        u = np.full(len(ti), np.nan, np.float32); v = u.copy()
        u[ok] = self.u[which][ti[ok], r[ok], c[ok]]; v[ok] = self.v[which][ti[ok], r[ok], c[ok]]
        return u, v

    def at(self, lon, lat, day, which="day"):
        u, v = self.vec([lon], [lat], [day], which); return float(u[0]), float(v[0])

    def mean_vec(self, lon, lat, day_from, day_to, which="day"):
        """vector-mean wind at one point over days [day_from, day_to) — the days a step was under way"""
        a, b = self.daynum(day_from), self.daynum(day_to)
        if b <= a: b = a + 1
        ds = np.arange(a, b); u, v = self.vec([lon] * len(ds), [lat] * len(ds), ds, which)
        return (np.nanmean(u), np.nanmean(v)) if np.isfinite(u).any() else (np.nan, np.nan)


def steps(traj):
    """daily steps of a fire_groups_v5 trajectory [[lon, lat, 'YYYY-MM-DD', hhmm], ...] ->
    list of (lon_mid, lat_mid, date_from, date_to, bearing_deg, km, gap_days)"""
    out = []
    for p, q in zip(traj, traj[1:]):
        d0, d1 = date.fromisoformat(p[2]), date.fromisoformat(q[2]); gap = max((d1 - d0).days, 1)
        km = float(dist_km(p[0], p[1], q[0], q[1]))
        if km < 1.0: continue                     # a stationary day has no bearing
        out.append(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, p[2], q[2], float(bearing(p[0], p[1], q[0], q[1])), km, gap))
    return out


def annotate_groups(groups, wf, which="day"):
    """Add the ANNOTATION fields to each group dict in place (NaN-free: fields absent when no wind day is covered)."""
    n = 0
    for g in groups:
        t = g.get("trajectory") or []
        if len(t) < 2: continue
        S = steps(t)
        if not S: continue
        us, vs = [], []
        for lo, la, d0, d1, b, km, gap in S:
            u, v = wf.mean_vec(lo, la, d0, d1, which); us.append(u); vs.append(v)
        us, vs = np.asarray(us), np.asarray(vs); ok = np.isfinite(us) & np.isfinite(vs)
        if not ok.any(): continue
        b = np.asarray([s[4] for s in S])[ok]; wto = wind_to_deg(us[ok], vs[ok]); d = ang_diff(b, wto)
        g["wind_steps"] = int(ok.sum()); g["wind_downwind_share"] = round(float((np.abs(d) <= 45).mean()), 3)
        g["wind_upwind_share"] = round(float((np.abs(d) >= 135).mean()), 3)
        g["wind_align_deg"] = round(float(np.degrees(np.arctan2(np.sin(np.radians(d)).mean(), np.cos(np.radians(d)).mean()))), 1)
        g["wind_speed_ms"] = round(float(np.hypot(us[ok], vs[ok]).mean()), 2); n += 1
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="XSA_Study_Area")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--ensure", nargs=2, metavar=("START", "END"), help="YYYY-MM YYYY-MM")
    ap.add_argument("--months", help="calendar months to keep, e.g. 10,11,12,1,2,3")
    a = ap.parse_args()
    if a.ensure:
        ensure(a.aoi, a.ensure[0], a.ensure[1], [int(m) for m in a.months.split(",")] if a.months else None)
    elif a.build:
        build(a.aoi)
    else:
        wf = WindField.load(a.aoi); print("covered", wf.covered(), "days", len(wf.days), "grid", len(wf.lats), "x", len(wf.lons))
