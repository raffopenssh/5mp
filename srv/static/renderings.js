/* renderings.js — ONE VOCABULARY FOR "HOW IS THIS DRAWN".
 *
 * A layer in this app can be drawn half a dozen ways: full shapes, dots, a
 * heat grid, dated paths, the season's contours, 2.5 km cells, patrol
 * isopleths. Those renderings are switched from three different surfaces —
 * the stats-panel legend row, a pinned chip, the animator's chip row — and
 * until now each surface had its own words and its own marks for the same
 * thing ("fires" on a chip, "grid" in a menu, "5 modes" in a pill, a coloured
 * dot for everything). Three vocabularies for one question is how a reader
 * ends up unable to tell whether two surfaces mean the same layer.
 *
 * So: a small, closed set of CATEGORIES, each with one word and one glyph.
 * The glyph is what the map actually draws (a lattice for a field, a dashed
 * contour for the front, nested loops for isopleths), so the chip, the pill
 * and the menu row all carry the same mark, and the mark is a picture of the
 * ink. The words are the only words any surface may use.
 *
 *   window.Renderings.cat(key)    -> category id for an animator/season/LOD key
 *   window.Renderings.word(key)   -> the one word for it ('grid', 'contours', …)
 *   window.Renderings.glyph(key)  -> HTML for its mark (a <i class="rg rg-…">)
 *   window.Renderings.summary([…])-> the pill's text: categories, deduped
 *
 * The glyph CSS lives in globe.css (`.rg`), because the legend needs it with
 * the animator closed.
 */
(function () {
    'use strict';

    // key -> category. Keys are the ones the rest of the app already uses:
    // LOD `render` values, animator layer names, season switch names.
    var CAT = {
        // what the LOD loader returned
        geometry: 'shapes', segments: 'lines', tiles: 'lines', points: 'dots',
        // animator data layers
        fireGrid: 'grid', firePts: 'dots', trajs: 'paths',
        effortGrid: 'grid', effortPts: 'dots', patrol: 'dots',
        deforest: 'dots', settlements: 'dots',
        // the Season overlay's own renderings (fireseason.js)
        // speed is the contours' WEIGHT, not a raster: its own mark (lines
        // thinning as they spread), so a chip row with front + speed shows
        // two different pictures, not the same one twice
        front: 'contours', vanguard: 'vanguard', entry: 'cells', speed: 'speed',
        patrolfront: 'iso', patrolpressure: 'iso'
    };

    // The word for each category — and its plain-language gloss, used where a
    // surface has room for a sentence (menu titles, tooltips).
    var WORD = {
        shapes: 'shapes', lines: 'lines', dots: 'dots', grid: 'grid',
        paths: 'paths', contours: 'contours', cells: 'cells', iso: 'iso',
        vanguard: 'vanguard', speed: 'weight'
    };
    var GLOSS = {
        shapes: 'full clickable outlines',
        lines: 'short direction lines',
        dots: 'one dot per feature',
        grid: 'a heat field summed over the window',
        paths: 'dated paths that build and fade',
        contours: 'dashed isochrones — a line per date',
        cells: 'a 2.5 km cell raster',
        iso: 'isopleths — nested lines of equal value',
        vanguard: 'the chains ahead of the front, in lead colour',
        speed: 'the contours weighted by the front\u2019s speed \u2014 heavy where it stalled, hairline where it raced'
    };

    function cat(key) { return CAT[key] || null; }
    function word(key) { var c = CAT[key] || key; return WORD[c] || c; }
    function gloss(key) { var c = CAT[key] || key; return GLOSS[c] || ''; }

    // The mark. `color` is optional: a chip colours its own glyph by the
    // layer, a menu row inherits the row's colour.
    function glyph(key, opts) {
        var c = CAT[key] || key;
        if (!WORD[c]) return '';
        opts = opts || {};
        return '<i class="rg rg-' + c + (opts.cls ? ' ' + opts.cls : '') + '"'
             + (opts.color ? ' style="color:' + opts.color + '"' : '')
             + ' aria-hidden="true"></i>';
    }

    /* The pill's text. Categories, in the order given, DEDUPED — two cell
     * rasters or two sets of isopleths are one word, because the reader is
     * being told what kind of ink is on the map, not how many switches are on.
     * Deduping is the whole reason this function exists: the old pill counted
     * switches and said "5 modes", which names nothing and is the shape of
     * answer that made the legend unreadable once the Season layers arrived.
     */
    function summary(keys, max) {
        max = max || 3;
        var seen = {}, out = [];
        (keys || []).forEach(function (k) {
            var c = CAT[k] || k;
            if (!WORD[c] || seen[c]) return;
            seen[c] = 1;
            out.push(WORD[c]);
        });
        if (!out.length) return { text: '', cats: [], extra: 0 };
        var shown = out.slice(0, max);
        var extra = out.length - shown.length;
        return {
            text: shown.join(' \u00b7 ') + (extra ? ' +' + extra : ''),
            cats: out,
            extra: extra
        };
    }

    window.Renderings = { cat: cat, word: word, gloss: gloss, glyph: glyph, summary: summary, CAT: CAT, WORD: WORD };
})();
