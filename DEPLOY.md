# Deploy to Production

## Quick Update (code only)
```bash
git pull --rebase
make build
sudo systemctl restart srv
```

## Full Data Sync (after major data changes)
```bash
git pull --rebase
source .venv/bin/activate

# Load JSON data to DB
python scripts/load_json_data.py

# Build and restart
make build
sudo systemctl restart srv
```

## Rebuild All Data (rare - takes hours)
```bash
source .venv/bin/activate

# 1. Fire analysis (~30 min)
python scripts/rebuild_park_fire_analysis.py

# 2. Trajectories (~10 min)  
python scripts/analyze_fire_trajectories_v2.py

# 3. Narratives (~1 min)
python scripts/precompute_narratives.py

# 4. Commit new JSON files
git add data/fire_analysis data/fire_trajectories data/export
git commit -m "Rebuild fire data"
git push
```

See `docs/SCRIPTS.md` for detailed documentation.
