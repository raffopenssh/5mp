# Plan: private "Study Area" + per-credential park visibility (ACL)

Status: PLAN ONLY (nothing implemented except the two staged data files in §1).
Written 2026-08-06. Execute in a fresh conversation; this file is the brief.

## Goal

1. Add artificial reserve `XSA_Study_Area` (KML staged, §1), **visible only to
   password `$AOI_OWNER_PWD`**; invisible to all other passwords and test mode.
2. Full data processing for it back to **2024-01-01** (fires, deforestation,
   GHSL settlements, rivers, roads, places, climate, species, basin, narratives).
3. Visibility must be **generic**: a *principal* (today a password, later a
   user/NGO/gov account) is scoped to a set of parks. Same tables either way.
4. Ingest must be **generic + batched**: per-park declaration of which datasets
   to collect, executed in tiles/batches by the nightly crons so GFW/Overpass/
   FIRMS quotas are never burst.
5. No heavy work until tonight's crons finish (§8).

## Facts established (do not re-derive)

* Polygon: 7 vertices, bbox `22.704,4.252 .. 31.297,10.966`, **482,000 km2**
  (2nd largest object in the system; only DZA_Ahaggar 542k is bigger).
* It **fully contains** CAF_Chinko and SSD_Southern; overlaps COD_Bili-Uere
  (35%) and COD_Garamba (13%). Overlap is the central design constraint (§3).
* `fire_detections` already has **3.75M** rows in that bbox (2024: 731k,
  2025: 905k, 2026: 522k, plus 2018-23). **No FIRMS backfill needed** (§4).
* GHSL 10 m tiles now 404 on JRC; **100 m tiles are live**. Needed tiles:
  `R7_C20, R7_C21, R8_C20, R8_C21` (BUILT_S 100 m, ~19 MB total).
  `scripts/process_settlement_polygons.py` hardcodes
  `data/ghsl/ghsl_pop_2030.zip`, and `data/ghsl/` does not exist -> needs a
  fetch step + generalisation to a tile dir.
* `data/hydro_source/` also absent -> rivers/lakes need download or reuse (§5).
* 163 parks in `data/keystones_with_boundaries.json` (1 onboarded: DZA_Djurdjura).

## 0. Can we reuse onboard_park.py?

**Partially: reuse its shape, not its path.** `scripts/onboard_park.py` starts
from a **WDPA id** (`fetch_pp_area(wdpa_id)`), and `park_onboarding_requests`
has `wdpa_id INTEGER NOT NULL UNIQUE`. Our area has no WDPA id.

* Refactor the post-geometry half into `run_pipeline(park_id, geometry,
  datasets, window)`.
* Add entry point `--from-geojson FILE --park-id XSA_Study_Area` that skips
  Protected Planet and calls `run_pipeline`.
* Keep `park_onboarding_requests` for the WDPA flow; the study area is seeded
  by CLI + rows in the new `park_datasets` table (§5.1).
* Do **not** run its all-time FIRMS backfill (§4), and keep its
  `sudo systemctl restart 5mp` to one deliberate restart (§8).

## 1. Files already staged

```
data/study_areas/XSA_Study_Area.kml       # original upload
data/study_areas/XSA_Study_Area.geojson   # {"type":"Polygon","coordinates":[[...]]}
```

Park id `XSA_Study_Area` (verify against `parkIDRe` in
`srv/park_id_middleware.go`: `^[A-Z]{3}_[\pL0-9_'\-.]+$` -> OK).
Keystone entry: `country_code:"XSA", country:"Central Africa (study)",
name:"Study Area", wdpa_id:"", area_km2:482087,
coordinates{lat:7.6,lon:27.0}, geometry, onboarded_at`, plus two new
additive flags (old readers ignore them):

```json
"restricted": true,   // never served to unscoped principals
"overlay":    true    // overlaps other parks; excluded from park_assigner
```

