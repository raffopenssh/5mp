# Open, Continent-Wide Geospatial Data Sources for ASM & Industrial Mining Monitoring in African Protected Areas

**Compiled:** 2026-08-05 · **Scope:** Africa-wide (163 protected areas), focus on artisanal & small-scale mining
(ASM: alluvial gold/diamond in savanna river headwaters — e.g. Chinko/CAR, Boma/South Sudan) plus industrial mining.
**Existing stack:** Sentinel-2 red-reflectance turbidity onsets; naive bright-bare-pixel pit detector; GFW integrated
deforestation alerts; OSM waterways; HydroRIVERS; HeiGIT roads.

**Verification legend**
- ✅ VERIFIED — URL probed with `curl` from this VM during compilation (HTTP 200 + plausible size/content-type).
- ⚠️ PARTIAL — landing page/API reachable but exact bulk file not confirmed, or needs a form/token step.
- ❌ DEAD / BLOCKED — 403/404/timeout from this VM (Cloudflare, DNS, or retired service). Details in each entry.
- 🔒 UNVERIFIED — cited from documentation; not probed. Treat as a lead, not a fact.

---

## 0. Summary table — ranked by (impact / effort)

| # | Source | Category | Impact | Effort | Status | Africa volume |
|---|--------|----------|--------|--------|--------|---------------|
| 1 | Amazon Mining Watch / `earthrise-media/mining-detector` (model weights + code, MIT/CC-BY) | ASM ML detector | ★★★★★ | Low–Med | ✅ | 16.6 MB weights; GBs of optional Amazon labels |
| 2 | mghydro Global Watersheds API (Heberger) — watershed + upstream rivers | Hydrology | ★★★★★ | Very low | ✅ | API, ~10–200 KB/call |
| 3 | Maus et al. Global mining polygons v2 (PANGAEA) | Truth data | ★★★★★ | Very low | ✅ | 24.7 MB global GPKG |
| 4 | HeiGIT ohsome API (OSM full history, `highway=track` growth) | Accessibility / prediction | ★★★★★ | Low | ✅ | API; JSON KBs |
| 5 | MAP 2020 friction surfaces + travel time (WCS subsetting, Weiss et al.) | Accessibility | ★★★★☆ | Low | ✅ | ~0.7 MB per 4°×3° tile |
| 6 | Sentinel-1 GRD via MS Planetary Computer STAC | Remote sensing (all-weather) | ★★★★★ | Med–High | ✅ | streaming COG/SAFE |
| 7 | Global Surface Water (Pekel/JRC) v1.4 2021 tiles on GCS | Hydrology / ponds | ★★★★☆ | Very low | ✅ | ~17 MB/10°×10° tile |
| 8 | USGS Mineral Operations outside US (`minfac`) — Africa facilities | Industrial truth | ★★★☆☆ | Very low | ✅ | 0.48 MB |
| 9 | GRIP4 roads — Region 3 = Africa (Zenodo) | Accessibility | ★★★★☆ | Low | ✅ | 242 MB shp / 125 MB fgdb |
| 10 | GHS-BUILT-S R2023A multitemporal 100 m | Settlement growth | ★★★★☆ | Med | ✅ | 2.0 GB global/epoch |
| 11 | MERIT-Basins offline (mghydro `delineator` data mirror) | Hydrology offline | ★★★★☆ | Med | ✅ | ~1.5 GB for Africa basins |
| 12 | global-river-runner pygeoapi (downstream tracing) | Hydrology | ★★★★☆ | Very low | ✅ | API, ~1.3 MB/trace |
| 13 | MERIT-Plus PostGIS dump (USGS ScienceBase) | Hydrology offline | ★★★☆☆ | Med | ✅ | 583 MB .sql.gz / 1.9 GB gpkg |
| 14 | Copernicus DEM GLO-30 (AWS open bucket) | Terrain | ★★★★☆ | Low | ✅ | ~43 MB/1° tile |
| 15 | ESA WorldCover 10 m v200 (S3) | Land cover baseline | ★★★☆☆ | Low | ✅ | ~55 MB/3° tile |
| 16 | Landsat C2 L2 STAC (1984+ baselines) | Long baseline | ★★★★☆ | Med–High | ✅ | streaming |
| 17 | Sentinel-2 L2A STAC (earth-search v1) | Optical (already used) | ★★★★☆ | Med | ✅ | streaming |
| 18 | Tang & Werner global mining footprint (Zenodo 6806817) | Truth data | ★★★☆☆ | Low | ✅ (⚠️ `.rar`) | 103.5 MB |
| 19 | USGS MRDS (mineral deposits/occurrences) | Prospectivity | ★★★☆☆ | Very low | ✅ | 25.8 MB |
| 20 | EMAG2 v3 magnetic anomaly (NOAA) | Geology/prospectivity | ★★★☆☆ | Low | ✅ | 88.6 MB global GeoTIFF |
| 21 | OurAirports `airports.csv` (incl. informal strips) | Accessibility | ★★★☆☆ | Very low | ✅ | 12.7 MB |
| 22 | Geofabrik country PBF (CAR, South Sudan) | OSM extraction | ★★★☆☆ | Low | ✅ | 99 MB / 138 MB |
| 23 | VIIRS monthly nighttime lights (EOG Colorado Mines) | Activity proxy | ★★★☆☆ | Med | ⚠️ | ~2 GB/month global |
| 24 | IPIS ASM site inventory (DRC/CAR/TZA/ZWE) | **Best ASM truth** | ★★★★★ | Med (form) | ⚠️/❌ | ~2,800+ sites |
| 25 | HydroSHEDS / HydroRIVERS / HydroBASINS / HydroATLAS | Hydrology attrs | ★★★★☆ | Med | ❌ from VM | 100s MB |
| 26 | USGS Geologic Map of Africa (OFR 97-470-A, `geo7_2ag`) | Geology | ★★★★☆ | Med | ❌ | ~20 MB |
| 27 | Planet NICFI PlanetScope basemaps | VHR imagery | ★★★★★ | — | ❌ RETIRED | n/a |
| 28 | OneGeology WMS | Geology | ★★☆☆☆ | — | ❌ unreachable | n/a |
| 29 | Delve (World Bank ASM database) | ASM stats | ★★☆☆☆ | High | ❌ blocked | tabular |
| 30 | Global Energy Monitor trackers | Industrial registry | ★★★☆☆ | Med (form) | ⚠️ | XLSX, MBs |

---

## 1. Mining site inventories / truth data

### 1.1 IPIS ASM site inventories (CAR + DRC) — ✅ VERIFIED — **best ASM truth data for our theatre**
- **Author/year:** International Peace Information Service (IPIS), Antwerp; curated open-data release dated 2026-05.
- **Contents:** Point inventory of *field-visited* artisanal mining sites. CAR file = **914 rows**; DRC file = **7,163 rows**.
  Columns include `longitude`,`latitude`,`visit_date`,`minerals_or`(gold),`minerals_diamant`(diamond),`chantiers_numb`
  (number of pits/workings), `workers_numb`, `workers_women`, `childunder15`, `production_destination`, plus armed-actor
  fields (`actor_type`,`actor1name`,`actor1name_tax`,`actor1name_pillage`) and state-service presence.
- **Resolution/temporal:** point (GPS), repeat visits since 2009 → 2026 release.
- **Licence:** Open Data Commons Attribution (ODC-BY 1.0) per IPIS open-data page.
- **Token:** **No** — direct CSV, no form needed for these curated files. (The older "download page" form and the
  `geo.ipisresearch.be` GeoServer WFS are *not* required; the WFS host times out from this VM.)
- **Landing page:** https://ipisresearch.be/mapping-services/open-data/
```bash
mkdir -p data/mining/ipis && cd data/mining/ipis
curl -sSL -A "Mozilla/5.0" -O https://ipisresearch.be/wp-content/uploads/2026/05/caf_mines_curated_all_opendata_p_ipis.csv   # 336 KB, 914 CAR sites
curl -sSL -A "Mozilla/5.0" -O https://ipisresearch.be/wp-content/uploads/2026/05/cod_mines_curated_all_opendata_p_ipis.csv   # 3.1 MB, 7,163 DRC sites
```
- **How it helps:** THE positive-label set. Use for (a) precision/recall of our bright-bare-pixel pit detector,
  (b) training/fine-tuning an ASM classifier, (c) empirical priors — distance-to-river, distance-to-track,
  geology class, slope — for a *predictive* ASM-risk surface transferable to Chinko and Boma.
  `workers_numb` gives a severity weight; armed-actor fields give a conflict-risk covariate.

