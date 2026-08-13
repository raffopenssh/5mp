# Handover: the geology mixer, its downloads, and the anchors

_Open brief, 2026-08-13. Delete this file when the work below is done._

Task as given: **(1)** make sure the geology mixer works correctly — "I want to
see where gold is likely, it shows me that"; **(2)** a GeoPackage download of
the **current chooser** (filtered/hidden/as-drawn), probably a download arrow in
the mixer; **(3)** include our **reference mining sites** in that download —
*coordinate, year, resource, original id, source url only* — as anchors that
show a reader we did the research. Follow-up in the same conversation: **the way
contacts are selected matters most, junctions look the most promising** (which
the measurements agree with: CAR gold junctions 2.18–2.53×, gold *units* 0.63×).

Read `docs/agents/overlays.md` § geology first — especially "the score is not
the disclaimer" and "Four lists, three sheets, and a disagreement". Nothing
below overrides an invariant there.

---

## Done and committed

### 1. `scripts/mining_anchors.py` → `data/geology_truth/mining_anchors.geojson`

3,687 anchors from **nine** lists (ipis_caf 914, osm 1488, gmis_tza 480,
ipis_tza 447, tang_werner 197, usgs_africa 61, crisistracker 49, tearline_caf
40, icmm 11). Committed, 1.8 MB, single writer.

Five fields and no more: `lon/lat`, `year`, `resource`, `source_id`,
`source_url` (+ `iso3`, `observed`, `licence`, `terms`, `attribution`).

* **Five fields is a citation, not a dataset.** What makes a mine inventory
  valuable — worker counts, pit counts, armed-actor fields, environmental
  scores, incident prose — is exactly what is dropped, so these rows cannot
  substitute for anybody's product. That is why *every* list ships, including
  the six whose publishers granted no licence.
* **`terms` rides on every row**, not just the header: `open` (ODC-BY, ODbL,
  CC-BY, USGS public domain) or `unstated`. A reader who filters this layer to
  one source and passes it on must carry the fact that nobody granted a licence.
* **ACLED is the one exclusion** and it is *named* in `withheld`, not omitted:
  its terms forbid rows in a public export, and it was never a mining list —
  it describes our truth sets' **reach**. "We didn't check there" and "we
  checked and may not show you" are different statements.
* **`year` is an observation date or it is NULL** — never the paper's
  publication year, never the fetch date. 2,898 of 3,687 have one; gmis,
  tearline, icmm and usgs have no date field at all.
* **`resource` is NULL where the source recorded none** (1,835 of 3,687 name
  one), in our own commodity vocabulary, mapped **by name never by substring**.
  `tang_werner` has neither commodity nor date for any row — stated in its
  `note` so the NULLs read as the dataset's shape, not as missing data.
* **`observed` is per row, not per source**: 131 of the IPIS Tanzania rows are
  *processing sites* (a mill), which say nothing about the rock underneath.
  Kept and labelled beats dropped.
* `source_id` is the id **the publisher** resolves: IPIS `pcode` (the site, not
  the visit row — 914 rows are repeat visits to 360 sites), OSM `type/id`,
  Crisis Tracker incident **IRNs** (our `ct_003` exists only in our file).
* Invariant 1 is enforced twice: a source yielding 0 anchors in scope is a
  `SystemExit`, and a `restricted` source reaching `SOURCES` is a `SystemExit`.

### 2. `srv/geomap_anchors.go`

Loads that file once (`loadGeoAnchors`), refuses an empty one, and exposes
`geoAnchorSummary()` for the catalogue — counts, per-source provenance, the
withheld block, the notice. **Not wired into `/api/geomap` yet.**

### 3. `srv/geomap_gpkg.go` — `geoMapSelection` type only

The type and its `unitSet()`/`pairSet()` helpers, plus
`buildGeoMapGeoPackageSel(path, sheets, sel)` which currently just forwards.
The design note is in the file and is the load-bearing part:

> A selection is a list of `"<sheet>:<code>"` unit keys and
> `"<sheet>:<a>|<b>"` contact pairs **resolved by the client**, never a copy of
> the chooser's five filter clauses. `visibleCodes()`/`visiblePairs()` in
> `geomap.js` are the same functions that feed the MapLibre filter, so the file
> *is* the picture by construction rather than by two implementations agreeing.
> A snapshot of codes is fine here (it lives for one request) and wrong in a
> share link (which travels as commodities and lithologies for that reason).

---

## Not done — pick up here

1. **Finish the filtered export.** Apply `sel.unitSet()` in
   `buildGeoMapGeoPackageSel`'s unit loop (fail if a non-nil selection matches
   0 units — a "current view" export whose view is empty is a broken filter,
   not an empty map). Add a `geology_contacts` LINESTRING layer from
   `<sheet>_contacts.geojson` (gitignored, 12–60 MB, `pair`/`code_a`/`code_b`/
   `km` plus the graded commodity and weight from `geoContactRuleIndex`) and a
   `mining_anchors` POINT layer. Contacts are the half the reader is being
   pointed at, so they must be in the file, styled by grade the way the map
   draws them.
2. **Routing.** `POST /api/geomap/geopackage` taking the selection JSON,
   building to a temp file, serving it once. The current `GET` stays as the
   whole-catalogue export and keeps its `geoMapGPKGReady()` stamp cache; a
   filtered build is per-request and must **not** overwrite `geology.gpkg`.
3. **The download arrow in the mixer.** In the panel bar
   (`headRow()` in `maplegend.js`, beside the settings/collapse/× buttons), so
   it is reachable from both tabs. It must offer **two** things and say which
   is which: *this view* and *everything*. The whole-catalogue link already
   exists in admin ▸ Map Settings (`geoMapDataRowHTML()` in `globe.html`) —
   don't build a third path, share `downloadGeoMapGPKG`'s "Preparing…" state.
4. **Anchors in the catalogue** (`HandleAPIGeoMap`, `geoAnchorSummary()`) so
   the panel can say "checked against 3,687 published workings" with the
   withheld line beside it.

### A bug found while testing, still open

**The panel bar's contact count goes stale and reads `no lines here` over a map
drawing hundreds of them.** Reproduce: `?geomap=sudan,car,tanzania`, open the
panel, click **Junctions**. `GeoMap.drawnContactCount()` = 452 and
`queryRenderedFeatures` = 439, but `.ml-bar-n` says `104 units · no lines here`
and stays wrong through `idle`, `triggerRepaint` and `MapLegend.refresh()`.
Closing and reopening the panel fixes it.

`contactHits` is measured in `measureCoverage()` (maplegend.js ~line 242) via
`contactsInView()`, which returns 0 while the layers do not exist yet — and
`MapLegend.mxMode('junction')` adds the contact layers and calls
`refreshWhenDrawn()` **in the same tick**, so the `idle` that `finish()` waits
on can fire before the new layers have rendered. `render()` then freezes
`contactHits = 0` and nothing re-measures, because `watchMap`'s `idle` handler
only rebuilds when `skillScope()` *changes*. The bar is exactly the surface
invariant "a label that contradicts the canvas is worse than no label" was
written for — and the comment at `geoOffView()` says this failure was already
paid for once, from the other direction.

Likely fix: re-measure on the `idle` after a layer is added (or have
`syncContactLayer` mark the coverage dirty), not a timeout.

### Judgement calls to preserve

* **Do not filter the anchors to the reader's commodity.** A file where every
  anchor agrees with the layer is a picture of our own filter and reads as a
  prediction that came true. Ship all of them; `resource` is a column, so the
  reader can narrow it and know that *they* did the narrowing.
* **The mixer itself is correct** as far as tested: gold row → 36 units, floor
  `any/likely/classic` → 36/15/3, junction cells and headers ADD (picks are a
  Set), `jcell:intrusive|volcanic` → 10 lines, `+ jcell:metamorphic|ultramafic`
  → 33, `+ jrow:intrusive` → 97. The gold row correctly shows `?0.06×` violet
  (contested) and the junction head `0.00×` — the *lowest* lift, never the
  flattering one, which is the rule. Don't "fix" that into a single number.
