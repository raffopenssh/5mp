package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
)

// HandleAPIFireFrames returns time-bucketed, spatially-gridded fire detection
// counts for animation playback.
//
// GET /api/fire-frames?bbox=w,s,e,n&from=YYYY-MM-DD&to=YYYY-MM-DD&step=day|week|month&res=0.1
//
// Response (compact):
//
//	{
//	  "res": 0.1, "step": "week", "from": "...", "to": "...",
//	  "frames": [ {"d": "2025-11-03", "p": [[xi, yi, count, frp], ...]}, ... ],
//	  "truncated": false
//	}
//
// Cell center lon = xi*res, lat = yi*res (xi/yi are round(coord/res)).
func (s *Server) HandleAPIFireFrames(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()

	bboxParts := strings.Split(q.Get("bbox"), ",")
	if len(bboxParts) != 4 {
		http.Error(w, `{"error":"bbox required: west,south,east,north"}`, http.StatusBadRequest)
		return
	}
	var bbox [4]float64
	for i, p := range bboxParts {
		v, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
		if err != nil {
			http.Error(w, `{"error":"invalid bbox"}`, http.StatusBadRequest)
			return
		}
		bbox[i] = v
	}
	west, south, east, north := bbox[0], bbox[1], bbox[2], bbox[3]
	if east < west {
		west, east = east, west
	}
	if north < south {
		south, north = north, south
	}

	from := q.Get("from")
	to := q.Get("to")
	if from == "" {
		from = "2020-01-01"
	}
	if to == "" {
		to = time.Now().UTC().Format("2006-01-02")
	}

	step := q.Get("step")
	var table string
	switch step {
	case "day":
		table = "fire_grid_day"
	case "month":
		table = "fire_grid_month"
		if t, err := time.Parse("2006-01-02", from); err == nil {
			from = t.Format("2006-01") + "-01" // align to bucket start
		}
	default:
		step = "week"
		table = "fire_grid_week"
		if t, err := time.Parse("2006-01-02", from); err == nil {
			offset := (int(t.Weekday()) + 6) % 7 // days since Monday
			from = t.AddDate(0, 0, -offset).Format("2006-01-02")
		}
	}

	// Pre-aggregated tables store cells at base resolution 0.1°; coarser output
	// resolutions are re-binned in SQL. Finer than 0.1 is clamped to 0.1.
	const baseRes = 0.1
	res := baseRes
	if rv, err := strconv.ParseFloat(q.Get("res"), 64); err == nil && rv >= baseRes && rv <= 2.0 {
		res = rv
	}

	const maxPoints = 200000
	pointsFallback := 0

	// layer=effort: patrol effort (green pixels) from effort_data, bucketed the same way.
	// Returns grid-indexed coords in p entries: [xi, yi, distance_km, uploads].
	if q.Get("layer") == "effort" {
		s.serveEffortFrames(w, PatrolEnv(r), from, to, step, res, south, north, west, east)
		return
	}

	// mode=estimate: how many detections are in this window, and would the
	// points rendering be allowed? ~10 ms against fire_grid_day.
	//
	// This exists so the UI can say NO BEFORE the user clicks. Offering a
	// "fire points" chip that can only ever answer "1.4M detections in view,
	// showing the grid instead" is an offer the app knows is refused; the chip
	// is disabled with that number in its hint instead.
	if q.Get("mode") == "estimate" {
		est := s.estimateFireCount(from, to, south, north, west, east)
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "public, max-age=300")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"mode": "estimate", "estimate": est, "max": firePointsMax,
			"points_ok": est > 0 && est <= firePointsMax,
			"from":      from, "to": to,
		})
		return
	}

	// mode=points: individual fire detections (for high-zoom animation).
	//
	// The gate is the ESTIMATED NUMBER OF DETECTIONS, not the size of the box.
	// A 40 deg² cap refused points over the Sahara (where a whole country holds
	// a few hundred fires) and allowed them over an Angolan dry season (where
	// one degree holds a million), so the user was told "zoom in" in exactly the
	// views where zooming in would not have helped. fire_grid_day answers
	// "how many are in this window" in milliseconds — ask it, then decide.
	if q.Get("mode") == "points" {
		est := s.estimateFireCount(from, to, south, north, west, east)
		if est <= firePointsMax {
			if s.serveFirePoints(w, from, to, south, north, west, east) {
				return
			}
		}
		// Too many: fall through to the grid, but say what the number was.
		// "Zoom in" with no number is advice the user cannot evaluate.
		w.Header().Set("X-Fire-Points-Estimate", strconv.Itoa(est))
		pointsFallback = est
	}

	// Query pre-aggregated grid (built by scripts/build_fire_grid_agg.py,
	// maintained incrementally by the daily fire cron). PK (d, xi, yi) makes
	// the date-range scan fast even for full 2020-2026 continental spans.
	xiMin := int(math.Round(west / baseRes))
	xiMax := int(math.Round(east / baseRes))
	yiMin := int(math.Round(south / baseRes))
	yiMax := int(math.Round(north / baseRes))

	query := fmt.Sprintf(`
		SELECT CAST(round(xi * ? / ?) AS INTEGER) AS oxi,
		       CAST(round(yi * ? / ?) AS INTEGER) AS oyi,
		       d AS bucket,
		       SUM(n) AS n,
		       SUM(frp) AS frp
		FROM %s
		WHERE d >= ? AND d <= ?
		  AND xi BETWEEN ? AND ?
		  AND yi BETWEEN ? AND ?
		GROUP BY oxi, oyi, bucket
		LIMIT ?`, table)

	type frame struct {
		D string           `json:"d"`
		P [][4]interface{} `json:"p"`
	}

	// If the result exceeds maxPoints, retry at coarser resolution (up to 2x twice)
	// so continental full-span animations still show everything, just coarser.
	var frames []*frame
	truncated := false
	for attempt := 0; ; attempt++ {
		rows, err := s.DB.Query(query, baseRes, res, baseRes, res, from, to, xiMin, xiMax, yiMin, yiMax, maxPoints+1)
		if err != nil {
			http.Error(w, `{"error":"query failed"}`, http.StatusInternalServerError)
			return
		}

		framesByDate := map[string]*frame{}
		count := 0
		truncated = false
		for rows.Next() {
			var xi, yi, n int
			var bucket string
			var frp float64
			if err := rows.Scan(&xi, &yi, &bucket, &n, &frp); err != nil {
				continue
			}
			count++
			if count > maxPoints {
				truncated = true
				break
			}
			f := framesByDate[bucket]
			if f == nil {
				f = &frame{D: bucket}
				framesByDate[bucket] = f
			}
			f.P = append(f.P, [4]interface{}{xi, yi, n, math.Round(frp)})
		}
		rows.Close()

		if truncated && attempt < 2 && res*2 <= 2.0 {
			res *= 2
			continue
		}

		frames = make([]*frame, 0, len(framesByDate))
		for _, f := range framesByDate {
			frames = append(frames, f)
		}
		sort.Slice(frames, func(i, j int) bool { return frames[i].D < frames[j].D })
		break
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	resp := map[string]interface{}{
		"res":       res,
		"step":      step,
		"from":      from,
		"to":        to,
		"frames":    frames,
		"truncated": truncated,
	}
	// The client asked for points and is getting the grid: tell it how many
	// detections are actually here, so it can say "1.2M detections in view —
	// showing the density grid" instead of "zoom in", which is what it used to
	// say in views where zooming in was not the problem.
	if pointsFallback > 0 {
		resp["points_unavailable"] = map[string]interface{}{
			"estimate": pointsFallback,
			"max":      firePointsMax,
		}
	}
	json.NewEncoder(w).Encode(resp)
}

