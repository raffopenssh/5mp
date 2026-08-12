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
        if (comms.size) return Array.from(comms).join(' + ') + ' hosts';
        if (sh && sh.liths && sh.liths.size) return sh.liths.size + ' rock type' + (sh.liths.size === 1 ? '' : 's');
        if (isolated) return 'filtered';
        if (hidden) return hidden + ' hidden';
        return '';
    }

    /* The ages actually on the map right now, youngest first — from the
     * catalogue, so a rebuild that adds a period adds a swatch by itself. */
    function ageSwatches() {
        if (typeof GeoMap === 'undefined' || !GeoMap.anyOn()) return '';
        var sheets = GeoMap.sheets() || {};
        var seen = {}, list = [];
        (GeoMap.order() || []).forEach(function (id) {
            var s = GeoMap.state(id);
            if (!s || !s.on || !(sheets[id] || {}).available) return;
            (GeoMap.classes(id) || []).forEach(function (c) {
                if (s.isolate && s.isolate.size && !s.isolate.has(c.code)) return;
                if ((!s.isolate || !s.isolate.size) && s.hidden && s.hidden.has(c.code)) return;
                var key = c.age || 'unknown';
                if (seen[key]) { seen[key].liths[c.lith || 'mixed'] = 1; return; }
                seen[key] = { key: key, liths: {} };
                seen[key].liths[c.lith || 'mixed'] = 1;
                list.push(seen[key]);
            });
        });
        if (!list.length) return '';
        list.forEach(function (e) { e.meta = GeoMap.age(e.key); });
        list.sort(function (a, b) { return (a.meta.rank || 99) - (b.meta.rank || 99); });
        var MAX = 8, extra = Math.max(0, list.length - MAX);
        var html = list.slice(0, MAX).map(function (e) {
            // One representative ornament per age: the strip answers "what is
            // this colour", not "what is every texture" — that is the full
            // legend's job, one tap away.
            var lith = Object.keys(e.liths)[0] || 'mixed';
            var bg = (typeof GeoPatterns !== 'undefined')
                ? 'background-image:' + GeoPatterns.swatchCSS(lith, e.meta.color) + ';background-size:16px 16px;'
                : 'background:' + esc(e.meta.color) + ';';
            return '<i class="ml-sw" style="' + bg + '" title="' + esc(e.meta.label) + '"></i>';
        }).join('');
        if (extra) html += '<span class="ml-sw-more" title="Open Map Settings for the full legend">+' + extra + '</span>';
        return '<div class="ml-swatches" aria-label="Geology age legend">' + html + '</div>';
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
        var already = !!menuEl;
        closeMenu();
        if (already) return;
        var el = document.createElement('div');
        el.className = 'aoi-menu mode-menu ml-menu';
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
        document.body.appendChild(el);
        var r = btn.getBoundingClientRect();
        var w = el.offsetWidth, h = el.offsetHeight;
        el.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, r.right - w)) + 'px';
        el.style.top = (r.bottom + h + 6 > window.innerHeight
            ? Math.max(6, r.top - h - 4) : r.bottom + 4) + 'px';
        menuEl = el;
        setTimeout(function () { document.addEventListener('click', closeMenu); }, 0);
    }

    // ---------------------------------------------------------------
    // The strip
    // ---------------------------------------------------------------
    function render() {
        var host = document.getElementById('stats-map');
        if (!host) return;
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
            var note = geoFilterNote();
            chips += '<button type="button" class="ml-chip geo' + (note ? ' filtered' : '') + '" title="' +
                esc(note ? 'Geology, showing only ' + note + ' \u2014 tap to hide'
                         : 'Geological units \u2014 tap to hide') + '" ' +
                'onclick="event.stopPropagation();MapLegend.toggleGeo()">' +
                '<i class="icon-mountain"></i>Geology' +
                (note ? '<em>' + esc(note) + '</em>' : '') + '</button>';
        }

        host.innerHTML =
            '<div class="stats-divider"></div>' +
            '<div class="stats-header">Map</div>' +
            '<div class="ml-row">' + chips + opener + '</div>' +
            ageSwatches();
    }

    var MapLegend = {
        refresh: render,
        menu: openMenu,
        close: closeMenu,

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
    function boot() {
        render();
        if (typeof HistMap !== 'undefined') HistMap.ensureMeta().then(render).catch(function () {});
        if (typeof GeoMap !== 'undefined') GeoMap.ensureMeta().then(render).catch(function () {});
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
})();
