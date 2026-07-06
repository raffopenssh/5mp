# Handover: Wire daily park rotation → fire/deforestation alert refresh

**Status: NOT IMPLEMENTED — investigation done, plan below. No code changed this session.**

## Task (user request)
Turbidity (`analysis/river_turbidity.py --rotate`, cron 06:00) and GFW mining/deforestation
(`analysis/gfw_alerts.py --rotate`, cron 04:30) already scan ONE park/day (CAF_+SSD_ priority).
Each scan also opportunistically enriches roads/rivers/place names (`enrich_park_infra`).
**Wire it so the scanned park's fire + deforestation alerts/narratives are redone afterwards**
(richer context: roads, rivers, osm_places, and GFW deforestation covering 2024+ which Hansen
lacks — deforestation_events currently end at year 2023).
**Constraint: keep narrative quality & polygon mapping exactly as currently in the DB.**

## Key findings (what "currently in the DB" means)

- **deforestation_events narratives = PYTHON style** from `scripts/rebuild_events_enhanced.py`
  ("Slash-and-burn clearing detected in 2023. Affected 0.01 km² across 1 patch. ... Located
  31.7km from X. Near Y river."). All 16,119 rows classified_at=2026-02-15 by python.
  The Go classifier (`srv/deforestation_classifier.go`, different text style "In 2023, ...")
  is gated by `classified_at < now-365d` in `srv/fire_narrative_cache.go:classifyParkDeforestation`
  → will WRONGLY rewrite all narratives in Go style starting 2027-02-15. Do NOT use Go for deforest.
- **park_settlements narratives = GO style** (`srv/settlement_classifier.go` ClassifySettlement,
  9,931 rows classified 2026-03-02). Mining scoring already uses turbidity+GFW signals.
  → safe to force-rerun Go classifier per park (same code = same quality).
- **fire_narrative_cache** = written by Go (`computeFireNarrativeForCache`, computed_at 2026-07-05)
  via NarrativeCacheWorker; python `precompute_narratives_v5.py` also writes it (daily_fire_update
  step 5). Trajectory context (nearest_place/river/road in properties_json) comes from
  `scripts/load_fire_groups_to_db.py` — THIS is what benefits from new osm_places/roads.
- **Polygon mapping**: deforestation_events.polygon_ids = comma list of
  feature_geometries.feature_id (`deforest_{park}_{year}_{idx}`, feature_type='deforestation').
  API joins via `(','||polygon_ids||',') LIKE ('%,'||fg.feature_id||',%')` (srv/api.go:4355 etc.)
  → feature_id format is free-form as long as polygon_ids references it. Preserve existing rows/IDs.
- feature_geometries has stat_value triggers reading properties_json ($.area_km2 for deforestation).
- `rebuild_events_enhanced.py` does global `DELETE FROM deforestation_events` — NEVER run as-is.
- GFW data: `data/gfw_alerts/{park}.json` = 0.01° cell clusters {lat,lon,n,first,last,high_conf},
  ~400 days back. Only CAF_Chinko scanned so far (state.json). ~10m px → area ≈ n × 0.0001 km².
- Fire detections table only has 2026-02-26+ (NRT); history lives in data/fire_groups_v5/*.json.
- precompute_narratives_v5.py has --incremental/--days but NO --park; load_fire_groups_to_db.py HAS --park.
- Server has RequireAdminOrLocal middleware (used by /api/update-fire-alerts) — good pattern for a
  localhost-triggered refresh endpoint.

## Recommended implementation plan

New script `scripts/daily_park_refresh.py --rotate` + cron `30 7 * * *` (after both scans):
1. Determine park(s) scanned in last 24h from `data/gfw_alerts/state.json` + `data/turbidity/state.json`.
2. **GFW → deforestation events 2024+** (idempotent per park):
   - Cluster the park's GFW cells (5km greedy clustering, reuse logic from rebuild_events_enhanced),
     bucket by year of `last` date, only years ≥ 2024 (Hansen ends 2023 → no double count).
   - Insert feature_geometries rows: feature_type='deforestation',
     feature_id=`deforest_gfw_{park}_{year}_{lat}_{lon}` (deterministic → INSERT OR REPLACE safe),
     small square polygon per cell, properties_json {year, area_km2≈n*0.0001, lat, lon},
     start_date/end_date from first/last.
   - Insert/replace deforestation_events per cluster with polygon_ids = its cell feature_ids.
     Dedup key: (park_id, year, rounded centroid) or a source='gfw' marker column—simplest:
     deterministic delete+reinsert of only rows whose polygon_ids LIKE 'deforest_gfw_{park}_%'.
3. **Reclassify deforestation for that park in PYTHON**, reusing the classification +
   `_generate_deforestation_narrative` functions from `scripts/rebuild_events_enhanced.py`
   (import the EventRebuilder class; do NOT copy-paste). UPDATE rows in place (keep id,
   polygon_ids, lat/lon); only classification, classification_confidence, pattern_type,
   narrative, fires_same_year, fire_ratio, classified_at change. New roads/rivers/places
   now flow into linear-pattern detection and location text.
4. **Settlements**: trigger Go reclassify for the park ignoring the 365d gate. Add
   `POST /api/refresh-park?park=X` (RequireAdminOrLocal) that calls a force variant of
   classifyParkSettlements (skip only the deforest Go classifier! see finding above) and
   then recomputes fire_narrative_cache for that park (computeFireNarrativeForCache + upsert,
   same as PrecomputeRecentFireNarratives loop body). Script calls it via localhost curl.
5. **Fire alerts/narratives**: `python3 scripts/load_fire_groups_to_db.py --park X --force`
   (re-enriches trajectory properties with new places/rivers/roads), then the Go endpoint
   from step 4 refreshes fire_narrative_cache. (Alternative: add --park to
   precompute_narratives_v5.py; Go path is simpler and is the current cache writer.)
6. Re-export that park's `data/deforestation_events/{park}.json` +
   `data/settlement_events/{park}.json` (per-park variant of scripts/export_events_from_db.py —
   add --park arg) so JSON mirrors DB.
7. Also fix Go gate: in classifyParkDeforestation, never overwrite python-classified rows
   (e.g. require classification IS NULL), or the 2027 auto-rewrite will trash narrative style.

Watch: DB is WAL, 1.8GB, 6.1M fire rows — per-park updates only, LIMIT exploration, backup before schema changes.

## Testing
- Run on CAF_Chinko (has gfw_alerts + turbidity data already).
- Verify: deforestation-narrative API shows 2024+ GFW events with polygons (popup accordion,
  polygon_ids resolve); narrative text style unchanged for pre-2024 events (diff a few rows
  before/after); settlement classifications only move where new context exists;
  fire narrative mentions new place/river names.
- `?pwd=test2026&test=1&popup=CAF_Chinko`, TEST.findBrokenEntries('deforestation').

## Related docs
- analysis/HANDOVER_MINING_DETECTION.md (turbidity/mining pipeline, session history)
- docs/FIRE_PIPELINE.md, docs/DATA_FLOW.md
