# Mental Model - Understanding 5MP in 5 Minutes

**Goal**: Give LLM agents a mental model before diving into code.

---

## The Big Picture

```
┌─────────────────────────────────────────────────────────────────┐
│                    USER SEES: 3D Globe                          │
│  - 162 parks rendered as polygons                               │
│  - Click park → Popup with 8 accordion sections                │
│  - Toggle layers (fire, settlement, deforestation, roads)       │
│  - Pin layers to compare across parks                           │
│  - Upload GPS tracks                                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ HTTP Requests
                              │
┌─────────────────────────────────────────────────────────────────┐
│                    GO HTTP SERVER                               │
│  - Serves globe.html (17K lines)                               │
│  - API endpoints return JSON                                    │
│  - Queries SQLite database (1.8GB)                             │
│  - Reads pre-computed JSON files (data/*)                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                  ┌───────────┼───────────┐
                  │           │           │
                  v           v           v
       ┌─────────────┐  ┌─────────┐  ┌────────────┐
       │  DATABASE   │  │  JSON   │  │  PYTHON    │
       │             │  │  FILES  │  │  SCRIPTS   │
       │ 6M+ fires   │  │ 162/type│  │            │
       │ 500K features  │         │  │ Daily cron │
       │ 162 parks   │  │ Fast    │  │ Fire NRT   │
       │             │  │ reads   │  │ Rebuild    │
       └─────────────┘  └─────────┘  └────────────┘
```

---

## Three Mental Models

### Model 1: The Park as a Data Container

**Think of each park as a folder with layers:**

```
CAF_Chinko/
  ├─ park_geometry.json       (boundary polygon)
  ├─ fire_trajectory.json    (173K features for all parks)
  ├─ settlement.json         (64K features)
  ├─ deforestation.json      (221K features)
  ├─ fire_narrative.txt      (pre-computed)
  ├─ deforestation_narrative.txt
  ├─ species.json            (IUCN mammals)
  ├─ climate.json            (monthly precip)
  ├─ rivers.json             (HydroRIVERS)
  └─ roads.json              (HeiGIT OSM)
```

**Each layer can be:**
- Toggled on/off (checkbox in popup)
- Pinned (stays visible after closing popup)
- Loaded lazily (only when accordion opens)

---

### Model 2: The Data Pipeline

**Fire data flows through 5 stages:**

```
1. INGEST
   NASA VIIRS satellite → daily_fire_update.py → fire_detections table
   
2. GROUP
   rebuild_fire_trajectories_v5.py → clusters fires into trajectories
   Output: data/fire_groups_v5/{park}.json
   
3. ENRICH
   load_fire_groups_to_db.py → adds context (rivers, roads, settlements)
   Output: feature_geometries table
   
4. NARRATE
   precompute_narratives_v5.py → generates human-readable summaries
   Output: fire_narrative_cache table
   
5. SERVE
   API reads from cache/JSON → Frontend renders on globe
```

**Key insight**: Stages 2-4 are expensive (minutes to hours).
That's why they're pre-computed and cached.

---

### Model 3: The Frontend State Machine

**The UI has 4 key states:**

```javascript
// 1. OPEN PANELS (left sidebar)
window.openPanels = new Set(['filter', 'star', 'admin', 'upload']);

// 2. OPEN POPUP (center, shows park details)
window.currentPopup = 'CAF_Chinko';
window.openAccordionSections = new Set(['fire', 'deforestation']);

// 3. LOADED LAYERS (Three.js scene)
window.loadedLayers = new Set([
    'CAF_Chinko:fire_trajectory',
    'CAF_Chinko:settlement',
    'COD_Virunga:deforestation'
]);

// 4. PINNED LAYERS (persistent across popups)
window.pinnedLayers = new Map([
    ['CAF_Chinko:fire_trajectory', { parkId: 'CAF_Chinko', type: 'fire_trajectory', name: 'Chinko Fires' }]
]);
```

