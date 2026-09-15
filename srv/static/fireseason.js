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
 *             front as an arrival-time surface (1/|∇T|), drawn as one PNG
 *             at the grid's 2.5 km cells (/api/fire-season-speed). Where the
 *             season runs and where it stalls. Descriptive, not a forecast.
 *   VANGUARD  the fire chains that BEGAN 10–60 days ahead of that front —
 *             the one population where the tracker's day-to-day links are
 *             measurably better than chance (link skill ≈0.5; in season ≈0).
 *             Each chain is drawn bright while it is still ahead of the
 *             front and faint once the season has caught up with it.
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
        SPEED_SRC = 'fireseason-speed-src', SPEED_LYR = 'fireseason-speed';

    var map = null;
    var st = { front: false, van: false, speed: false };   // the season shown follows the time slider
    var front = null;      // last /api/fire-season answer
    var van = null;        // last /api/fire-vanguard answer
    var speed = null;      // last /api/fire-season-speed answer
    var frontKey = '', vanKey = '', speedKey = '';
    var moveTimer = null, inflight = 0;
    var listeners = [];

    function pwd() { return encodeURIComponent((typeof getPwd === 'function' ? getPwd() : '') || ''); }
    function focusId() { return (typeof aoiFocusID !== 'undefined' && aoiFocusID) ? aoiFocusID : ''; }
    function dates() {
        var f = (typeof dateFrom !== 'undefined' && dateFrom) ? dateFrom : '';
        var t = (typeof dateTo !== 'undefined' && dateTo) ? dateTo : '';
        return { from: f, to: t };
    }
    function anyOn() { return st.front || st.van || st.speed; }
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
            '<span class="fs-wi"><span class="fs-w fs-w-ash"></span>season caught up</span></div>';
        return '<div class="fs-legend' + (opts.cls ? ' ' + opts.cls : '') + '"' + (opts.title ? ' title="' + esc(opts.title) + '"' : '') + '>' +
            '<div class="fs-ramp-cap">Line colour: days ahead of the season front</div>' + bar + ticks + width + '</div>';
    }

    /* ── layers ─────────────────────────────────────────────────────────── */
    function ensureLayers() {
        if (!map || !map.getStyle()) return;
        if (!map.getSource(SPEED_SRC)) {
            // A raster field under every line: added first so the contours
            // and chains draw over it. Coordinates are replaced per answer.
            map.addSource(SPEED_SRC, { type: 'image', url: BLANK_PNG,
                coordinates: [[0, 0.001], [0.001, 0.001], [0.001, 0], [0, 0]] });
        }
        if (!map.getLayer(SPEED_LYR)) {
            map.addLayer({ id: SPEED_LYR, type: 'raster', source: SPEED_SRC,
                paint: { 'raster-opacity': 0.5, 'raster-resampling': 'linear', 'raster-fade-duration': 0 } });
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
            registerTip();
        }
        applyVisibility();
    }
    function lift() {
        if (!map) return;
        [VAN_DIM_LYR, VAN_GAP_LYR, VAN_LYR].forEach(function (id) { if (map.getLayer(id)) map.moveLayer(id); });
    }
    function applyVisibility() {
        if (!map) return;
        [FRONT_LYR, FRONT_LBL, FRONT_WAVE].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', st.front ? 'visible' : 'none'); });
        [VAN_LYR, VAN_GAP_LYR, VAN_DIM_LYR].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', st.van ? 'visible' : 'none'); });
        if (map.getLayer(SPEED_LYR)) map.setLayoutProperty(SPEED_LYR, 'visibility', st.speed ? 'visible' : 'none');
    }
    // 1×1 transparent PNG: an image source needs a url at creation.
    var BLANK_PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
    function setSpeedImage(j) {
        var src = map && map.getSource(SPEED_SRC);
        if (!src) return;
        if (!j || !j.png || !j.bbox) {
            src.updateImage({ url: BLANK_PNG, coordinates: [[0, 0.001], [0.001, 0.001], [0.001, 0], [0, 0]] });
            return;
        }
        var b = j.bbox;   // [w, s, e, n] → MapLibre wants TL, TR, BR, BL
        src.updateImage({ url: j.png, coordinates: [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]] });
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
    // The PNG, decoded once into pixels, and the palette inverted: a click
    // on the field answers "how fast does the season travel HERE" from the
    // very image the map draws (one payload, one truth). Grid row 0 is the
    // south edge; the image was written top-down, so row = ny-1-iy.
    var speedPix = null;   // {data, w, h, bbox, byColor: {rgbKey: level}, lv}
    function decodeSpeed(j) {
        speedPix = null;
        if (!j || !j.png || !j.palette || !j.grid) return;
        var img = new Image();
        img.onload = function () {
            try {
                var cv = document.createElement('canvas');
                cv.width = img.width; cv.height = img.height;
                var ctx = cv.getContext('2d', { willReadFrequently: true });
                ctx.drawImage(img, 0, 0);
                var byColor = {};
                j.palette.forEach(function (hx, i) { byColor[parseInt(hx.slice(1), 16)] = i; });
                speedPix = { data: ctx.getImageData(0, 0, cv.width, cv.height).data, w: cv.width, h: cv.height,
                    bbox: j.bbox, grid: j.grid, byColor: byColor, lv: j.levels, season: j.season, area: j.area };
            } catch (e) { speedPix = null; }
        };
        img.src = j.png;
    }
    function speedAt(lng, lat) {
        var P = speedPix;
        if (!P) return null;
        var b = P.bbox;
        if (lng < b[0] || lng > b[2] || lat < b[1] || lat > b[3]) return null;
        // The grid's own arithmetic ((v - origin) / res, floor from the SOUTH
        // edge, then flip), so a point on a cell edge lands in the cell the
        // server put it in; scaling by the bbox instead differed in the last
        // bit and moved an edge point one row.
        var G = P.grid;
        var ix = Math.max(0, Math.min(P.w - 1, Math.floor((lng - G.x0) / G.res)));
        var iy = P.h - 1 - Math.max(0, Math.min(P.h - 1, Math.floor((lat - G.y0) / G.res)));
        var o = (iy * P.w + ix) * 4;
        if (P.data[o + 3] === 0) return null;   // no front here this season
        var lvl = P.byColor[(P.data[o] << 16) | (P.data[o + 1] << 8) | P.data[o + 2]];
        if (lvl == null) return null;
        var lo = Math.log(P.lv.km_d_min), hi = Math.log(P.lv.km_d_max);
        return { kmd: Math.exp(lo + (hi - lo) * lvl / (P.lv.n - 1)), level: lvl, season: P.season, area: P.area,
            atMax: lvl === P.lv.n - 1, atMin: lvl === 0 };
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
            setSpeedImage(j);
            decodeSpeed(j);
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
            fires: g.fires, days: g.days, start: g.start, end: g.end, season: g.season, type: g.type };
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
    function tipHTML(p) {
        var ls = p.lead_start;
        var h = '<div style="font-weight:600;margin-bottom:3px">Vanguard fire chain</div>';
        h += '<div>Began <b>' + esc(ls) + ' days ahead</b> of the ' + (p.lead_basis === 'usual' ? 'usual' : 'season') +
             ' front' + (p.lead_basis === 'usual' ? ' <span style="opacity:.7">(this season\u2019s front had not arrived yet)</span>' : '') + '</div>';
        if (p.ahead_km) h += '<div>Ran <b>' + Number(p.ahead_km).toFixed(0) + ' km</b> over ' + esc(p.ahead_days) + ' d before the season caught up</div>';
        if (p.part === 'after') h += '<div style="color:#9ca3af">Here the season had caught up ' + esc(-p.lead) + ' d earlier \u2014 one fire among the field\u2019s</div>';
        else if (p.part === 'ahead' && p.lead != null && p.lead !== ls) h += '<div>Still <b>' + esc(p.lead) + ' d ahead</b> here</div>';
        h += '<div style="opacity:.8">' + evidenceWords(p.tier, p.bits) + '</div>';
        h += '<div style="opacity:.75;margin-top:3px">' + fmtDate(p.start) + ' \u2013 ' + fmtDate(p.end) + ' \u00b7 ' + esc(p.fires) + ' detections \u00b7 ' +
             (p.km ? Number(p.km).toFixed(0) + ' km' : '') + (p.season ? ' \u00b7 season ' + esc(p.season) : '') + '</div>';
        h += '<div style="opacity:.6;font-size:11px;margin-top:4px">An early signal that people are moving ahead of the season \u2014 not a proof of who or why.</div>';
        return h;
    }
    function registerTip() {
        if (!window.MapTip || !MapTip.register) return;
        [VAN_LYR, VAN_GAP_LYR, VAN_DIM_LYR].forEach(function (id) {
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
        moveTimer = setTimeout(function () { loadFront(false); loadVan(false); loadSpeed(false); }, 350);
    }
    function onDates() {
        if (st.van) { vanKey = ''; loadVan(true); }
        if (st.front) loadFront(false);   // the front follows the window
        if (st.speed) loadSpeed(false);   // so does the speed map (one season per answer)
    }
    function onFocus() { if (anyOn()) { frontKey = ''; speedKey = ''; loadFront(true); loadVan(true); loadSpeed(true); } }

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
        [VAN_LYR, VAN_GAP_LYR, VAN_DIM_LYR].forEach(function (id) {
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', (st.van && !animating) ? 'visible' : 'none');
        });
        if (t == null) {
            clearTimeout(animTrail);
            if (animMeta) { animMeta = null; emit(); }
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
        speedOn: function () { return st.speed; },
        speedMeta: function () { return speed; },
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
            if (st.speed) loadSpeed(true); else { setSpeedImage(null); speedPix = null; refreshStrip(); }
        },
        off: function () { this.setFront(false); this.setVanguard(false); this.setSpeed(false); },
        animAt: animAt,
        lift: lift,      // lodlayer.js calls it after adding a line layer
        // switchBasemap() rebuilds the style; put the layers back on idle.
        reattach: function () {
            if (!anyOn() || !map) return;
            map.once('idle', function () {
                ensureLayers();
                frontKey = ''; vanKey = ''; speedKey = '';
                loadFront(true); loadVan(true); loadSpeed(true);
            });
        },
        getShareParams: function () {
            if (!anyOn()) return null;
            return { season: [st.front ? 'front' : '', st.van ? 'vanguard' : '', st.speed ? 'speed' : ''].filter(Boolean).join(',') };
        },
        restoreFromParams: function (params) {
            var v = params.get('season');
            if (!v) return;
            var parts = v.split(',');
            if (parts.indexOf('front') >= 0) this.setFront(true);
            if (parts.indexOf('vanguard') >= 0) this.setVanguard(true);
            if (parts.indexOf('speed') >= 0) this.setSpeed(true);
        },
        // The words the strip and the fire tip share; one definition.
        LEAD_DAYS: 10, LEAD_MAX: 60,
        leadColor: leadColor, frontColor: frontColor, tierWord: tierWord, tierWide: tierWide, legendHTML: legendHTML,
        evidenceMul: evidenceMul, widthMulExpr: widthMulExpr, leadAlpha: leadAlpha, evidenceWords: evidenceWords
    };
    window.FireSeason = FireSeason;
})();
