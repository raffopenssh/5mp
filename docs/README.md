# 5MP Conservation Monitoring - Documentation

## Overview

A Go web application for conservation monitoring of 162 African keystone protected areas. Features an interactive 3D globe visualization with fire detection, deforestation analysis, settlement data, patrol tracking, and legal framework information.

**Tech Stack:** Go 1.21+, SQLite, HTML/CSS/JS, MapLibre GL JS

---

## Quick Links

| Resource | URL |
|----------|-----|
| Live App | https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026 |
| DB Download | https://five-megapixel-conservation.exe.xyz:8000/static/downloads/five-megapixel-conservation_latest.sqlite3 |
| GitHub | https://github.com/raffopenssh/5mp |

---

## Documentation Index

### For AI Agents (Shelley)
- [AGENTS.md](../AGENTS.md) - Quick context for AI coding assistants
- [docs/SHELLEY_PROMPT_UI.md](SHELLEY_PROMPT_UI.md) - UI development instructions
- [docs/SHELLEY_PROMPT_ADMIN_UI.md](SHELLEY_PROMPT_ADMIN_UI.md) - Admin panel development

### For Humans
- [INSTALL.md](INSTALL.md) - Installation and setup guide
- [API.md](API.md) - API reference
- [DATABASE.md](DATABASE.md) - Database schema and data sources
- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture

### Project Status
- [../TODO.md](../TODO.md) - Current sprint status
- [CHANGELOG.md](CHANGELOG.md) - Recent changes

---

## Features

### Core Visualization
- **3D Globe** - Interactive MapLibre GL globe with 162 protected areas
- **Fire Detection** - Real-time fire data with trajectory analysis
- **Deforestation** - Yearly forest loss tracking with trend analysis
- **Settlements** - GHSL population and built-up area analysis
- **Patrol Tracking** - GPX upload with effort intensity visualization

### Analysis
- **Fire Trajectories** - Group movement tracking with NATO phonetic names
- **Fire Alerts** - Real-time notifications for groups entering parks
- **Narrative Generation** - text descriptions using OSM place names
- **Legal Frameworks** - Conservation law information for 19 countries

### User Features
- **Search** - Parks, countries, regions with autocomplete
- **Filtering** - By country, movement type, date range, bounding box
- **Starring** - Save parks, notifications, narratives for reports
- **Export** - CSV download, HTML reports, RSS feeds
- **Sharing** - URL state preservation for collaboration

---

## Access Credentials

| Type | Value |
|------|-------|
| App Passwords | test2026, REDACTED_PWD, REDACTED_PWD |
| Admin Email | admin@5mp.globe |
| Admin Password | REDACTED_PWD |

---

## Quick Start

```bash
# Clone and build
git clone https://github.com/raffopenssh/5mp.git
cd 5mp
make build

# Run (requires database - see INSTALL.md)
./server

# Access at http://localhost:8000/?pwd=test2026
```

---

## Directory Structure

```
5mp/
├── cmd/srv/          # Main entry point
├── srv/              # HTTP handlers and templates
│   ├── templates/    # HTML templates (globe.html is main UI)
│   ├── areas/        # Protected area data handling
│   └── gpx/          # GPX parsing and analysis
├── db/               # Database migrations and queries
│   ├── migrations/   # SQL migration files
│   ├── queries/      # SQLC query definitions
│   └── dbgen/        # Generated query code
├── data/             # Static data files
│   └── keystones_with_boundaries.json
├── scripts/          # Data processing scripts
│   ├── fire_nrt/     # FIRMS fire data download
│   └── *.py          # Various data processors
├── static/           # Static assets
│   └── downloads/    # Database downloads
└── docs/             # Documentation
```

---

## Data Sources

| Data | Source | Update Frequency |
|------|--------|------------------|
| Fire Detections | NASA FIRMS (VIIRS) | Daily (NRT) |
| Deforestation | Hansen Global Forest Change | Annual |
| Settlements | GHSL (Global Human Settlement Layer) | Static |
| Place Names | OpenStreetMap | Static |
| Park Boundaries | WDPA + Manual corrections | Static |

---

## Support

For issues or questions, create a GitHub issue or contact the development team.
