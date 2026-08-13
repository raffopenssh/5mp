package srv

// The view export: "give me a GeoPackage of exactly what is on my screen".
//
// It exists because of the paused animator. Pausing an animation produces a
// specific, hard-won picture — this window, this viewport, these layers, this
// instant — and until now the only way to keep it was the GIF, i.e. a picture.
// The GeoPackage next to it hands over the same thing as DATA.
//
// Three deliberate differences from the area export (srv/gpkg_export.go):
//
//  1. The scope is a BBOX, not an area. A view is frequently a continent, half
//     a park, or a region no protected area covers; forcing it through an area
//     id would answer a different question. `AreaID` is optional here and only
//     narrows.
//
//  2. It carries the animation instant. `at` is written into the boundary-ish
//     metadata layer and used as the upper bound of the window, because the
//     paused frame shows what had happened BY THEN — an export that silently
//     included the rest of the window would not be the picture the user paused.
//
//  3. Only the layers that were on. The chips are the user's statement of what
//     the picture is about; exporting the other nine layers "because we have
//     them" turns a 4 MB answer into a 400 MB one and buries the subject.
//
// Everything else — styling, the embedded QGIS project, the R-tree, the job
// queue, the cache, the 21-day link — is shared with the area export. A view
// export is a different QUESTION, not a different mechanism.

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// gpkgViewOpts is the view export's question. It is folded into gpkgExportOpts
// (which the job queue understands) via viewOpts.
type gpkgViewOpts struct {
	BBox   [4]float64 `json:"bbox"`             // minLng, minLat, maxLng, maxLat
	At     string     `json:"at,omitempty"`     // animation instant (YYYY-MM-DD), "" for a static view
	Layers []string   `json:"layers,omitempty"` // animator chip names that were on
	AOIID  string     `json:"aoi,omitempty"`    // when the view is scoped to an AOI (aoiScopeSQL)
}

// viewLayerTables maps an animator chip to the export tables it implies. The
// chip names are the UI's vocabulary and this is the only place that knows how
// they translate — an export that silently renamed them would be a second
// vocabulary to keep in sync.
var viewLayerTables = map[string][]string{
	"trajs":       {"fire_trajectories"},
	"fireGrid":    {"fire_detections"},
	"firePts":     {"fire_detections"},
	"deforest":    {"deforestation"},
	"settlements": {"settlements"},
	"effortGrid":  {"patrol_effort"},
	"effortPts":   {"patrol_effort"},
	"infra":       {"roads", "rivers", "places", "waterbodies"},
}

func (v gpkgViewOpts) wants(table string) bool {
	if len(v.Layers) == 0 {
		return true
	}
	for _, l := range v.Layers {
		for _, t := range viewLayerTables[l] {
			if t == table {
				return true
			}
		}
	}
	return false
}

// bboxSQL restricts feature_geometries (which carries a precomputed bbox) to
// the view. Same predicate as /api/features-in-bbox, so the file holds exactly
// the rows the screen was drawn from.
func (v gpkgViewOpts) bboxSQL() (string, []interface{}) {
	return ` AND bbox_maxx >= ? AND bbox_minx <= ? AND bbox_maxy >= ? AND bbox_miny <= ?`,
		[]interface{}{v.BBox[0], v.BBox[2], v.BBox[1], v.BBox[3]}
}

func (v gpkgViewOpts) pointSQL(latCol, lonCol string) (string, []interface{}) {
	return fmt.Sprintf(" AND %s BETWEEN ? AND ? AND %s BETWEEN ? AND ?", latCol, lonCol),
		[]interface{}{v.BBox[1], v.BBox[3], v.BBox[0], v.BBox[2]}
}

// scopeSQL is the park_id clause. With an AOI it is that AOI's rows only; with
// an area id, that area's; otherwise every park EXCEPT the AOIs, which is the
// same default every bbox-keyed endpoint uses (aoiExcludeSQL) and for the same
// two reasons: a private polygon must not leak, and an AOI's rows would
// double-count the parks it overlaps.
func (v gpkgViewOpts) scopeSQL(areaID string) (string, []interface{}) {
	if v.AOIID != "" {
		return aoiScopeSQL("park_id", v.AOIID), nil
	}
	if areaID != "" {
		return " AND park_id = ?", []interface{}{areaID}
	}
	return aoiExcludeSQL("park_id"), nil
}

