package gpx

import (
	"math"
	"strings"
	"testing"
	"time"
)

const testGPX = `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <metadata>
    <name>Test Patrol Route</name>
  </metadata>
  <trk>
    <name>Morning Patrol</name>
    <trkseg>
      <trkpt lat="-1.2921" lon="36.8219">
        <ele>1795</ele>
        <time>2024-01-15T08:00:00Z</time>
      </trkpt>
      <trkpt lat="-1.2931" lon="36.8229">
        <ele>1800</ele>
        <time>2024-01-15T08:10:00Z</time>
      </trkpt>
      <trkpt lat="-1.2941" lon="36.8239">
        <ele>1805</ele>
        <time>2024-01-15T08:20:00Z</time>
      </trkpt>
    </trkseg>
  </trk>
</gpx>`

func TestParseGPX(t *testing.T) {
	reader := strings.NewReader(testGPX)
	data, err := ParseGPX(reader)
	if err != nil {
		t.Fatalf("ParseGPX failed: %v", err)
	}

	if data.Name != "Test Patrol Route" {
		t.Errorf("expected name 'Test Patrol Route', got '%s'", data.Name)
	}

	if len(data.Tracks) != 1 {
		t.Fatalf("expected 1 track, got %d", len(data.Tracks))
	}

	track := data.Tracks[0]
	if track.Name != "Morning Patrol" {
		t.Errorf("expected track name 'Morning Patrol', got '%s'", track.Name)
	}

	if len(track.Segments) != 1 {
		t.Fatalf("expected 1 segment, got %d", len(track.Segments))
	}

	points := track.Segments[0]
	if len(points) != 3 {
		t.Fatalf("expected 3 points, got %d", len(points))
	}

	// Check first point
	p := points[0]
	if p.Lat != -1.2921 {
		t.Errorf("expected lat -1.2921, got %f", p.Lat)
	}
	if p.Lon != 36.8219 {
		t.Errorf("expected lon 36.8219, got %f", p.Lon)
	}
	if p.Elevation == nil || *p.Elevation != 1795 {
		t.Errorf("expected elevation 1795, got %v", p.Elevation)
	}
	if p.Time == nil {
		t.Errorf("expected time to be set")
	}
}

func TestParseGPXInvalid(t *testing.T) {
	reader := strings.NewReader("not valid xml")
	_, err := ParseGPX(reader)
	if err == nil {
		t.Error("expected error for invalid XML")
	}
}

func TestCalculateDistance(t *testing.T) {
	// Test with known distance: roughly 0.157 km between these points
	points := []Point{
		{Lat: -1.2921, Lon: 36.8219},
		{Lat: -1.2931, Lon: 36.8229},
	}

	dist := CalculateDistance(points)

	// Expected ~0.157 km (roughly 157 meters diagonal)
	if dist < 0.1 || dist > 0.2 {
		t.Errorf("expected distance ~0.157 km, got %f km", dist)
	}
}

func TestCalculateDistanceEmpty(t *testing.T) {
	dist := CalculateDistance([]Point{})
	if dist != 0 {
		t.Errorf("expected 0 for empty points, got %f", dist)
	}

	dist = CalculateDistance([]Point{{Lat: 0, Lon: 0}})
	if dist != 0 {
		t.Errorf("expected 0 for single point, got %f", dist)
	}
}

func TestCalculateDistanceKnownValues(t *testing.T) {
	// London to Paris is approximately 344 km
	points := []Point{
		{Lat: 51.5074, Lon: -0.1278}, // London
		{Lat: 48.8566, Lon: 2.3522},  // Paris
	}

	dist := CalculateDistance(points)

	// Allow 5% tolerance
	if math.Abs(dist-344) > 20 {
		t.Errorf("expected ~344 km London-Paris, got %f km", dist)
	}
}

func TestCalculateSpeed(t *testing.T) {
	t1 := time.Date(2024, 1, 15, 8, 0, 0, 0, time.UTC)
	t2 := time.Date(2024, 1, 15, 9, 0, 0, 0, time.UTC) // 1 hour later

	// London to Paris (~344 km) in 1 hour = ~344 km/h
	points := []Point{
		{Lat: 51.5074, Lon: -0.1278, Time: &t1}, // London
		{Lat: 48.8566, Lon: 2.3522, Time: &t2},  // Paris
	}

	speed := CalculateSpeed(points)

	// Allow 5% tolerance
	if math.Abs(speed-344) > 20 {
		t.Errorf("expected ~344 km/h, got %f km/h", speed)
	}
}

func TestCalculateSpeedNoTime(t *testing.T) {
	points := []Point{
		{Lat: 51.5074, Lon: -0.1278},
		{Lat: 48.8566, Lon: 2.3522},
	}

	speed := CalculateSpeed(points)
	if speed != 0 {
		t.Errorf("expected 0 for points without time, got %f", speed)
	}
}

func TestClassifyMovementType(t *testing.T) {
	tests := []struct {
		speed    float64
		expected string
	}{
		{0, "foot"},
		{5, "foot"},
		{7.9, "foot"},
		{8, "vehicle"},
		{50, "vehicle"},
		{120, "vehicle"},
		{121, "aircraft"},
		{500, "aircraft"},
	}

	for _, tc := range tests {
		seg := Segment{AvgSpeedKmh: tc.speed}
		result := ClassifyMovementType(seg)
		if result != tc.expected {
			t.Errorf("speed %f: expected '%s', got '%s'", tc.speed, tc.expected, result)
		}
	}
}

