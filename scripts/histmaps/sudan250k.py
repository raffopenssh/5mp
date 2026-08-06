#!/usr/bin/env python3
"""
sudan250k — fetch + auto-georeference Sudan Survey Dept 1:250,000 sheets
(LOC g8310m.gct00289, 264 scans, 1908-1976).

Georeferencing is done from the *printed graticule*: the sheets carry a 15-arcmin
grid inside a 1.5 deg x 1.0 deg neatline, so we get ~35 control points per sheet
instead of 4 corners. Residuals are reported; a thin-plate spline absorbs paper
shrinkage and scanner skew.

  python3 sudan250k.py list [--filter TEXT]
  python3 sudan250k.py fetch  cs000027 [cs000028 ...]      # full-res JP2
  python3 sudan250k.py geo    cs000027.jp2                 # -> _geo.tif (+ .points)
  python3 sudan250k.py all    --sheet 65-I                 # fetch+geo everything matching
"""
import argparse, json, os, re, subprocess, sys, tempfile, math
import numpy as np, cv2

ROOT = os.path.dirname(os.path.abspath(__file__))
IIIF = "https://tile.loc.gov/image-services/iiif/service:gmd:gmd8m:g8310m:g8310m:gct00289"
STOR = "https://tile.loc.gov/storage-services/service/gmd/gmd8m/g8310m/g8310m/gct00289"
CAPS = STOR + "/captions.txt"

# ---- IMW 1:1M block origins (SW corner), read off the 1941 index sheet cs000032
BLOCK = {33:(18,20),34:(24,20),35:(30,20),36:(36,20),
         43:(18,16),44:(24,16),45:(30,16),46:(36,16),
         53:(18,12),54:(24,12),55:(30,12),56:(36,12),
         64:(18, 8),65:(24, 8),66:(30, 8),67:(36, 8),
         76:(18, 4),77:(24, 4),78:(30, 4),79:(36, 4),
         85:(24, 0),86:(30, 0)}
LET = "ABCDEFGHIJKLMNOP"           # 4x4 within a 6x4 deg block -> 1.5 x 1.0 deg
SW, SH = 1.5, 1.0
GRAT = 0.25                         # printed graticule interval (15 arcmin)

