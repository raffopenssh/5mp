# EASY plan → budget → summary: one command, one facts file

`python3 scripts/easyplan.py` regenerates everything a plan change touches, in order:

1. **`data/plan_zones/solver/facts.json`** — every number the documents quote, derived from
   `deploy.json`, `solve.json`, `zones.geojson`, `rank_conservancies.json`, `movement.json`,
   `threat.json`, `frontier.json`, `conservancy_units/validation.json`, `data/eval/pip_facts.json`
   and `map_belt.json` (written by `build_map.py --planner …`, the "184,879 people beyond 40 km" line).
2. **`reports/BUDGET_EASY_<yyyy-mm>.{txt,xlsx}`** — `scripts/easybudget/build_budget.py`. Quantities
   are *functions* of the deployment (teams per year, new teams, focal points) and `docs/plan/plan.yaml`.
   The xlsx has **three editable sheets** (`Budget` line table with dropdowns for rate code and delivery
   unit, `Rates`, `Assumptions`; yellow cells) plus `Locations` (per-site weights = months active), a
   formula-only `Allocation` long table (line × site × year, direct and loaded), and **real pivot tables**
   over it (`Summary`, `By action`, `By location`, `By strand`, `By delivery unit`; one shared cache,
   `refreshOnLoad`). Helper columns go last and hidden. Verify with LibreOffice:
   `soffice --headless --convert-to xlsx` then read with `data_only=True` — pivot totals must equal
   `facts.budget.total` / `ssd_total` / `strands.*.total`. We do **not** drill: the corridor water line is
   `solar_pumps` (pump on an existing borehole), not boreholes.
3. **`reports/PIP_SUMMARY_EASY.txt`** (+ copy in `docs/plan/`) — Jinja render of
   `docs/plan/PIP_SUMMARY_template.txt`. Prose is prose; every number is `{{ }}`.
4. **`reports/PIP_SUMMARY_EASY.pdf`** — `scripts/pip_pdf.py` (needs `python3-reportlab`): the .txt on
   A4, all monospaced, prose re-wrapped; any table wider than a portrait line goes on a landscape
   page of its own (ECHO/TANGO, jurisdiction). Tables are runs of 2-space-indented lines — keep that
   indent in the template or the PDF will re-wrap them as prose.

`--check` exits 1 if the summary on disk differs from a fresh render (drift test).

## Answering a question about a team or zone — read ONE file

`data/plan_zones/solver/facts.json` → `deploy.teams[<code>]` (E1…E5 ECHO, T1…T5 TANGO,
F1…F3 FP). Each team carries place, year, zone uid, area, people, gold, and the boundary
three ways: `boundary` (≤4 names, for prose), `boundary_legal` (metes-and-bounds with
coordinates, from `plan_boundary.py`), `boundary_in_words` (≤3 plain sentences) and
`boundary_legs` (structured). There is no Numatina team; "Numatina" is the park's name and
a hand-drawn grazing zone. Do not open `ZONES.txt`/`DEPLOY.txt` unless facts.json lacks it.

```bash
python3 -c "import json;t=json.load(open('data/plan_zones/solver/facts.json'))['deploy']['teams']['E1'];print(t['place'],t['boundary_legal'])"
```

## Jurisdiction (2026-09-06)

Every team carries `jurisdiction` (from `plan_boundary.py`): county rows with `state`,
`state_pcode`, `county_pcode`, `pct` of zone area, `payams[]` (name, p-code, pct ≥ 1 %) and
`source`. All four countries = **OCHA COD-AB** (HDX, `data/admin_cod/`): SSD v03 2022-12-19
(10 states, 79 counties, 512 payams — official spellings: Warrap, Western Bahr el Ghazal …),
COD v01 admin-3 secteurs, CAF v02 admin-2 sous-préfectures, SDN v03 admin-2 localities. GADM
was dropped 2026-09-06 because it was **wrong where it mattered**: it put E3 in "Buram" (COD-AB:
Al Radoum locality) and merged Bambouti sous-préfecture into Obo (24 % of T4). Roll-ups in `facts.json` `deploy`: `counties`,
`n_counties`, `n_payams`, `states`, `admin_source`, `mining_desks` (state → team codes ≥ 5 %
of a zone — the Mining Act 2012 licence desks). The template uses them in three places: the
Mining Act paragraph ("state desks concerned are …"), the s.14 registration paragraph
(counties/payams to sign), each ECHO sentence (`counties_text`), and the "Jurisdiction of the
staffed zones" table. Never type a state or county name into the template.

