# Raster & vector map overlays (historical maps, geology)

_Split out of AGENTS.md. Read when working on this area._

## Historical map overlay (Sudan Survey 1:250,000)

**187 sheet cells** (editions 1915-1968, median 1933) of the Anglo-Egyptian
Sudan 1:250k series across all 18 1:1M blocks, georeferenced to their own
printed 15-arcmin graticule and mosaicked into `data/histmaps/sudan250k.mbtiles`
(3.6 GB, z0-14, 574k tiles, **gitignored derived output** — rebuild with
`scripts/histmaps/mosaic.sh`, don't commit). The 49 cells covering
`XSA_Study_Area` are complete.

⚠️ **The first build (2026-08-06) covered the wrong half of the country.**
`captions.txt` had
been truncated at **264 of 770 lines** by an interrupted `curl`, which dropped
every South Sudan block — exactly the AOI the overlay exists for. Nothing
failed: 76/76 sheets georeferenced to 0.0 arcsec, QA was clean, the mosaic
built, and the README then *documented the missing blocks as absent from the
archive*. **A short manifest reads as a small collection, not as a broken
download.** `catalogue()` now asserts 770 lines and cross-checks the LOC item's
own `segment_count`; `mosaic.sh` invalidates a block's cached tiles when its
sheet list changes. Same shape as the AOI "no-op that reads as an answer" rule,
applied to an input. Full post-mortem: `scripts/histmaps/README.md`.

The same shape bit twice more downstream, both fixed: `qa.json` was rewritten
wholesale per run (so a final 8-sheet retry pass replaced the record for all
187 — it is merged by id now), and a hardcoded `"76 sheets"` in the MBTiles
description survived a rebuild that more than doubled coverage (`refresh_meta.py`
derives it from `data/histmaps/geo/`). **Never type a count that describes a
variable input.**

8 cells fail to register and that is correct, not a gap: all are near-blank
Libyan/Nubian Desert sheets (blocks 43/44/45, median ink 0.021 vs 0.085 corpus)
with too little printed ink for the graticule detector to fit a ladder. It
declines rather than guessing — a wrong warp on a blank sheet would be invisible
and permanent.

Rebuild is a throttled resumable systemd oneshot
(`scripts/histmaps/histmap-rebuild.service` → `rebuild_night.sh`, ~22 h for 128
sheets + 4 h mosaic), and `select.py --priority-bbox` orders the AOI's sheets
first so an interrupted run still covers what matters. JP2 fetches are bounded
on *throughput* (`--speed-limit`/`--max-time`), because `--retry` does not cover
a stall: one curl sat on an idle connection for 22 min at load 0.00.

| Piece | Where |
|---|---|
| Fetch + georeference | `scripts/histmaps/sudan250k.py`, `select.py`, `runall.sh` |
| Mosaic to MBTiles | `scripts/histmaps/mosaic.sh` (~4 h, resumable); `refresh_meta.py` for metadata only |
| Overnight rebuild | `scripts/histmaps/rebuild_night.sh` + `histmap-rebuild.service` |
| Serving | `srv/histmap.go` — `GET /api/histmap`, `/api/histmap/sudan250k/{z}/{x}/{y}.png`, `/download` |
| UI | admin panel -> Map Settings -> Historical Maps (`HistMap` in globe.html) |
| Share link | `?histmap=sudan250k` |

**White ink is a paint property, not a second tileset.** The archive holds one
flat near-black (26,22,18) on transparent paper, so the layer sets
`raster-brightness-min: 1` to lift RGB to white while leaving alpha alone. The
**download stays black on purpose** — offline viewers (Locus, OsmAnd, QGIS)
default to light backgrounds where white ink is invisible. Never "fix" this by
generating a whitened archive; there would then be two copies to keep in sync.

Layer order is load-bearing: inserted **before the first non-raster layer**, so
above the basemap and below park/AOI outlines, trajectories and pins.
`switchBasemap()` therefore **excludes** `histmap-lyr`/`histmap-src` from its
generic custom-layer capture and calls `HistMap.reattach()` (which re-adds on
`idle`) instead — replaying the captured spec appends the scan on top of
everything, and re-adding during `styledata` is silently dropped because the
`before` id has not landed yet.

Tile misses return **204, not 404**: the series covers 18 of 22 1:1M blocks, so
most of the bounding box legitimately has no sheet.

**Tiles are `immutable, max-age=7d`, so a rebuild must change their URLs.**
`GET /api/histmap` returns `rev` (mtime+size of the MBTiles) and bakes it into
the `tiles` template as `?v=`. Without it the truncated 76-sheet build kept
rendering after the 187-sheet rebuild, and only at the zoom levels the browser
had cached — which reads as "gaps at some zoom levels", not as a stale cache.
The client must use `meta.tiles`, never a hand-written tile path.

Full detail, including why the mosaic is built per-block and why `tile_row` is
TMS: `scripts/histmaps/README.md`.

---

## Geology overlays (Sudan GRAS 2004, CAR BRGM 1964, Tanzania GST/GMIS 2015)

Three geological sheets as **vector** overlays: 46 classes for Sudan, 17 for
CAR, 41 for Tanzania, served as vector tiles and toggled per class or per
commodity. Full detail: `docs/GEOLOGY.md`, whose **"Adding a sheet"** section is
the whole contract for a fourth — read that before anything else here. Files:
`scripts/geomaps/{sheets,gridfit,georef,legend,vectorize}.py` + `tiles.sh`,
`srv/geomap.go`, `srv/geomap_std.go`, `srv/static/geomap.js`,
`renderGeoMapPanel()` in globe.html.
UI: admin ▸ Map Settings ▸ Geology. Share link `?geomap=car`.

Sudan and CAR are **scans we vectorized**; Tanzania comes from the survey's own
WFS (`scripts/geomaps/gmis_tanzania.py`) and so has no georeferencing, no
classifier, no hold-out and no merged classes. Both paths end at the same two
files (`<sheet>_units.geojson` gitignored, `<sheet>_classes.json` committed) and
nothing downstream knows which one produced them. A sheet id goes in
`geoMapSheets` (`srv/geomap.go`) and the default list in `tiles.sh`; everything
else — catalogue, GeoPackage commodity columns, cache stamp, panel counts — is
derived. The vocabulary audit
(`TestShippedCataloguesHaveNoUnmappedVocabulary`) walks `geoMapSheets`, so a new
sheet's wording cannot arrive unaudited: it must reach 0 unmapped ages, 0
unmapped lithologies and 0 answered-by-rule-order before it ships.

Not the `?histmap=` raster overlay (1:250k topographic scans) — different data,
different path. Vector because the units are *data*: the client recolours them,
hides one, and isolates "everything that can host gold". A raster drape would
need one tileset per combination of 46 classes.

* ⚠️ **A hold-out measured on a whole legend swatch lies about the map body.**
  It reported 0.95–1.00 while CAR's Mouka-Ouadda plateau — an area the size of
  Belgium — rendered **white**. The classifier decides from a 17–33 px window,
  where inks 0.13 apart in Bhattacharyya distance are noise; two such classes
  do not *swap*, they **cancel**, both lose the `--min-margin` test, and the
  formation vanishes instead of being mislabelled. Judge every change with
  `window_holdout()` and read the **claim rate**, not the accuracy: 34% of CAR
  inside its own cutline was unclaimed at accuracy 1.000. Merging what the
  window genuinely cannot separate took it to 8.7% (Sudan 16% → 9.0%).
* **`<sheet>_classes.json` is the catalogue the server reads, not
  `legend_*.json`.** The legend is the sheet's *printed* unit list; the tiles
  carry the *class* list, which merges inseparable units and drops ones that
  never occur. Serving the legend offers toggles for classes that cannot be
  drawn. Both are committed; `_units.geojson` and `*.mbtiles` are not.
* **A merged class is labelled with every member code** (`GC2/GO`), never a
  pick — the sheet does not say which one a patch is. Its affinities are the
  **union** at the highest member weight, each `why` prefixed with the member
  code so the union is not a quiet upgrade.
* **Commodity affinity is an inference over lithology, never an occurrence
  dataset**, and every surface that shows it says so. Same line as the mining verdict (`docs/agents/mining.md`): inference from context ships, fabricated evidence does not.
  Keyed by `(sheet, code)` — `S` is Silurian sandstone on Sudan and a
  gold-bearing schist belt on CAR.
* **Paper competes as a synthetic class and is then discarded**, which is how
  `paper_like()` units are resolved by exclusion. Both failure directions land
  on "unclaimed", never on a wrong formation.
* Tiles: 204 on miss, `immutable` + `?v=<rev>` (a rebuild can change the class
  list, so stale tiles would carry names the catalogue no longer has), every
  class kept at every zoom — detail is dropped as *geometry*, never as a
  missing unit.
* `switchBasemap()` excludes `geomap-*` and calls `GeoMap.reattach()`, which
  re-adds on `idle` — the same `before`-id trap as `HistMap`.
* **`add(id)` retries; it does not give up.** It used to `return` when
  `map.isStyleLoaded()` was false, which turns "try again in a moment" into
  "never". With two sheets on at once (`?geomap=sudan,car`) both queued on the
  same `idle`, the first one's `addSource`/`addLayer` put the style back into a
  loading state, and the second silently evaporated. The failure is invisible
  by construction — there IS geology on screen, over the other country — and it
  reads as a *click* bug: over the missing half a tap falls through to whatever
  is underneath. It now queues itself on the next `idle` (`pendingAdd`, cleared
  by `remove()` so a sheet switched off is not resurrected). Cross-cutting
  invariant 1: a unit that produces nothing for a valid input must report
  unfinished, not success.
* **The unit card is an ordinary hover tip at `priority: -30`** — the deepest
  backdrop on the map: a park (-10), an AOI (-20) and every pinned feature (0)
  answer before it does. It was `clickOnly` on the theory that a country-sized
  drape has no "off it" to move to; that was over-thought (MapTip shows only ONE
  tip, so geology simply loses to anything more specific) and it made the rock
  map heavier to use than a fire. It also carries `peers: false`: a drape's 17
  units are a legend, not a pile-up to "zoom in and separate".

  Until 2026-08-12 the -30 was academic inside a protected area: a park
  polygon silenced every backdrop outright (`setBackdropGuard`), so geology was
  unreachable exactly where this map is used. The park is a ranked layer now —
  see `docs/agents/map-ui.md`.

### One layer, one legend, and the legend is the industry's

The first two sheets were printed 40 years apart by different surveys and
digitised in *their own* inks (the third arrives with the GST's own GeoServer
inks, which is a third colour language, not a solution). Presented as separate
cards (a toggle, an opacity slider and a class list each) the user had to
reconcile them for one question — and at the CAR/Sudan border the same rock
changed colour. Several of the sheets' inks are also a desaturated blue-grey
that on a dark basemap is indistinguishable from the waterbody layer, which is
how a geology drape got read as **water**.

Fixed 2026-08-12 (`srv/geomap_std.go`, `srv/static/geopatterns.js`):

* **Colour = age**, ICS/CGMW International Chronostratigraphic Chart (v2023).
  **Pattern = lithology**, FGDC-STD-013-2006 §37 (dots for sand, bricks for
  carbonate, plus-signs for intrusives, wavy dashes for schist/gneiss…).
  Both are *derived server-side from the sheet's own group/name strings* and
  ride in `/api/geomap`'s catalogue, so **a legend change never invalidates a
  tile** — the tiles carry `code`, the catalogue says what a code means.
* **The ornament is load-bearing, not decoration.** Nothing else on the map is
  hatched, so a hatched polygon is always the rock map — at any opacity, on any
  basemap, and for a colour-blind reader. It also survives being turned down,
  which a hue does not: `GeoPatterns.tile()` weights the ink far above the
  background for exactly that reason. Default opacity 0.42.
* **The printed ink is never discarded.** `color` stays on every class,
  `setColorMode('ink')` draws the sheet as printed, and the GeoPackage keeps
  `ink_color` beside `ics_color` plus an `as_printed` named style.
* **The age scan is first-match and order-sensitive**: `"precambrien"` contains
  `"cambrien"`, so the Cambrian rules must come *after* the Precambrian ones or
  every CAR basement unit dates as Cambrian. Pinned by `TestGeoAgeOf`.
  A group naming several ages is `age_mixed` and takes the **oldest** — never
  a pick, same rule as a merged code. A hyphenated **span** is different: one
  unit straddling a boundary, so it gets a curated rule above *both* endpoints,
  and an uncurated one is reported as `age_ambiguous` rather than answered by
  rule order.
* **A sheet's own lithology column, where it has one, is read LAST** — after
  name and group (`geoLithResolveHint`). Such a column lists every constituent
  in no order, so a first-match scan over it calls a syenite-gabbro ring
  complex `ultramafic` off the word "pyroxenite". The name is the survey's own
  summary; the column only rescues names that are pure geography.
* **The sheet is demoted to provenance**: one Geology toggle, one legend, one
  opacity; the per-sheet API stays (tiles, downloads, share links are per
  sheet) but is no longer the user's unit of thought. A commodity chip acts on
  **every** sheet — rock does not stop at a border.
* **The QGIS export uses the same legend** (`styleGeoUnits`), because someone
  who filters "gold" on the map and opens the download must be looking at the
  same picture. Ornament is a real QGIS `LinePatternFill`/`PointPatternFill`,
  not a raster texture. Two traps: `use_custom_dash` belongs on the *line*
  layer (on the fill it is silently ignored and all nine ornaments come out
  solid), and a sub-symbol name `@parent@N` must be **unique** within its
  parent or QGIS drops one and a cross-hatch renders as half of itself.
* **Contacts are drawn in a darkened ink, never the unit's own colour.** These
  rings are traced off a scan, so 46 classes outlined at full saturation is a
  net of bright magenta over a whole country.

**Both downloads ship, at different granularity, and that is not an
inconsistency**: `MBTiles` is per sheet (a picture of one scan, with its own
zoom range and envelope — an offline viewer loads the one covering where it is
going), `GeoPackage` is **one file for every sheet** (`GET
/api/geomap/geopackage` → `geology.gpkg`, one `geology_units` layer, the scan as
a `sheet` column). The data has no reason to be cut along a border; the picture
does. `/api/geomap/{sheet}/geopackage` **308s** to it — those URLs are in
shipped links, and a 404 reads as "the export was removed".
A unit is `(sheet, code)`, carried as `key`, and the QGIS renderer categorises
on that: `code` alone collides (`S` = Silurian sandstone on Sudan, gold-bearing
schist on CAR) and would date half a country from the other's legend. The
legend is **one category per class**, deduplicated — a vector sheet has many
polygons per class (Tanzania: 596 rows, 41 classes) and a per-row legend lists
the same symbol 89 times.
Cache staleness is a **stamp of the input set**, not one mtime — adding a sheet
whose units file is older than the package would otherwise ship a country short.
A link to the panel itself is `?panel=admin&admin_tab=map-settings&map_sheet=car`.
`srv/geomap_gpkg.go`; details in `docs/GEOLOGY.md`.

### Contacts: the second table, and the panel that holds both

The affinity model says *which rock*; a contact says *where two of them meet*,
which is a property of the **boundary** — so it can never be an eleventh
commodity row. It is the same table transposed onto itself: **rock down, rock
across, cell = what that junction can host** (`junctionTableHTML()` in
`maplegend.js`, `GeoMap.junctions()`).

* **Upper triangle only.** A junction is unordered; a full square prints every
  pair twice. The mirrored half is blank space, not a repeated cell.
* **Only the lithologies these sheets actually join** (54 pairs of a possible
  55, from the junction index) — a cell for a junction no sheet has would claim
  it exists and is barren.
* **One scale, one ink, both modes.** ●●● classic / ●● likely / ● weak, hollow
  = graded but below the floor, faint dot = they meet and the model says
  nothing. The strength ladder sits **above both tabs**: it is the legend for
  the ink in both, and one piece of state may not have two controls.
* **No control is repeated in the junction view.** The commodity is picked in
  the Rocks table, where it *is* a row; the junction head only *names* what is
  carried over ("graded for gold, likely"). Both axes there are rock, so that
  view owes the reader a sentence, not another switch.
* **The lines follow the table.** There is no "draw contact lines" checkbox —
  opening the Junctions tab draws them (`MapLegend.mxMode`), and `autoContacts`
  remembers whether *we* switched them on, so only our own doing is undone.
  Picking a junction is a decision about the map and survives the tab switch.
* **Every count is what the map paints**, from the same filters the paint uses
  (`drawnUnitCount`, `drawnContactCount`, both gated on the sheet being **on**,
  not merely installed). The counts are `not in view` — absent, not zero — when
  no sheet reaches the viewport.
* Share link: `?geomap_junction=intrusive|volcanic,metamorphic` (a
  comma-separated list; each entry is a lithology pair or a single lithology).
  It travels as **lithology**, a server-owned vocabulary, so a re-tile cannot
  strand it; the entries this build still has are kept and the rest refused
  with a toast, rather than filtering the layer to nothing.

**PICKS ADD; THEY DO NOT REPLACE.** Both tables used to be radios: a matrix
cell replaced the commodity selection and soloed its period, a junction cell
replaced the previous junction. So every tap destroyed the previous answer, and
"the cobalt Archaean *and* the copper Palaeoproterozoic" — the obvious use of a
grid — could not be said at all. Worse, the matrix's columns are the periods
actually **drawn**, so soloing one collapsed the table to a single column and
took the cells the reader would pick next off the screen with it: *a surface
that offers choices must not be narrowed by the choice it just took.*

State is `shared.picks` (`"commodity|age"`) and `shared.contactPairs`, both
Sets; `applyCommodities()` unions the rows' host sets with the picks'
(row ∪ cells), so there is still exactly one isolation per sheet. Consequences
that are easy to get wrong:

* the table's columns come from `querySourceFeatures` (`columnEntries()` in
  maplegend.js), i.e. what the loaded tiles **have** here, not what is painted;
  a period the picks exclude stays as a dimmed column, because it is the next
  choice. The key strip stays keyed on what is *painted* — it is a legend for
  the picture, not a menu.
