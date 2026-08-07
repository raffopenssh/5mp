# AOI overlays — status & handover

Status: **commits 1–9 landed; the queue is running.** Rewritten 2026-08-06
from a plan into a handover, updated 2026-08-07 (animator polygon clip, `?aoi=`
share links, the `deforestation` runner). The design rationale is in the
code comments and the commit messages (`git log --oneline --grep '^aoi'`); this
file is the map of what exists, what is measured, and what is left.

---

## → Current handover: `AGENTS.md "Areas of interest"`

Written 2026-08-07. It supersedes the two "Resume here" sections below (kept
for the trail). **This file remains the design rationale and the record of
measured facts** — read §1 (the isolation rules and the two holes they plug),
§2 (numbers; do not re-derive them) and §4 (gotchas) before touching anything.

One entry in §3a is now **reversed**: the AOI's pre-2024 deforestation *should*
come from Hansen. Tiles are 45–116 MB COGs read through `/vsicurl` in 0.6 s per
2° window, not "tens of GB", and GFW alerts only start in 2024. See
`AGENTS.md "Areas of interest"` §2f.

---

## Resume here (2026-08-07, later — superseded by AGENTS.md "Areas of interest")

Three units are **in flight in tmux** (`aoiv5`, `aoighsl`, `aoidefo`) — check
them first, they may well have finished:

```bash
python3 scripts/aoi_runner.py --status
tmux ls
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM feature_geometries
  WHERE park_id='CAF_Chinko' AND feature_type='fire_trajectory'"   # must be 8753
```

`fire_gap` (3,182,542 detections) and **`gfw` are done** — 252/252 tiles,
2,225,511 alerts, `data/gfw_alerts/XSA_Study_Area.json` written.

1. **`fire_v5`** — started 18:39, step 2/4 (`rebuild_fire_trajectories_v5.py`)
   over a much larger detection set than the 21,189-group first run. One long
   restartable unit; if tmux died, rerun `--dataset fire_v5` or let the noon
   cron take it.
2. **`deforestation`** — started 19:05; it found **3,505 quality cells**
   (2024–2026) inside the polygon. Still to verify when it lands (this is the
   sanity check §3a asked for and it has *not* been done yet):
   ```bash
   sqlite3 db.sqlite3 "SELECT COUNT(*) FROM deforestation_events
     WHERE park_id='XSA_Study_Area' AND polygon_ids LIKE 'deforest_gfw_%'"
   python3 scripts/aoi_clip.py --aoi XSA_Study_Area --dry-run   # then for real
   # then re-count: the deforest_gfw_ rows must survive the clip
   ```
