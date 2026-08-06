# AOI — handover, 2026-08-06 (rev 2)

Replaces `docs/AOI_HANDOVER.md` (delete it once you have read this; everything
still true has been carried across). `docs/PLAN_AOI_OVERLAY.md` remains the
design rationale and the record of *measured* facts — read its §1, §2 and §4
before touching ingest.

**One sentence**: an AOI is a *power bounding box* — an arbitrary polygon that
is kept, owned, versioned, and has data fetched **for it** over days, as
opposed to the "Select Area" bbox which is an instant disposable filter over
data we already hold.

---

## 1. State: what works end to end

Read path, write path and the queue are all live. `XSA_Study_Area`
(485,150 km², owner `$AOI_OWNER_PWD`) is the only AOI.

```bash
python3 scripts/aoi_runner.py --status
```

| unit | state | note |
|---|---|---|
| `clip` | done | preview from the 4 overlapping parks |
| `fire_gap` | done | 3,182,542 detections, 34/34 windows |
| `gfw` | done | 2,225,511 alerts, 252/252 tiles |
| `deforestation` | done | 696 events from those alerts (2024+ only) |
| `fire_v5` | **pending at step 3/4** | groups already built (48 MB json); resume with the runner |
| `ghsl` | pending | never completed a tile |
| `osm`, `basin` | pending | runners exist |
| `gsw`, `hydro` | pending | **no runner** — §4 |

```bash
python3 scripts/aoi_runner.py --aoi XSA_Study_Area --minutes 120
```

