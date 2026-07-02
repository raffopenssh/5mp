package srv

// Incremental road learning from vehicle tracks.
//
// The original design had two road-learning paths that were both dead in
// practice (see docs/GPX_CLASSIFIER_HANDOFF.md "Known issue"):
//   - processRoadSegment only ran for Classification=="road", which the
//     classifier intentionally never assigns to vehicle movement.
//   - Cross-track analysis needed >=5 tracks in the same 10m cell within a
//     single upload; EarthRanger fixes are ~700m apart, so no cell ever
//     accumulated traffic.
//
// This path replaces both for roads. For every NEW vehicle track:
//   1. Resample the polyline to ~50m steps (bridging sparse ER fixes,
//      breaking at gaps >5km).
//   2. Subtract portions within 100m of HeiGIT reference roads — those are
//      already known and nothing needs learning.
//   3. Each remaining piece >=300m is matched against existing learned_roads
//      corridors (>=60% of points within 150m). A match increments
//      match_count (+25% confidence per traversal, capped 95); no match
//      creates a new pending learned_roads row at 25%.
//   4. Roads with confidence>=90 and >=5 traversals auto-approve into
//      feature_geometries (reuses autoApproveRoad).
//
// Corridor state is rebuilt from the DB on each learner job — nothing is
// held in memory, so confidence accumulates naturally as 4-hourly
// EarthRanger autofetch data arrives.

import (
	"context"
	"database/sql"
	"encoding/json"
	"log/slog"
	"math"

	"srv.exe.dev/db/dbgen"
)

const (
	roadResampleStepM   = 50.0   // resampling step along track
	roadMaxGapM         = 5000.0 // break track at fixes further apart than this
	roadHeigitMatchM    = 100.0  // distance to HeiGIT road = "already known"
	roadCorridorMatchM  = 150.0  // distance to learned road corridor = same road
	roadCorridorOverlap = 0.6    // fraction of points that must match
	roadMinPieceM       = 300.0  // ignore unmatched scraps shorter than this
)

// corridorIndex is a spatial hash of line segments for fast
// point-within-distance queries. Cells are ~indexCellM meters.
type corridorIndex struct {
	cellM float64
	grid  map[[2]int][][2][2]float64 // cell -> segments [[lonA,latA],[lonB,latB]]
	kx    float64                    // meters per degree lon
	ky    float64                    // meters per degree lat
}

func newCorridorIndex(refLat float64) *corridorIndex {
	return &corridorIndex{
		cellM: 250.0,
		grid:  make(map[[2]int][][2][2]float64),
		kx:    111320.0 * math.Cos(refLat*math.Pi/180.0),
		ky:    110540.0,
	}
}

func (c *corridorIndex) cellOf(lon, lat float64) [2]int {
	return [2]int{int(math.Floor(lon * c.kx / c.cellM)), int(math.Floor(lat * c.ky / c.cellM))}
}

// addLine indexes a polyline. Each segment is registered in every cell it
// passes through plus the 8 neighbours, so a query only needs its own cell.
func (c *corridorIndex) addLine(coords [][]float64) {
	for i := 0; i+1 < len(coords); i++ {
		a, b := coords[i], coords[i+1]
		seg := [2][2]float64{{a[0], a[1]}, {b[0], b[1]}}
		steps := int(havMeters(a, b)/c.cellM) + 1
		seen := make(map[[2]int]bool)
		for s := 0; s <= steps; s++ {
			t := float64(s) / float64(steps)
			cell := c.cellOf(a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t)
			for dx := -1; dx <= 1; dx++ {
				for dy := -1; dy <= 1; dy++ {
					k := [2]int{cell[0] + dx, cell[1] + dy}
					if !seen[k] {
						seen[k] = true
						c.grid[k] = append(c.grid[k], seg)
					}
				}
			}
		}
	}
}

// near reports whether pt=[lon,lat] is within threshM of any indexed segment.
func (c *corridorIndex) near(pt []float64, threshM float64) bool {
	for _, seg := range c.grid[c.cellOf(pt[0], pt[1])] {
		if pointSegDistMeters(pt, seg[0][:], seg[1][:]) <= threshM {
			return true
		}
	}
	return false
}

// havMeters returns the haversine distance between [lon,lat] points in
// METERS (the shared haversineDistance helper returns kilometers).
func havMeters(a, b []float64) float64 {
	return haversineDistance(a[1], a[0], b[1], b[0]) * 1000.0
}

// pointSegDistMeters is an equirectangular point-to-segment distance in meters.
func pointSegDistMeters(p, a, b []float64) float64 {
	kx := 111320.0 * math.Cos(p[1]*math.Pi/180.0)
	ky := 110540.0
	px, py := (p[0]-a[0])*kx, (p[1]-a[1])*ky
	bx, by := (b[0]-a[0])*kx, (b[1]-a[1])*ky
	l2 := bx*bx + by*by
	t := 0.0
	if l2 > 0 {
		t = math.Max(0, math.Min(1, (px*bx+py*by)/l2))
	}
	return math.Hypot(px-t*bx, py-t*by)
}

