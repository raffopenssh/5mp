# Enhanced Narratives & Pattern Detection Proposal

**Date:** February 2, 2026  
**Status:** Draft  
**VMs:** fivemp-testing (dev), five-mp-conservation-effort (processing), five-megapixel-conservation (production)

---

## Executive Summary

Upgrade the 5MP narrative system to:
1. **Detect meaningful patterns** (road clearing, management burns, artisanal mining, hamlets)
2. **Provide GeoJSON for all features** (clickable map integration)
3. **Support date-based time slider** (not just years)
4. **Include population estimates** per settlement footprint
5. **Deliver stats-only summaries** (no judgments/ratings)

---

## 1. Current State

### Data Available

| Table | Records | Date Range | Has GeoJSON | Notes |
|-------|---------|------------|-------------|---------|
| fire_detections | 4.6M | 2018-04-01 to 2024-12-31 | No (lat/lon only) | Individual VIIRS/MODIS detections |
| park_group_infractions | 801 | 2018-2024 | Yes (trajectories_json) | Fire group trajectories per park/year |
| deforestation_events | 3,218 | 2001-2024 | Yes (Point only) | Yearly aggregates, no polygons |
| deforestation_clusters | 5,616 | 2001-2024 | No | Individual clusters within events |
| park_settlements | 15,066 | N/A | No | GHSL built-up centroids |
| osm_places | 10,600 | N/A | Point only | Villages, rivers, hamlets |
| osm_roadless_data | 162 | N/A | Yes (roads_json) | Road line coordinates per park |

### Current Limitations

1. **No spatial cross-referencing**: Settlements near roads not identified
2. **No population data**: GHSL BUILT_S only, no POP layer
3. **Year-only granularity**: Deforestation has no month/day precision
4. **Basic pattern detection**: Only scattered/clustered/linear/strip
5. **No clickable GeoJSON**: Narratives don't link to map features

---

## 2. Pattern Detection Classifications

### 2.1 Settlement Classifications

| Pattern | Detection Criteria | Confidence |
|---------|-------------------|------------|
| **hamlet** | 5-50 buildings, >2km from roads, near village/hamlet in OSM | High |
| **roadside_settlement** | <500m from road, linear arrangement | High |
| **park_infrastructure** | Inside park, <1km from HQ/gate in OSM, small footprint | Medium |
| **agricultural_compound** | 1-5 buildings, >5km from settlements, rectangular pattern | Medium |
| **artisanal_mining_camp** | Near river/stream, <2km from deforestation cluster, temporary pattern | Low |
| **fishing_camp** | <500m from river/lake, seasonal occupation pattern | Low |
| **unclassified** | Default | - |

### 2.2 Deforestation Classifications

| Pattern | Detection Criteria | Confidence |
|---------|-------------------|------------|
| **road_clearing** | Linear, aspect ratio >5:1, parallel to road within 200m | High |
| **agricultural_expansion** | Near settlement (<5km), incremental year-over-year growth | High |
| **logging_concession** | Large rectangular blocks, systematic pattern | Medium |
| **charcoal_production** | Circular/irregular, 2-10km from road, near settlements | Medium |
| **artisanal_mining** | Near river, irregular shape, associated with sediment | Low |
| **management_firebreak** | Linear, along park boundary, recurring pattern | Medium |
| **natural_disturbance** | No roads/settlements nearby, irregular shape | Low |

### 2.3 Fire Classifications

| Pattern | Detection Criteria | Confidence |
|---------|-------------------|------------|
| **management_burn** | Inside park, during dry season, low spread rate, near roads | Medium |
| **agricultural_fire** | Near settlements, pre-planting season, contained spread | High |
| **pastoral_burn** | Moving pattern, follows grazing routes, seasonal | Medium |
| **wildfire** | High spread rate, no containment pattern, crosses features | High |
| **charcoal_fire** | Stationary, near deforestation, small radius | Medium |

---

## 3. Database Schema Changes

### 3.1 New Table: `feature_geometries`

Centralized GeoJSON storage for map display:

