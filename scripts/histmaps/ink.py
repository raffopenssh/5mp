"""Turn a scanned line map into a transparent ink overlay, by vectorizing it.

These sheets are **monochrome line work** -- measured across six sheets, the
fraction of pixels whose hue departs from the paper's own hue is 0.0000. The
only "colour" is the cream of aged paper. So greyness carries no information:
a pale stroke is a faded stroke, not a lighter feature.

The previous tone-only approach failed because ink and paper grain are not
separable per pixel -- a grain speck and a contour line are both "a bit darker
than the neighbourhood". What separates them is *shape*: ink is continuous and
extended, grain is isolated and tiny. So the decision is made geometrically:
binarize generously, then trace with potrace and let its speckle suppression
discard anything too small to be a printed line.

Three things this gets right that the tone version did not:

* **Hysteresis, not a single threshold.** A generous threshold catches the
  faint half of a fading stroke but also the darkest grain; a strict one is
  clean but drops whole words. Generous-threshold components are kept only if
  they contain a confident core pixel. Measured on a real blank-paper crop this
  scores exactly **zero** false ink while nearly doubling ink capture
  (0.041 -> 0.076) against the single threshold it replaces.
* **Real 8-bit alpha.** The old code emitted a soft alpha ramp, which GDAL then
  stored as a 1-bit internal mask because JPEG cannot carry an alpha band --
  silently promoting every alpha=1 grain wisp to fully opaque. That, not the
  threshold, is why the shipped sheet was 53% speckle. Antialiasing now comes
  from potrace's own coverage rendering and is stored losslessly (see georef():
  DEFLATE RGBA is ~30x *smaller* than the lossy JPEG was, because traced ink is
  a few percent coverage of one flat colour).
* **Flat ink colour.** Once vectorized there is no per-pixel tone left to
  preserve, and reproducing the scan's fade only makes the overlay muddy over a
  basemap. Ink is drawn as one near-black; line *weight* still carries the
  original stroke thickness because the trace follows the stroke outline.

No saturation rescue: on these sheets it only ever promoted yellowed paper.
"""
import numpy as np, cv2, subprocess, tempfile, os, shutil
from scipy import ndimage

# Thresholds are on the illumination-flattened image, where paper sits at ~255
# by construction, so they are absolute and transfer between sheets.
# Tuned on cs000029 (Hofrat en Nahas 1934) against a true blank-paper crop.
LO, HI = 195, 222      # confident-core / generous-extent
TURDSIZE = 30          # potrace: drop traced shapes smaller than this (px^2)
ALPHAMAX = 1.0         # potrace corner threshold
INK_RGB = (26, 22, 18) # near-black, faintly warm

# The paper model must span several line widths but stay well below the scale of
# real tonal variation, so it is a FRACTION OF IMAGE WIDTH, not a fixed pixel
# count. Tuning at preview size and running at 12000 px was the one bug that
# actually mattered here: a 31 px window on a 12 k scan models the grain itself
# as "paper", and every grain speck then survives as ink.
PAPER_FRAC = 0.02


def _odd(n, lo=3):
    n = int(round(n));  n += (n % 2 == 0)
    return max(lo, n)


def auto_radius(shape):
    return _odd(max(shape) * PAPER_FRAC)


def paper_model(gray, radius=None, blur=None):
    """Estimate the paper/background level behind the ink."""
    radius = radius or auto_radius(gray.shape)
    blur = blur or max(3.0, radius * 0.8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius, radius))
    return cv2.GaussianBlur(cv2.morphologyEx(gray, cv2.MORPH_CLOSE, k), (0, 0), blur)


def flatten(gray, radius=None):
    """Divide out the vignette / uneven paper tone. Paper -> ~255."""
    radius = radius or auto_radius(gray.shape)
    p = paper_model(gray, radius).astype(np.float32)
    return np.clip(gray.astype(np.float32) / np.maximum(p, 1.0) * 255.0, 0, 255)


