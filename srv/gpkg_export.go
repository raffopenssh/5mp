package srv

// Builds the "GeoPackage" download: every data layer the app holds for one area
// (park or AOI), in one .gpkg, styled for QGIS.
//
// It is deliberately the *same content* as the KML export, plus the things KML
// cannot carry honestly: raw fire detections (millions of points), typed
// numeric attributes, and per-layer symbology. KML's <ExtendedData> is all
// strings, so a Google Earth user can look at a fire but not sort by FRP; a
// GeoPackage user gets REAL frp, DATE start_date and a working temporal
// controller. That is the whole reason the format exists here.
//
// Every layer is exported WHOLE — no LIMIT. The geography-layer post-mortem in
// AGENTS.md ("Geography layers are served whole and cached") applies twice over
// to a download: a truncated file is indistinguishable from a complete one once
// it is off the server and in someone's QGIS project. Size is not a constraint
// (a 1 GB .gpkg is fine); silence about missing rows is.

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"path/filepath"
	"strings"
	"time"
)

// gpkgProgress reports which layer is being written, 0..1.
type gpkgProgress func(frac float64, label string)

type gpkgExportOpts struct {
	AreaID   string
	AreaName string
	FromDate string
	ToDate   string
	Env      string // tenant (RequestEnv); non-client tenants get no patrol layers
	Effort   bool   // include patrol effort (expensive, and empty for most AOIs)
	// RawFire includes the raw VIIRS detection points. They are the single
	// biggest layer by an order of magnitude (XSA: 6.9M points, ~1.1 GB of a
	// 1.4 GB file) while fire_trajectories tells the same story in 38k
	// features, so leaving them out is a legitimate, much smaller export —
	// not a degraded one.
	RawFire  bool
	Progress gpkgProgress
}

type gpkgLayerStat struct {
	Name  string `json:"name"`
	Count int    `json:"count"`
}

// buildAreaGeoPackage writes path and returns per-layer counts.
func (s *Server) buildAreaGeoPackage(path string, o gpkgExportOpts) ([]gpkgLayerStat, error) {
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

	name, boundary := s.resolveAreaGeom(o.AreaID)
	if o.AreaName == "" {
		o.AreaName = name
	}
	isAOI := IsAOIID(o.AreaID)

	steps := []struct {
		label string
		fn    func() error
	}{
		{"boundary", func() error { return s.gpkgBoundary(w, o, boundary, isAOI) }},
		{"fire trajectories", func() error { return s.gpkgFireTrajectories(w, o) }},
		{"fire detections", func() error { return s.gpkgFireDetections(w, o, boundary) }},
		{"deforestation", func() error { return s.gpkgDeforestation(w, o) }},
		{"settlements", func() error { return s.gpkgSettlements(w, o) }},
		{"rivers", func() error { return s.gpkgRivers(w, o) }},
		{"roads", func() error { return s.gpkgRoads(w, o) }},
		{"places", func() error { return s.gpkgPlaces(w, o) }},
		{"water", func() error { return s.gpkgWater(w, o) }},
		{"watersheds", func() error { return s.gpkgBasins(w, o) }},
		{"patrol data", func() error { return s.gpkgPatrol(w, o, boundary) }},
	}
	for i, st := range steps {
		if o.Progress != nil {
			o.Progress(float64(i)/float64(len(steps)), st.label)
		}
		if err := st.fn(); err != nil {
			slog.Warn("gpkg layer failed", "area", o.AreaID, "layer", st.label, "err", err)
		}
	}
	if o.Progress != nil {
		o.Progress(0.97, "finalising")
	}

	stats := make([]gpkgLayerStat, 0, len(w.Layers()))
	present := map[string]bool{}
	var ext [4]float64
	first := true
	for _, l := range w.Layers() {
		if l.Count() == 0 {
			continue
		}
		stats = append(stats, gpkgLayerStat{l.Name(), l.Count()})
		present[l.Name()] = true
		if first {
			ext, first = [4]float64{l.minx, l.miny, l.maxx, l.maxy}, false
		}
	}
	// The canvas opens on the area itself, not on the union of every layer:
	// fire detections and watersheds both reach far outside it, and a first
	// view framed on a 600 km watershed does not look like the area that was
	// asked for. Falls back to the first layer's extent.
	if _, bbox, ok := s.resolveAreaBBox(o.AreaID); ok {
		ext = bbox
	}
	grow := math.Max(0.02, (ext[2]-ext[0])*0.05)
	ext = [4]float64{ext[0] - grow, ext[1] - grow, ext[2] + grow, ext[3] + grow}

	if err := w.writeQGISProject(o.AreaName, filepath.Base(path), gpkgProjectSpecs(present, isAOI), ext); err != nil {
		slog.Warn("gpkg project", "area", o.AreaID, "err", err)
	}
	if err := w.Finish(); err != nil {
		return nil, err
	}
	ok = true
	return stats, nil
}

// ---- layers --------------------------------------------------------------

