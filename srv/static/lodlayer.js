/*
 * LODLayer — one loader for every viewport feature layer.
 *
 * WHY THIS EXISTS
 *
 * There were two loaders and they disagreed about what a layer *is*:
 *
 *   * the stats-panel toggles fetched a bbox with `limit=1500` and refetched
 *     on moveend, so zooming in gave you more of the truth;
 *   * a pinned layer fetched the whole park once with `limit=5000` and never
 *     refetched, so zooming in gave you nothing new — and a continental pin
 *     ("1,500 of 328,167 fires") stayed a picture forever.
 *
 * Both are now this: ask the server for the current view with `mode=auto` and
 * let it decide, from the TRUE count in view, whether the answer is clickable
 * geometry or bare centroids. The switch is therefore never tied to a zoom
 * number — two views at the same zoom differ by three orders of magnitude —
 * and it is never a silent loss of information: a centroid still carries its
 * row id, so hovering it fetches the same tip the geometry would have shown
 * (/api/feature-detail).
 *
 * The through-line of the whole change: THE SAME FEATURE IS THE SAME FEATURE
 * AT EVERY ZOOM. A cheap rendering may not quietly become a picture.
 *
 * A pin keeps its identity: `park` is sent for a park/AOI pin, so panning to a
 * neighbour does not adopt its rows. A stats-panel layer simply omits it.
 */
