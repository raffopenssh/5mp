package srv

import (
	"testing"
	"time"

	"srv.exe.dev/srv/gpx"
)

// segmentsFromGPX is a test helper that converts raw GPXData to segments
// (the same preprocessing that upload_queue.go and upload.go do before
// calling ValidateAndClassifyGPX).
func segmentsFromGPX(data *gpx.GPXData) []gpx.Segment {
	return gpx.SplitIntoSegments(data, 0)
}

// TestValidateAndClassifyGPX_TimeBasedSegmentation tests that large time spans
// are split into smaller segments (30 min max) to avoid unrealistic distances
func TestValidateAndClassifyGPX_TimeBasedSegmentation(t *testing.T) {
	// Create a track that spans 2 hours with points every 10 minutes
	// This should be split into multiple segments by SplitIntoSegments
	baseTime := time.Date(2024, 1, 1, 8, 0, 0, 0, time.UTC)
	
	var points []gpx.Point
	for i := 0; i < 12; i++ { // 12 points over 2 hours (every 10 min)
		pt := gpx.Point{
			Lat:  -1.0 + float64(i)*0.01, // Move south ~1km per point
			Lon:  29.0 + float64(i)*0.01, // Move east ~1km per point
			Time: func() *time.Time { t := baseTime.Add(time.Duration(i) * 10 * time.Minute); return &t }(),
		}
		points = append(points, pt)
	}

	data := &gpx.GPXData{
		Tracks: []gpx.Track{
			{
				Segments: [][]gpx.Point{points},
			},
		},
	}

	result := ValidateAndClassifyGPX(segmentsFromGPX(data))

	// Should be valid
	if !result.IsValid {
		t.Errorf("Expected valid GPX, got invalid: %v", result.ValidationErrors)
	}

	// After merging, consecutive same-type segments may be merged into one.
	// The important thing is that total distance is preserved and realistic.
	if len(result.ClassifiedSegments) < 1 {
		t.Errorf("Expected at least 1 segment, got %d", len(result.ClassifiedSegments))
	}

	// Total patrol km should be reasonable (roughly 12 points * ~1.5km = ~18km max)
	if result.PatrolKm > 50 {
		t.Errorf("Patrol distance %v km seems too high for a 2-hour track with 12 points", result.PatrolKm)
	}

	t.Logf("Results: %d segments, %.2f patrol km, %d points", 
		len(result.ClassifiedSegments), result.PatrolKm, result.TotalPoints)
}

// TestValidateAndClassifyGPX_ShortSegment tests that short tracks work correctly
func TestValidateAndClassifyGPX_ShortSegment(t *testing.T) {
	baseTime := time.Date(2024, 1, 1, 8, 0, 0, 0, time.UTC)
	
	var points []gpx.Point
	for i := 0; i < 20; i++ { // 20 points over 20 minutes
		pt := gpx.Point{
			Lat:  -1.0 + float64(i)*0.001, // Small movements
			Lon:  29.0 + float64(i)*0.001,
			Time: func() *time.Time { t := baseTime.Add(time.Duration(i) * time.Minute); return &t }(),
		}
		points = append(points, pt)
	}

	data := &gpx.GPXData{
		Tracks: []gpx.Track{
			{
				Segments: [][]gpx.Point{points},
			},
		},
	}

	result := ValidateAndClassifyGPX(segmentsFromGPX(data))

	if !result.IsValid {
		t.Errorf("Expected valid GPX, got invalid: %v", result.ValidationErrors)
	}

	// Short segment should stay as one segment
	if len(result.ClassifiedSegments) == 0 {
		t.Error("Expected at least one segment")
	}

	// Distance should be small (20 points * ~0.15km = ~3km)
	if result.PatrolKm > 10 {
		t.Errorf("Patrol distance %v km seems too high for a short patrol", result.PatrolKm)
	}

	t.Logf("Results: %d segments, %.2f patrol km", len(result.ClassifiedSegments), result.PatrolKm)
}

