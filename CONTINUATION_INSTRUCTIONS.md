# Continuation Instructions

## Current State (2026-01-30 06:05 UTC)

### System Status
- **Memory:** ~6GB available
- **GHSL Processing:** ✓ COMPLETE - 15,066 settlements

### Database Summary
| Table | Count | Status |
|-------|-------|--------|
| fire_detections | 1,764,155 | ✓ Complete |
| park_group_infractions | 398 | ✓ Complete |
| osm_places | 10,600 | ✓ Complete |
| deforestation_events | 293 | ✓ Complete (13 parks) |
| deforestation_clusters | 185 | ✓ Complete |
| park_settlements | 15,066 | ✓ Complete (161 parks) |
| osm_roadless_data | 3 | ⚠️ Only 3 parks |

### Completed This Session
1. ✅ **GHSL Global Processing** - 15,066 settlements across ALL 161 parks
   - Downloaded global 100m GHSL file (2.1GB)
   - Script: `scripts/ghsl_global_processor.py`
   
2. ✅ **Password Page Restyle** - Dark theme with animations
   - Matches globe.html aesthetic
   - Floating particles, frosted glass effect
   
3. ✅ **UI Fixes** - Monochrome icons
   - Replaced 18 colored emojis with Unicode symbols
   - Popup already scrollable

### Remaining Tasks

#### HIGH PRIORITY - Data
1. **Roadless Areas** - Only 3/162 parks have data
   - Script: `scripts/osm_roadless_analysis.py`
   - Uses Overpass API (rate limited)
   
2. **Deforestation** - Running on other VM
   - Only 13 parks from single Hansen tile here

#### MEDIUM PRIORITY - Content
1. **Legal Frameworks** - Expand from 10 to more countries
   - Search with French/Portuguese keywords
   - Add park management plans

#### LOW PRIORITY - UI
1. Fire trajectory azimuth (bearing 022°)
2. Any remaining visual polish

---

## Commands

### Check Data
```bash
sqlite3 db.sqlite3 "SELECT COUNT(*) as settlements FROM park_settlements;"
sqlite3 db.sqlite3 "SELECT COUNT(*) as roadless FROM osm_roadless_data;"
```

### Run Roadless Analysis
```bash
source .venv/bin/activate
nohup python scripts/osm_roadless_analysis.py > logs/roadless.log 2>&1 &
```

### Server
```bash
make build && pkill -f "./server"; nohup ./server > /tmp/server.log 2>&1 &
```

---

## URLs
- App: https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- DB Download: https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3

## Passwords
ngi2026, apn2026, j2026
