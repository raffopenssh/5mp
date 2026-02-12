# 5MP Conservation Monitoring - Database Schema

## Overview

SQLite database (~1.8GB) with conservation data for 162 African protected areas.

**Download:** https://five-megapixel-conservation.exe.xyz:8000/static/downloads/5mp_data_latest.sqlite3 (1.7 GB)  
**MD5:** https://five-megapixel-conservation.exe.xyz:8000/static/downloads/5mp_data_latest.sqlite3.md5  

**Last Updated:** 2026-02-12

---

## Database Statistics

| Table | Records | Description |
|-------|---------|-------------|
| fire_detections | 5,667,773 | VIIRS satellite fire detections (2018-2026) |
| feature_geometries | 279,749 | GeoJSON for fires/settlements/deforestation/roads |
| park_settlements | 15,066 | Settlement centroids with population |
| deforestation_events | 3,218 | Forest loss polygons |
| osm_places | 10,600 | Place names for narratives |
| park_climate | 162 | Monthly precipitation and temperature |
| park_species | 39,489 | IUCN mammal species |
| park_waterbodies | 2,573 | Lake/reservoir polygons |
| osm_roadless_data | 162 | Road networks by park |
| fire_group_alerts | ~270 | Active fire group alerts |
| fire_narrative_cache | 162 | Pre-computed fire narratives |

**Feature geometries breakdown:**
- fire_trajectory: 50,899 (years 2018-2024)
- deforestation: 153,980 (years 2001-2024)
- settlement: 64,016
- road: 10,854

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
**Date Range:** 2018-04-01 to 2026-02-05  
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
- fire_trajectory: 50,899 (years 2018-2024)
- deforestation: 153,980 (years 2001-2024)
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
| classification | TEXT | AI classification (mining/village/camp) |
| classification_confidence | REAL | Confidence score |
| narrative | TEXT | Generated narrative text |
| in_buffer | INTEGER | 1 if in 10km buffer zone |

**Records:** 15,066  
**Source:** GHSL GHS-BUILT-S 2018 + GHS-POP 2030

### deforestation_events
Hansen Global Forest Change data.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| park_id | TEXT | Protected area ID |
| year | INTEGER | Year of forest loss (2001-2024) |
| area_km2 | REAL | Area lost |
| lat | REAL | Centroid latitude |
| lon | REAL | Centroid longitude |
| geojson | TEXT | Polygon geometry |
| event_type | TEXT | Classification |
| pattern_type | TEXT | clearing/road/encroachment |
| classification | TEXT | AI classification |
| classification_confidence | REAL | Confidence score |
| narrative | TEXT | Generated narrative text |

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
| place_type | TEXT | city/town/village/hamlet/river/stream |
| geojson | TEXT | Point geometry (optional) |
| osm_id | TEXT | OpenStreetMap ID |

**Records:** 10,600  
**Source:** OpenStreetMap via Overpass API

**Place types:**
- city: 14
- town: 133
- village: 4,772
- hamlet: 2,280
- river: 3,360
- stream: 40
- lake: 1

### park_climate
WorldClim monthly climate data for seasonal context.

| Column | Type | Description |
|--------|------|-------------|
| park_id | TEXT | Primary key |
| temp_annual_c | REAL | Annual mean temperature |
| temp_max_c | REAL | Max temp (warmest month) |
| temp_min_c | REAL | Min temp (coldest month) |
| precip_annual_mm | INTEGER | Annual precipitation |
| precip_wettest_mm | INTEGER | Wettest month (mm) |
| precip_driest_mm | INTEGER | Driest month (mm) |
| climate_zone | TEXT | Tropical/Subtropical/Arid |
| rainy_season | TEXT | e.g., "Jun-Sep" |
| dry_season | TEXT | e.g., "Dec-Feb" |
| monthly_precip | TEXT | JSON array of 12 monthly values |

**Records:** 162  
**Source:** WorldClim 2.1 (2.5 arc-min resolution)

### park_species
IUCN Red List mammal species.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| park_id | TEXT | Protected area ID |
| binomial | TEXT | Scientific name |
| common_name | TEXT | English common name |
| status | TEXT | CR/EN/VU/NT/LC/DD |
| species_order | TEXT | Taxonomic order |
| family | TEXT | Taxonomic family |

**Records:** 39,489  
**Source:** IUCN Red List (2017 version)

**Conservation status codes:**
- CR: Critically Endangered (14)
- EN: Endangered (48)
- VU: Vulnerable (65)
- NT: Near Threatened (56)
- LC: Least Concern (679)
- DD: Data Deficient (88)

### park_waterbodies
Global waterbody polygons.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| park_id | TEXT | Protected area ID |
| waterbody_id | TEXT | Unique waterbody reference |
| name | TEXT | Waterbody name (if named) |
| waterbody_type | TEXT | Inland perennial/intermittent |
| lat | REAL | Centroid latitude |
| lon | REAL | Centroid longitude |
| geojson | TEXT | Polygon geometry |

**Records:** 2,573  
**Source:** Global waterbodies dataset

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

### fire_narrative_cache
Pre-computed fire narratives for fast loading.

| Column | Type | Description |
|--------|------|-------------|
| park_id | TEXT | Primary key |
| narrative_json | TEXT | Full narrative data |
| computed_at | TIMESTAMP | Cache timestamp |
| from_year | INTEGER | Data start year |
| to_year | INTEGER | Data end year |

**Records:** 162  
**Refresh:** Weekly (Sunday 2am UTC)

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
| trajectories_json | TEXT | Full trajectory data |

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

## JSON Data Files

Additional data is stored in JSON files for production sync:

| Directory | Count | Description |
|-----------|-------|-------------|
| `data/fire_analysis/` | 161 | Fire groups by year with trajectories |
| `data/fire_trajectories/` | 153 | Enhanced trajectories with context |
| `data/settlement_events/` | 156 | Classified settlement data |
| `data/deforestation_events/` | 79 | Classified deforestation events |
| `data/rivers/` | 161 | HydroRIVERS data per park |
| `data/roads_heigit/` | 159 | Road surface data |
| `data/osm_places/` | 91 | OSM place names |
| `data/climate/` | 1 | Monthly precipitation/seasons |
| `data/species/` | 1 | IUCN mammal species |
| `data/waterbodies/` | 137 | Waterbody polygons |
| `data/export/` | 7 | Pre-computed narratives |

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
| Fire narratives | Weekly Sunday 2am | Systemd timer |
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

### Fire trajectories by year
```sql
SELECT SUBSTR(start_date, 1, 4) as year, COUNT(*) as trajectories
FROM feature_geometries
WHERE feature_type = 'fire_trajectory'
GROUP BY year
ORDER BY year;
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

### Threatened species by park
```sql
SELECT park_id, status, COUNT(*) as species
FROM park_species
WHERE status IN ('CR', 'EN', 'VU')
GROUP BY park_id, status
ORDER BY park_id, status;
```

### Climate data
```sql
SELECT park_id, temp_annual_c, precip_annual_mm, 
       climate_zone, dry_season, rainy_season
FROM park_climate
WHERE park_id = 'CAF_Chinko';
```

### Roadless percentage ranking
```sql
SELECT park_id, roadless_percentage, road_density_km_per_km2
FROM osm_roadless_data
ORDER BY roadless_percentage DESC
LIMIT 10;
```