* a **row supersedes its own cells** (`GeoMap.clearPicksFor`), or the map draws
  the whole row while the table still lights two cells as the narrowing.
* `showEverything()` clears both sets; `anyFiltered()` counts both.
* a pick beats a hidden period (`geoCell` un-hides it) — the cell says "draw
  this ground" and the age filter says "draw none of it", and the tap must show
  what it promised.
* `geomap_cells=diamond|tertiary,copper|paleoproterozoic` in the share link,
  and `geomap_only` is then **omitted**: the picks already describe that
  isolation, and a frozen code list beside them would win on a build where a
  unit was merged.

**A TABLE MUST NOT MOVE UNDER THE GESTURE IT IS TAKING.** Both tables order
themselves by how strongly *this view* answers the current question — columns
by coverage, rows and the junction axis by grade for the selected commodity —
and every pick changes that. So picking three cells along the gold row
re-sorted the grid between taps and the third tap landed on a cell that had
swapped places: the app choosing for the reader. The order is now **frozen for
as long as the reader is working** (`mxFrozen`, keyed on a coarse view stamp:
sheets on + zoom/centre to 1 dp) and only the cells' *state* is live. One frozen
record covers the rock table and the junction table (`mxOrder`, `jxOrder`) —
they are two views of one object, so a tab switch must not be the thing that
reshuffles.

