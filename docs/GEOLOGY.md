# Geology overlays

Two scanned geological maps turned into **vector** overlays: Sudan (GRAS 2004,
1:2M) and CAR (BRGM 1964, 1:1.5M). A park or AOI facing a gold rush should be
able to switch on every unit relevant to gold in one click, and still hide or
isolate individual units and set opacity. Everything outside the mapped country
— paper, collar, legend boxes, neighbouring-country fill, insets — is dropped.

Not to be confused with the **raster** `?histmap=` overlay (Sudan Survey
1:250k topographic sheets, `scripts/histmaps/`, `srv/histmap.go`). Different
data, different serving path; the georeferencing discipline and the
204-on-miss / `?v=<rev>` conventions are shared.

## State

| Stage | Sudan | CAR |
|---|---|---|
| georeference | done, 29 GCPs, rms 1.01 px / 260 m | done, 42 GCPs, rms 5.17 px / 330 m |
| legend extracted | done, 53 units | done, 20 units |
| vectorized | done, **46 classes**, 34 MB GeoJSON | done, **17 classes**, 6.9 MB |
| window hold-out | claim 0.998, accuracy 1.000 | claim 0.998, accuracy 1.000 |
| unclaimed inside cutline | 9.0% | 8.7% |
| area claimed | 2.28M km² of ~2.5M | 564k km² of ~623k |
| tiles | `data/geomaps/sudan.mbtiles` 9.8 MB, z0–10 | `car.mbtiles` 3.1 MB, z0–10 |
| server | done — `srv/geomap.go` | same |
| UI | done — admin ▸ Map Settings ▸ Geology | same |

Live: `/?pwd=test2026&geomap=car&lat=8&lng=22&z=6`

## The one thing to understand

**Neither sheet is printed in flat ink. Both are halftone screens**, and the
legend colour is only the screen's average. Averaging destroys the signal: 12
groups of Sudan units and 5 of CAR average to *identical* RGB
(`Legend.merge_groups()`), so nearest-legend-colour is capped around 0.77 on
Sudan **by construction**. But the screens differ where the averages do not —
TA and TC are the same yellow at different dot densities.

So a pixel is classified by the **distribution of palette indices in a window
around it**, matched against the same distribution measured over the legend
swatch (Bhattacharyya, implemented as box filters + one argmax).

### ⚠️ The swatch hold-out lies. Use the window hold-out.

The original hold-out trained on half a legend box and tested on the other
half — both a few hundred pixels wide — and reported 0.95–1.00. It is measuring
the wrong thing. In the map body the decision is made from a **17–33 px
window**, whose histogram is a small sample; inks 0.13 apart in Bhattacharyya
distance are trivially separable over 180 px and pure noise over 33.

The gap is not a rounding error, it is a whole formation. CAR's `GO` and `GC2`
scored a *perfect* swatch hold-out while the Mouka-Ouadda plateau — an area the
size of Belgium — came out **white**: the two scored within `--min-margin` of
each other on every pixel, so it was dropped rather than mislabelled.
**Near-identical classes do not swap, they cancel**, and the symptom is a hole,
not a wrong colour. 34% of CAR inside its own cutline was unclaimed.

`window_holdout()` therefore tests random `win`-sized patches and reports two
numbers: **claim rate** (fraction of patches whose top-two margin clears
`min_margin` — the rest are the holes) and **accuracy of the claimed ones**.
`resolve_classes()` iterates it, merging the pair it most confuses, until
nothing is confused; merges are restricted to pairs `merge_groups()` already
calls the same ink, because a window confusion between two visibly different
colours means `win` is too small, not that two real formations are one.

Result: Sudan 53 units → 47 classes, unclaimed 16% → 9%. CAR 20 → 17,
unclaimed 34% → 8.7%.

```bash
python3 scripts/geomaps/vectorize.py sudan --holdout   # ~40 s, prints both numbers
python3 scripts/geomaps/vectorize.py car   --holdout   # ~90 s
```

**Never judge this by eye or by the swatch number.** The claim rate is the
metric that catches a formation going missing.

### paper is a class, and it is discarded

