# Fire Analysis Pipeline Specification (v7)

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

---

## v7 Algorithm & Data Source (2026-08)

### ⚠️ SQLite is the canonical fire source, NOT `data/raw-fire-viirs-*/`

`data/raw-fire-viirs-20200101-20260222/{park}.json` is a **rolling window**, not an
archive. As of 2026-08 it held only ~6 months for 162 of 163 parks (CAF_Chinko:
18k fires in JSON vs **425k** in `fire_detections`).

The trajectory builder used to read those files, which meant a full
(non-incremental) rebuild would silently discard years of trajectories — the
pre-window history only survived as frozen group JSON carried forward by each
incremental run. `scripts/fire_source.py` now reads `fire_detections` (~50ms per
park via `idx_fire_pa_date`).

```bash
# Correct (default):
python3 scripts/rebuild_fire_trajectories_v5.py --parks A,B,C
# A/B against the legacy window only:
python3 scripts/rebuild_fire_trajectories_v5.py --park X --source json
```

The raw JSON files and their two nightly writers were deleted on 2026-08-05,
along with the `--source json` flag; the dead v4-era readers
`rebuild_fire_front.py` / `rebuild_fire_hull.py` moved to `scripts/deprecated/`
at the same time. Nothing in the trajectory path reads JSON any more.

### Multi-satellite ingest

All three operational VIIRS sensors are ingested (`FIRMS_SOURCES` in
`daily_fire_update.py`). Previously only NOAA-20, i.e. ~2 overpasses/day.

| Source | Sat code | Typical Africa/day |
|--------|----------|--------------------|
| `VIIRS_NOAA20_NRT` | `N20` | ~1,560 |
| `VIIRS_SNPP_NRT`   | `N`   | ~2,680 |
| `VIIRS_NOAA21_NRT` | `N21` | ~500 |

Codes are distinct, so the `fire_detections` UNIQUE constraint
`(lat, lon, acq_date, acq_time, satellite)` keeps them as separate real rows.
A partial sensor failure still ingests the rest and raises a SYSTEM notification.

`DEFAULT_DAYS` is 10 (was 5). The fetch window must be >= the FIRMS late-arrival
delay; at 5 days a detection arriving later than that was lost permanently,
since the rebuild window is 14 days. Overlap is free (`INSERT OR IGNORE`).

### Algorithm (v7)

Measured on the 6-park golden set vs v6, same fire window:

| Metric | Change |
|--------|--------|
| `fires_per_grp` | **+5.9%** (less fragmentation) |
| `zigzag_bad_pct` | **−36.2%** (cleaner geometry) |
| `frag_pct` | **−16.3%** |
| `mean_days` | +4.1% |
| `traj_pts` | +7.6% |
| `coverage_pct` | +1.1% |

1. **Hungarian assignment** (`scipy.linear_sum_assignment`) per time slice
   instead of greedy nearest-first, which could hand a cluster to the wrong
   track and cross two parallel fronts. Alone: `fires_per_grp` +4.2%,
   `frag_pct` −18.8%.
2. **Mass-aware match cost** — `MASS_PENALTY_KM=3.0` × |log10(size ratio)|, so a
   1-fire DBSCAN noise point can't outbid a 400-fire front for a track. Value
   chosen by sweep over 0/3/6/12.
3. **Holes honoured in `pct_inside`** — v6 tested only `ring[0]`, so donut-shaped
   parks counted enclave fires as inside. Now shapely prepared geometry.
4. **Real detection times** in trajectory point 4 (was the `'1200'` stub);
   `speed_km_day` from true elapsed time, floored at 1 day so `classify_group`
   thresholds stay valid.

### Persistent-hotspot mask: validated 2026-08-06, KEEP

The A/B was owed from the 2026-08-05 handover (the earlier one ran mid-backfill
and measured the backfill). Re-run on a frozen DB, 6 parks chosen for mask
density (`DZA_Djurdjura` 36 cells, `ZWE_Hwange` 40, `COD_Virunga` 63,
`GAB_Lopé` 31, `CAF_Dzanga_Park` 4, `ZMB_Kafue` 0 as a null control):

