# Fire Pipeline Status - 2026-02-24 08:33 UTC

## Current Situation

**Pipeline is running but won't process NEW fire data yet.**

The issue: The NRT backfill data (9,942 fires in `data/fire_nrt/*.json`) is separate from the main fire data that the v5 pipeline processes.

## What's Running Now

✅ Step 2: Loading existing fire groups from `data/fire_groups_v5/` to database (~172K trajectories from 2020-2025)
⏳ ETA: ~5 minutes to complete
🔄 Then: Will precompute narratives (another ~5-10 min)

**Result:** Database will be refreshed with existing data, but won't include the new 9,942 fires yet.

## The Real Fix Needed

The NRT fire data needs to be integrated into the raw VIIRS files that the rebuild script reads from. Here's the actual flow:

```
Raw VIIRS CSV files         →  rebuild_fire_trajectories_v5.py  →  fire_groups_v5/*.json
(data/raw-fire-viirs-*)        (builds trajectories)               (trajectory groups)
                               
NRT JSON files (NEW)        →  Need to merge here ↑
(data/fire_nrt/*.json)
```

## Solution Options

### Option 1: Use daily_fire_update.py (Proper Integration)

This script is designed to:
1. Download NRT fires
2. Add them to raw VIIRS files  
3. Rebuild trajectories for affected parks
4. Update database

Since we already downloaded the NRT data, we can modify the script to skip download and just process existing NRT files.

### Option 2: Manual Integration Script

Create a script to merge `data/fire_nrt/*.json` into the raw VIIRS CSV files, then re-run rebuild.

### Option 3: Wait for Daily Cron

The daily cron (3am UTC) will:
- Download last 5 days from FIRMS
- Process properly through the pipeline
- Fix longitude 0 errors going forward

**Recommendation:** Let current pipeline finish (gives us fresh baseline), then run daily_fire_update manually tomorrow when FIRMS API is working.

## Current Pipeline Progress

```bash
# Monitor
tail -f logs/fire_v5_full_pipeline_20260224_0832.log

# Check if still running
ps aux | grep load_fire_groups
```

## Next Steps After Current Pipeline Completes

1. ✅ Database will have clean 2020-2025 data
2. 📝 Document that 2026-01-01 to 2026-02-24 gap exists
3. ⏰ Wait for FIRMS API to become available
4. 🔄 Run daily_fire_update.py to catch up

The 9,942 NRT fires are safely stored in `data/fire_nrt/` and can be processed later when we have a proper integration path.

---
**Bottom Line:** Current pipeline will complete successfully but won't include the new backfill data. We need a different approach to integrate NRT JSON files.
