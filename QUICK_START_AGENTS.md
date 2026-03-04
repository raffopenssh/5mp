# Quick Start for LLM Agents

**Read this first. 2-minute orientation.**

---

## What You're Working With

- **Type**: Single-page web app (Go backend + 17K-line HTML/JS frontend)
- **Purpose**: Conservation monitoring dashboard for 162 African protected areas
- **Data**: 6M+ fire detections, 500K features, 1.8GB SQLite database
- **Complexity**: High (3D globe, spatial queries, async uploads, narrative generation)

---

## Before You Start ANY Task

### Step 1: Identify the Domain

Which part of the system are you modifying?

- **Fire system** → Read `docs/FIRE_DATA_FLOW.md`
- **API endpoints** → Read `docs/DATA_FLOW.md` + `docs/QUICK_TASKS.md` §1
- **Frontend UI** → Read `docs/MENTAL_MODEL.md` §3 (State Machine)
- **Database queries** → Read `docs/DATABASE.md` + `docs/QUICK_TASKS.md` §9
- **Narratives** → Read `docs/ARCHITECTURE_DECISIONS.md` ADR-014
- **Data pipeline** → Read `docs/SCRIPTS.md`

### Step 2: Find the Files

Use the **80/20 rule** (see `docs/MENTAL_MODEL.md` §3):

```bash
# Frontend: Everything is in one file
srv/templates/globe.html     # 17K lines - UI, Three.js, state management

# Backend: Organized by feature
srv/api.go                   # Core API endpoints
srv/fire_realtime_handlers.go  # Fire APIs
srv/narrative_handlers.go    # Narrative generation
srv/enhanced_narratives.go   # Context-aware narratives
srv/upload.go                # GPX uploads

# Database
db.sqlite3                   # 1.8GB - All persistent data

# Data files
data/fire_groups_v5/*.json   # Pre-computed fire trajectories (162 files)
data/export/fire_narratives/*.json  # Pre-computed narratives (162 files)
```

### Step 3: Understand the Pattern

**Most features follow this flow:**

```
User Action (globe.html)
  ↓
  API Request (fetch + ?pwd=test2026)
  ↓
  Go Handler (srv/*_handlers.go)
  ↓
  Data Source (db.sqlite3 OR data/*.json)
  ↓
  JSON Response
  ↓
  Frontend Render (DOM update OR Three.js mesh)
```

**Find your place in this chain** → Read the relevant section in `docs/DATA_FLOW.md`

---

## Common Tasks (Copy-Paste Solutions)

**Before writing ANY code, check `docs/QUICK_TASKS.md` for your exact task:**

- §1: Add new API endpoint
- §2: Add new accordion section to park popup
- §3: Add new layer type to map
- §4: Add new panel to sidebar
- §5: Modify fire narrative generation
- §6: Add new notification type
- §7: Database queries from command line
- §8: Debug frontend issues
- §9: Common database queries
- §10: Testing workflow

**Don't reinvent patterns.** The codebase has established conventions. Copy them.

---

## Key Gotchas (Read Before You Break Things)

### 1. Database Protection

⚠️ **NEVER run DELETE/DROP without confirmation**

```bash
# Safe: Always use LIMIT when exploring
sqlite3 db.sqlite3 "SELECT * FROM fire_detections LIMIT 10"

# Dangerous: This deletes 6M+ rows
sqlite3 db.sqlite3 "DELETE FROM fire_detections"  # DON'T
```

### 2. Two Data Stores

**JSON files are source of truth** for computed data:
- `data/fire_groups_v5/` → Fire trajectories
- `data/export/fire_narratives/` → Narratives

**Database is for querying:**
- `feature_geometries` → Loaded from JSON
- `fire_narrative_cache` → Loaded from JSON

**If you modify generation logic**, rebuild both:
```bash
python3 scripts/rebuild_fire_trajectories_v5.py  # → JSON files
python3 scripts/load_fire_groups_to_db.py --force  # → Database
```

### 3. Frontend State Management

**All UI state lives in global variables:**

```javascript
window.pinnedLayers      // Map<string, object>
window.loadedLayers      // Set<string>
window.fireGroups        // Object<parkId, Array>
window.narrativeCache    // Object<parkId, Object>
```

**State syncs to URL params** for share links. If you add new state, update:
- `loadStateFromURL()` (parse params)
- `generateShareLink()` (serialize to params)

### 4. Three.js Memory Leaks

**Always dispose geometries and materials:**

```javascript
// Wrong: Leaks memory
scene.remove(group);

// Right: Clean up first
group.traverse(obj => {
    if (obj.geometry) obj.geometry.dispose();
    if (obj.material) obj.material.dispose();
});
scene.remove(group);
```

