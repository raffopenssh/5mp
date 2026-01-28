# Continuation Instructions

## Current State (2026-01-28 07:30 UTC)

### Background Processes Running
- **Fire Processing**: PID 846 - Processing African countries from fire_data.zip
- **OSM Roadless**: PID 778 - Processing parks (2/157 done, ~1.3 hours remaining)

### Database Status
- **fire_detections**: 1,764,155 records (DO NOT DELETE - took hours to calculate)
- **park_group_infractions**: 398 records (being updated with trajectories)
- **park_ghsl_data**: 155 records
- **osm_roadless_data**: 6 records (growing)
- **park_fire_analysis**: 231 records

### Database Dump
Download pre-processed data: https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3

### Data Files Location
- Fire data ZIP: `data/downloads/fire_data.zip` (2.3GB)
- GHSL data ZIP: needs re-download from Google Drive

---

## OPEN TASKS (Priority Order)

### HIGH PRIORITY - Do First (Sequential to avoid memory conflicts)

#### Task 1: Notification Click Enhancement
- When users click upload notifications, highlight pixels, zoom smoothly, set time frame
- Code location: `srv/templates/globe.html` lines 2116-2270
- Already partially working, needs time range sync improvement

#### Task 2: Processing Status in UI
- Park modal footer should show which data is complete vs processing
- Add estimated completion dates
- Code location: `srv/templates/globe.html` showParkStatsModal function (~line 2639)

#### Task 3: Legal Texts & Checklist
- Legal texts data: `data/legal_frameworks.json` (10 countries covered)
- Checklist schema: `data/park_checklist_schema.json`
- Ensure all checklist items are visible in park info

#### Task 4: Manifest Documentation
- Add methodology docs for: effort intensity, fire, roadless, GHSL
- Location: Create `docs/METHODOLOGY.md` or update README

#### Task 5: UI Improvement - Merge Modal into Tooltip
- Remove separate park modal, enhance tooltip with collapsible sections
- Show key info: fire, settlements, roadless, legal texts, encroachment logs
- Use monocolor icons matching toolbar style
- Respect current time filter for stats
- Code: `srv/templates/globe.html` PA popup section (~line 2500)

#### Task 6: Patrol Intensity Logic Fix
- Current: single visit = 100% (wrong)
- Correct: need monthly visits in dry season for full coverage
- Rainy season: lower expectation due to inaccessibility
- Code: Grid intensity calculation in handlers and globe.html

### MEDIUM PRIORITY - Can Run in Background

#### Task 7: GHSL Data Enhancement
- Download additional GHSL examples: https://drive.google.com/file/d/1Ubr6iYyFXpjTF-uDma6mrUww4dyLEhu5/view
- Download manual: https://drive.google.com/file/d/1yS_lD07eQUe46ffrYrfao-C9ghya9nYh/view
- API key for earthaccess: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP
- Process: building footprints, population estimates, settlement classification
- Label settlements with nearby OSM place names
- Store GPS locations for settlements in park and buffer
- **Memory**: Work with zipped files directly, don't extract

#### Task 8: Geographic Context in Text
- Enhance fire/GHSL messages with real place names
- Download villages/rivers from Overpass API
- Store simplified GeoJSON for reference
- Example output: "herder group came from Toulouse (Sudan), crossed X river south of Yalinga..."

#### Task 9: Deforestation Analysis
- Data source: https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12/Hansen_GFC-2024-v1.12_lossyear_10N_020E.tif
- Value encoding: 0=2000, 24=2024
- Detect patterns: farming (near villages), mining (near rivers, no villages), roads (strip-shaped), forestry
- Store events with GeoJSON, year, description, nearby places
- Include village names, pattern type, area cleared, trends

### LOWER PRIORITY

#### Task 10: Password Protection
- Add password gate: "ngi2026", "apn2026", or "j2026"
- Keep database download accessible (add password as URL param)

#### Task 11: Paper Research Improvement
- Current papers often don't specifically mention the park
- Filter to ensure park name appears in abstract
- Code: `srv/server.go` publication fetching

#### Task 12: VIIRS API Fix (Lowest Priority)
- Current: only area function works
- Try CORS proxy or earthaccess library
- Alternative: ESA CCI FireCCI dataset
- API key for earthaccess: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

#### Task 13: UI Polish
- Remove "STATS (CUSTOM)" label - users understand naturally
- Move view counter before "MapLibre" with | separator
- Add version number (count commits)
- Add GitHub icon linking to repo

#### Task 14: Mobile Responsive
- Better positions for stats and legend
- Test and fix mobile layout

---

## Sub-Agent Guidelines

### Memory Management
- **NEVER run multiple data-intensive tasks simultaneously**
- Fire, GHSL, OSM, Deforestation processing must be sequential
- Check `free -h` before starting heavy processing
- Use streaming/chunked processing for large files

### File Locations
```
/home/exedev/5mp/
├── srv/templates/globe.html    # Main UI
├── srv/server.go               # Main server
├── srv/park_stats_handlers.go  # Park stats API
├── srv/admin_handlers.go       # Admin + uploads
├── scripts/
│   ├── fire_processor_streaming.py
│   ├── osm_roadless_analysis.py
│   ├── ghsl_processor_streaming.py
│   └── regenerate_trajectories.py
├── data/
│   ├── keystones_with_boundaries.json
│   ├── legal_frameworks.json
│   └── downloads/
└── db.sqlite3
```

### Commit Frequently
```bash
git add <specific files>
git commit -m "descriptive message"
git push origin main
```

### Check Background Process Status
```bash
# Check running processes
ps aux | grep -E "fire_processor|osm_roadless|ghsl_processor" | grep python

# Check logs
tail -20 /home/exedev/5mp/logs/fire_processing.log
tail -20 /home/exedev/5mp/logs/osm_roadless.log

# Check progress files
cat /home/exedev/5mp/data/osm_roadless_progress.json
```

### Server
```bash
# Rebuild and restart
cd /home/exedev/5mp && make build
# Server runs on port 8000
# Public URL: https://fivemp-testing.exe.xyz:8000/
```

---

## Google Drive Links for Data
- Fire data: https://drive.google.com/file/d/1w59TvLxsOjTSRQWeQx3XYEdzeSTydUXP/view
- GHSL tiles: https://drive.google.com/file/d/1BVynyEFKnYB-gwEsbfc2MILAGQcJlo6K/view
- GHSL examples: https://drive.google.com/file/d/1Ubr6iYyFXpjTF-uDma6mrUww4dyLEhu5/view
- GHSL manual: https://drive.google.com/file/d/1yS_lD07eQUe46ffrYrfao-C9ghya9nYh/view

## API Keys
- earthaccess: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

