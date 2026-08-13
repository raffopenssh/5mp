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

---

# Crisis Tracker: the second site-resolution list, and the half of the CAR nobody surveyed

_Read with `scripts/crisistracker_*.py`, `scripts/eval_crisistracker.py`,
`data/eval/crisistracker/`._

ACLED's event level is closed to us (403, above), so the reach question was
stuck at ADM1 — a 50,000 km² bucket. **Crisis Tracker** (Invisible Children)
answers the same question at site resolution and, unexpectedly, contributes
mines: its public map is the incident database for the CAR/DRC/South
Sudan/Darfur border region, and every record carries coordinates,
`location_specifics` (whose values include *In a Mine*), a
`livelihood_activity_at_time_of_incident` of *Mining*, and — on the community
profiles — `presence_of_artisanal_mining`.

## Access is open, and that is not the same as unrestricted

No key, no login. The JSON behind the map: `/incidents.json`,
`/communities.json`, `/incidents/{id}.json`. `returnees.json` and
`combatant_locations.json` **401 and must stay that way** — those records are
withheld deliberately, for the safety of sources and victims (codebook p.6,
p.13). Do not add a login to `scripts/crisistracker_fetch.py`.

Traps, each paid for once:

* **`request_type=max_records` caps at 5000 and does not say so.** The whole
  archive is 5,168 incidents — right on the cap. A single full pull returns a
  number that looks complete and is 168 short. The fetcher pages in two-year
  windows, unions by id, and **aborts** if any window comes back at the cap
  (invariant 1).
* A filter param takes a **bare value, not `key[]=`**. `key[]=` yields a 500
  whose body is HTML, which `json.load` reports as a parse error — easy to
  misread as "the endpoint is broken" rather than "the syntax is wrong".
* Community characteristics are only obtainable one filtered pull at a time
  (`characteristics=presence_of_artisanal_mining`, joined back by id); the
  list endpoint returns no attributes. A filter that matches *all 910*
  communities means it was ignored — the fetcher treats that as an error.
* `python3 -c` with a `curl` piped in gets **403 without a User-Agent**;
  `urllib`'s default UA is blocked, curl's is not.
* An un-located incident is written `coordinates: "-,-"` and arrives as
  **0.0/0.0**. Null island is in the Gulf of Guinea; a truth point there scores
  the model against the sea.

## The commodity is only ever in the prose, so an LLM extracts it

"the Kpangou **gold and diamond mine**" (a working — geology evidence),
"they took … **some diamonds and gold**" (loot — a supply chain), "a **mining
site** in M'bres" (a working, no commodity). A keyword match cannot separate
those three, and the separation is the entire value of this source.

`scripts/crisistracker_label_prompt.py` builds the prompt; one
`llm_one_shot --model gpt-5.6-sol` pass writes
`data/eval/crisistracker/note_labels.json`, which is **committed as an
artefact** (a re-run would not be identical, so it is not a cache). The model
is asked only for extraction — what the sentence *says* — with a **verbatim
span** for every commodity claim, and `scripts/crisistracker_mines.py` checks
each span appears character-for-character in the note it came from. A model
that paraphrases its evidence has stopped extracting and started summarising.
On 164 records: 0 non-verbatim (one near miss — the evidence was in
`other_looting_types`, not the display note, so the check searches all three
text fields).

**`extracted` vs `looted` is provenance, and it is load-bearing.** Only
`extracted` reaches `commodities`; looted materials stay in
`commodities_looted`, which no geology score may consume. Of 24 commodity
mentions, **16 are loot**. Score those as lithology and you have scored a
supply chain.

## Unit: incidents are not sites

164 candidates → 65 `at_mine` → **50 sites**. The same mine is attacked
repeatedly (Yangou Waka ×3), so an incident count published as a site count is
a truth set of duplicates all sitting on one rock (invariant 7).

Clustering is 1 km single-link, **plus an identical name but only within
10 km**: "Yangou-Pendere" is reported from points **64 km apart** (a note may be
pinned to the mine, to the nearest community, or to the axis). At 1:1,500,000 a
64 km centroid sits on neither pit and crosses several map units, so beyond
10 km both rows stay and each carries `name_conflict` naming the other. A
cluster spread over 5 km, or an incident flagged
`exact_location_of_incident_unknown`, gets `precision: "approximate"` — scored
as a separate stratum, never dropped, because dropping it biases the list
toward places that are easy to pin.

## What it is worth: 41 CAR mines where we had none

`scripts/eval_crisistracker.py` (writes `data/eval/crisistracker/reach_car.json`):

| | |
|---|---|
| IPIS's 914 CAR sites span | lon **14.6–17.9** (west) |
| Crisis Tracker's 41 span | lon **21.2–24.9** (east: Haute-Kotto, Mbomou) |
| nearest IPIS mine, median | **617 km** — and **0** within 25 km |

**Every CAR lift we have ever published was measured on one half of the
country.** This is the first out-of-sample ground for that sheet, not a bigger
sample of the same ground.

It cannot be scored the way IPIS is: only 5 sites carry an extracted
commodity, so per-commodity lifts are **declined at `n<8`**, the same floor
`eval_affinity` uses. What it *can* answer is the weaker, honest question —
does a reported mine sit on ground the sheet grades for gold **or** diamond?

    n=38  capture 60.5%  area 68.2%  lift 0.89  p=0.38
          (this n could only have shown lift ≥1.23 or ≤0.73)

A null **with its power printed beside it**. On the east's rocks the model is
not distinguishable from random ground, and the ceiling says how weak a claim
that is. Note also that the network's hull is **68.2% graded vs 58.3% of the
whole sheet** — context, never a denominator: a capture measured in the hull
over an area measured over the sheet is a ratio of two different questions.

## The one thing this list must NOT be used for

Every site is here **because an armed group attacked it**. So
`site_armed_actor` is 100% by construction — it is the *selection rule*, not an
observation, and it is not comparable with IPIS's flag, where a surveyor
standing in a pit could record *nobody*. Pooling them would turn "28% of
visited mines had an armed actor" into a statement about how a list was built.
`scripts/acled_coverage_bias.py` therefore counts armed presence over
`observed == "field_visit"` rows only; Crisis Tracker's field keeps the actor
**identity** (LRA vs unidentified), which is a different claim.

Likewise a **mining town is not a mine**: the 49 communities flagged
`presence_of_artisanal_mining` (29 CAR, 20 DRC) land in
`communities_mining.json` as a context layer, with the settlement centre as
their coordinate and the workings somewhere around it.

## Attribution

Source field on every file we write: *Crisis Tracker, a project of Invisible
Children*, <https://crisistracker.org>, with the access date. Methodology:
`https://crisistracker.org/codebook.pdf`. Bulk/extended data has a request form
(About → Request Data Export) — that is the route if we ever need the
non-public fields, not a scraper.

The labels, the clustering and every score derived from them are **ours**, and
the `notice` field in each output says so. Do not attribute them to Crisis
Tracker or Invisible Children.

```bash
python3 scripts/crisistracker_fetch.py --details      # -> data/crisistracker/ (gitignored)
python3 scripts/crisistracker_label_prompt.py         # -> label_prompt.txt
# llm_one_shot --model gpt-5.6-sol label_prompt.txt -> data/eval/crisistracker/note_labels.json
python3 scripts/crisistracker_mines.py                # -> mine_sites.json, communities_mining.json
python3 scripts/eval_crisistracker.py                 # -> reach_car.json
python3 scripts/mining_reference.py                   # crisistracker joins the merged reference
```
