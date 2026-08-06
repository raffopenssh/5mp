# Mining pipeline rebuild — handover (updated 2026-08-06: the Africa AUC is in)

**Read `docs/MINING_FINDINGS_2026-08.md` §9.5 first (the result: AMW CNN scores
AUC 0.781 on African mines vs confusers, but with zero sensitivity at its own
threshold — a ranker, not a detector), then §8 (why every hand-picked spectral
index is dead) and §9.1–9.2 (how the model is wired and validated).**

Mining is **not** in the UI and must not be. `data/mining_pits/*.json` (old,
7,725 "sites", 0.1% truth agreement) is untouched and still wrong; `srv/turbidity.go`
still reads it — that is pre-existing and out of scope, but do not make it worse.

## State

| step | status |
|------|--------|
| 1 basin rescope | ✅ done, 163/163 parks |
| 2 DEM flow corridor | ✅ done |
| 3 ranker not thresholder | ✅ done |
| 4 golden-set evaluator | ✅ done — and it says the index detector doesn't work |
| 5 geology prior | ❌ cancelled (§8.4) |
| 6 Amazon Mining Watch model | ✅ **measured: Africa AUC 0.781** (sanity 0.995). Signal is real but uncalibrated — see §9.5 |
| 7 turbidity | ❌ cancelled as framed |
| action 2 truth adjudication | 🟡 tooling built; 8/723 adjudicated |
| action 3 ship the basin layer | ✅ done (API + popup summary line) |

## The number that was missing — read off 2026-08-06

Both tmux runs finished (`analysis/out/amw_{sanity,africa}_20260805.{json,log}`).
Full write-up: `docs/MINING_FINDINGS_2026-08.md` **§9.5**.

* **Sanity (our pipeline vs upstream labels): AUC 0.995**, sens 0.783 / spec 1.000
  at upstream's 0.43 threshold. The reproduction is sound, so the Africa number
  is about the domain, not about us.
* **Africa (25 IPIS visited gold mines vs 25 confusers): AUC 0.781**,
  95% CI 0.635–0.899, permutation p = 0.0004. Same two sets on which every
  spectral index scored 0.450–0.555.

That is §9.4 branch 1 (AUC ≫ 0.56) — but with a caveat that changes the shape of
the deliverable:

* **Sensitivity at threshold 0.43 is 0.000.** Not one African mine scores like an
  Amazonian one. All the signal sits between 1e-5 and 1e-1; best balanced
  accuracy (0.76) is at threshold **1.2e-5**. Absolute scores are meaningless
  across the domain gap; only the *ordering* transfers.
* Honest operating figure: **TPR 0.52 at FPR 0.20**; precision@10 = 0.80 on the
  pooled ranking.
* Burn scars (1.6e-10) and bare savanna (~0) — the classes that flattened the
  indices — are what the CNN is *most* sure about. Residual confusion is
  **villages** (median 1.4e-3, max 1.2e-2): geometry/texture, not bareness.
* n=25/25, positives unadjudicated, and 10/25 negatives are the easiest class.
  The CI lower bound of 0.635 is the number to plan against.

## What was added 2026-08-05 evening

| path | what |
|------|------|
| `data/models/amw/48px_v4.10b-…-ensemble.h5` | AMW July-2026 production ensemble (16 MB, upstream commit `bbbcb2d`, MIT) + its config |
| `analysis/amw_model.py` | runs it on our own S2 stacks — no Earth Engine, no Descartes. Band order / harmonisation / cloud-mask reasoning is documented inline; **read the docstring before changing anything** |
| `scripts/eval_amw_model.py` | `--sanity` (upstream labels, tests OUR pipeline) / `--africa` (the real measurement) / `--manual` |
| `data/mining_truth/amw_labels_holdout.json` | 3,892 upstream held-out labels (val + test2 + Venezuela geo-holdout) |
| `scripts/adjudicate_truth.py` | action #2: Esri contact sheets + a keyed verdict file (`pit`/`maybe`/`no_pit`/`unclear_imagery`) → derived `visible_positives.json` |
| `srv/park_basins.go`, popup | action #3: `upstream_river_km`, `…_order3plus`, `downstream_rivers`; one plain-language watershed line in the popup |

Pipeline validation (§9.2): labelled Amazon mines score 0.916 / 0.681 / 0.356,
labelled non-mines 1.2e-3 / 1e-4 / 7e-6. A wrong band order or a missing
harmonisation offset cannot produce that, so the reproduction is sound.

## Next actions, in order

1. **Build the scanner — as a ranker, never a thresholder.** AMW ensemble over
   `flow_corridor.scan_geom()` patches, percentile-ranked *within park*, top-N
   into `analysis/chip_grid.py` for adjudication. Do **not** apply the 0.43
   threshold, do not emit counts, and nothing enters the UI until adjudicated
   precision@N is measured on real scan output. `data/mining_pits/*.json`
   (0.1% truth agreement) is what skipping that step produces.
   Cost gate first: ~5–60 s/patch, network-bound — cost out one park's
   `scan_geom()` patch count before committing to 163.