## 2. Visibility / ACL layer (the reusable part)

### 2.1 `db/migrations/040-park-acl.sql`

```sql
CREATE TABLE IF NOT EXISTS principals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,            -- 'password' | 'user' | 'org'
  ref  TEXT NOT NULL,            -- sha256(pwd)[:16] | user_id | org slug
  label TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(kind, ref)
);
CREATE TABLE IF NOT EXISTS park_grants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  principal_id INTEGER NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
  park_id TEXT,                                  -- NULL = wildcard
  scope   TEXT NOT NULL DEFAULT 'view',          -- 'view' | 'all_public'
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(principal_id, park_id, scope)
);
CREATE INDEX IF NOT EXISTS idx_pg_principal ON park_grants(principal_id);
```

Seeding (in Go at startup, not in the .sql, so it can read `ACCESS_PASSWORDS`):
one `password` principal per configured password keyed by **sha256 prefix, not
the secret**; each gets `('all_public', NULL)`; `$AOI_OWNER_PWD` additionally gets
`('view','XSA_Study_Area')`.

### 2.2 `srv/park_acl.go` (new)

```go
type Visibility struct {
    AllPublic bool            // sees every non-restricted park
    Extra     map[string]bool // restricted parks explicitly granted
}
func (s *Server) VisibilityFor(r *http.Request) Visibility // in-memory cache
func (v Visibility) CanSee(parkID string) bool
func (v Visibility) SQLFilter(col string) string           // "AND col NOT IN ('XSA_...')"
```

`restrictedParks` is a startup-built set from the keystones `restricted` flag
(1 entry today), so checks are one map lookup. Principal lookup: sha256 of
`RequestPwd(r)` (`srv/auth_middleware.go:128`); when real accounts land, prefer
`s.Auth.GetUserFromRequest(r)` with `kind='user'`.

### 2.3 Enforcement points (a miss = data leak)

| Where | Change |
|---|---|
| `ParkIDMiddleware` (`srv/park_id_middleware.go`, wired at `srv/server.go:341`) | **primary choke point**: after id validation, restricted && !CanSee -> 404. Covers all ~40 `/api/parks/{id}/*` routes and `?park=`. Must become a method on `*Server`. |
| `HandleAPIAreas` (`srv/api.go` ~L900) | skip areas failing CanSee |
| `HandleAPIAreasSearch` (`srv/api.go` ~L1296) | same in the loaded-areas loop |
| `/api/fire-alerts`, `/api/notifications`, `/api/activity`, `/api/feed`, `/api/grid`, `/api/features-in-bbox`, `/api/parks/export`, `/api/stats` | append `Visibility.SQLFilter("park_id")`, mirroring `miningNotifSQLFilter()` in `srv/mining_flag.go` |
| `srv/response_cache.go` `cacheKey` (L68) | **must** add a visibility fingerprint: `env + "|" + visHash + "|" + path`. Otherwise a Chink0 response is served to everyone. |
| `globe.html` | server passes `RestrictedVisible []string` via `pageData`; `/api/areas` already omits hidden parks so the frontend needs nothing else. |

### 2.4 Frontend "only study area" toggle

Reuse the keystones toggle machinery (`keystonesEnabled`,
`#keystones-toggle-btn`, globe.html ~L293 / ~L4891). When the principal has
restricted grants, render a second chip `Study Area` with modes
`all / study only / off`. `study only` = filter the areas source to restricted
ids and grey the rest using the existing disabled paint branch (~L4825).
Share param `parks=study` in `shareCurrentView()` + `restoreStateFromURL()`.

### 2.5 Admin UI (last)

Admin panel -> "Access" tab: principals list, checkbox grid of restricted
parks + of `park_datasets`, `POST /api/admin/grants` (RequireAdmin). Enough to
onboard an NGO later with no migration.

## 3. Overlap handling (critical)

