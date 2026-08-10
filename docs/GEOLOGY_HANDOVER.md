# Geology overlays — handover

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
* Share links: `?geomap=car` is the common case. `geomap_only=`, `geomap_hide=`
  and `geomap_opacity=` only appear once changed, so a plain link does not
  carry 46 codes. **A code the current build no longer has is dropped**, and an
  isolation that ends up empty shows all classes plus a toast — a rebuild can
  merge two classes and thereby rename both, and rendering nothing looks
  exactly like "this sheet has no data here".

## Next steps

1. **GeoPackage download.** The user asked for the geology as `.gpkg` from the
   admin panel, beside the MBTiles link. The `.gpkg` machinery already exists
   and is well documented (`docs/GEOPACKAGE_EXPORT.md`, `srv/gpkg*.go`): typed
   columns, QGIS styling, an embedded project that ships sensible layer
   visibility. The honest shape here is **one layer per sheet, styled with the
   printed ink colours**, plus the `codes`/`commodities` columns so a QGIS user
   can filter by commodity themselves. Read the "declared column type is the
   contract" and "styles alone are not enough" notes there first. It is a
   static file per sheet, not a per-area job — so it can be built by
   `tiles.sh`'s sibling and served straight, no job queue.
2. **A commodity legend outside the admin panel.** Right now the only way in is
   admin ▸ Map Settings. If this is meant to be a user-facing question ("what
   could be under this park"), it wants a place in the filter panel too.
3. **More sheets.** `sheets.py` is the whole per-sheet contract; a third sheet
   is a legend measurement plus a graticule fit.
4. Nothing else is blocking.

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
