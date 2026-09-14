#!/usr/bin/env python3
"""
fire_front.py — the SEASON FRONT, and how far ahead of it a fire ran.

WHAT IT MEASURES (plain words)

Every dry season the burning arrives somewhere first and sweeps across the
land over the following weeks. For each 2.5 km cell we record the date by
which A FIFTH OF THE LAND THAT USUALLY BURNS within ~60 km had burned. That
date is the SEASON FRONT at that cell. Drawn as contours, the front is a set
of "the season had arrived here by …" lines that sweep across an area — on
the XSA from the east in November to the west in January, the same shape
every year.

A fire's LEAD is how many days before the front reached its cell it burned.
A fire with a lead of 20 days burned in country the season had not yet
reached — the sky around it was dark, the grass unburnt. Ahead of the front
the field is sparse enough that the tracker's day-to-day links carry real
information (skill 0.42–0.51 against the day-shuffled null, for every lead
threshold 5…25 days — scripts/fire_vanguard/README.md); inside the season
they do not (skill ~0). A trajectory that BEGAN at least VANGUARD_LEAD_DAYS
ahead of the front is a VANGUARD: what field staff describe as young men
laying fire along the migration route before the herds and the season
arrive.

WHY THIS DEFINITION

* Causal. "A fifth of the burnable land has burned" is known on the day it
  happens, so the same rule serves the finished archive and this morning's
  detections; nothing is re-labelled when the season ends.
* The denominator is land that has burned in ANY season we hold for the
  area (its burnable footprint), not all land: a window that is half
  rainforest would otherwise never reach a fifth, and a window on the edge
  of the data would count unobserved land as unburnt. Where fewer than
  MIN_BURNABLE_CELLS burnable cells lie in the window there is no front.
* Where this season's front has NOT (yet) reached a cell, the USUAL front
  — the median day-of-season over previous complete seasons — stands in,
  and the lead says so (`lead_basis: 'usual'`). That is what a live
  notification needs ("burning three weeks before the season usually
  reaches this area"); the measured front replaces it as the season
  arrives.
* The season starts in the area's quietest month (the trough of its monthly
  climatology), measured, never typed: the XSA burns Nov–Mar and is quiet in
  July/August; Zambia burns Jun–Oct.

WHAT IT WRITES

`fire_season_front` (db/migrations/066): one row per (area, season) with the
front grid (int16 day-of-season, -1 = no front), the usual-front grid, the
isochrone contours as GeoJSON (every CONTOUR_STEP_DAYS days) and a stats
blob. `tag_groups()` adds to each trajectory group: season, lead_start,
lead_basis, lead_max, leads (per vertex), ahead_km, ahead_days, vanguard.

USAGE

    python3 scripts/fire_front.py --area XSA_Study_Area          # all seasons
    python3 scripts/fire_front.py --area CAF_Chinko --current    # live season only
    python3 scripts/fire_front.py --all [--current]              # every park + AOI
    python3 scripts/fire_front.py --area XSA_Study_Area --tag    # also re-tag the
                                                                 # group JSON in place
"""
import argparse
import json
import math
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from fire_source import DB_PATH  # noqa: E402

BASE_DIR = Path(__file__).parent.parent
GROUPS_DIR = BASE_DIR / "data" / "fire_groups_v5"

RES_DEG = 0.025            # cell size (~2.8 km)
WINDOW_KM = 60.0           # half-width of the box the front is measured over
FRONT_FRACTION = 0.20      # "a fifth of the burnable land"
MIN_BURNABLE_CELLS = 40    # fewer burnable cells in the window -> no front
SMOOTH_SIGMA_CELLS = 3.0   # takes the staircase out of the day-by-day sweep
VANGUARD_LEAD_DAYS = 10    # a group that began this far ahead is a vanguard
# ...and no further ahead than this. Measured on XSA 2024/25 + 2025/26
# (eval_fire_vanguard bands): link skill 0.54-0.58 at lead 10-30 d, 0.36-0.42
# at 30-60 d, weak and on a few hundred detections beyond. A fire 190 days
# ahead of a July front is a wet-season fire, not a scout (AGO_Luengue-Luiana,
# January), and would have been the top-ranked "vanguard" of the park.
VANGUARD_LEAD_MAX = 60
CONTOUR_STEP_DAYS = 5
LABEL_STEP_DAYS = 15
COMPLETE_AFTER_DAYS = 330  # a season with data this far in is complete
MIN_SEASON_DETECTIONS = 200


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Seasons
# ---------------------------------------------------------------------------

