# KML Export V2 Implementation Summary

## Overview
Implemented comprehensive KML export function (`exportFullReportKmlV2()`) that exports all spatial data from star reports for use in Google Earth and other KML-compatible applications.

## Location
- **File**: `/home/exedev/5mp/srv/templates/globe.html`
- **Function**: `exportFullReportKmlV2()` (starting at line 5299)
- **Button**: Added to star modal footer (line 16250)

## Features Exported

The KML export includes the following spatial layers for each starred park:

### 1. **Protected Area Boundaries**
- Fetched from `/api/parks/{id}/features?type=boundary`
- Green polygon outline showing park boundaries
- Includes park metadata (country, area, fire groups, settlements, etc.)

### 2. **Fire Activity**
- Fetched from `/api/parks/{id}/features?type=fire_trajectory`
- Fire group trajectories as LineStrings
- Fire points as Points
- Includes narrative, status, and fire count
- Red styling with descriptive labels
- Limited to 200 features per park

### 3. **Settlements**
- Fetched from `/api/parks/{id}/features?type=settlement`
- Settlement polygons with population data
- Includes classification, population estimates, area
- Yellow/orange styling
- Limited to 300 features per park

### 4. **Deforestation Events**
- Fetched from `/api/parks/{id}/features?type=deforestation`
- Deforested area polygons
- Includes loss area (km²), year, and classification
- Purple/magenta styling
- Limited to 200 features per park

### 5. **Patrol Activity**
- Fetched from `/api/grid?park_id={id}`
- Grid points showing patrol locations
- Includes date, distance traveled, transport type
- Green point markers
- Limited to 500 features per park

### 6. **Roads (Context Layer)**
- Fetched from `/api/parks/{id}/features?type=road`
- Road network LineStrings
- Gray styling
- Limited to 100 features per park

### 7. **Rivers (Context Layer)**
- Fetched from `/api/parks/{id}/features?type=river`
- River LineStrings
- Blue/orange styling
- Limited to 50 features per park

## KML Structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>5MP Conservation Report - Comprehensive Export</name>
  <description>Generated {date} | Date range: {from} to {to} | {n} protected areas</description>
  
  <!-- Predefined Styles -->
  <Style id="park-style">...</Style>
  <Style id="fire-style">...</Style>
  <Style id="settlement-style">...</Style>
  <Style id="deforest-style">...</Style>
  <Style id="patrol-style">...</Style>
  <Style id="road-style">...</Style>
  <Style id="river-style">...</Style>
  
  <!-- For each park -->
  <Folder>
    <name>{Park Name}</name>
    <description>{metadata}</description>
    
    <Folder><name>Protected Area Boundary</name>...</Folder>
    <Folder><name>Fire Activity</name>...</Folder>
    <Folder><name>Settlements</name>...</Folder>
    <Folder><name>Deforestation</name>...</Folder>
    <Folder><name>Patrol Activity</name>...</Folder>
    <Folder><name>Roads</name>...</Folder>
    <Folder><name>Rivers</name>...</Folder>
  </Folder>
  
</Document>
</kml>
```

## Key Implementation Details

### Coordinate Conversion
- Implemented `coordsToKml()` helper to convert GeoJSON coordinates to KML format
- KML format: `lon,lat,alt` (altitude always set to 0)
- Handles Point, LineString, Polygon, and MultiPolygon geometries

### XML Escaping
- Implemented `escapeXml()` helper to properly escape special characters
- Escapes: `&`, `<`, `>`, `"`, `'`

### Geometry Builders
- `buildKmlPolygon()` - Creates KML Polygon with outerBoundaryIs/LinearRing
- `buildKmlLineString()` - Creates KML LineString
- `buildKmlPoint()` - Creates KML Point

### Data Fetching
- Uses existing `reportDataCache` for metadata
- Makes parallel API calls for geometry data
- Respects date range filters (dateFrom/dateTo)
- Includes error handling for failed API requests

### Performance Considerations
- Feature limits per layer to prevent oversized files:
  - Fire trajectories: 200
  - Settlements: 300
  - Deforestation: 200
  - Patrol points: 500
  - Roads: 100
  - Rivers: 50
- Async/await for sequential park processing
- Progress feedback via toast notifications

