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
    var REFETCH_MS = 260;
    var FADE_MS = 220;
    var FETCH_PAD = 0.25;   // fetch 25% beyond the view, so a small pan is free
    // The point budget is large on purpose: a centroid is ~26 bytes against
    // ~1.6 KB for its rings, so "as many as the view really holds" is
    // affordable in points mode and dishonest to refuse.
    var LIMIT_POINTS = 30000;
    // How many real vector features one view may carry. The server now ships
    // SLIM properties above ~1200 features (srv/features_bbox.go), so a
    // trajectory costs its coordinates and a row id rather than its coordinates
    // plus 750 bytes of narrative: 14,350 fire paths in an 8° view are 2.6 MB
    // and 0.25 s. That is why this is 12,000 rather than the 6,000 the
    // properties, not the shapes, used to force.
    var LIMIT_GEOM = 12000;

    // THE CHEAP TIER FOR A PATH IS A SHORTER PATH, NOT A DOT.
    //
    // Collapsing a fire trajectory to its centroid destroys the one property
    // that distinguishes a fire FRONT from a hotspot: the direction and
    // distance it ran. A whole AOI of them was a red stipple that said less
    // than the map it replaced. `seg=1` asks the server for a three-point
    // chord per feature instead (first / middle / last vertex): ~50 bytes
    // against ~350 for the full path, so an area with 38,725 trajectories
    // still shows every one of them as a line — 3.4 MB, 1.0 s — instead of
    // showing all of them as dots or 12,000 of them as paths.
    //
    // Polygons keep dots: a settlement's centroid IS the settlement, at the
    // zoom where this tier applies.
    var SEG_TYPES = { fire_trajectory: true };

    // Per-layer detail preference, cycled from the layer's own readout:
    //   'auto'   — the server decides from the true count in view (default)
    //   'shapes' — always full geometry, whatever it costs
    //   'fast'   — always the cheap tier, even when shapes would fit
    // A preference is a statement about ONE layer and is remembered across
    // pans and reloads; it is deliberately not global, because the whole
    // point is that fires and settlements in one view can want different
    // answers.
    var DETAIL_MODES = ['auto', 'shapes', 'fast'];

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

    function area(b) { return Math.abs((b[2] - b[0]) * (b[3] - b[1])); }

    function padBBox(b, f) {
        var w = (b[2] - b[0]) * f, h = (b[3] - b[1]) * f;
        return [b[0] - w, b[1] - h, b[2] + w, b[3] + h];
    }

    function ids(key) {
        return {
            src: 'lod-' + key,
            fill: 'lod-' + key + '-fill',
            line: 'lod-' + key + '-line',
            arrow: 'lod-' + key + '-arrows',
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
        [L.fill, L.line, L.arrow, L.point, L.dots].forEach(removeLayer);
        try { if (map.getSource(L.src)) map.removeSource(L.src); } catch (e) {}
        reg.delete(key);
    }

    // ---- rendering -------------------------------------------------------

    // Three renderings of ONE layer — full geometry, chords, dots — sharing a
    // source id and cross-fading rather than being torn down and rebuilt:
    // crossing a threshold should read as focus, not as a reload.
    //
    // `segments` deliberately reuses the LINE layer, so a chord is painted,
    // hovered and arrowed by exactly the code that paints the full path. It
    // is the same feature drawn shorter, and it must not become a special
    // case anywhere downstream.
    function ensureLayers(key, render, color, count) {
        var L = ids(key), s = reg.get(key);
        // Density first: addLayer below reads s.lineWidth / s.pointRadius, so
        // a layer is born at the right weight instead of flashing at the
        // default and being corrected a frame later.
        applyDensity(key, count || 0);
        var vector = render !== 'points';
        var wantArrow = vector && s.featureType === 'fire_trajectory';
        var want = vector ? [L.fill, L.line, L.point] : [L.dots];
        var unwant = vector ? [L.dots] : [L.fill, L.line, L.arrow, L.point];
        if (wantArrow) want.push(L.arrow);

        if (!vector) {
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
                    paint: { 'line-color': color, 'line-width': s.lineWidth || 2, 'line-opacity': 0 }
                });
                registerTip(key, L.line, false);
            }
            // Directional arrows: a fire trajectory is a MOVEMENT, and a line
            // without them says where it burned but not which way it went.
            // The legacy pin path drew these and the first LOD version lost
            // them, which read as the vectors being a lesser rendering.
            // Registered as no-tip: the line underneath answers the hover.
            if (wantArrow && !map.getLayer(L.arrow) && map.hasImage && map.hasImage('arrow-right')) {
                map.addLayer({
                    id: L.arrow, type: 'symbol', source: L.src,
                    filter: ['==', ['geometry-type'], 'LineString'],
                    layout: {
                        'symbol-placement': 'line', 'symbol-spacing': 100,
                        'icon-image': 'arrow-right', 'icon-size': 0.5, 'icon-rotate': 90,
                        'icon-rotation-alignment': 'map',
                        'icon-allow-overlap': true, 'icon-ignore-placement': true
                    },
                    paint: { 'icon-color': color, 'icon-opacity': 0 }
                });
            }
            if (!map.getLayer(L.point)) {
                map.addLayer({
                    id: L.point, type: 'circle', source: L.src,
                    filter: ['==', ['geometry-type'], 'Point'],
                    paint: {
                        'circle-radius': s.pointRadius || 4,
                        'circle-color': color, 'circle-opacity': 0,
                        'circle-stroke-width': 1, 'circle-stroke-color': '#fff',
                        'circle-stroke-opacity': s.pointRing == null ? 0.5 : s.pointRing
                    }
                });
                registerTip(key, L.point, false);
            }
        }
        var changed = s.render && s.render !== render;
        fade(key, want, unwant);
        if (changed) focusPulse(key, render);
        s.render = render;
    }

    // ---- the transition, made visible ------------------------------------
    //
    // Crossing the threshold is the one moment the map changes WHAT IT IS
    // showing rather than how much of it — dots become shapes you can click,
    // or shapes collapse into dots that still answer a hover. Without a signal
    // that reads as focus, a user zooming out sees their trajectories "turn
    // into dots" and reasonably concludes the app threw the detail away.
    //
    // So the incoming rendering overshoots and settles: lines come in thick
    // and thin down, dots come in large and shrink. It is ~380 ms of paint
    // transition on layers that are being added anyway — no extra geometry, no
    // extra request — and it is deliberately the same gesture in both
    // directions, because the claim is that nothing was lost either way.
    function focusPulse(key, render) {
        var L = ids(key), s = reg.get(key) || {};
        var steps = render === 'points'
            ? [[L.dots, 'circle-radius', ['interpolate', ['linear'], ['zoom'], 2, 4.5, 6, 6, 10, 8],
                                          ['interpolate', ['linear'], ['zoom'], 2, 1.6, 6, 2.6, 10, 4]]]
            // Settle back to the DENSITY width, not to a constant: a
            // hard-coded 2 here silently undid applyDensity and put the red
            // sheet back every time the layer crossed the threshold.
            : [[L.line, 'line-width', (s.lineWidth || 2) * 3, s.lineWidth || 2],
               [L.point, 'circle-radius', (s.pointRadius || 4) * 2.2, s.pointRadius || 4]];        steps.forEach(function (st) {
            if (!map.getLayer(st[0])) return;
            try {
                map.setPaintProperty(st[0], st[1] + '-transition', { duration: 0, delay: 0 });
                map.setPaintProperty(st[0], st[1], st[2]);
                setTimeout(function () {
                    if (!map.getLayer(st[0])) return;
                    map.setPaintProperty(st[0], st[1] + '-transition', { duration: 380, delay: 0 });
                    map.setPaintProperty(st[0], st[1], st[3]);
                }, 60);
            } catch (e) {}
        });
    }

    var OPACITY_PROP = {
        fill: ['fill-opacity', 0.25],
        line: ['line-opacity', 0.8],
        circle: ['circle-opacity', 0.7],
        symbol: ['icon-opacity', 0.7]
    };

    // DENSITY-AWARE PAINT.
    //
    // "As much detail as possible" is not the same as "as much ink as
    // possible". 1,856 fire trajectories in one view at line-width 2 /
    // opacity 0.8 is a solid red sheet: every path is there, none of them is
    // legible, and the picture says less than the dots it replaced. Thin,
    // translucent strokes at that density let the overlaps accumulate into
    // structure — corridors and fronts become visible — while a handful of
    // features stays bold and obviously clickable.
    //
    // Keyed on the COUNT ACTUALLY DRAWN, not on zoom, for the same reason the
    // geometry/points switch is: two views at one zoom differ by orders of
    // magnitude. Arrows go with it — a directional glyph every 100 px across
    // 2,000 overlapping paths is noise, so they fade out as the lines thin.
    function densityPaint(n) {
        // `r`/`ring` are the POINT rendering: a white ring is a "click me"
        // affordance and is right for a handful of features; across a field of
        // thousands it is the loudest thing on screen, so a hundred stationary
        // fires out-shouted 14,700 moving ones (the white dots that survived
        // the switch to line segments). Above a few hundred features a point
        // wears the same ink as the lines: no ring, line alpha, line-ish size.
        if (n <= 150) return { w: 2.4, o: 0.9, arrow: 0.75, r: 5, ring: 0.5, fill: 0.3 };
        if (n <= 600) return { w: 1.8, o: 0.7, arrow: 0.5, r: 4, ring: 0.35, fill: 0.26 };
        if (n <= 2000) return { w: 1.3, o: 0.5, arrow: 0.18, r: 2.6, ring: 0, fill: 0.22 };
        if (n <= 6000) return { w: 1.0, o: 0.36, arrow: 0, r: 1.8, ring: 0, fill: 0.18 };
        if (n <= 12000) return { w: 0.85, o: 0.26, arrow: 0, r: 1.4, ring: 0, fill: 0.14 };
        // Tens of thousands of chords in one view. Thin and translucent is
        // deliberate: what is legible at this density is where the ink
        // ACCUMULATES — corridors, repeatedly-burnt ground — and any stroke
        // heavy enough to read on its own turns the whole area into one flat
        // red field, which is the failure this ramp exists to avoid. But the
        // floor must still be visible on a dark basemap: 0.06 read as an empty
        // map with a smudge on it.
        if (n <= 25000) return { w: 0.75, o: 0.2, arrow: 0, r: 1.2, ring: 0, fill: 0.12 };
        return { w: 0.7, o: 0.16, arrow: 0, r: 1.1, ring: 0, fill: 0.1 };
    }

    function applyDensity(key, n) {
        var L = ids(key), d = densityPaint(n);
        var s0 = reg.get(key);
        if (s0) {
            s0.lineOpacity = d.o; s0.arrowOpacity = d.arrow; s0.fillOpacity = d.fill;
            s0.lineWidth = d.w; s0.pointRadius = d.r;
            // A point in a dense field is one dot of the same ink; on its own
            // it is a feature you are meant to click. Same rule as the lines,
            // one step brighter so a lone stationary fire in a field of moving
            // ones is still findable.
            s0.pointOpacity = d.ring > 0 ? 0.7 : Math.min(1, d.o * 1.6);
            s0.pointRing = d.ring;
        }
        var set = function (id, prop, val) {
            if (!map.getLayer(id)) return;
            try {
                map.setPaintProperty(id, prop + '-transition', { duration: FADE_MS, delay: 0 });
                map.setPaintProperty(id, prop, val);
            } catch (e) {}
        };
        set(L.line, 'line-width', d.w);
        set(L.point, 'circle-radius', d.r);
        set(L.point, 'circle-stroke-opacity', d.ring);
        // Opacity is only pushed for layers already visible; fade() owns the
        // 0 -> target step for one being brought in, and reads the same
        // remembered values (s.lineOpacity etc.) so the two never disagree.
        if (map.getLayer(L.line) && map.getPaintProperty(L.line, 'line-opacity')) set(L.line, 'line-opacity', d.o);
        if (map.getLayer(L.fill) && map.getPaintProperty(L.fill, 'fill-opacity')) set(L.fill, 'fill-opacity', d.fill);
        if (map.getLayer(L.arrow) && map.getPaintProperty(L.arrow, 'icon-opacity')) set(L.arrow, 'icon-opacity', d.arrow);
        if (map.getLayer(L.point) && map.getPaintProperty(L.point, 'circle-opacity')) {
            set(L.point, 'circle-opacity', (reg.get(key) || {}).pointOpacity || 0.7);
        }
    }

    function setOpacity(key, id, on) {
        if (!map.getLayer(id)) return;
        var type = map.getLayer(id).type;
        var p = OPACITY_PROP[type];
        if (!p) return;
        var s = reg.get(key) || {};
        var full = p[1];
        if (id === ids(key).line && s.lineOpacity != null) full = s.lineOpacity;
        else if (id === ids(key).arrow && s.arrowOpacity != null) full = s.arrowOpacity;
        else if (id === ids(key).fill && s.fillOpacity != null) full = s.fillOpacity;
        else if (id === ids(key).point && s.pointOpacity != null) full = s.pointOpacity;
        // A dots layer is denser, so it carries less alpha each.
        var target = on ? (id.endsWith('-dots') ? 0.75 : full) : 0;
        try {
            map.setPaintProperty(id, p[0], target);
            map.setPaintProperty(id, p[0] + '-transition', { duration: FADE_MS, delay: 0 });
        } catch (e) {}
    }

    function fade(key, on, off) {
        var s = reg.get(key);
        on.forEach(function (id) { setOpacity(key, id, true); });
        off.forEach(function (id) { setOpacity(key, id, false); });
        if (s.fadeTimer) clearTimeout(s.fadeTimer);
        // Remove the losing rendering only once it is invisible; removing it
        // immediately is the "reload" flicker this whole cross-fade avoids.
        s.fadeTimer = setTimeout(function () { off.forEach(removeLayer); }, FADE_MS + 60);
    }

    // ---- tips ------------------------------------------------------------

    // A dot's properties are just its row id, and above geoSlimAbove features
    // so are a VECTOR's — the server ships the shapes and keeps the narrative
    // for whoever actually reads one (srv/features_bbox.go). Either way the tip
    // goes and gets the rest: render a placeholder now, MapTip.refresh() when
    // it lands. Never an empty tip, and never a feature that is only a picture.
    function registerTip(key, layerId, isPoint) {
        if (!window.MapTip) return;
        var s = reg.get(key);
        window.MapTip.register(layerId, {
            // Names the tab when several answers share a point (see maptip.js).
            // The layer's own type, so "Fire" and "Settlement" under one click
            // are told apart by what they are, not by which is on top.
            tabLabel: (s.tipType || 'Feature').replace(/^./, function (c) { return c.toUpperCase(); }),
            html: function (props) {
                props = props || {};
                var id = props.rid;
                // Full properties already present (a small view): answer now.
                if (id == null || props.narrative || props.group_type || props.classification) {
                    return tipHTML(s, props);
                }
                if (detailCache.has(id)) return tipHTML(s, detailCache.get(id));
                fetchDetail(id, layerId);
                return '<div class="maptip-label">' + (s.tipType || 'Feature') + '</div>' +
                       '<div class="maptip-dim">loading details…</div>';
            },
            // The destination is the AREA's overview popup, and which kind of
            // area it is is only known per feature (a pinned AOI layer serves
            // AOI rows), so the label is a function of the feature.
            actionLabel: function (feature) {
                var p = (feature && feature.properties) || {};
                var area = p.park_id || s.park;
                return (typeof areaOverviewLabel === 'function')
                    ? areaOverviewLabel(area) : 'Open overview';
            },
            onActivate: function (feature) {
                var p = (feature && feature.properties) || {};
                if (p.rid != null && detailCache.has(p.rid)) p = detailCache.get(p.rid);
                var area = p.park_id || s.park;
                if (area && typeof openAreaOverview === 'function') {
                    // The list is keyed by the CLUSTER / EVENT, not the polygon
                    // under the cursor — see overviewFeatureIDs().
                    var fid = (typeof overviewFeatureIDs === 'function')
                        ? overviewFeatureIDs(s.tipType, p) : (p.feature_id || null);
                    openAreaOverview(area, s.tipType, fid);
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
        var pts = d.points || [], rids = d.ids || [], segs = d.segs || null;
        var feats = new Array(pts.length);
        for (var i = 0; i < pts.length; i++) {
            var g;
            if (segs && segs[i]) {
                var c = segs[i];
                // A degenerate chord (a Point trajectory, or a group that never
                // moved) is left as a Point rather than a zero-length line:
                // MapLibre draws nothing for the latter, so a stationary fire
                // would vanish exactly in the rendering meant to show more.
                if (c[0] === c[4] && c[1] === c[5] && c[0] === c[2] && c[1] === c[3]) {
                    g = { type: 'Point', coordinates: [c[0], c[1]] };
                } else {
                    g = { type: 'LineString', coordinates: [[c[0], c[1]], [c[2], c[3]], [c[4], c[5]]] };
                }
            } else {
                g = { type: 'Point', coordinates: [pts[i][0], pts[i][1]] };
            }
            feats[i] = {
                type: 'Feature',
                geometry: g,
                properties: { rid: rids[i], value: pts[i][3] }
            };
        }
        return { type: 'FeatureCollection', features: feats };
    }

    async function load(key, why) {
        var s = reg.get(key);
        if (!s) return;
        var view = viewBBox();
        if (!view) return;
        // Fetch a padded box, compare against the unpadded view. A small pan is
        // then answered from data already in the browser instead of a round
        // trip — the same trick the animator uses for its fetch bbox, and the
        // reason panning feels instant while zooming still re-asks.
        var bbox = padBBox(view, FETCH_PAD);

        // Skip a request that cannot reveal anything: the last answer covered
        // a bigger box, was not truncated, and was already real geometry. This
        // is what makes toggling and panning cheap — the old stats-panel path
        // refetched on every moveend regardless.
        if (why === 'move' && s.lastBBox && s.render === 'geometry' && !s.lastTruncated &&
            contains(s.lastBBox, view)) {
            return;
        }
        // In the cheap tiers the same containment is not enough on its own —
        // zooming IN is exactly what should promote chords or dots to full
        // geometry — but a pan or a zoom OUT inside a covered box cannot.
        // Compare the area we would now ask for: only a meaningfully smaller
        // one can change the answer.
        if (why === 'move' && s.lastBBox && s.render !== 'geometry' &&
            contains(s.lastBBox, view) && area(view) > area(s.lastBBox) * 0.55) {
            return;
        }

        if (s.abort) s.abort.abort();
        var ctrl = new AbortController();
        s.abort = ctrl;
        if (s.onLoading) s.onLoading(true);

        // The detail preference is expressed as a BUDGET, so the server keeps
        // making the decision from the true count in view and the client only
        // says how much it is willing to draw. 'shapes' therefore still
        // truncates honestly at a very large view rather than promising
        // something the browser cannot parse.
        var budget = LIMIT_GEOM;
        if (s.detail === 'shapes') budget = 200000;
        else if (s.detail === 'fast') budget = 0;
        var qs = new URLSearchParams({
            type: s.featureType,
            bbox: bbox.map(function (v) { return v.toFixed(4); }).join(','),
            mode: 'auto',
            limit: String(LIMIT_POINTS),
            geom_budget: String(budget),
            pwd: pwd()
        });
        // A path's cheap tier is a chord, not a centroid — see SEG_TYPES.
        if (SEG_TYPES[s.featureType]) qs.set('seg', '1');
        if (s.dated !== false) {
            if (typeof dateFrom !== 'undefined' && dateFrom) qs.set('from', dateFrom);
            if (typeof dateTo !== 'undefined' && dateTo) qs.set('to', dateTo);
        }
        // A pin means "this area's fires". Without it, panning east would
        // quietly adopt the neighbouring park's rows under the same label.
        //
        // `area`, not `park`: an AOI id in ?park= is a hard 404 from
        // ParkIDMiddleware, which is exactly how the first AOI fire pin came to
        // fetch nothing and report "0 in view".
        if (s.park) qs.set('area', s.park);
        // The popup's classification filter, applied server-side. It used to be
        // a client-side filter over feature properties, which is why a filtered
        // pin could not use this loader at all: neither the chord nor the slim
        // geometry rendering ships the property it filtered on.
        if (s.classification) qs.set('class', s.classification);
        // The focus scope, so the layer draws what the panel counts. An AOI
        // needs `aoi=` (it is excluded by default, for privacy and to stop it
        // double-counting the parks it overlaps); a park needs `park_focus=`,
        // NOT `park=`, which ParkIDMiddleware 404s for an AOI id — one
        // parameter name per kind, chosen by focusScopeParam in globe.html so
        // this cannot drift from what the stats call sends.
        var scopeAOI = s.aoi || (typeof aoiFocusID !== 'undefined' ? aoiFocusID : null);
        if (scopeAOI) {
            var isAoiScope = typeof focusIsAOI === 'function'
                ? focusIsAOI(scopeAOI)
                : true;   // pre-park-focus behaviour, if globe.html is older
            qs.set(isAoiScope ? 'aoi' : 'park_focus', scopeAOI);
        }

        try {
            var res = await fetch('/api/features-in-bbox?' + qs, { signal: ctrl.signal });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            var d = await res.json();
            if (!reg.has(key)) return;   // removed while loading
            var render = d.render || (d.mode === 'points' ? 'points' : 'geometry');
            var data = (render === 'points' || render === 'segments') ? pointsToGeoJSON(d) : d;
            var L = ids(key);
            if (map.getSource(L.src)) map.getSource(L.src).setData(data);
            else map.addSource(L.src, { type: 'geojson', data: data });
            ensureLayers(key, render, s.color, d.count || 0);
            s.lastBBox = bbox;
            s.lastTruncated = !!d.truncated;
            s.count = d.count || 0;
            s.total = d.total || 0;
            // WHAT THE COUNT IS A COUNT OF. A settlement is a cluster of
            // built-up footprints, and feature_geometries holds the
            // footprints: Chinko is 35 polygons and 27 settlements. The
            // readout used to print the polygon count beside the word
            // "settlements", disagreeing by a third with the panel and the
            // popup, both of which count clusters. The server now names its
            // unit and ships both numbers; carrying them here is what lets the
            // row say "27 settlements (35 footprints)" instead of picking one
            // and hoping.
            s.unit = d.unit || 'features';
            s.groups = (typeof d.groups === 'number') ? d.groups : null;
            s.groupUnit = d.group_unit || '';
            if (s.onCount) s.onCount(s.count, s.total, render, d.truncated);
            // One event, so anything that shows this layer's state (the stats
            // row, a pinned chip) is told rather than polling.
            try {
                window.dispatchEvent(new CustomEvent('lod:state', {
                    detail: { key: key, render: render, count: s.count,
                              total: s.total, truncated: !!d.truncated,
                              unit: s.unit, groups: s.groups, group_unit: s.groupUnit,
                              detail_mode: s.detail || 'auto' }
                }));
            } catch (e) {}
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

        /** The three detail preferences, in cycle order. */
        DETAIL_MODES: DETAIL_MODES,

        /**
         * setDetail(key, mode) — 'auto' | 'shapes' | 'fast', or undefined to
         * advance the cycle. Returns the mode now in force.
         *
         * The server still decides WHAT fits; this only says how much the user
         * wants drawn. Forcing 'shapes' on a continental view is allowed and
         * still truncates honestly rather than lying about the count.
         */
        setDetail: function (key, mode) {
            var s = reg.get(key);
            if (!s) return null;
            if (!mode) {
                var i = DETAIL_MODES.indexOf(s.detail || 'auto');
                mode = DETAIL_MODES[(i + 1) % DETAIL_MODES.length];
            }
            if (DETAIL_MODES.indexOf(mode) < 0) mode = 'auto';
            s.detail = mode;
            s.lastBBox = null;   // the budget changed; the last answer is stale
            load(key, 'detail');
            return mode;
        },
        detail: function (key) { return (reg.get(key) || {}).detail || 'auto'; },
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
        /** The shared per-feature detail cache, so a hover never re-asks. */
        detailFor: function (id) { return detailCache.get(id); },
        /**
         * Fetch a feature's full properties (narrative and all) and refresh
         * whatever tip is on screen when they land. Shared with the animator's
         * canvas probe: a paused frame's dots are the same rows as a pinned
         * layer's, so they must be the same answer and the same cache.
         */
        loadDetail: function (id, refreshLayerId) { return fetchDetail(id, refreshLayerId); },
        /** Render a feature tip the way a pinned layer would. */
        tipFor: function (props, type, parkName) {
            return tipHTML({ tipType: type, parkName: parkName }, props || {});
        },

        layerIds: function (key) { var L = ids(key); return [L.fill, L.line, L.point, L.dots]; }
    };

    window.LODLayer = LODLayer;
})();
