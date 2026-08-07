"""Graticule refinement for scanned thematic maps, shared by both geology sheets.

Same principle as scripts/histmaps/grat.py: georeference to the map's *own*
printed graticule rather than by eye against modern data, so the result is
reproducible and the residuals are measurable.  What differs here is the ink:
the 1:250k line sheets are black-on-cream with a ruled grid, while these two
geology sheets print the graticule as a **thin dark line over saturated colour
fill**.  A global threshold sees the polygon edges, not the grid.

So the detector is local: at a predicted crossing, take a strip, subtract a 1-D
background (a wide Gaussian along the profile) and look for one narrow dark
excursion inside a tight window.  A crossing is accepted only if that excursion
stands clear of the local noise; otherwise it is dropped, never faked -- the
report counts measured points, so a sheet that is mostly interpolated cannot
pass as a measured one.

The model is fitted iteratively: predict -> measure -> robust polynomial fit ->
re-predict with a tighter window.  A polygon edge that happens to run parallel
to a meridian survives round 0 and falls out on round 1.
"""
import numpy as np, cv2


def _vote_line(d, half, min_amp=2.0, min_snr=3.0, min_frac=0.10):
    """Sub-pixel offset of a *continuous straight* dark line in a 2-D strip.

    `d` is the background-subtracted strip, one row (or column) per sample along
    the line, so ink is positive.  Each sample votes for its own darkest offset
    if that offset is locally significant; the winner is the offset that carries
    the most votes.  Voting rather than averaging is the whole point: a
    geological contact is dark too, but it wanders, so its votes scatter across
    the window while the ruled graticule stacks its votes on one column.

    Returns (offset, votes/samples) or None if no offset carries `min_frac`
    of the samples -- i.e. the sheet does not print that line here.
    """
    n = d.shape[0]
    votes = np.zeros(d.shape[1], np.float32)
    for r in range(n):
        row = d[r]
        i = int(np.argmax(row))
        noise = float(np.median(np.abs(row - np.median(row)))) * 1.4826 + 1e-6
        if row[i] >= max(min_amp, min_snr * noise):
            votes[i] += 1.0
    v = cv2.GaussianBlur(votes.reshape(-1, 1), (1, 5), 0).ravel()
    i = int(np.argmax(v))
    frac = float(v[i]) / n
    if frac < min_frac or abs(i - half) > half * 0.85:
        return None
    lo, hi = max(0, i - 3), min(len(v), i + 4)
    w = v[lo:hi]
    if w.sum() <= 0:
        return None
    return float(np.dot(np.arange(lo, hi), w) / w.sum()), frac


class GridReader:
    """Measures graticule lines in a scanned map, in full-resolution pixels."""

    def __init__(self, src, half=60, strip=400, blur=41):
        self.src = src
        self.half = half        # search window around the prediction, px
        self.strip = strip      # how far along the line we look for continuity, px
        self.blur = blur | 1    # background scale for the 1-D high-pass

    def _gray(self, x0, y0, w, h):
        W, H = self.src.width, self.src.height
        if x0 < 0 or y0 < 0 or x0 + w > W or y0 + h > H:
            return None
        a = self.src.read(window=((y0, y0 + h), (x0, x0 + w)))
        if a.shape[0] >= 3:
            return cv2.cvtColor(np.transpose(a[:3], (1, 2, 0)), cv2.COLOR_RGB2GRAY).astype(np.float32)
        return a[0].astype(np.float32)

    def meridian_x(self, cx, cy, half=None):
        half = half or self.half
        g = self._gray(int(cx) - half, int(cy) - self.strip, 2 * half, 2 * self.strip)
        if g is None:
            return None
        d = cv2.GaussianBlur(g, (self.blur, 1), 0) - g          # high-pass across x
        r = _vote_line(d, half)
        return (int(cx) - half + r[0], r[1]) if r else None

    def parallel_y(self, cx, cy, half=None):
        half = half or self.half
        g = self._gray(int(cx) - self.strip, int(cy) - half, 2 * self.strip, 2 * half)
        if g is None:
            return None
        d = cv2.GaussianBlur(g, (1, self.blur), 0) - g          # high-pass across y
        r = _vote_line(d.T, half)                               # transpose: rows = samples
        return (int(cy) - half + r[0], r[1]) if r else None


