# Continuation Instructions

## Current State (2026-01-28 23:26 UTC)

### Active Background Processes
- **OSM Places Quick Download**: PID 2847 - Processing first 10 parks
  - Log: `logs/osm_quick.log`
  - Currently: 282 places downloaded

### Database Status (1.7M+ records preserved)
- **fire_detections**: 1,764,155 records ✓ DO NOT DELETE
- **park_group_infractions**: 398 records ✓
- **osm_places**: 282 records (growing)
- **osm_roadless_data**: 3 records
- **park_settlements**: Empty (pending GHSL processing)
- **deforestation_events**: Empty (pending processing)

### Downloaded Data Files
- `data/ghsl_examples.zip` - 749MB GHSL tiles with population data
- `data/ghsl_manual.pdf` - 15MB documentation  
- `data/hansen_lossyear_10N_020E.tif` - 76MB Hansen deforestation

### New Scripts Created (Tasks 7-9)
1. **scripts/ghsl_enhanced_processor.py** - Settlement detection with population
2. **scripts/download_osm_places.py** - OSM villages/rivers download
3. **scripts/deforestation_analyzer.py** - Hansen forest loss analysis

### Run Order (Sequential to avoid memory issues)
1. ✓ OSM Places (running) - completes in ~30 min for 10 parks
2. GHSL Enhanced - run after OSM completes
3. Deforestation - run after GHSL completes

### Commands

Start Server:
```bash
cd /home/exedev/5mpglobe && make build && pkill -f "./server"; nohup ./server > logs/server.log 2>&1 &
```

Resume OSM Download (all parks):
```bash
cd /home/exedev/5mpglobe && source .venv/bin/activate
nohup python scripts/download_osm_places.py --buffer-km 20 > logs/osm_places.log 2>&1 &
```

Run GHSL Processing:
```bash
cd /home/exedev/5mpglobe && source .venv/bin/activate  
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```

Run Deforestation:
```bash
cd /home/exedev/5mpglobe && source .venv/bin/activate
python scripts/deforestation_analyzer.py
```

### Monitor Progress
```bash
tail -f logs/osm_quick.log
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM osm_places;"
ps aux | grep python | grep -v grep
```

### DB Download
```bash
cp db.sqlite3 srv/static/downloads/5mp_data.sqlite3
# URL: https://five-mp-conservation-effort.exe.xyz:8000/static/downloads/5mp_data.sqlite3
```

---

## API Keys
- earthaccess: I3Ca5DUxxQH7nv0miCbBnngrerhMDOkIQfgOHLVP

## App Password
- ngi2026, apn2026, or j2026
