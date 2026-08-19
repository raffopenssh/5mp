# Areas of interest (AOI)

_Split out of AGENTS.md. Read when working on this area._

## Areas of interest (AOI)

A user-drawn polygon promoted to a first-class object: arbitrary geometry, a
fixed analysis window, an owner, and data fetched *for it* over days by a cron.
Instance #1 is `XSA_Study_Area` (485,150 km², owner `$AOI_OWNER_PWD`
in `secrets.env` — never spell a live password into a tracked file).
Full handover: `docs/PLAN_AOI_OVERLAY.md`.

**Current handover: none.** `docs/PLAN_AOI_OVERLAY.md` is the design rationale
and the measured-facts record; everything operational is below. The AOI work is
complete — read path, write path, queue, exports, animation, focus mode, abort,
versioning, star report and the admin Access tab are live, and all 11 datasets
for `XSA_Study_Area` are `done`:

    3.18M detections -> 38,725 trajectories · 2.23M GFW alerts -> 696 events
    76,903 Hansen polygons -> 7,079 events · 74,904 built-up polygons -> 1,552 settlements
    3,169 waterbodies · 11,370 rivers + 2,530 lakes · 3 country PBFs
    22 upstream watersheds + 24 downstream traces · 12,956 roads · 690 places

`park_basin_parts` is backfilled for **all 164 areas** (883 rows, 0 unsplit).
Highest migration is **051**.

### Verification set — re-run after any AOI work

```bash
source secrets.env; P="$AOI_OWNER_PWD"   # never hard-code the password
python3 scripts/aoi_runner.py --status                             # all done, no lease
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
go test ./srv/ -run 'TestGeoMemo|TestNarrativeSourceRev'           # cache equivalence
./tests/run_all.sh                                                 # db 37, api 45, ui 20
```

`go test ./srv/` fails on `TestServerSetupAndHandlers`
(`035-test-env.sql: no such column: avg_speed_kmh`). **Pre-existing, unrelated
to AOI** — verified by stashing. Do not chase it as an AOI regression.

### The recurring failure mode: a no-op that reads as an answer

Five times, days of ingest were silently unreachable or unfetched while every
layer reported success. The tell is always **a number suspiciously round for its
input size** (0 watersheds for 485,000 km²; 141 roads for three countries), and
the cause is always a filter that matched nothing while exiting 0. Two rules
fell out and are load-bearing:

1. **A unit that produces nothing for a large input must report unfinished**, so
   the queue retries instead of freezing a wrong answer as `done`
   (`run_basin` returns `ok = (rows > 0)`).
2. **An `aoi:` prefix only the exclusion filter understands is a write-only
   key.** Bare AOI id in park-shaped tables, plus `aoiExcludeSQL()`.

The sixth variant was a *timeout* reading as an empty section — see "Narrative
caching" below.

### API surface

