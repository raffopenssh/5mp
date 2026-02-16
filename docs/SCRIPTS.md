# Script Reference - 5MP Conservation Monitoring

## Setup
```bash
cd /home/exedev/5mpglobe
source .venv/bin/activate
```

## Data Processing Pipeline

Run these in order for a full rebuild:

### 1. Fire Analysis (from raw detections)
```bash
python scripts/rebuild_park_fire_analysis.py
```
- Input: `fire_detections` table (6M+ records)
- Output: `park_fire_analysis` table + `data/fire_analysis/*.json`
- Creates fire groups with trajectories and timestamps

### 2. Fire Trajectories (enhanced analysis)
```bash
python scripts/analyze_fire_trajectories_v3.py
```
- Input: `data/fire_analysis/*.json`
- Output: `data/fire_trajectories/*.json` + `feature_geometries` table
- Adds rivers, roads, places, settlements, climate context
- Uses REAL timestamps from trajectory points

### 3. Narratives (fire, settlement, deforestation)
```bash
python scripts/precompute_narratives_v3.py
```
- Input: All trajectory and event data
- Output: `data/export/*.json`
- Generates narrative text for each event

## Individual Data Scripts

### Rivers & Lakes (HydroRIVERS/HydroLAKES)
```bash
# Extract from source GDB files (requires download first)
python scripts/extract_hydro_data.py
```
- Requires: `data/hydro_source/HydroRIVERS_v10_af.gdb` and `HydroLAKES_polys_v10.gdb`
- Download from: https://data.hydrosheds.org/
- Output: `data/rivers_hydro/*.json` + `data/lakes_hydro/*.json`
- 50km buffer around each park, full geometry included

### Roads (HeiGIT)
```bash
python scripts/download_heigit_roads.py
```
- Downloads road surface data from HeiGIT/HDX
- Output: `data/roads_heigit/*.json`
- Includes surface type, passability, DL classification, geometry

### OSM Places
```bash
# Download from Overpass API
python scripts/download_osm_places_to_file.py
```
- Downloads villages, towns, rivers, lakes, mountains from OSM
- 50km buffer, retries failed parks
- Output: `data/osm_places/*.json`

### Update Hydro Names from OSM
```bash
python scripts/update_hydro_names.py
```
- Matches rivers/lakes to nearby OSM place names
- Updates `data/rivers_hydro/*.json` and `data/lakes_hydro/*.json`
- Run after downloading new OSM places

### Settlements & Deforestation (from polygons)
```bash
python scripts/rebuild_events_from_polygons.py
```
- Rebuilds both settlements and deforestation from polygon data
- **Settlements**: Clusters nearby polygons (2km threshold)
- **Deforestation**: Clusters nearby polygons (5km threshold), multiple events per year
- Output: `park_settlements` + `deforestation_events` tables
- Then export: `python scripts/export_events_from_db.py`

### Species (IUCN)
```bash
python scripts/load_json_data.py
```
- Loads species from JSON
- Output: `park_species` table

## Data Files Summary

| Directory | Files | Description |
|-----------|-------|-------------|
| `data/fire_analysis/` | 161 | Yearly fire groups with trajectories |
| `data/fire_trajectories/` | 153 | Enhanced trajectories (2018-2026) |
| `data/deforestation_events/` | 79 | Classified deforestation events |
| `data/settlement_events/` | 156 | Classified settlements |
| `data/roads_heigit/` | 161 | HeiGIT road data with geometry |
| `data/rivers_hydro/` | 161 | HydroRIVERS with geometry (50km buffer) |
| `data/lakes_hydro/` | 161 | HydroLAKES with geometry (50km buffer) |
| `data/osm_places/` | 161 | OSM place names (villages, rivers, etc.) |
| `data/climate/` | 1 | Monthly precipitation, seasons |
| `data/species/` | 1 | IUCN mammal species |
| `data/waterbodies/` | 137 | Global waterbody polygons |
| `data/feature_geometries/settlement/` | 156 | Settlement GeoJSON polygons |
| `data/feature_geometries/deforestation/` | 79 | Deforestation GeoJSON polygons |
| `data/export/` | 7 | Precomputed narratives |

---

## Production Deployment

### Quick Update (code only)
```bash
git pull --rebase
make build
sudo systemctl restart srv
```

**Note:** `make build` automatically:
- Sets version from `git rev-parse --short HEAD`
- Generates `.git-commits.txt` with last 20 commits (for version modal in UI)

### Full Data Import (after git pull with new JSON files)

Run the unified import script:
```bash
git pull --rebase
python3 scripts/import_json_to_db.py
make build
sudo systemctl restart srv
```

