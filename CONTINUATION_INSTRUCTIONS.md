# Continuation Instructions

## Current State (2026-01-29 06:35 UTC)

### System Status
- **Memory:** 6.8GB available ✓
- **Disk:** ~4GB free (large DB)

### Database Summary
| Table | Count | Status |
|-------|-------|--------|
| fire_detections | 4,621,211 | ✓ Complete (this VM has more data) |
| park_group_infractions | 801 | ✓ Complete |
| osm_roadless_data | 162 | ✓ Complete |
| osm_places | 10,600 | ✓ **Imported** (villages, rivers, towns) |
| deforestation_events | 96 | ✓ **Imported** (4 parks done) |
| deforestation_clusters | 1,097 | ✓ **Imported** |
| park_ghsl_data | 155 | ✓ Complete |
| park_settlements | 0 | Pending GHSL enhancement |

### Completed Tasks
1. ✅ **Narrative APIs** - Working with place names
   - `GET /api/parks/{id}/fire-narrative` - Shows nearby rivers/villages
   - `GET /api/parks/{id}/deforestation-narrative` - Full yearly breakdown with places
2. ✅ **Legal texts in tooltip** - §LEGAL section added to park popup
3. ✅ **OSM Places imported** - 10,600 places (4772 villages, 3360 rivers, 2280 hamlets)
4. ✅ **Deforestation data imported** - 96 events, 1097 clusters from 4 parks
5. ✅ **Compact keystones toggle** - Shows [🏛️ 162]

### Deforestation Coverage
Parks with deforestation data (Hansen 2001-2024):
- ✅ CAF_Bamingui-Bangoran (24 years)
- ✅ CAF_Chinko (24 years)  
- ✅ CAF_Manovo_Gounda_St_Floris (24 years)
- ✅ COD_Abumonbazi (24 years)
- ⏳ Other parks - script running on other VM

---

## REMAINING TASKS

### HIGH PRIORITY - Data Tasks

#### Task: GHSL Enhancement (park_settlements)
**Script ready:** `scripts/ghsl_enhanced_processor.py`
- Needs GHSL tiles data file
- Will populate park_settlements table

#### Task: More Deforestation Parks
- Script `scripts/deforestation_analyzer.py` can process more parks
- Need Hansen GFC tiles (~1.5hrs per park)

### MEDIUM PRIORITY - UI Tasks

#### 1. Fix Legal Section Not Opening
- Legal section in popup doesn't expand on click
- Data loads (API works) but UI toggle may be broken

#### 2. Remove EXPORT DATA Section
- Remove CSV/GeoJSON buttons from filter panel (redundant)

#### 3. Test Narrative Display in UI
- Narratives exist via API but need UI integration in popup

---

## Commands

### Test Narrative APIs
```bash
curl -s --cookie "access_pwd=ngi2026" "http://localhost:8000/api/parks/CAF_Chinko/fire-narrative" | python3 -m json.tool
curl -s --cookie "access_pwd=ngi2026" "http://localhost:8000/api/parks/CAF_Chinko/deforestation-narrative" | python3 -m json.tool
```

### Database Counts
```bash
sqlite3 db.sqlite3 "SELECT 'osm_places', COUNT(*) FROM osm_places UNION ALL SELECT 'deforestation', COUNT(*) FROM deforestation_events UNION ALL SELECT 'fire', COUNT(*) FROM fire_detections;"
```

### Server
```bash
make build && pkill -f "./server"; nohup ./server > /tmp/server.log 2>&1 &
```

---

## URLs
- App: https://fivemp-testing.exe.xyz:8000/?pwd=ngi2026
- DB Download: /static/downloads/5mp_data.sqlite3

## Passwords
ngi2026, apn2026, j2026
