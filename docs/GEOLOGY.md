# Geology overlays

Three geological maps as **vector** overlays: Sudan (GRAS 2004, 1:2M), CAR
(BRGM 1964, 1:1.5M) and Tanzania (GST/GMIS 2015, 1:1.5M). A park or AOI facing
a gold rush should be able to switch on every unit relevant to gold in one
click, and still hide or isolate individual units and set opacity.

The first two are **scans** we vectorized: everything outside the mapped
country — paper, collar, legend boxes, neighbouring-country fill, insets — is
dropped. The third is **already vector**, served by the survey's own WFS, so
most of this document (halftone screens, hold-outs, claim rates, merged
classes) does not apply to it. The two paths are marked throughout.

Not to be confused with the **raster** `?histmap=` overlay (Sudan Survey
1:250k topographic sheets, `scripts/histmaps/`, `srv/histmap.go`). Different
data, different serving path; the georeferencing discipline and the
204-on-miss / `?v=<rev>` conventions are shared.

## State

| Stage | Sudan | CAR | Tanzania |
|---|---|---|---|
| source | scan, georeferenced | scan, georeferenced | **WFS, already vector** |
| georeference | done, 29 GCPs, rms 1.01 px / 260 m | done, 42 GCPs, rms 5.17 px / 330 m | n/a — the server reprojects |
| legend extracted | done, 53 units | done, 20 units | n/a — the layer carries `leg_id` |
| vectorized | done, **46 classes**, 34 MB GeoJSON | done, **17 classes**, 6.9 MB | fetched, **41 classes** / 596 polygons, 5.6 MB |
| window hold-out | claim 0.998, accuracy 1.000 | claim 0.998, accuracy 1.000 | n/a — no classifier |
| unclaimed inside cutline | 9.0% | 8.7% | n/a |
| area claimed | 2.28M km² of ~2.5M | 564k km² of ~623k | 887k km² vs ~886k km² of land — see below |
| tiles | `data/geomaps/sudan.mbtiles` 9.8 MB, z0–10 | `car.mbtiles` 3.1 MB, z0–10 | `tanzania.mbtiles` 2.2 MB, z0–10 |
| server | done — `srv/geomap.go` | same | same |
| UI | done — admin ▸ Map Settings ▸ Geology | same | same |

Live: `/?pwd=test2026&geomap=car&lat=8&lng=22&z=6`

### Tanzania's area, stated honestly

The two scans report **area claimed** — how much of the country the classifier
dared label, where the shortfall is the classifier's. Tanzania has no
classifier, so the number means something else and is not comparable:

| | km² |
|---|---|
| mapped geology (596 polygons, what we ship) | **887,107** |
| the layer's own water polygons (25, dropped) | 44,851 |
| the layer's unattributed polygons (2, dropped) | 25 |
| — the layer, in total | 931,983 |
| Tanzania's land area (CIA/UN) | ~885,800 |
| Tanzania's total area (CIA/UN) | ~947,300 |

So the geology matches the country's **land** area to within 0.2%, which is the
check that matters. The layer as a whole is ~15,000 km² short of the total
because its own water polygons (44,851 km²) do not add up to Tanzania's inland
water (~61,500 km²) — **the sheet does not map every lake.** That is a fact
about the sheet, not a bug in the fetch, and `quality.area_note` says so in the
catalogue rather than leaving the totals looking like an error. Every dropped
feature is accounted for by count *and* area under `quality.dropped`, keyed by
the survey's own reason; the two unattributed polygons are recorded as "not
attributed" rather than folded into the water count, because guessing what we
threw away is worth less than saying the sheet does not say.

## The one thing to understand **about the two scans**

