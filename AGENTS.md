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
`park_basin_rivers` (migration 039, 163 parks, upstream polygon + downstream
trace + Strahler orders), `GET /api/parks/{id}/basin` (upstream km², upstream
river km total/order-3+, downstream length + outlet river name), pin buttons and
a plain-language watershed line in the popup's Roads/Rivers/Places section. QA
with `python3 scripts/check_basin_coverage.py`. Coverage <0.2 for 26
divide/endorheic parks is real, not a bug — hence `flow_corridor.scan_geom()`
scans (basin clipped to 200 km) ∪ park.

---

## Areas of interest (AOI)

A user-drawn polygon promoted to a first-class object: arbitrary geometry, a
fixed analysis window, an owner, and data fetched *for it* over days by a cron.
Instance #1 is `XSA_Study_Area` (485,150 km², owner `$AOI_OWNER_PWD`).
Full handover: `docs/PLAN_AOI_OVERLAY.md`.

**Current handover: `docs/AOI_HANDOVER_2.md`** — what is done, what is left,
and the verification set. `docs/PLAN_AOI_OVERLAY.md` remains the design
rationale and the measured-facts record.

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
- `chooseStep()`: ≤92d→day, ≤800d→week, else month. GIF export via `gifenc` CDN (80 frames, 720px; hidden on mobile).

**Server-side** (`fire_frames.go`):
- `/api/fire-frames?bbox&from&to&step=day|week|month&res=0.1` reads pre-agg tables (never `fire_detections` — a raw scan took 3min for full-span; agg is ~3s). Coarser `res` re-binned in SQL; `from` aligned to bucket start. If >200k points, auto-doubles `res` up to 2× twice instead of truncating.
- `layer=effort` returns `[xi, yi, km, uploads]` on the same grid (from `effort_data`+`grid_cells`, `movement_type='all', env='prod'`).
- Frame point format: `p: [[xi, yi, count, frp], ...]`, `d` = bucket start date.

**After bulk fire data changes**: rerun `python3 scripts/build_fire_grid_agg.py` (full) or `--since` — otherwise the animator shows stale fires. Daily cron keeps it fresh automatically.

---

**Popup fire chart**: single `areaSparkline` (globe.html) fed by `/api/parks/{id}/fire-trend`.
Series keys: `v`=fires (red, left axis), `v2`=groups (orange, right axis),
`v3`=prior-years ISO-week average (dashed gray, same axis as `v`, computed client-side
from full history). Don't add a second weekly chart.

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