func (s *Server) gpkgBoundary(w *gpkgWriter, o gpkgExportOpts, boundary string, isAOI bool) error {
	if boundary == "" {
		return nil
	}
	tbl := "boundary"
	style := styleBoundary()
	kind := "protected area"
	if isAOI {
		tbl = "aoi_boundary"
		style = styleAOI()
		kind = "area of interest"
	}
	l, err := w.AddLayer(tbl, "MULTIPOLYGON", o.AreaName+" boundary", []gpkgCol{
		{"area_id", "TEXT"}, {"area_name", "TEXT"}, {"area_kind", "TEXT"},
		{"window_from", "DATE"}, {"window_to", "DATE"},
		{"exported_at", "DATETIME"},
	})
	if err != nil {
		return err
	}
	l.Add(boundary, o.AreaID, o.AreaName, kind,
		gpkgDate(o.FromDate), gpkgDate(o.ToDate),
		time.Now().UTC().Format("2006-01-02T15:04:05Z"))
	w.SetStyle(tbl, style, "5MP "+kind+" outline")
	return nil
}

func (s *Server) gpkgFireTrajectories(w *gpkgWriter, o gpkgExportOpts) error {
	l, err := w.AddLayer("fire_trajectories", "GEOMETRY",
		"VIIRS fire groups tracked over time (v5 pipeline)", []gpkgCol{
			{"feature_id", "TEXT"},
			{"group_name", "TEXT"},
			{"group_type", "TEXT"},
			{"start_date", "DATE"},
			{"end_date", "DATE"},
			{"year", "INTEGER"},
			{"days", "INTEGER"},
			{"fires_total", "INTEGER"},
			{"total_frp_mw", "REAL"},
			{"distance_km", "REAL"},
			{"avg_speed_km_day", "REAL"},
			{"direction", "TEXT"},
			{"position", "TEXT"},
			{"pct_inside", "REAL"},
			{"dist_to_park_km", "REAL"},
			{"cross_border", "BOOLEAN"},
			{"season", "TEXT"},
			{"trajectory_type", "TEXT"},
			{"zigzag_ratio", "REAL"},
			{"nearest_place", "TEXT"},
			{"nearest_place_dist_km", "REAL"},
			{"nearest_river", "TEXT"},
			{"nearest_river_dist_km", "REAL"},
			{"narrative", "TEXT"},
		})
	if err != nil {
		return err
	}
	q := `SELECT geojson, COALESCE(properties_json,'{}'), COALESCE(start_date,''), COALESCE(end_date,'')
		FROM feature_geometries WHERE park_id = ? AND feature_type = 'fire_trajectory'`
	args := []interface{}{o.AreaID}
	if o.FromDate != "" {
		q += " AND (end_date IS NULL OR end_date >= ?)"
		args = append(args, o.FromDate)
	}
	if o.ToDate != "" {
		q += " AND (start_date IS NULL OR start_date <= ?)"
		args = append(args, o.ToDate)
	}
	q += " ORDER BY start_date"
	rows, err := s.DB.Query(q, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var geojson, props, sd, ed string
		if rows.Scan(&geojson, &props, &sd, &ed) != nil {
			continue
		}
		var p map[string]interface{}
		json.Unmarshal([]byte(props), &p)
		l.Add(geojson,
			gpkgJSONStr(p, "feature_id"),
			gpkgJSONStr(p, "group_name"),
			gpkgJSONStr(p, "group_type"),
			gpkgDate(sd), gpkgDate(ed),
			gpkgJSONInt(p, "year"),
			gpkgJSONInt(p, "days"),
			gpkgJSONInt(p, "fires_total"),
			gpkgJSONNum(p, "total_frp"),
			gpkgJSONNum(p, "distance_km"),
			gpkgJSONNum(p, "avg_speed_km_day"),
			gpkgJSONStr(p, "direction"),
			gpkgJSONStr(p, "position"),
			gpkgJSONNum(p, "pct_inside"),
			gpkgJSONNum(p, "dist_to_park_km"),
			gpkgJSONBool(p, "cross_border"),
			gpkgJSONStr(p, "season"),
			gpkgJSONStr(p, "trajectory_type"),
			gpkgJSONNum(p, "zigzag_ratio"),
			gpkgJSONStr(p, "nearest_place"),
			gpkgJSONNum(p, "nearest_place_dist"),
			gpkgJSONStr(p, "nearest_river"),
			gpkgJSONNum(p, "nearest_river_dist"),
			gpkgJSONStr(p, "narrative"),
		)
	}
	w.SetStyle("fire_trajectories", styleFireTrajectory(), "Coloured by fire behaviour type")
	return nil
}

// Raw VIIRS detections. These are keyed by coordinate, not by area, so the
// filter is the area's bbox — the same scope the map's own fire layers use.
// This is the single biggest layer (millions of rows for a large AOI over a
// multi-year window), which is why the export runs as a background job.
func (s *Server) gpkgFireDetections(w *gpkgWriter, o gpkgExportOpts, boundary string) error {
	if !o.RawFire {
		return nil
	}
	_, bbox, ok := s.resolveAreaBBox(o.AreaID)
	if !ok {
		return nil
	}
	l, err := w.AddLayer("fire_detections", "POINT",
		"Raw VIIRS active-fire detections (375 m, NOAA-20/SNPP/NOAA-21)", []gpkgCol{
			{"acq_datetime_utc", "DATETIME"},
			{"acq_date", "DATE"},
			{"acq_time_utc", "TEXT"},
			{"latitude", "REAL"},
			{"longitude", "REAL"},
			{"brightness_k", "REAL"},
			{"bright_t31_k", "REAL"},
			{"frp_mw", "REAL"},
			{"confidence", "TEXT"},
			{"daynight", "TEXT"},
			{"satellite", "TEXT"},
			{"instrument", "TEXT"},
			{"version", "TEXT"},
			{"scan", "REAL"},
			{"track", "REAL"},
			{"in_protected_area", "BOOLEAN"},
			{"protected_area_id", "TEXT"},
			{"in_area", "BOOLEAN"},
		})
	if err != nil {
		return err
	}
	q := `SELECT latitude, longitude, acq_date, COALESCE(acq_time,''), brightness, bright_t31, frp,
		COALESCE(confidence,''), COALESCE(daynight,''), COALESCE(satellite,''), COALESCE(instrument,''),
		COALESCE(version,''), scan, track, COALESCE(in_protected_area,0), COALESCE(protected_area_id,'')
		FROM fire_detections
		WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?`
	args := []interface{}{bbox[1], bbox[3], bbox[0], bbox[2]}
	if o.FromDate != "" {
		q += " AND acq_date >= ?"
		args = append(args, o.FromDate)
	}
	if o.ToDate != "" {
		q += " AND acq_date <= ?"
		args = append(args, o.ToDate)
	}
	// Detections are keyed by coordinate, so the indexed query is the bbox and
	// the polygon is applied here (see srv/gpkg_inarea.go). Rows outside the
	// polygon are KEPT and flagged rather than dropped: fires approaching a
	// boundary are exactly what people look for, and the fire narratives keep
	// context out to 20 km for the same reason.
	hit := newAreaHitTest(boundary)
	rows, err := s.DB.Query(q, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var lat, lon float64
		var bright, bt31, frp, scan, track sql.NullFloat64
		var date, atime, conf, dn, sat, instr, ver, paID string
		var inPA int
		if rows.Scan(&lat, &lon, &date, &atime, &bright, &bt31, &frp, &conf, &dn, &sat,
			&instr, &ver, &scan, &track, &inPA, &paID) != nil {
			continue
		}
		l.Add(fmt.Sprintf(`{"type":"Point","coordinates":[%g,%g]}`, lon, lat),
			gpkgDateTimeParts(date, atime), gpkgDate(date), gpkgStr(atime),
			lat, lon, gpkgNF(bright), gpkgNF(bt31), gpkgNF(frp),
			gpkgStr(conf), gpkgStr(dn), gpkgStr(sat), gpkgStr(instr), gpkgStr(ver),
			gpkgNF(scan), gpkgNF(track), inPA, gpkgStr(paID), gpkgBool(hit.Contains(lon, lat)))
	}
	w.SetStyle("fire_detections", styleFireDetections(),
		"One point per satellite overpass detection; filter or animate on acq_datetime_utc")
	return nil
}

