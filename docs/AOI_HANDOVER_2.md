# AOI — handover, rev 4 (2026-08-07)

`docs/PLAN_AOI_OVERLAY.md` is the design rationale and the record of *measured*
facts — read its §1, §2 and §4 before touching ingest. This file is only "what
is done, what is left, how to check".

**One sentence**: an AOI is a *power bounding box* — an arbitrary polygon that
is kept, owned, versioned, and has data fetched **for it** over days, as
opposed to the "Select Area" bbox which is an instant disposable filter over
data we already hold.

---

## 0. Read this first — the open blocker (rev 4)

**`POST /api/aois/{id}/archive` is written, wired and unverified.** It returns
500 while the AOI Hansen unit is running, and that is not a bug in the handler:
`rebuild_deforestation_for_park` on a 76,903-polygon input holds SQLite's
single writer **continuously for 35+ minutes**, so the request cannot get a
write slot at all.

What was tried, and why each was not enough:

* `busy_timeout` 5 s → 30 s (`db/db.go`). Helps a request that arrives between
  two short batch commits. Useless here.
* `execUserToggle()` (`srv/errors.go`): retry the one-row UPDATE for ~40 s
  around the driver's own wait. Still loses, for the same reason — there are no
  gaps to retry into. **A 60 s curl came back 500.**
* A 503 + Retry-After was written and then removed on the (correct) grounds
  that a user flipping a toggle should not be told to come back later.

So the honest framing of what is left: **the fix is not in the handler, it is
in the writer.** The clustering step needs to commit in batches and release the
lock between them, the same way the ingest half of the unit already does
(`hansen_loss.ingest` flushes per window — that is why its cursor is a real
resume point). Until that happens, any user-initiated write is hostage to
whatever batch job is running.

Verify with the *whole* sequence, on an idle database, or the result means
nothing:

```bash
P='$AOI_OWNER_PWD'
python3 scripts/aoi_runner.py --status | grep -c running   # must be 0 first
curl -s -X POST "localhost:8000/api/aois/XSA_Study_Area/archive?pwd=$P"   # {"archived":...}
curl -s "localhost:8000/api/aois?pwd=$P" | jq '.count'                   # 0  (off the map)
curl -s "localhost:8000/api/aois/XSA_Study_Area?geometry=0&pwd=$P" \
  | jq -r '.aoi.state'                                                   # archived
curl -s "localhost:8000/api/aois/search?q=Study&pwd=$P" | jq '.results[0].archived'  # true
curl -s -o /dev/null -w '%{http_code}\n' \
  "localhost:8000/api/aois/XSA_Study_Area/fire-narrative?pwd=$P"          # 200 (links live)
sqlite3 db.sqlite3 "SELECT count(*) FROM aoi_datasets
  WHERE aoi_id='XSA_Study_Area' AND enabled=0"                           # 0 — archive must
                                                                         # NOT stop ingest
curl -s -X POST "localhost:8000/api/aois/XSA_Study_Area/restore?pwd=$P"  # and back
```

That last assertion is the design decision most likely to be "fixed" by
mistake: **archiving is a statement about the screen, not about the question.**
An AOI with three days of ingest left should keep fetching while hidden, so
unhiding shows an answer rather than a progress bar. (An *edit* does disable
the old queue — there the question genuinely was superseded.)

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

`hansen` (Hansen loss 2001–2023, 20 windows × ~50 s) **finished its ingest**
(76,903 loss polygons, cursor `{"i": 20}`) and as of the end of rev 4's session
was still inside the clustering step — `rebuild_deforestation_for_park`, 36+
minutes and counting, holding the write lock the entire time (see §0). Check
whether the events landed before assuming it is stuck:

```bash
sqlite3 db.sqlite3 "SELECT count(*) FROM deforestation_events
  WHERE park_id='XSA_Study_Area' AND event_id LIKE 'deforest_hansen_%'"
python3 scripts/aoi_runner.py --status | grep hansen   # 'done' = it committed
```

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

⚠️ **Stale `aoi_progress` rows** — the runner has been writing them keyed by
AOI id since rev 3, but rows written before that were `park_id='SYSTEM'` and
therefore leaked the AOI's name to every principal. The manual UPDATE was
blocked by the writer lock on three attempts across two sessions, so it is now
**migration 044** (`044-aoi-progress-reown.sql`) and applies itself at the next
startup that can get a write slot. Confirm:

```bash
curl -s "localhost:8000/api/notifications?type=aoi_progress&pwd=test2026" \
  | jq '.notifications|length'                                     # 0
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
POST   /api/aois/{id}/archive          hide the overlay; keeps data + links
POST   /api/aois/{id}/cancel           stop fetching; keeps what landed
POST   /api/aois/{id}/refresh?resume=1 the inverse of cancel
DELETE /api/aois/{id}
```