## File Naming
- Uses existing `buildReportFilename()` helper
- Format: `5MP_Report_Comprehensive_{from}_to_{to}.kml`
- Example: `5MP_Report_Comprehensive_2024-01-01_to_2024-12-31.kml`

## User Interface

### Button Location
Star modal footer, alongside other export buttons:
- CSV V2
- XLSX V2
- KML (basic)
- **KML V2** (new)
- Print

### Button Tooltip
"Export comprehensive KML with park boundaries, fire trajectories, settlements, deforestation, patrol points, roads and rivers"

### User Feedback
- Warning if data is still loading for some parks
- Progress toast: "Generating comprehensive KML for {n} parks..."
- Success toast: "Exported comprehensive KML for {n} parks with all spatial layers"

## Comparison with Original KML Export

| Feature | Original `exportFullReportKml()` | New `exportFullReportKmlV2()` |
|---------|-----------------------------------|-------------------------------|
| Park boundaries | ❌ No | ✅ Yes (fetched from API) |
| Fire data | ✅ Yes (from cache) | ✅ Yes (with geometry from API) |
| Settlements | ✅ Yes (basic) | ✅ Yes (with full geometry) |
| Deforestation | ✅ Yes (basic) | ✅ Yes (with full geometry) |
| Patrol/Grid | ❌ No | ✅ Yes (with timestamps) |
| Roads | ✅ Yes (basic) | ✅ Yes (structured) |
| Rivers | ✅ Yes (basic) | ✅ Yes (structured) |
| Placemarks | ❌ Limited | ✅ Rich descriptions |
| Styles | ✅ Basic colors | ✅ Professional styles |
| Organization | ✅ Park folders | ✅ Hierarchical folders |

## Testing Recommendations

1. **Single Park Export**
   - Star a single park with diverse data
   - Click "↓ KML V2" button
   - Open in Google Earth
   - Verify all folders and placemarks appear

2. **Multi-Park Export**
   - Star 3-5 parks
   - Generate report to load data
   - Export KML V2
   - Verify each park has its own folder

3. **Date Range Filtering**
   - Set date range in star modal
   - Export KML V2
   - Verify fire and patrol data respects date filter

4. **Large Dataset**
   - Star a park with many features
   - Verify feature limits work correctly
   - Check file size is reasonable

5. **Error Handling**
   - Export before data loads (test warning)
   - Export with offline API (test graceful failures)

## Known Limitations

1. **Feature Limits**: Large parks may have features clipped to configured limits
2. **Single Polygon**: MultiPolygons only use first polygon component
3. **No Temporal Animation**: TimeSpan/TimeStamp not implemented (future enhancement)
4. **Boundary API**: Requires boundary feature type to be available in API
5. **File Size**: Very large exports (100+ parks) may be slow or large

## Future Enhancements

1. **Temporal KML**: Add TimeSpan for fire progression and patrol tracks
2. **Network Links**: Support for dynamic KML updates
3. **Custom Icons**: Use custom icons for different feature types
4. **Balloon Templates**: Rich HTML popups in Google Earth
5. **KMZ Export**: Compress to KMZ with embedded images
6. **3D Features**: Elevation data for terrain-aware visualization
7. **Configurable Limits**: Allow users to adjust feature limits

## API Dependencies

The function depends on these API endpoints:

```
GET /api/parks/{id}/features?type=boundary&pwd={pwd}
GET /api/parks/{id}/features?type=fire_trajectory&pwd={pwd}&from={from}&to={to}
GET /api/parks/{id}/features?type=settlement&pwd={pwd}
GET /api/parks/{id}/features?type=deforestation&pwd={pwd}
GET /api/parks/{id}/features?type=road&pwd={pwd}
GET /api/parks/{id}/features?type=river&pwd={pwd}
GET /api/grid?park_id={id}&pwd={pwd}&from={from}&to={to}
```

## Git Commit

```bash
git add srv/templates/globe.html
git commit -m "Add KML export v2 with all spatial layers"
```

Commit hash: `b8718741`

## Documentation

This implementation completes the export functionality trio:
- ✅ CSV V2 - Tabular data export
- ✅ XLSX V2 - Spreadsheet export
- ✅ KML V2 - Spatial data export (this implementation)

Users can now export their conservation reports in the format best suited for their analysis workflow:
- **CSV/XLSX** for statistical analysis, charts, and reporting
- **KML** for spatial analysis in Google Earth, QGIS, ArcGIS, etc.
