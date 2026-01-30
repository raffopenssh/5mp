# Park Stats API Test Report

**Date:** 2026-01-30  
**Endpoint:** `GET /api/parks/{id}/stats?pwd=test2026`

## Executive Summary

The park stats API provides fire incident analysis with insights, but has **significant gaps** in other data types (deforestation, settlements, roadless) and **data consistency issues**.

---

## Test Results by Park Type

### 1. COD_Virunga (High-threat park with conflict)
**Status:** ⚠️ Partial Data

| Metric | Present | Value |
|--------|---------|-------|
| Fire stats | ✅ | 9 groups entered, 89% response rate |
| Fire timeline | ✅ | 90 days of data |
| Fire trend | ⚠️ | Years present but `total_fires` always 0 |
| Settlement | ❌ | Missing (data exists in DB) |
| Roadless | ❌ | No data in DB |
| Deforestation | ❌ | Not implemented (data exists!) |
| Insights | ✅ | 4 narrative insights |

### 2. TZA_Selous (Large park)
**Status:** ⚠️ Partial Data

| Metric | Present | Value |
|--------|---------|-------|
| Fire stats | ✅ | 5 groups, 80% response rate |
| Fire timeline | ✅ | Present |
| Fire trend | ⚠️ | `total_fires` always 0 |
| Settlement | ❌ | Missing |
| Roadless | ❌ | No data |
| Deforestation | ❌ | Not implemented |

### 3. RWA_Nyungwe (Small forest park)
**Status:** ❌ Minimal Data

```json
{"park_id":"RWA_Nyungwe"}
```

Only returns park_id - no fires, no other metrics. This is a valid "pristine" park with 0 fire detections.

### 4. CMR_Nki (Pristine rainforest)
**Status:** ❌ Minimal Data

```json
{"park_id":"CMR_Nki"}
```

Same as Nyungwe - no data returned.

### 5. AGO_Cameia (Has roadless data)
**Status:** ⚠️ Good but incomplete

| Metric | Present | Value |
|--------|---------|-------|
| Fire stats | ✅ | Present |
| Roadless | ✅ | 93.8% roadless, 1537 km roads |
| Settlement | ❌ | Column mismatch bug |

---

## Issues Identified

### 🔴 Critical: Column Name Mismatch (Settlement Data)

**Problem:** The Go code queries for `built_up_km2` but the SQLite column is `built_up_area_km2`.

```go
// park_stats_handlers.go line 255
SELECT built_up_km2, settlement_count  // WRONG column name
```

**Database schema:**
```sql
built_up_area_km2 REAL DEFAULT 0,  -- Actual column name
```

**Impact:** Settlement data never returned even when present (155 records in DB).

**Fix:**
```go
SELECT built_up_area_km2, settlement_count
```

---

### 🔴 Critical: Deforestation Data Not Implemented

**Problem:** The API doesn't query or return deforestation statistics, even though significant data exists:
- 24+ years of data per park
- COD_Virunga: 22.9 km² lost in 2024 alone
- Pattern analysis (scattered, cluster, strip, edge)
- Narrative descriptions included

**Recommendation:** Add `Deforestation` field to `ParkStats` struct.

---

### 🟡 Medium: Fire Trend Shows total_fires=0

**Problem:** The `fire_trend` array shows `total_fires: 0` for all years.

```json
"fire_trend": [
  {"year": 2018, "total_fires": 0, "groups": 4},
  {"year": 2019, "total_fires": 0, "groups": 11}
]
```

The query doesn't populate total_fires - it's not in the `park_group_infractions` table.

**Fix:** Either join with fire_detections count per year, or remove the misleading field.

---

### 🟡 Medium: Parks with No Data Return Minimal Response

**Problem:** Parks without fire data (RWA_Nyungwe, CMR_Nki) return only `{"park_id":"..."}`.

**Impact:** Ministry staff may think the API failed rather than park being pristine.

**Recommendation:** Return explicit null/empty values or a status indicating "no threats detected":
```json
{
  "park_id": "RWA_Nyungwe",
  "status": "pristine",
  "fire": null,
  "settlement": null,
  "message": "No fire incidents or threats detected for this park."
}
```

---

### 🟡 Medium: Limited Roadless Data Coverage

Only 5 parks have OSM roadless data:
- AGO_Cameia
- AGO_Iona
- AGO_Luando
- AGO_Luengue-Luiana
- BEN_Pendjari

---

## What Ministry Staff Would Want

### Currently Missing Operational Metrics:

1. **Deforestation trends** - Critical for Congo Basin countries
2. **Year-over-year comparisons** - Is the park getting worse?
3. **Alert status** - Simple red/yellow/green for dashboard
4. **Ranger effort correlation** - Do patrols correlate with response rates?
5. **Threat severity score** - Single number for prioritization
6. **Legal protection status** - Gazette date, IUCN category
7. **Research activity** - Publications count (564 in DB, not exposed)
8. **Settlement growth trend** - Is encroachment increasing?

### Currently Provided (Good):
- Fire group behavior analysis
- Response rate calculation
- Peak month identification
- Narrative insights
- Historical fire timeline

---

## Redundant Fields

| Field | Issue |
|-------|-------|
| `fire_trend.total_fires` | Always 0, misleading |
| `fire.trajectories` | Large payload, rarely needed in stats endpoint |

**Recommendation:** Move trajectories to separate `/fire-trajectories` endpoint.

---

## Summary of Required Fixes

| Priority | Issue | Effort |
|----------|-------|--------|
| 🔴 P0 | Fix `built_up_km2` → `built_up_area_km2` column name | 1 line |
| 🔴 P0 | Add deforestation stats to response | 30 lines |
| 🟡 P1 | Fix or remove fire_trend.total_fires | 5 lines |
| 🟡 P1 | Add "pristine park" messaging | 10 lines |
| 🟢 P2 | Add threat severity score | 20 lines |
| 🟢 P2 | Add year-over-year change metrics | 40 lines |

---

## Recommendations

1. **Immediate:** Fix column name bug to enable settlement data
2. **Short-term:** Integrate existing deforestation data
3. **Medium-term:** Add computed threat severity index
4. **Long-term:** Add publication/research stats, legal status
