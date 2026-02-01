# Agent Instructions - 5MP Conservation Monitoring

## CURRENT SPRINT: UI Stabilization (Priority)

**STOP** - Do not add new features. Focus only on the items in TODO.md.

---

## Project Overview

A Go web application for conservation monitoring of 162 African keystone protected areas. Features an interactive 3D globe visualization with fire detection, deforestation analysis, settlement data, and legal framework information.

**Tech Stack:** Go, SQLite, HTML/CSS/JS (MapLibre GL for globe)

**Live URL:** https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026

---

## Key Files

- `cmd/srv/main.go` - Entry point
- `srv/server.go` - HTTP server and routing
- `srv/templates/globe.html` - Main UI (single-page app)
- `srv/api.go` - API endpoints
- `srv/auth_handlers.go` - Authentication
- `db.sqlite3` - SQLite database

---

## How to Run

```bash
make build && ./server
```

---

## Test Credentials

- **Email:** test@example.com
- **Password:** testpass123
- **App Password:** ngi2026

---

## Important Notes

1. Database is ~500 MB - be careful with queries
2. Fire data has 1.7M records - use LIMIT
3. All endpoints require `?pwd=` or session cookie
