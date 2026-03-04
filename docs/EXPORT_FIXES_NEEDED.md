# Export Fixes Required - Comprehensive Issue Tracker

## Summary
Inline reports work perfectly, but all three export formats (CSV, XLSX, KML) have field mapping issues causing zeros/nulls and missing critical features.

## ✅ CSV Export - FIXED
**Status**: Fixed in commit 35d20c60

Field name mapping corrected to use snake_case:
- ✅ fire.total_fires (was: fire.totalFires)
- ✅ fire.fire_groups (was: fire.fireGroups)  
- ✅ deforestation.total_loss_km2 (was: deforestation.totalLossKm2)
- ✅ settlement.settlement_count (was: settlement.count)
- ✅ species.total_count (was: species.count)
- ✅ Distance showing: 372.65 km

**Test Result**: Now shows correct data!

## ❌ XLSX Export - NEEDS FIXING

### Issues Found (tested):
1. **Summary Sheet** - Missing data:
   - Area_km2: None (should be 11,181)
   - Fire_Detections: None (should be 3,226)
   - Fire_Groups: None (should be 120)
   - ✅ Deforest_km2: 0.53 (correct!)
   - ✅ Settlements: 12 (correct!)
   - ✅ Distance_km: 372.6 (correct!)

2. **Species Sheet** - Partial data:
   - ✅ Park, Common_Name, Status, Order, Family (working)
   - ❌ Scientific_Name: None (should be binomial)

3. **Missing Sheets**:
   - ❌ All_Deforestation - Should include: Park, Year, Area_km2, Classification, Narrative, Location
   - ❌ All_Settlements - Should include: Park, Classification, Population, Area_km2, Narrative, Location

### Fix Locations (exportFullReportXlsxV2 at line 4731):
```javascript
// Summary sheet - needs field name mapping like CSV
park.area_km2 (not park.area)
fire.total_fires (not fire.totalFires)
fire.fire_groups (not fire.fireGroups)

// Species sheet - add binomial
s.binomial || s.scientific_name

// Add missing sheets:
- All_Deforestation with classified_events
- All_Settlements with classified_settlements
```

## ❌ KML Export - CRITICAL MISSING FEATURES

### Major Issues Found:
1. **NO PARK BOUNDARIES** ❌❌❌
   - This is critical for Google Earth visualization
   - Need to fetch and include park polygon/boundary
   - Style: green outline with semi-transparent fill

2. **Field Name Mapping** - Same as CSV/XLSX:
   - Area: 0 km² (should be 11,181)
   - Fire Groups: 0 (should be 120)
   - Total Fires: 0 (should be 3,226)
   - Deforestation Events: 0 (should be 20)
   - ✅ Settlements: 12 (correct!)
   - ✅ Patrol Points: 38 (correct!)

3. **Fire Narratives Show "Status: Unknown | Fires: 0"**
   - Should show actual fire counts
   - Should show proper status (approaching/cooling/contained)

### Fix Required (exportFullReportKmlV2 at line ~5299):
```javascript
// 1. Add park boundary polygon FIRST
const parkBoundary = await fetch(`/api/parks/${parkId}/boundary?pwd=${pwd}`);
// OR use park.geometry if available

// Include as first placemark in each park folder:
<Placemark>
  <name>${parkName} Boundary</name>
  <styleUrl>#park-style</styleUrl>
  <Polygon>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>...boundary coords...</coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>

// 2. Fix field names (same snake_case fixes as CSV)
// 3. Fix fire status and counts in descriptions
```

## 🔧 Detailed Fix Plan

### Phase 1: XLSX Export (High Priority)
File: `srv/templates/globe.html` lines 4731-5200

**Step 1**: Fix Summary sheet field mapping
```javascript
// Around line 4850-4900
areaKm2: park.area_km2 || 0  // not park.area
fireDetections: fire.total_fires || 0  // not fire.totalFires
fireGroups: fire.fire_groups || 0  // not fire.fireGroups
```

**Step 2**: Fix Species sheet
```javascript
// Around line 5100
scientificName: species.binomial || species.scientific_name || ''
```

**Step 3**: Add All_Deforestation sheet
```javascript
const deforestSheet = [];
deforestSheet.push(['Park', 'Year', 'Area_km2', 'Classification', 'Narrative', 'Lat', 'Lon']);

for (const {park, data} of allParks) {
    const events = data.deforestation?.classified_events || [];
    for (const event of events) {
        deforestSheet.push([
            park.name,
            event.year || '',
            event.area_km2 || 0,
            event.classification || '',
            event.narrative || '',
            event.lat || '',
            event.lon || ''
        ]);
    }
}

XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(deforestSheet), 'All_Deforestation');
```