3. **`ghsl`** — started 19:03, 4 tiles (`R8_C21 R8_C22 R9_C21 R9_C22`).
   Each tile is ~4 min of vectorising and yields ~40k polygons, so expect
   ~170k built-up polygons for the AOI, then one clustering pass.
   **It crashed on its first tile with `database is locked`** — the v5 fire
   chain holds SQLite's single write lock for minutes, which is longer than
   the 60 s busy_timeout, and a tile insert is 40k rows landing right in the
   middle of it. Fixed by `ghsl_tiles.write_rows()` /
   `aoi_runner.retry_write()` (exponential backoff up to ~60 s, wrapping
   `release()` and `progress()` too — a failed `release()` is what strands a
   unit in `running` until its 6 h lease expires, which is exactly what
   happened here). A tmux session `ghslkick` waits for the lock, clears the
   stranded lease and reruns it; if it is gone, do that by hand:
   ```bash
   sqlite3 db.sqlite3 "UPDATE aoi_datasets SET state='pending', lease_owner=NULL,
     lease_until=NULL WHERE aoi_id='XSA_Study_Area' AND dataset='ghsl'"
   python3 scripts/aoi_runner.py --aoi XSA_Study_Area --dataset ghsl --minutes 180
   ```
   **Sanity check when it lands**: `park_settlements` for the AOI should jump
   from the 145 clipped preview rows to thousands, and every row's
   `polygon_ids` must start `settlement_ghsl_` (the preview rows are deleted by
   the runner's handover delete — see SUPERSEDED_BY below).
4. Remaining blocked runners: `gsw`, `hydro` (both need a download, §3a).
5. Then §3e (report) and §3f (write endpoints + admin tab). §3c's Layers
   item is still open; the **tooltip is now done**.

### Landed this session (commit `31158bf`)

* **`scripts/ghsl_tiles.py`** — the GHSL blocker is gone.
  `process_settlement_polygons.py` read one hardcoded
  `data/ghsl/ghsl_pop_2030.zip` that does not exist on this machine, so the
  GHSL step of park onboarding *and* the AOI queue was a silent no-op. Tiles
  (R2023A E2030 100 m) are now fetched on demand and cached **by tile id**
  under `data/ghsl/tiles/` (gitignored via `data/ghsl/`), so a park onboarding
  or a second AOI over the same ground pays nothing — rule 2.
  **The tile grid is 1-indexed.** `R7_C20` in the JRC naming is grid cell
  (row 6, col 19) counting from zero; verified against the tile's own affine
  (`origin = (959000, 3000000)`). Off-by-one does not fail loudly — it reads a
  window 2,000 km away, or raises `Intersection is empty` if you are lucky.
* **`rebuild_events_enhanced.py`**: `rebuild_settlements_for_park()` split out
  of the global rebuild, so a single park (or an AOI) reaches the *canonical*
  clusterer and classifier instead of the codebase growing a second one.
  `_cluster_polygons()` is now grid-accelerated single linkage. The single-
  linkage partition is unique, so it is the same answer — **asserted identical**
  on ETH_Borana / TZA_Ngorongoro / CAF_Chinko (207 s → 0.11 s). The old
  quadratic version simply does not finish on an AOI's ~170k polygons. The
  longitude cell span is scaled by `1/cos(lat)`; without that the neighbour
  search misses links near the equator-distant edges.
* **`aoi_clip.py` `SUPERSEDED_BY`** — a preview layer is dropped once its real
  ingest is `done`. This is not tidiness: the real ingest covers the **whole**
  polygon *including* the ~10% inside parks that the preview stood in for, so
  keeping both double counts exactly that overlap. The clip still deletes its
  own previous preview rows (atomic handover); `DELETE_EXCLUDE` keeps the real
  ones. `settlement_ghsl_%` joins `deforest_gfw_%` there — there are now
  **four** writers of `(park_id=<aoi>, feature_type)` and the whole scheme
  rests on each deleting a disjoint id prefix.
* **AOI hover tooltip** (§3c): name, area, window, `N/M data layers ready`,
  parks inside. Registered through `MapTip`, not a per-layer popup.
  `MapTip.register(...).html()` may now return **falsy to decline** a feature —
  the hit-test falls through to whatever is underneath and, if nothing renders,
  there is no tip *and no click interception*. The AOI tip uses this to stand
  down over a park polygon, mirroring the click precedence rule ("a park inside
  an AOI wins"). AOI feature ids are per-coordinate, not per-counter, because
  the AOI is built one tile per queue unit across days and a counter would
  renumber on resume.

**Do not re-litigate**: whether AOI rows belong in the bbox-keyed endpoints
(no — §1), whether the clip is a preview (yes, and it must say so), whether
FIRMS product selection can be done by date arithmetic (no — §2), whether the
AOI's deforestation should come from Hansen (no — §3a), whether the 10 m GHSL
product can be used (it is not published as tiles; 100 m is what exists and is
the same source the parks' settlements came from, so numbers stay comparable).

---

## Resume here (2026-08-07, earlier — superseded, kept for the trail)

`fire_gap` is **done** (3,182,542 detections, all 570 windows; it refreshed
`aoi_fires` and ran `build_fire_grid_agg.py --since` on its final pass).
`fire_v5` was started by hand in tmux (`aoiv5`) and takes ~8 min over the much
larger detection set.

1. **Check `fire_v5` landed**, then re-check the isolation gate:
   ```bash
   python3 scripts/aoi_runner.py --status
   sqlite3 db.sqlite3 "SELECT COUNT(*) FROM feature_geometries
     WHERE park_id='CAF_Chinko' AND feature_type='fire_trajectory'"   # must be 8753
   ```
   If tmux is gone, the noon cron picks it up; or run it directly
   (`--dataset fire_v5`). It is one long restartable unit.
2. **Then `gfw`** (252 tiles, per-tile cache shared with the park rotation).
   Budget it: `--dataset gfw --budget 60 --minutes 90` a few times, or let the
   cron grind it. On its final tile it writes
   `data/gfw_alerts/XSA_Study_Area.json`.
3. **Then `deforestation`** runs itself via `depends_on: gfw` — one unit,
   derived from that scan file. **Sanity-check the first run**: alert cells
   outside the polygon but inside the bbox must be absent (`clip_geom`), and
   `aoi_clip.py` must still be re-runnable afterwards without deleting the
   `deforest_gfw_%` rows (`python3 scripts/aoi_clip.py --aoi XSA_Study_Area
   --dry-run` then for real, then re-count).
4. Remaining blocked runners are `ghsl`, `gsw`, `hydro` — all need a download,
   see §3a. `ghsl` is the closest: the 100 m R2023A E2030 tiles are confirmed
   live (R7_C20/R8_C20 both HTTP 200 as of 2026-08-07); what is missing is
   generalising `process_settlement_polygons.py` off its single hardcoded
   `data/ghsl/ghsl_pop_2030.zip` onto a tile directory.
5. Then §3e (report) and §3f (write endpoints + admin tab). §3c's tooltip and
   Layers-section items are still open; the share param and the animator clip
   are **done** (see below).

**Do not re-litigate**: whether AOI rows belong in the bbox-keyed endpoints
(no — §1), whether the clip is a preview (yes, and it must say so), whether
FIRMS product selection can be done by date arithmetic (no — §2), whether the
AOI's deforestation should come from Hansen (no — §3a).

---

## 0. Mental model — read this first

**An AOI is a power bounding box.** The app already had a user-drawn area
(`currentBbox` in globe.html): ephemeral, client-side, resolved into whichever
parks it touches. An AOI is that same object with five upgrades:

| drawn bbox | AOI |
|---|---|
| rectangle | arbitrary polygon |
| browser memory | row in `aois`, owned by a principal |
| answers only from data already ingested | **has data fetched for it**, over days, by a cron |
| whatever the time slider says | a **fixed analysis window** |
| union of the parks it touches | its own precomputed layers **plus** those parks |

Three rules keep it from sprawling. They are load-bearing; changing one means
re-reading everything below.

1. **An AOI is not a park.** Separate table, separate id space, separate route
   prefix. Not in `keystones_with_boundaries.json`, never a
   `fire_detections.protected_area_id`, no `/api/parks/{id}/*` surface. Species
   / publications / legal / climate stay park-level — they are meaningless
   averaged over 482,000 km².
2. **Ingest is keyed by geography, not by owner.** A private AOI may *cause*
   data to be fetched, but the rows/tiles land in the shared stores
   (`fire_detections`, `data/gfw_tiles/`, country PBFs). Every park in reach
   benefits permanently; a second AOI over the same ground costs nothing. Only
   the derived AOI-shaped artefacts are private. **When adding any dataset,
   ask: could this artefact be named after the tile instead of the AOI?**
3. **The work is a resumable queue ground down by a cron.** A user draws an
   area and the answer arrives over days. That is the product, so the UI must
   show progress and partial coverage rather than pretend.

Instance #1 is `XSA_Study_Area`, built as an instance of the general
mechanism, not a special case.

---

## 1. What exists now

### Schema — migrations `040-aoi-overlays.sql`, `041-aoi-parks.sql`
`aois`, `principals`, `aoi_grants`, `aoi_datasets` (the work queue),
`aoi_fires` (polygon membership cache), `aoi_parks` (precomputed park overlap,
both fractions). Applied; 41 migrations total.

### Go — `srv/aoi.go`
* `principalRef(pwd)` = `sha256(pwd)[:16]`. The secret is never stored.
  `SeedPrincipals()` runs at startup (6 principals seeded).
* `aoiVisibleSQL` — the single place visibility is decided; all reads go
  through it. `requireAOI()` answers **404, never 403**, so an id is not an
  oracle.
* `RefreshAOIIDs()` / `IsAOIID()` — the AOI id set, cached at startup.
* `aoiGate()` wraps the **existing park handlers** for the AOI routes. They key
  off `PathValue("id")` and read the same tables the `--aoi` chain writes, so
  there is no second copy of the narrative logic to drift.

### The one subtle hole, and its plug
AOI rows live in park-shaped tables (`feature_geometries.park_id`,
`fire_narrative_cache.park_id`) and `XSA_Study_Area` happens to satisfy
`parkIDRe`. So `/api/parks/XSA_Study_Area/fire-narrative` would have served a
private AOI with **no visibility check at all**. `ParkIDMiddleware` now 404s on
any id in the AOI set (also for `/api/park/`, `/park/`, and `?park=`).
**Any new park-shaped storage an AOI writes into needs the same thought.**

`response_cache.go cacheKey()` gained a visibility fingerprint, applied to
*every* cacheable path rather than an AOI-only list — such a list is exactly
the thing that goes stale. Cost: one cache entry per active password.

### The second hole: queries with no park_id at all
Visibility at the `/api/aois/*` boundary covers every query keyed by an
explicit park id. It does **not** cover the ones keyed by bbox alone:
`/api/features-in-bbox`, `/api/fire-anim-trajectories`, the dashboard's global
`SUM(stat_value)` counters, the global settlement count, the two `osm_places`
nearest-place lookups. Over the XSA bbox the feature browser was serving 573
of its top 1,500 rows from a private AOI. The same shape also **double
counts**: an AOI is rebuilt over ground four parks already cover.

`aoiExcludeSQL(col)` in `srv/aoi.go` is the one filter (leading `AND`,
subquery over the tiny `aois` table, plus the `'aoi:%'` scope prefix that
`run_osm` uses for `osm_places`/`roads_heigit`). Applied at all six sites; two
regression tests assert it over the XSA bbox. **Any new query over
park-shaped storage that does not take a park_id needs it.**

### Routes
```
GET /api/aois                                  list visible to the principal
GET /api/aois/{id}                             metadata + coverage + parks
GET /api/aois/{id}/fire-narrative              \
GET /api/aois/{id}/fire-trend                   |  park handlers,
GET /api/aois/{id}/fire-realtime                |  gated by aoiGate()
GET /api/aois/{id}/features?type=               |
GET /api/aois/{id}/deforestation-narrative      |
GET /api/aois/{id}/settlement-narrative        /
GET /api/aois/{id}/export.geojson
```
Still to add (§3): `POST /api/aois`, `POST /api/aois/{id}/refresh`,
`DELETE /api/aois/{id}`, `export.kml`.

### Frontend — `srv/templates/globe.html`
* **`apiBase(id)`** is the single routing point: `/api/aois/{id}` for an id in
  `window.AOI_IDS`, `/api/parks/{id}` otherwise. 22 call sites. Filled by
  `loadAOIs()` at map load, before anything can reference an AOI. Because
  `/api/parks/{aoi}` 404s, a missed call site fails loudly rather than leaking.
* `aois` source + `aois-outline` (dashed blue) and `aois-fill` (4% opacity,
  only so the interior is clickable). A park inside an AOI wins the click.
* `showAOIPopup()` reuses the park popup's chrome: Overview & coverage, Fire,
  Settlements, Deforestation — the last three are the *unmodified* park
  fetchers. Species/research/legal/climate are absent by design; Overview
  links to the intersecting parks instead.
* `aoiCoverageHTML()` renders one progress bar per `aoi_datasets` row.
* `#aoi-toggle` chip (hidden unless the principal can see an AOI),
  `animateAOI()` → fit bbox + `setTimeSliderRange()` to the AOI window +
  `Animator.open({aoi})` (polygon clip, §3d).

### Python
| file | what |
|---|---|
| `scripts/aoi_lib.py` | connect, `principal_ref` (mirrors Go), `upsert_aoi`, `seed_datasets`, `firms_area`, `inject_aoi` |
| `scripts/aoi_admin.py` | `create` / `list` / `show` |
| `scripts/aoi_clip.py` | **Phase A**: clip the overlapped parks' data into the AOI; writes `aoi_parks` |
| `scripts/build_aoi_fires.py` | polygon membership → `aoi_fires`; `--since` incremental |
| `scripts/aoi_runner.py` | the queue runner; `--daily` / `--aoi X --dataset Y --budget N` / `--status` |
| `scripts/osm_pbf.py` | Geofabrik+osmium, lifted out of the retired turbidity script; `--enrich-missing` |

`--aoi` added to `rebuild_fire_trajectories_v5.py`,
`load_fire_groups_to_db.py`, `precompute_narratives_v5.py`. It injects the AOI
into the **in-memory** parks dict only. The keystones *file* is never written —
that is the entire isolation argument (rule 1): an AOI there would make
`park_assigner` reassign detections away from the four parks it overlaps.

`fire_source.load_aoi_fires()` joins `fire_detections` through `aoi_fires`.
Still one fire source: `aoi_fires` holds **ids**, not detections.

---

## 2. Measured facts (do not re-derive)

**XSA_Study_Area**: 7-vertex polygon, bbox `22.7038,4.2520 .. 31.2974,10.9665`,
**485,150 km²**, window 2024-01-01.., owner `$AOI_OWNER_PWD` (appended to
`ACCESS_PASSWORDS` in `secrets.env`, gitignored). Spans SSD, CAF, SDN, COD, TCD.
Park overlap, now precomputed in `aoi_parks` (frac of AOI / frac of park):
CAF_Chinko 4.1% / 100%, SSD_Southern 4.0% / 100%, COD_Bili-Uere 2.3% / 34.6%,
COD_Garamba 0.13% / 12.6% — **10.5% of the polygon inside any park**.

**Phase A clip** (`aoi_clip.py`, ~4 s, zero quota): 145 settlements, 40
deforestation events, 539 places, 8,845 river reaches, 141 roads, 373
`feature_geometries`. Two traps found while building it, both still live for
any future clip: `feature_id` must be re-keyed `<aoi>_<srcpark>`, not `<aoi>`
(the per-park counters all restart at 0, so four parks' `_0` rows collide and
`INSERT OR IGNORE` silently drops 106 of 373); and roads must be clipped by
geojson intersection, not by centroid, and kept whole.

**Built end to end** (with only pre-existing ingest, i.e. before `fire_gap`):
2,154,657 detections tested in bbox → **1,613,243 inside** (14 s) → **21,189
groups** (~8 min, 1.3 GB RSS) → 21,189 `feature_geometries` rows → a v5
narrative with real hash feature_ids (`XSA_Study_Area_2024_grp_36960ecf`).
`data/fire_groups_v5/XSA_Study_Area.json` is 24 MB (gitignored).

**Isolation gate holds: `CAF_Chinko` fire_trajectory = 8,753 before and after.**
Re-check this after any AOI work; do not trust the diff to be zero.

**The data gap** (why `fire_gap` exists): only 10.5% of the polygon is inside
any park and 53.6% inside the 100 km ingest buffer. Median detections per 1°
cell: 122,179 inside the buffer, 1,050 outside. A 100× cliff at the buffer
edge — that is ingest scope, not fire behaviour.

**FIRMS**: area API rejects windows > 5 days; quota is 5000 transactions /
10 min. 2024-01-01..today ≈ 190 windows × 3 sensors ≈ **570 requests**, ~7M
rows, tens of minutes total. So `fire_gap` is one or two slices, not weeks.
**Always go through `scripts/firms_api.py`** — a hand-rolled 10-day URL is a
silent 400 (that bug shipped once).

**FIRMS product selection cannot be computed from the date** (found here, fixed
in `1c3330a`, affects the nightly cron too). Measured 2026-08-06 via
`/api/data_availability/csv/KEY/ALL`:

| sensor | SP | NRT |
|---|---|---|
| VIIRS_NOAA20 | .. 2026-05-31 | 2026-06-01 .. |
| VIIRS_SNPP | .. 2026-04-27 | 2026-04-28 .. |
| VIIRS_NOAA21 | **does not exist** | 2024-01-17 .. |

The old fixed 45-day threshold was wrong two ways: `VIIRS_NOAA21_SP` 400s
("Invalid source.") for every window, and asking the wrong side of a real
cutover returns **HTTP 200 with a header-only CSV** — zero detections that read
as "no fires". This backfill lost 34 of its first 414 windows to it.
`pick_source()` now reads availability (cached per process, SP preferred where
both cover) and returns `None` when no product covers the date; callers must
treat that as *skip*, not as a failure to retry. `fire_gap` requeues genuinely
failed windows for up to three passes.

**GFW**: `tiles_for_bbox` gives **252** tiles at 0.5° for this AOI.

**OSM enrichment gaps closed** while lifting the code: COG_Nouabalé-Ndoki
+1,748 roads, DZA_Djurdjura +2,505 places / +46,866 roads. `osm_places` and
`roads_heigit` are now **163/163 parks**.

**Tests**: `./tests/api_tests.sh` 45/45, including AOI visibility,
404-not-403, malformed id, a back-to-back owner/test2026 request proving the
response cache does not leak, and two assertions that AOI rows are absent from
the bbox-keyed endpoints. UI verified both ways: the owner principal sees the
polygon, the chip and populated sections; `test2026` gets `AOI_IDS` empty, the
chip hidden and a 0-feature source.

**Pre-existing, unrelated**: `check_fire_consistency.py` reports ~39k issues
across ZMB/ZWE parks from an earlier partial rebuild. Not caused by this work
(no XSA drift) — but it means the check cannot currently be used as a clean
gate. Fix it separately before relying on it.

---

## 3. What is left

Ordered; each is independently shippable.

**a. Finish the runner.** Cron installed (`0 12 * * *`, deliberately far from
the 03:00 fire job); `logs/aoi.log` is already covered by `5mp.logrotate`'s
`logs/*.log` glob. `clip`, `fire_gap` done; `fire_v5` in flight.

`fire_gap` finishes by refreshing `aoi_fires` and running
`build_fire_grid_agg.py --since` — **without that the animator shows stale
fires.** `fire_v5` then runs automatically via `depends_on`; ~8 min and 1.3 GB
RSS over a much larger detection set than the 21,189-group first run.
* **`deforestation` is implemented** (2026-08-07): derived from the GFW alerts
  the `gfw` unit already fetched, via the canonical
  `daily_park_refresh.ingest_gfw_deforestation()` with new optional
  `bbox`/`clip_geom` args (an AOI has no row in the parks bbox source and is
  not a rectangle). **Not Hansen**: tens of GB of tiles for one polygon,
  stops at 2023, and would give the AOI a *different method* from the parks it
  overlaps — the alerts are the same source the parks' own 2024+ events come
  from, so the numbers stay comparable. `run_gfw` collates
  `data/gfw_alerts/{aoi}.json` on its last tile (all cache hits by then) and
  pins `since` into the cursor, or a resumed scan stops matching its own cache
  keys.
* Still missing runners (`RUNNERS` returns `blocked`): `gsw` (3 missing
  10×10° occurrence tiles: `occ_20E_0N`, `occ_30E_10N`, `occ_30E_0N`);
  `hydro` (HydroRIVERS_v10_af + HydroLAKES; stopgap = PBF waterways from the
  `osm` unit). **`ghsl` is implemented** (2026-08-07): one 1000 km Mollweide
  tile per unit via `scripts/ghsl_tiles.py`, last unit clusters into
  `park_settlements` through the canonical `EventRebuilder`. The 10 m tiles
  still 404; 100 m R2023A E2030 is the product.
* Kill switch: `UPDATE aoi_datasets SET enabled=0 WHERE aoi_id='XSA_Study_Area'`.

**b. ~~Phase A: clip from neighbours.~~** Done — `scripts/aoi_clip.py`,
dataset `clip` at priority 5. See §2 for the numbers and the two re-keying
traps.

**c. ~~UI.~~** Done for read: `aois` map source with a dashed outline and a
click-only fill, `showAOIPopup()` (Overview & coverage / Fire / Settlements /
Deforestation), the per-dataset coverage table, `apiBase()` routing and the
`#aoi-toggle` chip. AOIs are served by `/api/aois`, **not** folded into
`/api/areas` as originally sketched — `/api/areas` is cached, unauthenticated
in effect, and consumed by a dozen park-shaped code paths; a visibility-scoped
member in it would have been the same hole as §1's park-route hole.

Still open:
* ~~Hover **tooltip**.~~ Done — via `MapTip`, declining over park polygons.
* ~~Share param `aoi=<id>`.~~ Done: `?aoi=<id>&aoi_sections=fire,ghsl,…`, plus
  `anim_aoi` so a shared animation restores its polygon clip. Deliberately
  **separate params from `?popup=`/`&sections=`**, whose restorer resolves the
  id against the `areas` source — an AOI is never in it (rule 1), and falling
  through to a park lookup is the confusion `apiBase()` exists to prevent. An
  id the principal cannot see simply does nothing: `loadAOIs()` is the gate,
  and a pending request is dropped if the id never appears.
* **Notifications** for an AOI must be principal-filtered or the name leaks in
  a title — same shape as `miningNotifSQLFilter()` in `srv/mining_flag.go`.
  Nothing generates them yet, so this is a precondition, not a bug.
* A **Layers** section (pins keyed `aoi:<id>:<type>`). The existing pin
  machinery already works because `loadPinnedLayer()` goes through
  `apiBase()`, but the pins are keyed by bare id, so an AOI and a park of the
  same name would share a pin. Not reachable today (ids are disjoint).

**d. ~~Animator.~~** Done. `draw()` takes an optional `clipGeom` and traces
every ring (`ctx.clip('evenodd')`, so a future hole stays a hole); the fetch
stays bbox-scoped because every frames endpoint is. Verified visually: the
fire grid now stops at the AOI's angled edges instead of filling the bbox
corners.

One trap found doing it: `animateAOI()` used to assign `dateFrom`/`dateTo`
directly. The animator reads those globals but the slider labels, presets and
pinned layers do not, so the chip read "9 May 2026" while the animation ran
from 2024. It now goes through `setTimeSliderRange()`, the single codepath.
**Any new caller that wants a specific window must do the same.**

**e. Report.** `collectReportParks()` already folds starred bboxes in by
resolving them to parks. An AOI slots in as a new source: AOI-level sections
first (its own narratives + a coverage caveat), then the intersecting parks as
today. PDF/KML/CSV/XLSX inherit it.

**f. Write endpoints + admin "Access" tab.** `POST /api/aois` (draw-to-create),
`POST /api/aois/{id}/refresh`, `DELETE` (owner only; call `RefreshAOIIDs()`
after either). Admin tab: principals, AOI ownership, per-dataset toggles.

`/api/fire-frames`, `/api/features-in-bbox`, `/api/grid` stay **unfiltered**.
They serve raw geography that was always public within the app; the AOI polygon
is the only secret. An AOI is privacy for *the question being asked*, not for
the underlying pixels — don't pretend otherwise.

---

## 4. Gotchas

* `data/fire_groups_v5/XSA_Study_Area.json` is 24 MB and gitignored, as is
  `data/gfw_tiles/`.
* `srv/areas/areas.go loadKeystonesWithBoundaries` keeps only the first ring
  for point-in-polygon. The AOI store must not inherit that if a drawn AOI ever
  has holes.
* A zero-group AOI is a real state: `load_fire_groups_to_db.py` must delete
  rows before its empty-input early return, and `precompute_narratives_v5.py`
  must write an **empty** cache row (a missing row drops the API into the
  deprecated 17s Go path with `feature_id: null`). Both already handle it.
* After any bulk fire change: `build_fire_grid_agg.py --since`.
* `aoi_runner` must never write `fire_narrative_cache` directly — it shells out
  to `precompute_narratives_v5.py` (AGENTS.md single-writer rule).
* **Any new query over park-shaped storage that does not take an explicit
  `park_id` needs `aoiExcludeSQL()`** — for privacy *and* to avoid double
  counting an AOI over the parks it overlaps. See §1.
* `aoi_clip.py` must not touch `feature_type='fire_trajectory'`: that belongs
  to the v5 chain, which has its own delete. **Four** writers now share
  `(park_id=<aoi>, feature_type)` — clip, the v5 chain, the `deforestation`
  unit and the `ghsl` unit — and they are only safe because each deletes a
  disjoint id prefix. The clip's deletes skip `deforest_gfw_%` and
  `settlement_ghsl_%` (`DELETE_EXCLUDE` / `delete_rows()` in `aoi_clip.py`);
  a fifth writer needs the same treatment. A layer in `SUPERSEDED_BY` is
  additionally *not re-clipped* once its real ingest is done — otherwise the
  preview and the real thing double count the ground they share.
* Frontend: build every park/AOI endpoint URL through `apiBase(id)`. A raw
  `/api/parks/${id}/` string works for parks and 404s for AOIs.
* Verification set: `/api/aois` empty for `test2026`, populated for
  the owner principal; `/api/aois/XSA_Study_Area/*` 404 vs 200;
  `/api/parks/XSA_Study_Area/*` 404 for everyone; **CAF_Chinko = 8,753**;
  runner killed mid-slice resumes with no duplicate and no lost unit;
  `./tests/run_all.sh`.
