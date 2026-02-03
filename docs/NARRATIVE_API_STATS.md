# Narrative API - Stats-Only Format

The narrative API endpoints now support a `format=stats` query parameter for a streamlined, stats-only response format with geojson_id references.

## Endpoints

### 1. Fire Narrative
```
GET /api/parks/{id}/fire-narrative?format=stats&start=2024-01-01&end=2024-12-31
```

**Response:**
```json
{
  "park_id": "CAF_Chinko",
  "park_name": "Chinko",
  "stats": {
    "total_trajectories": 58,
    "total_fires": 7607,
    "stopped_inside": 42,
    "transited": 16,
    "response_rate": 72.4,
    "avg_days_burning": 5.71,
    "peak_month": "December",
    "year_range": "2024",
    "by_outcome": {"STOPPED_INSIDE": 42, "TRANSITED": 16},
    "by_year": {"2024": 58}
  },
  "features": [
    {
      "id": "CAF_Chinko_2024_grp_58",
      "geojson_id": 7132,
      "year": 2024,
      "group_num": 58,
      "outcome": "STOPPED_INSIDE",
      "fires_inside": 36,
      "days_inside": 4,
      "entry_date": "2024-12-24",
      "last_inside": "2024-12-27",
      "origin_lat": 6.6658,
      "origin_lon": 24.1336,
      "dest_lat": 6.6138,
      "dest_lon": 24.1627
    }
  ]
}
```

**Date Filter Parameters:**
- `start`: Start date (YYYY-MM-DD) or year (YYYY)
- `end`: End date (YYYY-MM-DD) or year (YYYY)
- Also accepts `from`/`to` as aliases

---

### 2. Settlement Narrative
```
GET /api/parks/{id}/settlement-narrative?format=stats
```

**Response:**
```json
{
  "park_id": "CAF_Chinko",
  "park_name": "Chinko",
  "stats": {
    "settlement_count": 3,
    "total_area_km2": 0.29,
    "population_2030": 2052,
    "population_est": 30160,
    "by_classification": {"unclassified": 2, "village": 1},
    "avg_area_m2": 96666.67,
    "avg_distance_to_road_m": 92095.46
  },
  "features": [
    {
      "id": "settlement_15977",
      "geojson_id": 63005,
      "classification": "unclassified",
      "area_m2": 140000,
      "population_est": 14560,
      "population_2030": 840,
      "lat": 5.112453,
      "lon": 24.462369,
      "nearest_place": "Dembia",
      "distance_to_road_m": 137621.26
    }
  ]
}
```

---

### 3. Deforestation Narrative
```
GET /api/parks/{id}/deforestation-narrative?format=stats&start=2020&end=2024
```

**Response:**
```json
{
  "park_id": "CAF_Chinko",
  "park_name": "Chinko",
  "stats": {
    "total_events": 5,
    "total_area_km2": 1.89,
    "year_range": "2020-2024",
    "worst_year": 2021,
    "worst_year_area_km2": 1.16,
    "trend_direction": "improving",
    "by_classification": {"natural_disturbance": 17},
    "by_year": {"2020": 0.05, "2021": 1.16, "2022": 0.01, "2023": 0.59, "2024": 0.09}
  },
  "features": [
    {
      "id": "deforestation_371",
      "geojson_id": 77411,
      "year": 2024,
      "classification": "natural_disturbance",
      "area_km2": 0.0857,
      "lat": 5.99015,
      "lon": 23.85603,
      "pattern_type": "scattered",
      "distance_to_road_m": 29595.17,
      "distance_to_settlement_m": 35077.81
    }
  ]
}
```

**Date Filter Parameters:**
- `start`: Start year (YYYY)
- `end`: End year (YYYY)
- Also accepts `from`/`to` as aliases

---

## Using geojson_id

The `geojson_id` in each feature references the `id` in the `feature_geometries` table. To fetch the actual GeoJSON:

```sql
SELECT geojson FROM feature_geometries WHERE id = 7132;
```

Or use the features API:
```
GET /api/parks/{id}/features?type=fire_trajectory
```

---

## Settlement Classifications

Available classifications:
- `hamlet` - Small clustered settlement, >2km from roads
- `village` - Larger settlement with road access
- `roadside_settlement` - Linear settlement along road
- `sawmill_compound` - Large industrial footprint near roads
- `logging_camp` - Near forest edge, near track/tertiary road
- `fishing_camp` - Near water bodies
- `unclassified` - Default

## Deforestation Classifications

Available classifications:
- `natural_disturbance` - No nearby roads/settlements
- `selective_logging` - Scattered gaps, near logging roads
- `slash_and_burn` - Near settlements, seasonal pattern
- `smallholder_farming` - Small patches near villages
- `agricultural_expansion` - Incremental growth near settlements
- `charcoal_production` - 2-10km from road, clustered
- `artisanal_mining` - Near rivers, irregular shape
- `road_clearing` - Linear, parallel to road
- `infrastructure_clearing` - Near roads/settlements
- `unclassified` - Default
