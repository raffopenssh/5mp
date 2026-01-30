# Park Stats API Test Report

**Date:** 2025-01-30  
**Endpoint:** `/api/parks/{park_id}/stats`  
**Authentication:** Cookie `access_pwd=ngi2026`

## Summary

All core statistics (fire counts, settlements, roadless data) match correctly between API responses and database queries. Two data types are present in the database but not exposed by the API.

## Test Results by Park

### 1. COD_Virunga

| Metric | API Value | Database Value | Status |
|--------|-----------|----------------|--------|
| Fire Count | 55,782 | 55,782 | ✅ Match |
| Fire Date Range | 2024 (year field) | 2018-04-02 to 2024-12-31 | ✅ OK |
| Peak Fire Month | July | - | ✅ Present |
| Settlement Count | 146 | 146 | ✅ Match |
| Built-up Area (km²) | 58.59 | 58.59 | ✅ Match |
| Roadless % | 86.6 | 86.6 | ✅ Match |
| Total Road km | 38,623.21 | 38,623.21 | ✅ Match |
| Deforestation | null | 439.88 km² (24 years) | ⚠️ Not exposed |
| Legal Framework | null | 1 document | ⚠️ Not exposed |

### 2. TZA_Serengeti

| Metric | API Value | Database Value | Status |
|--------|-----------|----------------|--------|
| Fire Count | 62,808 | 62,808 | ✅ Match |
| Fire Date Range | 2024 (year field) | 2018-06-09 to 2024-12-28 | ✅ OK |
| Peak Fire Month | July | - | ✅ Present |
| Settlement Count | 212 | 212 | ✅ Match |
| Built-up Area (km²) | 33.44 | 33.44 | ✅ Match |
| Roadless % | 79.2 | 79.2 | ✅ Match |
| Total Road km | 50,594.23 | 50,594.23 | ✅ Match |
| Deforestation | null | 8.53 km² (24 years) | ⚠️ Not exposed |
| Legal Framework | null | - | ⚠️ Not exposed |

### 3. ZAF_Kruger

| Metric | API Value | Database Value | Status |
|--------|-----------|----------------|--------|
| Fire Count | 31,895 | 31,895 | ✅ Match |
| Fire Date Range | 2024 (year field) | 2018-05-28 to 2024-12-17 | ✅ OK |
| Peak Fire Month | September | - | ✅ Present |
| Settlement Count | 240 | 240 | ✅ Match |
| Built-up Area (km²) | 176.75 | 176.75 | ✅ Match |
| Roadless % | 75.3 | 75.3 | ✅ Match |
| Total Road km | 26,632.73 | 26,632.73 | ✅ Match |
| Deforestation | null | 0 km² | ✅ Correct (no data) |
| Legal Framework | null | - | ⚠️ Not exposed |

### 4. CMR_Nki (Pristine Park)

| Metric | API Value | Database Value | Status |
|--------|-----------|----------------|--------|
| Fire Count | 0 (no fire object) | 0 | ✅ Match |
| Settlement Count | 0 | 0 | ✅ Match |
| Built-up Area (km²) | 0 | 0 | ✅ Match |
| Roadless % | 100 | 100.0 | ✅ Match |
| Total Road km | 328.94 | 328.94 | ✅ Match |
| Deforestation | null | 1.77 km² (23 years) | ⚠️ Not exposed |
| Legal Framework | null | - | ⚠️ Not exposed |

**Note:** CMR_Nki correctly represents a pristine park with no settlements and 100% roadless wilderness.

### 5. GAB_Loango

| Metric | API Value | Database Value | Status |
|--------|-----------|----------------|--------|
| Fire Count | 54 (from timeline) | 54 | ✅ Match |
| Fire Date Range | 2019-09-01 to 2024-08-27 | 2019-09-01 to 2024-08-27 | ✅ Match |
| Settlement Count | 6 | 6 | ✅ Match |
| Built-up Area (km²) | 282.14 | 282.14 | ✅ Match |
| Roadless % | 100 | 100.0 | ✅ Match |
| Total Road km | 535.22 | 535.22 | ✅ Match |
| Deforestation | null | 8.23 km² (24 years) | ⚠️ Not exposed |
| Legal Framework | null | - | ⚠️ Not exposed |

## API Response Structure

The stats endpoint returns these fields:
- `park_id` - Park identifier
- `fire` - Fire statistics (total_fires, peak_month, year, trajectories, response_rate)
- `fire_timeline` - Daily fire counts
- `fire_trend` - Yearly fire group counts
- `settlement` - Settlement count and built-up area
- `roadless` - Roadless percentage and total road length
- `insights` - Generated text insights about the park

## Issues Identified

### 1. Deforestation Data Not Exposed
**Severity:** Medium  
**Description:** The `deforestation_events` table contains data for all tested parks except ZAF_Kruger, but the API does not include this in the stats response.

**Database evidence:**
```
COD_Virunga: 439.88 km² total, 24 distinct years
TZA_Serengeti: 8.53 km² total, 24 distinct years  
CMR_Nki: 1.77 km² total, 23 distinct years
GAB_Loango: 8.23 km² total, 24 distinct years
```

### 2. Legal Framework Data Not Exposed
**Severity:** Low  
**Description:** The `park_documents` table contains legal documents (category='legal_document'), but this is not surfaced in the stats API.

## Conclusion

**Core functionality: PASS** - All fire, settlement, and roadless statistics are accurate and match database values exactly.

**Data completeness: PARTIAL** - Deforestation and legal framework data exist in the database but are not exposed through this endpoint.

## Database Queries Used for Verification

```sql
-- Fire count
SELECT COUNT(*) FROM fire_detections WHERE protected_area_id='{park_id}';

-- Settlement stats
SELECT COUNT(*), SUM(area_m2)/1000000.0 FROM park_settlements WHERE park_id='{park_id}';

-- Deforestation stats
SELECT SUM(area_km2), COUNT(DISTINCT year) FROM deforestation_events WHERE park_id='{park_id}';

-- Roadless stats
SELECT roadless_percentage, road_length_km FROM osm_roadless_data WHERE park_id='{park_id}';
```