`Legend.paper_like()` names units within `PAPER_DIST` of the paper tone (CAR's
migmatite `M`, its alluvium `a2`). Colour cannot find them and, worse, *they*
find the paper — every margin, legend box and inset comes back as migmatite.
`paper_signature()` lets paper compete as a synthetic class (pure paper is
exactly one palette index) and `write_label_tif(drop=...)` writes it as 0.
Paper wins only where there is no screen at all, and paper losing narrowly is a
margin drop — both failure directions land on "unclaimed", never on a wrong
formation. Nothing is hand-picked; picking a blank region by eye is exactly the
unrecorded input this pipeline refuses elsewhere.

Also fixed: `fitted_palette()` returned k-means centres in **BGR** (OpenCV
reads BGR) while `palette_lut()` unpacks its colour cube as RGB. It still
classified — the mapping is deterministic — so it was invisible except as
accuracy left on the table.

## Files

| file | role |
|---|---|
| `scripts/geomaps/sheets.py` | the two sheets: provenance, graticule, seed affine |
| `scripts/geomaps/gridfit.py` | finds the printed graticule intersections |
| `scripts/geomaps/georef.py` | GCPs → TPS warp → `work/<id>_geo.tif`, cut to country |
| `scripts/geomaps/legend.py` | the printed legend, measured off the scan; `AFFINITY` |
| `scripts/geomaps/vectorize.py` | classifier → `<id>_units.geojson` + `<id>_classes.json` |
| `scripts/geomaps/tiles.sh` | tippecanoe → `<id>.mbtiles` (z0–10) |
| `srv/geomap.go` | `/api/geomap`, tile route, download |
| `srv/static/geomap.js` | `window.GeoMap` — layers, isolation, share links |
| `srv/templates/globe.html` | `renderGeoMapPanel()` / `geoMapSheetHTML()` |
| `data/geomaps/legend_{sudan,car}.json` | **committed** — measured input |
| `data/geomaps/{sudan,car}_classes.json` | **committed** — the class catalogue |
| `data/geomaps/*_units.geojson`, `*.mbtiles` | gitignored derived output |
| `data/geomaps/{src,work}/` | gitignored, ~1.9 GB |

## `_classes.json` is the catalogue, and the server reads it — not the legend

`legend_*.json` is the sheet's **printed unit list**. What the tiles carry is
the **class list**, which merges units the print screen does not separate and
drops any that never occur in the map body. Serving the legend would offer the
user toggles for classes that cannot be drawn. `_classes.json` is small,
committed, and carries the `quality` block (window, claim rate, accuracy, which
pairs were merged) — the record of what this build actually measured.

A merged class carries **every** member code (`code: "GC2/GO"`, `codes: [...]`)
and is labelled with all of them, never an arbitrary pick, because the sheet
genuinely does not say which one a patch is. Its commodity affinities are the
**union** of its members' at the highest weight any member carries, and each
`why` is prefixed with the member code it came from (`"GC2: the principal
secondary diamond reservoir"`) so the union is not a quiet upgrade.

## Commodity affinity

`AFFINITY` (in `legend.py`) is keyed by **`(sheet, code)`, not `code`**. The
sheets reuse letters for unrelated things — `S` is Silurian sandstone on Sudan
and a gold-bearing schist belt on CAR.

It is an **inference over lithology** ("rocks of this kind host X"), never an
occurrence dataset, and **must be labelled as such wherever it surfaces** —
the panel, the click popup and any future report line all say so. Compare the
mining verdict in `AGENTS.md`: inference from context ships, fabricated
evidence does not. Nothing here counts, ranks or locates a deposit.
`weight` is 1–3 (3 = classic host).

Present commodities: Sudan — cobalt 4, copper 4, gold 14, iron 2, lithium 2,
rare_earth 1, uranium 11. CAR — cobalt 3, copper 2, diamond 6, gold 7,
lithium 3, rare_earth 1, uranium 3.

## Vectorizer

```bash
python3 scripts/geomaps/vectorize.py sudan                 # ~5 min
python3 scripts/geomaps/vectorize.py car                   # ~4 min, peak 3.5 GB
python3 scripts/geomaps/vectorize.py sudan --repolygonize  # ~30 s, reuses the label raster
scripts/geomaps/tiles.sh                                   # both sheets, ~50 s
```

