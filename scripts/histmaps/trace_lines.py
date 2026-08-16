#!/usr/bin/env python3
"""Trace + geocode the LINEAR features of the Sudan 1:250k historical maps.

Companion to ocr_labels.py (same tiling, same model, same ledger
discipline): walks the georeferenced sheets in data/histmaps/geo/, cuts
each into overlapping 2048px native windows (sent at 1024px), and asks the
vision LLM to trace every linear feature as a polyline with pixel
vertices: caravan routes/tracks (e.g. DARB EL ARBA'IN), roads, railways,
telegraph lines, watercourses (khors/wadis) and boundaries. Vertices are
mapped through the sheet geotransform to WGS84 and stored in
data/histmaps/labels.sqlite3 (tables line_tiles, lines).

The existing OCR'd labels (ocr_labels.py) are used as hints: every
route/water/boundary label already located in the tile (labels.px/py are
native sheet pixels) is listed in the user message with its tile
coordinates, so the model names the traced line with the transcription the
label pass already agreed on instead of re-reading tiny curved text.

It also captures UNKNOWN POINT SYMBOLS: any icon the model can't read as
text (well rings, cairns, beacons, ruins, unfamiliar marks) is stored in
the `symbols` table with lon/lat and a 192x192 native-resolution PNG crop,
so they can all be categorized at once later (`symbols` subcommand dumps
crops + index.csv for a bulk human/LLM pass).

Spike result (2026-08-15, cs000223): fireworks/muse-glimmer-30b traces
dashed routes/boundaries/wadis with vertices within ~10-30 px of the ink
at 1024px (~250-750 m at this scale -- comparable to the map's own
compilation error, see README "route traverses" caveat). Same model as the
label OCR bake-off winner; do not swap without re-checking a traced tile
visually against the sheet.

Invariants honoured (AGENTS.md):
  * errored tiles stay state='error' and are retried -- a no-op must not
    read as an answer. Only 'done'/'blank' are terminal.
  * counts derived, never typed: `status` reads the DB.
  * resumable ledger (line_tiles); XSA-priority sheet order.

Usage:
  python3 trace_lines.py run [--workers 3] [--limit N] [--sheet cs000223]
  python3 trace_lines.py status
  python3 trace_lines.py dedupe        # build lines_dedup (drop overlap dupes)
  python3 trace_lines.py preview SHEET OUT.png   # traced lines over the sheet
"""
import argparse
import base64
import io
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.request
import concurrent.futures

from PIL import Image

Image.MAX_IMAGE_PIXELS = None

GEO_DIR = os.environ.get("HISTMAP_GEO", "/home/exedev/5mp/data/histmaps/geo")
DB_PATH = os.environ.get("HISTMAP_LABELS_DB", "/home/exedev/5mp/data/histmaps/labels.sqlite3")
LLM_URL = os.environ.get("HISTMAP_LLM_URL", "https://llm.int.exe.xyz/v1/chat/completions")
MODEL = os.environ.get("HISTMAP_OCR_MODEL", "fireworks/muse-glimmer-30b")

TILE = 2048
STRIDE = 1792
SEND = 1024
INK_MIN = 0.0015
MAX_TOKENS = 8000
RETRY_WAIT = 600
MAX_STALLED = 12

XSA_BBOX = (22.70, 4.25, 31.30, 10.97)

KINDS = {"track", "road", "railway", "telegraph", "watercourse", "boundary", "other"}

SYS_PROMPT = (
    "Scanned 1930s Sudan Survey map, black ink on white, 1024x1024 px. "
    "Trace EVERY linear feature; typical tiles have 3-15. Kinds:\n"
    "track - dotted/dashed camel or caravan routes, tracks, footpaths (most common)\n"
    "road - cleared/motor roads (double or heavy line)\n"
    "railway - line with cross-ticks\n"
    "telegraph - line with T-marks or labelled Telegraph\n"
    "watercourse - khor/wadi/river lines (solid, often branching - trace the main stems)\n"
    "boundary - international/province/district boundary (dash-dot patterns)\n"
    "Ignore: text, tree/bush symbols, contours and form lines, hill hachures, "
    "and the perfectly straight thin graticule grid lines that span the whole tile.\n"
    'One JSON object per line: {"kind":"...","style":"dashed|dotted|solid|dashdot|double",'
    '"name":"label written along this line, else null","pts":[[x,y],...]}\n'
    "pts: polyline vertices in pixel coords ordered along the line, dense enough "
    "to follow every bend (up to 30 points). Trace each line across the full tile, "
    "edge to edge if it continues.\n"
    "ALSO: for each distinct POINT SYMBOL or icon that is not plain text and not "
    "a tree/bush/grass fill pattern (e.g. well rings, cairns, beacons, ruins, "
    "forts, camps, mines, circled dots, unfamiliar marks), output one "
    'object: {"symbol":"short visual description","cx":int,"cy":int}. '
    "If nothing at all: NONE. Only JSON lines."
)


