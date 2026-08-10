// Package srv provides GPX validation and classification for conservation patrol tracking.
package srv

import (
	"encoding/json"
	"fmt"
	"math"
	"time"

	"srv.exe.dev/srv/gpx"
)

// GPXValidationResult contains the results of GPX file validation and classification
type GPXValidationResult struct {
	IsValid            bool     `json:"is_valid"`
	TotalPoints        int      `json:"total_points"`
	ValidationErrors   []string `json:"validation_errors,omitempty"`
	ValidationWarnings []string `json:"validation_warnings,omitempty"`

	// Classification
	ClassifiedSegments []ClassifiedSegment `json:"classified_segments"`

	// Stats for user feedback
	PatrolKm         float64 `json:"patrol_km"`
	RoadKm           float64 `json:"road_km"`
	BoundaryKm       float64 `json:"boundary_km"`
	StaticSegments   int     `json:"static_segments"`
	ExcludedSegments int     `json:"excluded_segments"`
	ExcludedKm       float64 `json:"excluded_km"`

	// Detailed movement stats
	MovementStats MovementStats `json:"movement_stats"`

	// Protected area detection
	ProtectedAreaID   string `json:"protected_area_id,omitempty"`
	ProtectedAreaName string `json:"protected_area_name,omitempty"`
}

// MovementStats provides detailed breakdown by movement and activity type
type MovementStats struct {
	// By movement type
	FootSegments     int     `json:"foot_segments"`
	FootKm           float64 `json:"foot_km"`
	FootMinutes      float64 `json:"foot_minutes"`
	VehicleSegments  int     `json:"vehicle_segments"`
	VehicleKm        float64 `json:"vehicle_km"`
	VehicleMinutes   float64 `json:"vehicle_minutes"`
	AircraftSegments int     `json:"aircraft_segments"`
	AircraftKm       float64 `json:"aircraft_km"`
	AircraftMinutes  float64 `json:"aircraft_minutes"`

	// Movement subtypes
	BoatSegments      int     `json:"boat_segments"` // Boat (subtype of vehicle)
	BoatKm            float64 `json:"boat_km"`
	BoatMinutes       float64 `json:"boat_minutes"`
	FixedWingSegments int     `json:"fixed_wing_segments"` // Fixed-wing (subtype of aircraft)
	FixedWingKm       float64 `json:"fixed_wing_km"`
	FixedWingMinutes  float64 `json:"fixed_wing_minutes"`
	RotorWingSegments int     `json:"rotor_wing_segments"` // Helicopter (subtype of aircraft)
	RotorWingKm       float64 `json:"rotor_wing_km"`
	RotorWingMinutes  float64 `json:"rotor_wing_minutes"`

	// Special categories for admin insights
	ReconSegments       int     `json:"recon_segments"` // Foot 0.5-4 km/h (reconnaissance)
	ReconKm             float64 `json:"recon_km"`
	ReconMinutes        float64 `json:"recon_minutes"`
	FastVehicleSegments int     `json:"fast_vehicle_segments"` // Vehicle >60 km/h (transit)
	FastVehicleKm       float64 `json:"fast_vehicle_km"`
	FastVehicleMinutes  float64 `json:"fast_vehicle_minutes"`

	// By activity type
	PatrolSegments    int     `json:"patrol_segments"`
	PatrolKm          float64 `json:"patrol_km_total"`
	TransitSegments   int     `json:"transit_segments"`
	TransitKm         float64 `json:"transit_km"`
	LogisticsSegments int     `json:"logistics_segments"`
	LogisticsKm       float64 `json:"logistics_km"`
}

// ClassifiedSegment represents a classified portion of a GPX track
type ClassifiedSegment struct {
	Classification  string        `json:"classification"`             // patrol, boundary, road, poi, static, auto_generated, aircraft
	MovementType    string        `json:"movement_type,omitempty"`    // foot, vehicle, aircraft
	MovementSubtype string        `json:"movement_subtype,omitempty"` // boat, fixed_wing, rotor_wing
	ActivityType    string        `json:"activity_type,omitempty"`    // patrol, reconnaissance, transit, logistics
	StartIndex      int           `json:"start_index"`
	EndIndex        int           `json:"end_index"`
	DistanceKm      float64       `json:"distance_km"`
	Duration        time.Duration `json:"-"`
	DurationStr     string        `json:"duration,omitempty"`
	AvgSpeedKmh     float64       `json:"avg_speed_kmh,omitempty"`
	Smoothness      float64       `json:"smoothness,omitempty"` // 0-1, trajectory smoothness
	Reason          string        `json:"reason"`
	IncludeInEffort bool          `json:"include_in_effort"`
	GeoJSON         string        `json:"geojson,omitempty"`
	Points          []gpx.Point   `json:"-"`                          // For internal processing
	OriginalIndices []int         `json:"original_indices,omitempty"` // Input segment indices covered by this classified segment (after merging)

	// SampledPoints is a compact serialization of (up to ~300) evenly sampled
	// track points: [lon, lat, unixSeconds, elevM]. unixSeconds is 0 when the
	// point has no timestamp; elevM is sampledElevMissing when absent.
	// This lets the background learner recover per-segment points exactly,
	// instead of guessing indices into the (sampled, effort-only) track_points
	// table — which broke for any upload with >1000 points or excluded segments.
	SampledPoints [][4]float64 `json:"sampled_points,omitempty"`
}

