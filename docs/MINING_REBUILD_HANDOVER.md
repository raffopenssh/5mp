# Mining pipeline rebuild — handover (2026-08-05)

Executing the Plan in `docs/MINING_FINDINGS_2026-08.md` §Plan. Steps 1–2 done
and validated, step 3 half-done, steps 4–5 not started.

**Mining pits are still NOT surfaced in the UI, and must not be until step 4
reports defensible precision.** The old `data/mining_pits/*.json` (7,725 sites,
0.1% agreement with visited-mine truth) is untouched and still wrong.

## Done

### 1. Basin rescoping ✅ (commits `934b348`, `9141492`)
- migration `039-park-basins.sql`: `park_basins` (upstream polygon + downstream
  MultiLineString), `park_basin_rivers` (Strahler order per reach), `http_cache`.
- `scripts/fetch_park_basins.py` — outlets = HydroRIVERS vertices within 4 km of
  the boundary, ranked by `ord_flow` then GLO-90 elevation, thinned to 25 km,
  max 3/park; union of their watersheds; nested outlets skipped.
- Uses **`https://mghydro.com/app/getwshed`** (not the two endpoints in
  `MINING_DATA_SOURCES.md` §5.1): returns watershed + upstream rivers *with
  stream order* + snapped outlet in ONE call. Response is always gzip and
  urllib won't decompress it. Silently drops to low precision >50,000 km².
  Serial, 5 s sleep, every body cached in `http_cache`.
- API: `GET /api/parks/{id}/basin`, `features?type=basin_upstream|
  basin_downstream|basin_rivers&min_order=N`. Pin buttons in the
  Roads/Rivers/Places popup section.
- CAF_Chinko: 59,254 km² upstream (2 outlets), 8,014 km downstream,
  417 reaches (373 at order ≥3). **8/8 truth pits inside the basin**; 88% of
  the park is inside it.

**⚠️ UNFINISHED:** `tmux new-session -d -s basins "python3 scripts/fetch_park_basins.py --all --sleep 5 > logs/park_basins.log 2>&1"`
was running at handover (~6/163 done, ~30–40 min total). Re-run it; it is
resumable and cache-backed, so it costs nothing to repeat.

### 2. Corridor from terrain ✅ (commit after `9141492`)
- `analysis/flow_corridor.py` — own D8 on Copernicus GLO-30, cached as
  percentile GeoTIFFs in `data/flowacc/` (gitignored, 382 MB, regenerable).
  pysheds 0.5 is unusable (calls `np.in1d`, needs a CRS viewfinder), so: skimage
  reconstruction-by-erosion fill → EPS·distance flat resolution → vectorised
  steepest descent → one numba pass in descending-elevation order.
- `load_corridor()` in `analysis/mining_pits.py` now scans the **basin**, from
  flow accumulation. `--corridor osm` (`_load_corridor_osm`) keeps the old path
  for A/B.
- Validation (`python3 analysis/flow_corridor.py --validate`): truth pits at
  window percentile **93.7–99.5** (median 97.4), matching the doc's 93.9–99.6;
  7/8 above the 94 cut. Nearest OSM waterway: **48.8–49.0 km**.
- `SNAP_PX=2` (~60 m) is load-bearing — a pit is dug *beside* its channel.
  Measured: snap 0 px → 20–98 pct (median 67, useless); 1 px → 69–99.6;
  2 px → 94.2–99.7; 8 px → all 99.6+ (stops discriminating).

### 3. Ranker, not thresholder — HALF DONE
`analysis/mining_features.py` exists and is tested standalone: dry-season median
composite, the 4 measured features (rb/bsi/−ndvi/red, weights = AUC−0.5), and a
`Calibration` class doing scene-percentile scoring from a basin+season sample.
No local-z (measured 0.66–0.72 vs 0.76–0.81).

**TODO:** rewrite `scan()` in `analysis/mining_pits.py` to use it:
- replace `clusters_in_tile()`'s `(red > RED_MIN) & (ndvi < NDVI_MAX)` with
  "score ≥ Nth scene percentile" from `Calibration.score()`; delete `RED_MIN`,
  `NDVI_MAX`.
- delete the `new_since` **+0.20** scoring bonus — it fires on the historical-pass
  scene date (dates cluster on 2025-08-10/09-06), which is why 90%+ of sites
  score ≥0.7. Keep the history pass as *reported metadata*, not score.
- output top-N per basin instead of `kept[:400]` + a cap that made 369–400-site
  files truncated rather than converged.

## Not started

### 4. Golden-set evaluator (`scripts/eval_mining_detector.py`)
Style it on `scripts/eval_fire_trajectories.py` (`--baseline`/`--candidate`/
`--snapshot`). Classes:
- **positives**: `data/ipis/{caf,cod}_mines_ipis.csv` (8,077 field-visited; filter
  `visit_date >= 2015` and gold, as `analysis/eval_ipis_auc.py` does) +
  `data/mining_truth/chinko_headwaters_manual.json` (8).
- **negatives — must be built by hand**, this is the whole point: the known FPs
  are `6.42818N 24.23213E` (sandbank in a forested meander, old score 0.95),
  `CMR_Waza 11.42193N 15.03891E` (irrigated rice paddies), plus burn scars,
  villages, laterite roads. Store as `data/mining_truth/negatives.json` with a
  `class` field per point.
- Report precision@N, recall, and precision by negative class. Building blocks:
  `analysis/eval_ipis_auc.py` (AUC harness), `analysis/basemap_chip.py`
  (Esri z16–17 chips with `--marks`, for eyeballing candidates).
- IPIS positional accuracy is village/pit-cluster level → match within ~1–2 km,
  and note IPIS covers western CAR + eastern DRC only, *not* Chinko or Boma.

### 5. Geology/terrain prior as a score multiplier
`analysis/geology_prior.py` does this for two legacy scans only. Continent-wide
replacement: expect to settle for **EMAG2 v3** (88.6 MB GeoTIFF, ✅ verified,
`MINING_DATA_SOURCES.md` §2.3) + **MRDS occurrences** (25.8 MB, §1.5). Say so in
the UI — EMAG2 is 3.7 km, a *regional* prior only, and per §6.4 prospectivity is
the least well-supported axis of the model. USGS Africa geology is a dead link,
SIGAfrique is NXDOMAIN, OneGeology unreachable, Africa Surficial Lithology
ArcGIS-gated.

## Environment notes
- `AWS_NO_SIGN_REQUEST=YES` needed for Copernicus DEM (set inside both modules).
- `pip install --break-system-packages` on this VM; numba 0.64 present; the
  NumPy-1.x-vs-2.x import warnings from pandas/bottleneck are noise.
- Nothing heavy was written to `db.sqlite3` beyond the three new tables.
- `make build && sudo systemctl restart 5mp` after Go changes (version in footer).
