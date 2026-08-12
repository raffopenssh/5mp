# Handover — smooth zoom LOD for map features + paused-animation GeoPackage

Status **2026-08-12**: server side is done, committed (`f8438b8`, `45e77d8`) and
live. **Frontend is not started.** Read this file, then the two commit messages
(`git show --stat f8438b8`) — they carry the reasoning, this carries the plan.

## The ask (user's words, condensed)

1. Zooming in should **smoothly transition** from the fast point/heat rendering
   to **actual clickable vectors with a hover tip**. High performance throughout.
2. The **fire grid should rescale** so it stays meaningful as you zoom.
3. **Fire points** must work — today they always say "zoom in more".
4. All of that must work **while an animation is paused**.
5. A paused animation needs a **GeoPackage download of the exact current state**,
   sitting next to the GIF button.
6. **Pinned layers get the same treatment** — they currently hard-cap at
   `limit=5000` and a pin can be continental (stats-panel toggles pin the whole
   view, e.g. "1.500 of 328.167 fires").

The through-line: *the same feature must be the same feature at every zoom.* A
cheap rendering may not silently become a picture, and zooming in may not lose
information.

## What is already done (server)

### `/api/features-in-bbox` (`srv/features_bbox.go`)
* Pass 1 streams into `spreadCollector` — no `featureScanCap`, O(limit) memory,
  `total` is the true count in view at any zoom. Selection identical to the old
  `spreadSelect` (deterministic, best feature per cell).
* **`mode=auto`** — server decides geometry vs centroids from the true count vs
  `geom_budget` (default 3000). Response carries `render: "geometry"|"points"`.
* points mode now returns **`ids[]`** parallel to `points[]`.
* **`GET /api/feature-detail?id=<row id>`** — one feature, geometry + enriched
  properties. AOI rows 404 for non-owners.
* **`?park=<id>`** scopes a viewport answer to one area (for pins).
* settlement/deforestation get `narrative`/`classification`/`nearest_place`
  (`enrichFeatureProps` in `srv/feature_meta.go`), so the bbox path and the
  per-park path give the same tip.
* `limit` cap raised 20000 → 200000.

### `/api/fire-frames` (`srv/fire_frames.go`)
* `mode=points` gate is now **estimated detections** (`estimateFireCount`, sums
  `fire_grid_day`, ~10 ms), not a 40 deg² bbox. Cap 60k → **120k**
  (`firePointsMax`).
* On fallback the JSON carries `points_unavailable: {estimate, max}` and the
  header `X-Fire-Points-Estimate`.
* `/api/fire-anim-trajectories`: `limit` cap 3000 → 12000; every group now
  carries `fires`, `days`, `frp`, `start`, `end`, `narrative` — enough for a tip.

### View GeoPackage (`srv/gpkg_view.go`, new)
`POST|GET /api/view/export.gpkg?bbox=w,s,e,n&from&to&at=YYYY-MM-DD&layers=trajs,deforest,…&aoi=<id>&area=<park>&peek=1&refresh=1`

* Same job queue/cache/card/21-day link as the area export; `gpkgKeyFor()` folds
  bbox+at+layers into the cache key.
* `at` is the **upper bound of the window** (a paused frame shows what had
  happened by then). `view_frame` layer records bbox/window/instant/layers.
* `fire_trajectories.active_at_instant` = the bright-head/ash distinction.
* Only the chips that were on are written (`viewLayerTables` maps chip → tables).
* Raw detections skipped above 3M estimated (never truncated).
* Measured: XSA, 1°×1°, 3-year window, `trajs,deforest,settlements,infra`
  → **8.6 MB, 4 s**, 1872 trajectories (211 active at instant), 11647
  settlements, 74 rivers, 262 waterbodies, QGIS project embedded.

Quick check:
```bash
source secrets.env
curl -s -X POST "localhost:8000/api/view/export.gpkg?bbox=26.5,8.5,27.5,9.5\
&from=2023-08-01&to=2026-08-12&at=2025-03-01&layers=trajs,settlements\
&aoi=XSA_Study_Area&pwd=$AOI_OWNER_PWD"
curl -s "localhost:8000/api/features-in-bbox?type=fire_trajectory\
&bbox=0,-10,40,20&mode=auto&limit=4000&pwd=test2026" | jq '{render,count,total}'
# -> points 4000 266087   (continental)   ~1.0 s
```

## What is left (frontend) — the plan

### A. Pinned layers and stats-panel view layers share one loader
Today there are **two** loaders and they disagree:
* `loadViewFeatureLayer()` (globe.html ~12330) — bbox, `limit=1500`, refetch on
  moveend. Used by the stats-panel toggles.
* `addPinnedLayer()` (globe.html ~17720) — per-park `/features?limit=5000`,
  fetched **once**, never refetched.

