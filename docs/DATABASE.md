# 5MP Conservation Monitoring - Database Schema

## Overview

SQLite database (~1.8GB) with conservation data for 162 African protected areas.

**Download:** https://five-megapixel-conservation.exe.xyz:8000/static/downloads/5mp_data_latest.sqlite3 (1.7 GB)  
**MD5:** https://five-megapixel-conservation.exe.xyz:8000/static/downloads/5mp_data_latest.sqlite3.md5  
**Checksum:** `4bc0e551691b2c3cea9afe02291b57f6`

**Last Updated:** 2026-02-06

---

## Database Statistics

| Table | Records | Description |
|-------|---------|-------------|
| fire_detections | 5,667,773 | VIIRS satellite fire detections |
| feature_geometries | 279,749 | GeoJSON for fires/settlements/deforestation/roads |
| park_settlements | 15,066 | Settlement centroids with population |
| deforestation_events | 3,218 | Forest loss polygons |
| osm_places | 10,600 | Place names for narratives |
| osm_roadless_data | 162 | Road networks by park |
| fire_group_alerts | 271 | Active fire group alerts |

---

## Core Tables

### fire_detections
Satellite fire detection data from NASA FIRMS (VIIRS sensor).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| latitude | REAL | Fire location |
| longitude | REAL | Fire location |
| brightness | REAL | Fire intensity (Kelvin) |
| acq_date | TEXT | Acquisition date (YYYY-MM-DD) |
| acq_time | TEXT | Acquisition time (HHMM) |
| satellite | TEXT | N=Suomi NPP, 1=NOAA-20, 2=NOAA-21 |
| confidence | TEXT | low/nominal/high |
| frp | REAL | Fire Radiative Power (MW) |
| daynight | TEXT | D=day, N=night |
| protected_area_id | TEXT | Park ID if inside boundary |

**Records:** 5,667,773  
**Date Range:** 2020-01-01 to present  
**Source:** NASA FIRMS VIIRS NRT and archive

### feature_geometries
Unified GeoJSON storage for all spatial features.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| feature_type | TEXT | fire_trajectory/deforestation/settlement/road |
| feature_id | TEXT | Unique feature reference |
| park_id | TEXT | Protected area ID |
| geojson | TEXT | Full GeoJSON geometry |
| bbox_minx/miny/maxx/maxy | REAL | Bounding box |
| start_date | TEXT | For time filtering |
| end_date | TEXT | For time filtering |
| properties_json | TEXT | Additional properties |

**Records by type:**
- fire_trajectory: 50,899
- deforestation: 153,980  
- settlement: 64,016
- road: 10,854

### park_settlements
GHSL settlement/built-up area data with population estimates.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| park_id | TEXT | Protected area ID |
| lat | REAL | Settlement centroid |
| lon | REAL | Settlement centroid |
| area_m2 | REAL | Built-up area (m²) |
| population_est | INTEGER | GHSL 2030 population estimate |
| households_est | INTEGER | Estimated households |
| nearest_place | TEXT | Nearest OSM place name |
| distance_to_place_km | REAL | Distance to nearest place |
| direction_from_place | TEXT | Cardinal direction |
| settlement_type | TEXT | temporary/permanent |
| in_buffer | INTEGER | 1 if in 10km buffer zone |

**Records:** 15,066  
**Source:** GHSL GHS-BUILT-S 2018 + GHS-POP 2030

### deforestation_events
Hansen Global Forest Change data.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| park_id | TEXT | Protected area ID |
| year | INTEGER | Year of forest loss |
| area_km2 | REAL | Area lost |
| lat | REAL | Centroid latitude |
| lon | REAL | Centroid longitude |
| geojson | TEXT | Polygon geometry |
| event_type | TEXT | Classification |
| pattern_type | TEXT | clearing/road/encroachment |

**Records:** 3,218  
**Source:** Hansen Global Forest Change v1.10

### osm_places
OpenStreetMap place names for narrative context.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| park_id | TEXT | Protected area ID |
| name | TEXT | Place name |
| lat | REAL | Location |
| lon | REAL | Location |
| place_type | TEXT | city/town/village/hamlet |
| geojson | TEXT | Point geometry (optional) |
| osm_id | TEXT | OpenStreetMap ID |

**Records:** 10,600  
**Source:** OpenStreetMap via Overpass API

### osm_roadless_data
Road network analysis by park.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| park_id | TEXT | Protected area ID |
| total_area_km2 | REAL | Park area |
| roaded_area_km2 | REAL | Area within 1km of roads |
| roadless_area_km2 | REAL | Roadless area |
| roadless_percentage | REAL | % roadless |
| road_length_km | REAL | Total road length |
| road_density_km_per_km2 | REAL | Road density |
| roads_json | TEXT | Road LineStrings GeoJSON |
| buffer_roads_json | TEXT | Buffer zone roads |