(Tanzania is vector; skip to "Adding a sheet" for that path.)

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
| `scripts/geomaps/sheets.py` | the two **scanned** sheets: provenance, graticule, seed affine |
| `scripts/geomaps/gridfit.py` | finds the printed graticule intersections |
| `scripts/geomaps/georef.py` | GCPs → TPS warp → `work/<id>_geo.tif`, cut to country |
| `scripts/geomaps/legend.py` | the printed legend, measured off the scan; `AFFINITY` |
| `scripts/geomaps/vectorize.py` | classifier → `<id>_units.geojson` + `<id>_classes.json` |
| `scripts/geomaps/gmis_tanzania.py` | **WFS path**: fetch → the same two files, no classifier |
| `scripts/geomaps/tiles.sh` | tippecanoe → `<id>.mbtiles` (z0–10), whichever built them |
| `srv/geomap.go` | `/api/geomap`, tile route, download |
| `srv/geomap_std.go` | the shared legend: ICS age + FGDC lithology, and the vocabulary audit |
| `srv/static/geomap.js` | `window.GeoMap` — layers, isolation, share links |
| `srv/templates/globe.html` | `renderGeoMapPanel()` / `geoMapSheetHTML()` |
| `data/geomaps/legend_{sudan,car}.json` | **committed** — measured input (scans only) |
| `data/geomaps/{sudan,car,tanzania}_classes.json` | **committed** — the class catalogue |
| `data/geomaps/*_units.geojson`, `*.mbtiles` | gitignored derived output |
| `data/geomaps/{src,work}/` | gitignored, ~1.9 GB |

## Is the model any good? Measure it.

`scripts/geomaps/eval_affinity.py` scores the commodity chooser and the
Junctions tab against occurrence datasets and writes
`data/eval/geo_affinity_car.json`; `srv/geomap_scores.go` quotes the numbers and
the UI prints them beside every grade.

```bash
python3 scripts/geomaps/eval_affinity.py                 # CAR/IPIS + USGS sheets, ~2 min
python3 scripts/geomaps/eval_affinity.py --continental    # + JRC AKP layers vs IPIS DRC
go test ./srv/ -run TestAffinity                          # the table may not disagree with the file
```

Headline: on CAR the gold **junctions** hold 2.32× more of the known workings
than the same amount of ground picked at random (and 2.33× against diamond
workings, so it is gold the lines find), while the gold **units** score 0.63× —
worse than random ground. Full discussion, the three baseline disciplines, the
survey of other continental sources, and the six unread Tanzania columns:
`docs/agents/overlays.md` § "The model has a score now".

**The score is not a tuning target** (same rule as the fire trajectories eval).

## Adding a sheet — the whole contract

**Read this first; it is the shortest path and it is the same for a scan and
for a web service.** Everything downstream is derived from the sheet list, so
adding a sheet is: produce two files, register the id, teach the vocabulary any
words it does not know, build tiles. If it takes an edit anywhere else, that is
a bug.

### The two files, and the only schema that matters

Whatever the source, a sheet is **exactly two files** in `data/geomaps/`:

* `<sheet>_units.geojson` — gitignored. Polygons in **EPSG:4326**, each with a
  `properties` block the GeoPackage builder reads: `sheet, code, codes, name,
  group, color, merged, commodities, affinity[], area_km2`, plus optional
  `lithology` (see below). `area_km2` is **per polygon**, never the class
  total — the export sums the column, and a class total repeated on 89
  alluvium polygons multiplies the country by 89.
* `<sheet>_classes.json` — **committed**. `sheet, title, short, year,
  publisher, scale, source_url, countries[], n_classes, n_units, quality{},
  groups[], commodities{}, classes[]`, where each class repeats the same
  per-unit fields. This is the catalogue the API serves and the UI renders
  from; it is small on purpose.

The tiles carry **only `code`**. Everything else is joined from the catalogue
at render time, which is why a legend or affinity change never invalidates a
tile.

One polygon per class or many is **your source's business, not a contract**:
the vectorizer dissolves each class to one multipart feature, the WFS sheet
ships the survey's own 596 polygons, and both work. Anything that must be
per-class (the QGIS legend) deduplicates on `(sheet, code)`.

### The steps