func (s *Server) gpkgDeforestation(w *gpkgWriter, o gpkgExportOpts) error {
	l, err := w.AddLayer("deforestation", "MULTIPOLYGON",
		"Canopy-loss polygons (Hansen <=2023, GFW integrated alerts >=2024)", []gpkgCol{
			{"feature_id", "TEXT"},
			{"loss_year", "INTEGER"},
			{"start_date", "DATE"},
			{"end_date", "DATE"},
			{"area_km2", "REAL"},
			{"pixel_count", "INTEGER"},
			{"classification", "TEXT"},
			{"classification_confidence", "REAL"},
			{"pattern_type", "TEXT"},
			{"fires_same_year", "INTEGER"},
			{"fire_ratio", "REAL"},
			{"nearest_settlement_km", "REAL"},
			{"narrative", "TEXT"},
			{"classified_at", "DATETIME"},
		})
	if err != nil {
		return err
	}
	// polygon_ids is a comma-separated list; joining on it in SQL is the
	// documented O(events x polygons) trap. One scan of the small events
	// table, split in Go (same shape as srv/feature_meta.go).
	type defoMeta struct {
		class, pattern, narrative, classifiedAt string
		year                                    sql.NullInt64
		area, conf, fireRatio, nearestSettle    sql.NullFloat64
		firesSameYear, pixels                   sql.NullInt64
	}
	meta := map[string]*defoMeta{}
	mrows, _ := s.DB.Query(`SELECT COALESCE(polygon_ids,''), year, area_km2, COALESCE(classification,''),
		COALESCE(classification_confidence,0), COALESCE(pattern_type,''), fires_same_year, fire_ratio,
		nearest_settlement_km, COALESCE(narrative,''), COALESCE(classified_at,''), pixel_count
		FROM deforestation_events WHERE park_id = ?`, o.AreaID)
	if mrows != nil {
		defer mrows.Close()
		for mrows.Next() {
			var ids, class, pattern, narrative, cAt string
			var year sql.NullInt64
			var area, conf, fr, ns sql.NullFloat64
			var fsy, px sql.NullInt64
			if mrows.Scan(&ids, &year, &area, &class, &conf, &pattern, &fsy, &fr, &ns, &narrative, &cAt, &px) != nil {
				continue
			}
			m := &defoMeta{class: class, pattern: pattern, narrative: narrative, classifiedAt: cAt,
				year: year, area: area, conf: conf, fireRatio: fr, nearestSettle: ns,
				firesSameYear: fsy, pixels: px}
			for _, id := range strings.Split(ids, ",") {
				if id = strings.TrimSpace(id); id != "" {
					meta[id] = m
				}
			}
		}
	}
	q := `SELECT feature_id, geojson, COALESCE(properties_json,'{}'), COALESCE(start_date,''), COALESCE(end_date,'')
		FROM feature_geometries WHERE park_id = ? AND feature_type = 'deforestation'`
	args := []interface{}{o.AreaID}
	if o.FromDate != "" {
		q += " AND (start_date IS NULL OR start_date >= ?)"
		args = append(args, o.FromDate)
	}
	if o.ToDate != "" {
		q += " AND (start_date IS NULL OR start_date <= ?)"
		args = append(args, o.ToDate)
	}
	rows, err := s.DB.Query(q, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var fid, geojson, props, sd, ed string
		if rows.Scan(&fid, &geojson, &props, &sd, &ed) != nil {
			continue
		}
		var p map[string]interface{}
		json.Unmarshal([]byte(props), &p)
		m := meta[fid]
		var class, pattern, narrative, cAt interface{}
		var conf, fr, ns, area interface{}
		var fsy, px, year interface{}
		area = gpkgJSONNum(p, "area_km2")
		year = gpkgJSONInt(p, "year")
		if m != nil {
			class, pattern, narrative = gpkgStr(m.class), gpkgStr(m.pattern), gpkgStr(m.narrative)
			cAt = gpkgDateTime(m.classifiedAt)
			conf, fr, ns = gpkgNF(m.conf), gpkgNF(m.fireRatio), gpkgNF(m.nearestSettle)
			fsy, px = gpkgNI(m.firesSameYear), gpkgNI(m.pixels)
			if year == nil {
				year = gpkgNI(m.year)
			}
		}
		l.Add(geojson, fid, year, gpkgDate(sd), gpkgDate(ed), area, px,
			class, conf, pattern, fsy, fr, ns, narrative, cAt)
	}
	w.SetStyle("deforestation", styleDeforestation(), "Coloured by driver classification")
	return nil
}

