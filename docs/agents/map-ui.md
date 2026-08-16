# Map interaction (hover tips, selection)

_Split out of AGENTS.md. Read when working on this area._

## Map interaction: hover previews, a click SELECTS, the selection is a place

`srv/static/maptip.js` owns the hover tip **and the selection** for every
interactive map layer — registered MapLibre layers and canvas `probe`s alike.
One model, one gesture set, the same on a mouse and a finger. None of it is
ours to invent; it is the shape every map application the user already knows
uses:

* **Google/Apple Maps** — tapping a POI drops a card that *stays*, anchored to
  the ground so it survives panning; you dismiss it with its × or by tapping
  empty map; a tap where there is no POI still answers, with the area you hit.
  The card is **not** the place page — it is how you get there.
* **Illustrator / Figma / CAD** — clicking overlapping shapes selects the
  topmost, and there is always a way *down* the stack (alt-click, "select
  behind"). Overlap never makes a shape unreachable. We show the stack as tabs
  instead of hiding it behind a modifier, because a list is discoverable and a
  modifier is not.

| gesture | meaning |
|---|---|
| hover (mouse) | transient preview at the cursor |
| click / tap | **select**: the card pins to the ground, gains × + action button |
| click the same again | unselect (a selection is the one thing that toggles) |
| click the void | clear — but "void" = *nothing answered*; a click inside a big polygon selects **that polygon** |
| double-click / ⏎ | skip the card, open the real thing (the express lane; the card's button does the same) |
| Esc | clear |

Load-bearing consequences, each of which was got wrong at least once:

* **Hover and selection are two separate DOM nodes**, not one tip that changes
  mode. A selection erased by moving the mouse is not a selection; a preview
  you must dismiss is not a preview. Hover keeps working *around* a selection
  — otherwise the card is a modal dialog in a tooltip's clothes — and
  `position()` moves the preview out from under the pinned card (which is the
  fixed one; the user put it there).
* **Tabs are not mobile-only.** They were, on the theory that a mouse can hover
  for the other answers; it cannot — hovering the same pixel yields the same
  winner forever. The pinned card lists every answer at the point on both
  pointers. That *is* the "select behind" affordance.
* **`clickOnly` suppresses the HOVER tip only.** It never meant "do not answer
  a click": clicking a backdrop is precisely how you reach it.
* **`activateOnClick`** — where a layer's full surface *is* the selection and
  its popup is already everything the card would be *and more* (the AOI), an
  **unambiguous** click opens the popup directly rather than pinning a
  three-line copy of it. Only when it is the sole answer at the point:
  otherwise the click has to ask *which*, and the card with its tabs is that
  question — the Area tab then carries the same button, so nothing becomes
  unreachable.
* **The selection is in the share link as a PLACE**: `?tip=<lng>,<lat>`
  (+ `tip_layer=` picking within the stack). Not a feature id — ids are not
  stable across a rebuild, the layer may return at a different level of detail,
  and "what is at this point" is the question the link reproduces. Restoring is
  a **re-ask** (`MapTip.pinAt()`), retried on a decaying schedule because the
  layers the same link carries land asynchronously, and it gives up quietly.
* **The action button names its destination, never a generic verb.**
  "Open in report" pointed at a *popup*, and the star report is a different
  thing entirely (several areas, printable). It is `areaOverviewLabel(id)` →
  "Open park overview" / "Open area overview"; `actionLabel` may be a function
  of the feature because a pinned layer only knows which kind of area it serves
  per row. `openAreaOverview()` (was `openFeatureInReport`) routes park→
  `showPAPopup`, AOI→`showAOIPopup`, then shares one `scrollAreaOverviewTo()`
  because both popups use the same section ids and entry markup.
* **The map and the overview list key the same thing differently, and the
  handoff must translate** (`overviewFeatureIDs(type, props)` in globe.html).
  Invariant 7 again, this time as a *lookup*. Two of the three narrative layers
  count in a different unit on the map than in the list:

  | layer | list row | map feature |
  |---|---|---|
  | settlement | cluster, `settlement_<park_settlements.id>` | footprints, `settlement_ghsl_<area>_<lat>_<lon>` |
  | deforestation | event, `event:<deforestation_events.id>` | patches, `deforest_<area>_<year>_<n>` |
  | fire, road | — same id — | |

  Every activation path handed the *polygon's* id to
  `scrollAreaOverviewTo()`, which asked for a selector the list has never
  emitted, and the honest fallback toast — *"This feature has no matching entry
  in the overview list"* — was the only visible result of clicking any
  settlement or any clearing on the map. So the server ships the list's id as a
  property (`settlement_id`, `event_id`, set in `enrichFeatureProps` so they
  reach `/api/features-in-bbox` and `/api/feature-detail` alike), and all three
  activation paths (`lodlayer.js`, `anim.js`, the pinned-layer registration)
  translate through `overviewFeatureIDs()`.

  It returns a **list, best first**, because one layer's list has more than one
  shape: deforestation falls back to one row per YEAR
  (`deforestation_year_<y>`) when nothing is classified, and that row is a
  legitimate destination for a click on a patch. `scrollAreaOverviewTo()` tries
  each in turn — one extra `querySelector`, and a "no matching entry" becomes
  the coarser-but-true answer. Deforestation also accepts `deforest_id` from
  `properties_json` (`scripts/import_events_from_json.py`) for rows the
  `polygon_ids` join misses.

  Two related traps found in the same fix:
  * `TEST.triggerLoadMore('settlement', …)` and `scrollAreaOverviewTo`'s
    paging loop both looked up **`settlement-more-btn-<id>`**; the button the
    list renders is **`settlements-more-btn-<id>`** (plural). A
    `getElementById` that always returns `null` is a paging step that silently
    never happens — invariant 1 in a single missing letter.
  * "Show more" **re-rendered** the remaining rows into the container while the
    same rows were already in the DOM inside the hidden div, so every entry
    past the tenth existed **twice** and `querySelector` could win the
    permanently-invisible copy. It reveals now (moves the children out, hides
    the div); the filter code already assumed exactly that.
* **On a phone the pinned card DOCKS to the bottom**, so it does not cover the
  feature the finger is already on. `bottomChromePx()` **measures** the toolbar
  and the time slider — a hard-coded `bottom` lands on both, and both change
  height on a narrow screen. Note `offsetParent` is **null for a
  `position:fixed` element**, which is exactly what that chrome is: the first
  version's visibility guard skipped both and docked right onto them.
* `MapTip.hide()` still means "both, now" and is what `showPAPopup()` /
  `showAOIPopup()` call: the popup is the same answer in full, and one answer
  on screen at a time.

### Precedence — draw order is not intent

Two things it arbitrates, both added 2026-08-10 after a tap produced
three overlapping answers at once (geology popup + AOI popup + AOI map tip):

* **`priority` (default 0, higher wins, render order breaks ties).** Draw order
  answers "what is on top", not "what did the user mean". Two layers are
  *backdrops* — the AOI polygon (`-20`) and the geology drape (`-30`) — and both
  sit under the cursor almost everywhere, so whichever happened to be drawn last
  won every hit-test and buried the specific feature underneath it. A pinned
  fire/settlement/deforestation layer is priority 0 and is therefore always
  asked first. `+N more here` counts peers only: a backdrop under a trajectory
  is context, not a second thing to zoom in and separate.
* **`clickOnly`.** A layer covering the whole viewport must not hover-tip —
  there is no "off it" to move to, so the tip just follows the cursor forever.
  Use it **sparingly**: only where the layer also owns the click (the AOI
  polygon, which opens its own popup). Geology is not such a layer. It has
  never suppressed the *click*; see `activateOnClick` above for the layer whose
  click bypasses the card entirely.
* **A click opens the real thing, never a copy of it** — and the card is not a
  copy, it is the route. This used to read "a click on a fine pointer runs
  `onActivate` directly", which was half right and cost the mouse user every
  answer underneath: `onActivate` is now reached from the card's button, from
  a double-click and from ⏎, while the click itself selects. The AOI keeps the
  direct behaviour via `activateOnClick`, and only when it is the sole answer.
* **The card carries every answer at the point as tabs on BOTH pointers**
  (`tabLabel` / `tabColor`, priority order, winner selected). Restricting them
  to a coarse pointer assumed a mouse could hover for the rest; hovering the
  same pixel yields the same winner every time, so on a mouse the losers were
  simply unreachable.
* **`+N more here` counts peers of the same feature, not tile parts.**
  `queryRenderedFeatures` returns one result per tile per part, so a
  multipolygon AOI reported "+3 more" about *itself* and a dissolved geology
  class "+96". Identity is the feature id, or the properties themselves when a
  vector tile carries none (`featureKey()`).
* **A park is a REGION, not an exception** (`registerParkTip`, priority -10).
  It used to own the click outright, and `MapTip.setBackdropGuard(fn)` silenced
  every negative-priority tip over a park polygon so the two would not both
  answer. The guard is **gone**. Its cost was that inside a park — most of what
  this map is about — geology and the AOI were not merely outranked, they were
  *erased*: no tab, no card, no way down the stack, and a tap on gold-hosting
  basement inside Chinko opened Chinko. The full ladder is now just numbers:

  | priority | layer |
  |---|---|
  | `0` | fires, settlements, roads, trajectories, probes |
  | `-10` | **park** (`areas-fill`) — a region, but the smaller one |
  | `-20` | AOI (`aois-fill`) — usually far larger, so it loses to a park |
  | `-30` | geology drape — a whole country |

  The park carries `activateOnClick` for the same reason the AOI does: sole
  answer → its overview popup opens directly (the old behaviour); several
  answers → one card, one tab each, Park selected and carrying the same button.
  Its `html` **declines** on shift/⌘/ctrl-click, because that gesture is
  multi-select and belongs to `setupPAClickHandler`, which still runs and still
  stands down on `window._mapTipClicked`.

  Anything that reads "if a park covers this point, stand down" is this bug
  coming back. Rank it; do not delete it.
* **Geology went through `maplibregl.Popup`**, so its click never reached the
  shared arbitration at all. It is a MapTip registration now, and `remove(id)`
  unregisters it — otherwise a switched-off sheet keeps swallowing clicks.
  The raw-Popup path survives only as a fallback if `maptip.js` fails to load.
* **The popup's × is FloatUI's own button, not MapLibre's relocated one**
  (`decoratePAPopup`). MapLibre's × used to be re-parented into the grab bar
  and left to MapLibre's internal listener — inside an element that calls
  `setPointerCapture` to drag. Browsers disagree about which element owns a
  click made under pointer capture (Safari charges it to the capturing bar), so
  on Safari the × did nothing and the card could not be dismissed at all.
  MapLibre's × is now `display:none` and the bar builds its own button calling
  `popup.remove()`. Never move a foreign library's control into a drag handle
  and keep its listener.
* **A popup restored from a share link can render with its grab bar off-screen**
  (`/s/bhk8ps5`, 2026-08-16). The popup stays *anchored* through programmatic
  moves (correct — the fly-to should land it on its area), but a share link
  sets its own `lat/lng/z`, and a large AOI's centroid can sit above the
  viewport: the card's top (grab bar, name, ×) was simply outside the map until
  the first user gesture happened to detach it. `decoratePAPopup` now runs
  `ensureVisible()` on map `idle` + one rAF: if the bar is off-screen it
  detaches and `clampIntoView()`s. Skips sheet mode and docked.
* **`.pa-popup-mini-stat` grid was `auto 1fr` with a `nowrap` label** — a long
  label ("Fire detections in groups (Dec 2023 - Aug 2026)") took the whole
  width and left the value a 7px column wrapping one digit per line: a
  180px-tall invisible number reading as empty space in the fire accordion.
  Now `minmax(0,1fr) auto`, label wraps, value never squashes (`globe.css`).
* Where a backdrop wins but hides another, **name the other in the same tip**
  rather than making it unreachable: the AOI tip carries a `Geology · <code>`
  line. One tip, both answers.
* `MapTip.refresh(layerId)` re-renders whatever is on screen when an async
  detail lands (AOI coverage, a LOD row's `/api/feature-detail`), instead of
  leaving "coverage…" frozen on a card that no longer gets a mousemove. It
  updates **the whole tab stack**, not just the visible tab — otherwise
  switching away and back shows the placeholder the detail just replaced.

---

## A share link opens at the viewport it names

`?lat/?lng/?z` used to be applied by `restoreStateFromURL()` on map `load` —
after Africa had already been drawn, and *while* a dozen other restorers were
still landing asynchronously. Any one of them calls `fitBounds`/`flyTo`
(`selectCountry` on a `?country=`, the `?bbox=` restorer, a park fly-to, an AOI
popup, `animateAOI`), several of them on `sourcedata` retries seconds later — so
a link carrying both a viewport and anything else could land somewhere its
sender never was, with nothing on screen saying so.

Two changes in globe.html, both small and both load-bearing:

1. **The URL's viewport is the map's INITIAL viewport** (`_urlView`, read before
   `new maplibregl.Map`). No flash of the default view, and no race to lose.
2. **It is then held** for ~6 s on a decaying schedule (`_holdURLView`), unless
   a real gesture has happened — a drag, wheel, pinch, key, or any capturing
   `pointerdown`. A person always outranks a link; a restorer never does.

The hold is `jumpTo`, not `flyTo`: it is correcting something that should not
have happened, and animating the correction would draw attention to it.
Call sites are deliberately untouched — there is no way to tell "the user asked
to fly here" from "a link merely mentioned this park" *at* the call site, but
there is here.

---

## Adding a layer or a focus does NOT move the camera

Three gestures used to fly: focusing a park/AOI (`toggleAOIFocus` →
`zoomToPark`/`zoomToAOI`) and turning on geology (`GeoMap.toggle/toggleAll`) or
a historical sheet (`HistMap.toggle`). Each is *"show me this here"* — a drape
is drawn on the view the reader built, and focus changes **scope**, not place.
Moving the camera answers a question nobody asked and throws that view away;
back-to-back toggles make the whole UI feel hectic.

The calm rule: **apply in place; offer the trip only when the thing is not on
screen at all**, as an action on the toast the gesture already shows
(`offerZoomTo(label, bounds, key)` in globe.html; `focusBBox()`/`bboxOnScreen()`
for areas). Overlapping the viewport = silence — a toast on every toggle is
the same hecticness in another costume. `opts.fly` still exists on
`HistMap.set` / `GeoMap.set` for a caller that genuinely means "go there"; the
toggles pass `fly:false`. `zoomToPark`/`zoomToAOI` remain for explicit
gestures (search results, starred tags).

**Toast placement is measured, not assumed.** The footer
(`#time-slider-container`) grows when the animator opens and when the date tags
wrap, so the old `bottom: 100px` put the toast — and its *Zoom there* button —
under the footer. `toastBottomPx()` measures it, a `ResizeObserver` +
`resize` listener re-runs `repositionToasts()` so live toasts follow the footer,
and multiple toasts stack upward instead of printing on top of each other
(geology + histmap off-screen offers two trips at once). Same lesson as
`bottomChromePx()` for the docked map tip.

---

## The park/AOI popup freezes on the first user map move (`decoratePAPopup`)

MapLibre re-projects a popup on every `move`, which for a card this size is
not "anchored to the ground" — it is the panel you are *reading* leaping
across the screen on every drag. So the ground anchoring only **places** it:
the first *user-driven* map interaction calls the existing `detach()` (unhooks
MapLibre's `_update`, switches to explicit `left`/`top`, `.fui-detached`), and
from then on the position is a screen position — exactly the state a manual
drag or a dock already produced. Every other floating surface (stats panel,
pinned-layers box, pinned map-tip card) was already screen-fixed; the popup
was the only one that jumped.

The discriminator is `e.originalEvent`: `movestart`/`dragstart`/`zoomstart`/
`rotatestart`/`pitchstart`/`wheel` carry one when a hand caused them and
nothing when code did. That distinction is load-bearing — the popup is
routinely opened *together with* a programmatic `flyTo`/`easeTo` (list click,
share-link restore, `zoomToPark`), and freezing on those would strand the card
wherever the camera happened to be mid-flight instead of landing it on its
area. Listeners are removed on `close`; sheet mode (phone) returns early
because the sheet stack owns the position there.

---

## Stats panel window furniture (`floatui.js setupStatsPanel`, desktop only)

The stats panel is the legend. On desktop it wears the standard `.fui-bar`
(grabber + chevron; CSS hides the bar ≤768px — the phone grid owns itself):
drag to move (`fui-moved`, position in `fui.stats.pos`), chevron/bar-tap to
collapse (`fui-collapsed`, persisted in `fui.stats.collapsed`). Three rules
learned here:

- **Collapse compacts, it never empties.** Users still need the legend: every
  `.stats-item` keeps its label, value, colour accent and click target at
  full font size; only `.stats-header`/`.stats-divider`/`.stats-lod` fold
  (the LOD line needs `max-width: 0` too — folded by height alone it still
  sets the shrink-to-fit width). An earlier version folded everything to a
  bare bar and read as "the legend is gone".
- **No auto-rest timer.** A 4 s rest (copied from the map strip) was tried
  and rejected — it took the legend away mid-read. Folding is the user's
  choice only.
- **Snap-home**: a `data-act=home` house button in the bar, shown only while
  `.fui-moved`, clears inline left/top and `fui.stats.pos`.

The focus scope row (`#stats-scope`): the × sits directly beside the focus
name (`flex: 0 1 auto`, not `flex: 1`) — flushed right it read as "close
panel" when it means "leave focus". The row is `align-items: center`
(baseline left icon/name/× on three different lines). Bar icons render at
12px — at 11px the chevron's diagonals antialias fainter than the house and
the pair looks two-coloured.

---

## The Map strip in the stats panel (`srv/static/maplegend.js`)

Basemap and the two drapes (`HistMap`, `GeoMap`) used to be reachable only from
admin ▸ Map Settings, and once a drape was on **nothing on the map said so**.
Two failures in one: a hatched country-sized polygon with no legend is a
rendering the reader has to reverse-engineer, and a geology layer isolated to
*"everything that can host gold"* is pixel-for-pixel a *complete* rock map.
That second one is invariant 7's shape applied to a filter — a partial answer
that does not announce itself.

The strip is the last section of `.stats-panel` (`#stats-map`), and its whole
design is the `.quiet` class:

* **Default state = dark basemap, no overlay = nothing true to say**, so it
  says nothing: no divider, no header, no chip, one `icon-layers` at 28%
  opacity. The opener never disappears — it is the only route to these
  switches that is not four clicks deep — it just stops claiming to be
  information. A permanent "Basemap: Dark" row is chrome that is *wrong to
  read*.
* **A chip is the state and the route to changing it — body configures, `×`
  switches off.** All three chips now (`.ml-chip.base`, `.hist`, `.geo` all get
  `padding: 0` and a `.ml-chip-main` + `.ml-chip-x` pair). It started as "tap a
  chip = that layer off, the opener beside it configures", which is wrong for
  the same reason in three places: the chip is *the only word on screen naming
  the layer*, so it is exactly where a reader taps to ask **which** — which
  imagery is under the data, how strongly is the ink drawn, what rock is this —
  and a single-target chip answered that question by destroying the layer.
  Geology got the `×` first (its body already opened the tables and "off" had
  retreated inside the menu); basemap and historical followed. `.ml-chip-x`
  hover is red, `.ml-chip-main` neutral, and the whole-chip hover is suppressed
  on two-target chips because it reads as one target.
* **A caret means "this opens something"** (`.ml-caret`): `icon-chevron-down`
  for a list, `icon-table-2` for geology, because that menu is a matrix rather
  than a list and the difference is worth one glyph.
* **The historical chip's menu is the opacity slider** (`openHistMenu`,
  `.ml-op`). Traced ink over satellite imagery is either invisible or
  obliterating, so "a bit less" is the commonest thing a reader wants, and it
  lived four clicks deep in admin while the chip in front of them could only
  delete the layer. Live `oninput` (`MapLegend.histOpacity`) updating only the
  `%` label — a re-render mid-drag tears the slider out from under the finger,
  and the point is watching the ink fade against the ground. Font sizing on
  `.ml-op` is explicit (`10px` label, `10px` value): the row inherits nothing
  useful from `.mode-menu`, whose sizes are on `.aoi-menu-item`.
* **Both drapes say "not in view" when they are on but not here**
  (`geoOffView()`, `histOffView()`), and `map.on('idle')` re-renders for
  *either* — a chip that keeps saying "not in view" after the reader pans onto
  the sheets is the contradiction the label exists to prevent. Geology measures
  the canvas (vector, queryable); the historical series measures its archive
  `bounds` envelope, because a raster has no features to query. In that state
  the menu grows a **"Go to the sheets"** row — the one place an overlay moves
  the camera, because the reader asked by tapping a row that says none are here.
* **A filtered geology chip is amber** (`.ml-chip.geo.filtered`) and *names*
  the filter (`gold hosts`, `3 rock types`, `filtered`, `n hidden`), derived
  from `GeoMap.state()` — amber is this app's colour for "you are looking at a
  subset" everywhere else, so it needs no legend of its own.
* **The age key is derived, never listed** — and every part of it is a
  switch. See "The key is the interface" below.
* The menu **reuses `.aoi-menu.mode-menu`** — radios for one-of, checks for
  any-of, `.refused` for a row that keeps its place and says why on hover. A
  sheet that is not built must read as "not installed here", never as "this map
  has no geology". No second control vocabulary; icons are Lucide only.

### The key is the interface (`ageSwatches()`, `openGeoMenu()`)

The key started as a picture of a key: eight swatches, none clickable, and a
`+n` whose only advice was to open another panel. Everything below is the
result of that being wrong in four different ways.

**A sample is a corner, not an inventory.** The key was *built from* the ~200-
point coverage grid, so a formation narrower than the grid spacing was painted
on the map and absent from the legend beside it — the reader sees an orange
belt with no orange swatch and cannot switch it off. Invariant 7 exactly. The
**set** now comes from an unfiltered `queryRenderedFeatures` over the viewport
(every feature actually rendered); the grid sample is demoted to what it can
honestly do — say *how much* of the view each covers, for order and tooltip. An
age with `hits === 0` is drawn but too thin to sample, and its tip says "a thin
unit in this view" rather than "0%". `geoOffView()` keys off `drawn`, not
`hits`.

**A hidden thing must stay visible.** Coverage cannot see a unit that is no
longer drawn, so a hidden period would vanish with the very gesture that hid
it, taking the only way back. Hidden ages are appended from `GeoMap.agesOff()`,
struck through, and carry **no** coverage claim.

**Nothing may empty the drape silently.** Hiding the last visible period, a
strength floor no unit meets, and a share link carrying either are all refused
with a toast. An empty drape is indistinguishable from "no geology here".

**Brackets: which colours answer which question.** With `cobalt + copper`
selected the key is five colours and the chip says two words, with nothing
joining them — and the interesting case, ground hosting *both*, was invisible.
Each selected commodity gets a bracket under the swatches it covers; an overlap
is two brackets over one swatch, which is the statement. `.ml-swatches` is a
**grid** so a bracket lines up without measuring anything, ages are ordered so a
bracket is normally one run, and a genuinely split bracket draws as two segments
rather than lying about contiguity. The bracket label is also the way out of
that *one* commodity — the chip's menu could only clear all of them.

**Size decides whether the ornament is drawn.** The FGDC hatch is legible on a
polygon and in a 13 px legend row. At 11×7 px it is not a pattern, it is dirt:
it darkens the colour unevenly, so two units of the same age read as two
different ages. Small swatches (menu rows) are **flat colour**; the ornament
lives in the key strip, the panel's legend rows and the map.

### The affinity matrix (`openGeoMenu()`)

The chip's menu was three surfaces holding one clause each of the same sentence
— a commodity list, a strength ladder, and the key strip outside the menu. It is
now one object: **rock across the top, commodity down the side, affinity in the
cell**, which is how this knowledge is written in every economic-geology text.

* a **row** = which ground hosts cobalt; a **column** = what this rock is
  prospective for (nothing in the app could answer that before); the **grid** =
  where two commodities share ground.
* the columns **are** the key strip's columns — same periods, same order, same
  swatch — so the menu is a legend for the map in front of the reader, not a
  catalogue of the dataset. No sheet in view ⇒ say so; a grid of empty cells
  reads as "nothing is prospective".
* a **cell** solos one commodity on one period, built out of the existing
  commodity + `agesOff` state, so the chip, brackets and share link stay true.
  No fourth kind of state.
* the grade is the cell's **ink** (●●● full / ●● / ● faint), so the floor reads
  as a threshold on the ink. An affinity below the floor is **hollow, not gone**:
  "you told me not to count this" ≠ "there is nothing here", and the empty cell
  already means the second.
* rows are ordered by what answers *this view*, not alphabetically — a menu
  sorted by the alphabet is a dictionary.

### Affinity strength (`shared.minWeight`, `?geomap_host_min=`)

The catalogue grades every affinity 1–3 (`legend.py`: 3 = classic host, 2 =
plausible, 1 = weak/derived). "gold" is 36 units across three sheets and **21 of
them are weight 1** — placer ground downstream of a lode, a quartzite inside the
belt. Drawn flat, the map says "gold is everywhere", which is the opposite of
the question. `GeoMap.setMinWeight()` is the floor; everything resolving a
commodity to units goes through `hostMap()`, so the floor cannot apply to the
map and not to the count describing it.

⚠️ **An empty isolation and no isolation are different states.** They were the
same value (`null`), so raising the floor to `classic` made CAR — which has no
weight-3 gold host — fall back to drawing *all 17* of its units: the filter that
matched nothing rendered as the whole sheet. `applyCommodities()` now keeps an
empty `Set`, `visibleCodes()` treats it as "this sheet has no answer" (draws
nothing), and emptying *every* sheet is refused in words. Invariant 1, in its
purest form.

### Every gesture narrowed; none widened (`.ml-state`)

The matrix was a trap. A cell picks one commodity on one period, the floor drops
grades, a column hides a period — three taps in, the reader is looking at two
units, and the only route back was a row buried in the list. The app's standing
rule (*a subset must announce itself*) needed one more clause: **a subset must
also be escapable from where it is announced.**

* the menu opens with `.ml-state`, a line saying what is drawn in words, which
  in the narrowed case carries "show all" as the primary action. Same object in
  both states, so it does not appear and disappear under the thumb.
* a **row** tap now clears an age narrowing (`GeoMap.clearAges()`): a row means
  "this commodity, on every ground it has", and the tooltip promised that.
* the strip's `all` button calls `geoAll()`, not `showAllAges()` — it used to
  clear only the periods while the map stayed commodity-filtered, which reads as
  a broken button rather than as one that did a third of its job. Its tooltip
  lists what it will clear.
* the chip names **both** filters (`gold hosts, 16 periods hidden`), counted from
  state, never from the canvas.

⚠️ **The matrix must wait for the map.** Its columns are the periods *drawn*,
measured off the rendered canvas, so a menu rebuilt in the same tick as the
filter change shows the columns of the map as it was one gesture ago — clearing a
one-period narrowing and still seeing one column reads as "the button did
nothing". `reopenGeoMenuWhenDrawn()` re-opens it on the next `idle`, one-shot,
preserving `scrollTop`, and only while the geology menu is still the open one.

The chip is also two targets now (`.ml-chip-main` + `.ml-chip-x`): its body
opens the matrix rather than switching the layer off, so "off" needs its own
target or the strip's one-meaning-per-target rule holds for two chips out of
three.

### Contact zones (not built)

The honest next question after "which rock" is "where do two of them **meet**" —
a granite/greenstone contact is the classic orogenic-gold setting, and it is a
property of the *boundary*, not of either unit. The polygons carry it already:
528 unit pairs share an edge on the Sudan sheet alone (~33 s in shapely). The
menu ships a `.refused` row that says so, because an absent row reads as "this
map cannot do that". It must be derived **in the sheet build**
(`scripts/geomaps/`) and served as an ordinary attribute — 500+ pairwise
boundary intersections in the browser on every pan would ship as a hang.

Wiring: `renderGeoMapPanel()` / `renderHistMapPanel()` / `switchBasemap()` all
call `MapLegend.refresh()`, and both refresh **before** their own early return
on a missing admin DOM node — the strip exists whether or not the admin panel
has ever been opened, so a share link that turns geology on paints the chip.

Mobile: the strip spans the stats grid (`grid-column: 1 / -1`) because it
describes the ground under every cell, not a fourth statistic; targets go to
32 px. In **landscape**, where the panel is hidden for vertical room, only the
strip survives — right-aligned and sized to itself, with its own backing on the
`.ml-row`, since an overlay that is on must stay visible and switchable there
too.

### FloatUI sheet stack (phones)

On a phone (≤640px wide, or ≤480px tall + coarse pointer — `isSheetMode()` in
`srv/static/floatui.js`) every persistent floating card becomes a bottom sheet
in a single stack: the AOI/park popup (id `popup`), the pinned map-tip card
(`maptip` — maptip.js registers/unregisters as it docks/unpins), and the
pinned-layers box (`pinned`). API: `FloatUI.registerSheet(id, {el, isCollapsed,
setCollapsed})`, `unregisterSheet`, `noteSheetToggle(id, collapsed)` from any
manual collapse handler so the user's choice sticks (`userCollapsed` beats
auto-expansion).

`layoutSheets()` runs greedy-by-recency: the most recently touched sheet is
expanded and sits nearest the thumb (bottom); older sheets expand only if
their remembered height still fits, else auto-collapse via the widget's own
`setCollapsed` (an `applyingLayout` flag keeps that from registering as a user
choice or persisting to localStorage — pinned passes `persist=false`). Each
sheet's `bottom` is set inline; the CSS (`globe.css` "FloatUI sheet stack")
only wins the horizontal fight: `transform: none !important` on
`.maplibregl-popup.fui-sheet` out-!importants MapLibre's inline anchor
transform so `_update` keeps running and the popup re-anchors cleanly when the
class comes off. Portrait sheets are full-width (8px gutters); landscape
anchors them bottom-left at `min(420px, 100vw-90px)` — clear of the toolbar
column (left) and the stats panel (right).

`sheetBottomPx()` measures the bottom chrome (`.map-toolbar`,
`#time-slider-container`) but only counts nodes whose **top** is in the lower
half of the screen — in landscape the toolbar is a full-height left column
whose bottom edge is low, and counting it pushed the whole stack above the
fold. The measured offset is also published as `--fui-bottom` so transient
menus (geology mixer `.ml-menu.ml-panel`) can anchor by CSS alone without
joining the stack.

Drag-to-detach is disabled while a widget wears `.fui-sheet` (the stack owns
position); tap-to-collapse still works and routes through `noteSheetToggle`.
A collapsed docked maptip folds to its first line (`.fui-collapsed` rules in
maptip.js's own CSS block). The stats panel is **not** a stack member — it has
its own mobile grid at the top.

