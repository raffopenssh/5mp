package srv

// A minimal, dependency-free OGC GeoPackage 1.3 writer.
//
// Why not shell out to ogr2ogr: the export runs inside the request over data we
// already hold as GeoJSON strings, and a subprocess would mean serialising every
// layer to a temp GeoJSON first (XSA's river layer alone is 34 MB) and then
// hoping GDAL is installed. A GeoPackage is a SQLite file with three metadata
// tables and a 40-byte header in front of ordinary WKB, and we already ship a
// SQLite driver.
//
// Two things here exist so the file is *useful*, not merely valid:
//
//  1. Every column is declared with a real type — INTEGER, REAL, TEXT, BOOLEAN,
//     DATE, DATETIME. GDAL reports the declared type verbatim, so a start_date
//     written as TEXT lands in QGIS as a string and the temporal controller
//     cannot use it; written as DATE it is a date field and animating by date
//     works with no user action. The *values* must then actually parse as
//     ISO-8601 / RFC-3339 UTC or GDAL reads them back as NULL, silently — hence
//     the gpkgDate/gpkgDateTime helpers, never a raw column pass-through.
//  2. Styles travel inside the file, in QGIS's layer_styles table. QGIS loads
//     the useAsDefault=1 row when the layer is opened, so the package looks like
//     the app on first open instead of 20 identical random-colour layers.

import (
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strings"
	"time"
)

// ---- geometry ------------------------------------------------------------

type gpkgGeom struct {
	Type        string          `json:"type"`
	Coordinates json.RawMessage `json:"coordinates"`
	Geometry    *gpkgGeom       `json:"geometry"`
	Geometries  []gpkgGeom      `json:"geometries"`
}

const (
	wkbPoint      = 1
	wkbLineString = 2
	wkbPolygon    = 3
	wkbMultiPoint = 4
	wkbMultiLine  = 5
	wkbMultiPoly  = 6
)

type wkbBuf struct {
	b                      []byte
	minx, miny, maxx, maxy float64
	n                      int
}

func newWKBBuf() *wkbBuf {
	return &wkbBuf{minx: math.Inf(1), miny: math.Inf(1), maxx: math.Inf(-1), maxy: math.Inf(-1)}
}

func (w *wkbBuf) u8(v byte)    { w.b = append(w.b, v) }
func (w *wkbBuf) u32(v uint32) { w.b = binary.LittleEndian.AppendUint32(w.b, v) }
func (w *wkbBuf) pt(lon, lat float64) {
	w.b = binary.LittleEndian.AppendUint64(w.b, math.Float64bits(lon))
	w.b = binary.LittleEndian.AppendUint64(w.b, math.Float64bits(lat))
	w.minx, w.maxx = math.Min(w.minx, lon), math.Max(w.maxx, lon)
	w.miny, w.maxy = math.Min(w.miny, lat), math.Max(w.maxy, lat)
	w.n++
}
func (w *wkbBuf) header(typ uint32) {
	w.u8(1) // little endian
	w.u32(typ)
}

func decodePositions(raw json.RawMessage) [][2]float64 {
	var arr [][]float64
	if json.Unmarshal(raw, &arr) != nil {
		return nil
	}
	out := make([][2]float64, 0, len(arr))
	for _, p := range arr {
		if len(p) >= 2 {
			out = append(out, [2]float64{p[0], p[1]})
		}
	}
	return out
}

func decodeRings(raw json.RawMessage) [][][2]float64 {
	var rawRings []json.RawMessage
	if json.Unmarshal(raw, &rawRings) != nil {
		return nil
	}
	var rings [][][2]float64
	for _, r := range rawRings {
		if pts := decodePositions(r); len(pts) >= 4 {
			rings = append(rings, pts)
		}
	}
	return rings
}

func (w *wkbBuf) writeLine(pts [][2]float64) {
	w.u32(uint32(len(pts)))
	for _, p := range pts {
		w.pt(p[0], p[1])
	}
}

