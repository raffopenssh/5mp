# Open Points - 5MP Conservation Monitoring

Last updated: 2026-02-21

## 🔄 Still Open (from remove-outdated-todos)

### High Priority
1. **Fix narratives** - Settlements/deforestation narratives don't use polygon data for context
2. **Fix pinning** - Individual narratives/notifications pinning, pinned info card display
3. **Nested hierarchy** - For starred section and KML export (parks grouped by country/region)

### Medium Priority
4. **Learning panel** - Test and fix the GPX learning/pattern detection UI
5. **RSS feed** - Fix updates on places (notifications feed)

### Low Priority (Code TODOs)
6. `srv/api.go:3722` - Implement bbox filtering for notifications
7. `srv/park_analysis.go:179` - Query Overpass API for roads in bbox
8. `srv/upload.go:427` - Compute protected area ID from area store

### Data Quality
9. Deforestation classification improvements
10. Settlement classification improvements
11. Species "Endangered: 0" - needs IUCN category mapping fix

---

## ✅ Completed

### Session 2026-02-21
- ✅ Pulsing dot position - MBTiles progress dot overlaps notification badge
- ✅ Publications in star reports - Added to fetchParkFullData and rendering
- ✅ MBTiles modal tested and working

### Previous Sessions (continue-conversation)
- ✅ Publications API uses WDPA ID lookup
- ✅ River/road names as text labels (zoom 10+)
- ✅ Bell notifications with MBTiles progress & publication citations
- ✅ Notification auto-refresh every 30s
- ✅ MBTiles max zoom 17, 8-hour retention
- ✅ Accordion icons uniform 13px
- ✅ Removed emojis from notifications
- ✅ Infrastructure buttons show counts

### Previous Sessions (remove-outdated-todos)
- ✅ Mobile notification dropdown
- ✅ Fire notification click handler
- ✅ Fire alerts updating (trajectory load in cron)
- ✅ Publication sync rate limiting (API key)
- ✅ Publication sync includes never-synced parks
- ✅ Mobile footer overlap
- ✅ Info modal content
- ✅ Weekly fire comparison chart
- ✅ Fire stats correct values
- ✅ Groups/km² calculation (divide by years)
- ✅ Emojis removed from popup/star report
- ✅ JSON atomic writes
- ✅ Merged KML export
- ✅ Detailed XLS export (summary + per-park tabs)

---

## 📊 Current State

### Data Coverage
- 162 protected areas with boundaries
- 6.1M+ fire detections (2018-2026)
- 453K feature geometries
- 39.5K IUCN mammal species records
- Publications synced via OpenAlex WDPA ID

### Background Workers
| Worker | Schedule | Status |
|--------|----------|--------|
| Upload Queue | Every 2s | ✅ Running |
| GPX Learner | Continuous | ✅ Running |
| Fire NRT | 3am UTC | ✅ Configured |
| Fire Backfill | 4am UTC | ✅ Configured |
| Narrative Cache | Weekly | ✅ Configured |
| Publication Sync | Daily | ✅ Running |
| FAOLEX Sync | Sundays | ✅ Configured |

---

## 🔗 Quick Reference

- **URL**: https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026
- **Test parks**: COD_Virunga, CAF_Chinko, COG_Lac_Télé
- **Build**: `make build && pkill server; ./server &`
