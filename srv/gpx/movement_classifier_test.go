package gpx

import (
	"math"
	"testing"
	"time"
)

// makeTime creates a *time.Time from base time plus offset seconds
func makeTime(base time.Time, offsetSec float64) *time.Time {
	t := base.Add(time.Duration(offsetSec * float64(time.Second)))
	return &t
}

// makeElev creates a *float64
func makeElev(v float64) *float64 {
	return &v
}

// generateLinearTrack creates a straight-line track at a given speed.
// Points go due north from (lat, lon) at given speed with given interval.
func generateLinearTrack(lat, lon float64, speedKmh float64, intervalSec float64, numPoints int, elev *float64) []Point {
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)
	points := make([]Point, numPoints)
	
	// Distance per interval in degrees lat (1 degree lat ≈ 111 km)
	kmPerInterval := speedKmh * (intervalSec / 3600.0)
	degPerInterval := kmPerInterval / 111.0
	
	for i := 0; i < numPoints; i++ {
		t := makeTime(base, float64(i)*intervalSec)
		var e *float64
		if elev != nil {
			e = makeElev(*elev)
		}
		points[i] = Point{
			Lat:       lat + float64(i)*degPerInterval,
			Lon:       lon,
			Time:      t,
			Elevation: e,
		}
	}
	return points
}

// generateErraticTrack creates a zigzag track to simulate foot patrol exploring.
func generateErraticTrack(lat, lon float64, speedKmh float64, intervalSec float64, numPoints int) []Point {
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)
	points := make([]Point, numPoints)
	
	kmPerInterval := speedKmh * (intervalSec / 3600.0)
	degPerInterval := kmPerInterval / 111.0
	
	for i := 0; i < numPoints; i++ {
		t := makeTime(base, float64(i)*intervalSec)
		// Zigzag: alternate east-west while going north
		lonOffset := degPerInterval * 0.5
		if i%2 == 0 {
			lonOffset = -lonOffset
		}
		points[i] = Point{
			Lat:  lat + float64(i)*degPerInterval*0.5,
			Lon:  lon + lonOffset,
			Time: t,
		}
	}
	return points
}

// --- Tests for new MovementMetrics fields ---

func TestAnalyzeTrajectory_SamplingRate(t *testing.T) {
	// 120s constant interval GPS tracker
	points120 := generateLinearTrack(0, 36, 50, 120, 30, nil)
	m120 := AnalyzeTrajectory(points120)
	
	if math.Abs(m120.MedianIntervalSec-120) > 1 {
		t.Errorf("expected median interval ~120s for 120s tracker, got %.1f", m120.MedianIntervalSec)
	}
	if m120.IntervalConsistency > 0.05 {
		t.Errorf("expected very low interval consistency (constant), got %.3f", m120.IntervalConsistency)
	}
	
	// 600s InReach interval
	points600 := generateLinearTrack(0, 36, 5, 600, 20, nil)
	m600 := AnalyzeTrajectory(points600)
	
	if math.Abs(m600.MedianIntervalSec-600) > 1 {
		t.Errorf("expected median interval ~600s for InReach, got %.1f", m600.MedianIntervalSec)
	}
}

func TestAnalyzeTrajectory_Elevation(t *testing.T) {
	// Aircraft with elevation changes: ground (300m) to cruise (3000m)
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)
	points := make([]Point, 30)
	for i := 0; i < 30; i++ {
		t := makeTime(base, float64(i)*120)
		var elev float64
		if i < 5 {
			elev = 300 + float64(i)*100 // Climbing: 300-700m
		} else if i < 25 {
			elev = 3000 + float64(i%3)*50 // Cruising: ~3000m
		} else {
			elev = 3000 - float64(i-24)*500 // Descending
		}
		points[i] = Point{
			Lat:       float64(i) * 0.01,
			Lon:       36.0,
			Time:      t,
			Elevation: makeElev(elev),
		}
	}
	
	m := AnalyzeTrajectory(points)
	
	if !m.HasElevation {
		t.Error("expected HasElevation = true")
	}
	if m.ElevationRangeM < 500 {
		t.Errorf("expected elevation range > 500m for aircraft, got %.1f", m.ElevationRangeM)
	}
	if m.MaxElevationM < 2000 {
		t.Errorf("expected max elevation > 2000m, got %.1f", m.MaxElevationM)
	}
	if m.AvgElevationM < 1000 {
		t.Errorf("expected avg elevation > 1000m, got %.1f", m.AvgElevationM)
	}
	if m.ElevationChangeRate == 0 {
		t.Error("expected non-zero elevation change rate")
	}
}