`scripts/park_assigner.py` assigns each fire to **exactly one** park. If
`XSA_Study_Area` enters that file it will **steal** detections from
Chinko/Southern/Bili-Uere/Garamba and silently gut their trajectories.

* `ParkAssigner.__init__` must skip `overlay: true` parks (add comment).
* Grep other `keystones_with_boundaries.json` consumers for disjointness
  assumptions before running anything.
* Consequence: `fire_detections.protected_area_id` is never `XSA_Study_Area`;
  the study area selects fires **by polygon** (§4).

## 4. Fires (no FIRMS backfill)

Add to `scripts/fire_source.py`:

```python
def load_area_fires_db(geometry, min_date, max_date=None, conn=None):
    # bbox prefilter via idx_fire_location, then shapely prepared.contains
```
dispatched from `load_park_fires` for overlay parks. 3.75M candidate rows in
bbox: do the point-in-polygon once and cache ids in a helper table
`overlay_area_fires(park_id, fire_id)`, refreshed incrementally (`--since`)
by the daily cron.

Window: build from **2024-01-01** (`STUDY_MIN_DATE`) though older data exists;
that is ~2.1M detections. Expect a very large group count; `dedupe_feature_ids`
and the 100-group payload cap in `srv/fire_realtime_handlers.go` already cope.

Chain (all support `--park`):
`rebuild_fire_trajectories_v5.py` -> `load_fire_groups_to_db.py --force` ->
`precompute_narratives_v5.py` -> `build_fire_grid_agg.py --since 2024-01-01`.
Obey AGENTS.md: single writer for `fire_narrative_cache`; zero-group park still
writes an empty v5 cache row. Then
`python3 scripts/check_fire_consistency.py --verbose`.

## 5. Per-park dataset declaration + batched ingest

### 5.1 `park_datasets` (same migration 040)

```sql
CREATE TABLE IF NOT EXISTS park_datasets (
  park_id TEXT NOT NULL,
  dataset TEXT NOT NULL,   -- fire|gfw|ghsl|hydro|osm_places|roads|climate|
                           -- species|basin|waterbodies|deforestation
  enabled INTEGER NOT NULL DEFAULT 1,
  since_date TEXT,         -- '2024-01-01' for the study area
  state TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed
  cursor TEXT,             -- JSON tile queue / next index (resumable)
  quota_cost INTEGER DEFAULT 1,
  last_run_at TIMESTAMP, next_run_at TIMESTAMP, detail TEXT,
  PRIMARY KEY (park_id, dataset)
);
CREATE INDEX IF NOT EXISTS idx_pd_state ON park_datasets(state, next_run_at);
```

This is the "choose per park what is collected" knob; the Access tab exposes it.

### 5.2 `scripts/dataset_worker.py` (new, the heart)

* One cron entry at `0 5 * * *` (after fire 03:00 and GFW 04:30).
* Per-dataset nightly budget (table at top of file, e.g. gfw 40 tiles,
  overpass 8 bboxes, ghsl 2 tiles); pops units off `cursor` and **commits
  progress after every unit** so a crash resumes.
* Writes a `dataset_progress` notification + a step into
  `data/pipeline_status.json` (existing admin badge picks it up).
* Deadline guard at 02:00 UTC, mirroring `past_deadline()` in `onboard_park.py`.

| dataset | work unit | note |
|---|---|---|
| gfw | one 0.5 deg tile | bbox 8.6x6.7 deg => **~240 tiles**; `gfw_alerts.py --park` would fire all at once. Add `--max-tiles N` + cursor, merge partials into `data/gfw_alerts/XSA_Study_Area.json`. |
| ghsl | one 100 m tile zip (R7_C20/R7_C21/R8_C20/R8_C21) | download to `data/ghsl/`, then `process_settlement_polygons.py --park`; generalise its hardcoded zip path |
| osm_places | one 2x2 deg sub-bbox (~20 units) | Overpass limits; `download_osm_places_to_file.py --park` currently uses one bbox |
| roads | one country pbf (CAF, SSD, COD, SDN) | partly present in `data/osm_geofabrik/` |
| hydro | HydroRIVERS_v10_af + HydroLAKES download (~2 GB, 1 unit) | or, stopgap, union the 4 overlapped parks' `park_rivers_hydro` rows |
| basin | `fetch_park_basins.py --park` (has `--sleep`) | |
| climate / species | local files, 1 unit each | |
| deforestation | Hansen tiles + `process_deforestation_polygons.py`, or derive from the GFW alerts already fetched | |