`/api/aois/{id}/*` = the park handlers wrapped in `aoiGate()`; one visibility
check covers them all.

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
POST   /api/aois/{id}/export.gpkg       everything, typed + styled for QGIS (a job)
POST   /api/aois/{id}/mbtiles           + GET .../mbtiles/estimate
POST   /api/aois/estimate               side-effect free; call it while dragging
POST   /api/aois                        create + seed queue (runs nothing)
POST   /api/aois/{id}/{edit,restore,refresh,kick,archive,cancel}
POST   /api/aois/{id}/rename            label only: keeps the id, forks nothing
POST   /api/aois/{id}/refresh?resume=1  the inverse of cancel
DELETE /api/aois/{id}
GET    /api/admin/access                Access tab: ownership + queue (scoped!)
POST   /api/admin/aoi-dataset           enable/disable one dataset (owner-only)
```

**Deliberately absent, a decision not a gap**: `stats`, `species`, `climate`,
`publications`, `legal`, `checklist`, `turbidity`. Per-protected-area facts;
averaging them over 485,000 km² would invent a number. The popup and the report
say so and point at the intersecting parks; `fetchParkReportData()` and
`fetchPopupRoadData()` skip them rather than eating 404s.

More load-bearing details, each already got wrong once:

* **No endpoint runs the ingest.** `scripts/aoi_runner.py` owns lease
  discipline; `kick` shells out to it. One implementation of "work a unit".
* **Persistence + cropland ride the units, not just the nightly rotations.**
  `enrich_area()` in the runner calls `ghsl_epochs` + `cropland` derives when
  the `ghsl` unit finishes (persistence + cropland) and when
  `deforestation`/`hansen` finish (cropland). Without this a new AOI spent its
  first day(s) unmeasured, and worse: the 06:20 cropland rotation once ran
  *before* aoi_Serra_Bonita's ingest and stamped "nothing to measure" at
  current version, freezing the no-op (invariant 1). `cropland.pending()` now
  also re-queues areas with unmeasured rows behind a current stamp, so the
  rotation is the safety net and the inline call the fast path. A failed
  derive leaves the unit `pending`; the retry skips the tiles (cursor done),
  re-clusters idempotently and re-derives.
* **`DELETE` order**: the `aois` row goes *last* — while it exists,
  `aoiExcludeSQL()` still masks derived rows not yet deleted. The delete list
  must include `roads_heigit`, `park_rivers_hydro`, `park_lakes_hydro`,
  `park_waterbodies`, `park_basins`, `park_basin_parts`, `park_basin_rivers`.
* Non-owners get **404, not 403** (`requireAOIOwner`) — an id must not be an
  oracle. Same reason `?aoi=` on a raw-geography endpoint is *ignored* when
  invisible rather than refused.
* `validateAOIGeom` caps at 2,000 vertices (re-parsed by every runner, traced by
  the animator's canvas clip every frame).

### Narrative caching — one approach for parks and AOIs

`HandleAPIDeforestationNarrative` enriches **every** event with nearby places,
rivers and roads: ~21 ms of queries per row. A park has hundreds of events
(CAF_Chinko 273 → 1.0 s) so it was never visibly slow; XSA has 7,815 → **2 m
27 s**, past the 120 s `WriteTimeout`, so the AOI popup's deforestation section
rendered as if the area had no data. The AOI did not break the handler, it made
an existing O(events) cost visible. Two fixes, both shared by parks and AOIs:

1. **`narrative_cache`** (migration 045, `srv/narrative_cache.go`) — the same
   cache-first shape as `fire_narrative_cache`, but **self-invalidating**:
   `source_rev` is `COUNT + MAX(id)` of the source rows, so any rebuild
   (python, the AOI runner, a manual reclassify) invalidates it without
   knowing the table exists. That is deliberately *not* the fire cache's
   Single Writer Rule: the fire cache holds v5 hash feature_ids only the python
   builder can mint, whereas this holds a pure function of `deforestation_events`
   that any reader can recompute. Keyed by `park_id`, and an AOI id **is** a
   `park_id` in every park-shaped table, so one code path serves both.
   `params` is the date window; only the 6 most recent per (park, kind) are
   kept, or slider-dragging grows a 1.8 GB database by 10 MB a time.
2. **`geoMemo`** — answers "what is near this point" per 0.25° cell instead of
   per event, by fetching a superset window once and filtering in Go. **Exact,
   not approximate**: `TestGeoMemoMatchesDirectQueries` compares 200 random
   points across 4 areas against the direct queries. Where exactness cannot be
   promised (the rivers query's `LIMIT`, if the superset itself truncates) the
   memo *declines* and the caller runs the original query.

Result: 2 m 27 s (timeout) → 10.5 s cold → 0.09 s warm, park output
byte-identical. **Any new per-event enrichment must go through the memo**, and
any new expensive narrative should take the `narrativeSourceRev` /
`getCachedNarrative` / `putCachedNarrative` route rather than inventing a
second cache.

### Geography layers are served whole and cached

`river`, `road`, `place` and `waterbody` on `/features` are **static per
ingest**, carry no date filter, and are the whole answer or nothing — pinning
"rivers" means the river network, not its 500 longest reaches. They used to cap
at 500 default / 2,000 max, which for a park was invisible and for
`XSA_Study_Area` silently dropped 17k of 19k rivers and 11k of 13k roads. It was
never AOI-only: `DZA_Ahaggar` has 32,977 river reaches, `DZA_Djurdjura` 46,866
roads, `COD_Virunga` 11,107 places — every enriched park was truncated too.

Fixed 2026-08-07 in `srv/feature_geo_cache.go`: whole layer by default, gzipped
into `narrative_cache` under `kind='features:<type>'` with the same
self-invalidating `COUNT+MAX(id)` `source_rev`, plus an ETag so a re-pin is a
304. XSA river: 34 MB, 2.2 s cold -> 0.47 s warm -> 0.004 s revalidated.

* **`&limit=5000` means "everything"**, not a cap (`geoFeatureWholeLimit`). It
  was only ever the old ceiling, and old share links and pinned-layer restores
  still send it. Only a deliberately *small* limit bypasses the cache — caching
  a truncated answer under the full key would poison it.
* **`geoFeatureSources` maps a type to every table its output depends on.**
  `place` lists three because it suppresses a place point whose name a river or
  road line already carries (OSM records "Chinko" both as a waterway node and
  on the reaches, so the map drew a village dot on the labelled river). A
  roads re-ingest therefore changes the *places* answer.

### Detail tiers: `major` / `main` / `all`

Serving the whole layer is honest but rarely what you want on screen: XSA's
road layer is 6,458 footpaths and 3,642 tracks around 114 trunk/primary roads,
and its river layer 14,011 order-1/2 headwater stubs around 549 major reaches.
At continental zoom that is a blue smear that hides the things it is drawn to
show. `?detail=` on `/features` picks a tier for `river`, `road` and `place`
(`geoDetailSQL` in `srv/feature_geo_cache.go`); `/infrastructure` returns
`summary.detail_counts` so a button can print the number of features it will
actually draw.

* **A tier is a WHERE clause, never a LIMIT.** It must be the *same* subset
  every time and at every zoom, or a share link stops reproducing a picture.
  That is also why there is no zoom-driven auto-tier.
* **Rivers key on `stream_order`, not on having a name.** 78% of XSA's order-4
  reaches are unnamed and an unnamed Nile tributary is still a major river.
  Works across both sources because `osm_hydro.py` maps river=4/canal=3/
  stream=2 onto the same scale as HydroRIVERS' Strahler.
* **Roads key on `highway_type`**, the only classification present for every
  row — HeiGIT's `surface`/`dl_class_2024` is null for the OSM-enriched
  majority.
* Each tier is a **separate cache row** (`params=<tier>`, `''` for `all` so the
  pre-existing rows stay valid) with its own ETag.
* Unknown or absent `detail` is `all`, never a 400: old share links and pinned
  layer restores predate the param.
* UI: one segmented control (`.geo-detail-seg`) above the Rivers/Roads/Places
  buttons it governs — one, not three, because the tiers mean the same thing
  for all three and the whole point is that two areas on screen agree. Global
  (`window.geoDetail`, default **`main`**), rides in the share link as
  `?detail=`, omitted at the default. `setGeoDetail()` re-pins affected layers
  in place rather than making the user unpin and pin again, and
  `restorePinnedFromURL()` reads `detail` **before** fetching any layer.

### `polygon_ids` LIKE joins are the same trap as `ABS()`

`park_settlements.polygon_ids` and `deforestation_events.polygon_ids` are
comma-separated lists of `feature_geometries.feature_id`. Joining on them in SQL
means `(',' || polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')`, which
pairs *every* event with *every* polygon and runs a string search on each: park
scale (hundreds x thousands) hid it, XSA scale did not. Pinning "all
settlements" from the AOI popup took **29 s** (1,552 x 74,904) and all
deforestation **13 s** (7,815 x 80,408) — the same class of failure as the
non-sargable `ABS()` above, and with the same tell: fires, which don't join,
were instant.

Fixed 2026-08-07 in `srv/feature_meta.go` — one scan of the small events table,
split in Go into a `feature_id -> meta` map. 0.08 s / 0.14 s, output
**byte-identical** for CAF_Chinko, COD_Virunga and XSA. `event_id=` likewise
resolves `polygon_ids` first and uses `IN (...)`. Don't reintroduce the join:

```bash
grep -rn "polygon_ids || ','" srv/
```

Two things that look like AOI bugs and are not: `settlement-intensity` returns
an empty FeatureCollection (`settlement_intensity` has rows for 3 areas total —
parks included), and `feature-stats.road_segments` is 0 because roads live in
`roads_heigit`, not `feature_geometries` (CAF_Chinko reports 0 too). Both are
pre-existing park behaviour, identical for AOIs.

An AOI is a **power bounding box**: kept, owned, versioned, with data fetched
*for it* over days — as opposed to "Select Area", which is a disposable filter
over data we already hold. The UI keeps them in separate filter sections for
exactly that reason; don't merge them back.

Two traps that cost time: handlers needing geography must use
`resolveAreaGeom(id)` (an AOI is never in `AreaStore`, so the old loop yields
an empty boundary and KML silently loses its patrol effort), and any query over
`notifications` without an explicit park_id needs `aoiNotifSQLFilter()` — an
`aoi_progress` row is keyed by the AOI id and carries its name.

**An AOI is not a park.** Own table (`aois`), own id space, own route prefix.
Never in `keystones_with_boundaries.json`, never a
`fire_detections.protected_area_id` — otherwise `park_assigner` reassigns
detections away from the four parks XSA overlaps. `--aoi` on the v5 scripts
injects it into the **in-memory** parks dict only.

But its *derived* rows do live in park-shaped tables (`feature_geometries`,
`park_settlements`, `osm_places`, …) keyed by the AOI id. Two consequences,
both load-bearing:

1. `ParkIDMiddleware` 404s on any id in the AOI set, so `/api/parks/{aoi}/*`
   cannot serve a private AOI unchecked. AOI data is reachable only through
   `/api/aois/*`, where `aoiGate()` applies one visibility check to the
   otherwise-unmodified park handlers. In the frontend, **always build these
   URLs with `apiBase(id)`**.
2. **Any query over those tables that does not take an explicit `park_id` must
   apply `aoiExcludeSQL(col)`** (`srv/aoi.go`) — the same shape as
   `scannerInjectedSQLFilter()`. Without it, bbox-keyed endpoints leak private
   rows *and* double-count the AOI over the parks it overlaps.
3. **Four writers share `(park_id=<aoi>, feature_type)`** in
   `feature_geometries`: `aoi_clip.py` (settlement/deforestation preview from
   neighbouring parks), the v5 fire chain (`fire_trajectory`), the
   `deforestation` unit (`deforest_gfw_%`, derived from the AOI's own GFW
   alerts — not Hansen), and the `ghsl` unit (`settlement_ghsl_%`, from
   built-up-surface tiles). Each is only safe because it deletes a **disjoint
   id prefix**. A fifth writer needs the same treatment. A layer listed in
   `aoi_clip.SUPERSEDED_BY` is also no longer clipped once its real ingest is
   `done` — the real ingest covers the whole polygon *including* the ~10%
   inside parks the preview stood in for, so keeping both double counts.

GHSL settlement polygons come from `scripts/ghsl_tiles.py` (R2023A E2030 100 m,
cached **by tile id** in `data/ghsl/tiles/`, shared by parks and AOIs alike).
The JRC tile grid is **1-indexed**; an off-by-one silently reads a window
2,000 km away. The 10 m product is not published as tiles.
`rebuild_events_enhanced.rebuild_settlements_for_park()` is the one clusterer +
classifier for a single park or AOI — don't write a second one.

UI: an AOI animates as its **polygon** (`Animator.open({aoi})` → `clipGeom`),
not its bbox; share links use `?aoi=`/`aoi_sections=`/`anim_aoi`, deliberately
separate from `?popup=`/`sections=` which resolve against the `areas` source an
AOI is never in. Anything wanting a specific date window must go through
`setTimeSliderRange()`, not `dateFrom`/`dateTo`.

**Focus mode** (`?aoi_focus=`) makes an area the subject of the whole map: parks
outside it are dimmed (never hidden — the outline shows the polygon crossing a
boundary), **starred parks are never dimmed** (a star is explicit and outranks
an implicit scope), and the bbox feature layers, the animator and the star
report all switch to that area's own rows. `aoiFocusBrightIDs()`
returns `null` — not `[]` — when the park list did not resolve, or the whole
world greys out. `var aoiFocusID`, not `let`: `updatePAHighlighting()` reads it
during map setup, thousands of lines above the declaration.

### A PARK IS AN AREA TOO (2026-08-12)

Focus was AOI-only. The cost was that the object this map is mostly made of had
no gesture that made its numbers agree: with Chinko on screen the stats panel
counted the continent, the popup counted Chinko's 27 settlement clusters, and
the viewport readout counted the 35 built-up polygons it had drawn — **three
numbers for one word**, none of them wrong, none of them saying what it was
counting.

`aoiFocusID` now holds **either kind of id** and everything branches on
`focusIsAOI()`. The variable keeps its name: ~25 call sites and one share-link
parameter (`?aoi_focus=`) already say it, and generalising a thing is not the
same as renaming it.

| | AOI | park |
|---|---|---|
| scope param | `?aoi=` | `?park_focus=` |
| server | `aoiScopeParam`/`aoiScopeSQL` | `areaScopeParam`/`areaScopeSQL` (accepts both) |
| bright set | `aoi_parks` lookup, async, may fail | just the park itself |
| camera | `zoomToAOI()` (stored bbox) | `zoomToPark()` (measured off the `areas` source) |
| animator | clips its canvas to the polygon | scopes its fetches only |

⚠️ **`?park_focus=`, never `?park=`.** `ParkIDMiddleware` validates every
`?park=` in the app and **404s an AOI id in one**. Reusing it would make "focus
on this area" a hard failure for half its inputs — the same trap `?area=` exists
for on the bbox endpoint. One parameter name per kind, chosen in one place
(`focusScopeParam()` in globe.html), so the stats call and the LOD loader cannot
drift.

⚠️ **A focused park is not in `window._aois`.** `loadAOIs()` clears a focus
whose AOI has vanished; that guard must ask `focusIsAOI()` first, or every list
refresh silently drops a park focus. Same shape as the restore path: the
share-link restorer defers until `AOI_IDS` is populated, because before that
`focusIsAOI()` would misread an AOI id as a park and scope the session with the
wrong parameter.

The control appears wherever the AOI's does, with the same `icon-focus` and the
same `aria-pressed`: the park tag in the filter panel (`.park-tag-btn`, violet
rather than the AOI's amber — one gesture, and the colour says which kind of
area), the park popup header, and the scope line, whose name now opens
`showPAPopup` or the AOI popup as appropriate.

**The stats panel is where focus states itself, and its numbers obey it.**
`/api/stats` takes the same visibility-checked scope as the feature endpoints
(`areaScopeParam` → `areaScopeSQL`) and echoes `scope`/`scope_name`. That is not a
contradiction of aoi.go's "endpoints that SUM must use `aoiExcludeSQL`" — that
rule is about the *default* answer, where adding an AOI's rows to the parks it
overlaps double-counts the overlap; with an explicit scope the area's own rows
*are* the complete answer for that geometry and the others would be the
double count. Non-owners get the global answer and no `scope` key (pinned by
`stats_aoi_scope_ignored_for_non_owner`).

The focus readout lives in that panel too (`.stats-scope`, written by
`updateAOIFocusBanner()` — name kept), *not* in `#top-chips` and not in a banner
over the slider. Both earlier homes were an always-on overlay announcing a scope
while the panel a few centimetres away reported the whole continent. The numbers
are the one thing focus changes invisibly, so the statement and the × belong on
top of them. `loadStats()` only decorates it with the server's confirmation
(`.unscoped` when the scope was declined); one writer, so the readout and the
toggle cannot disagree.

**A stats row and a pin can be the same layer.** Under focus the viewport layers
are scoped to the AOI too, so "XSA's fires" (a pin) and "fires in view" (the
row) fetch identical rows — two 3 MB requests, doubled ink, and one hover
answered twice. `duplicatePinKey()`/`reconcileViewLayerDuplicates()` make the
row **mirror** the pin instead: it shows on, shows the pin's count and its
detail control, and switching it off removes the pin. Deliberately one
direction — the pin is the named, shareable object, so it survives; a filtered
pin (`classification`) is a different answer and is never mirrored; without
focus a pin is a subset of the view, not a duplicate. `var mirroredPins`, not
`let` (`updateViewLayerUI()` is declared far above it).

The mirror must remember **whether the row was on before it took over**:
`mirroredPins[name]` is `{ key, wasOn }`. Mirroring sets `viewLayers[name] =
true` (it IS on screen), so when the pin is later removed, restoring
"whatever `viewLayers` says" resurrects the layer as a stats-row copy — that
was the 2026-08-16 bug where unpinning settlements left them drawn (share
link j723245: pin fires, pin settlements+deforestation, unpin settlements →
settlements stayed). On unpin the row reverts to `wasOn`; the row's own ×
while mirrored clears `wasOn` too, or reconcile would restore the pre-mirror
state the user just declined.

**archive ≠ cancel ≠ delete ≠ supersede.** `archive` hides the overlay and
touches nothing else — ingest keeps running, so unhiding shows an answer rather
than a progress bar. `cancel` disables unfinished datasets but keeps their
**cursors**, so `refresh?resume=1` resumes without re-spending FIRMS quota.
`delete` drops everything. An **edit forks**: v1 is archived and its queue
disabled, which looks identical to a cancel from outside — so `/progress`
reports `state:"superseded"` and `refresh?resume=1` **409s**, because resuming it
would re-spend days of quota on a question v2 already replaced.

`archive` works, verified end to end in 17 ms. The earlier "blocker" was never a
handler bug: `rebuild_deforestation_for_park` was **CPU**-bound on a
non-sargable `ABS()`, not waiting on the write lock. Before blaming SQLite's
single writer, check `ps` — a process waiting on a lock is `S`, not `R`.

Old `aoi_progress` rows keyed `park_id='SYSTEM'` leaked every private AOI's name
to every principal (`aoiNotifSQLFilter` reads visibility from `park_id`). Fixed
by `reownSystemAOIProgress()`, a **warn-and-continue startup fixup** in
`srv/aoi.go` — emphatically not a migration: as one it failed `NewServer` when
the write lock was held and systemd restart-looped the service. A privacy
tidy-up must never be able to take the site down.

The runner treats **interruption as its normal exit**: out of time, Ctrl-C or
SIGTERM all release the lease and resume next run with no cooldown, dead-pid
leases self-heal, and bookkeeping writes wait out the v5 chain's long hold on
SQLite's single writer. Never run two units concurrently — that is what
stranded three leases on 2026-08-07. `scripts/test_aoi_resume.py` proves the
guarantee; run it after any change to the lease/cursor code.

Pre-2024 deforestation for an AOI comes from **Hansen**, not GFW alerts
(`scripts/hansen_loss.py`, wired as the `hansen` unit 2026-08-07): tiles are
45-116 MB COGs read through `/vsicurl` in 0.6 s per 2-degree window, no
download and no quota, while GFW integrated alerts only start in 2024. Cutover
is Hansen <=2023 / alerts >=2024, matching the parks exactly so the numbers stay
comparable. **Onboarding a park runs it too** — before this, a new park showed
two years of loss beside 161 parks showing twenty-four.

Two traps the plan for it did not mention: the unit costs **~50 s per 2-degree
window**, not the 0.6 s of the read (the cost is polygonising the mask), and
polygons alone are invisible — the popup and narratives read
`deforestation_events`, so it finishes by clustering through
`EventRebuilder.rebuild_deforestation_for_park(park, id_prefix=...)`, the mirror
of `rebuild_settlements_for_park`. `id_prefix` scopes read *and* delete, so
Hansen and the GFW unit own events in one table for one park without erasing
each other.

**`gsw` and `hydro` now have runners** (2026-08-07), so every dataset does.
`gsw` = `scripts/gsw_water.py`: the "missing" JRC occurrence tiles are public
COGs, `/vsicurl` reads a 1-degree window in 0.55 s. It writes the parks' own two
`waterbody_type` values ('Inland perennial' >=75%, 'Inland intermittent'
25-75%) under a `gsw_` id prefix, so exports and narratives need no second path.
`hydro` = `scripts/osm_hydro.py`, because **HydroSHEDS cannot be fetched
unattended at all** — `data.hydrosheds.org` 403s every request behind
Cloudflare. It fills `park_rivers_hydro`/`park_lakes_hydro` from the country PBF
the `osm` unit already downloads, with **negated OSM ids** (HydroSHEDS ids are
positive, so `< 0` is provably ours) and a tag-derived `stream_order` band, not
Strahler. It is **not** the `basin` unit: mghydro/MERIT answers "what drains
through here" and carries no river names; OSM answers "what is this called",
which is what the narratives and KML folders key on. Both ship.

**AOIs are global since 2026-08-19** (Serra Bonita/BRA). `aoi_countries()`
intersects the polygon with `data/world_countries.geojson` (NE 50m), and
`osm_pbf.ensure_pbf` resolves non-African ISO3s via
`data/geofabrik_countries.json` (180 countries; both files generated by
`scripts/build_geofabrik_countries.py` — re-run if Geofabrik reshuffles).
The hand-maintained `GEOFABRIK` dict stays authoritative for Africa (local
staging in `data/osm_geofabrik/` — CAF+SSD — and groupings like
senegal-and-gambia). Downloaded PBFs are registered via
`keep_for_run(path, aoi_id)` and swept by `sweep_run_pbfs(conn)` only once
the AOI's `osm` **and** `hydro` datasets are done — the two units used to
download the same 2 GB Brazil PBF twice in five minutes. Also fixed then:
`_get_nearest_place` returns `(None, None)`, a *truthy* tuple, which crashed
the hansen/deforestation narrative on any AOI whose events are >330 km from
a known place (i.e. any AOI ingested before its own OSM unit ran).

```bash
python3 scripts/aoi_runner.py --status          # queue state
python3 scripts/aoi_runner.py --heal            # reclaim dead-pid leases
python3 scripts/aoi_clip.py --aoi XSA_Study_Area  # Phase A preview, ~4s
# cron: 0 12 * * *  aoi_runner.py --daily  (deliberately far from the 3am fire job)
```

**Every long writer now yields.** `rebuild_{deforestation,settlements}_for_park`
and the v5 fire chain (`load_fire_groups_to_db.py` every `BATCH_ROWS = 200`
groups, `precompute_narratives_v5.py` every 25 cache blobs) commit in batches, so
SQLite's one writer is free between them and a user toggle can always get a slot.
Safe because both are idempotent: the run deletes its own rows first and every
insert is an `INSERT OR REPLACE` keyed by id.

**Admin → Access tab** (`srv/aoi_admin.go`, `GET /api/admin/access`,
`POST /api/admin/aoi-dataset`) shows AOI ownership and per-dataset queue state,
plus enable/disable and "Run now". It is **scoped to the caller's principal**, not
global: `RequireAdmin` is satisfied by any valid password, so a global view would
leak every tenant's polygons. `principals.label` (`pwd[:3]+"…"`) is never served —
the handle is the non-secret `sha256(pwd)[:8]`.

**`aois.state` is never `'ready'`** (only `archived`). Readiness is *derived* from
the queue — `/progress` and the Access tab both do this. Printing the raw column
labels a fully ingested AOI "pending" forever.

### Frontend map

| piece | where |
|---|---|
| routing | `apiBase(id)` in globe.html; `window.AOI_IDS` filled by `loadAOIs()` |
| map layer + popup | globe.html `loadAOIs`/`showAOIPopup`/`aoiCoverageHTML`/`renderAOIOverviewHeader` |
| actions | `aoiActionsHTML(id, name, {isOwner, archived})` — one row, used by tip *and* popup |
| export menu | globe.html `toggleAOIMenu`/`aoiExportMenuItems` — `#aoi-menu` on `<body>` |
| rename | globe.html `startAOIRename` / `renameAOITag` → `POST /api/aois/{id}/rename` |
| filter section | globe.html `#aoi-section` — own heading, amber, own visibility toggle |
| editor | `srv/static/aoi_draw.js` — `AOIDraw.start()` / `.startEdit(id, name, geom)` |
| progress card | `srv/static/aoi_progress.js` — `AOIProgress.cardHTML(notif)` |
| animation | `Animator.open({aoi})` clips to the **polygon**; loaders append `&aoi=` |
| focus | globe.html `setAOIFocus`/`toggleAOIFocus`/`aoiFocusBrightIDs`/`applyAOIFocusPaint` |
| report | `collectReportParks()` folds in every visible AOI, first; visible = starred |
| admin | globe.html `loadAccessTab`/`setAOIDataset`/`kickAOIRunner` → `srv/aoi_admin.go` |

Things that will bite:

* **`window.map` is the `<div id="map">` ELEMENT** (named access on window). Use
  the bare lexical `map`. Symptom: `m.getSource is not a function`.
* **A `const` in one `<script>` block is invisible to another.** globe.html has
  several; `AOI_REPORT_SECTIONS` is mirrored onto `window` for that reason.
* Focus paint is layered **on top of** selection paint, not woven into it;
  `resetAOILayerPaint()` exists because the paint is not idempotent — re-apply
  after a basemap change (`updateParkFillForBasemap` owns `fill-opacity` too).
  Dim colour `#5b6b5f` / 0.55, **not** `#3f3f46` / 0.3, which erased 158 parks
  at continental zoom.
* `can_create` comes from the server — a password can arrive as a cookie, so
  `getPwd()` cannot decide whether `POST /api/aois` 403s.
* The progress card is **not client state** — it is a `notifications` row, so it
  survives a laptop shut for a week. Polling is adaptive and *stops* at `ready`,
  `cancelled` and `superseded`. `datasets_total` is the **planned** count, so a
  stopped queue reports 0; the card adds `datasets_stopped` back for the
  denominator, or it reads "0/0 layers" beside "0 of 11 were fetched".
* **Pins are namespaced for an AOI** (`aoi:<id>:<type>`). Ids are disjoint
  today; the flat park key `<id>_<type>` would have made a same-named AOI and
  park share one pin.
* **An AOI wears the park popup's controls, not its own.** One row of
  `.pa-export-btn` squares beside the title (Focus / Export ▾ / Edit) plus the
  ordinary `.star-btn` — `aoiActionsHTML()` renders it once for both the map tip
  and the popup. Two earlier versions were wrong in opposite directions: eight
  bare icons crushed beside the title (name wrapped one letter per line), then
  nine labelled buttons in three groups (View/Download/Manage), which is a form,
  not a tip. The four downloads are **one** category, so they live behind one
  button in `#aoi-menu`; the menu is appended to `<body>` because the map tip is
  an `overflow:hidden` box rebuilt on every mousemove, which both clips a child
  menu and destroys it under the cursor.
* **Animate is not in the AOI's action row.** The ▶ chip lives in the time
  slider, where a time window is chosen. `animateAOI()` still exists and is
  still what focus uses.
* **The star *is* the hide control.** For an AOI, visibility and report
  membership are the same fact (`collectReportParks()` folds in every visible
  AOI), so one ★ says both. Un-starring calls `/archive`; the polygon fades via
  `fadeAOILayer()` before the request so the click reads as immediate, the toast
  carries **Undo** (`showToast(..., {action})` → `unhideAOI()`) *and* names
  search as the permanent way back. Deliberately no `confirm()`: a modal asks
  the user to predict the result, an Undo lets them see it. "Hidden", not
  "archived", in the search result chip — hiding is what they did.
* **Renaming is not editing, and has no pencil.** `POST /api/aois/{id}/rename`
  keeps the id, so every share link, pin key and park-shaped row keyed by it
  survives; an *edit* forks because the question changed. Click the name and it
  becomes a field (`.aoi-editable-name`, hover underline). A pencil icon would
  be a second edit affordance beside the real one and would cost a slot on the
  row this whole layout exists to free.
* **`.maplibregl-popup-content` is capped at 280px** (globe.css). The AOI popup
  carried `min-width:300px` on its `.pa-popup` child, so every section stuck
  20px past the panel's right edge and the accordion rows looked sheared off.
  It is 260px now. A popup's own min-width must stay under that cap.
* **A sticky map tip and the popup are the same answer twice**, and the tip is
  anchored at the cursor, i.e. on top of the popup it just opened.
  `showAOIPopup()` calls `MapTip.hide()` first — and so does `showPAPopup()`.
* **The AOI tip is `clickOnly`, and every backdrop must be.** See
  "Hover tip precedence" below: a tip that follows the cursor across 485,000 km²
  has no "off it" to move to, so it hid whatever the user was reaching for.
* **Overview & coverage opens collapsed.** Once ingested it is a wall of 100%s,
  and the popup was opened to see fires and settlements. The header carries
  `{done}/{total}` and, only while work is outstanding,
  `renderAOIOverviewHeader()` draws a slim amber aggregate bar (weighted by
  `units_done/units_total`, so it moves during a long unit) plus "data below is
  partial". No bar at 11/11 — a full green rule is decoration.
* **The AOI popup has the Roads/Rivers/Places & Infrastructure section too.** It
  was simply never added, though `fetchPopupRoadData()` had already been taught
  `apiBase()`/`isAOI()` for it. `?aoi_sections=road` opens it.
* **`FloatUI.decoratePAPopup()` bails on `.pa-popup-name > span:last-child`.**
  It was written as a "does this look like a PA popup" guard; the AOI header put
  a second span after the name and the guard silently returned, taking the grab
  bar, the minimise button and MapLibre's × with it. Now `> span`. Anything
  added to a popup header must keep that selector matching.
* **The focus state is a chip in `#top-chips`, beside the session chip** — not
  a banner over the map. It was a pill floating above the time slider, measured
  against the slider's changing height (`positionAOIFocusBanner()` + a
  `ResizeObserver`) because a hard-coded `bottom` covered the ▶ animate button
  once preset tags wrapped on a narrow phone. That is the wrong fix to a
  self-inflicted problem: "you are focused on X" is the same kind of fact as
  "you are signed in as X" — scope, not data — so it belongs in the same row,
  in the same clothes, with the same ×. One fewer always-on overlay in the
  middle of the map it is talking about, and mobile placement comes free from
  the row. `updateAOIFocusBanner()` keeps its name and still removes any
  `#aoi-focus-banner` a cached page is holding; `positionAOIFocusBanner()` is a
  no-op kept for old call sites.
