# Icon Font Proposal for Emoji Replacement

## Recommended: Lucide Icons

**Why Lucide?**
- ✓ **Minimal & Clean** - Perfect for dark themes
- ✓ **Lightweight** - Only 2KB for icons you use
- ✓ **Modern** - Active development (fork of Feather)
- ✓ **Comprehensive** - 1000+ icons covering all our needs
- ✓ **Consistent** - Same stroke width, rounded corners
- ✓ **MIT License** - Free for commercial use

**CDN**: `https://unpkg.com/lucide-static@latest/font/lucide.css`

## Icon Mapping - Lucide Icons

### High Priority (Core Features)

| Current Emoji | Usage | Lucide Icon | Class | Preview |
|---------------|-------|-------------|-------|--------|
| 🔥 | Active fires | `flame` | `.lucide-flame` | Actual flame icon |
| ⚡ | Rapid spread | `zap` | `.lucide-zap` | Lightning bolt |
| ❄️ | Cooling fires | `snowflake` | `.lucide-snowflake` | Snowflake icon |
| 🌊 | Flood-influenced | `waves` | `.lucide-waves` | Water waves |
| ⚠️ | Warnings | `alert-triangle` | `.lucide-alert-triangle` | Triangle with ! |
| 📍 | Point/location | `map-pin` | `.lucide-map-pin` | Location pin |
| 🚶 | Foot patrol | `footprints` | `.lucide-footprints` | Footsteps icon |
| 🚗 | Vehicle | `car` | `.lucide-car` | Car icon |
| ✈️ | Aircraft | `plane` | `.lucide-plane` | Airplane icon |

### Medium Priority (Informational)

| Current Emoji | Usage | Lucide Icon | Class |
|---------------|-------|-------------|-------|
| 🌳 | Forest/deforestation | `tree-pine` | `.lucide-tree-pine` |
| 🏘️ | Settlements | `home` | `.lucide-home` |
| 🦁 | Biodiversity/species | `bug` | `.lucide-bug` |
| ☀️ | Dry season | `sun` | `.lucide-sun` |
| 🌧️ | Rainy season | `cloud-rain` | `.lucide-cloud-rain` |
| 🏞️ | Park/nature | `mountain` | `.lucide-mountain` |

### Low Priority (UI Elements)

| Current Emoji | Usage | Lucide Icon | Class |
|---------------|-------|-------------|-------|
| 📊 | Statistics | `bar-chart-3` | `.lucide-bar-chart-3` |
| 📈 | Increasing trend | `trending-up` | `.lucide-trending-up` |
| 📉 | Decreasing trend | `trending-down` | `.lucide-trending-down` |
| 🗺️ | Map view | `map` | `.lucide-map` |
| 💾 | Storage/disk | `hard-drive` | `.lucide-hard-drive` |
| 🛤️ | Roads | `route` | `.lucide-route` |
| 🏗️ | Construction/GHSL | `construction` | `.lucide-construction` |
| 🖨️ | Print | `printer` | `.lucide-printer` |
| ✓ | Success | `check` | `.lucide-check` |
| ✗ | Error | `x` | `.lucide-x` |
| ⬇️ | Download | `download` | `.lucide-download` |
| ⬆️ | Upload | `upload` | `.lucide-upload` |
| ▶️ | Play/run | `play` | `.lucide-play` |
| ⭐ | Star | `star` | `.lucide-star` |

## Implementation

### Step 1: Add Lucide to HTML

```html
<!-- In <head> of globe.html -->
<link href="https://unpkg.com/lucide-static@latest/font/lucide.css" rel="stylesheet">
```

### Step 2: Add Icon Utility CSS

```css
/* Icon base styles */
.icon {
    display: inline-block;
    width: 1em;
    height: 1em;
    vertical-align: -0.125em;
    font-style: normal;
}

/* Color variants */
.icon-fire { color: #ef4444; }
.icon-warning { color: #f59e0b; }
.icon-success { color: #22c55e; }
.icon-info { color: #3b82f6; }
.icon-cool { color: #60a5fa; }
.icon-neutral { color: #888; }

/* Size variants */
.icon-sm { font-size: 0.875em; }
.icon-lg { font-size: 1.25em; }
.icon-xl { font-size: 1.5em; }
```

