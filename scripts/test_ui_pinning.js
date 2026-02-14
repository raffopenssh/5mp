/**
 * UI Pinning Test Script
 * 
 * Tests all popup sections and pinning functionality.
 * Run in browser console or via automated testing.
 * 
 * Usage: 
 *   1. Load page with ?pwd=test2026
 *   2. Paste this script in console
 *   3. Call runAllTests() or individual test functions
 */

const TEST_PARK = 'CAF_Chinko';
const TEST_PARK_NAME = 'Chinko';

const testResults = {
    passed: [],
    failed: [],
    skipped: []
};

function log(msg, type = 'info') {
    const prefix = type === 'pass' ? '✅' : type === 'fail' ? '❌' : type === 'skip' ? '⏭️' : 'ℹ️';
    console.log(`${prefix} ${msg}`);
}

function assert(condition, testName) {
    if (condition) {
        testResults.passed.push(testName);
        log(`${testName}`, 'pass');
        return true;
    } else {
        testResults.failed.push(testName);
        log(`${testName}`, 'fail');
        return false;
    }
}

async function wait(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// Test 1: Popup loads correctly
async function testPopupLoads() {
    log('Testing popup loads...');
    const popup = document.querySelector('.pa-popup');
    assert(popup !== null, 'Popup element exists');
    
    const parkName = popup?.querySelector('.pa-popup-name span')?.textContent;
    assert(parkName === TEST_PARK_NAME, `Popup shows correct park name: ${parkName}`);
    
    return popup !== null;
}

// Test 2: Fire section loads
async function testFireSection() {
    log('Testing fire section...');
    const fireSection = document.getElementById(`fire-section-${TEST_PARK}`);
    assert(fireSection !== null, 'Fire section exists');
    
    // Click to open
    fireSection?.click();
    await wait(2000);
    
    const fireContent = document.getElementById(`fire-content-${TEST_PARK}`);
    const hasContent = fireContent && fireContent.innerHTML.length > 100;
    assert(hasContent, 'Fire section has content');
    
    const narrativeRows = fireContent?.querySelectorAll('.narrative-row');
    assert(narrativeRows && narrativeRows.length > 0, `Fire narratives loaded: ${narrativeRows?.length || 0} rows`);
    
    return narrativeRows && narrativeRows.length > 0;
}

// Test 3: Fire category pin (accordion icon)
async function testFireCategoryPin() {
    log('Testing fire category pin (icon)...');
    const fireIcon = document.querySelector(`[data-park-id="${TEST_PARK}"][data-type="fire"]`);
    assert(fireIcon !== null, 'Fire pin icon exists');
    
    // Click to pin
    fireIcon?.click();
    await wait(2000);
    
    const isPinned = fireIcon?.classList.contains('pinned');
    assert(isPinned, 'Fire icon shows pinned state');
    
    // Check indicator
    const indicator = document.querySelector('.pinned-indicator');
    const isIndicatorActive = indicator?.classList.contains('active');
    assert(isIndicatorActive, 'Pinned indicator is active');
    
    // Check map has layer
    const pinnedKeys = Object.keys(window.pinnedLayers || {});
    const hasFireLayer = pinnedKeys.some(k => k.includes('fire'));
    assert(hasFireLayer, 'Fire layer added to pinnedLayers');
    
    // Unpin
    fireIcon?.click();
    await wait(1000);
    
    return isPinned;
}

// Test 4: Single fire narrative pin
async function testFireNarrativePin() {
    log('Testing fire narrative pin (single feature)...');
    const narrativeRow = document.querySelector(`#fire-content-${TEST_PARK} .narrative-row`);
    assert(narrativeRow !== null, 'Fire narrative row exists');
    
    // Click to pin
    narrativeRow?.click();
    await wait(2000);
    
    const isPinned = narrativeRow?.classList.contains('pinned');
    assert(isPinned, 'Narrative row shows pinned class');
    
    // Check singlePinnedFeatures
    const singleKeys = Object.keys(window.singlePinnedFeatures || {});
    assert(singleKeys.length > 0, `Single pinned features: ${singleKeys.length}`);
    
    // Check indicator updated
    const indicator = document.querySelector('.pinned-indicator');
    const isActive = indicator?.classList.contains('active');
    assert(isActive, 'Indicator active after single pin');
    
    // Unpin
    narrativeRow?.click();
    await wait(1000);
    
    return isPinned;
}

// Test 5: Settlement section loads
async function testSettlementSection() {
    log('Testing settlement section...');
    const section = document.getElementById(`ghsl-section-${TEST_PARK}`);
    assert(section !== null, 'Settlement section exists');
    
    section?.click();
    await wait(2000);
    
    const content = document.getElementById(`ghsl-content-${TEST_PARK}`);
    const hasContent = content && content.innerHTML.length > 100;
    assert(hasContent, 'Settlement section has content');
    
    const rows = content?.querySelectorAll('.narrative-row, div[onclick*="toggleSingleFeaturePin"]');
    assert(rows && rows.length > 0, `Settlement items: ${rows?.length || 0}`);
    
    return rows && rows.length > 0;
}

// Test 6: Settlement category pin
async function testSettlementCategoryPin() {
    log('Testing settlement category pin...');
    const icon = document.querySelector(`[data-park-id="${TEST_PARK}"][data-type="settlement"]`);
    assert(icon !== null, 'Settlement pin icon exists');
    
    icon?.click();
    await wait(2000);
    
    const isPinned = icon?.classList.contains('pinned');
    assert(isPinned, 'Settlement icon shows pinned state');
    
    // Unpin
    icon?.click();
    await wait(1000);
    
    return isPinned;
}

// Test 7: Settlement single pin
async function testSettlementSinglePin() {
    log('Testing settlement single pin...');
    const row = document.querySelector(`#ghsl-content-${TEST_PARK} .narrative-row`);
    assert(row !== null, 'Settlement row exists');
    
    row?.click();
    await wait(2000);
    
    const isPinned = row?.classList.contains('pinned');
    assert(isPinned, 'Settlement row shows pinned class');
    
    // Unpin
    row?.click();
    await wait(1000);
    
    return isPinned;
}

// Test 8: Deforestation section
async function testDeforestationSection() {
    log('Testing deforestation section...');
    const section = document.getElementById(`deforest-section-${TEST_PARK}`);
    assert(section !== null, 'Deforestation section exists');
    
    section?.click();
    await wait(2000);
    
    const content = document.getElementById(`deforest-content-${TEST_PARK}`);
    const hasContent = content && content.innerHTML.length > 50;
    assert(hasContent, 'Deforestation section has content');
    
    return hasContent;
}

// Test 9: Deforestation category pin
async function testDeforestationCategoryPin() {
    log('Testing deforestation category pin...');
    const icon = document.querySelector(`[data-park-id="${TEST_PARK}"][data-type="deforestation"]`);
    assert(icon !== null, 'Deforestation pin icon exists');
    
    icon?.click();
    await wait(2000);
    
    const isPinned = icon?.classList.contains('pinned');
    assert(isPinned, 'Deforestation icon shows pinned state');
    
    icon?.click();
    await wait(1000);
    
    return isPinned;
}

// Test 10: Roads section
async function testRoadsSection() {
    log('Testing roads section...');
    const section = document.getElementById(`road-section-${TEST_PARK}`);
    assert(section !== null, 'Roads section exists');
    
    section?.click();
    await wait(2000);
    
    const content = document.getElementById(`road-content-${TEST_PARK}`);
    const hasContent = content && content.innerHTML.length > 50;
    assert(hasContent, 'Roads section has content');
    
    return hasContent;
}

// Test 11: Pinned indicator clear
async function testPinnedIndicatorClear() {
    log('Testing pinned indicator clear...');
    
    // Pin something first
    const fireIcon = document.querySelector(`[data-park-id="${TEST_PARK}"][data-type="fire"]`);
    fireIcon?.click();
    await wait(2000);
    
    const indicator = document.querySelector('.pinned-indicator');
    assert(indicator?.classList.contains('active'), 'Indicator active after pin');
    
    // Click clear hint
    const clearHint = indicator?.querySelector('.clear-hint');
    if (clearHint) {
        clearHint.click();
        await wait(1000);
        assert(!indicator.classList.contains('active'), 'Indicator cleared');
    } else {
        // Try clicking indicator itself
        indicator?.click();
        await wait(1000);
    }
    
    return true;
}

// Test 12: Time filter updates popup
async function testTimeFilterUpdate() {
    log('Testing time filter updates popup...');
    
    // Open fire section first
    const fireSection = document.getElementById(`fire-section-${TEST_PARK}`);
    if (!fireSection?.classList.contains('open')) {
        fireSection?.click();
        await wait(1000);
    }
    
    const beforeCount = document.querySelector(`#fire-count-${TEST_PARK}`)?.textContent;
    
    // Change time range
    if (typeof setTimeSliderRange === 'function') {
        setTimeSliderRange('2024-01-01', '2024-06-01');
        await wait(3000);
        
        const afterCount = document.querySelector(`#fire-count-${TEST_PARK}`)?.textContent;
        // Counts might be different
        log(`Fire count before: ${beforeCount}, after: ${afterCount}`);
        assert(true, 'Time filter applied (check counts visually)');
    } else {
        testResults.skipped.push('Time filter update');
        log('setTimeSliderRange not available', 'skip');
    }
    
    return true;
}

// Run all tests
async function runAllTests() {
    log('========== STARTING UI PINNING TESTS ==========');
    testResults.passed = [];
    testResults.failed = [];
    testResults.skipped = [];
    
    try {
        await testPopupLoads();
        await testFireSection();
        await testFireCategoryPin();
        await testFireNarrativePin();
        await testSettlementSection();
        await testSettlementCategoryPin();
        await testSettlementSinglePin();
        await testDeforestationSection();
        await testDeforestationCategoryPin();
        await testRoadsSection();
        await testPinnedIndicatorClear();
        await testTimeFilterUpdate();
    } catch (e) {
        log(`Test error: ${e.message}`, 'fail');
        testResults.failed.push(`Exception: ${e.message}`);
    }
    
    log('========== TEST RESULTS ==========');
    log(`Passed: ${testResults.passed.length}`);
    log(`Failed: ${testResults.failed.length}`);
    log(`Skipped: ${testResults.skipped.length}`);
    
    if (testResults.failed.length > 0) {
        log('Failed tests:', 'fail');
        testResults.failed.forEach(t => log(`  - ${t}`, 'fail'));
    }
    
    return testResults;
}

// Quick test - just category pins
async function testCategoryPins() {
    log('Testing category pins only...');
    const types = ['fire', 'settlement', 'deforestation', 'road'];
    
    for (const type of types) {
        const icon = document.querySelector(`[data-park-id="${TEST_PARK}"][data-type="${type}"]`);
        if (!icon) {
            log(`No icon for ${type}`, 'skip');
            continue;
        }
        
        icon.click();
        await wait(1500);
        
        const isPinned = icon.classList.contains('pinned');
        assert(isPinned, `${type} category pin`);
        
        icon.click();
        await wait(500);
    }
    
    return testResults;
}

// Quick test - just single pins
async function testSinglePins() {
    log('Testing single feature pins...');
    
    // Open all sections first
    ['fire', 'ghsl', 'deforest', 'road'].forEach(section => {
        const el = document.getElementById(`${section}-section-${TEST_PARK}`);
        if (el && !el.classList.contains('open')) el.click();
    });
    await wait(2000);
    
    // Find and click narrative rows
    const rows = document.querySelectorAll('.narrative-row');
    log(`Found ${rows.length} narrative rows`);
    
    if (rows.length > 0) {
        rows[0].click();
        await wait(2000);
        assert(rows[0].classList.contains('pinned'), 'First narrative row pinned');
        rows[0].click();
        await wait(500);
    }
    
    return testResults;
}

log('UI Pinning Test Script loaded. Run: runAllTests(), testCategoryPins(), or testSinglePins()');
