# TEST Mode Helpers

When `?test=1` is added to the URL, the app enables advanced testing helpers accessible via `window.TEST`.

## Entry ID Badges

Blue numbered badges (0, 1, 2...) appear next to entries in:
- Fire narratives
- Deforestation events  
- Settlement entries (future)
- Legal documents (future)

Entry IDs use format: `section-N` (e.g., `fire-0`, `deforestation-5`)

## TEST Helper Functions

### Popup Accordion Management

```javascript
TEST.expandAll('CAF_Chinko')   // Expand all accordion sections
TEST.collapseAll('CAF_Chinko') // Collapse all sections
```

### Entry Manipulation

```javascript
// Scroll to specific entry
TEST.scrollToEntry('deforestation', 5)

// Click entry programmatically
TEST.clickEntry('fire', 10)

// Highlight entry with color
TEST.highlightEntry('deforestation', 15, 'yellow')

// Count entries in section
TEST.getEntryCount('fire')  // Returns number

// List all entries with IDs and text
TEST.listEntries('deforestation')  // Prints table to console
```

### State Queries

```javascript
TEST.isPanelOpen('admin')           // Check if panel is open
TEST.isPopupOpen('CAF_Chinko')      // Check if popup is open
TEST.isAccordionOpen('CAF_Chinko', 'fire')  // Check accordion state
TEST.isPinned('CAF_Chinko', 'fire_trajectory')  // Check if layer pinned
TEST.getMapCenter()                 // Get current map position
TEST.getAdminTab()                  // Get active admin tab
```

### Star Report Helpers

```javascript
TEST.STAR.getStats()               // Get starred items stats
TEST.STAR.getReportData('CAF_Chinko')  // Get park data
TEST.STAR.exportXLSX()             // Export to Excel
```

### Fire Notification Helpers

```javascript
TEST.findFireNotification('Alpha-5')  // Find fire by name
TEST.clickFire('Hotel')               // Click fire entry
TEST.testFireClick('Alpha-5', 'Aouk') // Test click and verify
```

### Notification Helpers

```javascript
TEST.getSingleNotifications()      // Get notification list
TEST.getNotificationStats()        // Get notification counts
```

## Browser Setup for Testing

**Recommended viewport:** 1280x1400 or taller

This ensures all popup accordion sections are visible without scrolling, making screenshots more useful.

## Example Test Workflow

```javascript
// 1. Open park popup with deforestation expanded
// URL: ?test=1&popup=CAF_Chinko&sections=deforestation

// 2. In console:
TEST.expandAll('CAF_Chinko')           // Expand all sections
TEST.getEntryCount('deforestation')    // Count entries: 22
TEST.listEntries('deforestation')      // See all entries

// 3. Test specific entry:
TEST.scrollToEntry('deforestation', 15)
TEST.highlightEntry('deforestation', 15, 'yellow')
TEST.clickEntry('deforestation', 15)   // Pin to map

// 4. Verify state:
TEST.isPinned('CAF_Chinko', 'deforestation')
TEST.getPinnedCount()                  // Should be > 0
```

## Benefits

- **Reduced token usage:** Target specific entries by ID instead of describing location
- **Reproducible tests:** Share URLs with exact state (popup, sections, entries)
- **Faster debugging:** Programmatically manipulate UI without manual clicking
- **Screenshot clarity:** Entry IDs visible in screenshots for reference

## Implementation Details

Entry IDs are added at render time:
- Fire: Added in `renderNarrative()` function with `idx` parameter
- Deforestation: Added in `formatClassifiedDefo()` and numbered via script tag
- Future: Settlement, legal documents need implementation

Badge styling:
```css
.test-entry-id {
  display: inline-block;
  background: #3b82f6;  /* Blue */
  color: white;
  font-size: 8px;
  font-weight: 600;
  padding: 1px 4px;
  border-radius: 3px;
  margin-right: 4px;
}
```

Data attribute for targeting:
```html
<span class="test-entry-id" data-entry="fire-5">5</span>
```
