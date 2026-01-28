# Continuation Instructions

## Current State (2026-01-28 23:32 UTC)

### Active Background Processes
1. **OSM Places Download** (PID 2990): Processing ~50 parks, ~2400+ places
2. **Deforestation Analysis** (PID 3001): Processing 13 parks in Hansen tile area

### Database Summary
| Table | Count | Notes |
|-------|-------|-------|
| fire_detections | 1,764,155 | ✓ PRESERVE |
| park_group_infractions | 398 | ✓ PRESERVE |
| osm_places | 2,400+ | Growing |
| deforestation_events | TBD | Processing |
| park_settlements | 0 | Pending GHSL |

### Downloaded Data
- `data/ghsl_examples.zip` (749MB) - GHSL built-up + population
- `data/ghsl_manual.pdf` (15MB) - Documentation
- `data/hansen_lossyear_10N_020E.tif` (76MB) - Forest loss 2001-2024

### Scripts Ready
| Script | Status | Command |
|--------|--------|---------|
| `download_osm_places.py` | Running | Auto-continues |
| `deforestation_analyzer.py` | Running | Analyzing 13 parks |
| `ghsl_enhanced_processor.py` | Ready | Run after others |

### Monitor Progress
```bash
# Check processes
ps aux | grep python

# OSM progress
tail -f logs/osm_continue.log
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM osm_places;"

# Deforestation
tail -f logs/deforestation.log
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM deforestation_events;"
```

### Server
```bash
cd /home/exedev/5mpglobe && make build && pkill -f "./server"; nohup ./server > logs/server.log 2>&1 &
```

### URLs
- App: https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- DB: https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3

### Next Steps (When current processes finish)
1. Run GHSL Enhanced: `python scripts/ghsl_enhanced_processor.py`
2. Update park_stats_handlers.go to include new data
3. Run OSM roadless for remaining parks

### Passwords
ngi2026, apn2026, j2026