func (w *wkbBuf) writeRings(rings [][][2]float64) {
	w.u32(uint32(len(rings)))
	for _, r := range rings {
		w.writeLine(r)
	}
}

// geom writes one GeoJSON geometry as WKB; false for anything unrepresentable.
func (w *wkbBuf) geom(g *gpkgGeom) bool {
	if g == nil {
		return false
	}
	if g.Geometry != nil { // a Feature was passed
		return w.geom(g.Geometry)
	}
	switch g.Type {
	case "Point":
		var p []float64
		if json.Unmarshal(g.Coordinates, &p) != nil || len(p) < 2 {
			return false
		}
		w.header(wkbPoint)
		w.pt(p[0], p[1])
	case "MultiPoint":
		pts := decodePositions(g.Coordinates)
		if len(pts) == 0 {
			return false
		}
		w.header(wkbMultiPoint)
		w.u32(uint32(len(pts)))
		for _, p := range pts {
			w.header(wkbPoint)
			w.pt(p[0], p[1])
		}
	case "LineString":
		pts := decodePositions(g.Coordinates)
		if len(pts) < 2 {
			return false
		}
		w.header(wkbLineString)
		w.writeLine(pts)
	case "MultiLineString":
		var raw []json.RawMessage
		if json.Unmarshal(g.Coordinates, &raw) != nil || len(raw) == 0 {
			return false
		}
		var lines [][][2]float64
		for _, r := range raw {
			if pts := decodePositions(r); len(pts) >= 2 {
				lines = append(lines, pts)
			}
		}
		if len(lines) == 0 {
			return false
		}
		w.header(wkbMultiLine)
		w.u32(uint32(len(lines)))
		for _, l := range lines {
			w.header(wkbLineString)
			w.writeLine(l)
		}
	case "Polygon":
		rings := decodeRings(g.Coordinates)
		if len(rings) == 0 {
			return false
		}
		w.header(wkbPolygon)
		w.writeRings(rings)
	case "MultiPolygon":
		var raw []json.RawMessage
		if json.Unmarshal(g.Coordinates, &raw) != nil || len(raw) == 0 {
			return false
		}
		var polys [][][][2]float64
		for _, r := range raw {
			if rings := decodeRings(r); len(rings) > 0 {
				polys = append(polys, rings)
			}
		}
		if len(polys) == 0 {
			return false
		}
		w.header(wkbMultiPoly)
		w.u32(uint32(len(polys)))
		for _, p := range polys {
			w.header(wkbPolygon)
			w.writeRings(p)
		}
	case "GeometryCollection":
		// Take the first representable member: a mixed-type layer is not
		// expressible in one GeoPackage table anyway.
		for i := range g.Geometries {
			if w.geom(&g.Geometries[i]) {
				return true
			}
		}
		return false
	default:
		return false
	}
	return true
}

// gpkgBlob wraps WKB in the GeoPackage binary header (magic "GP", version 0,
// flags = little-endian + envelope [minx,maxx,miny,maxy], srs_id 4326).
func gpkgBlob(geojson string) (blob []byte, minx, miny, maxx, maxy float64, ok bool) {
	var g gpkgGeom
	if json.Unmarshal([]byte(geojson), &g) != nil {
		return nil, 0, 0, 0, 0, false
	}
	w := newWKBBuf()
	if !w.geom(&g) || w.n == 0 {
		return nil, 0, 0, 0, 0, false
	}
	hdr := make([]byte, 0, 40+len(w.b))
	hdr = append(hdr, 'G', 'P', 0, 0x03)
	hdr = binary.LittleEndian.AppendUint32(hdr, 4326)
	for _, v := range []float64{w.minx, w.maxx, w.miny, w.maxy} {
		hdr = binary.LittleEndian.AppendUint64(hdr, math.Float64bits(v))
	}
	return append(hdr, w.b...), w.minx, w.miny, w.maxx, w.maxy, true
}

// ---- writer --------------------------------------------------------------

