# Production Deployment - 2026-02-24

## Summary

Successfully deployed Fire Pipeline v5 fixes to production. All deployment steps completed successfully.

## Changes Deployed

| Commit | Description |
|--------|-------------|
| a36ccb27 | Fix fire-realtime to always use v5 trajectory data from feature_geometries |
| 27433170 | Fix daily fire update: reduce to 5 days (FIRMS limit) and update raw JSON files |
| 813afcd4 | Revert yearly fire trend chart - keep only weekly chart |
| 04a42093 | Add sync trigger endpoints and fix cron scripts to use API |

## Data Restored

Downloaded and loaded backup data from pipeline server:

- **Fire Groups v5:** 162 parks, 172,616 trajectories (2020-2026)
- **Fire Narratives v5:** 162 parks with precomputed narratives
- **Database:** 171,752 fire trajectory records loaded

## Fixes Applied

| Issue | Fix | Status |
|-------|-----|--------|
| FIRMS NRT API returned empty | Changed from 7 days to 5 days (API limit) | ✅ |
| NRT fires not in trajectories | Daily update now syncs to raw JSON files | ✅ |
| Realtime showed different groups | Now always uses v5 feature_geometries data | ✅ |
| 2026 data in database | 219 fire groups for 2026 loaded | ✅ |
| Cron job updated | Uses 5-day window, runs 3am UTC daily | ✅ |

## Database Statistics

```
Fire Trajectories: 171,752 records
  - 2020: 31,282 groups
  - 2021: 32,715 groups
  - 2022: 29,412 groups
  - 2023: 29,498 groups
  - 2024: 31,573 groups
  - 2025: 17,052 groups
  - 2026: 219 groups

Fire Narratives: 162 parks cached (2020-2025 data)
```

## Cron Jobs

```bash
# Daily fire update (3am UTC) - v5 pipeline with 5-day window
0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py >> logs/daily_fire.log 2>&1
```

## Verification

All endpoints tested and working:

- ✅ `/api/parks/{id}/fire-realtime` - Returns v5 trajectory data
- ✅ `/api/parks/{id}/fire-narrative` - Returns cached narratives (2020-2025)
- ✅ Fire groups JSON files restored and accessible
- ✅ Server running on port 8000
- ✅ Database integrity verified

## Next Steps

The system will automatically:
1. Download new fires daily at 3am UTC (5-day window)
2. Update fire trajectories for affected parks
3. Sync raw JSON files in `data/fire_groups_v5/`

To update narratives with 2026 data in the cache, manually run:
```bash
python3 scripts/precompute_narratives_v5.py
```

## Access

Live URL: https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026
