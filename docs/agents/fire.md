# Fire pipeline

_Split out of AGENTS.md. Read when working on this area._

## ⚠️ Fire data source: SQLite only

`fire_detections` (42.9M rows, 3 sensors) is the one and only fire source.
Read it via `scripts/fire_source.py` (`load_park_fires(park, min_date)`).

The old `data/raw-fire-viirs-*/{park}.json` was a **rolling ~6-month window**
masquerading as an archive (CAF_Chinko: 18k fires in JSON vs 425k in the DB), so
a full non-incremental rebuild silently discarded years of trajectories. It was
deleted 2026-08-05 along with its two nightly writers and the `--source json`
flag; don't reintroduce a second copy of the detections.

**Never tune the fire algorithm by eye** — use
`scripts/eval_fire_trajectories.py` (6-park golden set;
`--snapshot`/`--baseline`/`--candidate`). The builder's ablation flags
(`--no-hungarian --no-mass-penalty --no-overpass`) reproduce the old v6 output
bit-exactly; verify that before trusting any delta.
See `docs/FIRE_PIPELINE.md` § v7.

Per-overpass slicing (`--overpass`) is implemented but **off, permanently**.
All three VIIRS sensors share one sun-synchronous ~13:30 orbit plane, so
ingesting three of them tripled the density of each pass without adding passes
(1.71 slices/day). Re-tested 2026-08-06 on the frozen DB: every gate regresses
(`fires_per_grp` −10.6%, `mean_days` −22.4%, `dup_pairs` +16%). Only a
different orbit plane or a geostationary source could change this —
`docs/FIRE_PIPELINE.md` § "Per-overpass slicing".

**NRT→SP reconciliation is a measured no-op** — don't rebuild it. FIRMS' SP
reprocessing returns coordinates, FRP and confidence *byte-identical* to the
NRT rows we already have; only `acq_time` moves 1–2 min, which day-level
clustering cannot see but which *would* fork the `UNIQUE(lat, lon, acq_date,
acq_time, satellite)` key. Six NRT-provenance windows, `data/eval/nrt_sp/`,
`docs/FIRE_PIPELINE.md` § NRT→SP. What ships is a watchdog: `daily_fire_update`
step 2e on the 1st of each month runs `scripts/reconcile_nrt_sp.py --watchdog`
(read-only, ~40s) → `data/nrt_sp_audit.json`; exit 4 = FIRMS changed, recorded
as `nrt_sp_drift` in the pipeline heartbeat + a SYSTEM notification. Only then
use `--apply --yes` (matcher-based UPDATE, never a blind INSERT), and rerun
`build_fire_grid_agg.py --since` + the v5 rebuild for affected parks.
Beware three ways to measure this wrong: SNPP/N21 history is SP-sourced (a
tautology — the script checks provenance via id ordering), exact
`(date, acq_time)` bucketing discards ~85% of true pairs, and raw bbox add/drop
rates just reflect our own ingest-scope history.

All three VIIRS sensors are ingested (NOAA-20 + SNPP + NOAA-21, ~3x the
detections). Satellite codes `N`/`N20`/`N21` are part of the
`fire_detections` UNIQUE key — never default that field.

**All FIRMS downloads go through `scripts/firms_api.py`.** Two API facts that
both produce *silent* zero-row ingests if you hand-roll a URL:
* the area endpoint caps a request at **5 days** (a 10-day URL 400s);
* NRT-vs-SP is **not** a function of age. NOAA21 has no SP product at all;
  SNPP and NOAA20 cut over on different dates. Asking the wrong side of a real
  cutover returns HTTP 200 with a header-only CSV. `pick_source()` reads
  `/api/data_availability` and returns `None` when nothing covers the date —
  callers must skip, not retry. See `docs/PLAN_AOI_OVERLAY.md` §2.

Use `--parks a,b,c` (one process) rather than repeated `--park` calls.

`data/fire_groups_v5/` and `data/fire_trends_v5/` are **gitignored derived
output** (762 MB, 711k groups) — regenerate, don't commit.

