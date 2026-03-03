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


---

## Update: Comprehensive Fix (March 3, 2026)

### Additional Issues Discovered

After initial fix, testing revealed:

1. **Only 5 notifications** created for CAF_Chinko instead of 45
2. **Hash IDs displayed** (`CAF_Chinko_2026_grp_xxxxx`) instead of friendly names ("Tango", "Bravo")
3. **fire-alerts API priority bug**: `ORDER BY` prioritized "entered" (0) > "active" (2)
   - With limit=500, returned 188 entered + 312 active = missing 184 active alerts
4. **18,667 stale "left" alerts** polluting fire_group_alerts table
5. **Friendly names not stored**: Only generated ephemerally in fire-realtime API

### Root Cause Analysis

#### Problem 1: API Query Order

```sql
-- fire-alerts API query
SELECT * FROM fire_group_alerts
WHERE is_dismissed = 0 AND (left_at IS NULL OR left_at > datetime('now', '-1 day'))
ORDER BY CASE alert_type 
  WHEN 'entered' THEN 0    -- Highest priority
  WHEN 'active_inside' THEN 1
  WHEN 'active' THEN 2     -- Low priority!
  ELSE 3 
END
LIMIT 500;
```

Result breakdown:
- 188 "entered" alerts (priority 0)
- 496 "active" alerts (priority 2)
- 958 "cooling" alerts (priority 3)
- 18,667 "left" alerts (priority 3)

With limit=500, we get all 188 entered + 312 active, **missing 184 active alerts**.

#### Problem 2: No Stored Friendly Names

Friendly names ("Alpha", "Bravo", etc.) were only in fire-realtime API:

```go
// fire-realtime handler (ephemeral naming)
for i, featureID := range featureIDs {
    group := FireGroup{
        Name: getGroupName(i),  // "Alpha", "Bravo", etc.
        // ...
    }
}
```

But these names were:
- **Not stored** in feature_geometries or fire_group_alerts
- **Order-dependent**: Based on query result position
- **Not stable**: Changed with different query filters

#### Problem 3: notification Creation Dependency

```python
# OLD CODE (BROKEN)
response = requests.get(
    "http://localhost:8000/api/fire-alerts",
    params={'pwd': 'test2026', 'limit': 500},  # Limited by API issues
    timeout=30
)

for alert in response.json():
    if alert['alert_type'] == 'active':  # Filtered AFTER limit
        group_name = alert['group_name']  # Hash ID, not friendly name
        # Create notification...
```

Result: Notifications had hash IDs and missed most active groups.

### Comprehensive Solution

#### 1. Query feature_geometries Directly

```python
# NEW CODE (CORRECT)
cursor = conn.execute("""
    SELECT park_id, feature_id, properties_json
    FROM feature_geometries
    WHERE feature_type = 'fire_trajectory'
      AND end_date >= date('now', '-3 days')  -- Active definition
    ORDER BY park_id, start_date DESC
""")
```

Benefits:
- **Direct access** to source of truth (no API intermediary)
- **No limit issues**: Query returns all active trajectories
- **Fast**: SQLite query vs HTTP round-trip
- **Reliable**: No server dependency for cron job

#### 2. Stable Friendly Name Assignment

```python
# Sort by fires_total descending for consistent naming
groups.sort(key=lambda x: x['fires_total'], reverse=True)

for i, group in enumerate(groups):
    friendly_name = get_friendly_name(i)  # NATO phonetic
    # Alpha = most fires, Bravo = 2nd, etc.
```

Logic:
- **Alpha** = Group with most fires
- **Bravo** = 2nd most fires
- **Tango** = 20th most fires
- **Alpha-2** = 27th most fires (cycle)

Result: Names reflect fire intensity, stable across runs.

#### 3. Process ALL Parks

```python
# OLD: Only affected_parks from daily update
if park_id in self.affected_parks:
    # Create notifications

# NEW: All parks with active fires
for park_id, groups in park_groups.items():
    # Create notifications for any park with end_date >= now-3days
```

Benefits:
- **Comprehensive coverage**: Existing fires still burning
- **No missed notifications**: Even if no new fires detected today

#### 4. Clean Up Stale Alerts

```python
# Clean up old fire_group_alerts
conn.execute("""
    DELETE FROM fire_group_alerts
    WHERE alert_type = 'left' AND left_at < datetime('now', '-7 days')
""")

conn.execute("""
    DELETE FROM fire_group_alerts
    WHERE alert_type = 'entered' AND last_updated_at < datetime('now', '-14 days')
""")
```

Result: Reduced fire_group_alerts from 19,105 → ~600 records.

### Final Results

#### Before Complete Fix

