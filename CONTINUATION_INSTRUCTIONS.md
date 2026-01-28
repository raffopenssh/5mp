# Continuation Instructions

## Current State (2026-01-28 23:35 UTC)

### Active Background Processes
1. **OSM Places Download** (PID 2990): Processing ~50 parks
2. **Deforestation Analysis** (PID 3001): Processing 13 parks in Hansen tile

### Database Summary
| Table | Count | Notes |
|-------|-------|-------|
| fire_detections | 1,764,155 | ✓ PRESERVE |
| park_group_infractions | 398 | ✓ PRESERVE |
| osm_places | 1,200+ | Growing - villages, rivers, towns |
| deforestation_events | 24+ | Growing - year-by-year analysis |
| park_settlements | 0 | Pending GHSL |

### Downloaded Data
- `data/ghsl_examples.zip` (749MB) - GHSL built-up + population tiles
- `data/ghsl_manual.pdf` (15MB) - Documentation
- `data/hansen_lossyear_10N_020E.tif` (76MB) - Forest loss 2001-2024

---

## REMAINING TASKS (Priority Order)

### Task 8 Enhancement: Rivers for Textual Descriptions
- **Ensure rivers are downloaded** with names from OSM
- Use river names in fire trajectory descriptions: "crossed the Chinko River"
- Store river geometries (simplified GeoJSON) for intersection checks
- Update `download_osm_places.py` if needed to ensure rivers are captured

### Task 7: GHSL Enhanced Processing
After OSM completes:
```bash
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```

### Task 9: Continue Deforestation Analysis
Currently running - will complete automatically for 13 parks in Hansen tile area.

### NEW: Build APIs for Narrative Descriptions
Create endpoints that generate rich textual descriptions:
- `GET /api/parks/{id}/fire-narrative` - Describe fire group movements with place names
- `GET /api/parks/{id}/deforestation-narrative` - Describe forest loss with context
- `GET /api/parks/{id}/settlement-narrative` - Describe settlements with nearby places

Example output:
> "Fire group originated near Yalinga (Sudan), crossed the Chinko River 50km south of the confluence, entered the park on Dec 15, burned for 8 days, and was last detected near Mbuti village moving southwest."

### NEW: VIIRS API Fix (LOWEST PRIORITY - Do Last)
**Problem:** FIRMS API CORS issues, only area function works
**Options to try:**
1. Use CORS proxy for direct FIRMS API access
2. Try `earthaccess` library (NASA): https://github.com/nsidc/earthaccess
   - API key: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP
3. Use ESA CCI Fire dataset via Google Earth Engine:
   - https://developers.google.com/earth-engine/datasets/catalog/ESA_CCI_FireCCI_5_1

**Reference:** See FIRMS API manual at `/tmp/shelley-screenshots/upload_02d8722199390fc9.html`

---

## Scripts Status

| Script | Status | Purpose |
|--------|--------|---------|
| `download_osm_places.py` | Running | Villages, rivers, towns |
| `deforestation_analyzer.py` | Running | Hansen forest loss |
| `ghsl_enhanced_processor.py` | Ready | Settlements + population |
| `fire_processor_streaming.py` | Ready | Process uploaded fire CSVs |

---

## Monitor Progress
```bash
# Check processes
ps aux | grep python | grep -v grep

# OSM places count
sqlite3 db.sqlite3 "SELECT place_type, COUNT(*) FROM osm_places GROUP BY place_type;"

# Deforestation events
sqlite3 db.sqlite3 "SELECT park_id, COUNT(*) FROM deforestation_events GROUP BY park_id;"

# Check rivers specifically
sqlite3 db.sqlite3 "SELECT park_id, name FROM osm_places WHERE place_type='river' LIMIT 20;"
```

## Server
```bash
cd /home/exedev/5mpglobe && make build && pkill -f "./server"; nohup ./server > logs/server.log 2>&1 &
```

## URLs
- App: https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- DB: https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3

## Passwords
ngi2026, apn2026, j2026

## API Keys
- earthaccess/NASA: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP
