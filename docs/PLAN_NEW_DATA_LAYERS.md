# Plan: five new data layers (GHSL epochs, JRC AKP, nightlights, surface water, UCDP, Soviet 200k)

Six work packages, independent, ordered by payoff-per-effort. Each is sized for
one agent conversation. **Read the named `docs/agents/*.md` file before starting
a package** — this plan gives the contract, not the accumulated traps.

Cross-cutting rules that apply to every package (they are the repo's invariants;
violating any of them has already cost days at least once):

- **R1** A fetch/filter that matches nothing must report UNFINISHED, never
  exit 0 (invariant 1). Assert expected corpus sizes; a short manifest reads as
  a small collection.
- **R2** Never type a count that describes a variable input — derive it
  (invariant 2).
- **R3** Every derived flag names its input (SHA/mtime stamp) and a db test
  compares them (fire.md F10 pattern).
- **R4** A new proxy ships with a **measured skill** against an existing truth
  set, or ships saying UNMEASURED. `nil` prints as the word, not as a gap
  (invariant 12).
- **R5** Park-shaped tables hold AOI rows: any query without `park_id` applies
  `aoiExcludeSQL`; settlement queries apply `settlementFilterSQL` (invariant 5).
- **R6** Long writers commit in batches (invariant 16). Time-varying counts
  never cross an instrument change without cutting the line (fire.md F11).
- **R7** Attribution/licence fields (`source`, `accessed`, `citation`, `terms`,
  `notice`) ride in every committed artefact, ACLED-style (acled.md).

---

## WP1 — GHSL back-epochs: measure `settlement_type` instead of guessing it

**Question answered:** which of the 1,374 `temporary_camp` clusters in
`XSA_Study_Area` are persistent (visible in older built-up epochs) vs new?
This is the overgrazing proxy. `settlement_type` is deliberately NULL today
because inter-epoch persistence is not ingested (settlements.md, F12).

**Read first:** `docs/agents/settlements.md`, `scripts/ghsl_tiles.py` header.