### A park with zero groups is a real state, not a no-op

Rainforest/desert parks can rebuild to 0 groups, or to groups that all sit
>20 km outside the boundary (past the narrative cutoff). Both writers must
handle it explicitly or the old rows become immortal:

- `load_fire_groups_to_db.py` deletes `feature_geometries` rows **before**
  the empty-input early return.
- `precompute_narratives_v5.py` writes an **empty v5 cache row** for such
  parks. Do *not* delete the row instead: a cache miss drops
  `HandleAPIFireNarrative` into the deprecated Go slow path (17s,
  `feature_id: null`) — the exact Single-Writer-Rule failure.

---

## `protected_area_id` is a catchment, not a park (F10 — fixed 2026-08-13)

`park_assigner.ASSIGN_MAX_DIST_KM = 100`, so `WHERE protected_area_id = ?`
selects every detection whose *nearest* park boundary is X and is within
100 km. Until 2026-08-13 that was the whole predicate behind **every**
user-facing "fires in park X" count — a median **9.8× overstatement**, and for
seven rainforest parks a count made entirely of somebody else's savanna.
`CMR_Nki` is on the test-park list *as the pristine one* and `/stats` credited
it with 2,518 fires.

Two edits closed it, and they are separable on purpose:

1. **The queries name containment.** Eleven sites now append
   `srv/fire_containment.go`'s `fireInsideSQL` — `AND +in_protected_area = 1`
   — to their `protected_area_id = ?` predicate: `park_stats_handlers.go`
   (stats total, timeline, per-year, fire-log), `export.go` (`fire_count` CSV),
   `api.go` (`/api/parks/export`), `fire_trend.go`, `narrative_handlers.go`
   (Go-fallback total, peak month, hotspots, `analyzeFireTrend`'s year join),
   `fire_narrative_cache.go` (total + peak month), and
   `fire_realtime_handlers.go` (the 28-day alert window). **The `+` is
   load-bearing**: without it the planner takes `idx_fire_infraction`
   `(in_protected_area, acq_date)` and a 0.2 s park lookup becomes an 18 s scan
   of 8M rows (Kafue: 14.2 s). `tests/db_tests.sh` asserts the plan still names
   `idx_fire_pa_date` — same family as invariant 3.
2. **The flag was re-derived.** It was a stored ingest-time answer and 5.83%
   of it was wrong (433,632 rows from the bbox+0.5° `_find_park` that
   `ParkAssigner` replaced in `858eb69`). `scripts/rederive_fire_containment.py`
   recomputed point-in-polygon against `data/keystones_with_boundaries.json`
   for all 163 parks in 149 s: **469,692 cleared, 30 set**, flagged now
   7,585,655 = inside exactly. It corrects **both directions** — clearing only
   the false positives would move every count one way and read as a trend — and
   commits per park so the writer stays available (invariant 16).

```
                          before        after
tagged   protected_area_id = X       42,092,853   (unchanged — still a catchment)
flagged  + in_protected_area = 1      8,055,317    7,585,655
inside   point-in-polygon                          7,585,655   (identical)
CMR_Nki /stats "fires"                     2,518            0
CAF_Chinko                               962,444      132,570
```

**A derived flag must say what it was derived from.**
`data/fire_containment_state.json` records the boundary file's SHA-256; a db
test compares it against the file and fails when a boundary edit makes the flag
stale, which is the whole point — the old flag drifted for months precisely
because nothing compared it to anything (invariant 5: a stamp nobody checks is
a comment). Re-run after any boundary change:

```bash
python3 scripts/rederive_fire_containment.py --dry-run     # counts, writes nothing
python3 scripts/rederive_fire_containment.py               # ~150 s, all parks
python3 scripts/audit_fire_containment.py --park CAF_Chinko   # the independent check
```

`audit_fire_containment.py` is deliberately **not** the same code: it
re-measures containment instead of trusting the stamp, so a bug in the
re-derivation shows up as a disagreement rather than as agreement with itself.

**Still true, and not a bug:** `/fire-narrative`'s `total_fires` (302,900 for
Chinko) and `/stats`'s (132,570) are **different units** — the first sums
detections belonging to fire *groups* near or inside the park (the v5 chain,
`pct_inside` by point-in-polygon), the second counts detections inside the
boundary. Both now **name their basis**: the narrative carries
`total_fires_basis`, the popup label reads "Fire detections in groups", and the
stats panel says "Fire Activity (inside)". Invariant 7 asks two surfaces saying
one word to say one number; where they genuinely count two things, they must
say two words.

