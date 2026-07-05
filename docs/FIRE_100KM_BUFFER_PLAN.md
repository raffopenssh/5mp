# Fire Pipeline: 100km Buffer + Overlap Dedup + 20km Relevance Gating

**Status:** IN PROGRESS — see §7 "Handover" at the bottom for exact state
**Date:** 2026-07-05
**Context:** Fire transect (trajectory) detection was recently improved (v6 tracker,
commit `1162013e`, rebuilt in `0c890856`). Stats-panel now shows all fires in the
current map view via `/api/features-in-bbox` (commit `e94915f3`). This plan extends
the monitored area and fixes overlap artifacts + notification noise.

---

## 1. Goals

1. **100km ingest buffer** — build fire groups for a 100km buffer around each park
   (currently ~50km bbox buffer), so approaching fires are tracked earlier and the
   map view shows complete trajectories in the wider landscape.
2. **Kill overlap artifacts** — where park buffers overlap (see screenshot of the
   COD Lomami/Lualaba region: rectangular red "curtains" of duplicated/clipped
   trajectories), the same fire is currently ingested into multiple parks' raw
   JSONs and grouped independently per park → duplicate, differently-fragmented
   trajectories, with hard rectangular bbox edges.
3. **20km relevance gating** — notifications AND per-park stats must only count
   groups that are inside the park or within 20km of the park boundary. The map
   can still show everything in view (features-in-bbox already does this).
4. **Simplify download path** — direct FIRMS access now works from this VM
   (verified 2026-07-05: full Africa 1-day CSV in 0.65s, HTTP 200, no proxy).

## 2. Verified feasibility (measured 2026-07-05)

| Question | Answer |
|---|---|
| Fire volume at 100km vs 50km (14-day, sum over park bboxes) | 158,823 → 316,536 (**+99%**) |
| Daily cron runtime today | ~10 min (03:00:02→03:09:36) → expect **~20 min** at 2×. Fine for 3am cron. |
| Direct FIRMS download (no proxy) | **Works.** `curl .../VIIRS_NOAA20_NRT/-20,-35,55,40/1` → 200 in 0.65s. Webshare proxy still available as fallback (`scripts/webshare_proxy.py`). |
| shapely available for distance/assignment | Yes, shapely 2.1.2 (has `STRtree`, `Polygon.distance`, `prepare`) |
| Disk | raw JSON dir `data/raw-fire-viirs-20200101-20260222/` roughly doubles; check free space before backfill. |

Note: the user's "NASA power" remark = "can we fetch from NASA directly now" —
yes, confirmed. (NASA POWER the API is a climate API, not needed here.)

## 3. Current architecture (what must change)

### Data flow (daily cron `scripts/daily_fire_update.py`, 3am UTC)
1. Download Africa-wide NRT CSV from FIRMS (tries Webshare → free proxies → direct).
2. `insert_fires()` → `fire_detections` table. Park assignment via `_find_park()`:
   **first park whose bbox+0.5° contains the point** — first-match, bbox-based.
   ⟵ ROOT CAUSE of overlap artifacts + arbitrary assignment.
3. `update_raw_json_files()` → appends to `data/raw-fire-viirs-20200101-20260222/{park}.json`
   using the same `_find_park`. A fire in two parks' buffers goes to only ONE
   park's raw JSON (first match by dict order) — but historical backfill
   (`extract_raw_fire_json_from_backup.py`, BUFFER_DEG=0.45) put it in **every**
   overlapping park → duplicated groups where buffers overlap.
4. `rebuild_fire_trajectories_v5.py` (now v6 logic) per affected park → groups
   with `pct_inside` (point-in-polygon vs park geometry) → `data/fire_groups_v5/`.
5. `load_fire_groups_to_db.py` → `feature_geometries` (feature_type='fire_trajectory',
   properties_json incl. `position`, `pct_inside`) + `park_group_infractions` /
   `park_fire_weekly` stats.
6. `precompute_narratives_v5.py` → `fire_narrative_cache`.
7. `update_fire_group_alerts()` (POST /api/update-fire-alerts) → `fire_group_alerts`.
8. `assign_friendly_names_to_new_groups()` → `fire_group_names` (NATO names, ALL
   groups get names today, incl. far-outside ones).
9. `create_fire_notifications()` → `notifications` for every group active in last
   3 days, regardless of distance to park. ⟵ noise source (838 notifs on 2026-07-05).

