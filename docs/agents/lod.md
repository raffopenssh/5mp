# Level of detail (feature loading)

_Split out of AGENTS.md. Read when working on this area._

## Level of detail: one loader, and the same feature at every zoom

**No active handover.** `docs/HANDOVER_LOD_FEATURES.md` is done and deleted;
what follows is the record.

The rule the whole thing enforces: **the same feature is the same feature at
every zoom, and a cheap rendering may not quietly become a picture.**

`srv/static/lodlayer.js` is the one loader for the stats-panel toggles *and*
pinned fire/deforestation/settlement layers. It asks
`/api/features-in-bbox?mode=auto` and the **server** decides geometry vs
centroids from the true count in view — never from a zoom number, because two
views at one zoom differ by three orders of magnitude. A centroid carries its
row id, so hovering it fetches `/api/feature-detail` and shows the same tip the
geometry would have.

### The four measured costs, and what each one was

1. **`/api/fire-anim-trajectories` parsed 816 MB of JSON per request.** It read
   `data/fire_groups_v5/<park>.json` through a 40-park LRU for one thing the
   database did not have: the **date of each vertex**. 7.8 s at `limit=800`,
   17 s at 4000, >120 s continental. That date is a column now — migration
   **051** `feature_geometries.traj_days`, a compact array of day offsets from
   `start_date`, always the same length as the coordinate list (a Point is
   `[0]`). Written by `load_fire_groups_to_db.py` (`day_offsets()`), backfilled
   once by `scripts/backfill_traj_days.py` for all 757,754 rows. The endpoint is
   now two indexed passes with no file I/O: **continental 12,000 paths in 1.7 s**
   (was >120 s for 800). NULL `traj_days` is handled, not skipped — points are
   spread evenly across `start_date..end_date`, so a partial backfill degrades
   the *timing* of an animation rather than emptying the map.
2. **`end_date >= ?` is not in `idx_fg_bbox_scan`**, so the natural overlap
   predicate dropped the index. Ask `start_date BETWEEN from-trajMaxSpanDays
   AND to` (200 days; the longest group in the table is 167) and keep the
   `end_date` term as a filter. Dropping the tail *in Go* instead returned 2,409
   of a requested 4,000 — a sparse map that looks like missing data.
3. **`ORDER BY stat_value DESC LIMIT n` is a corner, not a sample** (the same
   trap as the settlements stripe). The trajectory endpoint now streams into
   `spreadCollector` like `/features-in-bbox`, so a truncated continental answer
   is spread across the view and `total` is the true count.
4. **Properties, not shapes, were the payload.** A fire trajectory carries ~350
   bytes of coordinates and ~750 bytes of properties, mostly a narrative
   sentence nothing on screen reads. Above `geoSlimAbove` (1200 features) the
   answer ships identity fields + `rid` and skips `enrichFeatureProps`; the tip
   fetches the rest on hover, exactly as points mode does. 14,350 fire paths in
   an 8° view: **4.1 MB → 2.6 MB and 1.3 s → 0.25 s**, which is what let the
   client's vector budget go 6,000 → 12,000 and the animator's 800 → 6,000.

### Detail is ink, not just count

Drawing 5,837 trajectories at the old width/opacity is a **solid red sheet**:
every path present, none legible, and less informative than the dots it
replaced. `densityPaint()` (lodlayer.js) and the `inkW`/`inkA` ramp in
`anim.js` thin and de-opacify strokes as the count rises, so overlaps
accumulate into structure — corridors and repeatedly-burnt ground glow on their
own — while a sparse view stays bold and obviously clickable. Arrows fade out
with them: a glyph every 100 px across 2,000 overlapping paths is noise.
**Never judge this by feature count alone; look at the render.**

### The renderer may not play one layer off against another (2026-08-16)

