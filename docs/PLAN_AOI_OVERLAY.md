# Plan: AOI overlays — the drawn bbox, promoted to a first-class object

Status: PLAN ONLY. Rewritten 2026-08-06 after the first draft
(`PLAN_STUDY_AREA_ACL.md`, in git history) was reframed by the user.
Execute in a fresh conversation; this file is the entire brief.

---

## 0. Mental model — read this first, it is the whole design

**An AOI is a power bounding box.**

The app already has a user-drawn area: `currentBbox` in `globe.html`. Today it
is a rectangle that is (a) ephemeral, (b) client-side, (c) resolved by
`findParksInBbox()` into whichever parks it touches, and (d) able to drive
`/api/grid`, the animator (`anim.js` `activeBbox()` → `A.bboxFixed`), the
starred-areas list (`starredItems.bboxes`) and the star report
(`collectReportParks()` folds bbox parks in alongside starred parks).

An **AOI** is that same object with five upgrades:

| drawn bbox (today) | AOI (this plan) |
|---|---|
| rectangle | arbitrary polygon |
| lives in browser memory | row in `aois`, owned by a principal |
| answers only from data already ingested | **has data fetched for it**, over days, by a cron |
| whatever the time slider says | a **fixed analysis window**, e.g. 2024-01-01..2026-01-01 |
| union of the parks it touches | its own precomputed layers **plus** those parks |

Everything else follows from that sentence, and three rules keep it from
sprawling:

1. **An AOI is not a park.** It does not enter
   `keystones_with_boundaries.json`, does not get a `park_assigner` entry, does
   not get the ~40 `/api/parks/{id}/*` endpoints. It gets a **small, deliberate
   surface**: summary, layers, animate, report, coverage. Species /
   publications / legal / climate stay park-level — the user said so, and they
   are meaningless averaged over 482,000 km².
2. **Ingest is keyed by geography, not by owner.** A private AOI may *cause*
   data to be fetched, but what lands is fetched into the shared, geographic
   stores (`fire_detections` rows, per-tile GFW/GHSL caches, country OSM
   extracts). Every park within reach benefits, permanently, and a second AOI
   over the same ground costs nothing. **Only the derived, AOI-shaped
   artefacts** (its trajectories, its narratives, its coverage numbers) are
   private. This is the answer to "the data is usable for all parks".
3. **The work is a queue, ground down by a cron, resumable at every step.**
   No long-lived process, no burst of quota, no babysitting. A user draws an
   area and the answer arrives over days — that is the product, not a
   limitation, so the UI must **show progress and partial coverage** rather
   than pretend.

The concrete first instance is `XSA_Study_Area` (7-vertex polygon, 482,000 km²,
window 2024-01-01..now, visible only to password `$AOI_OWNER_PWD`). Build it as
instance #1 of the general mechanism, not as a special case — the intended
future is "user draws an area of interest, it computes over a couple of weeks,
only they can see it".

---

## 1. Facts established (measured; do not re-derive)

**The area.** Polygon staged at `data/study_areas/XSA_Study_Area.{kml,geojson}`,
7 vertices, bbox `22.704,4.252 .. 31.297,10.966`, 482,000 km². Fully contains
CAF_Chinko and SSD_Southern; overlaps COD_Bili-Uere (35%) and COD_Garamba (13%).
Spans 5 GADM countries: SSD, CAF, SDN, COD, TCD.

**The data gap.** Only 10.5% of the polygon is inside any park and only 53.6%
inside the 100 km ingest buffer every current pipeline uses. Measured on a
1° grid (46 cells): median fire detections/cell is **122,179 inside** the
buffer and **1,050 outside** it. A 100× cliff at the buffer edge — that is
ingest scope, not fire behaviour. Same cliff for every other layer:

| layer | inside buffer | in the gap | fix |
|---|---|---|---|
| fires | full 2018-2026 | sparse | FIRMS backfill over the AOI bbox (§4) |
| GFW alerts | per-park scans | none | tiled scan, ~240 × 0.5° tiles |
| settlements | GHSL per-park | none | 4 GHSL 100 m tiles (URLs verified, §5) |
| roads / places | per park | none | Geofabrik PBF + osmium (§6) |
| rivers / lakes | `park_rivers_hydro` | none | HydroSHEDS, or PBF waterways |
| GSW water | `occ_20E_10N.tif` only | 3 tiles | direct download |
| basins | `park_basins` | none | `fetch_park_basins.py` |
| species / climate / publications | per park | n/a | **out of scope, by decision** |

