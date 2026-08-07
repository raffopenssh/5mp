# AOI — handover, rev 3 (2026-08-07)

`docs/PLAN_AOI_OVERLAY.md` is the design rationale and the record of *measured*
facts — read its §1, §2 and §4 before touching ingest. This file is only "what
is done, what is left, how to check".

**One sentence**: an AOI is a *power bounding box* — an arbitrary polygon that
is kept, owned, versioned, and has data fetched **for it** over days, as
opposed to the "Select Area" bbox which is an instant disposable filter over
data we already hold.

---

## 1. State

`XSA_Study_Area` (485,150 km², owner `$AOI_OWNER_PWD`) is the only AOI. Read
path, write path, queue, exports, animation and the star report are all live.

```bash
python3 scripts/aoi_runner.py --status
```

Every dataset has a runner and every one but `hansen` is `done` for XSA:
3.18M detections → 38,725 trajectories, 2.23M GFW alerts → 696 events,
74,904 built-up polygons → 1,552 settlements, 3,169 waterbodies, 11,370 rivers
+ 2,530 lakes, 3 country PBFs. `basin` returns **0 rows and that is correct** —
a 485,000 km² polygon has no single watershed.

`hansen` (Hansen loss 2001–2023, 20 windows × ~50 s) was left running at the
end of rev 3's session. If `--status` shows it `pending` with an `error:`
detail, read the log before assuming a code bug — it died once on a missing
column, now fixed by migration 043.

**Never run two units at once.** SQLite has one writer and the v5 chain holds
it for minutes; that stranded three leases on 2026-08-07. Dead-pid leases
self-heal (`--heal`, and at the start of every run).
`python3 scripts/test_aoi_resume.py` proves interruption is a normal exit — run
it after any change to lease/cursor code.

### Verification set — re-run after any AOI work

```bash
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM feature_geometries
  WHERE park_id='CAF_Chinko' AND feature_type='fire_trajectory'"   # 8753
curl -s "localhost:8000/api/aois?pwd=test2026"    | jq '.count'    # 0
curl -s "localhost:8000/api/aois?pwd=$AOI_OWNER_PWD" | jq '.count'    # 1
curl -s "localhost:8000/api/parks/XSA_Study_Area/stats?pwd=$AOI_OWNER_PWD" \
  -o /dev/null -w '%{http_code}\n'                                 # 404 (park route must 404)
curl -s "localhost:8000/api/notifications?type=aoi_progress&pwd=test2026" \
  | jq '.notifications|length'                                     # 0  <- privacy
# the AOI is visible to its own animation and to nobody else's
curl -s "localhost:8000/api/features-in-bbox?type=fire_trajectory&bbox=22.7,4.25,31.3,11&limit=50&pwd=$AOI_OWNER_PWD&aoi=XSA_Study_Area" \
  | jq '[.features[].properties.park_id]|unique'                   # ["XSA_Study_Area"]
curl -s ".../fire-anim-trajectories?...&pwd=test2026&aoi=XSA_Study_Area" \
  | jq '[.groups[].park]|unique'                                   # no XSA (param ignored)
./tests/run_all.sh                                                 # db 37, api 45, ui 20
```

`go test ./srv/` fails on `TestServerSetupAndHandlers`
(`035-test-env.sql: no such column: avg_speed_kmh`). **Pre-existing, unrelated
to AOI** — verified by stashing. Do not chase it as an AOI regression.

⚠️ **Stale `aoi_progress` rows**: rev 3 fixed the runner to key them by AOI id,
but rows written before that are `park_id='SYSTEM'` and therefore leak the
AOI's name to every principal. The migration UPDATE was blocked by the running
Hansen unit — **run it**:

```sql
UPDATE notifications SET park_id='XSA_Study_Area',
       message=replace(message,'XSA_Study_Area/','')
 WHERE notification_type='aoi_progress' AND park_id='SYSTEM';
```

---

## 2. The API surface, and the shape of the rule

`/api/aois/{id}/*` = the park handlers wrapped in `aoiGate()`. One visibility
check covers them all. `/api/parks/{aoi}` **404s** (`ParkIDMiddleware` +
`IsAOIID`), so getting the prefix wrong is a hard failure, not a leak. In the
frontend, **always build these URLs with `apiBase(id)`**.

```
GET    /api/aois                       list (+ can_create)
GET    /api/aois/search?q=              incl. archived; the only way back to one
GET    /api/aois/{id}                   metadata + per-dataset coverage
GET    /api/aois/{id}/versions          the lineage
GET    /api/aois/{id}/progress          live, no-store
GET    /api/aois/{id}/{fire-narrative,fire-trend,fire-realtime,features,
        deforestation-narrative,settlement-narrative,feature-stats,
        classified-settlements,classified-deforestation,
        settlement-intensity,infrastructure,basin}
GET    /api/aois/{id}/export.{geojson,kml,locus}
POST   /api/aois/{id}/mbtiles           + GET .../mbtiles/estimate
POST   /api/aois/estimate               side-effect free; call it while dragging
POST   /api/aois                        create + seed queue (runs nothing)
POST   /api/aois/{id}/{edit,restore,refresh,kick}
DELETE /api/aois/{id}
```

