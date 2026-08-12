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
* **A chip is the state and the way out.** Tapping a chip switches that one
  thing off; the opener beside it configures. Two targets, one meaning each —
  the alternative (a chip that opens a menu containing the same chip) is the
  ambiguity this replaced.
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
