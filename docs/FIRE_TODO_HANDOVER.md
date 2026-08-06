# Fire Pipeline — Open Work (handover, updated 2026-08-06)

Only **open** items are listed. Completed work is documented where it lives:
`AGENTS.md` § "Fire pipeline health & consistency" and `docs/FIRE_PIPELINE.md` § v7.

**Always A/B algorithm changes** with `scripts/eval_fire_trajectories.py`.

---

## Done since the 2026-08-05 handover

| # | Item | Commit |
|---|------|--------|
| 1 | VIIRS SNPP/NOAA-21 backfill | finished 2026-08-05 10:25; both sensors through 2026-07-25. `fire_detections` = **42.9M rows**, db.sqlite3 14.4 GB |
| 2 | Full rebuild + consistency repair | see below — **0 drift across all 163 parks** |
| 5 | Admin staleness badge | `1835096` |
| 6 | Retire the duplicate raw JSON | `e517e66` (closes #15) |
| — | Two permanent-drift bugs the rebuild exposed | `9a255fb` |
| — | Untrack derived `fire_groups_v5/` (762 MB) | `b15e889` |

### The rebuild (item 2)

```
rebuild_nrt_parks.sh          163 parks, 2h42m,  35.4M fires -> 711,506 groups
load_fire_groups_to_db.py     711,506 rows in feature_geometries
precompute_narratives_v5.py   156 parks, 377,766 narrative refs
build_fire_grid_agg.py        day 9.9M / week 4.2M / month 1.7M cells, 135s
check_fire_consistency.py     Consistent. (was: 14,950 missing + 17,207 stale
                                           + 722 dup + 681 ambiguous + 7 dangling)
```

Group count went 181k → 711k because the backfill roughly tripled the detection
count (3 sensors instead of 1). Yearly split shows the sensor ramp clearly:
~48–52k groups/yr for 2020–2023, then 78k (2024), 80k (2025).

**Two bugs found, both only reachable when a park's rebuild yields zero groups**
(hence invisible to every incremental run) — fixed in `9a255fb`:

1. `load_fire_groups_to_db.py` returned early on an empty JSON *before* the
   `DELETE`, so stale `feature_geometries` rows were immortal (TZA_Rungwa: 111).
2. `precompute_narratives_v5.py` never touched parks that produced no
   narratives, so their stale cache rows pointed at dead feature_ids. They now
   get an explicit **empty v5 row** — deleting the row is wrong, because a
   cache miss drops `HandleAPIFireNarrative` into the deprecated Go slow path
   (17s, `feature_id: null`, the exact Single-Writer-Rule failure).

The 8 parks affected (CMR_Lobéké, CMR_Nki, GAB_Ivindo, GAB_Minkebe,
GAB_Monts_de_Cristal, NAM_Ai-Ais, ZAF_Richtersveld, TZA_Rungwa) are rainforest
or desert parks whose only groups sit 22–82 km outside the boundary, past the
20 km narrative cutoff. "No fire groups within 20 km" is the correct answer.

---

## 3. Hotspot-mask A/B (owed)

The mask is live but the earlier A/B is uninterpretable — it ran mid-backfill,
so `fires +44.9%` / `CAF_Dzanga_Park groups +1966%` measured the backfill, not
the mask. The DB is now **frozen** (backfill complete), so this is finally
runnable as written:

```bash
P=DZA_Djurdjura,ZWE_Hwange,CAF_Dzanga_Park,COD_Virunga,GAB_Lopé,ZMB_Kafue
python3 scripts/eval_fire_trajectories.py --snapshot data/eval/post_rebuild --parks $P
python3 scripts/rebuild_fire_trajectories_v5.py --parks $P \
    --no-hotspot-mask --output-dir data/eval/no_mask
python3 scripts/eval_fire_trajectories.py \
    --baseline data/eval/no_mask --candidate data/eval/post_rebuild --parks $P
```

Expect `frag_pct` and immortal-group counts to drop. Watch `DZA_Djurdjura`:
36 masked cells and the longest history, and it looked *worse* on
`frag_pct`/`mean_days` in the polluted run.

Note `data/eval/pre_mask` and `data/eval/mask` are from the polluted,
mid-backfill era — do not compare anything new against them.

## 4. Per-overpass slicing A/B — the point of the backfill

`USE_OVERPASS = False` in `rebuild_fire_trajectories_v5.py`. With one satellite
the night pass was 12× sparser (763 fires @ FRP 10.6 vs 63 @ 1.7), giving
`mean_days -21%`, `dup_pairs +23%`, `coverage -2.4%`. With all three sensors
(~6 passes/day) each slice should stand on its own — this is now testable:

```bash
python3 scripts/eval_fire_trajectories.py --snapshot data/eval/pre_overpass
python3 scripts/rebuild_fire_trajectories_v5.py \
    --parks CAF_Chinko,ZMB_Kafue,COD_Virunga,TZA_Serengeti,CMR_Nki,MOZ_Niassa \
    --overpass --output-dir data/eval/overpass
python3 scripts/eval_fire_trajectories.py \
    --baseline data/eval/pre_overpass --candidate data/eval/overpass
```

Flip only if `fires_per_grp` / `frag_pct` / `mean_days` / `coverage_pct` all
hold or improve.

## 7. NRT→SP reconciliation (largest, untouched)

FIRMS revises NRT detections weeks later via Standard Processing (better
geolocation + confidence). We ingest NRT and never re-fetch, so early rows keep
provisional coordinates forever. Re-ingesting SP would **duplicate rather than
update**:

```sql
UNIQUE(latitude, longitude, acq_date, acq_time, satellite)  -- raw REALs
```

Verified: stored precision is inconsistent — of July 2026 rows, 1,321,500 have
5 decimals, 132,413 have 4, 13,002 have 3, 1,390 have 2, 154 have 1. Any SP
coordinate revision changes the key → a second row for the same fire. `acq_time`
is stored unpadded (`'1'`, `'11'`, `'1246'` all occur), so it is not a stable
key component either.

Plan:
1. Rounded key columns (`lat5`, `lon5` at 5dp, `acq_hhmm` zero-padded) + unique
   index on `(lat5, lon5, acq_date, acq_hhmm, satellite)`. Migration must dedupe
   existing rows first — expect collisions. The table is now **42.9M rows** and
   the DB is 14.4 GB: back up first, budget a slow migration, watch disk
   (59 GB free).
2. `processing` column (`NRT`/`SP`) so SP can UPSERT over NRT.
3. Monthly re-fetch of T-60d..T-30d from the SP archive with
   `ON CONFLICT ... DO UPDATE WHERE processing='NRT'`.
4. Reuse `scripts/backfill_viirs_sensors.py` to fetch — it already handles the
   5-day cap and per-sensor SP/NRT availability windows.

SP/NRT ranges do not overlap (SNPP_SP ends 2026-04-27, NRT starts 04-28), so you
cannot diff the two for the same day to size the drift; validate on historical
dates instead.

## 8. Narrative richness audit (new, requested 2026-08-06)

Spot-checked after the rebuild and the *content* is intact — place names,
rivers, bearings and distances all survive:

- fire: `"Transhumance fire pattern 2020-11-04 to 2020-11-21 (18 days).
  detected ~4km outside park boundary. Traveled 184.2km SE. near Mbari (2.1km).
  Near Mbari river."`
- deforestation: per-event rows with `geojson_id` + `nearby_places`
  (`"22km south-southeast of Guérékindo"`, `"16km northwest of Chinko River"`).
- settlement: per-settlement `narrative` with classification, nearest place,
  GFW corroboration.

Open questions worth a pass now that the hovertip can show more:

- Deforestation entries are keyed `year` + `geojson_id`, and the park-level
  `summary` still says "across 273 recorded years" — the yearly rollup wording
  leaks into what is really a per-event list. Reword, and check the UI is
  linking the per-event `geojson_id` rather than the year bucket.
- Fire `origin_desc` / `dest_desc` / `entry_date` / `outcome` are emitted empty
  for `entirely_outside` groups; decide whether to populate or drop them.
- Settlement `direction` is `""` throughout — a bearing from the park centroid
  (or from the nearest named place) is cheap and would match the fire wording.

---

## Order

3 → 4 → 8 → 7.
