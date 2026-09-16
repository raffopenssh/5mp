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
                        TEST.assert(a.fn(), a.msg);
                        break;
                }
            }
        }
        
        return TEST.done();
    };
}
