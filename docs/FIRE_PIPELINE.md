# Fire Analysis Pipeline Specification (v5)

## Overview

The fire analysis pipeline processes NASA VIIRS satellite fire detections into actionable conservation intelligence. It runs daily via cron at 3am UTC.

## Pipeline Stages

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐    ┌──────────────────┐    ┌────────────────────┐
│  1. DOWNLOAD    │───>│  2. CLUSTERING   │───>│  3. LOAD TO DB  │───>│  4. NARRATIVES   │───>│  5. NOTIFICATIONS  │
│  (NRT fires)    │    │  (v5 groups)     │    │  (enrichment)   │    │  (cache)         │    │  (alerts)          │
└─────────────────┘    └──────────────────┘    └─────────────────┘    └──────────────────┘    └────────────────────┘
     7 days              DBSCAN 5km            Context + Class       JSON → DB cache        Hurricane names
     Africa bbox         Track across days      Position compute      UI consumption        Status analysis
```

## Data Flow

### Input
- **NASA FIRMS API** → Raw fire detections (CSV)
- **Park boundaries** → `data/keystones_with_boundaries.json`

### Intermediate Files
| Directory | Purpose | Format |
|-----------|---------|--------|
| `data/fire_groups_v5/{park}.json` | Clustered groups with trajectories | JSON array |
| `data/export/fire_narratives/{park}.json` | Pre-computed narratives | JSON |

### Output
| Table | Purpose |
|-------|--------|
| `fire_detections` | Raw points (6M+ records) |
| `feature_geometries` | Trajectory LineStrings for map (type=fire_trajectory) |
| `fire_narrative_cache` | Pre-computed narratives per park |
| `park_group_infractions` | Yearly stats per park |
| `park_fire_weekly` | Weekly fire counts |
| `fire_group_names` | Persistent hurricane-style names (1,424+ mappings) |
| `fire_group_alerts` | Real-time alert status (synced daily) |
| `notifications` | User-facing alerts (fire_alert type, 433+ active) |

---

## Stage 1: Download

**Script:** `scripts/daily_fire_update.py`

**Purpose:** Download near-real-time (NRT) fire detections from NASA FIRMS.

**Parameters:**
- `--days 7` - Look back window (default, ensures no gaps)

**API:** `https://firms.modaps.eosdis.nasa.gov/api/area/csv/{API_KEY}/VIIRS_NOAA20_NRT/{bbox}/{days}`

**Output:** Inserted directly into `fire_detections` table (upsert, no deletions)

---

## Stage 2: Clustering (v5 Groups)

**Script:** `scripts/rebuild_fire_trajectories_v5.py`

**Purpose:** Cluster individual fire detections into fire "groups" representing single fire events.

**Algorithm (v5):**
1. Load fire detections for park + 30km buffer zone
2. Group by date
3. Within each day, group by 12-hour time windows
4. Apply DBSCAN clustering (eps=5km, min_samples=1)
5. Track clusters across consecutive days (same cluster if centroids <10km + 5km/day)
6. Build trajectory using **time-window centroids** (prevents zigzag artifacts)
7. Compute trajectory quality metrics:
   - `trajectory_type`: clean, cleaned, erratic
   - `zigzag_ratio`: deviation from straight line (0 = straight, >2 = erratic)
8. Compute group attributes:
   - `start_date`, `end_date`, `days`
   - `centroid` [lon, lat]
   - `trajectory` [[lon, lat, date, time], ...]
   - `fire_count` (detection count)
   - `distance_km`, `speed_km_day`, `direction`
   - `pct_inside` (% of trajectory inside park)
   - `cross_border`, `affected_parks`
   - `group_type` (spot_fire, local_fire, spreading_fire, etc.)

**Output:** `data/fire_groups_v5/{park_id}.json`

### Group Identification

Groups are identified by a **stable ID**:
```
feature_id = "{park_id}_{year}_grp_{hash}"
```

Where `hash` is derived from the group's start date and centroid.

---

## Stage 3: Load to Database

**Script:** `scripts/load_fire_groups_to_db.py`

**Purpose:** Load fire groups into database with context enrichment.

**Context Data:**
- Rivers (HydroRIVERS) - proximity
- Roads (HeiGIT) - proximity and surface type
- Places (OSM) - nearby villages, towns
- Settlements - nearby built-up areas
- Climate - seasonality (dry/wet/transition)

