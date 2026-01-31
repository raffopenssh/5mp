# Continuation Instructions

## Current State (2026-01-31 - fivemp-testing VM)

### Database Summary - This VM Has MORE Data
| Table | This VM | Other VM | Description |
|-------|---------|----------|-------------|
| fire_detections | **4,621,211** | 1,764,155 | FIRMS satellite fire data |
| park_settlements | 15,066 | 15,066 | GHSL settlement data |
| deforestation_events | **3,218** | 293 | Hansen forest loss events |
| deforestation_clusters | **5,616** | ~2,000 | Clustered deforestation |
| osm_roadless_data | **162** | 4 | OSM-derived roadless analysis |
| park_group_infractions | **801** | ~800 | Fire infractions by park group |
| osm_places | 10,600 | 10,600 | OpenStreetMap place names |

**Database size:** 1.3 GB (this VM is the primary data source)

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
- **Deforestation analysis** - trend direction, yearly breakdown
- **Settlement analysis** - population by region, conflict risk tiers
- **Legal frameworks** - 19 countries covered
- **Password protection** - Dark themed with monochrome SVG icon
- **Deforestation in popup** - Now shows in park popup with trend

### Time-Filtered Narratives
- Fire narratives accept `year`, `from`, `to` parameters
- Deforestation narratives filter by date range
- UI popup syncs with time slider selection

### Recent Fixes (This Session)
- ✅ Fixed settlement narrative column name (population_est)
- ✅ Fixed deforestation popup field names (total_loss_km2, trend_direction)
- ✅ Centered globe icon on password page

---

## What Needs Attention

### Known Issues
1. **Popup click via JS** - Programmatic click doesn't trigger popup (browser testing issue)
2. **Publications endpoint** - Returns empty (needs seeding)

### Suggested Improvements (see IMPROVEMENT_PROPOSALS_V2.md)
1. Dashboard summary cards
2. Alert system for threats
3. Park comparison view
4. Export/report generation
5. Mobile optimization

---

## Commands for Common Tasks

### Build and Run
```bash
cd /home/exedev/5mp
make build
./server  # or: sudo systemctl start srv
```

### Test APIs
```bash
# Fire narrative with year
curl "http://localhost:8000/api/parks/COD_Virunga/fire-narrative?pwd=ngi2026&year=2022"

# Deforestation with date range
curl "http://localhost:8000/api/parks/COD_Virunga/deforestation-narrative?pwd=ngi2026&from=2018-01-01&to=2022-12-31"

# Settlement narrative
curl "http://localhost:8000/api/parks/COD_Virunga/settlement-narrative?pwd=ngi2026"
```

---

## URLs and Access

- **This VM:** https://fivemp-testing.exe.xyz:8000/?pwd=ngi2026 (primary data)
- **Other VM:** https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- **Passwords:** ngi2026, apn2026, j2026

---

## Database Download

This VM's database is available at:
```
https://fivemp-testing.exe.xyz:8000/static/downloads/5mp_data.sqlite3
```
