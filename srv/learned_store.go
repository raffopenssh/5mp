package srv

// Database side of the event learner: load a ParkLearner from the learned_*
// tables, apply one upload, write the difference back. The decisions are in
// learned_park.go / learned_evidence.go and are pure.
//
// The learned_* tables carry no env column. They are written only from
// uploads whose tenant feeds learning (envFeedsLearning, checked where the
// job is queued) and read only by the client tenant (srv/test_env_guard.go).

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"strings"
	"time"

	"srv.exe.dev/srv/areas"
)

const learnerAuto = "auto" // approved_by of a decision the learner made

func (l *GPXLearner) loadParkLearner(ctx context.Context, parkID string) (*ParkLearner, error) {
	pl := NewParkLearner(parkID)
	pl.KnownRoads = l.heigitIndexForPark(ctx, parkID)
	pl.KnownAirfields = defaultKnownAirfields()
	if rows, err := l.db.QueryContext(ctx, `SELECT name, lat, lon FROM osm_places WHERE park_id = ?`, parkID); err == nil {
		for rows.Next() {
			var p GazetteerPlace
			if rows.Scan(&p.Name, &p.Lat, &p.Lon) == nil {
				pl.OSMPlaces = append(pl.OSMPlaces, p)
			}
		}
		rows.Close()
	}
	byID := map[string]map[int64]*LearnedFeature{"airfield": {}, "place": {}, "road": {}}
	load := func(family, q string) error {
		rows, err := l.db.QueryContext(ctx, q, parkID)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			f := &LearnedFeature{Family: family}
			var by, gj sql.NullString
			var length sql.NullFloat64
			if err := rows.Scan(&f.ID, &f.SeedLat, &f.SeedLon, &f.Status, &by, &gj, &length); err != nil {
				return err
			}
			f.Human = f.Status == "approved" || (f.Status == "rejected" && by.String != learnerAuto)
			if family == "road" {
				f.Coords = l.parseGeoJSONCoords(gj.String)
				if len(f.Coords) < 2 {
					continue
				}
				f.SeedLat, f.SeedLon, f.LengthM = f.Coords[0][1], f.Coords[0][0], length.Float64
				if f.LengthM == 0 {
					f.LengthM = lineLengthM(f.Coords)
				}
			}
			byID[family][f.ID] = f
			pl.Attach(f)
		}
		return rows.Err()
	}
	for _, q := range [][2]string{
		{"airfield", `SELECT id, COALESCE(seed_lat,lat), COALESCE(seed_lon,lon), COALESCE(status,'pending'), approved_by, NULL, NULL FROM learned_airstrips WHERE park_id = ? ORDER BY id`},
		{"place", `SELECT id, COALESCE(seed_lat,lat), COALESCE(seed_lon,lon), COALESCE(status,'pending'), approved_by, NULL, NULL FROM learned_places WHERE park_id = ? ORDER BY id`},
		{"road", `SELECT id, 0, 0, COALESCE(status,'pending'), approved_by, geojson, length_m FROM learned_roads WHERE park_id = ? ORDER BY id`},
	} {
		if err := load(q[0], q[1]); err != nil {
			return nil, err
		}
	}
	rows, err := l.db.QueryContext(ctx, `SELECT id, kind, tracker, t_start, t_end, lat, lon, heading_deg, subtype, journey, dwell_min, roll_json, feature_id
		FROM learned_events WHERE park_id = ? ORDER BY t_start, id`, parkID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		e := &LearnerEvent{HeadingDeg: math.NaN()}
		var ts, te string
		var hd, dw sql.NullFloat64
		var roll sql.NullString
		if err := rows.Scan(&e.ID, &e.Kind, &e.Tracker, &ts, &te, &e.Lat, &e.Lon, &hd, &e.Subtype, &e.Journey, &dw, &roll, &e.FeatureID); err != nil {
			return nil, err
		}
		e.Start, _ = time.Parse(time.RFC3339, ts)
		e.End, _ = time.Parse(time.RFC3339, te)
		if hd.Valid {
			e.HeadingDeg = hd.Float64
		}
		e.DwellMin = dw.Float64
		if roll.Valid {
			_ = json.Unmarshal([]byte(roll.String), &e.Roll)
		}
		if e.Kind == "coverage" {
			pl.Cover(e.Tracker, e.Start.Unix(), e.End.Unix())
			continue
		}
		if f := byID[eventFamily(e.Kind)][e.FeatureID]; f != nil {
			pl.AttachEvent(f, e)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	// Score what was loaded, so "changed" below means changed by this upload.
	for _, fs := range [][]*LearnedFeature{pl.Airfields, pl.Places, pl.Roads} {
		for _, f := range fs {
			f.loadedStatus = f.Status
		}
	}
	return pl, nil
}

func eventFamily(kind string) string {
	switch kind {
	case "landing", "takeoff":
		return "airfield"
	case "stop":
		return "place"
	}
	return "road"
}

func nf(p *float64) any {
	if p == nil {
		return nil
	}
	return *p
}

// saveParkLearner writes new features, new events and every changed feature
// in one short transaction, then publishes / withdraws map geometry.
func (l *GPXLearner) saveParkLearner(ctx context.Context, pl *ParkLearner, uploadID int64) error {
	tx, err := l.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	all := append(append(append([]*LearnedFeature{}, pl.Airfields...), pl.Places...), pl.Roads...)
	for _, f := range all {
		if f.ID > 0 {
			continue
		}
		if len(f.Events) == 0 {
			continue
		}
		var res sql.Result
		switch f.Family {
		case "airfield":
			res, err = tx.ExecContext(ctx, `INSERT INTO learned_airstrips (park_id, lat, lon, seed_lat, seed_lon, length_m, landing_count, takeoff_count, confidence_pct, status)
				VALUES (?, ?, ?, ?, ?, NULL, 0, 0, 0, 'pending')`, pl.Park, f.SeedLat, f.SeedLon, f.SeedLat, f.SeedLon)
		case "place":
			res, err = tx.ExecContext(ctx, `INSERT INTO learned_places (park_id, lat, lon, seed_lat, seed_lon, place_type, visit_count, confidence_pct, status)
				VALUES (?, ?, ?, ?, ?, 'stop', 0, 0, 'pending')`, pl.Park, f.SeedLat, f.SeedLon, f.SeedLat, f.SeedLon)
		case "road":
			gj, _ := json.Marshal(map[string]any{"type": "LineString", "coordinates": f.Coords})
			res, err = tx.ExecContext(ctx, `INSERT INTO learned_roads (park_id, geojson, length_m, match_count, confidence_pct, status)
				VALUES (?, ?, ?, 0, 0, 'pending')`, pl.Park, string(gj), f.LengthM)
		}
		if err != nil {
			return fmt.Errorf("insert %s: %w", f.Family, err)
		}
		f.ID, _ = res.LastInsertId()
		for _, e := range f.Events {
			e.FeatureID = f.ID
		}
	}
	for _, e := range pl.NewEvents {
		var hd, dw, roll any
		if !math.IsNaN(e.HeadingDeg) {
			hd = e.HeadingDeg
		}
		if e.Kind == "stop" {
			dw = e.DwellMin
		}
		if len(e.Roll) > 0 {
			b, _ := json.Marshal(e.Roll)
			roll = string(b)
		}
		res, err := tx.ExecContext(ctx, `INSERT INTO learned_events (park_id, kind, tracker, t_start, t_end, lat, lon, heading_deg, subtype, journey, dwell_min, roll_json, feature_id, upload_id)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			pl.Park, e.Kind, e.Tracker, e.Start.UTC().Format(time.RFC3339), e.End.UTC().Format(time.RFC3339),
			e.Lat, e.Lon, hd, e.Subtype, e.Journey, dw, roll, e.FeatureID, uploadID)
		if err != nil {
			return fmt.Errorf("insert event: %w", err)
		}
		e.ID, _ = res.LastInsertId()
	}
	for _, e := range pl.NewCovered {
		if _, err := tx.ExecContext(ctx, `INSERT INTO learned_events (park_id, kind, tracker, t_start, t_end, lat, lon, feature_id, upload_id)
			VALUES (?, 'coverage', ?, ?, ?, 0, 0, 0, ?)`, pl.Park, e.Tracker, e.Start.UTC().Format(time.RFC3339), e.End.UTC().Format(time.RFC3339), uploadID); err != nil {
			return fmt.Errorf("insert coverage: %w", err)
		}
	}
	for _, e := range pl.Updated {
		if _, err := tx.ExecContext(ctx, `UPDATE learned_events SET t_start = ?, t_end = ?, dwell_min = ? WHERE id = ?`,
			e.Start.UTC().Format(time.RFC3339), e.End.UTC().Format(time.RFC3339), e.DwellMin, e.ID); err != nil {
			return err
		}
	}
	for _, f := range all {
		if !f.Changed || f.ID <= 0 {
			continue
		}
		ev, _ := json.Marshal(f.Ev)
		conf := math.Round(f.Score*1000) / 10
		// approved_by records who decided: a reviewer's name stays, the
		// learner signs its own decisions, and pending carries none.
		by := "approved_by"
		if !f.Human {
			by = "NULL"
			if f.Status != "pending" {
				by = "'" + learnerAuto + "'"
			}
		}
		at := "approved_at"
		if f.Status != f.loadedStatus && !f.Human {
			at = "CASE WHEN ? = 'pending' THEN NULL ELSE CURRENT_TIMESTAMP END"
		} else {
			at = "COALESCE(?, approved_at, approved_at)" // no-op; keeps one arg list
			at = "CASE WHEN ? IS NULL THEN approved_at ELSE approved_at END"
		}
		switch f.Family {
		case "airfield":
			var line any
			if len(f.Ev.Line) == 2 {
				b, _ := json.Marshal(map[string]any{"type": "LineString", "coordinates": f.Ev.Line})
				line = string(b)
			}
			sub := ""
			switch f.Ev.Kind {
			case "airstrip":
				sub = "fixed_wing"
			case "landing_zone":
				sub = "rotor_wing"
			}
			_, err = tx.ExecContext(ctx, `UPDATE learned_airstrips SET lat = ?, lon = ?, heading_deg = ?, length_m = ?, aircraft_type = ?,
				landing_count = ?, takeoff_count = ?, confidence_pct = ?, status = ?, kind = ?, score = ?, evidence_json = ?,
				status_reason = ?, line_geojson = ?, last_seen = ?, approved_by = `+by+`, approved_at = `+at+`, updated_at = CURRENT_TIMESTAMP WHERE id = ?`,
				f.Ev.Lat, f.Ev.Lon, nf(f.Ev.AxisDeg), nf(f.Ev.LengthM), sub, f.Ev.Landings, f.Ev.Takeoffs, conf, f.Status, f.Ev.Kind,
				f.Score, string(ev), f.Reason, line, f.Ev.Last, f.Status, f.ID)
		case "place":
			_, err = tx.ExecContext(ctx, `UPDATE learned_places SET lat = ?, lon = ?, place_type = ?, visit_count = ?, avg_duration_minutes = ?,
				confidence_pct = ?, status = ?, score = ?, evidence_json = ?, status_reason = ?, last_seen = ?,
				name = COALESCE(NULLIF(name,''), NULLIF(?, '')), approved_by = `+by+`, approved_at = `+at+`, updated_at = CURRENT_TIMESTAMP WHERE id = ?`,
				f.Ev.Lat, f.Ev.Lon, f.Ev.Kind, f.Ev.Events, nf(f.Ev.DwellMedianMin), conf, f.Status, f.Score, string(ev), f.Reason,
				f.Ev.Last, f.Ev.NearPlace, f.Status, f.ID)
		case "road":
			_, err = tx.ExecContext(ctx, `UPDATE learned_roads SET match_count = ?, confidence_pct = ?, status = ?, score = ?, evidence_json = ?,
				status_reason = ?, last_seen = ?, approved_by = `+by+`, approved_at = `+at+`, updated_at = CURRENT_TIMESTAMP WHERE id = ?`,
				f.Ev.Events, conf, f.Status, f.Score, string(ev), f.Reason, f.Ev.Last, f.Status, f.ID)
		}
		if err != nil {
			return fmt.Errorf("update %s %d: %w", f.Family, f.ID, err)
		}
		if f.Status != f.loadedStatus {
			if err := l.publishLearned(ctx, tx, pl.Park, f); err != nil {
				return err
			}
		} else if f.Status == "auto_approved" || f.Status == "approved" {
			if err := l.publishLearned(ctx, tx, pl.Park, f); err != nil { // evidence in the popup follows the data
				return err
			}
		}
	}
	return tx.Commit()
}

// publishLearned keeps feature_geometries in step with a feature's status:
// present while approved, absent otherwise. Only roads and airfields were
// ever published there; places are served from learned_places.
func (l *GPXLearner) publishLearned(ctx context.Context, tx *sql.Tx, park string, f *LearnedFeature) error {
	var ftype, fid, gj string
	switch f.Family {
	case "road":
		b, _ := json.Marshal(map[string]any{"type": "LineString", "coordinates": f.Coords})
		ftype, fid, gj = "road", fmt.Sprintf("learned_road_%d", f.ID), string(b)
	case "airfield":
		ftype, fid = "airstrip", fmt.Sprintf("learned_airstrip_%d", f.ID)
		gj = fmt.Sprintf(`{"type":"Point","coordinates":[%.6f,%.6f]}`, f.Ev.Lon, f.Ev.Lat)
	default:
		return nil
	}
	if f.Status != "auto_approved" && f.Status != "approved" {
		_, err := tx.ExecContext(ctx, `DELETE FROM feature_geometries WHERE feature_type = ? AND feature_id = ?
			AND json_extract(properties_json, '$.source') = 'gpx_learner'`, ftype, fid)
		return err
	}
	props, _ := json.Marshal(map[string]any{
		"source": "gpx_learner", "approval_status": f.Status, "kind": f.Ev.Kind,
		"score": math.Round(f.Score*1000) / 1000, "events": f.Ev.Events, "days": f.Ev.Days,
		"journeys": f.Ev.Journeys, "trackers": f.Ev.Trackers, "last_seen": f.Ev.Last,
		"length_m": nf(f.Ev.LengthM), "learned_id": f.ID,
	})
	minX, minY, maxX, maxY := l.calculateBBox(gj)
	_, err := tx.ExecContext(ctx, `INSERT INTO feature_geometries (feature_type, feature_id, park_id, geojson, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, properties_json)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(feature_type, feature_id) DO UPDATE SET geojson = excluded.geojson, bbox_minx = excluded.bbox_minx,
			bbox_miny = excluded.bbox_miny, bbox_maxx = excluded.bbox_maxx, bbox_maxy = excluded.bbox_maxy, properties_json = excluded.properties_json`,
		ftype, fid, park, gj, minX, minY, maxX, maxY, string(props))
	return err
}

// eventsForPark keeps the events that happened in parkID. An event outside
// every protected area (an airstrip at a headquarters beyond the boundary)
// goes to the park its tracker was most recently or next inside, so each
// event belongs to exactly one park.
func eventsForPark(events []LearnerEvent, segs []ClassifiedSegment, parkID string, store interface {
	FindArea(lat, lon float64) *areas.ProtectedArea
}) []LearnerEvent {
	if store == nil {
		return events
	}
	type fix struct {
		t    time.Time
		park string
	}
	inside := map[string][]fix{}
	for _, cs := range segs {
		pts := cs.Points
		for _, i := range []int{0, len(pts) / 2, len(pts) - 1} {
			if i < 0 || i >= len(pts) || pts[i].Time == nil {
				continue
			}
			if a := store.FindArea(pts[i].Lat, pts[i].Lon); a != nil {
				inside[cs.Tracker] = append(inside[cs.Tracker], fix{*pts[i].Time, a.ID})
			}
		}
	}
	var out []LearnerEvent
	for _, e := range events {
		park := ""
		if a := store.FindArea(e.Lat, e.Lon); a != nil {
			park = a.ID
		} else {
			best := time.Duration(math.MaxInt64)
			for _, f := range inside[e.Tracker] {
				if d := absDur(f.t.Sub(e.Start)); d < best {
					best, park = d, f.park
				}
			}
		}
		if park == parkID {
			out = append(out, e)
		}
	}
	return out
}

// learnFromUpload is the event learner's part of a learning job.
func (l *GPXLearner) learnFromUpload(ctx context.Context, parkID string, uploadID int64, all, inPark []ClassifiedSegment, result *LearningResult) error {
	events := eventsForPark(ExtractLearnerEvents(all), all, parkID, l.areaStore)
	tracks := VehicleTracks(inPark)
	pl, err := l.loadParkLearner(ctx, parkID)
	if err != nil {
		return err
	}
	before := map[*LearnedFeature]string{}
	for _, fs := range [][]*LearnedFeature{pl.Airfields, pl.Places, pl.Roads} {
		for _, f := range fs {
			before[f] = f.Status
		}
	}
	pl.Add(events, tracks)
	asOf := time.Time{}
	for _, fs := range [][]*LearnedFeature{pl.Airfields, pl.Places, pl.Roads} {
		for _, f := range fs {
			for _, e := range f.Events {
				if e.Start.After(asOf) {
					asOf = e.Start
				}
			}
		}
	}
	if asOf.IsZero() {
		return nil
	}
	pl.Rescore(asOf)
	if err := l.saveParkLearner(ctx, pl, uploadID); err != nil {
		return err
	}
	approved := func(s string) bool { return s == "auto_approved" || s == "approved" }
	var demoted []string
	for _, f := range pl.Roads {
		if approved(f.Status) && !approved(before[f]) {
			result.NewRoads++
			result.NewRoadsKm += f.LengthM / 1000
			result.RoadConfidence = math.Max(result.RoadConfidence, f.Score*100)
		}
	}
	for _, f := range pl.Airfields {
		if approved(f.Status) && !approved(before[f]) {
			result.NewAirstrips++
			result.AirstripConfidence = math.Max(result.AirstripConfidence, f.Score*100)
		}
	}
	for _, f := range pl.Places {
		if approved(f.Status) && !approved(before[f]) {
			result.NewPlaces++
			result.PlaceTypes[f.Ev.Kind]++
			result.PlaceConfidence = math.Max(result.PlaceConfidence, f.Score*100)
		}
	}
	for f, was := range before {
		if was == "auto_approved" && f.Status == "pending" {
			demoted = append(demoted, fmt.Sprintf("%s %d", f.Family, f.ID))
		}
	}
	slog.Info("event learner", "park", parkID, "upload", uploadID, "events_new", len(pl.NewEvents),
		"events_duplicate", pl.Dupes, "demoted", strings.Join(demoted, ","), "counts", pl.Counts())
	return nil
}

// ── For cmd/movement-eval: the same learner, without a database to write ──

// NewReferenceParkLearner returns an empty ParkLearner carrying the park's
// reference data (HeiGIT roads, gazetteer, known airfields) read from db.
func NewReferenceParkLearner(ctx context.Context, db *sql.DB, parkID string, known []KnownAirfield) *ParkLearner {
	l := NewGPXLearner(db)
	pl := NewParkLearner(parkID)
	pl.KnownRoads = l.heigitIndexForPark(ctx, parkID)
	pl.KnownAirfields = known
	if rows, err := db.QueryContext(ctx, `SELECT name, lat, lon FROM osm_places WHERE park_id = ?`, parkID); err == nil {
		defer rows.Close()
		for rows.Next() {
			var p GazetteerPlace
			if rows.Scan(&p.Name, &p.Lat, &p.Lon) == nil {
				pl.OSMPlaces = append(pl.OSMPlaces, p)
			}
		}
	}
	return pl
}

// ReplayUpload applies one upload's classified segments to pl exactly as a
// learning job would, minus the database.
func ReplayUpload(pl *ParkLearner, segs []ClassifiedSegment, store interface {
	FindArea(lat, lon float64) *areas.ProtectedArea
}) {
	var inPark []ClassifiedSegment
	for _, s := range segs {
		if store == nil || segmentBelongsToPark(s, pl.Park, store) {
			inPark = append(inPark, s)
		}
	}
	pl.Add(eventsForPark(ExtractLearnerEvents(segs), segs, pl.Park, store), VehicleTracks(inPark))
}
