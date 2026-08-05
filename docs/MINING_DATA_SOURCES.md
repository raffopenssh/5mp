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