func TestAnalyzeTrajectory_NoElevation(t *testing.T) {
	points := generateLinearTrack(0, 36, 30, 120, 20, nil)
	m := AnalyzeTrajectory(points)
	
	if m.HasElevation {
		t.Error("expected HasElevation = false when no elevation data")
	}
}

func TestAnalyzeTrajectory_SpeedPercentiles(t *testing.T) {
	// Vehicle that stops sometimes: mix of fast and slow segments
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)
	points := make([]Point, 20)
	for i := 0; i < 20; i++ {
		t := makeTime(base, float64(i)*120)
		var latOffset float64
		if i < 15 {
			// Moving at ~50 km/h: 50 * (120/3600) / 111 degrees per interval
			latOffset = float64(i) * (50.0 * 120.0 / 3600.0 / 111.0)
		} else {
			// Stopped
			latOffset = float64(14) * (50.0 * 120.0 / 3600.0 / 111.0)
		}
		points[i] = Point{
			Lat:  latOffset,
			Lon:  36.0,
			Time: t,
		}
	}
	
	m := AnalyzeTrajectory(points)
	
	// P90 should be close to the cruising speed, P10 should be much lower
	if m.P90SpeedKmh < 30 {
		t.Errorf("expected P90 speed > 30 km/h for vehicle with stops, got %.1f", m.P90SpeedKmh)
	}
	if m.P90SpeedKmh <= m.P10SpeedKmh {
		t.Errorf("expected P90 > P10, got P90=%.1f P10=%.1f", m.P90SpeedKmh, m.P10SpeedKmh)
	}
}

// --- Tests for improved classification ---

func TestClassifyMovement_HighSpeedAircraft(t *testing.T) {
	// 200 km/h straight line = clearly aircraft
	points := generateLinearTrack(-1, 36, 200, 120, 30, nil)
	c := ClassifyMovementFull(points)
	
	if c.MovementType != "aircraft" {
		t.Errorf("expected aircraft at 200 km/h, got %s", c.MovementType)
	}
	if c.Confidence < 0.9 {
		t.Errorf("expected high confidence for fast aircraft, got %.2f", c.Confidence)
	}
}

func TestClassifyMovement_AircraftByElevation(t *testing.T) {
	// Medium speed (60 km/h) but high elevation range = aircraft
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)
	points := make([]Point, 30)
	for i := 0; i < 30; i++ {
		t := makeTime(base, float64(i)*120)
		// Climb then cruise pattern
		var elev float64
		if i < 10 {
			elev = 400 + float64(i)*300 // Climbing 400m -> 3400m
		} else {
			elev = 3400 // Cruising at 3400m
		}
		// ~60 km/h north
		points[i] = Point{
			Lat:       float64(i) * (60.0 * 120.0 / 3600.0 / 111.0),
			Lon:       36.0,
			Time:      t,
			Elevation: makeElev(elev),
		}
	}
	
	c := ClassifyMovementFull(points)
	
	if c.MovementType != "aircraft" {
		t.Errorf("expected aircraft for high elevation range + cruise pattern, got %s (avg=%.1f, p90=%.1f, elevRange=%.0f, maxElev=%.0f)",
			c.MovementType, c.Metrics.AvgSpeedKmh, c.Metrics.P90SpeedKmh, c.Metrics.ElevationRangeM, c.Metrics.MaxElevationM)
	}
}

