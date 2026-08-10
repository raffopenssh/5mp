"""Turn a scanned geological sheet into polygons, by reading its print screen.

The obvious approach - nearest legend colour per pixel - does not work on
either sheet, and the reason is worth stating because it decides the whole
design.  Neither map is printed in flat ink.  Both are **halftone screens**:
a unit is a pattern of dots of a few process colours, and the "colour" in the
legend is only that pattern's average.  Two consequences:

* Averaging throws away the signal.  On Sudan, 12 groups of units average to
  the same RGB (`legend.Legend.merge_groups()`), among them QE/QD and the
  whole TA/TB/TC yellow family; on CAR, 5 groups do.  A colour quantizer
  cannot separate them at all, and would have to emit a merged class.
* But the *screens* differ where the averages do not.  TA and TC are the same
  yellow at different dot densities; MSq and TQ differ in ruling.  So the
  discriminative feature is the local histogram of screen colours, not the
  local mean.

Hence: classify a pixel by the **distribution of palette indices in a window
around it**, compared against the same distribution measured over the legend
swatch.  Measured held-out accuracy (train on one half of each swatch, test on
the other) - Sudan 0.94 at a 17 px window, CAR 0.95 at 33 px with K=32.  Flat
colour, for comparison, cannot exceed ~0.77 on Sudan because 12 groups are
degenerate by construction.

Two sheets, two palettes, for a reason:

  sudan  the scan is already posterised - the whole 6956x9498 TIFF contains
         exactly **64 distinct colours**.  That is the printer's palette,
         recovered for free, so the index image is exact and needs no
         clustering.
  car    a 600 dpi continuous-tone scan, 583k colours in a single window.  Its
         palette is fitted with k-means over the legend swatches (K=32).

Where the work happens
----------------------
Classification runs on the **source** raster, not on the warped one.  The warp
is TPS with `-r near`, which duplicates and drops rows to fit the graticule;
that is invisible in a colour and fatal in a screen, since the classifier is
measuring exactly that dot density.  So: classify in scan space, write a label
raster, and warp *that* - a label raster is the one thing `-r near` is
unarguably correct for.

`--stride` subsamples the classification grid.  It costs nothing in accuracy
(the window is 17-33 px, far wider than the stride) and it is the difference
between a 400 Mpx problem and a 25 Mpx one.  The default keeps ground
resolution near 500 m, which is finer than either sheet's own line work at
1:1.5M-1:2M.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from legend import Legend, OUT_DIR, ROOT  # noqa: E402
from sheets import SHEETS  # noqa: E402

WORK = os.path.join(ROOT, "data", "geomaps", "work")

# per sheet: (window px, palette size, classification stride)
TUNING = {
    "sudan": dict(win=17, k=None, stride=2),   # k=None: the scan is already posterised
    "car": dict(win=33, k=32, stride=4),  # 406 Mpx: see the banding note in classify()
}
# a pixel whose best and second-best unit are closer than this in
# Bhattacharyya distance is not claimed - see `--min-margin`
MIN_MARGIN = 0.02
TILE = 1024


def run(cmd):
    t = time.time()
    subprocess.run(cmd, check=True)
    return time.time() - t


# ---------------------------------------------------------------------------
# palette
# ---------------------------------------------------------------------------
def exact_palette(img, limit=512):
    """The scan's own colour table, if it has few enough colours to have one."""
    b = img.astype(np.uint32)
    key = (b[:, :, 2] << 16) | (b[:, :, 1] << 8) | b[:, :, 0]
    u, c = np.unique(key, return_counts=True)
    if len(u) > limit:
        return None
    o = np.argsort(-c)
    u = u[o]
    return np.stack([(u >> 16) & 255, (u >> 8) & 255, u & 255], axis=1).astype(np.float32)