// firePointsMax is how many individual detections the animator will draw. The
// browser, not the database, is the limit: each one is a radial gradient.
const firePointsMax = 120000

// estimateFireCount sums the pre-aggregated daily grid over the window. Exact
// for whole 0.1° cells, approximate only at the bbox edge — which is all the
// precision a "can we draw these individually" decision needs, and it costs
// ~10 ms against minutes for a COUNT(*) over 42.9M raw detections.
func (s *Server) estimateFireCount(from, to string, south, north, west, east float64) int {
	const baseRes = 0.1
	var n sql.NullInt64
	err := s.DB.QueryRow(`SELECT SUM(n) FROM fire_grid_day
		WHERE d >= ? AND d <= ? AND xi BETWEEN ? AND ? AND yi BETWEEN ? AND ?`,
		from, to,
		int(math.Floor(west/baseRes)), int(math.Ceil(east/baseRes)),
		int(math.Floor(south/baseRes)), int(math.Ceil(north/baseRes))).Scan(&n)
	if err != nil || !n.Valid {
		return 0
	}
	return int(n.Int64)
}

// serveFirePoints returns individual fire detections for high-zoom animation.
// Response: {"mode":"points","from":from,"points":[[lon,lat,dayOffset,frp],...]} sorted by date.
// Returns false (and writes nothing) if the result would exceed the cap, so the
// caller can fall back to the gridded response.
func (s *Server) serveFirePoints(w http.ResponseWriter, from, to string, south, north, west, east float64) bool {
	const maxPts = firePointsMax
	fromT, err := time.Parse("2006-01-02", from)
	if err != nil {
		return false
	}
	rows, err := s.DB.Query(`
		SELECT longitude, latitude, acq_date, COALESCE(frp,0)
		FROM fire_detections
		WHERE acq_date >= ? AND acq_date <= ?
		  AND latitude BETWEEN ? AND ?
		  AND longitude BETWEEN ? AND ?
		ORDER BY acq_date
		LIMIT ?`, from, to, south, north, west, east, maxPts+1)
	if err != nil {
		return false
	}
	defer rows.Close()

	pts := make([][4]interface{}, 0, 4096)
	for rows.Next() {
		var lon, lat, frp float64
		var d string
		if err := rows.Scan(&lon, &lat, &d, &frp); err != nil {
			continue
		}
		if len(pts) >= maxPts {
			return false // too many; caller falls back to grid
		}
		dt, err := time.Parse("2006-01-02", d)
		if err != nil {
			continue
		}
		day := int(dt.Sub(fromT).Hours() / 24)
		pts = append(pts, [4]interface{}{math.Round(lon*10000) / 10000, math.Round(lat*10000) / 10000, day, math.Round(frp)})
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"mode":   "points",
		"from":   from,
		"to":     to,
		"points": pts,
	})
	return true
}

