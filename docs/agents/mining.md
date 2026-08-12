# Mining detection — retired

_Split out of AGENTS.md. Read when working on this area._

## ⚠️ Mining detection is retired. Do not rebuild it.

**Verdict 2026-08-06** (`docs/MINING_FINDINGS_2026-08.md` §10, read §10.2 first):
optical 10 m ASM detection from our stack is closed. Hand-picked spectral
indices measure at chance vs confusers (§8, AUC 0.45–0.56). The Amazon Mining
Watch CNN does carry signal (§9.5, **AUC 0.781**, p=0.0004, sanity 0.995 so the
reproduction is sound) — but at zero sensitivity at its own threshold, and a
scan is ~77k patches/park, so precision lands at ~0.001. We'd need FPR ≤ 2.6e-4;
25 negatives can only resolve 0.04. **The AUC is fine and the base rate is
fatal** — the same failure as `data/mining_pits/*.json` (7,725 "sites", 0.1%
truth agreement).

Kill switch: `srv/mining_flag.go` → `MiningEnabled = false` (mirrored by
`MINING_ENABLED` in globe.html). Nothing deleted; flip both to restore.

**The line: mining *inference* stays, turbidity/pit *evidence* goes.**
Settlements classified `mining` from river proximity + deforestation shape +
fire absence + remoteness are ordinary context — same kind as `fishing` — and
still render. Removed: popup "Mining & Water Quality" accordion, star-report
block, animator `turb` chip, `mining_alert`/`turbidity_scan_*` notifications,
turbidity+pit terms in `scoreMining` (they were +1.0 of a 1.0 cap — one spurious
plume could mint a label), the sediment-plume narrative sentence, and the two
cron rotations. `/api/parks/{id}/turbidity` returns `{"disabled": true}`.
`analysis/gfw_alerts.py --rotate` **stays** (real canopy-loss data).

⚠️ **3,019 `park_settlements` rows are detector output, not settlements** —
`RegisterMiningCandidate` wrote pit/turbidity hits into the settlements table,
inflating the global count 11,485 → 14,504. **Any new settlement query must
apply `settlementFilterSQL(narrativeCol, polygonCol)`.**

### The note was not provenance, and something rewrote it (2026-08-12)

