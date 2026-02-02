# Agent Instructions - 5MP Conservation Monitoring

## Project Overview

A Go web application for conservation monitoring of 162 African keystone protected areas. Features an interactive 3D globe visualization with fire detection, deforestation analysis, settlement data, and legal framework information.

**Tech Stack:** Go, SQLite, HTML/CSS/JS (MapLibre GL for globe)

**Live URL:** https://fivemp-testing.exe.xyz:8000/?pwd=test2026

---

## Key Files

- `cmd/srv/main.go` - Entry point
- `srv/server.go` - HTTP server and routing
- `srv/templates/globe.html` - Main UI (single-page app)
- `srv/narrative_handlers.go` - Fire/deforestation/settlement narrative APIs
- `srv/api.go` - Other API endpoints
- `db.sqlite3` - SQLite database (~1.3GB)

---

## How to Run

```bash
make build && ./server
```

Access at: http://localhost:8000/?pwd=test2026

---

## Important Notes

1. Database is ~1.3GB - be careful with queries
2. Fire data has 1.7M records - use LIMIT
3. All endpoints require `?pwd=` or session cookie
4. Static DB download: `/static/downloads/5mp_data.sqlite3`

---

## Current Sprint Status

See TODO.md for completed items and remaining P3 tasks.
See CONTINUATION.md for technical details and known limitations.

---

## Test Credentials

- **App Password:** test2026
