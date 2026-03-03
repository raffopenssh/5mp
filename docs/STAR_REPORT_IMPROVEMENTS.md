# Star Report Export Improvements for Donors

## Overview

The star report function is one of the most important features for donors, providing comprehensive data exports in multiple formats. All export formats (CSV, XLSX, KML, Print, RSS) have been enhanced to provide maximum information richness.

## Changes Summary (Commits: 22def3ee, 08686828)

### 1. KML Export Enhancements

**Problem**: Settlements were being exported as Point features instead of their actual Polygon footprints.

**Solution**:
- Updated `coordsToKML()` to properly handle Polygon geometry
- Changed KML structure from `<Point>` to `<Polygon><outerBoundaryIs><LinearRing>`
- Updated settlement style from icon-based to polygon-based with border and fill
- Added comprehensive settlement metadata:
  - Classification (temporary_camp, village, etc.)
  - Population estimate
  - Area in m²
  - Narrative description

**Example KML Output**:
```xml
<Placemark>
  <name>Safari Ht Chinko</name>
  <description>Classification: temporary_camp, Population: 1, Area: 10,000 m², Narrative: ...</description>
  <styleUrl>#settlement</styleUrl>
  <Polygon>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>23.838,6.854,0 23.838,6.854,0 ...</coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>
```

### 2. Print Report Enhancements

**New Sections Added**:

#### Biodiversity Section
- Total mammal species count
- Threatened species count (CR, EN, VU)
- Top 10 threatened species with:
  - IUCN status (color-coded: CR=red, EN=orange, VU=yellow)
  - Scientific name (binomial)
  - Common name
- Example: "CR Gorilla beringei (Mountain Gorilla)"

#### Climate Section
- Annual precipitation in mm
- Dry season months
- Wet season months

#### Research Publications
- Count of recent publications
- Top 5 publications with:
  - Title
  - Year
  - Authors (first 3, with "et al." if more)

#### Infrastructure & Features
- River count
- Road count
- Named places count
- Infrastructure count (airstrips, etc.)

**Print CSS Improvements**:
- Added `@media print` rules for better PDF output
- Page-break-inside: avoid for sections
- Black & white print-friendly colors
- Proper pagination

### 3. RSS Feed Improvements

**Problem**: Fire narrative wasn't loading due to incorrect column name.

**Solution**:
- Fixed `fetchParkNarrativeSummary()` to use `narrative_json` column
- Parse JSON to extract summary field
- Added deforestation stats (total loss km², event count)
- Added settlement stats (count, population)

**RSS Description Format**:
```
FIRE: From 2020-2026, Chinko experienced 215,568 fire detections across 2207 fire groups. 92 (4%) appear to be management burns. 352 (15%) stopped inside. Peak: December. | SETTLEMENTS: 27 settlements, est. population 823 | Recent updates: - fire_alert: ⚠️ Alpha-2 (Approaching) ...
```

### 4. CSV Export

Already comprehensive with 23 columns:
- Source, Park, Country, Area
- Fire metrics (total, groups, response %, peak month, trajectories, top locations)
- Deforestation metrics (km², events, trend, top locations)
- Settlement metrics (count, population, built-up km²)
- Infrastructure counts (rivers, roads, places, infrastructure)
- Roadless percentage
- Species count

### 5. XLSX Export

Multi-sheet workbook:
- Summary sheet with all parks
- Fire detail sheet
- Deforestation detail sheet
- Settlement detail sheet
- Patrol grid matrix

## Data Sources

All exports now include data from:
1. **Fire**: Narrative, statistics, trajectories (GeoJSON lines)
2. **Deforestation**: Events (GeoJSON polygons), trends, hotspots
3. **Settlements**: Classified clusters (GeoJSON polygons), population estimates
4. **Rivers**: HydroRIVERS data (GeoJSON lines)
5. **Roads**: Surface data from HeiGIT (GeoJSON lines)
6. **Places**: OSM place names (GeoJSON points)
7. **Infrastructure**: Airstrips and facilities (GeoJSON points)
8. **Species**: IUCN mammal species with threat status
9. **Climate**: Monthly precipitation, seasons
10. **Publications**: Recent research papers from OpenAlex

## Usage

### Star Parks
1. Click park on map or in search results
2. Click star icon (☆) in popup or panel
3. Star panel shows count badge

### Generate Exports

