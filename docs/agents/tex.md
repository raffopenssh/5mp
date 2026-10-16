# PIP LaTeX report (Radom–Numatina–Southern)

**Local only.** `scripts/piptex/` is git-ignored (it carries names from confidential field reporting and
live guest links); `reports/` is ignored anyway. The scripts that *compute* the numbers are public; the
report prose and data are not. Do not move anything from `scripts/piptex/` into a tracked path.
`docs/plan` and `data/plan_zones/solver` are likewise symlinks into `reports/` (plan inputs + solver outputs);
both were purged from 5mp history on 2026-10-16 — never re-add them.

## Build

```bash
scripts/piptex/build_all.sh          # main.pdf (30 MB) + main_share.pdf (1.5 MB), both in reports/pip_latex/
```
**Versioning (2026-10-01):** `reports/` is its own **local** git repo (`reports/.git`, never pushed — the report and data are not
public; the 5mp repo ignores `reports/` and `scripts/piptex`). The generator lives at `reports/piptex/`; `scripts/piptex` is a
symlink to it, so every command below still works from the 5mp root. **Commit in `reports/` after each build/share** (explicit
paths, no `git add -A`): `cd reports && git add piptex pip_latex <changed deliverables> && git commit`.

Needs TeX Live (`texlive-latex-extra`, `biber`, `latexmk`; installed 2026-09-07) and `share.json`.
`build_pip_tex.py [--share]` writes `main.tex`/`main_share.tex`; `--share` swaps the two maps for
2400-px JPEGs and adds a line in the title box linking to the full-resolution PDF.

## Rules the generator follows

- **No typed numbers.** Everything comes from `data/plan_zones/solver/facts.json`, `data/eval/pip_facts.json`,
  `data/eval/regional_mining/harness.json`, `data/plan_zones/solver/movement.json`, the settlement rows in
  `db.sqlite3` (same filter as the app; asserted equal to `pip_facts.xsa`) and the budget **txt** headline
  block (regex-parsed, so the report cannot disagree with `BUDGET_EASY_*.txt`). Change a number upstream
  and re-run `scripts/easyplan.py`, then `build_all.sh`.
- **Prose is the author's.** Edit wording in `build_pip_tex.py` in place; do not restructure or add
  enumerated summaries. Numbered `\section`s only for the seven chapters; `\subsection` is unnumbered
  italic run-in and used twice (General findings, Legally important). No TOC.
- **Zone E1 schedule** is a breakable `tcolorbox` (same yellow as the title box) holding a `fvextra` `Verbatim` of the generated text, placed *after* the plan prose (end of §5), with `\vspace{\baselineskip}` before it; `build_docx.py` maps it to `quote`+`verbatim`. The zoning/staffed tables are `[H]` directly after the deployment-map page so figure → tables → prose keeps its order.
- **Five tables**: AOI at a glance, zoning, staffed zones (tabularx, grey `\g{}` cells for the CAR/DRC/SDN
  strands), Tambura schedule (verbatim `boundary_schedule` from facts), budget (SSD + Sudan; CAR/DRC grey,
  excluded). Two landscape figures wrapped in `\afterpage{\clearpage\newgeometry{margin=10mm} … \restoregeometry}` so the preceding page fills; map pages are `\thispagestyle{empty}`, image `height=.93\textheight,keepaspectratio`, captions two lines ("legend on the map") — the long legend prose was clipped off the page bottom on 2026-09-07.
- **Citations** are IEEE numeric (`biblatex`/`biber`), `refs.bib` beside the script (copied into
  `reports/pip_latex/` by `build_all.sh`). Dossier file names use `\path{}` so they break. The 2022
  confidential field report is *never* cited; its names appear with “verify before contact”.
- **Share links** come from `share.json`; `share.py [keys]` uploads (`POST /api/files`) and mints 365-day
  guest links (server max), keys: `aoi_view geology_view gpkg pdf pdf_share docx maps budget dossier review`.
