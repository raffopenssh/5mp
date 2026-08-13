# Structural fixes found while reporting on `XSA_Study_Area` (2026-08-13)

Each item is a *data-correctness* defect that made a published number wrong, not
a feature request. Ordered by how badly it misleads a reader. Evidence commands
included so none of this has to be re-derived.

> **All twelve are fixed and deployed** (F1–F9, F12 on 2026-08-13; F10 and F11
> later the same day). This file is kept as the *evidence*, not as a queue — the
> numbers below are what was wrong and how it was measured. Where the work
> lives now: settlements in `docs/agents/settlements.md`, fire containment
> (F10) and the satellite-fleet step (F11) in `docs/agents/fire.md`.

---

## F1 — GHSL "built-up area" is the mask, not the surface (~24× overstatement)

`scripts/ghsl_tiles.py:polygons_in()` keeps any 100 m pixel with
`> PIXEL_THRESHOLD_M2 (50)` m² of built-up surface, then vectorises the **binary
mask** and reports the *polygon* area as built-up area. GHS_BUILT_S is a
*fractional* surface raster: a pixel holding 60 m² of building contributes
10,000 m².

```
mask area over the AOI's 4 tile windows : 6,798 km²
sum of the raster's own values          :   181 km²   -> ratio 0.027
stored park_settlements area (XSA)      : 4,268 km²
```

**Fix:** carry the value sum, not the mask area. Zonal-sum the raster inside
each vectorised polygon and store that as `area_m2`; keep the mask polygon as
*extent* under a different key. Both numbers are wanted — extent for drawing,
surface for counting — but they must not share a name (invariant 7: a number
must name its unit).

Affects every AOI and every park onboarded through this path.

## F2 — `population_est` is derived from F1 and is unusable

`ghsl_tiles.py:213` → `int(area_m2 / 10000 * 200)`. 200 people/ha applied to the
24×-inflated mask gives **85,360,922 people** in the AOI, and a single "town" of
**61.7 million**. The comment says the constant is kept so AOI figures stay
"comparable to the parks'" — they are comparably wrong.

**Fix:** F1 first, then replace the constant with GHS_POP (the population
raster of the same release and epoch, same tile grid, already-solved fetch
path). If GHS_POP is not ingested, emit **no** population rather than a
constant-density one; per invariant 1 an unmeasurable quantity must say so.

Note the epoch: `EPOCH = "E2030"` is a **projection**, not an observation.
Every settlement figure in the app is a modelled 2030 state presented as
current. Either ingest E2025/E2020 or label the epoch in the UI.

## F3 — Settlement clustering chains across an entire country

`SETTLEMENT_CLUSTER_KM = 2.0` single-linkage over 74,904 polygons produced one
cluster of **52,454 polygons spanning 7.19–9.66 °N, 26.63–29.31 °E** (≈270 ×
275 km) recorded as one "town". Single linkage has no diameter bound; at park
scale nothing chained, at AOI scale everything did.

```sql
select lat,lon,round(area_m2/1e6,1) km2,
       length(polygon_ids)-length(replace(polygon_ids,',',''))+1 polys
from park_settlements where park_id='XSA_Study_Area'
order by area_m2 desc limit 3;
-- 8.49,27.98  3085.6 km²  52454 polys
```

**Fix:** cap cluster diameter (split any cluster whose bbox exceeds e.g. 15 km)
or switch to DBSCAN with a max-extent post-split. A cluster larger than the
largest real settlement in the region is a bug the pipeline can detect itself
and should refuse to write.

## F4 — `nearest_place` / `distance_to_place_km` use 100 of 971 places

`rebuild_events_enhanced.py:_load_park_places()` has `LIMIT 100` after an
`ORDER BY` on place *type*. For a park that is most of the places; for XSA it is
**100 of 971**, all cities/towns/large villages, so the "nearest place" is
frequently hundreds of km away.

```
stored − true nearest-place distance:  median 67.3 km, mean 105.7, p90 284, max 375
1,323 of 1,552 settlements overstated by >10 km
```
Visible in the product as `Located 215.8km from Cueibet` and
`Located 148.3km from Nagero` on settlements that have a village 3 km away.

