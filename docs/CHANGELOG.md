# 5MP Conservation Monitoring - Changelog

## 2026-02-05

### Added
- **Async Upload Queue** - GPX uploads now process in background
  - `POST /api/upload/async` returns immediately with queue_id
  - `GET /api/upload/status/{id}` to check processing status
  - Scales to 5000+ daily uploads
  
- **Fire Trajectory Toggle** - Click notification icon to show/hide trajectory on map
- **Fire Group GeoJSON API** - `GET /api/parks/{id}/fire-group/{group}/geojson`

### Changed
- Database download now public (no password required)
- Hidden fire-glow layer for alpha (functionality preserved)

## 2026-02-04

### Added
- **Fire Group Alerts** - Real-time notifications for fire groups entering parks
  - `GET /api/fire-alerts` endpoint
  - Alert types: entered, active_inside, left
  - NATO phonetic naming (Alpha, Bravo, Charlie...)
  
- **Fire Realtime API** - `GET /api/parks/{id}/fire-realtime?days=28`
  - Trajectory analysis for fire groups
  - Movement classification (stationary, local, transhumance)
  - Inside-park detection

- **FIRMS NRT Download System**
  - Daily automatic downloads (3am UTC)
  - Historical backfill capability
  - Proxy support for blocked IPs

### Changed
- Stats API now filters by bounding box
- Grid API supports movement type filter
- Notification clicks show orange highlight

## 2026-02-03

### Added
- **Starring System** - Save parks, notifications, narratives for reports
- **Report Builder** - Export HTML reports with starred items
- **RSS Feed** - `GET /api/feed` for starred items

### Fixed
- Filter panel opens on first click
- Park popup sections collapsible

## Earlier

See git log for complete history:
```bash
git log --oneline -50
```
