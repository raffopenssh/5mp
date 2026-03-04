# Architecture Decision Records

**Why things are built the way they are**

---

## ADR-001: Single-Page Application with Inline JavaScript

**Decision**: All frontend code lives in one 17K-line `globe.html` file with inline JavaScript.

**Context**: 
- Rapid prototyping required
- No build step = faster iteration
- Three.js globe needs tight integration with UI

**Consequences**:
- ✅ Fast development, no webpack/build complexity
- ✅ Easy to see entire frontend logic
- ❌ Hard for LLM agents to navigate
- ❌ No code splitting or tree shaking

**Future**: Consider splitting into modules with ES6 imports if file grows beyond 20K lines.

---

## ADR-002: Pre-computed JSON Files + Database

**Decision**: Fire trajectories and narratives stored in BOTH:
- JSON files (`data/fire_groups_v5/`, `data/export/fire_narratives/`)
- Database tables (`feature_geometries`, `fire_narrative_cache`)

**Context**:
- Fire trajectory computation is expensive (v5 algorithm)
- Narrative generation hits multiple APIs and tables
- Need fast page loads (<2s)

**Consequences**:
- ✅ API responses are instant (read JSON file)
- ✅ Can rebuild anytime without losing data
- ❌ Dual storage = sync issues if not careful
- ❌ Deployment requires copying JSON files

**Usage**:
- **JSON files**: Source of truth for fire groups/narratives
- **Database**: Fast queries for filtering, stats, realtime
- **Sync**: `load_fire_groups_to_db.py`, `precompute_narratives_v5.py`

---

## ADR-003: Three.js for Globe Rendering

**Decision**: Use Three.js + WebGL for 3D globe instead of Mapbox/Leaflet.

**Context**:
- Need to render 100K+ polygons smoothly
- Want satellite-style view with rotation
- Conservation areas need visual prominence

**Consequences**:
- ✅ Smooth 60fps rotation with large datasets
- ✅ Custom styling, dramatic visual impact
- ❌ Higher complexity than 2D mapping libraries
- ❌ No built-in tile caching, zoom levels

**Performance Optimizations**:
- Frustum culling (only render visible parks)
- Level-of-detail (simplified geometry when far)
- Instanced rendering for repeated shapes

---

## ADR-004: SQLite for Production Database

**Decision**: Use SQLite instead of PostgreSQL/MySQL.

**Context**:
- Single-user admin interface
- 6M+ fire records, 500K features
- Hosted on exe.dev VM with limited resources

**Consequences**:
- ✅ Zero-config deployment
- ✅ 1.8GB file, easy backups (just copy file)
- ✅ Fast reads (most queries <10ms)
- ❌ Single writer (but no concurrent write needs)
- ❌ Limited full-text search vs Postgres

**Optimizations**:
- WAL mode enabled (better concurrency)
- JSON1 extension for property queries
- Spatial index using GeoJSON bbox

---

## ADR-005: Password-Protected Public Access

**Decision**: Simple password auth instead of user accounts.

**Context**:
- Stakeholders need easy access (no signup friction)
- Multiple organizations (NGI, APN, J-foundation)
- Admin interface separate from public view

**Consequences**:
- ✅ Zero-friction sharing (just add ?pwd=)
- ✅ Different passwords per partner
- ❌ No per-user permissions
- ❌ Password in URL (less secure)

**Security**:
- Admin endpoints require separate login (cookie-based)
- Download files are token-protected
- Robots.txt blocks search engines

---

## ADR-006: V5 Fire Trajectory Algorithm

**Decision**: Group fires using 3km distance + 7-day window + context-aware classification.

**Context**:
- V1-V4 produced too many false groups
- Need to distinguish slash-and-burn vs wildfires
- Narrative quality depends on accurate grouping

**Algorithm**:
```python
1. Sort fires by date
2. Start new group if:
   - Distance > 3km from any fire in group
   - Gap > 7 days since last fire in group
3. Classify group using:
   - Duration (short=clearing, long=wildfire)
   - Movement pattern (static=controlled, spreading=uncontrolled)
   - Proximity to roads/settlements
   - Seasonality (dry season=natural, wet season=human)
```

**Consequences**:
- ✅ 85% reduction in false groups
- ✅ Better narrative accuracy
- ❌ More complex to debug
- ❌ Requires context data (roads, settlements, climate)

---

## ADR-007: Lazy Accordion Loading