**Deliberately absent, and this is a decision not a gap**: `stats`, `species`,
`climate`, `publications`, `legal`, `checklist`, `turbidity`. They are
per-protected-area facts; averaging them over 485,000 km² would invent a
number. The popup and the report both say so and point at the intersecting
parks. `fetchParkReportData()` skips them for an AOI rather than eating 404s.

Load-bearing details, each already got wrong once:

* **No endpoint runs the ingest.** `scripts/aoi_runner.py` owns the lease
  discipline; `kick` shells out to it rather than reimplementing it.
* **`DELETE` order**: the `aois` row goes *last*, because while it exists
  `aoiExcludeSQL()` still masks derived rows not yet deleted.
* Non-owners get **404, not 403** (`requireAOIOwner`) — an id must not be an
  oracle. Same reason `?aoi=` on a raw-geography endpoint is *ignored* when
  invisible rather than refused.
* `validateAOIGeom` caps at 2,000 vertices (re-parsed by every runner, traced
  by the animator's canvas clip every frame).
* **Any query over park-shaped storage without an explicit `park_id` must
  apply `aoiExcludeSQL(col)`** — for privacy *and* to stop the AOI
  double-counting the parks it overlaps. Same for notifications:
  `aoiNotifSQLFilter(col, principal)`.
* **…unless it is showing the AOI, in which case `aoiScopeSQL(col, id)`.**
  Rev 3: `?aoi=<id>` on `/api/features-in-bbox` and
  `/api/fire-anim-trajectories` serves that AOI's rows and **only** those.
  Exclusive on purpose — the AOI's chain covers the whole polygon *including*
  the ~11% inside parks, so keeping park rows too paints the overlap twice.
  `aoiScopeParam(r)` re-checks visibility through `GetAOI`; it never trusts the
  parameter. Endpoints that **sum** (dashboard totals, `/api/stats`) must keep
  using `aoiExcludeSQL` — this one paints pixels.
* **`resolveAreaGeom(id)`** for a name + boundary, **`resolveAreaBBox(id)`**
  for a name + bbox (`srv/aoi.go`). An AOI is never in `AreaStore` (that would
  let `park_assigner` reassign detections away from the four parks it
  overlaps), so the old `for _, pa := range s.AreaStore.Areas` loop silently
  yielded an empty boundary — KML lost its patrol effort, MBTiles 404'd. Any
  new geography handler uses these.

---

## 3. Frontend

| piece | where |
|---|---|
| routing | `apiBase(id)` in globe.html; `window.AOI_IDS` filled by `loadAOIs()` |
| map layer + popup | globe.html `loadAOIs`/`showAOIPopup`/`aoiCoverageHTML` |
| exports | `aoiExportButtonsHTML(id, name)` — popup header **and** the sticky map tip's `.maptip-exports` row |
| filter section | globe.html `#aoi-section` — own heading, amber, own visibility toggle |
| tags | `updateAOITagsDisplay()` (amber, carries the window) + `updateStarredAreaTags()` (blue) |
| editor | `srv/static/aoi_draw.js` — `AOIDraw.start()` / `.startEdit(id, name, geom)` |
| progress card | `srv/static/aoi_progress.js` — `AOIProgress.cardHTML(notif)` |
| animation | `Animator.open({aoi})` clips to the **polygon**; loaders append `&aoi=` |
| report | `collectReportParks()` folds in every visible AOI, first, unstarred |

Things that will bite:

* **`window.map` is the `<div id="map">` ELEMENT**, not the MapLibre instance
  (named access on window). Use the bare lexical `map` binding, as
  `aoi_draw.js` `theMap()` and anim.js do. Symptom: `m.getSource is not a
  function`.
* **The analysis window is the time slider.** There must not be a second date
  picker that can disagree with it. `5mp:date-window-changed` (debounced, from
  both `applyPreciseDateFilter` paths) re-prices the editor live. Anything
  wanting a specific window goes through `setTimeSliderRange()`.
* `can_create` comes from the server. A password can arrive as a **cookie**,
  so the frontend's `getPwd()` cannot decide whether `POST /api/aois` 403s.
* Share links use `?aoi=` / `aoi_sections=` / `anim_aoi`, deliberately separate
  from `?popup=`/`sections=`, which resolve against the `areas` source an AOI
  is never in. `getReportGeom(id)` exists for the same reason (report map,
  patrol-effort bbox).
* The progress card is **not client state** — it is a `notifications` row, so
  it survives a laptop shut for a week. Polling is adaptive and *stops* at
  `ready`.

**Versioning**: an edit forks. Version N+1 is created, N is archived
(`state='archived'`, hidden from `ListAOIs`, still readable by id, queue
disabled). Versions are labelled by their **analysis window**, not by number.
An edit that changes nothing returns `unchanged: true`. Why: an AOI is a
question plus the days of ingest that answered it; mutating either in place
leaves its derived rows as answers to a question nobody asked, with no way to
tell, because the id did not change.

---

## 4. What is left, in priority order

### 4.1 Finish `hansen` for XSA

The only incomplete dataset. `~50 s` per 2° window × 20, resumable via
`start_window`. It is the **fifth writer** of `(park_id=<aoi>, feature_type)`
and safe only because it owns the disjoint `deforest_hansen_` prefix.

```bash
python3 scripts/aoi_runner.py --aoi XSA_Study_Area --dataset hansen --minutes 90
```

`seed_datasets()` is `INSERT OR IGNORE`, so an existing AOI picks up newly
added datasets by re-seeding.

### 4.2 Clear the stale `SYSTEM`-keyed `aoi_progress` rows

The SQL is in §1. One statement; it was blocked by the writer lock.

### 4.3 Verify the editor by hand

The draw flow has only been exercised programmatically; simulated
`map.fire('click')` did not produce the live estimate, which is probably the
simulation and not the code, but **click through it in a real browser**: place
3+ corners, watch the estimate appear, drag a vertex, move the time slider,
save, then Edit → check it forks and the old version shows as archived under
Versions.

### 4.4 Verify the shipped rev-3 UI by hand

Server-side is tested (see §1). Not yet clicked in a browser: the map tip's
export row on touch, the MBTiles dialog opened from an AOI (it now routes
through `apiBase`), and a full star report containing the AOI + its four parks
(PDF/CSV/XLSX/KML all inherit `collectReportParks`).

### 4.5 Admin "Access" tab

Principals, AOI ownership, per-dataset enable/disable, the `kick` button, and
what `--status` prints.

### 4.6 Pins keyed by bare id

`loadPinnedLayer()` routes via `apiBase()` so pinning an AOI layer works, but
pins are keyed by bare id — an AOI and a park of the same name would share one.
Not reachable today (ids are disjoint). Key them `aoi:<id>:<type>`.

---

## 5. Do not re-litigate

* AOI rows in bbox-keyed endpoints **by default** — no, `aoiExcludeSQL()`.
  With an explicit, visibility-checked `?aoi=` — yes, `aoiScopeSQL()`, and
  exclusively.
* An AOI in `keystones_with_boundaries.json` — **never**; `park_assigner` would
  reassign detections away from the parks it overlaps.
* Starring an AOI to get it into the report — no. Visibility is the trigger;
  requiring a star gave a user with one AOI and no stars an empty report.
* FIRMS product from the date — no, `firms_api.pick_source()` reads
  `/api/data_availability`. NOAA21 has no SP product; the wrong side of a
  cutover returns HTTP 200 with a header-only CSV.
* The 10 m GHSL product — not published as tiles. The grid is **1-indexed**.
* Deforestation from Hansen for ≤2023 — yes. Cutover: Hansen ≤2023
  (`deforest_hansen_`), GFW alerts ≥2024 (`deforest_gfw_`).
  `HANSEN_MAX_YEAR = 2023` deliberately stops short of Hansen's own 2024 band.
* HydroSHEDS fetched unattended — impossible, `data.hydrosheds.org` 403s every
  request behind Cloudflare. `scripts/osm_hydro.py` ships instead (negated OSM
  ids, tag-derived `stream_order` band). It is **not** the `basin` unit:
  mghydro/MERIT answers "what drains through here" and carries no names.
* Visibility-filtering `/api/fire-frames`, `/api/grid` — no. They serve raw
  geography that was always public within the app. **The polygon is the
  secret, not the pixels.**
* Species/climate/legal/stats for an AOI — no. Averaging a park-level fact over
  485,000 km² invents a number.

---

## 6. Files

| file | role |
|---|---|
| `scripts/aoi_runner.py` | the queue: leases, cursors, interruption |
| `scripts/test_aoi_resume.py` | proves resumability — run after lease changes |
| `scripts/aoi_lib.py` | connect, principal_ref, upsert, `DEFAULT_DATASETS` |
| `scripts/aoi_clip.py` | Phase A preview; `DELETE_EXCLUDE`, `SUPERSEDED_BY` |
| `scripts/hansen_loss.py` | streamed Hansen loss (<=2023), no download |
| `scripts/gsw_water.py` | streamed JRC surface water -> `park_waterbodies` |
| `scripts/osm_hydro.py` | rivers & lakes from a country PBF (HydroSHEDS is 403) |
| `scripts/ghsl_tiles.py` | GHSL tiles, cached by tile id, 1-indexed grid |
| `srv/aoi.go` | read path, visibility, `aoiExcludeSQL`/`aoiScopeSQL`, `resolveAreaGeom`/`resolveAreaBBox` |
| `srv/aoi_write.go` | create/refresh/delete/progress/kick |
| `srv/aoi_versions.go` | edit-as-fork, restore, search |
| `srv/aoi_estimate.go` | measured cost model (test pins 252 GFW / 570 FIRMS / 4 GHSL) |
| `srv/static/aoi_draw.js` | polygon editor + live estimate; `startEdit` forks |
| `srv/static/aoi_progress.js` | the multi-day notification card |
| `db/migrations/042-aoi-versions.sql` | applied 2026-08-06 |
| `db/migrations/043-deforestation-pixel-count.sql` | applied 2026-08-07 |
| `docs/PLAN_AOI_OVERLAY.md` | design rationale + measured facts |