### 5. Password Protection

**Most endpoints require password:**
- Query param: `?pwd=test2026`
- Cookie: `access_pwd=test2026`

**Valid passwords**: `test2026`, `REDACTED_PWD`, `REDACTED_PWD`

**Admin endpoints** require separate login (cookie-based).

---

## Testing Your Changes

### 1. Build and Run

```bash
make build && ./server
# Access: http://localhost:8000/?pwd=test2026
```

### 2. Run Tests

```bash
# All tests
./tests/run_all.sh

# Specific suite
./tests/run_all.sh db    # Database tests
./tests/run_all.sh api   # API tests
./tests/run_all.sh ui    # UI tests
```

### 3. Manual Browser Testing

```bash
# Resize browser to 1280x1400 (to see full popups)
# Add ?test=1 to URL for test helpers
http://localhost:8000/?pwd=test2026&test=1

# In console:
TEST.assertExists('#map', 'Map exists');
TEST.isPanelOpen('admin');
TEST.isPopupOpen('CAF_Chinko');
TEST.done();
```

### 4. Generate Share Link for Bug Reports

```javascript
// Reproduce bug state, then:
const shareLink = generateShareLink();
console.log('Bug reproduction link:', shareLink);
// Include link in bug report
```

---

## Architecture Principles

**Read `docs/ARCHITECTURE_DECISIONS.md` to understand WHY things are built this way.**

Key decisions:
- Single-page app (no build step) → Fast iteration
- SQLite (not Postgres) → Zero-config deployment
- Pre-computed JSON + DB → Fast API responses
- URL-based state → Perfect reproducibility
- Lazy accordion loading → Instant popups

**Don't fight the architecture.** Work with these patterns.

---

## File Sizes (What You're Dealing With)

```
srv/templates/globe.html     17K lines  (frontend monolith)
srv/*.go                     26K lines  (backend)
db.sqlite3                   1.8 GB     (6M fires, 500K features)
data/fire_groups_v5/         162 files  (pre-computed trajectories)
data/export/fire_narratives/ 162 files  (pre-computed narratives)
```

**Don't try to understand everything.** Use the data flow maps to find your specific path.

---

## Quick Command Reference

```bash
# Build and run
make build && ./server

# Run tests
./tests/run_all.sh

# Query database
sqlite3 db.sqlite3 "SELECT * FROM parks LIMIT 5"

# Search code
rg "functionName" --type go
rg "showParkPopup" srv/templates/globe.html

# Rebuild fire data
python3 scripts/rebuild_fire_trajectories_v5.py
python3 scripts/load_fire_groups_to_db.py --force
python3 scripts/precompute_narratives_v5.py

# Daily update (what cron runs)
python3 scripts/daily_fire_update.py --days 7
```

---

## When You're Stuck

1. **Check the docs** → `docs/QUICK_TASKS.md` has copy-paste solutions
2. **Search the code** → `rg "keyword"` finds patterns
3. **Trace the data flow** → `docs/DATA_FLOW.md` shows the path
4. **Check architecture decisions** → `docs/ARCHITECTURE_DECISIONS.md` explains why
5. **Build mental model** → `docs/MENTAL_MODEL.md` provides context

**Don't thrash.** Use the maps. Follow the patterns.

---

## Documentation Index

### For Agents (You)
- **THIS FILE** - 2-minute orientation
- `AGENTS.md` - Comprehensive reference (tables, APIs, credentials)
- `docs/DATA_FLOW.md` - Visual data flow maps
- `docs/QUICK_TASKS.md` - Copy-paste solutions
- `docs/MENTAL_MODEL.md` - Understanding the system
- `docs/ARCHITECTURE_DECISIONS.md` - Why things are built this way

### For Features
- `docs/FIRE_PIPELINE.md` - Fire data pipeline
- `docs/FIRE_DATA_FLOW.md` - Fire system specifics
- `docs/API.md` - API reference
- `docs/DATABASE.md` - Database schema
- `docs/SCRIPTS.md` - Data processing scripts

### For Humans
- `docs/README.md` - Project overview
- `docs/INSTALL.md` - Setup guide
- `docs/SHELLEY_PROMPT_UI.md` - UI development guide
- `docs/SHELLEY_PROMPT_ADMIN_UI.md` - Admin panel guide

---

## Final Checklist

Before you start coding:

- [ ] Read the relevant doc(s) from the index above
- [ ] Understand which files you need to modify
- [ ] Check `docs/QUICK_TASKS.md` for a copy-paste solution
- [ ] Understand the data flow for your feature
- [ ] Know how to test your changes
- [ ] Understand the gotchas (database safety, memory leaks, state sync)

**Good luck!**
