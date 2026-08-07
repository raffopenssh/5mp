# AOI — handover, rev 7 (2026-08-07)

`docs/PLAN_AOI_OVERLAY.md` is the design rationale and the record of *measured*
facts. This file is "what is done, what is left, how to check". Rev 7 is the
first revision with **nothing open**: every item rev 6 listed as remaining is
implemented and verified in a real browser, so this rewrite drops the archaeology
and keeps only the traps.

**One sentence**: an AOI is a *power bounding box* — an arbitrary polygon that is
kept, owned, versioned, and has data fetched **for it** over days, as opposed to
the "Select Area" bbox which is an instant disposable filter over data we already
hold. They stay in separate filter sections for exactly that reason.

---

## 0. State: complete

`XSA_Study_Area` (485,150 km², owner `$AOI_OWNER_PWD`) is the only AOI. Read path,
write path, queue, exports, animation, focus mode, abort, versioning, the star
report and the admin Access tab are all live, and every dataset is `done`:

    3.18M detections -> 38,725 trajectories · 2.23M GFW alerts -> 696 events
    76,903 Hansen polygons -> 7,079 events · 74,904 built-up polygons -> 1,552 settlements
    3,169 waterbodies · 11,370 rivers + 2,530 lakes · 3 country PBFs
    22 upstream watersheds + 24 downstream traces · 12,956 roads · 690 places

`park_basin_parts` is backfilled for **all 164 areas** (883 rows, 0 unsplit), so
no reader is on the merged-union fallback any more.

### Verification set — re-run after any AOI work

```bash
P='$AOI_OWNER_PWD'
python3 scripts/aoi_runner.py --status                             # all done, no lease
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM feature_geometries
  WHERE park_id='CAF_Chinko' AND feature_type='fire_trajectory'"   # 8753
curl -s "localhost:8000/api/aois?pwd=test2026"    | jq '.count'    # 0
curl -s "localhost:8000/api/aois?pwd=$P"          | jq '.count'    # 1
curl -s -o /dev/null -w '%{http_code}\n' \
  "localhost:8000/api/parks/XSA_Study_Area/stats?pwd=$P"           # 404 (park route must 404)
curl -s "localhost:8000/api/aois/XSA_Study_Area/basin?pwd=$P" \
  | jq '{upstream_count, downstream_count}'                        # 22, 24
curl -s "localhost:8000/api/notifications?type=aoi_progress&pwd=test2026" \
  | jq '.notifications|length'                                     # 0  <- privacy
curl -s "localhost:8000/api/admin/access?pwd=test2026" | jq '.aois|length'   # 0  <- privacy
python3 scripts/check_fire_consistency.py                          # Consistent.
python3 scripts/test_aoi_resume.py                                 # proves resumability
python3 scripts/check_basin_coverage.py                            # `wsh` per area
./tests/run_all.sh                                                 # db 37, api 45, ui 20
```

`go test ./srv/` fails on `TestServerSetupAndHandlers`
(`035-test-env.sql: no such column: avg_speed_kmh`). **Pre-existing, unrelated to
AOI** — verified by stashing. Do not chase it as an AOI regression.

Highest applied migration is **044** (`044-basin-parts.sql`).

---

## 1. The recurring failure mode: a no-op that reads as an answer

Four times now, days of ingest have been silently unreachable or unfetched while
every layer reported success:

| what | tell |
|---|---|
| the animator's missing 38,725 trajectories (rev 3) | a busy polygon animating empty |
| the `osm` unit's write-only `aoi:` scope key (rev 5) | 141 roads for three countries |
| `basin` fetching nothing because `--park <aoi>` matched no keystone (rev 6) | **0 watersheds for 485,000 km²** |
| `enrich_park_infra` skipping because the clip preview left rows | 432 placenames for 485,000 km² |

The tell is always **a number suspiciously round for its input size**, and the
cause is always a filter that matched nothing while exiting 0. Two rules fell
out of it and are now load-bearing:

1. **A unit that produces nothing for a large input must report unfinished**, so
   the queue retries instead of freezing a wrong answer as `done`. `run_basin`
   returns `ok = (rows > 0)`.
2. **An `aoi:` prefix that only the exclusion filter understands is a write-only
   key.** Bare AOI id in park-shaped tables, plus `aoiExcludeSQL()`.

## 1b. `ABS(<indexed col> - ?)` in a WHERE clause is a ~1000x slowdown

Non-sargable, so SQLite drops the index and covering-scans all 42.9M
`fire_detections` rows (19.8 s vs 0.02 s). This was the *real* cause of the
"archive is blocked by SQLite's single writer" blocker rev 4 spent a session on:
`_get_fire_density()` ran it once per deforestation cluster, so XSA's 76,903
Hansen polygons became hours of CPU inside one transaction, and every
user-initiated write 500'd. Fixed everywhere; grep before adding a query:

```bash
grep -rn 'ABS([a-z_]* - ?)' srv/ scripts/ analysis/
```

`ABS()` in a SELECT list is harmless. Same for `substr(acq_date,…)`: fine as a
secondary filter, fatal as the sole predicate.

**Before concluding "the single writer is the problem", check whether the writer
is CPU-bound.** `ps` showing 95% CPU and `State: R` is not lock contention.
`busy_timeout` bumps and `execUserToggle()` retries are treatments for a
different disease — harmless, and they fixed nothing.

## 1c. Every long writer now yields

Batch writers commit every ~200 rows and call `on_batch(n)` between batches (the
AOI runner passes a callback that reports progress and raises `Interrupted`), so
SQLite's one writer is free between batches and Ctrl-C / SIGTERM / out-of-time is
a normal exit:

* `rebuild_deforestation_for_park`, `rebuild_settlements_for_park`
* **the v5 fire chain** (rev 7): `load_fire_groups_to_db.py` commits every
  `BATCH_ROWS = 200` groups instead of once per park — safe because the run
  deletes the park's rows first and every group is an `INSERT OR REPLACE` keyed
  by `feature_id`, so an interruption re-derives exactly. `precompute_narratives_v5.py`
  commits every 25 cache blobs (they are megabytes each). Both connect with
  `timeout=120`.

A `SIGTERM` that appears ignored means the unit is inside a section with no
`stopping()` check, **not** that the lease is stranded. `--heal` only reclaims
*dead-pid* leases; for a live pid, `kill -9` then `--heal`.

---

## 2. The API surface, and the shape of the rule

