/**
 * UI Test Suite for 5MP Conservation Globe
 * 
 * Run with: node tests/ui_tests.js
 * Or use via browser console at /?pwd=test2026&test=1
 * 
 * Each test is a URL + assertions. The share link encodes full UI state,
 * so testing is: navigate → wait → assert DOM state.
 */

// Runs under Node (module export) and pasted into a browser console (no `process`).
const ENV = (typeof process !== 'undefined' && process.env) || {};
const BASE_URL = ENV.BASE_URL || 'http://localhost:8000';
const PWD = ENV.PWD || 'test2026';

// Test definitions: each test has a URL (relative) and assertions to run
const UI_TESTS = [
    // === BASIC PAGE LOAD ===
    {
        name: 'page_loads',
        url: '',
        wait: 2000,
        assertions: [
            { type: 'exists', selector: '#map', msg: 'Map container exists' },
            { type: 'exists', selector: '.stats-panel', msg: 'Stats panel exists' },
            { type: 'exists', selector: '.toolbar-btn', msg: 'Toolbar exists' },
            { type: 'visible', selector: '.mapboxgl-canvas', msg: 'Map canvas visible' },
        ]
    },
    
    // === FILTER PANEL ===
    {
        name: 'filter_panel_opens',
        url: '&panel=filter',
        wait: 1500,
        assertions: [
            { type: 'visible', selector: '#filter-panel', msg: 'Filter panel visible' },
            { type: 'exists', selector: '.movement-toggle', msg: 'Movement toggles exist' },
            { type: 'exists', selector: '#keystones-toggle-btn', msg: 'Keystones toggle exists' },
        ]
    },
    
    // === STAR MODAL ===
    {
        name: 'star_modal_opens',
        url: '&panel=star',
        wait: 1500,
        assertions: [
            { type: 'visible', selector: '#star-modal', msg: 'Star modal visible' },
            { type: 'exists', selector: '.star-modal-content', msg: 'Star modal content exists' },
        ]
    },
    
    // === STAR WITH ITEMS ===
    {
        name: 'starred_parks_appear',
        url: '&panel=star&starred_parks=CAF_Chinko,COD_Virunga',
        wait: 2000,
        assertions: [
            { type: 'visible', selector: '#star-modal', msg: 'Star modal visible' },
            { type: 'countMin', selector: '.starred-park-item', n: 2, msg: 'At least 2 starred parks' },
        ]
    },
    
    // === ADMIN PANEL ===
    {
        name: 'admin_panel_opens',
        url: '&panel=admin',
        wait: 1500,
        assertions: [
            { type: 'visible', selector: '#admin-panel', msg: 'Admin panel visible' },
            { type: 'exists', selector: '.admin-tab', msg: 'Admin tabs exist' },
            { type: 'hasClass', selector: '[data-tab="uploads"]', className: 'active', msg: 'Uploads tab active by default' },
        ]
    },
    
    // === ADMIN TABS ===
    {
        name: 'admin_learning_tab',
        url: '&panel=admin&admin_tab=learning',
        wait: 1500,
        assertions: [
            { type: 'visible', selector: '#admin-panel', msg: 'Admin panel visible' },
            { type: 'hasClass', selector: '[data-tab="learning"]', className: 'active', msg: 'Learning tab active' },
            { type: 'visible', selector: '#tab-learning', msg: 'Learning content visible' },
        ]
    },
    
    // === UPLOAD MODAL ===
    {
        name: 'upload_modal_opens',
        url: '&panel=upload',
        wait: 1500,
        assertions: [
            { type: 'visible', selector: '#modal-upload', msg: 'Upload modal visible' },
            { type: 'exists', selector: 'input[type="file"]', msg: 'File input exists' },
        ]
    },
    
    // === NOTIFICATION DROPDOWN ===
    {
        name: 'notification_dropdown',
        url: '&notif=1',
        wait: 1500,
        assertions: [
            { type: 'hasClass', selector: '#notification-dropdown', className: 'open', msg: 'Notification dropdown open' },
        ]
    },
    
    // === PARK POPUP ===
    {
        name: 'park_popup_opens',
        url: '&popup=CAF_Chinko',
        wait: 3000,
        assertions: [
            { type: 'exists', selector: '.pa-popup', msg: 'Park popup exists' },
            { type: 'text', selector: '.pa-popup', text: 'Chinko', msg: 'Popup shows Chinko' },
        ]
    },
    
    // === ACCORDION SECTIONS ===
    {
        name: 'accordion_fire_section',
        url: '&popup=CAF_Chinko&sections=fire',
        wait: 3500,
        assertions: [
            { type: 'exists', selector: '.pa-popup', msg: 'Popup exists' },
            { type: 'hasClass', selector: '#fire-section-CAF_Chinko', className: 'open', msg: 'Fire section open' },
        ]
    },
    {
        name: 'accordion_multiple_sections',
        url: '&popup=COD_Virunga&sections=fire,deforestation,settlement',
        wait: 3500,
        assertions: [
            { type: 'hasClass', selector: '#fire-section-COD_Virunga', className: 'open', msg: 'Fire section open' },
            { type: 'hasClass', selector: '#deforestation-section-COD_Virunga', className: 'open', msg: 'Deforestation section open' },
            { type: 'hasClass', selector: '#settlement-section-COD_Virunga', className: 'open', msg: 'Settlement section open' },
        ]
    },
    
    // === PINNED LAYERS ===
    {
        name: 'pinned_fire_trajectory',
        url: '&pinned=CAF_Chinko:fire_trajectory',
        wait: 3000,
        assertions: [
            { type: 'exists', selector: '.pinned-layer-item', msg: 'Pinned layer item exists' },
            { type: 'countMin', selector: '.pinned-layer-item', n: 1, msg: 'At least 1 pinned layer' },
            // A chevron is a claim about day order; only supported/weak
            // chains may carry one (docs/agents/fire.md "Link evidence").
            { type: 'fn', fn: () => { const d = TEST.fireDirection(); return d.length >= 1 && d.every(x => x.gated); }, msg: 'Every fire arrow layer is gated on the evidence tier' },
        ]
    },

    // === SEASON: EARLY-BURN GROUND (docs/agents/fire.md "Early-burn ground") ===
    // The chip toggles the layer and the share link follows; the Season menu
    // shows the graded swatch; the tip at a cell names seasons early / held,
    // days ahead and the month — every number from the answer, none typed.
    {
        name: 'season_entry_ground',
        url: '&lat=6.4&lng=24&z=7.5&from=2024-08-01&to=2024-12-01&season=front,entry',
        wait: 8000,
        assertions: [
            { type: 'fn', fn: () => { const e = TEST.fireEntry(); return e.on && e.drawn && e.status === 'ok' && e.area === 'CAF_Chinko' && e.seasonsHeld >= 3 && e.count > 0; }, msg: 'Entry ground on, drawn, with cells and a season count from the writer' },
            { type: 'fn', fn: () => { const e = TEST.fireEntry(); return /early-burn cells in view/.test(e.chip || '') && new RegExp(e.seasonsHeld + ' seasons').test(e.chip); }, msg: 'Map-strip chip counts cells in view and names the seasons held' },
            { type: 'fn', fn: () => {
                const m = FireSeason.entryMeta(), g = m.grid, c = m.cells.slice().sort((a, b) => b[2] - a[2])[0];
                const e = TEST.fireEntry(g.x0 + g.res * (c[0] + 0.5), g.y0 + g.res * (c[1] + 0.5));
                return e.cell && e.cell.early === c[2] && e.cell.held === c[3] && new RegExp(c[2] + ' of ' + c[3] + ' seasons').test(e.tip || '') && /days.*before the local front/.test(e.tip) && /Early-burn ground/.test(e.tip);
            }, msg: 'Tip at a cell says "early in N of M seasons", days ahead, month' },
            { type: 'fn', fn: () => {
                MapLegend.fireSeasonMenu(document.querySelector('#stats-map .ml-chip.fs'));
                const grade = document.querySelectorAll('.mode-menu .fs-ramp-cap .fs-entry-sw');
                const sw = grade.length === 1 && /159, ?18, ?57/.test(grade[0].firstChild.style.background) && /251, ?113, ?133/.test(grade[0].lastChild.style.background)
                    && document.querySelectorAll('.mode-menu .fs-entry-life .fs-entry-sw').length >= 4 && /dormant/.test(document.querySelector('.mode-menu .fs-entry-life').textContent);
                MapLegend.fireSeasonSet('entry', false); const off = TEST.fireEntry();
                MapLegend.fireSeasonSet('entry', true); const on = TEST.fireEntry();
                return sw && !off.on && !off.drawn && off.share.season === 'front' && on.on && on.drawn && /entry/.test(on.share.season);
            }, msg: 'Season menu shows the rose grade swatch (rose-800 → rose-400) and the season-life row; the chip toggles the layer and the share link' },
            // The cell's life over the season, as the animator draws it: the
            // same function the paint reads (entryState), checked at four
            // playhead positions of one real cell.
            { type: 'fn', fn: () => {
                const m = FireSeason.entryMeta(), g = m.grid, c = m.cells.slice().sort((a, b) => b[2] - a[2])[0];
                const cell = FireSeason.entryAt(g.x0 + g.res * (c[0] + 0.5), g.y0 + g.res * (c[1] + 0.5));
                if (!cell || cell.fb == null || cell.uf == null) return false;
                const S = FireSeason.entryState, due = cell.uf - cell.days;
                const ign = S(cell, cell.fb + 0.5), cooled = S(cell, cell.fb + 30), dormant = S(cell, Math.min(cell.fb, due) - 60), later = S(cell, Math.min(cell.fb, due) - 10);
                return ign.flash > 0.8 && /first detection today/.test(ign.word) && cooled.flash === 0 && cooled.mul === 1 && dormant.mul < 0.3 && later.mul > dormant.mul && S(cell, cell.fb + 200).ash === 1 && FireSeason.entryColor(0.4) === '#9f1239' && FireSeason.entryColor(0.7) === '#fb7185';
            }, msg: 'entryState: dormant (near-hidden) → due rising → ignition flash → cooled full weight → ash; the rose ramp ends are the documented ones' },
        ]
    },
    
    // === SEASON: COMPARE YEARS + PATROL ISOCHRONES ===
    // The slider still names the reference season; earlier seasons are ADDED
    // beside it (season_vs=), each in the hue of how many seasons back it is,
    // its 15-day lines labelled with the year, aligned by day of season. The
    // patrol isochrones are tenant-scoped: the sandbox owns no tracks, so the
    // link's `patrol` is dropped and the menu row says why, not an empty layer.
    {
        name: 'season_compare_and_patrol_scope',
        url: '&park_focus=CAF_Chinko&from=2025-07-01&to=2026-06-30&season=front,patrol&season_vs=2020%2F21,2022%2F23&layers=none&bbox=22.6,5.2,25.6,8.2',
        wait: 9000,
        assertions: [
            { type: 'fn', fn: () => { const f = TEST.fireSeason(); return f.front && f.seasonShown === '2025/26' && f.compare.length === 2 && f.compareAvailable.indexOf('2025/26') < 0 && f.compareAvailable.length >= 6; }, msg: 'Reference season from the slider; the compare list offers every other stored season' },
            { type: 'fn', fn: () => { const f = TEST.fireSeason(); return f.compareFeatures > 10 && f.compareYears.join(',') === '2020/21,2022/23' && f.compareColors.length === 2 && f.compareColors.indexOf(FireSeason.compareColor('2020/21')) >= 0; }, msg: 'Ghost fronts of both seasons loaded, one hue per season (cmpColor)' },
            { type: 'fn', fn: () => { const fs = map.querySourceFeatures('fireseason-cmp-src'); const lab = fs.filter(f => f.properties.label); const t0 = Date.parse(FireSeason.meta().season_start + 'T00:00:00Z'); return lab.length > 0 && lab.every(f => /\u201921|\u201923$/.test(f.properties.text)) && fs.every(f => f.properties.tr === t0 + f.properties.dos * 86400000); }, msg: 'Labels carry the year; every ghost line sits on the reference calendar by day of season' },
            // Tenant-scoped: a sandbox WITHOUT tracks drops `patrol` from the
            // link and has no Patrols chip; one WITH tracks (a test upload
            // gives test2026 some) keeps it, and the chip says honestly that
            // none of them are here (Chinko).
            { type: 'fn', fn: () => { const f = TEST.fireSeason(); if (f.share.season_vs !== '2020/21,2022/23') return false;
                return window.HAS_PATROL === false ? (!/patrol/.test(f.share.season || '') && !f.patrol && !f.patrolAllowed && !document.querySelector('#stats-map .ml-chip.pt'))
                    : (/patrol/.test(f.share.season || '') && f.patrol && f.patrolAllowed && /no patrol data/.test(f.patrolsChip || '') && !f.pressure); }, msg: 'Share link carries season_vs; patrol is dropped for a tenant without tracks, kept (chip: no patrol data here) for one with' },
            { type: 'fn', fn: () => {
                // Patrols live in their own chip/menu, not the Season one; for
                // a tenant without tracks the chip is absent and the layers
                // menu's Patrols row is refused with the reason.
                MapLegend.fireSeasonMenu(document.querySelector('#stats-map .ml-chip.fs'));
                const rows = [...document.querySelectorAll('.mode-menu .aoi-menu-item')];
                const noPatrolRow = !rows.some(r => /Patrol/.test(r.textContent)) && (!!document.querySelector('#stats-map .ml-chip.pt') === (window.HAS_PATROL !== false));
                const chips = document.querySelectorAll('.mode-menu .fs-cmp .filter-chip');
                const onChips = [...chips].filter(c => c.classList.contains('on')).map(c => c.textContent);
                const ok = noPatrolRow && chips.length >= 6 && onChips.join(',') === '2022/23,2020/21';
                MapLegend.fireSeasonCompare('2020/21');
                const after = TEST.fireSeason();
                return ok && after.compare.join(',') === '2022/23' && after.share.season_vs === '2022/23';
            }, msg: 'Season menu: no patrol row (Patrols is its own chip); year chips in the filter-chip style, toggling one updates the layer and the link' },
        ]
    },

    // === SEASON: EVERY PARK IN VIEW — PATROL PRESSURE + EARLY-BURN GROUND ===
    // Unfocused, the patrol/pressure and early-burn layers used to draw ONE
    // area (the park under the view centre). Now every park in the box comes
    // (server bbox mode); the sandbox's few test tracks sit where three park
    // grids overlap (Kilombero / Nyerere / Selous), so its pressure rings
    // must come for more than the reference, and each early-burn field is
    // its own canvas layer. Owned cells: a lattice cell belongs to the
    // smallest grid that has it, so no square is drawn twice.
    {
        name: 'season_every_park_in_view',
        url: '&lat=-8.4&lng=36.2&z=7&from=2026-01-01&to=2026-09-16&season=entry,pressure&layers=none',
        wait: 9000,
        assertions: [
            { type: 'fn', fn: () => {
                if (window.HAS_PATROL === false) return true;   // a tenant without tracks has no pressure layer to test
                const feats = map.getSource('fireseason-pressure-src')._data.features;
                const areas = new Set(feats.map(f => f.properties.area || FireSeason.patrolMeta().area));
                window.__everyParkProbe = { n: feats.length, areas: [...areas] };
                return feats.length > 0 && areas.size >= 2 && feats.every(f => f.properties.kind === 'pressure' && f.properties.text === String(f.properties.level));
            }, msg: 'Pressure rings for more than the reference park, every line labelled with its level' },
            { type: 'fn', fn: () => {
                const lyrs = map.getStyle().layers.map(l => l.id).filter(id => /^fireseason-entry-/.test(id));
                const e = TEST.fireEntry();
                return lyrs.length >= 2 && e.on && e.drawn && lyrs.every(id => map.getLayoutProperty(id, 'visibility') !== 'none');
            }, msg: 'Early-burn ground: one canvas field per other park in view, visible with the reference' },
            { type: 'fn', fn: () => {
                // one cell, one owner: the same lattice cell is never on two fields
                const seen = {}, dup = [];
                map.getStyle().layers.map(l => l.id).filter(id => /^fireseason-entry/.test(id)).forEach(id => {
                    const A = FireSeason.entryFieldFor ? FireSeason.entryFieldFor(id) : null; if (!A) return;
                    const g = A.grid(), ox = Math.round(g.x0 / g.res), oy = Math.round(g.y0 / g.res);
                    (A.sparse() || []).forEach(c => { const k = (oy + c.iy) + ',' + (ox + c.ix); if (seen[k]) dup.push(k); seen[k] = 1; });
                });
                return Object.keys(seen).length > 0 && dup.length === 0;
            }, msg: 'Owned cells: no lattice cell is drawn by two fields' },
            { type: 'fn', fn: () => {
                MapLegend.fireSeasonMenu(document.querySelector('#stats-map .ml-chip.fs'));
                const txt = document.querySelector('.mode-menu').textContent;
                return /Drawn for \d+ areas in view/.test(txt);
            }, msg: 'Legend says how many areas are drawn' },
        ]
    },

    // === ANIMATOR: EVERY SEASON OF THE WINDOW, ASHING OUT ===
    // A 2020–2026 window used to draw no front until the playhead reached the
    // season the slider ENDS in. Now every season the window touches is on
    // the source at its own dates once the animator asks; the playhead's
    // season is the one the stats row speaks for; earlier seasons are
    // filtered down to their 30-day lines (ash), never removed.
    {
        name: 'anim_front_every_season',
        url: '&park_focus=CAF_Chinko&from=2020-01-01&to=2026-06-30&season=front&layers=none&anim=&anim_paused=1&anim_t=2020-01-01&bbox=22.6,5.2,25.6,8.2',
        wait: 9000,
        assertions: [
            { type: 'fn', fn: async () => {
                if (!window.Animator || !Animator.seek) return false;
                Animator.seek('2022-01-15');                       // mid 2021/22 — a season the slider does NOT end in
                await new Promise(r => setTimeout(r, 6000));       // the history fetch (one request per season)
                Animator.seek('2022-01-16');
                await new Promise(r => setTimeout(r, 1500));
                const src = map.getSource('fireseason-front-src'), feats = (src && src._data && src._data.features) || [];
                const seasons = new Set(feats.map(f => f.properties.season).filter(Boolean));
                const m = FireSeason.meta();
                window.__animSeasonProbe = { n: feats.length, seasons: seasons.size, meta: m && m.season, pct: m && m.front_reached_pct };
                return seasons.size >= 5 && m && m.season === '2021/22' && m.at_playhead && m.front_reached_pct > 0;
            }, msg: 'Contours of ≥ 5 seasons loaded; the stats row speaks for the season the playhead is in (2021/22), not the slider\'s end' },
            { type: 'fn', fn: () => {
                // Ash is a filter, not a removal: 2020/21's 30-day lines
                // survive, its 5-day lines do not; 2021/22's do.
                const t = Date.parse('2022-01-16T00:00:00Z'), D = 86400000;
                const rendered = map.queryRenderedFeatures({ layers: ['fireseason-front'] }).map(f => f.properties);
                const old5 = rendered.filter(p => !p.label && t - p.t > 240 * D), old30 = rendered.filter(p => p.l30 && t - p.t > 240 * D);
                const cur = rendered.filter(p => t - p.t < 200 * D);
                const ancient = rendered.filter(p => t - p.t > 730 * D);   // ash lasts two seasons, then goes
                return old5.length === 0 && old30.length > 0 && cur.length > 0 && ancient.length === 0 && old30.every(p => /\u2019\d\d$/.test(p.text));
            }, msg: 'Older seasons keep only their 30-day lines, labelled with their own year, for two seasons; the current season draws all of them' },
            { type: 'fn', fn: () => { Animator.close(); const n = map.getSource('fireseason-front-src')._data.features.length; const s = new Set(map.getSource('fireseason-front-src')._data.features.map(f => f.properties.season)); return n > 0 && s.size === 1; },
              msg: 'Closing the animator puts the reference season alone back on the map' },
        ]
    },

    // === SEARCH ===
    {
        name: 'search_query',
        url: '&q=virunga',
        wait: 2000,
        assertions: [
            { type: 'value', selector: '#search-input', text: 'virunga', msg: 'Search input has query' },
        ]
    },
    
    // === BOUNDING BOX ===
    {
        name: 'bbox_filter',
        url: '&bbox=20,-5,30,5',
        wait: 2000,
        assertions: [
            { type: 'visible', selector: '#active-filter-bbox', msg: 'Bbox filter indicator visible' },
        ]
    },
    
    // === COUNTRY FILTER ===
    {
        name: 'country_filter',
        url: '&country=COD',
        wait: 2000,
        assertions: [
            // Country filter should be active
            { type: 'fn', fn: () => typeof currentCountryFilter !== 'undefined' && currentCountryFilter === 'COD', msg: 'Country filter is COD' },
        ]
    },
    
    // === MAP POSITION ===
    {
        name: 'map_position',
        url: '&lat=0&lng=25&z=5',
        wait: 2000,
        assertions: [
            { type: 'fn', fn: () => {
                const c = map.getCenter();
                return Math.abs(c.lat) < 1 && Math.abs(c.lng - 25) < 1;
            }, msg: 'Map centered near (0, 25)' },
        ]
    },
    
    // === KEYSTONES TOGGLE ===
    {
        name: 'keystones_disabled',
        url: '&keystones=0',
        wait: 2000,
        assertions: [
            { type: 'fn', fn: () => typeof keystonesEnabled !== 'undefined' && !keystonesEnabled, msg: 'Keystones disabled' },
        ]
    },
    
    // === COMBINED STATE ===
    {
        name: 'complex_state',
        url: '&popup=TZA_Serengeti&sections=fire,species&panel=filter&starred_parks=TZA_Serengeti',
        wait: 4000,
        assertions: [
            { type: 'exists', selector: '.pa-popup', msg: 'Popup exists' },
            { type: 'hasClass', selector: '#fire-section-TZA_Serengeti', className: 'open', msg: 'Fire section open' },
            { type: 'visible', selector: '#filter-panel', msg: 'Filter panel visible' },
        ]
    },
];

