# 5MP Conservation Monitoring - Database Schema

## Overview

SQLite database with ~1.5GB of conservation data.

**Download:** https://five-megapixel-conservation.exe.xyz:8000/static/downloads/five-megapixel-conservation_latest.sqlite3

---

## Core Tables

### fire_detections
Satellite fire detection data from NASA FIRMS (VIIRS sensor).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| latitude | REAL | Fire location |
| longitude | REAL | Fire location |
| brightness | REAL | Fire intensity |
| acq_date | TEXT | Acquisition date (YYYY-MM-DD) |
| acq_time | TEXT | Acquisition time |
| confidence | TEXT | Detection confidence |
| frp | REAL | Fire Radiative Power |
| protected_area_id | TEXT | Park ID if inside boundary |

**Records:** ~5M (as of Feb 2026)
**Source:** NASA FIRMS VIIRS NRT and historical

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
| event_type | TEXT | Classification |

**Records:** ~300
**Source:** Hansen Global Forest Change v1.10

### park_settlements
GHSL settlement/built-up area data.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| park_id | TEXT | Protected area ID |
| lat | REAL | Settlement centroid |
| lon | REAL | Settlement centroid |
| area_m2 | REAL | Built-up area |
| population_est | INTEGER | Estimated population |
| households_est | INTEGER | Estimated households |

**Records:** ~15,000
**Source:** GHSL GHS-BUILT-S 2018

### osm_places
OpenStreetMap place names for narrative context.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| name | TEXT | Place name |
| lat | REAL | Location |
| lon | REAL | Location |
| place_type | TEXT | city/town/village/hamlet |
| population | INTEGER | If available |

**Records:** ~10,600
**Source:** OpenStreetMap via Overpass API

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

### effort_data
Aggregated patrol effort by grid cell.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
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
| lat_min/max | REAL | Boundaries |
| lon_min/max | REAL | Boundaries |

---

## Analysis Tables

### fire_group_alerts
Real-time fire group tracking alerts.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| park_id | TEXT | Protected area |
| group_name | TEXT | NATO phonetic name |
| alert_type | TEXT | entered/active_inside/left |
| fire_count | INTEGER | Fires in group |
| days_active | INTEGER | Days burning |
| movement_direction | TEXT | N/S/E/W |
| lat/lon | REAL | Last known position |

### park_group_infractions
Historical fire infraction analysis.

| Column | Type | Description |
|--------|------|-------------|
| park_id | TEXT | Protected area |
| year | INTEGER | Year |
| total_groups | INTEGER | Fire groups detected |
| transhumance_groups | INTEGER | Long-range movement |
| herder_groups | INTEGER | Local herding pattern |

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

### gpx_learning_queue
Pattern learning queue.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| upload_id | INTEGER | FK to gpx_uploads |
| park_id | TEXT | Protected area |
| status | TEXT | pending/processing/completed/failed |

---

## Data Coverage by Park

### Fire Data (2018-2026)
All 162 parks have fire detection data.

### Deforestation (2018-2023)
~140 parks with measurable forest loss.

### Settlements
161 parks processed (1 park has no GHSL data).

### Parks with Full Coverage
| Park ID | Fire | Deforest | Settle | Roads |
|---------|------|----------|--------|-------|
| COD_Virunga | ✓ | ✓ | ✓ | ✓ |
| TZA_Serengeti | ✓ | ✓ | ✓ | ✓ |
| KEN_Masai_Mara | ✓ | ✓ | ✓ | ✓ |
| ... | | | | |

### Pristine Parks (No Settlements)
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
| Settlements | As needed | Manual script |
| Place names | As needed | Manual script |

---

## Useful Queries

### Fire count by park
```sql
SELECT protected_area_id, COUNT(*) as fires
FROM fire_detections
WHERE acq_date >= '2025-01-01'
GROUP BY protected_area_id
ORDER BY fires DESC
LIMIT 10;
```

### Recent fire activity
```sql
SELECT protected_area_id, acq_date, COUNT(*) as fires
FROM fire_detections
WHERE acq_date >= date('now', '-7 days')
GROUP BY protected_area_id, acq_date
ORDER BY acq_date DESC, fires DESC;
```

### Settlement density
```sql
SELECT park_id, COUNT(*) as settlements, SUM(population_est) as population
FROM park_settlements
GROUP BY park_id
ORDER BY population DESC
LIMIT 10;
```

### Patrol coverage
```sql
SELECT grid_cell_id, SUM(total_distance_km) as km
FROM effort_data
WHERE year = 2025
GROUP BY grid_cell_id
ORDER BY km DESC
LIMIT 20;
```