// serveEffortFrames returns time-bucketed patrol effort binned to the same
// xi/yi grid as fire frames (cell center = xi*res, yi*res), so the animator
// renders both layers with identical grid-aligned pixels.
// p entries: [xi, yi, distance_km, uploads].
func (s *Server) serveEffortFrames(w http.ResponseWriter, env, from, to, step string, res, south, north, west, east float64) {
	// env is the caller's tenant (RequestEnv). Patrol effort is client data:
	// the animator must draw only the pixels created in this scope, so the
	// value is used as given -- never coerced to the client tenant.
	if env == "" {
		env = clientTenant
	}
	var bucketExpr string
	dateExpr := "printf('%04d-%02d-%02d', e.year, e.month, COALESCE(e.day,1))"
	switch step {
	case "day":
		bucketExpr = dateExpr
	case "month":
		bucketExpr = fmt.Sprintf("strftime('%%Y-%%m-01', %s)", dateExpr)
	default:
		bucketExpr = fmt.Sprintf("date(%s, 'weekday 0', '-6 days')", dateExpr)
	}

	// NOTE: floor() indexing, not round(). grid_cells centers sit at exactly
	// x.x5, so round(center/0.1) divides x.5 values — float noise collapses
	// adjacent cells onto one index and leaves empty rows/columns (visible as
	// periodic line gaps in the animator). floor puts each center safely inside
	// its cell; the client renders at (xi+0.5)*res (see "align":"center").
	query := fmt.Sprintf(`
		SELECT CAST(floor(gc.lon_center / ?) AS INTEGER) AS xi,
		       CAST(floor(gc.lat_center / ?) AS INTEGER) AS yi,
		       %s AS bucket,
		       SUM(e.total_distance_km) AS dist, SUM(e.unique_uploads) AS ups
		FROM effort_data e
		JOIN grid_cells gc ON gc.id = e.grid_cell_id
		WHERE e.day IS NOT NULL AND e.movement_type = 'all' AND e.env = ?
		  AND gc.lat_center BETWEEN ? AND ? AND gc.lon_center BETWEEN ? AND ?
		  AND %s >= ? AND %s <= ?
		GROUP BY xi, yi, bucket
		LIMIT 200000`, bucketExpr, dateExpr, dateExpr)

	rows, err := s.DB.Query(query, res, res, env, south, north, west, east, from, to)
	if err != nil {
		http.Error(w, `{"error":"query failed"}`, http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type frame struct {
		D string           `json:"d"`
		P [][4]interface{} `json:"p"`
	}
	framesByDate := map[string]*frame{}
	for rows.Next() {
		var xi, yi, ups int
		var dist float64
		var bucket string
		if err := rows.Scan(&xi, &yi, &bucket, &dist, &ups); err != nil {
			continue
		}
		f := framesByDate[bucket]
		if f == nil {
			f = &frame{D: bucket}
			framesByDate[bucket] = f
		}
		f.P = append(f.P, [4]interface{}{xi, yi, math.Round(dist*10) / 10, ups})
	}
	frames := make([]*frame, 0, len(framesByDate))
	for _, f := range framesByDate {
		frames = append(frames, f)
	}
	sort.Slice(frames, func(i, j int) bool { return frames[i].D < frames[j].D })

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=600")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"res": res, "step": step, "from": from, "to": to,
		"layer": "effort", "frames": frames, "truncated": false,
		"align": "center", // cell center = (xi+0.5)*res, not xi*res
	})
}