// resampleLine returns pieces of the track resampled to ~stepM point spacing,
// splitting where consecutive input points are further than maxGapM apart.
func resampleLine(coords [][]float64, stepM, maxGapM float64) [][][]float64 {
	var pieces [][][]float64
	var cur [][]float64
	for i := 0; i+1 < len(coords); i++ {
		a, b := coords[i], coords[i+1]
		d := havMeters(a, b)
		if d > maxGapM {
			if len(cur) >= 2 {
				pieces = append(pieces, cur)
			}
			cur = nil
			continue
		}
		n := int(d/stepM) + 1
		for s := 0; s < n; s++ {
			t := float64(s) / float64(n)
			cur = append(cur, []float64{a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t})
		}
	}
	if len(coords) >= 1 && cur != nil {
		cur = append(cur, coords[len(coords)-1])
	}
	if len(cur) >= 2 {
		pieces = append(pieces, cur)
	}
	return pieces
}

// heigitIndexForPark loads all HeiGIT reference roads for a park into a
// corridor index. Handles LineString and MultiLineString geometries.
func (l *GPXLearner) heigitIndexForPark(ctx context.Context, parkID string) *corridorIndex {
	rows, err := l.db.QueryContext(ctx, `SELECT geojson FROM roads_heigit WHERE park_id = ?`, parkID)
	if err != nil {
		return nil
	}
	defer rows.Close()

	var idx *corridorIndex
	for rows.Next() {
		var gj string
		if rows.Scan(&gj) != nil {
			continue
		}
		var geom struct {
			Type        string          `json:"type"`
			Coordinates json.RawMessage `json:"coordinates"`
		}
		if json.Unmarshal([]byte(gj), &geom) != nil {
			continue
		}
		var lines [][][]float64
		switch geom.Type {
		case "MultiLineString":
			json.Unmarshal(geom.Coordinates, &lines)
		default: // LineString
			var line [][]float64
			if json.Unmarshal(geom.Coordinates, &line) == nil && len(line) >= 2 {
				lines = [][][]float64{line}
			}
		}
		for _, line := range lines {
			if len(line) < 2 {
				continue
			}
			if idx == nil {
				idx = newCorridorIndex(line[0][1])
			}
			idx.addLine(line)
		}
	}
	return idx
}

// learnedRoadCandidate is an existing learned_roads row with its corridor index.
type learnedRoadCandidate struct {
	id         int64
	geojson    string
	lengthM    float64
	matchCount int64
	idx        *corridorIndex
}

func (l *GPXLearner) loadLearnedRoadCandidates(ctx context.Context, parkID string) []*learnedRoadCandidate {
	rows, err := l.db.QueryContext(ctx, `
		SELECT id, geojson, COALESCE(length_m,0), match_count
		FROM learned_roads WHERE park_id = ? AND status != 'rejected'
		ORDER BY id LIMIT 2000`, parkID)
	if err != nil {
		return nil
	}
	defer rows.Close()

	var out []*learnedRoadCandidate
	for rows.Next() {
		c := &learnedRoadCandidate{}
		if rows.Scan(&c.id, &c.geojson, &c.lengthM, &c.matchCount) != nil {
			continue
		}
		coords := l.parseGeoJSONCoords(c.geojson)
		if len(coords) < 2 {
			continue
		}
		c.idx = newCorridorIndex(coords[0][1])
		c.idx.addLine(coords)
		out = append(out, c)
	}
	return out
}

