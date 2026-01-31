# Continuation Instructions

## Current State (2026-01-31)

### Database Summary - Production Data
| Table | Count | Description |
|-------|-------|-------------|
| fire_detections | 1,764,155 | FIRMS satellite fire data |
| park_settlements | 15,066 | GHSL settlement data (161 parks) |
| deforestation_events | 293 | Hansen forest loss events |
| deforestation_clusters | ~2,000 | Clustered deforestation polygons |
| osm_roadless_data | 162 | OSM-derived roadless analysis |
| park_group_infractions | ~800 | Fire infractions by park group |
| osm_places | 10,600 | OpenStreetMap place names |
| park_documents | 35+ | Management plan documents |
| gpx_uploads | 0 | Uploaded GPS tracks (needs testing) |
| users | 1 | Test user |

**Database size:** ~500 MB
**Note:** Remote VM at https://fivemp-testing.exe.xyz:8000 has larger dataset (4.6M fires, 3,218 deforestation events)

### 7 Pristine Wilderness Parks (No Settlements)
1. CMR_Nki (Cameroon)
2. COG_Nouabalé-Ndoki (Congo)
3. GAB_Monts_Birougou (Gabon)
4. GAB_Plateaux_Baték (Gabon)
5. KEN_Sibiloi (Kenya)
6. TZA_Rungwa (Tanzania)
7. TZA_Ugalla (Tanzania)

---

## What's Working ✓

### Core Features
- **Globe visualization** with 162 keystone park markers
- **Fire analysis** - enhanced with hotspots, trends, response rates
- **Deforestation analysis** - trend direction, 5-year comparisons, hotspots
- **Settlement analysis** - GHSL population data with conflict risk assessment
- **Legal frameworks** - 19 countries covered
- **Password protection** - Dark themed login page
- **Park Stats API** - Now includes deforestation statistics

### Narrative Enhancements (NEW)
- **Fire Narratives**: Hotspot analysis with nearby places, multi-year trends, peak months
- **Deforestation Narratives**: Trend direction (improving/worsening/stable), hotspot clusters
- **Settlement Narratives**: Conflict risk tiers, population density analysis

### UI/UX
- Monochrome icons (no colored emojis)
- Dark theme password page with animations
- Scrollable park popups with all sections
- Mobile responsive design
- Search functionality (works)
- Filter panel (works)
- Grid selection mode (works)
- Info/Manifest modal (works)
- Recent Activity notifications (works)

---

## What Needs Attention

### Priority Tasks
1. **GPX Upload Testing** - Auth required, test with real patrol data
2. **Patrol Intensity Display** - Upload data to show on map
3. **Fire narratives field** - Still null (generates from hotspots instead)
4. **Publications endpoint** - Returns empty (no data seeded)

### Test Data Available
- `data/test_patrol_virunga.gpx` - Synthetic Virunga patrol
- `data/virunga_patrol.gpx` - Real Ethiopia patrol data
- `data/another_patrol.gpx` - European test data

---

## Commands for Common Tasks

### Build and Run
```bash
cd /home/exedev/5mpglobe
make build
./server  # or: sudo systemctl start srv
```

### Restart Service
```bash
make build && sudo systemctl restart srv
journalctl -u srv -f  # View logs
```

### Database Queries
```bash
# Table counts
sqlite3 db.sqlite3 "
SELECT 'fire_detections', COUNT(*) FROM fire_detections
UNION ALL SELECT 'park_settlements', COUNT(*) FROM park_settlements
UNION ALL SELECT 'deforestation_events', COUNT(*) FROM deforestation_events
UNION ALL SELECT 'osm_roadless_data', COUNT(*) FROM osm_roadless_data
UNION ALL SELECT 'park_group_infractions', COUNT(*) FROM park_group_infractions;
"
```

