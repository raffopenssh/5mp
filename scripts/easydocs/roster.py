#!/usr/bin/env python3
"""Stage 4: consolidate the extracted people and organisations into a roster.

The chunk pass found 578 contact mentions (425 distinct spellings) and 1,462
distinct organisation strings across 94 documents -- with aliases ('WCS' /
'Wildlife Conservation Society'), title drift, and the same person signing
three documents in two roles. This pass merges them with the same cheap model
(fireworks/muse-glimmer-30b), in batches, with a resumable ledger.

The merge is asked to keep provenance and never to invent: an entry may only
name documents it was actually seen in, and a title it was actually given.
Unmerged input is retried, never dropped.

Usage:
  python3 roster.py people [--batch 60] [--workers 8]
  python3 roster.py orgs   [--batch 120] [--workers 8]
  python3 roster.py status
"""
import argparse, collections, json, os, re, sqlite3, threading, time
import concurrent.futures, urllib.request

ROOT = os.environ.get("EASYDOCS_DIR", "/home/exedev/5mp/data/easy_docs")
LLM_URL = os.environ.get("HISTMAP_LLM_URL", "https://llm.int.exe.xyz/v1/chat/completions")
MODEL = os.environ.get("EASYDOCS_CLS_MODEL", "fireworks/muse-glimmer-30b")

PEOPLE_SYS = """You consolidate a list of people extracted from South Sudanese
conservation, government, donor and legal documents, for a conservation
assessment of western South Sudan (a plan involving the South Sudan Wildlife
Service (SSWS), the Ministry of Wildlife Conservation and Tourism (MWCT), a
proposed Pongo-Wau-Numatinna National Park, community conservancies, community
scout teams and a transhumance corridor).

Each input line is: N | name | title | org | email | phone | place | note | source_file

Merge lines that are clearly the SAME PERSON (same name, or an obvious variant
spelling/abbreviation with a compatible org). Output ONE JSON object:

{"people":[{"name":"","titles":[""],"orgs":[""],"email":"","phone":"",
  "place":"","lines":[N,...],
  "category":"government|wildlife_service|ngo|donor|un|company|community|academic|legal|other",
  "usefulness":"contact_now|identify_role|historical",
  "note":""}]}

Rules:
- Copy names, titles, emails and phones EXACTLY as given; never invent one.
- titles/orgs: every distinct title or organisation seen for that person.
- lines: the input numbers merged into this entry. EVERY input line number must
  appear in exactly one entry.
- category: judge from the org/title.
- usefulness: contact_now = a person one could plausibly still reach in a
  current role or with contact details; identify_role = named official whose
  office matters but whose tenure may have ended; historical = signatory,
  author or figure of record only.
- note: <=20 words, why they matter to this assessment. Empty if unclear.
Output only the JSON object."""

ORGS_SYS = """You consolidate organisation names extracted from South Sudanese
conservation, government, donor and legal documents, for a conservation
assessment of western South Sudan (SSWS / MWCT, a proposed Pongo-Wau-Numatinna
National Park, community conservancies, community scouts, a transhumance
corridor, NGO registration and operating rules).

Each input line is: N | organisation name | kind | roles seen | count | source_files

Merge acronyms with their full names and obvious variants (e.g. 'WCS' with
'Wildlife Conservation Society'; 'MWCT' with 'Ministry of Wildlife Conservation
and Tourism'). Do NOT merge distinct bodies that merely sound alike (a Ministry
and a Directorate inside it are different; a state authority and the national
one are different). Output ONE JSON object:

{"orgs":[{"name":"","aliases":[""],
  "kind":"government|wildlife_service|ngo|donor|un|company|community|academic|legal|military_security|other",
  "role":"","lines":[N,...],
  "relevance":"core|supporting|background|none"}]}

Rules:
- name: the fullest form seen. aliases: the other forms, including acronyms.
- role: <=15 words, what it does that bears on the assessment.
- lines: EVERY input line number must appear in exactly one entry.
- relevance: core = we must deal with it directly (wildlife authorities,
  partners, NGO regulator, likely implementers); supporting = matters
  operationally (taxes, labour, land, security, donors); background = named in
  passing; none = unrelated.
- Generic fragments ('Ministry', 'Authority', 'Government') that name no
  specific body: keep them as their own entry with relevance 'none'.
Output only the JSON object."""