## Where to edit what

| Change | Edit | Then |
|---|---|---|
| horizon, start month, months paid, one-offs (survey, solar pumps), per-team ratios | `docs/plan/plan.yaml` | `easyplan.py` |
| a unit cost, a budget line's logic, a new line | `RATES` / `lines()` in `scripts/easybudget/build_budget.py` | `easyplan.py` |
| the proposal's wording | `docs/plan/PIP_SUMMARY_template.txt` | `easyplan.py` |
| which zones/teams | re-run `plan_solver.py solve` → `plan_deploy.py` → `plan_boundary.py --narrate` → `build_map.py --planner` | `easyplan.py` |
| a cross-border budget strand (cap, id prefix, law, dashed style) | `STRANDS` in `scripts/plan_deploy.py` (+ `STRAND_CAT` in `build_budget.py`) | full chain below |
| a country's ECHO cap | `plan_deploy.py --echo-car-y2 / --echo-cod-y2 / --echo-sdn-y2` | full chain below |

**Full chain after a deploy change** (the cheap→expensive order; each step reads the previous one's file):

```bash
python3 -W ignore scripts/plan_deploy.py --no-llm          # 1 min, look at DEPLOY.txt first
python3 -W ignore scripts/easypip/build_map.py --planner data/plan_zones/solver/zones.geojson --out /tmp/map.png   # 25 s, LOOK at it
python3 -W ignore scripts/plan_deploy.py --narrate         # LLM: only new/moved teams (cache in deploy.json.prev)
python3 -W ignore scripts/plan_boundary.py --narrate       # LLM: only zones whose legal text changed
python3 -W ignore scripts/easypip/build_map.py --planner data/plan_zones/solver/zones.geojson --out reports/DEPLOY_MAP_2026-09.png --pdf
python3 -W ignore scripts/plan_zones_package.py            # GPKG/xlsx with bd_* boundary columns
python3 -W ignore scripts/easyplan.py                      # facts → budget → summary → pdf
```

Looking at the map without blowing the context (AGENTS.md invariant 17):

```bash
python3 -c "from PIL import Image; im=Image.open('/tmp/map.png'); s=im.size[0]/1000
im.resize((1000,int(1000*im.size[1]/im.size[0]))).convert('RGB').save('/tmp/map_small.jpg', quality=70)   # overview, ~100 KB
im.crop((int(87*s),int(46*s),int(176*s),int(120*s))).save('/tmp/crop.png')"   # detail: a box in overview-pixel coords × s
```

Show the user the `/tmp` map **before** the two `--narrate` steps: a wrong plan is cheap to fix before the LLM has
written briefs for it. `deploy.json` has `robustness` (`chosen`, `bench`, `swapped_out/in`) and `strands`; if either
key is missing the template silently drops those paragraphs — check `PIP_SUMMARY_EASY.txt` for "How firm" and
"Across the borders".

**Never edit `reports/PIP_SUMMARY_EASY.txt` or `BUDGET_EASY_*.txt` by hand** — they are outputs.

## Template variables (the ones you will need)

`F` = facts.json; `B` = `F.budget` (`total`, `per_year`, `first_6_months`, `action4_register`,
`corridor_price`); `P` = plan.yaml; `CB` = the largest solved core block (the machine's Pongo-Wau park);
`T[code]` = a team dict keyed **by team code** (`E1`, `T2`, `F3`), with `place`, `year`, `area_ha`,
`people`, `gold`, `reported`, `watchlist`, `new15`, `named_pct`, `boundary` (named linear features
only — never a geological contact), `routes`, `herds`, `onset_name`, `shield_pct`, `urgency_rank`,
`second_team_of` (TANGO on an already-served corridor). Lists: `echo_all`, `tango_all`, `tango_zones`
(one per corridor), `fp_all` (with `serves_codes`), `echo_by_year[1|2]`, `fp_y1`, `fp_y2`.
Filters: `num` (1,234), `mha` (2.45 million ha), `ha`, `usd`, `musd`, `words` (five).
Team codes are the join key: the prose says "the Wau conservancy (E1)", so a re-deploy that
moves E1 moves the sentence.

## Budget model in one paragraph

Team = `plan.team` (4 scouts + 1 leader), FP = 1 person. `team_months = Σ echo·months_echo + tango·months_tango`
per year drives scouts, leaders, rations, bike running, messenger subscriptions. `new_*` counts drive
one-off kit (field kit, bikes, phones, Starlink, laptops, induction training). Per-conservancy lines
(facilitation, `CBO_SEED`) follow `new_echo`; corridor lines (meetings, charters) follow team counts.
`CBO_SEED` (17 k/conservancy-year) is the only rate taken from AP SSD's steady-state conservancy model
(`docs/plan/reference_conservancy_budget.md`, ~500 k/yr for a 61-staff conservancy — what EASY seeds,
not what it costs). Delivery-unit tags `TEAM`/`FP`/`HQ` give the loaded cost per team.

## 2026-10 ET adoption

The reviewed 18-month budget (ET input, 16 Sep 2026; diff in `reports/BUDGET_DIFF_ET_2026-09.md`, adoption notes in
`reports/BUDGET_ADOPT_ET_2026-10.md`) is the current plan: **1 Jun 2027 start, two PHASES** — Phase 1 Jun–Nov 2027
(set-up, no field teams), Phase 2 Dec 2027–Nov 2028 (the one field season). The code still calls the two periods
Y1/Y2; deploy.json "year 1/2" = first wave (Dec 2027) / second wave (Apr 2028). Months per unit come from
`months_paid` (already in place) and `months_first_phase` (the phase a unit joins): 4 × 12 + 6 × 8 = 96 team-months.
Per-team-YEAR ratios (meetings, SSWS days) apply to team-months ÷ 12; all equipment is bought in Phase 1 for every
unit. A full-time **Project Manager** in South Sudan (`pm_months` + `pm_prelaunch_months`, from 1 Jan 2027) replaces
the Chinko adviser. Aviation = `savannah_hours` + `heli_hours` + `avgas_barrels` (+ SURVEY_TECH); the FLY_HR recon
line is gone. Rates added: `MBIKE_SSD`, `CBO_SEED_SSD`, `SAV_HR`, `HELI_HR`, `AVGAS_BBL`, `GAP_*` (ESTIMATE); codes in
`NO_FREIGHT_CODES` sit in category 6 without freight. Gap-fill lines live inside categories 1–11 (the PIP LaTeX
parser asserts on those numbers — do not add a 15th category). Strands keep `strand_months_paid: [7, 9]` and are
unchanged. `facts.budget` gained `equipment`, `headcount`, `aviation`/`aviation_support`, `team_months`,
`gap_fill_total`; `first_6_months` now equals Phase 1. Total USD 1.90 M (0.55 / 1.35), SSD request 1.51 M.

## Budget scenarios and the focus interface (2026-10-16)

* **`docs/plan/scenarios.yaml`** — named override sets for plan.yaml (`cuts`, `adds`, `combined`). Each entry is one
  decision: `review` (the REVIEW_V2 item it answers, or "author"), `removes`/`adds` in the line's own basis words,
  `overrides` (plan.yaml keys; an `assumptions:` dict overrides `build_budget.ASSUMPTIONS` ratios). Savings are never typed
  — `plan_deploy_frontier.py --scenario all` prints them (`FRONTIER_LEAN.txt/.json`). plan.yaml keeps its defaults until a
  scenario is picked. Option keys added for this (all defaulting to the plan as it stands): `legal_retainer_months`,
  `gis_support_months`, `security_assessment`, `mbikes_per_team`, `fp_mbikes`, `starlink_team_bases` (terminal +
  subscription + `FIELD_SOLAR` kit per team base — the control-room link), `trackers_per_tango_team`, `inreach_echo_teams`,
  `inreach_fp`, `base_vehicle_days_per_month`.
* **The frontier reads `deploy_focus_pool.json`** (the 22-site pool run), not `deploy.json` — a capped run's pool holds only
  the sites it staged, in a different order (`--pool FILE` to override). It bit once: the baseline row came out as 3 TANGO.
* **`docs/plan/focus.yaml` + `scripts/plan_focus.py pool|frontier|pick|all [--dry-run]`** — the one file AP-RCA edits when
  the spatial focus or the money changes: constraint line / by-name penalties / factor, pool caps, `caps_musd × months ×
  scenarios`, and the `pick` row. `pick` freezes plan.yaml to `docs/plan/versions/`, edits the chosen overrides and
  contract months in place (comments kept, each line tagged `# focus.yaml <scenario> <date>`), runs `plan_deploy.py` at the
  row's team caps (`--echo-y1` = the row's wave-1 ECHO count) and `easyplan.py`. Team counts are read from the frontier
  row, never typed. **Caveat:** `months: 8` changes only the team contract months (13/12/11 → 8/7/6), not `phase_months` /
  `year_labels` — the backbone calendar stays 18 months; a true Nov–Jun submission is still a plan.yaml calendar edit.
* **PM-as-FP (`pm_fp_town: Wau`, scenario `pm_is_wau_fp`, in `focus`; 2026-10-16).** The Project Manager, based in Wau, is
  the wave-1 Wau focal point. Mechanics: `plan_deploy.py --pm-fp-town` (default = plan.yaml) seeds the town as the only
  wave-1 FP (`pm: true`, staff 0, `why` says DECISION); `--fp-y2 N` = N *paid* FPs on top, all wave 2 where the teams need
  one. The frontier seeds the same unit (free, outside `--max-fp`) and `assign()` puts every paid FP in wave 2 when a PM
  unit is present; `FP`/`staff` columns count paid FPs only (`=PM` marks the unit). Budget: `by_year.fp/new_fp` exclude
  `pm` units; the unit's own weight in the per-unit pivot is 0 (it is costed in the PM lines); the Wau desk keeps office
  running + Starlink months (`d["base_pm"]`); `fp_away` (FP flights) is now counted **by place** ("Wau" not in place), which
  moved the baseline row by +3.1 k — the old `fp - 1` assumed FP #1 was Wau while the frontier had put F1 at Tambura.
* **`docs/plan/versions/`** — frozen plan.yaml copies at decision points (README there); `cp` one back to reverse course.
* `easyplan.py --check` **re-renders facts.json and the budget before comparing** — `git checkout` them if you did not mean
  to publish (it is not a read-only check).

## Versioning the plan — two repos, how to reverse course (2026-10-01)

A plan change touches two places, and both are under git:

| What | Where | Tracked by |
|---|---|---|
| Inputs + decisions: `docs/plan/plan.yaml`, the summary template, `facts.json`, `deploy.json` + geojsons, `DEPLOY.txt`, scripts (`easyplan.py`, `plan_deploy.py`, `easybudget/`) | 5mp checkout | **5mp git** (GitHub) |
| Outputs + the report: `reports/BUDGET_EASY_*`, `PIP_SUMMARY_EASY.*`, maps, `ZONING_PLAN_*.gpkg`, `pip_latex/` (TeX, PDF, DOCX, `share.json`), and the report generator `reports/piptex/` (`scripts/piptex` is a symlink to it) | `reports/` | **local git repo `reports/.git`** — never pushed; the report and data are not public, 5mp ignores `reports/` and `scripts/piptex` |

Rules:
- **One plan change = one commit in each repo**, same wording, so the pair can be found again (`git log --oneline` in both;
  explicit paths, never `git add -A`). Commit `reports/` after every `easyplan.py` / `build_all.sh` / `share.py` run.
- **To reverse a plan decision:** `git log -- docs/plan/plan.yaml` in 5mp → `git checkout <rev> -- docs/plan/plan.yaml data/plan_zones/solver/deploy.json …`
  → `python3 scripts/easyplan.py` → `scripts/piptex/build_all.sh`; the `reports/` repo then shows the outputs' diff against the
  version that was shared (and `git -C reports checkout <rev> -- pip_latex/share.json` restores which guest links were live).
- **After a restart / new VM:** both repos must exist; if `reports/.git` is gone, the shared PDFs are still retrievable through
  the guest links in `docs/agents/tex.md` (slugs listed per share) and `data/shared_files/`. `scripts/piptex` must be a
  symlink to `../reports/piptex` (`ln -s ../reports/piptex scripts/piptex`), otherwise `build_all.sh` is missing.
- Decisions that are *not* in `plan.yaml` (hand edits such as "TANGO year = 1", "deploy frozen on the 7 Sep herd model") go into
  `deploy.json → params.note` **and** `docs/agents/zoning.md`, so a re-run cannot silently undo them.

## History

The August 2026 three-year budget (1.68 M) lives in `scripts/deprecated/build_budget_3yr_2026_08.py`
and is still imported by the August PIP generators (`easypip/pip_facts.py`, `build_docs.py`), which
describe the *hand-drawn* zones. Superseded reports are in `reports/superseded/` (gitignored).
Result 2026-09-06 (superseded): 2 years Nov 2026–Oct 2028, USD 1.52 M (0.50 / 1.03), 21 → 53 field staff.

## PIP LaTeX report

See `docs/agents/tex.md`.
