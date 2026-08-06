"""Two-tier graticule detection for Sudan Survey 1:250k sheets.

Tier 1 (neatline): the sheet border is the strongest full-width/full-height ink
run.  We find the outer rectangle and sanity-check its aspect ratio against the
expected 1.5deg x 1.0deg cell at that latitude.

Tier 2 (interior): later editions draw the 15' graticule as short crosses/ticks
rather than ruled lines, so a global threshold misses them.  Instead we predict
each rung from the neatline and refine it locally against an ink profile
computed *inside* the neatline only.  A rung is accepted as a GCP only if a
local peak is actually found; otherwise the linear prediction is used and the
point is flagged, so the report distinguishes measured from interpolated.
"""
import numpy as np, cv2

def ink(g):
    return cv2.adaptiveThreshold(g,255,cv2.ADAPTIVE_THRESH_MEAN_C,
                                 cv2.THRESH_BINARY_INV,51,15)

def _runs(thr, axis):
    """Profile of long straight ink runs along `axis` (0=horiz lines,1=vert)."""
    h,w = thr.shape
    if axis==0:
        k = cv2.getStructuringElement(cv2.MORPH_RECT,(max(80,w//8),1))
        return cv2.morphologyEx(thr,cv2.MORPH_OPEN,k).sum(1).astype(float)
    k = cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(80,h//8)))
    return cv2.morphologyEx(thr,cv2.MORPH_OPEN,k).sum(0).astype(float)

def _clusters(sig, frac, gap=8):
    idx=list(np.where(sig > frac*sig.max())[0]); out=[]; cur=[]
    for i in idx:
        if cur and i-cur[-1]>gap: out.append(cur); cur=[]
        cur.append(i)
    if cur: out.append(cur)
    return [(float(np.dot(c,sig[c])/sig[c].sum()), float(sig[c].sum())) for c in out]

def best_ladder(cands, n, extent_px, min_support=3):
    """Fit an arithmetic ladder of n+1 rungs (the 15' graticule) to `cands`.

    This is the primary estimator: a sheet's neatline is simply rung 0 and rung
    n of that ladder, which recovers border edges that are faint, cropped, or
    overprinted -- the common failure of a pure rectangle search.
    Returns (rung0, pitch, support) or None.
    """
    best = None
    for i in range(len(cands)):
        for j in range(i+1, len(cands)):
            d = cands[j] - cands[i]
            if d <= 0: continue
            for k in range(1, n+1):
                pitch = d/k
                span = pitch*n
                if not (extent_px*0.30 <= span <= extent_px*1.05): continue
                for t0 in range(0, n+1):
                    o = cands[i] - t0*pitch
                    if o < -pitch*0.25 or o+span > extent_px + pitch*0.25: continue
                    sup, err = 0, 0.0
                    for t in range(n+1):
                        r = o + t*pitch
                        d2 = min((abs(r-c) for c in cands), default=1e9)
                        if d2 < pitch*0.07: sup += 1; err += d2
                    if sup < min_support: continue
                    s = (sup, -err/max(sup,1))
                    if best is None or s > best[0]: best = (s, o, pitch, sup)
    return (best[1], best[2], best[3]) if best else None

def neatline(thr, aspect, nx, ny, tol=0.05):
    """Sheet border (x0,y0,x1,y1) in working px, via ladder fit + aspect check."""
    h, w = thr.shape
    hp = _clusters(_runs(thr,0), 0.12); vp = _clusters(_runs(thr,1), 0.12)
    hv = sorted(p[0] for p in hp if 0.002*h < p[0] < 0.998*h)
    vv = sorted(p[0] for p in vp if 0.002*w < p[0] < 0.998*w)
    if len(hv) < 2 or len(vv) < 2: raise RuntimeError("no neatline candidates")

    lx = best_ladder(vv, nx, w); ly = best_ladder(hv, ny, h)
    if lx and ly:
        x0, x1 = lx[0], lx[0]+lx[1]*nx
        y0, y1 = ly[0], ly[0]+ly[1]*ny
        err = abs(((x1-x0)/(y1-y0))/aspect - 1)
        if err <= tol:
            return (x0,y0,x1,y1), err, lx[2]+ly[2]

    # fallback: exhaustive rectangle search over observed lines
    best = None
    for i in range(len(vv)):
        for j in range(i+1,len(vv)):
            x0,x1 = vv[i], vv[j]
            if x1-x0 < 0.30*w: continue
            for a in range(len(hv)):
                for b in range(a+1,len(hv)):
                    y0,y1 = hv[a], hv[b]
                    if y1-y0 < 0.30*h: continue
                    r = ((x1-x0)/(y1-y0))/aspect
                    if abs(r-1) > tol*1.5: continue
                    s = (round((x1-x0)*(y1-y0)/(w*h),2), -abs(r-1))
                    if best is None or s > best[0]: best=(s,(x0,y0,x1,y1),abs(r-1))
    if best is None: raise RuntimeError("no rectangle matched expected aspect")
    return best[1], best[2], 0

def _refine(prof, guess, win):
    """Local peak of `prof` within +-win of guess; None if nothing stands out."""
    lo,hi = int(max(0,guess-win)), int(min(len(prof)-1,guess+win))
    if hi-lo < 3: return None
    seg = prof[lo:hi+1]
    base = np.median(prof)
    if seg.max() < base*2.0 or seg.max() <= 0: return None
    # centroid of the top of the peak
    m = seg >= seg.max()*0.7
    xs = np.arange(lo,hi+1)[m]; ws = seg[m]
    return float(np.dot(xs,ws)/ws.sum())

def graticule(g, ext, grat=0.25):
    """GCPs in working px: list of (x, y, lon, lat, measured_x, measured_y)."""
    import math
    thr = ink(g)
    lat_m = (ext[1]+ext[3])/2
    aspect = ((ext[2]-ext[0])*math.cos(math.radians(lat_m)))/(ext[3]-ext[1])
    nx = int(round((ext[2]-ext[0])/grat)); ny = int(round((ext[3]-ext[1])/grat))
    (x0,y0,x1,y1), err, hits = neatline(thr, aspect, nx, ny)
    # interior ink profiles (exclude the neatline itself and the collar)
    pad = int(0.01*max(thr.shape))
    inner = thr[int(y0)+pad:int(y1)-pad, int(x0)+pad:int(x1)-pad]
    # ticks are short => use a short structuring element
    hprof = cv2.morphologyEx(inner,cv2.MORPH_OPEN,
              cv2.getStructuringElement(cv2.MORPH_RECT,(21,1))).sum(1).astype(float)
    vprof = cv2.morphologyEx(inner,cv2.MORPH_OPEN,
              cv2.getStructuringElement(cv2.MORPH_RECT,(1,21))).sum(0).astype(float)
    px = (x1-x0)/nx; py = (y1-y0)/ny
    winx, winy = px*0.10, py*0.10
    xs, mx = [], []
    for i in range(nx+1):
        gx = x0 + i*px
        if i in (0,nx): xs.append(gx); mx.append(True); continue
        r = _refine(vprof, gx-(x0+pad), winx)
        xs.append(r+x0+pad if r is not None else gx); mx.append(r is not None)
    ys, my = [], []
    for j in range(ny+1):
        gy = y0 + j*py
        if j in (0,ny): ys.append(gy); my.append(True); continue
        r = _refine(hprof, gy-(y0+pad), winy)
        ys.append(r+y0+pad if r is not None else gy); my.append(r is not None)
    gcps=[]
    for i,x in enumerate(xs):
        lon = ext[0] + i*grat
        for j,y in enumerate(ys):
            lat = ext[3] - j*grat
            gcps.append((x,y,lon,lat,mx[i],my[j]))
    stats = dict(neatline=(x0,y0,x1,y1), aspect_err=err, ladder_hits=hits,
                 nx_meas=sum(mx)-2, nx_int=nx-1, ny_meas=sum(my)-2, ny_int=ny-1)
    return gcps, stats
