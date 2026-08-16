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
| Traced lines + symbols (vector linework off the sheets) | `scripts/histmaps/trace_lines.py` (vision-LLM per 2048-px tile → `refine` snaps vertices to sheet ink, `support<0.35` = hallucination → `dedupe` → `stitch` cross-sheet, conservative: ambiguity breaks the chain → `catsym` classifies symbol crops against the sheets' own REFERENCE legends (taxonomy: water/settlement/peak/trig_point/tree/enclosure/grave/fort/church/station/ruin/landmark/unknown/junk; ~35% junk = over-capture, kept in DB, excluded from exports) → `link` names symbols from nearest OCR label ≤600 m and unnamed lines by vertex vote ≥2). `run` auto-chains all of it plus `export_labels.sh` on completion. Ledger `line_tiles`, resumable, tmux `histlines`; 12 workers ≈ 6.4 s/tile, full run ≈ 9 h. Served by `srv/histmap_lines.go` — `GET /api/histmap/sudan250k/lines?bbox=` or `?q=name`, and `/around?lon=&lat=&radius_km=` (labels + surveyor notes + clipped lines + symbols + sheet survey year in one call; line geometry is clipped to the window — a 2,000 km river must not answer a 10 km question). `sheets` table carries LOC survey years (1915–68) → `year_min`/`year_max` per stitched line. |
| Vector exports | `scripts/histmaps/export_labels.sh` (auto-run post-trace) → `sudan250k_labels.gpkg` (3 layers + embedded QGIS `layer_styles`: lines by kind, symbols by category, buffered label text) + gzipped GeoJSONs; row counts verified against source. `make_gpkg_styles.py` writes the QML. |
| OCR'd labels (query the map by coordinate) | `scripts/histmaps/ocr_labels.py` (vision-LLM pipeline, resumable, tmux `histocr`); served by `srv/histmap_labels.go` — `GET /api/histmap/sudan250k/labels?lon=&lat=&radius_km=` or `?bbox=W,S,E,N`, plus `q=` (FTS5 prefix match), `kind=`, `category=` and `note_topic=` filters (notes sub-classified travel/water_supply/habitation/hazard/antiquity/grazing_game/survey/infrastructure/fragment by `categorize_labels.py notes`). **Partial while the run is in flight** — responses carry `complete` + `progress`, never pretend a missing label means the map is silent there. DB: `data/histmaps/labels.sqlite3` (gitignored derived output; `labels` raw, `labels_dedup` after `ocr_labels.py dedupe`, `labels_fts` trigger-maintained). Model: `fireworks/muse-glimmer-30b` — bake-off winner (gpt-5.6-luna hallucinated modern names); do not swap without re-running the comparison in `ocr_labels.py`'s docstring. |
| Label categories + vector downloads | `scripts/histmaps/categorize_labels.py` re-classified the 28k-row `kind='other'` bucket by DISTINCT text (13.7k texts, `fireworks/gpt-oss-120b`, ledger table `text_categories`); `apply` writes `category` onto both label tables (`place\|water\|terrain\|vegetation\|route\|boundary\|note\|collar\|junk`; ~11% junk, ~2.5% collar — filterable, not deleted: they are evidence of what OCR saw). `scripts/histmaps/export_labels.sh` builds `sudan250k_labels.gpkg` + `.geojson.gz` + `.kmz` from `labels_dedup` (refuses on uncategorized rows; row counts checked source→csv→gpkg). Served Range-capable by `HandleAPIHistMapLabelsDownload`; advertised in `/api/histmap` `labels_downloads` with the **file's** count (94,300 dedup), not `labels_count` (98,055 raw) — invariant 7. UI: inline "Map labels (vector)" section in the admin card (globe.html `renderHistMapPanel`), NOT the MBTiles popup — the popup grew taller than fit under its button and flipped up over the card. After a re-OCR: `dedupe` → `categorize_labels.py run` + `apply` → `export_labels.sh`. |
| UI | admin panel -> Map Settings -> Historical Maps (`HistMap` in globe.html) |
| Share link | `?histmap=sudan250k` |

**White ink is a paint property, not a second tileset.** The archive holds one
flat near-black (26,22,18) on transparent paper, so the layer sets
`raster-brightness-min: 1` to lift RGB to white while leaving alpha alone. The
**canonical download stays black** — offline viewers (Locus, OsmAnd, QGIS)
default to light backgrounds where white ink is invisible. Since 2026-08-16 a
**derived** white variant exists for dark-basemap use:
`scripts/histmaps/whiten_mbtiles.py` → `sudan250k_white.mbtiles` (RGB→255,
alpha untouched; builds as `.building.mbtiles`, renames on verified
completion so a partial file never wears the advertised name). Served at
`/api/histmap/sudan250k/download/white`; `/api/histmap` advertises
`download_white` only while the file exists. Never edit ink into the black
archive — after a mosaic rebuild, re-run the whitener (~35 min, tmux).

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
* **The two newer layers have now been LOOKED AT** (2026-08-13), through the
  file's own embedded project, and `scripts/geomaps/render_gpkg.py` renders
  every layer rather than `layers[0]`: the units, the graded contacts and the
  anchors stacked in the project's order, plus a contact-grade legend pass and
  an anchor-symbol-over-real-unit-fill pass, plus two ~50 km detail views. Two
  things the byte-level Go test could not see, both fixed in the writer:
  * **A `<maplayer>` with no `<resourceMetadata>` BLANKS the abstract** rather
    than falling back to the provider's. `ogrinfo` printed every disclaimer;
    QGIS, opening the file the way the docs tell people to, showed none of
    them (`qgsLayerMetadata`, `gpkgLayerSpec.Abstract`,
    `gpkgLayer.Description()`). A disclaimer that only survives on the route
    nobody takes is not shipped.
  * **The anchors' palette was written for a different background.** The
    evidence points land on a saturated FGDC pattern fill covering the whole
    canvas, and index 0 was pure white (invisible over a pale unit) while
    index 8 was byte-identical to the "other" fallback (the ninth list and the
    catch-all were one symbol). Now nine distinct saturated fills with an
    opaque outline; the shared 0,0,0,80 default outline dissolves into a
    cross-hatch.
  Judged RIGHT and not to be redone: the four grades are distinguishable at
  map scale, the amber ramp runs the same direction as the map's (classic
  strongest), `ungraded` is grey and visibly OFF the ramp so a NULL never
  reads as "measured, weakest", draw order is anchors > contacts > units, all
  three layers valid, and the counts match `ogrinfo` (659 / 882 / 3687).
  One renderer trap worth keeping: `layer.extent()` on a project-loaded layer
  with a subset string returns NULL and the render job then never finishes,
  which looks like a hang with no error — the script reads the R-tree instead.

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

