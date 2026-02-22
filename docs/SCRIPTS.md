# Scripts Reference

## Fire Pipeline Scripts

### `scripts/build_unified_fire_dataset.py`
**Purpose:** Build unified historical fire dataset from all sources.

**Usage:**
```bash
python3 scripts/build_unified_fire_dataset.py
```

**Inputs:**
- `fire_archive.zip` - Historical VIIRS CSVs (2018-2024)
- `data/fire_detections_2025_2026/` - 2025-2026 fires
- `data/fire_nrt/` - NRT fires

**Output:** `data/raw-fire-viirs-YYYYMMDD-YYYYMMDD/{park_id}.json`

**Features:**
- Spatial windowing (7 chunks with overlap)
- Memory efficient - processes one chunk at a time
- Resumable via progress file
- Deduplicates fires by lat/lon/date/time

---

### `scripts/rebuild_park_fire_analysis_v3.py`
**Purpose:** Cluster fires into groups and build trajectories (v3 algorithm).

**Usage:**
```bash
python3 scripts/rebuild_park_fire_analysis_v3.py           # Full rebuild
python3 scripts/rebuild_park_fire_analysis_v3.py --park CAF_Chinko  # Single park
```

**Inputs:**
- `data/raw-fire-viirs-*/` unified fire dataset

**Output:** `data/fire_groups_*/{park_id}.json`

**Algorithm (v3):**
- 12-hour time windows for centroid calculation
- 5km spatial clustering
- Progressive linking: 10km + 5km/day across gaps
- 3-day maximum gap between observations

---

### `scripts/load_fire_groups_to_db.py`
**Purpose:** Load fire groups from JSON to database with context enrichment.

**Usage:**
```bash
python3 scripts/load_fire_groups_to_db.py --force    # Full reload
python3 scripts/load_fire_groups_to_db.py --park CAF_Chinko  # Single park
```

**Input:** `data/fire_groups_*/` JSON files

**Output:**
- `feature_geometries` (fire_trajectory records)
- `park_group_infractions` (yearly stats)
- `park_fire_weekly` (weekly counts)

**Features:**
- Adds context from rivers, lakes, roads, places, settlements
- Classifies position: starts_inside, ends_inside, transits, entirely_outside
- Generates narratives with geographic context

---

### `scripts/analyze_fire_trajectories_v4.py`
**Purpose:** Enrich fire groups with context (rivers, roads, places).

**Usage:**
```bash
python3 scripts/analyze_fire_trajectories_v4.py
python3 scripts/analyze_fire_trajectories_v4.py --incremental
```

**Inputs:**
- `data/fire_groups_v2/`
- `data/rivers_hydro/`, `data/roads_heigit/`, `data/osm_places/`

**Output:** `data/fire_trajectories_v2/{park_id}.json`

---

### `scripts/load_fire_trajectories_to_db.py`
**Purpose:** Load trajectory geometries into database.

**Usage:**
```bash
python3 scripts/load_fire_trajectories_to_db.py --force
```

**Output:** `feature_geometries` table (type=fire_trajectory)

---

### `scripts/precompute_narratives_v4.py`
**Purpose:** Generate AI narratives for fire events.

**Usage:**
```bash
python3 scripts/precompute_narratives_v4.py
```

**Output:** `fire_narrative_cache` table

---

## Data Processing Scripts

### `scripts/download_fire_data.py`
Daily NRT fire download from NASA FIRMS.

### `scripts/backfill_fires_extended_buffer.py`
Backfill historical fires with extended buffer zones.

### `scripts/classify_features.py`
Classify settlement and deforestation events.

---

## Full Pipeline Execution Order

```bash
# 1. Build unified fire dataset (historical + recent)
python3 scripts/build_unified_fire_dataset.py

# 2. Cluster fires into groups with trajectories
python3 scripts/rebuild_park_fire_analysis_v2.py

# 3. Enrich with context data
python3 scripts/analyze_fire_trajectories_v4.py

# 4. Load to database
python3 scripts/load_fire_trajectories_to_db.py --force

# 5. Generate narratives
python3 scripts/precompute_narratives_v4.py
```
