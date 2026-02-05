# UI Development Instructions for Shelley

## New Backend Features to Integrate

### 1. Real-Time Fire Trajectory API (NEW)

**Endpoint:** `GET /api/parks/{id}/fire-realtime?days=28`

This endpoint provides real-time fire group tracking with:
- Group detection using spatial clustering
- NATO phonetic naming (Alpha, Bravo, Charlie...)
- Movement tracking and trajectory analysis
- Active group identification (groups still burning)
- Inside/outside park boundary status

**Example Request:**
```bash
curl "http://localhost:8000/api/parks/COD_Virunga/fire-realtime?pwd=test2026&days=28"
```

**Response Structure:**
```json
{
  "park_id": "COD_Virunga",
  "park_name": "Virunga",
  "analysis_period": "2026-01-07 to 2026-02-04",
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
      "fire_count": 350,
      "days_active": 6,
      "first_date": "2026-01-30",
      "last_date": "2026-02-04",
      "centroid": {"lat": -0.5, "lon": 29.5},
      "latest_position": {"lat": -0.48, "lon": 29.52},
      "movement": {
        "total_distance_km": 12.5,
        "avg_daily_km": 2.1,
        "direction": "northeast",
        "net_south_km": -3.2,
        "net_east_km": 8.1
      }
    }
  ],
  "narrative": "Over the past 28 days, 2 distinct fire groups have been detected..."
}
```

**UI Integration Ideas:**

1. **Fire Groups Panel** - Show active groups in the park info panel with:
   - Group name (Alpha, Bravo, etc.) in bold
   - Status badge: 🔴 ACTIVE INSIDE / 🟡 ACTIVE OUTSIDE / ⚪ INACTIVE
   - Days active, fire count
   - Movement direction arrow
   - Click to zoom to group location

2. **Map Visualization:**
   - Draw group centroids as pulsing markers
   - Color: Red for inside park, Orange for outside
   - Show trajectory lines connecting daily positions
   - Animate active groups with pulsing effect

3. **Alert Banner** - When `groups_inside_count > 0`:
   - Show warning: "⚠️ {N} active fire groups inside {park_name}"
   - List group names: "Groups Alpha, Charlie require attention"

4. **Notification Integration:**
   - Add fire group alerts to notification dropdown
   - "Fire group Alpha active in Virunga for 6 days"
   - Click to zoom to group

---

### 2. Movement Type Filter (Backend Ready)

The grid API now supports movement type filtering:

```
GET /api/grid?type=foot,vehicle&from=2023-01-01&to=2026-02-01
```

**Parameters:**
- `type`: Comma-separated movement types (foot, vehicle, aerial)
- When all 3 or none specified, uses pre-aggregated 'all' records

The UI already has Foot/Vehicle/Aerial toggles. Ensure they send the `type` parameter to `/api/grid`.

---

### 3. Bbox Filter for Stats (Backend Ready)

All stats are now filtered by bounding box:

```
GET /api/stats?bbox=28,-6,37,2&from=2023-01-01&to=2026-02-01
```

**Filtered metrics:**
- active_pixels (patrol grid cells)
- total_distance_km
- total_patrols
- total_fires
- total_deforestation
- total_settlements

The UI already sends bbox to stats. Verify it updates correctly when bbox changes.

---

### 4. NRT Fire Data Available

We now have Near Real-Time fire data:
- Data from 2025-01-01 onwards
- Updated daily at 3am UTC
- Backfill running for 2025 (check progress in logs)

Test with:
```bash
sqlite3 /home/exedev/5mp/db.sqlite3 "SELECT strftime('%Y-%m', acq_date) as month, COUNT(*) FROM fire_detections WHERE acq_date >= '2025-01-01' GROUP BY month;"
```

---

## Existing APIs Reference

### Fire Narrative (existing)
```
GET /api/parks/{id}/fire-narrative
```
Returns historical fire analysis with hotspots, trends, entry points.

### Park Stats (existing)
```
GET /api/parks/{id}/stats
```
Returns settlement, fire, deforestation stats.

### Deforestation Narrative (existing)
```
GET /api/parks/{id}/deforestation-narrative
```
Returns deforestation trends and clusters.