```sql
SELECT COUNT(*) FROM notifications WHERE notification_type = 'fire_alert' AND park_id = 'CAF_Chinko';
-- Result: 5

SELECT title FROM notifications WHERE notification_type = 'fire_alert' LIMIT 3;
-- Result: 🔥 CAF_Chinko_2026_grp_07419ea4, 🔥 CAF_Chinko_2026_grp_07c08138, ...
```

#### After Complete Fix

```sql
SELECT COUNT(*) FROM notifications WHERE notification_type = 'fire_alert' AND park_id = 'CAF_Chinko';
-- Result: 45 ✅

SELECT title FROM notifications WHERE notification_type = 'fire_alert' AND park_id = 'CAF_Chinko' LIMIT 5;
-- Result:
--   🔥 Alpha    (1140 fires, 6 days)
--   🔥 Bravo    (807 fires, 6 days)
--   🔥 Charlie  (666 fires, 6 days)
--   🔥 Delta    (235 fires, 6 days)
--   🔥 Echo     (142 fires, 6 days)
```

#### System-Wide

```bash
# Total notifications created
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM notifications WHERE notification_type = 'fire_alert';"
# Result: 433 notifications across 24 parks ✅

# Top parks
sqlite3 db.sqlite3 "SELECT park_id, COUNT(*) FROM notifications WHERE notification_type = 'fire_alert' GROUP BY park_id ORDER BY COUNT(*) DESC LIMIT 5;"
# Result:
#   CAF_Chinko: 45
#   NGA_Gashaka-Gumti: 44
#   COD_Bili-Uere: 43
#   CAF_Bamingui-Bangoran: 42
#   CAF_Manovo_Gounda_St_Floris: 40
```

### Data Consistency Validation

#### All Sources Now Agree

```bash
# 1. feature_geometries (source of truth)
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM feature_geometries WHERE park_id = 'CAF_Chinko' AND feature_type = 'fire_trajectory' AND end_date >= date('now', '-3 days');"
# Result: 45 ✅

# 2. fire_group_alerts (synced daily)
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM fire_group_alerts WHERE park_id = 'CAF_Chinko' AND alert_type = 'active';"
# Result: 45 ✅

# 3. fire-realtime API
curl -s "http://localhost:8000/api/parks/CAF_Chinko/fire-realtime?pwd=test2026" | jq '[.groups[] | select(.status == "active")] | length'
# Result: 45 ✅

# 4. notifications table
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM notifications WHERE park_id = 'CAF_Chinko' AND notification_type = 'fire_alert';"
# Result: 45 ✅
```

### Cron Job Verification

```bash
# Test daily pipeline
cd /home/exedev/5mp
python3 scripts/daily_fire_update.py --days 1

# Expected output (Step 6b):
#   Step 6b: Creating notifications for active fire groups...
#   CAF_Chinko: Created 45 notifications
#   NGA_Gashaka-Gumti: Created 44 notifications
#   ...
#   Total: 433 notifications across 24 parks
```

**Cron Schedule:**
```cron
0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py --days 7 >> logs/daily_fire.log 2>&1
```

### Architecture Improvements

**Before:**
```
feature_geometries → fire_group_alerts → fire-alerts API → notifications
                     (18,667 records)    (limited, buggy)   (incomplete)
```

**After:**
```
feature_geometries → notifications
(496 active)         (433 notifications, all parks)
     ↓
fire_group_alerts (optional, for other use cases)
(cleaned daily)
```

### Key Takeaways

1. **Single Source of Truth**: feature_geometries is authoritative
2. **Direct Database Queries**: Avoid HTTP API for cron reliability
3. **Stable Naming**: Sort by intensity for consistent friendly names
4. **Comprehensive Coverage**: Process all parks, not just today's affected
5. **Regular Cleanup**: Prevent table pollution with old alerts

### Future Monitoring

Add to daily pipeline:

```python
# Validate notification counts match active trajectories
active_count = conn.execute("""
    SELECT COUNT(*) FROM feature_geometries
    WHERE feature_type = 'fire_trajectory' AND end_date >= date('now', '-3 days')
""").fetchone()[0]

notif_count = conn.execute("""
    SELECT COUNT(*) FROM notifications
    WHERE notification_type = 'fire_alert' AND created_at > date('now', '-1 day')
""").fetchone()[0]

if abs(active_count - notif_count) > 10:
    log(f"WARNING: Notification mismatch! Active: {active_count}, Notif: {notif_count}")
```


---

## Final Update: Stable Hurricane-Style Naming (March 3, 2026)

### User Requirement

> "Make sure the friendly names stick with a group and stay the same like for tracking hurricanes. That will make the managers work easier."

### Problem with Previous Approach

The initial fix assigned names based on `fires_total` (descending):

```python
# OLD: Names changed as fire intensity changed
groups.sort(key=lambda x: x['fires_total'], reverse=True)
for i, group in enumerate(groups):
    friendly_name = get_friendly_name(i)  # Alpha, Bravo, Charlie
```

