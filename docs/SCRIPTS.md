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
   - **Step 2c**: Refresh `fire_grid_day/week/month` (time animator backend)
   - **Step 2d**: Persistent hotspot mask (monthly, 1st only)
   - **Step 2e**: NRT→SP reconciliation audit (monthly, 1st only, read-only)
3. Rebuild fire groups for affected parks only
4. Update `feature_geometries` for new groups
5. Update `fire_narrative_cache` for affected parks
6. **Step 6a**: Sync `fire_group_alerts` with feature_geometries
7. **Step 6b1**: Assign persistent hurricane-style names to new fires
8. **Step 6b2**: Create enhanced fire alert notifications
9. **Step 7**: Fire consistency check (JSON vs features vs narratives)

**Cron Setup:**
```bash
0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py --days 7 >> logs/daily_fire.log 2>&1
```

**Fire Notification System (Steps 6a-6b2):**

These steps create actionable fire alerts for managers:

- **Step 6a - Sync Alerts**: Updates `fire_group_alerts` table
  - Calls `/api/update-fire-alerts` endpoint
  - Marks fires as: active (< 3 days), cooling (3-7 days)
  - Cleans old alerts (left > 7 days)

- **Step 6b1 - Assign Names**: Persistent hurricane-style naming
  - Queries `fire_group_names` table
  - Assigns NATO phonetic names chronologically: Alpha, Bravo, Charlie, ...
  - Names cycle with suffixes: Alpha-2, Bravo-2, ... (27th+ fire)
  - **Names persist forever** - like hurricane tracking
  - Example: "Alpha-2" stays "Alpha-2" regardless of intensity changes

- **Step 6b2 - Create Notifications**: Enhanced status analysis
  - Creates `notifications` table entries (type = 'fire_alert')
  - Analyzes fire status:
    - ⚠️ **Approaching**: Outside park, moving toward boundary
    - 🌙 **Gone Dark**: No detections 3+ days
    - ❄️ **Cooling**: No new fires in 2 days
    - 📍 **Contained**: Fully inside park
    - ⚡ **Entering**: Crossing into park (CRITICAL)
    - 🚨 **Leaving**: Moving toward boundary
    - 🌊 **Transiting**: Crossing park boundary
    - 🔥 **Outside**: Outside park, not approaching
  - Includes movement details:
    - Direction (N, S, E, W, etc.)
    - Speed (fast > 2km/day, normal 0.5-2km/day, slow < 0.5km/day)
    - Boundary threat assessment
  - Example notification: "⚠️ Alpha-2 (Approaching) | 142 fires, 6 days • Outside, moving N at 5.3km/day (fast)"

**Output:**
- `fire_group_names`: 1,424+ persistent name mappings
- `fire_group_alerts`: ~600 current alert statuses
- `notifications`: 433+ fire_alert notifications

**Benefits for Managers:**
- Stable fire names for tracking ("Check on Alpha-2 status")
- Quick visual status (emoji + classification)
- Movement direction and speed for resource planning
- Boundary threat assessment (approaching vs leaving)
- "Gone dark" detection for follow-up investigation

---

### `scripts/reconcile_nrt_sp.py`
**Purpose:** Audit (and, if ever needed, apply) FIRMS Standard-Processing
revisions of the NRT detections we ingest nightly.

Measured 2026-08 across six NRT-provenance windows: SP returns coordinates,
FRP and confidence **byte-identical**; only `acq_time` moves 1–2 min. That is
invisible to day-level clustering but would fork the `fire_detections` UNIQUE
key, so reconciliation is a no-op and is deliberately not applied. See
`docs/FIRE_PIPELINE.md` § NRT→SP and `data/eval/nrt_sp/`.

```bash
python3 scripts/reconcile_nrt_sp.py --dry-run       # fetch plan, no network
python3 scripts/reconcile_nrt_sp.py --watchdog      # what cron runs (step 2e)
python3 scripts/reconcile_nrt_sp.py --from 2026-05-13 --days 5 \
    --bbox 20,-16,32,-8 --json data/eval/nrt_sp/kafue_2026-05.json

# only if the watchdog fires: matcher-based UPDATE, dry run without --yes
python3 scripts/reconcile_nrt_sp.py --apply --from ... --days 5 --bbox ... --yes
```

**Exit codes:** 0 no action · 2 window outside the SP archive · 3 inconclusive
(too few matched rows) · 4 material drift (cron raises a SYSTEM notification
and marks the pipeline degraded).

After any `--apply --yes`, rerun `build_fire_grid_agg.py --since <window>` and
the v5 rebuild for the affected parks — edited detections do not propagate.

---

### `scripts/audit_fire_containment.py`
**Purpose:** Measure the gap between `protected_area_id` (the nearest park
within 100 km — a *catchment*) and actual containment, per park (F10).

Read-only, and deliberately not the same code as the re-derivation below: it
re-measures containment from the boundary file instead of trusting the
re-derivation's stamp, so a bug in the writer surfaces as a disagreement rather
than as agreement with itself.

