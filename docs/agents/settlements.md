# Settlements — surface, extent, population, and their provenance

Where every settlement figure in the app comes from, and why three of them used
to be different numbers for one word. Began as the F1–F12 handover
(`docs/AOI_STRUCTURAL_FIXES.md`); **all twelve are deployed**, migration 055 has
run, the backfill has converted all 157 areas, and F10/F11 (fire containment
and the satellite-fleet step) closed on 2026-08-13 — those two live in
`docs/agents/fire.md` because they are fire, not settlement.

Every settlement figure here is now a **measured surface** and a **measured
population**, or absent.

---

## What the numbers look like now

```
                      settlements  surface km²   extent km²   population
converted (157 areas)      13,871        287.1      7,293.3    6,851,399
retired detector            3,019            —            —            —
legacy label                    0
```

Extent exceeds surface by a **median of 22×** across the 157 areas (min 6×,
max 116×) — that ratio *is* F1, and it is why the two must never share a
column name. `scripts/backfill_settlement_surface.py --list` is the queue and
prints `0 area(s) pending`.

Cross-checks worth re-running after any change here:

```bash
./tests/run_all.sh db     # 48 tests; 11 of them are these invariants
python3 scripts/backfill_settlement_surface.py --list
curl -s "localhost:8000/api/parks/CAF_Chinko/stats?pwd=test2026" | jq .settlement
curl -s "localhost:8000/api/parks/CAF_Chinko/settlement-narrative?pwd=test2026" \
  | jq '{settlement_count,polygon_count,total_population,population_measured_for}'
```

The last two must agree: 20 settlements, 0.027 km² surface, 826 people. They
disagreed for months (see "a fossil" below).

## The backfill

`scripts/backfill_settlement_surface.py` — one area per unit of work,
`--rotate N` for N of them, cron at **06:45** does 2/night to absorb newly
onboarded parks. It re-ingests the area's GHSL tiles through
`ghsl_tiles.polygons_in` and re-clusters through the canonical
`EventRebuilder.rebuild_settlements_for_park`, so parks and AOIs get the same
classifier. Whole queue: ~25 min for 157 areas; `XSA_Study_Area` alone is
4 tiles / 74,904 polygons / ~6 min.

Three things about it are load-bearing:

* **Ids are coordinate-keyed** (`settlement_<area>_<lat>_<lon>`), so a re-run
  is idempotent and a *shorter* second run cannot leave the tail of a longer
  first one behind. Parks use `ghsl_tiles.PARK_PREFIX`, **not** `AOI_PREFIX`:
  `aoi_clip.py` deletes everything except `settlement_ghsl_%` when copying a
  park's footprints into an overlapping AOI, so a park keyed with the AOI
  prefix would produce copies that are undeletable *and* double-counted.
* **A run that yields nothing where rows existed reports UNFINISHED**, leaves
  the old rows alone, and does not stamp the state file — so the rotation
  retries it (invariant 1).
* **`ghsl_tiles.PIPELINE_VERSION` re-queues everything when the *reader*
  changes.** This exists because two real bugs moved the numbers without
  moving any label; see below. A converted area whose state entry is missing
  or stamped with an older version counts as pending — unknown is not clean.

## Two reader bugs, and why a label could not catch them

Both were found *after* the first full backfill, by tests that compare
quantities rather than check for nulls.

1. **The POP window was one pixel off.** `_read_window` took each raster's
   window from the geometry's bounds independently. Those bounds are not on a
   pixel edge (`col_off = 5992.39`), so BUILT_S rounded one way and POP
   another. Verified against per-pixel coordinate lookups: the old read
   matched ground truth for **71 of 200** built pixels, the new one for
   **200 of 200**. It moved Comoé's population by 12% (11,788 → 9,838).
   `_read_window_like` now derives POP's integer offsets from the BUILT_S
   affine, so both rasters see the same pixels by construction. Tiles are also
   *cropped to their own data extent* — R10_C19 is 4000×3000 in BUILT_S and
   10000×10000 in POP — so shape equality was never a safe alignment check;
   it silently cost GAB_Loango its population entirely.
2. **Extent and surface counted different things.** `extent_m2` was the
   clipped polygon's geometric area while `area_m2` sums *whole* pixels, so a
   settlement straddling an area boundary could report more surface than
   extent — impossible, and true for 3 rows. Extent is now the mask's pixels
   × pixel area, over the same pixels the surface uses.

The db test `surface never exceeds extent` catches the second class directly;
the first was caught only by asking the raster. **The lesson worth keeping: a
provenance label names the instrument, not the code that read it.** That is
what `PIPELINE_VERSION` is for.

## A fossil, and a mislabelled column

Two things the backfill exposed that were not in the original list:

