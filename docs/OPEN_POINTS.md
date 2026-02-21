# Open Points - 5MP Conservation Monitoring

Last updated: 2026-02-21

## ✅ Recently Completed

### Session 2026-02-21
1. ✅ Pulsing dot position - MBTiles progress dot now overlaps notification badge
2. ✅ Publications in star reports - Added to fetchParkFullData and renderParkReportInline
3. ✅ MBTiles modal - Tested and working (zoom slider, storage estimates, 8-hour retention)

### Previous Sessions (continue-conversation)
1. ✅ Publications API uses WDPA ID lookup (Lac Télé now shows publications)
2. ✅ River/road names as text labels on map (zoom 10+)
3. ✅ Bell notifications show MBTiles progress and publication citations
4. ✅ Notification auto-refresh every 30s
5. ✅ MBTiles max zoom increased to 17
6. ✅ Accordion icons uniform 13px in 16x16 container
7. ✅ Removed emojis from publication notifications (use ◉)
8. ✅ Infrastructure pin buttons show counts directly

### Previous Sessions (remove-outdated-todos)
1. ✅ Mobile notification dropdown fixed
2. ✅ Fire notification click handler fixed
3. ✅ Fire alerts updating (trajectory load in cron)
4. ✅ Publication sync rate limiting (added API key)
5. ✅ Publication sync includes never-synced parks
6. ✅ Mobile footer overlap fixed
7. ✅ Info modal content updated
8. ✅ Weekly fire comparison chart added
9. ✅ Fire stats showing correct values
10. ✅ Groups/km² calculation fixed (divide by years)
11. ✅ Emojis removed from popup and star report
12. ✅ JSON file corruption fixed (atomic writes)
13. ✅ Merged KML export for multiple parks
14. ✅ Detailed XLS export (summary + per-park tabs)

---

## 🔄 In Progress / Low Priority

### Code TODOs (from source)
1. `srv/api.go:3722` - Implement bbox filtering for notifications
2. `srv/park_analysis.go:179` - Query Overpass API for roads in bbox
3. `srv/upload.go:427` - Compute protected area ID from area store

### Data Quality (lower priority)
1. Deforestation classification improvements
2. Settlement classification improvements
3. Species "Endangered: 0" showing for parks with endangered species (needs IUCN category mapping)

### UX Polish (nice-to-have)
1. RSS feed improvements for places
2. Learning panel testing
3. Pin individual narratives/notifications

---

## 📊 Current State

### Data Coverage
- 162 protected areas with boundaries
- 6.1M+ fire detections (2018-2026)
- 453K feature geometries (deforestation, fire trajectories, settlements)
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

### API Health
- Publications: ✅ Using WDPA ID lookup
- Fire narratives: ✅ Cached and serving
- MBTiles: ✅ Queue processing, 8hr retention

---

## 🔗 Quick Reference

- **URL**: https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026
- **Test parks**: COD_Virunga (full data), CAF_Chinko (fire trajectories), COG_Lac_Télé (publications)
- **Server binary**: `/home/exedev/5mp/server`
- **Build**: `make build && pkill server; ./server &`
