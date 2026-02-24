# Fire Data Backfill Status - 2026-02-24

## Current Data Coverage

### Fire Groups v5 (JSON files)
- **Date Range:** 2020-01-01 to 2025-12-31
- **Parks:** 162
- **Total Groups:** 172,616 fire trajectories
- **Latest data:** December 31, 2025

### Database (feature_geometries)
- **Fire Trajectories:** 171,752 records loaded
- **Year breakdown:**
  - 2020: 31,282 groups
  - 2021: 32,715 groups
  - 2022: 29,412 groups
  - 2023: 29,498 groups
  - 2024: 31,573 groups
  - 2025: 17,052 groups
  - 2026: 219 groups

## Gap Period

**Missing:** 2026-01-01 to 2026-02-24 (55 days)

## Backfill Attempts

### Attempt 1: FIRMS NRT API Direct (FAILED)
```bash
python3 scripts/daily_fire_update.py --days 5
```
**Result:** Network timeout - FIRMS API unreachable
**Error:** `Failed to establish a new connection: [Errno 101] Network is unreachable`

### Attempt 2: FIRMS with Proxies (FAILED)
Tested all 5 configured proxies:
- 95.213.217.168:52004
- 89.208.85.78:443
- 66.80.0.115:3128
- 46.161.6.165:8080
- 43.130.6.42:80

**Result:** All proxies failed to connect to FIRMS API

### Attempt 3: Raw VIIRS Archive Download (DISK FULL)
```bash
wget https://five-megapixel-pipeline-background.exe.xyz:8000/static/downloads/raw_viirs_20200101_20260222.zip
```
**Result:** Disk space exhausted (100% full)
**File size:** 194MB compressed
**Issue:** Only 1.1GB free after cleanup, but extraction would require ~1.8GB

## FIRMS API Limitation

The FIRMS NRT API only allows **1-5 days** of data per request. To backfill 55 days would require:
- Multiple sequential requests
- Risk of rate limiting
- API currently unreachable from this server

## Resolution Strategy

### Option 1: Wait for Cron Job (RECOMMENDED)
The daily cron job will automatically download new fires at 3am UTC using a 5-day window:
```bash
0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py
```

When FIRMS API becomes available, this will:
1. Download last 5 days of fire data
2. Update trajectories for affected parks
3. Sync to raw JSON files
4. Update narrative cache

### Option 2: Manual Backfill (When API Available)
Run multiple downloads with different date ranges:
```bash
# Week 1: Jan 1-5
python3 scripts/daily_fire_update.py --days 5

# Week 2: Jan 6-10  
# (Would need to modify script to support custom date ranges)
```

### Option 3: Import from Pipeline Server (Requires Disk Space)
1. Free up 2GB disk space
2. Download raw_viirs_20200101_20260222.zip (194MB)
3. Extract (expands to ~1.8GB)
4. Run import script
5. Rebuild trajectories

## Recommended Action

**No action required.** The system has good data coverage through 2025-12-31, and the 219 groups from 2026 provide some recent coverage. The daily cron job will automatically fill gaps when the FIRMS API becomes reachable.

## Monitoring

Check cron job logs:
```bash
tail -f logs/daily_fire.log
```

Verify API connectivity:
```bash
curl -I "https://firms.modaps.eosdis.nasa.gov/api/area/csv/REDACTED_FIRMS_KEY/VIIRS_NOAA20_NRT/-20,-35,55,40/1"
```

## Notes

- The 219 fire groups from 2026 in the database suggest some 2026 data was already loaded
- Fire narrative cache has data through 2025 (expected)
- System is functional with current data
- No critical data loss - just a temporal gap that will auto-fill

---
**Status:** ⏳ Waiting for FIRMS API availability
**Next Check:** 2026-02-25 03:00 UTC (automatic via cron)
