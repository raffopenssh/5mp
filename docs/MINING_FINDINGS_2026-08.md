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

### 9.5 THE RESULT (2026-08-05, both runs finished): AUC 0.781 — signal, but only as a ranker

Both tmux runs completed. Artefacts:
`analysis/out/amw_sanity_20260805.{json,log}`, `analysis/out/amw_africa_20260805.{json,log}`.

**Sanity (must pass first) — passes.** 60 upstream held-out labels vs 59 negatives,
scored through *our* S2 pipeline: **AUC 0.995**, mine median 0.779, non-mine median
0.000, specificity 1.000 and sensitivity 0.783 at upstream's own 0.43 threshold
(upstream reports 0.994/0.895 on its Venezuela geo-holdout). So the band order,
harmonisation offset, cloud masking and compositing are right, and the Africa
number below is about the *domain*, not about our reproduction.

**Africa — IPIS field-visited gold mines (25) vs the same confusers (25) that beat
every spectral index:**

| metric | value |
|---|---|
| **AUC** | **0.781** (95% CI 0.635–0.899, bootstrap n=2000) |
| permutation p | 0.0004 (5000 shuffles) |
| sensitivity @ thr 0.43 | **0.000** |
| specificity @ thr 0.43 | 1.000 |
| mine median / p90 | 0.00019 / 0.019 |
| non-mine median / p90 | 7e-9 / 0.0025 |
| best balanced accuracy | 0.76, at threshold **1.2e-5** |
| TPR @ FPR 0.20 | 0.52 |
| precision@10 (pooled ranking) | 0.80 |

Confusers by class: village n=7 median 1.4e-3 max 1.2e-2; water n=6 median 1.2e-6;
bare_savanna n=2 ~0; burn_scar n=10 ~1.6e-10. **Burn scars and bare savanna — the
two classes that destroyed our indices (§8.1) — are exactly what the CNN is most
certain about.** The residual confusion is villages, which is a texture/geometry
confusion, not a spectral-bareness one.

**How to read this — it lands in neither §9.4 branch cleanly:**

1. AUC 0.781 is far above 0.56, and p=0.0004 rules out chance. Compared with
   indices at 0.450–0.555 on the *identical* two sets, the learned model is the
   first thing in this rebuild that separates African ASM from its confusers.
2. But the **calibration is gone**: at upstream's 0.43 threshold sensitivity is
   *zero* — not one African mine scores like an Amazonian one. The whole usable
   signal lives in the range 1e-5…1e-1, five orders of magnitude below where the
   model was trained to operate. Absolute scores are meaningless here; only the
   *ordering* is.
3. So the deliverable is a **ranker, not a thresholder** — the same conclusion
   step 3 of the rebuild reached for the index detector, arrived at from the
   opposite direction. Any "N mining sites detected" count is unshippable; a
   ranked candidate list for human adjudication is not.
4. Caveats that bound this: n=25/25 (CI lower bound 0.635); the positive set is
   the unadjudicated IPIS set (handover action #2, 8/723 done, and those 8 came
   back `unclear_imagery`); and 10 of the 25 negatives are burn scars, the
   easiest possible class, which inflates AUC relative to a real scan where
   villages and bare ground dominate. Read TPR@FPR0.2 = 0.52 as the honest
   operating figure: about half the visited mines rank above the 80th percentile
   of confusers.

**Decision:** §9.4 branch 1, with the calibration caveat — build the scanner, as a
ranked-candidate producer only. Concretely: AMW ensemble over
`flow_corridor.scan_geom()` patches, percentile-ranked *within park* (never
thresholded against 0.43), top-N to `analysis/chip_grid.py` for adjudication.
Nothing enters the UI until adjudicated precision@N is measured on real scan
output — the 0.1%-consistent `data/mining_pits/*.json` is what happens when that
step is skipped.

Before scaling: n=25 is thin and the positive set is suspect. The cheapest way to
firm up both is handover action #2 (adjudicate IPIS positives) plus re-running
`--africa` with a larger, village-heavy negative set.

---

## 10. VERDICT (2026-08-06): nothing mining-related ships. Layer retired.

§9.5 found real signal (AUC 0.781, p=0.0004). This section is why that is still
not enough to build anything, and what was switched off as a result.

### 10.1 The AUC is real and the scanner is still infeasible

AUC is a *balanced* metric on a 25-vs-25 set. A scan is not balanced. Sizing it:

* AMW patch = 48 px × 10 m = **0.23 km²**.
* Median park scan extent (`flow_corridor.scan_geom()`, basin clipped to 200 km
  ∪ park) ≈ 17,700 km² → **~77,000 patches per park**. All 163 parks ≈ 60M
  patches.
* Inference is network-bound at 5–60 s/patch. One park ≈ **13 h at 8 workers**;
  the estate ≈ 10,000+ worker-hours. Composite caching does not help a scan —
  every patch is new.

And precision at the only operating points we can actually measure:

| true mine patches in a park | op point | TP | FP | precision |
|---|---|---|---|---|
| 20 | FPR 0.20 / TPR 0.52 | 10 | 15,360 | **0.0007** |
| 20 | FPR 0.04 / TPR 0.08 | 1.6 | 3,072 | **0.0005** |
| 500 | FPR 0.20 / TPR 0.52 | 260 | 15,264 | **0.017** |

To surface ~20 candidates worth a human's time we need **FPR ≤ 2.6e-4**. With 25
negatives the smallest FPR we can even *measure* is 0.04 — **154× coarser than
the requirement**. The measurement does not reach the regime the product needs,
and closing that gap needs ~10,000 labelled African negatives, which is the
labelled-training-set problem we started with.

Note the shape of this: ranking is fine, the base rate is fatal. That is the
identical failure mode as `data/mining_pits/*.json` (7,725 "sites", 0.1% truth
agreement) — a detector with respectable relative ordering, deployed against a
prevalence it was never characterised at.

### 10.2 So: is there anything we can do about mining?

Honestly:

* **Optical 10 m detection from our stack — no.** Both the hand-picked indices
  (§8, AUC 0.45–0.56) and a well-trained CNN (§9.5, 0.781 balanced / ~0.001
  precision at scale) fail, for different reasons. There is no third cheap
  optical idea. Consider this closed.
* **What would actually work** is out of our current reach, and worth naming so
  nobody re-litigates the cheap options: (a) sub-metre commercial imagery over
  small candidate AOIs, which needs a budget and an AOI source we don't have;
  (b) Sentinel-1 SAR change detection, immune to the dry-season "everything is
  bare" problem that flattened every optical feature, but a from-scratch
  project; (c) a few thousand hand-labelled African ASM chips to fine-tune AMW —
  the only route that turns 0.781 into something deployable; (d) simply
  ingesting IPIS/partner field visits as *reported* sites, which is not
  detection at all but is the only trustworthy mining data we have.
* **What we already got out of this** is the basin layer (`park_basins`,
  `park_basin_rivers`, `/api/parks/{id}/basin`, popup watershed line) — built as
  mining infrastructure, validates on its own terms, and stays.

### 10.3 What was switched off (2026-08-06)

Kill switch: `srv/mining_flag.go` → `MiningEnabled = false`, mirrored by
`MINING_ENABLED` in globe.html. Nothing was deleted — all JSON, notification
rows and settlement labels remain in the DB.

**The line drawn: the mining *inference* stays, the turbidity/pit *evidence*
goes.** A settlement classified `mining` from river proximity + deforestation
shape + fire absence + remoteness is ordinary contextual reasoning of exactly
the same kind as `fishing` or `pastoral`, and is unaffected by the spectral
negative result. What is removed is everything that came out of
`river_turbidity.py` / `mining_pits.py`.

| surface | action |
|---|---|
| Popup "Mining & Water Quality" accordion | removed (it was 100% turbidity-endpoint data) |
| Star report `### Mining & water quality` block | removed |
| Animator `turb` layer chip | dropped from `LAYER_ORDER`; remaining branches inert |
| `GET /api/parks/{id}/turbidity` | returns `{"disabled": true}` |
| `mining_alert` (4,267) + `turbidity_scan_*` notifications | filtered from the notifications API, dropdown, cron-status poll and RSS |
| Turbidity/pit terms in `scoreMining` | removed — they contributed up to **+1.0** of a 1.0-capped score, so a single spurious plume could mint a `mining` label on its own. Remaining terms are contextual. |
| "Sentinel-2 shows a sediment plume…" narrative sentence | stripped at serve time by `publicSettlementNarrative()` (28 stored rows) |
| **2,562 scanner-injected `park_settlements` rows** | excluded from every settlement query (`scannerInjectedSQLFilter`). These were never settlements — `RegisterMiningCandidate` wrote detector output into the settlements table, inflating the global count from a true 10,390 to 12,952. |
| cron `river_turbidity.py --rotate` (06:00), `mining_pits.py --rotate` (09:00) | commented out |
| About/methodology copy on turbidity + pit scanning | removed; settlement-classification copy now states the contextual basis |

`analysis/gfw_alerts.py --rotate` (04:30) **stays** — GFW integrated alerts are
genuine near-real-time canopy loss, only ever *borrowed* by the mining scorer.

Two subtleties worth keeping in mind if this is revisited:

* The narrative strip cannot use `[^.]*` to find the end of a sentence — these
  strings are full of decimals ("2.1km away", "0.11 km²") and a naive matcher
  stops mid-number, leaving debris like "…alluvial extraction.1km away (~67km of
  river turbid downstream…". See `sentenceTail` and the regression tests in
  `srv/mining_flag_test.go`.
* The injected rows are identified by the `[Pit detection …]` / `[Turbidity …]`
  prefix `RegisterMiningCandidate` prepends, not by classification — they were
  spread across *all six* classes (910 residential, 537 pastoral, 610 mining…),
  because they went through the normal classifier on insert.

If mining is revisited, start at §10.2's list — not at another index.