// buildViewGeoPackage writes the export for a viewport.
func (s *Server) buildViewGeoPackage(path string, o gpkgExportOpts) ([]gpkgLayerStat, error) {
	v := o.View
	if v == nil {
		return nil, fmt.Errorf("view export without a view")
	}
	w, err := newGPKGWriter(path)
	if err != nil {
		return nil, err
	}
	ok := false
	defer func() {
		if !ok {
			w.Close()
		}
	}()

	steps := []struct {
		label string
		fn    func() error
	}{
		{"view frame", func() error { return s.gpkgViewFrame(w, o) }},
		{"fire trajectories", func() error { return s.gpkgViewTrajectories(w, o) }},
		{"fire detections", func() error { return s.gpkgViewDetections(w, o) }},
		{"deforestation", func() error { return s.gpkgViewPolygons(w, o, "deforestation") }},
		{"settlements", func() error { return s.gpkgViewPolygons(w, o, "settlement") }},
		{"patrol effort", func() error { return s.gpkgViewEffort(w, o) }},
		{"context", func() error { return s.gpkgViewContext(w, o) }},
	}
	for i, st := range steps {
		if o.Progress != nil {
			o.Progress(float64(i)/float64(len(steps)), st.label)
		}
		if err := st.fn(); err != nil {
			slog.Warn("gpkg view layer failed", "layer", st.label, "err", err)
		}
	}
	if o.Progress != nil {
		o.Progress(0.97, "finalising")
	}

	stats := make([]gpkgLayerStat, 0, len(w.Layers()))
	present := map[string]bool{}
	for _, l := range w.Layers() {
		if l.Count() == 0 {
			continue
		}
		stats = append(stats, gpkgLayerStat{l.Name(), l.Count()})
		present[l.Name()] = true
	}
	// The canvas opens on the view — that is the whole point of this export.
	grow := math.Max(0.01, (v.BBox[2]-v.BBox[0])*0.02)
	ext := [4]float64{v.BBox[0] - grow, v.BBox[1] - grow, v.BBox[2] + grow, v.BBox[3] + grow}
	specs := gpkgProjectSpecs(present, false)
	if present["view_frame"] {
		specs = append(specs, gpkgLayerSpec{Table: "view_frame", Title: "Exported view",
			Group: "Reference", Geometry: "Polygon", WKBType: "Polygon",
			QML: styleViewFrame(), Visible: true})
	}
	if err := w.writeQGISProject(o.AreaName, filepath.Base(path), specs, ext); err != nil {
		slog.Warn("gpkg view project", "err", err)
	}
	if err := w.Finish(); err != nil {
		return nil, err
	}
	ok = true
	return stats, nil
}

// The view frame is one rectangle carrying the question the file answers.
// Without it the export is a pile of layers with no record of what was asked —
// and "which window was this?" is exactly what someone opening the file in
// three weeks needs, especially for a paused animation whose whole meaning is
// an instant.
func (s *Server) gpkgViewFrame(w *gpkgWriter, o gpkgExportOpts) error {
	v := o.View
	l, err := w.AddLayer("view_frame", "POLYGON", "The map view this export was taken from", []gpkgCol{
		{"title", "TEXT"}, {"area_id", "TEXT"}, {"window_from", "DATE"}, {"window_to", "DATE"},
		{"animation_at", "DATE"}, {"layers", "TEXT"},
		{"west", "REAL"}, {"south", "REAL"}, {"east", "REAL"}, {"north", "REAL"},
		{"exported_at", "DATETIME"},
	})
	if err != nil {
		return err
	}
	geo := fmt.Sprintf(`{"type":"Polygon","coordinates":[[[%g,%g],[%g,%g],[%g,%g],[%g,%g],[%g,%g]]]}`,
		v.BBox[0], v.BBox[1], v.BBox[2], v.BBox[1], v.BBox[2], v.BBox[3],
		v.BBox[0], v.BBox[3], v.BBox[0], v.BBox[1])
	l.Add(geo, o.AreaName, gpkgStr(o.AreaID), gpkgDate(o.FromDate), gpkgDate(o.ToDate),
		gpkgDate(v.At), gpkgStr(strings.Join(v.Layers, ",")),
		v.BBox[0], v.BBox[1], v.BBox[2], v.BBox[3],
		time.Now().UTC().Format("2006-01-02T15:04:05Z"))
	w.SetStyle("view_frame", styleViewFrame(), "The exported viewport")
	return nil
}