**All state serializes to URL:**
```
?panel=filter&popup=CAF_Chinko&sections=fire,deforestation&pinned=CAF_Chinko:fire_trajectory
```

**This is the secret to debugging:**
- User reports bug → "Send share link"
- URL contains exact state → Reproduce bug instantly

---

## The 80/20 Files

**These files handle 80% of functionality:**

| File | Lines | What It Does |
|------|-------|-------------|
| `srv/templates/globe.html` | 17K | Entire frontend (Three.js, UI, state) |
| `srv/api.go` | 1.2K | Core API endpoints |
| `srv/fire_realtime_handlers.go` | 800 | Fire data API |
| `srv/narrative_handlers.go` | 600 | Narrative generation |
| `srv/enhanced_narratives.go` | 1.5K | Context-aware narratives |
| `srv/upload.go` | 1.5K | GPX upload handling |
| `db.sqlite3` | 1.8GB | All persistent data |

**The rest is supporting infrastructure:**
- `scripts/*.py` - Data processing pipelines
- `data/*/*.json` - Pre-computed caches
- `srv/*_handlers.go` - Feature-specific APIs
- `tests/*.sh` - Automated testing

---

## The Three Data Sources

### 1. Database (SQLite)

**Use when**: You need to filter, aggregate, or query dynamically.

**Examples:**
- "Show fires in last 30 days" → `WHERE date >= date('now', '-30 days')`
- "Count settlements by park" → `GROUP BY park_id`
- "Find uploads pending approval" → `WHERE status = 'pending_admin'`

**Tables**: `fire_detections`, `feature_geometries`, `uploads`, `notifications`, `parks`

### 2. JSON Files (data/)

**Use when**: You need fast, pre-computed results.

**Examples:**
- "Load fire trajectories for Chinko" → `data/fire_groups_v5/CAF_Chinko.json`
- "Show fire narrative" → `data/export/fire_narratives/CAF_Chinko.json`
- "List settlement clusters" → `data/settlement_events/CAF_Chinko.json`

**Advantage**: No database query overhead, instant response.

### 3. Computed on Demand

**Use when**: Data is small or changes frequently.

**Examples:**
- Park stats (area, fire count, settlement count)
- Real-time fire alerts (last 7 days)
- Upload grid cells

**Pattern**: Query database, cache in memory for 5 minutes.

---

## The Lazy Loading Pattern

**Problem**: Loading all park data at once = 8+ API calls = 5s delay.

**Solution**: Lazy accordion loading.

```javascript
// User opens popup → Load only basic stats
showParkPopup('CAF_Chinko') {
    loadParkStats(parkId);  // 1 API call, <100ms
}

// User clicks "Fire" accordion → Load fire data
openAccordionSection('fire') {
    if (!section.dataset.loaded) {
        await loadFireNarrative(parkId);   // 1 API call
        await loadFireGroups(parkId);      // 1 API call
        section.dataset.loaded = 'true';
    }
}
```

**Result**: Popup opens in <200ms, each section loads in <500ms.

---

## The Context Enrichment Pattern

**Problem**: Fire at coordinates (10.5, 18.2) means nothing to rangers.

**Solution**: Add geographic context.

```
Raw: "Fire detected at 10.5, 18.2"

Enriched: "Fire detected 3km north of Chinko River, near Koundi village,
           along road to ranger camp. Dry season fire pattern."
```

**How?**

```go
func enrichFireLocation(lat, lon, parkID string) Context {
    ctx := Context{}
    
    // Spatial queries
    ctx.NearbyRivers = findRiversWithin(lat, lon, 5km)
    ctx.NearbyRoads = findRoadsWithin(lat, lon, 2km)
    ctx.NearbySettlements = findSettlementsWithin(lat, lon, 10km)
    ctx.NearbyPlaces = findPlacesWithin(lat, lon, 20km)  // OSM places
    
    // Temporal context
    ctx.Season = getSeasonForDate(date, parkClimate)
    
    // Historical context
    ctx.PreviousFires = countFiresWithin(lat, lon, 1km, lastYear)
    
    return ctx
}
```

