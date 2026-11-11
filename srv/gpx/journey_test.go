package gpx

import (
	"math"
	"testing"
	"time"
)

// track builds a path heading north-east from speeds (km/h), one per step.
func track(stepSec float64, speeds []float64) []Point {
	t := time.Date(2026, 1, 1, 6, 0, 0, 0, time.UTC)
	lat, lon := -8.0, 37.0
	pts := []Point{{Lat: lat, Lon: lon, Time: &t}}
	for i, s := range speeds {
		d := s * stepSec / 3600 / 111.2
		lat += d * 0.8
		lon += d * 0.6
		ti := t.Add(time.Duration(float64(i+1)*stepSec) * time.Second)
		pts = append(pts, Point{Lat: lat, Lon: lon, Time: &ti})
	}
	return pts
}

func rep(v float64, n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = v
	}
	return out
}

func TestAircraftModelMatchesFeatures(t *testing.T) {
	if len(aircraftSubtypeFeatures) != len(AircraftFeatureNames) {
		t.Fatalf("model trained on %d features, code computes %d: re-run scripts/train_aircraft_subtype.py", len(aircraftSubtypeFeatures), len(AircraftFeatureNames))
	}
	for i, n := range AircraftFeatureNames {
		if aircraftSubtypeFeatures[i] != n {
			t.Fatalf("feature %d: model %q, code %q", i, aircraftSubtypeFeatures[i], n)
		}
	}
	f, _, ok := AircraftFeatures(track(15, append(rep(0, 20), rep(150, 200)...)))
	if !ok || len(f) != len(AircraftFeatureNames) {
		t.Fatalf("AircraftFeatures returned %d values, want %d", len(f), len(AircraftFeatureNames))
	}
}

func TestBoatFromMovement(t *testing.T) {
	// 90 minutes at a held 28 km/h, small ripple: a boat.
	var sp []float64
	for i := 0; i < 540; i++ {
		sp = append(sp, 28+2*math.Sin(float64(i)/7))
	}
	boat := track(10, sp)
	if !BoatFromMovement(AnalyzeTrajectory(boat), len(boat)) {
		t.Error("steady 28 km/h for 90 min should read as a boat")
	}
	if s, _ := JourneySubtype(boat, "vehicle", MovementHint{}); s != "boat" {
		t.Errorf("JourneySubtype = %q, want boat", s)
	}
	// The device says truck: never a boat.
	if s, _ := JourneySubtype(boat, "vehicle", MovementHint{SubtypeHint: "land"}); s != "" {
		t.Errorf("land-vehicle hint must veto boat, got %q", s)
	}
	// A truck on a park road: same average, but it brakes, crawls and stops.
	var road []float64
	for i := 0; i < 540; i++ {
		switch {
		case i%60 < 6:
			road = append(road, 0)
		case i%60 < 20:
			road = append(road, 12)
		default:
			road = append(road, 42)
		}
	}
	truck := track(10, road)
	if BoatFromMovement(AnalyzeTrajectory(truck), len(truck)) {
		t.Error("stop-and-go driving must not read as a boat")
	}
	// Ten minutes of steady speed is not enough evidence.
	short := track(10, rep(28, 60))
	if BoatFromMovement(AnalyzeTrajectory(short), len(short)) {
		t.Error("a 10-minute steady run must not be called a boat")
	}
}

func TestDetectAirfieldEvents(t *testing.T) {
	// Parked 10 min, fly 30 min, parked 10 min: one take-off, one landing.
	full := track(15, append(append(rep(0, 40), rep(160, 120)...), rep(0, 40)...))
	ev := DetectAirfieldEvents(full)
	if len(ev) != 2 || ev[0].Kind != "takeoff" || ev[1].Kind != "landing" {
		t.Fatalf("want takeoff+landing, got %+v", ev)
	}
	if d := math.Abs(ev[0].Lat-full[0].Lat) * 111.2; d > 1.5 {
		t.Errorf("take-off placed %.1f km from where the aircraft stood", d)
	}
	// The same flight cut off in the air at both ends (a window edge, a park
	// boundary): it says nothing about where it landed.
	if ev := DetectAirfieldEvents(track(15, rep(160, 120))); len(ev) != 0 {
		t.Errorf("a journey that begins and ends in flight has no airfield, got %+v", ev)
	}
	// A slow minute in the middle of a flight (an orbit) is not a landing.
	orbit := track(15, append(append(rep(160, 60), rep(20, 4)...), rep(160, 60)...))
	if ev := DetectAirfieldEvents(orbit); len(ev) != 0 {
		t.Errorf("one slow minute mid-flight is not a landing, got %+v", ev)
	}
}