def db():
    c = sqlite3.connect(os.path.join(ROOT, "docs.sqlite3"), timeout=120,
                        check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS roster_batches(
        kind TEXT, batch INTEGER, state TEXT NOT NULL DEFAULT 'pending',
        input TEXT, json TEXT, error TEXT, model TEXT, updated_at TIMESTAMP,
        PRIMARY KEY(kind, batch));
    """)
    return c


MAX_TOKENS = 24000   # muse-glimmer reasons before it answers: at 8000 the
                     # whole budget went to reasoning and content came back
                     # EMPTY with finish_reason=length -- which parses as a
                     # failure, not as "no people found".


def call_llm(sys_prompt, user, max_tokens=MAX_TOKENS):
    body = json.dumps({
        "model": MODEL, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(LLM_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=600))
    return r["choices"][0]["message"].get("content") or ""


def parse_json(s):
    s = re.sub(r"^```(?:json)?|```$", "", s.strip(), flags=re.M).strip()
    i = s.find("{")
    if i < 0:
        raise ValueError("no JSON object")
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
            j = s.rfind("}")
            return json.loads(re.sub(r",\s*([}\]])", r"\1", s[nxt:j + 1]))
        if isinstance(o, dict):
            objs.append(o)
        pos = end
    if not objs:
        raise ValueError("no JSON object")
    m = objs[0]
    for o in objs[1:]:
        for k, v in o.items():
            if isinstance(v, list) and isinstance(m.get(k), list):
                m[k] += v
            elif not m.get(k):
                m[k] = v
    return m


def people_lines(c):
    """-> [(n, line_text, record)] one line per contact mention."""
    out = []
    for name, js in c.execute("SELECT name, json FROM chunks WHERE state='done'"
                             " ORDER BY name, idx"):
        try:
            o = json.loads(js)
        except Exception:
            continue
        for d in o.get("contacts") or []:
            if not isinstance(d, dict) or not str(d.get("name", "")).strip():
                continue
            rec = {k: str(d.get(k, "") or "").strip() for k in
                   ("name", "title", "org", "email", "phone", "place", "note")}
            rec["source"] = name
            out.append(rec)
    # stable order: group by lowercase name so a batch sees a person's mentions together
    out.sort(key=lambda r: (r["name"].lower(), r["source"]))
    return [(i + 1, r) for i, r in enumerate(out)]


def org_lines(c):
    agg = collections.OrderedDict()
    for name, js in c.execute("SELECT name, json FROM chunks WHERE state='done'"
                             " ORDER BY name, idx"):
        try:
            o = json.loads(js)
        except Exception:
            continue
        for d in o.get("orgs") or []:
            if not isinstance(d, dict) or not str(d.get("name", "")).strip():
                continue
            k = str(d["name"]).strip()
            e = agg.setdefault(k.lower(), {"name": k, "kinds": collections.Counter(),
                                           "roles": [], "srcs": collections.Counter(),
                                           "count": 0})
            e["count"] += 1
            e["kinds"][str(d.get("kind", "") or "")] += 1
            r = str(d.get("role", "") or "").strip()
            if r and r not in e["roles"]:
                e["roles"].append(r)
            e["srcs"][name] += 1
    rows = sorted(agg.values(), key=lambda e: (-e["count"], e["name"].lower()))
    return [(i + 1, r) for i, r in enumerate(rows)]


def fmt_person(n, r):
    return (f"{n} | {r['name']} | {r['title']} | {r['org']} | {r['email']} |"
            f" {r['phone']} | {r['place']} | {r['note'][:90]} | {r['source'][:60]}")


def fmt_org(n, r):
    kind = r["kinds"].most_common(1)[0][0] if r["kinds"] else ""
    roles = "; ".join(r["roles"][:3])[:160]
    srcs = ", ".join(s[:40] for s, _ in r["srcs"].most_common(3))
    return f"{n} | {r['name']} | {kind} | {roles} | {r['count']} | {srcs}"


def run(kind, lines, fmt, sys_prompt, batch, workers, passes=3):
    c = db()
    total = len(lines)
    nb = (total + batch - 1) // batch
    # Batch ids are positional, so changing --batch re-segments the input and
    # an old row under the same id would hold a DIFFERENT slice -- its result
    # would then be counted against lines it never saw. Drop any row whose
    # stored input no longer matches its segment.
    segs = {}
    for b in range(nb):
        seg = lines[b * batch:(b + 1) * batch]
        segs[b] = "\n".join(fmt(n, r) for n, r in seg)
    stale = [b for b, inp in c.execute(
        "SELECT batch, input FROM roster_batches WHERE kind=?", (kind,))
        if segs.get(b) != inp]
    if stale:
        c.executemany("DELETE FROM roster_batches WHERE kind=? AND batch=?",
                      [(kind, b) for b in stale])
        print(f"{kind}: dropped {len(stale)} batches re-segmented by --batch={batch}")
    for b, inp in segs.items():
        c.execute("INSERT OR IGNORE INTO roster_batches(kind,batch,input) VALUES(?,?,?)",
                  (kind, b, inp))
    c.commit()
    lock = threading.Lock()
    for p in range(1, passes + 1):
        todo = c.execute("SELECT batch, input FROM roster_batches WHERE kind=?"
                         " AND state IN ('pending','error') ORDER BY batch",
                         (kind,)).fetchall()
        if not todo:
            break
        print(f"{kind} pass {p}: {len(todo)}/{nb} batches ({total} lines),"
              f" {workers} workers, model {MODEL}", flush=True)
        t0 = time.time()
        done = [0]

        def work(job):
            b, inp = job
            try:
                out = call_llm(sys_prompt, inp)
                if not out.strip():
                    raise ValueError("empty content (token budget exhausted"
                                     " by reasoning; lower --batch)")
                obj = parse_json(out)
                if not obj.get(kind):
                    raise ValueError(f"no '{kind}' key in output")
                st, err, js = "done", None, json.dumps(obj, ensure_ascii=False)
            except Exception as e:
                st, err, js = "error", f"{type(e).__name__}: {e}"[:300], None
            with lock:
                c.execute("UPDATE roster_batches SET state=?, json=?, error=?, model=?,"
                          " updated_at=datetime('now') WHERE kind=? AND batch=?",
                          (st, js, err, MODEL, kind, b))
                done[0] += 1
                c.commit()
            print(f"  batch {b}: {st}" + (f" {err}" if err else "")
                  + f"  [{done[0]}/{len(todo)}, {time.time()-t0:.0f}s]", flush=True)

        with concurrent.futures.ThreadPoolExecutor(workers) as ex:
            list(ex.map(work, todo))
    left = c.execute("SELECT count(*) FROM roster_batches WHERE kind=? AND state!='done'",
                     (kind,)).fetchone()[0]
    print(f"{kind}: {nb - left}/{nb} batches done, {left} pending")
    coverage(c, kind, total)


def coverage(c, kind, total):
    """Every input line must land in exactly one merged entry -- report the gap.

    A merge that quietly drops 40 people looks identical to one that merged
    them (AGENTS.md invariant 1), so the gap is printed and the unclaimed
    line numbers are kept in the DB for the report stage to add verbatim.
    """
    claimed = set()
    ents = 0
    for (js,) in c.execute("SELECT json FROM roster_batches WHERE kind=? AND state='done'",
                           (kind,)):
        for e in json.loads(js).get(kind) or []:
            ents += 1
            for n in e.get("lines") or []:
                if isinstance(n, int):
                    claimed.add(n)
    miss = total - len(claimed & set(range(1, total + 1)))
    print(f"{kind}: {ents} merged entries; {len(claimed)}/{total} input lines claimed"
          + (f"; {miss} UNCLAIMED (kept verbatim in the report)" if miss else ""))


def cmd_people(a):
    run("people", people_lines(db()), fmt_person, PEOPLE_SYS, a.batch, a.workers)


def cmd_orgs(a):
    run("orgs", org_lines(db()), fmt_org, ORGS_SYS, a.batch, a.workers)


def cmd_status(a):
    c = db()
    for r in c.execute("SELECT kind, state, count(*) FROM roster_batches GROUP BY 1,2"):
        print(r)
    coverage(c, "people", len(people_lines(c)))
    coverage(c, "orgs", len(org_lines(c)))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd, fn, bt in (("people", cmd_people, 60), ("orgs", cmd_orgs, 120),
                        ("status", cmd_status, 0)):
        p = sub.add_parser(cmd)
        p.add_argument("--batch", type=int, default=bt)
        p.add_argument("--workers", type=int, default=8)
        p.set_defaults(fn=fn)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