**Settling is feedback, so it is watched, not discovered.** The freeze lifts
when the reader **moves on** — the other tab, the map (`movestart`/`click`), a
pointerdown anywhere outside the panel, or a 4 s backstop — and the rebuild
then plays as a **FLIP**: `mxRects()` measures every `data-mx` element
(`cell:gold|archean`, `row:`, `col:`, `jcell:`, `jrow:`, `jcol:`) before the
rebuild, `mxFlip()` inverts and releases them in one 0.42 s transform
transition (`.ml-mx-moving`; new elements fade with `.ml-mx-in`;
`prefers-reduced-motion` skips it). Transforms only, so nothing fights the
grid. Without this the panel — which is re-rendered from scratch on every
gesture — simply *blinks* into a different grid, and whether anything moved is
something the reader has to reconstruct from memory.

**The key strip may not set the panel's width.** Ten swatches at 21 px is
250 px of intrinsic width in a shrink-to-fit stats panel, so turning geology on
widened the panel over the map and moved every number above it. `.ml-swatches`
is `width:0; min-width:100%`, and `fitCols()` derives the column count from the
width that leaves (measuring one swatch rather than hardcoding 21/26 px, and
reserving for "+n"/"all" only when they will be drawn). The overflow opens as
its own wrapping grid below (`.ml-sw-rest`), never as more columns in the
braced one. A clipped swatch is a period the reader can see half of and cannot
tap; "+3" is a truncation that says it is one.

