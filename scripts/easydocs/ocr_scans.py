#!/usr/bin/env python3
"""Stage 2: vision-OCR the scanned pages of the EASY document dump.

Same model as the histmap OCR (fireworks/muse-glimmer-30b) and the same shape:
a resumable per-page ledger + a thread pool, because these are hundreds of
pages of scanned law and the run must survive interruption.

Ledger: pages(name, page) state pending|done|blank|error in docs.sqlite3.
A page whose transcription is suspiciously short for a scan is 'error', not
'done' -- it gets retried on the next pass (AGENTS.md invariant 1).

Pages are rendered at 150 dpi and downscaled so the long edge is 1600 px:
law scans are dense body text, not a map tile, and 1024 px loses section
numbers.

Usage:
  python3 ocr_scans.py run [--workers 12] [--limit N] [--only SUBSTR]
  python3 ocr_scans.py status
  python3 ocr_scans.py assemble        # write txt/<name>.txt from pages
"""
import argparse, base64, concurrent.futures, io, json, os, re, sqlite3, subprocess
import sys, tempfile, threading, time, urllib.request

from PIL import Image

ROOT = os.environ.get("EASYDOCS_DIR", "/home/exedev/5mp/data/easy_docs")
LLM_URL = os.environ.get("HISTMAP_LLM_URL", "https://llm.int.exe.xyz/v1/chat/completions")
MODEL = os.environ.get("EASYDOCS_OCR_MODEL", "fireworks/muse-glimmer-30b")
DPI = 150
LONG_EDGE = 1600
MAX_TOKENS = 8000
MIN_CHARS = 40          # below this, on a non-blank page, treat as failure

SYS_PROMPT = (
    "You transcribe scanned pages from South Sudanese and Sudanese documents: "
    "Acts, Bills, regulations, policies, MoUs, letters, NGO and donor reports, "
    "presentation slides and fee schedules. Output the page's text VERBATIM as "
    "plain text.\n"
    "Rules:\n"
    "- Preserve headings, chapter/section/clause numbers, dates, signatures, "
    "names, job titles, organisations, place names and figures exactly.\n"
    "- Render tables one row per line with ' | ' between cells.\n"
    "- Keep the page number if printed.\n"
    "- Transliterated Arabic place names: copy the spelling on the page.\n"
    "- Do NOT summarize, translate, correct or comment. No preamble.\n"
    "- If a word is illegible write [?].\n"
    "- If the page carries no text (blank, or only a photo/map with no legible "
    "caption) output exactly: [BLANK]"
)