Two dead things were removed while auditing the call sites, both of which would
have re-introduced the buffer if revived: a 56-line `park_stats` CTE in
`HandleAPIParksExport` assigned to `query` and discarded with
`_ = query // for future optimization` (it joined `fire_detections` on a
`park_id` column that table does not have, so it had never run), and
`PrecomputeRecentFireNarratives`, an uncalled **second writer** to
`fire_narrative_cache` with the same phantom column (invariant 10).

---

## The 2024 step is the satellite fleet, not the fire (F11 — fixed 2026-08-13)

One VIIRS sensor flies before 2024-01 (`N`, Suomi-NPP); three fly after (`N`,
`N20`, `N21`). Every raw detection series therefore has a ~3× step on
2024-01-01 that is **instrument, not landscape** — CAF_Chinko goes 61,509
detections in 2023 to 203,223 in 2024 without an extra hectare burning. Same
failure as F8's Hansen→GFW switch, so it reuses F8's mechanism: the sparkline's
`d.brk` **cuts the line** and captions why, rather than drawing a rise.

The fleet is **measured, never typed** (invariant 2): "three sensors since
2024" describes an ingest history that grows nightly and would be wrong the day
a sensor is added or a differently-sourced CSV is imported.

```
db/migrations/056-fire-sensor-epochs.sql   fire_sensor_epochs(month, sensors,
                                           sensor_count, detections, computed_at)
scripts/build_sensor_epochs.py             the writer — full scan, ~90 s, cron 04:30 on the 1st
srv/fire_sensor_epochs.go                  hourly-cached reader, sensorsOn()
/api/parks/{id}/fire-trend                 each week carries `sensors`/`sensor_count`
                                           + `sensor_epochs_measured`
```

Monthly and **global** on purpose: per-park-per-week distinct-satellite counts
are a sampling artefact — a quiet week in a small park shows one sensor because
only one fire burned. `detections` travels with each month so a reader can tell
a real single-sensor month from a thin one.

Three consequences in the frontend, each a separate lie avoided:

* **An unmeasured fleet is not a constant one.** When `fire_sensor_epochs` is
  empty the API says `sensor_epochs_measured: false`, no breaks are drawn, and
  the caption says the fleet is unmeasured — an unbroken line silently asserts
  continuity (invariant 12).
* **Do not compare across fleets.** The sparkline's "prior years average"
  reference now drops prior years flown by a *different* fleet. Comparing a
  2024 week against one-satellite years made every park read as a fire
  emergency at the cutover — the same rule F9 applies to `area_method`.
* **A null reference is a gap, not a zero.** `v3 || 0` drew the reference line
  down to the axis wherever it was unmeasured, which says "no fire in prior
  years"; `gapLine` now breaks the path instead.