**Decision**: Park popup accordion sections load data only when opened.

**Context**:
- Each park has 8+ data sections (fire, deforestation, species, climate, etc.)
- Loading all at once = 5+ API calls = slow popup
- Most users only view 1-2 sections

**Implementation**:
```javascript
// On accordion open:
if (!section.dataset.loaded) {
    const data = await loadSectionData(parkId, sectionType);
    renderSection(section, data);
    section.dataset.loaded = 'true';
}
```

**Consequences**:
- ✅ Popup opens instantly
- ✅ Reduced server load
- ❌ Slight delay when opening section
- ❌ More complex state management

---

## ADR-008: Async Upload Queue

**Decision**: GPX uploads process asynchronously via background worker.

**Context**:
- Large GPX files (100MB+) take 30s+ to parse
- HTTP timeout = 30s
- Need to show progress to user

**Flow**:
```
1. POST /api/upload/async → Insert into uploads table (status='pending')
2. Return { uploadId } immediately
3. Background worker polls for pending uploads
4. Parse GPX → Update status → Create notification
5. Frontend polls /api/uploads/{id} for status
```

**Consequences**:
- ✅ No HTTP timeouts
- ✅ User can continue using app during upload
- ✅ Scalable to multiple concurrent uploads
- ❌ More complex error handling
- ❌ Requires polling (no WebSockets)

---

## ADR-009: Notification-Driven Updates

**Decision**: All new data creates a notification that appears in dropdown.

**Context**:
- Fire alerts, upload approvals, publications arrive continuously
- Users need to know about new data
- Share links need to deep-link to specific events

**Types**:
- `fire_alert`: New fire trajectory detected
- `upload_approved`: Admin approved your patrol
- `publication`: New research paper published
- `legal_document`: New conservation law
- `mbtiles_ready`: Offline map downloaded

**Consequences**:
- ✅ Central place for all updates
- ✅ Deep-linkable via `?notif_fire=`, `?notif_upload=`, etc.
- ✅ Reduces need for email notifications
- ❌ Badge count can grow large
- ❌ No push notifications (browser-only)

---

## ADR-010: Share Link as State Serialization

**Decision**: Entire UI state serializes to URL parameters.

**Context**:
- Need reproducible bug reports
- Stakeholders want to share specific views
- Testing needs exact state restoration

**Encoded State**:
- `panel`: Open panels (filter, star, admin, upload)
- `popup`: Open park popup (e.g., CAF_Chinko)
- `sections`: Open accordion sections (fire, deforestation)
- `pinned`: Pinned layers (CAF_Chinko:fire_trajectory)
- `starred_parks`: Starred parks list
- `filters`: Active filters (date range, feature types)

**Example**:
```
?pwd=test2026
&panel=filter
&popup=CAF_Chinko
&sections=fire,deforestation
&pinned=CAF_Chinko:fire_trajectory
&starred_parks=CAF_Chinko,COD_Virunga
```

**Consequences**:
- ✅ Perfect state restoration
- ✅ Easy bug reproduction
- ✅ Shareable research findings
- ❌ Long URLs (200+ chars)
- ❌ Complex parsing logic

---

## ADR-011: Pin Layer System

**Decision**: Allow users to "pin" layers from any park to keep them visible.

**Context**:
- Users want to compare fire patterns across parks
- Closing popup removes layers = data loss
- Need visual reminder of active layers

**Implementation**:
```javascript
window.pinnedLayers = new Map([
    ['CAF_Chinko:fire_trajectory', { parkId: 'CAF_Chinko', type: 'fire_trajectory', name: 'Chinko Fires' }],
    ['COD_Virunga:settlement', { parkId: 'COD_Virunga', type: 'settlement', name: 'Virunga Settlements' }]
]);

// Badge shows count
document.getElementById('pinned-badge').textContent = pinnedLayers.size;

// Sidebar shows list
pinnedLayers.forEach(layer => {
    renderPinnedItem(layer);
});
```

**Consequences**:
- ✅ Easy cross-park comparison
- ✅ Persistent visualization
- ✅ Clear UI affordance (pin icon)
- ❌ Can accumulate too many layers
- ❌ Performance degrades beyond ~10 pinned layers

---

## ADR-012: Admin Approval Workflow

**Decision**: All user uploads require admin approval before appearing on map.

**Context**:
- Data quality is critical for conservation decisions
- Risk of spam or incorrect data
- Need to validate GPS tracks before public display

