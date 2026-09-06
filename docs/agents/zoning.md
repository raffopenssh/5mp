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
* **Legend is an inset card** (`draw_inset_legend`, `LEGEND_COMPACT=True`):
  symbol + a few words per row, no title, no notes; provenance in a footer
  strip under the frame. Frame widened east to `LANDSCAPE_ASPECT` 1.414 so
  the sheet is a report-format landscape and the card sits over the blank
  Sudan corner.
* Planner labels are one short line each and routed by `place_labels`
  (collision-avoiding) instead of centred on the polygon.

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

## Solver + imagery (2026-09-06, in progress — continue here)

`scripts/plan_solver.py` (`movement | threat | claim | solve | support | narrate | all`) is the step past the
greedy grower: **one integer programme (HiGHS via scipy.milp, 2,150 fine units, 5,572 edges, solves in 2 s)**
assigns every fine unit one class with a legibility-weighted boundary cost, class feasibility as linear
constraints, a core cap and a herd-utilisation floor for corridor. Inputs it computes and validates:
* `movement` — Brownian-bridge utilisation per origin–destination bundle of the 13,178 long fronts, **fitted on
  seasons 2023–24, held out 2025/26**: per bundle the smallest isopleth that captures ≥50 % of next season's
  fronts, with an equal-area all-fire null (bundle skill +0.45 … +0.85). Writes `movement.json`, `MOVEMENT.txt`,
  `movement_{ud,band,bundle_masks}.npy`.
* `threat` — logistic conversion model 2015→today (built-up +≥0.5 ha, new cluster, ≥5 ha clearing since 2020),
  predictors as of 2015, **AUC 0.86 on spatially held-out 20 km blocks**; `threat_p10.npy` = P(convert in 10 y).
* `claim` — 1930s village symbols/labels vs GHSL today per cell: continuous / abandoned / new / empty
  (`claim_state.npy`); sheet coverage is partial (CAR/DRC unrecorded).
* `solve` → `solve.json`, `SOLVE.txt`, `zones.geojson`/`ZONES.txt` (each zone measured by `attributes()` like a
  park). **First run's weights are not tuned**: corridor took 283,000 km² (herd UD term too strong vs boundary
  cost); rescaled weights (per-km² terms, `lam_boundary=2`) were written but the re-run was interrupted. Next:
  re-run `movement --k 16` (default capture now 0.5), `solve`, inspect class km², iterate weights, then
  `support` (data-resampling bootstrap) and `narrate` (parallel muse-glimmer panel, 2 readers/zone, `/around`).
* Fixed `optimize`: `--first-near lon,lat` pins area #1 on the drawn park (free-roaming objective left it 25 %
  covered); `grow()` is incremental (0.4 s vs hours on the fine mesh); `bootstrap()` runs draws in parallel.

`scripts/plan_imagery.py` (`units | sites | spot | calibrate | raster`) reads the owner's satellite basemap
(`tile_sources`, proxied owner-only, `AOI_OWNER_PWD`) with muse-glimmer. **Scope decided: LANDSCAPE, not
settlements** — GHSL/OSM know the people. `units` = one ~40 km chip (z11, 2×2 tiles) per chip-square over the
AOI (~300 chips, ~1,000 tokens each): toich/wetland, gallery forest, plateau/hills, drainage, burn, cultivated
mosaic → `imagery_*.npy` for the solver after `calibrate` prints Spearman vs cropland/JRC/GHSL/fire. `spot` =
sporadic z15 checks **only where a decision hinges on the ground** (team sites; clearing or cluster inside a
proposed core; chip/raster conflicts), capped 60/run → `IMAGERY_SPOT.txt`. First 6 spot checks: four ECHO sites
flagged `WATER UNVERIFIED` show **no village and no water** in imagery — move them (`teams_mode` should require
water, not merely flag it). Keep workers ≤4; the model answers in `reasoning_content`, parse both fields.

`scripts/plan_zones_package.py` writes the QGIS GeoPackage (embedded `layer_styles`, field aliases = column
definitions) + Excel workbook of all zones/teams/boundaries; unfinished: `validation.json` rows are keyed by
`name` differently — fix `V = {v["name"]…}` (KeyError) before first run.

`build_map.py --planner`: authors' drawn boundaries now one grey dashed style + one legend row; unserved-belt
label counts team sites as sites; legend is a compact inset card (no title), landscape frame.
