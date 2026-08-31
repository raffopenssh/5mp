# `easydocs` — the EASY document dump

A 234 MB zip of 98 mixed files (85 PDF, 8 `.doc`, 3 `.docx`, 1 `.xlsx`, 1 photo
of a printed fee schedule) arrived as background material for the AP-RCA
assessment of western South Sudan: South Sudanese and Sudanese legislation,
policies, MoUs, WCS/USAID donor decks, aerial-survey reports, NGO regulations
and one contact list. This file is how it was read and what the reading is
worth.

**Artefacts** (all gitignored — reproducible):

| Path | What |
|---|---|
| `data/easy_docs/raw/` | the unzipped originals (256 MB) |
| `data/easy_docs/txt/<file>.txt` | extracted or transcribed text |
| `data/easy_docs/docs.sqlite3` | the ledger: `files`, `pages`, `chunks`, `docs`, `roster_batches` |
| `data/easy_docs/easy_docs_index.json` | machine-readable everything (1.8 MB) |
| `reports/EASY_DOCS_DOSSIER_2026-08.txt` | the readable dossier (6,605 lines) |

**Pipeline** — five stages, each resumable, each with its own ledger table:

```bash
python3 scripts/easydocs/extract.py                        # ~1 min
python3 scripts/easydocs/ocr_scans.py run --workers 16     # 471 pages, ~12 min
python3 scripts/easydocs/classify.py chunks --workers 24   # 881 windows, ~25 min
python3 scripts/easydocs/classify.py docs --workers 12     # 94 documents, ~4 min
python3 scripts/easydocs/roster.py people --batch 40 --workers 10
python3 scripts/easydocs/roster.py orgs   --batch 60 --workers 12
python3 scripts/easydocs/report.py                         # no LLM
```

Model throughout: **`fireworks/muse-glimmer-30b`**, the same cheap vision/text
model as the histmap OCR (`scripts/histmaps/ocr_labels.py`). Cost of the full
run: 3.85 M prompt + 2.43 M completion tokens.

Result: 94 documents read (3 exact duplicates read once, 1 PDF unreadable),
387 people, 1,233 organisations, 713 place names, 3 documents scored **core**,
47 supporting, 37 background, 7 none.

---

### Status is the question, not topic

The dump is a legal *stack*, not a library: it holds the signed **Wildlife
Conservation and Protected Areas Act 2026** next to the 1975 Act it repealed,
the 2015 Bill it grew from, two 2003 New Sudan Acts, and 1935/1939 colonial
ordinances. A relevance score alone would rank the 1975 Act highly and be
wrong — it is background, because it has no force. So the document pass emits
`status` (`in_force` / `repealed` / `superseded` / `draft_never_enacted` /
`not_a_law` / `unknown`) with a `status_reason`, and the dossier's section 1
lists only in-force law as authority, with the superseded set below it under a
"do not cite" heading. 25 in force, 21 superseded, 6 never enacted, 11
unverified.

The same care applies to a *fee schedule*: `Park Fees South Sudan.jpg` is a
FY2018/19–2019/20 tariff, correctly marked superseded — useful as a historic
level, not as today's price.

### Provenance travels with every claim

Each person carries the files they were named in; each organisation its
aliases; each place the documents that mention it. Nothing in the dossier is
asserted without a filename beside it, because the whole artefact is
machine-read and a wrong clause number cited externally is the failure mode
that matters. The header says so, and section 5 repeats it per document.

### Coverage is asserted, not assumed

Three places where a silent no-op would have looked like an answer
(root invariant 1):

* **`extract.py`**: a PDF yielding under 120 chars/page is recorded
  `needs_ocr` with `chars=0` — not as a 53-byte success. That threshold is what
  caught the 27 scanned files, including the 2026 Act itself (53 pages,
  53 bytes of embedded text).
