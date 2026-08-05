package srv

// Locus Map 4 backup export (.locus button in park tooltip).
//
// Produces a zip restorable via Locus Map 4 → Settings → Backup → Restore.
// Format reverse-engineered from a real 2026 field-device backup
// (/tmp/locus_ex/2026-07-09_10-29-58): the folder tree lives in each DB's
// `groups` table (mode=1 rows are containers, mode=0 rows are folders);
// no myLibrary.db or .backup_info is needed. We ship only data/database/*.
// All polygon features (boundary, settlements, deforestation, waterbodies)
// are exported as closed-ring LINE tracks, never filled shapes — lines are
// much easier to work with in Locus in the field.
// — never another device's settings/config blobs.
//
// Locus 3 devices are no longer in the field (confirmed 2026-07-09), so this
// targets the L4 schema only.

import (
	"archive/zip"
	"context"
	"crypto/rand"
	"database/sql"
	"embed"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io/fs"
	"math"
	"net/http"
	"strings"
	"time"
)

// Default device configuration, taken from the reference 2026 field-device
// backup (BOMA preset, config.cfg, custom online map, quick-add points…).
// The _various/settings blob is sanitized: the original device's Dropbox
// OAuth credentials (KEY_S_DBX_CREDENTIALS) are blanked; personal search
// history (get_location_recently_used.lb) and the offline-map registry
// (config.db — references maps we don't ship) are excluded entirely.
//
//go:embed all:locus_defaults
var locusDefaultsFS embed.FS

// Reference blobs byte-copied from the 2026 field-device sample.
// Track extra_style: 140 bytes; ARGB color u32 at offset 82, line width f32 at 115.
const locusTrackStyleHex = "0100000002000000820000000000000031396130623138643933336566353233363634346534333064646433363263653431303062386430395f663764383136303700000000000001000000000000003a01ffff000000ffffffff00000006444f545445440000000653494d504c45000000003f80000000000006504958454c5300ffffffff00ffffffff00"

// Group extra_style: 85 bytes; ARGB color u32 at offset 32, width f32 at 65.
const locusGroupStyleHex = "0000000200000051000000000000000000000000000001000000000000003a0196007d3400ffffffff00000006444f545445440000000653494d504c45000000004080000000000006504958454c5300ffffffff00ffffffff"

// Track statistics: 177 bytes, zeroed except header + trailing +inf/-inf;
// numPoints i32 at offset 8, track length (m) f32 at offsets 28 and 64.
const locusStatsZeroHex = "00000004000000a900000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000007f800000ff800000"

func locusTrackStyle(argb uint32, width float32) []byte {
	b, _ := hex.DecodeString(locusTrackStyleHex)
	binary.BigEndian.PutUint32(b[82:], argb)
	binary.BigEndian.PutUint32(b[115:], math.Float32bits(width))
	return b
}

func locusGroupStyle(argb uint32, width float32) []byte {
	b, _ := hex.DecodeString(locusGroupStyleHex)
	binary.BigEndian.PutUint32(b[32:], argb)
	binary.BigEndian.PutUint32(b[65:], math.Float32bits(width))
	return b
}

func locusStats(numPoints int, lengthM float64) []byte {
	b, _ := hex.DecodeString(locusStatsZeroHex)
	binary.BigEndian.PutUint32(b[8:], uint32(numPoints))
	binary.BigEndian.PutUint32(b[28:], math.Float32bits(float32(lengthM)))
	binary.BigEndian.PutUint32(b[64:], math.Float32bits(float32(lengthM)))
	return b
}

func locusUUID() []byte {
	b := make([]byte, 16)
	rand.Read(b)
	return b
}

