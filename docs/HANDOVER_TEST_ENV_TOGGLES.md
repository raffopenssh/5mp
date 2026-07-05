# Handover: Test Environment + Stats Toggles + Pixel Rendering (2026-07-05)

User request (verbatim intent):
1. **Subtle "test" text link on the login page** → logs into full app via a test password. Test users can upload GPX; their pixels/uploads stay ONLY in test env. Test uploads must not clutter test2026; test users must not see test2026 pixels.
2. **Stats panel rows become clickable toggles** (screenshot: "Patrol Activity / Active Pixels / Total Distance / Conservation Data / Fire Activity / Deforestation / Settlements") to show/hide in-view layers: pixels, fires, settlements, deforestation. Defaults: **pixels ON, rest OFF**. Must interact consistently with the movement-type filter + bounding box ("Active Filters" panel) and **toggle state encoded in share URL** (like bbox/filters).
3. **Pixels fade out approaching z17** (grid layers), and verify the satellite-switch toast works (it triggers at zoom>=13, `showSatelliteHint()` ~line 976 globe.html).
4. **Improve pixel intensity rendering** — user likes recent-weeks look; refine combination of glow, ring, fill, transparency to show effort better.
Reference view: https://five-megapixel-conservation.exe.xyz/?lat=-8.2595&lng=36.6872&z=6.9&date_preset=90d

