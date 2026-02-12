# Agent Instructions - 5MP Conservation Monitoring

## Quick Context

Go web app for conservation monitoring of 162 African protected areas.
Interactive 3D globe with fire detection, deforestation, settlements, patrol tracking.

**Live URL:** https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026

---

## ⚠️ DATABASE PROTECTION

**The database has 5.7M+ fire records. DO NOT:**
- Run DELETE/DROP without confirmation
- UPDATE without WHERE clause
- Truncate any tables

**ALWAYS:**
- Use LIMIT when exploring
- Back up before schema changes: `cp db.sqlite3 db.sqlite3.bak`

---

## Key Files

| File | Purpose |
|------|--------|
| `cmd/srv/main.go` | Entry point |
| `srv/server.go` | HTTP routing |
| `srv/templates/globe.html` | Main UI (single-page app) |
| `srv/api.go` | API endpoints |
| `srv/narrative_handlers.go` | Fire/deforestation/settlement narratives |
| `srv/fire_realtime_handlers.go` | NRT fire analysis |
| `srv/enhanced_narratives.go` | Context-aware narrative generation |
| `srv/upload.go` | GPX upload handlers |
| `srv/upload_queue.go` | Async upload processor |
| `db.sqlite3` | SQLite database (~1.8GB) |

---

## How to Run

```bash
make build && ./server
```

**Build details:**
- `make build` embeds git commit hash as version (shown in footer)
- Generates `.git-commits.txt` for version history modal (click version in UI)
- Version passed via `-ldflags "-X srv.exe.dev/srv.Version=$(VERSION)"`

Access: http://localhost:8000/?pwd=test2026

---

## Data Files

JSON data files in `data/` provide precomputed data:

| Directory | Files | Description |
|-----------|-------|-------------|
| `data/fire_analysis/` | 161 | Fire groups with trajectories by year |
| `data/fire_trajectories/` | 153 | Enhanced trajectories with context |
| `data/settlement_events/` | 156 | Classified settlements |
| `data/deforestation_events/` | 79 | Classified deforestation events |
| `data/rivers/` | 161 | HydroRIVERS data per park |
| `data/roads_heigit/` | 159 | Road surface data from HeiGIT |
| `data/osm_places/` | 91 | OSM place names |
| `data/climate/` | 1 | Monthly precipitation, seasons |
| `data/species/` | 1 | IUCN mammal species |
| `data/waterbodies/` | 137 | Global waterbody polygons |
| `data/export/` | 7 | Precomputed narratives |

---

## Key APIs

```bash
# Park stats
curl "http://localhost:8000/api/parks/COD_Virunga/stats?pwd=test2026"

# Fire narrative (from cache)
curl "http://localhost:8000/api/parks/COD_Virunga/fire-narrative?pwd=test2026"

# Fire realtime (groups/trajectories)
curl "http://localhost:8000/api/parks/COD_Virunga/fire-realtime?pwd=test2026&days=28"

# Climate data
curl "http://localhost:8000/api/parks/COD_Virunga/climate?pwd=test2026"

# IUCN species
curl "http://localhost:8000/api/parks/COD_Virunga/species?pwd=test2026"

# Feature GeoJSON (fire, settlement, deforestation, waterbody)
curl "http://localhost:8000/api/parks/COD_Virunga/features?type=fire_trajectory&pwd=test2026"

# Fire alerts
curl "http://localhost:8000/api/fire-alerts?pwd=test2026&limit=10"

# Grid data with filters
curl "http://localhost:8000/api/grid?pwd=test2026&type=foot,vehicle&bbox=28,-6,37,2"

# Async upload
curl -X POST "http://localhost:8000/api/upload/async?pwd=test2026" -F "gpx=@file.gpx"
```

---

## Database Stats

| Table | Records | Description |
|-------|---------|-------------|
| fire_detections | 5.7M | VIIRS satellite fires (2018-2026) |
| feature_geometries | 443K | GeoJSON polygons/lines |
| park_fire_analysis | 1,214 | Fire analysis by park/year (160 parks) |
| park_rivers | 32,200 | HydroRIVERS data (161 parks) |
| park_settlements | 15K | GHSL built-up areas (154 parks) |
| deforestation_events | 3.2K | Hansen forest loss (149 parks) |
| osm_places | 105K | Place names for narratives (141 parks) |
| park_climate | 162 | Monthly climate/seasons |
| park_species | 39.5K | IUCN mammal species |
| park_waterbodies | 2.6K | Lake/reservoir polygons (145 parks) |
| fire_narrative_cache | 162 | Precomputed fire narratives |
| fire_group_alerts | varies | Active fire group alerts |

**Feature geometries by type:**
- deforestation: 221,277 (2001-2024)
- fire_trajectory: 130,708 (2018-2026)
- settlement: 64,016
- road: 26,550

---

## Background Workers

1. **Upload Queue** - Processes GPX uploads async (every 2s)
2. **GPX Learner** - Pattern detection from uploads
3. **Fire NRT Daily** - Downloads fire data (3am UTC)
4. **Fire Backfill** - Historical data download (4am UTC)
5. **Narrative Cache** - Pre-computes fire narratives (weekly)

---

## Test Parks

- **COD_Virunga** - Full data coverage, Mountain Gorillas
- **CAF_Chinko** - Detailed fire trajectories
- **CMR_Nki** - Pristine (0 settlements)
- **TZA_Serengeti** - Well-documented ecosystem

---

## Credentials

| Type | Value |
|------|-------|
| App Passwords | test2026, REDACTED_PWD, REDACTED_PWD |
| Admin | see secrets.env |

---

## Data Processing Scripts

See `docs/SCRIPTS.md` for the full pipeline:

```bash
# 1. Rebuild fire analysis from raw detections
python scripts/rebuild_park_fire_analysis.py

# 2. Generate enhanced trajectories with context
python scripts/analyze_fire_trajectories_v3.py

# 3. Precompute narratives
python scripts/precompute_narratives_v3.py

# 4. Load JSON to database
python scripts/load_json_data.py
```

---

## Documentation

See `docs/` directory:
- `README.md` - Overview
- `INSTALL.md` - Setup guide
- `API.md` - API reference
- `DATABASE.md` - Schema docs
- `SCRIPTS.md` - Data processing pipeline
- `ARCHITECTURE.md` - System design
- `SHELLEY_PROMPT_UI.md` - UI development
- `SHELLEY_PROMPT_ADMIN_UI.md` - Admin panel

---

## Current Status

See `TODO.md` for sprint status.
