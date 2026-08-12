# Agent Instructions - 5MP Conservation Monitoring

## Quick Context

Go web app for conservation monitoring of 162 African protected areas.
Interactive 3D globe with fire detection, deforestation, settlements, patrol tracking.

**Live URL:** https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026

## 🔍 Quick Navigation for Agents

**First time? Start here:**
1. `docs/DATA_FLOW.md` - How data moves through the system
2. `docs/QUICK_TASKS.md` - Copy-paste solutions for common tasks
3. `docs/ARCHITECTURE_DECISIONS.md` - Why things are built this way

**Working on specific features?**
- Fire system: `docs/FIRE_PIPELINE.md` + `docs/FIRE_DATA_FLOW.md`
- API changes: `docs/API.md` + `docs/QUICK_TASKS.md` section 1
- Frontend UI: `docs/SHELLEY_PROMPT_UI.md` + `docs/DATA_FLOW.md` section 4
- Database: `docs/DATABASE.md` + `docs/QUICK_TASKS.md` section 9

**Key Insight**: This is a 17K-line single-page app. Don't try to understand everything at once.
Use the data flow maps to find the specific files you need to modify.

---

## ⚠️ DATABASE PROTECTION

**The database has 5.7M+ fire records. DO NOT:**
- Run DELETE/DROP without confirmation
- UPDATE without WHERE clause
- Truncate any tables

**ALWAYS:**
- Use LIMIT when exploring
- Back up before schema changes: `cp db.sqlite3 db.sqlite3.bak`

---

## Key Files

| File | Purpose |
|------|--------|
| `cmd/srv/main.go` | Entry point |
| `srv/server.go` | HTTP routing |
| `srv/templates/globe.html` | Main UI (single-page app) |
| `srv/api.go` | API endpoints |
| `srv/narrative_handlers.go` | Fire/deforestation/settlement narratives |
| `srv/fire_realtime_handlers.go` | NRT fire analysis |
| `srv/enhanced_narratives.go` | Context-aware narrative generation |
| `srv/upload.go` | GPX upload handlers |
| `srv/upload_queue.go` | Async upload processor |
| `db.sqlite3` | SQLite database (~1.8GB) |

---

## How to Run

The server runs as a **systemd service** (`5mp.service`):

```bash
# Build and restart
make build && sudo systemctl restart 5mp

# Check status / logs
systemctl status 5mp
sudo journalctl -u 5mp -f
```

**Build details:**
- `make build` embeds git commit hash as version (shown in footer)
- Generates `.git-commits.txt` for version history modal (click version in UI)
- Version passed via `-ldflags "-X srv.exe.dev/srv.Version=$(VERSION)"`

Access: http://localhost:8000/?pwd=test2026

### Systemd Service (`5mp.service`)

The service auto-restarts on crash. Environment variables (e.g. `ZENODO_TOKEN`) are
configured in `/etc/systemd/system/5mp.service`. After editing, run:

```bash
sudo systemctl daemon-reload && sudo systemctl restart 5mp
```

### ⚠️ IMPORTANT: Keeping Version Up-to-Date

**ALWAYS rebuild after making changes to show the correct version:**

```bash
# After any code changes or git commits:
make build && sudo systemctl restart 5mp
```

**Why this matters:**
- Version shown in UI footer = git commit hash from build time
- Users can click version to see recent changes
- Old binary = stale version = confusion about what code is running

**Quick version check:**
```bash
# Check current running version:
curl -s "http://localhost:8000/api/version?pwd=test2026" | jq -r '.version'

# Check latest git commit:
git rev-parse --short HEAD

# If they don't match, rebuild:
make build && sudo systemctl restart 5mp
```

---

## Data Files

JSON data files in `data/` provide precomputed data:

| Directory | Files | Description |
|-----------|-------|-------------|
| `data/fire_groups_v5/` | 162 | Fire groups with v5 trajectories |
| `data/export/fire_narratives/` | 162 | Pre-computed fire narratives per park |
| `data/settlement_events/` | 156 | Classified settlements |
| `data/deforestation_events/` | 79 | Classified deforestation events |
| `data/rivers/` | 161 | HydroRIVERS data per park |
| `data/roads_heigit/` | 159 | Road surface data from HeiGIT |
| `data/osm_places/` | 91 | OSM place names |
| `data/climate/` | 1 | Monthly precipitation, seasons |
| `data/species/` | 1 | IUCN mammal species |
| `data/waterbodies/` | 137 | Global waterbody polygons |
| `data/export/` | 3 | Narrative summaries |

---

## Key APIs

```bash
# Park stats
curl "http://localhost:8000/api/parks/COD_Virunga/stats?pwd=test2026"

# Fire narrative (from cache)
curl "http://localhost:8000/api/parks/COD_Virunga/fire-narrative?pwd=test2026"

# Fire realtime (groups/trajectories)
curl "http://localhost:8000/api/parks/COD_Virunga/fire-realtime?pwd=test2026&days=28"

# Climate data
curl "http://localhost:8000/api/parks/COD_Virunga/climate?pwd=test2026"

# IUCN species
curl "http://localhost:8000/api/parks/COD_Virunga/species?pwd=test2026"

# Feature GeoJSON (fire, settlement, deforestation, waterbody)
curl "http://localhost:8000/api/parks/COD_Virunga/features?type=fire_trajectory&pwd=test2026"

# Fire alerts
curl "http://localhost:8000/api/fire-alerts?pwd=test2026&limit=10"

# Grid data with filters
curl "http://localhost:8000/api/grid?pwd=test2026&type=foot,vehicle&bbox=28,-6,37,2"

# Async upload
curl -X POST "http://localhost:8000/api/upload/async?pwd=test2026" -F "gpx=@file.gpx"
```

---

## Database Stats

| Table | Records | Description |
|-------|---------|-------------|
| fire_detections | 42.9M | VIIRS satellite fires, 3 sensors (2018-2026) |
| feature_geometries | 997K | GeoJSON polygons/lines |
| fire_narrative_cache | 162 | Precomputed fire narratives (v5) |
| park_rivers | 215K | HydroRIVERS data (161 parks) |
| park_settlements | 9,933 | Classified settlement clusters |
| osm_places | 271K | Place names for narratives (161 parks) |
| park_climate | 162 | Monthly climate/seasons |
| park_species | 39.5K | IUCN mammal species |

**Feature geometries by type:**
- fire_trajectory: 711,506 (2020-2026, v7 rebuild 2026-08-06)
- deforestation: 221,277 (2001-2024)
- settlement: 64,016
- road: 26,550

---

## Background Workers

1. **Upload Queue** - Processes GPX uploads async (every 2s)
2. **GPX Learner** - Pattern detection from uploads
3. **Fire Daily Cron** - `scripts/daily_fire_update.py` (3am UTC)
   - Downloads NRT fires from FIRMS API
   - Updates fire_detections (upsert)
   - Rebuilds groups for affected parks
   - Updates narrative cache
4. **Narrative Cache Worker** - Pre-computes narratives (weekly full refresh)

---

## Test Helpers (test=1 mode)

Add `?test=1` to URL to enable advanced testing tools:

**Entry ID Badges:** Blue numbered badges (0, 1, 2...) on fire/deforestation entries

**Key TEST Functions:**
```javascript
// Navigate & inspect
TEST.scrollToEntry('deforestation', 50)  // Scroll to entry by ID
TEST.scrollToText('fire', 'safari')      // Find by content
TEST.inspectEntry('fire', 10)            // Show full details
TEST.findBrokenEntries('deforestation')  // Scan for issues

// Manipulate UI
TEST.expandAll('CAF_Chinko')             // Expand all accordions
TEST.setPopupHeight(2000)                // Resize popup
TEST.triggerLoadMore('deforestation', 'CAF_Chinko')  // Click load more

// Shortcuts
TEST.testDeforest('CAF_Chinko', 100)    // Scroll+inspect+click entry
TEST.getEntryCount('fire')               // Count entries
```

**Benefits:** 50%+ token reduction in debugging (direct access replaces manual scrolling/clicking)

**Docs:** `docs/TEST_HELPERS.md`, `docs/TEST_HELPERS_QUICK_REF.md`

---

## Test Parks

- **COD_Virunga** - Full data coverage, Mountain Gorillas
- **CAF_Chinko** - Detailed fire trajectories
- **CMR_Nki** - Pristine (0 settlements)
- **TZA_Serengeti** - Well-documented ecosystem

---

## Credentials

| Type | Value |
|------|-------|
| App Passwords | see `secrets.env` (gitignored; template: `secrets.env.example`). Docs/tests use `test2026`. |
| Admin | see `secrets.env` |
| AOI owner | `$AOI_OWNER_PWD` in `secrets.env` |

**Never write a live password into a tracked file** — not into docs, not into a
verification snippet, not into a comment. Add a named variable to `secrets.env`
(+ a placeholder in `secrets.env.example`) and refer to it by name; snippets
start with `source secrets.env`. Only `test2026`, the shared demo password, is
written literally.

```bash
# before committing docs
grep -rn "$AOI_OWNER_PWD" --include="*.md" --include="*.py" --include="*.go" .
```

---

## Remote Database Backups

See `BACKUP_INFO.md` for full details and restore instructions.

### Latest: Zenodo Draft (April 1, 2026)

| Field | Value |
|-------|-------|
| **Deposition ID** | `19363779` |
| **State** | Draft (unsubmitted, no public DOI) |
| **Draft URL** | https://zenodo.org/deposit/19363779 |
| **File** | `5mp_db_backup_20260401.sqlite3` (1.2 GB) |
| **MD5** | `d17ef446b03f58b5fdd1cb527dcd3088` |
| **Manifest** | `data/db_backup_zenodo_manifest.json` |

```bash
# Download (requires ZENODO_TOKEN - draft is not public)
curl -H "Authorization: Bearer $ZENODO_TOKEN" \
  "https://zenodo.org/api/files/4bd66ea4-80b9-45f9-af7b-4237c268844a/5mp_db_backup_20260401.sqlite3" \
  -o 5mp_db_backup_20260401.sqlite3

# Create new backup:
ZENODO_TOKEN=... go run ./cmd/backup-zenodo/
```

### Previous: exe-dev-monitor-peer01 (March 2, 2026)

```
File ID:  c8de734b-ad0e-4c25-b5bb-6e4ddef3f847
Token:    $BACKUP_PEER_TOKEN (see secrets.env)
```

```bash
curl -H "Authorization: Bearer $BACKUP_PEER_TOKEN" \
  https://exe-dev-monitor-peer01.exe.xyz:8000/api/download/c8de734b-ad0e-4c25-b5bb-6e4ddef3f847 \
  -o db_backup_20260302.sqlite3
```

---

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
`--snapshot`/`--baseline`/`--candidate`). The builder's ablation flags
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

⚠️ **2,562 `park_settlements` rows are detector output, not settlements** —
`RegisterMiningCandidate` wrote pit/turbidity hits into the settlements table,
inflating the global count 10,390 → 12,952. They're excluded by
`scannerInjectedSQLFilter()`, keyed on the `[Pit detection …]`/`[Turbidity …]`
narrative prefix (not classification — they're spread across all six classes).
**Any new settlement query must apply that filter.**

What survives and validates: the **basin layer** (see below). Worth trying only
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

## Areas of interest (AOI)

A user-drawn polygon promoted to a first-class object: arbitrary geometry, a
fixed analysis window, an owner, and data fetched *for it* over days by a cron.
Instance #1 is `XSA_Study_Area` (485,150 km², owner `$AOI_OWNER_PWD`
in `secrets.env` — never spell a live password into a tracked file).
Full handover: `docs/PLAN_AOI_OVERLAY.md`.

**Current handover: none.** `docs/PLAN_AOI_OVERLAY.md` is the design rationale
and the measured-facts record; everything operational is below. The AOI work is
complete — read path, write path, queue, exports, animation, focus mode, abort,
versioning, star report and the admin Access tab are live, and all 11 datasets
for `XSA_Study_Area` are `done`:

    3.18M detections -> 38,725 trajectories · 2.23M GFW alerts -> 696 events
    76,903 Hansen polygons -> 7,079 events · 74,904 built-up polygons -> 1,552 settlements
    3,169 waterbodies · 11,370 rivers + 2,530 lakes · 3 country PBFs
    22 upstream watersheds + 24 downstream traces · 12,956 roads · 690 places

`park_basin_parts` is backfilled for **all 164 areas** (883 rows, 0 unsplit).
Highest migration is **051**.

