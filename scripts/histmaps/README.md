# Sudan Survey 1:250,000 — LOC g8310m.gct00289

770 scanned sheets of the Anglo-Egyptian Sudan 1:250,000 series (Sudan Survey
Dept., Khartoum), 1908–1976, covering Sudan / South Sudan and the CAR frontier.

## TL;DR

* The scans are **not** behind Cloudflare. Only `www.loc.gov` HTML is.
  `tile.loc.gov` serves full-resolution JP2 + IIIF with no auth and no UA games.
* You do not need to georeference these by eye against the index. Every sheet
  carries a **printed 15-arcmin graticule** inside a neatline that is *exactly*
  1.5° lon × 1.0° lat, so the sheet corners are known a priori from the sheet
  number alone, and there are ~35 control points per sheet.
* `sudan250k.py` decodes the sheet number → extent, finds the graticule
  automatically, and emits a TPS-warped GeoTIFF clipped to the neatline.

## Getting the images

```bash
# catalogue (id, sheet, year, extent) for all 770 scans
python3 sudan250k.py list
python3 sudan250k.py list --filter hofrat
python3 sudan250k.py list --json

# full-res JP2 (~20 MB, 12000x8700 px, ~600 dpi)
python3 sudan250k.py fetch cs000027

# georeference -> cs000027_geo.tif (EPSG:4326) + .points for QGIS
python3 sudan250k.py geo cs000027.jp2

# whole series / one 1:1M block
./runall.sh
python3 sudan250k.py all --block 65
```

URL patterns (both public, no Cloudflare):

```
IIIF    https://tile.loc.gov/image-services/iiif/service:gmd:gmd8m:g8310m:g8310m:gct00289:cs000027/info.json
        .../full/pct:25/0/default.jpg          # any region/size/rotation
JP2     https://tile.loc.gov/storage-services/service/gmd/gmd8m/g8310m/g8310m/gct00289/cs000027.jp2
titles  https://tile.loc.gov/storage-services/service/gmd/gmd8m/g8310m/g8310m/gct00289/captions.txt
```

The LOC JSON API also works from a script if you send a browser User-Agent:
`curl -A "Mozilla/5.0 ..." "https://www.loc.gov/resource/g8310m.gct00289/?fo=json"`.
`resources[0]` there gives you `captions.txt` and the IIIF manifest for any LOC item.

## The sheet grid (this is the part that replaces outline-fitting)

Sheets use the International Map of the World layout. A 1:1M block is 6° lon ×
4° lat; it is cut 4×4 into sheets lettered A–P, row-major from the NW corner.
So each 1:250k sheet is **1.5° × 1.0°** — confirmed independently by Durham
University's Sudan Archive guide and by reading the printed neatline of
65-I, which is exactly 24°00'–25°30'E, 9°00'–10°00'N.

Block SW corners, read off the 1941 International Map Company index (`cs000032`):

```
        18E     24E     30E     36E
20N      33      34      35      36
16N      43      44      45      46
12N      53      54      55      56
 8N      64      65      66      67
 4N      76      77      78      79
 0N              85      86
```

Hence for block `b` and letter `L` (index `i` = position in "ABCDEFGHIJKLMNOP"):

```
lon_min = block_lon + (i % 4) * 1.5
lat_max = block_lat + 4 - (i // 4) * 1.0
```

Later reprints renumber onto the IMW `NE-36-C` style; the captions keep the old
number in parentheses and the parser prefers it, since the cell is identical.

### What this collection actually contains

**770 scans, 195 sheet cells.** Blocks present: 33, 34, 35, 36, 43, 44, 45, 46,
53, 54, 55, 56, 64, 65, 66, 67, 77, 78 (+1 stray 51) — i.e. essentially the
whole Sudan/South Sudan series, including the South Sudanese blocks
(64/66/77/78) and the CAR frontier.

#### The 264-line truncation, and why nothing caught it

The first run of this pipeline (2026-08-06) shipped **76 cells covering only
northern Sudan**, and the AOI — the South Sudan / CAR frontier this whole
overlay exists for — was empty. It was not a georeferencing offset. Every sheet
that was built landed on its cell to 0.0 arcsec.

