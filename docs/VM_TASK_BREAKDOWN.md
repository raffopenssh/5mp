# VM Task Breakdown - Enhanced Narratives

**Last Updated:** February 2, 2026

---

## VM Overview

| VM | URL | Role | DB Status |
|----|-----|------|----------|
| **fivemp-testing** | https://fivemp-testing.exe.xyz:8000 | Development & API | Primary dev |
| **five-mp-conservation-effort** | https://five-mp-conservation-effort.exe.xyz:8000 | Heavy Processing | Sync from testing |
| **five-megapixel-conservation** | https://five-megapixel-conservation.exe.xyz:8000 | Production | DO NOT MODIFY |

---

## fivemp-testing (This VM)

### Immediate Tasks

```bash
# 1. Create database migration
cat > db/migrations/003-enhanced-features.sql << 'EOF'
-- Feature geometries table for map display
CREATE TABLE IF NOT EXISTS feature_geometries (
    id INTEGER PRIMARY KEY,
    feature_type TEXT NOT NULL,
    feature_id TEXT NOT NULL,
    park_id TEXT NOT NULL,
    geojson TEXT NOT NULL,
    bbox_minx REAL,
    bbox_miny REAL,
    bbox_maxx REAL,
    bbox_maxy REAL,
    start_date TEXT,
    end_date TEXT,
    properties_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(feature_type, feature_id)
);

CREATE INDEX IF NOT EXISTS idx_fg_park_type ON feature_geometries(park_id, feature_type);
CREATE INDEX IF NOT EXISTS idx_fg_dates ON feature_geometries(start_date, end_date);

-- Settlement enhancements
ALTER TABLE park_settlements ADD COLUMN classification TEXT DEFAULT 'unclassified';
ALTER TABLE park_settlements ADD COLUMN confidence REAL DEFAULT 0.0;
ALTER TABLE park_settlements ADD COLUMN distance_to_road_m REAL;
ALTER TABLE park_settlements ADD COLUMN distance_to_river_m REAL;
ALTER TABLE park_settlements ADD COLUMN footprint_geojson TEXT;
ALTER TABLE park_settlements ADD COLUMN population_2020 INTEGER;
ALTER TABLE park_settlements ADD COLUMN population_2030 INTEGER;

-- Deforestation cluster enhancements  
ALTER TABLE deforestation_clusters ADD COLUMN classification TEXT DEFAULT 'unclassified';
ALTER TABLE deforestation_clusters ADD COLUMN confidence REAL DEFAULT 0.0;
ALTER TABLE deforestation_clusters ADD COLUMN distance_to_road_m REAL;
ALTER TABLE deforestation_clusters ADD COLUMN distance_to_settlement_m REAL;
ALTER TABLE deforestation_clusters ADD COLUMN polygon_geojson TEXT;
ALTER TABLE deforestation_clusters ADD COLUMN start_date TEXT;
ALTER TABLE deforestation_clusters ADD COLUMN end_date TEXT;

-- Fire infraction enhancements
ALTER TABLE park_group_infractions ADD COLUMN bbox_geojson TEXT;
ALTER TABLE park_group_infractions ADD COLUMN start_date TEXT;
ALTER TABLE park_group_infractions ADD COLUMN end_date TEXT;
EOF

# 2. Apply migration (run manually after review)
sqlite3 db.sqlite3 < db/migrations/003-enhanced-features.sql
```

### API Development Tasks

1. **New endpoint:** `GET /api/park/{id}/features`
   - Returns GeoJSON FeatureCollection
   - Filters: `?type=`, `?start=`, `?end=`
   
2. **Modify:** `GET /api/park/{id}/fire-narrative`
   - Add `?start=` and `?end=` date filters
   - Return stats-only format
   - Include `geojson_id` references

3. **Modify:** `GET /api/park/{id}/settlement-narrative`
   - Return stats-only format
   - Include classification breakdown
   - Include `geojson_id` references

4. **Modify:** `GET /api/park/{id}/deforestation-narrative`
   - Add date range filters
   - Return stats-only format
   - Include `geojson_id` references

### Frontend Tasks

1. **Time slider:** Convert year slider to date slider
2. **Feature layers:** Add toggle for fire/deforestation/settlement/road layers
3. **Click handler:** Narrative item click → highlight on map
4. **Popup:** Show feature properties on click

---

## five-mp-conservation-effort (Processing VM)

### Setup Tasks

```bash
# 1. SSH to VM
ssh five-mp-conservation-effort.exe.xyz

# 2. Ensure 5mp repo is current
cd ~/5mp
git pull origin main

# 3. Install Python dependencies
pip install rasterio shapely scipy numpy pyproj

# 4. Download GHSL POP data from Google Drive
# Link: https://drive.google.com/file/d/1VuFtRpDxOV0aYINzEUiswp4g03rk35Sw/view
# Use gdown or manual download
pip install gdown
gdown 1VuFtRpDxOV0aYINzEUiswp4g03rk35Sw -O data/ghsl_pop_2030.zip

# 5. Verify download
ls -lh data/ghsl_pop_2030.zip
unzip -l data/ghsl_pop_2030.zip | head -20
```