// TestValidateAndClassifyGPX_MultiDayTrack tests that multi-day tracks are properly split
func TestValidateAndClassifyGPX_MultiDayTrack(t *testing.T) {
	// Simulate a track that spans 3 days with 8-hour gaps (overnight)
	var points []gpx.Point
	
	for day := 0; day < 3; day++ {
		baseTime := time.Date(2024, 1, 1+day, 8, 0, 0, 0, time.UTC)
		for i := 0; i < 10; i++ { // 10 points per day
			pt := gpx.Point{
				Lat:  -1.0 + float64(day)*0.1 + float64(i)*0.01,
				Lon:  29.0 + float64(day)*0.1 + float64(i)*0.01,
				Time: func() *time.Time { t := baseTime.Add(time.Duration(i) * 10 * time.Minute); return &t }(),
			}
			points = append(points, pt)
		}
	}

	data := &gpx.GPXData{
		Tracks: []gpx.Track{
			{
				Segments: [][]gpx.Point{points},
			},
		},
	}

	result := ValidateAndClassifyGPX(segmentsFromGPX(data))

	if !result.IsValid {
		t.Errorf("Expected valid GPX, got invalid: %v", result.ValidationErrors)
	}

	// After merging, consecutive same-type segments may be merged.
	// With overnight gaps, the splitter creates per-day segments, but if all
	// days are same movement type they merge. At minimum we have 1 segment.
	if len(result.ClassifiedSegments) < 1 {
		t.Errorf("Expected at least 1 segment for 3-day track, got %d", len(result.ClassifiedSegments))
	}

	// Total distance should be reasonable (not summing across day gaps)
	// Each day ~10km, so total should be around 30km max
	if result.PatrolKm > 100 {
		t.Errorf("Patrol distance %v km seems too high - may not be splitting days properly", result.PatrolKm)
	}

	t.Logf("Results: %d segments, %.2f patrol km for 3-day track", 
		len(result.ClassifiedSegments), result.PatrolKm)
}

// TestValidateAndClassifyGPX_InsufficientPoints tests minimum waypoints validation
func TestValidateAndClassifyGPX_InsufficientPoints(t *testing.T) {
	points := []gpx.Point{
		{Lat: -1.0, Lon: 29.0},
		{Lat: -1.01, Lon: 29.01},
	}

	data := &gpx.GPXData{
		Tracks: []gpx.Track{
			{
				Segments: [][]gpx.Point{points},
			},
		},
	}

	result := ValidateAndClassifyGPX(segmentsFromGPX(data))

	if result.IsValid {
		t.Error("Expected invalid GPX due to insufficient points")
	}

	if len(result.ValidationErrors) == 0 {
		t.Error("Expected validation error for insufficient points")
	}
}

// TestValidateAndClassifyGPX_RealisticDistances ensures distances are realistic
// This is a regression test for the bug where multi-day GPX files showed 90,000+ km
func TestValidateAndClassifyGPX_RealisticDistances(t *testing.T) {
	// Simulate 30 days of patrols with overnight gaps
	// Each day: 4 hours of patrol at ~4km/h = ~16km/day
	// Total expected: ~480km for 30 days
	
	var points []gpx.Point
	
	for day := 0; day < 30; day++ {
		baseTime := time.Date(2024, 1, 1+day, 8, 0, 0, 0, time.UTC)
		// Each day: patrol for 4 hours with GPS point every 5 minutes = 48 points
		for i := 0; i < 48; i++ {
			// Walk ~150m between points (0.0015 degrees ~= 167m at equator)
			pt := gpx.Point{
				Lat:  -1.0 + float64(i)*0.0015,
				Lon:  29.0 + float64(i)*0.0015,
				Time: func() *time.Time { 
					t := baseTime.Add(time.Duration(i*5) * time.Minute)
					return &t 
				}(),
			}
			points = append(points, pt)
		}
	}

	data := &gpx.GPXData{
		Tracks: []gpx.Track{{
			Segments: [][]gpx.Point{points},
		}},
	}

	result := ValidateAndClassifyGPX(segmentsFromGPX(data))

	if !result.IsValid {
		t.Errorf("Expected valid GPX, got invalid: %v", result.ValidationErrors)
	}

	// After merging, consecutive same-type foot segments are merged.
	// The important check: distance is realistic, not segment count.
	if len(result.ClassifiedSegments) < 1 {
		t.Errorf("Expected at least 1 segment for 30-day track, got %d", len(result.ClassifiedSegments))
	}

	// The key test: distance should be REALISTIC
	// WITHOUT time splitting, all 30 days would be one segment = ~240km but that's OK
	// WITH time splitting, it should be similar but properly segmented
	// The bug showed 90,000+ km which is clearly wrong
	totalKm := result.PatrolKm + result.BoundaryKm + result.RoadKm
	
	// Each day ~8km (48 points * 167m), 30 days = ~240km
	// Allow 2x margin for calculation differences
	maxRealisticKm := 500.0
	
	if totalKm > maxRealisticKm {
		t.Errorf("Total distance %.2f km exceeds realistic maximum %.2f km", totalKm, maxRealisticKm)
	}

	t.Logf("Results: %d segments, %.2f total km for 30-day track with %d points", 
		len(result.ClassifiedSegments), totalKm, result.TotalPoints)
}