def hysteresis(flatimg, lo=LO, hi=HI):
    """Generous threshold, kept only where it touches a confident core pixel."""
    lab, n = ndimage.label(flatimg < hi, structure=np.ones((3, 3), np.uint8))
    if n == 0:
        return np.zeros(flatimg.shape, bool)
    keep = np.zeros(n + 1, bool)
    keep[np.unique(lab[flatimg < lo])] = True
    keep[0] = False
    return keep[lab]


def trace(mask, turdsize=TURDSIZE, alphamax=ALPHAMAX):
    """Vectorize a boolean ink mask; return antialiased coverage in 0..1.

    potrace is the point of this step: -t discards traced shapes below an area,
    which is a decision about *shape* that no per-pixel rule can make. We render
    back through its own PGM backend so the returned coverage is the exact
    rasterisation of the curves trace_svg() would ship.
    """
    if not mask.any():
        return np.zeros(mask.shape, np.float32)
    d = tempfile.mkdtemp(prefix="ink")
    try:
        src, dst = os.path.join(d, "i.pbm"), os.path.join(d, "o.pgm")
        cv2.imwrite(src, np.where(mask, 0, 255).astype(np.uint8))   # potrace: black=ink
        subprocess.check_call(["potrace", "-b", "pgm", "-t", str(turdsize),
                               "-a", str(alphamax), "-u", "10", "-G", "2.2",
                               "-o", dst, src])
        g = cv2.imread(dst, cv2.IMREAD_GRAYSCALE)
        if g is None:
            raise RuntimeError("potrace produced no output")
        if g.shape != mask.shape:                    # potrace pads to whole px
            g = g[:mask.shape[0], :mask.shape[1]]
        return 1.0 - g.astype(np.float32) / 255.0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def trace_svg(mask, path, turdsize=TURDSIZE, alphamax=ALPHAMAX):
    """Same trace, written as SVG -- the vector artefact, for QGIS/Illustrator."""
    d = tempfile.mkdtemp(prefix="ink")
    try:
        src = os.path.join(d, "i.pbm")
        cv2.imwrite(src, np.where(mask, 0, 255).astype(np.uint8))
        subprocess.check_call(["potrace", "-b", "svg", "-t", str(turdsize),
                               "-a", str(alphamax), "-u", "10",
                               "-C", "#%02x%02x%02x" % INK_RGB, "-o", path, src])
        return path
    finally:
        shutil.rmtree(d, ignore_errors=True)


def ink_rgba(rgb, lo=LO, hi=HI, radius=None, turdsize=TURDSIZE, svg=None):
    """RGB uint8 -> (rgb uint8, alpha uint8) with the paper removed."""
    if rgb.ndim == 2:
        rgb = cv2.cvtColor(rgb, cv2.COLOR_GRAY2RGB)
    radius = radius or auto_radius(rgb.shape[:2])
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    mask = hysteresis(flatten(gray, radius), lo, hi)
    if svg:
        trace_svg(mask, svg, turdsize)
    alpha = trace(mask, turdsize)
    out = np.empty(rgb.shape, np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = INK_RGB
    return out, (alpha * 255).astype(np.uint8)


def apply_to_file(path, **kw):
    """In-place: add/replace the alpha band of an RGB(A) image file (cv2 formats).

    Returns the ink coverage fraction. It is computed here, from the array we
    just built, rather than by re-reading the file: once the 4th band is tagged
    as alpha, cv2 stops handing it back as a 4th channel and any re-read
    measurement silently becomes None.
    """
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError(f"cannot read {path}")
    keep = img[..., 3] if (img.ndim == 3 and img.shape[2] == 4) else None
    bgr = img[..., :3] if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    rgb, a = ink_rgba(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), **kw)
    if keep is not None:
        a = np.minimum(a, keep)   # never paint outside the sheet
    cv2.imwrite(path, np.dstack([cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), a]))
    return float((a > 128).mean())


def ink_fraction(path):
    """QA: fraction of the sheet that survived as ink. Sane range ~0.02-0.12.
    Near 0 means the trace ate the map; >0.25 means paper leaked through.
    Only valid before the alpha band is tagged -- prefer apply_to_file()'s
    return value."""
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is not None and img.ndim == 3 and img.shape[2] == 4:
        return float((img[..., 3] > 128).mean())
    return None
