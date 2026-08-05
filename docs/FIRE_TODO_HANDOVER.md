# Fire Pipeline — Remaining Work (handover, updated 2026-08-05 late)

Previous handover shipped v7 (SQLite source, Hungarian matching, mass-aware
cost, 3-sensor ingest, throttled notifications) + `eval_fire_trajectories.py`.
**Always A/B algorithm changes with that harness.** See `docs/FIRE_PIPELINE.md` § v7.

This session closed #16, #11, #10 and the cheap half of #15, and started the
SNPP/NOAA-21 backfill that the per-overpass A/B depends on. Details below;
everything still open is at the bottom.

---

## ⚠️ IN FLIGHT — VIIRS backfill running in tmux

```bash
tmux attach -t viirsbf         # or: tail -f logs/viirs_backfill.log
cat data/viirs_backfill_state.json
```

Command (resumable, idempotent — just rerun it if the session dies):

```bash
python3 scripts/backfill_viirs_sensors.py --sensor SNPP,NOAA21 --from 2024-01-01
```

Progress at handover: SNPP through 2024-08-13, NOAA21 through 2025-06-06.
~270 requests total, ~60s each, so allow ~4h. `fire_detections` grew from
~22M to 30.7M rows; db.sqlite3 is 9.6 GB (73 GB free — fine).

**Do not run the overpass A/B until this finishes** — see next section.

---

## #OVERPASS — still open, and the A/B must be redone

`USE_OVERPASS = False` in `rebuild_fire_trajectories_v5.py`. The recipe is in
`scripts/backfill_viirs_sensors.py`'s docstring. Two cautions learned today:

1. **Do not A/B while the backfill is running.** I ran the hotspot-mask A/B
   mid-backfill and the result is uninterpretable: `fires +44.9%` and
   `CAF_Dzanga_Park groups +1966%` are the backfill arriving, not the mask.
   Snapshot baseline and build candidate from the *same* frozen DB.
2. Once fires stop moving, re-snapshot everything:

```bash
python3 scripts/build_fire_grid_agg.py                       # animator agg
python3 scripts/eval_fire_trajectories.py --snapshot data/eval/pre_overpass
python3 scripts/rebuild_fire_trajectories_v5.py \
    --parks CAF_Chinko,ZMB_Kafue,COD_Virunga,TZA_Serengeti,CMR_Nki,MOZ_Niassa \
    --overpass --output-dir data/eval/overpass
python3 scripts/eval_fire_trajectories.py \
    --baseline data/eval/pre_overpass --candidate data/eval/overpass
```

Flip `USE_OVERPASS` only if `fires_per_grp` / `frag_pct` / `mean_days` /
`coverage_pct` all hold or improve. Pre-backfill numbers to beat (single
satellite): `mean_days -21%`, `dup_pairs +23%`, `coverage -2.4%`.

---

## ✅ #16 — Log rotation + heartbeat (DONE)

- `/etc/logrotate.d/5mp` installed; source of truth committed as `5mp.logrotate`
  (daily, rotate 14, compress, delaycompress, copytruncate, `su exedev exedev`).
  Verified with `sudo logrotate -d /etc/logrotate.d/5mp`.
- `daily_fire_update.py` now writes `data/pipeline_status.json` on **every** run
  including fatal ones (`write_status()`; `run()` wraps `_run()`), with
  timestamps, duration, `fires_fetched`, `detections_inserted`, `parks_rebuilt`,
  `groups_loaded`, `alerts_created`, `notifications_created`, `consistent`, and
  a per-step `errors[]` list. Status is `ok` / `degraded` / `failed`.
- `GET /api/pipeline-status` (`srv/pipeline_status.go`) serves it and adds
  `stale` (>48h = two missed runs) + `age_hours`.

**Not done:** no admin-panel UI consumes `/api/pipeline-status` yet. That is the
one remaining piece of #16 — a staleness badge in the admin panel.

---

## ✅ #11 — Re-ignition duplicate group IDs (DONE)

`dedupe_feature_ids()` in `rebuild_fire_trajectories_v5.py`, called after the
per-park sort in `main()`; raises if any duplicate survives.

