# Incident Report: Fire Groups Lost in Incremental Load

**Date:** March 3, 2026  
**Severity:** High - Data loss  
**Status:** ✅ Resolved  

---

## Summary

After the 3am UTC cron run on March 3, 2026, fire trajectory groups were missing from the database. For example, Chinko showed only 76 groups in the UI instead of the expected 2531 groups.

---

## Timeline

- **03:00 UTC** - Daily fire cron job started
- **03:08 UTC** - rebuild_fire_trajectories_v5.py generated 2531 groups for Chinko (correct)
- **03:27 UTC** - load_fire_groups_to_db.py loaded fire groups (incorrect)
- **03:28 UTC** - Pipeline completed
- **~06:00 UTC** - Issue discovered: Chinko showing only 76 groups in UI
- **06:54 UTC** - Issue diagnosed and fixed

---

## Root Cause

**Double-filtering bug** in incremental mode:

### Step 1: rebuild_fire_trajectories_v5.py --incremental (CORRECT)
```python
# Reads fires from last 14 days
fires = db.query("WHERE acq_date >= cutoff")

# Clusters into new groups
new_groups = cluster_fires(fires)

# Merges with existing JSON file
if incremental:
    old_groups = load_json()
    keep_old = [g for g in old_groups if g['end_date'] < cutoff]
    groups = keep_old + new_groups  # ✓ Old preserved, recent replaced
    
# Writes merged result to JSON
save_json(groups)  # File now has 2531 groups
```

### Step 2: load_fire_groups_to_db.py --incremental (INCORRECT)
```python
# Reads JSON file (has 2531 groups)
groups = load_json()

# Deletes all existing trajectories for park
db.execute("DELETE FROM feature_geometries WHERE park_id = ?")

for group in groups:
    # ❌ BUG: Filters out old groups AGAIN
    if incremental and group['end_date'] < cutoff:
        continue  # Skips groups older than 14 days
    
    db.insert(group)

# Result: Only 76 recent groups loaded to DB
```

---

## Impact

**Affected Parks:** 50 parks processed in the morning cron run

**Data Loss:**
- Historical fire trajectories (>14 days old) were deleted from database
- JSON files were correct (old + new groups preserved)
- Only database was affected

**User Impact:**
- UI showed significantly fewer fire groups
- Historical fire data appeared to be lost
- Notifications still generated correctly (26 for Chinko)

---

## Fix

### Code Change
```diff
- ['python3', 'scripts/load_fire_groups_to_db.py', '--park', park_id, '--incremental', '--days', '14']
+ ['python3', 'scripts/load_fire_groups_to_db.py', '--park', park_id, '--force']
```

**Rationale:**  
The JSON file already contains the correct merged dataset (old + new groups). The loader should load ALL groups without additional filtering.

### Recovery
Manually reloaded all 50 affected parks:
```bash
for park in <affected_parks>; do
    python3 scripts/load_fire_groups_to_db.py --park "$park" --force
done
```

**Result:**  
- Chinko: 2515 fire trajectories restored (was 76)
- All other parks restored similarly

---

## Verification

```sql
-- Before fix
SELECT COUNT(*) FROM feature_geometries 
WHERE park_id='CAF_Chinko' AND feature_type='fire_trajectory';
-- Result: 79

-- After fix
SELECT COUNT(*) FROM feature_geometries 
WHERE park_id='CAF_Chinko' AND feature_type='fire_trajectory';
-- Result: 2515

-- Compare with JSON file
$ python3 -c "import json; print(len(json.load(open('data/fire_groups_v5/CAF_Chinko.json'))))"
-- Result: 2531 (slightly higher due to invalid coords filtered during load)
```

---

## Prevention

### Why This Bug Wasn't Caught Earlier

1. **First incremental run:** This was the first time the incremental logic ran on a database with significant historical data
2. **Testing gap:** Incremental mode was tested with fresh databases, not with existing historical data
3. **Silent failure:** The script ran successfully with no errors, just loaded fewer records

### Safeguards Added

1. **Fixed:** Removed --incremental from load step
2. **Documentation:** Updated docs/FIRE_DATA_FLOW.md with clear explanation
3. **Testing:** Tomorrow's cron run will verify the fix works correctly

### Future Improvements

1. Add validation checks: Compare JSON record count with DB insert count
2. Add alerts for significant drops in trajectory counts
3. Consider adding a dry-run mode for incremental updates

---

## Related Files

- `scripts/daily_fire_update.py` - Main pipeline
- `scripts/rebuild_fire_trajectories_v5.py` - Group builder (correct)
- `scripts/load_fire_groups_to_db.py` - DB loader (had bug)
- `docs/FIRE_DATA_FLOW.md` - Pipeline documentation

---

## Commit

**Commit:** 6890c529  
**Message:** "Fix incremental fire group loading - double-filter bug"  
**Files Changed:** scripts/daily_fire_update.py (1 line)

---

## Lessons Learned

1. **Be careful with incremental logic at multiple stages** - Only one stage should decide what's old vs new
2. **Test with realistic data** - Incremental mode needs testing with existing historical data
3. **Add validation** - Compare expected vs actual record counts
4. **Silent failures are dangerous** - Exit code 0 doesn't mean data integrity is preserved