### 1.2 Maus et al. — Global-scale mining polygons v2 — ✅ VERIFIED
- **Author/year:** Maus, da Silva, Gutschlhofer, da Rosa, Giljum, Gass, Luckeneder, Lieber, McCallum (2022), PANGAEA; v1 = 2020.
- **Contents:** 44,929 hand-digitised polygons, 101,583 km² of mining land use (open cuts, tailings dams, waste rock
  dumps, water ponds, processing infra) — **includes ASM**. Fields: `ISO3_CODE`,`COUNTRY_NAME`,`AREA` (km²),`FID`,`geom` (WGS84).
  Also per-country CSV and gridded rasters at 30 arc-sec / 5 arc-min / 30 arc-min.
- **Temporal:** mines active ~2000–2019 (v2 digitised on Sentinel-2 2019 imagery).
- **Licence:** CC-BY 4.0. **Token:** No.
```bash
curl -sSL -o global_mining_polygons_v2.gpkg \
  https://download.pangaea.de/dataset/942325/files/global_mining_polygons_v2.gpkg   # ✅ 24.7 MB
# clip to Africa / a park buffer:
ogr2ogr -f GPKG africa_mines.gpkg global_mining_polygons_v2.gpkg -spat -20 -36 55 38
```
- **Caveat:** study area was limited to a **10 km buffer around 34,820 S&P-database mine coordinates** — so remote
  ASM with no S&P record (exactly Chinko/Boma) is systematically **absent**. Excellent as positives, useless as negatives.
- v1 (2020): DOI `10.1594/PANGAEA.910894`. ⚠️ The direct v1 `.gpkg` path returned HTTP 500; use the DOI landing page.
- **How it helps:** polygon truth for industrial + medium ASM; validation of our detector's shape/area estimates.

### 1.3 Tang & Werner — Global mining footprint from high-resolution imagery — ✅ VERIFIED (⚠️ `.rar`)
- **Author/year:** Tang & Werner (2023), *Communications Earth & Environment*; data on Zenodo record **6806817**.
- **Contents:** ~74,548 mining-land-use polygons, digitised from high-res imagery; broader and more inclusive than Maus.
- **Licence:** CC-BY 4.0. **Token:** No. **Size:** 103.5 MB — single **`.rar`** (needs `unar`/`7z`, mild annoyance).
```bash
curl -sSL -o mine_polygons_tang.rar \
  "https://zenodo.org/api/records/6806817/files/Supplementary%201%EF%BC%9Amine%20area%20polygons.rar/content"
sudo apt-get install -y unar && unar mine_polygons_tang.rar
```
- **How it helps:** second independent truth layer; union with Maus increases recall of positives. The paper explicitly
  quantifies **overlap with protected areas**, so it is directly comparable to our use case.

### 1.4 USGS Mineral Operations outside the United States (`minfac`) — ✅ VERIFIED
- **Author/year:** USGS National Minerals Information Center; descends from Eros & Candelario-Quintana (2006),
  OFR 2006-1135 *Mineral Facilities of Africa and the Middle East* (>1,500 African/ME facilities).
- **Contents:** Points: mines, plants, mills, refineries. Attributes: commodity, country, site & company name,
  facility type, mining method, status, capacity + units. Coordinates rounded to 0.01° (~1 km) — **positional caveat**.
- **Licence:** US Public Domain. **Token:** No.
```bash
curl -sSL -O https://mrdata.usgs.gov/mineral-operations/minfac.zip       # ✅ 480 KB zipped shapefile
curl -sSL -O https://mrdata.usgs.gov/mineral-operations/minfac-csv.zip   # ✅ 298 KB CSV
```
- **How it helps:** industrial-mining pressure layer; upstream-of-park screening; company attribution for reports.

### 1.5 USGS Mineral Resources Data System (MRDS) — ✅ VERIFIED
- **Contents:** global mineral deposits, occurrences, prospects & past producers with commodity, deposit type,
  host rock, development status. Public domain.
```bash
curl -sSL -O https://mrdata.usgs.gov/mrds/mrds-csv.zip     # ✅ 25.8 MB
```
- **How it helps:** **prospectivity prior** — known gold/diamond occurrences are the strongest single predictor of
  where ASM appears next. Join to catchments to score "geological attractiveness" upstream of each park.

### 1.6 USGS "Compilation of Geospatial Data (GIS) for the Mineral Industries and Related Infrastructure of Africa" — 🔒 UNVERIFIED
- **Year:** 2021, USGS NMIC. Geodatabase with ~20 layers: production/processing facilities, **exploration &
  development sites**, **mineral occurrences & deposits**, undiscovered-resource tracts (potash, PGE, copper),
  coal occurrence areas, power plants/lines, pipelines, **mineral-exporting ports**, railroads, major roads.
- **Discovery:** `catalog.data.gov` → "Compilation of Geospatial Data (GIS) for the Mineral Industries … of Africa";
  also `usgs.gov/data/compilation-geospatial-data-gis-mineral-industries-and-related-infrastructure-africa`.
- **Licence:** US Public Domain. **Token:** No. I did not resolve the exact ScienceBase file URL — resolve via:
```bash
curl -s "https://www.sciencebase.gov/catalog/items?q=mineral+industries+infrastructure+Africa&format=json&fields=title,files"
```
- **How it helps:** the single richest Africa-specific mining+infrastructure bundle; exploration-licence and port
  layers are strong *predictors* of future industrial pressure.

### 1.7 OSM `landuse=quarry` / `man_made=mineshaft` extraction — ✅ VERIFIED (via Geofabrik; ⚠️ Overpass)
- **Licence:** ODbL. **Token:** No.
```bash
# Country PBFs (verified sizes)
curl -sSL -O https://download.geofabrik.de/africa/central-african-republic-latest.osm.pbf   # ✅ 99 MB
curl -sSL -O https://download.geofabrik.de/africa/south-sudan-latest.osm.pbf               # ✅ 138 MB
osmium tags-filter central-african-republic-latest.osm.pbf \
   nwr/landuse=quarry nwr/man_made=mineshaft nwr/industrial=mine -o car_quarries.osm.pbf
```
- ⚠️ The `overpass-api.de` `area["ISO3166-1"="CF"]` count query returned an OSM3S error page from this VM
  (area lookup / load). Prefer offline `osmium` on Geofabrik PBFs, or the ohsome API (§3.1) which worked reliably.
- **How it helps:** crowd-sourced quarry/mine polygons; also a *reporting* channel — our detections can be
  cross-checked against, and eventually contributed back to, OSM.

### 1.8 Amazon Mining Watch / `mining-detector` — ✅ VERIFIED — **highest-leverage single item**
- **Author/year:** Earth Genome / Pulitzer Center Rainforest Investigations Network / Amazon Conservation Association.
  **July 2026 full rebuild** (models retrained from scratch).
- **Contents (code+weights):** production detector = **ensemble of 6 CNNs** on **48×48×13 Sentinel-2 patches**
  (~800k-param `CNN800k` + one ~100k-param member), Keras `.h5`. Training set now **23,463 labelled patches**
  (2,968 mine / ~20,495 not-mine) with geographic holdouts. Scar delineation via a **fine-tuned SAM2** model.
  Reported on Venezuela holdout: specificity **0.994**, sensitivity **0.895**; combined val+holdout 0.998 / 0.924.
- **Contents (data):** yearly detections 2018–2025 + quarterly from 2025, cumulative products, and SAM2 raster
  scar masks (COGs), for the Amazon basin — patch scale 480 m × 480 m (~20 ha).
- **Licence:** **code MIT, data CC-BY 4.0.** **Token:** No.
```bash
git clone https://github.com/earthrise-media/mining-detector.git
# production ensemble weights (verified 16.6 MB):
curl -sSL -o ensemble.h5 \
  "https://github.com/earthrise-media/mining-detector/raw/main/models/48px_v4.10b-18d-20g-21a-22bc-ensemble.h5"
# product catalogue:
curl -sSL https://raw.githubusercontent.com/earthrise-media/mining-detector/main/data/outputs/MANIFEST.yaml
# bulk outputs on Source Cooperative (verified listing + README):
curl -sSL "https://data.source.coop/earthgenome/amazon-mining-watch/README.md"
curl -sS "https://data.source.coop/earthgenome/amazon-mining-watch/?list-type=2&max-keys=200"
# recommended single-period product path prefix:
#   single_periods/postprocessed_t0.43_d5_3km_t-iso0.75/   (t_main=0.43, t_iso=0.75)
# stricter/cumulative:  postprocessed_t0.55_d5_3km_t-iso0.8/
```
- **Directly relevant caveats from their README:** they could not train on the *smallest* sites without inflating
  false positives — a practical lower size limit for any Sentinel-2 system; and their known false positives are
  **sandbars, braided rivers, farm/aquaculture ponds** — precisely the confusers in savanna headwaters. Also note
  mine scars *recede* as vegetation heals, so detections both expand and shrink over time.
