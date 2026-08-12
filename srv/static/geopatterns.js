/*
 * geopatterns.js — FGDC lithology hatches, drawn to a canvas at runtime.
 *
 * WHY A PATTERN AT ALL
 *
 * A flat translucent fill on a dark basemap is ambiguous: the screenshots that
 * prompted this showed a geology drape that a user read as WATER, because a
 * pale blue-grey polygon is what our waterbody layer looks like. Colour alone
 * cannot fix that — any hue we pick is already spoken for by some other layer
 * at some opacity, and the overlay is deliberately drawn at 55% over an
 * arbitrary basemap.
 *
 * Texture is the way out, and it is also the industry answer: every printed
 * geological map in the world distinguishes rock by ORNAMENT as well as by
 * colour. Nothing else on this map is hatched, so a hatched polygon is always
 * the rock map, at any opacity, on any basemap, and for a colour-blind reader
 * too.
 *
 * WHICH ORNAMENT
 *
 * FGDC-STD-013-2006 §37 (the USGS/FGDC Digital Cartographic Standard for
 * Geologic Map Symbolization), simplified to the families a 1:1.5M sheet can
 * honestly claim:
 *
 *   alluvium    scattered dots + granules   (unconsolidated sediment)
 *   sandstone   even stipple                (FGDC 607, sand/sandstone)
 *   mudrock     horizontal dashes           (FGDC 620, shale/siltstone)
 *   carbonate   brick courses               (FGDC 627, limestone/dolomite)
 *   intrusive   plus signs                  (FGDC 717, granitic rock)
 *   volcanic    "v"s                        (FGDC 712, volcanic rock)
 *   metamorphic wavy dashes                 (FGDC 706, schist/gneiss)
 *   ultramafic  cross-hatch                 (FGDC 723, ultramafic)
 *   ironstone   dot-and-dash bands          (banded iron / ferruginous)
 *   mixed       diagonal hatch, sparse      (the sheet does not separate these)
 *
 * The ornament is drawn in a dark ink over the AGE colour, so one swatch says
 * both things at once — exactly as a printed legend does.
 *
 * Everything here is deterministic (a fixed pseudo-random for the stipple), so
 * the same class draws the same texture on every reload and in every export.
 */
