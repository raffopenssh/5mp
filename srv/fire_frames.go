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
	var dateExpr string
	switch step {
	case "day":
		dateExpr = "acq_date"
	case "month":
		dateExpr = "strftime('%Y-%m-01', acq_date)"
	default:
		step = "week"
		// ISO-ish week starting Monday
		dateExpr = "date(acq_date, 'weekday 0', '-6 days')"
	}

	res := 0.1
	if rv, err := strconv.ParseFloat(q.Get("res"), 64); err == nil && rv >= 0.01 && rv <= 2.0 {
		res = rv
	}

	const maxPoints = 200000

	// layer=effort: patrol effort (green pixels) from effort_data, bucketed the same way.
	// Returns real cell-center coords in p entries: [lon, lat, distance_km, uploads].
	if q.Get("layer") == "effort" {
		s.serveEffortFrames(w, from, to, step, south, north, west, east)
		return
	}

	query := fmt.Sprintf(`
		SELECT CAST(round(longitude / ?) AS INTEGER) AS xi,
		       CAST(round(latitude / ?) AS INTEGER) AS yi,
		       %s AS bucket,
		       COUNT(*) AS n,
		       COALESCE(SUM(frp), 0) AS frp
		FROM fire_detections
		WHERE acq_date >= ? AND acq_date <= ?
		  AND latitude BETWEEN ? AND ?
		  AND longitude BETWEEN ? AND ?
		GROUP BY xi, yi, bucket
		LIMIT ?`, dateExpr)

	rows, err := s.DB.Query(query, res, res, from, to, south, north, west, east, maxPoints+1)
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
	count := 0
	truncated := false
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

	frames := make([]*frame, 0, len(framesByDate))
	for _, f := range framesByDate {
		frames = append(frames, f)
	}
	sort.Slice(frames, func(i, j int) bool { return frames[i].D < frames[j].D })

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

// serveEffortFrames returns time-bucketed patrol effort per grid cell.
func (s *Server) serveEffortFrames(w http.ResponseWriter, from, to, step string, south, north, west, east float64) {
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

	query := fmt.Sprintf(`
		SELECT gc.lon_center, gc.lat_center, %s AS bucket,
		       SUM(e.total_distance_km) AS dist, SUM(e.unique_uploads) AS ups
		FROM effort_data e
		JOIN grid_cells gc ON gc.id = e.grid_cell_id
		WHERE e.day IS NOT NULL AND e.movement_type = 'all' AND e.env = 'prod'
		  AND gc.lat_center BETWEEN ? AND ? AND gc.lon_center BETWEEN ? AND ?
		  AND %s >= ? AND %s <= ?
		GROUP BY gc.lon_center, gc.lat_center, bucket
		LIMIT 200000`, bucketExpr, dateExpr, dateExpr)

	rows, err := s.DB.Query(query, south, north, west, east, from, to)
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
		var lon, lat, dist float64
		var bucket string
		var ups int
		if err := rows.Scan(&lon, &lat, &bucket, &dist, &ups); err != nil {
			continue
		}
		f := framesByDate[bucket]
		if f == nil {
			f = &frame{D: bucket}
			framesByDate[bucket] = f
		}
		f.P = append(f.P, [4]interface{}{math.Round(lon*100) / 100, math.Round(lat*100) / 100, math.Round(dist*10) / 10, ups})
	}
	frames := make([]*frame, 0, len(framesByDate))
	for _, f := range framesByDate {
		frames = append(frames, f)
	}
	sort.Slice(frames, func(i, j int) bool { return frames[i].D < frames[j].D })

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=600")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"res": 0, "step": step, "from": from, "to": to,
		"layer": "effort", "frames": frames, "truncated": false,
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
		  AND bbox_maxx >= ? AND bbox_minx <= ? AND bbox_maxy >= ? AND bbox_miny <= ?
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
