# PIP LaTeX report (Radom–Numatina–Southern)

**Local only.** `scripts/piptex/` is git-ignored (it carries names from confidential field reporting and
live guest links); `reports/` is ignored anyway. The scripts that *compute* the numbers are public; the
report prose and data are not. Do not move anything from `scripts/piptex/` into a tracked path.

## Build

```bash
scripts/piptex/build_all.sh          # main.pdf (30 MB) + main_share.pdf (1.5 MB), both in reports/pip_latex/
```
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