**FIRMS (measured today, 2026-08-06).** This changes the schedule dramatically
versus the first draft:

* the area API rejects windows > 5 days (`Invalid day range. Expects [1..5]`)
  — this is why the nightly cron had been fetching **nothing**; fixed in
  `scripts/firms_api.py`, commit `6acafd5`. **Use that module, do not hand-roll
  URLs.**
* map-key quota is **5000 transactions / 10 minutes** (`firms_api.key_status()`),
  not the scarce resource the first draft assumed.
* 2024-01-01..today = 949 days = 190 windows × 3 sensors = **~570 requests**,
  ~13k rows per window per sensor over the AOI bbox → **~7M rows, ~30-60 min**
  of wall clock in total. So `fire_gap` is **one or two midday sessions**, not
  19 nights. Slice it anyway (resumability, and to stay clear of 03:00), but
  budget generously: 120 requests/slice, not 30.
* `INSERT OR IGNORE` on `UNIQUE(lat, lon, acq_date, acq_time, satellite)` makes
  overlap with existing rows free → request the **whole AOI bbox**, never try
  to tile a concave gap polygon.

**Baselines to record before touching anything.**
`CAF_Chinko` has **8,753** `feature_geometries` rows of type `fire_trajectory`
(2026-08-06). If that number moves after the AOI lands, the assigner isolation
(§3) is broken.

**Park infra enrichment is nearly complete but its only caller is dead.**
`enrich_park_infra()` in `analysis/river_turbidity.py` backfills `osm_places`
and `roads_heigit` opportunistically while a country PBF is on disk. Coverage
today: places 162/163, roads 161/163. Missing: `DZA_Djurdjura` (both),
`COG_Nouabalé-Ndoki` (roads). The turbidity cron that used to call it is
disabled (mining retired, AGENTS.md), so **the mechanism is orphaned**. It must
be lifted out — see §6; the user explicitly asked that this survive the
turbidity retirement.

**On disk already:** `data/osm_geofabrik/{central-african-republic,south-sudan}.osm.pbf`.
63 GB free. `osmium` at `/usr/bin/osmium`.

---

## 2. Schema (migration 040)

```sql
-- An area of interest: a polygon + an analysis window + an owner.
CREATE TABLE IF NOT EXISTS aois (
  id          TEXT PRIMARY KEY,       -- 'XSA_Study_Area', later 'aoi_<nanoid>'
  name        TEXT NOT NULL,
  geometry    TEXT NOT NULL,          -- GeoJSON Polygon/MultiPolygon
  bbox_minx REAL, bbox_miny REAL, bbox_maxx REAL, bbox_maxy REAL,
  area_km2    REAL,
  from_date   TEXT,                   -- analysis window; NULL = all available
  to_date     TEXT,
  owner_principal_id INTEGER REFERENCES principals(id) ON DELETE CASCADE,
  visibility  TEXT NOT NULL DEFAULT 'private',  -- 'private' | 'shared' | 'public'
  state       TEXT NOT NULL DEFAULT 'pending',  -- pending|ingesting|ready|failed
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_aois_owner ON aois(owner_principal_id);

-- Who may see what. Generic on purpose: a principal is a password today, a
-- user or an NGO tomorrow, with no migration.
CREATE TABLE IF NOT EXISTS principals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,      -- 'password' | 'user' | 'org'
  ref  TEXT NOT NULL,      -- sha256(pwd)[:16] | user_id | org slug
  label TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(kind, ref)
);
CREATE TABLE IF NOT EXISTS aoi_grants (
  aoi_id TEXT NOT NULL REFERENCES aois(id) ON DELETE CASCADE,
  principal_id INTEGER NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
  scope TEXT NOT NULL DEFAULT 'view',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (aoi_id, principal_id, scope)
);

-- The work queue. One row per (aoi, dataset); the cron grinds these down.
CREATE TABLE IF NOT EXISTS aoi_datasets (
  aoi_id  TEXT NOT NULL REFERENCES aois(id) ON DELETE CASCADE,
  dataset TEXT NOT NULL,   -- fire_gap|fire_v5|gfw|ghsl|osm|hydro|gsw|basin|deforestation
  enabled INTEGER NOT NULL DEFAULT 1,
  priority INTEGER NOT NULL DEFAULT 100,      -- lower runs first
  state TEXT NOT NULL DEFAULT 'pending',      -- pending|running|done|failed|blocked
  depends_on TEXT,                            -- e.g. fire_v5 depends on fire_gap
  cursor TEXT,             -- JSON work queue + index; resumable mid-unit
  units_total INTEGER, units_done INTEGER DEFAULT 0,
  coverage REAL,           -- 0..1, drives the UI line in §7
  lease_owner TEXT, lease_until TIMESTAMP,    -- so two hosts never collide
  last_run_at TIMESTAMP, next_run_at TIMESTAMP, detail TEXT,
  PRIMARY KEY (aoi_id, dataset)
);
CREATE INDEX IF NOT EXISTS idx_aoi_ds_state
  ON aoi_datasets(state, priority, next_run_at);

-- Point-in-polygon is expensive over millions of rows; cache the membership
-- once and refresh incrementally. This is what makes an AOI cheap to query.
CREATE TABLE IF NOT EXISTS aoi_fires (
  aoi_id TEXT NOT NULL, fire_id INTEGER NOT NULL,
  PRIMARY KEY (aoi_id, fire_id)
) WITHOUT ROWID;
```