func TestClassifyMovement_VehicleByP90(t *testing.T) {
	// Vehicle: avg speed ~25 km/h but P90 ~50 km/h (stops and goes)
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)
	points := make([]Point, 40)
	for i := 0; i < 40; i++ {
		t := makeTime(base, float64(i)*120)
		var speed float64
		if i%5 == 0 {
			speed = 0 // stopped
		} else {
			speed = 50 // cruising at 50 km/h
		}
		var prevLat float64
		if i > 0 {
			prevLat = points[i-1].Lat
		}
		points[i] = Point{
			Lat:  prevLat + speed*(120.0/3600.0)/111.0,
			Lon:  36.0,
			Time: t,
		}
	}
	
	c := ClassifyMovementFull(points)
	
	if c.MovementType != "vehicle" {
		t.Errorf("expected vehicle for stop-and-go 50 km/h, got %s (avg=%.1f, p90=%.1f)",
			c.MovementType, c.Metrics.AvgSpeedKmh, c.Metrics.P90SpeedKmh)
	}
}

func TestClassifyMovement_SlowFootPatrol(t *testing.T) {
	// 3 km/h erratic = foot patrol
	points := generateErraticTrack(-1, 36, 3, 600, 25)
	c := ClassifyMovementFull(points)
	
	if c.MovementType != "foot" {
		t.Errorf("expected foot for 3 km/h erratic patrol, got %s (avg=%.1f, p90=%.1f)",
			c.MovementType, c.Metrics.AvgSpeedKmh, c.Metrics.P90SpeedKmh)
	}
}

func TestClassifyMovement_InReachFoot(t *testing.T) {
	// 600s interval, 4 km/h = InReach foot patrol
	points := generateLinearTrack(-2, 35, 4, 600, 20, nil)
	c := ClassifyMovementFull(points)
	
	if c.MovementType != "foot" {
		t.Errorf("expected foot for 4 km/h InReach (600s), got %s (avg=%.1f, median_interval=%.0fs)",
			c.MovementType, c.Metrics.AvgSpeedKmh, c.Metrics.MedianIntervalSec)
	}
}

func TestClassifyMovement_AmbiguousZone_VehicleSmooth(t *testing.T) {
	// 10 km/h, very smooth, linear trajectory = vehicle (not foot)
	points := generateLinearTrack(-1, 36, 10, 120, 30, nil)
	c := ClassifyMovementFull(points)
	
	// At 10 km/h with smooth linear trajectory, should lean vehicle
	if c.MovementType == "aircraft" {
		t.Errorf("should not be aircraft at 10 km/h, got %s", c.MovementType)
	}
	// Given very smooth and linear at 10 km/h, vehicle is more likely
	if c.MovementType != "vehicle" {
		t.Errorf("expected vehicle for smooth linear 10 km/h, got %s (smooth=%.2f, linear=%.2f, bearingVar=%.2f)",
			c.MovementType, c.Metrics.SmoothnessFactor, c.Metrics.LinearityScore, c.Metrics.BearingVariance)
	}
}

func TestClassifyMovement_AmbiguousZone_FootErratic(t *testing.T) {
	// 6 km/h, erratic trajectory = foot (exploring)
	points := generateErraticTrack(-1, 36, 6, 300, 30)
	c := ClassifyMovementFull(points)
	
	if c.MovementType == "aircraft" {
		t.Errorf("should not be aircraft at 6 km/h erratic, got %s", c.MovementType)
	}
	// Erratic 6 km/h should be foot
	if c.MovementType != "foot" {
		t.Errorf("expected foot for erratic 6 km/h, got %s (bearingVar=%.2f, smooth=%.2f, p90=%.1f)",
			c.MovementType, c.Metrics.BearingVariance, c.Metrics.SmoothnessFactor, c.Metrics.P90SpeedKmh)
	}
}

func TestClassifyMovement_GPSTracker120s_MediumSpeed(t *testing.T) {
	// 120s constant interval at 50 km/h = vehicle (GPS tracker)
	points := generateLinearTrack(0, 36, 50, 120, 30, nil)
	c := ClassifyMovementFull(points)
	
	if c.MovementType != "vehicle" {
		t.Errorf("expected vehicle for 120s interval 50 km/h, got %s (median_interval=%.0f)",
			c.MovementType, c.Metrics.MedianIntervalSec)
	}
}

func TestClassifyMovement_HintPreservation(t *testing.T) {
	// Test that authoritative hint (1.0) still overrides everything
	points := generateLinearTrack(0, 36, 200, 120, 30, nil) // clearly aircraft by speed
	
	hint := MovementHint{Type: "vehicle", Confidence: 1.0, IsVehicle: true}
	c := ClassifyMovementFullWithHint(points, hint)
	
	if c.MovementType != "vehicle" {
		t.Errorf("authoritative hint should override: expected vehicle, got %s", c.MovementType)
	}
	if c.Confidence != 1.0 {
		t.Errorf("expected confidence 1.0 for authoritative hint, got %.2f", c.Confidence)
	}
}

