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
