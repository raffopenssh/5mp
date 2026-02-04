## Shelley Prompt - GPX Learning Admin UI

### Context

You are working on the 5MP Conservation Monitoring project.

**Project repo:** https://github.com/raffopenssh/5mp  
**Live URL:** https://fivemp-testing.exe.xyz:8000/?pwd=test2026  
**Password:** test2026

### Backend APIs Ready

The backend APIs for GPX learning and admin management are complete. Your task is to build the **Admin UI** for managing learned features.

### API Endpoints Available

#### GPX Upload Logs
```
GET /api/admin/gpx-logs?limit=20&offset=0&park_id=PARK_ID
```
Returns: `{ logs: [...], stats: { total_uploads, valid_uploads, total_patrol_km, total_road_km, total_boundary_km, total_excluded_km } }`

#### Learning Results
```
GET /api/admin/learning-results?limit=20&offset=0&park_id=PARK_ID
```
Returns: `{ results: [...], stats: { total_results, total_new_roads, total_new_roads_km, total_new_places, total_new_airstrips } }`

#### Learned Features by Park
```
GET /api/admin/learned-features?park_id=PARK_ID
```
Returns: `{ roads: [...], airstrips: [...], places: [...] }`

Each feature has:
- `id`, `park_id`, `confidence_pct`, `status` (pending/approved/rejected)
- Roads: `geojson`, `length_m`, `match_count`
- Airstrips: `lat`, `lon`, `heading_deg`, `length_m`, `aircraft_type`, `landing_count`
- Places: `lat`, `lon`, `place_type`, `visit_count`, `avg_duration_minutes`

#### Approve/Reject Features
```
POST /api/admin/approve-feature
POST /api/admin/reject-feature
Body: { "type": "road"|"place"|"airstrip", "id": 123 }
```

#### Feature History
```
GET /api/admin/feature-history?type=road&id=123
```
Returns: `{ type, id, history: [...] }`

#### Rollback Feature
```
POST /api/admin/rollback-feature
Body: { "type": "road", "id": 123, "history_id": 456 }
```

#### Patrol MCP (90% Minimum Convex Polygon)
```
GET /api/parks/{id}/patrol-mcp
```
Returns: `{ park_id, mcp_geojson, mcp_area_km2, point_count, updated_at }`

#### Learned Feature Statistics
```
GET /api/parks/{id}/learned-stats
```
Returns: `{ park_id, roads: {approved, pending, total_km}, airstrips: {...}, places: {...}, vehicle_stats: [...] }`

### UI Requirements

Build a new section in the admin panel (accessible from `/admin`) with these views:

#### 1. GPX Upload Log View
- Table showing recent uploads: filename, park name, timestamp
- For each upload show: patrol_km, road_km, boundary_km, excluded_km
- Color code: green (valid patrol), yellow (boundary/road), red (excluded/auto-generated)
- Filter by park, date range
- Summary stats at top

#### 2. Learning Results View
- Show what was learned from each upload
- Display: new roads found, new places, new airstrips
- Link to approve/reject individual items

#### 3. Pending Approvals View
- List all pending features needing approval
- Group by park and type (road, airstrip, place)
- Show confidence percentage (color coded: >75% green, 50-75% yellow, <50% red)
- Quick approve/reject buttons
- "Approve All >75%" bulk action

#### 4. Feature Details Modal
- When clicking a feature, show:
  - Map preview (using MapLibre)
  - History of changes
  - Confidence explanation
  - Approve/Reject/Rollback buttons

#### 5. Park Learned Features Summary
- Add to park detail page (when admin):
  - Section showing learned roads/places/airstrips
  - MCP visualization overlay on the park map
  - Vehicle speed stats (median, max, p90)

### Key Files to Modify
- `srv/templates/globe.html` - Add admin panel sections
- Or create new `srv/templates/admin_gpx.html` if better separation

### Design Guidelines
- Match existing admin panel style
- Use existing CSS classes
- Keep it clean and functional
- Mobile-responsive

### Commands
```bash
cd ~/5mp
git pull origin main
make build && ./server  # Test locally
```

### Important Notes
- **git pull frequently** - other VMs may be pushing changes
- All endpoints require `?pwd=test2026` or session cookie
- MapLibre GL JS is already loaded in globe.html
- Use the existing notification system for success/error messages