### Verification set — re-run after any AOI work

```bash
source secrets.env; P="$AOI_OWNER_PWD"   # never hard-code the password
python3 scripts/aoi_runner.py --status                             # all done, no lease
curl -s "localhost:8000/api/aois?pwd=test2026"    | jq '.count'    # 0
curl -s "localhost:8000/api/aois?pwd=$P"          | jq '.count'    # 1
curl -s -o /dev/null -w '%{http_code}\n' \
  "localhost:8000/api/parks/XSA_Study_Area/stats?pwd=$P"           # 404 (park route must 404)
curl -s "localhost:8000/api/aois/XSA_Study_Area/basin?pwd=$P" \
  | jq '{upstream_count, downstream_count}'                        # 22, 24
curl -s "localhost:8000/api/notifications?type=aoi_progress&pwd=test2026" \
  | jq '.notifications|length'                                     # 0  <- privacy
curl -s "localhost:8000/api/admin/access?pwd=test2026" | jq '.aois|length'   # 0  <- privacy
python3 scripts/check_fire_consistency.py                          # Consistent.
python3 scripts/test_aoi_resume.py                                 # proves resumability
go test ./srv/ -run 'TestGeoMemo|TestNarrativeSourceRev'           # cache equivalence
./tests/run_all.sh                                                 # db 37, api 45, ui 20
```

`go test ./srv/` fails on `TestServerSetupAndHandlers`
(`035-test-env.sql: no such column: avg_speed_kmh`). **Pre-existing, unrelated
to AOI** — verified by stashing. Do not chase it as an AOI regression.

### The recurring failure mode: a no-op that reads as an answer

Five times, days of ingest were silently unreachable or unfetched while every
layer reported success. The tell is always **a number suspiciously round for its
input size** (0 watersheds for 485,000 km²; 141 roads for three countries), and
the cause is always a filter that matched nothing while exiting 0. Two rules
fell out and are load-bearing:

1. **A unit that produces nothing for a large input must report unfinished**, so
   the queue retries instead of freezing a wrong answer as `done`
   (`run_basin` returns `ok = (rows > 0)`).
2. **An `aoi:` prefix only the exclusion filter understands is a write-only
   key.** Bare AOI id in park-shaped tables, plus `aoiExcludeSQL()`.

The sixth variant was a *timeout* reading as an empty section — see "Narrative
caching" below.

### API surface

`/api/aois/{id}/*` = the park handlers wrapped in `aoiGate()`; one visibility
check covers them all.

```
GET    /api/aois                       list (+ can_create)
GET    /api/aois/search?q=              incl. archived; the only way back to one
GET    /api/aois/{id}                   metadata + per-dataset coverage
GET    /api/aois/{id}/versions          the lineage
GET    /api/aois/{id}/progress          live, no-store
GET    /api/aois/{id}/{fire-narrative,fire-trend,fire-realtime,features,
        deforestation-narrative,settlement-narrative,feature-stats,
        classified-settlements,classified-deforestation,
        settlement-intensity,infrastructure,basin}
GET    /api/aois/{id}/export.{geojson,kml,locus}
POST   /api/aois/{id}/export.gpkg       everything, typed + styled for QGIS (a job)
POST   /api/aois/{id}/mbtiles           + GET .../mbtiles/estimate
POST   /api/aois/estimate               side-effect free; call it while dragging
POST   /api/aois                        create + seed queue (runs nothing)
POST   /api/aois/{id}/{edit,restore,refresh,kick,archive,cancel}
POST   /api/aois/{id}/rename            label only: keeps the id, forks nothing
POST   /api/aois/{id}/refresh?resume=1  the inverse of cancel
DELETE /api/aois/{id}
GET    /api/admin/access                Access tab: ownership + queue (scoped!)
POST   /api/admin/aoi-dataset           enable/disable one dataset (owner-only)
```

**Deliberately absent, a decision not a gap**: `stats`, `species`, `climate`,
`publications`, `legal`, `checklist`, `turbidity`. Per-protected-area facts;
averaging them over 485,000 km² would invent a number. The popup and the report
say so and point at the intersecting parks; `fetchParkReportData()` and
`fetchPopupRoadData()` skip them rather than eating 404s.

More load-bearing details, each already got wrong once:

* **No endpoint runs the ingest.** `scripts/aoi_runner.py` owns lease
  discipline; `kick` shells out to it. One implementation of "work a unit".
* **`DELETE` order**: the `aois` row goes *last* — while it exists,
  `aoiExcludeSQL()` still masks derived rows not yet deleted. The delete list
  must include `roads_heigit`, `park_rivers_hydro`, `park_lakes_hydro`,
  `park_waterbodies`, `park_basins`, `park_basin_parts`, `park_basin_rivers`.
* Non-owners get **404, not 403** (`requireAOIOwner`) — an id must not be an
  oracle. Same reason `?aoi=` on a raw-geography endpoint is *ignored* when
  invisible rather than refused.
