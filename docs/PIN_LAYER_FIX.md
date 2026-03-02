# Pin Layer Comprehensive Fix

## Changes Made

### 1. Auto-Pin Layers from URL `sections` Parameter

**Problem:** When sharing a URL with `sections=fire`, the accordion would open but the layer wouldn't automatically pin to the map. Users had to manually click the pin icon.

**Solution:** Added auto-pin logic after opening accordion sections.

**Code Location:** `srv/templates/globe.html` line ~10640

**Implementation:**
```javascript
// Auto-pin layers from sections parameter
setTimeout(async () => {
    const park = (window.keystoneAreas || []).find(p => p.id === popupPaId);
    const parkName = park ? park.name : popupPaId;
    
    for (const secType of sectionsToOpen) {
        // Map section types to layer types
        const layerTypeMap = {
            'fire': 'fire_trajectory',
            'settlement': 'settlement',
            'deforestation': 'deforestation',
            'roads': 'road',
            'places': 'place',
            'infrastructure': 'infrastructure'
        };
        
        const layerType = layerTypeMap[secType] || secType;
        const pinKey = getPinKey(popupPaId, layerType);
        
        // Only pin if not already pinned
        if (!pinnedLayers[pinKey]) {
            await addPinnedLayer(popupPaId, parkName, layerType);
            
            // Update the icon state
            const icon = document.querySelector(`#${secType}-section-${popupPaId} .pa-popup-section-icon`);
            if (icon) icon.classList.add('pinned');
        }
    }
    
    updatePinnedIndicator();
    savePinnedToURL();
}, 1000);
```

**Testing:**
```
URL: http://localhost:8000/?pwd=test2026&popup=CAF_Chinko&sections=fire&from=2023-01-01&to=2026-03-01

Expected:
- Popup opens for CAF_Chinko ✓
- Fire section accordion opens ✓
- Fire trajectories automatically pin to map ✓
- Pinned indicator shows "CAF_CHINKO 🔥 1027" ✓
- Fire icon shows green pinned state ✓
```

---

### 2. Fix Date Range Filtering for Auto-Pinned Fire Layers

**Problem:** When auto-pinning fire_trajectory layers, the date range from the time slider wasn't being applied because the check was for `type === 'fire'` but the actual type being passed was `fire_trajectory`.

**Solution:** Added `fire_trajectory` to the date filtering condition.

**Code Location:** `srv/templates/globe.html` in `addPinnedLayer()` function

**Before:**
```javascript
if (type === 'fire' || type === 'deforestation') {
    if (window.dateFrom && window.dateTo) {
        dateParams = `&start=${window.dateFrom}&end=${window.dateTo}`;
    }
}
```

**After:**
```javascript
if (type === 'fire' || type === 'fire_trajectory' || type === 'deforestation') {
    if (window.dateFrom && window.dateTo) {
        dateParams = `&start=${window.dateFrom}&end=${window.dateTo}`;
    }
}
```

**Impact:**
- Before: 2428 features loaded (all years)
- After: 1027 features loaded (2023-01-01 to 2026-03-01 only)

---

### 3. Fix Pinned Layers in Share URL (copyStarredLink)

**Problem:** The `copyStarredLink()` function was encoding pinned layers incorrectly:
- Used: `pinnedKeys.join(',')` → produced `CAF_Chinko_fire_trajectory`
- Needed: `parkId:type` format → `CAF_Chinko:fire_trajectory`

**Solution:** Map pinnedLayers keys to proper `parkId:type` format.

**Code Location:** `srv/templates/globe.html` in `copyStarredLink()` function (line ~7945)

**Before:**
```javascript
const pinnedKeys = Object.keys(pinnedLayers);
if (pinnedKeys.length > 0) {
    params.set('pinned', pinnedKeys.join(','));
}
```

**After:**
```javascript
const pinnedKeys = Object.keys(pinnedLayers);
if (pinnedKeys.length > 0) {
    const pinned = pinnedKeys.map(key => {
        const layer = pinnedLayers[key];
        return `${layer.parkId}:${layer.type}`;
    }).join(',');
    params.set('pinned', pinned);
}
```

**Note:** The main `shareCurrentView()` function already had the correct implementation. This fix makes `copyStarredLink()` consistent.

---

## Testing Checklist

### Test 1: Auto-Pin from Sections Parameter

```bash
# URL with sections=fire should auto-pin fire layer
URL: http://localhost:8000/?pwd=test2026&popup=CAF_Chinko&sections=fire&from=2023-01-01&to=2026-03-01