type gpkgCol struct {
	Name string
	Type string // INTEGER | REAL | TEXT | BOOLEAN | DATE | DATETIME
}

type gpkgLayer struct {
	w        *gpkgWriter
	name     string
	geomType string
	// desc is the gpkg_contents description — what GDAL and QGIS show as the
	// layer's abstract. Kept on the layer so a caller building the embedded
	// project does not have to re-derive (or, worse, re-type) the sentence the
	// table already carries; the two must not be able to disagree.
	desc                   string
	cols                   []gpkgCol
	stmt                   *sql.Stmt
	rtree                  *sql.Stmt
	minx, miny, maxx, maxy float64
	count                  int
}

func (l *gpkgLayer) Name() string        { return l.name }
func (l *gpkgLayer) Count() int          { return l.count }
func (l *gpkgLayer) Description() string { return l.desc }

// SetDescription rewrites the stored description, for the case where part of
// it is only knowable after the rows are written (a count).
func (l *gpkgLayer) SetDescription(d string) error {
	l.desc = d
	_, err := l.w.tx.Exec(`UPDATE gpkg_contents SET description=? WHERE table_name=?`, d, l.name)
	return err
}

type gpkgWriter struct {
	db     *sql.DB
	path   string
	tx     *sql.Tx
	layers []*gpkgLayer
	nWrite int
}

const gpkgApplicationID = 0x47504B47 // "GPKG"
const gpkgUserVersion = 10300        // 1.3.0

const wgs84WKT = `GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]`

func newGPKGWriter(path string) (*gpkgWriter, error) {
	_ = os.Remove(path)
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=journal_mode(off)&_pragma=synchronous(off)")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	for _, q := range []string{
		fmt.Sprintf("PRAGMA application_id = %d", gpkgApplicationID),
		fmt.Sprintf("PRAGMA user_version = %d", gpkgUserVersion),
		`CREATE TABLE gpkg_spatial_ref_sys (
			srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY,
			organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
			definition TEXT NOT NULL, description TEXT)`,
		`CREATE TABLE gpkg_contents (
			table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
			identifier TEXT UNIQUE, description TEXT DEFAULT '',
			last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
			min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER,
			CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id))`,
		`CREATE TABLE gpkg_geometry_columns (
			table_name TEXT NOT NULL, column_name TEXT NOT NULL, geometry_type_name TEXT NOT NULL,
			srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
			CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
			CONSTRAINT uk_gc_table_name UNIQUE (table_name),
			CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
			CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys (srs_id))`,
		`CREATE TABLE gpkg_extensions (
			table_name TEXT, column_name TEXT, extension_name TEXT NOT NULL,
			definition TEXT NOT NULL, scope TEXT NOT NULL,
			CONSTRAINT ge_tce UNIQUE (table_name, column_name, extension_name))`,
		`CREATE TABLE layer_styles (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			f_table_catalog TEXT, f_table_schema TEXT, f_table_name TEXT, f_geometry_column TEXT,
			styleName TEXT, styleQML TEXT, styleSLD TEXT, useAsDefault BOOLEAN,
			description TEXT, owner TEXT, ui TEXT,
			update_time DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))`,
	} {
		if _, err := db.Exec(q); err != nil {
			db.Close()
			return nil, fmt.Errorf("gpkg init: %w", err)
		}
	}
	for _, s := range [][]interface{}{
		{"Undefined cartesian SRS", -1, "NONE", -1, "undefined", "undefined cartesian coordinate reference system"},
		{"Undefined geographic SRS", 0, "NONE", 0, "undefined", "undefined geographic coordinate reference system"},
		{"WGS 84 geodetic", 4326, "EPSG", 4326, wgs84WKT, "longitude/latitude coordinates in decimal degrees on the WGS 84 spheroid"},
	} {
		if _, err := db.Exec(`INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)`, s...); err != nil {
			db.Close()
			return nil, err
		}
	}
	w := &gpkgWriter{db: db, path: path}
	if w.tx, err = db.Begin(); err != nil {
		db.Close()
		return nil, err
	}
	return w, nil
}

