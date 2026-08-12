package srv

import (
	"context"
	"database/sql"
	"encoding/json"
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
	if l, err := strconv.Atoi(q.Get("limit")); err == nil && l > 0 && l <= 200000 {
		limit = l
	}
	// mode=auto is the zoom transition, decided by the server because only the
	// server knows how many features are actually in the view: below
	// geom_budget the answer is real clickable geometry, above it the same
	// selection as bare centroids. The client renders both, so crossing the
	// threshold is a cross-fade rather than a different feature.
	mode := q.Get("mode")
	geomBudget := 0
	if mode == "auto" {
		geomBudget = 3000
		if b, err := strconv.Atoi(q.Get("geom_budget")); err == nil && b >= 0 && b <= 20000 {
			geomBudget = b
		}
	}
	pointsMode := mode == "points"
	// spread=0 opts back into the old "biggest N anywhere" behaviour.
	spread := q.Get("spread") != "0"

	where := `
		FROM feature_geometries
		WHERE feature_type = ?
		  AND bbox_maxx >= ? AND bbox_minx <= ?
		  AND bbox_maxy >= ? AND bbox_miny <= ?
	` + aoiScopeSQL("park_id", s.aoiScopeParam(r))
	args := []interface{}{featureType, bbox[0], bbox[2], bbox[1], bbox[3]}

	// ?park= scopes the answer to one area's rows. A pinned layer is a
	// statement about an area ("Chinko's fires"), so when the pin is rendered
	// viewport-first — fetching what is on screen instead of the whole park at
	// once — panning to a neighbouring park must not quietly adopt its rows.
	// An AOI id is a park_id in this table, so the same param serves both; the
	// visibility check still comes from aoiScopeSQL/aoiExcludeSQL above.
	if park := q.Get("park"); park != "" {
		where += " AND park_id = ?"
		args = append(args, park)
	}

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
	//
	// It is also STREAMED into the selector rather than collected: a
	// continental view of fire_trajectory is 711k candidate rows, and the old
	// `LIMIT featureScanCap` into a slice both allocated tens of MB and
	// silently biased the sample towards low ids once it bit — a cap that
	// reads as an answer. The collector keeps O(limit) rows and counts the
	// rest, so `total` is the true number in view at every zoom.
	scanQ := `SELECT id, (bbox_minx + bbox_maxx) / 2, (bbox_miny + bbox_maxy) / 2,
		         COALESCE(stat_value, 0),
		         COALESCE((bbox_maxx - bbox_minx) * (bbox_maxy - bbox_miny), 0),
		         start_date` + where

	rows, err := s.DB.QueryContext(r.Context(), scanQ, args...)
	if err != nil {
		internalError(w, "request failed", err)
		return
	}
	col := newSpreadCollector(limit, bbox, spread)
	for rows.Next() {
		var c bboxCand
		if err := rows.Scan(&c.id, &c.cx, &c.cy, &c.stat, &c.area, &c.startDate); err != nil {
			continue
		}
		col.add(c)
	}
	rows.Close()

	cands := col.result()
	total := col.total
	truncated := total > len(cands)

	// mode=auto: geometry while the view holds few enough features to be worth
	// drawing as shapes, centroids beyond that. The switch is on the TRUE
	// count in view, not on zoom — two views at the same zoom can differ by
	// three orders of magnitude, and the thing that must stay bounded is the
	// number of rings the browser parses.
	if mode == "auto" {
		pointsMode = total > geomBudget
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
		ids := make([]int64, 0, len(cands))
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
			ids = append(ids, c.id)
		}
		// The row id rides along so a dot stays a *feature*: hovering one asks
		// /api/feature-detail for the same tip the geometry mode shows. Eight
		// bytes per point against ~1.6 KB for its rings — the whole reason
		// points mode exists — and without it a zoomed-out map is a picture
		// rather than data.
		json.NewEncoder(w).Encode(map[string]interface{}{
			"mode":      "points",
			"render":    "points",
			"type":      featureType,
			"from":      base,
			"points":    pts,
			"ids":       ids,
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
	// SLIM PROPERTIES above a threshold, so "as much detail as possible" is
	// about GEOMETRY rather than about text.
	//
	// A fire trajectory's properties_json is ~750 bytes, most of it a narrative
	// sentence, against ~350 bytes of coordinates: 14,350 trajectories in one
	// 8° view cost 4.1 MB gzipped, of which the shapes were a tenth. Nothing on
	// screen reads those fields — they are for the hover tip, i.e. for exactly
	// one feature at a time. So above geoSlimAbove the answer carries the
	// identity fields and the row id, and the tip fetches the rest from
	// /api/feature-detail on hover, which is what the points rendering already
	// does. Same features, same geometry, same tip; the text arrives when it is
	// read instead of when it is drawn.
	//
	// It also skips enrichFeatureProps, a per-park lookup a wide view otherwise
	// pays for every park it touches.
	slim := len(cands) > geoSlimAbove
	features, err := s.fetchFeatureRows(r.Context(), cands, tol, featureType, slim)
	if err != nil {
		internalError(w, "request failed", err)
		return
	}
	json.NewEncoder(w).Encode(map[string]interface{}{
		"type":      "FeatureCollection",
		"render":    "geometry",
		"slim":      slim,
		"features":  features,
		"count":     len(features),
		"total":     total,
		"truncated": truncated,
	})
}

// geoSlimAbove: how many features one answer may carry full properties for.
// Below it a view is a handful of shapes and its tips should need no second
// request; above it the text is 90% of the payload and 100% unread.
const geoSlimAbove = 1200

// HandleAPIFeatureDetail — GET /api/feature-detail?id=123
//
// One feature_geometries row, geometry and all, enriched exactly as the bbox
// endpoint enriches it. It exists so the zoomed-out *points* rendering is not a
// dead picture: a dot carries its row id, and hovering it fetches the same tip
// the zoomed-in geometry would have shown. Without this the LOD transition
// would silently trade interactivity for speed, which is the trade this whole
// change is meant to avoid.
func (s *Server) HandleAPIFeatureDetail(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(r.URL.Query().Get("id"), 10, 64)
	if err != nil {
		http.Error(w, "id required", http.StatusBadRequest)
		return
	}
	var fType, fID, parkID, geojson string
	var startDate, endDate, propsJSON sql.NullString
	err = s.DB.QueryRowContext(r.Context(), `SELECT feature_type, feature_id, park_id, geojson,
		start_date, end_date, properties_json FROM feature_geometries WHERE id = ?`, id).
		Scan(&fType, &fID, &parkID, &geojson, &startDate, &endDate, &propsJSON)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	// An AOI's rows are private. 404, not 403 — an id must not be an oracle
	// (srv/aoi.go).
	if IsAOIID(parkID) {
		if _, err := s.GetAOI(parkID, s.RequestPrincipalID(r), false); err != nil {
			http.NotFound(w, r)
			return
		}
	}
	props := map[string]interface{}{}
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
	var meta featureMetaCache
	s.enrichFeatureProps(fType, parkID, fID, props, &meta)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, max-age=300")
	json.NewEncoder(w).Encode(geoFeature{
		Type: "Feature", Geometry: json.RawMessage(geojson), Properties: props,
	})
}

// spreadCollector is spreadSelect turned inside out: it consumes candidates as
// they stream off the cursor and keeps at most ~limit of them, so a continental
// view (711k fire trajectories) costs the same memory as a park view.
//
// The selection rule is unchanged and still deterministic: the bbox is divided
// into ~limit cells and each cell keeps its single most significant feature
// (sortCands' order: stat, then area, then lowest id). Because "most
// significant" is a total order, deciding it per cell as rows arrive gives the
// same set as sorting everything first — which is what makes this a
// pure optimisation and not a different answer.
//
// Overflow (features that lost their cell) is kept only up to `limit` so a
// sparse view still fills its budget; beyond that it is counted, not stored.
type spreadCollector struct {
	limit  int
	bbox   [4]float64
	spread bool
	cols   int
	rows   int
	w, h   float64
	cells  map[int]bboxCand
	over   []bboxCand
	total  int
}

func newSpreadCollector(limit int, bbox [4]float64, spread bool) *spreadCollector {
	c := &spreadCollector{limit: limit, bbox: bbox, spread: spread,
		w: bbox[2] - bbox[0], h: bbox[3] - bbox[1]}
	if !spread || c.w <= 0 || c.h <= 0 {
		c.spread = false
		return c
	}
	c.cols = int(math.Round(math.Sqrt(float64(limit) * c.w / c.h)))
	if c.cols < 1 {
		c.cols = 1
	}
	c.rows = (limit + c.cols - 1) / c.cols
	if c.rows < 1 {
		c.rows = 1
	}
	c.cells = make(map[int]bboxCand, limit)
	return c
}

func betterCand(a, b bboxCand) bool {
	if a.stat != b.stat {
		return a.stat > b.stat
	}
	if a.area != b.area {
		return a.area > b.area
	}
	return a.id < b.id
}

func (c *spreadCollector) add(f bboxCand) {
	c.total++
	if !c.spread {
		// "biggest N anywhere": keep a bounded buffer and sort at the end.
		if len(c.over) < c.limit*4 {
			c.over = append(c.over, f)
		} else {
			sortCands(c.over)
			c.over = c.over[:c.limit]
			c.over = append(c.over, f)
		}
		return
	}
	cx := int(float64(c.cols) * (f.cx - c.bbox[0]) / c.w)
	cy := int(float64(c.rows) * (f.cy - c.bbox[1]) / c.h)
	if cx < 0 {
		cx = 0
	} else if cx >= c.cols {
		cx = c.cols - 1
	}
	if cy < 0 {
		cy = 0
	} else if cy >= c.rows {
		cy = c.rows - 1
	}
	key := cy*c.cols + cx
	cur, ok := c.cells[key]
	if !ok {
		c.cells[key] = f
		return
	}
	if betterCand(f, cur) {
		c.cells[key] = f
		f = cur
	}
	if len(c.over) < c.limit {
		c.over = append(c.over, f)
	}
}

func (c *spreadCollector) result() []bboxCand {
	if !c.spread {
		sortCands(c.over)
		if len(c.over) > c.limit {
			c.over = c.over[:c.limit]
		}
		return c.over
	}
	out := make([]bboxCand, 0, c.limit)
	for _, f := range c.cells {
		out = append(out, f)
	}
	sortCands(out)
	if len(out) > c.limit {
		out = out[:c.limit]
	}
	if len(out) < c.limit && len(c.over) > 0 {
		sortCands(c.over)
		need := c.limit - len(out)
		if need > len(c.over) {
			need = len(c.over)
		}
		out = append(out, c.over[:need]...)
	}
	return out
}

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

// fetchFeatureRows reads geometry + properties for the selected ids, in id
// chunks so the IN list stays under SQLite's variable limit.
// fetchFeatureRows reads geometry + properties for the selected ids.
//
// featureType drives narrative/classification enrichment: a settlement or
// deforestation polygon carries none of that in properties_json (it lives in
// park_settlements / deforestation_events, keyed by the polygon_ids list), and
// without it a viewport-fetched feature's hover tip is emptier than the same
// feature fetched through the per-park endpoint — i.e. zooming in would *lose*
// information. Looked up per park via the map-in-Go helpers in feature_meta.go,
// never the polygon_ids LIKE join.
func (s *Server) fetchFeatureRows(ctx context.Context, cands []bboxCand, tol float64, featureType string, slim bool) ([]geoFeature, error) {
	features := make([]geoFeature, 0, len(cands))
	const chunk = 900
	byID := make(map[int64]int, len(cands))
	for i, c := range cands {
		byID[c.id] = i
	}
	ordered := make([]geoFeature, len(cands))
	got := make([]bool, len(cands))
	var meta featureMetaCache
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
			if propsJSON.Valid && !slim {
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
			if slim {
				// The row id is what makes a slim feature still a feature:
				// hovering it fetches /api/feature-detail, exactly as a
				// centroid does. Without it this would be a picture.
				props["rid"] = id
			} else {
				s.enrichFeatureProps(featureType, parkID, fID, props, &meta)
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