**A layer must never draw a shape nobody can see.** The `mode=auto` switch was
a pure **count** budget, so at AOI scale deforestation (80,408 rows in view)
stayed `geometry` while settlements (74,904) crossed the budget into dots. A
GLAD loss patch is ~0.0017° across — **1.4 px at z8** — so "shapes" was a
sub-pixel purple stipple on the 30 m grid beside solid amber dots, and the layer
read as *broken*, not as *rare*. Two rounds of alpha tuning failed because **the
geometry, not the ink, was the bug**; both per-type alpha boosts are now gone
(`lodlayer.js` — do not re-add one).

`mode=auto` therefore answers two more questions, both in `srv/features_bbox.go`:

* `subPixelShapes(cands, bbox)` — **polygon** layers. Median served feature
  under `subPixelPx` (3 px, at ~1400 px of viewport width) ⇒ dots. Median, not
  mean: one 0.03° clearing among 80,000 tiles must not vote for shapes on
  behalf of all of them. 3 px, not 1: at 2 px a patch is one antialiased square,
  a *worse* dot than the dots layer draws (no radius/alpha ramp).
* `inkCrowded(cands, bbox)` — **line** layers. A path's cheap tier is a chord
  (`?seg=1`), and all the full geometry adds is the wiggle *between* the three
  chord vertices — invisible once strokes overlap. So promotion depends on ink
  (Σ span×2 px vs viewport area, `inkCoverMax = 1.0`), not on row count: a dense
  fire field keeps chords at 10× less payload, a sparse one still promotes.

The reason is **named**, not just applied: `render_basis` is `budget` |
`subpixel` | `crowded` | `""`, carried through `lod:state` into the row's mode
tooltip (`LOD_BASIS_WHY`, globe.html). Dots beside a sibling layer's shapes is
indistinguishable from a layer that failed to load unless the row says which
question the renderer answered.

Guards: `tests/api_tests.sh` `lod_no_subpixel_shapes_*` measures the median
**served geometry** in px over four Chinko boxes at three scales and fails a
`geometry` answer under 2 px (and fails an *empty* fixture box, so the check
cannot pass by matching nothing); `lod_render_basis_named`,
`lod_lines_promote_when_sparse`; `srv/features_bbox_render_test.go` unit-tests
both predicates at the measured feature scales. `?subpixel=0` restores the old
count-only behaviour — it is how the regression was reproduced (1.4 px shapes).

**Stacking order is pin order, always** (`restack()`, `seq` assigned in
`LODLayer.add`). A rendering crossing its geometry/points threshold re-adds
layers, which used to shuffle whoever reloaded last to the top; now every
`ensureLayers` re-asserts the add sequence, so the layer pinned last draws on
top and stays there, and re-adding an existing key (date change, option
update) keeps its place.

⚠️ **The animator's ramp keys on what is drawn AT `t`, not on what was
loaded.** The map draws every feature in the view at once; an animation draws a
group only between its own `t0` and its ash-out, so a window holding 6,000
trajectories typically shows a few hundred in parallel. Keying `inkW`/`inkA` on
`D.trajs.length` therefore thinned a frame that was nearly empty, and the
original thick opaque strokes were simply better. It counts the live ones per
frame (cheap: off-screen groups are pre-flagged) and only thins past ~800
simultaneous paths.

The animator's fire-grid blob radius must also **cover its cell**
(`cellPx * (0.8 + 0.6*inten)`), or the layer draws a halftone lattice — a
picture of our 0.1° binning rather than of the fires.

### The transition is shown, not hidden

Crossing the threshold is the one moment the map changes *what* it is showing.
Unannounced, a user zooming out watches their trajectories "become dots" and
reasonably concludes the app threw the detail away. So:

* the two renderings share one source id and **cross-fade** (220 ms);
* the incoming one **overshoots and settles** (`focusPulse`, 380 ms) — the same
  gesture in both directions, because the claim is that nothing was lost either
  way. `focusPulse` must settle back to the **density** width, not a constant,
  or it silently undoes `densityPaint` on every crossing;
