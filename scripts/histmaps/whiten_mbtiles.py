#!/usr/bin/env python3
"""Whiten the sudan250k MBTiles: RGB -> 255, alpha untouched.

The app renders the black-ink archive white via raster-brightness-min:1;
offline viewers on dark basemaps need the same as a file. This produces
sudan250k_white.mbtiles next to the source. Resumable: skips tiles already
written. The archive stays black on purpose (docs/agents/overlays.md) --
this is a DERIVED artifact, never a replacement.
"""
import io
import sqlite3
import sys
from multiprocessing import Pool
from pathlib import Path

from PIL import Image

SRC = Path("data/histmaps/sudan250k.mbtiles")
FINAL = Path("data/histmaps/sudan250k_white.mbtiles")
# Build under a different name and rename on completion: the server
# advertises FINAL the moment it exists, so a partial file must never
# wear the final name (a truncated answer must not look complete).
DST = FINAL.with_suffix(".building.mbtiles")
BATCH = 2000

def whiten(args):
    z, x, y, blob = args
    im = Image.open(io.BytesIO(blob)).convert("RGBA")
    a = im.getchannel("A")
    out = Image.new("RGBA", im.size, (255, 255, 255, 0))
    out.putalpha(a)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=False, compress_level=6)
    return z, x, y, buf.getvalue()

def main():
    src = sqlite3.connect(SRC)
    dst = sqlite3.connect(DST)
    dst.executescript("""
      CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT);
      CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER,
        tile_row INTEGER, tile_data BLOB);
      CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles
        (zoom_level, tile_column, tile_row);
    """)
    dst.execute("DELETE FROM metadata")
    for n, v in src.execute("SELECT name, value FROM metadata"):
        if n == "name":
            v = v + " (white ink)"
        dst.execute("INSERT INTO metadata VALUES (?,?)", (n, v))
    dst.commit()
    total = src.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
    done_rows = {(z, x, y) for z, x, y in dst.execute(
        "SELECT zoom_level, tile_column, tile_row FROM tiles")}
    print(f"{total} tiles, {len(done_rows)} already done", flush=True)
    cur = src.execute(
        "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles")
    pool = Pool(2)
    pending, done = [], len(done_rows)
    def flush():
        nonlocal pending, done
        for z, x, y, b in pool.imap_unordered(whiten, pending, 100):
            dst.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)",
                        (z, x, y, b))
        dst.commit()   # batched commits: yield the writer (invariant 16)
        done += len(pending)
        pending = []
        print(f"{done}/{total}", flush=True)
    for z, x, y, blob in cur:
        if (z, x, y) in done_rows:
            continue
        pending.append((z, x, y, blob))
        if len(pending) >= BATCH:
            flush()
    if pending:
        flush()
    # completeness check: a truncated answer must not look complete (inv. 1/8)
    n2 = dst.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
    if n2 != total:
        print(f"UNFINISHED: {n2}/{total}", flush=True)
        sys.exit(1)
    dst.close()
    DST.rename(FINAL)
    print("complete ->", FINAL, flush=True)

if __name__ == "__main__":
    main()
