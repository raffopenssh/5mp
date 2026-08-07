# AOI — handover, rev 5 (2026-08-07)

`docs/PLAN_AOI_OVERLAY.md` is the design rationale and the record of *measured*
facts — read its §1, §2 and §4 before touching ingest. This file is only "what
is done, what is left, how to check".

**One sentence**: an AOI is a *power bounding box* — an arbitrary polygon that
is kept, owned, versioned, and has data fetched **for it** over days, as
opposed to the "Select Area" bbox which is an instant disposable filter over
data we already hold.

---

## 0. Read this first — rev 4's blocker was a missing index hint

**`archive` works. It was never a handler bug, and the fix was one SQL
predicate.** rev 4 spent a session concluding that
`rebuild_deforestation_for_park` "holds SQLite's single writer for 35+ minutes"
and that the fix belonged "in the writer, not the handler". Half right: the
writer *was* the problem, but not because of transaction shape. It was this, in
`EventRebuilder._get_fire_density()`:

```sql
WHERE ABS(latitude - ?) < ? AND ABS(longitude - ?) < ?   -- 5.04 s
WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?   -- 0.044 s
```

Wrapping an indexed column in `ABS()` makes the term non-sargable, so SQLite
abandons `idx_fire_location` and covering-scans all 42.9M `fire_detections`
rows. **115× slower, once per deforestation cluster.** On XSA's 76,903 Hansen
polygons that is hours of CPU inside one transaction — which is why every
user-initiated write on the deployment returned 500, and why
`daily_park_refresh` had been dying nightly with `database is locked`.

With the predicate fixed, the whole §0 verification sequence passes **in 17 ms**
on an idle database:

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
curl -s -X POST "localhost:8000/api/aois/XSA_Study_Area/restore?pwd=$P"  # and back
```

**Verified 2026-08-07: archive → count 0 → state archived → search finds it →
narrative 200 → datasets untouched → restore.** All of it.

That last assertion is the design decision most likely to be "fixed" by
mistake: **archiving is a statement about the screen, not about the question.**
An AOI with three days of ingest left should keep fetching while hidden, so
unhiding shows an answer rather than a progress bar. (An *edit* does disable
the old queue — there the question genuinely was superseded.)

Three lessons worth more than the fix:

1. **Before concluding "SQLite's single writer is the problem", check whether
   the writer is CPU-bound.** `ps` showed 95% CPU and `State: R` for 2.5 hours.
   A process genuinely waiting on a lock is `S`, not `R`. rev 4's `busy_timeout`
   bump and `execUserToggle()` retry loop were both treatments for a diagnosis
   that was wrong — they are harmless and can stay, but they fixed nothing.
2. **A 500 from a write while a batch job runs is a performance bug until
   proven otherwise.** The temptation is to make the handler more patient. The
   right move is to ask why the batch is slow.
3. `rebuild_deforestation_for_park` *also* now commits every 200 events and
   calls `on_batch(count)` between batches (the AOI runner passes a callback
   that reports progress and raises `Interrupted`). That is the fix rev 4
   proposed, and it is worth having on its own — but it was the second-order
   problem, not the first.

### Still open from this: the clustering loop had no interrupt point

`kill -TERM` on the hansen runner did nothing for 65 s because the clustering
step never checked `stopping()`. `on_batch` closes that. But note that
`--status` showed `running` with a live lease the whole time, so **a `SIGTERM`
that appears ignored means the unit is inside a section with no check, not that
the lease is stranded.** `--heal` only reclaims *dead-pid* leases; for a live
pid you must `kill -9` and then `--heal`.

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

`hansen` (Hansen loss 2001–2023, 20 windows × ~50 s) finished its ingest
(76,903 loss polygons, cursor `{"i": 20}`) and its clustering step now runs at a
usable speed (§0). Check it landed:

```bash
sqlite3 db.sqlite3 "SELECT count(*) FROM deforestation_events
  WHERE park_id='XSA_Study_Area' AND polygon_ids LIKE 'deforest_hansen_%'"
