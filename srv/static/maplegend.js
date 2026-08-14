/*
 * maplegend.js — "Map" section of the stats panel: what the ground under the
 * data currently is, and one way to change it.
 *
 * WHY IT EXISTS
 *
 * Basemap and the two drapes (historical scans, geology) were reachable only
 * from admin ▸ Map Settings. That is four clicks from the map, and — worse —
 * once a drape was on, NOTHING on the map said so. A hatched country-sized
 * polygon with no legend is a rendering the user has to reverse-engineer, and
 * a geology layer FILTERED to "everything that can host gold" looks exactly
 * like a complete geology layer. Same shape as the app's standing rule that a
 * truncated answer must announce itself: a filtered drape must say it is
 * filtered.
 *
 * WHY IT IS (ALMOST) NOT THERE
 *
 * The default state — dark basemap, no overlay — has nothing to report, and a
 * permanent "Basemap: Dark" row would be a line of chrome that is wrong to
 * read. So in the default state this section collapses to ONE ghost icon: no
 * header, no divider, no label. It is still a real target (the only direct
 * route to basemap/overlay switching from the map), it just does not claim to
 * be information. The moment anything is non-default the section grows a
 * header and the chips that describe it.
 *
 * ONE MENU, NO SECOND VOCABULARY
 *
 * The menu is the app's existing `.aoi-menu.mode-menu` component (radios for
 * one-of, checks for any-of, a `refused` row that keeps its place and says
 * why on hover). A chip in the strip is the state; tapping a chip turns that
 * one thing OFF, tapping the opener configures. Two targets, each with one
 * meaning, both ≥28 px on touch.
 *
 * THE LEGEND IS THE STRIP, NOT A KEY
 *
 * Geology on: a row of age swatches (ICS colour wearing its FGDC ornament)
 * for the periods actually drawn — derived from the catalogue, never a fixed
 * list, and capped with a "+n" rather than growing without bound. The full
 * legend stays in Map Settings; this is the minimum that makes the drape
 * readable at a glance and reachable in one tap.
 */
