# 5MP Conservation Monitoring - Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │            MapLibre GL JS (3D Globe)                 │   │
│  │  - Park boundaries (GeoJSON)                         │   │
│  │  - Fire/effort grid layers                           │   │
│  │  - Trajectory overlays                               │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTPS
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                      Go Web Server                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ HTTP Handlers│  │ Background   │  │ Area Store   │      │
│  │ - API        │  │ Workers      │  │ (in-memory)  │      │
│  │ - Upload     │  │ - GPX Queue  │  │ - 162 parks  │      │
│  │ - Narratives │  │ - Fire Alerts│  │ - Boundaries │      │
│  └──────┬───────┘  │ - Learning   │  └──────────────┘      │
│         │          └──────────────┘                         │
│         ▼                                                    │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                    SQLite Database                    │   │
│  │  - fire_detections (5M records)                      │   │
│  │  - effort_data, grid_cells                           │   │
│  │  - deforestation_events, park_settlements            │   │
│  │  - gpx_uploads, upload_queue                         │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                             │
                             ▼ (Daily cron)
┌─────────────────────────────────────────────────────────────┐
│                   External Data Sources                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ NASA FIRMS   │  │ Hansen GFC   │  │ GHSL         │      │
│  │ (Fire NRT)   │  │ (Deforest)   │  │ (Settlements)│      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

---

## Code Structure

### Entry Point
```
cmd/srv/main.go
  └── Creates Server, starts HTTP listener
```

### Server Package (`srv/`)

**Core:**
- `server.go` - HTTP routing, server initialization
- `auth_middleware.go` - Password/session authentication
- `api.go` - General API endpoints (stats, grid, areas)

**Narratives:**
- `narrative_handlers.go` - Fire/deforestation/settlement narratives
- `fire_realtime_handlers.go` - NRT fire analysis and alerts

**Upload:**
- `upload.go` - Synchronous GPX upload
- `upload_async.go` - Async upload endpoints
- `upload_queue.go` - Background upload processor
- `gpx_validation.go` - Track validation and classification
- `gpx_learner.go` - Pattern learning from uploads

**Data:**
- `areas/` - Protected area store and lookup
- `gpx/` - GPX parsing library

### Database (`db/`)

**Migrations:** `db/migrations/001-*.sql` through `020-*.sql`
**Queries:** `db/queries/queries.sql` (SQLC definitions)
**Generated:** `db/dbgen/` (SQLC-generated Go code)

### Scripts (`scripts/`)

**Fire Data:**
- `fire_nrt/download_nrt.py` - FIRMS API downloader
- `fire_nrt/trajectory_analyzer.py` - Group detection
- `fire_nrt/config.py` - API keys and proxies

**Data Processing:**
- `process_deforestation_polygons.py` - Hansen data processor
- `process_settlement_polygons.py` - GHSL processor
- `ghsl_data_manager.py` - GHSL tile management
- `download_hansen_tiles.py` - Hansen tile downloader

---

## Background Workers

### Upload Queue Processor
Processes queued GPX uploads every 2 seconds.

```go
// srv/upload_queue.go
func (p *UploadQueueProcessor) processLoop() {
    ticker := time.NewTicker(2 * time.Second)
    for {
        select {
        case <-ticker.C:
            p.processNextBatch()  // Up to 5 concurrent
        }
    }
}
```

### GPX Learner
Analyzes processed uploads for pattern detection.

```go
// srv/gpx_learner.go
func (l *GPXLearner) Start() {
    go l.processLoop()  // Background goroutine
}
```

### Fire Alert Updater
Can be triggered manually or via scheduled job.

```bash
# Manual trigger
curl -X POST "http://localhost:8000/api/admin/update-fire-alerts?pwd=test2026"
```

---

## Data Flow

### GPX Upload Flow
```
1. User uploads GPX file
   │
   ▼
2. /api/upload/async - Store file content, return queue_id
   │
   ▼
3. Background worker picks up pending item
   │
   ▼
4. Parse GPX → Split segments → Calculate distances
   │
   ▼
5. Validate & classify (patrol/road/boundary/static)
   │
   ▼
6. Persist to gpx_uploads, effort_data, grid_cells
   │
   ▼
7. Queue for learning analysis
   │
   ▼
8. Mark upload complete, store result JSON
```

### Fire Data Flow
```
1. Daily cron (3am UTC) triggers download
   │
   ▼
2. download_nrt.py fetches from FIRMS API (via proxy)
   │
   ▼
3. For each park: Request fires for last 5 days
   │
   ▼
4. Insert new records into fire_detections
   │
   ▼
5. Fire alerts updated (manual trigger or separate job)
```

---

## API Design Principles

### Authentication
- Simple password-based auth (`?pwd=test2026`)
- Session cookie after first auth
- Admin endpoints require admin session

### Response Formats
- JSON for API endpoints
- GeoJSON for map layers
- HTML for page rendering

### Error Handling
```json
{
  "error": "descriptive error message"
}
```

### Pagination
- Use `limit` parameter
- Large datasets return first N with "load more" option

---

## Performance Considerations

### Database
- SQLite with WAL mode for concurrent reads
- Indexes on frequently queried columns
- Pre-aggregated effort_data for fast grid rendering

### Memory
- Area boundaries loaded once at startup (~50MB)
- Fire data queried on-demand (not cached)

### Scaling
- Async upload queue prevents blocking
- Background workers run independently
- Can add more workers for higher throughput

---

## Security

### Authentication
- Password query param or cookie
- Admin-only endpoints protected by middleware
- No sensitive data exposed without auth

### Input Validation
- GPX files validated and sanitized
- SQL queries use parameterization
- File size limits enforced

### Rate Limiting
- FIRMS API has 5000 requests/10 minutes
- Proxy rotation for blocked IPs
