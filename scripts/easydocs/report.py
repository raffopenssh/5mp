#!/usr/bin/env python3
"""Stage 5: write the EASY document-dossier report from the ledgers.

Pure assembly -- no LLM. Everything here is derived from docs.sqlite3 so a
number in the report can be re-derived by rerunning this script (AGENTS.md
invariant 2: never type a count that describes a variable input).

Writes:
  reports/EASY_DOCS_DOSSIER_<month>.txt     the readable dossier
  data/easy_docs/easy_docs_index.json       machine-readable, everything

Usage: python3 report.py [--out reports/EASY_DOCS_DOSSIER_2026-08.txt]
"""
import argparse, collections, json, os, sqlite3, textwrap, datetime

ROOT = os.environ.get("EASYDOCS_DIR", "/home/exedev/5mp/data/easy_docs")
REPO = "/home/exedev/5mp"
W = 80
RELEV = ["core", "supporting", "background", "none"]
STATUS_NOTE = {
    "in_force": "in force",
    "repealed": "REPEALED",
    "superseded": "SUPERSEDED",
    "draft_never_enacted": "DRAFT, never enacted",
    "not_a_law": "not legislation",
    "unknown": "status unverified",
}
PLAN_ITEM = {
    1: "Tamboura-Deim Zubeir road", 2: "livestock corridor",
    3: "Pongo-Wau-Numatinna NP", 4: "Community Wildlife Areas",
    5: "ECHO/TANGO teams", 6: "Focal Points",
}


# Places the plan and the XSA reports actually name. Matched as substrings,
# lower-cased, because spelling varies by document and era (Raga/Raja,
# Tambura/Tamboura, Numatina/Numatinna) -- see invariant 7: two surfaces
# saying one word must say one number, so the match key is listed with the
# hits it produced rather than hidden.
XSA_KEYS = [
    "numatina", "boro", "pongo", "southern national park", "chinko",
    "garamba", "bili", "bahr el ghazal", "bahr el-ghazal", "wau", "raga",
    "raja", "deim zubeir", "daym", "tambura", "tamboura", "nagero",
    "bittima", "raffili", "ali golo", "faraj", "medina", "busseri", "jur",
    "sue river", "sue r", "lol", "kafia", "mbomou", "zemio", "doruma",
]


def db():
    return sqlite3.connect(os.path.join(ROOT, "docs.sqlite3"), timeout=120)


def wrap(s, indent="  ", width=W):
    return textwrap.fill(" ".join(str(s).split()), width=width,
                         initial_indent=indent, subsequent_indent=indent)


def load(c):
    docs = {}
    for name, js in c.execute("SELECT name, json FROM docs WHERE state='done'"):
        try:
            docs[name] = json.loads(js)
        except Exception:
            pass
    files = {r[0]: dict(zip(("ext", "pages", "chars", "method", "dup_of"), r[1:]))
             for r in c.execute("SELECT name, ext, pages, chars, method, dup_of FROM files")}
    return docs, files


def people(c):
    """Merged roster + the raw mentions each entry came from."""
    from roster import people_lines  # same module, same ordering
    raw = {n: r for n, r in people_lines(c)}
    out, claimed = [], set()
    for (js,) in c.execute("SELECT json FROM roster_batches WHERE kind='people' AND state='done'"):
        for e in json.loads(js).get("people") or []:
            ln = [n for n in (e.get("lines") or []) if isinstance(n, int) and n in raw]
            claimed.update(ln)
            e["sources"] = sorted({raw[n]["source"] for n in ln})
            out.append(e)
    for n in sorted(set(raw) - claimed):    # never silently drop a person
        r = raw[n]
        out.append({"name": r["name"], "titles": [r["title"]] if r["title"] else [],
                    "orgs": [r["org"]] if r["org"] else [], "email": r["email"],
                    "phone": r["phone"], "place": r["place"], "note": r["note"],
                    "category": "other", "usefulness": "identify_role",
                    "sources": [r["source"]], "unmerged": True})
    # collapse exact duplicate names produced by different batches
    by = collections.OrderedDict()
    for e in sorted(out, key=lambda e: e.get("name", "").lower()):
        k = " ".join(e.get("name", "").lower().split())
        if k in by:
            t = by[k]
            for f in ("titles", "orgs", "sources"):
                t[f] = sorted(set((t.get(f) or []) + (e.get(f) or [])))
            for f in ("email", "phone", "place", "note"):
                t[f] = t.get(f) or e.get(f)
            if e.get("usefulness") == "contact_now":
                t["usefulness"] = "contact_now"
        else:
            by[k] = e
    return list(by.values())


