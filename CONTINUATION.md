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

## Known Limitations

### Classification
Current pattern detection is basic:
- **Deforestation**: scattered, cluster, strip, minor
  - Does NOT distinguish: farms vs clear-cut vs charcoal vs road-clearing
- **Settlements**: clustered, linear, scattered, dispersed, isolated
  - Does NOT distinguish: hamlets vs farms vs mines

### To improve classification would need:
1. Distance to roads analysis (charcoal typically <5km walk)
2. Shape analysis (circular=charcoal, linear=road)
3. Cross-reference with OSM industrial/mining data
4. Size thresholds for hamlet/farm/mine

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