// sampledElevMissing is the sentinel elevation for points without elevation data.
const sampledElevMissing = -100000

// sampledPointsMax caps how many points are embedded per classified segment.
const sampledPointsMax = 300

// buildSampledPoints compactly serializes points for storage in
// classified_segments_json (see ClassifiedSegment.SampledPoints).
func buildSampledPoints(points []gpx.Point) [][4]float64 {
	if len(points) == 0 {
		return nil
	}
	n := len(points)
	out := make([][4]float64, 0, min(n, sampledPointsMax))
	appendPt := func(pt gpx.Point) {
		var ts, elev float64 = 0, sampledElevMissing
		if pt.Time != nil {
			ts = float64(pt.Time.Unix())
		}
		if pt.Elevation != nil {
			elev = *pt.Elevation
		}
		out = append(out, [4]float64{pt.Lon, pt.Lat, ts, elev})
	}
	if n <= sampledPointsMax {
		for _, pt := range points {
			appendPt(pt)
		}
		return out
	}
	step := float64(n-1) / float64(sampledPointsMax-1)
	for i := 0; i < sampledPointsMax; i++ {
		idx := int(math.Round(float64(i) * step))
		if idx >= n {
			idx = n - 1
		}
		appendPt(points[idx])
	}
	return out
}

// decodeSampledPoints reverses buildSampledPoints.
func decodeSampledPoints(sp [][4]float64) []gpx.Point {
	points := make([]gpx.Point, 0, len(sp))
	for _, v := range sp {
		pt := gpx.Point{Lon: v[0], Lat: v[1]}
		if v[2] != 0 {
			t := time.Unix(int64(v[2]), 0).UTC()
			pt.Time = &t
		}
		if v[3] != sampledElevMissing {
			elev := v[3]
			pt.Elevation = &elev
		}
		points = append(points, pt)
	}
	return points
}

// MinimumWaypoints is the minimum required waypoints for a valid GPX file
const MinimumWaypoints = 10

