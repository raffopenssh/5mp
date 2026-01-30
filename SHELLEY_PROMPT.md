# Continue 5MP Globe Development

## Context
Working directory: `/home/exedev/5mp`
Go web server with Python data processing scripts for conservation monitoring.

**Read CONTINUATION_INSTRUCTIONS.md for full status.**

## Background Processes
1. **Deforestation** - Check if 25% done (40+ parks), then stop: `pkill -f deforestation_analyzer`
2. **Roadless rerun** - Running for 18 parks with missing data (has MultiPolygon bug)

## Priority Tasks

### 1. GHSL Settlement Processing (HIGH)
```bash
source .venv/bin/activate
python scripts/ghsl_enhanced_processor.py --zip data/ghsl_examples.zip
```
Populates `park_settlements` with building footprints, population, nearest places.

### 2. Fix Roadless MultiPolygon Bug (HIGH)
18 parks fail with: `'MultiPolygon' object has no attribute 'exterior'`
Fix in `scripts/osm_roadless_analysis.py` - handle MultiPolygon geometries.
Parks: CAF_Dzanga_Park, COD_Kundelungu, COD_Salonga, COD_Virunga, COG_Ntokou-Pikounda, 
GAB_Monts_de_Cristal, GNQ_Reserva_de_la_Paz, NGA_Cross_River, NGA_Kainji_Lake, RWA_Nyungwe,
TZA_Selous, UGA_Queen_Elizabeth, ZAF_Mountain_Zebra-Camdeboo, ZAF_Namaqua, ZAF_Richtersveld,
ZAF_Sederberg, ZMB_Musalangu, ZWE_Matetsi

### 3. UI Tasks (use subagents)

**Subagent: ui-export** - Remove EXPORT DATA section from filter panel in globe.html

**Subagent: ui-popup** - Add scrollable popup (max-height: 400px, overflow-y: auto to .pa-popup)

**Subagent: ui-azimuth** - Add fire trajectory azimuth in narrative_handlers.go when no places nearby

## Database Status
| Table | Count | Notes |
|-------|-------|-------|
| fire_detections | 4,621,211 | Complete |
| osm_places | 10,600 | Complete |
| osm_roadless_data | 162 (144 with data) | 18 need rerun |
| deforestation_events | ~200+ | Check progress |
| park_settlements | 0 | Run GHSL |

## Commands
```bash
# Check deforestation progress
sqlite3 db.sqlite3 "SELECT COUNT(DISTINCT park_id) FROM deforestation_events;"

# Check roadless rerun
tail -30 logs/roadless_rerun.log

# Server
make build && pkill -f "./server"; nohup ./server > /tmp/server.log 2>&1 &
```

## URLs
App: https://fivemp-testing.exe.xyz:8000/?pwd=ngi2026
Passwords: ngi2026, apn2026, j2026