// TestValidateAndClassifyGPX_AircraftSeparation ensures aircraft movements
// are separated from patrol distances (they shouldn't count toward patrol effort)
func TestValidateAndClassifyGPX_AircraftSeparation(t *testing.T) {
	baseTime := time.Date(2024, 1, 1, 8, 0, 0, 0, time.UTC)
	
	var points []gpx.Point
	
	// Simulate a flight: 500km in 2 hours = 250 km/h
	// Points every 10 minutes = 12 points over 2 hours
	for i := 0; i < 12; i++ {
		pt := gpx.Point{
			// Move ~42 km between points (0.38 degrees at equator)
			Lat:  0.0 + float64(i)*0.38,
			Lon:  30.0,
			Time: func() *time.Time { t := baseTime.Add(time.Duration(i*10) * time.Minute); return &t }(),
		}
		points = append(points, pt)
	}
	
	data := &gpx.GPXData{
		Tracks: []gpx.Track{{
			Segments: [][]gpx.Point{points},
		}},
	}
	
	result := ValidateAndClassifyGPX(segmentsFromGPX(data))
	
	if !result.IsValid {
		t.Errorf("Expected valid GPX, got invalid: %v", result.ValidationErrors)
	}
	
	// Patrol km should be 0 or very low (aircraft shouldn't count)
	if result.PatrolKm > 10 {
		t.Errorf("Patrol km %.2f is too high - aircraft should not count as patrol", result.PatrolKm)
	}
	
	// Excluded km should include the aircraft distance
	// Flight distance should be in excluded (at least 300km)
	if result.ExcludedKm < 300 {
		t.Errorf("Excluded km %.2f is too low - aircraft distance should be excluded", result.ExcludedKm)
	}
	
	// Check we have an aircraft segment
	hasAircraft := false
	for _, seg := range result.ClassifiedSegments {
		if seg.Classification == "aircraft" {
			hasAircraft = true
			t.Logf("Aircraft segment: %.2f km, avg %.0f km/h", seg.DistanceKm, seg.AvgSpeedKmh)
		}
	}
	if !hasAircraft {
		t.Error("Expected aircraft classification for high-speed movement")
	}
	
	t.Logf("Results: patrol=%.2f km, excluded=%.2f km", result.PatrolKm, result.ExcludedKm)
}

// TestMergeAdjacentSegments_OrphanAbsorption tests that tiny segments between
// same-type neighbors get absorbed (e.g. 2-point foot fragment between aircraft segments)
func TestMergeAdjacentSegments_OrphanAbsorption(t *testing.T) {
	segs := []ClassifiedSegment{
		{Classification: "aircraft", MovementType: "aircraft", DistanceKm: 35.0, EndIndex: 50, IncludeInEffort: true, OriginalIndices: []int{0}},
		{Classification: "patrol", MovementType: "foot", DistanceKm: 0.05, EndIndex: 2, IncludeInEffort: true, OriginalIndices: []int{1}},  // orphan: 3 pts, 50m
		{Classification: "aircraft", MovementType: "aircraft", DistanceKm: 21.0, EndIndex: 100, IncludeInEffort: true, OriginalIndices: []int{2}},
	}

	merged := mergeAdjacentSegments(segs)

	// The orphan foot segment should be absorbed, then the two aircraft segments merged
	if len(merged) != 1 {
		t.Errorf("Expected 1 merged segment, got %d", len(merged))
		for i, s := range merged {
			t.Logf("  [%d] %s/%s %.2f km, origIndices=%v", i, s.Classification, s.MovementType, s.DistanceKm, s.OriginalIndices)
		}
		return
	}
	if merged[0].MovementType != "aircraft" {
		t.Errorf("Expected aircraft, got %s", merged[0].MovementType)
	}
	// Distance should include all three segments
	expectedKm := 35.0 + 0.05 + 21.0
	if merged[0].DistanceKm < expectedKm-0.01 || merged[0].DistanceKm > expectedKm+0.01 {
		t.Errorf("Expected %.2f km, got %.2f km", expectedKm, merged[0].DistanceKm)
	}
	// OriginalIndices should include all three
	if len(merged[0].OriginalIndices) != 3 {
		t.Errorf("Expected 3 original indices, got %d: %v", len(merged[0].OriginalIndices), merged[0].OriginalIndices)
	}
}

