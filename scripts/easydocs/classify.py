#!/usr/bin/env python3
"""Stage 3: read every extracted document and tag it against the XSA plan.

Same model as the histmap OCR / this dump's OCR (fireworks/muse-glimmer-30b),
same shape: resumable ledger + thread pool.

Two passes, because a 1 M-char Act does not fit in one call and a single
document-level verdict cannot be trusted to have read the middle:

  chunks  ~12 k chars each -> JSON extraction of contacts, organisations,
          places, legal provisions, plan-relevant facts, and a local
          relevance verdict.
  docs    the chunk verdicts + head of the document -> one document-level
          record: type, jurisdiction, date, LEGAL STATUS (in force /
          repealed / superseded / draft-never-enacted), relevance tier,
          and the concrete hooks into the AP-RCA plan.

Status matters as much as topic here: the dump mixes the signed 2026
Wildlife Act with the 1975 Act it repealed, the 2015 Bill it grew from, and
two 2003 New Sudan Acts. A superseded law is background, not authority.

A chunk or doc that fails to parse stays 'error' and is retried -- never
recorded as an empty answer (AGENTS.md invariant 1).

Usage:
  python3 classify.py chunks [--workers 20] [--limit N] [--only SUBSTR]
  python3 classify.py docs    [--workers 12]
  python3 classify.py status
"""
import argparse, concurrent.futures, json, os, re, sqlite3, sys, threading, time
import urllib.request

ROOT = os.environ.get("EASYDOCS_DIR", "/home/exedev/5mp/data/easy_docs")
LLM_URL = os.environ.get("HISTMAP_LLM_URL", "https://llm.int.exe.xyz/v1/chat/completions")
MODEL = os.environ.get("EASYDOCS_CLS_MODEL", "fireworks/muse-glimmer-30b")
CHUNK = 12000
OVERLAP = 600
MAX_TOKENS = 6000

# What the reader is for. Kept verbatim in both prompts so a chunk in the
# middle of the Companies Act is judged against the same yardstick as a WCS
# donor deck.
CONTEXT = """CONTEXT - what this reading is for.
We are producing a conservation assessment ("EASY report") of a 485,150 km2
study area (XSA) where Central African Republic, DR Congo, South Sudan and
Sudan's borderlands meet. It contains four protected areas - Chinko (CAR),
Bili-Uere (DRC), Garamba (DRC), Southern National Park (South Sudan) - plus
unprotected country between them: Western Bahr el Ghazal's return belt around
Wau, and Kafia Kingi in the far north.

We are assessing a support plan for WESTERN SOUTH SUDAN with six items:
 1 reopening the old Tamboura - Deim Zubeir road;
 2 a livestock/transhumance corridor along the CAR - South Sudan border;
 3 a new Pongo-Wau-Numatinna National Park (~14,500 km2), overlapping the
   existing, unmanaged Numatina and Boro game reserves;
 4 Community Wildlife Areas (to be registered as community conservancies);
 5 eight 4-person ECHO/TANGO community awareness teams (to be registered as
   community wildlife associations with community scouts), sited at
   Boro-Medina, Raga/Raja, Deim Zubeir, Ali Golo, Faraj Allah, Raffili
   Mission, Nagero, M'Bittima;
 6 four Focal Points: Boro-Medina, Deim Zubeir, Wau, Tambura.
The premise is that nothing happens without the South Sudan Wildlife Service
(SSWS, under the Ministry of Wildlife Conservation and Tourism, MWCT) as the
visible lead. The governing law is the Wildlife Conservation and Protected
Areas Act 2026 (signed 18 Feb 2026), whose regulations are being written now.

Therefore we care about: people we can contact and their positions; NGOs,
donors, government bodies and their mandates; legal authority that is
CURRENTLY IN FORCE (and which older law it repealed); protected-area names,
boundaries, gazettement history and fees; places inside or near the study
area; transhumance/livestock, community conservancies, scouts, corridors,
land tenure and customary law, NGO registration and operating rules, mining
and forestry permitting, labour/tax/immigration rules that constrain running
field teams; wildlife survey numbers; and security/access conditions."""

