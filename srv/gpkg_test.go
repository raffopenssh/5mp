package srv

import (
	"database/sql"
	"encoding/binary"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	_ "modernc.org/sqlite"
)

// The GeoPackage writer's whole value is that another program can read what it
// wrote. These tests check the two properties that are invisible from inside
// Go: that the bytes are a valid GeoPackage (header, magic, metadata tables)
// and that the typed columns carry values GDAL will actually parse as dates.
// The end-to-end "does QGIS render it" check is a manual one and is recorded in
// docs/GEOPACKAGE_EXPORT.md; there is no QGIS in CI.

func TestGPKGBlobHeaderAndWKB(t *testing.T) {
	blob, minx, miny, maxx, maxy, ok := gpkgBlob(`{"type":"LineString","coordinates":[[10,20],[12,25]]}`)
	if !ok {
		t.Fatal("line not encoded")
	}
	if blob[0] != 'G' || blob[1] != 'P' {
		t.Fatalf("bad magic %q", blob[:2])
	}
	if blob[2] != 0 {
		t.Fatalf("version = %d, want 0", blob[2])
	}
	if blob[3] != 0x03 {
		t.Fatalf("flags = %#x, want 0x03 (little-endian + envelope)", blob[3])
	}
	if srs := binary.LittleEndian.Uint32(blob[4:]); srs != 4326 {
		t.Fatalf("srs = %d, want 4326", srs)
	}
	// Envelope order in the header is minx, maxx, miny, maxy — NOT the
	// minx, miny, maxx, maxy order used everywhere else in this codebase.
	env := make([]float64, 4)
	for i := range env {
		env[i] = math.Float64frombits(binary.LittleEndian.Uint64(blob[8+i*8:]))
	}
	if env[0] != 10 || env[1] != 12 || env[2] != 20 || env[3] != 25 {
		t.Fatalf("envelope = %v, want [10 12 20 25]", env)
	}
	if minx != 10 || miny != 20 || maxx != 12 || maxy != 25 {
		t.Fatalf("returned bbox = %v %v %v %v", minx, miny, maxx, maxy)
	}
	wkb := blob[40:]
	if wkb[0] != 1 {
		t.Fatal("wkb not little-endian")
	}
	if typ := binary.LittleEndian.Uint32(wkb[1:]); typ != wkbLineString {
		t.Fatalf("wkb type = %d, want %d", typ, wkbLineString)
	}
}

func TestGPKGBlobRejectsDegenerate(t *testing.T) {
	for _, gj := range []string{
		``,
		`not json`,
		`{"type":"LineString","coordinates":[[1,2]]}`,      // a line needs 2 points
		`{"type":"Polygon","coordinates":[[[1,2],[3,4]]]}`, // a ring needs 4
		`{"type":"Point","coordinates":[]}`,
		`{"type":"Sphere","coordinates":[1,2]}`,
	} {
		if _, _, _, _, _, ok := gpkgBlob(gj); ok {
			t.Errorf("accepted degenerate geometry: %s", gj)
		}
	}
	// A Feature wrapper is unwrapped rather than rejected: several tables store
	// whole Features in their geojson column.
	if _, _, _, _, _, ok := gpkgBlob(`{"type":"Feature","properties":{},"geometry":{"type":"Point","coordinates":[1,2]}}`); !ok {
		t.Error("Feature wrapper rejected")
	}
}

