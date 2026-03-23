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

const testLocusGPX = `<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<gpx version="1.1" creator="Locus Map, Android"
 xmlns="http://www.topografix.com/GPX/1/1"
 xmlns:locus="http://www.locusmap.eu">
<trk>
<name>in to nyerere 2026-03-22 09:23</name>
	<extensions>
		<locus:activity>transport_airplane</locus:activity>
	</extensions>
<trkseg>
<trkpt lat="-6.7974583" lon="37.6524417">
	<ele>519.50</ele>
	<time>2026-03-22T06:23:35Z</time>
</trkpt>
<trkpt lat="-7.1057550" lon="37.2469417">
	<ele>728.93</ele>
	<time>2026-03-22T06:44:21Z</time>
</trkpt>
<trkpt lat="-8.2036683" lon="36.9564983">
	<ele>286.86</ele>
	<time>2026-03-22T08:01:40Z</time>
</trkpt>
</trkseg>
</trk>
</gpx>`

func TestParseGPXLocusActivity(t *testing.T) {
	reader := strings.NewReader(testLocusGPX)
	data, err := ParseGPX(reader)
	if err != nil {
		t.Fatalf("ParseGPX error: %v", err)
	}

	if len(data.Tracks) != 1 {
		t.Fatalf("expected 1 track, got %d", len(data.Tracks))
	}

	track := data.Tracks[0]
	if track.Activity != "transport_airplane" {
		t.Errorf("expected activity 'transport_airplane', got %q", track.Activity)
	}
	if track.Name != "in to nyerere 2026-03-22 09:23" {
		t.Errorf("expected track name, got %q", track.Name)
	}
}

func TestMergeTrackActivityHint(t *testing.T) {
	tests := []struct {
		activity string
		wantType string
	}{
		{"transport_airplane", "aircraft"},
		{"transport_car", "vehicle"},
		{"walk", "foot"},
		{"run", "foot"},
		{"bike", ""},    // bike not mapped
		{"", ""},         // empty stays empty
	}

	for _, tt := range tests {
		hint := mergeTrackActivityHint(MovementHint{}, tt.activity)
		if hint.Type != tt.wantType {
			t.Errorf("activity %q: want type %q, got %q", tt.activity, tt.wantType, hint.Type)
		}
		if tt.wantType != "" && hint.Confidence < 0.9 {
			t.Errorf("activity %q: want high confidence, got %.2f", tt.activity, hint.Confidence)
		}
	}
}

func TestLocusAircraftClassification(t *testing.T) {
	// Simulate: Locus says airplane, speed is ~113 km/h (normally classified as vehicle)
	reader := strings.NewReader(testLocusGPX)
	data, err := ParseGPX(reader)
	if err != nil {
		t.Fatalf("ParseGPX error: %v", err)
	}

	// Use 2-hour max duration to keep all 3 points in one segment
	segments := SplitIntoSegments(data, 2*time.Hour)
	if len(segments) == 0 {
		t.Fatal("expected at least 1 segment")
	}

	// With Locus hint, even the avg speed ~113 km/h segment should be classified as aircraft
	for _, seg := range segments {
		if seg.MovementType != "aircraft" {
			t.Errorf("expected aircraft classification, got %q (speed: %.1f km/h)", seg.MovementType, seg.AvgSpeedKmh)
		}
	}
}

// ── EarthRanger extension tests ──────────────────────────────────────────────

