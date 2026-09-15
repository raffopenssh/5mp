# Fire pipeline

_Split out of AGENTS.md. Read when working on this area._

## ⚠️ Fire data source: SQLite only

`fire_detections` (42.9M rows, 3 sensors) is the one and only fire source.
Read it via `scripts/fire_source.py` (`load_park_fires(park, min_date)`).

The old `data/raw-fire-viirs-*/{park}.json` was a **rolling ~6-month window**
masquerading as an archive (CAF_Chinko: 18k fires in JSON vs 425k in the DB), so
a full non-incremental rebuild silently discarded years of trajectories. It was
deleted 2026-08-05 along with its two nightly writers and the `--source json`
flag; don't reintroduce a second copy of the detections.

**Never tune the fire algorithm by eye** — use
`scripts/eval_fire_trajectories.py` (6-park golden set;
`--snapshot`/`--baseline`/`--candidate`) **and** `scripts/eval_fire_null.py`
(real vs day-shuffled; the A/B harness cannot tell linking from over-linking —
see "Link evidence" below). The builder's ablation flags
(`--no-hungarian --no-mass-penalty --no-overpass`) reproduce the old v6 output
bit-exactly; verify that before trusting any delta.
See `docs/FIRE_PIPELINE.md` § v7.

Per-overpass slicing (`--overpass`) is implemented but **off, permanently**.
All three VIIRS sensors share one sun-synchronous ~13:30 orbit plane, so
ingesting three of them tripled the density of each pass without adding passes
(1.71 slices/day). Re-tested 2026-08-06 on the frozen DB: every gate regresses
(`fires_per_grp` −10.6%, `mean_days` −22.4%, `dup_pairs` +16%). Only a
different orbit plane or a geostationary source could change this —
`docs/FIRE_PIPELINE.md` § "Per-overpass slicing".

**NRT→SP reconciliation is a measured no-op** — don't rebuild it. FIRMS' SP
reprocessing returns coordinates, FRP and confidence *byte-identical* to the
NRT rows we already have; only `acq_time` moves 1–2 min, which day-level
clustering cannot see but which *would* fork the `UNIQUE(lat, lon, acq_date,
acq_time, satellite)` key. Six NRT-provenance windows, `data/eval/nrt_sp/`,
`docs/FIRE_PIPELINE.md` § NRT→SP. What ships is a watchdog: `daily_fire_update`
step 2e on the 1st of each month runs `scripts/reconcile_nrt_sp.py --watchdog`
(read-only, ~40s) → `data/nrt_sp_audit.json`; exit 4 = FIRMS changed, recorded
as `nrt_sp_drift` in the pipeline heartbeat + a SYSTEM notification. Only then
use `--apply --yes` (matcher-based UPDATE, never a blind INSERT), and rerun
`build_fire_grid_agg.py --since` + the v5 rebuild for affected parks.
Beware three ways to measure this wrong: SNPP/N21 history is SP-sourced (a
tautology — the script checks provenance via id ordering), exact
`(date, acq_time)` bucketing discards ~85% of true pairs, and raw bbox add/drop
rates just reflect our own ingest-scope history.

All three VIIRS sensors are ingested (NOAA-20 + SNPP + NOAA-21, ~3x the
detections). Satellite codes `N`/`N20`/`N21` are part of the
`fire_detections` UNIQUE key — never default that field.

**All FIRMS downloads go through `scripts/firms_api.py`.** Two API facts that
both produce *silent* zero-row ingests if you hand-roll a URL:
* the area endpoint caps a request at **5 days** (a 10-day URL 400s);
* NRT-vs-SP is **not** a function of age. NOAA21 has no SP product at all;
  SNPP and NOAA20 cut over on different dates. Asking the wrong side of a real
  cutover returns HTTP 200 with a header-only CSV. `pick_source()` reads
  `/api/data_availability` and returns `None` when nothing covers the date —
  callers must skip, not retry. See `docs/PLAN_AOI_OVERLAY.md` §2.

Use `--parks a,b,c` (one process) rather than repeated `--park` calls.

`data/fire_groups_v5/` and `data/fire_trends_v5/` are **gitignored derived
output** (762 MB, 711k groups) — regenerate, don't commit.

### A park with zero groups is a real state, not a no-op

Rainforest/desert parks can rebuild to 0 groups, or to groups that all sit
>20 km outside the boundary (past the narrative cutoff). Both writers must
handle it explicitly or the old rows become immortal:

- `load_fire_groups_to_db.py` deletes `feature_geometries` rows **before**
  the empty-input early return.
- `precompute_narratives_v5.py` writes an **empty v5 cache row** for such
  parks. Do *not* delete the row instead: a cache miss drops
  `HandleAPIFireNarrative` into the deprecated Go slow path (17s,
  `feature_id: null`) — the exact Single-Writer-Rule failure.

---

## Link evidence (v8, 2026-09-14): a trajectory carries its own score

**The finding.** On XSA 2024 (830k detections, 485k km²) the v7 tracker gave
the *same* output on real data as on data whose calendar days were randomly
permuted within each month (spatial field and seasonal march kept, every
day-to-day continuity destroyed): 9,500 vs 10,468 groups, median 16 days on
both, **more** ≥150 km fronts on the shuffled data (2,912 vs 3,675), and
straighter ones. In a dense field there is always a cluster inside the 13 km
gate, and the heading gate then *selects* a straight continuation out of
noise. Every A/B metric in `eval_fire_trajectories.py` rewards linking; none
asked whether the links beat chance. Invariant 15 (constants calibrated at
park scale) and invariant 12 (a grade without its score) in one.

**What does separate a real link from a coincidence is contiguity, not speed.**
A front advances from its own edge and a herder's ignition chain is a string
of adjacent burns, so the nearest detection pair between yesterday's and
today's cluster abuts (<0.25 km) ~6× more often in real data than in shuffled
data, and is >10 km apart *less* often — the same ratio curve on dense XSA,
Chinko and Serengeti grassland. Speed is deliberately **not** a feature:
herders walk 800 km one way and hunters cover 80 km in a day; every gate that
capped distance or ended contested tracks (`AMBIGUITY_RATIO`, `CONTIGUITY_KM`,
`DENSITY_GATE_K`) cut real ≥150 km fronts from 112 to 6 on the test box.
They are kept as ablation switches, all 0.