def db():
    c = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db(c):
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS line_tiles(
          sheet TEXT NOT NULL, tx INTEGER NOT NULL, ty INTEGER NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending',  -- pending|blank|done|error
          ink REAL, n_lines INTEGER, error TEXT, model TEXT,
          prompt_tokens INTEGER, completion_tokens INTEGER,
          updated_at TEXT DEFAULT (datetime('now')),
          PRIMARY KEY(sheet, tx, ty));
        CREATE TABLE IF NOT EXISTS lines(
          id INTEGER PRIMARY KEY,
          sheet TEXT NOT NULL, tx INTEGER, ty INTEGER,
          kind TEXT, style TEXT, name TEXT,
          n_pts INTEGER, length_km REAL,
          pts TEXT NOT NULL,        -- JSON [[lon,lat],...]
          minlon REAL, minlat REAL, maxlon REAL, maxlat REAL,
          model TEXT);
        CREATE INDEX IF NOT EXISTS idx_lines_bbox ON lines(minlon, maxlon, minlat, maxlat);
        CREATE INDEX IF NOT EXISTS idx_lines_kind ON lines(kind);
        
        -- Point symbols the tracer saw but could not classify: a georef'd
        -- native-resolution crop each, so a later bulk pass (human or LLM)
        -- can categorize them all at once without re-reading the sheets.
        CREATE TABLE IF NOT EXISTS symbols(
          id INTEGER PRIMARY KEY,
          sheet TEXT NOT NULL, tx INTEGER, ty INTEGER,
          descr TEXT,           -- model's short visual description
          px REAL, py REAL,     -- native sheet pixel coords of center
          lon REAL NOT NULL, lat REAL NOT NULL,
          crop BLOB,            -- 192x192 native-res PNG centered on the symbol
          category TEXT,        -- NULL until a categorization pass fills it
          model TEXT);
        CREATE INDEX IF NOT EXISTS idx_symbols_lonlat ON symbols(lon, lat);
        """
    )
    for col, typ in (("pts_raw", "TEXT"), ("support", "REAL")):
        try:
            c.execute(f"ALTER TABLE lines ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # already there
    c.commit()


def sheet_geotransform(path):
    from osgeo import gdal
    ds = gdal.Open(path)
    gt = ds.GetGeoTransform()
    size = (ds.RasterXSize, ds.RasterYSize)
    ds = None
    return gt, size


def list_sheets():
    out = []
    import glob
    for p in sorted(glob.glob(os.path.join(GEO_DIR, "*_geo.tif"))):
        sid = os.path.basename(p).replace("_geo.tif", "")
        gt, size = sheet_geotransform(p)
        w, s = gt[0], gt[3] + gt[5] * size[1]
        e, n = gt[0] + gt[1] * size[0], gt[3]
        pri = e > XSA_BBOX[0] and w < XSA_BBOX[2] and n > XSA_BBOX[1] and s < XSA_BBOX[3]
        out.append((sid, p, gt, size, pri))
    out.sort(key=lambda r: (not r[4], r[0]))
    return out


def tile_grid(size):
    w, h = size
    xs = list(range(0, max(w - 256, 1), STRIDE))
    ys = list(range(0, max(h - 256, 1), STRIDE))
    return [(tx, ty) for ty in ys for tx in xs]


def render_tile(img, tx, ty):
    c = img.crop((tx, ty, tx + TILE, ty + TILE))
    if c.mode != "RGBA":
        c = c.convert("RGBA")
    bg = Image.new("RGB", c.size, (255, 255, 255))
    bg.paste(c, (0, 0), c)
    g = bg.convert("L").resize((256, 256))
    hist = g.histogram()
    ink = sum(hist[:128]) / (256 * 256)
    if ink < INK_MIN:
        return None, ink
    return bg.resize((SEND, SEND), Image.LANCZOS), ink


def call_llm(png_bytes, hint_text=None, max_tokens=MAX_TOKENS, sys_prompt=None):
    img64 = base64.b64encode(png_bytes).decode()
    content = [{"type": "image_url",
                "image_url": {"url": "data:image/png;base64," + img64}}]
    if hint_text:
        content.append({"type": "text", "text": hint_text})
    body = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": sys_prompt or SYS_PROMPT},
            {"role": "user", "content": content},
        ],
    }).encode()
    req = urllib.request.Request(LLM_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=300))
    msg = r["choices"][0]["message"]
    usage = r.get("usage", {})
    return msg.get("content") or "", r["choices"][0].get("finish_reason"), usage


# lines contain nested arrays, so match balanced-enough objects per line
OBJ_RE = re.compile(r"\{.*\}")


def parse_lines(content):
    """-> (lines, symbols). Tolerant JSON-lines parse."""
    out = []
    syms = []
    for ln in content.splitlines():
        m = OBJ_RE.search(ln)
        if not m:
            continue
        try:
            o = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if "symbol" in o:
            try:
                cx, cy = float(o["cx"]), float(o["cy"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= cx <= SEND and 0 <= cy <= SEND):
                continue
            d = str(o["symbol"]).strip()
            if d:
                syms.append({"descr": d[:200], "cx": cx, "cy": cy})
            continue
        pts = o.get("pts")
        if not isinstance(pts, list) or len(pts) < 2:
            continue
        clean = []
        for p in pts:
            try:
                x, y = float(p[0]), float(p[1])
            except (TypeError, ValueError, IndexError):
                clean = []
                break
            if not (-64 <= x <= SEND + 64 and -64 <= y <= SEND + 64):
                clean = []
                break
            clean.append((min(max(x, 0), SEND), min(max(y, 0), SEND)))
        if len(clean) < 2:
            continue
        kind = str(o.get("kind") or "other").lower()
        if kind not in KINDS:
            kind = "other"
        name = o.get("name")
        if isinstance(name, str):
            name = name.strip() or None
        else:
            name = None
        style = str(o.get("style") or "").lower() or None
        out.append({"kind": kind, "style": style, "name": name, "pts": clean})
    return out, syms


def poly_length_km(lonlat):
    km = 0.0
    for (a, b), (c, d) in zip(lonlat, lonlat[1:]):
        dx = (c - a) * 111.32 * math.cos(math.radians((b + d) / 2))
        dy = (d - b) * 111.32
        km += math.hypot(dx, dy)
    return km


# Categories of already-OCR'd labels that name linear features. 'terrain'
# and 'place' would only distract; route/water/boundary are the lines'
# own names ("DARB EL ARBA'IN", "Wadi Howar", "District Boundary").
HINT_CATS = ("route", "water", "boundary")


def sheet_label_hints(c, sid):
    """[(px, py, text)] for this sheet's linear-feature labels (native px)."""
    return c.execute(
        "SELECT px, py, text FROM labels WHERE sheet=? AND px IS NOT NULL"
        " AND category IN (?,?,?)", (sid,) + HINT_CATS).fetchall()


