# AOI — what is done, what is left

Crisp handover, written 2026-08-07. Supersedes the "Resume here" sections of
`docs/PLAN_AOI_OVERLAY.md` (that file stays as the design rationale and the
measured-facts record; read its §1, §2 and §4 before touching anything).

**Rule of thumb**: the read path and the ingest queue are done and running.
What is left is the *write* path — the UI to draw, price, edit and version an
AOI — plus two ingest units. Roughly half of the write path landed this
session but is **not yet wired into globe.html**, so it is invisible in the
browser. That wiring is the first task.

---

## 1. State of the world

`XSA_Study_Area` (485,150 km², owner `$AOI_OWNER_PWD`) is the only AOI.

```bash
python3 scripts/aoi_runner.py --status
```

| unit | state | note |
|---|---|---|
| `clip` | done | preview from 4 overlapping parks |
| `fire_gap` | done | 3,182,542 detections, 34/34 windows |
| `gfw` | done | 2,225,511 alerts, 252/252 tiles |
| `fire_v5` | **stale lease** | step 2/4 finished (38,725 groups written to `data/fire_groups_v5/XSA_Study_Area.json`, 48 MB) then the process died on `database is locked`. Its pid is gone; the next runner call heals it and resumes at step 3. |
| `deforestation` | running | pid 211408, still alive at handover |
| `ghsl` | **stale lease** | pid gone, never completed a tile |
| `osm`, `basin` | pending | |
| `gsw`, `hydro` | pending | no runner — see §4 |

The three stale leases self-heal now (§2). Just run:

```bash
python3 scripts/aoi_runner.py --aoi XSA_Study_Area --minutes 120
```

**Do not run two units concurrently.** That is what caused all of this: SQLite
has one writer, the v5 chain holds it for minutes, and the other units'
bookkeeping writes lost the race. One unit at a time, or use `--daily`.

### Verification set (re-check after any AOI work)

```bash
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM feature_geometries
  WHERE park_id='CAF_Chinko' AND feature_type='fire_trajectory'"   # must be 8753
curl -s "localhost:8000/api/aois?pwd=test2026"        | jq '.count'   # 0
curl -s "localhost:8000/api/aois?pwd=$AOI_OWNER_PWD"     | jq '.count'   # 1
curl -s "localhost:8000/api/parks/XSA_Study_Area/stats?pwd=$AOI_OWNER_PWD" -o /dev/null -w '%{http_code}\n'  # 404
./tests/run_all.sh
```

---

## 2. Landed this session

### a. The runner survives interruption (commit `ba50ee4`) — done, tested

Three things turned a pause into lost time. All three are fixed in
`scripts/aoi_runner.py`:

1. **A signal looked like a crash.** Every exception parked the dataset for 24 h
   via `retry_hours`, so a Ctrl-C cost a day. `Interrupted` is now its own
   exception → `pending`, no cooldown. Runners poll `stopping()` between units
   so they finish the unit in hand; `sh()` forwards the signal to a shelled-out
   v5 step; a second signal hard-exits.
2. **A killed process wedged the queue for 6 h.** `lease_owner` is
   `hostname:pid`, so a dead pid on this host means a dead unit —
   `heal_leases()` reclaims it at the start of every run, in `--status`
   (non-blocking) and via a new `--heal` flag.
3. **Bookkeeping writes lost the SQLite write lock.** The old 8-try ladder tops
   out near 4 minutes; the v5 chain holds the lock longer. `claim`/`progress`/
   `release` now wait up to 45 min (`BOOKKEEPING_WAIT_S`). Transient errors
   (locked, timeout, 5xx) retry in **1 h**, not 24.

`python3 scripts/test_aoi_resume.py` proves it end to end against a temp DB
with a fake slow runner: SIGTERM mid-dataset, resume without redoing units,
`kill -9` between `progress()` and `release()`, and a deadline stop. **Run it
after any change to the lease/cursor code.**

### b. Cost estimation — done, tested, **not wired to any UI**

`srv/aoi_estimate.go` + `srv/aoi_estimate_test.go`.

`estimateAOI(bbox, areaKm2, windowDays, countries)` prices a polygon from
**measured** rates (`logs/aoi.log`, the XSA run): 3.8 s/FIRMS window,
4.8 s/GFW tile, 240 s/GHSL tile, 30 min for the v5 chain at XSA's area.

The test asserts it reproduces the real ingest exactly: **252 GFW tiles, 570
FIRMS windows, 4 GHSL tiles**. If you change the rates, keep that test green.

