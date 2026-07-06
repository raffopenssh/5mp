# Patrol Pixel Export - Final Implementation

## Overview
Added detailed patrol pixel data to Excel and CSV exports, matching the map visualization exactly.

## Fixes Completed

### 1. ✅ Removed Patrol_Summary Sheet
- Redundant with detailed Patrol_Pixels sheet
- Reduces file size and confusion

### 2. ✅ Fixed Intensity Calculation
**Problem**: Export showed uniform intensity, but map showed variation

**Root Cause**: Export was calculating intensity per month instead of per grid cell across entire time range

**Solution**: Now uses `QueryGridData()` - same function the map uses
- Counts DISTINCT dry/rainy months visited per grid cell
- Formula: `intensity = (dry_months * 1.0 + rainy_months * 0.3) / 6.0`
- Cap at 1.5 for overglow effect

### 3. ✅ Verified Against Map
Tested with TCD_Zakouma:
- Map API shows: `(10.85, 19.65)` = 0.333 intensity
- Export API shows: `(10.85, 19.65)` = 0.333 intensity ✓
- Other cells: 0.167 intensity (visited 1 month vs 2 months)

### 4. ✅ Geographic 30km Buffer
- Uses actual distance to polygon boundary (not bounding box)
- Haversine formula for accurate measurements
- Filters out pixels beyond 30km

### 5. ✅ Movement Type Aggregation
- Separate columns for Foot_km, Vehicle_km, Aerial_km
- Aggregates from movement_type = 'foot', 'vehicle', 'aircraft'
- Matches map's data display

## Excel Sheet Structure

**Sheet Name**: Patrol_Pixels

| Column | Description | Example |
|--------|-------------|---------|
| Park | Park name | Zakouma |
| Start_Date | Month start | 2024-02-01 |
| End_Date | Month end | 2024-02-28 |
| Lat | Grid center latitude | 10.85 |
| Lon | Grid center longitude | 19.65 |
| Intensity | Visit frequency (0-1.5) | 0.333 |
| Foot_km | Foot patrol distance | 7.30 |
| Vehicle_km | Vehicle patrol distance | 16.80 |
| Aerial_km | Aircraft patrol distance | 0 |

## API Endpoint

```bash
GET /api/export/patrol-pixels?parks=PARK1,PARK2&from=YYYY-MM-DD&to=YYYY-MM-DD
```

**Example**:
```bash
curl "http://localhost:8000/api/export/patrol-pixels?parks=TCD_Zakouma&pwd=test2026" | \
  jq '.pixels | group_by(.lat) | map({lat: .[0].lat, intensity: .[0].intensity})'
```

**Result**:
```json
[
  {"lat": 10.85, "intensity": 0.333},  // Visited 2 months
  {"lat": 10.75, "intensity": 0.167},  // Visited 1 month
  {"lat": 10.95, "intensity": 0.167}   // Visited 1 month
]
```

## Implementation Details

### Backend Flow
1. Query park boundary from AreaStore
2. Calculate 30km buffer from polygon (not bbox)
3. Call `QueryGridData()` to get intensity per cell:
   ```sql
   COUNT(DISTINCT CASE WHEN month IN (11,12,1,2,3,4) THEN year||'-'||month END) as dry_months
   COUNT(DISTINCT CASE WHEN month IN (5,6,7,8,9,10) THEN year||'-'||month END) as rainy_months
   ```
4. Calculate intensity: `(dry + rainy*0.3) / 6`
5. Query monthly details per grid cell
6. Join intensity to monthly records
7. Filter by 30km buffer
8. Return pixels with intensity

### Frontend Integration
- XLSX export calls `/api/export/patrol-pixels` in bulk
- Uses returned intensity value directly
- 3 decimal precision for display
- CSV export already has correct patrol columns

## WorldClim Climate Integration

The intensity calculation respects seasonal patterns:
- **Dry Season (Nov-Apr)**: Full weight (1.0)
  - All movement types viable
  - Optimal conditions for patrols
- **Rainy Season (May-Oct)**: Reduced weight (0.3)
  - Flooding reduces foot/vehicle access
  - Aircraft still viable
  - Off-road movement limited

This matches conservation reality where:
- Dry season = 6 months of optimal patrolling
- Rainy season = Limited ground access but aerial works
- 30% weight acknowledges aerial capability during rains

## Files Modified
- `srv/api.go` - Uses QueryGridData for intensity
- `srv/templates/globe.html` - Removed summary sheet
- `srv/grid_query.go` - (existing, no changes)

## Commits
- **bfac01d9**: Fix intensity to match map exactly
- **c605123d**: Initial intensity calculation attempt
- **25016126**: Add patrol pixels export

## Testing

```bash
# Verify intensity matches map
curl "http://localhost:8000/api/grid?bbox=19.5,10.7,20.0,11.1&pwd=test2026" | \
  jq '.features[0].properties.intensity'
# Result: 0.333

curl "http://localhost:8000/api/export/patrol-pixels?parks=TCD_Zakouma&pwd=test2026" | \
  jq '.pixels[0].intensity'  
# Result: 0.333 ✓
```

## Related Features
- KML export (uses same patrol data)
- Map visualization (now matches export)
- Inline star reports (uses grid API)
- CSV export (has patrol columns)