// The date helpers are where a wrong answer is silent: GDAL reads a DATE column
// whose value does not parse as ISO-8601 back as NULL, with no error anywhere.
func TestGPKGDateNormalisation(t *testing.T) {
	cases := []struct{ in, want string }{
		{"2024-03-05", "2024-03-05"},
		{"2024-03-05T11:22:33Z", "2024-03-05"},
		{"2024", ""},    // a bare year is not a date
		{"2024-03", ""}, // nor a month
		{"", ""},
		{"not a date", ""},
	}
	for _, c := range cases {
		got := gpkgDate(c.in)
		if c.want == "" {
			if got != nil {
				t.Errorf("gpkgDate(%q) = %v, want nil", c.in, got)
			}
			continue
		}
		if got != c.want {
			t.Errorf("gpkgDate(%q) = %v, want %q", c.in, got, c.want)
		}
	}
	if got := gpkgDateTime("2024-03-05 11:22:33"); got != "2024-03-05T11:22:33Z" {
		t.Errorf("gpkgDateTime space form = %v", got)
	}
	// FIRMS splits the timestamp and drops leading zeros: "134" is 01:34.
	for in, want := range map[string]string{
		"1134": "2024-03-05T11:34:00Z",
		"134":  "2024-03-05T01:34:00Z",
		"":     "2024-03-05T00:00:00Z",
		"9999": "2024-03-05T00:00:00Z", // not a time — the date still stands
	} {
		if got := gpkgDateTimeParts("2024-03-05", in); got != want {
			t.Errorf("gpkgDateTimeParts(%q) = %v, want %q", in, got, want)
		}
	}
	if gpkgDateTimeParts("", "1134") != nil {
		t.Error("no date should give no timestamp")
	}
}

// A written file must satisfy the things GDAL checks before it will open it at
// all, and the things QGIS checks before it will style it.
func TestGPKGWriterProducesReadableFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "t.gpkg")
	w, err := newGPKGWriter(path)
	if err != nil {
		t.Fatal(err)
	}
	l, err := w.AddLayer("pts", "POINT", "test points", []gpkgCol{
		{"name", "TEXT"}, {"when", "DATE"}, {"n", "INTEGER"},
	})
	if err != nil {
		t.Fatal(err)
	}
	l.Add(`{"type":"Point","coordinates":[10,20]}`, "a", gpkgDate("2024-01-02"), 7)
	l.Add(`{"type":"Point","coordinates":[12,22]}`, "b", gpkgDate("nope"), nil)
	l.Add(`garbage`, "c", nil, nil) // skipped, not fatal

	// An empty layer must not survive: in QGIS's browser an empty table is
	// indistinguishable from a broken one.
	if _, err := w.AddLayer("empty", "POINT", "", []gpkgCol{{"x", "TEXT"}}); err != nil {
		t.Fatal(err)
	}
	w.SetStyle("pts", styleFireDetections(), "test")
	if err := w.writeQGISProject("Test", "t.gpkg",
		[]gpkgLayerSpec{{Table: "pts", Title: "Points", Group: "G", Geometry: "Point",
			WKBType: "Point", QML: styleFireDetections(), Visible: true, TemporalStart: "when"}},
		[4]float64{9, 19, 13, 23}); err != nil {
		t.Fatal(err)
	}
	if err := w.Finish(); err != nil {
		t.Fatal(err)
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	// GDAL identifies a GeoPackage by the SQLite header's application_id at
	// offset 68 — get this wrong and the file opens as "SQLite", not "GPKG".
	if id := binary.BigEndian.Uint32(raw[68:72]); id != gpkgApplicationID {
		t.Errorf("application_id = %#x, want %#x", id, gpkgApplicationID)
	}
	if v := binary.BigEndian.Uint32(raw[60:64]); v != gpkgUserVersion {
		t.Errorf("user_version = %d, want %d", v, gpkgUserVersion)
	}

	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	var n int
	db.QueryRow(`SELECT COUNT(*) FROM pts`).Scan(&n)
	if n != 2 {
		t.Errorf("rows = %d, want 2 (the malformed geometry must be skipped)", n)
	}
	if err := db.QueryRow(`SELECT COUNT(*) FROM gpkg_contents WHERE table_name='empty'`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Error("empty layer left in gpkg_contents")
	}
	if err := db.QueryRow(`SELECT COUNT(*) FROM sqlite_master WHERE name='empty'`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Error("empty layer table not dropped")
	}
	// Declared types are what GDAL reports, so they are part of the contract.
	rows, _ := db.Query(`SELECT name, type FROM pragma_table_info('pts')`)
	types := map[string]string{}
	for rows.Next() {
		var c, ty string
		rows.Scan(&c, &ty)
		types[c] = ty
	}
	rows.Close()
	for col, want := range map[string]string{"geom": "POINT", "when": "DATE", "n": "INTEGER", "name": "TEXT"} {
		if types[col] != want {
			t.Errorf("column %s declared %q, want %q", col, types[col], want)
		}
	}
	// Extent must be real, not the 0,0,0,0 placeholder written at create time:
	// QGIS's "zoom to layer" reads it.
	var minx, miny, maxx, maxy float64
	db.QueryRow(`SELECT min_x, min_y, max_x, max_y FROM gpkg_contents WHERE table_name='pts'`).
		Scan(&minx, &miny, &maxx, &maxy)
	if minx != 10 || miny != 20 || maxx != 12 || maxy != 22 {
		t.Errorf("extent = %v %v %v %v", minx, miny, maxx, maxy)
	}
	// The R-tree is what makes a 7M-point layer pannable.
	if err := db.QueryRow(`SELECT COUNT(*) FROM rtree_pts_geom`).Scan(&n); err != nil {
		t.Fatalf("no r-tree: %v", err)
	}
	if n != 2 {
		t.Errorf("r-tree rows = %d, want 2", n)
	}
	if err := db.QueryRow(`SELECT COUNT(*) FROM gpkg_extensions
		WHERE table_name='pts' AND extension_name='gpkg_rtree_index'`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Error("r-tree not registered in gpkg_extensions")
	}
	// Style and project.
	var qml string
	if err := db.QueryRow(`SELECT styleQML FROM layer_styles WHERE f_table_name='pts' AND useAsDefault=1`).
		Scan(&qml); err != nil {
		t.Fatalf("no default style: %v", err)
	}
	if !strings.Contains(qml, "renderer-v2") {
		t.Error("style carries no renderer")
	}
	var content string
	if err := db.QueryRow(`SELECT content FROM qgis_projects WHERE name='Test'`).Scan(&content); err != nil {
		t.Fatalf("no embedded project: %v", err)
	}
	if !strings.HasPrefix(content, "504b0304") { // hex-encoded zip
		t.Errorf("project content is not a hex-encoded zip: %.16s", content)
	}
}