| Metric | mask off → on | Read |
|--------|---------------|------|
| `stationary_fire_pct` | 3.01 → **0.27** (−91%) | the point of the mask |
| `stationary_pct` | 0.27 → **0.04** (−86%) | −100% on DZA/COD/GAB |
| `mean_days` | 15.1 → 10.9 (−28%) | *expected*, see below |
| `traj_pts` | 10.3 → 7.3 (−29%) | *expected* |
| `coverage_pct` | 75.0 → 59.9 (−20%) | *expected* |
| `frag_pct` | 3.09 → 6.24 (+102%) | denominator artefact |
| `ZMB_Kafue` (0 cells) | byte-identical output | mask is inert where unused |

**Every headline metric "regresses", and that is the correct outcome.** The
harness rewards long, many-vertex, high-coverage groups; a lava lake is the
perfect such group. Masked-off examples:

| Group | fires | days | trajectory bbox |
|-------|-------|------|-----------------|
| `COD_Virunga_2025_grp_999bc103` | 8,218 | 75 | 0.04° × 0.02° (Nyiragongo) |
| `ZWE_Hwange_2025_grp_79670a6b` | 1,529 | **249** | 0.007° × 0.003° |
| `GAB_Lopé_2024_grp_af1e56f3` | 187 | 132 | 0.004° × 0.003° |
| `DZA_Djurdjura_2025_grp_110c868e` | 458 | 134 | 0.003° × 0.003° |

A "spreading_fire" that spreads 3 metres for 249 days is a flare. 377 of the
481 groups DZA loses start on a masked cell; on `GAB_Lopé` it is 169/192.
`frag_pct` rises only because these giants leave the denominator — the absolute
fragment count *falls* everywhere (DZA 347→201, GAB 144→45).

So the harness gained two metrics (`stationary_pct`, `stationary_fire_pct`:
groups ≥60 days whose whole trajectory fits in a <3 km box). Without them a
future agent re-running this A/B reads eleven ✗ marks and reverts a correct
feature. **Do not judge a detection-filtering change on `coverage_pct`.**

Residue: `ZWE_Hwange` keeps 2 stationary groups (1,102 fires) at ~26.515,
−18.433 — a real un-masked cell cluster (cell 7800,−5424: 853 detections over
**80 distinct months**, mean FRP 1.9). It is the same Hwange flare complex,
spread over neighbouring cells that individually clear the ≥30-month bar but
whose group seeds land on the un-listed fringe. Not a mask bug; a mask-recall
gap. Worth a dilate-by-one-cell experiment if it recurs elsewhere.

### Per-overpass slicing: implemented, OFF by default — re-tested 2026-08-06, STAYS OFF

`--overpass` slices by satellite overpass (gap-clustering `acq_dt` with
`OVERPASS_GAP_H=4`) rather than by calendar day.

The 2026-08-05 handover parked this as "the point of the backfill": with only
NOAA-20 the two daily passes were wildly asymmetric, and the expectation was
that three sensors would give **~6 passes/day**, each dense enough to stand on
its own. The full backfill is in, so it was finally testable.

**The premise is false.** The three VIIRS sensors are all in the same
sun-synchronous ~13:30 orbit plane, so their overpasses land on top of each
other, not spread across the day. MOZ_Niassa, July 2025:

| Sensor | Modal overpass hour (UTC) |
|--------|---------------------------|
| SNPP (`N`) | 10–11 |
| NOAA-20 (`N20`) | 10–12 |
| NOAA-21 (`N21`) | 10–12 |

Gap-clustering at 4 h therefore yields **1.71 slices/day** (2 on 10 days of 14,
1 on the other 4) — essentially the same day/night split as with one sensor.
Three sensors made each pass ~3× denser; they did not add passes. And the
day/night asymmetry that killed this in the first place is unchanged, because
it is physical (night fires are smaller and cooler), not a sampling artefact:

| Pass | detections | mean FRP |
|------|-----------:|---------:|
| day | 38,948 | 9.5 |
| night | 2,220 | 1.5 |

Still a 17× count gap and 6× FRP gap with all three sensors. Alternating a
39k-detection estimate with a 2k one injects the same oscillation as before.

A/B on the golden set (`data/eval/pre_overpass` vs `data/eval/overpass`):

