# Fire Analysis Pipeline Specification

## Overview

The fire analysis pipeline processes NASA VIIRS satellite fire detections into actionable conservation intelligence. It runs daily via cron at 3am UTC.

## Pipeline Stages

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│  1. DOWNLOAD    │───>│  2. CLUSTERING   │───>│  3. ENRICHMENT  │───>│  4. CACHE        │
│  (NRT fires)    │    │  (Daily groups)  │    │  (Trajectories) │    │  (Narratives)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘    └──────────────────┘
     5 days              DBSCAN 15km            Context + Class       JSON → DB cache
     50km buffer         Track across days      Outcome computation   UI consumption
```

## Data Flow

### Input
- **NASA FIRMS API** → Raw fire detections (CSV)
- **Park boundaries** → `data/keystones_with_boundaries.json`

### Intermediate Files
| Directory | Purpose | Format |
|-----------|---------|--------|
| `data/fire_nrt/` | Raw NRT downloads | CSV per date |
| `data/fire_groups_v2/{park}.json` | Clustered groups | JSON array |
| `data/fire_trajectories_v2/{park}.json` | Enriched trajectories | JSON array |

### Output
| Table | Purpose |
|-------|--------|
| `fire_detections` | Raw points (6M+ records) |
| `fire_narrative_cache` | Pre-computed narratives per park |
| `fire_group_alerts` | Active fire alerts |
| `feature_geometries` | Trajectory LineStrings for map |

---

## Stage 1: Download

**Script:** `scripts/fire_nrt/download_nrt.py`

**Purpose:** Download near-real-time (NRT) fire detections from NASA FIRMS.

**Parameters:**
- `--days 5` - Look back window (catches late-arriving data)
- `--buffer 50` - Kilometers outside park boundaries to include
- `--all` - Process all 162 parks

**Output:** `data/fire_nrt/{date}.csv`

**Note:** Also inserts into `fire_detections` table with `protected_area_id` assigned.

---

## Stage 2: Clustering (Daily Groups)

**Script:** `scripts/rebuild_park_fire_analysis_v2.py`

**Purpose:** Cluster individual fire detections into fire "groups" that represent single fire events.

**Algorithm:**
1. Load fire detections for park + buffer zone
2. Group by date
3. Apply DBSCAN clustering (eps=15km, min_samples=1)
4. Track clusters across consecutive days (same cluster if centroids <15km apart)
5. Build trajectory using **daily centroids** (one point per day, not individual detections)
   - This prevents zigzag artifacts when many detections occur across a large area in one day
6. Compute group attributes:
   - `start_date`, `end_date`, `days`
   - `centroid` [lon, lat]
   - `trajectory` [[lon, lat, date], ...]
   - `fires` (detection count)
   - `distance_km`, `speed_km_day`, `direction`
   - `pct_inside` (% of trajectory inside park)
   - `cross_border`, `affected_parks`
   - `group_type` (preliminary classification)

**Output:** `data/fire_groups_v2/{park_id}.json`

### Group Identification

Groups are identified by a **stable ID** that survives incremental updates:

```
group_id = "{park_id}_{start_date}_{start_centroid_hash}"
```

Where `start_centroid_hash` is the md5 hash of the group's first centroid position.

This allows:
- Active groups to update (add new days) without creating duplicates
- Incremental processing to merge with existing data
- Deterministic IDs that can be regenerated

### Incremental Mode

With `--incremental --days 14`:
1. Only process detections from last 14 days
2. Load existing groups from JSON
3. Update groups that overlap with the window
4. Add new groups
5. Remove duplicate group_ids (keep latest version)

---

## Stage 3: Enrichment (Trajectories)

**Script:** `scripts/analyze_fire_trajectories_v4.py`

**Purpose:** Enrich fire groups with context and compute final classification.

**Context Data:**
- Rivers (HydroRIVERS) - proximity and crossings
- Lakes (HydroLAKES) - proximity
- Roads (HeiGIT) - proximity and surface type
- Places (OSM) - nearby villages, towns
- Settlements - nearby built-up areas
- Deforestation events - correlation
- Climate - seasonality (dry/wet season)

**Classification:**
| Type | Criteria |
|------|----------|
| `management_controlled` | Short duration, stopped inside, near road |
| `management_spot` | Very short (1-2 days), small area |
| `transhumance` | Long trajectory, specific direction, dry season |
| `agricultural` | Near settlements, deforestation correlation |
| `external_fire` | Originated outside, entered park |
| `spot_fire` | Isolated, no clear pattern |

**Outcome Computation:**

```python
# Determine if fire "stopped inside" vs "transited"
last_point = trajectory[-1]
if park_boundary.contains(Point(last_point.lon, last_point.lat)):
    outcome = "STOPPED_INSIDE"
else:
    outcome = "TRANSITED"
