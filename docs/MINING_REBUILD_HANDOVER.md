# Mining pipeline rebuild — handover (updated 2026-08-05, end of steps 1–4)

**Read `docs/MINING_FINDINGS_2026-08.md` §8 first. It is the punchline: the
spectral premise of this whole pipeline measures at chance against confusers, so
steps 5 and 7 of the original plan are cancelled and the priority order changed.**

Mining is **not** in the UI and must not be. `data/mining_pits/*.json` (old,
7,725 "sites", 0.1% truth agreement) is untouched and still wrong; `srv/turbidity.go`
still reads it — that is pre-existing and out of scope, but do not make it worse.

## State

| step | status |
|------|--------|
| 1 basin rescope | ✅ done, 163/163 parks |
| 2 DEM flow corridor | ✅ done |
| 3 ranker not thresholder | ✅ done |
| 4 golden-set evaluator | ✅ done — and it says the detector doesn't work |
| 5 geology prior | ❌ cancelled (§8.4) |
| 6 Amazon Mining Watch model | ⬅ **do this next** |
| 7 turbidity | ❌ cancelled as framed |

## The measurement that matters

```
python3 scripts/eval_mining_detector.py --pixel-auc --n 25    # ~15 min
```
25 IPIS visited gold mines vs 25 confusers, same feature code as the scanner:
rb **0.450**, bsi 0.534, −ndvi 0.555, red 0.517 — at or below chance; rb is
inverted. The §2 AUCs of 0.75–0.81 only ever measured "bare ≠ vegetation".
Log: `analysis/out/pixel_auc_vs_confusers_20260805.log`.

Scan-mode consequence on the Chinko truth bbox: recall **0/8** (nearest candidate
1.16 km), all top 12 rejected by eye as bare savanna
(`analysis/out/eval_new_top12.png`). And the truth pits are invisible at 50 cm
too (`analysis/out/truth_vs_top.png`) — see §8.3, the truth set itself is suspect.

## What exists and works (use it, don't rebuild it)

| thing | what |
|-------|------|
| `park_basins` (mig 039) | 163 parks: upstream polygon + downstream MultiLineString. `park_basin_rivers` = 64,421 reaches with Strahler order. **This is the one deliverable that validates.** |
| `scripts/fetch_park_basins.py` | resumable, cache-backed (`http_cache`), courtesy-serial. `--all` re-run is free. |
| `scripts/check_basin_coverage.py` | read-only QA. coverage median 0.54; <0.2 for 26 parks (divide/endorheic — real, not a bug); 13 basins >200k km² (trunk-river snap). |
| `analysis/flow_corridor.py` `scan_geom()` | scan extent = (basin clipped 200 km from park edge) ∪ park. Both tails of the coverage distribution require this. `basin_geom/park_geom` also exported; boundaries get `buffer(0)` (Kruger, Niokolo-Koba are self-intersecting). |
| `analysis/mining_features.py` | composites + 4 features + `Calibration` (per-feature CDF **and** score CDF). Grid pinned to first accepted scene; `proj:epsg` pre-filter; shared finite mask in `calibrate()`. |
| `analysis/mining_pits.py` | ranker. Writes `data/mining_candidates/` (NOT `mining_pits/`). Percentile cut, no RED_MIN/NDVI_MAX, no `new_since` bonus, top-N per basin, spatially even `--max-tiles` thinning. Ablate: `--scope park|strict`, `--corridor osm`. |
| `scripts/eval_mining_detector.py` | scan mode (precision@N / recall / **precision by negative class**) + `--pixel-auc`. Unmatched candidates are `unknown`, not FPs; `unknown_pct` reports blindness. Self-adjudicated negatives excluded from `neg_rate` (no grading on self-set homework). |
| `scripts/build_mining_negatives.py` | `data/mining_truth/negatives.json` (186): 2 documented FPs, 12 adjudicated bare-savanna, generated village/burn_scar/water. `confidence` field distinguishes "certainly a village" from "certainly no mine". |
| `analysis/chip_grid.py` | Esri contact sheet, 12 candidates per image. `--json scan.json --n 12` or `--points`. Adjudication tool. |

## Next actions, in order

1. **Evaluate `earthrise-media/mining-detector`** (open weights, trained on ASM in
   the Amazon) on our Sentinel-2 stacks, scored with
   `scripts/eval_mining_detector.py`. Our indices are at chance; a learned model
   is the only remaining candidate. If it is also at chance on IPIS-vs-confuser,
   **document optical 10 m ASM detection as closed** and stop.
2. **Adjudicate the truth sets** — which IPIS points are visible as pits in Esri
   imagery (`analysis/chip_grid.py`)? What are the 8 Chinko points really: exact
   pits or a mining *area*? §6's 93.7–99.5 flow-accumulation validation rests on
   the same points, so it inherits the doubt.
3. **Ship the basin layer** as its own feature (API + pins already exist:
   `GET /api/parks/{id}/basin`, `features?type=basin_upstream|basin_downstream|
   basin_rivers&min_order=N`, buttons in the Roads/Rivers/Places popup section).
   Defensible, useful, independent of mining.

## Gotchas

- `pip install --break-system-packages`; `AWS_NO_SIGN_REQUEST=YES` is set inside
  both analysis modules; NumPy 1.x/2.x pandas import warnings are noise.
- One 0.05° tile composite ≈ 20–30 s. A 30-tile bbox scan ≈ 12 min → run scans in
  `tmux`, not in a tool call.
- `data/park_basins/*.json` is gitignored: `park_basins` is the source of truth
  and every mghydro body is in `http_cache`, so the 138 MB dump is regenerable.
- `data/flowacc/` (382 MB) likewise: regenerate via `analysis/flow_corridor.py`.
- `pysheds` 0.5 is unusable here (calls `np.in1d`); flow_corridor has its own D8.
- Don't touch `db.sqlite3` beyond the mig-039 tables. 6.1M fire rows.