### The model has a score now, and the score is not the disclaimer

> **Superseded in part, 2026-08-13 (later that day).** What follows describes
> the first measurement — CAR against IPIS alone, one lift per claim, typed by
> hand into `srv/geomap_scores.go`. All of it still reads correctly as the
> *reasoning*, and the discipline sections below are unchanged. But the numbers
> are now **generated**, there are **seven truth sets across three sheets**, and
> a claim can come out **contested**. Read "Four lists, three sheets, and a
> disagreement that must reach the reader" below before quoting any figure in
> this section.

Added 2026-08-13. Both choosers — "rock types that can host gold" and the
Junctions tab — were textbook inference stated in three amber dots and had
**never been scored against an occurrence dataset**. Every surface carried "an
inference, not a record of any deposit", which is true and is not the same
claim: it says the layer is not *evidence*, not that it is not *useful*. Three
dots cannot help reading as a ranking, because a ranking is the only thing
three dots can be.

Measured (`scripts/geomaps/eval_affinity.py`, quoted in `srv/geomap_scores.go`,
shipped in `std.affinity_skill`, output `data/eval/geo_affinity_car.json`) on
the one sheet where we hold an independent occurrence list: **CAR vs 914 IPIS
artisanal-mine visits** (`data/ipis/caf_mines_ipis.csv`, per-site gold/diamond
flags). The result inverts the panel's implicit story:

| claim | capture | baseline | lift | control |
|---|---|---|---|---|
| gold **junctions**, likely+ | 52.8% within 5 km | 22.8% | **2.32×** | 2.33× vs diamond sites |
| gold junctions, classic | 20.4% | 5.6% | **3.63×** | ∞ (0 diamond sites) |
| gold **units**, any host | 23.7% of sites | 37.7% of area | **0.63×** | — |
| gold units, likely+ | 10.1% | 26.4% | **0.38×** | — |
| diamond units, classic | 34.0% | 24.1% | **1.41×** | — |
| diamond junctions, likely+ | 2.2% | 5.3% | **0.41×** | — |

**On this sheet the junctions carry the signal and the units do not.** A reader
who isolates "rocks that can host gold" on CAR is looking at ground *less* often
worked than the sheet as a whole. The eval's per-class table says why: the pits
sit on `Zeta` (gneiss, 294 sites) and `gamma_h` (heterogeneous syncinematic
granite, 85) — the two largest units the affinity table grades **zero** for
gold, because "gneiss" and "syncinematic granite" are not the words a textbook
uses for a host. The junction rules recover exactly that ground from the other
side: it is the *contact* between them that is graded, and that is where the
workings are. Diamond is the mirror image (units 1.41×, junctions 0.41×), which
is why the eval runs two commodities — one would have concluded "junctions
good, units bad" about the model as a whole.

Three measurement disciplines, each of which changed a number materially:

* **The baseline is area, and area is where a lift comes from.** 53% sounds
  strong until you know that a random point on CAR is within 5 km of *some*
  contact 53.4% of the time. Proximity to a line only means something graded.
* **The random baseline is drawn inside the sheet's own union of units**, never
  its bounding box, and the continental section draws inside the **convex hull
  of the visits** — IPIS surveys the east of DRC, so a country-wide baseline
  credits any layer that merely happens to be eastern, including a layer of the
  survey's own footprint.
