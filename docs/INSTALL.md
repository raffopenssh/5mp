# 5MP Conservation Monitoring - Installation Guide

## Prerequisites

- Go 1.21 or later
- SQLite 3
- Python 3.8+ (for data processing scripts)
- ~2GB disk space for database

---

## Quick Installation

### 1. Clone Repository

```bash
git clone https://github.com/raffopenssh/5mp.git
cd 5mp
```

### 2. Download Database

The database contains pre-processed fire, deforestation, and settlement data.

```bash
# Download latest database
mkdir -p static/downloads
curl -o db.sqlite3 "https://five-megapixel-conservation.exe.xyz:8000/static/downloads/five-megapixel-conservation_latest.sqlite3"
```

Or use the download script:
```bash
./scripts/download_db.sh
```

### 3. Build and Run

```bash
make build
./server
```

The `make build` command:
- Compiles the Go server binary
- Embeds the git commit hash as the version number
- Generates `.git-commits.txt` with recent commit history (shown in UI version modal)

Access at: http://localhost:8000/?pwd=test2026

---

## Production Data Import

After cloning/pulling the repo, sync JSON data files to the database:

```bash
# Pull latest code and data files
git pull --rebase

# Import all JSON data to database (idempotent, safe to re-run)
python3 scripts/import_json_to_db.py

# Rebuild and restart server
make build
sudo systemctl restart srv
```

**What gets imported:**

| Source | Table | Records |
|--------|-------|---------|
| `data/fire_trajectories/*.json` | feature_geometries (fire_trajectory) | 130,708 |
| `data/feature_geometries/settlement/*.json` | feature_geometries (settlement) | 64,016 |
| `data/feature_geometries/deforestation/*.json` | feature_geometries (deforestation) | 221,277 |
| `data/roads_heigit/*.json` | feature_geometries (road) | 26,550 |
| `data/fire_analysis/*.json` | park_fire_analysis | 1,214 |
| `data/osm_places/*.json` | osm_places | 105,334 |
| `data/climate/park_climate.json` | park_climate | 162 |
| `data/waterbodies/*.json` | park_waterbodies | 2,573 |

The import script:
- Ensures exact match between JSON files and database
- Deletes orphan records not in JSON
- Inserts new records from JSON
- Safe to run multiple times (idempotent)

---

## First-Time Data Generation

If you need to regenerate data from scratch (not recommended - use DB download):

### Fire Data

1. **Get FIRMS API Key:**
   - Register at https://firms.modaps.eosdis.nasa.gov/api/
   - Update `scripts/fire_nrt/config.py` with your MAP_KEY

2. **Download Fire Data:**
   ```bash
   # Download historical data (slow - uses proxy)
   python3 scripts/fire_nrt/download_nrt.py --all --days 365
   
   # Or backfill specific range
   python3 scripts/fire_nrt/download_nrt.py --backfill --start 2024-01-01 --end 2024-12-31
   ```

3. **Set up Daily Updates:**
   ```bash
   sudo cp fire-nrt-daily.service /etc/systemd/system/
   sudo cp fire-nrt-daily.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now fire-nrt-daily.timer
   ```

### Deforestation Data

1. **Download Hansen Tiles:**
   ```bash
   python3 scripts/download_hansen_tiles.py
   ```

2. **Process Polygons:**
   ```bash
   python3 scripts/process_deforestation_polygons.py
   ```

### Settlement Data (GHSL)

1. **Download GHSL Tiles:**
   ```bash
   python3 scripts/ghsl_data_manager.py download
   ```

2. **Process Population Data:**
   ```bash
   python3 scripts/ghsl_data_manager.py process
   ```

3. **Generate Settlement Polygons:**
   ```bash
   python3 scripts/process_settlement_polygons.py
   ```

### OSM Place Names

```bash
python3 scripts/process_osm_places.py
```

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| PORT | HTTP server port | 8000 |
| DB_PATH | SQLite database path | db.sqlite3 |

### API Keys

Edit `scripts/fire_nrt/config.py`:
```python
MAP_KEY = "your_firms_api_key_here"
```

### Proxy Configuration

If FIRMS API is blocked, configure proxies in `scripts/fire_nrt/config.py`:
```python
PROXIES = [
    "95.213.217.168:52004",
    "194.58.34.63:3128",
]
```

See `scripts/fire_nrt/PROXY_INFO.md` for finding working proxies.

---

## Running as a Service

### Using systemd

```bash
# Copy service file
sudo cp srv.service /etc/systemd/system/srv.service

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable srv.service
sudo systemctl start srv

# Check status
systemctl status srv

# View logs
journalctl -u srv -f
```

### After Code Changes

```bash
make build
sudo systemctl restart srv
```

---

## Database Migrations

Migrations are applied automatically on server start. To run manually:

```bash
# Apply all migrations
sqlite3 db.sqlite3 < db/migrations/001-base.sql
sqlite3 db.sqlite3 < db/migrations/002-auth.sql
# ... etc
```

---

## Troubleshooting

### "database is locked"
SQLite doesn't handle concurrent writes well. Ensure only one process writes at a time.

### "FIRMS API timeout"
The NASA FIRMS API may block certain IP ranges. Use a proxy (see config).

### "migration failed"
Check if migration was partially applied:
```sql
SELECT * FROM migrations;
```

### Server won't start
Check port availability:
```bash
lsof -i :8000
```

---

## Development Setup

### Install Go
```bash
# Ubuntu/Debian
sudo apt install golang-go

# macOS
brew install go
```

### Install Python dependencies
```bash
pip install requests pandas geopandas shapely
```

### Run tests
```bash
go test ./...
```

---

## Backup

### Database Backup
```bash
sqlite3 db.sqlite3 ".backup backup_$(date +%Y%m%d).sqlite3"
```

### Full System Backup
```bash
tar -czf 5mp_backup_$(date +%Y%m%d).tar.gz \
    db.sqlite3 \
    data/ \
    static/downloads/
```
