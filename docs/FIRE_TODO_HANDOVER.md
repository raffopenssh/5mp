# Fire Pipeline — Open Work (handover, 2026-08-05)

Only **open** items are listed. Completed work is documented where it lives:
`AGENTS.md` § "Fire pipeline health & consistency" (hotspot mask, feature_id
dedupe, consistency checker, heartbeat) and `docs/FIRE_PIPELINE.md` § v7.
Commit `7b71c6f` closed handover #16, #11, #10 and half of #15.

**Always A/B algorithm changes** with `scripts/eval_fire_trajectories.py`.

---

## 1. IN FLIGHT — VIIRS SNPP/NOAA-21 backfill (blocks everything below)

```bash
tmux attach -t viirsbf              # or: tail -f logs/viirs_backfill.log
cat data/viirs_backfill_state.json
# if the session died, just rerun — resumable + idempotent:
python3 scripts/backfill_viirs_sensors.py --sensor SNPP,NOAA21 --from 2024-01-01
```

At handover: SNPP through 2024-08-28, NOAA21 through 2025-06-06. ~270 requests,
~60s each. `fire_detections` 22M → 30.7M rows so far; db.sqlite3 9.6 GB, 73 GB
free.

---

## 2. Full rebuild + consistency repair (do first once #1 finishes)

The backfill added years of SNPP/NOAA-21 fires, so a full non-incremental
rebuild is wanted anyway — and it also clears the existing drift.

```bash
bash rebuild_nrt_parks.sh                        # all parks, one process, mask on
python3 scripts/load_fire_groups_to_db.py --force
python3 scripts/precompute_narratives_v5.py      # ONLY legal cache writer
python3 scripts/build_fire_grid_agg.py           # animator aggregates
python3 scripts/check_fire_consistency.py --verbose   # must exit 0
```

Current drift (why this matters — users see "Feature not found" when pinning a
fire from a narrative): 14,950 groups missing from `feature_geometries`, 17,207
stale DB rows, 722 duplicate JSON ids, 681 narratives on an ambiguous id, 7
dangling. `NAM_Ai-Ais_Hot_Springs` and `ZAF_Richtersveld` have JSON but zero DB
rows.

Alternative if a full rebuild is undesirable: `scripts/fix_fire_consistency.py`
(101 drifted parks, ~15-25 min, idempotent, `--dry-run` first). Verified on
`TZA_Mahale_Mts`: 871 DB rows → 690, matching JSON.

---

## 3. Hotspot-mask A/B (owed)

The mask is live but my A/B is uninterpretable — it ran mid-backfill, so
`fires +44.9%` / `CAF_Dzanga_Park groups +1966%` is the backfill, not the mask.
Redo on a **frozen** DB (baseline snapshot and candidate from the same data):

```bash
P=DZA_Djurdjura,ZWE_Hwange,CAF_Dzanga_Park,COD_Virunga,GAB_Lopé,ZMB_Kafue
python3 scripts/eval_fire_trajectories.py --snapshot data/eval/pre_mask --parks $P
python3 scripts/rebuild_fire_trajectories_v5.py --parks $P \
    --no-hotspot-mask --output-dir data/eval/no_mask
python3 scripts/eval_fire_trajectories.py \
    --baseline data/eval/no_mask --candidate data/eval/pre_mask --parks $P
```

Expect `frag_pct` and immortal-group counts to drop. Watch `DZA_Djurdjura`
specifically: 36 masked cells and the longest history (all-time backfill), and it
looked *worse* on `frag_pct`/`mean_days` in the polluted run.

---

## 4. Per-overpass slicing A/B — the point of the backfill

`USE_OVERPASS = False` in `rebuild_fire_trajectories_v5.py`. With one satellite
the night pass was 12× sparser (763 fires @ FRP 10.6 vs 63 @ 1.7), giving
`mean_days -21%`, `dup_pairs +23%`, `coverage -2.4%`. With ~6 passes/day each
slice should stand on its own. Recipe in
`scripts/backfill_viirs_sensors.py`'s docstring:

```bash
python3 scripts/eval_fire_trajectories.py --snapshot data/eval/pre_overpass
python3 scripts/rebuild_fire_trajectories_v5.py \
    --parks CAF_Chinko,ZMB_Kafue,COD_Virunga,TZA_Serengeti,CMR_Nki,MOZ_Niassa \
    --overpass --output-dir data/eval/overpass
python3 scripts/eval_fire_trajectories.py \
    --baseline data/eval/pre_overpass --candidate data/eval/overpass
```

Flip only if `fires_per_grp` / `frag_pct` / `mean_days` / `coverage_pct` all hold
or improve. Again: frozen DB, no concurrent ingest.

---

## 5. Admin-panel staleness badge (small)

`GET /api/pipeline-status` (`srv/pipeline_status.go`) already serves the
heartbeat with `status` (`ok`/`degraded`/`failed`), `stale` (>48h = two missed
runs), `age_hours`, per-step counters and `errors[]`. Nothing in the UI consumes
it yet — add a badge in the admin panel.

---

## 6. #15 — finish retiring the duplicate raw JSON

Done: the two shell scripts enumerate parks from `fire_detections`; dead v4-era
readers are in `scripts/deprecated/`.

Remaining:
1. Delete `export_raw_fire_json()` in `scripts/onboard_park.py` (~line 217).
2. Delete `update_raw_json_files()` (step 2b) in `daily_fire_update.py`
   (~15s/night). ⚠️ it also feeds `self.affected_parks` via `parks_updated`;
   `insert_fires()` already populates that set — verify affected-park coverage
   does not shrink before deleting.
3. `git rm -r data/raw-fire-viirs-20200101-20260222` (tracked, 176 MB).
4. Decide the fate of `--source json` in `rebuild_fire_trajectories_v5.py` /
   `eval_fire_trajectories.py`: keep for A/B (it will error once the dir is
   gone) or drop the flag.

---

## 7. #2 — NRT→SP reconciliation (largest, untouched)

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
is stored unpadded (`'1'`, `'11'`, `'1246'` all occur), so it is not a stable key
component either.

Plan:
1. Rounded key columns (`lat5`, `lon5` at 5dp, `acq_hhmm` zero-padded) + unique
   index on `(lat5, lon5, acq_date, acq_hhmm, satellite)`. Migration must dedupe
   existing rows first — expect collisions. The table is now **30.7M rows**:
   back up first, budget a slow migration.
2. `processing` column (`NRT`/`SP`) so SP can UPSERT over NRT.
3. Monthly re-fetch of T-60d..T-30d from the SP archive with
   `ON CONFLICT ... DO UPDATE WHERE processing='NRT'`.
4. Reuse `scripts/backfill_viirs_sensors.py` to fetch — it already handles the
   5-day cap and per-sensor SP/NRT availability windows.

SP/NRT ranges do not overlap (SNPP_SP ends 2026-04-27, NRT starts 04-28), so you
cannot diff the two for the same day to size the drift; validate on historical
dates instead.

---

## Order

1 → 2 → 3 → 4 → 5 → 6 → 7.