func quoteIdent(s string) string { return `"` + strings.ReplaceAll(s, `"`, `""`) + `"` }

func (w *gpkgWriter) insertSQL(l *gpkgLayer) string {
	names := ""
	for _, c := range l.cols {
		names += ", " + quoteIdent(c.Name)
	}
	return fmt.Sprintf("INSERT INTO %s (geom%s) VALUES (?%s)",
		quoteIdent(l.name), names, strings.Repeat(",?", len(l.cols)))
}

// AddLayer declares a feature table. geomType is a GeoPackage geometry type
// name (POINT, LINESTRING, POLYGON, MULTIPOLYGON, GEOMETRY...).
func (w *gpkgWriter) AddLayer(name, geomType, description string, cols []gpkgCol) (*gpkgLayer, error) {
	var b strings.Builder
	fmt.Fprintf(&b, "CREATE TABLE %s (fid INTEGER PRIMARY KEY AUTOINCREMENT, geom %s", quoteIdent(name), geomType)
	for _, c := range cols {
		fmt.Fprintf(&b, ", %s %s", quoteIdent(c.Name), c.Type)
	}
	b.WriteString(")")
	if _, err := w.tx.Exec(b.String()); err != nil {
		return nil, fmt.Errorf("create %s: %w", name, err)
	}
	if _, err := w.tx.Exec(
		`INSERT INTO gpkg_contents (table_name, data_type, identifier, description, min_x, min_y, max_x, max_y, srs_id)
		 VALUES (?, 'features', ?, ?, 0, 0, 0, 0, 4326)`, name, name, description); err != nil {
		return nil, err
	}
	if _, err := w.tx.Exec(
		`INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, 4326, 0, 0)`, name, geomType); err != nil {
		return nil, err
	}
	// The spec's R-tree spatial index. Without it every pan in QGIS is a full
	// table scan — 6.9M fire detections cost ~1.1 s per redraw, which reads as
	// "this file is too big to use" rather than as a missing index. Rows are
	// inserted alongside the feature rather than bulk-built at the end, because
	// bulk-building means either a SQL envelope function this driver does not
	// have, or holding every envelope in memory (7M x 32 bytes).
	//
	// The five maintenance triggers the spec defines are deliberately omitted:
	// the file is written once and never edited, and re-exporting is how it
	// changes. GDAL and QGIS only need the table and the gpkg_extensions row.
	if _, err := w.tx.Exec("CREATE VIRTUAL TABLE " + quoteIdent("rtree_"+name+"_geom") +
		" USING rtree(id, minx, maxx, miny, maxy)"); err != nil {
		return nil, err
	}
	if _, err := w.tx.Exec(`INSERT INTO gpkg_extensions
		(table_name, column_name, extension_name, definition, scope)
		VALUES (?, 'geom', 'gpkg_rtree_index', 'http://www.geopackage.org/spec120/#extension_rtree', 'write-only')`,
		name); err != nil {
		return nil, err
	}
	l := &gpkgLayer{w: w, name: name, geomType: geomType, desc: description, cols: cols,
		minx: math.Inf(1), miny: math.Inf(1), maxx: math.Inf(-1), maxy: math.Inf(-1)}
	stmt, err := w.tx.Prepare(w.insertSQL(l))
	if err != nil {
		return nil, err
	}
	l.stmt = stmt
	if l.rtree, err = w.tx.Prepare("INSERT INTO " + quoteIdent("rtree_"+name+"_geom") +
		" (id, minx, maxx, miny, maxy) VALUES (?,?,?,?,?)"); err != nil {
		return nil, err
	}
	w.layers = append(w.layers, l)
	return l, nil
}