func (s *Server) gpkgSettlements(w *gpkgWriter, o gpkgExportOpts) error {
	l, err := w.AddLayer("settlements", "MULTIPOLYGON",
		"Built-up polygons (GHSL), clustered and classified", []gpkgCol{
			{"feature_id", "TEXT"},
			{"classification", "TEXT"},
			{"classification_confidence", "REAL"},
			{"settlement_type", "TEXT"},
			{"area_m2", "REAL"},
			{"population_est", "INTEGER"},
			{"households_est", "INTEGER"},
			{"nearest_place", "TEXT"},
			{"distance_to_place_km", "REAL"},
			{"direction_from_place", "TEXT"},
			{"fires_1km", "INTEGER"},
			{"fires_5km", "INTEGER"},
			{"fire_seasonality", "TEXT"},
			{"deforest_nearby_km2", "REAL"},
			{"in_buffer", "BOOLEAN"},
			{"narrative", "TEXT"},
			{"detected_at", "DATETIME"},
			{"classified_at", "DATETIME"},
		})
	if err != nil {
		return err
	}
	type setMeta struct {
		class, sType, place, dir, seasonality, narrative, detected, classified string
		conf, distPlace, defoNearby                                            sql.NullFloat64
		pop, hh, f1, f5, inBuf                                                 sql.NullInt64
	}
	meta := map[string]*setMeta{}
	mq := `SELECT COALESCE(polygon_ids,''), COALESCE(classification,''), classification_confidence,
		COALESCE(settlement_type,''), population_est, households_est, COALESCE(nearest_place,''),
		distance_to_place_km, COALESCE(direction_from_place,''), fires_1km, fires_5km,
		COALESCE(fire_seasonality,''), deforest_nearby_km2, in_buffer, COALESCE(narrative,''),
		COALESCE(detected_at,''), COALESCE(classified_at,'')
		FROM park_settlements WHERE park_id = ?` + scannerInjectedSQLFilter("narrative")
	mrows, _ := s.DB.Query(mq, o.AreaID)
	if mrows != nil {
		defer mrows.Close()
		for mrows.Next() {
			var ids string
			m := &setMeta{}
			if mrows.Scan(&ids, &m.class, &m.conf, &m.sType, &m.pop, &m.hh, &m.place, &m.distPlace,
				&m.dir, &m.f1, &m.f5, &m.seasonality, &m.defoNearby, &m.inBuf, &m.narrative,
				&m.detected, &m.classified) != nil {
				continue
			}
			m.class = publicSettlementClass(m.class)
			m.narrative = publicSettlementNarrative(m.class, m.narrative)
			for _, id := range strings.Split(ids, ",") {
				if id = strings.TrimSpace(id); id != "" {
					meta[id] = m
				}
			}
		}
	}
	rows, err := s.DB.Query(`SELECT feature_id, geojson, COALESCE(properties_json,'{}')
		FROM feature_geometries WHERE park_id = ? AND feature_type = 'settlement'`, o.AreaID)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var fid, geojson, props string
		if rows.Scan(&fid, &geojson, &props) != nil {
			continue
		}
		var p map[string]interface{}
		json.Unmarshal([]byte(props), &p)
		m := meta[fid]
		vals := []interface{}{fid, nil, nil, nil, gpkgJSONNum(p, "area_m2"), gpkgJSONInt(p, "population_est"),
			nil, nil, nil, nil, nil, nil, nil, nil, nil, nil, nil, nil}
		if m != nil {
			vals[1], vals[2], vals[3] = gpkgStr(m.class), gpkgNF(m.conf), gpkgStr(m.sType)
			if vals[5] == nil {
				vals[5] = gpkgNI(m.pop)
			}
			vals[6] = gpkgNI(m.hh)
			vals[7], vals[8], vals[9] = gpkgStr(m.place), gpkgNF(m.distPlace), gpkgStr(m.dir)
			vals[10], vals[11], vals[12] = gpkgNI(m.f1), gpkgNI(m.f5), gpkgStr(m.seasonality)
			vals[13], vals[14] = gpkgNF(m.defoNearby), gpkgNI(m.inBuf)
			vals[15] = gpkgStr(m.narrative)
			vals[16], vals[17] = gpkgDateTime(m.detected), gpkgDateTime(m.classified)
		}
		l.Add(geojson, vals...)
	}
	w.SetStyle("settlements", styleSettlements(), "Coloured by settlement classification")
	return nil
}

func (s *Server) gpkgRivers(w *gpkgWriter, o gpkgExportOpts) error {
	// Raw reaches, whole layer: the field-facing exports merge them into
	// continuous polylines, but a GIS user wants the reach ids and orders
	// intact so they can join to HydroRIVERS. The merged version ships too.
	l, err := w.AddLayer("rivers", "GEOMETRY",
		"River reaches (HydroRIVERS, or OSM waterways where negative hyriv_id)", []gpkgCol{
			{"hyriv_id", "INTEGER"},
			{"name", "TEXT"},
			{"stream_order", "INTEGER"},
			{"ord_flow", "INTEGER"},
			{"length_km", "REAL"},
			{"source", "TEXT"},
		})
	if err != nil {
		return err
	}
	rows, err := s.DB.Query(`SELECT hyriv_id, COALESCE(name,''), stream_order, ord_flow, length_km, geojson
		FROM park_rivers_hydro WHERE park_id = ? AND geojson IS NOT NULL`, o.AreaID)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var id int64
			var name, geojson string
			var order, ordFlow sql.NullInt64
			var lengthKm sql.NullFloat64
			if rows.Scan(&id, &name, &order, &ordFlow, &lengthKm, &geojson) != nil {
				continue
			}
			src := "HydroRIVERS"
			if id < 0 {
				src = "OSM"
			}
			l.Add(geojson, id, gpkgStr(name), gpkgNI(order), gpkgNI(ordFlow), gpkgNF(lengthKm), src)
		}
	}
	w.SetStyle("rivers", styleRivers(), "Labelled by name")

	ml, err := w.AddLayer("rivers_merged", "LINESTRING",
		"Named rivers with touching reaches chained into continuous polylines", []gpkgCol{
			{"name", "TEXT"}, {"stream_order", "INTEGER"}, {"length_km", "REAL"},
		})
	if err != nil {
		return err
	}
	for _, rv := range s.loadMergedRivers(o.AreaID, 3, 100000) {
		ml.Add(lineGeoJSON(rv.Path), gpkgStr(rv.Name), rv.StreamOrder, rv.LengthKm)
	}
	w.SetStyle("rivers_merged", styleRivers(), "Labelled by name")
	return nil
}