`captions.txt` had been truncated at **264 of 770 lines** by an interrupted
`curl -sSL -o`. The missing 506 lines were, almost exactly, the southern blocks.

The reason this survived a full run, a QA pass and a visual check is worth
internalising: **a short catalogue does not look like a broken catalogue, it
looks like a small collection.** Every downstream stage behaved correctly on the
input it was given — `select.py` reported "76 cells selected", `runall.sh`
reported 76/76 georeferenced, `qa.json` showed a healthy quality distribution,
the mosaic built, and the README (this file) then *documented the truncation as
a property of the archive*: "Blocks 53, 55, 64, 66, 77, 78 are not in this LOC
item" and go look at Durham instead. That sentence was false, and it was written
with confidence because 76 sheets of real, correctly-registered map were sitting
on disk. A wrong answer with no failures anywhere is the expensive kind.

The tell was available and was not read: 264 is a suspiciously round stopping
point, and the LOC item page states its own length. So:

* `catalogue()` now checks the line count against `EXPECTED_SCANS = 770` on
  every call and re-fetches a short file.
* `fetch_captions()` downloads to `.part`, cross-checks the item's own
  `resource.segment_count` from `?fo=json`, and refuses to install a file
  shorter than that. `curl -f`, `--retry 5`, atomic `os.replace`.
* The general form, which is the AGENTS.md "no-op that reads as an answer" rule
  applied to an *input*: **a manifest is not trusted for its content until its
  length is checked against the source.** Partial input is the failure mode that
  produces no error and no gap — only a smaller world.

Related: `mosaic.sh` step 1 now writes each `blk*.txt` sheet list and
**invalidates the cached `blk*.mbtiles` when that list changes**. Without it,
step 2's "exists, skip" would have happily reused the old northern-only tiles
for a block that had just gained 12 sheets — a rerun that appears to work while
re-shipping the identical partial coverage.

#### Ordering is part of the product

A 195-cell run is ~2 days of download+warp and *will* be interrupted (the first
attempt at `JOBS=3` took the VM down; the second was OOM-throttled). So
`select.py --priority-bbox W,S,E,N` emits the cells intersecting the area under
study first, nearest-centre outwards, and marks them `"priority": true`;
`sudan250k.py all --ids` preserves that order rather than collapsing it to a set.
An interrupted run then has the sheets that matter, and "is the AOI covered yet"
is answerable at any moment.

```bash
python3 select.py --priority-bbox 22.70,4.25,31.30,10.97   # the XSA study area
```

`rebuild_night.sh` + `histmap-rebuild.service` run the whole thing as a
throttled, resumable systemd oneshot. Two things learned by doing it wrong:

* **Retry before tiling, not after.** A failure list from a single run mixes real
  defects with network noise — cs000643 (curl reset) and cs000694 (truncated
  JP2) both succeeded on a second attempt, and both were AOI sheets. A sheet
  missing at mosaic time is a hole that nothing downstream notices.
* **The throttle was worth less than it cost.** At `CPUQuota=60%` the run took
  13.5 min/sheet while load sat at 0.6 and app latency at 8 ms. A single warp
  cannot exceed one core anyway (`GDAL_NUM_THREADS=1`), so 100% of one core —
  still leaving a whole core free — is the right setting. `MemoryHigh` before
  `MemoryMax` so a ballooning sheet is throttled into swap rather than
  OOM-killed mid-warp.

## How the georeferencing works

`grat.py` is a two-tier detector, built after two real failure modes showed up:

1. **The ladder is the primary estimator, not the rectangle.** Fitting an
   arithmetic ladder of 15' rungs to the detected straight-ink lines, then
   taking rung 0 and rung n as the neatline, recovers borders that are faint,
   cropped, or overprinted by the collar. A pure "find the biggest rectangle"
   search picked the wrong lines on the 1909 Hofrat el Nahas sheet.
