# Deploy to Production

## Quick Update (code only)
```bash
git pull --rebase
make build
sudo systemctl restart srv
```

## Full Data Sync (with new JSON files)
```bash
git pull --rebase

# Import all JSON data to database
source .venv/bin/activate

# 1. Polygon geometries (deforestation/settlement)
python scripts/load_polygon_geometries.py

# 2. Rebuild events with classifications
python scripts/rebuild_events_from_polygons.py

# 3. Fire detections (if new fire JSON files)
python << 'PY'
import json, sqlite3
from pathlib import Path
conn = sqlite3.connect('db.sqlite3')
for f in Path('data/fire_detections_2025_2026').glob('*.json'):
    with open(f) as fp: fires = json.load(fp)
    for fire in fires:
        try:
            conn.execute("INSERT INTO fire_detections (latitude, longitude, brightness, scan, track, acq_date, acq_time, satellite, confidence, frp, daynight) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fire.get('lat'), fire.get('lng'), fire.get('brightness'), fire.get('scan'), fire.get('track'), fire.get('date'), fire.get('time',''), fire.get('satellite',''), fire.get('confidence',''), fire.get('frp',0), fire.get('daynight','')))
        except: pass
conn.commit()
PY

# 4. OSM places
python << 'PY'
import json, sqlite3
from pathlib import Path
conn = sqlite3.connect('db.sqlite3')
for f in Path('data/osm_places').glob('*.json'):
    if f.suffix == '.error': continue
    park_id = f.stem
    with open(f) as fp: data = json.load(fp)
    places = data.get('places', []) if isinstance(data, dict) else data
    conn.execute("DELETE FROM osm_places WHERE park_id = ?", (park_id,))
    for p in places:
        if isinstance(p, dict):
            conn.execute("INSERT OR IGNORE INTO osm_places (park_id, place_type, name, lat, lon) VALUES (?, ?, ?, ?, ?)",
                (park_id, p.get('type', p.get('place_type', '')), p.get('name', ''), p.get('lat', 0), p.get('lon', 0)))
conn.commit()
PY

# 5. HeiGIT roads
python << 'PY'
import json, sqlite3
from pathlib import Path
conn = sqlite3.connect('db.sqlite3')
conn.execute("DELETE FROM feature_geometries WHERE feature_type = 'road_heigit'")
for f in Path('data/roads_heigit').glob('*.json'):
    park_id = f.stem
    with open(f) as fp: roads = json.load(fp)
    for i, road in enumerate(roads):
        props = {k: v for k, v in road.items() if k != 'geometry'}
        conn.execute("INSERT INTO feature_geometries (park_id, feature_id, feature_type, geojson, properties_json) VALUES (?, ?, 'road_heigit', ?, ?)",
            (park_id, f"road_heigit_{park_id}_{i}", json.dumps(road.get('geometry', {})), json.dumps(props)))
conn.commit()
PY

# 6. Species and other data
python scripts/load_json_data.py

# Build and restart
make build
sudo systemctl restart srv
```

## Complete Rebuild (after major data changes)
```bash
git pull --rebase
source .venv/bin/activate

# All imports above, plus:

# 7. Rebuild fire analysis (slow - hours)
python scripts/rebuild_park_fire_analysis.py

# 8. Generate fire trajectories with context (slow)
python scripts/analyze_fire_trajectories_v2.py

# 9. Precompute all narratives
python scripts/precompute_narratives.py

make build
sudo systemctl restart srv
```

## Check Status
```bash
# Server
systemctl status srv
journalctl -u srv -f

# Database counts
sqlite3 db.sqlite3 "
SELECT 'fire_detections', COUNT(*) FROM fire_detections
UNION ALL SELECT 'osm_places', COUNT(*) FROM osm_places  
UNION ALL SELECT 'feature_geometries', COUNT(*) FROM feature_geometries
UNION ALL SELECT 'deforestation_events', COUNT(*) FROM deforestation_events
UNION ALL SELECT 'park_settlements', COUNT(*) FROM park_settlements;"
```

## Live URLs
- **App:** https://five-mp-conservation-effort.exe.xyz:8000/?pwd=test2026
- **API:** https://five-mp-conservation-effort.exe.xyz:8000/api/parks/CAF_Chinko/stats?pwd=test2026
