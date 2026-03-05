# TEST Helpers Quick Reference

Add `?test=1` to URL to enable. Entry ID badges show as blue numbers.

## Quick Commands

```javascript
// EXPAND & RESIZE
TEST.expandAll('CAF_Chinko')             // Expand all accordions
TEST.setPopupHeight(2000)                // Make popup taller

// NAVIGATE TO ENTRY
TEST.scrollToEntry('deforestation', 50)  // By ID
TEST.scrollToText('deforestation', 'safari')  // By text

// INSPECT & DEBUG
TEST.inspectEntry('deforestation', 100)  // Full details
TEST.findBrokenEntries('deforestation')  // Find broken onclick

// MANIPULATE
TEST.clickEntry('fire', 10)              // Click to pin
TEST.highlightEntry('fire', 10, 'red')   // Highlight

// LOAD MORE
TEST.triggerLoadMore('deforestation', 'CAF_Chinko')  // Click button
TEST.validateLoadMore('CAF_Chinko')      // Test all sections

// SHORTCUTS
TEST.testDeforest('CAF_Chinko', 100)     // Scroll + inspect + click
TEST.getEntryCount('fire')               // Count entries
TEST.listEntries('deforestation')        // List all
```

## One-Liner Debugging

```javascript
// Debug load more in one line
const b=TEST.getEntryCount('deforestation');TEST.triggerLoadMore('deforestation','CAF_Chinko');setTimeout(()=>console.log(`${b} → ${TEST.getEntryCount('deforestation')}`),1000)

// Find and test entry by text
const id=TEST.scrollToText('deforestation','safari');if(id)TEST.testDeforest('CAF_Chinko',+id.split('-')[1])

// Scan all for issues
TEST.findBrokenEntries('fire');TEST.findBrokenEntries('deforestation')
```

## Common Workflows

### Test Load More
```javascript
TEST.expandAll('CAF_Chinko')
TEST.getEntryCount('deforestation')  // 22
TEST.triggerLoadMore('deforestation', 'CAF_Chinko')
// Wait 1 sec
TEST.getEntryCount('deforestation')  // 392
```

### Debug Specific Entry
```javascript
TEST.scrollToEntry('deforestation', 100)
TEST.inspectEntry('deforestation', 100)
TEST.highlightEntry('deforestation', 100, 'yellow')
TEST.clickEntry('deforestation', 100)
```

### Batch Highlight
```javascript
for(let i=0;i<10;i++) TEST.highlightEntry('fire',i,['red','yellow','green'][i%3])
```