// ---- Dated fire trajectories for animation ----
//
// GET /api/fire-anim-trajectories?bbox=w,s,e,n&from=&to=&limit=4000
//
// Returns trajectories with per-point dates so the client can animate the
// transect building up over time (true movement speed, not interpolation):
//
//	{"groups": [{"id": "...", "park": "...", "type": "transhumance",
//	             "pts": [[lon,lat,dayOffset], ...], "t0": "YYYY-MM-DD",
//	             "km": 310.9, "kmd": 5.7}]}
//
// SPEED (rewritten 2026-08-12). This used to read data/fire_groups_v5/<park>.json
// per park through a 40-park LRU to recover the DATE of each vertex — 816 MB of
// JSON on disk, so a wide bbox parsed hundreds of MB per request: 7.8 s at
// limit=800, 17 s at 4000, over 120 s for a continental view. The geometry was
// in feature_geometries the whole time and answers the same window in 0.4 s;
// only the dates were missing. They are a column now (migration 051,
// `traj_days` — day offsets from start_date, one per coordinate), so this is a
// single indexed query with no file I/O at all, and the client can afford the
// limit that makes a continental animation an answer rather than a sample.
//
// Wire format is compact for the same reason: a point is [lon, lat, dayOffset]
// against the group's own `t0`, not a repeated ISO date string.

