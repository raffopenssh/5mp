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

    Returns centres in **RGB** order, like `exact_palette` - the scan is read
    BGR by OpenCV, and `palette_lut` unpacks its colour cube as RGB.  A
    silently BGR palette still classifies (the mapping is deterministic, so
    the histograms remain discriminative) but every pixel is assigned to the
    wrong centre, which is only visible as accuracy left on the table.
    """
    px = np.vstack([img[y:y + h, x:x + w].reshape(-1, 3)
                    for (x, y, w, h) in (u.box for u in leg.units)]).astype(np.float32)
    px = px[::max(1, len(px) // 400000)]
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
    _, _, cen = cv2.kmeans(px, k, None, crit, seed, cv2.KMEANS_PP_CENTERS)
    return cen.astype(np.float32)[:, ::-1].copy()


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
def swatch_hist(idx, u, npal, half=None):
    x, y, w, h = u.box
    if half == "train":
        w = w // 2
    elif half == "test":
        x, w = x + w // 2, w - w // 2
    v = idx[y:y + h, x:x + w].ravel()
    return np.bincount(v, minlength=npal).astype(np.float64)


def signatures(idx, leg, npal, half=None, classes=None):
    """sqrt of the palette-index histogram of each CLASS.

    Square-rooted here so the classifier's inner loop is a dot product: the
    Bhattacharyya coefficient of two histograms is <sqrt(p), sqrt(q)>.

    A class is one or more units (see `resolve_classes`); its histogram pools
    every member swatch, so a merged class is measured over all the ink that
    prints it rather than over an arbitrarily chosen member.
    """
    if classes is None:
        classes = [[i] for i in range(len(leg.units))]
    sig = []
    for members in classes:
        hh = np.zeros(npal, np.float64)
        for i in members:
            hh += swatch_hist(idx, leg.units[i], npal, half)
        hh /= max(hh.sum(), 1.0)
        sig.append(np.sqrt(hh).astype(np.float32))
    return np.stack(sig)


def paper_signature(pal, sheet, npal):
    """A signature for unprinted paper, so paper-like units cannot claim it.

    `Legend.paper_like()` names the units whose ink is within `PAPER_DIST` of
    the sheet's paper tone - CAR's migmatite `M` and its recent alluvium `a2`.
    Colour cannot find them, and worse, *they* find the paper: every blank
    margin, every legend box and every inset would come back as migmatite.

    The fix is to let paper compete as a class of its own and then discard it,
    which is "resolve by exclusion" done in the one place that can afford to be
    exact.  Its signature is synthetic on purpose: pure paper is exactly one
    palette index (the entry nearest PAPER_RGB), so its window histogram is
    that index's indicator, whereas a paper-like *unit* is paper plus its own
    sparse screen dots.  Nothing needs to be sampled and no blank region has to
    be hand-picked - which matters, because picking one by eye is exactly the
    kind of unrecorded input this pipeline refuses elsewhere.

    Paper wins only where there is no screen at all, so a pale unit keeps its
    dots and its class; and paper losing narrowly is a margin drop, i.e. also
    unclaimed. Both failure directions land on "unclaimed", never on a wrong
    formation.
    """
    from legend import PAPER_RGB
    p = np.array(PAPER_RGB[sheet], np.float32)
    d = ((pal[:, 0] - p[0]) ** 2 + (pal[:, 1] - p[1]) ** 2 + (pal[:, 2] - p[2]) ** 2)
    hh = np.zeros(npal, np.float32)
    hh[int(d.argmin())] = 1.0
    return hh


def window_holdout(idx, leg, npal, win, classes=None, n=400, seed=7,
                   min_margin=MIN_MARGIN):
    """Hold-out at the size the classifier actually sees. The real measurement.

    The swatch hold-out (`holdout`) trains on half a legend box and tests on the
    other half - both a few hundred pixels wide. It reports 1.000 on both
    sheets, and it is *lying about the map body*: there the decision is made
    from a `win` x `win` window, 17-33 px, whose histogram is a small sample.
    Inks whose signatures differ by 0.13 in Bhattacharyya distance are trivially
    separable over 180 px and pure noise over 33.

    That gap is not a rounding error, it is a whole formation. CAR's GO and GC2
    scored a perfect swatch hold-out while the Mouka-Ouadda plateau - an area
    the size of Belgium - came out **white**: the two classes scored within
    `--min-margin` of each other on every pixel of it, so instead of being
    labelled wrongly, it was dropped. Near-identical classes do not swap, they
    **cancel**.

    So: train on half of each swatch, test on random `win`-sized patches drawn
    from the other half, and report both numbers that matter -

      claim rate  fraction of patches whose top-two margin clears min_margin
                  (the rest are the white holes)
      accuracy    of the claimed ones, how many got their own class

    Returns (claim_rate, accuracy, confusion, drawn) where confusion[(i, j)]
    counts patches of class i whose best match was class j, and drawn[i] is how
    many patches of class i were tested - so a confusion count can be read as a
    rate of the class, not of the other confusions.
    """
    if classes is None:
        classes = [[i] for i in range(len(leg.units))]
    tr = signatures(idx, leg, npal, half="train", classes=classes)
    rng = np.random.default_rng(seed)
    claimed = 0
    correct = 0
    total = 0
    confusion = {}
    drawn = [0] * len(classes)
    for ci, members in enumerate(classes):
        for i in members:
            x, y, w, h = leg.units[i].box
            x, w = x + w // 2, w - w // 2          # the test half
            ww = min(win, w)
            hh = min(win, h)
            per = max(1, n // len(members))
            for _ in range(per):
                px = x + int(rng.integers(0, max(1, w - ww + 1)))
                py = y + int(rng.integers(0, max(1, h - hh + 1)))
                v = idx[py:py + hh, px:px + ww].ravel()
                q = np.bincount(v, minlength=npal).astype(np.float32)
                q /= max(q.sum(), 1)
                sc = tr @ np.sqrt(q)
                order = np.argsort(-sc)
                total += 1
                drawn[ci] += 1
                if sc[order[0]] - sc[order[1]] >= min_margin:
                    claimed += 1
                    if order[0] == ci:
                        correct += 1
                    else:
                        confusion[(ci, int(order[0]))] = confusion.get((ci, int(order[0])), 0) + 1
                else:
                    # a drop still names the pair that cancelled: that is the
                    # merge candidate, and counting it is the only way a class
                    # that never wins anywhere can be seen at all
                    a, b = int(order[0]), int(order[1])
                    other = b if a == ci else a
                    if other != ci:
                        confusion[(ci, other)] = confusion.get((ci, other), 0) + 1
    return (claimed / max(total, 1),
            correct / max(claimed, 1),
            confusion, drawn)


def holdout(idx, leg, npal, classes=None):
    """Train on half of each swatch, test on the other. Returns (acc, misses).

    This is the only measurement that says whether a change to the palette,
    the window or the class list helped, so every tuning knob is judged here
    and nowhere by eye.
    """
    tr = signatures(idx, leg, npal, half="train", classes=classes)
    te = signatures(idx, leg, npal, half="test", classes=classes)
    pred = (te @ tr.T).argmax(1)
    truth = np.arange(len(pred))
    return float((pred == truth).mean()), [(i, int(j)) for i, j in enumerate(pred) if i != j]


def resolve_classes(idx, leg, npal, win, min_margin=MIN_MARGIN, max_rounds=25,
                   confuse_frac=0.15, verbose=True):
    """Group units the sheet does not separate AT THE CLASSIFIER'S WINDOW SIZE.

    `Legend.merge_groups()` says which inks average to the same RGB; the whole
    point of the screen-histogram classifier is that most of those *are* still
    separable, because the dot patterns differ.  So the merge list is a
    hypothesis, not the answer.  The answer is `window_holdout`, which asks the
    question the map body asks: from a `win` x `win` patch, which class is
    this?

    Why merging matters beyond labelling: two classes with near-identical
    signatures do not merely swap, they **cancel**.  Every patch of that ink
    scores nearly the same for both, so the `--min-margin` test drops it and
    the formation disappears from the map rather than appearing under the wrong
    name.  That is what left CAR's Mouka-Ouadda plateau - an area the size of
    Belgium - as a white hole while the swatch hold-out read 1.000: GO and GC2
    were cancelling, not competing.

    Iterated, because merging is not idempotent in the useful direction: once
    GO and GC2 are one class, the pooled signature can start beating a third
    neighbour that both were previously losing to.  Each round re-measures.

    Candidate pairs are restricted to what the legend already calls the same
    ink.  A window-scale confusion between two visibly different colours is a
    signal to raise `win`, not to merge two real formations.

    A merged class carries **every** member code and is labelled with all of
    them - never an arbitrary pick, because the sheet genuinely does not say
    which one a given patch is.
    """
    n = len(leg.units)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        a, b = find(i), find(j)
        if a != b:
            parent[max(a, b)] = min(a, b)

    by_code = {u.code: i for i, u in enumerate(leg.units)}
    candidate = set()
    for g in leg.merge_groups():
        ids = [by_code[c] for c in g if c in by_code]
        for a in ids:
            for b in ids:
                if a != b:
                    candidate.add((a, b))

    def current_classes():
        groups = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)
        return [groups[k] for k in sorted(groups)]

    merged = []
    for _ in range(max_rounds):
        classes = current_classes()
        claim, acc, conf, drawn = window_holdout(idx, leg, npal, win, classes,
                                                 min_margin=min_margin)
        name = lambda ci: "/".join(leg.units[i].code for i in classes[ci])  # noqa: E731
        did = False
        # `confuse_frac` is a fraction OF THE CLASS's own patches, not of the
        # other confusions: a class confused with three neighbours a third of
        # the time each is in real trouble, and normalising by the confusions
        # would hide that behind 0.33 each.
        for (a, b), c in sorted(conf.items(), key=lambda t: -t[1]):
            if c / max(drawn[a], 1) < confuse_frac:
                continue
            pairs = [(i, j) for i in classes[a] for j in classes[b]]
            if not any(p in candidate for p in pairs):
                if verbose:
                    print("  NOTE: %s <-> %s confused %d/%d at win=%d but the legend "
                          "calls them different inks; not merging"
                          % (name(a), name(b), c, drawn[a], win))
                continue
            if verbose:
                print("  merge %s + %s (%d/%d patches at win=%d)"
                      % (name(a), name(b), c, drawn[a], win))
            merged.append((name(a), name(b)))
            union(classes[a][0], classes[b][0])
            did = True
            break
        if not did:
            return current_classes(), merged, claim, acc
    classes = current_classes()
    claim, acc, _, _ = window_holdout(idx, leg, npal, win, classes, min_margin=min_margin)
    return classes, merged, claim, acc


def class_props(leg, members):
    """The public identity of a class: every member code, never a pick.

    A merged class takes the **union** of its members' commodity affinities at
    the highest weight any member carries, and each `why` is prefixed with the
    member code it came from.  Union, not intersection, because the sheet does
    not say which member a given patch is - if either could host gold, then
    this ground could.  The prefix is what stops that being a quiet upgrade:
    a reader sees "GC2: the principal secondary diamond reservoir" and knows
    the claim is about one of the two inks, not both.
    """
    us = [leg.units[i] for i in members]
    aff = {}
    for u in us:
        for c in u.commodities:
            row = dict(c)
            if len(us) > 1:
                row["why"] = "%s: %s" % (u.code, c["why"])
            cur = aff.get(c["commodity"])
            if cur is None or row["weight"] > cur["weight"]:
                aff[c["commodity"]] = row
    affinity = sorted(aff.values(), key=lambda c: (-c["weight"], c["commodity"]))
    return dict(
        sheet=leg.sheet,
        code="/".join(u.code for u in us),
        codes=[u.code for u in us],
        # Two members often carry the *same* printed description (CAR's GC2
        # and GO are both "Gres ... fluvio-lacustres", differing only in which
        # plateau they name), so joining blindly prints the sentence twice.
        name=" / ".join(dict.fromkeys(u.name for u in us)),
        group=us[0].group if len({u.group for u in us}) == 1 else " / ".join(
            dict.fromkeys(u.group for u in us)),
        color=us[0].hex,
        merged=len(us) > 1,
        commodities=[c["commodity"] for c in affinity],
        affinity=affinity,
    )


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
def write_label_tif(sheet, best, margin, stride, path, min_margin=MIN_MARGIN, drop=()):
    """A single-band label raster in SCAN space, 0 = unclaimed.

    Codes are 1-based so 0 can mean "no unit" - unclaimed pixels, and later
    everything outside the cutline.  A pixel whose two best units are within
    `min_margin` is dropped rather than guessed: on a screen-printed sheet the
    ambiguous pixels are mostly line work and lettering, and a confident wrong
    label there would draw a hairline of some unrelated formation along every
    contact.

    `drop` names class indices that competed but are not units - the paper
    class.  They are written as 0 for the same reason: paper is a real answer
    ("nothing is printed here") and its correct rendering is a hole.
    """
    import rasterio
    from rasterio.transform import Affine
    lab = (best.astype(np.uint16) + 1)
    lab[margin < min_margin] = 0
    for i in drop:
        lab[lab == i + 1] = 0
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


def polygonize(sheet, warped, leg, classes, out_geojson, min_area_km2=1.0, simplify_m=250,
               quality=None):
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

    props = [class_props(leg, m) for m in classes]
    by_class = {}
    with rasterio.open(warped) as d:
        arr = d.read(1)
        tr = d.transform
    lat = abs(tr.f + tr.e * arr.shape[0] / 2)
    km_per_deg_x = 111.32 * max(0.1, np.cos(np.radians(lat)))
    min_area_deg2 = min_area_km2 / (km_per_deg_x * 110.57)
    simplify_deg = simplify_m / 111320.0

    for geom, val in rfeatures.shapes(arr, mask=(arr > 0), transform=tr):
        v = int(val)
        if v < 1 or v > len(props):
            continue
        g = sg.shape(geom)
        if g.area < min_area_deg2:
            continue
        by_class.setdefault(v - 1, []).append(g)

    feats = []
    for i, p in enumerate(props):
        polys = by_class.get(i)
        if not polys:
            continue
        g = unary_union(polys).simplify(simplify_deg, preserve_topology=True)
        if g.is_empty:
            continue
        feats.append(dict(
            type="Feature",
            properties=dict(p, area_km2=round(g.area * km_per_deg_x * 110.57, 1)),
            geometry=sg.mapping(g)))
    json.dump(dict(type="FeatureCollection", features=feats), open(out_geojson, "w"))
    write_catalogue(leg, feats, quality=quality)
    return out_geojson, len(feats)


def write_catalogue(leg, feats, quality=None):
    """The small, committed description of what the big GeoJSON contains.

    The server's `/api/geomap` reads this, not the 40 MB FeatureCollection and
    not `legend_*.json`: the legend is the *printed* unit list, while what the
    map actually carries is the **class** list, which merges the units the
    sheet does not separate and drops any that never occur.  Serving the legend
    instead would offer toggles for classes that cannot be drawn.

    Small enough to commit, and worth committing: it is the record of which
    merges the hold-out forced on this build.
    """
    classes = [dict(p["properties"]) for p in feats]
    commodities = {}
    for c in classes:
        for aff in c["affinity"]:
            commodities.setdefault(aff["commodity"], []).append(
                dict(code=c["code"], weight=aff["weight"], why=aff["why"],
                     area_km2=c["area_km2"]))
    for v in commodities.values():
        v.sort(key=lambda t: (-t["weight"], -t["area_km2"]))
    groups = list(dict.fromkeys(c["group"] for c in classes))
    path = os.path.join(OUT_DIR, "%s_classes.json" % leg.sheet)
    sh = SHEETS[leg.sheet]
    json.dump(dict(sheet=leg.sheet,
                   title=sh["title"], short=sh["short"], year=sh["year"],
                   publisher=sh["publisher"], scale=sh["scale"],
                   source_url=sh["source_url"], countries=sh["countries"],
                   n_classes=len(classes), n_units=len(leg.units),
                   quality=quality,
                   groups=groups,
                   commodities={k: commodities[k] for k in sorted(commodities)},
                   classes=classes),
              open(path, "w"), indent=1)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sheet", choices=sorted(TUNING))
    ap.add_argument("--stride", type=int)
    ap.add_argument("--win", type=int)
    ap.add_argument("--min-margin", type=float, default=MIN_MARGIN)
    ap.add_argument("--min-area-km2", type=float, default=1.0)
    ap.add_argument("--holdout", action="store_true",
                    help="train on half of each swatch, report accuracy on the other, exit")
    ap.add_argument("--repolygonize", action="store_true",
                    help="reuse the existing warped label raster; only redo "
                         "polygonisation and the catalogue. For changes to "
                         "labelling or metadata, not to classification.")
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

    codes = [u.code for u in leg.units]
    if a.holdout:
        acc, miss = holdout(idx, leg, len(pal))
        print("swatch held-out accuracy %.3f -- the OPTIMISTIC number, measured"
              " over a whole legend box" % acc)
        for i, j in miss:
            print("  %-6s -> %s" % (codes[i], codes[j]))
        claim, wacc, _, _ = window_holdout(idx, leg, len(pal), win, min_margin=a.min_margin)
        print("window (%dpx) hold-out before merging: claim %.3f, accuracy %.3f"
              % (win, claim, wacc))
        classes, merged, claim2, wacc2 = resolve_classes(
            idx, leg, len(pal), win, a.min_margin)
        print("%d units -> %d classes: claim %.3f, accuracy %.3f"
              % (len(leg.units), len(classes), claim2, wacc2))
        return 0

    classes, merged, claim, acc = resolve_classes(idx, leg, len(pal), win, a.min_margin)
    print("%d units -> %d classes; window hold-out claim %.3f, accuracy %.3f"
          % (len(leg.units), len(classes), claim, acc))

    warped = os.path.join(WORK, "%s_labels_geo.tif" % sheet)
    if not a.repolygonize:
        sig = signatures(idx, leg, len(pal), classes=classes)
        # paper competes as an extra class and is discarded; see paper_signature()
        sig = np.vstack([sig, np.sqrt(paper_signature(pal, sheet, len(pal)))[None, :]])
        paper_cls = len(sig) - 1
        t = time.time()
        best, margin = classify(idx, sig, len(pal), win, stride, a.min_margin)
        print("classified %dx%d (win %d, stride %d) in %.0fs"
              % (best.shape[1], best.shape[0], win, stride, time.time() - t))
        del idx

        os.makedirs(WORK, exist_ok=True)
        lab = os.path.join(WORK, "%s_labels.tif" % sheet)
        _, unclaimed = write_label_tif(sheet, best, margin, stride, lab, a.min_margin,
                                       drop=(paper_cls,))
        print("labels -> %s (%.1f%% unclaimed)" % (lab, 100 * unclaimed))
        del best, margin

        t = time.time()
        warp_labels(sheet, lab, stride, warped)
        print("warped -> %s in %.0fs" % (warped, time.time() - t))
    else:
        # The label raster encodes CLASS INDICES, so reusing it is only valid
        # if the class list came out identical. resolve_classes() is
        # deterministic given the same scan, palette, window and margin, so
        # this holds for a labelling or metadata change and NOT for a change
        # to any of those - hence the check rather than a promise.
        del idx
        prev = os.path.join(OUT_DIR, "%s_classes.json" % sheet)
        if os.path.exists(prev):
            old = [c["codes"] for c in json.load(open(prev))["classes"]]
            new = [class_props(leg, m)["codes"] for m in classes]
            missing = [c for c in old if c not in new]
            if missing:
                raise SystemExit("--repolygonize: the class list changed (%s no longer "
                                 "exists); rerun the full pipeline" % missing)
        print("reusing %s" % warped)

    out = os.path.join(OUT_DIR, "%s_units.geojson" % sheet)
    t = time.time()
    _, n = polygonize(sheet, warped, leg, classes, out, a.min_area_km2,
                      quality=dict(window_px=win, claim_rate=round(claim, 4),
                                   accuracy=round(acc, 4), stride=stride,
                                   min_margin=a.min_margin,
                                   merged=[list(m) for m in merged]))
    print("polygonised %d units -> %s in %.0fs (%.1f MB)"
          % (n, out, time.time() - t, os.path.getsize(out) / 1e6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
