# 5MP Codebase Patterns Reference

## 1. Admin Panel Tabs (globe.html)

### CSS Structure
- `.admin-panel` - full-screen overlay, `display:none` by default, `.active` shows it
- `.admin-panel-container` - max-width 1200px centered container
- `.admin-panel-header` - title + close button
- `.admin-panel-tabs` - flex row of tab buttons
- `.admin-tab` - individual tab button, `.active` highlights green (#22c55e)
- `.admin-tab-badge` - counter badge inside tab (e.g., pending count)
- `.admin-panel-body` - scrollable content area
- `.admin-tab-content` - content div per tab, `.active` shows it
- `.admin-table-container` > `.admin-table` - styled data tables

### Tab Button Pattern
```html
<button class="admin-tab" data-tab="TABNAME" onclick="switchAdminTab('TABNAME')">
    <svg ...icon.../>
    Tab Label
    <span class="admin-tab-badge" id="TABNAME-count">0</span>  <!-- optional -->
</button>
```

### Tab Content Pattern
```html
<div class="admin-tab-content" id="tab-TABNAME">
    <div class="admin-stats-row" id="TABNAME-stats">
        <div class="admin-stat-card">...</div>
    </div>
    <div class="admin-table-container">
        <table class="admin-table">...</table>
    </div>
</div>
```

### JavaScript Tab Switching
```js
function switchAdminTab(tabName) {
    adminCurrentTab = tabName;
    adminSelectedItems = [];
    updateBulkActionBar();
    document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.admin-tab-content').forEach(c => c.classList.remove('active'));
    const tab = document.querySelector(`.admin-tab[data-tab="${tabName}"]`);
    const content = document.getElementById(`tab-${tabName}`);
    if (tab) tab.classList.add('active');
    if (content) content.classList.add('active');
    // Load data for tab
    if (tabName === 'uploads') loadUploadLogs();
    else if (tabName === 'pending') loadPendingApprovals();
    else if (tabName === 'learning') loadLearningResults();
    else if (tabName === 'features') loadLearnedFeatures();
}
```

### Existing Tabs:
1. `uploads` - Upload Logs (default active)
2. `pending` - Pending Approvals (with badge counter)
3. `learning` - Learning Results
4. `features` - Learned Features
5. `map-settings` - Map Settings

---

## 2. Upload/Async Endpoint Pattern

### Synchronous Upload (POST /upload, POST /api/upload)
- `srv/upload.go` - HandleUpload processes GPX immediately
- Returns UploadResponse with segments, validation, distances

### Async Upload (POST /api/upload/async)
- `srv/upload_async.go` - HandleAsyncUpload
- Reads file, calculates SHA256 hash, checks for duplicates
- Queues to `upload_queue` table via `q.QueueUpload()`
- Returns HTTP 202 with `{queue_id, status: "pending", message}`
- Status check: GET /api/upload/status/{id}

### Upload Queue Processor (Background)
- `srv/upload_queue.go` - UploadQueueProcessor
- Started in `server.go` New(): `srv.UploadQueueProcessor = NewUploadQueueProcessor(srv.DB, srv); srv.UploadQueueProcessor.Start()`
- Polls every 2 seconds via ticker
- Processes up to 5 pending items per batch
- Calls `p.server.persistUploadWithValidation()` for actual processing
- Updates queue status: pending → processing → completed/failed

---

## 3. Database Tables / Migrations

### Migration System
- Files in `db/migrations/NNN-name.sql` (e.g., `020-upload-queue.sql`)
- Numbered 001-021+
- Tracked in `migrations` table with `migration_number`
- Each migration ends with: `INSERT OR IGNORE INTO migrations (migration_number, migration_name) VALUES (NNN, 'NNN-name');`
- Auto-run on server start via `db.RunMigrations(wdb)` in `server.go:setUpDatabase()`
- Embedded via `//go:embed migrations/*.sql`

### sqlc Code Generation
- Config: `db/sqlc.yaml`
- Queries: `db/queries/*.sql` (gpx_logs.sql, queries.sql, publications.sql, notifications.sql, gpx_learning.sql)
- Generated Go: `db/dbgen/` package
- Usage: `q := dbgen.New(s.DB)` then `q.MethodName(ctx, params)`

### Migration File Pattern
```sql
-- Description of what this migration does
CREATE TABLE IF NOT EXISTS table_name (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ...
);
CREATE INDEX IF NOT EXISTS idx_table_col ON table_name(col);
-- Record execution
INSERT OR IGNORE INTO migrations (migration_number, migration_name)
VALUES (NNN, 'NNN-description');
```

---

## 4. Background Workers

### Pattern A: Struct-based worker (started in New())
Used by GPXLearner and UploadQueueProcessor.
```go
type MyWorker struct {
    db       *sql.DB
    server   *Server  // optional
    stopChan chan struct{}
    wg       sync.WaitGroup  // or just mu+running
    running  bool
    mu       sync.Mutex
}

func NewMyWorker(db *sql.DB) *MyWorker {
    return &MyWorker{db: db, stopChan: make(chan struct{})}
}

func (w *MyWorker) Start() {
    w.mu.Lock()
    if w.running { w.mu.Unlock(); return }
    w.running = true
    w.mu.Unlock()
    w.wg.Add(1)
    go w.processLoop()
    slog.Info("worker started")
}

func (w *MyWorker) processLoop() {
    defer w.wg.Done()
    ticker := time.NewTicker(N * time.Second)
    defer ticker.Stop()
    for {
        select {
        case <-w.stopChan: return
        case <-ticker.C: w.processNextBatch()
        }
    }
}
```
Started in `server.go New()`:
```go
srv.GPXLearner = NewGPXLearner(srv.DB)
srv.GPXLearner.Start()
srv.UploadQueueProcessor = NewUploadQueueProcessor(srv.DB, srv)
srv.UploadQueueProcessor.Start()
```

### Pattern B: Context-based goroutine (started in main.go)
Used by ResearchWorker and NarrativeCacheWorker.
```go
// In main.go:
ctx := context.Background()
go server.StartResearchWorker(ctx)
go server.StartNarrativeCacheWorker(ctx)
```

---

## 5. Environment Variables / Config

### No .env file - hardcoded config
- Passwords: hardcoded in `srv/auth_middleware.go`: `validPasswords = []string{"test2026", "REDACTED_PWD", "REDACTED_PWD"}`
- DB path: hardcoded in `main.go`: `srv.New("db.sqlite3", hostname)`
- Listen addr: CLI flag `-listen` (default `:8000`)
- Data dir: CLI flag `-data` (default `data`)
- Version: set via ldflags `var Version = "dev"`

### No os.Getenv calls in srv/*.go or main.go

---

## 6. Route Registration

All routes registered in `server.go Serve()` method using Go 1.22+ pattern matching:
```go
mux.HandleFunc("METHOD /path/{param}", s.HandlerFunc)
mux.HandleFunc("GET /admin", s.RequireAdmin(s.HandleAdminPage))
```

Admin-only routes wrapped with `s.RequireAdmin()`.

### API Pattern Groups:
- `/api/admin/*` - Admin APIs (some use RequireAdmin, some use password auth for cron)
- `/api/parks/{id}/*` - Park-specific data
- `/api/upload/*` - Upload endpoints
- `/api/*` - General APIs

---

## 7. Server Struct

```go
type Server struct {
    DB           *sql.DB
    Hostname     string
    TemplatesDir string
    StaticDir    string
    AreaStore    *areas.AreaStore
    WDPAIndex    *areas.WDPAIndex
    Auth         *auth.Manager
    LegalStore   *LegalStore
    GADMStore    *GADMStore
    GPXLearner   *GPXLearner
    UploadQueueProcessor *UploadQueueProcessor
}
```
New workers should be added as fields on Server struct and started in New().
