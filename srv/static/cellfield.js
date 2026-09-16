/*
 * CellField — one renderer for every per-cell field on the season-front grid.
 *
 * The season SPEED map and the EARLY-BURN GROUND are both values on the same
 * 2.5 km grid scripts/fire_front.py measures the front on. Before 2026-09-15
 * the speed came as a server PNG (palette inverted per pixel for the tip) and
 * the early ground was a static figure. Both are now the same thing:
 *
 *   wire     grid {x0, y0, res, nx, ny} + either a dense uint8 array (base64,
 *            one byte per cell, row 0 = SOUTH) or a sparse list of cells
 *   client   colour per cell (a paint function the layer owns) → an offscreen
 *            canvas → a MapLibre CANVAS source drawn with NEAREST resampling,
 *            so a cell is a true square that scales with zoom, never a
 *            pixel-sized symbol pasted on the map. A canvas source, not an
 *            image source: an image source takes a data-URL PNG (encode,
 *            then an async decode that can land out of order under a
 *            scrubbing playhead); the canvas is uploaded to the GPU
 *            synchronously the frame it is drawn (play() then pause() on
 *            the source — pause() uploads once and stops re-reading).
 *   probe    lng/lat → cell index by the grid's own arithmetic, so a tip
 *            reads the value under the pointer from the array it drew from
 *
 * Rows are resampled to MERCATOR when the canvas is written: an image source
 * is a quad in mercator, the grid's rows are equal steps in latitude, and over
 * an AOI spanning 7° of latitude the difference is ~4 cells (10 km) mid-image
 * — the same seam anim.js's heatBuffer removes for the fire grid. A field
 * drawn one row off is a field whose squares lie about their footprint.
 *
 * Re-rendering is the only way a raster changes, so it is cheap by design:
 * a paint pass over an XSA-sized grid (93k cells) is a few ms at scale 1; the
 * caller decides when (a zoom band crossed, the playhead moved) and picks the
 * scale (px per cell): 1 for a dense field, more for a sparse one that wants
 * a rim on each square.
 */