// ValidateAndClassifyGPX validates a GPX file and classifies its segments.
// It accepts pre-processed segments (already split and gap-cleaned) so that
// upstream processing like RemoveStraightLineGaps is respected.
func ValidateAndClassifyGPX(segments []gpx.Segment) *GPXValidationResult {
	result := &GPXValidationResult{
		IsValid:            true,
		ClassifiedSegments: []ClassifiedSegment{},
	}

	// Count total points from pre-processed segments
	for _, seg := range segments {
		result.TotalPoints += len(seg.Points)
	}

	// Validation: minimum points
	if result.TotalPoints < MinimumWaypoints {
		result.IsValid = false
		result.ValidationErrors = append(result.ValidationErrors,
			fmt.Sprintf("Insufficient waypoints: %d (minimum %d required)", result.TotalPoints, MinimumWaypoints))
		return result
	}

	// Process each pre-processed segment
	for segIdx, timeSeg := range segments {
		seg := timeSeg.Points
		if len(seg) < 2 {
			continue
		}

		// Check for auto-generated patterns
		if isAutoGenerated(seg) {
			classified := ClassifiedSegment{
				Classification:  "auto_generated",
				StartIndex:      0,
				EndIndex:        len(seg) - 1,
				DistanceKm:      calculateSegmentDistance(seg),
				Reason:          "Detected systematic pattern (regular intervals or grid coordinates)",
				IncludeInEffort: false,
				OriginalIndices: []int{segIdx},
			}
			result.ClassifiedSegments = append(result.ClassifiedSegments, classified)
			result.ExcludedSegments++
			result.ExcludedKm += classified.DistanceKm
			result.ValidationWarnings = append(result.ValidationWarnings,
				"Segment detected as auto-generated and excluded from effort mapping")
			continue
		}

		// Check for static segments
		staticSegs := detectStaticSegments(seg)
		if len(staticSegs) > 0 {
			for _, ss := range staticSegs {
				ss.OriginalIndices = []int{segIdx}
				result.ClassifiedSegments = append(result.ClassifiedSegments, ss)
				result.StaticSegments++
				result.ExcludedKm += ss.DistanceKm
			}
		}

		// Check for boundary traces (skip for aircraft/vehicle — a circular
		// survey flight is not a park boundary digitization). Consider both the
		// external hint AND the segment's movement-based classification.
		isAirOrVehicle := timeSeg.Hint.Type == "aircraft" || timeSeg.Hint.Type == "vehicle" ||
			timeSeg.MovementType == "aircraft" || timeSeg.MovementType == "vehicle"
		if isBoundaryTrace(seg) && !isAirOrVehicle {
			classified := ClassifiedSegment{
				Classification:  "boundary",
				StartIndex:      0,
				EndIndex:        len(seg) - 1,
				DistanceKm:      calculateSegmentDistance(seg),
				Reason:          "Detected as boundary trace (closed polygon)",
				IncludeInEffort: false,
				GeoJSON:         pointsToGeoJSON(seg),
				OriginalIndices: []int{segIdx},
			}
			result.ClassifiedSegments = append(result.ClassifiedSegments, classified)
			result.BoundaryKm += classified.DistanceKm
			continue
		}

		// Check for road traces (skip for aircraft/vehicle — straight survey transects
		// and highway driving are not road digitizations)
		if isRoadTrace(seg) && !isAirOrVehicle {
			classified := ClassifiedSegment{
				Classification:  "road",
				StartIndex:      0,
				EndIndex:        len(seg) - 1,
				DistanceKm:      calculateSegmentDistance(seg),
				Reason:          "Detected as road trace (high bearing consistency)",
				IncludeInEffort: false,
				GeoJSON:         pointsToGeoJSON(seg),
				OriginalIndices: []int{segIdx},
			}
			result.ClassifiedSegments = append(result.ClassifiedSegments, classified)
			result.RoadKm += classified.DistanceKm
			continue
		}

		// Use full movement classification (movement type + activity type)
		// Pass through any movement hints from EarthRanger metadata, Locus, etc.
		hint := timeSeg.Hint
		var classification gpx.MovementClassification
		if len(seg) >= 3 {
			classification = gpx.ClassifyMovementFullWithHint(seg, hint)
		} else if len(seg) >= 2 {
			avgSpeed := gpx.CalculateSpeed(seg)
			classification.Metrics.AvgSpeedKmh = avgSpeed
			// Use hint for small segments too
			if hint.Type != "" && hint.Confidence >= 0.9 {
				classification.MovementType = hint.Type
				classification.ActivityType = "patrol"
			} else if avgSpeed < 7 {
				classification.MovementType = "foot"
				classification.ActivityType = "patrol"
			} else if avgSpeed < 100 {
				classification.MovementType = "vehicle"
				classification.ActivityType = "patrol"
			} else {
				classification.MovementType = "aircraft"
				// 2-point segments are too short to be meaningful transport;
				// they're inter-segment fragments or GPS artifacts.
				classification.ActivityType = "patrol"
			}
		}

		distanceKm := calculateSegmentDistance(seg)
		avgSpeed := classification.Metrics.AvgSpeedKmh
		smooth := classification.Metrics.SmoothnessFactor
		movementType := classification.MovementType
		activityType := classification.ActivityType

		// Build reason string
		var reason string
		switch movementType {
		case "aircraft":
			reason = fmt.Sprintf("Aircraft %s (avg %.0f km/h, smoothness %.2f)", activityType, avgSpeed, smooth)
		case "vehicle":
			if avgSpeed > 60 {
				reason = fmt.Sprintf("Vehicle %s - fast (avg %.0f km/h, smoothness %.2f)", activityType, avgSpeed, smooth)
			} else {
				reason = fmt.Sprintf("Vehicle %s (avg %.0f km/h)", activityType, avgSpeed)
			}
		case "foot":
			if activityType == "reconnaissance" {
				reason = fmt.Sprintf("Foot reconnaissance (avg %.1f km/h)", avgSpeed)
			} else {
				reason = fmt.Sprintf("Foot patrol (avg %.1f km/h)", avgSpeed)
			}
		}

		// Determine classification type and whether to include in patrol effort
		classType := "patrol"
		includeInEffort := true
		if movementType == "aircraft" {
			classType = "aircraft"
			// Include ALL aircraft segments in effort until the logistics/survey
			// classifier is reliable enough to distinguish them.
		}

		// Filter out idle/stationary segments: a GPS tracker pinging while
		// parked (0 km/h, <10m total distance) is not patrol effort.
		// These create phantom grid pixels without meaningful coverage.
		const minEffortDistanceKm = 0.01 // 10 meters
		if distanceKm < minEffortDistanceKm {
			includeInEffort = false
			classType = "idle"
		}

		classified := ClassifiedSegment{
			Classification:  classType,
			MovementType:    movementType,
			MovementSubtype: classification.MovementSubtype,
			ActivityType:    activityType,
			StartIndex:      0,
			EndIndex:        len(seg) - 1,
			DistanceKm:      distanceKm,
			AvgSpeedKmh:     avgSpeed,
			Smoothness:      smooth,
			Reason:          reason,
			IncludeInEffort: includeInEffort,
			Points:          seg,
			OriginalIndices: []int{segIdx},
		}

		// Calculate duration
		var durationMin float64
		if len(seg) > 0 && seg[0].Time != nil && seg[len(seg)-1].Time != nil {
			duration := seg[len(seg)-1].Time.Sub(*seg[0].Time)
			classified.Duration = duration
			classified.DurationStr = formatDuration(duration)
			durationMin = duration.Minutes()
		}

		result.ClassifiedSegments = append(result.ClassifiedSegments, classified)

		// Update stats
		if includeInEffort {
			result.PatrolKm += distanceKm
		} else if classType != "idle" {
			// Don't count idle segments as "excluded" — they're just noise
			result.ExcludedKm += distanceKm
		}

		// Skip idle segments from movement stats entirely
		if classType == "idle" {
			result.StaticSegments++
			continue
		}

		// Update movement stats
		switch movementType {
		case "foot":
			result.MovementStats.FootSegments++
			result.MovementStats.FootKm += distanceKm
			result.MovementStats.FootMinutes += durationMin
			// Reconnaissance: foot patrol at 0.5-4 km/h
			if avgSpeed >= 0.5 && avgSpeed <= 4 {
				result.MovementStats.ReconSegments++
				result.MovementStats.ReconKm += distanceKm
				result.MovementStats.ReconMinutes += durationMin
			}
		case "vehicle":
			result.MovementStats.VehicleSegments++
			result.MovementStats.VehicleKm += distanceKm
			result.MovementStats.VehicleMinutes += durationMin
			// Fast vehicle: >60 km/h (likely transit)
			if avgSpeed > 60 {
				result.MovementStats.FastVehicleSegments++
				result.MovementStats.FastVehicleKm += distanceKm
				result.MovementStats.FastVehicleMinutes += durationMin
			}
		case "aircraft":
			result.MovementStats.AircraftSegments++
			result.MovementStats.AircraftKm += distanceKm
			result.MovementStats.AircraftMinutes += durationMin
		}

		// Update movement subtype stats
		switch classification.MovementSubtype {
		case "boat":
			result.MovementStats.BoatSegments++
			result.MovementStats.BoatKm += distanceKm
			result.MovementStats.BoatMinutes += durationMin
		case "fixed_wing":
			result.MovementStats.FixedWingSegments++
			result.MovementStats.FixedWingKm += distanceKm
			result.MovementStats.FixedWingMinutes += durationMin
		case "rotor_wing":
			result.MovementStats.RotorWingSegments++
			result.MovementStats.RotorWingKm += distanceKm
			result.MovementStats.RotorWingMinutes += durationMin
		}

		// Update activity stats
		switch activityType {
		case "patrol", "reconnaissance":
			result.MovementStats.PatrolSegments++
			result.MovementStats.PatrolKm += distanceKm
		case "transit":
			result.MovementStats.TransitSegments++
			result.MovementStats.TransitKm += distanceKm
		case "logistics":
			result.MovementStats.LogisticsSegments++
			result.MovementStats.LogisticsKm += distanceKm
		}
	}

	// === Post-classification merge pass ===
	// Merge adjacent segments to fix fragmentation from 30-min time splitting.
	// When a flight is split into [aircraft, tiny-foot, aircraft], the tiny
	// middle segment is a boundary artifact, not real foot patrol.
	result.ClassifiedSegments = mergeAdjacentSegments(result.ClassifiedSegments)

	// Recompute all stats from merged segments (the per-segment accumulation
	// above is now stale because segments were merged/absorbed).
	recomputeStatsFromSegments(result)

	// Embed compact sampled points for the background learner. Points is
	// json:"-" (too large), and reconstructing from the track_points table by
	// index is unreliable (sampling + excluded segments shift offsets).
	for i := range result.ClassifiedSegments {
		cs := &result.ClassifiedSegments[i]
		if len(cs.Points) > 0 && cs.SampledPoints == nil {
			cs.SampledPoints = buildSampledPoints(cs.Points)
		}
	}

	// Final validation checks
	if result.PatrolKm == 0 && result.TotalPoints >= MinimumWaypoints {
		result.ValidationWarnings = append(result.ValidationWarnings,
			"No valid patrol segments detected - all data was classified as boundaries, roads, or auto-generated")
	}

	return result
}

