# ACLED, and what it is allowed to tell us about mining

_Read when working with `scripts/acled_*.py`, `scripts/mining_reference.py`,
`scripts/eval_reach_strata.py`, or `data/eval/{acled,mining_reference*}`._

**ACLED is not a mining dataset and we do not hold one.** It enters this project
in exactly one role: a description of **our own truth sets' reach**. The geology
affinity model (`scripts/geomaps/eval_affinity.py`) is scored against occurrence
lists that are all reachability-limited, and a score is only about rocks if the
list could have found a mine anywhere the model grades.

## Access: we authenticate fine and are not authorised

ACLED retired static API keys in 2025. Access is a myACLED account plus cookie
auth or an OAuth password grant (`POST /oauth/token`, `grant_type=password`,
`client_id=acled`). Credentials: `ACLED_USERNAME` / `ACLED_PASSWORD` in
`secrets.env`.

The distinction worth remembering: **authentication succeeding tells you nothing
about authorisation.** Our token mints (24 h, `scope: authenticated`) and then
every `/api/acled/read` returns **403**, as does the event-data download page
("This content isn't available at your current access level"). Event-level
access needs Research/Partner/Enterprise, assigned per organisation — request via
access@acleddata.com. `scripts/fetch_acled.py` is the event-level fetcher, kept
for the day that lands; it prints the 403 explanation rather than an empty pull.

| Surface | Granularity | Verdict |
|---|---|---|
| `/api/acled/read` | event-level | **403** |
| Explorer `newexplorer/api/details` | daily, last 12 months only; `time_range=year` silently degrades to **monthly** | too shallow, window floats |
| **Africa aggregated xlsx** | week x admin1 x sub-event type, 1996→now | **what we use** |

Paging the Explorer day-by-day was considered and rejected: it reconstructs a
product we are not licensed for, and it is *worse data* than the aggregated file
we are openly offered (which is weekly and 30 years deep).

## What we keep: scalars, never the dataset

`scripts/acled_adm1.py` reads the 279k-row sheet and writes **77 kB of per-ADM1
scalars** (`data/eval/acled/adm1_conflict.json`): events, fatalities, peak weekly
exposure, a year histogram. The weekly rows are summed and dropped. ACLED's own
`.xlsx` is a **gitignored cache** of their file. We must not create a dataset
that substitutes for theirs, so the committed artefact is a summary that could
not.

```bash
set -a; source secrets.env; set +a
python3 scripts/acled_download.py    # cookie login, current xlsx (Mondays)
python3 scripts/acled_adm1.py        # -> data/eval/acled/adm1_conflict.json
python3 scripts/fetch_adm1.py        # geoBoundaries ADM1 polygons
python3 scripts/mining_reference.py  # -> data/eval/mining_reference.json
python3 scripts/acled_coverage_bias.py
python3 scripts/eval_reach_strata.py
```

Parsing traps, all paid for once: `openpyxl` cannot open the sheet (`read_only`
reports the dimension as `A1` because ACLED writes `<dimension ref="A1"/>`;
non-read-only dies on a missing `drawing1.xml`) → streaming `iterparse` + a
manual `sharedStrings` table. `WEEK` is an Excel serial (1900 epoch, Lotus bug →
`1899-12-30`). Shared strings need `html.unescape` or `Ombella-M&apos;Poko`
reaches the join key literally. Cross-checked against the Explorer's independent
12-month totals: **1.5–3%** for all four countries.

## The mining reference (the actual deliverable)

`scripts/mining_reference.py` merges every occurrence list we hold into
**3,452 sites across all four countries**, each assigned to an ADM1 polygon
(geoBoundaries gbOpen — the ACLED centroid is a *label position*: a CAR
prefecture is ~50,000 km², never treat that point as a location).

| source | sites | observation |
|---|---|---|
| `ipis_caf` | 914 | field visit, commodity + **armed actor at the pit** |
| `ipis_tza` | 447 | field visit |
| `gmis_tza` | 480 | GST occurrence register |
| `tearline_caf` | 40 | imagery census inside permits |
| `osm` | 1,363 | OSM tags (SDN/SSD/TZA/CAF) |
| `tang_werner` | 197 | satellite footprints, **no commodity** |
| `icmm` | 11 | industry database |

CAF 1,272 · SDN 1,097 · TZA 1,012 · SSD 71. Only 47 of 80 ADM1 units hold a
single known site — the empty 33 are the subject, not a gap to hide. ADM1 name
joins need accent folding **plus** an explicit alias table (`ADM1_ALIAS`):
Zanzibar is Swahili in ACLED and English in geoBoundaries, and Gezira/Al Jazirah
differ outright. An unmatched name is reported, never dropped.

## Finding 1: at province scale, coverage is NOT conflict-biased

