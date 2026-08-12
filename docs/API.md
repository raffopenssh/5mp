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

### Get Climate Data
```
GET /api/parks/{id}/climate
```

**Response:**
```json
{
  "park_id": "CAF_Chinko",
  "temp_annual_c": 24.3,
  "temp_max_c": 33.8,
  "temp_min_c": 14.4,
  "precip_annual_mm": 1498,
  "precip_wettest_mm": 255,
  "precip_driest_mm": 6,
  "climate_zone": "Tropical Savanna",
  "rainy_season": "Jun-Sep",
  "dry_season": "Dec-Feb",
  "monthly_precip": [5, 17, 59, 113, 173, 204, 214, 247, 232, 182, 44, 6]
}
```

### Get IUCN Species
```
GET /api/parks/{id}/species
```

**Response:**
```json
{
  "park_id": "COD_Virunga",
  "total_species": 272,
  "threatened_count": 15,
  "species": [
    {
      "binomial": "Gorilla beringei",
      "common_name": "Mountain Gorilla",
      "status": "CR"
    }
  ]
}
```

### Get Park Features (GeoJSON)
```
GET /api/parks/{id}/features?type={type}&start={date}&end={date}&limit={n}&detail={tier}
```

**Types:** `fire_trajectory`, `deforestation`, `settlement`, `road`, `river`, `place`, `water`, `waterbody`

**Parameters:**
- `type` - Feature type (required)
- `start`, `end` - Date range filter (for fire_trajectory, deforestation)
- `limit` - Max features (default: 1000). The geography layers (`river`,
  `road`, `place`, `waterbody`) default to the WHOLE layer instead; only a
  limit below 5000 truncates them.
- `detail` - `major` | `main` | `all` (default `all`). Applies to `river`,
  `road` and `place`; ignored elsewhere. An unknown value is `all`, never an
  error — old share links predate the param.

**Detail tiers** are a stable WHERE clause, not a zoom-dependent cap, so a
share link reproduces the same picture every time:

| tier | river | road | place |
|---|---|---|---|
| `major` | stream_order >= 5 | motorway/trunk/primary | city, town |
| `main` | stream_order >= 3 | + secondary/tertiary/unclassified | + village |
| `all` | everything | + track/path/residential/service | + hamlet |

Each tier is cached separately (`narrative_cache`, `kind='features:<type>'`,
`params=<tier>`) and carries its own ETag.

**Response:** GeoJSON FeatureCollection

### Get Feature Stats
```
GET /api/parks/{id}/feature-stats
```

**Response:**
```json
{
  "fire_trajectories": 341,
  "settlements": 35,
  "deforestation_events": 24,
  "road_segments": 37,
  "places": 51,
  "waterbodies": 4,
  "rivers": 30
}
```

### Viewport Features (the LOD loader)
```
GET /api/features-in-bbox?type=fire_trajectory|deforestation|settlement
    &bbox=minLng,minLat,maxLng,maxLat
    &from=YYYY-MM-DD&to=YYYY-MM-DD
    &area={park or AOI id}      scope to one area's rows (see below)
    &class={classification}     e.g. slash_burn, agricultural, fishing
    &mode=auto|points           auto = the server picks the rendering
    &geom_budget=12000          how many real shapes the client will draw
    &limit=30000                how many features the answer may carry
    &seg=1                      cheap tier for a path = a chord, not a dot
    &spread=0                   escape hatch: biggest-N instead of spread
    &simplify=0                 escape hatch: full-precision rings
```

One endpoint behind `srv/static/lodlayer.js`, used by the stats-panel layer
toggles, pinned layers and the animator. `mode=auto` decides from the **true
count in view** — never from zoom — whether the answer is clickable geometry
(`render: "geometry"`), short direction chords (`render: "segments"`, with
`seg=1`) or bare centroids (`render: "points"`). Every rendering carries the
row id (`rid`), so `/api/feature-detail?id=` gives the same hover tip.

* **`area=`, not `park=`.** An AOI id in `?park=` is a hard 404 from
  `ParkIDMiddleware`. `park=` is still accepted for real parks.
* **`class=` is applied server-side**, before the selection, so `total` counts
  what passes it. It needs `area=`; a type with no classification
  (fire_trajectory) ignores it rather than returning nothing.
* **`spread=0`** turns off spread-select and returns the `limit` biggest
  features anywhere in the bbox. That is a *corner*, not a sample — every
  settlement has `stat_value = 0`, so the tie-break falls through to rowid and
  you get one contiguous ingest block. Only useful for reproducing a pre-2026-08
  answer or debugging the collector.
* **`simplify=0`** returns full-precision rings. The default decimates to half
  a screen pixel derived from the bbox (2.1 MB → 0.6 MB gzipped at continental
  zoom, byte-identical picture). Use it when the coordinates themselves are the
  output — an export or a diff — never for drawing.

Both escape hatches are deliberately undocumented in the UI: they exist so a
developer can prove the defaults are the same answer, and neither should be in
a share link.

### Feature Detail
```
GET /api/feature-detail?id={feature_geometries.id}
```
One row, geometry and properties, enriched exactly as the bbox endpoint
enriches it. This is what makes a cheap rendering still a feature rather than a
picture. An AOI's row is 404 for a non-owner.

### Export KML
```
GET /api/parks/{id}/export.kml?from={date}&to={date}
```

Exports park data as KML for Google Earth with:
- Park boundary
- Fire trajectories (filtered by date)
- Deforestation polygons
- Settlements
- Places
- Waterbodies

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

## Historical Maps

Scanned survey series draped over the basemap. See
`scripts/histmaps/README.md` for how the archive is produced.

### Get Archive Metadata
```
GET /api/histmap
```

**Response** (when installed):
```json
{
  "available": true,
  "id": "sudan250k",
  "name": "Sudan Survey 1:250,000 (1908-1944)",
  "bounds": [17.995605, 7.993957, 40.517578, 24.006326],
  "center": [29.256592, 16.000142, 7],
  "minzoom": 0, "maxzoom": 14,
  "tiles": "/api/histmap/sudan250k/{z}/{x}/{y}.png",
  "download": "/api/histmap/sudan250k/download",
  "size_bytes": 1471717376,
  "attribution": "Sudan Survey Dept., Khartoum / Library of Congress ..."
}
```

When the archive is not installed the response is
`{"available": false, "reason": "..."}` with HTTP 200 — the client greys the
toggle out rather than treating a missing optional dataset as an error.

### Get Tile
```
GET /api/histmap/sudan250k/{z}/{x}/{y}.png
```

XYZ order (the handler flips to the MBTiles' TMS row internally). Returns
`image/png` RGBA with a transparent background and near-black ink.

**`204 No Content` means "no sheet here"**, which is the normal case: the series
covers 8 of 22 1:1M blocks. Do not treat it as an error.

Cached `public, max-age=604800, immutable`.

### Download Archive
```
GET /api/histmap/sudan250k/download
```

The MBTiles file itself (1.4 GB), for offline use in Locus Map, OsmAnd or QGIS.
Supports Range requests so a field-link transfer can be resumed.

Ink is **black** in the download and white only on screen: the whitening is a
client-side `raster-brightness-min`, applied because the globe's basemap is dark.
Offline viewers default to light backgrounds, where white ink would be invisible.

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