Principals are seeded in Go at startup (needs `ACCESS_PASSWORDS`, so not in the
.sql): one `password` principal per configured password, keyed by **sha256
prefix, never the secret**. `$AOI_OWNER_PWD`'s principal owns `XSA_Study_Area`.

---

## 3. Isolation: an AOI must not perturb the parks

This is the one way this feature can do damage, so it is stated before any
code. `scripts/park_assigner.py` assigns each detection to **exactly one**
park. If the AOI polygon ever enters `keystones_with_boundaries.json`, it steals
detections from Chinko / Southern / Bili-Uere / Garamba and silently guts their
trajectories.

* The AOI lives in the `aois` table. **It is never appended to the keystones
  file.** (The first draft proposed a keystone entry with `overlay: true`;
  rejected — a flag that 20 consumers must remember to honour is a landmine.
  A separate table cannot be forgotten.)
* Therefore `fire_detections.protected_area_id` is never an AOI id. Gap
  detections stay `NULL` (or belong to a nearby park if within 100 km), which
  is correct: **the AOI selects by polygon**, via `aoi_fires`.
* Verification gate: `CAF_Chinko` fire_trajectory count is **8,753** before and
  after. Re-check it; do not trust the diff to be zero.

New in `scripts/fire_source.py`:

```python
def load_aoi_fires(aoi_id, min_date, max_date=None, conn=None):
    """Fires inside an AOI polygon, via the cached aoi_fires membership."""
```

and a `--aoi` mode on the v5 chain (`rebuild_fire_trajectories_v5.py` →
`load_fire_groups_to_db.py` → `precompute_narratives_v5.py`) that swaps the
park geometry for the AOI geometry and the fire loader for the above. Those
three scripts already take `--park`/`--parks`; add `--aoi` alongside, sharing
the code path, writing `feature_geometries` rows with `park_id = <aoi_id>`.
Obey AGENTS.md: single writer for `fire_narrative_cache`, and a zero-group AOI
still writes an **empty** v5 cache row.

---

## 4. Ingest: a dedicated midday cron

The user's call, and it is the right one: **a dedicated cron, not slices
smuggled into the existing ones.** Rationale — the 03:00 fire cron is
time-critical and was until today silently broken; the last thing it needs is a
second job sharing its window and its FIRMS quota. And the measured quota
(5000/10 min) means the overlay does not need to scavenge anyone's budget.

```cron
# AOI overlay ingest — grinds the aoi_datasets queue. Midday, far from the
# 03:00 fire cron, the 04:30 GFW rotation and the 07:30 park refresh.
0 12 * * * cd /home/exedev/5mp && /usr/bin/python3 scripts/aoi_runner.py --daily >> logs/aoi.log 2>&1
```

`scripts/aoi_runner.py`:

* `--daily` — take a lease on the highest-priority `pending` dataset whose
  `depends_on` is `done`, run until **budget or wall-clock deadline** (default:
  90 min, hard stop 13:45), then release. Move to the next dataset if one
  finishes early.
* Commits `cursor` + `units_done` **after every unit**, so a kill at any moment
  resumes cleanly. Leases (`lease_owner`/`lease_until`, stale after 6 h) mean a
  manual run and the cron cannot collide.