**Position Classification:**
| Position | Criteria |
|----------|----------|
| `starts_inside` | First point inside park boundary |
| `ends_inside` | Last point inside park boundary |
| `transits` | Passes through without stopping |
| `entirely_outside` | Never enters park |
| `contained` | Entirely within park |

**Output:**
- `feature_geometries` (LineString/Point with properties_json)
- `park_group_infractions` (yearly stats)
- `park_fire_weekly` (weekly counts)

### Properties JSON Schema (v5)

```json
{
  "feature_id": "CAF_Chinko_2025_grp_a1b2c3d4",
  "feature_type": "fire_trajectory",
  "group_type": "spreading_fire",
  "position": "ends_inside",
  "days": 4,
  "fires_total": 45,
  "direction": "NE",
  "distance_km": 12.5,
  "avg_speed_km_day": 3.1,
  "total_frp": 1250.5,
  "pct_inside": 85.0,
  "cross_border": false,
  "affected_parks": ["CAF_Chinko"],
  "season": "dry",
  "nearest_place": "Bakouma",
  "nearest_place_dist": 15.0,
  "nearest_river": "Chinko",
  "nearest_river_dist": 2.3,
  "trajectory_type": "clean",
  "zigzag_ratio": 0.3,
  "year": 2025,
  "narrative": "Fire group detected 2025-01-15 near Bakouma..."
}
```

---

## Stage 4: Narratives

**Script:** `scripts/precompute_narratives_v5.py`

**Purpose:** Pre-compute narrative summaries for fast UI loading.

**Process:**
1. Load trajectories from `feature_geometries` table
2. Aggregate statistics per park
3. Build summary narrative text
4. Store in `fire_narrative_cache` table and `data/export/fire_narratives/`

### Narrative Schema (v5)

```json
{
  "park_id": "CAF_Chinko",
  "park_name": "Chinko",
  "year": 2026,
  "total_fires": 45000,
  "total_groups": 2455,
  "total_frp": 125000.5,
  "response_rate": 15.2,
  "peak_month": "January",
  "summary": "From 2020-2026, Chinko experienced...",
  
  "management_fires": 150,
  "cross_border_groups": 45,
  "outside_park_groups": 800,
  "stopped_inside_groups": 350,
  "transited_groups": 25,
  
  "group_types": {"spot_fire": 500, "local_fire": 1200, ...},
  "seasons": {"dry": 1800, "transition": 600, "wet": 55},
  "directions": {"N": 300, "NE": 250, ...},
  
  "trajectory_types": {"clean": 1377, "cleaned": 754},
  "erratic_count": 0,
  "zigzag_count": 0,
  "clean_count": 1377,
  "avg_zigzag_ratio": 0.27,
  
  "trend": {
    "years": [{"year": 2020, "total_groups": 489, ...}, ...],
    "trend_direction": "stable",
    "avg_response_rate": 15.2,
    "worst_year": 2022,
    "worst_year_groups": 499
  },
  
  "climate": {
    "dry_season": "Dec-Feb",
    "rainy_season": "Jun-Sep",
    "climate_zone": "tropical_savanna"
  },
  
  "narratives": [
    {
      "group_num": 1,
      "feature_id": "CAF_Chinko_2025_grp_a1b2c3d4",
      "year": 2025,
      "start_date": "2025-01-15",
      "end_date": "2025-01-18",
      "days": 4,
      "fires_total": 45,
      "total_frp": 125.5,
      "distance_km": 12.5,
      "avg_speed_km_day": 3.1,
      "direction": "NE",
      "group_type": "spreading_fire",
      "position": "ends_inside",
      "pct_inside": 85.0,
      "cross_border": false,
      "season": "dry",
      "trajectory_type": "clean",
      "zigzag_ratio": 0.3,
      "origin": {
        "nearest_place": {"name": "Bakouma", "distance_km": 15.0},
        "nearest_river": {"name": "Chinko", "distance_km": 2.3}
      },
      "narrative": "Fire group detected..."
    }
  ]
}
```

---

## Stage 5: Notifications (Fire Alerts)

**Script:** `scripts/daily_fire_update.py` (Steps 6a-6b2)

**Purpose:** Create actionable fire notifications for park managers with stable tracking names and enhanced status information.

### Step 6a: Sync fire_group_alerts

**API Call:** `POST /api/update-fire-alerts?pwd=XXX`

**Process:**
1. Query feature_geometries for all fire trajectories with `end_date >= now - 14 days`
2. For each trajectory, determine status:
   - **active**: last seen within 3 days
   - **cooling**: last seen 3-7 days ago
