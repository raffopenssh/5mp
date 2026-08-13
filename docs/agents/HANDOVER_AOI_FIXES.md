# Handover — AOI structural fixes (docs/AOI_STRUCTURAL_FIXES.md)

Started 2026-08-13. **F1–F9 and F12 are deployed, migration 055 has run, and
the backfill has converted all 157 areas.** Every settlement figure in the app
is now a measured surface and a measured population. F10 and F11 are still
open, and they are the only items left in the original list.

Status of the whole list: **F1 F2 F3 F4 F5 F6 F7 F8 F9 F12 done · F10 F11
open.**

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

## Still open

**F10 — `protected_area_id` is a 100 km buffer.** The column F10 asks for
already exists: `in_protected_area` already means `dist_km == 0`, i.e. inside
the boundary (8,055,317 rows set, 34,037,536 with an id but outside, 5,532,890
unassigned). The work is **auditing which user-facing "fires in park X" counts
use `protected_area_id` alone** and adding `AND in_protected_area = 1`.
Nothing has been changed, so nothing has regressed — but a 10× overstatement
is live wherever such a count exists.

**F11 — sensor count changes at 2024-01-01.** One VIIRS sensor before 2024,
three after (2026: `N` 2,381,805 · `N20` 4,097,180 · `N21` 2,440,808). Every
raw fire chart has a 3× step at that date that is instrument, not landscape.
The sparkline now *has* a break mechanism (`d.brk`, built for F8) that this can
reuse; a per-sensor rate is the alternative.

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
  at the 2024 cutover.