**What ships.** Linking is **bit-identical to v7** (verified on
TZA_Serengeti + CMR_Nki; no id or name migration). Each link gets
`log2 LR(min_pair_km / gap_days)` from `data/fire_link_lr.json`, written by
`scripts/calibrate_fire_link_lr.py` (real vs 2 shuffles × 3 regimes; the file
records its histograms and the builder's hash). Each group emits:

* `evidence_bits` — the sum over its links (unit: bits, log2 real/shuffled);
* `evidence_tier` — `supported` (≥6 bits), `weak` (≥2), `unsupported`,
  `single` (one slice, nothing to judge), `unmeasured` (no LR table);
* `link_margin` — mean assignment margin, 1 = every link uncontested.

Tiers come from the null: on the XSA box the shuffle produced 2 groups at
≥6 bits where the real data produced 220 (~1 % by chance) carrying 74 % of all
multi-day detections; ≥2 bits ≈ 18 %; below 0 bits a coin toss. Real
unsupported groups are many and small (207 groups, 9.9k detections).

**All tiers are drawn.** Hiding `unsupported` (migration 066, `fireDrawnSQL`,
grey `evColor`) was tried and **reverted the same day**: the shuffled null
keeps every corridor and only scrambles the order the days were visited, so
"shuffled data draws the same line" means the line's *shape* does not depend
on day order — it says nothing about whether the corridor is real (they recur
every season). A herder chain is *expected* to score low on contiguity: scouts
lay fire ahead, the burns do not touch. The tip words it as *day order
confirmed / unconfirmed*, never *not real* (`fireEvidenceLine` in globe.html).

**Measure it with `scripts/eval_fire_null.py`** (real vs shuffled on one
area/window, `--bbox` for a 10 s iteration; `skill = 1 − null/real`). Read
`long_ev_ge6` (supported long fronts: real vs null) and *real long fronts
retained* together; a change that raises skill by deleting herder chains is a
regression. Re-run `calibrate_fire_link_lr.py` after any change to
`daily_clusters`/`build_tracks` — the LR describes the links *as the tracker
selects them*.

**Herd model × vanguard (measured 2026-09-15, `scripts/eval_herd_vanguard.py`;
report unchanged).** `plan_solver.movement()` fits on all ≥150 km transhumance
fronts and must stay that way: fitting on `supported` only (759 of 8,224 fit
fronts) widens every band to the 80 % isopleth, mean per-bundle skill
+0.47 → +0.37, network capture 0.79 *below* the all-fire null 0.95;
`supported+weak` +0.43. Lead-weighting the UD (+0.48) and fitting on
pre-front fronts only (+0.45) are within noise of the current +0.47. Tier is
day-order evidence, not route evidence — 184 of the 205 held-out vanguard
chains are `unsupported`. What vanguard *does* add is independent validation:
the current bands, fitted on 2023–24, capture **64 %** of the 2025/26 vanguard
chains per bundle vs 19 % for an equal-area all-fire band (ordinary fronts:
0.57), and 80 % of vanguard chains head within 90° of their bundle's day-order
heading (50 % = chance; bundle 9 at 58 % with R 0.03 has no direction to
trust). Per bundle only 7–32 vanguard chains a season, so report these as a
second skill column, not a fit. `movement()`'s assert trips until `build`
reruns (state.pkl 13,178 fronts vs 13,180 in the v8 file).

---

## Season front & vanguard (shipped 2026-09-14)

Prototype history and the ten order-sensitive tests that came out real ≈
shuffled inside the season: `scripts/fire_vanguard/README.md`. What shipped:

**Definition** (`scripts/fire_front.py`, table `fire_season_front`, migration
066). Per area (park or AOI) and season: 2.5 km cells; *burnable* = cells that
burned in any season held; the **front** at a cell is the day-of-season when
20 % of the burnable cells within ±60 km have burned — **causal** (known the
day it happens, so archive and this morning's detections meet the same rule),
normalised-convolution smoothed σ=3 cells. Season starts in the area's quietest
month (climatology trough), measured, never assumed; a season whose data begins
> 15 d after its start is skipped (XSA 2023/24 was an artefact: data starts
2024-01-01). **usual** = median front of previous complete seasons; live, where
this season's front has not arrived, `lead_basis:'usual'` stands in — and the
front follows the time slider (`?at=`), there is no season picker. **Lead** of
a detection = front − its day. A group's `lead_start` is its first vertex;
**vanguard** = `10 ≤ lead_start ≤ 60` (`VANGUARD_LEAD_DAYS/_MAX`).

**Measured, and the gate** (`scripts/eval_fire_vanguard.py`: production tracker
on detections with lead ≥ L, real vs `shuffle_days`). XSA 2024/25 and 2025/26
against the *stored* front: link skill **+0.46/+0.50/+0.51** (L=5/10/15) and
**+0.49/+0.49/+0.49**; long-chain skill 0.7–1.0; whole field ≈0. Bands at
equal density (60k detections each): +0.58 (lead −10…0), +0.55, +0.44, +0.31,
+0.16 (> 80 d in) — lead is a continuous confidence axis, which is why a chain
is drawn bright while ahead and faint after. Beyond 60 d ahead: a few hundred
detections, skill ≈0.1, and a January fire 193 d ahead of a July front is a
wet-season fire, not a scout — hence the cap. **Do not read the COUNT of
vanguard groups after shuffling the whole field as skill**: shuffling moves
detections onto pre-front days and the null gets *more* (749 real vs 1,117).

Probes from other domains, same harness, XSA 2024/25: **Hawkes/ETAS
declustering** (`fire_vanguard/hawkes.py`, background = no fire within 10 km
in the previous 3 d) — no skill in season (−0.07) and 74 % of vanguard
detections are not "background" (scouts light chains); the front position is
the separator, not independence. **Eikonal / first-arrival**
(`fire_vanguard/eikonal.py`): the front's gradient gives a season-speed map
(XSA median 4.6 km/d, p10 1.9, p90 14.7 — a good descriptive product, not
drawn yet); as a live *predictor* Dijkstra travel time from the early arrivals
loses badly to last season's front (MAE 59 vs 7.8 d at day 45–60). The
speed map is now drawn (below). **Shift-calibrated `usual` was measured and
declined** (`scripts/eval_usual_shift.py`, 39 complete park seasons, front
truncated at day 45/60/90 and `usual` shifted by the median or quantile
offset seen so far): MAE none **14.5/16.0/15.7 d** vs median-shift
74/37/30 vs quantile-shift 38.5/26.2/20.1. The single-area XSA 6.7-vs-7.8
number did not generalise — early arrivals are a biased sample of the season
(Kafue 2024: −173 d at day 45 against −25 d at the end). Live `usual` stays
unshifted; do not re-add a shift without beating those numbers. Also untried:
DTW of this season's front against usual per corridor ("12 days early
here"), moveHMM transit/encamped on vanguard chains.

**Plumbing.** One writer of the front: `fire_front.py`. `--current` runs in
`daily_fire_update.py` before the rebuild (parks) and `--area` in
`aoi_runner.run_fire_v5` before the AOI rebuild; `rebuild_fire_trajectories_v5`
calls `fire_front.tag_groups` at the end (annotation only — linking untouched);
`load_fire_groups_to_db.py` writes `lead_start`/`vanguard` columns + the lead
fields in `properties_json` (`fire_season, lead_start, lead_basis, lead_max,
leads[], ahead_km, ahead_days, vanguard`). Backfill/catch-up: cron 04:40
`fire_front.py --rotate 25` (oldest first, all seasons, re-tags the group JSON
and patches `feature_geometries` in place, batched; reports `fire_front_success/
_failed` to the bell). First full pass ran 2026-09-14 (162 areas, ~8 s each).
Index `idx_fg_vanguard` (partial, `vanguard=1`) makes the map question
indexed.

**Read side.** `GET /api/fire-season?area=|lon=&lat=[&at=YYYY-MM-DD]` →
contours (GeoJSON features, `dos/date/label/text`), seasons, stats,
`vanguard_groups`; AOI ids answered only if visible (404 otherwise); no
area → `status`. `GET /api/fire-vanguard?bbox=&from=&to=` → animator wire
format + `leads[]`, all chains (no spread collector — the population is
~1 % and the point is to draw all of it; `truncated` still reported).
`/api/fire-anim-trajectories` carries `vanguard`/`lead_start`.

**Read side, additions (2026-09-14 pm).** `/api/fire-season` also takes
`summary=1` (no contour JSON — up to 300 KB; the tips and the stats row use
it), `from`/`to` → `vanguard_in_window` (the panel's basis; `vanguard_groups`
is the whole season), and with `at=` reads the packed int16 grids
(`frontProgress`) → `front_reached_pct` (share of front-bearing cells reached
by `at`) and `usual_offset_days` (median front − usual over those cells, ≥ 20
cells; + = later than usual; Chinko 2024/25 was −21 d, checked against numpy).
`/api/stats` carries `vanguard_groups` under the fire row's own
scope/window/bbox; `/api/features-in-bbox?type=fire_trajectory` carries
`vanguard_total` (over the whole set in view, like `total`);
`/api/fire-anim-trajectories` carries `leads[]`/`lead_basis`/`ahead_km` for
vanguard groups only.

**UI** (`srv/static/fireseason.js`; Map-strip chip in `maplegend.js`, body =
configure, × = off, sub-label = state: `front 63 %` / `front not yet` /
`front complete`, "not yet computed" vs "no area here"): share param
`season=front,vanguard`. **One fire palette, roles told apart by line style
first** so greyscale survives: trajectories solid red with a head; the front
thin **dashed** isochrones, ember red (early) → pale rose (late), never a
cool hue (a blue line beside fire read as a second data family); vanguard
solid, wider, haloed, **yellow (10 d) → white (60 d)** by `lead_start`, chain
split client-side at lead 0 into `ahead` (bright) + `after` (faint) because
MapLibre cannot colour one LineString per vertex; glyphs must be `Noto Sans
Regular` (the demotiles font server 404s anything else and the whole source
then draws nothing).

**No row of its own.** The front and the vanguard are two more renderings of
the fire subject, so they live on the **Fire Activity row**: its rendering
menu (`openModeMenu` 'fires') has a *Season* group (front / vanguard
checkboxes, refused with the reason where no front is built), its readout
line prints `front 60 % · 26 d early · 149 vanguard` (`seasonFrontWords`,
one writer shared with the chip menu and the region tips), the pill says
`front · vanguard`, and the eye becomes a dashed-contour glyph
(`.layer-season`) when only the overlay draws. A sixth "Season Front" row was
built first and removed: it said "Season Front" over a count of fire chains
and cost a phone a third row of cards. **Fire pins** carry a `.chip-van`
mark (`87` in lead yellow) — the vanguard share of the pin's chains, and
the switch for that rendering. **Park/AOI hover tips** carry a season line
(`seasonTipLine`, `FireSeason.summary()` cache per area+window, tip
re-renders once when it lands; says nothing where no front is built).

**Animator.** The front animates through data-driven paint on its own
MapLibre layers (`FireSeason.animAt(t)`, ≤ ~12 repaints/s with a trailing
update so the last scrub position lands): contours the season has not reached
are filtered out; those reached in the last ~6 d are the **wave** — a wide
blurred stroke (`fireseason-front-wave`) plus a heavy crisp line, its date
labelled even on an unlabelled 5-day contour; older lines thin and dim with
age. Vanguard chains are drawn by `anim.js drawVanguard` after the field:
ignition ring opening over the first 4 d, lead-coloured run with halo,
pulsing lead-coloured head while ahead, **ordinary fire red from the vertex
where `leads[i] < 0`** (the season caught up), same ash-out as every
trajectory. The paused-frame tip says "Still N d ahead" / "The season caught
up N d ago". Fire tip: `fireSeasonLine()`; fire_alert notifications append
"began N d ahead of the season front — early movement ahead of the season"
and rank the group at priority 20. Methods block: "Season Front & Vanguard
Fires". Test: `?test=1` → `TEST.fireSeason()`; API: `tests/api_tests.sh`
"Season front & vanguard".

**Exports** (`gpkgFormatVersion` v4). `fire_trajectories` (area and view)
carry `fire_season, lead_start_days, lead_basis, vanguard, ahead_km,
ahead_days`. New layer **`fire_season_front`**: area exports hold every
season overlapping the window; view exports (animator → GeoPackage adds
`season` to `layers` while the front is on) hold one season per area in
view, the one the instant falls in, with `reached_at_instant`. Styled dashed
ember red, labelled by date (`styleFireSeasonFront`), temporal on
`front_date`.

**Vanguard rendering (2026-09-14, graded 2026-09-15).** `fireseason.js splitChain`
emits one feature per **segment**, colour = `leadColor(mean of the two
vertex leads)` on the ramp 0 d orange `#fb923c` → 15 d yellow `#fde047` →
≥ 40 d white; `part:'after'` (lead < 0) is ash grey. Three more things the
original XSA render (`/tmp/fx/render3.py`) said and the app now says too:

* **Width = certainty of the day order**, continuous. `evidenceMul(tier,
  bits)` grades `evidence_bits` (log2 LR vs the day-shuffled null) linearly
  ×1 at ≤ 0 bits → ×1.5 at 6 (`supported`); bits absent → tier word
  (supported ×1.5, weak ×1.3, else ×1); `unmeasured` never widens. The
  segment carries it pre-computed as `wf`; `tierWidth()` multiplies it
  inside each zoom stop (MapLibre refuses `zoom` under `*`).
  `widthMulExpr('eb','ev')` is the same rule as an expression for
  `lodlayer.js widthExpr` on plain trajectories, and `anim.js drawVanguard`
  calls `evidenceMul` — one rule, three drawers. Wires: `/api/fire-vanguard`
  and `/api/fire-anim-trajectories` send `bits`; `/api/features-in-bbox`
  sends `eb` beside `ev` (both via a whitespace-tolerant scan —
  `afterJSONKey` — because the stored JSON is Python's `"key": value`; the
  first `ev` extractor assumed `":"` and had matched **no row**, so LOD
  trajectories were never widened until 2026-09-15).
* **Opacity = presence**: `leadAlpha(L)` 0.55 at 0 d ahead → 0.95 at ≥ 10 d
  (`alpha` on the segment; `line-opacity` reads it). A chain fades as the
  season closes on it.
* **Dashed = a day not seen**: segment `gap` (days between its two vertices)
  > 1 draws on `fireseason-van-gap` (`line-dasharray [3,1.4]`; dasharray is
  not data-driven, so it is a filter split with `fireseason-van`). The
  animator uses `setLineDash` per segment.

`FireSeason.legendHTML()` samples the ramp and says the width/dash rule in
park-manager words (wider = surer of the day order · thin = unsure · dashed
= a day not seen · season caught up). Tips say `evidenceWords()`:
"**Fairly sure** of the day order along this chain, so it is drawn wider
(weak evidence, 2.8 bits)". `FireSeason.lift()` moves the three vanguard
layers to the top; `lodlayer.js restack()` calls it, so the chains that
carry information sit above the pinned trajectories. Tests:
`vanguard_wire_bits`, `bbox_fire_eb_beside_ev`, `anim_trajs_vanguard_bits`;
`TEST.fireSeason()` → `drawnGapDashed`, `vanguardWidthFactors`,
`vanguardAlphas`. No start/end dots (asked for, then declined).

**Why every tier was `unmeasured`:** `data/fire_groups_v5/*.json` predated
the v8 evidence fields — the nightly `--incremental` carries old groups
forward unscored (`rebuild_fire_trajectories_v5.py` only scores what it
re-forms). Fix is a **full rebuild**: `rebuild_fire_trajectories_v5.py &&
load_fire_groups_to_db.py --force && precompute_narratives_v5.py` (started
17:08 in tmux `firev8`, `logs/fire_v8_rebuild_YYYYMMDD.log`, ~2 min/park,
hours). Check: `SELECT COUNT(*) FROM feature_geometries WHERE
feature_type='fire_trajectory' AND properties_json LIKE '%evidence_tier%'`.
Until it lands, `vanguard_tiers` says `unmeasured: N` and the report prints
"their day order has not yet been scored".

**Report side.** `/api/fire-season?…&summary=1&leads=N` adds `vanguard_top`
(ranked by `ahead_km`, each with `tier`, `nearest_place`, start lon/lat,
narrative), `vanguard_tiers` (histogram over **all** vanguard chains in the
window, not the shortlist) and `words` — the server's one-sentence summary
(`seasonWords`, every number derived). The ★ report (`buildParkMarkdown`)
fetches it (`fetchFireSeasonDirect`, parks and AOIs) → a "Fire season"
overview row (`seasonFrontWords`), a vanguard mark on each group line, and a
`#### Season front & vanguard fires` sub-section (words, ranked table, tier
histogram, method line); an unbuilt front prints "not yet computed", not
nothing. Fire narratives (`precompute_narratives_v5.py`, `FireGroupStory`)
now carry `lead_start/lead_basis/vanguard/ahead_km/ahead_days`. Fire-row
rendering menu has a **Map** group (the row's own layer as a checkbox) above
Season. `db.sqlite3.bak` deleted (+22 GB).

**Playhead readout (2026-09-14).** `/api/fire-season` always sends
`front_curve` (per-day `front_reached_pct`, small), and `fireseason.js`
`curveAt()/playheadMeta(t)` re-derive the chip / fire-row / tip readout at
the animator's instant (`FireSeason.meta()` returns `animMeta || front`).
`usual_offset_days` is deliberately `null` at the playhead — a curve
quantile would be a second estimator under the one word (invariant 7).

**Season speed map (2026-09-14).** `GET /api/fire-season-speed?area=|lon=&lat=
[&at=|&season=]` (`srv/fire_season_speed.go`) → the eikonal gradient of the
stored front as a **PNG data URL** on a fixed log ramp 1–50 km/d
(`legend` 5 stops, areas comparable), σ=2 normalised-convolution smoothing,
central differences; `stats{cells, p10/median/p90_km_d}` (Chinko 2024/25:
18,770 cells, median 5.1 — matches numpy exactly), `grid{x0,y0,res,nx,ny}`
(row 0 = south; PNG written top-down). The 256-entry `palette` is unique per
level (blue-LSB nudge) so the image is **invertible**: the client decodes it
once and a click-only MapTip backdrop probe (`fireseason-speed-probe`,
priority −10) prints the km/d under the pointer — no second grid payload
(XSA would be ~233 KB). Index by `grid.x0/y0/res` arithmetic, not bbox
scaling (last-bit drift shifted a row). UI: fire-row Season menu → *Season
speed*, chip `speed N km/d` (`icon-gauge`), share `season=front,vanguard,speed`,
`TEST.fireSeason().speed/speedStats`. Not a GeoPackage layer. Tests:
`fire_season_speed_*` (api), `season_front_vanguard_speed` (ui).

**Covering index `(feature_type, park_id, vanguard)` measured unnecessary:**
`fire_season.go` 0.00 s (`idx_fg_park_type_date`), stats 0.07 s, export GROUP
BY 0.57 s via partial `idx_fg_vanguard`. The `/api/export/parks` CSV's 30 s
was the `fire_detections` GROUP BY — now `parkFireCountMemo` in
`srv/export.go`, keyed on `MAX(rowid)` (append-only table → O(1)
fingerprint), stale-while-revalidate; warm 0.45 s.

**AOI fire catch-up (2026-09-14).** XSA fires stopped 2026-08-06 because
`fire_v5` stayed `done` with a stale cursor. `aoi_runner.py daily()` now
calls `catch_up_fires()` first (`FIRE_CATCHUP_DAYS = 7`): live open-ended
AOIs whose fire_v5/fire_gap ran > 7 d ago **and** have newer detections in
their bbox get fire_v5 requeued (cursor NULL, units_done 0). `HandleAPIAOIRefresh`
resets cursor/units_done for `aoiDerivedDatasetsSQL` (clip, fire_v5,
deforestation, basin) — before, refresh only re-ran narratives. The XSA run
(~1 h 50 m) is due after the v8 rebuild finishes (do not run both: lock/CPU).

**Rebuild landed 2026-09-14 20:06** (`EXIT=0`, 38.9M fires → 752,312
groups, 157 park narratives): Chinko 9,362/9,362 trajectories carry
`evidence_tier`, `fire-narrative` rows carry `vanguard/lead_start/lead_basis/
ahead_km/ahead_days` + `evidence_tier`, the map draws
supported/weak/unsupported/single (no `unmeasured`). The rebuild re-formed
groups, so vanguard counts moved (Chinko 2024/25 window 86 → 90 chains) —
`tests/api_tests.sh fire_season_report_leads` now derives the count it looks
for in `words` from `vanguard_in_window` (invariant 2). Two surfaces, two
bases: the Map-strip chip counts chains **in view** (`/api/fire-vanguard`
bbox, says "in view"), the fire row / tips count the area's window
(`vanguard_in_window`). XSA fire_v5 catch-up requeued via `catch_up_fires`
and run in tmux `xsafire` (`logs/aoi_xsa_fire_20260914.log`, ~1 h 50 m).
A `rebuild_fire_trajectories_v5.py --aoi` alone does **not** refresh an
AOI's fires: it reads `build_aoi_fires.py` output (step 0 of `run_fire_v5`),
so run the runner, not the script. XSA caught up 20:45 (38,789 trajectories
to 2026-09-07, all tiered). **That exposed a no-op reading as an answer:**
a season *in progress* bears a front only where it has already arrived, so
`front_reached_pct` measured against its own cells read **100 %** on its
latest day (XSA 2026/27, six weeks in) and `words` said "half the area by
2026-08-28". `frontProgress`/`frontCurve` now take `complete`; an incomplete
season's denominator is the union of its front cells and the cells the
**usual** front reaches (XSA 2026/27 → 0.8 %), and `seasonWords` speaks
"half"/"the last of it" only for a complete season. Test
`fire_season_in_progress_not_100pct` (owner-gated). The early-season
`usual_offset_days` (−39 d at 1 %) is the selection bias measured above;
it prints beside its pct, which is the reader's warning.

## Kalman seed-ahead chains — the vanguard layer's population (shipped 2026-09-15)

**What and why, in plain words.** The plain tracker runs over the whole
field, so a chain that began ahead of the season was cut the moment the
season's own fires arrived around it (43 % of vanguard chain ends, measured;
`scripts/fire_vanguard/README.md` round 2). `scripts/fire_vanguard_kf.py`
tracks only the sparse field from 10 d after the front backwards
(`FIELD_LEAD_MIN = −10`), lets a track be **born only 10–60 d ahead** of the
front (the vanguard window), and **follows it with a Kalman filter** (state
x, y, vx, vy; Mahalanobis gate χ² < 9.21; σv0 5 km/d, q 1.5, R floor 1.5 km,
lost at σ 30 km) into the arriving season. Gap budget is the production 3 d;
cloud coasting and "dead layers" were measured and **rejected** (README:
beyond two missed days the next sighting is someone else's fire) — a missed
day is drawn dashed, never bridged. Everything after linking is production
code untouched (SPRT, `chain_tracks`, `track_to_group`, evidence bits,
`fire_front.tag_group`).

**Measured** (`eval_fire_vanguard.py --tracker kf`, XSA 2024/25, field fixed
by real leads and its days shuffled — the prototype's null): links 7,959 vs
5,663 (**+0.29**), ≥150 km fronts 154 vs 74 (+0.52), fires in long chains
11,494 vs 4,456 (**+0.61**), median chain 74 km vs 44. Reproduces the
prototype (0.29 / 0.60). XSA 2024/25: 847 chains in 9 s; a park season ~1–3 s.
Shuffling the *whole season* instead is not a test of linking: in-season
fires land on pre-front days and the null simply gets a bigger field (1,380
groups vs 848) — the harness fixes the field first.

**Storage.** `feature_geometries` rows `feature_type='fire_vanguard'`,
`vanguard=1` (so `idx_fg_vanguard` covers them), ids `kf_<area>_<year>_grp_…`,
`properties_json` = a trajectory's props + `tracker:'kf'`, `tracker_version`,
`seed_lead`, `end_cause` (`ongoing` = last seen within 3 d of the area's
newest data · `season` = the front had arrived at the end · `lost` = still
ahead, no fire within reach for 3 d), `heading_deg`/`speed_kmd` (the filter
state at the end), `kf_hits`. Table `fire_vanguard_kf` (migration 067): one
row per area that has been tracked. `data/fire_vanguard_kf/{area}.json` holds
the group dicts. An AOI delete removes both (`aoi_write.go`).

**One population per area (invariant 7).** `srv/fire_season.go
vanguardRowsSQL` is *the* definition of "a vanguard chain" for every surface
— `/api/fire-vanguard`, `/api/fire-season` (`vanguard_groups`,
`vanguard_in_window`, `vanguard_top`, `vanguard_tiers`), `/api/stats
vanguard_groups`, the parks CSV: KF chains where `fire_vanguard_kf` lists the
area, the plain groups flagged `vanguard=1` elsewhere, never both. Every
answer names it: `/api/fire-season` → `vanguard_tracker: 'kf'|'groups'`;
`/api/fire-vanguard` → `trackers: {kf: n, groups: n}` and `tracker` per
chain. The plain trajectory's own `vanguard`/`lead_start` **properties**
stay (a fact about that line: tips, ▲ in KML/Locus, GeoPackage columns) —
they are no longer what the layer or a count means.

**UI — the same rendering, no new control.** The Vanguard rendering
(`fireseason.js`) draws whatever `/api/fire-vanguard` returns: lead colour,
evidence width, dash for a missed day, ash after the season caught up — as
before. **Direction** (2026-09-15): fires have dates, so a chain has an
order, and from z7 every chain carries small arrowheads along it
(`fireseason-van-arrow`, `symbol-placement: line`) in the segment's own
colour (lead ramp ahead, ash after). The glyph is one **SDF** image,
`arrow-right` (`globe.html makeArrowheadSDF`, points north at rotate 0 as
the old bitmap did), so every arrow layer — LOD fire trajectories
(`lodlayer.js`), pinned trajectories, vanguard — colours it with its own
line colour and rims it with a dark halo; the old bitmap was a fixed red
arrow on every colour of line. New for KF chains: a **live head** on
`ongoing` chains — a *large arrowhead* at the chain's end turned to
`heading_deg` (falls back to the last step's bearing) over a soft lead
glow (`fireseason-van-head` symbol + `-halo` circle). It was a ringed dot
with a geometry chevron until 2026-09-15; with settlement and deforestation
dots on at the same time a dot read as one more point feature, and an arrow
at the end of a line can only be the line's head. The chip says `12
vanguard in view · 1 still moving`; the tip says *Still moving — last seen
11 Sept heading S at ~7 km/d* / *Followed until the season arrived* /
*Trail lost: no fire within reach for 3 days*, with "Kalman-tracked" as a
small secondary note and one method sentence at the foot. Legend gains the
arrow line, the head line and a one-line method when KF chains are in view
(`kfShown()`); the Methods page and the Season menu item say the same words.
**Animator:** the `trajs` loader also fetches `/api/fire-vanguard` for the
view and window; those chains (`_van`) are drawn by `drawVanguard` while the
overlay is on (direction there is motion — no arrows), plain trajectories
with the same id step aside (`_hideWhenVan`), and KF chains are not drawn at
all with the overlay off — the map does not draw them either. Paused-frame
probe skips whichever is hidden. `TEST.fireSeason()` → `trackers`,
`kfShown`, `drawnLiveHeads`.

**Exports carry the population (2026-09-15).** One reader,
`srv/fire_vanguard_export.go vanguardChains` (vanguardRowsSQL through
`idx_fg_vanguard`, `tracker` named per row, `truncated` when a cap cut it),
feeds: the park and merged **KML** (`writeVanguardKML`: a "Fire vanguard"
folder, one subfolder per year, newest visible, the folder description
counts how they ended and states its basis — *every chain overlapping the
window*, as the map draws, where the season summary / ★ report count chains
that *began* in it, so 149 vs 148 is two bases, not a bug); **Locus** (a
`FIRES <year> · VANGUARD` folder per year, the end words in the track name);
the **GeoPackage** `fire_vanguard` layer (area + view export; columns
`tracker`, `seed_lead_days`, `end_cause`, `end_words`, `heading_deg`,
`speed_kmd`, `kf_hits`; QML categorised by `end_cause`; format key `v5`).
`fire_trajectories.vanguard` still means "plain chain that began ahead" and
the layer description says the vanguard layer is what the app counts. The
**parks CSV** adds `vanguard_tracker` / `vanguard_moving`; its per-park
fire count memo now persists in `server_memo` (migration 068, fingerprint =
`MAX(rowid)` of `fire_detections`) and is warmed at startup, so a cold
request is 2.8 s, not 30 s. `fireSeasonWords` says "ran N km *ahead of the
season*" (lost) / "*so far*" (ongoing) instead of "before the season caught
up" for a chain the season never caught. The ★ report table prints
`end_cause`/heading (MD/PDF/XLSX/CSV). Tests `kml_fire_vanguard_folder`,
`parks_csv_vanguard_tracker`.

**Cron / pipeline.** `daily_fire_update.py` runs `fire_vanguard_kf.py
--areas … --current` right after `fire_front.py --current` (live season of
the affected parks, nightly 03:00); `aoi_runner.run_fire_v5` has it as its
last step (all seasons of the AOI — appended last so a mid-run cursor still
resumes); cron 04:50 `fire_vanguard_kf.py --rotate 25 --quiet` redoes the
oldest / missing / older-`tracker_version` areas (bell:
`fire_vanguard_kf_success/_failed`). An area without a front is **skipped and
counted as such**, not written as zero chains (invariant 1). First full
pass `--all` ran 2026-09-15 (tmux `kfall`, `logs/fire_vanguard_kf_all_20260915.log`).
Tests: `vanguard_kf_one_population`, `fire_season_vanguard_tracker_kf`,
`vanguard_kf_no_ongoing_in_past`.

**Not a measurement.** "Cattle follow 1–2 weeks behind" in the tip is
field knowledge from the user, not a measurement, and is worded as such.
`scripts/eval_herd_vanguard.py` now reads the KF set (runs F/G: a
vanguard-only fit) and has **not been re-run** since — a separate task.

**Can the KF improve the plain trajectories? Measured 2026-09-15: no.**
With the full pass done (162 areas, 8–9 seasons each, 5 areas honestly at
0 chains — rainforest/desert), the open question was whether the Kalman
tracker beats v7 *inside* the season. `eval_fire_vanguard.py --tracker
kf_all` (KF over the whole field, seeds anywhere) vs `--tracker groups
--leads all`, 2 shuffles: CAF_Chinko 2024/25 links +0.02 vs +0.03,
long fronts −0.47 vs −0.35, fires in long −0.22 vs −0.23; TZA_Serengeti
2024/25 links +0.13 vs +0.17, long fronts −0.21 vs −0.83. Same null-level
skill, same shape: the Mahalanobis gate is no more selective than the
13 km + heading gate when every gate contains a patch. The seed-ahead rule,
not the filter, is what carries the vanguard skill, and merging KF chains
into the plain population would break "one population per area". Plain
trajectories stay v7/v8; the Methods page now states this as a tested
result ("cannot be read — by anyone") rather than a caveat.

## `protected_area_id` is a catchment, not a park (F10 — fixed 2026-08-13)

`park_assigner.ASSIGN_MAX_DIST_KM = 100`, so `WHERE protected_area_id = ?`
selects every detection whose *nearest* park boundary is X and is within
100 km. Until 2026-08-13 that was the whole predicate behind **every**
user-facing "fires in park X" count — a median **9.8× overstatement**, and for
seven rainforest parks a count made entirely of somebody else's savanna.
`CMR_Nki` is on the test-park list *as the pristine one* and `/stats` credited
it with 2,518 fires.

Two edits closed it, and they are separable on purpose:

1. **The queries name containment.** Eleven sites now append
   `srv/fire_containment.go`'s `fireInsideSQL` — `AND +in_protected_area = 1`
   — to their `protected_area_id = ?` predicate: `park_stats_handlers.go`
   (stats total, timeline, per-year, fire-log), `export.go` (`fire_count` CSV),
   `api.go` (`/api/parks/export`), `fire_trend.go`, `narrative_handlers.go`
   (Go-fallback total, peak month, hotspots, `analyzeFireTrend`'s year join),
   `fire_narrative_cache.go` (total + peak month), and
   `fire_realtime_handlers.go` (the 28-day alert window). **The `+` is
   load-bearing**: without it the planner takes `idx_fire_infraction`
   `(in_protected_area, acq_date)` and a 0.2 s park lookup becomes an 18 s scan
   of 8M rows (Kafue: 14.2 s). `tests/db_tests.sh` asserts the plan still names
   `idx_fire_pa_date` — same family as invariant 3.
2. **The flag was re-derived.** It was a stored ingest-time answer and 5.83%
   of it was wrong (433,632 rows from the bbox+0.5° `_find_park` that
   `ParkAssigner` replaced in `858eb69`). `scripts/rederive_fire_containment.py`
   recomputed point-in-polygon against `data/keystones_with_boundaries.json`
   for all 163 parks in 149 s: **469,692 cleared, 30 set**, flagged now
   7,585,655 = inside exactly. It corrects **both directions** — clearing only
   the false positives would move every count one way and read as a trend — and
   commits per park so the writer stays available (invariant 16).

```
                          before        after
tagged   protected_area_id = X       42,092,853   (unchanged — still a catchment)
flagged  + in_protected_area = 1      8,055,317    7,585,655
inside   point-in-polygon                          7,585,655   (identical)
CMR_Nki /stats "fires"                     2,518            0
CAF_Chinko                               962,444      132,570
```

**A derived flag must say what it was derived from.**
`data/fire_containment_state.json` records the boundary file's SHA-256; a db
test compares it against the file and fails when a boundary edit makes the flag
stale, which is the whole point — the old flag drifted for months precisely
because nothing compared it to anything (invariant 5: a stamp nobody checks is
a comment). Re-run after any boundary change:

```bash
python3 scripts/rederive_fire_containment.py --dry-run     # counts, writes nothing
python3 scripts/rederive_fire_containment.py               # ~150 s, all parks
python3 scripts/audit_fire_containment.py --park CAF_Chinko   # the independent check
```

`audit_fire_containment.py` is deliberately **not** the same code: it
re-measures containment instead of trusting the stamp, so a bug in the
re-derivation shows up as a disagreement rather than as agreement with itself.

**Still true, and not a bug:** `/fire-narrative`'s `total_fires` (302,900 for
Chinko) and `/stats`'s (132,570) are **different units** — the first sums
detections belonging to fire *groups* near or inside the park (the v5 chain,
`pct_inside` by point-in-polygon), the second counts detections inside the
boundary. Both now **name their basis**: the narrative carries
`total_fires_basis`, the popup label reads "Fire detections in groups", and the
stats panel says "Fire Activity (inside)". Invariant 7 asks two surfaces saying
one word to say one number; where they genuinely count two things, they must
say two words.

Two dead things were removed while auditing the call sites, both of which would
have re-introduced the buffer if revived: a 56-line `park_stats` CTE in
`HandleAPIParksExport` assigned to `query` and discarded with
`_ = query // for future optimization` (it joined `fire_detections` on a
`park_id` column that table does not have, so it had never run), and
`PrecomputeRecentFireNarratives`, an uncalled **second writer** to
`fire_narrative_cache` with the same phantom column (invariant 10).

---

## The 2024 step is the satellite fleet, not the fire (F11 — fixed 2026-08-13)

One VIIRS sensor flies before 2024-01 (`N`, Suomi-NPP); three fly after (`N`,
`N20`, `N21`). Every raw detection series therefore has a ~3× step on
2024-01-01 that is **instrument, not landscape** — CAF_Chinko goes 61,509
detections in 2023 to 203,223 in 2024 without an extra hectare burning. Same
failure as F8's Hansen→GFW switch, so it reuses F8's mechanism: the sparkline's
`d.brk` **cuts the line** and captions why, rather than drawing a rise.

The fleet is **measured, never typed** (invariant 2): "three sensors since
2024" describes an ingest history that grows nightly and would be wrong the day
a sensor is added or a differently-sourced CSV is imported.

```
db/migrations/056-fire-sensor-epochs.sql   fire_sensor_epochs(month, sensors,
                                           sensor_count, detections, computed_at)
scripts/build_sensor_epochs.py             the writer — full scan, ~90 s, cron 04:30 on the 1st
srv/fire_sensor_epochs.go                  hourly-cached reader, sensorsOn()
/api/parks/{id}/fire-trend                 each week carries `sensors`/`sensor_count`
                                           + `sensor_epochs_measured`
```

Monthly and **global** on purpose: per-park-per-week distinct-satellite counts
are a sampling artefact — a quiet week in a small park shows one sensor because
only one fire burned. `detections` travels with each month so a reader can tell
a real single-sensor month from a thin one.

Three consequences in the frontend, each a separate lie avoided:

* **An unmeasured fleet is not a constant one.** When `fire_sensor_epochs` is
  empty the API says `sensor_epochs_measured: false`, no breaks are drawn, and
  the caption says the fleet is unmeasured — an unbroken line silently asserts
  continuity (invariant 12).
* **Do not compare across fleets.** The sparkline's "prior years average"
  reference now drops prior years flown by a *different* fleet. Comparing a
  2024 week against one-satellite years made every park read as a fire
  emergency at the cutover — the same rule F9 applies to `area_method`.
* **A null reference is a gap, not a zero.** `v3 || 0` drew the reference line
  down to the axis wherever it was unmeasured, which says "no fire in prior
  years"; `gapLine` now breaks the path instead.

Only a **known, changed** fleet breaks the line: a week with no epoch row must
not manufacture a break out of ignorance (the same guard F8's `methodKey` uses).

## Fire pipeline health & consistency

Three artefacts must agree or the popup silently breaks ("Feature not found"
when clicking a fire in a narrative):

    data/fire_groups_v5/*.json   ->  builder output (source of truth)
    feature_geometries           ->  what the map/pin API resolves
    fire_narrative_cache         ->  what narrative links point at

```bash
python3 scripts/check_fire_consistency.py --verbose   # read-only, exit 1 on drift
python3 scripts/fix_fire_consistency.py --dry-run     # then without --dry-run
```

The nightly pipeline runs the check as step 7 and records the result in
`data/pipeline_status.json`, served by `GET /api/pipeline-status` (adds `stale`
after 48h = two missed runs) and shown as a colour-coded badge in the **admin
panel header** (click for per-step counters + errors). Log rotation:
`5mp.logrotate` → `/etc/logrotate.d/5mp`.

**Persistent hotspot mask**: `fire_persistent_cells` (323 cells, 32 parks) lists
0.0034deg cells detected in >=30 distinct months — lava lakes (COD_Virunga),
flares, kilns. Built by `scripts/build_persistent_hotspots.py` (monthly, step 2d
on the 1st). Masked detections cannot **seed** a cluster but are still absorbed
by a real front within `DAY_EPS_KM`. Ablate with `--no-hotspot-mask`.

A/B'd 2026-08-06 on a frozen DB: **keep it**. It cuts `stationary_fire_pct`
(detections locked up in groups that burn ≥60 days inside a <3 km box) from
3.01% to 0.27%. Beware: every *other* harness metric "regresses" — a lava lake
is the harness's idea of a perfect group, so removing it costs `mean_days`
−28%, `coverage_pct` −20%. **Never judge a detection-filtering change on
`coverage_pct`**; read `stationary_pct`/`stationary_fire_pct` instead.

**Group feature_ids are deduped**: `dedupe_feature_ids()` salts only actual
collisions (722/181,711) so persisted friendly names in `fire_group_names` stay
valid. Never change the primary hash without a name migration.

## ⚠️ Fire Narrative Cache — Single Writer Rule

**Only `scripts/precompute_narratives_v5.py` may write `fire_narrative_cache`.**
It reads `feature_geometries` and emits real v5 hash feature_ids
(`CAF_Chinko_2026_grp_dcb35641`). The legacy Go path
(`computeFireNarrativeForCache` → `getTrajectoryNarrativesFromJSON` in
`srv/fire_narrative_cache.go`) reads stale `data/fire_trajectories_v2/` files and
generates sequential `_grp_N` ids that don't exist in the features API →
"Feature not found" when pinning fires. It is DEPRECATED — never re-wire it
into refresh paths. (This bug shipped once via `/api/refresh-park`; fixed 2026-07-06.)

- Per-park refresh: `python3 scripts/precompute_narratives_v5.py --park CAF_Chinko` (~1s, fire-only)
- `/api/refresh-park` and the weekly cache worker shell out to this script.
- Detect stale v2-written rows: `computed_at` without a `T` (Go used `CURRENT_TIMESTAMP`,
  python uses ISO8601): `SELECT park_id FROM fire_narrative_cache WHERE computed_at NOT LIKE '%T%'`
- Verify a cache is v5: feature_ids in `narratives[].feature_id` must be hex hashes, not `_grp_1`.

**fire-realtime counts** (`srv/fire_realtime_handlers.go`, `handleFireRealtimeFromFeatures`):
`groups[]` payload is capped at 100, but `total_groups`/`active_groups_count` are true
pre-cap counts. `is_inside` = touches park (`dist_to_park_km≈0` or `pct_inside>0`);
groups up to 20km outside are included for context but not "inside". Peak-season
parks (Angola/DRC/Zambia, Jun–Aug) legitimately have 150–280 active groups — not a bug.

## Data Processing Scripts (v5)

See `docs/SCRIPTS.md` and `docs/FIRE_PIPELINE.md` for full details.

```bash
# Full rebuild pipeline:

# 1. Rebuild fire groups with v5 algorithm
python3 scripts/rebuild_fire_trajectories_v5.py

# 2. Load to database with context enrichment
python3 scripts/load_fire_groups_to_db.py --force

# 3. Precompute v5 narratives
python3 scripts/precompute_narratives_v5.py

# Daily incremental update (runs via cron at 3am UTC):
python3 scripts/daily_fire_update.py --days 7
```

---
