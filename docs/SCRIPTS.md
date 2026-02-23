# Scripts Reference

## Fire Pipeline Scripts (v5)

### `scripts/rebuild_fire_trajectories_v5.py`
**Purpose:** Cluster fires into groups and build trajectories (v5 algorithm).

**Usage:**
```bash
python3 scripts/rebuild_fire_trajectories_v5.py           # Full rebuild
python3 scripts/rebuild_fire_trajectories_v5.py --park CAF_Chinko  # Single park
```

**Inputs:**
- `fire_detections` table in database

**Output:** `data/fire_groups_v5/{park_id}.json`

**Algorithm (v5):**
- 12-hour time windows for centroid calculation
- 5km spatial clustering (DBSCAN)
- Progressive linking: 10km + 5km/day across gaps
- 3-day maximum gap between observations
- Trajectory smoothing to eliminate zigzag artifacts
- New fields: `trajectory_type` (clean/cleaned/erratic), `zigzag_ratio`

---

### `scripts/load_fire_groups_to_db.py`
**Purpose:** Load fire groups from JSON to database with context enrichment.

**Usage:**
```bash
python3 scripts/load_fire_groups_to_db.py --force    # Full reload
python3 scripts/load_fire_groups_to_db.py --park CAF_Chinko  # Single park
```

**Input:** `data/fire_groups_v5/` JSON files

**Output:**
- `feature_geometries` (fire_trajectory records)
- `park_group_infractions` (yearly stats)
- `park_fire_weekly` (weekly counts)

**Features:**
- Adds context from rivers, lakes, roads, places, settlements
- Classifies position: starts_inside, ends_inside, transits, entirely_outside
- Generates narratives with geographic context
- Includes v5 fields: trajectory_type, zigzag_ratio, year

---

### `scripts/precompute_narratives_v5.py`
**Purpose:** Generate narratives for fire, settlement, and deforestation events.

**Usage:**
```bash
python3 scripts/precompute_narratives_v5.py
```

**Output:**
- `data/export/fire_narratives/{park_id}.json`
- `data/export/classified_settlements.json`
- `data/export/classified_deforestation.json`
- `fire_narrative_cache` table

**v5 Narrative Fields:**
- Park-level: `trajectory_types`, `erratic_count`, `zigzag_count`, `clean_count`, `avg_zigzag_ratio`, `seasons`, `directions`
- Per-trajectory: `trajectory_type`, `zigzag_ratio`

---

### `scripts/daily_fire_update.py`
**Purpose:** Daily incremental fire data update (runs via cron at 3am UTC).

**Usage:**
```bash
python3 scripts/daily_fire_update.py --days 7  # Default: 7 days
python3 scripts/daily_fire_update.py --days 2  # Test with 2 days
```

**Pipeline Steps:**
1. Download NRT fires from NASA FIRMS API (last N days)
2. Insert new fires to `fire_detections` (upsert, no deletions)
3. Rebuild fire groups for affected parks only
4. Update `feature_geometries` for new groups
5. Update `fire_narrative_cache` for affected parks

**Cron Setup:**
```bash
0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py --days 7 >> logs/daily_fire.log 2>&1
```

---

## Historical Data Scripts

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

### `scripts/download_fire_data.py`
**Purpose:** Download historical fire data from NASA FIRMS country archive.

---

## Full Pipeline Execution Order (v5)

```bash
# 1. Build unified fire dataset (historical + recent) - if rebuilding from scratch
python3 scripts/build_unified_fire_dataset.py

# 2. Rebuild fire groups with v5 algorithm
python3 scripts/rebuild_fire_trajectories_v5.py

# 3. Load to database with context enrichment
python3 scripts/load_fire_groups_to_db.py --force

# 4. Generate v5 narratives
python3 scripts/precompute_narratives_v5.py

# 5. Restart server
make build && sudo systemctl restart srv
```

---

## Deprecated Scripts (Do Not Use)

- `rebuild_park_fire_analysis.py` (v1, v2, v3)
- `analyze_fire_trajectories_v2.py`, `v3.py`, `v4.py`
- `precompute_narratives.py`, `v3.py`, `v4.py`
- `load_fire_trajectories_to_db.py`
- `step1_*.py`, `step2_*.py`, `step3_*.py`
