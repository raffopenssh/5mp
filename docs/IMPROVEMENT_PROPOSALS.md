# Improvement Proposals - 5MP.globe

Based on comprehensive testing across user personas (Ministry staff, NGOs, Park managers), API testing, and UI flow analysis.

---

## High Priority Improvements

### 1. Add Country Filter
**User Need:** Ministry staff need to quickly see all parks in their country
**Effort:** Medium
**Implementation:**
- Add country dropdown in filter panel
- Populate from parks data (already have country field)
- Filter map markers by country
- Update stats panel to show country totals

### 2. Surface Deforestation Data in UI
**User Need:** NGO managers need to see deforestation trends
**Effort:** Low
**Current State:** API exists (`/api/parks/{id}/deforestation-narrative`) but not shown in popup
**Implementation:**
- Add "Deforestation" collapsible section in park popup
- Show total km² lost, worst year, trend
- Display small timeline chart

### 3. Add Data Export
**User Need:** Ministry staff need to export data for reports
**Effort:** Medium
**Implementation:**
- Add export button in filter panel or popup
- Support CSV export of visible parks
- Include: park name, country, fire count, settlement count, deforestation, roadless %

### 4. Fix Grammar in Narratives
**User Need:** Professional text for reports
**Effort:** Low
**Current Issue:** "Burned inside the park for 1 days" (should be "1 day")
**Implementation:** Add singular/plural handling in narrative_handlers.go

---

## Medium Priority Improvements

### 5. Park Comparison View
**User Need:** NGO managers need to compare parks
**Effort:** High
**Implementation:**
- Allow selecting multiple parks (already can select 162)
- Add "Compare Selected" button
- Show side-by-side stats table
- Highlight differences/priorities

### 6. Threat Prioritization Dashboard
**User Need:** Identify which parks need intervention most
**Effort:** High
**Implementation:**
- Calculate composite threat score: fire activity + deforestation + settlement encroachment
- Add sortable list view alongside map
- Color-code parks by threat level

### 7. Search by Country
**User Need:** Find parks by country, not just name
**Effort:** Low
**Current Issue:** Search only matches park names
**Implementation:**
- Include country in search index
- Show "Country: X" results

### 8. API Error Handling
**User Need:** Graceful handling of invalid requests
**Effort:** Low
**Current Issue:** Invalid park IDs return empty data instead of 404
**Implementation:** Return proper HTTP status codes with error messages

---

## Low Priority Improvements

### 9. Number Formatting
**User Need:** Clean display of large numbers
**Effort:** Low
**Current Issue:** JSON shows `1.7693999999999999` instead of `1.77`
**Implementation:** Round floats in API responses

### 10. OSM Place Data Coverage
**User Need:** Contextual fire narratives
**Effort:** Medium
**Current Issue:** Some parks (e.g., Kruger) use raw coordinates instead of place names
**Implementation:** Import more OSM places for southern/eastern Africa

### 11. Accessibility
**User Need:** WCAG compliance
**Effort:** Medium
**Current Issue:** Missing aria-labels on some controls
**Implementation:** Add aria-labels to sliders, buttons

---

## Data Wealth Showcase Ideas

The app has remarkable data that could be better highlighted:

### A. "24 Years of Deforestation" Timeline
- 3,218 deforestation events spanning 2001-2024
- Could show animated timeline of forest loss
- Powerful visualization for advocacy

### B. "Fire Response Success Rate"
- 801 fire group infractions tracked
- Shows ranger response effectiveness (STOPPED_INSIDE vs TRANSITED)
- Could highlight parks with best/worst response rates

### C. "Settlement Encroachment Pressure"
- 15,066 settlements detected
- 7 pristine parks with 0 settlements
- Could show "pressure index" based on settlement density

### D. "Roadless Wilderness Map"
- 162 parks with roadless analysis
- Some parks 99%+ roadless (true wilderness)
- Could highlight most pristine areas

### E. "Pan-African Conservation Network"
- 162 keystones across 30+ countries
- 19 legal frameworks documented
- Could show continental connectivity

---

## Implementation Priority

1. **Quick wins (this week):**
   - Fix grammar in narratives
   - Surface deforestation in popup
   - Fix number formatting

2. **Next sprint:**
   - Add country filter
   - Add data export
   - Search by country

3. **Future:**
   - Comparison view
   - Threat dashboard
   - Enhanced visualizations