`--repolygonize` reuses the warped label raster and only redoes polygonisation
and the catalogue — for a labelling or metadata change, not a classification
one. It asserts the class list did not change (the raster encodes class
*indices*) and refuses rather than silently relabelling.

Load-bearing decisions, all still true:

* **Classification runs on the SOURCE raster, never the warped one.** The warp
  is TPS with `-r near`, which duplicates and drops rows — invisible in a
  colour and fatal in a screen. We classify in scan space and warp the **label
  raster**, the one thing `-r near` is unarguably correct for. GCPs are reused
  with x/y divided by the stride.
* **Sudan's scan contains exactly 64 distinct colours** — already posterised,
  so that *is* the printer's palette. CAR is continuous-tone → 32-centre
  k-means **fitted over the legend swatches only** (a global fit spends its
  clusters on paper).
* **Ambiguous pixels are dropped, not guessed** (`--min-margin`, 0.02). On a
  screened sheet the ambiguous pixels are mostly line work and lettering, where
  a confident wrong label draws a hairline of some unrelated formation along
  every contact.
* **`--stride` costs nothing in accuracy** (window is 17–33 px) and turns a
  400 Mpx problem into 25 Mpx.
* Sieve and simplify happen **before** the GeoJSON is written; `min_area_km2`
  is in ground units so both scales drop the same real feature size.
* Memory (both learned by being OOM-killed on CAR, 406 Mpx, 7 GB box):
  `palette_lut()` searches the 24-bit colour cube **once**; `classify()` works
  in row bands with a `win//2` halo. Peak ~3.5 GB. Watch it if you raise
  `band` or `K`.

## Tiles

`scripts/geomaps/tiles.sh` → `data/geomaps/<sheet>.mbtiles`, z0–10, one layer
`units`. **Every class is kept at every zoom** — `--drop-densest-as-needed` and
friends are deliberately not used, because a formation absent at z4 reads as a
rendering bug and a tileset missing a unit is indistinguishable from a sheet
that never mapped it. What gives instead is geometry detail
(`--simplification`, `--coalesce`), which is visible as a coarser outline.

z10 because at 1:1.5M–1:2M the source line work is ~500 m; beyond it the client
overzooms, which is honest about the sheet's precision.

## Server (`srv/geomap.go`)

```
GET /api/geomap                          catalogue: every sheet, classes, quality
GET /api/geomap/{sheet}/{z}/{x}/{y}.pbf  vector tile (204 on miss)
GET /api/geomap/{sheet}/download         the MBTiles
```

* A tile **miss is 204, not 404** — a sheet maps one country inside a
  rectangular envelope, so most in-range tiles legitimately have no data.
* Tiles are `immutable, max-age=7d`, so a rebuild **must** change their URLs:
  `rev` (mtime+size) rides in the `tiles` template as `?v=`. Worse here than
  for the histmap, because a rebuild can change the *class list*, so stale
  tiles would carry class names the catalogue no longer describes. **The client
  must use `meta.tiles`, never a hand-written tile path.**
* Blobs are gzipped inside the MBTiles and go out with `Content-Encoding: gzip`
  rather than being decompressed server-side.
* A sheet whose catalogue exists but whose tiles do not is reported
  `available:false` **with** its catalogue, so the panel can name it and say
  what to run instead of showing an empty list.

## UI (`srv/static/geomap.js` + `renderGeoMapPanel()`)

Admin ▸ Map Settings ▸ Geology, below Historical Maps. Per sheet: Show/Hide,
opacity slider, **commodity chips** (the headline interaction), and a
collapsed list of all classes with per-class hide.

* **Two layers per sheet regardless of class count** — one `fill` and one
  `line`, both with a data-driven `match` colour expression built from the
  catalogue. A layer per class would be 46 layers on Sudan.
* **Isolation, not a hide-list, for commodities.** One click in, one click out,
  and it composes with nothing the user has to undo. `isolate` wins over
  `hidden`; hiding a class *while* isolated removes it from the isolation,
  which is the only reading that does not silently discard what the user built.
