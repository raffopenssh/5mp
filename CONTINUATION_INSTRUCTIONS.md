# Continuation Instructions

## Current State (2026-01-28 23:58 UTC)

### Active Background Processes (This VM)
1. **OSM Places Download**: ~28 parks done, 3,229 places (1,212 rivers)
2. **Deforestation Analysis**: Processing 13 parks, 24+ events

### Other VM Note
**https://fivemp-testing.shelley.exe.xyz/** has fire algorithm running - will have more fire data soon. Check status and sync if needed.

### Database Summary
| Table | Count |
|-------|-------|
| fire_detections | 1,764,155 ✓ |
| park_group_infractions | 398 ✓ |
| osm_places | 3,229 (1,212 rivers, 1,098 villages) |
| deforestation_events | 24+ |
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

**Run after OSM completes:**
```bash
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```

**Additional GHSL sources to try:**
- earthaccess library: https://github.com/nsidc/earthaccess
- Google Earth Engine: https://developers.google.com/earth-engine/datasets/catalog/JRC_GHSL_P2023A_GHS_BUILT_S_10m
- API key: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

### Task 8: Geographic Context in Text
**Script ready:** `scripts/download_osm_places.py`
- ✓ Rivers captured (1,212)
- ✓ Villages/hamlets captured (1,926)
- Use place names in fire/settlement/deforestation descriptions
- Store simplified GeoJSON for spatial queries

**Example narrative:**
> "Fire group originated near Yalinga, crossed the Chinko River, entered park Dec 15, burned 8 days near Mbuti village."

### Task 9: Deforestation Analysis
**Script running:** `scripts/deforestation_analyzer.py`
- ✓ 24+ events detected
- Classifies patterns: farming, mining, road, forestry
- Generates narratives with coordinates
- Uses nearby village/river names

### Task: Build Narrative APIs
Create rich textual description endpoints:
- `GET /api/parks/{id}/fire-narrative` - Fire group movements with places
- `GET /api/parks/{id}/deforestation-narrative` - Forest loss with context
- `GET /api/parks/{id}/settlement-narrative` - Settlements with nearby places

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
| `download_osm_places.py` | Running | Villages, rivers, towns |
| `deforestation_analyzer.py` | Running | Hansen forest loss |
| `fire_processor_streaming.py` | Ready | Process fire CSVs from ZIP |

---

## Commands

### Run GHSL Processing
```bash
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```

### Resume OSM Download (all parks)
```bash
source .venv/bin/activate
nohup python scripts/download_osm_places.py --buffer-km 20 > logs/osm_places.log 2>&1 &
```

### Monitor Progress
```bash
ps aux | grep python | grep -v grep
sqlite3 db.sqlite3 "SELECT place_type, COUNT(*) FROM osm_places GROUP BY place_type;"
sqlite3 db.sqlite3 "SELECT park_id, COUNT(*) FROM deforestation_events GROUP BY park_id;"
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
