# Fire Pipeline — Remaining Work (handover, 2026-08-05)

Context: commits `612da11..974a978` shipped v7 (SQLite source, Hungarian
matching, mass-aware cost, 3-sensor ingest, throttled notifications) plus
`scripts/eval_fire_trajectories.py`. **Always A/B algorithm changes with that
harness** — ablation flags reproduce v6 bit-exactly; verify that first.
See `docs/FIRE_PIPELINE.md` § v7.

Each item below was verified against live data. Numbers are real, not estimates.

---

## #2 — NRT→SP reconciliation (correctness, medium effort)

FIRMS revises NRT detections weeks later via Standard Processing (better
geolocation + confidence). We ingest NRT and **never re-fetch**, so early rows
keep their provisional coordinates forever.

Worse, re-ingesting SP would **duplicate rather than update**:

```sql
UNIQUE(latitude, longitude, acq_date, acq_time, satellite)  -- raw REALs
```

Verified: stored precision is inconsistent — of July 2026 rows, 1,321,500 have
5 decimal places, 132,413 have 4, 13,002 have 3, 1,390 have 2, 154 have 1. Any
SP coordinate revision changes the key → a second row for the same fire.

Also note `acq_time` is stored unpadded (`'1'`, `'11'`, `'1246'` all occur), so
it is not a stable key component either.

**Plan**
1. Add generated/rounded key columns (`lat5`, `lon5` at 5dp, `acq_hhmm`
   zero-padded), unique index on `(lat5, lon5, acq_date, acq_hhmm, satellite)`.
   Migration must dedupe existing rows first — expect collisions.
2. Add a `processing` column (`NRT` / `SP`) so SP can UPSERT over NRT.
3. New job: monthly re-fetch of T-60d..T-30d from the SP archive, `ON CONFLICT
   ... DO UPDATE` where `processing='NRT'`.
4. Reuse `scripts/backfill_viirs_sensors.py` for fetching — it already handles
   the 5-day cap and per-sensor SP/NRT availability windows.

Note: SP/NRT ranges do not overlap (SNPP_SP ends 2026-04-27, NRT starts
04-28), so you cannot diff the two for the same day to size the drift; validate
on historical dates instead.

---

## #10 — Persistent hotspot mask (CONFIRMED real, low effort, high value)

Query used:

```sql
SELECT protected_area_id, CAST(latitude/0.0034 AS INT), CAST(longitude/0.0034 AS INT),
       COUNT(DISTINCT substr(acq_date,1,7)) m, COUNT(*) n
FROM fire_detections
WHERE protected_area_id IS NOT NULL AND acq_date >= '2020-01-01'
GROUP BY 1,2,3 HAVING m >= 30 ORDER BY m DESC;
```

**15 cells burn in 75–78 distinct months** — i.e. essentially *every month* of
the 79-month record. These are not wildfires; they are flares, kilns, industrial
heat or a permanent sensor artifact:

| Park | months | detections |
|------|--------|-----------|
| DZA_Djurdjura (8 distinct cells) | 75–78 | 350–796 each |
| CAF_Dzanga_Park (2 cells) | 77–78 | 308, 360 |
| ZWE_Hwange | 78 | 597 |
| GAB_Ivindo | 77 | 316 |
| CMR_Boumba_Bek | 76 | 404 |
| ZAF_Sederberg | 76 | 759 |

DZA_Djurdjura dominates (it was onboarded with all-time backfill, so it has the
longest history — check whether other parks look similar once backfilled).

Earlier I checked ZMB_Kafue alone and found zero, and wrongly concluded this
wasn't biting. It is — just concentrated in specific parks.

**Plan**: build a `fire_persistent_cells` table (cell, park, months, first/last
seen), refresh monthly. Exclude those cells from **cluster seeding** in
`daily_clusters()` (they may still extend a track). Then re-run the eval harness
on DZA_Djurdjura / ZWE_Hwange / CAF_Dzanga_Park — add them to the golden set for
this task. Expect `frag_pct` and immortal-group counts to drop.

---

## #11 — Re-ignition duplicate group IDs (CONFIRMED real, low effort)

`track_to_group()` builds the ID from `park + start_date + first_point`:

```python
hash_input = f"{park_id}_{start_date}_{first_point[0]:.4f}_{first_point[1]:.4f}"
feature_id = f"{park_id}_{year}_grp_{group_hash}"
```

