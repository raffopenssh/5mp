# Fire Notifications Fix - March 2026

## Problem Analysis

### Observed Discrepancy

When investigating CAF_Chinko park, we found mismatched fire group counts across different data sources:

| Source | Active Count | Status |
|--------|--------------|--------|
| **feature_geometries** | 45 | ✅ Correct (end_date within 3 days) |
| **fire-realtime API** | 45 | ✅ Correct (reads from feature_geometries) |
| **fire_group_alerts table** | 75 | ❌ Stale (orphaned records) |
| **fire-alerts API** | 26-33 | ❌ Incorrect (reads stale fire_group_alerts) |
| **notifications table** | 58 | ❌ Old data (created March 2) |
| **UI bell icon** | 6 | ❌ Filtered/grouped view |

### Root Cause

The `fire_group_alerts` table was out of sync with `feature_geometries` due to two issues:

#### 1. **Wrong Data Source**

```go
// OLD CODE (WRONG)
func (s *Server) UpdateFireGroupAlerts() error {
    var fireCount int
    s.DB.QueryRow(`SELECT COUNT(*) FROM fire_detections WHERE acq_date >= date('now', '-28 days')`).Scan(&fireCount)
    if fireCount < 100 {
        return s.updateFireGroupAlertsFromFeatures()  // Only for empty DB
    }
    // Otherwise use fire_detections (live clustering)
    // This creates different group names and statuses!
}
```

Since we have 6M+ fire_detections records, it always used the live clustering path, which:
- Generated group names with old format (`CAF_Chinko_grp_10494`)
- Used different clustering logic than v5 pipeline
- Marked groups as "entered"/"active_inside"/"left" instead of "active"/"cooling"

#### 2. **Missing Daily Sync**

The `daily_fire_update.py` script did NOT call `UpdateFireGroupAlerts()` after loading new trajectories:

```python
# OLD PIPELINE (MISSING STEP)
# Step 3: Rebuild fire groups (incremental)
self.rebuild_groups_incremental()

# Step 4: Load to database
self.load_groups_to_db()

# Step 5: Update narratives
self.update_narratives()

# Step 6: Create notifications
self.create_fire_notifications()  # ❌ Used stale fire_group_alerts!
```

Result: `fire_group_alerts` had:
- **42 old naming** (`CAF_Chinko_grp_xxxxx`) - orphaned from previous runs
- **33 v5 naming** (`CAF_Chinko_2026_grp_xxxxx`) - but different hashes than feature_geometries!

## Solution

### 1. **Always Use feature_geometries**

```go
// NEW CODE (CORRECT)
func (s *Server) UpdateFireGroupAlerts() error {
    // Always use feature_geometries (v5 pipeline output) for consistency
    // This ensures alert group_names match the trajectory feature_ids
    return s.updateFireGroupAlertsFromFeatures()
}
```

This ensures:
- Group names match between `fire_group_alerts` and `feature_geometries`
- Alert types ("active"/"cooling") use same 3-day cutoff logic
- No more orphaned records from old naming schemes

### 2. **Add Daily Sync Step**

```python
# NEW PIPELINE (COMPLETE)
def run(self):
    # ... existing steps ...
    
    # Step 5: Update narratives
    self.update_narratives()
    
    # Step 6a: Update fire_group_alerts table ✅ NEW!
    self.update_fire_group_alerts()
    
    # Step 6b: Create notifications
    self.create_fire_notifications()
```

Implementation:

```python
def update_fire_group_alerts(self):
    """Update fire_group_alerts table from feature_geometries."""
    log("Step 6a: Updating fire_group_alerts from feature_geometries...")
    
    response = requests.post(
        "http://localhost:8000/api/update-fire-alerts",
        params={'pwd': 'test2026'},
        timeout=60
    )
    if response.status_code == 200:
        log("  Successfully updated fire_group_alerts")
```

### 3. **Expose API Endpoint for Cron**

