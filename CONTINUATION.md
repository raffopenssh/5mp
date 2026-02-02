# 5MP Globe - Continuation Notes

## Current State (Feb 2026)

The application is functional with:
- Interactive 3D globe showing 162 African keystone protected areas
- Fire detection data with trajectory narratives
- Deforestation analysis with yearly narratives
- Settlement/GHSL data with pattern analysis
- GPX patrol track uploads (anonymous)
- Search across parks, countries, regions

## Recent Changes

### Narrative Improvements
- Fire trajectories describe movement using OSM place names
- Deforestation shows location context: "14.15 km² occurred 8 km southeast of Bihundo"
- Settlements show pattern analysis: "Widely dispersed settlements suggest isolated homesteads"
- All narrative sections show top 10 with "load more" button

### UI Changes
- Login/register hidden - anonymous uploads enabled
- GPX upload button in header
- Unified search (parks + countries + regions)
- Collapsible popup sections
- Increased popup height for narrative visibility

## Enhanced Narratives (Feb 2026)

See `docs/ENHANCED_NARRATIVES_PROPOSAL.md` for full plan.

### New Classification System

**Settlements** (via `scripts/classify_features.py`):
- `roadside_settlement`: <500m from road
- `hamlet`: 5+ nearby settlements, >2km from roads
- `artisanal_mining_camp`: near river + deforestation
- `agricultural_compound`: small, isolated
- `fishing_camp`: near river, no deforestation
- `unclassified`: default

**Deforestation** (via `scripts/classify_features.py`):
- `road_clearing`: linear, parallel to road <500m
- `agricultural_expansion`: near settlements <5km
- `charcoal_production`: 2-10km from road
- `artisanal_mining`: near river <1km
- `natural_disturbance`: far from everything
- `unclassified`: default

### New Tables

- `feature_geometries`: Centralized GeoJSON for map display
  - fire_trajectory, settlement, deforestation, road
  - bbox for spatial queries
  - start_date/end_date for time slider

### Processing Scripts

```bash
# Extract GeoJSON from existing data
python scripts/extract_geometries.py --all-types --all

# Classify features using spatial cross-reference
python scripts/classify_features.py --all-types --all

# Add population from GHSL POP data
python scripts/ghsl_pop_processor.py --zip data/ghsl_pop_2030.zip --all
```

### VM Distribution

- **fivemp-testing**: Development, API changes
- **five-mp-conservation-effort**: Heavy processing (GHSL, classification)
- **five-megapixel-conservation**: Production (don't modify until validated)

## Database

Main database: `db.sqlite3` (~1.3GB)
Static download: `static/downloads/5mp_data.sqlite3`

Key tables:
- `fire_detections` - 1.7M fire records
- `deforestation_events` - yearly forest loss per park
- `park_settlements` - GHSL built-up areas
- `osm_places` - place names for narrative context

## Running

```bash
make build && ./server
```

Access at: http://localhost:8000/?pwd=test2026

## Deployment

Server runs as systemd service. After changes:
```bash
make build
sudo systemctl restart srv
```
