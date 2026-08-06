# AOI overlays — status & handover

Status: **commits 1–7/7 landed; the queue is running.** Rewritten 2026-08-06
from a plan into a handover, updated 2026-08-07. The design rationale is in the
code comments and the commit messages (`git log --oneline --grep '^aoi'`); this
file is the map of what exists, what is measured, and what is left.

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
  `animateAOI()` → fit bbox + set the window + `Animator.open()`.

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

**GFW**: `tiles_for_bbox` gives **252** tiles at 0.5° for this AOI.

**OSM enrichment gaps closed** while lifting the code: COG_Nouabalé-Ndoki
+1,748 roads, DZA_Djurdjura +2,505 places / +46,866 roads. `osm_places` and
`roads_heigit` are now **163/163 parks**.

**Tests**: `./tests/api_tests.sh` 45/45, including AOI visibility,
404-not-403, malformed id, a back-to-back owner/test2026 request proving the
response cache does not leak, and two assertions that AOI rows are absent from
the bbox-keyed endpoints. UI verified both ways: `$AOI_OWNER_PWD` sees the
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
`logs/*.log` glob. `clip` is done, `fire_gap` is in progress (~2M new
detections, 570 five-day windows × 3 sensors).

`fire_gap` finishes by refreshing `aoi_fires` and running
`build_fire_grid_agg.py --since` — **without that the animator shows stale
fires.** `fire_v5` then runs automatically via `depends_on`; its row is still
`pending` even though the chain has been run once by hand, which is correct:
it must re-run once the gap is filled, and it will take ~8 min and 1.3 GB RSS
over a much larger detection set than the 21,189-group first run.
* Missing runners (`RUNNERS` returns `blocked`): `ghsl` (4 tiles, R2023A E2030
  100 m, `R7_C20` 1.5 MB / `R7_C21` 1.7 MB / `R8_C20` 11.5 MB / `R8_C21` 6.6 MB
  under `GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2030_GLOBE_R2023A_54009_100/V1-0/tiles/`;
  the 10 m tiles now 404. `process_settlement_polygons.py` hardcodes a path
  that doesn't exist — generalise it to a tile dir); `gsw` (3 missing 10×10°
  occurrence tiles: `occ_20E_0N`, `occ_30E_10N`, `occ_30E_0N`); `hydro`
  (HydroRIVERS_v10_af + HydroLAKES; stopgap = PBF waterways from the `osm`
  unit); `deforestation` (derive from the GFW alerts already fetched — prefer
  that over a Hansen download).
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
* Hover **tooltip** (currently click-only, no hover card).
* Share param `aoi=<id>` and the popup/section state that goes with it.
* **Notifications** for an AOI must be principal-filtered or the name leaks in
  a title — same shape as `miningNotifSQLFilter()` in `srv/mining_flag.go`.
  Nothing generates them yet, so this is a precondition, not a bug.
* A **Layers** section (pins keyed `aoi:<id>:<type>`). The existing pin
  machinery already works because `loadPinnedLayer()` goes through
  `apiBase()`, but the pins are keyed by bare id, so an AOI and a park of the
  same name would share a pin. Not reachable today (ids are disjoint).

**d. Animator.** `animateAOI()` ships the cheap 80%: fit the AOI bbox, set the
date range to the AOI window, open the animator. What is left is the polygon
clip — `anim.js` clips to a *rectangle* (`A.bboxFixed`), so frames currently
spill into the bbox corners outside the polygon. Replace the rect clip with
one `ctx.clip()` on the polygon path. Layer chips work unchanged; every frames
endpoint is bbox-scoped.

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
  to the v5 chain, which has its own delete. The two writers are only safe
  because they own disjoint feature types for the same `park_id`.
* Frontend: build every park/AOI endpoint URL through `apiBase(id)`. A raw
  `/api/parks/${id}/` string works for parks and 404s for AOIs.
* Verification set: `/api/aois` empty for `test2026`, populated for
  `$AOI_OWNER_PWD`; `/api/aois/XSA_Study_Area/*` 404 vs 200;
  `/api/parks/XSA_Study_Area/*` 404 for everyone; **CAF_Chinko = 8,753**;
  runner killed mid-slice resumes with no duplicate and no lost unit;
  `./tests/run_all.sh`.
