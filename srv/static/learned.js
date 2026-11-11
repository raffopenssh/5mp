/* What the learner approved, drawn on the map with no user action --
 * for the parks whose matching row in the popup's roads/rivers section is
 * pinned: tracks follow Roads, places follow Places, airstrips and landing
 * zones follow Airstrips. Nothing here is drawn unasked.
 *
 * GET /api/learned-map returns approved airstrips (a runway LINE where the
 * ground runs measured one), helicopter landing zones, places and road
 * sections, each with the evidence it was approved on. Outside the client
 * tenant the collection is empty and nothing is added.
 *
 * Every tip names its evidence -- journeys, days, last seen -- and its origin
 * ("learned from patrol tracks"): a symbol with no provenance reads as survey
 * data, and these are inferences. A length is printed only when it was
 * measured, and then as "at least": the tracks show the ground actually
 * rolled on, not the cleared strip.
 *
 * ?learned=0 keeps all of it off whatever is pinned.
 */
(function () {
    'use strict';
    var SRC = 'learned-map';
    var L = { roadCase: 'learned-roads-case', road: 'learned-roads', runwayCase: 'learned-runway-case', runway: 'learned-runway', pt: 'learned-points', air: 'learned-airfield-icon', ptH: 'learned-lz' };
    var COL = { airstrip: '#f8fafc', landing_zone: '#f0abfc', place: '#5eead4', road: '#fde68a' };

    function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
    function n(v, one, many) { v = Number(v) || 0; return v.toLocaleString() + ' ' + (v === 1 ? one : many); }
    function day(s) {
        if (!s) return 'unknown';
        var d = new Date(s + 'T00:00:00Z');
        return isNaN(d) ? esc(s) : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
    }
    var TITLE = { airstrip: 'Airstrip', landing_zone: 'Helicopter landing zone', airfield: 'Landing site', base: 'Base', outpost: 'Outpost', camp: 'Camp', stop: 'Regular stop', road: 'Road / track' };

    function tipHTML(p) {
        if (!p || !p.family) return '';
        var col = COL[p.family === 'airfield' ? p.kind : p.family] || '#e5e7eb';
        // An unnamed place is titled by where it is: "Camp" alone sends the
        // reader to the map to find out what the tip could have said.
        var label = p.name || (p.family !== 'road' && p.near_village && Number(p.near_village_km) <= 5 ? 'near ' + p.near_village : '');
        var h = '<div style="font-weight:600;margin-bottom:3px;color:' + col + '">' + esc(TITLE[p.kind] || p.kind) + (label ? ' \u00b7 ' + esc(label) : '') + '</div>';
        var loc = whereLine(p);
        if (loc) h += '<div style="margin-bottom:3px">' + loc + '</div>';
        var ev = [];
        if (p.unscored) {
            ev.push('approved by a reviewer; no event evidence kept');
        } else if (p.family === 'airfield') {
            ev.push(n(p.journeys, 'flight', 'flights') + ' on ' + n(p.days, 'day', 'days'));
            ev.push(n(p.landings, 'landing', 'landings') + ', ' + n(p.takeoffs, 'take-off', 'take-offs'));
            var fw = Number(p.fixed_wing_journeys) || 0, rw = Number(p.rotor_wing_journeys) || 0;
            if (fw || rw) ev.push((fw ? fw + ' fixed-wing' : '') + (fw && rw ? ', ' : '') + (rw ? rw + ' helicopter' : ''));
            if (p.kind === 'airstrip') {
                if (p.length_m) ev.push('runway at least ' + Math.round(p.length_m).toLocaleString() + ' m (ground runs observed)' + (p.axis_deg != null ? ', axis ' + runwayName(p.axis_deg) : ''));
                else ev.push('runway line not measured');
            }
            if (p.known_airfield) ev.push('matches listed airfield ' + esc(p.known_airfield));
        } else if (p.family === 'place') {
            ev.push(n(p.events, 'stay', 'stays') + ' on ' + n(p.days, 'day', 'days') + ' by ' + n(p.trackers, 'tracker', 'trackers'));
            if (p.dwell_median_min != null) ev.push('typical stay ' + dwell(p.dwell_median_min));
        } else {
            ev.push(n(p.events, 'vehicle pass', 'vehicle passes') + ' on ' + n(p.days, 'day', 'days') + ' by ' + n(p.trackers, 'tracker', 'trackers'));
            // Three states, three sentences: measured absent, measured present
            // (a later OSM refresh mapped the same track), and not measured.
            var ref = p.outside_reference ? 'outside the reference road map\u2019s coverage'
                : (Number(p.on_reference_frac) >= 0.5 ? 'now also on the OSM road map' : 'not on the reference road map');
            if (p.length_m) ev.push((p.length_m / 1000).toFixed(1) + ' km, ' + ref);
        }
        h += '<div>' + ev.join('<br>') + '</div>';
        h += '<div style="margin-top:3px">Last seen ' + day(p.last_seen) + '</div>';
        h += '<div style="opacity:.6;font-size:11px;margin-top:4px">Learned from patrol tracks'
            + (p.status === 'approved' ? ' \u00b7 approved by a reviewer' : (p.score != null ? ' \u00b7 auto-approved, ' + Math.round(p.score * 100) + '% likely in use' : '')) + '</div>';
        return h;
    }
    // Village and river from the OSM tables the nightly refresh maintains
    // (srv/learned_context.go); nothing in range prints nothing.
    function whereLine(p) {
        var out = [];
        if (p.near_village) {
            var km = Number(p.near_village_km) || 0;
            out.push((km < 0.3 ? 'in ' : (km < 10 ? km.toFixed(1) : Math.round(km)) + ' km ' + esc(p.near_village_dir || '') + ' of ')
                + esc(p.near_village) + ' <span style="opacity:.6">(' + esc(p.near_village_type || 'place') + ')</span>');
        }
        if (p.near_river) {
            var m = Number(p.near_river_m) || 0;
            out.push((m < 100 ? 'on the ' : (m >= 1000 ? (m / 1000).toFixed(1) + ' km' : m + ' m') + ' from the ') + esc(p.near_river)
                + (/river|mto\b/i.test(p.near_river) ? '' : ' <span style="opacity:.6">(river)</span>'));
        }
        return out.join('<br>');
    }
    function dwell(m) { m = Number(m); return m >= 90 ? (m / 60).toFixed(m >= 600 ? 0 : 1) + ' h' : Math.round(m) + ' min'; }
    function runwayName(axis) {
        var a = Math.round(Number(axis) / 10) % 18; if (a === 0) a = 18;
        var b = a + 18; return (a < 10 ? '0' : '') + a + '/' + b;
    }

    // Learned tracks are a ROADS layer: drawn only for the parks whose Roads
    // row is on (popup or stats panel -- both are the 'road' pin).
    var roadParks = [];
    function roadFilter() {
        return ['all', ['==', ['get', 'family'], 'road'], ['in', ['get', 'park_id'], ['literal', roadParks]]];
    }

    // Learned places likewise: only for parks whose Places row is on. The
    // Patrol visits pin draws the same sites as visit-sized circles (with this
    // file's tip), so where that is on the dot would be a second marker and
    // the caller leaves the park out. Airstrips share the layer and follow the
    // Airstrips pin (airParks), as do runways and landing zones.
    var placeParks = [], airParks = [];
    function inParks(ids) { return ['in', ['get', 'park_id'], ['literal', ids]]; }
    function ptFilter() {
        return ['all', ['==', ['geometry-type'], 'Point'], ['!=', ['get', 'kind'], 'landing_zone'],
            ['case', ['==', ['get', 'family'], 'place'], inParks(placeParks), inParks(airParks)]];
    }
    function runwayFilter() { return ['all', ['==', ['get', 'runway'], true], inParks(airParks)]; }
    function airFilter() { return ['all', ['==', ['geometry-type'], 'Point'], ['==', ['get', 'family'], 'airfield'], ['!=', ['get', 'kind'], 'landing_zone'], inParks(airParks)]; }
    function lzFilter() { return ['all', ['==', ['geometry-type'], 'Point'], ['==', ['get', 'kind'], 'landing_zone'], inParks(airParks)]; }

    // A bare white disc reads as a rendering fault, not as a landing site
    // (reported as "Safari shows white dots"). The disc carries a plane.
    var PLANE = 'M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z';
    function planeImage(map) {
        if (map.hasImage('learned-plane')) return true;
        try {
            var c = document.createElement('canvas'); c.width = c.height = 48;
            var g = c.getContext('2d');
            g.scale(2, 2); g.fillStyle = '#000'; g.fill(new Path2D(PLANE));
            map.addImage('learned-plane', g.getImageData(0, 0, 48, 48), { sdf: true, pixelRatio: 2 });
            return true;
        } catch (e) { return false; }
    }

    function addLayers(map) {
        var z = ['interpolate', ['linear'], ['zoom']];
        // A dark band under the dashes: the track is drawn ABOVE the pinned
        // OSM roads (see lift), and bare yellow dashes on a blue line read as
        // noise. The band cuts the road out so the dashes sit on dark ground.
        map.addLayer({ id: L.roadCase, type: 'line', source: SRC, minzoom: 7, filter: roadFilter(),
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: { 'line-color': '#0b1220', 'line-width': z.concat([7, 3.2, 12, 6.5]), 'line-opacity': 0.92 } });
        map.addLayer({ id: L.road, type: 'line', source: SRC, minzoom: 7, filter: roadFilter(),
            layout: { 'line-cap': 'butt', 'line-join': 'round' },
            paint: { 'line-color': COL.road, 'line-width': z.concat([7, 1.3, 12, 2.8]), 'line-dasharray': [2.2, 1.4] } });
        map.addLayer({ id: L.runwayCase, type: 'line', source: SRC, minzoom: 9, filter: runwayFilter(),
            paint: { 'line-color': '#0f172a', 'line-width': z.concat([9, 4, 14, 14]) } });
        map.addLayer({ id: L.runway, type: 'line', source: SRC, minzoom: 9, filter: runwayFilter(),
            paint: { 'line-color': COL.airstrip, 'line-width': z.concat([9, 2, 14, 9]) } });
        map.addLayer({ id: L.pt, type: 'circle', source: SRC, minzoom: 5,
            filter: ptFilter(),
            paint: {
                'circle-radius': z.concat([5, ['match', ['get', 'family'], 'airfield', 6, 2], 11, ['match', ['get', 'family'], 'airfield', 9, 5]]),
                'circle-color': ['match', ['get', 'family'], 'airfield', COL.airstrip, COL.place],
                'circle-stroke-color': '#0f172a', 'circle-stroke-width': 1.5,
                // An airstrip with a drawn runway steps back once the line is legible.
                // (a zoom expression must be outermost, so the case sits in the stop)
                'circle-opacity': z.concat([10, 1, 12, ['case', ['all', ['==', ['get', 'kind'], 'airstrip'], ['has', 'length_m']], 0.25, 1]]) } });
        if (planeImage(map)) map.addLayer({ id: L.air, type: 'symbol', source: SRC, minzoom: 5,
            filter: airFilter(),
            layout: { 'icon-image': 'learned-plane', 'icon-size': z.concat([5, 0.4, 11, 0.6]), 'icon-allow-overlap': true, 'icon-ignore-placement': true },
            paint: { 'icon-color': '#0f172a',
                'icon-opacity': z.concat([10, 1, 12, ['case', ['all', ['==', ['get', 'kind'], 'airstrip'], ['has', 'length_m']], 0.25, 1]]) } });
        map.addLayer({ id: L.ptH, type: 'circle', source: SRC, minzoom: 5,
            filter: lzFilter(),
            paint: { 'circle-radius': z.concat([5, 3, 11, 6]), 'circle-color': 'rgba(15,23,42,0.6)',
                'circle-stroke-color': COL.landing_zone, 'circle-stroke-width': 2 } });
        if (window.MapTip && MapTip.register) {
            MapTip.register(L.road, { html: tipHTML, tabLabel: 'Track', tabColor: COL.road, priority: 4 });
            MapTip.register(L.runway, { html: tipHTML, tabLabel: 'Airstrip', tabColor: COL.airstrip, priority: 7 });
            MapTip.register(L.pt, { html: tipHTML, tabLabel: 'Learned', tabColor: COL.place, priority: 8 });
            MapTip.register(L.ptH, { html: tipHTML, tabLabel: 'Landing zone', tabColor: COL.landing_zone, priority: 8 });
        }
    }

    var LearnedMap = {
        layers: L,
        data: null,
        tipHTML: tipHTML,
        setRoadParks: function (ids) {
            ids = (ids || []).slice().sort();
            if (ids.join('|') === roadParks.join('|')) return;
            roadParks = ids;
            var map = LearnedMap.map;
            if (map && map.getLayer(L.road)) { map.setFilter(L.roadCase, roadFilter()); map.setFilter(L.road, roadFilter()); }
        },
        // Pins are added after us and LOD pins restack themselves to the top,
        // so the order is re-asserted from both places: learned tracks over
        // the pinned roads, the pins' own point markers and labels over the
        // tracks, learned airstrips and places over everything.
        lift: function () {
            var map = LearnedMap.map;
            if (!map || !map.getLayer(L.road)) return;
            var mv = function (id) { try { if (map.getLayer(id)) map.moveLayer(id); } catch (e) { } };
            mv(L.roadCase); mv(L.road);
            ((map.getStyle() || {}).layers || []).forEach(function (l) {
                if (l.id.indexOf('pinned-') === 0 && (l.type === 'circle' || l.type === 'symbol')) mv(l.id);
            });
            [L.runwayCase, L.runway, L.pt, L.air, L.ptH].forEach(mv);
        },
        setPlaceParks: function (ids) {
            ids = (ids || []).slice().sort();
            if (ids.join('|') === placeParks.join('|')) return;
            placeParks = ids;
            var map = LearnedMap.map;
            if (map && map.getLayer(L.pt)) map.setFilter(L.pt, ptFilter());
        },
        setAirParks: function (ids) {
            ids = (ids || []).slice().sort();
            if (ids.join('|') === airParks.join('|')) return;
            airParks = ids;
            var map = LearnedMap.map;
            if (!map || !map.getLayer(L.pt)) return;
            var set = function (id, f) { if (map.getLayer(id)) map.setFilter(id, f); };
            set(L.pt, ptFilter()); set(L.runwayCase, runwayFilter()); set(L.runway, runwayFilter());
            set(L.air, airFilter()); set(L.ptH, lzFilter());
        },
        // After a reviewer decision in the admin panel: redraw from the server.
        reload: function () {
            var map = LearnedMap.map;
            if (!map || !map.getSource(SRC)) return;
            fetch('/api/learned-map', { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (fc) { if (fc && fc.features && map.getSource(SRC)) { LearnedMap.data = fc; map.getSource(SRC).setData(fc); } })
                .catch(function () { });
        },
        init: function (map) {
            LearnedMap.map = map;
            if (/[?&]learned=0\b/.test(location.search)) return;
            fetch('/api/learned-map', { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (fc) {
                    if (!fc || !fc.features || !fc.features.length) return; // other tenants: nothing to draw
                    LearnedMap.data = fc;
                    var go = function () {
                        if (map.getSource(SRC)) { map.getSource(SRC).setData(fc); return; }
                        map.addSource(SRC, { type: 'geojson', data: fc });
                        addLayers(map);
                        LearnedMap.lift();
                        // pins restored from a link may have landed before us
                        if (typeof window.updatePinnedIndicator === 'function') window.updatePinnedIndicator();
                    };
                    if (map.isStyleLoaded && map.isStyleLoaded()) go(); else map.once('idle', go);
                    // A basemap switch replaces the style and drops our layers.
                    map.on('style.load', function () { if (!map.getSource(SRC)) go(); });
                })
                .catch(function () { /* the map works without it */ });
        }
    };
    window.LearnedMap = LearnedMap;
})();
