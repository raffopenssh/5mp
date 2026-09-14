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
 *   VANGUARD  the fire chains that BEGAN 10–60 days ahead of that front —
 *             the one population where the tracker's day-to-day links are
 *             measurably better than chance (link skill ≈0.5; in season ≈0).
 *             Each chain is drawn bright while it is still ahead of the
 *             front and faint once the season has caught up with it.
 *
 * Lives in the stats-panel Map strip like Geology and Historical maps: a
 * chip is the state, its body configures, its × switches off
 * (srv/static/maplegend.js). Share-link: `season=front,vanguard`. Which
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
        FRONT_LBL = 'fireseason-front-label',
        VAN_SRC = 'fireseason-van-src', VAN_LYR = 'fireseason-van',
        VAN_DIM_LYR = 'fireseason-van-dim';

    var map = null;
    var st = { front: false, van: false };   // the season shown follows the time slider
    var front = null;      // last /api/fire-season answer
    var van = null;        // last /api/fire-vanguard answer
    var frontKey = '', vanKey = '';
    var moveTimer = null, inflight = 0;
    var listeners = [];

    function pwd() { return encodeURIComponent((typeof getPwd === 'function' ? getPwd() : '') || ''); }
    function focusId() { return (typeof aoiFocusID !== 'undefined' && aoiFocusID) ? aoiFocusID : ''; }
    function dates() {
        var f = (typeof dateFrom !== 'undefined' && dateFrom) ? dateFrom : '';
        var t = (typeof dateTo !== 'undefined' && dateTo) ? dateTo : '';
        return { from: f, to: t };
    }
    function anyOn() { return st.front || st.van; }
    function emit() { listeners.forEach(function (fn) { try { fn(); } catch (e) { /* listener's problem */ } }); }
    function refreshStrip() {
        if (window.MapLegend && MapLegend.refresh) MapLegend.refresh();
        emit();
    }

    /* ── colour ───────────────────────────────────────────────────────────
     * Front: cool ramp, early (pale) → late (deep blue-violet). Deliberately
     * not a warm colour: the fire lines are red and orange, and a contour in
     * the same family would read as another fire. Vanguard: yellow → cyan by
     * how far ahead the chain began; both ends are far from the fire red. */
    function lerp(a, b, t) { return a + (b - a) * t; }
    function hex(r, g, b) {
        return '#' + [r, g, b].map(function (v) { v = Math.max(0, Math.min(255, Math.round(v))); return (v < 16 ? '0' : '') + v.toString(16); }).join('');
    }
    function frontColor(t) {           // t 0..1 early→late
        // #e0f2fe → #38bdf8 → #6d28d9
        if (t < 0.5) { var u = t / 0.5; return hex(lerp(224, 56, u), lerp(242, 189, u), lerp(254, 248, u)); }
        var v = (t - 0.5) / 0.5; return hex(lerp(56, 109, v), lerp(189, 40, v), lerp(248, 217, v));
    }
    function leadColor(lead) {         // 10 d → yellow, 60 d → cyan
        var t = Math.max(0, Math.min(1, (lead - 10) / 50));
        return hex(lerp(253, 34, t), lerp(224, 211, t), lerp(71, 238, t));
    }

    /* ── layers ─────────────────────────────────────────────────────────── */
    function ensureLayers() {
        if (!map || !map.getStyle()) return;
        if (!map.getSource(FRONT_SRC)) map.addSource(FRONT_SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        if (!map.getSource(VAN_SRC)) map.addSource(VAN_SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        if (!map.getLayer(FRONT_LYR)) {
            map.addLayer({
                id: FRONT_LYR, type: 'line', source: FRONT_SRC,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': ['get', 'color'],
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
                paint: { 'line-color': ['get', 'color'], 'line-width': 1.3, 'line-opacity': 0.3 }
            });
        }
        if (!map.getLayer(VAN_LYR)) {
            map.addLayer({
                id: VAN_LYR, type: 'line', source: VAN_SRC,
                filter: ['==', ['get', 'part'], 'ahead'],
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': ['get', 'color'],
                    'line-width': ['interpolate', ['linear'], ['zoom'], 5, 1.6, 9, 2.6, 12, 3.4],
                    'line-opacity': 0.95
                }
            });
            registerTip();
        }
        applyVisibility();
    }
    function applyVisibility() {
        if (!map) return;
        [FRONT_LYR, FRONT_LBL].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', st.front ? 'visible' : 'none'); });
        [VAN_LYR, VAN_DIM_LYR].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', st.van ? 'visible' : 'none'); });
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
        if (dates().to) u += '&at=' + dates().to;   // the season the window ends in
        return u;
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
        inflight++;
        return fetch(frontURL()).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
            inflight--;
            frontKey = key;
            front = j || { status: 'request failed', seasons: [] };
            var feats = [];
            if (j && j.contours && j.contours.length) {
                var ds = j.contours.map(function (f) { return f.properties.dos; });
                var lo = Math.min.apply(null, ds), hi = Math.max.apply(null, ds);
                var bb = [180, 90, -180, -90];
                feats = j.contours.map(function (f) {
                    var t = hi > lo ? (f.properties.dos - lo) / (hi - lo) : 0.5;
                    f.properties.color = frontColor(t);
                    f.properties.label = !!f.properties.label;
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
        }).catch(function () { inflight--; });
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
    // Split one chain where the season catches up: vertices with lead >= 0
    // are "ahead", the rest "after". The vertex at the seam belongs to both
    // parts so the line has no gap. MapLibre cannot colour one LineString
    // per vertex, hence two features.
    function splitChain(g) {
        var pts = g.pts || [], leads = g.leads || [];
        var out = [], cur = [], curPart = null;
        var color = leadColor(g.lead_start == null ? 10 : g.lead_start);
        function flush() {
            if (cur.length >= 2) {
                out.push({ type: 'Feature', properties: { part: curPart, color: color, id: g.id, park: g.park, lead_start: g.lead_start,
                    lead_basis: g.lead_basis, ahead_km: g.ahead_km, ahead_days: g.ahead_days, km: g.km, kmd: g.kmd, fires: g.fires, days: g.days,
                    start: g.start, end: g.end, season: g.season, type: g.type },
                    geometry: { type: 'LineString', coordinates: cur.slice() } });
            }
        }
        for (var i = 0; i < pts.length; i++) {
            var L = leads[i];
            var part = (L == null || L >= 0) ? 'ahead' : 'after';
            if (curPart !== null && part !== curPart) { flush(); cur = [cur[cur.length - 1]]; }
            curPart = part;
            cur.push([pts[i][0], pts[i][1]]);
        }
        flush();
        if (pts.length === 1) {
            // A one-vertex chain still deserves a mark: a very short line.
            var p = pts[0], e = 0.004;
            out.push({ type: 'Feature', properties: { part: 'ahead', color: color, id: g.id, park: g.park, lead_start: g.lead_start, lead_basis: g.lead_basis,
                ahead_km: g.ahead_km, ahead_days: g.ahead_days, km: g.km, fires: g.fires, days: g.days, start: g.start, end: g.end, season: g.season },
                geometry: { type: 'LineString', coordinates: [[p[0] - e, p[1]], [p[0] + e, p[1]]] } });
        }
        return out;
    }
    function loadVan(force) {
        if (!st.van || !map) return Promise.resolve();
        var key = vanURL();
        if (!force && key === vanKey) return Promise.resolve();
        inflight++;
        return fetch(key).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
            inflight--;
            vanKey = key;
            van = j;
            var feats = [];
            ((j && j.groups) || []).forEach(function (g) { feats = feats.concat(splitChain(g)); });
            setData(VAN_SRC, feats);
            refreshStrip();
        }).catch(function () { inflight--; });
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
        h += '<div style="opacity:.75;margin-top:3px">' + fmtDate(p.start) + ' \u2013 ' + fmtDate(p.end) + ' \u00b7 ' + esc(p.fires) + ' detections \u00b7 ' +
             (p.km ? Number(p.km).toFixed(0) + ' km' : '') + (p.season ? ' \u00b7 season ' + esc(p.season) : '') + '</div>';
        h += '<div style="opacity:.6;font-size:11px;margin-top:4px">An early signal that people are moving ahead of the season \u2014 not a proof of who or why.</div>';
        return h;
    }
    function registerTip() {
        if (!window.MapTip || !MapTip.register) return;
        [VAN_LYR, VAN_DIM_LYR].forEach(function (id) {
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
        moveTimer = setTimeout(function () { loadFront(false); loadVan(false); }, 350);
    }
    function onDates() {
        if (st.van) { vanKey = ''; loadVan(true); }
        if (st.front) loadFront(false);   // the front follows the window
    }
    function onFocus() { if (anyOn()) { frontKey = ''; loadFront(true); loadVan(true); } }

    /* ── animator ───────────────────────────────────────────────────────
     * The animator hands us its playhead (ms). Contours the season had not
     * reached by then are hidden; the one it reached most recently is drawn
     * heavier, so the reader sees the front MOVE as the fires build up under
     * it. Updated only when the day changes: setFilter on a 30-feature source
     * is cheap, but not 60 times a second. null = whole season again. */
    var animDay = null;
    function animAt(t) {
        if (!map || !map.getLayer(FRONT_LYR)) return;
        if (t == null) {
            if (animDay === null) return;
            animDay = null;
            map.setFilter(FRONT_LYR, null);
            map.setFilter(FRONT_LBL, ['==', ['get', 'label'], true]);
            map.setPaintProperty(FRONT_LYR, 'line-width', ['case', ['get', 'label'], 1.6, 0.7]);
            map.setPaintProperty(FRONT_LYR, 'line-opacity', ['case', ['get', 'label'], 0.9, 0.55]);
            return;
        }
        var iso = new Date(t).toISOString().slice(0, 10);
        if (iso === animDay) return;
        animDay = iso;
        map.setFilter(FRONT_LYR, ['<=', ['get', 'date'], iso]);
        map.setFilter(FRONT_LBL, ['all', ['==', ['get', 'label'], true], ['<=', ['get', 'date'], iso]]);
        // "Recent" = within one contour step (5 d) of the playhead.
        var recent = new Date(t - 5 * 86400000).toISOString().slice(0, 10);
        map.setPaintProperty(FRONT_LYR, 'line-width', ['case', ['>=', ['get', 'date'], recent], 2.6, ['get', 'label'], 1.4, 0.6]);
        map.setPaintProperty(FRONT_LYR, 'line-opacity', ['case', ['>=', ['get', 'date'], recent], 1.0, ['get', 'label'], 0.8, 0.45]);
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
        meta: function () { return front; },
        vanguard: function () { return van; },
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
        off: function () { this.setFront(false); this.setVanguard(false); },
        animAt: animAt,
        // switchBasemap() rebuilds the style; put the layers back on idle.
        reattach: function () {
            if (!anyOn() || !map) return;
            map.once('idle', function () {
                ensureLayers();
                frontKey = ''; vanKey = '';
                loadFront(true); loadVan(true);
            });
        },
        getShareParams: function () {
            if (!anyOn()) return null;
            return { season: [st.front ? 'front' : '', st.van ? 'vanguard' : ''].filter(Boolean).join(',') };
        },
        restoreFromParams: function (params) {
            var v = params.get('season');
            if (!v) return;
            var parts = v.split(',');
            if (parts.indexOf('front') >= 0) this.setFront(true);
            if (parts.indexOf('vanguard') >= 0) this.setVanguard(true);
        },
        // The words the strip and the fire tip share; one definition.
        LEAD_DAYS: 10, LEAD_MAX: 60,
        leadColor: leadColor, frontColor: frontColor
    };
    window.FireSeason = FireSeason;
})();