```bash
python3 scripts/audit_fire_containment.py                    # all 163 parks, ~12 min
python3 scripts/audit_fire_containment.py --csv /tmp/f10.csv
python3 scripts/audit_fire_containment.py --park CAF_Chinko CMR_Nki
python3 scripts/audit_fire_containment.py --sample 20000     # labelled ESTIMATE
```

Reports three quantities that are routinely confused: `tagged` (the column),
`flagged` (`+ in_protected_area = 1`) and `inside` (point-in-polygon, measured
now). Before the F10 fix: 42,092,853 / 8,055,317 / 7,585,655, median 9.8× per
park. After: `flagged` == `inside` == 7,585,655, and `flagged but NOT inside`
is 0 — a non-zero value there means a boundary was edited and the flag is
stale. A park with no polygon reports `unmeasured`, not 0. See
`docs/agents/fire.md` § F10.

---

### `scripts/rederive_fire_containment.py`
**Purpose:** Recompute `fire_detections.in_protected_area` by point-in-polygon
against `data/keystones_with_boundaries.json` (F10, the writer).

The flag was an ingest-time answer from whichever rule ran that night, and
5.83% of it disagreed with today's boundary — 433,632 rows from one batch
(2026-02-26 → 2026-07-03, the bbox+0.5° `_find_park` that `ParkAssigner`
replaced). Corrects **both** directions (clearing only false positives moves
every count one way and reads as a trend), commits per park so the single
writer stays available, and leaves a park with no polygon **untouched** rather
than clearing it.

```bash
python3 scripts/rederive_fire_containment.py --dry-run          # counts only
python3 scripts/rederive_fire_containment.py --park CMR_Nki
python3 scripts/rederive_fire_containment.py                    # 163 parks, ~150 s
```

2026-08-13: 469,692 cleared, 30 set. Writes
`data/fire_containment_state.json` with the boundary file's SHA-256; a db test
compares it and fails when a boundary edit makes the flag stale.

---

### `scripts/build_sensor_epochs.py`
**Purpose:** Measure the satellite fleet behind the archive, per month, into
`fire_sensor_epochs` (F11).

One VIIRS sensor before 2024, three after, so every raw detection chart steps
~3× at that date for reasons that are instrument, not landscape. The count is
measured rather than typed because the fleet describes an ingest history that
grows nightly (invariant 2). Full scan, ~90 s; cron 04:30 on the 1st.

```bash
python3 scripts/build_sensor_epochs.py --dry-run
python3 scripts/build_sensor_epochs.py --since 2026-01
python3 scripts/build_sensor_epochs.py
```

A scan that returns fewer months than exist reports UNFINISHED and writes
nothing. `/api/parks/{id}/fire-trend` then carries `sensors`/`sensor_count` per
week plus `sensor_epochs_measured`, and the sparkline cuts the line where the
fleet changes. See `docs/agents/fire.md` § F11.

---

## Park Refresh

### `scripts/daily_park_refresh.py`
**Purpose:** Nightly per-park refresh — river naming, GFW deforestation ingest,
reclassification, **anomaly flagging (F9)**, fire-group reload, refresh endpoint,
JSON export. Cron 07:30, one park per run (`--rotate`).

```bash
python3 scripts/daily_park_refresh.py --park XSA_Study_Area --dry-run
python3 scripts/daily_park_refresh.py --rotate      # what cron runs
```

`flag_anomalous_years()` marks deforestation whose loss is a step change as
`needs_review`, so the UI can question the ground instead of drawing a spike.
It tests **two scales**, and the second is the point: park-wide it scored 0 on
`XSA_Study_Area`, the area F9 was written about, because 313.6 km² against a
48.9 km² five-year median is 6.4× and the threshold is 50× — the 1,000× step is
local, and summing the park averages it away. It now also tests ~0.5° (~55 km)
cells and flags the events in the offending cell. Comparisons are scoped to one
`area_method` (F8) and must clear `ANOMALY_MIN_KM2 = 5.0` absolutely. Flags are
recomputed from scratch each run, so a year that stops being anomalous loses
the flag. Current state: 19 flags, 99 rows, 8 of 81 areas; two db tests hold
both ends (the F9 block stays flagged; flags stay under 5 % of the corpus).

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

# 5. Assign persistent names and create notifications
# (Happens automatically in daily_fire_update.py, but can manually trigger:)
python3 -c "import requests; requests.post('http://localhost:8000/api/update-fire-alerts?pwd=test2026')"

# 6. Restart server
make build && sudo systemctl restart srv
```

---

## Deprecated Scripts (Do Not Use)

- `rebuild_park_fire_analysis.py` (v1, v2, v3)
- `analyze_fire_trajectories_v2.py`, `v3.py`, `v4.py`
- `precompute_narratives.py`, `v3.py`, `v4.py`
- `load_fire_trajectories_to_db.py`
- `step1_*.py`, `step2_*.py`, `step3_*.py`