// extractPaths returns line paths ([lon,lat] vertex lists) from a GeoJSON
// geometry or feature. Polygons yield their outer rings (closed).
func extractPaths(geojsonStr string) [][][2]float64 {
	var g map[string]interface{}
	if err := json.Unmarshal([]byte(geojsonStr), &g); err != nil {
		return nil
	}
	if geom, ok := g["geometry"].(map[string]interface{}); ok {
		g = geom
	}
	typ, _ := g["type"].(string)
	coords := g["coordinates"]
	toPt := func(v interface{}) ([2]float64, bool) {
		arr, ok := v.([]interface{})
		if !ok || len(arr) < 2 {
			return [2]float64{}, false
		}
		lon, ok1 := arr[0].(float64)
		lat, ok2 := arr[1].(float64)
		return [2]float64{lon, lat}, ok1 && ok2
	}
	toPath := func(v interface{}) [][2]float64 {
		arr, ok := v.([]interface{})
		if !ok {
			return nil
		}
		var path [][2]float64
		for _, p := range arr {
			if pt, ok := toPt(p); ok {
				path = append(path, pt)
			}
		}
		return path
	}
	var out [][][2]float64
	switch typ {
	case "LineString":
		if p := toPath(coords); len(p) >= 2 {
			out = append(out, p)
		}
	case "MultiLineString":
		if arr, ok := coords.([]interface{}); ok {
			for _, ls := range arr {
				if p := toPath(ls); p != nil && len(p) >= 2 {
					out = append(out, p)
				}
			}
		}
	case "Polygon":
		if arr, ok := coords.([]interface{}); ok && len(arr) > 0 {
			if p := toPath(arr[0]); len(p) >= 3 {
				out = append(out, openRing(p))
			}
		}
	case "MultiPolygon":
		if arr, ok := coords.([]interface{}); ok {
			for _, poly := range arr {
				if rings, ok := poly.([]interface{}); ok && len(rings) > 0 {
					if p := toPath(rings[0]); len(p) >= 3 {
						out = append(out, openRing(p))
					}
				}
			}
		}
	}
	return out
}

// extractPoint returns lon/lat from a GeoJSON Point (or Feature wrapping one).
func extractPoint(geojsonStr string) (float64, float64, bool) {
	var g map[string]interface{}
	if err := json.Unmarshal([]byte(geojsonStr), &g); err != nil {
		return 0, 0, false
	}
	if geom, ok := g["geometry"].(map[string]interface{}); ok {
		g = geom
	}
	if t, _ := g["type"].(string); t != "Point" {
		return 0, 0, false
	}
	arr, ok := g["coordinates"].([]interface{})
	if !ok || len(arr) < 2 {
		return 0, 0, false
	}
	lon, ok1 := arr[0].(float64)
	lat, ok2 := arr[1].(float64)
	return lon, lat, ok1 && ok2
}

// openRing ensures a closed ring is NOT detected as a polygon by Locus:
// if first==last vertex, the closing vertex is pulled back ~5 m along the
// final segment, leaving a small visible gap. Field users are always inside
// some polygon; filled/closed shapes make map tapping hard in Locus.
func openRing(path [][2]float64) [][2]float64 {
	n := len(path)
	if n < 4 || path[0] != path[n-1] {
		return path
	}
	prev := path[n-2]
	end := path[n-1]
	dLon := end[0] - prev[0]
	dLat := end[1] - prev[1]
	segM := haversineDistanceKm(prev[1], prev[0], end[1], end[0]) * 1000
	const gapM = 5.0
	if segM <= gapM {
		// final segment shorter than the gap: just drop the closing vertex
		return path[:n-1]
	}
	f := (segM - gapM) / segM
	path[n-1] = [2]float64{prev[0] + dLon*f, prev[1] + dLat*f}
	return path
}

func pathLengthM(path [][2]float64) float64 {
	var m float64
	for i := 1; i < len(path); i++ {
		m += haversineDistanceKm(path[i-1][1], path[i-1][0], path[i][1], path[i][0]) * 1000
	}
	return m
}

// locusDB wraps one of the two backup sqlite databases (built in RAM only —
// disk usage costs extra on this VM; these DBs are a few MB at most).
type locusDB struct {
	db      *sql.DB
	nowMs   int64
	visible [][2]int64 // (itemId, groupId) pairs for .mVisibleItems file
}

func newLocusDB(schema string) (*locusDB, error) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		return nil, err
	}
	// Single connection required so all writes hit the same :memory: DB and
	// Serialize sees them.
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, err
	}
	return &locusDB{db: db, nowMs: time.Now().UnixMilli()}, nil
}

