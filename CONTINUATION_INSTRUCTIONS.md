# Continuation Instructions

## Current State (2026-01-31)

### Database Summary - Production Data
| Table | Count | Description |
|-------|-------|-------------|
| fire_detections | 4,621,211 | FIRMS satellite fire data |
| park_settlements | 15,066 | GHSL settlement data (161 parks) |
| deforestation_events | 3,218 | Hansen forest loss events |
| deforestation_clusters | 5,616 | Clustered deforestation polygons |
| osm_roadless_data | 162 | OSM-derived roadless analysis |
| park_group_infractions | 801 | Fire infractions by park group |
| osm_places | 10,600 | OpenStreetMap place names |
| ghsl_data | 161 | Global Human Settlement Layer tiles |
| park_ghsl_data | 155 | Park-specific GHSL data |
| park_documents | 7 | Management plan documents |
| gpx_uploads | 59 | Uploaded GPS tracks |
| users | 2 | Registered users |

**Database size:** 1.3 GB

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
- **Fire analysis** - detection data, narratives, animations
- **Deforestation analysis** - Hansen data with clustering
- **Settlement analysis** - GHSL population data
- **Legal frameworks** - 19 countries covered
- **Password protection** - Dark themed login page
- **API endpoints** - Fire, deforestation, settlement narratives
- **Data download** - SQLite database export

### UI/UX
- Monochrome icons (no colored emojis)
- Dark theme password page with animations
- Scrollable park popups with all sections
- Mobile responsive design

---

## What Needs Attention

### Priority Tasks
1. Fire trajectory azimuth display in narratives (bearing 022°)
2. Visual testing/screenshots
3. Park management plans (5-year/10-year docs)
4. Service currently inactive - needs restart

### Potential Improvements
- More park documents
- Extended legal framework coverage
- Performance optimization for large fire queries

---

## Commands for Common Tasks

### Build and Run
```bash
cd /home/exedev/5mp
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

# Check specific park
sqlite3 db.sqlite3 "SELECT * FROM fire_detections WHERE park_id='COD_Virunga' LIMIT 5;"
```

### Test APIs
```bash
# Fire narrative
curl -s "http://localhost:8000/api/parks/COD_Virunga/fire-narrative?pwd=ngi2026" | jq

# Deforestation narrative
curl -s "http://localhost:8000/api/parks/COD_Virunga/deforestation-narrative?pwd=ngi2026" | jq

# Settlement narrative
curl -s "http://localhost:8000/api/parks/COD_Virunga/settlement-narrative?pwd=ngi2026" | jq

# Park stats
curl -s "http://localhost:8000/api/parks/COD_Virunga/stats?pwd=ngi2026" | jq
```

### Code Generation
```bash
cd db && sqlc generate  # Regenerate DB code from queries
```

### Run Tests
```bash
make test
go test ./srv/... -v
```

---

## URLs and Access

### Production
- **App:** https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- **DB Download:** https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3

### Local Development
- **App:** http://localhost:8000/?pwd=ngi2026

### Passwords
- `ngi2026` - Primary access
- `apn2026` - Alternative
- `j2026` - Alternative

---

## Testing Checklist

### Globe Navigation
- [ ] Load globe, verify 162 keystone markers
- [ ] Click park marker - popup with stats
- [ ] Popup scrollable, Legal section visible
- [ ] Zoom/pan/rotate works

### Filter Panel
- [ ] Open filter panel (hamburger)
- [ ] Toggle "162 Keystones"
- [ ] Search park by name
- [ ] All controls monochrome

### Park Details (use COD_Virunga)
- [ ] Fire Analysis section expands
- [ ] Deforestation section shows events
- [ ] Settlements section shows count
- [ ] Legal section shows legislation

### Data Integrity
```bash
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM fire_detections;"  # Expected: 4,621,211
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM park_settlements;"  # Expected: 15,066
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM deforestation_events;"  # Expected: 3,218
```
