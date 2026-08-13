package srv

import (
	"archive/zip"
	"bytes"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	_ "modernc.org/sqlite"
)

// What makes the geology GeoPackage worth having over the MBTiles is that the
// units are queryable: typed area, and one weight column per commodity so
// `"w_gold" IS NOT NULL` is an exact filter rather than a LIKE over a joined
// string. These tests pin that, plus the two things that are invisible from
// inside Go — the file is a real GeoPackage, and the QGIS project inside it
// names the same container it lives in.

func writeTestSheet(t *testing.T, dir string) {
	t.Helper()
	units := map[string]any{
		"type": "FeatureCollection",
		"features": []any{
			map[string]any{
				"type": "Feature",
				"properties": map[string]any{
					"sheet": "tst", "code": "Au/Bx", "codes": []string{"Au", "Bx"},
					"name": "Schist belt", "group": "Precambrian", "color": "#a13c5f",
					"merged": true, "commodities": []string{"gold", "lithium"},
					"affinity": []any{
						map[string]any{"commodity": "gold", "weight": 3, "why": "Au: orogenic gold host"},
						map[string]any{"commodity": "lithium", "weight": 1, "why": "Bx: pegmatite affinity"},
					},
					"area_km2": 1234.5,
				},
				"geometry": map[string]any{
					"type":        "MultiPolygon",
					"coordinates": [][][][]float64{{{{10, 20}, {11, 20}, {11, 21}, {10, 21}, {10, 20}}}},
				},
			},
			map[string]any{
				"type": "Feature",
				"properties": map[string]any{
					"sheet": "tst", "code": "Qz", "name": "Quartzite", "group": "Precambrian",
					"color": "#cccccc", "area_km2": 10,
				},
				"geometry": map[string]any{
					"type":        "MultiPolygon",
					"coordinates": [][][][]float64{{{{12, 20}, {13, 20}, {13, 21}, {12, 21}, {12, 20}}}},
				},
			},
		},
	}
	b, _ := json.Marshal(units)
	if err := os.WriteFile(filepath.Join(dir, "tst_units.geojson"), b, 0o644); err != nil {
		t.Fatal(err)
	}
	cat, _ := json.Marshal(map[string]any{
		"sheet": "tst", "title": "Test sheet", "short": "Test", "year": 1964,
		"publisher": "Nobody", "scale": "1:1",
	})
	if err := os.WriteFile(filepath.Join(dir, "tst_classes.json"), cat, 0o644); err != nil {
		t.Fatal(err)
	}
}

// The package is built from the sheets this server has, so a test has to say
// which sheets exist — otherwise geoMapGPKGSheets() looks for sudan/car in a
// temp dir, finds nothing, and the staleness check has no inputs to compare.
func useTestSheets(t *testing.T, dir string, ids ...string) {
	t.Helper()
	oldDir, oldSheets := geoMaps.dir, geoMapSheets
	geoMaps.dir, geoMapSheets = dir, ids
	t.Cleanup(func() { geoMaps.dir, geoMapSheets = oldDir, oldSheets })
}

func TestGeoMapGeoPackageIsQueryableByCommodity(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst")
	writeTestSheet(t, dir)

	path := geoMapGPKGPath()
	if err := buildGeoMapGeoPackage(path, []string{"tst"}); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	// One weight column per commodity the sheet mentions, and only those: a
	// fixed column list would silently drop a commodity a re-vectorized sheet
	// introduced, or invent one it dropped.
	cols := map[string]string{}
	rows, err := db.Query(`SELECT name, type FROM pragma_table_info('geology_units')`)
	if err != nil {
		t.Fatal(err)
	}
	for rows.Next() {
		var n, ty string
		rows.Scan(&n, &ty)
		cols[n] = ty
	}
	rows.Close()
	for _, want := range []string{"w_gold", "w_lithium"} {
		if cols[want] != "INTEGER" {
			t.Errorf("%s type = %q, want INTEGER", want, cols[want])
		}
	}
	if _, ok := cols["w_uranium"]; ok {
		t.Error("w_uranium present: commodity columns must come from this sheet, not a fixed list")
	}
	if cols["area_km2"] != "REAL" {
		t.Errorf("area_km2 type = %q, want REAL (a string cannot be graduated or summed)", cols["area_km2"])
	}

	var n int
	if err := db.QueryRow(`SELECT COUNT(*) FROM geology_units WHERE "w_gold" IS NOT NULL`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Errorf("gold-hosting units = %d, want 1", n)
	}
	// A unit with no affinity at all must be NULL, not 0: 0 would read as
	// "measured, none", and would also match a `>= 0` filter.
	var w sql.NullInt64
	db.QueryRow(`SELECT "w_gold" FROM geology_units WHERE code='Qz'`).Scan(&w)
	if w.Valid {
		t.Error("a unit with no gold affinity must be NULL, not a number")
	}

	// The merged class keeps BOTH codes and is labelled with both — the sheet
	// does not say which member a patch is, so the export must not pick one.
	var code, codes, note string
	db.QueryRow(`SELECT code, codes, affinity_note FROM geology_units WHERE merged=1`).Scan(&code, &codes, &note)
	if code != "Au/Bx" || codes != "Au,Bx" {
		t.Errorf("merged class = %q / %q, want Au/Bx and Au,Bx", code, codes)
	}
	if !strings.Contains(note, "Au:") || !strings.Contains(note, "Bx:") {
		t.Errorf("affinity_note %q must attribute each reason to its member code", note)
	}

	if err := db.QueryRow(`SELECT COUNT(*) FROM layer_styles WHERE f_table_name='geology_units' AND useAsDefault=1`).Scan(&n); err != nil || n != 1 {
		t.Errorf("default style rows = %d (err %v), want 1", n, err)
	}
	// The project's datasource is relative to the file it lives in, so the
	// on-disk basename and the download name must be the same string.
	var content string
	if err := db.QueryRow(`SELECT content FROM qgis_projects`).Scan(&content); err != nil {
		t.Fatal(err)
	}
	if len(content) == 0 {
		t.Fatal("no embedded QGIS project")
	}
	if got := filepath.Base(path); got != "geology.gpkg" {
		t.Errorf("basename = %q, want geology.gpkg", got)
	}
}