// Add writes one feature. Values follow the layer's column order; missing
// trailing values become NULL. Unrepresentable geometry is skipped rather than
// failing — one malformed row must not kill a 25-layer export.
func (l *gpkgLayer) Add(geojson string, vals ...interface{}) {
	if l == nil || geojson == "" {
		return
	}
	blob, minx, miny, maxx, maxy, ok := gpkgBlob(geojson)
	if !ok {
		return
	}
	args := make([]interface{}, 0, len(l.cols)+1)
	args = append(args, blob)
	args = append(args, vals...)
	for len(args) < len(l.cols)+1 {
		args = append(args, nil)
	}
	res, err := l.stmt.Exec(args[:len(l.cols)+1]...)
	if err != nil {
		return
	}
	if fid, err := res.LastInsertId(); err == nil {
		l.rtree.Exec(fid, minx, maxx, miny, maxy)
	}
	l.count++
	l.minx, l.miny = math.Min(l.minx, minx), math.Min(l.miny, miny)
	l.maxx, l.maxy = math.Max(l.maxx, maxx), math.Max(l.maxy, maxy)
	l.w.nWrite++
	if l.w.nWrite%50000 == 0 {
		l.w.checkpoint()
	}
}

// checkpoint commits and reopens the transaction so a million-row export does
// not hold one unbounded transaction in memory. Prepared statements belong to a
// transaction, so they are re-prepared with it.
func (w *gpkgWriter) checkpoint() {
	if w.tx == nil {
		return
	}
	if err := w.tx.Commit(); err != nil {
		return
	}
	tx, err := w.db.Begin()
	if err != nil {
		w.tx = nil
		return
	}
	w.tx = tx
	for _, l := range w.layers {
		if st, err := tx.Prepare(w.insertSQL(l)); err == nil {
			l.stmt = st
		}
		if st, err := tx.Prepare("INSERT INTO " + quoteIdent("rtree_"+l.name+"_geom") +
			" (id, minx, maxx, miny, maxy) VALUES (?,?,?,?,?)"); err == nil {
			l.rtree = st
		}
	}
}

// SetStyle stores a QGIS QML style for a layer, applied automatically on open.
func (w *gpkgWriter) SetStyle(table, qml, description string) {
	w.SetStyleNamed(table, table, qml, description, true)
}

// SetStyleNamed stores an ADDITIONAL named style. QGIS lists every row of
// layer_styles under Layer Properties → Style → Load, so a second style is how
// a file offers a legitimate alternative rendering (the geology export ships
// "as printed" beside the standard age/lithology legend) without a second
// download or a .qml sidecar that can go missing.
//
// `useAsDefault` must be true for exactly one style per table: QGIS applies the
// first default it finds, so two would make which legend you get depend on
// insertion order.
func (w *gpkgWriter) SetStyleNamed(table, name, qml, description string, useAsDefault bool) {
	if qml == "" || w.tx == nil {
		return
	}
	def := 0
	if useAsDefault {
		def = 1
	}
	w.tx.Exec(`INSERT INTO layer_styles
		(f_table_catalog, f_table_schema, f_table_name, f_geometry_column, styleName, styleQML, styleSLD, useAsDefault, description, owner, ui)
		VALUES ('', '', ?, 'geom', ?, ?, NULL, ?, ?, '5MP', NULL)`,
		table, name, qml, def, description)
}

// Finish updates extents, drops empty layers (an empty table in QGIS's browser
// is indistinguishable from a broken one), builds spatial indexes and closes.
func (w *gpkgWriter) Finish() error {
	for _, l := range w.layers {
		if l.count == 0 {
			w.tx.Exec("DROP TABLE " + quoteIdent(l.name))
			w.tx.Exec("DROP TABLE IF EXISTS " + quoteIdent("rtree_"+l.name+"_geom"))
			w.tx.Exec(`DELETE FROM gpkg_extensions WHERE table_name = ?`, l.name)
			w.tx.Exec(`DELETE FROM gpkg_contents WHERE table_name = ?`, l.name)
			w.tx.Exec(`DELETE FROM gpkg_geometry_columns WHERE table_name = ?`, l.name)
			w.tx.Exec(`DELETE FROM layer_styles WHERE f_table_name = ?`, l.name)
			continue
		}
		w.tx.Exec(`UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=? WHERE table_name=?`,
			l.minx, l.miny, l.maxx, l.maxy, l.name)
	}
	if err := w.tx.Commit(); err != nil {
		w.db.Close()
		return err
	}
	w.tx = nil
	return w.db.Close()
}