(function () {
    'use strict';

    var BASEMAPS = [
        ['dark',              'Dark',      'icon-moon',      'The default cartographic basemap'],
        ['satellite-esri',    'ESRI',      'icon-satellite', 'Esri World Imagery'],
        ['satellite-bing',    'Bing',      'icon-satellite', 'Bing-style imagery'],
        ['satellite-google',  'Google',    'icon-satellite', 'Google satellite imagery']
    ];

    function bm() { return (typeof currentBasemap === 'string') ? currentBasemap : 'dark'; }
    function bmEntry(id) {
        for (var i = 0; i < BASEMAPS.length; i++) if (BASEMAPS[i][0] === id) return BASEMAPS[i];
        return BASEMAPS[0];
    }
    function esc(s) {
        return (typeof escapeHtml === 'function') ? escapeHtml(String(s == null ? '' : s))
            : String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
                return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
            });
    }

    /* The catalogue's 1-3 affinity grade in words. One definition for the
     * matrix, the floor and the tips: three wordings for one scale is how a
     * reader concludes they are three scales. */
    var WEIGHT_WHY = {
        1: 'a weak or derived association \u2014 placer ground downstream of a lode, or a unit that merely sits inside the belt',
        2: 'a plausible host',
        3: 'the classic host rock'
    };

    function histOn() { return typeof HistMap !== 'undefined' && HistMap.isOn(); }
    function histMeta() { return (typeof HistMap !== 'undefined' && HistMap.meta()) || null; }
    function geoOn() { return typeof GeoMap !== 'undefined' && GeoMap.anyOn(); }

    /* The scanned series covers 8 of 22 blocks of one country, so "on, but not
     * here" is the ordinary case — the same trap as the geology chip's
     * `not in view`: a chip with nothing drawn beside it reads as a broken
     * layer. Measured off the archive's own envelope (the same `bounds` the
     * source is clamped to), not off the canvas: a raster has no queryable
     * features, and an envelope is the honest statement of where sheets exist.
     * Coarser than the geology answer and deliberately so — inside the
     * envelope a given sheet cell may still be blank, which is why the words
     * are about the view, not about a pixel. */
    function histOffView() {
        if (!histOn()) return false;
        var m = histMeta();
        var bb = m && m.bounds;
        if (!bb || bb.length !== 4) return false;
        if (typeof bboxOnScreen !== 'function') return false;
        return !bboxOnScreen(bb);
    }

    /* What the geology layer is currently NOT showing, in words. A filtered
     * drape that reads as a complete one is the failure this line prevents. */
    function geoFilterNote(said) {
        if (typeof GeoMap === 'undefined' || !GeoMap.anyOn()) return { short: '', full: '' };
        var alreadySaid = {};
        (said || []).forEach(function (k) { alreadySaid[k] = 1; });
        var sh = GeoMap.shared ? GeoMap.shared() : null;
        var comms = new Set(), hidden = 0, isolated = false;
        (GeoMap.order() || []).forEach(function (id) {
            var s = GeoMap.state(id);
            if (!s || !s.on) return;
            if (s.commodities && s.commodities.forEach) s.commodities.forEach(function (c) { comms.add(c); });
            if (s.isolate && s.isolate.size) isolated = true;
            if (s.hidden && s.hidden.size) hidden += s.hidden.size;
        });
        // A floor is part of what the drape IS, so the chip carries it: "gold
        // hosts" and "classic gold hosts" are different maps.
        var min = (typeof GeoMap.minWeight === 'function') ? GeoMap.minWeight() : 1;
        var grade = min === 3 ? 'classic ' : (min === 2 ? 'likely ' : '');

        // ── ONE CLAUSE PER THING, AND NEVER THE SAME THING TWICE ────────
        //
        // The note used to be assembled by hand at four return points, each
        // gluing the contact clause on with its own `join()`. The result read
        // "copper hosts, + graded contacts" — a stray plus, a comma splice,
        // and with a commodity picked the word "copper" was liable to appear
        // in the chip AND in the bracket label under it. It is a LIST now:
        // clauses are pushed in order of what they change about the map, the
        // list is deduplicated, and the chip prints the first one with a "+n"
        // for the rest rather than a sentence that no longer fits.
        var parts = [];
        // A commodity the key strip already brackets by name is dropped here:
        // the bracket is the better statement of it (it points at the exact
        // swatches and carries its own undo), so the chip carries whatever the
        // bracket cannot say — the grade floor, the hidden periods, the
        // contacts. With every selected commodity bracketed, the chip falls
        // back to the grade alone, which is precisely the part the strip omits.
        // Cells first: a pick is the most specific thing the reader did, and
        // it is the one narrowing no bracket in the strip can express.
        var picked = (GeoMap.picks && GeoMap.picks()) || new Set();
        if (picked.size) parts.push(cellWords(picked));
        var named = Array.from(comms).filter(function (k) { return !alreadySaid[k]; });
        if (named.length) parts.push(grade + named.join(' + ') + ' hosts');
        else if (comms.size && grade) parts.push(grade.trim() + ' grade only');
        // Every selected commodity is already bracketed by name in the strip
        // below, and the floor is the default: there is nothing left for the
        // chip to add. It says nothing rather than something vague — "some
        // units" was a worse answer than the bracket that is already there.
        else if (sh && sh.liths && sh.liths.size) {
            parts.push(sh.liths.size + ' rock type' + (sh.liths.size === 1 ? '' : 's'));
        }
        // A hand-built isolation has no better name, so it says how many. It
        // is only reached with NO commodity selected: an isolation derived
        // from one is that commodity, and naming it twice is the repetition
        // this note exists to avoid.
        if (!comms.size && isolated) parts.push('picked units');
        else if (!comms.size && hidden) {
            parts.push(hidden + ' unit' + (hidden === 1 ? '' : 's') + ' hidden');
        }
        var nOff = (sh && sh.agesOff) ? sh.agesOff.size : 0;
        if (nOff) parts.push(nOff + ' period' + (nOff === 1 ? '' : 's') + ' hidden');
        // The contact layer is a second thing on screen and is itself a subset,
        // so the chip names it — a reader who sees orange hairlines and no word
        // for them has to guess whether that layer is complete. Its own
        // narrowing (a picked junction) is said HERE and nowhere else, so the
        // panel's state line and the chip cannot word it differently.
        if (typeof GeoMap.contactsOn === 'function' && GeoMap.contactsOn()) {
            var pick = (GeoMap.contactPairs && GeoMap.contactPairs()) || new Set();
            parts.push(pick.size ? junctionsWords(pick) + ' contacts'
                : (GeoMap.contactsGradedOnly && GeoMap.contactsGradedOnly()
                    ? 'graded contacts' : 'every contact'));
        }
        var seen = {}, uniq = [];
        parts.forEach(function (p) { if (p && !seen[p]) { seen[p] = 1; uniq.push(p); } });
        if (!uniq.length) return { short: '', full: '' };
        return {
            // The chip has room for one clause. "+2" is a truncation that says
            // it is one; the whole list is in the tooltip and in the panel.
            short: uniq[0] + (uniq.length > 1 ? ' +' + (uniq.length - 1) : ''),
            full: uniq.join(', ')
        };
    }

    /* ── How much of the view each age actually covers ──────────────────
     *
     * The key used to be ordered by ICS rank (young to old) and capped at 8.
     * That is the order a printed chart uses, but this is not a chart: it is a
     * key to ONE view, and ranking by age means the "+n" drops whatever
     * periods happen to be old — which over the CAR basement or the Tanzanian
     * craton is most of what is on screen. The reader is then shown eight
     * colours that are not the eight colours in front of them.
     *
     * So the order is coverage, largest first, and it is MEASURED rather than
     * assumed. Two things the catalogue's `area_km2` cannot know: what is
     * inside the viewport (a sheet is mostly off-screen at any working zoom),
     * and what is on top (units overlap where two sheets meet). Sampling the
     * rendered map answers both at once — it counts painted pixels, which is
     * what "visible" means to the person reading the key.
     *
     * A grid of ~200 point queries costs a few ms and runs on `idle`, never
     * per frame. It is a SAMPLE, so it is used to order and to say roughly how
     * much; it is never printed as an area.
     */
    var coverage = null;   // {byAge:{key:hits}, lith:{key:lith}, hits, samples} | null
    // Contact features painted in this viewport, measured alongside coverage.
    // Its own variable, not a field of `coverage`, because it has to survive
    // the paths where there is no fill to measure at all.
    var contactHits = 0;
    // Which layers the numbers above were measured from, and whether the
    // contact number is trustworthy yet. See reMeasure().
    var measuredSig = '';
    var contactsPending = false;

    function geoLayerSig() {
        return geoFillLayers().join(',') + '|' + geoContactLayers().join(',');
    }

    function geoFillLayers() {
        if (typeof GeoMap === 'undefined' || typeof map === 'undefined' || !map) return [];
        return (GeoMap.order() || [])
            .filter(function (id) { var s = GeoMap.state(id); return s && s.on; })
            .map(function (id) { return 'geomap-fill-' + id; })
            .filter(function (l) { try { return !!map.getLayer(l); } catch (e) { return false; } });
    }

    /* The contact layers on screen. Separate from the fills because a contact
     * is a different KIND of thing (an edge, not a ground) and is counted and
     * keyed separately — but it is still geology being drawn, which is the
     * only question geoOffView() asks. */
    function geoContactLayers() {
        if (typeof GeoMap === 'undefined' || typeof map === 'undefined' || !map) return [];
        if (!(GeoMap.contactsOn && GeoMap.contactsOn())) return [];
        return (GeoMap.order() || [])
            .filter(function (id) { var s = GeoMap.state(id); return s && s.on; })
            .map(function (id) { return 'geomap-contact-' + id; })
            .filter(function (l) { try { return !!map.getLayer(l); } catch (e) { return false; } });
    }

    /* How many contact features are actually painted in this viewport.
     *
     * NOT the same number as GeoMap.drawnContactCount(), which counts PAIR
     * TYPES passing the filter (a property of the selection, true anywhere).
     * This one is about the picture: it is what decides whether the layer has
     * anything to say about the place on screen. */
    function contactsInView() {
        var layers = geoContactLayers();
        if (!layers.length) return 0;
        try { return (map.queryRenderedFeatures({ layers: layers }) || []).length; }
        catch (e) { return 0; }
    }

    /* ── MEASURING TWICE PER TICK IS MEASURING ONCE, SLOWLY ─────────────
     *
     * measureCoverage() is ~120 ms with three sheets on: one unfiltered
     * queryRenderedFeatures over the contact layers (~40 ms at 452 lines),
     * one over the fills, and a ~200-point grid. render() calls it, and
     * render() is called from six places — a single gesture (a junction pick:
     * setFilter, refreshWhenDrawn, render, rebuildGeoMenuNow, reMeasure)
     * therefore paid for it three or four times over, all on the same
     * unchanged canvas, all on the main thread, all between the reader's
     * click and the panel redrawing.
     *
     * So the measurement is memoised on WHAT IT IS A MEASUREMENT OF: the
     * viewport, the layer set, the filters those layers carry, and a nonce
     * the map bumps whenever it paints or a tile lands. Anything that could
     * change the picture changes the key; nothing else does. This is a cache
     * over a pure read of the canvas, not a staleness bet — a caller that
     * needs a genuinely fresh read (reMeasure, after a layer was added) says
     * so with `force`.
     */
    var measureKey = '';        // what `coverage`/`contactHits` describe
    var paintNonce = 0;         // bumped by the map: idle, tiles, style

    function bumpPaint() { paintNonce++; }

    /* ── THE MAP'S OWN WORD FOR "I HAVE DRAWN THIS" ────────────────────
     *
     * `paintNonce` counts EVENTS THAT COULD CHANGE THE PICTURE — a tile
     * arriving, a style settling — and that is the wrong question for
     * "has this layer had its chance to paint?". A `sourcedata` fires while
     * the frame is still ahead of us; a zero that "survived a paintNonce
     * bump" may have survived nothing but a network event. That is why the
     * reverted attempt (see contactsPending) did not clear the label.
     *
     * `idle` is different in kind: MapLibre only says it after it has
     * finished rendering everything it can. So idles are counted separately,
     * and an unproven zero becomes a proven one only when the map has gone
     * idle AT LEAST ONCE SINCE THE LAYER SET CHANGED. */
    var idleNonce = 0;
    var contactSig = '';        // the contact layers this settle-state is about
    var contactIdleAt = -1;     // idleNonce when that set was first seen

    function bumpIdle() { idleNonce++; }

    /* Has the contact layer been given a frame to paint in? Two conditions,
     * both necessary and neither sufficient:
     *
     *  - the map has gone idle since these layers appeared (a layer added
     *    this tick can reach `map.loaded()` before rendering anything — the
     *    failure reMeasure() was written for), and
     *  - the tiles the layers read from are in (`isSourceLoaded`), or a zero
     *    is just "the vector tile has not landed".
     *
     * When both hold, `0` is a MEASUREMENT: there are no contact lines in
     * this viewport, and the bar is free to say so. */
    function contactsSettled(layers) {
        if (typeof map === 'undefined' || !map) return false;
        if (contactIdleAt < 0 || idleNonce <= contactIdleAt) return false;
        try { if (map.loaded && !map.loaded()) return false; } catch (e) { return false; }
        for (var i = 0; i < layers.length; i++) {
            var src;
            try { src = (map.getLayer(layers[i]) || {}).source; } catch (e) { return false; }
            if (!src) return false;
            try { if (!map.isSourceLoaded(src)) return false; } catch (e) { return false; }
        }
        return true;
    }

    /* The filters are part of the picture: a commodity chip changes what is
     * painted without moving the map or loading a tile, and a memo keyed only
     * on the viewport would then hand back the previous selection's counts. */
    function geoFilterSig() {
        if (typeof map === 'undefined' || !map) return '';
        var out = '';
        geoFillLayers().concat(geoContactLayers()).forEach(function (l) {
            try { out += l + '=' + JSON.stringify(map.getFilter(l)) + ';'; }
            catch (e) { out += l + '=?;'; }
        });
        return out;
    }

    function geoViewKey() {
        if (typeof map === 'undefined' || !map) return '';
        var c = map.getCenter(), cv = map.getCanvas();
        return map.getZoom().toFixed(3) + ',' + c.lng.toFixed(5) + ',' + c.lat.toFixed(5) +
            ',' + map.getBearing().toFixed(1) + ',' + map.getPitch().toFixed(1) +
            ',' + (cv.clientWidth || 0) + 'x' + (cv.clientHeight || 0) +
            '|' + geoLayerSig() + '|' + geoFilterSig() + '|' + paintNonce;
    }

    /* ── WHAT THE CARD IS AN ANSWER TO ─────────────────────────────────
     *
     * The mixer's two tables are built from the CANVAS: their columns are the
     * periods actually drawn, the tabs count what is painted, and the state
     * line describes the current view. So the mixer is not a function of
     * GeoMap's state alone — it is a function of state AND of what MapLibre has
     * finished drawing, and the second half changes without anybody touching a
     * control (a tile lands, the style settles, the reader pans).
     *
     * The floating panel already handles that: watchMap's idle handler
     * re-measures, syncBar restates the counts and a changed skill scope
     * rebuilds it. The admin card had no such path, and its symptom was
     * precise: opening `?panel=admin&admin_tab=map-settings` on a cold load
     * painted the card BEFORE the first tile arrived, so it said "No mapped
     * sheet reaches this view" over a map that was drawing three countries —
     * and it stayed wrong, because nothing after that was a state change.
     * Same failure as the frozen "counting lines…" bar, one surface over.
     *
     * This is the signature of the measurement the card is built from. Cheap
     * (no queryRenderedFeatures of its own: it reads what the idle pass just
     * measured), and it deliberately does NOT include the viewport, so panning
     * inside one country does not rebuild a card the reader is working in. */
    function geoAnswerSig() {
        var cols = columnEntries().map(function (e) { return e.key; }).join(',');
        return measuredSig + '|' + contactHits + '|' + cols + '|' +
            (typeof GeoMap !== 'undefined' && GeoMap.skillScope ? GeoMap.skillScope() : '');
    }

    function measureCoverage(force) {
        var key = geoViewKey();
        if (!force && key === measureKey && measureKey !== '') return;
        measureKey = key;
        // Measured first and kept outside `coverage`, because every early
        // return below sets `coverage = null` and the contact layer can be
        // the ONLY geology on screen (see geoOffView).
        /* A layer that EXISTS but has painted nothing yet answers 0 to
         * queryRenderedFeatures, which is indistinguishable from "there is
         * nothing here". That zero is not a measurement UNTIL THE LAYER HAS
         * HAD ITS FRAME, so it is flagged and re-taken (reMeasure below).
         *
         * `contactHits === 0` alone was one flag for TWO STATES — "unproven"
         * and "measured, none" — so a genuine zero stayed pending forever and
         * the bar said `counting lines…` indefinitely (invisible at z3 over
         * three sheets, where some contact is always in view; reproducible at
         * z10.5 over one sheet, where 71 line TYPES pass the filter and none
         * of their geometry reaches a ~50 km viewport). The second state is
         * now decided by contactsSettled(): the map has gone idle since these
         * layers appeared AND their sources are loaded. Then "no lines here"
         * is a reading of the canvas, not a guess about paint latency, and
         * the bar can say it. */
        var cLayers = geoContactLayers();
        var cSig = cLayers.join(',');
        if (cSig !== contactSig) {
            // A NEW SET OF LAYERS RESETS THE CLOCK. This is the moment the
            // layer was added (or the reader switched contacts off and on),
            // and nothing measured before the next idle is about it.
            contactSig = cSig;
            contactIdleAt = cSig ? idleNonce : -1;
        }
        contactHits = contactsInView();
        measuredSig = geoLayerSig();
        contactsPending = contactHits === 0 && cLayers.length > 0 &&
            !contactsSettled(cLayers);
        var layers = geoFillLayers();
        if (!layers.length) { coverage = null; return; }
        var cv = map.getCanvas();
        var w = cv.clientWidth || cv.width, h = cv.clientHeight || cv.height;
        if (!w || !h) { coverage = null; return; }

        // ── WHAT IS DRAWN, exhaustively ──────────────────────────────────
        //
        // The key used to be BUILT from the sample, and a sample is a corner,
        // not an inventory: a formation narrower than the ~40 px grid spacing
        // was painted on the map and absent from the legend beside it — the
        // reader sees an orange belt with no orange swatch and cannot switch
        // it off, which is the app's standing "a truncated answer must
        // announce itself" failure in its purest form.
        //
        // So the SET comes from an unfiltered queryRenderedFeatures over the
        // whole viewport, which returns every feature actually rendered, and
        // the grid sample is demoted to what it can honestly do: say how much
        // of the view each one covers, for ORDER and for the tooltip.
        var present = {}, lithHits = {};
        try {
            (map.queryRenderedFeatures({ layers: layers }) || []).forEach(function (f) {
                var sheet = String((f.layer && f.layer.id) || '').replace('geomap-fill-', '');
                var cls = GeoMap.classOf(sheet, (f.properties || {}).code);
                if (!cls) return;
                var age = cls.age || 'unknown';
                present[age] = (present[age] || 0) + 1;
                var lk = age + '|' + (cls.lith || 'mixed');
                lithHits[lk] = (lithHits[lk] || 0) + 1;
            });
        } catch (e) { coverage = null; return; }

        // ── HOW MUCH of the view each covers ─────────────────────────────
        //
        // ~200 point queries, which is cheap on `idle` and wasteful per frame.
        // Sampling error can move a swatch one place at the tail; it cannot
        // move one at the head, which is the part of the order anybody reads.
        // It can no longer DROP one, which is the part that mattered.
        var step = Math.max(24, Math.ceil(Math.sqrt((w * h) / 200)));
        var byAge = {}, hits = 0, samples = 0;
        for (var y = step / 2; y < h; y += step) {
            for (var x = step / 2; x < w; x += step) {
                samples++;
                var f;
                try { f = map.queryRenderedFeatures([x, y], { layers: layers })[0]; }
                catch (e2) { break; }
                if (!f) continue;
                var sh2 = String((f.layer && f.layer.id) || '').replace('geomap-fill-', '');
                var c2 = GeoMap.classOf(sh2, (f.properties || {}).code);
                if (!c2) continue;
                byAge[c2.age || 'unknown'] = (byAge[c2.age || 'unknown'] || 0) + 1;
                hits++;
                // The ornament shown for an age is the lithology covering most
                // of the view under it, weighted by the sample where we have
                // one — falling back to the feature count for a unit too thin
                // to be sampled at all.
                var lk2 = (c2.age || 'unknown') + '|' + (c2.lith || 'mixed');
                lithHits[lk2] = (lithHits[lk2] || 0) + 200;
            }
        }
        // Every age that is drawn gets an entry, sampled or not. A zero here
        // means "too thin for the sample", never "not on the map" — the
        // difference the old code could not express.
        Object.keys(present).forEach(function (a) { if (!byAge[a]) byAge[a] = 0; });

        var lith = {};
        Object.keys(lithHits).forEach(function (k) {
            var i = k.indexOf('|'), age = k.slice(0, i), l = k.slice(i + 1);
            if (!lith[age] || lithHits[k] > lithHits[age + '|' + lith[age]]) lith[age] = l;
        });
        coverage = { byAge: byAge, lith: lith, hits: hits, samples: samples,
                     drawn: Object.keys(present).length };
    }

    /* ── WHAT THE READER COULD PICK, NOT WHAT THEY ALREADY DID ─────────
     *
     * The matrix's columns were the periods RENDERED, and its cells narrow
     * the render — so picking one collapsed the table to that one column and
     * took every other cell off the screen with it. A table you cannot make a
     * second choice in is not a table, and the reader who wanted "the cobalt
     * Archaean AND the copper Palaeoproterozoic" could not express the second
     * half of that sentence.
     *
     * So the TABLE is keyed on what the sheets have HERE, independent of the
     * selection, while the KEY STRIP stays keyed on what is painted (its job
     * is to be a legend for the picture). The filtering happens on the map,
     * not on the surface that offers the filters.
     *
     * Measured with querySourceFeatures, which reads the loaded tiles and so
     * ignores every layer filter. That is a superset of the viewport (tiles
     * overhang it), which is the right side to err on for a menu of what
     * could be picked — and it is never printed as a count or an area.
     */
    var srcAges = null;         // {age: lith} present in the loaded tiles | null

    function measureSourceAges() {
        if (typeof GeoMap === 'undefined' || typeof map === 'undefined' || !map) {
            srcAges = null; return;
        }
        var out = {};
        (GeoMap.order() || []).forEach(function (id) {
            var s = GeoMap.state(id);
            if (!s || !s.on) return;
            var src = 'geomap-src-' + id;
            try { if (!map.getSource(src)) return; } catch (e) { return; }
            var feats;
            try { feats = map.querySourceFeatures(src, { sourceLayer: 'units' }) || []; }
            catch (e2) { return; }
            feats.forEach(function (f) {
                var cls = GeoMap.classOf(id, (f.properties || {}).code);
                if (!cls) return;
                if (!out[cls.age || 'unknown']) out[cls.age || 'unknown'] = cls.lith || 'mixed';
            });
        });
        srcAges = out;
    }

    /* The periods the table offers: everything the sheets have in view, drawn
     * or filtered out, ordered by how much of the view each covers (the ones
     * currently filtered out have no coverage, so they follow, oldest first). */
    function columnEntries() {
        var drawn = ageEntries().filter(function (e) { return e.on; });
        if (!srcAges) return drawn;
        var have = {};
        drawn.forEach(function (e) { have[e.key] = true; });
        var rest = Object.keys(srcAges).filter(function (k) { return !have[k]; })
            .map(function (k) {
                return { key: k, hits: 0, meta: GeoMap.age(k), lith: srcAges[k] || 'mixed',
                         on: true, offSelection: true, pct: 0 };
            });
        rest.sort(function (a, b) { return (a.meta.rank || 99) - (b.meta.rank || 99); });
        return drawn.concat(rest);
    }

    /* ── THE TABLE MUST HOLD STILL WHILE IT IS BEING USED ─────────────
     *
     * Picking three cells of the gold row is one gesture in three taps, and it
     * only works if the third cell is still where the reader saw it after the
     * first two. Everything in the layout wanted to move under them: columns
     * are ordered by how much of the view each period covers, and a pick
     * changes what is painted; rows are ordered by how strongly this view
     * answers each commodity, and the grade floor changes that. So the table
     * re-sorted between taps and the reader hit a cell that had swapped with
     * its neighbour — which reads as the app choosing for them.
     *
     * The ORDER is therefore frozen for as long as the reader is working in
     * one view, and only the cells' STATE is live. It is recomputed when the
     * subject actually changes — the map moved, a sheet came or went — and
     * not when the selection does, because the selection is what the reader is
     * building out of this table.
     *
     * Frozen as KEYS, never as rendered entries: a stale entry would carry a
     * stale coverage number and a stale swatch into a view it is not from.
     */
    var mxFrozen = null;         // {stamp, cols:[ageKey], rows:[commodity]}

    /* Working = the reader is mid-gesture in this table. The order holds
     * still while this is true, and settles — visibly — when the gesture is
     * over. "Over" is not a timer: it is the reader moving on (the other tab,
     * the map, anything outside the panel), which is exactly when a reorder
     * stops being a surface moving under their thumb and becomes an answer.
     * A timeout is the backstop for a reader who simply stopped, and it is
     * long enough (4 s) not to fire between two taps of one gesture. */
    var mxWorking = false;
    var mxSettleTimer = null;

    function mxTouch() {
        mxWorking = true;
        clearTimeout(mxSettleTimer);
        mxSettleTimer = setTimeout(function () { MapLegend.mxSettle(); }, 4000);
    }

    function mxStamp() {
        var sheets = (typeof GeoMap !== 'undefined' && GeoMap.order)
            ? (GeoMap.order() || []).filter(function (id) {
                var st = GeoMap.state(id); return st && st.on;
              }).join(',') : '';
        if (typeof map === 'undefined' || !map || !map.getCenter) return sheets;
        var c = map.getCenter();
        // Coarse on purpose: nudging the map by a pixel is not a new subject,
        // and re-sorting the table under a reader who is panning to see what
        // they just picked is the same failure by another route.
        return sheets + '|' + map.getZoom().toFixed(1) + '|' +
            c.lng.toFixed(1) + '|' + c.lat.toFixed(1);
    }

    /* The frozen order, recomputed only when the view is a different one.
     * `liveCols` and `liveRows` are this render's answer; the frozen list is
     * intersected with them, so a period that has genuinely left the view
     * drops out rather than being drawn from a stale swatch. */
    function mxOrder(liveCols, liveRows) {
        var stamp = mxStamp();
        var have = {};
        liveCols.forEach(function (k) { have[k] = true; });
        var haveRow = {};
        liveRows.forEach(function (k) { haveRow[k] = true; });
        // A NEW VIEW IS A NEW SUBJECT, but not while the reader is mid-
        // gesture: refreshWhenDrawn() runs on the map's `idle`, and a pick
        // that changes what is painted can move the centre by a hair. Re-
        // sorting then would be the very reshuffle this exists to prevent.
        if (!mxFrozen || (mxFrozen.stamp !== stamp && !mxWorking)) {
            mxFrozen = { stamp: stamp, cols: liveCols.slice(), rows: liveRows.slice() };
            return mxFrozen;
        }
        // Same view, so keep the order the reader is looking at: what is still
        // there stays put, and anything new (a tile that finished loading)
        // goes on the end rather than displacing what they are aiming at.
        var cols = mxFrozen.cols.filter(function (k) { return have[k]; });
        liveCols.forEach(function (k) { if (cols.indexOf(k) < 0) cols.push(k); });
        var rows = mxFrozen.rows.filter(function (k) { return haveRow[k]; });
        liveRows.forEach(function (k) { if (rows.indexOf(k) < 0) rows.push(k); });
        mxFrozen.cols = cols;
        mxFrozen.rows = rows;
        return mxFrozen;
    }

    /* The junction table's axis, held still the same way and for the same
     * reason. One frozen record for both tables: they are two views of one
     * object, the reader moves between them, and a settle must settle both or
     * the tab switch becomes the thing that reshuffles. */
    function jxOrder(live) {
        var stamp = mxStamp();
        if (!mxFrozen || (mxFrozen.stamp !== stamp && !mxWorking)) {
            mxFrozen = { stamp: stamp, cols: [], rows: [], liths: live.slice() };
            return mxFrozen.liths;
        }
        if (!mxFrozen.liths) { mxFrozen.liths = live.slice(); return mxFrozen.liths; }
        var have = {};
        live.forEach(function (k) { have[k] = true; });
        var out = mxFrozen.liths.filter(function (k) { return have[k]; });
        live.forEach(function (k) { if (out.indexOf(k) < 0) out.push(k); });
        mxFrozen.liths = out;
        return out;
    }

    /* The layer is on but none of it is in this view — the ordinary case for
     * most of Africa, since three sheets cover three countries. A chip with no
     * key beside it otherwise reads as a broken layer, so the strip says so.
     * "Nothing drawn", not "nothing sampled": a single thin unit in the corner
     * is in view, and saying otherwise would be wrong in the same direction as
     * the missing swatch.
     *
     * CONTACTS COUNT AS DRAWN. They used not to, and the result was the exact
     * failure this function exists to prevent, one layer over: with the units
     * filtered down to nothing here but the junction lines painting orange
     * across the whole screen, the chip said "not in view" and the panel bar
     * said it too — over a map visibly drawing 67 of them. A label that
     * contradicts the canvas is worse than no label. So the question is "is
     * ANY geology painted here", and the fills and the edges each answer for
     * themselves below (drawnKinds).
     */
    function geoOffView() {
        if (!geoOn()) return false;
        if (contactHits > 0) return false;
        // An unproven zero is not an absence: the contact layers are on the
        // map and have not painted yet, so "not in view" would contradict the
        // canvas a frame later. See reMeasure().
        if (contactsPending) return false;
        return !!(coverage && !coverage.drawn);
    }

    /* Which KINDS of geology are painted here — the two are counted
     * separately because they are different units (a ground and an edge), the
     * standing rule that a number must name its unit. */
    function drawnKinds() {
        return { units: !!(coverage && coverage.drawn), contacts: contactHits > 0 };
    }

    /* ── The key, made operable ──────────────────────────────────────────
     *
     * The ages actually painted right now, most of the view first — and each
     * one a switch. A key you cannot act on is a picture of a key: the reader
     * looking at eight periods over the Chinko basement wants to drop the two
     * that cover everything and see what is underneath, and that question had
     * no answer anywhere in the app (the panel's age rows were `.static`).
     *
     * Two things this must not do:
     *
     *  - LOSE A HIDDEN AGE. Coverage is measured from RENDERED features, so
     *    the moment a period is hidden it stops being sampled and its swatch
     *    would vanish — taking the only way back with it. Hidden ages are
     *    therefore appended from state, after the measured ones, struck out
     *    and WITHOUT a coverage claim (we no longer know one; printing the
     *    last measured share would be a number about a picture that is no
     *    longer on screen).
     *  - CLAIM COMPLETENESS. "+n" is still a truncation, so it expands
     *    rather than only pointing at Map settings.
     */
    // How many periods the key shows before it collapses the rest behind "+n".
    // Ten, not eight: the strip spans the panel and the row was ending well
    // short of it, so the cap was throwing away swatches for space that was
    // there. Ten still fits a 360 px phone (26 px targets, wrapping to two
    // rows at worst) and covers every view we have measured except the widest
    // three-sheet zoom-outs, where "+n" is the honest answer anyway.
    var MAX = 10;
    var expanded = false;      // "+n" opened: show every age, not just the head
    var swTimers = [];
    function cancelSwTimers() { swTimers.forEach(clearTimeout); swTimers = []; }

    /* ── RESTING: THE STRIP IS A STATEMENT, NOT A BANNER ──────────────────
     *
     * The strip earns its width when the reader is looking at it and spends
     * it the rest of the time: on a 412 px phone the header, two chips and an
     * eleven-swatch key took a third of the readout, over the map. So after a
     * few seconds untouched it folds back to its icons (CSS: .ml-rest), and
     * anything that looks like attention unfolds it again.
     *
     * Three rules, each of which is a bug if broken:
     *  - it NEVER rests while a menu or the geology panel is open, or a
     *    surface the reader is working in shrinks under their hand;
     *  - it NEVER rests while the pointer or keyboard focus is inside it;
     *  - a rested strip still SAYS what it folded (the swatch count on the
     *    chip), because a key that silently disappears is a truncation that
     *    does not announce itself.
     * Waking is free and idempotent — one class toggle, no re-render — so it
     * can be wired to hover, focus, pointerdown and every public action.
     */
    var REST_MS = 4000;
    var resting = false;
    var restTimer = null;
    var restWired = false;
    var lastFitW = 0;          // the strip's width when it was last awake

    function stripHost() { return document.getElementById('stats-map'); }

    function restBlocked(host) {
        if (menuEl) return true;                       // a menu or the geology panel is open
        if (!host) return true;
        try {
            if (host.matches(':hover')) return true;
            if (host.contains(document.activeElement) &&
                document.activeElement !== document.body) return true;
        } catch (e) { /* :hover is unsupported nowhere we ship, but never throw here */ }
        return false;
    }

    function applyRest() {
        var host = stripHost();
        if (!host) return;
        host.classList.toggle('ml-rest', resting && !host.classList.contains('quiet'));
        if (!resting) return;
        // WHAT IT FOLDED, in the fold's own words. Derived from the swatches
        // actually rendered, never typed: the key's length is a property of
        // the view (invariant 2).
        var n = host.querySelectorAll('.ml-sw:not(.ml-sw-more):not(.ml-sw-all)').length;
        var main = host.querySelector('.ml-chip.geo .ml-chip-main') ||
                   host.querySelector('.ml-chip .ml-chip-main');
        Array.prototype.forEach.call(host.querySelectorAll('[data-rest]'), function (el) {
            if (el !== main) el.removeAttribute('data-rest');
        });
        if (!main) return;
        if (n > 0) main.setAttribute('data-rest', String(n));
        else main.removeAttribute('data-rest');
    }

    function scheduleRest() {
        if (restTimer) { clearTimeout(restTimer); restTimer = null; }
        restTimer = setTimeout(function () {
            restTimer = null;
            if (restBlocked(stripHost())) { scheduleRest(); return; }
            resting = true;
            applyRest();
        }, REST_MS);
    }

    /* Unfold, and start the clock again. Called from every public entry point
     * and from the strip's own pointer/focus handlers. */
    function wake() {
        if (resting) { resting = false; applyRest(); }
        scheduleRest();
    }

    function wireRest(host) {
        if (restWired || !host) return;
        restWired = true;
        // On the HOST rather than on the buttons: render() replaces the
        // strip's innerHTML on every idle, so a listener on a chip would be
        // thrown away with it. pointerenter covers mouse and pen; touch
        // arrives as pointerdown, which is also the first half of the tap
        // that opens a menu — so a phone user's first tap unfolds and their
        // second chooses, and the chip they aimed at has not moved, because
        // the chip is the one thing that does not move when it unfolds.
        ['pointerenter', 'pointerdown', 'focusin', 'wheel'].forEach(function (ev) {
            host.addEventListener(ev, wake, { passive: true });
        });
        host.addEventListener('pointerleave', scheduleRest, { passive: true });
        host.addEventListener('focusout', scheduleRest, { passive: true });
        scheduleRest();
    }

    function ageEntries() {
        if (typeof GeoMap === 'undefined' || !GeoMap.anyOn()) return [];
        var cov = coverage;
        var list = [];
        if (cov && cov.drawn) {
            list = Object.keys(cov.byAge).map(function (k) {
                return { key: k, hits: cov.byAge[k], meta: GeoMap.age(k),
                         lith: cov.lith[k] || 'mixed', on: true,
                         pct: cov.samples ? Math.round(100 * cov.byAge[k] / cov.samples) : 0 };
            });
            // Coverage descending; ICS rank breaks a tie, so two ages sampled
            // the same do not swap places between renders.
            list.sort(function (a, b) {
                return (b.hits - a.hits) || ((a.meta.rank || 99) - (b.meta.rank || 99));
            });
        }
        // Hidden ages, from state rather than from the canvas. Ordered by the
        // chart, since coverage is exactly what we cannot know about them.
        var off = (GeoMap.agesOff ? GeoMap.agesOff() : new Set());
        var lithOf = {};
        (GeoMap.allClasses ? GeoMap.allClasses() : []).forEach(function (c) {
            if (off.has(c.age) && !lithOf[c.age]) lithOf[c.age] = c.lith || 'mixed';
        });
        var offList = Array.from(off).map(function (k) {
            return { key: k, hits: 0, meta: GeoMap.age(k), lith: lithOf[k] || 'mixed', on: false };
        });
        offList.sort(function (a, b) { return (a.meta.rank || 99) - (b.meta.rank || 99); });
        return list.concat(offList);
    }

    /* One swatch, one rule about ornament. `px` is the swatch's SMALLER side:
     * GeoPatterns drops the hatch below its floor rather than shrinking it,
     * because an ornament too small to resolve is not a key, it is dirt on
     * the colour — and two units of one age then read as two ages. Every
     * surface here goes through it, so the strip and the matrix cannot end up
     * on different sides of that line. */
    function swatchBG(lith, color, px) {
        if (typeof GeoPatterns === 'undefined') return 'background:' + esc(color) + ';';
        return GeoPatterns.swatchStyle(lith, color, px);
    }

    function swatchHTML(e, collapsible) {
        // 13 px tall (see .ml-sw > i), which is exactly the ornament floor:
        // the key carries the hatch because lithology is half of what a
        // swatch means here, and the box is sized to the pattern rather than
        // the pattern squeezed into the box.
        var bg = swatchBG(e.lith, e.meta.color, 13);
        // A share of the VIEW, not an area: the sample cannot support a km²
        // and the tooltip must not imply one.
        // A unit too thin for the ~40 px sample grid gets no percentage at
        // all rather than "0%" or a rounded-up "1%": it IS drawn (the set
        // comes from the rendered features), we simply cannot say how much.
        var where = !e.on ? ' \u2014 hidden'
            : (e.hits === 0 ? ' \u2014 a thin unit in this view'
            : (e.pct >= 1 ? ' \u2014 about ' + e.pct + '% of this view'
                          : ' \u2014 under 1% of this view'));
        var title = e.meta.label + where + (e.on ? ' \u2014 tap to hide' : ' \u2014 tap to show again');
        return '<button type="button" class="ml-sw' + (e.on ? '' : ' off') +
            (collapsible ? ' ml-sw-x' : '') + '" aria-pressed="' + (!e.on) +
            '" aria-label="' + esc(e.meta.label) + (e.on ? ', shown' : ', hidden') + '"' +
            ' title="' + esc(title) + '"' +
            ' onclick="event.stopPropagation();MapLegend.toggleAge(\'' + esc(e.key) + '\')">' +
            '<i style="' + bg + '"></i></button>';
    }

    /* ── Which of these colours answers which question ──────────────────
     *
     * With "cobalt + copper" selected, the key is a row of five colours and
     * the chip says the two words — but nothing joins them. The reader cannot
     * tell which of those five is the cobalt ground and which the copper, and
     * where they OVERLAP (which is the interesting part: a unit that hosts
     * both is the one worth walking) there is nothing to see at all.
     *
     * So each selected commodity gets a bracket under the swatches it covers,
     * the way a printed legend braces a group. Overlaps are visible as two
     * brackets over the same swatch, which is exactly the statement.
     *
     * The ages are ordered so a commodity's swatches sit TOGETHER (grouped by
     * which set of commodities they answer, coverage breaking the tie), so a
     * bracket is normally one run; where it genuinely is not, it draws as two
     * segments rather than lying about contiguity.
     */
    function commodityAgeMap(keys) {
        var out = {};
        if (typeof GeoMap === 'undefined' || !GeoMap.sheets()) return out;
        var sheets = GeoMap.sheets(), min = GeoMap.minWeight ? GeoMap.minWeight() : 1;
        keys.forEach(function (k) { out[k] = {}; });
        (GeoMap.order() || []).forEach(function (id) {
            if (!(sheets[id] || {}).available) return;
            var stt = GeoMap.state(id);
            (GeoMap.classes(id) || []).forEach(function (c) {
                // Only units actually DRAWN: a bracket over a colour the map
                // is not painting would be a legend for a different map.
                var drawn = stt.isolate ? stt.isolate.has(c.code) : !stt.hidden.has(c.code);
                if (!drawn || !GeoMap.ageOn(c.age)) return;
                keys.forEach(function (k) {
                    var w = GeoMap.commodityWeight(id, c.code, k);
                    if (w >= min) out[k][c.age] = Math.max(out[k][c.age] || 0, w);
                });
            });
        });
        return out;
    }

    /* Which commodities the key strip has just drawn a bracket (and a
     * labelled way out) for. The chip beside it must not say the same word
     * again: "copper hosts" in the chip over a "copper ×" bracket two rows
     * below is one fact printed twice, and the reader has to work out whether
     * they are two different narrowings. Set by ageSwatches(), read by
     * render() — which is why render() builds the strip BEFORE the chips. */
    var bracketed = [];

    /* ── The contact layer's own swatch ─────────────────────────────────
     *
     * The key was a key to the FILLS only, so a view drawing nothing but
     * junction lines had a legend with nothing in it — orange hairlines all
     * over the map and no swatch, no word, and no way to switch them off
     * without opening the panel. An edge is not a period, so it is not one of
     * the age swatches; it is one extra cell at the end, drawn as a line, and
     * (like every swatch here) it is a switch.
     *
     * Only when contacts are actually PAINTED in this view: a legend entry for
     * something not on screen is the same lie as a missing one, in reverse. */
    function contactSwatchHTML() {
        if (!(typeof GeoMap !== 'undefined' && GeoMap.contactsOn && GeoMap.contactsOn())) return '';
        if (!contactHits) return '';
        var n = (GeoMap.drawnContactCount && GeoMap.drawnContactCount()) || 0;
        var pick = (GeoMap.contactPairs && GeoMap.contactPairs()) || new Set();
        var what = pick.size ? junctionsWords(pick)
            : (GeoMap.contactsGradedOnly && GeoMap.contactsGradedOnly()
                ? 'graded junctions' : 'every mapped junction');
        var title = 'Contacts — ' + what + ', ' + n + ' junction type(s) in scope' +
            ' — tap to hide the lines';
        return '<button type="button" class="ml-sw ml-sw-cont" aria-pressed="false"' +
            ' aria-label="Contact lines, shown" title="' + esc(title) + '"' +
            ' onclick="event.stopPropagation();MapLegend.toggleContacts()"><i></i></button>';
    }

    function ageSwatches() {
        bracketed = [];
        var list = ageEntries();
        var contactSw = contactSwatchHTML();
        // No fills here but lines painted: the strip is the contact swatch
        // alone, which is a legend for what IS on the screen.
        if (!list.length) {
            return contactSw
                ? '<div class="ml-swatches" style="grid-template-columns:repeat(1,max-content);"' +
                  ' aria-label="Geology legend — contact lines">' + contactSw + '</div>'
                : '';
        }

        var comms = (typeof GeoMap !== 'undefined' && GeoMap.selectedCommodities)
            ? Array.from(GeoMap.selectedCommodities()).sort() : [];
        var cmap = comms.length ? commodityAgeMap(comms) : {};
        // Drop a commodity that ends up bracketing nothing here: an empty
        // bracket row is a claim about this view that is not true.
        comms = comms.filter(function (k) { return Object.keys(cmap[k] || {}).length; });
        bracketed = comms.slice();

        if (comms.length) {
            // Group by which commodities an age answers, so each bracket is a
            // run. Coverage still orders inside a group, and the sample-blind
            // (hits 0) ones stay where their group is rather than being
            // scattered.
            var sig = function (e) {
                var m = 0;
                comms.forEach(function (k, i) { if (cmap[k][e.key]) m |= (1 << i); });
                return m;
            };
            list = list.slice().sort(function (a, b) {
                return (sig(b) - sig(a)) || (b.hits - a.hits) ||
                       ((a.meta.rank || 99) - (b.meta.rank || 99));
            });
        }

        /* ── THE KEY MUST NOT SET THE PANEL'S WIDTH ───────────────
         *
         * Ten swatches at 21 px is 250 px of grid, and the stats panel is
         * shrink-to-fit: turning geology on therefore WIDENED the panel over
         * the map, and every number above it moved. A legend is an annotation
         * on the panel, not a claim on its layout.
         *
         * Two halves to the fix, and both are needed:
         *  - CSS makes the strip contribute NO intrinsic width (`width:0;
         *    min-width:100%`), so the panel is sized by the rows that carry
         *    numbers and the strip fills whatever that leaves.
         *  - here, the number of columns is derived from that measured width,
         *    so the strip TRUNCATES honestly behind its "+n" instead of being
         *    clipped by the overflow. A clipped swatch is a period the reader
         *    can see half of and cannot tap; "+3" is a truncation that says
         *    it is one.
         */
        var cap = fitCols(list.length);
        var head = list.slice(0, cap), tail = list.slice(cap);
        var cells = head.map(function (e) { return swatchHTML(e, false); }).join('');
        var nCols = Math.min(list.length, cap);
        var extras = 0;
        // The contact line goes right after the periods and before the chrome:
        // it is part of the key, not a control on it.
        if (contactSw) { extras++; cells += contactSw; }
        if (tail.length) {
            extras++;
            cells += '<button type="button" class="ml-sw-more" id="ml-sw-more"' +
                ' aria-expanded="' + expanded + '"' +
                ' title="' + (expanded ? 'Show fewer periods'
                    : tail.length + ' more period(s), each covering less of this view — tap to show them') + '"' +
                ' onclick="event.stopPropagation();MapLegend.toggleMore()">' +
                (expanded ? '×' : '+' + tail.length) + '</button>';
        }
        // The way back. Only present once something is hidden, so the default
        // key carries no chrome — same rule as the strip itself.
        if (typeof GeoMap !== 'undefined' && GeoMap.anyFiltered && GeoMap.anyFiltered()) {
            extras++;
            // Clears EVERYTHING, not just the periods. A button labelled "all"
            // that leaves the map filtered by commodity is the failure this
            // whole pass is about: the reader taps it, the map barely changes,
            // and they conclude the control is broken rather than that it did
            // one third of what it said.
            var offN2 = GeoMap.agesOff().size;
            var selN = GeoMap.selectedCommodities().size;
            var bits = [];
            if (selN) bits.push('the commodity selection');
            if (GeoMap.minWeight() > 1) bits.push('the grade floor');
            if (offN2) bits.push(offN2 + ' hidden period' + (offN2 === 1 ? '' : 's'));
            cells += '<button type="button" class="ml-sw-all" title="' +
                esc('Back to every mapped unit \u2014 clears ' + bits.join(', ')) + '"' +
                ' onclick="event.stopPropagation();MapLegend.geoAll()">all</button>';
        }

        var rows = '';
        comms.forEach(function (k, ri) {
            // Only over columns the reader can actually see: a bracket across
            // a collapsed (zero-width) swatch would point at nothing.
            // The head row only: the overflow lives in its own grid below
            // and a bracket cannot span two grids. A commodity whose ground
            // is all in the overflow simply has no bracket, which is honest —
            // it also has no swatch up here to brace.
            var visN = nCols;
            var members = [];
            for (var i = 0; i < visN; i++) if (cmap[k][list[i].key]) members.push(i);
            if (!members.length) return;
            var segs = [], start = members[0], prev = members[0];
            members.slice(1).forEach(function (i) {
                if (i !== prev + 1) { segs.push([start, prev]); start = i; }
                prev = i;
            });
            segs.push([start, prev]);
            var best = 0;
            members.forEach(function (i) { best = Math.max(best, cmap[k][list[i].key]); });
            var gr = ri + 2;
            rows += segs.map(function (sg) {
                return '<span class="ml-br g' + best + '" style="grid-row:' + gr +
                    ';grid-column:' + (sg[0] + 1) + '/' + (sg[1] + 2) + '"></span>';
            }).join('');
            var lastCol = segs[segs.length - 1][1] + 2;
            // The label is also the way OUT of that commodity. With two
            // selected, "show me the copper too" needs an undo that does not
            // mean "clear everything": the chip's menu could only offer the
            // whole selection, and the bracket is where the reader is already
            // looking when they decide one of the two was a mistake.
            rows += '<button type="button" class="ml-br-lbl" style="grid-row:' + gr +
                ';grid-column:' + lastCol + '/-1" title="' +
                esc(k.replace(/_/g, ' ') + ': ' + members.length + ' period(s) drawn here' +
                    (best === 3 ? ', classic host' : best === 2 ? ', likely host' : '') +
                    ' \u2014 tap to stop showing ' + k.replace(/_/g, ' ')) + '"' +
                ' aria-label="Stop showing ' + esc(k.replace(/_/g, ' ')) + ' hosts"' +
                ' onclick="event.stopPropagation();MapLegend.geoCommodity(\'' + esc(k) + '\')">' +
                esc(k.replace(/_/g, ' ')) + '<i>\u00d7</i></button>';
        });

        // One grid, so a bracket lines up with the swatch it braces without
        // measuring anything: the swatches are the columns.
        var style = 'grid-template-columns:repeat(' + nCols + ',auto)' +
            (extras ? ' repeat(' + extras + ',max-content)' : '') + ';';
        var html = '<div class="ml-swatches' + (rows ? ' braced' : '') + '" style="' + style + '"' +
            ' aria-label="Geology legend, most of this view first — tap a period to hide it">' +
            cells + rows + '</div>';

        /* The overflow, when "+n" is open: its OWN grid, wrapping at the same
         * column count. It is a separate element rather than more columns in
         * the first one because the first one is width-constrained by the
         * panel — adding columns there would widen the panel again, which is
         * the whole thing this pass removed. */
        if (expanded && tail.length) {
            html += '<div class="ml-swatches ml-sw-rest" style="grid-template-columns:repeat(' +
                nCols + ',auto);" aria-label="The rest of the periods drawn here">' +
                tail.map(function (e) { return swatchHTML(e, true); }).join('') + '</div>';
        }
        return html;
    }

    /* ── How many swatches fit ──────────────────────────────
     *
     * MEASURED off the panel, never assumed: the stats panel is a different
     * width with a park selected than with an AOI, and on a phone it is the
     * viewport. A fixed cap is how the strip came to be 250 px wide inside a
     * 180 px panel.
     *
     * The reserve is the chrome the strip can carry in the same row: the
     * "+n" (only when there IS an overflow) and the "all" escape (only once
     * something is filtered). Reserving for a button that is not there is how
     * a 5-swatch key becomes a 4-swatch key for no reason, so each is counted
     * only when it will be drawn. Floor of 4, because a key of one swatch and
     * a "+9" is not a key.
     */
    function fitCols(n) {
        var host = document.getElementById('stats-map');
        var w = host ? host.clientWidth : 0;
        // A RESTED STRIP IS THE WRONG RULER. While folded it sits in the
        // readout's spare grid cell (~a third of a phone), so measuring it
        // would size the key for a box it is not going to be shown in, and
        // the reader would wake a full-width strip carrying four swatches and
        // a "+7". The last width it actually had awake is the honest one.
        if (resting && lastFitW) w = lastFitW;
        else if (w) lastFitW = w;
        if (!w) return MAX;
        // The swatch is 21 px on the desktop panel and 26 px on a phone, and
        // that is a CSS decision: measure one rather than keeping a second
        // copy of it here.
        var one = host.querySelector('.ml-sw');
        var SW = (one && one.offsetWidth ? one.offsetWidth : 21) + 4;
        var filtered = typeof GeoMap !== 'undefined' && GeoMap.anyFiltered && GeoMap.anyFiltered();
        var reserve = (filtered ? 30 : 0);
        var fits = function (r) { return Math.floor((w - 16 - r) / SW); };
        // Does the "+n" have to exist? Only if the key does not fit without it.
        var cap = fits(reserve);
        if (n > cap) cap = fits(reserve + 30);
        return Math.max(4, Math.min(MAX, cap));
    }

    /* Collapsed swatches animate in the way the date-preset tags do: width and
     * opacity, staggered, so the strip grows rather than jumping. Re-rendering
     * while already expanded must NOT re-stagger (the strip re-renders on
     * every map idle), so the animation is opt-in. */
    function syncSwatchExpansion(animate) {
        cancelSwTimers();
        var els = Array.prototype.slice.call(document.querySelectorAll('#stats-map .ml-sw-x'));
        if (!expanded) { els.forEach(function (el) { el.classList.remove('visible'); }); return; }
        els.forEach(function (el, i) {
            if (!animate) { el.classList.add('visible'); return; }
            swTimers.push(setTimeout(function () { el.classList.add('visible'); }, i * 45));
        });
    }


    // ---------------------------------------------------------------
    // The menu
    // ---------------------------------------------------------------
    var menuEl = null;
    // A rebuild waiting for MapLibre to paint; see refreshWhenDrawn() below.
    // Declared here because closeMenu() cancels it, and a reader should not
    // have to know about `var` hoisting to see that the two belong together.
    var paintWait = null;

    function closeMenu() {
        if (menuEl) { menuEl.remove(); menuEl = null; }
        document.removeEventListener('click', closeMenu);
        // A pending "rebuild once the map has drawn" outlives the menu it was
        // for otherwise, and fires a timer against a menu nobody is looking
        // at. rebuildGeoMenuNow() cancels first and then calls this, so the
        // legitimate rebuild path is unaffected.
        cancelPaintWait();
    }

    function row(cls, role, on, label, icon, title, onclick, mark) {
        return '<button type="button" class="aoi-menu-item mode-opt' + (on ? ' on' : '') + cls +
            '" role="' + role + '" aria-checked="' + (on ? 'true' : 'false') +
            '" title="' + esc(title) + '" onclick="event.stopPropagation();' + onclick + '">' +
            '<span class="mode-mark ' + mark + '"></span>' +
            '<i class="' + icon + ' ml-mi"></i>' + esc(label) + '</button>';
    }

    function openMenu(btn) {
        var already = menuEl && menuEl.dataset.kind === 'layers';
        closeMenu();
        if (already) return;
        var el = document.createElement('div');
        el.className = 'aoi-menu mode-menu ml-menu';
        el.dataset.kind = 'layers';
        el.setAttribute('role', 'menu');
        el.setAttribute('aria-label', 'Basemap and overlays');
        var html = '<div class="mode-menu-head">Basemap</div>';
        var cur = bm();
        BASEMAPS.forEach(function (b) {
            html += row('', 'menuitemradio', cur === b[0], b[1], b[2], b[3],
                "MapLegend.pickBasemap('" + b[0] + "')", 'radio');
        });

        html += '<div class="mode-menu-head">Overlays</div>';

        // Geology. A sheet that is not built keeps its row and says why:
        // an absent row reads as "this map has no geology", which is a
        // different (and wrong) statement.
        var geoAvail = false, geoWhy = 'not built on this server', nSheets = 0;
        if (typeof GeoMap !== 'undefined' && GeoMap.sheets()) {
            var gs = GeoMap.sheets();
            (GeoMap.order() || []).forEach(function (id) {
                if (gs[id] && gs[id].available) { geoAvail = true; nSheets++; }
                else if (gs[id] && gs[id].reason) geoWhy = gs[id].reason;
            });
        }
        html += row(geoAvail ? '' : ' refused', 'menuitemcheckbox', geoOn(), 'Geology', 'icon-mountain',
            geoAvail ? nSheets + ' survey sheet' + (nSheets === 1 ? '' : 's') +
                       ' \u2014 colour = age, pattern = rock type'
                     : 'Geology: ' + geoWhy,
            geoAvail ? 'MapLegend.toggleGeo()' : 'void 0', 'check');

        var hm = histMeta();
        var hAvail = !!(hm && hm.available);
        html += row(hAvail ? '' : ' refused', 'menuitemcheckbox', histOn(), 'Historical maps', 'icon-scroll-text',
            hAvail ? ((hm.name || 'Scanned survey series') + ' \u2014 draped over the basemap')
                   : ('Historical maps: ' + ((hm && hm.reason) || 'no archive installed')),
            hAvail ? 'MapLegend.toggleHist()' : 'void 0', 'check');

        // The full legend, per-unit toggles, opacity and the downloads live in
        // Map Settings. This strip is deliberately not a second home for them.
        html += '<button type="button" class="aoi-menu-item ml-more" ' +
            'onclick="event.stopPropagation();MapLegend.openSettings()">' +
            '<i class="icon-sliders-horizontal ml-mi"></i>Map settings\u2026' +
            '<em>legend, opacity, downloads</em></button>';

        el.innerHTML = html;
        place(el, btn);
    }

    /* ── The historical chip's own menu ──────────────────────────────────
     *
     * Small on purpose. A scanned sheet series has exactly three questions:
     * how strongly is it drawn, where are the sheets, and where is the file.
     * Everything else (the description, the attribution, the black-ink
     * download blurb) belongs to Map Settings, and the last row goes there.
     *
     * The opacity slider is the reason this menu exists. A traced-ink overlay
     * over satellite imagery is either invisible or obliterating, so "a bit
     * less" is the commonest thing a reader wants and it was four clicks deep
     * in the admin panel; the chip in front of them could only destroy the
     * layer. Live `oninput` — the whole point is watching the ink fade against
     * the ground, and a commit-on-release slider makes that a guessing game.
     */
    function openHistMenu(btn) {
        var already = menuEl && menuEl.dataset.kind === 'hist';
        closeMenu();
        if (already) return;
        var hm = histMeta() || {};
        var el = document.createElement('div');
        el.className = 'aoi-menu mode-menu ml-menu';
        el.dataset.kind = 'hist';
        el.setAttribute('role', 'menu');
        el.setAttribute('aria-label', 'Historical map overlay');
        var pct = (typeof HistMap !== 'undefined') ? Math.round(HistMap.opacity() * 100) : 85;
        var html = '<div class="mode-menu-head">' + esc(hm.name || 'Historical maps') + '</div>';
        html += '<div class="ml-op"><span class="ml-op-l">Opacity</span>' +
            '<input type="range" min="10" max="100" value="' + pct + '" ' +
            'aria-label="Overlay opacity" ' +
            'onclick="event.stopPropagation()" ' +
            'oninput="MapLegend.histOpacity(this.value)">' +
            '<b id="ml-op-v">' + pct + '%</b></div>';
        // Where the sheets are is only worth a row when the answer is "not
        // here": inside the envelope the map itself is the answer, and a
        // permanent "go to the sheets" row would move a reader who is already
        // looking at them. Same rule as the calm-map toasts.
        if (histOffView()) {
            html += '<button type="button" class="aoi-menu-item ml-more" ' +
                'onclick="event.stopPropagation();MapLegend.histGoTo()">' +
                '<i class="icon-locate-fixed ml-mi"></i>Go to the sheets' +
                '<em>none reach this view</em></button>';
        }
        html += '<button type="button" class="aoi-menu-item ml-more" ' +
            'onclick="event.stopPropagation();MapLegend.openSettings()">' +
            '<i class="icon-sliders-horizontal ml-mi"></i>Map settings\u2026' +
            '<em>provenance, download</em></button>';
        el.innerHTML = html;
        place(el, btn);
    }

    /* ── The matrix has to wait for the map ─────────────────────────────
     *
     * Its columns are the periods DRAWN in this view, and "drawn" is measured
     * off the rendered canvas (`measureCoverage`, above). A menu rebuilt in
     * the same tick as the filter change therefore shows the columns of the
     * map as it was ONE GESTURE AGO: clear a one-period narrowing and the
     * matrix still has one column, which reads as "the button did nothing" —
     * the exact failure this whole surface exists to prevent.
     *
     * The first fix rebuilt the menu twice: once immediately (with the stale
     * canvas) and again on `idle`. That is a flicker, it does the work twice,
     * and for the width of one frame it shows the reader a wrong answer —
     * which is worse than showing them the previous answer, because a wrong
     * answer that corrects itself is indistinguishable from a control that
     * bounces. So the menu is rebuilt EXACTLY ONCE, after MapLibre has
     * painted, and until then it keeps the picture it already had.
     *
     * Two things this has to survive:
     *
     *  - `idle` MAY NOT COME. It fires when the map settles, and a filter
     *    change that turns out to alter no tile in view can settle without a
     *    repaint. A menu that waits forever for it is stuck showing the old
     *    matrix, so the wait is bounded: `triggerRepaint()` asks for a frame,
     *    and a timer answers if neither arrives.
     *  - THE READER MOVES ON. They may close the menu, open the layers menu,
     *    or fire a second gesture while the first is still settling. The
     *    pending rebuild is one-shot, keyed on the menu still being the geology
     *    one, and a second request replaces the first rather than queueing a
     *    second rebuild behind it.
     */
    function cancelPaintWait() {
        if (!paintWait) return;
        clearTimeout(paintWait.timer);
        paintWait = null;
    }

    /* ── THE PANEL IS REBUILT; IT MUST NOT LOOK REBUILT ────────────
     *
     * Every gesture re-renders this panel from scratch, so without help the
     * table BLINKS: the reader taps a cell, 700 ms later a new grid appears,
     * and whether anything moved (and which thing) is something they have to
     * work out by comparing two pictures from memory.
     *
     * FLIP, keyed on `data-mx` (`cell:gold|archean`, `row:gold`, `col:archean`).
     * Measure every keyed element before the rebuild, measure again after, and
     * play the difference backwards: a row that changed place SLIDES there, so
     * the reorder is something the reader watches happen rather than something
     * they discover. An element that did not move is not touched, so the
     * ordinary rebuild costs nothing and does not flicker.
     *
     * Transforms only — no layout is animated, so this cannot fight the
     * grid, and `prefers-reduced-motion` skips straight to the end state.
     */
    function mxRects() {
        var out = {};
        if (!menuEl) return out;
        menuEl.querySelectorAll('[data-mx]').forEach(function (el) {
            out[el.dataset.mx] = el.getBoundingClientRect();
        });
        return out;
    }

    function mxFlip(before) {
        if (!menuEl || !before) return;
        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        menuEl.querySelectorAll('[data-mx]').forEach(function (el) {
            var b = before[el.dataset.mx];
            if (!b) {
                // New here: fade it in rather than having it appear
                // mid-animation as though it had always been there.
                el.classList.add('ml-mx-in');
                return;
            }
            var a = el.getBoundingClientRect();
            var dx = b.left - a.left, dy = b.top - a.top;
            if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
            el.style.transition = 'none';
            el.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
            el.classList.add('ml-mx-moving');
        });
        // One reflow, then release everything at once: staggering a reorder
        // would read as several separate movements rather than one settling.
        void menuEl.offsetWidth;
        menuEl.querySelectorAll('.ml-mx-moving').forEach(function (el) {
            el.style.transition = '';
            el.style.transform = '';
            setTimeout(function () { el.classList.remove('ml-mx-moving'); }, 420);
        });
    }

    function rebuildGeoMenuNow(opts) {
        if (!menuEl || menuEl.dataset.kind !== 'geo') return;
        var btn = document.querySelector('#stats-map .ml-chip.geo');
        if (!btn) return;
        var sc = menuEl.scrollTop;
        var before = (opts && opts.animate) ? mxRects() : null;
        closeMenu();
        openGeoMenu(btn);
        if (menuEl) menuEl.scrollTop = sc;   // do not throw away their place
        if (before) mxFlip(before);
    }

    /* ── THE BAR IS A LIVE SURFACE, NOT A SNAPSHOT ─────────────────────
     *
     * THE BUG THIS FIXES, because it survived the whole reMeasure() apparatus
     * below and looked exactly like it: with three sheets on and Junctions
     * picked, the panel bar said "104 units · counting lines…" for as long as
     * you cared to watch, over a map drawing 452 of them — and
     * queryRenderedFeatures, asked from the console at that moment, said 439.
     * The measurement was RIGHT and the label was stale.
     *
     * Why: contactHits/contactsPending are module state, and the label is
     * HTML built once by headRow(). watchMap's idle handler calls render(),
     * which re-measures — so the pending flag was cleared by the very pass
     * that should have redrawn the bar, and the follow-up
     * `if (contactsPending) reMeasure(2)` then found nothing pending and did
     * nothing. The state healed; the pixels did not. The panel is only rebuilt
     * when the SKILL SCOPE changes, so "counting lines…" was frozen until the
     * reader closed and reopened the panel — which is exactly what "closing
     * and reopening fixed it" meant, and it was read as a paint-timing bug.
     *
     * The fix is that whatever re-measures also re-STATES, in place: one
     * innerHTML and one title, no teardown. rebuildGeoMenuNow() would also
     * have worked and is the wrong tool — it closes and reopens the panel,
     * throwing away focus, hover, scroll and any half-made choice, ~20 ms of
     * DOM for two numbers, on every idle. A surface that contradicts the
     * canvas must be CHEAP to correct, or it will end up corrected rarely.
     */
    function syncBar() {
        if (!menuEl || menuEl.dataset.kind !== 'geo') return;
        var el = menuEl.querySelector('.ml-bar-n');
        if (!el) return;
        var c = barCountText();
        if (el.innerHTML !== c.html) el.innerHTML = c.html;
        if (el.title !== c.title) el.title = c.title;
    }

    /* Re-measure the canvas and rebuild the strip + the open matrix, once the
     * map has actually drawn the change. Everything that alters what is on
     * screen ends with this instead of with a bare render(). */
    function refreshWhenDrawn(opts) {
        render();                       // the strip's own state (chip, wording)
        if (typeof map === 'undefined' || !map || !map.once) {
            rebuildGeoMenuNow(opts);
            if (typeof geoMapPanelDirtied === 'function') geoMapPanelDirtied();
            return;
        }
        cancelPaintWait();
        var done = false;
        var finish = function () {
            if (done) return;
            done = true;
            cancelPaintWait();
            render();                   // NOW the canvas says what is drawn
            rebuildGeoMenuNow(opts);
            // THE OTHER HOME OF THE SAME MIXER. The admin card is built from
            // the same canvas measurement as the panel, so it waits for the
            // same paint. Not renderGeoMapPanel(): that begins with a
            // MapLegend.refresh(), and render() ran two lines ago — the card's
            // own scheduler is what we want, without a second strip pass.
            if (typeof geoMapPanelDirtied === 'function') geoMapPanelDirtied();
            syncBar();                  // the bar the rebuild just re-emitted, from the fresh measurement
            // ONE `idle` IS NOT ENOUGH WHEN A LAYER WAS JUST ADDED. The gesture
            // that adds the contact layers (mxMode('junction'), a junction pick)
            // can reach idle before they have painted, and the 0 measured here
            // would then be frozen into the bar. See reMeasure().
            reMeasure();
        };
        paintWait = { timer: setTimeout(finish, 700) };
        map.once('idle', finish);
        // A filter that changes no tile in view can settle without drawing;
        // asking for a frame is what makes `idle` a promise rather than a hope.
        if (map.triggerRepaint) map.triggerRepaint();
    }

    /* ── A COUNT MEASURED BEFORE THE LAYER PAINTED IS NOT A MEASUREMENT ──
     *
     * The failure this exists for, in full, because it cost an afternoon:
     * with three sheets on and the Junctions tab opened, `drawnContactCount()`
     * said 452, `queryRenderedFeatures` said 439 — and the panel bar said
     * "104 units · no lines here" over a map visibly drawing hundreds of
     * them, and stayed wrong through `idle`, `triggerRepaint` and a manual
     * refresh. Closing and reopening the panel fixed it.
     *
     * Why: `mxMode('junction')` ADDS the contact layers and then asks for a
     * refresh in the same tick. refreshWhenDrawn() waits for one `idle`, and
     * MapLibre can reach idle before a layer added this tick has rendered
     * anything — so contactsInView() honestly returned 0, render() froze it,
     * and nothing re-measured, because watchMap's idle handler only rebuilds
     * when the SKILL SCOPE changes.
     *
     * Two halves to the fix, and the second matters more than the first:
     *
     *  1. RE-MEASURE while a zero is still unproven — on the next `idle`, up
     *     to a few times, stopping the moment the number moves or the layer
     *     set changes under us. A timeout would be a guess about paint
     *     latency; `idle` is the map's own word for "I have finished drawing".
     *  2. SAY NOTHING rather than "no lines here" in the meantime. `0` and
     *     "not yet known" are different states and only one of them is a
     *     claim about the canvas. A label that contradicts the canvas is
     *     worse than no label — the rule geoOffView() was written for, and
     *     this is the same rule arriving from the timing side.
     */
    var reMeasureTimer = null;
    var reMeasureLeft = 0;
    var reMeasureGen = 0;

    function cancelReMeasure() {
        if (reMeasureTimer) { clearTimeout(reMeasureTimer); reMeasureTimer = null; }
        reMeasureLeft = 0;
        reMeasureGen++;      // any pending `idle` from an older pass is stale
    }

    /* Call after anything that ADDS or REMOVES a geology layer. Cheap when
     * nothing is pending: one queryRenderedFeatures and a string compare. */
    function reMeasure(tries) {
        cancelReMeasure();
        reMeasureLeft = tries || 3;
        var gen = reMeasureGen;

        function again() {
            if (gen !== reMeasureGen) return;    // superseded
            if (reMeasureTimer) { clearTimeout(reMeasureTimer); reMeasureTimer = null; }
            step();
        }

        function step() {
            if (typeof map === 'undefined' || !map || !map.once) return;
            var wasHits = contactHits, wasSig = measuredSig;
            // FORCED: this pass exists precisely because the last one may have
            // read a layer that had not painted, and the memo's key cannot see
            // that difference — same viewport, same layers, same filters.
            measureCoverage(true);
            if (contactHits !== wasHits || measuredSig !== wasSig) {
                render();
                // The bar with the counts lives in the geology PANEL, not in
                // the strip, so a re-measure that does not restate it fixes a
                // number nobody is looking at. In place — rebuilding the panel
                // here would throw away the reader's scroll and hover for the
                // sake of two digits. See syncBar().
                syncBar();
            }
            // Only an UNPROVEN zero is worth another pass. A real "nothing
            // here" settles on the first one, so panning over empty ocean
            // costs a single query.
            if (!contactsPending || reMeasureLeft-- <= 0) return;
            reMeasureTimer = setTimeout(again, 250);
            map.once('idle', again);
            if (map.triggerRepaint) map.triggerRepaint();
        }

        step();
    }

    /* ── THE DOWNLOAD, AND WHY IT OFFERS TWO THINGS ─────────────────────
     *
     * The panel is where a reader BUILDS a view — gold hosts, likely and up,
     * these two junctions, that period hidden. The question that follows is
     * "can I have this in QGIS", and until now the only download was the whole
     * catalogue, four clicks away in admin ▸ Map Settings. A single arrow that
     * silently gave them everything would answer a question they did not ask,
     * and one that silently gave them only their filter would be a truncation
     * that does not announce itself. So it offers BOTH and says which is which,
     * with the counts each one would carry.
     *
     * It lives in the panel BAR rather than in a tab, because the view being
     * exported is made in both tabs (rocks and junctions) and the bar is the
     * one piece of chrome that belongs to neither.
     *
     * Two paths, deliberately different shapes:
     *   - "everything" is GET /api/geomap/geopackage, the stamped cached file.
     *     It is a plain link (it works with JS off) and shares the same
     *     "Preparing…" state as the admin one.
     *   - "this view" is POST /api/geomap/geopackage with the selection
     *     GeoMap.selection() resolved. It cannot be a link: the body is a list
     *     of keys, and the answer is built for this request only.
     */
    function downloadFacts() {
        var f = { ok: false, units: 0, lines: 0, anchors: null, whole: null, view: null };
        if (typeof GeoMap === 'undefined' || !GeoMap.geopackage) return f;
        var g = GeoMap.geopackage();
        if (!g) return f;
        f.ok = true;
        f.whole = g.url;
        f.view = g.viewUrl || g.url;
        f.bytes = g.bytes || 0;
        f.sheets = (g.sheets || []).length;
        f.units = (GeoMap.drawnUnitCount && GeoMap.drawnUnitCount()) || 0;
        f.lines = (GeoMap.contactsOn && GeoMap.contactsOn() && GeoMap.drawnContactCount)
            ? GeoMap.drawnContactCount() : 0;
        f.anchors = (GeoMap.anchors && GeoMap.anchors()) || null;
        return f;
    }

    function downloadBtnTitle() {
        var f = downloadFacts();
        if (!f.ok) return 'No GeoPackage is built on this server';
        return 'Download as a GeoPackage — this view (' + f.units + ' unit(s)' +
            (f.lines ? ', ' + f.lines + ' junction type(s)' : '') + ') or every sheet';
    }

    /* What the reader would call the view, for the file name and the layer
     * description inside it. The same phrase the chip shows, so the file and
     * the map are not named differently.
     *
     * geoFilterNote() ALREADY names the contact layer and its own narrowing
     * ("graded contacts", "granite against greenstone contacts"), so this must
     * not append a second contact clause: the first file out of this path was
     * called `geology-gold-hosts-graded-contacts-graded-junctions.gpkg`, which
     * says one thing twice and reads as two different filters. */
    function viewLabel() {
        var fn = geoFilterNote([]);
        var base = (fn && fn.full) || '';
        if (!base && mxMode === 'junction' && contactFacts().on) {
            base = junctionsWords(contactFacts().pairs) || 'graded junctions';
        }
        return base || 'the whole legend, as drawn';
    }

    /* The link that reproduces this view and points at one download row.
     *
     * Copied from anim.js's exportShareLink(), and deliberately the same shape:
     * a download link in this app is a SHARE LINK that points at a row, never a
     * URL that spends minutes of CPU on open. `geo_panel` already rides along
     * (getShareParams), so the recipient lands on the same panel, same tab,
     * same filter — with the menu open and the row pointed at. */
    function exportShareLink(item) {
        var url;
        try {
            url = (typeof buildShareUrl === 'function') ? buildShareUrl() : window.location.href;
        } catch (e) { url = window.location.href; }
        try {
            var u = new URL(url, window.location.href);
            u.searchParams.set('geo_export', item);
            return u.toString();
        } catch (e) { return url; }
    }

    /* One row, the app's own download-row markup: the entry, plus an explicit
     * copy-link button. Same component as the park/AOI menu (globe.html
     * exportMenuItems) and the animator's (anim.js exportMenuHTML) — including
     * the reason the ⧉ exists at all, which is that Safari's own "Copy Link"
     * copies the row's LABEL rather than the address. */
    function dlRow(inner, url, item) {
        return '<div class="aoi-menu-row">' + inner +
            (url ? '<button class="aoi-menu-copy" title="Copy a link to this view and this download" ' +
                'aria-label="Copy a link to this view and this download" ' +
                'onclick="return copyExportLink(event, this)" ' +
                'data-item="' + esc(item || '') + '" data-url="' + esc(url) + '"><i class="icon-copy"></i></button>'
                : '') + '</div>';
    }

    function openDownloadMenu(btn, highlight) {
        var already = dlEl;
        // The geology PANEL must survive this: it is what is being exported,
        // and closing it to show a two-row menu would take the counts the
        // reader is checking off the screen. So this menu is its own element
        // and does not go through closeMenu().
        closeDlMenu();
        if (already) return;
        var f = downloadFacts();
        var el = document.createElement('div');
        el.className = 'aoi-menu mode-menu ml-menu ml-menu-dl';
        el.dataset.kind = 'geodl';
        el.setAttribute('role', 'menu');
        el.setAttribute('aria-label', 'Download the geology');
        var html = '<div class="mode-menu-head">Download as GeoPackage</div>';
        if (!f.ok) {
            html += '<div class="mode-menu-note">No vectorized sheet is built on this ' +
                'server, so there is nothing to package.</div>';
            el.innerHTML = html;
            dlEl = el;
            placeDl(el, btn);
            return;
        }
        // THIS VIEW FIRST: it is the answer to the question the panel exists
        // for. Its counts are the bar's own counts, from the same filters the
        // paint uses, so the reader can see what they are about to get.
        var canView = f.units > 0;
        html += dlRow('<button type="button" class="aoi-menu-item" data-item="view"' +
            (canView ? '' : ' disabled') + ' ' +
            'title="' + esc(canView
                ? 'The units and junctions on screen right now, with the same age/rock-type ' +
                  'legend and a QGIS project inside. Filtered exactly as the map is.'
                : 'Nothing is drawn here, so there is no view to export — pan onto a sheet ' +
                  'or widen the filter.') + '" ' +
            'onclick="event.stopPropagation();' + (canView ? 'MapLegend.downloadView()' : 'void 0') + '">' +
            '<i class="icon-crop"></i><span>GeoPackage</span>' +
            '<em>' + (canView
                ? 'this view, ' + f.units + ' unit' + (f.units === 1 ? '' : 's') +
                  (f.lines ? ' + ' + f.lines + ' line type' + (f.lines === 1 ? '' : 's') : '')
                : 'nothing drawn here') + '</em></button>',
            canView ? exportShareLink('view') : '', 'view');
        html += dlRow('<a class="aoi-menu-item" data-item="all" id="ml-dl-all" ' +
            'href="' + esc(dlUrl(f.whole)) + '" ' +
            'title="' + esc('Every unit of every sheet, unfiltered — one geology_units layer with ' +
                'the sheet as a column, typed area and one w_<commodity> weight per commodity.') + '" ' +
            'onclick="MapLegend.downloadAllStarted()">' +
            '<i class="icon-database"></i><span>GeoPackage</span>' +
            '<em>all ' + f.sheets + ' sheets' +
            (f.bytes ? ', ' + Math.round(f.bytes / 1048576) + ' MB' : '') + '</em></a>',
            exportShareLink('all'), 'all');

        // THE EVIDENCE RIDES IN BOTH FILES, and the reader is told so here
        // rather than discovering an unexplained point layer in QGIS. Its
        // absence is named for the same reason: "we could not ship it" is not
        // "nothing was ever checked".
        var a = f.anchors;
        if (a && a.available) {
            var withheld = (a.withheld || []).map(function (x) { return x.label; }).join(', ');
            html += '<div class="mode-menu-note">Both files carry <b>' +
                a.n.toLocaleString() + '</b> published workings from ' +
                (a.sources || []).length + ' lists — the ground this model was scored ' +
                'against, five fields each (coordinate, year, resource, the publisher\u2019s ' +
                'own id and where it resolves).' +
                (withheld ? ' Used in the scoring but not redistributable: ' +
                    esc(withheld) + '.' : '') +
                '</div>';
        } else if (a && a.reason) {
            html += '<div class="mode-menu-note warn">The reference workings are not ' +
                'installed on this server, so neither file can carry the evidence the ' +
                'model was scored against.</div>';
        }
        html += '<div class="mode-menu-note">An affinity is an inference from rock type — ' +
            'nothing in either file counts, ranks or locates a deposit. It says so in the ' +
            'layer description too, so the disclaimer travels with the file.</div>';
        el.innerHTML = html;
        dlEl = el;
        placeDl(el, btn, highlight);
        // A HINT, NOT AN ACTION — the same rule as ?anim_export= and
        // ?aoi_menu_item=: a link whose only outcome is a build and a few tens
        // of MB must be a click the recipient makes, not a consequence of
        // opening a URL. So the row is pointed at and focused, never run.
        if (highlight) {
            var row = el.querySelector('.aoi-menu-item[data-item="' + highlight + '"]');
            if (row) {
                row.classList.add('highlight');
                row.setAttribute('tabindex', '0');
                try { row.focus({ preventScroll: true }); } catch (e) { }
            }
        }
        return;
    }

    /* The historical archive's download, in the SAME menu component as the
     * geology one. There is only one file, so this could have stayed the bare
     * link it was — but then the two drapes in one admin section would offer
     * their downloads two different ways, and only one of them would carry the
     * ⧉ that is the reliable way to get a link out of Safari (see dlRow).
     * One row, the app's row. */
    function openHistDownloadMenu(btn) {
        var already = dlEl;
        closeDlMenu();
        if (already) return;
        var m = histMeta() || {};
        var el = document.createElement('div');
        el.className = 'aoi-menu mode-menu ml-menu ml-menu-dl';
        el.dataset.kind = 'histdl';
        el.setAttribute('role', 'menu');
        el.setAttribute('aria-label', 'Download the historical maps');
        var html = '<div class="mode-menu-head">Download the archive</div>';
        if (!m.available || !m.download) {
            html += '<div class="mode-menu-note">No archive is installed on this server' +
                (m.reason ? ' (' + esc(m.reason) + ')' : '') + ', so there is nothing to download.</div>';
            el.innerHTML = html;
            dlEl = el;
            placeDl(el, btn);
            return;
        }
        var mb = m.size_bytes ? Math.round(m.size_bytes / 1048576) + ' MB' : '';
        html += dlRow('<a class="aoi-menu-item" data-item="mbtiles" ' +
            'href="' + esc(dlUrl(m.download)) + '" ' +
            'title="' + esc('The mosaicked sheet series as MBTiles, z' + (m.minzoom || 0) + '–' +
                (m.maxzoom || 0) + ', for Locus Map, OsmAnd or QGIS.') + '">' +
            '<i class="icon-scroll-text"></i><span>MBTiles</span>' +
            '<em>' + esc(m.name || 'archive') + (mb ? ', ' + mb : '') + '</em></a>',
            dlUrl(m.download), 'mbtiles');
        // The one thing about this file a reader has to be told BEFORE they
        // open it somewhere it looks empty. On screen the ink is lifted to
        // white so imagery stays readable; the file keeps the archive's own
        // near-black, because offline viewers default to light backgrounds.
        html += '<div class="mode-menu-note">Black ink, as printed — the white you see on ' +
            'the map is a paint property of the overlay, not a second archive. On a light ' +
            'background (the default in Locus, OsmAnd and QGIS) black is what you want.</div>';
        if (m.attribution) html += '<div class="mode-menu-note">' + esc(m.attribution) + '</div>';
        el.innerHTML = html;
        dlEl = el;
        placeDl(el, btn);
    }

    /* Placed WITHOUT touching menuEl, because the thing it hangs off is the
     * geology panel and that panel is what is being exported: place() would
     * make this little menu "the menu", and the next rebuild would rebuild the
     * wrong element. It closes on the next click anywhere else — including
     * inside the panel, which is the reader going back to adjusting the view. */
    function placeDl(el, btn, highlight) {
        document.body.appendChild(el);
        var r = btn.getBoundingClientRect();
        var w = el.offsetWidth, h = el.offsetHeight;
        el.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, r.right - w)) + 'px';
        el.style.top = (r.bottom + h + 6 > window.innerHeight
            ? Math.max(6, r.top - h - 4) : r.bottom + 4) + 'px';
        // A pointed-at row gets a moment before the next click can dismiss the
        // menu: a share link's landing click would otherwise close it.
        setTimeout(function () { document.addEventListener('click', closeDlMenu); },
                   highlight ? 400 : 0);
        // AND IT MUST NOT COME ADRIFT OF ITS ANCHOR. Over the map the arrow
        // cannot move under it; on the admin card the button sits in a
        // scrolling page section, and a fixed-position menu is then left
        // hanging off nothing.
        //
        // It FOLLOWS rather than closes. Closing would have been simpler and is
        // wrong twice over: the gesture that brings the button into view is
        // itself a scroll (`scrollIntoView`, smooth, still running when the
        // menu opens), so a share link pointing at a row closed the menu it had
        // just opened; and a reader who nudges the page while reading two
        // download descriptions has not asked for anything to go away. Capture,
        // so the section's own scroll reaches us; passive, because this never
        // prevents the scroll it is following.
        dlAnchor = btn;
        window.addEventListener('scroll', followDl, { capture: true, passive: true });
        window.addEventListener('resize', followDl);
    }

    /* Keep the menu on its button, and give up only when the button is gone
     * from the screen entirely — at which point the menu is pointing at
     * something the reader cannot see, which is the one case where staying
     * open is worse than closing. */
    function followDl() {
        if (!dlEl || !dlAnchor || !dlAnchor.isConnected) return closeDlMenu();
        var r = dlAnchor.getBoundingClientRect();
        if (r.bottom < 0 || r.top > window.innerHeight) return closeDlMenu();
        var w = dlEl.offsetWidth, h = dlEl.offsetHeight;
        dlEl.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, r.right - w)) + 'px';
        dlEl.style.top = (r.bottom + h + 6 > window.innerHeight
            ? Math.max(6, r.top - h - 4) : r.bottom + 4) + 'px';
    }

    function closeDlMenu(e) {
        // A click INSIDE this menu must not close it mid-download: the row
        // replaces its own label with "Building your view…", and losing the
        // element would lose the only progress the reader has.
        if (e && e.type === 'click' && dlEl && dlEl.contains(e.target)) return;
        if (dlEl) { dlEl.remove(); dlEl = null; }
        dlAnchor = null;
        // The card may have gone stale while the menu held it still.
        if (typeof geoMapPanelFlush === 'function') setTimeout(geoMapPanelFlush, 0);
        document.removeEventListener('click', closeDlMenu);
        window.removeEventListener('scroll', followDl, { capture: true });
        window.removeEventListener('resize', followDl);
        // The button a share link pointed at stops being the pointed-at one
        // the moment the reader dismisses the menu it opened.
        var hl = document.querySelector('.geo-cardbtn.highlight');
        if (hl) hl.classList.remove('highlight');
    }

    function dlUrl(path) {
        if (!path) return '#';
        var pwd = (typeof window.getPwd === 'function') ? (window.getPwd() || '') : '';
        if (!pwd) return path;
        return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'pwd=' + encodeURIComponent(pwd);
    }

    var dlEl = null;
    var dlAnchor = null;      // the button this menu hangs off; see followDl()
    var dlBusy = false;
    /* A download row the SHARE LINK asked the admin card to point at, held
     * until that card actually paints (it is built from a fetch and from the
     * rendered canvas, so it can be seconds late). Read once — pointing at a
     * row is a thing that happens on arrival, not a setting. */
    var geoAdminExport = '';

    /* The filtered build. A POST, so it cannot be an <a>: the body is the
     * resolved selection, and the answer is a file built for this one request.
     *
     * NOTHING HAPPENS ON SCREEN for the seconds a build takes, which reads as
     * a dead control — the same failure downloadGeoMapGPKG() exists for. So the
     * row says so, and a FAILURE is said out loud rather than swallowed: a
     * stale selection comes back 409 with the server\u2019s own sentence, which
     * tells the reader to reload rather than leaving them with no file and no
     * reason. */
    function downloadView() {
        if (dlBusy) return;
        if (typeof GeoMap === 'undefined' || !GeoMap.selection) return;
        var f = downloadFacts();
        var sel = GeoMap.selection();
        if (!sel.units.length) {
            if (typeof showToast === 'function') {
                showToast('Nothing is drawn here — pan onto a sheet or widen the filter; ' +
                    'an export of an empty view would be an empty file.', 'warning');
            }
            return;
        }
        sel.label = viewLabel();
        // The share link goes INTO the file, so a colleague who receives the
        // GeoPackage can get back to the map that produced it. buildShareUrl()
        // never carries the password (srv/guest.go, invariant 14).
        try {
            if (typeof buildShareUrl === 'function') sel.link = buildShareUrl();
        } catch (e) { /* a link we cannot build must not cost the download */ }

        dlBusy = true;
        var row = dlEl && dlEl.querySelector('.icon-crop');
        var label = row && row.parentNode;
        var was = label ? label.innerHTML : null;
        if (label) label.innerHTML = '<i class="icon-loader"></i><span>Building your view\u2026</span>' +
            '<em>' + f.units + ' unit(s)</em>';
        fetch(dlUrl(f.view), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sel)
        }).then(function (r) {
            if (!r.ok) return r.text().then(function (t) {
                // A 401 here serves the login PAGE — 20 KB of HTML is not a
                // sentence a toast can show.
                if (/^\s*</.test(t)) t = '';
                throw new Error(t || ('HTTP ' + r.status));
            });
            var name = 'geology-view.gpkg';
            var cd = r.headers.get('Content-Disposition') || '';
            var m = /filename="?([^";]+)/.exec(cd);
            if (m) name = m[1];
            return r.blob().then(function (b) { return { b: b, name: name }; });
        }).then(function (res) {
            var u = URL.createObjectURL(res.b);
            var a = document.createElement('a');
            a.href = u; a.download = res.name;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(function () { URL.revokeObjectURL(u); }, 30000);
            if (typeof showToast === 'function') {
                showToast('Your view is downloading — ' + res.name + ', ' + f.units +
                    ' unit(s)' + (f.lines ? ', ' + f.lines + ' junction type(s)' : '') +
                    ', plus the published workings it was scored against.', 'success');
            }
        }).catch(function (e) {
            if (typeof showToast === 'function') {
                showToast('That view could not be packaged — ' +
                    String(e.message || e).slice(0, 300), 'error');
            }
        }).then(function () {
            // The reset must be unconditional: dlBusy stuck true turns every
            // later click into a silent no-op ("works only once"). A `.then`
            // after a throwing `.catch` is skipped on rejection — that is
            // exactly how this stuck once — so both arms are handled here.
            dlBusy = false;
            if (label && was !== null && label.isConnected) label.innerHTML = was;
        }, function () {
            dlBusy = false;
            if (label && was !== null && label.isConnected) label.innerHTML = was;
        });
    }

    /* ── Placement, and why the geology one is not a popover ────────────
     *
     * The basemap menu is a MENU: it asks one question, takes one answer and
     * should vanish. The geology one stopped being that the day it became a
     * table — it is a legend for the map, read WHILE panning, comparing and
     * clicking units, and a popover that closes on the next click outside
     * itself cannot be read that way. Every gesture in the table also moves
     * the map underneath, so the reader was choosing between seeing the map
     * and seeing the key to it.
     *
     * So the geology one is a floating panel with the app's own furniture
     * (`.fui-bar`: grabber, collapse, close — the same bar the pinned-layers
     * box and the park popup wear), and the layers menu stays a popover.
     * Position and collapsed state persist, because a panel the user has
     * dragged out of the way must stay out of the way across a rebuild — and
     * this panel rebuilds on every gesture (refreshWhenDrawn).
     */
    var panelPos = null;        // {x,y} once dragged; null = anchored to the chip
    var panelCollapsed = false;
    var PANEL_LS = 'fui.geomenu';

    (function restorePanelState() {
        try {
            var v = JSON.parse(localStorage.getItem(PANEL_LS) || 'null');
            if (v && typeof v.x === 'number') panelPos = { x: v.x, y: v.y };
            if (v && v.collapsed) panelCollapsed = true;
        } catch (e) { /* a corrupt entry must not cost the reader the panel */ }
    })();

    function savePanelState() {
        try {
            localStorage.setItem(PANEL_LS, JSON.stringify(
                panelPos ? { x: panelPos.x, y: panelPos.y, collapsed: panelCollapsed }
                         : { collapsed: panelCollapsed }));
        } catch (e) { }
    }

    function clampToViewport(el, x, y) {
        var w = el.offsetWidth, h = el.offsetHeight;
        return {
            x: Math.max(6, Math.min(window.innerWidth - w - 6, x)),
            // Only the BAR has to stay on screen: a tall panel clamped by its
            // full height jumps upward the moment it grows, which reads as the
            // panel moving on its own.
            y: Math.max(6, Math.min(window.innerHeight - 28, y))
        };
    }

    function place(el, btn, opts) {
        document.body.appendChild(el);
        var floating = opts && opts.floating;
        if (floating && panelPos) {
            var p = clampToViewport(el, panelPos.x, panelPos.y);
            el.style.left = p.x + 'px';
            el.style.top = p.y + 'px';
        } else {
            var r = btn.getBoundingClientRect();
            var w = el.offsetWidth, h = el.offsetHeight;
            el.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, r.right - w)) + 'px';
            el.style.top = (r.bottom + h + 6 > window.innerHeight
                ? Math.max(6, r.top - h - 4) : r.bottom + 4) + 'px';
        }
        menuEl = el;
        if (floating) {
            bindPanelBar(el);
            return;     // a panel closes by its ×, not by the next click elsewhere
        }
        setTimeout(function () { document.addEventListener('click', closeMenu); }, 0);
    }

    /* The bar: drag to move, tap to collapse, buttons for the rest. Pointer
     * events (not mouse), so it works with a finger; the drag threshold is
     * what lets one bar be both a handle and a target. */
    function bindPanelBar(el) {
        var bar = el.querySelector('.ml-bar');
        if (!bar) return;
        var sx = 0, sy = 0, ox = 0, oy = 0, active = false, moved = false;
        bar.addEventListener('pointerdown', function (e) {
            if (e.button !== undefined && e.button !== 0) return;
            if (e.target.closest('button')) return;
            active = true; moved = false;
            sx = e.clientX; sy = e.clientY;
            var r = el.getBoundingClientRect();
            ox = r.left; oy = r.top;
            try { bar.setPointerCapture(e.pointerId); } catch (err) { }
        });
        bar.addEventListener('pointermove', function (e) {
            if (!active) return;
            var dx = e.clientX - sx, dy = e.clientY - sy;
            if (!moved && Math.hypot(dx, dy) < 6) return;
            moved = true;
            bar.classList.add('fui-dragging');
            var p = clampToViewport(el, ox + dx, oy + dy);
            el.style.left = p.x + 'px';
            el.style.top = p.y + 'px';
            panelPos = p;
            e.preventDefault();
        });
        var end = function () {
            if (!active) return;
            active = false;
            bar.classList.remove('fui-dragging');
            if (moved) savePanelState();
            else MapLegend.togglePanelCollapsed();
            moved = false;
        };
        bar.addEventListener('pointerup', end);
        bar.addEventListener('pointercancel', end);
    }


    /* ── The geology chip's own menu ─────────────────────────────────────
     *
     * The chip started as "the state and the way out": tap = switch this off.
     * That is right for the basemap and for the historical scan, which have no
     * inside — but geology does. The question this overlay exists to answer is
     * "which ground here can host gold", and that filter lived only in admin ▸
     * Map Settings, four clicks away, while the chip in front of the user
     * could do exactly one thing: destroy the layer they had just turned on.
     *
     * So an ALREADY-ON geology chip opens its own menu, in the same component
     * and the same vocabulary as the basemap one: checks (any-of, because
     * "gold + copper" is an ordinary question and a radio makes it
     * impossible), an "All units" row that clears the filter, and Hide at the
     * bottom. Turning geology ON is still one tap from the layers menu; only
     * the second tap became a choice instead of an undo.
     *
     * A commodity a sheet never mentions must not be offered — asking a sheet
     * to isolate cobalt it never names empties it — so the list is the UNION
     * across the installed sheets, counted from the catalogue, and the toggle
     * goes through geoToggleCommodityAll(), the panel's own function. One
     * implementation, so the strip and the panel cannot drift.
     */
    /* commodity -> how many units it would actually draw AT THE CURRENT
     * STRENGTH FLOOR. Counting every unit that merely mentions the commodity
     * would print "gold 36" next to a map showing 3 — a number that describes
     * a state nobody selected. */
    function commodityIndex() {
        var out = {};
        if (typeof GeoMap === 'undefined' || !GeoMap.sheets()) return out;
        var sheets = GeoMap.sheets();
        var min = GeoMap.minWeight ? GeoMap.minWeight() : 1;
        (GeoMap.order() || []).forEach(function (id) {
            if (!(sheets[id] || {}).available) return;
            (GeoMap.classes(id) || []).forEach(function (c) {
                (c.commodities || []).forEach(function (k) {
                    var w = GeoMap.commodityWeight ? GeoMap.commodityWeight(id, c.code, k) : 1;
                    if (w >= min) out[k] = (out[k] || 0) + 1;
                    else if (!(k in out)) out[k] = 0;   // keep the row: see hostMap()
                });
            });
        });
        return out;
    }

    /* ── What a commodity would actually DRAW ────────────────────────────
     *
     * The menu named eleven commodities and a count, which is a table of
     * contents, not a legend: "gold 36" says nothing about what the map turns
     * into, and the reader had to pick one to find out. Each row now carries
     * the swatches of the ground it would draw — age colour wearing its FGDC
     * ornament, exactly as on the polygon and in the key below — so the menu
     * IS the legend for the choice it is offering.
     *
     * It follows the strength floor, because that is the whole point: raising
     * the floor to `classic` drops gold from a dozen colours to the two
     * greenstone units, and seeing that happen is what makes the control
     * legible. Distinct by (age, lithology), most units first, capped like the
     * strip's key with a +n rather than growing without bound.
     */
    /* Toggle one commodity without redrawing the world — geoCell() changes
     * several at once and renders after. */
    function geoCommodityRaw(k) {
        if (typeof geoToggleCommodityAll === 'function') { geoToggleCommodityAll(k); return; }
        if (typeof GeoMap === 'undefined') return;
        var sheets = GeoMap.sheets() || {}, on = commodityOn(k);
        (GeoMap.order() || []).forEach(function (id) {
            if (!(sheets[id] || {}).available) return;
            var has = (GeoMap.classes(id) || []).some(function (c) {
                return (c.commodities || []).indexOf(k) >= 0;
            });
            if (has && GeoMap.commodityOn(id, k) === on) GeoMap.toggleCommodity(id, k);
        });
    }

    function commodityOn(k) {
        if (typeof GeoMap === 'undefined') return false;
        var sheets = GeoMap.sheets() || {};
        return (GeoMap.order() || []).some(function (id) {
            return (sheets[id] || {}).available && GeoMap.commodityOn(id, k);
        });
    }

    /* ── THE AFFINITY MATRIX ─────────────────────────────────────────────
     *
     * The menu used to be three surfaces describing one thing: a list of
     * eleven commodities, a strength ladder under it, and — in the strip
     * outside — the age key with its brackets. Each held a piece of the same
     * sentence ("cobalt is hosted by THIS ground, at THIS grade"), and the
     * reader had to hold the other two in their head while reading one.
     *
     * So it is one object: rock across the top, commodity down the side, and
     * the affinity in the cell. That is how this knowledge is written down in
     * every economic-geology text, and it answers in one glance the three
     * questions the three surfaces answered one at a time:
     *
     *   read a ROW    -> which ground hosts cobalt, and how well
     *   read a COLUMN -> what this rock is prospective for (nothing else in
     *                    the app could answer this at all)
     *   read the GRID -> where two commodities share ground, which is the
     *                    interesting case and was previously invisible
     *
     * The columns ARE the key strip's columns — the periods actually drawn in
     * this view, in the same order, wearing the same swatch. The menu is
     * therefore a legend for the map in front of the reader, not a catalogue
     * of the dataset: a period not on screen is not a column, because "what is
     * cobalt hosted by, in Sudan, in general" is not a question the map is
     * being asked.
     *
     * Interaction, one meaning per target:
     *   cell   -> show that commodity on that ground (and only that ground)
     *   row    -> show that commodity on every ground it has
     *   column -> hide that period, exactly as tapping its swatch in the key
     *
     * The grade is the cell's INK, not a number: ●●● full, ●● two-thirds, ●
     * faint. The floor then reads as what it is — a threshold on the ink —
     * and raising it visibly empties the weak half of the grid rather than
     * changing a count somewhere else.
     */

    /* (age, commodity) -> best grade among the units actually drawn. */
    function affinityGrid(ages) {
        var g = {}, unitN = {};
        if (typeof GeoMap === 'undefined' || !GeoMap.sheets()) return { g: g, n: unitN };
        var sheets = GeoMap.sheets();
        var want = {};
        ages.forEach(function (a) { want[a] = true; });
        (GeoMap.order() || []).forEach(function (id) {
            if (!(sheets[id] || {}).available) return;
            (GeoMap.classes(id) || []).forEach(function (c) {
                if (!want[c.age]) return;
                (c.commodities || []).forEach(function (k) {
                    var w = GeoMap.commodityWeight(id, c.code, k);
                    if (!w) return;
                    var kk = k + '|' + c.age;
                    g[kk] = Math.max(g[kk] || 0, w);
                    unitN[kk] = (unitN[kk] || 0) + 1;
                });
            });
        });
        return { g: g, n: unitN };
    }

    /* ── Contacts, as the second mode of the same table ─────────────────
     *
     * The matrix is rock × commodity. A contact is a pair of ROCKS, so it is
     * not an eleventh row and it is not another commodity: it is the same
     * table transposed onto itself — rock across, rock down, and in the cell
     * what that junction can host. Two modes of one object, one vocabulary,
     * one grade scale, one set of gestures.
     *
     * `mxMode` is which of the two is on screen. It is deliberately NOT the
     * same thing as whether the contact layer is drawn: a reader can look at
     * the junction table to decide, and the layer is what they decide TO.
     * Choosing a junction turns it on (GeoMap.setContactPair), because a cell
     * that changes nothing visible is a control that reads as broken.
     */
    var mxMode = 'rock';        // 'rock' | 'junction'
    // Did WE switch the contact layer on (by opening the junction tab), or did
    // the reader? Only our own doing may be undone automatically — silently
    // switching off a layer the reader turned on is the kind of "helpful"
    // that costs them the map they built.
    var autoContacts = false;

    // The model's lithology vocabulary is the row/column label here. The
    // legend's own words ("Unconsolidated sediment", "Ultramafic / ophiolite")
    // are written for a legend row three times this wide and would force the
    // menu wider than the panel it hangs off, so the table uses the SHORT form
    // and the tooltip carries the legend's wording verbatim.
    var LITH_SHORT = {
        alluvium: 'alluvium', sandstone: 'sandstone', mudrock: 'mudrock',
        carbonate: 'carbonate', intrusive: 'intrusive', volcanic: 'volcanic',
        metamorphic: 'metamorph.', ultramafic: 'ultramafic',
        ironstone: 'iron fm.', mixed: 'mixed'
    };

    function lithMeta(key) {
        var std = (typeof GeoMap !== 'undefined' && GeoMap.std) ? GeoMap.std() : null;
        var l = ((std && std.lithology) || []).filter(function (x) { return x.key === key; })[0];
        return l || { key: key, label: key, desc: '' };
    }

    function lithLabel(key) { return LITH_SHORT[key] || key; }

    /* Contacts as the strip and the menu need them: how many junction TYPES
     * exist, how many the model grades, and how many lines are actually drawn
     * right now. All three come from GeoMap, so no surface here derives a
     * number the map could disagree with. */
    function contactFacts() {
        var f = { any: false, reason: '', on: false, graded: true, pair: null,
                  pairs: new Set(), types: 0, gradedTypes: 0, drawn: 0, junctions: {} };
        if (typeof GeoMap === 'undefined' || !GeoMap.anyContacts) return f;
        f.any = !!GeoMap.anyContacts();
        f.reason = (GeoMap.contactsReason && GeoMap.contactsReason()) || '';
        if (!f.any) return f;
        f.on = !!(GeoMap.contactsOn && GeoMap.contactsOn());
        f.graded = !!(GeoMap.contactsGradedOnly && GeoMap.contactsGradedOnly());
        f.pairs = (GeoMap.contactPairs && GeoMap.contactPairs()) || new Set();
        f.pair = f.pairs.size ? Array.from(f.pairs)[0] : null;
        f.junctions = (GeoMap.junctions && GeoMap.junctions()) || {};
        Object.keys(f.junctions).forEach(function (k) {
            f.types++;
            if (f.junctions[k].best) f.gradedTypes++;
        });
        f.drawn = (GeoMap.drawnContactCount && GeoMap.drawnContactCount()) || 0;
        return f;
    }

    /* The junction currently picked, in words — "granite against greenstone"
     * for a cell, "intrusive junctions" for a header. One definition, used by
     * the chip, the key strip and the table's own state line, so the three
     * cannot describe the same filter differently. */
    /* The picked junctions in one phrase. Two are named; beyond that it says
     * how many, because a chip is one line and a list of five pairs is not a
     * phrase. One definition, used by the chip, the state line and the
     * junction table's foot, so the three cannot word one filter differently. */
    function junctionsWords(set) {
        var a = Array.from(set || []);
        if (!a.length) return '';
        if (a.length <= 2) return a.map(junctionWords).join(' + ');
        return a.length + ' junction types';
    }

    /* Picked cells in words. Same rule as the junctions: two are named in
     * full, more are counted — a chip is one line, and "cobalt in the
     * Archaean + copper in the Palaeoproterozoic + …" is not a line. */
    function cellWords(set) {
        var a = Array.from(set || []);
        if (!a.length) return '';
        var name = function (k) {
            var i = k.indexOf('|');
            var m = (typeof GeoMap !== 'undefined' && GeoMap.age) ? GeoMap.age(k.slice(i + 1)) : null;
            return { comm: k.slice(0, i).replace(/_/g, ' '),
                     age: (m && m.label) || k.slice(i + 1) };
        };
        var parts = a.map(name);
        // One commodity across several periods is the ordinary shape of this
        // gesture (three taps along the gold row), and "3 picked cells" throws
        // away the only word the reader cares about. Say the commodity and
        // count the periods.
        var comms = {};
        parts.forEach(function (p) { comms[p.comm] = 1; });
        var only = Object.keys(comms);
        if (only.length === 1 && parts.length > 2) {
            return only[0] + ' in ' + parts.length + ' periods';
        }
        if (parts.length > 2) return parts.length + ' picked cells';
        return parts.map(function (p) { return p.comm + ' in the ' + p.age; }).join(' + ');
    }

    function junctionWords(key) {
        if (!key) return '';
        if (key.indexOf('|') < 0) return lithLabel(key) + ' junctions';
        var p = key.split('|');
        return lithLabel(p[0]) + '/' + lithLabel(p[1]);
    }

    /* ── The panel's bar ────────────────────────────────────────────────
     *
     * "Hide geology" and "Map settings" used to be the last two rows of the
     * menu, BELOW the disclaimer — so the destructive action sat at the end of
     * a surface that scrolls, in the place a thumb lands after reading, and
     * after the sentence saying none of this is a deposit. They are chrome for
     * the whole panel, not steps in the reasoning, so they belong in its title
     * bar with the other window controls.
     *
     * It is the app's `.fui-bar` (grabber, then buttons) rather than a bar of
     * this panel's own invention: the pinned-layers box and the park popup
     * wear the same one, and a reader who has learned to drag one has learned
     * to drag all three. Order is by consequence, left to right: settings,
     * collapse, close.
     */
    /* What the bar and the chip say the drape is doing, in one place so the
     * two cannot word one map differently.
     *
     * A COUNT MUST NOT SURVIVE ITS SUBJECT: these are what the filters leave
     * in scope, which is right while a sheet is on screen and a lie the moment
     * none is ("36 units" over an empty Atlantic reads as a layer drawing
     * invisibly). Three sheets cover three countries, so "nothing here" is
     * ordinary.
     *
     * But EACH HALF ANSWERS FOR ITSELF. Units and contacts are drawn by
     * different filters and go out of view at different zooms, so a single
     * "not in view" for both was wrong exactly when it mattered: filtered down
     * to no units, with 67 junction lines painted across the screen, the bar
     * claimed nothing was there. Now the half with nothing to show says so and
     * the half that is drawing keeps its number.
     */
    function barCountText() {
        var k = drawnKinds();
        var cOn = typeof GeoMap !== 'undefined' && GeoMap.contactsOn && GeoMap.contactsOn();
        var units = (typeof GeoMap !== 'undefined' && GeoMap.drawnUnitCount)
            ? GeoMap.drawnUnitCount() : 0;
        var lines = cOn && GeoMap.drawnContactCount ? GeoMap.drawnContactCount() : null;
        if (!k.units && !k.contacts) {
            if (cOn && contactsPending) {
                return { html: 'counting\u2026', short: 'counting\u2026',
                    title: 'The contact layer has just been added and has not painted yet.' };
            }
            return { html: 'not in view', short: 'not in view',
                title: 'Geology is on, but no mapped sheet reaches this view — ' +
                       'pan to Sudan, the CAR or Tanzania.' };
        }
        var parts = [], words = [];
        if (k.units) {
            parts.push('<b>' + units + '</b> unit' + (units === 1 ? '' : 's'));
            words.push(units + ' unit(s)');
        } else if (cOn) {
            // The units half is the one that is absent, and it is absent HERE
            // rather than switched off — different statements, and only the
            // second would be a reason to reach for the "all" escape.
            parts.push('no units here');
            words.push('no mapped units in this view');
        }
        if (k.contacts && lines !== null) {
            parts.push('<b>' + lines + '</b> line' + (lines === 1 ? '' : 's'));
            words.push(lines + ' contact line(s)');
        } else if (cOn && contactsPending) {
            // NOT ZERO — not measured yet. The layer exists and has not
            // painted, so "no lines here" would be a claim about a canvas we
            // have not read. reMeasure() replaces this within a frame or two.
            parts.push('counting lines\u2026');
            words.push('the contact layer has just been added and has not painted yet');
        } else if (cOn) {
            parts.push('no lines here');
            words.push('no contact lines in this view');
        }
        return { html: parts.join(' · '), short: parts.join(' · ').replace(/<\/?b>/g, ''),
            title: words.join(' and ') +
                ' — counted from what the map paints, not from the catalogue.' };
    }

    function headRow() {
        var barCounts = barCountText();
        return '<div class="ml-bar fui-bar" role="toolbar" aria-label="Geology — drag to move, tap to collapse">' +
            '<i class="icon-mountain ml-bar-ico"></i><span class="ml-bar-t">Geology</span>' +
            // The panel is often collapsed or dragged aside, and then the bar
            // is the ONLY thing on screen saying what the drape is doing. So
            // it carries the two counts the tabs carry — units drawn, lines
            // drawn — from the same filters the paint uses. The lines half is
            // absent, not zero, when the layer is off: "0" is a measurement,
            // and "off" is not one.
            '<span class="ml-bar-n" title="' + esc(barCounts.title) + '">' +
            barCounts.html + '</span>' +
            '<span class="fui-grabber" aria-hidden="true"></span>' +
            '<span class="fui-bar-btns">' +
            '<button type="button" class="fui-bar-btn" data-geodl="1" title="' +
            esc(downloadBtnTitle()) + '"' +
            ' aria-label="Download the geology" aria-haspopup="menu"' +
            ' onclick="event.stopPropagation();MapLegend.downloadMenu(this)">' +
            '<i class="icon-download"></i></button>' +
            '<button type="button" class="fui-bar-btn" title="Full legend, opacity and downloads"' +
            ' aria-label="Map settings"' +
            ' onclick="event.stopPropagation();MapLegend.openSettings()">' +
            '<i class="icon-sliders-horizontal"></i></button>' +
            '<button type="button" class="fui-bar-btn" title="Collapse / expand"' +
            ' aria-label="Collapse the geology panel" aria-expanded="' + (!panelCollapsed) + '"' +
            ' onclick="event.stopPropagation();MapLegend.togglePanelCollapsed()">' +
            '<i class="icon-chevron-up"></i></button>' +
            '<button type="button" class="fui-bar-btn ml-bar-x" title="Close (the layer stays on)"' +
            ' aria-label="Close the geology panel"' +
            ' onclick="event.stopPropagation();MapLegend.close()">\u00d7</button>' +
            '</span></div>';
    }

    /* How many answers of each KIND a commodity has here — units that can
     * host it, and junction types graded for it — at the current floor.
     *
     * It is what the rock table's commodity rows put in their tooltip, so a
     * reader deciding between gold and copper can see that one is answered by
     * ground and the other mostly by edges. Both halves are counted the same
     * way the map filters them, never from the catalogue.
     */
    function commodityAnswers() {
        var out = {};
        var min = GeoMap.minWeight ? GeoMap.minWeight() : 1;
        var idx = commodityIndex();          // units, already at the floor
        Object.keys(idx).forEach(function (k) {
            out[k] = { rocks: idx[k] || 0, junctions: 0 };
        });
        var J = (GeoMap.junctions && GeoMap.junctions()) || {};
        Object.keys(J).forEach(function (key) {
            (J[key].affinity || []).forEach(function (a) {
                if (a.weight < min) return;
                (out[a.commodity] = out[a.commodity] || { rocks: 0, junctions: 0 }).junctions++;
            });
        });
        return out;
    }

    /* The MEASURED skill of a commodity's rules, as one short phrase.
     *
     * The panel's three amber dots are a grade, and a grade with no
     * measurement beside it reads as a ranking — which is exactly what a
     * reader hunting "where is the next gold rush" wants it to be. So every
     * row that offers a commodity says, in words, how that commodity's rules
     * scored against an occurrence dataset, and says "not measured" when they
     * never were (eight of ten: we hold an occurrence list for two).
     *
     * Kept as a TITLE and a footnote rather than a badge per row: it is a
     * sentence with a scope and an n, and a coloured pill next to a grade
     * would be a second grade to decode. srv/geomap_scores.go.
     */
    function skillNote(commodity, kind, min) {
        if (!GeoMap.skill) return '';
        var r = GeoMap.skill(commodity, kind, min || 1);
        var phrase = GeoMap.skillPhrase(r);
        var h = mxSkill(commodity, kind, min);
        // WHERE, always. Without it the sentence is read as a statement about
        // the map on screen, and the measurement was made on one sheet.
        if (h.where === 'elsewhere') {
            return 'not measured on the ground you are looking at; in ' + h.place +
                   ' it ' + phrase;
        }
        // TWO LISTS, TWO ANSWERS: the sentence names both rather than picking.
        // Two DIFFERENT reasons for that, and they need different words --
        // see skillHere()'s `reason`.
        if (h.verdict === 'mixed') {
            return (h.reason === 'places'
                ? 'scores differently on the countries in view \u2014 '
                : 'measured more than once on this ground, and the answers disagree \u2014 ') +
                evidenceText(h);
        }
        // AND WHAT IS STILL UNMEASURED, when a list holds the commodity but too
        // few sites to score it. Eastern CAR is the case: 4 gold sites, under
        // the floor, so "no one has checked there" is the honest addition to a
        // number measured in the west.
        var few = tooFewText(h);
        if (few) {
            return phrase + '; elsewhere only ' + few + ' — too few to score';
        }
        return phrase;
    }

    /* == DO NOT DRAW A CHOOSER THAT IS MEASURED NOT TO WORK HERE ==========
     *
     * b1ac4dc measured the two halves of this panel and they disagree: in the
     * CAR the gold JUNCTIONS hold 2.3x more of the known workings than the
     * same amount of ground picked at random, and the gold UNITS hold FEWER
     * than random ground does (0.63x). Both were rendered in the same amber,
     * with the same three dots, under the same disclaimer -- so the half that
     * fails and the half that works were typographically identical, and a
     * reader asking "where do I prepare for the next gold rush" was as likely
     * to be sent to the wrong one. A grade drawn without its score beside it
     * reads as a ranking (invariant 12).
     *
     * The interactive mixer stays; what changes is that its INK carries the
     * measurement:
     *
     *  - a cell whose kind is measured, HERE, not to beat random ground is
     *    drawn grey and hollow (`.unproven`), never amber. It stays present,
     *    tappable and counted -- removing it would claim the sheet does not
     *    mention the commodity, and the reader may well want that ground for
     *    another reason. It simply stops looking like a recommendation.
     *  - measured to beat random ground here: amber, and the panel says so.
     *  - nothing measured on this ground: amber as before. It is an inference,
     *    which is what it has always been, and "nobody has checked" is not the
     *    same statement as "checked and it fails".
     *
     * mxSkill/mxVerdict are the ONE place that decision is made, so the two
     * tables, the ladder, the tips and the routing line cannot disagree about
     * a commodity.
     */
    function mxSkill(commodity, kind, min) {
        if (typeof GeoMap === 'undefined' || !GeoMap.skillHere) {
            return { score: null, where: 'none', place: '' };
        }
        return GeoMap.skillHere(commodity, kind,
            min || (GeoMap.minWeight ? GeoMap.minWeight() : 1));
    }

    /* Which measured grounds the viewport last reached. Compared on `idle`; see
     * watchMap(). A string rather than a boolean because a view can reach two
     * measured sheets at once, and "which" is what changes the verdicts. */
    var skillScopeSeen = null;

    /* Does this (commodity, kind) chooser work on the ground IN VIEW?
     *   'works'      -- every list that measured here puts it above 1
     *   'fails'      -- every list puts it at or below 1: worse than area
     *   'split'      -- the lists that measured here DISAGREE across 1
     *   'unmeasured' -- no measurement speaks for this view
     * Four states, never two. "fails", "nobody looked" and "the two surveys of
     * this country contradict each other" are three different statements, and
     * only the first is a reason to draw a grade as a dead end. */
    function mxVerdict(commodity, kind, min) {
        var h = mxSkill(commodity, kind, min);
        if (h.where !== 'here' || !h.score) return 'unmeasured';
        // FOUR STATES, because the CAR has three independent occurrence lists
        // and on gold junctions they disagree: IPIS's field visits say 2.4x,
        // Tearline's permit imagery census says 0.0x. Folding that into either
        // 'works' or 'fails' picks a side of an open question on the reader's
        // behalf -- and whichever side is picked, the panel is then confidently
        // wrong for half the evidence. 'split' draws like neither.
        if (h.verdict === 'mixed') return 'split';
        return h.verdict === 'concentrates' ? 'works' : 'fails';
    }

    /* The evidence behind a verdict, as a sentence a reader can act on.
     *
     * Every list, named by its place and its n, in one line -- so "measured"
     * cannot hide the fact that two surveys of the same country reached
     * opposite conclusions. */
    function evidenceText(h) {
        if (!h || !h.all || !h.all.length) return '';
        return h.all.map(function (r) {
            return (GeoMap.skillLift ? GeoMap.skillLift(r.lift) : r.lift.toFixed(2) + 'x') +
                   ' (' + r.scope + ', n=' + r.n + ')';
        }).join(' vs ');
    }

    /* Lists that hold this commodity but too few sites to score, as words.
     *
     * "Nobody has checked eastern CAR" and "checked, and it fails" are
     * different statements; only this one distinguishes them, and it is the
     * honest answer for the half of the country IPIS never reached. */
    function tooFewText(h) {
        if (!h || !h.tooFew) return '';
        var parts = Object.keys(h.tooFew).map(function (eid) {
            return h.tooFew[eid] + ' site(s)';
        });
        return parts.length ? parts.join(', ') : '';
    }

    /* A lift, printed the one way, through GeoMap: two surfaces rounding one
     * measurement differently read as two measurements. */
    function liftText(h) {
        if (!h || !h.score) return '';
        return GeoMap.skillLift ? GeoMap.skillLift(h.score.lift)
                                : h.score.lift.toFixed(1) + '\u00d7';
    }

    /* The word "here", qualified when "here" is only part of the view.
     *
     * A viewport spanning the CAR and Tanzania reaches the measured ground and
     * two countries of unmeasured ground; "measured 0.63x here" over that view
     * claims the whole screen, which is the same over-reach as quoting the
     * number off-sheet. So when the view straddles, "here" becomes "in the
     * Central African Republic" — the place, never the sheet id. */
    function hereWords(h) {
        if (!h || h.where !== 'here') return 'here';
        return h.whole ? 'here' : 'in ' + h.place;
    }

    /* == THE ANSWER THE READER CAME FOR ==================================
     *
     * A park manager opens this panel with one question: where do I prepare
     * for the next gold rush (or cobalt, or coltan). On the one sheet where we
     * can check, the panel's honest answer is "not from the rock types -- from
     * the contacts between them", and nothing on screen said it: the reader
     * had to notice that two tooltips disagreed.
     *
     * So with a commodity picked, the panel states the measured finding for
     * that commodity on the ground in view and -- when one half works and the
     * other does not -- carries the route to the half that works as a button
     * in the same line. Same shape as the "show all" escape: a statement about
     * the map with its action in it.
     *
     * When nothing here is measured it says THAT, in those words. A confident
     * sentence would be worse than the old amber, because it would look like a
     * measurement. */
    function skillRouteHTML(sel, min, viewMode) {
        if (!sel || sel.size !== 1) return '';
        var com = Array.from(sel)[0];
        var name = com.replace(/_/g, ' ');
        var u = mxSkill(com, 'unit', min), j = mxSkill(com, 'junction', min);
        var uv = mxVerdict(com, 'unit', min), jv = mxVerdict(com, 'junction', min);
        var why = (u.score ? 'Rocks: ' + skillNote(com, 'unit', min) + '. ' : '') +
                  (j.score ? 'Junctions: ' + skillNote(com, 'junction', min) + '. ' : '') +
                  'Measured against an independent list of workings and never tuned to it ' +
                  '\u2014 the score is not a target.';
        if (uv === 'unmeasured' && jv === 'unmeasured') {
            var place = u.place || j.place;
            return '<div class="ml-skill unmeasured"><i class="icon-help-circle"></i>' +
                '<span title="' + esc('The model has only been scored where we hold an ' +
                    'independent list of workings' + (place ? ' (' + place + ')' : '') +
                    '. What is drawn here is inference from rock type, which is what it has ' +
                    'always been \u2014 it is simply not known to work on this ground.') + '">' +
                'No one has checked this model against real workings here' +
                (place ? ' \u2014 only in ' + esc(place) : '') + '</span></div>';
        }
        var cls = 'ml-skill', ico = 'icon-target', msg, act = '';
        // THE ROUTE IS ONLY A ROUTE FROM SOMEWHERE ELSE. A "use junctions"
        // button under the junction table is a control whose job is already
        // done — tapping it is a no-op, which is the "reads as broken" failure
        // this panel keeps removing. The SENTENCE stays in both tabs (it is the
        // finding, and the reader arriving by a share link needs it); only the
        // button is conditional on being in the other half.
        var here = (viewMode === 'junction') ? 'junction' : 'rock';
        // Which of the two carries the measurement decides which one's "here"
        // is quoted; they are the same sheet, so either does — but it must be
        // one of the two that actually HAS a score.
        var W = hereWords(j.score ? j : u);
        // DISAGREEMENT IS REPORTED BEFORE ANY ROUTE IS OFFERED. On CAR gold
        // junctions IPIS measures 2.4x and Tearline's permit census 0.0x on the
        // same claim; "use junctions" there would be routing a reader on one of
        // two contradictory measurements. The open question is the finding, and
        // the reader is the one who gets to weigh a national field survey
        // against a 4,000 km2 imagery census -- but only if we say both exist.
        if (jv === 'split' || uv === 'split') {
            var sp = (uv === 'split') ? u : j;
            var half = (uv === 'split') ? 'rock types' : 'junctions';
            cls += ' split'; ico = 'icon-help-circle';
            if (sp.reason === 'places') {
                // NOT A CONTRADICTION: the model works on one country's ground
                // and not on another's, and both are in view. That is a real
                // finding about the model, and the words for a contradiction
                // would turn it into doubt about the data. Conflating the two
                // was a live bug: over the Central African basin at zoom 5 the
                // viewport also reaches Sudan, and the CAR's own two lists
                // AGREE about gold units (0.63x, 0.06x) while Sudan scores
                // 1.91x -- "the surveys disagree" was the wrong sentence for it.
                msg = '<b>' + esc(name) + ': it depends where you are looking.</b> ' +
                      'The ' + half + ' score differently on the countries in ' +
                      'view \u2014 ' + esc(evidenceText(sp)) + '. Zoom to one ' +
                      'country for a verdict that speaks for your ground.';
            } else {
                msg = '<b>' + esc(name) + ': the surveys disagree.</b> For the ' + half +
                      ' ' + esc(W) + ', independent lists of known workings do not ' +
                      'agree whether this beats random ground \u2014 ' +
                      esc(evidenceText(sp)) + '. Treat it as an open question, not ' +
                      'as a target.';
            }
        } else if (jv === 'works' && uv === 'fails') {
            cls += ' route';
            msg = '<b>' + esc(name) + ': the contacts, not the rock types.</b> Graded ' +
                  'junctions hold ' + liftText(j) + ' more of the known workings than random ' +
                  'ground ' + esc(W) + '; these rock types hold ' + liftText(u) + ' \u2014 fewer.';
            if (here !== 'junction') {
                act = '<button type="button" title="' + esc('Open the Junctions table with ' + name +
                          ' carried over \u2014 the half of this model measured to work here') + '"' +
                      ' onclick="event.stopPropagation();MapLegend.mxMode(\'junction\')">' +
                      '<i class="icon-git-merge"></i>use junctions</button>';
            }
        } else if (uv === 'works' && jv === 'fails') {
            cls += ' route';
            msg = '<b>' + esc(name) + ': the rock types, not the contacts.</b> The graded units ' +
                  'hold ' + liftText(u) + ' more of the known workings than random ground ' +
                  esc(W) + '; the junctions hold ' + liftText(j) + '.';
            if (here !== 'rock') {
                act = '<button type="button" title="' + esc('Back to the rock table \u2014 the half ' +
                          'measured to work for ' + name + ' here') + '"' +
                      ' onclick="event.stopPropagation();MapLegend.mxMode(\'rock\')">' +
                      '<i class="icon-table-2"></i>use rocks</button>';
            }
        } else if (uv === 'fails' && jv === 'fails') {
            cls += ' bad'; ico = 'icon-ban';
            msg = 'Neither half of this model beats random ground for <b>' + esc(name) +
                  '</b> ' + esc(W) + ' (rocks ' + liftText(u) + ', junctions ' + liftText(j) +
                  '). Read it as background geology, not as a target.';
        } else if (uv === 'works' && jv === 'works') {
            msg = 'Both halves beat random ground for <b>' + esc(name) + '</b> ' + esc(W) +
                  ' \u2014 rocks ' + liftText(u) + ', junctions ' + liftText(j) + '.';
        } else {
            // One measured, the other never was. The measured half speaks, and
            // the silent half is named as SILENT, not as weaker.
            var m = (uv === 'unmeasured') ? j : u;
            var mw = (uv === 'unmeasured') ? 'junctions' : 'rock types';
            var sw = (uv === 'unmeasured') ? 'rock types' : 'junctions';
            var ok = ((uv === 'unmeasured') ? jv : uv) === 'works';
            if (!ok) { cls += ' bad'; ico = 'icon-ban'; }
            msg = 'For <b>' + esc(name) + '</b> the ' + mw + ' ' +
                  (ok ? 'hold ' + liftText(m) + ' more of the known workings than random ground'
                      : 'do no better than random ground (' + liftText(m) + ')') +
                  ' ' + esc(hereWords(m)) + '; the ' + sw + ' have never been scored.';
        }
        return '<div class="' + cls + '"><i class="' + ico + '"></i>' +
            '<span title="' + esc(why) + '">' + msg + '</span>' + act + '</div>';
    }

    /* ── ONE MIXER, TWO PLACES IT CAN BE READ ────────────────────────────
     *
     * This is the body of the geology chooser — the state line, the strength
     * ladder, the two tables — with NO furniture around it. It is a string,
     * because it has two homes: the floating panel over the map (openGeoMenu,
     * below) and the Geology card in admin ▸ Map Settings.
     *
     * It is ONE function rather than two similar ones on purpose. The card in
     * admin used to be a second, older chooser for the same layer: amber pill
     * chips, a 63-row unit list, its own opacity slider and no junction table
     * at all. Two surfaces for one piece of state is the shape of bug where a
     * reader changes one, looks at the other, and concludes the app has lost
     * their selection — and the older one silently could not express half the
     * current state (picked cells, junctions, the measured lift). Everything
     * here reads GeoMap, so both homes show the same answer by construction.
     */
    function geoBodyHTML() {
        var idx = commodityIndex();
        var keys = Object.keys(idx).sort();
        var anyOn = keys.some(commodityOn);
        var min = GeoMap.minWeight ? GeoMap.minWeight() : 1;

        // The columns are the key strip's columns: the periods DRAWN in this
        // view, most of it first. Capped, because a matrix wider than the menu
        // is a horizontal scroll nobody finds — and the cap announces itself.
        var COLS = 7;
        var drawn = columnEntries();
        var byAgeKey = {};
        drawn.forEach(function (e) { byAgeKey[e.key] = e; });
        // The order is frozen for this view (mxOrder); only the state of each
        // column is live, so a pick cannot move the next cell the reader is
        // reaching for.
        var frozen = mxOrder(drawn.map(function (e) { return e.key; }),
                             Object.keys(commodityIndex()).sort());
        drawn = frozen.cols.map(function (k) { return byAgeKey[k]; })
                           .filter(function (e) { return !!e; });
        var cols = drawn.slice(0, COLS);
        var colExtra = drawn.length - cols.length;
        var ages = cols.map(function (c) { return c.key; });
        var A = affinityGrid(ages);

        var html = '';

        /* ── WHERE AM I, AND HOW DO I GET OUT ────────────────────────
         *
         * Every gesture in the matrix NARROWS (a cell picks one commodity on
         * one period, the floor drops grades, a column hides a period) and
         * none of them widened. Three taps in, the reader is looking at two
         * units and the only route back was a row buried in the list. That is
         * a trap, and it is this app's "a subset must announce itself" rule
         * owed one step further: a subset must also be *escapable* from where
         * it is visible.
         *
         * So the menu opens with a line that says what is being drawn, in
         * words, and — whenever that is not everything — carries the way back
         * as the primary action in the same row. It is the same object in both
         * states, so it does not appear and disappear under the reader's
         * thumb.
         */
        var CF = contactFacts();
        var narrowed = [];
        var pickedCells = (GeoMap.picks && GeoMap.picks()) || new Set();
        if (pickedCells.size) narrowed.push(cellWords(pickedCells));
        if (anyOn) {
            narrowed.push(Array.from(GeoMap.selectedCommodities())
                .map(function (c) { return c.replace(/_/g, ' '); }).join(' + ') + ' hosts');
        }
        if (min > 1) narrowed.push(min === 3 ? 'classic grade only' : 'likely grade and up');
        var offN = GeoMap.agesOff ? GeoMap.agesOff().size : 0;
        // Counted from STATE, not from the rendered sample: the menu is built
        // the instant the filter changes, before MapLibre has re-rendered, so
        // a count read off the canvas here would report the map as it was one
        // gesture ago — which is exactly the kind of number that reads as
        // truth and is not.
        if (offN) narrowed.push(offN + ' period' + (offN === 1 ? '' : 's') + ' hidden');
        // A picked junction narrows the CONTACT layer, so it belongs in the
        // same sentence — said ONCE, here, and never repeated by the tab, the
        // switch or the chip.
        if (CF.on && CF.pairs.size) narrowed.push(junctionsWords(CF.pairs) + ' contacts');
        html += '<div class="ml-state' + (narrowed.length ? ' narrowed' : '') + '">' +
            '<i class="' + (narrowed.length ? 'icon-funnel' : 'icon-layers') + '"></i>' +
            '<span>' + (narrowed.length
                ? 'Showing ' + esc(narrowed.join(', '))
                : 'Showing every mapped unit') + '</span>' +
            (narrowed.length
                ? '<button type="button" title="Back to every mapped unit \u2014 clears the ' +
                  'commodity, the grade, any period you hid and any junction you picked" ' +
                  'onclick="event.stopPropagation();MapLegend.geoAll()">' +
                  '<i class="icon-rotate-ccw"></i>show all</button>'
                : '') + '</div>';

        if (!keys.length) {
            return html + '<div class="mode-menu-note">No sheet installed here lists a commodity affinity.</div>';
        }

        if (!cols.length && !(CF.on && contactHits)) {
            // Nothing drawn here: the matrix would be a grid of empty cells,
            // which reads as "nothing is prospective" rather than as "no rock
            // is on screen". Say the true thing instead.
            return html + '<div class="mode-menu-note">No mapped sheet reaches this view, so there is ' +
                'no rock here to describe. Pan to Sudan, the CAR or Tanzania.</div>';
        }

        // NO GROUND, BUT EDGES: the fills are filtered or off-sheet here and
        // the junction lines are painting anyway. The reader tapped the chip
        // to find out what those orange lines are, and the rock table cannot
        // answer — it would be a grid of empty cells over a map that is
        // visibly drawing something. So the table opens on the half that has
        // an answer. Not a preference change: the reader's own tab choice is
        // restored the moment a unit is back in view.
        var viewMode = (!cols.length && CF.any) ? 'junction' : mxMode;

        /* ── Two modes of one table ─────────────────────────────────
         *
         * Rock × commodity, and rock × rock. They are the same object asked
         * two ways ("what can this ground host" / "what can this junction
         * host"), so they are one surface with a two-tab switch rather than a
         * table plus a checkbox that produces a different table somewhere
         * else. The tab says how many junction types the sheets have; it is
         * NOT a second copy of the layer switch, which lives inside the
         * junction view where its subject is.
         */
        // The strength floor comes before the tables: it is the legend for the
        // ink in BOTH of them, and one control for one piece of state.
        //
        // The commodity does NOT sit here. In the rock table it is the row —
        // the reader picks gold by tapping the gold row, which also shows them
        // what gold is hosted by — so a chip strip above would be the same
        // selection offered twice, six lines from itself. The junction table
        // has both axes spent on rock and no row to tap, so it carries the
        // chips (junctionTableHTML), and the selection is carried over between
        // the two.
        html += gradeLadderHTML(anyOn);

        if (CF.any || CF.reason) {
            // EACH TAB COUNTS WHAT IT DRAWS. The two halves of the drape are
            // both on at once — fills and lines — so a tab that names its
            // table but not its layer leaves the reader guessing which of the
            // two the map is currently showing. Both numbers come from the
            // same filters the paint uses (GeoMap.drawnUnitCount /
            // drawnContactCount), so a count can never describe a map that is
            // not on screen.
            var unitsDrawn = (GeoMap.drawnUnitCount && GeoMap.drawnUnitCount()) || 0;
            // A tab that cannot answer says so rather than opening an empty
            // grid: with no unit in view the rock table has nothing to
            // describe, and a live-looking tab that does nothing on tap is the
            // "control that reads as broken" this surface keeps avoiding.
            var rockDead = !cols.length;
            html += '<div class="ml-mode" role="tablist" aria-label="What the table describes">' +
                '<button type="button" role="tab" class="ml-mode-b' +
                    (viewMode === 'rock' ? ' on' : '') + (rockDead ? ' dead' : '') +
                    '" aria-selected="' + (viewMode === 'rock') + '"' +
                    (rockDead ? ' aria-disabled="true"' : '') +
                    ' title="' + esc(rockDead
                        ? 'No mapped unit reaches this view, so there is no rock here to describe \u2014 ' +
                          'pan to Sudan, the CAR or Tanzania, or clear the filters.'
                        : 'What each rock can host \u2014 ' + unitsDrawn +
                          ' unit(s) drawn right now') + '"' +
                    ' onclick="event.stopPropagation();' +
                    (rockDead ? 'void 0' : 'MapLegend.mxMode(\'rock\')') + '">' +
                    '<i class="icon-table-2"></i>Rocks<em>' +
                    (rockDead ? 'none here' : unitsDrawn) + '</em></button>' +
                '<button type="button" role="tab" class="ml-mode-b' +
                    (viewMode === 'junction' ? ' on' : '') + (CF.any ? '' : ' dead') +
                    (CF.on ? ' lit' : '') +
                    '" aria-selected="' + (viewMode === 'junction') + '"' +
                    (CF.any ? '' : ' aria-disabled="true"') +
                    ' title="' + esc(CF.any
                        ? 'Where two rocks MEET \u2014 ' + CF.gradedTypes + ' of ' + CF.types +
                          ' junction types on these sheets are a setting the model grades' +
                          (CF.on ? '. ' + CF.drawn + ' contact line(s) drawn right now.'
                                 : '. No contact lines are drawn; open this tab to pick some.')
                        : 'Contact zones: ' + (CF.reason || 'not built on this server')) + '"' +
                    ' onclick="event.stopPropagation();' +
                    (CF.any ? 'MapLegend.mxMode(\'junction\')' : 'void 0') + '">' +
                    '<i class="icon-git-merge"></i>Junctions' +
                    '<em>' + (CF.any ? (CF.on ? CF.drawn : CF.types) : 'soon') + '</em></button>' +
                '</div>';
        }

        if (viewMode === 'junction' && CF.any) {
            return html + junctionTableHTML(CF);
        }

        html += '<div class="mode-menu-head ml-mx-head">' +
            '<i class="icon-table-2"></i>What each rock can host' +
            '<em>rock across, commodity down</em></div>';
        html += '<div class="ml-mx" style="grid-template-columns:minmax(66px,1fr) repeat(' +
            cols.length + ',22px) auto;">';

        // Column headers: the age swatch itself, so the matrix and the key
        // strip are visibly the same columns. Tapping one hides that period —
        // the same gesture as tapping it in the strip, because it is the same
        // thing.
        html += '<span class="ml-mx-corner"></span>';
        cols.forEach(function (c) {
            // The matrix's column head was 16x11 and drew the hatch anyway,
            // which at 11 px is the failure this floor exists for: the same
            // class read as one rock in the strip and another here. It is
            // 16x13 now — the same swatch, the same size class, the same
            // ornament as the key it mirrors.
            var bg = swatchBG(c.lith, c.meta.color, 13);
            // A column the current picks leave off the map keeps its place,
            // dimmed: it is what the reader would pick NEXT, and dropping it
            // is what made the table collapse to one column in the first
            // place. Dim rather than absent, so the header reads as "this is
            // on the sheets here, not on your map".
            html += '<button type="button" data-mx="col:' + esc(c.key) + '" class="ml-mx-col' +
                (c.offSelection ? ' dim' : '') + '" title="' +
                esc(c.meta.label + (c.offSelection
                    ? ' — on these sheets here, but not in what you have picked'
                    : ' — tap to hide this period')) + '"' +
                ' aria-label="' + esc(c.meta.label) + ', hide"' +
                ' onclick="event.stopPropagation();MapLegend.toggleAge(\'' + esc(c.key) + '\')">' +
                '<i style="' + bg + '"></i></button>';
        });
        html += '<span class="ml-mx-corner">' +
            (colExtra ? '<b title="' + colExtra + ' more period(s) drawn here than fit this table \u2014 ' +
                'the key strip below the chip lists them all">+' + colExtra + '</b>' : '') + '</span>';

        // One row per commodity. Ordered by how much of THIS view answers it
        // (cells present, then grade), not alphabetically: a menu sorted by
        // the alphabet is a dictionary, and the reader is asking what is under
        // them, not what starts with 'c'.
        // Ordered by how much of THIS view answers each commodity — but
        // ordered ONCE per view (mxOrder), because the grade floor and the
        // picks both change that measure, and a row that moves between two
        // taps of the same gesture is the reader's aim thrown away.
        var rows = frozen.rows.filter(function (k) { return keys.indexOf(k) >= 0; })
            .map(function (k) {
                var cells = 0, best = 0;
                ages.forEach(function (a) {
                    var w = A.g[k + '|' + a] || 0;
                    if (w >= min) { cells++; best = Math.max(best, w); }
                });
                return { k: k, cells: cells, best: best };
            });
        if (!frozen.sorted) {
            rows.sort(function (a, b) {
                return (b.best - a.best) || (b.cells - a.cells) || a.k.localeCompare(b.k);
            });
            frozen.rows = rows.map(function (r) { return r.k; });
            frozen.sorted = true;
        }

        var ANS = commodityAnswers();
        rows.forEach(function (r) {
            var k = r.k, on = commodityOn(k);
            // THE ROW CARRIES ITS OWN VERDICT FOR THIS TABLE'S KIND. The rock
            // table offers units, so it is scored on units: in the CAR the
            // gold units are measured at 0.63x, and a gold row drawn like the
            // rest is the panel recommending ground that is worked LESS often
            // than the sheet as a whole. Marked, never hidden — hiding it
            // would say no sheet here mentions gold.
            var uV = mxVerdict(k, 'unit', min);
            var jV = mxVerdict(k, 'junction', min);
            var uS = mxSkill(k, 'unit', min);
            // A commodity with nothing on screen keeps its row, greyed: an
            // absent row reads as "no sheet here mentions cobalt", which is a
            // different and wrong statement.
            html += '<button type="button" data-mx="row:' + esc(k) + '" class="ml-mx-row' + (on ? ' on' : '') +
                (r.cells ? '' : ' dead') +
                (uV === 'fails' ? ' unproven' : '') +
                (uV === 'split' ? ' contested' : '') +
                (uV === 'works' ? ' proven' : '') + '"' +
                ' role="menuitemcheckbox" aria-checked="' + on + '"' +
                ' title="' + esc(r.cells
                    ? (on ? 'Stop showing ' + k.replace(/_/g, ' ') + ' hosts'
                          : 'Show the ' + idx[k] + ' unit(s) that can host ' +
                            k.replace(/_/g, ' ')) +
                      ' \u2014 every period it has, on all sheets' +
                      // The other half of the answer. A commodity is answered
                      // by GROUND and by EDGES, and the junction count is the
                      // only place a reader learns that the Junctions tab has
                      // something to say about the commodity they are choosing.
                      (ANS[k] && ANS[k].junctions
                        ? '. ' + ANS[k].junctions + ' junction type(s) too \u2014 see the ' +
                          'Junctions tab.' : '') +
                      (GeoMap.agesOff().size
                        ? ' Clears the single-period narrowing you are in.' : '')
                    : 'Nothing drawn in this view is graded a host for ' + k.replace(/_/g, ' ') +
                      ' at this strength') +
                    // MEASURED, not claimed. Both kinds, because they score
                    // differently and the reader is about to pick one: on CAR
                    // the gold JUNCTIONS concentrate 2.3x and the gold UNITS
                    // do worse than random ground, and a row that offers the
                    // units without saying so is selling the junctions' number.
                    ' \u2014 rocks: ' + esc(skillNote(k, 'unit', min)) +
                    '; junctions: ' + esc(skillNote(k, 'junction', min)) +
                    (uV === 'fails' && jV === 'works'
                        ? '. FOR THIS COMMODITY THE ROCK TYPES ARE THE WRONG HALF of this ' +
                          'model here \u2014 use the Junctions tab.' : '') + '"' +
                ' onclick="event.stopPropagation();MapLegend.geoCommodity(\'' + esc(k) + '\')">' +
                '<span class="mode-mark check"></span>' + esc(k.replace(/_/g, ' ')) +
                // The measured lift, ON the row that offers the choice. A
                // sentence in a tooltip is not read before a tap; this is.
                // Absent when unmeasured — a blank is honest, a "?" is a grade.
                (uV === 'unmeasured' ? ''
                    : '<span class="ml-mx-lift ' + uV + '" title="' +
                      esc(uV === 'split'
                          ? 'The lists disagree here: ' + evidenceText(uS) +
                            '. The lower number is shown, never the flattering one.'
                          : skillNote(k, 'unit', min)) + '">' +
                      (uV === 'split' ? '?\u2009' : '') + liftText(uS) + '</span>') +
                '</button>';
            ages.forEach(function (a) {
                var w = A.g[k + '|' + a] || 0;
                var n = A.n[k + '|' + a] || 0;
                var faded = w && w < min;
                if (!w) {
                    html += '<span class="ml-mx-cell empty" aria-hidden="true"></span>';
                    return;
                }
                var meta = GeoMap.age(a);
                var picked = GeoMap.cellPicked && GeoMap.cellPicked(k, a);
                html += '<button type="button" data-mx="cell:' + esc(k) + '|' + esc(a) +
                    '" class="ml-mx-cell g' + w + (faded ? ' below' : '') +
                    // Measured NOT to work on the ground in view: grey, not
                    // amber. The gesture is unchanged — the reader can still
                    // draw it — but the cell stops reading as a target.
                    // 'contested' is its own ink: two surveys of this ground
                    // disagree, which is neither a target nor a dead end.
                    (uV === 'fails' ? ' unproven' : '') +
                    (uV === 'split' ? ' contested' : '') +
                    (picked ? ' picked' : (on ? ' on' : '')) + '"' +
                    ' aria-pressed="' + (!!picked) + '"' +
                    ' title="' + esc(meta.label + ' \u2014 ' + WEIGHT_WHY[w] + ' for ' +
                        k.replace(/_/g, ' ') + ' (' + n + ' unit(s))' +
                        (uV === 'fails'
                            ? '. MEASURED ' + hereWords(uS).toUpperCase() + ' AT ' + liftText(uS) +
                              ' vs random ground: this grade does not pick out worked ground ' +
                              'there' + (jV === 'works' ? ' \u2014 the junctions do' : '') + '.'
                            : uV === 'works'
                                ? '. Measured ' + hereWords(uS) + ' at ' + liftText(uS) + ' vs random ground.'
                                : '') +
                        (picked ? ' Picked \u2014 tap to drop it from the map.'
                                : faded
                            ? ' Below the strength floor; tapping lowers the floor and adds it.'
                            : ' Tap to ADD this ground to what the map draws.')) + '"' +
                    ' aria-label="' + esc(k.replace(/_/g, ' ') + ', ' + meta.label + ', grade ' + w +
                        (uV === 'fails' ? ', measured no better than random ground here' : '')) + '"' +
                    ' onclick="event.stopPropagation();MapLegend.geoCell(\'' + esc(k) + '\',\'' +
                        esc(a) + '\',' + w + ')"><i></i></button>';
            });
            html += '<em class="ml-mx-n">' + idx[k] + '</em>';
        });
        html += '</div>';

        // The route, under the table that offers the choice: with a commodity
        // picked, what the measurement says about THIS ground and which half
        // of the model to use. See skillRouteHTML().
        html += skillRouteHTML(GeoMap.selectedCommodities
            ? GeoMap.selectedCommodities() : new Set(), min, 'rock');

        // The continental setting, under both tables (this one and the
        // junction view): a fault or a margin is not a rock and not a sheet
        // junction, so it sits outside the matrix — but it is geology, and
        // this panel is where geology is operated.
        html += structuralFootHTML();

        html += '<div class="mode-menu-note">An inference from rock type \u2014 nothing here ' +
            'counts, ranks or locates a deposit.</div>';

        return html;
    }

    /* ── Structural context rows ─────────────────────────────────
     *
     * Faults and craton margins in the SAME panel as the rest of the
     * geology: they lived only in admin ▸ Map Settings ▸ Advanced, which
     * made them a different kind of thing from the contacts — and they are
     * not; they are the continental-scale version of the junction question.
     *
     * Deliberately OUTSIDE the matrix and the amber ramp: these lines are
     * ungraded context, and a graded cell would claim a grade nobody
     * computed (invariant 12). Each row wears its own map ink as its swatch
     * (a fault is a red dash, a margin is a violet band — the same ink
     * geomap.js paints), and carries its measured lifts from the CATALOGUE,
     * never typed: the gold-near-a-margin lift is the number a reader choosing
     * these layers is owed, and "unmeasured" is a word, not a blank.
     */
    function structuralFootHTML() {
        var s = (typeof GeoMap !== 'undefined' && GeoMap.structural) ? GeoMap.structural() : null;
        if (!s) return '';
        var rows = '';
        ['active_faults', 'craton_edges'].forEach(function (id) {
            var e = s[id];
            if (!e || !e.available) return;   // admin's Advanced block names the reason
            var on = GeoMap.structuralOn(id);
            var sw = id === 'craton_edges'
                ? '<i class="ml-strx-sw band"></i>'
                : '<i class="ml-strx-sw dash"></i>';
            var lifts = e.skill && e.skill.lifts;
            var liftTxt, title;
            if (lifts) {
                var parts = Object.keys(lifts).sort(function (a, b) {
                    return lifts[b].lift - lifts[a].lift;
                }).map(function (c) {
                    return c.replace(/_/g, ' ') + ' ' + lifts[c].lift.toFixed(1) + '\u00d7';
                });
                liftTxt = parts.join(', ');
                title = (e.notice || '') + ' \u2014 Share of known workings within ' +
                    Math.round(e.skill.near_km || 25) + ' km vs random ground, measured on ' +
                    (e.skill.scope || '') + '. Above 1\u00d7 concentrates them; below 1\u00d7 is ' +
                    'worse than random ground. Measured on eastern DR Congo \u2014 ground none ' +
                    'of these sheets covers.';
            } else {
                liftTxt = 'unmeasured';
                title = (e.notice || '') + ' \u2014 never scored against a workings list.';
            }
            rows += '<button type="button" class="ml-strx-row' + (on ? ' on' : '') + '"' +
                ' role="menuitemcheckbox" aria-checked="' + on + '"' +
                ' title="' + esc(title) + '"' +
                ' onclick="event.stopPropagation();MapLegend.toggleStructural(\'' + id + '\')">' +
                '<span class="mode-mark check"></span>' + sw +
                esc(e.label || id) +
                '<span class="ml-strx-lift' + (lifts ? '' : ' unm') + '">' + esc(liftTxt) + '</span>' +
                '<em class="ml-mx-n">' + (e.n || 0) + '</em></button>';
        });
        if (!rows) return '';
        return '<div class="mode-menu-head ml-strx-head"><i class="icon-git-merge"></i>' +
            'Structural setting<em>continental, ungraded</em></div>' + rows;
    }

    /* The floating panel over the map: the mixer, wearing the app's own
     * window furniture (`.fui-bar` — grabber, download arrow, collapse,
     * close). The bar belongs to THIS home only; the admin card has the
     * admin panel's own chrome, and giving it a second close button would
     * offer to close a card that is a section of a page. */
    function openGeoMenu(btn) {
        var already = menuEl && menuEl.dataset.kind === 'geo';
        closeMenu();
        if (already) return;
        var el = document.createElement('div');
        el.className = 'aoi-menu mode-menu ml-menu ml-menu-geo ml-panel' +
            (panelCollapsed ? ' ml-panel-collapsed' : '');
        el.dataset.kind = 'geo';
        // A dialog, not a menu: it stays open while the reader works the map,
        // and calling it a menu would promise a screen reader that Escape and
        // the next click dismiss it.
        el.setAttribute('role', 'dialog');
        el.setAttribute('aria-label', 'Geology');
        el.innerHTML = headRow() + geoBodyHTML();
        place(el, btn, { floating: true });
    }

    /* The floor, as a threshold on the ink the reader has just been looking
     * at. Always present, not only once something is selected: it is the
     * legend for the cells (what a full dot means) as much as it is a control,
     * and a grid whose ink is unexplained is the picture-of-a-legend failure
     * again.
     *
     * ONE ladder for BOTH modes of the table. The floor is a property of the
     * question ("count as a host"), not of the view, and a second copy under
     * the junction table would be a second control for one piece of state \u2014
     * the reader would then have to discover that they are the same.
     */
    function gradeLadderHTML(anyOn) {
        var min = GeoMap.minWeight ? GeoMap.minWeight() : 1;
        var n = GeoMap.weightCounts ? GeoMap.weightCounts() : null;
        var html = '<div class="ml-grade" role="radiogroup" aria-label="Minimum strength of affinity">';
        html += '<span class="ml-grade-lbl">count as a host</span>';
        [[1, 'any'], [2, 'likely'], [3, 'classic']].forEach(function (lv) {
            var w = lv[0];
            var dead = anyOn && n && !n[w];
            html += '<button type="button" class="ml-grade-b' + (min === w ? ' on' : '') +
                (dead ? ' dead' : '') + '" role="radio" aria-checked="' + (min === w) + '"' +
                ' title="' + esc(WEIGHT_WHY[w] + (anyOn && n
                    ? (dead ? '. No unit in this selection is graded that highly.'
                            : '. Leaves ' + n[w] + ' unit(s).') : '')) + '"' +
                ' onclick="event.stopPropagation();MapLegend.geoMinWeight(' + w + ')">' +
                '<span class="ml-grade-dot g' + w + '"><i></i></span>' + esc(lv[1]) +
                (anyOn && n ? '<em>' + n[w] + '</em>' : '') + '</button>';
        });
        return html + '</div>';
    }

    /* ── THE JUNCTION TABLE ──────────────────────────────────────────────
     *
     * Rock down, rock across, and in the cell what that junction can host —
     * the affinity matrix transposed onto itself, which is the shape the
     * knowledge has ("intrusive against carbonate is the skarn setting") and
     * the only shape that can state a property of a BOUNDARY. Neither unit
     * alone says it, so it can never be an eleventh commodity row.
     *
     * Four decisions, each of which the brief asked for or a screenshot
     * forced:
     *
     *  - UPPER TRIANGLE ONLY. A junction is unordered, so a full square
     *    prints every pair twice — the "copper twice" this pass exists to
     *    remove. The mirrored half is drawn as blank space, so the eye reads
     *    the triangle as a triangle rather than as a table with holes.
     *  - ONLY THE ROCKS THESE SHEETS ACTUALLY JOIN. The 10-lithology cross
     *    product is 55 cells, most of them junctions no sheet has, and a cell
     *    for a junction that does not exist is a claim that it exists and is
     *    barren. Rows and columns come from the junction index, so the table
     *    is as wide as the data and no wider.
     *  - THE CELL IS THE INK, exactly as in the rock matrix: ●●● classic, ●●
     *    likely, ● weak, hollow = graded but below the floor, a faint dot =
     *    these two rocks meet and the model has nothing to say. One scale,
     *    one rendering, in both modes.
     *  - IT MUST NOT WIDEN THE MENU. Labels are the short lithology form and
     *    the columns are fixed 20 px cells, so the table's width is a
     *    function of the menu, not of the vocabulary.
     */
    function junctionTableHTML(CF) {
        var J = CF.junctions;
        var keys = Object.keys(J);
        if (!keys.length) {
            return '<div class="mode-menu-note">These sheets have no mapped junctions.</div>';
        }

        // The lithologies that actually take part in a junction here, ordered
        // by how many junction types they are in (alphabetical breaks the tie,
        // so the table does not reshuffle between renders).
        var deg = {};
        keys.forEach(function (k) {
            var p = J[k];
            deg[p.a] = (deg[p.a] || 0) + 1;
            if (p.b !== p.a) deg[p.b] = (deg[p.b] || 0) + 1;
        });
        var min = GeoMap.minWeight ? GeoMap.minWeight() : 1;
        var sel = GeoMap.selectedCommodities ? GeoMap.selectedCommodities() : new Set();
        // Every junction picked, not just one: the table adds now.
        var picks = (GeoMap.contactPairs && GeoMap.contactPairs()) || new Set();
        var isPicked = function (k) { return picks.has(k); };

        // How strong the best junction each rock takes part in is, FOR THE
        // COMMODITY IN QUESTION. With gold picked, the reader is asking which
        // rocks are worth walking the edge of, so the strongest rocks go to
        // the top and the diagonal of full dots reads down the leading edge
        // instead of being scattered through an alphabet.
        var bestOf = {};
        keys.forEach(function (k) {
            var p = J[k], w = 0;
            (p.affinity || []).forEach(function (a) {
                if (sel.size && !sel.has(a.commodity)) return;
                if (a.weight > w) w = a.weight;
            });
            bestOf[p.a] = Math.max(bestOf[p.a] || 0, w);
            bestOf[p.b] = Math.max(bestOf[p.b] || 0, w);
        });
        /* SAME RULE AS THE ROCK TABLE: the order is a property of the view,
         * not of the selection. Both axes here are ordered by how strongly
         * each rock answers the CURRENT commodity, so picking a junction (or
         * a commodity in the other tab) re-sorts them — which, mid-gesture,
         * moves the next cell out from under the reader's finger. It holds
         * still while they are working and settles, visibly, when they move
         * on (mxSettle → mxFlip). */
        var liths = Object.keys(deg).sort(function (a, b) {
            return ((bestOf[b] || 0) - (bestOf[a] || 0)) ||
                   (deg[b] - deg[a]) || a.localeCompare(b);
        });
        liths = jxOrder(liths);

        // No sub-caption here. "rock across, commodity down" earns its place
        // in the rock table because the two axes differ; both axes here are
        // rock, the title says so, and the second line only wrapped the head
        // onto three rows in a 330 px panel.
        /* NO CONTROLS ARE REPEATED HERE.
         *
         * The commodity is picked in the rock table, where it is a ROW and
         * tapping it also shows what hosts it; the strength floor sits above
         * both tabs. Offering either again here would be one piece of state
         * with two controls a few lines apart, which is how a reader ends up
         * unsure which copy they just changed.
         *
         * What this view owes the reader is not another switch but a
         * SENTENCE: this table has both axes spent on rock, so nothing in it
         * says which commodity its ink is about. The head says it, and the
         * panel's state line above says it again in full — one statement,
         * read twice, rather than two controls.
         */
        var forWhat = sel.size
            ? Array.from(sel).map(function (k) { return k.replace(/_/g, ' '); }).join(' + ')
            : '';
        var grade = min === 3 ? 'classic' : (min === 2 ? 'likely' : '');

        // THIS TABLE'S KIND IS 'junction', so it is scored on junctions. One
        // commodity picked => one verdict for the whole table's ink; with none
        // or several picked the cells grade for "anything" and no single
        // measurement speaks for them, so the ink stays as it was.
        var jV = (sel.size === 1) ? mxVerdict(Array.from(sel)[0], 'junction', min) : 'unmeasured';
        var jS = (sel.size === 1) ? mxSkill(Array.from(sel)[0], 'junction', min) : null;

        var html = '<div class="mode-menu-head ml-mx-head">' +
            '<i class="icon-git-merge"></i>Where two rocks meet' +
            // The carried-over question, in the head, because the cells cannot
            // carry it: without this, a full dot means "good for something"
            // and the reader supplies their own guess as to what.
            (forWhat || grade
                ? '<em title="Set in the Rocks table \u2014 the same selection, carried over">' +
                  'graded for ' + esc(forWhat || 'anything') +
                  (grade ? ', ' + grade : '') + '</em>'
                : '<em>graded for anything</em>') +
            // The measured lift for THIS table's kind, in its head, where the
            // "graded for gold" claim is made. Same object as the rock table's
            // per-row badge: one commodity, two kinds, two scores, each shown
            // beside the choice it is a score for.
            (jV === 'unmeasured' ? ''
                : '<em class="ml-mx-lift ' + jV + '" title="' +
                  esc(skillNote(Array.from(sel)[0], 'junction', min)) + '">' +
                  liftText(jS) + '</em>') +
            '</div>';

        html += '<div class="ml-jx" style="grid-template-columns:minmax(52px,1fr) repeat(' +
            liths.length + ',18px);">';

        // Column heads: the lithology ornament, the same object the key strip
        // and the rock matrix use. Tapping one asks for every junction that
        // rock takes part in — the question no single cell can put.
        html += '<span class="ml-mx-corner"></span>';
        liths.forEach(function (l) {
            var lm = lithMeta(l);
            html += '<button type="button" data-mx="jcol:' + esc(l) + '" class="ml-jx-col' +
                (isPicked(l) ? ' on' : '') + '"' +
                ' aria-pressed="' + isPicked(l) + '"' +
                ' title="' + esc(lm.label + (lm.desc ? ' \u2014 ' + lm.desc : '') +
                    ' \u2014 in ' + deg[l] + ' junction type(s) here. Tap to draw every contact ' +
                    'it takes part in' + (isPicked(l) ? ' (tap again to drop it)' : '') + '.') + '"' +
                ' aria-label="' + esc(lm.label) + ' junctions"' +
                ' onclick="event.stopPropagation();MapLegend.geoJunction(\'' + esc(l) + '\')">' +
                '<i style="' + swatchBG(l, '#9ca3af', 13) + '"></i></button>';
        });

        liths.forEach(function (ra, ri) {
            var rm = lithMeta(ra);
            html += '<button type="button" data-mx="jrow:' + esc(ra) + '" class="ml-jx-row' +
                (isPicked(ra) ? ' on' : '') + '"' +
                ' aria-pressed="' + isPicked(ra) + '"' +
                ' title="' + esc(rm.label + (rm.desc ? ' \u2014 ' + rm.desc : '') +
                    ' \u2014 tap to draw every contact it takes part in' +
                    (isPicked(ra) ? ' (tap again to drop it)' : '') + '.') + '"' +
                ' onclick="event.stopPropagation();MapLegend.geoJunction(\'' + esc(ra) + '\')">' +
                '<i style="' + swatchBG(ra, '#9ca3af', 13) + '"></i>' +
                '<span>' + esc(lithLabel(ra)) + '</span></button>';
            liths.forEach(function (rb, ci) {
                if (ci < ri) {              // the mirrored half: never printed twice
                    html += '<span class="ml-jx-cell mirror" aria-hidden="true"></span>';
                    return;
                }
                var key = ra < rb ? ra + '|' + rb : rb + '|' + ra;
                var p = J[key];
                if (!p) {                   // these two rocks never meet on these sheets
                    html += '<span class="ml-jx-cell none" aria-hidden="true"></span>';
                    return;
                }
                // The grade shown follows the commodity selection, exactly as
                // the layer's own filter does: with gold picked, a cell is its
                // GOLD grade, not its best grade for anything at all.
                var w = 0, why = [];
                (p.affinity || []).forEach(function (a) {
                    if (sel.size && !sel.has(a.commodity)) return;
                    if (a.weight > w) w = a.weight;
                    why.push('\u25cf'.repeat(a.weight) + ' ' + a.commodity.replace(/_/g, ' '));
                });
                var lbl = lithLabel(ra) + ' / ' + lithLabel(rb);
                var km = p.km >= 1000 ? Math.round(p.km / 1000) + ',000 km'
                                      : Math.round(p.km) + ' km';
                var head = lbl + ' \u2014 ' + p.n + ' mapped junction' + (p.n === 1 ? '' : 's') +
                    ', ' + km;
                if (!w) {
                    html += '<span data-mx="jcell:' + esc(key) + '" class="ml-jx-cell ungraded" title="' +
                        esc(head + '. ' + (sel.size
                            ? 'Not a setting this model grades for ' +
                              Array.from(sel).join(' or ').replace(/_/g, ' ') + '.'
                            : 'These two rock types in contact are not a setting this model ' +
                              'grades \u2014 a gap in the model, not evidence of absence.')) +
                        '"></span>';
                    return;
                }
                var faded = w < min;
                html += '<button type="button" data-mx="jcell:' + esc(key) +
                    '" class="ml-jx-cell g' + w + (faded ? ' below' : '') +
                    // Measured, on the ground in view, NOT to beat random
                    // ground for the picked commodity: grey. In the CAR this
                    // is what the diamond junctions look like (0.41x), and it
                    // is the same discipline the rock table applies to its own
                    // cells — one model, two halves, each drawn as what it
                    // measures.
                    (jV === 'fails' ? ' unproven' : '') +
                    (jV === 'split' ? ' contested' : '') +
                    (isPicked(key) ? ' on' : '') + '"' +
                    ' aria-pressed="' + isPicked(key) + '"' +
                    ' title="' + esc(head + '. ' + why.join(', ') +
                        (jV === 'fails'
                            ? '. MEASURED ' + hereWords(jS).toUpperCase() + ' AT ' + liftText(jS) +
                              ' vs random ground: for ' + forWhat + ', contacts do not pick out ' +
                              'worked ground there.'
                            : jV === 'works'
                                ? '. Measured ' + hereWords(jS) + ' at ' + liftText(jS) + ' vs random ground.' : '') +
                        (faded ? ' Below the strength floor, so it is not drawn.'
                               : ' Tap to draw just this junction' +
                                 (isPicked(key) ? ' (tap again to drop it)'
                                                : ' \u2014 it ADDS to the junctions already picked') +
                                 '.')) + '"' +
                    ' aria-label="' + esc(lbl + ', grade ' + w +
                        (jV === 'fails' ? ', measured no better than random ground here' : '')) + '"' +
                    ' onclick="event.stopPropagation();MapLegend.geoJunction(\'' + esc(key) + '\')">' +
                    '<i></i></button>';
            });
        });
        html += '</div>';

        /* What the map is drawing, and the one filter that is on by default.
         *
         * There is no "draw contact lines" checkbox any more. It was a switch
         * under a table about contacts, so the reader read the table, saw
         * nothing on the map, and had to find a second control saying
         * yes-really; a table whose subject is not on screen is a catalogue,
         * not a legend. Opening this view draws them (MapLegend.mxMode), and
         * this line REPORTS that rather than asking for it.
         *
         * The count is what is DRAWN, from the same visiblePairs() the paint
         * filter uses, so the number and the map cannot disagree — and when it
         * is zero the line says so instead of leaving an empty layer to be
         * read as "there are no contacts here" (invariant 1, at the reading
         * end).
         */
        var drawn = CF.on ? CF.drawn : 0;
        html += '<div class="ml-jx-foot' + (drawn ? '' : ' empty') + '">' +
            '<i class="icon-git-merge"></i>' +
            '<span>' + (CF.on
                ? (drawn ? '<b>' + drawn + '</b> contact line' + (drawn === 1 ? '' : 's') +
                           ' on the map' + (CF.pairs.size ? ', ' + esc(junctionsWords(CF.pairs)) : '')
                         : 'Nothing is drawn at this setting')
                : 'Contact lines are off') + '</span>' +
            '<button type="button" class="ml-jx-graded' + (CF.graded ? '' : ' on') + '"' +
                ' aria-pressed="' + (!CF.graded) + '"' +
                ' title="' + esc(CF.graded
                    ? 'Drawing only the junctions the model grades. Tap to draw every mapped ' +
                      'junction \u2014 a sheet is mostly boundaries, so that is a lot of lines.'
                    : 'Drawing every mapped junction, graded or not. Tap to go back to the ' +
                      'graded ones.') + '"' +
                ' onclick="event.stopPropagation();MapLegend.toggleContactsGraded()">' +
                (CF.graded ? 'graded only' : 'every junction') + '</button>' +
            '</div>';

        if (CF.on && !drawn) {
            html += '<div class="mode-menu-note warn">Lower the strength floor, pick another ' +
                'junction, or draw every junction.</div>';
        }

        /* THE DISCLAIMER IS NOT THE SCORE.
         *
         * "An inference, not a record" tells the reader this is not evidence.
         * It does not tell them whether it WORKS, and the two are different
         * questions: on CAR the gold-graded junctions hold 2.3x more of the
         * known workings than the same amount of ground picked at random, and
         * the gold-graded UNITS hold fewer than random ground does. Both were
         * drawn in the same amber under the same disclaimer. So the measured
         * finding rides beside the claim — as the SAME line the rock table
         * carries, because a reader moving between the tabs must not have to
         * reconcile two wordings of one measurement — and where nothing has
         * been measured it says that word rather than leaving a gap that reads
         * as a low score. srv/geomap_scores.go, scripts/geomaps/eval_affinity.py.
         */
        html += skillRouteHTML(sel, min, 'junction');
        // The continental version of this table's question: a craton margin
        // or a fault is a SETTING, like a junction, at the scale above the
        // sheets. Same rows as the rock view — one surface, two doors.
        html += structuralFootHTML();
        html += '<div class="mode-menu-note">An inference from the two rock types either side ' +
            '\u2014 nothing here counts, ranks or locates a deposit.</div>';
        return html;
    }

    // ---------------------------------------------------------------
    // The strip
    // ---------------------------------------------------------------
    function render() {
        var host = document.getElementById('stats-map');
        if (!host) return;
        measureCoverage();
        measureSourceAges();
        var b = bm(), quiet = (b === 'dark') && !histOn() && !geoOn();
        host.classList.toggle('quiet', quiet);
        wireRest(host);

        var opener = '<button type="button" class="ml-opener" id="ml-opener" ' +
            'aria-haspopup="menu" aria-label="Basemap and overlays" ' +
            'title="Basemap and overlays" onclick="event.stopPropagation();MapLegend.menu(this)">' +
            '<i class="icon-layers"></i></button>';

        if (quiet) {
            if (host.innerHTML !== opener) host.innerHTML = opener;
            applyRest();
            return;
        }

        // THE KEY IS BUILT FIRST, and the chips are built knowing what it
        // said. The strip's bracket labels name commodities; the chip must
        // not name them again (see geoFilterNote). Order matters, so it is
        // stated rather than implied by where the strings are concatenated.
        var swatches = ageSwatches();

        var chips = '';
        if (b !== 'dark') {
            var e = bmEntry(b);
            // TWO TARGETS, ONE MEANING EACH — the same reason the geology chip
            // has an ×. "Google" is the only word on screen naming the ground
            // under the data, so it is also the place a reader will tap to ask
            // "which imagery am I on, and what else is there": a single-target
            // chip answered that question by throwing the imagery away. Body =
            // choose the basemap (the same one menu the opener shows, with the
            // current one checked), × = back to dark, deliberately.
            chips += '<span class="ml-chip base">' +
                '<button type="button" class="ml-chip-main" title="' + esc(e[3]) +
                ' \u2014 tap to choose the basemap" ' +
                'onclick="event.stopPropagation();MapLegend.menu(this.parentNode)">' +
                '<i class="' + e[2] + '"></i><span class="ml-chip-label">' + esc(e[1]) + '</span>' +
                '<i class="icon-chevron-down ml-caret"></i></button>' +
                '<button type="button" class="ml-chip-x" aria-label="Back to the dark basemap" ' +
                'title="Back to the dark basemap" ' +
                'onclick="event.stopPropagation();MapLegend.pickBasemap(\'dark\')">\u00d7</button></span>';
        }
        if (histOn()) {
            var hm = histMeta();
            // Same two targets as geology and the basemap: body = configure
            // (which is where the opacity lives, the one control this drape
            // genuinely needs — a traced-ink sheet over imagery is unreadable
            // at the wrong opacity, and that slider was four clicks deep in
            // admin), × = hide it, deliberately. A single-target chip made
            // "I want to see through it a bit" and "throw it away" the same
            // gesture.
            var hOff = histOffView();
            chips += '<span class="ml-chip hist' + (hOff ? ' offview' : '') + '">' +
                '<button type="button" class="ml-chip-main" title="' +
                esc((hOff ? 'The sheet series does not reach this view — tap for opacity and the way there'
                          : ((hm && hm.name) || 'Historical map overlay') + ' — tap for opacity and the archive')) + '" ' +
                'onclick="event.stopPropagation();MapLegend.histMenu(this.parentNode)">' +
                '<i class="icon-scroll-text"></i><span class="ml-chip-label">Historical</span>' +
                (hOff ? '<em>not in view</em>' : '') +
                '<i class="icon-chevron-down ml-caret"></i></button>' +
                '<button type="button" class="ml-chip-x" aria-label="Hide the historical map overlay" ' +
                'title="Hide the historical map overlay" ' +
                'onclick="event.stopPropagation();MapLegend.toggleHist()">\u00d7</button></span>';
        }
        if (geoOn()) {
            // Three sheets cover three countries, so "on, but not here" is the
            // ordinary case across most of Africa. Saying it on the chip is
            // the difference between a layer the user can trust and one they
            // report as broken; the sub-label wins over the filter note,
            // because "which units" is moot where none are drawn.
            var off = geoOffView();
            var kinds = drawnKinds();
            // "Contacts only" is its own state and needs its own words. The
            // chip used to fall back to "not in view" here — over a map
            // drawing 67 orange junction lines — because the whole layer was
            // judged by whether a FILL was painted. What the reader can see is
            // what the chip has to describe.
            var fn = off ? { short: 'not in view', full: 'not in view' }
                : (!kinds.units && kinds.contacts
                    ? { short: 'contacts only', full: 'contact lines — no mapped unit reaches this view' }
                    : geoFilterNote(bracketed));
            var note = fn.short;
            // TWO TARGETS, ONE MEANING EACH — the strip's founding rule, which
            // geology broke the day its chip started opening a menu. The other
            // two chips destroy their layer on tap; this one configures, and
            // "switch the whole thing off" was then reachable only from inside
            // the menu, two taps and a scroll away. The × restores the
            // symmetry: chip body = configure, × = off, exactly as everywhere
            // else in the app.
            chips += '<span class="ml-chip geo' +
                (off ? ' offview' : (note ? ' filtered' : '')) + '">' +
                '<button type="button" class="ml-chip-main" title="' +
                esc(off ? 'Geology is on, but no mapped sheet reaches this view \u2014 tap to choose what to show'
                        : (note ? 'Geology, showing only ' + fn.full + ' \u2014 tap for the rock and junction tables'
                                : 'Geological units \u2014 tap for the rock and junction tables')) + '" ' +
                'onclick="event.stopPropagation();MapLegend.geoMenu(this.parentNode)">' +
                '<i class="icon-mountain"></i><span class="ml-chip-label">Geology</span>' +
                (note ? '<em>' + esc(note) + '</em>' : '') +
                // The caret said "this opens something"; the table icon says
                // WHAT — a matrix of rock against commodity, not another list
                // of switches. It is the only chip in the strip whose menu is
                // an object rather than a list, and that is worth one glyph.
                '<i class="icon-table-2 ml-caret"></i></button>' +
                '<button type="button" class="ml-chip-x" aria-label="Hide the geology layer" ' +
                'title="Hide the geology layer" ' +
                'onclick="event.stopPropagation();MapLegend.toggleGeo()">\u00d7</button></span>';
        }

        // ── AN IDENTICAL REPAINT IS NOT A REPAINT, IT IS A RESET ──────────
        //
        // render() is called from six places and MapLegend.refresh() from two
        // dozen more, so one junction pick rebuilt this strip four times —
        // and three of those produced byte-identical HTML. Writing it anyway
        // is not merely wasted layout: innerHTML destroys and recreates the
        // buttons, which drops focus, cancels the :hover the reader is
        // pointing at, and restarts the swatch stagger. So the write is
        // conditional on the markup actually differing.
        //
        // Safe because this string is a pure function of the state the strip
        // shows: equal HTML means an equal strip. Anything NOT in the string
        // (the swatch expansion below) is re-applied either way.
        var html =
            '<div class="stats-divider"></div>' +
            '<div class="stats-header">Map</div>' +
            '<div class="ml-row">' + chips + opener + '</div>' +
            swatches;
        if (host.innerHTML !== html) host.innerHTML = html;
        // The fold is a CLASS plus one derived attribute, and the write above
        // may have just replaced the element carrying it. Re-applied
        // unconditionally, for the same reason syncSwatchExpansion is.
        applyRest();
        // Not animated here: render() runs on every map idle, and re-staggering
        // an already-open strip on each pan is a flicker, not a transition.
        syncSwatchExpansion(false);
    }

    var MapLegend = {
        refresh: render,
        menu: openMenu,
        histMenu: openHistMenu,
        geoMenu: openGeoMenu,
        /** The mixer's body, for the OTHER surface that shows it: the Geology
         *  card in admin ▸ Map Settings. Same string, same handlers, so the
         *  two cannot disagree — see geoBodyHTML(). */
        /** The signature of the canvas measurement the mixer is built from,
         *  for the admin card, which has no idle handler of its own. */
        geoAnswerSig: geoAnswerSig,
        geoBody: geoBodyHTML,
        /** "A repaint is on its way." The matrix's columns are the periods
         *  DRAWN, so a surface rebuilt in the same tick as the gesture shows
         *  the map as it was one gesture ago; the floating panel waits for the
         *  paint (refreshWhenDrawn) and the admin card has to wait for the
         *  same one. Without this the card painted twice per gesture, and the
         *  first of the two was wrong. */
        paintPending: function () { return !!paintWait; },
        /** The panel bar's download arrow: this view, or every sheet. */
        downloadMenu: openDownloadMenu,
        /** The same arrow on the Historical Maps card in admin. */
        histDownloadMenu: openHistDownloadMenu,
        /** Which download row a share link asked the admin card to point at,
         *  consumed once. '' when the link named none. */
        takeAdminExport: function () { var v = geoAdminExport; geoAdminExport = ''; return v; },
        downloadView: downloadView,
        /** The download arrow's tooltip, so the admin card's button and the
         *  panel's arrow say one sentence about one file. */
        downloadTitle: downloadBtnTitle,
        /** The whole-catalogue link is a plain <a download>, so there is no
         *  completion event — the row says "Preparing…" for a floor rather
         *  than pretending to measure a build it cannot see. Same floor and
         *  same wording as the admin link (downloadGeoMapGPKG in globe.html):
         *  one behaviour, not a third path. */
        downloadAllStarted: function () {
            var a = document.getElementById('ml-dl-all');
            if (!a || a.classList.contains('busy')) return true;
            var was = a.innerHTML;
            a.classList.add('busy');
            a.innerHTML = '<i class="icon-loader"></i><span>Preparing\u2026</span>' +
                '<em>building the whole catalogue</em>';
            setTimeout(function () { a.classList.remove('busy'); a.innerHTML = was; }, 12000);
            return true;
        },
        close: closeMenu,

        /* A swatch is a switch. Hiding the LAST visible period would empty the
         * drape, which reads as "no geology here" rather than as a filter, so
         * that click is refused in words instead. */
        toggleAge: function (key) {
            if (typeof GeoMap === 'undefined' || !GeoMap.toggleAge) return;
            var shown = ageEntries().filter(function (e) { return e.on; });
            if (GeoMap.ageOn(key) && shown.length <= 1) {
                if (typeof showToast === 'function') {
                    showToast('That is the only period in view \u2014 hiding it would leave an ' +
                        'empty map. Hide something else first, or tap \u201call\u201d.', 'warning');
                }
                return;
            }
            GeoMap.toggleAge(key);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            refreshWhenDrawn();
        },

        /* The strength floor. Refuses to empty the map — the standing rule
         * that an empty drape reads as "no data here", not as a filter. */
        geoMinWeight: function (w) {
            if (typeof GeoMap === 'undefined' || !GeoMap.setMinWeight) return;
            if (!GeoMap.setMinWeight(w)) {
                if (typeof showToast === 'function') {
                    showToast('Nothing that strong here \u2014 no unit in this selection is graded ' +
                        'that highly, so the current setting stands.', 'warning');
                }
                return;
            }
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            // The menu stays open AND unchanged until the map has drawn:
            // picking a floor is a comparison ("how much does 'classic'
            // actually drop?"), and rebuilding it now would answer with the
            // previous map. refreshWhenDrawn() rebuilds it once, after.
            refreshWhenDrawn();
        },

        /* ── A CELL IS A PICK, AND PICKS ADD ────────────────────────
         *
         * "The Palaeoproterozoic of the cobalt ground" — and then, one tap
         * later, "…and the Archaean of the copper ground". The map draws the
         * UNION of the cells the reader has tapped.
         *
         * It used to be the opposite gesture: a cell REPLACED the commodity
         * selection and soloed its period, so every tap threw the previous
         * answer away. Two things went wrong with that. The reader could not
         * assemble a map out of the table — the obvious use of a grid of
         * cells — and, because the matrix's columns are the periods actually
         * DRAWN, soloing one collapsed the table to a single column, hiding
         * the cells they would have picked next. The narrowing on the map is
         * the same kind of thing; what changed is that the table stays whole
         * and the selection accumulates.
         *
         * The floor gives way to the pick: tapping a weight-1 cell while the
         * floor says "classic" would otherwise select nothing, which is a
         * control that reads as broken. Tapping a picked cell removes it, so
         * the gesture is its own undo. */
        geoCell: function (k, age, w) {
            if (typeof GeoMap === 'undefined' || !GeoMap.toggleCell) return;
            if (!GeoMap.cellPicked(k, age) && w < GeoMap.minWeight()) GeoMap.setMinWeight(w);
            // A pick and a hidden period are contradictory answers to one
            // question: the cell says "draw this ground", the age filter says
            // "draw nothing of that period". The pick wins, so the reader sees
            // what they just tapped.
            if (!GeoMap.cellPicked(k, age) && !GeoMap.ageOn(age)) GeoMap.toggleAge(age);
            mxTouch();      // mid-gesture: the order holds still until they stop
            GeoMap.toggleCell(k, age);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            refreshWhenDrawn({ animate: true });
        },

        /* ── THE GESTURE IS OVER ─────────────────────────────
         *
         * The reader moved on — to the Junctions tab, to the map, to anything
         * outside this panel — so the selection is a decision now rather than
         * something they are still assembling. That is the moment the table
         * may re-sort: it puts what they picked where the table says the
         * strongest answers go, and it does it as a movement they WATCH (FLIP,
         * see mxFlip) instead of as a grid that has silently become a
         * different grid the next time they look at it.
         *
         * Deliberately not a timer alone: settling under a reader who is still
         * choosing is the failure; settling when they have stopped choosing is
         * the feedback. The timer is only the backstop for "stopped without
         * going anywhere". */
        mxSettle: function () {
            clearTimeout(mxSettleTimer);
            if (!mxWorking) return;
            mxWorking = false;
            mxFrozen = null;            // next render re-derives the order
            if (menuEl && menuEl.dataset.kind === 'geo') {
                rebuildGeoMenuNow({ animate: true });
            }
        },

        showAllAges: function () {
            if (typeof GeoMap === 'undefined' || !GeoMap.clearAges) return;
            GeoMap.clearAges();
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            refreshWhenDrawn();
        },

        /* "+n" is a truncation announcing itself; tapping it must therefore
         * SHOW the rest here, not send the reader to another panel. */
        toggleMore: function () {
            expanded = !expanded;
            render();
            syncSwatchExpansion(true);
        },

        /* The commodity filter, driven through the ADMIN PANEL's own function
         * so there is exactly one implementation of "a commodity is a property
         * of rock, so the chip acts on every sheet at once". If that function
         * is not on the page yet (the panel's markup is in globe.html, this
         * module is not), fall back to GeoMap directly rather than doing
         * nothing — a menu item that silently no-ops is invariant 1's failure. */
        geoCommodity: function (k) {
            // A ROW is "this commodity, on every ground it has", so it
            // SUPERSEDES that commodity's cells: leaving both would draw the
            // whole row while the table still lit two cells as though they
            // were the narrowing, and un-picking the row would then leave the
            // cells behind as a filter nobody remembers setting.
            if (typeof GeoMap !== 'undefined' && GeoMap.clearPicksFor) GeoMap.clearPicksFor(k);
            if (typeof geoToggleCommodityAll === 'function') geoToggleCommodityAll(k);
            else if (typeof GeoMap !== 'undefined') {
                var sheets = GeoMap.sheets() || {};
                var on = commodityOn(k);
                (GeoMap.order() || []).forEach(function (id) {
                    if (!(sheets[id] || {}).available) return;
                    var has = (GeoMap.classes(id) || []).some(function (c) {
                        return (c.commodities || []).indexOf(k) >= 0;
                    });
                    if (has && GeoMap.commodityOn(id, k) === on) GeoMap.toggleCommodity(id, k);
                });
            }
            // The menu stays where it is until the map has drawn. Choosing
            // commodities is a comparison ("what does adding copper do?"), and
            // the answer is the matrix's own columns — rebuilding it in this
            // tick would show the columns of the map before the copper.
            refreshWhenDrawn();
        },

        /* Back to the whole rock map. showEverything() clears the hand-hidden
         * units and the lithology filter too, which is what "All units" says
         * on the tin — a row that cleared only the chips would leave the map
         * filtered while claiming otherwise. */
        geoAll: function () {
            if (typeof GeoMap !== 'undefined') GeoMap.showEverything();
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            // Stays open: "show all" is a step in an exploration, not the end
            // of one. Rebuilt once the map has drawn every period back, since
            // the whole point of the gesture is the columns that come back.
            refreshWhenDrawn();
        },

        /* Contacts on/off. Rebuilt after the map has drawn, like every other
         * gesture here: the row's own count and the chip's wording describe
         * what is on screen, and reading them from the canvas one tick early
         * describes the map as it was before the tap. */
        toggleContacts: function () {
            if (typeof GeoMap === 'undefined' || !GeoMap.setContacts) return;
            GeoMap.setContacts(!GeoMap.contactsOn());
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            refreshWhenDrawn();
        },

        /* Graded-only off = every mapped junction, which is a lot of lines and
         * is exactly why it is a second target rather than a mode nobody can
         * see. The word on the button says which of the two is running. */
        toggleContactsGraded: function () {
            if (typeof GeoMap === 'undefined' || !GeoMap.setContactsGraded) return;
            GeoMap.setContactsGraded(!GeoMap.contactsGradedOnly());
            refreshWhenDrawn();
        },

        /* A structural layer on/off, from its row in the mixer. The reader's
         * own gesture, so it goes through setStructural (which clears the
         * autoStructural flag — a hand-made choice is never auto-undone). */
        toggleStructural: function (id) {
            if (typeof GeoMap === 'undefined' || !GeoMap.setStructural) return;
            GeoMap.setStructural(id, !GeoMap.structuralOn(id));
            refreshWhenDrawn();
        },

        /* Which of the two tables is on screen. Not a filter, so it does not
         * touch the map and does not wait for a paint — but the rebuild goes
         * through the same path so the panel keeps its scroll and its place. */
        /* ── THE LINES FOLLOW THE TABLE ───────────────────────────
         *
         * "Draw contact lines" was a checkbox under a table about contacts:
         * the reader opened the junction view, read it, and saw nothing on the
         * map until they found a second control saying yes-really. A table
         * whose subject is not on screen is a catalogue, not a legend.
         *
         * So opening this tab draws them, and it is honest about who asked:
         *  - opened the tab and browsed  -> the lines came with the view, and
         *    they go when the view goes.
         *  - PICKED a junction           -> that is a decision about the map,
         *    so the lines stay when the reader goes back to the rock table
         *    (and the chip says so). Clearing the pick, or "show all", takes
         *    them away again — the same gesture that made them.
         *  - switched them on by hand before -> never overridden.
         */
        mxMode: function (m) {
            var want = (m === 'junction') ? 'junction' : 'rock';
            if (want === mxMode) return;
            // Leaving the rock table IS moving on, so whatever was being
            // assembled there is a decision now and the table settles behind
            // the reader rather than surprising them on the way back.
            this.mxSettle();
            mxMode = want;
            if (typeof GeoMap !== 'undefined' && GeoMap.setContacts && GeoMap.anyContacts()) {
                if (want === 'junction' && !GeoMap.contactsOn()) {
                    autoContacts = true;
                    GeoMap.setContacts(true);
                } else if (want === 'rock' && autoContacts && !GeoMap.contactPair()) {
                    autoContacts = false;
                    GeoMap.setContacts(false);
                }
            }
            // The structural context follows the same tab with the same
            // contract (GeoMap.setStructuralAuto keeps its own autoStructural
            // flag): the junction view is about SETTINGS, and a fault or a
            // craton margin is the continental-scale version of the same
            // question the contacts answer at sheet scale.
            if (typeof GeoMap !== 'undefined' && GeoMap.setStructuralAuto) {
                GeoMap.setStructuralAuto(want === 'junction');
            }
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            refreshWhenDrawn();
        },

        /* A JUNCTION is the junction table's own gesture: "draw me where
         * granite meets greenstone". A cell is a pair, a header is one rock
         * against everything — and tapping the one already picked clears it,
         * so the gesture is its own undo. GeoMap.setContactPair() turns the
         * layer on when it has to: a cell that changes nothing visible is a
         * control that reads as broken. */
        geoJunction: function (key) {
            if (typeof GeoMap === 'undefined' || !GeoMap.setContactPair) return;
            mxTouch();      // same rule as a cell: the table holds still
            GeoMap.setContactPair(key);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            refreshWhenDrawn({ animate: true });
        },

        /* "anything" — grade each junction by the strongest thing it hosts.
         * It clears the commodity selection and nothing else: the floor, the
         * hidden periods and the picked junction are separate answers to
         * separate questions, and a control that quietly reset them would be
         * the "button that did more than it said" this surface keeps avoiding. */
        geoCommodityClear: function () {
            if (typeof GeoMap === 'undefined') return;
            Array.from(GeoMap.selectedCommodities()).forEach(geoCommodityRaw);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            refreshWhenDrawn();
        },

        /* Collapsed = the bar alone. The state persists, because a reader who
         * collapsed the panel to see the map wants it collapsed after the next
         * gesture rebuilds it, not open again. */
        togglePanelCollapsed: function () {
            panelCollapsed = !panelCollapsed;
            savePanelState();
            if (menuEl && menuEl.dataset.kind === 'geo') {
                menuEl.classList.toggle('ml-panel-collapsed', panelCollapsed);
                var b = menuEl.querySelector('.fui-bar-btn[aria-expanded]');
                if (b) b.setAttribute('aria-expanded', String(!panelCollapsed));
            }
        },

        pickBasemap: function (id) {
            closeMenu();
            if (typeof switchBasemap === 'function') switchBasemap(id);
            setTimeout(render, 0);
        },

        /* ── What this module contributes to a share link ─────────────────
         *
         * GeoMap.getShareParams() describes the DRAPE; this describes the
         * surface the reader was reading it through, which is a different
         * object and was previously lost entirely. A link sent from the
         * junction table, with the panel open beside the map, arrived with a
         * closed panel on the rock tab — so the recipient saw orange lines
         * and no table explaining them, i.e. the exact question the sender was
         * pointing at was the one thing the link dropped.
         *
         * Each is omitted at its default, so an ordinary link stays short.
         * The panel POSITION is deliberately not carried: it is furniture the
         * recipient's own window decides, and a coordinate from a 27-inch
         * screen restores off the edge of a laptop. */
        getShareParams: function () {
            var p = {};
            var open = !!(menuEl && menuEl.dataset.kind === 'geo');
            if (open) p.geo_panel = panelCollapsed ? 'collapsed' : '1';
            // The tab is a property of the panel, so it only travels with it.
            if (open && mxMode === 'junction') p.geo_tab = 'junction';
            // "+n" opened is a truncation the reader lifted — a link that
            // re-truncates shows fewer periods than the picture it claims to
            // reproduce.
            if (expanded) p.geo_key = 'all';
            // Which download the link points at, when it was copied from a row
            // of the download menu (copyExportLink writes it into the URL it
            // hands ShareLink). Never set by an ordinary share.
            return p;
        },

        restoreFromParams: function (params) {
            if (params.get('geo_key') === 'all') expanded = true;
            var want = params.get('geo_panel');
            if (params.get('geo_tab') === 'junction') mxMode = 'junction';
            // ?geo_export= points at a download row. It needs the panel, since
            // the arrow lives in the panel's bar, so a link that names a
            // download implies one — otherwise the recipient gets a map and no
            // sign of what the link was about.
            var wantDL = params.get('geo_export');
            if (wantDL !== 'view' && wantDL !== 'all') wantDL = '';
            // WHICH HOME THE LINK CAME FROM. The same mixer is readable in two
            // places, so `geo_export` alone is ambiguous: a link copied from
            // the admin card that opened the floating panel would answer with
            // a surface the sender was not looking at. buildShareUrl() already
            // carries panel=admin + admin_tab when the admin panel is open, so
            // that pair IS the answer, and the panel is only implied when it
            // is absent. Admin's own restore (?admin_tab=map-settings) opens
            // the tab; this only has to point at the row there.
            var toAdmin = wantDL && params.get('panel') === 'admin' &&
                params.get('admin_tab') === 'map-settings';
            if (toAdmin) {
                // ONE ROW IS POINTED AT, IN ONE PLACE. A sender with both
                // surfaces open produces a link naming both; opening two
                // download menus would be the app answering a question twice.
                // Admin wins when the link names it, and the floating panel is
                // still restored if `geo_panel` asked for it — just without a
                // second menu on top of it.
                geoAdminExport = wantDL;      // the card points at it when it paints
                wantDL = '';
            }
            if (wantDL && !want) want = '1';
            if (!want) return;
            panelCollapsed = (want === 'collapsed');
            // TWO THINGS HAVE TO EXIST FIRST, and only one of them is the
            // chip. The panel anchors off the chip (a missing anchor put it in
            // the corner of the screen), and its whole body is derived from
            // the LOADED TILES — so a panel opened the moment the chip appears
            // is built from an empty canvas and says "not in view" over a map
            // that is about to draw. It does not correct itself either: the
            // rebuild is a repaint away, and by then MapLibre has been idle
            // once already.
            //
            // So the link waits for geology to actually be PAINTED (a fill or
            // a contact line), polling the same measurement the strip uses,
            // and gives up after ~9 s by opening anyway — a panel that says
            // "not in view" over a view that really has none is correct, and
            // silently not opening a panel the link asked for is not.
            var tries = 0;
            var open = function () {
                var btn = document.querySelector('#stats-map .ml-chip.geo');
                if (menuEl && menuEl.dataset.kind === 'geo') return;
                // FORCED, and this is the one caller that must be: the whole
                // question here is "has it painted YET", which is exactly the
                // difference the memo's key cannot see (same viewport, same
                // layers, same filters, one frame later). Sixty forced passes
                // at ~120 ms would be seven seconds of main thread during page
                // load, so it polls at 250 ms and gives up after ~9 s as
                // before — same wall clock, a third of the work.
                var drawn = btn && (measureCoverage(true), drawnKinds());
                if (!btn || (!(drawn.units || drawn.contacts) && tries < 36)) {
                    tries++;
                    return setTimeout(open, 250);
                }
                openGeoMenu(btn);
                // The download menu, with the row pointed at — opened after the
                // panel exists, since the arrow is in the panel's own bar.
                if (wantDL) {
                    setTimeout(function () {
                        var arrow = menuEl && menuEl.querySelector('.fui-bar-btn[data-geodl]');
                        if (arrow) openDownloadMenu(arrow, wantDL);
                    }, 250);
                }
            };
            setTimeout(open, 300);
        },

        toggleHist: function () {
            closeMenu();
            if (typeof HistMap === 'undefined') return;
            Promise.resolve(HistMap.toggle()).then(render);
        },

        /* Live, and the menu stays open: the reader is comparing ink against
         * ground, and a re-render on every input event would tear the slider
         * out from under the finger. Only the number beside it is updated. */
        histOpacity: function (v) {
            if (typeof HistMap === 'undefined') return;
            var pct = Math.max(10, Math.min(100, parseInt(v, 10) || 0));
            HistMap.setOpacity(pct / 100);
            var lbl = menuEl && menuEl.querySelector('#ml-op-v');
            if (lbl) lbl.textContent = pct + '%';
        },

        /* The one place this app moves the camera for an overlay: the reader
         * asked "where are the sheets" by tapping a row that says none are
         * here. Everywhere else a drape applies in place. */
        histGoTo: function () {
            closeMenu();
            var m = histMeta();
            if (!m || typeof map === 'undefined' || !map) return;
            if (m.bounds && m.bounds.length === 4) {
                map.fitBounds([[m.bounds[0], m.bounds[1]], [m.bounds[2], m.bounds[3]]], { padding: 40 });
            } else if (m.center) {
                map.flyTo({ center: [m.center[0], m.center[1]], zoom: m.center[2] || 7 });
            }
        },

        toggleGeo: function () {
            closeMenu();
            if (typeof GeoMap === 'undefined') return;
            Promise.resolve(GeoMap.toggleAll()).then(render);
        },

        openSettings: function () {
            closeMenu();
            var p = document.getElementById('admin-panel');
            if (p && !p.classList.contains('active') && typeof toggleAdminPanel === 'function') {
                toggleAdminPanel();
            }
            if (typeof switchAdminTab === 'function') setTimeout(function () { switchAdminTab('map-settings'); }, 60);
        }
    };
    /* ── ANY DELIBERATE ACT WAKES THE STRIP ────────────────────────────
     *
     * Every method here is something the reader did on purpose, so each one
     * is a reason to unfold and to restart the clock. Wrapping the table is
     * how that stays true for methods added later — a new control that forgot
     * to call wake() would fold itself mid-gesture, which is the kind of bug
     * that gets reported as "it flickers".
     *
     * `refresh` is the exception and has to be: it is render(), and watchMap
     * calls it on every map idle. Waking there would mean the strip never
     * rests while anything on the map is moving — i.e. never. The others in
     * QUIET are the same shape of thing: reads the app makes of itself (the
     * admin card asks geoAnswerSig()/geoBody() on every idle, a share link
     * asks getShareParams()), and gestures that mean the reader is LEAVING —
     * mxSettle() fires on a click anywhere outside the panel, so waking on it
     * would mean every click on the map unfolds the legend.
     */
    var QUIET = { refresh: 1, geoAnswerSig: 1, geoBody: 1, paintPending: 1,
        downloadTitle: 1, takeAdminExport: 1, getShareParams: 1,
        restoreFromParams: 1, mxSettle: 1, close: 1 };
    Object.keys(MapLegend).forEach(function (k) {
        if (QUIET[k] || typeof MapLegend[k] !== 'function') return;
        var fn = MapLegend[k];
        MapLegend[k] = function () { wake(); return fn.apply(this, arguments); };
    });

    window.MapLegend = MapLegend;

    // The overlays are asked about once, on load, so the strip can say
    // "historical maps: no archive installed" instead of offering a switch
    // that will fail — and so a share link that turned one on paints a chip
    // without the user opening admin first.
    // The key describes THIS view, so it has to follow the view. On 'idle'
    // only: the sample is ~200 queryRenderedFeatures calls, which is cheap
    // once a movement has settled and wasteful on every frame of a pan. The
    // strip is otherwise driven by state changes (a toggle, a share link),
    // which do not move the map.
    function watchMap() {
        if (typeof map === 'undefined' || !map || !map.on) return;        // Both drapes describe THIS view: geology by what it painted, the
        // scanned series by whether its envelope reaches the screen. A chip
        // that says "not in view" has to stop saying it when the reader pans
        // onto the sheets, or it is the contradiction it exists to prevent.
        map.on('idle', function () {
            bumpPaint();               // the canvas changed; the memo must not survive it
            bumpIdle();                // ...and the map has now had its frame (contactsSettled)
            if (!(geoOn() || histOn())) return;
            render();
            // THE BAR IS RESTATED ON EVERY IDLE, unconditionally, and not only
            // when a zero is unproven. render() above has just re-measured, so
            // by the time the old code asked `if (contactsPending)` the flag it
            // was testing had already been cleared by that very measurement —
            // and the label it wanted to fix stayed on screen for good. Two
            // text writes that usually change nothing; see syncBar().
            syncBar();
            // THE ADMIN CARD SHOWS THE SAME MIXER and has no idle handler of
            // its own; without this it keeps whatever the canvas said when it
            // was built, which on a cold `?panel=admin` load is "no mapped
            // sheet reaches this view" over three countries. Free when the tab
            // is closed, and a no-op when the answer has not changed.
            if (typeof geoMapPanelCanvasChanged === 'function') geoMapPanelCanvasChanged();
            // A zero that was never proven gets another pass here too: `idle`
            // is exactly when a layer added a moment ago has finished
            // painting, and the panel bar must not be left saying "no lines
            // here" over a map drawing hundreds. See reMeasure().
            if (contactsPending) reMeasure(2);
            // A VERDICT KEYED TO THE VIEWPORT MUST NOT SURVIVE THE VIEWPORT.
            // The panel stays open while the reader pans, and the scores are
            // measured on one country's ground: pan off it and "measured 0.63x
            // here" becomes a number about ground nobody measured, which is
            // the failure the score was added to prevent, arriving from the
            // other side. Rebuilt only when the answer CHANGES — panning
            // inside one country is free, and a rebuild on every idle would
            // reshuffle a table the reader is working in.
            if (typeof GeoMap === 'undefined' || !GeoMap.skillScope) return;
            var s = GeoMap.skillScope();
            if (s === skillScopeSeen) return;
            skillScopeSeen = s;
            if (menuEl && menuEl.dataset.kind === 'geo') rebuildGeoMenuNow();
        });
        // MOVING ON, in the map's own words. A reader who drags the map has
        // finished choosing in the table; the selection becomes a decision and
        // the table settles behind them. `movestart` rather than `moveend`, so
        // the settling happens while they are looking at the map — not as a
        // grid that has silently rearranged itself by the time they look back.
        map.on('movestart', function () { MapLegend.mxSettle(); });
        map.on('click', function () { MapLegend.mxSettle(); });
        // A TILE THAT LANDS CHANGES THE PICTURE WITHOUT MOVING THE MAP, and so
        // does a style change. Both must invalidate the measurement memo, or a
        // reader who sits still while Tanzania finishes loading keeps the
        // counts taken before it arrived. Cheap: an integer, no measurement.
        map.on('sourcedata', function (e) {
            if (e && e.sourceId && String(e.sourceId).indexOf('geomap-src-') !== 0) return;
            bumpPaint();
        });
        map.on('styledata', bumpPaint);
    }

    /* The same statement for the rest of the page: any pointer that lands
     * outside the geology panel is the reader moving on. Capture phase, so a
     * handler that stops propagation cannot swallow it, and passive because
     * this never prevents anything. */
    function watchOutside() {
        document.addEventListener('pointerdown', function (e) {
            if (!menuEl || menuEl.dataset.kind !== 'geo') return;
            if (menuEl.contains(e.target)) return;
            MapLegend.mxSettle();
        }, { capture: true, passive: true });
    }

    function boot() {
        render();
        watchMap();
        watchOutside();
        if (typeof HistMap !== 'undefined') HistMap.ensureMeta().then(render).catch(function () {});
        if (typeof GeoMap !== 'undefined') GeoMap.ensureMeta().then(render).catch(function () {});
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
})();
