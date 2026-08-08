# Geology map overlays — handover

**Goal.** Two scanned geological maps, vectorized into toggleable vector
overlays (analogous to the raster `?histmap=`), grouped so a park/AOI facing a
gold rush can switch on every unit relevant to gold / silver / uranium /
lithium / cobalt at once, while still selecting or hiding individual units and
changing opacity. Everything outside the country boundary, and all background
without meaning (paper, collar, legend boxes, neighbouring-country fill), is
dropped.

Read `scripts/histmaps/README.md` first — the georeferencing discipline here is
deliberately the same one, and its caveats apply.

Last commit: `004b535`.

---

## Sources (downloaded, gitignored, ~1.4 GB in `data/geomaps/src/`)

| id | file | size | provenance |
|---|---|---|---|
| `sudan` | `sudan_geology_2004.tif` | 6956x9498 | GRAS *Geological Map of the Sudan* 1:2,000,000 (2004), Zenodo record 19150268 |
| `car` | `car_geology_1964.tif` | 24196x16791 @600dpi | BRGM *Carte géologique de la République Centrafricaine* 1:1,500,000 (1964), coord. J-L. Mestraud, NLA `nla.obj-2981820452` |

Two fetch facts that cost time:

* **Zenodo is a plain download.** The Sudan TIFF carries a rough affine
  geotransform already (units nominally "fathom", a visible skew term). It is
  right to about ±30 px — a fine *seed*, not an answer.
* **NLA is behind Anubis** (proof-of-work interstitial). `curl` gets an HTML
  challenge; the `/m` full TIFF (1.16 GB) does not. Get past it with the
  browser tool: navigate to `https://nla.gov.au/nla.obj-2981820452/view`, then
  `network_cookies`, then `curl -b "<cookies>" -C - .../m`. The `image?wid=`
  derivative endpoint **caps at 5000 px** silently (`wid=10000` and `wid=20000`
  both return the same 5000 px JPEG), so it is not a substitute for `/m`.
  The CAR scan has **no** georeferencing at all.

---

## What exists (`scripts/geomaps/`)

| file | role |
|---|---|
| `gridfit.py` | `_vote_line`, `GridReader`, `PolyModel`, `measure_grid` |
| `sheets.py` | per-sheet metadata: title/publisher/scale/source, grid lon+lat lists, seed transform, `countries` (ISO3) |
| `georef.py` | `python3 scripts/geomaps/georef.py <sudan\|car> [--preview]` → `data/geomaps/work/<id>_geo.tif` + `<id>_gcps.json` |

`georef.py` builds a GCP VRT, warps with `gdalwarp -tps`, and clips with
`-cutline`/`-crop_to_cutline` against the GADM outline (reusing
`scripts/histmaps/gadm_{SDN,SSD,CAF}.json`). Seeds live in `sheets.py` and never
reach the output; every shipped coordinate is measured off the printed
graticule.

CAR seed, read off the collar ticks: lon 15° at x=1812, 1° = 1723 px;
lat 10° at y=2395, 1° = 1757 px.

### Why the line detector votes

The 1:250k line sheets are black ink on cream, so a global threshold finds the
grid. These two print the graticule as **a thin dark line over saturated colour
fill**, where a threshold finds the polygon edges instead. `_vote_line` takes a
2-D strip along the predicted line, high-passes across it, lets each sample vote
for its own darkest offset, and keeps the offset carrying the most votes: a
geological contact is dark too, but it *wanders*, so its votes scatter across
the window while a ruled meridian stacks them on one column. A crossing with
fewer than `min_frac` of samples voting is **dropped, not interpolated**, and
the report counts measured points, so a mostly-guessed sheet cannot pass as a
measured one.

`PolyModel` (degree 2, iterative 3σ trim) vets the points *before* the TPS —
deg-2 absorbs meridian convergence and scanner keystone but cannot chase a
mis-measured point, which a TPS gladly would. The TPS then runs in `gdalwarp`
over points already vetted.

---

## State: unfinished, one measurement outstanding

The **old** profile-median detector scored Sudan at 61 GCPs / **12.9 px rms**
(~3.3 km on the ground) — not good enough. `_vote_line` replaced it and **has
not been re-run**. Spot checks of the vote detector against the seed prediction
were promising (30E/15N: peak within 1 px, 48/800 samples; 26E/18N within 2 px)
and confirmed the old failures were real mis-locks (33E/18N was 30 px off,
31E/13N 62 px off).

**First action:**

```bash
python3 scripts/geomaps/georef.py sudan --preview     # expect low single-digit px rms
python3 scripts/geomaps/georef.py car   --preview
python3 scripts/histmaps/overlay.py data/geomaps/work/sudan_geo.tif /tmp/chk.png
```

`overlay.py` draws GADM CAF/SDN/SSD in colour over the result — that is the
acceptance test. **The two sheets must abut exactly along the SDN/SSD–CAF
frontier**; a seam is the symptom of a bad fit on one of them, not of the
cutline. Only when `--preview` is right, run full resolution.

---

## Still to do

1. **Vectorize.** Colour-quantize each sheet and polygonize into geological
   units, matching against the printed legend swatches (both sheets carry a full
   legend block with letter codes: `QB`, `PZgr`, `MVa`, … on Sudan; `γs`, `Q`,
   `Me`, … on CAR). Drop paper, collar, legend boxes, inset maps and
   neighbouring-country fill. Expect the hardest part to be that both sheets use
   **halftone screens and hachures**, not flat fill — quantize on a
   median-filtered image or the screen dots become their own class.
2. **Commodity grouping.** Map units → affinity groups (gold, silver, uranium,
   lithium, cobalt, …). This is an *inference over lithology*, so label it as
   such in the UI; do not present it as an occurrence dataset. (Compare the
   mining verdict in `AGENTS.md`: inference from context ships, fabricated
   evidence does not.)
3. **Tiles.** `tippecanoe` (installed) → MBTiles per sheet, served like the
   histmap archive so the online overlay and any offline copy are the same
   bytes.
4. **Server.** `srv/geomap.go`, modelled on `srv/histmap.go`
   (`GET /api/geomap`, `/api/geomap/{id}/{z}/{x}/{y}.pbf`, `/download`).
   Tile misses return **204, not 404** — most of a bounding box is off-sheet.
5. **UI.** A section beside Map Settings → Historical Maps in `globe.html`:
   commodity-group toggles, per-unit checkboxes, opacity slider, `?geomap=`
   share param. Keep the histmap layer-order rules — insert **before the first
   non-raster layer**, and `switchBasemap()` must **exclude** the geomap layers
   from its generic custom-layer capture and call a `reattach()` on `idle`
   instead.

## Cost discipline

Don't re-download the sources. Run gdal jobs in tmux. Stay at `--preview`
resolution until the residuals are right — a full-res warp of the CAR sheet is
24k x 17k px.