func (s *Server) gpkgRoads(w *gpkgWriter, o gpkgExportOpts) error {
	l, err := w.AddLayer("roads", "GEOMETRY", "Road network (OSM via Geofabrik / HeiGIT)", []gpkgCol{
		{"osm_id", "TEXT"},
		{"name", "TEXT"},
		{"highway_type", "TEXT"},
		{"surface", "TEXT"},
		{"length_km", "REAL"},
		{"dl_class_2024", "TEXT"},
		{"passability", "TEXT"},
	})
	if err != nil {
		return err
	}
	rows, err := s.DB.Query(`SELECT COALESCE(osm_id,''), COALESCE(name,''), COALESCE(highway_type,''),
		COALESCE(surface,''), length_km, COALESCE(dl_class_2024,''), COALESCE(passability,''), geojson
		FROM roads_heigit WHERE park_id = ? AND geojson IS NOT NULL`, o.AreaID)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var osmID, name, hw, surface, dl, pass, geojson string
			var lengthKm sql.NullFloat64
			if rows.Scan(&osmID, &name, &hw, &surface, &lengthKm, &dl, &pass, &geojson) != nil {
				continue
			}
			l.Add(geojson, gpkgStr(osmID), gpkgStr(name), gpkgStr(hw), gpkgStr(surface),
				gpkgNF(lengthKm), gpkgStr(dl), gpkgStr(pass))
		}
	}
	w.SetStyle("roads", styleRoads(), "Coloured by highway class, labelled by name")
	return nil
}

func (s *Server) gpkgPlaces(w *gpkgWriter, o gpkgExportOpts) error {
	l, err := w.AddLayer("places", "POINT", "Named places (OpenStreetMap)", []gpkgCol{
		{"osm_id", "TEXT"}, {"name", "TEXT"}, {"place_type", "TEXT"},
		{"latitude", "REAL"}, {"longitude", "REAL"}, {"osm_tags", "TEXT"},
	})
	if err != nil {
		return err
	}
	rows, err := s.DB.Query(`SELECT COALESCE(osm_id,''), name, place_type, lat, lon, COALESCE(osm_tags,'')
		FROM osm_places WHERE park_id = ?`, o.AreaID)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var osmID, name, ptype, tags string
			var lat, lon float64
			if rows.Scan(&osmID, &name, &ptype, &lat, &lon, &tags) != nil {
				continue
			}
			l.Add(fmt.Sprintf(`{"type":"Point","coordinates":[%g,%g]}`, lon, lat),
				gpkgStr(osmID), gpkgStr(name), gpkgStr(ptype), lat, lon, gpkgStr(tags))
		}
	}
	w.SetStyle("places", stylePlaces(), "Labelled by name")
	return nil
}

func (s *Server) gpkgWater(w *gpkgWriter, o gpkgExportOpts) error {
	wl, err := w.AddLayer("waterbodies", "GEOMETRY",
		"Surface water (JRC Global Surface Water / global waterbody polygons)", []gpkgCol{
			{"waterbody_id", "TEXT"}, {"name", "TEXT"}, {"waterbody_type", "TEXT"},
			{"latitude", "REAL"}, {"longitude", "REAL"},
		})
	if err != nil {
		return err
	}
	rows, _ := s.DB.Query(`SELECT waterbody_id, COALESCE(name,''), COALESCE(waterbody_type,''), lat, lon, geojson
		FROM park_waterbodies WHERE park_id = ? AND geojson IS NOT NULL`, o.AreaID)
	if rows != nil {
		defer rows.Close()
		for rows.Next() {
			var id, name, wtype, geojson string
			var lat, lon sql.NullFloat64
			if rows.Scan(&id, &name, &wtype, &lat, &lon, &geojson) != nil {
				continue
			}
			wl.Add(geojson, gpkgStr(id), gpkgStr(name), gpkgStr(wtype), gpkgNF(lat), gpkgNF(lon))
		}
	}
	w.SetStyle("waterbodies", styleWater(), "")

	ll, err := w.AddLayer("lakes", "GEOMETRY", "Lakes (HydroLAKES / OSM)", []gpkgCol{
		{"hylak_id", "INTEGER"}, {"name", "TEXT"}, {"lake_type", "INTEGER"},
		{"area_km2", "REAL"}, {"latitude", "REAL"}, {"longitude", "REAL"},
	})
	if err != nil {
		return err
	}
	lrows, _ := s.DB.Query(`SELECT hylak_id, COALESCE(name,''), lake_type, area_km2, centroid_lat, centroid_lon, geojson
		FROM park_lakes_hydro WHERE park_id = ?`, o.AreaID)
	if lrows != nil {
		defer lrows.Close()
		for lrows.Next() {
			var id int64
			var name string
			var ltype sql.NullInt64
			var area, lat, lon sql.NullFloat64
			var geojson sql.NullString
			if lrows.Scan(&id, &name, &ltype, &area, &lat, &lon, &geojson) != nil {
				continue
			}
			g := ""
			if geojson.Valid && geojson.String != "" {
				g = geojson.String
			} else if lat.Valid && lon.Valid {
				g = fmt.Sprintf(`{"type":"Point","coordinates":[%g,%g]}`, lon.Float64, lat.Float64)
			}
			ll.Add(g, id, gpkgStr(name), gpkgNI(ltype), gpkgNF(area), gpkgNF(lat), gpkgNF(lon))
		}
	}
	w.SetStyle("lakes", styleLakes(), "Labelled by name")
	return nil
}

