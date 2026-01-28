# Continuation Instructions

## Current State (2026-01-28 23:30 UTC)

### Active Background Processes
1. **OSM Places Quick Download**: PID 2847 - 6+ parks processed, 1125+ places
2. **Deforestation Analyzer**: PID 2703 - Processing CAF_Chinko

### Database Status (Fire data preserved!)
| Table | Count | Status |
|-------|-------|--------|
| fire_detections | 1,764,155 | ✓ PRESERVED |
| park_group_infractions | 398 | ✓ PRESERVED |
| osm_places | 1,125+ | Growing |
| osm_roadless_data | 3 | Needs expansion |
| park_settlements | 0 | Pending GHSL |
| deforestation_events | 0 | Processing |

### Downloaded Data Files
- `data/ghsl_examples.zip` - 749MB (BUILT_S + POP tiles)
- `data/ghsl_manual.pdf` - 15MB
- `data/hansen_lossyear_10N_020E.tif` - 76MB

### Scripts Created (Tasks 7-9)
| Script | Purpose |
|--------|---------|
| `scripts/ghsl_enhanced_processor.py` | Settlement + population detection |
| `scripts/download_osm_places.py` | OSM villages/rivers |
| `scripts/deforestation_analyzer.py` | Hansen forest loss |

### Commands to Run Tasks

```bash
# Start server
cd /home/exedev/5mpglobe && make build && pkill -f "./server"; nohup ./server > logs/server.log 2>&1 &

# Resume OSM Places (all parks)
cd /home/exedev/5mpglobe && source .venv/bin/activate
nohup python scripts/download_osm_places.py --buffer-km 20 > logs/osm_places.log 2>&1 &

# Run GHSL Processing
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --park CAF_Chinko

# Run Deforestation
source .venv/bin/activate
python scripts/deforestation_analyzer.py
```

### Monitor Progress
```bash
# Check processes
ps aux | grep python | grep -v grep

# Database counts
sqlite3 db.sqlite3 "SELECT 'osm_places', COUNT(*) FROM osm_places UNION SELECT 'deforest', COUNT(*) FROM deforestation_events;"

# OSM progress by park
sqlite3 db.sqlite3 "SELECT park_id, COUNT(*) FROM osm_places GROUP BY park_id;"
```

### URLs
- App: https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- DB: https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3

### API Key
- earthaccess: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

### App Passwords
- ngi2026, apn2026, j2026