def season_start_month(dates_by_month):
    """The quietest month of the area's climatology. `dates_by_month` maps
    'YYYY-MM' -> detections. Ties go to the earlier month."""
    clim = np.zeros(12)
    for ym, n in dates_by_month.items():
        clim[int(ym[5:7]) - 1] += n
    # A 3-month circular mean so one odd month does not pick the trough.
    sm = np.array([clim[(i - 1) % 12] + clim[i] + clim[(i + 1) % 12] for i in range(12)])
    return int(np.argmin(sm)) + 1


def season_of(d, start_month):
    """'2024/25' for a date in the season starting `start_month` 2024.
    With start_month == 1 the label is the plain year."""
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    y = d.year if d.month >= start_month else d.year - 1
    return season_label(y, start_month)


def season_label(y, start_month):
    return str(y) if start_month == 1 else f"{y}/{(y + 1) % 100:02d}"


def season_bounds(label, start_month):
    y = int(label[:4])
    s = date(y, start_month, 1)
    e = date(y + 1, start_month, 1) - timedelta(days=1) if start_month > 1 else date(y, 12, 31)
    return s, e


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def area_kind(conn, area_id):
    if conn.execute("SELECT 1 FROM aois WHERE id=?", (area_id,)).fetchone():
        return "aoi"
    return "park"


def load_points(conn, area_id):
    """lon, lat, day (ordinal) arrays for every detection of the area."""
    if area_kind(conn, area_id) == "aoi":
        sql = ("SELECT f.longitude, f.latitude, f.acq_date FROM aoi_fires a "
               "JOIN fire_detections f ON f.id = a.fire_id WHERE a.aoi_id = ?")
    else:
        sql = ("SELECT longitude, latitude, acq_date FROM fire_detections "
               "WHERE protected_area_id = ?")
    lon, lat, day = [], [], []
    cache = {}
    for x, y, d in conn.execute(sql, (area_id,)):
        if not x or not y:
            continue
        o = cache.get(d)
        if o is None:
            o = date.fromisoformat(d).toordinal()
            cache[d] = o
        lon.append(x); lat.append(y); day.append(o)
    return (np.asarray(lon, dtype=np.float64), np.asarray(lat, dtype=np.float64),
            np.asarray(day, dtype=np.int64))


# ---------------------------------------------------------------------------
# The front
# ---------------------------------------------------------------------------

class Grid:
    def __init__(self, x0, y0, nx, ny, res=RES_DEG):
        self.x0, self.y0, self.nx, self.ny, self.res = x0, y0, nx, ny, res

    @classmethod
    def around(cls, lon, lat, res=RES_DEG, pad_deg=0.05):
        x0 = math.floor((lon.min() - pad_deg) / res) * res
        y0 = math.floor((lat.min() - pad_deg) / res) * res
        nx = int(math.ceil((lon.max() + pad_deg - x0) / res)) + 1
        ny = int(math.ceil((lat.max() + pad_deg - y0) / res)) + 1
        return cls(x0, y0, nx, ny, res)

    def index(self, lon, lat):
        ix = np.clip(((np.asarray(lon) - self.x0) / self.res).astype(int), 0, self.nx - 1)
        iy = np.clip(((np.asarray(lat) - self.y0) / self.res).astype(int), 0, self.ny - 1)
        return iy, ix

    def meta(self):
        return dict(res=self.res, x0=self.x0, y0=self.y0, nx=self.nx, ny=self.ny)


def onset_grid(grid, lon, lat, dos):
    """First burn day-of-season per cell (NaN where nothing burned)."""
    onset = np.full((grid.ny, grid.nx), np.inf)
    iy, ix = grid.index(lon, lat)
    np.minimum.at(onset, (iy, ix), dos.astype(float))
    onset[~np.isfinite(onset)] = np.nan
    return onset


