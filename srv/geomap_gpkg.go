package srv

// The geology sheets as a GeoPackage — the fourth thing you can do with them
// after "show", "isolate a commodity" and "download the MBTiles".
//
// The MBTiles is a *picture* of the sheet: tiles are simplified per zoom,
// coalesced across neighbours, and carry no attribute typing. It is the right
// thing for an offline viewer and the wrong thing for anyone who wants to
// intersect the units with a concession boundary, dissolve by group, or ask
// "how many km2 of gold-hosting rock is inside this park". That is what this
// file is: the *source* polygons, whole and untouched, with typed columns.
//
// Two rules borrowed from docs/GEOPACKAGE_EXPORT.md, both already paid for
// there:
//
//   - The declared column type is the contract. `area_km2` is REAL and
//     `merged` is BOOLEAN because GDAL reports the declared type verbatim, and
//     a number arriving in QGIS as a string cannot be graduated or summed.
//   - Styles alone are not enough. A GeoPackage carries no layer order,
//     visibility or canvas, so the file embeds a QGIS project that opens on the
//     sheet with its printed ink colours already applied. Without it the user
//     gets one random pastel over an entire country and has to rebuild a
//     46-class legend by hand.
//
// Commodity filtering is the reason a user asks for this rather than a
// screenshot, so it is not left as a comma-joined string to be picked apart
// with LIKE (which would also make "gold" match nothing sensible once a
// commodity name becomes a substring of another). Every commodity the sheet
// mentions gets its own INTEGER weight column, `w_<commodity>`, NULL where the
// unit does not host it: `"w_gold" IS NOT NULL` is then an exact filter, and
// graduating on the weight is a map of how strong the affinity is. The
// human-readable list stays in `commodities` for labelling.
//
// Same disclaimer as everywhere else this surfaces: an affinity is an
// inference over lithology, never an occurrence. It is written into the layer
// description and the project title so it travels with the file.
//
// The build is a pure function of <sheet>_units.geojson, so it is done once and
// cached beside it — no job queue (unlike the per-area export, which depends on
// a date window and a live database). Staleness is mtime: a re-vectorized sheet
// rebuilds on the next request.

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type geoMapAffinity struct {
	Commodity string `json:"commodity"`
	Weight    int    `json:"weight"`
	Why       string `json:"why"`
}

type geoMapUnitProps struct {
	Sheet       string           `json:"sheet"`
	Code        string           `json:"code"`
	Codes       []string         `json:"codes"`
	Name        string           `json:"name"`
	Group       string           `json:"group"`
	Color       string           `json:"color"`
	Merged      bool             `json:"merged"`
	Commodities []string         `json:"commodities"`
	Affinity    []geoMapAffinity `json:"affinity"`
	AreaKm2     float64          `json:"area_km2"`
}

type geoMapCatalogue struct {
	Sheet     string `json:"sheet"`
	Title     string `json:"title"`
	Short     string `json:"short"`
	Year      int    `json:"year"`
	Publisher string `json:"publisher"`
	Scale     string `json:"scale"`
	SourceURL string `json:"source_url"`
}

// geoMapGPKGPath is also the DOWNLOAD name. The embedded QGIS project
// references its own container as "./<basename>.gpkg", so if the two ever
// diverge the project opens with every layer unresolvable.
//
// ONE FILE, EVERY SHEET. It used to be one GeoPackage per scanned sheet, which
// made the download mirror our storage layout rather than the user's question:
// "the geology" does not stop at a border, and anyone intersecting units with a
// concession or a park had to open two files, reconcile two column sets and
// union them by hand. The sheet survives as a COLUMN (`sheet`, `sheet_title`,
// `sheet_year`), which is what it is — provenance — exactly as the web map
// demoted it. Same reasoning as the single Geology toggle; see geomap.js note 4.
func geoMapGPKGPath() string {
	return filepath.Join(geoMaps.dir, "geology.gpkg")
}

func geoMapUnitsPath(sheet string) string {
	return filepath.Join(geoMaps.dir, sheet+"_units.geojson")
}

var geoMapGPKGMu sync.Mutex

// hexRGB turns "#ece8d5" into the "236,232,213" the QML helpers speak.
func hexRGB(hex string) string {
	h := strings.TrimPrefix(strings.TrimSpace(hex), "#")
	if len(h) != 6 {
		return "136,136,136"
	}
	v, err := strconv.ParseUint(h, 16, 32)
	if err != nil {
		return "136,136,136"
	}
	return fmt.Sprintf("%d,%d,%d", (v>>16)&0xff, (v>>8)&0xff, v&0xff)
}