```bash
# 1. produce the two files
python3 scripts/geomaps/vectorize.py <sheet>        # a scan
python3 scripts/geomaps/gmis_tanzania.py            # a WFS; write one per source

# 2. register the id
#    srv/geomap.go: geoMapSheets = []string{..., "<sheet>"}
#    scripts/geomaps/tiles.sh: the default list at the bottom

# 3. the vocabulary must be clean BEFORE you look at the map
go test ./srv/ -run TestShippedCataloguesHaveNoUnmappedVocabulary -v
#    it prints every unmapped string; each is a line to add to geoAgeRules or
#    geoLithRules in srv/geomap_std.go. Do not relax the test.

# 4. tiles, then restart
scripts/geomaps/tiles.sh <sheet>
make build && sudo systemctl restart 5mp

# 5. verify from the API, not by eye
curl -s "localhost:8000/api/geomap?pwd=$PWD_TOKEN" | jq '.sheets[]|{sheet,available,unmapped:.catalogue.unmapped}'
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' \
  "localhost:8000/api/geomap/<sheet>/8/152/129.pbf?pwd=$PWD_TOKEN"   # 200 + bytes, not 204
curl -s "localhost:8000/api/geomap/geopackage?pwd=$PWD_TOKEN" -o /tmp/g.gpkg
sqlite3 /tmp/g.gpkg "select sheet,count(*),round(sum(area_km2)) from geology_units group by sheet"
```

The GeoPackage cache invalidates itself from the input **set**, so step 5 needs
no `rm` — but check the row count anyway: that sum against the country's real
area is the one number that catches a half-downloaded sheet.

**Compare it to a figure from outside the sheet, and report what you find even
if it disagrees.** A geology layer's coverage is not the country's area and has
no obligation to match it: lakes may be excluded, a survey may not map its own
border region, an offshore strip may be included. Tanzania's geology matches
the **land** area to 0.2% while the layer as a whole falls ~15,000 km² short of
the total, because the sheet does not map every lake. Write that down
(`quality.area_note`) rather than picking whichever comparison flatters the
build — an unexplained 5% is how a half-downloaded sheet hides.

### If the source is a web service (WFS/WMS) — the four traps

`scripts/geomaps/gmis_tanzania.py` is the worked example; copy its shape.

1. **A short download looks exactly like a small layer.** Ask the server
   `resultType=hits` FIRST and treat `numberMatched` as the truth; page with
   `count`/`startIndex`; abort without writing anything if the totals disagree.
   An unpaged request silently truncated at an unknown `maxFeatures` returns
   valid JSON. `scripts/histmaps/` shipped half a country this way once.