def front_grid(onset, burnable, lat_mid):
    """Day-of-season at which FRONT_FRACTION of the burnable cells in the
    window around each cell had burned. Causal: the day-by-day sweep only
    ever looks backwards. NaN where the window holds too little burnable
    land or the fraction is never reached."""
    from scipy import ndimage
    km_per_cell_y = 111.0 * RES_DEG
    km_per_cell_x = 111.0 * math.cos(math.radians(lat_mid)) * RES_DEG
    size = (2 * int(round(WINDOW_KM / km_per_cell_y)) + 1,
            2 * int(round(WINDOW_KM / km_per_cell_x)) + 1)
    denom = ndimage.uniform_filter(burnable.astype(float), size=size, mode="constant") \
        * size[0] * size[1]
    ok = denom >= MIN_BURNABLE_CELLS
    front = np.full(onset.shape, np.nan)
    if not ok.any():
        return front
    burned_days = onset[np.isfinite(onset)]
    if burned_days.size == 0:
        return front
    d_lo, d_hi = int(burned_days.min()), int(burned_days.max())
    pending = ok.copy()
    for t in range(d_lo, d_hi + 1):
        burned = (onset <= t)
        num = ndimage.uniform_filter(burned.astype(float), size=size, mode="constant") \
            * size[0] * size[1]
        frac = np.where(denom > 0, num / np.maximum(denom, 1), 0.0)
        hit = pending & (frac >= FRONT_FRACTION)
        if hit.any():
            front[hit] = t
            pending &= ~hit
            if not pending.any():
                break
    # Smooth only where defined (normalised convolution), so the staircase
    # of the day steps goes without bleeding NaN inwards.
    if SMOOTH_SIGMA_CELLS > 0:
        w = np.isfinite(front).astype(float)
        f = np.where(w > 0, front, 0.0)
        num = ndimage.gaussian_filter(f, SMOOTH_SIGMA_CELLS)
        den = ndimage.gaussian_filter(w, SMOOTH_SIGMA_CELLS)
        sm = np.where(den > 0.3, num / np.maximum(den, 1e-9), np.nan)
        front = np.where(np.isfinite(front), sm, np.nan)
    return front


# ---------------------------------------------------------------------------
# Contours
# ---------------------------------------------------------------------------

def contours(grid, front, season_start, step=CONTOUR_STEP_DAYS):
    """Isochrones of the front as GeoJSON features, one per level."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fin = front[np.isfinite(front)]
    if fin.size < 10:
        return []
    lo = int(math.floor(fin.min() / step) * step)
    hi = int(math.ceil(fin.max() / step) * step)
    levels = list(range(lo, hi + 1, step))
    if len(levels) < 2:
        return []
    xs = grid.x0 + (np.arange(grid.nx) + 0.5) * grid.res
    ys = grid.y0 + (np.arange(grid.ny) + 0.5) * grid.res
    fig = plt.figure()
    ax = fig.add_subplot(111)
    try:
        cs = ax.contour(xs, ys, np.ma.masked_invalid(front), levels=levels)
        feats = []
        # matplotlib >= 3.8 exposes one path per level; older versions allsegs.
        segs_by_level = getattr(cs, "allsegs", None)
        if segs_by_level is None:
            segs_by_level = []
            for p in cs.get_paths():
                segs_by_level.append([np.asarray(s) for s in p.to_polygons(closed_only=False)])
        for lvl, segs in zip(cs.levels, segs_by_level):
            lines = []
            for s in segs:
                s = np.asarray(s)
                if len(s) < 3:
                    continue
                # Thin to ~1/4 cell: the contour is smooth, the vertices are not
                # information.
                keep = [0]
                for i in range(1, len(s)):
                    if abs(s[i, 0] - s[keep[-1], 0]) + abs(s[i, 1] - s[keep[-1], 1]) >= grid.res * 0.25:
                        keep.append(i)
                if keep[-1] != len(s) - 1:
                    keep.append(len(s) - 1)
                pts = [[round(float(x), 4), round(float(y), 4)] for x, y in s[keep]]
                if len(pts) >= 2:
                    lines.append(pts)
            if not lines:
                continue
            dos = int(round(lvl))
            d = season_start + timedelta(days=dos)
            feats.append({
                "type": "Feature",
                "geometry": {"type": "MultiLineString", "coordinates": lines},
                "properties": {"dos": dos, "date": d.isoformat(),
                                "label": dos % LABEL_STEP_DAYS == 0,
                                "text": d.strftime("%-d %b")},
            })
        return feats
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# Build + store
# ---------------------------------------------------------------------------

def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS fire_season_front (
        area_id TEXT NOT NULL, season TEXT NOT NULL,
        season_start TEXT NOT NULL, season_end TEXT NOT NULL,
        start_month INTEGER NOT NULL,
        complete INTEGER NOT NULL, latest_day TEXT,
        res REAL NOT NULL, x0 REAL NOT NULL, y0 REAL NOT NULL,
        nx INTEGER NOT NULL, ny INTEGER NOT NULL,
        front BLOB, usual BLOB, contours_json TEXT, stats_json TEXT,
        computed_at TEXT NOT NULL,
        PRIMARY KEY (area_id, season))""")