2. **Later editions rule the interior graticule as short crosses, not lines.**
   A global long-run threshold sees only the border on the 1936 sheets. Interior
   rungs are therefore predicted from the neatline and refined locally with a
   short structuring element; rungs that cannot be measured are interpolated and
   *reported as such* rather than silently faked.

The rectangle is accepted only if its aspect matches
`1.5·cos(lat) / 1.0` for the sheet's own latitude, which is what stops the
detector locking onto the paper edge or a title box.

Warping is thin-plate spline over all 35 points, not a 4-corner affine. This
matters: East View documents the series as **modified polyconic**, so the
graticule is genuinely curved and a corner-only fit bakes that curvature in as
error. The reported "non-affine deformation" is exactly the size of that effect
(typically 70–350 m, i.e. 0.3–1.5 mm on the paper) — it is the amount the TPS is
absorbing, *not* an accuracy estimate.

Output is clipped to the neatline with `-crop_to_cutline`, so sheets mosaic
edge-to-edge with no collar overlap.

## Checking the result

The CAR/South Sudan border has not moved since the condominium, so modern GADM
is a fair check on 1900s–1930s sheets:

```bash
python3 overlay.py geo/cs000029_geo.tif /tmp/check.png   # GADM CAF/SDN/SSD in colour
```

`qa.json` flags any sheet with aspect error > 2% or unmeasured interior rungs.
Those are the ones worth eyeballing; the rest are fine.

### Result of the 195-cell run (2026-08-08)

**187 of 195 sheet cells georeferenced**, one edition each, no duplicate cells,
every output landing on its IMW cell with **bounds error 0.0 arcsec** and 35
control points each. Editions 1915-1968, median 1933. All 18 blocks present:

```
33  6   34  8   35 12   36  8   43  2   44 11
45 15   46  8   53  7   54 16   55 16   56  9
64  5   65 16   66 16   67  4   77 12   78 16
```

**The 49 cells covering the XSA study area are complete (49/49)** — that was
the point of the rebuild, and of `--priority-bbox`.

Mosaic: 3.6 GB, z0-14, 574k tiles (398,893 at z14).

#### The 8 failures are one failure, and it is honest

| cell | sheet | ink |
|---|---|---|
| 43-D | Hagar Waqif | 0.012 |
| 43-L | Ein Aga | 0.019 |
| 44-D | J. Abyad | 0.017 |
| 44-F | Bir En Natrun | 0.023 |
| 44-L | Abu Tabari | 0.032 |
| 44-M | Libyan Desert | 0.012 |
| 44-P | J. El'ein | 0.037 |
| 45-M | Eilai | 0.050 |

Seven report `no neatline candidates`, one `no rectangle matched expected
aspect`, one dies inside an OpenCV morphology call. They are **not** scattered:
every one is in blocks 43/44/45, the Libyan and Nubian Desert, and their median
ink coverage is **0.021 against a corpus median of 0.085** — four of them are in
the twenty sparsest cells in the whole series. These are near-blank sheets of
empty desert, and the graticule detector needs printed straight ink to fit its
ladder to. There is not enough map on the paper to register the paper.

This is the detector declining rather than guessing, which is the behaviour we
want: a wrong warp on a blank sheet would be invisible in the mosaic and wrong
forever. All 8 are far outside any area of interest. 45-M Eilai additionally
fails identically on its other edition (cs000191, 1935), so that cell is
genuinely unrecoverable by this method.

If they are ever needed: they would have to be registered from the sheet corners
(the extent is known a priori from the sheet number) rather than from detected
graticule — i.e. `--method affine` with synthesised corner GCPs. Not done,
because an unvalidated warp is worse than a hole.

#### Caveat that outlives all of the above

The 1:250k series was compiled from route traverses, so **the map's own interior
geometry is the dominant error**, not the georeferencing. Rivers and hills on a
1909 sheet can sit kilometres off truth even when the graticule is registered
perfectly. Georeference to the graticule (as here) and treat the content as the
historical claim it is — do not rubber-sheet the content onto modern rivers, or
you destroy the evidence.

#### qa.json is merged, not overwritten

