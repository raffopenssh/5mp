# Time animator

_Split out of AGENTS.md. Read when working on this area._

## Time Animator ("▶ Animate" button next to slider presets)

Animates all toggled/pinned map layers over the time-slider window.

| Piece | Where |
|-------|-------|
| Frontend | `srv/static/anim.js` (canvas overlay above MapLibre; `window.Animator.open/close/toggle`) |
| Fire/effort frames API | `GET /api/fire-frames` → `srv/fire_frames.go` |
| Dated trajectories API | `GET /api/fire-anim-trajectories` → same file (reads `data/fire_groups_v5/*.json`, ~40-park LRU cache) |
| Pre-agg tables | `fire_grid_day/week/month` (base 0.1°, PK `(d, xi, yi)` WITHOUT ROWID; cell center = `xi*res, yi*res`) |
| Agg builder | `scripts/build_fire_grid_agg.py` (full ~100s; `--since YYYY-MM-DD` incremental; called by `daily_fire_update.py` step 2c) |

**Fire grid rendering** (`heatIndex`/`heatBuffer`/`drawFireGrid` in anim.js,
rewritten 2026-08-12): the grid is a **heat FIELD**, not a set of pulses.

What it replaced: every bucket still inside a fade window was painted straight
onto the map with `globalCompositeOperation = 'lighter'`. Two consequences,
both visible over a 2.5-year AOI window and neither of them about colour:

1. **Alpha stacked instead of intensity adding.** Three quiet months on top of
   each other came out brighter than one busy one, so 485,000 km² of dry season
   washed to a flat pink sheet with the structure buried in it.
2. **Every cell strobed once per bucket.** A bucket landed as an impulse and
   decayed, so ground that burns continuously all season flickered monthly —
   a picture of our *bucketing*, not of the fire.

So **pulsing was the wrong model**. Heat is computed as a *number* per cell and
coloured once:

    heat(cell, t) = Σ_bucket  n · ramp(t) · exp(−age / τ)      τ ≈ 1.2 buckets

`ramp` rises across the bucket's own duration (we do not know *when* inside a
month a detection burnt, so spreading it is the least-wrong statement, and it
is what removes the strobe); the exponential is cooling memory, so a moving
front leaves a fading trail. Decay reaches zero deliberately — **no permanent
burn scar**: over a multi-year window every cell in a savanna has burnt, so a
scar layer is a solid rectangle. Persistent loss is the deforestation layer, in
purple.

* **Rasterised in CELL SPACE**, one texel per 0.1° cell, then upscaled by the
  GPU with bilinear filtering. Cost is (cells in view) writes + one
  `drawImage`, independent of how many buckets are alive — it used to be one
  `fillRect` per cell *per bucket* plus a full-screen CSS blur. XSA
  continental, three layers: **~25 ms/frame** including the deforestation and
  settlement sprites.
* **The buffer's rows are mercator, not latitude.** Cell rows are equally
  spaced in latitude, screen rows in mercator y. `heatBuffer` resamples so the
  blit stays a single exact `drawImage`. Stitching horizontal bands instead
  (the first attempt) drew every band edge twice through a translucent alpha =
  bright seams across the map. Rotation/pitch falls back to per-cell
  projection — a wrong picture is not an acceptable fast path.
* **The scale is measured on the quantity actually drawn**, once per load:
  `accumulate()` is run at 7 sample times and the 99th percentile of the result
  is the top of the ramp. Deriving it from raw bucket counts needs a fudge
  factor for the decay tail, and that factor is data-dependent (three
  consecutive burning days stack, three scattered ones do not) — it came out
  wrong in *opposite* directions at park and continental scale. Never
  per-frame-normalise: the picture would brighten as the season ends, i.e. the
  colour would stop meaning anything over time.
* **Ink gamma is keyed on COVERAGE**, the same rule as `densityPaint()`: what
  fraction of cells carry fire in the busiest bucket. A continental dry season
  (>60%) curves hard, so only the top decile reads; a single park (~2%) is
  nearly linear, because there the hot cells *are* the point. One constant
  cannot serve both — 5.3M detections over ~4,000 cells means the *median* cell
  holds 17 a month, so a linear ramp paints a whole country at a third opacity.
* **The ramp spends most of its length in the deep reds** (inferno/magma
  convention). The previous dark-red→orange→amber→white ramp was right for
  sparse blobs and wrong for a field: over a continent most cells land
  mid-ramp, and a country of mid-ramp orange is a brown smear that reads as
  land cover — the same failure as the olive ramp before it, one step along.
* **It draws UNDER the discrete layers and composites `source-over`.** It is a
  surface, so it belongs below settlements, clearings, trajectories and
  detections (the same rule as the geology drape). Additive would double-count
  buckets that were already summed as numbers. Detections and trajectories stay
  additive — they are sparse, and that is what makes a cluster glow.
* **Settlement and deforestation sprites carry a dark halo**, because the field
  is now behind them and settlement yellow is the top of the fire ramp. Free:
  those bitmaps are built once per view transform, not per frame.
* Hovering the paused field answers `≈N detections burning here` from `acc` —
  approximate on purpose, since it mixes the current bucket with what is still
  cooling. It answers **last**, only when nothing discrete did: every pixel of
  a surface is a hit, so competing on distance would beat the trajectory the
  user is pointing at.

### Every season of the window; the past as ash (2026-09-17)

The front was fetched for ONE season — the one the slider ends in — and the
animator filtered its contours by `t ≤ playhead`. Over a 2020–2026 window that
drew nothing for six years, then 2026; the compare seasons did not help
because they sit on the reference season's *calendar* (same day of season),
so they too waited for 2026. Patrol isochrones/pressure did worse: the server
caps a window at 800 d, so the request 400'd and the guest link on a phone
showed "none of it" (`/s/g-h7c4n4qd3thfh5h7` was the report).

Now, in `fireseason.js`:

* **Fire front — `loadHistory()`.** On the animator's first `animAt(t)`
  (`starting`) every season in `front.seasons` overlapping the window is
  fetched with `season=` (shared cache `cmpData`, one request per season,
  once) and put on `FRONT_SRC` beside the reference (`applyFrontData`), each
  contour at its **own** dates. Teardown (`animAt(null)`) puts the reference
  alone back. `playheadMeta` reads `front_curve` from the season the playhead
  is *in* (`seasonAnswerAt`), so the stats row says `front 71 %` for 2021/22,
  not `0 %` of 2026/27.
* **Patrol — `loadPatrolHistory()`.** `patrolURL()` always sends `clip=1`
  (server: `from = max(from, season_start)`), so the static picture is the
  season `to` falls in — the front's rule — and the request stays under the
  cap. The wire now carries `seasons[]`; the animator fetches one window per
  earlier season (`pdata`, `phist.periods`), each holding its isochrones,
  end-of-season pressure rings and visits.
* **Pressure is per season, never carried across** (little in the rains,
  a build-up, a peak, then a new count). `pressureAnimFeatures(t)`: the
  season `t` is in is rebuilt live from its visits (`pressureContoursFor`,
  `prs.src` keyed to the answer); a finished season keeps only its **peak
  core** — the top two rungs of its ladder (`PRESSURE_LADDER`) — as ash,
  `at` = the day after it ended; nothing for seasons ahead.