* **Layer order**: directly above the basemap raster, below every vector
  overlay. Anchored on the first non-raster, non-background layer, so anything
  added later (a pin, a trajectory) lands on top for free.
  `switchBasemap()` **excludes** `geomap-*` from its generic capture and calls
  `GeoMap.reattach()`, which re-adds on `idle` — re-adding during `styledata`
  is silently dropped because the `before` id has not landed yet. Same trap,
  same fix, as `HistMap`.
* Click a unit → popup with code, name, group, the merged-units warning, and
  the affinity list with its disclaimer. **Click, not hover**: the overlay
  covers the whole country and a hover handler would fight every other tip.
* Share links: `?geomap=car` is the common case. `geomap_only=`, `geomap_hide=`,
  `geomap_color=`, `geomap_pattern=`, `geomap_lith=`, `geomap_opacity=` and
  `geomap_adv=` (the Advanced disclosure — a panel setting, so it travels even
  with the layer off) only appear once changed, so a plain link does not
  carry 46 codes. **`geomap_opacity` absent means auto**, not 0.42: the value
  is picked per basemap, so freezing a computed number into a link would break
  the layer for whoever opens it on the other basemap. **A code the current build no longer has is dropped**, and an
  isolation that ends up empty shows all classes plus a toast — a rebuild can
  merge two classes and thereby rename both, and rendering nothing looks
  exactly like "this sheet has no data here".

## GeoPackage download

`GET /api/geomap/geopackage` → `geology.gpkg`, **one file covering every
sheet**, offered in the panel's *Data* row. `srv/geomap_gpkg.go`; measured
2026-08-12: 16 MB in 2.6 s for Sudan + CAR.

**One file, one layer, the sheet as a column** (2026-08-12). It used to be one
GeoPackage per scan, which made the download mirror our storage rather than the
user's question: rock does not stop at a border, and anyone intersecting units
with a park or a concession had to open two files, reconcile two column sets and
union them by hand. Exactly the seam the single Geology toggle removed on the
map. `sheet`, `sheet_title` and `sheet_year` are columns; `GET
/api/geomap/{sheet}/geopackage` **308s** to the combined file, because those
URLs are in shipped links and a 404 would read as "the export was removed".

* **A unit is identified by `(sheet, code)`, and `key` is that pair.** `code` is
  unique only *within* a sheet — `S` is Silurian sandstone on Sudan and a
  gold-bearing schist belt on CAR. The categorised renderer keys on `key`; on
  `code` the two would share one symbol and half a country would be dated from
  the other's legend. `scripts/geomaps/render_gpkg.py` matches on `key` too.
* **The commodity columns are the union over every sheet**, so `"w_gold" IS NOT
  NULL` answers across the whole area rather than per file.
* **Staleness is a stamp of the input SET** (`geology.gpkg.stamp`: sheet, mtime,
  size), not one mtime. Adding a sheet whose units file is *older* than the
  package — a restore, a copy that preserved timestamps — would otherwise leave
  the old file looking fresh, and the user downloads a country short of what
  the map draws. Same shape as the no-op-reading-as-an-answer rule, applied to
  a set of inputs. A sheet contributing zero features **fails the build**
  rather than shipping a package missing a country.

**The MBTiles is the picture; this is the data.** Tiles are simplified per
zoom, coalesced across neighbours and carry no typing — right for an offline
viewer, useless for intersecting the units with a concession boundary or asking
how many km² of gold-hosting rock sit inside a park. This is the source
polygons, whole, one MultiPolygon per class (the vectorizer's own shape;
exploding to parts here would invent a feature count the source does not have).

* **`"w_gold" IS NOT NULL` is why it exists.** Every commodity the sheet
  mentions gets its own INTEGER column `w_<commodity>` holding the 1–3 weight,
  so the headline question is an exact filter and a graduated renderer works on
  it. A comma-joined `commodities` string would make it a `LIKE`. The column
  set is **derived from this build**, never a fixed list — a merge changes the
  union of affinities, and a hardcoded set would then either lie or drop one.
  The readable list stays in `commodities`, the reasons in `affinity_note`,
  each prefixed with the member code it came from.
