// ============================================================
// 5MP geology overlay — two scanned sheets, vectorized.
//
// Sudan (GRAS 2004, 1:2M) and CAR (BRGM 1964, 1:1.5M), turned into
// polygons by scripts/geomaps/ and served as vector tiles by
// srv/geomap.go. See docs/GEOLOGY_HANDOVER.md.
//
// Vector, not raster — and that IS the feature. The whole point of
// this overlay is that a park facing a gold rush can switch on
// every unit that hosts gold in one click, then hide or isolate
// individual ones. A raster drape can do none of that: it would
// need one pre-rendered tileset per combination of ~47 classes.
//
// Three things that are load-bearing here:
//
// 1. COMMODITY AFFINITY IS AN INFERENCE, NEVER AN OCCURRENCE.
//    "Rocks of this kind host gold" is a statement about lithology,
//    not about a deposit, and nothing here counts, ranks or locates
//    one. Every surface that shows it says so — same line as the
//    mining verdict in AGENTS.md: inference from context ships,
//    fabricated evidence does not.
//
// 2. A MERGED CLASS IS LABELLED WITH ALL ITS CODES. Where the print
//    screen does not separate two units (CAR's GO/GC2, Sudan's
//    MSq/TQ) the vectorizer emits one class carrying both codes,
//    because the sheet genuinely does not say which one a patch is.
//    The UI must never pick one to show.
//
// 3. LAYER ORDER. The units go directly above the basemap raster and
//    below every vector overlay — park/AOI outlines, trajectories,
//    pins. Same rule and the same `before` anchoring as HistMap; see
//    the reattach() note there about styledata vs idle.
// ============================================================
(function () {
    'use strict';

    const SRC = id => 'geomap-src-' + id;
    const FILL = id => 'geomap-fill-' + id;
    const LINE = id => 'geomap-line-' + id;

    let sheets = null;          // id -> catalogue entry from /api/geomap
    let order = [];             // sheet ids in server order
    let metaPromise = null;
    const state = {};           // id -> {on, opacity, hidden:Set, isolate:Set|null}

    function api(path) {
        return path + (path.indexOf('?') >= 0 ? '&' : '?') +
            'pwd=' + encodeURIComponent(getPwd());
    }

    function st(id) {
        if (!state[id]) state[id] = {
            on: false, opacity: 0.55, hidden: new Set(), isolate: null,
            // Commodity chips are a MULTI-select: the isolation is the UNION
            // of the host sets of every selected commodity. "gold" alone and
            // "gold + copper" are both ordinary questions, and a radio button
            // makes the second one impossible without hand-picking codes out
            // of a 47-row legend.
            commodities: new Set()
        };
        return state[id];
    }

    function fetchMeta() {
        if (!metaPromise) {
            metaPromise = fetch(api('/api/geomap'))
                .then(r => r.json())
                .then(j => {
                    sheets = {};
                    order = [];
                    (j.sheets || []).forEach(s => { sheets[s.id] = s; order.push(s.id); });
                    return sheets;
                })
                .catch(() => { sheets = {}; order = []; return sheets; });
        }
        return metaPromise;
    }

    // Classes of a sheet, whether or not its tiles are installed: the
    // catalogue is served even when only vectorize.py has run, so the panel
    // can name what is missing instead of showing an empty list.
    function classesOf(id) {
        const s = sheets && sheets[id];
        return (s && s.catalogue && s.catalogue.classes) || [];
    }

    // Which classes are drawn right now. `isolate` (a commodity selection, or
    // a single class pinned from the legend) wins over `hidden`, so clicking
    // "gold" never has to guess what the user hid earlier.
    function visibleCodes(id) {
        const s = st(id);
        const all = classesOf(id).map(c => c.code);
        if (s.isolate && s.isolate.size) return all.filter(c => s.isolate.has(c));
        return all.filter(c => !s.hidden.has(c));
    }

    // A data-driven fill-colour ramp built from the catalogue: the polygons
    // carry a `code`, and the colour is the ink measured off the scan. Doing
    // it as one match expression rather than a layer per class keeps this to
    // two layers per sheet regardless of how many units the sheet has.
    function colorExpr(id) {
        const e = ['match', ['get', 'code']];
        classesOf(id).forEach(c => { e.push(c.code, c.color); });
        e.push('#888888');
        return e;
    }

    function filterExpr(id) {
        return ['in', ['get', 'code'], ['literal', visibleCodes(id)]];
    }

    // See the HistMap note: anchor above the basemap raster and below the
    // first vector layer, so anything added later (a pin, a trajectory) lands
    // on top for free. 'background' is skipped — it is the paper the basemap
    // sits on, and inserting before it would hide the overlay entirely.
    function firstOverlayLayer() {
        const style = map.getStyle();
        if (!style || !style.layers) return undefined;
        const l = style.layers.find(x =>
            x.id.indexOf('geomap-') !== 0 && x.type !== 'raster' && x.type !== 'background');
        return l ? l.id : undefined;
    }

    function add(id) {
        const s = sheets && sheets[id];
        if (!s || !s.available || !map || !map.isStyleLoaded()) return;
        const o = st(id);
        if (!map.getSource(SRC(id))) {
            map.addSource(SRC(id), {
                type: 'vector',
                tiles: [window.location.origin + api(s.tiles)],
                minzoom: s.minzoom || 0,
                maxzoom: s.maxzoom || 10,
                bounds: s.bounds || undefined,
                attribution: (s.catalogue && s.catalogue.publisher)
                    ? s.catalogue.publisher + ', ' + s.catalogue.year : ''
            });
        }
        const before = firstOverlayLayer();
        if (!map.getLayer(FILL(id))) {
            map.addLayer({
                id: FILL(id), type: 'fill', source: SRC(id), 'source-layer': 'units',
                filter: filterExpr(id),
                paint: { 'fill-color': colorExpr(id), 'fill-opacity': o.opacity }
            }, before);
        }
        if (!map.getLayer(LINE(id))) {
            // A hairline in the unit's own colour, darkened by opacity rather
            // than a shared grey: at continental zoom 47 classes of flat fill
            // with no edge read as a single blotch.
            map.addLayer({
                id: LINE(id), type: 'line', source: SRC(id), 'source-layer': 'units',
                filter: filterExpr(id),
                paint: {
                    'line-color': colorExpr(id), 'line-width': 0.6,
                    'line-opacity': Math.min(1, o.opacity + 0.25)
                }
            }, before);
        }
        bindTip(id);
    }

    function remove(id) {
        [FILL(id), LINE(id)].forEach(l => { if (map.getLayer(l)) map.removeLayer(l); });
        if (map.getSource(SRC(id))) map.removeSource(SRC(id));
        // Switching the sheet off must also take its tip away, or a click keeps
        // being swallowed by a layer that is no longer on the map.
        if (window.MapTip) window.MapTip.unregister(FILL(id));
        bound[id] = false;
    }

    function refresh(id) {
        if (!map.getLayer(FILL(id))) return;
        const o = st(id);
        [FILL(id), LINE(id)].forEach(l => map.setFilter(l, filterExpr(id)));
        map.setPaintProperty(FILL(id), 'fill-opacity', o.opacity);
        map.setPaintProperty(LINE(id), 'line-opacity', Math.min(1, o.opacity + 0.25));
    }

    // Click a unit and it says what it is. Deliberately a click and not a
    // hover tip: the overlay covers the whole country, so a hover handler
    // would fight every other tip on the map for the same pixel — it would
    // never have an "off it" to move to. That is what MapTip's `clickOnly`
    // means, and `priority: -30` says the same thing about depth: geology is
    // the backdrop *under* everything (even under the AOI polygon), so a
    // trajectory, a settlement or an area always answers the click first.
    //
    // It used to open its own maplibregl.Popup, which is why a tap could
    // produce three answers at once (geology popup + AOI popup + AOI map tip):
    // its click never went through the shared arbitration. One tip, one owner.
    const bound = {};
    function tipHTML(id, p) {
            const cat = (sheets[id] && sheets[id].catalogue) || {};
            let aff = [];
            try { aff = JSON.parse(p.affinity || '[]'); } catch (err) { aff = []; }
            const codes = String(p.code || '').split('/');
            const merged = codes.length > 1;
            return `
                <div style="font-family:inherit;max-width:260px;">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span style="width:14px;height:14px;border-radius:3px;flex:none;background:${escapeHtml(p.color || '#888')};border:1px solid rgba(0,0,0,0.3);"></span>
                        <b style="color:#fff;font-size:13px;">${escapeHtml(p.code || '')}</b>
                    </div>
                    <div style="color:#ddd;font-size:12px;margin-top:6px;line-height:1.45;">${escapeHtml(p.name || '')}</div>
                    <div style="color:#888;font-size:11px;margin-top:4px;">${escapeHtml(p.group || '')} &middot; ${escapeHtml(cat.short || id)}</div>
                    ${merged ? `<div style="color:#f59e0b;font-size:11px;margin-top:6px;line-height:1.4;">
                        Printed as ${codes.length} units (${escapeHtml(codes.join(', '))}) in inks this sheet's
                        print screen does not separate &mdash; which one a given patch is, the map does not say.</div>` : ''}
                    ${aff.length ? `<div style="margin-top:8px;border-top:1px solid rgba(255,255,255,0.08);padding-top:6px;">
                        <div style="color:#aaa;font-size:11px;font-weight:600;">Commodities this rock type can host</div>
                        ${aff.map(a => `<div style="color:#ccc;font-size:11px;margin-top:4px;line-height:1.4;">
                            ${'&#9679;'.repeat(a.weight)} <b style="color:#fff;">${escapeHtml(a.commodity)}</b> &mdash; ${escapeHtml(a.why)}</div>`).join('')}
                        <div style="color:#777;font-size:10px;margin-top:6px;line-height:1.4;">
                            An inference from lithology, not a record of any deposit. Nothing here
                            counts, ranks or locates an occurrence.</div>
                    </div>` : ''}
                </div>`;
    }

    function bindTip(id) {
        if (bound[id]) return;
        bound[id] = true;
        if (window.MapTip) {
            window.MapTip.register(FILL(id), {
                clickOnly: true,
                priority: -30,
                html: p => tipHTML(id, p)
            });
            return;
        }
        // No MapTip (shouldn't happen; keeps the overlay usable if it fails to load)
        map.on('click', FILL(id), e => {
            const f = e.features && e.features[0];
            if (!f) return;
            new maplibregl.Popup({ closeButton: true, maxWidth: '280px' })
                .setLngLat(e.lngLat).setHTML(tipHTML(id, f.properties || {})).addTo(map);
        });
        map.on('mouseenter', FILL(id), () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', FILL(id), () => { map.getCanvas().style.cursor = ''; });
    }

    // Only codes this build of the sheet actually has. See restoreFromParams.
    function keep(id, codes) {
        const known = new Set(classesOf(id).map(c => c.code));
        return new Set(codes.filter(c => known.has(c)));
    }

    // Every commodity this sheet mentions -> the codes that can host it.
    function hostMap(id) {
        const all = {};
        classesOf(id).forEach(c => (c.commodities || []).forEach(k => {
            (all[k] = all[k] || []).push(c.code);
        }));
        return all;
    }

    // Which chips a given isolation lights up. Derived rather than stored, so
    // a chip stays honest after the user hand-hides one of its units from the
    // legend: a commodity reads as selected only while ALL of its host units
    // are actually drawn.
    function commoditiesCovered(id, codes) {
        const all = hostMap(id);
        return new Set(Object.keys(all).filter(
            k => all[k].length && all[k].every(c => codes.has(c))));
    }

    // isolate := union of the selected commodities' host sets. Empty selection
    // is "no isolation" (show everything), never "show nothing" — an empty map
    // is indistinguishable from a sheet with no data here.
    function applyCommodities(id) {
        const o = st(id);
        if (!o.commodities.size) { o.isolate = null; return; }
        const all = hostMap(id);
        const codes = new Set();
        o.commodities.forEach(k => (all[k] || []).forEach(c => codes.add(c)));
        o.isolate = codes.size ? codes : null;
    }

    const GeoMap = {
        ensureMeta: fetchMeta,
        sheets: () => sheets,
        order: () => order,
        classes: classesOf,
        state: st,
        isOn: id => st(id).on,
        anyOn: () => order.some(id => st(id).on),

        async set(id, want, opts) {
            opts = opts || {};
            await fetchMeta();
            const s = sheets && sheets[id];
            if (want && !(s && s.available)) {
                if (!opts.quiet) {
                    showToast('Geology sheet unavailable',
                        (s && s.reason) || 'not built on this server', null, null, 'warning');
                }
                return false;
            }
            st(id).on = !!want;
            if (map && map.isStyleLoaded()) { want ? add(id) : remove(id); }
            else if (want) { map.once('idle', () => add(id)); }
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            if (want && opts.fly && s.center) {
                map.flyTo({ center: [s.center[0], s.center[1]], zoom: Math.max(map.getZoom(), 5) });
            }
            return st(id).on;
        },

        toggle(id) { return this.set(id, !st(id).on, { fly: !st(id).on }); },

        setOpacity(id, v) {
            st(id).opacity = Math.max(0, Math.min(1, v));
            refresh(id);
            const lbl = document.getElementById('geomap-op-' + id);
            if (lbl) lbl.textContent = Math.round(st(id).opacity * 100) + '%';
        },

        toggleClass(id, code) {
            const o = st(id);
            // Hiding while isolated means "drop this one from the isolation",
            // which is the only reading that does not silently discard the
            // isolation the user just built.
            if (o.isolate) {
                o.isolate.delete(code);
                if (!o.isolate.size) o.isolate = null;
                // The chips describe the isolation, so re-derive them rather
                // than leaving "gold" lit next to a gold-bearing unit the user
                // just switched off.
                o.commodities = o.isolate ? commoditiesCovered(id, o.isolate) : new Set();
            }
            else if (o.hidden.has(code)) o.hidden.delete(code);
            else o.hidden.add(code);
            refresh(id);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },

        // The headline interaction: "show me everything that can host gold" —
        // and, one more click, "...or copper". Each chip is an independent
        // switch over the same isolation, so the map shows the union and the
        // panel shows exactly which switches are down.
        toggleCommodity(id, commodity) {
            const o = st(id);
            // Hand-hidden units from the legend are a different question and
            // would silently subtract from the union; drop them when a chip is
            // used, the same way isolation already wins over `hidden`.
            if (o.commodities.has(commodity)) o.commodities.delete(commodity);
            else o.commodities.add(commodity);
            applyCommodities(id);
            if (!o.on) { this.set(id, true, { quiet: true }); }
            refresh(id);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },

        // Old name kept: share links and any caller predating the multi-select.
        isolateCommodity(id, commodity) { return this.toggleCommodity(id, commodity); },

        commodityOn(id, commodity) { return st(id).commodities.has(commodity); },

        showAll(id) {
            const o = st(id);
            o.hidden = new Set(); o.isolate = null; o.commodities = new Set();
            refresh(id);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },

        // switchBasemap() rebuilds the style and is told to skip our layers,
        // so we re-insert at the right depth here. On 'idle', not inside the
        // 'styledata' handler: addLayer() with a `before` id that has not
        // landed yet is silently dropped.
        reattach() {
            if (!this.anyOn()) return;
            map.once('idle', () => {
                order.forEach(id => { if (st(id).on) { remove(id); add(id); } });
            });
        },

        // Share-link state. `?geomap=sudan` is the common case and stays
        // short; the rest only appears once the user has actually changed it,
        // so a plain "here is the geology" link does not carry 47 codes.
        getShareParams() {
            const on = order.filter(id => st(id).on);
            if (!on.length) return null;
            const p = { geomap: on.join(',') };
            const only = [], hide = [], op = [], host = [];
            on.forEach(id => {
                const o = st(id);
                // A commodity selection travels as the commodities, not as the
                // codes they expand to: a rebuild can merge or rename units,
                // and "everything that can host gold" is still answerable
                // afterwards while a frozen code list is not.
                if (o.commodities.size) host.push(id + ':' + [...o.commodities].join('|'));
                else if (o.isolate && o.isolate.size) only.push(id + ':' + [...o.isolate].join('|'));
                else if (o.hidden.size) hide.push(id + ':' + [...o.hidden].join('|'));
                if (Math.abs(o.opacity - 0.55) > 0.01) op.push(id + ':' + Math.round(o.opacity * 100));
            });
            if (host.length) p.geomap_host = host.join(',');
            if (only.length) p.geomap_only = only.join(',');
            if (hide.length) p.geomap_hide = hide.join(',');
            if (op.length) p.geomap_opacity = op.join(',');
            return p;
        },

        async restoreFromParams(params) {
            const want = (params.get('geomap') || '').split(',').filter(Boolean);
            if (!want.length) return;
            await fetchMeta();
            const parse = (raw, fn) => (raw || '').split(',').filter(Boolean).forEach(part => {
                const i = part.indexOf(':');
                if (i > 0) fn(part.slice(0, i), part.slice(i + 1));
            });
            parse(params.get('geomap_opacity'), (id, v) => { st(id).opacity = (parseInt(v, 10) || 55) / 100; });
            parse(params.get('geomap_hide'), (id, v) => { st(id).hidden = keep(id, v.split('|')); });
            parse(params.get('geomap_only'), (id, v) => {
                const codes = keep(id, v.split('|'));
                // A code that no longer exists must not silently produce an
                // empty map: a rebuild can MERGE two classes, which renames
                // both (GO + GC2 become 'GC2/GO'), so an old link's codes can
                // all disappear at once. Rendering nothing looks exactly like
                // "this sheet has no data here", so drop the isolation and say
                // so instead.
                if (!codes.size) {
                    if (typeof showToast === 'function') {
                        showToast('Geology selection is out of date',
                            'That link names units this build of the sheet no longer has — showing all of them.',
                            null, null, 'warning');
                    }
                    return;
                }
                st(id).isolate = codes;
                st(id).commodities = commoditiesCovered(id, codes);
            });
            // Commodity chips last: they are the authoritative selection and
            // recompute `isolate` from the sheet as built.
            parse(params.get('geomap_host'), (id, v) => {
                const known = new Set(Object.keys(hostMap(id)));
                const want = v.split('|').filter(k => known.has(k));
                if (!want.length) {
                    if (typeof showToast === 'function') {
                        showToast('Geology selection is out of date',
                            'That link names rock-type hosts this build of the sheet does not list \u2014 showing all units.',
                            null, null, 'warning');
                    }
                    return;
                }
                st(id).commodities = new Set(want);
                applyCommodities(id);
            });
            for (const id of want) await this.set(id, true, { quiet: true, fly: false });
        }
    };

    window.GeoMap = GeoMap;
})();
