# Agent Instructions — 5MP Conservation Monitoring

Go web app: conservation monitoring of 162 African protected areas + user-drawn
AOIs. Interactive 3D globe (MapLibre), fire detection, deforestation,
settlements, patrol tracking. ~17k-line single-page frontend + SQLite (1.8 GB).

**Live:** https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026

> **This file is deliberately short.** The detailed, hard-won knowledge lives in
> `docs/agents/*.md`, one file per subsystem. **Read only the ones your task
> touches** — loading them all costs ~35k tokens and is almost never needed.

---

## Load-on-demand map

| Working on… | Read |
|---|---|
| Fire pipeline, trajectories, narrative cache, FIRMS ingest | `docs/agents/fire.md` (+ `docs/FIRE_PIPELINE.md`) |
| AOIs: runner, queue, visibility, versions, AOI frontend | `docs/agents/aoi.md` (+ `docs/PLAN_AOI_OVERLAY.md`) |
| Hover tips, click-to-select, map-tip precedence, the stats-panel Map strip | `docs/agents/map-ui.md` |
| Historical raster maps, geology vector overlays | `docs/agents/overlays.md` (+ `docs/GEOLOGY.md`) |
| Time animator, fire heat field, frame probe | `docs/agents/animator.md` |
| GeoPackage / KML / Locus exports, download links, gzip | `docs/agents/exports.md` (+ `docs/GEOPACKAGE_EXPORT.md`) |
| Feature loading, LOD, `/features-in-bbox`, detail tiers | `docs/agents/lod.md` |
| Backups, cron/notifications, OSM enrichment, onboarding, workers | `docs/agents/ops.md` |
| Passwords, tenants, caching headers | `docs/agents/auth.md` |
| Share links, `/s/{slug}`, guest capabilities | `docs/agents/sharing.md` |
| Writing/running tests, share-link params, `TEST` helper | `docs/agents/testing.md` (+ `docs/TEST_HELPERS.md`) |
| Data files, API examples, DB stats, Lucide icons | `docs/agents/reference.md` |
| Mining detection (retired — do not rebuild) | `docs/agents/mining.md` |
| Mining reference lists, ACLED + Crisis Tracker, coverage bias, reach strata | `docs/agents/acled.md` |
| Geological contact zones (open brief, not built) | `docs/agents/HANDOVER_CONTACTS.md` |

Other human docs: `docs/API.md`, `docs/DATABASE.md`, `docs/SCRIPTS.md`,
`docs/ARCHITECTURE.md`, `docs/QUICK_TASKS.md`, `docs/DATA_FLOW.md`.

---

## ⚠️ Where to write what you learn

**New knowledge goes to `docs/agents/<subsystem>.md`, not here.** This file
reached 140 KB by accretion — every conversation, on every topic, paid for all
of it. Only three kinds of edit belong in this file:

1. a **cross-cutting invariant** a task in *any* subsystem can violate (one or
   two lines in the list below, plus a pointer to the subsystem file for the
   story);
2. a new row in the **load-on-demand map** when a new subsystem file appears;
3. a change to a **hard rule** (database, secrets, build, tests).

Anything longer than three lines, or naming a specific file/table/handler, is
subsystem knowledge → `docs/agents/`. Keep this file under ~8 KB; when it grows
past that, move a section out rather than trimming the invariants. Delete a
handover section once its work is done. See `docs/agents/README.md`.

