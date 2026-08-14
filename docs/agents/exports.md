# Exports (GeoPackage, KML/Locus, downloads)

_Split out of AGENTS.md. Read when working on this area._

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
* **A running export is cancellable, and a cancel cleans up everything.**
  DELETE on a pending/running job closes its cancel channel (`gpkgCancels`,
  in-memory — a cancel can only reach a goroutine in this process) and answers
  **202**; the build goroutine — the one owner of the job directory — notices
  between layers (and between rows in the fire-detections loops, the only
  minutes-long layers), returns `errGPKGCancelled`, and deletes file + job row
  + notification together. No "cancelled" card is left: a cancelled export was
  the user saying "I don't want this". A queued job cancels for free (select on
  semaphore vs cancel). The in-progress card carries the Cancel button; the
  JS `remove()` handles 202 with a delayed `loadNotifications` so the panel
  doesn't repaint the card before the server's cleanup lands. Two races are
  closed server-side: a DELETE before the goroutine registers its channel is
  caught by a row-exists check before the job is published ready, and the
  sweeper deletes any `geopackage_*` notification whose job row is gone —
  otherwise the card polls a 404 forever and shows "checking…" as a limbo
  state (the client also removes a card on a 404 poll for the same reason).
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