It reports **both** wall-clock days and machine hours, and leads with days —
the runner takes one 90-minute slice per day, so a big AOI is days even though
it is only hours of work. Blocked datasets are priced at zero *and labelled*,
never hidden.

### c. Write endpoints — done, **not wired to any UI**

`srv/aoi_write.go`, routes in `srv/server.go`:

```
POST   /api/aois/estimate      side-effect free; call it while the user drags
POST   /api/aois               create + seed queue (does NOT run anything)
GET    /api/aois/{id}/progress live progress, no-store
POST   /api/aois/{id}/refresh  requeue (?dataset= for one)
POST   /api/aois/{id}/kick     shell out to the runner, admin use
DELETE /api/aois/{id}          owner only; deletes every derived row
```

Design points that are load-bearing:

* **No endpoint runs the ingest.** `scripts/aoi_runner.py` owns the lease
  discipline and is the only thing that works a unit. A second, unsupervised
  writer racing it is exactly what stranded the leases above. `kick` shells out
  to the same script rather than reimplementing it.
* **`DELETE` order matters**: the `aois` row goes *last*, because while it
  exists `aoiExcludeSQL()` still masks any derived row not yet deleted.
  Deleting it first would expose the remainder for the length of the
  transaction.
* Non-owners get **404, not 403** (`requireAOIOwner`) — an id must not be an
  oracle.
* `validateAOIGeom` caps at 2,000 vertices: the polygon is re-parsed by every
  runner and traced by the animator's canvas clip every frame.
* `ringsAreaKm2` is spherical-excess shoelace, agreeing with `aoi_lib`'s
  pyproj area. Holes subtract.
* `defaultAOIDatasets` duplicates `aoi_lib.DEFAULT_DATASETS`;
  `TestAOIDatasetsMatchPython` parses the Python and asserts they agree.

### d. Versioning: an edit forks, never mutates — done, **needs a migration run + UI**

`db/migrations/042-aoi-versions.sql` (+`lineage_id`, `version`,
`superseded_by`, `archived_at`), `srv/aoi_versions.go`:

```
GET  /api/aois/{id}/versions   the lineage
POST /api/aois/{id}/edit       fork: new version, archive the old head
POST /api/aois/{id}/restore    make an archived version current again
GET  /api/aois/search?q=       how archived versions come back; visibility-scoped
```

