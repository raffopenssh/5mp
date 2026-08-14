#!/usr/bin/env python3
"""Categorize the OCR'd historical-map labels with a cheap text LLM.

The vision OCR (ocr_labels.py) already sorted labels into
place|water|hill|route|boundary|other, but 'other' is a 28k-row grab bag:
surveyor's notes ("water after rains"), vegetation ("Tundub tree"),
collar/graticule junk ("39° 0'", "815"), and OCR debris ("O", "a.v.").
This pass sends each DISTINCT 'other' text (13.7k) to a cheap text model
and stores the verdict, so the vector export can carry a usable category
and downstream users can filter the junk.

Resumable ledger: table text_categories(text PK) in labels.sqlite3 —
rerunning skips classified texts (a no-op must not read as an answer:
an unparseable batch is retried, not recorded).

Final column: labels_dedup.category / labels.category =
  kind when the vision model was specific (place|water|route|boundary),
  'terrain' for kind='hill', the LLM verdict for kind='other'.

Taxonomy: place water terrain vegetation route boundary note collar junk

Usage:
  python3 categorize_labels.py run [--limit N] [--batch 150]
  python3 categorize_labels.py status
  python3 categorize_labels.py apply     # write labels_dedup.category
"""
import argparse, json, os, re, sqlite3, sys, time, urllib.request

DB_PATH = os.environ.get("HISTMAP_LABELS_DB", "/home/exedev/5mp/data/histmaps/labels.sqlite3")
LLM_URL = os.environ.get("HISTMAP_LLM_URL", "https://llm.int.exe.xyz/v1/chat/completions")
MODEL = os.environ.get("HISTMAP_CAT_MODEL", "fireworks/gpt-oss-120b")

CATS = ["place", "water", "terrain", "vegetation", "route", "boundary",
        "note", "collar", "junk"]

SYS_PROMPT = (
    "You classify text labels transcribed by OCR from 1908-1976 Sudan Survey "
    "1:250,000 map sheets (Anglo-Egyptian Sudan; English with transliterated "
    "Arabic). For each numbered label output exactly one line: the number, a "
    "space, and one category from:\n"
    "place - named settlement, village, camp, station, district or tribe name\n"
    "water - river, khor, wadi, well, spring, pool, swamp names or water notes "
    "(e.g. 'Well (P)', 'water after rains', 'Dry Nov.')\n"
    "terrain - hills, jebels, rocks, plains, soil/ground descriptions "
    "(e.g. 'Cotton soil', 'Very rocky', 'J. Abyad')\n"
    "vegetation - trees, grass, bush, forest, cultivation "
    "(e.g. 'Tundub tree', 'bush', 'Cultivation', 'Kitir & gum')\n"
    "route - roads, tracks, routes, railways, telegraph\n"
    "boundary - administrative/international boundary text\n"
    "note - other surveyor annotations: elevations with context, beacons, "
    "graves, ruins, dates, long descriptive passages\n"
    "collar - map margin/graticule text: bare coordinates like \"39° 0'\", "
    "sheet numbers/names, edition and compilation notes, scale text\n"
    "junk - OCR debris with no standalone meaning: single letters, bare "
    "numbers, '?', fragments like 'a.v.' or 'net'\n"
    "Output ONLY the numbered lines, one per input, nothing else."
)


def db():
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS text_categories(
        text TEXT PRIMARY KEY, category TEXT NOT NULL, model TEXT)""")
    return c


def pending(c, limit=None):
    q = """SELECT DISTINCT d.text FROM labels_dedup d
           LEFT JOIN text_categories t ON t.text = d.text
           WHERE d.kind = 'other' AND t.text IS NULL ORDER BY d.text"""
    if limit:
        q += f" LIMIT {int(limit)}"
    return [r[0] for r in c.execute(q)]


def call_llm(texts):
    user = "\n".join(f"{i+1}. {t[:300]}" for i, t in enumerate(texts))
    body = json.dumps({
        "model": MODEL, "max_tokens": 16000,
        "messages": [{"role": "system", "content": SYS_PROMPT},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(LLM_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=300))
    return r["choices"][0]["message"].get("content") or ""


LINE_RE = re.compile(r"^\s*(\d+)[.)\s]+\s*([a-z]+)\s*$", re.M)


def cmd_run(args):
    c = db()
    todo = pending(c, args.limit)
    print(f"{len(todo)} distinct texts to classify (model {MODEL})")
    done = 0
    t0 = time.time()
    for i in range(0, len(todo), args.batch):
        batch = todo[i:i + args.batch]
        try:
            content = call_llm(batch)
        except Exception as e:
            print(f"  batch @{i}: {type(e).__name__}: {e} -- skipped, rerun to retry")
            time.sleep(5)
            continue
        got = {}
        for m in LINE_RE.finditer(content):
            n, cat = int(m.group(1)), m.group(2)
            if 1 <= n <= len(batch) and cat in CATS:
                got[n] = cat
        rows = [(batch[n - 1], cat, MODEL) for n, cat in got.items()]
        c.executemany("INSERT OR IGNORE INTO text_categories VALUES(?,?,?)", rows)
        c.commit()
        done += len(rows)
        miss = len(batch) - len(rows)
        rate = done / max(time.time() - t0, 1)
        print(f"  {i + len(batch)}/{len(todo)} classified={done}"
              + (f" (batch missed {miss}, will retry)" if miss else "")
              + f" {rate:.0f}/s")
    left = len(pending(c))
    print(f"done: {done} written this run, {left} still pending"
          + (" -- rerun to finish" if left else ""))


def cmd_status(args):
    c = db()
    n = c.execute("SELECT count(*) FROM text_categories").fetchone()[0]
    left = len(pending(c))
    print(f"classified: {n}, pending: {left}")
    for cat, k in c.execute(
            "SELECT category, count(*) FROM text_categories GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {cat:11s} {k}")


def cmd_apply(args):
    """Write the final category column onto labels_dedup and labels."""
    c = db()
    left = len(pending(c))
    if left:
        sys.exit(f"{left} texts still unclassified -- run `run` first "
                 "(a partial apply would freeze 'other' as an answer)")
    for tbl in ("labels_dedup", "labels"):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({tbl})")]
        if "category" not in cols:
            c.execute(f"ALTER TABLE {tbl} ADD COLUMN category TEXT")
        c.execute(f"""UPDATE {tbl} SET category = CASE
            WHEN kind IN ('place','water','route','boundary') THEN kind
            WHEN kind = 'hill' THEN 'terrain'
            ELSE COALESCE((SELECT category FROM text_categories t
                           WHERE t.text = {tbl}.text), 'note') END""")
        c.commit()
        for cat, k in c.execute(
                f"SELECT category, count(*) FROM {tbl} GROUP BY 1 ORDER BY 2 DESC"):
            print(f"  {tbl}.{cat:11s} {k}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--limit", type=int)
    p.add_argument("--batch", type=int, default=150)
    sub.add_parser("status")
    sub.add_parser("apply")
    args = ap.parse_args()
    {"run": cmd_run, "status": cmd_status, "apply": cmd_apply}[args.cmd](args)