### Test APIs
```bash
# Fire narrative (enhanced!)
curl -s "http://localhost:8000/api/parks/COD_Virunga/fire-narrative?pwd=ngi2026" | jq '.summary, .hotspots[:2], .trend'

# Deforestation narrative (with trends!)
curl -s "http://localhost:8000/api/parks/COD_Virunga/deforestation-narrative?pwd=ngi2026" | jq '.trend_direction, .hotspots[:2]'

# Settlement narrative
curl -s "http://localhost:8000/api/parks/COD_Virunga/settlement-narrative?pwd=ngi2026" | jq '.conflict_risk, .largest_settlements[:3]'

# Park stats (now with deforestation!)
curl -s "http://localhost:8000/api/parks/COD_Virunga/stats?pwd=ngi2026" | jq '.deforestation'
```

---

## URLs and Access

### Production
- **App:** https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- **Testing VM:** https://fivemp-testing.exe.xyz:8000/?pwd=ngi2026 (larger dataset)

### Local Development
- **App:** http://localhost:8000/?pwd=ngi2026

### Passwords
- `ngi2026` - Primary access
- `apn2026` - Alternative
- `j2026` - Alternative

---

## Testing Checklist

### Globe Navigation
- [x] Load globe, verify 162 keystone markers
- [x] Click park marker - popup with stats
- [x] Popup scrollable, Legal section visible
- [x] Zoom/pan/rotate works

### Filter Panel
- [x] Open filter panel (hamburger)
- [x] Toggle movement types (Foot, Vehicle, Aerial)
- [x] Toggle "162 Keystones"
- [x] Time slider works

### Search
- [x] Search for "Virunga"
- [x] Results appear with "loaded" badges
- [x] Click result zooms to park

### API Endpoints (All Tested ✓)
- [x] /api/parks/{id}/stats
- [x] /api/parks/{id}/fire-narrative
- [x] /api/parks/{id}/deforestation-narrative
- [x] /api/parks/{id}/settlement-narrative
- [x] /api/parks/{id}/documents
- [x] /api/parks/{id}/management-plans
- [x] /api/parks/{id}/infractions
- [x] /api/parks/{id}/data-status

### GPX Upload (Needs Testing)
- [ ] Login as test user (test@example.com)
- [ ] Upload GPX file via modal
- [ ] Verify patrol data appears on map
- [ ] Check patrol intensity legend

---

## For Other VM Instances

### Sync Database
The remote VM at https://fivemp-testing.exe.xyz:8000 has more data:
- 4.6M fire detections vs 1.7M here
- 3,218 deforestation events vs 293 here

To sync (if database download works):
```bash
curl -L -o db.sqlite3.new "https://fivemp-testing.exe.xyz:8000/static/downloads/5mp_data.sqlite3?pwd=ngi2026"
# Verify integrity before replacing
sqlite3 db.sqlite3.new "PRAGMA integrity_check;"
```

### Required Files
- `data/keystones_with_boundaries.json` - Park boundary data
- `data/wdpa_index.json` - WDPA protected area index
- `data/legal_frameworks.json` - Legal framework data
- `db.sqlite3` - SQLite database

---

## User Roles for Testing

| Type | Use Case |
|------|----------|
| Ministry Staff | Overview of all 162 parks, national trends, compliance |
| NGO Managers | Regional focus, funding allocation, intervention priorities |
| Park Rangers | Patrol planning, fire response, threat assessment |
| Researchers | Data export, trend analysis, publication support |

---

## Latest Updates (2026-01-31 Session 2)

### Time-Filtered Narratives ✓
- Fire and deforestation narratives now accept `year`, `from`, `to` parameters
- UI popup data syncs with time slider selection
- Example: `/api/parks/COD_Virunga/fire-narrative?year=2020` returns 2020 data

### Patrol Upload Tested ✓
- GPX upload working with authenticated user
- Test credentials: test@example.com / testpass123
- Patrol data stored in effort_data table (135 records, 1558 km)
- Patrol intensity visible on map when time range includes 2024

### API Parameter Reference
```bash
# Fire narrative with year filter
curl "http://localhost:8000/api/parks/COD_Virunga/fire-narrative?pwd=ngi2026&year=2020"

# Deforestation with date range
curl "http://localhost:8000/api/parks/COD_Virunga/deforestation-narrative?pwd=ngi2026&from=2015-01-01&to=2020-12-31"

# Grid effort data (for map)
curl "http://localhost:8000/api/grid?pwd=ngi2026&year=2024"
```