* **`ocr_scans.py assemble`**: a file with *any* pending or errored page is not
  written at all. A partial transcription must not be indistinguishable from a
  complete one (invariant 8).
* **`roster.py coverage()`**: every input line must land in exactly one merged
  entry, and the gap is printed (`578/578`, `1414/1414`). Unclaimed people are
  added to the report verbatim rather than dropped.

### Two failures that cost a rerun

**muse-glimmer reasons before it answers.** At `max_tokens=8000` the roster
merge returned `finish_reason=length` with **empty content** — 8,000 completion
tokens of reasoning and zero output. That parsed as "no JSON object", i.e. as a
failure, which is right, but the cause looked like a bad prompt. The budget is
now 24,000 and empty content raises with the fix in its message. Page OCR and
chunk extraction are unaffected (6 k budget, ~700-token answers); it is the
*merge* prompts, which reason over 40–60 lines at once, that need the room.

**Roster batch ids are positional.** `roster_batches(kind, batch)` keys on the
segment index, so re-running with a different `--batch` re-segments the input
and an old row under the same id holds a *different* slice — its merged output
would then be counted against lines it never saw. `run()` now compares each
stored `input` to the segment it would produce and drops the stale rows.

**The model sometimes emits two JSON objects back to back.** First-brace-to-
last-brace makes that unparseable; `parse_json` decodes incrementally and
merges lists instead of discarding a good read (this was ~60 retried chunks
before the fix).

### Rendering scans

`pdftoppm ... -png file -` (stdout) silently produced nothing for some PDFs on
this poppler build while `-singlefile <stem>` worked — 50 pages failed as
"pdftoppm produced no image" before that was traced. Always render to a temp
file. Pages go to the model at 150 dpi downscaled to a 1600 px long edge:
1024 px (the histmap tile size) loses section numbers on dense legal body text.

One PDF, `CDH - S Sudan Labour Law.pdf`, has a destroyed xref that neither
`qpdf --qdf` nor Ghostscript could rebuild (gs produced a 1-page file). It is
marked `damaged_unreadable` and is listed as not-read in the dossier — but the
same publication is in the dump twice, and the other copy
(`Labour Law CDH - S Sudan.pdf`) read fine.

### What the dossier is for

Section 4 answers the question the assessment actually asks: *what does the
dump give each of the plan's six items?* Item 4 (Community Wildlife Areas) has
28 documents behind it, item 5 (ECHO/TANGO teams) 25, item 3 (the new park) 23;
item 1 (the road) 9 and item 6 (Focal Points) 9 are thin. Section 6a filters
the 713 place names down to the 39 that touch the study area — Numatina and
Boro Game Reserves appear in the 2026 Act's Schedule IV, which is the legal
hook for absorbing them into a Pongo-Wau-Numatinna park.

Spelling varies by document and era (Raga/Raja, Tambura/Tamboura,
Numatina/Numatinna); the place index matches on substring and prints its key,
so the match is auditable. Match on position, not on string.

### What the plan took from it (2026-08-31)

`reports/PLAN_APRCA_WSS_ASSESSMENT_TO_ACTION_2026-08_EASY.txt` (untracked, like
all of `reports/`) was rewritten against the dossier: a new **section 7 on
artisanal gold**, and an Oct-2026 start with 6 mo / 1 yr / 2 yr / 3 yr+ phases
built around the single Dec 15 – Feb 15 field window.

The gold section turns on a mismatch only the dump makes visible: the 2026 Act
s.10(1)(e) bans mining and prospecting inside a protected area, but **Mining
Act 2012 s.23(1)(g) only defers to a park "wherein Mining Operations are
specifically banned"** — so the ban must be written in those words into the
declaration and the Schedule IV boundary regulations (2026 Act s.8(3)), or a
state mining office reads no bar. And it is a *state* office: artisanal
licences are applied for and granted by "the respective State Authorities"
(Mining Act s.75), citizens only, 12,000 m³/yr cap (s.74(1)), annual renewal,
EIA and closure plan exemptible under s.75(6); Community Development
Agreements bind large-scale holders only (s.68(1)). The cheap counter-moves
are **s.24** (Gazette order closing an area to mineral-title applications —
needs no park, no budget) and 2026 Act **s.14(4)** (an authorized
conservancy's governing authority must consent in writing before *any*
natural-resource use inside it). **s.27** is the same tool pointed the other
way (Mineral Resource Reserve), so filing order matters.