// isAutoGenerated detects if a segment appears to be computer-generated
// Checks for:
// 1. Sub-second time intervals (e.g., 0.001s between points)
// 2. Grid-pattern coordinates (coordinates on exact grid lines)
// 3. Impossible speeds combined with microsecond intervals
// NOTE: Regular 3-second intervals from GPS devices are NORMAL and should NOT be flagged
// mergeAdjacentSegments performs a post-classification merge to fix
// fragmentation caused by rigid 30-minute time-window splitting.
//
// Problem: A continuous flight split into 30-min windows produces many
// tiny segments at boundaries. A 2-point segment between two aircraft
// segments gets classified as "foot" because there's not enough data
// to determine it's aircraft. These phantom segments create wrong grid pixels.
//
// Strategy:
//  1. Absorb orphans: A small segment (< 5 points AND < 200m) sitting between
//     two segments of the same movement type gets absorbed into the preceding one.
//  2. Merge consecutive: Adjacent segments with the same movement type and
//     classification are merged into one.
//  3. Idle segments from ER hints: If a segment is "idle" but has a strong
//     ER hint, leave it idle (parked aircraft is still idle).
func mergeAdjacentSegments(segs []ClassifiedSegment) []ClassifiedSegment {
	if len(segs) < 3 {
		return segs
	}

	mergeableType := func(cs *ClassifiedSegment) bool {
		return cs.Classification == "patrol" || cs.Classification == "aircraft" || cs.Classification == "idle"
	}

	// absorbable: small segment that can be absorbed into a neighbor.
	absorbable := func(cs *ClassifiedSegment) bool {
		if !mergeableType(cs) {
			return false
		}
		nPts := cs.EndIndex - cs.StartIndex + 1
		return nPts < 5 && cs.DistanceKm < 0.2
	}

	// mergeInto absorbs src into dst.
	mergeInto := func(dst, src *ClassifiedSegment) {
		dst.DistanceKm += src.DistanceKm
		dst.Points = append(dst.Points, src.Points...)
		dst.EndIndex += src.EndIndex - src.StartIndex + 1
		dst.OriginalIndices = append(dst.OriginalIndices, src.OriginalIndices...)
		dst.IncludeInEffort = dst.IncludeInEffort || src.IncludeInEffort
		// Preserve subtype from whichever segment has it
		if dst.MovementSubtype == "" {
			dst.MovementSubtype = src.MovementSubtype
		}
		if len(dst.Points) > 1 {
			dst.AvgSpeedKmh = gpx.CalculateSpeed(dst.Points)
		}
		if len(dst.Points) > 0 {
			first := dst.Points[0]
			last := dst.Points[len(dst.Points)-1]
			if first.Time != nil && last.Time != nil {
				dst.Duration = last.Time.Sub(*first.Time)
				dst.DurationStr = formatDuration(dst.Duration)
			}
		}
	}

	// updateReason sets the reason string for a merged segment.
	updateReason := func(cs *ClassifiedSegment) {
		if len(cs.OriginalIndices) <= 1 {
			return
		}
		switch cs.MovementType {
		case "aircraft":
			cs.Reason = fmt.Sprintf("Aircraft %s (avg %.0f km/h, %.1f km merged from %d segments)",
				cs.ActivityType, cs.AvgSpeedKmh, cs.DistanceKm, len(cs.OriginalIndices))
		case "vehicle":
			cs.Reason = fmt.Sprintf("Vehicle %s (avg %.0f km/h, %.1f km merged from %d segments)",
				cs.ActivityType, cs.AvgSpeedKmh, cs.DistanceKm, len(cs.OriginalIndices))
		case "foot":
			cs.Reason = fmt.Sprintf("Foot %s (avg %.1f km/h, %.1f km merged from %d segments)",
				cs.ActivityType, cs.AvgSpeedKmh, cs.DistanceKm, len(cs.OriginalIndices))
		}
	}

	// Pass 1: Merge consecutive segments of the same movement type + classification.
	// This collapses chains like [foot, foot, foot] into one segment FIRST,
	// so the orphan pass can then see the merged result between its neighbors.
	consecMerge := func(input []ClassifiedSegment) []ClassifiedSegment {
		if len(input) < 2 {
			return input
		}
		var out []ClassifiedSegment
		current := input[0]
		for i := 1; i < len(input); i++ {
			nxt := input[i]
			if mergeableType(&current) && mergeableType(&nxt) &&
				current.MovementType == nxt.MovementType &&
				current.Classification == nxt.Classification {
				mergeInto(&current, &nxt)
				updateReason(&current)
			} else {
				out = append(out, current)
				current = nxt
			}
		}
		out = append(out, current)
		return out
	}

	// Pass 2: Absorb orphan segments between same-type neighbors.
	// Iterate until stable — absorbing one orphan may expose another.
	orphanAbsorb := func(input []ClassifiedSegment) []ClassifiedSegment {
		for iter := 0; iter < 5; iter++ { // max 5 rounds to avoid infinite loop
			changed := false
			absorbed := make([]bool, len(input))
			for i := 1; i < len(input)-1; i++ {
				if absorbed[i] || !absorbable(&input[i]) {
					continue
				}
				// Find previous non-absorbed
				prev := -1
				for p := i - 1; p >= 0; p-- {
					if !absorbed[p] {
						prev = p
						break
					}
				}
				if prev < 0 || !mergeableType(&input[prev]) {
					continue
				}
				// Find next non-absorbed
				next := -1
				for n := i + 1; n < len(input); n++ {
					if !absorbed[n] {
						next = n
						break
					}
				}
				if next < 0 || !mergeableType(&input[next]) {
					continue
				}
				if input[prev].MovementType == input[next].MovementType {
					mergeInto(&input[prev], &input[i])
					absorbed[i] = true
					changed = true
				}
			}
			if !changed {
				break
			}
			// Rebuild without absorbed
			var next []ClassifiedSegment
			for i, s := range input {
				if !absorbed[i] {
					next = append(next, s)
				}
			}
			input = next
		}
		return input
	}

	// Run: consecutive merge → orphan absorb → consecutive merge again
	// (absorption can create new consecutive same-type pairs)
	result := consecMerge(segs)
	result = orphanAbsorb(result)
	result = consecMerge(result)

	// Final: update reasons on merged segments
	for i := range result {
		updateReason(&result[i])
	}

	return result
}

