# Continue 5MP Globe Development

## Context
Working directory: `/home/exedev/5mp`
This is a Go web server with Python data processing scripts for conservation monitoring.

**Read CONTINUATION_INSTRUCTIONS.md for full task list and status.**

## Immediate Priority

### 1. Check Deforestation Progress
```bash
sqlite3 db.sqlite3 "SELECT COUNT(DISTINCT park_id) FROM deforestation_events;"
# If >= 40 parks (25% of 162), stop the process:
pkill -f deforestation_analyzer
```

### 2. Run GHSL Settlement Processing (HIGH PRIORITY)
```bash
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```
This will populate `park_settlements` table with:
- Building footprints with GPS coordinates
- Population estimates
- Nearest place names from osm_places (10,600 records)
- Direction/distance descriptions

### 3. UI Tasks (use subagents)
Split these to subagents to limit token size:

**Subagent 1: Remove Export Section**
- Remove EXPORT DATA section (CSV/GeoJSON buttons) from filter panel in `srv/templates/globe.html`

**Subagent 2: Scrollable Popup**
- Add `max-height: 400px; overflow-y: auto;` to `.pa-popup` CSS
- Legal section currently gets cut off

**Subagent 3: Fire Trajectory Azimuth**
- In `srv/narrative_handlers.go`, add 360° direction for fire group movement
- Use when no nearby places found (fallback from "at coordinates")
- Example: "moving north-northeast (bearing 022°)"

## Database Status (as of last check)
| Table | Count | Notes |
|-------|-------|-------|
| fire_detections | 4,621,211 | Complete |
| park_group_infractions | 801 | Complete |
| osm_places | 10,600 | Villages, rivers, hamlets |
| deforestation_events | ~200+ | Running, check progress |
| park_settlements | 0 | Run GHSL processor |

## Key Files
- `scripts/ghsl_enhanced_processor.py` - Settlement detection (ready)
- `scripts/deforestation_analyzer.py` - Forest loss (running)
- `srv/templates/globe.html` - Main UI
- `srv/narrative_handlers.go` - Narrative text APIs

## Data Files
- `data/ghsl_examples.zip` (785MB) - GHSL built-up + population
- `data/hansen/` (1.2GB) - 32 Hansen GFC tiles

## Commands
```bash
# Check processes
ps aux | grep python | grep -v grep

# Memory
free -h

# Server
make build && pkill -f "./server"; nohup ./server > /tmp/server.log 2>&1 &
```

## URLs
- App: https://fivemp-testing.exe.xyz:8000/?pwd=ngi2026
- Passwords: ngi2026, apn2026, j2026