* **A cross-commodity control separates "finds gold" from "finds mines."** In a
  country with 914 visited artisanal pits that is a real confusion: the gold
  junctions score 2.33× against *diamond-only* sites, so it is gold the lines
  are finding. The diamond junctions' 1.09× control is how you can tell their
  0.41× is not a sign error.

⚠️ **The scores are not a tuning target.** Re-weighting the affinity table until
CAR's unit rows read 1.5× fits 675 visits in one country and calls it geology —
the same failure invariant 12 forbids for the fire algorithm. The eval exists to
make a weak claim *legible*, not to launder it. If a rule changes it is because
the geology argument changed, and the score is re-measured afterwards.

Shape rules that are easy to undo:

* **`nil`/absent means UNMEASURED, and the surface must print that word.**
  Eight of ten commodities have no occurrence list, and an absent number beside
  a 2.3× reads as a low score rather than as no score.
* **`geoAffinityScoreFor` never quotes a higher floor than asked for** — a
  reader on "any host" must not be shown the flattering "classic" number
  (`TestAffinityScoreForNeverQuotesAHigherFloor`).
* **`gold unit w>=3` is absent, not 0.** No CAR class is graded classic for
  gold, so the filter selects 0% of the map and the ratio has no denominator.
  "Nothing to measure" and "measured, found nothing" are different statements.
* **The verdict follows the lift**, checked (`TestAffinityVerdictFollowsLift`),
  because the UI colours on the verdict.
* **`TestAffinityScoresMatchEvalOutput` pins the hand-typed table to the eval's
  JSON** (skips if absent — it is a gitignored input, and a test that fails for
  everyone who has not run a 90 s script is disabled within a week). Invariant 2
  applied to a measurement: the table may be hand-written, it may not disagree.
  `json.dump` writes `NaN`, which is not JSON and made the file unreadable to
  the Go side — undefined ratios now ship as `null`.

### Four lists, three sheets, and a disagreement that must reach the reader

Added 2026-08-13, after the section above. The first measurement scored **one**
sheet against **one** occurrence list, which left two problems: the model was
unmeasured on two of three served sheets (so the panel quoted a Central African
number over the Tanzanian craton), and a single list cannot be wrong out loud.

**The eval now runs every served sheet against every list that reaches it, by
default**, and `scripts/geomaps/gen_scores_go.py` writes `srv/geomap_scores_table.go`
from the JSON — 68 measurements, seven truth sets, **no number typed by hand**
(invariant 2; the table is committed so the server builds without Python or the
licence-sensitive occurrence files, and `TestAffinityScoresMatchEvalOutput` pins
every shipped row to the JSON by `EvidenceID` when it is present).

| truth set | what it is | its blind spot |
|---|---|---|
| `car/ipis` | 914 artisanal-mine field visits, gold/diamond flags | where a surveyor can drive |
| `car/tearline` | NGA Tearline / W&M geoLab, 40 mine systems traced off imagery inside 8 Lobaye Invest permits | a **licence boundary**; only 3 of 40 are within 2 km of an IPIS site |
| `car/crisistracker` | 41 mine sites recovered from Crisis Tracker incident reports, **eastern** CAR | attacked places that could be reported; 5 of 41 name a mineral |
| `car/ipis_armed`, `car/ipis_calm` | **strata**, not lists: IPIS split by armed presence recorded at the pit | share IPIS's footprint entirely |
| `tanzania/gst` | the GST's own 480-point register, 6 commodities above the floor | **not arm's length** — same minerogenic programme drew the units |
| `sudan/osm` | 272 OSM gold-tagged mine features | 91% one mapper's campaign |

Five rules, each of which changed a number or a sentence:

* **The baseline is the ground the list could have seen**, carried by the truth
  set so a call site cannot forget it: the mapped sheet for a national register,
  the sites' own hull for one mapper's campaign, **the searched permits** for
  Tearline. A hull round the mines Tearline *found* would be tighter than the
  ground it searched — flattery by hiding the searched-and-empty part. When the
  region is not the sheet, the **area share is clipped to it too**, or capture
  and baseline answer different questions.
* **A stratum is not a second opinion.** Two halves of one survey share its
  footprint and its definition of a mine; counting them as agreement lets one
  survey vote three times. They are excluded from the agreement report and from
  `geoAffinityEvidenceFor`, and surface instead as `Spread`.
* **The pooled CAR gold lift has a known confound, so both strata ship by
  default** — not behind a flag, because a correctness fact that costs a flag to
  see gets quoted without it. Gold units score **2.04×** where IPIS recorded an
  armed actor and **1.31×** where it did not (capture p=0.0033 over 20k label
  permutations, p=0.0021 shuffling only within prefecture, which holds the
  geology fixed — `scripts/eval_reach_strata.py`, read never recomputed).
* **Under the floor is a count, not a zero.** Crisis Tracker's 4 gold sites ship
  as `geoAffinityTooFew`, so the panel can say eastern CAR is *unmeasured*
  rather than leaving a gap that reads as a low score.