* the state is written into the control that switches the layer on —
  `.stats-lod` inside the stats row (`setLayerLOD`), and a ◇/· mark on the
  pinned chip. **Not a floating HUD**: the question is about one layer, and a
  HUD would be a fourth thing competing with the toast, the pinned indicator
  and the time slider for the same corner. It takes the row's own colour at low
  alpha; on mobile only the pill survives (the count is already the row's
  value). It replaced a toast fired per truncated fetch — i.e. on every zoom,
  covering the map to say something permanent about the view.

### A count must name its unit (2026-08-12)

**A settlement is a CLUSTER; `feature_geometries` holds its FOOTPRINTS.**
`park_settlements` rows are clusters of adjacent GHSL built-up polygons
(`rebuild_events_enhanced.py`); the polygons live in `feature_geometries` and
are what the map draws. Chinko is **35 polygons and 27 settlements**.

Every surface counted honestly and they still disagreed, because none of them
said what it was counting: the stats panel and the popup count clusters, this
loader counted the polygons it had drawn and printed the result beside the word
"Settlements". Three numbers, one word — read as a data-quality problem, which
is worse than a wrong number because it impugns everything next to it.

`/api/features-in-bbox` now names its unit: `unit` (`footprints` for a
settlement, `features` otherwise), plus `groups` and `group_unit`. The group
count is over **everything in view**, not just the served sample, resolved
through the same per-park map the hover tips use (`settlementGroupKey` in
`feature_meta.go`) — **never** the `polygon_ids` LIKE join. A footprint no
cluster claims is **its own group**, not dropped: it is on screen, so it must be
in the number describing the screen (invariant 1).

The readout leads with the number the rest of the app means by the word and
names the drawn one after it — `27 in view (35 footprints)` — and **only when
they differ**: "27 in view (27 footprints)" is noise, and a clustering that
happens to be 1:1 in this view is not worth a parenthesis. `.lod-sub` is
visibly subordinate, or the row reads as two competing counts again, which is
the bug this exists to close.

Suppressed while truncated (`!st.truncated`): `groups` describes everything in
view but `count` describes the sample, so pairing them under truncation would
put two different denominators side by side — the failure this section is
about, one level up.

### Panning is free, zooming re-asks

`lodlayer.js` fetches a **25%-padded** box and compares against the unpadded
view, so a small pan is answered from memory. It skips a refetch that cannot
reveal anything (contained + untruncated + already geometry), and in points
mode also skips a pan or zoom-out inside a covered box — only a meaningfully
smaller view can promote it to geometry.

### The animator's two downloads are share links

GIF and GeoPackage were two bare buttons that could not be handed to anyone,
which is backwards: the whole point of both is that you found a frame worth
keeping. They are now one ⬇ opening the **same `.aoi-menu` component** the park
and AOI downloads use — one row each, each with a ⧉ that copies a link
reproducing this exact animation (window, viewport, layers, speed, playhead)
and pointing at that row. `?anim_export=gif|gpkg` **highlights, never runs**:
same rule as `aoi_menu_item=`, because opening a link must not spend minutes of
CPU. The GIF row hides itself on mobile (encoding is minutes and a hot
battery). Reusing the menu also inherits its Safari copy-link workaround and
its touch sizing for free.

### A view GeoPackage is immediate when fast, a notification when not

`?wait=<s>` on `POST /api/view/export.gpkg` (capped 20 s), migration **050**
(`view_json`) so a card that outlives its tab can still describe and retry
itself. Same job, cache, 21-day link and delete button as the area export — it
is a different *question* (only what is on screen), not a different mechanism.

### Still open, deliberately

Nothing. The three that were listed here are done — see "The cheap tier for a
path is a chord" below for what closing them changed.

### The cheap tier for a path is a chord, not a dot

Collapsing a fire trajectory to its centroid destroys the one property that
distinguishes a fire *front* from a hotspot: the direction and distance it ran.
A whole AOI of them was a red stipple that said less than the map it replaced.
`?seg=1` on `/features-in-bbox` returns a three-point chord per feature (first /
middle / last vertex) alongside `points`/`ids`, and `render` becomes
`"segments"`: ~50 bytes against ~350 for the full path, so `XSA_Study_Area`
draws **all 38,725** trajectories as lines (3.4 MB, 1.0 s) instead of 12,000 as
paths or all of them as dots.