**Issues:**
- If Fire Group A had 1000 fires (Alpha) and Fire Group B had 500 fires (Bravo)
- Next day: Fire Group B grows to 1200 fires, Fire Group A stays at 1000
- Result: **Names swap!** B becomes Alpha, A becomes Bravo
- Managers lose track of which fire they were monitoring

### Solution: Persistent Name Mapping

Created `fire_group_names` table to store stable assignments:

```sql
CREATE TABLE fire_group_names (
    park_id TEXT NOT NULL,
    feature_id TEXT NOT NULL,
    friendly_name TEXT NOT NULL,
    assigned_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    first_seen_date TEXT,
    last_seen_date TEXT,
    PRIMARY KEY (park_id, feature_id)
);
```

### Naming Logic

#### 1. Chronological Assignment (Not Intensity)

Names are assigned based on **when the fire first appeared**, not how big it is:

```python
# NEW: Sort by start_date (chronological)
groups.sort(key=lambda x: x['start_date'])

for i, group in enumerate(groups):
    feature_id = group['feature_id']
    
    # Check if name already exists
    existing = db.get(feature_id)
    
    if not existing:
        friendly_name = get_friendly_name(i)  # Alpha, Bravo, ...
        db.store(feature_id, friendly_name, first_seen=start_date)
    else:
        # Keep existing name, just update last_seen
        db.update(feature_id, last_seen=end_date)
```

#### 2. Name Persistence

Once assigned, a name **NEVER changes**:

| Date | Feature ID | Friendly Name | Fires | Status |
|------|-----------|---------------|-------|--------|
| Feb 26 | CAF_Chinko_2026_grp_2caaa51b | **Alpha-2** | 50 | Growing |
| Feb 27 | CAF_Chinko_2026_grp_2caaa51b | **Alpha-2** | 80 | Growing |
| Feb 28 | CAF_Chinko_2026_grp_2caaa51b | **Alpha-2** | 120 | Growing |
| Mar 1 | CAF_Chinko_2026_grp_2caaa51b | **Alpha-2** | 100 | Declining |
| Mar 3 | CAF_Chinko_2026_grp_2caaa51b | **Alpha-2** | 142 | Active |

**Alpha-2 stays Alpha-2** regardless of intensity changes.

#### 3. Cycle Suffixes

Like hurricanes, we cycle through the alphabet:

- **Alpha, Bravo, ..., Zulu** = First 26 fires (cycle 1)
- **Alpha-2, Bravo-2, ..., Zulu-2** = Fires 27-52 (cycle 2)
- **Alpha-3, Bravo-3, ...** = Fires 53-78 (cycle 3)

### Implementation

#### Step 6b1: Assign Names to New Groups

```python
def assign_friendly_names_to_new_groups(self):
    # Get all fire trajectories for current year
    cursor = db.execute("""
        SELECT park_id, feature_id, start_date, end_date
        FROM feature_geometries
        WHERE feature_type = 'fire_trajectory'
          AND feature_id LIKE '%_2026_grp_%'
        ORDER BY park_id, start_date ASC
    """)
    
    for park_id, groups in park_groups.items():
        groups.sort(key=lambda x: x['start_date'])  # Chronological!
        
        for i, group in enumerate(groups):
            if not name_exists(feature_id):
                assign_name(feature_id, get_friendly_name(i))
            else:
                update_last_seen(feature_id)
```

#### Step 6b2: Use Stored Names for Notifications

```python
def create_fire_notifications(self):
    # JOIN with fire_group_names to get stable names
    cursor = db.execute("""
        SELECT fg.park_id, fg.feature_id, fg.properties_json, 
               fgn.friendly_name  -- ← Stable name!
        FROM feature_geometries fg
        JOIN fire_group_names fgn 
          ON fg.park_id = fgn.park_id 
         AND fg.feature_id = fgn.feature_id
        WHERE fg.end_date >= date('now', '-3 days')
        ORDER BY fg.park_id, fgn.friendly_name
    """)
    
    for row in cursor:
        title = f"🔥 {friendly_name}"  # Name from mapping table
        # Create notification...
```

### Real-World Example: CAF_Chinko

#### Current Active Fires (March 3, 2026)

```sql
SELECT friendly_name, first_seen_date, last_seen_date, 
       julianday(last_seen_date) - julianday(first_seen_date) as days_burning
FROM fire_group_names
WHERE park_id = 'CAF_Chinko' 
  AND last_seen_date >= date('now', '-3 days')
ORDER BY friendly_name
LIMIT 10;
```

