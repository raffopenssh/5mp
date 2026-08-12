// ============================================================
// 5MP geology overlay — two scanned sheets, vectorized.
//
// Sudan (GRAS 2004, 1:2M) and CAR (BRGM 1964, 1:1.5M), turned into
// polygons by scripts/geomaps/ and served as vector tiles by
// srv/geomap.go. See docs/GEOLOGY.md.
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
//
// 4. IT IS ONE LAYER, NOT TWO SHEETS. Sudan and CAR are two scans
//    that happen to be the provenance of one thing the user wants:
//    "what rock is under here?". Presenting them as two cards, two
//    legends, two opacity sliders and two toggles made the user
//    reconcile two 40-years-apart colour languages — and, at the
//    border, the same rock changed colour. So: ONE Geology switch,
//    ONE legend, and the sheet demoted to what it is, provenance,
//    named in the unit card and in the panel's footnote. The
//    per-sheet API is still here (a share link, a download and the
//    tile source are all per sheet) — it is just no longer the
//    user's unit of thought.
//
// 5. THE LEGEND IS THE INDUSTRY'S, NOT OURS. Colour = age, from the
//    ICS/CGMW International Chronostratigraphic Chart; ornament =
//    lithology, from FGDC-STD-013-2006 §37. Both are computed
//    server-side (srv/geomap_std.go) and ride in the catalogue, so
//    this file never hard-codes a colour or a hatch. Two reasons the
//    hatch is load-bearing and not decoration: a flat translucent
//    fill on a dark basemap is read as WATER (that is the bug this
//    came from — nothing else on the map is hatched), and a texture
//    survives being drawn at 20% opacity over an arbitrary basemap
//    where a hue does not.
//
//    The printed ink is not discarded: `colorMode: 'ink'` draws the
//    sheet as it was printed, which is the honest view when the
//    question is about the SCAN rather than about the rock.
// ============================================================
(function () {
    'use strict';

    const SRC = id => 'geomap-src-' + id;
    const FILL = id => 'geomap-fill-' + id;
    const LINE = id => 'geomap-line-' + id;
    const PAT = (lith, age) => 'geopat-' + lith + '-' + age;

    let sheets = null;          // id -> catalogue entry from /api/geomap
    let order = [];             // sheet ids in server order
    let gpkg = null;            // the ONE combined GeoPackage: {url, bytes, sheets}
    let metaPromise = null;
    const state = {};           // id -> {on, opacity, hidden:Set, isolate:Set|null}

    // Everything the user actually operates is GLOBAL, because the user
    // operates "geology", not "the 1964 BRGM sheet": one on/off, one opacity,
    // one colour mode, one lithology filter. Per-sheet state stays for the
    // things that really are per sheet (which classes are hidden, where the
    // tiles come from, what the download is).
    const shared = {
        colorMode: 'age',       // 'age' (ICS) | 'ink' (as printed)
        pattern: true,          // FGDC ornament on/off
        // Opacity ADAPTS to the basemap by default. The right value is not a
        // constant: 0.42 reads well over the dark basemap and all but
        // disappears over satellite imagery, which is exactly how a working
        // geology layer gets reported as "not showing anything". Auto re-picks
        // whenever the basemap changes; moving the slider is what turns it
        // off, so a hand-set value is never silently overwritten.
        opacityAuto: true,
        opacity: 0.52,
        liths: new Set(),       // lithology filter; empty = all
        // Whether the Advanced block is open. A setting, so it travels in the
        // share link with the rest — "look at this" should reproduce the panel
        // the sender was reading, not just the map.
        advOpen: false
    };

    // Per basemap, the opacity at which a hatched drape is legible without
    // burying what is under it. Satellite imagery is bright, busy and
    // mid-toned, so it needs distinctly more ink than the near-black basemap;
    // both were picked by looking at the map, not by halving a number.
    function autoOpacity() {
        const b = (typeof currentBasemap === 'string') ? currentBasemap : 'dark';
        return b.indexOf('satellite') === 0 ? 0.72 : 0.52;
    }

    function api(path) {
        return path + (path.indexOf('?') >= 0 ? '&' : '?') +
            'pwd=' + encodeURIComponent(getPwd());
    }

    function st(id) {
        if (!state[id]) state[id] = {
            on: false, hidden: new Set(), isolate: null,
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
                    // ONE GeoPackage for every sheet — a property of the
                    // catalogue, not of a sheet, because the map is one layer
                    // and the data behind it must not arrive as a per-country
                    // jigsaw for the user to reassemble.
                    gpkg = j.geopackage
                        ? { url: j.geopackage, bytes: j.geopackage_bytes || 0,
                            sheets: j.geopackage_sheets || [] }
                        : null;
                    return sheets;
                })
                .catch(() => { sheets = {}; order = []; gpkg = null; return sheets; });
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

    // The shared legend the server computed (ICS ages, FGDC lithologies).
    // From the first sheet that carries it — it is one legend by construction,
    // so taking it from a sheet is only a transport detail.
    function stdLegend() {
        for (const id of order) {
            const s = sheets && sheets[id];
            if (s && s.catalogue && s.catalogue.std) return s.catalogue.std;
        }
        return { ages: [], lithology: [] };
    }

    function ageMeta(key) {
        return (stdLegend().ages || []).find(a => a.key === key) ||
               { key: key, label: key, color: '#BDBDBD', rank: 99 };
    }

    /** Every class of every sheet, as one list — the user's unit of thought. */
    function allClasses() {
        const out = [];
        order.forEach(id => classesOf(id).forEach(c => out.push(Object.assign({ sheet: id }, c))));
        return out;
    }

    /** One class by (sheet, code) — what a tile feature indexes into. */
    function classOf(id, code) {
        return classesOf(id).find(c => c.code === code) || null;
    }

    function classColor(c) {
        return shared.colorMode === 'ink' ? (c.color || '#888888')
                                          : (c.ics_color || ageMeta(c.age).color);
    }

    // A contact is drawn in a DARKENED version of the unit, never in the unit's
    // own colour. These polygons are traced off a scan, so their boundaries
    // carry every wiggle of the print: 46 classes outlined at full saturation
    // is a net of bright magenta over an entire country, and it out-shouts the
    // fills it is supposed to bound. Same ink the ornament uses, so a contact
    // and its hatch read as one drawing.
    function inkColor(c) {
        const h = String(classColor(c)).replace('#', '');
        const v = parseInt(h.length === 3 ? h.split('').map(x => x + x).join('') : h, 16);
        if (isNaN(v)) return '#555555';
        const r = (v >> 16) & 255, g = (v >> 8) & 255, b = v & 255;
        const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        const k = lum > 0.55 ? 0.45 : 0.62;
        const hex = n => Math.round(n * (1 - k)).toString(16).padStart(2, '0');
        return '#' + hex(r) + hex(g) + hex(b);
    }

    // Which classes are drawn right now. `isolate` (a commodity selection, or
    // a single class pinned from the legend) wins over `hidden`, so clicking
    // "gold" never has to guess what the user hid earlier. The lithology
    // filter is ANDed on top: it is a legend-wide question ("show me the
    // intrusives") and must not silently discard a commodity selection.
    function visibleCodes(id) {
        const s = st(id);
        const cls = classesOf(id);
        let keep = cls;
        if (s.isolate && s.isolate.size) keep = keep.filter(c => s.isolate.has(c.code));
        else keep = keep.filter(c => !s.hidden.has(c.code));
        if (shared.liths.size) keep = keep.filter(c => shared.liths.has(c.lith));
        return keep.map(c => c.code);
    }

    // A data-driven ramp built from the catalogue: the polygons carry a
    // `code`, and the catalogue says what colour and what ornament that code
    // gets. One `match` expression rather than a layer per class keeps this to
    // two layers per sheet regardless of how many units the sheet has.
    function colorExpr(id) {
        const e = ['match', ['get', 'code']];
        classesOf(id).forEach(c => { e.push(c.code, classColor(c)); });
        e.push('#888888');
        return e;
    }

    function lineColorExpr(id) {
        const e = ['match', ['get', 'code']];
        classesOf(id).forEach(c => { e.push(c.code, inkColor(c)); });
        e.push('#555555');
        return e;
    }

    // The FGDC ornament, as a fill-pattern per class.
    //
    // MapLibre needs each pattern registered as an IMAGE before a layer can
    // name it, and an unregistered name renders as NOTHING — an invisible
    // unit, which is the one failure this overlay must never have (a missing
    // formation reads as "no data here"). So: register every pattern the
    // sheet can ask for, up front and synchronously, and fall back to the flat
    // fill if the pattern module did not load at all.
    //
    // Keyed on (lithology, colour) because the ink is derived from the fill:
    // one tile serves every class that shares both, so 46 classes cost ~12
    // images, not 46.
    function ensurePatterns(id) {
        if (!shared.pattern || !window.GeoPatterns || !map) return false;
        let ok = false;
        classesOf(id).forEach(c => {
            const name = patternName(c);
            if (map.hasImage && map.hasImage(name)) { ok = true; return; }
            try {
                map.addImage(name, window.GeoPatterns.tile(c.lith || 'mixed', classColor(c), 1),
                             { pixelRatio: 2 });
                ok = true;
            } catch (err) { /* already added by a concurrent add() */ ok = true; }
        });
        return ok;
    }

    function patternName(c) {
        return PAT(c.lith || 'mixed', String(classColor(c)).replace('#', ''));
    }

    function patternExpr(id) {
        const e = ['match', ['get', 'code']];
        classesOf(id).forEach(c => { e.push(c.code, patternName(c)); });
        e.push(patternName({ lith: 'mixed', color: '#888888', ics_color: '#888888' }));
        return e;
    }

    // Applying the paint is one function because the two modes must stay
    // mutually exclusive: a fill-pattern silently WINS over fill-color in
    // MapLibre, so leaving a stale pattern set is how "as printed" quietly
    // keeps showing the age colours.
    function paintFill(id) {
        const patterned = ensurePatterns(id);
        if (patterned) {
            map.setPaintProperty(FILL(id), 'fill-pattern', patternExpr(id));
            // No boost: the tile already weights ink over background, so the
            // slider means the same thing in both modes and turning geology
            // down actually turns it down.
            map.setPaintProperty(FILL(id), 'fill-opacity', shared.opacity);
        } else {
            map.setPaintProperty(FILL(id), 'fill-pattern', undefined);
            map.setPaintProperty(FILL(id), 'fill-color', colorExpr(id));
            map.setPaintProperty(FILL(id), 'fill-opacity', shared.opacity);
        }
        // The contact hairline is a boundary, not a highlight: at full colour
        // it out-shouts the fill it is bounding (46 saturated outlines is a
        // net of pink over a whole country). Half the fill's opacity, so it
        // separates units without competing with them.
        map.setPaintProperty(LINE(id), 'line-color', lineColorExpr(id));
        map.setPaintProperty(LINE(id), 'line-opacity', Math.min(0.75, shared.opacity * 1.1));
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
                paint: { 'fill-color': colorExpr(id), 'fill-opacity': shared.opacity }
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
                    'line-color': lineColorExpr(id), 'line-width': 0.5,
                    'line-opacity': Math.min(0.75, shared.opacity * 1.1)
                }
            }, before);
        }
        paintFill(id);
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
        [FILL(id), LINE(id)].forEach(l => map.setFilter(l, filterExpr(id)));
        paintFill(id);
    }

    /** Re-apply everything shared to every sheet on the map. */
    function refreshAll() {
        order.forEach(id => { if (st(id).on) refresh(id); });
        if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
    }

    // Hover a unit and it says what it is — an ordinary hover tip, the same as
    // a fire trajectory, a road or a river. It used to be `clickOnly`, on the
    // theory that a country-sized drape has no "off it" to move to, and that
    // was over-thought: MapTip only ever shows ONE tip, so a geology answer
    // simply loses to anything more specific under the cursor, and where there
    // is nothing more specific, naming the rock is exactly what the user wants.
    // (`clickOnly` is still right for the AOI polygon, which owns a click.)
    //
    // `priority: -30` still says geology is the backdrop *under* everything:
    // a trajectory, a settlement or an area answers first, on hover and on
    // click alike.
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
            // The TILE carries code/name/group/ink; age and lithology live in
            // the catalogue, keyed by code. Joining here (rather than baking
            // them into the tiles) is what lets the legend change without
            // invalidating 3 GB of vector tiles.
            const cls = classOf(id, p.code) || {};
            const age = ageMeta(cls.age);
            const lith = (stdLegend().lithology || []).find(l => l.key === cls.lith);
            const swatch = window.GeoPatterns
                ? `background-image:${window.GeoPatterns.swatchCSS(cls.lith || 'mixed', classColor(cls))};background-size:22px 22px;`
                : `background:${escapeHtml(classColor(cls))};`;
            return `
                <div style="font-family:inherit;max-width:260px;">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span style="width:16px;height:16px;border-radius:3px;flex:none;${swatch}border:1px solid rgba(0,0,0,0.45);"></span>
                        <b style="color:#fff;font-size:13px;">${escapeHtml(p.code || '')}</b>
                    </div>
                    <div style="color:#ddd;font-size:12px;margin-top:6px;line-height:1.45;">${escapeHtml(p.name || '')}</div>
                    <div style="color:#9ca3af;font-size:11px;margin-top:6px;">
                        ${escapeHtml(age.label)}${cls.age_mixed ? ' (undifferentiated)' : ''}
                        ${lith ? ' &middot; ' + escapeHtml(lith.label) : ''}</div>
                    <div style="color:#777;font-size:10px;margin-top:3px;">
                        as printed: ${escapeHtml(p.group || '')} &middot; ${escapeHtml(cat.short || id)}${cat.year ? ', ' + cat.year : ''}</div>
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
                priority: -30,
                // A drape has no "other features here" worth zooming in for —
                // its 17 units are a legend, not a pile-up. See maptip.js.
                peers: false,
                tabLabel: 'Geology',
                tabColor: '#f59e0b',
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
        geopackage: () => gpkg,
        classes: classesOf,
        allClasses: allClasses,
        classOf: classOf,
        classColor: classColor,
        std: stdLegend,
        age: ageMeta,
        shared: () => shared,
        state: st,
        isOn: id => st(id).on,
        anyOn: () => order.some(id => st(id).on),

        // ---- the one switch the user sees -------------------------------
        //
        // "Geology" is one thing. Which SHEETS answer for a given place is
        // provenance, not a choice: a user in South Sudan cannot be expected
        // to know that the 2004 GRAS sheet is the one that covers them.
        // Turning it on turns on every sheet installed; the panel still lists
        // them, and a share link still names them, so nothing is lost.
        async setAll(want, opts) {
            opts = opts || {};
            await fetchMeta();
            const avail = order.filter(id => sheets[id] && sheets[id].available);
            if (want && !avail.length) {
                if (!opts.quiet) {
                    showToast('Geology unavailable', 'no sheets are built on this server',
                              null, null, 'warning');
                }
                return false;
            }
            for (const id of avail) await this.set(id, want, { quiet: true, fly: false });
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            return want && avail.length > 0;
        },
        toggleAll() { return this.setAll(!this.anyOn()); },

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
            // Auto opacity is evaluated at the moment the layer goes on, not
            // only when the basemap changes: switching to satellite and THEN
            // turning geology on would otherwise draw it at the dark
            // basemap's value, i.e. almost invisibly.
            if (want && shared.opacityAuto) shared.opacity = autoOpacity();
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

        // Opacity, colour mode and the lithology filter are legend-wide: two
        // sheets at 40% and 80% of one legend is not a picture anybody asked
        // for, and at a border it reads as a data difference.
        // Moving the slider is what leaves auto mode: an explicit value the
        // user chose must not be recomputed behind their back on the next
        // basemap switch.
        setOpacity(v, opts) {
            opts = opts || {};
            shared.opacity = Math.max(0, Math.min(1, v));
            if (!opts.auto) shared.opacityAuto = false;
            order.forEach(id => { if (st(id).on) refresh(id); });
            const lbl = document.getElementById('geomap-op');
            if (lbl) lbl.textContent = Math.round(shared.opacity * 100) + '%';
            if (!opts.quiet && typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },

        /** Back to "whatever this basemap needs". */
        setAutoOpacity(on) {
            shared.opacityAuto = !!on;
            if (shared.opacityAuto) this.setOpacity(autoOpacity(), { auto: true });
            else if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },
        autoOpacityOn: () => shared.opacityAuto,

        // Called by switchBasemap(). A drape tuned for the dark basemap is
        // nearly invisible over satellite imagery, so "auto" has to actually
        // re-pick rather than being a one-time default.
        basemapChanged() {
            if (!shared.opacityAuto) return;
            // quiet: the panel is re-rendered once below rather than from
            // inside setOpacity, which also runs on every drag of the slider.
            this.setOpacity(autoOpacity(), { auto: true, quiet: true });
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },

        setAdvancedOpen(on) { shared.advOpen = !!on; },
        advancedOpen: () => shared.advOpen,

        // 'age' = the ICS chart (two sheets agree, and a geologist reads it
        // without a legend); 'ink' = the sheet as printed, which is the right
        // answer when the question is about the SCAN. The printed ink is never
        // discarded, only not shown.
        setColorMode(mode) {
            shared.colorMode = mode === 'ink' ? 'ink' : 'age';
            refreshAll();
        },
        colorMode: () => shared.colorMode,

        // The FGDC ornament off is a legitimate ask (printing, a very dense
        // view), so it is a switch — but it defaults ON, because a flat
        // translucent fill on a dark basemap is what gets mistaken for water.
        setPattern(on) { shared.pattern = !!on; refreshAll(); },
        patternOn: () => shared.pattern,

        // "Show me the intrusives" — a legend-wide question, ANDed with
        // whatever commodity selection is running, never replacing it.
        toggleLith(key) {
            if (shared.liths.has(key)) shared.liths.delete(key);
            else shared.liths.add(key);
            refreshAll();
        },
        lithOn: key => shared.liths.has(key),
        clearLiths() { shared.liths.clear(); refreshAll(); },

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
            shared.liths.clear();
            refresh(id);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },

        /** Clear every filter on every sheet — the panel has one "show all". */
        showEverything() {
            order.forEach(id => {
                const o = st(id);
                o.hidden = new Set(); o.isolate = null; o.commodities = new Set();
            });
            shared.liths.clear();
            refreshAll();
        },

        /** Is anything filtered out right now, on any sheet? */
        anyFiltered() {
            if (shared.liths.size) return true;
            return order.some(id => {
                const o = st(id);
                return o.hidden.size > 0 || (o.isolate && o.isolate.size);
            });
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
            // With the layer off there is no overlay to describe — except the
            // panel's own disclosure, which is a setting the sender may well
            // be pointing at ("open the unit list"). Nothing else survives:
            // an opacity or a lithology filter for a layer that is not drawn
            // would restore invisibly and then surprise whoever switched it on.
            if (!on.length) return shared.advOpen ? { geomap_adv: '1' } : null;
            const p = { geomap: on.join(',') };
            const only = [], hide = [], host = [];
            on.forEach(id => {
                const o = st(id);
                // A commodity selection travels as the commodities, not as the
                // codes they expand to: a rebuild can merge or rename units,
                // and "everything that can host gold" is still answerable
                // afterwards while a frozen code list is not.
                if (o.commodities.size) host.push(id + ':' + [...o.commodities].join('|'));
                else if (o.isolate && o.isolate.size) only.push(id + ':' + [...o.isolate].join('|'));
                else if (o.hidden.size) hide.push(id + ':' + [...o.hidden].join('|'));
            });
            if (host.length) p.geomap_host = host.join(',');
            if (only.length) p.geomap_only = only.join(',');
            if (hide.length) p.geomap_hide = hide.join(',');
            // Legend-wide state, so no sheet prefix. Each is omitted at its
            // default, so a plain "here is the geology" link stays short.
            // A number means "the user chose this"; auto is the default and
            // is therefore the absence of the parameter. Carrying the computed
            // number instead would freeze one basemap's value into a link that
            // may well be opened on another.
            if (!shared.opacityAuto) p.geomap_opacity = String(Math.round(shared.opacity * 100));
            if (shared.colorMode !== 'age') p.geomap_color = shared.colorMode;
            if (!shared.pattern) p.geomap_pattern = '0';
            if (shared.liths.size) p.geomap_lith = [...shared.liths].join('|');
            if (shared.advOpen) p.geomap_adv = '1';
            return p;
        },

        async restoreFromParams(params) {
            // The Advanced disclosure is a panel setting, not a layer setting,
            // so it is honoured even in a link that does not turn geology on
            // (?panel=admin&admin_tab=map-settings&geomap_adv=1 — "look at the
            // unit list"). Everything below it needs the catalogue and a
            // layer, so it stays behind the early return.
            if (params.get('geomap_adv') === '1') shared.advOpen = true;
            const want = (params.get('geomap') || '').split(',').filter(Boolean);
            if (!want.length) return;
            await fetchMeta();
            const parse = (raw, fn) => (raw || '').split(',').filter(Boolean).forEach(part => {
                const i = part.indexOf(':');
                if (i > 0) fn(part.slice(0, i), part.slice(i + 1));
            });
            // Opacity is legend-wide now, but an OLD link carries the
            // per-sheet form ("car:80"). Accept both: a bare number is the
            // new one, and a prefixed list takes the first sheet's value
            // rather than dropping the user's setting on the floor.
            const opRaw = params.get('geomap_opacity') || '';
            if (opRaw === 'auto') {
                shared.opacityAuto = true;
                shared.opacity = autoOpacity();
            } else if (opRaw) {
                const first = opRaw.split(',')[0];
                const n = parseInt(first.indexOf(':') > 0 ? first.split(':')[1] : first, 10);
                if (!isNaN(n)) {
                    shared.opacity = Math.max(0, Math.min(1, n / 100));
                    shared.opacityAuto = false;
                }
            } else {
                // No parameter = auto, evaluated against the basemap this link
                // actually opens on (which restoreFromParams runs after).
                shared.opacity = autoOpacity();
            }
            if (params.get('geomap_color') === 'ink') shared.colorMode = 'ink';
            if (params.get('geomap_pattern') === '0') shared.pattern = false;
            const lithRaw = params.get('geomap_lith') || '';
            if (lithRaw) {
                const known = new Set((stdLegend().lithology || []).map(l => l.key));
                const keys = lithRaw.split('|').filter(k => known.has(k));
                // Same rule as an out-of-date code list: a filter that matches
                // nothing renders an empty map, which is indistinguishable
                // from "no data here". Drop it and say so.
                if (keys.length) shared.liths = new Set(keys);
                else if (typeof showToast === 'function') {
                    showToast('Geology selection is out of date',
                        'That link names rock types this legend no longer has \u2014 showing all of them.',
                        null, null, 'warning');
                }
            }
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
