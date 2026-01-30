# Narrative API Test Report

**Date:** January 30, 2026  
**Tester:** Automated Test Suite  
**Authentication:** Cookie `access_pwd=ngi2026`

---

## Summary

All narrative APIs were tested across various park scenarios. **All endpoints returned valid JSON responses with meaningful narratives.** No critical issues were found.

| Endpoint Type | Tests Passed | Tests Failed |
|--------------|-------------|-------------|
| Settlement Narratives | 4/4 | 0 |
| Fire Narratives | 4/4 | 0 |
| Deforestation Narratives | 4/4 | 0 |
| Error Handling | 3/3 | 0 |

---

## 1. Settlement Narrative API

### Endpoint: `/api/parks/{park_id}/settlement-narrative`

### Test Results

#### 1.1 COD_Virunga (High Settlements) ✅
```json
{
  "park_id": "COD_Virunga",
  "park_name": "Virunga",
  "summary": "Virunga contains 146 detected settlement(s) covering approximately 58.59 km² of built-up area. Further analysis with OSM place data can provide specific village and hamlet names.",
  "status": "complete"
}
```
**Assessment:** Meaningful narrative with realistic settlement count and area.

#### 1.2 CMR_Nki (Pristine - 0 Settlements) ✅
```json
{
  "park_id": "CMR_Nki",
  "park_name": "Nki",
  "summary": "No permanent settlements detected inside Nki boundaries. This suggests good protection from human encroachment.",
  "status": "complete"
}
```
**Assessment:** Correctly identifies pristine parks with no settlements and provides appropriate positive context.

#### 1.3 TZA_Serengeti (Medium) ✅
```json
{
  "park_id": "TZA_Serengeti",
  "park_name": "Serengeti",
  "summary": "Serengeti contains 212 detected settlement(s) covering approximately 33.44 km² of built-up area. Further analysis with OSM place data can provide specific village and hamlet names.",
  "status": "complete"
}
```
**Assessment:** Returns expected medium-level settlement data.

#### 1.4 ZAF_Kruger ✅
```json
{
  "park_id": "ZAF_Kruger",
  "park_name": "Kruger",
  "summary": "Kruger contains 240 detected settlement(s) covering approximately 176.75 km² of built-up area.",
  "status": "complete"
}
```
**Assessment:** Largest settlement area tested, correctly reported.

---

## 2. Fire Narrative API

### Endpoint: `/api/parks/{park_id}/fire-narrative`

### Test Results

#### 2.1 COD_Virunga (High Fire Activity) ✅
```json
{
  "park_id": "COD_Virunga",
  "park_name": "Virunga",
  "year": 2024,
  "summary": "In 2024, 49 fire group(s) entered Virunga. 44 group(s) stopped inside (possible ranger contact). 5 group(s) transited through without stopping.",
  "narratives": [/* 49 detailed fire group narratives */],
  "key_places": [/* 20 nearby reference places */]
}
```
**Assessment:** Excellent detail including:
- Individual fire group tracking (49 groups)
- Entry/exit dates and coordinates
- Days burning inside park
- Number of fire detections per group
- Rivers crossed during fire movement
- Outcome classification (STOPPED_INSIDE vs TRANSITED)
- Nearby village/town references (e.g., "near Bwera", "12 km west-southwest of Bihundo")

**Notable finding:** Fire group 35 had 2,914 fire detections over 45 days - the most intense.

#### 2.2 ZAF_Kruger ✅
```json
{
  "park_id": "ZAF_Kruger",
  "park_name": "Kruger",
  "year": 2024,
  "summary": "In 2024, 16 fire group(s) entered Kruger. 8 group(s) stopped inside (possible ranger contact). 8 group(s) transited through without stopping.",
  "narratives": [/* 16 detailed fire group narratives */]
}
```
**Assessment:** Valid response but uses coordinate-based descriptions (e.g., "(-24.720°, 31.674°)") instead of place names.

**Note:** `key_places` is null for Kruger - place data may not be loaded for this park.

#### 2.3 GAB_Loango (No Fire Data) ✅
```json
{
  "park_id": "GAB_Loango",
  "park_name": "Loango",
  "year": 0,
  "summary": "No significant fire group incursions recorded for Loango.",
  "narratives": null,
  "key_places": null
}
```
**Assessment:** Correctly handles parks with no fire activity.

#### 2.4 CMR_Nki (No Fire Data) ✅
```json
{
  "park_id": "CMR_Nki",
  "summary": "No significant fire group incursions recorded for Nki.",
  "narratives": null
}
```
**Assessment:** Correctly reports no fire incursions.

---

## 3. Deforestation Narrative API