const testERGPX = `<?xml version='1.0' encoding='utf-8'?>
<gpx version="1.1" creator="5mp-autofetch"
 xmlns="http://www.topografix.com/GPX/1/1"
 xmlns:er="http://5mp.globe/earthranger/1">
  <trk>
    <name>subject-truck-1</name>
    <extensions>
      <er:subject_type>vehicle</er:subject_type>
      <er:subject_subtype>truck</er:subject_subtype>
    </extensions>
    <trkseg>
      <trkpt lat="-8.2" lon="36.9"><time>2026-03-22T06:00:00Z</time></trkpt>
      <trkpt lat="-8.2001" lon="36.9001"><time>2026-03-22T06:30:00Z</time></trkpt>
      <trkpt lat="-8.2002" lon="36.9002"><time>2026-03-22T07:00:00Z</time></trkpt>
    </trkseg>
  </trk>
  <trk>
    <name>subject-plane-1</name>
    <extensions>
      <er:subject_type>aircraft</er:subject_type>
      <er:subject_subtype>plane</er:subject_subtype>
    </extensions>
    <trkseg>
      <trkpt lat="-8.0" lon="36.0"><time>2026-03-22T08:00:00Z</time></trkpt>
      <trkpt lat="-8.5" lon="36.5"><time>2026-03-22T08:30:00Z</time></trkpt>
      <trkpt lat="-9.0" lon="37.0"><time>2026-03-22T09:00:00Z</time></trkpt>
    </trkseg>
  </trk>
  <trk>
    <name>subject-mobile-in-heli</name>
    <extensions>
      <er:subject_type>person</er:subject_type>
      <er:subject_subtype>er_mobile</er:subject_subtype>
      <er:patrol_type>heli_patrol_operations</er:patrol_type>
    </extensions>
    <trkseg>
      <trkpt lat="-7.0" lon="35.0"><time>2026-03-22T10:00:00Z</time></trkpt>
      <trkpt lat="-7.5" lon="35.5"><time>2026-03-22T10:30:00Z</time></trkpt>
      <trkpt lat="-8.0" lon="36.0"><time>2026-03-22T11:00:00Z</time></trkpt>
    </trkseg>
  </trk>
  <trk>
    <name>subject-ranger</name>
    <extensions>
      <er:subject_type>person</er:subject_type>
      <er:subject_subtype>ranger</er:subject_subtype>
    </extensions>
    <trkseg>
      <trkpt lat="-8.3" lon="36.8"><time>2026-03-22T12:00:00Z</time></trkpt>
      <trkpt lat="-8.3001" lon="36.8001"><time>2026-03-22T12:30:00Z</time></trkpt>
      <trkpt lat="-8.3002" lon="36.8002"><time>2026-03-22T13:00:00Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>`

func TestParseERExtensions(t *testing.T) {
	data, err := ParseGPX(strings.NewReader(testERGPX))
	if err != nil {
		t.Fatalf("ParseGPX error: %v", err)
	}

	if len(data.Tracks) != 4 {
		t.Fatalf("expected 4 tracks, got %d", len(data.Tracks))
	}

	// Track 0: vehicle/truck
	trk := data.Tracks[0]
	if trk.ERSubjectType != "vehicle" {
		t.Errorf("track 0: want subject_type=vehicle, got %q", trk.ERSubjectType)
	}
	if trk.ERSubjectSubtype != "truck" {
		t.Errorf("track 0: want subject_subtype=truck, got %q", trk.ERSubjectSubtype)
	}

	// Track 1: aircraft/plane
	trk = data.Tracks[1]
	if trk.ERSubjectType != "aircraft" {
		t.Errorf("track 1: want subject_type=aircraft, got %q", trk.ERSubjectType)
	}

	// Track 2: person/er_mobile with heli patrol
	trk = data.Tracks[2]
	if trk.ERSubjectType != "person" {
		t.Errorf("track 2: want subject_type=person, got %q", trk.ERSubjectType)
	}
	if trk.ERPatrolType != "heli_patrol_operations" {
		t.Errorf("track 2: want patrol_type=heli_patrol_operations, got %q", trk.ERPatrolType)
	}

	// Track 3: person/ranger
	trk = data.Tracks[3]
	if trk.ERSubjectSubtype != "ranger" {
		t.Errorf("track 3: want subject_subtype=ranger, got %q", trk.ERSubjectSubtype)
	}
}