* **Ash is one rule for every dated line** (`ashColor/ashOpacity/ashWidth/
  ashLineFilter/ashLabelFilter`): colour → `ASH_FIRE` (warm) / `ASH_PATROL`
  (cool) between 150 and 330 d of age; opacity 0.3 @150 d → 0.2 @1 y → 0.1
  @5 y; width 1.3 → 0.8; the 5-day lines drop after 240 d, the 15-day after
  600 d, the 30-day lines (`l30`) stay. Labels: the current season's;
  once ashed only the 30-day lines, in the line's **own year**
  (`'16 Jul ’21'` from `date`, not the season label — the 2020/21 season's
  August is ’20). Pressure rings share the fade, and only live rings carry
  numbers (an ashed ring's number and year are in its tip).
* `Animator.seek(ISO|ms)` pauses and draws one instant — for tests and the
  console; the slider is the UI.

Tests: api `patrol_isochrones_long_window_400` / `_clip_to_season`; ui
`anim_front_every_season` (async assertion — `runUITests` awaits `fn`).

### The playhead moves feature-state, never the filter (2026-09-18)

Every frame of the season animation used to write **new** data-driven paint
expressions and filters on the front/patrol/compare/pressure layers
(`ageD = t − get('t')` baked into `line-color/width/opacity`, `ashLineFilter(t)`).
A changed data-driven expression or filter makes MapLibre **reload the
GeoJSON source in its workers** — re-tessellating every contour — so at z 5
over CAR (402k front vertices) a frame cost ~0.6–1 s and a phone visibly
hung (`/s/g-z2k3jvphq7kfssnh`). Now, in `fireseason.js`:

* Sources carry `promoteId: 'fid'` (`setData` numbers features), and the
  animation paint is **constant**, reading `['feature-state', …]`: `o`
  opacity, `w` width ramp, `k` ash mix 0..1 (`fsColor`: interpolate
  `get color` → ash), `v` wave opacity, `x` text opacity. `fsEnter()` sets it
  once when the animation starts, teardown restores the static paint.
* `fsFrame(t)` evaluates the **same** piecewise-linear ramps in JS (`pl()`,
  `frontState()` = ashLineFilter + ashLabelFilter + ashColor/Opacity/Width
  + wave + text), quantised to 1/100, and calls `setFeatureState` only where
  a value moved (`fsSet` cache). Hidden = `o` 0 **and** `w` 0 (a 14 px
  blurred wave stroke costs fragments even at opacity 0). Pixel-identical
  to the expression version by construction — same stops, same rules.
* **Labels live on a mirror source** (`FRONT_LBL_SRC`, `PAT_LBL_SRC` — the
  labelled lines only, filled by `setData`). A symbol's filter still has to
  follow the playhead (a label hidden by opacity keeps its collision box
  and blocks a shown one), but a reload of the mirror is a third of the
  geometry and no line tessellation, and `throttledFilter` applies it at
  most every 300 ms with the last state always landing.
* The repaint gap adapts: `animGap()` = clamp(80 ms, 3× the EMA of the
  frame's main-thread ms, 250 ms) — drop frames, never stall.
* Main-thread cost measured on that CAR view: 5–15 ms JS + 20–50 ms render
  bookkeeping per frame, from a full source reload. `TEST.fireSeason()
  .drawnFront/drawnPatrol` and `anim_front_every_season` exclude
  `state.o === 0` features, since hiding is no longer a filter.
* `FireSeason.frontAnyInView()`: the animator's "no animatable data" test
  used to read the reference front (the park under the view CENTRE — null
  over open ground) while the bbox mosaic drew lines all over the view.

### Every park's front, unless scoped (2026-09-17)

The reference front (`front` in `fireseason.js`) is ONE area: the focus,
else the park under the view centre. Unfocused, that was also all the map
drew — two parks side by side, contours in one and none in the other, which
reads as "no data there". Now `/api/fire-season?bbox=…` (`fireSeasonBBox`,
`srv/fire_season.go`) answers for every **park** whose front grid intersects
the bbox, each at the season `at` falls in (else latest — the single-area
rule), and `loadOthers()` puts them on `FRONT_SRC` beside the reference
(`others.feats`, property `area`). Rules:

* **Scope:** a focus (park or AOI, `aoiFocusID`) → the reference alone; a
  filter box (`currentBbox`, `5mp:bbox-changed`) → the parks the box
  intersects; neither → the padded viewport. AOIs are never in bbox mode
  (their grid spans the parks inside them).
* **Zoom thins every park the same way** (`linesForZoom`: all 5-day lines
  ≥ z6.5, labelled 15-day ≥ z4.5, 30-day below) and the reference is
  thinned to match (`thinFront`) — one key for the whole picture. Server
  `lines=all|15|30`; continental at 30-day = 60 areas, ~150 KB gzipped.
* **Payload discipline:** bbox quantised to 0.5° after 30 % padding (a pan
  inside it costs nothing), `limit` 60 desktop / 30 under 768 px
  (`othersLimit`), `exclude=<reference>` so it is not shipped twice,
  `truncated` reported (invariant 8).
* **Animator:** `loadOthersHistory()` fetches one bbox answer per earlier
  reference season (`at` = that season's end), deduped by area+season, so
  the playhead meets each park's earlier fronts at their own dates. Seek
  cost measured 5.3 ms with 13 areas on vs 2.1 ms off.
* The stats row, compare seasons and `playheadMeta` still describe the
  **reference** only — they are one area's numbers and say which.

Tests: api `fire_season_bbox_*`.

### Every park's patrols, pressure and early-burn ground (2026-09-18)

The front's bbox rule, extended to the layers that were still one-area:

* **Patrol isochrones + pressure** — `/api/patrol-isochrones?bbox=…&from&to`
  (`patrolIsochronesBBox`, `srv/patrol_isochrone.go`): every *park* whose
  grid intersects the bbox **and holds a patrol-day** in the window, each
  the full single-area wire, clipped to the season `to` falls in,
  `lines=all|15|30` thinning, `limit`/`truncated`/`exclude`. One
  `track_points` scan serves them all: every front grid sits on the global
  0.025° lattice (`patrolGridAligned`; `patrolRawVisit` is keyed
  `floor(lon/res)`), so visits are bucketed per park in Go. `candidates`
  counts the parks in the box so an empty `areas` reads "no patrols", not
  "no parks" (invariant 1). **`visits` ship only with `visits=1`** — they
  are ⅔ of the bytes and only the animator needs them (`pothers.visits`
  marks an answer that can animate; a static answer never serves an
  animation). Client: `pothers` / `pothersHist` in `fireseason.js`, the
  reference thinned to the same zoom key (`thinFront` works on patrol
  features too), pressure accumulators live **on the answer** (`j._prs`),
  one per area × season.
* **Pressure CPU budget** (`pressureAnimStep`): the per-frame cost that
  scales with the viewport is re-contouring n parks × ladder × grid. The
  pass is timed and the next one waits `2 × cost` of wall time, so on a slow
  phone the rings step in coarser increments while the rest animates at
  full rate; `force` (export frames) always cuts. Per answer: no new visits
  since the last cut → the last features stand; grid off the padded view →
  not re-cut (`gridNearView`). `marchingSquaresJS` scans only the touched
  box (`_prs.box`, visits ± kernel + 1) and links segments by integer
  **edge id**, not coordinate strings — 13 Tanzanian parks at peak went
  from ~140 ms to ~40 ms a forced frame on the VM's headless Chrome.
* **Early-burn ground** — `/api/fire-season?bbox=&early=1&summary=1`
  (contours omitted) adds `early_ground` + `seasons` per area;
  `entryAreas[area]` = one `CellField` per park (`fireseason-entry-<area>`,
  placed before `FRONT_WAVE`), `entryStateAt(c, T, E)` takes the area's
  answer, the strip count sums every field in view.
* **Owned cells** (`ownCells` / `ownsCell`): grids are boundary + margin and
  neighbours overlap, so a cell drawn by two fields was a brighter block
  where nothing differed. A lattice cell belongs to the **smallest grid
  that has a value there** (the point → area rule of `fireSeasonAreaAt`); a
  smaller grid with no answer does not punch a hole.
* **Speed across parks is wired but OFF** (`SPEED_OTHERS = false`):
  `/api/fire-season-speed?bbox=` (`fireSeasonSpeedBBox`, legend once at the
  top, no `values` per area) and `speedAreas[area]` fields work, but every
  park's padded rectangle drawn as its own seamed block is a wall of orange
  at z6, not a field — and 40 parks × 8 seasons is 2.9 MB gzipped. Needs a
  rework of the rendering (one viewport-resolution composite raster, or a
  different visual altogether) before it is switched on.
* Legends say `Drawn for n areas in view` (+ how many the cap cut).

Tests: api `patrol_isochrones_bbox_*`, `fire_season_speed_bbox`,
`fire_season_bbox_early_summary`; ui `season_every_park_in_view`.

### Vanguard frame cost (2026-09-18)

With vanguard on, a frame at z5 over CAR cost ~30 ms on the VM's headless
Chrome (≈100 ms on a phone); now ~14 ms, same pixels (A/B'd against HEAD's
`anim.js` with a frozen `performance.now`: inked pixel counts equal, mean
per-pixel difference 0.04–0.13/255 — antialiasing at path joins).

* **Half of it was an echo.** `draw()` → `FireSeason.animAt()` →
  `playheadMeta()` → `emit()` → anim.js's `FireSeason.onChange` listener →
  `draw(A.t)` again, from inside the first, on every frame whose reached-%
  ticked. `draw()` now sets `A.drawing` and the listener skips while it is
  set (a state change from the legend still redraws).
* **`vanStatic(g)`** computes each chain's per-segment lead, gap days and
  colour bin once (`g._vs`); `drawVanguard` walks it and strokes **runs** —
  consecutive segments of one bin / one side of the front / one dash state
  as one path — instead of one `beginPath`/`stroke` and a fresh rgba string
  per segment (×2 with the halo). Dashed runs still stroke per segment so
  each gap's dash phase starts at its vertex, as before. `vanRGB(lead)`
  memoises the ramp on the half-day; `FireSeason.leadColor` built a hex
  string that was parsed back per segment per frame.

What remains is `ctx.strokeStyle` assignment per run (~4 ms/frame here) and
MapLibre's own render; the next lever would be coarser colour bins, which
changes the ramp's resolution and so is not free of visual change.

`speed` has its own rendering category (`Renderings.CAT.speed = 'speed'`,
word `weight`, glyph `.rg-speed` — contours thinning as they spread). It was
filed under `contours`, so front + speed wore one mark twice and the pill
deduped them into one word.

### Patrol follows the panel's pixels row (2026-09-17)

"The patrol layer showed" — with the panel's patrol (pixels) row OFF, a
plain open ran the `now` profile, which switched `patrol` on regardless
(`layers=none&anim=…&anim_hl=now` reproduced it). `patrolOffered()` =
`HAS_PATROL !== false && viewLayers.pixels`; `profileLayers` and the
`patrol` profile's `needs` both use it, the chip hides/unhides with the row
while open (`Animator.refreshChips()` from `applyViewLayer('pixels')`), and
chips now keep `aria-pressed` in step with `.on`. Two close-during-load
crashes fixed with it (`ensureLayer` catch/finally and `open()` after its
awaits read `A` while null). Soaked with `MapStress.phases.animator()` —
every chip, every profile, pixels toggles, date reopen, close cycles, all
asserted against the invariants listed at the top of that phase.

### Highlight is a curator, not a dimmer (2026-09-16)

`highlight` used to be one switch that dimmed the surfaces and left the eleven
chips to the user — a fresh open showed "whatever the map had on". Now it is
**on by default** and **chooses the chips**: each profile in `HL_PROFILES`
(`anim.js`) is a small set that reads together over the dimmed heat field.

| id | chips | needs |
|---|---|---|
| `now` (default) | fireGrid · paths · deforest · patrol | — |
| `season` | fireGrid · front · vanguard · entry | season data (`seasonRefusal()` null) and a window ≥ `HL_SEASON_MIN_DAYS` (60) |
| `patrol` | paths · patrol · patrol pressure | `HAS_PATROL !== false` |
| `change` | fireGrid · deforest · settlements | — |

* **Click steps to the next available profile** (wraps); the chip label reads
  `highlight · season`, its title names the next one. A profile whose data
  layers all come back empty is skipped with a toast (one lap max).
* **Any chip click switches highlight off** (`toggleChip` clears `A.highlight`
  unless `A.applyingHL`) and *keeps the layers as they are* — the profile is
  the user's starting point, not a rule that undoes their click. Off never
  clears the map.
* **State is the profile id**: `A.highlight` is `'now' | … | false`;
  `Animator.highlight()` / `getState().highlight` return it; the share link
  writes `anim_hl=<id>` (legacy `anim_hl=1` → default). A link that names
  `anim=` layers **without** `anim_hl` is a hand-picked set and opens with
  highlight off; a fresh open (no `opts.layers`) opens with `now`.
* `Animator.setHighlight(id|false|undefined)` and `highlightProfiles()` are the
  programmatic surface.

What else the curator touches — and what it must not:

* **Season overlay** (`fireseason.js`): profiles set the chips through
  `FireSeason.set*` (the same switch as the chip), so they flip the map's
  overlay too. It is snapshotted before the first profile (`A.seasonBefore`)
  and **restored on close** unless the user toggled a *season* chip themselves
  (`A.seasonTouched`). Clicking a non-season chip switches highlight off but
  still lets the overlay go back.
* **Live LOD layers** (`lod-view-{fires,deforest,settlements}-*`): while a
  profile is active, the live map layer of a row **the animation draws** is
  hidden (`syncLiveLayers`, re-applied on `lod:state` because LODLayer
  re-adds layers on refetch) — the legend already marks that row as
  `.layer-animated`, so hiding tells no lie. Rows the profile does not animate
  stay as the user set them: hiding those would leave an "on" row drawing
  nothing. Patrol pixels were already handled by `syncBaseEffortVisibility`.
* **Never touched: basemap, historical sheets, geology.** They are not
  animated; they are the user's choice.

### The legend states every rendering, and switches them

The stats panel's rows are the map's legend. With the animator open they were
lying twice over: a row switched *off* in the panel was being drawn by the
animation anyway ("Fire Activity 👁̸ 302" over a screen full of animated fire),
and a row switched *on* reported `1,448 in view · shapes` over a map showing an
animated heat grid and no shapes at all. The animation's own switches lived
somewhere else entirely — chips under the time slider, in a different
vocabulary.

Fixed 2026-08-12. `anim.js` emits **`anim:layers`** (`announceLayers()`, fired
from `updateChips()` and on close) and exposes `Animator.layers()`,
`isLayerOn()`, `layerRefusal()` and `setLayer(name, on)`. globe.html maps rows
to animation layers (`ANIM_ROW_LAYERS`) and renders both in one control.

* **One vocabulary, one control.** `auto/shapes/fast` (map detail) and
  `grid/points/paths/circles/dots` (animation) are all *renderings of this row*,
  so they are one dropdown — `.aoi-menu` again, the download menu's component,
  already touch-sized and already body-level so nothing clips it. The old cycle
  button could not survive the list growing: cycling six states makes changing
  one thing five taps, and on a phone a cycle button never shows what the other
  states are.
* **Map detail is one-of, animation is any-of** — radio marks and checkbox
  marks, because a fire legitimately *is* a heat field and a set of paths at
  once. That is why the animator's chips are chips; the menu must not
  re-describe them as alternatives.
* **The animation group appears only while the animator is open.** Otherwise
  choosing "grid" would have to silently open the animator — changing the time
  slider, the map and the share link from a control that said one word. When it
  is closed the menu *says* so instead of hiding the possibility.
* **The row's readout shows whenever the row is drawing anything**
  (`.has-modes`, not just `.layer-on`), and a row drawn only by the animation
  keeps its label legible (`.layer-animated`) while its accent bar stays off —
  the map layer really is off; the animation is what is on screen.
* **Its eye becomes a ▷.** A crossed-out eye states *not visible* about
  something plainly on screen, which is the same lie the whole change exists to
  fix, one glyph smaller. Same rule as `.layer-pinned` (eye → pin), and the row
  then has to say what its own switch still does — hence the tooltip "Drawn by
  the animation. Click to also show this layer on the map.
* **One direction only.** The chips remain the animator's own state; the legend
  is a second way to reach the same switch, exactly as the detail control lives
  on both a stats row and a pinned chip. A pinned chip's menu has **no**
  animation group — the animator's renderings are of the viewport, not of a
  named pin.
* The pill names up to two renderings and then says `3 modes`: a pill is not a
  legend, and the list is one tap away.

### A paused frame's dot is the same feature a pinned layer draws

Hovering a settlement or a clearing in the animator used to answer
`4.9630, 25.0595` — coordinates and nothing else — while hovering *the same
row* as a pinned layer answered with its narrative, classification and an
"Open area overview" button. Same database row, two different answers, and the
poorer one was the one the user was looking at. The animation was a picture.

Fixed 2026-08-12. `/api/features-in-bbox?mode=points` already ships `ids`
alongside `points` for exactly this reason (see `docs/agents/lod.md`); the
animator simply threw them away. It keeps them as `rid` now and renders the tip
**through LODLayer**, so there is one cache, one fetch per row ever, and one
renderer:

* `LODLayer.detailFor(rid)` / `.loadDetail(rid, refreshId)` / `.tipFor(props,
  type, area)` are the shared surface. A dot hovered in the animation and the
  same dot hovered as a pinned layer hit the same `detailCache` — hovering one
  after the other costs zero requests, and the two cannot drift into
  disagreeing about what a settlement is.
* The placeholder is never empty: kind, date and size are known locally and
  render immediately; `MapTip.refresh()` swaps in the full narrative when
  `/api/feature-detail` lands. **A probe may now return `render(props)` instead
  of a fixed `html` string** — that is what makes refresh work for a canvas
  feature, which has no MapLibre properties to re-query.
* Fire paths already carried their narrative in the trajectory payload; they
  gained the **action button** (`openAreaOverview(park, 'fire', id)`), so ⏎ or
  a double-click on a burning front opens the park's fire section like a pin.

**A probe may answer with SEVERAL features, one per kind.** It used to return a
single nearest hit across all layers, so a settlement under a fire path was
unreachable — the exact "select behind" failure the card's tabs exist to fix
for registered layers. `probeFrame` keeps one best per kind and hands MapTip a
list; each becomes a candidate under a stable sub-id (`animator-frame:trajs`),
so the tabs work **and** `?tip_layer=` still round-trips.

### The probe is index-backed, because it runs on every mousemove

The naive probe projected every loaded point per pointer event: 12,000
settlements + 3,269 clearings + up to 6,000 trajectories x ~30 vertices, each a
matrix multiply, at pointer rate. Measured on `XSA_Study_Area` at z7.5 that is
**15,269 projections = 14.8 ms of pure `project()`** before a single distance
test — a paused map that stutters under the cursor while doing nothing.

Now: **project once per view transform, then bucket.** `screenIndex(name, arr,
…)` holds a `Float32Array` of screen coordinates plus a 32 px uniform bucket
grid (`heads`/`next` linked lists in `Int32Array`s); `idxNear()` visits ~9
cells. Trajectory vertices get the same treatment in `trajIndex()`, built
**lazily** — a play-through never pays for it. Everything screen-space is keyed
on one `viewKey()` string, and `invalidateSprites()` clears it.

* **The sprites use the same index.** `settlementSprite`/`deforestSprite` used
  to project independently, so the same 15k points were projected twice per
  view change. One projection now serves both the picture and the hit test.
* `showHover()` **skips the innerHTML write when the answer has not changed**.
  Moving across one feature is not a new answer, and rewriting the tip's DOM
  60+ times a second for an identical string was most of what remained.

| measured — XSA z7.5, 4 layers (12,000 settlements · 3,269 clearings · 6,000 fire paths) | before | after |
|---|---|---|
| `probeFrame()` alone | ~15 ms (15,269 projections) | **0.045 ms** |
| mousemove, paused (probe + MapTip + DOM) | 6.33 ms | **1.84 ms** |
| mousemove, playing (probe unregistered) | 0.21 ms | 0.20 ms |
| playback | 11.1 fps | 11.9 fps |
| tip content | `4.9630, 25.0595` | narrative + classification + action |

Playback is untouched by design: the probe is registered on pause and dropped
on play, so an animation is never asked to hit-test a frame that has moved on.

**The `fire points` chip is disabled when the view cannot have it.**
`GET /api/fire-frames?mode=estimate` is a ~10 ms SUM over `fire_grid_day`
returning `{estimate, max, points_ok}`; the animator asks on open and on every
refetch. Over the ceiling the chip dims and its **hover hint carries the
number** ("10.8M detections in this view — too many to draw one by one (limit
120k). Zoom in, or shorten the window…"). It used to be always live, so the
user clicked, waited for a request, and was told it had fallen back to the
grid — an offer the app already knew was refused. Load-bearing details:

* `.anim-chip.unavailable` keeps `pointer-events`. `pointer-events: none` made
  the chip invisible to the cursor, i.e. the one thing it exists to do — say
  why it is off — could not happen, and it read as broken rather than refused.
  The click is refused in `toggleChip()` instead, where the reason is known and
  is spoken as a toast.
* **A share link is not an override**: it can carry a viewport its author never
  had, so `anim=…,firePts` on an impossible view is dropped *with its reason*
  rather than switched on to draw nothing. The probe is therefore `await`ed
  before the loaders run, and `fireGrid` uses it to skip a doomed points ask.
* A failed probe is **unknown, never a refusal** — the chip stays live and the
  old ask-then-fall-back path answers.

### Waiting is said where the answer will be (2026-08-17)

The animator had its own loading language: a fixed modal in the middle of the
screen with a flickering 🔥, a red/amber progress bar and "Loaded 2/4". Three
things wrong with it, and none of them was the animation. It **covered the map
it was loading**; it spoke a visual language nothing else in the app speaks
(every other wait here is `.chip-dots` — three dots, `globe.css`); and it
attributed to *the app* a wait that belongs to **four named layers whose chips
were right there**, greyed and silent.

Now the chips carry it. `.anim-chip.is-loading` swaps the layer's own status dot
for the three dots and adds the same sheen a pinned layer chip uses, so the
answer lands exactly where the waiting was drawn. Progress is **counted, not
narrated** — the chips still dotted *are* "2 of 4", and it is spatial: you can
see *which* two. The date label carries the overall first load (`showLoading()`,
now a boolean), because until a layer lands there is no frame and the date on
screen is not yet true of the picture — hence `drawAndSync()` refuses to
overwrite the label while `is-loading`.

* The **mark slot is one width in both states** (dot centred in 12–14 px). A
  chip that grew on entering the wait would reflow seven chips from two rows to
  three and shove the footer up — the same rule as the pinned chip's min-width
  count slot.
* `⏳` is gone from both exports. A **GeoPackage** job is unmeasurable (the
  server owns it) → dots. A **GIF** encode is measurable (we own the loop) →
  it keeps its percentage. A real number always beats dots; dots are for waits
  that have no number.

### Touch: separation, not bulk

The controls were badge-sized with an invisible `::after` stretched 8 px past
every edge. That produced **mistaps, not misses**: `−` and `+` sat 3 px apart
with 6 px of slop each, so a band between them belonged to both and DOM order
won it — a finger aiming at *slower* stepped *faster*, which reads as the app
ignoring you. And a target you cannot see cannot be aimed at: enlarging the
invisible box does not move the aim point.

The first fix over-corrected to 40 px (then 26 px) pill-shaped controls. That
was **the wrong reference**: these controls are siblings of the `90d` date
preset tag one row up, which is **14 px tall with a 3 px corner**. Blown up
they stopped reading as time-slider furniture and became a second toolbar on
top of the first, and they ate 40 px of a map the user opened the app to look
at. Touch changes how **big** a control is, never **what it is**.

So, under `@media (hover: none) and (pointer: coarse)` — keyed on the pointer,
not the viewport (a landscape phone is 900 px wide and still a thumb):

| | before | now | reference |
|---|---|---|---|
| button | 12×14 px + 8 px slop | 17–18 px tall, 3 px radius | `90d` tag = 14 px |
| chip | 13 px | 17 px | same badge |
| gutter | 3 px | 7 px | — |
| hit box | ±8 px (overlapping) | **≤ half a gutter** (±3 px) | — |
| footer, 412×915 | ~120 px | **138 px** | map keeps 85% |

Half a gutter can never overlap, so every tap lands on the control nearest the
finger. Verified with `document.elementFromPoint` swept along both rows and
across the band between them: every pixel resolves to at most one control, with
dead space between (`anim-play×24 · -×3 · chip:fireGrid×19`). Desktop is
untouched (footer 93 px). Press feedback is `:active { transform: scale(.93) }`
— a touch has no hover, so the press itself has to answer.

**Key behaviors** (all in `anim.js`, v2 — integrated into the time slider):
- UI lives **inside** the time-slider header: play/date/speed/GIF/close inline, playhead + progress rendered in the slider track (playhead is pointer-draggable to scrub; pauses while dragging, resumes after). `#anim-open-btn` is a preset-tag-styled chip.
- **Layer chips** (`.anim-chip`, staggered reveal like date tags — all always shown so users see what's available): fireGrid / firePts / trajs / effortGrid / effortPts / deforest / settlements. Lazy-load on first enable (`ensureLayer`); toggleable mid-play.

  ⚠️ **`infra` removed 2026-08-12.** It was a chip for something the animator
  does not animate: a re-drawing, onto the animation canvas, of pinned
  roads/rivers/places **the map is already drawing underneath it**. Nothing
  about it was dated, so it looked identical in every frame — and the chip row's
  whole subject is time, so a static entry in it invites the reading that the
  others are static too. Worse, it was a *second* switch for a layer whose real
  switch is elsewhere (the pin, reached from the map tip / AOI tip), so
  switching it off here left the lines on screen and read as a broken control.
  One switch, one meaning. `turb` was removed earlier (§10) and its remaining
  branches are inert.
- Defaults from `viewLayers` toggles + pins; zoom ≥ `POINTS_ZOOM` (6.5) and bbox ≤ 40 deg² prefers real points. `firePts` = `/api/fire-frames?mode=points` (individual VIIRS detections, ≤60k, server falls back to grid). `effortPts` = patrol-effort **circles**: same aggregated frames as effortGrid, drawn as fire-style green glow + recency ring, newest visit per cell wins.
- Map stays fully interactive (canvas pointer-events:none); pan/zoom outside the 30%-padded fetch bbox triggers debounced refetch (`onMoveEnd`), unless a drawn bbox is fixed (then canvas is clipped to it).
- Temporal semantics: fire grid/points flash + afterglow; trajectories build at true dated speed with glowing head, then **ashen out** (red→grey→gone over `TRAJ_FADE_DAYS`=21); effort ages to ash over 90d so refreshes flash green; deforestation accumulates (45d flash); settlements static; turbidity accumulates.
- Speed +/−: click steps ×1.35, press-and-hold ramps (mobile). Keyboard: space/←/→/Esc.
- **Share links**: `anim=<layers>&anim_speed&anim_t&anim_paused` written by `shareCurrentView()` (via `Animator.getState()`); restored through `window._pendingAnim` set in `restoreStateFromURL()`, polled by anim.js until map ready.
- `chooseStep()`: ≤92d→day, ≤800d→week, else month. GIF export via `gifenc` CDN
  (720px; on mobile the frame cap drops to `GIF_MAX_FRAMES_MOBILE`=80 — never hidden, a hidden row reads as "unavailable"). **The GIF plays back at the on-screen speed**: its
  duration is `spanDays / A.speed` seconds, frames are `10/s` capped at
  `GIF_MAX_FRAMES`, and the per-frame `delay` is then stretched so a capped
  export gets *choppier, not faster*. It used to be a fixed 80 frames × 100 ms,
  i.e. always an 8 s clip regardless of the speed control the user had just set.
- **An AOI animation must never silently fall back to its bbox.**
  `Animator.open({aoi})` reads `window._aois`, which `loadAOIs()` fills
  asynchronously — a share link carrying `anim_aoi=` can win that race. Without
  the geometry no `&aoi=` is sent, `aoiExcludeSQL()` then hides the AOI's own
  rows, and the animation plays *empty*. A missing entry is now treated as
  not-loaded-yet and fetched from `/api/aois/{id}?geometry=1` (whose payload is
  `{aoi, datasets, parks}` — unwrap `.aoi`).
- **Opening paused at `t0` is legitimately blank** (no trajectory has started
  yet), which reads as a broken layer. `frameHasContent(t)` drives a one-off
  hint instead; it affects wording only, never drawing.

**Server-side** (`fire_frames.go`):
- `/api/fire-frames?bbox&from&to&step=day|week|month&res=0.1` reads pre-agg tables (never `fire_detections` — a raw scan took 3min for full-span; agg is ~3s). Coarser `res` re-binned in SQL; `from` aligned to bucket start. If >200k points, auto-doubles `res` up to 2× twice instead of truncating.
- `layer=effort` returns `[xi, yi, km, uploads]` on the same grid (from `effort_data`+`grid_cells`, `movement_type='all', env='prod'`).
- Frame point format: `p: [[xi, yi, count, frp], ...]`, `d` = bucket start date.
- `mode=estimate` returns `{estimate, max, points_ok}` and nothing else: the
  same ~10 ms SUM the `mode=points` gate uses, exposed so the UI can refuse
  *before* offering (see the fire-points chip above). Side-effect free; pinned
  by `fire_frames_estimate_*` in `tests/api_tests.sh`.

**After bulk fire data changes**: rerun `python3 scripts/build_fire_grid_agg.py` (full) or `--since` — otherwise the animator shows stale fires. Daily cron keeps it fresh automatically.

### Settlements & deforestation at AOI scale

The animator's `deforest`/`settlements` layers and the stats-panel view layers
all come from `/api/features-in-bbox` (`srv/features_bbox.go`). At park scale it
was fine; over `XSA_Study_Area` (78,105 settlement polygons in one view) it
returned a wrong picture slowly. Four fixes, all measured 2026-08-10:

1. **`ORDER BY stat_value DESC LIMIT n` is not a sample, it is a corner.**
   Every settlement carries `stat_value = 0`, so the tie-break fell through to
   rowid and the 1,500 rows served were one contiguous *ingest block* — the
   yellow stripe along the AOI's north edge, which reads as "the data is
   wrong", not as "truncated". `spreadSelect()` buckets the bbox into ~limit
   cells and keeps the best feature per cell. Deterministic; `&spread=0`
   restores the old behaviour.
2. **Don't read geometry for rows you are about to discard.** Pass 1 selects
   ids + centroids only, pass 2 fetches geojson for the survivors (`IN` chunks
   of 900). `mode=points` skips geometry entirely and returns
   `[lon, lat, dayOffset, value]` against `from` — the animator draws dots, so
   it was inflating ~1 MB of polygon rings to recover 1,500 centres. 947 KB →
   118 KB gzipped, and the point budget rose 1,500 → 12,000, i.e. a real
   sample instead of a corner.
3. **Migration 046 (`idx_fg_bbox_scan`) makes pass 1 covering.** `idx_fg_stats`
   lacked `park_id`, which `aoiExcludeSQL`/`aoiScopeSQL` always reads, so
   SQLite fetched each candidate's full row — including up to 100 KB of
   geojson — just to read one short string. `fire_trajectory` over a 3° window:
   **3.0 s → 0.22 s**. Same shape as the `ABS()` and `polygon_ids LIKE` traps:
   the index existed and was silently not enough.
4. **Polygons are simplified to half a screen pixel** derived from the bbox
   (radial-distance decimation + 6-decimal coords, `&simplify=0` to disable).
   At continental zoom the *biggest* built-up polygon per cell ships 5 KB of
   sub-pixel ring detail: 2.1 MB → 0.6 MB gzipped, unchanged when zoomed in.

Rendering had the mirror problem — 12,000 arcs re-stroked at 60 fps for a
picture that does not change with `t`. Static/settled layers rasterise into an
offscreen canvas keyed on the view transform (`settlementSprite`,
`deforestSprite`), and trajectory points project once per transform
(`projectTrajs`, `Float32Array` + an off-screen flag) instead of once per frame.
`invalidateSprites()` on refetch/close. **Any new dense static animator layer
should do the same** — the cost is one screen of pixels regardless of N.

**Deforestation ages over the window, and never vanishes.** A fire front is an
event that ends; canopy loss is a state that persists. New clearings flash
purple for 45 days, then grey towards ash over the *window span* (floor 90
days) — not over a fixed number of years, because the loader only fetches
events inside the window, so a fixed 10-year ramp puts every event in the first
6% of a 7-month window and greys nothing. Alpha floors at 0.22 and the radius
shrinks 40%: an old clearing is faint, not gone. Ageing is quantised into 24
bands so the settled-prefix bitmap survives ~4% of the playback per redraw.


---

**Popup fire chart**: single `areaSparkline` (globe.html) fed by `/api/parks/{id}/fire-trend`.
Series keys: `v`=fires (red, left axis), `v2`=groups (orange, right axis),
`v3`=prior-years ISO-week average (dashed gray, same axis as `v`, computed client-side
from full history). Don't add a second weekly chart.

---

### Auto-focus when only an AOI is in view (2026-08-19)

An animation opened *unscoped* over a viewport containing no park but a
visible AOI played empty for trajectories/deforest/settlements: the default
`aoiExcludeSQL()` hid every row on screen while the chips said "on" (guest
link `/s/g-4dqkd24fq56h4cgj` was the report). Fix in `anim.js` `open()`:
when `opts.aoi === undefined` and no `aoiFocusID` and
`queryRenderedFeatures(['areas-fill'])` is empty, `autoFocusAOI()` picks the
visible AOI with the largest viewport overlap, adopts it as the animation's
subject, calls `window.setAOIFocus()` for real (panel/dimming/share link must
agree) and toasts (`key: 'anim-auto-focus'`). Details that matter:

* `opts.aoi === null` means **deliberately unscoped** and suppresses the
  guess — `setAOIFocus`'s animator-reopen branch passes `null` (not
  `undefined`) when leaving focus, or un-focusing would instantly re-adopt
  the AOI it just left.
* `setAOIFocus` is called while `A` is still null, so its
  reopen-the-animator branch cannot recurse.
* `window._aoisPromise` (set at `map.on('load')` in globe.html) is awaited so
  a share link opening the animator does not race `loadAOIs()`.
* `/api/fire-anim-trajectories` returns `groups`, not `trajectories` — a
  debugging probe on the wrong key reads as an empty layer.

---

### One rendering vocabulary; the chip row rests (2026-09-16)

Three surfaces describe the same renderings — the stats-panel legend pill, a
pinned chip, the animator's chip row — and each had grown its own words and its
own mark. The animator said `fires` / `patrol circles` / `entry`, the legend's
pill said **"5 modes"** (a number that names nothing), and every chip wore the
same coloured dot, which meant only "on". With the Season renderings added
(front, vanguard, speed, entry, patrol iso, pressure iso) the row reached
eleven chips and the pill became unreadable.

**`srv/static/renderings.js` is now the only vocabulary.** A small closed set of
categories — `shapes lines dots grid paths contours cells iso vanguard` — each
with one word, one gloss and one glyph; `Renderings.summary()` dedupes
categories and caps the list (3 on desktop, 2 under 768 px) with `+n`. The
glyph is a *picture of the ink*: a weighted lattice for a summed field, outlined
squares for a 2.5 km raster, parallel fronts (one dashed) for the isochrones,
rings from a point for isopleths, chevrons for the chains ahead of the front.
CSS lives in `globe.css` (`.rg`, `.rg-*`) because the legend needs it with the
animator closed.

* **No new legend rows.** Patrol iso/pressure attach to the existing **pixels**
  row, the season lines to **fires**. The rows that own season renderings are
  *derived* (`SEASON_ROWS` + `renderSeasonRows()` in globe.html) — the two old
  call sites typed `renderRowModes('fires')`, which is why the patrol pill
  stayed empty while the map was drawing the lines.
* **The menu row is `glyph · what it shows · how`** — "season front · contours",
  "patrol pressure · iso", "entry ground · cells". The other way round, two
  renderings of one category were two rows both labelled `cells`. A count rides
  in the annotation (`vanguard · 15 in view`), which replaced the pin's second,
  footprints-marked button.
* **In-menu legends are `.compact`**: caption + ramp + first line of numbers;
  the method prose folds away and `openModeMenu()` moves it into the legend's
  `title`. `.mode-menu` is capped at 252 px / 76 vh (300 px / 58 vh on a phone)
  — an unfolded fire row used to be taller than the screen, putting its own
  switches out of reach.
* **The pulse is gone.** `renderRowModes()` caches `el.__lodHTML` and writes
  only on change, and flashes `.changed` only when the *rendering key* changes;
  it used to rewrite `innerHTML` every playhead frame, replaying the flip
  animation ~12×/s while contours were on.

**The chip row rests to what is drawn.** `#anim-chips.rested` folds every chip
that is OFF (width + opacity, negative margin to swallow the flex gap; never
`display:none`, so it grows back) and shows one button whose glyph names its
direction: `⋯ n` while folded (n more renderings), `«` while open (fold back).
It opens rested, and folds on the gestures that mean "done choosing, looking
now": play, a grab of the playhead, a hand on the map canvas (`pointerdown`/
`touchstart`/`wheel` on the canvas — **not** `map.on('movestart')`, which
also fires for the code's own `fitBounds` after a chip, and whose
`originalEvent` is absent on wheel zoom). Never on a timer, never on hover,
never on a chip toggle: an earlier idle/hover version reflowed three lines to
one under a finger mid-decision. A chip switched OFF while resting stays
(`.recent`) until the next fold gesture. `highlight` never folds: it is the
switch that can rebuild the whole set. One entry point: `attentionOnMap()`.

**Highlight yields to a composed map.** The curator is ON only for a plain
open: `opts.layers` naming layers *or* `anySeasonOn()` leaves it off, so a link
carrying `season=front,patrol,pressure` is not clobbered by the `now` profile
half a second after it restored. A date-window change **reopens** the same
animation and must carry the profile **id** (`A.highlight || false`, not
`!!A.highlight`) and `close({ keepSeason: true })`.

**GIF export composites a real map frame.** `mapFrameInto()` calls
`map.triggerRepaint()` and copies the GL canvas from the map's own event:
`preserveDrawingBuffer` is false, and the season layers are MapLibre's, so the
old straight `drawImage(map.getCanvas())` froze the contours while the canvas
fires moved. **Which event: `idle` when a season rendering is on, `render`
otherwise** (2026-09-17). `setData` is a worker round-trip, so the first
`render` after `triggerRepaint` still paints the *previous* contours — measured
in the browser, the render-hash sequence was the idle-hash sequence shifted by
exactly one frame. `idle` costs ~0.9 s/frame (160 frames ≈ 52 s), so a
canvas-only export keeps the 180 ms `render` path. `draw(t, true)` forces
`FireSeason.animAt(t, force)` past its 80 ms coalescing — a frame dropped on
screen is invisible, a frame dropped in a file is the file being wrong.

**A module that throws at load looks like a CSS bug.** A pair of backticks in a
comment *inside* anim.js's injected-CSS template literal ended the literal;
`node --check` passed, `window.Animator` was never defined, and the only
symptom was the "Animate" button rendering as an unstyled native `<button>`.
`tests/js_load_smoke.js` (wired into `run_ui_tests.sh`) now loads each module
under a stub DOM and asserts its global and its injected selectors. **Never put
a backtick in anim.js's CSS block.**