* **The chord reuses the LINE layer.** It is the same feature drawn shorter —
  same paint, same hover, same arrows — and must not become a special case
  anywhere downstream. Only `pointsToGeoJSON()` knows the difference.
* **The middle point is the middle VERTEX**, not the average of the ends: a
  fire that ran out and doubled back would otherwise draw a straight line
  through ground it never touched.
* **`scanCoordPairs` is a byte scan, not `json_extract`.** Six `json_extract`
  calls per row (two with a concatenated path, so the document is re-parsed)
  measured 30 us/row = **1.2 s** for one continental view; the byte scan is
  ~1 us. Same class of trap as `ABS()` in a WHERE clause: the obvious SQL is
  the slow one.
* A degenerate chord stays a **Point**, not a zero-length LineString —
  MapLibre draws nothing for the latter, so a stationary fire would vanish in
  the rendering meant to show more.

**Points wear the layer's ink above ~600 features.** A white `circle-stroke`
ring is a "click me" affordance and is right for a handful; across a field of
thousands it is the loudest thing on screen — 103 stationary fires out-shouted
14,700 moving ones. `densityPaint()` returns `ring` and `pointOpacity` and
drops the ring past 2,000.

### `?class=` is server-side, and a filtered pin follows the viewport

The popup's classification filter used to be applied in the browser over a
whole-park fetch, which is why a filtered pin could not use the LOD loader at
all: neither the chord nor the slim-geometry rendering ships the property it
filtered on. It is `?class=` now, resolved through the same Go-side map as the
hover tips (`featureIDsWithClass` in `feature_meta.go`) — never the
`polygon_ids` LIKE join.

`?age=` and `?crop=` (2026-08) are two more dimensions of the same filter and
AND with `?class=`: settlement GHSL persistence
(`permanent|established|recent|unmeasured`), settlement cropland-nearby
(`crops` = frac_2019 ≥ 3%, the same threshold the "Cropland nearby" stat uses
`|nocrops|unmeasured`), deforestation conversion (`converted` =
cropland_conversion_frac ≥ 0.5 `|regrows|unmeasured`). Bucket words live in
lockstep in `featureIDsWithClass` (Go) and
`settlementCropBucket`/`deforestConvBucket` (globe.html) — NULL is its own
`unmeasured` bucket, never a value bucket (invariant 1). The popup chip strip
(`filterStrip`/`filterChips`/`selectChip`/`syncFilterChips`, state in
`sectionFilters`) is the only writer of the five URL params
`settlement_filter|settlement_age|settlement_crop|deforest_filter|deforest_crop`;
`buildShareUrl` copies them from `location.search`, and the section renderers
restore them by seeding `sectionFilters` then calling the filter handler once.
Design choice: no per-dimension "all" chip (empty selection means all); one
shared ✕ appears only while filtering; `.filter-strip.filtering` dims
unselected chips.

* Applied **before** the spread collector, so `total` counts what passes it and
  the selection spreads over the filtered set. A filter that changed the
  picture but not the number reads as broken.
* Needs `?area=`. A type with **no** classification (fire_trajectory) serves
  the unfiltered superset: *cannot apply* is not *excludes everything*.

### `?area=`, because `?park=<aoi>` is a 404 by design

`ParkIDMiddleware` 404s an AOI id in `?park=` on every request — correctly, an
AOI is not a park. The viewport-first pin sent it anyway, so **every** AOI fire
pin fetched nothing and reported "0 features in view" with a toast telling the
user to pan. The recurring failure mode, once more: a no-op that reads as an
answer. `?area=` carries the same scope and accepts either id; `?park=` still
works for parks and still 404s for an AOI (pinned by
`features_bbox_park_param_still_404s_aoi`).