(function () {
    'use strict';

    var TILE = 32;             // CSS px; drawn at 2x for retina
    var RATIO = 2;
    var cache = new Map();     // key -> {data, width, height}

    function hexToRGB(hex) {
        var h = String(hex || '#888888').replace('#', '');
        if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
        var v = parseInt(h, 16);
        if (isNaN(v)) return [136, 136, 136];
        return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
    }

    // Ink that stays legible on both a pale Quaternary yellow and a saturated
    // Palaeoproterozoic magenta: a dark version of the fill itself, never pure
    // black (which reads as a border) and never white (which reads as snow).
    function inkOf(rgb) {
        var lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255;
        var k = lum > 0.55 ? 0.45 : 0.62;   // darken pale colours less
        return 'rgb(' + Math.round(rgb[0] * (1 - k)) + ',' +
                        Math.round(rgb[1] * (1 - k)) + ',' +
                        Math.round(rgb[2] * (1 - k)) + ')';
    }

    // Deterministic jitter — a stipple that reshuffles on every reload reads
    // as animation, and a screenshot then does not reproduce.
    function rnd(seed) {
        var s = seed;
        return function () {
            s = (s * 1103515245 + 12345) & 0x7fffffff;
            return s / 0x7fffffff;
        };
    }

    var DRAW = {
        alluvium: function (c, n, S) {
            var r = rnd(7);
            for (var i = 0; i < 26; i++) {
                var x = r() * n, y = r() * n, rad = (0.5 + r() * 1.1) * S;
                c.beginPath(); c.arc(x, y, rad, 0, 6.283); c.fill();
            }
        },
        sandstone: function (c, n, S) {
            var r = rnd(11);
            for (var i = 0; i < 60; i++) {
                c.beginPath(); c.arc(r() * n, r() * n, 0.55 * S, 0, 6.283); c.fill();
            }
        },
        mudrock: function (c, n, S) {
            c.lineWidth = 1.1 * S; c.lineCap = 'butt';
            for (var row = 0; row < 4; row++) {
                var y = (row + 0.5) * n / 4;
                var off = (row % 2) * n / 4;
                for (var i = 0; i < 2; i++) {
                    var x = off + i * n / 2;
                    c.beginPath(); c.moveTo(x, y); c.lineTo(x + n / 5, y); c.stroke();
                }
            }
        },
        carbonate: function (c, n, S) {
            c.lineWidth = 1 * S;
            for (var row = 0; row < 3; row++) {
                var y = (row + 1) * n / 3;
                c.beginPath(); c.moveTo(0, y); c.lineTo(n, y); c.stroke();
                var off = (row % 2) * n / 2;
                c.beginPath(); c.moveTo(off, y); c.lineTo(off, y - n / 3); c.stroke();
            }
        },
        intrusive: function (c, n, S) {
            c.lineWidth = 1.2 * S;
            [[0.28, 0.28], [0.72, 0.72], [0.72, 0.22], [0.22, 0.75]].forEach(function (p) {
                var x = p[0] * n, y = p[1] * n, a = 0.09 * n;
                c.beginPath(); c.moveTo(x - a, y); c.lineTo(x + a, y);
                c.moveTo(x, y - a); c.lineTo(x, y + a); c.stroke();
            });
        },
        volcanic: function (c, n, S) {
            c.lineWidth = 1.2 * S; c.lineJoin = 'miter';
            [[0.25, 0.3], [0.72, 0.55], [0.4, 0.82]].forEach(function (p) {
                var x = p[0] * n, y = p[1] * n, a = 0.1 * n;
                c.beginPath(); c.moveTo(x - a, y - a); c.lineTo(x, y + a); c.lineTo(x + a, y - a);
                c.stroke();
            });
        },
        metamorphic: function (c, n, S) {
            c.lineWidth = 1.15 * S; c.lineCap = 'round';
            for (var row = 0; row < 4; row++) {
                var y = (row + 0.5) * n / 4, off = (row % 2) * n / 3;
                c.beginPath();
                c.moveTo(off, y);
                c.bezierCurveTo(off + n / 8, y - n / 12, off + n / 4, y + n / 12, off + n / 2.6, y);
                c.stroke();
            }
        },
        ultramafic: function (c, n, S) {
            c.lineWidth = 0.9 * S;
            for (var i = -1; i < 4; i++) {
                var o = i * n / 3;
                c.beginPath(); c.moveTo(o, 0); c.lineTo(o + n, n); c.stroke();
                c.beginPath(); c.moveTo(o + n, 0); c.lineTo(o, n); c.stroke();
            }
        },
        ironstone: function (c, n, S) {
            c.lineWidth = 1.2 * S; c.lineCap = 'butt';
            for (var row = 0; row < 3; row++) {
                var y = (row + 0.6) * n / 3;
                c.beginPath(); c.moveTo(0.06 * n, y); c.lineTo(0.42 * n, y); c.stroke();
                c.beginPath(); c.arc(0.62 * n, y, 0.8 * S, 0, 6.283); c.fill();
                c.beginPath(); c.moveTo(0.74 * n, y); c.lineTo(0.94 * n, y); c.stroke();
            }
        },
        mixed: function (c, n, S) {
            c.lineWidth = 0.9 * S;
            c.setLineDash([3 * S, 3 * S]);
            for (var i = -1; i < 3; i++) {
                var o = i * n / 2;
                c.beginPath(); c.moveTo(o, 0); c.lineTo(o + n, n); c.stroke();
            }
            c.setLineDash([]);
        }
    };

    /**
     * An ImageData tile of `lith` ornament over `color`.
     *
     * `alpha` is baked in rather than left to fill-opacity, because MapLibre's
     * fill-opacity multiplies the whole pattern — ornament included — and an
     * ornament at 20% is invisible while its background is still legible. Here
     * the BACKGROUND fades and the ink stays, which is what keeps a 20%
     * geology drape recognisable as geology.
     */
    function tile(lith, color, alpha) {
        var key = lith + '|' + color + '|' + (alpha == null ? 1 : alpha).toFixed(2);
        if (cache.has(key)) return cache.get(key);
        var n = TILE * RATIO;
        var cv = document.createElement('canvas');
        cv.width = n; cv.height = n;
        var c = cv.getContext('2d');
        var rgb = hexToRGB(color);
        var a = alpha == null ? 1 : Math.max(0, Math.min(1, alpha));
        // The BACKGROUND is deliberately much fainter than the INK.
        //
        // A geological drape is a backdrop: everything the app is actually
        // about — fires, trajectories, settlements, park outlines — is drawn on
        // top of it, and a saturated ICS magenta at 60% buries all of it. But
        // fading the whole thing evenly also fades the ornament, and then the
        // layer is back to being a flat wash that reads as water. So the tile
        // carries a weak tint and a strong hatch: the STRUCTURE survives being
        // turned down, which is what makes 30% geology still legible AS
        // geology.
        c.fillStyle = 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + (a * 0.55).toFixed(3) + ')';
        c.fillRect(0, 0, n, n);
        var ink = inkOf(rgb);
        c.fillStyle = ink;
        c.strokeStyle = ink;
        c.globalAlpha = Math.min(1, 0.62 + a * 0.38);
        (DRAW[lith] || DRAW.mixed)(c, n, RATIO);
        var img = c.getImageData(0, 0, n, n);
        var out = { data: new Uint8Array(img.data), width: n, height: n, pixelRatio: RATIO };
        cache.set(key, out);
        return out;
    }

    /** A CSS background-image for a legend swatch: same texture, same meaning. */
    function swatchCSS(lith, color) {
        var n = TILE * RATIO;
        var cv = document.createElement('canvas');
        cv.width = n; cv.height = n;
        var c = cv.getContext('2d');
        var rgb = hexToRGB(color);
        c.fillStyle = 'rgb(' + rgb.join(',') + ')';
        c.fillRect(0, 0, n, n);
        c.fillStyle = c.strokeStyle = inkOf(rgb);
        (DRAW[lith] || DRAW.mixed)(c, n, RATIO);
        return 'url(' + cv.toDataURL() + ')';
    }

    window.GeoPatterns = {
        tile: tile,
        swatchCSS: swatchCSS,
        keys: function () { return Object.keys(DRAW); },
        tileSizeCSS: TILE
    };
})();
