/**
 * Quick Popup Data Test
 * 
 * Run in browser console on a page with popup open, e.g.:
 * /?pwd=test2026&popup=CAF_Chinko&sections=fire,ghsl,deforest,road
 * 
 * Returns pass/fail summary with details about what data is present.
 */

async function testPopupData(parkId = null) {
    // Auto-detect park from URL or popup
    if (!parkId) {
        const params = new URLSearchParams(window.location.search);
        parkId = params.get('popup');
        if (!parkId) {
            const popup = document.querySelector('.pa-popup');
            parkId = popup?.querySelector('[data-park-id]')?.dataset.parkId;
        }
    }
    
    if (!parkId) {
        console.error('❌ No park ID found. Open a popup first or pass parkId.');
        return { pass: false, error: 'No park ID' };
    }
    
    console.log(`\n🧪 Testing popup data for: ${parkId}\n${'='.repeat(50)}`);
    
    const results = {
        parkId,
        sections: {},
        errors: [],
        warnings: []
    };
    
    // Test each section
    const sections = [
        { id: 'fire', name: 'Fire Activity', contentId: `fire-content-${parkId}` },
        { id: 'ghsl', name: 'Settlements', contentId: `ghsl-content-${parkId}` },
        { id: 'deforest', name: 'Deforestation', contentId: `deforest-content-${parkId}` },
        { id: 'road', name: 'Roads & Places', contentId: `road-content-${parkId}` }
    ];
    
    for (const section of sections) {
        const sectionEl = document.getElementById(`${section.id}-section-${parkId}`);
        const contentEl = document.getElementById(section.contentId);
        
        if (!sectionEl) {
            results.sections[section.id] = { exists: false, error: 'Section not found' };
            results.errors.push(`${section.name}: Section element missing`);
            console.log(`❌ ${section.name}: Section not found`);
            continue;
        }
        
        // Open section if closed
        if (!sectionEl.classList.contains('open')) {
            sectionEl.click();
            await new Promise(r => setTimeout(r, 2000));
        }
        
        const result = {
            exists: true,
            hasContent: contentEl?.innerHTML?.length > 100,
            contentLength: contentEl?.innerHTML?.length || 0,
            narrativeRows: contentEl?.querySelectorAll('.narrative-row')?.length || 0,
            hasError: contentEl?.textContent?.includes('not available') || 
                      contentEl?.textContent?.includes('Error'),
            countText: document.querySelector(`#${section.id}-count-${parkId}`)?.textContent || '',
            sampleNarrative: ''
        };
        
        // Get sample narrative text
        const firstRow = contentEl?.querySelector('.narrative-row');
        if (firstRow) {
            result.sampleNarrative = firstRow.textContent.trim().substring(0, 100);
        }
        
        // Check for detailed content
        if (result.hasError) {
            results.errors.push(`${section.name}: Shows error/not available`);
            console.log(`❌ ${section.name}: Data not available or error`);
        } else if (!result.hasContent) {
            results.warnings.push(`${section.name}: Empty or minimal content`);
            console.log(`⚠️ ${section.name}: No content loaded`);
        } else if (result.narrativeRows === 0) {
            results.warnings.push(`${section.name}: No clickable narrative rows`);
            console.log(`⚠️ ${section.name}: Loaded but no narrative rows (count: ${result.countText})`);
        } else {
            console.log(`✅ ${section.name}: ${result.narrativeRows} narratives, sample: "${result.sampleNarrative}..."`);
        }
        
        results.sections[section.id] = result;
    }
    
    // Test pinning
    console.log(`\n📌 Testing pinning...`);
    const fireIcon = document.querySelector(`[data-park-id="${parkId}"][data-type="fire"]`);
    if (fireIcon) {
        fireIcon.click();
        await new Promise(r => setTimeout(r, 2000));
        
        const pinned = fireIcon.classList.contains('pinned');
        const indicator = document.querySelector('.pinned-indicator');
        const indicatorActive = indicator?.classList.contains('active');
        
        results.pinning = { iconPinned: pinned, indicatorActive };
        
        if (pinned && indicatorActive) {
            console.log(`✅ Category pin: Icon pinned, indicator active`);
        } else {
            results.errors.push(`Pinning: icon=${pinned}, indicator=${indicatorActive}`);
            console.log(`❌ Category pin failed: icon=${pinned}, indicator=${indicatorActive}`);
        }
        
        // Unpin
        fireIcon.click();
        await new Promise(r => setTimeout(r, 500));
    }
    
    // Summary
    console.log(`\n${'='.repeat(50)}`);
    const passCount = Object.values(results.sections).filter(s => s.exists && s.hasContent && !s.hasError).length;
    const totalSections = sections.length;
    
    results.pass = results.errors.length === 0;
    results.summary = `${passCount}/${totalSections} sections OK, ${results.errors.length} errors, ${results.warnings.length} warnings`;
    
    if (results.pass) {
        console.log(`✅ PASS: ${results.summary}`);
    } else {
        console.log(`❌ FAIL: ${results.summary}`);
        results.errors.forEach(e => console.log(`   Error: ${e}`));
    }
    
    if (results.warnings.length > 0) {
        results.warnings.forEach(w => console.log(`   Warning: ${w}`));
    }
    
    return results;
}

// Quick test for multiple parks
async function testMultipleParks(parks = ['CAF_Chinko', 'TCD_Zakouma', 'COD_Virunga']) {
    const allResults = {};
    const pwd = new URLSearchParams(window.location.search).get('pwd') || 'test2026';
    
    for (const parkId of parks) {
        console.log(`\n\n${'#'.repeat(60)}\nTesting ${parkId}...\n`);
        
        // Navigate to park
        window.location.href = `/?pwd=${pwd}&popup=${parkId}&sections=fire,ghsl,deforest,road`;
        await new Promise(r => setTimeout(r, 5000));
        
        allResults[parkId] = await testPopupData(parkId);
    }
    
    console.log(`\n\n${'#'.repeat(60)}\nSUMMARY\n`);
    for (const [park, result] of Object.entries(allResults)) {
        console.log(`${result.pass ? '✅' : '❌'} ${park}: ${result.summary}`);
    }
    
    return allResults;
}

console.log('📋 Popup data test loaded. Run: testPopupData() or testMultipleParks()');
