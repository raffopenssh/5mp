# Quick Task Guide for LLM Agents

**Copy-paste solutions for common modification requests**

---

## 1. Add New API Endpoint

### Backend (Go)

```go
// In srv/api.go (or new srv/my_feature_handlers.go)

func handleMyNewEndpoint(w http.ResponseWriter, r *http.Request) {
    parkID := chi.URLParam(r, "id")  // If route has {id}
    
    // Query database
    var result MyStruct
    err := db.QueryRow(`SELECT ... FROM ... WHERE park_id = ?`, parkID).Scan(&result.Field)
    if err != nil {
        http.Error(w, err.Error(), 500)
        return
    }
    
    // Return JSON
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(result)
}

// In srv/server.go, add to router:
r.Get("/api/parks/{id}/my-endpoint", handleMyNewEndpoint)
```

### Frontend (JavaScript)

```javascript
// In globe.html, add to API section (~line 3000-4000)

async function loadMyData(parkId) {
    try {
        const resp = await fetch(`/api/parks/${parkId}/my-endpoint?pwd=${PWD}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        return data;
    } catch (err) {
        console.error('Failed to load my data:', err);
        return null;
    }
}
```

---

## 2. Add New Accordion Section to Park Popup

### Step 1: Add HTML (globe.html ~line 10500)

```html
<div class="accordion-section" data-section="mynewsection">
    <div class="accordion-header">
        <span class="icon">📊</span>
        <span class="title">My New Section</span>
        <span class="arrow">▼</span>
    </div>
    <div class="accordion-content">
        <div class="loading">Loading...</div>
        <div class="mynewsection-data" style="display:none;">
            <!-- Content goes here -->
        </div>
    </div>
</div>
```

### Step 2: Add Loading Logic (globe.html ~line 12000)

```javascript
// In loadAccordionSection() function

if (section === 'mynewsection') {
    const contentDiv = accordionEl.querySelector('.mynewsection-data');
    if (contentDiv.dataset.loaded === 'true') return;
    
    const data = await loadMyData(parkId);
    if (data) {
        contentDiv.innerHTML = `<p>${data.summary}</p>`;
        contentDiv.dataset.loaded = 'true';
        contentDiv.style.display = 'block';
        accordionEl.querySelector('.loading').style.display = 'none';
    }
}
```

### Step 3: Add to Share Link Support (globe.html ~line 2000)

```javascript
// In loadStateFromURL()
if (params.has('sections') && params.get('sections').includes('mynewsection')) {
    openAccordionSection('mynewsection');
}

// In generateShareLink()
const openSections = Array.from(document.querySelectorAll('.accordion-section.open'))
    .map(el => el.dataset.section);
if (openSections.length) params.set('sections', openSections.join(','));
```

---

## 3. Add New Layer Type to Map

### Step 1: Database Schema

```sql
-- Add to migrations/
INSERT INTO feature_geometries (park_id, type, geometry, properties)
VALUES (?, 'my_layer_type', ?, ?);
```

### Step 2: API (srv/api.go)

```go
// In handleFeatures(), add case:
if featureType == "my_layer_type" {
    // Load from JSON file or database
}
```

### Step 3: Frontend Rendering (globe.html ~line 6000)

```javascript
function renderMyLayerType(features, parkId) {
    const group = new THREE.Group();
    group.name = `${parkId}:my_layer_type`;
    
    features.forEach(feature => {
        const mesh = createMeshFromGeoJSON(feature);
        mesh.userData = { parkId, type: 'my_layer_type', ...feature.properties };
        group.add(mesh);
    });
    
    scene.add(group);
    window.loadedLayers.add(`${parkId}:my_layer_type`);
}
```

### Step 4: Add Checkbox to Popup (globe.html ~line 11000)

```html
<div class="layer-control">
    <label>
        <input type="checkbox" data-layer="my_layer_type" onchange="toggleParkLayer('{{parkId}}', 'my_layer_type')">
        My Layer Type
    </label>
    <button class="pin-btn" onclick="togglePinLayer('{{parkId}}', 'my_layer_type', 'My Layer')">📌</button>
</div>
```

---

## 4. Add New Panel to Sidebar

### Step 1: HTML (globe.html ~line 800)

```html
<div id="mypanel" class="panel">
    <div class="panel-header">
        <h3>My Panel</h3>
        <button class="close-btn" onclick="closePanel('mypanel')">&times;</button>
    </div>
    <div class="panel-content">
        <!-- Content -->
    </div>