* **Comparisons are per floor.** Quoting each list at its own highest floor
  compares w≥3 with w≥2 whenever one grades no ground that highly, and buries
  the finding: the CAR gold strata differ 1.55× at w≥1 and 1.04× at w≥2.

**The disagreement is the headline.** On CAR gold junctions IPIS says **2.18×**
and Tearline says **0.00×** on the same claim. Any single row is defensible alone
and misleading on screen, so `verdict` is `mixed` and the panel says *"the
surveys disagree"*, names both with their n, and draws the cells **violet**
(`.contested`) — neither amber (a target) nor grey (a dead end). When a caller
takes one row anyway, it gets the **lowest** lift, never the flattering one.

Two bugs this found, both of which had printed a plausible wrong number:

* **`mixed` had two causes and one sentence.** At z6 over the Central African
  basin the Sudan and Tanzania sheet *envelopes* also reach the viewport, so
  "the surveys disagree" appeared for gold units — when in fact the CAR's two
  lists agree (0.63×, 0.06×) and it is **Sudan** that scores 1.91×. `reason` is
  now `'surveys'` (one ground, contradictory lists) or `'places'` (the model
  works in one country and not another), with different wording; `'surveys'`
  outranks `'places'`.
* **A score must describe a layer that is DRAWN.** `sheetInView` was true for
  any sheet whose bounds reached the screen, switched on or not — so a reader
  drawing only the CAR was quoted Sudan's number. `sheetActive` = available **and
  on** and in view, and it is now the single test everywhere.
* **The per-floor loops silently matched nothing** (`rec["1"]` vs `rec[1]`) and
  printed an empty agreement section, which reads exactly like "the lists agree
  everywhere" — invariant 1, in the report whose job is honesty. `at_floor()`
  accepts both, and "nothing comparable" now prints as a result.

### Structural context layers (WP2, shipped 2026-08-13)

The two AKP layers the survey below recommended are ingested and served:
`active_faults` (406, Macgregor 2014) and `craton_edges` (9 — the **boundary**
linework of the craton polygons, never the fill; see the survey for why the
interior means nothing). They are **ungraded context lines outside the
rock/junction matrix**: no affinity rows, no amber ramp — fault red-brown
short dash, craton a **solid wide blurred translucent violet band** (never a
dash: the AOI outline is a dashed cool-toned line, and a dashed violet line
at similar width read as "a boundary somebody drew" — a band reads as a
zone). Same inks in the GPKG QML (`styleGeoStructural`).

* **Fetch:** `scripts/geomaps/fetch_akp.py` → `data/akp/*.geojson` with R7
  attribution embedded; fails writing nothing when counts fall short.
* **Serve:** `srv/geomap_structural.go`, `GET /api/geomap-structural/{layer}`
  (NOT `/api/geomap/structural/…` — that pattern conflicts with
  `/{sheet}/download` in Go's ServeMux). Gzipped once at load, immutable +
  `?v=` rev. Catalogue block `structural` in `/api/geomap`; an empty file is
  `available:false` + reason, never an empty layer (invariant 1/8).
* **Skill:** `geoStructuralSkill`/`geoStructuralSkillScope` in
  `geomap_scores_table.go` are **generated** by `gen_scores_go.py` from the
  eval's `continental.proximity` block (which records its own `near_km`).
  Absent block ⇒ empty table ⇒ the UI prints "unmeasured". Skill keys keep
  the eval's words (`craton_edge` vs served `craton_edges`) so a mismatch is
  visible, and `TestStructuralSkillMatchesEvalOutput` compares the two.
* **Frontend:** `shared.structural` Set in geomap.js; add-once layers, toggle
  is a paint property (`paintStructural`); share param `geomap_structural=`
  (only reader-drawn state travels); auto-drawn with the junction tab via
  `GeoMap.setStructuralAuto()` under the autoContacts contract
  (`shared.autoStructural`). Panel block `geoStructuralBlockHTML()` in
  globe.html prints the catalogue's lifts (never typed — a src_guard greps
  for hardcoded `×` numbers) incl. coltan/craton 0.4×, which is a mixed
  result and stays visible.
* **Mixer rows + MapTips (2026-08-13):** the layers are operated from the
  geology mixer like everything else geological — `structuralFootHTML()` in
  maplegend.js renders a "Structural setting" row pair under BOTH tabs (rock
  and junction views), each row wearing its map ink as its swatch
  (`.ml-strx-sw`) and its catalogue lifts (src_guard
  `geo_structural_mixer_no_lift` keeps typed lifts out of maplegend.js too);
  toggle via `MapLegend.toggleStructural(id)` → `GeoMap.setStructural` (a
  hand gesture, clears autoStructural). Hover/click tips:
  `bindStructuralTip`/`structuralTipHTML` in geomap.js register on the line
  layers (priority −28, below AOI/park −20 — the drawn boundary answers
  first, the margin is a peer tab "Craton"/"Fault"); the tip names the
  feature, the notice, and the lifts+scope from the catalogue, with sub-1×
  spelled "LESS likely". `near_km` ships in the catalogue skill block
  (generated — `geoStructuralNearKm` in geomap_scores_table.go) so the tip
  derives its "within N km" instead of typing 25. globe.html's restore gate
  accepts `geomap_structural=` without `geomap=` (structural lines need no
  sheet).
