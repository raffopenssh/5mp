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
`source`. South Sudan = **OCHA COD-AB v03 (2022-12-19)** — the ten post-2020 states with
official spellings (Warrap, Western Bahr el Ghazal …), 79 counties, 512 payams; other
countries = GADM 4.1 level-2 (no payam level). Roll-ups in `facts.json` `deploy`: `counties`,
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