def db():
    c = sqlite3.connect(os.path.join(ROOT, "docs.sqlite3"), timeout=120,
                        check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS pages(
        name TEXT, page INTEGER, state TEXT NOT NULL DEFAULT 'pending',
        text TEXT, chars INTEGER, error TEXT, model TEXT,
        prompt_tokens INTEGER, completion_tokens INTEGER,
        updated_at TIMESTAMP, PRIMARY KEY(name, page))""")
    return c


def enqueue(c):
    """Every needs_ocr file contributes its pages to the ledger."""
    rows = c.execute("SELECT name, ext, pages FROM files WHERE method='needs_ocr'").fetchall()
    n = 0
    for name, ext, pages in rows:
        if ext in ("jpg", "jpeg", "png", "tif", "tiff", "webp"):
            pages = 1
        if not pages:
            pages = count_pages(os.path.join(ROOT, "raw", name))
            if pages:
                c.execute("UPDATE files SET pages=? WHERE name=?", (pages, name))
        if not pages:
            print(f"  !! {name}: page count unknown, cannot enqueue", file=sys.stderr)
            continue
        for p in range(1, pages + 1):
            cur = c.execute("INSERT OR IGNORE INTO pages(name,page) VALUES(?,?)", (name, p))
            n += cur.rowcount
    c.commit()
    return n


def count_pages(path):
    """Render-based page count for PDFs whose xref is damaged."""
    for cmd in (["pdfinfo", path], ["qpdf", "--show-npages", path]):
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=180).stdout.decode("utf8", "ignore")
            m = re.search(r"^Pages:\s+(\d+)", out, re.M) or re.match(r"\s*(\d+)\s*$", out)
            if m:
                return int(m.group(1))
        except Exception:
            continue
    return 0


def render(name, page):
    """-> PNG bytes for one page (or the whole image file)."""
    path = os.path.join(ROOT, "raw", name)
    ext = name.rsplit(".", 1)[-1].lower()
    if ext in ("jpg", "jpeg", "png", "tif", "tiff", "webp"):
        img = Image.open(path)
    else:
        with tempfile.TemporaryDirectory() as td:
            stem = os.path.join(td, "p")
            r = subprocess.run(
                ["pdftoppm", "-r", str(DPI), "-f", str(page), "-l", str(page),
                 "-png", "-singlefile", path, stem],
                capture_output=True, timeout=900)
            if not os.path.exists(stem + ".png"):
                raise RuntimeError("pdftoppm produced no image: "
                                   + r.stderr.decode("utf8", "ignore")[:200])
            img = Image.open(stem + ".png")
            img.load()
    img = img.convert("RGB")
    w, h = img.size
    s = LONG_EDGE / max(w, h)
    if s < 1:
        img = img.resize((max(int(w * s), 1), max(int(h * s), 1)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def call_llm(png):
    img64 = base64.b64encode(png).decode()
    body = json.dumps({
        "model": MODEL, "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64," + img64}}]},
        ],
    }).encode()
    req = urllib.request.Request(LLM_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=600))
    ch = r["choices"][0]
    return (ch["message"].get("content") or "", ch.get("finish_reason"),
            r.get("usage") or {})


def process(name, page):
    try:
        png = render(name, page)
    except Exception as e:
        return dict(state="error", error=f"render: {type(e).__name__}: {e}"[:300],
                    text=None, usage={})
    try:
        content, finish, usage = call_llm(png)
    except Exception as e:
        return dict(state="error", error=f"llm: {type(e).__name__}: {e}"[:300],
                    text=None, usage={})
    txt = content.strip()
    if txt.upper().startswith("[BLANK]"):
        return dict(state="blank", text="", error=None, usage=usage)
    if len(re.sub(r"\s", "", txt)) < MIN_CHARS:
        return dict(state="error", text=txt, usage=usage,
                    error=f"short output ({len(txt)} ch), finish={finish}")
    return dict(state="done", text=txt, error=None, usage=usage)


def cmd_run(a):
    c = db()
    added = enqueue(c)
    if added:
        print(f"enqueued {added} new pages")
    lock = threading.Lock()
    for attempt in range(1, a.passes + 1):
        q = "SELECT name, page FROM pages WHERE state IN ('pending','error')"
        args = []
        if a.only:
            q += " AND name LIKE ?"
            args.append(f"%{a.only}%")
        q += " ORDER BY name, page"
        if a.limit:
            q += f" LIMIT {int(a.limit)}"
        todo = c.execute(q, args).fetchall()
        if not todo:
            print("nothing pending")
            break
        print(f"pass {attempt}: {len(todo)} pages, {a.workers} workers, model {MODEL}",
              flush=True)
        t0 = time.time()
        n = [0]
        okc = [0]

        def work(job):
            name, page = job
            res = process(name, page)
            u = res.get("usage") or {}
            with lock:
                c.execute(
                    "UPDATE pages SET state=?, text=?, chars=?, error=?, model=?,"
                    " prompt_tokens=?, completion_tokens=?, updated_at=datetime('now')"
                    " WHERE name=? AND page=?",
                    (res["state"], res.get("text"),
                     len(re.sub(r"\s", "", res.get("text") or "")),
                     res.get("error"), MODEL, u.get("prompt_tokens"),
                     u.get("completion_tokens"), name, page))
                n[0] += 1
                if res["state"] != "error":
                    okc[0] += 1
                if n[0] % 10 == 0:
                    c.commit()
                    el = time.time() - t0
                    print(f"  {n[0]}/{len(todo)} ok={okc[0]} "
                          f"{el/n[0]:.2f}s/page ({n[0]/el*60:.0f} pages/min)", flush=True)
            if res["state"] == "error":
                print(f"  ERR {name} p{page}: {res.get('error')}", flush=True)

        with concurrent.futures.ThreadPoolExecutor(a.workers) as ex:
            list(ex.map(work, todo))
        c.commit()
        left = c.execute("SELECT count(*) FROM pages WHERE state IN ('pending','error')").fetchone()[0]
        print(f"pass {attempt} done: {okc[0]}/{n[0]} ok, {left} still pending", flush=True)
        if not left or a.limit:
            break
    cmd_status(a)
    if not a.limit:
        cmd_assemble(a)


def cmd_status(a):
    c = db()
    print(dict(c.execute("SELECT state, count(*) FROM pages GROUP BY 1").fetchall()))
    tok = c.execute("SELECT sum(prompt_tokens), sum(completion_tokens) FROM pages").fetchone()
    print(f"tokens: prompt={tok[0]} completion={tok[1]}")
    for r in c.execute(
            "SELECT name, sum(state='done'), sum(state='blank'),"
            " sum(state IN ('pending','error')), count(*) FROM pages"
            " GROUP BY name ORDER BY 4 DESC, name"):
        flag = "  <-- incomplete" if r[3] else ""
        print(f"  {r[1]:3d}done {r[2]:3d}blank {r[3]:3d}todo /{r[4]:3d}  {r[0]}{flag}")


def cmd_assemble(a):
    """Write txt/<name>.txt for files whose pages are all resolved.

    A file with any pending/error page is NOT written -- a partial
    transcription must not be indistinguishable from a complete one
    (AGENTS.md invariant 8).
    """
    c = db()
    names = [r[0] for r in c.execute("SELECT DISTINCT name FROM pages")]
    wrote = skipped = 0
    for name in names:
        left = c.execute("SELECT count(*) FROM pages WHERE name=? AND state IN ('pending','error')",
                         (name,)).fetchone()[0]
        if left:
            skipped += 1
            continue
        parts = []
        for p, t in c.execute("SELECT page, text FROM pages WHERE name=? ORDER BY page", (name,)):
            parts.append(f"\n[[page {p}]]\n" + (t or "[BLANK]"))
        body = "\n".join(parts)
        with open(os.path.join(ROOT, "txt", name + ".txt"), "w") as f:
            f.write(body)
        c.execute("UPDATE files SET method='vision_ocr', chars=? WHERE name=?",
                  (len(re.sub(r"\s", "", body)), name))
        wrote += 1
    c.commit()
    print(f"assembled {wrote} files; {skipped} still incomplete")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--workers", type=int, default=12)
    r.add_argument("--limit", type=int)
    r.add_argument("--only")
    r.add_argument("--passes", type=int, default=3)
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("status")
    s.set_defaults(fn=cmd_status)
    s2 = sub.add_parser("assemble")
    s2.set_defaults(fn=cmd_assemble)
    a = ap.parse_args()
    for k in ("workers", "limit", "only", "passes"):
        if not hasattr(a, k):
            setattr(a, k, None)
    a.fn(a)


if __name__ == "__main__":
    main()
