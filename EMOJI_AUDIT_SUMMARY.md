# Emoji Replacement Audit - Summary

## Complete Emoji Inventory

Audited all files and found **120+ emoji instances** using **32 unique characters**.

## Breakdown by Category

### 1. Fire & Heat (🔥⚡❄️🌊) - **30 instances**
- Fire groups, active fires, fire alerts
- Status indicators: rapid spread, cooling, flood-influenced
- **Priority**: HIGH (core feature)

### 2. Success/Warnings (✓✗⚠️) - **34 instances**  
- Success confirmations, errors, warnings
- Already using some ASCII (✓)
- **Priority**: MEDIUM (functional)

### 3. Movement Types (🚶🚗✈️📍) - **4 instances**
- Patrol movement classification
- Upload panel icons
- **Priority**: HIGH (user-facing)

### 4. Nature/Environment (🌳🌲🏘️🦁☀️🌧️) - **28 instances**
- Deforestation, settlements, biodiversity
- Climate indicators (dry/rainy seasons)
- **Priority**: MEDIUM (informational)

### 5. Data/Statistics (📊📈📉📝📅) - **12 instances**
- Summary stats, trends, reports
- **Priority**: LOW (decorative)

### 6. Infrastructure (🗺️💾🛤️🏗️🖨️) - **12 instances**
- Map controls, storage, roads, print
- **Priority**: LOW (secondary features)

## Style Guide

### App Design System
```
Background:  #0a0a0a, #1a1a1a, #1e1e1e (dark)
Text:        #e0e0e0 (primary), #888 (secondary)
Success:     #22c55e (green)
Info:        #3b82f6 (blue)  
Warning:     #f59e0b (orange)
Danger:      #ef4444 (red)
Cool:        #60a5fa (light blue)
```

### Icon Principles
1. **Monocolor** - use CSS color, not multi-color emoji
2. **Geometric** - simple shapes (●▲■◆)
3. **Semantic** - meaning clear from context + color
4. **Accessible** - screen reader friendly
5. **Consistent** - same shape = same meaning

## Proposed Replacements

### High Priority (Core Features)

| Emoji | Usage | Replacement | Color | Rationale |
|-------|-------|-------------|-------|-----------|
| 🔥 | Active fires | `●` | #ef4444 | Already used in "Currently Active" |
| ⚡ | Rapid spread | `▲` | #f59e0b | Triangle = alert, orange = caution |
| ❄️ | Cooling | `○` | #60a5fa | Hollow = inactive, blue = cool |
| 🌊 | Flood fire | `≋` | #3b82f6 | Wavy = water, blue = wet |
| ⚠️ | Warning | `▲` | #f59e0b | Standard warning shape |
| 📍 | Point/pin | `●` | #888 | Standard bullet point |
| 🚶 | Foot patrol | `▬` | #888 | Single bar = slow |
| 🚗 | Vehicle | `▬▬` | #888 | Double bar = medium speed |
| ✈️ | Aircraft | `△` | #888 | Triangle = flight |

### Medium Priority (Informational)

| Emoji | Usage | Replacement | Color | Rationale |
|-------|-------|-------------|-------|-----------|
| 🌳 | Forest/trees | `▲` | #22c55e | Triangle = tree shape |
| 🏘️ | Settlements | `■` | #888 | Square = building |
| 🦁 | Biodiversity | `◆` | #f59e0b | Diamond = precious |
| ☀️ | Dry season | `○` | #f59e0b | Circle = sun |
| 🌧️ | Rainy season | `≋` | #3b82f6 | Wavy = rain |
| 🏞️ | Park/nature | `▭` | #22c55e | Rectangle = landscape |

### Low Priority (Decorative)

| Emoji | Usage | Replacement | Color | Rationale |
|-------|-------|-------------|-------|-----------|
| 📊 | Statistics | `≡` | #3b82f6 | Three lines = data |
| 📈 | Increasing | `╱` | #ef4444 | Rising line |
| 📉 | Decreasing | `╲` | #22c55e | Falling line |
| 🗺️ | Map | `⊞` | #3b82f6 | Square+ = zoom/expand |
| 💾 | Storage | `▫` | #888 | Square = disk |
| ✓ ✗ | Keep as-is | - | - | Standard ASCII chars |

## Implementation Plan

### Phase 1: Create Icon System (30 min)
1. Add CSS classes for all icon types
2. Create utility functions for icon rendering
3. Document in style guide

### Phase 2: Backend Go Files (1 hour)
- `srv/fire_realtime_handlers.go` - Fire status emojis
- `srv/park_stats_handlers.go` - Insight emojis  
- `srv/narrative_handlers.go` - Trend indicators

### Phase 3: Frontend Templates (2 hours)
- `srv/templates/globe.html` - Main UI (largest file)
- `srv/templates/upload.html` - Movement type icons
- `srv/templates/admin.html` - Admin panel
- `srv/templates/park_analysis.html` - Analysis page

### Phase 4: Testing (30 min)
- Visual regression check all pages
- Screen reader testing
- Cross-browser compatibility

## Example Before/After

### Before (Emoji)
```javascript
🔥 Fire group Alpha-4 (Approaching)
⚠️ Fire pressure is INCREASING
✓ No settlements detected
```

### After (Monocolor)
```javascript
<span class="icon-fire">●</span> Fire group Alpha-4 (Approaching)
<span class="icon-warning">▲</span> Fire pressure is INCREASING  
<span class="icon-success">✓</span> No settlements detected
```

### CSS
```css
.icon-fire { color: #ef4444; font-weight: 500; }
.icon-warning { color: #f59e0b; font-weight: 500; }
.icon-success { color: #22c55e; }
```

## Benefits

1. **Visual Consistency** - Matches dark theme perfectly
2. **Performance** - ASCII loads instantly vs emoji fonts
3. **Accessibility** - Screen readers handle ASCII better
4. **Flexibility** - Easy to change colors/sizes via CSS
5. **Cross-platform** - No rendering differences
6. **Professional** - Clean, focused aesthetic

## Risks & Mitigation

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Loss of visual distinction | Medium | Use color + shape combinations |
| User confusion (change) | Low | Icons intuitive in context |
| Extra CSS complexity | Low | Reusable classes |
| Testing overhead | Medium | Systematic phase-by-phase |

## Next Step

Shall I proceed with Phase 1 (Create Icon System)?
