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
* **`MapTip.setBackdropGuard(fn)`** — stated once, in the `map.on('load')`
  block, not re-implemented per layer: a park polygon has its own popup and its
  own click handler, so every negative-priority tip stands down over one. The
  AOI tip used to do this itself, which is exactly why the geology overlay,
  added later, did not — a tap on a park inside the AOI opened a geology card.
* **Geology went through `maplibregl.Popup`**, so its click never reached the
  shared arbitration at all. It is a MapTip registration now, and `remove(id)`
  unregisters it — otherwise a switched-off sheet keeps swallowing clicks.
  The raw-Popup path survives only as a fallback if `maptip.js` fails to load.
* Where a backdrop wins but hides another, **name the other in the same tip**
  rather than making it unreachable: the AOI tip carries a `Geology · <code>`
  line. One tip, both answers.
* `MapTip.refresh(layerId)` re-renders whatever is on screen when an async
  detail lands (AOI coverage, a LOD row's `/api/feature-detail`), instead of
  leaving "coverage…" frozen on a card that no longer gets a mousemove. It
  updates **the whole tab stack**, not just the visible tab — otherwise
  switching away and back shows the placeholder the detail just replaced.

---
