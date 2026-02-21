# Shelley Prompt for New 5MP Fire Pipeline VM

Copy everything below the line when starting a new Shelley conversation:

---

## Task: Run Fire Pipeline on New VM

You are setting up a new VM to run the 5MP fire analysis pipeline. The production VM has limited disk space, so you'll run the full rebuild here and copy results back.

## Repository

```bash
git clone https://github.com/raffopenssh/5mp.git /home/exedev/5mp
cd /home/exedev/5mp
```

## Quick Setup

```bash
# Install dependencies
sudo apt update && sudo apt install -y golang python3 python3-pip python3-venv sqlite3 git curl make jq

# Python venv
cd /home/exedev/5mp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Build server (optional - only if you want to test locally)
make build
```

## Database

Download the production database backup (1.2GB):

```bash
curl -L "https://five-megapixel-conservation.exe.xyz:8000/static/downloads/5mp_backup_20260221.sqlite3?pwd=test2026" -o db.sqlite3
sqlite3 db.sqlite3 "PRAGMA integrity_check"
```

**Note:** The `fire_detections` table is empty in the backup. Raw fire data is in JSON files.

## Historic Fire Data Archive (Optional)

If you need the full historic VIIRS fire CSVs (2018-2024, ~5GB uncompressed):

**Google Drive:** https://drive.google.com/file/d/1w59TvLxsOjTSRQWeQx3XYEdzeSTydUXP/view?usp=share_link

```bash
# Download via gdown (pip install gdown)
gdown 1w59TvLxsOjTSRQWeQx3XYEdzeSTydUXP -O fire_archive.zip
unzip fire_archive.zip -d data/fire/
```

This archive contains the raw VIIRS CSVs that were processed into `fire_additional_buffer/*.json`.

---

## Data File Locations

All data is in the `data/` directory. Here's what's available:

### Fire Data (Your Main Focus)

| Directory | Files | Description |
|-----------|-------|-------------|
| `data/fire_nrt/` | 162 | **Raw NRT fires per park** - JSON with lat/lon/date/time/frp |
| `data/fire_groups_v2/` | 161 | **Clustered fire groups** - Output of Step 1 |
| `data/fire_analysis/` | 163 | Legacy fire analysis (being replaced) |
| `data/fire_trends/` | 2 | Aggregated daily/weekly/monthly counts |
| `data/fire_additional_buffer/` | 1009 | Historic fires in buffer zones |

### Context Data (For Enrichment)

| Directory | Files | Description |
|-----------|-------|-------------|
| `data/rivers_hydro/` | 161 | HydroRIVERS river data per park |
| `data/lakes_hydro/` | 161 | HydroLAKES lake data per park |
| `data/roads_heigit/` | 161 | HeiGIT road surface data |
| `data/osm_places/` | 176 | OSM place names |
| `data/settlement_events/` | 156 | Classified settlement clusters |
| `data/deforestation_events/` | 79 | Classified deforestation events |
| `data/climate/` | 2 | Monthly precipitation, seasons |

### Park Boundaries

```bash
# Main park boundaries file
data/keystones_with_boundaries.json  # 162 parks with GeoJSON boundaries
```

---

## Fire Pipeline Specification

**Full spec:** `docs/FIRE_PIPELINE.md`

### Pipeline Stages

```
Step 1: rebuild_park_fire_analysis_v2.py
        → Reads: fire_nrt/*.json, fire_additional_buffer/*.json
        → Writes: fire_groups_v2/*.json, fire_trends/*.json
        → DB: park_fire_daily, park_fire_weekly, park_fire_monthly tables

Step 2: analyze_fire_trajectories_v4.py  
        → Reads: fire_groups_v2/*.json, rivers_hydro/, roads_heigit/, etc.
        → Writes: fire_trajectories_v2/*.json (NOT in git - too large)

Step 3: load_fire_trajectories_to_db.py
        → Reads: fire_trajectories_v2/*.json
        → DB: feature_geometries table (fire_trajectory type)

Step 4: precompute_narratives_v4.py
        → Reads: fire_trajectories_v2/*.json
        → DB: fire_narrative_cache table
```