Source comments citing `AGENTS.md "<section>"` predate the split — those
sections are now the correspondingly-named files in `docs/agents/` ("Areas of
interest" → `aoi.md`, "Single Writer Rule" → `fire.md`, and so on).

---

## Hard rules

**Database.** 42.9M fire rows, 1.8 GB. No `DELETE`/`DROP`/unscoped `UPDATE`
without confirmation. Use `LIMIT` when exploring. `cp db.sqlite3 db.sqlite3.bak`
before schema changes.

**Secrets.** Never write a live password into a tracked file — not in docs, not
in a snippet, not in a comment. Add a named var to `secrets.env` (+ placeholder
in `secrets.env.example`) and refer to it by name; snippets start with
`source secrets.env`. Only `test2026` (shared demo password) is written
literally. Admin password and `$AOI_OWNER_PWD` live in `secrets.env`.

```bash
grep -rn "$AOI_OWNER_PWD" --include="*.md" --include="*.py" --include="*.go" .
```

**Rebuild after every change** — the UI footer shows the build's git hash:

```bash
make build && sudo systemctl restart 5mp
curl -s "http://localhost:8000/api/version?pwd=test2026" | jq -r .version   # == git rev-parse --short HEAD
```

Logs: `sudo journalctl -u 5mp -f`. Env vars (e.g. `ZENODO_TOKEN`) live in
`/etc/systemd/system/5mp.service` (`daemon-reload` after editing).

**Test parks:** `COD_Virunga` (full coverage), `CAF_Chinko` (fire detail),
`CMR_Nki` (pristine, 0 settlements), `TZA_Serengeti`. AOI: `XSA_Study_Area`.

```bash
./tests/run_all.sh          # db 37, api 45, ui 20
```

`go test ./srv/` fails on `TestServerSetupAndHandlers`
(`035-test-env.sql: no such column: avg_speed_kmh`) — **pre-existing**, don't
chase it.

---

## Key files

| File | Purpose |
|------|--------|
| `cmd/srv/main.go` | Entry point |
| `srv/server.go` | HTTP routing |
| `srv/templates/globe.html` | Main UI (single-page app) |
| `srv/api.go` | API endpoints |
| `srv/narrative_handlers.go` | Fire/deforestation/settlement narratives |
| `srv/fire_realtime_handlers.go` | NRT fire analysis |
| `srv/aoi*.go` | AOI read/write/versions/admin |
| `srv/features_bbox.go`, `srv/static/lodlayer.js` | Viewport feature loading |
| `srv/static/{anim,maptip,aoi_draw,geomap}.js` | Animator, tips, AOI editor, geology |
| `srv/upload.go`, `srv/upload_queue.go` | GPX upload + async processor |
| `db.sqlite3` | SQLite database (~1.8 GB) |

---

## Cross-cutting invariants

These apply no matter what you touch. Each cost real time at least once.

1. **A no-op must not read as an answer.** The recurring bug in this codebase is
   a filter that matched nothing, exited 0, and froze a wrong result as
   success. The tell is *a number suspiciously round for its input size* (0
   watersheds for 485,000 km²; 141 roads for three countries; a 264-line
   manifest that should be 770). A unit producing nothing for a large input must
   report **unfinished**, so the caller retries.

2. **Never type a count that describes a variable input.** Derive it. A
   hardcoded "76 sheets" survived a rebuild that doubled coverage.

3. **`ABS(<indexed col> - ?)` in a WHERE clause is non-sargable** → full scan of
   42.9M rows (19.8 s vs 0.02 s). Always `col BETWEEN ? AND ?`.
   `grep -rn 'ABS([a-z_]* - ?)' srv/ scripts/`

4. **Never join on `polygon_ids` with `LIKE`** — it pairs every event with every
   polygon. Resolve in Go (`srv/feature_meta.go`).
   `grep -rn "polygon_ids || ','" srv/`

5. **Park-shaped tables hold AOI rows too.** Any query without an explicit
   `park_id` must apply `aoiExcludeSQL(col)` (`srv/aoi.go`), and any settlement
   query must apply `settlementFilterSQL(narrative, polygon_ids)` (3,019 rows
   there are retired detector output, not settlements). **Provenance must be a
   property the pipeline cannot overwrite as a side effect**: that filter tested
   a narrative *prefix* until the nightly reclassify started regenerating
   narratives, laundering 495 detector rows into settlements — including all 79
   in `CMR_Nki`, the park the test list calls "pristine". A flag some other job
   rewrites is not a flag, it is a comment. See `docs/agents/mining.md`.

6. **An AOI is not a park.** `/api/parks/{aoi}/*` 404s by design; use
   `/api/aois/*` (frontend: `apiBase(id)`, `?area=` not `?park=`). Non-owners
   get **404, not 403** — an id must not be an oracle.

7. **A number must name its unit, and two surfaces saying one word must say
   one number.** A settlement is a *cluster*; `feature_geometries` holds its
   *footprints* (Chinko: 27 and 35). Three surfaces counted honestly, disagreed,
   and none said what it counted — which reads as broken data, not as two units.
   `/api/features-in-bbox` names `unit`/`groups`; focus (`?park_focus=`/`?aoi=`)
   is the gesture that makes the panel, the map and the popup agree.

8. **Serve a layer whole, or say you truncated.** A truncated answer is
   indistinguishable from a complete one. `ORDER BY … LIMIT n` is a *corner*,
   not a sample — use `spreadSelect`.

9. **Middleware must not commit to an encoding before the handler describes the
   response** (`srv/gzip.go`), and an authenticated response must never be
   `Cache-Control: public` (`PrivateCacheMiddleware`, `Vary: Cookie`).

10. **Fire narrative cache has one writer:**
   `scripts/precompute_narratives_v5.py`. The Go path is DEPRECATED — never
   re-wire it. See `docs/agents/fire.md`.

11. **Expensive narratives go through `narrative_cache` + `geoMemo`**
    (`srv/narrative_cache.go`), not a second cache. Park scale hides costs that
    time out at AOI scale (2 m 27 s → 10.5 s cold → 0.09 s warm).

12. **Never tune the fire algorithm by eye** — `scripts/eval_fire_trajectories.py`.
    Never judge a detection-filtering change on `coverage_pct`.
    The same rule covers geology: the commodity/junction model has a measured
    skill (`scripts/geomaps/eval_affinity.py`) and it is **not a tuning
    target**. A grade drawn without its score beside it reads as a ranking —
    on CAR the gold *junctions* concentrate known workings 2.3×, the gold
    *units* score 0.63× (worse than random ground), and both were the same
    amber dots. `nil` means **unmeasured** and must print that word.
    See `docs/agents/overlays.md`.

13. **Mining detection is retired** (`MiningEnabled = false`). Do not rebuild
    it; read `docs/agents/mining.md` before proposing anything mining-related.

14. **A share link is a name; a guest link is a credential.** Never put an
    access password in a URL — not in the link a button copies, not in a
    redirect target, and not left in the address bar for the *next* link to
    copy (that last one is how it leaked for weeks). A `pwd=` authenticates the
    request it arrives on, becomes a cookie, and is scrubbed by a redirect; and never let a guest capability write, mint
    another link, or fingerprint as `anon` in the response cache. Read-only is
    not the same as harmless: patrol reads go through `PatrolEnv(r)`, never
    `RequestEnv(r)`, or a link shared to show a fire scar ships the ranger
    tracks with it. A capability's **dates** are the same shape of question:
    a window is clamped once in the middleware (`clampGuestQuery`), so a
    handler whose range is not a `from`/`to` pair must call
    `ClampGuestDates` itself. See `docs/agents/sharing.md`.

15. Long writers **yield** (batched commits) so SQLite's single writer stays
    available. Before blaming the write lock, check `ps` — a lock wait is `S`,
    not `R`.