```

**Output:** `data/fire_trajectories_v2/{park_id}.json`

### Trajectory Schema

```json
{
  "group_id": "CAF_Chinko_2025-01-15_a1b2c3d4",
  "feature_id": "CAF_Chinko_2025_grp_a1b2c3d4",
  "start_date": "2025-01-15",
  "end_date": "2025-01-18",
  "days": 4,
  "fires": 45,
  "centroid": [24.5, 6.2],
  "trajectory": [[24.4, 6.1, "2025-01-15"], ...],
  "trajectory_with_time": [{"lon": 24.4, "lat": 6.1, "date": "2025-01-15"}, ...],
  "distance_km": 12.5,
  "speed_km_day": 3.1,
  "direction": "NE",
  "pct_inside": 85.0,
  "cross_border": false,
  "affected_parks": ["CAF_Chinko"],
  "year": 2025,
  "group_type": "transhumance",
  "classification": {
    "primary_type": "transhumance",
    "confidence": 0.8,
    "factors": ["speed", "direction", "season"]
  },
  "context": {
    "nearest_river": {"name": "Chinko", "distance_km": 2.3},
    "nearest_place": {"name": "Bakouma", "distance_km": 15.0},
    "season": "dry"
  },
  "outcome": "STOPPED_INSIDE",
  "narrative": "Fire group detected 2025-01-15 near Bakouma..."
}
```

---

## Stage 4: Cache (Narratives)

**Script:** `scripts/precompute_narratives_v4.py`

**Purpose:** Pre-compute narrative summaries for fast UI loading.

**Process:**
1. Load trajectories from `data/fire_trajectories_v2/{park}.json`
2. Aggregate statistics (total fires, stopped/transited counts, response rate)
3. Build summary narrative text
4. Store in `fire_narrative_cache` table as JSON

**Output:** `fire_narrative_cache` table

### Cache Schema

```json
{
  "park_id": "CAF_Chinko",
  "park_name": "Chinko",
  "year": 2026,
  "total_fires": 45000,
  "response_rate": 15.2,
  "summary": "In 2018-2026, Chinko experienced...",
  "narratives": [
    {
      "group_id": "CAF_Chinko_2025-01-15_a1b2c3d4",
      "feature_id": "CAF_Chinko_2025_grp_a1b2c3d4",
      "start_date": "2025-01-15",
      "end_date": "2025-01-18",
      "entry_date": "2025-01-15",  // Alias for UI compatibility
      "last_inside": "2025-01-18", // Alias for UI compatibility
      "days": 4,
      "fires_inside": 45,
      "fires_total": 45,           // Alias
      "outcome": "STOPPED_INSIDE",
      "narrative": "Fire group detected...",
      "year": 2025
    }
  ],
  "trend": {
    "avg_response_rate": 15.2,
    "seasonality": "Dec-Feb",
    "years": [...],
    "weeks": [...]
  }
}
```

---

## Stage 5: Alerts (Go Server)

**Endpoint:** `POST /api/admin/update-fire-alerts`

**Purpose:** Update `fire_group_alerts` table with currently active fire groups.

**Criteria for "active":**
- Last detection within 3 days
- Still burning (no long gap)

---

## Database Load (Optional)

**Script:** `scripts/load_fire_trajectories_to_db.py`

**Purpose:** Load trajectory geometries into `feature_geometries` for map rendering.

---

## Cron Configuration

```bash
# /etc/cron.d/5mp-fire
# Daily fire NRT download and analysis (3am UTC)
0 3 * * * /home/exedev/5mp/scripts/fire_nrt/cron_daily.sh
```

### Daily Cron Script Steps

`scripts/fire_nrt/cron_daily.sh` runs these steps in order:

1. **Download NRT data** (5 days, 50km buffer)
   ```bash
   python3 scripts/fire_nrt/download_nrt.py --all --days 5 --buffer 50
   ```

2. **Incremental clustering** (14 day window)
   ```bash
   python3 scripts/rebuild_park_fire_analysis_v2.py --incremental --days 14
   ```

3. **Incremental trajectory enrichment**
   ```bash
   python3 scripts/analyze_fire_trajectories_v4.py --incremental --days 14
   ```

4. **Load trajectories to database**
   ```bash
   python3 scripts/load_fire_trajectories_to_db.py --force
   ```

5. **Update narrative cache**
   ```bash
   python3 scripts/precompute_narratives_v4.py --incremental --days 14
   ```

6. **Update fire alerts**
   ```bash
   curl -X POST "http://localhost:8000/api/admin/update-fire-alerts?pwd=test2026"
   ```

**Logs:** `/home/exedev/5mp/logs/fire_nrt_daily_YYYYMMDD.log`

---

## Key Design Decisions

### 1. Stable Group IDs

Groups are identified by `{park}_{start_date}_{centroid_hash}` to:
- Allow updates without duplicates
- Support incremental processing
- Enable feature pinning in UI

### 2. Field Name Aliases

Both old and new field names are supported:
- `start_date` / `entry_date`
- `end_date` / `last_inside`
- `fires` / `fires_inside` / `fires_total`

### 3. Outcome Computation

Computed from trajectory geometry, not classification:
- `STOPPED_INSIDE`: Last point inside park boundary
- `TRANSITED`: Last point outside park boundary

### 4. File-Based Intermediate Storage

JSON files in `data/` directories because:
- Easy to inspect and debug
- Git-trackable for small parks
- Atomic updates (write to .tmp, rename)
- No database locks during heavy processing

### 5. Incremental Processing

Only process recent data (14 days) to:
- Reduce daily processing time
- Update active fire groups
- Preserve historical analysis

---

## Troubleshooting

### Duplicate Groups

**Symptom:** Group count doubles after incremental update

**Cause:** `group_id` or `feature_id` is None/unstable

**Fix:** Ensure group_id is generated from stable attributes:
```python
group_id = f"{park_id}_{start_date}_{hash(centroid)[:8]}"
```

### Date Filtering Not Working

**Symptom:** UI shows all groups regardless of date filter

**Cause:** Field name mismatch (JS expects `entry_date`, data has `start_date`)

**Fix:** Support both field names in JS filter:
```javascript
const entryStr = n.entry_date || n.start_date;
```

### Wrong Outcome Counts

**Symptom:** All groups show as TRANSITED or STOPPED_INSIDE

**Cause:** Outcome computed from classification instead of trajectory

**Fix:** Compute outcome from last trajectory point:
```python
if park_boundary.contains(last_point):
    outcome = "STOPPED_INSIDE"
