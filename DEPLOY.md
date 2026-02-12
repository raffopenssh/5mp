# Deploy to Production

## Quick Update (code only)
```bash
git pull --rebase
make build
sudo systemctl restart srv
```

## Full Data Sync
```bash
git pull --rebase
source .venv/bin/activate
python scripts/load_json_data.py
make build
sudo systemctl restart srv
```

## Complete Rebuild (all data)
```bash
source .venv/bin/activate

# Step 1: Fire analysis from raw detections
python scripts/rebuild_park_fire_analysis.py

# Step 2: Enhanced trajectories with context
python scripts/analyze_fire_trajectories_v3.py

# Step 3: Generate narratives
python scripts/precompute_narratives_v3.py

# Step 4: Rebuild and restart
make build
sudo systemctl restart srv
```

## Data Files on GitHub

| Directory | Count | Description |
|-----------|-------|-------------|
| `data/fire_analysis/` | 157 | Fire groups with trajectory timestamps |
| `data/fire_trajectories/` | 153 | Enhanced trajectories (rivers, roads, places) |
| `data/deforestation_events/` | 79 | Classified deforestation |
| `data/settlement_events/` | 156 | Classified settlements |
| `data/roads_heigit/` | 159 | HeiGIT road data |
| `data/rivers/` | 161 | HydroRIVERS |
| `data/osm_places/` | 106 | OSM places |
| `data/export/` | 7 | Precomputed narratives |

## Key Counts

- Fire detections: 6M+
- Fire trajectory groups: 130,708
- Settlements: 11,559
- Deforestation events: 1,401
- Rivers: 183,381
- OSM places: 105,334
- Species: 39,489
