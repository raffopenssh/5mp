package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
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
