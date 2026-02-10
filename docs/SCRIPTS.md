# Script Reference - 5MP Conservation Monitoring

Quick reference for data processing scripts. All commands run from project root.

## Setup

```bash
cd /home/exedev/5mpglobe
source .venv/bin/activate
```

---

## 1. Update Production from GitHub

Pull latest code and data:

```bash
git pull --rebase
make build
sudo systemctl restart srv  # or: pkill -f "./server" && ./server &
```

---

## 2. Fire Data Processing

### 2.1 Rebuild Fire Analysis (from fire_detections table)

Run when: New fire detections added, or annually to refresh all years.

```bash
python scripts/rebuild_park_fire_analysis.py > /tmp/fire_analysis.log 2>&1 &
tail -f /tmp/fire_analysis.log
```

Output: Updates `park_fire_analysis` table (158 parks × 9 years)

### 2.2 Export Fire Trajectories with Climate/River Context

Run when: After fire analysis rebuild, or when climate/river data updated.

```bash
rm -rf data/fire_trajectories/*
PYTHONUNBUFFERED=1 python scripts/analyze_fire_trajectories_v2.py > /tmp/fire_traj.log 2>&1 &
tail -f /tmp/fire_traj.log
```

Output: `data/fire_trajectories/*.json` (132 parks, 50k+ trajectories)

### 2.3 Export Fire Analysis to JSON

Run when: After fire analysis rebuild.

```bash
python3 << 'PY'
import json, sqlite3, struct
from pathlib import Path
conn = sqlite3.connect('db.sqlite3')
conn.row_factory = sqlite3.Row
OUTPUT = Path('data/fire_analysis')
OUTPUT.mkdir(exist_ok=True)
by_park = {}
for row in conn.execute('SELECT * FROM park_fire_analysis ORDER BY park_id, year'):
    pk = row['park_id']
    if pk not in by_park: by_park[pk] = []
    by_park[pk].append({
        'year': row['year'], 'total_fires': row['total_fires'],
        'transhumance_groups': row['transhumance_groups'],
        'herder_groups': row['herder_groups'], 'management_groups': row['management_groups'],
        'analysis': json.loads(row['analysis_json']) if row['analysis_json'] else None
    })
for pk, yrs in by_park.items():
    with open(OUTPUT / f'{pk}.json', 'w') as f: json.dump({'park_id': pk, 'years': yrs}, f)
print(f"Exported {len(by_park)} parks")
PY
```

---

## 3. River Data

### 3.1 Import HydroRIVERS (one-time or when new shapefile)

```bash
python scripts/import_hydrorivers.py > /tmp/rivers.log 2>&1 &
tail -f /tmp/rivers.log
```

Output: `rivers` table, `park_rivers` table, `data/rivers/*.json`

---

## 4. Settlement & Deforestation Classification

### 4.1 Run Classification (annually)

Classification runs automatically on server start. To force:

```bash
# In Go code: server.PrecomputeClassifications() 
# Or restart server - it runs on startup if data is stale (>1 year)
```

### 4.2 Export Classified Data to JSON

```bash
python scripts/load_json_data.py
```

Output: `data/export/classified_settlements.json`, `data/export/classified_deforestation.json`

---

## 5. Precompute All Narratives

Run when: After any of the above, before production deployment.

```bash
PYTHONUNBUFFERED=1 python scripts/precompute_narratives.py > /tmp/narratives.log 2>&1 &
tail -f /tmp/narratives.log
```

Output: 
- `data/export/fire_narratives.json` (53MB)
- `data/export/settlement_narratives.json` (2.7MB)
- `data/export/deforestation_narratives.json` (612KB)

---

## 6. Species Data

### 6.1 Load Species from JSON to DB

```bash
python scripts/load_json_data.py
```

Source: `data/species/park_mammals.json`
Output: `park_species` table

---

## 7. OSM Places

### 7.1 Download OSM Places for Missing Parks

```bash
python scripts/download_osm_places_to_file.py --list-missing  # Check which parks need data
python scripts/download_osm_places_to_file.py                  # Download all missing
python scripts/download_osm_places_to_file.py --park CAF_Chinko  # Single park
```

Output: `data/osm_places/*.json`

---

## 8. Full Production Update Sequence

When deploying all changes to a fresh production instance:

```bash
# 1. Pull code and data
git pull --rebase

# 2. Load JSON data to database
source .venv/bin/activate
python scripts/load_json_data.py

# 3. Build and restart
make build
./server &

# Server will auto-run classification if needed
```

---

## 9. Adding a New Park

1. Add park to `data/keystones_with_boundaries.json` with geometry
2. Run fire analysis: `python scripts/rebuild_park_fire_analysis.py`
3. Run trajectory export: `python scripts/analyze_fire_trajectories_v2.py`
4. Download OSM places: `python scripts/download_osm_places_to_file.py --park NEW_PARK_ID`
5. Run narratives: `python scripts/precompute_narratives.py`
6. Commit and push all JSON files

---

## Data Files Summary

| Path | Description | Update Frequency |
|------|-------------|------------------|
| `data/fire_analysis/*.json` | Yearly fire stats | After fire rebuild |
| `data/fire_trajectories/*.json` | Trajectories with timestamps | After fire rebuild |
| `data/rivers/*.json` | HydroRIVERS per park | One-time |
| `data/osm_places/*.json` | OSM place names | One-time per park |
| `data/climate/park_climate.json` | Climate zones, seasons | One-time |
| `data/species/park_mammals.json` | IUCN species | Annual |
| `data/export/*.json` | Precomputed narratives | Before deployment |

---

## Database Tables Summary

| Table | Records | Source |
|-------|---------|--------|
| `fire_detections` | 5.6M | FIRMS NRT downloads |
| `park_fire_analysis` | 1,197 | `rebuild_park_fire_analysis.py` |
| `rivers` | 183K | `import_hydrorivers.py` |
| `park_rivers` | 215K | `import_hydrorivers.py` |
| `park_settlements` | 15K | GHSL import |
| `deforestation_events` | 3.2K | Hansen import |
| `park_species` | 39K | `load_json_data.py` |
| `osm_places` | 10K+ | `download_osm_places.py` |
