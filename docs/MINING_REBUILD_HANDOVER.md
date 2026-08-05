# Mining pipeline rebuild — handover (updated 2026-08-05 evening, after actions 1–3)

**Read `docs/MINING_FINDINGS_2026-08.md` §8 first (the negative result that killed
the spectral approach), then §9 (the AMW learned model, now wired and
pipeline-validated).**

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
| 6 Amazon Mining Watch model | 🟡 **wired + sanity-checked; the Africa AUC is the open question** |
| 7 turbidity | ❌ cancelled as framed |
| action 2 truth adjudication | 🟡 tooling built; 8/723 adjudicated |
| action 3 ship the basin layer | ✅ done (API + popup summary line) |

## The one number still missing

```bash
# in tmux; ~40 network reads/point, composites cached under data/amw_cache/
python3 scripts/eval_amw_model.py --africa 25 --jitter --workers 4 --verbose \
    --json analysis/out/amw_africa_20260805.json
```

AMW CNN ensemble on 25 IPIS field-visited gold mines vs 25 confusers — the same
two sets on which every spectral index scored AUC 0.450–0.555. Interpretation is
pre-committed in `docs/MINING_FINDINGS_2026-08.md` §9.4:

* **AUC ≫ 0.56** → build the scanner: AMW scored over `flow_corridor.scan_geom()`
  patches, ranked, adjudicated with `analysis/chip_grid.py`.
* **AUC ≈ 0.5** → optical 10 m ASM detection in African savanna is **closed**.
  Document it, ship only the basin layer, stop. (Caveat: that is a
  domain-transfer null — Amazonian wash-plains in rainforest vs small pits in an
  already-bare savanna — not proof the phenomenon is undetectable. But without a
  labelled African training set it is the end of the cheap options.)

A run of `--sanity 60` (`analysis/out/amw_sanity_20260805.{log,json}`) and
`--africa 25` were both launched 2026-08-05 ~15:15 UTC in tmux sessions
`amwsanity` / `amwafrica`; if the logs are incomplete, just re-run — the patch
cache makes a resume cheap.

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

1. **Read off the Africa AUC** (above) and act on whichever branch of §9.4 it
   lands in. This is the decision the whole rebuild has been converging on.
2. **Continue truth adjudication.** `scripts/adjudicate_truth.py --sheets --set
   ipis --n 48`, then record verdicts. Only 8/723 done — and those 8 (the Chinko
   manual pits) came back `unclear_imagery`: Esri has **no z18 coverage** there,
   and at z16 they are wooded savanna with pale seasonal drainage lines, no pit
   morphology. `unclear_imagery` deliberately does **not** remove them from the
   positive set; `no_pit` would. The number to watch is "of N looked at, how many
   show a pit" — it bounds how much any AUC against these positives is worth,
   including §6's flow-accumulation validation.
3. **The basin layer is shipped but under-exploited.** `GET /api/parks/{id}/basin`
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
| `analysis/amw_model.py` | AMW CNN on our S2 stacks. `score_point(lon, lat, windows, jitter=1)`. |
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
