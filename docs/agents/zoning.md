# Zoning planner — legible units, four classes, bootstrapped proposals

`scripts/plan_conservancy_units.py` (one file, ~1,600 lines; modes `build`,
`validate`, `rank`, `conservancies`, `optimize`, `describe`, `teams`, `show`, `export`). Outputs in
`data/plan_zones/conservancy_units/` (XSA) or `data/plan_zones/conservancy_units_<AOI>/`.
The map: `scripts/easypip/build_map.py --planner <optimize_*.geojson>` →
`reports/ZONING_PLANNER_MAP_2026-09.{png,pdf}` (gitignored, regenerate).
Human method note: `docs/ZONING_METHOD.md`. Status 2026-09-05: **works on
XSA; runs on CAF_Chinko but that area is too empty and has no 1930s sheets to
be a useful benchmark** (see "What Chinko taught").

## The idea in one paragraph

5MP already measures a park: people (GHSL clusters), fire detections and
fronts, clearing, cropland, mining targets, rivers. The planner turns that
into a zoning tool by (1) cutting the AOI **only along features a person can
point at** (rivers, swamp/lake edges, ridge chains, named khors, roads,
borders, 1930s *district* lines, geological contacts — **never 1930s tribal
boundaries**, see "Legible features" below), (2) measuring each piece with the *same*
rasters, (3) classifying with **one rule** whose every test is stored with its
number, (4) **growing** the best area of a wanted class from those pieces
under bootstrap perturbation so every proposal carries a support figure, and
(5) **measuring what it proposes — and what humans drew — with the same
machinery it used to propose it**. The four classes are the four the plan
already uses: `core` (park / extension), `wilderness`, `community`
(conservancy, Wildlife Act 2026 s.14), `corridor` (s.9). **Do not add
classes.**

## Pipeline (`build`)