### Step 3: Helper Function

```javascript
// Add to globe.html
function icon(name, color = '', size = '') {
    const classes = ['icon', `lucide-${name}`];
    if (color) classes.push(`icon-${color}`);
    if (size) classes.push(`icon-${size}`);
    return `<i class="${classes.join(' ')}"></i>`;
}

// Usage examples:
icon('flame', 'fire')           // 🔥 → Red flame
icon('plane', 'neutral', 'sm')  // ✈️ → Gray airplane (small)
icon('alert-triangle', 'warning') // ⚠️ → Orange warning
```

## Example Replacements

### Before (Emoji)
```javascript
🔥 Fire group Alpha-4 (Approaching)
⚠️ Fire pressure is INCREASING
✓ No settlements detected
🚶 Foot patrol • 🚗 Vehicle • ✈️ Aircraft
```

### After (Lucide)
```javascript
<i class="icon lucide-flame icon-fire"></i> Fire group Alpha-4 (Approaching)
<i class="icon lucide-alert-triangle icon-warning"></i> Fire pressure is INCREASING
<i class="icon lucide-check icon-success"></i> No settlements detected
<i class="icon lucide-footprints icon-neutral"></i> Foot patrol • 
<i class="icon lucide-car icon-neutral"></i> Vehicle • 
<i class="icon lucide-plane icon-neutral"></i> Aircraft
```

### With Helper Function
```javascript
${icon('flame', 'fire')} Fire group Alpha-4 (Approaching)
${icon('alert-triangle', 'warning')} Fire pressure is INCREASING
${icon('check', 'success')} No settlements detected
${icon('footprints', 'neutral')} Foot • ${icon('car', 'neutral')} Vehicle • ${icon('plane', 'neutral')} Aircraft
```

## Visual Preview

Lucide icons in your dark theme:

```
🔥 → 🔥 (flame, red)
⚡ → ⚡ (zap, orange)
❄️ → ❄ (snowflake, light blue)
🌊 → 〰 (waves, blue)
⚠️ → ⚠ (alert-triangle, orange)
🚶 → 👣 (footprints, gray)
🚗 → 🚗 (car, gray)
✈️ → ✈ (plane, gray)
🌳 → 🌲 (tree-pine, green)
🏘️ → 🏠 (home, gray)
📊 → 📊 (bar-chart-3, blue)
```

## Alternative Options

### Font Awesome 6 (Free)
- **Pros**: Most popular, 2000+ free icons
- **Cons**: Heavier (70KB), less minimal aesthetic
- **CDN**: `https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css`

### Feather Icons
- **Pros**: Ultra-minimal, only 24px x 24px
- **Cons**: Only 287 icons, less maintained
- **CDN**: `https://unpkg.com/feather-icons@latest/dist/feather.css`

### Material Symbols (Google)
- **Pros**: Comprehensive, variable font
- **Cons**: Heavier, more "Material Design" style
- **CDN**: `https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined`

## Recommendation

**Go with Lucide** for:
1. Perfect balance of coverage vs weight
2. Clean, minimal style fits your dark theme
3. Active development and support
4. Icons that actually look like what they represent (airplane looks like airplane, not triangle)

## Size Comparison

| Library | Size | Icons | Style |
|---------|------|-------|-------|
| **Lucide** | **2KB** (gzip) | 1000+ | Minimal, clean |
| Font Awesome 6 Free | 70KB | 2000+ | Detailed |
| Feather | 14KB | 287 | Ultra-minimal |
| Material Symbols | 50KB+ | 2500+ | Material Design |

## Next Steps

1. ✓ **Confirm**: Use Lucide Icons?
2. Add CDN link to globe.html
3. Add CSS utility classes
4. Add JavaScript helper function
5. Replace emojis systematically (backend → frontend)
6. Test visual appearance
7. Commit changes

Shall I proceed with Lucide implementation?