def tile_hint_text(hints, tx, ty):
    """Hint block for one tile: known labels in send-scale (1024px) coords."""
    scale = SEND / TILE
    seen = set()
    rows = []
    for px, py, text in hints:
        if not (tx <= px < tx + TILE and ty <= py < ty + TILE):
            continue
        x, y = int((px - tx) * scale), int((py - ty) * scale)
        key = (text.lower(), x // 50, y // 50)
        if key in seen:
            continue
        seen.add(key)
        rows.append(f'- "{text}" near ({x},{y})')
        if len(rows) >= 25:
            break
    if not rows:
        return None
    return ("Known label transcriptions already OCR'd from this tile "
            "(route/water/boundary names with pixel positions). When a traced "
            "line passes one of these, use that exact text as its name:\n"
            + "\n".join(rows))


def process_tile(img, gt, sid, tx, ty, hints=()):
    tile_img, ink = render_tile(img, tx, ty)
    if tile_img is None:
        return {"state": "blank", "ink": ink, "lines": [], "usage": {}}
    buf = io.BytesIO()
    tile_img.save(buf, "PNG")
    png = buf.getvalue()
    hint = tile_hint_text(hints, tx, ty)
    try:
        content, finish, usage = call_llm(png, hint)
        if not content.strip() and finish == "length":
            content, finish, usage = call_llm(png, hint, max_tokens=MAX_TOKENS * 2)
    except Exception as e:
        return {"state": "error", "ink": ink, "lines": [],
                "error": f"{type(e).__name__}: {e}", "usage": {}}
    if not content.strip():
        return {"state": "error", "ink": ink, "lines": [],
                "error": f"empty content (finish={finish})", "usage": usage}
    traced, syms = parse_lines(content)
    scale = TILE / SEND
    rows = []
    for t in traced:
        lonlat = []
        for x, y in t["pts"]:
            px = tx + x * scale
            py = ty + y * scale
            lon = gt[0] + gt[1] * px + gt[2] * py
            lat = gt[3] + gt[4] * px + gt[5] * py
            lonlat.append((round(lon, 6), round(lat, 6)))
        lons = [p[0] for p in lonlat]
        lats = [p[1] for p in lonlat]
        rows.append((sid, tx, ty, t["kind"], t["style"], t["name"],
                     len(lonlat), round(poly_length_km(lonlat), 3),
                     json.dumps(lonlat), min(lons), min(lats),
                     max(lons), max(lats), MODEL))
    sym_rows = []
    for s in syms:
        px = tx + s["cx"] * scale
        py = ty + s["cy"] * scale
        lon = gt[0] + gt[1] * px + gt[2] * py
        lat = gt[3] + gt[4] * px + gt[5] * py
        # native-res 192x192 crop centered on the symbol, georef'd via px/py
        half = 96
        box = (int(px - half), int(py - half), int(px + half), int(py + half))
        cimg = img.crop(box)
        if cimg.mode == "RGBA":
            cbg = Image.new("RGB", cimg.size, (255, 255, 255))
            cbg.paste(cimg, (0, 0), cimg)
            cimg = cbg
        cbuf = io.BytesIO()
        cimg.save(cbuf, "PNG")
        sym_rows.append((sid, tx, ty, s["descr"], px, py, lon, lat,
                         cbuf.getvalue(), MODEL))
    return {"state": "done", "ink": ink, "lines": rows, "symbols": sym_rows,
            "usage": usage}


def cmd_run(args):
    c = db()
    init_db(c)
    sheets = list_sheets()
    if args.sheet:
        sheets = [s for s in sheets if s[0] == args.sheet]
        if not sheets:
            sys.exit(f"sheet {args.sheet} not found in {GEO_DIR}")

    for sid, path, gt, size, pri in sheets:
        c.executemany(
            "INSERT OR IGNORE INTO line_tiles(sheet,tx,ty) VALUES(?,?,?)",
            [(sid, tx, ty) for tx, ty in tile_grid(size)])
    c.commit()

    stalled = 0
    while True:
        attempted, succeeded = run_pass(c, sheets, args)
        if args.limit:
            break
        remaining = c.execute(
            "SELECT count(*) FROM line_tiles WHERE state IN ('pending','error')"
            + (" AND sheet=?" if args.sheet else ""),
            (args.sheet,) if args.sheet else ()).fetchone()[0]
        if remaining == 0:
            print("queue clean: all tiles done or blank", flush=True)
            break
        if succeeded == 0:
            stalled += 1
            if stalled >= MAX_STALLED:
                print(f"STALLED: {remaining} tiles still failing after "
                      f"{MAX_STALLED} no-progress passes -- giving up.", flush=True)
                sys.exit(1)
            print(f"pass fixed nothing ({remaining} left); waiting "
                  f"{RETRY_WAIT}s before retry {stalled}/{MAX_STALLED}", flush=True)
            time.sleep(RETRY_WAIT)
        else:
            stalled = 0
            print(f"pass done: {succeeded}/{attempted} ok, "
                  f"{remaining} remaining; sweeping again", flush=True)

    c.commit()
    if not args.limit:
        print("post-run: refine -> dedupe -> stitch", flush=True)
        args.all = False   # refine only sheets not yet snapped
        cmd_refine(args)
        cmd_dedupe(args)
        cmd_stitch(args)
        import subprocess
        r = subprocess.run(["bash", os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "export_labels.sh")])
        print(f"export_labels.sh exit={r.returncode}", flush=True)
    cmd_status(args)


def run_pass(c, sheets, args):
    lock = threading.Lock()
    done = [0]
    ok = [0]
    t0 = time.time()

    for sid, path, gt, size, pri in sheets:
        todo = c.execute(
            "SELECT tx,ty FROM line_tiles WHERE sheet=? AND state IN ('pending','error')"
            " ORDER BY ty,tx", (sid,)).fetchall()
        if not todo:
            continue
        if args.limit and done[0] >= args.limit:
            break
        img = Image.open(path)
        img.load()
        hints = sheet_label_hints(c, sid)
        print(f"[{sid}] {len(todo)} tiles (priority={pri}, {len(hints)} label hints)", flush=True)

        def work(txy):
            tx, ty = txy
            res = process_tile(img, gt, sid, tx, ty, hints)
            with lock:
                u = res.get("usage") or {}
                c.execute(
                    "UPDATE line_tiles SET state=?, ink=?, n_lines=?, error=?,"
                    " model=?, prompt_tokens=?, completion_tokens=?,"
                    " updated_at=datetime('now') WHERE sheet=? AND tx=? AND ty=?",
                    (res["state"], res["ink"], len(res["lines"]),
                     res.get("error"), MODEL,
                     u.get("prompt_tokens"), u.get("completion_tokens"),
                     sid, tx, ty))
                if res["lines"]:
                    c.execute("DELETE FROM lines WHERE sheet=? AND tx=? AND ty=?",
                              (sid, tx, ty))
                    c.executemany(
                        "INSERT INTO lines(sheet,tx,ty,kind,style,name,n_pts,"
                        " length_km,pts,minlon,minlat,maxlon,maxlat,model)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", res["lines"])
                if res.get("symbols"):
                    c.execute("DELETE FROM symbols WHERE sheet=? AND tx=? AND ty=?",
                              (sid, tx, ty))
                    c.executemany(
                        "INSERT INTO symbols(sheet,tx,ty,descr,px,py,lon,lat,"
                        " crop,model) VALUES(?,?,?,?,?,?,?,?,?,?)", res["symbols"])
                done[0] += 1
                if res["state"] != "error":
                    ok[0] += 1
                if done[0] % 10 == 0:
                    c.commit()
                    el = time.time() - t0
                    print(f"  {done[0]} tiles, {el/done[0]:.1f}s/tile", flush=True)
            if res["state"] == "error":
                print(f"  ERR {sid} {tx},{ty}: {res.get('error')}", flush=True)

        n = args.limit - done[0] if args.limit else len(todo)
        with concurrent.futures.ThreadPoolExecutor(args.workers) as ex:
            list(ex.map(work, todo[:max(n, 0)]))
        c.commit()
        img.close()
        if args.limit and done[0] >= args.limit:
            break

    c.commit()
    return done[0], ok[0]