- **DOCX for reviewers**: `build_docx.py` (run by `build_all.sh`) pre-processes `main_share.tex` (tcolorbox→quote, tabularx/multicolumn/`>{}` column specs flattened, `\ref` numbered by order, table captions become a bold `Table N.` paragraph — pandoc 3.1 otherwise emits captions twice) and runs `pandoc --citeproc` with `ieee.csl`. The PDF stays the reference; the docx is for comments. LibreOffice Writer is not installed, so check with `pandoc x.docx -t plain`.
  `pdf_share` is minted **after** the full `pdf`, because the small PDF's box quotes the full one's URL.
- **Re-uploading a PDF mints a new slug**: after `share.py pdf`, rebuild `--share`, then `share.py pdf_share`, then revoke the two old slugs (`UPDATE short_links SET revoked_at=…`). Audit rule (2026-09-07): the only live guest links in the XSA owner account (principal 6) minted on/after 2026-08-17 must be exactly those printed in the PDFs (+ `pdf_share`); everything earlier is phase I and stays.
- **Checks after a build**: `grep -E "^!|undefined|Overfull .hbox .[0-9]{2}" main.log` must be empty;
  `pdftotext -layout main.pdf -` split on `\f` — every page but the last must have text (no float-only
  pages except the two maps).

## Layout choices (so nobody re-litigates them)

Palatino (`mathpazo`), 150 mm text width, `titlesec`, `parskip`, `tcolorbox` title box in Habsburg yellow
(`#F2D33C`), IEEE bibliography in `\small\RaggedRight`. Keep it that way unless the author asks.

## v2 review round (2026-10-01, commit d7c71078 →)

- **Map pages**: image `height=.89\textheight` (was `.93`). The landscape text block is 186 mm; at `.93` the
  3-line Figure 1 caption overflowed by 10 pt (`Overfull \vbox … while \output is active`), the `[H]` figure
  spilled to a second landscape page and left a blank page 4. The `\afterpage{\clearpage\newgeometry…}`
  wrapper was innocent — check `grep "Overfull .vbox" main.log` before blaming it. 21 pages, no empty page.
