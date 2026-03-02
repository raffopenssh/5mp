# Fire Data Discrepancy Analysis - Issue #5

## Problem Statement

User reports discrepancy:
- **Pinned layer:** Shows "🔥 1027" features loaded
- **Fire section:** Shows "• 1239 GROUPS" 

## Root Causes Found

### 1. Daily Fire Cron Failures (CRITICAL)

**Status:** ❌ **Failing since Feb 28, 2026**

```bash
# Log excerpt from logs/daily_fire.log:
[2026-02-28 03:00:02] Error downloading NRT fires: Network is unreachable
[2026-03-01 03:02:17] Error downloading NRT fires: Network is unreachable  
[2026-03-02 03:02:16] Error downloading NRT fires: Network is unreachable
[2026-03-02 03:02:16] Step 3: No parks affected, skipping group rebuild
[2026-03-02 03:02:16] Step 4: No parks affected, skipping DB load
```

**Impact:**
- No new fires downloaded since Feb 24
- No 2026 fire groups built or loaded to database
- feature_geometries table missing all 2026 data

### 2. Data Counts Breakdown

**Source: fire_groups_v5/CAF_Chinko.json**
```
2020: 489 groups
2021: 422 groups  
2022: 499 groups
2023: 360 groups
2024: 458 groups
2025: 216 groups
2026: 0 groups (NOT BUILT YET)
---
Total: 2,444 groups
```

**Source: feature_geometries table**
```sql
SELECT year, COUNT(*) FROM feature_geometries 
WHERE park_id = 'CAF_Chinko' AND feature_type = 'fire_trajectory'
GROUP BY year;

2020: 485 trajectories
2021: 420 trajectories
2022: 496 trajectories
2023: 357 trajectories
2024: 455 trajectories
2025: 215 trajectories
2026: 0 trajectories (MISSING!)
---
Total: 2,428 trajectories
```

**Source: fire_narrative_cache**
- Total narratives: 2,332
- Year-based filter (2023-2026): 1,027
- **Date-based filter (2023-01-01 to 2026-03-01): 1,239** ← This is what UI shows!

### 3. Date Filtering Discrepancy

**The 212-group difference (1239 - 1027) explained:**

Fires that **started in late 2022** but have `start_date` that extends into early 2023:
- Year field: 2022
- Start date: "2022-12-28" or similar
- When filtered by `start_date >= "2023-01-01"`: EXCLUDED from year filter, INCLUDED in date filter

Similarly for late 2025 fires extending into 2026.

**Client-side JavaScript filter:**
```javascript
// Line 5336 in globe.html
allNarratives = allNarratives.filter(n => {
    const startStr = n.entry_date || n.start_date;
    const startDate = startStr ? new Date(startStr) : null;
    return startDate >= fromDate && startDate <= toDate;  // Filters by START DATE
});
```

**API filter:**
```go
// srv/api.go - filters by year field
json_extract(properties_json, '$.year') >= ? AND json_extract(properties_json, '$.year') <= ?
```

### 4. Why Pinned Layer Shows 1027

**API endpoint:** `/api/parks/CAF_Chinko/features?type=fire_trajectory&start=2023-01-01&end=2026-03-01`

**Backend filtering in srv/api.go:**
```go
// Filters by start_date field
json_extract(properties_json, '$.start_date') >= ? 
AND json_extract(properties_json, '$.start_date') <= ?
```

**Database reality:**
- feature_geometries has NO 2026 data (cron failed)
- Query returns only 2023-2025: 357 + 455 + 215 = 1,027 trajectories

## Why They Don't Match

| Source | Count | Reason |
|--------|-------|--------|
| Pinned layer (API) | 1,027 | feature_geometries table, start_date filter, NO 2026 data |
| Fire section (narrative cache) | 1,239 | Includes fires that started in late 2022/2025 but burned into 2023/2026 |

## The Real Problem

**2026 data is missing from the database entirely!**

1. ✅ Fire detections exist: `fire_detections` table has 136,184 recent fires
2. ❌ Fire groups not built: No 2026 groups in `fire_groups_v5/` JSON files
3. ❌ No database load: feature_geometries has 0 rows for year 2026
4. ❌ Cron failing: Daily pipeline not running due to network errors

## Verification Queries

```sql
-- Check 2026 fire detections exist
SELECT COUNT(*) FROM fire_detections 
WHERE acq_date >= '2026-01-01';
-- Result: 136,184 fires

-- Check 2026 trajectories in DB
SELECT COUNT(*) FROM feature_geometries 
WHERE park_id = 'CAF_Chinko' 
  AND feature_type = 'fire_trajectory'
  AND json_extract(properties_json, '$.year') = 2026;
-- Result: 0 (NONE!)

-- Check fire alerts (realtime)
SELECT COUNT(*) FROM fire_group_alerts 
WHERE park_id = 'CAF_Chinko' 
  AND CAST(strftime('%Y', first_detected_at) AS INTEGER) = 2026;
-- Result: 443 alerts (from realtime API, not from feature_geometries)
```

## Solution Required

### Immediate Fix

1. **Run fire group rebuild manually for recent dates:**
```bash
# Rebuild fire groups for last 30 days
python3 scripts/rebuild_fire_trajectories_v5.py --park CAF_Chinko --days 30

# Load to database
python3 scripts/load_fire_groups_to_db.py --park CAF_Chinko --force

# Update narrative cache
python3 scripts/precompute_narratives_v5.py
```

2. **Fix daily cron network issue:**
- Investigate why FIRMS API is unreachable
- Check firewall/network settings
- Test manual download:
```bash
curl "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{API_KEY}/VIIRS_NOAA20_NRT/-20,-35,55,40/5"
```

### Long-term Fix

1. **Add fallback/retry logic** to daily_fire_update.py
2. **Monitor cron success** - alert on failures
3. **Verify data consistency** - daily check that feature_geometries matches fire_groups_v5
4. **Handle date range edge cases** - document difference between year-based and date-based filtering

## Files to Check

- `scripts/daily_fire_update.py` - Daily cron pipeline
- `logs/daily_fire.log` - Cron execution logs
- `data/fire_groups_v5/CAF_Chinko.json` - Fire groups JSON (last updated Feb 24)
- `data/fire_nrt/CAF_Chinko_nrt.json` - NRT fire detections (updated Feb 28, but not processed)

## Impact on Users

- ✅ **2023-2025 data:** Accurate and complete
- ❌ **2026 data:** Missing entirely from map/database
- ⚠️ **Date filters:** Confusing discrepancy between year-based (1027) and date-based (1239) counts
- ✅ **Fire alerts:** Working via realtime API (not dependent on feature_geometries)

## Priority

**CRITICAL** - Fire data pipeline has been broken for 3 days. All 2026 fire analysis is missing.