### 5.3 Phase A first (cheap, zero quota)

Chinko + Southern are entirely inside and Bili-Uere/Garamba partly, so rivers,
roads, places, settlements and deforestation can be **assembled by clipping the
4 overlapped parks' existing rows** to the polygon. Ship that as **phase A**
(usable tomorrow), and let `dataset_worker.py` fill the polygon's uncovered
remainder over subsequent nights as **phase B**.

## 6. Commit order

1. migration 040 + `srv/park_acl.go` + enforcement + cache-key fix + api tests.
2. `$AOI_OWNER_PWD` appended to `ACCESS_PASSWORDS` in `secrets.env` (gitignored).
3. keystone entry + `restricted`/`overlay` flags + `park_assigner` skip +
   `fire_source.load_area_fires_db`.
4. Phase A assembly + fire v5 chain from 2024-01-01.
5. `park_datasets` seeds + `dataset_worker.py` + cron + `--max-tiles` on
   `gfw_alerts.py`.
6. Frontend chip + share param; admin Access tab.

## 7. Verification

* `/api/areas?pwd=test2026` must not contain `XSA_Study_Area`; with
  `pwd=$AOI_OWNER_PWD` it must.
* `/api/parks/XSA_Study_Area/{stats,fire-narrative,fire-realtime,features,basin}`
  200 for Chink0, 404 otherwise.
* Response cache: Chink0 request then test2026 request back to back; the second
  must not be `X-Response-Cache: HIT` of the first.
* `scripts/check_fire_consistency.py --verbose` exit 0.
* **CAF_Chinko group count unchanged** before/after (proves the assigner skip).
  Record the baseline *before* step 3.
* `./tests/run_all.sh`.

## 8. Scheduling / resources

* **histmaps is running now** (tmux session `histmaps`,
  `sudan250k.py all --method tps --jobs 2 --resume`, ~166% CPU, 2.1 GB RSS on a
  7.4 GB box with **no swap**, into the night). Do not run CPU/RAM-heavy jobs
  alongside it; keep `systemctl restart 5mp` to one deliberate restart.
* Tonight: 01:00 onboard_park, 03:00 fire update, 04:30 GFW rotate,
  07:30 park refresh. **Bulk study-area ingest must not start before ~08:00 UTC
  tomorrow.** Either schedule the first run manually or set `next_run_at` on the
  seeded `park_datasets` rows and let the 05:00 cron pick it up the next night.
* Steps 1-3 (schema/ACL/code) are cheap and can be done immediately; only the
  ingest execution waits.
* Gate example:
  `tmux new-session -d "sleep <until 08:00 UTC> && cd /home/exedev/5mp && python3 scripts/dataset_worker.py --park XSA_Study_Area --phase a"`

## 9. Gotchas

* `data/fire_groups_v5/XSA_Study_Area.json` will be large (gitignored; 63 GB
  free on `/`).
* `srv/areas/areas.go loadKeystonesWithBoundaries` keeps only the first ring for
  point-in-polygon; our simple Polygon is fine.
* `notifications` must be ACL-filtered too or the park name leaks in a title.
* Star report / KML / Locus exports all go through `/api/parks/{id}/...`, so the
  middleware choke point covers them.
* Do not introduce a second fire source (AGENTS.md): `load_area_fires_db` reads
  `fire_detections` only.