// styleGeoUnits paints the units in the SAME legend the web map uses: fill
// colour = ICS/CGMW chronostratigraphic age, ornament = FGDC-STD-013-2006
// lithology hatch. See srv/geomap_std.go for why that legend and not ours.
//
// The two surfaces must not disagree. Someone who filters "gold" on the map,
// downloads the GeoPackage and opens it in QGIS is looking at the same
// polygons; if the export arrived in the scan's own inks (as it used to) the
// two pictures share no colour at all, and the export looks like different
// data. The printed ink is still in the file as `ink_color` and is what the
// "as printed" style column carries — not discarded, just not the default.
//
// QGIS renders the ornament natively: a SimpleFill for the age colour plus a
// LinePatternFill / PointPatternFill layer on top. That is a real symbol, not
// a raster texture, so it stays crisp at any print scale — which is the whole
// reason a geological map is drawn this way.
func styleGeoUnits(classes []geoMapUnitProps) string {
	cats := make([]qmlCat, 0, len(classes))
	var symXML strings.Builder
	var catXML strings.Builder
	for i, c := range classes {
		label := c.Code
		if c.Name != "" {
			label = c.Code + " — " + c.Name
		}
		ageKey, _ := geoAgeOf(c.Group)
		age := geoAgeByKey[ageKey]
		lith := geoLithOf(c.Name, c.Group, c.Codes)
		// Keyed on `key` = (sheet, code), NOT on code alone. One layer now
		// holds every sheet, and a code is only unique within its own sheet
		// ("S" is Silurian sandstone on Sudan and a gold-bearing schist belt
		// on CAR): categorising on `code` would give the two one symbol and
		// silently date half of one country from the other's legend.
		// The label carries the age so the QGIS legend reads like a
		// stratigraphic column.
		label = label + "  [" + age.Label + "]"
		if c.Sheet != "" {
			label = label + " · " + c.Sheet
		}
		key := geoMapUnitKey(c.Sheet, c.Code)
		fmt.Fprintf(&catXML, `<category render="1" value=%q label=%q symbol="%d"/>`+"\n", key, label, i)
		symXML.WriteString(qmlGeoUnitSymbol(fmt.Sprint(i), hexRGB(age.Color), lith) + "\n")
		cats = append(cats, qmlCat{Value: key, Label: label, RGB: hexRGB(age.Color)})
	}
	fmt.Fprintf(&catXML, `<category render="1" value="" label="other" symbol="%d"/>`+"\n", len(classes))
	symXML.WriteString(qmlGeoUnitSymbol(fmt.Sprint(len(classes)), "136,136,136", "mixed") + "\n")
	renderer := fmt.Sprintf(`<renderer-v2 type="categorizedSymbol" attr="key" forceraster="0" symbollevels="0" enableorderby="0">
  <categories>
%s  </categories>
  <symbols>
%s  </symbols>
</renderer-v2>`, catXML.String(), symXML.String())
	return qmlDoc(renderer)
}

// styleGeoUnitsAsPrinted is the second style shipped in the file: each unit in
// the ink measured off its own scan, no ornament. QGIS lists both in Layer
// Properties → Style → Load, so the question "what did the sheet look like"
// stays answerable without re-deriving anything.
func styleGeoUnitsAsPrinted(classes []geoMapUnitProps) string {
	cats := make([]qmlCat, 0, len(classes))
	for _, c := range classes {
		label := c.Code
		if c.Name != "" {
			label = c.Code + " — " + c.Name
		}
		if c.Sheet != "" {
			label = label + " · " + c.Sheet
		}
		cats = append(cats, qmlCat{Value: geoMapUnitKey(c.Sheet, c.Code), Label: label, RGB: hexRGB(c.Color)})
	}
	return qmlDoc(qmlCategorized("key", "fill", cats, "136,136,136", 0.2, 190))
}

