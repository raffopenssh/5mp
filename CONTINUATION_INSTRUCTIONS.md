# Continuation Instructions

## Current State (2026-01-29 00:05 UTC)

### System Status
- **Memory:** 6.2GB available ✓
- **Disk:** 6.9GB free ✓

### Active Background Processes (This VM)
1. **Deforestation Analysis** (PID 3001): 48+ events detected, still running

### Other VM Note
**https://fivemp-testing.shelley.exe.xyz/** has fire algorithm running - will have more fire data soon. Check status and sync if needed.

### Database Summary
| Table | Count |
|-------|-------|
| fire_detections | 1,764,155 ✓ |
| park_group_infractions | 398 ✓ |
| osm_places | 10,600 ✓ (rivers, villages, towns) |
| deforestation_events | 48+ (growing) |
| park_settlements | 0 (pending GHSL) |

### Downloaded Data Files
- `data/ghsl_examples.zip` (749MB) - BUILT_S + POP tiles
- `data/ghsl_manual.pdf` (15MB) - Documentation
- `data/hansen_lossyear_10N_020E.tif` (76MB) - Forest loss 2001-2024

---

## HIGH PRIORITY - DATA TASKS

### Task 7: GHSL Data Enhancement
**Script ready:** `scripts/ghsl_enhanced_processor.py`
- Combines built-up surface with population data
- Detects settlements with GPS coordinates
- Estimates households/people per settlement
- Labels settlements with OSM village names
- Stores in `park_settlements` table

**Run after deforestation completes:**
```bash
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```

**GHSL Data Alternatives (if direct download fails):**
- Google Earth Engine Built-up Surface 10m: 
  https://developers.google.com/earth-engine/datasets/catalog/JRC_GHSL_P2023A_GHS_BUILT_S_10m
- Google Earth Engine Built-up Characteristics:
  https://developers.google.com/earth-engine/datasets/catalog/JRC_GHSL_P2023A_GHS_BUILT_C
- earthaccess library: https://github.com/nsidc/earthaccess
- API key: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

### Task 8: Geographic Context in Text
**OSM Download COMPLETE:** 10,600 places captured
- Rivers, villages, hamlets, towns
- Use place names in fire/settlement/deforestation descriptions

**Example narrative:**
> "Fire group originated near Yalinga, crossed the Chinko River, entered park Dec 15, burned 8 days near Mbuti village."

### Task 9: Deforestation Analysis
**Script running:** `scripts/deforestation_analyzer.py`
- ✓ 48+ events detected (growing)
- Classifies patterns: farming, mining, road, forestry
- Generates narratives with coordinates

### Task: Build Narrative APIs
Create rich textual description endpoints:
- `GET /api/parks/{id}/fire-narrative` - Fire group movements with places
- `GET /api/parks/{id}/deforestation-narrative` - Forest loss with context
- `GET /api/parks/{id}/settlement-narrative` - Settlements with nearby places

### Task: Add Legal Texts to Park Tooltip
- Park tooltip should show legal framework information
- Display: designation, establishment year, governing body
- Show country-level legislation links
- Data source: `data/legal_frameworks.json`
- Currently only 10 countries have data - may need expansion

### Task: VIIRS API Fix (After Other Data Tasks)
**Problem:** FIRMS API CORS issues
**Options:**
1. CORS proxy for direct FIRMS API
2. earthaccess library (NASA)
3. ESA CCI Fire: https://developers.google.com/earth-engine/datasets/catalog/ESA_CCI_FireCCI_5_1

---

## MEDIUM PRIORITY - UI FIX TASKS

### 1. Fix Double Tooltip
- Two tooltips appearing on park hover/click
- Check globe.html for duplicate popup code

### 2. Fix Menu X Button
- Close button not working on filter menu

### 3. Simplify "162 Keystones" Toggle
- Convert to compact toggle: `[🏛️ 162]`

### 4. Remove Redundant Download Section
- Remove CSV/GeoJSON from filter panel

### 5. Full UI Redundancy Audit
- Review all panels for duplicates
- Consolidate similar controls

### 6. Replace Globe Logo and Login Button
- Match dark theme aesthetic

---

## Scripts Status
| Script | Status | Purpose |
|--------|--------|---------|
| `ghsl_enhanced_processor.py` | **Ready** | Settlements + population |
| `download_osm_places.py` | **Complete** | 10,600 places captured |
| `deforestation_analyzer.py` | **Running** | 48+ events |
| `fire_processor_streaming.py` | Ready | Process fire CSVs from ZIP |

---

## Commands

### Run GHSL Processing
```bash
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```

### Monitor Progress
```bash
ps aux | grep python | grep -v grep
sqlite3 db.sqlite3 "SELECT place_type, COUNT(*) FROM osm_places GROUP BY place_type;"
sqlite3 db.sqlite3 "SELECT park_id, COUNT(*) FROM deforestation_events GROUP BY park_id;"
free -h
```

### Server
```bash
cd /home/exedev/5mpglobe && make build && pkill -f "./server"; nohup ./server > logs/server.log 2>&1 &
```

---

## URLs
- This VM: https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- Other VM (more fire data): https://fivemp-testing.shelley.exe.xyz/
- DB Download: https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3

## API Keys
- earthaccess/NASA: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

## Passwords
ngi2026, apn2026, j2026
