# EarthRanger Autofetch & Patrol Classification – Complete Analysis

## 1. autofetch.go — EarthRanger Connection & Scheduling

### What it does
Manages the full lifecycle of automated GPS data fetching from EarthRanger (PAMDAS) instances:

- **CRUD for autofetch sources**: Add/list/enable/disable/delete EarthRanger connections
- **Credential management**: Passwords encrypted with AES-256-GCM before DB storage (key from `AUTOFETCH_SECRET` env var or auto-generated `.autofetch_key` file)
- **Background worker**: Checks every 15 minutes for sources whose `interval_h` has elapsed, then runs the Python fetch script
- **Upload queue cleanup**: Purges completed upload_queue BLOBs older than 7 days (runs every 6 hours)

### How fetching works
1. Go background worker decrypts stored credentials
2. Shells out to `python3 scripts/fetch_earthranger_gpx.py` with `--url`, `--user`, `--upload-url`, `--days` args
3. Password passed via `EARTHRANGER_PASSWORD` env var (never CLI args)
4. Upload URL points to `http://localhost:8000/api/upload/async?pwd=test2026`
5. Python script returns JSON `{ok, points, error}` on stdout
6. Go updates `last_run_at`, `last_status`, `last_points` in DB

### EarthRanger API interaction (in probeEarthRanger)
- Authenticates via `POST /oauth2/token` with OAuth2 password grant
- Tries client IDs: `das_web_client`, then `er_mobile_tracker`
- Park name extracted from URL hostname (hardcoded map: nyerere→"Nyerere NP, Ruaha NP", chinko→"Chinko", etc.)
- **No actual API data is fetched by the Go code** — all data fetching is in the Python script

### Python script (fetch_earthranger_gpx.py) — EarthRanger API fields used
- **`GET /api/v1.0/subjects?page_size=500`** — Lists all subjects
  - Uses: `subject_type` (filters to person/vehicle/aircraft only; blocks wildlife/animal/collar)
  - Uses: `id` (kept as opaque track name)
  - **IGNORES**: subject name, additional_info, last_position, tracks_available
- **`GET /api/v1.0/subject/{id}/subjectsources`** — Gets GPS source IDs for each subject  
  - Uses: `source` field from each item
- **`GET /api/v1.0/subject/{id}/source/{src}/tracks?since=...&until=...`** — Gets actual GPS tracks
  - Uses: `geometry.coordinates` (lon, lat pairs)
  - Uses: `properties.coordinateProperties.times` (timestamps)
  - **IGNORES**: elevation, accuracy, heading, speed (if present in ER)
  - Only includes points that have timestamps
- Output: Single anonymized GPX file with subject IDs as track names (no real names)
- **Subject type from ER is NOT passed through** — the GPX has no metadata about whether the ER subject was person/vehicle/aircraft. Classification is re-derived from GPS speed patterns.

---

## 2. movement_classifier.go — Speed/Trajectory-Based Movement Classification

### What it does
Classifies GPS tracks into movement type (foot/vehicle/aircraft) and activity type (patrol/reconnaissance/transit/logistics) based purely on trajectory analysis.

### MovementMetrics computed
- **AvgSpeedKmh, MaxSpeedKmh, MinSpeedKmh** (excluding stops <0.5 km/h)
- **SpeedVariance** (coefficient of variation, normalized 0-1)
- **BearingVariance** (average bearing change / 90°, normalized 0-1)
- **SmoothnessFactor** (1 - 0.5*speedVar - 0.5*bearingVar)
- **StopFrequency** (proportion of segments <0.5 km/h)
- **AccelerationScore** (average speed change / mean speed)
- **BoundingBoxKm, PointDensity, CentroidLat/Lon**
- **LinearityScore** (direct distance / total distance; >0.85 with low bearing variance = linear)
- **HasLandingPattern** (last third: speed drops from >30 to <10 km/h)
- **HasTakeoffPattern** (first third: speed rises from <10 to >30 km/h)

### Movement Type Classification (ClassifyMovementFull)

**Aircraft detection (checked first):**
- Landing/takeoff pattern detected → aircraft (95% confidence)
- Speed >150 km/h → aircraft (99%)
- Speed ≥100 + smoothness >0.5 → aircraft (90%)
- Speed 80-100 + smoothness >0.7 + bearingVar <0.1 → aircraft (80%)
- Speed ≥40 + smoothness >0.85 + linearity >0.9 + bearingVar <0.05 → aircraft (75%)

**If not aircraft:**
- Speed <7 km/h → **foot** (90% confidence)
- Speed ≥7 km/h → **vehicle** (85% confidence)

### Activity Type Classification
- **Foot**: default "patrol"; if speed 0.5-4 km/h OR high stop frequency/bearing variance → "reconnaissance"
- **Vehicle**: speed >60 + smooth >0.6 → "transit"; speed >40 + smooth >0.7 + linear >0.8 → "transit"; otherwise "patrol"
- **Aircraft**: linear >0.8 + smooth >0.7 → "logistics"; high bearing variance or stops → "patrol"; otherwise "logistics"

### Key insight
**The 7 km/h threshold is the sole boundary between foot and vehicle.** There are no intermediate checks. EarthRanger's `subject_type` field is completely ignored for classification.

---

## 3. gpx_analysis.go — Segment-Level Pattern Analysis

### What it does
Performs GPX segment analysis focused on patrol quality assessment. This is a simpler, separate classification system from movement_classifier.go.

