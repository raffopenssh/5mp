# Fire NRT Backfill Summary - 2026-02-24

## Objective
Backfill fire data from 2025-12-27 to 2026-02-24 (60 days) using FIRMS NRT API to fix longitude 0 error in existing data.

## Proxy Configuration

Found 2 working proxies from GitHub free proxy lists:
- `18.229.170.122:3128` ✅ 
- `43.161.214.161:1081` ✅

Updated `/scripts/fire_nrt/config.py` with working proxies.

## Backfill Process

**Command:**
```bash
python3 scripts/fire_nrt/download_nrt.py --all --backfill --start 2025-12-27 --end 2026-02-24
```

**Status:** Running (in progress)
- Started: 2026-02-24 07:46 UTC  
- Progress: ~60/162 parks processed
- ETA: ~4-5 hours (162 parks × 60 days × API rate limits)

## Issues Encountered

1. **Proxy Instability**
   - Multiple "500 Internal Server Error" from proxies
   - Read timeouts after 60s
   - Proxy connection drops intermittently

2. **No Fires Found**
   - All processed parks returning "No fires in last 60 days"
   - Unclear if this is due to:
     - Actual absence of fires in this period
     - API returning empty results due to proxy issues
     - NRT data not available for historical 60-day period

3. **Performance**
   - Very slow: ~15 minutes for 60 parks
   - Many timeout and retry errors
   - At current rate: 162 parks would take 40+ minutes

## Current Data Status

**Existing Data (Pre-Backfill):**
- Fire groups v5: 2020-01-01 to 2025-12-31
- Database: 171,752 fire trajectories  
- 2026 data: 219 groups (with longitude 0 error)

## Recommendations

### Option 1: Let Daily Cron Handle It (RECOMMENDED)
The daily cron job at 3am UTC will automatically:
- Download last 5 days of NRT fires
- Fix any longitude 0 errors in new data
- Gradually fill the gap as fresh data arrives

**Advantages:**
- No manual intervention required
- More reliable with fresh NRT data
- Uses proven daily pipeline

### Option 2: Manual Daily Downloads  
Run `daily_fire_update.py` manually each day for 12 days to catch up:
```bash
python3 scripts/daily_fire_update.py --days 5
```

### Option 3: Wait for Better Proxy/API Access
Re-attempt backfill when:
- More stable proxies are available
- FIRMS API is directly accessible
- Or use VPN/different network

### Option 4: Use Raw Archive Data (Requires Disk Cleanup)
1. Free up 2GB disk space
2. Download and extract raw_viirs archive
3. Run import and rebuild

## Files Updated

- `scripts/fire_nrt/config.py` - Updated with 2 working proxies
- `logs/fire_nrt_backfill_20260224_0743.log` - Backfill attempt log

## Next Steps

1. **Let the backfill process complete** (if still running)
2. **Check results:** Count actual fires downloaded
3. **If no fires found:** Investigate if NRT data exists for Dec 27 - Feb 24 period
4. **Fall back to daily cron:** Let it handle incremental updates going forward

## Monitoring

Check if backfill is still running:
```bash
ps aux | grep download_nrt.py
tail -f logs/fire_nrt_backfill_20260224_0743.log
```

Check downloaded data:
```bash
ls -lh data/fire_nrt/  # NRT downloads directory
```

---
**Status:** ⚠️  Backfill in progress but encountering proxy errors
**Recommendation:** Rely on daily cron job for incremental updates