func TestSplitIntoSegments(t *testing.T) {
	reader := strings.NewReader(testGPX)
	data, err := ParseGPX(reader)
	if err != nil {
		t.Fatalf("ParseGPX failed: %v", err)
	}

	// With 30 min default, all 3 points (spanning 20 min) should be in one segment
	segments := SplitIntoSegments(data, 0)

	if len(segments) != 1 {
		t.Fatalf("expected 1 segment with default duration, got %d", len(segments))
	}

	seg := segments[0]
	if len(seg.Points) != 3 {
		t.Errorf("expected 3 points in segment, got %d", len(seg.Points))
	}

	if seg.StartTime == nil {
		t.Error("expected StartTime to be set")
	}
	if seg.EndTime == nil {
		t.Error("expected EndTime to be set")
	}

	if seg.DistanceKm <= 0 {
		t.Error("expected positive distance")
	}

	if seg.MovementType == "" {
		t.Error("expected MovementType to be set")
	}
}

func TestSplitIntoSegmentsShortDuration(t *testing.T) {
	reader := strings.NewReader(testGPX)
	data, err := ParseGPX(reader)
	if err != nil {
		t.Fatalf("ParseGPX failed: %v", err)
	}

	// With 10 min segments, points at 0, 10, 20 min:
	// - Segment 1: points at 0, 10 min (10 min duration is inclusive)
	// - Segment 2: point at 20 min (>10 min from start triggers new segment)
	segments := SplitIntoSegments(data, 10*time.Minute)

	if len(segments) != 2 {
		t.Errorf("expected 2 segments with 10min duration, got %d", len(segments))
	}

	// With 9 min segments, we get 3 segments (each point triggers a split)
	reader = strings.NewReader(testGPX)
	data, _ = ParseGPX(reader)
	segments = SplitIntoSegments(data, 9*time.Minute)

	if len(segments) != 3 {
		t.Errorf("expected 3 segments with 9min duration, got %d", len(segments))
	}
}

func TestSplitIntoSegmentsEmpty(t *testing.T) {
	data := &GPXData{}
	segments := SplitIntoSegments(data, 30*time.Minute)

	if len(segments) != 0 {
		t.Errorf("expected 0 segments for empty data, got %d", len(segments))
	}
}

func TestHaversineDistance(t *testing.T) {
	// Same point should have 0 distance
	p := Point{Lat: 0, Lon: 0}
	dist := haversineDistance(p, p)
	if dist != 0 {
		t.Errorf("expected 0 for same point, got %f", dist)
	}

	// Equator distance: 1 degree longitude at equator ≈ 111.32 km
	p1 := Point{Lat: 0, Lon: 0}
	p2 := Point{Lat: 0, Lon: 1}
	dist = haversineDistance(p1, p2)
	if math.Abs(dist-111.32) > 1 {
		t.Errorf("expected ~111.32 km for 1 degree at equator, got %f km", dist)
	}
}

// Test with a larger GPX file structure
func TestParseGPXMultipleTracks(t *testing.T) {
	gpxData := `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <metadata>
    <name>Multi-Track Test</name>
  </metadata>
  <trk>
    <name>Track 1</name>
    <trkseg>
      <trkpt lat="0" lon="0"><time>2024-01-15T08:00:00Z</time></trkpt>
      <trkpt lat="0.001" lon="0.001"><time>2024-01-15T08:05:00Z</time></trkpt>
    </trkseg>
  </trk>
  <trk>
    <name>Track 2</name>
    <trkseg>
      <trkpt lat="1" lon="1"><time>2024-01-15T09:00:00Z</time></trkpt>
      <trkpt lat="1.001" lon="1.001"><time>2024-01-15T09:05:00Z</time></trkpt>
    </trkseg>
    <trkseg>
      <trkpt lat="2" lon="2"><time>2024-01-15T10:00:00Z</time></trkpt>
      <trkpt lat="2.001" lon="2.001"><time>2024-01-15T10:05:00Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>`

	reader := strings.NewReader(gpxData)
	data, err := ParseGPX(reader)
	if err != nil {
		t.Fatalf("ParseGPX failed: %v", err)
	}

	if len(data.Tracks) != 2 {
		t.Errorf("expected 2 tracks, got %d", len(data.Tracks))
	}

	if len(data.Tracks[1].Segments) != 2 {
		t.Errorf("expected 2 segments in track 2, got %d", len(data.Tracks[1].Segments))
	}

	// Should produce 3 segments total when split
	segments := SplitIntoSegments(data, 30*time.Minute)
	if len(segments) != 3 {
		t.Errorf("expected 3 segments, got %d", len(segments))
	}
}

// Benchmark for large file simulation
func BenchmarkCalculateDistance(b *testing.B) {
	// Create 1000 points
	points := make([]Point, 1000)
	for i := range points {
		points[i] = Point{
			Lat: float64(i) * 0.001,
			Lon: float64(i) * 0.001,
		}
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		CalculateDistance(points)
	}
}

func BenchmarkParseGPX(b *testing.B) {
	for i := 0; i < b.N; i++ {
		reader := strings.NewReader(testGPX)
		_, _ = ParseGPX(reader)
	}
}
