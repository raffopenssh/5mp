# Continuation Instructions

## Current State (2026-01-28 23:55 UTC)

### Active Background Processes
1. **OSM Places Download**: ~28 parks done, 3,229 places (1,212 rivers)
2. **Deforestation Analysis**: Processing 13 parks, 24+ events

### Database Summary
| Table | Count |
|-------|-------|
| fire_detections | 1,764,155 ✓ |
| park_group_infractions | 398 ✓ |
| osm_places | 3,229 (1,212 rivers, 1,098 villages) |
| deforestation_events | 24+ |

---

## UI FIX TASKS (High Priority)

### 1. Fix Double Tooltip
- Two tooltips appearing on park hover/click
- Check globe.html for duplicate popup/tooltip code
- Ensure only ONE tooltip displays at a time

### 2. Fix Menu X Button
- Close button (X) not working properly on filter menu
- Check the onclick handler for closeModal or toggle function

### 3. Simplify "162 Keystones" Toggle
- Currently takes too much space with label
- Convert to simple toggle button (just icon + number)
- Example: `[🏛️ 162]` as compact toggle

### 4. Remove Redundant Download Section
- "EXPORT DATA" section in filter panel is redundant
- Remove CSV/GeoJSON buttons from filter section
- Keep export functionality elsewhere (e.g., in stats panel or menu)

### 5. Full UI Redundancy Audit
- Review all panels for duplicate functionality
- Consolidate similar controls
- Remove unused/redundant UI elements
- Ensure consistent spacing and layout

### 6. Replace Globe Logo and Login Button
- Current globe logo doesn't match app style
- Login button style inconsistent
- Update to match dark theme aesthetic
- Consider using simpler icon or text logo

---

## DATA TASKS (Lower Priority)

### Rivers for Textual Descriptions
- ✓ 1,212 rivers captured in osm_places
- Use in fire trajectory descriptions

### Narrative APIs to Build
- `GET /api/parks/{id}/fire-narrative`
- `GET /api/parks/{id}/deforestation-narrative`
- `GET /api/parks/{id}/settlement-narrative`

### VIIRS API Fix (Lowest Priority)
- Try earthaccess library or ESA CCI Fire dataset
- API key: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

---

## Files to Edit for UI Fixes
- `srv/templates/globe.html` - Main UI, tooltips, panels
- `srv/static/css/` - Styles if separate
- `srv/templates/login.html` - Login page styling

## Server
```bash
cd /home/exedev/5mpglobe && make build && pkill -f "./server"; nohup ./server > logs/server.log 2>&1 &
```

## URLs
- App: https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- DB: https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3

## Passwords
ngi2026, apn2026, j2026
