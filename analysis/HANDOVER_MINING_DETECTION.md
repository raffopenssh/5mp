# Handover: Artisanal Gold Mine Detection (5MP)

**Updated:** 2026-07-06 (session 2). Continue in a fresh conversation with this doc.

## Status: pipeline built & live for CAF_Chinko

### Confirmed mine (registered)
- **Pit:** 7.44637°N, 24.02954°E (Chinko headwaters, OUTSIDE park, upstream).
  Largest bright-bare cluster ~0.5ha (53 px S2 20260530) + second cluster
  41px at 7.44219,24.02137. Registered as `park_settlements` id **29800**
  (classification=mining, conf 0.95).
- **Onset dating** (`analysis/date_mining_onset.py`, monthly S2 bright-bare
  fraction at pit ±150m, `data/turbidity/artifacts/pit_onset_series.json`):
  bright_bare 0.02-0.03 baseline; rises 2025-03/04 (0.08→0.14), again
  2026-02→04 (0.07→0.11). Strong seasonal confound (dry-season bareness) —
  interpretation: clearing began **~Mar-Apr 2025**, expanded 2026. NB: monthly
  red/NDVI at this savanna site never shows a clean step; the *turbidity* on
  the river is the unambiguous signal.
- **Downstream impact:** plume masks (S2C_34NHN_20260619) snapped to OSM
  waterways = **21.3 turbid km** in that scene footprint
  (`data/turbidity/artifacts/turbid_extent.json`; masks turbid_px*.json
  copied there from /tmp). Full-basin scan shows Chinko mainstem turbid_km
  ≈ 67 over 647km surveyed (see data/turbidity/CAF_Chinko.json rivers[]).

## Components shipped (session 2)

### 1. Per-park turbidity scanner — `analysis/river_turbidity.py` (rewritten)
- `--park CAF_Chinko` / `--rotate` (most-stale park w/ PBF coverage) /
  `--datetime 2025-01-01/2025-03-01` for historic scans; `--days 45` default.
- osmium bbox-extract waterways from country PBF (`PBF_MAP` in script: CAF,
  SSD present in `data/osm_raw/`), cached `data/osm_raw/waterways/{park}.geojson`.
- Chains named rivers (OSM ways = flow order), samples red+SCL every
  250m (rivers) / 500m (streams ≥8km), newest low-cloud scenes first.
- Two alert types in `data/turbidity/{park}.json`:
  - `turbidity_onset`: red > 1.8× upstream rolling median, >1200, confirmed
    4/6 downstream samples, upstream clean (<1000), dedup 5km.
  - `turbid_headwater`: turbid at uppermost observable water sample and
    ≥5km turbid — the confirmed-mine signature (source hidden in narrow channel).
- CAF_Chinko current output: 3 alerts (Chinko headwater 66.8 turbid-km;
  unnamed river 5.65N/24.34E onset 26.8km; Mbari headwater 1.5km — the Mbari
  one is likely marginal/wet-season, watch false-positive rate).
- Runtime ~15min/park, ~13.5k sample points, no API keys needed.

### 2. Server integration — `srv/turbidity.go` (new)
- Loads `data/turbidity/{park}.json` + `data/gfw_alerts/{park}.json` (cached).
- `scoreMining()` in `srv/settlement_classifier.go` now adds:
  +0.5 turbidity alert <3km / +0.3 <10km; +0.2 GFW alerts >100 within 5km
  (+0.1 >20). `ClassifiedSettlement` gained `TurbidityAlertKm`,
  `TurbidityAlert`, `GFWAlertsWithin5km`; mining narrative mentions plume+GFW.
- `SyncTurbidityAlerts()` (via `StartTurbidityWatcher()`, every 6h, started in
  cmd/srv/main.go): creates `notification_type='mining_alert'` notifications
  (dedup on reference_id `turbidity_{park}_{lat}_{lon}`, alert JSON in
  reference_data) and **auto-registers mining settlement candidates** for
  alerts with ≥10km downstream plume (`RegisterMiningCandidate`).
- `cmd/register-mining/`: manual registration CLI.
- Registered so far: 29800 (confirmed pit, manual), 29801 (7.184,24.559
  headwater alert — auto, classified temporary_camp 0.8; review), 29802
  (5.650,24.341 onset — auto, mining 1.0).