`sudan250k.py` merges `qa.json` **by id** across runs. It used to rewrite the
file wholesale, so the final `--resume` pass over 8 retried sheets replaced the
record for all 187 — and the only thing that had been making that survivable was
a hand-made `/tmp/qa_full_backup.json`, which a VM reboot deleted. The QA record
for a corpus must not depend on a file in `/tmp`, and it must not be able to
shrink while every step reports success. (That is the *same* failure as the
truncated `captions.txt` this rebuild exists to fix, one directory downstream.)

The per-sheet quality fields (aspect error, non-affine deformation, rung counts)
for the runs before the fix are gone; what is recoverable from the `.points`
sidecars — bounds error and GCP count, both perfect — is in
`data/histmaps/qa_bounds_recon.json`. Quality numbers from the earlier
76-sheet run, which are representative: 20/69 sheets over the 2% aspect gate, 6
over 5%, non-affine deformation 628 m median / 1447 m max, all dominated by the
modified-polyconic projection the TPS absorbs rather than by registration error.

### Result of the first (truncated) run (2026-08-06/07) — superseded

Kept because the numbers are a good calibration of what a *correct* run of a
*wrong* input looks like: 76 of 76 sheet cells georeferenced, one edition each,
no duplicate cells, every output landing on its IMW cell with **bounds error 0.0
arcsec**. Blocks 45 (16/16), 54 (15/16), 35, 65, 34, 46, 33, 36. Editions
1915-1944, median 1932. Flawless — and covering the wrong half of the country,
for the catalogue reason above.

One sheet was unrecoverable: **45-M Eilai (cs000192), "no neatline candidates"**.
Its only other edition (cs000191, 1935) fails the same way, so the cell is
genuinely absent rather than mis-selected. Two others (45-B, 65-K) failed on
transient `curl` errors and succeeded on retry — the observation that is now
automated as the retry pass in `rebuild_night.sh`.

Quality distribution: 20/69 sheets exceeded the 2% aspect gate and 6 exceeded
5%; non-affine deformation 628 m median, 1447 m max. Both are dominated by the
modified-polyconic projection the TPS absorbs, **not** by registration error —
see the caveat below. Three sheets (34-M, 36-I, 45-G) had interior rungs that
could not be measured and were interpolated; they are marked as such in
`qa.json` rather than silently faked.

Caveat worth knowing: the 1:250k series was compiled from route traverses, so
**the map's own interior geometry is the dominant error**, not the
georeferencing. Rivers and hills on a 1909 sheet can sit kilometres off truth
even when the graticule is registered perfectly. Georeference to the graticule
(as here) and treat the content as the historical claim it is — do not
rubber-sheet the content onto modern rivers, or you destroy the evidence.

## The shipped product: one MBTiles

`runall.sh` leaves one GeoTIFF per sheet cell in `data/histmaps/geo/`.
Those are the archival artefact; the *usable* one is a single tile pyramid:

```bash
./mosaic.sh          # ~4 h at 187 sheets, resumable -> data/histmaps/sudan250k.mbtiles
```

3.6 GB, z0-14, 574k tiles (398,893 at z14), EPSG:3857, transparent-background
RGBA PNG. (The earlier truncated 76-sheet build was 1.4 GB / 226k tiles.)

`refresh_meta.py` rewrites the `metadata` table — name, bounds, and a
**derived** sheet/block count — without touching tiles. It is separate from
`mosaic.sh` step 4 on purpose: that step also deletes the overview pyramid (an
interrupted `gdaladdo` otherwise leaves a half-populated zoom level), so on the
3.6 GB file re-running it just to fix a string would throw away ~40 min of
rebuilding. The counts are read from `data/histmaps/geo/`, never typed: a
hardcoded `"76 sheets"` survived a rebuild that more than doubled the coverage
and shipped inside the layer's own description.

Three things about the merge are load-bearing:

* **Per 1:1M block, then union the z14 tables.** The series covers 18 of 22
  blocks, so the bounding box is still partly empty; tiling the whole envelope
  in one `gdal_translate` walks millions of tiles that can only ever be blank.
  Each block is dense and tiles in 3-17 min. The z0-13 pyramid is then built
  *once* over the merged file, so overview tiles straddling a block seam are
  averaged from both blocks rather than being built per-block and clobbering
  each other. Step 1 invalidates a block's cached tiles when its sheet list
  changes — see the truncation note above for why that is not optional.
* **A few hundred z14 tiles are seam duplicates** — adjacent blocks share an
  edge tile (672 of 155,579 in the 76-sheet build).
  `insert or replace` keeps the last writer; the sheets are clipped
  to their neatlines so either copy is correct.
* **`tile_row` is TMS.** The bounds computed for the metadata must take the
  *minimum* row as the SOUTH edge. Getting that backwards writes `south > north`
  and GDAL answers `Invalid value for 'bounds' metadata` and silently falls back
  — which is a warning, not an error, so it ships unless you read the log.

Step 4 also deletes any partial pyramid before rebuilding: `gdaladdo` on this
file takes ~40 min and has been interrupted, which leaves a half-populated
zoom level that looks like a rendering bug rather than a truncated run.

## How it reaches a user

| Piece | Where |
|---|---|
| Meta (available? bounds? size?) | `GET /api/histmap` -> `srv/histmap.go` |
| Tiles | `GET /api/histmap/sudan250k/{z}/{x}/{y}.png` |
| Archive download | `GET /api/histmap/sudan250k/download` (Range-capable) |
| UI | admin panel -> **Map Settings** -> Historical Maps (`HistMap` in globe.html) |
| Share link | `?histmap=sudan250k` |
| OCR'd labels | `ocr_labels.py` -> `data/histmaps/labels.sqlite3`; `GET /api/histmap/sudan250k/labels` (see `docs/API.md`) |

Tiles are read out of the MBTiles rather than exploded to 226k files, so the
online overlay and the file handed to a field device are literally the same
bytes and cannot drift.

**White ink is a client-side effect, not a second tileset.** `ink.py` writes one
flat near-black (26,22,18) on transparent paper, so the map layer sets
`raster-brightness-min: 1`, which lifts RGB to white and leaves alpha alone.
The download deliberately stays black: offline viewers (Locus, OsmAnd, QGIS)
default to light backgrounds, where white ink is invisible.

Two ordering rules the UI depends on:

* The layer is inserted **before the first non-raster layer**, so it sits above
  the basemap and below park/AOI outlines, fire trajectories and pins. Anything
  added later is appended above it, so pins made after enabling it stay on top
  for free.
* `switchBasemap()` rebuilds the style; its generic custom-layer capture
  **excludes** `histmap-lyr`/`histmap-src` and calls `HistMap.reattach()`
  instead, which re-adds on `idle`. Replaying the captured spec would append the
  scan on top of everything, and re-adding during `styledata` silently drops the
  layer because the `before` id has not landed yet. Both were observed.

A tile miss returns **204, not 404**: most of the bounding box has no sheet, and
204 keeps MapLibre's error path and the browser console quiet.

**A rebuild must change the tile URLs.** Tiles are served
`immutable, max-age=7d`, which is honest *within* one build — but the URL is a
pure function of (z, x, y), so after a rebuild every client stays pinned to the
previous mosaic for a week. That happened: the 76-sheet truncated build kept
rendering after the 187-sheet rebuild, and only at the zoom levels the browser
had actually cached — the levels that had been a 204 refetched and filled in.
So it looked like *gaps at some zoom levels*, i.e. a tiling bug, not a stale
cache. `GET /api/histmap` therefore returns `rev` (mtime+size of the MBTiles,
`histMapRev`) and puts it in the `tiles` template as `?v=`; a rebuild changes
every tile URL exactly once. Never hand-write the tile URL in the client —
take `meta.tiles`.

## Rights

LOC lists no known copyright restrictions for this item (US government
holdings of foreign official mapping of this era; digitised by LOC). Verify per
sheet at https://www.loc.gov/item/87692353/ before republishing.
