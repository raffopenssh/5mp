# Agent Instructions - 5MP Conservation Monitoring

## Quick Context

Go web app for conservation monitoring of 162 African protected areas.
Interactive 3D globe with fire detection, deforestation, settlements, patrol tracking.

**Live URL:** https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026

## 🔍 Quick Navigation for Agents

**First time? Start here:**
1. `docs/DATA_FLOW.md` - How data moves through the system
2. `docs/QUICK_TASKS.md` - Copy-paste solutions for common tasks
3. `docs/ARCHITECTURE_DECISIONS.md` - Why things are built this way

**Working on specific features?**
- Fire system: `docs/FIRE_PIPELINE.md` + `docs/FIRE_DATA_FLOW.md`
- API changes: `docs/API.md` + `docs/QUICK_TASKS.md` section 1
- Frontend UI: `docs/SHELLEY_PROMPT_UI.md` + `docs/DATA_FLOW.md` section 4
- Database: `docs/DATABASE.md` + `docs/QUICK_TASKS.md` section 9

**Key Insight**: This is a 17K-line single-page app. Don't try to understand everything at once.
Use the data flow maps to find the specific files you need to modify.

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

The server runs as a **systemd service** (`5mp.service`):

```bash
# Build and restart
make build && sudo systemctl restart 5mp

# Check status / logs
systemctl status 5mp
sudo journalctl -u 5mp -f
```

**Build details:**
- `make build` embeds git commit hash as version (shown in footer)
- Generates `.git-commits.txt` for version history modal (click version in UI)
- Version passed via `-ldflags "-X srv.exe.dev/srv.Version=$(VERSION)"`

Access: http://localhost:8000/?pwd=test2026

### Systemd Service (`5mp.service`)

The service auto-restarts on crash. Environment variables (e.g. `ZENODO_TOKEN`) are
configured in `/etc/systemd/system/5mp.service`. After editing, run:

```bash
sudo systemctl daemon-reload && sudo systemctl restart 5mp
```

### ⚠️ IMPORTANT: Keeping Version Up-to-Date

**ALWAYS rebuild after making changes to show the correct version:**

```bash
# After any code changes or git commits:
make build && sudo systemctl restart 5mp
```

**Why this matters:**
- Version shown in UI footer = git commit hash from build time
- Users can click version to see recent changes
- Old binary = stale version = confusion about what code is running

**Quick version check:**
```bash
# Check current running version:
curl -s "http://localhost:8000/api/version?pwd=test2026" | jq -r '.version'

# Check latest git commit:
git rev-parse --short HEAD

# If they don't match, rebuild:
make build && sudo systemctl restart 5mp
```

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

## Test Helpers (test=1 mode)

Add `?test=1` to URL to enable advanced testing tools:

**Entry ID Badges:** Blue numbered badges (0, 1, 2...) on fire/deforestation entries

**Key TEST Functions:**
```javascript
// Navigate & inspect
TEST.scrollToEntry('deforestation', 50)  // Scroll to entry by ID
TEST.scrollToText('fire', 'safari')      // Find by content
TEST.inspectEntry('fire', 10)            // Show full details
TEST.findBrokenEntries('deforestation')  // Scan for issues

// Manipulate UI
TEST.expandAll('CAF_Chinko')             // Expand all accordions
TEST.setPopupHeight(2000)                // Resize popup
TEST.triggerLoadMore('deforestation', 'CAF_Chinko')  // Click load more

// Shortcuts
TEST.testDeforest('CAF_Chinko', 100)    // Scroll+inspect+click entry
TEST.getEntryCount('fire')               // Count entries
```

**Benefits:** 50%+ token reduction in debugging (direct access replaces manual scrolling/clicking)

**Docs:** `docs/TEST_HELPERS.md`, `docs/TEST_HELPERS_QUICK_REF.md`

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

## Remote Database Backup

**Location:** exe-dev-monitor-peer01.exe.xyz  
**File:** 5mp_db_backup_20260302.sqlite3 (1.87 GB)  
**Uploaded:** 2026-03-04 09:18:53 UTC