Deliberate design: the primary hash is **unchanged**, so no existing group is
renamed. Only actual collisions get a discriminated hash (salted with
`end_date` + `fire_count`), which is 722 of 181,711 groups (0.4%) — that keeps
the 50,748 persisted friendly names in `fire_group_names` valid.

Verified: idempotent (second pass is a no-op), no JSON feature_id disappears,
and **0 newly-dangling references** in `feature_geometries`,
`fire_group_names`, `notifications` or `fire_narrative_cache`.

Duplicates are only removed from the JSON once each park is rebuilt or repaired
— see the consistency section.

---

## ✅ #10 — Persistent hotspot mask (DONE, but A/B still owed)

- `scripts/build_persistent_hotspots.py` → `fire_persistent_cells`
  (0.0034° ≈ 375m cell, ≥30 distinct months, ≥24-month span, ≥50 detections).
  **323 cells across 32 parks.** Run monthly; `--report` is read-only.
- Biggest offenders are physically real and exactly the intended target:
  `COD_Virunga` 61 cells / 20,367 detections (Nyiragongo + Nyamuragira lava
  lakes), `ZWE_Hwange` 40, `DZA_Djurdjura` 36, `GAB_Lopé` 31, `GAB_Loango` 23.
- Builder: `daily_clusters(fires, persistent_mask)` excludes masked detections
  from DBSCAN **and** from the singleton-noise fallback, so they cannot seed a
  track. They are still absorbed into a real cluster within `DAY_EPS_KM`
  (`_absorb`), so a genuine front crossing a flare keeps its fire count.
- Ablation flag `--no-hotspot-mask` reproduces pre-mask behaviour;
  `hotspot_mask` is recorded in the run summary params.
- Nightly: `refresh_persistent_hotspots()` (step 2d) runs on the 1st only.

**Owed:** a clean A/B. Mine was polluted by the concurrent backfill (see above).
Golden set for this feature: `DZA_Djurdjura,ZWE_Hwange,CAF_Dzanga_Park` (+
`COD_Virunga,GAB_Lopé,ZMB_Kafue`). Expect `frag_pct` and immortal-group counts
to drop. Note `DZA_Djurdjura` looked *worse* on `frag_pct`/`mean_days` in the
polluted run — check that specifically, it has the longest history (all-time
backfill) and 36 masked cells, so it is the most sensitive park.

---

## 🔶 CONSISTENCY — new, partly done, biggest remaining job

Found while validating #11: the three fire artefacts have drifted badly.

| Artefact | Count |
|---|---|
| `data/fire_groups_v5/*.json` | 181,711 groups (180,989 unique ids) |
| `feature_geometries` (fire_trajectory) | 183,246 |
| narrative references | 155,933 |

Drift: **14,950 groups missing from the DB**, **17,207 stale DB rows** (rebuilt
parks that were never re-loaded), 722 duplicate ids, 681 narratives pointing at
an ambiguous (duplicated) id, 7 outright dangling. Two parks
(`NAM_Ai-Ais_Hot_Springs`, `ZAF_Richtersveld`) have JSON but no DB rows at all.
Symptom for users: clicking a fire in the popup narrative → "Feature not found".

New tooling:

- `scripts/check_fire_consistency.py` — read-only; `--verbose`, `--json`;
  exit 1 on drift. Wired into the nightly run as step 7 and into the heartbeat
  as `consistent: true|false`.
