# Universal Entry ID System for test=1 Mode

## Goal
Add numbered blue ID badges to ALL clickable entries in popups when test=1 is active.
Make TEST helpers generic to work with any section.

## Sections to Update

### 1. Fire Narratives (line ~9203)
- Container: `#fire-narratives-${paId}` or similar
- Entry class: `.narrative-row[data-type="fire"]`
- Add badge: `<span class="test-entry-id" data-section="fire">ID</span>`

### 2. Settlement Narratives (lines ~9372, 9389)
- Container: `#settlement-stories-${paId}` or similar  
- Entry class: `.narrative-row[data-type="settlement"]`
- Add badge: `<span class="test-entry-id" data-section="settlement">ID</span>`

### 3. Deforestation Narratives (lines ~9556, 9564) ✅ DONE
- Container: `#deforest-stories-${paId}`
- Entry class: `.narrative-row[data-type="deforestation"]`
- Badge already added, needs numbering fix

### 4. Legal Documents (line ~9939)
- Button: `onclick="loadMoreLegalDocs..."`
- Need to find container and add badges

## Numbering Strategy

Instead of inline `<script>` tags (which may not execute reliably), use a global function:

```javascript
function numberTestEntries(containerId, section) {
    if (!window.TEST) return;
    const container = document.getElementById(containerId);
    if (!container) return;
    
    const entries = container.querySelectorAll(`.narrative-row[data-type="${section}"]`);
    entries.forEach((entry, i) => {
        const badge = entry.querySelector('.test-entry-id');
        if (badge) badge.textContent = i;
    });
}

// Call after rendering:
setTimeout(() => numberTestEntries('deforest-stories-CAF_Chinko', 'deforestation'), 100);
```

## Generic TEST Helpers

Update TEST.DEFOREST to TEST.ENTRIES:

```javascript
TEST.ENTRIES = {
    scrollTo(section, index) { /* works for any section */ },
    find(section, searchText) { /* ... */ },
    inspect(section, index) { /* ... */ },
    findBroken(section) { /* ... */ },
    list(section, start, end) { /* ... */ },
    count(section) { /* ... */ },
    click(section, index) { /* ... */ }
};

// Keep shortcuts:
TEST.DEFOREST = {
    scrollTo: (i) => TEST.ENTRIES.scrollTo('deforestation', i),
    find: (t) => TEST.ENTRIES.find('deforestation', t),
    // ...
};
TEST.FIRE = { /* shortcuts for fire */ };
TEST.SETTLEMENT = { /* shortcuts for settlement */ };
```

## Implementation Steps

1. ✅ Add badges to deforestation (done, needs fix)
2. Add universal `numberTestEntries()` function
3. Add badges to fire narratives
4. Add badges to settlement narratives  
5. Add badges to legal docs (if applicable)
6. Update TEST helpers to be generic
7. Add section-specific shortcuts
8. Test all sections

## Badge Style
```html
<span class="test-entry-id" data-section="fire" style="display: inline-block; background: #3b82f6; color: white; font-size: 8px; font-weight: 600; padding: 1px 4px; border-radius: 3px; margin-right: 4px;">0</span>
```

Colors by section:
- Fire: #ef4444 (red)
- Deforestation: #10b981 (green)
- Settlement: #f59e0b (amber)
- Legal: #8b5cf6 (purple)
- Default: #3b82f6 (blue)