def cmd_status(args):
    c = db()
    init_db(c)
    rows = c.execute("SELECT state, count(*) FROM line_tiles GROUP BY 1").fetchall()
    print(dict(rows))
    nl = c.execute("SELECT count(*), count(DISTINCT sheet) FROM lines").fetchone()
    print(f"lines: {nl[0]} across {nl[1]} sheets")
    k = c.execute("SELECT kind, count(*), round(sum(length_km)) FROM lines GROUP BY 1 ORDER BY 2 DESC").fetchall()
    for kind, n, km in k:
        print(f"  {kind:12s} {n:6d}  {km:8.0f} km")
    tok = c.execute("SELECT sum(prompt_tokens), sum(completion_tokens) FROM line_tiles").fetchone()
    print(f"tokens: prompt={tok[0]} completion={tok[1]}")
    ns = c.execute("SELECT count(*), count(category) FROM symbols").fetchone()
    print(f"symbols: {ns[0]} captured, {ns[1]} categorized")
    err = c.execute("SELECT count(*) FROM line_tiles WHERE state='error'").fetchone()[0]
    if err:
        print(f"NOTE: {err} errored tiles will be retried on next run")


def _seg_dup(short_pts, long_pts, tol_deg):
    """True if >=70% of short's vertices lie within tol of long's vertices."""
    hit = 0
    for x, y in short_pts:
        best = min((x - a) ** 2 + (y - b) ** 2 for a, b in long_pts)
        if best <= tol_deg * tol_deg:
            hit += 1
    return hit >= 0.7 * len(short_pts)



