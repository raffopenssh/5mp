# Sudan Survey 1:250,000 — LOC g8310m.gct00289

264 scanned sheets of the Anglo-Egyptian Sudan 1:250,000 series (Sudan Survey
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
# catalogue (id, sheet, year, extent) for all 264 scans
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

Blocks present: 33, 34, 35, 36, 45, 46, 54, 65 (+1 stray 51).
**Blocks 53, 55, 64, 66, 77, 78 are not in this LOC item** — that is most of
South Sudan proper and the western Darfur/CAR strip. For those, see the
Durham Sudan Archive, AMS series N504/P502, and IGN's 1:200,000 AEF sheets.

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

Caveat worth knowing: the 1:250k series was compiled from route traverses, so
**the map's own interior geometry is the dominant error**, not the
georeferencing. Rivers and hills on a 1909 sheet can sit kilometres off truth
even when the graticule is registered perfectly. Georeference to the graticule
(as here) and treat the content as the historical claim it is — do not
rubber-sheet the content onto modern rivers, or you destroy the evidence.

## Rights

LOC lists no known copyright restrictions for this item (US government
holdings of foreign official mapping of this era; digitised by LOC). Verify per
sheet at https://www.loc.gov/item/87692353/ before republishing.
