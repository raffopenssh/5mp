# Handover: Artisanal Gold Mine Detection (Chinko, CAR)

**Date:** 2026-07-06. Continue in a fresh conversation with this doc.

## Goal
Detect artisanal/unregulated gold mines that our current settlement-classifier
(hydrorivers + GHSL, `srv/settlement_classifier.go` → `scoreMining`) overlooks.
Only 3 mining sites classified in CAF_Chinko today (`park_settlements`,
classification='mining', ids 20775/20779/20782). User confirmed new mines exist
at the **Chinko river headwaters** and asked to combine:
- fire data (VIIRS, `fire_detections` table + `data/fire_groups_v5/*.json`)
- OSM waterways (downloaded: `data/osm_raw/caf_rivers.geojson`, 41.6k segments;
  also `caf_mining.geojson`, `ssd_mining.geojson`, raw PBFs for CAR + SSD)
- **river color / turbidity** from Sentinel-2 (KEY SIGNAL, works — see below)
- NASA POWER rainfall as control for rain-driven turbidity
- downstream/watershed impact (user suggested global-river-runner /
  mghydro.com/watersheds for tracing)

## KEY FINDING — confirmed turbidity plume (likely active mining)
Sentinel-2 scene **S2C_34NHN_20260619_0_L2A** (2026-06-19, 3% cloud):
- The **upper Chinko mainstem turns abruptly turbid (golden-brown)**.
  Clean water upstream of ~6.77°N; strongly turbid from **~6.75°N,24.266°E
  down through 6.60°N** and beyond (red reflectance jumps 600→1700-2100).
- The turbid channel traces NE **up a western tributary** past 7.05°N toward
  ~7.35°N, 24.0–24.15°E (scene edge). Plume head not yet localized — next step:
  inspect chips north of 7.13°N along ~24.05–24.15°E, and the parallel branch
  at ~23.93°E. True-color chips saved in /tmp/onset/ (regenerate if gone).
- **Rain control done** (NASA POWER PRECTOTCORR): 7d rain before 2026-06-19 was
  38-56mm basin-wide, uniform — rain can't explain a point-source onset;
  upstream-of-onset water is clean on the same date. Signal is anthropogenic.
- Esri basemap (older imagery) shows the same river CLEAN → activity is recent.
- VIIRS fire groups near headwaters (2025-12→2026-03) include several small
  spot/local fires within 2km of the OSM river, e.g. grp dd1a9e50 (7.497,24.569),
  fe7b2e7f (7.682,24.513) — candidate camp corroboration.

## Method that works (river turbidity scan)
`analysis/river_turbidity.py` — samples S2 L2A red/green/nir/SCL along OSM
river polylines (earth-search.aws.element84.com STAC, sentinel-cogs COGs,
rasterio direct HTTP). Water = SCL==6. Turbidity proxy: red reflectance jump
vs upstream rolling median (>1.8x and >800 = alert).
`analysis/trace_channel.py` — full-raster turbid-pixel mask from TCI
((r>140)&(r-b>60)&(r-g>20)) → maps whole plume network; /tmp/turbid_px.json.

## Fire-side candidates (recurring small fires, riverine, remote)
From fire_groups_v5 clustering (small groups ≤60 fires, spot/local type,
recurring ≥2yrs, <2km from OSM river, far from known places/settlements):
top: 6.7524,24.7088 / 6.7512,23.3308 / 6.6584,23.2022 / 6.5929,24.9801 /
5.5454,23.1721 / 6.4252,23.0874(3yrs) / 4.8569,23.7379(3yrs).
Esri z15 chips were inconclusive (old imagery, savanna bare patches) —
should re-check with fresh S2 instead. /tmp/mining_candidates.json (58 sites).

## Suggested next steps
1. Localize plume head: S2 chips N of 7.13°N; then Esri/S2 zoom for pits/camps.
2. Wrap turbidity scan into a script for all park rivers (needs OSM waterways
   per park; CAR+SSD PBFs already in data/osm_raw/). Alert = onset point.
3. Time series: run same scan on monthly S2 scenes to date mining onset
   (scene IDs found via STAC query in analysis/river_turbidity.py).
4. Cross-check turbid onsets with small VIIRS fire clusters (camp fires) and
   Hansen deforestation; add `turbidity` evidence to scoreMining().
5. Downstream impact: trace turbid extent (already have full plume mask) =
   km of river impacted; consider mghydro watershed API for basin delineation.
6. Surface as notifications/park narrative ("possible new mining, river X").

## Data/tools inventory
- data/osm_raw/: CAR+SSD PBF, caf_rivers.geojson, caf/ssd_mining.geojson
- /tmp/chinko_river_color.json (1947 samples along 644km mainstem, 2026-05/06)
- /tmp/turbid_px.json, /tmp/mining_candidates.json, /tmp/onset/*.jpg chips
- rasterio/shapely/numpy installed; osmium-tool installed
- STAC: earth-search.aws.element84.com/v1, collection sentinel-2-l2a, free
- NASA POWER: power.larc.nasa.gov daily PRECTOTCORR point API, free
- DB: fire_detections has NRT 2026-02-26→today only; full history 2020-2026
  in data/fire_groups_v5/*.json (v5 groups, centroid=[lon,lat]!)