## State: NO code changes made yet. DB backup exists: `db.sqlite3.bak-testenv` (Jul 5 04:37).
Pre-existing dirty files (do NOT commit): data/fire_groups_v5/*, .git-commits.txt, db/migrations/033 (local tweak).

## Research findings (verified)

### Auth
- `srv/auth_middleware.go`: `loadPasswords()` → {test2026,REDACTED_PWD,REDACTED_PWD,REDACTED_PWD} (or ACCESS_PASSWORDS env). Cookie `access_pwd` or `?pwd=`. `showPasswordForm()` renders login HTML inline in this file.
- `srv/server.go` HandleRoot (line 72) renders globe.html with `pageData{Hostname,User,Version}`.

### Upload/pixel pipeline
- POST /api/upload/async → `srv/upload_async.go` HandleAsyncUpload → `upload_queue` (EnqueueUpload). `srv/upload_queue.go` processUpload (every 2s) → `persistUploadWithValidation(ctx,userID,userEmail,filename,fileHash,segments)` (srv/upload.go ~1590) → filters patrol segments → `persistUpload` (line 371): gpx_uploads (CreateGPXUpload), track_points, `updateEffortData` (line 872, UpsertEffortData → effort_data), `trackSubcellVisits` (line 1403 → subcell_visits), raw INSERT notifications type='new_upload' (~line 686), queues learner jobs per park (~1677, `queuedParks` loop).
- autofetch.go POSTs to /api/upload/async?pwd=test2026 (stays prod automatically).
- Schemas: `effort_data` UNIQUE(grid_cell_id,year,month,day,movement_type), 22415 rows. `subcell_visits` UNIQUE(grid_cell_id,subcell_id,visit_date), 42615 rows (also has legacy year/month variants in queries.sql ~line 183 vs 202). gpx_uploads 32 rows, upload_queue 33, track_points 16514.
- sqlc: config db/sqlc.yaml, queries db/queries/queries.sql, out db/dbgen. sqlc v1.31.1 installed at $(go env GOPATH)/bin/sqlc. Migrations: db/migrations/NNN-*.sql, embedded, tracked by number, latest = 034.

### Read paths to env-filter
- /api/grid → api.go HandleAPIGrid (line 566) → QueryGridData (srv/grid_query.go line 61, raw SQL on effort_data; GridQueryParams) + enrichSubcellCoverage (subcell_visits, line 232) + enrichMovementTypes (line 304).
- /api/stats → api.go HandleAPIStats (line 1015); patrol statsQuery on effort_data (~1078). Fire/deforest stats come from feature_geometries stat_value; settlements from park_settlements — those stay GLOBAL (shared in both envs).
- /api/grid/{id}/effort (HandleAPIGridCellEffort, api.go line ~421), /api/export/patrol-pixels, worldclim_intensity.go QueryGridDataWithWorldClim — filter if they touch effort_data.
- /api/notifications → srv/notifications.go HandleGetNotifications (several query variants).

### Frontend (srv/templates/globe.html, 16476 lines)
- Stats panel HTML lines 48–72: ids stat-pixels, stat-distance, stat-fires, stat-deforest, stat-settlements; handleStatClick() at ~8574 (old highlight/pulse modes — replace with toggle semantics; deactivateStatMode, highlightActivePixels, animatePixelHighlight, showDistanceHeatmap can be removed/simplified).
- Grid pixel layers defined ~lines 1787–1950: grid-halo, grid-glow, grid-fill, grid-cells (4-layer circle system; expressions gridIntensity/gridRecency/gridSubcell/gridFillMetric; comments explain semantics). Zoom-radius interpolation stops at z12.
- loadStats() line 2816 (bbox = currentBbox else map bounds if z>4; movement types via getActiveMovementTypes()).
- Satellite hint: lines 180–199 (HTML), 972–1030 (JS; zoom>=13, dark basemap only, quickSwitchToSatellite→switchBasemap('satellite-esri')).
- Share URL: shareCurrentView() line 15301 (params: pwd, lat/lng/z, basemap, date_preset|from/to, bbox, parks, country, types, q, popup, sections, pixel, pinned, pinned_features, starred_*). restoreStateFromURL() line 15481 (bbox restore ~15528, types ~15655, keystones ~15695). getPwd() line 577.
- pinnedLayers per-park feature layers exist; there is NO global "fires in view" layer yet.
- feature_geometries: bbox_minx/y/maxx/y + start/end_date + stat_value + properties_json; types fire_trajectory(173k), deforestation(221k), settlement(64k), road. HandleAPIParkFeatures shows how geojson column is parsed → reuse.

## Agreed plan

### A. Backend test env
1. Add password `test2026`; helper `RequestEnv(r) → "test"|"prod"` (pwd param or cookie == test2026).
2. Login form: subtle muted link `<a href="/?pwd=test2026">test environment</a>` (11px, #555).
3. Migration 035: add `env TEXT NOT NULL DEFAULT 'prod'` to gpx_uploads, upload_queue, notifications, track_points; REBUILD effort_data + subcell_visits (new tables) so UNIQUE includes env (needed for upserts): UNIQUE(...,movement_type,env) / UNIQUE(...,visit_date,env). Copy data with env='prod', recreate indexes. Preserve all columns.
4. Thread `env` param: HandleAsyncUpload → upload_queue.env → processUpload → persistUploadWithValidation → persistUpload → updateEffortData/trackSubcellVisits/notification insert/track_points/CreateGPXUpload. Update queries.sql (EnqueueUpload, GetPendingUploads, CreateGPXUpload, UpsertEffortData with new conflict target, UpsertSubcellVisit, track point insert) + `sqlc generate` + fix callers. Callers without request ctx use "prod".
5. **Skip learner queue when env=="test"** (don't pollute learned roads/airstrips). Optionally env-aware file_hash dedup.
6. Read filters: QueryGridData (+Env in GridQueryParams), enrichSubcellCoverage, enrichMovementTypes, HandleAPIStats patrol query, HandleAPIGridCellEffort. Notifications: WHERE `(env = ? OR notification_type <> 'new_upload')` — new_upload env-scoped, fire alerts etc global.
7. pageData.IsTest; in globe.html add `window.IS_TEST_ENV = {{if .IsTest}}true{{else}}false{{end}}` + small fixed "TEST ENV" yellow badge after `<div id="map">`.

### B. New endpoint
`GET /api/features-in-bbox?type=fire_trajectory|deforestation|settlement&bbox=&from=&to=&limit=` (default 1500, max 4000) on feature_geometries: bbox intersect + start_date in [from,to], ORDER BY stat_value DESC. Returns GeoJSON FC with feature_id, park_id, start/end_date, stat_value, merged properties_json. Register in server.go.

### C. Frontend toggles
- Repurpose handleStatClick: stat-pixels toggles visibility of the 4 grid layers; stat-fires/stat-deforest/stat-settlements toggle viewport overlay layers fed by /api/features-in-bbox (fetch on toggle-on + debounced moveend while active; respect currentBbox if set else viewport; respect dateFrom/dateTo; refetch on date/filter change). Visual "off" state class on stats rows (e.g. dimmed). Distance row: keep non-toggle (or tie to pixels).
- Defaults: pixels visible, others hidden.
- Share URL: add param e.g. `layers=pixels,fire,deforest,settlements` (encode only when != default; `layers=` empty possible for pixels-off). Parse in restoreStateFromURL.
- Styling for overlays: fires = orange lines/polys (#f97316), deforestation = purple (#a855f7), settlements = amber points (#eab308), modest opacity, below popups.

### D. Pixel rendering
- Fade all 4 grid layers out z15→z17: wrap each opacity in `['interpolate',['linear'],['zoom'], 15, <existing expr>, 17, 0]` (works: data expr per zoom stop). Also circle-stroke-opacity of grid-cells.
- Verify satellite toast still fires at z>=13 on dark basemap (browser test).
- Intensity polish (taste, keep green): e.g. fill radius uses sqrt(gridFillMetric) for perceptual scaling; ring stroke-width scales slightly with intensity; halo opacity driven more by recency; consider slightly brighter core (#86efac) for intensity>1. Keep zoom-radius scheme; extend interpolation stops beyond z12 so radii don't explode (add e.g. 14/16 stops consistent with exponential base 2 then let fade handle it).

### E. Test/verify (after `make build && sudo systemctl restart 5mp`)
- Migration: journalctl -u 5mp; `SELECT COUNT(*) FROM effort_data WHERE env='prod'` == 22415.
- Isolation: tiny GPX (3-4 pts near -8.26,36.69, today) → POST /api/upload/async?pwd=test2026; check /api/grid?pwd=test2026 shows cell, ?pwd=test2026 doesn't; notifications scoped.
- /api/stats with pwd=test2026 → 0 pixels initially.
- Browser test toggles + share URL roundtrip + z17 fade + toast at test URL above (use ?test=1 TEST helpers; resize 1280x1400).
- ./tests/run_all.sh api|db (note pre-existing failures).
- Commit in logical chunks; don't stage data/fire_groups_v5, .git-commits.txt, db.sqlite3*. Rebuild so footer version matches HEAD (AGENTS.md).

## Cautions
- effort_data/subcell_visits rebuild: ON CONFLICT target must exactly match new unique index columns.
- Migration 033 has uncommitted local edits — leave alone.
- upload_queue GetPendingUploadsRow type changes ripple via sqlc — fix compile errors.
- Server runs as systemd `5mp`; DB is live — WAL, don't hold long write locks during migration (it runs at startup, fine).