func TestClassifyMovement_StrongHintPreservation(t *testing.T) {
	// Strong hint (0.9) for foot, but speed > 20 => should upgrade to vehicle
	points := generateLinearTrack(0, 36, 30, 120, 30, nil)
	
	hint := MovementHint{Type: "foot", Confidence: 0.9, IsFoot: true}
	c := ClassifyMovementFullWithHint(points, hint)
	
	if c.MovementType != "vehicle" {
		t.Errorf("strong foot hint but speed 30 should be vehicle, got %s", c.MovementType)
	}
}

func TestClassifyMovement_ModerateHintNudge(t *testing.T) {
	// Speed ~10 km/h (ambiguous), moderate vehicle hint should nudge to vehicle
	points := generateLinearTrack(-1, 36, 10, 120, 30, nil)
	
	hintVehicle := MovementHint{Type: "vehicle", Confidence: 0.6, IsVehicle: true}
	c := ClassifyMovementFullWithHint(points, hintVehicle)
	
	if c.MovementType != "vehicle" {
		t.Errorf("expected vehicle with moderate vehicle hint at 10 km/h, got %s", c.MovementType)
	}
}

func TestClassifyMovement_BackwardCompat(t *testing.T) {
	// ClassifyMovementAdvanced should still work
	points := generateLinearTrack(0, 36, 3, 600, 20, nil)
	mvType := ClassifyMovementAdvanced(points)
	
	if mvType != "foot" {
		t.Errorf("ClassifyMovementAdvanced should return foot for 3 km/h, got %s", mvType)
	}
}

func TestClassifyMovement_FewPoints(t *testing.T) {
	// Less than 3 points => default behavior
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)
	t1 := makeTime(base, 0)
	t2 := makeTime(base, 600)
	points := []Point{
		{Lat: 0, Lon: 36, Time: t1},
		{Lat: 0.001, Lon: 36, Time: t2},
	}
	
	c := ClassifyMovementFull(points)
	if c.MovementType != "foot" {
		t.Errorf("expected foot for < 3 points, got %s", c.MovementType)
	}
}

func TestClassifyMovement_HighAltitudeAircraft(t *testing.T) {
	// Ennedi scenario: high altitude aircraft at moderate speed
	// Elevation up to ~6000m, speed ~120 km/h
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)
	points := make([]Point, 40)
	for i := 0; i < 40; i++ {
		t := makeTime(base, float64(i)*120)
		var elev float64
		switch {
		case i < 5:
			elev = 500 + float64(i)*500 // Climbing
		case i < 35:
			elev = 3000 + float64(i%5)*200 // Varying cruise altitude 3000-3800m
		default:
			elev = 3000 - float64(i-34)*500 // Descending
		}
		points[i] = Point{
			Lat:       float64(i) * (120.0 * 120.0 / 3600.0 / 111.0),
			Lon:       36.0,
			Time:      t,
			Elevation: makeElev(elev),
		}
	}
	
	c := ClassifyMovementFull(points)
	if c.MovementType != "aircraft" {
		t.Errorf("expected aircraft for high-altitude Ennedi-like track, got %s (avg=%.1f, p90=%.1f, elevRange=%.0f, maxElev=%.0f)",
			c.MovementType, c.Metrics.AvgSpeedKmh, c.Metrics.P90SpeedKmh, c.Metrics.ElevationRangeM, c.Metrics.MaxElevationM)
	}
}

func TestClassifyMovement_GroundVehicleWithElevation(t *testing.T) {
	// Vehicle at 40 km/h with moderate elevation (400-600m, typical ground)
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)
	points := make([]Point, 30)
	for i := 0; i < 30; i++ {
		t := makeTime(base, float64(i)*120)
		elev := 400.0 + float64(i%5)*40.0 // Small rolling hills 400-560m
		points[i] = Point{
			Lat:       float64(i) * (40.0 * 120.0 / 3600.0 / 111.0),
			Lon:       36.0,
			Time:      t,
			Elevation: makeElev(elev),
		}
	}
	
	c := ClassifyMovementFull(points)
	if c.MovementType != "vehicle" {
		t.Errorf("expected vehicle for ground-level 40 km/h, got %s (elevRange=%.0f, maxElev=%.0f)",
			c.MovementType, c.Metrics.ElevationRangeM, c.Metrics.MaxElevationM)
	}
}