| Metric | Change | |
|--------|--------|--|
| `groups` | +10.5% | same fires cut into more pieces |
| `fires_per_grp` | −10.6% | ✗ |
| `mean_days` | −22.4% | ✗ tracks retired early |
| `dup_pairs` | +16.0% | ✗ over-splitting |
| `frag_pct` | +16.6% | ✗ |
| `coverage_pct` | −3.3% | ✗ |
| `speed_p95` | +16.9% | spurious velocity from the day↔night centroid jump |
| `zigzag_bad_pct` | −17.8% | ✓ (only because groups are too short to zigzag) |

Every gate in the flip criterion fails. `stationary_pct` is 0 on both sides, so
this is not the hotspot-mask situation where the metrics mislead — the
regression is real.

**Conclusion: `USE_OVERPASS` stays `False`, and the "re-evaluate once more
sensors land" plan is closed, not deferred.** More VIIRS sensors cannot help;
they all fly the same orbit. This would only be worth revisiting with a sensor
in a genuinely different plane or a geostationary source (MSG/SEVIRI, ~15 min
cadence), which is a different ingest problem entirely.

### Historical note: the original single-sensor measurement

With only NOAA-20 on ZMB_Kafue the day pass averaged 763 fires at mean FRP
10.6, the night pass **63 fires at FRP 1.7**, median centroid offset 55 km;
`mean_days` −21%, `dup_pairs` +23%, `coverage` −2.4%. The 2026-08 re-test above
supersedes this but reaches the same conclusion for the same reason.

### NRT→SP reconciliation: measured, and it is a no-op (2026-08)

FIRMS serves each detection twice — NRT (minutes old, provisional) and, weeks
later, SP (Standard Processing, reprocessed with definitive ephemeris). We
ingest NRT nightly and never re-fetch, which *looked* like the last open gap in
the fire data path.

It is not. Measured across six windows whose DB copy is genuinely NRT
(`scripts/reconcile_nrt_sp.py`, results in `data/eval/nrt_sp/`):

| window | scope | rows | coords identical | acq_time revised | p99 Δt | drop | add |
|--------|-------|-----:|-----------------:|-----------------:|-------:|-----:|----:|
| Kafue 2025-07 | inside park | 3,699 | 100% | 0% | 0 min | 0% | 0% |
| Kafue 2026-05 | inside park | 489 | 100% | 66% | 2 min | 0% | 0% |
| Chinko 2026-04 | ≤100 km | 968 | 100% | 24% | 2 min | 0% | 0% |
| Niassa 2026-05 | ≤100 km | 510 | 100% | 62% | 2 min | 0% | 0% |
| Virunga 2026-03 | ≤100 km | 259 | 100% | 86% | 2 min | 0% | 0% |
| Serengeti 2026-05 | bbox | 62 | 100% | 18% | 1 min | 0% | 0% |

**Coordinates, FRP and confidence come back byte-identical.** The only field SP
revises is `acq_time`, by 1–2 minutes. Clustering is day-level (and even
`--overpass` slices at 4 h), so a 2-minute shift cannot move a single
trajectory — but it *is* enough to fork
`UNIQUE(latitude, longitude, acq_date, acq_time, satellite)`, so a naive SP
re-ingest would be all cost (duplicate rows, a 42.9M-row migration) and no
benefit. The rounded-key + `processing` column migration sketched here
previously is **not** being built.

Three traps that make this easy to measure wrong; the script handles all three:

- **Provenance.** SNPP/NOAA-21 history was backfilled *from SP* by
  `backfill_viirs_sensors.py`, so comparing those against SP is a tautology.
  Only NOAA-20 was ingested nightly as NRT. The script recovers this from
  AUTOINCREMENT id ordering (a nightly row has a smaller id than any row
  acquired a month later) and warns when a window looks backfilled — 2024-08
  NOAA-20, for instance, reads 0% NRT.
- **Time-bucket matching.** Keying pairs on exact `(date, acq_time)` throws
  away ~85% of true pairs precisely *because* SP re-timestamps the overpass, and
  that reads as a catastrophic drop rate. Match per day with acq_time as a
  scaled matching dimension instead (`--time-tol-min`, default 6).
- **Ingest scope.** Add/drop rates over a raw bbox are meaningless: our scope
  changed over the years (per-park bbox buffers → ParkAssigner 100 km → keep
  everything), FIRMS has no such notion. Unfiltered, SP "adds" up to 99% of
  rows that were simply never in scope. The verdict runs on a symmetric subset,
  both sides clipped with the same assigner.