// The temporal mode values are QGIS enum members, and 0 means "fixed range" —
// i.e. ignore the fields. Getting this wrong produces a layer that claims to be
// temporal and silently renders everything at every timestep.
func TestQGISTemporalModes(t *testing.T) {
	instant := qgsTemporal(gpkgLayerSpec{TemporalStart: "acq_datetime_utc"})
	if !strings.Contains(instant, `mode="1"`) {
		t.Errorf("instant mode wrong: %s", instant)
	}
	span := qgsTemporal(gpkgLayerSpec{TemporalStart: "start_date", TemporalEnd: "end_date"})
	if !strings.Contains(span, `mode="2"`) || !strings.Contains(span, `endField="end_date"`) {
		t.Errorf("range mode wrong: %s", span)
	}
	off := qgsTemporal(gpkgLayerSpec{})
	if !strings.Contains(off, `enabled="0"`) {
		t.Errorf("absent temporal field should disable: %s", off)
	}
}

// The project references its own container by basename, so a job's file must be
// named exactly what the download is named — otherwise the project opens with
// no layers, which reads as a broken export.
func TestGeoPackageDownloadNameIsStable(t *testing.T) {
	if got := gpkgDownloadName("CAF_Chinko", "2024-01-01", "", true); got != "CAF_Chinko_2024-01-01_to_now.gpkg" {
		t.Errorf("got %q", got)
	}
	if got := gpkgDownloadName("XSA_Study_Area", "", "", true); got != "XSA_Study_Area.gpkg" {
		t.Errorf("got %q", got)
	}
	// The two variants differ by a gigabyte and land in the same Downloads
	// folder, so the name has to say which one it is.
	if got := gpkgDownloadName("XSA_Study_Area", "", "", false); got != "XSA_Study_Area_no_raw_fire.gpkg" {
		t.Errorf("got %q", got)
	}
	// Path separators and quotes must never survive into a filename: the value
	// becomes a directory entry and a Content-Disposition header.
	if got := gpkgDownloadName("../../etc/passwd", "", "", true); got != "etcpasswd.gpkg" {
		t.Errorf("unsafe name %q", got)
	}
	if got := gpkgDownloadName(`a"b/c\d..e`, "", "", true); got != "abcde.gpkg" {
		t.Errorf("unsafe name %q", got)
	}
}

