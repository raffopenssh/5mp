# 5MP Globe - UI Stabilization Sprint

## COMPLETED

### Critical (P0)
- [x] Park tooltip working across browsers (Safari, Firefox, Chrome, DuckDuckGo Android)
- [x] Anonymous GPX uploads (login hidden)

### High Priority (P1)
- [x] Share link icon and state preservation
- [x] Unified search for parks/countries/regions with autocomplete
- [x] Search sets country filter
- [x] Stats adapt to visible map bounds

### Medium Priority (P2)
- [x] 5MP modal updated (removed "coming soon")
- [x] CSV download with filter in filename
- [x] Roadless indicator tooltip
- [x] Duplicate filters removed

### Narratives
- [x] Fire trajectories with OSM place references
- [x] Deforestation narratives with location context and pattern descriptions
- [x] Settlement narratives with place-based references and pattern assessment
- [x] Top 10 display with "load more" for all narrative types
- [x] Collapsible popup sections

---

## REMAINING (P3 - UI Simplification)

- [x] Remove "Click a park to select" text - Done (already removed)
- [ ] 162 toggle behavior - With bbox, deselect 162, show bbox park count
- [x] Parks as tags with country codes, allow deselect - Done (country tags show counts, park tags show country codes)
- [x] Country filter section removed - Countries now shown as tags from selected parks
- [ ] Refactor panels - Move bbox selector to Selected Parks
- [x] Remove title text - Done: Globe icon resets map view to Africa

---

## KNOWN ISSUES

- Settlement display uses old format ("X at Y sector") instead of new format ("X km² near Y") on some deployments - may need cache clear
- Deforestation/settlement classification is basic (scattered/clustered/linear) - does not distinguish farms/logging/charcoal/mines

---

## Data Download

Database available at: `/static/downloads/5mp_data.sqlite3`

---

## Test Data

GPX test files in `data/` directory:
- test_patrol_1.gpx through test_patrol_7.gpx
- test_patrol_virunga.gpx, virunga_patrol.gpx

## Testing Checklist

- [x] Safari 18.6 - tooltips work
- [x] Firefox 146 - tooltips work  
- [x] Chrome - tooltips work
- [x] Share link preserves state
- [x] Search finds parks and countries
- [ ] CSV download works
- [ ] GPX upload works with test files
