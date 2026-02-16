/**
 * UI Test Suite for 5MP Conservation Globe
 * 
 * Run with: node tests/ui_tests.js
 * Or use via browser console at /?pwd=test2026&test=1
 * 
 * Each test is a URL + assertions. The share link encodes full UI state,
 * so testing is: navigate → wait → assert DOM state.
 */

const BASE_URL = process.env.BASE_URL || 'http://localhost:8000';
const PWD = process.env.PWD || 'test2026';

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
            console.log(`\n=== ${test.name} ===");
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
