# 5MP Conservation Monitoring - API Reference

All endpoints require authentication via `?pwd=test2026` query parameter or session cookie.

---

## Parks

### Get Park Stats
```
GET /api/parks/{id}/stats
```

**Parameters:**
- `id` - Park ID (e.g., `COD_Virunga`)
- `from` - Start date (YYYY-MM-DD)
- `to` - End date (YYYY-MM-DD)

**Response:**
```json
{
  "park_id": "COD_Virunga",
  "park_name": "Virunga",
  "fire_count": 15234,
  "deforestation": {
    "total_km2": 45.6,
    "trend": "worsening"
  },
  "settlements": {
    "count": 234,
    "population_est": 12500
  }
}
```

### Get Fire Narrative
```
GET /api/parks/{id}/fire-narrative
```

Returns rich text description of fire activity with hotspots, trends, and group movements.

### Get Fire Realtime (NRT)
```
GET /api/parks/{id}/fire-realtime?days=28
```

**Response:**
```json
{
  "park_id": "COD_Virunga",
  "total_fires": 430,
  "total_groups": 2,
  "active_groups_count": 2,
  "groups_inside_count": 1,
  "groups": [
    {
      "name": "Alpha",
      "type": "local_stationary",
      "is_active": true,
      "is_inside": true,
      "trajectory": [...]
    }
  ],
  "narrative": "Over the past 28 days, 2 distinct fire groups..."
}
```

### Get Fire Group GeoJSON
```
GET /api/parks/{id}/fire-group/{group}/geojson?days=28
```

Returns GeoJSON FeatureCollection with trajectory line and cluster points.

### Get Deforestation Narrative
```
GET /api/parks/{id}/deforestation-narrative
```

### Get Settlement Narrative
```
GET /api/parks/{id}/settlement-narrative
```

### Get Park Features (GeoJSON)
```
GET /api/parks/{id}/features?type={type}
```

**Types:** `fire_trajectory`, `deforestation`, `settlements`

---

## Fire Alerts

### Get Active Fire Alerts
```
GET /api/fire-alerts?limit=20
```

Returns fire group alerts for all parks.

**Response:**
```json
[
  {
    "park_id": "UGA_Murchison_Falls",
    "park_name": "Murchison Falls",
    "group_name": "Alpha",
    "alert_type": "entered",
    "fire_count": 27,
    "days_active": 3,
    "movement_direction": "north",
    "message": "🔥 Fire group Alpha entered Murchison Falls"
  }
]
```

### Update Fire Alerts (Admin)
```
POST /api/admin/update-fire-alerts
```

Triggers re-analysis of all parks with recent fire activity.

---

## Grid/Effort Data

### Get Grid Data
```
GET /api/grid
```

**Parameters:**
- `from`, `to` - Date range
- `type` - Movement types (comma-separated: foot,vehicle,aerial)
- `bbox` - Bounding box (minLng,minLat,maxLng,maxLat)

**Response:** GeoJSON FeatureCollection of grid cells with effort intensity.

---

## Stats

### Get Global Stats
```
GET /api/stats
```

**Parameters:**
- `from`, `to` - Date range
- `bbox` - Bounding box filter

**Response:**
```json
{
  "active_pixels": 187,
  "total_distance_km": 20319.97,
  "total_patrols": 403,
  "total_fires": 1372017,
  "total_deforestation": 1326.79,
  "total_settlements": 15066,
  "fire_trend": "down",
  "deforest_trend": "improving"
}
```

---

## Upload

### Synchronous Upload
```
POST /upload
Content-Type: multipart/form-data
```

**Form fields:**
- `gpx` - GPX file(s)

### Async Upload (Recommended for scale)
```
POST /api/upload/async
Content-Type: multipart/form-data
```

**Response:**
```json
{
  "queue_id": 1,
  "status": "pending",
  "message": "Upload queued for processing. Check status at /api/upload/status/1"
}
```

### Check Upload Status
```
GET /api/upload/status/{id}
```

**Response:**
```json
{
  "queue_id": 1,
  "status": "completed",
  "result": {
    "total_points": 1234,
    "total_distance": 45.6,
    "validation": {...}
  }
}
```

---

## Activity/Notifications

### Get Recent Activity
```
GET /api/activity
```

Returns recent GPX uploads and their locations.

---

## Search

### Search Areas
```
GET /api/areas/search?q={query}
```

Searches parks, countries, and regions.

---

## Export

### Export Parks CSV
```
GET /api/export/parks
```

### Get RSS Feed
```
GET /api/feed
```

Returns RSS feed of starred items and alerts.

---

## Admin Endpoints

All admin endpoints require admin authentication.

### GPX Learning Queue
```
GET /api/admin/learning-queue
POST /api/admin/process-learning
```

### Pending Approvals
```
GET /api/admin/pending-approvals
POST /api/admin/approve/{id}
POST /api/admin/reject/{id}
```

### Delete Upload
```
POST /api/admin/delete-upload
```
