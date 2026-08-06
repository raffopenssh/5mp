package srv

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
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

	// layer=effort: patrol effort (green pixels) from effort_data, bucketed the same way.
	// Returns grid-indexed coords in p entries: [xi, yi, distance_km, uploads].
	if q.Get("layer") == "effort" {
		s.serveEffortFrames(w, RequestEnv(r), from, to, step, res, south, north, west, east)
		return
	}

	// mode=points: individual fire detections (for high-zoom animation).
	// Only allowed for reasonably small bboxes; falls back to grid if too many.
	if q.Get("mode") == "points" && (east-west)*(north-south) <= 40 {
		if s.serveFirePoints(w, from, to, south, north, west, east) {
			return
		}
		// too many points -> fall through to grid response
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
	json.NewEncoder(w).Encode(map[string]interface{}{
		"res":       res,
		"step":      step,
		"from":      from,
		"to":        to,
		"frames":    frames,
		"truncated": truncated,
	})
}

// serveFirePoints returns individual fire detections for high-zoom animation.
// Response: {"mode":"points","from":from,"points":[[lon,lat,dayOffset,frp],...]} sorted by date.
// Returns false (and writes nothing) if the result would exceed the cap, so the
// caller can fall back to the gridded response.
func (s *Server) serveFirePoints(w http.ResponseWriter, from, to string, south, north, west, east float64) bool {
	const maxPts = 60000
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
	if env != "test" {
		env = "prod"
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
// GET /api/fire-anim-trajectories?bbox=w,s,e,n&from=&to=&limit=800
//
// Returns trajectories with per-point dates so the client can animate the
// transect building up over time (true movement speed, not interpolation):
//
//	{"groups": [{"id": "...", "park": "...", "type": "transhumance",
//	             "pts": [[lon,lat,"YYYY-MM-DD"], ...], "km": 310.9, "kmd": 5.7}]}

var fireGroupTrajCache = struct {
	sync.Mutex
	m map[string]map[string][][3]interface{} // parkID -> featureID -> pts
}{m: map[string]map[string][][3]interface{}{}}

func loadParkDatedTrajectories(parkID string) map[string][][3]interface{} {
	fireGroupTrajCache.Lock()
	if v, ok := fireGroupTrajCache.m[parkID]; ok {
		fireGroupTrajCache.Unlock()
		return v
	}
	fireGroupTrajCache.Unlock()

	if strings.ContainsAny(parkID, "/\\.") {
		return nil
	}
	data, err := os.ReadFile("data/fire_groups_v5/" + parkID + ".json")
	if err != nil {
		return nil
	}
	var groups []struct {
		FeatureID  string          `json:"feature_id"`
		Trajectory [][]interface{} `json:"trajectory"`
	}
	if err := json.Unmarshal(data, &groups); err != nil {
		return nil
	}
	out := make(map[string][][3]interface{}, len(groups))
	for _, g := range groups {
		if g.FeatureID == "" || len(g.Trajectory) == 0 {
			continue
		}
		pts := make([][3]interface{}, 0, len(g.Trajectory))
		for _, p := range g.Trajectory {
			if len(p) < 3 {
				continue
			}
			pts = append(pts, [3]interface{}{p[0], p[1], p[2]})
		}
		out[g.FeatureID] = pts
	}

	fireGroupTrajCache.Lock()
	// Cap cache at ~40 parks to bound memory (~25MB worst case)
	if len(fireGroupTrajCache.m) > 40 {
		fireGroupTrajCache.m = map[string]map[string][][3]interface{}{}
	}
	fireGroupTrajCache.m[parkID] = out
	fireGroupTrajCache.Unlock()
	return out
}

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
	limit := 800
	if lv, err := strconv.Atoi(q.Get("limit")); err == nil && lv > 0 && lv <= 3000 {
		limit = lv
	}

	// Largest/most significant groups first (stat_value = distance for trajectories)
	rows, err := s.DB.Query(`
		SELECT feature_id, park_id, properties_json
		FROM feature_geometries
		WHERE feature_type = 'fire_trajectory'
		  AND start_date <= ? AND end_date >= ?
		  AND bbox_maxx >= ? AND bbox_minx <= ? AND bbox_maxy >= ? AND bbox_miny <= ?`+
		aoiExcludeSQL("park_id")+`
		ORDER BY stat_value DESC
		LIMIT ?`, to, from, bbox[0], bbox[2], bbox[1], bbox[3], limit)
	if err != nil {
		http.Error(w, `{"error":"query failed"}`, http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type animGroup struct {
		ID   string           `json:"id"`
		Park string           `json:"park"`
		Type string           `json:"type,omitempty"`
		Km   float64          `json:"km,omitempty"`
		Kmd  float64          `json:"kmd,omitempty"`
		Pts  [][3]interface{} `json:"pts"`
	}
	var out []animGroup
	for rows.Next() {
		var fid, park, propsJSON string
		if err := rows.Scan(&fid, &park, &propsJSON); err != nil {
			continue
		}
		trajs := loadParkDatedTrajectories(park)
		pts, ok := trajs[fid]
		if !ok || len(pts) == 0 {
			continue
		}
		g := animGroup{ID: fid, Park: park, Pts: pts}
		var props struct {
			GroupType string  `json:"group_type"`
			Km        float64 `json:"distance_km"`
			Kmd       float64 `json:"avg_speed_km_day"`
		}
		if json.Unmarshal([]byte(propsJSON), &props) == nil {
			g.Type = props.GroupType
			g.Km = props.Km
			g.Kmd = props.Kmd
		}
		out = append(out, g)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=600")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"from": from, "to": to, "groups": out,
	})
}