* **GPKG:** two tables `structural_active_faults`/`structural_craton_edges`;
  whole catalogue ships both `Visible:false`, view export ships exactly
  `sel.Structural` with `Visible:true`; stamp covers both input files
  (`geoMapGPKGInputs`), tested.
* **Tests:** `srv/geomap_structural_test.go` (served == file, empty refusal,
  `?v=`, stamp, view selection), `TestStructuralSkillMatchesEvalOutput`,
  api `geomap_structural_served_whole`, ui share params + src_guards.

### Other geology data, weighed: what is worth ingesting and what is not

Surveyed 2026-08-13, all measured against the same IPIS/USGS truth rather than
judged by resolution. `--continental` in the eval reproduces the table.

| source | what it is | verdict |
|---|---|---|
| **JRC Africa Knowledge Platform** WFS (`akp:` layers, GeoJSON, whole continent, one request, no key) | 200+ layers incl. `LithoMap_Africa` (USGS 2001, 11,977 polys, 44 age classes), `cratons` (9), `active_faults` (406, Macgregor 2014), `granites` (544), `africa_major_mineral_deposits` (969 USGS deposits) | **the one to ingest** |
| **CGMW/BRGM 1:10M Africa** (`mapsref.brgm.fr/wxs/1GG/…`, the BGS/OneGeology record) | Thiéblemont 2016, `GeologicUnits` (STRATI/AGE/NOTATION/LITHO) + a `Faults` layer with `DESCR` = Fault / Thrust fault / Inferred thrust | good vocabulary, **GML only and painfully slow** (4 min for a 6°×8° box, connection resets); take the faults, not the units |
| `edepot.wur.nl/484816` (DRC geological map) | 5040×7072 JPEG scan of the same Thiéblemont compilation, ~130-entry printed legend, deposit symbols **with no key on the sheet** | **skip** — a scan of data we can get as vectors, and its symbols are unusable |
| RCMRD `Africa_Surface_Lithology` ImageServer | 20-class parent-material raster, 3-band **RGB composite**, `exportTilesAllowed: false`, empty raster attribute table | **skip** — it is a picture of a classification, not the classification |
| `data/flowacc/` (already local) | 273 flow-accumulation tiles | covers 19.25–25.75E; **zero** IPIS CAR sites fall inside it |

Measured skill of the JRC layers, DRC (7,163 IPIS visits, baseline = random
points in the visit hull):

* `active_faults` within 25 km: gold 0.227, cassiterite 0.350, coltan 0.376 vs
  random 0.138 — **1.6–2.7×, and the ordering is right** (pegmatite-hosted tin
  and tantalum are more fault-controlled than gold). This is the single most
  valuable new layer, and it is 406 lines.
* `cratons` **edge** within 25 km: gold 0.256 vs 0.087 random, **2.9×**. Scored
  on the edge, not the interior, on purpose: 60% of the hull is inside a craton,
  so "on a craton" scores ~1.0 and means nothing. Craton margin is the same
  claim our junction rules make, one tectonic order up.
* `LithoMap_Africa` gold density: `PC` 2.3×, `Qv` 4.7× — but `pCm`
  (undivided Precambrian) is 79% of the workings on 61% of the area, i.e. **1.3×
  at continental scale**. It is an age map with four igneous classes; as a
  *host* map it is much weaker than our sheets, which is the argument for
  keeping ours and using this one only where no sheet reaches.
* `Kimb`/`S_d` (kimberlite): 159 polygons, but the nearest is **761 km** from
  the median DRC diamond site and 876 km from CAR's. Southern Africa only —
  a layer that is real and simply not here.
* `gem_active_faults` (16,195): fetched, then **discarded** — 727 in an Africa
  bbox, and their median distance to every site set is ~3,500 km. The catalogue
  is UCERF3/SHARE, i.e. California and Europe. A bbox filter on a global
  catalogue returning 727 features is exactly the shape invariant 1 warns
  about: it succeeded, and it answered a different question.

`data/geology_truth/usgs_africa_deposits.geojson` (969 points, CC BY 4.0,
committed) is the second truth set and the reason to keep it: it is
industrial-scale *named* deposits, a different kind of claim from a visited
artisanal pit, and a model scored only on the latter is being credited with the
former. It reaches Sudan (5 deposits, 3 gold) and Tanzania (18, 3 gold) — and
the eval **refuses to print a lift below n=8** rather than computing one from
three points, because a number printed there gets quoted. Its one finding so
far is a negative worth keeping: Tanzania's `tectonic_setting` = "greenstone
belt" covers 2.1% of the sheet and contains **0 of the 3** gold deposits (both
Bulyanhulu and Buck Reef land in `miNA`, the Neoarchaean granitic complex) — so
"filter to greenstone" is not the free win it looks like.