// recomputeStatsFromSegments recalculates all stats from the (potentially merged)
// classified segments. Called after mergeAdjacentSegments to ensure consistency.
func recomputeStatsFromSegments(result *GPXValidationResult) {
	// Reset all stats
	result.PatrolKm = 0
	result.ExcludedKm = 0
	result.RoadKm = 0
	result.BoundaryKm = 0
	result.StaticSegments = 0
	result.ExcludedSegments = 0
	result.MovementStats = MovementStats{}

	for _, cs := range result.ClassifiedSegments {
		durationMin := cs.Duration.Minutes()

		// Km accounting
		switch cs.Classification {
		case "idle":
			result.StaticSegments++
		case "auto_generated":
			result.ExcludedSegments++
			result.ExcludedKm += cs.DistanceKm
		case "boundary":
			result.BoundaryKm += cs.DistanceKm
		case "road":
			result.RoadKm += cs.DistanceKm
		case "aircraft":
			if cs.IncludeInEffort {
				result.PatrolKm += cs.DistanceKm
			} else {
				result.ExcludedKm += cs.DistanceKm
			}
		case "patrol":
			if cs.IncludeInEffort {
				result.PatrolKm += cs.DistanceKm
			} else {
				result.ExcludedKm += cs.DistanceKm
			}
		}

		// Skip idle/non-patrol from movement stats
		if cs.Classification == "idle" || cs.Classification == "auto_generated" ||
			cs.Classification == "boundary" || cs.Classification == "road" {
			continue
		}

		// Movement type stats
		switch cs.MovementType {
		case "foot":
			result.MovementStats.FootSegments++
			result.MovementStats.FootKm += cs.DistanceKm
			result.MovementStats.FootMinutes += durationMin
			if cs.AvgSpeedKmh >= 0.5 && cs.AvgSpeedKmh <= 4 {
				result.MovementStats.ReconSegments++
				result.MovementStats.ReconKm += cs.DistanceKm
				result.MovementStats.ReconMinutes += durationMin
			}
		case "vehicle":
			result.MovementStats.VehicleSegments++
			result.MovementStats.VehicleKm += cs.DistanceKm
			result.MovementStats.VehicleMinutes += durationMin
			if cs.AvgSpeedKmh > 60 {
				result.MovementStats.FastVehicleSegments++
				result.MovementStats.FastVehicleKm += cs.DistanceKm
				result.MovementStats.FastVehicleMinutes += durationMin
			}
		case "aircraft":
			result.MovementStats.AircraftSegments++
			result.MovementStats.AircraftKm += cs.DistanceKm
			result.MovementStats.AircraftMinutes += durationMin
		}

		// Movement subtype stats (boat, fixed_wing, rotor_wing)
		switch cs.MovementSubtype {
		case "boat":
			result.MovementStats.BoatSegments++
			result.MovementStats.BoatKm += cs.DistanceKm
			result.MovementStats.BoatMinutes += durationMin
		case "fixed_wing":
			result.MovementStats.FixedWingSegments++
			result.MovementStats.FixedWingKm += cs.DistanceKm
			result.MovementStats.FixedWingMinutes += durationMin
		case "rotor_wing":
			result.MovementStats.RotorWingSegments++
			result.MovementStats.RotorWingKm += cs.DistanceKm
			result.MovementStats.RotorWingMinutes += durationMin
		}

		// Activity type stats
		switch cs.ActivityType {
		case "patrol", "reconnaissance":
			result.MovementStats.PatrolSegments++
			result.MovementStats.PatrolKm += cs.DistanceKm
		case "transit":
			result.MovementStats.TransitSegments++
			result.MovementStats.TransitKm += cs.DistanceKm
		case "logistics":
			result.MovementStats.LogisticsSegments++
			result.MovementStats.LogisticsKm += cs.DistanceKm
		}
	}
}