* **A unit with no affinity is NULL, not 0.** 0 reads as "measured, none" and
  matches `>= 0`.
* `area_km2` REAL, `merged` BOOLEAN, `sheet_year` INTEGER — the declared type is
  the contract (`docs/GEOPACKAGE_EXPORT.md`); a number arriving as text cannot
  be summed or graduated.
* **Styles alone are not enough**, so the file embeds a QGIS project: without
  it a 46-class country opens as one random pastel and the legend has to be
  rebuilt by hand. The project references its container as
  `./<basename>.gpkg`, so the on-disk name and the download name are the same
  string.
* Categorized on `key`, whose code half is the **merged** code (`GC2/GO`)
  exactly as the tiles and the UI carry it. The sheet does not say which member
  a patch is, so the export must not pick one.
* Built on first request from every `<sheet>_units.geojson` present and cached
  beside them (gitignored) with a `.stamp` naming those inputs. If the units are
  gone but a package survives, it is served — it is a snapshot of a real build,
  and refusing it is a worse answer than an old one.
* **No job queue**, deliberately, unlike the per-area export in
  `gpkg_jobs.go`: that one is minutes over a live database and needs a card;
  this is one static file. The panel's link does say "Preparing…" though — two
  silent seconds after a click read as a dead link.
* Rendered and looked at, not just `ogrinfo`'d — see the section below. Until
  2026-08-12 the only thing testing the cartography was a byte-level Go test,
  which asserted the XML we *wrote* and could not notice that QGIS ignored it.
  Three of the nine ornaments were wrong.

## What the GeoPackage actually looks like in QGIS (rendered 2026-08-12)

```bash
sudo apt-get install -y python3-qgis                     # 3.34, big download
# The export is built on first request and cached beside the *_units.geojson,
# so ask the server for it rather than calling the builder directly:
curl -s "localhost:8000/api/geomap/geopackage?pwd=test2026" -o /dev/null
QT_QPA_PLATFORM=offscreen python3 scripts/geomaps/render_gpkg.py  # ~60 s
# -> /tmp/geomap_render/{car,sudan}_{full,zoom_*,swatches}.png + combined_full.png
```

There is one file now, so a sheet argument selects a **view** of it (a subset
string on `sheet`), not another datasource — and the subset is cleared again
afterwards, or the next sheet renders through the previous filter and the
combined extent comes out one country short.

`render_gpkg.py` opens the file through **its own embedded QGIS project**
(`geopackage:<abspath>?projectName=<name>` — the name is required and the path
must be absolute, or `read()` returns False and then hangs) and renders with
`QgsMapSettings` + `QgsMapRendererParallelJob`. It deliberately does **not**
apply a style of its own: a renderer that wrote its own QML would have the same
blind spot as the byte-level test, one level up. The `_swatches.png` draws one
representative symbol per lithology at 240 px — the map views cannot answer
"are the nine families distinguishable" because at 1:1.5M a 2 mm hatch spacing
is sub-pixel.

**What the first render showed. Three of nine ornaments were wrong, and the
byte-level test passed the whole time.**

| family | intended | what QGIS drew |
|---|---|---|
| carbonate | brick courses | **flat horizontal rules** — the vertical course rendered **zero pixels** |
| intrusive | field of plus-signs | a sparse, uneven diagonal mesh |
| volcanic | field of "v"s | a dashed 45° hatch, indistinguishable from `mixed` |
| the other six | — | correct |

Contacts *were* drawn in the darkened ink as intended, and nothing was solid
that should have been patterned. Colour = age was right on both sheets.

### The trap: QGIS clips a custom dash to its pattern tile

`use_custom_dash` on the LINE layer (the trap already documented) was set, and
set correctly, and **it still did nothing**. QGIS renders a `LinePatternFill`
by building a small repeating tile and stamping it. The tile is only about as
long as the line spacing, so a dash whose period is much longer than the
spacing gets clipped — to a solid rule, or to nothing at all.

Measured on 3.34.4, dash period as a multiple of line spacing:

| ratio | result |
|---|---|
| 1.00 – 1.50 | correct dash, at every angle |
| 1.76 and above | **blank or solid** |

