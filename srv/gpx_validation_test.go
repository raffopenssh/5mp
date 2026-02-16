package srv

import (
	"testing"
	"time"

	"srv.exe.dev/srv/gpx"
)

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

	result := ValidateAndClassifyGPX(data)

	// Should be valid
	if !result.IsValid {
		t.Errorf("Expected valid GPX, got invalid: %v", result.ValidationErrors)
	}

	// Should have multiple segments (2 hours / 30 min max = at least 4 segments)
	if len(result.ClassifiedSegments) < 2 {
		t.Errorf("Expected multiple segments due to time-based splitting, got %d", len(result.ClassifiedSegments))
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

	result := ValidateAndClassifyGPX(data)

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

	result := ValidateAndClassifyGPX(data)

	if !result.IsValid {
		t.Errorf("Expected valid GPX, got invalid: %v", result.ValidationErrors)
	}

	// Should have at least 3 segments (one per day due to time gaps)
	if len(result.ClassifiedSegments) < 3 {
		t.Errorf("Expected at least 3 segments for 3-day track, got %d", len(result.ClassifiedSegments))
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

	result := ValidateAndClassifyGPX(data)

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

	result := ValidateAndClassifyGPX(data)

	if !result.IsValid {
		t.Errorf("Expected valid GPX, got invalid: %v", result.ValidationErrors)
	}

	// Should have ~30 segments (one per day after overnight gaps split them)
	if len(result.ClassifiedSegments) < 20 {
		t.Errorf("Expected ~30 segments for 30-day track, got %d", len(result.ClassifiedSegments))
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