### Go server (read side)
- `srv/fire_realtime_handlers.go`: `handleFireRealtimeFromFeatures` (groups list per
  park from feature_geometries), `HandleAPIFireAlerts`, `updateFireGroupAlertsFromFeatures`,
  `analyzeFireStatus` (uses `position`/`pct_inside`).
- `srv/features_bbox.go`: `/api/features-in-bbox` — the "all fires in view" layer.
  Should stay UNGATED (that's the point of the view layer), but needs dedup to not
  return the same fire group twice via two parks.
- `srv/api.go` ~line 1131: global stats from `SUM(stat_value)` over feature_geometries
  — will double-count while duplicates exist; fixed by dedup, optionally gate later.

## 4. Design

### 4.1 Canonical single-park assignment (fixes overlaps)
Assign every fire detection to exactly ONE park: the park whose **boundary polygon
is nearest**, if within 100km; ties broken by park_id. Implementation:

- New helper module `scripts/park_assigner.py`:
  - Loads `data/keystones_with_boundaries.json`, builds `shapely` prepared geoms +
    `STRtree` of park polygons, plus a coarse per-park bbox+1.0° prefilter.
  - `assign(lon, lat) -> (park_id | None, dist_km)` where dist_km=0 if inside.
    Use metric approx: degrees→km via cos(lat) scaling is fine at these latitudes,
    or `shapely` distance in degrees × 111 with lat correction (document choice).
  - Batch API `assign_many(points)` using STRtree bulk query for speed
    (~300k points/14d must be fast; STRtree handles this in seconds).
- Use it in:
  - `daily_fire_update.py` `_find_park` (replace bbox first-match),
  - `extract_raw_fire_json_from_backup.py` (replace BUFFER_DEG bbox loop; one park
    per fire, buffer 100km),
  - `extract_buffer_fires_from_db.py` if still used.
- Consequence: each fire appears in exactly one park's raw JSON → one group → no
  duplicate trajectories, no rectangular bbox seams. Groups near a shared boundary
  belong to the nearest park; cross-border display still works because the map
  layer is park-agnostic (bbox query).

### 4.2 100km buffer
- Constant `ASSIGN_MAX_DIST_KM = 100` in `park_assigner.py` (single source of truth;
  export for other scripts).
- Backfill: re-extract raw JSONs from `fire_detections` (or DB backup) for ALL
  parks with the new assigner (100km, deduped), then full v6 rebuild for the recent
  season (or since 2020 if runtime allows — measure one park first), then
  `load_fire_groups_to_db.py --force`, then narratives.
- `data/fire_sync_status` / `fire_data_manager.py` buffer_km values update to 100.

### 4.3 `dist_to_park_km` on every group (enables 20km gating)
- In `rebuild_fire_trajectories_v5.py::track_to_group`: compute
  `dist_to_park_km` = 0 if any fire inside park else min distance of trajectory
  points to park polygon (shapely). Add to group JSON.
- `load_fire_groups_to_db.py`: pass through into `properties_json`; ALSO add a
  real column for SQL gating: migration `db/migrations/036-feature-dist-to-park.sql`
  (`ALTER TABLE feature_geometries ADD COLUMN dist_to_park_km REAL`) + backfill
  from properties during load. Migrations run via `db.RunMigrations` on server start.

### 4.4 20km gating (notifications + per-park stats)
Define **relevant to park** := `pct_inside > 0 OR dist_to_park_km <= 20`, where
`dist_to_park_km` = **min over all trajectory points** (not centroid, not start).

**IMPORTANT: gating never clips geometry.** A transect that starts 80km out and
reaches the park is relevant (dist=0) and its FULL trajectory is stored and
displayed from its origin. Gating only decides whether the group counts toward
park stats and generates notifications.
- `daily_fire_update.py`:
  - `assign_friendly_names_to_new_groups()` — only name relevant groups (names are
    park-scoped resources; don't burn Alpha..Zulu on fires 80km out). Keep existing
    names for continuity.
  - `create_fire_notifications()` — only relevant groups. Expect big drop from 838/day.
- `load_fire_groups_to_db.py::update_park_stats` + yearly/weekly aggregation —
  only relevant groups counted in `park_group_infractions` / `park_fire_weekly`.
- `precompute_narratives_v5.py` — park narrative counts/summary from relevant groups
  only (verify where it filters; likely counts all groups in park JSON).
- Go: `handleFireRealtimeFromFeatures`, `updateFireGroupAlertsFromFeatures`,
  `HandleAPIFireAlerts` — add `AND (dist_to_park_km <= 20 OR dist_to_park_km IS NULL)`
  (NULL tolerated until backfill done). The park popup "fire" section thus only
  shows relevant groups; wider-landscape fires remain visible via map layer.
- `srv/api.go` global stats + `/api/features-in-bbox`: leave ungated (view-scoped),
  duplicates already removed by 4.1.

### 4.5 Download path simplification
In `daily_fire_update.py::download_nrt_fires`: try **direct first** (it works now),
then Webshare, then give up + notification. DELETE the free-proxy scraping code
(`PROXY_SOURCES`, `fetch_proxies`, `test_proxy`, `get_working_proxy`) — slow,
flaky, and a supply-chain risk. Keep the failure notification.

## 5. Rollout plan (ordered, resumable)

1. **Prep:** `cp db.sqlite3 db.sqlite3.bak` (1.8GB — check disk first, ~15GB free needed incl. raw JSON growth).
2. `scripts/park_assigner.py` + unit-ish test (known points: inside Chinko, 60km out, in Virunga/Queen-Elizabeth overlap zone → single deterministic park).
3. Migration: `dist_to_park_km` column on feature_geometries.
4. Update rebuild script (dist_to_park_km) + loader (column write, gated stats).
5. Update `daily_fire_update.py` (assigner, direct-first download, gated names/notifs, drop free proxies).
6. **Backfill raw JSONs** from `fire_detections` with new assigner (100km buffer).
   Script: extend `extract_raw_fire_json_from_backup.py` to read live DB. Verify a
   park's JSON fire counts before/after.
7. Full rebuild recent season (`--incremental --days` back to 2025-12-26 like
   `0c890856`, or full since 2020 if <2h): rebuild → load --force → narratives.
8. Update Go handlers gating; `make build && sudo systemctl restart 5mp`.
9. **Validation:**
   - Screenshot region (Lomami/Upemba/Kundelungu area): no rectangular seams, no
     doubled trajectories. Compare `/api/features-in-bbox` counts before/after.
   - `sqlite3`: no feature_id duplicates across parks for same fire cluster
     (spot-check via centroid proximity between parks).
   - Notification dry-run: count relevant vs total groups; expect large reduction.
   - `curl /api/parks/CAF_Chinko/fire-realtime` — groups all ≤20km or inside.
   - Cron timing next morning: `tail logs/daily_fire.log`, confirm <45 min.
10. Commit in slices (assigner, migration+loader, cron script, Go gating, rebuild data).

## 6. Open questions / decisions for follow-up

- **Full 2020+ rebuild vs recent-season only?** Recent-season (like `0c890856`)
  preserves old v5 groups; historical stats then stay on old 50km/duplicated basis.
  Recommend: recent season now, full historical rebuild as separate batch job later.
- **20km definition:** distance from park *boundary* (inside counts too) — using
  `pct_inside>0 OR dist<=20` as above. Confirm the UI copy ("within 20km").
- **fire_detections.protected_area_id** rewrite for history? The daily path will use
  the new assigner going forward; backfilling 6.1M rows is a separate optional pass
  (`UPDATE` in batches; needed only if any UI reads per-park counts from
  fire_detections — `srv/api.go:5085` does! Check and either backfill or gate).
- **Naming continuity:** groups already named but >20km out — keep names, just
  don't notify. New far groups get names only once relevant.

---

## 7. HANDOVER (2026-07-05, conversation 1)

### DONE (committed as WIP)

1. **`scripts/park_assigner.py`** — NEW. Canonical single-park assignment
   (nearest park boundary within 100km, shapely STRtree, deterministic
   tie-break by park_id). Smoke-tested: Chinko inside/60km-out, Sahara → None,
   Virunga/QE overlap → single park. Perf: 10k pts in 0.9s (~27s per 300k).
   **NOT yet wired into any pipeline script.**

2. **`db/migrations/036-feature-dist-to-park.sql`** — NEW. Adds
   `feature_geometries.dist_to_park_km REAL` (NULL = unknown = treated
   relevant) + index `idx_fg_dist_park`.
   **APPLIED MANUALLY to live db.sqlite3** (inserted into `migrations` table as
   row 36) because RunMigrations only fires at server start. Backup exists:
   `db.sqlite3.bak` (pre-migration, 2026-07-05).

3. **Backfill of dist_to_park_km on live DB — DONE for all 164,865
   fire_trajectory rows.** Two passes:
   - centroid-based via ParkAssigner (rough),
   - then re-done properly as **min over trajectory points** for all rows with
     dist>0 (user requirement: transects reaching the park are relevant AND
     keep their full geometry from origin).
   Final: 50,384 rows dist=0; 90,459 ≤20km; 164,865 total.

4. **`scripts/rebuild_fire_trajectories_v5.py`** — groups now emit
   `dist_to_park_km` (min over trajectory points via shapely, 0 if pct_inside>0).
   Verified on ZMB_Kafue incremental rebuild (594 groups with field, sensible
   values 2.4–72km). NOTE: CAF_Chinko + ZMB_Kafue
   `data/fire_groups_v5/*.json` were regenerated during testing (uncommitted
   data churn is normal here — daily cron rewrites these too).

5. **`scripts/load_fire_groups_to_db.py`** —
   - `RELEVANCE_KM = 20` constant (top of file, with doc comment).
   - writes `dist_to_park_km` column + property on insert.
   - per-park stats (park_group_infractions / park_fire_weekly) now count only
     relevant groups (pct_inside>0 OR dist≤20). Geometry never clipped.

6. **`scripts/precompute_narratives_v5.py`** — main query gated to ≤20km.

7. **Go read-path gating (`AND (dist_to_park_km IS NULL OR dist_to_park_km <= 20)`):**
   - `srv/fire_realtime_handlers.go:945` updateFireGroupAlertsFromFeatures
   - `srv/fire_realtime_handlers.go` handleFireRealtimeFromFeatures (park popup)
   - `srv/fire_narrative_cache.go:364` monthly trend query
   `make build` passes. **Server NOT yet rebuilt/restarted with these changes.**

### NOT DONE (ordered TODO for follow-up)

1. **`scripts/daily_fire_update.py`** — the big one:
   - replace `_find_park` bbox first-match with `ParkAssigner` (100km, single
     park per fire) in both `insert_fires` and `update_raw_json_files`;
     instantiate ParkAssigner once in `__init__` and use `assign_many` for speed.
   - `create_fire_notifications()`: only notify groups with
     `dist_to_park_km <= 20 OR pct_inside > 0` (read from properties_json or
     the new column). Expect drop from ~838/day.
   - `assign_friendly_names_to_new_groups()`: only name relevant groups
     (keep existing names).
   - download path: try DIRECT first (verified working: full-Africa 1-day CSV
     in 0.65s), then Webshare; DELETE free-proxy code (PROXY_SOURCES,
     fetch_proxies, test_proxy, get_working_proxy).
   - NOTE: there is dead/buggy code in create_fire_notifications — it re-reads
     `props_json`/`end_date` from loop-leaked variables after already having
     them in `group` dict (lines ~850). Clean up while there.

2. **Raw JSON backfill with new assigner (100km, deduped)** — extend
   `extract_raw_fire_json_from_backup.py` to read live `fire_detections` and
   use ParkAssigner (one park per fire). This is what actually fixes the
   screenshot artifacts (Lomami/Upemba/Kundelungu rectangular seams +
   duplicate trajectories from overlapping bbox buffers).

3. **Season rebuild** after backfill: rebuild v6 since 2025-12-26 (like commit
   `0c890856`) → `load_fire_groups_to_db.py --force` → narratives. Then verify
   no duplicate trajectories in the screenshot region (~lon 24-28, lat -9..-4).

4. **`make build && sudo systemctl restart 5mp`** + validation (§5 step 9).
   Also check `srv/api.go:5085` (per-park fire counts from fire_detections
   with old protected_area_id assignments — decide: backfill 6.1M rows or gate).

5. Update AGENTS.md fire sections + `docs/FIRE_PIPELINE.md` when done.

### Key decisions locked in
- Relevance := `pct_inside > 0 OR min-trajectory-point-distance ≤ 20km`.
- Gating affects ONLY stats + notifications + park popup lists; map layers
  (`/api/features-in-bbox`, api.go global stats) stay ungated; geometry never clipped.
- One fire → one park (nearest boundary ≤100km). Overlap dedup comes from
  assignment, not from post-hoc filtering.
- NULL dist_to_park_km is treated as relevant (safety until pipelines all write it).