CHUNK_SYS = CONTEXT + """

You are given one excerpt of one document (it may start or end mid-sentence).
Extract ONLY what is actually present in the excerpt. Never invent, never
infer a name or number that is not written. Output ONE JSON object, no prose,
no markdown fence:

{"contacts":[{"name":"","title":"","org":"","email":"","phone":"","place":"","note":""}],
 "orgs":[{"name":"","kind":"government|ngo|donor|un|company|community|academic|other","role":""}],
 "places":[{"name":"","kind":"protected_area|town|river|state|county|other","note":""}],
 "provisions":[{"ref":"","gist":""}],
 "facts":[""],
 "numbers":[""],
 "relevance":"core|supporting|background|none",
 "why":""}

Field rules:
- contacts: only named individuals. Copy titles/positions exactly. Omit a
  field you do not have (empty string). Signatories, addressees, authors,
  officials, staff lists and contact tables all count.
- orgs: organisations named, with what they do here in <=12 words.
- places: only places in or near the study area, or protected areas anywhere
  in South Sudan/CAR/DRC/Sudan.
- provisions: legal/policy clauses that would bind or enable the plan
  ("ref" = section/article number as printed). Include penalties, fees,
  permits, registration duties, land rules, corridor and community clauses.
- facts: <=25-word statements from the excerpt useful to the assessment
  (mandates, dates, gazettement, survey results, security, procedures).
- numbers: figures worth keeping (areas, fees, populations, wildlife counts,
  budgets) each with its unit and what it measures.
- relevance: how useful THIS EXCERPT is to the plan above.
- why: one clause, <=20 words.
If the excerpt contains nothing in a category, use an empty list."""

DOC_SYS = CONTEXT + """

You are given: a document's filename, the first part of its text, and the
digests our reader produced for every chunk of it. Produce ONE document-level
JSON record. No prose, no markdown fence:

{"title":"",
 "doc_type":"act|bill|regulation|policy|strategy|mou|letter|report|survey|presentation|contact_list|evaluation|treaty|decree|notes|other",
 "jurisdiction":"South Sudan|Sudan (pre-2011)|New Sudan/SPLM|CAR|DRC|international|other|unclear",
 "date":"", "date_confidence":"stated|inferred|unknown",
 "status":"in_force|repealed|superseded|draft_never_enacted|unknown|not_a_law",
 "status_reason":"",
 "supersedes":[], "superseded_by":[],
 "topics":[],
 "relevance":"core|supporting|background|none",
 "relevance_reason":"",
 "plan_items":[],
 "xsa_hooks":[""],
 "cautions":[""],
 "summary":""}

Rules:
- status: judge from the document itself plus what you know of the sequence
  (Wildlife: 1935/1939 ordinances -> 1975 Act -> New Sudan 2003 Acts ->
  2015 Bill (never enacted) -> Wildlife Conservation and Protected Areas Act
  2026, signed 18 Feb 2026, which repeals the earlier wildlife laws;
  South Sudan independence 9 July 2011 - pre-2011 Sudanese law applies only
  where kept in force). Say WHY in status_reason, in <=25 words. Use
  "unknown" rather than guessing.
- plan_items: which of the six numbered plan items above this document bears
  on, as numbers (e.g. [3,4,6]). Empty list if none.
- xsa_hooks: up to 6 concrete, specific things this document gives us -
  a clause we can cite, a person to contact, a boundary or fee, a survey
  number, a procedural requirement. Each <=25 words, each checkable.
- cautions: up to 3 warnings - superseded provisions, unverified drafts,
  confidentiality markings, stale contacts, disputed boundaries.
- relevance: core = we will cite or act on it; supporting = useful
  corroboration or operational constraint; background = context only;
  none = unrelated to this assessment.
- summary: 2-4 sentences, plain language, what it is and what it is for."""