**Unused fields already in the data.** The Tanzania catalogue carries six
survey-owned columns nothing reads: `metamorphism` (32/41 classes — greenschist
16.1% of the sheet, almandine-amphibolite 14.7%, granulite 4.4%),
`tectonic_unit` (30/41 — Tanzania Craton 27.7%, Mozambique Belt 20.2%, Ubendian
5.7%), `tectonic_setting` (41/41), `stratigraphy` (15/41), `age_ma` (13/41) and
`age_strat` (41/41, already used for the tip's "as printed"). Metamorphic facies
is the interesting one: orogenic gold is a *greenschist-facies* phenomenon and
we have that column for 78% of the sheet, unread. It is a **third axis** beside
age and lithology, not a fourth commodity — the same argument that made contacts
a table rather than an eleventh row.

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
### The download in the panel: the view you built, whole or filtered

Added 2026-08-13. The panel is where a reader *builds* a view — gold hosts,
likely and up, these two junctions, that period hidden — so the question that
follows it is "can I have this in QGIS". Until then the only download was the
whole catalogue, four clicks away in admin ▸ Map Settings.

**Two paths, deliberately different shapes** (`downloadFacts`,
`openDownloadMenu`, `downloadView` in `maplegend.js`):

| | route | shape |
|---|---|---|
| **this view** | `POST /api/geomap/geopackage` | body is the resolved selection; built per request, temp file, `private, no-store`, served once |
| **every sheet** | `GET /api/geomap/geopackage` | the stamped cached `geology.gpkg`, a plain `<a>` so it works with JS off |

* **The selection is resolved by the CLIENT** and travels as unit keys +
  contact pairs (`GeoMap.selection()` → `geoMapSelection` in
  `srv/geomap_gpkg.go`). The panel's filter is five clauses deep (commodity
  chips expanded through the host map at a strength floor, matrix cells, hidden
  units, hidden periods, a lithology filter); re-implementing it in Go would put
  two filters between the reader and their own download, which is the shape of
  bug where the picture and the file disagree and neither says so. It sends the
  same `visibleCodes()`/`visiblePairs()` that feed the MapLibre filter, so **the
  file is the picture by construction**. A snapshot of codes is fine here (it
  lives for one request) and wrong in a share link, which is why
  `getShareParams` travels as commodities and lithologies instead.
* **The selection is applied before the commodity columns are derived**, so a
  filtered file's `w_*` columns describe *the file*: a `w_uranium` column on a
  gold-only export is a claim that uranium was considered and found absent.
  Only the sheets that actually contributed are named in the description.
* **A selection matching nothing is an ERROR** (409, not 500 — the usual cause
  is a stale page and the message says to reload), never an empty layer. Same
  for a pair set that matches no line. Invariant 1.
* **A filtered export announces itself** — `A VIEW, not the whole catalogue…`
  in the layer description, and in the QGIS project title. Two files with one
  name and different contents is the truncation trap, so the filename carries
  the reader's own words for the view (`geoViewFileName`, never `geology.gpkg`).
* **It never touches `geology.gpkg`.** Writing one reader's subset into the
  cached file would hand the next visitor a subset with a *fresh stamp* — the
  staleness trap in its worst form, because the file would look correct. No
  cache either: a view is one of ~2⁴⁶ combinations.
* **The view export is not in admin ▸ Map Settings, and should not be.** A view
  only exists while the panel is open; admin offers the catalogue. Two surfaces
  for one download would be a third path to keep in sync.

**Two more layers, in both files** (`srv/geomap_gpkg_layers.go`):

* `geology_contacts` (MULTILINESTRING) — graded from the lithology pair by the
  *same* `geoContactRuleIndex` the map paints from, in the map's own amber ramp,
  because someone who filters junctions on screen and opens the file is looking
  at the same lines. `grade` is a word (`classic`/`likely`/`weak`/`ungraded`);
  `ungraded` means **the model says nothing** and carries NULL, not 0.
* `mining_anchors` (POINT) — the published workings the model was scored
  against, **never filtered by the reader's commodity or by the sheets**.
  `resource` is a column, so the reader narrows it and knows *they* did; a file
  where every anchor agrees with the layer is a picture of our own filter. An
  anchor outside the cutline is exactly the row that shows where the evidence
  stops. A server without the file gets **no layer and a project title that says
  so** — "we could not ship it" and "nothing was ever checked" are different
  statements.

**The cache stamp covers units, contacts AND anchors.** A re-derived contacts
file leaves units that are still right and hairlines a generation old — the same
staleness one layer down, invisible *because* the polygons are correct. A
missing input is recorded as absent, not skipped.

**The menu is the app's download menu.** Same `.aoi-menu-row` markup, same ⧉
copy-link button and same `copyExportLink()` as the park/AOI menu
(`exportMenuItems` in globe.html) and the animator's (`anim.js`) — including the
reason the ⧉ exists at all, which is that Safari's own "Copy Link" copies the
row's *label*. The link carries `geo_export=view|all` and, like `?anim_export=`
and `?aoi_menu_item=`, **points at a row without running it**: a link whose only
outcome is a build must be a click the recipient makes. `restoreFromParams`
implies `geo_panel` from it, since the arrow lives in the panel's bar.
`.ml-menu-dl` is width-fixed for the same reason the panel is — the notes would
otherwise set a 1,200 px shrink-to-fit box that gets clamped to the screen edge.

