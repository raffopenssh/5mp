# Enhanced TEST Helper Functions - Design

## Problems from Last Debug Session (300k tokens!)
1. Manual scrolling through 392 entries to find broken one
2. Couldn't see all entries at once (browser height limit)
3. No direct navigation to entry N
4. Had to manually click "show more" and inspect
5. Couldn't quickly scan for broken onclick handlers

## Proposed TEST Helper Functions

### 1. Entry Navigation & Inspection
```javascript
TEST.DEFOREST = {
    // Jump to specific entry
    scrollTo: (index) => { /* scroll popup to show entry N */ },
    
    // Find entry by text
    find: (searchText) => { /* find and show matching entries */ },
    
    // Get full details of entry
    inspect: (index) => { 
        /* return {index, text, onclick, hasOnclick, innerHTML, outerHTML} */
    },
    
    // Scan for broken entries
    findBroken: () => {
        /* return array of entries with broken/missing onclick */
    },
    
    // Show range
    list: (start, end) => {
        /* console.table entries with key info */
    },
    
    // Count entries
    count: () => { /* total entries in container */ },
    
    // Click entry
    click: (index) => { /* click entry N */ }
};
```

### 2. Popup & Accordion Control
```javascript
TEST.POPUP = {
    // Expand all sections at once
    expandAll: (parkId) => { /* click all accordion headers */ },
    
    // Set popup height to show everything
    setHeight: (pixels) => { /* resize .pa-popup */ },
    
    // Scroll popup to position
    scrollTo: (pixels) => { /* set scrollTop */ },
    
    // Get popup scroll info
    getScrollInfo: () => { /* {scrollTop, scrollHeight, clientHeight} */ }
};
```

### 3. Load More Testing
```javascript
TEST.LOADMORE = {
    // Trigger load more for section
    trigger: (section) => { /* click show more button */ },
    
    // Check if button exists
    hasButton: (section) => { /* return boolean */ },
    
    // Get button text (shows count)
    getButtonText: (section) => { /* e.g., "Show 221 more events" */ },
    
    // Count before/after
    testLoadMore: (section) => {
        /* return {before: N, after: M, loaded: M-N} */
    }
};
```

### 4. Quick Test Scenarios
```javascript
TEST.SCENARIO = {
    // Full deforestation test
    testDeforest: (parkId) => {
        /* 1. Open popup, 2. Expand section, 3. Check all entries,
           4. Click load more, 5. Verify all clickable */
    },
    
    // Test specific entry
    testEntry: (section, index) => {
        /* Click entry, verify it pins, return success/fail */
    },
    
    // Scan all entries for issues
    scanEntries: (section) => {
        /* Check onclick, innerHTML, formatting for all entries */
    }
};
```

### 5. Browser Automation
```javascript
TEST.BROWSER = {
    // Set browser to optimal size for testing
    setOptimalSize: () => { /* 1280x3000 or taller */ },
    
    // Make tooltip visible longer
    pauseTooltips: () => { /* prevent tooltip auto-hide */ },
    
    // Show all entries in viewport
    showAll: (section) => {
        /* Adjust heights to show everything without scrolling */
    }
};
```

## Usage Examples

### Example 1: Quick deforestation entry test
```javascript
// Open page with ?test=1
TEST.POPUP.expandAll('CAF_Chinko');
TEST.DEFOREST.findBroken();  // Returns: [{index: 23, reason: "no onclick"}, ...]
TEST.DEFOREST.scrollTo(23);  // Jump to broken entry
TEST.DEFOREST.inspect(23);   // Show full details
```

### Example 2: Test load more functionality
```javascript
TEST.LOADMORE.testLoadMore('deforestation');
// Returns: {before: 22, after: 392, loaded: 370, allClickable: true}
```

### Example 3: Find entry by text
```javascript
TEST.DEFOREST.find('31.7km from Safari Ht Chinko');
// Returns: [{index: 18, text: "...", onclick: "..."}]
TEST.DEFOREST.scrollTo(18);
TEST.DEFOREST.click(18);  // Pin it
```

### Example 4: Comprehensive test
```javascript
TEST.SCENARIO.testDeforest('CAF_Chinko');
// Runs full test suite, returns report:
// {
//   entriesInitial: 22,
//   entriesAfterLoadMore: 392,
//   brokenEntries: [],
//   clickableEntries: 392,
//   pinTest: "PASS"
// }
```

## Implementation Priority

### High Priority (most useful)
1. `TEST.DEFOREST.findBroken()` - scan for broken onclick
2. `TEST.DEFOREST.scrollTo(index)` - jump to entry
3. `TEST.DEFOREST.inspect(index)` - detailed inspection
4. `TEST.POPUP.expandAll()` - expand all sections
5. `TEST.LOADMORE.trigger()` - click load more

### Medium Priority
6. `TEST.DEFOREST.list(start, end)` - show range
7. `TEST.DEFOREST.find(text)` - search entries
8. `TEST.SCENARIO.testDeforest()` - full test
9. `TEST.POPUP.setHeight()` - resize popup

### Low Priority (nice to have)
10. `TEST.BROWSER.setOptimalSize()` - browser sizing
11. `TEST.BROWSER.pauseTooltips()` - tooltip control

## Estimated Token Savings

With these helpers, the debugging session would have been:
```javascript
// 1. Open popup and expand section (2 commands)
TEST.POPUP.expandAll('CAF_Chinko');

// 2. Find broken entries (1 command)
TEST.DEFOREST.findBroken();  // Returns: []

// 3. Test load more (1 command)
TEST.LOADMORE.testLoadMore('deforestation');
// Returns: {before: 22, after: 392, loaded: 370, allClickable: true}
```

**Estimated: 10k tokens instead of 300k = 97% savings!**
