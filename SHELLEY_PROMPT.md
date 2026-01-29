# Continue 5MP Globe Development

## Context
You are continuing work on the 5MP.globe conservation monitoring application at `/home/exedev/5mpglobe`. This is a Go web server with Python data processing scripts.

**CRITICAL: Read CONTINUATION_INSTRUCTIONS.md first** - it has the current state and task list.

## Memory Management
- **Check memory before heavy tasks:** `free -h`
- **Never run multiple data-intensive Python scripts simultaneously**
- **Current memory:** ~6GB available
- **Deforestation script may still be running** - check with `ps aux | grep python`

## Database Protection
- **DO NOT DROP fire_detections table** - contains 1.7M records that took hours to compute
- **DO NOT DROP park_group_infractions** - contains 398 trajectory analyses
- Use WAL mode: `sqlite3 db.sqlite3 "PRAGMA journal_mode=WAL;"`

## Current Data Status
| Table | Records | Status |
|-------|---------|--------|
| fire_detections | 1,764,155 | ✓ Complete |
| park_group_infractions | 398 | ✓ Complete |
| osm_places | 10,600+ | ✓ Complete (rivers, villages) |
| deforestation_events | 48+ | Running/Growing |
| park_settlements | 0 | Pending GHSL processing |

## Priority Tasks (Data > UI)

### HIGH PRIORITY - Data Tasks
1. **GHSL Processing** - Run `scripts/ghsl_enhanced_processor.py` for settlements
2. **Narrative APIs** - Build endpoints for rich text descriptions using place names
3. **Legal texts in tooltip** - Add legal framework info to park popups
4. **VIIRS API fix** - Try earthaccess or ESA CCI Fire dataset

### MEDIUM PRIORITY - UI Tasks
1. Fix double tooltip on parks
2. Fix menu X close button
3. Simplify "162 Keystones" to compact toggle
4. Remove redundant download section
5. UI redundancy audit
6. Update globe logo and login button styling

## Key Files
- `srv/templates/globe.html` - Main UI
- `srv/park_stats_handlers.go` - Park stats API
- `scripts/ghsl_enhanced_processor.py` - Settlement detection
- `scripts/deforestation_analyzer.py` - Forest loss analysis
- `data/legal_frameworks.json` - Legal info (10 countries)

## Commands
```bash
# Check processes
ps aux | grep python | grep -v grep

# Check database
sqlite3 db.sqlite3 "SELECT 'fire', COUNT(*) FROM fire_detections UNION SELECT 'osm', COUNT(*) FROM osm_places UNION SELECT 'deforest', COUNT(*) FROM deforestation_events;"

# Start server
cd /home/exedev/5mpglobe && make build && ./server &

# Run GHSL (when memory available)
source .venv/bin/activate && python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```

## URLs
- App: https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- Other VM with more fire data: https://fivemp-testing.shelley.exe.xyz/

## Passwords
ngi2026, apn2026, j2026

## API Key
earthaccess/NASA: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

## Git
Commit frequently: `git add <files> && git commit -m "message" && git push github main`
