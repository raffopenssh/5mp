# 5MP Globe - UI Stabilization Sprint

## CRITICAL (Must fix for demo)

- [x] ~~**P0: Park tooltip not working**~~ - Fixed
- [x] ~~**P0: Login broken**~~ - Login hidden, anonymous uploads enabled

## HIGH PRIORITY (Core functionality)

- [x] ~~**P1: Share link icon**~~ - Done
- [x] ~~**P1: Share link state**~~ - Done: URL encodes lat/lng/zoom, time range, bbox, parks, country, movement types, popup
- [x] ~~**P1: Combine searches**~~ - Done: unified search for parks/countries/regions
- [x] ~~**P1: Search autocomplete**~~ - Done
- [x] ~~**P1: Search sets filter**~~ - Done: country selection sets filter
- [x] ~~**P1: Stats card adaptation**~~ - Done: Stats update based on visible map bounds when zoom > 4

## MEDIUM PRIORITY (UX improvements)

- [x] ~~**P2: 5MP modal update**~~ - Done: removed "coming soon" badges
- [x] ~~**P2: CSV download**~~ - Done: includes wdpa_id, name, country, area_km2; filename reflects active filter
- [x] ~~**P2: Roadless indicator**~~ - Done: added tooltip explaining "% of area >1km from roads"
- [x] ~~**P2: Recent activity**~~ - Done: API already returns all activity (shows empty when no uploads in DB)
- [x] ~~**P2: Virunga appearing**~~ - Fixed
- [x] ~~**P2: Remove duplicate date filter**~~ - Fixed: removed country search from filter panel

## UI SIMPLIFICATION

- [ ] **P3: Remove "Click a park to select"** text
- [ ] **P3: 162 toggle behavior** - With bbox, deselect 162, show bbox park count instead
- [ ] **P3: Parks as tags** - Show parks as tags (not lines), with country codes, allow deselect
- [ ] **P3: Refactor panels** - Move bbox selector to Selected Parks; keep only Filter, Search, Notifications on top
- [ ] **P3: Remove title text** - Keep globe icon as reset/home button
- [x] ~~**P3: Simplify auth**~~ - Done: GPX upload button, no login required
- [x] ~~**P3: Auto-approve signups**~~ - Done

---

## Test Data

GPX test files in `data/` directory:
- test_patrol_1.gpx through test_patrol_7.gpx (new)
- test_patrol_virunga.gpx, virunga_patrol.gpx (existing)

## Testing Checklist

- [x] Safari 18.6 - tooltips work
- [x] Firefox 146 - tooltips work  
- [x] DuckDuckGo Android - tooltips work
- [x] Chrome - tooltips work
- [x] Share link preserves state
- [x] ~~Login/logout flow works~~ - Hidden
- [x] Search finds parks and countries
- [ ] CSV download works
- [ ] GPX upload works with test files
