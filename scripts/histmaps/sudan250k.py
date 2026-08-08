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

# The letter I and the letter L are indistinguishable in these OCR'd captions
# when the caption renders lowercase ("Sheet 65-l"). Guessing is not safe: 65-I
# and 65-L sit at the same latitude, 4.5 deg apart in longitude, so the aspect
# check cannot catch a wrong guess and the sheet lands ~450 km off.
# Resolved by reading the printed NW graticule corner off the scan itself.
#   cs000004 "Hasoba Sheet 65-l Mar 1910" prints 28d30'E 10d00'N -> 65-L, not 65-I.
# Add an entry here (with the printed corner as evidence) for any new one.
AMBIGUOUS_IL = {"cs000004": (65, "L")}      # verified against printed corner

def parse_sheet(title, cs_id=None):
    if cs_id in AMBIGUOUS_IL:
        return AMBIGUOUS_IL[cs_id]
    t = title
    m = re.search(r'\bN[EFCD][-\s]*(\d{2})-([A-Pa-p])\b', t)         # new-style
    if m:
        # new designators use a different block table; prefer the "(Old Number NN-X)"
        old = re.search(r'Old\s*(?:Number|no\.?)\s*(\d{2})-([A-Pa-p])', t, re.I)
        if old: m = old
        else: return None, None
    else:
        m = re.search(r'\b(\d{2})[-\s]([A-Pa-p])\b', t)
    if not m: return None, None
    letter = m.group(2)
    if letter == "l":            # unresolved lowercase l: refuse to guess I vs L
        return None, None
    return int(m.group(1)), letter.upper()

# ------------------------------------------------------------------ catalogue
# The item holds 770 scans. This number is asserted, not assumed: the first run
# of this pipeline (2026-08-06) worked from a captions.txt that had been
# truncated at 264 lines by an interrupted curl. Nothing failed -- the parser
# happily produced 76 sheet cells, the mosaic built, the overlay shipped -- and
# the missing 506 lines were exactly blocks 53/55/64/66/77/78, i.e. all of South
# Sudan. A short file reads as a small collection. So: verify the length, and
# cross-check it against the item's own segment_count when we can reach it.
EXPECTED_SCANS = 770
RESOURCE_JSON = "https://www.loc.gov/resource/g8310m.gct00289/?fo=json"


def _remote_segment_count():
    try:
        out = subprocess.check_output(
            ["curl", "-sS", "--http1.1", "--retry", "3", "-A",
             "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120",
             RESOURCE_JSON], timeout=120)
        return int(json.loads(out)["resource"]["segment_count"])
    except Exception:
        return None


def fetch_captions(path, verify=True):
    """Download captions.txt atomically and refuse a short file."""
    tmp = path + ".part"
    subprocess.check_call(["curl", "-fsSL", "--http1.1", "--retry", "5",
                           "--retry-all-errors", "--retry-delay", "2",
                           "-o", tmp, CAPS])
    n = sum(1 for _ in open(tmp, encoding="utf-8", errors="replace"))
    want = (_remote_segment_count() if verify else None) or EXPECTED_SCANS
    if n < want:
        os.remove(tmp)
        raise RuntimeError(f"captions.txt truncated: {n} lines, expected >= {want}")
    os.replace(tmp, path)
    return n


def catalogue(refresh=False):
    p = os.path.join(ROOT, "captions.txt")
    if refresh or not os.path.exists(p):
        fetch_captions(p)
    else:
        n = sum(1 for _ in open(p, encoding="utf-8", errors="replace"))
        if n < EXPECTED_SCANS:
            print(f"captions.txt has {n} lines, expected {EXPECTED_SCANS} "
                  f"-- re-fetching", file=sys.stderr)
            fetch_captions(p)
    out = []
    for line in open(p, encoding="utf-8", errors="replace"):
        f = line.rstrip("\n").split("\t")
        if len(f) < 4: continue
        cs, title = f[2], f[3]
        blk, let = parse_sheet(title, cs)
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

def _make_ink_vrt(path, verbose=True, svg=None):
    """Render the source to a paper-free RGBA TIFF for warping.

    Done before gdalwarp (not after) so the alpha is resampled together with the
    image and the neatline cutline still applies. Full resolution is preserved.

    Intermediate is TIFF, not PNG: at 12000x9200 the PNG encoder is the slowest
    step in the whole sheet, and this file is deleted seconds later.
    """
    import ink
    W, H = rsize(path)
    tmp = tempfile.mktemp(suffix=".tif")
    subprocess.check_call(["gdal_translate","-q","-of","GTiff","-ot","Byte",
                           "-co","COMPRESS=LZW","-co","BIGTIFF=IF_SAFER", path, tmp])
    frac = ink.apply_to_file(tmp, svg=svg)
    # cv2 writes the 4th band without an ExtraSamples tag, so GDAL reads it as
    # "Undefined" and would warp it as a plain 4th colour band -- transparency
    # silently lost. Declare it alpha before it reaches gdalwarp.
    subprocess.check_call(["python3", "-c",
        "from osgeo import gdal;"
        "ds=gdal.Open(%r, gdal.GA_Update);"
        "ds.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand);"
        "ds.FlushCache();ds=None" % tmp])
    if verbose:
        print(f"  ink traced ({W}x{H}), coverage {frac:.3f}")
    return tmp, frac