def _sheet_ink_snap(path):
    """-> snap(px,py,max_px): pull a native-px point to the nearest inked
    pixel of its own sheet (None if none within max_px). Built once per
    sheet from a half-scale grayscale + EDT with indices."""
    import numpy as np
    from scipy import ndimage
    img = Image.open(path)
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, (0, 0), img)
        img = bg
    g = img.convert("L").resize((img.width // 2, img.height // 2))
    a = np.asarray(g) < 128
    img.close()
    dist, (iy, ix) = ndimage.distance_transform_edt(~a, return_indices=True)
    H, W = a.shape

    def snap(px, py, max_px=60.0):
        x, y = int(px / 2), int(py / 2)
        if not (0 <= x < W and 0 <= y < H):
            return None
        if dist[y, x] * 2 > max_px:
            return None
        return (float(ix[y, x]) * 2, float(iy[y, x]) * 2)

    return snap


def _refine_polyline(pts_px, snap, step=16.0, iters=3):
    """Densify + snap a native-px polyline onto the ink; iterate with a
    shrinking radius (coarse pull, then settle). Returns (pts, support)."""
    cur = list(pts_px)
    radius = 60.0
    support = 0.0
    for _ in range(iters):
        dense = [cur[0]]
        for a, b in zip(cur, cur[1:]):
            d = math.hypot(b[0] - a[0], b[1] - a[1])
            n = max(1, int(d / step))
            for k in range(1, n + 1):
                dense.append((a[0] + (b[0] - a[0]) * k / n,
                              a[1] + (b[1] - a[1]) * k / n))
        hit = 0
        out = []
        for q in dense:
            sp = snap(q[0], q[1], radius)
            if sp is not None:
                hit += 1
                out.append(sp)
            else:
                out.append(q)
        support = hit / max(len(dense), 1)
        cur = out
        radius = max(radius / 2, 12.0)
    dedup = [cur[0]]
    for q in cur[1:]:
        if math.hypot(q[0] - dedup[-1][0], q[1] - dedup[-1][1]) >= 4:
            dedup.append(q)
    return dedup, support


def cmd_refine(args):
    """Raster-guided refinement. The LLM contributes topology/kind/name; the
    sheet raster contributes exact geometry: densify each traced polyline to
    ~16 native px spacing, snap every sample to the nearest ink pixel,
    iterate with shrinking radius -- meanders the model chord-cut are
    recovered from the ink itself. support = fraction of samples that found
    ink; < 0.35 means the line was drawn over blank paper (hallucinated
    straight diagonals) and dedupe/stitch/export drop it. Original vertices
    kept in pts_raw; idempotent (re-runs restart from pts_raw)."""
    c = db()
    init_db(c)
    which = "1=1" if args.all else "support IS NULL"
    sheets = [r[0] for r in c.execute(
        f"SELECT DISTINCT sheet FROM lines WHERE {which}")]
    # EDT + snapping is CPU-bound pure-Python/numpy: farm sheets to processes,
    # write results back on this connection (single writer).
    import multiprocessing as mp
    if len(sheets) > 1:
        with mp.Pool(min(4, len(sheets))) as pool:
            for sid, updates in pool.imap_unordered(
                    _refine_sheet_worker, [(sid, which) for sid in sheets]):
                nlow = 0
                for row in updates:
                    c.execute(
                        "UPDATE lines SET pts_raw=COALESCE(pts_raw, pts), pts=?,"
                        " support=?, n_pts=?, length_km=?, minlon=?, minlat=?,"
                        " maxlon=?, maxlat=? WHERE id=?", row)
                    if row[1] < 0.35:
                        nlow += 1
                c.commit()
                print(f"[{sid}] refined {len(updates)} lines, {nlow} low-support",
                      flush=True)
        return
    for sid in sheets:
        path = os.path.join(GEO_DIR, sid + "_geo.tif")
        if not os.path.exists(path):
            print(f"[{sid}] missing geotiff, skipped")
            continue
        gt, size = sheet_geotransform(path)
        snap = _sheet_ink_snap(path)
        rows = c.execute(
            f"SELECT id, COALESCE(pts_raw, pts) FROM lines WHERE sheet=? AND {which}",
            (sid,)).fetchall()
        nlow = 0
        for rid, pts in rows:
            lonlat = json.loads(pts)
            px = [((lon - gt[0]) / gt[1], (lat - gt[3]) / gt[5]) for lon, lat in lonlat]
            ref, sup = _refine_polyline(px, snap)
            out = [(round(gt[0] + gt[1] * x, 6), round(gt[3] + gt[5] * y, 6))
                   for x, y in ref]
            lons = [q[0] for q in out]
            lats = [q[1] for q in out]
            c.execute(
                "UPDATE lines SET pts_raw=COALESCE(pts_raw, pts), pts=?, support=?,"
                " n_pts=?, length_km=?, minlon=?, minlat=?, maxlon=?, maxlat=?"
                " WHERE id=?",
                (json.dumps(out), round(sup, 3), len(out),
                 round(poly_length_km(out), 3),
                 min(lons), min(lats), max(lons), max(lats), rid))
            if sup < 0.35:
                nlow += 1
        c.commit()
        print(f"[{sid}] refined {len(rows)} lines, {nlow} low-support (<0.35)",
              flush=True)


def cmd_dedupe(args):
    """Overlapping windows trace the same ink twice; drop the shorter of two
    same-kind segments when it lies along the longer one. Tol ~0.012 deg
    (~1.3 km) -- generous because vertex placement jitters tile to tile."""
    c = db()
    init_db(c)
    c.execute("DROP TABLE IF EXISTS lines_dedup")
    c.execute("""CREATE TABLE lines_dedup(
        id INTEGER PRIMARY KEY, sheet TEXT, kind TEXT, style TEXT, name TEXT,
        n_pts INTEGER, length_km REAL, pts TEXT,
        minlon REAL, minlat REAL, maxlon REAL, maxlat REAL)""")
    rows = c.execute(
        "SELECT id, sheet, kind, style, name, n_pts, length_km, pts,"
        " minlon, minlat, maxlon, maxlat FROM lines"
        " WHERE support IS NULL OR support >= 0.35 ORDER BY length_km DESC").fetchall()
    # bucket by 0.25-deg cell of bbox center for locality
    from collections import defaultdict
    buckets = defaultdict(list)
    kept = []
    TOL = 0.012
    for r in rows:
        pts = json.loads(r[7])
        cx = (r[8] + r[10]) / 2
        cy = (r[9] + r[11]) / 2
        key0 = (int(cx / 0.25), int(cy / 0.25))
        dup = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for kr, kpts in buckets[(key0[0] + dx, key0[1] + dy)]:
                    if kr[2] != r[2]:
                        continue
                    # bbox proximity gate
                    if r[8] > kr[10] + TOL or r[10] < kr[8] - TOL or \
                       r[9] > kr[11] + TOL or r[11] < kr[9] - TOL:
                        continue
                    if _seg_dup(pts, kpts, TOL):
                        dup = True
                        break
                if dup:
                    break
            if dup:
                break
        if not dup:
            buckets[key0].append((r, pts))
            kept.append(r)
    c.executemany(
        "INSERT INTO lines_dedup(id,sheet,kind,style,name,n_pts,length_km,pts,"
        " minlon,minlat,maxlon,maxlat) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", kept)
    c.commit()
    print(f"lines_dedup: kept {len(kept)} of {len(rows)}")


def _rdp(pts, eps):
    """Douglas-Peucker in degrees (lon scaled by cos lat is overkill here;
    eps is small enough that isotropic degrees are fine for simplification)."""
    if len(pts) < 3:
        return list(pts)
    ax, ay = pts[0]
    bx, by = pts[-1]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    imax, dmax = 0, -1.0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        if L2 == 0:
            d2 = (px - ax) ** 2 + (py - ay) ** 2
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
            d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
        if d2 > dmax:
            imax, dmax = i, d2
    if dmax <= eps * eps:
        return [pts[0], pts[-1]]
    left = _rdp(pts[:imax + 1], eps)
    return left[:-1] + _rdp(pts[imax:], eps)


def _bearing(p, q):
    return math.atan2(q[1] - p[1], q[0] - p[0])


def _ang_ok(chain_pts, at_end, next_dir, min_cos=0.4):
    """Does continuing from this chain endpoint in next_dir keep direction?
    Uses the last ~2 vertices for the chain's own exit bearing."""
    if at_end:
        a = chain_pts[-2] if len(chain_pts) >= 2 else chain_pts[0]
        b = chain_pts[-1]
    else:
        a = chain_pts[1] if len(chain_pts) >= 2 else chain_pts[-1]
        b = chain_pts[0]
    own = _bearing(a, b)
    return math.cos(own - next_dir) >= min_cos


def cmd_stitch(args):
    """Join deduped segments into continuous lines across tile and sheet
    boundaries -> lines_stitched.

    conservative by construction: two same-kind segments are joined only
    when (a) an endpoint of one is within TOL of an endpoint of the other
    (TOL measured from the data: p25 of nearest endpoint gaps ~2.8 km, so
    3.3 km catches genuine continuations while staying well under the
    ~28 km tile pitch), (b) both segments' bearings at the junction agree
    (cos >= 0.4, ~66deg), and (c) that pairing is the best available for
    both endpoints (greedy by gap, each endpoint consumed once). Ambiguity therefore breaks the
    chain rather than guessing -- a wrong join is worse than a gap.
    Names: most frequent non-null name along the chain wins; all seen
    names kept in names_all. Geometry simplified with Douglas-Peucker
    (~150 m) after joining."""
    c = db()
    init_db(c)
    if not c.execute("SELECT count(*) FROM sqlite_master WHERE name='lines_dedup'").fetchone()[0] \
       or not c.execute("SELECT count(*) FROM lines_dedup").fetchone()[0]:
        cmd_dedupe(args)
    TOL = 0.03           # deg, ~3.3 km max gap (from endpoint-gap stats)
    EPS = 0.0014         # deg, ~150 m simplification
    rows = c.execute(
        "SELECT id, sheet, kind, style, name, pts FROM lines_dedup").fetchall()
    from collections import defaultdict, Counter
    chains = {}   # cid -> dict(pts, kind, styles, names, segs, sheets)
    for rid, sheet, kind, style, name, pts in rows:
        chains[rid] = {"pts": [tuple(p) for p in json.loads(pts)], "kind": kind,
                       "styles": Counter([style] if style else []),
                       "names": Counter([name] if name else []),
                       "segs": 1, "sheets": {sheet}}

    def endpoints(cid):
        p = chains[cid]["pts"]
        return (p[0], p[-1])

    merged = True
    while merged:
        merged = False
        # spatial hash of endpoints, rebuilt each sweep (chains shrink fast)
        cell = defaultdict(list)   # (kind, ix, iy) -> [(cid, at_end)]
        for cid in chains:
            s, e = endpoints(cid)
            k = chains[cid]["kind"]
            cell[(k, int(s[0] / TOL), int(s[1] / TOL))].append((cid, False))
            cell[(k, int(e[0] / TOL), int(e[1] / TOL))].append((cid, True))
        # candidate joins: (gap, cid_a, end_a, cid_b, end_b)
        cands = []
        seen = set()
        for (k, ix, iy), lst in cell.items():
            near = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    near.extend(cell.get((k, ix + dx, iy + dy), ()))
            for cid, at_end in lst:
                p = endpoints(cid)[1 if at_end else 0]
                for cid2, at_end2 in near:
                    if cid2 == cid:
                        continue
                    key = tuple(sorted([(cid, at_end), (cid2, at_end2)]))
                    if key in seen:
                        continue
                    seen.add(key)
                    q = endpoints(cid2)[1 if at_end2 else 0]
                    gap = math.hypot(p[0] - q[0], p[1] - q[1])
                    if gap > TOL:
                        continue
                    # direction continuity both ways across the junction
                    d = _bearing(p, q) if gap > 1e-9 else None
                    if d is not None:
                        if not _ang_ok(chains[cid]["pts"], at_end, d):
                            continue
                        if not _ang_ok(chains[cid2]["pts"], at_end2, d + math.pi):
                            continue
                    cands.append((gap, cid, at_end, cid2, at_end2))
        cands.sort(key=lambda t: t[0])
        used = set()
        for gap, a, ae, b, be in cands:
            if (a, ae) in used or (b, be) in used or a not in chains or b not in chains:
                continue
            ca, cb = chains[a], chains[b]
            pa = ca["pts"] if ae else list(reversed(ca["pts"]))   # ends at junction
            pb = cb["pts"] if not be else list(reversed(cb["pts"]))  # starts at junction
            ca["pts"] = pa + pb
            ca["styles"] += cb["styles"]
            ca["names"] += cb["names"]
            ca["segs"] += cb["segs"]
            ca["sheets"] |= cb["sheets"]
            used.add((a, ae)); used.add((b, be))
            # b's far endpoint now belongs to a; conservatively retire both
            # from this sweep -- the next sweep re-indexes everything.
            del chains[b]
            merged = True

    c.execute("DROP TABLE IF EXISTS lines_stitched")
    c.execute("""CREATE TABLE lines_stitched(
        id INTEGER PRIMARY KEY, kind TEXT, style TEXT, name TEXT,
        names_all TEXT, n_segs INTEGER, n_sheets INTEGER, sheets TEXT,
        year_min INTEGER, year_max INTEGER,
        n_pts INTEGER, length_km REAL, pts TEXT,
        minlon REAL, minlat REAL, maxlon REAL, maxlat REAL)""")
    sheet_year = dict(c.execute("SELECT id, year FROM sheets")) if c.execute(
        "SELECT count(*) FROM sqlite_master WHERE name='sheets'").fetchone()[0] else {}
    out = []
    for cid, ch in chains.items():
        pts = _rdp(ch["pts"], EPS)
        if len(pts) < 2:
            continue
        km = poly_length_km(pts)
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        name = ch["names"].most_common(1)[0][0] if ch["names"] else None
        style = ch["styles"].most_common(1)[0][0] if ch["styles"] else None
        names_all = json.dumps(sorted(ch["names"])) if len(ch["names"]) > 1 else None
        yrs = [sheet_year[sid] for sid in ch["sheets"] if sheet_year.get(sid)]
        out.append((ch["kind"], style, name, names_all, ch["segs"],
                    len(ch["sheets"]), ",".join(sorted(ch["sheets"])),
                    min(yrs) if yrs else None, max(yrs) if yrs else None,
                    len(pts), round(km, 3),
                    json.dumps([[round(x, 6), round(y, 6)] for x, y in pts]),
                    min(lons), min(lats), max(lons), max(lats)))
    c.executemany(
        "INSERT INTO lines_stitched(kind,style,name,names_all,n_segs,n_sheets,"
        " sheets,year_min,year_max,n_pts,length_km,pts,minlon,minlat,maxlon,maxlat)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    c.commit()
    ndd = c.execute("SELECT count(*) FROM lines_dedup").fetchone()[0]
    multi = sum(1 for r in out if r[4] > 1)
    xsheet = sum(1 for r in out if r[5] > 1)
    print(f"lines_stitched: {len(out)} lines from {ndd} segments "
          f"({multi} joined from >1 seg, {xsheet} span sheets)")
    for kind, n, km in c.execute(
            "SELECT kind, count(*), round(sum(length_km)) FROM lines_stitched"
            " GROUP BY 1 ORDER BY 3 DESC"):
        print(f"  {kind:12s} {n:6d}  {km:8.0f} km")


def cmd_symbols(args):
    """Dump captured symbol crops to a directory as PNGs named
    id_lon_lat.png plus an index.csv -- the bulk-categorization input."""
    c = db()
    init_db(c)
    os.makedirs(args.out, exist_ok=True)
    import csv
    rows = c.execute(
        "SELECT id, sheet, descr, lon, lat, category, crop FROM symbols"
        " WHERE category IS " + ("NOT NULL" if args.categorized else "NULL")).fetchall()
    with open(os.path.join(args.out, "index.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "sheet", "descr", "lon", "lat", "category", "file"])
        for i, sid, d, lon, lat, cat, blob in rows:
            fn = f"{i}_{lon:.5f}_{lat:.5f}.png"
            with open(os.path.join(args.out, fn), "wb") as g:
                g.write(blob)
            w.writerow([i, sid, d, lon, lat, cat or "", fn])
    print(f"{len(rows)} symbol crops -> {args.out}")


SYM_TAXONOMY = ("water", "settlement", "peak", "trig_point", "tree",
                "enclosure", "grave", "fort", "church", "station", "ruin",
                "landmark", "unknown", "junk")

SYM_PROMPT = """Each numbered cell in this contact sheet is a 192x192 px crop from a \
Sudan Survey 1:250,000 map (1915-1968), centered on one point symbol. \
Classify the CENTER symbol of each cell using the series' own conventional \
signs (from the sheet legends):
- water: any water point -- small circle/circled dot WITH a letter code \
beside it: W (bir/'idd well), R (rahad lake), P (pool), T (tamad waterhole), \
F (fula rain pool), H (hafir rain pond), G.W.B (govt water bore). On \
1940s-60s sheets a plain solid black dot labelled near water words is also \
"well or place where water may be found".
- settlement: town/village marker -- filled square block (1st/2nd \
importance), circled dot WITHOUT a letter code next to a place name \
(3rd/4th), hut/house symbols, temporary village (small horseshoe), deserted \
village (dotted cluster).
- peak: hill/mountain -- hachured or form-lined mound, often "J." (jebel) \
name or a height number.
- trig_point: survey mark -- solid triangle (triangulation/astro point), \
dotted circle (intersected point), beacon or cairn.
- tree: single tree symbols -- palm, dom palm, tebeldi (baobab), general \
tree; also scattered-scrub tufts.
- enclosure: morah (cattle enclosure), zariba, camp ring.
- grave: cemetery, site of graves, qubba (domed tomb), pyramid.
- fort: fort (star or square outline symbol), ancient fort.
- church: church, mosque, mission station cross.
- station: manned post abbreviations -- R.H. (rest house), C.C. (chief's \
court), P. (police post), T.O./P (telegraph/post office), wireless station, \
landing ground, meshra M (landing place), ford, ferry.
- ruin: ruins.
- landmark: other genuine point feature you can see but none of the above.
- unknown: a real symbol you cannot identify.
- junk: not a point symbol (text fragment only, line crossing, blank paper, \
smudge, border tick).
Judge by the SYMBOL INK at the cell center, not by nearby text alone.
Answer one line per cell: <number>: <category>. Nothing else."""


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        return e


# category -> label kinds that can plausibly name it, in preference order
LINK_KINDS = {"water": ("water", "place"), "peak": ("hill",),
              "settlement": ("place",), "fort": ("place", "other"),
              "church": ("place", "other"), "station": ("place", "other"),
              "ruin": ("place", "other"), "grave": ("place", "other"),
              "enclosure": ("place", "other"), "landmark": ("place", "other")}


def cmd_link(args):
    """Join the three layers by proximity: give symbols the name of the
    nearest compatible OCR label, and name unnamed watercourse/track lines
    from water/route labels along their path. Pure geometry, no LLM.
    Idempotent: recomputes all links from scratch each run."""
    c = db()
    init_db(c)
    for ddl in ("ALTER TABLE symbols ADD COLUMN name TEXT",
                "ALTER TABLE symbols ADD COLUMN name_dist_km REAL",
                "ALTER TABLE lines_stitched ADD COLUMN name_src TEXT"):
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass
    # bucket-grid the labels once (0.02 deg cells)
    CELL = 0.02
    grid = {}
    for text, kind, lon, lat in c.execute(
            "SELECT text, kind, lon, lat FROM labels_dedup"
            " WHERE COALESCE(category,'') NOT IN ('junk','collar','note')"):
        grid.setdefault((int(lon / CELL), int(lat / CELL)), []).append(
            (text, kind, lon, lat))

    def near(lon, lat, kinds, max_deg):
        best = None
        ci, cj = int(lon / CELL), int(lat / CELL)
        r = int(max_deg / CELL) + 1
        for i in range(ci - r, ci + r + 1):
            for j in range(cj - r, cj + r + 1):
                for text, kind, llon, llat in grid.get((i, j), ()):
                    if kind not in kinds:
                        continue
                    d2 = (llon - lon) ** 2 + (llat - lat) ** 2
                    # prefer earlier kinds: penalize later ones slightly
                    d2 *= 1 + kinds.index(kind)
                    if d2 <= max_deg ** 2 and (best is None or d2 < best[0]):
                        best = (d2, text, llon, llat)
        return best

    # --- symbols -> nearest compatible label (<= ~600 m) ---
    nsym = 0
    ups = []
    for sid, cat, lon, lat in c.execute(
            "SELECT id, category, lon, lat FROM symbols"
            " WHERE category NOT IN ('junk','unknown','tree')"):
        kinds = LINK_KINDS.get(cat)
        if not kinds:
            continue
        b = near(lon, lat, kinds, 0.006)
        if b:
            ups.append((b[1], round(111 * b[0] ** 0.5, 3), sid))
            nsym += 1
    c.execute("UPDATE symbols SET name=NULL, name_dist_km=NULL")
    c.executemany("UPDATE symbols SET name=?, name_dist_km=? WHERE id=?", ups)
    total_sym = c.execute("SELECT count(*) FROM symbols WHERE category NOT IN"
                          " ('junk','unknown','tree')").fetchone()[0]
    print(f"symbols: {nsym}/{total_sym} named", flush=True)

    # --- unnamed lines -> label voted by vertices along the path ---
    LKIND = {"watercourse": ("water",), "track": ("route",),
             "road": ("route",), "railway": ("route",)}
    nline = 0
    ups = []
    for lid, kind, pts in c.execute(
            "SELECT id, kind, pts FROM lines_stitched"
            " WHERE (name IS NULL OR name = '') AND kind IN"
            " ('watercourse','track','road','railway')"):
        votes = {}
        for lon, lat in json.loads(pts)[::2]:
            b = near(lon, lat, LKIND[kind], 0.008)
            if b:
                votes[b[1]] = votes.get(b[1], 0) + 1
        if votes:
            name, n = max(votes.items(), key=lambda kv: kv[1])
            if n >= 2:  # one grazing vertex is coincidence, not a name
                ups.append((name, lid))
                nline += 1
    c.executemany(
        "UPDATE lines_stitched SET name=?, name_src='label' WHERE id=?", ups)
    c.commit()
    print(f"lines: {nline} previously-unnamed lines named from labels",
          flush=True)


def cmd_catsym(args):
    """Vision-LLM categorization of captured symbol crops, in contact-sheet
    batches. Resumable: only category IS NULL rows are sent; a batch that
    fails parses stays NULL and is retried on the next invocation."""
    c = db()
    init_db(c)
    rows = c.execute("SELECT id, crop FROM symbols WHERE category IS NULL"
                     " ORDER BY id").fetchall()
    print(f"{len(rows)} uncategorized symbols (model {MODEL})", flush=True)
    B, COLS, CELL = 25, 5, 192
    from PIL import ImageDraw
    from concurrent.futures import ThreadPoolExecutor

    def classify(batch):
        nrows = (len(batch) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * (CELL + 24), nrows * (CELL + 24)),
                          (255, 255, 255))
        d = ImageDraw.Draw(sheet)
        for n, (rid, blob) in enumerate(batch):
            im = Image.open(io.BytesIO(blob)).convert("RGB")
            x = (n % COLS) * (CELL + 24) + 12
            y = (n // COLS) * (CELL + 24) + 20
            sheet.paste(im, (x, y))
            d.rectangle([x - 1, y - 1, x + CELL, y + CELL], outline=(200, 0, 0))
            d.text((x, y - 14), str(n + 1), fill=(200, 0, 0))
        buf = io.BytesIO()
        sheet.save(buf, format="PNG")
        # tracer SYS_PROMPT suppressed: with it the model answered the wrong
        # question (returned nothing); reasoning needs ~7k tokens, so 10k cap.
        content, _fin, _u = call_llm(
            buf.getvalue(), hint_text=SYM_PROMPT, max_tokens=10000,
            sys_prompt="You classify point symbols on historical maps.")
        got = {}
        for m in re.finditer(r"(\d+)\s*[:.\-]\s*([a-z_]+)", content.lower()):
            n, cat = int(m.group(1)), m.group(2)
            if 1 <= n <= len(batch) and cat in SYM_TAXONOMY:
                got[n] = cat
        return [(cat, batch[n - 1][0]) for n, cat in got.items()]

    batches = [rows[i:i + B] for i in range(0, len(rows), B)]
    done = sent = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for batch, res in zip(batches, ex.map(
                lambda b: _try(classify, b), batches)):
            sent += len(batch)
            if isinstance(res, Exception):
                print(f"  ERR batch: {type(res).__name__}: {res}", flush=True)
                continue
            c.executemany("UPDATE symbols SET category=? WHERE id=?", res)
            c.commit()
            done += len(res)
            if sent % (B * 10) == 0:
                print(f"  {sent}/{len(rows)} sent, {done} categorized",
                      flush=True)
    left = c.execute(
        "SELECT count(*) FROM symbols WHERE category IS NULL").fetchone()[0]
    print(f"done: {done} categorized this pass, {left} still NULL"
          + (" -- re-run to retry" if left else ""), flush=True)


def cmd_preview(args):
    """Render traced lines over a downscaled sheet for visual QA."""
    from PIL import ImageDraw
    c = db()
    path = os.path.join(GEO_DIR, args.sheet + "_geo.tif")
    gt, size = sheet_geotransform(path)
    img = Image.open(path)
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, (0, 0), img)
        img = bg
    else:
        img = img.convert("RGB")
    sc = 4096 / max(size)
    img = img.resize((int(size[0] * sc), int(size[1] * sc)), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    cols = {"track": (0, 120, 255), "road": (255, 0, 0), "railway": (160, 0, 200),
            "telegraph": (255, 150, 0), "watercourse": (0, 170, 0),
            "boundary": (220, 0, 120), "other": (120, 120, 120)}
    tbl = "lines_dedup" if not args.raw and c.execute(
        "SELECT count(*) FROM sqlite_master WHERE name='lines_dedup'").fetchone()[0] else "lines"
    n = 0
    for kind, pts in c.execute(f"SELECT kind, pts FROM {tbl} WHERE sheet=?", (args.sheet,)):
        lonlat = json.loads(pts)
        px = [(((lon - gt[0]) / gt[1]) * sc, ((lat - gt[3]) / gt[5]) * sc)
              for lon, lat in lonlat]
        d.line(px, fill=cols.get(kind, (0, 0, 0)), width=5)
        n += 1
    img.save(args.out)
    print(f"{args.out}: {n} lines from {tbl}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--limit", type=int)
    r.add_argument("--sheet")
    sub.add_parser("status")
    sub.add_parser("dedupe")
    sub.add_parser("stitch")
    rf = sub.add_parser("refine")
    rf.add_argument("--all", action="store_true")
    cy = sub.add_parser("catsym")
    cy.add_argument("--workers", type=int, default=8)
    sub.add_parser("link")
    s = sub.add_parser("symbols")
    s.add_argument("out")
    s.add_argument("--categorized", action="store_true")
    p = sub.add_parser("preview")
    p.add_argument("sheet")
    p.add_argument("out")
    p.add_argument("--raw", action="store_true")
    args = ap.parse_args()
    {"run": cmd_run, "status": cmd_status, "dedupe": cmd_dedupe,
     "stitch": cmd_stitch, "refine": cmd_refine, "symbols": cmd_symbols, "preview": cmd_preview,
     "catsym": cmd_catsym, "link": cmd_link}[args.cmd](args)


if __name__ == "__main__":
    main()