// The cache is keyed on mtime, and the failure it guards against is a rebuilt
// sheet still serving the old polygons — the same stale-artefact trap as the
// tile ?v= revision.
func TestGeoMapGeoPackageCacheFollowsTheUnits(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst")
	writeTestSheet(t, dir)

	if _, ok := geoMapGPKGReady(); ok {
		t.Fatal("nothing built yet, must not report ready")
	}
	if err := buildGeoMapGeoPackage(geoMapGPKGPath(), []string{"tst"}); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); !ok {
		t.Fatal("freshly built package must be ready")
	}
	// Touch the units into the future = a re-vectorize.
	st, _ := os.Stat(geoMapGPKGPath())
	future := st.ModTime().Add(2 * 1e9)
	if err := os.Chtimes(geoMapUnitsPath("tst"), future, future); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); ok {
		t.Error("units newer than the package must invalidate it")
	}
}

// ---- one file, every sheet ------------------------------------------------
//
// The export used to be one GeoPackage per scanned sheet, which made the
// download mirror our storage rather than the user's question: rock does not
// stop at a border, and anyone intersecting units with a concession had to
// open two files and union two column sets by hand. These pin the two things
// that can silently go wrong once they share a layer.

func writeSecondTestSheet(t *testing.T, dir string) {
	t.Helper()
	units := map[string]any{
		"type": "FeatureCollection",
		"features": []any{
			// SAME code as the first sheet's quartzite, different rock and a
			// different age — which is the real case (Sudan's "S" is Silurian
			// sandstone, CAR's is a gold-bearing schist belt).
			map[string]any{
				"type": "Feature",
				"properties": map[string]any{
					"sheet": "two", "code": "Qz", "name": "Basalt flow",
					"group": "Neogene", "color": "#334455", "area_km2": 55,
					"commodities": []string{"copper"},
					"affinity": []any{
						map[string]any{"commodity": "copper", "weight": 2, "why": "basalt-hosted"},
					},
				},
				"geometry": map[string]any{
					"type":        "MultiPolygon",
					"coordinates": [][][][]float64{{{{20, 5}, {21, 5}, {21, 6}, {20, 6}, {20, 5}}}},
				},
			},
		},
	}
	b, _ := json.Marshal(units)
	if err := os.WriteFile(filepath.Join(dir, "two_units.geojson"), b, 0o644); err != nil {
		t.Fatal(err)
	}
	cat, _ := json.Marshal(map[string]any{
		"sheet": "two", "title": "Second sheet", "short": "Two", "year": 2004,
	})
	if err := os.WriteFile(filepath.Join(dir, "two_classes.json"), cat, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestGeoMapGeoPackageHoldsEverySheetInOneLayer(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst", "two")
	writeTestSheet(t, dir)
	writeSecondTestSheet(t, dir)

	path := geoMapGPKGPath()
	if err := buildGeoMapGeoPackage(path, geoMapGPKGSheets()); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	var n int
	if err := db.QueryRow(`SELECT COUNT(DISTINCT sheet) FROM geology_units`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 2 {
		t.Errorf("sheets in the package = %d, want 2 — a download short of a country reads as a country with no geology", n)
	}
	// The commodity columns are the UNION over sheets, so the headline filter
	// answers across the whole area rather than per file.
	for _, want := range []string{"w_gold", "w_copper"} {
		var c int
		db.QueryRow(`SELECT COUNT(*) FROM pragma_table_info('geology_units') WHERE name=?`, want).Scan(&c)
		if c != 1 {
			t.Errorf("missing %s: commodity columns must be the union over every sheet", want)
		}
	}
	// A code is unique only WITHIN a sheet. If the legend keyed on `code`,
	// two sheets' "Qz" would share one symbol and half a country would be
	// dated from the other's legend.
	if err := db.QueryRow(`SELECT COUNT(*) FROM geology_units WHERE code='Qz'`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 2 {
		t.Fatalf("expected the same code on both sheets, got %d rows", n)
	}
	rows, err := db.Query(`SELECT key FROM geology_units WHERE code='Qz' ORDER BY key`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var keys []string
	for rows.Next() {
		var k string
		rows.Scan(&k)
		keys = append(keys, k)
	}
	if len(keys) != 2 || keys[0] == keys[1] {
		t.Errorf("keys = %v, want one per (sheet, code)", keys)
	}
	var qml string
	if err := db.QueryRow(`SELECT styleQML FROM layer_styles WHERE useAsDefault=1`).Scan(&qml); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(qml, `attr="key"`) {
		t.Error(`the categorised renderer must key on "key" (sheet, code), not on code`)
	}
	for _, k := range keys {
		if !strings.Contains(qml, `value="`+k+`"`) {
			t.Errorf("legend has no category for %q", k)
		}
	}
}

// Adding a sheet must invalidate the cache even though nothing the package was
// already built from changed. mtime alone cannot see this: the new sheet's
// units file can legitimately be OLDER than the package (a restore, a copy
// that preserved timestamps), and the user would then download a country short
// of what the map draws — a no-op reading as an answer.
func TestGeoMapGeoPackageCacheNoticesANewSheet(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst", "two")
	writeTestSheet(t, dir)

	if err := buildGeoMapGeoPackage(geoMapGPKGPath(), geoMapGPKGSheets()); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); !ok {
		t.Fatal("freshly built package must be ready")
	}
	writeSecondTestSheet(t, dir)
	old := time.Now().Add(-72 * time.Hour)
	if err := os.Chtimes(geoMapUnitsPath("two"), old, old); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); ok {
		t.Error("a sheet the package does not contain must invalidate it, even if its units are older")
	}
}

// ---- the ornament, as QGIS actually draws it -------------------------------
//
// These pin what RENDERING the file taught us (2026-08-12,
// scripts/geomaps/render_gpkg.py; findings in docs/GEOLOGY.md). Every
// one of them describes a way an ornament came out WRONG while the XML we
// wrote was exactly what we intended — which is why the byte-level test above
// could not see any of it, and why nine families shipped as six.

// THE ONE THAT COST THE MOST. QGIS renders a LinePatternFill by building a
// small repeating tile and stamping it; on an axis-aligned angle (0/90/180/270)
// the tile is only as long as the line spacing, so a custom dash whose period
// exceeds the spacing is clipped to nothing or to a solid rule. Measured on
// 3.34.4: the carbonate brick's vertical course (dash 2;6 at 90°, spacing
// 3.6 mm) rendered ZERO pixels, so "bricks" was flat horizontal rules and read
// as mudrock. `use_custom_dash` was set, and set on the LINE layer, which is
// the trap already documented — it was right, and it still did nothing.
//
// The rule that survives it, measured rather than reasoned: the dash period
// must not exceed 1.5x the line spacing. Every ornament at or below that ratio
// rendered as a correct dash at every angle we ship; everything at 1.76x and
// above came out blank or solid. The bound is applied at every angle, not only
// the axis-aligned ones, because the same clipping bites off-axis for a long
// enough period and a per-angle exception is a rule nobody will remember.
func TestGeoOrnamentDashesSurviveTheQGISPatternTile(t *testing.T) {
	// Measured on QGIS 3.34.4 (see the ratio table in
	// docs/GEOLOGY.md). Ratios of 1.00-1.50 rendered correctly;
	// 1.76, 2.17, 2.22, 2.31, 3.12, 3.24 and 3.27 all rendered blank or solid.
	const maxRatio = 1.5
	for lith, layers := range geoOrnaments {
		for i, l := range layers {
			if l.marker != "" || l.dash == "" {
				continue
			}
			spacing, err := strconv.ParseFloat(l.b, 64)
			if err != nil {
				t.Errorf("%s[%d]: spacing %q unparseable", lith, i, l.b)
				continue
			}
			period := 0.0
			for _, seg := range strings.Split(l.dash, ";") {
				v, err := strconv.ParseFloat(seg, 64)
				if err != nil {
					t.Errorf("%s[%d]: dash segment %q unparseable", lith, i, seg)
					continue
				}
				period += v
			}
			if period > spacing*maxRatio+1e-9 {
				t.Errorf("%s[%d]: dash %q has period %.2f mm, %.2fx the line spacing "+
					"%.2f mm (max %.2fx). QGIS clips the dash to its pattern tile and "+
					"this ornament will render SOLID or BLANK, not dashed. Shorten the "+
					"dash or widen the spacing, then re-render and LOOK at it: "+
					"QT_QPA_PLATFORM=offscreen python3 scripts/geomaps/render_gpkg.py",
					lith, i, l.dash, period, period/spacing, spacing, maxRatio)
			}
		}
	}
}

// Nine families exist to be TOLD APART. Two lithologies drawn with the same
// angle, spacing and dash are one family wearing two names — and because the
// fill colour is the age, two units of the same age and different rock would
// then be pixel-identical. (This is how the old volcanic ornament, a dashed
// 45° hatch, was indistinguishable from `mixed`.)
func TestGeoOrnamentFamiliesAreDistinct(t *testing.T) {
	seen := map[string]string{}
	for _, l := range geoLithologies {
		orn := geoOrnamentOf(l.Key)
		if len(orn) == 0 {
			t.Errorf("%s: no ornament — it would render as a flat colour, i.e. "+
				"as something that is not the geology layer", l.Key)
			continue
		}
		key := fmt.Sprint(orn)
		if prev, dup := seen[key]; dup {
			t.Errorf("%s and %s have the SAME ornament (%v): at one age they are "+
				"the same pixels", l.Key, prev, orn)
		}
		seen[key] = l.Key
	}
	// `mixed` means "the sheet does not say" and must stay QUIET: it must not
	// out-shout a family that does say something. Ink coverage cannot be
	// computed from the table — a marker's coverage and a dashed hatch's are
	// not the same arithmetic, and the first attempt at a formula here ranked
	// `mixed` above the stipples, which the render flatly contradicts. So these
	// are MEASURED, off the render, at 96 dpi:
	//
	//	QT_QPA_PLATFORM=offscreen python3 scripts/geomaps/render_gpkg.py
	//
	// If you change a spacing, re-render and update the number. A stale number
	// here is exactly the failure this whole exercise was about, so the check
	// below is deliberately loose: it asserts the ORDERING that matters, not
	// the values.
	inkPct := map[string]float64{
		"sandstone": 5, "mixed": 6, "volcanic": 8, "alluvium": 9,
		"ironstone": 12, "metamorphic": 15, "mudrock": 17,
		"intrusive": 18, "carbonate": 26, "ultramafic": 33,
	}
	for _, l := range geoLithologies {
		if _, ok := inkPct[l.Key]; !ok {
			t.Errorf("%s has no measured ink coverage — render it and record it, "+
				"do not guess", l.Key)
		}
	}
	// Below the median: quieter than most, without pretending an ornament that
	// must remain VISIBLE can be the faintest thing on the sheet.
	louder := 0
	for k, v := range inkPct {
		if k != "mixed" && v > inkPct["mixed"] {
			louder++
		}
	}
	if louder < len(inkPct)/2 {
		t.Errorf("`mixed` is louder than half the families (%d of %d are louder): "+
			"the ornament meaning 'the sheet does not say' must not dominate the map",
			louder, len(inkPct)-1)
	}
	// And it must still be visible: an invisible "mixed" is a flat colour, i.e.
	// a polygon that does not read as the geology layer at all.
	if inkPct["mixed"] < 2 {
		t.Error("`mixed` is too faint to read as an ornament")
	}
}

// A sub-symbol name must be "@<parent>@<n>" and UNIQUE within its parent: two
// layers called @3@1 make QGIS drop one, and a cross-hatch then renders as half
// of itself. Already documented, never checked against every family.
func TestGeoOrnamentSubSymbolNamesAreUnique(t *testing.T) {
	for _, l := range geoLithologies {
		xml := qmlGeoUnitSymbol("7", "200,100,50", l.Key)
		names := map[string]int{}
		for _, part := range strings.Split(xml, `name="@`)[1:] {
			names["@"+part[:strings.Index(part, `"`)]]++
		}
		for n, c := range names {
			if c > 1 {
				t.Errorf("%s: sub-symbol %q appears %d times; QGIS keeps one and "+
					"the ornament renders as part of itself", l.Key, n, c)
			}
		}
		if len(names) != len(geoOrnamentOf(l.Key)) {
			t.Errorf("%s: %d sub-symbols for %d ornament layers",
				l.Key, len(names), len(geoOrnamentOf(l.Key)))
		}
		// A stroke-only marker (a plus, a cross) has no interior, so a fill
		// colour alone leaves it INVISIBLE — it must carry the stroke.
		for _, o := range geoOrnamentOf(l.Key) {
			if o.marker == "cross" || o.marker == "cross2" {
				if !strings.Contains(xml, `"outline_style" type="QString" value="solid"`) {
					t.Errorf("%s: a %q marker needs a stroke or it renders as nothing",
						l.Key, o.marker)
				}
			}
		}
	}
}

// Contacts are drawn in a darkened ink, never black and never the unit's own
// colour: 46 classes outlined at full saturation is a net of bright magenta
// over a country, and black reads as a political border. Confirmed by looking
// at the render.
func TestGeoHatchInkIsDarkenedNotBlack(t *testing.T) {
	for _, rgb := range []string{"249,249,127", "240,4,127", "52,178,201", "189,189,189"} {
		got := geoHatchInk(rgb)
		if got == rgb {
			t.Errorf("%s: ink equals the fill — the ornament would be invisible", rgb)
		}
		var r, g, b int
		fmt.Sscanf(got, "%d,%d,%d", &r, &g, &b)
		if r+g+b == 0 {
			t.Errorf("%s -> black: reads as a border, not as an ornament", rgb)
		}
		var fr, fg, fb int
		fmt.Sscanf(rgb, "%d,%d,%d", &fr, &fg, &fb)
		if r > fr || g > fg || b > fb {
			t.Errorf("%s -> %s is not darker than its fill", rgb, got)
		}
	}
}

// A vector sheet ships the survey's OWN polygons, so one class is many rows
// (Tanzania: 596 rows, 41 classes). The QGIS legend is per class, and this was
// invisible while both sheets were scans: the vectorizer dissolves each class
// to one multipart row, so rows and classes coincided and a per-row legend
// looked correct. On a WFS sheet it lists "aQ - Predominantly alluvial and
// eluvial sediments" 89 times — the same symbol, keyed identically, repeated
// until the legend is unusable.
func TestGeoPackageLegendIsPerClassNotPerPolygon(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "many")

	// Three polygons, two classes: exactly the shape a per-feature legend
	// cannot tell from three classes.
	feat := func(code, name string, x float64) map[string]any {
		return map[string]any{
			"type": "Feature",
			"properties": map[string]any{
				"sheet": "many", "code": code, "name": name,
				"group": "Quaternary", "color": "#cccccc", "area_km2": 5,
			},
			"geometry": map[string]any{
				"type": "MultiPolygon",
				"coordinates": [][][][]float64{{{
					{x, 20}, {x + 1, 20}, {x + 1, 21}, {x, 21}, {x, 20}}}},
			},
		}
	}
	b, _ := json.Marshal(map[string]any{"type": "FeatureCollection", "features": []any{
		feat("aQ", "Alluvium", 10), feat("aQ", "Alluvium", 12), feat("mK", "Sandstone", 14),
	}})
	if err := os.WriteFile(filepath.Join(dir, "many_units.geojson"), b, 0o644); err != nil {
		t.Fatal(err)
	}
	cat, _ := json.Marshal(map[string]any{"sheet": "many", "title": "Many", "year": 2015})
	if err := os.WriteFile(filepath.Join(dir, "many_classes.json"), cat, 0o644); err != nil {
		t.Fatal(err)
	}

	path := geoMapGPKGPath()
	if err := buildGeoMapGeoPackage(path, geoMapGPKGSheets()); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	// Every polygon is still a row: the export must not dissolve the survey's
	// own geometry to make its legend tidy.
	var rows int
	if err := db.QueryRow(`SELECT COUNT(*) FROM geology_units`).Scan(&rows); err != nil {
		t.Fatal(err)
	}
	if rows != 3 {
		t.Errorf("rows = %d, want 3 — every source polygon is a feature", rows)
	}
	var qml string
	if err := db.QueryRow(`SELECT styleQML FROM layer_styles WHERE useAsDefault=1`).Scan(&qml); err != nil {
		t.Fatal(err)
	}
	// 2 classes + the trailing "other" catch-all.
	if got := strings.Count(qml, "<category "); got != 3 {
		t.Errorf("legend categories = %d, want 3 (2 classes + other); a duplicated "+
			"category is one symbol listed twice, i.e. a legend that cannot be read", got)
	}
}

// ---- the FILTERED export: the view, its hairlines, and the evidence --------
//
// The download in the geology panel's bar builds what the reader is LOOKING AT
// (srv/geomap_gpkg.go, geoMapSelection): the unit keys and contact pairs the
// client is painting. Three failures are specific to that path and none of them
// is visible from the whole-catalogue tests above.

// writeTestContacts gives a sheet the contacts file scripts/geomaps/contacts.py
// produces. Two junctions whose lithologies the rule table actually grades, so
// `grade` is a measured word rather than "ungraded" everywhere.
func writeTestContacts(t *testing.T, dir, sheet string, pairs ...[2]string) {
	t.Helper()
	feats := []any{}
	x := 10.0
	for _, p := range pairs {
		feats = append(feats, map[string]any{
			"type": "Feature",
			"properties": map[string]any{
				"sheet": sheet, "code_a": p[0], "code_b": p[1],
				"pair": p[0] + "|" + p[1], "km": 12.5,
			},
			"geometry": map[string]any{
				"type":        "MultiLineString",
				"coordinates": [][][]float64{{{x, 20}, {x + 0.5, 20.5}}},
			},
		})
		x += 1
	}
	b, _ := json.Marshal(map[string]any{"type": "FeatureCollection", "features": feats})
	if err := os.WriteFile(geoContactsGeoJSONPath(sheet), b, 0o644); err != nil {
		t.Fatal(err)
	}
}

// The anchors are read once, process-wide, from a path derived from the sheet
// directory. A test that wants them present (or absent) has to say so, and has
// to put the once back — otherwise the first test to touch them decides for
// every later one.
//
// A sync.Once is RESTORED BY REPLAYING IT, never by copying it: a Once holds a
// noCopy and `go vet` fails the assignment. "Already loaded" is reproduced by
// running a fresh Once with an empty function — the same state, without the
// copy.
func useTestAnchors(t *testing.T, dir string, n int) {
	t.Helper()
	oldDoc, oldErr := geoAnchors, geoAnchorErr
	oldLoaded := oldDoc != nil || oldErr != nil
	geoAnchorOnce, geoAnchors, geoAnchorErr = sync.Once{}, nil, nil
	t.Cleanup(func() {
		geoAnchorOnce, geoAnchors, geoAnchorErr = sync.Once{}, oldDoc, oldErr
		if oldLoaded {
			geoAnchorOnce.Do(func() {})
		}
	})
	if n <= 0 {
		return
	}
	if err := os.MkdirAll(filepath.Dir(geoAnchorFile()), 0o755); err != nil {
		t.Fatal(err)
	}
	feats := make([]any, 0, n)
	for i := 0; i < n; i++ {
		feats = append(feats, map[string]any{
			"type": "Feature",
			"properties": map[string]any{
				"source": "testlist", "source_id": fmt.Sprintf("t%d", i),
				"source_url": "https://example.invalid/t", "iso3": "CAF",
				"year": 2019, "resource": []string{"gold", "diamond"}[i%2],
				"observed": "2019", "licence": "CC-BY", "terms": "open",
				"attribution": "Test list",
			},
			// Deliberately far from the units above (which sit at 10-13 E,
			// 20-21 N): an anchor outside the sheets must still ship.
			"geometry": map[string]any{"type": "Point", "coordinates": []float64{30 + float64(i), -5}},
		})
	}
	b, _ := json.Marshal(map[string]any{
		"notice":   "test",
		"sources":  []any{map[string]any{"source": "testlist", "label": "Test list", "terms": "open", "sites": n}},
		"features": feats,
	})
	if err := os.WriteFile(geoAnchorFile(), b, 0o644); err != nil {
		t.Fatal(err)
	}
}

// A SELECTION THAT MATCHES NOTHING IS A BROKEN FILTER, NOT AN EMPTY MAP.
// The client sends the same key set it paints, so an empty match means the two
// have drifted (a re-vectorize between page load and click, a hand-made
// request). A GeoPackage with one empty layer is indistinguishable from a
// country with no geology — invariant 1, and the reason the handler answers 409
// rather than serving a file.
func TestGeoMapViewExportRefusesASelectionThatMatchesNothing(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst")
	useTestAnchors(t, dir, 0)
	writeTestSheet(t, dir)

	path := filepath.Join(dir, "view.gpkg")
	sel := &geoMapSelection{Units: []string{"tst:NotACode", "car:Au"}, Label: "gold hosts"}
	err := buildGeoMapGeoPackageSel(path, []string{"tst"}, sel)
	if err == nil {
		t.Fatal("a selection matching no unit must be an ERROR; an empty layer " +
			"reads as a country with no geology")
	}
	if !strings.Contains(err.Error(), "reload") {
		t.Errorf("error %q must tell the reader what to do — a stale selection is "+
			"the usual cause and reloading is the fix", err)
	}
	if _, e := os.Stat(path); e == nil {
		t.Error("a refused build must leave no file: a half-written .gpkg served " +
			"once is the truncation trap")
	}

	// And the same selection with ONE real key is fine — otherwise the check
	// above would pass on a build that simply never works.
	if err := buildGeoMapGeoPackageSel(path, []string{"tst"},
		&geoMapSelection{Units: []string{"tst:Qz", "tst:NotACode"}, Label: "quartzite"}); err != nil {
		t.Fatalf("a selection with one real key must build: %v", err)
	}
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var n int
	db.QueryRow(`SELECT COUNT(*) FROM geology_units`).Scan(&n)
	if n != 1 {
		t.Errorf("filtered units = %d, want 1", n)
	}
	// The commodity columns describe THE FILE. Qz has no affinity at all, so a
	// w_gold column here would claim gold was considered and found absent in a
	// file built from the one unit that says nothing about it.
	var c int
	db.QueryRow(`SELECT COUNT(*) FROM pragma_table_info('geology_units') WHERE name='w_gold'`).Scan(&c)
	if c != 0 {
		t.Error("a filtered export's w_<commodity> columns must be derived AFTER " +
			"the selection, or they describe units the file does not contain")
	}
	// A FILTERED EXPORT MUST ANNOUNCE ITSELF: two files with one name and
	// different contents is the truncation trap (invariant 8).
	var desc string
	db.QueryRow(`SELECT description FROM gpkg_contents WHERE table_name='geology_units'`).Scan(&desc)
	if !strings.Contains(desc, "A VIEW") || !strings.Contains(desc, "quartzite") {
		t.Errorf("layer description %q must say it is a view and say which one", desc)
	}
	// TWO SURFACES OF ONE WORD (invariant 7). The panel counts CLASSES, the
	// layer holds POLYGONS, and on a vector sheet those differ (104 vs 659 for
	// one real view). Both numbers must be there and both must name their unit,
	// or the file reads as not matching the picture it came from.
	if !strings.Contains(desc, "map unit(s)") || !strings.Contains(desc, "polygon(s)") {
		t.Errorf("description %q must name BOTH counts and their units: a class "+
			"count over a layer of polygons reads as a mismatch", desc)
	}
	if strings.Contains(desc, geoRowsToken) {
		t.Error("the polygon count placeholder survived into the file")
	}
}

// THE EVIDENCE IS NOT FILTERED. A file in which every anchor agrees with the
// layer is a picture of our own filter and reads as a prediction that came
// true; `resource` is a column, so the reader narrows it and knows THEY did.
// The anchors also sit outside the sheets on purpose here — an anchor beyond the
// cutline is exactly the kind of row that shows a reader where the evidence
// stops.
func TestGeoMapViewExportShipsEveryAnchorRegardlessOfTheSelection(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst")
	useTestAnchors(t, dir, 7)
	writeTestSheet(t, dir)

	count := func(path string) (units, anchors int, resources int) {
		db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
		if err != nil {
			t.Fatal(err)
		}
		defer db.Close()
		db.QueryRow(`SELECT COUNT(*) FROM geology_units`).Scan(&units)
		db.QueryRow(`SELECT COUNT(*) FROM mining_anchors`).Scan(&anchors)
		db.QueryRow(`SELECT COUNT(DISTINCT resource) FROM mining_anchors`).Scan(&resources)
		return
	}

	whole := filepath.Join(dir, "whole.gpkg")
	if err := buildGeoMapGeoPackage(whole, []string{"tst"}); err != nil {
		t.Fatal(err)
	}
	wu, wa, _ := count(whole)

	// A gold-only view: one unit of two, and the same 7 anchors — including the
	// diamond ones, which the reader's filter says nothing about.
	view := filepath.Join(dir, "view.gpkg")
	if err := buildGeoMapGeoPackageSel(view, []string{"tst"},
		&geoMapSelection{Units: []string{"tst:Au/Bx"}, Label: "gold hosts"}); err != nil {
		t.Fatal(err)
	}
	vu, va, vres := count(view)

	if vu >= wu {
		t.Fatalf("the view holds %d units and the catalogue %d — the filter did nothing, "+
			"so this test proves nothing about the anchors", vu, wu)
	}
	if va != wa || va != 7 {
		t.Errorf("anchors: view %d, catalogue %d, file %d — the evidence must ship WHOLE. "+
			"An anchor layer that agrees with the commodity filter is a picture of "+
			"our own filter, not a check on it", va, wa, 7)
	}
	if vres < 2 {
		t.Errorf("distinct resources in a gold view = %d, want both — `resource` is a "+
			"column so the READER narrows it and knows they did", vres)
	}
}

// A SERVER WITHOUT THE ANCHOR FILE SAYS SO. "We could not ship it" and "nothing
// was ever checked" are different statements (srv/geomap_gpkg_layers.go), and
// the project title is where a reader looks.
func TestGeoMapExportNamesMissingAnchorsInsteadOfOmittingThem(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst")
	useTestAnchors(t, dir, 0) // not installed
	writeTestSheet(t, dir)

	path := filepath.Join(dir, "no-anchors.gpkg")
	if err := buildGeoMapGeoPackage(path, []string{"tst"}); err != nil {
		t.Fatalf("a missing anchor file must not fail the export: %v", err)
	}
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var n int
	db.QueryRow(`SELECT COUNT(*) FROM gpkg_contents WHERE table_name='mining_anchors'`).Scan(&n)
	if n != 0 {
		t.Error("no anchor file, yet an anchor layer exists")
	}
	// The project's NAME, not its content: the content is a hex-encoded gzip of
	// the .qgs, so a substring search over it would pass or fail for reasons
	// that have nothing to do with the title.
	var title string
	if err := db.QueryRow(`SELECT name FROM qgis_projects`).Scan(&title); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(title, "no reference workings installed") {
		t.Error("the project title must NAME the absent evidence; an unexplained " +
			"omission reads as a model nobody checked")
	}
}

// The contact layer follows the SWITCH, not the geometry.
//
// `pairs` empty on a real selection means the reader had the junction layer off,
// which is a different statement from "every contact" — a contacts-off view must
// not ship the whole hairline set. And a selection whose pairs match no line is
// the same broken filter as a selection with no units: the reader can see
// hundreds of lines on screen and would receive an empty layer.
func TestGeoMapViewExportContactsFollowTheReadersSwitch(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst")
	useTestAnchors(t, dir, 0)
	writeTestSheet(t, dir)
	// Au/Bx resolves to a schist belt (metamorphic), Qz to quartzite; the pair
	// is whatever the lithology index makes of them — the grading is asserted
	// below from the rule table rather than assumed.
	writeTestContacts(t, dir, "tst", [2]string{"Au/Bx", "Qz"})

	has := func(path string) (bool, int) {
		db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
		if err != nil {
			t.Fatal(err)
		}
		defer db.Close()
		var listed int
		db.QueryRow(`SELECT COUNT(*) FROM gpkg_contents WHERE table_name='geology_contacts'`).Scan(&listed)
		if listed == 0 {
			return false, 0
		}
		var n int
		db.QueryRow(`SELECT COUNT(*) FROM geology_contacts`).Scan(&n)
		return true, n
	}

	// (1) The whole catalogue takes every line: nil selection, not an empty one.
	whole := filepath.Join(dir, "whole.gpkg")
	if err := buildGeoMapGeoPackage(whole, []string{"tst"}); err != nil {
		t.Fatal(err)
	}
	if ok, n := has(whole); !ok || n != 1 {
		t.Errorf("catalogue: contacts layer=%v rows=%d, want 1 line", ok, n)
	}

	// (2) Junctions OFF: units selected, no pairs. No layer at all — not an
	// empty one, which QGIS would show as a checkbox over nothing.
	off := filepath.Join(dir, "off.gpkg")
	if err := buildGeoMapGeoPackageSel(off, []string{"tst"},
		&geoMapSelection{Units: []string{"tst:Au/Bx", "tst:Qz"}, Label: "rocks only"}); err != nil {
		t.Fatal(err)
	}
	if ok, _ := has(off); ok {
		t.Error("a contacts-OFF view must ship no contact layer: an empty selection " +
			"is not 'every contact'")
	}

	// (3) Junctions ON, one pair: that line, and its grade in the app's own
	// vocabulary rather than a number.
	on := filepath.Join(dir, "on.gpkg")
	if err := buildGeoMapGeoPackageSel(on, []string{"tst"}, &geoMapSelection{
		Units: []string{"tst:Au/Bx", "tst:Qz"},
		Pairs: []string{"tst:Au/Bx|Qz"},
		Label: "gold hosts, graded junctions",
	}); err != nil {
		t.Fatal(err)
	}
	if ok, n := has(on); !ok || n != 1 {
		t.Fatalf("junctions ON: layer=%v rows=%d, want 1", ok, n)
	}
	db, err := sql.Open("sqlite", "file:"+on+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var grade, litha, lithb string
	var weight sql.NullInt64
	db.QueryRow(`SELECT grade, lith_a, lith_b, weight FROM geology_contacts`).Scan(&grade, &litha, &lithb, &weight)
	want := geoContactGrade(0)
	if rules := geoContactRuleIndex[geoLithPairKey(litha, lithb)]; len(rules) > 0 {
		want = geoContactGrade(rules[0].Weight)
	}
	if grade != want {
		t.Errorf("grade = %q, want %q for a %s|%s junction — the export and the map "+
			"read the SAME rule table", grade, want, litha, lithb)
	}
	// `ungraded` is "the model says nothing", not weight 0: a number there would
	// be a measurement nobody made (invariant 12).
	if grade == "ungraded" && weight.Valid {
		t.Error("an ungraded junction must carry NULL, not 0 — 0 is a measurement")
	}

	// (4) Pairs that match nothing: the reader is looking at lines and would
	// receive an empty layer. Same refusal as a unit selection that misses.
	bad := filepath.Join(dir, "bad.gpkg")
	err = buildGeoMapGeoPackageSel(bad, []string{"tst"}, &geoMapSelection{
		Units: []string{"tst:Qz"},
		Pairs: []string{"tst:Nope|AlsoNope"},
		Label: "stale",
	})
	if err == nil {
		t.Error("a pair selection matching no line must be an ERROR, not an empty layer")
	}
}

// The stamp watches EVERY input, not only the units.
//
// A contacts file re-derived after the package was built leaves units that are
// still correct and hairlines that are a generation old — the same staleness as
// the ?v= tile revision, one layer down, and invisible precisely because the
// polygons in it are right. Same for the anchors.
func TestGeoMapGPKGStampCoversContactsAndAnchors(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst")
	useTestAnchors(t, dir, 3)
	writeTestSheet(t, dir)
	writeTestContacts(t, dir, "tst", [2]string{"Au/Bx", "Qz"})

	if err := buildGeoMapGeoPackage(geoMapGPKGPath(), []string{"tst"}); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); !ok {
		t.Fatal("freshly built package must be ready")
	}

	// (1) A rewritten contacts file. Written with a FUTURE mtime for the same
	// reason the units test does: a re-derivation seconds after the build would
	// otherwise be indistinguishable from the build itself at 1 s resolution.
	writeTestContacts(t, dir, "tst", [2]string{"Au/Bx", "Qz"}, [2]string{"Qz", "Au/Bx"})
	future := time.Now().Add(2 * time.Hour)
	if err := os.Chtimes(geoContactsGeoJSONPath("tst"), future, future); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); ok {
		t.Error("a re-derived contacts file must invalidate the package: the units " +
			"in it would still be right, which is what makes this invisible")
	}

	// Rebuild, then (2) a rewritten anchor file.
	if err := buildGeoMapGeoPackage(geoMapGPKGPath(), []string{"tst"}); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); !ok {
		t.Fatal("rebuilt package must be ready")
	}
	if err := os.Chtimes(geoAnchorFile(), future, future); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); ok {
		t.Error("a rewritten anchor file must invalidate the package")
	}

	// (3) A DELETED contacts file changes what the package should hold too. An
	// input recorded as absent is the point: skipping it would leave the old
	// package looking fresh.
	if err := buildGeoMapGeoPackage(geoMapGPKGPath(), []string{"tst"}); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); !ok {
		t.Fatal("rebuilt package must be ready")
	}
	if err := os.Remove(geoContactsGeoJSONPath("tst")); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); ok {
		t.Error("a contacts file that DISAPPEARED must invalidate the package")
	}
}