* **The AOI tag in the filter panel carries three verbs, not five.** It had
  ◎ focus, ⌖ zoom, ✎ edit and ★ — four glyph families, two of which look
  like the same idea. Now: focus (`icon-focus`, the same icon as the popup's
  action row and the focus chip), edit, star. Zoom is the tag's own dead space
  (one tap, works on a phone); **renaming is clicking the name**, exactly as in
  the popup, instead of the old double-click-here / single-click-there split —
  and double-clicking the name zooms, so an impatient double-tap never lands in
  an edit box. A non-owner sees no rename affordance and the whole tag zooms.

### Versioning

An edit forks: version N+1 is created, N is archived (`state='archived'`, hidden
from `ListAOIs`, still readable by id, queue disabled). Versions are labelled by
their **analysis window**, not by number. An edit that changes nothing returns
`unchanged: true`; an edit that did not move a vertex sends no geometry at all.
Only the outer ring is editable — a hole or a multipolygon is `ringLocked` and
carried through untouched, because flattening a donut would change what the AOI
*means* while looking like a date-only edit.

### What is left

Nothing blocking. Two nice-to-haves:

* **A second AOI has never existed.** Everything is written to be per-AOI, but
  every measurement is n=1. The first thing a second one will exercise is tile
  cache sharing (`data/ghsl/tiles/`, `data/gfw_tiles/`, `http_cache`) and the
  runner's fairness across two pending queues — it currently takes them in
  `(priority, dataset)` order with no round-robin, so a big AOI can starve a
  small one for days.