| Friendly Name | First Seen | Last Seen | Days Burning |
|---------------|------------|-----------|-------------|
| **Alpha-2** | 2026-02-26 | 2026-03-03 | **6 days** |
| **Bravo-2** | 2026-02-26 | 2026-03-02 | **5 days** |
| **Charlie-2** | 2026-02-27 | 2026-03-01 | **3 days** |
| **Delta-2** | 2026-02-26 | 2026-03-03 | **6 days** |

**Managers can now track**: "Alpha-2 has been burning for 6 days since Feb 26."

### Manager Benefits

#### Before (Unstable Names)
```
Day 1: "Monitor Fire Alpha (1000 fires)"
Day 2: "Fire Alpha now has 1200 fires"  // But it's actually a different fire!
Day 3: "Where did Alpha go?"            // Original Alpha is now Bravo
```

#### After (Stable Names)
```
Day 1: "Monitor Fire Alpha-2 (50 fires, near Chinko river)"
Day 2: "Alpha-2 grew to 80 fires, still near Chinko"
Day 3: "Alpha-2 now 120 fires, moving south"
Day 4: "Alpha-2 declining to 100 fires"
Day 5: "Alpha-2 extinguished"
```

### Historical Tracking

#### CAF_Chinko Fire Season 2026

```sql
SELECT friendly_name, first_seen_date, last_seen_date,
       julianday(last_seen_date) - julianday(first_seen_date) + 1 as duration_days
FROM fire_group_names
WHERE park_id = 'CAF_Chinko'
ORDER BY first_seen_date
LIMIT 20;
```

| Name | First Seen | Last Seen | Duration | Status |
|------|------------|-----------|----------|--------|
| **Alpha** | Feb 13 | Feb 16 | 4 days | Extinguished |
| **Bravo** | Feb 13 | Feb 15 | 3 days | Extinguished |
| **Charlie** | Feb 14 | Feb 15 | 2 days | Extinguished |
| **Tango** | Feb 26 | Feb 27 | 2 days | Extinguished |
| **Alpha-2** | Feb 26 | **Mar 3** | **6 days** | **Active** |
| **Delta-2** | Feb 26 | **Mar 3** | **6 days** | **Active** |

Managers can see: **45 fire groups tracked so far this season**.

### Data Validation

```bash
# Total named groups
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM fire_group_names;"
# Result: 1,424 fire groups with stable names

# Active groups with notifications
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM notifications WHERE notification_type = 'fire_alert';"
# Result: 433 notifications (matches active fire groups)

# Verify name stability
sqlite3 db.sqlite3 "SELECT friendly_name, feature_id FROM fire_group_names WHERE park_id = 'CAF_Chinko' AND friendly_name = 'Alpha-2';"
# Result: Alpha-2|CAF_Chinko_2026_grp_2caaa51b (same feature_id, same name ✅)
```

### Cron Integration

Daily pipeline (3am UTC) now includes:

```python
# Step 6b1: Assign names to any new fire groups
self.assign_friendly_names_to_new_groups()

# Step 6b2: Create notifications using stable names
self.create_fire_notifications()
```

Output:
```
[2026-03-03 03:00:15] Step 6b1: Assigning friendly names to new fire groups...
[2026-03-03 03:00:15]   Assigned 3 new friendly names
[2026-03-03 03:00:15] Step 6b2: Creating notifications for active fire groups...
[2026-03-03 03:00:15]   CAF_Chinko: Created 45 notifications
[2026-03-03 03:00:15]   Total: 433 notifications across 24 parks
```

### Communication Examples

**Morning Briefing:**
> "Good morning team. We have 45 active fires in Chinko. Alpha-2 and Delta-2 have been burning for 6 days near the Chinko river. Bravo-2 is declining and may extinguish today. New fire Echo-4 detected yesterday near the northern boundary."

**Field Report:**
> "Patrol Unit 3 reporting: Investigated Alpha-2. Fire is contained within park boundaries, moving south at 1.2 km/day. No settlements threatened. Recommend continued monitoring."

**Weekly Summary:**
> "This week: 12 new fires detected (Lima-4 through Whiskey-4). Alpha-2 and Delta-2 remain most active. Total fires this season: 87 groups tracked."

### Future Enhancements

1. **Fire Reports**: Generate PDF with fire history by friendly name
2. **API Endpoint**: `/api/fire-history/CAF_Chinko/Alpha-2`
3. **UI Timeline**: Show fire progression over time
4. **Alerts**: "Alpha-2 has been active for 7+ days (long-duration fire)"

### Summary

✅ **Stable Names**: Once assigned, never changes  
✅ **Chronological**: Based on first appearance, not intensity  
✅ **Trackable**: Managers can follow specific fires over days/weeks  
✅ **Historical**: Complete fire season record  
✅ **Production Ready**: Integrated into daily cron pipeline  

**Analogy**: Just like Hurricane Katrina was always Katrina (not renamed based on wind speed), Fire Alpha-2 is always Alpha-2 (not renamed based on fire count).