2. **Firm up the measurement before scaling.** n=25 with an unadjudicated
   positive set and a burn-scar-heavy negative set. Re-run
   `--africa` with a larger, **village-heavy** negative set (villages are the
   only class the model confuses), and do action #3 below first.
3. **Continue truth adjudication.** `scripts/adjudicate_truth.py --sheets --set
   ipis --n 48`, then record verdicts. Only 8/723 done — and those 8 (the Chinko
   manual pits) came back `unclear_imagery`: Esri has **no z18 coverage** there,
   and at z16 they are wooded savanna with pale seasonal drainage lines, no pit
   morphology. `unclear_imagery` deliberately does **not** remove them from the
   positive set; `no_pit` would. The number to watch is "of N looked at, how many
   show a pit" — it bounds how much any AUC against these positives is worth,
   including §6's flow-accumulation validation.
4. **The basin layer is shipped but under-exploited.** `GET /api/parks/{id}/basin`
   now returns upstream km² + river km (total and order-3+) and the named outlet
   river, and the popup states it in words. Obvious next uses, all independent of
   mining: upstream deforestation/fire inside the basin polygon; "which other
   parks share this basin"; flagging the 26 low-coverage divide/endorheic parks in
   the UI so a low number reads as geography, not missing data
   (`scripts/check_basin_coverage.py`).

## What exists and works (use it, don't rebuild it)

| thing | what |
|-------|------|
| `park_basins` (mig 039) | 163 parks: upstream polygon + downstream MultiLineString. `park_basin_rivers` = 64,421 reaches with Strahler order. **The one deliverable that validates.** |
| `scripts/fetch_park_basins.py` | resumable, cache-backed (`http_cache`), courtesy-serial. `--all` re-run is free. |
| `scripts/check_basin_coverage.py` | read-only QA. coverage median 0.54; <0.2 for 26 parks (divide/endorheic — real, not a bug); 13 basins >200k km² (trunk-river snap). |
| `analysis/flow_corridor.py` `scan_geom()` | scan extent = (basin clipped 200 km from park edge) ∪ park. Both tails of the coverage distribution require this. `buffer(0)` on boundaries (Kruger, Niokolo-Koba self-intersect). |
| `analysis/mining_features.py` | composites + 4 features + `Calibration`. **Measures at chance against confusers** — kept for reproducibility, not for use. |
| `analysis/mining_pits.py` | index ranker → `data/mining_candidates/` (NOT `mining_pits/`). Ablate: `--scope park|strict`, `--corridor osm`. |
| `analysis/amw_model.py` | AMW CNN on our S2 stacks. `score_point(lon, lat, windows, jitter=1)`. **Africa AUC 0.781; ignore its 0.43 threshold, rank instead (§9.5).** |
| `scripts/eval_mining_detector.py` | index-scan evaluator: precision@N / recall / precision by negative class, plus `--pixel-auc`. |
| `scripts/eval_amw_model.py` | AMW evaluator: `--sanity` / `--africa` / `--manual`. |
| `scripts/adjudicate_truth.py` | imagery adjudication of the truth sets; writes `data/mining_truth/adjudications.json` + `visible_positives.json`. |
| `scripts/build_mining_negatives.py` | `data/mining_truth/negatives.json` (186): 2 documented FPs, 12 adjudicated bare-savanna, generated village/burn_scar/water. |
| `analysis/chip_grid.py` | Esri contact sheet, 12 per image. `--json scan.json --n 12` or `--points`. |

## Gotchas

- `pip install --break-system-packages`. **TensorFlow**: the model is a Keras 2
  `.h5`, so it needs `tensorflow-cpu==2.21.0` + `tf-keras` (mismatched
  tensorflow/tensorflow-cpu versions give an `ImportError` in
  `_pywrap_tensorflow_lite_metrics_wrapper`). NumPy 1.x/2.x warnings from
  pandas/matplotlib are noise — the model itself is fine.
- AMW inference is **network-bound**, not CPU-bound: 13 bands × up to 4 dates +
  SCL per patch, ~5–60 s/point. Bands are fetched concurrently; composites cache
  to `data/amw_cache/*.npz` (gitignored, regenerable) so re-runs are ~free.
- L1C on Earth Search is `s3://sentinel-s2-l1c` = **requester-pays**. Use the free
  Google mirror (`gcp-public-data-sentinel-2`), which `amw_model.gcs_band_urls()`
  resolves via `s2:product_uri` + one cached bucket listing.
- `AWS_NO_SIGN_REQUEST=YES` is set inside the analysis modules.
- One 0.05° tile composite ≈ 20–30 s; a 30-tile bbox scan ≈ 12 min → tmux.
- `data/park_basins/*.json` and `data/flowacc/` are gitignored and regenerable.
- `pysheds` 0.5 is unusable here (calls `np.in1d`); flow_corridor has its own D8.
- Don't touch `db.sqlite3` beyond the mig-039 tables. 6.1M fire rows.