// geoHatchInk is the ornament colour: a darkened version of the fill, exactly
// as geopatterns.js computes it for the web map. Never black (reads as a
// border) and never white (reads as snow).
func geoHatchInk(rgb string) string {
	var r, g, b int
	fmt.Sscanf(rgb, "%d,%d,%d", &r, &g, &b)
	lum := (0.299*float64(r) + 0.587*float64(g) + 0.114*float64(b)) / 255
	k := 0.62
	if lum > 0.55 {
		k = 0.45
	}
	return fmt.Sprintf("%d,%d,%d", int(float64(r)*(1-k)), int(float64(g)*(1-k)), int(float64(b)*(1-k)))
}

// geoOrnamentLayer is one pattern-fill layer of a lithology's ornament.
// A line layer uses (a=angle, b=spacing mm, dash); a marker layer uses
// (a=distance_x, b=distance_y, marker, size, angle).
type geoOrnamentLayer struct {
	a, b   string
	dash   string
	marker string
	size   string
	angle  string
}

// geoOrnaments is the FGDC-STD-013-2006 §37 families as QGIS pattern layers.
//
// Each choice, and why it is this and not the obvious thing:
//
//	alluvium/sandstone  Stipple, coarse and fine. FGDC dots; the only two
//	                    families that differ from each other by density
//	                    alone, which is what the standard does too.
//	mudrock             Fine horizontal dashes. Dash period == spacing, so
//	                    the axis-aligned tile cannot clip it (trap 1 above).
//	carbonate           REAL brick courses: solid horizontal rules at 2.4 mm
//	                    plus a vertical dashed at 4.8 mm whose dash period
//	                    equals its own spacing, which puts one vertical tick
//	                    per course and offsets it every other row — a brick.
//	                    Was `90° dashed 2;6 at 3.6mm`, which rendered ZERO
//	                    pixels, so carbonate was flat horizontal rules and
//	                    indistinguishable from mudrock at a glance.
//	intrusive           A PLUS-SIGN MARKER, not two dashed hatches. FGDC's
//	                    intrusive ornament is a field of discrete plus-signs;
//	                    drawing it as two coarsely dashed 45°/135° hatches
//	                    needed the dashes to line up, which QGIS's tile does
//	                    not guarantee, and it rendered as a sparse mesh.
//	                    1.7 mm at 3.4 mm spacing is the size at which the arms
//	                    of the plus are legible at 96 dpi — 0.9 mm, the first
//	                    attempt, rendered as a smudged dot indistinguishable
//	                    from the alluvium stipple.
//	volcanic            A FILLED TRIANGLE MARKER. FGDC uses a "v"; a triangle
//	                    is the nearest primitive, and it is a discrete shape,
//	                    which a dashed diagonal is not. This was a dashed 45°
//	                    hatch that at real scale was indistinguishable from
//	                    `mixed`. Filled rather than hollow: at 1.3 mm a hollow
//	                    outline is three hairlines and reads as noise.
//	metamorphic         Dashes at 20° — off-axis, off every other family's
//	                    angle, and the standard's wavy schistosity dash.
//	ultramafic          Solid 45°+135° cross-hatch, no dash anywhere: the one
//	                    family the standard draws as a full mesh, and solid
//	                    lines are the case QGIS's tile always gets right.
//	ironstone           Long-short horizontal dashes, again period<=spacing.
//	mixed               Sparse plain 45° dashes — deliberately the LIGHTEST
//	                    ornament, because it is the one that means "the sheet
//	                    does not say", and it must not out-shout a family
//	                    that does say something.
var geoOrnaments = map[string][]geoOrnamentLayer{
	"alluvium":    {{a: "3.2", b: "3.2", marker: "circle", size: "0.7", angle: "0"}},
	"sandstone":   {{a: "1.8", b: "1.8", marker: "circle", size: "0.4", angle: "0"}},
	"mudrock":     {{a: "0", b: "1.6", dash: "1.2;1.2"}},
	"carbonate":   {{a: "0", b: "2.4"}, {a: "90", b: "4.8", dash: "2.4;2.4"}},
	"intrusive":   {{a: "3.4", b: "3.4", marker: "cross", size: "1.7", angle: "0"}},
	"volcanic":    {{a: "3.4", b: "3.4", marker: "triangle", size: "1.3", angle: "0"}},
	"metamorphic": {{a: "20", b: "2.0", dash: "1.5;1.5"}},
	"ultramafic":  {{a: "45", b: "2.2"}, {a: "135", b: "2.2"}},
	"ironstone":   {{a: "0", b: "2.2", dash: "2.2;1.1"}},
	"mixed":       {{a: "45", b: "3.4", dash: "2.5;2.5"}},
}