// The download name is what a folder of three downloads reads as. It is derived
// from the reader's own words for the view, and it must never collide with the
// cached whole-catalogue file — two files with one name and different contents
// is the trap this whole path is arranged around.
func TestGeoViewFileNameIsReadableAndNeverTheCatalogues(t *testing.T) {
	for _, c := range []struct{ in, want string }{
		{"gold hosts, likely+", "geology-gold-hosts-likely.gpkg"},
		{"gold hosts on CAR: classic junctions", "geology-gold-hosts-on-car-classic-junctions.gpkg"},
		{"", "geology-view.gpkg"},
		{"   ", "geology-view.gpkg"},
		{"////", "geology-view.gpkg"},
	} {
		if got := geoViewFileName(c.in); got != c.want {
			t.Errorf("geoViewFileName(%q) = %q, want %q", c.in, got, c.want)
		}
	}
	// A 200-character label must not produce a 200-character filename, and must
	// not end in a dash.
	long := geoViewFileName(strings.Repeat("gold hosts ", 40))
	if len(long) > 64 || strings.Contains(long, "--") || strings.HasSuffix(long, "-.gpkg") {
		t.Errorf("long label -> %q", long)
	}
	if geoViewFileName("") == filepath.Base(geoMapGPKGPath()) {
		t.Error("a view file must never be named geology.gpkg: the cached catalogue " +
			"lives under that name and the two have different contents")
	}
}

