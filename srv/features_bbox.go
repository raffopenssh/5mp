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
		// The ceiling is the client's "draw shapes whatever it costs"
		// preference (LODLayer.setDetail 'shapes'), not a safety limit: the
		// answer is bounded by ?limit= either way, and a budget the server
		// silently ignored made the forced-shapes control a no-op above 20k.
		if b, err := strconv.Atoi(q.Get("geom_budget")); err == nil && b >= 0 && b <= 200000 {
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
	` + areaScopeSQL("park_id", s.areaScopeParam(r))
	args := []interface{}{featureType, bbox[0], bbox[2], bbox[1], bbox[3]}

	// ?area= scopes the answer to one area's rows. A pinned layer is a
	// statement about an area ("Chinko's fires"), so when the pin is rendered
	// viewport-first — fetching what is on screen instead of the whole park at
	// once — panning to a neighbouring park must not quietly adopt its rows.
	//
	// It is `area`, not `park`, because an AOI id in `?park=` is a hard 404:
	// ParkIDMiddleware rejects one on every request, by design (an AOI is not a
	// park and /api/parks/{aoi} must not serve it). The first viewport-first
	// pin sent `park=XSA_Study_Area` and every fetch 404'd, so an AOI fire pin
	// silently drew nothing and reported "0 in view" — see AGENTS.md, the
	// no-op that reads as an answer. `park=` is still accepted for parks so old
	// share links and any cached client keep working.
	//
	// An AOI id IS a park_id in this table, so one column serves both; the
	// visibility check is aoiScopeSQL/aoiExcludeSQL above plus the explicit
	// check here — an invisible id is ignored rather than refused, so an id is
	// never an oracle.
	area := q.Get("area")
	if area == "" {
		area = q.Get("park")
	}
	if area != "" && IsAOIID(area) {
		if _, err := s.GetAOI(area, s.RequestPrincipalID(r), false); err != nil {
			area = ""
		}
	}
	if area != "" {
		where += " AND park_id = ?"
		args = append(args, area)
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

	// ?class= — the popup's classification filter ("only agricultural
	// clearings", "only fishing camps"), applied HERE rather than in the
	// browser.
	//
	// It used to be client-side over a whole-park fetch, which is why a
	// filtered pin was the one thing the LOD loader could not serve: the
	// points and slim-geometry renderings ship no properties to filter on, so
	// the filter would have silently emptied the layer. The classification is
	// not in feature_geometries at all — it lives in park_settlements /
	// deforestation_events keyed by the polygon_ids list — so it is resolved
	// through the same Go-side map as the hover tips (feature_meta.go), never
	// the polygon_ids LIKE join.
	//
	// Requires ?area=: without it the candidate set spans every park in view,
	// and the filter only ever comes from one area's popup. Ignored rather
	// than refused otherwise — the unfiltered answer is the honest superset.
	classFilter := strings.TrimSpace(q.Get("class"))
	var classIDs map[string]bool
	if classFilter != "" && area != "" {
		classIDs = s.featureIDsWithClass(featureType, area, classFilter)
	}
	if classIDs == nil {
		classFilter = ""
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
		         start_date, feature_id, park_id` + where

	rows, err := s.DB.QueryContext(r.Context(), scanQ, args...)
	if err != nil {
		internalError(w, "request failed", err)
		return
	}
	// A SETTLEMENT IS A CLUSTER; feature_geometries holds its FOOTPRINTS.
	//
	// Chinko's 35 built-up polygons are 27 settlements, and until now the two
	// were reported by different surfaces under the same word: the stats panel
	// and the popup counted park_settlements rows, the viewport readout counted
	// the polygons it had drawn, and nothing said they were different units.
	// Two numbers that disagree are a bug report; two numbers that disagree
	// *and* claim the same noun is a data-quality accusation.
	//
	// So the answer carries both, and the unit it is drawing. Counted over
	// everything in view (not just the served sample) via the same per-park map
	// the hover tips use -- never the polygon_ids LIKE join (feature_meta.go).
	groupKeys := map[string]bool{}
	countGroups := featureType == "settlement"
	var groupMeta featureMetaCache
	col := newSpreadCollector(limit, bbox, spread)
	for rows.Next() {
		var c bboxCand
		var featureID, rowPark string
		if err := rows.Scan(&c.id, &c.cx, &c.cy, &c.stat, &c.area, &c.startDate, &featureID, &rowPark); err != nil {
			continue
		}
		// The filter is applied BEFORE the collector, so `total` counts what
		// passes it and a spread selection spreads over the filtered set. A
		// filter that changed the picture but not the number would read as a
		// broken filter.
		if classFilter != "" && !classIDs[featureID] {
			continue
		}
		if countGroups {
			groupKeys[s.settlementGroupKey(rowPark, featureID, &groupMeta)] = true
		}
		col.add(c)
	}
	rows.Close()

	cands := col.result()
	total := col.total
	truncated := total > len(cands)
	// What the count is a count OF. `total` is polygons because polygons are
	// what is drawn; `groups` is the number of settlements those polygons
	// belong to, which is the number every other surface in the app says.
	// Named in the response rather than left to the client to infer.
	unit, groups := "features", 0
	if countGroups {
		unit, groups = "footprints", len(groupKeys)
	}
	addCounts := func(out map[string]interface{}) map[string]interface{} {
		out["unit"] = unit
		if countGroups {
			out["groups"] = groups
			out["group_unit"] = "settlements"
		}
		return out
	}

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
		out := addCounts(map[string]interface{}{
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
		// A FIRE IS A MOVEMENT, AND A DOT IS THE ONE THING IT IS NOT.
		//
		// The cheap rendering used to collapse each trajectory to its
		// centroid, so a zoomed-out view of 38,725 fire fronts was a red
		// stipple: it showed *where* fires were and destroyed the only
		// property that distinguishes a fire front from a hotspot — the
		// direction and length it ran. ?seg=1 asks for a three-point chord
		// instead (first, middle, last vertex), read straight out of the
		// stored GeoJSON by index, so the field of arrows still reads as
		// movement at continental zoom. ~50 bytes a feature against ~350 for
		// the full path and ~26 for a dot.
		//
		// Parallel to `points`, not instead of it: a client that has not
		// heard of segments still gets the dots it expects, and the tip,
		// the day offset and the row id are all still keyed by index.
		if q.Get("seg") == "1" && lineLikeFeature(featureType) {
			if segs := s.fetchChords(r.Context(), cands); segs != nil {
				out["segs"] = segs
				out["render"] = "segments"
			}
		}
		// The row id rides along so a dot stays a *feature*: hovering one asks
		// /api/feature-detail for the same tip the geometry mode shows. Eight
		// bytes per point against ~1.6 KB for its rings — the whole reason
		// points mode exists — and without it a zoomed-out map is a picture
		// rather than data.
		json.NewEncoder(w).Encode(out)
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
	json.NewEncoder(w).Encode(addCounts(map[string]interface{}{
		"type":      "FeatureCollection",
		"render":    "geometry",
		"slim":      slim,
		"features":  features,
		"count":     len(features),
		"total":     total,
		"truncated": truncated,
	}))
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
// into ~limit cells and each cell keeps its most significant features
// (sortCands' order: stat, then area, then lowest id).
//
// THE OVERFLOW HAS TO BE SPREAD TOO. The first version kept one feature per
// cell plus a flat "best of the rest" list to top the budget back up. That list
// is where the settlement layer's blotchy picture came from: every settlement
// carries stat_value = 0 and area is a near-tie, so the rest-list collapsed to
// *lowest id*, i.e. one contiguous ingest block — the same corner-of-the-map
// bias that ORDER BY stat_value DESC had, re-introduced through the back door.
// On a polygon AOI it is worse than at bbox scale, because every grid cell
// outside the polygon comes back empty and hands its budget to that block: the
// user sees a dense yellow patch in one district and a thin scatter everywhere
// else, which reads as "the data is wrong", not as "this is a sample".
//
// So leftovers are kept PER CELL (bounded), and the budget is filled
// round-robin by depth: every cell's first feature, then every cell's second,
// and so on. Density variation between cells then comes from the data — a cell
// with three settlements contributes three before a cell with one contributes
// a second — while no region can monopolise the budget. Deterministic:
// depth-major, then the same total order within a depth.
//
// What is left over after that is a genuinely spatial problem, not an ordering
// one: over an AOI most cells of the bounding box are outside the polygon and
// donate nothing, so ~25% of the budget goes unspent while 66,000 features are
// dropped. Topping it up from a "best of the rest" list is exactly the bug
// above. It is topped up by a HASH sample instead (`fnvID`): a deterministic,
// spatially uniform 1-in-k of the rest, so the extra density lands in
// proportion to where the features actually are.
type spreadCollector struct {
	limit   int
	bbox    [4]float64
	spread  bool
	cols    int
	rows    int
	w, h    float64
	cells   map[int][]bboxCand
	perCell int
	sample  []bboxCand // hash-sampled rest, for topping the budget up
	over    []bboxCand
	total   int
}

func newSpreadCollector(limit int, bbox [4]float64, spread bool) *spreadCollector {
	c := &spreadCollector{limit: limit, bbox: bbox, spread: spread,
		w: bbox[2] - bbox[0], h: bbox[3] - bbox[1]}
	if !spread || c.w <= 0 || c.h <= 0 {
		c.spread = false
		return c
	}
	// ~limit cells, so the common case is still one feature per cell; the
	// per-cell depth is what absorbs an area whose features are concentrated.
	c.cols = int(math.Round(math.Sqrt(float64(limit) * c.w / c.h)))
	if c.cols < 1 {
		c.cols = 1
	}
	c.rows = (limit + c.cols - 1) / c.cols
	if c.rows < 1 {
		c.rows = 1
	}
	// DEPTH IS THE LEVER, not the top-up. Over an AOI most cells of the
	// bounding box are outside the polygon and stay empty, so the depth rounds
	// have to be able to fill the whole budget from the occupied ones:
	// XSA_Study_Area occupies ~a fifth of its bbox, i.e. depth 5. Ending the
	// rounds early and taking the difference from the hash sample gives a
	// dense cluster two thirds of the budget, because the dropped features it
	// samples from are exactly the ones a cluster produces. 24 keeps memory
	// bounded (~2x limit worst case, and only cells that are actually that
	// deep pay it) while covering every real shape we serve.
	c.perCell = 24
	c.cells = make(map[int][]bboxCand, limit)
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
	list := c.cells[key]
	// Insertion sort into the cell's bounded top-k. k is 8, so this is a
	// handful of comparisons per row and no allocation once warm.
	pos := len(list)
	for i, cur := range list {
		if betterCand(f, cur) {
			pos = i
			break
		}
	}
	if pos >= c.perCell {
		c.addSample(f)
		return
	}
	if len(list) < c.perCell {
		list = append(list, f)
	} else {
		c.addSample(list[len(list)-1])
	}
	copy(list[pos+1:], list[pos:len(list)-1])
	list[pos] = f
	c.cells[key] = list
}

// A deterministic, spatially unbiased reservoir: keep the `limit` candidates
// with the smallest hash of their id. Hashing the id (not using the id itself)
// is the point — ids are assigned in ingest order, which is geographic, so
// "lowest id" is a corner of the map and "lowest hash" is nowhere in
// particular. Bounded by a single compaction pass, so no heap.
func (c *spreadCollector) addSample(f bboxCand) {
	if len(c.sample) < c.limit*2 {
		c.sample = append(c.sample, f)
		return
	}
	sort.Slice(c.sample, func(i, j int) bool { return fnvID(c.sample[i].id) < fnvID(c.sample[j].id) })
	c.sample = c.sample[:c.limit]
	c.sample = append(c.sample, f)
}

// splitmix64's finalizer: a full avalanche, so consecutive ids land nowhere
// near each other. FNV alone is not enough here — for small ids the top seven
// bytes are zero, so its output stayed largely monotonic in the id and the
// sample drifted back towards ingest order, which is the bias being avoided.
func fnvID(id int64) uint64 {
	h := uint64(id) + 0x9E3779B97F4A7C15
	h = (h ^ (h >> 30)) * 0xBF58476D1CE4E5B9
	h = (h ^ (h >> 27)) * 0x94D049BB133111EB
	return h ^ (h >> 31)
}

func (c *spreadCollector) result() []bboxCand {
	if !c.spread {
		sortCands(c.over)
		if len(c.over) > c.limit {
			c.over = c.over[:c.limit]
		}
		return c.over
	}
	// Depth-major: one from every occupied cell, then a second from every
	// cell that has one, and so on until the budget is spent. Within a depth
	// the order is the same total order as everywhere else, so the answer is
	// a pure function of the input rows.
	out := make([]bboxCand, 0, c.limit)
	for depth := 0; depth < c.perCell && len(out) < c.limit; depth++ {
		level := make([]bboxCand, 0, len(c.cells))
		for _, list := range c.cells {
			if depth < len(list) {
				level = append(level, list[depth])
			}
		}
		if len(level) == 0 {
			continue
		}
		sortCands(level)
		if n := c.limit - len(out); len(level) > n {
			level = level[:n]
		}
		out = append(out, level...)
	}
	// Budget left after the depth rounds: top up from the hash sample, which
	// is uniform over whatever was dropped rather than over the ingest order.
	//
	// CAPPED, because the dropped set is not spatially neutral: it is exactly
	// what a dense cluster produces, so an uncapped top-up hands a single
	// cluster two thirds of the budget and re-creates the blotch from the
	// other side. Past this point an unspent budget is the honest answer — it
	// means every cell has already contributed 24 features and there is
	// nothing spatially new left to add, only more of the same cluster.
	if need := c.limit - len(out); need > 0 && len(c.sample) > 0 {
		if room := c.limit / 4; need > room {
			need = room
		}
		sort.Slice(c.sample, func(i, j int) bool { return fnvID(c.sample[i].id) < fnvID(c.sample[j].id) })
		if need > len(c.sample) {
			need = len(c.sample)
		}
		extra := append([]bboxCand(nil), c.sample[:need]...)
		sortCands(extra)
		out = append(out, extra...)
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
// lineLikeFeature: types whose geometry is a path, i.e. types for which a
// centroid throws away the thing the feature is about.
func lineLikeFeature(t string) bool { return t == "fire_trajectory" }

// fetchChords reads a three-point chord (first, middle, last vertex) per
// candidate, in the SAME index order as the caller's points array.
//
// SQLite's json_extract with a fixed path is an O(1)-ish read into the stored
// geojson string, so this is ~40 ms for 30,000 rows — an order of magnitude
// cheaper than shipping and parsing the full coordinate lists, which is the
// whole point of the cheap rendering. A row whose geometry is not a LineString
// (a Point trajectory) yields nulls and is served as a degenerate chord, so the
// arrays stay parallel and the client never has to re-index.
func (s *Server) fetchChords(ctx context.Context, cands []bboxCand) [][6]float64 {
	if len(cands) == 0 {
		return nil
	}
	idx := make(map[int64]int, len(cands))
	for i, c := range cands {
		idx[c.id] = i
	}
	out := make([][6]float64, len(cands))
	for i, c := range cands {
		out[i] = [6]float64{round6(c.cx), round6(c.cy), round6(c.cx), round6(c.cy), round6(c.cx), round6(c.cy)}
	}
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
		rows, err := s.DB.QueryContext(ctx, `
			SELECT id, geojson FROM feature_geometries
			WHERE id IN (`+strings.Join(ph, ",")+`)`, args...)
		if err != nil {
			return nil
		}
		buf := make([][2]float64, 0, 256)
		for rows.Next() {
			var id int64
			var gj string
			if err := rows.Scan(&id, &gj); err != nil {
				continue
			}
			i, ok := idx[id]
			if !ok {
				continue
			}
			pts := scanCoordPairs(gj, buf[:0])
			if len(pts) == 0 {
				continue
			}
			a, b := pts[0], pts[len(pts)-1]
			// The middle point is the actual middle VERTEX, not the average of
			// the ends: a fire that ran out and doubled back would otherwise
			// draw as a straight line through ground it never touched.
			m := pts[len(pts)/2]
			out[i] = [6]float64{round6(a[0]), round6(a[1]),
				round6(m[0]), round6(m[1]), round6(b[0]), round6(b[1])}
		}
		rows.Close()
	}
	return out
}

// scanCoordPairs pulls every [x, y] pair out of a GeoJSON geometry string.
//
// Deliberately a byte scan rather than json.Unmarshal or SQLite's json_extract:
// this runs for tens of thousands of rows in one request and only ever needs
// three of the ~15 points. Six `json_extract` calls per row (two of them with a
// concatenated path, so the document is re-parsed each time) measured 30 us a
// row — 1.2 s for one continental view; this is ~1 us. Nesting is irrelevant
// because every geometry we serve is a flat list of positions or a list of
// rings of them, and a chord across the ring order is what we want either way.
func scanCoordPairs(s string, out [][2]float64) [][2]float64 {
	i := strings.Index(s, `"coordinates"`)
	if i < 0 {
		return out
	}
	for ; i < len(s); i++ {
		c := s[i]
		if c != '[' {
			continue
		}
		// A pair opens with a number, an array-of-arrays with another '['.
		j := i + 1
		for j < len(s) && (s[j] == ' ' || s[j] == '\n') {
			j++
		}
		if j >= len(s) || s[j] == '[' {
			continue
		}
		end := strings.IndexByte(s[j:], ']')
		if end < 0 {
			break
		}
		body := s[j : j+end]
		comma := strings.IndexByte(body, ',')
		if comma < 0 {
			i = j + end
			continue
		}
		x, err1 := strconv.ParseFloat(strings.TrimSpace(body[:comma]), 64)
		yStr := body[comma+1:]
		if k := strings.IndexByte(yStr, ','); k >= 0 { // z or m, ignored
			yStr = yStr[:k]
		}
		y, err2 := strconv.ParseFloat(strings.TrimSpace(yStr), 64)
		if err1 == nil && err2 == nil {
			out = append(out, [2]float64{x, y})
		}
		i = j + end
	}
	return out
}
