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

