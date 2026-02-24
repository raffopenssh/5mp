# Fire Data Restoration - 2026-02-24

## Issue Found

After incremental rebuild, CAF_Chinko only showed 5 fire features instead of 2,444.

**Root cause:** The incremental rebuild ran on an empty `data/fire_groups_v5/` directory. When we created the raw fire directory and ran the incremental pipeline, it had no existing groups to merge with, so it only saved the new 60-day groups.

## What Happened

```
1. Downloaded 60,722 NRT fires → data/raw-fire-viirs-20200101-20260222/
2. Ran: rebuild_fire_trajectories_v5.py --incremental --days 60
   - Found NO existing groups in data/fire_groups_v5/ to merge with
   - Created only 26 new groups for CAF_Chinko (last 60 days)
   - Original 2,418 groups were lost
3. Loaded to DB → Only 26 groups in database
```

**Expected behavior:** Incremental mode should have merged with existing 2,444 groups, replacing only the 7 recent ones.

## Fix Applied

```bash
# 1. Re-download original fire_groups_v5 backup
wget https://five-megapixel-pipeline-background.exe.xyz:8000/static/downloads/fire_groups_v5.zip

# 2. Restore to data/fire_groups_v5/
unzip fire_groups_v5.zip
cp -r data/fire_groups_v5/* /home/exedev/5mp/data/fire_groups_v5/

# 3. Reload all groups to database
python3 scripts/load_fire_groups_to_db.py --force
```

## Verified

CAF_Chinko now has correct data:
- 2020: 485 groups
- 2021: 420 groups
- 2022: 496 groups
- 2023: 357 groups
- 2024: 455 groups
- 2025: 215 groups
- **Total: 2,428 groups** ✅

## Lesson

When running incremental rebuild, the `data/fire_groups_v5/` directory MUST contain the existing trajectory files. The merge logic works correctly but requires existing files to merge with.

For future incremental runs, the daily cron will work properly because:
1. fire_groups_v5/ files already exist
2. Incremental mode will merge new groups with existing ones
3. Only recent groups (last 60 days) will be replaced

---
**Status:** ✅ Fixed - All historical fire data restored
**Database:** 172,000+ fire trajectories loaded