`/api/aois/{id}/*` = the park handlers wrapped in `aoiGate()`; one visibility
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
POST   /api/aois/{id}/{edit,restore,refresh,kick,archive,cancel}
POST   /api/aois/{id}/refresh?resume=1  the inverse of cancel
DELETE /api/aois/{id}
GET    /api/admin/access                Access tab: ownership + queue (scoped!)
POST   /api/admin/aoi-dataset           enable/disable one dataset (owner-only)
```

**archive vs cancel vs delete** — three different sentences; conflating any two
loses something the user cannot get back:

| | says | touches `aoi_datasets` | reversible |
|---|---|---|---|
| `archive` | "off my screen" | **no** | `/restore` |
| `cancel` | "stop spending quota" | `enabled=0` for unfinished | `/refresh?resume=1` |
| `delete` | "this was a mistake" | drops everything | no |

`cancel` keeps **cursors** — that is what makes resume a resume. Verified byte-
identical across a cancel→resume round trip. `refresh?resume=1` is deliberately a
**different query** from plain `refresh`: plain refresh re-runs the derived layers
of a still-`enabled` queue, which after a cancel is the empty set.

**`archive` works** and the whole sequence passes in 17 ms on an idle database:
count → 0, state `archived`, search still finds it, narrative still 200,
`aoi_datasets` untouched, `/restore` back.

**Deliberately absent, and this is a decision not a gap**: `stats`, `species`,
`climate`, `publications`, `legal`, `checklist`, `turbidity`. Per-protected-area
facts; averaging them over 485,000 km² would invent a number. The popup and the
report say so and point at the intersecting parks; `fetchParkReportData()` and
`fetchPopupRoadData()` skip them rather than eating 404s.

Load-bearing details, each already got wrong once:

* **No endpoint runs the ingest.** `scripts/aoi_runner.py` owns lease discipline;
  `kick` shells out to it. There is one implementation of "work a unit".
* **`DELETE` order**: the `aois` row goes *last* — while it exists,
  `aoiExcludeSQL()` still masks derived rows not yet deleted. The delete list
  must include `roads_heigit`, `park_rivers_hydro`, `park_lakes_hydro`,
  `park_waterbodies`, `park_basins`, `park_basin_parts`, `park_basin_rivers`.
* Non-owners get **404, not 403** (`requireAOIOwner`) — an id must not be an
  oracle. Same reason `?aoi=` on a raw-geography endpoint is *ignored* when
  invisible rather than refused.
* **Any query over park-shaped storage without an explicit `park_id` must apply
  `aoiExcludeSQL(col)`** — for privacy *and* to stop the AOI double-counting the
  parks it overlaps. Notifications: `aoiNotifSQLFilter(col, principal)`.
* **…unless it is showing the AOI**, then `aoiScopeSQL(col, id)`: `?aoi=<id>` on
  `/api/features-in-bbox` and `/api/fire-anim-trajectories` serves that AOI's
  rows and **only** those, because its chain covers the whole polygon including
  the ~11% inside parks. Endpoints that **sum** keep using `aoiExcludeSQL` —
  this one paints pixels.
* **`resolveAreaGeom(id)` / `resolveAreaBBox(id)`** for any geography handler. An
  AOI is never in `AreaStore` (that would let `park_assigner` reassign detections
  away from the parks it overlaps), so the old loop yielded an empty boundary and
  KML lost its patrol effort.
* `validateAOIGeom` caps at 2,000 vertices (re-parsed by every runner, traced by
  the animator's canvas clip every frame).
* **Four writers share `(park_id=<aoi>, feature_type)`** in `feature_geometries`
  — `aoi_clip.py`, the v5 fire chain, the GFW `deforestation` unit, the `ghsl`
  unit — safe only because each deletes a **disjoint id prefix**. A fifth needs
  the same treatment. A layer in `aoi_clip.SUPERSEDED_BY` stops being clipped
  once its real ingest is `done`, or both would double count.
* **`aois.state` is never `'ready'`** — only `archived`. Readiness is *derived*
  from the queue (`/progress`, and the Access tab does the same). Printing the
  raw column labels a fully ingested AOI "pending" forever.
* **A superseded version is not a cancelled one** (rev 7). An edit forks and
  disables v1's queue, which looks identical to a Stop from the outside — but
  offering Resume there re-spends days of quota answering a question v2 already
  replaced. `/progress` reports `state:"superseded"` + `superseded_by`, the card
  says "Replaced by a newer version", and `refresh?resume=1` **409s**.

---

## 3. Frontend

| piece | where |
|---|---|
| routing | `apiBase(id)` in globe.html; `window.AOI_IDS` filled by `loadAOIs()` |
| map layer + popup | globe.html `loadAOIs`/`showAOIPopup`/`aoiCoverageHTML` |
| exports | `aoiExportButtonsHTML(id, name)` — popup header **and** the map tip's `.maptip-exports` row |
| filter section | globe.html `#aoi-section` — own heading, amber, own visibility toggle |
| editor | `srv/static/aoi_draw.js` — `AOIDraw.start()` / `.startEdit(id, name, geom)` |
| progress card | `srv/static/aoi_progress.js` — `AOIProgress.cardHTML(notif)` |
| animation | `Animator.open({aoi})` clips to the **polygon**; loaders append `&aoi=` |
| focus | globe.html `setAOIFocus`/`toggleAOIFocus`/`aoiFocusBrightIDs`/`applyAOIFocusPaint` |
| report | `collectReportParks()` folds in every visible AOI, first, unstarred |
| admin | globe.html `loadAccessTab`/`setAOIDataset`/`kickAOIRunner` → `srv/aoi_admin.go` |