`scripts/acled_coverage_bias.py`, per country and per source: Spearman rho with
permutation p, Mann-Whitney, Cliff's delta. **No significant association
anywhere** (all p > 0.09).

The number that makes that null readable is `min_detectable_delta`: these splits
could only have caught |delta| ≥ **0.58–0.95**. So the honest statement is *"no
strong province-scale association"*, not *"no bias"* — and the script prints the
ceiling next to every null, because a null without its power is a shrug wearing
a result's clothes (invariant 1).

**This killed an earlier eyeballed claim.** "IPIS-surveyed prefectures have
median 236 events vs 400 for unsurveyed" looked like coverage avoiding conflict.
Tested: delta −0.10, p=0.86. It was noise in 17 units. Do not restore it.

## Finding 2: at site scale, the gold lift depends on who was standing there

ADM1 is too coarse and ACLED cannot go finer for us — but **IPIS's own surveyors
recorded an armed actor at 251 of 914 CAR mines**. That is site-resolution,
mining-specific, and independent of ACLED. `scripts/eval_reach_strata.py` splits
the CAR truth set on it and re-scores with `eval_affinity.score_sheet` itself
(imported, never reimplemented, so a stratum cannot be scored by a different
rule), each stratum against random ground in its **own hull**.

| | armed | unarmed | ratio |
|---|---|---|---|
| gold, units | **2.04** | **1.31** | 1.55 |
| gold, junctions | 1.89 | 1.70 | 1.11 |
| diamond, units | 1.00 | 0.94 | 1.07 |

Capture of gold sites by gold-graded units: **31.3% armed vs 20.5% unarmed,
p=0.0033** (20k label permutations). Geography is the obvious confound — armed
sites are unevenly spread across the 7 prefectures, and so is the geology — so
the permutation is repeated **stratified within prefecture**: **p=0.0021**. It
survives.

Per invariant 12 the permutation tests the **capture numerator only**; each
stratum's area denominator is its own hull, so p qualifies the capture
difference and the 1.55 is the effect size beside it. The script says so.

**What this means for every CAR gold lift we publish.** The headline number
(1.55 over all IPIS gold sites) is an average over two populations that score
differently. Quote a gold lift without naming its stratum and you are quoting a
number whose value depends on a variable you did not mention. The plain reading
— armed presence concentrates where the gold-bearing rock is, because that is
what is worth holding — is a *hypothesis about our truth set*, not an ACLED
finding, and note it cuts against the intuition we started with: the model looks
*better* on the harder-to-reach stratum, not worse.

Diamond is UNMEASURED at junctions (no denominator) and flat at units. It should
be: the affinity model makes a weaker diamond claim.

## Licence — the constraints that shaped the design above

Terms: <https://acleddata.com/contentusage> · Attribution:
<https://acleddata.com/attribution-policy> (both 8 July 2025).

Required, and present in every file we write (`source`, `accessed`, `citation`,
`terms`, `notice`):

* Name it **ACLED** or **ACLED (Armed Conflict Location & Event Data)** —
  ampersand always — with a link and the **access date** (living dataset,
  updated weekly; the date names the snapshot).
* Citation: Raleigh, Kishi & Linke, *Political instability patterns are obscured
  by conflict dataset scope conditions, sources, and coding choices*, Humanities
  and Social Sciences Communications, 25 Feb 2023,
  doi:10.1057/s41599-023-01559-4.
* A file handed to another party carries ACLED in a **source field** — in the
  layer, not just a README, if this ever reaches a `.gpkg`/KML export.
* State manipulations. We sum weekly rows to ADM1 totals; `unit` says so.

Prohibited, each a live constraint here:

* **Do not attribute our analysis to ACLED.** The lifts, the strata, the
  coverage verdicts are ours. ACLED is the source of event counts and nothing
  else. Every output carries `notice` saying this — keep it.
* **Do not build a substitute, or benchmark one.** The asymmetry that makes our
  use legitimate: we use ACLED to benchmark **our geology model**. So —
  aggregated scalars only, **no conflict layer in the UI**, no ACLED rows in a
  public export, and the raw xlsx stays gitignored.
* **Do not train/test ML on ACLED content** where that substitutes for ACLED or
  grants access to its data. The affinity model is fitted to lithology and
  scored against mine occurrences; ACLED describes *coverage*, and putting a
  conflict count into the feature vector is the line. Note that Finding 2 does
  not use ACLED at all — it uses IPIS's own field observation, which is why it
  can be this specific.
* No use that harms or disparages any group; represent the methodology
  faithfully; correct errors promptly; ask admin@acleddata.com when unsure.

Interpretation is at ACLED's sole discretion — when in doubt, ask them rather
than reason about it here.
