# Reference (data files, APIs, DB stats, icons)

_Split out of AGENTS.md. Read when working on this area._

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

## External credentials

All live values in `secrets.env` (untracked, mode 600, loaded by
`/etc/systemd/system/5mp.service` via `EnvironmentFile`); names + placeholders
in `secrets.env.example`. Read them from the environment, never inline a
literal. In shell snippets: `set -a; source secrets.env; set +a`.

| Var | Service |
|---|---|
| `NASA_FIRMS_KEY` | FIRMS fire ingest |
| `GFW_API_KEY` | Global Forest Watch |
| `ACLED_USERNAME` / `ACLED_PASSWORD` | ACLED |
| `PROTECTEDPLANET_TOKEN` | Protected Planet (old literal is in git history — public) |
| `EARTHDATA_TOKEN` | NASA URS (nightlights, ~60-day expiry) |
| `ZENODO_TOKEN` | Zenodo publishing |
| `CARTO_API_KEY` | CARTO (`basemaps.cartocdn.com` + platform APIs) |

`CARTO_API_KEY` is **server-side only** — the dark base-map tile URLs in
`globe.html`, `fire_animation.html` and `fire_analysis.html` are browser-fetched
and must stay unauthenticated public endpoints; templating the key into them
publishes it. Use it only from Go/Python that runs on the VM, and treat its
absence as "fall back to the public endpoint", failing (if it must) by naming
the variable.

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
| fire_detections | 42.9M | VIIRS satellite fires, 3 sensors (2018-2026) |
| feature_geometries | 997K | GeoJSON polygons/lines |
| fire_narrative_cache | 162 | Precomputed fire narratives (v5) |
| park_rivers | 215K | HydroRIVERS data (161 parks) |
| park_settlements | 9,933 | Classified settlement clusters |
| osm_places | 271K | Place names for narratives (161 parks) |
| park_climate | 162 | Monthly climate/seasons |
| park_species | 39.5K | IUCN mammal species |

**Feature geometries by type:**
- fire_trajectory: 711,506 (2020-2026, v7 rebuild 2026-08-06)
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

// Escape backend prose AND convert its emoji in one step
insightHtml(s)  // for any API string that carries a leading glyph
```

⚠️ **`escapeHtml()` alone is not enough for text the backend wrote.**
`/api/parks/{id}/stats` returns `insights` as sentences that *start* with an
emoji (`"🏘️ 20 settlements detected…"`, `srv/park_stats_handlers.go`), and the
starred-report panel escaped them and nothing more — so the one surface that
shows them showed raw glyphs while the rest of the app used the font.
`emojiToIcon` had existed for exactly this since the migration and was wired
only into the fire-alert list. Use `insightHtml()` (globe.html): it escapes
first, then maps each glyph, and **drops** unmapped ones rather than passing
them through. Markdown export strips them the same way (`plain`).

An emoji in a Go string is therefore not a bug by itself — it is the API's
*token* for an icon. Adding one means adding its mapping in `emojiToIcon`.

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
| 👍 | `icon-thumbs-up` | Good response rate |

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
