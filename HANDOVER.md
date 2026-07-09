# HANDOVER — Park popup export overhaul, round 2 (KML tooltip param + LOCUS export)

Previous round completed **Task 3 (MBTiles limits)** in commit `fb8da77`. Remaining: **Task 1 (tooltip KML minus patrol effort)** and **Task 2 (LOCUS backup export)**. Read `AGENTS.md` first. Build/deploy: `make build && sudo systemctl restart 5mp`. Uncommitted `data/` changes are from background workers — never commit/revert them.

## Task 1 — Tooltip KML = star-report KML minus patrol effort (small, do first)

`HandleAPIParkKML` in `srv/api.go` (~line 4218) is already restructured (commit `ed2a42b`) to the clean folder format. Only remaining work:

1. Add query param `?effort=0` that skips the "Patrol Effort (30km buffer)" block. The block is `srv/api.go` ~lines 4671–4803 (`patrolPlacemarks` build + folder emit). Simplest: wrap in `if r.URL.Query().Get("effort") != "0" { ... }`.
2. Set `&effort=0` on the tooltip KML link: `srv/templates/globe.html` line ~7169 (`.pa-export-btn` anchor inside the park tooltip HTML).
3. Star report keeps effort: `exportFullReportKmlV2` in globe.html ~4406 fetches the same endpoint — leave it without the param (default = effort on).
4. Verify: `curl "http://localhost:8000/api/parks/CAF_Chinko/export.kml?pwd=test2026&effort=0" | grep -c "Patrol Effort"` → 0; without param → 1. Diff folder names between both to confirm otherwise identical.

## Task 2 — LOCUS button (Locus Map backup zip)

New button next to KML/MBTiles in tooltip header (globe.html ~7169), endpoint e.g. `GET /api/parks/{id}/export.locus`, producing a Locus backup zip restorable via Locus Map → Backup → Restore. Use `runExport` busy-button pattern from ed2a42b for the frontend.

### Ground-truth samples (re-extract from /tmp/shelley-uploads/*.zip if /tmp/locus_ex/ is gone)

| Dir in /tmp/locus_ex/ | App | Notes |
|---|---|---|
| `2026-07-09_10-29-58` | **Latest Locus 4 (2026) — PRIMARY REFERENCE, real field device** | No myLibrary.db, no .backup_info! Folders in `groups` table. 1000 tracks / 81k locations / 1986 waypoints. Has `config.cfg` + preset `BOMA_FIELD_DEVICE_14-MAY-2025.lb` |
| `2025-10-23_12-41-34_1b396716adc25c9d` | Locus 4.31.3 | Has myLibrary.db `folder` table + `.backup_info` JSON |
| `2019-07-11_17-14-08_73264581576e2767` | Locus 3 | Richest category structure (boundaries/rivers/roads/forest loss/missions) — use as content model |
| `LOCUS basemap_757014e69b347933` | Locus 3 | |

### Key format findings (verified)

**Target the 2026 sample's layout** (it's the actual field device):
```
data/database/tracks.db, waypoints.db, .mVisibleItems_dbTracks, .mVisibleItems_dbWaypoints
data/config/*.lb (tiny stubs, 4–8 bytes, copy from sample)   data/presets/default.lb
_various/settings   config.cfg   config_projections.cfg
```
CAUTION: minimal viable zip is probably just `data/database/*` — test whether Locus restores without `_various/settings` etc. before copying a 75KB settings blob from another device (it contains device-specific paths). Ask the user to test-restore on a device.

**tracks.db schema (both L4 samples identical):**
- `tracks(_id, parent_id→groups._id, rw_mode, name, time_created, time_updated, activity_type, statistics BLOB, extra_data BLOB, extra_style BLOB, use_category_style INT, trackpoints, breaks, store_item_id, store_version_id, privacy TEXT, uuid BLOB, overview_image)` — 2026 sample: `privacy='PRIVATE'`, uuid = 16 random bytes, statistics 177-byte blob, extra_style 140-byte, use_category_style=1, trackpoints NULL.
- `locations(_id, provider, longitude, latitude, time, elevation, speed, bearing, accuracy, parent_id→tracks._id, previous_id→prev location _id, sensor_* NULL)` — one row per vertex, time=0 ok, elevation optional. Indexes: `locations_parent_id`, `locations_previous_id`, `tracks_uuid`.
- `groups(_id, name, mode INT, icon TEXT, extra_style BLOB, parent_id INT default -1, labels_mode, time_created, time_updated, uuid BLOB)` — **this is the folder tree in the 2026 sample**: `mode=1` rows "Base"(7)/"Mission"(8) are top-level containers; `mode=0` rows (BOUNDARY, ROADS, RIVERS, CORRIDORS… icon='ic_tracks') have `parent_id`=7/8. Track.parent_id → mode-0 group id. Also keep the `tracks_category_invisible` group (id 1, parent -1). Tables `categories` (empty in 2026), `folder_group` (empty), `items_deleted`, `android_metadata` (row 'en').
- **No myLibrary.db needed** for the 2026-format target.

