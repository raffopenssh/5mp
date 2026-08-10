package srv

import (
	"context"
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

// HandleAPIFeaturesInBBox returns features of a given type within a bounding
// box, optionally filtered by date range. Used by the stats-panel layer
// toggles (fires / deforestation / settlements in current view) and by the
// time animator.
//
// GET /api/features-in-bbox?type=fire_trajectory|deforestation|settlement
//
//	&bbox=minLng,minLat,maxLng,maxLat&from=YYYY-MM-DD&to=YYYY-MM-DD&limit=1500
//	&mode=points   -> compact [lon, lat, dayOffset, value] arrays
//
// Two things a large AOI made visible (XSA_Study_Area: 78,105 settlement
// polygons in one view, 947 KB of GeoJSON for the 1,500 that survived):
//
//   - `ORDER BY stat_value DESC LIMIT n` is not a sample, it is a *corner*.
//     Settlements all carry stat_value 0, so the tie-break fell back to rowid
//     and the 1,500 rows served were a contiguous ingest block — the yellow
//     stripe along the AOI's north edge in the animation. `spread=1` (the
//     default when a request truncates) buckets the bbox into ~limit cells and
//     keeps the biggest feature per cell, so a truncated answer still looks
//     like the whole area. Deterministic: same bbox+limit, same features.
//   - reading `geojson` for rows that are then thrown away is the bulk of the
//     cost. When the answer truncates we now select ids + centroids first
//     (index-only, ~50 ms for 78k rows) and fetch geometry for the survivors.
//     `mode=points` skips geometry entirely — the animator draws dots, so it
//     was inflating and parsing ~1 MB of polygon rings to get 1,500 centres.
func (s *Server) HandleAPIFeaturesInBBox(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	featureType := q.Get("type")
	switch featureType {
	case "fire_trajectory", "deforestation", "settlement":
	default:
		http.Error(w, "invalid type (fire_trajectory|deforestation|settlement)", http.StatusBadRequest)
		return
	}

	parts := strings.Split(q.Get("bbox"), ",")
	if len(parts) != 4 {
		http.Error(w, "bbox required: minLng,minLat,maxLng,maxLat", http.StatusBadRequest)
		return
	}
	var bbox [4]float64
	for i, p := range parts {
		v, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
		if err != nil {
			http.Error(w, "invalid bbox", http.StatusBadRequest)
			return
		}
		bbox[i] = v
	}

	limit := 1500
	if l, err := strconv.Atoi(q.Get("limit")); err == nil && l > 0 && l <= 20000 {
		limit = l
	}
	pointsMode := q.Get("mode") == "points"
	// spread=0 opts back into the old "biggest N anywhere" behaviour.
	spread := q.Get("spread") != "0"

	where := `
		FROM feature_geometries
		WHERE feature_type = ?
		  AND bbox_maxx >= ? AND bbox_minx <= ?
		  AND bbox_maxy >= ? AND bbox_miny <= ?
	` + aoiScopeSQL("park_id", s.aoiScopeParam(r))
	args := []interface{}{featureType, bbox[0], bbox[2], bbox[1], bbox[3]}

	// Date filters match UI narrative behavior: filter on start_date.
	// Settlements mostly lack dates, so NULL start_date always passes.
	if from := q.Get("from"); from != "" {
		where += " AND (start_date IS NULL OR start_date >= ?)"
		args = append(args, from)
	}
	if to := q.Get("to"); to != "" {
		where += " AND (start_date IS NULL OR start_date <= ?)"
		args = append(args, to)
	}

	// Pass 1 is index-only: id, centroid, rank inputs. No geojson, so the rows
	// that lose the selection cost nothing to read.
	scanQ := `SELECT id, (bbox_minx + bbox_maxx) / 2, (bbox_miny + bbox_maxy) / 2,
		         COALESCE(stat_value, 0),
		         COALESCE((bbox_maxx - bbox_minx) * (bbox_maxy - bbox_miny), 0),
		         start_date` + where + fmt.Sprintf(" LIMIT %d", featureScanCap)

	rows, err := s.DB.QueryContext(r.Context(), scanQ, args...)
	if err != nil {
		internalError(w, "request failed", err)
		return
	}
	cands := make([]bboxCand, 0, 4096)
	for rows.Next() {
		var c bboxCand
		if err := rows.Scan(&c.id, &c.cx, &c.cy, &c.stat, &c.area, &c.startDate); err != nil {
			continue
		}
		cands = append(cands, c)
	}
	rows.Close()

	total := len(cands)
	truncated := total > limit
	if truncated {
		if spread {
			cands = spreadSelect(cands, limit, bbox)
		} else {
			sortCands(cands)
			cands = cands[:limit]
		}
	}

	w.Header().Set("Content-Type", "application/json")

	// Compact mode: the animator draws dots, so shipping polygon rings for
	// them was ~1 MB of JSON per layer to recover 1,500 centroids.
	if pointsMode {
		base := q.Get("from")
		if base == "" {
			base = minStartDate(cands)
		}
		baseT, haveBase := parseISODate(base)
		pts := make([][4]float64, 0, len(cands))
		for _, c := range cands {
			day := -1.0
			if haveBase && c.startDate.Valid {
				if t, ok := parseISODate(c.startDate.String); ok {
					day = t.Sub(baseT).Hours() / 24
				}
			}
			pts = append(pts, [4]float64{
				round6(c.cx), round6(c.cy), day, round6(c.stat),
			})
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"mode":      "points",
			"type":      featureType,
			"from":      base,
			"points":    pts,
			"count":     len(pts),
			"total":     total,
			"truncated": truncated,
		})
		return
	}

	// Pass 2: geometry for the survivors only.
	// One screen pixel ≈ bboxWidth/1400 at the zoom this bbox implies; half of
	// that is invisible. ?simplify=0 disables it.
	tol := 0.0
	if q.Get("simplify") != "0" {
		tol = math.Abs(bbox[2]-bbox[0]) / 2800
	}
	features, err := s.fetchFeatureRows(r.Context(), cands, tol)
	if err != nil {
		internalError(w, "request failed", err)
		return
	}
	json.NewEncoder(w).Encode(map[string]interface{}{
		"type":      "FeatureCollection",
		"features":  features,
		"count":     len(features),
		"total":     total,
		"truncated": truncated,
	})
}

