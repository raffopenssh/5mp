# Handover — smooth zoom LOD for map features + paused-animation GeoPackage

Status **2026-08-12 (session 2)**: server done (`f8438b8`, `45e77d8`); frontend
**mostly done** in this commit. Read "What is left" — there is **one measured
performance problem** and it is the next thing to do.

## The ask (user's words, condensed)

1. Zooming in should **smoothly transition** from fast point/heat rendering to
   **actual clickable vectors with a hover tip**. High performance throughout.
2. The **fire grid should rescale** so it stays meaningful as you zoom.
3. **Fire points** must work — they used to always say "zoom in more".
4. All of that must work **while an animation is paused**.
5. A paused animation needs a **GeoPackage of the exact current state**, next to
   the GIF button — *immediate when fast, a notification when larger*, deletable
   from the notification or after 21 days. Unlike the area/AOI export (which is
   everything we hold for an area) it exports **only what the user sees**.
6. **Pinned layers get the same treatment** — they hard-capped at `limit=5000`
   and never refetched.

The through-line: *the same feature must be the same feature at every zoom.* A
cheap rendering may not silently become a picture.

---

## ⚠️ THE ONE OPEN PROBLEM: `/api/fire-anim-trajectories` is slow

Measured 2026-08-12 on the live DB, `from=2024-06-01&to=2024-09-30`:

| bbox | limit | time |
|---|---|---|
| 20,-16,28,-8 (8°×8°) | 800 | **7.8 s** |
| 20,-16,28,-8 | 4000 | **17.0 s** |
| world | 800 | > 120 s (timed out) |

So the cost is roughly linear in `limit` *and* brutal in bbox. This is
pre-existing — 800 was always ~8 s — but it became visible because the animator
now has a reason to ask for more, and because I briefly raised the client's
limit to 4000 and the animator hung at "Loaded 1/2" for minutes.

**I reverted the client to `limit=800`** (`loadLayer` case `'trajs'` in
`srv/static/anim.js`, with a comment saying why). The server's cap stays at
12000 — that is correct and harmless; nothing asks for it yet.

**Do not raise the client limit again until the endpoint is cheap.** The endpoint
reads `data/fire_groups_v5/*.json` per park through a ~40-park LRU
(`srv/fire_frames.go`), so a wide bbox parses hundreds of MB of JSON per
request. Directions, cheapest first:

* Serve trajectories from `feature_geometries` (`fire_trajectory`) with the
  `idx_fg_bbox_scan` covering index — the same path `/api/features-in-bbox`
  already uses, which does a continental fire query in ~1.0 s. The JSON files
  carry the dated point list the animator needs; check whether
  `properties_json` already has enough, and if not, whether a compact dated
  polyline can be stored at build time (`load_fire_groups_to_db.py`).
* Or cache the endpoint's answer in `narrative_cache` with the
  `COUNT+MAX(id)` self-invalidating `source_rev` shape (`srv/narrative_cache.go`).
* Whatever you do, **measure with `curl -w '%{time_total}'` before and after**,
  at 800 and at 4000, small bbox and world.

---

## What shipped in this commit

### Server
* **migration 050** — `geopackage_jobs.view_json`. A view export's question
  (bbox, instant, chips) lived only inside `cache_key`. Without the column a
  card that outlives its tab cannot say what it is, and its "Try again" button
  silently rebuilt an *area* export instead. `GeoPackageJob.View` is scanned
  from it and served in the JSON.
* **`?wait=<seconds>` on `POST /api/view/export.gpkg`** (`waitForGeoPackageJob`,
  hard-capped at 20 s, well under the 120 s `WriteTimeout`). This is the
  "immediate when fast, notification when larger" rule. Nothing about the job
  changes: the card is still written at queue time, so a fast export is still
  in the bell, still deletable, still expires in 21 days.
* `gpkgTitleSuffix` prints a human instant ("view at 1 Jun 2025"), because two
  paused frames of one animation are two cards in one bell.

Measured: XSA 1°×1° 3-year `trajs,settlements` → **8.3 MB in ~1 s** (cached);
a fresh 1°×1° `trajs,deforest` → 1.8 MB well inside the wait; a 20°×25°
four-layer view → still `running` at 6 s, i.e. correctly becomes a card.

### `srv/static/lodlayer.js` (new) — one loader for every viewport feature layer
Replaces the two disagreeing loaders. Calls
`/api/features-in-bbox?mode=auto`; the **server** decides geometry vs centroids
from the true count in view. Both renderings share one source id and
**cross-fade** (200 ms) so crossing the threshold reads as focus, not a reload.
A centroid carries its row id, so hovering it fetches `/api/feature-detail` and
`MapTip.refresh()`es — verified: a dot at continental zoom shows the full fire
narrative tip.

Verified in the browser: continental → `points`, 30,000 of 45,327; zoom to 8 →
`geometry`, 1,128 of 1,128; zoom back out → `points` again, and the losing
layer set is removed after the fade.

* `park=` is sent for a park/AOI pin so panning cannot adopt a neighbour's rows.
* It **skips a refetch that cannot reveal anything** (previous answer covered a
  bigger box, was not truncated, was already geometry) — this is what keeps
  panning cheap; the old stats path refetched on every `moveend`.
