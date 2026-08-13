# Handover: the geology mixer, its downloads, and the anchors

_Open brief, updated 2026-08-13 (second pass). Delete when the work below is done._

Task as given: **(1)** make sure the geology mixer works correctly — "I want to
see where gold is likely, it shows me that"; **(2)** a GeoPackage download of
the **current chooser** (filtered/hidden/as-drawn), probably a download arrow in
the mixer; **(3)** include our **reference mining sites** in that download —
*coordinate, year, resource, original id, source url only*. Follow-up: **the way
contacts are selected matters most, junctions look the most promising** (CAR
gold junctions 2.18–2.53×, gold *units* 0.63×). Second follow-up: **make it
fast — this is the core feature of the app.**

Read `docs/agents/overlays.md` § geology first. Nothing below overrides an
invariant there.

---

## The performance pass (2026-08-13, committed, VERIFIED BY CLICKING)

The gesture measured throughout is the one the app exists for:
`?geomap=sudan,car,tanzania` → open the panel → click **Junctions**. Before:
**225 ms of blocked main thread and ~4 s before the map answered.** After:
**~95 ms and the lines are up on the next frames.** Four separate causes, all
measured rather than guessed, none of them the one the previous handover
blamed.

**1. "counting lines…" was not a paint-timing bug, it was a stale label.**
The previous pass diagnosed it as a layer measured before it painted and built
`contactsPending` + `reMeasure()` for it. That apparatus is right and it did not
fix the symptom: the bar sat at `counting lines…` *indefinitely* while
`queryRenderedFeatures` returned 439 from the console. Cause:
`contactHits`/`contactsPending` are module state, the bar is HTML built once by
`headRow()`, and `watchMap`'s idle handler calls `render()` → `measureCoverage()`
— which **cleared the pending flag**, so the very next line
(`if (contactsPending) reMeasure(2)`) found nothing to do. The state healed; the
pixels never did. This is also what "closing and reopening the panel fixed it"
really meant, and it is why that read as a timing problem.
Fix: `syncBar()` restates the bar in place (one `innerHTML`, one `title`), called
unconditionally on every idle and from `reMeasure`. **Not** `rebuildGeoMenuNow()`
— that tears down and reopens the panel, losing scroll, focus and hover for two
digits, which is why it would have been "fixed" rarely.

**2. 343 KB of HTML built twice per click, into a panel that was `display:none`.**
`renderGeoMapPanel()` (globe.html) writes the Map Settings geology card. ~24 call
sites end with it, all correctly — they all change what the card would say — but
the card lives behind an unopened admin tab (`offsetParent === null`, 0×0). This
was the single largest cost of the click, larger than every
`queryRenderedFeatures` the legend takes combined, and none of it reached a pixel.
Fix: dirty flag + `geoMapPanelFlush()` on tab open (`requestAnimationFrame`, since
`.active` is set before layout). **The flag is the correctness condition, not an
optimisation**: skipping the build without recording it would leave a stale card.

**3. `measureCoverage()` ran 3–4× per gesture on an unchanged canvas** (~120 ms
each: two full `queryRenderedFeatures` + a ~200-point grid). Memoised on what it
is a measurement *of* — viewport, layer set, **layer filters**, and a `paintNonce`
bumped on `idle`/`sourcedata`/`styledata`. The filters must be in the key: a
commodity chip changes the picture without moving the map. `measureCoverage(true)`
forces a read; `reMeasure` and the share-link opener are the only callers that
may, because "has it painted yet" is exactly the difference the key cannot see.

**4. THE BIG ONE — 4.1 s of bucket building, and it was the layer's own absence.**
Contacts used to be *removed* when switched off, reasoned as "an empty layer still
costs a filter per tile per frame". True, and the smaller of two costs that were
never compared. `contacts` is a source-layer MapLibre has no reason to parse while
no style layer references it, so **adding** the layer is not "start drawing", it is
"re-parse every loaded tile". Measured at z3, three sheets: **4.1 s on the main
thread before one line appeared.** Measured and ruled out as workarounds — it is
the same 4.1 s when the layer is added with `visibility: none`, added at
`line-opacity: 0`, or added with a filter matching nothing. None of them start the
parse earlier.
Fix: the layer is created **when the sheet is added** (map otherwise idle) and
never removed; `shared.contacts` is expressed as `line-opacity` 0.9/0 in
`paintContacts()`. Toggling is now a paint property: **~90 ms both ways.**
  * `line-opacity: 0` is not a second source of truth. `GeoMap.contactsOn()` is
    the flag; maplegend's `geoContactLayers()` consults it before counting.
  * The **tip follows the switch, not the layer** — `syncContactLayer` unregisters
    it when off, or an invisible line answers a hover.
  * A sheet with no contacts gets no layer at all (nothing to warm).
  * Do **not** "simplify" this back to a filter: a match-nothing filter keeps the
    bucket only until MapLibre drops it — measured 6.0 s to come back.

**5. The strip repainted itself byte-identically 4× per click.** `innerHTML` is
not free even when the string is equal: it destroys and recreates the buttons,
dropping focus, cancelling `:hover` and restarting the swatch stagger. Now
written only when it differs — safe because the string is a pure function of what
the strip shows.