</div>
```

### Step 2: Toggle Button (globe.html ~line 600)

```html
<button class="control-btn" onclick="togglePanel('mypanel')" title="My Panel">
    <span class="icon">🔧</span>
    <span class="badge" id="mypanel-badge" style="display:none;">0</span>
</button>
```

### Step 3: Panel Logic (globe.html ~line 2500)

```javascript
function togglePanel(panelId) {
    const panel = document.getElementById(panelId);
    const isOpen = panel.classList.contains('open');
    
    // Close all panels first
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('open'));
    
    if (!isOpen) {
        panel.classList.add('open');
        if (panelId === 'mypanel') loadMyPanelData();
    }
}
```

---

## 5. Modify Fire Narrative Generation

**File**: `srv/enhanced_narratives.go` (main logic) or `srv/narrative_handlers.go` (caching)

### Add New Context Data Source

```go
// In buildFireContext()
func buildFireContext(db *sql.DB, parkID string, stats FireStats) (FireContext, error) {
    ctx := FireContext{}
    
    // Add your new query
    var myData string
    err := db.QueryRow(`SELECT my_field FROM my_table WHERE park_id = ?`, parkID).Scan(&myData)
    if err == nil {
        ctx.MyField = myData  // Add MyField to FireContext struct first
    }
    
    return ctx, nil
}
```

### Modify Narrative Template

```go
// In generateFireNarrative()
narrative := fmt.Sprintf(`
    Fire activity analysis for %s:
    
    Over the past %d days, %d fires detected.
    
    New context: %s
    
    ...rest of template...
`, park.Name, stats.Days, stats.Count, ctx.MyField)
```

### Invalidate Cache

```bash
# After modifying narrative logic:
sqlite3 db.sqlite3 "DELETE FROM fire_narrative_cache WHERE park_id = 'CAF_Chinko'"

# Or rebuild all:
python3 scripts/precompute_narratives_v5.py
```

---

## 6. Add New Notification Type

### Step 1: Database (srv/server.go on startup)

```go
// Ensure notifications table has your type
_, err = db.Exec(`
    INSERT INTO notifications (notification_type, park_id, title, message, reference_id)
    VALUES (?, ?, ?, ?, ?)
`, "my_notification_type", parkID, title, message, refID)
```

### Step 2: Frontend Handler (globe.html ~line 13500)

```javascript
function renderNotification(notif) {
    if (notif.notification_type === 'my_notification_type') {
        return `
            <div class="notification-item" data-id="${notif.id}">
                <div class="icon">🔔</div>
                <div class="content">
                    <strong>${notif.title}</strong>
                    <p>${notif.message}</p>
                </div>
                <button onclick="handleMyNotification('${notif.reference_id}')">View</button>
            </div>
        `;
    }
}

function handleMyNotification(refId) {
    // Zoom to location, open popup, etc.
}
```

### Step 3: Share Link Support (globe.html ~line 2000)

```javascript
// Add URL param: ?notif_mytype=REF_ID
if (params.has('notif_mytype')) {
    const refId = params.get('notif_mytype');
    handleMyNotification(refId);
}
```

---

## 7. Query Database from Command Line

```bash
# Basic query
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM fire_detections WHERE park_id = 'CAF_Chinko'"

# With formatting
sqlite3 -header -column db.sqlite3 "SELECT * FROM parks LIMIT 5"

# Export to CSV
sqlite3 -header -csv db.sqlite3 "SELECT * FROM fire_detections WHERE park_id = 'CAF_Chinko' LIMIT 1000" > fires.csv

# Check table schema
sqlite3 db.sqlite3 ".schema fire_detections"

# List all tables
sqlite3 db.sqlite3 ".tables"
```

---

## 8. Debug Frontend Issues

### Enable Test Mode

```javascript
// URL: http://localhost:8000/?pwd=test2026&test=1

// In console:
TEST.assertExists('#map', 'Map exists');
TEST.assertVisible('.stats-panel', 'Stats visible');
TEST.isPanelOpen('admin');  // Returns true/false
TEST.isPopupOpen('CAF_Chinko');
TEST.done();  // Print results
```

### Inspect State

```javascript
// Check pinned layers
console.log(window.pinnedLayers);

// Check loaded layers
console.log(Array.from(window.loadedLayers));

// Check fire groups
console.log(window.fireGroups['CAF_Chinko']);

// Check narrative cache
console.log(window.narrativeCache['CAF_Chinko']);
```

### Network Debugging

```javascript
// Monitor API calls
window.addEventListener('fetch', (e) => {
    console.log('Fetch:', e.request.url);
});

// Check failed requests
const failed = performance.getEntriesByType('resource')
    .filter(r => r.name.includes('/api/') && r.transferSize === 0);