```

---

## Scripts Reference

| Script | Stage | Purpose |
|--------|-------|--------|
| `fire_nrt/download_nrt.py` | 1 | Download NRT data |
| `rebuild_park_fire_analysis_v2.py` | 2 | Cluster into groups |
| `analyze_fire_trajectories_v4.py` | 3 | Enrich with context |
| `precompute_narratives_v4.py` | 4 | Build cache |
| `load_fire_trajectories_to_db.py` | - | Load to feature_geometries |

### Deprecated Scripts (Do Not Use)

- `rebuild_park_fire_analysis.py` (v1)
- `analyze_fire_trajectories_v2.py`, `v3.py`
- `precompute_narratives.py`, `v3.py`
- `step1_*.py`, `step2_*.py`, `step3_*.py`

---

## Manual Full Rebuild

To rebuild all fire data from scratch (takes 1-2 hours):

```bash
# Activate venv if not already
cd /home/exedev/5mp
source .venv/bin/activate

# 1. Rebuild all fire groups (from fire_detections table)
python3 scripts/rebuild_park_fire_analysis_v2.py

# 2. Enrich with trajectories and context
python3 scripts/analyze_fire_trajectories_v4.py

# 3. Load trajectories to feature_geometries
python3 scripts/load_fire_trajectories_to_db.py --force

# 4. Precompute all narratives
python3 scripts/precompute_narratives_v4.py
```

### Full Rebuild for Single Park

```bash
# Rebuild just CAF_Chinko
python3 scripts/rebuild_park_fire_analysis_v2.py --park CAF_Chinko
python3 scripts/analyze_fire_trajectories_v4.py --park CAF_Chinko
python3 scripts/precompute_narratives_v4.py --park CAF_Chinko
```

---

## API Endpoints

### Fire Narrative (Cached)

```
GET /api/parks/{park_id}/fire-narrative?pwd=XXX
```

Returns pre-computed narrative from `fire_narrative_cache`.

**Response:**
```json
{
  "park_id": "CAF_Chinko",
  "park_name": "Chinko",
  "total_fires": 45000,
  "response_rate": 15.2,
  "summary": "In 2018-2026...",
  "narratives": [...],
  "trend": {...}
}
```

### Fire Realtime (Live)

```
GET /api/parks/{park_id}/fire-realtime?pwd=XXX&days=28
```

Computes fire groups dynamically for recent data (useful for testing).

### Fire Alerts

```
GET /api/fire-alerts?pwd=XXX&limit=10
```

Returns active fire group alerts.

### Fire Features (Map)

```
GET /api/parks/{park_id}/features?type=fire_trajectory&pwd=XXX
```

Returns GeoJSON FeatureCollection of trajectory lines for rendering.

---

## Data Validation

### Check Narrative Cache Counts

```sql
-- Parks with fire narratives
SELECT COUNT(DISTINCT park_id) FROM fire_narrative_cache;

-- Sample narrative group counts
SELECT park_id, 
       json_array_length(json_extract(narrative_json, '$.narratives')) as groups
FROM fire_narrative_cache
ORDER BY groups DESC
LIMIT 10;
```

### Check Trajectory Geometries

```sql
-- Fire trajectories in feature_geometries
SELECT COUNT(*) FROM feature_geometries WHERE feature_type = 'fire_trajectory';

-- Sample by park
SELECT park_id, COUNT(*) as count
FROM feature_geometries
WHERE feature_type = 'fire_trajectory'
GROUP BY park_id
ORDER BY count DESC
LIMIT 10;
```

### Verify Group Counts Match

```bash
# Count groups in JSON files
for f in data/fire_groups_v2/*.json; do
  echo "$(jq length "$f") $(basename $f .json)"
done | sort -n | tail -10
```