// serialize returns the database file bytes (modernc.org/sqlite Serialize).
func (l *locusDB) serialize() ([]byte, error) {
	conn, err := l.db.Conn(context.Background())
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	var out []byte
	err = conn.Raw(func(driverConn interface{}) error {
		s, ok := driverConn.(interface{ Serialize() ([]byte, error) })
		if !ok {
			return fmt.Errorf("driver conn does not implement Serialize")
		}
		var serErr error
		out, serErr = s.Serialize()
		return serErr
	})
	return out, err
}

const locusTracksSchema = `
CREATE TABLE tracks (_id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, rw_mode TEXT, name TEXT, time_created INTEGER, time_updated INTEGER, activity_type INTEGER,statistics BYTE, extra_data BYTE, extra_style BYTE, use_category_style INTEGER,trackpoints BYTE, breaks BYTE,store_item_id INTEGER,store_version_id INTEGER, privacy TEXT, uuid BYTE, overview_image BYTE);
CREATE INDEX tracks_uuid ON tracks (uuid);
CREATE TABLE locations (_id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, longitude FLOAT, latitude FLOAT, time INTEGER, elevation FLOAT, speed FLOAT, bearing FLOAT, accuracy FLOAT, parent_id INTEGER, previous_id INTEGER, sensor_heart_rate INTEGER, sensor_cadence INTEGER, sensor_speed FLOAT, sensor_power FLOAT, sensor_strides INTEGER, sensor_battery INTEGER, sensor_temperature FLOAT);
CREATE INDEX locations_parent_id ON locations (parent_id);
CREATE INDEX locations_previous_id ON locations (previous_id);
CREATE TABLE groups (_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NON NULL, mode INTEGER DEFAULT 0, icon TEXT NON NULL, extra_style BYTE, parent_id INTEGER DEFAULT -1, labels_mode INTEGER DEFAULT -1, time_created INTEGER, time_updated INTEGER, uuid BYTE);
CREATE TABLE categories (_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,icon TEXT,extra_style BYTE, group_id INTEGER, labels_mode INTEGER);
CREATE TABLE folder_group (_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
CREATE TABLE items_deleted (_id INTEGER PRIMARY KEY AUTOINCREMENT, type INTEGER, time_deleted INTEGER, uuid BYTE);
CREATE TABLE android_metadata (locale TEXT);
INSERT INTO android_metadata VALUES ('en');
`

const locusWaypointsSchema = `
CREATE TABLE waypoints (_id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, track_id INTEGER, rw_mode TEXT, name TEXT, name_testing TEXT, extra_data BYTE, extra_icon TEXT, extra_style BYTE, extra_gc_simple BYTE, extra_gc BYTE, longitude INTEGER, latitude INTEGER, time_created INTEGER, time INTEGER, elevation FLOAT, speed FLOAT, bearing FLOAT, accuracy FLOAT, privacy TEXT, uuid BYTE);
CREATE INDEX waypoints_id ON waypoints (_id);
CREATE INDEX waypoints_query_exists ON waypoints (name_testing, parent_id);
CREATE INDEX waypoints_uuid ON waypoints (uuid);
CREATE TABLE groups (_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NON NULL, mode INTEGER DEFAULT 0, icon TEXT NON NULL, extra_style BYTE, parent_id INTEGER DEFAULT -1, labels_mode INTEGER DEFAULT -1, time_created INTEGER, time_updated INTEGER, uuid BYTE);
CREATE TABLE categories (_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,icon TEXT,extra_style BYTE, group_id INTEGER, labels_mode INTEGER);
CREATE TABLE folder_group (_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
CREATE TABLE items_deleted (_id INTEGER PRIMARY KEY AUTOINCREMENT, type INTEGER, time_deleted INTEGER, uuid BYTE);
CREATE TABLE android_metadata (locale TEXT);
INSERT INTO android_metadata VALUES ('en');
`

