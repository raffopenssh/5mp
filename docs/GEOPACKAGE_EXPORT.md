# GeoPackage export (the "GeoPackage" download)

One `.gpkg` holding **every data layer we have for an area** — park or AOI —
with real column types and QGIS styling baked in.

    Park popup    →  ⬇ → GeoPackage
    AOI popup     →  ⬇ → GeoPackage
    API           →  POST /api/{parks|aois}/{id}/export.gpkg[?raw=0]

**Two variants, not a checkbox**: "all layers" and "no raw fire points". They
differ by a gigabyte and several minutes (XSA: 1.4 GB / 9 min vs the derived
`fire_trajectories` telling the same story in 38k features), which is a choice
between two downloads rather than a setting on one. The filename says which
(`..._no_raw_fire.gpkg`), the notification title says which, and `raw_fire` is
part of the **cache key** — otherwise the second request would be served the
first one's file, and it would look perfectly valid.

It is deliberately the same *content* as the KML export plus the things KML
cannot carry honestly: raw fire detections (millions of points), typed numeric
attributes, and per-layer symbology. KML's `<ExtendedData>` is all strings, so a
Google Earth user can look at a fire but not sort by FRP; a GeoPackage user gets
`REAL frp_mw`, `DATE start_date` and a working temporal controller. **That is the
whole reason the format exists here** — if a change would make the file merely
valid rather than *usable*, it is the wrong change.

---

## What it is