func isAutoGenerated(points []gpx.Point) bool {
	if len(points) < 10 {
		return false
	}

	// Check for sub-second intervals - this is the key indicator of auto-generated data
	// Real GPS devices record at least 1 second apart
	subSecondCount := 0
	for i := 1; i < len(points); i++ {
		if points[i].Time != nil && points[i-1].Time != nil {
			interval := points[i].Time.Sub(*points[i-1].Time).Seconds()
			if interval > 0 && interval < 0.5 { // Less than 0.5 seconds = definitely auto-generated
				subSecondCount++
			}
		}
	}
	// If more than 20% of intervals are sub-second, it's auto-generated
	if float64(subSecondCount)/float64(len(points)-1) > 0.2 {
		return true
	}

	// Check for grid pattern coordinates
	gridCount := 0
	for _, pt := range points {
		// Check if coordinates are on exact grid (e.g., 0.001 degree increments)
		latFrac := math.Abs(pt.Lat*1000 - math.Round(pt.Lat*1000))
		lonFrac := math.Abs(pt.Lon*1000 - math.Round(pt.Lon*1000))
		if latFrac < 0.0001 && lonFrac < 0.0001 {
			gridCount++
		}
	}
	// If more than 80% of points are on exact grid, suspicious
	if float64(gridCount)/float64(len(points)) > 0.8 {
		return true
	}

	// Check for impossible speeds (> 500 km/h consistently)
	impossibleSpeedCount := 0
	for i := 1; i < len(points); i++ {
		if points[i].Time != nil && points[i-1].Time != nil {
			duration := points[i].Time.Sub(*points[i-1].Time).Hours()
			if duration > 0 {
				dist := haversineDistanceKm(points[i-1].Lat, points[i-1].Lon, points[i].Lat, points[i].Lon)
				speed := dist / duration
				if speed > 500 { // > 500 km/h
					impossibleSpeedCount++
				}
			}
		}
	}
	if float64(impossibleSpeedCount)/float64(len(points)) > 0.5 {
		return true
	}

	return false
}