Incremental mode only walks the cutoff back for groups with
`end_date >= cutoff`. A group that ended earlier but reignites at the same spot
gets a **new** group next to the old one — and if start_date+location repeat,
an identical `feature_id`.

Verified across `data/fire_groups_v5/`: **722 duplicate feature_ids out of
181,711 groups**. Worst: `GHA_Mole` (e.g. `GHA_Mole_2025_grp_2721eebb` twice,
plus 2024/2023 cases).

Consequences: duplicate map features, duplicate notifications, and
`?notif_fire=` share links resolving ambiguously.

**Plan**: include `end_date` (or fire count) in the hash input so distinct
burns can't collide; add a post-build assertion that `feature_id` is unique per
park and fail loudly. Check whether `fire_group_names` / `feature_geometries`
already hold colliding rows before changing the hash — a hash change renames
groups, which breaks persistent friendly names, so plan a migration.

---

## #15 — Retire the duplicate raw JSON (now MUCH easier than expected)

`data/raw-fire-viirs-*/` is 176 MB rewritten wholesale nightly, duplicating
`fire_detections`. Since v7 the trajectory builder no longer reads it. Remaining
references — all trivial:

| File | Role | Action |
|------|------|--------|
| `scripts/onboard_park.py:217` `export_raw_fire_json()` | writer | **Delete.** Its own docstring says it exists only because "rebuild_fire_trajectories_v5.py reads these files, not the DB" — no longer true. |
| `scripts/daily_fire_update.py` `update_raw_json_files()` (step 2b) | writer | Delete once readers are gone. Also drops ~15s/night. |
| `scripts/rebuild_fire_front.py`, `rebuild_fire_hull.py` | readers | **Dead v4-era experiments.** Last touched by "Add fire trajectory v4"; output dirs `data/fire_groups_front/`, `data/fire_groups_hull/` **do not exist**, not in cron, not referenced by `srv/`. Delete or move to `scripts/deprecated/`. |
| `rebuild_nrt_parks.sh`, `run_nrt_rebuild.sh` | use dir only to **enumerate park names** (`ls $RAW_DIR/*.json`) | Replace with `SELECT DISTINCT protected_area_id FROM fire_detections`. |
| `scripts/backfill_raw_fire_json_100km.py`, `extract_raw_fire_json_from_backup.py` | one-off backfills | Deprecate. |
| `scripts/eval_fire_trajectories.py`, `fire_source.py` | intentional `--source json` A/B path | **Keep.** |

Sequence: fix the 2 shell scripts → delete/deprecate the dead readers → drop
both writers → `git rm -r` the data dir (it is tracked; this is a large but
welcome diff). Keep `--source json` working for A/B.

---

## #16 — Log rotation + pipeline heartbeat (trivial)

No logrotate rule exists (`/etc/logrotate.d/` has nginx, apt, ufw… but nothing
for 5mp). `logs/` is 3.5 MB; `daily_fire.log` is the largest and grows
unbounded — it was 3.2 MB/35k lines before I truncated nothing, and each nightly
run appends ~200 lines.

**Plan**
1. `/etc/logrotate.d/5mp`: `/home/exedev/5mp/logs/*.log`, `daily`, `rotate 14`,
   `compress`, `delaycompress`, `missingok`, `notifempty`, `copytruncate`.
2. Pipeline health is currently only visible by reading the log. `daily_fire_update.py`
   now notifies on download/rebuild failure (`_notify_system`), but there is no
   *success* heartbeat — a cron that never fires looks identical to success.
   Write a one-line status row (or a `data/pipeline_status.json`) with timestamp,
   detections ingested, parks rebuilt, alerts created, and surface staleness in
   the admin panel.

---

## Suggested order

1. **#16** (minutes, prevents future blindness)
2. **#11** (small, fixes user-visible duplicates — mind the friendly-name migration)
3. **#10** (small, clear quality win; verify with the eval harness)
4. **#15** (mechanical, big cleanup, low risk now)
5. **#2** (largest; needs a schema migration + dedupe)

Also still open: the per-overpass A/B, which needs
`scripts/backfill_viirs_sensors.py --sensor SNPP,NOAA21 --from 2024-01-01`
(~320 requests, resumable, tmux; 2024+ only — NOAA-21 has no SP archive before
2024-01-17). Recipe is in that script's docstring.