**Records:** 162 (all parks)  
**Source:** OpenStreetMap

---

## Alert & Analysis Tables

### fire_group_alerts
Real-time fire group tracking alerts.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| park_id | TEXT | Protected area |
| group_name | TEXT | NATO phonetic name (Alpha, Bravo...) |
| alert_type | TEXT | entered/active_inside/left |
| first_detected_at | TIMESTAMP | First detection |
| fire_count | INTEGER | Fires in group |
| days_active | INTEGER | Days burning |
| movement_direction | TEXT | N/S/E/W/stationary |
| centroid_lat/lon | REAL | Group center |
| latest_lat/lon | REAL | Last known position |
| is_dismissed | BOOLEAN | User dismissed |

### park_group_infractions
Historical fire pattern analysis.

| Column | Type | Description |
|--------|------|-------------|
| park_id | TEXT | Protected area |
| year | INTEGER | Year |
| total_groups | INTEGER | Fire groups detected |
| transhumance_groups | INTEGER | Long-range movement |
| herder_groups | INTEGER | Local herding pattern |
| stationary_groups | INTEGER | Fixed location fires |

---

## Patrol/Effort Tables

### gpx_uploads
Uploaded GPX track files.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| user_id | TEXT | Uploader ID |
| filename | TEXT | Original filename |
| file_hash | TEXT | SHA256 for deduplication |
| upload_date | TIMESTAMP | Upload time |
| total_distance_km | REAL | Track distance |
| movement_type | TEXT | foot/vehicle/aircraft |
| park_id | TEXT | Detected park |

### effort_data
Aggregated patrol effort by grid cell.

| Column | Type | Description |
|--------|------|-------------|
| grid_cell_id | TEXT | Grid cell reference |
| year | INTEGER | Year |
| month | INTEGER | Month (1-12) |
| movement_type | TEXT | foot/vehicle/aircraft/all |
| total_distance_km | REAL | Total distance |
| total_points | INTEGER | GPS point count |
| unique_uploads | INTEGER | Upload count |

### grid_cells
0.1° (~10km) grid cells for effort visualization.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT | Cell ID (e.g., "-0.5_29.4") |
| lat_center | REAL | Center latitude |
| lon_center | REAL | Center longitude |

---

## Queue Tables

### upload_queue
Async upload processing queue.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| user_id | TEXT | Uploader |
| filename | TEXT | Filename |
| file_content | BLOB | Raw GPX data |
| status | TEXT | pending/processing/completed/failed |
| result_json | TEXT | Processing result |

---

## Parks with No Settlements (Pristine)

- CMR_Nki
- COG_Nouabalé-Ndoki  
- GAB_Monts_Birougou
- GAB_Plateaux_Baték
- KEN_Sibiloi
- TZA_Rungwa
- TZA_Ugalla

---

## Data Refresh Schedule

| Data | Frequency | Method |
|------|-----------|--------|
| Fire (NRT) | Daily 3am UTC | Systemd timer |
| Fire (backfill) | Daily 4am UTC | Systemd timer |
| Deforestation | Annual | Manual script |
| Settlements/Pop | As needed | Manual script |
| Place names | As needed | Manual script |
| Roads | As needed | Manual script |

---

## Useful Queries

### Fire count by park (last 30 days)
```sql
SELECT protected_area_id, COUNT(*) as fires
FROM fire_detections
WHERE acq_date >= date('now', '-30 days')
GROUP BY protected_area_id
ORDER BY fires DESC
LIMIT 10;
```

### Settlement population by park
```sql
SELECT park_id, COUNT(*) as settlements, SUM(population_est) as population
FROM park_settlements
GROUP BY park_id
ORDER BY population DESC
LIMIT 10;
```

### Feature geometry counts
```sql
SELECT feature_type, COUNT(*) as count
FROM feature_geometries
GROUP BY feature_type;
```

### Recent fire trajectories for a park
```sql
SELECT feature_id, start_date, end_date, properties_json
FROM feature_geometries
WHERE park_id = 'COD_Virunga' 
  AND feature_type = 'fire_trajectory'
  AND end_date >= date('now', '-7 days')
ORDER BY end_date DESC;
```

### Roadless percentage ranking
```sql
SELECT park_id, roadless_percentage, road_density_km_per_km2
FROM osm_roadless_data
ORDER BY roadless_percentage DESC
LIMIT 10;
```