// The window's upper bound. For a paused animation it is the playhead: the
// picture shows what had happened by then, so including the rest of the window
// would export something the user never saw.
func (o gpkgExportOpts) viewTo() string {
	if o.View != nil && o.View.At != "" {
		return o.View.At
	}
	return o.ToDate
}

func (s *Server) gpkgViewTrajectories(w *gpkgWriter, o gpkgExportOpts) error {
	v := o.View
	if !v.wants("fire_trajectories") {
		return nil
	}
	l, err := w.AddLayer("fire_trajectories", "GEOMETRY",
		"VIIRS fire groups tracked over time (v5 pipeline)", []gpkgCol{
			{"feature_id", "TEXT"}, {"park_id", "TEXT"}, {"group_type", "TEXT"},
			{"start_date", "DATE"}, {"end_date", "DATE"}, {"year", "INTEGER"},
			{"days", "INTEGER"}, {"fires_total", "INTEGER"}, {"total_frp_mw", "REAL"},
			{"distance_km", "REAL"}, {"avg_speed_km_day", "REAL"}, {"direction", "TEXT"},
			{"position", "TEXT"}, {"pct_inside", "REAL"}, {"dist_to_park_km", "REAL"},
			{"season", "TEXT"}, {"nearest_place", "TEXT"}, {"nearest_river", "TEXT"},
			{"active_at_instant", "BOOLEAN"}, {"narrative", "TEXT"},
		})
	if err != nil {
		return err
	}
	q := `SELECT feature_id, park_id, geojson, COALESCE(properties_json,'{}'),
		COALESCE(start_date,''), COALESCE(end_date,'')
		FROM feature_geometries WHERE feature_type = 'fire_trajectory'`
	scope, args := v.scopeSQL(o.AreaID)
	q += scope
	bb, bargs := v.bboxSQL()
	q += bb
	args = append(args, bargs...)
	if o.FromDate != "" {
		q += " AND (end_date IS NULL OR end_date >= ?)"
		args = append(args, o.FromDate)
	}
	if to := o.viewTo(); to != "" {
		q += " AND (start_date IS NULL OR start_date <= ?)"
		args = append(args, to)
	}
	rows, err := s.DB.Query(q, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	at := o.View.At
	for rows.Next() {
		var fid, park, geojson, props, sd, ed string
		if rows.Scan(&fid, &park, &geojson, &props, &sd, &ed) != nil {
			continue
		}
		var p map[string]interface{}
		json.Unmarshal([]byte(props), &p)
		// "Was this group burning at the paused instant?" is the difference
		// between the bright heads and the ash on screen, so the file records
		// it rather than making the user recompute it from two dates.
		active := interface{}(nil)
		if at != "" {
			active = gpkgBool(sd != "" && sd <= at && (ed == "" || ed >= at))
		}
		l.Add(geojson, fid, park, gpkgJSONStr(p, "group_type"),
			gpkgDate(sd), gpkgDate(ed), gpkgJSONInt(p, "year"), gpkgJSONInt(p, "days"),
			gpkgJSONInt(p, "fires_total"), gpkgJSONNum(p, "total_frp"),
			gpkgJSONNum(p, "distance_km"), gpkgJSONNum(p, "avg_speed_km_day"),
			gpkgJSONStr(p, "direction"), gpkgJSONStr(p, "position"),
			gpkgJSONNum(p, "pct_inside"), gpkgJSONNum(p, "dist_to_park_km"),
			gpkgJSONStr(p, "season"), gpkgJSONStr(p, "nearest_place"),
			gpkgJSONStr(p, "nearest_river"), active, gpkgJSONStr(p, "narrative"))
	}
	w.SetStyle("fire_trajectories", styleFireTrajectory(), "Coloured by fire behaviour type")
	return nil
}

// gpkgViewDetectionsMax bounds the one layer that can run away: a continental
// view over three years is tens of millions of detections, i.e. a file nobody
// asked for behind a button labelled "the view". Above the cap the layer is
// omitted and SAID SO in the job's layer list, rather than silently truncated —
// a truncated fire layer is indistinguishable from a quiet fire season.
const gpkgViewDetectionsMax = 3000000

func (s *Server) gpkgViewDetections(w *gpkgWriter, o gpkgExportOpts) error {
	v := o.View
	if !v.wants("fire_detections") {
		return nil
	}
	to := o.viewTo()
	if est := s.estimateFireCount(strOr(o.FromDate, "2020-01-01"), strOr(to, time.Now().UTC().Format("2006-01-02")),
		v.BBox[1], v.BBox[3], v.BBox[0], v.BBox[2]); est > gpkgViewDetectionsMax {
		slog.Info("gpkg view: skipping raw detections", "estimate", est)
		return nil
	}
	l, err := w.AddLayer("fire_detections", "POINT",
		"Raw VIIRS active-fire detections in the exported view", []gpkgCol{
			{"acq_datetime_utc", "DATETIME"}, {"acq_date", "DATE"}, {"acq_time_utc", "TEXT"},
			{"latitude", "REAL"}, {"longitude", "REAL"}, {"brightness_k", "REAL"},
			{"frp_mw", "REAL"}, {"confidence", "TEXT"}, {"daynight", "TEXT"},
			{"satellite", "TEXT"}, {"protected_area_id", "TEXT"},
		})
	if err != nil {
		return err
	}
	q := `SELECT latitude, longitude, acq_date, COALESCE(acq_time,''), brightness, frp,
		COALESCE(confidence,''), COALESCE(daynight,''), COALESCE(satellite,''),
		COALESCE(protected_area_id,'')
		FROM fire_detections
		WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?`
	args := []interface{}{v.BBox[1], v.BBox[3], v.BBox[0], v.BBox[2]}
	if o.FromDate != "" {
		q += " AND acq_date >= ?"
		args = append(args, o.FromDate)
	}
	if to != "" {
		q += " AND acq_date <= ?"
		args = append(args, to)
	}
	rows, err := s.DB.Query(q, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var lat, lon float64
		var bright, frp sql.NullFloat64
		var date, atime, conf, dn, sat, paID string
		if rows.Scan(&lat, &lon, &date, &atime, &bright, &frp, &conf, &dn, &sat, &paID) != nil {
			continue
		}
		l.Add(fmt.Sprintf(`{"type":"Point","coordinates":[%g,%g]}`, lon, lat),
			gpkgDateTimeParts(date, atime), gpkgDate(date), gpkgStr(atime),
			lat, lon, gpkgNF(bright), gpkgNF(frp), gpkgStr(conf), gpkgStr(dn),
			gpkgStr(sat), gpkgStr(paID))
	}
	w.SetStyle("fire_detections", styleViewDetections(),
		"One point per satellite overpass detection")
	return nil
}

// Deforestation and settlements share a shape: polygons in feature_geometries
// whose text lives in a companion events table, keyed by polygon_ids. The
// per-park meta maps are reused (never the LIKE join — AGENTS.md).
func (s *Server) gpkgViewPolygons(w *gpkgWriter, o gpkgExportOpts, featureType string) error {
	v := o.View
	table := "deforestation"
	if featureType == "settlement" {
		table = "settlements"
	}
	if !v.wants(table) {
		return nil
	}
	cols := []gpkgCol{
		{"feature_id", "TEXT"}, {"park_id", "TEXT"}, {"start_date", "DATE"},
		{"year", "INTEGER"}, {"area_km2", "REAL"}, {"classification", "TEXT"},
		{"pattern_type", "TEXT"}, {"narrative", "TEXT"},
	}
	desc := "Canopy-loss polygons in the exported view"
	if featureType == "settlement" {
		cols = []gpkgCol{
			{"feature_id", "TEXT"}, {"park_id", "TEXT"}, {"area_m2", "REAL"},
			{"population_est", "INTEGER"}, {"classification", "TEXT"},
			{"nearest_place", "TEXT"}, {"distance_to_place_km", "REAL"}, {"narrative", "TEXT"},
		}
		desc = "Built-up polygons in the exported view"
	}
	l, err := w.AddLayer(table, "MULTIPOLYGON", desc, cols)
	if err != nil {
		return err
	}
	q := `SELECT feature_id, park_id, geojson, COALESCE(properties_json,'{}'), COALESCE(start_date,'')
		FROM feature_geometries WHERE feature_type = ?`
	args := []interface{}{featureType}
	scope, sargs := v.scopeSQL(o.AreaID)
	q += scope
	args = append(args, sargs...)
	bb, bargs := v.bboxSQL()
	q += bb
	args = append(args, bargs...)
	// Settlements carry no date, so a window filter would erase them.
	if featureType != "settlement" {
		if o.FromDate != "" {
			q += " AND (start_date IS NULL OR start_date >= ?)"
			args = append(args, o.FromDate)
		}
		if to := o.viewTo(); to != "" {
			q += " AND (start_date IS NULL OR start_date <= ?)"
			args = append(args, to)
		}
	}
	rows, err := s.DB.Query(q, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	var meta featureMetaCache
	for rows.Next() {
		var fid, park, geojson, props, sd string
		if rows.Scan(&fid, &park, &geojson, &props, &sd) != nil {
			continue
		}
		p := map[string]interface{}{}
		json.Unmarshal([]byte(props), &p)
		s.enrichFeatureProps(featureType, park, fid, p, &meta)
		if featureType == "settlement" {
			l.Add(geojson, fid, park, gpkgJSONNum(p, "area_m2"), gpkgJSONInt(p, "population_est"),
				gpkgJSONStr(p, "classification"), gpkgJSONStr(p, "nearest_place"),
				gpkgJSONNum(p, "distance_to_place_km"), gpkgJSONStr(p, "narrative"))
		} else {
			l.Add(geojson, fid, park, gpkgDate(sd), gpkgJSONInt(p, "year"),
				gpkgJSONNum(p, "area_km2"), gpkgJSONStr(p, "classification"),
				gpkgJSONStr(p, "pattern_type"), gpkgJSONStr(p, "narrative"))
		}
	}
	if featureType == "settlement" {
		w.SetStyle("settlements", styleSettlements(), "Coloured by settlement classification")
	} else {
		w.SetStyle("deforestation", styleDeforestation(), "Coloured by driver classification")
	}
	return nil
}

func (s *Server) gpkgViewEffort(w *gpkgWriter, o gpkgExportOpts) error {
	v := o.View
	if !v.wants("patrol_effort") || o.patrolTenant() != clientTenant {
		return nil
	}
	l, err := w.AddLayer("patrol_effort", "POLYGON",
		"Monthly patrol effort per grid cell in the exported view", []gpkgCol{
			{"grid_cell_id", "TEXT"}, {"period_start", "DATE"}, {"year", "INTEGER"},
			{"month", "INTEGER"}, {"distance_km", "REAL"}, {"points", "INTEGER"},
			{"latitude", "REAL"}, {"longitude", "REAL"},
		})
	if err != nil {
		return err
	}
	q := `SELECT e.grid_cell_id, e.year, e.month, SUM(e.total_distance_km), SUM(e.total_points),
			g.lat_min, g.lat_max, g.lon_min, g.lon_max, g.lat_center, g.lon_center
		FROM effort_data e JOIN grid_cells g ON e.grid_cell_id = g.id
		WHERE e.movement_type = 'all' AND e.env = ?
			AND g.lat_center BETWEEN ? AND ? AND g.lon_center BETWEEN ? AND ?`
	args := []interface{}{o.patrolTenant(), v.BBox[1], v.BBox[3], v.BBox[0], v.BBox[2]}
	if t, err := time.Parse("2006-01-02", o.FromDate); err == nil {
		q += " AND (e.year > ? OR (e.year = ? AND e.month >= ?))"
		args = append(args, t.Year(), t.Year(), int(t.Month()))
	}
	if t, err := time.Parse("2006-01-02", o.viewTo()); err == nil {
		q += " AND (e.year < ? OR (e.year = ? AND e.month <= ?))"
		args = append(args, t.Year(), t.Year(), int(t.Month()))
	}
	q += " GROUP BY e.grid_cell_id, e.year, e.month"
	rows, err := s.DB.Query(q, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var cellID string
		var year, month, points int
		var dist, latMin, latMax, lonMin, lonMax, latC, lonC float64
		if rows.Scan(&cellID, &year, &month, &dist, &points,
			&latMin, &latMax, &lonMin, &lonMax, &latC, &lonC) != nil {
			continue
		}
		geo := fmt.Sprintf(`{"type":"Polygon","coordinates":[[[%g,%g],[%g,%g],[%g,%g],[%g,%g],[%g,%g]]]}`,
			lonMin, latMin, lonMax, latMin, lonMax, latMax, lonMin, latMax, lonMin, latMin)
		l.Add(geo, cellID, fmt.Sprintf("%04d-%02d-01", year, month), year, month,
			math.Round(dist*100)/100, points, latC, lonC)
	}
	w.SetStyle("patrol_effort", stylePatrolEffort(), "Graduate on distance_km")
	return nil
}

// Context layers (roads, rivers, places, water) are what makes the exported
// picture legible as a place rather than as a scatter plot. They are park-keyed
// tables with no bbox column, so the filter is the row's own coordinates where
// it has them and the geometry's first vertex where it does not — cheap, and
// wrong only for a reach that leaves the view, which is the harmless direction.
func (s *Server) gpkgViewContext(w *gpkgWriter, o gpkgExportOpts) error {
	v := o.View
	if !v.wants("roads") {
		return nil
	}
	parks := s.parksInView(v, o.AreaID)
	if len(parks) == 0 {
		return nil
	}
	inView := func(lon, lat float64) bool {
		return lon >= v.BBox[0] && lon <= v.BBox[2] && lat >= v.BBox[1] && lat <= v.BBox[3]
	}

	rl, err := w.AddLayer("roads", "GEOMETRY", "Road network in the exported view", []gpkgCol{
		{"osm_id", "TEXT"}, {"name", "TEXT"}, {"highway_type", "TEXT"},
		{"surface", "TEXT"}, {"length_km", "REAL"},
	})
	if err != nil {
		return err
	}
	vl, err := w.AddLayer("rivers", "GEOMETRY", "River reaches in the exported view", []gpkgCol{
		{"hyriv_id", "INTEGER"}, {"name", "TEXT"}, {"stream_order", "INTEGER"}, {"length_km", "REAL"},
	})
	if err != nil {
		return err
	}
	pl, err := w.AddLayer("places", "POINT", "Named places in the exported view", []gpkgCol{
		{"osm_id", "TEXT"}, {"name", "TEXT"}, {"place_type", "TEXT"},
		{"latitude", "REAL"}, {"longitude", "REAL"},
	})
	if err != nil {
		return err
	}
	wl, err := w.AddLayer("waterbodies", "GEOMETRY", "Surface water in the exported view", []gpkgCol{
		{"waterbody_id", "TEXT"}, {"name", "TEXT"}, {"waterbody_type", "TEXT"},
	})
	if err != nil {
		return err
	}

	ph := strings.TrimSuffix(strings.Repeat("?,", len(parks)), ",")
	pargs := make([]interface{}, 0, len(parks)+4)
	for _, p := range parks {
		pargs = append(pargs, p)
	}

	if rows, err := s.DB.Query(`SELECT COALESCE(osm_id,''), COALESCE(name,''),
		COALESCE(highway_type,''), COALESCE(surface,''), length_km, geojson
		FROM roads_heigit WHERE park_id IN (`+ph+`) AND geojson IS NOT NULL`, pargs...); err == nil {
		for rows.Next() {
			var osmID, name, hw, surface, geojson string
			var lengthKm sql.NullFloat64
			if rows.Scan(&osmID, &name, &hw, &surface, &lengthKm, &geojson) != nil {
				continue
			}
			if !geojsonTouchesBBox(geojson, v.BBox) {
				continue
			}
			rl.Add(geojson, gpkgStr(osmID), gpkgStr(name), gpkgStr(hw), gpkgStr(surface), gpkgNF(lengthKm))
		}
		rows.Close()
	}
	w.SetStyle("roads", styleRoads(), "Coloured by highway class, labelled by name")

	if rows, err := s.DB.Query(`SELECT hyriv_id, COALESCE(name,''), stream_order, length_km, lat, lon, geojson
		FROM park_rivers_hydro WHERE park_id IN (`+ph+`) AND geojson IS NOT NULL`, pargs...); err == nil {
		for rows.Next() {
			var id int64
			var name, geojson string
			var order sql.NullInt64
			var length, lat, lon sql.NullFloat64
			if rows.Scan(&id, &name, &order, &length, &lat, &lon, &geojson) != nil {
				continue
			}
			if lat.Valid && lon.Valid && !inView(lon.Float64, lat.Float64) &&
				!geojsonTouchesBBox(geojson, v.BBox) {
				continue
			}
			vl.Add(geojson, id, gpkgStr(name), gpkgNI(order), gpkgNF(length))
		}
		rows.Close()
	}
	w.SetStyle("rivers", styleRivers(), "Labelled by name")

	qargs := append(append([]interface{}{}, pargs...), v.BBox[1], v.BBox[3], v.BBox[0], v.BBox[2])
	if rows, err := s.DB.Query(`SELECT COALESCE(osm_id,''), name, place_type, lat, lon
		FROM osm_places WHERE park_id IN (`+ph+`)
		  AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?`, qargs...); err == nil {
		for rows.Next() {
			var osmID, name, ptype string
			var lat, lon float64
			if rows.Scan(&osmID, &name, &ptype, &lat, &lon) != nil {
				continue
			}
			pl.Add(fmt.Sprintf(`{"type":"Point","coordinates":[%g,%g]}`, lon, lat),
				gpkgStr(osmID), gpkgStr(name), gpkgStr(ptype), lat, lon)
		}
		rows.Close()
	}
	w.SetStyle("places", stylePlaces(), "Labelled by name")

	if rows, err := s.DB.Query(`SELECT waterbody_id, COALESCE(name,''), COALESCE(waterbody_type,''), geojson
		FROM park_waterbodies WHERE park_id IN (`+ph+`) AND geojson IS NOT NULL`, pargs...); err == nil {
		for rows.Next() {
			var id, name, wtype, geojson string
			if rows.Scan(&id, &name, &wtype, &geojson) != nil {
				continue
			}
			if !geojsonTouchesBBox(geojson, v.BBox) {
				continue
			}
			wl.Add(geojson, gpkgStr(id), gpkgStr(name), gpkgStr(wtype))
		}
		rows.Close()
	}
	w.SetStyle("waterbodies", styleWater(), "")
	return nil
}

// parksInView: which areas' context tables are worth reading. An explicit area
// or AOI answers it directly; otherwise it is every protected area whose bbox
// meets the view, which at continental zoom is most of them — bounded, because
// the per-row bbox test then throws away what is off screen.
func (s *Server) parksInView(v *gpkgViewOpts, areaID string) []string {
	if v.AOIID != "" {
		return []string{v.AOIID}
	}
	if areaID != "" {
		return []string{areaID}
	}
	if s.AreaStore == nil {
		return nil
	}
	out := []string{}
	for i := range s.AreaStore.Areas {
		a := &s.AreaStore.Areas[i]
		latMin, latMax, lonMin, lonMax := a.GetBoundingBox()
		if lonMax < v.BBox[0] || lonMin > v.BBox[2] || latMax < v.BBox[1] || latMin > v.BBox[3] {
			continue
		}
		out = append(out, a.ID)
	}
	return out
}

// geojsonTouchesBBox is a coarse envelope test done on the raw JSON: parsing a
// 40k-vertex river reach to decide whether to keep it costs more than keeping
// it. It scans numbers pairwise, which is exact for the envelope of any
// coordinate array regardless of nesting.
func geojsonTouchesBBox(geojson string, bb [4]float64) bool {
	minx, miny := math.Inf(1), math.Inf(1)
	maxx, maxy := math.Inf(-1), math.Inf(-1)
	i, n := 0, len(geojson)
	odd := false
	for i < n {
		c := geojson[i]
		if c == '-' || c == '.' || (c >= '0' && c <= '9') {
			j := i
			for j < n {
				d := geojson[j]
				if d == '-' || d == '+' || d == '.' || d == 'e' || d == 'E' || (d >= '0' && d <= '9') {
					j++
					continue
				}
				break
			}
			var f float64
			if _, err := fmt.Sscanf(geojson[i:j], "%g", &f); err == nil {
				if !odd {
					minx, maxx = math.Min(minx, f), math.Max(maxx, f)
				} else {
					miny, maxy = math.Min(miny, f), math.Max(maxy, f)
				}
				odd = !odd
			}
			i = j
			continue
		}
		i++
	}
	if math.IsInf(minx, 1) {
		return true // unparseable: keep it rather than silently drop data
	}
	return maxx >= bb[0] && minx <= bb[2] && maxy >= bb[1] && miny <= bb[3]
}

// ---- handler -------------------------------------------------------------

// HandleAPIViewGeoPackage — POST/GET /api/view/export.gpkg
//
//	?bbox=w,s,e,n &from &to &at=YYYY-MM-DD &layers=trajs,deforest &aoi=<id>
//	&area=<park id>   (optional; narrows to one area's rows)
//	&peek=1           (side-effect free lookup, same contract as the area one)
//
// It is not under /api/parks/* or /api/aois/* because a view is not an area:
// the common case for this button is a paused animation over three countries.
func (s *Server) HandleAPIViewGeoPackage(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	parts := strings.Split(q.Get("bbox"), ",")
	if len(parts) != 4 {
		http.Error(w, "bbox required: minLng,minLat,maxLng,maxLat", http.StatusBadRequest)
		return
	}
	v := &gpkgViewOpts{At: q.Get("at")}
	for i, p := range parts {
		f, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
		if err != nil {
			http.Error(w, "invalid bbox", http.StatusBadRequest)
			return
		}
		v.BBox[i] = f
	}
	if v.BBox[2] < v.BBox[0] {
		v.BBox[0], v.BBox[2] = v.BBox[2], v.BBox[0]
	}
	if v.BBox[3] < v.BBox[1] {
		v.BBox[1], v.BBox[3] = v.BBox[3], v.BBox[1]
	}
	if l := q.Get("layers"); l != "" {
		for _, name := range strings.Split(l, ",") {
			if name = strings.TrimSpace(name); name != "" && viewLayerTables[name] != nil {
				v.Layers = append(v.Layers, name)
			}
		}
	}
	// ?aoi= is re-checked against visibility, never trusted (srv/aoi.go).
	v.AOIID = s.aoiScopeParam(r)

	areaID := q.Get("area")
	if areaID != "" && IsAOIID(areaID) {
		// An AOI id here means the same thing as ?aoi=, but only if visible.
		if v.AOIID != areaID {
			areaID = ""
		}
	}
	name := "Map view"
	if v.AOIID != "" {
		// The job row carries the AOI id, because visibility on status and
		// download is checked from it (loadGeoPackageJob). An is_aoi job with an
		// empty area id would 404 for its own owner.
		areaID = v.AOIID
		if n, _ := s.resolveAreaGeom(v.AOIID); n != "" {
			name = n + " — view"
		}
	} else if areaID != "" {
		if n, _ := s.resolveAreaGeom(areaID); n != "" {
			name = n + " — view"
		}
	}

	o := gpkgExportOpts{
		AreaID:    areaID,
		AreaName:  name,
		FromDate:  q.Get("from"),
		ToDate:    q.Get("to"),
		Env:       RequestEnv(r),
		PatrolEnv: PatrolEnv(r),
		Effort:    q.Get("effort") != "0",
		RawFire:   q.Get("raw") != "0",
		View:      v,
	}
	if q.Get("peek") == "1" {
		j := s.findGeoPackageJob(gpkgKeyFor(o))
		w.Header().Set("Cache-Control", "no-store")
		if j == nil {
			http.Error(w, "no export for this view", http.StatusNotFound)
			return
		}
		j.Cached = j.State == "ready"
		writeJSON(w, http.StatusOK, j)
		return
	}
	job, err := s.startGeoPackageJob(o, v.AOIID != "", s.RequestPrincipalID(r), q.Get("refresh") == "1")
	if err != nil {
		slog.Warn("view geopackage job", "err", err)
		http.Error(w, "could not start export", http.StatusInternalServerError)
		return
	}
	// "Immediate when fast, a notification when it is not."
	//
	// A view export is usually a few MB of one screen — ready before the user
	// has looked at the bell — but the same button over a continental view with
	// raw detections on is minutes. The client cannot know which it asked for,
	// and neither can the server before it has tried, so ?wait= lets it hold the
	// request briefly and answer with whatever is true then. Nothing about the
	// job changes: the card is written at queue time either way, so a fast
	// export is still in the bell, still deletable, still expires in 21 days.
	if ws := q.Get("wait"); ws != "" && job.State != "ready" {
		if secs, err := strconv.ParseFloat(ws, 64); err == nil && secs > 0 {
			if j := s.waitForGeoPackageJob(job.ID, time.Duration(secs*float64(time.Second))); j != nil {
				job = j
			}
		}
	}
	writeJSON(w, http.StatusOK, job)
}