// The disclaimer must survive the route we tell people to use.
//
// Every layer's description says what it is not ("an inference ... NOT a record
// of any deposit", "OTHER ORGANISATIONS' observations", "a VIEW rather than the
// catalogue"). GDAL surfaces gpkg_contents.description as the layer abstract —
// but the embedded QGIS project's <maplayer> supplies its own metadata, and an
// ABSENT <resourceMetadata> does not fall back to the provider's: it blanks it.
// Rendering the file through its own project (scripts/geomaps/render_gpkg.py)
// printed "abstract: (none)" for all three layers while ogrinfo printed the
// paragraph, which is exactly the shape of bug that pass exists to catch.
func TestGeoMapProjectCarriesEachLayersDisclaimer(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst")
	useTestAnchors(t, dir, 3)
	writeTestSheet(t, dir)
	writeTestContacts(t, dir, "tst", [2]string{"Au/Bx", "Qz"})

	path := filepath.Join(dir, "geology.gpkg")
	if err := buildGeoMapGeoPackage(path, []string{"tst"}); err != nil {
		t.Fatal(err)
	}
	qgs := readProjectXML(t, path)
	for _, want := range []struct{ table, phrase string }{
		{"geology_units", "not a record of any deposit"},
		{"geology_contacts", "NOT a record of any deposit"},
		{"mining_anchors", "OTHER ORGANISATIONS"},
	} {
		if !strings.Contains(qgs, "<identifier>"+want.table+"</identifier>") {
			t.Errorf("%s has no <resourceMetadata> in the embedded project: QGIS shows "+
				"an empty abstract, and the abstract is where the disclaimer lives", want.table)
			continue
		}
		if !strings.Contains(qgs, want.phrase) && !strings.Contains(qgs,
			strings.ReplaceAll(want.phrase, "'", "&#39;")) {
			t.Errorf("%s's abstract in the project does not carry %q", want.table, want.phrase)
		}
	}
}

