# Continuation Instructions

## Current State (2026-01-29 06:00 UTC)

### System Status
- **Memory:** 5.8GB available ✓
- **Disk:** ~6.5GB free ✓

### Active Background Processes (This VM)
1. **Deforestation Analysis** (PID 3001): 96 events (4/13 parks complete), ~1.5hrs per park

### Other VM Note
**https://fivemp-testing.shelley.exe.xyz/** has fire algorithm running - may have more fire data.

### Database Summary
| Table | Count | Status |
|-------|-------|--------|
| fire_detections | 1,764,155 | ✓ Complete |
| park_group_infractions | 398 | ✓ Complete |
| osm_places | 10,600 | ✓ Complete (rivers, villages, towns) |
| deforestation_events | 96 | ⏳ Running (4 parks done, 9 remaining) |
| park_settlements | 0 | Pending GHSL (wait for deforestation) |

### Completed Tasks This Session
1. ✅ **Narrative APIs** - Created `srv/narrative_handlers.go`
   - `GET /api/parks/{id}/fire-narrative` - Working
   - `GET /api/parks/{id}/deforestation-narrative` - Working  
   - `GET /api/parks/{id}/settlement-narrative` - Placeholder ready
2. ✅ **Cardinal Directions** - Narratives now include directions
   - Example: "8 km north-northeast of Gigi, 6 km northwest of the Ebola"
   - Uses 16-point compass (N, NNE, NE, ENE, E, etc.)
3. ✅ **Auth fix** - API endpoints work with `?pwd=` param without redirect
4. ✅ **UI Fixes** (via subagent)
   - Fixed double tooltip issue
   - Simplified "162 Keystones" to compact toggle `[🏛️ 162]`
   - Menu X button investigated (code looks correct)

### Deforestation Progress
Parks in Hansen tile (20E-30E, 0N-10N): **13 total**
- ✅ CAF_Bamingui-Bangoran (24 years)
- ✅ CAF_Chinko (24 years)  
- ✅ CAF_Manovo_Gounda_St_Floris (24 years)
- ✅ COD_Abumonbazi (24 years)
- ⏳ COD_Bili-Uere (processing now)
- ⏳ COD_Garamba
- ⏳ COD_Maiko
- ⏳ COD_Okapis
- ⏳ COD_Virunga
- ⏳ SSD_Southern
- ⏳ TCD_Aouk
- ⏳ UGA_Queen_Elizabeth
- ⏳ UGA_Rwenzori_Mountains

**Note:** Script is CPU-bound at 99.9%. ~1.5 hours per park.
**ETA for completion:** ~13.5 more hours (9 parks remaining).
**Option:** Could kill and optimize script, but will lose current progress.

---

## HIGH PRIORITY - DATA TASKS

### Task 7: GHSL Data Enhancement
**Script ready:** `scripts/ghsl_enhanced_processor.py`
- Wait for deforestation to complete (uses 1.2GB memory)
- Combines built-up surface with population data

**Run when deforestation completes:**
```bash
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```

### Task: Legal Texts in Park Tooltip
- API exists: `GET /api/legal/pa/{pa_id}`
- Need to integrate into globe.html tooltip
- Data: 10 countries, 9 PA-specific entries

### Task: VIIRS API Fix (Lower Priority)
- FIRMS API CORS issues
- Options: CORS proxy, earthaccess library, ESA CCI Fire
- API key: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

---

## MEDIUM PRIORITY - UI FIX TASKS

### 1. ⚠️ Fix Menu X Button (needs verification)
- Code looks correct but may have runtime issue
- Test in browser and check console for errors

### 2. Remove Redundant Download Section
- Remove CSV/GeoJSON from filter panel

### 3. Full UI Redundancy Audit
- Review all panels for duplicates
- Consolidate similar controls

### 4. Replace Globe Logo and Login Button
- Match dark theme aesthetic

---

## Scripts Status
| Script | Status | Purpose |
|--------|--------|--------|
| `ghsl_enhanced_processor.py` | **Ready** | Settlements + population |
| `download_osm_places.py` | **Complete** | 10,600 places captured |
| `deforestation_analyzer.py` | **Running** | 4/13 parks done |
| `narrative_handlers.go` | **Complete** | Rich text APIs |

---

## Commands

### Run GHSL Processing (after deforestation)
```bash
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```

### Monitor Progress
```bash
ps aux | grep python | grep -v grep
sqlite3 db.sqlite3 "SELECT park_id, COUNT(*) FROM deforestation_events GROUP BY park_id;"
free -h
```

### Test Narrative APIs
```bash
curl -s "http://localhost:8000/api/parks/CAF_Chinko/fire-narrative?pwd=ngi2026" | python3 -m json.tool
curl -s "http://localhost:8000/api/parks/CAF_Chinko/deforestation-narrative?pwd=ngi2026" | python3 -m json.tool
```

### Server
```bash
cd /home/exedev/5mpglobe && make build && pkill -f "./server"; nohup ./server > logs/server.log 2>&1 &
```

### Update DB Download
```bash
cp db.sqlite3 srv/static/downloads/5mp_data.sqlite3
```

---

## URLs
- This VM: https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- Other VM (fire data): https://fivemp-testing.shelley.exe.xyz/
- DB Download: https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3

## API Keys
- earthaccess/NASA: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

## Passwords
ngi2026, apn2026, j2026
