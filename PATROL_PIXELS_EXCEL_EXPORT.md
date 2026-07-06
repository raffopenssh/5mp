# Patrol Pixel Details in Excel Export

## Feature
Added a new "Patrol_Pixels" sheet to the starred reports Excel export with monthly patrol effort data at the pixel level.

## Excel Sheet: Patrol_Pixels

### Columns
1. **Park** - Park name
2. **Start_Date** - Month start date (YYYY-MM-01)
3. **End_Date** - Month end date (YYYY-MM-28)
4. **Lat** - Grid cell center latitude (5 decimal precision)
5. **Lon** - Grid cell center longitude (5 decimal precision)
6. **Intensity** - Patrol intensity calculated from temporal frequency
7. **Foot_km** - Distance covered on foot (km)
8. **Vehicle_km** - Distance covered by vehicle (km)
9. **Aerial_km** - Distance covered by aircraft (km)

### Geographic Buffer
- Uses **30km buffer from park polygon boundary** (not bounding box)
- Calculates minimum distance from each grid cell to polygon edges
- Only includes pixels within 30km of actual park boundary
- Uses Haversine distance for accuracy

### Implementation

#### Backend API
**Endpoint**: `GET /api/export/patrol-pixels`

**Parameters**:
- `parks` (required): Comma-separated park IDs (max 100)
- `from` (optional): Start date (YYYY-MM-DD)
- `to` (optional): End date (YYYY-MM-DD)

**Example**:
```bash
curl "http://localhost:8000/api/export/patrol-pixels?parks=TCD_Zakouma&pwd=test2026&from=2020-01-01&to=2024-12-31"
```

**Response**:
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
      "foot_km": 0,
      "vehicle_km": 30.99,
      "aircraft_km": 0
    }
  ],
  "total_pixels": 11,
  "parks": 1
}
```

####Frontend Integration
- Automatically called during Excel export
- Fetches all parks in bulk (one API call)
- Shows toast: "Fetching patrol pixel details..."
- Data added to "Patrol_Pixels" sheet

### Algorithm Details

1. **Query Expansion**: Gets bounding box + 35km buffer for initial SQL query efficiency
2. **Distance Filtering**: Calculates actual distance from each grid cell to polygon boundary
3. **30km Cutoff**: Only includes cells within 30km of park boundary
4. **Aggregation**: Groups by park, year, month, lat, lon
5. **Movement Types**: Aggregates foot, vehicle, aircraft separately from effort_data table

### Helper Functions
- `minDistanceToPolygon()`: Calculates minimum distance from point to polygon
- `pointInPolygon()`: Ray casting algorithm for inside/outside check
- `distanceToLineSegment()`: Projects point onto line segment
- `haversineDistance()`: Great circle distance (reused from existing code)

### Data Source
- Table: `effort_data` joined with `grid_cells`
- Filters: `day IS NULL` (monthly aggregates only)
- Movement types: 'foot', 'vehicle', 'aircraft'
- Limit: 5000 pixels per park (most recent)

### Intensity Calculation
- Dry season months (Nov-Apr): weight = 1.0
- Rainy season months (May-Oct): weight = 0.3
- Normalize to 6 dry months baseline
- Displayed with 3 decimal precision

## Files Modified
- `srv/api.go` - Added bulk export endpoint and helper functions
- `srv/server.go` - Added route for `/api/export/patrol-pixels`
- `srv/templates/globe.html` - Added patrol pixels sheet to Excel export

## Testing
```bash
# Test with Zakouma (has patrol data)
curl -s "http://localhost:8000/api/export/patrol-pixels?parks=TCD_Zakouma&pwd=test2026&from=2020-01-01&to=2024-12-31" | jq '{total_pixels, parks}'

# Result: 11 pixels within 30km of Zakouma boundary
```

## Related Features
- **KML Export**: Already includes patrol pixels (added yesterday in commit 7a73e394)
- **Patrol Summary Sheet**: Existing sheet with park-level aggregates
- **Grid API**: `/api/grid` for map visualization

## Commit
- **Hash**: 25016126
- **Message**: "Add patrol pixel details sheet to Excel export"
- **Date**: 2026-03-05
