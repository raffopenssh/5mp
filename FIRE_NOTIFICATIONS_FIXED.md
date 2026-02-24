# Fire Notifications - Fixed 2026-02-24

## ✅ Problem Solved

**Issue:** Notifications were being created for ALL fire groups instead of only ACTIVE groups.

**Solution:** Daily cron now integrates with existing `fire_group_alerts` system.

## How Fire Alerts Work

### 1. Fire Group Alerts Table
```sql
fire_group_alerts
├─ Populated by: /api/parks/{id}/fire-realtime endpoint
├─ Alert types: 'entered', 'active_inside', 'left', 'cooling'
├─ Active = alert_type IN ('active_inside', 'entered')
└─ Auto-cleanup: left_at groups deleted after 1 day
```

### 2. Daily Cron Flow

```
Step 6: Fire Notifications
  ├─ For each affected park:
  │   ├─ Call /api/parks/{id}/fire-realtime?days=28
  │   │   └─ This updates fire_group_alerts table
  │   ├─ Get active_groups_count from response
  │   └─ If active_groups_count > 0:
  │       ├─ Check for existing notification (last 7 days)
  │       └─ Create notification if none exists
  └─ Result: Bell notifications only for ACTIVE fires
```

### 3. Notification Format

**Type:** `fire_alert`  
**Title:** "Active Fire Alert: {Park Name}"  
**Message:** "X active fire groups currently burning"

### 4. UI Integration

- **Fire Realtime API** populates `fire_group_alerts` with NATO names (Alpha, Bravo, etc.)
- **Active Groups** shown in fire popup with movement direction
- **Bell Icon** shows notifications for parks with active groups
- **No duplicates:** Checks for notifications within 7 days before creating new one

## Testing

```bash
# Simulate daily fire update
python3 scripts/daily_fire_update.py --days 5

# Check fire_group_alerts
sqlite3 db.sqlite3 "SELECT park_id, group_name, alert_type, fire_count, days_active 
FROM fire_group_alerts 
WHERE left_at IS NULL 
ORDER BY last_updated_at DESC 
LIMIT 10"

# Check notifications created
sqlite3 db.sqlite3 "SELECT park_id, title, message, created_at 
FROM notifications 
WHERE notification_type='fire_alert' 
ORDER BY created_at DESC 
LIMIT 5"
```

## Key Difference

**Before:** Created notifications for all fire trajectory groups (historical)  
**After:** Only creates notifications for active groups currently burning (real-time)

This matches the UI behavior where "active groups" are highlighted separately from historical trajectory data.

---
**Status:** ✅ Fixed - Notifications now correctly show only active fires
**Commit:** a2b80369
