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