3. Update `fire_group_alerts` table with current status
4. Clean up old alerts (left > 7 days, entered > 14 days)

**Result:** fire_group_alerts table synchronized with feature_geometries (source of truth)

### Step 6b1: Assign Stable Hurricane-Style Names

**Table:** `fire_group_names`

**Process:**
1. Query all fire trajectories for current year (e.g., `%_2026_grp_%`)
2. Sort by `start_date` (chronological order)
3. For each new fire group:
   - Assign NATO phonetic name: Alpha, Bravo, Charlie, ..., Zulu
   - Cycle with suffixes: Alpha-2, Bravo-2, ... (27th fire onwards)
   - Store mapping: `park_id` + `feature_id` → `friendly_name`
   - Record `first_seen_date` and `last_seen_date`
4. For existing groups:
   - Update `last_seen_date` only
   - **Name never changes** (like hurricane tracking)

**Example Mapping:**
```
park_id: CAF_Chinko
feature_id: CAF_Chinko_2026_grp_2caaa51b
friendly_name: Alpha-2  (STABLE FOREVER)
first_seen_date: 2026-02-26
last_seen_date: 2026-03-03
```

**Result:** Each fire has a persistent, memorable name for tracking over time.

### Step 6b2: Create Enhanced Notifications

**Table:** `notifications` (type = 'fire_alert')

**Process:**
1. Query active fire trajectories with their persistent names:
   ```sql
   SELECT fg.park_id, fg.feature_id, fg.properties_json, fg.end_date, fgn.friendly_name
   FROM feature_geometries fg
   JOIN fire_group_names fgn ON fg.park_id = fgn.park_id AND fg.feature_id = fgn.feature_id
   WHERE fg.feature_type = 'fire_trajectory'
     AND fg.end_date >= date('now', '-3 days')
   ```

2. For each fire, analyze status:
   - **⚠️ Approaching** (137): Fire outside park, moving toward boundary
   - **🌙 Gone Dark** (137): No detections for 3+ days (investigate)
   - **❄️ Cooling** (89): No new fires in 2 days (declining)
   - **📍 Contained** (28): Fully inside park boundaries
   - **⚡ Entering** (3): Crossing INTO park (CRITICAL)
   - **🚨 Leaving**: Started inside, moving toward boundary
   - **🌊 Transiting**: Crossing park boundary
   - **🔥 Outside** (37): Outside park, not approaching

3. Compute movement details:
   - Direction (N, S, E, W, NE, NW, SE, SW)
   - Speed classification:
     - **Fast**: > 2 km/day (urgent attention)
     - **Normal**: 0.5-2 km/day
     - **Slow**: < 0.5 km/day
     - **Stationary**: 0 km/day

4. Create notification with:
   - **Title**: `{emoji} {friendly_name} ({status})`
     - Example: "⚠️ Alpha-2 (Approaching)"
   - **Message**: `{fires} fires, {days} days • {status_detail}`
     - Example: "142 fires, 6 days • Outside, moving N at 5.3km/day (fast)"
   - **reference_data**: JSON with park_id, feature_id, friendly_name, status, status_detail

5. Skip if notification already exists (within 7 days, same feature_id)

**Example Notifications:**

```
⚠️ Alpha-2 (Approaching)
142 fires, 6 days • Outside, moving N at 5.3km/day (fast)

🌙 Echo-2 (Gone dark)
27 fires, 3 days • No detections for 3+ days

📍 Foxtrot-3 (Contained)
666 fires, 6 days • Fully inside park at 2.3km/day (fast)

❄️ Charlie-2 (Cooling)
41 fires, 4 days • No new fires in 2 days

⚡ Lima-5 (Entering)
18 fires, 1 days • Crossing into park from N at 1.2km/day
```

**Result:** 433 fire notifications across 24 parks with actionable status information.

### Notification Data Schema

```json
{
  "id": 1714,
  "park_id": "CAF_Chinko",
  "notification_type": "fire_alert",
  "title": "⚠️ Alpha-2 (Approaching)",
  "message": "142 fires, 6 days • Outside, moving N at 5.3km/day (fast)",
  "reference_id": "CAF_Chinko_2026_grp_2caaa51b",
  "reference_data": {
    "park_id": "CAF_Chinko",
    "park_name": "Chinko",
    "feature_id": "CAF_Chinko_2026_grp_2caaa51b",
    "type": "fire_trajectory",
    "group_name": "Alpha-2",
    "status": "Approaching",
    "status_detail": "Outside, moving N at 5.3km/day (fast)"
  },
  "created_at": "2026-03-03 03:00:15"
}
```