* **`aoi_grants` has no UI.** The table, the `aoiVisibleSQL` clause and the
  Access tab's "Shared with" column all exist; nothing writes a grant. Sharing
  an AOI today means sharing the password.

### Do not re-litigate

* Hiding parks outside a focused AOI instead of dimming them — no. Nor dimming
  *starred* parks: a star is explicit and outranks an implicit scope.
* Archiving stopping the ingest — no. Archive is about the screen, `cancel` is
  about the quota.
* Offering Resume on a superseded version — no. Editing is the way forward,
  `/restore` the way back; `refresh?resume=1` 409s.
* Telling the user "database busy, try again" — no, that was written and
  removed. Fix the writer, and first check whether it is *CPU*-bound.
* An `aoi:<id>` scope key in a park-shaped table — no. Bare id +
  `aoiExcludeSQL()`.
* AOI rows in bbox-keyed endpoints **by default** — no. With an explicit,
  visibility-checked `?aoi=` — yes, `aoiScopeSQL()`, and exclusively.
* Requiring a *separate* star before an AOI enters the report — no. Visibility
  is the trigger, and the ★ in the UI **is** that visibility (un-starring hides
  it). An extra opt-in gave a user with one AOI and no stars an empty report.
  The star panel lists visible AOIs first, in their own section, and
  `updateStarBadge()` counts them.
