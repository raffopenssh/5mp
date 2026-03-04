# Data Flow Reference

**Quick navigation for LLM agents to understand the system**

## Core Data Flow

```
Database → API Handler → JSON Response → Frontend State → UI Update
```

## 1. Fire Data Flow

### Source Chain
```
VIIRS Satellite → daily_fire_update.py → fire_detections table
                                        ↓
                    rebuild_fire_trajectories_v5.py → data/fire_groups_v5/{park}.json
                                        ↓
                    load_fire_groups_to_db.py → feature_geometries table
                                        ↓
                    precompute_narratives_v5.py → fire_narrative_cache table
```

### Runtime API Chain
```
GET /api/parks/{id}/fire-realtime
  ↓ srv/fire_realtime_handlers.go:handleFireRealtime()
  ↓ Reads data/fire_groups_v5/{park}.json
  ↓ Returns: { groups: [...], trajectories: [...] }
  ↓ Frontend: FireAPI.loadFireGroups()
  ↓ Stores in: window.fireGroups[parkId]
  ↓ Renders: addFireTrajectories() → Three.js meshes
```

### Fire Narrative Chain
```
GET /api/parks/{id}/fire-narrative
  ↓ srv/narrative_handlers.go:handleFireNarrative()
  ↓ Checks fire_narrative_cache table (keyed by park_id)
  ↓ If miss: Generates from fire_detections + context tables
  ↓ Returns: { narrative: "...", stats: {...} }
  ↓ Frontend: NarrativeAPI.loadNarrative('fire')
  ↓ Renders in park popup accordion
```

## 2. Settlement/Deforestation Flow

### Source Chain
```
GHSL Data → scripts/process_ghsl.py → data/settlement_events/{park}.json
                                     ↓
          srv/upload.go:handleSettlementUpload() → feature_geometries table
                                                  + park_settlements table
```

### Runtime Chain
```
GET /api/parks/{id}/features?type=settlement
  ↓ srv/api.go:handleFeatures()
  ↓ Queries feature_geometries WHERE park_id={id} AND type='settlement'
  ↓ Returns GeoJSON FeatureCollection
  ↓ Frontend: loadParkLayer(parkId, 'settlement')
  ↓ Converts to Three.js meshes with polygon tessellation
```

## 3. Patrol Upload Flow

### Upload Chain
```
POST /api/upload/async + GPX file
  ↓ srv/upload.go:handleAsyncUpload()
  ↓ Inserts into uploads table (status='pending')
  ↓ Returns: { uploadId }
  ↓
  ↓ Background worker (srv/upload_queue.go)
  ↓ Polls uploads WHERE status='pending'
  ↓ Parses GPX → segments → grid cells
  ↓ Inserts into upload_segments, upload_grid_cells
  ↓ Updates status='completed'
  ↓ Creates notification
```

### Learning Chain
```
Background worker (srv/gpx_learner.go)
  ↓ Queries upload_grid_cells grouped by cell_id
  ↓ Detects patterns (frequency, recency, seasonal)
  ↓ Updates learned_patterns table
  ↓ Used by recommendation engine
```

## 4. Popup Accordion Data Loading

**User clicks park → Popup opens → Accordion sections lazy-load**

```javascript
// globe.html around line 12000
function showParkPopup(parkId) {
  // 1. Load basic stats (always)
  loadParkStats(parkId);
  
  // 2. When accordion section opens:
  if (section === 'fire') {
    NarrativeAPI.loadNarrative(parkId, 'fire');
    FireAPI.loadFireGroups(parkId);
  }
  if (section === 'deforestation') {
    NarrativeAPI.loadNarrative(parkId, 'deforestation');
  }
  if (section === 'species') {
    loadSpecies(parkId);
  }
}
```

## 5. Admin Approval Flow

```
Upload appears in Admin Panel
  ↓ User reviews on map
  ↓ POST /api/admin/approve-feature { uploadId, featureId }
  ↓ srv/api.go:handleApproveFeature()
  ↓ Creates entry in feature_geometries
  ↓ Updates upload status
  ↓ Creates notification for community
```

## 6. Notification System

```
Event occurs (fire alert, upload approval, publication)
  ↓ srv creates notification entry
  ↓ GET /api/notifications returns unread count
  ↓ Frontend polls every 30s
  ↓ Badge updates in UI
  ↓ User clicks → Dropdown shows grouped notifications
  ↓ Click notification → Zoom to location + pin layer
```

## Key State Objects in Frontend

```javascript
// Global state (globe.html)
window.pinnedLayers = new Map();  // Map<layerKey, {parkId, type, name}>
window.fireGroups = {};           // Map<parkId, Array<fireGroup>>
window.starredParks = new Set();  // Set of parkIds
window.loadedLayers = new Set();  // Set of 'parkId:type' strings
window.narrativeCache = {};       // Map<parkId, {fire: {...}, deforestation: {...}}>
```

## Common Patterns

### Pattern 1: Layer Toggle
```javascript
// 1. User clicks checkbox in popup
// 2. Event handler calls toggleParkLayer(parkId, layerType)
// 3. If not loaded: loadParkLayer() → API call → render Three.js
// 4. If loaded: toggle visibility of existing meshes
// 5. Update window.loadedLayers Set
```

### Pattern 2: Pin/Unpin Layer
```javascript
// 1. User clicks pin icon
// 2. Event handler calls togglePinLayer(parkId, layerType, name)
// 3. Update window.pinnedLayers Map
// 4. Sync to URL params (?pinned=)
// 5. Update sidebar badge count
```

### Pattern 3: Share Link Generation
```javascript
// All UI state → URL params
function generateShareLink() {
  const params = new URLSearchParams();
  if (openPanels.size) params.set('panel', Array.from(openPanels).join(','));
  if (openPopup) params.set('popup', openPopup);
  if (pinnedLayers.size) params.set('pinned', serializePinned());
  // ... etc
  return window.location.origin + '/?' + params.toString();
}
```

## Performance Notes

- **Fire detections**: 6M+ rows, queries MUST use date range filters
- **Feature geometries**: 458K rows, always filter by park_id first
- **JSON files**: Pre-computed for fast API responses (~162 files per type)
- **Three.js**: Render budget ~10K polygons visible at once
- **Lazy loading**: Only load data when accordion section opens

## Debugging Tips

```javascript
// Browser console
TEST.isPanelOpen('admin');  // Check panel state
TEST.isPopupOpen('CAF_Chinko');  // Check popup state
console.log(window.pinnedLayers);  // See pinned state
console.log(window.loadedLayers);  // See loaded layers
```

## Quick File Lookup

| Need to modify... | Edit this file |
|-------------------|----------------|
| API endpoint | `srv/api.go` or `srv/*_handlers.go` |
| Fire narrative logic | `srv/narrative_handlers.go`, `srv/enhanced_narratives.go` |
| Fire realtime data | `srv/fire_realtime_handlers.go` |
| Database queries | Search for `db.Query` in `srv/*.go` |
| Frontend UI | `srv/templates/globe.html` (lines 1-16852) |
| Popup layout | globe.html ~line 10000-12000 |
| Three.js rendering | globe.html ~line 5000-8000 |
| State management | globe.html ~line 1000-3000 |
| Panel logic | globe.html ~line 12000-14000 |
| URL param handling | globe.html ~line 2000 (loadStateFromURL) |