| Step | What | Where |
|---|---|---|
| grid | 2 km cells, cylindrical equal-area `+proj=cea +lon_0=27 +lat_ts=7.5` (`CEA`; fixed for XSA — re-centre per AOI is a TODO) | `Grid` |
| legible features | `rivers()` … + `swamps()` (HydroLAKES ≥5 km² + JRC `park_waterbodies` dissolved, weight 4–5) + **point beacons** `G.beacons` (1930s village *symbols* and Capitalised place labels, 1930s water *symbols* + swamp/pool/lake labels, lone hill marks, today's OSM villages; weight 3, decay 0.7 cell) | `swamps()`, `hist_waters()`, `hist_villages()` |
| legible features (lines) | `rivers()` (HydroRIVERS ≥ order 4 or named; unnamed reaches take the 1930s water-label name when ≥2 vertices agree), `ridges()` (peak/trig symbols + `J./HILLS` labels, single-linkage ≤15 km, ≥3 pts → principal-axis polyline), `hist_boundaries()` (**district/province lines only**, weight 4; tribal and sub-tribal lines are excluded by user decision 2026-09-06 — a 1930s tribal limit is not a boundary today's communities should be asked to accept), `hist_watercourses()` (≥15 km named khors), `roads()` (trunk…tertiary), `geology()` contacts (weight 2 — steers through open bush, never overrules a river), borders, AOI edge | |
| legibility surface | 0..1, `w/8·exp(-d/1.5 cells)`, strongest feature wins; `G.fid/G.fdist` for boundary descriptions | `legibility()` |
| seeds + watershed | village clusters (10 km single linkage, pop ≥150) + 20 km empty-land lattice → `skimage.watershed` on the surface | `seeds()`, `segment()` |
| merge 1 | absorb < `--min-ha` (2,000) across least legible edge → **`lab1`** (fine units, 644 on XSA) | `merge_units()` |
| fine mesh | every village ≥20 people + 10 km lattice → **`labf`** (2,200 units) — the mesh conservancies grow from | build block |
| evidence | **`cells()`**: every measurement as a raster on the grid, computed once and cached on `G`. Settlements/pop/new15/camps/built, OSM places, fire 2024-25 (+Nov–Feb share), fronts as cell-index sets (`G.fronts`), clearing (reviewed), mining anchors/candidates/top05/watchlist (XSA only; elsewhere `mining_measured=False`), GLAD cropland 2003/2019 fraction, geology unit id + commodity-affinity weight | |
| attributes | **`attributes()`** = one `bincount` per raster over any label image + front×unit incidence + towns + boundary description (`describe_mask`) + `classify()`. `freeze=T` reuses the mesh's corridor quantile so a merged unit is judged by the same bar | |
| classify | order matters: **corridor → core → wilderness → community**. Every test appended to `d["rationale"]` as `"name value op threshold: yes/no"` | `classify()` |
| merge 2 | like-with-like only, across weak edges (< `--weak` 0.35), to `--target-ha` 300k, never above `--max-ha` 600k → `lab` (510 units) | |
| corridor axis | least-cost path over **coherence-weighted** long-front density (`G.coherence` = per-cell axial mean resultant length of long-front headings, 1 = one axis, 0 = every direction — what the eye reads as a "corridor" in the animation is density *and* coherence), penalised by people; XSA anchors Radom→Garamba, generic AOI anchors = densest rim cells on opposite sides; `closed=` re-routes around the park | `corridor_axis()` |
| write | `units.{json,geojson,kml}`, `mesh_features.geojson`, `corridor_walked.geojson`, `state.pkl` (G, lab, lab1, labf, surf, feats, T) | |

### Class rules (`classify`, thresholds are CLI flags, defaults in brackets)

* **corridor**: `long_front_intensity` = long (≥150 km) transhumance fronts
  per 10 km of unit width (`flg·10/√area` — **scale-free**; per-area density
  punished big units, per-unit counts rewarded them) ≥ AOI quantile
  `--corridor-q` [0.75] of units > 100 km²; transhumance share ≥ 50 %;
  **axis coherence** (share of fronts on the modal axis N–S/NE–SW/E–W/SE–NW,
  0.25 = random) ≥ [0.4]; people ≤ 2/km².
* **core**: people ≤ 0.02/km², cropland 2019 ≤ 0.05 %, reviewed clearing since
  2020 ≤ 0.5 km² **per 1,000 km²** (scale-invariant since 2026-09-05), no
  reported working.
* **wilderness**: people ≤ 0.5/km², cropland ≤ 0.5 %.
* **community**: the rest.
Relative thresholds because the fleet tripled on 2024-01-01 (`fire.md` F11);
absolute ones have physical meaning. Never tune by eye (invariant 12): change
a threshold, re-run `validate`, read the IoU table.

## Recursion: `assess()` and `UnitTable`

* **`assess(con, G, geoms, …)`** rasterises arbitrary polygons (WDPA PAs,
  hand-drawn KML zones, a proposal) into one label image and calls
  `attributes()` — so "what would the planner call Southern NP as drawn?" is
  one line, and `validate` prints it under **REFERENCES MEASURED WHOLE** with
  the full rationale.
* **`UnitTable(con, G, mesh, surf, T)`**: per-fine-unit sums of every raster +
  a sparse front×unit matrix, so `measure(members)` for any set of units is
  O(members) and `classify()` applies unchanged. `legibility_of()` = mean
  legibility of the *outer* edge only.

## `optimize` — grow the best area of a class, with bootstrap support

`optimize --want-class {core,wilderness,community,corridor} --draws N --n-areas K --opt-max-ha H [--rim-km R --exclude …]`

* **core/wilderness/community**: greedy region growing from the best seeds
  (fine units already of that class; most populated for community, largest
  otherwise). Add the neighbour that keeps the **whole union** in the wanted
  class and best raises the objective; stop when no neighbour helps. Objectives
  (`OBJECTIVES`) = area-like × (0.3+outer legibility) × √(0.3+compactness) ×
  class term; community has a governance size term
  `exp(-(ha/--cons-target-ha)²)` so it stops at a size one committee can run.
  Community grows from `labf`, excludes `--exclude` polygons (default park +
  Southern NP), optional `--rim-km` around the park.
* **Bootstrap**: `--draws` runs with every threshold ±`--perturb` (25 %),
  greedy gain jittered ±10 %, seed order shuffled. **support(u)** = share of
  draws unit *u* ends inside the best area. Proposal = support ≥ 0.5;
  0.25–0.5 = *contested* (listed, and excluded from the next area). Size
  p10/p50/p90 across draws is printed — that spread **is** the uncertainty.
* **corridor** is a *path*, not a blob: bootstraps `corridor_axis` itself
  (cost jitter ±15 %, smoothing 2–5 cells, people penalty 1–4, half-width
  8–16 km); support raster → `optimize_corridor_support.geojson` at 0.25/0.5/0.75.
* Outputs `OPTIMIZE_<class>.txt`, `optimize_<class>.{geojson,kml}`,
  `optimize_<class>_support.geojson`. Each proposal is then measured like a
  park (towns, boundary by named feature and compass side, rock, rationale).

### XSA results 2026-09-05 (superseded 2026-09-06 by the fine-mesh core, corridor network and beacons — re-read `OPTIMIZE_*.txt`)

* **core**: #1 2.34 M ha, 79 people, support 0.83, 52 % inside the drawn
  Pongo-Wau park and 81 % inside Wau-Wilderness — the machine re-finds the
  park and says it should be bigger to the SW. #2 1.61 M ha north (Boro), #3
  1.64 M ha CAR side (Mbomou/Ouara), support 0.74.
* **corridor**: support-≥0.5 band 21,468 km² (draws p10–p90 16–27 k km²),
  Radom → Raga → Deim Zubeir → north of Wau → east of Southern NP → Garamba.
  Long-front density inside 29.4/cell vs 13.9 outside. As a whole it
  classifies *community* (2.9 people/km², coherence 0.27) — a corridor
  through settled land, which is the honest answer.
* **community (rim 80 km, target 150 k ha)**: six areas 24–131 k ha; #1 SE rim
  Libu/Bo River 119,600 ha 5,663 people support 0.79; #2 130,800 ha east of
  Wau, support 0.98; #5 R. Nangundi / J. Dombili ridge 67,600 ha. Boundaries
  are 5–35 % unnamed and mix khors, Busseri/Jur, ridges and geological contacts.
* **validate** IoU vs references: Zemongo 0.93, Bili-Uere 0.85, Chinko 0.81,
  Wau-Wilderness 0.77, Southern-NP-Wilderness 0.76, Boro 0.73, Chelkou 0.69,
  Southern NP 0.65, **Pongo-Wau park 0.54 (its perimeter only 44 % legible — a
  finding, not a bug)**, grazing zones ≤ 0.47 (they are not what the fronts
  walk; see corridor).

## Legible features — what a boundary may be described by (2026-09-06)

The description a villager hears must use today's vocabulary: rivers, khors,
swamp edges, hills, village sites, wells. `describe_mask` therefore names a
boundary by its *linear* features (river / khor / ridge / swamp edge / road /
district line) and, for every stretch on none of those, lists the **point
landmarks within `BEACON_KM`=3 km** ("landmarks on the unnamed stretches:
Tidi (1930s village) (SW), Kuru pool (1930s sheet) (S) …"). Beacons come from
the histmap DB: `symbols` (category `settlement`, `water`, `peak`/`trig_point`
— the symbol layer is classified against the sheets' own legends and is more
reliable than OCR text or traced lines) plus `labels_dedup` place/water labels,
plus OSM villages. A geological contact still counts as *unnamed* (not
visible). Rule: **a beacon is a point, never a line** — do not chain villages
into a "boundary".

The same histmap layers are exposed for tools/LLMs via
`GET /api/histmap/sudan250k/around?lon=&lat=&radius_km=[&category=water,settlement&limit=]`
(`srv/histmap_lines.go`): every symbol now carries `dist_km` + `bearing`,
`symbols_by_category`, `symbols_truncated`, and three nearest-of-kind answers
regardless of radius (`nearest_water_symbol`, `nearest_village_symbol`,
`nearest_hill_symbol`, ≤25 km) — the "can a team sit here?" question.

## Metes-and-bounds (`scripts/plan_boundary.py`, 2026-09-06)

`describe_mask` gives a *summary* ("Nahr al Jur on the SE/N (64 km)"). A drafter or a
chief needs the *walk*: `python3 -W ignore scripts/plan_boundary.py [E1 T2 …] [--narrate]`
→ `data/plan_zones/solver/boundaries.json` + `BOUNDARIES.txt`, one record per solved
zone keyed by uid with the team codes that serve it. Method: simplify the raster outline
(`SIMPLIFY_DEG` ≈ 1.3 km, so a leg is a walking length, not a staircase), start at the
northernmost vertex, go clockwise, sample every 1 km, snap each sample to the nearest
**walkable** feature ≤ 3.5 km (the mesh `feats` from `state.pkl`; geology and beacons
excluded), run-length into legs, absorb legs < 3 km, merge same-name neighbours (a river
exists twice: HydroRIVERS + 1930s sheet copy). Each leg: `kind`, `name`, `km`, 8-point
`bearing`, `start`/`end` lon-lat, and for open-bush / geological-contact legs the
landmarks ≤ 3 km with offset and side. `legal` is the rendered paragraph ("thence SE along
Nahr al Jur for 45 km to the junction with K. Nuduk at 7.5150°N 28.1708°E"). `--narrate`
adds `in_words` (muse-glimmer, 36 workers, ≤3 sentences, names only from the legs;
`max_tokens` 4,000 — 1,500 returned "no JSON", the reasoning budget again). 264 zones ≈ 3 s
without narration. `named_pct` here (91 % for E1) is by leg and differs from
`unattributed_pct` (78 %) which is by raster edge cell — two units, two words; the summary
text quotes the raster one. `easyplan.py` copies `legal`/`in_words`/`legs` into each team
in facts.json.

## Corridor NETWORK, not one path (2026-09-06)

`optimize --want-class corridor` routes **one branch per origin–destination
bundle** (`corridor_bundles()`: k-means k=12 on (start, end) of the long
transhumance fronts, bundles ≥4 % kept) plus the Radom→Garamba through-route,
each bootstrapped (`--draws`) on its own; the proposal is the union of per-branch
support ≥0.5 bands. Each branch carries what operations need and
`OPTIMIZE_corridor.txt` / `optimize_corridor.geojson` / `AREAS.txt` print it:
`from_place → to_place` (nearest named place ≤40 km, towns preferred),
`bundle_fronts`, `onset` + `months` histogram, `straight_km`, band km² with
p10/p50/p90, and **the null**: `long_density_ratio` (long fronts inside ÷
outside) vs `all_fire_ratio` (all fronts inside ÷ outside) →
`excess_over_burning` (1.0 = the herds are simply where the burning is;
>1 = the band is a herd route beyond the burning), and `coherence_in`.
Several bundles begin *and* end inside the AOI (Wau→Tonj side, Raga→Deim
Zubeir→south, Wau→north) — the single Radom→Garamba axis was an assumption.
`build_map.py` labels each branch "from → to · N herds · onset".

## Why core #1 covered only half the drawn park (answered 2026-09-06)

Not the data: every fine unit inside the park is `core` (76 people in
15,800 km²). Growth ran on the **coarse mesh `lab1`**, whose two 5,500 km²
Busseri/Bo units straddled the park edge and carried 1,900 people *outside*
the park; the whole unit classified `wilderness`, so 3,700 km² (24 %) of
park-grade land inside them could never be added, and another 10 % was lost
where units crossed the boundary. Fix: every class now grows from the fine
mesh `labf` (invariant 15 — a constant calibrated at one scale). The
remaining gap, if any, is the *drawn* line crossing bush where the mesh
follows a river.

## `teams` mode (2026-09-06)

`teams` → `TEAMS.txt` + `teams.geojson`. Sizes fixed by the plan: **FP = 1
person, ECHO = 2, TANGO = 2**. ECHO in each community proposal's largest
village cluster **that has water** (2 teams if >20,000 people); TANGO per
corridor branch at the densest long-front passage within 25 km of a village
**that has water**, active from the branch's onset month; FP = verified town /
OSM city-town ≥`--fp-min-pop` (5,000) within 25 km of proposals, ≥40 km apart,
ranked by people served. **Water** = HydroRIVERS reach (order ≥4 or named),
1930s water symbol / well-pool-hafir label, or perennial JRC surface water
within `WATER_KM`=3 km (8 km for an FP); the source and distance are printed on
every line, and a site with none says `WATER UNVERIFIED`. Every placement has a
`why` built from the zone's numbers. `build_map.py` draws them (teal star /
square / triangle).

## Map (`scripts/easypip/build_map.py`, 2026-09-06)

* **Print-scale LOD**: with `--planner`, built-up km² and reviewed-clearing km²
  per 2 km cell (from the planner's `state.pkl` rasters) are drawn as amber /
  magenta washes (PowerNorm 0.5, 98th-pct max); settlement dots then show only
  towns ≥500 people. A footprint polygon is 0.7 px at this scale (`lod.md`).
  `load_density` falls back to `../conservancy_units/state.pkl` when
  `--planner` points at `solver/` (which has no state.pkl of its own) — until
  2026-09-06 it silently returned None there and the deploy sheet shipped with
  no clearing layer; it now warns, and the legend rows are gated on the wash
  being drawn.
* **Legend is an inset card** (`draw_inset_legend`, `LEGEND_COMPACT=True`):
  symbol + a few words per row, no title, no notes; provenance in a footer
  strip under the frame. Frame widened east to `LANDSCAPE_ASPECT` 1.414 so
  the sheet is a report-format landscape and the card sits over the blank
  Sudan corner.
* Planner labels are one short line each and routed by `place_labels`
  (collision-avoiding) instead of centred on the polygon.
* **One symbol grammar** (`SYM_PT`, `draw_sym`): every point mark is one visual
  size; the gold subject uses Lucide pictographs (`scripts/easypip/fonts/`,
  pickaxe / scan-search / eye / map-pin-x), teams and plan sites plain
  geometric shapes (hollow = year 2), areas are wide swatches in the legend —
  so it survives greyscale. Legend rows call the same `draw_sym`.
* **Spread, don't stack** (`draw_marks`): all point marks are queued and marks
  closer than one symbol box *on the page* (single linkage) are laid in a
  horizontal row on a translucent rounded paper plate centred on the group's
  median page position. Group by display distance, never by rounded
  coordinates.

## What Chinko taught (2026-09-05)

`PLAN_AOI=CAF_Chinko` works end to end (park boundary from
`data/keystones_with_boundaries.json`; AOI = park ∪ settlement bbox, buffered).
But: 76 settlements, 0 sheet symbols inside (no Sudan Survey coverage), rivers
mostly unnamed → 98 units, 75 with >25 % unnamed boundary, everything `core`
or `wilderness`, corridor optimizer finds nothing at 50 % support. **Chinko is
a null case, not a benchmark.** Its useful lesson was the classify order: an
empty unit the herds cross yearly is a corridor, not a core.

## Geology (added 2026-09-05)

`geology(aoi)` reads `data/geomaps/{car,sudan,tanzania}_{units,contacts}.geojson`.
Contacts ≥10 km are legible features at weight 2. Rock unit per cell +
summed commodity `affinity` weight → `geology` (top-3 lithologies with % and
commodities) and `geo_affinity_mean` on every unit; enters the community
objective as a **prior** (÷3). Its skill is measured only for CAR junctions
(invariant 12, `overlays.md`) — print it as a prior, never as a ranking.

## Other modes

* `conservancies [--rim-km --reach-km --cons-target-ha --cons-min-pop --describe --workers]`
  — earlier agglomerative variant (used land = village reach ∪ mining
  exposure, weakest-edge merging). `--describe` sends each candidate's 1930s
  gazetteer (`gazetteer()`: peaks, khors, notes *Deserted/Uninhabited*, forts,
  missions) + measurements to `fireworks/muse-glimmer-30b`
  (`HISTMAP_LLM_URL`, same as `scripts/histmaps/ocr_labels.py`) for name,
  meaning, designation, why, boundary story, mining veto, verify-on-ground;
  parallel workers, cached in `descriptions_<tag>.json`. **Advisory only** —
  the LLM never classifies; on 81 XSA units it called 58 "corridor", which is
  the reader over-weighting fire fronts. Superseded by `optimize` but kept.
* `rank --want {people,pressure,rim,balanced} --class --country --exclude --top`.

## Known gaps / next

1. `CEA` is fixed for XSA; `Grid` should re-centre per AOI.
2. Town names: OSM city/town/village within 8 km, else 1930s CAPITAL place
   labels (`hist_town`, +4 km penalty), else `nearest_place*`. Sheet capitals
   include tribe names; the filter regex is crude. "Koko" in `nearest_place`
   is Raga (data error, corrected in text only).
3. `optimize` for community should also report the *hand-drawn* conservancy
   overlap once the client draws any.
4. Stability of `classify` thresholds is bootstrapped only inside `optimize`;
   a whole-mesh stability map (which units flip class under ±25 %) is not yet
   written.
5. `build` ≈ 3 min on XSA; `optimize core --draws 16 --n-areas 3` ≈ 8 min;
   run in tmux.

## Run

```bash
python3 -W ignore scripts/plan_conservancy_units.py build
python3 -W ignore scripts/plan_conservancy_units.py validate
python3 -W ignore scripts/plan_conservancy_units.py optimize --want-class core --draws 16 --n-areas 3 --opt-max-ha 2500000
python3 -W ignore scripts/plan_conservancy_units.py optimize --want-class corridor --draws 24
python3 -W ignore scripts/plan_conservancy_units.py optimize --want-class community --draws 16 --n-areas 6 --opt-max-ha 250000 --rim-km 80
python3 -W ignore scripts/plan_conservancy_units.py teams
python3 -W ignore scripts/easypip/build_map.py --planner data/plan_zones/conservancy_units/optimize_community.geojson \
    --out reports/ZONING_PLANNER_MAP_2026-09.png --pdf --dpi 110      # draws optimize_{core,corridor,community}.geojson
PLAN_AOI=CAF_Chinko python3 -W ignore scripts/plan_conservancy_units.py build   # any keystone park id
```

## Solver (2026-09-06 → 07) — `scripts/plan_solver.py`

Modes `movement | threat | claim | solve | frontier | compare | support | narrate | all`; outputs in `data/plan_zones/solver/`.
One integer programme (HiGHS via `scipy.milp`) gives every fine unit one class. What it does that no Marxan/prioritizr
run does, and the rule each piece obeys:

* **movement** (`--k 16`, default capture 0.5): Brownian-bridge utilisation per origin–destination bundle of the 13,178
  long fronts, fit 2023–24, held out 2025/26. 15 bundles, per-bundle skill +0.37…+0.54. **Network-level the herds are
  simply where the fire is** (hold-out 90 % vs all-fire null 91 %) — value is per route, never "the network". Writes
  `movement_bundle_ud.npy` (per-bundle UD, float16) which the solver's per-bundle floor and flow constraint read.
* **threat**: logistic conversion 2015→today, AUC 0.86 on 20 km spatial blocks. `threat_p10.npy`.
* **imagery** (`plan_imagery.py`, 346 z11 chips, all read): calibration ρ 0.05–0.19 vs cropland/JRC/GHSL/fire — **noise
  at 40 km**. It enters the solver at `lam_imagery × measured skill` (= 0.12, printed in SOLVE.txt), i.e. effectively
  nothing, by invariant 12. Keep `spot` (z15) for decision-hinge checks only; four ECHO sites flagged `WATER UNVERIFIED`
  had no village and no water in imagery → `teams` should *require* water.
* **solve**: constraints are the plan's *decisions*, weights are fixed (`DEFAULT_W`) and never tuned by eye:
  - **per-bundle corridor floor** `--corridor-capture` (0.25): each route keeps that share of *its own* utilisation in
    the corridor class (one network floor let the solver satisfy it with the fat bundles and drop whole routes);
  - **flow connectivity** `--connect 1`: one unit of flow per bundle from its origin unit to its destination unit,
    allowed only through corridor units → every bundle's corridor is **one walkable route end to end**. Origin/destination
    = nearest corridor-feasible unit to the bundle's mean start/end inside the largest feasible component (else
    infeasible). Costs 8 min instead of 5 s; `frontier`/`support` run without it and the chosen point is re-solved with it;
  - `--fix-designated 1`: units ≥50 % inside a gazetted WDPA NP/faunal reserve/conservation area are fixed core where
    they meet the core rule (the ones that don't are counted, not hidden);
  - `--max-people-corridor`: cap on people whose land becomes corridor (a frontier axis);
  - `herd_vs_core = 0` **by user decision 2026-09-07**: herds crossing a candidate core are not a veto — zoning shapes
    future movement (Chinko was a through-route and is now largely avoided). The redirection is a **ledger** line.
* **LEDGER** (in `solve.json`/`SOLVE.txt`/`COMPARE.txt`): who pays — people whose land becomes core/wilderness/corridor,
  1930s-claimed cells inside core, km of boundary through open bush to walk and mark, per bundle the UD share in
  corridor/core/community, herd-months outside corridor and herd-months to redirect out of core.
* **frontier**: ε-constraint sweep (q × core cap × people cap), Pareto rows starred, infeasible rows say *why*
  ("at q no plan puts fewer than N people on corridor land", from a min-people re-solve). This replaces weight tuning.
* **compare**: the authors' KML (+ WDPA designations as status quo; undrawn = *unzoned*, scored as community) under the
  same objective and ledger, and the disagreement as connected blocks of one (authors → solver) class pair, largest
  first, each with the numbers that drive it and `core_eligible` share ("meets the core rule but LOST TO THE CORE CAP"
  vs "only 40 % of it meets the rule"). `scripts/plan_compare_map.py` draws both plans side by side, same colours,
  blocks numbered → `reports/ZONING_COMPARE_<date>.png` (a judging aid, not the report map).
* **support** (data resampling) and **narrate** (muse-glimmer panel) are unchanged; run them last.

First honest results (q 0.35, connected, herd_vs_core 0.7 — superseded, re-read SOLVE/COMPARE): authors' plan scores
420 k vs solver 463 k under the same objective; agreement 48 % of drawn ground; solver puts 67,690 people on corridor
land vs 426 (the authors' pâturage zones are 2,732 km² and hold 0.3 % of herd UD); the drawn Pongo-Wau park was made
*wilderness* only because of the herd penalty — 81/82 of its units meet the core rule.

`scripts/plan_zones_package.py` (QGIS GeoPackage + Excel): fixed (validation.json summary row, beacons as their own
point layer, XML-escaped label expressions; QML verified well-formed since QGIS is not on the VM). Still reads the
greedy `optimize_*` outputs — add the solver `zones.geojson` as a layer once the chosen frontier point is solved.

Next: pick the frontier row with the client (core cap is a political number), `solve --connect 1` there, `support`,
`teams` (require water), package + `build_map.py --planner` with solver zones, regenerate report text.

## Deploy (2026-09-06) — `scripts/plan_deploy.py`

Runs **after** `plan_solver.py solve/rank`; the solver decides classes, deploy decides where a small staff stands first.
`python3 -W ignore scripts/plan_deploy.py [--narrate] [--staff-y1 30 --staff-y2 80 --echo-y2 5 --tango-y2 5 --fp-y2 3]` →
`data/plan_zones/solver/DEPLOY.txt`, `deploy.json`, `deploy_teams.geojson` (points), `deploy_footprint.geojson` (served zones +
25 km disc per team, `year` attribute — the map should outline **only** these).

* **Gold enters here, not in the ILP** (user decision): ECHO urgency = 0.5·gold-target rank (top-5 % cells + candidates +
  watchlist + reported, from `cells()`) + 0.3·(people × mean P10) + 0.2·shield; **zero without a gold target**. TANGO urgency
  = 0.4·herd UD in band + 0.35·UD within 25 km of residents + 0.25·gold in band. Rank-percentiles, weights fixed.
* **Site** = GHSL settlement ≥150 people (residence implies water; mapped water still re-checked and printed). ECHO needs a
  trunk…tertiary road ≤5 km or OSRM car trips (motorbikes); **TANGO has no road test** (walks with the herds; helicopter
  in extremis). Shortlist of 3 scored by **OSRM reach** (`data/eval/xsa_mining/osrm_times.npz`: share of the zone's
  targets within 4 h, car share, median min); **muse-glimmer adjudicates among the shortlist only** (`ADJ_SYS`; needs
  `max_tokens` ≥ 2,500 — the model spends ~400 tokens in `reasoning_content` before the JSON, 400 returned "no JSON").
  Two same-kind sites within 50 km collapse to one. A corridor zone ≥1 M ha spanning ≥3 bundles gets a second TANGO.
* **Staging**: team = 5 (4 scouts + 1 leader), FP = 1; year 1 ≤30 staff (2 ECHO, 2 TANGO, 1 FP = 21), year 2 ≤80
  (5/5/3 = 53). FPs by **marginal** coverage of team sites within 120 km (else three FPs pile onto Wau).
* `--narrate`: `short` (one sentence) + `brief` (≤120 words) + `first_season` per team, built only from the served zones'
  stored descriptions. 32 workers default.
* Gotcha fixed on the way: `roads_heigit` XSA rows covered only the CAR extract until 2026-09-06 (Wau/Tambura "no road");
  now 1,484 segments to lon 31.4. `SOLVE_tune`/`ZONES_tune` (old formulation) deleted.
* **Deployment sheet** (2026-09-06): `build_map.py --planner data/plan_zones/solver/zones.geojson --out reports/DEPLOY_MAP_<date>.png --pdf`
  picks up `deploy_footprint.geojson`/`deploy_teams.geojson` beside it (`--deploy ''` to switch off). Served zones are
  filled by the solver's per-pixel intensity (`solve_lab.npy`/`solve_intensity.npy`, class colour, α = lo + hi·intensity),
  all other zoning as dash-dot outlines in class colour, authors' KML one grey dash-dot; the 25 km discs are **not**
  drawn. Draw order: fire hairlines (z 2.0) < clearing/built-up washes (2.08/2.12) < outlines < team fills (3.05) <
  towns < teams (5.6) < gold marks with a paper halo (5.8) — the mining candidates are the subject and stay on top.
  Legend rows are ≤4 words at the scale-bar size (PANEL_SCALE 0.80); provenance is one 7.2 pt line under the frame.
* **QGIS package** = `plan_zones_package.py` then `QT_QPA_PLATFORM=offscreen python3 scripts/plan_zones_qgis.py`
  (PyQGIS 3.34 is installed). Package adds `deploy_zones` / `deploy_teams` (`sym` = kind+year drives the hollow year-2
  style) / `deploy_reach`, writes `<stem>_deploy_fill.tif` (RGBA — the sheet's own fill pixels) and `solve_class`/
  `solve_intensity` rasters. The QGIS step copies the EASY layers (settlements, fire, gold, rivers, Southern NP) with
  their styles, builds the grouped project (Deployment / Gold / Zoning found / Drawn by the authors / On the ground /
  Reference mesh — every layer present, non-sheet ones unchecked), stores it **inside** the .gpkg
  (Project ▸ Open from ▸ GeoPackage → `deployment_map`) and as `.qgs`, and renders `<stem>_qgis_check.png` to prove it.
  Two bugs found by rendering, not by reading: point QML needs `<symbol type="marker">` (`"point"` drew nothing —
  `teams`/`beacons` had been blank since they were written), and every GeoTIFF carried `+proj=cea` without the grid's
  `+lon_0=27 +lat_ts=7.5` (3,000 km off). **Check a QGIS style by rendering it with QGIS**, not by parsing the XML.