- **How it helps:** replaces our naive bright-pixel detector with a validated architecture. Alluvial gold ASM in
  Africa is morphologically near-identical to Amazonian *garimpo* (river-margin muddy flats + multi-coloured
  wastewater pools), so **retraining/fine-tuning on African chips labelled from IPIS points (§1.1) is the single
  highest-value engineering task in this catalogue.** Their airstrip detection work is a bonus (§3.6).

### 1.9 Delve — World Bank / Pact global ASM platform — ❌ BLOCKED from this VM
- `https://www.delvedatabase.org/data` and `/api/data` both returned **HTTP 403** (bot protection) from this VM.
- Content is largely **country-level tabular statistics** (employment estimates, State of the ASM Sector reports
  2019/2020/2023) — *not* site geometry. Low geospatial value for us.
- **Verdict:** deprioritise. IPIS (§1.1) supplies what Delve does not: coordinates.

### 1.10 Global Energy Monitor trackers — ⚠️ PARTIAL
- Global Coal Mine Tracker (**May 2026** release), Global Iron Ore Mines Tracker (Aug 2025), Global Cement & Concrete
  Tracker, Global Iron & Steel Tracker (June 2026). Includes a supplemental **coal mine boundaries** file (Feb 2026).
- Landing page `https://globalenergymonitor.org/projects/global-coal-mine-tracker/` = ✅ HTTP 200, but the XLSX is
  behind a **short registration form** (a dated direct link I tried returned HTTP 410 Gone — GEM rotates filenames
  each release, so hard-coding URLs is fragile).
- **Licence:** CC-BY-style with attribution; free. **Token:** email form.
- **How it helps:** industrial coal/iron pressure — marginal for gold/diamond ASM in CAR/South Sudan. Low priority.

### 1.11 S&P / RCS-Nodus style commercial registries — ❌ PAYWALLED
- S&P Capital IQ Pro Metals & Mining (the 34,820 mine coordinates underpinning Maus et al.) is **commercial**.
  RCS-Nodus is likewise not open. **Do not plan around these** — use Maus/Tang as the open derivative.

---

## 2. Geology & mineral prospectivity (continent-wide)

### 2.1 USGS Geologic Map of Africa — OFR 97-470-A, surficial geology `geo7_2ag` — ❌ DEAD LINK (data exists)
- **Author/year:** Persits, Ahlbrandt, Tuttle, Charpentier, Brownfield, Takahashi (1997, v2.0 2002),
  USGS OFR 97-470-A; DOI `10.3133/ofr97470A`; data DOI `10.5066/P9RGTRMC`.
- **Contents:** polygon surface geology of the whole continent (lithology + age), plus geologic provinces
  (`prv7_2ag`, DOI `10.5066/P9IFZKJW`) and oil/gas fields.
- **Licence:** US Public Domain. **Token:** No.
- **Status:** the classic path `https://certmapper.cr.usgs.gov/data/we/ofr97470a/spatial/shape/geo7_2ag.zip`
  returns **HTTP 403** (certmapper retired). `pubs.usgs.gov/of/1997/ofr-97-470/OF97-470A/` = 200 but only links the
  DOI. The ScienceBase item `60d0ff26d34e86b938aab404` = 200 but exposes **no attached data file** (only a JPEG
  thumbnail + a publication weblink). **The download is effectively broken via the documented routes.**
- **Workarounds:** (a) ScienceBase search for sibling items; (b) mirrors — Cornell
  `atlas.geo.cornell.edu/geoid/metadata/htmls/geology/afr_geol.html` documents the same coverage;
  (c) **use §2.2 Africa Surficial Lithology instead**, which is a modern reclassification of 12 geology/soil
  databases into 19–20 parent-material classes.
```bash
curl -s "https://www.sciencebase.gov/catalog/items?q=geo7_2ag&format=json&fields=title,files,webLinks"
```
- **How it helps:** lithology is a first-order ASM predictor. Greenstone belts / Birimian & Archaean basement →
  primary gold; kimberlite fields → diamond; and **alluvial cover over those units in headwaters** is the exact
  ASM niche. This is the missing "geology prospectivity" axis in our model.

### 2.2 Africa Surficial Lithology — ⚠️ PARTIAL (ArcGIS-gated)
- **Author/year:** USGS/Esri, 2019 vintage; catalogued by ISRIC (`africasis.isric.org`) and the EC Africa Knowledge Platform.
- **Contents:** Africa mapped into **19–20 parent-material classes** (bedrock geology + unconsolidated surficial
  material), compiled/reclassified from 12 digital geology, soil and lithology databases; built to explain
  vegetation/ecosystem distribution.
- **Caveat:** the ArcGIS Online item (`34ca8e39933e4725b9a68f538abaa565`) notes that **downloading requires an
  ArcGIS Online organisational subscription or Developer account**; image-export/query services are capped at
  24,000×24,000 px. So it is viewable/queryable but not cleanly bulk-downloadable → **not plain-curl friendly.**
- **Best open route:** ISRIC catalogue entry `e2e86fc2-62ba-4220-a88e-16591a5de1a5` at
  `https://africasis.isric.org/cat/collections/metadata:main/items/…` — check for an OGC API / direct asset.
- **How it helps:** laterite/regolith/alluvium classes → where placer gold concentrates; a clean categorical
  covariate for an ASM-risk model.

### 2.3 EMAG2 v3 — Earth Magnetic Anomaly Grid (2 arc-min) — ✅ VERIFIED
- **Author/year:** Meyer, Saltus, Chulliat et al., NOAA NCEI/NGDC, v3 (2017-05-30).
- **Contents:** crustal magnetic anomaly, **nT**, 2 arc-min (~3.7 km), global; sea-level and upward-continued
  (4 km) variants + an error grid and a data-source code grid.
- **Licence:** US Public Domain / open. **Token:** No.
```bash
curl -sSL -O https://www.ngdc.noaa.gov/geomag/data/EMAG2/EMAG2_V3_SeaLevel_DataTiff.tif   # ✅ 88.6 MB
curl -sSL -O https://www.ngdc.noaa.gov/geomag/data/EMAG2/EMAG2_V3_UpCont_DataTiff.tif     # upward-continued
curl -sSL -O https://www.ngdc.noaa.gov/geomag/data/EMAG2/EMAG2_V3_Error_DataTiff.tif      # uncertainty
```
- ⚠️ Note: `www.ncei.noaa.gov/pub/data/ngdc/mgg/geophysical_models/…` paths are **404** now; the working host is
  `www.ngdc.noaa.gov/geomag/data/EMAG2/`. Also `EMAG2_V3_*.csv` = 404 (GeoTIFF only).
- **How it helps:** magnetic highs/lows trace greenstone belts, BIF, and dyke/shear systems that host orogenic gold —
  a genuine geophysical prospectivity covariate at continental scale, free, 89 MB. **Caveat:** 3.7 km resolution is
  far too coarse to locate individual pits; it is a *regional* prior only.

### 2.4 WGM2012 global gravity model — ❌ BOTH LINKS DEAD
- BGI/IAG WGM2012 (Bouguer, isostatic, free-air anomalies, 2 arc-min). Both
  `https://bgi.obs-mip.fr/data-products/outils/wgm2012-global-model/` and the DTU FTP mirror returned **404** from
  this VM. Requires hunting a current BGI mirror; likely a registration step. **Low priority** — EMAG2 already
  gives us a regional geophysical prior at lower effort.

### 2.5 OneGeology WMS (incl. CAR, South Sudan coverage) — ❌ UNREACHABLE from this VM
- `onegeology.org` / `portal.onegeology.org` resolve (192.171.144.11) but **all HTTPS requests time out**
  (curl exit 60/000); `portal.onegeology.org/OnegeologyGlobal/` returns 404 with `-k`.
  `sigafrique.brgm.fr` (SIGAfrique) is **NXDOMAIN — the service is gone.**
- **Verdict:** the OneGeology/BRGM African geology web-service route is effectively unavailable. National coverage
  for CAR/South Sudan was always thin anyway. Fall back to §2.1/§2.2. If needed, contact BRGM or GTK directly for
  their Africa geology compilations (GTK has done CAR/West Africa greenstone mapping) — expect email, not curl.

### 2.6 ASTER-derived mineral maps — 🔒 UNVERIFIED / heavy
- ASTER band ratios (e.g. AlOH, FeOx, silica indices) are the standard for regolith/alteration mapping. CSIRO
  published Australia-wide ASTER mineral maps; **no equivalent open Africa-wide product is known to me.**
  Building one means processing the ASTER L1T archive — **heavy compute, weeks of GPU/CPU, not curl-able.**
- **Verdict:** out of scope for now. Sentinel-2 SWIR band ratios (B11/B12) give a cheaper approximation of
  bare/altered ground and we already have the S2 pipeline.

