/* What the learner approved, drawn on the map with no user action --
 * except road sections, which follow the Roads toggle (setRoadParks).
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
 * ?learned=0 hides the layer.
 */
(function () {
    'use strict';
    var SRC = 'learned-map';
    var L = { road: 'learned-roads', runwayCase: 'learned-runway-case', runway: 'learned-runway', pt: 'learned-points', ptH: 'learned-lz' };
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
    // row is on (popup or stats panel -- both are the 'road' pin). Airstrips,
    // landing zones and places stay on by default.
    var roadParks = [];
    function roadFilter() {
        return ['all', ['==', ['get', 'family'], 'road'], ['in', ['get', 'park_id'], ['literal', roadParks]]];
    }

    // Learned places likewise: only for parks whose Places or Patrol visits
    // row is on. Airstrips share the layer and are not gated.
    var placeParks = [];
    function ptFilter() {
        return ['all', ['==', ['geometry-type'], 'Point'], ['!=', ['get', 'kind'], 'landing_zone'],
            ['any', ['!=', ['get', 'family'], 'place'], ['in', ['get', 'park_id'], ['literal', placeParks]]]];
    }

    function addLayers(map) {
        var z = ['interpolate', ['linear'], ['zoom']];
        map.addLayer({ id: L.road, type: 'line', source: SRC, minzoom: 7, filter: roadFilter(),
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: { 'line-color': COL.road, 'line-width': z.concat([7, 0.8, 12, 2.2]), 'line-opacity': 0.85, 'line-dasharray': [2, 1.5] } });
        map.addLayer({ id: L.runwayCase, type: 'line', source: SRC, minzoom: 9, filter: ['==', ['get', 'runway'], true],
            paint: { 'line-color': '#0f172a', 'line-width': z.concat([9, 4, 14, 14]) } });
        map.addLayer({ id: L.runway, type: 'line', source: SRC, minzoom: 9, filter: ['==', ['get', 'runway'], true],
            paint: { 'line-color': COL.airstrip, 'line-width': z.concat([9, 2, 14, 9]) } });
        map.addLayer({ id: L.pt, type: 'circle', source: SRC, minzoom: 5,
            filter: ptFilter(),
            paint: {
                'circle-radius': z.concat([5, ['match', ['get', 'family'], 'airfield', 3.5, 2], 11, ['match', ['get', 'family'], 'airfield', 7, 5]]),
                'circle-color': ['match', ['get', 'family'], 'airfield', COL.airstrip, COL.place],
                'circle-stroke-color': '#0f172a', 'circle-stroke-width': 1.5,
                // An airstrip with a drawn runway steps back once the line is legible.
                // (a zoom expression must be outermost, so the case sits in the stop)
                'circle-opacity': z.concat([10, 1, 12, ['case', ['all', ['==', ['get', 'kind'], 'airstrip'], ['has', 'length_m']], 0.25, 1]]) } });
        map.addLayer({ id: L.ptH, type: 'circle', source: SRC, minzoom: 5,
            filter: ['all', ['==', ['geometry-type'], 'Point'], ['==', ['get', 'kind'], 'landing_zone']],
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
            if (map && map.getLayer(L.road)) map.setFilter(L.road, roadFilter());
        },
        setPlaceParks: function (ids) {
            ids = (ids || []).slice().sort();
            if (ids.join('|') === placeParks.join('|')) return;
            placeParks = ids;
            var map = LearnedMap.map;
            if (map && map.getLayer(L.pt)) map.setFilter(L.pt, ptFilter());
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
