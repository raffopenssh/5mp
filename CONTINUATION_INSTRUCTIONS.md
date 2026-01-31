# Continuation Instructions

## Current State (2026-02-01 - five-mp-conservation-effort VM)

### Database Summary
| Table | Count | Description |
|-------|-------|-------------|
| fire_detections | 1,764,155 | FIRMS satellite fire data |
| park_settlements | 15,066 | GHSL settlement data |
| deforestation_events | 293 | Hansen forest loss events |
| deforestation_clusters | ~2,000 | Clustered deforestation |
| osm_roadless_data | ~160 | OSM-derived roadless analysis |
| park_group_infractions | ~800 | Fire infractions by park group |
| osm_places | 10,600 | OpenStreetMap place names |
| users | 2 | admin + test user |

**Database size:** ~500 MB
**Note:** fivemp-testing.exe.xyz has larger dataset but DB download corrupts

---

## What's Working ✓

### Core Features
- **Globe visualization** with 162 keystone park markers
- **PA popup with collapsible sections** - Fire, Settlements, Deforestation, Roads, Research
- **Fire analysis** - enhanced with hotspots, trends, response rates
- **Deforestation analysis** - trend direction, yearly breakdown
- **Settlement analysis** - population by region, conflict risk tiers
- **Legal frameworks** - 19 countries covered
- **Stats panel** - Patrol Activity + Conservation Data summaries
- **Export Parks CSV** - data export feature
- **Country filter** dropdown
- **Time slider** with date range filtering
- **Share URL** - copies current view state to clipboard
- **PA click priority** - parks get click priority over grid cells

### Admin Access
- **Email:** admin@5mp.globe
- **Password:** admin5mp2026
- **Login:** https://five-mp-conservation-effort.exe.xyz:8000/login

### User Access
- **Test user:** test@example.com / testpass123
- **App password:** ngi2026, apn2026, or j2026

---

## Share Link Feature

The share button (bottom right, upload icon) copies a URL with current state:
- Map position (lat, lng, zoom)
- Time range (from, to)
- Selected PA
- Country filter
- Movement type filters

Example URL:
```
https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026&lat=-2.5&lng=35.1&z=8&from=2023-01-01&to=2025-12-31&pa=TZA_Serengeti
```

---

## Known Issues / Edge Cases

1. **Browser automation clicks** - Synthetic mouse events don't trigger MapLibre click handlers (real user clicks work fine)
2. **DB download from fivemp-testing** - Corrupts during download (may need scp)
3. **Publications endpoint** - Returns empty (needs data seeding)

---

## URLs and Access

- **This VM:** https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- **Other VM:** https://fivemp-testing.exe.xyz:8000/?pwd=ngi2026 (more data)
- **Login:** https://five-mp-conservation-effort.exe.xyz:8000/login
- **Admin:** https://five-mp-conservation-effort.exe.xyz:8000/admin

---

## Commands for Common Tasks

### Build and Run
```bash
cd /home/exedev/5mpglobe
make build
./server  # or: sudo systemctl start srv
```

### Test APIs
```bash
# Park stats
curl "http://localhost:8000/api/parks/TZA_Serengeti/stats?pwd=ngi2026"

# Fire narrative
curl "http://localhost:8000/api/parks/COD_Virunga/fire-narrative?pwd=ngi2026&year=2023"

# Deforestation narrative  
curl "http://localhost:8000/api/parks/COD_Virunga/deforestation-narrative?pwd=ngi2026&from=2018-01-01&to=2023-12-31"

# Settlement narrative
curl "http://localhost:8000/api/parks/COD_Virunga/settlement-narrative?pwd=ngi2026"

# Export parks CSV
curl "http://localhost:8000/api/export/parks?pwd=ngi2026"
```

---

## 7 Pristine Wilderness Parks (No Settlements)
1. CMR_Nki (Cameroon)
2. COG_Nouabalé-Ndoki (Congo)
3. GAB_Monts_Birougou (Gabon)
4. GAB_Plateaux_Baték (Gabon)
5. KEN_Sibiloi (Kenya)
6. TZA_Rungwa (Tanzania)
7. TZA_Ugalla (Tanzania)

---

## Browser Compatibility

Tested with MapLibre GL JS 4.1.2:
- Chrome/Edge: ✓
- Firefox: ✓  
- Safari: ✓ (with -webkit- prefixes in CSS)
- Mobile: Touch events supported, responsive design

---

## Future Improvements

See `docs/IMPROVEMENT_PROPOSALS_V2.md` for:
1. Dashboard summary cards
2. Alert system for threats
3. Park comparison view
4. PDF report generation
5. Mobile optimization
