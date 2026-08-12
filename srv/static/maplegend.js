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

    function histOn() { return typeof HistMap !== 'undefined' && HistMap.isOn(); }
    function histMeta() { return (typeof HistMap !== 'undefined' && HistMap.meta()) || null; }
    function geoOn() { return typeof GeoMap !== 'undefined' && GeoMap.anyOn(); }

    /* What the geology layer is currently NOT showing, in words. A filtered
     * drape that reads as a complete one is the failure this line prevents. */
    function geoFilterNote() {
        if (typeof GeoMap === 'undefined' || !GeoMap.anyOn()) return '';
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
        if (comms.size) return grade + Array.from(comms).join(' + ') + ' hosts';
        if (sh && sh.liths && sh.liths.size) return sh.liths.size + ' rock type' + (sh.liths.size === 1 ? '' : 's');
        // An age hidden from the key is a subset like any other, and the chip
        // is the one place that says a drape is not the whole drape.
        if (sh && sh.agesOff && sh.agesOff.size) {
            return sh.agesOff.size + ' period' + (sh.agesOff.size === 1 ? '' : 's') + ' hidden';
        }
        if (isolated) return 'filtered';
        if (hidden) return hidden + ' hidden';
        return '';
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

    function geoFillLayers() {
        if (typeof GeoMap === 'undefined' || typeof map === 'undefined' || !map) return [];
        return (GeoMap.order() || [])
            .filter(function (id) { var s = GeoMap.state(id); return s && s.on; })
            .map(function (id) { return 'geomap-fill-' + id; })
            .filter(function (l) { try { return !!map.getLayer(l); } catch (e) { return false; } });
    }

    function measureCoverage() {
        var layers = geoFillLayers();
        if (!layers.length) { coverage = null; return; }
        var cv = map.getCanvas();
        var w = cv.clientWidth || cv.width, h = cv.clientHeight || cv.height;
        if (!w || !h) { coverage = null; return; }
        // ~200 samples whatever the viewport. Sampling error can move a swatch
        // one place at the tail; it cannot move one at the head, which is the
        // part of the order anybody reads.
        var step = Math.max(24, Math.ceil(Math.sqrt((w * h) / 200)));
        var byAge = {}, lithHits = {}, hits = 0, samples = 0;
        for (var y = step / 2; y < h; y += step) {
            for (var x = step / 2; x < w; x += step) {
                samples++;
                var f;
                try { f = map.queryRenderedFeatures([x, y], { layers: layers })[0]; }
                catch (e) { coverage = null; return; }
                if (!f) continue;
                var sheet = String((f.layer && f.layer.id) || '').replace('geomap-fill-', '');
                var cls = GeoMap.classOf(sheet, (f.properties || {}).code);
                if (!cls) continue;
                var age = cls.age || 'unknown';
                byAge[age] = (byAge[age] || 0) + 1;
                hits++;
                // The ornament shown for an age is the lithology covering most
                // of the view under it, not the first class that happened to
                // be listed — same rule as the order itself.
                var lk = age + '|' + (cls.lith || 'mixed');
                lithHits[lk] = (lithHits[lk] || 0) + 1;
            }
        }
        var lith = {};
        Object.keys(lithHits).forEach(function (k) {
            var i = k.indexOf('|'), age = k.slice(0, i), l = k.slice(i + 1);
            if (!lith[age] || lithHits[k] > lithHits[age + '|' + lith[age]]) lith[age] = l;
        });
        coverage = { byAge: byAge, lith: lith, hits: hits, samples: samples };
    }

    /* The layer is on but none of it is in this view — the ordinary case for
     * most of Africa, since three sheets cover three countries. A chip with no
     * key beside it otherwise reads as a broken layer, so the strip says so. */
    function geoOffView() { return !!(geoOn() && coverage && coverage.hits === 0); }

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
    var MAX = 8;
    var expanded = false;      // "+n" opened: show every age, not just the head
    var swTimers = [];
    function cancelSwTimers() { swTimers.forEach(clearTimeout); swTimers = []; }

    function ageEntries() {
        if (typeof GeoMap === 'undefined' || !GeoMap.anyOn()) return [];
        var cov = coverage;
        var list = [];
        if (cov && cov.hits) {
            list = Object.keys(cov.byAge).map(function (k) {
                return { key: k, hits: cov.byAge[k], meta: GeoMap.age(k),
                         lith: cov.lith[k] || 'mixed', on: true,
                         pct: Math.round(100 * cov.byAge[k] / cov.samples) };
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

    function swatchHTML(e, collapsible) {
        var bg = (typeof GeoPatterns !== 'undefined')
            ? 'background-image:' + GeoPatterns.swatchCSS(e.lith, e.meta.color) + ';background-size:16px 16px;'
            : 'background:' + esc(e.meta.color) + ';';
        // A share of the VIEW, not an area: the sample cannot support a km²
        // and the tooltip must not imply one.
        var where = e.on
            ? (e.pct >= 1 ? ' \u2014 about ' + e.pct + '% of this view' : ' \u2014 under 1% of this view')
            : ' \u2014 hidden';
        var title = e.meta.label + where + (e.on ? ' \u2014 tap to hide' : ' \u2014 tap to show again');
        return '<button type="button" class="ml-sw' + (e.on ? '' : ' off') +
            (collapsible ? ' ml-sw-x' : '') + '" aria-pressed="' + (!e.on) +
            '" aria-label="' + esc(e.meta.label) + (e.on ? ', shown' : ', hidden') + '"' +
            ' title="' + esc(title) + '"' +
            ' onclick="event.stopPropagation();MapLegend.toggleAge(\'' + esc(e.key) + '\')">' +
            '<i style="' + bg + '"></i></button>';
    }

    function ageSwatches() {
        var list = ageEntries();
        if (!list.length) return '';
        var head = list.slice(0, MAX), tail = list.slice(MAX);
        var html = head.map(function (e) { return swatchHTML(e, false); }).join('') +
                   tail.map(function (e) { return swatchHTML(e, true); }).join('');
        if (tail.length) {
            html += '<button type="button" class="ml-sw-more" id="ml-sw-more"' +
                ' aria-expanded="' + expanded + '"' +
                ' title="' + (expanded ? 'Show fewer periods'
                    : tail.length + ' more period(s), each covering less of this view \u2014 tap to show them') + '"' +
                ' onclick="event.stopPropagation();MapLegend.toggleMore()">' +
                (expanded ? '\u00d7' : '+' + tail.length) + '</button>';
        }
        // The way back. Only present once something is hidden, so the default
        // key carries no chrome — same rule as the strip itself.
        if (typeof GeoMap !== 'undefined' && GeoMap.agesOff && GeoMap.agesOff().size) {
            html += '<button type="button" class="ml-sw-all" title="Show every period again"' +
                ' onclick="event.stopPropagation();MapLegend.showAllAges()">all</button>';
        }
        return '<div class="ml-swatches" aria-label="Geology legend, most of this view first \u2014 tap a period to hide it">' +
            html + '</div>';
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

    function closeMenu() {
        if (menuEl) { menuEl.remove(); menuEl = null; }
        document.removeEventListener('click', closeMenu);
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

    /* Anchored to the control, flipped above it when it would run off the
     * bottom, and clamped to the viewport — the same placement the mode menu
     * uses, factored out because the geology chip opens a second one. */
    function place(el, btn) {
        document.body.appendChild(el);
        var r = btn.getBoundingClientRect();
        var w = el.offsetWidth, h = el.offsetHeight;
        el.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, r.right - w)) + 'px';
        el.style.top = (r.bottom + h + 6 > window.innerHeight
            ? Math.max(6, r.top - h - 4) : r.bottom + 4) + 'px';
        menuEl = el;
        setTimeout(function () { document.addEventListener('click', closeMenu); }, 0);
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

    function commodityOn(k) {
        if (typeof GeoMap === 'undefined') return false;
        var sheets = GeoMap.sheets() || {};
        return (GeoMap.order() || []).some(function (id) {
            return (sheets[id] || {}).available && GeoMap.commodityOn(id, k);
        });
    }

    function openGeoMenu(btn) {
        var already = menuEl && menuEl.dataset.kind === 'geo';
        closeMenu();
        if (already) return;
        var el = document.createElement('div');
        el.className = 'aoi-menu mode-menu ml-menu';
        el.dataset.kind = 'geo';
        el.setAttribute('role', 'menu');
        el.setAttribute('aria-label', 'Geology');

        var idx = commodityIndex();
        var keys = Object.keys(idx).sort();
        var anyOn = keys.some(commodityOn);

        var html = '<div class="mode-menu-head">Show only rock that can host</div>';
        // "All units" is the cleared state and therefore a row, not a button:
        // the absence of a filter is one of the states this menu chooses
        // between, and hiding it behind a "clear" link makes it look like a
        // repair rather than a choice.
        html += '<button type="button" class="aoi-menu-item mode-opt' + (anyOn ? '' : ' on') +
            '" role="menuitemradio" aria-checked="' + (!anyOn) + '" ' +
            'title="Draw every mapped unit" ' +
            'onclick="event.stopPropagation();MapLegend.geoAll()">' +
            '<span class="mode-mark radio"></span><i class="icon-layers ml-mi"></i>All units</button>';

        if (!keys.length) {
            html += '<div class="mode-menu-note">No sheet installed here lists a commodity affinity.</div>';
        }
        keys.forEach(function (k) {
            var on = commodityOn(k);
            html += '<button type="button" class="aoi-menu-item mode-opt' + (on ? ' on' : '') +
                '" role="menuitemcheckbox" aria-checked="' + on + '" ' +
                'title="' + esc((on ? 'Stop showing' : 'Also show') + ' the ' + idx[k] +
                    ' unit(s) whose rock type can host ' + k.replace(/_/g, ' ')) + '" ' +
                'onclick="event.stopPropagation();MapLegend.geoCommodity(\'' + esc(k) + '\')">' +
                '<span class="mode-mark check"></span><i class="icon-gem ml-mi"></i>' +
                esc(k.replace(/_/g, ' ')) + '<em class="ml-n">' + idx[k] + '</em></button>';
        });

        // The disclaimer is not optional and not a footnote elsewhere: this is
        // an inference over lithology, and every surface that offers it says
        // so. See docs/agents/overlays.md.
        /* ── How strong an affinity has to count ───────────────────────────
         *
         * Only once something is selected, because a floor with nothing to
         * filter is a control with no referent. "gold" is 36 units across
         * three sheets and 21 of them are weight 1 — placer ground downstream
         * of a lode, a quartzite that happens to sit in the belt. Shown flat,
         * the map says "gold is everywhere", which is the opposite of the
         * question. The floor is the one gesture that drops them.
         *
         * The counts are DERIVED (never typed): each level says how many units
         * it would leave, so the reader sees the cost of the choice before
         * making it, and a level that would leave nothing is refused rather
         * than offered.
         */
        if (anyOn && typeof GeoMap !== 'undefined' && GeoMap.weightCounts) {
            var n = GeoMap.weightCounts();
            var cur = GeoMap.minWeight();
            var LV = [[1, 'any', 'Every unit whose rock type is associated with it at all, including weak and derived ones (placer ground downstream of a lode).'],
                      [2, 'likely', 'Plausible hosts and better \u2014 drops weak and derived associations.'],
                      [3, 'classic', 'Only the textbook host rock for it.']];
            html += '<div class="mode-menu-head">How strong an affinity counts</div>';
            html += '<div class="ml-wrow" role="group" aria-label="Minimum strength of affinity">';
            LV.forEach(function (lv) {
                var dead = !n[lv[0]];
                html += '<button type="button" class="ml-wlv' + (cur === lv[0] ? ' on' : '') +
                    (dead ? ' dead' : '') + '" aria-pressed="' + (cur === lv[0]) + '"' +
                    ' title="' + esc(lv[2] + (dead ? ' \u2014 no unit in this selection qualifies.'
                                                   : ' ' + n[lv[0]] + ' unit(s).')) + '"' +
                    ' onclick="event.stopPropagation();MapLegend.geoMinWeight(' + lv[0] + ')">' +
                    esc(lv[1]) + '<em>' + n[lv[0]] + '</em></button>';
            });
            html += '</div>';
        }

        if (keys.length) {
            html += '<div class="mode-menu-note">An inference from rock type \u2014 nothing here ' +
                'counts, ranks or locates a deposit.</div>';
        }

        html += '<button type="button" class="aoi-menu-item ml-more" ' +
            'onclick="event.stopPropagation();MapLegend.toggleGeo()">' +
            '<i class="icon-eye-off ml-mi"></i>Hide geology</button>';
        html += '<button type="button" class="aoi-menu-item ml-more" ' +
            'onclick="event.stopPropagation();MapLegend.openSettings()">' +
            '<i class="icon-sliders-horizontal ml-mi"></i>Map settings\u2026' +
            '<em>full legend, opacity</em></button>';

        el.innerHTML = html;
        place(el, btn);
    }

    // ---------------------------------------------------------------
    // The strip
    // ---------------------------------------------------------------
    function render() {
        var host = document.getElementById('stats-map');
        if (!host) return;
        measureCoverage();
        var b = bm(), quiet = (b === 'dark') && !histOn() && !geoOn();
        host.classList.toggle('quiet', quiet);

        var opener = '<button type="button" class="ml-opener" id="ml-opener" ' +
            'aria-haspopup="menu" aria-label="Basemap and overlays" ' +
            'title="Basemap and overlays" onclick="event.stopPropagation();MapLegend.menu(this)">' +
            '<i class="icon-layers"></i></button>';

        if (quiet) { host.innerHTML = opener; return; }

        var chips = '';
        if (b !== 'dark') {
            var e = bmEntry(b);
            chips += '<button type="button" class="ml-chip base" title="' + esc(e[3]) +
                ' \u2014 tap to go back to the dark basemap" ' +
                'onclick="event.stopPropagation();MapLegend.pickBasemap(\'dark\')">' +
                '<i class="' + e[2] + '"></i>' + esc(e[1]) + '</button>';
        }
        if (histOn()) {
            var hm = histMeta();
            chips += '<button type="button" class="ml-chip hist" title="' +
                esc((hm && hm.name) || 'Historical map overlay') + ' \u2014 tap to hide" ' +
                'onclick="event.stopPropagation();MapLegend.toggleHist()">' +
                '<i class="icon-scroll-text"></i>Historical</button>';
        }
        if (geoOn()) {
            // Three sheets cover three countries, so "on, but not here" is the
            // ordinary case across most of Africa. Saying it on the chip is
            // the difference between a layer the user can trust and one they
            // report as broken; the sub-label wins over the filter note,
            // because "which units" is moot where none are drawn.
            var off = geoOffView();
            var note = off ? 'not in view' : geoFilterNote();
            chips += '<button type="button" class="ml-chip geo' +
                (off ? ' offview' : (note ? ' filtered' : '')) + '" title="' +
                esc(off ? 'Geology is on, but no mapped sheet reaches this view \u2014 tap to choose what to show, or to hide it'
                        : (note ? 'Geology, showing only ' + note + ' \u2014 tap to change what is shown'
                                : 'Geological units \u2014 tap to show only what can host a commodity')) + '" ' +
                'onclick="event.stopPropagation();MapLegend.geoMenu(this)">' +
                '<i class="icon-mountain"></i>Geology' +
                (note ? '<em>' + esc(note) + '</em>' : '') +
                '<i class="icon-chevron-down ml-caret"></i></button>';
        }

        host.innerHTML =
            '<div class="stats-divider"></div>' +
            '<div class="stats-header">Map</div>' +
            '<div class="ml-row">' + chips + opener + '</div>' +
            ageSwatches();
        // Not animated here: render() runs on every map idle, and re-staggering
        // an already-open strip on each pan is a flicker, not a transition.
        syncSwatchExpansion(false);
    }

    var MapLegend = {
        refresh: render,
        menu: openMenu,
        geoMenu: openGeoMenu,
        close: closeMenu,

        /* A swatch is a switch. Hiding the LAST visible period would empty the
         * drape, which reads as "no geology here" rather than as a filter, so
         * that click is refused in words instead. */
        toggleAge: function (key) {
            if (typeof GeoMap === 'undefined' || !GeoMap.toggleAge) return;
            var shown = ageEntries().filter(function (e) { return e.on; });
            if (GeoMap.ageOn(key) && shown.length <= 1) {
                if (typeof showToast === 'function') {
                    showToast('That is the only period in view',
                        'Hiding it would leave an empty map \u2014 hide something else first, or tap “all”.',
                        null, null, 'warning');
                }
                return;
            }
            GeoMap.toggleAge(key);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            render();
        },

        /* The strength floor. Refuses to empty the map — the standing rule
         * that an empty drape reads as "no data here", not as a filter. */
        geoMinWeight: function (w) {
            if (typeof GeoMap === 'undefined' || !GeoMap.setMinWeight) return;
            if (!GeoMap.setMinWeight(w)) {
                if (typeof showToast === 'function') {
                    showToast('Nothing that strong here',
                        'No unit in this selection is graded that highly \u2014 keeping the current setting.',
                        null, null, 'warning');
                }
                return;
            }
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            render();
            // The menu stays open: picking a floor is a comparison ("how much
            // does 'classic' actually drop?"), and a menu that closes on each
            // pick makes the comparison three round trips.
            var btn = document.querySelector('#stats-map .ml-chip.geo');
            if (btn && menuEl && menuEl.dataset.kind === 'geo') { closeMenu(); openGeoMenu(btn); }
        },

        showAllAges: function () {
            if (typeof GeoMap === 'undefined' || !GeoMap.clearAges) return;
            GeoMap.clearAges();
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            render();
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
            closeMenu();
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
            render();
        },

        /* Back to the whole rock map. showEverything() clears the hand-hidden
         * units and the lithology filter too, which is what "All units" says
         * on the tin — a row that cleared only the chips would leave the map
         * filtered while claiming otherwise. */
        geoAll: function () {
            closeMenu();
            if (typeof GeoMap !== 'undefined') GeoMap.showEverything();
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            render();
        },

        pickBasemap: function (id) {
            closeMenu();
            if (typeof switchBasemap === 'function') switchBasemap(id);
            setTimeout(render, 0);
        },

        toggleHist: function () {
            closeMenu();
            if (typeof HistMap === 'undefined') return;
            Promise.resolve(HistMap.toggle()).then(render);
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
        if (typeof map === 'undefined' || !map || !map.on) return;
        map.on('idle', function () { if (geoOn()) render(); });
    }

    function boot() {
        render();
        watchMap();
        if (typeof HistMap !== 'undefined') HistMap.ensureMeta().then(render).catch(function () {});
        if (typeof GeoMap !== 'undefined') GeoMap.ensureMeta().then(render).catch(function () {});
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
})();