* **`ghsl_data` was a 156-row snapshot from 2026-03-03 that nothing in the
  tree writes.** `/api/parks/{id}/stats` served `built_up_km2` from it, so the
  stats panel said 0.61 km² for CAF_Chinko while the settlement narrative said
  0.027 km² — one word, two numbers, 22× apart, and the stale one was the
  mask. Now derived from `park_settlements` through the provenance helpers, and
  it reports extent and surface separately plus `surface_measured_for` /
  `population_measured_for`. **A cached aggregate with no writer is not a
  cache, it is a fossil** — the table still exists and is now unread.
* **`nearest_boundary_km` was the distance to the nearest named PLACE.**
  globe.html duly scored it as encroachment (`< 10 km ⇒ priority`), which gave
  every remote settlement 0 — in CAF_Chinko that is all of them (nearest place
  70.9 km). Renamed `distance_to_place_km`; the proximity term is **gone**
  from the priority score rather than re-weighted, because distance to the
  boundary is not in that response at all. A weight on the wrong quantity is
  worse than no weight.

## The download carries its own provenance (F12, and F1/F2 leaving the app)

`settlement_type` is **NULL everywhere**, deliberately. It used to read
`permanent` for every row because the only rules emitting `temporary`
(`temporary_camp`, `pastoral`) required `total_area < 5,000 m²` — *below* the
`MIN_AREA_M2 = 5,000` ingest floor, so `temporary` was unreachable by
construction, in a landscape whose defining feature is seasonal pastoral camps.
The fix was not to nudge the threshold until both words appear: size cannot
answer the question at all, and inter-epoch persistence — which can — is not
ingested. So the column says **unmeasured**. A column that always says the same
word is not a classification (invariant 12).

The GeoPackage export (`srv/gpkg_export.go`, `gpkgSettlements`) is where this
bites hardest, because **a download is the most published artefact there is**:
it leaves the app, gets joined to other data, and has no stats panel beside it
to explain anything. It therefore ships `area_m2` *and* `extent_m2` as separate
columns with `area_source`, plus `population_source` and `epoch`, and writes
`settlement_type` as SQL `NULL` rather than `''` (`gpkgStr` maps empty →
`NULL`, so an unmeasured class cannot arrive in QGIS as a blank string that
reads like a value). Per-polygon values from `properties_json` win over the
cluster's row, falling back to the cluster so a missing property reads as the
cluster's provenance rather than as absent. Verified on `CAF_Chinko`: 28
footprints, 28 NULL `settlement_type`, 0 NULL `population_source`, 0 NULL
`extent_m2`.

## Traps that still apply

* **`polygons_in` yields `(poly, dict)`**, not `(poly, area_m2)`. `ingest_tile`
  is the only in-tree caller — check before assuming that holds.
* **`rebuild_settlements_for_park` must never delete rows without
  `polygon_ids`.** Those are the 3,019 retired pit/turbidity rows (invariant 5)
  that this clusterer cannot recreate. The park-scoped and global deletes are
  both scoped now; running the rebuild over 160 areas is how an unscoped purge
  would have gone unnoticed until the rows were gone.
* **The classifier keys on surface, not extent**, and the two differ by ~22×,
  so class histograms moved for every park (Chinko: 32 `agricultural` → 25,
  a `village` appeared). That is the fix, not a regression.
* **`_get_nearest_river` returning None is the point** — the sentence is
  omitted rather than asserting a river 700 km away.
* **Do not compare across `area_method`.** F9's flagger scopes its median to
  one method; any new year-over-year comparison must too, or every park flags
  at the 2024 cutover. The fire series learned the same rule from a different
  input: a prior-years average drawn across the 2024 satellite-fleet change is
  a comparison against a third of an instrument (`docs/agents/fire.md`, F11).
* **...and do not compare at one scale only.** That same flagger
  (`flag_anomalous_years` in `scripts/daily_park_refresh.py`) scored **0 on
  `XSA_Study_Area`**, the area it was written for: park-wide, 2023's 313.6 km²
  against a 48.9 km² median is 6.4×, and the threshold is 50×. The 1,000× step
  is real but *local* — 198.6 km² of it lands in four 0.5° cells north of
  10 °N — and summing the park averaged it away. It now tests park-wide **and**
  per ~55 km cell, flagging the events in the offending cell so the reader
  learns which ground is questioned. Two db tests hold it: the block stays
  flagged, and flags stay under 5 % of the corpus (currently 19 flags / 99 rows
  / 8 of 81 areas). An anomaly detector that flags nothing has not cleared the
  data; it has failed to look, and "almost nothing is anomalous" over a whole
  corpus is invariant 1 wearing a result's clothes.