func (l *locusDB) addGroup(name string, mode int, icon string, style []byte, parentID int64) (int64, error) {
	res, err := l.db.Exec(`INSERT INTO groups (name, mode, icon, extra_style, parent_id, labels_mode, time_created, time_updated, uuid) VALUES (?,?,?,?,?,-1,?,?,?)`,
		name, mode, icon, style, parentID, l.nowMs, l.nowMs, locusUUID())
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// addTrack inserts a track + its locations chain. Returns track id.
func (l *locusDB) addTrack(groupID int64, name string, path [][2]float64, style []byte, visible bool) (int64, error) {
	res, err := l.db.Exec(`INSERT INTO tracks (parent_id, name, time_created, time_updated, activity_type, statistics, extra_style, use_category_style, privacy, uuid) VALUES (?,?,?,?,0,?,?,1,'PRIVATE',?)`,
		groupID, name, l.nowMs, l.nowMs, locusStats(len(path), pathLengthM(path)), style, locusUUID())
	if err != nil {
		return 0, err
	}
	trackID, _ := res.LastInsertId()
	var prev interface{} // NULL for first vertex
	for _, pt := range path {
		lres, err := l.db.Exec(`INSERT INTO locations (provider, longitude, latitude, time, parent_id, previous_id) VALUES ('',?,?,0,?,?)`,
			pt[0], pt[1], trackID, prev)
		if err != nil {
			return 0, err
		}
		id, _ := lres.LastInsertId()
		prev = id
	}
	if visible {
		l.visible = append(l.visible, [2]int64{trackID, groupID})
	}
	return trackID, nil
}

func (l *locusDB) addWaypoint(groupID int64, name string, lon, lat float64, icon string, visible bool) error {
	res, err := l.db.Exec(`INSERT INTO waypoints (parent_id, name, name_testing, extra_icon, longitude, latitude, time_created, time, privacy, uuid) VALUES (?,?,?,?,?,?,?,?, 'PRIVATE',?)`,
		groupID, name, name, nullIfEmpty(icon), lon, lat, l.nowMs, l.nowMs, locusUUID())
	if err != nil {
		return err
	}
	if visible {
		id, _ := res.LastInsertId()
		l.visible = append(l.visible, [2]int64{id, groupID})
	}
	return nil
}

func nullIfEmpty(s string) interface{} {
	if s == "" {
		return nil
	}
	return s
}

func (l *locusDB) visibleBytes() []byte {
	buf := make([]byte, 0, len(l.visible)*16)
	var tmp [8]byte
	for _, pair := range l.visible {
		binary.BigEndian.PutUint64(tmp[:], uint64(pair[0]))
		buf = append(buf, tmp[:]...)
		binary.BigEndian.PutUint64(tmp[:], uint64(pair[1]))
		buf = append(buf, tmp[:]...)
	}
	return buf
}

// HandleAPIParkLocus builds a Locus Map 4 backup zip for one park.
// GET /api/parks/{id}/export.locus  (?from=&to= date filters like export.kml)
func (s *Server) HandleAPIParkLocus(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}
	fromDate := r.URL.Query().Get("from")
	toDate := r.URL.Query().Get("to")

	parkName := parkID
	var boundary string
	for _, pa := range s.AreaStore.Areas {
		if pa.ID == parkID {
			parkName = pa.Name
			if pa.Geometry.Type != "" {
				if b, err := json.Marshal(pa.Geometry); err == nil {
					boundary = string(b)
				}
			}
			break
		}
	}

	tdb, err := newLocusDB(locusTracksSchema)
	if err != nil {
		http.Error(w, "tracks db: "+err.Error(), http.StatusInternalServerError)
		return
	}
	defer tdb.db.Close()
	wdb, err := newLocusDB(locusWaypointsSchema)
	if err != nil {
		http.Error(w, "waypoints db: "+err.Error(), http.StatusInternalServerError)
		return
	}
	defer wdb.db.Close()

	if err := s.buildLocusContent(tdb, wdb, parkID, parkName, boundary, fromDate, toDate, r); err != nil {
		http.Error(w, "build: "+err.Error(), http.StatusInternalServerError)
		return
	}

	tracksBytes, err := tdb.serialize()
	if err != nil {
		http.Error(w, "serialize tracks: "+err.Error(), http.StatusInternalServerError)
		return
	}
	wptsBytes, err := wdb.serialize()
	if err != nil {
		http.Error(w, "serialize waypoints: "+err.Error(), http.StatusInternalServerError)
		return
	}
	visTracks := tdb.visibleBytes()
	visWpts := wdb.visibleBytes()
	tdb.db.Close()
	wdb.db.Close()

	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s_locus_%s.zip"`, parkID, time.Now().Format("2006-01-02")))
	zw := zip.NewWriter(w)
	writeFile := func(name string, data []byte) error {
		f, err := zw.Create(name)
		if err != nil {
			return err
		}
		_, err = f.Write(data)
		return err
	}
	if writeFile("data/database/tracks.db", tracksBytes) != nil {
		return
	}
	if writeFile("data/database/waypoints.db", wptsBytes) != nil {
		return
	}
	writeFile("data/database/.mVisibleItems_dbTracks", visTracks)
	writeFile("data/database/.mVisibleItems_dbWaypoints", visWpts)
	// Default device settings/presets (sanitized reference backup)
	fs.WalkDir(locusDefaultsFS, "locus_defaults", func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		data, rerr := locusDefaultsFS.ReadFile(path)
		if rerr != nil {
			return nil
		}
		writeFile(strings.TrimPrefix(path, "locus_defaults/"), data)
		return nil
	})
	zw.Close()
}

