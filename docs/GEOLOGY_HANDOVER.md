# Geology overlays — handover

Two scanned geological maps turned into **vector** overlays: Sudan (GRAS 2004,
1:2M) and CAR (BRGM 1964, 1:1.5M). A park or AOI facing a gold rush should be
able to switch on every unit relevant to gold in one click, and still hide or
isolate individual units and set opacity. Everything outside the mapped country
— paper, collar, legend boxes, neighbouring-country fill, insets — is dropped.

Not to be confused with the **raster** `?histmap=` overlay (Sudan Survey
1:250k topographic sheets, `scripts/histmaps/`, `srv/histmap.go`). Different
data, different serving path; only the georeferencing discipline is shared.

## State

| Stage | Sudan | CAR |
|---|---|---|
| georeference | done, 29 GCPs, rms 1.01 px / 260 m | done, 42 GCPs, rms 5.17 px / 330 m |
| legend extracted | done, 53 units | done, 20 units |
| held-out accuracy | **0.962** | **0.950** |
| vectorized | done, 51 units, 39 MB GeoJSON | **running** (`tmux geo`, `/tmp/geo3.log`) |
| tiles / server / UI | not started | not started |

Commits: `004b535` georeferencing (WIP) · `27d781a` legend · `e9afe6b` vectorizer.

## The one thing to understand

**Neither sheet is printed in flat ink. Both are halftone screens**, and the
legend colour is only the screen's average. This is not a detail, it decides
the design:

* Averaging destroys the signal. 12 groups of Sudan units and 5 of CAR average
  to *identical* RGB (`Legend.merge_groups()`). Nearest-legend-colour is capped
  around 0.77 on Sudan **by construction** — the sheet separates e.g. QE from
  QD by position in the legend column, information the map body does not carry.
* But the screens differ where the averages do not: TA and TC are the same
  yellow at different dot densities, MSq and TQ differ in ruling.

So a pixel is classified by the **distribution of palette indices in a window
around it**, matched against the same distribution measured over the legend
swatch (Bhattacharyya, implemented as box filters + one argmax). That is what
gets 0.96 / 0.95 instead of 0.77.

If you change anything here, re-run the hold-out — it trains on one half of
each legend swatch and tests on the other:

```bash
python3 scripts/geomaps/vectorize.py sudan --holdout   # 0.962, ~10 s
python3 scripts/geomaps/vectorize.py car   --holdout   # 0.950, ~26 s
```

The only residual confusions are `MSq/TQ`, `PLq/TA` (Sudan) and `GO/GC2` (CAR)
— all pairs `merge_groups()` already flags as the same ink. That is the floor,
not a bug.

## Files

| file | role |
|---|---|
| `scripts/geomaps/sheets.py` | the two sheets: provenance, graticule, seed affine |
| `scripts/geomaps/gridfit.py` | finds the printed graticule intersections |
| `scripts/geomaps/georef.py` | GCPs → TPS warp → `work/<id>_geo.tif`, cut to country |
| `scripts/geomaps/legend.py` | the printed legend, measured off the scan |
| `scripts/geomaps/vectorize.py` | screen-histogram classifier → `<id>_units.geojson` |
| `data/geomaps/legend_{sudan,car}.json` | **committed** — measured input, not derived |
| `data/geomaps/*_units.geojson` | gitignored derived output (39 MB Sudan) |
| `data/geomaps/{src,work}/` | gitignored, ~1.9 GB |

## Legend (`legend.py`)

Vectorizing is colour-quantization, so **the legend is the class list** and a
wrong swatch colour silently relabels a whole formation. Every colour is
sampled from the scan at a recorded pixel box in **source-TIFF coordinates**;
`--verify` re-samples and exits 1 on drift. None was picked by eye.

```bash
python3 scripts/geomaps/legend.py sudan --contact /tmp/leg.png   # rebuild + QA sheet
python3 scripts/geomaps/legend.py sudan --verify                 # 0/53 drifted
```

Three things the sampling has to survive — each broke a naive version:

1. **Halftone.** A raw median inside a swatch is pulled toward the paper by the
   white between dots. `sample_swatch` medians a *median-blurred* copy.
2. **The code is printed on the swatch.** Pixels darker than their local
   background by `INK_DELTA` are dropped, or every swatch reads a few percent
   dark and pale ones read grey.
3. **Sudan's colour column has no rules between bands** — it is one continuous
   strip. Its 26 Phanerozoic boxes are a *fitted 43 px pitch from y=70*, not
   detected rectangles. The Pan-African blocks below are subdivided diagonally
   (MS/MV/OP) or side-by-side (IY/IU/IO) and are hand-placed.

Two API calls the vectorizer must respect:

* `merge_groups()` — codes whose inks are not separable. Emit the merged class
  labelled with every member; never guess which one.
