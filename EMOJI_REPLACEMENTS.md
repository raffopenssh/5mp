# Emoji to Monocolor Icon Replacements

## UI Style
- **Dark theme**: Background #0a0a0a, #1a1a1a, #1e1e1e
- **Text colors**: #e0e0e0 (primary), #888 (secondary), #666 (tertiary)
- **Accent colors**: #22c55e (green/success), #3b82f6 (blue/info), #ef4444 (red/danger), #f59e0b (orange/warning)
- **Font**: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif

## Current Emoji Usage

### Fire/Heat (12 instances)
| Current | Usage | Suggested Replacement |
|---------|-------|----------------------|
| 🔥 | Fire groups, active fires, alerts | `●` (bullet) with #ef4444 color |
| ⚡ | Rapid fire (3x spread) | `▲` (triangle) with #f59e0b color |
| ❄️ | Cooling fires (no new in 2 days) | `○` (circle) with #60a5fa color |
| 🌊 | Flood-influenced fires (wet season) | `≋` (wavy line) with #3b82f6 color |

**Files**: `srv/fire_realtime_handlers.go` (lines 39, 44, 50, 64, 75, 80, 85, 720, 910, 913, 915, 918), `srv/park_stats_handlers.go` (lines 225, 503, 506), `srv/templates/globe.html` (lines 1804, 5909, 5933, 9462, 12346), `srv/templates/park_analysis.html` (lines 427, 478)

### Success/Checkmarks (20 instances)
| Current | Usage | Suggested Replacement |
|---------|-------|----------------------|
| ✓ | Success messages, approvals, completed tasks | `✓` (keep, standard checkmark) |
| ✗ | Errors, failures | `✕` (keep, standard x-mark) |

**Files**: `srv/templates/globe.html` (lines 566, 2214, 2219, 2294, 2675, 2724, 2726, 3493, 5357, 5661, 5774, 6944, 6958, 6975, 6992, 7013, 7840, 7969, 8037, 9559, 9714, 10455, 11878, 11930, 11978, 12172), `srv/templates/welcome.html` (lines 22, 26, 30, 34), `srv/templates/admin.html` (line 258), `srv/fire_realtime_handlers.go` (line 918), `srv/park_stats_handlers.go` (lines 212, 325, 425), `srv/narrative_handlers.go` (line 2256)

### Warnings/Alerts (14 instances)
| Current | Usage | Suggested Replacement |
|---------|-------|----------------------|
| ⚠️ | Warnings, increasing trends, critical issues | `▲` (triangle) with #f59e0b color OR `!` in triangle border |
| ⚠ | Same as above (no variation selector) | Same as above |

**Files**: `srv/fire_realtime_handlers.go` (lines 50, 720), `srv/park_stats_handlers.go` (lines 196, 231, 349, 423, 506), `srv/narrative_handlers.go` (lines 1953, 2254), `srv/templates/globe.html` (lines 4198, 5758, 9570, 9730, 9938, 12175), `srv/templates/park_analysis.html` (line 498)

### Movement/Transport (4 instances)
| Current | Usage | Suggested Replacement |
|---------|-------|----------------------|
| 🚶 | Foot patrol | `▬` (horizontal bar) with thickness |
| 🚗 | Vehicle patrol | `▬▬` (double bar) with thickness |
| ✈️ | Aircraft patrol | `△` (outline triangle) |
| 📍 | Default/point | `●` (bullet) |

**Files**: `srv/templates/upload.html` (lines 309-312), `srv/templates/park_analysis.html` (line 490)

### Nature/Environment (14 instances)
| Current | Usage | Suggested Replacement |
|---------|-------|----------------------|
| 🌳 | Deforestation, forest loss | `▲` (tree shape) with #22c55e color |
| 🌲 | Roadless wilderness | Same as above |
| 🏘️ | Settlements | `▪` (square) with #888 color |
| 🏠 | Village | Same as above |
| 🦁 | Biodiversity/species | `◆` (diamond) with #f59e0b color |
| ☀️ | Dry season, climate | `○` (circle) with #f59e0b color |
| 🌧️ | Rainy season | `≋` (wavy line) with #3b82f6 color |
| 🏞️ | Park details | `▭` (rectangle) with #22c55e color |