// The in-area test is what stops a bbox query from presenting its corners as if
// they were the area. It has to be exact for a concave ring and for holes.
func TestAreaHitTest(t *testing.T) {
	// A C shape: the notch on the right is outside.
	c := `{"type":"Polygon","coordinates":[[[0,0],[10,0],[10,3],[4,3],[4,7],[10,7],[10,10],[0,10],[0,0]]]}`
	h := newAreaHitTest(c)
	for _, tc := range []struct {
		x, y float64
		in   bool
	}{
		{1, 5, true},   // in the spine
		{7, 5, false},  // in the notch
		{7, 1, true},   // in the lower arm
		{7, 9, true},   // in the upper arm
		{-1, 5, false}, // west of everything
		{11, 5, false}, // east of the bbox
		{5, 11, false}, // north of the bbox
	} {
		if got := h.Contains(tc.x, tc.y); got != tc.in {
			t.Errorf("Contains(%v,%v) = %v, want %v", tc.x, tc.y, got, tc.in)
		}
	}
	// A hole reverses parity.
	donut := `{"type":"Polygon","coordinates":[[[0,0],[10,0],[10,10],[0,10],[0,0]],[[4,4],[6,4],[6,6],[4,6],[4,4]]]}`
	d := newAreaHitTest(donut)
	if !d.Contains(1, 1) {
		t.Error("point in ring reported outside")
	}
	if d.Contains(5, 5) {
		t.Error("point in hole reported inside")
	}
	// An unusable boundary must not mark the whole export as outside: silently
	// flagging every row 0 is worse than not knowing.
	for _, bad := range []string{"", "{}", `{"type":"Point","coordinates":[1,2]}`} {
		if !newAreaHitTest(bad).Contains(999, 999) {
			t.Errorf("unusable boundary %q should default to inside", bad)
		}
	}
}

func TestGeoPackageCacheKeyDistinguishesQuestions(t *testing.T) {
	base := gpkgCacheKey("CAF_Chinko", "2024-01-01", "", true, true, "prod")
	for _, other := range []string{
		gpkgCacheKey("CAF_Chinko", "2024-01-02", "", true, true, "prod"),
		gpkgCacheKey("CAF_Chinko", "2024-01-01", "2025-01-01", true, true, "prod"),
		gpkgCacheKey("CAF_Chinko", "2024-01-01", "", false, true, "prod"),
		gpkgCacheKey("CAF_Chinko", "2024-01-01", "", true, false, "prod"), // the lighter export
		gpkgCacheKey("CAF_Chinko", "2024-01-01", "", true, true, "test"),
		gpkgCacheKey("COD_Virunga", "2024-01-01", "", true, true, "prod"),
	} {
		if other == base {
			t.Errorf("cache key collision: %q", other)
		}
	}
	if gpkgCacheKey("A", "b", "c", true, true, "prod") != gpkgCacheKey("A", "b", "c", true, true, "prod") {
		t.Error("cache key is not deterministic")
	}
}