```sql
CREATE TABLE feature_geometries (
    id INTEGER PRIMARY KEY,
    feature_type TEXT NOT NULL,  -- 'fire_trajectory', 'deforestation', 'settlement', 'road_segment'
    feature_id TEXT NOT NULL,    -- Reference to source table
    park_id TEXT NOT NULL,
    geojson TEXT NOT NULL,       -- Full GeoJSON geometry
    bbox_minx REAL,
    bbox_miny REAL,
    bbox_maxx REAL,
    bbox_maxy REAL,
    start_date TEXT,             -- ISO date for time slider
    end_date TEXT,
    properties_json TEXT,        -- Additional properties for popup
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(feature_type, feature_id)
);

CREATE INDEX idx_fg_park_type ON feature_geometries(park_id, feature_type);
CREATE INDEX idx_fg_dates ON feature_geometries(start_date, end_date);
CREATE INDEX idx_fg_bbox ON feature_geometries(bbox_minx, bbox_miny, bbox_maxx, bbox_maxy);
```

### 3.2 Alter `park_settlements`

```sql
ALTER TABLE park_settlements ADD COLUMN classification TEXT DEFAULT 'unclassified';
ALTER TABLE park_settlements ADD COLUMN confidence REAL DEFAULT 0.0;
ALTER TABLE park_settlements ADD COLUMN distance_to_road_m REAL;
ALTER TABLE park_settlements ADD COLUMN nearest_road_type TEXT;
ALTER TABLE park_settlements ADD COLUMN distance_to_river_m REAL;
ALTER TABLE park_settlements ADD COLUMN nearby_deforestation_km2 REAL;
ALTER TABLE park_settlements ADD COLUMN footprint_geojson TEXT;  -- Polygon from GHSL
ALTER TABLE park_settlements ADD COLUMN population_2020 INTEGER;
ALTER TABLE park_settlements ADD COLUMN population_2030 INTEGER;
```

### 3.3 Alter `deforestation_clusters`

```sql
ALTER TABLE deforestation_clusters ADD COLUMN classification TEXT DEFAULT 'unclassified';
ALTER TABLE deforestation_clusters ADD COLUMN confidence REAL DEFAULT 0.0;
ALTER TABLE deforestation_clusters ADD COLUMN distance_to_road_m REAL;
ALTER TABLE deforestation_clusters ADD COLUMN distance_to_settlement_m REAL;
ALTER TABLE deforestation_clusters ADD COLUMN distance_to_river_m REAL;
ALTER TABLE deforestation_clusters ADD COLUMN polygon_geojson TEXT;  -- Actual deforestation polygon
ALTER TABLE deforestation_clusters ADD COLUMN start_date TEXT;  -- Estimated start (year-01-01 or finer)
ALTER TABLE deforestation_clusters ADD COLUMN end_date TEXT;
```

### 3.4 Alter `park_group_infractions`

```sql
-- Already has trajectories_json, add:
ALTER TABLE park_group_infractions ADD COLUMN bbox_geojson TEXT;  -- Bounding box for all trajectories
ALTER TABLE park_group_infractions ADD COLUMN start_date TEXT;    -- First fire date
ALTER TABLE park_group_infractions ADD COLUMN end_date TEXT;      -- Last fire date
```

---

## 4. API Changes

### 4.1 New Endpoint: `/api/park/{id}/features`

Returns all GeoJSON features for a park, filterable by type and date:

```
GET /api/park/CAF_Chinko/features?type=fire_trajectory&start=2024-01-01&end=2024-12-31
```