**archive vs cancel vs delete** — three different sentences, and conflating any
two of them loses something the user cannot get back:

| | says | touches `aoi_datasets` | reversible |
|---|---|---|---|
| `archive` | "off my screen" | **no** | `/restore` |
| `cancel` | "stop spending quota" | `enabled=0` for unfinished | `/refresh?resume=1` |
| `delete` | "this was a mistake" | drops everything | no |

`cancel` keeps **cursors**, which is what makes resume a resume rather than a
restart — nothing is re-downloaded and no FIRMS quota is re-spent. It sets a
`running` row back to `pending` rather than `failed`: the runner that owns it
may be mid-unit and its own interrupt path releases the lease cleanly.

`refresh?resume=1` is deliberately a **different query** from plain `refresh`,
not a mode of it. Plain refresh re-runs the cheap derived layers of a queue
that is still `enabled=1` — which after a cancel is the empty set, so the
Resume button would have appeared to work and done nothing.

`/progress` grew `state:"cancelled"` plus `datasets_stopped` and `is_owner`.
The first matters: a disabled-and-unfinished row counted as "not planned" made
a cancelled AOI report **100% ready**, which is the one number the card must
never invent. `is_owner` is what gates the Stop/Resume buttons client-side (the
endpoints 404 for non-owners regardless).

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
| focus | globe.html `setAOIFocus`/`toggleAOIFocus`/`aoiFocusBrightIDs`/`applyAOIFocusPaint` |
| archive | globe.html `archiveAOI()` → `POST /api/aois/{id}/archive` |
| abort | `aoi_progress.js` `cancel`/`resume` → `/cancel`, `/refresh?resume=1` |
| report | `collectReportParks()` folds in every visible AOI, first, unstarred |

### Focus mode (rev 4)

An AOI is a *focus*: someone drew it because that is the ground they care
about. One switch, one meaning — "this polygon is the subject" — applied
everywhere it can be, because a scope that only changes the colours is a lie
about the numbers:

* parks outside the AOI are **dimmed, not hidden**. Their outline is how you
  see the polygon crossing a boundary, and clicking one must still work.
  Dimming states a priority; hiding would state a filter nobody asked for.
* **starred parks are never dimmed**, in focus or out. A star is an explicit
  "keep this in front of me" and it outranks an implicit scope; it is also the
  escape hatch that stops focus from hiding the one park someone is comparing
  against. Same for anything in `selectedPAIds`. `toggleStar` repaints
  immediately while focused, or starring appears to do nothing.
* the bbox feature layers and the animator switch to the AOI's own rows via
  `?aoi=` (`aoiScopeSQL`), so its 38,725 trajectories are what you see rather
  than park rows covering 11% of the same ground twice.
* `collectReportParks()` filters to the same bright set. A report that silently
  covers ground the map is dimming is worse than either behaviour alone.
* the animator inherits focus when the caller did not say otherwise, so ▶ from
  the time slider animates the subject and the focus banner cannot disagree
  with what is on screen.

Traps in it, each already hit:

* **`var aoiFocusID`, not `let`.** `updatePAHighlighting()` is ~2,800 lines
  above the declaration and reads focus during map setup; a `let` puts it in a
  temporal dead zone and the first highlight pass throws.
* **`aoiFocusBrightIDs()` returns `null`, not `[]`, when the park list did not
  resolve.** With an empty array the "inside" set is empty and the entire world
  greys out, which looks exactly like a broken basemap. Every consumer treats
  `null` as "no focus".
* **Focus paint is layered on top of the selection paint, not woven into it.**
  Two things want the same four paint properties; interleaving them produced
  nested `case` expressions that were wrong in the corners. Selection decides
  colour, `applyAOIFocusPaint()` then knocks back what focus does not cover.
* **`resetAOILayerPaint()` exists because the paint is not idempotent.**
  Leaving focus must put `aois-outline`/`aois-fill` back explicitly.
* **Re-apply after a basemap change.** Dimming lives in `fill-opacity`, which
  `updateParkFillForBasemap()` also owns — without the call at its end,
  switching to satellite silently drops focus.
* The dim colour is `#5b6b5f` / 0.55 outline opacity, **not** the `#3f3f46` /
  0.3 it started as: at continental zoom that erased the other 158 parks
  completely, and "my parks disappeared" is a worse misreading than "my parks
  look secondary".