Both should become one path that calls `/api/features-in-bbox?mode=auto` with
`&park=<id>` when the pin names an area, and refetches on moveend. `limit=5000`
must stop being a number in the URL — it was only ever the old ceiling.

**Keep the pin's identity**: a pin means "this area's fires", so `park=` is
mandatory for a park/AOI pin, and a bbox-only pin (from the stats panel) simply
omits it. `getPinKey`, share links and the pinned indicator do not change.

### B. Two renderings of the same layer, cross-faded
`render: "points"` → a MapLibre **circle** layer fed from `points[]` (build the
GeoJSON client-side, one Point per row, `id` in properties).
`render: "geometry"` → the existing fill/line/circle layers.

Both keep the **same source id**; swap the layer set and fade `*-opacity` over
~200 ms so crossing the threshold reads as focus, not as a reload. Do **not**
tie the switch to a zoom number — the server already decided from the real
count, and two views at the same zoom differ by orders of magnitude.

### C. The point rendering must still answer a hover
Register the circle layer with `MapTip` (priority 0, same as the geometry
layers). Its `html` callback gets only `{id}`, so it must fetch
`/api/feature-detail?id=` and call `MapTip.refresh(layerId)` when it lands —
the same pattern the AOI coverage tip already uses. Cache by id; a `Map` of
~2k entries is nothing. Show a one-line placeholder meanwhile, never an empty
tip.

### D. Fire grid rescaling
`chooseRes()` in `anim.js` returns 0.1 below 10° of width — i.e. the grid stops
getting finer exactly when the user starts zooming in, which is why the
screenshots show fat blobs over a 1:250k historical sheet. The pre-agg tables
are 0.1° base and the server clamps below that, so:
* below ~2° of bbox width the animator should stop asking for the grid at all
  and ask for **points** (now possible — the gate is count-based), and
* the grid's drawn radius should be derived from **cell size in screen pixels**
  (`proj(lon+res)-proj(lon)`), not from `zoom/5`. The effort layer already does
  this (`cellPx`); the fire grid does not, and that is the visual bug.

### E. Fire points chip
`loadLayer('firePts')` still refuses locally on `bboxArea > POINTS_MAX_AREA`
(40 deg²) before the server is asked. Delete that check; ask, and use
`points_unavailable.estimate` to word the toast:
"1.2M detections in view — showing the density grid" instead of "zoom in".

### F. Interaction while paused
When `A.playing === false`, the animator should register MapTip handlers for the
layers it is drawing, hit-testing its own arrays (trajectory groups carry
`narrative` etc. now; deforest/settlement points carry `ids` if you pass
`mode=auto`). Unregister on play — a tip that follows a moving animation is
noise. This is what "clickable while paused" means; the canvas stays
`pointer-events:none`, so the hit test is a nearest-point search in the
projected arrays (they are already cached: `projectTrajs`, sprites).

### G. The GPKG button next to the GIF
In `buildUI()` (`anim.js` ~1140), beside `#anim-gif`, add `#anim-gpkg`. On click:
`POST /api/view/export.gpkg` with the current `A.fetchBbox`, `A.fromISO`,
`A.toISO`, `at = fmtDate(A.t)`, `layers = LAYER_ORDER.filter(n => A.on[n])`,
`aoi = A.aoiID`. Then hand the job to `GPKGExport` (`srv/static/gpkg_export.js`)
— it already owns the card, polling, download and copy-link. **Do not write a
second job watcher.**

Two things to get right:
* the button must **pause** first (like the GIF does) — the instant is the
  question, and it must not move between the click and the request;
* label it with the instant (`GPKG @ 1 Mar 2025`) or the user cannot tell two
  cards apart in the bell.

## Traps (each already cost time here or in earlier work)

* **`window.map` is the `<div>`**, use the bare lexical `map`.
* A `const` in one `<script>` block is invisible to another (globe.html has
  several).
* `runGeoPackageJob` calling both builders cost 763 MB and minutes — a "pick
  the builder" bug that looks like a slow query. If a job sits at
  "writing fire detections", check `ps` and the file size on disk.
* An AOI job must store the AOI id as `area_id` or its **own owner** gets 404.
* Don't cache a truncated answer under a full key (`geoFeatureWholeLimit` rule).
* `&limit=5000` in old share links means "everything" — keep honouring it.
* Never judge this work by frame rate alone: the failure mode being fixed is
  *information loss that looks like speed*.

## Verify after frontend work
```bash
./tests/run_all.sh                  # db 37, api 45, ui 20
go test ./srv/ -run 'GPKG|QGIS|GeoPackage|AreaHit|Gzip'
python3 scripts/check_fire_consistency.py
```
(`TestServerSetupAndHandlers` fails pre-existing — `035-test-env.sql`, unrelated.)
