# Continuation Instructions

## Current State (2026-01-29 19:55 UTC)

### System Status
- **Memory:** ~6.4GB available
- **Disk:** Hansen tiles downloaded (1.2GB), GHSL examples downloaded (785MB)

### Background Processes Running
1. **Deforestation Analysis** (PID 5434): Processing all 162 parks
   - Uses `scripts/deforestation_analyzer.py`
   - Log: `data/hansen/analysis_full.log`
   - Currently processing tile 00N_010E (COD_Tumba-Lediima)

### Database Summary
| Table | Count | Status |
|-------|-------|--------|
| fire_detections | 4,621,211 | ✓ Complete |
| park_group_infractions | 801 | ✓ Complete |
| osm_roadless_data | 162 | ✓ Complete |
| osm_places | 10,600 | ✓ Complete (villages, rivers, towns) |
| deforestation_events | ~48+ | ⏳ Running (2+ parks done so far) |
| deforestation_clusters | growing | ⏳ Running |
| park_ghsl_data | 155 | ✓ Complete |
| park_settlements | 0 | Ready (script enhanced, needs run) |

### Data Files Available
- `data/hansen/` - 32 Hansen GFC-2024 tiles (1.2GB total)
- `data/ghsl_examples.zip` - GHSL built-up + population tiles (785MB)

### Completed Tasks This Session
1. ✅ Downloaded all 32 Hansen GFC tiles for Africa
2. ✅ Enhanced `deforestation_analyzer.py` - multi-tile support, auto park-tile matching
3. ✅ Enhanced `ghsl_enhanced_processor.py` - local osm_places lookup, bearing/direction
4. ✅ Started full deforestation analysis (running in background)

---

## REMAINING TASKS

### HIGH PRIORITY - Data Tasks

#### Deforestation Analysis (Running)
Monitor progress:
```bash
tail -30 data/hansen/analysis_full.log
sqlite3 db.sqlite3 "SELECT COUNT(DISTINCT park_id) as parks, COUNT(*) as events, ROUND(SUM(area_km2),1) as total_km2 FROM deforestation_events;"
```

#### GHSL Settlement Processing (Ready to Run)
After deforestation completes:
```bash
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```

### MEDIUM PRIORITY - UI Tasks

#### 1. Remove EXPORT DATA Section
- Remove CSV/GeoJSON buttons from filter panel (redundant)

#### 2. Make Popup Scrollable
- Add max-height and overflow-y to `.pa-popup` for Legal section visibility

#### 3. Add Fire Trajectory Azimuth
- Add 360° direction for fire group movement when no places nearby

---

## Commands

### Monitor Deforestation
```bash
# Check progress
tail -30 data/hansen/analysis_full.log

# Database status
sqlite3 db.sqlite3 "SELECT park_id, COUNT(*) as years, ROUND(SUM(area_km2),2) as km2 FROM deforestation_events GROUP BY park_id ORDER BY km2 DESC LIMIT 20;"

# Check if still running
ps aux | grep deforest | grep -v grep
```

### Run GHSL Processing
```bash
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```

### Test Narrative APIs
```bash
curl -s --cookie "access_pwd=ngi2026" "http://localhost:8000/api/parks/GAB_Loango/deforestation-narrative" | python3 -m json.tool
```

### Server
```bash
make build && pkill -f "./server"; nohup ./server > /tmp/server.log 2>&1 &
```

---

## URLs
- App: https://fivemp-testing.exe.xyz:8000/?pwd=ngi2026

## Passwords
ngi2026, apn2026, j2026