### Manager Benefits

**Before Enhancement:**
```
Notification: "🔥 Fire 07419ea4 | 142 fires, 6 days"

Questions:
❓ Which fire is this? (hash ID meaningless)
❓ Is it moving? In what direction?
❓ Is it approaching the park?
❓ Is it still burning or gone dark?
❓ How fast is it spreading?
```

**After Enhancement:**
```
Notification: "⚠️ Alpha-2 (Approaching) | 142 fires, 6 days • Outside, moving N at 5.3km/day (fast)"

Answers:
✅ Fire Alpha-2 (memorable, trackable)
✅ Moving north (direction clear)
✅ Approaching park from outside (boundary threat)
✅ Active (detected today)
✅ Fast-moving (5.3km/day = urgent)
```

### UI Integration

**Bell Icon Notification Dropdown:**
1. Shows all fire_alert notifications grouped by park
2. Clicking notification:
   - Closes dropdown
   - Fetches fire trajectory by feature_id (from features API)
   - Displays trajectory on map
   - Zooms to fire location
   - Auto-pins fire_trajectory layer
   - Shows stable friendly name in UI

**Tooltip (Fire-Realtime API):**
- Now includes `feature_id` and persistent `name` from fire_group_names
- Consistent with notification names
- Example: Both show "Alpha-2" for same fire

### Data Consistency

All systems now use persistent names and feature_ids:

| Source | Name | Feature ID | Status |
|--------|------|------------|--------|
| fire_group_names | Alpha-2 | CAF_Chinko_2026_grp_2caaa51b | ✅ Persistent |
| feature_geometries | (computed) | CAF_Chinko_2026_grp_2caaa51b | ✅ Source of truth |
| fire-realtime API | Alpha-2 | CAF_Chinko_2026_grp_2caaa51b | ✅ Joins names |
| notifications | Alpha-2 | CAF_Chinko_2026_grp_2caaa51b | ✅ Stable |
| UI tooltip | Alpha-2 | CAF_Chinko_2026_grp_2caaa51b | ✅ Consistent |
| UI notification | Alpha-2 | CAF_Chinko_2026_grp_2caaa51b | ✅ Click works |

---

## Daily Cron Job

**Script:** `scripts/daily_fire_update.py`

**Schedule:** `0 3 * * *` (3am UTC daily)

**Cron Entry:**
```bash
0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py --days 7 >> logs/daily_fire.log 2>&1
```

**Steps:**
1. Download NRT fires from FIRMS API (last 7 days, Africa bbox)
2. Insert new fires to `fire_detections` (upsert, no deletions)
3. Identify affected parks
4. Rebuild fire groups for affected parks only (v5 algorithm)
5. Update `feature_geometries` for new groups
6. Update `fire_narrative_cache` for affected parks
7. **Step 6a**: Sync fire_group_alerts table
8. **Step 6b1**: Assign persistent names to new fire groups
9. **Step 6b2**: Create/update fire alert notifications

**Logs:** `/home/exedev/5mp/logs/daily_fire.log`

---

## Key Design Decisions

### 1. Trajectory Smoothing (v5)

Problem: Raw fire detections create zigzag trajectories.

Solution:
- Group fires by 12-hour time windows
- Calculate centroid per window
- Connect centroids chronologically
- Track `zigzag_ratio` and `trajectory_type` for quality

### 2. Incremental Updates

Only process recent data to:
- Reduce daily processing time
- Update active fire groups
- Preserve historical analysis
- No deletions (append-only)

### 3. Stable IDs

Groups identified by `{park}_{year}_grp_{hash}` to:
- Allow updates without duplicates
- Support feature pinning in UI
- Enable consistent map layer management

### 4. Position vs Classification

- `position`: Computed from trajectory geometry (starts_inside, ends_inside, transits, etc.)
- `group_type`: Computed from behavior patterns (spot_fire, spreading_fire, etc.)

These are independent - a fire can be a "spot_fire" that "ends_inside".

---

## API Endpoints

### Fire Narrative (Cached)
```
GET /api/parks/{park_id}/fire-narrative?pwd=XXX
```
Returns pre-computed narrative from `fire_narrative_cache`.