* A separate "Hide" button beside the star — no, they were the same switch under
  two names, and "archive"/"hide"/"delete" for one action is how the old popup
  got to nine buttons.
* A global admin view of all AOIs — no (it would leak every tenant's polygons).
* Visibility-filtering `/api/fire-frames`, `/api/grid` — no. They serve raw
  geography that was always public within the app. **The polygon is the secret,
  not the pixels.**
* A privacy tidy-up as a migration — no. `reownSystemAOIProgress()` is a
  warn-and-continue startup fixup. When a migration is downgraded to a fixup,
  **delete the file** — a doc note is not a revert.
* Recomputing an expensive narrative per request because "a park is fast" — no.
  See "Narrative caching": park scale hid a 2m27s AOI timeout for months.

### Files

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
| `srv/narrative_cache.go` | `narrative_cache` + `geoMemo` (parks and AOIs alike) |
| `srv/errors.go` | `isDBLocked`, `execUserToggle` (not sufficient alone) |
| `srv/park_basins.go` | `loadBasinParts` = all watersheds; merged is the fallback |
| `srv/static/aoi_draw.js` | polygon editor + live estimate; `startEdit` forks |
| `srv/static/aoi_progress.js` | the multi-day notification card |
| `db/migrations/040..045` | overlays, parks, versions, pixel count, basin parts, narrative cache |
| `docs/PLAN_AOI_OVERLAY.md` | design rationale + measured facts |

---