// geoOrnamentOf falls back to the `mixed` ornament, which is the same thing
// geoLithOf's own fallback means — and a lithology key with no ornament would
// otherwise render as a flat colour, i.e. as a unit that is not geology.
func geoOrnamentOf(lith string) []geoOrnamentLayer {
	if o, ok := geoOrnaments[lith]; ok {
		return o
	}
	return geoOrnaments["mixed"]
}

// qmlGeoUnitSymbol = age colour + the lithology's FGDC ornament, as a
// multi-layer QGIS fill symbol.
//
// The ornament families map onto QGIS's own pattern fill layers:
//
//	LinePatternFill   at an angle, dashed or solid  — the hatches and bricks
//	PointPatternFill  with a marker sub-symbol       — the stipples, plusses,
//	                                                  crosses and "v"s
//
// EVERYTHING BELOW WAS CHOSEN BY RENDERING IT, not by reading QGIS's docs.
// `scripts/geomaps/render_gpkg.py` opens the shipped GeoPackage through its own
// embedded project and draws every symbol; three of the nine families were
// wrong in ways no byte-level test could see (see the handover doc). The two
// hard constraints that came out of that, both of them QGIS 3.34 behaviour
// rather than anything in the standard:
//
//	1. A CUSTOM DASH ON AN AXIS-ALIGNED PATTERN LINE (angle 0/90/180/270)
//	   IS DISCARDED. QGIS renders a LinePatternFill by building a small
//	   repeating tile; on an axis-aligned angle the tile is only as long as
//	   the line spacing, so a dash whose period exceeds it is clipped to
//	   either nothing or a solid rule. Measured: a `2;6` dash at angle 90,
//	   spacing 3.6 mm — the vertical course of the carbonate brick — rendered
//	   ZERO pixels, so "brick" shipped for two years as plain horizontal
//	   rules. `use_custom_dash` was set correctly and on the right layer; the
//	   dash was simply thrown away downstream. So: on an axis-aligned line
//	   the dash period must stay at or below the spacing (checked by
//	   TestGeoOrnamentDashesSurviveTheQGISPatternTile).
//	2. OFF-AXIS IS NOT SAFE EITHER — the same clipping bites at 45°/135° for
//	   a long enough period, which is what made the intrusive cross-hatch
//	   render at a third of its intended density. Where FGDC wants a
//	   discrete SHAPE (a plus, a cross, a "v") the honest primitive is a
//	   PointPatternFill marker, not a pair of coarsely dashed hatches
//	   pretending to be one: the marker is what the shape actually is, and it
//	   is not subject to the dash tile at all.
func qmlGeoUnitSymbol(name, rgb, lith string) string {
	ink := geoHatchInk(rgb)
	base := fmt.Sprintf(`<layer class="SimpleFill" enabled="1" locked="0" pass="0">
      <Option type="Map">
        %s
        %s
        %s
        %s
        %s
      </Option>
    </layer>`,
		qmlOpt("color", rgb+",190"),
		qmlOpt("style", "solid"),
		qmlOpt("outline_color", ink+",255"),
		qmlOpt("outline_style", "solid"),
		qmlOpt("outline_width", "0.2"))

	// A sub-symbol's name must be "@<parent>@<layer index>" and must be
	// UNIQUE within the parent: two hatch layers (a cross-hatch, brick
	// courses) both called @3@1 make QGIS drop one, so one ornament silently
	// renders as half of itself. `sub` counts the layers we have added,
	// starting at 1 because layer 0 is the SimpleFill.
	sub := 0
	// One dashed/solid hatch layer at `angle`, spaced `distance` mm apart.
	// `dash` is QGIS's "on;off" custom dash in mm; empty = a solid rule.
	linePat := func(angle, distance, dash string) string {
		sub++
		lineOpts := []string{
			qmlOpt("line_color", ink+",255"),
			qmlOpt("line_width", "0.26"),
			qmlOpt("line_width_unit", "MM"),
		}
		if dash != "" {
			// use_custom_dash belongs on the LINE layer, not on the fill:
			// setting it on the pattern silently does nothing and every
			// ornament comes out solid, i.e. all nine look alike.
			lineOpts = append(lineOpts,
				qmlOpt("use_custom_dash", "1"),
				qmlOpt("customdash", dash),
				qmlOpt("customdash_unit", "MM"))
		}
		symXML := fmt.Sprintf(`<symbol type="line" name="@%s@%d" alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
        <layer class="SimpleLine" enabled="1" locked="0" pass="0">
          <Option type="Map">
            %s
          </Option>
        </layer>
      </symbol>`, name, sub, strings.Join(lineOpts, "\n            "))
		return fmt.Sprintf(`<layer class="LinePatternFill" enabled="1" locked="0" pass="0">
      <Option type="Map">
        %s
        %s
        %s
      </Option>
      %s
    </layer>`,
			qmlOpt("angle", angle),
			qmlOpt("distance", distance),
			qmlOpt("distance_unit", "MM"),
			symXML)
	}
	pointPat := func(distX, distY, shape, size, angle string) string {
		sub++
		markerOpts := []string{
			qmlOpt("name", shape),
			qmlOpt("color", ink+",255"),
			qmlOpt("size", size),
			qmlOpt("size_unit", "MM"),
			qmlOpt("angle", angle),
		}
		// A stroke-only shape (a plus, a cross) has no interior to fill, so
		// `color` alone leaves it INVISIBLE; it needs the stroke, and the
		// stroke needs a width in MM or it hairlines away at print scale. A
		// closed shape (circle, triangle) is filled and needs no stroke.
		switch shape {
		case "cross", "cross2", "line":
			markerOpts = append(markerOpts,
				qmlOpt("outline_style", "solid"),
				qmlOpt("outline_color", ink+",255"),
				qmlOpt("outline_width", "0.26"),
				qmlOpt("outline_width_unit", "MM"))
		default:
			markerOpts = append(markerOpts, qmlOpt("outline_style", "no"))
		}
		symXML := fmt.Sprintf(`<symbol type="marker" name="@%s@%d" alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
        <layer class="SimpleMarker" enabled="1" locked="0" pass="0">
          <Option type="Map">
            %s
          </Option>
        </layer>
      </symbol>`, name, sub, strings.Join(markerOpts, "\n            "))
		return fmt.Sprintf(`<layer class="PointPatternFill" enabled="1" locked="0" pass="0">
      <Option type="Map">
        %s
        %s
        %s
        %s
      </Option>
      %s
    </layer>`,
			qmlOpt("distance_x", distX),
			qmlOpt("distance_y", distY),
			qmlOpt("distance_x_unit", "MM"),
			qmlOpt("distance_y_unit", "MM"),
			symXML)
	}

	// The nine families. Every line here has been rendered and looked at; the
	// spacings are the ones that came out legible at 96 dpi rather than the
	// ones that read well in source. `geoOrnaments` is the same table in data
	// form, so a test can check the dash/spacing rule without parsing XML.
	orn := ""
	for _, o := range geoOrnamentOf(lith) {
		if o.marker != "" {
			orn += pointPat(o.a, o.b, o.marker, o.size, o.angle)
		} else {
			orn += linePat(o.a, o.b, o.dash)
		}
	}

	return fmt.Sprintf(`<symbol type="fill" name=%q alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
    %s
    %s
  </symbol>`, name, base, orn)
}