* `paper_like()` — codes too close to the paper tone to find by colour at all
  (CAR's migmatite `M`, its recent alluvium `a2`). Must be resolved by
  exclusion inside the cutline, or the whole unmapped margin becomes migmatite.

## Commodity affinity

`AFFINITY` is keyed by **`(sheet, code)`, not `code`**. The sheets reuse
letters for unrelated things — `S` is Silurian sandstone on Sudan and a
gold-bearing schist belt on CAR; `C` is Cambrian molasse vs charnockite. A
code-only table gave CAR's schist the uranium affinity of a Sudanese sandstone.

It is an **inference over lithology** ("rocks of this kind host X"), never an
occurrence dataset, and must be labelled as such wherever it surfaces. Compare
the mining verdict in `AGENTS.md`: inference from context ships, fabricated
evidence does not. Nothing here counts, ranks or locates a deposit.
`weight` is 1–3 (3 = classic host). `Legend.commodity_index()` returns
`commodity -> [(code, weight)]`, which is what the grouped UI toggle wants.

## Vectorizer (`vectorize.py`)

```bash
python3 scripts/geomaps/vectorize.py sudan     # ~13 min end to end
python3 scripts/geomaps/vectorize.py car       # much longer, 406 Mpx
```

Pipeline: read scan → palette → index image → per-swatch signatures →
windowed classify → label raster (scan space) → TPS warp → polygonize → clip.

Load-bearing decisions:

* **Classification runs on the SOURCE raster, never the warped one.** The warp
  is TPS with `-r near`, which duplicates and drops rows. Invisible in a colour
  and *fatal* in a screen, since dot density is exactly what is measured. We
  classify in scan space and warp the **label raster**, which is the one thing
  `-r near` is unarguably correct for. The GCPs are reused with x/y divided by
  the stride.
* **Sudan's scan contains exactly 64 distinct colours** — it is already
  posterised, so that *is* the printer's palette and the index image is exact,
  no clustering. CAR is continuous-tone (583k colours in one window) → 32-centre
  k-means **fitted over the legend swatches only**. A global fit spends its
  clusters on paper and the two commonest fills and leaves rare units sharing a
  centre.
* **Ambiguous pixels are dropped, not guessed** (`--min-margin`, default 0.02).
  On a screened sheet the ambiguous pixels are mostly line work and lettering,
  where a confident wrong label draws a hairline of some unrelated formation
  along every contact. Sudan leaves 10.2% unclaimed — that is healthy.
* **`--stride` costs nothing in accuracy** (window is 17–33 px, far wider) and
  turns a 400 Mpx problem into 25 Mpx. Defaults keep ground resolution near
  500 m, finer than either sheet's own line work.
* Sieve and simplify happen **before** the GeoJSON is written; `min_area_km2`
  is in ground units so both scales drop the same real feature size.

### Memory — both learned by being OOM-killed on CAR (406 Mpx, 7 GB box)

* `palette_lut()` does the nearest-centre search **once over the 24-bit colour
  cube**; every pixel is then a lookup. Per-pixel it builds a `(rows, cols, K)`
  float cube — ~6 GB at K=32 — which is what actually died. Exact, not binned.
* `classify()` works in **row bands with a `win//2` halo**; one whole-image
  float32 indicator plane is 1.6 GB and the score cube 2 GB. Bit-identical away
  from the image border.

Peak RSS is now ~3.5 GB for CAR. Watch it if you raise `band` or `K`.

## Result shape

`data/geomaps/<sheet>_units.geojson` — one Feature per unit, geometry a
MultiPolygon, properties: `sheet, code, name, group, color, commodities[],
affinity[{commodity,weight,why}], area_km2`.

Sudan sanity: 2.10M km² claimed of ~2.5M (Sudan+South Sudan); largest units
PLu 454k, QB 358k, QC 187k km².

## Next steps

1. **Tiles.** `tippecanoe` → `data/geomaps/<sheet>.mbtiles` (already
   gitignored). Serve like `srv/histmap.go` but vector; keep the same
   204-on-miss convention.
2. **Server.** `GET /api/geomap` (catalogue: sheets, units, groups, commodity
   index — read straight from `legend_*.json`) plus a tile route. Note
   `srv/server.go` currently has **uncommitted** histmap routes.
3. **UI.** Grouped toggles keyed on `commodity_index()`, per-unit
   visibility, opacity slider, share-link param (`?geomap=`). Layer order:
   above the basemap, below park/AOI outlines and pins — see the histmap note
   in `AGENTS.md` about `switchBasemap()` and `before` ids.
4. Resolve `paper_like()` units by exclusion inside the cutline.
5. Decide how to present `merge_groups()` sets in the UI — one entry labelled
   with all member codes, never an arbitrary pick.

## Traps already paid for

* `str.replace()` with an empty needle splices a module between every character
  of itself. Use the patch tool.
* The oversized temp PNGs (`/tmp/car_leg.png` 38 MB, `/tmp/cl*.png`) killed an
  earlier conversation with HTTP 413. Crop small, downscale, read once.
* Zenodo's Sudan TIFF carries a rough affine (units "fathom", skew term),
  ±30 px. Seed only — every shipped coordinate comes from the printed
  graticule via `gridfit.py`.
* NLA is behind an Anubis proof-of-work: browser tool → `network_cookies` →
  `curl -b "<cookies>" -C - .../m`. The `image?wid=` endpoint silently caps at
  5000 px.
* Long runs go in tmux. `scripts/histmaps/README.md` has the georeferencing
  discipline both sheets follow.