(function () {
    'use strict';

    var BLANK_COORDS = [[0, 0.001], [0.001, 0.001], [0.001, 0], [0, 0]];
    // MapLibre binds a raster tile's texture with LINEAR_MIPMAP_NEAREST when
    // its size is a power of two, and a canvas texture has no mipmaps: a
    // 128×64 field draws as an opaque black quad. So a canvas here is never
    // POT on either side (an extra transparent column/row, and the quad's
    // coordinates stretched by the same fraction so a cell keeps its
    // footprint). 1×1 is POT too — the blank is 3×3.
    function merc(lat) { return Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)); }
    function unmerc(y) { return (2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180 / Math.PI; }

    function b64u8(s) {
        var bin = atob(s), out = new Uint8Array(bin.length);
        for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
        return out;
    }
    function isPOT(n) { return n > 0 && (n & (n - 1)) === 0; }
    function blank(canvas) {
        canvas.width = 3; canvas.height = 3;
        canvas.getContext('2d').clearRect(0, 0, 3, 3);
    }
    // Push the canvas' current pixels to the GPU, then stop re-reading it
    // every frame (a static field costs nothing between repaints). pause()
    // uploads once — but only if the source already has a tile, so keep
    // playing until a render has both the tile and a texture of the
    // canvas' size (bounded: a source out of view never gets a tile).
    function upload(map, src) {
        if (!src || !src.play) return;
        if (src.__cfOff) { map.off('render', src.__cfOff); src.__cfOff = null; }
        src.play();
        var tries = 0;
        function onRender() {
            var tx = src.texture, ok = tx && tx.size && tx.size[0] === src.canvas.width && tx.size[1] === src.canvas.height &&
                Object.keys(src.tiles || {}).length > 0;
            if (ok || ++tries > 40) { map.off('render', onRender); src.__cfOff = null; if (src.pause) src.pause(); }
        }
        src.__cfOff = onRender;
        map.on('render', onRender);
    }

    /* A field: one canvas source + one raster layer. `beforeId` places the
     * layer under the lines that must stay legible over it. */
    function create(map, id, opts) {
        opts = opts || {};
        var SRC = id + '-src', LYR = id;
        var grid = null, dense = null, sparse = null, sparseIndex = null;
        var canvas = document.createElement('canvas');
        blank(canvas);
        var lastKey = null;

        function ensure() {
            if (!map || !map.getStyle()) return false;
            if (!map.getSource(SRC)) map.addSource(SRC, { type: 'canvas', canvas: canvas, coordinates: BLANK_COORDS, animate: false });
            if (!map.getLayer(LYR)) {
                var before = opts.beforeId && map.getLayer(opts.beforeId) ? opts.beforeId : undefined;
                map.addLayer({ id: LYR, type: 'raster', source: SRC,
                    minzoom: opts.minzoom || 0,
                    paint: { 'raster-opacity': opts.opacity == null ? 1 : opts.opacity, 'raster-resampling': 'nearest', 'raster-fade-duration': 0 } }, before);
            }
            return true;
        }
        function clear() {
            var s = map && map.getSource(SRC);
            blank(canvas);
            if (s) { s.setCoordinates(BLANK_COORDS); upload(map, s); }
            lastKey = null;
        }
        function setGrid(g) { grid = g; sparseIndex = null; }
        function setDense(u8) { dense = u8 instanceof Uint8Array ? u8 : (u8 ? b64u8(u8) : null); sparse = null; sparseIndex = null; }
        function setSparse(cells) {
            sparse = cells || []; dense = null;
            sparseIndex = {};
            for (var i = 0; i < sparse.length; i++) sparseIndex[sparse[i].iy * grid.nx + sparse[i].ix] = i;
        }
        /* paint(cell) → [r,g,b,a] (0..255) or null; a 5th element 0..1
         * marks the cell HOT (flaring): it fills its gutter and blooms a
         * soft ring of its own colour into the empty cells around it, so
         * an igniting square glows past its edge and cools back into the
         * grid. For a dense field `cell` is {i, ix, iy, v}; for a sparse
         * one it is the cell object itself.
         *
         * Look (o):
         *   scale   px per cell (1 for a field, 4–8 to see squares)
         *   gutter  at scale ≥ 4, draw the last pixel column/row of each
         *           cell at this share of its alpha (0 = a clear hairline,
         *           0.5 = a soft one for a dense field): the squares sit on a grid
         *           and touching cells read as tiles of one field, not as
         *           outlined sprites (the dark rim that preceded it drew
         *           every square as a game tile)
         *   block   coarsen a SPARSE field to k×k-cell blocks (aligned to
         *           the grid, the brightest member colours the block): the
         *           prototype's 5 km cell at the zoom where a 2.5 km cell
         *           is two pixels, so the pattern survives as organised
         *           blocks instead of a dust of specks
         *   key     skip the repaint if unchanged */
        function render(paint, o) {
            o = o || {};
            if (!grid || !ensure()) return false;
            if (o.key != null && o.key === lastKey) return true;
            lastKey = o.key == null ? null : o.key;
            var S = Math.max(1, Math.round(o.scale || 1));
            var nx = grid.nx, ny = grid.ny, W = nx * S, H = ny * S;
            if (W * H > 12e6) { S = 1; W = nx; H = ny; }
            var CW = isPOT(W) ? W + 1 : W, CH = isPOT(H) ? H + 1 : H;   // never POT (see isPOT)
            canvas.width = CW; canvas.height = CH;
            var ctx = canvas.getContext('2d');
            var img = ctx.createImageData(CW, CH), d = img.data;
            // output row j (top-down) → grid row iy, uniform in mercator
            var N = grid.y0 + grid.res * ny, Sy = grid.y0, mN = merc(N), mS = merc(Sy);
            var rowIy = new Int32Array(H);
            for (var j = 0; j < H; j++) {
                var lat = unmerc(mN + (mS - mN) * (j + 0.5) / H);
                var iy = Math.floor((lat - grid.y0) / grid.res);
                rowIy[j] = iy < 0 ? 0 : iy >= ny ? ny - 1 : iy;
            }
            // colour per grid cell, computed once per cell (not per pixel)
            var col = new Uint8ClampedArray(nx * ny * 4), has = new Uint8Array(nx * ny), hot = new Float32Array(nx * ny);
            var any = false;
            var K = sparse && o.block > 1 ? Math.round(o.block) : 1;
            function put(ci, c) {
                if (has[ci] && col[ci * 4 + 3] >= c[3] && hot[ci] >= (c[4] || 0)) return;
                col[ci * 4] = c[0]; col[ci * 4 + 1] = c[1]; col[ci * 4 + 2] = c[2]; col[ci * 4 + 3] = c[3]; has[ci] = 1; hot[ci] = c[4] > 0 ? Math.min(1, c[4]) : 0; any = true;
            }
            if (dense) {
                for (var i = 0; i < nx * ny; i++) {
                    var v = dense[i]; if (!v) continue;
                    var c = paint({ i: i, ix: i % nx, iy: (i / nx) | 0, v: v });
                    if (!c || !c[3]) continue;
                    put(i, c);
                }
            } else if (sparse) {
                for (var k = 0; k < sparse.length; k++) {
                    var cl = sparse[k];
                    var cc = paint(cl);
                    if (!cc || !cc[3]) continue;
                    if (K === 1) { put(cl.iy * nx + cl.ix, cc); continue; }
                    var bx = Math.floor(cl.ix / K) * K, by = Math.floor(cl.iy / K) * K;
                    for (var yy = by; yy < by + K && yy < ny; yy++) for (var xx = bx; xx < bx + K && xx < nx; xx++) put(yy * nx + xx, cc);
                }
            }
            if (!any) { clear(); return true; }
            // bloom: a hot cell lends a ring of its colour to the EMPTY
            // cells around it, alpha scaled by how hot it is — the flare.
            // Never over a cell that has its own answer.
            var bcol = null, bhas = null;
            for (var q = 0; q < nx * ny; q++) {
                if (!hot[q]) continue;
                if (!bcol) { bcol = new Uint8ClampedArray(col.length); bhas = new Uint8Array(has.length); }
                var qx = q % nx, qy = (q / nx) | 0;
                for (var dy = -1; dy <= 1; dy++) for (var dx = -1; dx <= 1; dx++) {
                    if (!dx && !dy) continue;
                    var yy2 = qy + dy, xx2 = qx + dx;
                    if (yy2 < 0 || xx2 < 0 || yy2 >= ny || xx2 >= nx) continue;
                    var ni = yy2 * nx + xx2;
                    if (has[ni]) continue;
                    var na = col[q * 4 + 3] * hot[q] * (dx && dy ? 0.3 : 0.5);
                    if (na > bcol[ni * 4 + 3]) { bcol[ni * 4] = col[q * 4]; bcol[ni * 4 + 1] = col[q * 4 + 1]; bcol[ni * 4 + 2] = col[q * 4 + 2]; bcol[ni * 4 + 3] = na; bhas[ni] = 1; }
                }
            }
            if (bcol) for (var hi = 0; hi < has.length; hi++) if (bhas[hi]) { has[hi] = 1; col.set(bcol.subarray(hi * 4, hi * 4 + 4), hi * 4); }
            var gutter = o.gutter != null && o.gutter !== false && S >= 4, gA = gutter ? Math.max(0, Math.min(1, +o.gutter || 0)) : 1;
            for (var jj = 0; jj < H; jj++) {
                var gy = rowIy[jj];
                var lastRowOfCell = gutter && (jj === H - 1 || rowIy[jj + 1] !== gy);
                var rowOff = jj * CW * 4, base = gy * nx;
                for (var ix = 0; ix < nx; ix++) {
                    var gi = base + ix;
                    if (!has[gi]) continue;
                    var r = col[gi * 4], g = col[gi * 4 + 1], b = col[gi * 4 + 2], a = col[gi * 4 + 3], h = hot[gi];
                    // a hot cell keeps its gutter (it flares past the grid);
                    // a warm one keeps part of it
                    var ga = h > 0 ? a * Math.max(gA, Math.min(1, h * 1.5)) : a * gA;
                    for (var px = 0; px < S; px++) {
                        var edge = gutter && (lastRowOfCell || px === S - 1);
                        var off = rowOff + (ix * S + px) * 4;
                        d[off] = r; d[off + 1] = g; d[off + 2] = b; d[off + 3] = edge ? ga : a;
                    }
                }
            }
            ctx.putImageData(img, 0, 0);
            // the quad covers the padded canvas: stretch east / south by the
            // padding's share so every drawn pixel lands on its cell
            var Wd = grid.x0, E = grid.x0 + grid.res * nx * (CW / W);
            var Sy2 = unmerc(mN + (mS - mN) * (CH / H));
            var src = map.getSource(SRC);
            if (src) { src.setCoordinates([[Wd, N], [E, N], [E, Sy2], [Wd, Sy2]]); upload(map, src); }
            return true;
        }
        /* The cell under a point, by the grid's own arithmetic (floor from
         * the south/west edge — never by scaling the bbox, whose last bit
         * once moved an edge point one row). null outside or where the
         * field has nothing. */
        function at(lng, lat) {
            if (!grid) return null;
            var ix = Math.floor((lng - grid.x0) / grid.res), iy = Math.floor((lat - grid.y0) / grid.res);
            if (ix < 0 || iy < 0 || ix >= grid.nx || iy >= grid.ny) return null;
            var i = iy * grid.nx + ix;
            if (dense) return dense[i] ? { i: i, ix: ix, iy: iy, v: dense[i] } : null;
            if (sparse && sparseIndex) { var k = sparseIndex[i]; return k == null ? null : sparse[k]; }
            return null;
        }
        function setVisible(on) {
            if (!ensure()) return;
            map.setLayoutProperty(LYR, 'visibility', on ? 'visible' : 'none');
        }
        function setOpacity(v) { if (map.getLayer(LYR)) map.setPaintProperty(LYR, 'raster-opacity', v); }
        // Cell footprint in km² at the grid's mid-latitude — the unit the
        // strip counts in ("N cells · X km²").
        function cellKm2() {
            if (!grid) return 0;
            var lat = grid.y0 + grid.res * grid.ny / 2;
            return grid.res * 111 * grid.res * 111 * Math.cos(lat * Math.PI / 180);
        }
        function cellsIn(bounds) {
            if (!grid || !sparse) return [];
            var out = [];
            for (var k = 0; k < sparse.length; k++) {
                var c = sparse[k], w = grid.x0 + grid.res * c.ix, s = grid.y0 + grid.res * c.iy;
                if (w + grid.res >= bounds[0] && w <= bounds[2] && s + grid.res >= bounds[1] && s <= bounds[3]) out.push(c);
            }
            return out;
        }
        function bboxOf(c) {
            var w = grid.x0 + grid.res * c.ix, s = grid.y0 + grid.res * c.iy;
            return [w, s, w + grid.res, s + grid.res];
        }
        return { id: LYR, srcId: SRC, ensure: ensure, clear: clear, setGrid: setGrid, setDense: setDense, setSparse: setSparse,
            render: render, at: at, setVisible: setVisible, setOpacity: setOpacity, cellKm2: cellKm2, cellsIn: cellsIn, bboxOf: bboxOf,
            grid: function () { return grid; }, sparse: function () { return sparse; }, hasData: function () { return !!(dense || (sparse && sparse.length)); },
            invalidate: function () { lastKey = null; } };
    }

    window.CellField = { create: create, b64u8: b64u8 };
})();