The filter used to be `scannerInjectedSQLFilter()` alone, keyed on the
`[Pit detection …]`/`[Turbidity …]` narrative prefix (not classification —
they're spread across all six classes). **A note is a string, and a string can
be rewritten.** It was:

`/api/refresh-park` runs `ClassifyParkSettlementsForce` nightly
(`scripts/daily_park_refresh.py`, cron 07:30), which regenerates `narrative`
from scratch. It skipped rows beginning `[Turbidity alert` — and **nothing** for
the 2,457 beginning `[Pit detection `. So every night the force pass rewrote a
pit detection's narrative into ordinary classifier prose ("Agricultural
settlement 16km north of Safari Ht Chinko") and the row walked straight through
the filter that existed to stop it. **495 rows had already been laundered**,
including all 79 in `CMR_Nki` — the park this repo's own test list calls
"pristine, 0 settlements", which is exactly the kind of check that should have
caught it and did not, because the number it produced was plausible.

The test is now the row's **ORIGIN**, which prose cannot rewrite:

| column | meaning |
|---|---|
| `polygon_ids` non-empty | clustered from observed GHSL built-up footprints in `feature_geometries` — this is a settlement |
| `polygon_ids` empty | inserted as a bare lat/lon by `RegisterMiningCandidate` — there was no observed built-up polygon to point at |

Verified exhaustively against `data/mining_pits/*.json` and
`data/turbidity/*.json`: of the 3,019 rows with no footprints, 2,483 still wear
the note, 495 were laundered, and 4 are pit-adjacent rows whose note was lost
the same way. **Zero** GHSL-derived rows lack footprints.

* `settlementSourceSQL(col)` — the origin test.
* `settlementFilterSQL(narrative, polygon_ids)` — origin **AND** note, and what
  call sites should use. Both are kept because they fail in opposite
  directions: a laundered note slips past the note check, and a legitimate
  cluster whose `polygon_ids` a future refactor dropped would slip past the
  origin check. A settlement should satisfy both.
* `scannerInjectedRow(narrative, polygonIDs)` — the Go predicate, where both
  columns are to hand.
* `classifyParkSettlementsImpl` now **excludes footprint-less rows from
  reclassification entirely**, so the laundry cannot run again. Fixing only the
  serving filter would have left the corruption accumulating behind it.

The general lesson, worth more than the mining context: **a derived flag must
be a property the pipeline cannot overwrite as a side effect.** If provenance
lives in a field some other job regenerates, it is not provenance, it is a
comment.

What survives and validates: the **basin layer** (`docs/agents/aoi.md`). Worth trying only
with new resources, per §10.2: Sentinel-1 SAR, sub-metre commercial imagery over
small AOIs, a few thousand labelled African chips to fine-tune AMW, or simply
ingesting IPIS field visits as *reported* sites.

`srv/turbidity.go` still reads `data/mining_pits/*.json` behind the flag; leave
it, don't extend it.

Background if you need it: `docs/MINING_FINDINGS_2026-08.md` §8 (why
hand-picked indices are dead), §9 (the AMW learned model and its 0.781),
`docs/MINING_REBUILD_HANDOVER.md` (rebuild history). Data source catalogue:
`docs/MINING_DATA_SOURCES.md`.

The 2026-08 rebuild produced a **negative result** for every hand-picked index:
mine pixels vs *confuser* pixels (villages, burn scars, river water, bare
savanna) score AUC 0.45–0.56 — rb is inverted. The earlier 0.75–0.81 AUCs only
measured "bare ≠ vegetation".

```bash
python3 scripts/eval_mining_detector.py --pixel-auc --n 25    # ~15 min, tmux
```

The one live thread is the **Amazon Mining Watch CNN ensemble**, run on our own
Sentinel-2 stacks without Earth Engine (`analysis/amw_model.py`, model in
`data/models/amw/`). **Measured 2026-08-06** (`docs/MINING_FINDINGS_2026-08.md` §9.5):

* pipeline sanity vs upstream held-out labels: **AUC 0.995** — our reproduction is sound
* 25 IPIS visited African gold mines vs 25 confusers: **AUC 0.781** (CI 0.635–0.899,
  p=0.0004), vs 0.450–0.555 for every spectral index on the same sets
* but **sensitivity 0.000 at upstream's 0.43 threshold** — all signal lives at
  1e-5–1e-1. Only the *ordering* transfers across the domain gap.

So it is a **ranker, not a thresholder**: percentile-rank within park, top-N to
human adjudication, no counts, nothing in the UI until adjudicated precision@N is
measured on real scan output. Burn scars/bare savanna are now easy; villages are
the remaining confusion. Needs `tensorflow-cpu==2.21.0` + `tf-keras`.

```bash
python3 scripts/eval_amw_model.py --africa 25 --jitter --workers 4   # re-measure
```

Truth sets are themselves under suspicion — `scripts/adjudicate_truth.py` renders
Esri contact sheets and records keyed verdicts (`pit`/`maybe`/`no_pit`/
`unclear_imagery`; the last is *not* the same as `no_pit`). The 8 Chinko manual
pits came back `unclear_imagery`: no z18 coverage exists there, and z16 shows only
wooded savanna.

What *does* validate and is shipped: the **basin layer** — `park_basins` +
`park_basin_parts` + `park_basin_rivers` (migrations 039/044, upstream polygon +
downstream trace + Strahler orders), `GET /api/parks/{id}/basin` (upstream km²,
upstream river km total/order-3+, downstream length + outlet river name,
`upstream_count`/`upstream_rivers`), pin buttons and a plain-language watershed
line in the popup's Roads/Rivers/Places section. QA with
`python3 scripts/check_basin_coverage.py`. Coverage <0.2 for 26
divide/endorheic parks is real, not a bug — hence `flow_corridor.scan_geom()`
scans (basin clipped to 200 km) ∪ park.

⚠️ **An area has several watersheds, not one.** `park_basins` is
`PRIMARY KEY (park_id, kind)`, so it holds only the *union* of every outlet's
watershed — which cannot say which river carries which lobe. `park_basin_parts`
(migration 044) keeps one row per outlet, named by its river; `?type=basin`
serves all of them, `&merged=1` asks for the old union. CAF_Chinko drains via
both the Chinko and the Mbari; `XSA_Study_Area` by 22 rivers.

`fetch_park_basins.py` needs **`--aoi`** for an AOI: it resolves ids against
`keystones_with_boundaries.json`, which an AOI is never in, so `--park <aoi-id>`
matched nothing, exited 0, and the AOI runner recorded "0 basin rows" as a
successful `done` for years of missing data. Outlet budget scales with area
(`outlet_budget()`); don't rank outlets by `ord_flow` alone (HydroRIVERS: lower =
bigger; OSM rows store 0 there and use `stream_order`, higher = bigger —
`_discharge_rank()` unifies them).

⚠️ **`ABS(<indexed col> - ?)` in a WHERE clause is a ~1000x slowdown.** It is
non-sargable, so SQLite drops the index and covering-scans all 42.9M
`fire_detections` rows (19.8 s vs 0.02 s, measured 2026-08-07). Always
`col BETWEEN ? AND ?`. Fixed in `settlement_classifier.go`,
`deforestation_classifier.go`, `turbidity.go`, `upload.go`,
`rebuild_events_{enhanced,from_polygons}.py`; grep before adding a query:
`grep -rn 'ABS([a-z_]* - ?)' srv/ scripts/`. In a SELECT list it is harmless —
only a WHERE term on an indexed column matters.

---