// detectStaticSegments finds segments where the device wasn't moving
// Returns segments where speed < 0.1 km/h for > 30 minutes
func detectStaticSegments(points []gpx.Point) []ClassifiedSegment {
	var staticSegs []ClassifiedSegment

	if len(points) < 5 {
		return staticSegs
	}

	staticStart := -1
	staticStartTime := time.Time{}

	for i := 1; i < len(points); i++ {
		dist := haversineDistanceKm(points[i-1].Lat, points[i-1].Lon, points[i].Lat, points[i].Lon)

		// If movement is very small (< 10 meters)
		if dist < 0.01 {
			if staticStart == -1 {
				staticStart = i - 1
				if points[i-1].Time != nil {
					staticStartTime = *points[i-1].Time
				}
			}
		} else {
			// Movement detected - check if we had a static segment
			if staticStart != -1 && points[i-1].Time != nil && !staticStartTime.IsZero() {
				duration := points[i-1].Time.Sub(staticStartTime)
				if duration > 30*time.Minute {
					seg := ClassifiedSegment{
						Classification:  "static",
						StartIndex:      staticStart,
						EndIndex:        i - 1,
						DistanceKm:      0,
						Duration:        duration,
						DurationStr:     formatDuration(duration),
						Reason:          fmt.Sprintf("Stationary for %s", formatDuration(duration)),
						IncludeInEffort: false,
					}
					staticSegs = append(staticSegs, seg)
				}
			}
			staticStart = -1
			staticStartTime = time.Time{}
		}
	}

	// Check final segment
	if staticStart != -1 && len(points) > 0 && points[len(points)-1].Time != nil && !staticStartTime.IsZero() {
		duration := points[len(points)-1].Time.Sub(staticStartTime)
		if duration > 30*time.Minute {
			seg := ClassifiedSegment{
				Classification:  "static",
				StartIndex:      staticStart,
				EndIndex:        len(points) - 1,
				DistanceKm:      0,
				Duration:        duration,
				DurationStr:     formatDuration(duration),
				Reason:          fmt.Sprintf("Stationary for %s", formatDuration(duration)),
				IncludeInEffort: false,
			}
			staticSegs = append(staticSegs, seg)
		}
	}

	return staticSegs
}