This script:
1. Syncs `feature_geometries` table with JSON files (exact match)
2. Imports `park_fire_analysis` from fire_analysis/*.json
3. Imports `park_group_infractions` from fire_trajectories/*.json (with computed outcomes)
4. Imports `osm_places` from osm_places/*.json  
5. Imports `park_climate` from climate/park_climate.json
6. Imports `park_waterbodies` from waterbodies/*.json
7. Verifies all counts match source files

**Note:** JSON files in `data/` are the source of truth. They were generated on the build VM and should not be regenerated locally unless fire_detections data changes.

### Complete Rebuild (regenerate from raw data)
```bash
# 1. Fire analysis (from fire_detections table)
python scripts/rebuild_park_fire_analysis.py

# 2. Trajectories (adds context from rivers, roads, places)
python scripts/analyze_fire_trajectories_v3.py

# 3. Narratives
python scripts/precompute_narratives_v3.py

# 4. Import to database
python3 scripts/import_json_to_db.py

# 5. Rebuild server
make build
sudo systemctl restart srv
```

## Import Scripts (for fresh database)

### Full JSON Import
```bash
python scripts/import_all_json_data.py
```
- Imports all JSON data into database tables
- Matches settlements by `(park_id, lat, lon)` - NOT by auto-increment ID
- Matches deforestation by `(park_id, year, lat, lon)` - multiple clusters per year
- Updates polygon_ids for UI polygon display

### Update Classifications Only
```bash
python scripts/update_classifications.py
```
- Updates classification/narrative fields without full reimport
- Settlements: Match by `(park_id, lat, lon)`
- Deforestation: Match by `(park_id, year, lat, lon)`

### Import Feature Geometries
```bash
python scripts/import_json_to_db.py settlements
python scripts/import_json_to_db.py deforestation
python scripts/import_json_to_db.py fire_trajectories
```
- Loads polygon geometries from `data/feature_geometries/`
- Applies classifications from `data/*_events/*.json`

## Export Scripts

### Export Events from Database
```bash
python scripts/export_events_from_db.py
```
- Exports `park_settlements` → `data/settlement_events/*.json`
- Exports `deforestation_events` → `data/deforestation_events/*.json`
- Includes polygon_ids for feature_geometries linking

## Important: ID Matching

## Enhanced Event Rebuild (with road detection)

```bash
python scripts/rebuild_events_enhanced.py
```

This enhanced script rebuilds both settlements and deforestation with:

**Data sources used:**
- `park_rivers_hydro` - HydroRIVERS with geometry (50km buffer)
- `park_lakes_hydro` - HydroLAKES with geometry (50km buffer)
- `osm_places` - OSM villages, towns, rivers, mountains
- `roads_heigit` - HeiGIT road geometries for linear pattern detection
- `park_climate` - Seasonality data

**Linear road detection:**
- Checks if >60% of deforestation polygons are within 500m of a road
- Classifies as `logging` with `linear_road` pattern when detected
- Includes nearest road name in narrative

**Output:**
- Updates `deforestation_events` and `park_settlements` tables
- Exports to `data/deforestation_events/*.json` and `data/settlement_events/*.json`

**Classifications:**
- **Deforestation**: slash_burn, logging (with linear/linear_road patterns), encroachment, natural
- **Settlements**: town, village, agricultural, temporary_camp, settlement

### Load new hydro/OSM data before rebuild

```bash
# Import all JSON data to database (rivers, lakes, places, roads)
python scripts/import_all_json_data.py

# Then rebuild events with enhanced classification
python scripts/rebuild_events_enhanced.py
```

The database uses auto-increment IDs for `park_settlements.id` and 
`deforestation_events.id`. These IDs can differ between database instances
if data is imported in different orders.

**DO NOT match by auto-increment ID when importing.** Instead:
- Settlements: Match by `(park_id, lat, lon)` coordinates
- Deforestation: Match by `(park_id, year, lat, lon)` - multiple clusters per year
- Fire trajectories: Use `feature_id` which is consistent

The `polygon_ids` field links events to `feature_geometries` entries.
Always ensure this field is populated for UI polygon display.

## Spatial Clustering

Both settlements and deforestation use spatial clustering to group nearby polygons into events.

| Type | Cluster Distance | Events per Park |
|------|------------------|------------------|
| Settlements | 2km | ~74 avg |
| Deforestation | 5km | ~204 avg (multiple per year) |

### Rebuild from scratch
```bash
# Both settlements and deforestation:
python scripts/rebuild_events_from_polygons.py

# Or deforestation only:
python scripts/rebuild_deforestation_clusters.py
```

### Deforestation Schema
The `UNIQUE(park_id, year)` constraint was removed. Each distinct spatial cluster within a year is a separate event.

### Classifications
- **Deforestation**: slash_burn, logging, encroachment, natural, agricultural_clearing
- **Settlements**: town, village, compound, agricultural, pastoral, temporary_camp

### Statistics
- Settlements: 11,559 records across 156 parks
- Deforestation: 16,119 events across 79 parks (avg 11.5 per park-year)

### Import/Export
- Export: `python scripts/export_events_from_db.py`
- Import: Match by coordinates with 0.0001° tolerance

### Fire Groups v2 (Cross-park clustering)
```bash
python scripts/rebuild_park_fire_analysis_v2.py
```
- **Cross-park/country aware**: Clusters fires globally, not per-park
- **Cross-chunk stitching**: Handles geographic chunk boundaries
- **Memory-safe batching**: Processes in 400k group batches
- Input: `fire_detections` table + `data/fire_additional_buffer/*.json`
- Output: `data/fire_groups_v2/*.json` + `park_fire_weekly` table
- Includes: trajectory with timestamps, affected_parks list, cross_border flag
- ~1M groups, 25% cross-border

### Fire Buffer Backfill (50km zone)
```bash
# 2025-2026 NRT data
python scripts/backfill_fires_extended_buffer.py

# 2018-2024 historical data (from Google Drive)
python scripts/process_historical_fire_buffer.py
```
- Downloads fire detections in 50km buffer around parks
- Output: `data/fire_additional_buffer/*_YEAR_buffer.json`
- Resumable with progress tracking

