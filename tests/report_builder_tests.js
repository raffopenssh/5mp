// Report Builder Tests - Run in browser console with test=1
// Usage: Copy-paste this into browser console at http://localhost:8000/?pwd=test2026&test=1

async function runReportBuilderTests() {
    console.log('\n==========================================');
    console.log('🧪 Report Builder Test Suite');
    console.log('==========================================\n');
    
    const results = [];
    
    // Helper function
    function test(name, condition, detail = '') {
        const pass = !!condition;
        const icon = pass ? '✅' : '❌';
        console.log(`${icon} ${name}${detail ? ': ' + detail : ''}`);
        results.push({ name, pass, detail });
        return pass;
    }
    
    // Test 1: Check if determineSmartDefault exists
    test('determineSmartDefault function exists', typeof determineSmartDefault === 'function');
    
    // Test 2: Single park detection
    console.log('\n📊 Test: Single Park Profile');
    starredItems.parks = [{ id: 'TZA_Serengeti', name: 'Serengeti' }];
    starredItems.bboxes = [];
    reportDataCache.parks.set('TZA_Serengeti', {
        fire: { total_fires: 86640 },
        settlement: { settlement_count: 210 },
        deforestation: { total_loss_km2: 0.0 },
        species: { total_count: 342 }
    });
    
    const sd1 = determineSmartDefault();
    test('Single park → Scientific profile', sd1.profile === 'scientific', sd1.reason);
    test('All sections enabled', Object.values(sd1.sections).filter(v => v).length === 9);
    test('skipZeros disabled', sd1.filters.skipZeros === false);
    test('Detail level comprehensive', sd1.detailLevel === 'comprehensive');
    
    // Test 3: Small selection (2-5 parks)
    console.log('\n📊 Test: Small Selection (3 parks)');
    starredItems.parks = [
        { id: 'TZA_Serengeti', name: 'Serengeti' },
        { id: 'COD_Virunga', name: 'Virunga' },
        { id: 'CAF_Chinko', name: 'Chinko' }
    ];
    reportDataCache.parks.set('COD_Virunga', {
        fire: { total_fires: 29203 },
        settlement: { settlement_count: 5 },
        deforestation: { total_loss_km2: 2.34 },
        species: { total_count: 156 }
    });
    reportDataCache.parks.set('CAF_Chinko', {
        fire: { total_fires: 99752 },
        settlement: { settlement_count: 12 },
        deforestation: { total_loss_km2: 2.83 },
        species: { total_count: 89 }
    });
    
    const sd2 = determineSmartDefault();
    test('3 parks → Donor profile', sd2.profile === 'donor', sd2.reason);
    test('Biodiversity enabled (species > 50)', sd2.sections.biodiversity === true);
    test('Publications enabled (≤3 parks)', sd2.sections.publications === true);
    
    // Test 4: Medium selection (6-15 parks)
    console.log('\n📊 Test: Medium Selection (8 parks)');
    starredItems.parks = [];
    for (let i = 0; i < 8; i++) {
        const id = `PARK_${i}`;
        starredItems.parks.push({ id, name: `Park ${i}` });
        reportDataCache.parks.set(id, {
            fire: { total_fires: 15000 },
            settlement: { settlement_count: 25 },
            deforestation: { total_loss_km2: 5.0 },
            species: { total_count: 50 }
        });
    }
    
    const sd3 = determineSmartDefault();
    test('8 parks → Donor profile', sd3.profile === 'donor', sd3.reason);
    test('High activity detected', sd3.reason.includes('high-activity'));
    test('Summary detail level', sd3.detailLevel === 'summary');
    
    // Test 5: Large selection (30+ parks)
    console.log('\n📊 Test: Large Selection (36 parks)');
    starredItems.parks = [];
    for (let i = 0; i < 36; i++) {
        const id = `PARK_${i}`;
        starredItems.parks.push({ id, name: `Park ${i}` });
        reportDataCache.parks.set(id, {
            fire: { total_fires: 50000 },
            settlement: { settlement_count: 30 },
            deforestation: { total_loss_km2: 20.0 },
            species: { total_count: 100 }
        });
    }
    
    const sd4 = determineSmartDefault();
    test('36 parks → Quick profile', sd4.profile === 'quick', sd4.reason);
    test('Biodiversity disabled (too many parks)', sd4.sections.biodiversity === false);
    test('Climate disabled', sd4.sections.climate === false);
    test('Publications disabled', sd4.sections.publications === false);
    test('Core sections enabled', sd4.sections.fire && sd4.sections.settlement && sd4.sections.threat);
    
    // Test 6: Apply smart default
    console.log('\n📊 Test: Apply Smart Default');
    starredItems.parks = [{ id: 'TZA_Serengeti', name: 'Serengeti' }];
    reportDataCache.parks.clear();
    reportDataCache.parks.set('TZA_Serengeti', {
        fire: { total_fires: 86640 },
        settlement: { settlement_count: 210 },
        species: { total_count: 342 }
    });
    
    openReportBuilder();
    await new Promise(r => setTimeout(r, 500));
    
    test('Report builder opened', document.getElementById('report-builder-modal').classList.contains('active'));
    test('Smart default stored', !!window.currentSmartDefault);
    
    applySmartDefault();
    test('Config applied', reportConfig.preset === 'scientific');
    test('Detail level set', reportConfig.detailLevel === 'comprehensive');
    test('All sections enabled', Object.values(reportConfig.sections).filter(v => v).length === 9);
    
    closeReportBuilder();
    
    // Summary
    console.log('\n==========================================');
    const passed = results.filter(r => r.pass).length;
    const failed = results.filter(r => !r.pass).length;
    const total = results.length;
    const pct = Math.round((passed / total) * 100);
    
    console.log(`📊 SUMMARY: ${passed}/${total} tests passed (${pct}%)`);
    console.log(`✅ Passed: ${passed}`);
    console.log(`❌ Failed: ${failed}`);
    console.log('==========================================\n');
    
    if (failed > 0) {
        console.log('Failed tests:');
        results.filter(r => !r.pass).forEach(r => {
            console.log(`  ❌ ${r.name}`);
        });
    }
    
    return { passed, failed, total, pct, results };
}

// Run tests
console.log('📝 Report Builder tests loaded. Run with: runReportBuilderTests()');
