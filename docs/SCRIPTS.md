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

### Rivers (HydroRIVERS)
```bash
python scripts/import_hydrorivers.py
```
- Downloads and imports HydroRIVERS Africa data
- Output: `rivers` + `park_rivers` tables + `data/rivers/*.json`

### Roads (HeiGIT)
```bash
python scripts/download_heigit_roads.py
```
- Downloads road surface data from HeiGIT/HDX
- Output: `feature_geometries` (road_heigit) + `data/roads_heigit/*.json`
- Includes surface type, passability, width

### OSM Places
```bash
python scripts/download_osm_places_to_file.py
```
- Downloads villages, rivers, landmarks from OSM
- Output: `osm_places` table + `data/osm_places/*.json`

### Settlements (from polygons)
```bash
python scripts/rebuild_events_from_polygons.py
```
- Rebuilds settlement events from GHSL polygons
- Output: `park_settlements` table + `data/settlement_events/*.json`

### Deforestation (from polygons)  
```bash
python scripts/rebuild_events_from_polygons.py
```
- Rebuilds deforestation events from Hansen polygons
- Output: `deforestation_events` table + `data/deforestation_events/*.json`

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
| `data/roads_heigit/` | 159 | Road surface data |
| `data/rivers/` | 161 | HydroRIVERS data |
| `data/osm_places/` | 91 | OSM place names |
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