### Classification (classifyMovement function — different from movement_classifier.go!)
| Avg Speed | Movement Type | Speed Category |
|-----------|--------------|----------------|
| <1 km/h | foot | stationary |
| 1-3 km/h | foot | slow_patrol |
| 3-6 km/h | foot | fast_patrol |
| 6-30 km/h | vehicle | slow_vehicle |
| 30-80 km/h | vehicle | fast_vehicle |
| 80-150 km/h | aircraft | low_altitude |
| >150 km/h | aircraft | high_altitude |

**Note:** This uses different speed thresholds than movement_classifier.go (6 km/h vs 7 km/h for foot/vehicle boundary).

### Pattern detection
- **Sinuosity**: total distance / direct distance (1.0 = straight; >1.2 = winding)
- **Circling detection**: sliding window of 5 bearings; >270° total bearing change = circling event
- **Messages**: Extracts non-default descriptions from GPX points (filters out "I'm checking in", "Tracking", "Waypoint")

### Coverage quality scoring (0-100)
- Base: 50 points
- slow_patrol: +30, fast_patrol: +20, slow_vehicle: +15, low_altitude: +20, high_altitude: -10
- High sinuosity (>2.0): +15; straight line: -15
- Circling events: +3 each
- Categories: excellent (≥80), good (≥60), moderate (≥40), poor (<40)

---

## 4. gpx_learner.go — Machine Learning from GPS Tracks (2516 lines)

### What it does
Background system that learns infrastructure (roads, airstrips, places) from accumulated GPS data. Processes a queue every 30 seconds.

### Processing pipeline
1. Picks pending learning jobs from `gpx_learning_queue` table
2. Loads `classified_segments_json` from `gpx_upload_logs`
3. Reconstructs Points from GeoJSON or raw `track_points` table
4. For each segment, processes by classification type:
   - **patrol** → collects foot patrol points for MCP (Minimum Convex Polygon) area
   - **road** → road learning pipeline (see below)
   - **boundary** → stored for reference
   - **aircraft** → airstrip detection pipeline (see below)
5. Detects stops (clusters of points <50m apart for >30 min)
6. Runs cross-track analysis (grid-based road detection)
7. Enriches with context (nearby rivers, OSM places, HeiGIT roads, settlements)
8. Stores results in `learning_results` table

### Road learning
- Simplifies vehicle tracks to 10m resolution
- Compares against HeiGIT reference roads (±30m matching threshold)
- **Only learns portions NOT matching existing reference roads**
- Tracks match count across uploads; confidence = min(matchCount × 25%, 95%)
- **Auto-approval**: confidence ≥90% AND ≥5 traversals → inserted into `feature_geometries`

### Airstrip detection
- Analyzes aircraft segments: last/first 2000m for approach/departure patterns
- Classifies aircraft: >150 km/h = fixed_wing, <80 km/h = rotor_wing, else mixed
- Estimates runway length (⅓ of approach distance, max 2000m) and heading
- Tracks landing/takeoff counts; confidence = min(count × 20%, 90%)
- **Auto-approval**: confidence ≥90% AND ≥5 total landings/takeoffs

### Place/base detection
- **Stop detection**: Points within 50m for >30 minutes
- Place classification by average stop duration:
  - >8 hours → headquarters
  - >4 hours → outpost
  - >1 hour → camp
  - >30 min → gate
- **Cross-track base detection**: Clusters track start/end points within 100m
  - 10+ endpoints + >60 min avg → base
  - 5+ endpoints → outpost
  - 3+ endpoints → camp

### Cross-track road detection (grid-based)
- Overlays 10m grid cells on all vehicle segments
- Cells traversed by ≥5 different tracks = likely road
- Connected components of high-traffic cells → road linestrings (min 50m)
- Confidence = min(avgTrackCount × 10%, 95%)

### Statistics tracked per park
- Vehicle: median/max/P90 speed, total distance/time
- Foot: median/max/P90 speed, total distance/time, 90% MCP area

### Context enrichment
Queries DB for nearby features within ±0.5° (~55km):
- Rivers (from `park_rivers` + `rivers` tables, ordered by discharge)
- OSM places (from `osm_places`, sorted by distance)
- HeiGIT roads (from `roads_heigit`, aggregated by highway_type/surface)
- Settlements (from `park_settlements`, sorted by distance)

---

## Summary: How Patrol Types Are Determined

**There are TWO independent classification systems with DIFFERENT thresholds:**

1. **movement_classifier.go** (`ClassifyMovementFull`): foot <7 km/h, vehicle 7-80 km/h, aircraft detected by pattern/speed
2. **gpx_analysis.go** (`classifyMovement`): foot <6 km/h, vehicle 6-80 km/h, aircraft >80 km/h

Additionally, `gpx_learner.go` has its own `inferMovementType`: foot <8 km/h, vehicle 8-100 km/h, aircraft >100 km/h.

**EarthRanger's subject_type (person/vehicle/aircraft) is fetched and used to FILTER subjects** (excluding wildlife) **but is NOT used for movement classification.** The subject type is lost when tracks are converted to GPX — classification is re-derived purely from GPS speed/trajectory analysis.

## Key EarthRanger API Fields

| Field | Used? | How |
|-------|-------|-----|
| subject_type | Yes | Filter only (exclude wildlife) |
| subject.id | Yes | Opaque track name in GPX |
| subject.name | **No** | Deliberately excluded (anonymization) |
| coordinates | Yes | Core GPS data |
| times | Yes | Speed calculation |
| elevation | **No** | Not fetched from ER |
| speed | **No** | Re-derived from coordinates+times |
| heading | **No** | Re-derived from coordinates |
| accuracy | **No** | Not fetched |
| additional_info | **No** | Not fetched |