Response:
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {"type": "LineString", "coordinates": [...]},
      "properties": {
        "feature_type": "fire_trajectory",
        "feature_id": "CAF_Chinko_2024_grp_17",
        "start_date": "2024-01-15",
        "end_date": "2024-01-23",
        "classification": "pastoral_burn",
        "fires_inside": 45,
        "days_inside": 8,
        "outcome": "TRANSITED"
      }
    }
  ]
}
```

### 4.2 Modified Narrative Endpoints

**Before (verbose):**
```json
{
  "summary": "Chinko shows concerning settlement pressure with 127 detected built-up areas...",
  "conflict_risk": "HIGH"
}
```

**After (stats-only):**
```json
{
  "stats": {
    "settlement_count": 127,
    "total_area_km2": 2.4,
    "population_est": 15600,
    "by_classification": {
      "hamlet": 45,
      "roadside_settlement": 32,
      "agricultural_compound": 28,
      "unclassified": 22
    },
    "avg_distance_to_road_km": 4.2,
    "avg_distance_to_boundary_km": 12.5
  },
  "features": [
    {
      "id": "settlement_4521",
      "classification": "hamlet",
      "area_m2": 12500,
      "population_est": 340,
      "lat": 7.234,
      "lon": 23.456,
      "geojson_id": "fg_12345"  // Reference to feature_geometries
    }
  ]
}
```

### 4.3 Time Slider Support

All narrative endpoints accept date range:
```
GET /api/park/{id}/fire-narrative?start=2024-01-01&end=2024-06-30
GET /api/park/{id}/deforestation-narrative?start=2020-01-01&end=2024-12-31
GET /api/park/{id}/settlement-narrative  // No date filter (current state)
```

---

## 5. Processing Scripts

### 5.1 Memory-Efficient GHSL Population Processor

**File:** `scripts/ghsl_pop_processor.py`

```python
"""GHSL Population Integration - Windowed Processing

Reads GHSL POP tiles directly from ZIP using windowed reads.
No full extraction needed. Memory usage: <500MB per park.

Usage:
    python scripts/ghsl_pop_processor.py --park CAF_Chinko
    python scripts/ghsl_pop_processor.py --all --workers 4
"""

import rasterio
from rasterio.windows import from_bounds
import zipfile
import numpy as np

def process_park_population(park_id, park_geometry, pop_zip_path):
    """Extract population for park using windowed reads."""
    with zipfile.ZipFile(pop_zip_path) as zf:
        # Find relevant tiles based on park bounds
        tiles = get_tiles_for_bounds(park_geometry.bounds)
        
        total_pop = 0
        for tile_name in tiles:
            with zf.open(tile_name) as tile_file:
                with rasterio.open(tile_file) as src:
                    # Read only the window covering the park
                    window = from_bounds(*park_geometry.bounds, src.transform)
                    data = src.read(1, window=window)
                    
                    # Mask to park boundary
                    mask = rasterize_geometry(park_geometry, window, src.transform)
                    pop_in_park = np.sum(data[mask])
                    total_pop += pop_in_park
        
        return total_pop
```

### 5.2 Cross-Reference Classifier

**File:** `scripts/classify_features.py`

```python
"""Feature Classification using Spatial Cross-Reference

Classifies settlements and deforestation based on proximity to:
- Roads (osm_roadless_data.roads_json)
- Rivers (osm_places where place_type='river')
- Other settlements
- Deforestation clusters

Usage:
    python scripts/classify_features.py --park CAF_Chinko
    python scripts/classify_features.py --all
"""

def classify_settlement(settlement, roads, rivers, deforestation, other_settlements):
    """Determine settlement classification based on spatial context."""
    
    dist_to_road = min_distance_to_lines(settlement, roads)
    dist_to_river = min_distance_to_points(settlement, rivers)
    dist_to_deforestation = min_distance_to_polygons(settlement, deforestation)
    nearby_settlements = count_within_radius(settlement, other_settlements, 2000)
    
    # Classification logic
    if dist_to_road < 500 and is_linear_arrangement(settlement, other_settlements):
        return 'roadside_settlement', 0.9
    
    if nearby_settlements >= 5 and dist_to_road > 2000:
        return 'hamlet', 0.85
    
    if dist_to_river < 500 and dist_to_deforestation < 2000:
        return 'artisanal_mining_camp', 0.5
    
    if settlement.area_m2 < 5000 and dist_to_road > 5000:
        return 'agricultural_compound', 0.6
    
    return 'unclassified', 0.0
```

### 5.3 GeoJSON Extractor

**File:** `scripts/extract_geometries.py`

```python
"""Extract and Store GeoJSON for All Features

Populates feature_geometries table with:
- Fire trajectories as LineStrings
- Deforestation clusters as Polygons (from Hansen raster)
- Settlement footprints as Polygons (from GHSL)
- Road segments as LineStrings

Usage:
    python scripts/extract_geometries.py --park CAF_Chinko
    python scripts/extract_geometries.py --all --type fire_trajectory
"""