- 3 mining_alert notifications live in DB (ids 22285-7).

## NEXT STEPS (user-requested, not yet done)

1. **Daily cron** at a different time than others (gfw_alerts runs 04:30,
   fire cron 03:00) — e.g. `0 6 * * *  cd /home/exedev/5mp && python3
   analysis/river_turbidity.py --rotate >> logs/turbidity.log 2>&1`.
   Server watcher picks results up within 6h automatically.
2. **Dedicated park-tooltip section** ("Mining / Water quality"?). Popup
   sections live in srv/templates/globe.html ~line 6500 (pa-popup-section
   blocks; fetchPopupFireData pattern ~line 6670). Per-site show: river name,
   alert type (onset vs turbid headwater), date+scene, downstream turbid-km,
   distance/direction from park, link to notification, mining-classified
   settlements w/ confidence + narrative. Data source: new API endpoint
   (e.g. GET /api/parks/{id}/turbidity serving data/turbidity/{park}.json +
   mining settlements) — endpoint NOT yet written.
3. **Pinning**: reuse togglePinFromIcon/addPinnedLayer machinery
   (globe.html ~12700+). Options: pin alert points (build GeoJSON client-side
   from the API), and ideally the turbid river segments (would need plume
   mask → line features; artifacts/turbid_px*.json can seed a
   feature_geometries type 'turbidity').
4. **Timeline filter**: alerts carry scene `date`; monthly historic scans via
   `--datetime` can build an onset timeline per river (first turbid month).
   Integrate with globe time slider (window.dateFrom/dateTo, see
   addPinnedLayer dateParams) by filtering alerts on date.
5. **Rollout beyond CAR+SSD** (user decision): fetch geofabrik PBF per
   country on demand, extract per-park waterways (small), run scans for all
   parks of that country, also enrich park roads/river places if useful,
   then DELETE the big PBF once done. Implement as a `--country XYZ` batch
   or extend --rotate to: pick next country with unscanned parks → download
   PBF → loop parks → cleanup. Keep only data/osm_raw/waterways/*.geojson.
   Geofabrik URL pattern: https://download.geofabrik.de/africa/{name}-latest.osm.pbf
   (need ISO3→geofabrik-name map for the 33 countries in keystones).
6. Tune false positives: Mbari headwater alert looks weak (1.5km, but passed
   ≥5km gate via turbid_km? — recheck; wet-season sediment naturally higher).
   Consider NASA POWER rain control per alert (method in session-1 notes).
7. review settlement 29801 classification (temporary_camp despite alert 0km —
   scoreMining ties with pastoral; maybe boost turbidity weight or force
   mining when TurbidityAlertKm<1).

## Method notes / gotchas
- SCL==6 (water) only in wide channels; headwater sources hide upstream of
  first water pixel — that's why turbid_headwater type exists.
- Dry season (Dec-Apr) makes whole savanna bare AND rivers shrink; best scan
  months May-Nov. Onset dating must compare same-season months.
- GFW integrated alerts do NOT see this mining (savanna, no canopy). VIIRS
  non-discriminating. Turbidity is primary; GFW+fires only corroborate.
- rasterio direct-HTTP on sentinel-cogs is free & keyless; ~1-2s/scene open.
- fire_detections table has NRT 2026-02-26+ only; full history in
  data/fire_groups_v5/*.json (centroid=[lon,lat]!).

## Inventory
- analysis/river_turbidity.py (scanner), date_mining_onset.py (pit dating),
  trace_channel.py + trace_turbidity.py (session-1 one-offs), gfw_alerts.py
  (daily cron 04:30, data/gfw_alerts/).
- srv/turbidity.go, scoreMining in srv/settlement_classifier.go,
  cmd/register-mining/.
- data/turbidity/CAF_Chinko.json + state.json + artifacts/ (plume masks,
  turbid extent, pit onset series).
- data/osm_raw/: CAR+SSD PBFs (283MB — candidates for cleanup per step 5),
  waterways/CAF_Chinko.geojson cache, caf_rivers/mining geojsons.
- Test: https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026&test=1&popup=CAF_Chinko
- DB backup: db.sqlite3.bak (pre-change, 2026-07-06, delete when confident).