console.log('Failed API calls:', failed);
```

---

## 9. Common Database Queries

### Fire Data

```sql
-- Recent fires for a park
SELECT * FROM fire_detections 
WHERE park_id = 'CAF_Chinko' 
  AND date >= date('now', '-30 days')
ORDER BY date DESC 
LIMIT 100;

-- Fire count by month
SELECT strftime('%Y-%m', date) AS month, COUNT(*) AS fires
FROM fire_detections
WHERE park_id = 'CAF_Chinko'
GROUP BY month
ORDER BY month;

-- Fire trajectories for a park
SELECT id, properties->>'group_id' AS group_id, 
       json_extract(properties, '$.classification') AS class
FROM feature_geometries
WHERE park_id = 'CAF_Chinko' AND type = 'fire_trajectory';
```

### Upload Data

```sql
-- Recent uploads
SELECT id, filename, status, created_at
FROM uploads
ORDER BY created_at DESC
LIMIT 20;

-- Uploads pending admin review
SELECT id, filename, user_id, created_at
FROM uploads
WHERE status = 'pending_admin'
ORDER BY created_at;

-- Upload statistics by user
SELECT user_id, COUNT(*) AS uploads, 
       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS approved
FROM uploads
GROUP BY user_id;
```

### Feature Data

```sql
-- Count features by type and park
SELECT park_id, type, COUNT(*) AS count
FROM feature_geometries
GROUP BY park_id, type
ORDER BY park_id, type;

-- Find features by classification
SELECT park_id, type, id, properties->>'classification' AS class
FROM feature_geometries
WHERE properties->>'classification' = 'critical'
LIMIT 100;
```

---

## 10. Testing Workflow

### Run Tests

```bash
# All tests
./tests/run_all.sh

# Specific suite
./tests/run_all.sh db    # Database tests
./tests/run_all.sh api   # API tests
./tests/run_all.sh ui    # UI tests
```

### Manual Browser Test

```bash
# 1. Start server
make build && ./server

# 2. Open in browser (resize to 1280x1400+)
http://localhost:8000/?pwd=test2026&test=1

# 3. Run test scenarios
- Click park → Check popup opens
- Toggle layer → Check mesh renders
- Pin layer → Check badge updates
- Share link → Check state restores
```

### Create Share Link for Bug Report

```javascript
// Reproduce bug state, then:
const shareLink = generateShareLink();
console.log('Bug reproduction link:', shareLink);
// Copy link to bug report
```

---

## 11. Performance Optimization

### Database

```sql
-- Add index for slow query
CREATE INDEX IF NOT EXISTS idx_fire_park_date 
ON fire_detections(park_id, date);

-- Check query plan
EXPLAIN QUERY PLAN 
SELECT * FROM fire_detections WHERE park_id = 'CAF_Chinko';

-- Vacuum database
VACUUM;
```

### Frontend

```javascript
// Throttle expensive operations
const throttledRender = throttle(() => {
    renderVisibleLayers();
}, 100);

// Lazy load images
document.querySelectorAll('img[data-src]').forEach(img => {
    observer.observe(img);
});

// Request animation frame for smooth updates
function updateFrame() {
    if (needsUpdate) {
        renderer.render(scene, camera);
        needsUpdate = false;
    }
    requestAnimationFrame(updateFrame);
}
```

---

## 12. Common Gotchas

### URL Parameter Encoding

```javascript
// Wrong: ?pinned=CAF_Chinko:fire_trajectory,COD_Virunga:settlement
// Right: Encode the whole value
params.set('pinned', encodeURIComponent(pinnedStr));
```

### Three.js Memory Leaks

```javascript
// Always dispose when removing layers
function removeParkLayer(parkId, type) {
    const group = scene.getObjectByName(`${parkId}:${type}`);
    if (group) {
        group.traverse(obj => {
            if (obj.geometry) obj.geometry.dispose();
            if (obj.material) obj.material.dispose();
        });
        scene.remove(group);
    }
}
```

### SQLite Date Formats

```sql
-- Store as ISO 8601: '2026-03-04'
-- Query with date functions:
WHERE date >= date('now', '-30 days')

-- NOT as Unix timestamp (unless you need it)
```

### Async Race Conditions

```javascript
// Wrong: Multiple calls overwrite each other
function loadData() {
    fetch('/api/data').then(data => this.data = data);
}

// Right: Track pending request
function loadData() {
    if (this.loadingPromise) return this.loadingPromise;
    this.loadingPromise = fetch('/api/data')
        .then(data => this.data = data)
        .finally(() => this.loadingPromise = null);
    return this.loadingPromise;
}
```