**Never run two units at once.** SQLite has one writer and the v5 chain holds
it for minutes; that is what stranded three leases on 2026-08-07. Dead-pid
leases now self-heal (`--heal`, and automatically at the start of every run).
`python3 scripts/test_aoi_resume.py` proves interruption is a normal exit —
run it after any change to lease/cursor code.

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
./tests/run_all.sh                                                 # db 37, api 45, ui 20
```

`go test ./srv/` currently fails on `TestServerSetupAndHandlers`
(`035-test-env.sql: no such column: avg_speed_kmh`). **Pre-existing, unrelated
to AOI** — verified by stashing. Fix it or ignore it, but do not chase it as
an AOI regression.

---

## 2. The API surface, and the shape of the rule

`/api/aois/{id}/*` = the park handlers, wrapped in `aoiGate()`. One visibility
check covers them all. `/api/parks/{aoi}` **404s** (`ParkIDMiddleware` +
`IsAOIID`), so getting the prefix wrong is a hard failure, not a leak.
In the frontend, **always build these URLs with `apiBase(id)`**.

Live and verified against XSA:

```
GET    /api/aois                       list (+ can_create)
GET    /api/aois/search?q=             incl. archived; the only way back to one
GET    /api/aois/{id}                  metadata + per-dataset coverage
GET    /api/aois/{id}/versions         the lineage
GET    /api/aois/{id}/progress         live, no-store
GET    /api/aois/{id}/{fire-narrative,fire-trend,fire-realtime,features,
        deforestation-narrative,settlement-narrative,feature-stats,
        classified-settlements,classified-deforestation,
        settlement-intensity,infrastructure,basin}
GET    /api/aois/{id}/export.{geojson,kml,locus}
POST   /api/aois/estimate              side-effect free; call it while dragging
POST   /api/aois                       create + seed queue (runs nothing)
POST   /api/aois/{id}/{edit,restore,refresh,kick}
DELETE /api/aois/{id}
```

**Deliberately absent, and this is a decision not a gap**: `stats`, `species`,
`climate`, `publications`, `legal`, `checklist`, `turbidity`. They are
per-protected-area facts; averaging them over 485,000 km² would invent a
number. The popup says so and links to the intersecting parks.

Load-bearing details, all of which have already been got wrong once:

* **No endpoint runs the ingest.** `scripts/aoi_runner.py` owns the lease
  discipline; `kick` shells out to it rather than reimplementing it.
* **`DELETE` order**: the `aois` row goes *last*, because while it exists
  `aoiExcludeSQL()` still masks derived rows not yet deleted.
* Non-owners get **404, not 403** (`requireAOIOwner`) — an id must not be an
  oracle.
* `validateAOIGeom` caps at 2,000 vertices (re-parsed by every runner, traced
  by the animator's canvas clip every frame).
* **Any query over park-shaped storage without an explicit `park_id` must
  apply `aoiExcludeSQL(col)`** — for privacy *and* to stop the AOI
  double-counting the parks it overlaps.
* Same for notifications: `aoiNotifSQLFilter(col, principal)` in
  `/api/notifications` and the RSS feed. `aoi_progress` rows are keyed by
  `park_id = <aoi id>` and carry the AOI's **name** in their title.
* **`resolveAreaGeom(id)`** (`srv/aoi.go`) is how a handler gets a name and a
  boundary for a park *or* an AOI. An AOI is never in `AreaStore` (that would
  let `park_assigner` reassign detections away from the four parks it
  overlaps), so the old `for _, pa := range s.AreaStore.Areas` loop silently
  yielded an empty boundary — and KML derives the patrol-effort bbox from the
  boundary, so the export lost its effort too. Any new geography handler uses
  this.

---

## 3. Frontend map

| piece | where |
|---|---|
| routing | `apiBase(id)` in globe.html; `window.AOI_IDS` filled by `loadAOIs()` |
| map layer + popup | globe.html `loadAOIs`/`showAOIPopup`/`aoiCoverageHTML` |
| filter section | globe.html `#aoi-section` — own heading, amber, own visibility toggle |
| tags | `updateAOITagsDisplay()` (amber, carries the window) + `updateStarredAreaTags()` (blue) |
| editor | `srv/static/aoi_draw.js` — `AOIDraw.start()` / `.startEdit(id, name, geom)` |
| progress card | `srv/static/aoi_progress.js` — `AOIProgress.cardHTML(notif)` |
| animation | `Animator.open({aoi})` clips to the **polygon**, not the bbox |

Things that will bite:

* **`window.map` is the `<div id="map">` ELEMENT**, not the MapLibre instance
  (named access on window). Use the bare lexical `map` binding, as
  `aoi_draw.js` `theMap()` and anim.js do. Symptom: `m.getSource is not a
  function`.
* **The analysis window is the time slider.** There must not be a second date
  picker that can disagree with it. `5mp:date-window-changed` (debounced, from
  both `applyPreciseDateFilter` paths) re-prices the editor live.
* `can_create` comes from the server. A password can arrive as a **cookie**,
  so the frontend's `getPwd()` cannot decide whether `POST /api/aois` 403s.
* Share links use `?aoi=` / `aoi_sections=` / `anim_aoi`, deliberately separate
  from `?popup=`/`sections=`, which resolve against the `areas` source an AOI
  is never in.
* The progress card is **not client state** — it is a `notifications` row, so
  it survives a laptop shut for a week. Polling is adaptive and *stops* at
  `ready`.

**Versioning**: an edit forks. Version N+1 is created, N is archived
(`state='archived'`, hidden from `ListAOIs`, still readable by id, queue
disabled). Versions are labelled by their **analysis window**, not by number —
a user remembers "the 2024 one". An edit that changes nothing returns
`unchanged: true`. Why: an AOI is a question plus the days of ingest that
answered it; mutating either in place leaves its derived rows as answers to a
question nobody asked, with no way to tell, because the id did not change.

---

## 4. What is left, in priority order

### 4.1 Finish the queue for XSA (cheap, unblocks everything else)

`fire_v5` step 3/4, then `ghsl`, `osm`, `basin`. One unit at a time.

### 4.2 Two ingest units have no runner

* `gsw` — 3 missing 10×10° occurrence tiles (`occ_20E_0N`, `occ_30E_10N`,
  `occ_30E_0N`). Try `/vsicurl` before assuming a download (it worked for
  Hansen).
* `hydro` — HydroRIVERS_v10_af + HydroLAKES. Stopgap: PBF waterways from `osm`.

### 4.3 Hansen for pre-2024 deforestation — script written, **not wired**

`scripts/hansen_loss.py` works (`--park CAF_Chinko --dry-run`; XSA is 20
windows across 4 tiles). GFW integrated alerts only start in **2024**, so the
AOI has no deforestation history before then while every park it overlaps has
2001–2024 Hansen polygons. Tiles are 45–116 MB public COGs read through
`/vsicurl` in **0.6 s per 2° window** — no download, no quota. Cutover matches
the parks exactly: Hansen ≤2023 (`deforest_hansen_` prefix), alerts ≥2024
(`deforest_gfw_`). `HANSEN_MAX_YEAR = 2023` deliberately stops short of
Hansen's own 2024 band so the two never double count.

To wire it: add `("hansen", 36, None)` to **both** `aoi_lib.DEFAULT_DATASETS`
and `defaultAOIDatasets` (a test enforces they agree); add `run_hansen` to
`aoi_runner.RUNNERS` (one window per unit — `ingest(..., start_window=cur["i"])`
is already resumable); add `deforest_hansen_%` to `aoi_clip.DELETE_EXCLUDE`
and gate the `deforestation` preview in `SUPERSEDED_BY` on *both* real ingests
being done; add a rate to `srv/aoi_estimate.go` (measure a real park run first
and put the number in the test); add the label to `DS_LABEL` in both JS files
and to `aoiCoverageHTML`.

⚠️ It is the **fifth writer** of `(park_id=<aoi>, feature_type)` and is only
safe because it owns a disjoint id prefix. It must never touch
`deforest_gfw_%`, plain `deforest_%`, or the settlement/fire prefixes.

Bonus: the parks' existing Hansen rows came from a one-off download of 26 tiles
into `data/hansen/` which **is not on this machine**. `hansen_loss.py` streams,
so `--park X` now works with no local data.

### 4.4 Star report does not know about AOIs

`collectReportParks()` folds starred bboxes in by resolving them to parks.
An AOI should slot in as a new source: AOI-level sections first (its own
narratives + a coverage caveat), then the intersecting parks. PDF/KML/CSV/XLSX
inherit it. The exports themselves are now ready (§2), so this is frontend
plumbing in `collectReportParks` / `buildFullReportMarkdown`.
There is currently **no way to star an AOI** — decide whether an AOI belongs in
`starredItems` as its own bucket (probably yes: it is already a kept object)
or whether owning one implies starring it.

### 4.5 Verify the editor by hand

The draw flow has only been exercised programmatically; simulated `map.fire('click')`
did not produce the live estimate, which is probably the simulation and not the
code, but **click through it in a real browser** before calling it done: place
3+ corners, watch the estimate appear, drag a vertex, move the time slider,
save, then Edit → check it forks and the old version shows as archived under
Versions.

### 4.6 Admin "Access" tab

Principals, AOI ownership, per-dataset enable/disable, the `kick` button, and
what `--status` prints.

### 4.7 Pins keyed by bare id

`loadPinnedLayer()` routes via `apiBase()` so pinning an AOI layer works, but
pins are keyed by bare id — an AOI and a park of the same name would share one.
Not reachable today (ids are disjoint). Key them `aoi:<id>:<type>`.

---

## 5. Do not re-litigate

* AOI rows in bbox-keyed endpoints — **no**, `aoiExcludeSQL()`.
* An AOI in `keystones_with_boundaries.json` — **never**; `park_assigner` would
  reassign detections away from the parks it overlaps.
* FIRMS product from the date — **no**, `firms_api.pick_source()` reads
  `/api/data_availability`. NOAA21 has no SP product; the wrong side of a
  cutover returns HTTP 200 with a header-only CSV.
* The 10 m GHSL product — not published as tiles. The grid is **1-indexed**.
* Deforestation from Hansen for ≤2023 — **yes** (reversed 2026-08-07, §4.3).
* Visibility-filtering `/api/fire-frames`, `/api/features-in-bbox`, `/api/grid`
  — no. They serve raw geography that was always public within the app. **The
  polygon is the secret, not the pixels.**
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
| `scripts/hansen_loss.py` | streamed Hansen loss, no download — **unwired** |
| `scripts/ghsl_tiles.py` | GHSL tiles, cached by tile id, 1-indexed grid |
| `srv/aoi.go` | read path, visibility, `aoiExcludeSQL`, `aoiNotifSQLFilter`, `resolveAreaGeom` |
| `srv/aoi_write.go` | create/refresh/delete/progress/kick |
| `srv/aoi_versions.go` | edit-as-fork, restore, search |
| `srv/aoi_estimate.go` | measured cost model (test pins 252 GFW / 570 FIRMS / 4 GHSL) |
| `srv/static/aoi_draw.js` | polygon editor + live estimate; `startEdit` forks |
| `srv/static/aoi_progress.js` | the multi-day notification card |
| `db/migrations/042-aoi-versions.sql` | applied 2026-08-06 |
| `docs/PLAN_AOI_OVERLAY.md` | design rationale + measured facts |