* Focus is **its own share param `?aoi_focus=`**, not implied by `?aoi=`
  (popup) and not folded into `?parks=`. Opening an AOI's popup and declaring
  it the subject of the map are different acts. It is restored via
  `window._pendingAOIFocus`, drained by `loadAOIs()`, and degrades to "no
  focus" for an id this principal cannot see — an id must not be an oracle.
  `loadAOIs()` also clears focus whose AOI vanished, or the map stays dimmed
  against a polygon that is no longer there.

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
  `ready` **and at `cancelled`** (nothing will move until the user says so;
  Resume re-arms the watcher through `post()`).

**Versioning**: an edit forks. Version N+1 is created, N is archived
(`state='archived'`, hidden from `ListAOIs`, still readable by id, queue
disabled). Versions are labelled by their **analysis window**, not by number.
An edit that changes nothing returns `unchanged: true`. Why: an AOI is a
question plus the days of ingest that answered it; mutating either in place
leaves its derived rows as answers to a question nobody asked, with no way to
tell, because the id did not change.

---

## 4. What is left, in priority order

### 4.0 Verify `archive` — and fix the writer that blocks it

See §0. The handler, route, buttons (popup header + filter tag), toast and
focus-teardown are all in; **not one successful call has been observed.** Do
not "fix" it in the handler until you have run the §0 sequence on an idle
database — the retry loop is already there and already loses.

The real work is making the batch writers yield:
`EventRebuilder.rebuild_deforestation_for_park()` should commit per cluster
batch the way `hansen_loss.ingest()` commits per window. That is one fix for a
whole class of symptom — every user-initiated write on this deployment is
currently hostage to whatever cron is running.

### 4.1 Finish `hansen` for XSA

The only incomplete dataset. `~50 s` per 2° window × 20, resumable via
`start_window`. It is the **fifth writer** of `(park_id=<aoi>, feature_type)`
and safe only because it owns the disjoint `deforest_hansen_` prefix.

```bash
python3 scripts/aoi_runner.py --aoi XSA_Study_Area --dataset hansen --minutes 90
```

`seed_datasets()` is `INSERT OR IGNORE`, so an existing AOI picks up newly
added datasets by re-seeding.

### 4.2 Confirm migration 044 applied

Was §4.2 "run this SQL by hand" in rev 2 and rev 3, and never got run — hence
it is a migration now. One check, in §1.

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

### 4.4b Verify the rev-4 abort UI by hand

Stop/Resume on the progress card are wired and the endpoints answer, but the
round trip has only been exercised while the writer was locked. In a browser,
with the runner idle: open the notification dropdown → the AOI card shows "Stop
fetching" → click → the line reads `Stopped · N of M layers were fetched` and
the bar goes grey → "Resume" → back to queued/partial. Then confirm
`aoi_datasets.cursor` is **unchanged** across both.

Also still unclicked from rev 4: the focus toggle in the popup header (the
filter-tag ◎ and the programmatic path are verified, including
`?aoi_focus=` restore and star-overrides-focus), and a star report taken while
focused — it should contain the AOI plus only the bright parks.

### 4.5 Admin "Access" tab

Principals, AOI ownership, per-dataset enable/disable, the `kick` button, and
what `--status` prints.

### 4.6 Pins keyed by bare id

`loadPinnedLayer()` routes via `apiBase()` so pinning an AOI layer works, but
pins are keyed by bare id — an AOI and a park of the same name would share one.
Not reachable today (ids are disjoint). Key them `aoi:<id>:<type>`.

---

## 5. Do not re-litigate

* Hiding parks outside a focused AOI instead of dimming them — no. The outline
  is how you see the polygon crossing a boundary, and clicking one must keep
  working. Nor dimming *starred* parks: a star is explicit and outranks an
  implicit scope.
* Archiving an AOI stopping its ingest — no. Archive is about the screen;
  `cancel` is about the quota. Conflating them means unhiding shows a progress
  bar instead of an answer.
* Telling the user "database busy, try again" when they flip a toggle — no,
  that was written and removed. Fix the batch writer instead (§0).
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
| `srv/aoi_versions.go` | edit-as-fork, restore, **archive** |
| `srv/errors.go` | `isDBLocked`, `execUserToggle` — retry a user toggle around the write lock (§0: not sufficient on its own) |
| `srv/aoi_estimate.go` | measured cost model (test pins 252 GFW / 570 FIRMS / 4 GHSL) |
| `srv/static/aoi_draw.js` | polygon editor + live estimate; `startEdit` forks |
| `srv/static/aoi_progress.js` | the multi-day notification card |
| `db/migrations/042-aoi-versions.sql` | applied 2026-08-06 |
| `db/migrations/043-deforestation-pixel-count.sql` | applied 2026-08-07 |
| `db/migrations/044-aoi-progress-reown.sql` | closes the `SYSTEM`-keyed notification leak |
| `docs/PLAN_AOI_OVERLAY.md` | design rationale + measured facts |