// Export for use in browser or Node
if (typeof module !== 'undefined') {
    module.exports = { UI_TESTS, BASE_URL, PWD };
}

// Browser runner - paste this into console at /?pwd=test2026&test=1
if (typeof window !== 'undefined' && window.TEST) {
    window.runUITests = async function(testNames) {
        const tests = testNames 
            ? UI_TESTS.filter(t => testNames.includes(t.name))
            : UI_TESTS;
        
        console.log(`Running ${tests.length} UI tests...`);
        
        for (const test of tests) {
            console.log(`\n=== ${test.name} ===`);
            // Note: In browser, we can only run assertions for current URL state
            // Full URL navigation requires Playwright/Puppeteer
            for (const a of test.assertions) {
                switch (a.type) {
                    case 'exists':
                        TEST.assertExists(a.selector, a.msg);
                        break;
                    case 'visible':
                        TEST.assertVisible(a.selector, a.msg);
                        break;
                    case 'text':
                        TEST.assertText(a.selector, a.text, a.msg);
                        break;
                    case 'count':
                        TEST.assertCount(a.selector, a.n, a.msg);
                        break;
                    case 'countMin':
                        TEST.assertCountMin(a.selector, a.n, a.msg);
                        break;
                    case 'hasClass':
                        TEST.assert(TEST.hasClass(a.selector, a.className), a.msg);
                        break;
                    case 'value':
                        const el = document.querySelector(a.selector);
                        TEST.assert(el && el.value.includes(a.text), a.msg);
                        break;
                    case 'fn':
                        TEST.assert(await a.fn(), a.msg);   // a fn may be async (the animator's history fetch)
                        break;
                }
            }
        }
        
        return TEST.done();
    };
}