**Data sources:**
- Rivers: `park_rivers` table (HydroRIVERS)
- Roads: `data/roads_heigit/{park}.json` (OSM)
- Settlements: `park_settlements` table (GHSL)
- Places: `osm_places` table (OSM)
- Climate: `park_climate` table (monthly precip)

---

## The Pin System (Most Unique Feature)

**Problem**: User closes popup → All layers disappear.

**Solution**: Pin layers to keep them visible.

```javascript
// User clicks pin icon → Layer persists
togglePinLayer(parkId, layerType, name) {
    const key = `${parkId}:${layerType}`;
    
    if (pinnedLayers.has(key)) {
        pinnedLayers.delete(key);
        // Layer still visible if popup open
    } else {
        pinnedLayers.set(key, { parkId, layerType, name });
        // Layer stays visible even after closing popup
    }
    
    updatePinnedBadge(pinnedLayers.size);
    updateURL();  // Sync to ?pinned= param
}
```

**Use case**: Compare fire patterns across 3 parks.

```
1. Open CAF_Chinko → Enable fire layer → Pin it
2. Open COD_Virunga → Enable fire layer → Pin it
3. Open TZA_Serengeti → Enable fire layer → Pin it
4. Close all popups → All 3 fire layers still visible
5. Rotate globe → Compare patterns side-by-side
```

**Visual affordance**: Pinned layers show in sidebar with badge count.

---

## The Notification System

**Problem**: Users miss new data (fire alerts, upload approvals, publications).

**Solution**: Unified notification dropdown.

```
Event occurs → Insert into notifications table → Badge count updates

User clicks bell icon → Dropdown shows grouped notifications:
  🔥 Fire Alerts (3 new)
    - CAF_Chinko: 2 new fire groups detected
    - COD_Virunga: 1 new fire group detected
  🚿 Uploads (1 approved)
    - Your patrol at Koundi was approved
  📚 Publications (2 new)
    - "Mountain Gorilla Conservation in Virunga" (2026-03-01)
```

**Deep linking**: Each notification is shareable.

```
?notif_fire=CAF_Chinko:2026_grp_2caaa51b
  → Opens notification dropdown
  → Expands fire section
  → Loads fire trajectory
  → Zooms to fire
  → Pins layer
```

---

## The Daily Cron Pipeline

**What happens every morning at 3am UTC?**

```bash
# scripts/daily_fire_update.py

1. Download last 7 days from NASA FIRMS API
   → UPSERT into fire_detections (6M+ rows)
   
2. Find affected parks (WHERE date >= yesterday)
   → List of 10-20 parks with new fires
   
3. For each affected park:
   a. Rebuild fire groups (v5 algorithm)
   b. Update data/fire_groups_v5/{park}.json
   c. Update feature_geometries table
   d. Regenerate narrative (fire_narrative_cache)
   e. Create notification if new group detected
   
4. Log summary:
   "Processed 15 parks, 234 new fires, 7 new groups"
```

**Result**: By 4am, dashboard shows yesterday's fires.

---

## Common Confusion Points

### "Why two data stores (JSON + DB)?"

JSON files are the **source of truth** for complex computed data.
Database is for **querying and filtering**.

```
JSON file (data/fire_groups_v5/CAF_Chinko.json):
  - Generated by Python script (v5 algorithm)
  - Contains full trajectory data
  - Fast to read (no DB query overhead)
  - API just reads file and returns it

Database (feature_geometries table):
  - Loaded from JSON file
  - Enables queries: "Show fires near this location"
  - Enables filters: "Show fires from 2024-2025"
  - Enables aggregations: "Count fires by month"
```

### "Why is globe.html 17K lines?"