**Files**: `srv/park_stats_handlers.go` (lines 322, 325, 341, 345, 349, 419, 423, 425), `srv/templates/globe.html` (lines 5954, 5976, 5987, 6008, 6041, 6066, 6073, 10112, 10117, 12291, 12352, 12359, 14440, 14461), `srv/templates/park_analysis.html` (lines 463, 482)

### Data/Statistics (7 instances)
| Current | Usage | Suggested Replacement |
|---------|-------|----------------------|
| 📊 | Summary statistics, charts | `≡` (three lines) with #3b82f6 color |
| 📈 | Fire groups trend | `╱` (rising line) with #ef4444 color |
| 📉 | Decreasing trends | `╲` (falling line) with #22c55e color |
| 📝 | Text/documents | `≡` (lines) with #888 color |
| 📅 | Calendar/dates | `▭` (rectangle) with #888 color |

**Files**: `srv/park_stats_handlers.go` (line 263), `srv/templates/globe.html` (lines 1804, 5823, 5865, 12465)

### Infrastructure/Technical (10 instances)
| Current | Usage | Suggested Replacement |
|---------|-------|----------------------|
| 🗺️ | Map, layers, navigation | `⊞` (square with plus) with #3b82f6 color |
| 💾 | Storage/disk | `◫` (square with line) with #888 color |
| 🛤️ | Roads | `═` (double line) with #888 color |
| 🏗️ | GHSL/construction | `▦` (square with fill) with #888 color |
| 🖨️ | Print | `⊡` (square in square) with #888 color |
| ▶️ | Play/run | `▶` (keep, standard triangle) |
| ⬇ | Download | `↓` (keep, standard arrow) |
| ⬆️ | Upload | `↑` (keep, standard arrow) |

**Files**: `srv/templates/admin.html` (lines 209, 217, 230), `srv/templates/globe.html` (lines 7840, 9990, 12012, 14496), `srv/templates/park_analysis.html` (lines 475, 478, 482, 490)

### Other (5 instances)
| Current | Usage | Suggested Replacement |
|---------|-------|----------------------|
| ⏭️ | Skip (in tests) | `»` (double chevron) |
| ⏱️ | Time animation | `◷` (clock) with #888 color |
| ⭐ | Starred items | `★` (keep, standard star) |

**Files**: `srv/templates/globe.html` (lines 7840), `srv/templates/test_pinning.html` (lines 56, 91), `srv/templates/park_analysis.html` (line 498)

## Implementation Strategy

### Phase 1: Backend (Go files)
Replace emojis in Go string literals with simple ASCII + color codes will be handled by frontend CSS.

### Phase 2: Frontend (HTML/JS)
Replace emoji characters with:
1. **Inline spans**: `<span class="icon-fire">●</span>` with CSS classes
2. **CSS pseudo-elements**: Use `::before` content for repeating patterns
3. **SVG icons**: For complex shapes (optional, phase 3)

### Phase 3: CSS Classes
Create reusable icon classes:
```css
.icon-fire { color: #ef4444; font-weight: bold; }
.icon-success { color: #22c55e; }
.icon-warning { color: #f59e0b; }
.icon-info { color: #3b82f6; }
.icon-cooling { color: #60a5fa; }
.icon-tree { color: #22c55e; }
.icon-settlement { color: #888; }
```

## Total Count
- **120+ emoji instances** across 11 files
- **32 unique emoji characters**

## Benefits
1. **Consistent styling** - matches dark theme aesthetic
2. **Better performance** - ASCII characters load faster than Unicode emoji
3. **Accessibility** - screen readers handle ASCII better
4. **Cross-platform** - no emoji rendering differences between OS/browsers
5. **Flexibility** - easy to change colors via CSS

## Next Steps
1. ✓ Audit complete
2. Create icon classes in CSS
3. Replace emojis in backend Go files
4. Replace emojis in frontend templates
5. Test across all pages
6. Commit with proper message
