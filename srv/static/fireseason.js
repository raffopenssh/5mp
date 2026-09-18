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
 *             front as an arrival-time surface (1/|∇T|). Not a raster: a
 *             property OF THE FRONT LINES. /api/fire-season?speed=1 splits
 *             every isochrone into runs of one speed class
 *             (srv/fire_season_lines.go, `speed_contours`) and the same
 *             contour layer draws them heavier where the season stalled and
 *             hairline where it raced — the spacing of the lines, restated
 *             as their weight. Zero extra payload beyond the split lines,
 *             every zoom, every park in view. Descriptive, not a forecast.
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
        ENTRY_LYR = 'fireseason-entry',
        CMP_SRC = 'fireseason-cmp-src', CMP_LYR = 'fireseason-cmp', CMP_LBL = 'fireseason-cmp-label',
        PAT_SRC = 'fireseason-patrol-src', PAT_LYR = 'fireseason-patrol', PAT_LBL = 'fireseason-patrol-label', PAT_WAVE = 'fireseason-patrol-wave',
        PRS_SRC = 'fireseason-pressure-src', PRS_LYR = 'fireseason-pressure', PRS_LBL = 'fireseason-pressure-label';

    var map = null;
    var st = { front: false, van: false, speed: false, entry: false, patrol: false, pressure: false, cmp: [] };   // the season shown follows the time slider; cmp = earlier seasons drawn beside it
    var cmpData = {};      // season label → /api/fire-season answer (contours) for the compared seasons
    var cmpArea = '';      // the area cmpData belongs to
    var patrol = null;     // last /api/patrol-isochrones answer
    var patrolKey = '';
    var front = null;      // last /api/fire-season answer
    var van = null;        // last /api/fire-vanguard answer
    var speed = null;      // the reference front's speed summary ({area, season, stats, status}; from /api/fire-season?speed=1)
    var entry = null;      // last /api/fire-season?early=1 answer (its early_ground + season_start)
    var frontKey = '', vanKey = '', entryKey = '';
    var entryField = null;   // CellField renderer (early-burn ground)
    var moveTimer = null, inflight = 0;
    var listeners = [];

    function pwd() { return encodeURIComponent((typeof getPwd === 'function' ? getPwd() : '') || ''); }
    function focusId() { return (typeof aoiFocusID !== 'undefined' && aoiFocusID) ? aoiFocusID : ''; }
    function dates() {
        var f = (typeof dateFrom !== 'undefined' && dateFrom) ? dateFrom : '';
        var t = (typeof dateTo !== 'undefined' && dateTo) ? dateTo : '';
        return { from: f, to: t };
    }
    function anyOn() { return fireOn() || patrolAnyOn(); }
    function fireOn() { return st.front || st.van || st.speed || st.entry; }        // the Season chip
    function patrolAnyOn() { return st.patrol || st.pressure; }                    // the Patrols chip
    function patrolAllowed() { return window.HAS_PATROL !== false; }
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
        // The early-burn ground under every line, added first so the
        // contours and chains draw over it (CellField squares).
        if (window.CellField) {
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
                id: FRONT_WAVE, type: 'line', source: FRONT_SRC, filter: FRONT_LBL_SEL,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: { 'line-color': ['get', 'color'], 'line-width': 14, 'line-blur': 6, 'line-opacity': 0 }
            });
        }
        if (!map.getLayer(FRONT_LYR)) {
            map.addLayer({
                id: FRONT_LYR, type: 'line', source: FRONT_SRC, filter: FRONT_LINE_SEL,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': ['get', 'color'],
                    'line-dasharray': [2.5, 2],   // a contour, not a fire line — legible in greyscale
                    'line-width': frontWidthStatic(),
                    'line-opacity': frontOpacityStatic()
                }
            });
        }
        if (!map.getLayer(FRONT_LBL)) {
            map.addLayer({
                id: FRONT_LBL, type: 'symbol', source: FRONT_SRC,
                filter: ['all', FRONT_LBL_SEL, ['==', ['get', 'label'], true]],
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
        if (!map.getSource(CMP_SRC)) map.addSource(CMP_SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        if (!map.getSource(PAT_SRC)) map.addSource(PAT_SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        if (!map.getSource(PRS_SRC)) map.addSource(PRS_SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        if (!map.getLayer(CMP_LYR)) {
            // Earlier seasons beside the reference one: the same dashed
            // isochrone (it is the same object), the YEAR in the hue. Only
            // the 15-day lines outside an animation — a second season's
            // 5-day lines on top of the first's is texture, not comparison.
            map.addLayer({
                id: CMP_LYR, type: 'line', source: CMP_SRC,
                filter: ['==', ['get', 'label'], true],
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: { 'line-color': ['get', 'color'], 'line-dasharray': [2.5, 2], 'line-width': 1.3, 'line-opacity': 0.8 }
            }, FRONT_WAVE);
            map.addLayer({
                id: CMP_LBL, type: 'symbol', source: CMP_SRC,
                filter: ['==', ['get', 'label'], true],
                layout: {
                    'symbol-placement': 'line', 'symbol-spacing': 360,
                    'text-field': ['get', 'text'], 'text-size': 10,
                    'text-font': ['Noto Sans Regular'],
                    'text-letter-spacing': 0.04, 'text-max-angle': 30,
                    'text-pitch-alignment': 'viewport', 'text-rotation-alignment': 'map'
                },
                paint: { 'text-color': ['get', 'color'], 'text-halo-color': 'rgba(8,10,16,0.95)', 'text-halo-width': 1.3 }
            }, FRONT_WAVE);
        }
        if (!map.getLayer(PAT_WAVE)) {
            // Patrol isochrones: the rangers' front. Same recipe as the fire
            // front (dated lines every 5 d, labelled every 15, a wave in the
            // animator) in the patrol green, DASH-DOT so the two families
            // are told apart in greyscale before hue.
            map.addLayer({
                id: PAT_WAVE, type: 'line', source: PAT_SRC,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: { 'line-color': ['get', 'color'], 'line-width': 14, 'line-blur': 6, 'line-opacity': 0 }
            });
            map.addLayer({
                id: PAT_LYR, type: 'line', source: PAT_SRC,
                layout: { 'line-cap': 'butt', 'line-join': 'round' },
                paint: {
                    'line-color': ['get', 'color'],
                    'line-dasharray': [4, 1.6, 1, 1.6],
                    'line-width': ['case', ['get', 'label'], 1.8, 0.8],
                    'line-opacity': ['case', ['get', 'label'], 0.92, 0.55]
                }
            });
            map.addLayer({
                id: PAT_LBL, type: 'symbol', source: PAT_SRC,
                filter: ['==', ['get', 'label'], true],
                layout: {
                    'symbol-placement': 'line', 'symbol-spacing': 340,
                    'text-field': ['get', 'text'], 'text-size': 10.5,
                    'text-font': ['Noto Sans Regular'],
                    'text-letter-spacing': 0.04, 'text-max-angle': 30,
                    'text-pitch-alignment': 'viewport', 'text-rotation-alignment': 'map'
                },
                paint: { 'text-color': ['get', 'color'], 'text-halo-color': 'rgba(8,10,16,0.95)', 'text-halo-width': 1.4 }
            });
            registerPatrolTip();
        }
        if (!map.getLayer(PRS_LYR)) {
            // Pressure isopleths: the same presence field at the window's
            // end, contoured by AMOUNT (1, 2, 5, 10 … patrol-days within
            // ~5 km). SOLID thin green so they read as a different family
            // from the dash-dot isochrones (when) in greyscale; brighter
            // and wider up the ladder, every line labelled with its number.
            map.addLayer({
                id: PRS_LYR, type: 'line', source: PRS_SRC,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': ['get', 'color'],
                    'line-width': ['+', 0.6, ['*', 1.4, ['get', 't']]],
                    'line-opacity': ['+', 0.45, ['*', 0.5, ['get', 't']]]
                }
            });
            map.addLayer({
                id: PRS_LBL, type: 'symbol', source: PRS_SRC,
                layout: {
                    'symbol-placement': 'line', 'symbol-spacing': 300,
                    'text-field': ['get', 'text'], 'text-size': 10,
                    'text-font': ['Noto Sans Regular'],
                    'text-letter-spacing': 0.04, 'text-max-angle': 30,
                    'text-pitch-alignment': 'viewport', 'text-rotation-alignment': 'map'
                },
                paint: { 'text-color': ['get', 'color'], 'text-halo-color': 'rgba(8,10,16,0.95)', 'text-halo-width': 1.4 }
            });
            registerPressureTip();
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
        [CMP_LYR, CMP_LBL].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', (st.front && st.cmp.length) ? 'visible' : 'none'); });
        [PAT_LYR, PAT_LBL, PAT_WAVE].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', st.patrol ? 'visible' : 'none'); });
        [PRS_LYR, PRS_LBL].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', st.pressure ? 'visible' : 'none'); });
        [VAN_LYR, VAN_GAP_LYR, VAN_DIM_LYR, VAN_ARROW_LYR, VAN_HEAD_HALO, VAN_HEAD_LYR].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', st.van ? 'visible' : 'none'); });
        if (entryField) entryField.setVisible(st.entry);
        Object.keys(entryAreas).forEach(function (a) { entryAreas[a].field.setVisible(st.entry); });
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
        return u + speedParam();
    }
    function speedParam() { return st.speed ? '&speed=1' : ''; }

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
    /* One season's contours as features: colour by day of season, absolute
     * time `t` for the animator's age expressions, `l30` on the 30-day
     * lines (what an ashed-out season keeps), and — for a season other
     * than the reference (`mark` truthy) — the line's own year in the
     * label text, since an ash line three seasons back must still say
     * which year it was. */
    /* Two roles on one source: `lb` (the whole isochrone — labels and the
     * animator's wave run along it) and `ln` (what the line layer draws).
     * With SPEED off both are the plain contour. With speed on and the
     * answer carrying `speed_contours`, the line role moves to the split
     * runs, each with `sw` — its slowness 0..1 on the log ramp (1 = the
     * front stalled, 0 = it raced) — which the paint turns into weight;
     * the whole contour keeps the labels so a run boundary never cuts a
     * date in half. An answer without the split (an older cache, a season
     * fetched before the toggle) keeps the plain line. */
    function slowness(kmd) {
        var lo = Math.log(1), hi = Math.log(50);
        return Math.max(0, Math.min(1, (hi - Math.log(Math.max(1e-3, kmd))) / (hi - lo)));
    }
    function frontFeatures(j, mark) {
        if (!j || !j.contours || !j.contours.length) return { feats: [], bbox: null };
        var ds = j.contours.map(function (f) { return f.properties.dos; });
        var lo = Math.min.apply(null, ds), hi = Math.max.apply(null, ds);
        var bb = [180, 90, -180, -90];
        var split = st.speed && j.speed_contours && j.speed_contours.length ? j.speed_contours : null;
        function props(p) {
            var t = hi > lo ? (p.dos - lo) / (hi - lo) : 0.5;
            return { dos: p.dos, date: p.date, text: (p.text || '') + (mark ? ' \u2019' + String(p.date || '').slice(2, 4) : ''), season: j.season || '',
                color: frontColor(t), label: !!p.label, l30: !!p.label && (p.dos % 30 === 0),
                t: Date.parse((p.date || '') + 'T00:00:00Z') || 0 };
        }
        var feats = j.contours.map(function (f) {
            var q = props(f.properties);
            q.lb = true; q.ln = !split;
            (f.geometry.coordinates || []).forEach(function (line) {
                line.forEach(function (pt) {
                    if (pt[0] < bb[0]) bb[0] = pt[0]; if (pt[1] < bb[1]) bb[1] = pt[1];
                    if (pt[0] > bb[2]) bb[2] = pt[0]; if (pt[1] > bb[3]) bb[3] = pt[1];
                });
            });
            return { type: 'Feature', geometry: f.geometry, properties: q };
        });
        if (split) {
            split.forEach(function (f) {
                var q = props(f.properties);
                q.lb = false; q.ln = true; q.kmd = f.properties.kmd; q.sw = slowness(f.properties.kmd);
                feats.push({ type: 'Feature', geometry: f.geometry, properties: q });
            });
        }
        return { feats: feats, bbox: bb };
    }
    var frontFeats = [];   // the reference season's contours (what the map shows outside an animation)
    function applyFrontData() {
        // Unfocused, the server serves ONE surface over the view (the
        // mosaic, reference included) — the reference's own contours and
        // its history stay off the map, else the seam is back.
        var mosaic = othersActive() && others.mosaic;
        var feats = mosaic ? [] : ((animT !== null && hist.feats) ? frontFeats.concat(hist.feats) : frontFeats);
        if (others.feats.length) feats = feats.concat(others.feats);
        if (animT !== null && othersHist.feats.length) feats = feats.concat(othersHist.feats);
        setData(FRONT_SRC, feats);
    }

    /* ── every park in view (no focus) ────────────────────────────────
     * The reference (`front`) is ONE area — the focus, else the park under
     * the view centre — and it is what the stats row, the compare seasons
     * and the animator's history describe. Unfocused, the map used to draw
     * only that one: two parks side by side, contours in one and none in
     * the other, which reads as "no data there". Now the other parks in
     * view come too (server bbox mode, one request), each at the season
     * the window ends in, so the picture is consistent across parks.
     *   * Focus (park or AOI) or a filter box scopes it: focus → the
     *     reference alone (the old picture); a filter box → the parks the
     *     box intersects.
     *   * Zoom thins the lines the same way for every park: every 5-day
     *     line from z 6.5, the labelled 15-day lines from z 4.5, the
     *     30-day lines below — a continent of 5-day lines is ~9 MB and a
     *     thicket. The reference is thinned to match (`thinFront`), or
     *     one park would be drawn in a different key than its neighbours.
     *   * The bbox is quantised (padded 30 %, rounded to 0.5°) so a pan
     *     inside the fetched box costs nothing; small screens ask for
     *     fewer areas (`othersLimit`) — one payload, decoded once.
     *   * Animator: the other parks' earlier seasons come one request per
     *     reference season (`at` = that season's end), so the playhead
     *     meets each park's front at its own dates; deduped by
     *     area+season. */
    var others = { key: '', feats: [], areas: {}, stats: {}, loading: false };
    var othersHist = { key: '', feats: [], loading: false };
    function filterBox() {
        return (typeof currentBbox !== 'undefined' && currentBbox && currentBbox.length === 4) ? currentBbox : null;
    }
    function linesForZoom() {
        var z = map.getZoom();
        return z >= 6.5 ? 'all' : (z >= 4.5 ? '15' : '30');
    }
    function othersLimit() {
        var small = (window.innerWidth || 1400) < 768;
        return small ? 30 : 60;
    }
    function othersBbox() {
        var fb = filterBox();
        if (fb) return fb.map(function (v) { return +v.toFixed(2); });
        var b = map.getBounds(), w = b.getEast() - b.getWest(), h = b.getNorth() - b.getSouth();
        var q = 0.5;
        return [Math.floor((b.getWest() - w * 0.3) / q) * q, Math.floor((b.getSouth() - h * 0.3) / q) * q,
                Math.ceil((b.getEast() + w * 0.3) / q) * q, Math.ceil((b.getNorth() + h * 0.3) / q) * q];
    }
    function othersActive() { return st.front && !focusId(); }
    function thinFront(feats, lines) {
        if (lines === 'all') return feats;
        return feats.filter(function (f) { return f.properties.label && (lines === '15' || f.properties.dos % 30 === 0); });
    }
    function othersFeatures(j, mark) {
        var feats = [];
        if (j && j.mosaic && j.mosaic.contours) {
            // one surface: its lines belong to no single park (`owners` lists whose ground each came from)
            var mf = frontFeatures(j.mosaic, mark ? cmpYearMark(j.mosaic.season) : '').feats;
            mf.forEach(function (f) { f.properties.mosaic = true; });
            return mf;
        }
        ((j && j.areas) || []).forEach(function (a) {
            if (front && a.area === front.area) return;   // the reference draws itself
            var ff = frontFeatures(a, mark ? cmpYearMark(a.season) : '').feats;
            ff.forEach(function (f) { f.properties.area = a.area; });
            feats = feats.concat(ff);
        });
        return feats;
    }
    function othersURL(bb, at, lines) {
        var u = '/api/fire-season?pwd=' + pwd() + '&bbox=' + bb.join(',') + '&lines=' + lines + '&limit=' + othersLimit() + speedParam();
        if (at) u += '&at=' + at;
        if (front && front.area) u += '&exclude=' + encodeURIComponent(front.area);
        return u;
    }
    function loadOthers(force) {
        if (!map) return;
        if (!othersActive()) {
            if (others.feats.length || othersHist.feats.length) { others = { key: '', feats: [], areas: {}, stats: {}, loading: false }; othersHist = { key: '', feats: [], loading: false }; applyFrontData(); }
            return;
        }
        var bb = othersBbox(), lines = linesForZoom(), at = dates().to || '';
        var key = bb.join(',') + '|' + lines + '|' + at + '|' + (front && front.area || '') + speedParam();
        if (!force && key === others.key) return;
        others.key = key; others.loading = true;
        fetch(othersURL(bb, at, lines)).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
            if (others.key !== key) return;   // a newer ask is out
            others.loading = false;
            others.areas = {};
            ((j && j.areas) || []).forEach(function (a) { others.areas[a.area] = a.season; if (a.speed_stats) others.stats[a.area] = a.speed_stats; });
            others.mosaic = !!(j && j.mosaic);
            others.feats = othersFeatures(j, false);
            others.truncated = !!(j && j.truncated);
            // the reference is thinned to the same key as its neighbours
            if (front && front.contours) frontFeats = thinFront(frontFeatures(front, '').feats, lines);
            applyFrontData();
            if (animT !== null) loadOthersHistory();
        }).catch(function () { if (others.key === key) others.loading = false; });
    }
    function loadOthersHistory() {
        if (!othersActive() || !front || !front.seasons || animT === null) return;
        var bb = othersBbox(), lines = linesForZoom();
        var want = windowSeasons(front.seasons).filter(function (s) { return s.label !== front.season; });
        var key = bb.join(',') + '|' + lines + '|' + want.map(function (s) { return s.end; }).join(',') + speedParam();
        if (key === othersHist.key || othersHist.loading) return;
        othersHist.key = key; othersHist.loading = true;
        var seen = {};
        Object.keys(others.areas).forEach(function (a) { seen[a + '|' + others.areas[a]] = true; });
        Promise.all(want.map(function (s) {
            return fetch(othersURL(bb, s.end, lines)).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
        })).then(function (answers) {
            if (othersHist.key !== key) return;
            othersHist.loading = false;
            var feats = [];
            answers.forEach(function (j) {
                if (!j || !j.areas) return;
                j.areas = j.areas.filter(function (a) { var k = a.area + '|' + a.season; if (seen[k]) return false; seen[k] = true; return true; });
                feats = feats.concat(othersFeatures(j, true));
            });
            othersHist.feats = feats;
            applyFrontData();
            if (animT !== null) animAt(animT, true);
        });
    }
    function loadFront(force) {
        if (!st.front || !map) return Promise.resolve();
        var key = (focusId() || 'pt') + '|@' + dates().to + speedParam();
        if (!force && key === frontKey && front && (focusId() || frontInView())) return Promise.resolve();
        inflight++; emit();
        return fetch(frontURL()).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
            inflight--;
            frontKey = key;
            front = j || { status: 'request failed', seasons: [] };
            setSpeedMeta(front);
            if (cmpArea !== (front.area || '')) { cmpData = {}; cmpArea = front.area || ''; hist.key = ''; hist.feats = null; }
            animMeta = null; animPctKey = null;
            if (animT !== null) playheadMeta(animT);   // a new front under a running animator
            var ff = frontFeatures(j, '');
            frontFeats = othersActive() ? thinFront(ff.feats, linesForZoom()) : ff.feats;
            if (ff.bbox) front._bbox = ff.bbox;
            applyFrontData();
            loadOthers(true);   // the reference changed: the neighbours' exclude/thinning follow it
            if (animT !== null) loadHistory();   // the other seasons of the window, for the animator
            loadCompare();   // the reference moved: the ghosts re-align to its calendar
            refreshStrip();
        }).catch(function () { inflight--; emit(); });
    }

    /* ── every season in the window (animator) ─────────────────────────
     * The map shows ONE season's front — the one the slider ends in. Over
     * a 2020–2026 window that left the animator empty for six years and
     * then drew 2026 (the report that fixed this: "contours only show up
     * in 2026"). While the animator runs, the contours of every season the
     * window touches are on the source, each at its OWN dates, so the
     * playhead meets each year's front where and when it stood; the
     * age expressions in animAt then ash a season out as the next one
     * builds — thinner, greyer, and down to its 30-day lines, so six
     * seasons read as a comparison rather than a thicket. Fetched one
     * season at a time (the compare feature's cache, cmpData) the first
     * time the animator asks; nothing outside an animation. */
    var hist = { key: '', feats: null, loading: false };
    function windowSeasons(list) {
        var d = dates(), from = d.from || '0000-01-01', to = d.to || '9999-12-31';
        return (list || []).filter(function (s) { return (s.end || s.season_end || '9999') >= from && (s.start || s.season_start || '0000') <= to; })
            .map(function (s) { return { label: s.label || s.season, start: s.start || s.season_start, end: s.end || s.season_end }; });
    }
    function loadHistory() {
        if (!st.front || !front || !front.area || !front.seasons) return;
        var want = windowSeasons(front.seasons).map(function (s) { return s.label; }).filter(function (l) { return l && l !== front.season; });
        var key = front.area + '|' + want.join(',');
        if (key === hist.key || hist.loading) return;
        var missing = want.filter(function (l) { return !cmpData[l]; });
        function build() {
            hist.key = key; hist.loading = false;
            var feats = [];
            want.forEach(function (lbl) { feats = feats.concat(frontFeatures(cmpData[lbl], cmpYearMark(lbl)).feats); });
            hist.feats = feats;
            applyFrontData();
            if (animT !== null) { animPctKey = null; playheadMeta(animT); animAt(animT, true); }
        }
        if (!missing.length) { build(); return; }
        hist.loading = true; inflight++; emit();
        Promise.all(missing.map(function (lbl) {
            var u = '/api/fire-season?pwd=' + pwd() + '&area=' + encodeURIComponent(front.area) + '&season=' + encodeURIComponent(lbl) + speedParam();
            return fetch(u).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) { cmpData[lbl] = j || { contours: [] }; }).catch(function () { cmpData[lbl] = { contours: [] }; });
        })).then(function () { inflight--; build(); refreshStrip(); });
    }
    // The season answer whose dates hold instant t: the reference, or one
    // of the history's. null = none loaded covers it.
    function seasonAnswerAt(t) {
        var day = new Date(t).toISOString().slice(0, 10);
        if (front && front.season_start && front.seasons) {
            var hit = null;
            front.seasons.forEach(function (s) { if (s.start <= day && day <= s.end) hit = s; });
            if (hit) {
                if (hit.label === front.season) return front;
                var j = cmpData[hit.label];
                return (j && j.season_start) ? j : null;
            }
        }
        return null;
    }

    /* ── compare seasons ────────────────────────────────────────────────
     * "Chinko 2020 against 2026." The slider still names the REFERENCE
     * season (no picker — a list of years beside a slider that says the
     * year is a second control for one question); the compared seasons are
     * ADDED beside it, drawn on the reference season's calendar by day of
     * season, so the 16 Jul line of 2021 lies where the front stood on the
     * same day of that season. Hue = how many seasons back, an ordinal
     * ramp that cools with distance (pink → violet → indigo → sky): the
     * reference stays in the fire's own ember, the past is the cold end.
     * Static: 15-day lines only, each labelled with its year ("16 Jul ’21").
     * Animator: each compared season shows the line the playhead's day of
     * season has just reached, with a two-week wake — where the front
     * stood THEN, beside where it stands NOW. */
    var CMP_STOPS = ['#f472b6', '#c084fc', '#a78bfa', '#818cf8', '#60a5fa', '#7dd3fc'];
    function seasonStartYear(lbl) { return parseInt(String(lbl || '').slice(0, 4), 10); }
    function cmpColor(lbl) {
        var ref = front && front.season ? seasonStartYear(front.season) : NaN;
        var k = isNaN(ref) ? 1 : Math.max(1, ref - seasonStartYear(lbl));
        return CMP_STOPS[Math.min(CMP_STOPS.length, k) - 1];
    }
    function cmpYearMark(lbl) { var y = String(lbl || ''); return '\u2019' + (y.indexOf('/') > 0 ? y.slice(-2) : y.slice(-2)); }
    function cmpAvailable() {
        // every stored season but the reference one, newest first
        if (!front || !front.seasons) return [];
        return front.seasons.map(function (x) { return x.label || x.season; }).filter(function (l) { return l && l !== front.season; }).reverse();
    }
    function buildCompareFeatures() {
        var feats = [];
        var refStart = front && front.season_start ? Date.parse(front.season_start + 'T00:00:00Z') : null;
        st.cmp.forEach(function (lbl) {
            var j = cmpData[lbl];
            if (!j || !j.contours || lbl === (front && front.season)) return;
            var col = cmpColor(lbl), start = j.season_start ? Date.parse(j.season_start + 'T00:00:00Z') : null;
            j.contours.forEach(function (f) {
                var p = f.properties || {};
                feats.push({ type: 'Feature', geometry: f.geometry, properties: {
                    cmp: lbl, dos: p.dos, date: p.date, label: !!p.label, color: col,
                    text: (p.text || '') + ' ' + cmpYearMark(lbl),
                    // on the REFERENCE season's calendar, by day of season
                    tr: (refStart != null ? refStart : (start || 0)) + (p.dos || 0) * DAY_MS
                } });
            });
        });
        setData(CMP_SRC, feats);
        applyVisibility();
    }
    function loadCompare() {
        if (!st.front || !front || !front.area || !st.cmp.length) { setData(CMP_SRC, []); return; }
        if (cmpArea !== front.area) { cmpData = {}; cmpArea = front.area; }
        var missing = st.cmp.filter(function (l) { return !cmpData[l] && l !== front.season; });
        if (!missing.length) { buildCompareFeatures(); return; }
        inflight++; emit();
        Promise.all(missing.map(function (lbl) {
            var u = '/api/fire-season?pwd=' + pwd() + '&area=' + encodeURIComponent(front.area) + '&season=' + encodeURIComponent(lbl) + speedParam();
            return fetch(u).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) { cmpData[lbl] = j || { contours: [] }; }).catch(function () { cmpData[lbl] = { contours: [] }; });
        })).then(function () { inflight--; buildCompareFeatures(); refreshStrip(); });
    }
    function compareLegendHTML(opts) {
        opts = opts || {};
        var avail = cmpAvailable();
        if (!avail.length) return '';
        var h = '<div class="fs-cmp' + (opts.cls ? ' ' + opts.cls : '') + '"><div class="fs-ramp-cap">Compare with earlier seasons \u2014 same day of season, on ' + esc(front.season) + '\u2019s calendar</div><div class="filter-strip">';
        avail.forEach(function (lbl) {
            var on = st.cmp.indexOf(lbl) >= 0, c = cmpColor(lbl);
            h += '<button type="button" class="filter-chip' + (on ? ' on' : '') + '" style="--chip:' + c + '" aria-pressed="' + on + '" ' +
                'title="' + (on ? 'Stop drawing' : 'Draw') + ' the ' + esc(lbl) + ' front beside ' + esc(front.season) + '" ' +
                'onclick="event.stopPropagation();' + (opts.onclick || 'FireSeason.toggleCompare') + '(\'' + esc(lbl) + '\')">' + esc(lbl) + '</button>';
        });
        h += '</div>';
        if (st.cmp.length) h += '<div class="fs-ramp-how">15-day lines of each season, labelled with the year; in the animator, where its front stood on the same day of season, with a two-week wake.</div>';
        h += '</div>';
        return h;
    }

    /* ── patrol isochrones ──────────────────────────────────────────────
     * The rangers' front: the day patrol presence had built up at a place
     * (≥ 3 patrol-days within ~5 km since the window's start, each
     * movement type at its own weight — the server names them), contoured
     * every 5 d in the patrol green, dash-dot. Read against the fire front
     * it answers "were the teams there before the season arrived, and did
     * the front come later than usual where they were" — the server's
     * `association` says the second in numbers, at the front's own 60 km
     * scale, as a description, never as an effect. Tenant-scoped: an
     * account without patrol tracks is refused with that reason. */
    function patrolColor(t) {            // t 0..1 early→late: bright green → pale mint
        var a = [0x22, 0xc5, 0x5e], b = [0xbb, 0xf7, 0xd0];
        return hex(lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t));
    }
    function patrolURL(from, to, area) {
        var f = area || focusId(), c = map.getCenter();
        var u = '/api/patrol-isochrones?pwd=' + pwd() + (f ? '&area=' + encodeURIComponent(f)
            : '&lon=' + c.lng.toFixed(3) + '&lat=' + c.lat.toFixed(3));
        var d = dates();
        from = from || d.from; to = to || d.to;
        if (from) u += '&from=' + from;
        if (to) u += '&to=' + to;
        // Presence is counted per season (the front's rule: the season `to`
        // falls in), so clip=1 always; the animator fetches the earlier
        // seasons of the window one by one. Also what keeps a 2020–2026
        // window under the server's 800 d cap.
        return u + '&clip=1';
    }
    function patrolInView() {
        if (!patrol || !patrol.grid) return false;
        var c = map.getCenter(), g = patrol.grid;
        return c.lng >= g.x0 && c.lng <= g.x0 + g.res * g.nx && c.lat >= g.y0 && c.lat <= g.y0 + g.res * g.ny;
    }
    // One patrol answer's isochrones as features, at their own dates.
    function patrolFeatures(j, mark) {
        if (!j || !j.contours || !j.contours.length) return [];
        var ds = j.contours.map(function (f) { return f.properties.dos; });
        var lo = Math.min.apply(null, ds), hi = Math.max.apply(null, ds);
        return j.contours.map(function (f) {
            var p = f.properties, t = hi > lo ? (p.dos - lo) / (hi - lo) : 0.5;
            return { type: 'Feature', geometry: f.geometry, properties: {
                dos: p.dos, date: p.date, text: (p.text || '') + (mark ? ' \u2019' + String(p.date || '').slice(2, 4) : ''), kind: 'patrol', season: j.season || '',
                color: patrolColor(t), label: !!p.label, l30: !!p.label && (p.dos % 30 === 0),
                t: Date.parse((p.date || '') + 'T00:00:00Z') || 0 } };
        });
    }
    var patrolFeats = [];
    function patrolOthersActive() { return patrolAnyOn() && !focusId(); }
    function applyPatrolData() {
        var feats = patrolOthersActive() ? thinFront(patrolFeats, linesForZoom()) : patrolFeats;
        if (animT !== null && phist.periods) phist.periods.forEach(function (P) { feats = feats.concat(P.feats); });
        if (pothers.feats.length) feats = feats.concat(pothers.feats);
        if (animT !== null && pothersHist.periods.length) pothersHist.periods.forEach(function (P) { feats = feats.concat(P.feats); });
        setData(PAT_SRC, feats);
    }
    // The window-end pressure rings: the reference's and every other
    // park's in view (what the map shows outside an animation).
    function applyPressureStatic() {
        var feats = pressureFeatures(patrol);
        if (pothers.prsFeats.length) feats = feats.concat(pothers.prsFeats);
        setData(PRS_SRC, feats);
    }
    function loadPatrol(force) {
        if (!patrolAnyOn() || !map) return Promise.resolve();
        var d = dates();
        var key = (focusId() || 'pt') + '|' + d.from + '|' + d.to;
        if (!force && key === patrolKey && patrol && (focusId() || patrolInView())) return Promise.resolve();
        inflight++; emit();
        return fetch(patrolURL()).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
            inflight--;
            patrolKey = key;
            patrol = j || { status: 'request failed' };
            if (pdataArea !== (patrol.area || '')) { pdata = {}; pdataArea = patrol.area || ''; phist.key = ''; phist.periods = null; }
            patrolFeats = patrolFeatures(j, '');
            applyPatrolData();
            if (patrol) patrol._prs = null;   // new visits: the playhead field restarts
            applyPressureStatic();
            loadPatrolOthers(true);   // the reference changed: the neighbours' exclude follows it
            if (animT !== null) { loadPatrolHistory(); animAt(animT, true); }
            refreshStrip();
        }).catch(function () { inflight--; emit(); });
    }

    /* ── every patrolled park in view (no focus) ─────────────────────
     * The front's rule (see `others`) for the rangers: the reference
     * `patrol` is one area, and unfocused the map used to draw only it —
     * Ruaha's isochrones with Nyerere's missing beside them. Server bbox
     * mode (/api/patrol-isochrones?bbox=) answers for every park in the
     * box that holds a patrol-day in the window, clipped to the season
     * `to` falls in, thinned by zoom like the fronts; the visits (the
     * animator's raw material, ~⅔ of the bytes) come only when an
     * animation is on (`visits=1`), so the static picture is light. Scope,
     * bbox quantisation and the small-screen limit are the front's. */
    var pothers = { key: '', areas: {}, feats: [], prsFeats: [], loading: false, truncated: false, total: 0 };
    var pothersHist = { key: '', periods: [], loading: false };
    function pothersURL(bb, from, to, lines, visits) {
        var u = '/api/patrol-isochrones?pwd=' + pwd() + '&bbox=' + bb.join(',') + '&lines=' + lines + '&limit=' + othersLimit() + '&clip=1';
        if (from) u += '&from=' + from;
        if (to) u += '&to=' + to;
        if (visits) u += '&visits=1';
        if (patrol && patrol.area) u += '&exclude=' + encodeURIComponent(patrol.area);
        return u;
    }
    function pothersFeatures(j) {
        var feats = [];
        ((j && j.areas) || []).forEach(function (a) {
            if (patrol && a.area === patrol.area) return;
            var ff = patrolFeatures(a, '');
            ff.forEach(function (f) { f.properties.area = a.area; });
            feats = feats.concat(ff);
        });
        return feats;
    }
    function loadPatrolOthers(force) {
        if (!map) return;
        if (!patrolOthersActive()) {
            if (pothers.feats.length || pothers.prsFeats.length || pothersHist.periods.length) {
                pothers = { key: '', areas: {}, feats: [], prsFeats: [], loading: false, truncated: false, total: 0 };
                pothersHist = { key: '', periods: [], loading: false };
                applyPatrolData(); applyPressureStatic();
            }
            return;
        }
        var bb = othersBbox(), lines = linesForZoom(), d = dates(), visits = animT !== null;
        var key = bb.join(',') + '|' + lines + '|' + (d.from || '') + '|' + (d.to || '') + '|' + (patrol && patrol.area || '');
        // an answer WITH visits serves a static picture too; one without
        // does not serve an animation
        if (!force && key === pothers.key && (pothers.visits || !visits)) return;
        pothers.key = key; pothers.loading = true; pothers.visits = visits;
        fetch(pothersURL(bb, d.from, d.to, lines, visits)).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
            if (pothers.key !== key) return;   // a newer ask is out
            pothers.loading = false;
            pothers.areas = {};
            var prsFeats = [];
            ((j && j.areas) || []).forEach(function (a) {
                if (patrol && a.area === patrol.area) return;
                pothers.areas[a.area] = a;
                prsFeats = prsFeats.concat(pressureFeatures(a).map(function (f) { f.properties.area = a.area; return f; }));
            });
            pothers.feats = pothersFeatures(j);
            pothers.prsFeats = prsFeats;
            pothers.truncated = !!(j && j.truncated);
            pothers.total = (j && j.total) || 0;
            applyPatrolData();
            if (animT === null) applyPressureStatic();
            if (animT !== null) { loadPatrolOthersHistory(); animAt(animT, true); }
            refreshStrip();
        }).catch(function () { if (pothers.key === key) pothers.loading = false; });
    }
    // The season list that names the window's seasons: the reference's,
    // else the fire front's, else the first neighbour's (a view centre on
    // open water still has parks in it).
    function patrolSeasonList() {
        if (patrol && patrol.seasons && patrol.seasons.length) return patrol.seasons;
        if (front && front.seasons && front.seasons.length) return front.seasons;
        for (var k in pothers.areas) if (pothers.areas[k].seasons) return pothers.areas[k].seasons;
        return [];
    }
    /* The other parks' earlier seasons (animator only): one bbox request
     * per earlier season of the window, `to` = that season's end (the
     * server picks, per park, the season that day falls in and clips to
     * it), deduped by area+season, visits included. */
    function loadPatrolOthersHistory() {
        if (!patrolOthersActive() || animT === null || pothers.loading) return;   // (called again when the current seasons land)
        var bb = othersBbox(), lines = linesForZoom(), d = dates(), from = d.from || '', to = d.to || '';
        var refLabel = patrol && patrol.season;
        var want = windowSeasons(patrolSeasonList()).map(function (s) {
            return { label: s.label, from: (from && from > s.start) ? from : s.start, to: (to && to < s.end) ? to : s.end };
        }).filter(function (P) { return P.label !== refLabel && P.from <= P.to && (!patrol || !patrol.from || P.from < patrol.from); });
        var key = bb.join(',') + '|' + lines + '|' + want.map(function (P) { return P.from + '_' + P.to; }).join(',') + '|' + Object.keys(pothers.areas).join(',');
        if (key === pothersHist.key || pothersHist.loading) return;
        pothersHist.key = key; pothersHist.loading = true;
        var seen = {};
        Object.keys(pothers.areas).forEach(function (a) { seen[a + '|' + pothers.areas[a].season] = true; });
        Promise.all(want.map(function (P) {
            return fetch(pothersURL(bb, P.from, P.to, lines, true)).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
        })).then(function (answers) {
            if (pothersHist.key !== key) return;
            pothersHist.loading = false;
            var periods = [];
            answers.forEach(function (j) {
                ((j && j.areas) || []).forEach(function (a) {
                    if (patrol && a.area === patrol.area) return;
                    var k = a.area + '|' + a.season; if (seen[k]) return; seen[k] = true;
                    var ff = patrolFeatures(a, cmpYearMark(a.season));
                    ff.forEach(function (f) { f.properties.area = a.area; });
                    periods.push({ from: a.from, to: a.to, label: a.season, j: a, feats: ff });
                });
            });
            pothersHist.periods = periods;
            applyPatrolData();
            if (animT !== null) animAt(animT, true);
        });
    }
    /* The earlier patrol seasons of the window (animator only; see the
     * front's loadHistory). One request per season, each clipped to the
     * window, cached per area; the answer holds that season's isochrones,
     * its end-of-season pressure rings, and its visits for the playhead. */
    var pdata = {}, pdataArea = '';
    var phist = { key: '', periods: null, loading: false };
    function loadPatrolHistory() {
        if (!patrolAnyOn() || !patrol || !patrol.area || !patrol.seasons || !patrol.from) return;
        var d = dates(), from = d.from || '', to = d.to || '';
        var want = windowSeasons(patrol.seasons).map(function (s) {
            return { label: s.label, from: (from && from > s.start) ? from : s.start, to: (to && to < s.end) ? to : s.end };
        }).filter(function (P) { return P.label !== patrol.season && P.from <= P.to && P.from < patrol.from; });
        var key = patrol.area + '|' + want.map(function (P) { return P.from + '_' + P.to; }).join(',');
        if (key === phist.key || phist.loading) return;
        var missing = want.filter(function (P) { return !pdata[P.from + '|' + P.to]; });
        function build() {
            phist.key = key; phist.loading = false;
            phist.periods = want.map(function (P) {
                var j = pdata[P.from + '|' + P.to] || {};
                return { from: P.from, to: P.to, label: P.label, j: j, feats: patrolFeatures(j, cmpYearMark(P.label)) };
            });
            applyPatrolData();
            if (animT !== null) animAt(animT, true);
        }
        if (!missing.length) { build(); return; }
        phist.loading = true; inflight++; emit();
        Promise.all(missing.map(function (P) {
            return fetch(patrolURL(P.from, P.to, patrol.area)).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) { pdata[P.from + '|' + P.to] = j || { status: 'request failed' }; }).catch(function () { pdata[P.from + '|' + P.to] = { status: 'request failed' }; });
        })).then(function () { inflight--; build(); refreshStrip(); });
    }
    /* Pressure: one hue ramp up the ladder (dim moss → bright mint), t = rank
     * among the levels actually cut, so the top line is always the
     * brightest whatever the maximum is. */
    function pressureColor(t) {
        var a = [0x16, 0x65, 0x34], b = [0xd9, 0xf9, 0x9d];
        return hex(lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t));
    }
    function pressureFeatures(j) {
        var pc = j && j.pressure && j.pressure.contours;
        if (!pc || !pc.length) return [];
        var n = pc.length;
        return pc.map(function (f, i) {
            var t = n > 1 ? i / (n - 1) : 1;
            f.properties.t = t;
            f.properties.color = pressureColor(t);
            f.properties.kind = 'pressure';
            return f;
        });
    }
    /* Animated pressure: the field the server contoured at the window's
     * end, rebuilt on the client at the playhead. Visits come day-sorted,
     * so a forward step only adds the days since the last frame; a scrub
     * backwards restarts from zero (11k visits × 81 kernel cells is under
     * a millisecond). Contoured here with the same marching squares. The
     * accumulator lives ON the answer (`j._prs`): one per area × season,
     * since every park in view animates at once. */
    function pressureReset(j) {
        var g = j.grid, r = j.kernel_r || 4, sg = j.kernel_cells || 2, k = [];
        for (var dy = -r; dy <= r; dy++) for (var dx = -r; dx <= r; dx++) k.push(Math.exp(-(dx * dx + dy * dy) / (2 * sg * sg)));
        j._prs = { acc: new Float64Array(g.nx * g.ny), vi: 0, day: -1, kern: k, r: r, feats: null, featsVi: -1, box: [g.nx, g.ny, -1, -1] };
        return j._prs;
    }
    function pressureFieldAt(j, dayIdx) {
        if (!j || !j.visits || !j.grid) return null;
        var prs = j._prs;
        if (!prs || dayIdx < prs.day) prs = pressureReset(j);
        var vs = j.visits, nx = j.grid.nx, ny = j.grid.ny, r = prs.r, k = prs.kern, acc = prs.acc, W = 2 * r + 1, bx = prs.box;
        while (prs.vi < vs.length && vs[prs.vi][2] <= dayIdx) {
            var v = vs[prs.vi++], ix = v[0], iy = v[1], w = v[3];
            // the touched box, one cell wider than the kernel so a contour can close
            if (ix - r - 1 < bx[0]) bx[0] = ix - r - 1; if (iy - r - 1 < bx[1]) bx[1] = iy - r - 1;
            if (ix + r + 1 > bx[2]) bx[2] = ix + r + 1; if (iy + r + 1 > bx[3]) bx[3] = iy + r + 1;
            for (var dy = -r; dy <= r; dy++) { var yy = iy + dy; if (yy < 0 || yy >= ny) continue;
                for (var dx = -r; dx <= r; dx++) { var xx = ix + dx; if (xx < 0 || xx >= nx) continue;
                    acc[yy * nx + xx] += w * k[(dy + r) * W + dx + r]; } }
        }
        prs.day = dayIdx;
        return acc;
    }
    var PRESSURE_LADDER = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000];
    function pressureContoursFor(j, t) {
        if (!j || !j.visits || !j.from) return [];
        var dayIdx = Math.floor((t - Date.parse(j.from + 'T00:00:00Z')) / DAY_MS);
        if (dayIdx < 0) return [];
        var acc = pressureFieldAt(j, dayIdx); if (!acc) return [];
        var prs = j._prs;
        // Nothing landed since the last cut (the same visits, a later day):
        // the rings have not moved, so the last features stand.
        if (prs.feats && prs.featsVi === prs.vi) return prs.feats;
        var mx = 0; for (var i = 0; i < acc.length; i++) if (acc[i] > mx) mx = acc[i];
        var g = j.grid, feats = [], lv = PRESSURE_LADDER.filter(function (l) { return l <= mx; });
        if (!lv.length) { prs.feats = feats; prs.featsVi = prs.vi; return feats; }
        // Colour by rank on the FULL window's ladder, so a line keeps its hue
        // as the season plays rather than re-tinting every time a new level
        // appears above it.
        var full = (j.pressure && j.pressure.levels && j.pressure.levels.length) ? j.pressure.levels : lv;
        lv.forEach(function (l) {
            var lines = marchingSquaresJS(acc, g.nx, g.ny, g.x0, g.y0, g.res, l, prs.box);
            if (!lines.length) return;
            var ri = full.indexOf(l), tt = full.length > 1 ? Math.max(0, ri) / (full.length - 1) : 1;
            feats.push({ type: 'Feature', geometry: { type: 'MultiLineString', coordinates: lines },
                properties: { level: l, label: true, text: String(l), t: tt, color: pressureColor(tt), kind: 'pressure', at: t, season: j.season || '', area: j.area || '' } });
        });
        prs.feats = feats; prs.featsVi = prs.vi;
        return feats;
    }
    // Is this answer's grid anywhere near the view? A park two screens
    // away is not re-contoured every frame (its rings come back the frame
    // it is panned into).
    function gridNearView(g) {
        if (!g || !map) return true;
        var b = map.getBounds(), w = b.getEast() - b.getWest(), h = b.getNorth() - b.getSouth();
        return g.x0 <= b.getEast() + w * 0.3 && g.x0 + g.res * g.nx >= b.getWest() - w * 0.3 &&
               g.y0 <= b.getNorth() + h * 0.3 && g.y0 + g.res * g.ny >= b.getSouth() - h * 0.3;
    }
    // Every patrol answer that can animate: the reference and its earlier
    // seasons, every other park in view and theirs.
    function pressurePeriods() {
        var periods = (phist.periods || []).slice();
        if (patrol && patrol.visits) periods.push({ from: patrol.from, to: patrol.to, j: patrol, label: patrol.season });
        Object.keys(pothers.areas).forEach(function (a) { var j = pothers.areas[a]; if (j.visits) periods.push({ from: j.from, to: j.to, j: j, label: j.season }); });
        pothersHist.periods.forEach(function (P) { if (P.j && P.j.visits) periods.push(P); });
        return periods;
    }
    /* The pressure picture at playhead t over every season of the window.
     * Pressure is PER SEASON, never carried across: little in the rains,
     * building before the dry season, a peak, then a new count — a ring
     * that grew for six years would say the teams never left. The season
     * t is in is rebuilt from its visits up to t (live, `at` = t); every
     * season already over keeps its end-of-season rings as the server cut
     * them (`at` = the day after it ended), which the age expressions in
     * animAt ash out; nothing for seasons still ahead. */
    function pressureAnimFeatures(t) {
        var out = [];
        pressurePeriods().forEach(function (P) {
            var j = P.j; if (!j || !j.visits || !j.from) return;
            var f0 = Date.parse(P.from + 'T00:00:00Z'), f1 = Date.parse(P.to + 'T00:00:00Z') + DAY_MS;
            if (t < f0) return;
            if (t >= f1 + ASH_MAX_DAYS * DAY_MS) return;   // ash lasts two seasons, like the lines
            if (t >= f1) {
                // A finished season keeps its PEAK CORE — the top two rungs
                // of its ladder — as ash: where the effort concentrated,
                // not every ring it ever had. Amounts are not dated lines;
                // the outer rings go with the season.
                var pf = pressureFeatures(j), top = pf.length ? Math.max.apply(null, pf.map(function (f) { return f.properties.level; })) : 0;
                var keep = PRESSURE_LADDER.filter(function (l) { return l <= top; }).slice(-2);
                pf.forEach(function (f) {
                    if (keep.indexOf(f.properties.level) < 0) return;
                    out.push({ type: 'Feature', geometry: f.geometry, properties: Object.assign({}, f.properties, { at: f1, text: f.properties.text + ' ' + cmpYearMark(P.label), season: P.label || '', area: j.area || '' }) });
                });
            } else if (gridNearView(j.grid)) out = out.concat(pressureContoursFor(j, t));
            else if (j._prs && j._prs.feats) out = out.concat(j._prs.feats);   // off screen: the last cut stands
        });
        return out;
    }
    /* Pressure under the playhead, within a CPU budget. Re-contouring
     * every park in view every frame is the one cost that scales with the
     * viewport (n parks × ladder × grid), and a phone at 12 repaints/s has
     * ~80 ms a frame for everything. So the pass is timed, and the next one
     * waits until the playhead has moved on by at least twice what the
     * last one cost (a 40 ms cut → at most one every 80 ms of wall time):
     * the rings then grow in slightly larger steps on a slow device rather
     * than the whole animation stuttering. `force` (an export frame) always
     * cuts. */
    var prsWall = { at: 0, cost: 0 };
    function pressureAnimStep(t, force) {
        if (!(st.pressure && pressurePeriods().length)) return;
        var now = performance.now();
        if (!force && now - prsWall.at < prsWall.cost * 2) return;
        var feats = pressureAnimFeatures(t);
        prsWall.cost = performance.now() - now; prsWall.at = performance.now();
        setData(PRS_SRC, feats);
    }
    // Port of srv/patrol_isochrone.go marchingSquares (segments linked into
    // polylines by shared endpoints, thinned to ~¼ cell). Two things the Go
    // one does not need, because this runs per frame on a phone for every
    // park in view: `box` = [ix0, iy0, ix1, iy1] limits the scan to the
    // cells the presence has touched (a patrol field is a few knots on a
    // 30k-cell grid), and endpoints are keyed by the integer id of the cell
    // EDGE they sit on, not by a string of their coordinates.
    function marchingSquaresJS(z, nx, ny, x0, y0, res, level, box) {
        var segs = [], keys = [];
        var bx0 = 0, by0 = 0, bx1 = nx - 1, by1 = ny - 1;
        if (box) { bx0 = Math.max(0, box[0]); by0 = Math.max(0, box[1]); bx1 = Math.min(nx - 1, box[2]); by1 = Math.min(ny - 1, box[3]); }
        function interp(ax, ay, az, bx, by, bz) { var t = (level - az) / (bz - az); t = t < 0 ? 0 : t > 1 ? 1 : t; return [ax + t * (bx - ax), ay + t * (by - ay)]; }
        for (var iy = by0; iy < by1; iy++) for (var ix = bx0; ix < bx1; ix++) {
            var v0 = z[iy * nx + ix], v1 = z[iy * nx + ix + 1], v2 = z[(iy + 1) * nx + ix + 1], v3 = z[(iy + 1) * nx + ix];
            var idx = (v0 >= level ? 1 : 0) | (v1 >= level ? 2 : 0) | (v2 >= level ? 4 : 0) | (v3 >= level ? 8 : 0);
            if (idx === 0 || idx === 15) continue;
            var cx = [x0 + (ix + 0.5) * res, x0 + (ix + 1.5) * res, x0 + (ix + 1.5) * res, x0 + (ix + 0.5) * res];
            var cy = [y0 + (iy + 0.5) * res, y0 + (iy + 0.5) * res, y0 + (iy + 1.5) * res, y0 + (iy + 1.5) * res];
            var v = [v0, v1, v2, v3];
            // edge ids: horizontal edge of cell (ix,iy) = 2·(iy·nx+ix), vertical = 2·(iy·nx+ix)+1
            var ek = [2 * (iy * nx + ix), 2 * (iy * nx + ix + 1) + 1, 2 * ((iy + 1) * nx + ix), 2 * (iy * nx + ix) + 1];
            var edge = function (e) { var a = e, b = (e + 1) % 4; return interp(cx[a], cy[a], v[a], cx[b], cy[b], v[b]); };
            var add = function (e1, e2) { segs.push([edge(e1), edge(e2)]); keys.push([ek[e1], ek[e2]]); };
            switch (idx) {
                case 1: case 14: add(3, 0); break;
                case 2: case 13: add(0, 1); break;
                case 3: case 12: add(3, 1); break;
                case 4: case 11: add(1, 2); break;
                case 6: case 9: add(0, 2); break;
                case 7: case 8: add(3, 2); break;
                default: { var c = (v0 + v1 + v2 + v3) / 4; if ((c >= level) === (idx === 5)) { add(3, 0); add(1, 2); } else { add(0, 1); add(3, 2); } }
            }
        }
        if (!segs.length) return [];
        var ends = new Map();
        for (var si = 0; si < segs.length; si++) {
            var k0 = keys[si][0], k1 = keys[si][1];
            var l0 = ends.get(k0); if (!l0) ends.set(k0, l0 = []); l0.push(si);
            var l1 = ends.get(k1); if (!l1) ends.set(k1, l1 = []); l1.push(si);
        }
        var used = new Uint8Array(segs.length), out = [];
        // take: the unused segment touching edge k, returning its far end (point + edge key)
        function take(k) { var l = ends.get(k); if (!l) return null; for (var q = 0; q < l.length; q++) { var i = l[q]; if (used[i]) continue; used[i] = 1; return keys[i][0] === k ? [segs[i][1], keys[i][1]] : [segs[i][0], keys[i][0]]; } return null; }
        for (var i = 0; i < segs.length; i++) {
            if (used[i]) continue; used[i] = 1;
            var line = [segs[i][0], segs[i][1]], headK = keys[i][1], tailK = keys[i][0], np;
            while ((np = take(headK))) { line.push(np[0]); headK = np[1]; }
            while ((np = take(tailK))) { line.unshift(np[0]); tailK = np[1]; }
            if (line.length < 3) continue;
            var thin = [[r4(line[0][0]), r4(line[0][1])]], last = line[0];
            for (var k = 1; k < line.length; k++) {
                if (Math.abs(line[k][0] - last[0]) + Math.abs(line[k][1] - last[1]) >= res * 0.25 || k === line.length - 1) { thin.push([r4(line[k][0]), r4(line[k][1])]); last = line[k]; }
            }
            if (thin.length >= 6) out.push(thin);
        }
        return out;
    }
    function r4(v) { return Math.round(v * 1e4) / 1e4; }
    /* "Drawn for n parks in view": every park-in-view layer says how many it
     * drew, and when the cap cut some (invariant 8) — a picture with a park
     * missing must not read as that park having nothing. */
    function othersNote(n, truncated, total) {
        if (!n && !truncated) return '';
        var h = '<div class="fs-ramp-how">Drawn for ' + (n + 1) + ' areas in view';
        if (truncated) h += ' — ' + Math.max(0, total - n) + ' more not drawn at this zoom (zoom in)';
        return h + '</div>';
    }
    function pressureLegendHTML(opts) {
        opts = opts || {};
        var j = patrol || {}, pr = j.pressure || {};
        var lv = pr.levels || [];
        var h = '<div class="fs-legend' + (opts.cls ? ' ' + opts.cls : '') + '">' +
            '<div class="fs-ramp-cap"><span class="fs-sw-line" style="background:linear-gradient(90deg,' + pressureColor(0) + ',' + pressureColor(1) + ')"></span> Pressure isopleths: patrol-days within ~5 km at the window\u2019s end</div>';
        if (lv.length) {
            h += '<div class="fs-ramp-how">Lines at ' + lv.map(function (v, i) { return '<b style="color:' + pressureColor(lv.length > 1 ? i / (lv.length - 1) : 1) + '">' + esc(String(v)) + '</b>'; }).join(' \u00b7 ') +
                ' \u00b7 most anywhere ' + esc(String(pr.max)) + (j.threshold ? ' \u00b7 the ' + esc(String(j.threshold)) + '-line is where the isochrones stop' : '') + '</div>';
        } else if (j.status && j.status !== 'ok') h += '<div class="fs-ramp-how">' + esc(j.status) + '</div>';
        else if (pr.max != null) h += '<div class="fs-ramp-how">Presence everywhere below 1 patrol-day (most ' + esc(String(pr.max)) + ')</div>';
        h += othersNote(Object.keys(pothers.areas).length, pothers.truncated, pothers.total);
        h += '<div class="fs-ramp-how">Where the effort went, not when: a log ladder because presence piles up around stations. A patrol-day is a 2.5 km cell with a patrol in it on a day, weighted by how it moved (foot 1 \u00b7 vehicle 0.7 \u00b7 helicopter 0.4 \u00b7 fixed-wing 0.2), spread over ~5 km.</div>';
        h += '</div>';
        return h;
    }
    function pressureTipHTML(p) {
        if (!p || p.kind !== 'pressure') return '';
        var P = null; (phist.periods || []).forEach(function (x) { if (x.label === p.season) P = x; });
        var by = P ? fmtDate(P.to) : fmtDate((patrol && patrol.to) || ''), since = P ? P.from : (patrol && patrol.from);
        var h = '<div style="font-weight:600;margin-bottom:3px;color:#bef264">Patrol pressure \u00b7 ' + esc(String(p.level)) + ' patrol-days' + (P ? ' \u00b7 ' + esc(P.label) : '') + '</div>';
        h += '<div>By ' + esc(by) + ' patrols had spent <b>' + esc(String(p.level)) + ' patrol-days</b> within ~5 km of this line' + (since ? ' since ' + esc(fmtDate(since)) : '') + '; more inside it, less outside.</div>';
        h += '<div style="opacity:.6;font-size:11px;margin-top:4px">Solid lines say how much; the dash-dot isochrones say when.</div>';
        return h;
    }
    function registerPressureTip() {
        if (!window.MapTip || !MapTip.register) return;
        MapTip.register(PRS_LYR, { html: pressureTipHTML, tabLabel: 'Pressure', tabColor: '#bef264', priority: 5 });
    }
    function patrolModeWords(j) {
        var bm = (j && j.by_mode) || {}, ks = Object.keys(bm).sort(function (a, b) { return bm[b] - bm[a]; });
        return ks.map(function (k) { return bm[k].toLocaleString() + ' ' + k.replace('_', '-'); }).join(' \u00b7 ');
    }
    function patrolAssocWords(j) {
        var a = j && j.association;
        if (!a || !a.patrolled_before_front) return '';
        var b = a.patrolled_before_front, o = a.other;
        function off(v) { return v == null ? 'too few cells to say' : v === 0 ? 'on its usual day' : Math.abs(v) + ' d ' + (v > 0 ? 'later' : 'earlier') + ' than usual'; }
        return 'Where patrols had been before the front arrived (' + b.cells.toLocaleString() + ' cells) the front came <b>' + off(b.offset_days) +
            '</b>; elsewhere (' + o.cells.toLocaleString() + ' cells) ' + off(o.offset_days) + '. A description at the front\u2019s ~60 km scale, not an effect.';
    }
    function patrolLegendHTML(opts) {
        opts = opts || {};
        var j = patrol || {};
        var sw = '<span class="fs-sw-line" style="background:repeating-linear-gradient(90deg,' + patrolColor(0.2) + ' 0 6px,transparent 6px 8px,' + patrolColor(0.2) + ' 8px 10px,transparent 10px 12px)"></span>';
        var h = '<div class="fs-legend' + (opts.cls ? ' ' + opts.cls : '') + '">' +
            '<div class="fs-ramp-cap">' + sw + ' Patrol isochrones: by this date \u2265 ' + esc(j.threshold || 3) + ' patrol-days within ~5 km</div>';
        var w = j.weights || {};
        var ws = ['foot', 'vehicle', 'boat', 'rotor_wing', 'fixed_wing'].filter(function (k) { return w[k] != null; })
            .map(function (k) { return k.replace('_', '-') + ' ' + w[k]; }).join(' \u00b7 ');
        if (ws) h += '<div class="fs-ramp-how">A patrol-day is a 2.5 km cell with a patrol in it on a day, weighted by how it moved: ' + esc(ws) + ' \u2014 a team on foot is the presence that meets a fire, an aircraft sees it.</div>';
        if (j.status === 'ok') {
            h += '<div class="fs-ramp-how">' + esc(j.patrol_days.toLocaleString()) + ' patrol-days since ' + esc(fmtDate(j.from)) + ' (' + esc(patrolModeWords(j)) + ') \u00b7 ' + esc(j.cells.toLocaleString()) + ' cells reached</div>';
            var aw = patrolAssocWords(j);
            if (aw) h += '<div class="fs-ramp-how" style="color:#bbf7d0">' + aw + '</div>';
        }
        h += othersNote(Object.keys(pothers.areas).length, pothers.truncated, pothers.total);
        h += '</div>';
        return h;
    }
    function patrolTipHTML(p) {
        if (!p || p.kind !== 'patrol') return '';
        var P = null; (phist.periods || []).forEach(function (x) { if (x.label === p.season) P = x; });
        var since = P ? P.from : (patrol && patrol.from);
        var h = '<div style="font-weight:600;margin-bottom:3px;color:#86efac">Patrol isochrone \u00b7 ' + esc(fmtDate(p.date)) + (P ? ' \u00b7 ' + esc(P.label) : '') + '</div>';
        h += '<div>By this date patrols had spent <b>\u2265 ' + esc((patrol && patrol.threshold) || 3) + ' patrol-days</b> within ~5 km of this line' + (since ? ' since ' + esc(fmtDate(since)) : '') + '.</div>';
        if (patrol && patrol.by_mode) h += '<div style="opacity:.75;margin-top:3px">' + esc(patrolModeWords(patrol)) + ' cell-days in the window; foot 1 \u00b7 vehicle 0.7 \u00b7 helicopter 0.4 \u00b7 fixed-wing 0.2</div>';
        h += '<div style="opacity:.6;font-size:11px;margin-top:4px">Read against the fire front: the dashed ember lines say when the season arrived, these say when the teams had.</div>';
        return h;
    }
    function registerPatrolTip() {
        if (!window.MapTip || !MapTip.register) return;
        MapTip.register(PAT_LYR, { html: patrolTipHTML, tabLabel: 'Patrol', tabColor: '#4ade80', priority: 5 });
    }

    /* ── speed ──────────────────────────────────────────────────────────
     * A property of the front lines (frontFeatures: `ln` runs carry `sw`,
     * their slowness 0..1 on the ramp 50 → 1 km/d). This section is the
     * paint that turns `sw` into weight, the tip that reads a run's km/day
     * back, and the legend. Nothing is computed here but weight.
     *
     * The weight is deliberately restrained: the picture is still the
     * isochrones, and where they crowd (the front stalled) they are also
     * heavier and more opaque; where they spread (it raced) they thin to a
     * hairline. One reading reinforcing the other, never a second colour
     * over the date ramp. Static: width 0.45 + 1.3·sw (×1.3 on labelled
     * lines), opacity 0.35 + 0.6·sw — a stalled line is barely heavier
     * than today's labelled one; a racing line recedes to a faint hair. Animator: the age ramp × (0.5 + sw).
     * Plain features (speed off) have no `sw`: coalesce to 0.5, i.e. the
     * old 0.7 / 1.6 px. */
    var FRONT_LINE_SEL = ['==', ['get', 'ln'], true], FRONT_LBL_SEL = ['==', ['get', 'lb'], true];
    function swExpr() { return ['coalesce', ['get', 'sw'], 0.5]; }
    function frontWidthStatic() {
        // speed off → labelled 1.6 / plain 0.7 (the old constants); on → by slowness
        return ['*', ['case', ['get', 'label'], 1.3, 1], ['case', ['has', 'sw'], ['+', 0.45, ['*', 1.3, swExpr()]], ['case', ['get', 'label'], 1.23, 0.7]]];
    }
    function frontOpacityStatic() {
        return ['case', ['has', 'sw'], ['+', 0.35, ['*', 0.6, swExpr()]], ['case', ['get', 'label'], 0.9, 0.55]];
    }
    function speedWeight(expr) {   // the animator's width ramp, weighted by slowness where the runs carry it
        return ['*', expr, ['case', ['has', 'sw'], ['+', 0.5, swExpr()], 1]];
    }
    // The strip's / legend's object: the reference front's speed summary.
    function setSpeedMeta(j) {
        if (!st.speed) { speed = null; return; }
        if (!j || j.area === null) { speed = { area: null, status: (j && j.status) || 'no area here' }; return; }
        speed = { area: j.area, season: j.season, stats: j.speed_stats || null,
                  status: j.speed_stats ? 'ok' : (j.status || (j.contours ? 'no speed for this front' : 'not yet computed')) };
    }
    // What the pointer is on: the split run under it (its km/day is the class centre).
    function speedAt(lng, lat, point) {
        if (!map || !st.speed || !map.getLayer(FRONT_LYR)) return null;
        var p = point || map.project([lng, lat]), r = 5;
        var fs = map.queryRenderedFeatures([[p.x - r, p.y - r], [p.x + r, p.y + r]], { layers: [FRONT_LYR] });
        var f = null;
        for (var i = 0; i < fs.length; i++) { if (fs[i].properties && fs[i].properties.kmd != null) { f = fs[i]; break; } }
        if (!f) return null;
        var q = f.properties, area = q.area || (front && front.area) || '';
        var sst = (speed && speed.area === area && speed.stats) || others.stats[area] || null;
        return { kmd: +q.kmd, season: q.season, area: area, dos: q.dos, date: q.date, stats: sst };
    }
    function speedWords(kmd) {
        return kmd < 2 ? 'the season stalls here' : kmd < 6 ? 'the season walks here' : kmd < 15 ? 'the season runs here' : 'the season sweeps through here';
    }
    function speedTipHTML(h) {
        var v = h.kmd < 10 ? h.kmd.toFixed(1) : Math.round(h.kmd);
        var stt = h.stats ? '<div class="maptip-meta">' + (h.area ? esc(h.area.replace(/_/g, ' ')) : 'this area') + ': median ' + h.stats.median_km_d + ' km/d (p10 ' + h.stats.p10_km_d + ', p90 ' + h.stats.p90_km_d + ')</div>' : '';
        var when = h.date ? '<div class="maptip-meta">Isochrone of ' + fmtDate(h.date) + ' (day ' + h.dos + ' of the ' + esc(h.season || '') + ' season)</div>' : '';
        return '<div class="maptip-title">Season speed: <b>~' + v + ' km/day</b></div>' +
            '<div class="maptip-body">' + speedWords(h.kmd) + ' \u2014 how fast the front travelled along this stretch, from the gradient of its arrival-time surface; the line is drawn heavier the slower it moved.</div>' + when + stt +
            '<div class="maptip-dim">Describes the season drawn; not a forecast.</div>';
    }
    var SPEED_PROBE = 'fireseason-speed-probe', speedProbeOn = false;
    function ensureSpeedProbe() {
        if (speedProbeOn || !window.MapTip || !MapTip.registerProbe) return;
        // Click only, low priority: a contour under a chain or a pin must not
        // outrank it (maptip.js "PRECEDENCE").
        MapTip.registerProbe(SPEED_PROBE, {
            priority: -5, clickOnly: true, tabLabel: 'Season speed', tabColor: '#f59e0b',
            probe: function (e) {
                if (!st.speed || !e || !e.lngLat) return null;
                var h = speedAt(e.lngLat.lng, e.lngLat.lat, e.point);
                if (!h) return null;
                return { html: speedTipHTML(h), properties: { kmd: h.kmd, season: h.season, area: h.area }, dist: 0 };
            }
        });
        speedProbeOn = true;
    }
    // Legend: a weight ramp, not a colour ramp — the colour stays the date's.
    function speedLegendHTML(opts) {
        opts = opts || {};
        var sst = speed && speed.stats;
        var cls = (sst && sst.classes) || [];
        var samples = cls.length ? [cls[0], cls[Math.floor(cls.length / 3)], cls[Math.floor(2 * cls.length / 3)], cls[cls.length - 1]] : [{ km_d: 1.3 }, { km_d: 3.4 }, { km_d: 9 }, { km_d: 39 }];
        var rows = samples.map(function (c) {
            var w = (0.45 + 1.3 * slowness(c.km_d)).toFixed(1), o = (0.35 + 0.6 * slowness(c.km_d)).toFixed(2);
            return '<div class="fs-spd-row"><span class="fs-spd-line" style="border-top:' + w + 'px dashed #fb923c;opacity:' + o + '"></span><span>' + (c.km_d < 10 ? c.km_d : Math.round(c.km_d)) + ' km/d \u2014 ' + speedWords(c.km_d).replace('the season ', '').replace(' here', '') + '</span></div>';
        }).join('');
        var stt = sst ? '<div class="fs-ramp-width">' + esc(speed.season || '') + ': median ' + sst.median_km_d + ' km/d (p10 ' + sst.p10_km_d + ', p90 ' + sst.p90_km_d + ') over ' + Number(sst.cells || 0).toLocaleString() + ' cells the front reached</div>' : '';
        var how = '<div class="fs-ramp-how">Where the isochrones crowd the front stalled; where they spread it raced. The weight says the same: a line is heavier the slower the season moved along it. Click a line for its km/day.</div>';
        var nOthers = Object.keys(others.areas || {}).length;
        return '<div class="fs-legend' + (opts.cls ? ' ' + opts.cls : '') + '"><div class="fs-ramp-cap">Season speed \u2014 line weight, km/day the front travelled</div><div class="fs-spd">' + rows + '</div>' + stt + how + othersNote(nOthers, others.truncated, 0) + '</div>';
    }
    // Shared by the entry ground (the one CellField left): a field's canvas
    // and the cell-ownership rule between overlapping park grids.
    function removeField(f) {
        f.clear();
        if (map.getLayer(f.id)) map.removeLayer(f.id);
        if (map.getSource(f.srcId)) map.removeSource(f.srcId);
    }

    function ownCells(list) {
        list.forEach(function (A) {
            var g = A.grid, n = g.nx * g.ny, m = A.mask = new Uint8Array(A.union);
            list.forEach(function (B) {
                if (B === A) return;
                var h = B.grid, nb = h.nx * h.ny;
                if (nb > n || (nb === n && B.area >= A.area)) return;   // B is not smaller: A keeps its cells there
                // B's extent in A's cell indices (both on the 0.025° lattice);
                // a cell goes to B only where B has an answer of its own — a
                // smaller grid with no front there does not punch a hole
                var dx = Math.round((h.x0 - g.x0) / g.res), dy = Math.round((h.y0 - g.y0) / g.res);
                var ix0 = Math.max(0, dx), iy0 = Math.max(0, dy), ix1 = Math.min(g.nx, dx + h.nx), iy1 = Math.min(g.ny, dy + h.ny);
                for (var iy = iy0; iy < iy1; iy++) for (var ix = ix0; ix < ix1; ix++) {
                    if (B.union[(iy - dy) * h.nx + (ix - dx)]) m[iy * g.nx + ix] = 0;
                }
            });
        });
    }

    function ownsCell(list, A, ix, iy, has) {
        var g = A.grid, n = g.nx * g.ny, gx = Math.round(g.x0 / g.res) + ix, gy = Math.round(g.y0 / g.res) + iy;
        for (var k = 0; k < list.length; k++) {
            var B = list[k]; if (B === A) continue;
            var h = B.grid, nb = h.nx * h.ny;
            if (nb > n || (nb === n && B.area >= A.area)) continue;
            if (has(B, gx, gy)) return false;
        }
        return true;
    }

    function cellPx(grid) { return grid.res / 360 * 512 * Math.pow(2, map.getZoom()); }

    function cellScale(grid) { return Math.max(4, Math.min(8, Math.round(cellPx(grid)))); }


    /* ── entry ground ───────────────────────────────────────────────────
     * The early-burn ground rides /api/fire-season (early=1, summary=1):
     * same area rule, same season (the one the window ends in), one fetch
     * per area+window. Cells arrive compact ([ix, iy, early, held, days
     * ahead, month, usual front dos] + this season's first-burn day per
     * cell) and are drawn as squares by CellField. Nothing is computed
     * here but colour. */
    /* Colour: the fire family's ROSE end, graded by share of seasons like
     * the front's isochrones are graded by date. Ember red is the fires and
     * the front, orange→yellow→white the vanguard, rust→cream the speed
     * field, amber settlements, violet clearings: rose/crimson is the one
     * warm hue none of them draw, and a square is a shape no line has.
     * Grade (share of seasons early): 40 % deep crimson → 55 % rose →
     * 70 %+ light rose, alpha rising with it. */
    var ENTRY_STOPS = [[0.40, [159, 18, 57]], [0.55, [225, 29, 72]], [0.70, [251, 113, 133]]];   // rose-800 → rose-600 → rose-400
    var ENTRY_FLASH = [255, 241, 242];       // the season's first detection lands: white-hot, cooling back to the grade over ~8 d
    var ENTRY_FADE_DAYS = 60;                // once the usual front is past a cell: −70 % over 60 d, never off
    var ENTRY_DUE_DAYS = 30;                 // the window opens: a cell brightens from dormant over the 30 d before its usual entry
    var ENTRY_FLASH_DAYS = 8;                // ignition afterglow length (days of season)
    var ENTRY_ASH_START = 45, ENTRY_ASH_DAYS = 150, ENTRY_ASH = [128, 96, 104];   // a burnt cell ashens: from 45 d after its burn, over 150 d, toward a rose-grey; never below 60 % weight
    var ENTRY_DORMANT = 0.22;                // dormant / long-past cells are nearly hidden: the animation is about what is happening
    function entryRGB(share) {
        var st = ENTRY_STOPS;
        if (share <= st[0][0]) return st[0][1];
        for (var i = 1; i < st.length; i++) {
            if (share <= st[i][0]) {
                var t = (share - st[i - 1][0]) / (st[i][0] - st[i - 1][0]), a = st[i - 1][1], b = st[i][1];
                return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
            }
        }
        return st[st.length - 1][1];
    }
    function entryHex(share) { var c = entryRGB(share); return hex(c[0], c[1], c[2]); }
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
    function entryAlpha(share) { return 0.5 + 0.42 * Math.max(0, Math.min(1, (share - 0.4) / 0.3)); }
    /* Time. dos = day of season at the slider's end / playhead; c.uf = the
     * usual front's day at this cell, c.days = how far ahead of it this
     * cell usually burns, c.fb = this season's first detection here. Entry
     * ground is a statement about where the season BEGINS, so a cell has a
     * life over the season and the animation shows it:
     *
     *   dormant   long before its usual entry: half weight — the ground is
     *             known, nothing is due
     *   due       the 30 d before its usual entry (uf − days): rises to
     *             full weight, so the season's expected arrival sweeps the
     *             ground ahead of the front as a wave, not a switch
     *   ignition  this season's first detection lands: white-hot, cooling
     *             back to the grade over 8 d (an ember, not a strobe)
     *   burnt     it did what it does this year: full weight
     *   past      the usual front is past and it has not burned: steps back
     *             −70 % over 60 d, never off (the pattern stays legible)
     *
     * With no playhead (dos null) every cell is at full weight. entryState()
     * returns {mul, flash} and is the one function the paint, the tip and
     * the legend read. */
    function entryState(c, dos, fbOverride) {
        if (dos == null) return { mul: 1, flash: 0, ash: 0, word: '' };
        var fb = fbOverride === undefined ? c.fb : fbOverride, burnt = fb != null && dos >= fb;
        if (burnt) {
            var age = dos - fb;
            if (age <= ENTRY_FLASH_DAYS) { var k = Math.exp(-age / (ENTRY_FLASH_DAYS / 3)); return { mul: 1, flash: k, ash: 0, word: age < 1 ? 'first detection today' : 'burned ' + Math.round(age) + ' d ago' }; }
            // burnt, cooling: the grade holds for 45 d, then the square
            // ashens toward rose-grey over 150 d (the ground did what it
            // does; the story is over until next season) — never off
            var ash = Math.max(0, Math.min(1, (age - ENTRY_ASH_START) / ENTRY_ASH_DAYS));
            return { mul: 1 - (1 - ENTRY_DORMANT - 0.1) * ash, flash: 0, ash: ash, word: 'burned ' + Math.round(age) + ' d ago' + (ash > 0.5 ? ' (ash)' : '') };
        }
        var uf = (c.uf != null && c.uf >= 0) ? c.uf : null;
        if (uf == null) return { mul: 1, flash: 0, ash: 0, word: '' };
        var past = dos - uf;
        if (past > 0) return { mul: 1 - (1 - ENTRY_DORMANT) * Math.min(1, past / ENTRY_FADE_DAYS), flash: 0, ash: 0, word: 'usual front passed ' + Math.round(past) + ' d ago, not yet burned' };
        var due = uf - Math.max(0, c.days || 0), lead = due - dos;   // days until the usual entry here
        if (lead <= 0) return { mul: 1, flash: 0, ash: 0, word: 'usual entry ' + Math.round(-lead) + ' d ago, not yet burned' };
        if (lead >= ENTRY_DUE_DAYS) return { mul: ENTRY_DORMANT, flash: 0, ash: 0, word: 'usual entry in ' + Math.round(lead) + ' d' };
        return { mul: ENTRY_DORMANT + (1 - ENTRY_DORMANT) * (1 - lead / ENTRY_DUE_DAYS), flash: 0, ash: 0, word: 'usual entry in ' + Math.round(lead) + ' d' };
    }
    // zoom bands: below the zoom where a cell is a few pixels, thin by
    // confidence (highest share first) so the pattern survives, like the
    // front's labels do — never drop the layer.
    function entryMinShare(z) { return z < 5.5 ? 0.7 : z < 7 ? 0.5 : 0; }
    // block size at low zoom: 2×2 (the prototype's 5 km cell) once a
    // 2.5 km cell is under ~4 px, 3×3 when under ~2 px — organised blocks
    // on the grid, never a dust of specks (CellField `block`)
    function entryBlock(z) { return z < 5.5 ? 3 : z < 7 ? 2 : 1; }
    /* The season an instant falls in, from the answer's seasons[] (start,
     * end, label) — the last that had begun when none contains it (the
     * dry-season gap belongs to the season just ended). null = unknown. */
    function seasonAt(list, T) {
        var out = null;
        (list || []).forEach(function (sn) {
            var t0 = Date.parse((sn.start || sn.season_start) + 'T00:00:00Z');
            if (isFinite(t0) && t0 <= T) out = { label: sn.label || sn.season, start: t0 };
        });
        return out;
    }
    function entryDos(t, E) {
        // day of season for a playhead / window end against the season the
        // answer names; null when unknown
        E = E || entry;
        if (!E || !E.season_start) return null;
        var t0 = Date.parse(E.season_start + 'T00:00:00Z');
        return isFinite(t0) ? (t - t0) / DAY_MS : null;
    }
    /* A cell's state at an ABSOLUTE instant: the season T falls in gives
     * the day of season and that season's first burn (c.fbs, every season
     * the writer stored); so a window that crosses a season boundary shows
     * each season's ignitions as they come, and last season's ash under
     * this season's dormant ground. Falls back to entryState on the
     * answer's own season when the list is missing. */
    function entryStateAt(c, T, E) {
        E = E || entry;
        if (T == null) return entryState(c, null);
        var sn = seasonAt(E && E.seasons, T);
        if (!sn) return entryState(c, entryDos(T, E));
        var dos = (T - sn.start) / DAY_MS;
        var fb = c.fbs && sn.label in c.fbs ? c.fbs[sn.label] : (sn.label === (E && E.season) ? c.fb : null);
        return entryState(c, dos, fb);
    }
    var entryRenderT = null;   // the instant the squares are drawn for (window end, or the playhead)
    /* One field per park in view (no focus), like the speed map:
     * `entryAreas[area]` = {j (the area's answer: season, season_start,
     * seasons, early_ground), field, cells}; the reference is among them
     * on `entryField`. */
    var entryAreas = {};
    function entryCells(eg) {
        var fb = eg.first_burn || null, all = eg.first_burn_all || null;
        return eg.cells.map(function (c, i) {
            var fbs = null;
            if (all) { fbs = {}; for (var k in all) if (all[k] && all[k][i] != null) fbs[k] = all[k][i]; }
            return { ix: c[0], iy: c[1], early: c[2], held: c[3], share: c[3] ? c[2] / c[3] : 0, days: c[4], month: c[5],
                uf: c[6], fb: fb ? fb[i] : null, fbs: fbs };
        });
    }
    function drawEntryArea(A, t, z) {
        var eg = A.j && A.j.early_ground, field = A.field;
        if (!eg || eg.status !== 'ok' || !eg.cells) { field.clear(); return; }
        if (!gridNearView(eg.grid)) return;   // off screen: drawn the frame it comes back
        var minShare = entryMinShare(z), hi = z >= 8, block = entryBlock(z), S = hi ? cellScale(eg.grid) : 1;
        var key = ['entry', A.j.area, A.j.season, minShare, S, block, t == null ? 'x' : Math.round(t / (DAY_MS / 4))].join('|');
        var E = A.j;
        field.render(function (c) {
            if (c.share < minShare) return null;
            var rgb = entryRGB(c.share), stt = entryStateAt(c, t, E);
            var a = entryAlpha(c.share) * stt.mul;
            if (stt.flash > 0) {
                var k = Math.pow(stt.flash, 0.7);   // hold the white a little, then cool
                return [lerp(rgb[0], ENTRY_FLASH[0], k), lerp(rgb[1], ENTRY_FLASH[1], k), lerp(rgb[2], ENTRY_FLASH[2], k), 255 * Math.max(a, 0.7 + 0.3 * k), k];
            }
            if (stt.ash > 0) return [lerp(rgb[0], ENTRY_ASH[0], stt.ash * 0.8), lerp(rgb[1], ENTRY_ASH[1], stt.ash * 0.8), lerp(rgb[2], ENTRY_ASH[2], stt.ash * 0.8), 255 * a];
            return [rgb[0], rgb[1], rgb[2], 255 * a];
        }, { scale: S, gutter: hi ? 0 : false, block: block, key: key });
    }
    function drawEntry(t) {
        if (!entryField || !map) return;
        entryRenderT = t;
        var z = map.getZoom(), any = false;
        Object.keys(entryAreas).forEach(function (a) { any = true; drawEntryArea(entryAreas[a], t, z); });
        if (!any) entryField.clear();
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
            Object.keys(entryAreas).forEach(function (a) { if (entryAreas[a].field === entryField) delete entryAreas[a]; });
            if (eg && eg.status === 'ok' && eg.cells && entryField) {
                var cells = entryCells(eg);
                if (entryAreas[entry.area] && entryAreas[entry.area].field !== entryField) { removeField(entryAreas[entry.area].field); delete entryAreas[entry.area]; }
                entryField.setGrid(eg.grid);
                entryField.setSparse(cells);
                entryField.invalidate();
                entryAreas[entry.area] = { j: entry, field: entryField, cells: cells };
                drawEntry(animT !== null ? animT : windowEndMs());
            } else if (entryField) entryField.clear();
            loadEntryOthers(true);
            ensureEntryProbe();
            refreshStrip();
        }).catch(function () { inflight--; emit(); });
    }
    /* Every park in view (no focus): /api/fire-season?bbox=&early=1
     * &summary=1 — the early-burn ground of each park at the season the
     * window ends in, no contours (the front layer holds those). Sparse
     * cells, ~5 KB a park gzipped. */
    var eothers = { key: '', loading: false, truncated: false, total: 0 };
    function entryOthersActive() { return st.entry && !focusId(); }
    function eothersURL(bb) {
        var d = dates(), u = '/api/fire-season?pwd=' + pwd() + '&bbox=' + bb.join(',') + '&early=1&summary=1&limit=' + othersLimit();
        if (d.to) u += '&at=' + d.to;
        if (entry && entry.area) u += '&exclude=' + encodeURIComponent(entry.area);
        return u;
    }
    function entryOwnCells() {
        var list = Object.keys(entryAreas).map(function (a) {
            var A = entryAreas[a], g = A.j.early_ground.grid, ox = Math.round(g.x0 / g.res), oy = Math.round(g.y0 / g.res), set = {};
            A.cells.forEach(function (c) { set[(oy + c.iy) * 1e6 + (ox + c.ix)] = 1; });   // lattice cell → present
            return { area: a, grid: g, A: A, set: set };
        });
        function has(B, gx, gy) { return !!B.set[gy * 1e6 + gx]; }
        list.forEach(function (L) {
            var own = L.A.cells.filter(function (c) { return ownsCell(list, L, c.ix, c.iy, has); });
            L.A.field.setSparse(own); L.A.field.invalidate();
        });
    }
    function dropEntryOthers(keep) {
        Object.keys(entryAreas).forEach(function (a) {
            var A = entryAreas[a];
            if (A.field === entryField || (keep && keep[a])) return;
            removeField(A.field);
            delete entryAreas[a];
        });
    }
    function loadEntryOthers(force) {
        if (!map || !entryField) return Promise.resolve();
        if (!entryOthersActive()) {
            if (eothers.key) { eothers = { key: '', loading: false, truncated: false, total: 0 }; dropEntryOthers(null); entryOwnCells(); drawEntry(animT !== null ? animT : windowEndMs()); }
            return Promise.resolve();
        }
        var bb = othersBbox(), d = dates();
        var key = bb.join(',') + '|@' + d.to + '|' + (entry && entry.area || '');
        if (!force && key === eothers.key) return Promise.resolve();
        eothers.key = key; eothers.loading = true;
        return fetch(eothersURL(bb)).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
            if (eothers.key !== key) return;
            eothers.loading = false;
            var keep = {};
            ((j && j.areas) || []).forEach(function (a) {
                var eg = a.early_ground;
                if (!eg || eg.status !== 'ok' || !eg.cells || (entry && a.area === entry.area)) return;
                keep[a.area] = true;
                var cells = entryCells(eg);
                var A = entryAreas[a.area], field = A && A.field !== entryField ? A.field
                    : CellField.create(map, ENTRY_LYR + '-' + a.area, { opacity: 1, minzoom: 4, beforeId: map.getLayer(FRONT_WAVE) ? FRONT_WAVE : undefined });
                field.ensure(); field.setVisible(st.entry);
                field.setGrid(eg.grid); field.setSparse(cells); field.invalidate();
                // the area's answer in the reference's shape (season, season_start, seasons, early_ground)
                entryAreas[a.area] = { j: { area: a.area, season: a.season, season_start: a.season_start, seasons: a.seasons, early_ground: eg }, field: field, cells: cells };
            });
            dropEntryOthers(keep);
            entryOwnCells();
            eothers.truncated = !!(j && j.truncated); eothers.total = (j && j.total) || 0;
            drawEntry(animT !== null ? animT : windowEndMs());
            refreshStrip();
        }).catch(function () { if (eothers.key === key) eothers.loading = false; });
    }
    var MONTHS = ['', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    function entryTipHTML(c, E) {
        E = E || entry;
        var eg = E.early_ground;
        var h = '<div class="maptip-title" style="color:' + entryHex(Math.max(0.55, c.share)) + '">Early-burn ground</div>';
        h += '<div class="maptip-body">Herds have entered here early in <b>' + c.early + ' of ' + c.held + ' seasons</b>' +
            (c.days > 0 ? ' \u00b7 typically ~<b>' + c.days + ' days</b> before the local front' : '') +
            (c.month ? ' \u00b7 usually <b>' + MONTHS[c.month] + '</b>' : '') + '</div>';
        if (c.fb != null && eg.first_burn_season) {
            var d0 = Date.parse(E.season_start + 'T00:00:00Z'), d = new Date(d0 + c.fb * DAY_MS).toISOString().slice(0, 10);
            var before = (c.uf != null && c.uf >= 0) ? c.uf - c.fb : null;
            h += '<div class="maptip-meta">Season ' + esc(eg.first_burn_season) + ': first detection here ' + fmtDate(d) +
                (before != null ? (before > 0 ? ' \u2014 ' + Math.round(before) + ' d before the usual front' : ' \u2014 ' + Math.round(-before) + ' d after the usual front') : '') + '</div>';
        }
        var stw = entryStateAt(c, entryRenderT, E).word;
        if (stw) h += '<div class="maptip-meta">At the playhead: ' + esc(stw) + '</div>';
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
            priority: -5, tabLabel: 'Early-burn ground', tabColor: entryHex(0.7),
            probe: function (e) {
                if (!st.entry || !entryField || !e || !e.lngLat) return null;
                var areas = Object.keys(entryAreas), minShare = entryMinShare(map.getZoom());
                for (var k = 0; k < areas.length; k++) {
                    var A = entryAreas[areas[k]], c = A.field.at(e.lngLat.lng, e.lngLat.lat);
                    if (!c || c.share < minShare) continue;
                    return { html: entryTipHTML(c, A.j), properties: { early: c.early, held: c.held, days_ahead: c.days, month: c.month, area: A.j.area }, dist: 0 };
                }
                return null;
            }
        });
        entryProbeOn = true;
    }
    // Legend: a graded square (the rule's floor faint → solid), and the
    // area's count beside its chance — every number from the answer.
    function entryLegendHTML(opts) {
        opts = opts || {};
        var eg = entry && entry.early_ground;
        var sw = '<span class="fs-entry-sw">' + entrySwatches() + '</span>';
        var cap = '<div class="fs-ramp-cap">' + sw + ' Early-burn ground: squares = 2.5 km cells that burn ahead of their surroundings season after season (deep crimson 40 % of seasons \u2192 light rose 70 %+)</div>';
        var line = '';
        if (eg && eg.status === 'ok') {
            line = '<div class="fs-ramp-width">' + eg.count.toLocaleString() + ' cells (' + Math.round(eg.km2).toLocaleString() + ' km\u00b2) early in \u2265 ' +
                Math.round(eg.min_share * 100) + ' % of ' + eg.seasons_held + ' seasons \u00b7 ~' + Math.round(eg.chance_cells || 0) + ' expected by chance</div>' +
                '<div class="fs-ramp-width fs-entry-life">' + entryLifeHTML() + '</div>';
        } else if (eg && eg.status) {
            line = '<div class="fs-ramp-width">' + esc(eg.reason || eg.status) + '</div>';
        }
        var nOthers = Object.keys(entryAreas).filter(function (a) { return entryAreas[a].field !== entryField; }).length;
        return '<div class="fs-legend' + (opts.cls ? ' ' + opts.cls : '') + '">' + cap + line + othersNote(nOthers, eothers.truncated, eothers.total) + '</div>';
    }
    // The graded swatch (the rule's floor, midway, 70 %+), sampled from the
    // same stops the squares draw — the panel cannot say one ramp while the
    // map draws another.
    function entrySwatches() {
        return [0.4, 0.55, 0.7].map(function (sh) { var c = entryRGB(sh); return '<i style="background:rgba(' + Math.round(c[0]) + ',' + Math.round(c[1]) + ',' + Math.round(c[2]) + ',' + entryAlpha(sh).toFixed(2) + ')"></i>'; }).join('');
    }
    // A cell's life over the season, as the animation draws it: the same
    // colours entryState() gives, so the legend is a sample of the map.
    function entryLifeHTML() {
        var c = entryRGB(0.7), a = entryAlpha(0.7);
        function sq(mul, flash, ash) {
            var r = c[0], g = c[1], b = c[2];
            if (flash) { var k = Math.pow(flash, 0.7); r = lerp(r, ENTRY_FLASH[0], k); g = lerp(g, ENTRY_FLASH[1], k); b = lerp(b, ENTRY_FLASH[2], k); }
            if (ash) { r = lerp(r, ENTRY_ASH[0], ash * 0.8); g = lerp(g, ENTRY_ASH[1], ash * 0.8); b = lerp(b, ENTRY_ASH[2], ash * 0.8); }
            return '<i style="background:rgba(' + Math.round(r) + ',' + Math.round(g) + ',' + Math.round(b) + ',' + (flash ? Math.max(a, 0.7 + 0.3 * flash) : a * mul).toFixed(2) + ')"></i>';
        }
        function item(sw, words) { return '<span class="fs-entry-life-i"><span class="fs-entry-sw">' + sw + '</span>' + words + '</span>'; }
        return 'Over a season (animated): ' + item(sq(ENTRY_DORMANT), 'dormant') + ' \u2192 ' + item(sq(1), 'due, the 30 d before its usual entry') + ' \u2192 ' +
            item(sq(1, 1) + sq(1, 0.4), 'first detection lands, cools over ' + ENTRY_FLASH_DAYS + ' d') + ' \u2192 ' +
            item(sq(ENTRY_DORMANT + 0.1, 0, 1), 'burnt: ashens over ' + ENTRY_ASH_DAYS + ' d') + ' \u00b7 ' +
            item(sq(ENTRY_DORMANT), 'usual front past, unburned (back to dormant over ' + ENTRY_FADE_DAYS + ' d)');
    }
    // The strip's count: cells / km² of early-burn ground in the viewport
    // (the same cells the squares draw; the thinning band is honoured).
    function entryInViewCount() {
        if (!entryField || !map) return null;
        var areas = Object.keys(entryAreas).filter(function (a) { var eg = entryAreas[a].j.early_ground; return eg && eg.status === 'ok'; });
        if (!areas.length) return null;
        var b = map.getBounds(), bb = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], minShare = entryMinShare(map.getZoom());
        var n = 0, km2 = 0, total = 0, seasons = entry && entry.early_ground ? entry.early_ground.seasons_held : null;
        areas.forEach(function (a) {
            var A = entryAreas[a], cells = A.field.cellsIn(bb).filter(function (c) { return c.share >= minShare; });
            n += cells.length; km2 += cells.length * A.field.cellKm2(); total += A.j.early_ground.count || 0;
        });
        return { cells: n, km2: Math.round(km2), total: total, seasons: seasons, areas: areas.length };
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
            loadFront(false); loadOthers(false); loadVan(false); loadPatrol(false); loadPatrolOthers(false);
            loadEntry(false).then(function () { loadEntryOthers(false); if (st.entry && animT === null) drawEntry(windowEndMs()); });
            if (st.entry) refreshStrip();   // the in-view count
        }, 350);
    }
    function onDates() {
        if (st.van) { vanKey = ''; loadVan(true); }
        if (st.front) loadFront(false);   // the front follows the window
        if (patrolAnyOn()) loadPatrol(false);  // presence accumulates from the window's start
        if (st.entry) loadEntry(false).then(function () { loadEntryOthers(false); if (st.entry && animT === null) drawEntry(windowEndMs()); });   // and the fade follows the window's end
    }
    function onFocus() { if (anyOn()) { frontKey = ''; entryKey = ''; patrolKey = ''; loadFront(true); loadVan(true); loadEntry(true); loadPatrol(true); } }

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
        // The season the playhead is IN — over a multi-year window that is
        // one of the history's, not the slider's end — else the reference.
        var sn = seasonAnswerAt(t) || front;
        if (!sn || !sn.front_curve || !sn.season_start) { animMeta = null; return; }
        var cv = sn.front_curve, step = cv.step_days || 5;
        var dos = (t - Date.parse(sn.season_start + 'T00:00:00Z')) / DAY_MS;
        var pct = curveAt(cv.front, step, dos);
        if (pct === null) { animMeta = null; return; }
        // No offset against usual at the playhead: the server's
        // `usual_offset_days` is median(front - usual) over reached cells,
        // and a curve-quantile reading here would be a second estimator
        // under the same word (invariant 7) -- and scripts/eval_usual_shift.py
        // shows both are biased tens of days early until late season.
        var key = Math.round(pct) + '|' + (sn.season || '');
        if (key === animPctKey && animMeta) return;
        animPctKey = key;
        animMeta = Object.assign({}, front, sn, { front_reached_pct: pct, usual_offset_days: null, at_playhead: true, seasons: front.seasons });
        emit();
    }
    /* `force`: draw this exact instant now, skipping the 80 ms coalescing
       below. The throttle exists for a playhead the eye is watching (12
       repaints/s is plenty, and re-contouring the pressure field is not free);
       an EXPORT is not being watched -- it asks for n specific instants and
       must get all n, or the GIF's contours stand still while its fires move.
       A frame dropped on screen is invisible; a frame dropped in a file is
       the file being wrong. */
    /* Ash. A season that is over stays on the map as a comparison, not as
     * ink: over ~a year after the front passed a line it goes from its own
     * colour to a neutral grey, from 1.3 px to 0.8, from 0.3 opacity to
     * ~0.1, and sheds first its 5-day lines (after 8 months) and then its
     * 15-day lines (after 20 months), keeping the 30-day ones. Subtle on
     * purpose: six seasons of 5-day lines at full weight is a thicket; six
     * seasons of grey 30-day lines is what "last year it stood here" looks
     * like. The ash is warm for fire and cool for patrols so a reader who
     * has lost the dash pattern still has the family. */
    var ASH_FIRE = '#8d8380', ASH_PATROL = '#7f8d84';
    // How long ash lasts. A season's 30-day lines stay through the NEXT
    // season as "last year it stood here", and go when the one after that
    // begins: six years of kept 30-day lines over a 2020–2026 window was a
    // thicket again, one rung up (`/s/rwv2rz5`, 2026-09-17). Two seasons,
    // in days, since a season is a year.
    var ASH_MAX_DAYS = 2 * 365;
    function ashColor(ageD, ash) { return ['interpolate', ['linear'], ageD, 150, ['get', 'color'], 330, ash]; }
    function ashOpacity(ageD, stops) { return ['interpolate', ['linear'], ageD].concat(stops, [365, 0.2, 800, 0.14, 2000, 0.1]); }
    function ashWidth(ageD, stops) { return ['*', ['case', ['get', 'label'], 1.0, 0.6], ['interpolate', ['linear'], ageD].concat(stops, [400, 1.0, 1500, 0.8])]; }
    function ashLineFilter(t) {   // which lines survive at what age (ms)
        return ['all', ['<=', ['get', 't'], t], ['>=', ['get', 't'], t - ASH_MAX_DAYS * DAY_MS],
            ['any', ['>=', ['get', 't'], t - 240 * DAY_MS],
                ['all', ['==', ['get', 'label'], true], ['>=', ['get', 't'], t - 600 * DAY_MS]],
                ['==', ['get', 'l30'], true]]];
    }
    function ashLabelFilter(t) {  // this season's labels; only the 30-day lines (with their year) once ashed
        return ['all', ['<=', ['get', 't'], t], ['>=', ['get', 't'], t - ASH_MAX_DAYS * DAY_MS],
            ['any', ['>=', ['get', 't'], t - 6 * DAY_MS],
                ['all', ['==', ['get', 'label'], true], ['>=', ['get', 't'], t - 200 * DAY_MS]],
                ['==', ['get', 'l30'], true]]];
    }
    function ashTextOpacity(ageD) { return ['interpolate', ['linear'], ageD, 150, 1, 365, 0.55, 2000, 0.4]; }
    function animAt(t, force) {
        if (!map || !map.getLayer(FRONT_LYR)) { if (map && st.entry) drawEntry(t == null ? windowEndMs() : t); return; }
        // While the animator runs it draws the vanguard chains itself, built
        // up to the playhead; the whole-season layer would show them ahead
        // of it. Hidden for the duration, back on teardown.
        var animating = t != null;
        [VAN_LYR, VAN_GAP_LYR, VAN_DIM_LYR, VAN_HEAD_LYR, VAN_ARROW_LYR].forEach(function (id) {
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', (st.van && !animating) ? 'visible' : 'none');
        });
        // Pressure follows the playhead: the field is rebuilt from the
        // visits up to that day and re-contoured, so the rings grow as the
        // effort lands. Back to the server's window-end lines on teardown.
        if (!animating && st.pressure) applyPressureStatic();
        if (t == null) {
            clearTimeout(animTrail);
            if (animMeta) { animMeta = null; emit(); }
            if (st.entry) drawEntry(windowEndMs());   // back to the slider's end
            if (animDay === null) return;
            animDay = null; animT = null;
            applyFrontData(); applyPatrolData();   // the reference season only, again
            map.setFilter(FRONT_LYR, FRONT_LINE_SEL);
            map.setFilter(FRONT_LBL, ['all', FRONT_LBL_SEL, ['==', ['get', 'label'], true]]);
            map.setFilter(FRONT_WAVE, FRONT_LBL_SEL);
            map.setPaintProperty(FRONT_WAVE, 'line-opacity', 0);
            map.setPaintProperty(FRONT_LYR, 'line-color', ['get', 'color']);
            map.setPaintProperty(FRONT_LYR, 'line-width', frontWidthStatic());
            map.setPaintProperty(FRONT_LYR, 'line-opacity', frontOpacityStatic());
            map.setPaintProperty(FRONT_LBL, 'text-color', ['get', 'color']);
            map.setPaintProperty(FRONT_LBL, 'text-opacity', 1);
            if (map.getLayer(CMP_LYR)) { map.setFilter(CMP_LYR, ['==', ['get', 'label'], true]); map.setFilter(CMP_LBL, ['==', ['get', 'label'], true]); map.setPaintProperty(CMP_LYR, 'line-width', 1.3); map.setPaintProperty(CMP_LYR, 'line-opacity', 0.8); }
            if (map.getLayer(PAT_LYR)) {
                map.setFilter(PAT_LYR, null); map.setFilter(PAT_LBL, ['==', ['get', 'label'], true]); map.setFilter(PAT_WAVE, null);
                map.setPaintProperty(PAT_WAVE, 'line-opacity', 0);
                map.setPaintProperty(PAT_LYR, 'line-color', ['get', 'color']);
                map.setPaintProperty(PAT_LYR, 'line-width', ['case', ['get', 'label'], 1.8, 0.8]);
                map.setPaintProperty(PAT_LYR, 'line-opacity', ['case', ['get', 'label'], 0.92, 0.55]);
                map.setPaintProperty(PAT_LBL, 'text-color', ['get', 'color']);
                map.setPaintProperty(PAT_LBL, 'text-opacity', 1);
            }
            if (map.getLayer(PRS_LYR)) {
                map.setFilter(PRS_LBL, null);
                map.setPaintProperty(PRS_LYR, 'line-color', ['get', 'color']);
                map.setPaintProperty(PRS_LYR, 'line-width', ['+', 0.6, ['*', 1.4, ['get', 't']]]);
                map.setPaintProperty(PRS_LYR, 'line-opacity', ['+', 0.45, ['*', 0.5, ['get', 't']]]);
            }
            return;
        }
        var now = performance.now();
        if (!force && animT !== null && Math.abs(t - animT) < 0.1 * DAY_MS) return;   // same tenth of a day: nothing to say
        var starting = animDay === null;
        playheadMeta(t);
        if (!force && animT !== null && now - animWall < 80) {                          // ~12 repaints/s is plenty…
            // …but the LAST position of a scrub must land: trail it.
            clearTimeout(animTrail);
            animTrail = setTimeout(function () { if (animDay !== null) animAt(t); }, 90);
            return;
        }
        clearTimeout(animTrail);
        animWall = now; animT = t; animDay = new Date(t).toISOString().slice(0, 10);
        if (starting) {
            // Every season the window touches goes on the sources (the
            // earlier ones fetched once), so the playhead meets each year's
            // front at its own dates rather than waiting for the last one.
            loadHistory(); loadPatrolHistory(); loadOthersHistory(); loadPatrolOthers(false); loadPatrolOthersHistory();
            applyFrontData(); applyPatrolData();
        }
        // The ground under the animated fires: full weight until the usual
        // front arrives at a cell, stepping back after; a cell lights up for
        // four days when this season's first detection lands in it.
        if (st.entry) drawEntry(t);
        pressureAnimStep(t, force);
        var ageD = ['/', ['-', t, ['get', 't']], DAY_MS];                    // days since the season reached this line
        var reached = ['<=', ['get', 't'], t];
        map.setFilter(FRONT_LYR, ['all', FRONT_LINE_SEL, ashLineFilter(t)]);
        map.setFilter(FRONT_WAVE, ['all', FRONT_LBL_SEL, reached, ['>=', ['get', 't'], t - 6 * DAY_MS]]);
        map.setFilter(FRONT_LBL, ['all', FRONT_LBL_SEL, ashLabelFilter(t)]);
        map.setPaintProperty(FRONT_WAVE, 'line-opacity',
            ['interpolate', ['linear'], ageD, 0, 0.6, 2.5, 0.4, 6, 0]);
        map.setPaintProperty(FRONT_LYR, 'line-color', ashColor(ageD, ASH_FIRE));
        map.setPaintProperty(FRONT_LYR, 'line-width', speedWeight(ashWidth(ageD, [0, 3.6, 4, 2.6, 10, 1.9, 40, 1.5, 120, 1.3])));
        map.setPaintProperty(FRONT_LYR, 'line-opacity', ashOpacity(ageD, [0, 1.0, 5, 0.9, 20, 0.65, 60, 0.4, 150, 0.3]));
        map.setPaintProperty(FRONT_LBL, 'text-color', ashColor(ageD, ASH_FIRE));
        map.setPaintProperty(FRONT_LBL, 'text-opacity', ashTextOpacity(ageD));
        if (map.getLayer(CMP_LYR)) {
            // each compared season: the line its front had just reached on
            // this day of season, and a two-week wake behind it
            var ageR = ['/', ['-', t, ['get', 'tr']], DAY_MS];
            var wake = ['all', ['<=', ['get', 'tr'], t], ['>=', ['get', 'tr'], t - 15 * DAY_MS]];
            map.setFilter(CMP_LYR, wake);
            map.setFilter(CMP_LBL, ['all', ['<=', ['get', 'tr'], t], ['>=', ['get', 'tr'], t - 5 * DAY_MS]]);
            map.setPaintProperty(CMP_LYR, 'line-width', ['interpolate', ['linear'], ageR, 0, 2.6, 5, 1.4, 15, 0.8]);
            map.setPaintProperty(CMP_LYR, 'line-opacity', ['interpolate', ['linear'], ageR, 0, 0.95, 5, 0.6, 15, 0.25]);
        }
        if (map.getLayer(PAT_LYR)) {
            map.setFilter(PAT_LYR, ashLineFilter(t));
            map.setFilter(PAT_WAVE, ['all', reached, ['>=', ['get', 't'], t - 6 * DAY_MS]]);
            map.setFilter(PAT_LBL, ashLabelFilter(t));
            map.setPaintProperty(PAT_WAVE, 'line-opacity', ['interpolate', ['linear'], ageD, 0, 0.55, 2.5, 0.35, 6, 0]);
            map.setPaintProperty(PAT_LYR, 'line-color', ashColor(ageD, ASH_PATROL));
            map.setPaintProperty(PAT_LYR, 'line-width', ashWidth(ageD, [0, 3.6, 4, 2.6, 10, 2.0, 40, 1.7, 120, 1.5]));
            map.setPaintProperty(PAT_LYR, 'line-opacity', ashOpacity(ageD, [0, 1.0, 5, 0.9, 20, 0.7, 60, 0.5, 150, 0.4]));
            map.setPaintProperty(PAT_LBL, 'text-color', ashColor(ageD, ASH_PATROL));
            map.setPaintProperty(PAT_LBL, 'text-opacity', ashTextOpacity(ageD));
        }
        if (map.getLayer(PRS_LYR)) {
            // Live rings (`at` = playhead) at full weight; a finished
            // season's end-of-season rings ash out from the day it ended,
            // and only the live season's rings carry their numbers (an
            // ashed ring's number and year are in its tip).
            var ageP = ['/', ['-', t, ['coalesce', ['get', 'at'], t]], DAY_MS];
            var rank = ['coalesce', ['get', 't'], 1];
            map.setFilter(PRS_LBL, ['>=', ['coalesce', ['get', 'at'], t], t - 1 * DAY_MS]);
            map.setPaintProperty(PRS_LYR, 'line-color', ['interpolate', ['linear'], ageP, 30, ['get', 'color'], 240, ASH_PATROL]);
            map.setPaintProperty(PRS_LYR, 'line-width', ['*', ['+', 0.6, ['*', 1.4, rank]], ['interpolate', ['linear'], ageP, 0, 1, 365, 0.7, 1500, 0.55]]);
            map.setPaintProperty(PRS_LYR, 'line-opacity', ['*', ['+', 0.45, ['*', 0.5, rank]], ['interpolate', ['linear'], ageP, 0, 1, 30, 0.6, 150, 0.35, 365, 0.22, 800, 0.15, 2000, 0.1]]);
        }
    }

    /* ── public ─────────────────────────────────────────────────────────── */
    var FireSeason = {
        init: function (m) {
            map = m;
            map.on('moveend', onMove);
            window.addEventListener('5mp:date-window-changed', onDates);
            window.addEventListener('5mp:focus-changed', onFocus);
            window.addEventListener('5mp:bbox-changed', function () { if (st.front) loadOthers(true); });
        },
        isOn: anyOn,
        frontOn: function () { return st.front; },
        vanguardOn: function () { return st.van; },
        endWords: endWords, kfShown: kfShown,
        speedOn: function () { return st.speed; },
        speedMeta: function () { return speed; },
        entryOn: function () { return st.entry; },
        // test hook: the CellField behind an entry layer id (reference or another park's)
        entryFieldFor: function (layerId) { var hit = null; Object.keys(entryAreas).forEach(function (a) { if (entryAreas[a].field.id === layerId) hit = entryAreas[a].field; }); return hit; },
        entryMeta: function () { return entry && entry.early_ground ? Object.assign({ area: entry.area, season: entry.season, season_start: entry.season_start }, entry.early_ground) : (entry || null); },
        entryInView: entryInViewCount,
        entryLegendHTML: entryLegendHTML,
        entryAt: function (lng, lat) { return entryField ? entryField.at(lng, lat) : null; },
        entryRenderedFor: function () { return entryRenderT; },
        ENTRY_COLOR: entryHex(0.7), entryColor: entryHex, entryState: entryState, entryStateAt: entryStateAt,
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
            if (!st.front && st.speed) { st.speed = false; speed = null; }   // speed is a property of the lines: no lines, no speed
            if (!map) return;
            ensureLayers();
            if (st.front) loadFront(true); else { others = { key: '', feats: [], areas: {}, stats: {}, loading: false }; othersHist = { key: '', feats: [], loading: false }; setData(FRONT_SRC, []); refreshStrip(); }
        },
        setVanguard: function (want) {
            st.van = !!want;
            if (!map) return;
            ensureLayers();
            if (st.van) loadVan(true); else { setData(VAN_SRC, []); refreshStrip(); }
        },
        setSpeed: function (want) {
            // Speed rides the front lines (`speed_contours` on every front
            // answer), so the toggle refetches the fronts with or without the
            // split runs: reference, neighbours, and the animator's / compare's
            // earlier seasons (their cache is per season, so it is emptied).
            st.speed = !!want;
            if (st.speed) st.front = true;
            if (!st.speed) speed = null;
            if (!map) return;
            ensureLayers();
            cmpData = {}; hist.key = ''; hist.feats = null; others.key = ''; othersHist.key = ''; othersHist.feats = [];
            if (st.speed) ensureSpeedProbe();
            loadFront(true);
        },
        setEntry: function (want) {
            st.entry = !!want;
            if (!map) return;
            ensureLayers();
            if (st.entry) loadEntry(true); else { if (entryField) entryField.clear(); loadEntryOthers(false); refreshStrip(); }
        },
        patrolOn: function () { return st.patrol; },
        patrolMeta: function () { return patrol; },
        patrolAllowed: patrolAllowed,
        patrolLegendHTML: patrolLegendHTML,
        patrolAssocWords: function () { return patrolAssocWords(patrol); },
        setPatrol: function (want) {
            st.patrol = !!want && patrolAllowed();
            if (!map) return;
            ensureLayers(); applyVisibility();
            if (st.patrol) loadPatrol(patrolKey === ''); else if (!st.pressure) { patrol = null; patrolKey = ''; patrolFeats = []; loadPatrolOthers(false); setData(PAT_SRC, []); setData(PRS_SRC, []); refreshStrip(); } else refreshStrip();
        },
        pressureOn: function () { return st.pressure; },
        pressureLegendHTML: pressureLegendHTML,
        setPressure: function (want) {
            st.pressure = !!want && patrolAllowed();
            if (!map) return;
            ensureLayers(); applyVisibility();
            if (st.pressure) loadPatrol(patrolKey === ''); else if (!st.patrol) { patrol = null; patrolKey = ''; patrolFeats = []; loadPatrolOthers(false); setData(PAT_SRC, []); setData(PRS_SRC, []); refreshStrip(); } else refreshStrip();
        },
        fireOn: fireOn, patrolAnyOn: patrolAnyOn,
        patrolsOff: function () { this.setPatrol(false); this.setPressure(false); },
        compare: function () { return st.cmp.slice(); },
        compareAvailable: cmpAvailable,
        compareColor: cmpColor,
        compareLegendHTML: compareLegendHTML,
        setCompare: function (list) {
            st.cmp = (list || []).filter(Boolean);
            if (!map) return;
            ensureLayers();
            if (st.cmp.length && !st.front) this.setFront(true); else loadCompare();
            refreshStrip();
        },
        toggleCompare: function (lbl) {
            var i = st.cmp.indexOf(lbl);
            if (i >= 0) st.cmp.splice(i, 1); else st.cmp.push(lbl);
            this.setCompare(st.cmp);
        },
        off: function () { this.setFront(false); this.setVanguard(false); this.setSpeed(false); this.setEntry(false); this.setCompare([]); },   // the Season chip's ×; the Patrols chip has its own (patrolsOff)
        animAt: animAt,
        lift: lift,      // lodlayer.js calls it after adding a line layer
        // switchBasemap() rebuilds the style; put the layers back on idle.
        reattach: function () {
            if (!anyOn() || !map) return;
            map.once('idle', function () {
                ensureLayers();
                frontKey = ''; vanKey = ''; entryKey = ''; patrolKey = '';
                if (entryField) entryField.invalidate();
                loadFront(true); loadVan(true); loadEntry(true); loadPatrol(true);
            });
        },
        getShareParams: function () {
            if (!anyOn()) return null;
            var p = { season: [st.front ? 'front' : '', st.van ? 'vanguard' : '', st.speed ? 'speed' : '', st.entry ? 'entry' : '', st.patrol ? 'patrol' : '', st.pressure ? 'pressure' : ''].filter(Boolean).join(',') };
            if (st.cmp.length) p.season_vs = st.cmp.join(',');
            return p;
        },
        restoreFromParams: function (params) {
            var v = params.get('season');
            if (!v) return;
            var parts = v.split(',');
            if (parts.indexOf('front') >= 0) this.setFront(true);
            if (parts.indexOf('vanguard') >= 0) this.setVanguard(true);
            if (parts.indexOf('speed') >= 0) this.setSpeed(true);
            if (parts.indexOf('entry') >= 0) this.setEntry(true);
            if (parts.indexOf('patrol') >= 0 && patrolAllowed()) this.setPatrol(true);   // a link naming patrol is dropped, not switched on, for an account without it
            if (parts.indexOf('pressure') >= 0 && patrolAllowed()) this.setPressure(true);
            var vs = params.get('season_vs');
            if (vs) this.setCompare(vs.split(',').filter(Boolean));
        },
        // The words the strip and the fire tip share; one definition.
        LEAD_DAYS: 10, LEAD_MAX: 60,
        leadColor: leadColor, frontColor: frontColor, tierWord: tierWord, tierWide: tierWide, legendHTML: legendHTML,
        evidenceMul: evidenceMul, widthMulExpr: widthMulExpr, leadAlpha: leadAlpha, evidenceWords: evidenceWords,
        directionFilter: directionFilter, tierDirectional: tierDirectional
    };
    window.FireSeason = FireSeason;
})();
