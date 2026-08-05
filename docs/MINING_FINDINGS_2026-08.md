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
## 9. ADDENDUM (2026-08-05, evening): the AMW learned model, wired and validated

Handover action #1 ("evaluate `earthrise-media/mining-detector` before declaring
optical ASM detection closed") is now **executable**. The plumbing exists and is
verified; the Africa measurement is the next thing to read off it.

### 9.1 What was built

| path | what |
|------|------|
| `data/models/amw/48px_v4.10b-…-ensemble.h5` (16 MB, in git) | the July-2026 production ensemble from upstream commit `bbbcb2d`, + its `config-t0.43.txt` and MIT LICENSE |
| `analysis/amw_model.py` | runs that model on **our** Sentinel-2 stacks (no Earth Engine, no Descartes) |
| `scripts/eval_amw_model.py` | `--sanity` / `--africa` / `--manual` scoring modes |
| `data/mining_truth/amw_labels_holdout.json` | 3,892 upstream held-out labels (val + test2 + Venezuela geo-holdout), extracted from their `collected_locations2026-05-04` |

The input pipeline is a deliberate reproduction of upstream's, documented
inline in `analysis/amw_model.py`. The parts that are easy to get silently wrong:

- **13 bands in GEE `S2L1C` order**: `B1 B2 B3 B4 B5 B6 B7 B8A B8 B9 B10 B11 B12`
  — note **B8A before B8**, and B10 (cirrus) present. Wrong order → plausible
  garbage, no error.
- **Harmonisation**: upstream reads `COPERNICUS/S2_HARMONIZED` and divides by
  10,000. Baseline ≥ 04.00 scenes need the −1000 DN shift; we apply it per scene
  from `s2:processing_baseline`. (Earth Search says the same as
  `scale 1e-4, offset -0.1`.)
- **Imagery host**: Earth Search's L1C assets are `s3://sentinel-s2-l1c`
  = requester-pays = unusable unsigned. The identical granule is free on Google's
  `gcp-public-data-sentinel-2`; we resolve L1C → GCS JP2s via `s2:product_uri`
  plus one cached bucket listing. L2A (for SCL) stays on AWS unsigned.
- **Cloud mask**: Cloud Score+ (`cs_cdf ≥ 0.6`) is GEE-only, so we mask with the
  SCL of the *same granule's* L2A twin before the median. Both are per-pixel
  cloud/shadow rejections ahead of a multi-date median.

### 9.2 Sanity check: our pipeline reproduces upstream's behaviour

Before any Africa number is meaningful, the model must behave on its own
held-out labels when fed by *our* code. First 6 points (3 mine / 3 non-mine,
each scored in its own label window):

| | scores |
|--|--|
| labelled mines | 0.916, 0.681, 0.356 |
| labelled non-mines | 0.0012, 0.0001, 0.000007 |

AUC 1.000, specificity 1.000, sensitivity 0.667 at the published `t=0.43`.
Three points per class is not a measurement, but it is decisive as a *wiring*
check: a wrong band order or a missing harmonisation offset does not produce
0.9 on mines and 1e-5 on forest. Larger run: `analysis/out/amw_sanity_*.json`
(60+60, `--sanity 60`).

### 9.3 Cost, and how to run it

~40 HTTP range reads per patch (13 bands × up to 4 dates + SCL), bands fetched
concurrently: **5–60 s per point**, network-bound. Composites are cached in
`data/amw_cache/*.npz` (gitignored, regenerable), so re-runs at different
thresholds are nearly free.

```bash
# 1. does OUR pipeline reproduce upstream? (must pass first)
python3 scripts/eval_amw_model.py --sanity 60 --workers 6 --verbose \
    --json analysis/out/amw_sanity.json

# 2. THE measurement: IPIS visited mines vs the same confusers that
#    beat our indices (§8.1: AUC 0.450-0.555)
python3 scripts/eval_amw_model.py --africa 25 --jitter --workers 6 --verbose \
    --json analysis/out/amw_africa.json

# 3. probe the 8 Chinko pits (read with §8.3's doubt about their identity)
python3 scripts/eval_amw_model.py --manual --jitter
```

Run these in `tmux`, not in a tool call.

### 9.4 How to read the pending result

- **AUC ≫ 0.56 on IPIS-vs-confuser** → the learned model carries signal our
  indices do not, and the pipeline becomes: AMW ensemble scored over
  `flow_corridor.scan_geom()` patches, ranked, adjudicated with
  `analysis/chip_grid.py`. That is a real detector, not an index.
- **AUC ≈ 0.5, or confusers scoring like mines** → optical 10 m ASM detection in
  African savanna is **closed**, and §8.4's step 1 is answered: document it,
  ship only the basin layer, and stop. Note in that case what a null means: the
  model was trained on Amazonian wash-plains in rainforest, and CAR/DRC ASM is
  smaller pits in a landscape that is *already* seasonally bare — the same
  "everything is bare" problem that flattened our indices. A domain-transfer
  failure is not proof that the phenomenon is undetectable, but with no labelled
  African training set it is the end of the cheap options.

Either way, handover action #2 (adjudicate the truth sets) still gates the
confidence of the answer: an at-chance result against a suspect positive set is
weaker evidence than the number looks.