// buildLocusContent mirrors HandleAPIParkKML's data queries into the two DBs.
func (s *Server) buildLocusContent(tdb, wdb *locusDB, parkID, parkName, boundary, fromDate, toDate string, r *http.Request) error {
	// ---- Folder tree (groups) ----
	// Invisible system groups (id 1 in the field sample)
	tdb.addGroup("tracks_category_invisible", 0, "ic_tracks", nil, -1)
	wdb.addGroup("waypoints_category_invisible", 0, "ic_tracks", nil, -1)

	// Containers (mode=1)
	tBase, _ := tdb.addGroup("Base", 1, "", nil, -1)
	tMission, _ := tdb.addGroup("Mission", 1, "", nil, -1)
	wBase, _ := wdb.addGroup("Base", 1, "", nil, -1)
	wMission, _ := wdb.addGroup("Mission", 1, "", nil, -1)

	// Track folders (mode=0) with group styles (ARGB)
	gBoundary, _ := tdb.addGroup("BOUNDARY", 0, "ic_tracks", locusGroupStyle(0xFF00C853, 4), tBase)
	gRivers, _ := tdb.addGroup("RIVERS", 0, "ic_tracks", locusGroupStyle(0xFF2196F3, 2), tBase)
	gRoads, _ := tdb.addGroup("ROADS", 0, "ic_tracks", locusGroupStyle(0xFFD2B48C, 2), tBase)
	gPatrol, _ := tdb.addGroup("PATROL TRACKS", 0, "ic_tracks", locusGroupStyle(0xFF8BC34A, 2), tBase)
	gWater, _ := tdb.addGroup("WATERBODIES", 0, "ic_tracks", locusGroupStyle(0xFF03A9F4, 1), tBase)
	gSettle, _ := tdb.addGroup("SETTLEMENTS", 0, "ic_tracks", locusGroupStyle(0xFFFF9800, 2), tMission)
	gDefo, _ := tdb.addGroup("DEFORESTATION", 0, "ic_tracks", locusGroupStyle(0xFFE91E63, 2), tMission)

	// Waypoint folders
	wPlaces, _ := wdb.addGroup("PLACES", 0, "icon_default.png", nil, wBase)
	wLakes, _ := wdb.addGroup("LAKES", 0, "icon_default.png", nil, wBase)
	wAirstrips, _ := wdb.addGroup("AIRSTRIPS", 0, "transport-airport.png", nil, wBase)
	wTurb, _ := wdb.addGroup("TURBIDITY ALERTS", 0, "z-ico16.png", nil, wMission)

	// ---- Base content ----
	// Park boundary (visible)
	for i, path := range extractPaths(boundary) {
		name := parkName + " boundary"
		if i > 0 {
			name = fmt.Sprintf("%s boundary %d", parkName, i+1)
		}
		tdb.addTrack(gBoundary, name, path, locusTrackStyle(0xFF00C853, 4), true)
	}

	// Rivers (HydroRIVERS) — merged into continuous polylines (raw table rows
	// are hundreds of tiny disconnected reach stubs), width by stream order.
	for _, rv := range s.loadMergedRivers(parkID, 3, 400) {
		label := fmt.Sprintf("%s (%.0f km)", rv.Name, rv.LengthKm)
		width := float32(1)
		if rv.StreamOrder >= 6 {
			width = 3
		} else if rv.StreamOrder >= 4 {
			width = 2
		}
		tdb.addTrack(gRivers, label, rv.Path, locusTrackStyle(0xFF2196F3, width), false)
	}

	// Roads (HeiGIT)
	heigitRows, _ := s.DB.Query(`SELECT osm_id, highway_type, surface, length_km, geojson FROM roads_heigit WHERE park_id = ? AND geojson IS NOT NULL LIMIT 1000`, parkID)
	if heigitRows != nil {
		defer heigitRows.Close()
		for heigitRows.Next() {
			var osmID, highwayType, surface sql.NullString
			var lengthKm float64
			var geojson string
			heigitRows.Scan(&osmID, &highwayType, &surface, &lengthKm, &geojson)
			label := fmt.Sprintf("%s (%s, %.1f km)", strOr(highwayType.String, "road"), strOr(surface.String, "unknown surface"), lengthKm)
			for _, path := range extractPaths(geojson) {
				tdb.addTrack(gRoads, label, path, locusTrackStyle(0xFFD2B48C, 2), false)
			}
		}
	}

	// Patrol-learned tracks (feature_geometries type=road) — client data,
	// omitted in the test tenant.
	var roadRows *sql.Rows
	if !isTestEnv(r) {
		roadRows, _ = s.DB.Query(`SELECT geojson, properties_json FROM feature_geometries WHERE park_id = ? AND feature_type = 'road' LIMIT 500`, parkID)
	}
	if roadRows != nil {
		defer roadRows.Close()
		i := 0
		for roadRows.Next() {
			var geojson, props string
			roadRows.Scan(&geojson, &props)
			i++
			for _, path := range extractPaths(geojson) {
				tdb.addTrack(gPatrol, fmt.Sprintf("Patrol track %d", i), path, locusTrackStyle(0xFF8BC34A, 2), false)
			}
		}
	}

	// Waterbodies (closed rings) + lakes as waypoints
	wbRows, _ := s.DB.Query(`SELECT waterbody_id, name, waterbody_type, lat, lon, geojson FROM park_waterbodies WHERE park_id = ? LIMIT 500`, parkID)
	if wbRows != nil {
		defer wbRows.Close()
		for wbRows.Next() {
			var wbID, wbName, wbType, geojson string
			var lat, lon float64
			wbRows.Scan(&wbID, &wbName, &wbType, &lat, &lon, &geojson)
			label := strOr(wbName, wbType)
			paths := extractPaths(geojson)
			for _, path := range paths {
				tdb.addTrack(gWater, label, path, locusTrackStyle(0xFF03A9F4, 1), false)
			}
			if len(paths) == 0 && lat != 0 {
				wdb.addWaypoint(wLakes, label, lon, lat, "", false)
			}
		}
	}
	lakeRows, _ := s.DB.Query(`SELECT hylak_id, name, area_km2, centroid_lat, centroid_lon, geojson FROM park_lakes_hydro WHERE park_id = ? ORDER BY area_km2 DESC LIMIT 50`, parkID)
	if lakeRows != nil {
		defer lakeRows.Close()
		for lakeRows.Next() {
			var hylakID int64
			var name, geojson sql.NullString
			var areaKm2, lat, lon float64
			lakeRows.Scan(&hylakID, &name, &areaKm2, &lat, &lon, &geojson)
			label := strOr(name.String, fmt.Sprintf("Lake %d", hylakID))
			wdb.addWaypoint(wLakes, fmt.Sprintf("%s (%.1f km2)", label, areaKm2), lon, lat, "", false)
			if geojson.Valid {
				for _, path := range extractPaths(geojson.String) {
					tdb.addTrack(gWater, label, path, locusTrackStyle(0xFF03A9F4, 1), false)
				}
			}
		}
	}

	// Airstrips (learned from patrol GPX) — point waypoints. Client data,
	// omitted in the test tenant.
	var airRows *sql.Rows
	if !isTestEnv(r) {
		airRows, _ = s.DB.Query(`SELECT geojson, properties_json FROM feature_geometries WHERE park_id = ? AND feature_type = 'airstrip' LIMIT 100`, parkID)
	}
	if airRows != nil {
		defer airRows.Close()
		i := 0
		for airRows.Next() {
			var geojson, props string
			airRows.Scan(&geojson, &props)
			lon, lat, ok := extractPoint(geojson)
			if !ok {
				continue
			}
			i++
			var pm map[string]interface{}
			json.Unmarshal([]byte(props), &pm)
			kind := "airstrip"
			if t, _ := pm["aircraft_type"].(string); t == "rotor_wing" {
				kind = "helipad"
			}
			wdb.addWaypoint(wAirstrips, fmt.Sprintf("Airstrip %d (%s)", i, kind), lon, lat, "transport-airport.png", false)
		}
	}

	// Places / villages
	placeRows, _ := s.DB.Query(`SELECT name, lat, lon, place_type FROM osm_places WHERE park_id = ? AND place_type NOT IN ('river', 'stream', 'lake') LIMIT 500`, parkID)
	if placeRows != nil {
		defer placeRows.Close()
		for placeRows.Next() {
			var name, placeType string
			var lat, lon float64
			placeRows.Scan(&name, &lat, &lon, &placeType)
			wdb.addWaypoint(wPlaces, fmt.Sprintf("%s (%s)", name, placeType), lon, lat, "", false)
		}
	}

	// ---- Mission content ----
	// Fire trajectories grouped per year (latest year visible)
	fireQuery := `SELECT geojson, properties_json, start_date, end_date FROM feature_geometries WHERE park_id = ? AND feature_type = 'fire_trajectory'`
	fireArgs := []interface{}{parkID}
	if fromDate != "" {
		fireQuery += " AND (end_date IS NULL OR end_date >= ?)"
		fireArgs = append(fireArgs, fromDate)
	}
	if toDate != "" {
		fireQuery += " AND (start_date IS NULL OR start_date <= ?)"
		fireArgs = append(fireArgs, toDate)
	}
	fireQuery += " ORDER BY start_date DESC LIMIT 500"
	fireGroups := map[string]int64{} // year -> group id
	latestFireYear := ""
	fireRows, _ := s.DB.Query(fireQuery, fireArgs...)
	if fireRows != nil {
		defer fireRows.Close()
		for fireRows.Next() {
			var geojson, props string
			var startDate, endDate sql.NullString
			fireRows.Scan(&geojson, &props, &startDate, &endDate)
			if strings.Contains(geojson, `"coordinates": [0.0,`) || strings.Contains(geojson, `"coordinates": [0,`) || strings.Contains(geojson, `[0.0, `) {
				continue
			}
			var propMap map[string]interface{}
			json.Unmarshal([]byte(props), &propMap)
			name := "Fire"
			if featureID, ok := propMap["feature_id"].(string); ok && featureID != "" {
				if parts := strings.Split(featureID, "_grp_"); len(parts) == 2 && len(parts[1]) >= 8 {
					name = "Fire " + parts[1][:8]
				}
			}
			if groupName, ok := propMap["group_name"].(string); ok && groupName != "" {
				name = groupName
			}
			year := "undated"
			if startDate.Valid && len(startDate.String) >= 4 {
				year = startDate.String[:4]
				name = name + " (" + startDate.String + ")"
			}
			gid, ok := fireGroups[year]
			if !ok {
				gid, _ = tdb.addGroup("FIRES "+year, 0, "ic_tracks", locusGroupStyle(0xFFFF3B30, 2), tMission)
				fireGroups[year] = gid
				if year > latestFireYear && year != "undated" {
					latestFireYear = year
				}
			}
			for _, path := range extractPaths(geojson) {
				// visibility fixed below (only latest year); mark later
				tdb.addTrack(gid, name, path, locusTrackStyle(0xFFFF3B30, 2), true) // trimmed to latest year below
			}
		}
	}
	// Fix visibility: keep only latest fire year in the visible list
	if latestFireYear != "" {
		latestGid := fireGroups[latestFireYear]
		var kept [][2]int64
		for _, pair := range tdb.visible {
			isFire := false
			for _, gid := range fireGroups {
				if pair[1] == gid {
					isFire = true
					break
				}
			}
			if !isFire || pair[1] == latestGid {
				kept = append(kept, pair)
			}
		}
		tdb.visible = kept
	}

	// Settlements (closed rings, hidden by default)
	settlementRows, _ := s.DB.Query(`
		SELECT fg.feature_id, fg.geojson, ps.classification, ps.nearest_place
		FROM feature_geometries fg
		LEFT JOIN park_settlements ps ON fg.park_id = ps.park_id
			AND (',' || ps.polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')
		WHERE fg.park_id = ? AND fg.feature_type = 'settlement' LIMIT 1000`, parkID)
	if settlementRows != nil {
		defer settlementRows.Close()
		for settlementRows.Next() {
			var featureID, geojson string
			var classification, nearestPlace sql.NullString
			settlementRows.Scan(&featureID, &geojson, &classification, &nearestPlace)
			name := strOr(classification.String, "Settlement")
			if nearestPlace.Valid && nearestPlace.String != "" {
				name += " near " + nearestPlace.String
			}
			for _, path := range extractPaths(geojson) {
				tdb.addTrack(gSettle, name, path, locusTrackStyle(0xFFFF9800, 2), false)
			}
		}
	}

	// Deforestation (closed rings, hidden by default)
	defoQuery := `
		SELECT fg.feature_id, fg.geojson, fg.start_date, de.classification, de.area_km2, de.year
		FROM feature_geometries fg
		LEFT JOIN deforestation_events de ON fg.park_id = de.park_id
			AND CAST(fg.properties_json->>'year' AS INTEGER) = de.year
			AND (',' || de.polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')
		WHERE fg.park_id = ? AND fg.feature_type = 'deforestation'`
	defoArgs := []interface{}{parkID}
	if fromDate != "" {
		defoQuery += " AND (fg.start_date IS NULL OR fg.start_date >= ?)"
		defoArgs = append(defoArgs, fromDate)
	}
	if toDate != "" {
		defoQuery += " AND (fg.start_date IS NULL OR fg.start_date <= ?)"
		defoArgs = append(defoArgs, toDate)
	}
	defoQuery += " LIMIT 1000"
	defoRows, _ := s.DB.Query(defoQuery, defoArgs...)
	if defoRows != nil {
		defer defoRows.Close()
		for defoRows.Next() {
			var featureID, geojson string
			var startDate, classification sql.NullString
			var areaKm2 sql.NullFloat64
			var year sql.NullInt64
			defoRows.Scan(&featureID, &geojson, &startDate, &classification, &areaKm2, &year)
			name := "Deforestation"
			if classification.Valid && classification.String != "" {
				name = classification.String
			}
			if year.Valid {
				name = fmt.Sprintf("%s (%d)", name, year.Int64)
			}
			for _, path := range extractPaths(geojson) {
				tdb.addTrack(gDefo, name, path, locusTrackStyle(0xFFE91E63, 2), false)
			}
		}
	}

	// Turbidity alerts (visible caution waypoints)
	for _, a := range loadTurbidityAlerts(parkID) {
		name := fmt.Sprintf("Turbidity %s %s", a.River, a.Date)
		wdb.addWaypoint(wTurb, name, a.Lon, a.Lat, "z-ico16.png", true)
	}

	// Drop empty leaf folders (e.g. parks without airstrips or patrol tracks)
	// so field users don't scroll past dead entries.
	tdb.db.Exec(`DELETE FROM groups WHERE mode = 0 AND parent_id > 0 AND _id NOT IN (SELECT DISTINCT parent_id FROM tracks)`)
	wdb.db.Exec(`DELETE FROM groups WHERE mode = 0 AND parent_id > 0 AND _id NOT IN (SELECT DISTINCT parent_id FROM waypoints)`)

	return nil
}

func strOr(s, def string) string {
	if s == "" {
		return def
	}
	return s
}