def orgs(c):
    out = []
    for (js,) in c.execute("SELECT json FROM roster_batches WHERE kind='orgs' AND state='done'"):
        for e in json.loads(js).get("orgs") or []:
            out.append(e)
    by = collections.OrderedDict()
    for e in out:
        k = " ".join(str(e.get("name", "")).lower().split())
        if not k:
            continue
        if k in by:
            t = by[k]
            t["aliases"] = sorted(set((t.get("aliases") or []) + (e.get("aliases") or [])))
            if RELEV.index(e.get("relevance", "none") if e.get("relevance") in RELEV else "none") \
               < RELEV.index(t.get("relevance", "none") if t.get("relevance") in RELEV else "none"):
                t["relevance"] = e["relevance"]
                t["role"] = e.get("role") or t.get("role")
        else:
            by[k] = e
    return list(by.values())


def chunk_evidence(c):
    """provisions / places / numbers seen, grouped for the annexes."""
    prov = collections.defaultdict(list)
    places = collections.Counter()
    place_src = collections.defaultdict(set)
    for name, js in c.execute("SELECT name, json FROM chunks WHERE state='done' ORDER BY name, idx"):
        try:
            o = json.loads(js)
        except Exception:
            continue
        for d in o.get("provisions") or []:
            if isinstance(d, dict) and (d.get("ref") or d.get("gist")):
                prov[name].append((str(d.get("ref", "")).strip(), str(d.get("gist", "")).strip()))
        for d in o.get("places") or []:
            if isinstance(d, dict) and str(d.get("name", "")).strip():
                pn = " ".join(str(d["name"]).split())
                places[pn] += 1
                place_src[pn].add(name)
    return prov, places, place_src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        REPO, "reports", "EASY_DOCS_DOSSIER_2026-08.txt"))
    a = ap.parse_args()
    import sys
    sys.path.insert(0, os.path.join(REPO, "scripts", "easydocs"))
    c = db()
    docs, files = load(c)
    ppl = people(c)
    org = orgs(c)
    prov, places, place_src = chunk_evidence(c)
    npages = c.execute("SELECT sum(pages) FROM files WHERE method='vision_ocr'").fetchone()[0] or 0
    nchunks = c.execute("SELECT count(*) FROM chunks WHERE state='done'").fetchone()[0]
    dups = {n: f["dup_of"] for n, f in files.items() if f["method"] == "duplicate"}
    unread = {n: f for n, f in files.items()
              if f["method"] in ("damaged_unreadable", "needs_ocr", "unsupported")}

    def rel(n):
        r = docs.get(n, {}).get("relevance")
        return r if r in RELEV else "none"

    by_rel = collections.defaultdict(list)
    for n in docs:
        by_rel[rel(n)].append(n)

    L = []
    P = L.append
    P("=" * W)
    P("")
    P("        THE DOCUMENT DOSSIER")
    P("        Every file in the EASY document dump, read, dated, status-checked")
    P("        and scored against the AP-RCA plan for western South Sudan")
    P(f"        Machine-read {datetime.date.today():%d %B %Y} - INTERNAL")
    P("")
    P(wrap(f"{len(files)} files ({len(dups)} exact duplicates, "
           f"{len(unread)} unreadable) -> {len(docs)} documents read in full: "
           f"{npages} scanned pages transcribed by vision OCR, "
           f"{nchunks} text windows extracted, then merged into "
           f"{len(ppl)} named people and {len(org)} organisations.", "        "))
    P("")
    P(wrap("Everything below is derived from the ledger in "
           "data/easy_docs/docs.sqlite3 - rerun scripts/easydocs/report.py to "
           "regenerate it. Machine-read: verify any name, clause number or "
           "figure against the scan before citing it externally.", "        "))
    P("")
    P("=" * W)
    P("")
    P("")
    P("WHAT IS IN THE DUMP")
    P("-" * W)
    for r in RELEV:
        P(f"  {r:12s} {len(by_rel[r]):3d} documents")
    P("")
    st = collections.Counter(docs[n].get("status") for n in docs)
    P("  Legal status of what was read:")
    for k, v in st.most_common():
        P(f"    {STATUS_NOTE.get(k, str(k)):22s} {v:3d}")
    P("")
    hits = collections.Counter()
    for n, o in docs.items():
        for i in o.get("plan_items") or []:
            if isinstance(i, int):
                hits[i] += 1
    P("  Documents bearing on each plan item:")
    for i in sorted(PLAN_ITEM):
        P(f"    {i}  {PLAN_ITEM[i]:34s} {hits.get(i, 0):3d}")
    P("")

    # ---- section 1: the authorities
    P("")
    P("1. THE LAW THAT ACTUALLY BINDS THE PLAN")
    P("-" * W)
    P(wrap("Status first: a repealed Act is background, and a Bill that never "
           "passed is an intention, not a power. The dump mixes all three.", ""))
    P("")
    inforce = sorted([n for n in docs if docs[n].get("status") == "in_force"],
                     key=lambda n: (RELEV.index(rel(n)), n))
    for n in inforce:
        o = docs[n]
        items = ",".join(str(i) for i in (o.get("plan_items") or []) if isinstance(i, int))
        P(f"  [{rel(n).upper()}] {o.get('title') or n}")
        P(f"      file: {n}")
        P(f"      {o.get('doc_type')}, {o.get('jurisdiction')}, {o.get('date') or 'undated'}"
          f" ({o.get('date_confidence')}); plan items: {items or '-'}")
        if o.get("status_reason"):
            P(wrap("status: " + o["status_reason"], "      "))
        for h in (o.get("xsa_hooks") or [])[:6]:
            P(wrap("* " + str(h), "      "))
        for w in (o.get("cautions") or [])[:3]:
            P(wrap("! " + str(w), "      "))
        P("")
    P("  SUPERSEDED / REPEALED / NEVER ENACTED - do not cite as authority:")
    for n in sorted(docs):
        o = docs[n]
        if o.get("status") in ("repealed", "superseded", "draft_never_enacted"):
            sup = ", ".join(str(x) for x in (o.get("superseded_by") or [])) or "-"
            P(wrap(f"{o.get('title') or n} [{STATUS_NOTE.get(o.get('status'))}]"
                   f" -> replaced by: {sup}   ({n})", "    "))
    P("")

    # ---- section 2: people
    P("")
    P("2. PEOPLE")
    P("-" * W)
    order = {"contact_now": 0, "identify_role": 1, "historical": 2}
    ppl_sorted = sorted(ppl, key=lambda e: (order.get(e.get("usefulness"), 3),
                                            e.get("category", ""), e.get("name", "")))
    withc = [e for e in ppl if e.get("email") or e.get("phone")]
    P(wrap(f"{len(ppl)} named individuals across {len(docs)} documents; "
           f"{len(withc)} carry an email or phone number in the source. "
           "Most are named in documents years old: treat a title as the OFFICE "
           "to approach, not as a person still in post.", ""))
    P("")
    for want, head in (("contact_now", "REACHABLE / CURRENT ROLE"),
                       ("identify_role", "NAMED OFFICE-HOLDERS (verify tenure)"),
                       ("historical", "SIGNATORIES AND FIGURES OF RECORD")):
        grp = [e for e in ppl_sorted if e.get("usefulness") == want]
        P(f"  {head} - {len(grp)}")
        for e in grp:
            t = "; ".join(x for x in (e.get("titles") or []) if x)[:110]
            o2 = "; ".join(x for x in (e.get("orgs") or []) if x)[:90]
            P(f"    {e.get('name')}"
              + (f" [{e.get('category')}]" if e.get("category") else ""))
            if t:
                P(wrap(t, "        "))
            if o2:
                P(wrap(o2, "        "))
            cd = " ".join(x for x in (e.get("email"), e.get("phone"), e.get("place")) if x)
            if cd:
                P(wrap(cd, "        "))
            if e.get("note"):
                P(wrap(e["note"], "        "))
            P(wrap("src: " + ", ".join(e.get("sources") or [])[:400], "        "))
        P("")

    # ---- section 3: organisations
    P("")
    P("3. ORGANISATIONS")
    P("-" * W)
    for r in ("core", "supporting"):
        grp = [e for e in org if e.get("relevance") == r]
        grp.sort(key=lambda e: (e.get("kind", ""), e.get("name", "")))
        P(f"  {r.upper()} - {len(grp)}")
        for e in grp:
            al = ", ".join(x for x in (e.get("aliases") or []) if x)
            P(f"    {e.get('name')} [{e.get('kind')}]" + (f"  (= {al})" if al else ""))
            if e.get("role"):
                P(wrap(e["role"], "        "))
        P("")
    nbg = len([e for e in org if e.get("relevance") not in ("core", "supporting")])
    P(f"  ({nbg} further organisations scored background/none - see the JSON index.)")
    P("")

    # ---- section 4: per plan item
    P("")
    P("4. WHAT THE DUMP GIVES EACH PLAN ITEM")
    P("-" * W)
    for i in sorted(PLAN_ITEM):
        names = [n for n in docs if i in (docs[n].get("plan_items") or [])]
        names.sort(key=lambda n: (RELEV.index(rel(n)), n))
        P(f"  ITEM {i} - {PLAN_ITEM[i].upper()}  ({len(names)} documents)")
        for n in names[:14]:
            o = docs[n]
            P(wrap(f"{o.get('title') or n} [{STATUS_NOTE.get(o.get('status'), o.get('status'))}]"
                   f" - {(o.get('xsa_hooks') or [''])[0]}", "      "))
        if len(names) > 14:
            P(f"      ... and {len(names) - 14} more (JSON index)")
        P("")

    # ---- section 5: every document
    P("")
    P("5. EVERY DOCUMENT, BY RELEVANCE")
    P("-" * W)
    for r in RELEV:
        P(f"  === {r.upper()} ({len(by_rel[r])}) ===")
        for n in sorted(by_rel[r]):
            o = docs[n]
            f = files.get(n, {})
            P(f"  {o.get('title') or n}")
            P(f"      {n}  [{f.get('pages') or '?'}p, {f.get('method')}]")
            P(f"      {o.get('doc_type')} | {o.get('jurisdiction')} |"
              f" {o.get('date') or 'undated'} |"
              f" {STATUS_NOTE.get(o.get('status'), o.get('status'))}")
            if o.get("summary"):
                P(wrap(o["summary"], "      "))
            if o.get("relevance_reason"):
                P(wrap("why: " + o["relevance_reason"], "      "))
            for h in (o.get("xsa_hooks") or [])[:5]:
                P(wrap("* " + str(h), "      "))
            for w in (o.get("cautions") or [])[:3]:
                P(wrap("! " + str(w), "      "))
            np_ = len(prov.get(n, []))
            if np_:
                P(f"      ({np_} provisions extracted - see the JSON index)")
            P("")

    # ---- section 6: places
    P("")
    P("6. PLACES AND PROTECTED AREAS NAMED")
    P("-" * W)
    P(wrap(f"{len(places)} distinct place names appear across the dump. "
           "Spellings vary between documents and eras (Raga/Raja, "
           "Tambura/Tamboura, Numatina/Numatinna) - match on position, not "
           "on string.", ""))
    P("")
    xsa = [(p, ct) for p, ct in places.most_common()
           if any(k in p.lower() for k in XSA_KEYS)]
    P(f"  6a. IN OR BESIDE THE STUDY AREA / THE PLAN'S OWN PLACES ({len(xsa)} names)")
    P(wrap("This is the slice that touches the plan: the two reserves the new "
           "park would absorb, the four Focal Point towns, the ECHO/TANGO "
           "sites, and the neighbouring parks. Anything named here has a "
           "document behind it.", "     "))
    P("")
    for p, ct in xsa:
        P(f"    {ct:4d}x  {p}")
        P(wrap("in: " + ", ".join(sorted(place_src[p])), "           "))
    P("")
    P("  6b. EVERYTHING ELSE, most-mentioned first")
    rest = [(p, ct) for p, ct in places.most_common() if (p, ct) not in set(xsa)]
    for p, ct in rest[:60]:
        P(f"    {ct:4d}x  {p}")
    if len(rest) > 60:
        P(f"    ... and {len(rest) - 60} more (JSON index)")
    P("")

    # ---- provenance
    P("")
    P("HOW THIS WAS MADE, AND WHAT IT CANNOT TELL YOU")
    P("-" * W)
    P(wrap("Born-digital text was extracted with pdftotext/antiword/OOXML. "
           f"{npages} pages of scans were transcribed page by page by a vision "
           "model (fireworks/muse-glimmer-30b, the same model as the historical-"
           "map OCR). Every page, window, document, person and organisation is "
           "a row in data/easy_docs/docs.sqlite3 with its state, so a failed "
           "read is retried rather than recorded as an empty answer.", ""))
    P("")
    P(wrap("LIMITS. (1) The reading is machine-made: names, clause numbers and "
           "figures must be checked against the scan before external use. "
           "(2) Legal status is judged from the documents themselves plus the "
           "known sequence of wildlife law; it is not a lawyer's opinion, and "
           "no gazette was consulted. (3) Contact details are as printed in "
           "documents up to two decades old. (4) The dump is what it is - "
           "absence of a subject here is not evidence about the ground.", ""))
    P("")
    if dups:
        P("  Exact duplicates (read once):")
        for n, d in sorted(dups.items()):
            P(wrap(f"{n} == {d}", "    "))
        P("")
    if unread:
        P("  NOT READ - and why:")
        for n, f in sorted(unread.items()):
            P(wrap(f"{n}: {f['method']}", "    "))
        P("")
    P("=" * W)

    out = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        f.write(out)

    idx = {
        "generated": datetime.date.today().isoformat(),
        "counts": {"files": len(files), "documents_read": len(docs),
                   "ocr_pages": npages, "chunks": nchunks,
                   "people": len(ppl), "orgs": len(org), "places": len(places)},
        "documents": {n: dict(docs[n], _file=files.get(n, {}),
                              provisions=[{"ref": r, "gist": g} for r, g in prov.get(n, [])])
                      for n in docs},
        "people": ppl, "orgs": org,
        "places": [{"name": p, "mentions": ct, "documents": sorted(place_src[p]),
                    "in_study_area": any(k in p.lower() for k in XSA_KEYS)}
                   for p, ct in places.most_common()],
        "not_read": unread, "duplicates": dups,
    }
    jp = os.path.join(ROOT, "easy_docs_index.json")
    with open(jp, "w") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    print(f"wrote {a.out} ({len(out)} chars, {out.count(chr(10))} lines)")
    print(f"wrote {jp} ({os.path.getsize(jp)} bytes)")


if __name__ == "__main__":
    main()