func (s *Server) gpkgBasins(w *gpkgWriter, o gpkgExportOpts) error {
	// One row per outlet, not the union: an area has several watersheds, and
	// merging them loses which river carries which lobe (AGENTS.md).
	up, err := w.AddLayer("watershed_upstream", "MULTIPOLYGON",
		"Upstream contributing watershed, one polygon per outlet", []gpkgCol{
			{"outlet_index", "INTEGER"}, {"river", "TEXT"}, {"area_km2", "REAL"},
			{"outlet_lat", "REAL"}, {"outlet_lon", "REAL"}, {"source", "TEXT"},
			{"fetched_at", "DATETIME"},
		})
	if err != nil {
		return err
	}
	down, err := w.AddLayer("watershed_downstream", "GEOMETRY",
		"Downstream flow trace from each outlet", []gpkgCol{
			{"outlet_index", "INTEGER"}, {"river", "TEXT"}, {"length_km", "REAL"},
			{"outlet_lat", "REAL"}, {"outlet_lon", "REAL"}, {"source", "TEXT"},
			{"fetched_at", "DATETIME"},
		})
	if err != nil {
		return err
	}
	rows, _ := s.DB.Query(`SELECT kind, idx, COALESCE(river,''), area_km2, length_km,
		outlet_lat, outlet_lon, COALESCE(source,''), COALESCE(fetched_at,''), geojson
		FROM park_basin_parts WHERE park_id = ? ORDER BY kind, idx`, o.AreaID)
	if rows != nil {
		defer rows.Close()
		for rows.Next() {
			var kind, river, source, fetched, geojson string
			var idx int64
			var area, length sql.NullFloat64
			var olat, olon float64
			if rows.Scan(&kind, &idx, &river, &area, &length, &olat, &olon, &source, &fetched, &geojson) != nil {
				continue
			}
			if kind == "upstream" {
				up.Add(geojson, idx, gpkgStr(river), gpkgNF(area), olat, olon, gpkgStr(source), gpkgDateTime(fetched))
			} else {
				down.Add(geojson, idx, gpkgStr(river), gpkgNF(length), olat, olon, gpkgStr(source), gpkgDateTime(fetched))
			}
		}
	}
	w.SetStyle("watershed_upstream", styleBasinUpstream(), "")
	w.SetStyle("watershed_downstream", styleBasinDownstream(), "Labelled by river")

	br, err := w.AddLayer("watershed_rivers", "GEOMETRY",
		"Rivers within the upstream watershed, with Strahler order", []gpkgCol{
			{"comid", "INTEGER"}, {"stream_order", "INTEGER"}, {"length_km", "REAL"},
		})
	if err != nil {
		return err
	}
	brows, _ := s.DB.Query(`SELECT comid, stream_order, length_km, geojson
		FROM park_basin_rivers WHERE park_id = ?`, o.AreaID)
	if brows != nil {
		defer brows.Close()
		for brows.Next() {
			var comid int64
			var order sql.NullInt64
			var length sql.NullFloat64
			var geojson string
			if brows.Scan(&comid, &order, &length, &geojson) != nil {
				continue
			}
			br.Add(geojson, comid, gpkgNI(order), gpkgNF(length))
		}
	}
	w.SetStyle("watershed_rivers", styleBasinRivers(), "")
	return nil
}

// Patrol layers are client-derived: suppressed in the test tenant exactly as
// the KML and Locus exports suppress them.
func (s *Server) gpkgPatrol(w *gpkgWriter, o gpkgExportOpts, boundary string) error {
	if o.Env != clientTenant {
		return nil
	}
	tl, err := w.AddLayer("patrol_tracks", "GEOMETRY",
		"Tracks learned from uploaded patrol GPX", []gpkgCol{
			{"feature_id", "TEXT"}, {"source", "TEXT"}, {"confidence_pct", "INTEGER"},
			{"match_count", "INTEGER"}, {"length_m", "REAL"}, {"approval_status", "TEXT"},
			{"first_seen", "DATE"}, {"last_seen", "DATE"},
		})
	if err != nil {
		return err
	}
	rows, _ := s.DB.Query(`SELECT feature_id, geojson, COALESCE(properties_json,'{}'),
		COALESCE(start_date,''), COALESCE(end_date,'')
		FROM feature_geometries WHERE park_id = ? AND feature_type = 'road'`, o.AreaID)
	if rows != nil {
		defer rows.Close()
		for rows.Next() {
			var fid, geojson, props, sd, ed string
			if rows.Scan(&fid, &geojson, &props, &sd, &ed) != nil {
				continue
			}
			var p map[string]interface{}
			json.Unmarshal([]byte(props), &p)
			tl.Add(geojson, fid, gpkgJSONStr(p, "source"), gpkgJSONInt(p, "confidence_pct"),
				gpkgJSONInt(p, "match_count"), gpkgJSONNum(p, "length_m"),
				gpkgJSONStr(p, "approval_status"), gpkgDate(sd), gpkgDate(ed))
		}
	}
	w.SetStyle("patrol_tracks", stylePatrolTracks(), "")

	al, err := w.AddLayer("airstrips", "POINT", "Airstrips and helipads learned from patrol GPX",
		[]gpkgCol{{"feature_id", "TEXT"}, {"name", "TEXT"}, {"aircraft_type", "TEXT"}, {"landings", "INTEGER"}})
	if err != nil {
		return err
	}
	arows, _ := s.DB.Query(`SELECT feature_id, geojson, COALESCE(properties_json,'{}')
		FROM feature_geometries WHERE park_id = ? AND feature_type = 'airstrip'`, o.AreaID)
	if arows != nil {
		defer arows.Close()
		i := 0
		for arows.Next() {
			var fid, geojson, props string
			if arows.Scan(&fid, &geojson, &props) != nil {
				continue
			}
			i++
			var p map[string]interface{}
			json.Unmarshal([]byte(props), &p)
			kind := "airstrip"
			if t, _ := p["aircraft_type"].(string); t == "rotor_wing" {
				kind = "helipad"
			}
			al.Add(geojson, fid, fmt.Sprintf("Airstrip %d", i), kind, gpkgJSONInt(p, "landing_count"))
		}
	}
	w.SetStyle("airstrips", styleAirstrips(), "Labelled by name")

	if !o.Effort {
		return nil
	}
	return s.gpkgPatrolEffort(w, o, boundary)
}