(function () {
    'use strict';

    var map = null;
    var reg = new Map();          // key -> layer state
    var detailCache = new Map();  // feature row id -> properties
    var detailPending = new Map();
    var REFETCH_MS = 350;
    var FADE_MS = 220;
    // The point budget is large on purpose: a centroid is ~26 bytes against
    // ~1.6 KB for its rings, so "as many as the view really holds" is
    // affordable in points mode and dishonest to refuse.
    var LIMIT_POINTS = 30000;
    var LIMIT_GEOM = 6000;

    function pwd() { return (typeof getPwd === 'function' ? getPwd() : '') || ''; }

    function viewBBox() {
        if (typeof currentBbox !== 'undefined' && currentBbox && currentBbox.length === 4) return currentBbox;
        if (!map || !map.getBounds) return null;
        var b = map.getBounds();
        return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    }

    // Does `inner` sit inside `outer`? Used to skip a refetch that cannot
    // reveal anything: zooming further into an answer that was not truncated
    // already contains every feature in the new view.
    function contains(outer, inner) {
        if (!outer || !inner) return false;
        return outer[0] <= inner[0] && outer[1] <= inner[1] &&
               outer[2] >= inner[2] && outer[3] >= inner[3];
    }

    function ids(key) {
        return {
            src: 'lod-' + key,
            fill: 'lod-' + key + '-fill',
            line: 'lod-' + key + '-line',
            point: 'lod-' + key + '-point',
            dots: 'lod-' + key + '-dots'
        };
    }

    function removeLayer(id) {
        if (window.MapTip) window.MapTip.unregister(id);
        try { if (map.getLayer(id)) map.removeLayer(id); } catch (e) {}
    }

    function destroy(key) {
        var s = reg.get(key);
        if (!s) return;
        if (s.abort) s.abort.abort();
        if (s.fadeTimer) clearTimeout(s.fadeTimer);
        var L = ids(key);
        [L.fill, L.line, L.point, L.dots].forEach(removeLayer);
        try { if (map.getSource(L.src)) map.removeSource(L.src); } catch (e) {}
        reg.delete(key);
    }

    // ---- rendering -------------------------------------------------------

    // Geometry and points are two renderings of ONE layer, so they share the
    // source id and cross-fade rather than being torn down and rebuilt:
    // crossing the threshold should read as focus, not as a reload.
    function ensureLayers(key, render, color) {
        var L = ids(key), s = reg.get(key);
        var want = render === 'points' ? [L.dots] : [L.fill, L.line, L.point];
        var unwant = render === 'points' ? [L.fill, L.line, L.point] : [L.dots];

        if (render === 'points') {
            if (!map.getLayer(L.dots)) {
                map.addLayer({
                    id: L.dots, type: 'circle', source: L.src,
                    paint: {
                        // Small and dense: this rendering exists because there
                        // are a lot of them, and fat dots at continental zoom
                        // are a smear, not a map.
                        'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 1.6, 6, 2.6, 10, 4],
                        'circle-color': color,
                        'circle-opacity': 0,
                        'circle-stroke-width': 0
                    }
                });
                registerTip(key, L.dots, true);
            }
        } else {
            if (!map.getLayer(L.fill)) {
                map.addLayer({
                    id: L.fill, type: 'fill', source: L.src,
                    filter: ['any', ['==', ['geometry-type'], 'Polygon'], ['==', ['geometry-type'], 'MultiPolygon']],
                    paint: { 'fill-color': color, 'fill-opacity': 0, 'fill-outline-color': color }
                });
                registerTip(key, L.fill, false);
            }
            if (!map.getLayer(L.line)) {
                map.addLayer({
                    id: L.line, type: 'line', source: L.src,
                    filter: ['any', ['==', ['geometry-type'], 'LineString'], ['==', ['geometry-type'], 'MultiLineString']],
                    paint: { 'line-color': color, 'line-width': 2, 'line-opacity': 0 }
                });
                registerTip(key, L.line, false);
            }
            if (!map.getLayer(L.point)) {
                map.addLayer({
                    id: L.point, type: 'circle', source: L.src,
                    filter: ['==', ['geometry-type'], 'Point'],
                    paint: {
                        'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 2, 8, 4, 12, 6],
                        'circle-color': color, 'circle-opacity': 0,
                        'circle-stroke-width': 1, 'circle-stroke-color': '#fff', 'circle-stroke-opacity': 0.5
                    }
                });
                registerTip(key, L.point, false);
            }
        }
        fade(key, want, unwant);
        s.render = render;
    }

    var OPACITY_PROP = {
        fill: ['fill-opacity', 0.25],
        line: ['line-opacity', 0.8],
        circle: ['circle-opacity', 0.7]
    };

    function setOpacity(id, on) {
        if (!map.getLayer(id)) return;
        var type = map.getLayer(id).type;
        var p = OPACITY_PROP[type];
        if (!p) return;
        // A dots layer is denser, so it carries less alpha each.
        var target = on ? (id.endsWith('-dots') ? 0.75 : p[1]) : 0;
        try {
            map.setPaintProperty(id, p[0], target);
            map.setPaintProperty(id, p[0] + '-transition', { duration: FADE_MS, delay: 0 });
        } catch (e) {}
    }

    function fade(key, on, off) {
        var s = reg.get(key);
        on.forEach(function (id) { setOpacity(id, true); });
        off.forEach(function (id) { setOpacity(id, false); });
        if (s.fadeTimer) clearTimeout(s.fadeTimer);
        // Remove the losing rendering only once it is invisible; removing it
        // immediately is the "reload" flicker this whole cross-fade avoids.
        s.fadeTimer = setTimeout(function () { off.forEach(removeLayer); }, FADE_MS + 60);
    }

    // ---- tips ------------------------------------------------------------

    // A dot's properties are just its row id, so the tip has to go and get the
    // rest — the same pattern the AOI coverage tip uses: render a placeholder
    // now, MapTip.refresh() when it lands. Never an empty tip.
    function registerTip(key, layerId, isPoint) {
        if (!window.MapTip) return;
        var s = reg.get(key);
        window.MapTip.register(layerId, {
            html: function (props) {
                props = props || {};
                if (!isPoint) return tipHTML(s, props);
                var id = props.rid;
                if (id == null) return tipHTML(s, props);
                if (detailCache.has(id)) return tipHTML(s, detailCache.get(id));
                fetchDetail(id, layerId);
                return '<div class="maptip-label">' + (s.tipType || 'Feature') + '</div>' +
                       '<div class="maptip-dim">loading details…</div>';
            },
            actionLabel: 'Open in report',
            onActivate: function (feature) {
                var p = (feature && feature.properties) || {};
                if (p.rid != null && detailCache.has(p.rid)) p = detailCache.get(p.rid);
                var park = p.park_id || s.park;
                if (park && typeof openFeatureInReport === 'function') {
                    openFeatureInReport(park, s.tipType, p.feature_id || null);
                }
            }
        });
    }

    function tipHTML(s, props) {
        if (typeof featureTipHTML === 'function') {
            return featureTipHTML(props, s.tipType, props.park_name || s.parkName || props.park_id || '');
        }
        return '<div class="maptip-title">' + (props.feature_id || 'Feature') + '</div>';
    }

    function fetchDetail(id, layerId) {
        if (detailPending.has(id)) return detailPending.get(id);
        var p = fetch('/api/feature-detail?id=' + encodeURIComponent(id) + '&pwd=' + encodeURIComponent(pwd()))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (f) {
                if (f && f.properties) {
                    // A couple of thousand entries is nothing, and a re-hover
                    // must not re-ask.
                    detailCache.set(id, f.properties);
                    if (window.MapTip) window.MapTip.refresh(layerId);
                }
            })
            .catch(function () {})
            .finally(function () { detailPending.delete(id); });
        detailPending.set(id, p);
        return p;
    }

    // ---- loading ---------------------------------------------------------

    function pointsToGeoJSON(d) {
        var pts = d.points || [], rids = d.ids || [];
        var feats = new Array(pts.length);
        for (var i = 0; i < pts.length; i++) {
            feats[i] = {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [pts[i][0], pts[i][1]] },
                properties: { rid: rids[i], value: pts[i][3] }
            };
        }
        return { type: 'FeatureCollection', features: feats };
    }

    async function load(key, why) {
        var s = reg.get(key);
        if (!s) return;
        var bbox = viewBBox();
        if (!bbox) return;

        // Skip a request that cannot reveal anything: the last answer covered
        // a bigger box, was not truncated, and was already real geometry. This
        // is what makes toggling and panning cheap — the old stats-panel path
        // refetched on every moveend regardless.
        if (why === 'move' && s.lastBBox && s.render === 'geometry' && !s.lastTruncated &&
            contains(s.lastBBox, bbox)) {
            return;
        }

        if (s.abort) s.abort.abort();
        var ctrl = new AbortController();
        s.abort = ctrl;
        if (s.onLoading) s.onLoading(true);

        var qs = new URLSearchParams({
            type: s.featureType,
            bbox: bbox.map(function (v) { return v.toFixed(4); }).join(','),
            mode: 'auto',
            limit: String(LIMIT_POINTS),
            geom_budget: String(LIMIT_GEOM),
            pwd: pwd()
        });
        if (s.dated !== false) {
            if (typeof dateFrom !== 'undefined' && dateFrom) qs.set('from', dateFrom);
            if (typeof dateTo !== 'undefined' && dateTo) qs.set('to', dateTo);
        }
        // A pin means "this area's fires". Without it, panning east would
        // quietly adopt the neighbouring park's rows under the same label.
        if (s.park) qs.set('park', s.park);
        var scopeAOI = s.aoi || (typeof aoiFocusID !== 'undefined' ? aoiFocusID : null);
        if (scopeAOI) qs.set('aoi', scopeAOI);

        try {
            var res = await fetch('/api/features-in-bbox?' + qs, { signal: ctrl.signal });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            var d = await res.json();
            if (!reg.has(key)) return;   // removed while loading
            var render = d.render || (d.mode === 'points' ? 'points' : 'geometry');
            var data = render === 'points' ? pointsToGeoJSON(d) : d;
            var L = ids(key);
            if (map.getSource(L.src)) map.getSource(L.src).setData(data);
            else map.addSource(L.src, { type: 'geojson', data: data });
            ensureLayers(key, render, s.color);
            s.lastBBox = bbox;
            s.lastTruncated = !!d.truncated;
            s.count = d.count || 0;
            s.total = d.total || 0;
            if (s.onCount) s.onCount(s.count, s.total, render, d.truncated);
        } catch (e) {
            if (e.name !== 'AbortError') console.error('LOD layer ' + key + ':', e);
        } finally {
            if (s.abort === ctrl) s.abort = null;
            if (s.onLoading) s.onLoading(false);
        }
    }

    // ---- public ----------------------------------------------------------

    var moveTimer = null;
    var reloadTimer = null;
    function onMoveEnd() {
        if (moveTimer) clearTimeout(moveTimer);
        moveTimer = setTimeout(function () {
            reg.forEach(function (s, key) { load(key, 'move'); });
        }, REFETCH_MS);
    }

    var LODLayer = {
        /** Types this loader can serve; everything else keeps its own path. */
        TYPES: ['fire_trajectory', 'deforestation', 'settlement'],
        supports: function (t) { return LODLayer.TYPES.indexOf(t) >= 0; },

        init: function (m) {
            if (map) return;
            map = m;
            map.on('moveend', onMoveEnd);
        },

        /**
         * add(key, {featureType, color, tipType, park, parkName, aoi, dated,
         *           onCount, onLoading})
         * Idempotent: adding an existing key updates its options and reloads.
         */
        add: function (key, opts) {
            if (!map) return Promise.resolve();
            var s = reg.get(key) || {};
            Object.keys(opts || {}).forEach(function (k) { s[k] = opts[k]; });
            s.lastBBox = null;
            reg.set(key, s);
            return load(key, 'add');
        },

        remove: function (key) { destroy(key); },
        removeAll: function () { Array.from(reg.keys()).forEach(destroy); },
        has: function (key) { return reg.has(key); },
        state: function (key) { return reg.get(key) || null; },
        /** Date window or focus changed: every layer's answer is now stale. */
        reload: function () {
            // Debounced: "the dates changed" is announced by several call
            // sites (the slider, the presets, the pinned-layer refresh), and
            // each of them would otherwise abort the previous request mid
            // flight for no gain.
            if (reloadTimer) clearTimeout(reloadTimer);
            reloadTimer = setTimeout(function () {
                reg.forEach(function (s, key) { s.lastBBox = null; load(key, 'reload'); });
            }, 80);
        },
        layerIds: function (key) { var L = ids(key); return [L.fill, L.line, L.point, L.dots]; }
    };

    window.LODLayer = LODLayer;
})();
