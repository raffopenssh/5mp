# Mining detection: diagnosis & plan (2026-08-05)

Evidence-based review of the turbidity + pit detectors, triggered by 8 manually
identified pits in the Chinko headwaters and two georeferenced legacy geological
maps (CAR 1964 1:1.5M, South Sudan).

All numbers below were measured on this VM; scripts are in `analysis/`.
Data-source catalogue: `docs/MINING_DATA_SOURCES.md`.

---

## 1. What the truth data says

**The manual sites are 123 km OUTSIDE the CAF_Chinko boundary** (24.40E 8.13N,
near Mont Abourassein) — but **inside the Chinko/Mbomou basin**, which drains
straight through the park (`analysis/out/chinko_basin.json`, 52,766 km²; 38,503 km²
of it outside the park; 72.5% of the park lies inside the basin).

Consequences:
- The pit scanner never looked there. Corridor tiles come from
  `data/osm_raw/waterways/{park}.geojson` = OSM waterways clipped to the park
  bbox. **Nearest cached waterway vertex is 48.9 km away.**
- Our whole mining pipeline is park-bbox-scoped, while mining pressure on a park
  is a *watershed* phenomenon. This is the single biggest structural gap.

## 2. The current pit detector cannot see these pits (measured)

`analysis/compare_known_pit.py`, same date (2026-03-11):

| site | red_max | ndvi_min | px > 1400 (prod threshold) |
|------|---------|----------|----------------------------|
| previously-validated big pit (7.44644N 24.02958E) | 2317 | 0.13 | 25 |
| 8 manual pits | 911–1242 | 0.24–0.37 | **0** |

`RED_MIN=1400 & NDVI_MAX=0.35` (absolute) → **recall 0/40** across 5 dates
(`analysis/eval_detector_rules.py`). The manual pits are small hand-dug shafts in
grass/woodland, 1–3 px each, not the multi-hectare bare wash-plains the detector
was tuned on. Two different phenomena; the current detector only finds regime 2.

**AUC of candidate per-pixel features** (truth pixels vs 20k local background,
`analysis/eval_auc.py`, 5 dates 2019–2026):

| feature | mean AUC |
|---------|----------|
| BSI | 0.771 |
| **red/blue ratio (iron/laterite)** | 0.758 |
| −NDVI | 0.754 |
| red | 0.727 |
| local-z variants | 0.66–0.72 |
| **median red/blue over 5 dates** | **0.806** |
| trend red 2026−2019 | 0.427 (useless) |

Cross-checked against **IPIS visited-mine truth** (40 random post-2015 gold sites,
CAR+DRC, `analysis/eval_ipis_auc.py`): red/blue **0.757**, red 0.728, −NDVI 0.718,
BSI 0.713. Independent dataset, same ranking → the ordering is real.

Takeaways:
- Add **red/blue (iron oxide) ratio** — it beats red and NDVI on both truth sets.
- **Multi-date medians beat single-date** (0.806 vs 0.758) — the persistence pass
  should become a composite, not a re-check.
- **Local-z normalisation does not help** (0.66–0.72). Absolute thresholds fail,
  but so do naive local contrasts; scene-percentile thresholds are the middle ground.
- AUC ~0.8 at 10 m is a *ranking* signal, not a detector. For 1–3 px pits, 10 m
  optical is at its limit → this is where ML on multi-band multi-date stacks
  (Amazon Mining Watch weights) or sub-10 m imagery is needed.

## 3. Current output is mostly noise (measured)

7,725 pit "sites" across 28 parks. Many files are pinned at the internal cap
(369–400 sites), i.e. truncated, not converged:

- Only **7 of 7,725 (0.1%)** are within 2 km of an IPIS visited mine.
- Of 12 IPIS mines within 20 km of a scanned CAF/COD park, 4 detected (33% recall).
- Spot-check of top-scored sites on Esri imagery: `CAF_Chinko` #1
  (6.42818N 24.23213E, score 0.95, "2898 pond px") is a **seasonal sandbank in a
  forested meander**; `CMR_Waza` #1 is **irrigated rice paddies** on the Logone.
- `new_since` dates cluster on 2025-08-10/09-06 — that's the *historical pass
  scene date*, not a real onset; scoring +0.20 for "new" is firing on cloud/
  seasonality, which is why 90%+ of sites get score ≥0.7.

The scoring function is uncalibrated: pond_px counts water anywhere in a
±150 m bbox, `persistent` is true almost everywhere, and there is no
false-positive class (sandbanks, burn scars, paddies, laterite roads, villages).