// geoMapGPKGSheets is the sheets that can go into the combined package: every
// configured sheet whose vectorized units are on disk, in catalogue order.
// Derived, never a fixed list — a server with only one sheet built must ship a
// package containing that one sheet, not fail, and a third sheet added later
// must appear without an edit here.
func geoMapGPKGSheets() []string {
	out := make([]string, 0, len(geoMapSheets))
	for _, id := range geoMapSheets {
		if _, err := os.Stat(geoMapUnitsPath(id)); err == nil {
			out = append(out, id)
		}
	}
	return out
}

// geoMapGPKGStamp records exactly which inputs a cached package was built
// from. mtime alone is not enough here, because the input is now a SET: adding
// a sheet whose units file happens to be older than the package (a restore, a
// copy that preserved timestamps) would otherwise leave the old two-sheet file
// looking fresh, and the user would download a country short of what the map
// draws. A no-op must not read as an answer.
type geoMapGPKGStamp struct {
	Sheets []geoMapGPKGInput `json:"sheets"`
}

type geoMapGPKGInput struct {
	Sheet string `json:"sheet"`
	MTime int64  `json:"mtime"`
	Size  int64  `json:"size"`
}

func geoMapGPKGStampPath() string { return geoMapGPKGPath() + ".stamp" }

func geoMapGPKGInputs() geoMapGPKGStamp {
	var st geoMapGPKGStamp
	for _, id := range geoMapGPKGSheets() {
		fi, err := os.Stat(geoMapUnitsPath(id))
		if err != nil {
			continue
		}
		st.Sheets = append(st.Sheets, geoMapGPKGInput{id, fi.ModTime().Unix(), fi.Size()})
	}
	return st
}

