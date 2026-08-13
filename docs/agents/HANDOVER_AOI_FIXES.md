# Handover — AOI structural fixes (docs/AOI_STRUCTURAL_FIXES.md)

Started 2026-08-13. **The code is written and builds; NOTHING HAS BEEN
DEPLOYED and migration 055 HAS NOT RUN.** `db.sqlite3` is untouched, the live
service still runs the previous build, and no cron has executed any of this.
That is the safe state to pick up from, not a half-finished one — but it is
also why the app currently shows the *old* numbers.

## What is done (in the repo, unmerged into the running system)

| Fix | Where | State |
|---|---|---|
| F1 mask-area vs built surface | `scripts/ghsl_tiles.py:polygons_in` now yields `{extent_m2, area_m2, population}`; zonal sum of the raster's own values | written, spot-checked (0.4°×0.4° window: extent 173.5 km², surface 3.05 km², ratio 0.018 — matches the doc's 0.027) |
| F2 population from GHS_POP | same file: `POP_PRODUCT`/`POP_BASE_URL`, same tile grid, zonal sum; **absent** rather than constant-density when POP is unreadable | written, one tile fetched OK (13 MB) |
| F3 cluster diameter cap | `rebuild_events_enhanced._split_oversized`, `MAX_CLUSTER_DIAMETER_KM = 15` | written, **never run on real data** |
| F4 `LIMIT 100` on places | `_load_park_places` → `_PointIndex` (exact grid nearest) | written |
| F5 nearest river | `_load_park_rivers` → `_RoadIndex` of segments; `_get_nearest_river` does a real nearest-segment query and returns None beyond `RIVER_CONTEXT_MAX_KM = 10` | written |
| F6 fire context for AOIs | `_settlement_fire_context()` in the one clusterer + `fire_context_at` column | written |
| F7 response rate for AOIs | `srv/fire_containment_scope.go`, `containment_meaningful` in the JSON, popup rows suppressed | written |
| F8 Hansen vs GFW units | `area_method` column + `_area_method_for()`; sparkline breaks the line (`d.brk`) | written |
| F9 needs-review flag | `daily_park_refresh.flag_anomalous_years()` (≥50× the 5-yr median **within one method**), `needs_review` column, ring marker in the chart | written |
| F12 `settlement_type` | now written as `NULL` — `temporary` was unreachable below the ingest floor | written |
| provenance read path | `srv/settlement_provenance.go`: `settlementPopulationSQL` / `settlementSurfaceSQL` / `settlementExtentSQL`, applied across `api.go`, `narrative_handlers.go`, `fire_narrative_cache.go`, `settlement_classifier.go` | written |

Migration `055-settlement-surface-and-provenance.sql` adds
`extent_m2`, `area_source`, `population_source`, `epoch`, `fire_context_at`
to `park_settlements` and `area_method`, `needs_review` to
`deforestation_events`, and **labels** existing rows rather than blanking them:
14,504 settlements become `area_source='ghsl_mask_extent'`,
`population_source='legacy_density_200_per_ha'`, which the read path then
declines to serve as a population. That label is also the backfill's work
queue.

## What is NOT done — pick up here

1. **Run the migration.** `cp db.sqlite3 db.sqlite3.bak` first (1.8 GB, 39 GB
   free). It is four `ALTER`s plus three scoped `UPDATE`s over 14.5k + 24k
   rows; expect seconds, but it takes the write lock, so do it when the AOI
   runner (12:00) and the fire job (03:00) are not running.
2. **`make build && sudo systemctl restart 5mp`**, then check the version
   endpoint against `git rev-parse --short HEAD`.
3. **`scripts/backfill_settlement_surface.py` DOES NOT EXIST YET.** Migration
   055 and `settlement_provenance.go` both name it. It must re-derive
   `area_m2`/`extent_m2`/`population_est` for one park per run from the GHSL
   tiles (the polygons in `feature_geometries` need re-ingesting through the
   new `ingest_tile`, then `rebuild_settlements_for_park`), rate-limited so the
   live app keeps its write slot — the `on_batch` yield discipline is already
   in the rebuilder. 159 parks + 1 AOI; the AOI is 74,904 polygons and 4 tiles.
   **Until it runs, every park serves an absent population and an extent-only
   area.** That is honest but visibly emptier than before — decide whether to
   ship the read-path change and the backfill together.
4. **F10 (`protected_area_id` is a 100 km buffer) is untouched.** Finding from
   this session: `in_protected_area` **already exists and already means
   `dist_km == 0`, i.e. inside the boundary** — 8,055,317 rows set, 34,037,536
   with an id but outside, 5,532,890 unassigned. So the column F10 asks for is
   *there*; the work is auditing which user-facing "fires in park X" counts use
   `protected_area_id` alone and switching them to `AND in_protected_area = 1`.
   Nothing was changed, so nothing has regressed.
5. **F11 (sensor count changes at 2024-01-01) is untouched.** Measured this
   session: `N` 2,381,805 · `N20` 4,097,180 · `N21` 2,440,808 for 2026 — three
   sensors now, one before 2024. Needs either a per-sensor normalisation or a
   drawn discontinuity; the sparkline now *has* a break mechanism (`d.brk`,
   added for F8) that this can reuse.
6. **Nothing has been re-run over real data.** No park, no AOI, has been
   rebuilt through the new clusterer. The first run should be **one small
   park** (`CMR_Nki`, 0 settlements, or `CAF_Chinko`, 27 clusters / 35
   polygons) with a before/after diff, *not* `XSA_Study_Area` (74,904
   polygons). F3's split and F5's threshold are the two that will change
   published numbers most, and neither has a measurement beside it yet.
7. **No tests were written or run.** `./tests/run_all.sh` has not been executed
   against these changes.

## Traps found while doing this

* **`polygons_in` changed its yield shape** from `(poly, area_m2)` to
  `(poly, dict)`. `ingest_tile` is the only caller in-tree — check again before
  assuming that holds.
* **The two areas differ by ~24×, and the classifier keyed on the wrong one.**
  `total_area > 50000 → agricultural` was reading mask extent; it now reads
  surface, so *classification changes for every park* once the backfill runs.
  Expect the class histogram to move and do not read that as a bug.
* **`_get_nearest_river` returning None is the point**, not a failure: the
  sentence is omitted. 9,366 rows currently assert a river.
* **Do not compare across `area_method`.** F9's flagger already scopes its
  median to one method; any new year-over-year comparison must too, or every
  park flags at the 2024 cutover.