### The detail control moved onto the layer, and is now a MENU of renderings

`.stats-lod`'s readout is a **dropdown** (`openLayerModeMenu`), and every pinned
LOD chip carries the same one (`openPinModeMenu` → `openModeMenu`), reusing the
`.aoi-menu` component the downloads already use. It was a cycle button
(`cycleLayerDetail`/`cyclePinDetail`, both kept for old call sites) through
`auto → shapes → fast`, which stopped working the moment the list stopped being
three items and one dimension — see "The legend states every rendering" in
`animator.md`. Deliberately per layer, not global — 12,000 fire fronts are worth
waiting for as shapes, 78,000 built-up polygons never are. The preference is
expressed as `geom_budget`, so the **server** still decides what fits and a
forced answer still truncates honestly instead of promising something the
browser cannot parse. (`geom_budget`'s ceiling was 20,000, silently clamped,
which made "forced shapes" a no-op on exactly the views that wanted it.)

`?spread=0` and `?simplify=0` are documented in `docs/API.md` as what they are:
developer escape hatches for proving the defaults are the same answer. Neither
belongs in a share link.

The detail preference itself is deliberately **not** in the share link either:
it is a statement about this screen's patience, not about what is being looked
at, and `?detail=` already means the *geography* tier (`major`/`main`/`all`),
which is a different thing entirely — that one is a WHERE clause and must
reproduce, this one is a budget.

### An individual pin is served whole, and it says so (2026-08-16)

A pinned *town* stayed one dot at z16 while every other layer gained detail.
It was not the loader: an individual pin (`addSinglePinnedFeature`) never goes
through `LODLayer` — it is one named feature, fetched once and drawn at every
zoom — so there is nothing to promote. The bug was upstream of that, and it was
invariant 7 again: **a settlement is a CLUSTER, `feature_geometries` holds its
FOOTPRINTS**, and `/features?type=settlement&feature_id=settlement_<id>` was
matching that id against `feature_geometries.feature_id`, missing, and falling
through to the cluster's stored `lat`/`lon` as a Point. So pinning a town of 21
built-up polygons drew its centroid, and no zoom could improve on it.

`settlementFootprintIDs` (`srv/feature_meta.go`) resolves the cluster id to its
`polygon_ids` and the handler serves **all** of them — the same shape as
`event_id=` for deforestation, which was already whole (31 patches for Chinko's
biggest event). Retired detector rows are excluded here as everywhere
(`scannerInjectedRow`): a footprint-less row is not a settlement, and pinning
must not be the one door that lets it become one. A footprint id still pins
itself, so nothing that worked before changed. Test:
`settlement_pin_serves_all_footprints` (api suite), with the count **derived**
from `polygon_ids` and the cluster chosen as the one with the most footprints —
a park of 1:1 clusters must not pass it vacuously.

Two consequences, both "the same feature at every zoom" applied to pins:

* **A pin with extent is fitted, not flown to.** `flyToSinglePin` used
  `zoom: max(current, 10)` on a guessed centre; it now `fitBounds` the pin's
  own bbox (`singlePinBBox`, one walk over whatever geometry it has) whenever
  that bbox is bigger than ~0.004°. Arriving at z10 over a 3 km cluster is the
  same "nothing to see" complaint, one layer up.
* **A point pin scales with zoom** (`circle-radius` 2.5→7 interpolate), like
  the loader's dots. A fixed 2 px is unfindable at the zoom the pin flies you
  to.

And the chip now names its rendering, in the *same words* as the stats rows and
the LOD chips (`LOD_MODE_LABEL`): `shapes`, `dots`, or `mixed` when several
pins of one type disagree. It is `.chip-lod.static` — no menu, no pointer —
because an individual pin has no budget to spend: it is served whole by
definition, and the mark is a statement of fact. Silence there is what made a
correctly-drawn Point read as a layer that had thrown its detail away.

## Waiting is said in the chip that will answer it (2026-08-16)