### Focus mode

One switch, one meaning — "this polygon is the subject" — applied everywhere,
because a scope that only changes the colours is a lie about the numbers.

* parks outside are **dimmed, not hidden**: the outline is how you see the
  polygon crossing a boundary, and clicking one must still work.
* **starred parks are never dimmed.** A star is explicit and outranks an implicit
  scope; same for `selectedPAIds`. `toggleStar` repaints immediately while
  focused, or starring appears to do nothing.
* bbox layers, the animator and `collectReportParks()` all switch to the AOI's
  own rows. A report that silently covers ground the map is dimming is worse than
  either behaviour alone.

Traps, each already hit:

* **`var aoiFocusID`, not `let`.** `updatePAHighlighting()` reads it ~2,800 lines
  above the declaration during map setup; a `let` is a temporal dead zone.
* **`aoiFocusBrightIDs()` returns `null`, not `[]`,** when the park list did not
  resolve — with `[]` the whole world greys out.
* **Focus paint is layered on top of selection paint**, not woven into it;
  `resetAOILayerPaint()` exists because the paint is not idempotent; re-apply
  after a basemap change (`updateParkFillForBasemap` owns `fill-opacity` too).
* Dim colour `#5b6b5f` / 0.55, **not** `#3f3f46` / 0.3 — that erased 158 parks at
  continental zoom.
* Focus is **its own share param `?aoi_focus=`**, restored via
  `window._pendingAOIFocus`, degrading to "no focus" for an invisible id.

Things that will bite:

* **`window.map` is the `<div id="map">` ELEMENT** (named access on window). Use
  the bare lexical `map`. Symptom: `m.getSource is not a function`.
* **The analysis window is the time slider.** No second date picker.
  `5mp:date-window-changed` re-prices the editor live; anything wanting a
  specific window goes through `setTimeSliderRange()`.
* **A `const` in one `<script>` block is invisible to another.** globe.html has
  several; `AOI_REPORT_SECTIONS` is mirrored onto `window` for that reason.
* `can_create` comes from the server — a password can arrive as a cookie, so
  `getPwd()` cannot decide whether `POST /api/aois` 403s.
* Share links use `?aoi=` / `aoi_sections=` / `anim_aoi`, deliberately separate
  from `?popup=`/`sections=`, which resolve against the `areas` source an AOI is
  never in.
* The progress card is **not client state** — it is a `notifications` row, so it
  survives a laptop shut for a week. Polling is adaptive and *stops* at `ready`,
  `cancelled` and `superseded`.
* `datasets_total` is the **planned** count, so a stopped queue reports 0; the
  card adds `datasets_stopped` back for the denominator, or it reads "0/0 layers"
  beside "0 of 11 were fetched".
* **Pins are namespaced for an AOI** (`aoi:<id>:<type>`, rev 7). Ids are disjoint
  today; the flat park key `<id>_<type>` would have made a same-named AOI and
  park share one pin.

### Versioning

An edit forks: version N+1 is created, N is archived (`state='archived'`, hidden
from `ListAOIs`, still readable by id, queue disabled). Versions are labelled by
their **analysis window**, not by number. An edit that changes nothing returns
`unchanged: true`; an edit that did not move a vertex sends no geometry at all.
Only the outer ring is editable — a hole or a multipolygon is `ringLocked` and
carried through untouched, because flattening a donut would change what the AOI
*means* while looking like a date-only edit.

Verified in a real browser (rev 7): 4 corners placed with real `MouseEvent`s →
live estimate appears → dragging a vertex re-prices (3.3M → 4.08M km²) → moving
the time slider re-prices (54 → 567 satellite requests) → clicking the amber
first corner closes the ring → save creates v1 → Edit forks to v2 with v1
`archived` and `superseded_by` set → delete removes both with no orphan rows.

