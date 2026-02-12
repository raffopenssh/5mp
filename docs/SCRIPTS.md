# Script Reference - 5MP Conservation Monitoring

Quick reference for data processing scripts. All commands run from project root.

## Setup

```bash
cd /home/exedev/5mpglobe
source .venv/bin/activate
```

---

## Data Import Scripts

### 1. Load Polygon Geometries (deforestation/settlement)
```bash
python scripts/load_polygon_geometries.py
```
- Loads `data/feature_geometries/deforestation/*.json` and `data/feature_geometries/settlement/*.json`
- Populates `feature_geometries` table
- **Run when:** New polygon data available

### 2. Rebuild Events from Polygons
```bash
python scripts/rebuild_events_from_polygons.py
```
- Rebuilds `deforestation_events` and `park_settlements` from polygon data
- Generates classifications and narratives
- **Run after:** `load_polygon_geometries.py`
- **Output:** `data/deforestation_events/*.json`, `data/settlement_events/*.json`

### 3. Import HydroRIVERS
```bash
python scripts/import_hydrorivers.py
```
- Imports river data from `data/hydrorivers/HydroRIVERS_v10_af_shp/`
- Populates `rivers` and `park_rivers` tables
- **Output:** `data/rivers/*.json`

### 4. Load JSON Data (species, rivers, settlements)
```bash
python scripts/load_json_data.py
```
- Loads IUCN species from `data/species/park_mammals.json`
- Verifies rivers and settlements
- **Run when:** Updating species data

### 5. Import Fire Detections
```bash
# For 2025-2026 data from JSON files:
python << 'PY'
import json, sqlite3
from pathlib import Path

conn = sqlite3.connect('db.sqlite3')
fire_dir = Path('data/fire_detections_2025_2026')

for json_file in sorted(fire_dir.glob('*.json')):
    with open(json_file) as f:
        fires = json.load(f)
    for fire in fires:
        conn.execute("""
            INSERT INTO fire_detections 
            (latitude, longitude, brightness, scan, track, 
             acq_date, acq_time, satellite, confidence, frp, daynight)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fire.get('lat'), fire.get('lng'), fire.get('brightness'),
              fire.get('scan'), fire.get('track'), fire.get('date'),
              fire.get('time', ''), fire.get('satellite', ''),
              fire.get('confidence', ''), fire.get('frp', 0), fire.get('daynight', '')))
    conn.commit()
conn.close()
PY
```

### 6. Import OSM Places
```bash
python << 'PY'
import json, sqlite3
from pathlib import Path

conn = sqlite3.connect('db.sqlite3')
osm_dir = Path('data/osm_places')

for json_file in sorted(osm_dir.glob('*.json')):
    if json_file.suffix == '.error': continue
    park_id = json_file.stem
    with open(json_file) as f:
        data = json.load(f)
    places = data.get('places', []) if isinstance(data, dict) else data
    conn.execute("DELETE FROM osm_places WHERE park_id = ?", (park_id,))
    for p in places:
        if isinstance(p, dict):
            conn.execute("INSERT OR IGNORE INTO osm_places (park_id, place_type, name, lat, lon) VALUES (?, ?, ?, ?, ?)",
                (park_id, p.get('type', p.get('place_type', '')), p.get('name', ''), p.get('lat', 0), p.get('lon', 0)))
conn.commit()
conn.close()
PY
```

### 7. Import HeiGIT Roads
```bash
python << 'PY'
import json, sqlite3
from pathlib import Path

conn = sqlite3.connect('db.sqlite3')
roads_dir = Path('data/roads_heigit')
conn.execute("DELETE FROM feature_geometries WHERE feature_type = 'road_heigit'")

for json_file in sorted(roads_dir.glob('*.json')):
    park_id = json_file.stem
    with open(json_file) as f:
        roads = json.load(f)
    for i, road in enumerate(roads):
        props = {k: v for k, v in road.items() if k != 'geometry'}
        conn.execute("""
            INSERT INTO feature_geometries (park_id, feature_id, feature_type, geojson, properties_json)
            VALUES (?, ?, 'road_heigit', ?, ?)
        """, (park_id, f"road_heigit_{park_id}_{i}", json.dumps(road.get('geometry', {})), json.dumps(props)))
conn.commit()
conn.close()
PY
```

---

## Analysis Scripts

### 8. Rebuild Park Fire Analysis
```bash
python scripts/rebuild_park_fire_analysis.py
```
- Analyzes fire trajectories for all parks (2018-2026)
- Classifies fire groups (transhumance, herder, management, etc.)
- **Run when:** Major fire data updates

### 9. Fire Trajectory Analysis v2
```bash
python scripts/analyze_fire_trajectories_v2.py
```
- Enhanced trajectories with climate, rivers, roads context
- Generates timestamped coordinates
- **Output:** `data/fire_trajectories/*.json`
- **Run after:** `rebuild_park_fire_analysis.py`

### 10. Precompute Narratives
```bash
python scripts/precompute_narratives.py
```
- Generates all narrative JSON files
- **Output:** `data/export/fire_narratives.json`, `deforestation_narratives.json`, `settlement_narratives.json`

---

## Download Scripts

### 11. Download OSM Places (for missing parks)
```bash
python scripts/download_osm_places_to_file.py --list-missing  # Check first
python scripts/download_osm_places_to_file.py                 # Run all
python scripts/download_osm_places_to_file.py --park TZA_Serengeti  # Single park
```

### 12. Download HeiGIT Roads
```bash
python scripts/download_heigit_roads.py          # All countries
python scripts/download_heigit_roads.py NG KE TZ # Specific countries
```

---

## Full Production Update Sequence

```bash
cd /home/exedev/5mpglobe
source .venv/bin/activate

# 1. Pull latest code and data
git pull --rebase

# 2. Import polygon data
python scripts/load_polygon_geometries.py

# 3. Rebuild events from polygons
python scripts/rebuild_events_from_polygons.py

# 4. Import other JSON data (OSM, roads, fires, species)
python scripts/load_json_data.py
# Plus inline imports above for fires, OSM, roads

# 5. Rebuild fire analysis
python scripts/rebuild_park_fire_analysis.py

# 6. Generate enhanced fire trajectories
python scripts/analyze_fire_trajectories_v2.py

# 7. Precompute all narratives
python scripts/precompute_narratives.py

# 8. Rebuild and restart server
make build
sudo systemctl restart srv
```

---

## Automated Jobs (in server)

The server runs these automatically via `StartNarrativeCacheWorker`:

| Schedule | Task | Description |
|----------|------|-------------|
| Daily 3am UTC | `PrecomputeRecentFireNarratives` | Updates parks with fires in last 14 days |
| Weekly Sun 2am | `PrecomputeFireNarratives` | Full fire narrative refresh |
| Jan 1st 4am | `PrecomputeAllClassifications` | Settlement/deforestation classifications |

---

## Data Files Summary

| Directory | Contents | Update Frequency |
|-----------|----------|------------------|
| `data/fire_trajectories/` | Fire group trajectories with timestamps | After fire analysis |
| `data/fire_analysis/` | Yearly park fire analysis | After fire analysis |
| `data/deforestation_events/` | Classified deforestation by park | After polygon rebuild |
| `data/settlement_events/` | Classified settlements by park | After polygon rebuild |
| `data/export/` | Precomputed narratives | After precompute |
| `data/rivers/` | HydroRIVERS per park | One-time |
| `data/osm_places/` | OSM places per park | After OSM download |
| `data/roads_heigit/` | HeiGIT road data per park | After road download |
| `data/climate/` | WorldClim climate data | Static |
| `data/species/` | IUCN species data | Yearly update |
