#!/usr/bin/env python3
"""OCR + geocode the Sudan 1:250k historical map labels.

Walks the georeferenced sheets in data/histmaps/geo/, cuts each into
overlapping 2048px native windows (downscaled to 1024 for the model),
sends each to a vision LLM via the exe.dev LLM integration
(https://llm.int.exe.xyz), and stores every transcribed label with its
WGS84 lon/lat in data/histmaps/labels.sqlite3.

Model choice (2026-08-13 bake-off on cs000009 crops):
  fireworks/muse-glimmer-30b found the most labels (incl. rotated
  "District Boundary" and collar text), transcribed 1930s letterpress
  correctly where gpt-5.6-luna hallucinated ("Khartoum", "White Nile")
  and grok/sol mangled, and its pixel centers were within ~20 px.
  It is also the cheapest vision model on the gateway. Do not "upgrade"
  without re-running the comparison.

Invariants honoured (AGENTS.md):
  * a tile that errors is recorded as state='error' and retried on the
    next run -- a no-op must not read as an answer. Only 'done' and
    'blank' are terminal.
  * counts are derived, never typed: `status` reads the DB.
  * resumable: tiles table is the ledger; rerunning skips finished work.
  * XSA priority: sheets intersecting the study bbox are processed first.

Usage:
  python3 ocr_labels.py run [--workers 3] [--limit N] [--sheet cs000009]
  python3 ocr_labels.py status
  python3 ocr_labels.py dedupe        # build labels_dedup from labels
  python3 ocr_labels.py query LON LAT [RADIUS_KM]
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

TILE = 2048          # native pixels per window
STRIDE = 1792        # 256 px overlap so edge labels appear whole in one tile
SEND = 1024          # downscaled size sent to the model
INK_MIN = 0.0015     # fraction of dark pixels below which a tile is 'blank'
MAX_TOKENS = 6000
RETRY_WAIT = 600     # s between no-progress passes (allocation empty, gateway down)
MAX_STALLED = 12     # give up after this many consecutive no-progress passes (~2 h)

XSA_BBOX = (22.70, 4.25, 31.30, 10.97)  # W,S,E,N -- study area first

SYS_PROMPT = (
    "Scanned 1930s survey map of Sudan, black ink on white, 1024x1024 px. "
    "Transcribe EVERY text label (including rotated and edge-cut ones). "
    'One JSON object per line: {"text":"...","cx":int,"cy":int,'
    '"kind":"place|water|hill|route|boundary|other","partial":bool}. '
    "cx,cy = label pixel center. Use \"?\" for unreadable chars. "
    "If there is no text at all output exactly: NONE. Only JSON lines."
)


def db():
    c = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db(c):
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS tiles(
          sheet TEXT NOT NULL, tx INTEGER NOT NULL, ty INTEGER NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending',  -- pending|blank|done|error
          ink REAL, n_labels INTEGER, error TEXT, model TEXT,
          prompt_tokens INTEGER, completion_tokens INTEGER,
          updated_at TEXT DEFAULT (datetime('now')),
          PRIMARY KEY(sheet, tx, ty));
        CREATE TABLE IF NOT EXISTS labels(
          id INTEGER PRIMARY KEY,
          sheet TEXT NOT NULL, tx INTEGER, ty INTEGER,
          text TEXT NOT NULL, kind TEXT, partial INTEGER,
          px REAL, py REAL,          -- native sheet pixel coords
          lon REAL NOT NULL, lat REAL NOT NULL,
          model TEXT);
        CREATE INDEX IF NOT EXISTS idx_labels_lonlat ON labels(lon, lat);
        CREATE INDEX IF NOT EXISTS idx_labels_text ON labels(text COLLATE NOCASE);
        -- Full-text index for fast text search (the API's q= filter would
        -- otherwise be a LIKE '%..%' scan). Kept in sync by triggers, so the
        -- long-running OCR writer maintains it for free once it exists.
        CREATE VIRTUAL TABLE IF NOT EXISTS labels_fts USING fts5(
          text, content='labels', content_rowid='id', tokenize='unicode61');
        CREATE TRIGGER IF NOT EXISTS labels_ai AFTER INSERT ON labels BEGIN
          INSERT INTO labels_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS labels_ad AFTER DELETE ON labels BEGIN
          INSERT INTO labels_fts(labels_fts, rowid, text) VALUES('delete', old.id, old.text);
        END;
        """
    )
    # Backfill the FTS index for rows inserted before it existed. 'rebuild'
    # re-derives the whole index from the content table; cheap at this size.
    n_raw = c.execute("SELECT count(*) FROM labels").fetchone()[0]
    n_fts = c.execute("SELECT count(*) FROM labels_fts").fetchone()[0]
    if n_fts != n_raw:
        c.execute("INSERT INTO labels_fts(labels_fts) VALUES('rebuild')")
    c.commit()


