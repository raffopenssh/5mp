/*
 * FireSeason — the "season front" drape and the vanguard chains.
 *
 * Two toggleable annotations over the fire layer, both READ from what
 * scripts/fire_front.py measured (docs/agents/fire.md § Season front &
 * vanguard); nothing is computed here.
 *
 *   FRONT     isochrones of the burning season: "by this date a fifth of the
 *             land that burns had burned, within ~60 km". Drawn as thin lines
 *             every 5 days, labelled every 15, coloured early→late.
 *   SPEED     how fast the front travels, km/day — the gradient of the
 *             front as an arrival-time surface (1/|∇T|), one byte per 2.5 km
 *             cell (/api/fire-season-speed), drawn as squares by CellField.
 *             Where the season runs and where it stalls. Descriptive, not a
 *             forecast.
 *   ENTRY     "traditional early-burn ground": the cells whose FIRST burn of
 *             the season came >= 15 d before the local front in >= 40 % of
 *             the complete seasons held (and in at least 2) — where the
 *             season ENTERS, year after year; the leading edge the first
 *             flights go to. Cyan squares graded by the share of seasons,
 *             fading once the slider is well past the usual front here (its
 *             question is entry, not the season). Same grid, same renderer
 *             as the speed map (srv/static/cellfield.js); rows of
 *             fire_early_ground, written by scripts/fire_front.py, chosen
 *             over the early-season density by eval_fire_baseline.py.
 *   VANGUARD  the fire chains that BEGAN 10–60 days ahead of that front —
 *             the one population where the tracker's day-to-day links are
 *             measurably better than chance (link skill ≈0.5; in season ≈0).
 *             Each chain is drawn bright while it is still ahead of the
 *             front and faint once the season has caught up with it.
 *             Where scripts/fire_vanguard_kf.py has run (`tracker:'kf'`),
 *             the chains are the KALMAN SEED-AHEAD ones: born only ahead
 *             of the front and followed by a Kalman filter into the
 *             arriving season (5× the long chains at the same null ratio);
 *             a chain still moving at the newest data carries a live head
 *             with its heading. Elsewhere the plain trajectories that began
 *             ahead stand in (`tracker:'groups'`). The server decides per
 *             area (vanguardRowsSQL); every answer says which.
 *
 * Lives in the stats-panel Map strip like Geology and Historical maps: a
 * chip is the state, its body configures, its × switches off
 * (srv/static/maplegend.js). Share-link: `season=front,vanguard,speed`. Which
 * season is drawn follows the time slider (the season the window ends in),
 * exactly as the vanguard chains are filtered by it — one control, not two.
 *
 * The area whose front is shown is the focused area (park or AOI) when there
 * is one, otherwise whichever area's grid holds the view centre — resolved
 * by the server, which also enforces AOI visibility.
 */
