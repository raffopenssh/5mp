# `docs/agents/` — subsystem knowledge for coding agents

`AGENTS.md` at the repo root is the **index and the invariants**, and is kept
short on purpose: every conversation pays for it in context. Everything else
lives here, one file per subsystem, loaded only when a task touches it.

| File | Covers |
|---|---|
| `fire.md` | Fire pipeline: SQLite-only source, v5/v7 builder, consistency, narrative-cache single-writer rule, FIRMS ingest |
| `aoi.md` | Areas of interest: runner/queue, visibility, API surface, narrative + geography caching, detail tiers, frontend, versioning |
| `map-ui.md` | Hover preview vs selection, tab stack, priority ladder (feature 0 / park -10 / AOI -20 / geology -30) |
| `overlays.md` | Sudan 1:250k historical raster overlay; Sudan/CAR geology vector overlays |
| `animator.md` | Time animator: heat field, sprites, indexed probe, chips, share links, `/api/fire-frames` |
| `exports.md` | GeoPackage (QGIS), KML/Locus, gzip-vs-download rule, copy-link, shared download links |
| `lod.md` | Level of detail: one loader, `/features-in-bbox`, chords, density paint, `?class=`, `?area=` |
| `ops.md` | Remote backups, OSM enrichment rotation, cron→bell notifications, park onboarding, background workers |
| `auth.md` | Passwords, per-password tenants, `Cache-Control` rule, admin-only endpoints |
| `sharing.md` | Share links, `/s/{slug}`, guest capabilities |
| `testing.md` | `?test=1` helpers, test suites, share-link params |
| `reference.md` | Data files, key API examples, DB stats, Lucide icon system |
| `mining.md` | Retired mining detection — read before proposing anything mining-related |
| `acled.md` | Mining occurrence reference; what ACLED and Crisis Tracker may say about our truth sets' reach |
| `HANDOVER_CONTACTS.md` | Open brief: geological contact zones. Delete when the work lands. |

## Where a new note goes

**Default: not in `AGENTS.md`.** Append to the subsystem file here, under a
`###` heading that states the rule in its own words. Only three kinds of thing
belong in the root file:

1. a **cross-cutting invariant** that a task in *any* subsystem can violate
   (one or two lines, plus a pointer here for the story);
2. a change to the **load-on-demand map** when a new subsystem file appears;
3. a change to a **hard rule** (database, secrets, build, tests).

If a note is longer than three lines, or names a specific file/table/handler, it
is subsystem knowledge — put it here.

Keep the root file under ~8 KB. When it grows past that, the fix is to move a
section out, never to trim the invariants.

## Style

These files record **measured facts and decisions, not tutorials**. Prefer:
state the trap, the symptom it produced, the fix, and the command that proves
it. Delete a handover section once its work is done — a finished plan read as
an open task more than once.