// learnRoadsFromTrack runs the incremental road-learning pipeline for one
// vehicle track (simplified coords). heigit and candidates are loaded once
// per learner job and mutated (new candidates appended) across calls.
func (l *GPXLearner) learnRoadsFromTrack(ctx context.Context, parkID string, coords [][]float64,
	heigit *corridorIndex, candidates *[]*learnedRoadCandidate, result *LearningResult) {

	for _, piece := range resampleLine(coords, roadResampleStepM, roadMaxGapM) {
		// Subtract portions already covered by HeiGIT reference roads.
		var unmatched [][][]float64
		var cur [][]float64
		for _, pt := range piece {
			if heigit != nil && heigit.near(pt, roadHeigitMatchM) {
				if len(cur) >= 2 {
					unmatched = append(unmatched, cur)
				}
				cur = nil
			} else {
				cur = append(cur, pt)
			}
		}
		if len(cur) >= 2 {
			unmatched = append(unmatched, cur)
		}

		for _, seg := range unmatched {
			lengthM := l.calculateSegmentLength(seg) * 1000
			if lengthM < roadMinPieceM {
				continue
			}

			// Match against existing learned road corridors.
			var match *learnedRoadCandidate
			for _, cand := range *candidates {
				hits := 0
				for _, pt := range seg {
					if cand.idx.near(pt, roadCorridorMatchM) {
						hits++
					}
				}
				if float64(hits)/float64(len(seg)) >= roadCorridorOverlap {
					match = cand
					break
				}
			}

			if match != nil {
				match.matchCount++
				confidence := math.Min(float64(match.matchCount)*25.0, 95.0)
				if err := l.queries.UpdateLearnedRoadMatch(ctx, dbgen.UpdateLearnedRoadMatchParams{
					ConfidencePct: ptrFloat64(confidence),
					ID:            match.id,
				}); err != nil {
					slog.Error("road learner: update match failed", "id", match.id, "error", err)
					continue
				}
				if match.matchCount >= 2 {
					result.NewRoads++
					result.NewRoadsKm += match.lengthM / 1000
					if confidence > result.RoadConfidence {
						result.RoadConfidence = confidence
					}
				}
				if confidence >= AutoApprovalConfidenceThreshold && match.matchCount >= AutoApprovalMinTraversals {
					l.autoApproveRoad(ctx, parkID, match.id, match.geojson, match.lengthM, match.matchCount, confidence)
				}
			} else {
				// New candidate road. Store the ORIGINAL-resolution shape for
				// the matched span, not the resampled one, by simplifying the
				// resampled piece (10m) — visually equivalent, compact.
				stored := l.simplifyCoords(seg, 10.0)
				gj, _ := json.Marshal(map[string]interface{}{
					"type":        "LineString",
					"coordinates": stored,
				})
				id, err := l.queries.CreateLearnedRoad(ctx, dbgen.CreateLearnedRoadParams{
					ParkID:        parkID,
					Geojson:       string(gj),
					LengthM:       ptrFloat64(lengthM),
					MatchCount:    ptrInt64(1),
					ConfidencePct: ptrFloat64(25.0),
				})
				if err != nil {
					slog.Error("road learner: create failed", "park", parkID, "error", err)
					continue
				}
				cand := &learnedRoadCandidate{id: id, geojson: string(gj), lengthM: lengthM, matchCount: 1}
				cand.idx = newCorridorIndex(seg[0][1])
				cand.idx.addLine(seg)
				*candidates = append(*candidates, cand)
			}
		}
	}
}

// vehicleTrackExists reports whether an identical simplified geometry is
// already stored for this park — 4-hourly ER autofetch and requeues can
// re-deliver the same track; learning it twice would inflate match counts.
func (l *GPXLearner) vehicleTrackExists(ctx context.Context, parkID, geojson string) bool {
	var id int64
	err := l.db.QueryRowContext(ctx,
		`SELECT id FROM vehicle_tracks WHERE park_id = ? AND geojson = ? LIMIT 1`,
		parkID, geojson).Scan(&id)
	return err == nil
}

// BackfillLearnedRoads replays all stored vehicle_tracks (deduplicated,
// oldest first) through the road learner. Safe to run repeatedly only on an
// empty learned_roads table — otherwise match counts double-count history.
func (l *GPXLearner) BackfillLearnedRoads(ctx context.Context) error {
	rows, err := l.db.QueryContext(ctx, `
		SELECT park_id, MIN(id) AS mid, geojson FROM vehicle_tracks
		GROUP BY park_id, geojson ORDER BY mid`)
	if err != nil {
		return err
	}
	type tr struct {
		parkID, geojson string
	}
	var tracks []tr
	for rows.Next() {
		var t tr
		var mid int64
		if rows.Scan(&t.parkID, &mid, &t.geojson) == nil {
			tracks = append(tracks, t)
		}
	}
	rows.Close()

	heigitByPark := map[string]*corridorIndex{}
	candsByPark := map[string]*[]*learnedRoadCandidate{}
	result := &LearningResult{}
	n := 0
	for _, t := range tracks {
		coords := l.parseGeoJSONCoords(t.geojson)
		if len(coords) < 2 {
			continue
		}
		heigit, ok := heigitByPark[t.parkID]
		if !ok {
			heigit = l.heigitIndexForPark(ctx, t.parkID)
			heigitByPark[t.parkID] = heigit
		}
		cands, ok := candsByPark[t.parkID]
		if !ok {
			c := l.loadLearnedRoadCandidates(ctx, t.parkID)
			cands = &c
			candsByPark[t.parkID] = cands
		}
		l.learnRoadsFromTrack(ctx, t.parkID, coords, heigit, cands, result)
		n++
	}
	slog.Info("road learner backfill complete", "tracks", n, "roads_with_2plus", result.NewRoads)
	return nil
}

var _ = sql.ErrNoRows // keep database/sql import if unused in future edits