### 2.7 Airborne magnetics / radiometrics at survey resolution — ❌ NOT OPEN for CAR/South Sudan
- High-resolution airborne geophysics (the data that would actually predict pit-scale prospectivity) is held by
  national geological surveys and exploration companies; for CAR and South Sudan it is **not openly published**.
  Some countries (e.g. Namibia, South Africa, parts of West Africa via WAXI/BRGM) have partial releases.
- **Verdict:** flag as a known, structural data gap. Do not plan on it.

---

## 3. Accessibility (roads, tracks, settlement, travel time)

### 3.1 HeiGIT ohsome API — OSM **full history** — ✅ VERIFIED — **best predictor signal we can get cheaply**
- **Author:** HeiGIT / Heidelberg University. API version returned: **1.10.4**.
- **Temporal extent (live, from `/v1/metadata`):** **2007-10-08 → 2026-06-19**, global spatial extent.
- **Licence:** ODbL (data © OpenStreetMap contributors). **Token:** **No.**
- **Concrete verified example — `highway=track` length (metres) per year in a Chinko-area bbox:**
```bash
curl -s -X POST "https://api.ohsome.org/v1/elements/length" \
  -d "bboxes=20.5,5.0,24.0,7.5" \
  -d "filter=highway=track and type:way" \
  -d "time=2018-01-01/2026-01-01/P1Y"
```
Actual verified response (metres of `highway=track`, bbox lon 20.5–24.0, lat 5.0–7.5):

| year | track length (m) |
|------|------------------|
| 2018 | 1,175,024 |
| 2019 | 1,203,962 |
| 2020 | 2,310,695 |
| 2021 | 4,210,489 |
| 2022 | 4,984,013 |
| 2023 | 5,552,238 |
| 2024 | 5,868,598 |
| 2025 | 5,883,933 |
| 2026 | 5,869,228 |

- **CRITICAL interpretation caveat:** that 2019→2022 quadrupling is **overwhelmingly mapping effort, not new
  tracks on the ground.** OSM history conflates *reality change* with *observation change*. To use this as an ASM
  predictor you must control for mapper activity — e.g. normalise against total edits in the same bbox
  (`/v1/contributions/count`) or against a control bbox with similar mapping history but no mining.
- **Other useful endpoints:** `/v1/elements/count`, `/v1/elements/area`, `/v1/contributions/count`,
  `/v1/elements/length/groupBy/tag`. `/v1/metadata` for extent.
- ⚠️ `/v1/elements/geometry` returned **HTTP 403** for my test (bulk geometry extraction is restricted /
  rate-limited). Use aggregation endpoints, or get geometries from Geofabrik PBFs (§1.7) / `.osh.pbf` history files.
- **How it helps:** **new track growth into a roadless headwater is the classic leading indicator of ASM.**
  Aggregating per-catchment `highway=track` length change gives a per-park early-warning covariate — this is the
  cheapest genuinely *predictive* feature in the entire catalogue.

### 3.2 Malaria Atlas Project friction surfaces & travel time — ✅ VERIFIED (WCS subsetting works!)
- **Author/year:** Weiss et al. **2018** (*Nature* 553:333, travel time to cities 2015) and Weiss et al. **2020**
  (*Nature Medicine*, global maps of travel time to healthcare; friction v5.1, nominal year 2019/2020).
- **Contents:** friction = **minutes required to traverse one metre**, 30 arc-sec (~1 km), 85°N–60°S;
  motorized and walking-only variants; plus derived travel-time-to-healthcare and travel-time-to-cities rasters.
- **Licence:** CC-BY 4.0. **Token:** No.
- **The good news:** MAP runs an open **GeoServer WCS 2.0.1** that supports **bbox subsetting** — so you do NOT
  need to download global rasters. Verified available coverages:
  - `Accessibility__202001_Global_Motorized_Friction_Surface`
  - `Accessibility__202001_Global_Walking_Only_Friction_Surface`
  - `Accessibility__201501_Global_Travel_Speed_Friction_Surface`
  - `Accessibility__201501_Global_Travel_Time_to_Cities`
  - `Accessibility__202001_Global_Motorized_Travel_Time_to_Healthcare`
  - `Accessibility__202001_Global_Walking_Only_Travel_Time_To_Healthcare`
```bash
# list coverages
curl -s "https://data.malariaatlas.org/geoserver/Accessibility/ows?service=WCS&version=2.0.1&request=GetCapabilities" \
  | grep -o "<wcs:CoverageId>[^<]*"

# VERIFIED working subset: motorized friction over a CAR window (returned a valid 707 KB GeoTIFF,
# 480x360 px, EPSG:4326, 0.008333° = 30 arc-sec — exactly as expected)
curl -sS -o friction_motorized_car.tif \
 "https://data.malariaatlas.org/geoserver/Accessibility/ows?service=WCS&version=2.0.1&request=GetCoverage&coverageId=Accessibility__202001_Global_Motorized_Friction_Surface&format=image/geotiff&subset=Long(20.0,24.0)&subset=Lat(5.0,8.0)"

# walking-only, same window
curl -sS -o friction_walking_car.tif \
 "https://data.malariaatlas.org/geoserver/Accessibility/ows?service=WCS&version=2.0.1&request=GetCoverage&coverageId=Accessibility__202001_Global_Walking_Only_Friction_Surface&format=image/geotiff&subset=Long(20.0,24.0)&subset=Lat(5.0,8.0)"
```
- **Bulk alternative (travel time to cities & ports 2015)** — ✅ VERIFIED via figshare article **7638134**
  ("Travel time to cities and ports in the year 2015", CC-BY 4.0), 12 city tiles + 5 port tiles, ~400–450 MB each:
```bash
curl -s https://api.figshare.com/v2/articles/7638134 | python3 -c "
import json,sys
for f in json.load(sys.stdin)['files']: print(f['name'], round(f['size']/1e6,1),'MB', f['download_url'])"
curl -sSL -o travel_time_to_cities_1.tif https://ndownloader.figshare.com/files/14189804   # 451 MB
```
- ⚠️ Old WordPress paths like `malariaatlas.org/wp-content/uploads/2019/06/2015_friction_surface_v1.geotiff.zip`
  are **404**. Use the WCS or figshare. GEE assets also exist:
  `projects/malariaatlasproject/assets/accessibility/friction_surface/2019_v5_1` (+ `_walking_only`);
  `Oxford/MAP/friction_surface_2019` is **deprecated**.
- **How it helps:** ASM siting is an accessibility-vs-prospectivity trade-off. Compute cost-distance from
  roads/settlements to each candidate headwater → an "economic reachability" covariate. Walking-only friction
  matters specifically because artisanal miners often **walk in**, which is why pure road-distance underperforms.

### 3.3 GRIP4 — Global Roads Inventory Project v4 — ✅ VERIFIED (**Region 3 = Africa**)
- **Author/year:** Meijer, Huijbregts, Schotten & Schipper (2018), *Environ. Res. Lett.* 13:064006; GLOBIO/PBL.
  Zenodo record **6420961** ("Version 4 – 2018").
- **Contents:** ~21 million km of roads, 222 countries, harmonised from ~60 datasets (incl. OSM) via the
  UNSDI-Transportation datamodel; 5 road-type classes. Plus **road-density rasters at 5 arc-min (~8 km)**:
  total + per type 1–5. Also SSP-based future road scenarios to 2050 (the paper projects large increases in the
  **Congo basin** specifically).
- **Licence:** Zenodo metadata says **CC-BY 4.0**; GLOBIO's own page says **CC-0**; FAO's catalogue says **ODbL**.
  ⚠️ Licence statements conflict across mirrors — safest is to attribute Meijer et al. (2018) either way.
- **Token:** No. **Region coding:** 1 N.America, 2 C&S America, **3 = AFRICA**, 4 Europe, 5 Middle East & C.Asia,
  6 S&E Asia, 7 Oceania.
```bash
# Africa vector only — much smaller than the 2.3 GB global fgdb
curl -sSL -o GRIP4_Region3_africa_shp.zip \
  https://zenodo.org/api/records/6420961/files/GRIP4_Region3_vector_shp.zip/content    # ✅ 242 MB
curl -sSL -o GRIP4_Region3_africa_fgdb.zip \
  https://zenodo.org/api/records/6420961/files/GRIP4_Region3_vector_fgdb.zip/content   # ✅ 125 MB
# tiny global density rasters (5 arc-min)
curl -sSL -o GRIP4_density_total.zip \
  https://zenodo.org/api/records/6420961/files/GRIP4_density_total.zip/content         # ✅ 3.6 MB
curl -sSL -o GRIP4_density_tp5.zip \
  https://zenodo.org/api/records/6420961/files/GRIP4_density_tp5.zip/content           # ✅ 2.2 MB (local roads)
```
- **Caveat:** explicitly **"not suitable for navigation"**, and it is a **2018 static snapshot** — it cannot show
  new track growth. Pair it with ohsome (§3.1) for the temporal dimension.
