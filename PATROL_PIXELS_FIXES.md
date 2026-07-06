# Patrol Pixel Fixes - 2026-03-05

## Changes Made

### 1. Removed Patrol_Summary Sheet
- Deleted redundant "Patrol_Summary" sheet from Excel export
- Patrol_Pixels sheet now provides all detailed information
- Reduces confusion and file size

### 2. Fixed Intensity Calculation
**Problem**: All pixels showed same intensity value, not reflecting actual visit frequency

**Root Cause**: Frontend was calculating intensity based only on month type (dry vs rainy), not on actual visit frequency per grid cell

**Solution**: 
- Backend now calculates intensity per grid cell based on unique months visited
- Groups data by `grid_cell_id` before aggregating
- Counts unique dry months and rainy months visited for each cell
- Formula: `intensity = (dryMonths * 1.0 + rainyMonths * 0.3) / 6.0`

**Implementation**:
```go
// Group by grid cell to track visit frequency
type GridCellData struct {
    GridCellID  string
    Lat, Lon    float64
    DryMonths   map[string]bool  // Unique dry months visited
    RainyMonths map[string]bool  // Unique rainy months visited
    Months      map[string]*PixelData // Per-month data
}

// Calculate intensity per cell
intensity = (dryMonthCount * 1.0 + rainyMonthCount * 0.3) / 6.0
if intensity > 1.5 {
    intensity = 1.5 // Cap for overglow
}
```

### 3. Updated API Response
- Added `Intensity` field to `PixelData` struct
- Each pixel now includes its calculated intensity in JSON response
- Frontend uses API value instead of recalculating

### 4. Frontend Changes
- Removed intensity calculation from XLSX export
- Now uses `p.intensity` from API response directly
- Maintains 3 decimal precision for display

### 5. Star Report Content
- Inline report already uses correct patrol data from `fetchGridDataDirect()`
- CSV export already has correct patrol columns
- No changes needed - working correctly

## Example API Response

```json
{
  "pixels": [
    {
      "park_id": "TCD_Zakouma",
      "park_name": "Zakouma",
      "year": 2024,
      "month": 2,
      "lat": 10.85,
      "lon": 19.85,
      "intensity": 0.167,  // ← Now varies per cell
      "foot_km": 0,
      "vehicle_km": 30.99,
      "aircraft_km": 0
    }
  ]
}
```

## Why Zakouma Shows Same Intensity

Testing revealed that Zakouma pixels all have intensity ~0.167 because:
- Each grid cell was visited in only **1 month** (February 2024)
- February is a dry month → weight = 1.0
- Calculation: 1 / 6 = 0.167
- This is **correct** - all cells have same visit frequency

If cells had different visit patterns:
- Cell A: 3 dry months → intensity = 3/6 = 0.5
- Cell B: 1 dry month → intensity = 1/6 = 0.167
- Cell C: 2 dry + 1 rainy → intensity = (2 + 0.3)/6 = 0.383

## Testing

```bash
# Test Zakouma pixels
curl -s "http://localhost:8000/api/export/patrol-pixels?parks=TCD_Zakouma&pwd=test2026" | \
  jq '.pixels[:3] | .[] | {lat, intensity, months: "\(.year)-\(.month)"}'

# Result: All show 0.167 because all visited in same single month
```

## Files Modified
- `srv/api.go` - Backend intensity calculation
- `srv/templates/globe.html` - Removed summary sheet, use API intensity

## Commits
- **c605123d**: Fix patrol pixel intensity calculation and remove patrol_summary sheet
- **25016126**: Add patrol pixel details sheet to Excel export (initial)

## Related
- Map visualization already shows varying intensities correctly
- KML export uses same data (added yesterday)
- CSV export includes patrol fields (already working)