**Data:** GHS_BUILT_S R2023A also publishes epochs E1975…E2020 in 5-year steps,
same 100 m Mollweide tile grid, same URL pattern as the E2030 product already in
`scripts/ghsl_tiles.py` (`PRODUCT`/`TILE_BASE` — parameterise `EPOCH`). We need
**two** back-epochs only: E2000 and E2015 (enough to say "existed 10+ / 25+
years ago"; more epochs is more download for no extra class).

**Design:**

1. `scripts/ghsl_epochs.py` — for one area: for each settlement row (with
   `polygon_ids`, per R5/`settlementSourceSQL`), read the E2000/E2015 BUILT_S
   values over the **same mask pixels** the current surface uses. Reuse
   `_read_window_like` (the one-pixel-offset bug in settlements.md is exactly
   the trap here — derive offsets from the E2030 affine). Output per row:
   `surface_e2000_m2`, `surface_e2015_m2`.
2. Persistence rule, applied in the same script:
   - built surface ≥ 25% of today's in E2000 → `permanent`
   - ≥ 25% in E2015 only → `established` — **do not** shoehorn into the
     existing CHECK constraint; migration 057 widens
     `settlement_type CHECK IN ('temporary','permanent','established')`
     *only if* you keep the column; otherwise add
     `persistence TEXT` + `persistence_source TEXT` and leave
     `settlement_type` NULL (preferred: the old column's two words never
     matched this measurement — a new measured column beats a reinterpreted
     old one; check with the user before choosing).
   - absent in both → `recent` (NOT "temporary": a 2021 village is recent, not
     seasonal — the word must claim only what the measurement supports).
   - **A pixel absent from an old epoch and a tile absent from the download
     are different states** (R1): a missing tile ⇒ `persistence NULL`,
     `persistence_source='tile_missing'`.
3. Provenance: `persistence_source = 'ghsl_E2000+E2015'`; stamp file
   `data/ghsl_epoch_state.json` records product SHAs per tile; bump
   `PIPELINE_VERSION` in `ghsl_tiles.py` so the 06:45 rotation re-queues all
   157 areas (that mechanism exists — use it, do not write a second queue).
4. Surfacing: settlement narrative sentence ("N of M camps existed in 2000");
   popup line; GeoPackage export column (`gpkg_export.go`, `gpkgSettlements` —
   NULL stays SQL NULL per settlements.md).

**Tests (add to `tests/db_tests.sh`):** persistence only on rows with
`polygon_ids`; `persistence_source` non-NULL wherever persistence is;
stamp-vs-file comparison (R3). API test: Chinko narrative unchanged in
count/surface (this package must not move any existing number).

**Est. download:** ~4 tiles × 2 epochs for XSA; whole corpus fits the existing
tile cache pattern. Batch-commit per area (R6).

---

## WP2 — JRC Africa Knowledge Platform: faults + craton edges as a scored layer

**Question answered:** structural context for mining ground in eastern CAR,
where the sheet affinity model measures null (overlays.md "Other geology data,
weighed"). The eval **already measured** these layers: `active_faults` 1.6–2.7×
on DRC gold/cassiterite/coltan at 25 km, craton **edge** 2.9× on gold. Verdict
in overlays.md: "the one to ingest". This WP is ingest + serve, not research.

**Read first:** `docs/agents/overlays.md` §"Other geology data, weighed" and
§"Contacts"; `srv/geomap.go`, `srv/static/geomap.js`.

**Data:** JRC AKP WFS, GeoJSON, whole continent, one request, no key. Layers:
`akp:active_faults` (406 lines, Macgregor 2014), `akp:cratons` (9 polygons →
derive **edges** as linework; the interior scores ~1.0 and means nothing —
overlays.md). Skip `LithoMap_Africa` (weaker than our sheets) and
`gem_active_faults` (California/Europe — the eval already discarded it).

**Design:**

1. `scripts/geomaps/fetch_akp.py` → `data/akp/{active_faults,craton_edges}.geojson`
   (committed — they are small and CC-licensed; carry R7 fields). Assert
   feature counts ≥ known values (406 / 9) or report unfinished (R1).
2. Serve as **one** additional overlay beside the contacts, not a fourth
   "sheet": these are continental lines, not a vectorized scan — they have no
   classes, no legend colours, no commodity columns. New thin handler
   `srv/geomap_structural.go` (`GET /api/geomap/structural` → the two GeoJSONs,
   gzipped, `immutable` + `?v=` mtime rev), one MapLibre line layer each,
   toggle rows inside the geology panel's Advanced section
   (`MapLegend.geoBody()`), OFF by default. Style: distinct dash, **never** the
   contact amber ramp — these lines are ungraded context, and wearing the
   graded ink would claim a grade (overlays.md invariant 12 shape).
3. Skill line: the panel's junction/score area shows the eval's measured lifts
   with their truth set named ("faults ×2.7 vs DRC coltan visits; unmeasured on
   this sheet's ground") — pull from the existing eval output, never typed
   (R2). If the number isn't in `data/eval/geo_affinity.json`'s continental
   section, re-run `eval_affinity.py --continental` and regenerate
   `srv/geomap_scores_table.go` via `gen_scores_go.py`. **Never hand-edit that
   file** (invariant 12).
4. GeoPackage: both linework layers ride in `geology.gpkg`
   (`srv/geomap_gpkg_layers.go`, same pattern as `geology_contacts`); the cache
   stamp must include the two new inputs (overlays.md: "the cache stamp covers
   units, contacts AND anchors" — extend it).
5. Share link: `?geomap_structural=faults,cratons`.

**Tests:** `srv/geomap_*_test.go` — layer served whole (count == file count,
R1/invariant 8), stamp includes new inputs; UI test that the toggle exists and
the score line prints "unmeasured" for sheets with no continental eval row.

---

## WP3 — VIIRS Black Marble nightlights: the fire-quiet, human-active layer

**Question answered:** which reported mine clusters and remote camps emit
light? The review measured that mines have NO fire signature; nightlight is the
complementary proxy. Target first: Yangou/Kotto (8.0–8.5°N 23.3–23.9°E) and
Kpangou/SW-Chinko clusters.

**Read first:** `docs/agents/mining.md` (the line: inference ships, fabricated
evidence does not), `docs/agents/reference.md` (workers).

**Data:** VNP46A3 (monthly composite, ~500 m, NASA LAADS, needs an Earthdata
token — **request-integration skill**, never a pasted secret; env var
`EARTHDATA_TOKEN`, absent ⇒ scripts skip with the var's name per AGENTS.md).
Monthly, 2014→now. One tile (h20v08 area) covers most of the AOI; fetch by
bbox via the LAADS API.

**Design:**

1. `scripts/fetch_nightlights.py --bbox <aoi>` → monthly radiance GeoTIFFs
   under `data/nightlights/` (gitignored). Gap-filled product,
   `AllAngle_Composite_Snow_Free`; keep the `Num_Obs` band — a radiance from 2
   cloud-free nights is a different claim than from 25, and the count must
   travel with the value (R4 spirit).
2. `scripts/nightlight_sites.py` — **site-anchored scalars, not a raster
   layer**: for each site in `data/eval/mining_reference.json` +
   `data/eval/crisistracker/mine_sites.json` inside our areas, extract a
   monthly radiance series (3×3 pixel window, median). Output
   `data/eval/nightlights_sites.json` (committed, small, R7 fields). Also a
   control set: N random points in the same hull (the acled.md baseline
   discipline — a site series without a random-ground series beside it is a
   number with no denominator).
3. **Skill before UI** (R4): does radiance at reported mine sites exceed the
   hull baseline? Print the lift and its permutation p in the JSON. If it is
   null — likely for pit sites with no generators — the finding "artisanal
   mining here is dark" is itself worth committing, and **no map layer ships**.
   Only if signal exists: a per-site sparkline in the feature popup for
   `mining_anchors` (the anchors already render via the geology GeoPackage /
   panel), never a continental raster drape.
4. Cron: monthly rotation appending the newest composite month
   (`--rotate`-style, one areas' sites per night; batch commits R6).

**Traps recorded in advance:** monthly composites have real gaps in the wet
season (that is `NULL`, not 0 — a dark month and an unobserved month are
different states); VNP46A3's native HDF5-in-h5py needs `h5py`, check it's
installed before writing the reader; radiance is nW·cm⁻²·sr⁻¹ — name the unit
in every field (invariant 7).

---

## WP4 — JRC Global Surface Water: new permanent ponds = pit-lake candidates

**Question answered:** shape-based pit detection that optical spectral indices
failed at (mining.md: retired at AUC ≈ chance). A NEW permanent water body is a
different, much narrower claim than "turbid pixel", and pit lakes are exactly
that. Joins the basin layer (which already gives downstream traces into parks).

**Read first:** `docs/agents/mining.md` (whole file — this WP walks the edge of
a retired detector), `docs/agents/aoi.md` §basin.

**Data:** JRC GSW v1.4 (Pekel et al.) — `transitions` and `seasonality` rasters,
30 m, tiled 10°×10°, plain HTTPS, no key. Two tiles cover the AOI. The
YearlyHistory rasters give per-year water class if the transitions layer proves
too coarse (it ends 2021 — check the current release's end year and **write it
into the output**, R2/R6: a 2021-ending layer must not be read as "no new pits
since").

**Design:**

1. `scripts/fetch_gsw.py --bbox` → `data/gsw/` (gitignored).
2. `scripts/gsw_new_water.py` — extract polygons whose transition class is
   "new permanent" or "new seasonal", area 0.1–50 ha (pit-lake scale; the
   bounds are constants calibrated at AOI scale — name them in the output per
   invariant 15), **excluding** anything within 200 m of a HydroRIVERS reach
   (`park_rivers` / `park_basin_rivers`) — river migration is the confuser.
   Output `data/eval/gsw_new_water.json`: candidate polygons + nearest
   reported mine site distance + nearest settlement distance.
3. **Skill measurement is the deliverable** (R4): capture = % of reported mine
   sites (mining_reference + crisistracker, field-visit rows) with a new-water
   candidate within 2 km; baseline = same for random hull points. Print lift,
   p, and the power ceiling beside any null (acled.md: "a null without its
   power is a shrug wearing a result's clothes").
4. Ship decision mirrors WP3: signal → candidates become a small point layer
   `water_anchors` beside `mining_anchors` in the geology GeoPackage +
   panel note, labelled "new permanent water (candidate pit lakes,
   UNADJUDICATED)"; no signal → commit the eval JSON and stop. **Nothing enters
   `park_settlements` or any narrative** — that is `RegisterMiningCandidate`'s
   corpse and it stays buried (mining.md).

---

## WP5 — UCDP GED: event-level conflict context ACLED refused us

**Question answered:** site-scale conflict context for the mining clusters
(ACLED event-level is 403; our ADM1 scalars are 50,000 km² buckets). UCDP GED
is free, event-level, georeferenced, CC-BY.

**Read first:** `docs/agents/acled.md` — the licence discipline transfers even
though UCDP's terms are lighter; and the **"coordinates are label positions"**
trap: GED's `where_prec` codes 1–7 grade exactly that, and a prec-4 point is an
ADM1 centroid wearing coordinates.

**Data:** UCDP GED via API (`https://ucdpapi.pcr.uu.se/api/gedevents/<version>`,
paged JSON, no key) or the yearly CSV. Filter countries: CAR, DRC, South Sudan,
Sudan, Tanzania.

**Design:**

1. `scripts/fetch_ucdp.py` → `data/ucdp/ged.json` (gitignored raw; the API
   pages — union by `id`, abort if a page returns exactly the cap, R1/Crisis
   Tracker lesson).
2. `scripts/ucdp_sites.py` → `data/eval/ucdp_context.json` (committed):
   - per reference mine site: events/fatalities within 10 km and 25 km,
     **prec ≤ 3 rows only** for distance claims; prec > 3 rows counted
     separately as "district-level" — never pooled (two units, two words,
     invariant 7);
   - per park/AOI: yearly event series inside the boundary (point-in-polygon
     against `keystones_with_boundaries.json`; stamp the boundary SHA, R3);
   - R7 fields: cite UCDP/GED (Sundberg & Melander 2013; current version
     string **read from the API**, not typed, R2).
3. Cross-check vs our ACLED ADM1 scalars: same-province totals should
   correlate; print the comparison in the JSON (they measure different
   inclusion rules — a divergence is a fact to record, not an error to fix).
4. Surfacing: one sentence in the park/AOI settlement- or fire-narrative is
   tempting — **don't**, yet. First consumer is the geology panel's anchors
   block (`geoAnchorBlockHTML`): a per-site "conflict within 10 km: N events
   (UCDP GED vX)" line. UI-wide conflict layers repeat the ACLED prohibition
   discussion; UCDP allows it, but do it as its own decision later, not as a
   rider on ingest.

**Tests:** row-count floor per country (R1); prec-pooling guard (a db/JSON
test that the ≤3 and >3 counters are disjoint); version string present.

---

## WP6 — Soviet General Staff 1:200k: histmap #2 over the whole AOI

**Question answered:** a uniform 1960s–80s baseline (tracks, wells, named
settlements) across CAR + DRC + SSD — the half of the AOI the Sudan 250k series
does not reach. Primary consumers: WP1 adjudication (a "recent" cluster on a
1975 Soviet village is a re-classification) and the linear-clearing review
north of 10°N.

**Read first:** `scripts/histmaps/README.md` (the full post-mortem),
`docs/agents/overlays.md` §Historical map overlay, `srv/histmap.go`.

**Sourcing (do this step first — it gates everything):** Soviet 200k sheets for
equatorial Africa are mirrored across several archives with varying
completeness and no single manifest. Enumerate the needed sheet IDs from the
grid (nomenclature `X-34-…` etc. for 4–11°N 22–31°E — derive the list from the
bbox, R2), then survey sources (loc.gov, university mirrors, commercial
archives) for coverage **before** building. Deliverable of step 1 is
`scripts/histmaps/soviet200k_manifest.json`: sheet id → URL/absent. If coverage
is < ~60% of the AOI, stop and report — a sparse mosaic is fine (204 on miss is
already the design) but the user should choose. Licence: Soviet military maps
are publisher-contested; record `terms: unstated` per source and show those
words (overlays.md: "open" and "nobody said" are different states).

**Build (mirrors sudan250k, second instance not a fork):**

1. Generalise, don't copy: `srv/histmap.go` already hints at multi-map
   (`histMaps.rev`, id in the URL). Promote `histMapDefaultPath` to a
   two-entry table (`sudan250k`, `soviet200k`), `GET /api/histmap` returns a
   list; the client (`HistMap` in globe.html) iterates. Share param stays
   `?histmap=<id>`.
2. Georeference: Soviet sheets carry a printed graticule like the Sudan series
   — `scripts/histmaps/grat.py` should adapt. **Gauss–Krüger, not UTM**; fit
   the graticule ladder in lat/lon as sudan250k.py does and it won't matter.
   Blank-desert decline rule carries over (a sheet the detector can't fit is
   declined, not guessed).
3. Mosaic: `mosaic.sh` parameterised by map id → `data/histmaps/soviet200k.mbtiles`
   (gitignored, R2 for all counts in metadata via `refresh_meta.py`).
   Cyrillic labels: **do not** pre-translate the raster; OCR labels into the
   existing `labels.sqlite3` pipeline (`ocr_labels.py`) with a transliteration
   pass, so search works while the map stays as printed (the histmap "white
   ink" lesson: never fork the archive to fix presentation).
4. Serving: tile URLs carry `?v=<rev>` (the stale-cache trap is documented and
   will recur); misses 204; download endpoint per map id.
5. Rebuild as a throttled resumable systemd oneshot with `--priority-bbox` on
   the AOI, exactly like `histmap-rebuild.service`.

**Tests:** manifest line-count assertion vs the derived sheet list (R1 — the
264/770 captions.txt failure is the founding trauma here); `GET /api/histmap`
lists both maps; tile rev changes when the MBTiles changes.

---

## Order and sizing

| WP | effort | blocking dependency | headline deliverable |
|---|---|---|---|
| 1 GHSL epochs | M | none | measured `persistence` on 13,871 settlements |
| 2 JRC AKP | S | none (eval already ran) | faults+craton-edge overlay w/ measured lift |
| 3 nightlights | M | Earthdata token (user) | site radiance series + lift, ship/stop gate |
| 4 GSW water | M | none | pit-lake candidate eval, ship/stop gate |
| 5 UCDP | S | none | per-site conflict scalars, anchors-block line |
| 6 soviet200k | L | source survey gate | second histmap over CAR/DRC/SSD |

Suggested sequence: 2 → 1 → 5 → 4 → 3 → 6. WP2 is a day and pays immediately;
WP6 starts with a cheap survey step that can run in parallel with everything.

After each WP: `make build && sudo systemctl restart 5mp`, verify
`/api/version` == `git rev-parse --short HEAD`, run `./tests/run_all.sh`, and
add the subsystem knowledge to the matching `docs/agents/*.md` (NOT AGENTS.md).