func TestERTruckClassifiedAsVehicleEvenWhenParked(t *testing.T) {
	// The truck track has very slow speed (nearly stationary) but the ER
	// metadata says vehicle/truck — it should be classified as vehicle, not foot.
	data, err := ParseGPX(strings.NewReader(testERGPX))
	if err != nil {
		t.Fatalf("ParseGPX error: %v", err)
	}

	segments := SplitIntoSegments(data, 2*time.Hour)

	var truckSeg *Segment
	for i, seg := range segments {
		// Find the truck segment (very low speed, ~0.01 km/h)
		if seg.AvgSpeedKmh < 1 && seg.Hint.Type == "vehicle" {
			truckSeg = &segments[i]
			break
		}
	}

	if truckSeg == nil {
		t.Fatal("could not find truck segment")
	}

	// Key test: even at ~0 km/h, should be classified as vehicle (not foot)
	if truckSeg.MovementType != "vehicle" {
		t.Errorf("parked truck: want vehicle, got %q (speed=%.2f km/h, hint=%+v)",
			truckSeg.MovementType, truckSeg.AvgSpeedKmh, truckSeg.Hint)
	}
}

func TestERMobileInHeliClassifiedAsAircraft(t *testing.T) {
	// A person/er_mobile leading a heli_patrol should be classified as aircraft
	data, err := ParseGPX(strings.NewReader(testERGPX))
	if err != nil {
		t.Fatalf("ParseGPX error: %v", err)
	}

	segments := SplitIntoSegments(data, 2*time.Hour)

	var heliSeg *Segment
	for i, seg := range segments {
		if seg.Hint.Type == "aircraft" && seg.Hint.Confidence >= 1.0 {
			heliSeg = &segments[i]
			break
		}
	}

	if heliSeg == nil {
		t.Fatal("could not find helicopter segment")
	}

	if heliSeg.MovementType != "aircraft" {
		t.Errorf("mobile in heli: want aircraft, got %q", heliSeg.MovementType)
	}
}

func TestERRangerClassifiedAsFoot(t *testing.T) {
	data, err := ParseGPX(strings.NewReader(testERGPX))
	if err != nil {
		t.Fatalf("ParseGPX error: %v", err)
	}

	segments := SplitIntoSegments(data, 2*time.Hour)

	var rangerSeg *Segment
	for i, seg := range segments {
		if seg.Hint.Confidence == 0.9 && seg.Hint.Type == "foot" {
			rangerSeg = &segments[i]
			break
		}
	}

	if rangerSeg == nil {
		t.Fatal("could not find ranger segment")
	}

	if rangerSeg.MovementType != "foot" {
		t.Errorf("ranger: want foot, got %q", rangerSeg.MovementType)
	}
}

func TestMergeERSubjectHint(t *testing.T) {
	tests := []struct {
		name           string
		subjectType    string
		subjectSubtype string
		patrolType     string
		wantType       string
		wantMinConf    float64
	}{
		{"vehicle/truck", "vehicle", "truck", "", "vehicle", 1.0},
		{"aircraft/plane", "aircraft", "plane", "", "aircraft", 1.0},
		{"aircraft/helicopter", "aircraft", "helicopter", "", "aircraft", 1.0},
		{"person/ranger", "person", "ranger", "", "foot", 0.9},
		{"person/er_mobile", "person", "er_mobile", "", "foot", 0.5},
		{"person + heli_patrol", "person", "er_mobile", "heli_patrol_operations", "aircraft", 1.0},
		{"person + vehicle_patrol", "person", "er_mobile", "vehicle_patrol", "vehicle", 1.0},
		{"person + plane_patrol", "person", "ranger", "plane_patrol_operations", "aircraft", 1.0},
		{"empty", "", "", "", "", 0},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			hint := mergeERSubjectHint(MovementHint{}, tt.subjectType, tt.subjectSubtype, tt.patrolType)
			if hint.Type != tt.wantType {
				t.Errorf("want type %q, got %q", tt.wantType, hint.Type)
			}
			if hint.Confidence < tt.wantMinConf {
				t.Errorf("want confidence >= %.1f, got %.2f", tt.wantMinConf, hint.Confidence)
			}
		})
	}
}