* `reload()` is debounced (80 ms) because "the dates changed" is announced by
  several call sites.

### globe.html
* stats-panel toggles → `LODLayer` (`loadViewFeatureLayer` is now ~20 lines).
* pinned fire/deforestation/settlement → `LODLayer` too, marked `lod: true` on
  the `pinnedLayers` entry. `removePinnedLayer`, `refreshPinnedLayers` and
  `flyToPinnedLayer` all branch on it (`flyToAreaBounds` — an LOD pin holds
  what is on screen, so "fly to this pin" means "fly to its area").
* `updatePinnedIconState()` extracted so the LOD unpin path resets the popup
  icons too.
* A **classification filter still uses the old whole-park fetch** — that filter
  reads feature properties client-side and points mode deliberately ships none.

### `srv/static/maptip.js` — probes
`registerProbe(id, {probe(e), priority})`. The animator draws to a canvas, so
`queryRenderedFeatures` cannot see a single one of its trajectories; a probe
answers from arrays it already keeps and its result **competes in the same
priority ordering** as a real layer. Deliberately not a second tooltip.

### `srv/static/anim.js`
* **`#anim-gpkg` beside `#anim-gif`.** Pauses first (the instant is the
  question), sends `A.fetchBbox` / `A.fromISO` / `A.toISO` / `at=fmtDate(A.t)` /
  the chips that are on / `A.aoiID`, and hands the job to
  `GeoPackageExport.startView()` — **there is exactly one job watcher**.
  Verified end to end: a small view downloaded immediately with a toast; the
  card appeared in the bell; the bell's delete button removed it.
* **Fire grid rescaling.** Radius now derives from `cellPx`
  (`proj(lon+res)-proj(lon)`), like the effort layer, never from `zoom/5`.
  Three renderings of one layer, chosen by **cell size in screen pixels**
  (`GRID_CELL_PX_MAX = 10`), never by a zoom number:
  small cell → soft blob; large cell → **draw the cell** (a blob at the centre
  of a visibly large cell claims a peak the pre-agg tables never asserted —
  that was the halftone-lattice screenshot); zoomed right in → the layer has
  already swapped itself for **real detections** (`asPoints`).
* `onMoveEnd` refetches when **the LOD it deserves changed**, not only when the
  viewport left the fetched box. Without this the layer froze at whatever
  detail it had when the animator opened.
* **`firePts` no longer refuses locally.** The 40 deg² check is gone; the server
  gates on *estimated detections*, and `points_unavailable.estimate` words the
  toast ("1.2M detections in view — showing the density grid").
* **Interaction while paused** (`probeFrame`): nearest-point hit test over
  trajectories (reusing `projectTrajs`' cached screen coords), deforestation,
  settlements and fire points. Registered on pause, dropped on play — including
  when playback reaches the end on its own. `trajs` now keeps
  `fires/days/frp/narrative` so the tip needs no second request.

---

## What is left

1. **The trajectory endpoint** (above). Everything else waits on it.
2. **Raise the animator's `trajs` limit** once it is cheap — 800 of 38,725 in an
   AOI is a sample presented as an answer.
3. **A pinned LOD layer has no `-arrows` / river-label / place-label styling**
   (the legacy path builds those per type). Fire pins therefore lost their
   directional arrows. Either port the arrow layer into `lodlayer.js` for
   `render === 'geometry'`, or keep a legacy branch for it.
4. **`geoFeatureWholeLimit` interaction unverified**: old share links carrying
   `&limit=5000` mean "everything" on `/features`. The LOD path never sends a
   limit to `/features` at all (it uses `/features-in-bbox`), so nothing should
   break — but a share link that restores a pin was not tested end to end.
5. **`?spread=0` and `simplify=0`** are untouched escape hatches; no UI.

## Verify
```bash
./tests/run_all.sh                                    # db 37, api 45, ui 20 — passing
go test ./srv/ -run 'GPKG|QGIS|GeoPackage|AreaHit|Gzip|Tenant|RequestEnv'   # passing
python3 scripts/check_fire_consistency.py
```
(`TestServerSetupAndHandlers` fails pre-existing — `035-test-env.sql`, unrelated.)

Browser checks that caught real bugs this session, worth repeating:
```
?pwd=test2026&test=1&from=2024-06-01&to=2024-09-30&anim=fireGrid&anim_paused=1&anim_t=2024-06-24&lng=24&lat=-12&zoom=6
```
then `map.jumpTo({zoom:7.5})` and `{zoom:9}` — the grid must go blob → cells →
real detections, and must **re-ask on zoom** rather than freezing.

## Traps (each cost time here)
* **`window.map` is the `<div>`**; use the bare lexical `map`.
* A `const` in one `<script>` block is invisible to another.
* **Judging a render by a screenshot is the whole job here** — every fire-grid
  bug in this session passed all automated checks and looked wrong on screen
  (halftone dots, then a saturated white sheet, then a lattice).
* Never judge this work by frame rate alone: the failure being fixed is
  *information loss that looks like speed*.
