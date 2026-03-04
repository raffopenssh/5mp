# Bug Fix: KML Export Missing Narratives and Settlement Polygons

## Issues Found

1. **Fire trajectories**: All labeled "Fire Trajectory" with NO narrative descriptions
2. **Settlements**: Folder was EMPTY - no polygons exported at all
3. **Deforestation**: Wrong join logic, narratives not matching correctly
4. **All features**: Not using the same `polygon_ids` join logic as tooltips/popups

## Root Causes

### Fire Narratives
The code was querying a non-existent `fire_groups` table and trying to match by `group_id`. 

**Reality:** Narratives are stored directly in `feature_geometries.properties_json` as `narrative` field.

### Settlement Polygons
The LEFT JOIN was using a coordinate-based match that failed:
```sql
LEFT JOIN park_settlements ps ON ... 
  AND ABS(ps.lat - fg.centroid_lat) < 0.001
```

Problem: `feature_geometries` table has no `centroid_lat` column!

### Wrong Join Pattern
All three feature types were using different join logic than the tooltip/popup display, which uses:
```sql
AND (',' || ps.polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')
```

## The Fix

All three feature types now use **identical join logic to tooltips/popups**:

### Fire Trajectories

**Before:**
```xml
<Placemark><name>Fire Trajectory</name>
  <styleUrl>#fire</styleUrl>
  <!-- NO DESCRIPTION -->
</Placemark>
```

**After:**
```xml
<Placemark><name>Fire eb3f6745</name>
  <styleUrl>#fire</styleUrl>
  <description><![CDATA[
    Isolated fire event 2026-03-03 to 2026-03-04 (2 days). 
    entered and stopped inside park. Traveled 0.6km NE. 
    near Bamingui (24.4km).
  ]]></description>
  <TimeSpan>...</TimeSpan>
  <LineString>...</LineString>
</Placemark>
```

### Settlements

**Before:**
```xml
<Folder><name>Settlements</name>
  <!-- EMPTY! -->
</Folder>
```

**After:**
```xml
<Folder><name>Settlements</name>
  <Placemark><name>Fishing Settlement (pop: 1) near Gatamainda</name>
    <styleUrl>#settlement</styleUrl>
    <description><![CDATA[
      Fishing camp 5km from Gatamainda, 0.2km from Bangoran River. 
      Small footprint (10000 m²) and minimal forest disturbance 
      consistent with seasonal fishing activity.
    ]]></description>
    <Polygon>...</Polygon>
  </Placemark>
  ...
</Folder>
```

### Deforestation

**Before:**
- Inconsistent narratives due to wrong year matching
- All labels appeared the same

**After:**
```xml
<Placemark><name>Slash_burn (2023) - 0.01 km²</name>
  <styleUrl>#deforestation</styleUrl>
  <description><![CDATA[
    Slash-and-burn clearing detected in 2023. Affected 0.01 km² 
    across 1 patch. Strong fire correlation indicates agricultural 
    burning. Located 3.0km from Niango. Near Bahr Aouk river.
  ]]></description>
  <TimeSpan><begin>2023-01-01</begin><end>2023-12-31</end></TimeSpan>
  <Polygon>...</Polygon>
</Placemark>
```

## Implementation

### Settlements Query
```sql
SELECT 
  fg.feature_id,
  fg.geojson,
  fg.properties_json,
  ps.narrative,
  ps.classification,
  ps.nearest_place
FROM feature_geometries fg
LEFT JOIN park_settlements ps ON fg.park_id = ps.park_id 
  AND (',' || ps.polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')
WHERE fg.park_id = ? AND fg.feature_type = 'settlement'
```

### Deforestation Query
```sql
SELECT 
  fg.feature_id,
  fg.geojson,
  fg.properties_json,
  fg.start_date,
  de.narrative,
  de.classification,
  de.pattern_type,
  de.area_km2,
  de.year
FROM feature_geometries fg
LEFT JOIN deforestation_events de ON fg.park_id = de.park_id 
  AND CAST(fg.properties_json->>'year' AS INTEGER) = de.year
  AND (',' || de.polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')
WHERE fg.park_id = ? AND fg.feature_type = 'deforestation'
```

### Fire Trajectory Logic
```go
// Get narrative directly from properties_json
if narrative, ok := propMap["narrative"].(string); ok && narrative != "" {
    description = narrative
}

// Build descriptive name from feature_id or group_name
if groupName, ok := propMap["group_name"].(string); ok && groupName != "" {
    name = groupName
}
```

## Data Consistency

**Key Achievement:** KML export now shows **exactly the same data** as:
- Popup tooltips when clicking features
- Feature info panels
- Single-pin displays

All use the same `polygon_ids` join pattern defined in:
- `handleSettlementFeatures()` 
- `handleDeforestationFeatures()`
- Fire narratives from `properties_json`

## Testing

```bash
# Test single park KML
curl -s "http://localhost:8000/api/parks/CAF_Bamingui-Bangoran/export.kml?pwd=test2026" > test.kml
xmllint --noout test.kml  # Validates XML

# Check fire narratives
grep -A 3 'Fire Trajectories' test.kml | head -20

# Check settlement polygons
grep -A 5 'Settlements' test.kml | head -30

# Count features
grep '<description><![CDATA[' test.kml | wc -l
```

## Impact

KML exports now provide **full context** for:
- Fire analysis in Google Earth with detailed narratives
- Settlement mapping with actual polygons and descriptions
- Deforestation patterns with classification and context

**All data matches what users see in the web UI tooltips.**

## Related Fixes

- XML escaping (commit a8a9be9c) - ensures special characters don't break XML
- Polygon buffer (commit 1bbdcf0a) - fixed grid cell buffer calculation

## Commit

```
commit b411faf7
Fix: KML export now includes full narratives and uses polygon_ids joins
```