def georef(path, out=None, method="tps", cutline=True, verbose=True,
           transparent=True, svg=False):
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
    svg_out = re.sub(r'\.\w+$', '', out) + ".svg" if (transparent and svg) else None
    if transparent:
        src, ink_frac = _make_ink_vrt(path, verbose, svg_out)
    else:
        src, ink_frac = path, None
    tmp = tempfile.mktemp(suffix=".vrt")
    cmd = ["gdal_translate","-q","-of","VRT","-a_srs","EPSG:4326"]
    for x, y, lon, lat in gcps:
        cmd += ["-gcp", f"{x:.2f}", f"{y:.2f}", f"{lon:.6f}", f"{lat:.6f}"]
    cmd += [src, tmp]
    subprocess.check_call(cmd)
    # Storage, and the bug that ate the last attempt: a JPEG-compressed COG
    # cannot hold an alpha band, so GDAL demoted our 8-bit alpha to a 1-BIT
    # internal mask -- every antialiased grain wisp at alpha=1 became fully
    # opaque, and the sheet came out 53% speckle. Traced ink is a few percent
    # coverage of one flat colour, so lossless DEFLATE RGBA is ~30x SMALLER
    # than that JPEG was (1.3 MB vs 44 MB on cs000029). Keep the real alpha band.
    warp = ["gdalwarp","-q","-r","cubic","-t_srs","EPSG:4326","-of","COG",
            "-co","COMPRESS=DEFLATE","-co","PREDICTOR=2","-co","LEVEL=9",
            "-co","BLOCKSIZE=512","-co","OVERVIEWS=IGNORE_EXISTING",
            "-co","BIGTIFF=IF_SAFER","-overwrite"]
    # Source already carries alpha when traced; -dstalpha would add a second one.
    if not transparent:
        warp += ["-dstalpha"]
    warp += ["-tps"] if method == "tps" else ["-order","1"]
    if cutline:                                   # clip collar to the neatline
        cl = tempfile.mktemp(suffix=".geojson")
        json.dump({"type":"FeatureCollection","features":[{"type":"Feature","properties":{},
          "geometry":{"type":"Polygon","coordinates":[[[ext[0],ext[1]],[ext[2],ext[1]],
                       [ext[2],ext[3]],[ext[0],ext[3]],[ext[0],ext[1]]]]}}]}, open(cl,"w"))
        warp += ["-cutline", cl, "-crop_to_cutline"]
    warp += [tmp, out]
    subprocess.check_call(warp)   # COG driver builds overviews itself
    if src != path and os.path.exists(src):
        os.remove(src)
        for e in (".aux.xml", ".wld"):
            if os.path.exists(src+e): os.remove(src+e)
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
                nonaffine_mean_m=round(mean), nonaffine_max_m=round(mx),
                ink_frac=ink_frac)

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
    g.add_argument("--no-transparent", action="store_true", help="keep the raw scan, paper and all")
    g.add_argument("--svg", action="store_true", help="also write the traced vectors as .svg")
    a = sub.add_parser("all");   a.add_argument("--sheet"); a.add_argument("--filter")
    a.add_argument("--ids", help="comma-separated cs ids, or @file / @selection.json")
    a.add_argument("--block", help="1:1M block number, e.g. 65")
    a.add_argument("--all", action="store_true", help="every sheet with a known extent")
    a.add_argument("--method", default="tps", choices=["tps","affine"])
    a.add_argument("--jobs", type=int, default=4, help="parallel sheets")
    a.add_argument("--dry-run", action="store_true", help="list what would be done, fetch nothing")
    a.add_argument("--resume", action="store_true", help="skip sheets already georeferenced")
    a.add_argument("--out-check", default="/home/exedev/5mp/data/histmaps/geo",
                   help="also treat sheets present here as done (for --resume)")
    a.add_argument("--no-transparent", action="store_true", help="keep the raw scan, paper and all")
    a.add_argument("--svg", action="store_true", help="also write the traced vectors as .svg")
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
        for p in ns.files: georef(p, method=ns.method, cutline=not ns.no_cutline,
                                  transparent=not ns.no_transparent, svg=ns.svg)
    elif ns.cmd == "all":
        want = None
        if ns.ids:
            if ns.ids.startswith("@"):
                p = ns.ids[1:]
                raw = open(p).read()
                if p.endswith(".json"):
                    want = [r["id"] for r in json.loads(raw)["selected"]] \
                           if isinstance(json.loads(raw), dict) else json.loads(raw)
                else:
                    want = raw.split()
            else:
                want = ns.ids.split(",")
            # Keep the requested ORDER, not just the membership: a 195-sheet run
            # is ~2 days, so selection.json puts the sheets that matter first
            # (see select.py --priority-bbox) and an interrupted run must have
            # georeferenced those, not an alphabetical prefix.
            order = [w.strip() for w in want if w.strip()]
            want = set(order)
            rank = {cid: i for i, cid in enumerate(order)}
        sel = [c for c in cat if c["extent"] and
               (want is not None and c["id"] in want
                or want is None and
                (ns.all
                 or (ns.block and c["sheet"].split("-")[0] == str(ns.block))
                 or (ns.sheet and c["sheet"] == ns.sheet.upper())
                 or (ns.filter and ns.filter.lower() in c["title"].lower())))]
        if want is not None:
            sel.sort(key=lambda c: rank.get(c["id"], 1 << 30))
            missing = want - {c["id"] for c in sel}
            if missing:
                print(f"warning: {len(missing)} requested ids have no known extent: "
                      f"{','.join(sorted(missing))}", flush=True)
        if ns.resume:
            # A full run is many hours; skip sheets already written, whether they
            # sit in the working dir or have been swept into --out-check.
            def have(cid):
                n = f"{cid}_geo.tif"
                return any(os.path.exists(os.path.join(d, n))
                           for d in (ROOT, ns.out_check) if d)
            before = len(sel)
            sel = [c for c in sel if not have(c["id"])]
            print(f"resume: skipping {before - len(sel)} already-written sheets", flush=True)
        if ns.dry_run:
            for c in sel:
                print(f"  {c['id']} {(c['sheet'] or '?'):7} {str(c['year'] or ''):5} "
                      f"{c['title'][:52]}")
            print(f"\n{len(sel)} sheets would be fetched+georeferenced")
            return
        print(f"{len(sel)} sheets selected", flush=True)
        qa, fails = [], []
        import io, threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        class _ThreadTee:
            """Per-thread stdout capture.

            contextlib.redirect_stdout patches sys.stdout *globally*, so with
            --jobs>1 one worker's buffer swallows both the other worker's lines
            and the main thread's progress report -- the run goes silent for
            hours while still writing correct files. Route each thread to its
            own buffer instead, falling back to the real stdout.
            """
            def __init__(self, real):
                self.real, self.local = real, threading.local()
            def _t(self):
                return getattr(self.local, "buf", None) or self.real
            def write(self, s):
                return self._t().write(s)
            def flush(self):
                t = self._t()
                if hasattr(t, "flush"): t.flush()

        tee = _ThreadTee(sys.stdout)
        sys.stdout = tee

        def one(c):
            """Download+warp a sheet. Output is captured per-thread so parallel
            workers do not interleave their line-by-line reports."""
            buf = io.StringIO()
            tee.local.buf = buf
            try:
                r = georef(fetch(c["id"]), method=ns.method,
                           transparent=not ns.no_transparent, svg=ns.svg)
                return c, r, None, buf.getvalue()
            except Exception as e:
                return c, None, e, buf.getvalue()
            finally:
                tee.local.buf = None
                if not ns.keep_jp2:
                    j = os.path.join(ROOT, f"{c['id']}.jp2")
                    if os.path.exists(j): os.remove(j)

        done = 0
        with ThreadPoolExecutor(max_workers=ns.jobs) as ex:
            futs = {ex.submit(one, c): c for c in sel}
            for f in as_completed(futs):
                c, r, err, log = f.result()
                done += 1
                print(f"[{done}/{len(sel)}] {log.rstrip()}", flush=True)
                if err is not None:
                    print(f"  !! {c['id']}: {err}", flush=True)
                    fails.append(dict(id=c["id"], sheet=c["sheet"],
                                      title=c["title"], error=str(err)))
                else:
                    qa.append(r)
        json.dump(dict(ok=qa, failed=fails), open(os.path.join(ROOT,"qa.json"),"w"), indent=1)
        print(f"\n{len(qa)} georeferenced, {len(fails)} failed -> qa.json")
        # Ink coverage is the tracing QA: ~0.02-0.12 is a normal sheet. Near 0
        # means the trace ate the map (over-strict threshold / blank scan);
        # high means paper grain leaked through and survived speckle removal.
        bad = [q for q in qa if q["aspect_err"] > 0.02
               or q["lon_rungs"][0] < q["lon_rungs"][1] or q["lat_rungs"][0] < q["lat_rungs"][1]
               or (q.get("ink_frac") is not None and not 0.01 <= q["ink_frac"] <= 0.25)]
        if bad:
            print("needs review:")
            for q in bad:
                ink = q.get("ink_frac")
                print(f"  {q['id']} {q['sheet']:6} asp {q['aspect_err']*100:.1f}% "
                      f"rungs {q['lon_rungs'][0]}/{q['lon_rungs'][1]},{q['lat_rungs'][0]}/{q['lat_rungs'][1]}"
                      f"  ink {ink if ink is None else round(ink,3)}  {q['title']}")

if __name__ == "__main__": main()
