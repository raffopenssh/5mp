# Operations (backups, cron, OSM enrichment, onboarding, workers)

_Split out of AGENTS.md. Read when working on this area._

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

Every unloaded search result carries an **Add** control; every row already in
the queue carries its live status (`queued` / `building` / `removing tonight`
chip, plus Cancel / Keep / Remove). No dwell timer.

*Why the timer went (2026-08-17).* The offer used to be a footer banner in the
dropdown that appeared after **15 s** of dwelling on a matching search. Nobody
looks at a dropdown that long, and on a phone the keyboard covers it — the
capability existed but was effectively unreachable. **An action belongs on the
object it acts on, visible the moment that object is.** `searchAreas()` now
fetches `/api/onboarding` alongside the three search endpoints (60 s cache,
`indexOnboarding()` → `_onboardByWdpa` / `_onboardByPark`), and
`onboardRowAction(area)` renders the row's control. After a request/cancel,
`refreshSearchResults()` re-runs the search so the row shows its new state.
Guests get **no** control at all: `guestMayRead` rejects the POST, and an offer
the page then withdraws is worse than none.

Two related search fixes shipped with it:

* **Duplicate designations.** WDPA lists one protected area under several
  designations, so Djurdjura appeared once loaded and once as an unloaded
  Biosphere Reserve offering "Add" for a park already on the globe. Unloaded
  rows whose name is already loaded are dropped from *both* sources.
* **Mobile: the dropdown drops *up*.** The search bar is fixed just above the
  bottom toolbar, so a downward list was drawn over the toolbar buttons — a tap
  on a result hid the list and the same click then landed on the filter button
  underneath, opening "Active Filters" instead of the park. It now opens
  upward, over the map. Same shape of bug in `selectSearchResult()`: the filter
  panel is a full-screen sheet on a phone, so it opens only on a wide screen
  (`FloatUI.isSheetMode()`), and never for an unloaded PA, which has no
  selection to confirm.

Backend: `srv/park_onboarding.go` (`POST /api/onboarding/request|cancel`,
`GET /api/onboarding`; routes NOT under /api/parks/* because of park-id
middleware). Tables: `park_onboarding_requests` (migration 037) +
`park_onboarding_subscribers` (migration 063). Worker:
`scripts/onboard_park.py` (cron 02:30) — Protected Planet boundary → keystone
append, FIRMS all-time fire backfill, fire reassignment pass, v5 pipeline, GFW
scan + Hansen loss, GHSL/HydroSHEDS if local sources exist, restart. Removal:
the Remove control on a loaded, onboarded row (or the undo toast); only parks
with `onboarded_at` in keystones_with_boundaries.json can be removed.

**Scoping (2026-08-17): the request is the caller's; the work is shared.**
A park is global data, so one `park_onboarding_requests` row exists per
`wdpa_id` no matter how many logins ask — but each caller is a row in
`park_onboarding_subscribers` (`pwd_ref = principalRef(pwd)`, same non-secret
handle as AOIs/short links, plus their `env` for notification routing).
Consequences, each enforced by `srv/park_onboarding_test.go`:

* `GET /api/onboarding` lists only the caller's subscriptions; a non-subscriber
  cancelling gets **404, not 403** (an id must not be an oracle). Requests
  never leak across tenants.
* A second tenant requesting the same park **joins** the existing row
  (`already_requested`) instead of duplicating the multi-hour ingest; on
  completion `notify_subscribers()` writes one notification per subscriber env
  (the row's own `env` is only the first requester's tenant).
* Cancel removes only the caller's subscription. The pending row is deleted —
  and a ready park is scheduled for removal — **only when the last subscriber
  leaves**; otherwise the caller gets `unsubscribed` and the park stays.
  `process_removal()` re-checks subscribers at run time (someone may join
  between the cancel and the nightly run) and aborts back to `ready`.
* Requesting a park whose row is `remove_requested`/`removed` revives it
  (ready/pending) and re-subscribes the caller.
* The worker skips a request only when **all** subscribers are `env='test'`
  (sandbox); pre-063 rows have no subscribers and are reachable by nobody,
  like legacy empty-`pwd_ref` short links.

**Data reuse (same date): never re-fetch what the DB already holds.**
`backfill_fires` consults `covered_fire_ranges()`: any completed covering-AOI
`fire_gap` ingest (state=done, coverage≥1.0, AOI bbox ⊇ buffered park bbox)
marks its `from_date..last_run_at-1d` range as already ingested, and whole
5-day FIRMS windows inside it are skipped (a window straddling a range edge is
fetched whole; `INSERT OR IGNORE` absorbs duplicates). For Numatina inside
XSA_Study_Area that skips 2024-01→now, ~190 of ~610 windows. The reused rows
carry a park assignment computed before the park existed, so
`reassign_fires_bbox()` then re-runs `ParkAssigner` over ALL existing
detections in the buffered bbox (keyset-paginated, batched commits — invariant
16) and updates every row whose canonical assignment changed; it raises if the
new park is missing from the assigner's keystones, because a reassignment that
cannot possibly match is a no-op reading as an answer. The read path is
`protected_area_id` (`scripts/fire_source.py`), so without this pass a park
inside an ingested AOI shows zero fires while the rows sit under it. A park
already in the keystones file (onboarded by another tenant) short-circuits to
`ready` with no ingest at all — except when retrying a `failed` row, which
re-runs the idempotent pipeline steps. GFW/GHSL reuse needs no special code:
their per-tile caches (`data/gfw_tiles/`, `ghsl_tiles.py`) make park-scoped
re-runs cheap. Test-env requests are skipped by the worker.

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
