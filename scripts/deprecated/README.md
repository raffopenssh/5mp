# Deprecated Scripts

These scripts are no longer used by the active pipeline but are kept for reference.

## Reason for Deprecation

The fire data pipeline was upgraded from v2/v3 to v5 with the following improvements:

- **v2/v3**: Used separate `data/fire_nrt/` JSON files
- **v5**: Uses database as source of truth with incremental JSON updates

## Current Active Pipeline

See `scripts/daily_fire_update.py` which runs daily at 3am UTC and uses:

1. `rebuild_fire_trajectories_v5.py` - DBSCAN clustering with trajectory detection
2. `load_fire_groups_to_db.py` - Load groups to database
3. `precompute_narratives_v5.py` - Generate fire narratives

## Files in This Directory

- `rebuild_park_fire_analysis_v2.py` - Old fire analysis (used fire_nrt/)
- `rebuild_park_fire_analysis_v3.py` - Old fire analysis (used fire_nrt/)
- `build_unified_fire_dataset.py` - Old dataset builder
- `backfill_fires_extended_buffer.py` - Old backfill script
- Various GHSL and deforestation processing scripts (superseded by v4+)

## If You Need to Use These

These scripts reference `data/fire_nrt/` which has been removed (18MB freed).
They are incompatible with the current v5 pipeline.

For historical fire analysis, use the database directly:
```sql
SELECT * FROM fire_detections WHERE acq_date >= '2020-01-01';
```

## Deprecated 2026-08-05 (raw JSON retirement, handover #15)

Since v7 the trajectory builder reads `fire_detections` directly
(`scripts/fire_source.py`), so `data/raw-fire-viirs-*/` is redundant duplicated
data and everything that depends on it is being retired:

| File | Why |
|------|-----|
| `rebuild_fire_front.py` | v4-era experiment. Output dir `data/fire_groups_front/` never existed, not in cron, not read by `srv/`. |
| `rebuild_fire_hull.py` | Same, for `data/fire_groups_hull/`. |
| `backfill_raw_fire_json_100km.py` | One-off backfill of the raw JSON window. |
| `extract_raw_fire_json_from_backup.py` | One-off restore of the raw JSON window from a DB backup. |
| `rebuild_fire_trajectories_v4.py` | v4 builder; read the raw JSON dir, superseded by v5/v7. |

**Completed 2026-08-05** (handover #15 closed): both writers are gone
(`onboard_park.py:export_raw_fire_json()`,
`daily_fire_update.py:update_raw_json_files()` / step 2b), the 179MB
`data/raw-fire-viirs-20200101-20260222/` directory is deleted, and
`--source json` was dropped from `rebuild_fire_trajectories_v5.py` +
`fire_source.py` (SQLite is now the only fire source). Old A/B baselines built
from the JSON window are still comparable via `data/eval/*` snapshots.