# ---------------------------------------------------------------- polynomial
def _design(lon, lat, deg):
    cols = [np.ones_like(lon)]
    for d in range(1, deg + 1):
        for k in range(d + 1):
            cols.append((lon ** (d - k)) * (lat ** k))
    return np.stack(cols, 1)


class PolyModel:
    """(lon, lat) -> (x, y), least squares with iterative outlier trimming.

    Degree 2 is enough for a conic sheet this size: it absorbs meridian
    convergence and the scanner's slight keystone, and unlike a TPS it cannot
    chase a mis-measured point -- which is exactly what we want *while fitting*.
    The TPS comes later, in gdalwarp, over points this model has already vetted.
    """

    def __init__(self, deg=2):
        self.deg = deg
        self.cx = self.cy = None

    def fit(self, lon, lat, x, y, trim=3.0, iters=4):
        lon, lat, x, y = (np.asarray(v, float) for v in (lon, lat, x, y))
        keep = np.ones(len(lon), bool)
        nparam = _design(lon[:1], lat[:1], self.deg).shape[1]
        for _ in range(iters):
            A = _design(lon[keep], lat[keep], self.deg)
            self.cx = np.linalg.lstsq(A, x[keep], rcond=None)[0]
            self.cy = np.linalg.lstsq(A, y[keep], rcond=None)[0]
            px, py = self.predict(lon, lat)
            r = np.hypot(px - x, py - y)
            s = np.median(r[keep]) * 1.4826 + 1e-6
            nk = r < max(trim * s, 3.0)
            if nk.sum() < nparam + 3 or (nk == keep).all():
                break
            keep = nk
        self.keep = keep
        px, py = self.predict(lon, lat)
        self.resid = np.hypot(px - x, py - y)
        return self

    def predict(self, lon, lat):
        A = _design(np.asarray(lon, float), np.asarray(lat, float), self.deg)
        return A @ self.cx, A @ self.cy


def measure_grid(src, model, lons, lats, rounds=((90, 4.0), (40, 3.0), (20, 2.5)),
                 strip=400, log=print):
    """Iteratively measure every (lon, lat) crossing the sheet actually prints.

    Returns (gcps, model, stats); gcps is [(x, y, lon, lat), ...] in full-res px.
    """
    gcps = []
    for rnd, (half, trim) in enumerate(rounds):
        rd = GridReader(src, half=half, strip=strip)
        obs = []
        for lo in lons:
            px_all, py_all = model.predict([lo] * len(lats), list(lats))
            for la, px, py in zip(lats, px_all, py_all):
                vx = rd.meridian_x(px, py)
                vy = rd.parallel_y(px, py)
                if vx and vy:
                    obs.append((lo, la, vx[0], vy[0]))
        if len(obs) < 8:
            raise RuntimeError(f"only {len(obs)} graticule crossings found")
        a = np.array(obs, float)
        model = PolyModel(model.deg).fit(a[:, 0], a[:, 1], a[:, 2], a[:, 3], trim=trim)
        keep = model.keep
        log(f"  round {rnd}: window +-{half}px  measured {len(obs)}  kept {int(keep.sum())}"
            f"  rms {np.sqrt((model.resid[keep]**2).mean()):.2f}px"
            f"  max {model.resid[keep].max():.2f}px")
        gcps = [(x, y, lo, la) for (lo, la, x, y), k in zip(obs, keep) if k]
    stats = dict(n_gcps=len(gcps),
                 rms_px=float(np.sqrt((model.resid[model.keep] ** 2).mean())),
                 max_px=float(model.resid[model.keep].max()))
    return gcps, model, stats
