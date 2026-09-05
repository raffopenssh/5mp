# Zoning planner — legible units, four classes, bootstrapped proposals

`scripts/plan_conservancy_units.py` (one file, ~1,300 lines; modes `build`,
`validate`, `rank`, `conservancies`, `optimize`, `show`, `export`). Outputs in
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
point at** (rivers, ridge chains, 1930s district and (rarely) sub-tribal boundaries, named khors,
roads, borders, geological contacts), (2) measuring each piece with the *same*
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
| legible features | `rivers()` (HydroRIVERS ≥ order 4 or named; unnamed reaches take the 1930s water-label name when ≥2 vertices agree), `ridges()` (peak/trig symbols + `J./HILLS` labels, single-linkage ≤15 km, ≥3 pts → principal-axis polyline), `hist_boundaries()` (`lines_stitched kind='boundary'`, weight 5 = customary land, Land Act 2009 s.66-67), `hist_watercourses()` (≥15 km named khors), `roads()` (trunk…tertiary), `geology()` contacts (weight 2 — steers through open bush, never overrules a river), borders, AOI edge | |
| legibility surface | 0..1, `w/8·exp(-d/1.5 cells)`, strongest feature wins; `G.fid/G.fdist` for boundary descriptions | `legibility()` |
| seeds + watershed | village clusters (10 km single linkage, pop ≥150) + 20 km empty-land lattice → `skimage.watershed` on the surface | `seeds()`, `segment()` |
| merge 1 | absorb < `--min-ha` (2,000) across least legible edge → **`lab1`** (fine units, 644 on XSA) | `merge_units()` |
| fine mesh | every village ≥20 people + 10 km lattice → **`labf`** (2,200 units) — the mesh conservancies grow from | build block |
| evidence | **`cells()`**: every measurement as a raster on the grid, computed once and cached on `G`. Settlements/pop/new15/camps/built, OSM places, fire 2024-25 (+Nov–Feb share), fronts as cell-index sets (`G.fronts`), clearing (reviewed), mining anchors/candidates/top05/watchlist (XSA only; elsewhere `mining_measured=False`), GLAD cropland 2003/2019 fraction, geology unit id + commodity-affinity weight | |
| attributes | **`attributes()`** = one `bincount` per raster over any label image + front×unit incidence + towns + boundary description (`describe_mask`) + `classify()`. `freeze=T` reuses the mesh's corridor quantile so a merged unit is judged by the same bar | |
| classify | order matters: **corridor → core → wilderness → community**. Every test appended to `d["rationale"]` as `"name value op threshold: yes/no"` | `classify()` |
| merge 2 | like-with-like only, across weak edges (< `--weak` 0.35), to `--target-ha` 300k, never above `--max-ha` 600k → `lab` (510 units) | |
| corridor axis | least-cost path over smoothed long-front density, penalised by people; XSA anchors Radom→Garamba, generic AOI anchors = densest rim cells on opposite sides; `closed=` re-routes around the park | `corridor_axis()` |
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

### XSA results 2026-09-05 (what the map shows)

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
python3 -W ignore scripts/easypip/build_map.py --planner data/plan_zones/conservancy_units/optimize_community.geojson \
    --out reports/ZONING_PLANNER_MAP_2026-09.png --pdf --dpi 110      # draws optimize_{core,corridor,community}.geojson
PLAN_AOI=CAF_Chinko python3 -W ignore scripts/plan_conservancy_units.py build   # any keystone park id
```
