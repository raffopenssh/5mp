# Continuation Instructions

## Current State (2026-01-28 23:20 UTC)

### Active Background Processes
- **OSM Places Download**: PID 2728 - Downloading villages/rivers/towns for all parks
  - Log: `logs/osm_places.log`
  - Progress: ~1 park per 30-60 seconds (rate limited)

### Database Status
- **fire_detections**: 1,764,155 records ✓
- **park_group_infractions**: 398 records ✓ 
- **osm_roadless_data**: 3 records (needs more processing)
- **osm_places**: New table (populating)
- **park_settlements**: New table (empty)
- **deforestation_events**: New table (empty)

### Downloaded Data Files
- `data/ghsl_examples.zip` - 749MB GHSL tiles (BUILT_S 10m/100m, POP 100m)
- `data/ghsl_manual.pdf` - 15MB GHSL documentation
- `data/hansen_lossyear_10N_020E.tif` - 76MB Hansen deforestation data

### New Scripts Created (Tasks 7-9)
1. **scripts/ghsl_enhanced_processor.py** - Task 7
   - Combines built-up surface with population data
   - Creates park_settlements with GPS coordinates
   - Labels settlements with OSM village names

2. **scripts/download_osm_places.py** - Task 8
   - Downloads villages, rivers, towns from Overpass API
   - Rate limited (30s between parks)
   - Provides utility functions for place lookup

3. **scripts/deforestation_analyzer.py** - Task 9
   - Processes Hansen GFC-2024 lossyear data
   - Classifies patterns (farming, mining, road, forestry)
   - Generates narratives with nearby place names

### Run Order (Due to Memory Constraints)
1. ✓ OSM Places (currently running) - light memory usage
2. WAIT for OSM Places to finish, then run GHSL Enhanced
3. WAIT for both, then run Deforestation Analyzer

### Server
```bash
cd /home/exedev/5mpglobe && make build && pkill -f "./server"; ./server &
# URL: https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
```

### Monitor Progress
```bash
# OSM Places
tail -f logs/osm_places.log
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM osm_places;"

# Check all processes
ps aux | grep python | grep -v grep
```

### DB Snapshot Download
```bash
# Copy latest DB for download
cp db.sqlite3 srv/static/downloads/5mp_data.sqlite3
# URL: https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3
```

---

## REMAINING TASKS

### Task 7: GHSL Enhancement (Script Ready)
- Run after OSM places completes:
  ```bash
  source .venv/bin/activate
  python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
  ```

### Task 8: Geographic Context (Running)
- OSM places download in progress
- Will enable rich place-based descriptions for fire/settlement events

### Task 9: Deforestation (Script Ready)
- Run after Tasks 7 & 8:
  ```bash
  source .venv/bin/activate
  python scripts/deforestation_analyzer.py --park CAF_Chinko
  ```

### Task 12: VIIRS API Fix
- Try earthaccess library with key: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

---

## API Keys
- earthaccess: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

## Google Drive Links
- GHSL examples: https://drive.google.com/file/d/1Ubr6iYyFXpjTF-uDma6mrUww4dyLEhu5/view
- GHSL manual: https://drive.google.com/file/d/1yS_lD07eQUe46ffrYrfao-C9ghya9nYh/view