| Piece | Where |
|---|---|
| GeoPackage writer (WKB, metadata tables, R-tree) | `srv/gpkg.go` |
| Layer queries + attribute mapping | `srv/gpkg_export.go` |
| QML styles (the app's palette) | `srv/gpkg_style.go` |
| Embedded QGIS project (order, groups, visibility) | `srv/gpkg_project.go` |
| Point-in-polygon for `in_area` | `srv/gpkg_inarea.go` |
| Job queue, cache, notification, download, expiry | `srv/gpkg_jobs.go` |
| Notification card + share links | `srv/static/gpkg_export.js` |
| Table | `db/migrations/047-geopackage-jobs.sql` |
| Tests | `srv/gpkg_test.go` |

No GDAL, no cgo: a GeoPackage is a SQLite file with three metadata tables and a
40-byte header in front of ordinary WKB, and we already ship a SQLite driver.
Shelling out to `ogr2ogr` would mean serialising every layer to a temp GeoJSON
first (XSA's river layer alone is 34 MB) and hoping GDAL is installed.

## Layers

`boundary`/`aoi_boundary`, `fire_trajectories`, `fire_detections`,
`deforestation`, `settlements`, `rivers` (+ `rivers_merged`), `roads`, `places`,
`waterbodies`, `lakes`, `watershed_upstream`/`_downstream`/`_rivers`,
`patrol_tracks`, `airstrips`, `patrol_effort`.

Measured: **CAF_Chinko** (1 year) 14 layers, 176k features, 37 MB, ~16 s.
**XSA_Study_Area** (485,150 km², 2024→now) 14 layers, 7.1M features, 1.4 GB,
~9 min.

* **Every layer is exported whole — no LIMIT.** Same rule as the geography
  layers on `/features`: a truncated file is indistinguishable from a complete
  one once it is off the server and in someone's QGIS project. Size is not a
  constraint; silence about missing rows is.
* Empty layers are **dropped**, not shipped empty — in QGIS's browser an empty
  table is indistinguishable from a broken one.
* Patrol layers are client-derived and are suppressed in the test tenant,
  exactly as the KML and Locus exports suppress them.

## Column types are the point

Every column is declared `INTEGER` / `REAL` / `TEXT` / `BOOLEAN` / `DATE` /
`DATETIME`. GDAL reports the declared type verbatim, so a `start_date` written
as TEXT lands in QGIS as a string and the temporal controller cannot use it.

**A DATE/DATETIME column is only honoured if the *value* parses as ISO-8601.**
A bare `"2024"` or `"2024-03"` reads back as NULL with no error anywhere — so
`gpkgDate`/`gpkgDateTime`/`gpkgDateTimeParts` return nil for anything they
cannot make into a full date, and partial originals keep their own INTEGER
column (`loss_year` beside `start_date`). Never pass a raw column through.

FIRMS splits its timestamp into `acq_date` + `acq_time` and drops leading zeros
(`"134"` is 01:34, UTC); `acq_datetime_utc` recombines them, which is what makes
"animate the fires" a one-click operation.

## Styles and the embedded project

Two mechanisms, and both are needed:

1. **`layer_styles`** — QGIS applies the `useAsDefault=1` row when a layer is
   opened, so adding a single layer by hand still gets the app's colours.
2. **`qgis_projects`** — a full project (Project ▸ Open From ▸ GeoPackage, or
   double-click it in the Browser). Styles alone are not enough: a GeoPackage
   has no layer order, no visibility and no grouping, so "Add layers" on a
   finished export puts 163k fire detections on top of everything and the file
   looks broken. **The raw firehoses ship switched OFF**, one checkbox away, in
   a group that says what they are.

The QML bodies are shared between the two, so there is exactly one description
of what a fire looks like.

Gotchas, each already got wrong once:

* **QGIS temporal `mode` is not zero-based-by-convenience.** `0` is
  *FixedTemporalRange*, i.e. ignore the fields. `1` = instant from one field,
  `2` = start+end. Writing 0/1 gave layers that reported themselves as temporal,
  showed the fields in the dialog, and rendered everything at every timestep.
* **The project references its own container as `./<basename>.gpkg`.** So the
  on-disk name must equal the download name — hence one directory per job. If
  they diverge the project opens with no layers, which reads as a broken export.
* `qgis_projects.content` is a **hex-encoded** `.qgz` (a zip around a `.qgs`),
  which is what QGIS itself writes there.
* The canvas opens on the **area**, not the union of every layer: detections and
  watersheds both reach far outside it.

## `in_area`: a bbox is not an area

Fire detections are keyed by coordinate, so the only indexed query is the area's
**bounding box**. For a compact park that is close enough; for `XSA_Study_Area`
the polygon holds 3.18M detections and its bbox holds 6.9M — **more than half of
a file named after the area lies outside it**.

Dropping them would also be wrong (the fire narratives deliberately keep context
out to 20 km, and a fire heading for a boundary is what people look for), so
they are kept and **labelled**: `in_area` is 1 inside the polygon, 0 in the
surrounding box, and the renderer says so — hot orange inside, muted ember
outside. A single symbol would quietly present the bbox corners as the area.

`srv/gpkg_inarea.go` buckets ring edges into 512 latitude bands so 7M points
cost a few edge tests each instead of 2,000. Exact for concave rings and holes.
**An unusable boundary defaults to "inside"** — silently flagging every row 0 is
worse than not knowing.

## The R-tree is not optional

Without the spec's R-tree, every pan in QGIS is a full table scan: a spatial
query over 6.9M detections took **1.1 s**; with it, **0.068 s**. Rows are
inserted alongside each feature (bulk-building would need either a SQL envelope
function this driver lacks, or 7M envelopes in memory). The spec's five
maintenance triggers are omitted on purpose: the file is written once and never
edited, and re-exporting is how it changes.

## The job: cache, not spool

A GeoPackage cannot be served inline (9 minutes vs a 120 s `WriteTimeout`), so
it is a background job with a notification card — the same shape as MBTiles and
park onboarding, with two deliberate differences:

1. **The table is the state**, not an in-memory map. A download link that dies
   on the next deploy makes the notification a lie, and the whole point of the
   card is that the user can walk away.
2. **It is a cache**, keyed by `(area, window, effort, env)`. Asking twice
   returns the same file instead of rebuilding it, which is also what makes a
   shared link meaningful. Files live **21 days**; `?refresh=1` forces a rebuild.

```
POST /api/{parks|aois}/{id}/export.gpkg?from=&to=&effort=0&refresh=1
GET  /api/geopackage                    this principal's exports
GET  /api/geopackage/{id}               live status (no-store)
GET  /api/geopackage/{id}/download
```

* **One build at a time** (`gpkgBuildSem`): these are heavy over the same SQLite
  file every other request uses. A queued job says *"waiting for another
  export"*, not 0% — a bar that has not moved in four minutes reads as broken.
* The card is written at **queue** time, not at build start, for the same reason.
* **One notification per export question**, keyed by `cache_key` and rewritten in
  place. A rebuild replaces the card but **keeps the old file and link alive** —
  a link someone was given must not break because the sender rebuilt it.
* **Startup reconciles orphans**: a job left `running` by a restart is failed
  with a retryable message. A card frozen at 40% forever is the "no-op that
  reads as an answer" failure this codebase keeps re-learning.
* Sweeper runs hourly: expired rows, their files, their notifications, plus
  orphan directories.
* **AOI exports are private.** Non-owners get **404, not 403**, on status and
  download; the list endpoint is scoped by principal. An id must not be an
  oracle.

## One menu for parks and AOIs

A park used to carry three bare icon buttons in its popup header (a globe for
KML, a phone for Locus, layers for tiles) while an AOI had a ⬇ menu. That was
two answers to one question: the icons had to be *guessed* (a globe reads as
"web", not "Google Earth"), they competed with the star for the same crowded
row, and every new format cost another slot — the GeoPackage entry would have
made it four. Both now render `exportMenuItems()`, and `isAOI()` picks the route
prefix. GeoJSON is AOI-only: a park's boundary is public geography served
elsewhere, so the entry would be noise.

## Share links

| Param | Meaning |
|---|---|
| `aoi_menu=<id>` | open that area's download menu |
| `aoi_menu_item=gpkg` \| `gpkg_light` \| `kml` … | highlight one entry in it |
| `gpkg=<job id>` | open the bell on that export's card |

An open menu is on screen, so a share link should reproduce it — and it is the
one piece of UI whose entire purpose is a *next click*, so restoring it without
saying **which** entry leaves the recipient exactly where the sender was trying
to get them past.

`aoi_menu_item` is a **hint, never an action**: a link that starts a 400 MB
download on open is a trap. Same reason `?gpkg=` opens the card rather than the
file — the recipient should see the size, the layer list and the expiry first,
and the card is also where "it expired" can be said honestly.

`aoi_menu` takes a park id too, despite the name. Draining it **retries on a
decaying schedule** (12 attempts) rather than firing once at 900 ms: the button
is drawn by a popup that is itself waiting on tiles or a fetch, and a link whose
entire payload is "click this entry" must not quietly do nothing because the map
was half a second slow.

## Verifying a change

There is no QGIS in CI, so `go test ./srv/ -run 'GPKG|QGIS|GeoPackage|AreaHit'`
checks the parts that are invisible from inside Go: the SQLite `application_id`
at byte 68 (GDAL identifies the format by it), envelope byte order, declared
column types, dropped empty layers, R-tree registration, temporal modes.

For anything touching styling or the project, install QGIS and **look at it** —
the first version of this feature passed every automated check and opened as an
orange smear:

```bash
sudo apt-get install -y python3-qgis     # 3.34
# then render the embedded project offscreen and read the image
```