**CSV Export**: Complete data table, 23 columns
- Click "CSV Export" button in star panel
- Downloads: `5MP_Full_Report_from_YYYY-MM-DD_to_YYYY-MM-DD.csv`

**XLSX Export**: Multi-sheet workbook with detail tables
- Click "XLSX Export" button
- Downloads: `5MP_Full_Report_from_YYYY-MM-DD_to_YYYY-MM-DD.xlsx`

**KML Export**: Geographic data for Google Earth/Maps
- Click "KML Export" button
- Downloads: `5MP_Report_from_YYYY-MM-DD_to_YYYY-MM-DD.kml`
- Settlements shown as actual polygons with footprints

**Print Report**: Comprehensive HTML report for PDF printing
- Click "⎙ Print Report" button
- Opens new window with formatted report
- Use browser's Print to PDF function
- Suggested filename: `5MP_Report_YYYY-MM-DD_from_YYYY-MM-DD_to_YYYY-MM-DD.pdf`

**RSS Feed**: Live updates for starred areas
- Click "RSS Feed" button
- Copies URL to clipboard
- Add URL to feed reader (Feedly, Inoreader, etc.)
- Receives updates when:
  - New fire alerts detected
  - New patrol data uploaded
  - New research published
  - New MBTiles downloads ready

## Technical Details

### KML Geometry Conversion

```javascript
function coordsToKML(coords, geomType) {
    if (geomType === 'Point') {
        return `${coords[0]},${coords[1]},0`;
    } else if (geomType === 'LineString') {
        return coords.map(c => `${c[0]},${c[1]},0`).join(' ');
    } else if (geomType === 'Polygon') {
        return coords[0].map(c => `${c[0]},${c[1]},0`).join(' ');
    } else if (geomType === 'MultiPolygon') {
        return coords[0][0].map(c => `${c[0]},${c[1]},0`).join(' ');
    }
    return '';
}
```

### Species Data Structure

API returns:
```json
{
  "park_id": "COD_Virunga",
  "total_count": 342,
  "threatened": 21,
  "critical": 1,
  "endangered": 8,
  "vulnerable": 12,
  "species": [
    {
      "binomial": "Gorilla beringei",
      "common_name": "Mountain Gorilla",
      "status": "CR",
      "status_name": "Critically Endangered",
      "order": "PRIMATES",
      "family": "HOMINIDAE"
    }
  ]
}
```

### Print Report Data Fetching

```javascript
async function fetchParkFullData(parkId) {
    // Fetches 14 data sources in parallel:
    // fire-narrative, deforestation-narrative, settlement-narrative,
    // stats, species, climate, publications, 
    // fire_trajectory, deforestation, settlement, river, road, place, infrastructure
    
    const [fire, deforest, settle, stats, species, climate, publications] = 
        await Promise.all([...]);
    
    // Then fetches all feature layers
    const features = await Promise.all([
        fetch(`/api/parks/${parkId}/features?type=fire_trajectory`),
        fetch(`/api/parks/${parkId}/features?type=deforestation`),
        // ... etc
    ]);
}
```

### Auto-Prefetch on Page Load

When URL contains `?parks=CAF_Chinko,COD_Virunga`, data is automatically prefetched in background:

```javascript
if (urlParams.has('parks')) {
    const parkIds = urlParams.get('parks').split(',');
    setTimeout(() => prefetchAllStarredData(), 1000);
}
```

## Testing

Test with multiple parks:
```
http://localhost:8000/?pwd=test2026&parks=CAF_Chinko,COD_Virunga,CMR_Lobéké
```

All data will prefetch in background, exports ready immediately.

## Files Modified

1. `srv/templates/globe.html`:
   - KML export settlement polygon fix
   - Print report biodiversity, climate, publications sections
   - Species data structure handling
   - CSV species count fix

2. `srv/api.go`:
   - RSS feed narrative extraction
   - Deforestation and settlement stats in RSS
   - JSON parsing for narrative_json column

## Commits

- `22def3ee` - Comprehensive star report improvements for donors
- `08686828` - Fix species data structure in print report

## Production Ready

All export formats are now production-ready with:
✅ Accurate geometry types (settlements as polygons)
✅ Comprehensive metadata in all formats
✅ Rich biodiversity data with threatened species
✅ Climate and research context
✅ Print-optimized CSS for PDF generation
✅ Live RSS updates with detailed summaries
✅ Auto-prefetch for instant exports
✅ Professional formatting suitable for donor reports