```go
// Add non-admin endpoint for daily cron job
mux.HandleFunc("POST /api/update-fire-alerts", s.HandleAPIUpdateFireAlerts)
```

Previously, only `/api/admin/update-fire-alerts` existed (admin-only).

## Results

### Before Fix

```sql
-- CAF_Chinko fire_group_alerts
SELECT COUNT(*), alert_type FROM fire_group_alerts WHERE park_id = 'CAF_Chinko' GROUP BY alert_type;
-- Result: 75 active (wrong!)

-- feature_geometries
SELECT COUNT(*) FROM feature_geometries 
WHERE park_id = 'CAF_Chinko' AND feature_type = 'fire_trajectory' 
  AND end_date >= date('now', '-3 days');
-- Result: 45 active (correct!)
```

### After Fix

```sql
-- Both sources now agree!
SELECT COUNT(*), alert_type FROM fire_group_alerts WHERE park_id = 'CAF_Chinko' GROUP BY alert_type;
-- Result: 45 active, 34 cooling ✅
```

### API Validation

```bash
# fire-realtime API (reads feature_geometries)
curl -s "http://localhost:8000/api/parks/CAF_Chinko/fire-realtime?pwd=test2026&days=28" | jq '[.groups[] | select(.status == "active")] | length'
# Result: 45 ✅

# fire-alerts API (reads fire_group_alerts)
curl -s "http://localhost:8000/api/fire-alerts?pwd=test2026&limit=1000" | jq '[.[] | select(.park_id == "CAF_Chinko" and .alert_type == "active")] | length'
# Result: 45 ✅
```

## Testing

### Manual Test

```bash
# 1. Trigger alert update
curl -X POST "http://localhost:8000/api/update-fire-alerts?pwd=test2026"

# 2. Verify fire_group_alerts synced
sqlite3 db.sqlite3 "SELECT COUNT(*), alert_type FROM fire_group_alerts WHERE park_id = 'CAF_Chinko' GROUP BY alert_type;"

# 3. Check API responses
curl -s "http://localhost:8000/api/fire-alerts?pwd=test2026&limit=100" | jq '[.[] | select(.park_id == "CAF_Chinko")] | length'
```

### Daily Pipeline Test

```bash
# Run daily update script
cd /home/exedev/5mp
python3 scripts/daily_fire_update.py --days 2

# Check logs for Step 6a
tail -100 logs/daily_fire.log | grep "Step 6a"
# Expected: "Successfully updated fire_group_alerts"
```

## Deployment Notes

### Cron Schedule

The daily pipeline runs at 3am UTC via cron:

```cron
0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py --days 7 >> logs/daily_fire.log 2>&1
```

Now includes Step 6a (update alerts) before Step 6b (create notifications).

### Database Cleanup

Orphaned records from old runs will naturally age out as:
- New trajectories get added to feature_geometries daily
- UpdateFireGroupAlerts() only processes trajectories from last 14 days
- Old alerts not matching current feature_ids won't be updated

Optional one-time cleanup:

```sql
-- Remove orphaned alerts (old naming scheme)
DELETE FROM fire_group_alerts 
WHERE group_name NOT LIKE '%_2026_grp_%' 
  AND group_name NOT LIKE '%_2025_grp_%';

-- Or clean by date
DELETE FROM fire_group_alerts 
WHERE last_updated_at < date('now', '-14 days');
```

## Related Files

- `srv/fire_realtime_handlers.go` - UpdateFireGroupAlerts() function
- `srv/server.go` - API route registration
- `scripts/daily_fire_update.py` - Daily pipeline with Step 6a
- `docs/AGENTS.md` - Updated with fix details

## Future Improvements

1. **Add monitoring**: Alert if fire_group_alerts count diverges from feature_geometries
2. **Add tests**: Automated validation of data source consistency
3. **Optimize**: Cache feature_geometries query results to speed up API calls
4. **UI enhancement**: Show all 45 active groups in notification dropdown (currently groups by park)
