# Agent Instructions - 5MP Conservation Monitoring

## Project Overview

A Go web application for conservation monitoring of 162 African keystone protected areas. Features an interactive 3D globe visualization with fire detection, deforestation analysis, settlement data, and legal framework information.

**Tech Stack:** Go, SQLite, HTML/CSS/JS (MapLibre GL for globe)

**Live URLs:**
- https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- https://fivemp-testing.exe.xyz:8000/?pwd=ngi2026 (larger dataset)

---

## Key File Locations

### Entry Points
- `cmd/srv/main.go` - Application entry point
- `srv/server.go` - HTTP server setup and routing

### Handlers
- `srv/api.go` - Main API endpoints (parks, stats)
- `srv/narrative_handlers.go` - AI-generated narrative endpoints (fire, deforestation, settlement)
- `srv/park_stats_handlers.go` - Park statistics with fire trends, deforestation
- `srv/fire_handlers.go` - Fire analysis endpoints
- `srv/upload.go` - GPX file upload handling
- `srv/auth_handlers.go` - Authentication and sessions
- `srv/admin_handlers.go` - Admin panel handlers

### Templates
- `srv/templates/globe.html` - Main 3D globe interface
- `srv/templates/welcome.html` - Password entry page
- `srv/templates/admin.html` - Admin panel

### Database
- `db.sqlite3` - Production database (~500 MB)
- `db/db.go` - Database initialization
- `db/migrations/` - SQL migration files (001-011)
- `db/queries/` - SQLC query definitions
- `db/dbgen/` - Generated SQLC code

### Static Assets
- `srv/static/` - CSS, JS, images
- `data/` - GeoJSON, configuration, GPX test files

---

## Recent Enhancements (2026-01-31)

### Fire Narrative (`/api/parks/{id}/fire-narrative`)
- **Hotspot analysis** with geographic context (nearby places from OSM)
- **Multi-year trend analysis** with response rates
- **Peak month** identification
- **Total fire counts** per park

### Deforestation Narrative (`/api/parks/{id}/deforestation-narrative`)
- **Trend direction**: improving/worsening/stable
- **5-year rolling average** comparison
- **Hotspots** from deforestation_clusters table
- **Varied pattern descriptions**

### Park Stats (`/api/parks/{id}/stats`)
- **Deforestation statistics**: total_loss_km2, worst_year, trend
- **Fixed fire_trend** total_fires calculation

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

---

## Testing

### Manual API Testing
```bash
# Park stats with deforestation
curl "http://localhost:8000/api/parks/COD_Virunga/stats?pwd=ngi2026" | jq '.deforestation'

# Enhanced fire narrative
curl "http://localhost:8000/api/parks/COD_Virunga/fire-narrative?pwd=ngi2026" | jq '.hotspots[:2], .trend'

# Deforestation with trends
curl "http://localhost:8000/api/parks/COD_Virunga/deforestation-narrative?pwd=ngi2026" | jq '.trend_direction'

# Settlement narrative
curl "http://localhost:8000/api/parks/COD_Virunga/settlement-narrative?pwd=ngi2026" | jq '.conflict_risk'
```

### Test Parks
- **COD_Virunga** - High data coverage, good for testing all features
- **CMR_Nki** - Pristine wilderness (0 settlements)
- **TZA_Serengeti** - Popular, well-documented
- **COD_Salonga** - Large, minimal deforestation data

---

## Database Schema (Key Tables)

| Table | Purpose |
|-------|--------|
| fire_detections | FIRMS satellite fire data (1.7M records) |
| park_settlements | GHSL human settlement data (15K records) |
| deforestation_events | Hansen Global Forest Watch data |
| deforestation_clusters | Clustered deforestation polygons |
| osm_roadless_data | Road network analysis |
| park_group_infractions | Fire infractions summary |
| osm_places | Place names from OpenStreetMap |
| park_documents | Management plans and documents |
| gpx_uploads | Uploaded GPS tracks |
| users | User accounts |

---

## Access

- **Local:** http://localhost:8000/?pwd=ngi2026
- **Production:** https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- **Passwords:** ngi2026, apn2026, j2026

---

## Important Notes

1. **Database is ~500 MB** - Be careful with queries
2. **Fire data has 1.7M records** - Use LIMIT in queries
3. **Password required** - All endpoints need `?pwd=` or cookie
4. **Service file:** `/etc/systemd/system/srv.service`
5. **Test GPX files:** `data/test_patrol_virunga.gpx`, `data/virunga_patrol.gpx`