// featureScanCap bounds pass 1's memory. 400k x ~56 B is ~22 MB worst case and
// covers every real view (the largest today is 78k settlements over XSA).
const featureScanCap = 400000

type bboxCand struct {
	id         int64
	cx, cy     float64
	stat, area float64
	startDate  sql.NullString
}

type geoFeature struct {
	Type       string                 `json:"type"`
	Geometry   json.RawMessage        `json:"geometry"`
	Properties map[string]interface{} `json:"properties"`
}

func sortCands(c []bboxCand) {
	sort.Slice(c, func(i, j int) bool {
		if c[i].stat != c[j].stat {
			return c[i].stat > c[j].stat
		}
		if c[i].area != c[j].area {
			return c[i].area > c[j].area
		}
		return c[i].id < c[j].id
	})
}

// spreadSelect keeps the most significant feature in each of ~limit grid cells
// covering the bbox, then tops the answer up with the next-ranked leftovers.
//
// Why not just LIMIT: every settlement carries stat_value 0, so the ORDER BY
// degenerated to insertion order and a truncated answer was one contiguous
// corner of the ingest — 1,500 dots in a stripe along one edge of a
// 485,000 km² AOI, which reads as "the data is wrong", not as "truncated".
func spreadSelect(c []bboxCand, limit int, bbox [4]float64) []bboxCand {
	sortCands(c)
	w := bbox[2] - bbox[0]
	h := bbox[3] - bbox[1]
	if w <= 0 || h <= 0 {
		return c[:limit]
	}
	// cols*rows ≈ limit, cells roughly square in degrees.
	cols := int(math.Round(math.Sqrt(float64(limit) * w / h)))
	if cols < 1 {
		cols = 1
	}
	rows := (limit + cols - 1) / cols
	if rows < 1 {
		rows = 1
	}
	seen := make(map[int]bool, limit)
	out := make([]bboxCand, 0, limit)
	taken := make([]bool, len(c))
	for i, f := range c {
		if len(out) >= limit {
			break
		}
		cx := int(float64(cols) * (f.cx - bbox[0]) / w)
		cy := int(float64(rows) * (f.cy - bbox[1]) / h)
		if cx < 0 {
			cx = 0
		} else if cx >= cols {
			cx = cols - 1
		}
		if cy < 0 {
			cy = 0
		} else if cy >= rows {
			cy = rows - 1
		}
		key := cy*cols + cx
		if seen[key] {
			continue
		}
		seen[key] = true
		taken[i] = true
		out = append(out, f)
	}
	for i, f := range c {
		if len(out) >= limit {
			break
		}
		if !taken[i] {
			out = append(out, f)
		}
	}
	return out
}