**Workflow**:
```
1. User uploads GPX → status='pending_admin'
2. Appears in admin panel with map preview
3. Admin reviews → Approve or Reject
4. If approved: Creates feature_geometries entry + notification
5. Community sees new patrol data
```

**Consequences**:
- ✅ High data quality
- ✅ Prevents abuse
- ❌ Requires active admin monitoring
- ❌ Delay before user sees data public

**Future**: Add "trusted uploader" status to skip approval.

---

## ADR-013: Daily Fire Cron Job

**Decision**: Run fire data update every day at 3am UTC via cron.

**Context**:
- NASA FIRMS provides NRT (near-realtime) fire data with 3-hour lag
- Users expect daily updates
- Compute-intensive operations should run off-peak

**Pipeline** (see `scripts/daily_fire_update.py`):
```
1. Download last 7 days from FIRMS API (upsert to fire_detections)
2. Identify affected parks (WHERE date >= yesterday)
3. Rebuild fire groups for those parks (v5 algorithm)
4. Update JSON files (data/fire_groups_v5/)
5. Update database (load_fire_groups_to_db.py)
6. Regenerate narratives (precompute_narratives_v5.py)
7. Create notifications for new fire groups
```

**Consequences**:
- ✅ Always fresh data by morning
- ✅ Incremental updates = faster than full rebuild
- ❌ If cron fails, data becomes stale
- ❌ No alerting on failure (yet)

---

## ADR-014: Context-Aware Narratives

**Decision**: Fire narratives incorporate geographic context (rivers, roads, settlements, places).

**Context**:
- "Fire detected at (10.5, 18.2)" is meaningless to rangers
- Need to say "Fire near Chinko River, 3km from ranger camp"
- Context makes narratives actionable

**Data Sources**:
- **Rivers**: HydroRIVERS (loaded per park)
- **Roads**: HeiGIT OSM road surface data
- **Settlements**: GHSL clustering
- **Places**: OSM place names (cities, villages, landmarks)
- **Climate**: Monthly precipitation + season definitions

**Implementation** (see `srv/enhanced_narratives.go`):
```go
func buildFireContext(parkID string) FireContext {
    ctx.NearbyRivers = findRiversWithin(fireLocation, 5km)
    ctx.NearbyRoads = findRoadsWithin(fireLocation, 2km)
    ctx.NearbySettlements = findSettlementsWithin(fireLocation, 10km)
    ctx.NearbyPlaces = findPlacesWithin(fireLocation, 20km)
    ctx.Season = getSeasonForDate(fireDate, parkClimate)
    return ctx
}
```

**Consequences**:
- ✅ Narratives are geographically meaningful
- ✅ Rangers can locate fires without coordinates
- ❌ Complex data pipeline (5+ sources)
- ❌ Context data must be kept in sync

---

## ADR-015: No Real-Time Collaboration

**Decision**: No WebSockets or real-time sync between users.

**Context**:
- Expected concurrent users: <10
- Data updates are infrequent (daily fire updates)
- Simplicity > real-time features

**Consequences**:
- ✅ Simple HTTP-only architecture
- ✅ No WebSocket infrastructure
- ❌ Users must refresh to see others' changes
- ❌ No live cursors or presence indicators

**Future**: If user count grows, consider Server-Sent Events for notifications.

---

## ADR-016: Embedded Git Version

**Decision**: Build embeds git commit hash into binary, shown in footer.

**Why**:
- Need to know which version is deployed
- Bug reports should include version
- Click footer version → see commit history modal

**Implementation** (Makefile):
```makefile
VERSION := $(shell git rev-parse --short HEAD)
build:
    go build -ldflags "-X srv.exe.dev/srv.Version=$(VERSION)" -o server cmd/srv/main.go
```

**Consequences**:
- ✅ Easy version tracking
- ✅ `.git-commits.txt` shows history in UI
- ❌ Requires git repo during build

---

## Summary

These decisions prioritize:
1. **Simplicity**: No build steps, no frameworks, no containers
2. **Performance**: Pre-computed data, lazy loading, spatial indexing
3. **Shareability**: URL-based state, password-protected public access
4. **Data Quality**: Admin approval, context-aware narratives, daily updates

Trade-offs:
- Single-page app = harder to navigate for LLMs
- Dual storage (JSON + DB) = sync complexity
- No real-time = refresh required