- **Org chart** (Figure 3) is black-and-white TikZ in absolute mm: lane heads (LANDSCAPE / PROGRAMME / FIELD) with a hairline, cards 38.5 mm wide on a 25/75/125 grid, partners dashed, field teams grey-filled with a count badge, orthogonal reporting lines, key row beside the NGO card. One card reads "Project Manager/National Coordinator" (author's call), Wau-based, two-line description. `color=black` on the picture so `\sh{}` colours only the caption.
- Share 2026-10-01: new slugs pdf `g-w9h7mb42xnxx4bq5`, pdf_share `g-42vbvdj7x78pxbfk`, docx `g-m2n3rw2d4pq3btd6`,
  maps `g-97dbb6zhjrhm6gw8`, budget `g-npdxrw8vtm3mf8wj`, dossier `g-g2g5qxkzxczxsqfh` (gpkg/legal_brief/aoi_gpkg_light/views unchanged).
- Revoked 13 superseded guest slugs (the six 2026-09-07 file links, the intermediate docx minted before the `--share` rebuild, and the pdf/pdf_share/docx of the first 2026-10-01 pass, superseded by the org-chart redo, twice).
  Still live but not in the PDFs: six 2026-09-15/16 `map view`/`tmp ui inspect` links on principal 6 — not PIP, left for the owner to decide.
- **Gazettement, not a new park (2026-10-01, author's call):** the plan no longer proposes a national park; the mid-term target is Numatina's *existing* gazettement made real (Schedule IV boundary description under ss. 8(3)/80, mining ban written in, 1953 forest-reserve overlap settled). Red edits in exec summary, General findings, Mining Act box, MWCT/MoEF entries, Priority action 2, §5(iii) block paragraph (`NUM_GR_PCT` = WDPA Numatina share of the core_block zone, read from `zones.geojson` overlaps, asserted present), §5 target sentence, Conclusion. Headline figure `TOTAL_M` = South Sudan request + Sudan strand (CAR/DRC excluded), footnoted; `\label{sec:cost}` added. 22 pages now (last bib entry spills to p. 22; no blank page).
  Share: pdf `g-pxk4n2qj7dr2hg2k`, pdf_share `g-6xkjz6px5ct3z3px`, docx `g-76wrj2ndfv2fsk8q`; the three org-chart-round slugs + the uncoloured docx `g-psrdv2d4k2spjxvh` revoked (17 total).
- **Review colours in the docx**: pandoc drops `\color{}` but keeps `\textcolor{}` as a Span `style`; `build_docx.py` rewrites the `\au`/`\sh`/`\flag` macros to `\textcolor`, `colors.lua` maps the span to character styles `AuthorEdit`/`ShelleyEdit`, which are injected into a generated `reference.docx` (pandoc default + two `<w:style type="character">` with the same hex). Check: `unzip -p x.docx word/document.xml | grep -o 'rStyle w:val="[^"]*"' | sort | uniq -c`.

## Round 3 (2026-10-01 afternoon) — IN PROGRESS, handover

Source of the asks: the author's comments in `/tmp/shelley-uploads/PIP_Radom_Numatina_Southern_v1_2026-09-2-3.docx`
(18 comments C0–C17 + tracked edits; extract with the `comments.xml`/`w:ins`/`w:del` walker — python-docx is not installed).

**Done (uncommitted parts live in the git-ignored `scripts/piptex/`, so check the files, not the log):**
- Budget: PM + national coordinator **merged** into one post (`coordinator_months: [0, 0]`, `SAL_INTL` relabelled, headcount/
  recruitment/laptop lines follow); solar pumps **stay** (author: "I thought this was referring to water points") but are never
  called "water points" anywhere; SAV_HR/HELI_HR/MBIKE_SSD notes rewritten as *AP in-house dry operating cost, fuel in
  AVGAS_BBL* / *AP SSD delivered price vs East-African retail* — no FLAG left in the workbook. New headline SSD 1.56 M,
  SSD+SDN 1.67 M (`reports/BUDGET_EASY_2026-10.*`).
- Situation map: isochrones **wine red** `#7a1f2e` (lines, labels, legend swatch), fire hairlines alpha .035, labels
  white-haloed bold 8 pt, **hand-tuned: monthly isochrones only, longest line only, 1–2 labels each** (6 labels: 30 Oct,
  29 Nov, 29 Dec). `build_all.sh` now makes `*_full.jpg` (q88, full res) + `*_small.jpg` from `reports/*.png` — the full PDF
  carried stale Sep-7 PNG copies before; full PDF is 6.8 MB now (author's cap ~10 MB).
- Report: title "Version 2, 1 October 2026", no colour legend of the old round (green/red macros are identity now);
  **`\nw{}` = pink (#C2185B) marks everything new in this round** (default on; `--clean` off; docx style `NewEdit`).
  Exec summary in a yellow tcolorbox (fits page 1), "How to read the numbers and links" grey box after it; links are
  blue in-text `\href`s, footnote URLs only for `gpkg`/`aoi_gpkg_light`/`budget` (`FOOTNOTE_URL_KEYS`); **no `:8000`**
  anywhere (`share.py PUBLIC`, `share.json` rewritten). Legally important back to a plain `description` list with the
  legal-brief footnote; §4 institutions shortened; E1-in-numbers box removed (no extra sentence needed — the schedule
  box's intro lists what every zone stores); Boro-Medina paragraph no longer proposes T6; §6 rewritten (no "reviewed
  budget of 16 Sep" sentence, no flags); budget/headcount/equipment tables → **Appendix A** after the bibliography;
  conclusion takes the author's text + the "recommended for forty years" chain (hillman1983, wcs2007/2010/2016,
  brooks2023, 2026 Act).
- Org chart figure is `[tbp]` now (was `[H]`) so the Gantt page is not half empty.

**Open:**
1. ~~Timeline figure~~ **redone 2026-10-01 evening** — `scripts/piptex/timeline_tikz.py` (`timeline_tikz(P, B, y1, y2, tex, usd, teams=T)`;
   run standalone → `/tmp/tl/tl-1.png`). A *season calendar*, not a Gantt: month strip (dry months Habsburg, bold initials; "wet
   season", never "rains"), faint dry wash through the lanes, lanes PAPER / PEOPLE / TANGO teams / ECHO teams / Focal points / AIR.
   Team lanes use the **map colours** (corridor red, community purple, teal) with one numbered dot per deployment month derived from
   `deploy.teams` × `plan.wave_dates_by_kind`, a dotted recruitment line per batch (`recruit_lead_months` before its own dot, staggered),
   the line thickening with people. Gate/close = wine-red dashed verticals + diamonds; phase brackets carry the SSD budget. Labels sit
   *beside* events, never inside bars. Checked in greyscale + deuteranopia. **Still to fine-tune (next conversation):** page flow —
   `\FloatBarrier` before the Conclusion (`placeins`) keeps Fig 4 / Table 5 out of the References but makes p. 16 (org chart) and
   p. 17 (risk table) float-only pages; 24 pages now.
1b. **Waves by kind (author, 2026-10-01 evening):** TANGO corridor teams deploy **Nov 2027** before the herds (`movement.json` herd-front
   weight Nov 1,158 / Dec 1,583 / Jan 3,212 / Feb 1,190 / Mar 255 / Apr 88), ECHO Dec 2027 + Jan 2028, FP Nov + Jan. Phase 1 = Jun–Oct
   (5 mo), Phase 2 = Nov 2027–Nov 2028 (13 mo). `plan.yaml`: `wave_dates_by_kind`, `months_paid tango [0,13] / fp [5,13]`,
   `pm_months [5,13]`, `finance_officer_months [2.5,6.5]`, `recruit_lead_months`. `plan_deploy.py --tango-y1` (default all) /
   `--echo-y1`, `--staff-y1` 40. **The deploy pipeline was NOT re-run**: `muse-glimmer-30b` returns HTTP 400 and `--no-llm` re-picks
   sites (Bisellia/Kozi/Tonj…) — `deploy.json` + the two geojsons had TANGO `year` set to 1 by hand (`params.note`), sites unchanged;
   DEPLOY.txt header edited. 122 team-months (5×13 + 2×12 + 3×11), SSD request **1.59 M** (0.379 / 1.215), total 1.99 M; author will
   adapt the budget. Map legend says "second wave"; belt is 7 towns / 91,218 (the `--no-llm` geojsons had briefly made it 10).
1c. **Org chart in colour** (map palette: teal FP, purple ECHO, red TANGO cards + badges, Habsburg PM card). **Appendix B "Data detail:
   one zone as stored"**: the E1-in-numbers box (restored from the author's screenshot; rows read from `zones.geojson` uid 187 +
   `deploy.json` E1, as `\parbox` pairs so the breakable tcolorbox can break) + the E1 boundary schedule box, both moved out of §5
   (pointer sentence stays). Prose: "first wave" phrases rewritten by kind; "rains" → "wet season" everywhere (plan.yaml, budget txt,
   summary template, TeX). Budget/summary/map outputs in `reports/` regenerated (git-ignored).
1d. **Page flow fixed (2026-10-01 night):** `\FloatBarrier` moved from before the Conclusion to before References (floats interleave
   with the Conclusion text; no float-only pages), the risk register is **Appendix B** (`app:risk`, `[H]`; §6 keeps the Risks paragraph
   with a pointer), every appendix section starts with `\clearpage` (a `[H]` table under an appendix heading otherwise orphans the
   heading). GitHub-scripts sentence removed from the front box (generator is no longer public). `build_docx.py` resolves
   `\ref` to appendix letters (`app:*` by order) and `sec:cost`. 23 pages. Docx re-shared for the author's final read:
   `g-9fjgt9zsf9h6ff3h` (old `g-76wrj2ndfv2fsk8q` revoked). Final layout pass after the read.
2. Rebuild + share: `scripts/piptex/build_all.sh`; then `set -a; source secrets.env; set +a; python3 scripts/piptex/share.py pdf`,
   rebuild `--share` (the small PDF prints the full PDF's link), `share.py pdf_share docx maps budget`, revoke the four
   superseded slugs (`pdf g-pxk4n2qj7dr2hg2k`, `pdf_share g-6xkjz6px5ct3z3px`, `maps
   g-97dbb6zhjrhm6gw8`, `budget g-npdxrw8vtm3mf8wj`) per the audit rule above.
3. Decisions the author has not confirmed: the aviation-rate justification wording (AP in-house aircraft) and that the
   workplan table in §5 stays beside the new timeline figure (duplication).

## Round 5 — final (2026-10-01 night, after the author's flags)

- **Border source → OCHA (flag 3):** `plan_boundary.py` `SOURCE["border"]` had read "GADM 4.1" — wrong twice: the mesh's border
  feature is `data/world_countries.geojson` (Natural Earth 50m, `plan_conservancy_units.countries()`), and the admin set of the
  report is OCHA COD-AB. The label now names COD-AB as the line a drafter follows and says the 2 km mesh edge was cut on the NE
  outline (NE vs COD-AB: median 2 km apart, max ~10 km). Re-running `plan_boundary.py` changed **only** that label (272 zones, no leg,
  no legal text, all 272 `in_words` kept); then `easyplan.py` (budget txt byte-identical), `plan_zones_package.py --out
  reports/ZONING_PLAN_2026-10`, `plan_zones_qgis.py`. Swapping the snap feature itself to COD-AB was rejected: the zone polygons
  follow the NE line, so a COD-AB feature 2–5 km off would turn border legs into "open bush".
- **Workplan table gone (flag 4):** the calendar figure (`fig:timeline`) is emitted right after the Workplan paragraph at the end
  of §5 and is the only workplan; `WP`/`WP_ROWS` still read `plan.yaml` but are unused. Cost's first sentence points back to it.
- **"plan", not "program(me)" (flag 5):** exec summary + Conclusion; the org-chart lane is `PROJECT` (was PROGRAMME). "integer
  programme" (§5 (iii)) stays — a term of art. `plan.yaml`/budget txt untouched (one "permanent programme" in a plan.yaml row that
  no longer prints, one "older programmes" in a budget note).
- **Appendix A on one page (flag 6):** the three budget tables share p. 18 — `\footnotesize`, `\arraystretch 1.03`, captions
  `footnotesize`/`skip=3pt` inside a `\begingroup`, budget-table description column `.53`, headcount `.34/.47`, one-line captions, the
  Excel link in-text (the footnote URL is already in §6). Needs ~195 pt; the page has ~200 — if a table grows a row, drop
  `\arraystretch` to 1.0 before anything else.
- `\widowpenalty=\clubpenalty=10000`; `\why` is `\par\nobreak` so an institution's italic line cannot leave its name (it had, p. 6/7).
- **21 pages**, both logs clean. Share: pdf `g-rmnfvfm5ns9ssdpp`, pdf_share `g-zqt2q5htcjfcfj6f`, docx `g-3cp9rzd8frf86pc6`, gpkg
  `g-shzqsxwqrn33bdkn`; maps/budget/dossier/legal_brief/aoi_gpkg_light unchanged. Revoked 7 (the four 1 Oct evening slugs + three
  minted in the wrong order — mint **gpkg before pdf**, the PDFs embed the gpkg link). Live PIP guest set = 9 links, one per deliverable.

## Round 6 (2026-10-01, late) — layout fixes from the author's screenshots

- **Page after a map page overflowed the paper** (p. 5 and p. 11 ran to 839 pt instead of 790; footnote lines were cut off at the
  page edge, silently — no `Overfull \vbox` in the log). Cause: `\end{landscape}` ships the map page with `\newgeometry{margin=10mm}`
  and the *next* page's `\@colht`/`\@colroom`/`\vsize` were already set from that tall text block before `\restoregeometry` ran.
  `\fixcolht` (defined in the preamble) resets the three after `\restoregeometry` in both map wrappers. **Check after every build:**
  `pdftotext -bbox main.pdf` → max `yMax` per page must be ≤ 790 (map pages lower).
- Legally important: the legal-brief footnote hung on `\subsection{}` and printed a lone "7" under the heading; it now sits on a
  one-line lead sentence. `\interfootnotelinepenalty=10000` removed (it was not the cause and forbids legitimate splits).
- §4 institutions: one `enumerate` (the three `[resume]` blocks produced uneven gaps), `itemsep=2pt` for all enumerates.
- §5 priority actions: the action words lead in bold (Present this plan / Find out the state of the s. 80 regulations / Ask for a
  s. 24 closure order / Use the 2026–27 dry season).
- 21 pages, logs clean. Share: pdf `g-vhqp427vfw953ppk`, pdf_share `g-fh5524mts95hqg6q`, docx `g-8ptqksvk7544kz98`; round-5
  pdf/pdf_share/docx revoked. Live guest set on principal 6 since 2026-08-17: 15 = 9 PIP deliverables + the six map-view links.

## v3 S4 (2026-10-16) — Appendix C "Methods and AI note"

`app:methods` sits between the risk register and Data detail (letters shift: Data detail is D). The front "How to read" box and the §5 (i)–(iv)
paragraphs live there (`HOWTO_BODY`, `METHOD_PARAS` in the generator); Priority keeps a three-sentence summary; every "Section 5(iv)" cross-ref
reads "Appendix C(iv)". The AI disclosure is stated once, in that appendix, as what was produced with a model and what people checked — never
"should be checked" (user's rule). Constraint block prints `deploy.json` `spatial_constraint[0].name` verbatim, so `tex()` maps ≤/≥ to math.

## v3 S5 (2026-10-16) — downstream + TA archive

After any rebuild that changes the PDF, summary, ToR or budget: `python3 scripts/ta_archive_refresh_report.py` (run from `scripts/`;
imports `sha` from `ta_archive_finalize.py`) re-copies `report/` inside `reports/TA_ARCHIVE_XSA_2026-10/`, rewrites `MANIFEST.txt`
and re-zips (0.85 GB, ~3 min, zip not committed). The archive PDF keeps the project's historic spelling `Pongo-Wau-Numatinna`.
The "mineral title on the rim → expansion pauses" stop rule never existed in the PIP; it was summary/slides-only and is now a
risk-register sentence there. `build_budget.py` derives the "one-offs" and "corridor's price" prose from non-zero lines, so a
deferred line (survey package, vet campaign, pump) drops out of the sentence by itself.
Deliverable filenames follow the title: `DELIV_STEM` in the generator (`build_pip_tex.py --name` → `PIP_Radom_Numatina_Southern_v3_2026-10-16`,
from `VERSION_LINE`); `build_all.sh`, `build_docx.py`, `share.py` and the archive refresh all read it — bump `VERSION_LINE`, nothing else.
`share.py` re-uploads and mints **new** links, so run it (then rebuild once more so the PDF carries the new links) only at delivery.
C7 (16 Oct, author): the 30,000 governance seed is now the "Community outreach and engagement" line, same amount (`\revchg{5MP}{C7}`).