* `--aoi X --dataset Y --budget N` for manual/debug runs.
* Writes a step into `data/pipeline_status.json` (own key, `aoi_runner`, so it
  never marks the fire pipeline degraded) and one throttled
  `aoi_progress` notification per day, visible only to the AOI's principal.
* Never touches `fire_narrative_cache` directly — shells out to
  `precompute_narratives_v5.py` (AGENTS.md single-writer rule).

Work units:

| dataset | unit | count | notes |
|---|---|---|---|
| `fire_gap` | one 5-day FIRMS window × sensor | ~570 | `firms_api.fetch_range`, AOI bbox, `INSERT OR IGNORE`. Budget 120/slice → done in ~2 days. Then `build_fire_grid_agg.py --since 2024-01-01` **or the animator shows stale fires**. |
| `fire_v5` | the AOI v5 chain | 1 | depends on `fire_gap`; expect a very large group count over 482k km² |
| `gfw` | one 0.5° tile | ~240 | add `--max-tiles N` + cursor to `analysis/gfw_alerts.py`; **cache per tile** (§8), not per park |
| `ghsl` | one 100 m tile zip | 4 | R2023A E2030 100 m, verified live: `R7_C20` 1.5 MB, `R7_C21` 1.7 MB, `R8_C20` 11.5 MB, `R8_C21` 6.6 MB under `GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2030_GLOBE_R2023A_54009_100/V1-0/tiles/`. The 10 m tiles now 404. `process_settlement_polygons.py` hardcodes `data/ghsl/ghsl_pop_2030.zip` (a path that doesn't exist) → generalise to a tile dir. |
| `osm` | one country PBF: download → extract → enrich → delete | 5 | §6 |
| `hydro` | HydroRIVERS_v10_af + HydroLAKES | 1-2 | `data/hydro_source/` absent; stopgap = PBF waterways from the `osm` unit |
| `gsw` | one 10×10° occurrence tile | 3 | missing `occ_20E_0N`, `occ_30E_10N`, `occ_30E_0N` |
| `basin` | `fetch_park_basins.py` on the AOI | 1 | has `--sleep` |
| `deforestation` | derive from the GFW alerts already fetched | ~6 | prefer deriving over a Hansen download — no extra quota |

Rough completion: `fire_gap` + `fire_v5` in 2-3 days, `gfw` ~2 days at 120
tiles/slice, everything else within the first week. **Usable in days, complete
in ~1-2 weeks** — the requested cadence, with the fire cron untouched.

Optional, **off by default**: `aoi_runner.run_slice(host=...)` appended to the
existing crons as extra capacity. Ship the dedicated cron first; only add
piggyback slots if the queue is actually the bottleneck. If they are added, the
call must be the last statement, wrapped in try/except, so an AOI failure can
never mark the host cron failed.

Kill switch: `UPDATE aoi_datasets SET enabled=0 WHERE aoi_id='XSA_Study_Area'`.

---

## 5. Phase A: clip first, so it is usable on day one

Before any download: intersect what already exists (the 4 overlapped parks'
`feature_geometries`, settlements, deforestation, rivers, roads) with the AOI
polygon and window. Instant, zero quota, and it makes the AOI real immediately.
Per §1 it covers 10.5%-53.6% of the polygon, so it is a **preview**. Label it
(§7) and let the cron fill in the rest.

---

## 6. OSM: rescue the park-infra enrichment from the turbidity script

The user asked for this explicitly and it is independently worth doing: the
`osm_places` / `roads_heigit` backfill is good code whose only caller was
retired with mining.

Lift into a new **`scripts/osm_pbf.py`** — moved verbatim, not retyped:
`GEOFABRIK` (ISO3 → region; note DR Congo is `congo-democratic-republic`,
`democratic-republic-of-the-congo` 404s), `_osmium_extract()`,
`_export_filtered()`, `_road_class()`, `enrich_park_infra()`. Then:

* `analysis/river_turbidity.py` imports from it (behind the mining flag, no
  behaviour change).
* the AOI `osm` unit calls it per country: download PBF → extract AOI bbox →
  fill AOI places/roads → **while the PBF is on disk, enrich every park of that
  country that still has no rows** → delete the PBF.
* that last clause is the point: it restores the opportunistic park backfill
  and would close today's two known gaps (`DZA_Djurdjura` places+roads,
  `COG_Nouabalé-Ndoki` roads — neither country is in the AOI, so also add
  `scripts/osm_pbf.py --enrich-missing` for a one-off manual run).