## 4. Turbidity detector: right idea, too sparse

42 alerts across 29 parks; 15 parks have zero. Root cause is the corridor problem
plus water-pixel availability: on the manual-site drainage, **0 of 261 sampled
MERIT/OSM network points classified as water (SCL=6)** in any July 2026 scene
(`analysis/downstream_turbidity_test.py`), and JRC Global Surface Water shows
**0 px with occurrence ≥25%** in the 2 km AOI. These are 1st–2nd order streams
under canopy — Sentinel-2 cannot see the water surface.

So: turbidity works only for ≥3rd-order rivers. For headwater mines it must be
replaced by *pit* detection plus *downstream-of-basin* reasoning, not by looking
for a plume next to the mine.

## 5. Geology is a genuinely predictive prior (measured, surprising)

The 1964 CAR geological map, georeferenced at 308 m/px, colour-classified for
mafic/amphibolite lenses (blue polygons, 3.9% of the Chinko basin area):

| | distance to mapped mafic lens |
|--|--|
| 8 manual pits | 0.44, 0.44, 0.44, 0.62, 0.69, 0.87 km (median **0.44**) |
| random basin points | p10 1.95 / **p50 12.02** / p90 40.4 km |

P(random point ≤ 0.44 km) = 0.053, so 8/8 is ~5e-11 by chance. Greenstone/mafic
contacts host the alluvial gold — exactly what a prospector uses.
`analysis/geology_prior.py` turns this into a 0.02° prior grid (`data/geology_prior/`).

**But** legacy scans don't scale to 163 parks. Continent-wide substitutes needed
(see `docs/MINING_DATA_SOURCES.md`): USGS Geologic Map of Africa, SIGAfrique,
EMAG2 magnetics, plus the mafic/laterite spectral signature itself.

## 6. Terrain is a cheap second prior

D8 flow accumulation on Copernicus GLO-30 (`AWS_NO_SIGN_REQUEST=YES` required)
puts all 8 pits at flow-accumulation percentile **93.9–99.6** of their 10x8 km
window — on valley-bottom drainage lines, even where OSM/GSW show no water at
all. DEM drainage should replace OSM waterways as the corridor definition.

## 7. Accessibility

`highway=track` length around the mine, ohsome API 2017→2026: **0.0 km every
year** (`analysis/out/ohsome.txt`). OSM has nothing there, so OSM-track growth is
not an accessibility proxy in CAR/SSD (it may work in Ghana/Tanzania). Use MAP
friction surfaces + DEM + Landsat-detected new linear clearings instead.

---

## Plan (ordered by measured value / effort)

1. **Rescope from park bbox → contributing basin.** Fetch each park's upstream
   watershed once (mghydro API, verified working), store as a park layer, and
   drive both scanners off it. Also store the downstream trace (river-runner API)
   so an upstream mine can be linked to the park it pollutes.
2. **Rebuild the pit detector as a ranker, not a thresholder**: multi-date
   dry-season median composite → features (red/blue, BSI, −NDVI, red) →
   scene-percentile ranking → top-N per basin. Kill the absolute RED_MIN/NDVI_MAX
   thresholds and the `new_since` scoring bonus.
3. **Corridor from DEM flow accumulation**, not OSM waterways.
4. **Calibrate against IPIS** (`data/ipis/*.csv`, 8,077 visited sites; already
   downloaded) + the 8 manual points. Build a golden-set evaluator in the style of
   `scripts/eval_fire_trajectories.py`, with a **negative** class (sandbanks,
   paddies, burn scars, villages, laterite roads) so precision is measurable.
5. **Add the geology/terrain prior** as a multiplier on the ranked score; source a
   continent-wide lithology layer to replace the two legacy scans.
6. **Evaluate the Amazon Mining Watch model** (`earthrise-media/mining-detector`,
   weights open) on our Sentinel-2 stacks — trained on exactly this phenomenon.
7. **Only then** re-enable turbidity, restricted to ≥3rd-order reaches, used as
   *confirmation* for a ranked pit cluster upstream rather than as a detector.

Do **not** ship the current `data/mining_pits/*.json` to the UI as "mining sites";
at 0.1% agreement with visited-mine truth it will destroy trust in the layer.

---

## Artefacts produced

