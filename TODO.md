# 5MP Globe - UI Stabilization Sprint

## CRITICAL (Must fix for demo)

- [ ] **P0: Park tooltip not working** - Safari 18.6, Firefox 146, DuckDuckGo Android all fail to show park tooltips on click
- [ ] **P0: Login broken** - Status doesn't change after successful login (test: test@example.com / testpass123)

## HIGH PRIORITY (Core functionality)

- [ ] **P1: Share link icon** - Replace upload-looking icon with chain/link icon
- [ ] **P1: Share link state** - URL should encode all state (filters, searches, selected parks, view)
- [ ] **P1: Combine searches** - Merge country search and park search into one unified search
- [ ] **P1: Search autocomplete** - Search box should have autocomplete
- [ ] **P1: Search sets filter** - When user searches for park/country, set as active filter
- [ ] **P1: Stats card adaptation** - Stats should adapt to current map area and bounding box, not just time slider

## MEDIUM PRIORITY (UX improvements)

- [ ] **P2: 5MP modal update** - Remove "coming soon" for GHSL and roadless (they're implemented)
- [ ] **P2: CSV download** - Include all info per park (narratives, stats), add active filter to filename
- [ ] **P2: Roadless indicator** - Clarify what the percentage means
- [ ] **P2: Recent activity** - Should show all activity, not just logged-in user's
- [ ] **P2: Mystery X button** - Investigate and fix/remove (see screenshot)
- [ ] **P2: Virunga appearing** - Investigate why it shows "Virunga" in filter panel
- [ ] **P2: Remove duplicate date filter** - Time range shown in filter panel is redundant (already on time slider)

## UI SIMPLIFICATION

- [ ] **P3: Remove "Click a park to select"** text
- [ ] **P3: 162 toggle behavior** - With bbox, deselect 162, show bbox park count instead
- [ ] **P3: Parks as tags** - Show parks as tags (not lines), with country codes, allow deselect
- [ ] **P3: Refactor panels** - Move bbox selector to Selected Parks; keep only Filter, Search, Notifications on top
- [ ] **P3: Remove title text** - Keep globe icon as reset/home button
- [ ] **P3: Simplify auth** - Single login/upload button, email signup only (no password auth for now)
- [ ] **P3: Auto-approve signups** - All new users approved by default

---

## Test Data

GPX test files in `data/` directory:
- test_patrol_1.gpx through test_patrol_7.gpx (new)
- test_patrol_virunga.gpx, virunga_patrol.gpx (existing)

## Testing Checklist

- [ ] Safari 18.6 - tooltips work
- [ ] Firefox 146 - tooltips work  
- [ ] DuckDuckGo Android - tooltips work
- [ ] Chrome - tooltips work
- [ ] Share link preserves state
- [ ] Login/logout flow works
- [ ] Search finds parks and countries
- [ ] CSV download works
- [ ] GPX upload works with test files
