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
func geoMapGPKGPath(sheet string) string {
	return filepath.Join(geoMaps.dir, sheet+"_geology.gpkg")
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
		// Categorised by AGE in the legend text but keyed on `code`, because
		// code is what identifies a row; the label carries the age so the
		// QGIS legend reads like a stratigraphic column.
		label = label + "  [" + age.Label + "]"
		fmt.Fprintf(&catXML, `<category render="1" value=%q label=%q symbol="%d"/>`+"\n", c.Code, label, i)
		symXML.WriteString(qmlGeoUnitSymbol(fmt.Sprint(i), hexRGB(age.Color), lith) + "\n")
		cats = append(cats, qmlCat{Value: c.Code, Label: label, RGB: hexRGB(age.Color)})
	}
	fmt.Fprintf(&catXML, `<category render="1" value="" label="other" symbol="%d"/>`+"\n", len(classes))
	symXML.WriteString(qmlGeoUnitSymbol(fmt.Sprint(len(classes)), "136,136,136", "mixed") + "\n")
	renderer := fmt.Sprintf(`<renderer-v2 type="categorizedSymbol" attr="code" forceraster="0" symbollevels="0" enableorderby="0">
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
		cats = append(cats, qmlCat{Value: c.Code, Label: label, RGB: hexRGB(c.Color)})
	}
	return qmlDoc(qmlCategorized("code", "fill", cats, "136,136,136", 0.2, 190))
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

// qmlGeoUnitSymbol = age colour + the lithology's FGDC ornament, as a
// two-layer QGIS fill symbol.
//
// The ornament families map onto QGIS's own pattern fill layers:
//
//	LinePatternFill   at 0°/45°/90°/cross, dashed or solid  — the hatches
//	PointPatternFill  with a marker sub-symbol                — the stipples
//
// A carbonate "brick" and a volcanic "v" have no primitive in QGIS, so they
// are approximated by the closest standard pattern (crossed courses; a dense
// dashed 45°) rather than by an SVG that would have to ship beside the file
// and would go missing. The web map draws them properly; the export is honest
// about being an approximation of the same idea rather than pretending.
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
	pointPat := func(distX, distY, size string) string {
		sub++
		symXML := fmt.Sprintf(`<symbol type="marker" name="@%s@%d" alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
        <layer class="SimpleMarker" enabled="1" locked="0" pass="0">
          <Option type="Map">
            %s
            %s
            %s
            %s
            %s
          </Option>
        </layer>
      </symbol>`, name, sub,
			qmlOpt("name", "circle"),
			qmlOpt("color", ink+",255"),
			qmlOpt("outline_style", "no"),
			qmlOpt("size", size),
			qmlOpt("size_unit", "MM"))
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

	var orn string
	switch lith {
	case "alluvium":
		orn = pointPat("3.2", "3.2", "0.7")
	case "sandstone":
		orn = pointPat("1.8", "1.8", "0.4")
	case "mudrock":
		orn = linePat("0", "1.6", "3;2")
	case "carbonate":
		// Brick courses: horizontal rules plus a coarse vertical, which is
		// as close as two primitive pattern layers get to FGDC 627.
		orn = linePat("0", "1.8", "") + linePat("90", "3.6", "2;6")
	case "intrusive":
		orn = linePat("45", "2.6", "1;5") + linePat("135", "2.6", "1;5")
	case "volcanic":
		orn = linePat("45", "2.4", "1.2;4")
	case "metamorphic":
		orn = linePat("20", "1.7", "3;2.5")
	case "ultramafic":
		orn = linePat("45", "1.6", "") + linePat("135", "1.6", "")
	case "ironstone":
		orn = linePat("0", "2.2", "4;1.5;0.6;1.5")
	default: // mixed / unknown
		orn = linePat("45", "3.4", "2;4")
	}

	return fmt.Sprintf(`<symbol type="fill" name=%q alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
    %s
    %s
  </symbol>`, name, base, orn)
}

// buildGeoMapGeoPackage writes the sheet's polygons to path.
//
// Every unit is one MultiPolygon feature covering the whole country, i.e. the
// layer has ~20-50 rows. That is the vectorizer's own output shape (one
// dissolved multipart per class) and it is kept: exploding to parts here would
// invent a feature count the source does not have, and QGIS's "Multipart to
// singleparts" is one menu item away for anyone who wants it.
func buildGeoMapGeoPackage(sheet, path string) error {
	blob, err := os.ReadFile(geoMapUnitsPath(sheet))
	if err != nil {
		return fmt.Errorf("vectorized units not built: %w", err)
	}
	var fc struct {
		Features []struct {
			Properties geoMapUnitProps `json:"properties"`
			Geometry   json.RawMessage `json:"geometry"`
		} `json:"features"`
	}
	if err := json.Unmarshal(blob, &fc); err != nil {
		return fmt.Errorf("units geojson: %w", err)
	}
	if len(fc.Features) == 0 {
		return fmt.Errorf("units geojson has no features")
	}
	var cat geoMapCatalogue
	if b, err := os.ReadFile(filepath.Join(geoMaps.dir, sheet+"_classes.json")); err == nil {
		_ = json.Unmarshal(b, &cat)
	}
	if cat.Title == "" {
		cat.Title = sheet + " geology"
	}

	// The commodity columns are derived from what this build of the sheet
	// actually mentions, never from a fixed list: a re-vectorized sheet can
	// merge two classes and thereby change the union of affinities, and a
	// hardcoded column set would then either lie or drop one.
	commodities := map[string]bool{}
	for _, f := range fc.Features {
		for _, a := range f.Properties.Affinity {
			commodities[a.Commodity] = true
		}
	}
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

	table := "geology_" + sheet
	cols := []gpkgCol{
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
	desc := cat.Title + " — units vectorized from the printed sheet. " +
		"Coloured by ICS/CGMW age with FGDC-STD-013-2006 lithology ornament; " +
		"ink_color keeps the sheet's own printed ink. " +
		"w_<commodity> is an inference from lithology (1-3, 3 = classic host), not a record of any deposit."
	l, err := w.AddLayer(table, "MULTIPOLYGON", desc, cols)
	if err != nil {
		return err
	}

	classes := make([]geoMapUnitProps, 0, len(fc.Features))
	for _, f := range fc.Features {
		p := f.Properties
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
			p.Code, gpkgStr(p.Name), gpkgStr(p.Group),
			ageKey, age.Label, age.Rank, gpkgBool(ageMixed),
			geoLithOf(p.Name, p.Group, p.Codes), age.Color,
			strings.Join(p.Codes, ","), gpkgBool(p.Merged), gpkgStr(p.Color),
			p.AreaKm2, gpkgStr(strings.Join(p.Commodities, ",")),
			gpkgStr(strings.Join(whys, "; ")),
			sheet, cat.Title, cat.Year,
		}
		for _, c := range comms {
			if a, ok := byComm[c]; ok {
				vals = append(vals, a.Weight)
			} else {
				vals = append(vals, nil)
			}
		}
		l.Add(string(f.Geometry), vals...)
	}
	if l.Count() == 0 {
		return fmt.Errorf("no unit geometry could be written")
	}
	qml := styleGeoUnits(classes)
	w.SetStyle(table, qml, "Age (ICS/CGMW) with FGDC lithology ornament")
	// A second, non-default style: the sheet in its own printed inks. QGIS
	// lists every row of layer_styles under Style → Load, so "what did the
	// scan look like" stays one click away instead of needing the ink_color
	// column and a hand-built renderer.
	w.SetStyleNamed(table, "as_printed", styleGeoUnitsAsPrinted(classes),
		"The sheet's own printed ink per unit", false)

	grow := math.Max(0.05, (l.maxx-l.minx)*0.03)
	ext := [4]float64{l.minx - grow, l.miny - grow, l.maxx + grow, l.maxy + grow}
	title := cat.Title + " (5MP)"
	if err := w.writeQGISProject(title, filepath.Base(path), []gpkgLayerSpec{{
		Table: table, Title: cat.Short + " units", Group: "Geology",
		Geometry: "Polygon", WKBType: "MultiPolygon", QML: qml, Visible: true, Opacity: 0.75,
	}}, ext); err != nil {
		slog.Warn("geomap gpkg project", "sheet", sheet, "err", err)
	}
	if err := w.Finish(); err != nil {
		return err
	}
	done = true
	return os.Rename(tmp, path)
}

// geoMapGPKGReady reports the cached file if it is newer than the units it was
// built from. Anything else (missing, stale) means "build on demand".
func geoMapGPKGReady(sheet string) (os.FileInfo, bool) {
	st, err := os.Stat(geoMapGPKGPath(sheet))
	if err != nil {
		return nil, false
	}
	src, err := os.Stat(geoMapUnitsPath(sheet))
	if err != nil {
		// Units gone (gitignored derived output can be cleaned) but a built
		// package survives: serve it. It is a snapshot of a real build, and
		// refusing it would be a worse answer than a slightly old one.
		return st, true
	}
	// >=, not >: the two files are usually written seconds apart, but a build
	// that finishes inside the same filesystem timestamp tick as its input
	// would otherwise rebuild on every single request forever. A re-vectorize
	// takes minutes, so equality means "built from this", never "stale".
	return st, !st.ModTime().Before(src.ModTime())
}

// HandleAPIGeoMapGeoPackage serves the sheet as a GeoPackage, building it on
// first request. One build at a time, globally: the two sheets together take a
// few seconds and a second concurrent request would only duplicate the work.
//
// Synchronous, deliberately — unlike the per-area export in gpkg_jobs.go. That
// one takes minutes over a live database and must therefore be a job with a
// progress card; this is a static file per sheet, so a job queue would add a
// notification, a poll and a card to a two-second wait.
func (s *Server) HandleAPIGeoMapGeoPackage(w http.ResponseWriter, r *http.Request) {
	sheet := r.PathValue("sheet")
	if geoMaps.load()[sheet] == nil {
		http.Error(w, "unknown sheet", http.StatusNotFound)
		return
	}
	path := geoMapGPKGPath(sheet)
	if _, ok := geoMapGPKGReady(sheet); !ok {
		geoMapGPKGMu.Lock()
		if _, ok := geoMapGPKGReady(sheet); !ok {
			t0 := time.Now()
			if err := buildGeoMapGeoPackage(sheet, path); err != nil {
				geoMapGPKGMu.Unlock()
				slog.Warn("geomap gpkg build failed", "sheet", sheet, "err", err)
				http.Error(w, "GeoPackage not available: "+err.Error(), http.StatusNotFound)
				return
			}
			slog.Info("geomap gpkg built", "sheet", sheet, "took", time.Since(t0))
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
