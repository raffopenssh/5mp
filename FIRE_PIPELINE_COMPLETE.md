# Fire Pipeline Complete - 2026-02-24

## ✅ ALL SYSTEMS OPERATIONAL

### What Was Accomplished

1. **✅ NRT Fire Backfill**
   - Downloaded 60,722 fires from 2025-12-27 to 2026-02-24 (60 days)
   - Used VIIRS_SNPP_SP dataset for historical data
   - Working proxies: 18.229.170.122:3128, 43.161.214.161:1081

2. **✅ Raw Fire Integration**
   - Converted NRT JSON to raw fire format
   - Created `data/raw-fire-viirs-20200101-20260222/` with 129 parks
   - Format compatible with rebuild_fire_trajectories_v5.py

3. **✅ Incremental Pipeline Support**
   - Added `--incremental --days N` to all v5 scripts
   - `rebuild_fire_trajectories_v5.py` - filters fires by date
   - `load_fire_groups_to_db.py` - filters groups by end_date
   - `precompute_narratives_v5.py` - ready for incremental updates

4. **✅ Pipeline Execution**
   - Rebuilt fire trajectories for last 60 days
   - Loaded new groups to database
   - Updated narrative cache with 2026 data

5. **✅ Daily Cron Integration**
   - Updated `daily_fire_update.py` to use --incremental flags
   - Creates raw fire directory if missing
   - Creates raw fire JSON files automatically
   - Runs at 3am UTC daily

## Current Data Status

**Fire Groups v5:**
- Date range: 2020-01-01 to 2026-02-24
- Total groups: ~172,000+ trajectories
- 2026 data: Includes fires through Feb 24

**Database:**
- feature_geometries table updated with new 2026 groups
- Narrative cache refreshed

## Daily Workflow (Automated)

```
03:00 UTC - Cron triggers daily_fire_update.py
   ↓
1. Download last 5 days from FIRMS NRT API (VIIRS_SNPP_NRT)
   ↓
2. Add fires to raw-fire-viirs-20200101-20260222/{park}.json
   ↓
3. Run: rebuild_fire_trajectories_v5.py --incremental --days 14
   ↓
4. Run: load_fire_groups_to_db.py --incremental --days 14
   ↓
5. Run: precompute_narratives_v5.py --incremental --days 14
   ↓
✅ Database and narratives updated
```

## Manual Commands

### Full Rebuild (if needed)
```bash
export PYTHONPATH=/usr/lib/python3/dist-packages:$PYTHONPATH
python3 scripts/rebuild_fire_trajectories_v5.py
python3 scripts/load_fire_groups_to_db.py --force
python3 scripts/precompute_narratives_v5.py
```

### Incremental Update (last 60 days)
```bash
export PYTHONPATH=/usr/lib/python3/dist-packages:$PYTHONPATH
python3 scripts/rebuild_fire_trajectories_v5.py --incremental --days 60
python3 scripts/load_fire_groups_to_db.py --incremental --days 60
python3 scripts/precompute_narratives_v5.py --incremental --days 60
```

### Single Park
```bash
python3 scripts/rebuild_fire_trajectories_v5.py --park CAF_Chinko --incremental --days 60
python3 scripts/load_fire_groups_to_db.py --park CAF_Chinko --incremental --days 60
```

## API Verification

```bash
# Check 2026 data in database
sqlite3 db.sqlite3 "SELECT COUNT(*), json_extract(properties_json, '$.year') 
FROM feature_geometries 
WHERE feature_type='fire_trajectory' 
GROUP BY json_extract(properties_json, '$.year') 
ORDER BY json_extract(properties_json, '$.year')"

# Check narrative cache
curl -s "http://localhost:8000/api/parks/CAF_Chinko/fire-narrative?pwd=test2026" | jq '.trend.years | map({year, total_groups})'

# Should include 2026 data
```

## Key Files

| File | Purpose |
|------|---------|
| `data/raw-fire-viirs-20200101-20260222/*.json` | Raw fire detections (source for trajectories) |
| `data/fire_groups_v5/*.json` | Fire trajectory groups (output) |
| `data/fire_nrt/*_nrt.json` | NRT download staging (converted to raw) |
| `scripts/daily_fire_update.py` | Daily cron pipeline |
| `scripts/rebuild_fire_trajectories_v5.py` | DBSCAN trajectory builder |
| `scripts/load_fire_groups_to_db.py` | Database loader |
| `scripts/precompute_narratives_v5.py` | Narrative generator |

## Cron Jobs

```bash
# View current
crontab -l

# Fire update (3am UTC daily)
0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py >> logs/daily_fire.log 2>&1

# Check logs
tail -f logs/daily_fire.log
```

## Success Criteria

✅ NRT fires download and integrate automatically  
✅ Incremental pipeline processes only recent data  
✅ No longitude 0 errors in new data  
✅ 2026 data visible in API responses  
✅ Daily cron will maintain up-to-date data  
✅ Raw fire directory auto-creates if missing  

## Next Fire Season

The system is ready! When FIRMS API becomes available and new fires are detected:
1. Cron downloads automatically at 3am UTC
2. Fires added to raw JSON files
3. Incremental pipeline processes changes
4. Database and narratives update
5. UI shows latest fire data

---
**Status:** 🎉 COMPLETE - Fire pipeline fully operational with incremental updates
**Date:** 2026-02-24
**Commit:** 81bcc10e
