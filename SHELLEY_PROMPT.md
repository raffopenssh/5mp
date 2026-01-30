# Quick Context for Shelley

## What This Is

5MP Conservation Monitoring - Go web app with 3D globe showing 162 African protected areas.
Features: Fire detection, deforestation tracking, settlement analysis, legal frameworks.

---

## ⚠️ DATABASE PROTECTION - READ FIRST

**The database has 4.6M fire records and took significant time to populate.**

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

## Priority Tasks

1. **Fire trajectory azimuth** - Add bearing display to narratives (e.g., "bearing 022°")
2. **Visual testing** - Screenshot verification of UI
3. **Park documents** - Add more management plans
4. **Service restart** - Currently inactive

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

# Test API
curl "http://localhost:8000/api/parks/COD_Virunga/stats?pwd=ngi2026" | jq
```

---

## File Locations

| What | Where |
|------|-------|
| Main server | `cmd/srv/main.go` |
| HTTP routes | `srv/server.go` |
| API handlers | `srv/api.go`, `srv/narrative_handlers.go` |
| Globe UI | `srv/templates/globe.html` |
| Database | `db.sqlite3` (1.3 GB) |
| Migrations | `db/migrations/` |

---

## Database Stats

| Table | Count |
|-------|-------|
| fire_detections | 4,621,211 |
| park_settlements | 15,066 |
| deforestation_events | 3,218 |
| osm_roadless_data | 162 |
| park_group_infractions | 801 |

---

## Access

- **Local:** http://localhost:8000/?pwd=ngi2026
- **Prod:** https://five-mp-conservation-effort.exe.xyz:8000/?pwd=ngi2026
- **Passwords:** ngi2026, apn2026, j2026

---

## Test Parks

- **COD_Virunga** - Has all data types, good for testing
- **CMR_Nki** - Pristine wilderness (0 settlements)
- **KEN_Masai_Mara** - Popular, well-documented

---

## Quick Health Check

```bash
# All in one
cd /home/exedev/5mp && \
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM fire_detections;" && \
curl -s "http://localhost:8000/api/parks?pwd=ngi2026" | jq '.parks | length'
```

Expected: `4621211` fires, `162` parks