- **How it helps:** stable baseline road network + density rasters as a static accessibility covariate; the SSP
  2050 projections support long-horizon "future pressure" narratives.

### 3.4 GHS-BUILT-S R2023A — multitemporal built-up surface — ✅ VERIFIED
- **Author/year:** JRC Global Human Settlement Layer, release **R2023A**, V1-0. Epochs **1975–2030 in 5-year steps**.
- **Contents:** built-up **surface area per cell, m² per cell**. Two useful grids: 100 m (Mollweide ESRI:54009)
  and 3 arc-sec (~90 m, EPSG:4326).
- **Licence:** CC-BY 4.0 (JRC open data). **Token:** No.
```bash
# 100 m Mollweide, epoch 2020 (verified 2,036,312,871 bytes ≈ 2.0 GB)
curl -sSL -O https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100/V1-0/GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100_V1_0.zip
# 3 arc-sec WGS84, epoch 2020 (verified 3,208,625,400 bytes ≈ 3.2 GB)
curl -sSL -O https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2020_GLOBE_R2023A_4326_3ss/V1-0/GHS_BUILT_S_E2020_GLOBE_R2023A_4326_3ss_V1_0.zip
# swap E2020 -> E2015 / E2010 / E2005 / E2000 ... for the time series (change BOTH occurrences)
# NOTE: R2024A / R2025A directories are 404 — R2023A is the current release at this path.
```
- **Volume warning:** a full 1990–2030 series at 100 m is ~9 epochs × 2 GB = **~18 GB**. Prefer downloading 2–3
  epochs (e.g. 2000, 2010, 2020) and clipping to Africa immediately.
- **How it helps:** **mining camps are built-up growth in the middle of nowhere.** Differencing epochs inside
  park buffers flags new settlement — a strong ASM/industrial-camp signature, and it is *independent* of both
  optical mine detection and OSM mapping bias.

### 3.5 WorldPop population — ⚠️ PARTIAL
- **Licence:** CC-BY 4.0. **Token:** No. REST API works: `https://hub.worldpop.org/rest/data/pop/wpgp1km` ✅ 200
  (returns JSON list of yearly datasets with ids).
- ⚠️ The constrained-2020 path I guessed (`…/Global_2000_2020_Constrained/2020/BSGM/CAF/caf_ppp_2020_constrained.tif`)
  was **404**; the unconstrained path `…/Global_2000_2020/2020/CAF/caf_ppp_2020.tif` returned **200**. Resolve exact
  URLs via the REST API rather than hand-constructing:
```bash
curl -s "https://hub.worldpop.org/rest/data/pop/wpgp1km" | python3 -m json.tool | head -40
curl -sSL -O https://data.worldpop.org/GIS/Population/Global_2000_2020/2020/CAF/caf_ppp_2020.tif
```
- **How it helps:** population-pressure covariate; denominator for "people within X km of a detected pit"; helps
  distinguish agricultural clearing (near settled population) from mining (often anomalously remote).

### 3.6 OurAirports — airports & informal airstrips — ✅ VERIFIED
- **Contents:** ~80k airports/airstrips/heliports globally with lat/lon, type, scheduled service, identifiers —
  including many small unpaved and closed strips.
- **Licence:** Public domain / ODbL-compatible (OurAirports dedicates data to the public domain). **Token:** No.
```bash
curl -sSL -O https://davidmegginson.github.io/ourairports-data/airports.csv   # ✅ 12.7 MB
curl -sSL -O https://davidmegginson.github.io/ourairports-data/runways.csv
```
- **Relevance:** Amazon Mining Watch's partners (NYT / Intercept Brasil) surveyed **>1,000 clandestine airstrips**
  in Brazil's Legal Amazon and found **362 within 20 km of mining activity** — airstrips are a proven mining
  proxy, and the `mining-detector` repo includes airstrip detection work.
- **How it helps:** registered-airstrip proximity is a covariate; more importantly, **unregistered** strips
  detected in imagery but absent from OurAirports are a high-value alert (gold flies out by air).

### 3.7 Navigable rivers / ports — ⚠️ PARTIAL
- Ports: the USGS Africa geodatabase (§1.6) includes **major mineral-exporting maritime ports**; MAP's
  `Accessibility__201501_…Travel_Time_to_Cities` figshare bundle (§3.2) includes **travel time to ports** rasters
  (5 tiles, ~450 MB each) — verified via figshare API.
- Navigable-river classification Africa-wide: no clean open product identified. HydroRIVERS discharge
  (`DIS_AV_CMS`) is a reasonable proxy for canoe/barge navigability. **Flag as a gap.**

---

## 4. Remote sensing beyond Sentinel-2 optical

### 4.1 Sentinel-1 SAR — ✅ VERIFIED (Planetary Computer STAC works; live search returned scenes)
- **Contents:** C-band SAR GRD (VV/VH) and RTC. All-weather, cloud-penetrating — **the key advantage in the
  African wet season when our S2 pipeline goes blind.** ~10 m, 6–12 day revisit.
- **Licence:** Copernicus open licence (free, full, open). **Token:** MS Planetary Computer allows anonymous
  STAC search; **asset download needs a (free) SAS token** via `planetarycomputer.microsoft.com/api/sas/v1/token/...`
  or the `planetary-computer` Python package.
- ⚠️ `GET` on `…/collections/sentinel-1-grd` returns **405** — use `POST /search` (which I verified works):
```bash
# VERIFIED: returned real scenes, e.g. S1C_IW_GRDH_1SDV_20250625T041456_..., assets: vv, vh, thumbnail, ...
curl -s -X POST "https://planetarycomputer.microsoft.com/api/stac/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"collections":["sentinel-1-grd"],"bbox":[23.0,5.5,23.5,6.0],
       "datetime":"2025-06-01/2025-07-01","limit":5}' | python3 -m json.tool | head -40
```
- Also available: AWS `sentinel-s1-l1c` S3 bucket ✅ 200 (**Requester-Pays** — you pay egress), and
  `earth-search.aws.element84.com/v1` exposes a `sentinel-1-grd` collection (verified in its collection list).
- **Compute warning:** coherence-loss analysis needs SLC pairs + InSAR processing (SNAP/ISCE) — **heavy**.
  A far cheaper first cut: **VV/VH backscatter time series**. Fresh pits = rough bare soil (backscatter ↑);
  water-filled pits/tailings ponds = specular (backscatter ↓↓, very dark). That contrast is highly diagnostic
  and computable from GRD alone.