Two other findings the dump settled:

* **Numatina is claimed twice** — Game Reserve in Schedule IV of the 2026 Act
  *and* "Namatina Central Forest Reserve", gazetted 15-06-1953 (Gazette 856,
  610,236 feddans ≈ 2,560 km²) in the Harmonized Forestry Policy's own reserve
  list, whose boundaries only the forests Minister may change. Settle before
  declaration.
* **No recent wildlife baseline.** The last aerial numbers here are WCS 2007
  (165 Derby's eland, Southern NP); the 2015/16 survey named
  Numatina–Chelkou–Boro a key unassessed area and listed it as still to be
  flown. It never was.

The same 2015/16 survey is the precedent for the gold risk: gold found at
Nyalongoro in 2016, a small-scale mine in an elephant corridor on the Kidepo
drainage, and 2015–16 exploration licences outside protected areas but inside
corridors, proposed conservancies and river headwaters.
The dossier's own contact material is old in a specific way. `SS contacts from
Jean.xlsx` is **undated** (internal references run to mid-2021), so government
post-holders in it must be treated as ~5 years stale — the posts survive, the
names may not. Section 8 of the plan therefore carries a *last read* column
per counterpart, and is filled from two sources of very different vintage:
the dump for government, and the South Sudan NGO Forum's national-member
roster read live on 2026-08-31 (250 listed, 139 certified) for NNGOs.
Certification there is Forum membership status, not diligence, and the
entries are self-written. Best geographic fit found: HARD (Wau HQ, Raja and
Aweil offices). Two counterparts have **no name anywhere**: the state mining
authority that actually issues artisanal gold licences, and the WBeG State
Wildlife Conservation Authority consulted under s.16.

### The dump is four years stale on partners (checked 2026-08-31)

A web check while building section 8 of the plan overturned three things the
dump implies:

* **FFI is the incumbent next door.** Fauna & Flora works in **Southern
  National Park** plus Bangangai and Bire Kpatuos Game Reserves with MWCT/SSWS
  and Bucknell (camera traps, ranger+community biomonitoring, lion confirmed
  in SNP 2022), EU-funded under NaturAfrica, with a 2023–2026 grant cycle
  turning now. The plan's "no effective management here" was true only of
  Numatina/Boro and had to be narrowed. FFI also already calls its scouts
  **Community Wildlife Ambassadors (CWAs)** — the plan's own acronym for
  Community Wildlife Areas, an avoidable collision.
* **African Parks holds a 10-year agreement (Aug 2022) for Boma/Badingilo and
  the Jonglei landscape**; Boma–Badingilo was inscribed as South Sudan's first
  UNESCO World Heritage Site in July 2026. That is the working precedent for
  how a management agreement, an aerial survey and a designation get done
  here. The dump only has the 2022 AP–WCS dispute.
* **The MWCT minister named in `SS contacts from Jean.xlsx` is out of post** —
  Hon. Denay Jock Chagor holds it as of mid-2026. Treat every government
  contact in that sheet the same way: the post survives, the name may not.

The EU delegation also runs a standing "Friends of Conservation" forum in Juba
on implementing the 2026 Act (4th session Aug 2026) — a convening table that
already exists for exactly this plan's subject.

Rule of thumb: **the dump is authoritative on law and stale on people.** Legal
status from `docs.sqlite3`; every named human or organisational relationship
re-checked before use.