**Why forking rather than editing**: an AOI is a question ("what happened in
this polygon, between these dates") plus the days of ingest that answered it.
Its derived rows are keyed by its id and were computed for exactly that polygon
and window. Mutating either in place leaves them as answers to a question
nobody asked, with no way to tell — the id did not change. Deleting and
recomputing throws away days of work and breaks every share link. So: version
N+1 is created, version N is archived (`state='archived'`, hidden from
`ListAOIs` by `aoiActiveSQL`, still fully readable by id), and its queue is
disabled so the runner stops spending quota on the superseded question.

Versions are labelled by their **analysis window**, not by number
(`aoiVersionLabel`): a user remembers "the 2024 one", not "v3". The number is a
tiebreaker for geometry-only edits.

An edit that changes nothing returns `unchanged: true` rather than minting a
version.

**Migration 042 has not been applied** — it runs automatically on next server
start (`RunMigrations`). Back up first: `cp db.sqlite3 db.sqlite3.bak`.

### e. Frontend files — written, **not referenced by globe.html yet**

* `srv/static/aoi_draw.js` — `window.AOIDraw.start()`. Click to place vertices,
  drag to adjust, click the amber first vertex to close. Live estimate on every
  edit (300 ms debounce). Renders into its own `aoi-draft` source so a draft can
  never be confused with a saved AOI.
* `srv/static/aoi_progress.js` — `window.AOIProgress.cardHTML(notif)` renders
  the live notification card; adaptive polling (8 s running / 60 s partial /
  5 min queued / **stop** when ready), pauses on `visibilitychange`. The card is
  not client state: it is a `notifications` row (`type='aoi_progress'`,
  `park_id=<aoi id>`) written by `notifyAOIQueued`, so it survives reloads and
  a laptop shut for a week.
* `globe.html` gained `notifyDateWindowChanged()`, a debounced
  `5mp:date-window-changed` event fired from both `applyPreciseDateFilter`
  paths. **The AOI's analysis window is the time slider** — there must not be a
  second date picker that can disagree with it. The editor listens and
  re-prices live.

### f. Hansen for pre-2024 deforestation — `scripts/hansen_loss.py` written, **no runner yet**

The plan doc said Hansen was "tens of GB of tiles" and chose GFW alerts alone.
**Measured 2026-08-07: that was wrong.** A lossyear tile is 45–116 MB, and it
never needs downloading — they are public COGs on GCS and rasterio reads them
through `/vsicurl` with range requests. A **2°×2° window took 0.6 s**. There is
no API and no quota, so unlike the alerts it cannot silently return empty
because of a rate limit.

And the cost of not using it is concrete: GFW integrated alerts start in
**2024** (`MIN_GFW_YEAR`), so the AOI had *no deforestation history at all*
before then, while every park it overlaps has 2001–2024 Hansen polygons
(221,277 rows). The comparability argument points the other way.

Cutover, matching the parks' exactly so the numbers stay comparable:

| years | source | feature_id prefix |
|---|---|---|
| 2001–2023 | Hansen GFC-2024-v1.12 | `deforest_hansen_{id}_{year}_{n}` |
| 2024+ | GFW integrated alerts | `deforest_gfw_{id}_{year}_{lat}_{lon}` |

Hansen GFC-2024 *has* a 2024 band; `HANSEN_MAX_YEAR = 2023` deliberately stops
short so the two never double count.

Verified working:
```bash
python3 scripts/hansen_loss.py --park CAF_Chinko --dry-run
```
XSA is 20 windows across 4 tiles (`tiles_for_bbox` / `windows_for_bbox`).

⚠️ **This is the FIFTH writer of `(park_id=<aoi>, feature_type)`** and is only
safe because it owns the disjoint `deforest_hansen_` prefix. It must never
touch `deforest_gfw_%`, plain `deforest_%` (the original park run), or the
settlement/fire prefixes. Update `aoi_clip.DELETE_EXCLUDE` and
`SUPERSEDED_BY` when you wire it up (§3.2).

---

## 3. What is left, in order

### 3.1 Wire the frontend (highest value — everything in §2c–e is invisible without it)

1. Add to `globe.html` before `</body>`, next to `anim.js`:
   ```html
   <script src="/static/aoi_draw.js?v={{.Version}}"></script>
   <script src="/static/aoi_progress.js?v={{.Version}}"></script>
   ```
2. **CSS is not written yet.** `aoi_draw.js` and `aoi_progress.js` reference
   these classes: `.aoi-draw-panel/-head/-x/-hint/-estimate/-input/-actions/-btn/-dim/-err`,
   `.aoi-est-row/-head/-eta/-note/-details`, `.aoi-prog-line/-bar/-dim`.
   Match the existing `.filter-panel` and `.notification-item` styling.
3. **Where does the draw button go?** Put it in the **filter panel's "Selected
   Parks" row**, next to `#bbox-select-btn` and `#aoi-toggle` — that row is
   already "what am I looking at", and the AOI chip lives there.
   *But keep it visually distinct from `Select Area`*: the bbox tool is an
   instant, disposable filter; an AOI is days of ingest. Suggested label
   "New area…" with the pencil icon, calling `AOIDraw.start()`. Only render it
   for a principal with a password (`getPwd()` non-empty) — create returns 403
   otherwise.
4. Route `aoi_progress` notifications: add `'aoi_progress'` to the `type=`
   list in `loadNotifications()` (~line 5595), and in `updateNotificationList()`
   dispatch those items to `AOIProgress.cardHTML(item)`. Add a click handler for
   `data-action="open_aoi"` → `openAOIPopupById(id)`.
5. Add **Edit / Versions** to `showAOIPopup()` header for `is_owner`:
   * Edit → `AOIDraw.startEdit(id)` (**not written**: load the existing
     geometry as the initial `S.pts`, `POST /api/aois/{id}/edit`, carry the
     current slider window). The doc's key promise is that editing *always*
     lets you set a new window from the slider.
   * Versions → `GET /api/aois/{id}/versions`, list by `label`, archived ones
     dimmed with a Restore button.
6. Add AOI results to the **existing park search box** via
   `GET /api/aois/search?q=` — this is the only way back to an archived
   version. Show `label` (the window) under the name, and an "archived" chip.

### 3.2 Wire Hansen as an ingest unit

* Add `("hansen", 36, None)` to `aoi_lib.DEFAULT_DATASETS` **and** to
  `defaultAOIDatasets` in `srv/aoi_write.go` (the test enforces both).
* Add `run_hansen` to `aoi_runner.RUNNERS`: one window per unit via
  `hansen_loss.ingest(..., start_window=cur["i"], progress_cb=...)`, which is
  already resumable by window index.
* Add `deforest_hansen_%` to `aoi_clip.DELETE_EXCLUDE`, and add the
  `deforestation` preview layer to `SUPERSEDED_BY` keyed on *both* real
  ingests being done.
* Add `hansen` to `DS_LABEL` in both JS files and to `aoiCoverageHTML`'s label
  map in globe.html, and to `aoiBlockedDatasets`… no — remove nothing; just
  give it a rate in `srv/aoi_estimate.go` (measured: ~0.6 s/window read plus
  vectorising; time a real park run first and put the number in the test).
* Consider backfilling **parks** the same way: the existing park rows came from
  a one-off download of 26 tiles into `data/hansen/` which is **not on this
  machine**. `hansen_loss.py` streams instead, so `--park X` now works with no
  local data.

### 3.3 Remaining ingest units

* `gsw` — 3 missing 10×10° occurrence tiles (`occ_20E_0N`, `occ_30E_10N`,
  `occ_30E_0N`). Same `/vsicurl` trick as Hansen almost certainly applies;
  check before assuming a download is needed.
* `hydro` — HydroRIVERS_v10_af + HydroLAKES. Stopgap = PBF waterways from the
  `osm` unit.

### 3.4 Report (§3e of the plan doc)

`collectReportParks()` folds starred bboxes in by resolving them to parks. An
AOI slots in as a new source: AOI-level sections first (its own narratives + a
coverage caveat), then the intersecting parks. PDF/KML/CSV/XLSX inherit it.

### 3.5 Admin "Access" tab

Principals, AOI ownership, per-dataset enable/disable, the `kick` button, and
the queue status that `--status` prints.

### 3.6 Notifications must be principal-filtered

`aoi_progress` rows carry the AOI name in their title. They are keyed by
`park_id = <aoi id>`, so `GET /api/notifications` **will leak a private AOI's
name to every principal** once more than one AOI exists. Needs the same shape
as `miningNotifSQLFilter()` — join `aois` and apply `aoiVisibleSQL`. Today
there is exactly one AOI and one owner, so it is a precondition rather than a
live bug, **but it must land before a second AOI is created.**

### 3.7 Pins keyed by bare id

`loadPinnedLayer()` goes through `apiBase()` so pinning an AOI layer works, but
pins are keyed by bare id — an AOI and a park of the same name would share one.
Not reachable today (ids are disjoint). Key them `aoi:<id>:<type>`.

---

## 4. Do not re-litigate

* Whether AOI rows belong in bbox-keyed endpoints — **no**, `aoiExcludeSQL()`
  (plan doc §1). Any new query over park-shaped storage without an explicit
  `park_id` needs it, for privacy *and* to avoid double counting.
* Whether an AOI may enter `keystones_with_boundaries.json` — **never**.
  `park_assigner` would reassign detections away from the four parks it
  overlaps.
* Whether FIRMS product selection can be computed from the date — **no**,
  `firms_api.pick_source()` reads `/api/data_availability`. NOAA21 has no SP
  product; asking the wrong side of a cutover returns HTTP 200 with a
  header-only CSV.
* Whether the 10 m GHSL product can be used — it is not published as tiles.
* ~~Whether the AOI's deforestation should come from Hansen~~ — **reversed
  2026-08-07, see §2f. It should, for ≤2023.**
* Whether `/api/fire-frames`, `/api/features-in-bbox`, `/api/grid` should be
  visibility-filtered — no. They serve raw geography that was always public
  within the app. The polygon is the secret, not the pixels.

---

## 5. Files

| file | role |
|---|---|
| `scripts/aoi_runner.py` | the queue; leases, cursors, interruption |
| `scripts/test_aoi_resume.py` | proves resumability — run after lease changes |
| `scripts/aoi_lib.py` | connect, principal_ref, upsert, `DEFAULT_DATASETS` |
| `scripts/aoi_clip.py` | Phase A preview; `DELETE_EXCLUDE`, `SUPERSEDED_BY` |
| `scripts/hansen_loss.py` | **new** — streamed Hansen loss, no download |
| `scripts/ghsl_tiles.py` | GHSL tiles, cached by tile id, 1-indexed grid |
| `srv/aoi.go` | read path, visibility, `aoiExcludeSQL`, versioning columns |
| `srv/aoi_write.go` | **new** — create/refresh/delete/progress/kick |
| `srv/aoi_versions.go` | **new** — edit-as-fork, restore, search |
| `srv/aoi_estimate.go` | **new** — measured cost model |
| `srv/static/aoi_draw.js` | **new** — polygon editor, live estimate |
| `srv/static/aoi_progress.js` | **new** — the multi-day notification card |
| `db/migrations/042-aoi-versions.sql` | **new, unapplied** |
| `docs/PLAN_AOI_OVERLAY.md` | design rationale + measured facts (§1, §2, §4) |