// buildGeoMapGeoPackage writes every sheet's polygons into ONE layer.
//
// Every unit is one MultiPolygon feature covering the whole country, i.e. the
// layer has ~20-70 rows. That is the vectorizer's own output shape (one
// dissolved multipart per class) and it is kept: exploding to parts here would
// invent a feature count the source does not have, and QGIS's "Multipart to
// singleparts" is one menu item away for anyone who wants it.
//
// One layer, not one per sheet, for the same reason the map has one toggle: the
// question is "what rock is under here", and a border is not part of it. The
// scan is a column. Two consequences that are load-bearing:
//
//   - `code` is only unique WITHIN a sheet ("S" is Silurian sandstone on Sudan
//     and a gold-bearing schist belt on CAR), so anything that identifies a row
//     uses (sheet, code). The QGIS legend is categorised on `key`, which is
//     exactly that pair, or two sheets' "S" would share one symbol and one
//     commodity affinity.
//   - the commodity columns are the UNION over all sheets, so `"w_gold" IS NOT
//     NULL` answers across the whole area rather than per file.
func buildGeoMapGeoPackage(path string, sheets []string) error {
	if len(sheets) == 0 {
		return fmt.Errorf("no vectorized sheets are built")
	}

	type unit struct {
		props geoMapUnitProps
		geom  json.RawMessage
		cat   geoMapCatalogue
	}
	var units []unit
	var titles []string
	commodities := map[string]bool{}

	for _, sheet := range sheets {
		blob, err := os.ReadFile(geoMapUnitsPath(sheet))
		if err != nil {
			return fmt.Errorf("vectorized units not built for %s: %w", sheet, err)
		}
		var fc struct {
			Features []struct {
				Properties geoMapUnitProps `json:"properties"`
				Geometry   json.RawMessage `json:"geometry"`
			} `json:"features"`
		}
		if err := json.Unmarshal(blob, &fc); err != nil {
			return fmt.Errorf("units geojson %s: %w", sheet, err)
		}
		// A sheet that contributes nothing is a broken input, not an empty
		// country: fail rather than quietly shipping a package that is one
		// country short of the map.
		if len(fc.Features) == 0 {
			return fmt.Errorf("units geojson for %s has no features", sheet)
		}
		var cat geoMapCatalogue
		if b, err := os.ReadFile(filepath.Join(geoMaps.dir, sheet+"_classes.json")); err == nil {
			_ = json.Unmarshal(b, &cat)
		}
		if cat.Title == "" {
			cat.Title = sheet + " geology"
		}
		if cat.Short == "" {
			cat.Short = sheet
		}
		cat.Sheet = sheet
		titles = append(titles, cat.Title)
		for _, f := range fc.Features {
			units = append(units, unit{f.Properties, f.Geometry, cat})
			for _, a := range f.Properties.Affinity {
				commodities[a.Commodity] = true
			}
		}
	}

	// The commodity columns are derived from what these builds of the sheets
	// actually mention, never from a fixed list: a re-vectorized sheet can
	// merge two classes and thereby change the union of affinities, and a
	// hardcoded column set would then either lie or drop one.
	comms := make([]string, 0, len(commodities))
	for c := range commodities {
		comms = append(comms, c)
	}
	sort.Strings(comms)

	tmp := path + ".tmp"
	w, err := newGPKGWriter(tmp)
	if err != nil {
		return err
	}
	done := false
	defer func() {
		if !done {
			w.Close()
			os.Remove(tmp)
		}
	}()

	table := "geology_units"
	cols := []gpkgCol{
		// (sheet, code) is the identity of a unit; `key` is that pair as one
		// string so a QGIS categorised renderer (which takes ONE field) can
		// key on it without two sheets' identical codes colliding.
		{"key", "TEXT"},
		{"code", "TEXT"},
		{"unit_name", "TEXT"},
		{"unit_group", "TEXT"},
		// The shared legend, as columns — so "select every Neoproterozoic
		// unit across both sheets" is one filter and not a lookup table
		// somebody has to rebuild by hand. Same keys the web map uses.
		{"age", "TEXT"},
		{"age_label", "TEXT"},
		{"age_rank", "INTEGER"},
		{"age_mixed", "BOOLEAN"},
		{"lithology", "TEXT"},
		{"ics_color", "TEXT"},
		{"codes", "TEXT"},
		{"merged", "BOOLEAN"},
		{"ink_color", "TEXT"},
		{"area_km2", "REAL"},
		{"commodities", "TEXT"},
		{"affinity_note", "TEXT"},
		{"sheet", "TEXT"},
		{"sheet_title", "TEXT"},
		{"sheet_year", "INTEGER"},
	}
	for _, c := range comms {
		cols = append(cols, gpkgCol{"w_" + c, "INTEGER"})
	}
	desc := "Geological units vectorized from " + strings.Join(titles, " + ") + ". " +
		"Coloured by ICS/CGMW age with FGDC-STD-013-2006 lithology ornament; " +
		"ink_color keeps each sheet's own printed ink. " +
		"A unit is identified by (sheet, code) — the same code can mean different rock on another sheet. " +
		"w_<commodity> is an inference from lithology (1-3, 3 = classic host), not a record of any deposit."
	l, err := w.AddLayer(table, "MULTIPOLYGON", desc, cols)
	if err != nil {
		return err
	}

	classes := make([]geoMapUnitProps, 0, len(units))
	for _, u := range units {
		p := u.props
		if p.Sheet == "" {
			p.Sheet = u.cat.Sheet
		}
		classes = append(classes, p)
		ageKey, ageMixed := geoAgeOf(p.Group)
		age := geoAgeByKey[ageKey]
		byComm := map[string]geoMapAffinity{}
		whys := make([]string, 0, len(p.Affinity))
		for _, a := range p.Affinity {
			byComm[a.Commodity] = a
			whys = append(whys, fmt.Sprintf("%s (%d): %s", a.Commodity, a.Weight, a.Why))
		}
		vals := []interface{}{
			geoMapUnitKey(p.Sheet, p.Code),
			p.Code, gpkgStr(p.Name), gpkgStr(p.Group),
			ageKey, age.Label, age.Rank, gpkgBool(ageMixed),
			geoLithOf(p.Name, p.Group, p.Codes), age.Color,
			strings.Join(p.Codes, ","), gpkgBool(p.Merged), gpkgStr(p.Color),
			p.AreaKm2, gpkgStr(strings.Join(p.Commodities, ",")),
			gpkgStr(strings.Join(whys, "; ")),
			p.Sheet, u.cat.Title, u.cat.Year,
		}
		for _, c := range comms {
			if a, ok := byComm[c]; ok {
				vals = append(vals, a.Weight)
			} else {
				vals = append(vals, nil)
			}
		}
		l.Add(string(u.geom), vals...)
	}
	if l.Count() == 0 {
		return fmt.Errorf("no unit geometry could be written")
	}
	qml := styleGeoUnits(classes)
	w.SetStyle(table, qml, "Age (ICS/CGMW) with FGDC lithology ornament")
	// A second, non-default style: each sheet in its own printed inks. QGIS
	// lists every row of layer_styles under Style → Load, so "what did the
	// scan look like" stays one click away instead of needing the ink_color
	// column and a hand-built renderer.
	w.SetStyleNamed(table, "as_printed", styleGeoUnitsAsPrinted(classes),
		"Each sheet's own printed ink per unit", false)

	grow := math.Max(0.05, (l.maxx-l.minx)*0.03)
	ext := [4]float64{l.minx - grow, l.miny - grow, l.maxx + grow, l.maxy + grow}
	if err := w.writeQGISProject("Geology (5MP)", filepath.Base(path), []gpkgLayerSpec{{
		Table: table, Title: "Geological units", Group: "Geology",
		Geometry: "Polygon", WKBType: "MultiPolygon", QML: qml, Visible: true, Opacity: 0.75,
	}}, ext); err != nil {
		slog.Warn("geomap gpkg project", "err", err)
	}
	if err := w.Finish(); err != nil {
		return err
	}
	if b, err := json.Marshal(geoMapGPKGInputs()); err == nil {
		_ = os.WriteFile(geoMapGPKGStampPath(), b, 0o644)
	}
	done = true
	return os.Rename(tmp, path)
}