Opening a large share link (`/s/g-…`, an AOI with three pins over a 2.7-year
window) showed an empty map for several seconds with nothing saying why. Two
separate faults, found by wrapping `fetch`/`LODLayer.reload` and reading the
stacks.

**1. The same question was asked three times.** "The dates changed" is
announced by three call sites, and on a restored link two of them fire *after*
the pins have already loaded with those very dates: `initTimeSlider` →
`applyPreciseDateFilter` at t≈2.4 s, and `reloadGridData` again when the
`areas` source finishes (t≈4.6 s and t≈11.8 s). Each announcement reached
`LODLayer.reload()`, which cleared `lastBBox` and refetched every layer — so
the fire trajectories' 1.2 MB / 2.8 s payload was fetched **three times**, the
later copies aborting the earlier ones mid-flight. The 80 ms debounce cannot
help: these are seconds apart.

The fix is that **the request is its query string**. `load()` builds `qs`,
compares it to `s.inflightSig` and `s.lastSig`, and returns if either matches;
`why === 'force'` bypasses it for "the rows behind an unchanged query may have
changed" (an upload, a reingest). `reload()` no longer clears `lastBBox` —
it never consulted it (the containment skips are `why === 'move'` only), and
clearing it was precisely what defeated the signature check. Result on that
link: **6 heavy fetches → 3**, and pans/date changes/detail switches still
refetch (each changes the string). Pinned by `lod_dedupes_by_signature`,
`lod_dedupes_inflight`, `lod_reload_keeps_bbox` in the ui suite.

**2. The pin did not exist until its data did.** `addPinnedLayer` awaited
`LODLayer.add()` and only then wrote `pinnedLayers[key]`, so for the whole
fetch there was no chip, no count, no evidence of work — and a user with no
evidence clicks, pans and re-toggles, which aborts the fetch and starts it
again. The pin is now registered **before** the fetch with `loading: true`, and
the loader owns the flag from then on (`onLoading`), so a refetch on pan or a
date change says the same thing the first load did. Two consequences that are
easy to get wrong: the pin may be **removed while its data is in flight** (the
× on a loading chip is a legitimate click and the whole point of showing one),
so the post-await path re-checks `pinnedLayers[key]` instead of re-asserting
it; and `updatePinnedIndicator` must not resurrect a dismissed layer.

**The dots are in the count's slot**, not beside it: a stale number about to be
replaced reads as the answer. Three `currentColor` dots on a travelling wave
(1.05 s, 0.13 s stagger, scale+opacity only — compositor properties, no
layout), the chip carrying one slow same-tint sheen so a glance at the panel
reads "still arriving" without resolving three 3 px dots. `prefers-reduced-motion`
gets three static dots of graded opacity. The count animates in once on arrival
with the chip's own 0.22 s ease — the value is the news.

**A repaint must not restart a neighbour's animation.** This function runs on
every count, every load and every pan, and it assigned `innerHTML` wholesale —
so each pin's answer landing tore down and rebuilt the other two chips,
resetting their wave to frame zero exactly when it was meant to be reassuring.
The park/chip **shape** is now compared (`_pinShape`) and only rebuilt when it
changes; otherwise each chip is addressed by `data-chip-key` and swapped alone.

**Basemap switches cost nothing** (verified): `switchBasemap` replays sources
and layers, so satellite ↔ dark is 0 fetches and the pins keep their data.

### `font: 600 8px/1 inherit` is invalid, and fails silently

Found while checking the chip's type scale. The `font:` shorthand's family slot
cannot be a keyword, so the browser drops the **whole** declaration — and a
`<button>` falls back to the UA's 13.33 px/400. That is why `.chip-lod`, the
quiet footnote naming what is drawn, printed *larger and lighter* than the count
it annotates. The same mistake was in four more rules (`.sl-day`, `.sl-copy`,
`.sh-btn`, `.footer-btn.share-btn`) — every one a button, every one silently at
UA size since 2026-08-12. All five are longhands now, and
`no_font_shorthand_inherit` fails the suite if the shorthand comes back.