func (s *Server) HandleAPIFireAnimTrajectories(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	bboxParts := strings.Split(q.Get("bbox"), ",")
	if len(bboxParts) != 4 {
		http.Error(w, `{"error":"bbox required"}`, http.StatusBadRequest)
		return
	}
	var bbox [4]float64
	for i, p := range bboxParts {
		v, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
		if err != nil {
			http.Error(w, `{"error":"invalid bbox"}`, http.StatusBadRequest)
			return
		}
		bbox[i] = v
	}
	from := q.Get("from")
	to := q.Get("to")
	if from == "" {
		from = "2020-01-01"
	}
	if to == "" {
		to = time.Now().UTC().Format("2006-01-02")
	}
	limit := 4000
	if lv, err := strconv.Atoi(q.Get("limit")); err == nil && lv > 0 && lv <= 40000 {
		limit = lv
	}

	// Two passes, the same shape as /api/features-in-bbox:
	//
	//  1. index-only over idx_fg_bbox_scan (id, centroid, rank inputs), STREAMED
	//     into the spread collector, so `total` is the true number of groups in
	//     view at every zoom and memory is O(limit) rather than O(rows in view);
	//  2. geometry + dates + properties for the survivors only.
	//
	// `ORDER BY stat_value DESC LIMIT n` alone is not a sample, it is a corner
	// (AGENTS.md): at continental zoom the biggest N fire groups are whichever
	// region was burning hardest, so half of Africa animates empty. The spread
	// collector keeps the most significant group per grid cell, which is the
	// same rule the feature layers use, and is deterministic.
	//
	// The date predicate is `start_date BETWEEN from-maxSpan AND to`, not
	// `start_date <= to AND end_date >= from`: end_date is not in
	// idx_fg_bbox_scan, so the overlap form drops the index and covering-scans.
	// A group lasts at most 167 days in the whole table, so widening the lower
	// bound by trajMaxSpanDays is exact; `end_date >= from` then drops the tail
	// inside the query, which matters because it is what fills the LIMIT —
	// dropping it in Go returned 2,409 of a requested 4,000 and read as a
	// sparse map.
	fromT, _ := parseISODate(from)
	scanFrom := from
	if !fromT.IsZero() {
		scanFrom = fromT.AddDate(0, 0, -trajMaxSpanDays).Format("2006-01-02")
	}
	scan, err := s.DB.QueryContext(r.Context(), `
		SELECT id, (bbox_minx + bbox_maxx) / 2, (bbox_miny + bbox_maxy) / 2,
		       COALESCE(stat_value, 0), 0, start_date
		FROM feature_geometries INDEXED BY idx_fg_bbox_scan
		WHERE feature_type = 'fire_trajectory'
		  AND bbox_maxx >= ? AND bbox_minx <= ? AND bbox_maxy >= ? AND bbox_miny <= ?
		  AND start_date >= ? AND start_date <= ?
		  AND (end_date IS NULL OR end_date >= ?)`+
		aoiScopeSQL("park_id", s.aoiScopeParam(r)),
		bbox[0], bbox[2], bbox[1], bbox[3], scanFrom, to, from)
	if err != nil {
		internalError(w, "query failed", err)
		return
	}
	col := newSpreadCollector(limit, bbox, q.Get("spread") != "0")
	for scan.Next() {
		var c bboxCand
		if err := scan.Scan(&c.id, &c.cx, &c.cy, &c.stat, &c.area, &c.startDate); err != nil {
			continue
		}
		col.add(c)
	}
	scan.Close()
	cands := col.result()

	type animGroup struct {
		ID   string       `json:"id"`
		Park string       `json:"park"`
		Type string       `json:"type,omitempty"`
		Km   float64      `json:"km,omitempty"`
		Kmd  float64      `json:"kmd,omitempty"`
		T0   string       `json:"t0"`
		Pts  [][3]float64 `json:"pts"`
		// Everything below exists so a PAUSED animation can hand the same
		// trajectory to the hover tip that a pinned layer would: pausing must
		// turn the picture into features, not into a screenshot.
		Fires int     `json:"fires,omitempty"`
		Days  int     `json:"days,omitempty"`
		FRP   float64 `json:"frp,omitempty"`
		Start string  `json:"start,omitempty"`
		End   string  `json:"end,omitempty"`
		Narr  string  `json:"narrative,omitempty"`
	}
	out := make([]animGroup, 0, len(cands))
	const chunk = 900
	for start := 0; start < len(cands); start += chunk {
		end := start + chunk
		if end > len(cands) {
			end = len(cands)
		}
		ph := make([]string, 0, end-start)
		args := make([]interface{}, 0, end-start)
		for _, c := range cands[start:end] {
			ph = append(ph, "?")
			args = append(args, c.id)
		}
		rows, err := s.DB.QueryContext(r.Context(), `
			SELECT feature_id, park_id, geojson, traj_days, properties_json,
			       COALESCE(start_date,''), COALESCE(end_date,'')
			FROM feature_geometries WHERE id IN (`+strings.Join(ph, ",")+`)`, args...)
		if err != nil {
			internalError(w, "query failed", err)
			return
		}
		for rows.Next() {
			var fid, park, geojson, propsJSON, sd, ed string
			var days sql.NullString
			if err := rows.Scan(&fid, &park, &geojson, &days, &propsJSON, &sd, &ed); err != nil {
				continue
			}
			pts := datedPoints(geojson, days.String, sd, ed)
			if len(pts) == 0 {
				continue
			}
			g := animGroup{ID: fid, Park: park, Pts: pts, Start: sd, End: ed, T0: sd}
			var props struct {
				GroupType string  `json:"group_type"`
				Km        float64 `json:"distance_km"`
				Kmd       float64 `json:"avg_speed_km_day"`
				Fires     int     `json:"fires_total"`
				Days      int     `json:"days"`
				FRP       float64 `json:"total_frp"`
				Narrative string  `json:"narrative"`
			}
			if json.Unmarshal([]byte(propsJSON), &props) == nil {
				g.Type = props.GroupType
				g.Km = props.Km
				g.Kmd = props.Kmd
				g.Fires = props.Fires
				g.Days = props.Days
				g.FRP = props.FRP
				g.Narr = props.Narrative
			}
			out = append(out, g)
		}
		rows.Close()
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, max-age=600")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"from": from, "to": to, "limit": limit,
		"count": len(out), "total": col.total, "truncated": col.total > len(out),
		"groups": out,
	})
}