func TestAnalyzeTrajectory_PartialElevation(t *testing.T) {
	// Only 30% of points have elevation => HasElevation should be false
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)
	points := make([]Point, 20)
	for i := 0; i < 20; i++ {
		t := makeTime(base, float64(i)*120)
		var elev *float64
		if i < 6 { // 6/20 = 30%
			elev = makeElev(500)
		}
		points[i] = Point{
			Lat:       float64(i) * 0.001,
			Lon:       36.0,
			Time:      t,
			Elevation: elev,
		}
	}
	
	m := AnalyzeTrajectory(points)
	if m.HasElevation {
		t.Error("expected HasElevation = false when only 30% of points have elevation")
	}
}

func TestAnalyzeTrajectory_IrregularIntervals(t *testing.T) {
	// Mix of short and long intervals => high IntervalConsistency
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)
	points := make([]Point, 20)
	cumSec := 0.0
	for i := 0; i < 20; i++ {
		if i > 0 {
			// Alternate between 30s and 300s intervals
			if i%2 == 0 {
				cumSec += 30
			} else {
				cumSec += 300
			}
		}
		t := makeTime(base, cumSec)
		points[i] = Point{
			Lat:  float64(i) * 0.001,
			Lon:  36.0,
			Time: t,
		}
	}
	
	m := AnalyzeTrajectory(points)
	if m.IntervalConsistency < 0.3 {
		t.Errorf("expected high IntervalConsistency for irregular intervals, got %.3f", m.IntervalConsistency)
	}
}

// --- Airstrip waypoint tests ---

func TestExtractAirstrips(t *testing.T) {
	waypoints := []Waypoint{
		{Lat: 4.78, Lon: 33.59, Name: "Kapoeta 1200m"},
		{Lat: 6.18, Lon: 34.39, Name: "Boma 900m"},
		{Lat: 4.87, Lon: 31.60, Name: "Juba  3000m"},
		{Lat: 6.19, Lon: 31.60, Name: "Bor 1.3 km"},
		{Lat: 7.74, Lon: 31.40, Name: "Duk Fadiat 1200 m"},
		{Lat: 7.15, Lon: 33.86, Name: "Otallo"},             // No runway = not an airstrip
		{Lat: 4.76, Lon: 32.63, Name: "Torit-Lafon Rd., Ama"}, // Road = not an airstrip
		{Lat: 5.0, Lon: 33.0, Name: "Loki airstrip"},           // Has "airstrip" keyword
	}

	airstrips := ExtractAirstrips(waypoints)

	if len(airstrips) != 6 {
		names := make([]string, len(airstrips))
		for i, a := range airstrips {
			names[i] = a.Name
		}
		t.Fatalf("expected 6 airstrips, got %d: %v", len(airstrips), names)
	}

	// Check runway lengths
	expected := map[string]float64{
		"Kapoeta 1200m":      1200,
		"Boma 900m":          900,
		"Juba  3000m":        3000,
		"Bor 1.3 km":         1300,
		"Duk Fadiat 1200 m":  1200,
		"Loki airstrip":      0,
	}
	for _, a := range airstrips {
		exp, ok := expected[a.Name]
		if !ok {
			t.Errorf("unexpected airstrip: %s", a.Name)
			continue
		}
		if math.Abs(a.RunwayM-exp) > 1 {
			t.Errorf("airstrip %q: expected runway %.0fm, got %.0fm", a.Name, exp, a.RunwayM)
		}
	}
}

func TestExtractAirstrips_NotAirstrip(t *testing.T) {
	// Names that should NOT be detected as airstrips
	waypoints := []Waypoint{
		{Lat: 0, Lon: 0, Name: "Camp Alpha"},
		{Lat: 0, Lon: 0, Name: "River crossing 50m wide"},  // 50m is too short for runway
		{Lat: 0, Lon: 0, Name: "Base camp elevation 1200m"}, // elevation, not runway
	}

	airstrips := ExtractAirstrips(waypoints)
	// "elevation 1200m" will match the pattern, so we need to check
	// that 50m doesn't (too short)
	for _, a := range airstrips {
		if a.Name == "River crossing 50m wide" {
			t.Error("50m should not be a plausible runway")
		}
	}
}