Only a **known, changed** fleet breaks the line: a week with no epoch row must
not manufacture a break out of ignorance (the same guard F8's `methodKey` uses).

## Fire pipeline health & consistency

Three artefacts must agree or the popup silently breaks ("Feature not found"
when clicking a fire in a narrative):

    data/fire_groups_v5/*.json   ->  builder output (source of truth)
    feature_geometries           ->  what the map/pin API resolves
    fire_narrative_cache         ->  what narrative links point at

```bash
python3 scripts/check_fire_consistency.py --verbose   # read-only, exit 1 on drift
python3 scripts/fix_fire_consistency.py --dry-run     # then without --dry-run
```

The nightly pipeline runs the check as step 7 and records the result in
`data/pipeline_status.json`, served by `GET /api/pipeline-status` (adds `stale`
after 48h = two missed runs) and shown as a colour-coded badge in the **admin
panel header** (click for per-step counters + errors). Log rotation:
`5mp.logrotate` → `/etc/logrotate.d/5mp`.

**Persistent hotspot mask**: `fire_persistent_cells` (323 cells, 32 parks) lists
0.0034deg cells detected in >=30 distinct months — lava lakes (COD_Virunga),
flares, kilns. Built by `scripts/build_persistent_hotspots.py` (monthly, step 2d
on the 1st). Masked detections cannot **seed** a cluster but are still absorbed
by a real front within `DAY_EPS_KM`. Ablate with `--no-hotspot-mask`.

A/B'd 2026-08-06 on a frozen DB: **keep it**. It cuts `stationary_fire_pct`
(detections locked up in groups that burn ≥60 days inside a <3 km box) from
3.01% to 0.27%. Beware: every *other* harness metric "regresses" — a lava lake
is the harness's idea of a perfect group, so removing it costs `mean_days`
−28%, `coverage_pct` −20%. **Never judge a detection-filtering change on
`coverage_pct`**; read `stationary_pct`/`stationary_fire_pct` instead.

**Group feature_ids are deduped**: `dedupe_feature_ids()` salts only actual
collisions (722/181,711) so persisted friendly names in `fire_group_names` stay
valid. Never change the primary hash without a name migration.

## ⚠️ Fire Narrative Cache — Single Writer Rule

**Only `scripts/precompute_narratives_v5.py` may write `fire_narrative_cache`.**
It reads `feature_geometries` and emits real v5 hash feature_ids
(`CAF_Chinko_2026_grp_dcb35641`). The legacy Go path
(`computeFireNarrativeForCache` → `getTrajectoryNarrativesFromJSON` in
`srv/fire_narrative_cache.go`) reads stale `data/fire_trajectories_v2/` files and
generates sequential `_grp_N` ids that don't exist in the features API →
"Feature not found" when pinning fires. It is DEPRECATED — never re-wire it
into refresh paths. (This bug shipped once via `/api/refresh-park`; fixed 2026-07-06.)

- Per-park refresh: `python3 scripts/precompute_narratives_v5.py --park CAF_Chinko` (~1s, fire-only)
- `/api/refresh-park` and the weekly cache worker shell out to this script.
- Detect stale v2-written rows: `computed_at` without a `T` (Go used `CURRENT_TIMESTAMP`,
  python uses ISO8601): `SELECT park_id FROM fire_narrative_cache WHERE computed_at NOT LIKE '%T%'`
- Verify a cache is v5: feature_ids in `narratives[].feature_id` must be hex hashes, not `_grp_1`.

**fire-realtime counts** (`srv/fire_realtime_handlers.go`, `handleFireRealtimeFromFeatures`):
`groups[]` payload is capped at 100, but `total_groups`/`active_groups_count` are true
pre-cap counts. `is_inside` = touches park (`dist_to_park_km≈0` or `pct_inside>0`);
groups up to 20km outside are included for context but not "inside". Peak-season
parks (Angola/DRC/Zambia, Jun–Aug) legitimately have 150–280 active groups — not a bug.

## Data Processing Scripts (v5)

See `docs/SCRIPTS.md` and `docs/FIRE_PIPELINE.md` for full details.

```bash
# Full rebuild pipeline:

# 1. Rebuild fire groups with v5 algorithm
python3 scripts/rebuild_fire_trajectories_v5.py

# 2. Load to database with context enrichment
python3 scripts/load_fire_groups_to_db.py --force

# 3. Precompute v5 narratives
python3 scripts/precompute_narratives_v5.py

# Daily incremental update (runs via cron at 3am UTC):
python3 scripts/daily_fire_update.py --days 7
```

---
