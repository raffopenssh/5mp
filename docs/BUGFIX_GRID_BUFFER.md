# Bug Fix: Grid Cell Buffer Calculation

## Issue

Aouk (TCD_Aouk) was showing **26 active pixels** in Excel exports, despite having no patrol data within its boundaries.

## Root Cause

**The code was using a bounding box buffer instead of a proper geometric polygon buffer.**

When calculating which grid cells fall within 30km of a park:

1. The code extracted the park's polygon geometry
2. Calculated a **rectangular bounding box** around the polygon (min/max lat/lon)
3. Added 30km buffer to this **rectangle**
4. Queried all grid cells within the buffered rectangle

### Problem with Aouk

Aouk is an elongated, diagonally-oriented park:
- Actual geometry bounds: lon [18.39°, 21.04°], lat [8.78°, 11.04°]
- Bounding box size: **294 km × 251 km**
- Orientation: Southwest to Northeast diagonal

When you add a 30km buffer to this large rectangle, you get a massive area that includes regions far outside the actual park:

```
     [Northwest corner - OUTSIDE PARK]
              |
              |  <-- These grid cells are 75-116 km
              |      from the actual park boundary!
              |
         [Actual Aouk polygon - diagonal strip]
              |
              |
     [Southeast corner]
```

## Verification

Tested the 26 grid cells that were being counted:
- **Closest distance**: 75.4 km from park boundary
- **Farthest distance**: 116.9 km from park boundary
- **All 26 cells**: Outside the 30km buffer (> 30 km away)

## Fix

Added proper geometric filtering:

1. Still uses bounding box for initial database query (efficient)
2. **Added post-filter**: `isPointNearPolygon()` function that:
   - Checks if grid cell center is inside the polygon (distance = 0)
   - If outside, calculates actual distance to nearest polygon edge
   - Only includes cells within true 30km buffer

### New Helper Function

```javascript
function isPointNearPolygon(lng, lat, geom, bufferDeg) {
    // 1. Quick check: is point inside polygon?
    // 2. If outside: calculate minimum distance to any polygon edge
    // 3. Return true only if distance <= bufferDeg
}
```

## Results

**Before fix:**
- Aouk: 26 active pixels (all 75-116km away!)
- Other elongated parks likely affected similarly

**After fix:**
- Aouk: **0 active pixels** (correct - no patrol data within 30km)
- Only cells truly within 30km buffer are counted

## Impact

This fix affects:
- Star report Excel/CSV/KML exports
- Any park with an elongated or irregular shape
- Grid cell counts in the "Active Pixels" column

**The fix ensures accurate reporting of patrol coverage area.**

## Files Changed

- `srv/templates/globe.html`:
  - Modified `fetchGridDataDirect()` to filter grid cells
  - Added `isPointNearPolygon()` helper function

## Commit

```
commit 1bbdcf0a
Fix: Use proper polygon buffer instead of bounding box buffer for grid cells
```