def pack(g):
    a = np.where(np.isfinite(g), np.round(g), -1).astype("<i2")
    return a.tobytes()


def unpack(blob, ny, nx):
    a = np.frombuffer(blob, dtype="<i2").reshape(ny, nx).astype(float)
    a[a < 0] = np.nan
    return a


def build_area(conn, area_id, current_only=False, verbose=True):
    """Compute and store the front for every season of the area (or only the
    season containing the latest detection). Returns the season labels
    written."""
    t0 = time.time()
    lon, lat, day = load_points(conn, area_id)
    if lon.size < MIN_SEASON_DETECTIONS:
        if verbose:
            log(f"{area_id}: {lon.size} detections — too few for a season front")
        return []
    dates = np.array([date.fromordinal(int(d)) for d in np.unique(day)])
    by_month = {}
    # detections per month for the climatology
    ord_months = {}
    for o in np.unique(day):
        d = date.fromordinal(int(o))
        ord_months[int(o)] = f"{d.year:04d}-{d.month:02d}"
    months = np.vectorize(ord_months.get)(day)
    um, cnt = np.unique(months, return_counts=True)
    by_month = dict(zip(um.tolist(), cnt.tolist()))
    sm = season_start_month(by_month)
    latest = date.fromordinal(int(day.max()))
    seasons = sorted({season_of(d, sm) for d in dates})
    grid = Grid.around(lon, lat)
    lat_mid = float((lat.min() + lat.max()) / 2)

    # Burnable footprint: every cell that burned in any season we hold.
    burnable = np.zeros((grid.ny, grid.nx), bool)
    iy, ix = grid.index(lon, lat)
    burnable[iy, ix] = True

    ensure_table(conn)
    # Usual front needs the previous complete seasons: process in order and
    # keep the completed fronts in hand.
    completed = []
    existing = {r[0]: r for r in conn.execute(
        "SELECT season, complete, front FROM fire_season_front WHERE area_id=?", (area_id,))}
    written = []
    for s in seasons:
        s0, s1 = season_bounds(s, sm)
        complete = (latest - s0).days >= COMPLETE_AFTER_DAYS
        if current_only and s != season_of(latest, sm):
            # Reuse the stored complete front for the usual-front stack.
            row = existing.get(s)
            if row and row[1]:
                completed.append(unpack(row[2], grid.ny, grid.nx) if len(row[2]) == grid.ny * grid.nx * 2 else None)
            continue
        m = (day >= s0.toordinal()) & (day <= s1.toordinal())
        if m.sum() < MIN_SEASON_DETECTIONS:
            continue
        # A season whose data begins after the season did cannot show where
        # the front started: the first day we hold would masquerade as the
        # front everywhere (XSA's AOI data begins 2024-01-01, mid-season).
        if date.fromordinal(int(day.min())) > s0 + timedelta(days=15):
            if verbose:
                log(f"{area_id} {s}: data begins {date.fromordinal(int(day.min()))}, "
                    f"after the season start — skipped (truncated)")
            continue
        dos = day[m] - s0.toordinal()
        onset = onset_grid(grid, lon[m], lat[m], dos)
        front = front_grid(onset, burnable, lat_mid)
        prev = [f for f in completed if f is not None]
        if prev:
            with np.errstate(all="ignore"):
                import warnings as _w
                with _w.catch_warnings():
                    _w.simplefilter("ignore")
                    usual = np.nanmedian(np.stack(prev), axis=0)
        else:
            usual = np.full(front.shape, np.nan)
        feats = contours(grid, front, s0)
        fin = front[np.isfinite(front)]
        stats = {
            "detections": int(m.sum()),
            "cells_burned": int(np.isfinite(onset).sum()),
            "cells_with_front": int(fin.size),
            "front_first": (s0 + timedelta(days=int(fin.min()))).isoformat() if fin.size else None,
            "front_median": (s0 + timedelta(days=int(np.median(fin)))).isoformat() if fin.size else None,
            "front_last": (s0 + timedelta(days=int(fin.max()))).isoformat() if fin.size else None,
            "usual_seasons": len(prev),
            "window_km": WINDOW_KM, "fraction": FRONT_FRACTION,
        }
        conn.execute("""INSERT OR REPLACE INTO fire_season_front
            (area_id, season, season_start, season_end, start_month, complete, latest_day,
             res, x0, y0, nx, ny, front, usual, contours_json, stats_json, computed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (area_id, s, s0.isoformat(), s1.isoformat(), sm, int(complete),
                      min(latest, s1).isoformat(), grid.res, grid.x0, grid.y0, grid.nx, grid.ny,
                      pack(front), pack(usual), json.dumps(feats, separators=(",", ":")),
                      json.dumps(stats), datetime.now().astimezone().isoformat(timespec="seconds")))
        conn.commit()
        written.append(s)
        if complete:
            completed.append(front)
        if verbose:
            log(f"{area_id} {s}{'' if complete else ' (live)'}: {stats['detections']:,} det, "
                f"front on {stats['cells_with_front']:,} cells ({stats['cells_burned']:,} burned), "
                f"{stats['front_first']} → {stats['front_last']}, {len(feats)} contours")
    if verbose:
        log(f"{area_id}: season starts month {sm}; {len(written)} season(s) in {time.time() - t0:.1f}s")
    return written


# ---------------------------------------------------------------------------
# Lead lookup + group tagging
# ---------------------------------------------------------------------------

class FrontSet:
    """All stored seasons of one area, for lead lookups."""

    def __init__(self, conn, area_id):
        self.area_id = area_id
        self.seasons = {}
        self.start_month = None
        for row in conn.execute(
                "SELECT season, season_start, start_month, complete, latest_day, res, x0, y0, nx, ny, "
                "front, usual FROM fire_season_front WHERE area_id=?", (area_id,)):
            s, s0, sm, complete, latest, res, x0, y0, nx, ny, fb, ub = row
            g = Grid(x0, y0, nx, ny, res)
            self.seasons[s] = dict(start=date.fromisoformat(s0), complete=bool(complete),
                                   latest=date.fromisoformat(latest) if latest else None,
                                   grid=g, front=unpack(fb, ny, nx), usual=unpack(ub, ny, nx))
            self.start_month = sm

    def __bool__(self):
        return bool(self.seasons)

    def lead(self, lon, lat, d):
        """(lead_days, basis) for a point burning on date `d` ('YYYY-MM-DD').
        basis 'front' = this season's measured front, 'usual' = the median of
        previous seasons where this season's front has not arrived (yet),
        None = nothing to measure against."""
        if not self.seasons:
            return None, None
        s = season_of(d, self.start_month)
        S = self.seasons.get(s)
        if S is None:
            return None, None
        iy, ix = S["grid"].index([lon], [lat])
        dos = (date.fromisoformat(d[:10]) - S["start"]).days
        f = S["front"][iy[0], ix[0]]
        if np.isfinite(f):
            return int(round(f - dos)), "front"
        # No measured front here. In a finished season that means the
        # season never came to this cell: nothing to be ahead of. Live, the
        # usual front stands in.
        if S["complete"]:
            return None, None
        u = S["usual"][iy[0], ix[0]]
        if np.isfinite(u):
            return int(round(u - dos)), "usual"
        return None, None

    def lead_array(self, lon, lat, dates):
        """Vectorised lead(): float array (NaN = nothing to measure against)
        for detections of ONE season. Same rule as lead()."""
        lon = np.asarray(lon, float); lat = np.asarray(lat, float)
        out = np.full(lon.shape, np.nan)
        if not self.seasons or len(lon) == 0:
            return out
        d0 = np.array([date.fromisoformat(d[:10]).toordinal() for d in dates])
        seasons = np.array([season_of(d, self.start_month) for d in dates])
        for s in np.unique(seasons):
            S = self.seasons.get(s)
            if S is None:
                continue
            m = seasons == s
            iy, ix = S["grid"].index(lon[m], lat[m])
            dos = d0[m] - S["start"].toordinal()
            f = S["front"][iy, ix]
            v = f - dos
            if not S["complete"]:
                u = S["usual"][iy, ix] - dos
                v = np.where(np.isfinite(v), v, u)
            out[m] = v
        return out

    def season_for(self, d):
        return season_of(d, self.start_month) if self.start_month else None


def _hav_km(lon1, lat1, lon2, lat2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def tag_group(g, fs):
    """Add season/lead fields to one group dict (in place). Idempotent."""
    traj = g.get("trajectory") or []
    leads, basis = [], None
    for p in traj:
        if len(p) < 3 or not p[2]:
            leads.append(None)
            continue
        l, b = fs.lead(p[0], p[1], str(p[2]))
        leads.append(l)
        if b and basis is None:
            basis = b
        elif b == "front":
            basis = "front" if basis in (None, "front") else "mixed"
    g["season"] = fs.season_for(g.get("start_date", "")) if g.get("start_date") else None
    known = [l for l in leads if l is not None]
    if not known:
        g["lead_start"] = None
        g["lead_basis"] = None
        g["lead_max"] = None
        g["leads"] = None
        g["ahead_km"] = 0.0
        g["ahead_days"] = 0
        g["vanguard"] = False
        return g
    first = next(l for l in leads if l is not None)
    g["lead_start"] = first
    g["lead_basis"] = basis
    g["lead_max"] = max(known)
    g["leads"] = leads
    # The part of the path burned ahead of the season: length and duration.
    km = 0.0
    ahead_dates = set()
    for i, p in enumerate(traj):
        if leads[i] is not None and leads[i] >= 0:
            ahead_dates.add(str(p[2])[:10])
            if i + 1 < len(traj) and leads[i + 1] is not None and leads[i + 1] >= 0:
                km += _hav_km(p[0], p[1], traj[i + 1][0], traj[i + 1][1])
    g["ahead_km"] = round(km, 1)
    g["ahead_days"] = len(ahead_dates)
    g["vanguard"] = bool(VANGUARD_LEAD_DAYS <= first <= VANGUARD_LEAD_MAX)
    return g


def tag_groups(groups, area_id, conn=None):
    own = conn is None
    if own:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        ensure_table_ro_safe(conn)
        fs = FrontSet(conn, area_id)
    finally:
        if own:
            conn.close()
    if not fs:
        return 0
    n = 0
    for g in groups:
        tag_group(g, fs)
        n += bool(g.get("vanguard"))
    return n


def ensure_table_ro_safe(conn):
    """A read-only connection cannot create the table; a missing table simply
    means no fronts yet."""
    try:
        conn.execute("SELECT 1 FROM fire_season_front LIMIT 1")
    except sqlite3.OperationalError:
        pass


def tag_file(area_id, conn=None):
    """Re-tag data/fire_groups_v5/{area}.json in place. Returns
    (groups, vanguards) or None when the file is absent."""
    p = GROUPS_DIR / f"{area_id}.json"
    if not p.exists():
        return None
    groups = json.loads(p.read_text())
    n = tag_groups(groups, area_id, conn)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(groups))
    tmp.replace(p)
    if conn is not None:
        patch_feature_properties(conn, area_id, groups)
    return len(groups), n


LEAD_PROPS = ("fire_season", "lead_start", "lead_basis", "lead_max", "leads",
              "ahead_km", "ahead_days", "vanguard")


def patch_feature_properties(conn, area_id, groups, batch=500):
    """Carry the lead fields of already-loaded trajectories into
    feature_geometries.properties_json (what /api/features-in-bbox and the
    popups read) without a full load_fire_groups_to_db run. Scoped UPDATE by
    feature_id; batched commits so the single writer stays available."""
    have = {r[0] for r in conn.execute(
        "SELECT feature_id FROM feature_geometries WHERE feature_type='fire_trajectory' AND park_id=?",
        (area_id,))}
    todo = [g for g in groups if g.get("feature_id") in have and "lead_start" in g]
    n = 0
    for i in range(0, len(todo), batch):
        for g in todo[i:i + batch]:
            props = {"fire_season": g.get("season"), "lead_start": g.get("lead_start"),
                     "lead_basis": g.get("lead_basis"), "lead_max": g.get("lead_max"),
                     "leads": g.get("leads"), "ahead_km": g.get("ahead_km"),
                     "ahead_days": g.get("ahead_days"), "vanguard": bool(g.get("vanguard"))}
            conn.execute(
                "UPDATE feature_geometries SET properties_json = json_patch(COALESCE(properties_json,'{}'), ?), "
                "lead_start = ?, vanguard = ? WHERE feature_type='fire_trajectory' AND feature_id=?",
                (json.dumps(props), g.get("lead_start"), 1 if g.get("vanguard") else 0, g["feature_id"]))
            n += 1
        conn.commit()
        time.sleep(0.05)
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def all_area_ids(conn):
    parks = [r[0] for r in conn.execute(
        "SELECT DISTINCT protected_area_id FROM fire_detections WHERE protected_area_id IS NOT NULL")]
    aois = [r[0] for r in conn.execute("SELECT id FROM aois WHERE archived_at IS NULL")]
    return sorted(set(parks) | set(aois))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--area", help="park id or AOI id")
    ap.add_argument("--areas", help="comma-separated ids")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--current", action="store_true", help="only the season holding the latest detection")
    ap.add_argument("--tag", action="store_true", help="also re-tag data/fire_groups_v5/{area}.json")
    ap.add_argument("--rotate", type=int, metavar="N",
                    help="build the N areas whose front is oldest / missing (cron catch-up)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    ensure_table(conn)
    if a.all:
        ids = all_area_ids(conn)
    elif a.areas:
        ids = [s.strip() for s in a.areas.split(",") if s.strip()]
    elif a.area:
        ids = [a.area]
    elif a.rotate:
        have = {r[0]: r[1] for r in conn.execute(
            "SELECT area_id, MAX(computed_at) FROM fire_season_front GROUP BY area_id")}
        ids = sorted(all_area_ids(conn), key=lambda i: have.get(i, ""))[:a.rotate]
    else:
        ap.error("--area, --areas, --all or --rotate N")
    done, failed, tagged = 0, [], 0
    t0 = time.time()
    for i, aid in enumerate(ids):
        try:
            written = build_area(conn, aid, current_only=a.current, verbose=not a.quiet)
            if a.tag or a.rotate:
                r = tag_file(aid, conn)
                if r:
                    tagged += r[1]
                    if not a.quiet:
                        log(f"{aid}: tagged {r[0]:,} groups, {r[1]:,} vanguard")
            done += bool(written)
        except Exception as ex:  # one bad area must not stop the sweep
            log(f"{aid}: FAILED {ex!r}")
            failed.append(aid)
    log(f"done: {done}/{len(ids)} areas")
    if a.rotate:
        # Cron status into the bell (docs/agents/ops.md): a no-op still reports.
        from cron_notify import notify_status
        n_have = conn.execute("SELECT COUNT(DISTINCT area_id) FROM fire_season_front").fetchone()[0]
        n_all = len(all_area_ids(conn))
        msg = (f"{done}/{len(ids)} areas rebuilt, {tagged:,} vanguard trajectories tagged, "
               f"{time.time() - t0:.0f}s; fronts held for {n_have}/{n_all} areas")
        if failed:
            notify_status("fire_front_failed", "Fire Season Front", msg + f"; failed: {', '.join(failed[:8])}")
        else:
            notify_status("fire_front_success", "Fire Season Front", msg)


if __name__ == "__main__":
    main()