def db():
    c = sqlite3.connect(os.path.join(ROOT, "docs.sqlite3"), timeout=120,
                        check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS chunks(
        name TEXT, idx INTEGER, start INTEGER, len INTEGER,
        state TEXT NOT NULL DEFAULT 'pending', json TEXT, error TEXT,
        model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,
        updated_at TIMESTAMP, PRIMARY KEY(name, idx));
    CREATE TABLE IF NOT EXISTS docs(
        name TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'pending',
        json TEXT, error TEXT, model TEXT, updated_at TIMESTAMP);
    """)
    return c


def text_of(name):
    p = os.path.join(ROOT, "txt", name + ".txt")
    if not os.path.exists(p):
        return ""
    return open(p, errors="ignore").read()


def split(t):
    """-> [(start, text)] overlapping windows on paragraph-ish boundaries."""
    out, i = [], 0
    while i < len(t):
        end = min(i + CHUNK, len(t))
        if end < len(t):
            nl = t.rfind("\n", i + CHUNK // 2, end)
            if nl > 0:
                end = nl
        out.append((i, t[i:end]))
        if end >= len(t):
            break
        i = max(end - OVERLAP, i + 1)
    return out


def enqueue_chunks(c):
    n = 0
    rows = c.execute("SELECT name FROM files WHERE method NOT IN"
                     " ('duplicate','needs_ocr','damaged_unreadable','unsupported')").fetchall()
    for (name,) in rows:
        t = text_of(name)
        if not t.strip():
            continue
        for i, (s, body) in enumerate(split(t)):
            cur = c.execute("INSERT OR IGNORE INTO chunks(name,idx,start,len)"
                            " VALUES(?,?,?,?)", (name, i, s, len(body)))
            n += cur.rowcount
    c.commit()
    return n


def call_llm(sys_prompt, user, max_tokens=MAX_TOKENS):
    body = json.dumps({
        "model": MODEL, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(LLM_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=600))
    ch = r["choices"][0]
    return (ch["message"].get("content") or "", ch.get("finish_reason"),
            r.get("usage") or {})


def parse_json(s):
    """Tolerant: strip fences, decode the first object; merge any that follow.

    The model sometimes emits two JSON objects back to back (one per logical
    half of a chunk). Taking first-brace-to-last-brace makes that unparseable,
    which cost a retry per affected chunk -- so decode incrementally and merge
    lists instead of discarding the read.
    """
    s = s.strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    i = s.find("{")
    if i < 0:
        raise ValueError("no JSON object in output")
    dec = json.JSONDecoder()
    objs, pos = [], i
    while pos < len(s):
        nxt = s.find("{", pos)
        if nxt < 0:
            break
        try:
            o, end = dec.raw_decode(s, nxt)
        except json.JSONDecodeError:
            if objs:
                break
            # last resort: trailing-comma cleanup over the whole span
            j = s.rfind("}")
            frag = re.sub(r",\s*([}\]])", r"\1", s[nxt:j + 1])
            return json.loads(frag)
        if isinstance(o, dict):
            objs.append(o)
        pos = end
    if not objs:
        raise ValueError("no JSON object in output")
    merged = objs[0]
    for o in objs[1:]:
        for k, v in o.items():
            if isinstance(v, list) and isinstance(merged.get(k), list):
                merged[k] = merged[k] + v
            elif k not in merged or not merged.get(k):
                merged[k] = v
    return merged


def run_pool(c, todo, work, workers, label):
    lock = threading.Lock()
    t0 = time.time()
    n, ok = [0], [0]

    def wrapped(job):
        good, msg = work(job, lock)
        with lock:
            n[0] += 1
            if good:
                ok[0] += 1
            if n[0] % 10 == 0:
                c.commit()
                el = time.time() - t0
                print(f"  {n[0]}/{len(todo)} ok={ok[0]} {el/n[0]:.2f}s/item"
                      f" ({n[0]/el*60:.0f}/min)", flush=True)
        if not good:
            print(f"  ERR {msg}", flush=True)

    print(f"{label}: {len(todo)} items, {workers} workers, model {MODEL}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(workers) as ex:
        list(ex.map(wrapped, todo))
    c.commit()
    print(f"{label} done: {ok[0]}/{n[0]} ok", flush=True)


def cmd_chunks(a):
    c = db()
    added = enqueue_chunks(c)
    if added:
        print(f"enqueued {added} new chunks")
    cache = {}
    clock = threading.Lock()

    def get_text(name):
        with clock:
            if name not in cache:
                cache[name] = text_of(name)
            return cache[name]

    for p in range(1, a.passes + 1):
        q = "SELECT name, idx, start, len FROM chunks WHERE state IN ('pending','error')"
        args = []
        if a.only:
            q += " AND name LIKE ?"
            args.append(f"%{a.only}%")
        q += " ORDER BY name, idx"
        if a.limit:
            q += f" LIMIT {int(a.limit)}"
        todo = c.execute(q, args).fetchall()
        if not todo:
            print("no chunks pending")
            break

        def work(job, lock):
            name, idx, start, ln = job
            body = get_text(name)[start:start + ln]
            user = (f"DOCUMENT: {name}\nEXCERPT {idx + 1} (chars {start}-{start + ln})"
                    f"\n---\n{body}\n---")
            try:
                content, finish, usage = call_llm(CHUNK_SYS, user)
                obj = parse_json(content)
            except Exception as e:
                with lock:
                    c.execute("UPDATE chunks SET state='error', error=?, model=?,"
                              " updated_at=datetime('now') WHERE name=? AND idx=?",
                              (f"{type(e).__name__}: {e}"[:300], MODEL, name, idx))
                return False, f"{name} #{idx}: {type(e).__name__}: {e}"[:200]
            with lock:
                c.execute("UPDATE chunks SET state='done', json=?, error=NULL, model=?,"
                          " prompt_tokens=?, completion_tokens=?, updated_at=datetime('now')"
                          " WHERE name=? AND idx=?",
                          (json.dumps(obj, ensure_ascii=False), MODEL,
                           usage.get("prompt_tokens"), usage.get("completion_tokens"),
                           name, idx))
            return True, ""

        run_pool(c, todo, work, a.workers, f"chunk pass {p}")
        left = c.execute("SELECT count(*) FROM chunks WHERE state IN ('pending','error')").fetchone()[0]
        print(f"  {left} chunks still pending")
        if not left or a.limit:
            break
    cmd_status(a)


RELEV_ORDER = {"core": 3, "supporting": 2, "background": 1, "none": 0}


def doc_digest(c, name, budget=26000):
    """Chunk verdicts + evidence, compressed for the document pass."""
    rows = c.execute("SELECT idx, json FROM chunks WHERE name=? AND state='done'"
                     " ORDER BY idx", (name,)).fetchall()
    lines, seen = [], set()
    for idx, js in rows:
        try:
            o = json.loads(js)
        except Exception:
            continue
        bits = [f"[chunk {idx + 1}] relevance={o.get('relevance')} why={o.get('why')}"]
        for key, fmt in (("contacts", lambda d: " / ".join(
                            str(d.get(k, "")) for k in ("name", "title", "org", "email", "phone") if d.get(k))),
                         ("orgs", lambda d: f"{d.get('name')} ({d.get('kind')}): {d.get('role')}"),
                         ("places", lambda d: f"{d.get('name')} [{d.get('kind')}] {d.get('note','')}"),
                         ("provisions", lambda d: f"{d.get('ref')}: {d.get('gist')}")):
            vals = o.get(key) or []
            out = []
            for d in vals[:12]:
                if isinstance(d, dict):
                    s = fmt(d).strip()
                elif isinstance(d, str):
                    s = d
                else:
                    continue
                if s and s.lower() not in seen:
                    seen.add(s.lower())
                    out.append(s)
            if out:
                bits.append(f"  {key}: " + "; ".join(out))
        for key in ("facts", "numbers"):
            vals = [str(x) for x in (o.get(key) or [])][:10]
            vals = [v for v in vals if v.lower() not in seen and not seen.add(v.lower())]
            if vals:
                bits.append(f"  {key}: " + " | ".join(vals))
        lines.append("\n".join(bits))
    d = "\n".join(lines)
    if len(d) > budget:
        d = d[:budget // 2] + "\n[... digest truncated ...]\n" + d[-budget // 2:]
    return d, len(rows)


def cmd_docs(a):
    c = db()
    names = [r[0] for r in c.execute(
        "SELECT DISTINCT name FROM chunks WHERE state='done'")]
    for name in names:
        c.execute("INSERT OR IGNORE INTO docs(name) VALUES(?)", (name,))
    c.commit()
    q = ("SELECT d.name FROM docs d WHERE d.state IN ('pending','error')"
         " AND NOT EXISTS (SELECT 1 FROM chunks k WHERE k.name=d.name"
         "                 AND k.state IN ('pending','error'))")
    args = []
    if a.only:
        q += " AND d.name LIKE ?"
        args.append(f"%{a.only}%")
    todo = [r[0] for r in c.execute(q, args)]
    skipped = c.execute(
        "SELECT count(DISTINCT name) FROM chunks WHERE state IN ('pending','error')").fetchone()[0]
    if skipped:
        print(f"NOTE: {skipped} documents have unread chunks and are held back"
              " -- rerun `chunks` first (a partial read must not pass as a full one)")
    if not todo:
        print("no docs pending")
        cmd_status(a)
        return

    def work(job, lock):
        name = job
        digest, nch = doc_digest(c, name)
        head = text_of(name)[:6000]
        meta = c.execute("SELECT ext, pages, chars, method FROM files WHERE name=?",
                         (name,)).fetchone() or ("", 0, 0, "")
        user = (f"FILENAME: {name}\nFORMAT: {meta[0]}, {meta[1]} pages,"
                f" {meta[2]} chars, extracted by {meta[3]}\nCHUNKS READ: {nch}\n\n"
                f"=== HEAD OF DOCUMENT ===\n{head}\n\n"
                f"=== CHUNK DIGESTS ===\n{digest}\n")
        try:
            content, finish, usage = call_llm(DOC_SYS, user, max_tokens=4000)
            obj = parse_json(content)
        except Exception as e:
            with lock:
                c.execute("UPDATE docs SET state='error', error=?, model=?,"
                          " updated_at=datetime('now') WHERE name=?",
                          (f"{type(e).__name__}: {e}"[:300], MODEL, name))
            return False, f"{name}: {type(e).__name__}: {e}"[:200]
        with lock:
            c.execute("UPDATE docs SET state='done', json=?, error=NULL, model=?,"
                      " updated_at=datetime('now') WHERE name=?",
                      (json.dumps(obj, ensure_ascii=False), MODEL, name))
        return True, ""

    run_pool(c, todo, work, a.workers, "doc pass")
    cmd_status(a)


def cmd_status(a):
    c = db()
    print("chunks:", dict(c.execute("SELECT state, count(*) FROM chunks GROUP BY 1").fetchall()))
    print("docs:  ", dict(c.execute("SELECT state, count(*) FROM docs GROUP BY 1").fetchall()))
    tok = c.execute("SELECT sum(prompt_tokens), sum(completion_tokens) FROM chunks").fetchone()
    print(f"chunk tokens: prompt={tok[0]} completion={tok[1]}")
    rows = c.execute("SELECT json FROM docs WHERE state='done'").fetchall()
    tally = {}
    for (js,) in rows:
        try:
            o = json.loads(js)
        except Exception:
            continue
        k = (o.get("relevance"), o.get("status"))
        tally[k] = tally.get(k, 0) + 1
    for k in sorted(tally, key=lambda k: -RELEV_ORDER.get(k[0], -1)):
        print(f"  {str(k[0]):11s} {str(k[1]):22s} {tally[k]:3d}")
    bad = c.execute("SELECT name, idx, substr(error,1,90) FROM chunks WHERE state='error'"
                    " LIMIT 10").fetchall()
    for b in bad:
        print(f"  chunk error: {b[0]} #{b[1]}: {b[2]}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd, fn, dw in (("chunks", cmd_chunks, 20), ("docs", cmd_docs, 12),
                        ("status", cmd_status, 1)):
        p = sub.add_parser(cmd)
        p.add_argument("--workers", type=int, default=dw)
        p.add_argument("--limit", type=int)
        p.add_argument("--only")
        p.add_argument("--passes", type=int, default=3)
        p.set_defaults(fn=fn)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