def sheet_geotransform(path):
    """Read the 6-coeff geotransform without gdal_array (numpy 2 breakage)."""
    from osgeo import gdal  # noqa: gdal core works; gdal_array does not
    ds = gdal.Open(path)
    gt = ds.GetGeoTransform()
    size = (ds.RasterXSize, ds.RasterYSize)
    ds = None
    return gt, size


def list_sheets():
    """[(sheet_id, path, gt, (w,h), priority)] -- XSA sheets first."""
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
    """-> (PIL 1024 RGB on white, ink fraction) or (None, ink) if blank."""
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


def call_llm(png_bytes, max_tokens=MAX_TOKENS):
    img64 = base64.b64encode(png_bytes).decode()
    body = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64," + img64}}]},
        ],
    }).encode()
    req = urllib.request.Request(LLM_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=300))
    msg = r["choices"][0]["message"]
    usage = r.get("usage", {})
    return msg.get("content") or "", r["choices"][0].get("finish_reason"), usage


JSON_RE = re.compile(r"\{[^{}]*\}")


def parse_labels(content):
    """Tolerant JSON-lines parse. Returns list of dicts; [] for NONE."""
    labels = []
    for m in JSON_RE.finditer(content):
        try:
            o = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        t = str(o.get("text", "")).strip()
        if not t or set(t) <= set("?. "):
            continue
        try:
            cx, cy = float(o["cx"]), float(o["cy"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= cx <= SEND and 0 <= cy <= SEND):
            continue
        labels.append({
            "text": t,
            "cx": cx, "cy": cy,
            "kind": o.get("kind") or "other",
            "partial": 1 if o.get("partial") else 0,
        })
    return labels


def process_tile(img, gt, sid, tx, ty):
    """Returns dict describing outcome; raises nothing."""
    tile_img, ink = render_tile(img, tx, ty)
    if tile_img is None:
        return {"state": "blank", "ink": ink, "labels": [], "usage": {}}
    buf = io.BytesIO()
    tile_img.save(buf, "PNG")
    png = buf.getvalue()
    try:
        content, finish, usage = call_llm(png)
        if not content.strip() and finish == "length":
            # runaway reasoning ate the budget; one retry with more headroom
            content, finish, usage = call_llm(png, max_tokens=MAX_TOKENS * 2)
    except Exception as e:  # network/HTTP/JSON -- record, retry next run
        return {"state": "error", "ink": ink, "labels": [],
                "error": f"{type(e).__name__}: {e}", "usage": {}}
    if not content.strip():
        return {"state": "error", "ink": ink, "labels": [],
                "error": f"empty content (finish={finish})", "usage": usage}
    labels = parse_labels(content)
    scale = TILE / SEND
    out = []
    for lb in labels:
        px = tx + lb["cx"] * scale
        py = ty + lb["cy"] * scale
        lon = gt[0] + gt[1] * px + gt[2] * py
        lat = gt[3] + gt[4] * px + gt[5] * py
        out.append((sid, tx, ty, lb["text"], lb["kind"], lb["partial"],
                    px, py, lon, lat, MODEL))
    return {"state": "done", "ink": ink, "labels": out, "usage": usage}


def cmd_run(args):
    c = db()
    init_db(c)
    sheets = list_sheets()
    if args.sheet:
        sheets = [s for s in sheets if s[0] == args.sheet]
        if not sheets:
            sys.exit(f"sheet {args.sheet} not found in {GEO_DIR}")

    # enqueue all tiles (idempotent)
    for sid, path, gt, size, pri in sheets:
        c.executemany(
            "INSERT OR IGNORE INTO tiles(sheet,tx,ty) VALUES(?,?,?)",
            [(sid, tx, ty) for tx, ty in tile_grid(size)])
    c.commit()

    # Self-healing loop: a pass sweeps everything pending or errored; errored
    # tiles are transient by construction (402 allocation-empty, 502, TLS
    # resets -- the model itself never marks a tile terminal), so the run
    # keeps sweeping until the queue is clean. A pass that fixes *nothing*
    # means the failure is environmental (allocation still empty, gateway
    # down), so we wait RETRY_WAIT and try again rather than hammering; after
    # MAX_STALLED consecutive no-progress passes we stop and say so loudly --
    # exiting 0 with work outstanding would be a no-op reading as an answer.
    stalled = 0
    while True:
        attempted, succeeded = run_pass(c, sheets, args)
        if args.limit:  # bounded test run: one pass, no healing
            break
        remaining = c.execute(
            "SELECT count(*) FROM tiles WHERE state IN ('pending','error')"
            + (" AND sheet=?" if args.sheet else ""),
            (args.sheet,) if args.sheet else ()).fetchone()[0]
        if remaining == 0:
            print("queue clean: all tiles done or blank", flush=True)
            break
        if succeeded == 0:
            stalled += 1
            if stalled >= MAX_STALLED:
                print(f"STALLED: {remaining} tiles still failing after "
                      f"{MAX_STALLED} no-progress passes -- giving up. "
                      f"Check token allocation / gateway, then re-run.",
                      flush=True)
                sys.exit(1)
            print(f"pass fixed nothing ({remaining} left); waiting "
                  f"{RETRY_WAIT}s before retry {stalled}/{MAX_STALLED}",
                  flush=True)
            time.sleep(RETRY_WAIT)
        else:
            stalled = 0
            print(f"pass done: {succeeded}/{attempted} ok, "
                  f"{remaining} remaining; sweeping again", flush=True)

    c.commit()
    # Finalize the queryable artefacts. The FTS index is trigger-maintained
    # (always current), but labels_dedup is a batch product and would
    # otherwise lag every new label until someone remembered to run `dedupe`
    # by hand -- the API prefers labels_dedup when non-empty, so a stale one
    # silently hides new sheets. Rebuild it whenever this run added anything.
    if not args.limit:
        print("rebuilding labels_dedup + optimizing indexes...", flush=True)
        cmd_dedupe(args)
        c.execute("INSERT INTO labels_fts(labels_fts) VALUES('optimize')")
        c.execute("ANALYZE")
        c.commit()
    cmd_status(args)


def run_pass(c, sheets, args):
    """One sweep over pending+error tiles. Returns (attempted, succeeded)."""
    lock = threading.Lock()
    done = [0]
    ok = [0]
    t0 = time.time()

    for sid, path, gt, size, pri in sheets:
        todo = c.execute(
            "SELECT tx,ty FROM tiles WHERE sheet=? AND state IN ('pending','error')"
            " ORDER BY ty,tx", (sid,)).fetchall()
        if not todo:
            continue
        if args.limit and done[0] >= args.limit:
            break
        img = Image.open(path)
        img.load()
        print(f"[{sid}] {len(todo)} tiles (priority={pri})", flush=True)

        def work(txy):
            tx, ty = txy
            res = process_tile(img, gt, sid, tx, ty)
            with lock:
                u = res.get("usage") or {}
                c.execute(
                    "UPDATE tiles SET state=?, ink=?, n_labels=?, error=?,"
                    " model=?, prompt_tokens=?, completion_tokens=?,"
                    " updated_at=datetime('now') WHERE sheet=? AND tx=? AND ty=?",
                    (res["state"], res["ink"], len(res["labels"]),
                     res.get("error"), MODEL,
                     u.get("prompt_tokens"), u.get("completion_tokens"),
                     sid, tx, ty))
                if res["labels"]:
                    # clear any labels from a previous errored attempt of this tile
                    c.execute("DELETE FROM labels WHERE sheet=? AND tx=? AND ty=?",
                              (sid, tx, ty))
                    c.executemany(
                        "INSERT INTO labels(sheet,tx,ty,text,kind,partial,px,py,lon,lat,model)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?)", res["labels"])
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
    rows = c.execute("SELECT state, count(*) FROM tiles GROUP BY 1").fetchall()
    print(dict(rows))
    nl = c.execute("SELECT count(*), count(DISTINCT sheet) FROM labels").fetchone()
    print(f"labels: {nl[0]} across {nl[1]} sheets")
    tok = c.execute("SELECT sum(prompt_tokens), sum(completion_tokens) FROM tiles").fetchone()
    print(f"tokens: prompt={tok[0]} completion={tok[1]}")
    err = c.execute("SELECT count(*) FROM tiles WHERE state='error'").fetchone()[0]
    if err:
        print(f"NOTE: {err} errored tiles will be retried on next run")


def cmd_dedupe(args):
    """Overlapping windows see the same label twice; merge near-duplicates.
    Same normalized text within ~1.2 km collapses to the centroid."""
    c = db()
    init_db(c)
    c.executescript(
        """
        DROP TABLE IF EXISTS labels_dedup;
        CREATE TABLE labels_dedup(
          id INTEGER PRIMARY KEY, text TEXT, kind TEXT,
          lon REAL, lat REAL, n_src INTEGER, sheets TEXT);
        """)
    rows = c.execute(
        "SELECT text, kind, lon, lat, sheet FROM labels ORDER BY text, lon, lat"
    ).fetchall()
    def norm(t):
        return re.sub(r"[^a-z0-9]", "", t.lower())
    clusters = []  # (norm, [lons],[lats], kinds, sheets, texts)
    by_norm = {}
    R = 0.011  # deg ~ 1.2 km
    for text, kind, lon, lat, sheet in rows:
        k = norm(text)
        if not k:
            continue
        hit = None
        for cl in by_norm.get(k, []):
            if abs(cl["lon"] - lon) < R and abs(cl["lat"] - lat) < R:
                hit = cl
                break
        if hit is None:
            hit = {"lon": lon, "lat": lat, "n": 0, "texts": {}, "kinds": {}, "sheets": set()}
            by_norm.setdefault(k, []).append(hit)
            clusters.append(hit)
        hit["n"] += 1
        hit["lon"] += (lon - hit["lon"]) / hit["n"]
        hit["lat"] += (lat - hit["lat"]) / hit["n"]
        hit["texts"][text] = hit["texts"].get(text, 0) + 1
        hit["kinds"][kind] = hit["kinds"].get(kind, 0) + 1
        hit["sheets"].add(sheet)
    for cl in clusters:
        text = max(cl["texts"], key=cl["texts"].get)
        kind = max(cl["kinds"], key=cl["kinds"].get)
        c.execute(
            "INSERT INTO labels_dedup(text,kind,lon,lat,n_src,sheets) VALUES(?,?,?,?,?,?)",
            (text, kind, cl["lon"], cl["lat"], cl["n"], ",".join(sorted(cl["sheets"]))))
    c.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_dedup_lonlat ON labels_dedup(lon, lat);
        CREATE INDEX IF NOT EXISTS idx_dedup_text ON labels_dedup(text COLLATE NOCASE);
        """)
    c.commit()
    n = c.execute("SELECT count(*) FROM labels_dedup").fetchone()[0]
    print(f"labels_dedup: {n} (from {len(rows)} raw)")


def cmd_query(args):
    c = db()
    r_deg = args.radius_km / 111.0
    table = "labels_dedup" if c.execute(
        "SELECT count(*) FROM sqlite_master WHERE name='labels_dedup'").fetchone()[0] else "labels"
    rows = c.execute(
        f"SELECT text, kind, lon, lat FROM {table}"
        " WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?",
        (args.lon - r_deg, args.lon + r_deg, args.lat - r_deg, args.lat + r_deg)).fetchall()
    rows.sort(key=lambda r: (r[2] - args.lon) ** 2 + (r[3] - args.lat) ** 2)
    for text, kind, lon, lat in rows[:30]:
        d = math.hypot((lon - args.lon) * 111 * math.cos(math.radians(args.lat)),
                       (lat - args.lat) * 111)
        print(f"{d:6.1f} km  {kind:9s} {text}  ({lon:.4f},{lat:.4f})")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--limit", type=int, default=0, help="max tiles this run")
    r.add_argument("--sheet", help="only this sheet id")
    sub.add_parser("status")
    sub.add_parser("dedupe")
    q = sub.add_parser("query")
    q.add_argument("lon", type=float)
    q.add_argument("lat", type=float)
    q.add_argument("radius_km", type=float, nargs="?", default=10.0)
    args = ap.parse_args()
    {"run": cmd_run, "status": cmd_status, "dedupe": cmd_dedupe,
     "query": cmd_query}[args.cmd](args)


if __name__ == "__main__":
    main()