* `validateAOIGeom` caps at 2,000 vertices (re-parsed by every runner, traced by
  the animator's canvas clip every frame).

### Narrative caching — one approach for parks and AOIs

`HandleAPIDeforestationNarrative` enriches **every** event with nearby places,
rivers and roads: ~21 ms of queries per row. A park has hundreds of events
(CAF_Chinko 273 → 1.0 s) so it was never visibly slow; XSA has 7,815 → **2 m
27 s**, past the 120 s `WriteTimeout`, so the AOI popup's deforestation section
rendered as if the area had no data. The AOI did not break the handler, it made
an existing O(events) cost visible. Two fixes, both shared by parks and AOIs:

1. **`narrative_cache`** (migration 045, `srv/narrative_cache.go`) — the same
   cache-first shape as `fire_narrative_cache`, but **self-invalidating**:
   `source_rev` is `COUNT + MAX(id)` of the source rows, so any rebuild
   (python, the AOI runner, a manual reclassify) invalidates it without
   knowing the table exists. That is deliberately *not* the fire cache's
   Single Writer Rule: the fire cache holds v5 hash feature_ids only the python
   builder can mint, whereas this holds a pure function of `deforestation_events`
   that any reader can recompute. Keyed by `park_id`, and an AOI id **is** a
   `park_id` in every park-shaped table, so one code path serves both.
   `params` is the date window; only the 6 most recent per (park, kind) are
   kept, or slider-dragging grows a 1.8 GB database by 10 MB a time.
2. **`geoMemo`** — answers "what is near this point" per 0.25° cell instead of
   per event, by fetching a superset window once and filtering in Go. **Exact,
   not approximate**: `TestGeoMemoMatchesDirectQueries` compares 200 random
   points across 4 areas against the direct queries. Where exactness cannot be
   promised (the rivers query's `LIMIT`, if the superset itself truncates) the
   memo *declines* and the caller runs the original query.

Result: 2 m 27 s (timeout) → 10.5 s cold → 0.09 s warm, park output
byte-identical. **Any new per-event enrichment must go through the memo**, and
any new expensive narrative should take the `narrativeSourceRev` /
`getCachedNarrative` / `putCachedNarrative` route rather than inventing a
second cache.

### Geography layers are served whole and cached

`river`, `road`, `place` and `waterbody` on `/features` are **static per
ingest**, carry no date filter, and are the whole answer or nothing — pinning
"rivers" means the river network, not its 500 longest reaches. They used to cap
at 500 default / 2,000 max, which for a park was invisible and for
`XSA_Study_Area` silently dropped 17k of 19k rivers and 11k of 13k roads. It was
never AOI-only: `DZA_Ahaggar` has 32,977 river reaches, `DZA_Djurdjura` 46,866
roads, `COD_Virunga` 11,107 places — every enriched park was truncated too.

Fixed 2026-08-07 in `srv/feature_geo_cache.go`: whole layer by default, gzipped
into `narrative_cache` under `kind='features:<type>'` with the same
self-invalidating `COUNT+MAX(id)` `source_rev`, plus an ETag so a re-pin is a
304. XSA river: 34 MB, 2.2 s cold -> 0.47 s warm -> 0.004 s revalidated.

* **`&limit=5000` means "everything"**, not a cap (`geoFeatureWholeLimit`). It
  was only ever the old ceiling, and old share links and pinned-layer restores
  still send it. Only a deliberately *small* limit bypasses the cache — caching
  a truncated answer under the full key would poison it.
* **`geoFeatureSources` maps a type to every table its output depends on.**
  `place` lists three because it suppresses a place point whose name a river or
  road line already carries (OSM records "Chinko" both as a waterway node and
  on the reaches, so the map drew a village dot on the labelled river). A
  roads re-ingest therefore changes the *places* answer.

### Detail tiers: `major` / `main` / `all`

Serving the whole layer is honest but rarely what you want on screen: XSA's
road layer is 6,458 footpaths and 3,642 tracks around 114 trunk/primary roads,
and its river layer 14,011 order-1/2 headwater stubs around 549 major reaches.
At continental zoom that is a blue smear that hides the things it is drawn to
show. `?detail=` on `/features` picks a tier for `river`, `road` and `place`
(`geoDetailSQL` in `srv/feature_geo_cache.go`); `/infrastructure` returns
`summary.detail_counts` so a button can print the number of features it will
actually draw.

* **A tier is a WHERE clause, never a LIMIT.** It must be the *same* subset
  every time and at every zoom, or a share link stops reproducing a picture.
  That is also why there is no zoom-driven auto-tier.
* **Rivers key on `stream_order`, not on having a name.** 78% of XSA's order-4
  reaches are unnamed and an unnamed Nile tributary is still a major river.
  Works across both sources because `osm_hydro.py` maps river=4/canal=3/
  stream=2 onto the same scale as HydroRIVERS' Strahler.
* **Roads key on `highway_type`**, the only classification present for every
  row — HeiGIT's `surface`/`dl_class_2024` is null for the OSM-enriched
  majority.
* Each tier is a **separate cache row** (`params=<tier>`, `''` for `all` so the
  pre-existing rows stay valid) with its own ETag.
* Unknown or absent `detail` is `all`, never a 400: old share links and pinned
  layer restores predate the param.
* UI: one segmented control (`.geo-detail-seg`) above the Rivers/Roads/Places
  buttons it governs — one, not three, because the tiers mean the same thing
  for all three and the whole point is that two areas on screen agree. Global
  (`window.geoDetail`, default **`main`**), rides in the share link as
  `?detail=`, omitted at the default. `setGeoDetail()` re-pins affected layers
  in place rather than making the user unpin and pin again, and
  `restorePinnedFromURL()` reads `detail` **before** fetching any layer.

### `polygon_ids` LIKE joins are the same trap as `ABS()`

`park_settlements.polygon_ids` and `deforestation_events.polygon_ids` are
comma-separated lists of `feature_geometries.feature_id`. Joining on them in SQL
means `(',' || polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')`, which
pairs *every* event with *every* polygon and runs a string search on each: park
scale (hundreds x thousands) hid it, XSA scale did not. Pinning "all
settlements" from the AOI popup took **29 s** (1,552 x 74,904) and all
deforestation **13 s** (7,815 x 80,408) — the same class of failure as the
non-sargable `ABS()` above, and with the same tell: fires, which don't join,
were instant.

Fixed 2026-08-07 in `srv/feature_meta.go` — one scan of the small events table,
split in Go into a `feature_id -> meta` map. 0.08 s / 0.14 s, output
**byte-identical** for CAF_Chinko, COD_Virunga and XSA. `event_id=` likewise
resolves `polygon_ids` first and uses `IN (...)`. Don't reintroduce the join:

```bash
grep -rn "polygon_ids || ','" srv/
```

Two things that look like AOI bugs and are not: `settlement-intensity` returns
an empty FeatureCollection (`settlement_intensity` has rows for 3 areas total —
parks included), and `feature-stats.road_segments` is 0 because roads live in
`roads_heigit`, not `feature_geometries` (CAF_Chinko reports 0 too). Both are
pre-existing park behaviour, identical for AOIs.

An AOI is a **power bounding box**: kept, owned, versioned, with data fetched
*for it* over days — as opposed to "Select Area", which is a disposable filter
over data we already hold. The UI keeps them in separate filter sections for
exactly that reason; don't merge them back.

Two traps that cost time: handlers needing geography must use
`resolveAreaGeom(id)` (an AOI is never in `AreaStore`, so the old loop yields
an empty boundary and KML silently loses its patrol effort), and any query over
`notifications` without an explicit park_id needs `aoiNotifSQLFilter()` — an
`aoi_progress` row is keyed by the AOI id and carries its name.

**An AOI is not a park.** Own table (`aois`), own id space, own route prefix.
Never in `keystones_with_boundaries.json`, never a
`fire_detections.protected_area_id` — otherwise `park_assigner` reassigns
detections away from the four parks XSA overlaps. `--aoi` on the v5 scripts
injects it into the **in-memory** parks dict only.

But its *derived* rows do live in park-shaped tables (`feature_geometries`,
`park_settlements`, `osm_places`, …) keyed by the AOI id. Two consequences,
both load-bearing:

1. `ParkIDMiddleware` 404s on any id in the AOI set, so `/api/parks/{aoi}/*`
   cannot serve a private AOI unchecked. AOI data is reachable only through
   `/api/aois/*`, where `aoiGate()` applies one visibility check to the
   otherwise-unmodified park handlers. In the frontend, **always build these
   URLs with `apiBase(id)`**.
2. **Any query over those tables that does not take an explicit `park_id` must
   apply `aoiExcludeSQL(col)`** (`srv/aoi.go`) — the same shape as
   `scannerInjectedSQLFilter()`. Without it, bbox-keyed endpoints leak private
   rows *and* double-count the AOI over the parks it overlaps.
3. **Four writers share `(park_id=<aoi>, feature_type)`** in
   `feature_geometries`: `aoi_clip.py` (settlement/deforestation preview from
   neighbouring parks), the v5 fire chain (`fire_trajectory`), the
   `deforestation` unit (`deforest_gfw_%`, derived from the AOI's own GFW
   alerts — not Hansen), and the `ghsl` unit (`settlement_ghsl_%`, from
   built-up-surface tiles). Each is only safe because it deletes a **disjoint
   id prefix**. A fifth writer needs the same treatment. A layer listed in
   `aoi_clip.SUPERSEDED_BY` is also no longer clipped once its real ingest is
   `done` — the real ingest covers the whole polygon *including* the ~10%
   inside parks the preview stood in for, so keeping both double counts.

GHSL settlement polygons come from `scripts/ghsl_tiles.py` (R2023A E2030 100 m,
cached **by tile id** in `data/ghsl/tiles/`, shared by parks and AOIs alike).
The JRC tile grid is **1-indexed**; an off-by-one silently reads a window
2,000 km away. The 10 m product is not published as tiles.
`rebuild_events_enhanced.rebuild_settlements_for_park()` is the one clusterer +
classifier for a single park or AOI — don't write a second one.

UI: an AOI animates as its **polygon** (`Animator.open({aoi})` → `clipGeom`),
not its bbox; share links use `?aoi=`/`aoi_sections=`/`anim_aoi`, deliberately
separate from `?popup=`/`sections=` which resolve against the `areas` source an
AOI is never in. Anything wanting a specific date window must go through
`setTimeSliderRange()`, not `dateFrom`/`dateTo`.

**Focus mode** (`?aoi_focus=`) makes the AOI the subject of the whole map: parks
outside it are dimmed (never hidden — the outline shows the polygon crossing a
boundary), **starred parks are never dimmed** (a star is explicit and outranks
an implicit scope), and the bbox feature layers, the animator and the star
report all switch to the AOI's own rows via `aoiScopeSQL`. `aoiFocusBrightIDs()`
returns `null` — not `[]` — when the park list did not resolve, or the whole
world greys out. `var aoiFocusID`, not `let`: `updatePAHighlighting()` reads it
during map setup, thousands of lines above the declaration.

**archive ≠ cancel ≠ delete ≠ supersede.** `archive` hides the overlay and
touches nothing else — ingest keeps running, so unhiding shows an answer rather
than a progress bar. `cancel` disables unfinished datasets but keeps their
**cursors**, so `refresh?resume=1` resumes without re-spending FIRMS quota.
`delete` drops everything. An **edit forks**: v1 is archived and its queue
disabled, which looks identical to a cancel from outside — so `/progress`
reports `state:"superseded"` and `refresh?resume=1` **409s**, because resuming it
would re-spend days of quota on a question v2 already replaced.

`archive` works, verified end to end in 17 ms. The earlier "blocker" was never a
handler bug: `rebuild_deforestation_for_park` was **CPU**-bound on a
non-sargable `ABS()`, not waiting on the write lock. Before blaming SQLite's
single writer, check `ps` — a process waiting on a lock is `S`, not `R`.

Old `aoi_progress` rows keyed `park_id='SYSTEM'` leaked every private AOI's name
to every principal (`aoiNotifSQLFilter` reads visibility from `park_id`). Fixed
by `reownSystemAOIProgress()`, a **warn-and-continue startup fixup** in
`srv/aoi.go` — emphatically not a migration: as one it failed `NewServer` when
the write lock was held and systemd restart-looped the service. A privacy
tidy-up must never be able to take the site down.

The runner treats **interruption as its normal exit**: out of time, Ctrl-C or
SIGTERM all release the lease and resume next run with no cooldown, dead-pid
leases self-heal, and bookkeeping writes wait out the v5 chain's long hold on
SQLite's single writer. Never run two units concurrently — that is what
stranded three leases on 2026-08-07. `scripts/test_aoi_resume.py` proves the
guarantee; run it after any change to the lease/cursor code.

Pre-2024 deforestation for an AOI comes from **Hansen**, not GFW alerts
(`scripts/hansen_loss.py`, wired as the `hansen` unit 2026-08-07): tiles are
45-116 MB COGs read through `/vsicurl` in 0.6 s per 2-degree window, no
download and no quota, while GFW integrated alerts only start in 2024. Cutover
is Hansen <=2023 / alerts >=2024, matching the parks exactly so the numbers stay
comparable. **Onboarding a park runs it too** — before this, a new park showed
two years of loss beside 161 parks showing twenty-four.

Two traps the plan for it did not mention: the unit costs **~50 s per 2-degree
window**, not the 0.6 s of the read (the cost is polygonising the mask), and
polygons alone are invisible — the popup and narratives read
`deforestation_events`, so it finishes by clustering through
`EventRebuilder.rebuild_deforestation_for_park(park, id_prefix=...)`, the mirror
of `rebuild_settlements_for_park`. `id_prefix` scopes read *and* delete, so
Hansen and the GFW unit own events in one table for one park without erasing
each other.

**`gsw` and `hydro` now have runners** (2026-08-07), so every dataset does.
`gsw` = `scripts/gsw_water.py`: the "missing" JRC occurrence tiles are public
COGs, `/vsicurl` reads a 1-degree window in 0.55 s. It writes the parks' own two
`waterbody_type` values ('Inland perennial' >=75%, 'Inland intermittent'
25-75%) under a `gsw_` id prefix, so exports and narratives need no second path.
`hydro` = `scripts/osm_hydro.py`, because **HydroSHEDS cannot be fetched
unattended at all** — `data.hydrosheds.org` 403s every request behind
Cloudflare. It fills `park_rivers_hydro`/`park_lakes_hydro` from the country PBF
the `osm` unit already downloads, with **negated OSM ids** (HydroSHEDS ids are
positive, so `< 0` is provably ours) and a tag-derived `stream_order` band, not
Strahler. It is **not** the `basin` unit: mghydro/MERIT answers "what drains
through here" and carries no river names; OSM answers "what is this called",
which is what the narratives and KML folders key on. Both ship.

```bash
python3 scripts/aoi_runner.py --status          # queue state
python3 scripts/aoi_runner.py --heal            # reclaim dead-pid leases
python3 scripts/aoi_clip.py --aoi XSA_Study_Area  # Phase A preview, ~4s
# cron: 0 12 * * *  aoi_runner.py --daily  (deliberately far from the 3am fire job)
```

**Every long writer now yields.** `rebuild_{deforestation,settlements}_for_park`
and the v5 fire chain (`load_fire_groups_to_db.py` every `BATCH_ROWS = 200`
groups, `precompute_narratives_v5.py` every 25 cache blobs) commit in batches, so
SQLite's one writer is free between them and a user toggle can always get a slot.
Safe because both are idempotent: the run deletes its own rows first and every
insert is an `INSERT OR REPLACE` keyed by id.

**Admin → Access tab** (`srv/aoi_admin.go`, `GET /api/admin/access`,
`POST /api/admin/aoi-dataset`) shows AOI ownership and per-dataset queue state,
plus enable/disable and "Run now". It is **scoped to the caller's principal**, not
global: `RequireAdmin` is satisfied by any valid password, so a global view would
leak every tenant's polygons. `principals.label` (`pwd[:3]+"…"`) is never served —
the handle is the non-secret `sha256(pwd)[:8]`.

**`aois.state` is never `'ready'`** (only `archived`). Readiness is *derived* from
the queue — `/progress` and the Access tab both do this. Printing the raw column
labels a fully ingested AOI "pending" forever.

### Frontend map

| piece | where |
|---|---|
| routing | `apiBase(id)` in globe.html; `window.AOI_IDS` filled by `loadAOIs()` |
| map layer + popup | globe.html `loadAOIs`/`showAOIPopup`/`aoiCoverageHTML`/`renderAOIOverviewHeader` |
| actions | `aoiActionsHTML(id, name, {isOwner, archived})` — one row, used by tip *and* popup |
| export menu | globe.html `toggleAOIMenu`/`aoiExportMenuItems` — `#aoi-menu` on `<body>` |
| rename | globe.html `startAOIRename` / `renameAOITag` → `POST /api/aois/{id}/rename` |
| filter section | globe.html `#aoi-section` — own heading, amber, own visibility toggle |
| editor | `srv/static/aoi_draw.js` — `AOIDraw.start()` / `.startEdit(id, name, geom)` |
| progress card | `srv/static/aoi_progress.js` — `AOIProgress.cardHTML(notif)` |
| animation | `Animator.open({aoi})` clips to the **polygon**; loaders append `&aoi=` |
| focus | globe.html `setAOIFocus`/`toggleAOIFocus`/`aoiFocusBrightIDs`/`applyAOIFocusPaint` |
| report | `collectReportParks()` folds in every visible AOI, first; visible = starred |
| admin | globe.html `loadAccessTab`/`setAOIDataset`/`kickAOIRunner` → `srv/aoi_admin.go` |

Things that will bite:

* **`window.map` is the `<div id="map">` ELEMENT** (named access on window). Use
  the bare lexical `map`. Symptom: `m.getSource is not a function`.
* **A `const` in one `<script>` block is invisible to another.** globe.html has
  several; `AOI_REPORT_SECTIONS` is mirrored onto `window` for that reason.
* Focus paint is layered **on top of** selection paint, not woven into it;
  `resetAOILayerPaint()` exists because the paint is not idempotent — re-apply
  after a basemap change (`updateParkFillForBasemap` owns `fill-opacity` too).
  Dim colour `#5b6b5f` / 0.55, **not** `#3f3f46` / 0.3, which erased 158 parks
  at continental zoom.
* `can_create` comes from the server — a password can arrive as a cookie, so
  `getPwd()` cannot decide whether `POST /api/aois` 403s.
* The progress card is **not client state** — it is a `notifications` row, so it
  survives a laptop shut for a week. Polling is adaptive and *stops* at `ready`,
  `cancelled` and `superseded`. `datasets_total` is the **planned** count, so a
  stopped queue reports 0; the card adds `datasets_stopped` back for the
  denominator, or it reads "0/0 layers" beside "0 of 11 were fetched".
* **Pins are namespaced for an AOI** (`aoi:<id>:<type>`). Ids are disjoint
  today; the flat park key `<id>_<type>` would have made a same-named AOI and
  park share one pin.
* **An AOI wears the park popup's controls, not its own.** One row of
  `.pa-export-btn` squares beside the title (Focus / Export ▾ / Edit) plus the
  ordinary `.star-btn` — `aoiActionsHTML()` renders it once for both the map tip
  and the popup. Two earlier versions were wrong in opposite directions: eight
  bare icons crushed beside the title (name wrapped one letter per line), then
  nine labelled buttons in three groups (View/Download/Manage), which is a form,
  not a tip. The four downloads are **one** category, so they live behind one
  button in `#aoi-menu`; the menu is appended to `<body>` because the map tip is
  an `overflow:hidden` box rebuilt on every mousemove, which both clips a child
  menu and destroys it under the cursor.
* **Animate is not in the AOI's action row.** The ▶ chip lives in the time
  slider, where a time window is chosen. `animateAOI()` still exists and is
  still what focus uses.
* **The star *is* the hide control.** For an AOI, visibility and report
  membership are the same fact (`collectReportParks()` folds in every visible
  AOI), so one ★ says both. Un-starring calls `/archive`; the polygon fades via
  `fadeAOILayer()` before the request so the click reads as immediate, the toast
  carries **Undo** (`showToast(..., {action})` → `unhideAOI()`) *and* names
  search as the permanent way back. Deliberately no `confirm()`: a modal asks
  the user to predict the result, an Undo lets them see it. "Hidden", not
  "archived", in the search result chip — hiding is what they did.
* **Renaming is not editing, and has no pencil.** `POST /api/aois/{id}/rename`
  keeps the id, so every share link, pin key and park-shaped row keyed by it
  survives; an *edit* forks because the question changed. Click the name and it
  becomes a field (`.aoi-editable-name`, hover underline). A pencil icon would
  be a second edit affordance beside the real one and would cost a slot on the
  row this whole layout exists to free.
* **`.maplibregl-popup-content` is capped at 280px** (globe.css). The AOI popup
  carried `min-width:300px` on its `.pa-popup` child, so every section stuck
  20px past the panel's right edge and the accordion rows looked sheared off.
  It is 260px now. A popup's own min-width must stay under that cap.
* **A sticky map tip and the popup are the same answer twice**, and the tip is
  anchored at the cursor, i.e. on top of the popup it just opened.
  `showAOIPopup()` calls `MapTip.hide()` first — and so does `showPAPopup()`.
* **The AOI tip is `clickOnly`, and every backdrop must be.** See
  "Hover tip precedence" below: a tip that follows the cursor across 485,000 km²
  has no "off it" to move to, so it hid whatever the user was reaching for.
* **Overview & coverage opens collapsed.** Once ingested it is a wall of 100%s,
  and the popup was opened to see fires and settlements. The header carries
  `{done}/{total}` and, only while work is outstanding,
  `renderAOIOverviewHeader()` draws a slim amber aggregate bar (weighted by
  `units_done/units_total`, so it moves during a long unit) plus "data below is
  partial". No bar at 11/11 — a full green rule is decoration.
* **The AOI popup has the Roads/Rivers/Places & Infrastructure section too.** It
  was simply never added, though `fetchPopupRoadData()` had already been taught
  `apiBase()`/`isAOI()` for it. `?aoi_sections=road` opens it.
* **`FloatUI.decoratePAPopup()` bails on `.pa-popup-name > span:last-child`.**
  It was written as a "does this look like a PA popup" guard; the AOI header put
  a second span after the name and the guard silently returned, taking the grab
  bar, the minimise button and MapLibre's × with it. Now `> span`. Anything
  added to a popup header must keep that selector matching.
* **The focus banner positions itself from the measured slider height**
  (`positionAOIFocusBanner()` + a `ResizeObserver`). A hard-coded `bottom`
  covered the ▶ animate button once the preset tags wrapped on a narrow phone,
  and the slider also grows when the animator opens.

### Versioning

An edit forks: version N+1 is created, N is archived (`state='archived'`, hidden
from `ListAOIs`, still readable by id, queue disabled). Versions are labelled by
their **analysis window**, not by number. An edit that changes nothing returns
`unchanged: true`; an edit that did not move a vertex sends no geometry at all.
Only the outer ring is editable — a hole or a multipolygon is `ringLocked` and
carried through untouched, because flattening a donut would change what the AOI
*means* while looking like a date-only edit.

### What is left

Nothing blocking. Two nice-to-haves:

* **A second AOI has never existed.** Everything is written to be per-AOI, but
  every measurement is n=1. The first thing a second one will exercise is tile
  cache sharing (`data/ghsl/tiles/`, `data/gfw_tiles/`, `http_cache`) and the
  runner's fairness across two pending queues — it currently takes them in
  `(priority, dataset)` order with no round-robin, so a big AOI can starve a
  small one for days.
* **`aoi_grants` has no UI.** The table, the `aoiVisibleSQL` clause and the
  Access tab's "Shared with" column all exist; nothing writes a grant. Sharing
  an AOI today means sharing the password.

### Do not re-litigate

* Hiding parks outside a focused AOI instead of dimming them — no. Nor dimming
  *starred* parks: a star is explicit and outranks an implicit scope.
* Archiving stopping the ingest — no. Archive is about the screen, `cancel` is
  about the quota.
* Offering Resume on a superseded version — no. Editing is the way forward,
  `/restore` the way back; `refresh?resume=1` 409s.
* Telling the user "database busy, try again" — no, that was written and
  removed. Fix the writer, and first check whether it is *CPU*-bound.
* An `aoi:<id>` scope key in a park-shaped table — no. Bare id +
  `aoiExcludeSQL()`.
* AOI rows in bbox-keyed endpoints **by default** — no. With an explicit,
  visibility-checked `?aoi=` — yes, `aoiScopeSQL()`, and exclusively.
* Requiring a *separate* star before an AOI enters the report — no. Visibility
  is the trigger, and the ★ in the UI **is** that visibility (un-starring hides
  it). An extra opt-in gave a user with one AOI and no stars an empty report.
  The star panel lists visible AOIs first, in their own section, and
  `updateStarBadge()` counts them.
* A separate "Hide" button beside the star — no, they were the same switch under
  two names, and "archive"/"hide"/"delete" for one action is how the old popup
  got to nine buttons.
* A global admin view of all AOIs — no (it would leak every tenant's polygons).
* Visibility-filtering `/api/fire-frames`, `/api/grid` — no. They serve raw
  geography that was always public within the app. **The polygon is the secret,
  not the pixels.**
* A privacy tidy-up as a migration — no. `reownSystemAOIProgress()` is a
  warn-and-continue startup fixup. When a migration is downgraded to a fixup,
  **delete the file** — a doc note is not a revert.
* Recomputing an expensive narrative per request because "a park is fast" — no.
  See "Narrative caching": park scale hid a 2m27s AOI timeout for months.

### Files

| file | role |
|---|---|
| `scripts/aoi_runner.py` | the queue: leases, cursors, interruption |
| `scripts/test_aoi_resume.py` | proves resumability — run after lease changes |
| `scripts/aoi_lib.py` | connect, principal_ref, upsert, `DEFAULT_DATASETS` |
| `scripts/aoi_admin.py` | CLI: list/show/create an AOI |
| `scripts/aoi_clip.py` | Phase A preview; `DELETE_EXCLUDE`, `SUPERSEDED_BY` |
| `scripts/hansen_loss.py` | streamed Hansen loss (<=2023), no download |
| `scripts/gsw_water.py` | streamed JRC surface water -> `park_waterbodies` |
| `scripts/osm_hydro.py` | rivers & lakes from a country PBF (HydroSHEDS is 403) |
| `scripts/ghsl_tiles.py` | GHSL tiles, cached by tile id, 1-indexed grid |
| `scripts/fetch_park_basins.py` | watersheds per outlet; **`--aoi`**, `outlet_budget()` |
| `srv/aoi.go` | read path, visibility, `aoiExcludeSQL`/`aoiScopeSQL`, `resolveAreaGeom`/`resolveAreaBBox`, `reownSystemAOIProgress` |
| `srv/aoi_write.go` | create/refresh/delete/progress/kick/cancel |
| `srv/aoi_versions.go` | edit-as-fork, restore, archive |
| `srv/aoi_admin.go` | the Access tab: scoped ownership + queue control |
| `srv/aoi_estimate.go` | measured cost model (test pins 252 GFW / 570 FIRMS / 4 GHSL) |
| `srv/narrative_cache.go` | `narrative_cache` + `geoMemo` (parks and AOIs alike) |
| `srv/errors.go` | `isDBLocked`, `execUserToggle` (not sufficient alone) |
| `srv/park_basins.go` | `loadBasinParts` = all watersheds; merged is the fallback |
| `srv/static/aoi_draw.js` | polygon editor + live estimate; `startEdit` forks |
| `srv/static/aoi_progress.js` | the multi-day notification card |
| `db/migrations/040..045` | overlays, parks, versions, pixel count, basin parts, narrative cache |
| `docs/PLAN_AOI_OVERLAY.md` | design rationale + measured facts |

---

## Hover tip precedence — draw order is not intent

`srv/static/maptip.js` is the one hover/tap tooltip for every interactive map
layer. Two things it arbitrates, both added 2026-08-10 after a tap produced
three overlapping answers at once (geology popup + AOI popup + AOI map tip):

* **`priority` (default 0, higher wins, render order breaks ties).** Draw order
  answers "what is on top", not "what did the user mean". Two layers are
  *backdrops* — the AOI polygon (`-20`) and the geology drape (`-30`) — and both
  sit under the cursor almost everywhere, so whichever happened to be drawn last
  won every hit-test and buried the specific feature underneath it. A pinned
  fire/settlement/deforestation layer is priority 0 and is therefore always
  asked first. `+N more here` counts peers only: a backdrop under a trajectory
  is context, not a second thing to zoom in and separate.
* **`clickOnly`.** A layer covering the whole viewport must not hover-tip —
  there is no "off it" to move to, so the tip just follows the cursor forever.
  Such a layer answers a deliberate click, as a **sticky** tip (close button +
  action row) even for a mouse.
* **`MapTip.setBackdropGuard(fn)`** — stated once, in the `map.on('load')`
  block, not re-implemented per layer: a park polygon has its own popup and its
  own click handler, so every negative-priority tip stands down over one. The
  AOI tip used to do this itself, which is exactly why the geology overlay,
  added later, did not — a tap on a park inside the AOI opened a geology card.
* **Geology went through `maplibregl.Popup`**, so its click never reached the
  shared arbitration at all. It is a MapTip registration now, and `remove(id)`
  unregisters it — otherwise a switched-off sheet keeps swallowing clicks.
  The raw-Popup path survives only as a fallback if `maptip.js` fails to load.
* Where a backdrop wins but hides another, **name the other in the same tip**
  rather than making it unreachable: the AOI tip carries a `Geology · <code>`
  line. One tip, both answers.
* `MapTip.refresh(layerId)` re-renders the tip on screen when an async detail
  lands (AOI coverage), instead of leaving "coverage…" frozen on a sticky tip
  that no longer gets a mousemove.

---

## Historical map overlay (Sudan Survey 1:250,000)

**187 sheet cells** (editions 1915-1968, median 1933) of the Anglo-Egyptian
Sudan 1:250k series across all 18 1:1M blocks, georeferenced to their own
printed 15-arcmin graticule and mosaicked into `data/histmaps/sudan250k.mbtiles`
(3.6 GB, z0-14, 574k tiles, **gitignored derived output** — rebuild with
`scripts/histmaps/mosaic.sh`, don't commit). The 49 cells covering
`XSA_Study_Area` are complete.

⚠️ **The first build (2026-08-06) covered the wrong half of the country.**
`captions.txt` had
been truncated at **264 of 770 lines** by an interrupted `curl`, which dropped
every South Sudan block — exactly the AOI the overlay exists for. Nothing
failed: 76/76 sheets georeferenced to 0.0 arcsec, QA was clean, the mosaic
built, and the README then *documented the missing blocks as absent from the
archive*. **A short manifest reads as a small collection, not as a broken
download.** `catalogue()` now asserts 770 lines and cross-checks the LOC item's
own `segment_count`; `mosaic.sh` invalidates a block's cached tiles when its
sheet list changes. Same shape as the AOI "no-op that reads as an answer" rule,
applied to an input. Full post-mortem: `scripts/histmaps/README.md`.

The same shape bit twice more downstream, both fixed: `qa.json` was rewritten
wholesale per run (so a final 8-sheet retry pass replaced the record for all
187 — it is merged by id now), and a hardcoded `"76 sheets"` in the MBTiles
description survived a rebuild that more than doubled coverage (`refresh_meta.py`
derives it from `data/histmaps/geo/`). **Never type a count that describes a
variable input.**

8 cells fail to register and that is correct, not a gap: all are near-blank
Libyan/Nubian Desert sheets (blocks 43/44/45, median ink 0.021 vs 0.085 corpus)
with too little printed ink for the graticule detector to fit a ladder. It
declines rather than guessing — a wrong warp on a blank sheet would be invisible
and permanent.

Rebuild is a throttled resumable systemd oneshot
(`scripts/histmaps/histmap-rebuild.service` → `rebuild_night.sh`, ~22 h for 128
sheets + 4 h mosaic), and `select.py --priority-bbox` orders the AOI's sheets
first so an interrupted run still covers what matters. JP2 fetches are bounded
on *throughput* (`--speed-limit`/`--max-time`), because `--retry` does not cover
a stall: one curl sat on an idle connection for 22 min at load 0.00.

| Piece | Where |
|---|---|
| Fetch + georeference | `scripts/histmaps/sudan250k.py`, `select.py`, `runall.sh` |
| Mosaic to MBTiles | `scripts/histmaps/mosaic.sh` (~4 h, resumable); `refresh_meta.py` for metadata only |
| Overnight rebuild | `scripts/histmaps/rebuild_night.sh` + `histmap-rebuild.service` |
| Serving | `srv/histmap.go` — `GET /api/histmap`, `/api/histmap/sudan250k/{z}/{x}/{y}.png`, `/download` |
| UI | admin panel -> Map Settings -> Historical Maps (`HistMap` in globe.html) |
| Share link | `?histmap=sudan250k` |

**White ink is a paint property, not a second tileset.** The archive holds one
flat near-black (26,22,18) on transparent paper, so the layer sets
`raster-brightness-min: 1` to lift RGB to white while leaving alpha alone. The
**download stays black on purpose** — offline viewers (Locus, OsmAnd, QGIS)
default to light backgrounds where white ink is invisible. Never "fix" this by
generating a whitened archive; there would then be two copies to keep in sync.

Layer order is load-bearing: inserted **before the first non-raster layer**, so
above the basemap and below park/AOI outlines, trajectories and pins.
`switchBasemap()` therefore **excludes** `histmap-lyr`/`histmap-src` from its
generic custom-layer capture and calls `HistMap.reattach()` (which re-adds on
`idle`) instead — replaying the captured spec appends the scan on top of
everything, and re-adding during `styledata` is silently dropped because the
`before` id has not landed yet.

Tile misses return **204, not 404**: the series covers 18 of 22 1:1M blocks, so
most of the bounding box legitimately has no sheet.

**Tiles are `immutable, max-age=7d`, so a rebuild must change their URLs.**
`GET /api/histmap` returns `rev` (mtime+size of the MBTiles) and bakes it into
the `tiles` template as `?v=`. Without it the truncated 76-sheet build kept
rendering after the 187-sheet rebuild, and only at the zoom levels the browser
had cached — which reads as "gaps at some zoom levels", not as a stale cache.
The client must use `meta.tiles`, never a hand-written tile path.

Full detail, including why the mosaic is built per-block and why `tile_row` is
TMS: `scripts/histmaps/README.md`.

---

## Geology overlays (Sudan GRAS 2004, CAR BRGM 1964)

Two scanned geological sheets turned into **vector** overlays: 46 classes for
Sudan, 17 for CAR, served as vector tiles and toggled per class or per
commodity. Full detail: `docs/GEOLOGY_HANDOVER.md`. Files:
`scripts/geomaps/{sheets,gridfit,georef,legend,vectorize}.py` + `tiles.sh`,
`srv/geomap.go`, `srv/static/geomap.js`, `renderGeoMapPanel()` in globe.html.
UI: admin ▸ Map Settings ▸ Geology. Share link `?geomap=car`.

Not the `?histmap=` raster overlay (1:250k topographic scans) — different data,
different path. Vector because the units are *data*: the client recolours them,
hides one, and isolates "everything that can host gold". A raster drape would
need one tileset per combination of 46 classes.

* ⚠️ **A hold-out measured on a whole legend swatch lies about the map body.**
  It reported 0.95–1.00 while CAR's Mouka-Ouadda plateau — an area the size of
  Belgium — rendered **white**. The classifier decides from a 17–33 px window,
  where inks 0.13 apart in Bhattacharyya distance are noise; two such classes
  do not *swap*, they **cancel**, both lose the `--min-margin` test, and the
  formation vanishes instead of being mislabelled. Judge every change with
  `window_holdout()` and read the **claim rate**, not the accuracy: 34% of CAR
  inside its own cutline was unclaimed at accuracy 1.000. Merging what the
  window genuinely cannot separate took it to 8.7% (Sudan 16% → 9.0%).
* **`<sheet>_classes.json` is the catalogue the server reads, not
  `legend_*.json`.** The legend is the sheet's *printed* unit list; the tiles
  carry the *class* list, which merges inseparable units and drops ones that
  never occur. Serving the legend offers toggles for classes that cannot be
  drawn. Both are committed; `_units.geojson` and `*.mbtiles` are not.
* **A merged class is labelled with every member code** (`GC2/GO`), never a
  pick — the sheet does not say which one a patch is. Its affinities are the
  **union** at the highest member weight, each `why` prefixed with the member
  code so the union is not a quiet upgrade.
* **Commodity affinity is an inference over lithology, never an occurrence
  dataset**, and every surface that shows it says so. Same line as the mining
  verdict below: inference from context ships, fabricated evidence does not.
  Keyed by `(sheet, code)` — `S` is Silurian sandstone on Sudan and a
  gold-bearing schist belt on CAR.
* **Paper competes as a synthetic class and is then discarded**, which is how
  `paper_like()` units are resolved by exclusion. Both failure directions land
  on "unclaimed", never on a wrong formation.
* Tiles: 204 on miss, `immutable` + `?v=<rev>` (a rebuild can change the class
  list, so stale tiles would carry names the catalogue no longer has), every
  class kept at every zoom — detail is dropped as *geometry*, never as a
  missing unit.
* `switchBasemap()` excludes `geomap-*` and calls `GeoMap.reattach()`, which
  re-adds on `idle` — the same `before`-id trap as `HistMap`.
* **The unit card is a MapTip registration, `clickOnly` + `priority: -30`** —
  see "Hover tip precedence". It is the deepest backdrop on the map: a park, an
  AOI, and every pinned feature answer a click before it does.

**Both downloads ship**: `?geomap=` renders the sheet, `Download MBTiles` is
the picture, `Download GeoPackage` is the data (typed columns, one
`w_<commodity>` weight column, ink colours and a QGIS project inside). A link
to the panel itself is `?panel=admin&admin_tab=map-settings&map_sheet=car`.
`srv/geomap_gpkg.go`; details in `docs/GEOLOGY_HANDOVER.md`.

* **`w_gold IS NOT NULL` is the point.** Commodities as one comma-joined string
  would make the export's headline question a `LIKE` over text; one INTEGER
  column per commodity makes it an exact filter and lets QGIS graduate on the
  weight. The column set is derived per build, never fixed — a re-vectorized
  sheet can merge classes and change the union of affinities.
* **NULL, not 0, where a unit hosts nothing.** 0 reads as "measured, none" and
  matches `>= 0`.
* Built on first request and cached beside `<sheet>_units.geojson`, keyed on
  mtime (`>=`, or a build finishing inside its input's timestamp tick rebuilds
  forever). No job queue: it is a static file per sheet and takes ~2 s, unlike
  the per-area export which is minutes over a live database.
* The button only appears when `_units.geojson` is present, and the size only
  once a build exists — a link whose only outcome is a 404, or a "(12 MB)"
  nobody measured, are both worse than nothing.
* Verified by rendering it in QGIS (`docs/GEOLOGY_HANDOVER.md` § GeoPackage),
  not just by `ogrinfo`.

---

## Time Animator ("▶ Animate" button next to slider presets)

Animates all toggled/pinned map layers over the time-slider window.

| Piece | Where |
|-------|-------|
| Frontend | `srv/static/anim.js` (canvas overlay above MapLibre; `window.Animator.open/close/toggle`) |
| Fire/effort frames API | `GET /api/fire-frames` → `srv/fire_frames.go` |
| Dated trajectories API | `GET /api/fire-anim-trajectories` → same file (reads `data/fire_groups_v5/*.json`, ~40-park LRU cache) |
| Pre-agg tables | `fire_grid_day/week/month` (base 0.1°, PK `(d, xi, yi)` WITHOUT ROWID; cell center = `xi*res, yi*res`) |
| Agg builder | `scripts/build_fire_grid_agg.py` (full ~100s; `--since YYYY-MM-DD` incremental; called by `daily_fire_update.py` step 2c) |

**Key behaviors** (all in `anim.js`, v2 — integrated into the time slider):
- UI lives **inside** the time-slider header: play/date/speed/GIF/close inline, playhead + progress rendered in the slider track (playhead is pointer-draggable to scrub; pauses while dragging, resumes after). `#anim-open-btn` is a preset-tag-styled chip.
- **Layer chips** (`.anim-chip`, staggered reveal like date tags — all always shown so users see what's available): fireGrid / firePts / trajs / effortGrid / effortPts / deforest / settlements / turb / infra. Lazy-load on first enable (`ensureLayer`); toggleable mid-play. turb/infra greyed (`.unavailable`) when nothing pinned.
- Defaults from `viewLayers` toggles + pins; zoom ≥ `POINTS_ZOOM` (6.5) and bbox ≤ 40 deg² prefers real points. `firePts` = `/api/fire-frames?mode=points` (individual VIIRS detections, ≤60k, server falls back to grid). `effortPts` = patrol-effort **circles**: same aggregated frames as effortGrid, drawn as fire-style green glow + recency ring, newest visit per cell wins.
- Map stays fully interactive (canvas pointer-events:none); pan/zoom outside the 30%-padded fetch bbox triggers debounced refetch (`onMoveEnd`), unless a drawn bbox is fixed (then canvas is clipped to it).
- Temporal semantics: fire grid/points flash + afterglow; trajectories build at true dated speed with glowing head, then **ashen out** (red→grey→gone over `TRAJ_FADE_DAYS`=21); effort ages to ash over 90d so refreshes flash green; deforestation accumulates (45d flash); settlements/infra static; turbidity accumulates.
- Speed +/−: click steps ×1.35, press-and-hold ramps (mobile). Keyboard: space/←/→/Esc.
- **Share links**: `anim=<layers>&anim_speed&anim_t&anim_paused` written by `shareCurrentView()` (via `Animator.getState()`); restored through `window._pendingAnim` set in `restoreStateFromURL()`, polled by anim.js until map ready.
- `chooseStep()`: ≤92d→day, ≤800d→week, else month. GIF export via `gifenc` CDN
  (720px; hidden on mobile). **The GIF plays back at the on-screen speed**: its
  duration is `spanDays / A.speed` seconds, frames are `10/s` capped at
  `GIF_MAX_FRAMES`, and the per-frame `delay` is then stretched so a capped
  export gets *choppier, not faster*. It used to be a fixed 80 frames × 100 ms,
  i.e. always an 8 s clip regardless of the speed control the user had just set.
- **An AOI animation must never silently fall back to its bbox.**
  `Animator.open({aoi})` reads `window._aois`, which `loadAOIs()` fills
  asynchronously — a share link carrying `anim_aoi=` can win that race. Without
  the geometry no `&aoi=` is sent, `aoiExcludeSQL()` then hides the AOI's own
  rows, and the animation plays *empty*. A missing entry is now treated as
  not-loaded-yet and fetched from `/api/aois/{id}?geometry=1` (whose payload is
  `{aoi, datasets, parks}` — unwrap `.aoi`).
- **Opening paused at `t0` is legitimately blank** (no trajectory has started
  yet), which reads as a broken layer. `frameHasContent(t)` drives a one-off
  hint instead; it affects wording only, never drawing.

**Server-side** (`fire_frames.go`):
- `/api/fire-frames?bbox&from&to&step=day|week|month&res=0.1` reads pre-agg tables (never `fire_detections` — a raw scan took 3min for full-span; agg is ~3s). Coarser `res` re-binned in SQL; `from` aligned to bucket start. If >200k points, auto-doubles `res` up to 2× twice instead of truncating.
- `layer=effort` returns `[xi, yi, km, uploads]` on the same grid (from `effort_data`+`grid_cells`, `movement_type='all', env='prod'`).
- Frame point format: `p: [[xi, yi, count, frp], ...]`, `d` = bucket start date.

**After bulk fire data changes**: rerun `python3 scripts/build_fire_grid_agg.py` (full) or `--since` — otherwise the animator shows stale fires. Daily cron keeps it fresh automatically.

### Settlements & deforestation at AOI scale

The animator's `deforest`/`settlements` layers and the stats-panel view layers
all come from `/api/features-in-bbox` (`srv/features_bbox.go`). At park scale it
was fine; over `XSA_Study_Area` (78,105 settlement polygons in one view) it
returned a wrong picture slowly. Four fixes, all measured 2026-08-10:

1. **`ORDER BY stat_value DESC LIMIT n` is not a sample, it is a corner.**
   Every settlement carries `stat_value = 0`, so the tie-break fell through to
   rowid and the 1,500 rows served were one contiguous *ingest block* — the
   yellow stripe along the AOI's north edge, which reads as "the data is
   wrong", not as "truncated". `spreadSelect()` buckets the bbox into ~limit
   cells and keeps the best feature per cell. Deterministic; `&spread=0`
   restores the old behaviour.
2. **Don't read geometry for rows you are about to discard.** Pass 1 selects
   ids + centroids only, pass 2 fetches geojson for the survivors (`IN` chunks
   of 900). `mode=points` skips geometry entirely and returns
   `[lon, lat, dayOffset, value]` against `from` — the animator draws dots, so
   it was inflating ~1 MB of polygon rings to recover 1,500 centres. 947 KB →
   118 KB gzipped, and the point budget rose 1,500 → 12,000, i.e. a real
   sample instead of a corner.
3. **Migration 046 (`idx_fg_bbox_scan`) makes pass 1 covering.** `idx_fg_stats`
   lacked `park_id`, which `aoiExcludeSQL`/`aoiScopeSQL` always reads, so
   SQLite fetched each candidate's full row — including up to 100 KB of
   geojson — just to read one short string. `fire_trajectory` over a 3° window:
   **3.0 s → 0.22 s**. Same shape as the `ABS()` and `polygon_ids LIKE` traps:
   the index existed and was silently not enough.
4. **Polygons are simplified to half a screen pixel** derived from the bbox
   (radial-distance decimation + 6-decimal coords, `&simplify=0` to disable).
   At continental zoom the *biggest* built-up polygon per cell ships 5 KB of
   sub-pixel ring detail: 2.1 MB → 0.6 MB gzipped, unchanged when zoomed in.

Rendering had the mirror problem — 12,000 arcs re-stroked at 60 fps for a
picture that does not change with `t`. Static/settled layers rasterise into an
offscreen canvas keyed on the view transform (`settlementSprite`,
`deforestSprite`), and trajectory points project once per transform
(`projectTrajs`, `Float32Array` + an off-screen flag) instead of once per frame.
`invalidateSprites()` on refetch/close. **Any new dense static animator layer
should do the same** — the cost is one screen of pixels regardless of N.

**Deforestation ages over the window, and never vanishes.** A fire front is an
event that ends; canopy loss is a state that persists. New clearings flash
purple for 45 days, then grey towards ash over the *window span* (floor 90
days) — not over a fixed number of years, because the loader only fetches
events inside the window, so a fixed 10-year ramp puts every event in the first
6% of a 7-month window and greys nothing. Alpha floors at 0.22 and the radius
shrinks 40%: an old clearing is faint, not gone. Ageing is quantised into 24
bands so the settled-prefix bitmap survives ~4% of the playback per redraw.


---

**Popup fire chart**: single `areaSparkline` (globe.html) fed by `/api/parks/{id}/fire-trend`.
Series keys: `v`=fires (red, left axis), `v2`=groups (orange, right axis),
`v3`=prior-years ISO-week average (dashed gray, same axis as `v`, computed client-side
from full history). Don't add a second weekly chart.

---

## GeoPackage export (QGIS) — the fourth download

One `.gpkg` with **every layer we hold for an area** (park or AOI), typed
columns, QGIS styling and an embedded QGIS project. Full detail:
`docs/GEOPACKAGE_EXPORT.md`. Files:
`srv/gpkg{,_export,_style,_project,_inarea,_jobs,_test}.go`,
`srv/static/gpkg_export.js`, migration **047**.

It is the KML export's content plus what KML cannot carry honestly: raw fire
detections, typed numerics, symbology. **A change that makes the file merely
valid rather than usable is the wrong change.**

* **The declared column type is the contract**, and a `DATE`/`DATETIME` column is
  only honoured if the *value* parses as ISO-8601 — `"2024"` reads back as NULL
  silently. Use `gpkgDate`/`gpkgDateTime`/`gpkgDateTimeParts`, never a raw
  column; keep partial originals in their own INTEGER column (`loss_year`).
* **QGIS temporal `mode` is not zero-based-by-convenience**: `0` is
  *FixedTemporalRange* (ignores the fields). 1 = instant, 2 = start+end. The
  wrong value yields a layer that claims to be temporal, shows the fields in the
  dialog, and renders everything at every timestep.
* **Styles alone are not enough.** A GeoPackage has no layer order or
  visibility, so a styled-but-projectless export opens as an orange smear —
  163k fire points on top of everything. The embedded project (`qgis_projects`,
  a hex-encoded `.qgz`) ships the firehoses **off**. It references its own
  container as `./<basename>.gpkg`, so the on-disk name must equal the download
  name — hence one directory per job.
* **A bbox is not an area.** Detections are coordinate-keyed, so the query is the
  bbox: XSA's polygon holds 3.18M and its bbox 6.9M. They are kept (context out
  to 20 km is deliberate) and **labelled** `in_area`, with the renderer
  distinguishing them. An unusable boundary defaults to *inside* — silently
  flagging every row 0 is worse than not knowing.
* **The R-tree is not optional** — and every layer gets one, raw detections
  included. QGIS rendering 6.9M detections zoomed in: **1.96 s → 0.08 s**. At
  regional zoom (2M points on screen) it is 4.97 s → 5.83 s: the cost there is
  *drawing*, and no index touches it — which is why the layer ships off and
  "no raw fire points" is a separate export. Built inline per feature; the
  spec's maintenance triggers are omitted because the file is never edited.
* **Every layer is exported whole — no LIMIT** (same rule as the `/features`
  geography layers; a truncated file is indistinguishable from a complete one
  once it is in someone's QGIS project). Empty layers are dropped.
* **The job is a cache, not a spool**: keyed by (area, window, effort, env),
  file kept **21 days**, so asking twice returns the same file and a shared link
  keeps working. `?refresh=1` rebuilds — and **keeps the old file alive**,
  because a link someone was given must not break because the sender rebuilt it.
* One build at a time; a queued job says *"waiting for another export"*, not 0%.
  The card is written at queue time, and startup fails orphaned `running` jobs
  rather than freezing a bar at 40%.
* **Two variants, not a checkbox**: "all layers" and "no raw fire points"
  (`?raw=0`). A gigabyte and several minutes apart, so it is a choice between
  two downloads; `raw_fire` is in the cache key, the filename and the card
  title, or one would be served the other's file.
* **A park and an AOI share one ⬇ download menu** (`exportMenuItems()`), instead
  of the park's old three guessable icons. `aoi_menu=` takes a park id, and
  draining it retries on a decaying schedule — the anchoring button is drawn by
  a popup that is itself waiting on a fetch.
* **Raw fire detections ship switched OFF; trajectories ON.** Millions of
  coincident points on top of everything are not a map, but a Fire group that
  shows nothing until you go looking is its own wrong answer.
* AOI exports are **404, not 403**, for non-owners, on status *and* download.
* Share links: `aoi_menu=<id>`, `aoi_menu_item=gpkg` — **the highlight never
  starts a BUILD, but it does download a file that already exists.** The rule
  was written against one danger (a link that spends five minutes of server
  time and 400 MB on open), and that danger only exists when the export has to
  be made. When it is already built the link *is* a link to a file: the sender
  made it and is handing it over, and pointing at a row and waiting for a
  second click is ceremony. `?peek=1` on `export.gpkg` tells the two apart
  without building — a pure lookup on the cache key, 404 when nothing matches
  (pinned by `gpkg_peek_is_side_effect_free` in `tests/api_tests.sh`; if asking
  ever creates a job, every shared link becomes the trap the rule forbids).
  `resolveHighlightedExport()` in globe.html: ready → download; pending/running
  → point at the bell and adopt the job, never start a second; absent → a toast
  saying the highlighted entry has to be clicked to build it. The window is
  part of the identity, so it runs after `restoreStateFromURL` has applied
  `?from`/`?to`. Only the two GeoPackage entries need it — KML/GeoJSON/Locus
  are plain hrefs served inline and have nothing to already exist.
  `gpkg=<job id>` still opens the card, not the file.
* No QGIS in CI. `go test ./srv/ -run 'GPKG|QGIS|GeoPackage|AreaHit'` covers the
  byte-level contract; for styling changes install `python3-qgis` and **look at
  the render** — the first version passed every automated check and was unusable.

---

## ⚠️ Gzip is decided AFTER the handler sets Content-Type

`GzipMiddleware` used to set `Content-Encoding: gzip` and swap in a
`gzip.Writer` *before* calling the handler — i.e. before anything knew what the
body was. Every binary download was therefore compressed *and* carried
`http.ServeContent`'s `Content-Length` taken from the file size. The browser is
promised 101,113,856 bytes, receives 51,381,359, and reports **a network
error** — "Die Netzwerkverbindung wurde unterbrochen" on a 100 MB GeoPackage
that transferred perfectly. The tell: the file *looks* like a failed download
and the server log shows a clean 200.

It also gzipped 206 bodies while `Content-Range` still described the identity
byte span (so a resumed 3.6 GB histmap fetch could not be reassembled), and
burned CPU deflating SQLite/MBTiles/PNG/zip.

Fixed 2026-08-10 in `srv/gzip.go`: the choice is made on the first `Write`
(or `WriteHeader`), from the headers the handler has set. Compress text-ish
types; pass everything else through **untouched, headers included** — which is
what makes `Content-Length` and `Range` correct again. Skipped when the handler
set its own `Content-Encoding`, on 204/304/206/`Content-Range`, and on any
`Content-Disposition: attachment` (a download is a file on someone's disk:
byte-exact and resumable beats a little bandwidth). Pinned by
`go test ./srv/ -run Gzip`.

**A middleware must not commit to a response encoding before the handler has
described the response.**

### Safari cannot "Copy Link" from the export menu — give it a button

The download menu's rows are anchors so that right-click → Copy Link needs no
extra UI. On Safari it does not work, in **two** ways, and the second is not
fixable in markup:

1. With a `download="…"` attribute, Copy Link yields the **attribute** — the
   bare filename (`XSA_Study_Area.kml`). The attribute is gone; the filename
   now comes from the server's `Content-Disposition` (`HandleAPIParkKML` puts
   the date window in it, which is what the attribute used to do — the window
   is part of what the file *is*).
2. Without it, Safari still writes a **rich-text** link whose visible text is
   the row's own label, so pasting into Mail or Notes gives "KML".

So every row with a real URL carries an explicit ⧉ (`copyExportLink`) that
writes `clipboard.writeText` of the absolute URL, and the menu **stays open** —
copying a link is not choosing a download. The anchors stay for ⌘-click and
"open in new tab". Pinned by `kml_filename_from_content_disposition` and
`export_links_have_no_download_attr` in `tests/api_tests.sh`.

**A filename is the server's job; a copyable URL is a button's.**

### A shared download link logs you in and then downloads

`?pwd=` on an `/api/` path now **sets the cookie** before serving (it used to
serve and set nothing), so someone who was sent
`/api/geopackage/{id}/download?pwd=…` is logged in afterwards rather than
re-prompted on their next click. Still no redirect for `/api/`: a download must
arrive as the response to *this* request.

Without a password the login form is shown as usual — it already carries every
query param through as hidden fields and posts back to the same path, so
submitting it returns the file. For a file link (`…/download`, `…/export.*`)
the form reads "Sign in to download" and **drops the sandbox link**: `test2026`
does not own that export, so "try it out" would land on a 404 that reads as a
dead link. The filename is deliberately not shown — it carries the area's name,
and an id must not be an oracle.

---


## Locus / KML Park Exports

Park tooltip icon buttons → `GET /api/parks/{id}/export.kml` (`srv/api.go`,
`HandleAPIParkKML`) and `GET /api/parks/{id}/export.locus`
(`srv/locus_export.go`). Both support `?from=&to=` date filters; KML also
`?effort=0` (tooltip skips patrol effort, star report keeps it).

- **Locus zip** = Locus Map 4 backup (restore via Backup → Restore). Contains
  `data/database/tracks.db|waypoints.db` (folder tree in `groups`: Base =
  boundary/rivers/roads/waterbodies/places/lakes/airstrips, Mission =
  fires-per-year/settlements/deforestation/turbidity) **plus default device
  config** embedded from `srv/locus_defaults/` (sanitized 2026 field-device
  backup: `_various/settings` with Dropbox OAuth blanked, config.cfg, BOMA
  preset…). Only boundary + latest fire year + turbidity visible by default;
  polygons exported as open-ring lines (5 m gap) so Locus map taps stay usable;
  empty folders dropped.
- **Rivers**: raw `park_rivers_hydro` rows are tiny disconnected HydroRIVERS
  reach stubs — always export via `loadMergedRivers()` (`srv/rivers_merged.go`)
  which chains touching segments into continuous polylines.
- **park_waterbodies** is populated from `data/waterbodies/*.json` by
  `scripts/import_json_to_db.py waterbodies` (creates table if missing).

---

## OSM detail: every park at Geofabrik level

`roads_heigit` and `osm_places` are filled from Geofabrik country PBFs by
`enrich_park_infra()` in `scripts/osm_pbf.py`. Until 2026-08-07 the only entry
point was `--enrich-missing`, which fires **only for a park with zero rows** —
so the 159 parks carrying the old HeiGIT import (major roads only: no `track`,
`path` or `residential`) were permanently "not missing" and stayed thin. Five
parks in the whole database had a single track or path, while an AOI ingested
from the *same* PBFs had thousands. That is the mismatch you see when a park
outline and an AOI drawn over it look like different maps. CAF_Chinko: 42 roads
before, 3,164 after.

```bash
python3 scripts/osm_pbf.py --enrich-all --rotate 2   # nightly, 05:15 UTC
python3 scripts/osm_pbf.py --enrich-all --iso CAF    # one country, ~75s
```

* **The unit of work is a COUNTRY, not a park** — the 50-750 MB PBF download is
  the whole cost, so one download serves every park of that country and is then
  deleted. 33 countries at 2/night = a full turnover every ~17 days.
* State is `data/osm_enrich_state.json` (stalest country first), deliberately a
  file and not a table: it is scheduling bookkeeping, and it must survive a
  database restore.
* `--enrich-all` passes `force=True` (a refresh, not a backfill), **but an
  empty osmium export never deletes** — that is a transient failure, not "this
  park has no villages", and deleting on it would make one bad run permanent.
  It warns and keeps the existing rows for the next rotation.
* Interruption is safe: each park commits before the next, and the state file
  only advances per completed country.
* **A country that enriched 0 parks does not advance the state file** and
  reports `osm_enrich_failed` — otherwise a bad PBF freezes as "refreshed"
  and that country never comes round again (AGENTS.md's recurring no-op).
  One country failing does not cost the other its turn in the same night.
* The PBF fetch is bounded on **throughput** (`--speed-limit 10000
  --speed-time 120`, `--max-time 5400`) and a failed download is **deleted** —
  `--retry` does not cover a stall, and a truncated PBF reads as a smaller
  country, silently thinning every park in it.
* Progress is visible: each country writes an `osm_enrich_success` /
  `osm_enrich_failed` notification with road/place deltas and how many
  countries are still on the thin HeiGIT import. It is a ~17-day rotation, so
  without that there is nothing to look at between start and finish.

## Cron jobs report into the notification bell — by SHAPE, not by a list

Every nightly job writes a `<job>_success` / `<job>_failed` row via
`scripts/cron_notify.py`. The bell fetches
`/api/notifications?type=cron_status`, a **meta-type** resolved server-side by
`cronStatusSQL()` (`srv/notifications.go`): `LIKE '%_success'`,
`LIKE '%_failed'`, plus the few one-off system types.

It used to be a hardcoded comma-separated list of six types in globe.html, so
every job added after it wrote notifications **nobody ever saw** — the OSM
rotation, park onboarding, and `daily_fire_update`'s own `nrt_sp_drift` /
`fire_ingest_errors` / `fire_rebuild_failed` rows. Never go back to
enumerating types there; a new job must appear with no frontend change.
`renderFireDownloadNotification()` is likewise generic (`CRON_ICONS` keyed on
the type prefix, sensible default) because it renders jobs it has not heard of.
Excluded on purpose: `mbtiles_*`, `aoi_progress`, `fire_alert`, `new_upload`,
`new_publication` — each has its own renderer; and the retired
`turbidity_scan_*`, via `miningNotifSQLFilter()`.

**Two systemd units were failing every night since March** —
`fire-nrt-daily.timer` / `fire-backfill.timer` still ran
`scripts/fire_nrt/cron_*.sh`, deleted in commit 087956d (203/EXEC), and were
superseded by the crontab's `daily_fire_update.py`. Removed 2026-08-10.
A duplicate scheduler is worse than none: check
`systemctl list-timers --all` as well as `crontab -l`.


## On-the-fly Park Onboarding

Search for an unloaded-but-WDPA-matched park name, dwell 15s → offer to add it.
Backend: `srv/park_onboarding.go` (`POST /api/onboarding/request|cancel`,
`GET /api/onboarding`; routes NOT under /api/parks/* because of park-id
middleware). Table: `park_onboarding_requests` (migration 037). Worker:
`scripts/onboard_park.py` (cron 02:30) — Protected Planet boundary → keystone
append, FIRMS fire backfill max(6mo, current year), v5 pipeline, GFW scan,
GHSL/HydroSHEDS if local sources exist, restart. Removal: same search-dwell on
an onboarded park (or undo toast); only parks with `onboarded_at` in
keystones_with_boundaries.json can be removed. Test-env requests are tagged
`env='test'` and skipped by the worker; test UI never shows offers.

---

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

## Level of detail: one loader, and the same feature at every zoom

**No active handover.** `docs/HANDOVER_LOD_FEATURES.md` is done and deleted;
what follows is the record.

The rule the whole thing enforces: **the same feature is the same feature at
every zoom, and a cheap rendering may not quietly become a picture.**

`srv/static/lodlayer.js` is the one loader for the stats-panel toggles *and*
pinned fire/deforestation/settlement layers. It asks
`/api/features-in-bbox?mode=auto` and the **server** decides geometry vs
centroids from the true count in view — never from a zoom number, because two
views at one zoom differ by three orders of magnitude. A centroid carries its
row id, so hovering it fetches `/api/feature-detail` and shows the same tip the
geometry would have.

### The four measured costs, and what each one was

1. **`/api/fire-anim-trajectories` parsed 816 MB of JSON per request.** It read
   `data/fire_groups_v5/<park>.json` through a 40-park LRU for one thing the
   database did not have: the **date of each vertex**. 7.8 s at `limit=800`,
   17 s at 4000, >120 s continental. That date is a column now — migration
   **051** `feature_geometries.traj_days`, a compact array of day offsets from
   `start_date`, always the same length as the coordinate list (a Point is
   `[0]`). Written by `load_fire_groups_to_db.py` (`day_offsets()`), backfilled
   once by `scripts/backfill_traj_days.py` for all 757,754 rows. The endpoint is
   now two indexed passes with no file I/O: **continental 12,000 paths in 1.7 s**
   (was >120 s for 800). NULL `traj_days` is handled, not skipped — points are
   spread evenly across `start_date..end_date`, so a partial backfill degrades
   the *timing* of an animation rather than emptying the map.
2. **`end_date >= ?` is not in `idx_fg_bbox_scan`**, so the natural overlap
   predicate dropped the index. Ask `start_date BETWEEN from-trajMaxSpanDays
   AND to` (200 days; the longest group in the table is 167) and keep the
   `end_date` term as a filter. Dropping the tail *in Go* instead returned 2,409
   of a requested 4,000 — a sparse map that looks like missing data.
3. **`ORDER BY stat_value DESC LIMIT n` is a corner, not a sample** (the same
   trap as the settlements stripe). The trajectory endpoint now streams into
   `spreadCollector` like `/features-in-bbox`, so a truncated continental answer
   is spread across the view and `total` is the true count.
4. **Properties, not shapes, were the payload.** A fire trajectory carries ~350
   bytes of coordinates and ~750 bytes of properties, mostly a narrative
   sentence nothing on screen reads. Above `geoSlimAbove` (1200 features) the
   answer ships identity fields + `rid` and skips `enrichFeatureProps`; the tip
   fetches the rest on hover, exactly as points mode does. 14,350 fire paths in
   an 8° view: **4.1 MB → 2.6 MB and 1.3 s → 0.25 s**, which is what let the
   client's vector budget go 6,000 → 12,000 and the animator's 800 → 6,000.

### Detail is ink, not just count

Drawing 5,837 trajectories at the old width/opacity is a **solid red sheet**:
every path present, none legible, and less informative than the dots it
replaced. `densityPaint()` (lodlayer.js) and the `inkW`/`inkA` ramp in
`anim.js` thin and de-opacify strokes as the count rises, so overlaps
accumulate into structure — corridors and repeatedly-burnt ground glow on their
own — while a sparse view stays bold and obviously clickable. Arrows fade out
with them: a glyph every 100 px across 2,000 overlapping paths is noise.
**Never judge this by feature count alone; look at the render.**

The animator's fire-grid blob radius must also **cover its cell**
(`cellPx * (0.8 + 0.6*inten)`), or the layer draws a halftone lattice — a
picture of our 0.1° binning rather than of the fires.

### The transition is shown, not hidden

Crossing the threshold is the one moment the map changes *what* it is showing.
Unannounced, a user zooming out watches their trajectories "become dots" and
reasonably concludes the app threw the detail away. So:

* the two renderings share one source id and **cross-fade** (220 ms);
* the incoming one **overshoots and settles** (`focusPulse`, 380 ms) — the same
  gesture in both directions, because the claim is that nothing was lost either
  way. `focusPulse` must settle back to the **density** width, not a constant,
  or it silently undoes `densityPaint` on every crossing;
* the state is written into the control that switches the layer on —
  `.stats-lod` inside the stats row (`setLayerLOD`), and a ◇/· mark on the
  pinned chip. **Not a floating HUD**: the question is about one layer, and a
  HUD would be a fourth thing competing with the toast, the pinned indicator
  and the time slider for the same corner. It takes the row's own colour at low
  alpha; on mobile only the pill survives (the count is already the row's
  value). It replaced a toast fired per truncated fetch — i.e. on every zoom,
  covering the map to say something permanent about the view.

### Panning is free, zooming re-asks

`lodlayer.js` fetches a **25%-padded** box and compares against the unpadded
view, so a small pan is answered from memory. It skips a refetch that cannot
reveal anything (contained + untruncated + already geometry), and in points
mode also skips a pan or zoom-out inside a covered box — only a meaningfully
smaller view can promote it to geometry.

### The animator's two downloads are share links

GIF and GeoPackage were two bare buttons that could not be handed to anyone,
which is backwards: the whole point of both is that you found a frame worth
keeping. They are now one ⬇ opening the **same `.aoi-menu` component** the park
and AOI downloads use — one row each, each with a ⧉ that copies a link
reproducing this exact animation (window, viewport, layers, speed, playhead)
and pointing at that row. `?anim_export=gif|gpkg` **highlights, never runs**:
same rule as `aoi_menu_item=`, because opening a link must not spend minutes of
CPU. The GIF row hides itself on mobile (encoding is minutes and a hot
battery). Reusing the menu also inherits its Safari copy-link workaround and
its touch sizing for free.

### A view GeoPackage is immediate when fast, a notification when not

`?wait=<s>` on `POST /api/view/export.gpkg` (capped 20 s), migration **050**
(`view_json`) so a card that outlives its tab can still describe and retry
itself. Same job, cache, 21-day link and delete button as the area export — it
is a different *question* (only what is on screen), not a different mechanism.

### Still open, deliberately

* A **classification filter** on a pin still uses the old whole-park fetch:
  that filter reads feature properties client-side, and both points mode and
  slim geometry ship none. A filtered pin is a small deliberate subset anyway.
* `?spread=0` and `?simplify=0` remain undocumented escape hatches with no UI.

## Documentation

See `docs/` directory:
- `README.md` - Overview
- `INSTALL.md` - Setup guide
- `API.md` - API reference
- `DATABASE.md` - Schema docs
- `SCRIPTS.md` - Data processing pipeline
- `ARCHITECTURE.md` - System design
- `SHELLEY_PROMPT_UI.md` - UI development
- `SHELLEY_PROMPT_ADMIN_UI.md` - Admin panel

---

## Testing

### Run Tests

```bash
# Run all tests
./tests/run_all.sh

# Run specific test suites
./tests/run_all.sh db    # Database tests (37 tests)
./tests/run_all.sh api   # API tests (31 tests)
./tests/run_all.sh ui    # UI URL tests (20 tests)
```

### Browser Test Mode

Add `?test=1` to URL to enable `window.TEST` helper:

**Browser Setup:**
- Resize browser to **1280x1400** (or taller) to see full popup content
- This allows testing of all accordion sections without scrolling

```javascript
// Navigate to: http://localhost:8000/?pwd=test2026&test=1
// In browser console:
TEST.assertExists('#map', 'Map exists');
TEST.assertVisible('.stats-panel', 'Stats visible');
TEST.isPanelOpen('admin');  // Returns true/false
TEST.isPopupOpen('CAF_Chinko');
TEST.done();  // Print results
```

### Share Link Testing

URL params encode full UI state for reproducible tests:

| Param | Example | Description |
|-------|---------|-------------|
| `test` | `1` | Enable TEST helper |
| `panel` | `filter,star,admin,upload` | Open panel |
| `admin_tab` | `learning,features` | Admin tab |
| `popup` | `CAF_Chinko` | Open park popup |
| `sections` | `fire,deforestation` | Open accordions |
| `pinned` | `CAF_Chinko:fire_trajectory` | Pin layers |
| `detail` | `major` \| `main` \| `all` | Geography detail tier (omitted at the `main` default) |
| `geomap` | `sudan,car` | Show these geology sheets |
| `geomap_only` | `car:GC2/GO\|S` | Isolate these classes (unknown codes dropped) |
| `geomap_hide` | `car:Zeta` | Hide these classes |
| `geomap_opacity` | `car:80` | Per-sheet opacity % (omitted at the 55 default) |
| `map_sheet` | `car`, `sudan`, `histmap` | With `panel=admin&admin_tab=map-settings`: flash that sheet's card. Points at the downloads and provenance, not at the map — composes with `geomap=` |
| `aoi_menu` | `XSA_Study_Area` | Open that area's download menu |
| `aoi_menu_item` | `gpkg`, `gpkg_light`, `kml`… | Highlight one entry in it. A **built** GeoPackage downloads immediately; an unbuilt one only explains itself — a link never starts a build |
| `gpkg` | `<job id>` | Open the bell on that GeoPackage export's card |
| `starred_parks` | `CAF_Chinko,COD_Virunga` | Star parks |
| `notif` | `1` | Open notification dropdown |
| `notif_fire` | `CAF_Chinko:2026_grp_2caaa51b` | Zoom to fire + pin (see below) |
| `notif_upload` | `10.52,18.19` | Zoom to patrol location |
| `notif_pub` | `CAF_Chinko` | Open popup with research |
| `notif_download` | `123` | Download MBTiles file |

#### Fire Notification Share Links

Format: `?notif_fire=PARK_ID:YEAR_grp_HASH`

Example: `?notif_fire=CAF_Chinko:2026_grp_2caaa51b`

This will:
1. Open the notification dropdown
2. Expand the fire notification group for that park
3. Load the fire trajectory from features API
4. Look up friendly name from fire-realtime API (e.g., "Alpha-2")
5. Display trajectory on map and zoom to it
6. Pin the fire layer with friendly name

**To get the correct format:**
```bash
# Query notifications table
sqlite3 db.sqlite3 "SELECT park_id, reference_id FROM notifications WHERE notification_type = 'fire_alert' LIMIT 5"
# Returns: CAF_Chinko|CAF_Chinko_2026_grp_2caaa51b

# Format for share link: parkId:year_grp_hash
# Remove park prefix: CAF_Chinko:2026_grp_2caaa51b
```

### Playwright (Full UI)

```bash
npm install -D @playwright/test
npx playwright test tests/playwright/
```

---

## Authentication

### Password-Protected Endpoints

Most endpoints require password via:
- Cookie: `access_pwd=test2026`
- Query param: `?pwd=test2026`

Valid passwords: loaded from `ACCESS_PASSWORDS` env var or `secrets.env` (fallback: `test2026`)

### ⚠️ Patrol data is scoped to a TENANT, not shared across passwords

Patrol effort belongs to the rangers who uploaded it, not to everyone holding
an alpha password. The scoping key is the `env` column that already existed on
`effort_data`, `subcell_visits`, `gpx_uploads`, `track_points`, `upload_queue`,
`notifications` and now `autofetch_sources` — `srv/tenant.go` turns it from
"prod vs the test sandbox" into a real per-password tenant:

    PASSWORD_ENVS=<client-pwd-a>:prod,<client-pwd-b>:prod,<client-pwd-c>:prod,test2026:test

**An access password that is not listed gets its own empty tenant**
(`pw_<ref8>`). That direction is deliberate: adding a password must never
silently widen access, so the failure mode is "I see no patrol data", not
"I see yours". Omit `PASSWORD_ENVS` entirely and the historical single-tenant
behaviour applies (`test2026` → test, everything else → prod), which is what a
fresh checkout and `tests/` expect.

Nothing new was invented for the read path: **every query that already filtered
`env = RequestEnv(r)` became tenant-correct for free**, and `cacheKey` already
carried `RequestEnv`. What changed is only what `RequestEnv` *means*.

* `isTestEnv(r)` now means **"outside the client tenant"**, not "is test2026".
  Its callers all ask one question — may this request see client-derived data —
  and that has exactly one answer per tenant. `clientTenant`/`sandboxTenant`
  constants; never compare against the string `"test"` again.
* A server-side job that acts through our own HTTP API must pick a password for
  the *tenant it is working for* (`pwdForTenant`), and **refuse** when none
  maps. `runAutofetchSource` used `validPasswords[0]`, which files a client's
  EarthRanger tracks under whichever password happens to sort first.
* `autofetch_sources` is per tenant (migration **049**): a subscription names
  the client's tracking server, their username and the parks they operate in,
  and it feeds their pixels. Every by-id write is `WHERE id = ? AND env = ?`,
  and `run` checks ownership **in the handler**, not in the worker (the worker
  is also the scheduler's entry point and must not need a request).
* Onboarding is *not* tenant-scoped — a park is global data. `onboard_park.py`
  now skips only `env='test'` instead of requiring `env='prod'`, or a
  non-default tenant's request would never be processed.

### ⚠️ An authenticated response must not be `Cache-Control: public`

The first version of the tenant split was correct on the server and still
showed the wrong answer in a browser: after visiting as one account, switching
to another served the **first account's body from the browser's own HTTP
cache**, which is keyed on the URL and ignores the cookie. Symptom: "the pixels
are gone" (or worse, another tenant's pixels), while `curl` returns the right
thing — so it reads as a server bug and is not one.

`PrivateCacheMiddleware` (`srv/security.go`) downgrades `public` → `private`
and adds `Vary: Cookie` on everything behind the password gate. It downgrades
rather than deletes, so handlers keep the revalidation they asked for.
`isPublicPath()` is the **single** list shared with `PasswordMiddleware` — two
copies would drift, and the drift is invisible in both directions (a leak, or a
needlessly uncacheable asset).

UI: `window.HAS_PATROL` (from `pageData.HasPatrol`) says whether this tenant
owns any effort at all. When it does not, the Active Pixels / Total Distance
rows are dimmed and inert and the animator's patrol chips are `unavailable` —
including when a **share link** names them, which is dropped rather than
switched on. An empty layer reads as a broken feature; a dimmed one reads as
"not yours".

Pinned by `go test ./srv/ -run 'Tenant|RequestEnv'` and the
"Patrol data tenants" block in `tests/api_tests.sh`. Note the assertion there
is *"does not see THESE pixels"*, not "sees nothing": a tenant with its own
patrols is the normal case.

### Unauthenticated Endpoints

These paths bypass password check (see `srv/auth_middleware.go`):
- `/static/downloads/*` - Downloadable files
- `/robots.txt` - SEO
- `/sitemap.xml` - SEO
- `/static/robots.txt`
- `/static/sitemap.xml`

### Admin-Only Endpoints

Require admin login (see `srv/server.go` lines with `RequireAdmin`):
- `POST /admin/approve`, `/admin/reject`
- `POST /admin/upload/fire`, `/admin/upload/ghsl`
- `POST /api/admin/approve-feature`, `/api/admin/reject-feature`
- `POST /api/admin/bulk-approve`, `/api/admin/bulk-reject`
- `POST /api/admin/delete-upload`, `/api/admin/hide-notification`

---

## Background Workers

| Worker | Schedule | File | Description |
|--------|----------|------|-------------|
| Upload Queue | Every 2s | `srv/upload_queue.go` | Process GPX uploads |
| GPX Learner | Continuous | `srv/gpx_learner.go` | Pattern detection |
| Fire NRT | 3am UTC | `srv/fire_nrt.go` | Download daily fires |
| Fire Backfill | 4am UTC | `srv/fire_nrt.go` | Historical data |
| Narrative Cache | Weekly | `srv/fire_narrative_cache.go` | Pre-compute narratives |
| Publication Sync | Daily | `srv/research.go` | OpenAlex publications |
| FAOLEX Sync | Sundays | `srv/faolex_scraper.go` | Legal documents |

### FAOLEX Legal Documents

Syncs conservation-related legal documents from FAO FAOLEX database:
- Runs weekly on Sundays via `RunFAOLEXSync()`
- Creates `legal_documents` table on first run
- Searches by country ISO code and GADM region names
- Creates notifications for relevant new documents

API endpoint: `GET /api/parks/{id}/legal`

---

## Icon System - Lucide Icons

**All emojis replaced with Lucide icon font** for consistent styling across dark theme.

### Infrastructure

- **CDN**: `https://unpkg.com/lucide-static@latest/font/lucide.css` (2KB)
- **Icon font**: Uses `icon-{name}` classes with CSS `::before` pseudo-elements
- **Colors**: CSS utility classes (`.icon-color-fire`, `.icon-color-success`, etc.)

### Helper Functions

```javascript
// Generate icon HTML
icon('flame', 'fire')  // → <i class="icon-flame icon-color-fire"></i>
icon('zap', 'warning', 'lg')  // → <i class="icon-zap icon-color-warning icon-size-lg"></i>

// Convert backend emojis to icons
emojiToIcon('🔥')  // → <i class="icon-flame icon-color-fire"></i>
```

### Icon Colors

| Class | Color | Usage |
|-------|-------|-------|
| `icon-color-fire` | #ef4444 (red) | Active fires, errors |
| `icon-color-warning` | #f59e0b (orange) | Warnings, approaching fires |
| `icon-color-success` | #22c55e (green) | Success, checkmarks |
| `icon-color-info` | #3b82f6 (blue) | Info, downloads, water |
| `icon-color-cool` | #60a5fa (light blue) | Cooling fires |
| `icon-color-neutral` | #888 (gray) | Default, points, settlements |
| `icon-color-tree` | #22c55e (green) | Forest, nature |

### Common Icons

| Icon | Class | Usage |
|------|-------|-------|
| 🔥 | `icon-flame` | Active fires |
| ⚡ | `icon-zap` | Rapid fire spread |
| ❄️ | `icon-snowflake` | Cooling fires |
| ⚠️ | `icon-alert-triangle` | Warnings |
| ✓ | `icon-check` | Success |
| ✗ | `icon-x` | Errors |
| 🚶 | `icon-footprints` | Foot patrol |
| 🚗 | `icon-car` | Vehicle patrol |
| ✈️ | `icon-plane` | Aircraft patrol |
| 🌳 | `icon-tree-pine` | Forest/deforestation |
| 🏘️ | `icon-home` | Settlements |
| 🦁 | `icon-bug` | Biodiversity |
| ☀️ | `icon-sun` | Dry season |
| 🌧️ | `icon-cloud-rain` | Rainy season |
| 🗺️ | `icon-map` | Map/infrastructure |

### Usage Locations

1. **Fire status indicators** - Popup "Currently Active" section
2. **Notification panel** - All notification types (fire, upload, download, etc.)
3. **Star report stats** - Quick stats display
4. **Biodiversity/Climate sections** - Section titles
5. **Admin panel** - Upload section headers
6. **Movement types** - GPX track classification

### Benefits

- **Consistent styling** - All icons match dark theme colors
- **Performance** - 2KB font vs ~20KB emoji fallbacks (-90%)
- **Cross-platform** - No rendering differences between browsers/OS
- **Flexibility** - Easy color changes via CSS
- **Professional** - Clean, recognizable icon shapes

**Documentation**: See `LUCIDE_ICONS_PROGRESS.md` for full implementation details.