### The admin Access tab

`srv/aoi_admin.go` + `loadAccessTab()`. Three decisions:

1. **It is scoped, not global.** "Admin" in this app is any valid password
   (`RequireAdmin` accepts the access cookie), so it goes through `ListAOIs` with
   the caller's principal. A global view would hand every alpha tenant every
   other tenant's polygons — the one thing §2 exists to prevent. An id must not
   be an oracle, and neither must a tab.
2. **`principals.label` is never served.** It is `pwd[:3]+"…"`, useful in a local
   sqlite session and three characters of a credential on the wire. The handle is
   `ref[:8]`, a sha256 prefix, non-secret by construction, plus `is_you`.
3. **It reports the queue; it does not reimplement it.** Lease state comes
   straight from `aoi_datasets` (the columns `--status` prints) and "Run now" is
   the existing `/kick`. Disabling keeps the **cursor**, so re-enabling resumes.

---

## 4. What is left

Nothing blocking. Two nice-to-haves:

* **A second AOI has never existed.** Everything is written to be per-AOI, but
  every measurement is n=1. The first thing a second one will exercise is tile
  cache sharing (`data/ghsl/tiles/`, `data/gfw_tiles/`, `http_cache`) and the
  runner's fairness across two pending queues — it currently takes them in
  `(priority, dataset)` order with no round-robin, so a big AOI can starve a
  small one for days.
* **`aoi_grants` has no UI.** The table, the `aoiVisibleSQL` clause and the
  Access tab's "Shared with" column all exist; nothing writes a grant. Sharing an
  AOI today means sharing the password.

---

## 5. Do not re-litigate

* Hiding parks outside a focused AOI instead of dimming them — no. Nor dimming
  *starred* parks: a star is explicit and outranks an implicit scope.
* Archiving stopping the ingest — no. Archive is about the screen, `cancel` is
  about the quota. Conflating them means unhiding shows a progress bar instead of
  an answer.
* Offering Resume on a superseded version — no. Editing is the way forward,
  `/restore` the way back; `refresh?resume=1` 409s.
* Telling the user "database busy, try again" — no, that was written and removed.
  Fix the writer, and first check whether it is *CPU*-bound (§1b: it was, and 2.5
  hours of "lock contention" was one non-sargable `ABS()`).
* An `aoi:<id>` scope key in a park-shaped table — no. Bare id +
  `aoiExcludeSQL()`.
* AOI rows in bbox-keyed endpoints **by default** — no. With an explicit,
  visibility-checked `?aoi=` — yes, `aoiScopeSQL()`, and exclusively.
* An AOI in `keystones_with_boundaries.json` — **never**; `park_assigner` would
  reassign detections away from the parks it overlaps. `--aoi` injects it into
  the **in-memory** parks dict only.
* Starring an AOI to get it into the report — no. Visibility is the trigger;
  requiring a star gave a user with one AOI and no stars an empty report.
* A global admin view of all AOIs — no (§3, decision 1).
* FIRMS product from the date — no, `firms_api.pick_source()` reads
  `/api/data_availability`. NOAA21 has no SP product; the wrong side of a cutover
  returns HTTP 200 with a header-only CSV. The area endpoint caps at 5 days.
* The 10 m GHSL product — not published as tiles. The JRC grid is **1-indexed**.
* Deforestation from Hansen for ≤2023 — yes. Cutover: Hansen ≤2023
  (`deforest_hansen_`), GFW alerts ≥2024 (`deforest_gfw_`).
  `HANSEN_MAX_YEAR = 2023` deliberately stops short of Hansen's own 2024 band.
  Polygons alone are invisible: finish by clustering through
  `EventRebuilder.rebuild_deforestation_for_park(..., id_prefix=...)`.
