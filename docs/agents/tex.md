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
- **Five tables**: AOI at a glance, zoning, staffed zones (tabularx, grey `\g{}` cells for the CAR/DRC/SDN
  strands), Tambura schedule (verbatim `boundary_schedule` from facts), budget (SSD + Sudan; CAR/DRC grey,
  excluded). Two landscape figures wrapped in `\afterpage{\clearpage\newgeometry{margin=10mm} … \restoregeometry}` so the preceding page fills; map pages are `\thispagestyle{empty}`, image `height=.93\textheight,keepaspectratio`, captions two lines ("legend on the map") — the long legend prose was clipped off the page bottom on 2026-09-07.
- **Citations** are IEEE numeric (`biblatex`/`biber`), `refs.bib` beside the script (copied into
  `reports/pip_latex/` by `build_all.sh`). Dossier file names use `\path{}` so they break. The 2022
  confidential field report is *never* cited; its names appear with “verify before contact”.
- **Share links** come from `share.json`; `share.py [keys]` uploads (`POST /api/files`) and mints 365-day
  guest links (server max), keys: `aoi_view geology_view gpkg pdf pdf_share maps budget dossier review`.
  `pdf_share` is minted **after** the full `pdf`, because the small PDF's box quotes the full one's URL.
- **Re-uploading a PDF mints a new slug**: after `share.py pdf`, rebuild `--share`, then `share.py pdf_share`, then revoke the two old slugs (`UPDATE short_links SET revoked_at=…`). Audit rule (2026-09-07): the only live guest links in the XSA owner account (principal 6) minted on/after 2026-08-17 must be exactly those printed in the PDFs (+ `pdf_share`); everything earlier is phase I and stays.
- **Checks after a build**: `grep -E "^!|undefined|Overfull .hbox .[0-9]{2}" main.log` must be empty;
  `pdftotext -layout main.pdf -` split on `\f` — every page but the last must have text (no float-only
  pages except the two maps).

## Layout choices (so nobody re-litigates them)

Palatino (`mathpazo`), 150 mm text width, `titlesec`, `parskip`, `tcolorbox` title box in Habsburg yellow
(`#F2D33C`), IEEE bibliography in `\small\RaggedRight`. Keep it that way unless the author asks.