(function () {
    'use strict';

    var FRONT_SRC = 'fireseason-front-src', FRONT_LYR = 'fireseason-front',
        FRONT_LBL = 'fireseason-front-label', FRONT_WAVE = 'fireseason-front-wave',
        VAN_SRC = 'fireseason-van-src', VAN_LYR = 'fireseason-van',
        VAN_DIM_LYR = 'fireseason-van-dim', VAN_GAP_LYR = 'fireseason-van-gap',
        VAN_HEAD_LYR = 'fireseason-van-head', VAN_HEAD_HALO = 'fireseason-van-head-halo',
        VAN_ARROW_LYR = 'fireseason-van-arrow',
        SPEED_LYR = 'fireseason-speed', ENTRY_LYR = 'fireseason-entry';

    var map = null;
    var st = { front: false, van: false, speed: false, entry: false };   // the season shown follows the time slider
    var front = null;      // last /api/fire-season answer
    var van = null;        // last /api/fire-vanguard answer
    var speed = null;      // last /api/fire-season-speed answer
    var entry = null;      // last /api/fire-season?early=1 answer (its early_ground + season_start)
    var frontKey = '', vanKey = '', speedKey = '', entryKey = '';
    var speedField = null, entryField = null;   // CellField renderers (one grid, two fields)
    var moveTimer = null, inflight = 0;
    var listeners = [];

    function pwd() { return encodeURIComponent((typeof getPwd === 'function' ? getPwd() : '') || ''); }
    function focusId() { return (typeof aoiFocusID !== 'undefined' && aoiFocusID) ? aoiFocusID : ''; }
    function dates() {
        var f = (typeof dateFrom !== 'undefined' && dateFrom) ? dateFrom : '';
        var t = (typeof dateTo !== 'undefined' && dateTo) ? dateTo : '';
        return { from: f, to: t };
    }
    function anyOn() { return st.front || st.van || st.speed || st.entry; }
    function emit() { listeners.forEach(function (fn) { try { fn(); } catch (e) { /* listener's problem */ } }); }
    function refreshStrip() {
        if (window.MapLegend && MapLegend.refresh) MapLegend.refresh();
        emit();
    }

    /* ── colour ───────────────────────────────────────────────────────────
     * One fire palette, three roles told apart by LINE STYLE first and hue
     * second, so the picture survives greyscale:
     *
     *   trajectories  solid red, glowing head            (anim.js / lodlayer)
     *   front         thin DASHED isochrones, ember red (early) → pale rose
     *                 (late): the season's own colour, ageing to ash as it
     *                 passes, and dashed because an isochrone is a contour,
     *                 not a thing that burned
     *   vanguard      solid, wider, with a halo, coloured PER SEGMENT by how
     *                 far ahead of the season it was there: orange (0 d, the
     *                 season arriving) → yellow (15 d) → white (≥ 40 d); the
     *                 hottest, brightest lines on the map — the few that
     *                 carry measured information — and in greyscale simply
     *                 the lightest. Width says whether the day order along
     *                 the chain is measured (evidence tier). legendHTML()
     *                 samples this same ramp for every panel that shows it.
     *
     * Nothing cool-hued: a blue line beside red fire read as a second data
     * family (water, roads), and the reader had to be told it was fire. */
    function lerp(a, b, t) { return a + (b - a) * t; }
    function hex(r, g, b) {
        return '#' + [r, g, b].map(function (v) { v = Math.max(0, Math.min(255, Math.round(v))); return (v < 16 ? '0' : '') + v.toString(16); }).join('');
    }
    function frontColor(t) {           // t 0..1 early→late: #ef4444 → #fb923c → #fecdd3
        if (t < 0.5) { var u = t / 0.5; return hex(lerp(239, 251, u), lerp(68, 146, u), lerp(68, 60, u)); }
        var v = (t - 0.5) / 0.5; return hex(lerp(251, 254, v), lerp(146, 205, v), lerp(60, 211, v));
    }
    // Lead ramp, per SEGMENT: 0 d orange #fb923c (the season is arriving —
    // the line turns towards fire red) → 15 d yellow #fde047 → ≥ 40 d white.
    // Below 0 the chain is 'after' and drawn as faint ash, not by this ramp.
    function leadColor(lead) {
        if (lead == null) lead = 10;
        if (lead < 15) { var u = Math.max(0, lead) / 15; return hex(lerp(251, 253, u), lerp(146, 224, u), lerp(60, 71, u)); }
        var t = Math.min(1, (lead - 15) / 25);
        return hex(lerp(253, 255, t), lerp(224, 255, t), lerp(71, 255, t));
    }
    // Certainty → width. The width of a chain says how sure we are of its
    // DAY ORDER: evidence_bits (rebuild_fire_trajectories_v5, log2 likelihood
    // ratio of its links vs the day-shuffled null) is graded linearly from
    // 0 bits (x1, a coin toss — the ordinary trajectory width) to 6 bits
    // ('supported', x1.5); negative never thins below x1, the line still
    // marks where fire ran. When only the tier word is known (old wire),
    // supported reads x1.5 and weak x1.3. 'unmeasured' never widens.
    var WIDE_TIERS = ['supported', 'weak'];
    var SUPPORTED_BITS = 6, WIDE_MAX = 0.5;
    function tierWord(t) { return t || 'unmeasured'; }
    function tierWide(t) { return WIDE_TIERS.indexOf(t) >= 0; }
    function tierBits(t) { return t === 'supported' ? SUPPORTED_BITS : (t === 'weak' ? 3.6 : 0); }
    function evidenceMul(tier, bits) {
        var b = (typeof bits === 'number' && isFinite(bits)) ? bits : tierBits(tier);
        return 1 + WIDE_MAX * Math.max(0, Math.min(1, b / SUPPORTED_BITS));
    }
    // The same rule as a MapLibre expression over feature properties
    // bitsKey (number) / tierKey (word). lodlayer.js reads it with 'eb'/'ev'.
    function widthMulExpr(bitsKey, tierKey) {
        var bits = ['coalesce', ['get', bitsKey],
            ['match', ['coalesce', ['get', tierKey], 'unmeasured'], 'supported', SUPPORTED_BITS, 'weak', 3.6, 0]];
        return ['+', 1, ['*', WIDE_MAX, ['min', 1, ['max', 0, ['/', bits, SUPPORTED_BITS]]]]];
    }
    // A direction chevron is a claim about the ORDER the days were visited
    // in, and that is exactly what the evidence tier measures. So a plain
    // trajectory carries chevrons only where it is drawn wide (supported /
    // weak); an unsupported, single or unmeasured chain is a corridor and
    // gets none — the Methods page says "we say so rather than draw arrows
    // we cannot back", and this is the filter that keeps that promise.
    // (Vanguard chains are exempt: their direction is backed by the
    // seed-ahead population test, not the per-chain contiguity score.)
    // MapLibre filter over a feature property holding the tier word.
    function directionFilter(tierKey) {
        return ['in', ['coalesce', ['get', tierKey], 'unmeasured'], ['literal', WIDE_TIERS]];
    }
    function tierDirectional(t) { return tierWide(t); }
    // "zoom" may only feed a top-level interpolate, so the factor goes
    // inside each stop: stops = [[zoom, width], ...] or a plain number.
    // Vanguard segments carry their factor pre-computed as 'wf'.
    function tierMul(w) { return ['*', w, ['coalesce', ['get', 'wf'], 1]]; }
    function tierWidth(stops) {
        if (typeof stops === 'number') return tierMul(stops);
        var e = ['interpolate', ['linear'], ['zoom']];
        stops.forEach(function (st) { e.push(st[0], tierMul(st[1])); });
        return e;
    }
    // Presence → opacity: a chain fades as the season closes in on it
    // (0 d ahead: 0.55) and is fully present 10+ d ahead (0.95) — the
    // original XSA render's alpha rule, so the eye reads "far ahead" as
    // both whiter and firmer.
    function leadAlpha(L) { return 0.55 + 0.4 * Math.max(0, Math.min(1, (L == null ? 10 : L) / 10)); }
    // One word for the tip: how sure, and what that did to the line.
    function evidenceWords(tier, bits) {
        var tw = tierWord(tier);
        var how = tw === 'supported' ? 'Confident' : tw === 'weak' ? 'Fairly sure' : tw === 'unsupported' ? 'Unsure' : tw === 'single' ? 'One day only' : 'Not measured';
        var rest = tw === 'single' ? ' \u2014 no day order to judge'
            : ' of the day order along this chain' + (evidenceMul(tier, bits) > 1.1 ? ', so it is drawn wider' : '');
        var b = (typeof bits === 'number' && isFinite(bits)) ? esc(tw) + ' evidence, ' + bits.toFixed(1) + ' bits' : esc(tw);
        return '<b>' + how + '</b>' + rest + ' <span style="opacity:.6">(' + b + ')</span>';
    }
    // The legend's swatch is SAMPLED from leadColor, so the panel cannot say
    // one ramp while the map draws another. Returns HTML: gradient bar with
    // its three ticks, and the width rule in one line.
    // Whether any chain now drawn came from the Kalman seed-ahead tracker.
    function kfShown() { return !!(van && van.trackers && van.trackers.kf); }
    function legendHTML(opts) {
        opts = opts || {};
        var stops = [];
        for (var d = 0; d <= 40; d += 5) stops.push(leadColor(d) + ' ' + (d / 40 * 100).toFixed(0) + '%');
        var bar = '<div class="fs-ramp" style="background:linear-gradient(90deg,' + stops.join(',') + ')"></div>';
        var ticks = '<div class="fs-ramp-ticks"><span>season arrives</span><span>15 d ahead</span><span>40+ d</span></div>';
        var width = '<div class="fs-ramp-width">' +
            '<span class="fs-wi"><span class="fs-w fs-w-wide"></span>wider = surer of the day order</span>' +
            '<span class="fs-wi"><span class="fs-w fs-w-thin"></span>thin = unsure</span>' +
            '<span class="fs-wi"><span class="fs-w fs-w-dash"></span>dashed = a day not seen</span>' +
            '<span class="fs-wi"><span class="fs-w fs-w-ash"></span>season caught up</span>' +
            '<span class="fs-wi"><span class="fs-w fs-w-arrow"></span>chevrons = direction of travel (zoomed in)</span>' +
            (kfShown() ? '<span class="fs-wi"><span class="fs-w fs-w-head"></span>large chevron at the end = still moving (points its heading)</span>' : '') + '</div>';
        var how = kfShown()
            ? '<div class="fs-ramp-how">Chains are born only ahead of the front and followed by a Kalman filter into the arriving season (seed-ahead tracker)' +
              (van && van.trackers && van.trackers.groups ? '; ' + van.trackers.groups + ' in view are plain chains where that has not run yet' : '') + '.</div>'
            : '';
        return '<div class="fs-legend' + (opts.cls ? ' ' + opts.cls : '') + '"' + (opts.title ? ' title="' + esc(opts.title) + '"' : '') + '>' +
            '<div class="fs-ramp-cap">Line colour: days ahead of the season front</div>' + bar + ticks + width + how + '</div>';
    }

    /* ── layers ─────────────────────────────────────────────────────────── */
    function ensureLayers() {
        if (!map || !map.getStyle()) return;
        // Two raster fields under every line, added first so the contours
        // and chains draw over them: the speed map (dense) and the
        // early-burn ground (sparse squares). One renderer (CellField).
        if (window.CellField) {
            if (!speedField) speedField = CellField.create(map, SPEED_LYR, { opacity: 0.5 });
            speedField.ensure();
            if (!entryField) entryField = CellField.create(map, ENTRY_LYR, { opacity: 1, minzoom: 4 });
            entryField.ensure();
        }
        if (!map.getSource(FRONT_SRC)) map.addSource(FRONT_SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        if (!map.getSource(VAN_SRC)) map.addSource(VAN_SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        if (!map.getLayer(FRONT_WAVE)) {
            // The animator's wave: a wide blurred stroke on the contours the
            // season reached in the last few days, fading as they age. Silent
            // (opacity 0) outside an animation; drawn under the crisp lines.
            map.addLayer({
                id: FRONT_WAVE, type: 'line', source: FRONT_SRC,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: { 'line-color': ['get', 'color'], 'line-width': 14, 'line-blur': 6, 'line-opacity': 0 }
            });
        }
        if (!map.getLayer(FRONT_LYR)) {
            map.addLayer({
                id: FRONT_LYR, type: 'line', source: FRONT_SRC,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': ['get', 'color'],
                    'line-dasharray': [2.5, 2],   // a contour, not a fire line — legible in greyscale
                    'line-width': ['case', ['get', 'label'], 1.6, 0.7],
                    'line-opacity': ['case', ['get', 'label'], 0.9, 0.55]
                }
            });
        }
        if (!map.getLayer(FRONT_LBL)) {
            map.addLayer({
                id: FRONT_LBL, type: 'symbol', source: FRONT_SRC,
                filter: ['==', ['get', 'label'], true],
                layout: {
                    'symbol-placement': 'line', 'symbol-spacing': 320,
                    'text-field': ['get', 'text'], 'text-size': 10.5,
                    'text-font': ['Noto Sans Regular'],
                    'text-letter-spacing': 0.04, 'text-max-angle': 30,
                    'text-pitch-alignment': 'viewport', 'text-rotation-alignment': 'map'
                },
                paint: { 'text-color': ['get', 'color'], 'text-halo-color': 'rgba(8,10,16,0.95)', 'text-halo-width': 1.4 }
            });
        }
        if (!map.getLayer(VAN_DIM_LYR)) {
            // The continuation: the same chain after the season caught up.
            // Drawn first so the ahead part sits on top where they meet.
            map.addLayer({
                id: VAN_DIM_LYR, type: 'line', source: VAN_SRC,
                filter: ['==', ['get', 'part'], 'after'],
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: { 'line-color': '#9ca3af', 'line-width': tierWidth(1.3), 'line-opacity': 0.3 }
            });
        }
        if (!map.getLayer(VAN_LYR)) {
            // Two layers, one rule: a segment whose two days are consecutive
            // is solid; one that bridges a day nobody saw (cloud, or a fire
            // too small for the satellite) is dashed. line-dasharray is not
            // data-driven, so the split is a filter on 'gap' (days).
            var vanPaint = {
                'line-color': ['get', 'color'],
                'line-width': tierWidth([[5, 1.6], [9, 2.6], [12, 3.4]]),
                'line-opacity': ['coalesce', ['get', 'alpha'], 0.95],
                'line-opacity-transition': { duration: 450 }
            };
            map.addLayer({
                id: VAN_LYR, type: 'line', source: VAN_SRC,
                filter: ['all', ['==', ['get', 'part'], 'ahead'], ['<=', ['coalesce', ['get', 'gap'], 1], 1]],
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: vanPaint
            });
            map.addLayer({
                id: VAN_GAP_LYR, type: 'line', source: VAN_SRC,
                filter: ['all', ['==', ['get', 'part'], 'ahead'], ['>', ['coalesce', ['get', 'gap'], 1], 1]],
                layout: { 'line-cap': 'butt', 'line-join': 'round' },
                paint: Object.assign({}, vanPaint, { 'line-dasharray': [3, 1.4] })
            });
            // Direction. A chain has a day order, so at the zoom where it is
            // a route rather than a stroke it carries chevrons along it —
            // the same SDF glyph the LOD trajectories use (globe.html
            // makeChevronSDF), in the segment's own colour: lead ramp while
            // ahead, ash after. Hidden below z7, where they would only be
            // texture. Not a tip target: the line underneath answers.
            if (map.hasImage && map.hasImage('arrow-right')) {
                map.addLayer({
                    id: VAN_ARROW_LYR, type: 'symbol', source: VAN_SRC, minzoom: 7,
                    filter: ['all', ['==', ['geometry-type'], 'LineString'], ['any', ['==', ['get', 'part'], 'ahead'], ['==', ['get', 'part'], 'after']]],
                    layout: {
                        'symbol-placement': 'line',
                        'symbol-spacing': ['interpolate', ['linear'], ['zoom'], 7, 90, 11, 130],
                        'icon-image': 'arrow-right', 'icon-rotate': 90,
                        'icon-size': ['interpolate', ['linear'], ['zoom'], 7, 0.38, 10, 0.5, 13, 0.64],
                        'icon-rotation-alignment': 'map',
                        'icon-allow-overlap': true, 'icon-ignore-placement': true
                    },
                    paint: {
                        'icon-color': ['get', 'color'],
                        'icon-opacity': ['case', ['==', ['get', 'part'], 'after'], 0.45, ['coalesce', ['get', 'alpha'], 0.95]],
                        'icon-halo-color': 'rgba(8,10,16,0.8)', 'icon-halo-width': 0.6
                    }
                });
            }
            // The live head: a chain whose last sighting is within the gap
            // budget of the newest data we hold is still moving. It is drawn
            // as a LARGER chevron at the chain's end, turned to the filter's
            // heading, over a soft lead-coloured glow — an arrow at the end
            // of a line can only be the line's head, where a dot read as one
            // more point feature next to settlements and deforestation.
            map.addLayer({
                id: VAN_HEAD_HALO, type: 'circle', source: VAN_SRC,
                filter: ['==', ['get', 'part'], 'head'],
                paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 9, 10, 15], 'circle-color': ['get', 'color'],
                    'circle-opacity': 0.2, 'circle-blur': 1 }
            });
            if (map.hasImage && map.hasImage('arrow-right')) {
                map.addLayer({
                    id: VAN_HEAD_LYR, type: 'symbol', source: VAN_SRC,
                    filter: ['==', ['get', 'part'], 'head'],
                    layout: {
                        'icon-image': 'arrow-right',
                        'icon-rotate': ['coalesce', ['get', 'heading_deg'], 0],
                        'icon-size': ['interpolate', ['linear'], ['zoom'], 5, 0.7, 10, 1.05],
                        'icon-rotation-alignment': 'map', 'icon-pitch-alignment': 'map',
                        'icon-allow-overlap': true, 'icon-ignore-placement': true
                    },
                    paint: { 'icon-color': ['get', 'color'], 'icon-opacity': 0.97, 'icon-halo-color': 'rgba(8,10,16,0.95)', 'icon-halo-width': 1.1 }
                });
            } else {
                map.addLayer({
                    id: VAN_HEAD_LYR, type: 'circle', source: VAN_SRC,
                    filter: ['==', ['get', 'part'], 'head'],
                    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 3, 10, 5.5], 'circle-color': ['get', 'color'],
                        'circle-stroke-color': 'rgba(8,10,16,0.9)', 'circle-stroke-width': 1.2, 'circle-opacity': 0.95 }
                });
            }
            registerTip();
        }
        applyVisibility();
    }
    function lift() {
        if (!map) return;
        [VAN_DIM_LYR, VAN_GAP_LYR, VAN_LYR, VAN_ARROW_LYR, VAN_HEAD_HALO, VAN_HEAD_LYR].forEach(function (id) { if (map.getLayer(id)) map.moveLayer(id); });
    }
    function applyVisibility() {
        if (!map) return;
        [FRONT_LYR, FRONT_LBL, FRONT_WAVE].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', st.front ? 'visible' : 'none'); });
        [VAN_LYR, VAN_GAP_LYR, VAN_DIM_LYR, VAN_ARROW_LYR, VAN_HEAD_HALO, VAN_HEAD_LYR].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', st.van ? 'visible' : 'none'); });
        if (speedField) speedField.setVisible(st.speed);
        if (entryField) entryField.setVisible(st.entry);
    }
    function setData(src, features) {
        var s = map && map.getSource(src);
        if (s) s.setData({ type: 'FeatureCollection', features: features || [] });
    }

    /* ── front ──────────────────────────────────────────────────────────── */
    function frontURL() {
        var f = focusId(), c = map.getCenter();
        var u = '/api/fire-season?pwd=' + pwd() + (f ? '&area=' + encodeURIComponent(f)
            : '&lon=' + c.lng.toFixed(3) + '&lat=' + c.lat.toFixed(3));
        var d = dates();
        if (d.to) u += '&at=' + d.to;   // the season the window ends in
        if (d.from) u += '&from=' + d.from;   // vanguard_in_window: the panel's basis
        if (d.to) u += '&to=' + d.to;
        return u;
    }

    /* ── summary for tips ───────────────────────────────────────────────
     * The park/AOI hover tips ask "where does the season stand here" for
     * an area that may not be the one drawn. summary=1 skips the contour
     * geometry (up to 300 KB), the answer is cached per area+window, and
     * the caller is told once when it lands (a tip re-renders itself). */
    var sumCache = {};
    function summary(areaId, onDone) {
        if (!areaId) return null;
        var d = dates();
        var key = areaId + '|' + d.from + '|' + d.to;
        if (key in sumCache) return sumCache[key];
        sumCache[key] = null;   // in flight
        var u = '/api/fire-season?pwd=' + pwd() + '&area=' + encodeURIComponent(areaId) + '&summary=1';
        if (d.to) u += '&at=' + d.to + '&to=' + d.to;
        if (d.from) u += '&from=' + d.from;
        fetch(u).then(function (r) { return r.ok ? r.json() : { status: 'not available' }; })
            .catch(function () { return { status: 'not available' }; })
            .then(function (j) { sumCache[key] = j || { status: 'not available' }; if (onDone) onDone(sumCache[key]); });
        return null;
    }
    function frontInView() {
        // Cheap: is the view centre inside the grid we already hold? Only
        // used when the area was resolved by point, to avoid a refetch on
        // every pan inside one park.
        if (!front || !front.stats || !front._bbox) return false;
        var c = map.getCenter(), b = front._bbox;
        return c.lng >= b[0] && c.lng <= b[2] && c.lat >= b[1] && c.lat <= b[3];
    }
    function loadFront(force) {
        if (!st.front || !map) return Promise.resolve();
        var key = (focusId() || 'pt') + '|@' + dates().to;
        if (!force && key === frontKey && front && (focusId() || frontInView())) return Promise.resolve();
        inflight++; emit();
        return fetch(frontURL()).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
            inflight--;
            frontKey = key;
            front = j || { status: 'request failed', seasons: [] };
            animMeta = null; animPctKey = null;
            if (animT !== null) playheadMeta(animT);   // a new front under a running animator
            var feats = [];
            if (j && j.contours && j.contours.length) {
                var ds = j.contours.map(function (f) { return f.properties.dos; });
                var lo = Math.min.apply(null, ds), hi = Math.max.apply(null, ds);
                var bb = [180, 90, -180, -90];
                feats = j.contours.map(function (f) {
                    var t = hi > lo ? (f.properties.dos - lo) / (hi - lo) : 0.5;
                    f.properties.color = frontColor(t);
                    f.properties.label = !!f.properties.label;
                    // numeric time for the animator's age expressions
                    f.properties.t = Date.parse((f.properties.date || '') + 'T00:00:00Z') || 0;
                    (f.geometry.coordinates || []).forEach(function (line) {
                        line.forEach(function (p) {
                            if (p[0] < bb[0]) bb[0] = p[0]; if (p[1] < bb[1]) bb[1] = p[1];
                            if (p[0] > bb[2]) bb[2] = p[0]; if (p[1] > bb[3]) bb[3] = p[1];
                        });
                    });
                    return f;
                });
                front._bbox = bb;
            }
            setData(FRONT_SRC, feats);
            refreshStrip();
        }).catch(function () { inflight--; emit(); });
    }

    /* ── speed ──────────────────────────────────────────────────────────
     * Same area rule as the front (focus, else the grid under the view
     * centre; the server resolves both), same season (the one the window
     * ends in). One PNG per area+season; nothing is computed here. */
    function speedURL() {
        var f = focusId(), c = map.getCenter();
        var u = '/api/fire-season-speed?pwd=' + pwd() + (f ? '&area=' + encodeURIComponent(f)
            : '&lon=' + c.lng.toFixed(3) + '&lat=' + c.lat.toFixed(3));
        var d = dates();
        if (d.to) u += '&at=' + d.to;
        return u;
    }
    function speedInView() {
        if (!speed || !speed.bbox) return false;
        var c = map.getCenter(), b = speed.bbox;
        return c.lng >= b[0] && c.lng <= b[2] && c.lat >= b[1] && c.lat <= b[3];
    }
    // The field, decoded once into a byte per cell (0 = no front, b = level
    // b−1 on the server's log ramp) and coloured here from the SAME stops
    // the legend prints; a click reads the byte under the pointer back.
    var speedStops = null;
    function speedRGB(kmd) {
        var lg = speedStops || [];
        if (!lg.length) return [245, 158, 11];
        function rgb(h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; }
        if (kmd <= lg[0].km_d) return rgb(lg[0].color);
        for (var i = 1; i < lg.length; i++) {
            if (kmd <= lg[i].km_d) {
                var a = lg[i - 1], b = lg[i], t = (Math.log(kmd) - Math.log(a.km_d)) / (Math.log(b.km_d) - Math.log(a.km_d));
                var ca = rgb(a.color), cb = rgb(b.color);
                return [ca[0] + (cb[0] - ca[0]) * t, ca[1] + (cb[1] - ca[1]) * t, ca[2] + (cb[2] - ca[2]) * t];
            }
        }
        return rgb(lg[lg.length - 1].color);
    }
    function speedOfByte(b) {
        var lv = speed && speed.levels;
        if (!lv || !b) return null;
        var lo = Math.log(lv.km_d_min), hi = Math.log(lv.km_d_max);
        return Math.exp(lo + (hi - lo) * (b - 1) / (lv.n - 1));
    }
    function drawSpeed(j) {
        if (!speedField) return;
        if (!j || !j.values || !j.grid) { speedField.clear(); return; }
        speedStops = j.legend || null;
        speedField.setGrid(j.grid);
        speedField.setDense(j.values);
        speedField.render(function (c) { var k = speedRGB(speedOfByte(c.v)); return [k[0], k[1], k[2], 255]; }, { scale: 1, key: 'speed|' + j.area + '|' + j.season });
    }
    function speedAt(lng, lat) {
        if (!speedField || !speed || !speed.levels) return null;
        var h = speedField.at(lng, lat);
        if (!h) return null;   // no front here this season
        var lv = speed.levels, lvl = h.v - 1;
        return { kmd: speedOfByte(h.v), level: lvl, season: speed.season, area: speed.area,
            atMax: lvl === lv.n - 1, atMin: lvl === 0 };
    }
    function speedWords(kmd) {
        return kmd < 2 ? 'the season stalls here' : kmd < 6 ? 'the season walks here' : kmd < 15 ? 'the season runs here' : 'the season sweeps through here';
    }
    function speedTipHTML(h) {
        var v = h.atMax ? '\u2265 ' + Math.round(h.kmd) : h.atMin ? '\u2264 ' + h.kmd.toFixed(1) : (h.kmd < 10 ? h.kmd.toFixed(1) : Math.round(h.kmd));
        var stt = speed && speed.stats ? '<div class="maptip-meta">this area: median ' + speed.stats.median_km_d + ' km/d (p10 ' + speed.stats.p10_km_d + ', p90 ' + speed.stats.p90_km_d + ')</div>' : '';
        return '<div class="maptip-title">Season speed: <b>' + v + ' km/day</b></div>' +
            '<div class="maptip-body">' + speedWords(h.kmd) + ' \u2014 how fast the ' + esc(h.season || '') + ' front travelled, from the gradient of its arrival-time surface.</div>' + stt +
            '<div class="maptip-dim">Describes the season drawn; not a forecast.</div>';
    }
    var SPEED_PROBE = 'fireseason-speed-probe', speedProbeOn = false;
    function ensureSpeedProbe() {
        if (speedProbeOn || !window.MapTip || !MapTip.registerProbe) return;
        // A backdrop (negative priority, click only): the field sits under
        // every line and pin over the area, so it must never outrank a chain
        // or a settlement, and a hover tip over a whole-viewport fill would
        // follow the cursor forever (maptip.js "PRECEDENCE").
        MapTip.registerProbe(SPEED_PROBE, {
            priority: -10, clickOnly: true, tabLabel: 'Season speed', tabColor: '#f59e0b',
            probe: function (e) {
                if (!st.speed || !e || !e.lngLat) return null;
                var h = speedAt(e.lngLat.lng, e.lngLat.lat);
                if (!h) return null;
                return { html: speedTipHTML(h), properties: { kmd: h.kmd, season: h.season, area: h.area }, dist: 0 };
            }
        });
        speedProbeOn = true;
    }
    function loadSpeed(force) {
        if (!st.speed || !map) return Promise.resolve();
        var key = (focusId() || 'pt') + '|@' + dates().to;
        if (!force && key === speedKey && speed && (focusId() || speedInView())) return Promise.resolve();
        inflight++; emit();
        return fetch(speedURL()).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
            inflight--;
            speedKey = key;
            speed = j || { status: 'request failed' };
            drawSpeed(j);
            ensureSpeedProbe();
            refreshStrip();
        }).catch(function () { inflight--; emit(); });
    }
    // Speed legend, sampled from the server's fixed stops (km/day, log
    // ramp) so the panel cannot say one ramp while the PNG draws another.
    function speedLegendHTML(opts) {
        opts = opts || {};
        var lg = (speed && speed.legend) || [];
        if (!lg.length) return '';
        var stops = lg.map(function (st, i) { return st.color + ' ' + (i / (lg.length - 1) * 100).toFixed(0) + '%'; });
        var bar = '<div class="fs-ramp" style="background:linear-gradient(90deg,' + stops.join(',') + ')"></div>';
        var ticks = '<div class="fs-ramp-ticks">' + lg.map(function (st) { return '<span>' + st.km_d + '</span>'; }).join('') + '</div>';
        var stt = speed.stats ? '<div class="fs-ramp-width">here: median ' + speed.stats.median_km_d + ' km/d (p10 ' + speed.stats.p10_km_d + ', p90 ' + speed.stats.p90_km_d + ')</div>' : '';
        return '<div class="fs-legend' + (opts.cls ? ' ' + opts.cls : '') + '"><div class="fs-ramp-cap">Season speed, km/day (how fast the front travels; log scale)</div>' + bar + ticks + stt + '</div>';
    }


    /* ── entry ground ───────────────────────────────────────────────────
     * The early-burn ground rides /api/fire-season (early=1, summary=1):
     * same area rule, same season (the one the window ends in), one fetch
     * per area+window. Cells arrive compact ([ix, iy, early, held, days
     * ahead, month, usual front dos] + this season's first-burn day per
     * cell) and are drawn as squares by CellField. Nothing is computed
     * here but colour. */
    var ENTRY_RGB = [34, 211, 238];          // cyan-400: a family no other layer uses
    var ENTRY_FLASH = [236, 254, 255];       // the season's first detection lands: a brief light
    var ENTRY_FADE_DAYS = 60;                // full weight to the usual front, ~30 % once 60 d past it
    function entryURL() {
        var f = focusId(), c = map.getCenter();
        var u = '/api/fire-season?pwd=' + pwd() + '&summary=1&early=1' + (f ? '&area=' + encodeURIComponent(f)
            : '&lon=' + c.lng.toFixed(3) + '&lat=' + c.lat.toFixed(3));
        var d = dates();
        if (d.to) u += '&at=' + d.to;
        return u;
    }
    function entryInView() {
        if (!entry || !entry.early_ground || !entry.early_ground.bbox) return false;
        var c = map.getCenter(), b = entry.early_ground.bbox;
        return c.lng >= b[0] && c.lng <= b[2] && c.lat >= b[1] && c.lat <= b[3];
    }
    // share of seasons → alpha: the rule's floor (40 %) is faint, 70 %+
    // solid; "2 of 2" is solid too (it is the most a two-season area can say,
    // and the tip prints the 2).
    function entryAlpha(share) { return 0.35 + 0.5 * Math.max(0, Math.min(1, (share - 0.4) / 0.3)); }
    // time: dos = day of season at the slider's end / playhead; c.uf = the
    // usual front's day at this cell. Entry ground is a statement about
    // where the season BEGINS, so once the season is long past here it
    // steps back (never off: the pattern stays legible under the fires).
    function entryTimeMul(c, dos) {
        if (dos == null || c.uf == null || c.uf < 0) return 1;
        var past = dos - c.uf;
        if (past <= 0) return 1;
        return 1 - 0.7 * Math.min(1, past / ENTRY_FADE_DAYS);
    }
    // zoom bands: below the zoom where a cell is a few pixels, thin by
    // confidence (highest share first) so the pattern survives, like the
    // front's labels do — never drop the layer.
    function entryMinShare(z) { return z < 5.5 ? 0.7 : z < 7 ? 0.5 : 0; }
    function entryDos(t) {
        // day of season for a playhead / window end against the season the
        // answer names; null when unknown
        if (!entry || !entry.season_start) return null;
        var t0 = Date.parse(entry.season_start + 'T00:00:00Z');
        return isFinite(t0) ? (t - t0) / DAY_MS : null;
    }
    var entryRenderT = null;   // the instant the squares are drawn for (window end, or the playhead)
    function drawEntry(t) {
        if (!entryField || !map) return;
        var eg = entry && entry.early_ground;
        if (!eg || eg.status !== 'ok' || !eg.cells) { entryField.clear(); return; }
        var z = map.getZoom(), minShare = entryMinShare(z), hi = z >= 8;
        entryRenderT = t;
        var dos = t == null ? null : entryDos(t);
        var key = ['entry', entry.area, entry.season, minShare, hi ? 1 : 0, z < 7 ? 'h' : '', dos == null ? 'x' : Math.round(dos * 4)].join('|');
        entryField.render(function (c) {
            if (c.share < minShare) return null;
            var a = entryAlpha(c.share) * entryTimeMul(c, dos);
            // this season's first detection lands here: light up for 4 days
            if (dos != null && c.fb != null && dos >= c.fb && dos - c.fb <= 4) {
                var k = 1 - (dos - c.fb) / 4;
                return [ENTRY_RGB[0] + (ENTRY_FLASH[0] - ENTRY_RGB[0]) * k, ENTRY_RGB[1] + (ENTRY_FLASH[1] - ENTRY_RGB[1]) * k,
                    ENTRY_RGB[2] + (ENTRY_FLASH[2] - ENTRY_RGB[2]) * k, 255 * Math.max(a, 0.6 + 0.4 * k)];
            }
            return [ENTRY_RGB[0], ENTRY_RGB[1], ENTRY_RGB[2], 255 * a];
        }, { scale: hi ? 6 : 1, rim: hi ? { alpha: 0.55, dark: 0.45 } : null, halo: z < 7 ? 0.4 : 0, key: key });
    }
    function windowEndMs() { var d = dates(); return d.to ? Date.parse(d.to + 'T00:00:00Z') : null; }
    function loadEntry(force) {
        if (!st.entry || !map) return Promise.resolve();
        var key = (focusId() || 'pt') + '|@' + dates().to;
        if (!force && key === entryKey && entry && (focusId() || entryInView())) return Promise.resolve();
        inflight++; emit();
        return fetch(entryURL()).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
            inflight--;
            entryKey = key;
            entry = j || { status: 'request failed' };
            var eg = entry.early_ground;
            if (eg && eg.status === 'ok' && eg.cells && entryField) {
                var fb = eg.first_burn || null;
                var cells = eg.cells.map(function (c, i) {
                    return { ix: c[0], iy: c[1], early: c[2], held: c[3], share: c[3] ? c[2] / c[3] : 0, days: c[4], month: c[5],
                        uf: c[6], fb: fb ? fb[i] : null };
                });
                entryField.setGrid(eg.grid);
                entryField.setSparse(cells);
                entryField.invalidate();
                drawEntry(animT !== null ? animT : windowEndMs());
            } else if (entryField) entryField.clear();
            ensureEntryProbe();
            refreshStrip();
        }).catch(function () { inflight--; emit(); });
    }
    var MONTHS = ['', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    function entryTipHTML(c) {
        var eg = entry.early_ground;
        var h = '<div class="maptip-title" style="color:#67e8f9">Early-burn ground</div>';
        h += '<div class="maptip-body">Herds have entered here early in <b>' + c.early + ' of ' + c.held + ' seasons</b>' +
            (c.days > 0 ? ' \u00b7 typically ~<b>' + c.days + ' days</b> before the local front' : '') +
            (c.month ? ' \u00b7 usually <b>' + MONTHS[c.month] + '</b>' : '') + '</div>';
        if (c.fb != null && eg.first_burn_season) {
            var d0 = Date.parse(entry.season_start + 'T00:00:00Z'), d = new Date(d0 + c.fb * DAY_MS).toISOString().slice(0, 10);
            var before = (c.uf != null && c.uf >= 0) ? c.uf - c.fb : null;
            h += '<div class="maptip-meta">Season ' + esc(eg.first_burn_season) + ': first detection here ' + fmtDate(d) +
                (before != null ? (before > 0 ? ' \u2014 ' + Math.round(before) + ' d before the usual front' : ' \u2014 ' + Math.round(-before) + ' d after the usual front') : '') + '</div>';
        }
        h += '<div class="maptip-dim">Rule: first burn of the season \u2265 ' + eg.ahead_days + ' d ahead of the front in \u2265 ' + Math.round(eg.min_share * 100) +
            ' % of seasons (at least ' + eg.min_early + '); ' + eg.count.toLocaleString() + ' cells over ' + eg.seasons_held + ' seasons, ~' +
            Math.round(eg.chance_cells || 0) + ' expected by chance. Where the season usually enters \u2014 not this year\u2019s herds (the vanguard chains are that).</div>';
        return h;
    }
    var ENTRY_PROBE = 'fireseason-entry-probe', entryProbeOn = false;
    function ensureEntryProbe() {
        if (entryProbeOn || !window.MapTip || !MapTip.registerProbe) return;
        // Sparse squares, so a hover tip is fine (there is "off it" to move
        // to); ranked under every line and pin (priority −5) and above the
        // speed backdrop (−10): a chain on top of a square is the answer.
        MapTip.registerProbe(ENTRY_PROBE, {
            priority: -5, tabLabel: 'Early-burn ground', tabColor: '#22d3ee',
            probe: function (e) {
                if (!st.entry || !entryField || !e || !e.lngLat) return null;
                var c = entryField.at(e.lngLat.lng, e.lngLat.lat);
                if (!c || c.share < entryMinShare(map.getZoom())) return null;
                return { html: entryTipHTML(c), properties: { early: c.early, held: c.held, days_ahead: c.days, month: c.month }, dist: 0 };
            }
        });
        entryProbeOn = true;
    }
    // Legend: a graded square (the rule's floor faint → solid), and the
    // area's count beside its chance — every number from the answer.
    function entryLegendHTML(opts) {
        opts = opts || {};
        var eg = entry && entry.early_ground;
        var sw = '<span class="fs-entry-sw"><i style="opacity:' + entryAlpha(0.4) + '"></i><i style="opacity:' + entryAlpha(0.55) + '"></i><i style="opacity:' + entryAlpha(0.7) + '"></i></span>';
        var cap = '<div class="fs-ramp-cap">' + sw + ' Early-burn ground: squares = 2.5 km cells that burn ahead of their surroundings season after season (faint 40 % of seasons \u2192 solid 70 %+)</div>';
        var line = '';
        if (eg && eg.status === 'ok') {
            line = '<div class="fs-ramp-width">' + eg.count.toLocaleString() + ' cells (' + Math.round(eg.km2).toLocaleString() + ' km\u00b2) early in \u2265 ' +
                Math.round(eg.min_share * 100) + ' % of ' + eg.seasons_held + ' seasons \u00b7 ~' + Math.round(eg.chance_cells || 0) + ' expected by chance \u00b7 fades once the slider is past the usual front here</div>';
        } else if (eg && eg.status) {
            line = '<div class="fs-ramp-width">' + esc(eg.reason || eg.status) + '</div>';
        }
        return '<div class="fs-legend' + (opts.cls ? ' ' + opts.cls : '') + '">' + cap + line + '</div>';
    }
    // The strip's count: cells / km² of early-burn ground in the viewport
    // (the same cells the squares draw; the thinning band is honoured).
    function entryInViewCount() {
        if (!entryField || !map || !entry || !entry.early_ground || entry.early_ground.status !== 'ok') return null;
        var b = map.getBounds(), minShare = entryMinShare(map.getZoom());
        var cells = entryField.cellsIn([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]).filter(function (c) { return c.share >= minShare; });
        return { cells: cells.length, km2: Math.round(cells.length * entryField.cellKm2()), total: entry.early_ground.count, seasons: entry.early_ground.seasons_held };
    }

    /* ── vanguard ───────────────────────────────────────────────────────── */
    function vanURL() {
        var b = map.getBounds(), d = dates(), f = focusId();
        var bb = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map(function (v) { return v.toFixed(3); }).join(',');
        var u = '/api/fire-vanguard?bbox=' + bb + '&pwd=' + pwd() + '&limit=6000';
        if (d.from) u += '&from=' + d.from;
        if (d.to) u += '&to=' + d.to;
        if (f && typeof focusIsAOI === 'function' && focusIsAOI(f)) u += '&aoi=' + encodeURIComponent(f);
        return u;
    }
    // One feature per SEGMENT, so each carries its own lead colour: MapLibre
    // cannot colour one LineString per vertex. A segment's lead is the mean
    // of its two vertex leads; >= 0 is 'ahead' (bright, lead ramp), < 0 is
    // 'after' (faint ash — the season caught up). The chain's evidence tier
    // rides on every segment for the width rule.
    function chainProps(g, part) {
        return { part: part, id: g.id, park: g.park, lead_start: g.lead_start, lead_basis: g.lead_basis,
            tier: tierWord(g.tier), bits: g.bits, wf: +evidenceMul(g.tier, g.bits).toFixed(2), ahead_km: g.ahead_km, ahead_days: g.ahead_days, km: g.km, kmd: g.kmd,
            fires: g.fires, days: g.days, start: g.start, end: g.end, season: g.season, type: g.type,
            tracker: g.tracker || 'groups', end_cause: g.end_cause || null, heading_deg: g.heading_deg == null ? null : g.heading_deg,
            speed_kmd: g.speed_kmd == null ? null : g.speed_kmd, seed_lead: g.seed_lead == null ? null : g.seed_lead };
    }
    // Heading (degrees clockwise from north) from one point to the next,
    // for a live head the filter left without one.
    function bearing(a, b) {
        var toR = Math.PI / 180, la1 = a[1] * toR, la2 = b[1] * toR, dl = (b[0] - a[0]) * toR;
        var y = Math.sin(dl) * Math.cos(la2), x = Math.cos(la1) * Math.sin(la2) - Math.sin(la1) * Math.cos(la2) * Math.cos(dl);
        return (Math.atan2(y, x) / toR + 360) % 360;
    }
    function splitChain(g) {
        var pts = g.pts || [], leads = g.leads || [];
        var out = [];
        var lead0 = g.lead_start == null ? 10 : g.lead_start;
        for (var i = 0; i + 1 < pts.length; i++) {
            var a = leads[i], b = leads[i + 1];
            var L = (a == null && b == null) ? lead0 : (a == null ? b : (b == null ? a : (a + b) / 2));
            var pr = chainProps(g, L >= 0 ? 'ahead' : 'after');
            pr.lead = Math.round(L); pr.color = L >= 0 ? leadColor(L) : '#9ca3af';
            pr.alpha = +leadAlpha(L).toFixed(2);
            // days between the two vertices (pts[i][2] is the day offset)
            pr.gap = (pts[i][2] != null && pts[i + 1][2] != null) ? Math.round(pts[i + 1][2] - pts[i][2]) : 1;
            out.push({ type: 'Feature', properties: pr,
                geometry: { type: 'LineString', coordinates: [[pts[i][0], pts[i][1]], [pts[i + 1][0], pts[i + 1][1]]] } });
        }
        if (g.end_cause === 'ongoing' && pts.length) {
            var last = pts[pts.length - 1], lastLead = leads.length ? leads[leads.length - 1] : lead0;
            if (lastLead == null) lastLead = lead0;
            var ph = chainProps(g, 'head');
            ph.lead = Math.round(lastLead); ph.color = lastLead >= 0 ? leadColor(lastLead) : '#fb923c'; ph.alpha = 0.95; ph.gap = 1;
            // the head arrow turns to the filter's heading; failing that, the last step's
            if (ph.heading_deg == null && pts.length > 1) ph.heading_deg = Math.round(bearing(pts[pts.length - 2], last));
            out.push({ type: 'Feature', properties: ph, geometry: { type: 'Point', coordinates: [last[0], last[1]] } });
        }
        if (pts.length === 1) {
            // A one-vertex chain still deserves a mark: a very short line.
            var p = pts[0], e = 0.004, pr1 = chainProps(g, 'ahead');
            pr1.lead = lead0; pr1.color = leadColor(lead0); pr1.alpha = +leadAlpha(lead0).toFixed(2); pr1.gap = 1;
            out.push({ type: 'Feature', properties: pr1,
                geometry: { type: 'LineString', coordinates: [[p[0] - e, p[1]], [p[0] + e, p[1]]] } });
        }
        return out;
    }
    function loadVan(force) {
        if (!st.van || !map) return Promise.resolve();
        var key = vanURL();
        if (!force && key === vanKey) return Promise.resolve();
        inflight++; emit();
        return fetch(key).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
            inflight--;
            vanKey = key;
            van = j;
            var feats = [];
            ((j && j.groups) || []).forEach(function (g) { feats = feats.concat(splitChain(g)); });
            setData(VAN_SRC, feats);
            // The few hundred chains that carry information sit on top of
            // the thousands of plain trajectories (lodlayer adds its line
            // layer later and would otherwise bury them).
            lift();
            refreshStrip();
        }).catch(function () { inflight--; emit(); });
    }

    /* ── tip ─────────────────────────────────────────────────────────────── */
    function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
    function fmtDate(s) {
        if (!s) return '';
        var d = new Date(s + 'T00:00:00Z');
        return isNaN(d) ? s : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
    }
    var COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
    function compass(deg) { return COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16]; }
    // Why the chain ends, in the words a ranger would use. 'ongoing' is the
    // one that matters live: last seen within the gap budget of the newest
    // data, so someone is still moving out there.
    function endWords(p) {
        if (p.tracker !== 'kf' || !p.end_cause) return '';
        var hd = (p.heading_deg != null) ? ' heading <b>' + compass(p.heading_deg) + '</b>' + (p.speed_kmd ? ' at ~' + Number(p.speed_kmd).toFixed(0) + ' km/d' : '') : '';
        if (p.end_cause === 'ongoing') return '<div style="color:#fde047"><b>Still moving</b> \u2014 last seen ' + fmtDate(p.end) + hd + '. Field staff report the herds following the scouts 1\u20132 weeks behind.</div>';
        if (p.end_cause === 'season') return '<div style="opacity:.85">Followed until the season arrived around it' + hd + '; from there it is one fire among the field\u2019s.</div>';
        return '<div style="opacity:.85">Trail lost' + hd + ': no fire within reach for 3 days \u2014 cloud, a fire too small to see, or they stopped.</div>';
    }
    function tipHTML(p) {
        var ls = p.lead_start;
        var h = '<div style="font-weight:600;margin-bottom:3px">Vanguard fire chain' + (p.tracker === 'kf' ? ' <span style="opacity:.6;font-weight:400">\u00b7 Kalman-tracked</span>' : '') + '</div>';
        if (p.part === 'head') h += '<div style="color:#fde047;font-weight:600">Live head</div>';
        h += '<div>Began <b>' + esc(ls) + ' days ahead</b> of the ' + (p.lead_basis === 'usual' ? 'usual' : 'season') +
             ' front' + (p.lead_basis === 'usual' ? ' <span style="opacity:.7">(this season\u2019s front had not arrived yet)</span>' : '') + '</div>';
        if (p.ahead_km) h += '<div>Ran <b>' + Number(p.ahead_km).toFixed(0) + ' km</b> over ' + esc(p.ahead_days) + ' d ' +
            (p.end_cause === 'season' || (!p.end_cause && p.part === 'after') ? 'before the season caught up' : 'ahead of the season' + (p.end_cause === 'ongoing' ? ' so far' : '')) + '</div>';
        if (p.part === 'after') h += '<div style="color:#9ca3af">Here the season had caught up ' + esc(-p.lead) + ' d earlier \u2014 one fire among the field\u2019s</div>';
        else if (p.part === 'ahead' && p.lead != null && p.lead !== ls) h += '<div>Still <b>' + esc(p.lead) + ' d ahead</b> here</div>';
        h += endWords(p);
        h += '<div style="opacity:.8">' + evidenceWords(p.tier, p.bits) + '</div>';
        h += '<div style="opacity:.75;margin-top:3px">' + fmtDate(p.start) + ' \u2013 ' + fmtDate(p.end) + ' \u00b7 ' + esc(p.fires) + ' detections \u00b7 ' +
             (p.km ? Number(p.km).toFixed(0) + ' km' : '') + (p.season ? ' \u00b7 season ' + esc(p.season) : '') + '</div>';
        h += '<div style="opacity:.6;font-size:11px;margin-top:4px">An early signal that people are moving ahead of the season \u2014 not a proof of who or why.' +
             (p.tracker === 'kf' ? ' Born ahead of the front, followed by a Kalman filter into the season.' : '') + '</div>';
        return h;
    }
    function registerTip() {
        if (!window.MapTip || !MapTip.register) return;
        [VAN_LYR, VAN_GAP_LYR, VAN_DIM_LYR, VAN_HEAD_LYR].forEach(function (id) {
            MapTip.register(id, {
                html: function (props) { return props && props.lead_start != null ? tipHTML(props) : ''; },
                tabLabel: 'Vanguard', tabColor: '#fde047', priority: 5
            });
        });
    }

    /* ── events ─────────────────────────────────────────────────────────── */
    function onMove() {
        if (!anyOn()) return;
        clearTimeout(moveTimer);
        moveTimer = setTimeout(function () {
            loadFront(false); loadVan(false); loadSpeed(false);
            loadEntry(false).then(function () { if (st.entry && animT === null) drawEntry(windowEndMs()); });   // zoom band may have changed
            if (st.entry) refreshStrip();   // the in-view count
        }, 350);
    }
    function onDates() {
        if (st.van) { vanKey = ''; loadVan(true); }
        if (st.front) loadFront(false);   // the front follows the window
        if (st.speed) loadSpeed(false);   // so does the speed map (one season per answer)
        if (st.entry) loadEntry(false).then(function () { if (st.entry && animT === null) drawEntry(windowEndMs()); });   // and the fade follows the window's end
    }
    function onFocus() { if (anyOn()) { frontKey = ''; speedKey = ''; entryKey = ''; loadFront(true); loadVan(true); loadSpeed(true); loadEntry(true); } }

    /* ── animator ───────────────────────────────────────────────────────
     * The animator hands us its playhead (ms). The front is a MapLibre
     * layer, not canvas, so it animates through data-driven paint: every
     * contour carries its own time `t`, and the expressions below turn
     * (playhead − t) into ink —
     *
     *   * not yet reached      → hidden (filter)
     *   * reached < ~5 d ago   → the WAVE: a wide soft glow plus a heavy
     *                            crisp line, its date labelled even on an
     *                            unlabelled 5-day contour, so the reader
     *                            sees the season ARRIVE and reads when
     *   * older                → thins and dims with age, the labelled
     *                            15-day lines staying legible as the wake
     *
     * so the front MOVES across the area as the fires build up under it,
     * the way the trajectories build vertex by vertex. Repainting is
     * throttled to ~12/s: setPaintProperty on a 30-feature source is cheap,
     * but not 60 times a second on top of the canvas. null = whole season. */
    var animDay = null, animWall = 0, animT = null, animTrail = null;
    var DAY_MS = 86400000;
    /* Where the front stands at the playhead, from `front_curve` (share of
     * front-bearing cells reached per step_days of season) — the server
     * computes `front_reached_pct` at the window's end only.
     * Listeners are told only when the rounded % changes, not per frame. */
    var animMeta = null, animPctKey = null;
    function curveAt(c, step, dos) {
        if (!c || !c.length) return null;
        var x = dos / step, i = Math.floor(x);
        if (i < 0) return 0;
        if (i >= c.length - 1) return c[c.length - 1];
        return c[i] + (c[i + 1] - c[i]) * (x - i);
    }
    function playheadMeta(t) {
        if (!front || !front.front_curve || !front.season_start) { animMeta = null; return; }
        var cv = front.front_curve, step = cv.step_days || 5;
        var dos = (t - Date.parse(front.season_start + 'T00:00:00Z')) / DAY_MS;
        var pct = curveAt(cv.front, step, dos);
        if (pct === null) { animMeta = null; return; }
        // No offset against usual at the playhead: the server's
        // `usual_offset_days` is median(front - usual) over reached cells,
        // and a curve-quantile reading here would be a second estimator
        // under the same word (invariant 7) -- and scripts/eval_usual_shift.py
        // shows both are biased tens of days early until late season.
        var key = Math.round(pct);
        if (key === animPctKey && animMeta) return;
        animPctKey = key;
        animMeta = Object.assign({}, front, { front_reached_pct: pct, usual_offset_days: null, at_playhead: true });
        emit();
    }
    function animAt(t) {
        if (!map || !map.getLayer(FRONT_LYR)) return;
        // While the animator runs it draws the vanguard chains itself, built
        // up to the playhead; the whole-season layer would show them ahead
        // of it. Hidden for the duration, back on teardown.
        var animating = t != null;
        [VAN_LYR, VAN_GAP_LYR, VAN_DIM_LYR, VAN_HEAD_LYR].forEach(function (id) {
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', (st.van && !animating) ? 'visible' : 'none');
        });
        if (t == null) {
            clearTimeout(animTrail);
            if (animMeta) { animMeta = null; emit(); }
            if (st.entry) drawEntry(windowEndMs());   // back to the slider's end
            if (animDay === null) return;
            animDay = null; animT = null;
            map.setFilter(FRONT_LYR, null);
            map.setFilter(FRONT_LBL, ['==', ['get', 'label'], true]);
            map.setFilter(FRONT_WAVE, null);
            map.setPaintProperty(FRONT_WAVE, 'line-opacity', 0);
            map.setPaintProperty(FRONT_LYR, 'line-width', ['case', ['get', 'label'], 1.6, 0.7]);
            map.setPaintProperty(FRONT_LYR, 'line-opacity', ['case', ['get', 'label'], 0.9, 0.55]);
            return;
        }
        var now = performance.now();
        if (animT !== null && Math.abs(t - animT) < 0.1 * DAY_MS) return;   // same tenth of a day: nothing to say
        playheadMeta(t);
        if (animT !== null && now - animWall < 80) {                          // ~12 repaints/s is plenty…
            // …but the LAST position of a scrub must land: trail it.
            clearTimeout(animTrail);
            animTrail = setTimeout(function () { if (animDay !== null) animAt(t); }, 90);
            return;
        }
        clearTimeout(animTrail);
        animWall = now; animT = t; animDay = new Date(t).toISOString().slice(0, 10);
        // The ground under the animated fires: full weight until the usual
        // front arrives at a cell, stepping back after; a cell lights up for
        // four days when this season's first detection lands in it.
        if (st.entry) drawEntry(t);
        var ageD = ['/', ['-', t, ['get', 't']], DAY_MS];                    // days since the season reached this line
        var reached = ['<=', ['get', 't'], t];
        map.setFilter(FRONT_LYR, reached);
        map.setFilter(FRONT_WAVE, ['all', reached, ['>=', ['get', 't'], t - 6 * DAY_MS]]);
        map.setFilter(FRONT_LBL, ['all', reached, ['any', ['==', ['get', 'label'], true], ['>=', ['get', 't'], t - 6 * DAY_MS]]]);
        map.setPaintProperty(FRONT_WAVE, 'line-opacity',
            ['interpolate', ['linear'], ageD, 0, 0.6, 2.5, 0.4, 6, 0]);
        map.setPaintProperty(FRONT_LYR, 'line-width',
            ['*', ['case', ['get', 'label'], 1.0, 0.6],
                ['interpolate', ['linear'], ageD, 0, 3.6, 4, 2.6, 10, 1.9, 40, 1.5, 120, 1.3]]);
        map.setPaintProperty(FRONT_LYR, 'line-opacity',
            ['interpolate', ['linear'], ageD, 0, 1.0, 5, 0.9, 20, 0.65, 60, 0.4, 150, 0.3]);
    }

    /* ── public ─────────────────────────────────────────────────────────── */
    var FireSeason = {
        init: function (m) {
            map = m;
            map.on('moveend', onMove);
            window.addEventListener('5mp:date-window-changed', onDates);
            window.addEventListener('5mp:focus-changed', onFocus);
        },
        isOn: anyOn,
        frontOn: function () { return st.front; },
        vanguardOn: function () { return st.van; },
        endWords: endWords, kfShown: kfShown,
        speedOn: function () { return st.speed; },
        speedMeta: function () { return speed; },
        entryOn: function () { return st.entry; },
        entryMeta: function () { return entry && entry.early_ground ? Object.assign({ area: entry.area, season: entry.season, season_start: entry.season_start }, entry.early_ground) : (entry || null); },
        entryInView: entryInViewCount,
        entryLegendHTML: entryLegendHTML,
        entryAt: function (lng, lat) { return entryField ? entryField.at(lng, lat) : null; },
        entryRenderedFor: function () { return entryRenderT; },
        ENTRY_COLOR: '#22d3ee',
        speedAt: speedAt,
        speedLegendHTML: speedLegendHTML,
        // The front as loaded, or — while the animator runs — the same
        // object with `front_reached_pct` / `usual_offset_days` read off the
        // season curve at the playhead (the server's numbers are at the
        // window's END, which is where the slider rests, not where it is).
        meta: function () { return animMeta || front; },
        vanguard: function () { return van; },
        summary: summary,
        busy: function () { return inflight > 0; },
        onChange: function (fn) { listeners.push(fn); },
        setFront: function (want) {
            st.front = !!want;
            if (!map) return;
            ensureLayers();
            if (st.front) loadFront(true); else { setData(FRONT_SRC, []); refreshStrip(); }
        },
        setVanguard: function (want) {
            st.van = !!want;
            if (!map) return;
            ensureLayers();
            if (st.van) loadVan(true); else { setData(VAN_SRC, []); refreshStrip(); }
        },
        setSpeed: function (want) {
            st.speed = !!want;
            if (!map) return;
            ensureLayers();
            if (st.speed) loadSpeed(true); else { if (speedField) speedField.clear(); refreshStrip(); }
        },
        setEntry: function (want) {
            st.entry = !!want;
            if (!map) return;
            ensureLayers();
            if (st.entry) loadEntry(true); else { if (entryField) entryField.clear(); refreshStrip(); }
        },
        off: function () { this.setFront(false); this.setVanguard(false); this.setSpeed(false); this.setEntry(false); },
        animAt: animAt,
        lift: lift,      // lodlayer.js calls it after adding a line layer
        // switchBasemap() rebuilds the style; put the layers back on idle.
        reattach: function () {
            if (!anyOn() || !map) return;
            map.once('idle', function () {
                ensureLayers();
                frontKey = ''; vanKey = ''; speedKey = ''; entryKey = '';
                if (speedField) speedField.invalidate();
                if (entryField) entryField.invalidate();
                loadFront(true); loadVan(true); loadSpeed(true); loadEntry(true);
            });
        },
        getShareParams: function () {
            if (!anyOn()) return null;
            return { season: [st.front ? 'front' : '', st.van ? 'vanguard' : '', st.speed ? 'speed' : '', st.entry ? 'entry' : ''].filter(Boolean).join(',') };
        },
        restoreFromParams: function (params) {
            var v = params.get('season');
            if (!v) return;
            var parts = v.split(',');
            if (parts.indexOf('front') >= 0) this.setFront(true);
            if (parts.indexOf('vanguard') >= 0) this.setVanguard(true);
            if (parts.indexOf('speed') >= 0) this.setSpeed(true);
            if (parts.indexOf('entry') >= 0) this.setEntry(true);
        },
        // The words the strip and the fire tip share; one definition.
        LEAD_DAYS: 10, LEAD_MAX: 60,
        leadColor: leadColor, frontColor: frontColor, tierWord: tierWord, tierWide: tierWide, legendHTML: legendHTML,
        evidenceMul: evidenceMul, widthMulExpr: widthMulExpr, leadAlpha: leadAlpha, evidenceWords: evidenceWords,
        directionFilter: directionFilter, tierDirectional: tierDirectional
    };
    window.FireSeason = FireSeason;
})();