The carbonate brick's vertical course was `2;6` at 3.6 mm = **2.22x**, which
rendered 0 px. So "bricks" shipped as plain horizontal rules and read as
mudrock. `TestGeoOrnamentDashesSurviveTheQGISPatternTile` now fails on any
ratio above 1.5x, and it was confirmed to fail on the old carbonate values.

Axis-aligned angles (0/90/180/270) are the worst case, but off-axis is not
safe: 45°/135° clipped too, which is what made the intrusive cross-hatch
sparse. The bound is applied at every angle — a per-angle exception is a rule
nobody will remember.

### Where FGDC wants a shape, use a marker, not two hatches

intrusive (plus-signs) and volcanic ("v") were pairs of coarsely dashed
hatches whose dashes had to line up to make a shape, which QGIS's tile does not
guarantee. They are `PointPatternFill` markers now — a `cross` and a filled
`triangle`. A marker is what the shape actually *is*, and it is not subject to
the dash tile at all. Two things that only a render tells you:

* **A stroke-only marker (cross/plus) needs `outline_style=solid`.** It has no
  interior, so a fill colour alone leaves it invisible.
* **Size, not just spacing, decides legibility.** The first attempt used
  0.9 mm markers at 2.8 mm; at 96 dpi that is a smudged dot indistinguishable
  from the alluvium stipple. 1.7 mm at 3.4 mm is where the arms of the plus
  became readable.

**After the fix, re-rendered: all nine families are visibly distinct**,
carbonate is a real brick, the ultramafic cross-hatch is complete (both
diagonals, solid — solid lines are the one case the tile always gets right),
and at map scale the ornament is legible without burying the age colour.
Measured ink coverage, which `TestGeoOrnamentFamiliesAreDistinct` records
because it cannot be computed from the table: sandstone 5%, mixed 6%,
volcanic 8%, alluvium 9%, ironstone 12%, metamorphic 15%, mudrock 17%,
intrusive 18%, carbonate 26%, ultramafic 33%. `mixed` stays near the quiet end
on purpose — it means "the sheet does not say" and must not out-shout a family
that does say something.

PNGs are not committed. Re-run the command above.

## The vocabulary is the silent failure mode, so it reports itself

`geoAgeOf` / `geoLithOf` in `srv/geomap_std.go` are **first-match string scans
over the words the two sheets happen to print** — English (GRAS 2004) and
French (BRGM 1964). That is fine for two sheets and a liability for a third.

A third sheet does not error. Its classes come back age `unknown` (a flat grey
polygon, legend "Age not stated") and lithology `mixed` (the generic sparse
hatch) — which is **exactly** how the map deliberately renders a unit that is
genuinely undated and genuinely undifferentiated. A missing rule and a
cartographic statement are the same pixels. Nobody gets a log line. This is the
recurring shape in this codebase: **a no-op that reads as an answer.**

So the gap is loud:

* **`geoVocabAudit(sheet, blob)`** walks a catalogue and returns the strings
  that produced no rule. The **raw group/name string is the deliverable** — it
  is precisely what a maintainer pastes into `geoAgeRules`. A count alone sends
  them back to the printed sheet.
* **`TestShippedCataloguesHaveNoUnmappedVocabulary`** audits the committed
  `data/geomaps/*_classes.json` and fails listing every unmapped string. Both
  shipped sheets pass at **0 unmapped** (46 Sudan classes, 17 CAR). It skips
  cleanly if a catalogue is absent, and fails if one parses to zero classes —
  an audit over nothing passes trivially, which is the same failure shape one
  level up.
* **`/api/geomap` ships an `unmapped` summary** beside `std`: counts plus the
  offending strings, so the admin legend can say "3 classes on this sheet have
  no age rule" instead of drawing three grey polygons. Unmapped classes are
  also flagged in place (`age_unmapped` / `lith_unmapped`), **absent rather
  than false** when healthy — 63 `false` keys would bury the two that matter.
  No UI was added; the data is simply present instead of absent.