// geoMapUnitKey is the identity of a unit across sheets. Deliberately the same
// "<sheet>:<code>" the web map's share links use, so a code copied out of QGIS
// pastes back into the map.
func geoMapUnitKey(sheet, code string) string { return sheet + ":" + code }

// geoMapGPKGReady reports the cached file if it was built from exactly the
// inputs on disk now. Anything else (missing, an input rewritten, a sheet
// added or removed) means "build on demand" — the same stale-artefact trap as
// the tile ?v= revision, one level up.
func geoMapGPKGReady() (os.FileInfo, bool) {
	st, err := os.Stat(geoMapGPKGPath())
	if err != nil {
		return nil, false
	}
	want := geoMapGPKGInputs()
	if len(want.Sheets) == 0 {
		// Units gone (gitignored derived output can be cleaned) but a built
		// package survives: serve it. It is a snapshot of a real build, and
		// refusing it would be a worse answer than a slightly old one.
		return st, true
	}
	blob, err := os.ReadFile(geoMapGPKGStampPath())
	if err != nil {
		return nil, false
	}
	var have geoMapGPKGStamp
	if err := json.Unmarshal(blob, &have); err != nil {
		return nil, false
	}
	if len(have.Sheets) != len(want.Sheets) {
		return nil, false
	}
	for i := range want.Sheets {
		if have.Sheets[i] != want.Sheets[i] {
			return nil, false
		}
	}
	return st, true
}