def extract_fire_trajectories(park_id, year):
    """Convert trajectories_json to individual GeoJSON features."""
    trajectories = load_trajectories(park_id, year)
    
    features = []
    for i, traj in enumerate(trajectories):
        # Create LineString from path
        coords = [[p['lon'], p['lat']] for p in traj['path']]
        geojson = {
            'type': 'LineString',
            'coordinates': coords
        }
        
        # Calculate bounding box
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        
        features.append({
            'feature_type': 'fire_trajectory',
            'feature_id': f"{park_id}_{year}_grp_{i+1}",
            'park_id': park_id,
            'geojson': json.dumps(geojson),
            'bbox_minx': min(lons),
            'bbox_miny': min(lats),
            'bbox_maxx': max(lons),
            'bbox_maxy': max(lats),
            'start_date': traj['entry_date'],
            'end_date': traj['last_inside'],
            'properties_json': json.dumps({
                'outcome': traj['outcome'],
                'fires_inside': traj['fires_inside'],
                'days_inside': traj['days_inside']
            })
        })
    
    return features
```

---

## 6. Frontend Changes

### 6.1 Time Slider Enhancement

```javascript
// Current: Year-only slider (2018-2024)
// New: Date-based slider with play/pause

const timeSlider = {
    start: '2018-04-01',
    end: '2024-12-31',
    current: '2024-01-01',
    granularity: 'month',  // 'day', 'month', 'year'
    playing: false,
    speed: 1000  // ms per step
};
```

### 6.2 Feature Click Handler

```javascript
// When user clicks narrative item, highlight on map
function showFeatureOnMap(featureId) {
    fetch(`/api/feature/${featureId}`)
        .then(r => r.json())
        .then(feature => {
            // Add to map as highlight layer
            map.getSource('highlight').setData(feature.geojson);
            
            // Fly to feature bounds
            map.fitBounds(feature.bbox, {padding: 50});
            
            // Show popup with properties
            showFeaturePopup(feature);
        });
}
```

### 6.3 Layer Toggle

```javascript
// Add toggles for feature layers
const featureLayers = [
    {id: 'fire-trajectories', label: 'Fire Trajectories', color: '#ff6b35'},
    {id: 'deforestation', label: 'Deforestation', color: '#8b0000'},
    {id: 'settlements', label: 'Settlements', color: '#4a90d9'},
    {id: 'roads', label: 'Roads', color: '#666666'}
];
```

---

## 7. Work Distribution

### VM: fivemp-testing.exe.xyz (Development)

**Role:** Development, testing, API changes

**Tasks:**
1. Database schema migrations
2. API endpoint development
3. Frontend time slider & feature display
4. Integration testing

**Timeline:** Weeks 1-3

### VM: five-mp-conservation-effort.exe.xyz (Processing)

**Role:** Heavy data processing

**Tasks:**
1. GHSL population extraction (memory-intensive)
2. Deforestation polygon extraction from Hansen tiles
3. Feature classification batch processing
4. GeoJSON generation for all features

**Timeline:** Weeks 1-2 (parallel with dev)

**Setup:**
```bash
# Copy processing scripts
scp scripts/ghsl_pop_processor.py five-mp-conservation-effort:~/5mp/scripts/
scp scripts/classify_features.py five-mp-conservation-effort:~/5mp/scripts/
scp scripts/extract_geometries.py five-mp-conservation-effort:~/5mp/scripts/

# Copy GHSL population data
# (Download from Google Drive link to this VM)

