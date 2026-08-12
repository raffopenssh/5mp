# Handover: geological contact zones

**Task:** turn "where two units MEET" into a real layer. Read
`docs/agents/overlays.md` and `docs/GEOLOGY.md` first; this file is only the
brief.

Delete this file when the work lands.

## Why

The affinity model answers *which rock* can host a commodity. The honest next
question is *where two of them meet*: a granite/greenstone contact is the
classic orogenic-gold setting, a carbonate/intrusive contact is the skarn
setting. **Prospectivity there is a property of the boundary, not of either
polygon**, so nothing in the current model can express it — the matrix can say
"greenstone hosts gold" and "late granite hosts gold" but not "the line between
them is worth more than either".

The data already carries it. On the Sudan sheet alone, **528 unit pairs share a
boundary**, total shared length ≈ 757 degrees. Measured, in shapely 2.1.2:

```python
# 46 features, MultiPolygon; ~33 s for the full pairwise pass
inter = a.boundary.intersection(b.boundary); inter.length
```

## Hard constraints

1. **Derive it in the build, never in the browser.** 500+ pairwise boundary
   intersections per pan would ship as a hang. It belongs in
   `scripts/geomaps/` beside `vectorize.py`, written to a file, tiled by
   `tiles.sh`, served by `srv/geomap.go` like any other attribute.
2. **A contact is a pair, and the pair is the payload.** A line feature needs
   both codes (`code_a`, `code_b`), both ages, both lithologies — the UI has to
   answer "granite against greenstone" without a second lookup.
3. **The commodity model must extend, not fork.** Today an affinity is
   `(sheet, code) -> [(commodity, weight, why)]` in `scripts/geomaps/legend.py`.
   A contact affinity is `(lith_a, lith_b) -> [(commodity, weight, why)]` — a
   statement about a *pair of rock types*, so it generalises the existing table
   rather than duplicating it. Keep `weight` on the same 1–3 scale; the matrix,
   the floor, the unit list and the map tip all already render that scale and
   must not learn a second one.
4. **Invariant 1 applies to the derivation.** A pass that finds 0 contacts for
   a sheet with 46 units must report *unfinished*, not succeed. Sliver
   intersections (two polygons touching at a point, or along 3 m of digitising
   noise) are not contacts — pick a minimum length, state it, and count what
   you dropped.
5. **Never claim a deposit.** Same disclaimer discipline as the rest of the
   overlay: this is an inference from a boundary between two rock types.
   Nothing here counts, ranks or locates an occurrence.

## Where it plugs into the UI

Already built and waiting:

* `openGeoMenu()` in `srv/static/maplegend.js` ships a **`.refused` "Contact
  zones — soon" row** with the reasoning in its `title`. Enabling it is the
  visible half of this task.
* The matrix (`.ml-mx`) is rock × commodity. A contact is a *pair* of rocks, so
  it is **not** another row: consider it a second mode of the same object (an
  upper triangle of rock × rock, cell = what that junction can host), which is
  the same table transposed onto itself. Decide deliberately — do not bolt it
  on as an eleventh commodity row.
* The key strip's **brackets** (`ageSwatches()`) already tie a commodity to the
  ages it covers. A contact bracket spanning two swatches is the natural
  drawing of "these two, where they touch", and `.ml-br` is already a grid
  span — reuse it.
* Rendering: a line layer over `geomap-fill-*`, below pins/trajectories. See
  the layer-order note in `overlays.md` — insert *before the first non-raster
  layer*, and `switchBasemap()` must not capture it generically.

## Read before starting

| What | Where |
|---|---|
| Sheet build, adding a sheet | `docs/GEOLOGY.md` § "Adding a sheet" |
| Affinity table, 1–3 weights | `scripts/geomaps/legend.py` (~line 251) |
| Class catalogue shape | `data/geomaps/<sheet>_classes.json` |
| Unit geometry | `data/geomaps/<sheet>_units.geojson` (gitignored) |
| Tiling | `scripts/geomaps/tiles.sh` (why every class is kept at every zoom) |
| Serving + share params | `srv/geomap.go`, `srv/static/geomap.js` |
| Matrix / key / escape rules | `docs/agents/map-ui.md` § "The key is the interface" |

## Open questions for the new conversation

* Which pairs are worth naming at all? 528 on one sheet is too many to list;
  the ones with a **commodity affinity** are probably the only ones that earn a
  line, but that is a decision to make with the legend in front of you.
* Does a contact get a *width*? A buffered zone is what a geologist means
  ("within ~2 km of the contact"), but a buffer is a claim about scale that a
  1:1.5M sheet may not support. A hairline is the conservative answer.
* Tanzania has no scan and no classifier (WFS source). Its contacts come from
  the same polygon topology, so the path should be identical — verify, don't
  assume.