// HandleAPIGeoMapGeoPackage serves every sheet as ONE GeoPackage, building it
// on first request. One build at a time, globally: the sheets together take a
// few seconds and a second concurrent request would only duplicate the work.
//
// Synchronous, deliberately — unlike the per-area export in gpkg_jobs.go. That
// one takes minutes over a live database and must therefore be a job with a
// progress card; this is one static file, so a job queue would add a
// notification, a poll and a card to a few seconds' wait.
func (s *Server) HandleAPIGeoMapGeoPackage(w http.ResponseWriter, r *http.Request) {
	path := geoMapGPKGPath()
	if _, ok := geoMapGPKGReady(); !ok {
		geoMapGPKGMu.Lock()
		if _, ok := geoMapGPKGReady(); !ok {
			t0 := time.Now()
			sheets := geoMapGPKGSheets()
			if err := buildGeoMapGeoPackage(path, sheets); err != nil {
				geoMapGPKGMu.Unlock()
				slog.Warn("geomap gpkg build failed", "err", err)
				http.Error(w, "GeoPackage not available: "+err.Error(), http.StatusNotFound)
				return
			}
			slog.Info("geomap gpkg built", "sheets", sheets, "took", time.Since(t0))
		}
		geoMapGPKGMu.Unlock()
	}
	f, err := os.Open(path)
	if err != nil {
		http.Error(w, "GeoPackage not available", http.StatusNotFound)
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		http.Error(w, "stat failed", http.StatusInternalServerError)
		return
	}
	name := filepath.Base(path)
	w.Header().Set("Content-Type", "application/geopackage+sqlite3")
	w.Header().Set("Content-Disposition", `attachment; filename="`+name+`"`)
	http.ServeContent(w, r, name, st.ModTime(), f)
}

// HandleAPIGeoMapGeoPackageLegacy keeps /api/geomap/{sheet}/geopackage working.
//
// The export used to be per sheet and those URLs are in shipped share links,
// in docs/GEOLOGY.md and in scripts/geomaps/render_gpkg.py. There is
// now one file covering every sheet, so the honest answer to "give me CAR's
// GeoPackage" is that file — a 404 would read as "the export was removed",
// which is not what happened. 308 rather than 302 so a client that followed it
// with a range request keeps its method and its Range header.
func (s *Server) HandleAPIGeoMapGeoPackageLegacy(w http.ResponseWriter, r *http.Request) {
	if geoMaps.load()[r.PathValue("sheet")] == nil {
		http.Error(w, "unknown sheet", http.StatusNotFound)
		return
	}
	target := "/api/geomap/geopackage"
	if q := r.URL.RawQuery; q != "" {
		target += "?" + q
	}
	http.Redirect(w, r, target, http.StatusPermanentRedirect)
}