- **How it helps:** wet-season continuity; direct detection of mining **ponds** (our turbidity signal's cause)
  independent of cloud; confirmation of optical detections.

### 4.2 Sentinel-2 L2A STAC — ✅ VERIFIED
```bash
curl -s "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a" | python3 -m json.tool | head -30
# collections available (verified): sentinel-2-l2a, sentinel-2-c1-l2a, sentinel-2-pre-c1-l2a,
#   sentinel-2-l1c, landsat-c2-l2, cop-dem-glo-30, cop-dem-glo-90, sentinel-1-grd, naip
```
- Copernicus Data Space STAC also live: `https://catalogue.dataspace.copernicus.eu/stac/collections` ✅ (needs a
  free CDSE account for downloads). **Licence:** Copernicus open. **How it helps:** already our backbone; the
  48×48×13-band patch layout required by the Amazon Mining Watch model (§1.8) comes straight from S2 L2A.

### 4.3 Landsat Collection 2 L2 — ✅ VERIFIED
```bash
curl -s "https://landsatlook.usgs.gov/stac-server/collections/landsat-c2l2-sr" | python3 -m json.tool | head -20
curl -s "https://earth-search.aws.element84.com/v1/collections/landsat-c2-l2" | python3 -m json.tool | head -20
```
- **Contents:** surface reflectance 30 m, **1984→present** (TM/ETM+/OLI). **Licence:** US Public Domain / free.
  **Token:** anonymous STAC search fine; AWS `usgs-landsat` bucket is **Requester-Pays**.
- **How it helps:** establishes a **pre-2015 baseline** so "new" ASM is genuinely new, not merely newly-observed.
  Essential for distinguishing decades-old workings from recent incursions — a question Sentinel-2 (2015+) cannot answer.

### 4.4 JRC Global Surface Water (Pekel et al.) — ✅ VERIFIED
- **Author/year:** Pekel, Cottam, Gorelick, Belward (2016) *Nature*; JRC GSW **v1.4, 2021 edition** (1984–2021).
- **Contents:** per-pixel 30 m layers: `occurrence` (% of time water present), `change`, `seasonality`,
  `recurrence`, `transitions`, `extent`. Tiled 10°×10°.
- **Licence:** CC-BY 4.0 / free reuse with attribution. **Token:** No.
```bash
curl -sSL -O https://storage.googleapis.com/global-surface-water/downloads2021/occurrence/occurrence_20E_10Nv1_4_2021.tif  # ✅ 17.3 MB
curl -sSL -O https://storage.googleapis.com/global-surface-water/downloads2021/change/change_20E_10Nv1_4_2021.tif          # ✅ 16.6 MB
# tile naming: {layer}_{lon}{E|W}_{lat}{N|S}v1_4_2021.tif  — Africa needs roughly 20W..50E, 40N..40S
```
- ⚠️ `monthlyRecurrence` at that path was **404** — layer names differ; monthly *history* (JRC Monthly Water
  History, 1984–2021, ~440 monthly images) is most practical in **GEE** (`JRC/GSW1_4/MonthlyHistory`) rather than
  by curl. Bulk monthly download would be very large.
- **How it helps:** **new persistent water where there was none = a mining pond.** GSW `change` and `transitions`
  give a ready-made "new water body" detector at 30 m over 37 years — arguably the cleanest, cheapest ASM signal
  after optical scars, and it directly corroborates our existing turbidity onsets.

### 4.5 Copernicus DEM GLO-30 — ✅ VERIFIED
```bash
curl -sSL -O https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/Copernicus_DSM_COG_10_N05_00_E023_00_DEM/Copernicus_DSM_COG_10_N05_00_E023_00_DEM.tif   # ✅ 42.7 MB (1° tile, Chinko area)
```
- **Contents:** 30 m DSM (COG), global. **Licence:** free/open for the 30 m product with attribution. **Token:** No
  (open S3, no requester-pays). Also on `earth-search` as `cop-dem-glo-30` / `cop-dem-glo-90`.
- **FABDEM** (Hawker et al., forest-and-building-removed 30 m DTM): landing page `data.bris.ac.uk/datasets/
  s5hqmjcdj8yo2ibzi9b4ew3sn/` ✅ 200, but the licence is **CC-BY-NC-SA (non-commercial)** — check that against your
  deployment before using.
- **How it helps:** slope/terrain covariate; alluvial-terrace and floodplain identification (where placer gold sits);
  input for our own flow accumulation if we go offline (§5).

### 4.6 ESA WorldCover 10 m — ✅ VERIFIED
```bash
curl -sSL -O https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N03E021_Map.tif  # ✅ 55 MB (3°x3° tile)
```
- **Contents:** 11-class land cover, 10 m, epochs **2020 (v100)** and **2021 (v200)**. **Licence:** CC-BY 4.0.
  **Token:** No. Tile naming: `N03E021` = 3°×3° grid, lower-left corner.
- **Dynamic World** (Brown et al., 10 m near-real-time, 9 classes) is **GEE-only**
  (`GOOGLE/DYNAMICWORLD/V1`) — no bulk download; needs a free GEE account.
- **How it helps:** baseline masking (exclude cropland/urban to cut false positives); "bare/sparse vegetation"
  class change inside a park is a cheap mining-scar screen. ESA WorldCereal is agriculture-focused → **not useful here**.

### 4.7 VIIRS nighttime lights (monthly) — ⚠️ PARTIAL
- **Author:** Earth Observation Group, Colorado School of Mines (Elvidge et al.). Monthly VCMSLCFG cloud-free
  composites, 15 arc-sec (~500 m), 2012→present.
- **Status:** directory `https://eogdata.mines.edu/nighttime_light/monthly/v10/2025/202501/vcmslcfg/` returned
  ✅ 200, and a constructed `SVDNB_npp_..._vcmslcfg_v10_*.avg_rade9h.tif.gz` URL also returned 200 — but the file
  listing did not parse cleanly, and **EOG now requires free registration for some paths**. Verify the exact
  filename by browsing the directory.
- **Licence:** free with attribution. **Token:** free EOG account for parts of the archive.
```bash
curl -s "https://eogdata.mines.edu/nighttime_light/monthly/v10/2025/202501/vcmslcfg/" | grep -o 'href="[^"]*avg_rade9h[^"]*"'
```
- **Caveat:** ~500 m and low radiance sensitivity means **ASM camps are usually invisible** — no grid power, no
  gas flares. Useful mainly for **industrial** mine/processing activity and night-lit boomtowns. Volume is heavy
  (~2 GB/month global). **Medium-low priority for ASM specifically.**

### 4.8 Planet NICFI PlanetScope basemaps — ❌ **RETIRED — do not build on this**
- The NICFI Satellite Data Program provided free <5 m PlanetScope mosaics (biannual Dec 2015–Aug 2020, then
  monthly Sep 2020–Jan 2025) for the tropics including **Africa**
  (GEE: `projects/planet-nicfi/assets/basemaps/africa`).
- **Timeline:** contract **ended 23 Jan 2025**; Level 0/1 access temporarily extended free of charge, then phased
  out from **April 2025**; **removed from Global Forest Watch and Collect Earth Online on 1 April 2025**;
  Level 2 not renewed. In **September 2025 the Norwegian government cancelled the procurement for the next phase**,
  so continuity of free high-res tropical imagery is now **highly uncertain**.
- **Replacements:** Planet's **Tropical Forest Observatory (TFO)** — same imagery, now a **paid subscription**
  (`https://api.planet.com/basemaps/v1/series/{series-id}/wmts?api_key={key}`); **Nimbo** (commercial,
  Sentinel-2-derived monthly mosaics). Neither is free/open.
- **Implication for us:** **plan on Sentinel-2 10 m + Sentinel-1 as the operational resolution.** Do not design a
  workflow that assumes 5 m imagery. This also strengthens the case for the Amazon Mining Watch model (§1.8),
  which is deliberately built for Sentinel-2 at 10 m.

### 4.9 Other ML mining detectors — ⚠️ mixed
- **`earthrise-media/mining-detector`** — ✅ open weights + MIT code. **Use this.** (§1.8)
- **MapBiomas mining** (Brazil) — annual mining class, open data, but Brazil-only and tied to Brazilian
  land-cover legend/GEE workflows; useful as methodological reference only.
- **MineSegSAT** (Sentinel-2 mining-disturbance segmentation) and various "global mining footprint from
  Sentinel-2" papers — code sometimes public, **pretrained weights usually not**, and training regions are
  Canada/Australia/China. Retraining cost is high vs. reusing Amazon Mining Watch.
- **Verdict:** one clear winner (§1.8); treat the rest as literature.

---

## 5. Hydrology for upstream/downstream reasoning

### 5.1 mghydro **Global Watersheds API** (Matt Heberger) — ✅ VERIFIED — **immediate win, zero setup**
- **Author/year:** Heberger, M. (2022–), *Global Watersheds* web app; built on **MERIT-Hydro, MERIT-Basins and
  HydroSHEDS**. Suggested citation: *Heberger, Matthew. 2022. Global Watersheds (web application).
  https://mghydro.com/watersheds*.
- **Licence:** data derived from MERIT-Hydro (**CC-BY-NC 4.0** dual-licensed with ODbL) / HydroSHEDS.
  Terms of use disclaim any warranty of correctness. **Note the NC clause** if commercial.
- **Token:** **No.**

**Endpoint 1 — upstream watershed polygon:**
```
https://mghydro.com/app/watershed_api?lat=<lat>&lng=<lng>&precision=low|high
```
```bash
# VERIFIED (Chinko-area headwater): returns GeoJSON FeatureCollection, Polygon/MultiPolygon,
# properties = {"area_km2","outlet_lat","outlet_lng"}
curl -s "https://mghydro.com/app/watershed_api?lat=6.0&lng=23.5&precision=low"   # -> area_km2 = 261
curl -s "https://mghydro.com/app/watershed_api?lat=6.0&lng=23.5&precision=high"  # -> area_km2 = 188, 14.2 KB
```
Note `low` vs `high` gave **261 vs 188 km²** for the same point — `precision=high` snaps differently and is the
one to use for real analysis; `low` is faster/coarser. Both are valid GeoJSON in EPSG:4326.

**Endpoint 2 — upstream river network:**
```
https://mghydro.com/app/upstream_rivers_api?lat=<lat>&lng=<lng>&precision=low|high
```
```bash
# VERIFIED: returns GeoJSON LineString features of all upstream reaches
curl -s "https://mghydro.com/app/upstream_rivers_api?lat=-3.5&lng=25.0&precision=low"
```
- ⚠️ **RATE LIMIT / ETIQUETTE — important.** Heberger posted (2025-11-26) that the API reached ~**4,000 requests
  per day**, causing lag and errors, and explicitly asks users to **space out requests (~5 s between calls) and
  avoid asynchronous/parallel requests**, or he may add IP-based throttling. It runs on a **$25/month shared
  host**. So: **for 163 parks, call it once per site of interest, cache aggressively to SQLite, sleep 5 s between
  calls — and for any bulk/repeated work, go offline (§5.2).** Consider donating (buymeacoffee.com/mheberger).
- **How it helps:** instant, exact answer to *"what is upstream of this park boundary / this turbidity onset?"* —
  the core spatial question for attributing river turbidity to upstream ASM. Delineate the catchment above each
  park's river inlet, then intersect with detections, GFW alerts, and mine polygons to build an upstream
  pressure score. This replaces a lot of custom flow-accumulation code with one HTTP call.

### 5.2 MERIT-Basins offline — ✅ VERIFIED (mghydro `delineator` data mirror)
- **Author/year:** **Lin, Pan, Beck, Yang, Yamazaki, Frasson, David, Durand, Pavelsky, Allen, Gleason** et al.
  (2019–2021) — MERIT-Basins / MERIT Hydro-Vector, built on **MERIT-Hydro v0.7/v1.0** (Yamazaki et al. 2019).
  Official home: `https://www.reachhydro.org/home/params/merit-basins`.
- **Contents:** unit catchments (`cat_pfaf_??_MERIT_Hydro_v07_Basins_v01.shp`) and river reaches
  (`riv_pfaf_??_MERIT_Hydro_v07_Basins_v01.shp`), organised into **61 Pfafstetter level-2 basins** (codes 11–91),
  plus a `pfaf_level_01` grouping of 9 continents/sub-continents. ~25 km² channelisation threshold.
- ⚠️ **Official distribution is Google Drive / Globus / TPDC folders — NOT curl-friendly** (needs `gdown`,
  a Globus account, or a browser). Verified folder IDs on reachhydro include
  `1J8vyqCnSdquY1cRI1PPsXzMBLBXKzzoW`, `1uCQFmdxFbjwoT9OYJxw-pXaP8q_GYH1a`,
  `1yYjAIA3QCPBshoRSsIi9YV2jjegUkDwW`, `1ypWH78DK4p3EonFoTLzW9hakQgFYhUHO`, and a Globus endpoint
  `f349971a-b759-4e38-8bc4-f6ccb057c499`.
- ✅ **The curl-friendly route:** Heberger's mirror for his `delineator` Python package —
  **`https://mghydro.com/watersheds/data/`** — serves per-basin **SQLite/spatialite `.db`** vectors and
  **merged seamless GeoTIFF** flow accumulation + flow direction rasters (he did the work of mosaicking
  MERIT-Hydro's 5° tiles into basin-wide layers). **Licence: CC-BY-NC-SA 4.0.** Checksums at
  `https://mghydro.com/watersheds/data/checksums.sha256` ✅.
- **Verified spot-checks:** `basins11.db` = **667.9 MB**, `rivers11.db` = **133.4 MB**,
  `accum11.tif` = **612.2 MB**, `flowdir18.tif` = **27.0 MB**, `rivers15.db` = **322.0 MB**, `checksums.sha256` ✅ 200.
- **Africa Pfafstetter level-2 basins are the 1x codes** (11–18) — Africa is Pfaf region 1 in this scheme.
  Sizes from the published table for 11–18:

| basin | basins.db (GB) | rivers.db (GB) | accum.tif (GB) | flowdir.tif (GB) |
|-------|------|------|------|------|
| 11 | 0.65 | 0.13 | 0.60 | 0.22 |
| 12 | 0.95 | 0.19 | 0.62 | 0.23 |
| 13 | 0.91 | 0.18 | 0.72 | 0.28 |
| 14 | 0.76 | 0.15 | 0.59 | 0.24 |
| 15 | 1.44 | 0.31 | 1.04 | 0.43 |
| 16 | 0.51 | 0.12 | 0.47 | 0.20 |
| 17 | 0.63 | 0.13 | 0.61 | 0.26 |
| 18 | 0.11 | 0.03 | 0.06 | 0.03 |

  → **vectors only for all Africa ≈ 5.96 GB basins + 1.24 GB rivers ≈ 7.2 GB**; adding accum+flowdir rasters
  roughly doubles it (~+6.6 GB). For Chinko (CAR, Congo/Ubangi drainage) and Boma (South Sudan, White Nile)
  you likely need only 2–3 basins → **~2–3 GB**, very manageable.
```bash
# pick your basin(s); example: one basin's vectors + checksum verify
cd data/hydro
curl -sSL -O https://mghydro.com/watersheds/data/checksums.sha256
for f in basins11.db rivers11.db; do curl -sSL -O "https://mghydro.com/watersheds/data/$f"; done
grep -E 'basins11.db|rivers11.db' checksums.sha256 | sha256sum -c -
# optional rasters for your own flow routing:
curl -sSL -O https://mghydro.com/watersheds/data/accum11.tif
curl -sSL -O https://mghydro.com/watersheds/data/flowdir11.tif
```
- **Tooling:** `mheberger/delineator` (Zenodo DOI 10.5281/zenodo.7314287) and `mheberger/upstream-delineator`
  (sub-basin splitting) — Python, `pip install delineator`, uses Pooch with SHA-256 verification.
  **Raw MERIT-Hydro** itself (flow dir/accum, hydrologically-adjusted elevation, channel width, 3 arc-sec) is at
  `hydro.iis.u-tokyo.ac.jp/~yamadai/MERIT_Hydro/` but ⚠️ **requires registration + licence agreement via Google
  Form** — the mghydro mirror avoids that. MERIT-Hydro is also in GEE as `MERIT/Hydro/v1_0_1`.
- **How it helps:** unlimited, offline, rate-limit-free upstream/downstream tracing at ~90 m — far finer than
  HydroRIVERS for the small headwater streams where ASM actually happens. This is the durable replacement for
  §5.1 once we exceed courtesy-API volumes.

### 5.3 Global River Runner — downstream tracing pygeoapi — ✅ VERIFIED
- **Author:** Kyle Onda / Internet of Water (Lincoln Institute, Duke Nicholas Institute) + USGS (Dave Blodgett);
  visualisation by Sam Learner. Built on **MERIT-Basins** hydrography + Natural Earth river names.
- **Endpoint:** `https://merit.internetofwater.app/processes/river-runner/execution?lat=<lat>&lng=<lng>`
- **Licence:** code open (`ksonda/global-river-runner`, pygeoapi process
  `internetofwater/pygeoapi@river-runner`); MERIT-Basins terms apply to data. **Token:** No.
```bash
# VERIFIED: HTTP 200, 1.33 MB GeoJSON; response is {"id":"path","code":"success","value":{FeatureCollection}}
# with reach properties incl. terminalpa, streamlev, hydroseq...
curl -s "https://merit.internetofwater.app/processes/river-runner/execution?lat=-3.5&lng=25.0" -o downstream.json
curl -s "https://merit.internetofwater.app/processes?f=json"    # -> processes: ['river-runner']
```
- Alternate deployments listed by the project: `https://merit-nldi.internetofwater.app`,
  `http://d1za2aav0xp6il.cloudfront.net`, `https://merit-dev-z3iqgg3uaa-uc.a.run.app`.
- **Note:** traces **downstream to the sea/terminal sink** — the complement to mghydro's upstream API.
- **How it helps:** given a detected pit or turbidity source, enumerate **every downstream reach and which
  protected areas / communities it passes** — turning a point detection into a *downstream impact* statement.
  This is exactly the "who is affected" narrative for reports.

### 5.4 MERIT-Plus PostGIS dump (self-host the river runner) — ✅ VERIFIED
- **Author/year:** Blodgett, D., Johnson, J.M., Sondheim, M., Wieczorek, M., Frazier, N. — USGS ScienceBase item
  **`614a8864d34e0df5fb97572d`**, *"Mainstem Rivers of the World based on MERIT hydrography and Natural Earth
  names"*. Method paper: Blodgett et al. 2021, *Environmental Modelling & Software* 135:104927 (Mainstems /
  HY_Features).
- **Contents:** MERIT-derived global hydrography with **mainstem** identifiers and Natural Earth river names —
  the exact database behind the Global River Runner API.
- **Licence:** US Public Domain (USGS data release). **Token:** No.
- **Verified files:**
  - `merit_plus.sql.gz` — **583,581,761 bytes (~583 MB)** — Postgres/PostGIS dump
  - `merit_plus.gpkg` — **1,934,618,624 bytes (~1.93 GB)** — GeoPackage (no Postgres needed!)
  - `merit_plus.xml` — 21 KB metadata
```bash
# discover current file URLs (tokens are embedded in the ScienceBase paths)
curl -s "https://www.sciencebase.gov/catalog/item/614a8864d34e0df5fb97572d?format=json" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for f in d.get('files',[]) or []: print(f['name'], f['size'], f['url'])"

# GeoPackage (easiest — read directly with GDAL/ogr, no DB server):
curl -sSL -o merit_plus.gpkg "https://www.sciencebase.gov/catalog/file/get/614a8864d34e0df5fb97572d?f=__disk__3b%2F9b%2F0b%2F3b9b0b5e3570f5d0cb1cf64155db899c9df94d3b"
# Postgres dump (for self-hosting the river-runner pygeoapi):
curl -sSL -o merit_plus.sql.gz "https://www.sciencebase.gov/catalog/file/get/614a8864d34e0df5fb97572d?f=__disk__00%2F2e%2Fbc%2F002ebcfce333c2c3693061f75ba6e6c636214906"
```
- ⚠️ A `HEAD` on those `file/get` URLs returned no status from this VM (ScienceBase dislikes HEAD); the JSON API
  reports both files with sizes, so use `GET` with `-o` and verify byte counts.
- **Self-hosting:** `ksonda/global-river-runner` provides Dockerfiles + pygeoapi configs
  (`internetofwater/pygeoapi` images tagged `river*` on Docker Hub); env vars `URL`, `DBHOST`, `DBUSER`,
  `DBNAME`, `DBPASSWORD`. Needs Postgres+PostGIS.
- **How it helps:** removes dependence on someone else's free API for downstream tracing; the **`.gpkg` route
  needs no database at all** and is the pragmatic choice for a Go+SQLite stack (query via GDAL/ogr or convert
  the reaches we need into our own SQLite).

### 5.5 HydroSHEDS / HydroRIVERS / HydroBASINS / HydroATLAS — ❌ **BLOCKED from this VM (Cloudflare)**
- **Author/year:** Lehner & Grill (WWF); HydroRIVERS v1.0, HydroBASINS v1.c, HydroATLAS v1.0 (BasinATLAS /
  RiverATLAS / LakeATLAS). **Licence:** free for scientific, educational and commercial use with attribution.
- **Contents:** HydroRIVERS ≈ **8.5 M reaches / 35.9 M km**, all rivers ≥10 km² catchment or ≥0.1 m³/s, with
  reach length, river order, distance from headwaters/outlet, long-term average discharge. HydroBASINS =
  **12 nested Pfafstetter levels**, ~1.0 M sub-basins at the finest level. HydroATLAS attaches **~280 hydro-
  environmental attributes** (climate, soil, geology, land cover, human footprint) to every basin/reach — this is
  the attribute-rich layer we'd want for an ASM risk model.
- **Status:** every `data.hydrosheds.org/file/...` URL I probed returned **HTTP 403 with a Cloudflare
  "Just a moment..." challenge page (~5.4 KB)**, including with a browser User-Agent and with range requests.
  Africa file paths (note the **capitalised** `HydroRIVERS` directory, unlike the lowercase in some docs):
  - `https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_af_shp.zip`
  - `https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_af.gdb.zip`
  - `https://data.hydrosheds.org/file/hydrobasins/standard/hybas_af_lev01-12_v1c.zip`
  - `https://data.hydrosheds.org/file/hydrobasins/customized_with_lakes/hybas_lake_af_lev01-12_v1c.zip`
  - `https://data.hydrosheds.org/file/HydroATLAS/BasinATLAS_Data_v10.gdb.zip` / `RiverATLAS_Data_v10.gdb.zip`
  - Tech docs (also 403): `…/file/technical-documentation/{HydroATLAS_TechDoc_v10_1,BasinATLAS_Catalog_v10,RiverATLAS_Catalog_v10}.pdf`
- **Workarounds:** (a) download in a real browser and `scp` up; (b) HydroATLAS is mirrored on **figshare**
  (search "HydroATLAS"; my keyword search didn't surface the canonical record — retry manually);
  (c) HydroSHEDS layers are in **GEE** (`WWF/HydroSHEDS/*`, `WWF/HydroATLAS/v1/Basins/level12`);
  (d) **prefer MERIT-Basins (§5.2)** which is both curl-able and higher resolution.
- **We already have HydroRIVERS** locally, so the real target here is **HydroATLAS attributes** — worth the
  manual browser download once.

### 5.6 Flow accumulation rasters — ✅ VERIFIED (via §5.2)
- `accum{basin}.tif` / `flowdir{basin}.tif` at `https://mghydro.com/watersheds/data/` — seamless per-basin
  MERIT-Hydro flow accumulation and D8 flow direction, GeoTIFF, ~90 m. Africa basins 11–18: accum ≈ 4.7 GB,
  flowdir ≈ 1.9 GB total. CC-BY-NC-SA 4.0.
- **How it helps:** lets us compute our own upstream masks, snap detections to channels, and weight turbidity
  contributions by contributing area — offline and unlimited.

### 5.7 GLOFAS — ⚠️ deprioritise
- Copernicus Emergency Management Service **GloFAS** river discharge forecasts/reanalysis are open but require a
  **CDS/ECMWF account + API key** (`cdsapi`), are ~0.05°–0.1° resolution, and are oriented to **flood
  forecasting**, not sediment or mining. Marginal value for ASM detection; would only matter if we wanted to
  normalise turbidity by discharge. **Low priority.**

---

## 6. Honest assessment of gaps and risks

1. **No ASM truth data for our two flagship sites.** IPIS covers **CAR (western prefectures, 914 sites)** and
   **eastern DRC (7,163)** — *not* Chinko specifically, and **nothing for South Sudan / Boma.** Any model trained
   on IPIS is being extrapolated geographically. Plan a manual labelling campaign on Sentinel-2 chips for
   Chinko and Boma, using the Amazon Mining Watch label schema so the data is reusable.
2. **Loss of free VHR imagery (NICFI) is a real capability regression** for visual verification. 10 m Sentinel-2
   is at the edge of detectability for small pits — Earth Genome found a practical **lower size limit** even with
   a good CNN. Expect to miss the smallest workings, and say so in outputs.
3. **The savanna-headwater false-positive problem is worse than the Amazon's.** Seasonal sandbars, braided
   channels, dry-season bare soil, and (in CAR/South Sudan) burn scars all mimic mining scars. Our existing
   "bright bare pixel" detector will be badly affected. Mitigations: require *persistence* across dates,
   require *new water* (GSW §4.4), require SAR-dark ponds (§4.1), and mask by land cover (§4.6).
4. **Continental geology is the weakest verified link.** The canonical USGS Africa geology download is broken
   (§2.1), SIGAfrique is gone, OneGeology is unreachable, Africa Surficial Lithology is ArcGIS-gated, and
   high-res airborne geophysics is not public for CAR/South Sudan. EMAG2 (89 MB, ✅) is the only cleanly
   downloadable continental geophysics — but at 3.7 km. **Prospectivity will be the least well-supported axis
   of any risk model.** Be honest about that in the UI rather than implying geological rigour.
5. **OSM temporal bias will fool a naive predictive model.** The verified 5× rise in `highway=track` length
   (§3.1) is mostly mapping effort. Always normalise by mapper activity or use a control region.
6. **Courtesy APIs are not infrastructure.** mghydro runs on a $25/month shared host and has *asked* users to
   throttle to ~5 s between requests. Cache every response in SQLite; migrate bulk work to the offline
   MERIT-Basins mirror (§5.2).
7. **Licence heterogeneity.** MERIT-Hydro/MERIT-Basins derivatives and the mghydro mirror are **CC-BY-NC-SA**;
   FABDEM is **CC-BY-NC**; GRIP4's licence is stated inconsistently (CC-0 / CC-BY / ODbL across mirrors).
   If this app is ever commercial or its outputs are relicensed, audit these before shipping.
8. **Cloudflare/bot-blocking is a live operational risk** — HydroSHEDS, Delve, `open.africa`, and (transiently)
   Zenodo all 403'd from this VM. Budget for occasional manual browser downloads.

---

## 7. Suggested build order (concrete next steps)

1. **Ingest IPIS CSVs** (§1.1) → SQLite table of 8,077 labelled ASM sites with dates, minerals, workers.
2. **Ingest Maus v2 + Tang polygons** (§1.2, §1.3) → mine-polygon truth layer; clip to Africa.
3. **Benchmark the current pit detector** against IPIS points + Maus polygons. Publish precision/recall honestly.
4. **Stand up upstream/downstream reasoning**: mghydro watershed API (§5.1) for the 163 parks, cached; then
   river-runner (§5.3) for downstream impact. Download MERIT-Basins for the 2–3 relevant African basins (§5.2).
5. **Add GSW `change`/`transitions`** (§4.4) as a "new water body" detector — cheapest large win after IPIS.
6. **Clone `mining-detector`, load the ensemble weights** (§1.8), run inference on Chinko/Boma S2 chips as-is
   to see zero-shot transfer, then fine-tune on African labels.
7. **Add accessibility covariates**: MAP friction via WCS (§3.2), GRIP4 Africa (§3.3), ohsome track growth
   normalised by mapper activity (§3.1), GHS-BUILT-S deltas (§3.4).
8. **Add Sentinel-1 VV/VH** (§4.1) for wet-season continuity and pond confirmation.
9. **Only then** attempt geology/prospectivity (§2) — expect to settle for EMAG2 + MRDS occurrences.