// isBoundaryTrace detects if segment is a park boundary trace
// Must be: very large area (>100 km²), closed polygon, low point density per km² (boundary tracing)
// Normal patrols that return to base should NOT be detected as boundaries
func isBoundaryTrace(points []gpx.Point) bool {
	if len(points) < 100 {
		return false // Boundary traces need many points
	}

	// Check if it forms a closed polygon (start and end within 200m)
	startEnd := haversineDistanceKm(points[0].Lat, points[0].Lon,
		points[len(points)-1].Lat, points[len(points)-1].Lon)
	if startEnd > 0.2 {
		return false // Not a tightly closed polygon
	}

	// Calculate approximate area using shoelace formula
	var area float64
	for i := 0; i < len(points)-1; i++ {
		area += points[i].Lon * points[i+1].Lat
		area -= points[i+1].Lon * points[i].Lat
	}
	area = math.Abs(area) / 2.0

	// Convert to approximate km² (rough conversion at equator)
	areaKm2 := area * 111 * 111

	// Boundary traces must enclose VERY large areas (> 100 km²)
	// Normal patrol walks might cover 1-50 km², so use high threshold
	if areaKm2 < 100.0 {
		return false
	}

	// Calculate total distance walked
	var totalDistKm float64
	for i := 1; i < len(points); i++ {
		totalDistKm += haversineDistanceKm(points[i-1].Lat, points[i-1].Lon,
			points[i].Lat, points[i].Lon)
	}

	// Boundary traces have LOW area-to-perimeter ratio (thin boundary line)
	// A circular area of 100km² has perimeter ~35km, ratio ~2.9
	// Patrol walks have HIGH ratio (wandering inside area)
	if totalDistKm < 10 {
		return false
	}
	ratio := areaKm2 / totalDistKm

	// Boundary traces: ratio < 5 (thin perimeter around large area)
	// Patrol walks: ratio > 5 (lots of walking inside smaller area)
	return ratio < 5.0
}

// isRoadTrace detects if segment is a road network trace
// Characteristics: high bearing consistency, follows linear patterns
func isRoadTrace(points []gpx.Point) bool {
	if len(points) < 10 {
		return false
	}

	// Calculate bearings between consecutive points
	var bearings []float64
	for i := 1; i < len(points); i++ {
		bearing := calculateBearing(points[i-1].Lat, points[i-1].Lon,
			points[i].Lat, points[i].Lon)
		bearings = append(bearings, bearing)
	}

	if len(bearings) < 5 {
		return false
	}

	// Count segments where bearing changes by less than 10 degrees
	straightCount := 0
	for i := 1; i < len(bearings); i++ {
		diff := math.Abs(bearings[i] - bearings[i-1])
		if diff > 180 {
			diff = 360 - diff
		}
		if diff < 10 {
			straightCount++
		}
	}

	// If more than 90% of segments are straight, likely a road trace
	straightRatio := float64(straightCount) / float64(len(bearings)-1)
	if straightRatio > 0.9 {
		return true
	}

	return false
}

// calculateSegmentDistance calculates total distance of a segment
func calculateSegmentDistance(points []gpx.Point) float64 {
	var total float64
	for i := 1; i < len(points); i++ {
		total += haversineDistanceKm(points[i-1].Lat, points[i-1].Lon,
			points[i].Lat, points[i].Lon)
	}
	return total
}

// formatDuration formats a duration as a human-readable string
func formatDuration(d time.Duration) string {
	if d < time.Minute {
		return fmt.Sprintf("%ds", int(d.Seconds()))
	}
	if d < time.Hour {
		return fmt.Sprintf("%dm", int(d.Minutes()))
	}
	hours := int(d.Hours())
	minutes := int(d.Minutes()) % 60
	if minutes > 0 {
		return fmt.Sprintf("%dh%dm", hours, minutes)
	}
	return fmt.Sprintf("%dh", hours)
}

// pointsToGeoJSON converts points to a GeoJSON LineString
func pointsToGeoJSON(points []gpx.Point) string {
	if len(points) < 2 {
		return ""
	}

	coords := make([][]float64, len(points))
	for i, pt := range points {
		coords[i] = []float64{pt.Lon, pt.Lat}
	}

	geojson := map[string]interface{}{
		"type":        "LineString",
		"coordinates": coords,
	}

	bytes, _ := json.Marshal(geojson)
	return string(bytes)
}