Geofabrik, not Overpass: the gap is ~223,000 km² over 5 countries; Overpass
needs ~20 oversized bbox queries that time out, is rate-limited, and gives no
resumability. One deterministic download per country + offline osmium wins on
every axis. ~750 MB to fetch (`congo-democratic-republic` 415 MB, `sudan`
203 MB, `chad` 135 MB; CAF and South Sudan already on disk), deleted after use.

---

## 7. UI: reduced popup, layer inspection, animate, report

The AOI reuses the *chrome* of the park popup and almost none of its content.

**Tooltip** (hover on the AOI outline) — deliberately thin: name, area km²,
analysis window, `state` badge, and a one-line coverage summary
("fires 100%, forest 62%, settlements pending"). Two icon buttons: Animate,
Report.

**Popup** — sections, in order:

1. **Overview** — area, window, the 4 parks it intersects (each a link that
   opens the real park popup — this is where species/publications/legal/climate
   live, and saying so once is better than duplicating them), and the
   **coverage table**: one row per dataset with a progress bar and an ETA
   ("gfw 118/240 · ~2 days"). Straight from `aoi_datasets`.
2. **Fire** — v5 narrative + the `areaSparkline` trend, same components as the
   park popup, fed by `/api/aois/{id}/fire-narrative` and `/fire-trend`.
3. **Deforestation** and 4. **Settlements** — same pattern.
5. **Layers** — the toggle/pin grid, so every available layer can be inspected
   on the map. Pins reuse the existing pinned-layer machinery with
   `aoi:<id>:<type>` keys.

No species, no publications, no legal, no climate. Not "hidden" — absent, with
the Overview line pointing at the parks.

**Animate.** Nearly free: `anim.js` is already bbox-driven and already supports
a fixed bbox (`activeBbox()` → `A.bboxFixed`, canvas clipped). Opening the
animator from an AOI sets the fetch bbox to the AOI bbox, the time range to the
AOI window, and **clips the canvas to the AOI polygon** instead of the
rectangle — one `ctx.clip()` with a path instead of a rect. Layer chips work
unchanged because every frames endpoint is bbox-scoped.

**Report.** `collectReportParks()` already folds starred bboxes in by resolving
them to parks. An AOI is a starred bbox with a polygon and its own precomputed
layers, so it slots in as a new source: AOI-level sections first (its own fire /
deforestation / settlement narratives + coverage caveat), then the intersecting
parks as today. PDF/KML/CSV/XLSX exports inherit this for free.

**Visibility in the shell.** `/api/areas` gains AOIs the principal may see, as
features with `kind: "aoi"` (dashed outline, distinct colour, no fill). Reuse
the keystones-toggle machinery for an `Areas of interest` chip with
`all / AOI only / off`. Share param `aoi=<id>`; `parks=study` from the first
draft is dropped as too special-cased.

---

## 8. Making the data reusable for every park (rule 2, concretely)

* **Fires** — already global. The `fire_gap` backfill lands in
  `fire_detections` with normal park assignment, so Chinko/Southern/Bili-Uere/
  Garamba gain real detections in their outer buffers. Free win, no extra code.
* **GFW** — today `data/gfw_alerts/{park_id}.json`. The AOI scan must write
  `data/gfw_tiles/{tile_key}.json` (0.5° key) and have both the AOI *and* the
  park rotation read through that cache with a freshness TTL. Without this,
  240 tiles of API work benefit exactly one private AOI.
* **OSM** — §6 already does it: park backfill while the PBF is on disk.
* **GHSL / GSW / HydroSHEDS** — raw tiles under `data/{ghsl,gsw,hydro_source}/`,
  keyed by tile id. Any park in the footprint can use them afterwards.

The rule to apply when adding any future dataset: **if the artefact is named
after the AOI, ask whether it could be named after the tile instead.**

---

## 9. API surface (small on purpose)

```
GET  /api/aois                      -> list visible to the principal
POST /api/aois                      -> create {name, geometry, from, to} (future: draw)
GET  /api/aois/{id}                 -> metadata + coverage per dataset
GET  /api/aois/{id}/fire-narrative  -> v5 cache row (AOI-scoped)
GET  /api/aois/{id}/fire-trend
GET  /api/aois/{id}/deforestation-narrative
GET  /api/aois/{id}/settlement-narrative
GET  /api/aois/{id}/features?type=  -> GeoJSON, same shape as the park endpoint
GET  /api/aois/{id}/export.{kml,geojson}
POST /api/aois/{id}/refresh         -> requeue a dataset (owner only)
DELETE /api/aois/{id}               -> owner only
```

