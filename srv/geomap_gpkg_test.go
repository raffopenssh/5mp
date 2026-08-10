package srv

import (
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

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

func TestGeoMapGeoPackageIsQueryableByCommodity(t *testing.T) {
	dir := t.TempDir()
	old := geoMaps.dir
	geoMaps.dir = dir
	defer func() { geoMaps.dir = old }()
	writeTestSheet(t, dir)

	path := geoMapGPKGPath("tst")
	if err := buildGeoMapGeoPackage("tst", path); err != nil {
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
	rows, err := db.Query(`SELECT name, type FROM pragma_table_info('geology_tst')`)
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
	if err := db.QueryRow(`SELECT COUNT(*) FROM geology_tst WHERE "w_gold" IS NOT NULL`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Errorf("gold-hosting units = %d, want 1", n)
	}
	// A unit with no affinity at all must be NULL, not 0: 0 would read as
	// "measured, none", and would also match a `>= 0` filter.
	var w sql.NullInt64
	db.QueryRow(`SELECT "w_gold" FROM geology_tst WHERE code='Qz'`).Scan(&w)
	if w.Valid {
		t.Error("a unit with no gold affinity must be NULL, not a number")
	}

	// The merged class keeps BOTH codes and is labelled with both — the sheet
	// does not say which member a patch is, so the export must not pick one.
	var code, codes, note string
	db.QueryRow(`SELECT code, codes, affinity_note FROM geology_tst WHERE merged=1`).Scan(&code, &codes, &note)
	if code != "Au/Bx" || codes != "Au,Bx" {
		t.Errorf("merged class = %q / %q, want Au/Bx and Au,Bx", code, codes)
	}
	if !strings.Contains(note, "Au:") || !strings.Contains(note, "Bx:") {
		t.Errorf("affinity_note %q must attribute each reason to its member code", note)
	}

	if err := db.QueryRow(`SELECT COUNT(*) FROM layer_styles WHERE f_table_name='geology_tst' AND useAsDefault=1`).Scan(&n); err != nil || n != 1 {
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
	if got := filepath.Base(path); got != "tst_geology.gpkg" {
		t.Errorf("basename = %q, want tst_geology.gpkg", got)
	}
}

// The cache is keyed on mtime, and the failure it guards against is a rebuilt
// sheet still serving the old polygons — the same stale-artefact trap as the
// tile ?v= revision.
func TestGeoMapGeoPackageCacheFollowsTheUnits(t *testing.T) {
	dir := t.TempDir()
	old := geoMaps.dir
	geoMaps.dir = dir
	defer func() { geoMaps.dir = old }()
	writeTestSheet(t, dir)

	if _, ok := geoMapGPKGReady("tst"); ok {
		t.Fatal("nothing built yet, must not report ready")
	}
	if err := buildGeoMapGeoPackage("tst", geoMapGPKGPath("tst")); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady("tst"); !ok {
		t.Fatal("freshly built package must be ready")
	}
	// Touch the units into the future = a re-vectorize.
	st, _ := os.Stat(geoMapGPKGPath("tst"))
	future := st.ModTime().Add(2 * 1e9)
	if err := os.Chtimes(geoMapUnitsPath("tst"), future, future); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady("tst"); ok {
		t.Error("units newer than the package must invalidate it")
	}
}