// Patrol effort is a monthly per-grid-cell aggregate. Exported as the cell
// rectangle rather than the KML's decorative circle: in a GIS the cell is the
// actual unit of measurement, and a circle would imply a coverage radius that
// the number does not claim.
func (s *Server) gpkgPatrolEffort(w *gpkgWriter, o gpkgExportOpts, boundary string) error {
	_, bbox, ok := s.resolveAreaBBox(o.AreaID)
	if !ok {
		return nil
	}
	const bufferDeg = 30.0 / 111.0
	l, err := w.AddLayer("patrol_effort", "POLYGON",
		"Monthly patrol effort per grid cell (30 km buffer around the area)", []gpkgCol{
			{"grid_cell_id", "TEXT"}, {"period_start", "DATE"}, {"year", "INTEGER"},
			{"month", "INTEGER"}, {"movement_type", "TEXT"}, {"distance_km", "REAL"},
			{"points", "INTEGER"}, {"latitude", "REAL"}, {"longitude", "REAL"},
		})
	if err != nil {
		return err
	}
	q := `SELECT e.grid_cell_id, e.year, e.month, e.movement_type,
			SUM(e.total_distance_km), SUM(e.total_points),
			g.lat_min, g.lat_max, g.lon_min, g.lon_max, g.lat_center, g.lon_center
		FROM effort_data e JOIN grid_cells g ON e.grid_cell_id = g.id
		WHERE e.movement_type = 'all' AND e.env = ?
			AND g.lat_center BETWEEN ? AND ? AND g.lon_center BETWEEN ? AND ?`
	args := []interface{}{strOr(o.Env, clientTenant),
		bbox[1] - bufferDeg, bbox[3] + bufferDeg, bbox[0] - bufferDeg, bbox[2] + bufferDeg}
	if o.FromDate != "" {
		if t, err := time.Parse("2006-01-02", o.FromDate); err == nil {
			q += " AND (e.year > ? OR (e.year = ? AND e.month >= ?))"
			args = append(args, t.Year(), t.Year(), int(t.Month()))
		}
	}
	if o.ToDate != "" {
		if t, err := time.Parse("2006-01-02", o.ToDate); err == nil {
			q += " AND (e.year < ? OR (e.year = ? AND e.month <= ?))"
			args = append(args, t.Year(), t.Year(), int(t.Month()))
		}
	}
	q += " GROUP BY e.grid_cell_id, e.year, e.month, e.movement_type"
	rows, err := s.DB.Query(q, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var cellID, mtype string
		var year, month, points int
		var dist, latMin, latMax, lonMin, lonMax, latC, lonC float64
		if rows.Scan(&cellID, &year, &month, &mtype, &dist, &points,
			&latMin, &latMax, &lonMin, &lonMax, &latC, &lonC) != nil {
			continue
		}
		geo := fmt.Sprintf(`{"type":"Polygon","coordinates":[[[%g,%g],[%g,%g],[%g,%g],[%g,%g],[%g,%g]]]}`,
			lonMin, latMin, lonMax, latMin, lonMax, latMax, lonMin, latMax, lonMin, latMin)
		l.Add(geo, cellID, fmt.Sprintf("%04d-%02d-01", year, month), year, month, mtype,
			math.Round(dist*100)/100, points, latC, lonC)
	}
	w.SetStyle("patrol_effort", stylePatrolEffort(), "Graduate on distance_km for a coverage map")
	return nil
}

func lineGeoJSON(path [][2]float64) string {
	if len(path) < 2 {
		return ""
	}
	var b strings.Builder
	b.WriteString(`{"type":"LineString","coordinates":[`)
	for i, p := range path {
		if i > 0 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, "[%g,%g]", p[0], p[1])
	}
	b.WriteString("]}")
	return b.String()
}