### Fire Realtime (Live)
```
GET /api/parks/{park_id}/fire-realtime?pwd=XXX&days=28
```
Computes fire groups dynamically for recent data.

**Response includes (v5+):**
- `feature_id`: Stable identifier for each fire group
- `name`: Persistent friendly name from fire_group_names table
- `status`: active, cooling, dormant
- `trajectory`: Array of [{lat, lon, date}]

### Fire Features (Map)
```
GET /api/parks/{park_id}/features?type=fire_trajectory&pwd=XXX
```
Returns GeoJSON FeatureCollection for map rendering.

**Used for:** Notification click handler (no date filters)

### Fire Alerts
```
GET /api/fire-alerts?pwd=XXX&limit=1000
```
Returns recent fire group alerts from `fire_group_alerts` table.

**Ordering:** entered > active_inside > active > cooling > left

### Fire Notifications
```
GET /api/notifications?type=fire_alert&limit=500&pwd=XXX
```
Returns user-facing fire notifications with enhanced status.

**Includes:**
- Persistent friendly names (Alpha-2, Bravo, etc.)
- Status emoji and classification
- Movement direction and velocity
- Boundary threat assessment

### Update Fire Alerts (Cron)
```
POST /api/update-fire-alerts?pwd=XXX
```
Syncs fire_group_alerts table with feature_geometries.

**Called by:** Daily cron (Step 6a)

---

## Database Stats

| Table | Records | Description |
|-------|---------|-------------|
| `fire_detections` | 6.1M+ | Raw VIIRS fires (2018-2026) |
| `feature_geometries` (fire_trajectory) | 173K+ | Trajectory LineStrings |
| `fire_narrative_cache` | 162 | Pre-computed narratives |
| `park_group_infractions` | ~1,200 | Yearly stats (162 parks × ~7 years) |
| `fire_group_names` | 1,424+ | Persistent hurricane-style name mappings |
| `fire_group_alerts` | ~600 | Current alert status (cleaned daily) |
| `notifications` (fire_alert) | 433+ | Active user-facing notifications |

---

## Scripts Reference (v5)

| Script | Stage | Purpose |
|--------|-------|--------|
| `daily_fire_update.py` | 1-4 | Daily incremental pipeline |
| `rebuild_fire_trajectories_v5.py` | 2 | Cluster into groups |
| `load_fire_groups_to_db.py` | 3 | Load to database |
| `precompute_narratives_v5.py` | 4 | Build narratives |

### Deprecated Scripts (Do Not Use)

- `rebuild_park_fire_analysis*.py` (v1, v2, v3)
- `analyze_fire_trajectories*.py`
- `precompute_narratives*.py` (v1, v2, v3, v4)
- `load_fire_trajectories_to_db.py`
- `step1_*.py`, `step2_*.py`, `step3_*.py`

---

## Manual Full Rebuild

```bash
cd /home/exedev/5mp

# 1. Rebuild all fire groups (from fire_detections, ~2-3 hours)
python3 scripts/rebuild_fire_trajectories_v5.py

# 2. Load to database with context (~20 minutes)
python3 scripts/load_fire_groups_to_db.py --force

# 3. Generate narratives (~5 minutes)
python3 scripts/precompute_narratives_v5.py

# 4. Restart server
make build && sudo systemctl restart srv
```

### Single Park Rebuild

```bash
python3 scripts/rebuild_fire_trajectories_v5.py --park CAF_Chinko
python3 scripts/load_fire_groups_to_db.py --park CAF_Chinko --force
python3 scripts/precompute_narratives_v5.py  # Updates cache for all
```

---

## Troubleshooting

### No New Fires Inserted

**Symptom:** `Inserted 0 new fire records`

**Cause:** Fires already exist (UNIQUE constraint on lat/lon/date/time/satellite)

**This is normal** if running twice on same data.

### Missing v5 Fields in API

**Symptom:** `trajectory_types: None` in API response

**Cause:** Go struct missing fields or narrative cache not updated

**Fix:**
1. Update Go struct in `srv/narrative_handlers.go`
2. Run `make build && sudo systemctl restart srv`
3. Re-run `python3 scripts/precompute_narratives_v5.py`

### Zigzag Trajectories

**Symptom:** Trajectory lines show erratic zigzag patterns

**Cause:** Using raw detections instead of time-window centroids

**Fix:** Rebuild with v5 algorithm:
```bash
python3 scripts/rebuild_fire_trajectories_v5.py --park PARK_ID
```
