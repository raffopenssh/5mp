# Handover: the geology mixer, its downloads, and the anchors

_Open brief, 2026-08-13. Delete this file when the work below is done._

Task as given: **(1)** make sure the geology mixer works correctly — "I want to
see where gold is likely, it shows me that"; **(2)** a GeoPackage download of
the **current chooser** (filtered/hidden/as-drawn), probably a download arrow in
the mixer; **(3)** include our **reference mining sites** in that download —
*coordinate, year, resource, original id, source url only*. Follow-up: **the way
contacts are selected matters most, junctions look the most promising** (CAR
gold junctions 2.18–2.53×, gold *units* 0.63×).

Read `docs/agents/overlays.md` § geology first. Nothing below overrides an
invariant there.

---

## Done and committed

1. `scripts/mining_anchors.py` → `data/geology_truth/mining_anchors.geojson`
   (3,687 anchors, nine lists, five fields, `terms` on every row, ACLED named
   in `withheld`). The file's own header holds the judgement calls.
2. `srv/geomap_anchors.go` — loader + `geoAnchorSummary()`.
3. **The filtered export.** `buildGeoMapGeoPackageSel` applies `sel.unitSet()`
   in the sheet loop — *before* the commodity columns are derived, so a
   filtered file's `w_*` columns describe the file (a `w_uranium` column on a
   gold-only export claims uranium was considered and found absent). It fails
   when the selection matches 0 units, and names only the sheets that actually
   contributed.
4. **`srv/geomap_gpkg_layers.go`** — two new layers in every geology
   GeoPackage: `geology_contacts` (MULTILINESTRING from
   `<sheet>_contacts.geojson`, graded here from the lithology pair, styled by
   `grade` in the map's own amber ramp) and `mining_anchors` (POINT, whole,
   never filtered by the reader's commodity, notice + withheld list in the
   layer description). Both appear in the embedded QGIS project.
5. **Routing.** `POST /api/geomap/geopackage` takes the selection JSON, builds
   to a temp file, serves it once as `private, no-store`, and never touches
   `geology.gpkg` or its stamp. Filename derives from the reader's own label.
   409 (not 500) on a stale selection — that is the client's to fix by
   reloading, and the message says so.
6. **The cache stamp now covers every input** — units, contacts and anchors.
   One that watched only the units would serve a package whose hairlines were
   a re-derivation old and whose polygons were right, i.e. invisible staleness.
7. **Anchors in the catalogue.** `/api/geomap` carries `anchors` (counts,
   sources, withheld, notice) and `geopackage_view`.
8. **The download arrow**, in the panel bar beside settings/collapse/×. Offers
   *This view* (with its unit and junction counts) and *Everything* (sheets,
   MB), plus the anchor line and the disclaimer. The whole-catalogue row is a
   plain `<a download>` sharing `downloadGeoMapGPKG`'s "Preparing…" floor; the
   view row is fetch+blob, because it is a POST. `GeoMap.selection()` resolves
   the view from the same `visibleCodes()`/`visiblePairs()` the paint uses.
9. **The stale contact count is fixed.** `measureCoverage()` records which
   layers it measured (`measuredSig`) and whether a 0 is *unproven*
   (`contactsPending`: the contact layers exist and have painted nothing yet).
   `reMeasure()` re-takes it on the next `idle`, a few times, and the bar says
   `counting lines…` rather than `no lines here` meanwhile — 0 and "not
   measured yet" are different states and only one is a claim about the canvas.

Verified end to end: whole catalogue = 659 units / 882 contacts / 3,687
anchors; a CAR gold + classic-junction view = 7 units / 40 contacts / 3,687
anchors, named `geology-gold-hosts-on-car-classic-junctions.gpkg`, with
`geology.gpkg` and its stamp untouched.

---

## Not done — pick up here

1. **None of the new UI has been clicked.** Reproduce the original bug
   (`?geomap=sudan,car,tanzania`, open the panel, click **Junctions**) and
   confirm the bar reaches the real number; then take both downloads and open
   one in QGIS. `scripts/geomaps/render_gpkg.py` draws a file through its own
   embedded project — three of the nine unit ornaments were wrong in ways no
   byte-level test could see, and the contact style has had no such pass.
2. **No tests.** `srv/geomap_gpkg_test.go` has the shape to copy
   (`writeTestSheet`, `useTestSheets`). Three worth pinning: a selection that
   matches nothing is an ERROR, not an empty layer; the anchors are NOT
   filtered by the selection (a file where every anchor agrees with the layer
   is a picture of our own filter); the stamp notices a rewritten contacts file.
3. **`docs/agents/overlays.md` says nothing** about the two new layers, the
   POST route, or the `contactsPending` rule. Write it there, not in AGENTS.md.
4. **The view export is not reachable from admin ▸ Map Settings**, which still
   offers only the whole catalogue. Probably right — a view only exists while
   the panel is open — but say so in the doc rather than leaving it as an
   omission somebody re-adds as a third path.

### Judgement calls to preserve

* **Do not filter the anchors to the reader's commodity.** Ship all of them;
  `resource` is a column, so the reader can narrow it and know that *they* did
  the narrowing.
* **A filtered export must announce itself** — it does, in the layer
  description ("A VIEW, not the whole catalogue — …") and in the QGIS project
  title. Two files with one name and different contents is the truncation trap.
* **The mixer itself is correct** as far as tested: gold row → 36 units, floor
  `any/likely/classic` → 36/15/3, junction cells and headers ADD,
  `jcell:intrusive|volcanic` → 10 lines, `+ jcell:metamorphic|ultramafic` → 33,
  `+ jrow:intrusive` → 97. The gold row correctly shows `?0.06×` violet
  (contested) and the junction head `0.00×` — the *lowest* lift, never the
  flattering one. Don't "fix" that into a single number.