Net on the headline gesture: **730 KB → 21 KB of HTML, 225 ms → ~95 ms blocked,
~4 s → next-frame lines, bar correct immediately (`104 units · 452 lines`).**

### The rule these five share

Every one was a surface doing work **proportional to something other than what
changed**: a card nobody could see, a measurement nobody had invalidated, a
repaint of an identical string, a parse triggered by a layer's arrival rather
than by its data. Before optimising anything here, **count the writes and time
the click** (`Object.defineProperty(Element.prototype,'innerHTML',…)` and a
`queryRenderedFeatures` wrapper were enough to find all five) — three of the five
would have been missed by reasoning, and the previous pass's careful
`contactsPending` machinery was built for a bug that was somewhere else entirely.

---

## Done and committed (earlier pass)

1. `scripts/mining_anchors.py` → `data/geology_truth/mining_anchors.geojson`
   (3,687 anchors, nine lists, five fields, `terms` on every row, ACLED named
   in `withheld`). The file's own header holds the judgement calls.
2. `srv/geomap_anchors.go` — loader + `geoAnchorSummary()`.
3. **The filtered export.** `buildGeoMapGeoPackageSel` applies `sel.unitSet()`
   in the sheet loop — *before* the commodity columns are derived, so a
   filtered file's `w_*` columns describe the file. It fails when the selection
   matches 0 units, and names only the sheets that actually contributed.
4. **`srv/geomap_gpkg_layers.go`** — `geology_contacts` (MULTILINESTRING, graded
   from the lithology pair, styled in the map's own amber ramp) and
   `mining_anchors` (POINT, whole, never filtered by the reader's commodity).
   Both in the embedded QGIS project.
5. **Routing.** `POST /api/geomap/geopackage` takes the selection JSON, builds to
   a temp file, serves it once as `private, no-store`, never touches
   `geology.gpkg` or its stamp. 409 (not 500) on a stale selection.
6. **The cache stamp covers units, contacts AND anchors.**
7. **Anchors in the catalogue.** `/api/geomap` carries `anchors` + `geopackage_view`.
8. **The download arrow** in the panel bar. *This view* (fetch+blob, a POST) and
   *Everything* (plain `<a download>`). `GeoMap.selection()` resolves the view
   from the same `visibleCodes()`/`visiblePairs()` the paint uses.

Verified end to end (server side): whole catalogue = 659 units / 882 contacts /
3,687 anchors; a CAR gold + classic-junction view = 7 units / 40 contacts / 3,687
anchors, named `geology-gold-hosts-on-car-classic-junctions.gpkg`.

---

## Not done — pick up here

1. **The download menu itself has still not been clicked through.** The panel bar,
   the Junctions toggle and the counts have now been click-tested and are correct
   (screenshotted). The **download arrow** was reached and opened but the probe
   errored before reading the menu, so: open it, take *This view* and *Everything*,
   confirm the filenames and that the view file really is the filtered one — then
   open one in QGIS. `scripts/geomaps/render_gpkg.py` draws a file through its own
   embedded project; three of nine unit ornaments were wrong in ways no byte-level
   test could see, and **the contact style has had no such pass.**
2. **No tests.** `srv/geomap_gpkg_test.go` has the shape to copy (`writeTestSheet`,
   `useTestSheets`). Three worth pinning: a selection matching nothing is an ERROR,
   not an empty layer; the anchors are NOT filtered by the selection (a file where
   every anchor agrees with the layer is a picture of our own filter); the stamp
   notices a rewritten contacts file.
3. **`docs/agents/overlays.md` says nothing** about the two new layers, the POST
   route, the `contactsPending` rule, or **any of the performance pass above**
   (the contact layer's residency in particular is a standing invariant now:
   *do not remove the contact layer to switch it off*). Write it there, not in
   AGENTS.md.
4. **The view export is not reachable from admin ▸ Map Settings**, which still
   offers only the whole catalogue. Probably right — a view only exists while the
   panel is open — but say so in the doc rather than leaving it as an omission
   somebody re-adds as a third path.
5. **Only measured at z3 with three sheets.** The zoomed-in case (z8–10, one
   sheet, far more contact features in view) has not been timed, and
   `measureCoverage`'s grid is viewport-sized. Time it before assuming it is fine.

### Judgement calls to preserve

* **Do not filter the anchors to the reader's commodity.** Ship all of them;
  `resource` is a column, so the reader narrows it and knows *they* did.
* **A filtered export must announce itself** — it does, in the layer description
  and the QGIS project title. Two files with one name and different contents is
  the truncation trap.
* **Do not remove the contact layer to hide it** (see 4 above) and do not infer
  the on/off state from the layer's presence — `contactsOn()` is the flag.
* **The mixer itself is correct** as far as tested: gold row → 36 units, floor
  `any/likely/classic` → 36/15/3, junction cells and headers ADD,
  `jcell:intrusive|volcanic` → 10 lines, `+ jcell:metamorphic|ultramafic` → 33,
  `+ jrow:intrusive` → 97. The gold row correctly shows `?0.06×` violet
  (contested) and the junction head `0.00×` — the *lowest* lift, never the
  flattering one. Don't "fix" that into a single number.