Note SP/NRT coverage for one sensor never overlaps (NOAA-20 SP ends 2026-05-31,
NRT starts 06-01), so you can never fetch the same day both ways; the
comparison is always our DB copy vs the SP archive.

#### What ships: a monthly watchdog

Zero drift is a property of FIRMS' *current* processing, not a law. A new VIIRS
collection or an ephemeris fix could make SP genuinely relocate detections, and
we would otherwise learn about it from a user reporting a fire in the wrong
place. So the nightly cron re-measures one dense window on the 1st of each
month (`daily_fire_update.py` step 2e → `reconcile_nrt_sp.py --watchdog`,
read-only, ~40 s):

- writes `data/nrt_sp_audit.json`
- exit 4 = material drift → recorded as a failed step in
  `data/pipeline_status.json` (`nrt_sp_drift: true`, shown in the admin
  pipeline badge popover) **and** raises a SYSTEM notification
- exit 3 = inconclusive (too few matched rows) — not a failure

```bash
python3 scripts/reconcile_nrt_sp.py --dry-run              # show the plan
python3 scripts/reconcile_nrt_sp.py --watchdog             # what cron runs
python3 scripts/reconcile_nrt_sp.py --from 2026-05-13 --days 5 \
    --bbox 20,-16,32,-8 --json data/eval/nrt_sp/kafue_2026-05.json
```

If the watchdog ever fires, `--apply` does the reconciliation *through the
matcher*: paired rows become `UPDATE OR IGNORE ... WHERE id = ?` (so a revision
can never fork the UNIQUE key; collisions are skipped and counted), and only
genuinely-unpaired SP rows are inserted, with canonical park assignment.
Unpaired *DB* rows are left alone — SP omitting a detection is nearly always a
scope difference, not a retraction. `--apply` is a dry run until `--yes`, and
it is deliberately **not** wired into cron: edited detections do not propagate
on their own, so a real reconciliation is `--apply --yes` followed by
`build_fire_grid_agg.py --since` and a v5 rebuild of the affected parks.

### A/B eval harness

Never tune this pipeline by eye — use `scripts/eval_fire_trajectories.py`.

```bash
# Snapshot current production output for the golden parks
python3 scripts/eval_fire_trajectories.py --snapshot data/eval/baseline

# Build a candidate
python3 scripts/rebuild_fire_trajectories_v5.py --parks CAF_Chinko,ZMB_Kafue \
    --output-dir data/eval/candidate --set MASS_PENALTY_KM=6

# Compare
python3 scripts/eval_fire_trajectories.py \
    --baseline data/eval/baseline --candidate data/eval/candidate
```

Golden set: `CAF_Chinko` (transhumance), `ZMB_Kafue` (peak-season volume),
`COD_Virunga` (montane/agricultural edge), `TZA_Serengeti` (fast grassland
fronts), `CMR_Nki` (near-zero fire regression canary), `MOZ_Niassa` (high volume).

Ablation flags reproduce the v6 configuration **bit-exactly**, which is how the
numbers above were validated — always confirm this before trusting a delta:

```bash
python3 scripts/rebuild_fire_trajectories_v5.py --park ZMB_Kafue --source json \
  --no-overpass --no-mass-penalty --no-hungarian --output-dir data/eval/v6check
```

`--set NAME=VALUE` overrides any tuning constant.

### Notification volume control

Peak season generated **509–1,132 fire alerts per night** (25,154 in five weeks;
93 for a single park in one night), which buried everything else. Two gates in
`create_fire_notifications`:

1. **Status-transition only** — a group is re-announced only when its status
   actually changes ("Active → Entering" alerts, "Active → Active" is silent).
   Was: re-announce every group every 7 days. ~20% of alerts were pure repeats.
2. **`MAX_ALERTS_PER_PARK = 5`** per run, applied *after* the existing priority
   sort so Entering/Approaching survive over Cooling/Outside. The remainder
   becomes one rollup notification per park ("N more active fire groups").

Combined: ~509 → ~150 alerts/night.

### Performance

Step 3 ran ~100 subprocesses (one `--park` each), re-paying the sklearn/scipy
import, keystone load and DB connect every time (~6 min). Use `--parks a,b,c`
for one process with a shared read-only connection.

A partial run (`--park`/`--parks`) now **skips** the global trend/summary
rewrite; previously every per-park invocation overwrote
`data/fire_trends_v5/park_fire_trends.json` with just that park's data.