// Nine lists, nine distinguishable dots — and none of them the fallback.
//
// The anchors land on a saturated FGDC pattern fill covering the whole canvas,
// so this layer's contrast budget is not the export's usual one. Two ways the
// first palette failed it, both invisible in an XML assertion: source #1 was
// pure white (a white dot on a pale unit fill is nothing), and source #9 was
// byte-identical to the "other" fallback, so the ninth real list and the
// catch-all were one symbol in the legend.
func TestGeoAnchorSymbolsAreDistinctVisibleAndNotTheFallback(t *testing.T) {
	srcs := []string{"a", "b", "c", "d", "e", "f", "g", "h", "i"}
	qml := styleGeoAnchors(srcs)

	re := regexp.MustCompile(`<Option name="color" type="QString" value="([0-9,]+)"/>`)
	m := re.FindAllStringSubmatch(qml, -1)
	if len(m) != len(srcs)+1 {
		t.Fatalf("want %d symbols (%d sources + fallback), got %d", len(srcs)+1, len(srcs), len(m))
	}
	seen := map[string]int{}
	for i, g := range m {
		c := g[1]
		if j, dup := seen[c]; dup {
			t.Errorf("symbol %d repeats symbol %d (%s): two lists, or a list and the "+
				"fallback, reading as one provenance", i, j, c)
		}
		seen[c] = i
		var r, gg, b, a int
		if n, _ := fmt.Sscanf(c, "%d,%d,%d,%d", &r, &gg, &b, &a); n < 3 {
			t.Fatalf("unparseable colour %q", c)
		}
		if r > 235 && gg > 235 && b > 235 {
			t.Errorf("symbol %d is %s — near-white, which disappears into a pale unit "+
				"fill and onto paper; the anchors are the evidence layer", i, c)
		}
	}
	// The outline must be opaque: the shared default is 0,0,0,80, a 69%
	// transparent hairline that dissolves into a cross-hatch.
	for _, g := range regexp.MustCompile(
		`<Option name="outline_color" type="QString" value="([0-9,]+)"/>`).FindAllStringSubmatch(qml, -1) {
		p := strings.Split(g[1], ",")
		if len(p) == 4 {
			if a, _ := strconv.Atoi(p[3]); a < 200 {
				t.Errorf("anchor outline %s is translucent; over a pattern fill that is no outline", g[1])
			}
		}
	}
}

// readProjectXML unhexes and unzips the embedded .qgz back to its .qgs text.
// The project is stored the way QGIS stores it (hex of a zip), so any test
// asserting what the project SAYS has to go the same way back.
func readProjectXML(t *testing.T, path string) string {
	t.Helper()
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var content string
	if err := db.QueryRow(`SELECT content FROM qgis_projects`).Scan(&content); err != nil {
		t.Fatal(err)
	}
	raw, err := hex.DecodeString(content)
	if err != nil {
		t.Fatal(err)
	}
	zr, err := zip.NewReader(bytes.NewReader(raw), int64(len(raw)))
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range zr.File {
		if strings.HasSuffix(f.Name, ".qgs") {
			rc, err := f.Open()
			if err != nil {
				t.Fatal(err)
			}
			defer rc.Close()
			b, err := io.ReadAll(rc)
			if err != nil {
				t.Fatal(err)
			}
			return string(b)
		}
	}
	t.Fatal("no .qgs inside the embedded project")
	return ""
}