**Fix:** drop the LIMIT and use a spatial index (or the existing `geoMemo`
0.25°-cell approach in `srv/narrative_cache.go` — this is the same "what is near
this point" question it already answers exactly).

## F5 — `_get_nearest_river()` does not look at the point

```python
def _get_nearest_river(self, lat, lon, rivers):
    if rivers: return rivers[0]   # "Already sorted by importance"
```
It returns the longest river in the whole area, ignoring `lat`/`lon`. Result:
**1,552 of 1,552 settlements and 7,814 of 7,815 deforestation events in XSA say
"Near Mbomou river."** — including events 700 km from the Mbomou. `rivers` is
itself `LIMIT 20`.

**Fix:** actual nearest-segment distance, and *omit* the sentence beyond a
threshold (say 10 km) rather than asserting a false one.

## F6 — Park-shaped fire-context columns are silently null for AOIs

`park_settlements.fires_1km`, `fires_5km`, `fire_seasonality`,
`deforest_nearby_km2` are populated for parks (Chinko 81/83, Bili-Uere 228/241)
and **0 / empty for all 1,552 XSA rows**. Nothing failed; the enrichment step
simply is not in the AOI path. A zero here reads as "no fire near this
settlement", which is the opposite of true (median 1,594 detections within 5 km).

**Fix:** either run the enrichment for AOIs, or write `NULL` and have the UI
print "not computed". A zero that means "unmeasured" is invariant 12's `nil`
problem in a different table.

## F7 — `park_group_infractions` reports 100 % "response rate" for an AOI

```
XSA 2024: total 16444, stopped_inside 16444, transited 0
```
and every trajectory has `position: "contained"`, `cross_border: false`. This is
structurally true — a group is "contained" relative to the polygon it was built
for, and the AOI polygon contains everything by construction — but the popup
renders it as **"Avg response rate 100 %"** next to "Stopped inside 38,725
(100 %)". For a park that phrase means rangers intervened. For an AOI it means
nothing at all.

**Fix:** suppress response-rate / stopped / transited for AOIs, or relabel.
`cross_border` should likewise be computed against national boundaries, not the
analysis polygon, or omitted.

## F8 — Hansen and GFW areas share a column and a chart

`deforestation_events.area_km2` holds **mapped canopy loss** for 2001–2023
(Hansen) and **`alerts × 0.0001 km²`** for 2024+ (`KM2_PER_ALERT` in
`daily_park_refresh.py`). Plotted as one series it shows 313.6 km² in 2023 →
0.7 km² in 2024, which reads as a 99.8 % collapse in deforestation and is
purely a unit change.

**Fix:** separate columns or an explicit `area_method` discriminator, and never
render both in one chart without a break. Same shape as invariant 7.

## F9 — The 2023 northern Hansen block needs a provenance check before it ships

203 of 2023's 313.6 km² sits north of 9.5 °N, where the series recorded
~0.2 km²/yr for 22 years — a 1,000× step in one year, in savanna, in the block
where the GFW alerts see essentially nothing (283 alerts, all years).

```sql
select json_extract(properties_json,'$.year') y,
       round(sum(json_extract(properties_json,'$.area_km2')),1)
from feature_geometries where park_id='XSA_Study_Area'
  and feature_type='deforestation' and bbox_miny>=9.8
group by 1 order by 1 desc limit 5;   -- 2023: 203.0, 2022: 0.3, 2021: 0.2
```

Most likely a Hansen baseline/definition change or a large fire scar scored as
loss. **Fix:** flag any (park, year) whose loss exceeds ~50× its 5-year median
as `needs_review` and let the UI say so, rather than drawing a spike.

**DONE 2026-08-13 — and the first implementation scored zero on this very
example.** Written park-wide only, `flag_anomalous_years()` returned 0 for
`XSA_Study_Area`: 313.6 km² against a 48.9 km² five-year median is **6.4×**,
nowhere near 50×. Across all 81 areas it flagged two rows, and a corpus-wide
"almost nothing is anomalous" is exactly the shape invariant 1 warns about —
the detector had exited 0, not measured anything. The step is *local*: the
anomaly lives in one block, and a park-wide median averages it away. The test
now also runs per ~0.5° (~55 km) cell (`ANOMALY_CELL_DEG`), where the same
year is a 1,000× step against a 0.0–0.1 km²/yr history, and flags the events
**in that cell** rather than the whole park-year — the reader is told which
ground is questioned, not handed a suspicious park. A cell must also clear
`ANOMALY_MIN_KM2 = 5.0` absolutely, because "100× of 0.01 km²" is not a
finding.

Result: **19 flags across 8 of 81 areas, 99 event rows** — `XSA_Study_Area`
2023 carries 227.6 km², including the four cells at 10–11 °N that are the
198.6 km² northern block this section is about. `tests/db_tests.sh` asserts
that block stays flagged *and* that flags stay under 5 % of the corpus, so
neither a silent zero nor a flag-everything regression passes.

## F10 — `protected_area_id` on `fire_detections` is a 100 km buffer, not the park

`ASSIGN_MAX_DIST_KM = 100` in `park_assigner.py`. Detections tagged
`CAF_Chinko` span 4.60–8.12 °N / 22.37–25.63 °E, far outside the boundary. Any
"fires in park X" figure taken straight from this column is a fires-within-100-km
figure. Point-in-polygon over the AOI gives 54,860 detections truly inside
Chinko vs **559,798** carrying its id — a **10×** difference.

**Fix:** keep the buffer column (it is useful) but add `in_park_boundary` and
make every user-facing "in park" count use it. At minimum, rename the column so
no one reads it as containment.

**DONE 2026-08-13** — and the flag that already existed turned out to be the
better half of the answer. Eleven call sites now carry `fireInsideSQL`
(`AND +in_protected_area = 1`, `srv/fire_containment.go`), and the flag itself
was re-derived against today's boundaries because 5.83% of it was a stored
answer from a rule that no longer runs (469,692 cleared, 30 set). `CMR_Nki`
goes 2,518 → 0. See `docs/agents/fire.md` "F10".

## F11 — Sensor-count change at 2024-01-01 is invisible in every time series

One VIIRS sensor before 2024, three after. Every raw fire chart in the app has a
3× step at that date that is instrument, not landscape.

**Fix:** either normalise (per-sensor rate) or draw the discontinuity. The
per-sensor query is cheap:
`select satellite, strftime('%Y',acq_date), count(*) ... group by 1,2`.

**DONE 2026-08-13** — the discontinuity is drawn. The fleet is *measured* into
`fire_sensor_epochs` (that cheap query, monthly, by
`scripts/build_sensor_epochs.py`) rather than typed as a constant, and
`/fire-trend` ships it per week so the sparkline cuts the line where the fleet
changes. The prior-years reference also stops comparing across fleets. See
`docs/agents/fire.md` "F11".

## F12 — `settlement_type` is 100 % `permanent` here

All 1,552 XSA rows are `permanent`; the classifier only emits `temporary` for
`temporary_camp`/`pastoral`, which require `total_area < 5000 m²` — below the
`MIN_AREA_M2 = 5000` ingest floor, so **`temporary` is unreachable by
construction**. In a landscape whose defining feature is seasonal pastoral
camps, the column says there are none.

**Fix:** raise the temporary threshold above the ingest floor, or better,
classify on **inter-epoch persistence** rather than size. Until then the column
should be `NULL`, not `permanent`.

**DONE 2026-08-13** — the column is `NULL`. Size cannot answer this question at
all (the two candidate rules straddle the ingest floor), and persistence between
GHSL epochs is not ingested, so the honest value is *unmeasured* — not a
threshold nudged until it emits both words. The 3,019 surviving `temporary` rows
are retired detector output, excluded by `settlementFilterSQL` (invariant 5).
The GeoPackage export now writes it as SQL `NULL` rather than `''`, alongside
`extent_m2`, `area_source`, `population_source` and `epoch`: a download is the
most published artefact there is, so F1's surface-vs-extent distinction and
F2's measured-or-absent population have to travel *in the file*, where there is
no panel to explain them.

---

## Cheap wins, in order (all taken — kept for the ordering argument)

1. **F5** (one function, ~10 lines) — removes a false sentence from 9,366 rows.
2. **F4** (delete a `LIMIT`) — fixes 1,323 wrong distances.
3. **F7 / F11 / F8** (labelling) — no pipeline work, stops three wrong readings.
4. **F1 → F2** (zonal sum, then GHS_POP) — the big one; unlocks the settlement
   layer for publication.
5. **F3, F12, F10, F6, F9** — pipeline changes, each self-contained.

One correction from doing it: **F11 was not labelling.** Drawing the break was
indeed a few lines, but the *fleet* had to be measured into a table first, or
the label would have been a hardcoded "three since 2024" describing an ingest
history that grows nightly (invariant 2). A labelling fix that has to name a
number is a pipeline fix wearing a label's clothes.

And a second: **F9 was not self-contained — its threshold was.** Flagging a
step against a five-year median is a dozen lines, but at park scale that
threshold answered "no" to the very block that prompted the item. The cheap
part was real; the *scale* the cheap part ran at was the whole fix — which is
the invariant below, arriving one item later than expected.

## Suggested invariant for `AGENTS.md`

> **A derived quantity must not outlive the scale it was calibrated at.**
> Single-linkage clustering, a `LIMIT 100` nearest-neighbour list, a
> "return the biggest one" stub and a mask-area-as-surface shortcut were all
> invisible at park scale (10²–10³ features) and all produced confidently wrong
> published numbers at AOI scale (10⁴–10⁵). When a code path first runs on an
> input an order of magnitude larger, its *constants* are the bug, not its
> logic.
