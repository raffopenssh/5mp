# Continuation Instructions

## Current State (2026-01-28 22:35 UTC)

### Background Processes Running
- **Fire Processing**: PID 846 - Processing 2019 African countries (still running, ~15 hours in)
- **OSM Roadless**: PID 1887 - Just restarted, 128/162 parks done

### Database Status
- **fire_detections**: 1,764,155 records (DO NOT DELETE - took hours to calculate)
- **park_group_infractions**: 398 records
- **park_ghsl_data**: 155 records
- **osm_roadless_data**: 128 records (34 remaining)
- **park_fire_analysis**: 231 records

### Database Dump
Download: https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3?pwd=ngi2026

### Completed Tasks
- ✅ Task 10: Password protection (ngi2026, apn2026, j2026)
- ✅ Task 13: UI polish - removed STATS label, added footer with views/version/GitHub

---

## REMAINING TASKS (Priority Order)

### HIGH PRIORITY - UI Tasks (Can do now, no memory conflict)

#### Task 5: UI Improvement - Merge Modal into Tooltip ⭐
- Remove separate park modal, enhance tooltip with collapsible sections
- Show: fire, settlements, roadless, legal texts, encroachment logs
- Use monocolor icons matching toolbar style
- Respect current time filter for stats
- Code: `srv/templates/globe.html` PA popup section (~line 2500)

#### Task 6: Patrol Intensity Logic Fix ⭐
- Current: single visit = 100% (WRONG)
- Correct: need monthly visits in dry season (Nov-Apr) for full coverage
- Rainy season (May-Oct): lower expectation due to inaccessibility
- Code: Grid intensity calculation in handlers and globe.html

#### Task 14: Mobile Responsive
- Better positions for stats and legend
- Test and fix mobile layout

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

#### Task 1-4: Various UI enhancements
- Already partially done, review needed

#### Task 11: Paper Research Improvement
- Filter to ensure park name appears in abstract
- Code: `srv/server.go` publication fetching

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

### Server
```bash
cd /home/exedev/5mp && make build && pkill -f "./server"; ./server &
# Public URL: https://fivemp-testing.exe.xyz:8000/?pwd=ngi2026
```

---

## API Keys
- earthaccess: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

## Google Drive Links
- Fire data: https://drive.google.com/file/d/1w59TvLxsOjTSRQWeQx3XYEdzeSTydUXP/view
- GHSL tiles: https://drive.google.com/file/d/1BVynyEFKnYB-gwEsbfc2MILAGQcJlo6K/view
- GHSL examples: https://drive.google.com/file/d/1Ubr6iYyFXpjTF-uDma6mrUww4dyLEhu5/view
- GHSL manual: https://drive.google.com/file/d/1yS_lD07eQUe46ffrYrfao-C9ghya9nYh/view

