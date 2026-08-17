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
  (720px; hidden on mobile). **The GIF plays back at the on-screen speed**: its
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