✓ Fire section opens automatically
✓ Fire trajectories load and pin to map
✓ Correct feature count (1027 for date range)
✓ Pinned indicator shows "CAF_CHINKO 🔥 1027"
✓ Toast: "Pinned 1027 fire_trajectory features from CAF_Chinko"
```

### Test 2: Multiple Sections Auto-Pin

```bash
# URL with multiple sections
URL: http://localhost:8000/?pwd=test2026&popup=CAF_Chinko&sections=fire,settlement,deforestation

✓ All 3 sections open
✓ All 3 layer types pin to map
✓ Pinned indicator shows all 3
```

### Test 3: Share URL Includes Pinned Layers

```bash
# After pinning layers, click share button
1. Open CAF_Chinko popup
2. Click fire section icon to pin
3. Click settlement section icon to pin
4. Click share button (⛓ icon in footer)
5. Check URL contains: pinned=CAF_Chinko:fire_trajectory,CAF_Chinko:settlement
```

### Test 4: Starred Items Share Link

```bash
# From starred items modal
1. Star CAF_Chinko and COD_Virunga
2. Pin fire layers for both
3. Open star modal
4. Click share icon
5. Check URL contains both starred_parks and pinned params
```

### Test 5: Date Range Filtering

```bash
# Verify date filtering works for auto-pinned layers
URL: http://localhost:8000/?pwd=test2026&popup=CAF_Chinko&sections=fire&from=2025-01-01&to=2025-12-31

Expected: Only fires from 2025 (should be ~455 features based on DB stats)
```

---

## URL Parameter Reference

### Pinned Layers Format

```
pinned=PARK_ID:TYPE,PARK_ID:TYPE,...

Examples:
- pinned=CAF_Chinko:fire_trajectory
- pinned=CAF_Chinko:fire_trajectory,CAF_Chinko:settlement
- pinned=CAF_Chinko:fire_trajectory,COD_Virunga:deforestation
```

### Sections Format

```
sections=TYPE,TYPE,...

Valid types:
- fire (maps to fire_trajectory layer)
- settlement
- deforestation
- roads (maps to road layer)
- places (maps to place layer)
- infrastructure

Examples:
- sections=fire
- sections=fire,settlement
- sections=fire,deforestation,settlement
```

### Complete Share URL Example

```
http://localhost:8000/
  ?pwd=test2026
  &lat=5.8521
  &lng=23.0073
  &z=5.9
  &from=2023-01-01
  &to=2026-03-01
  &popup=CAF_Chinko
  &sections=fire
  &pinned=CAF_Chinko:fire_trajectory
```

---

## Database Stats (for validation)

### CAF_Chinko Fire Trajectories by Year

```sql
SELECT 
    json_extract(properties_json, '$.year') as year,
    COUNT(*) as count
FROM feature_geometries 
WHERE park_id = 'CAF_Chinko' 
  AND feature_type = 'fire_trajectory'
GROUP BY year
ORDER BY year;

Results:
2020: 485 trajectories
2021: 420 trajectories
2022: 496 trajectories
2023: 357 trajectories
2024: 455 trajectories
2025: 215 trajectories

Total: 2,428 trajectories (all years)
2023-2026: ~1,027 trajectories (verified)
```

---

## Related Files

- `srv/templates/globe.html` - Main UI with all pinning logic
- `srv/api.go` - `/api/parks/{id}/features` endpoint
- `docs/API.md` - API documentation
- `docs/SHELLEY_PROMPT_UI.md` - UI development guide

---

## Future Improvements

1. **Persist pin state across page reloads** - Store in localStorage
2. **Pin limit** - Warn when pinning too many layers (performance)
3. **Pin presets** - Save/load pin configurations
4. **Pin from search results** - Pin all results matching a query
5. **Pin animation** - Visual feedback when auto-pinning from URL