python3 scripts/aoi_runner.py --status | grep hansen   # 'done' = it committed
```

⚠️ Note the column is **`polygon_ids`**, not `event_id` — rev 4's snippet named
a column that does not exist, so it errored rather than reporting 0.

### The `osm` unit was writing where nothing reads (fixed 2026-08-07)

`run_osm` keyed `osm_places`/`roads_heigit` by an **`aoi:<id>` scope key** on the
theory that AOI rows should stay out of the park id space. But that is what
`aoiExcludeSQL()` is for, and **no read path resolved the prefix**: the
`/infrastructure` handler, `/features?type=road|place`, the narratives and the
KML/Locus exports all key on the bare id. So the AOI's real OSM ingest —
**12,956 roads and 432 placenames** — was invisible to its own popup, which
showed only the 141 roads the clip preview had copied from neighbouring parks.
Days of ingest, unreachable. Exactly the same shape of bug as the animator's
missing 38,725 trajectories in rev 3, and the same lesson: **an `aoi:` prefix
that only the exclusion filter understands is a write-only key.**

Fixed by writing the bare id like every other unit (ghsl, hansen, gsw, hydro
all already did). After the re-key: roads 141 → 12,956, places 287 → 690,
road km 864 → 2,766. `aoiExcludeSQL` keeps its `NOT LIKE 'aoi:%'` clause for
any stragglers, and `DELETE` now removes both spellings — plus
`roads_heigit`/`park_rivers_hydro`/`park_lakes_hydro`/`park_waterbodies`, which
were **never in the delete list at all** (an AOI delete left them behind).

### `enrich_park_infra` has three modes, and the default is wrong for an AOI

It used to be "skip if this key already has rows", which is right for the
opportunistic park backfill and silently wrong for an AOI: the clip preview
always leaves rows, so the unit was a no-op end to end, and a multi-country AOI
ingested only its first country. Now:

* default — skip if rows exist (the park backfill; cheap, idempotent)
* `replace=True` — the AOI's **first** country, so the real ingest supersedes
  the clip preview atomically
* `append=True` — the AOI's **later** countries, deduped by `osm_id` because
  Geofabrik country extracts overlap at borders

XSA spans 3 countries and had 432 placenames for 485,000 km²; that was the tell.

**Never run two units at once.****Never run two units at once.** SQLite has one writer and the v5 chain holds
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
a **best-effort startup fixup**, `reownSystemAOIProgress()` in `srv/aoi.go`,
called from `NewServer` next to `SeedPrincipals`.

It was briefly a migration (044) and that was **wrong**: a migration that
cannot get a write slot fails `NewServer`, and systemd restart-looped the whole
service. A privacy tidy-up must never be able to take the site down. As a
warn-and-continue fixup it converges on the first boot that gets a slot.
Confirm:

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

### 4.0 ~~Verify `archive`~~ — done (§0)

Verified end to end on an idle database, 17 ms. The batch writers now yield
(`on_batch`, every 200 events). What is *not* done: the same treatment for the
other long writers — `rebuild_settlements_for_park` and the v5 fire chain still
hold the writer for their whole run. Neither has bitten yet because neither is
CPU-pathological the way `_get_fire_density` was, but the pattern to copy is in
`rebuild_deforestation_for_park`.

**Grep for the bug class, not the bug.** `ABS(<indexed col> - ?)` is the
signature; there may be more. Same for `strftime(acq_date)` and
`substr(acq_date,...)` in a WHERE clause.

### 4.1 Let `hansen` finish

Ingest is complete; clustering is running at a usable speed now. Just confirm
`--status` reaches `done` and the event count settles.

### 4.2 ~~Confirm the aoi_progress re-key ran~~ — done

It converged on the first boot after the writer was freed (§0): both
`aoi_progress` rows are now keyed by the AOI id and
`/api/notifications?type=aoi_progress&pwd=test2026` returns **0**. Three
sessions of "run this SQL by hand" were blocked by the same non-sargable query.

⚠️ **`db/migrations/044-aoi-progress-reown.sql` was still on disk** despite rev 4
recording it as reverted, and it restart-looped the service at 06:30–06:34
(`failed to run migrations: ... database is locked`) until it was removed. When a
migration is downgraded to a startup fixup, **delete the file** — a doc note is
not a revert. Highest applied migration is 043.

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
  that was written and removed. Fix the writer instead — and first check
  whether it is *CPU*-bound rather than lock-bound (§0: it was, and 2.5 hours of
  "lock contention" was one non-sargable `ABS()`).
* An `aoi:<id>` scope key in a park-shaped table — no. Bare AOI id, and
  `aoiExcludeSQL()` for privacy. A prefix only the exclusion filter understands
  is a write-only key (§1).
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
| `srv/aoi.go` `reownSystemAOIProgress()` | closes the `SYSTEM`-keyed notification leak; startup fixup, never a migration |
| `docs/PLAN_AOI_OVERLAY.md` | design rationale + measured facts |