### Processing Tasks

```bash
# Run in order:

# 1. Extract fire trajectory GeoJSON (fast, uses existing data)
python scripts/extract_geometries.py --type fire_trajectory --all
# ✅ DONE: 50,899 fire trajectories

# 2. Extract road GeoJSON
python scripts/extract_geometries.py --type road --all
# ✅ DONE: 10,854 road segments

# 3. Extract settlement GeoJSON
python scripts/extract_geometries.py --type settlement --all
# ✅ DONE: 15,066 settlements

# 4. Extract deforestation GeoJSON
python scripts/extract_geometries.py --type deforestation --all
# ✅ DONE: 3,218 deforestation events

# 5. Classify settlements (needs roads + rivers)
python scripts/classify_features.py --type settlement --all
# ✅ DONE: hamlet(4098), village(1539), sawmill_compound(100), etc.

# 6. Classify deforestation (needs roads + settlements)
python scripts/classify_features.py --type deforestation --all  
# ✅ DONE: natural_disturbance(2690), selective_logging(644), etc.

# 7. Process GHSL population (slow, memory-intensive)
# ⏳ PENDING: Downloading GHSL POP data (5.4GB)
python scripts/ghsl_pop_processor.py --zip data/ghsl_pop_2030_full.zip --all
```

### Data Sync to Testing VM

```bash
# After processing complete:

# 1. Export updated tables
sqlite3 db.sqlite3 ".dump feature_geometries" > exports/feature_geometries.sql
sqlite3 db.sqlite3 ".dump park_settlements" > exports/park_settlements.sql
sqlite3 db.sqlite3 ".dump deforestation_clusters" > exports/deforestation_clusters.sql

# 2. Copy to testing VM
scp exports/*.sql fivemp-testing.exe.xyz:~/5mp/imports/

# 3. On testing VM, import:
sqlite3 db.sqlite3 < imports/feature_geometries.sql
sqlite3 db.sqlite3 < imports/park_settlements.sql
sqlite3 db.sqlite3 < imports/deforestation_clusters.sql
```

---

## five-megapixel-conservation (Production)

### DO NOT MODIFY UNTIL:

- [ ] All processing complete on effort VM
- [ ] All API changes tested on testing VM
- [ ] Frontend changes validated on testing VM
- [ ] Full regression testing passed

### Deployment Checklist (Week 4)

```bash
# Only after all validation:

# 1. Backup production DB
cp db.sqlite3 db.sqlite3.backup.$(date +%Y%m%d)

# 2. Pull latest code
git pull origin main

# 3. Apply migration
sqlite3 db.sqlite3 < db/migrations/003-enhanced-features.sql

# 4. Import processed data
sqlite3 db.sqlite3 < imports/feature_geometries.sql
# ... etc

# 5. Rebuild and restart
make build
sudo systemctl restart srv

# 6. Verify
curl -s "https://five-megapixel-conservation.exe.xyz:8000/api/park/CAF_Chinko/features?pwd=test2026" | jq '.features | length'
```

---

## Scripts to Create

### 1. scripts/extract_geometries.py

Extracts GeoJSON from existing data:
- Fire trajectories from `park_group_infractions.trajectories_json`
- Roads from `osm_roadless_data.roads_json`
- Settlement centroids (for now, polygons later with GHSL)

### 2. scripts/ghsl_pop_processor.py

Windowed GHSL POP processing:
- Read directly from ZIP
- Memory-efficient per-park processing
- Store population_2020, population_2030 in park_settlements

### 3. scripts/classify_features.py

Spatial cross-reference classification:
- Calculate distances to roads, rivers, settlements
- Apply classification rules (see proposal)
- Store classification + confidence

---

## Timeline

| Week | fivemp-testing | five-mp-conservation-effort |
|------|----------------|-----------------------------|
| 1 | Schema migration, start API | Download data, setup env |
| 2 | API endpoints, stats-only format | Run all processing scripts |
| 3 | Frontend time slider, feature display | Sync data to testing |
| 4 | Integration testing, deploy to prod | Support/fixes |

---

## Quick Commands

### Check processing progress (effort VM)
```bash
sqlite3 db.sqlite3 "SELECT classification, COUNT(*) FROM park_settlements GROUP BY classification;"
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM feature_geometries GROUP BY feature_type;"
```

### Test API (testing VM)
```bash
curl -s "http://localhost:8000/api/park/CAF_Chinko/features?pwd=test2026&type=fire_trajectory" | jq '.features | length'
```

### Check feature_geometries count
```bash
sqlite3 db.sqlite3 "SELECT feature_type, COUNT(*) FROM feature_geometries GROUP BY feature_type;"