# Run processing
python scripts/ghsl_pop_processor.py --all --workers 4
python scripts/classify_features.py --all
python scripts/extract_geometries.py --all
```

### VM: five-megapixel-conservation.exe.xyz (Production)

**Role:** Stable production, no changes until validated

**Tasks:**
1. None until Phase 2 complete
2. Final deployment after testing on fivemp-testing

**Timeline:** Week 4 (deployment only)

---

## 8. Implementation Phases

### Phase 1: Schema & Processing (Week 1)

| Task | VM | Priority |
|------|-----|----------|
| Create migration for new tables/columns | testing | P0 |
| Implement ghsl_pop_processor.py | effort | P0 |
| Implement classify_features.py | effort | P1 |
| Implement extract_geometries.py | effort | P1 |
| Download GHSL POP data to effort VM | effort | P0 |

### Phase 2: API Changes (Week 2)

| Task | VM | Priority |
|------|-----|----------|
| New /api/park/{id}/features endpoint | testing | P0 |
| Modify narrative endpoints (stats-only) | testing | P1 |
| Add date range filtering | testing | P1 |
| Update narrative_handlers.go | testing | P0 |

### Phase 3: Frontend (Week 3)

| Task | VM | Priority |
|------|-----|----------|
| Date-based time slider | testing | P1 |
| Feature click → map highlight | testing | P0 |
| Layer toggles for feature types | testing | P2 |
| Narrative item → GeoJSON linking | testing | P0 |

### Phase 4: Deployment (Week 4)

| Task | VM | Priority |
|------|-----|----------|
| Sync processed data to testing | testing | P0 |
| Full integration testing | testing | P0 |
| Deploy to production | production | P0 |
| Documentation update | all | P1 |

---

## 9. Data Files

### Required Downloads

| File | Size | Source | Destination VM |
|------|------|--------|----------------|
| GHSL POP 2030 | ~2GB | [Google Drive](https://drive.google.com/file/d/1VuFtRpDxOV0aYINzEUiswp4g03rk35Sw/view) | five-mp-conservation-effort |
| Hansen lossyear tiles | ~20GB | Already partial | five-mp-conservation-effort |

### Database Sync Strategy

```bash
# After processing on effort VM:
# 1. Export new/updated tables
sqlite3 db.sqlite3 ".dump feature_geometries" > feature_geometries.sql
sqlite3 db.sqlite3 ".dump park_settlements" > park_settlements.sql

# 2. Transfer to testing VM
scp *.sql fivemp-testing:~/5mp/

# 3. Import on testing VM
sqlite3 db.sqlite3 < feature_geometries.sql
sqlite3 db.sqlite3 < park_settlements.sql
```

---

## 10. Success Metrics

| Metric | Target |
|--------|--------|
| Settlement classification coverage | >80% classified |
| Deforestation classification coverage | >70% classified |
| GeoJSON available for features | 100% |
| API response time (features endpoint) | <500ms |
| Memory usage during processing | <2GB per park |
| Time slider date precision | Day-level for fires, Year-level for deforestation |

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| GHSL POP download too large | Processing delayed | Use windowed reads, process in chunks |
| Classification accuracy low | Misleading labels | Add confidence scores, default to 'unclassified' |
| GeoJSON bloats database | Slow queries | Use spatial indexes, lazy loading |
| Breaking production | User impact | Strict staging on testing VM first |

---

## Appendix A: Classification Decision Tree

```
Settlement Classification:
├── Distance to road < 500m?
│   ├── Yes → Linear arrangement? → roadside_settlement (0.9)
│   └── No → Continue
├── Nearby settlements >= 5 within 2km?
│   ├── Yes → hamlet (0.85)
│   └── No → Continue  
├── Distance to river < 500m AND near deforestation?
│   ├── Yes → artisanal_mining_camp (0.5)
│   └── No → Continue
├── Area < 5000m² AND distance to road > 5km?
│   ├── Yes → agricultural_compound (0.6)
│   └── No → unclassified (0.0)
```

---

## Appendix B: Sample Queries

### Get all fire trajectories for a park in date range

```sql
SELECT fg.geojson, fg.properties_json, fg.start_date, fg.end_date
FROM feature_geometries fg
WHERE fg.park_id = 'CAF_Chinko'
  AND fg.feature_type = 'fire_trajectory'
  AND fg.start_date >= '2024-01-01'
  AND fg.end_date <= '2024-12-31'
ORDER BY fg.start_date;
```

### Get settlements with classification breakdown

```sql
SELECT classification, COUNT(*) as count, 
       SUM(population_2030) as total_pop,
       AVG(distance_to_road_m) as avg_road_dist
FROM park_settlements
WHERE park_id = 'CAF_Chinko'
GROUP BY classification;
```

### Get deforestation near roads

```sql
SELECT dc.year, dc.area_km2, dc.classification, dc.distance_to_road_m
FROM deforestation_clusters dc
WHERE dc.park_id = 'CAF_Chinko'
  AND dc.distance_to_road_m < 500
  AND dc.classification = 'road_clearing'
ORDER BY dc.year, dc.area_km2 DESC;
```
