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

// styleGeoUnits paints each class in the ink measured off the scan, so the file
// opens looking like the sheet it came from. Categorized on `code`, which is
// the merged code ("GC2/GO") exactly as the tiles and the UI carry it — the
// sheet does not say which member a patch is, so neither may this.
func styleGeoUnits(classes []geoMapUnitProps) string {
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
		"w_<commodity> is an inference from lithology (1-3, 3 = classic host), not a record of any deposit."
	l, err := w.AddLayer(table, "MULTIPOLYGON", desc, cols)
	if err != nil {
		return err
	}

	classes := make([]geoMapUnitProps, 0, len(fc.Features))
	for _, f := range fc.Features {
		p := f.Properties
		classes = append(classes, p)
		byComm := map[string]geoMapAffinity{}
		whys := make([]string, 0, len(p.Affinity))
		for _, a := range p.Affinity {
			byComm[a.Commodity] = a
			whys = append(whys, fmt.Sprintf("%s (%d): %s", a.Commodity, a.Weight, a.Why))
		}
		vals := []interface{}{
			p.Code, gpkgStr(p.Name), gpkgStr(p.Group),
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
	w.SetStyle(table, qml, "Printed ink colour per unit")

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