It's a **single-page app** with no build step.

```
Alternative: React + Webpack + Babel + TypeScript
  - More modular code
  - Slower development (build step, tooling)
  - Harder to deploy (node_modules, env vars)
  
Chosen: Vanilla JS in one file
  - No build step = faster iteration
  - Easy to see all frontend logic
  - Easy to deploy (just serve HTML)
  
Trade-off: Hard for LLMs to navigate
  - Solution: Use docs/DATA_FLOW.md and docs/QUICK_TASKS.md
```

### "Why pre-compute narratives?"

Narrative generation is **expensive**:

```python
def generateFireNarrative(parkId):
    # 1. Query 6M fire_detections (1-2s)
    fires = db.query("SELECT ... WHERE park_id = ? AND date >= ...", parkId)
    
    # 2. Group into trajectories (5-10s)
    groups = clusterFires(fires)  # v5 algorithm
    
    # 3. Load context data (2-3s)
    rivers = loadRivers(parkId)
    roads = loadRoads(parkId)
    settlements = loadSettlements(parkId)
    places = loadPlaces(parkId)
    
    # 4. Enrich each group (1-2s per group)
    for group in groups:
        group.context = enrichLocation(group.centroid)
    
    # 5. Generate narrative (1-2s)
    narrative = templateNarrative(groups, context)
    
    return narrative  # Total: 10-20 seconds
```

**Solution**: Pre-compute once, cache in `fire_narrative_cache` table.

**API response time**: <50ms (just read from cache).

---

## Where to Start?

**If you're modifying...**

1. **API endpoints** → Read `docs/QUICK_TASKS.md` section 1
2. **Fire system** → Read `docs/FIRE_DATA_FLOW.md`
3. **Frontend UI** → Search globe.html for the component name, read surrounding 100 lines
4. **Database queries** → Read `docs/DATABASE.md`, use `docs/QUICK_TASKS.md` section 9
5. **Narratives** → Read `srv/enhanced_narratives.go` comments
6. **Data pipeline** → Read `docs/SCRIPTS.md`

**Pro tip**: Use `rg` (ripgrep) to find code:

```bash
# Find where fire narratives are generated
rg "generateFireNarrative" --type go

# Find where park popup opens
rg "showParkPopup" srv/templates/globe.html

# Find all API endpoints with "fire"
rg "r\.Get.*fire" srv/
```

---

## Final Mental Model: The Request Flow

**Trace a typical user interaction:**

```
User clicks park on globe
  ↓
  JavaScript: showParkPopup('CAF_Chinko')
  ↓
  fetch('/api/parks/CAF_Chinko/stats?pwd=test2026')
  ↓
  Go: handleParkStats() in srv/api.go
  ↓
  SQL: SELECT area, fire_count, settlement_count FROM parks WHERE id = 'CAF_Chinko'
  ↓
  JSON: {"area": 17600, "fires": 12453, "settlements": 234}
  ↓
  JavaScript: renderParkStats(data)
  ↓
  DOM: Update popup with stats
  
---

User clicks "Fire" accordion
  ↓
  JavaScript: openAccordionSection('fire')
  ↓
  Check: if (!section.dataset.loaded)
  ↓
  fetch('/api/parks/CAF_Chinko/fire-narrative?pwd=test2026')
  ↓
  Go: handleFireNarrative() in srv/narrative_handlers.go
  ↓
  SQL: SELECT narrative FROM fire_narrative_cache WHERE park_id = 'CAF_Chinko'
  ↓
  JSON: {"narrative": "Fire analysis shows..."}
  ↓
  JavaScript: renderNarrative(data)
  ↓
  DOM: Insert narrative into accordion content
  ↓
  Set: section.dataset.loaded = 'true'
```

**Key insight**: Follow the data from user action → JS → API → DB/JSON → Response → Render.

Use `docs/DATA_FLOW.md` to find the specific files for each step.
