# EASY plan → budget → summary: one command, one facts file

`python3 scripts/easyplan.py` regenerates everything a plan change touches, in order:

1. **`data/plan_zones/solver/facts.json`** — every number the documents quote, derived from
   `deploy.json`, `solve.json`, `zones.geojson`, `rank_conservancies.json`, `movement.json`,
   `threat.json`, `frontier.json`, `conservancy_units/validation.json`, `data/eval/pip_facts.json`
   and `map_belt.json` (written by `build_map.py --planner …`, the "184,879 people beyond 40 km" line).
2. **`reports/BUDGET_EASY_<yyyy-mm>.{txt,xlsx}`** — `scripts/easybudget/build_budget.py`. Quantities
   are *functions* of the deployment (teams per year, new teams, focal points) and `docs/plan/plan.yaml`.
   The xlsx is live formulae (VLOOKUP rates, SUMIF subtotals).
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
| horizon, start month, months paid, one-offs (survey, boreholes), per-team ratios | `docs/plan/plan.yaml` | `easyplan.py` |
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

## History

The August 2026 three-year budget (1.68 M) lives in `scripts/deprecated/build_budget_3yr_2026_08.py`
and is still imported by the August PIP generators (`easypip/pip_facts.py`, `build_docs.py`), which
describe the *hand-drawn* zones. Superseded reports are in `reports/superseded/` (gitignored).
Current result (2026-09-06): 2 years Nov 2026–Oct 2028, USD 1.52 M (0.50 / 1.03), 21 → 53 field staff.
