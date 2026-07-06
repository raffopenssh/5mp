# Bug Fix: Fire Notification Count Mismatch

## Problem
Notification dropdown showed "43 fire groups" for CAF_Chinko, but popup showed "Currently Active (38)".

## Root Cause
The fire realtime API had two issues:

### Issue 1: Incorrect `active_groups_count`
- `activeCount` variable was incremented during row scan for ALL active fire groups
- Then `groups` array was truncated to 100 items
- Some active groups (5 of 43) were removed by truncation
- But `ActiveGroupsCount` used the pre-truncation `activeCount` (43) instead of post-truncation `len(activeGroups)` (38)

### Issue 2: Non-deterministic truncation
- Sorting was by Priority → LastSeen
- Active groups weren't prioritized, so truncation could randomly remove active groups
- CAF_Chinko had 43 active groups but API only returned 38 after truncation

## Solution

### Fix 1: Use actual active group count
Changed `ActiveGroupsCount: activeCount` to `ActiveGroupsCount: len(activeGroups)`

This ensures the count reflects groups actually returned in the response.

### Fix 2: Prioritize active groups before truncation
Changed sort order to:
1. **IsActive** (active groups first)
2. Priority (within active/inactive)
3. LastSeen (within same priority)

This guarantees all active groups are retained when truncating to 100.

## Files Changed
- `srv/fire_realtime_handlers.go`

## Verification
```bash
curl "http://localhost:8000/api/parks/CAF_Chinko/fire-realtime?pwd=test2026&days=28" | \
  jq '{active_groups_count, active_groups_length: (.active_groups | length)}'
```

Before fix:
```json
{
  "active_groups_count": 43,
  "active_groups_length": 38
}
```

After fix:
```json
{
  "active_groups_count": 43,
  "active_groups_length": 43
}
```

## Impact
- Notification count (43) now matches popup count (43)
- All active fire groups are preserved in API responses
- No active groups lost to truncation
- Counts are consistent across API, notifications, and UI

## Commits
- `9f3b4491`: Fix active_groups_count calculation
- `a5086e2d`: Prioritize active groups before truncation