| path | what |
|------|------|
| `data/mining_truth/chinko_headwaters_manual.{json,kml}` | the 8 manual pits |
| `data/ipis/{caf,cod}_mines_ipis.csv` | 8,077 IPIS visited ASM sites (open data) |
| `data/geology_prior/CAF_Chinko_head.json` | mafic-proximity prior grid |
| `analysis/out/chinko_basin.json` | Chinko contributing watershed (mghydro) |
| `analysis/out/{auc,ipis_auc,probe_truth,truth_timeseries}.json` | measurements |
| `analysis/eval_auc.py`, `eval_ipis_auc.py`, `eval_detector_rules.py` | evaluators |
| `analysis/geology_prior.py` | legacy-map → prior grid |
| `analysis/basemap_chip.py`, `chip_render.py` | visual QA chips (Esri / S2) |

---

## 8. ADDENDUM (2026-08-05, after steps 1–4): the spectral premise fails

Plan steps 1–4 are implemented and measured. The measurement kills the approach.

### 8.1 Spectral features do not separate mines from confusers

`scripts/eval_mining_detector.py --pixel-auc --n 25` — 25 IPIS visited gold
sites vs 25 confusers (village / burn scar / river-water / adjudicated bare
savanna), identical composite and feature code as the scanner
(`analysis/mining_features.py`), 90th-percentile in a ~440 m box:

| feature | AUC vs **confusers** | AUC vs random background (§2) |
|---------|---------------------|-------------------------------|
| rb (red/blue) | **0.450** | 0.806 |
| −NDVI | **0.555** | 0.754 |
| bsi | **0.534** | 0.771 |
| red | **0.517** | 0.517 → 0.727 |

Raw log: `analysis/out/pixel_auc_vs_confusers_20260805.log`.

All four are **at or below chance**. `rb` is *inverted* — confusers are more
ferruginous than mines (median 1.671 vs 1.588). The §2 AUCs of 0.75–0.81 measured
only "bare ≠ vegetation"; against things that are also bare, the features carry
no information. No re-weighting, percentile calibration, or threshold can rescue
this, because there is nothing to calibrate.

### 8.2 The ranker built on them behaves exactly as that predicts

Chinko headwater bbox (24.30–24.50 E, 8.05–8.22 N, 30 corridor tiles, 10 clear
dry-season dates, 177 candidates → top 100):

- recall **0/8** manual truth pits; nearest candidate 1.16 km away
- all **top 12** rejected by eye in Esri z16 imagery as dry-season bare savanna /
  grazing scald — no pit morphology, no spoil heaps, no ponds
  (`analysis/out/eval_new_top12.png`)

This is strictly better engineering than the old scanner (basin scope, DEM
corridor, multi-date median, no fake `new_since` bonus, honest ranking) and it is
still useless, which is the informative part: the losses were never in the
plumbing.

### 8.3 The truth pits are not visible in 10 m data — or in 30 cm

`analysis/out/truth_vs_top.png` (Esri z17, ~50 cm): the 8 manual pits are not
distinguishable from surrounding savanna even at basemap resolution. They were
identified from a KML, not from imagery interpretation. So we have been asking
Sentinel-2 to see 1–3 px hand-dug shafts that a human cannot see at 6× the
resolution. Two possibilities, both fatal to the current framing:

1. the pits are genuinely sub-resolution → **no optical detector can work**, and
2. the manual points may be a *known mining area* rather than 8 exact pit
   locations → the 0.4 km match radius is then wrong, but so is the 93.7–99.5
   flow-accumulation validation in §6, which used the same points.

### 8.4 Revised plan

Steps 5 (geology prior) and 7 (turbidity) are **cancelled as currently framed**:
multiplying an at-chance score by a regional 3.7 km prior produces a map of the
prior, not of mining.

What is worth doing, in order:

1. **Step 6 first, not last** — evaluate `earthrise-media/mining-detector`
   (Amazon Mining Watch, open weights) on our stacks. It is a learned model
   trained on this exact phenomenon; our hand-picked indices are demonstrably at
   chance. If a trained CNN also lands at chance on IPIS-vs-confuser, optical
   ASM detection at 10 m is closed and should be documented as closed.
2. **Fix the truth set before trusting any evaluator** — get IPIS points
   adjudicated in imagery (which of the 8,077 are visible as pits?), and settle
   what the 8 Chinko points actually are. Everything measured here inherits their
   ambiguity, §6 included.
3. **Re-frame the deliverable.** What the basin work *did* produce and what does
   validate is the **watershed layer itself** (163 parks, upstream + downstream,
   with Strahler orders) — "this park's water comes from 59,000 km² including
   these 8 000 km of upstream reaches" is defensible and useful on its own.
   Ship that; do not ship pits.
