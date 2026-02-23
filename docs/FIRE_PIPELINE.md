# Fire Analysis Pipeline Specification (v5)

## Overview

The fire analysis pipeline processes NASA VIIRS satellite fire detections into actionable conservation intelligence. It runs daily via cron at 3am UTC.

## Pipeline Stages

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│  1. DOWNLOAD    │───>│  2. CLUSTERING   │───>│  3. LOAD TO DB  │───>│  4. NARRATIVES   │
│  (NRT fires)    │    │  (v5 groups)     │    │  (enrichment)   │    │  (cache)         │
└─────────────────┘    └──────────────────┘    └─────────────────┘    └──────────────────┘
     7 days              DBSCAN 5km            Context + Class       JSON → DB cache
     Africa bbox         Track across days      Position compute      UI consumption
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
4. Rebuild fire groups for affected parks only
5. Update `feature_geometries` for new groups
6. Update `fire_narrative_cache` for affected parks

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

### Fire Features (Map)
```
GET /api/parks/{park_id}/features?type=fire_trajectory&pwd=XXX
```
Returns GeoJSON FeatureCollection for map rendering.

---

## Database Stats

| Table | Records | Description |
|-------|---------|-------------|
| `fire_detections` | 6.1M+ | Raw VIIRS fires (2018-2026) |
| `feature_geometries` (fire_trajectory) | 173K+ | Trajectory LineStrings |
| `fire_narrative_cache` | 162 | Pre-computed narratives |
| `park_group_infractions` | ~1,200 | Yearly stats (162 parks × ~7 years) |

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