Enforcement, mirroring `ParkIDMiddleware` (`srv/park_id_middleware.go`, wired at
`srv/server.go:341`): an **`AOIMiddleware`** validates the id and resolves
visibility once — not visible → **404**, never 403, so an id is not an oracle.
Because AOIs are a separate id space and a separate route prefix, the
park-endpoint audit that the first draft needed (~40 routes, one miss = a leak)
**disappears**. That is the main reason not to model an AOI as a park.

Still required:

* `srv/response_cache.go` `cacheKey()` (L68) must gain a visibility fingerprint:
  `env + "|" + visHash + "|" + path`. Cache the AOI endpoints only after this.
  Otherwise a `Chink0` response is served to everyone.
* `notifications` for an AOI must be principal-filtered, or the name leaks in a
  title. Same shape as `miningNotifSQLFilter()` in `srv/mining_flag.go`.
* `/api/fire-frames`, `/api/features-in-bbox`, `/api/grid` stay **unfiltered** —
  they serve raw geography that was always public within the app, and the AOI
  polygon itself is the only secret. Do not pretend otherwise: an AOI is
  privacy for *the question being asked*, not for the underlying pixels.

---

## 10. Commit order

1. Migration 040 (`aois`, `principals`, `aoi_grants`, `aoi_datasets`,
   `aoi_fires`) + `srv/aoi.go` (store + `AOIMiddleware` + visibility) +
   response-cache key fix + api tests. Seed `$AOI_OWNER_PWD` into `secrets.env`
   (gitignored) and the AOI row from the staged geojson.
2. `scripts/osm_pbf.py` lifted from `river_turbidity.py`, with
   `--enrich-missing`; close the two known park gaps. **Independently useful,
   ships first, no AOI needed.**
3. `fire_source.load_aoi_fires` + `aoi_fires` builder + `--aoi` on the v5 chain.
   **Record CAF_Chinko = 8,753 fire_trajectory rows first**, re-check after.
4. Phase A clip-from-neighbours + `/api/aois/*` read endpoints + the reduced
   popup with the coverage table.
5. `scripts/aoi_runner.py` + the midday cron + `--max-tiles`/tile-cache on
   `gfw_alerts.py`. Seed `aoi_datasets` rows; first slice runs the next midday.
6. Animator polygon clip + AOI chip in `/api/areas` + report integration.
7. Admin "Access" tab: principals, AOI ownership, per-dataset toggles.

---

## 11. Verification

* `/api/aois?pwd=test2026` empty; with `pwd=$AOI_OWNER_PWD` contains the study area.
* `/api/aois/XSA_Study_Area/*` → 200 for Chink0, **404** otherwise.
* Response cache: Chink0 request then test2026 back-to-back; the second must not
  be `X-Response-Cache: HIT` of the first.
* **CAF_Chinko fire_trajectory count still 8,753.**
* `python3 scripts/check_fire_consistency.py --verbose` exit 0.
* `aoi_runner` killed mid-slice resumes with no duplicate and no lost unit
  (kill -9 during `fire_gap`, rerun, compare `units_done` and row counts).
* `./tests/run_all.sh`.

---

## 12. Gotchas

* `data/fire_groups_v5/XSA_Study_Area.json` will be very large (gitignored).
* `srv/areas/areas.go loadKeystonesWithBoundaries` keeps only the first ring for
  point-in-polygon; the AOI store must not inherit that shortcut if a future
  drawn AOI has holes.
* Do not introduce a second fire source (AGENTS.md): `load_aoi_fires` reads
  `fire_detections` only.
* After any bulk fire change: `scripts/build_fire_grid_agg.py --since`.
* The 03:00 fire cron's FIRMS window cap is now enforced in `firms_api.py`.
  Any new FIRMS caller **must** go through it; a hand-rolled 10-day URL is a
  silent 400.
* `park_datasets` from the first draft is renamed `aoi_datasets` and is *not* a
  per-park switchboard. If a per-park version is wanted later, it is a separate
  table — conflating them was what pulled AOIs into the park id space.
