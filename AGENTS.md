# Agent Instructions - 5MP Conservation Monitoring

## Project Overview

A Go web application for conservation monitoring of 162 African keystone protected areas. Features an interactive 3D globe visualization with fire detection, deforestation analysis, settlement data, and legal framework information.

**Tech Stack:** Go, SQLite, HTML/CSS/JS (CesiumJS for globe)

---

## Key File Locations

### Entry Points
- `cmd/srv/main.go` - Application entry point
- `srv/server.go` - HTTP server setup and routing

### Handlers
- `srv/api.go` - Main API endpoints (parks, stats)
- `srv/narrative_handlers.go` - AI-generated narrative endpoints
- `srv/park_stats_handlers.go` - Park statistics endpoints
- `srv/fire_handlers.go` - Fire analysis endpoints
- `srv/admin_handlers.go` - Admin panel handlers
- `srv/auth_middleware.go` - Password/auth middleware

### Templates
- `srv/templates/globe.html` - Main 3D globe interface
- `srv/templates/welcome.html` - Password entry page
- `srv/templates/admin.html` - Admin panel
- `srv/templates/fire_analysis.html` - Fire analysis view
- `srv/templates/park_analysis.html` - Park analysis view

### Database
- `db.sqlite3` - Production database (1.3 GB)
- `db/db.go` - Database initialization
- `db/migrations/` - SQL migration files (001-011)
- `db/queries/` - SQLC query definitions
- `db/dbgen/` - Generated SQLC code

### Static Assets
- `srv/static/` - CSS, JS, images
- `srv/areas/` - Park boundary GeoJSON files
- `static/downloads/` - Downloadable files

---

## How to Run

### Build
```bash
make build  # Creates ./server binary
```

### Run Locally
```bash
./server  # Listens on :8000
```

### Run as Service
```bash
sudo systemctl start srv
sudo systemctl status srv
journalctl -u srv -f  # View logs
```

### Restart After Changes
```bash
make build && sudo systemctl restart srv
```

---

## Testing

### Run Tests
```bash
make test
# or
go test ./... -v
```

### Manual API Testing
```bash
# Park stats
curl "http://localhost:8000/api/parks/COD_Virunga/stats?pwd=ngi2026"

# Fire narrative
curl "http://localhost:8000/api/parks/COD_Virunga/fire-narrative?pwd=ngi2026"

# Deforestation narrative
curl "http://localhost:8000/api/parks/COD_Virunga/deforestation-narrative?pwd=ngi2026"

# Settlement narrative
curl "http://localhost:8000/api/parks/COD_Virunga/settlement-narrative?pwd=ngi2026"
```

### Database Verification
```bash
sqlite3 db.sqlite3 ".tables"  # List all tables
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM fire_detections;"  # 4.6M records
```

---

## Database Schema (Key Tables)

| Table | Purpose |
|-------|--------|
| fire_detections | FIRMS satellite fire data (4.6M records) |
| park_settlements | GHSL human settlement data |
| deforestation_events | Hansen Global Forest Watch data |
| deforestation_clusters | Clustered deforestation polygons |
| osm_roadless_data | Road network analysis |
| park_group_infractions | Fire infractions summary |
| osm_places | Place names from OpenStreetMap |
| park_documents | Management plans and documents |
| gpx_uploads | Uploaded GPS tracks |

---

## API Endpoints

### Public (with password)
- `GET /api/parks` - List all parks
- `GET /api/parks/{id}/stats` - Park statistics
- `GET /api/parks/{id}/fire-narrative` - Fire analysis narrative
- `GET /api/parks/{id}/deforestation-narrative` - Deforestation narrative
- `GET /api/parks/{id}/settlement-narrative` - Settlement narrative
- `GET /api/parks/{id}/fires` - Raw fire data

### Protected
- `GET /admin` - Admin panel
- `POST /upload` - File upload

---

## Access

- **URL:** http://localhost:8000/?pwd=ngi2026
- **Production:** https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- **Passwords:** ngi2026, apn2026, j2026

---

## Code Generation

Database queries use SQLC:
```bash
cd db && sqlc generate
```

---

## Important Notes

1. **Database is large (1.3 GB)** - Be careful with queries
2. **Fire data has 4.6M records** - Use LIMIT in queries
3. **Password required** - All endpoints need `?pwd=` or cookie
4. **Service file:** `/etc/systemd/system/srv.service`