def fitted_palette(img, leg, k, seed=3):
    """k-means over the legend swatches only.

    Deliberately not over the whole sheet: the sheet is mostly a few large
    units plus a lot of paper, so a global fit spends its clusters on paper
    and on the commonest fill and leaves the rare units sharing one centre.
    Fitting on the swatches gives every unit equal weight by construction.
    """
    px = np.vstack([img[y:y + h, x:x + w].reshape(-1, 3)
                    for (x, y, w, h) in (u.box for u in leg.units)]).astype(np.float32)
    px = px[::max(1, len(px) // 400000)]
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
    _, _, cen = cv2.kmeans(px, k, None, crit, seed, cv2.KMEANS_PP_CENTERS)
    return cen.astype(np.float32)


def build_palette(sheet, img, leg):
    k = TUNING[sheet]["k"]
    if k is None:
        pal = exact_palette(img)
        if pal is None:
            raise SystemExit("%s: expected a posterised scan, found >512 colours" % sheet)
        return pal, "exact"
    return fitted_palette(img, leg, k), "kmeans%d" % k


def sheet_index(sheet, img, leg):
    pal, how = build_palette(sheet, img, leg)
    return index_image(img, pal, exact=(how == "exact")), pal, how


def palette_lut(pal, block=1 << 20):
    """A full 24-bit colour -> palette-index table.

    The obvious per-pixel nearest-centre search is what OOM-killed CAR twice:
    at 406 Mpx even a 2000-row chunk builds a (rows, cols, K) float distance
    cube, ~6 GB for K=32.  But there are only 16.7M possible colours and they
    do not depend on the image, so the search is done **once** over the colour
    cube - 16.7M x K, in blocks - and every pixel afterwards is a table
    lookup.  Exact, not binned: no quantisation is introduced.
    """
    lut = np.empty(1 << 24, np.uint8)
    for i in range(0, 1 << 24, block):
        k = np.arange(i, min(i + block, 1 << 24), dtype=np.uint32)
        rr = ((k >> 16) & 255).astype(np.float32)
        gg = ((k >> 8) & 255).astype(np.float32)
        bb = (k & 255).astype(np.float32)
        d = ((rr[:, None] - pal[None, :, 0]) ** 2 +
             (gg[:, None] - pal[None, :, 1]) ** 2 +
             (bb[:, None] - pal[None, :, 2]) ** 2)
        lut[i:i + block] = d.argmin(1).astype(np.uint8)
    return lut


def index_image(img, pal, exact=False, band=4096):
    """Map every pixel to its nearest palette entry, in row bands.

    For an already-posterised scan the table is *sparse and exact* - the 24-bit
    colour is the key and every key is a real palette entry.  Otherwise it is
    the nearest-centre map from `palette_lut`.  Either way this is one gather
    per pixel, and the bands keep the intermediate uint32 key array off the
    peak: a whole-image key array for CAR is 1.6 GB on top of the 1.2 GB image.
    """
    if exact:
        lut = np.zeros(1 << 24, np.uint8)
        key = (pal[:, 0].astype(np.uint32) << 16 | pal[:, 1].astype(np.uint32) << 8
               | pal[:, 2].astype(np.uint32))
        lut[key] = np.arange(len(pal), dtype=np.uint8)
    else:
        lut = palette_lut(pal)
    h, w = img.shape[:2]
    out = np.empty((h, w), np.uint8)
    for y in range(0, h, band):
        b = img[y:y + band].astype(np.uint32)
        out[y:y + band] = lut[(b[:, :, 2] << 16) | (b[:, :, 1] << 8) | b[:, :, 0]]
        del b
    return out


# ---------------------------------------------------------------------------
# signatures + classification
# ---------------------------------------------------------------------------
def signatures(idx, leg, npal, half=None):
    """sqrt of the palette-index histogram of each legend swatch.

    Square-rooted here so the classifier's inner loop is a dot product: the
    Bhattacharyya coefficient of two histograms is <sqrt(p), sqrt(q)>.
    """
    sig = []
    for u in leg.units:
        x, y, w, h = u.box
        if half == "train":
            w = w // 2
        elif half == "test":
            x, w = x + w // 2, w - w // 2
        v = idx[y:y + h, x:x + w].ravel()
        hh = np.bincount(v, minlength=npal).astype(np.float32)
        hh /= max(hh.sum(), 1)
        sig.append(np.sqrt(hh))
    return np.stack(sig)


def classify(idx, sig, npal, win, stride, min_margin=MIN_MARGIN, band=2048):
    """Label every `stride`-th pixel by its windowed palette histogram.

    Implemented as npal box filters rather than a per-pixel loop: the windowed
    histogram of one palette index is a box blur of that index's indicator
    image, so the whole thing is npal separable convolutions and one argmax
    over a (h, w, nunits) score - which is why this runs in minutes and not
    days.

    Done in **row bands with a halo**, because the whole-image version is not
    merely slow on a big sheet, it is fatal: CAR is 406 Mpx, so one float32
    indicator plane is 1.6 GB and the score cube is 2 GB, and the process was
    OOM-killed twice before this loop existed.  The halo is `win // 2` rows on
    each side so a band's edge pixels see the same window they would have seen
    whole; the result is bit-identical to the unbanded version except at the
    image border, where both fall back to BORDER_REPLICATE.
    """
    h, w = idx.shape
    hs, ws = (h + stride - 1) // stride, (w + stride - 1) // stride
    nunits = sig.shape[0]
    best = np.zeros((hs, ws), np.uint8)
    margin = np.zeros((hs, ws), np.float32)
    k = (win, win)
    halo = win // 2
    live = [p for p in range(npal) if sig[:, p].any()]

    # bands are aligned to the stride grid so ::stride sampling stays coherent
    step = max(stride, (band // stride) * stride)
    for y0 in range(0, h, step):
        y1 = min(h, y0 + step)
        a0, a1 = max(0, y0 - halo), min(h, y1 + halo)
        chunk = idx[a0:a1]
        sc = np.zeros(((y1 - y0 + stride - 1) // stride, ws, nunits), np.float32)
        off = y0 - a0
        for p in live:
            ind = (chunk == p).astype(np.float32)
            bl = cv2.boxFilter(ind, -1, k, normalize=True,
                               borderType=cv2.BORDER_REPLICATE)
            bl = bl[off:off + (y1 - y0):stride, ::stride]
            np.sqrt(bl, out=bl)
            sc += bl[:, :, None] * sig[:, p][None, None, :]
        b = sc.argmax(2)
        r0 = y0 // stride
        best[r0:r0 + b.shape[0]] = b.astype(np.uint8)
        bi = b[:, :, None]
        top = np.take_along_axis(sc, bi, 2)[:, :, 0]
        np.put_along_axis(sc, bi, -1.0, 2)
        margin[r0:r0 + b.shape[0]] = top - sc.max(2)
        del sc
    return best, margin


# ---------------------------------------------------------------------------
# label raster -> warped -> polygons
# ---------------------------------------------------------------------------
def write_label_tif(sheet, best, margin, stride, path, min_margin=MIN_MARGIN):
    """A single-band label raster in SCAN space, 0 = unclaimed.

    Codes are 1-based so 0 can mean "no unit" - unclaimed pixels, and later
    everything outside the cutline.  A pixel whose two best units are within
    `min_margin` is dropped rather than guessed: on a screen-printed sheet the
    ambiguous pixels are mostly line work and lettering, and a confident wrong
    label there would draw a hairline of some unrelated formation along every
    contact.
    """
    import rasterio
    from rasterio.transform import Affine
    lab = (best.astype(np.uint16) + 1)
    lab[margin < min_margin] = 0
    src = os.path.join(ROOT, SHEETS[sheet]["src"])
    with rasterio.open(src) as s:
        prof = dict(driver="GTiff", width=lab.shape[1], height=lab.shape[0], count=1,
                    dtype="uint16", nodata=0, compress="deflate", tiled=True)
    # scan-space "geotransform": the stride grid, so the GCPs still apply after
    # they are scaled by the same factor
    prof["transform"] = Affine(stride, 0, 0, 0, stride, 0)
    with rasterio.open(path, "w", **prof) as d:
        d.write(lab, 1)
    return path, float((lab == 0).mean())


def warp_labels(sheet, lab_path, stride, out_path, res=None):
    """Warp the label raster with the sheet's own GCPs, scaled by the stride."""
    gcps = json.load(open(os.path.join(WORK, "%s_gcps.json" % sheet)))["gcps"]
    args = []
    for g in gcps:
        args += ["-gcp", "%.4f" % (g["x"] / stride), "%.4f" % (g["y"] / stride),
                 str(g["lon"]), str(g["lat"])]
    vrt = out_path + ".vrt"
    run(["gdal_translate", "-q", "-of", "VRT", "-a_srs", "EPSG:4326"] + args
        + [lab_path, vrt])
    cut = os.path.join(WORK, "%s_cut.geojson" % sheet)
    if res is None:
        import rasterio
        with rasterio.open(os.path.join(WORK, "%s_geo.tif" % sheet)) as d:
            res = d.transform.a * stride
    run(["gdalwarp", "-q", "-overwrite", "-tps", "-r", "near",
         "-t_srs", "EPSG:4326", "-tr", str(res), str(res),
         "-cutline", cut, "-crop_to_cutline", "-dstnodata", "0",
         "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE",
         "-wo", "NUM_THREADS=ALL_CPUS", "-multi", vrt, out_path])
    os.unlink(vrt)
    return out_path


def polygonize(sheet, warped, leg, out_geojson, min_area_km2=1.0, simplify_m=250):
    """Label raster -> one MultiPolygon feature per unit.

    Sieved and simplified before it is written, not after: the raw
    polygonisation of a screened scan is millions of few-pixel specks (dot
    clusters that survived the window filter) and no viewer, and no
    tippecanoe run, wants to see them.  `min_area_km2` is in ground units so
    the two sheets, at different scales, drop the same *real* size of feature.
    """
    import rasterio
    from rasterio import features as rfeatures
    import shapely.geometry as sg
    from shapely.ops import unary_union

    codes = [u.code for u in leg.units]
    by_code = {}
    with rasterio.open(warped) as d:
        arr = d.read(1)
        tr = d.transform
    lat = abs(tr.f + tr.e * arr.shape[0] / 2)
    km_per_deg_x = 111.32 * max(0.1, np.cos(np.radians(lat)))
    min_area_deg2 = min_area_km2 / (km_per_deg_x * 110.57)
    simplify_deg = simplify_m / 111320.0

    for geom, val in rfeatures.shapes(arr, mask=(arr > 0), transform=tr):
        v = int(val)
        if v < 1 or v > len(codes):
            continue
        g = sg.shape(geom)
        if g.area < min_area_deg2:
            continue
        by_code.setdefault(codes[v - 1], []).append(g)

    feats = []
    for u in leg.units:
        polys = by_code.get(u.code)
        if not polys:
            continue
        g = unary_union(polys).simplify(simplify_deg, preserve_topology=True)
        if g.is_empty:
            continue
        feats.append(dict(
            type="Feature",
            properties=dict(
                sheet=leg.sheet, code=u.code, name=u.name, group=u.group,
                color=u.hex,
                commodities=[c["commodity"] for c in u.commodities],
                affinity=u.commodities,
                area_km2=round(g.area * km_per_deg_x * 110.57, 1),
            ),
            geometry=sg.mapping(g)))
    json.dump(dict(type="FeatureCollection", features=feats), open(out_geojson, "w"))
    return out_geojson, len(feats)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sheet", choices=sorted(TUNING))
    ap.add_argument("--stride", type=int)
    ap.add_argument("--win", type=int)
    ap.add_argument("--min-margin", type=float, default=MIN_MARGIN)
    ap.add_argument("--min-area-km2", type=float, default=1.0)
    ap.add_argument("--holdout", action="store_true",
                    help="train on half of each swatch, report accuracy on the other, exit")
    a = ap.parse_args(argv)

    sheet = a.sheet
    tun = TUNING[sheet]
    win = a.win or tun["win"]
    stride = a.stride or tun["stride"]
    leg = Legend.load(sheet)
    src = os.path.join(ROOT, SHEETS[sheet]["src"])

    t0 = time.time()
    img = cv2.imread(src, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit("cannot read %s" % src)
    print("read %s %dx%d in %.1fs" % (sheet, img.shape[1], img.shape[0], time.time() - t0))

    idx, pal, how = sheet_index(sheet, img, leg)
    del img
    print("palette: %s, %d entries" % (how, len(pal)))

    if a.holdout:
        tr = signatures(idx, leg, len(pal), half="train")
        te = signatures(idx, leg, len(pal), half="test")
        sim = te @ tr.T
        codes = [u.code for u in leg.units]
        pred = sim.argmax(1)
        print("swatch held-out accuracy %.3f" % (pred == np.arange(len(codes))).mean())
        for i, j in enumerate(pred):
            if i != j:
                print("  %-6s -> %s" % (codes[i], codes[j]))
        return 0

    sig = signatures(idx, leg, len(pal))
    t = time.time()
    best, margin = classify(idx, sig, len(pal), win, stride, a.min_margin)
    print("classified %dx%d (win %d, stride %d) in %.0fs"
          % (best.shape[1], best.shape[0], win, stride, time.time() - t))
    del idx

    os.makedirs(WORK, exist_ok=True)
    lab = os.path.join(WORK, "%s_labels.tif" % sheet)
    _, unclaimed = write_label_tif(sheet, best, margin, stride, lab, a.min_margin)
    print("labels -> %s (%.1f%% unclaimed)" % (lab, 100 * unclaimed))
    del best, margin

    warped = os.path.join(WORK, "%s_labels_geo.tif" % sheet)
    t = time.time()
    warp_labels(sheet, lab, stride, warped)
    print("warped -> %s in %.0fs" % (warped, time.time() - t))

    out = os.path.join(OUT_DIR, "%s_units.geojson" % sheet)
    t = time.time()
    _, n = polygonize(sheet, warped, leg, out, a.min_area_km2)
    print("polygonised %d units -> %s in %.0fs (%.1f MB)"
          % (n, out, time.time() - t, os.path.getsize(out) / 1e6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