**Access Credentials:**
```
File ID:  c8de734b-ad0e-4c25-b5bb-6e4ddef3f847
Token:    REDACTED_TOKEN
```

**Verification Status:**
- ✅ PRAGMA integrity_check: ok
- ✅ MD5: c4f7fff51e59277566d3d03e9eaf31a1
- ✅ 490,467 pages (4KB each), WAL mode
- ✅ Verified: 2026-03-04 09:43:56 UTC

**Download:**
```bash
curl -H "Authorization: Bearer REDACTED_TOKEN" \
  https://exe-dev-monitor-peer01.exe.xyz:8000/api/download/c8de734b-ad0e-4c25-b5bb-6e4ddef3f847 \
  -o db_backup_20260302.sqlite3
```

**Verify Integrity:**
```bash
curl -X POST -H "Authorization: Bearer REDACTED_TOKEN" \
  https://exe-dev-monitor-peer01.exe.xyz:8000/api/verify/c8de734b-ad0e-4c25-b5bb-6e4ddef3f847
```

See `backup_info.txt` for full details.

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

---

## Icon System - Lucide Icons

**All emojis replaced with Lucide icon font** for consistent styling across dark theme.

### Infrastructure

- **CDN**: `https://unpkg.com/lucide-static@latest/font/lucide.css` (2KB)
- **Icon font**: Uses `icon-{name}` classes with CSS `::before` pseudo-elements
- **Colors**: CSS utility classes (`.icon-color-fire`, `.icon-color-success`, etc.)

### Helper Functions

```javascript
// Generate icon HTML
icon('flame', 'fire')  // → <i class="icon-flame icon-color-fire"></i>
icon('zap', 'warning', 'lg')  // → <i class="icon-zap icon-color-warning icon-size-lg"></i>

// Convert backend emojis to icons
emojiToIcon('🔥')  // → <i class="icon-flame icon-color-fire"></i>
```

### Icon Colors

| Class | Color | Usage |
|-------|-------|-------|
| `icon-color-fire` | #ef4444 (red) | Active fires, errors |
| `icon-color-warning` | #f59e0b (orange) | Warnings, approaching fires |
| `icon-color-success` | #22c55e (green) | Success, checkmarks |
| `icon-color-info` | #3b82f6 (blue) | Info, downloads, water |
| `icon-color-cool` | #60a5fa (light blue) | Cooling fires |
| `icon-color-neutral` | #888 (gray) | Default, points, settlements |
| `icon-color-tree` | #22c55e (green) | Forest, nature |

### Common Icons

| Icon | Class | Usage |
|------|-------|-------|
| 🔥 | `icon-flame` | Active fires |
| ⚡ | `icon-zap` | Rapid fire spread |
| ❄️ | `icon-snowflake` | Cooling fires |
| ⚠️ | `icon-alert-triangle` | Warnings |
| ✓ | `icon-check` | Success |
| ✗ | `icon-x` | Errors |
| 🚶 | `icon-footprints` | Foot patrol |
| 🚗 | `icon-car` | Vehicle patrol |
| ✈️ | `icon-plane` | Aircraft patrol |
| 🌳 | `icon-tree-pine` | Forest/deforestation |
| 🏘️ | `icon-home` | Settlements |
| 🦁 | `icon-bug` | Biodiversity |
| ☀️ | `icon-sun` | Dry season |
| 🌧️ | `icon-cloud-rain` | Rainy season |
| 🗺️ | `icon-map` | Map/infrastructure |

### Usage Locations

1. **Fire status indicators** - Popup "Currently Active" section
2. **Notification panel** - All notification types (fire, upload, download, etc.)
3. **Star report stats** - Quick stats display
4. **Biodiversity/Climate sections** - Section titles
5. **Admin panel** - Upload section headers
6. **Movement types** - GPX track classification

### Benefits

- **Consistent styling** - All icons match dark theme colors
- **Performance** - 2KB font vs ~20KB emoji fallbacks (-90%)
- **Cross-platform** - No rendering differences between browsers/OS
- **Flexibility** - Easy color changes via CSS
- **Professional** - Clean, recognizable icon shapes

**Documentation**: See `LUCIDE_ICONS_PROGRESS.md` for full implementation details.