**`.sl-scrim` is z-index 10600, above `.aoi-menu`'s 10000.** The share dialog is
opened *from* one of these menus and deliberately leaves it open behind
(copying a link is not choosing a download), so it has to out-rank the menu
rather than close it. At 2600 it dimmed the map and left the geology panel and
its menu bright on top of the modal they had just opened.

**The geology panel itself sits at 900 (`.ml-menu.ml-panel`), not at
`.aoi-menu`'s 10000.** It borrows `.aoi-menu` for its box, but it is a
session-long panel, not a transient menu: at 10000 it floated over the admin
panel (2000) and every modal. 900 keeps it above the map furniture and below
the app's own overlay layer, the same rule the map tips (400) follow. The
selector needs both classes — `.aoi-menu`'s rule comes later in `globe.css`,
so a single-class rule loses the specificity tie.

Tests: `srv/geomap_gpkg_test.go` (the four filtered-export cases, the stamp, the
filename). Both rows have been **clicked through** and their files opened, and
the two newer layers have now had a `render_gpkg.py` pass (see § the raster
section above for what it found).

---

### One mixer, two homes: the panel over the map and the admin card

Added 2026-08-13. Admin ▸ Map Settings ▸ Geology used to be a **second, older
chooser for the same layer**: amber pill chips, its own strength ladder, its own
unit list, and no junction table at all. Two surfaces for one piece of state is
the shape of bug where a reader narrows the map in the panel, opens admin, and
finds a card that quietly disagrees with the map in front of them — and the old
one *could not express* half the current state (picked cells, junctions, the
measured lift). It is one object now.

* **`MapLegend.geoBody()` is the mixer's body as a string**, with no furniture
  around it: the state line, the grade ladder, both tables. `openGeoMenu()`
  wraps it in `headRow()` + `.ml-panel`; the admin card wraps it in
  `.geo-mixer`. Same handlers, same `GeoMap` reads, so the two cannot disagree
  by construction. The width stays fixed in both for the reason `.ml-menu-geo`
  is fixed — the two tables set different max-content widths.
* **The card's bar is the panel's bar minus the window furniture**: the same
  download arrow opening the same `MapLegend.downloadMenu`, a copy-link, and
  *Show beside the map* (the one gesture only this home needs — the mixer's
  columns are the periods **drawn**, and here the map is behind a fullscreen
  panel). No close/collapse: this is a section of a page, not a window.
* **The whole-catalogue `<a>` and `downloadGeoMapGPKG()` are gone.** One
  download surface, two rows, `MapLegend.downloadAllStarted()` for the
  "Preparing…" floor.
* **Historical Maps got the same treatment**: the app's toggle slider instead
  of a green Show/Hide *action* button for a *state*, `.geo-op` opacity, and
  `MapLegend.histDownloadMenu` — one row, but the app's row, so the two drapes
  in one section do not offer their downloads two different ways (and the ⧉,
  which is the only reliable way to get a link out of Safari, exists on both).
* **The download menu FOLLOWS its anchor now** (`followDl`), and does not close
  on scroll. Closing was tried first and is wrong twice: the gesture that
  brings the button into view *is* a smooth scroll, so a share link pointing at
  a row closed the menu it had just opened.
* **A rebuild under an open menu is a menu that vanishes** — the card stays
  dirty while `.ml-menu-dl` is up and paints when it closes.

**The card is built from the CANVAS, so it needs the canvas's own events.**
This is where the cost was, and both halves are load-bearing:

* A state change never paints; it marks dirty and asks for a frame, and the
  frame **declines while `MapLegend.paintPending()`** — `refreshWhenDrawn` calls
  back in after the paint. Without it the card built twice per gesture and the
  first of the two described the map as it was one gesture ago.
* `watchMap`'s idle handler calls `geoMapPanelCanvasChanged()`, guarded on
  `MapLegend.geoAnswerSig()` (layer set + contact hits + drawn columns + skill
  scope, deliberately **not** the viewport, so panning inside one country does
  not reshuffle a table the reader is working in). Without it, opening
  `?panel=admin&admin_tab=map-settings` cold painted the card before the first
  tile landed and it said *"No mapped sheet reaches this view"* over three
  countries, permanently — nothing after that was a state change. Same failure
  as the frozen `counting lines…` bar, one surface over.
* **The 104-row unit list is built only when Advanced is open.** Measured: 335
  KB of the card's 374 KB, nine tenths of it, written into a `<details>` nobody
  had opened. `geoSetAdvancedOpen()` re-renders, so opening the disclosure is
  what builds it. Same rule as the card behind a closed tab, one level in.

Net on the junction click with three sheets: card 374 → 116 KB, two builds →
one, 258 → 140 KB of HTML per gesture; the floating panel is unchanged at ~89
KB / ~113 ms and pays nothing for the card.