- `scripts/fix_fire_consistency.py` — repairs: (1) dedupe JSON ids with a
  timestamped `.bak-<ts>` per file, (2) `load_fire_groups_to_db.py --park X
  --force` (deletes that park's rows first, so stale rows go), (3)
  `precompute_narratives_v5.py --park X`. `--dry-run`, `--park`, `--all-parks`,
  `--skip-narratives`. Idempotent.

Verified on one park: `TZA_Mahale_Mts` 871 DB rows → 690, matching its JSON.

**TODO — run the full repair once the backfill finishes:**

```bash
tmux new -s fixcons "cd /home/exedev/5mp && \
  python3 scripts/fix_fire_consistency.py 2>&1 | tee logs/fix_consistency.log"
python3 scripts/check_fire_consistency.py --verbose   # must exit 0
```

101 parks drifted, ~15-25 min (the loader does river/road/settlement context
enrichment per park). Because the backfill added years of SNPP/NOAA-21 fires, a
**full non-incremental rebuild is wanted anyway** — prefer:

```bash
bash rebuild_nrt_parks.sh          # all parks, one process, mask applied
python3 scripts/load_fire_groups_to_db.py --force
python3 scripts/precompute_narratives_v5.py
python3 scripts/build_fire_grid_agg.py
python3 scripts/check_fire_consistency.py
```

Remember `precompute_narratives_v5.py` is the **only** legal writer of
`fire_narrative_cache` (AGENTS.md).

---

## 🔶 #15 — Retire the duplicate raw JSON (half done)

Done:
- `rebuild_nrt_parks.sh` / `run_nrt_rebuild.sh` now enumerate parks from
  `SELECT DISTINCT protected_area_id FROM fire_detections` instead of
  `ls data/raw-fire-viirs-*/`. `rebuild_nrt_parks.sh` also switched to a single
  `--parks` process (was one subprocess per park).
- Dead readers moved to `scripts/deprecated/`: `rebuild_fire_front.py`,
  `rebuild_fire_hull.py`, `backfill_raw_fire_json_100km.py`,
  `extract_raw_fire_json_from_backup.py` (README updated, `FIRE_PIPELINE.md`
  § corrected).

Still to do:
1. Delete `export_raw_fire_json()` in `scripts/onboard_park.py` (~line 217).
2. Delete `update_raw_json_files()` (step 2b) in `daily_fire_update.py`
   — saves ~15s/night. Note it currently also feeds `self.affected_parks`
   via `parks_updated`; `insert_fires()` already populates that set, so verify
   affected-park coverage doesn't shrink before deleting.
3. `git rm -r data/raw-fire-viirs-20200101-20260222` (tracked, 176MB, large but
   welcome diff).
4. Keep `--source json` in `rebuild_fire_trajectories_v5.py` and
   `eval_fire_trajectories.py` working for A/B (they degrade gracefully to an
   error if the dir is gone — decide whether that's acceptable or whether the
   flag should be dropped too).

---

## 🔴 #2 — NRT→SP reconciliation (untouched, largest)

FIRMS revises NRT detections weeks later via Standard Processing (better
geolocation + confidence). We ingest NRT and never re-fetch, so early rows keep
provisional coordinates forever. Re-ingesting SP would **duplicate rather than
update**:

```sql
UNIQUE(latitude, longitude, acq_date, acq_time, satellite)  -- raw REALs
```

Verified: stored precision is inconsistent — of July 2026 rows, 1,321,500 have
5 decimals, 132,413 have 4, 13,002 have 3, 1,390 have 2, 154 have 1. Any SP
coordinate revision changes the key → a second row for the same fire. `acq_time`
is also stored unpadded (`'1'`, `'11'`, `'1246'` all occur), so it is not a
stable key component either.

Plan (unchanged):
1. Rounded key columns (`lat5`, `lon5` at 5dp, `acq_hhmm` zero-padded), unique
   index on `(lat5, lon5, acq_date, acq_hhmm, satellite)`. The migration must
   dedupe existing rows first — expect collisions. **Note: the table is now
   30.7M rows, so budget for a slow migration and back up first.**
2. `processing` column (`NRT`/`SP`) so SP can UPSERT over NRT.
3. Monthly re-fetch of T-60d..T-30d from the SP archive with
   `ON CONFLICT ... DO UPDATE WHERE processing='NRT'`.
4. Reuse `scripts/backfill_viirs_sensors.py` for fetching — it already handles
   the 5-day cap and per-sensor SP/NRT availability windows.

SP/NRT ranges don't overlap (SNPP_SP ends 2026-04-27, NRT starts 04-28), so you
cannot diff the two for the same day to size the drift; validate on historical
dates instead.

---

## Suggested order from here

1. Wait out the backfill, then **full rebuild + reload + narratives**, then
   `check_fire_consistency.py` until it exits 0.
2. Clean A/B for the hotspot mask (#10) on a frozen DB; pay attention to
   `DZA_Djurdjura`.
3. The overpass A/B — the whole point of the backfill.
4. Admin-panel staleness badge consuming `/api/pipeline-status`.
5. Finish #15 (2 writers + `git rm` the data dir).
6. #2 (schema migration + dedupe on 30.7M rows).
