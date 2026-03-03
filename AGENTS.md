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
| `data/fire_groups_v5/` | 162 | Fire groups with v5 trajectories |
| `data/export/fire_narratives/` | 162 | Pre-computed fire narratives per park |
| `data/settlement_events/` | 156 | Classified settlements |
| `data/deforestation_events/` | 79 | Classified deforestation events |
| `data/rivers/` | 161 | HydroRIVERS data per park |
| `data/roads_heigit/` | 159 | Road surface data from HeiGIT |
| `data/osm_places/` | 91 | OSM place names |
| `data/climate/` | 1 | Monthly precipitation, seasons |
| `data/species/` | 1 | IUCN mammal species |
| `data/waterbodies/` | 137 | Global waterbody polygons |
| `data/export/` | 3 | Narrative summaries |

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
| fire_detections | 6.1M+ | VIIRS satellite fires (2018-2026) |
| feature_geometries | 458K | GeoJSON polygons/lines |
| fire_narrative_cache | 162 | Precomputed fire narratives (v5) |
| park_rivers | 215K | HydroRIVERS data (161 parks) |
| park_settlements | 9,933 | Classified settlement clusters |
| osm_places | 271K | Place names for narratives (161 parks) |
| park_climate | 162 | Monthly climate/seasons |
| park_species | 39.5K | IUCN mammal species |

**Feature geometries by type:**
- fire_trajectory: 173,066 (2020-2026, v5)
- deforestation: 221,277 (2001-2024)
- settlement: 64,016
- road: 26,550

---

## Background Workers

1. **Upload Queue** - Processes GPX uploads async (every 2s)
2. **GPX Learner** - Pattern detection from uploads
3. **Fire Daily Cron** - `scripts/daily_fire_update.py` (3am UTC)
   - Downloads NRT fires from FIRMS API
   - Updates fire_detections (upsert)
   - Rebuilds groups for affected parks
   - Updates narrative cache
4. **Narrative Cache Worker** - Pre-computes narratives (weekly full refresh)

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

## Data Processing Scripts (v5)

See `docs/SCRIPTS.md` and `docs/FIRE_PIPELINE.md` for full details.

```bash
# Full rebuild pipeline:

# 1. Rebuild fire groups with v5 algorithm
python3 scripts/rebuild_fire_trajectories_v5.py

# 2. Load to database with context enrichment
python3 scripts/load_fire_groups_to_db.py --force

# 3. Precompute v5 narratives
python3 scripts/precompute_narratives_v5.py

# Daily incremental update (runs via cron at 3am UTC):
python3 scripts/daily_fire_update.py --days 7
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

## Testing

### Run Tests

```bash
# Run all tests
./tests/run_all.sh

# Run specific test suites
./tests/run_all.sh db    # Database tests (37 tests)
./tests/run_all.sh api   # API tests (31 tests)
./tests/run_all.sh ui    # UI URL tests (20 tests)
```

### Browser Test Mode

Add `?test=1` to URL to enable `window.TEST` helper:

**Browser Setup:**
- Resize browser to **1280x1400** (or taller) to see full popup content
- This allows testing of all accordion sections without scrolling

```javascript
// Navigate to: http://localhost:8000/?pwd=test2026&test=1
// In browser console:
TEST.assertExists('#map', 'Map exists');
TEST.assertVisible('.stats-panel', 'Stats visible');
TEST.isPanelOpen('admin');  // Returns true/false
TEST.isPopupOpen('CAF_Chinko');
TEST.done();  // Print results
```

### Share Link Testing

URL params encode full UI state for reproducible tests:

| Param | Example | Description |
|-------|---------|-------------|
| `test` | `1` | Enable TEST helper |
| `panel` | `filter,star,admin,upload` | Open panel |
| `admin_tab` | `learning,features` | Admin tab |
| `popup` | `CAF_Chinko` | Open park popup |
| `sections` | `fire,deforestation` | Open accordions |
| `pinned` | `CAF_Chinko:fire_trajectory` | Pin layers |
| `starred_parks` | `CAF_Chinko,COD_Virunga` | Star parks |
| `notif` | `1` | Open notification dropdown |
| `notif_fire` | `CAF_Chinko:2026_grp_2caaa51b` | Zoom to fire + pin (see below) |
| `notif_upload` | `10.52,18.19` | Zoom to patrol location |
| `notif_pub` | `CAF_Chinko` | Open popup with research |
| `notif_download` | `123` | Download MBTiles file |

#### Fire Notification Share Links

Format: `?notif_fire=PARK_ID:YEAR_grp_HASH`

Example: `?notif_fire=CAF_Chinko:2026_grp_2caaa51b`

This will:
1. Open the notification dropdown
2. Expand the fire notification group for that park
3. Load the fire trajectory from features API
4. Look up friendly name from fire-realtime API (e.g., "Alpha-2")
5. Display trajectory on map and zoom to it
6. Pin the fire layer with friendly name

**To get the correct format:**
```bash
# Query notifications table
sqlite3 db.sqlite3 "SELECT park_id, reference_id FROM notifications WHERE notification_type = 'fire_alert' LIMIT 5"
# Returns: CAF_Chinko|CAF_Chinko_2026_grp_2caaa51b

# Format for share link: parkId:year_grp_hash
# Remove park prefix: CAF_Chinko:2026_grp_2caaa51b
```

### Playwright (Full UI)

```bash
npm install -D @playwright/test
npx playwright test tests/playwright/
```

---

## Authentication

### Password-Protected Endpoints

Most endpoints require password via:
- Cookie: `access_pwd=test2026`
- Query param: `?pwd=test2026`

Valid passwords: `test2026`, `REDACTED_PWD`, `REDACTED_PWD`

### Unauthenticated Endpoints

These paths bypass password check (see `srv/auth_middleware.go`):
- `/static/downloads/*` - Downloadable files
- `/robots.txt` - SEO
- `/sitemap.xml` - SEO
- `/static/robots.txt`
- `/static/sitemap.xml`

### Admin-Only Endpoints

Require admin login (see `srv/server.go` lines with `RequireAdmin`):
- `POST /admin/approve`, `/admin/reject`
- `POST /admin/upload/fire`, `/admin/upload/ghsl`
- `POST /api/admin/approve-feature`, `/api/admin/reject-feature`
- `POST /api/admin/bulk-approve`, `/api/admin/bulk-reject`
- `POST /api/admin/delete-upload`, `/api/admin/hide-notification`

---

## Background Workers

| Worker | Schedule | File | Description |
|--------|----------|------|-------------|
| Upload Queue | Every 2s | `srv/upload_queue.go` | Process GPX uploads |
| GPX Learner | Continuous | `srv/gpx_learner.go` | Pattern detection |
| Fire NRT | 3am UTC | `srv/fire_nrt.go` | Download daily fires |
| Fire Backfill | 4am UTC | `srv/fire_nrt.go` | Historical data |
| Narrative Cache | Weekly | `srv/fire_narrative_cache.go` | Pre-compute narratives |
| Publication Sync | Daily | `srv/research.go` | OpenAlex publications |
| FAOLEX Sync | Sundays | `srv/faolex_scraper.go` | Legal documents |

### FAOLEX Legal Documents

Syncs conservation-related legal documents from FAO FAOLEX database:
- Runs weekly on Sundays via `RunFAOLEXSync()`
- Creates `legal_documents` table on first run
- Searches by country ISO code and GADM region names
- Creates notifications for relevant new documents

API endpoint: `GET /api/parks/{id}/legal`