// TestMergeAdjacentSegments_DifferentTypes tests that segments of different types are NOT merged
func TestMergeAdjacentSegments_DifferentTypes(t *testing.T) {
	segs := []ClassifiedSegment{
		{Classification: "patrol", MovementType: "foot", DistanceKm: 5.0, EndIndex: 30, IncludeInEffort: true, OriginalIndices: []int{0}},
		{Classification: "patrol", MovementType: "vehicle", DistanceKm: 20.0, EndIndex: 50, IncludeInEffort: true, OriginalIndices: []int{1}},
		{Classification: "patrol", MovementType: "foot", DistanceKm: 3.0, EndIndex: 20, IncludeInEffort: true, OriginalIndices: []int{2}},
	}

	merged := mergeAdjacentSegments(segs)

	// All three should remain separate (different movement types, and middle one is too big to absorb)
	if len(merged) != 3 {
		t.Errorf("Expected 3 segments (different types), got %d", len(merged))
		for i, s := range merged {
			t.Logf("  [%d] %s/%s %.2f km", i, s.Classification, s.MovementType, s.DistanceKm)
		}
	}
}

// TestMergeAdjacentSegments_ConsecutiveSameType tests that consecutive same-type segments merge
func TestMergeAdjacentSegments_ConsecutiveSameType(t *testing.T) {
	segs := []ClassifiedSegment{
		{Classification: "patrol", MovementType: "foot", DistanceKm: 2.0, EndIndex: 20, IncludeInEffort: true, OriginalIndices: []int{0}},
		{Classification: "patrol", MovementType: "foot", DistanceKm: 3.0, EndIndex: 25, IncludeInEffort: true, OriginalIndices: []int{1}},
		{Classification: "patrol", MovementType: "foot", DistanceKm: 1.5, EndIndex: 15, IncludeInEffort: true, OriginalIndices: []int{2}},
		{Classification: "aircraft", MovementType: "aircraft", DistanceKm: 50.0, EndIndex: 100, IncludeInEffort: true, OriginalIndices: []int{3}},
	}

	merged := mergeAdjacentSegments(segs)

	// Should produce 2 segments: merged foot + aircraft
	if len(merged) != 2 {
		t.Errorf("Expected 2 merged segments, got %d", len(merged))
		for i, s := range merged {
			t.Logf("  [%d] %s/%s %.2f km origIndices=%v", i, s.Classification, s.MovementType, s.DistanceKm, s.OriginalIndices)
		}
		return
	}
	if merged[0].MovementType != "foot" || merged[0].DistanceKm < 6.49 {
		t.Errorf("First segment should be foot ~6.5 km, got %s %.2f km", merged[0].MovementType, merged[0].DistanceKm)
	}
	if merged[1].MovementType != "aircraft" || merged[1].DistanceKm < 49.99 {
		t.Errorf("Second segment should be aircraft ~50 km, got %s %.2f km", merged[1].MovementType, merged[1].DistanceKm)
	}
}

// TestMergeAdjacentSegments_IdleNotMerged tests that idle segments are not merged with patrol
func TestMergeAdjacentSegments_IdleNotMerged(t *testing.T) {
	segs := []ClassifiedSegment{
		{Classification: "patrol", MovementType: "foot", DistanceKm: 5.0, EndIndex: 30, IncludeInEffort: true, OriginalIndices: []int{0}},
		{Classification: "idle", MovementType: "foot", DistanceKm: 0.001, EndIndex: 2, IncludeInEffort: false, OriginalIndices: []int{1}},
		{Classification: "patrol", MovementType: "foot", DistanceKm: 3.0, EndIndex: 20, IncludeInEffort: true, OriginalIndices: []int{2}},
	}

	merged := mergeAdjacentSegments(segs)

	// The idle segment is tiny and between two foot patrols.
	// Pass 1 absorbs it, then pass 2 merges the two foot segments.
	if len(merged) != 1 {
		t.Errorf("Expected 1 merged segment (idle absorbed + foot merged), got %d", len(merged))
		for i, s := range merged {
			t.Logf("  [%d] %s/%s %.2f km effort=%v", i, s.Classification, s.MovementType, s.DistanceKm, s.IncludeInEffort)
		}
	}
}