// trajMaxSpanDays is the longest a fire group lasts in the whole table (167 as
// measured 2026-08-12), rounded up. It only ever widens a scan window, so a
// longer group would cost recall at the edge of a date filter, never
// correctness of what is returned.
const trajMaxSpanDays = 200

// datedPoints zips a trajectory's coordinates with its day offsets.
//
// traj_days is written per coordinate by load_fire_groups_to_db.py. When it is
// missing (a row written before migration 051 and not yet backfilled) the
// points are spread evenly across start_date..end_date: the trajectory is still
// drawn and still clickable, only its internal timing is approximate. Dropping
// the row instead would make a partially backfilled database look like a
// half-empty map.
func datedPoints(geojson, daysJSON, start, end string) [][3]float64 {
	var g struct {
		Type        string          `json:"type"`
		Coordinates json.RawMessage `json:"coordinates"`
	}
	if json.Unmarshal([]byte(geojson), &g) != nil {
		return nil
	}
	var coords [][]float64
	switch g.Type {
	case "LineString":
		if json.Unmarshal(g.Coordinates, &coords) != nil {
			return nil
		}
	case "Point":
		var p []float64
		if json.Unmarshal(g.Coordinates, &p) != nil || len(p) < 2 {
			return nil
		}
		coords = [][]float64{p}
	default:
		return nil
	}
	if len(coords) == 0 {
		return nil
	}
	var days []float64
	if daysJSON != "" {
		json.Unmarshal([]byte(daysJSON), &days)
	}
	span := 0.0
	if len(days) < len(coords) {
		if st, ok1 := parseISODate(start); ok1 {
			if en, ok2 := parseISODate(end); ok2 {
				span = en.Sub(st).Hours() / 24
			}
		}
	}
	out := make([][3]float64, 0, len(coords))
	for i, c := range coords {
		if len(c) < 2 {
			continue
		}
		var d float64
		if i < len(days) {
			d = days[i]
		} else if len(coords) > 1 {
			d = span * float64(i) / float64(len(coords)-1)
		}
		out = append(out, [3]float64{c[0], c[1], math.Round(d*10) / 10})
	}
	return out
}