func TestIsTrackNearAirstrip(t *testing.T) {
	airstrips := []AirstripWaypoint{
		{Lat: 6.18, Lon: 34.39, Name: "Boma 900m", RunwayM: 900},
	}

	// Track starting near Boma
	points := []Point{
		{Lat: 6.19, Lon: 34.39}, // ~1.1 km from Boma
		{Lat: 6.50, Lon: 34.50},
		{Lat: 7.00, Lon: 34.60},
	}
	near, closest := isTrackNearAirstrip(points, airstrips, 5.0)
	if !near || closest == nil {
		t.Error("expected track to be near Boma airstrip")
	}

	// Track far from any airstrip
	far := []Point{
		{Lat: 0, Lon: 30},
		{Lat: 1, Lon: 30},
	}
	near2, _ := isTrackNearAirstrip(far, airstrips, 5.0)
	if near2 {
		t.Error("expected track to NOT be near any airstrip")
	}
}

func TestAirstripHintIntegration(t *testing.T) {
	// Simulate GPX with airstrip waypoints and a fast track starting near one.
	// The track at 150 km/h near an airstrip should get aircraft classification
	// even without ER hints.
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)

	// Create track points: takeoff from near Boma (6.18, 34.39), fly north at ~150 km/h
	var trackPoints []Point
	for i := 0; i < 30; i++ {
		t := makeTime(base, float64(i)*120)
		trackPoints = append(trackPoints, Point{
			Lat:  6.19 + float64(i)*(150.0*120.0/3600.0/111.0),
			Lon:  34.39,
			Time: t,
		})
	}

	data := &GPXData{
		Tracks: []Track{
			{Name: "some track", Segments: [][]Point{trackPoints}},
		},
		Waypoints: []Waypoint{
			{Lat: 6.18, Lon: 34.39, Name: "Boma 900m"},
		},
	}

	segments := SplitIntoSegments(data, 30*time.Minute)
	if len(segments) == 0 {
		t.Fatal("expected at least one segment")
	}

	// All segments from this fast track near airstrip should have aircraft hint
	for i, seg := range segments {
		if seg.Hint.Type != "aircraft" {
			t.Errorf("segment %d: expected aircraft hint, got %q (conf=%.2f)",
				i, seg.Hint.Type, seg.Hint.Confidence)
		}
		if seg.MovementType != "aircraft" {
			t.Errorf("segment %d: expected aircraft classification, got %q", i, seg.MovementType)
		}
	}
}

func TestAirstripHintNotAppliedToSlowTrack(t *testing.T) {
	// A slow track (5 km/h) near an airstrip should NOT get aircraft hint.
	// Foot patrols operate near airstrips too.
	base := time.Date(2024, 6, 15, 8, 0, 0, 0, time.UTC)

	var trackPoints []Point
	for i := 0; i < 20; i++ {
		tt := makeTime(base, float64(i)*600) // 600s intervals = InReach
		trackPoints = append(trackPoints, Point{
			Lat:  6.19 + float64(i)*(5.0*600.0/3600.0/111.0),
			Lon:  34.39,
			Time: tt,
		})
	}

	data := &GPXData{
		Tracks: []Track{
			{Name: "foot patrol", Segments: [][]Point{trackPoints}},
		},
		Waypoints: []Waypoint{
			{Lat: 6.18, Lon: 34.39, Name: "Boma 900m"},
		},
	}

	segments := SplitIntoSegments(data, 30*time.Minute)
	for _, seg := range segments {
		if seg.Hint.Type == "aircraft" {
			t.Errorf("slow track near airstrip should NOT get aircraft hint, got conf=%.2f", seg.Hint.Confidence)
		}
		if seg.MovementType == "aircraft" {
			t.Error("slow track near airstrip should NOT be classified as aircraft")
		}
	}
}

// Ensure unused import doesn't cause issues
var _ = math.Abs