---

## Testing

```bash
# Test fire-realtime API
curl "http://localhost:8000/api/parks/COD_Virunga/fire-realtime?pwd=test2026&days=28" | jq '.groups[0]'

# Test with different analysis windows
curl "http://localhost:8000/api/parks/COD_Virunga/fire-realtime?pwd=test2026&days=7" | jq '.active_groups_count'

# Parks with recent fire activity
curl "http://localhost:8000/api/parks/SSD_Boma/fire-realtime?pwd=test2026&days=14" | jq '.groups | length'
```

---

## Priority UI Tasks

1. **HIGH:** Display active fire groups in park popup/panel
2. **HIGH:** Add visual indicator for parks with active inside-groups
3. **MEDIUM:** Show group trajectories on map
4. **MEDIUM:** Add fire group alerts to notifications
5. **LOW:** Movement type filter verification


---

## Fire Group Alerts API (NEW)

### Endpoint: `GET /api/fire-alerts?limit=20`

Returns fire group alerts for the notification dropdown. Shows:
- Groups that **entered** a park
- Groups **active inside** a park  
- Groups that **left** a park (visible for 24 hours after leaving)

**Response:**
```json
[
  {
    "id": 271,
    "park_id": "UGA_Murchison_Falls",
    "park_name": "Murchison Falls",
    "group_name": "Alpha",
    "alert_type": "entered",
    "first_detected_at": "2026-01-31T00:00:00Z",
    "last_updated_at": "2026-02-04T17:54:44Z",
    "fire_count": 27,
    "days_active": 3,
    "centroid_lat": 2.465,
    "centroid_lon": 32.098,
    "latest_lat": 2.492,
    "latest_lon": 32.075,
    "movement_direction": "north",
    "message": "🔥 Fire group Alpha entered Murchison Falls"
  }
]
```

### Alert Types
- `entered` - Group just detected inside park (< 2 days)
- `active_inside` - Group burning inside park for 2+ days
- `left` - Group no longer detected (removed after 24 hours)

### UI Integration

1. **Merge with existing notifications:**
   - Fetch both `/api/activity` (GPX uploads) and `/api/fire-alerts`
   - Sort by `last_updated_at` descending
   - Fire alerts show 🔥 emoji, GPX shows movement type icon

2. **Notification badge:**
   - Show count of `entered` + `active_inside` alerts
   - Red badge for active fire groups inside parks

3. **Click behavior:**
   - Zoom to `latest_lat`, `latest_lon`
   - Highlight the grid cell (same as GPX upload highlight)
   - Show park popup with fire-realtime data

4. **Message format already provided:**
   - `"🔥 Fire group Alpha entered Murchison Falls"`
   - `"🔥 Fire group Alpha active in Murchison Falls for 3 days (27 fires)"`
   - `"✓ Fire group Alpha left Murchison Falls"`

### Admin Endpoint: `POST /api/admin/update-fire-alerts`

Triggers re-analysis of all parks with recent fire data. Called automatically by the daily fire download cron job.

---

## Current Fire Data Status

The NRT fire data system is operational:
- **Daily downloads** at 3am UTC (last 5 days NRT data)
- **Backfill running** for 2025 historical data

Current coverage:
- 2025-01: 107,811 fires (99 parks)
- 2025-02: 102,512 fires (117 parks)
- 2025-03: 49,404 fires (116 parks)
- 2025-04: 13,270 fires (106 parks)
- 2025-05: 6,307 fires (93 parks)
- 2026-01: 6,792 fires (62 parks)
- 2026-02: 9,086 fires (67 parks)

---

## Integration Checklist for UI

- [x] Add fire alerts to notification dropdown (merge with GPX activity)
- [x] Show fire group alerts with 🔥 icon and movement direction
- [x] Badge showing count of active fire groups inside parks
- [x] Click alert → zoom to location + highlight grid cell
- [x] Park popup shows fire-realtime data when available
- [ ] Fire group trajectories on map (optional visualization)
- [x] Visual indicator on map for parks with active inside-groups (e.g., red glow on park boundary)

