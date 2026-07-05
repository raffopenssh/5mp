# Handover: test env + stats toggles + pixel polish (2026-07-05)

Read AGENTS.md first. DB backup: db.sqlite3.bak-testenv. Ignore dirty data/* and .git-commits.txt (pre-existing, don't commit).

## Goal (user request)
1. DONE(partial): subtle "test environment" link on login page → pwd test2026 → full app, but GPX uploads/pixels isolated from prod (test2026) both directions.
2. TODO: stats-panel rows (globe.html lines ~48-72; handleStatClick ~8574) become show/hide toggles for in-view layers: pixels / fires / deforestation / settlements. Default: pixels ON, rest OFF. Must respect movement filter + bbox (currentBbox) + date range, and encode toggle state in share URL (shareCurrentView ~15301, restoreStateFromURL ~15481; suggest `layers=` param).
3. TODO: fade the 4 grid pixel layers (grid-halo/grid-glow/grid-fill/grid-cells, defined globe.html ~1787-1950) to 0 opacity between z15→z17 (wrap opacity exprs in zoom interpolate). Verify satellite toast (z>=13, showSatelliteHint ~976) still works.
4. TODO: polish pixel intensity visuals (glow/ring/fill/transparency; user likes recent-weeks look; keep green; sqrt scaling for fill, recency-driven halo, brighter core for intensity>1 are ideas).

## State: WIP commit 2b64044f — backend WRITE path done, compiles (go build ./...). NOT built/deployed/tested.

### Done
- test2026 password + `RequestEnv(r)` helper + login link (srv/auth_middleware.go).
- db/migrations/035-test-env.sql: env col on gpx_uploads/upload_queue/notifications/track_points; effort_data + subcell_visits rebuilt with env in UNIQUE. **NOT YET APPLIED** — runs on service restart.
- env threaded: HandleAsyncUpload → upload_queue → processUpload → persistUploadWithValidation → persistUpload → updateEffortData/trackSubcellVisits/track_points/notification insert. Learner queue skipped when env=="test" (upload.go ~1691). queries.sql updated + sqlc regenerated (dbgen dirty is expected).

### TODO backend
- READ filters not done: QueryGridData/GridQueryParams (srv/grid_query.go, +enrichSubcellCoverage, enrichMovementTypes), HandleAPIGrid + HandleAPIStats patrol query (~api.go 1078) + HandleAPIGridCellEffort: add `e.env=?` from RequestEnv(r). Notifications (srv/notifications.go): WHERE `(env=? OR notification_type<>'new_upload')` (only new_upload is env-scoped).
- pageData.IsTest (server.go HandleRoot) → globe.html: `window.IS_TEST_ENV` + small yellow "TEST ENV" badge after `<div id="map">`.
- New endpoint for toggles: GET /api/features-in-bbox?type=fire_trajectory|deforestation|settlement&bbox&from&to&limit(1500/max4000) on feature_geometries (bbox_minx.. cols, start_date, stat_value DESC; parse geojson col like HandleAPIParkFeatures does). Register in server.go.

### Verify (after make build && sudo systemctl restart 5mp)
- journalctl -u 5mp: migration 035 ok; `SELECT COUNT(*) FROM effort_data WHERE env='prod'` == 22415.
- Upload tiny GPX via /api/upload/async?pwd=test2026 → appears in /api/grid?pwd=test2026, NOT in ?pwd=test2026; stats/notifications scoped.
- Browser test toggles/share-URL/z17 fade/toast at ?lat=-8.2595&lng=36.6872&z=6.9&date_preset=90d (use ?test=1 TEST helpers, viewport 1280x1400).
- ./tests/run_all.sh api db (note pre-existing failures). Rebuild so footer version matches HEAD.