### Key Fix: Zigzag Trajectories

The previous code created zigzag lines because it connected all individual fire detections. The fix (already in the code) uses **1-6 time-based centroids per day**:

- VIIRS has 3 satellites (Suomi NPP, NOAA-20, NOAA-21) with ~2 overpasses each
- Fires are grouped into 4-hour windows (up to 6 periods per day)
- Each period gets one centroid point
- This shows intra-day movement without zigzags

---

## Run the Full Pipeline

```bash
cd /home/exedev/5mp
source .venv/bin/activate

# Step 1: Rebuild fire groups (reads fire_nrt + fire_additional_buffer JSONs)
# Creates fire_groups_v2/*.json and fire_trends/*.json
python3 scripts/rebuild_park_fire_analysis_v2.py
# Takes ~30-60 minutes

# Step 2: Enrich with context (rivers, roads, places, classification)
# Creates fire_trajectories_v2/*.json (large files, not in git)
python3 scripts/analyze_fire_trajectories_v4.py
# Takes ~30-60 minutes

# Step 3: Load trajectories to database
python3 scripts/load_fire_trajectories_to_db.py --force

# Step 4: Precompute narratives
python3 scripts/precompute_narratives_v4.py
```

### Test Single Park First

```bash
python3 scripts/rebuild_park_fire_analysis_v2.py --park CAF_Chinko
python3 scripts/analyze_fire_trajectories_v4.py --park CAF_Chinko

# Verify trajectory has 1-6 points per day (not dozens)
python3 -c "
import json
with open('data/fire_groups_v2/CAF_Chinko.json') as f:
    groups = json.load(f)
# Find multi-day group
for g in groups:
    if g['days'] >= 5:
        traj = g['trajectory']
        print(f'Days: {g[\"days\"]}, Trajectory points: {len(traj)}')
        # Should be roughly equal (1-6 points per day)
        break
"
```

---

## Copy Results Back to Production

After pipeline completes:

```bash
# Create archive of outputs
cd /home/exedev/5mp
tar czf fire_rebuild_output.tar.gz \
    data/fire_groups_v2/ \
    data/fire_trajectories_v2/ \
    data/fire_trends/

# SCP to production or upload somewhere accessible
# Then on production:
tar xzf fire_rebuild_output.tar.gz
python3 scripts/load_fire_trajectories_to_db.py --force
python3 scripts/precompute_narratives_v4.py
sudo systemctl restart 5mp
```

---

## Verification

```bash
# Check fire groups created
ls data/fire_groups_v2/*.json | wc -l  # Should be ~161

# Check trajectory points per day
python3 -c "
import json, os
from collections import Counter

for fn in sorted(os.listdir('data/fire_groups_v2'))[:5]:
    with open(f'data/fire_groups_v2/{fn}') as f:
        groups = json.load(f)
    multi = [g for g in groups if g['days'] >= 3]
    if multi:
        g = multi[0]
        # Points should be ~1-6x days
        ratio = len(g['trajectory']) / g['days']
        status = '✓' if 1 <= ratio <= 6 else '✗ ZIGZAG!'
        print(f'{fn}: {g[\"days\"]} days, {len(g[\"trajectory\"])} pts, ratio={ratio:.1f} {status}')
"

# Check trends generated
cat data/fire_trends/fire_trends_summary.json | jq '.total_groups, .total_fires'
```

---

## Credentials

| Purpose | Value |
|---------|-------|
| App password | test2026 |
| Admin login | see secrets.env |
| Production URL | https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026 |

---

## Communication

When complete, report:
1. Number of fire groups generated
2. Sample trajectory point ratios (points/days should be 1-6)
3. Any errors encountered
4. Size of output archive

I can access results via HTTPS if you run a server on port 8000.