* One merged watershed polygon per area — no. `park_basins` PK `(park_id, kind)`
  can only hold the union, and a union cannot say which river carries which lobe.
  `park_basin_parts` keeps one row per outlet; `?merged=1` asks for the union.
* Ranking outlets by `ord_flow` alone — no. HydroRIVERS: lower = bigger. OSM rows
  store 0 there and use `stream_order`, higher = bigger. Use `_discharge_rank()`.
* HydroSHEDS fetched unattended — impossible, `data.hydrosheds.org` 403s behind
  Cloudflare. `scripts/osm_hydro.py` ships instead (negated OSM ids, tag-derived
  band). It is **not** the `basin` unit: mghydro/MERIT answers "what drains
  through here" and carries no names.
* Visibility-filtering `/api/fire-frames`, `/api/grid` — no. They serve raw
  geography that was always public within the app. **The polygon is the secret,
  not the pixels.**
* Species/climate/legal/stats for an AOI — no. Averaging a park-level fact over
  485,000 km² invents a number.
* A privacy tidy-up as a migration — no. `reownSystemAOIProgress()` is a
  warn-and-continue startup fixup: as a migration it failed `NewServer` when the
  write lock was held and systemd restart-looped the service. When a migration is
  downgraded to a fixup, **delete the file** — a doc note is not a revert.
* Running two runner units concurrently — no. SQLite has one writer and the v5
  chain holds it for minutes; that stranded three leases on 2026-08-07.

---

## 6. Files

| file | role |
|---|---|
| `scripts/aoi_runner.py` | the queue: leases, cursors, interruption |
| `scripts/test_aoi_resume.py` | proves resumability — run after lease changes |
| `scripts/aoi_lib.py` | connect, principal_ref, upsert, `DEFAULT_DATASETS` |
| `scripts/aoi_admin.py` | CLI: list/show/create an AOI |
| `scripts/aoi_clip.py` | Phase A preview; `DELETE_EXCLUDE`, `SUPERSEDED_BY` |
| `scripts/hansen_loss.py` | streamed Hansen loss (<=2023), no download |
| `scripts/gsw_water.py` | streamed JRC surface water -> `park_waterbodies` |
| `scripts/osm_hydro.py` | rivers & lakes from a country PBF (HydroSHEDS is 403) |
| `scripts/ghsl_tiles.py` | GHSL tiles, cached by tile id, 1-indexed grid |
| `scripts/fetch_park_basins.py` | watersheds per outlet; **`--aoi`**, `outlet_budget()` |
| `srv/aoi.go` | read path, visibility, `aoiExcludeSQL`/`aoiScopeSQL`, `resolveAreaGeom`/`resolveAreaBBox`, `reownSystemAOIProgress` |
| `srv/aoi_write.go` | create/refresh/delete/progress/kick/cancel |
| `srv/aoi_versions.go` | edit-as-fork, restore, archive |
| `srv/aoi_admin.go` | the Access tab: scoped ownership + queue control |
| `srv/aoi_estimate.go` | measured cost model (test pins 252 GFW / 570 FIRMS / 4 GHSL) |
| `srv/errors.go` | `isDBLocked`, `execUserToggle` (§1b: not sufficient alone) |
| `srv/park_basins.go` | `loadBasinParts` = all watersheds; merged is the fallback |
| `srv/static/aoi_draw.js` | polygon editor + live estimate; `startEdit` forks |
| `srv/static/aoi_progress.js` | the multi-day notification card |
| `db/migrations/040..044` | overlays, parks, versions, pixel count, basin parts |
| `docs/PLAN_AOI_OVERLAY.md` | design rationale + measured facts |

```bash
python3 scripts/aoi_runner.py --status            # queue state
python3 scripts/aoi_runner.py --heal              # reclaim dead-pid leases
python3 scripts/aoi_clip.py --aoi XSA_Study_Area  # Phase A preview, ~4s
# cron: 0 12 * * *  aoi_runner.py --daily  (deliberately far from the 3am fire job)
```