* **It never guesses.** There is no rule inferring an age from a unit's *name*,
  and there must not be. "Age not stated" is an answer the sheets really do
  give (Sudan's `PZs` is undifferentiated Palaeozoic because the survey could
  not date it). Papering over a missing rule with a plausible age destroys the
  only distinction that matters: **what the sheet does not say vs. what we have
  not taught it to read.** `geoLithResolve` returns that bit explicitly, so
  `mixed`-as-an-answer and `mixed`-as-a-gap stay separable.

### Two dead rules the audit found immediately

A rule sitting below a needle that contains it **can never fire**, and it looks
exactly like a decision that has been made. `TestGeoRuleOrderHasNoDeadRules`
now fails on that shape. Two were already in the list:

* **`{"greenschist", "volcanic"}` sat below `{"schist", "metamorphic"}`.** Real
  bug: Sudan's `MSv`, "Volcano-sedimentary greenschist assemblage", is a
  metavolcanic pile and was drawn with the metamorphic wavy dash.
* **`{"cambro-ordovician"}` sat below `{"ordovician"}`.** Harmless only because
  both answers agreed — invisible, and one edit from mattering.

### Hyphenated spans are curated, not merged

"Cambro-Ordovician" is **not** a merged class: the sheet is not declining to
say which unit a patch is, it is saying one unit straddles a boundary. So
`age_mixed` ("the map does not say") is the wrong flag and oldest-wins is
answering a question nobody asked. Three curated lines, each above **both** of
its endpoints, each a judgement written down rather than left to list order:
`cambro-ordovician` → Ordovician (the unit's own name says glacial deposits,
i.e. the Hirnantian glaciation), `jurassic-cretaceous` → Cretaceous (Nubian
sandstone), `tertiary-quaternary` → Quaternary (Umm Ruwaba).

Any span we have *not* curated is reported as `age_ambiguous`: a third sheet's
"Silurian-Devonian" would resolve to whichever rule sits higher, and rule order
is not a decision.

## Share a link to the sheet's panel entry

`?panel=admin&admin_tab=map-settings&map_sheet=car` opens Map Settings and
flashes that sheet's card; `map_sheet=histmap` does the same for the historical
archive. Both cards carry a **Copy link** button (`copyMapSheetLink()`), which
builds on top of the ordinary share URL so the map underneath is still the view
being discussed.

`?geomap=` and `?map_sheet=` answer different questions and compose: the first
puts the overlay on the map, the second points at where its downloads, class
list and provenance live — which is usually what someone is being sent ("grab
the CAR geology as a GeoPackage"). `highlightMapSheet()` polls for the card
because the tab renders from a fetch, and **gives up after 10 s**: a link naming
a sheet this server does not have must not spin forever.

## Done, and what a future change would look like

The overlay is **finished**: both sheets vectorized, served, rendered in one
industry legend, downloadable as a picture (MBTiles per sheet) and as data (one
GeoPackage), with the panel simplified to one switch, one legend, one adaptive
opacity and an Advanced block. Nothing here is waiting on anything.

Two things that would be *additions* rather than unfinished work:

* **A commodity legend outside the admin panel.** The only way in is admin ▸ Map
  Settings. If "what could be under this park" becomes a user-facing question
  rather than an analyst's, it wants a place in the filter panel too.
* **More sheets.** `sheets.py` is the whole per-sheet contract; a third sheet is
  a legend measurement plus a graticule fit. Everything downstream is derived
  from the sheet list — the class catalogue, the GeoPackage's commodity columns,
  its input stamp, the panel's counts — so adding one should need no edit
  anywhere else. If it does, that is the bug.

## Traps already paid for

* **A perfect hold-out with a hole in the map.** See above — measure at the
  window size, and read the *claim rate*.
* `str.replace()` with an empty needle splices a module between every character
  of itself. Use the patch tool.
* Oversized temp PNGs killed an earlier conversation with HTTP 413. Crop small,
  downscale, read once.
* Zenodo's Sudan TIFF carries a rough affine (units "fathom", skew term),
  ±30 px. Seed only — every shipped coordinate comes from the printed
  graticule via `gridfit.py`.
* NLA is behind an Anubis proof-of-work: browser tool → `network_cookies` →
  `curl -b "<cookies>" -C - .../m`. The `image?wid=` endpoint caps at 5000 px.
* Long runs go in tmux; python buffers, so the log is empty until it finishes.