2. **`srsName=EPSG:4326` may hand back lat/lon.** WFS 1.0.0 does, by spec.
   Tanzania then lands in the Indian Ocean and still renders as a plausible
   map. **Assert the envelope against the country** (the server's own
   `LatLonBoundingBox` is a good source for it) and stop on failure — never
   clip, never swap the numbers by hand: a national grid is usually a different
   *datum* too (Tanzania's is Arc 1960, ~200 m off WGS84), so if you must
   reproject, ogr2ogr does it and you do not.
3. **Take the publisher's own ink; do not invent one.** `WMS
   request=GetStyles` returns the SLD with a fill per legend id. A class with
   no rule gets **no `color` field at all** rather than a plausible grey —
   `color` means "this is how the survey prints it", and the map does not
   depend on it (screen colour is ICS age).
4. **Not every feature is rock.** The GST layer carries 26 water polygons and
   one unattributed one. Drop them — and record the count **and the area** per
   reason in `quality.dropped`, using the survey's own word where it has one.
   A bare count invites the reader to assume the remainder was negligible;
   44,851 km² of lake is not. Dropping them silently is the failure shape this
   codebase keeps paying for.

Also: record what the licence page actually says, including when there is none.
The GMIS capabilities state Fees NONE / AccessConstraints NONE and the site has
no terms page, so the catalogue attributes in full and links the portal rather
than claiming a licence nobody granted.

### The `lithology` hint, and why it is read LAST

A vector sheet usually ships a rock-description column. It is offered to
`geoLithResolveHint` **after** the unit's name and group, and that order is
load-bearing: the column lists every constituent in no particular order, so a
first-match scan over it calls the Mbozi syenite-gabbro ring complex
`ultramafic` off the word "pyroxenite". The **name** is the survey's own
summary of what the rock is. The column only rescues names that are pure
geography ("Mafic complex Nyabuyonza" + "Gabbroic rocks" → intrusive).

A scanned sheet has no such column and passes `""`; nothing changes for it.

### Ages: use the sheet's own words, and its own numbers

`group` is what `geoAgeOf` reads. Prefer the survey's chronostratigraphy
verbatim — **including its typos** (the GST prints "Cretacous" and
"Neoprozerozoic"; the catalogue records what the sheet says and
`geoAgeRules` learns both). Two derivations are legitimate and both are
documented in the fetch script:

* **Strip parenthetical sub-era codes** from the group string
  (`"Neoarchaean (NA) - Neoproterozoic (NP1)"` → `"Neoarchaean -
  Neoproterozoic"`). The scan matches substrings, and an interpolated `(NA)`
  breaks the curated span term in half so the answer falls back to rule order.
  The verbatim string stays in the class as `chronostrat`.
* **Read a bare Ma span through the ICS chart** when the sheet gives numbers
  and no words (`"23 - 0 Ma"` → `"Neogene - Quaternary"`). That is a lookup in
  the same chart `geomap_std.go` encodes, not a guess — the sheet *has* stated
  an age. Keep `age_strat` beside it so the derivation is checkable, and allow
  a small tolerance or `"2.6 - 0 Ma"` claims 0.02 Ma of Neogene and alluvium
  prints as a span.

Every hyphenated span needs a **curated rule above both of its endpoints**, or
rule order decides. `geoVocabAudit` reports any span you have not curated as
`age_ambiguous` — that is not a nag, it is the only thing standing between you
and a coin toss wearing a decision's face.

**If you derive `group`, keep the verbatim string.** The tip prints an "as
printed:" line, and it must be the survey's words — `chronostrat`, else
`age_strat`, else `group`. A derived value under that label is a false claim
about provenance, and it is invisible: it reads as the sheet being clearer than
it was.

### Sanity-check what the UI reads, not just what the tests pass

The panel and the stats-panel Map strip (`srv/static/maplegend.js`) render from
`age`, `ics_color` and `lith` on every class, all three added server-side by
`geoMapStandardise`. They are derived, so a new sheet appears in both by itself
— but only if the vocabulary covers it:

```bash
curl -s "localhost:8000/api/geomap?pwd=$PWD_TOKEN" | python3 -c '
import json,sys
for s in json.load(sys.stdin)["sheets"]:
    c=s["catalogue"]; u=c["unmapped"]
    bad=[x["code"] for x in c["classes"]
         if x.get("age","unknown")=="unknown" or not x.get("ics_color") or not x.get("lith")]
    print(c["sheet"], s["available"], len(c["classes"]), u["age"], u["lithology"],
          u["age_ambiguous"], bad or "ok")'
```

A class with `age: "unknown"` or a missing `lith` is a **vocabulary gap to fix
in `geomap_std.go`**, never something to default in the client: the strip would
draw it grey with the generic hatch, which is how the map deliberately says
"genuinely undated, genuinely undifferentiated".

### What a new sheet must NOT bring

* **No mineral occurrences, no licences, no concessions.** See
  `docs/agents/mining.md`. Most national portals serve them next to the
  geology; affinity here is an inference over lithology with an honest `why`,
  and a specific deposit stated as located data is the line that does not get
  crossed.
* **No invented colours, no invented ages, no guessed lithology.** "Age not
  stated" is an answer real sheets give.
* **No hardcoded count** describing the sheet anywhere in code — the class
  list, the commodity columns and the stamp are all derived.

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

`AFFINITY` is keyed by **`(sheet, code)`, not `code`**. The sheets reuse
letters for unrelated things — `S` is Silurian sandstone on Sudan and a
gold-bearing schist belt on CAR. It lives in `legend.py` for the scanned
sheets and in the fetch script for a WFS sheet (`gmis_tanzania.AFFINITY`),
because it is written against that sheet's own unit codes and there is nothing
to gain by moving it away from them.

It is an **inference over lithology** ("rocks of this kind host X"), never an
occurrence dataset, and **must be labelled as such wherever it surfaces** —
the panel, the click popup and any future report line all say so. Compare the
mining verdict in `AGENTS.md`: inference from context ships, fabricated
evidence does not. Nothing here counts, ranks or locates a deposit.
`weight` is 1–3 (3 = classic host).

Where a `why` names something — the Nyanzian greenstones, the Kabanga-Musongati
layered intrusion, the Karoo Supergroup — it is naming the **rock unit the
survey itself names**, not a discovery. That distinction is the whole licence
for this feature to exist.

Present commodities: Sudan — cobalt 4, copper 4, gold 14, iron 2, lithium 2,
rare_earth 1, uranium 11. CAR — cobalt 3, copper 2, diamond 6, gold 7,
lithium 3, rare_earth 1, uranium 3. Tanzania — coal, cobalt, copper, diamond,
gemstone, gold, graphite, iron, lithium, rare_earth, uranium (**coal, graphite,
gemstone are new keys**; the panel builds its chips from the catalogue, so they
needed no frontend change).

## Vectorizer

```bash
python3 scripts/geomaps/vectorize.py sudan                 # ~5 min
python3 scripts/geomaps/vectorize.py car                   # ~4 min, peak 3.5 GB
python3 scripts/geomaps/vectorize.py sudan --repolygonize  # ~30 s, reuses the label raster
scripts/geomaps/tiles.sh                                   # every sheet, ~50 s
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
* Click a unit → popup with code, name, the survey's own rock description
  (where the sheet has one), the derived age and lithology, the merged-units
  warning, and the affinity list with its disclaimer. **Click, not hover**: the
  overlay covers the whole country and a hover handler would fight every other
  tip.
* **"As printed:" must be the sheet's own words.** It shows `chronostrat`
  verbatim, else the sheet's `age_strat`, else `group`. `group` was the whole
  answer while both sheets were scans, where it *is* the printed string — on a
  vector sheet it is derived, so the label was attributing our reading to the
  survey. Tanzania's `cNP` prints "Neoproterozoic (NP2-3) - Cambrian(?)" and
  our group drops both the codes and a question mark the survey meant; its
  Cenozoic units print no age word at all, only "2.6 - 0 Ma". The derived
  period still shows, above, as our legend rather than as theirs.
* Share links: `?geomap=car` is the common case. `geomap_only=`, `geomap_hide=`,
  `geomap_color=`, `geomap_pattern=`, `geomap_lith=`, `geomap_age_off=`
  (periods hidden from the key — carried as the EXCLUSION, so a link that hides
  nothing carries nothing), `geomap_host_min=` (the affinity floor, 2 or 3;
  absent = 1 = any host), `geomap_opacity=` and `geomap_adv=` (the Advanced disclosure — a panel setting, so it travels even
  with the layer off) only appear once changed, so a plain link does not
  carry 46 codes. **`geomap_opacity` absent means auto**, not 0.42: the value
  is picked per basemap, so freezing a computed number into a link would break
  the layer for whoever opens it on the other basemap. **A code the current build no longer has is dropped**, and an
  isolation that ends up empty shows all classes plus a toast — a rebuild can
  merge two classes and thereby rename both, and rendering nothing looks
  exactly like "this sheet has no data here". `geomap_age_off` is an exclusion,
  so an unknown key there can only fail *safe* (it hides nothing) and is dropped
  silently — but a link hiding every period the build has, or a floor no unit
  meets, is refused with a toast for the same reason as an empty isolation.

## GeoPackage download

`GET /api/geomap/geopackage` → `geology.gpkg`, **one file covering every
sheet**, offered in the panel's *Data* row. `srv/geomap_gpkg.go`; measured
2026-08-12: 19 MB for Sudan + CAR + Tanzania.

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
polygons, whole, in whatever shape the source has them: one MultiPolygon per
class for a vectorized scan (exploding to parts would invent a feature count
the source does not have), the survey's own 596 polygons for Tanzania
(dissolving them would destroy one the source does have). The **legend**
deduplicates to one category per `(sheet, code)` — it did not have to before,
because on a scan the two counts coincided, and without it a vector sheet lists
one alluvium symbol 89 times.

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
over the words the sheets happen to print** — English (GRAS 2004), French
(BRGM 1964), and the GST's own English-with-typos (2015). That is fine for the
sheets we have and a liability for the next one.

A new sheet does not error. Its classes come back age `unknown` (a flat grey
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
  `data/geomaps/*_classes.json` and fails listing every unmapped string. It
  walks **`geoMapSheets`**, not a literal list, so a sheet added to the server
  cannot arrive unaudited. All three shipped sheets pass at **0 unmapped**
  (46 Sudan classes, 17 CAR, 41 Tanzania). It skips
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
answering a question nobody asked. So each span is a curated line above **both**
of its endpoints, each a judgement written down rather than left to list order:
`cambro-ordovician` → Ordovician (the unit's own name says glacial deposits,
i.e. the Hirnantian glaciation), `jurassic-cretaceous` → Cretaceous (Nubian
sandstone), `tertiary-quaternary` → Quaternary (Umm Ruwaba).

Tanzania added eight more, and they generalise the rule the first three only
hinted at. Its Precambrian spans (`neoarchaean - neoproterozoic`,
`paleoproterozoic - mesoproterozoic`, …) all resolve to their **younger**
endpoint, because for every one of them that is the orogeny the survey names as
having made the rock what it is — Neoarchaean protoliths in a Neoproterozoic
granulite belt are mapped as the belt. `uppermost carboniferous - lower
jurassic` (the Karoo) resolves to Triassic, the span's own middle, where the
bulk of the succession sits; either endpoint alone would be worse. In all cases
the tip still prints the survey's full string, so the colour summarises a span
the reader can still see whole.

Any span we have *not* curated is reported as `age_ambiguous`: a new sheet's
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

The overlay is **finished**: three sheets served, rendered in one industry
legend, downloadable as a picture (MBTiles per sheet) and as data (one
GeoPackage), with the panel simplified to one switch, one legend, one adaptive
opacity and an Advanced block. Nothing here is waiting on anything.

Two things that would be *additions* rather than unfinished work:

* **A commodity legend outside the admin panel.** The only way in is admin ▸ Map
  Settings. If "what could be under this park" becomes a user-facing question
  rather than an analyst's, it wants a place in the filter panel too.
* **More sheets.** See "Adding a sheet" above — it is the whole contract, and
  Tanzania was the proof that it holds for a source that is not a scan.
  Everything downstream is derived from the sheet list (the class catalogue, the
  GeoPackage's commodity columns, its input stamp, the panel's counts), so
  adding one needs no edit anywhere else. If it does, that is the bug.
  `gmis:minerogenictectonics` — the GST's structural lines (faults, fold axes,
  shear zones) — is deliberately **not** shipped: it is line geometry, and the
  whole overlay's contract is polygons with an age and a lithology. It would be
  a second layer with its own legend, not a fourth sheet.

## Traps already paid for

* **A perfect hold-out with a hole in the map.** See above — measure at the
  window size, and read the *claim rate*.
* **A short WFS download is valid JSON.** Cross-check against the server's own
  `numberMatched` and abort; see "Adding a sheet".
* **`srsName=EPSG:4326` is not a promise of lon/lat.** Assert the envelope
  against the country and stop, rather than swapping numbers by hand — the
  national grid is usually a different datum too.
* **A rule that fires on the sheet's own summary must sit above one that fires
  on its parts.** `meta-sediment` → metamorphic has to beat `sediment` →
  mixed, which means the *opposite* thing ("the sheet declines to say which
  sediment"); read in the wrong order, five Tanzanian belt units claimed to be
  undifferentiated while the survey was being specific.
* **A per-feature legend looks correct until the features outnumber the
  classes.** Both scans dissolve one class to one row; the first vector sheet
  put 89 identical categories in the QGIS legend.
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