**Step 4**: Add All_Settlements sheet
```javascript
const settlementSheet = [];
settlementSheet.push(['Park', 'Classification', 'Population', 'Area_km2', 'Narrative', 'Lat', 'Lon', 'Nearest_Place']);

for (const {park, data} of allParks) {
    const settlements = data.settlement?.classified_settlements || [];
    for (const s of settlements) {
        settlementSheet.push([
            park.name,
            s.classification || '',
            s.population_est || 0,
            (s.area_m2 / 1000000).toFixed(2) || 0,
            s.narrative || '',
            s.lat || '',
            s.lon || '',
            s.nearest_place || ''
        ]);
    }
}

XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(settlementSheet), 'All_Settlements');
```

### Phase 2: KML Export (Critical Priority)
File: `srv/templates/globe.html` lines ~5299-5700

**Step 1**: Add park boundary fetching
```javascript
// At start of park loop
const boundaryResp = await fetch(`/api/parks/${encodeURIComponent(parkId)}/features?type=boundary&pwd=${pwd}`);
const boundaryData = await boundaryResp.json();
const parkPolygon = boundaryData.features?.[0]?.geometry;

// OR if park object has geometry:
const parkPolygon = park.geometry;
```

**Step 2**: Add park boundary placemark FIRST
```javascript
if (parkPolygon && parkPolygon.coordinates) {
    kml += `<Placemark>\n`;
    kml += `  <name>${escapeXml(parkName)} - Protected Area Boundary</name>\n`;
    kml += `  <description>Area: ${park.area_km2?.toFixed(0) || '?'} km²</description>\n`;
    kml += `  <styleUrl>#park-style</styleUrl>\n`;
    kml += `  <Polygon>\n`;
    kml += `    <outerBoundaryIs>\n`;
    kml += `      <LinearRing>\n`;
    kml += `        <coordinates>\n`;
    
    // Handle MultiPolygon or Polygon
    const coords = parkPolygon.type === 'MultiPolygon' ? 
                   parkPolygon.coordinates[0][0] : 
                   parkPolygon.coordinates[0];
    
    for (const [lon, lat] of coords) {
        kml += `${lon},${lat},0 `;
    }
    
    kml += `\n        </coordinates>\n`;
    kml += `      </LinearRing>\n`;
    kml += `    </outerBoundaryIs>\n`;
    kml += `  </Polygon>\n`;
    kml += `</Placemark>\n`;
}
```

**Step 3**: Fix field names in descriptions (same as CSV/XLSX)

**Step 4**: Fix fire status display
```javascript
// Use isFireStopped() helper or check narrative status
const fireStatus = narrative.status || (isFireStopped(narrative) ? 'Contained' : 'Active');
const fireCount = narrative.fires_inside || narrative.fires_total || 0;
```

## Testing Checklist

### CSV Export:
- [x] Fire detections show
- [x] Deforestation km² show
- [x] Settlement counts show
- [x] Species counts show
- [x] Distance shows
- [ ] All parks in bbox included

### XLSX Export:
- [ ] Summary sheet - all fields populated
- [ ] Species sheet - Scientific_Name populated
- [ ] All_Deforestation sheet exists with narratives
- [ ] All_Settlements sheet exists with narratives
- [ ] Patrol_Summary has distance breakdown
- [ ] Fire sheet has all narratives
- [ ] No None/null values where data exists

### KML Export:
- [ ] Park boundaries show (green polygons)
- [ ] Fire trajectories with correct counts
- [ ] Deforestation polygons with narratives
- [ ] Settlement points with classifications
- [ ] Patrol grid points
- [ ] All descriptions have real data (no zeros)
- [ ] Opens correctly in Google Earth

## Priority Order

1. **CRITICAL**: Add park boundaries to KML
2. **HIGH**: Fix XLSX Summary sheet field names
3. **HIGH**: Add All_Deforestation sheet to XLSX
4. **HIGH**: Add All_Settlements sheet to XLSX
5. **MEDIUM**: Fix Species Scientific_Name in XLSX
6. **MEDIUM**: Fix KML descriptions (field names)
7. **LOW**: Add distance breakdown by type

## Data Structure Reference

For all exports, use these field names:
```javascript
fire: {
  total_fires: number,
  fire_groups: number,
  response_rate: number,
  narratives: [{narrative, fires_inside, ...}]
}

deforestation: {
  total_loss_km2: number,
  classified_events: [{
    year, area_km2, classification, narrative, lat, lon
  }]
}

settlement: {
  settlement_count: number,
  built_up_km2: number,
  total_population: number,
  classified_settlements: [{
    classification, population_est, area_m2, narrative, lat, lon
  }]
}

species: {
  total_count: number,
  species: [{
    binomial, common_name, status, order, family
  }]
}

grid: {
  activePixels: number,
  totalDistanceKm: number,
  byType: {foot, vehicle, boat, air}
}
```

## Notes

- Inline reports work perfectly - use as reference
- All data is available, just needs correct field mapping
- Park boundaries might be in `park.geometry` or need separate API call
- Distance data calculates correctly now (372.65 km verified)

---

**Status**: CSV fixed. XLSX and KML need comprehensive updates.
**Updated**: 2026-03-04 07:15
