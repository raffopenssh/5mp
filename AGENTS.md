# Agent Instructions - 5MP Conservation Monitoring

## Quick Context

Go web app for conservation monitoring of 162 African protected areas.
Interactive 3D globe with fire detection, deforestation, settlements, patrol tracking.

**Live URL:** https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026

---

## ⚠️ DATABASE PROTECTION

**The database has 5M+ fire records. DO NOT:**
- Run DELETE/DROP without confirmation
- UPDATE without WHERE clause
- Truncate any tables

**ALWAYS:**
- Use LIMIT when exploring
- Back up before schema changes: `cp db.sqlite3 db.sqlite3.bak`

---

## Key Files

| File | Purpose |
|------|---------|
| `cmd/srv/main.go` | Entry point |
| `srv/server.go` | HTTP routing |
| `srv/templates/globe.html` | Main UI (single-page app) |
| `srv/api.go` | API endpoints |
| `srv/narrative_handlers.go` | Fire/deforestation/settlement narratives |
| `srv/fire_realtime_handlers.go` | NRT fire analysis |
| `srv/upload.go` | GPX upload handlers |
| `srv/upload_queue.go` | Async upload processor |
| `db.sqlite3` | SQLite database (~1.5GB) |

---

## How to Run

```bash
make build && ./server
```

Access: http://localhost:8000/?pwd=test2026

---

## Key APIs

```bash
# Park stats
curl "http://localhost:8000/api/parks/COD_Virunga/stats?pwd=test2026"

# Fire realtime (groups/trajectories)
curl "http://localhost:8000/api/parks/COD_Virunga/fire-realtime?pwd=test2026&days=28"

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
| fire_detections | ~5M | FIRMS satellite fires |
| park_settlements | 15K | GHSL built-up areas |
| deforestation_events | 300 | Hansen forest loss |
| osm_places | 10K | Place names for narratives |
| gpx_uploads | varies | Uploaded patrol tracks |
| effort_data | varies | Aggregated patrol effort |
| fire_group_alerts | varies | Active fire group alerts |

---

## Background Workers

1. **Upload Queue** - Processes GPX uploads async (every 2s)
2. **GPX Learner** - Pattern detection from uploads
3. **Fire NRT Daily** - Downloads fire data (3am UTC)
4. **Fire Backfill** - Historical data download (4am UTC)

---

## Test Parks

- **COD_Virunga** - Full data coverage, good for testing
- **CMR_Nki** - Pristine (0 settlements)
- **TZA_Serengeti** - Well-documented

---

## Credentials

| Type | Value |
|------|-------|
| App Passwords | test2026, REDACTED_PWD, REDACTED_PWD |
| Admin | see secrets.env |

---

## Documentation

See `docs/` directory:
- `README.md` - Overview
- `INSTALL.md` - Setup guide
- `API.md` - API reference
- `DATABASE.md` - Schema docs
- `ARCHITECTURE.md` - System design
- `SHELLEY_PROMPT_UI.md` - UI development
- `SHELLEY_PROMPT_ADMIN_UI.md` - Admin panel

---

## Current Status

See `TODO.md` for sprint status.
