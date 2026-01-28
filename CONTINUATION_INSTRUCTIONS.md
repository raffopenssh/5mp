# Continuation Instructions

## Current State (2026-01-28 22:55 UTC)

### Background Processes Running
- **Fire Processing**: PID 846 - Processing 2021 African countries (still running)
- **OSM Roadless**: PID 1888 - 13/34 parks processed (157 total done)

### Database Status
- **fire_detections**: ~3.8M records (still processing)
- **osm_roadless_data**: 157 records
- **park_ghsl_data**: 155 records

### Recently Completed Tasks
- ✅ Task 6: Patrol intensity now based on temporal frequency (monthly visits)
  - New SQL query GetEffortDataWithMonthCounts
  - Dry months count fully, rainy months weighted 0.3
- ✅ Task 5: Enhanced tooltip with collapsible sections
  - Fire Activity, Settlements, Roads, Research sections
  - Monocolor unicode icons, inline data loading
- ✅ Task 14: Mobile responsive improvements
  - Touch-friendly defaults, proper panel positioning
- ✅ Task 11: Paper research filtering
  - Quoted park name for exact phrase matching
  - Filter to only papers mentioning park in title/abstract

### Server
Restart after changes:
```bash
cd /home/exedev/5mp && make build && pkill -f "./server"; ./server &
# Public URL: https://fivemp-testing.exe.xyz:8000/?pwd=ngi2026
```

---

## REMAINING TASKS (Priority Order)

### MEDIUM PRIORITY - Background Processing Tasks

#### Task 7: GHSL Data Enhancement
**WAIT for fire processing to complete before starting**
- Download GHSL examples: https://drive.google.com/file/d/1Ubr6iYyFXpjTF-uDma6mrUww4dyLEhu5/view
- Download manual: https://drive.google.com/file/d/1yS_lD07eQUe46ffrYrfao-C9ghya9nYh/view
- Process: building footprints, population estimates
- Label settlements with OSM place names
- **Memory**: Work with zipped files directly

#### Task 8: Geographic Context in Text
**WAIT for OSM to complete**
- Download villages/rivers from Overpass API
- Store simplified GeoJSON for reference
- Enhance fire/GHSL messages with place names

#### Task 9: Deforestation Analysis
**WAIT for Tasks 7 & 8**
- Data: Hansen GFC-2024 lossyear tiles
- Detect patterns: farming, mining, roads, forestry
- Store events with GeoJSON and descriptions

### LOWER PRIORITY

#### Task 12: VIIRS API Fix (Lowest Priority)
- Try CORS proxy or earthaccess library
- API key: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

---

## Sub-Agent Guidelines

### Memory Management
- **NEVER run multiple data-intensive tasks simultaneously**
- Check `free -h` before starting heavy processing
- Fire processing currently using ~500MB

### Check Background Process Status
```bash
# Check running processes
ps aux | grep -E "fire_processor|osm_roadless" | grep python

# Check logs
tail -20 /home/exedev/5mp/logs/fire_processing.log
tail -20 /home/exedev/5mp/logs/osm_roadless.log

# Database counts
sqlite3 /home/exedev/5mp/db.sqlite3 "SELECT 'fire', COUNT(*) FROM fire_detections UNION SELECT 'osm', COUNT(*) FROM osm_roadless_data UNION SELECT 'ghsl', COUNT(*) FROM park_ghsl_data;"
```

---

## API Keys
- earthaccess: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

## Google Drive Links
- Fire data: https://drive.google.com/file/d/1w59TvLxsOjTSRQWeQx3XYEdzeSTydUXP/view
- GHSL tiles: https://drive.google.com/file/d/1BVynyEFKnYB-gwEsbfc2MILAGQcJlo6K/view
- GHSL examples: https://drive.google.com/file/d/1Ubr6iYyFXpjTF-uDma6mrUww4dyLEhu5/view
- GHSL manual: https://drive.google.com/file/d/1yS_lD07eQUe46ffrYrfao-C9ghya9nYh/view