// fetchFeatureRows reads geometry + properties for the selected ids, in id
// chunks so the IN list stays under SQLite's variable limit.
func (s *Server) fetchFeatureRows(ctx context.Context, cands []bboxCand, tol float64) ([]geoFeature, error) {
	features := make([]geoFeature, 0, len(cands))
	const chunk = 900
	byID := make(map[int64]int, len(cands))
	for i, c := range cands {
		byID[c.id] = i
	}
	ordered := make([]geoFeature, len(cands))
	got := make([]bool, len(cands))
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
		rows, err := s.DB.QueryContext(ctx, `
			SELECT id, feature_type, feature_id, park_id, geojson, start_date, end_date, properties_json
			FROM feature_geometries WHERE id IN (`+strings.Join(ph, ",")+`)`, args...)
		if err != nil {
			return nil, err
		}
		for rows.Next() {
			var id int64
			var fType, fID, parkID, geojson string
			var startDate, endDate, propsJSON sql.NullString
			if err := rows.Scan(&id, &fType, &fID, &parkID, &geojson, &startDate, &endDate, &propsJSON); err != nil {
				continue
			}
			props := make(map[string]interface{})
			if propsJSON.Valid {
				json.Unmarshal([]byte(propsJSON.String), &props)
			}
			props["feature_type"] = fType
			props["feature_id"] = fID
			props["park_id"] = parkID
			if startDate.Valid {
				props["start_date"] = startDate.String
			}
			if endDate.Valid {
				props["end_date"] = endDate.String
			}
			if i, ok := byID[id]; ok {
				ordered[i] = geoFeature{Type: "Feature", Geometry: simplifyGeometry(json.RawMessage(geojson), tol), Properties: props}
				got[i] = true
			}
		}
		rows.Close()
	}
	for i := range ordered {
		if got[i] {
			features = append(features, ordered[i])
		}
	}
	return features, nil
}

func minStartDate(c []bboxCand) string {
	min := ""
	for _, f := range c {
		if f.startDate.Valid && (min == "" || f.startDate.String < min) {
			min = f.startDate.String
		}
	}
	return min
}

func parseISODate(s string) (time.Time, bool) {
	if len(s) < 10 {
		return time.Time{}, false
	}
	t, err := time.Parse("2006-01-02", s[:10])
	if err != nil {
		return time.Time{}, false
	}
	return t, true
}

func round6(v float64) float64 {
	return math.Round(v*1e6) / 1e6
}

// simplifyGeometry drops vertices closer together than tol degrees.
//
// At the zoom a 485,000 km² AOI is viewed at, one screen pixel is ~0.007°:
// selecting the *biggest* built-up polygon per grid cell (which is what makes
// a truncated answer readable) otherwise ships 5 KB of sub-pixel ring detail
// per feature — 2 MB gzipped for one settlement layer. Tolerance is derived
// from the requested bbox, so a zoomed-in view keeps full detail.
//
// Radial-distance decimation, not Douglas-Peucker: it is O(n), never moves a
// vertex, and always keeps the first and last point, so a ring stays closed.
func simplifyGeometry(raw json.RawMessage, tol float64) json.RawMessage {
	if tol <= 0 || len(raw) < 400 {
		return raw
	}
	var g struct {
		Type        string          `json:"type"`
		Coordinates json.RawMessage `json:"coordinates"`
	}
	if err := json.Unmarshal(raw, &g); err != nil {
		return raw
	}
	var out interface{}
	switch g.Type {
	case "Polygon", "MultiLineString":
		var rings [][][]float64
		if json.Unmarshal(g.Coordinates, &rings) != nil {
			return raw
		}
		out = simplifyRings(rings, tol)
	case "MultiPolygon":
		var polys [][][][]float64
		if json.Unmarshal(g.Coordinates, &polys) != nil {
			return raw
		}
		res := make([][][][]float64, 0, len(polys))
		for _, p := range polys {
			res = append(res, simplifyRings(p, tol))
		}
		out = res
	case "LineString":
		var line [][]float64
		if json.Unmarshal(g.Coordinates, &line) != nil {
			return raw
		}
		out = simplifyLine(line, tol, false)
	default:
		return raw
	}
	b, err := json.Marshal(map[string]interface{}{"type": g.Type, "coordinates": out})
	if err != nil || len(b) >= len(raw) {
		return raw
	}
	return json.RawMessage(b)
}

func simplifyRings(rings [][][]float64, tol float64) [][][]float64 {
	res := make([][][]float64, 0, len(rings))
	for _, r := range rings {
		res = append(res, simplifyLine(r, tol, true))
	}
	return res
}

func simplifyLine(pts [][]float64, tol float64, ring bool) [][]float64 {
	min := 2
	if ring {
		min = 4
	}
	if len(pts) <= min {
		return roundPts(pts)
	}
	out := make([][]float64, 0, len(pts))
	out = append(out, roundPt(pts[0]))
	last := pts[0]
	for _, p := range pts[1 : len(pts)-1] {
		if len(p) < 2 || len(last) < 2 {
			continue
		}
		if math.Abs(p[0]-last[0]) >= tol || math.Abs(p[1]-last[1]) >= tol {
			out = append(out, roundPt(p))
			last = p
		}
	}
	out = append(out, roundPt(pts[len(pts)-1]))
	if len(out) < min {
		return roundPts(pts)
	}
	return out
}

// roundPt: the ingest wrote float64 at full precision, so a vertex costs ~40
// bytes to express 0.1 nm of accuracy. 6 decimals is ~11 cm.
func roundPt(p []float64) []float64 {
	if len(p) < 2 {
		return p
	}
	return []float64{round6(p[0]), round6(p[1])}
}

func roundPts(pts [][]float64) [][]float64 {
	out := make([][]float64, 0, len(pts))
	for _, p := range pts {
		out = append(out, roundPt(p))
	}
	return out
}