**waypoints.db:** `waypoints(_id, parent_id→groups._id, track_id, name, …, longitude REAL, latitude REAL, time_created, …, privacy, uuid)` + same groups structure (`waypoints_category_invisible`, POI/Base/Mission…). Simple points need parent_id/name/lon/lat/time_created/privacy/uuid. `extra_icon` like `z-ico19.png` optional.

**extra_style blob (per track, big-endian "Storable"):** `u8 1, i32 ver=2, i32 bodySize, body`. Body: `i32 len+utf8` style name (may be empty), `i32 0, i32 0`, `00 00 00 01`, `i32 0`, `i32 0x3A` line sub-block: `u8 1, u32 ARGB color, u8 0, u32 FFFFFFFF, str "DOTTED", str "SIMPLE", i32 0, f32 width, i32 0, str "PIXELS", u8 0, u32 FFFFFFFF, u8 drawFill, u32 fillARGB`. **Recommendation: byte-copy a sample blob and patch the color u32 + width f32.** Extract reference blob: `sqlite3 tracks.db "SELECT hex(extra_style) FROM tracks LIMIT 1"` on the 2026 sample. With `use_category_style=1` you may instead style via the group's `extra_style` — check groups.extra_style blobs in the 2026 sample (BOUNDARY/ROADS/RIVERS groups may carry it).

**Visibility files** `.mVisibleItems_dbTracks`/`_dbWaypoints`: flat big-endian int64 pairs `(itemId, parentId)…` — items visible on map after restore. Put MISSION content + boundary in; leave bulk basemap detail out to keep field screens tidy.

**statistics blob**: 177 bytes (L4). Mostly zero-fillable; numPoints int32 at ~byte 8–12, length float32s at 28/64/88 (L3=148B layout). Copy a sample blob and zero/patch, or test whether NULL is tolerated (try one track with NULL statistics in a test restore).

### Content plan (agreed)
- **Base** container: park boundary (green w4), rivers HydroRIVERS (blue, width by stream_order), roads HeiGIT (tan by surface), patrol-learned tracks, waterbodies, places/villages as waypoints.
- **Mission** container: recent fire trajectories (red), settlements (orange), recent deforestation (magenta), turbidity alerts (caution waypoints).
- Data sources: exactly the same queries as `HandleAPIParkKML` — read that handler top-to-bottom and mirror it (`feature_geometries`, `park_rivers`, road tables, `park_waterbodies`, `osm_places`, settlements/deforestation JSON).
- Implementation: `modernc.org/sqlite` (in go.mod) writing two temp sqlite files (or :memory:+Serialize — these DBs are small), zip via `archive/zip`. LineStrings → tracks+locations; Points → waypoints. Polygons → closed-ring tracks (Locus has no fill for tracks; acceptable).

### Open questions for the user
1. **Locus 3 devices still in field?** L4 backups don't restore into L3. Samples suggest both existed; 2026 sample is L4 — likely target L4 only, but confirm.
2. Whether an optional layer-picker sheet (like `#mbtiles-dialog`, globe.html ~17914) is wanted for KML/LOCUS — was flagged nice-to-have.

### Verification
- Parse own output with python/sqlite3; diff table schemas + zip layout against `/tmp/locus_ex/2026-07-09_10-29-58`.
- Ask user to test-restore on a real device (only true validation).
- UI screenshot at 1280×1400 with `?test=1`: tooltip shows KML | MBTiles | LOCUS.

## Notes from round 1 (context)
- MBTiles: hybrid build — in-memory preferred (disk costs extra on this VM), disk temp fallback up to 8GB only for prod-password users (`RequestEnv(r)=="prod"`); test2026 capped at RAM (~2.2GB). Zenodo upload streams with retries/backoff, no client timeout. Estimate API now returns `capacity_reason`, `build_mode`, `extended_limits`.
- MBTiles notifications are env-scoped (`env` column; read filter in `srv/notifications.go`).
- Test suite fully green as of `fb8da77` (fixed stale publications-WDPA and empty park_fire_analysis assertions). Keep it green.