**The share link says which home it came from.** `geo_export=view|all` alone is
ambiguous once there are two surfaces, so the link relies on the
`panel=admin` + `admin_tab=map-settings` pair `buildShareUrl()` already carries:
with it the admin card points at the row, without it the floating panel opens.
One row is pointed at, in one place. The request is **held until a paint can
honour it** (`geoAdminExport`), because the first paint after a cold load is the
one that says "nothing reaches this view" — and the menu, once open, freezes
exactly that sentence under it.

**Reference mining sites are named under Advanced** (`geoAnchorBlockHTML`). The
download menu says it in one sentence; this is the reader who wants to know
*which* lists, under what terms, observed how. A withheld list is **named, not
omitted** (ACLED, whose terms forbid redistributing its rows and which was never
a mining list anyway); `terms: unstated` is shown as those words and is
deliberately not styled as a warning — "open" and "nobody said" are different
states and only one of them is permission; and every number is the catalogue's
(`/api/geomap` → `anchors`), never counted here.

---

### A zero must be provable, and a legend nobody has touched is furniture

Two closing passes on the geology strip, one about honesty and one about space.

**`counting lines…` never settled when the honest answer was zero.**
`contactsPending` was `contactHits === 0 && geoContactLayers().length > 0` — one
flag for **two states**. "The layer has not painted yet" and "the layer painted
and there is nothing in this viewport" are different claims, and only the first
is unmeasured; `barCountText()` was right to refuse to print a number it had
been told was unproven, so a genuine zero sat at `counting lines…` for good.
Invisible at z3 over three sheets (some contact is always in view); reproducible
at `?geomap=car&geo_panel=1&lat=6.5&lng=21.2&z=10.5`, where 71 line *types* pass
the filter and none of their geometry reaches a ~50 km viewport.

The fix is not a timeout and not a `paintNonce` bump — that was the earlier
attempt, and it failed because `paintNonce` counts *events that could change the
picture* (a `sourcedata` fires while the frame is still ahead of us), which is
not the same question as "has this layer had its chance to paint". `idle` is
different in kind: it is MapLibre's own word for *I have finished drawing*. So
idles are counted separately (`idleNonce`), the contact layer set records the
idle it first appeared at (`contactIdleAt`, reset whenever the set changes), and
`contactsSettled()` calls a zero **measured** only when the map has gone idle
since the layers appeared *and* every source they read is loaded. Then "no lines
here" is a reading of the canvas.

Measured after the fix, and the answer to the second open item — the contact
half of the zoomed-in gesture, which the previous pass could not time because
the count was stuck:

| gesture | sync | bar settles | max frame gap | HTML | qRF |
|---|---|---|---|---|---|
| Junctions, z9, one sheet | 58 ms | 535 ms | 510 ms | 49.8 KB | 129 ms / 606 calls |
| Junctions, z3.2, three sheets | 12 ms | 12 ms | 1620 ms | 67.1 KB | 184 ms / 404 calls |

Per idle, the ~200-point grid dominates and does **not** grow with zoom (z9: 51
ms for 200 point queries + 5.4 ms for the whole-viewport contact read, 439
features at z3 for 26 ms). The viewport bounds the work, as the earlier pass
guessed; the grid is the cost, and it is the same cost everywhere.

**The strip now rests.** It states what is draped, which is worth its width
while someone is reading it and is a banner the rest of the time — on a 412 px
phone the header, the chips and an eleven-swatch key took a third of the
readout, over the map. After 4 s untouched it folds back to its icons
(`.ml-rest`): words, sub-label, caret, × and key fold; the coloured tappable
chip and the opener stay. Rules, each a bug if broken:

* it **folds, it does not remove** — `max-width`/`max-height` and opacity, never
  `display:none`, so every target keeps its accessible name and title while
  folded, and it grows back rather than jumping;
* it **says how much it folded** (the swatch count rides on the chip as
  `data-rest`), derived from the rendered swatches, never typed — a key that
  silently disappears is a truncation that does not announce itself;
* it **never rests while a menu or the geology panel is open**, or while the
  pointer or focus is inside it (`restBlocked`) — a surface the reader is
  working in must not shrink under their hand;
* `fitCols()` measures the **last awake width**, not the current one: while
  folded the strip sits in the readout's spare grid cell, and sizing the key to
  that would wake a full-width strip carrying four swatches and a "+7".

Waking is one class toggle, so it is wired to `pointerenter`/`pointerdown`/
`focusin`/`wheel` on the **host** (render() replaces the strip's innerHTML on
every idle, so a listener on a chip would be thrown away with it) and to every
public `MapLegend` method except the quiet ones — `refresh` (it *is* render(),
called on every map idle, so waking there means never resting), the reads the
app makes of itself (`geoAnswerSig`, `geoBody`, `getShareParams`), and
`mxSettle`, which fires on a click **anywhere outside** the panel and would
otherwise unfold the legend on every click on the map.

On a phone the rested strip takes the readout's empty sixth cell
(`grid-column: auto`) instead of a full row: five stats in a three-column grid
always leave one, and a rested strip is exactly chip-sized. Woken, it spans
`1 / -1` again, because eleven periods do not fit in a third of a phone.