# modern NE/ND/NC-style designators map back onto the same 1.5x1.0 cells
def extent(block, letter):
    if block not in BLOCK: return None
    lo, la = BLOCK[block]; i = LET.index(letter.upper())
    xmin = lo + (i % 4) * SW
    ymax = la + 4 - (i // 4) * SH
    return (xmin, ymax - SH, xmin + SW, ymax)

def parse_sheet(title):
    t = re.sub(r'\bSheet\s+(\d{2})-l\b', r'Sheet \1-I', title)     # OCR l -> I
    m = re.search(r'\bN[EFCD][-\s]*(\d{2})-([A-Pa-p])\b', t)        # new-style
    if m:
        # new designators use a different block table; prefer the "(Old Number NN-X)"
        old = re.search(r'Old\s*(?:Number|no\.?)\s*(\d{2})-([A-Pa-p])', t, re.I)
        if old: m = old
        else: return None, None
    else:
        m = re.search(r'\b(\d{2})[-\s]([A-Pa-p])\b', t)
    if not m: return None, None
    return int(m.group(1)), m.group(2).upper()

# ------------------------------------------------------------------ catalogue
def catalogue(refresh=False):
    p = os.path.join(ROOT, "captions.txt")
    if refresh or not os.path.exists(p):
        subprocess.check_call(["curl","-sSL","-o",p,CAPS])
    out = []
    for line in open(p, encoding="utf-8", errors="replace"):
        f = line.rstrip("\n").split("\t")
        if len(f) < 4: continue
        cs, title = f[2], f[3]
        blk, let = parse_sheet(title)
        e = extent(blk, let) if blk else None
        yr = re.search(r'\b(19|18|20)\d{2}\b', title)
        out.append(dict(id=cs, title=title.strip(),
                        sheet=f"{blk}-{let}" if blk else None,
                        year=int(yr.group(0)) if yr else None,
                        extent=e,
                        iiif=f"{IIIF}:{cs}", jp2=f"{STOR}/{cs}.jp2"))
    return out

# ------------------------------------------------------------------ raster io
def rsize(path):
    j = json.loads(subprocess.check_output(["gdalinfo","-json",path]))
    return j["size"][0], j["size"][1]

def load_gray(path, maxdim=4000):
    W, H = rsize(path)
    s = max(W, H) / maxdim
    w, h = int(round(W/s)), int(round(H/s))
    tmp = tempfile.mktemp(suffix=".png")
    subprocess.check_call(["gdal_translate","-q","-of","PNG","-b","1",
                           "-outsize",str(w),str(h), path, tmp])
    a = cv2.imread(tmp, cv2.IMREAD_GRAYSCALE)
    for e in ("", ".aux.xml"):
        if os.path.exists(tmp+e): os.remove(tmp+e)
    return a, W, H

# ------------------------------------------------------------------ graticule
from grat import graticule as _graticule

def find_graticule(path, ext, verbose=True):
    g, W, H = load_gray(path)
    gcps_w, st = _graticule(g, ext, GRAT)
    sxw, syw = W/g.shape[1], H/g.shape[0]
    gcps = [(x*sxw, y*syw, lon, lat) for x, y, lon, lat, _, _ in gcps_w]
    if verbose:
        print(f"  neatline aspect err {st['aspect_err']*100:.2f}%  ladder support {st['ladder_hits']}"
              f"  interior rungs measured {st['nx_meas']}/{st['nx_int']} lon, "
              f"{st['ny_meas']}/{st['ny_int']} lat")
    return gcps, (W, H), st

# ------------------------------------------------------------------ warp
def residuals(gcps):
    """Affine LSQ fit residuals in metres (rough) — a sanity metric."""
    A = np.array([[x, y, 1] for x, y, _, _ in gcps])
    lon = np.array([p[2] for p in gcps]); lat = np.array([p[3] for p in gcps])
    cl, *_ = np.linalg.lstsq(A, lon, rcond=None)
    ca, *_ = np.linalg.lstsq(A, lat, rcond=None)
    dlon = A@cl - lon; dlat = A@ca - lat
    mlat = lat.mean()
    ex = dlon*111320*math.cos(math.radians(mlat)); ey = dlat*110540
    r = np.hypot(ex, ey)
    return r.mean(), r.max()

def georef(path, out=None, method="tps", cutline=True, verbose=True):
    cs = re.search(r'(cs\d{6})', os.path.basename(path))
    cat = {c["id"]: c for c in catalogue()}
    rec = cat.get(cs.group(1)) if cs else None
    if not rec or not rec["extent"]:
        raise SystemExit(f"no sheet extent known for {path}")
    ext = rec["extent"]
    print(f"{rec['id']}  {rec['title']}")
    print(f"  sheet {rec['sheet']}  extent {ext}")
    gcps, (W, H), stat = find_graticule(path, ext, verbose)
    mean, mx = residuals(gcps)
    # NB: this residual measures departure from affine (i.e. the polyconic
    # curvature + paper distortion the TPS will absorb). It is NOT an accuracy
    # estimate -- see aspect err / rung counts above for detection quality.
    print(f"  {len(gcps)} GCPs, non-affine deformation mean {mean:.0f} m  max {mx:.0f} m")
    out = out or re.sub(r'\.\w+$', '', path) + "_geo.tif"
    tmp = tempfile.mktemp(suffix=".vrt")
    cmd = ["gdal_translate","-q","-of","VRT","-a_srs","EPSG:4326"]
    for x, y, lon, lat in gcps:
        cmd += ["-gcp", f"{x:.2f}", f"{y:.2f}", f"{lon:.6f}", f"{lat:.6f}"]
    cmd += [path, tmp]
    subprocess.check_call(cmd)
    warp = ["gdalwarp","-q","-r","cubic","-t_srs","EPSG:4326",
            "-dstalpha","-co","COMPRESS=DEFLATE","-co","TILED=YES",
            "-co","BIGTIFF=IF_SAFER","-overwrite"]
    warp += ["-tps"] if method == "tps" else ["-order","1"]
    if cutline:                                   # clip collar to the neatline
        cl = tempfile.mktemp(suffix=".geojson")
        json.dump({"type":"FeatureCollection","features":[{"type":"Feature","properties":{},
          "geometry":{"type":"Polygon","coordinates":[[[ext[0],ext[1]],[ext[2],ext[1]],
                       [ext[2],ext[3]],[ext[0],ext[3]],[ext[0],ext[1]]]]}}]}, open(cl,"w"))
        warp += ["-cutline", cl, "-crop_to_cutline"]
    warp += [tmp, out]
    subprocess.check_call(warp)
    subprocess.call(["gdaladdo","-q","-r","average", out, "2","4","8","16","32"])
    pts = out + ".points"                          # QGIS Georeferencer format
    with open(pts,"w") as f:
        f.write("mapX,mapY,pixelX,pixelY,enable\n")
        for x,y,lon,lat in gcps: f.write(f"{lon},{lat},{x:.2f},{-y:.2f},1\n")
    print(f"  -> {out}\n  -> {pts}")
    return dict(id=rec["id"], sheet=rec["sheet"], year=rec["year"], title=rec["title"],
                extent=ext, out=out, n_gcps=len(gcps),
                aspect_err=stat["aspect_err"], ladder_support=stat["ladder_hits"],
                lon_rungs=[stat["nx_meas"], stat["nx_int"]],
                lat_rungs=[stat["ny_meas"], stat["ny_int"]],
                nonaffine_mean_m=round(mean), nonaffine_max_m=round(mx))

# ------------------------------------------------------------------ cli
def fetch(cs, dest=None):
    dest = dest or os.path.join(ROOT, f"{cs}.jp2")
    if os.path.exists(dest) and os.path.getsize(dest) > 1e6:
        print(f"  {cs} cached"); return dest
    print(f"  downloading {cs} ...")
    subprocess.check_call(["curl","-sSL","--retry","3","-o",dest,f"{STOR}/{cs}.jp2"])
    return dest

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list");  l.add_argument("--filter"); l.add_argument("--json", action="store_true")
    f = sub.add_parser("fetch"); f.add_argument("ids", nargs="+")
    g = sub.add_parser("geo");   g.add_argument("files", nargs="+")
    g.add_argument("--method", default="tps", choices=["tps","affine"])
    g.add_argument("--no-cutline", action="store_true")
    a = sub.add_parser("all");   a.add_argument("--sheet"); a.add_argument("--filter")
    a.add_argument("--block", help="1:1M block number, e.g. 65")
    a.add_argument("--all", action="store_true", help="every sheet with a known extent")
    a.add_argument("--method", default="tps", choices=["tps","affine"])
    a.add_argument("--keep-jp2", action="store_true", default=False,
                   help="keep the downloaded source JP2 (default: delete after warp)")
    ns = ap.parse_args()
    cat = catalogue()
    if ns.cmd == "list":
        rows = [c for c in cat if not ns.filter or ns.filter.lower() in c["title"].lower()
                or (c["sheet"] or "").lower() == ns.filter.lower()]
        if ns.json: print(json.dumps(rows, indent=1)); return
        for c in rows:
            e = c["extent"]
            print(f"{c['id']}  {(c['sheet'] or '?'):7} {str(c['year'] or ''):5} "
                  f"{('%.2f,%.2f..%.2f,%.2f' % e) if e else '(no extent)':32} {c['title']}")
        print(f"\n{len(rows)} sheets")
    elif ns.cmd == "fetch":
        for i in ns.ids: fetch(i)
    elif ns.cmd == "geo":
        for p in ns.files: georef(p, method=ns.method, cutline=not ns.no_cutline)
    elif ns.cmd == "all":
        sel = [c for c in cat if c["extent"] and
               (ns.all
                or (ns.block and c["sheet"].split("-")[0] == str(ns.block))
                or (ns.sheet and c["sheet"] == ns.sheet.upper())
                or (ns.filter and ns.filter.lower() in c["title"].lower()))]
        print(f"{len(sel)} sheets selected")
        qa, fails = [], []
        for c in sel:
            try:
                qa.append(georef(fetch(c["id"]), method=ns.method))
            except Exception as e:
                print(f"  !! {c['id']}: {e}"); fails.append(dict(id=c["id"], sheet=c["sheet"],
                       title=c["title"], error=str(e)))
            finally:
                if ns.keep_jp2 is False:
                    j = os.path.join(ROOT, f"{c['id']}.jp2")
                    if os.path.exists(j): os.remove(j)
        json.dump(dict(ok=qa, failed=fails), open(os.path.join(ROOT,"qa.json"),"w"), indent=1)
        print(f"\n{len(qa)} georeferenced, {len(fails)} failed -> qa.json")
        bad = [q for q in qa if q["aspect_err"] > 0.02
               or q["lon_rungs"][0] < q["lon_rungs"][1] or q["lat_rungs"][0] < q["lat_rungs"][1]]
        if bad:
            print("needs review:")
            for q in bad:
                print(f"  {q['id']} {q['sheet']:6} asp {q['aspect_err']*100:.1f}% "
                      f"rungs {q['lon_rungs'][0]}/{q['lon_rungs'][1]},{q['lat_rungs'][0]}/{q['lat_rungs'][1]}  {q['title']}")

if __name__ == "__main__": main()