func (w *gpkgWriter) Layers() []*gpkgLayer { return w.layers }

func (w *gpkgWriter) Close() {
	if w.tx != nil {
		w.tx.Rollback()
		w.tx = nil
	}
	w.db.Close()
}

// ---- typed value helpers -------------------------------------------------
//
// A DATE/DATETIME column is only honoured by GDAL if the *value* parses as
// ISO-8601. A bare "2024" or "2024-03" reads back as NULL, silently — so
// anything that cannot be made into a full date is written as NULL on purpose,
// and the partial original (e.g. a Hansen loss year) keeps its own INTEGER
// column.

func gpkgDate(s string) interface{} {
	s = strings.TrimSpace(s)
	if len(s) < 10 {
		return nil
	}
	if _, err := time.Parse("2006-01-02", s[:10]); err != nil {
		return nil
	}
	return s[:10]
}

func gpkgDateTime(s string) interface{} {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	for _, layout := range []string{time.RFC3339, "2006-01-02T15:04:05", "2006-01-02 15:04:05", "2006-01-02"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.UTC().Format("2006-01-02T15:04:05Z")
		}
	}
	return nil
}

// gpkgDateTimeParts builds a timestamp from FIRMS' split acq_date + acq_time
// ("HHMM", sometimes "HMM" or ""). FIRMS publishes acq_time in UTC.
func gpkgDateTimeParts(date, hhmm string) interface{} {
	d := gpkgDate(date)
	if d == nil {
		return nil
	}
	hhmm = strings.TrimSpace(hhmm)
	for len(hhmm) > 0 && len(hhmm) < 4 {
		hhmm = "0" + hhmm
	}
	if len(hhmm) != 4 {
		return d.(string) + "T00:00:00Z"
	}
	if _, err := time.Parse("1504", hhmm); err != nil {
		return d.(string) + "T00:00:00Z"
	}
	return d.(string) + "T" + hhmm[:2] + ":" + hhmm[2:] + ":00Z"
}

func gpkgStr(s string) interface{} {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	return s
}

func gpkgNS(s sql.NullString) interface{} {
	if !s.Valid {
		return nil
	}
	return gpkgStr(s.String)
}

func gpkgNF(f sql.NullFloat64) interface{} {
	if !f.Valid {
		return nil
	}
	return f.Float64
}

func gpkgNI(i sql.NullInt64) interface{} {
	if !i.Valid {
		return nil
	}
	return i.Int64
}

func gpkgBool(b bool) interface{} {
	if b {
		return 1
	}
	return 0
}

// The JSON helpers read properties_json with the right Go type, so an absent
// key becomes NULL rather than 0 or "".

func gpkgJSONNum(m map[string]interface{}, key string) interface{} {
	if m == nil {
		return nil
	}
	if v, ok := m[key].(float64); ok {
		return v
	}
	return nil
}

func gpkgJSONInt(m map[string]interface{}, key string) interface{} {
	if v, ok := m[key].(float64); ok {
		return int64(v)
	}
	return nil
}

func gpkgJSONStr(m map[string]interface{}, key string) interface{} {
	if m == nil {
		return nil
	}
	if v, ok := m[key].(string); ok {
		return gpkgStr(v)
	}
	return nil
}

func gpkgJSONBool(m map[string]interface{}, key string) interface{} {
	if m == nil {
		return nil
	}
	if v, ok := m[key].(bool); ok {
		return gpkgBool(v)
	}
	return nil
}

func gpkgJSONDate(m map[string]interface{}, key string) interface{} {
	if v, ok := m[key].(string); ok {
		return gpkgDate(v)
	}
	return nil
}