**The geology menu is a panel, not a popover.** It is a legend, read while
panning and clicking, so a surface the next map click dismisses made the reader
choose between seeing the map and seeing the key to it. It wears the app's own
`.fui-bar` (grabber, settings, collapse, ×); position and collapsed state
persist in `localStorage` (`fui.geomenu`), because the panel rebuilds on every
gesture. Its width is **fixed** — it was `max-content`, so the two tables'
different sentence lengths resized it on every tab switch. `Hide geology` and
`Map settings` moved from the last two rows (below the disclaimer, where a
thumb lands) into the bar.

**`showToast(msg, type)` takes two arguments.** Every geology call site used a
stale 5-argument form and therefore *threw* instead of warning — including the
one guarding an out-of-date share link, which then took `restoreFromParams`
down with it and left the layer off. If a refusal is not visible, check the
signature before the logic.

**A cell in the matrix could not show "checked".** `.mode-mark` filled only
under `.mode-opt.on`, and the commodity rows are `.ml-mx-row.on` — so the row
went amber, the map changed, and the only control on the row said "off"
throughout.

### The panel: one switch, one legend, and everything else under Advanced

Fixed 2026-08-12, same direction as the one-layer change above but for the
*controls*. The card had **four** idioms for "this is a switch" — a green
`Show`/`Hide` text button (the shape of an *action*, for a *state*), amber pill
chips, a blue segmented control and a bare `<input type=checkbox>` — so the user
re-learned the control at every row. Now:

* One vocabulary. **A switch that is down is filled; up is the same shape in
  outline**, and the shape carries the arity: a pill is multi-select, a segment
  is one-of, the app's slider switch is a single on/off (`.geo-switch`,
  `.geo-row`, `.geo-chip`, `.geo-seg` in `globe.css`).
* **Amber fill means "this row is a filter and it is down"**, which only the
  rock-type rows are. A unit row's `aria-pressed` says whether it is *drawn* —
  the normal state for 63 of 63 — so styling that as selected lit the whole list
  and made "everything is on" look like "everything is picked". `.geo-row.filter`
  gates the highlight; a unit that is off is dimmed instead. Age rows are
  `.static`: nothing filters by period, so they must not look clickable.
* **Advanced** (`<details class="geo-adv">`) holds colour mode, the ornament
  switch and the 63-row unit list — answers to "how should it be drawn", not to
  "what rock is under here". Its open state is a **setting**, so it rides in the
  share link (`geomap_adv=1`) and is honoured even with the layer off.
* **Opacity adapts.** 0.52 over the dark basemap, **0.72 over satellite**; the
  same drape tuned for one is nearly invisible on the other, which is how a
  working layer gets reported as "not showing anything". `autoOpacity()` is
  re-evaluated by `GeoMap.basemapChanged()` (called from `switchBasemap()`) *and*
  when the layer is switched on, since going satellite-then-on would otherwise
  draw at the dark value. **Dragging the slider leaves auto** — a hand-set value
  is never recomputed behind the user's back — and `geomap_opacity` is therefore
  *absent* in auto: baking the computed number into a link would break the layer
  for whoever opens it on the other basemap.

* **`w_gold IS NOT NULL` is the point.** Commodities as one comma-joined string
  would make the export's headline question a `LIKE` over text; one INTEGER
  column per commodity makes it an exact filter and lets QGIS graduate on the
  weight. The column set is derived per build, never fixed — a re-vectorized
  sheet can merge classes and change the union of affinities.
* **NULL, not 0, where a unit hosts nothing.** 0 reads as "measured, none" and
  matches `>= 0`.
* Built on first request and cached beside `<sheet>_units.geojson`, keyed on
  mtime (`>=`, or a build finishing inside its input's timestamp tick rebuilds
  forever). No job queue: it is a static file per sheet and takes ~2 s, unlike
  the per-area export which is minutes over a live database.
* The button only appears when `_units.geojson` is present, and the size only
  once a build exists — a link whose only outcome is a 404, or a "(12 MB)"
  nobody measured, are both worse than nothing.
* Verified by rendering it in QGIS (`docs/GEOLOGY.md` § GeoPackage),
  not just by `ogrinfo`.

---
