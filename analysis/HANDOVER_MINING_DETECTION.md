# Handover: Artisanal Gold Mine Detection (5MP)

**Updated:** 2026-07-07 (session 4). Continue in a fresh conversation with this doc.

## Session 4: pit scanner (the missing signal)

**Problem diagnosed:** the turbidity cron missed known mines because (a) the
`SCL==6` water gate never fires on narrow headwater channels (checked: zero
water pixels at the confirmed Chinko pit, its tributary, and confluence in
all Jun/Jul-2026 scenes), and (b) wet season suppresses both bare-earth and
ratio-vs-upstream signals.

**Fix shipped: `analysis/mining_pits.py`** — detects the pits themselves
(what the eye sees on Google Earth), no water pixels needed:
- Corridor = 0.05° tiles touched by OSM waterways (reuses
  `data/osm_raw/waterways/{park}.geojson` caches).
- Bright-bare clusters (red>1400, NDVI<0.35, ≥8px=0.08ha, ≤3000px) within
  1km of a waterway, newest clear scene per tile.
- Persistence pass (older scene ≥10d back, batched per scene) kills
  clouds/flood/burn artifacts. History pass (~1yr ago, same season) splits
  **new clearings** (score +0.20, `new_since` date) from villages/outcrops
  (score −0.15). Scoring: size/water-dist/ponds/persistence/newness.
- Output `data/mining_pits/{park}.json`; `--rotate` daily cron 09:00
  (`logs/mining_pits.log`, state in `data/mining_pits/state.json`);
  `--bbox` for corridor-scoped scans (merges with prior sites outside bbox).

**Validation:** confirmed Chinko pit re-found at exact coords (44px,
persistent). Bonus: discovered NEW 2025-onset complex ~7.24–7.28N 24.07–24.09E
(three clusters 1.4–8.6ha, vegetated Aug 2025) — 20km S of the known pit.

**Akobo/Boma test (user-requested):** `--park SSD_Boma --bbox 34.0,5.8,35.3,8.2`
→ **228 sites**, 121 new-since-2025, top sites 25–29ha with ponds at
6.58N 34.89E etc. — the known SSD/Ethiopia ASM belt lights up along the
Akobo drainage. Verified visually (S2 truecolor + Esri basemap).

**Server/UI wiring:**
- `srv/turbidity.go`: pits served in `/api/parks/{id}/turbidity` (`pits` key);
  `syncPitSites()` (called from SyncTurbidityAlerts, 6h watcher) creates
  `mining_alert` notifications (`reference_id=pit_{park}_{lat}_{lon}`) and
  auto-registers settlement candidates — capped: score≥0.8, top 15/park.
- `scoreMining()`: +0.5 if pit <1km, +0.2 <5km (`PitSiteKm`/`PitSite`).
- globe.html Mining & Water Quality accordion (reworked 2026-07-07):
  summary mini-stat table (pit sites, bare pit area km², new clearings ≈12mo,
  turbidity alerts + total turbid km, suspected sites) + monthly sparkline
  (new bare-ground ha by `new_since` month, turbidity alerts overlaid,
  click-to-filter like the fire sparkline). Pit list is grouped into
  clusters (greedy, ≤2 km link distance), collapsible, top-8 clusters +
  "+N more" expander; per-pit rows keep TEST badges `pit-{origIdx}`
  (indices refer to the UNFILTERED sites[] so `pit_N` feature_ids match
  buildTurbidityGeoJSON). Map tooltips: `turbidityFeatureHTML(props)`
  renders rich hover/pinned popups (area ha+km², pond ha, newness,
  persistence, plume km, ratio-vs-upstream) wired into
  createFeatureHoverPopup/showPinnedFeaturePopup ('turbidity' type) and
  single-pin hover handlers.
- globe.html: "Pit detections" block in Mining & Water Quality accordion
  (score≥0.45 shown, ⛏ icon, new-since flag, TEST badges pit-N); orange
  `pit_site` circles (score-scaled) in the turbidity pin layer.

**Tuning notes:** MAX_PX=3000 skips towns; kept[:400] caps verify work;
SSD_Boma full run ~25min. Wet-season bareness suppression means dry-season
rescans will find MORE (rotation handles this). Consider: polygon outlines
instead of centroids; Sentinel-1 pond detection for cloud-blind confirmation.

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

## Session 3 progress (this session)

### Rollout infrastructure (DONE)
- `analysis/river_turbidity.py`: `GEOFABRIK` map for all 33 keystone
  countries. `ensure_waterways()` downloads country PBF to /tmp on cache
  miss, extracts waterways for ALL parks of that country in one pass,
  deletes the PBF. Only small per-park geojson caches kept
  (`data/osm_raw/waterways/`, 27MB for CAF+SSD+GNQ). Big PBFs deleted
  (283MB freed). `--rotate` now eligible for all 162 parks.
- **Opportunistic infra enrichment** (`enrich_park_infra`): while a park
  PBF extract is on disk, backfills missing `osm_places` (settlements,
  named rivers deduped by longest way with osm_tags attrs, peaks/hills)
  and `roads_heigit` (highway type, surface, length, geometry, derived
  dl_class_2024 + passability PAV_/UNP_ codes matching HeiGIT). No-op when
  park already has rows. Verified: GNQ_Reserva_de_la_Paz +74 places,
  +3768 roads. Remaining gaps fill as rotation reaches AGO/COG.

### UI (DONE)
- `GET /api/parks/{id}/turbidity` (srv/turbidity.go): raw scan JSON
  (alerts + rivers[] coverage), mining-classified park_settlements,
  GFW corroboration counts.
- "Mining & Water Quality" popup section (globe.html, after
  Deforestation): deliberately separates **evidence** (scan metadata:
  rivers/km surveyed/sample pts; turbidity alerts with ratio, downstream
  turbid-km, scene date) from **interpretation** (suspected mining sites
  with confidence %) plus GFW corroboration footnote — so users can judge
  plausibility. Click rows to zoom. TEST badges (mining-N) in test=1.
- Pinning: `turbidity` type in addPinnedLayer (builds GeoJSON client-side
  from the API: turbidity_alert + mining_site points, #eab308), icon ◈,
  label "Mining / turbidity". Works via section icon toggle.

## NEXT STEPS

1. **Daily cron** (still todo): `0 6 * * * cd /home/exedev/5mp && python3
   analysis/river_turbidity.py --rotate >> logs/turbidity.log 2>&1`.
   Watch first runs: each cache-miss country downloads its PBF (COD 400MB+,
   TZA large) — fine on /tmp (51GB disk) but verify cleanup. Server watcher
   picks results up within 6h.
2. **Timeline filter**: alerts carry scene `date`; monthly historic scans via
   `--datetime` can build an onset timeline per river. Integrate with globe
   time slider (window.dateFrom/dateTo) by filtering alerts on date.
3. Turbid river segment lines (plume mask -> feature_geometries type
   'turbidity') for a nicer pin than points.
4. Tune false positives: Mbari headwater alert weak (1.5km); consider NASA
   POWER rain control per alert (method in session-1 notes).
5. Review settlement 29801 (temporary_camp despite turbidity 0km — consider
   forcing mining when TurbidityAlertKm<1).
6. mining_alert notifications: add share-link handling (notif_mining=...?)
   like notif_fire.

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
- data/osm_raw/: waterways/*.geojson caches (all CAF+SSD parks + GNQ),
  caf/ssd_mining geojsons. Country PBFs no longer stored (auto /tmp).
- Test: https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026&test=1&popup=CAF_Chinko
- DB backup: db.sqlite3.bak (pre-change, 2026-07-06, delete when confident).
