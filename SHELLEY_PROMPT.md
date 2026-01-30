# Quick Context for Shelley

## What This Is

5MP Conservation Monitoring - Go web app with 3D globe showing 162 African protected areas.
Features: Fire detection, deforestation tracking, settlement analysis, legal frameworks.

---

## ⚠️ DATABASE PROTECTION - READ FIRST

**The database has 1.7M fire records and took significant time to populate.**

### DO NOT:
- Run `DELETE` or `DROP` without explicit confirmation
- Run `UPDATE` on large tables without `WHERE` clause
- Truncate any tables
- Overwrite db.sqlite3

### ALWAYS:
- Use `LIMIT` when exploring data
- Back up before schema changes: `cp db.sqlite3 db.sqlite3.bak`
- Test queries with `SELECT` first

---

## Current Status (2026-01-31)

### Working Features ✓
- Fire narratives with hotspots and trends
- Deforestation narratives with trend analysis
- Settlement narratives with conflict risk
- Park stats with deforestation data
- Search, filters, grid selection
- Info modal (5MP Manifest)

### Needs Testing
- GPX upload (requires login)
- Patrol intensity visualization
- User registration flow

---

## Key Commands

```bash
# Build and run
make build && ./server

# Restart service
make build && sudo systemctl restart srv
journalctl -u srv -f

# Quick DB check
sqlite3 db.sqlite3 "SELECT 'fires', COUNT(*) FROM fire_detections;"

# Test enhanced fire narrative
curl "http://localhost:8000/api/parks/COD_Virunga/fire-narrative?pwd=ngi2026" | jq '.hotspots[:2]'

# Test deforestation trends
curl "http://localhost:8000/api/parks/COD_Virunga/deforestation-narrative?pwd=ngi2026" | jq '.trend_direction'
```

---

## File Locations

| What | Where |
|------|-------|
| Main server | `cmd/srv/main.go` |
| HTTP routes | `srv/server.go` |
| Narratives | `srv/narrative_handlers.go` |
| Park stats | `srv/park_stats_handlers.go` |
| Globe UI | `srv/templates/globe.html` |
| Database | `db.sqlite3` (~500 MB) |
| Test GPX | `data/virunga_patrol.gpx` |

---

## Database Stats

| Table | Count |
|-------|-------|
| fire_detections | 1,764,155 |
| park_settlements | 15,066 |
| deforestation_events | 293 |
| osm_places | 10,600 |
| park_group_infractions | ~800 |

---

## Access

- **Local:** http://localhost:8000/?pwd=ngi2026
- **Prod:** https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- **Testing VM:** https://fivemp-testing.exe.xyz:8000/?pwd=ngi2026 (more data)
- **Passwords:** ngi2026, apn2026, j2026

---

## Test Parks

- **COD_Virunga** - Has all data types, good for testing
- **CMR_Nki** - Pristine wilderness (0 settlements)
- **TZA_Serengeti** - Popular, well-documented

---

## Quick Health Check

```bash
# All in one
cd /home/exedev/5mpglobe && \
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM fire_detections;" && \
curl -s "http://localhost:8000/api/parks/COD_Virunga/stats?pwd=ngi2026" | jq '.deforestation.trend'
```

Expected: `1764155` fires, `"worsening"` trend