### Endpoint: `/api/parks/{park_id}/deforestation-narrative`

### Test Results

#### 3.1 COD_Virunga (High Deforestation) ✅
```json
{
  "park_id": "COD_Virunga",
  "park_name": "Virunga",
  "summary": "Virunga has experienced 439.88 km² of forest loss across 24 recorded years. The worst year was 2018 with 54.18 km² lost.",
  "yearly_stories": [/* 24 yearly narratives from 2001-2024 */],
  "total_loss_km2": 439.8826,
  "worst_year": 2018
}
```
**Assessment:** Excellent detail including:
- Year-by-year breakdown of forest loss
- Pattern classification (scattered, minor, etc.)
- Nearby place references (villages, rivers)
- Pattern interpretation ("consistent with smallholder agricultural expansion")

#### 3.2 GAB_Loango (Low Deforestation) ✅
```json
{
  "park_id": "GAB_Loango",
  "park_name": "Loango",
  "summary": "Loango has experienced 8.23 km² of forest loss across 24 recorded years. The worst year was 2013 with 1.77 km² lost.",
  "yearly_stories": [/* 24 yearly narratives */],
  "total_loss_km2": 8.2281,
  "worst_year": 2013
}
```
**Assessment:** Correctly reports much lower deforestation than Virunga with detailed yearly breakdowns.

#### 3.3 TZA_Serengeti (Savanna Park with Minimal Forest) ✅
```json
{
  "park_id": "TZA_Serengeti",
  "park_name": "Serengeti",
  "summary": "Serengeti has experienced 8.53 km² of forest loss across 24 recorded years. The worst year was 2013 with 2.87 km² lost.",
  "yearly_stories": [/* 24 yearly narratives */],
  "total_loss_km2": 8.525,
  "worst_year": 2013
}
```
**Assessment:** Valid data returned. Note that `nearby_places` is null for Serengeti yearly stories - uses raw coordinates instead.

#### 3.4 CMR_Nki (Pristine Forest) ✅
```json
{
  "park_id": "CMR_Nki",
  "summary": "Nki has experienced 1.77 km² of forest loss across 23 recorded years. The worst year was 2009 with 0.35 km² lost.",
  "total_loss_km2": 1.7693999999999999
}
```
**Assessment:** Very low deforestation - confirms pristine nature of park.

---

## 4. Error Handling Tests

### Invalid Park ID Tests ✅

| Endpoint | Response | Assessment |
|----------|----------|------------|
| Settlement | `"summary": "Settlement analysis for INVALID_PARK is pending..."` | Graceful handling, indicates data not yet processed |
| Fire | `"summary": "No significant fire group incursions recorded for INVALID_PARK."` | Returns valid empty state |
| Deforestation | `"summary": "No significant deforestation events recorded for INVALID_PARK."` | Returns valid empty state |

**Note:** Invalid park IDs don't return 404 errors - they return empty data with the raw park ID echoed back. This is acceptable but could be improved with actual error responses.

---

## Issues & Recommendations

### Minor Issues

1. **Missing Place Data for Some Parks**
   - ZAF_Kruger fire narratives use raw coordinates instead of place names
   - TZA_Serengeti deforestation has no nearby_places
   - *Recommendation:* Ensure OSM place data is loaded for all parks

2. **Invalid Park ID Handling**
   - Invalid park IDs return empty data instead of 404 errors
   - Park name is echoed as-is ("INVALID_PARK" instead of lookup failure)
   - *Recommendation:* Return 404 with error message for unknown park IDs

3. **Grammar in Fire Narratives**
   - "Burned inside the park for 1 days" should be "1 day"
   - *Recommendation:* Add singular/plural handling for day counts

4. **Floating Point Precision**
   - `total_loss_km2: 1.7693999999999999` shows floating point artifacts
   - *Recommendation:* Round to 2-4 decimal places in JSON output

### Strengths

1. **Rich Contextual Narratives** - Fire group tracking with river crossings, bearings, and place references is excellent
2. **Consistent JSON Structure** - All endpoints return well-structured, predictable responses
3. **Pattern Analysis** - Deforestation patterns (scattered, minor) provide actionable insights
4. **Year-by-Year Tracking** - Historical data across 24 years provides trend analysis capability
5. **Ranger Intervention Hints** - Fire outcomes (STOPPED_INSIDE vs TRANSITED) are valuable for operational assessment

---

## Conclusion

**All narrative APIs are functioning correctly** and provide meaningful, actionable information for conservation monitoring. The APIs handle edge cases (no data, pristine parks) gracefully and return rich contextual narratives for parks with activity.

**Overall Grade: A-**

Minor improvements could address grammar, place data coverage, and error handling, but the core functionality is solid.
