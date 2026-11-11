package srv

import (
	"math"
	"testing"
	"time"

	"srv.exe.dev/srv/gpx"
)

func tp(lat, lon float64, t time.Time) gpx.Point { return gpx.Point{Lat: lat, Lon: lon, Time: &t} }

// The bug this replaced compared kilometres with a radius in metres: any
// 30-minute segment was "a stop". A vehicle driving 40 km/h is not staying.
func TestDetectStaysUnits(t *testing.T) {
	t0 := time.Date(2026, 7, 1, 6, 0, 0, 0, time.UTC)
	var drive, camp []gpx.Point
	for i := 0; i < 60; i++ { // 1 fix/min, ~660 m apart
		drive = append(drive, tp(-8.0+float64(i)*0.006, 37.0, t0.Add(time.Duration(i)*time.Minute)))
	}
	if s := detectStays(drive); len(s) != 0 {
		t.Fatalf("a moving vehicle produced %d stays", len(s))
	}
	for i := 0; i < 120; i++ { // 2 h within ~20 m, one 400 m multipath fix
		lat := -8.0 + float64(i%3)*0.0001
		if i == 50 {
			lat += 0.004
		}
		camp = append(camp, tp(lat, 37.0, t0.Add(time.Duration(i)*time.Minute)))
	}
	s := detectStays(camp)
	if len(s) != 1 || s[0].DwellMin < 115 {
		t.Fatalf("camp: want one ~2 h stay unbroken by an outlier fix, got %+v", s)
	}
}

func TestEventDedupeIsIdempotent(t *testing.T) {
	t0 := time.Date(2026, 7, 1, 6, 0, 0, 0, time.UTC)
	evs := []LearnerEvent{
		{Kind: "landing", Tracker: "a", Start: t0, End: t0, Lat: -8, Lon: 37, HeadingDeg: 90, Subtype: "fixed_wing", Journey: "a@1"},
		{Kind: "takeoff", Tracker: "a", Start: t0.Add(time.Hour), End: t0.Add(time.Hour), Lat: -8, Lon: 37.001, HeadingDeg: 270, Subtype: "fixed_wing", Journey: "a@2"},
		{Kind: "stop", Tracker: "v", Start: t0, End: t0.Add(2 * time.Hour), Lat: -8.1, Lon: 37, DwellMin: 120},
	}
	var trk VehicleTrack
	trk.Tracker = "v"
	for i := 0; i < 40; i++ {
		trk.Coords = append(trk.Coords, [3]float64{37 + float64(i)*0.001, -8.2, float64(t0.Unix() + int64(i)*20)})
	}
	pl := NewParkLearner("X")
	pl.Add(evs, []VehicleTrack{trk})
	n := len(pl.NewEvents)
	if n != 3+len(pl.Roads) || len(pl.Roads) == 0 {
		t.Fatalf("first delivery: %d events, %d roads", n, len(pl.Roads))
	}
	// Re-delivered: same events, a stop reported 30 min longer, the same track.
	evs[2].End = evs[2].End.Add(30 * time.Minute)
	pl.Add(evs, []VehicleTrack{trk})
	if len(pl.NewEvents) != n {
		t.Fatalf("re-delivery added %d events", len(pl.NewEvents)-n)
	}
	if len(pl.Airfields) != 1 || len(pl.Places) != 1 {
		t.Fatalf("sites: %d airfields, %d places", len(pl.Airfields), len(pl.Places))
	}
	if d := pl.Places[0].Events[0].DwellMin; d != 150 {
		t.Fatalf("re-delivered longer stop should extend the stay, dwell %v", d)
	}
	// The same tracker an hour later is a second traversal; another day a third.
	for _, off := range []time.Duration{time.Hour, 26 * time.Hour} {
		t2 := trk
		t2.Coords = nil
		for _, c := range trk.Coords {
			t2.Coords = append(t2.Coords, [3]float64{c[0], c[1], c[2] + off.Seconds()})
		}
		pl.Add(nil, []VehicleTrack{t2})
	}
	ev := RoadEvidence(derefEvents(pl.Roads[0].Events), pl.Roads[0].LengthM)
	if ev.Events != 3 || ev.Days != 2 || len(pl.Roads) != 1 {
		t.Fatalf("road: %d events on %d days in %d sections; want 3, 2, 1", ev.Events, ev.Days, len(pl.Roads))
	}
}

func derefEvents(in []*LearnerEvent) []LearnerEvent {
	out := make([]LearnerEvent, len(in))
	for i, e := range in {
		out[i] = *e
	}
	return out
}

// A runway is derived from ground-run fixes or it is absent; a helicopter-only
// site is a landing zone and never gets a line.
func TestAirfieldEvidenceRunway(t *testing.T) {
	t0 := time.Date(2026, 7, 1, 6, 0, 0, 0, time.UTC)
	mk := func(sub string, withRoll bool) []LearnerEvent {
		var evs []LearnerEvent
		for i := 0; i < 6; i++ {
			e := LearnerEvent{Kind: []string{"landing", "takeoff"}[i%2], Tracker: "a", Start: t0.Add(time.Duration(i) * 25 * time.Hour),
				Lat: -8, Lon: 37.004, HeadingDeg: []float64{92, 268}[i%2], Subtype: sub, Journey: string(rune('a' + i))}
			if withRoll {
				for k := 0; k < 4; k++ { // fixes along an east-west strip ~880 m long
					e.Roll = append(e.Roll, [3]float64{37.0 + float64((i+k*2)%9)*0.001, -8.0 + float64(k%2)*0.00005, 60})
				}
			}
			evs = append(evs, e)
		}
		return evs
	}
	e := AirfieldEvidence(mk("fixed_wing", true), nil)
	if e.Kind != "airstrip" || e.LengthM == nil || len(e.Line) != 2 {
		t.Fatalf("fixed-wing with ground runs: %+v", e)
	}
	if *e.LengthM < 700 || *e.LengthM > 1000 || math.Abs(*e.AxisDeg-90) > 5 {
		t.Fatalf("runway length %v axis %v; want ~880 m, ~90", *e.LengthM, *e.AxisDeg)
	}
	if e.Days != 6 || e.Journeys != 6 || e.Landings != 3 || e.Takeoffs != 3 || *e.AxisAgreement < 0.95 {
		t.Fatalf("evidence counts: %+v", e)
	}
	if e := AirfieldEvidence(mk("fixed_wing", false), nil); e.LengthM != nil || e.Line != nil {
		t.Fatalf("no ground runs observed, yet a runway was drawn: %+v", e)
	}
	if e := AirfieldEvidence(mk("rotor_wing", true), nil); e.Kind != "landing_zone" || e.Line != nil {
		t.Fatalf("helicopter-only site must be a landing zone without a runway: %+v", e)
	}
	if e := AirfieldEvidence(mk("", true), nil); e.Kind != "airfield" {
		t.Fatalf("unmeasured airframe must stay 'airfield', got %s", e.Kind)
	}
}

// Three states stay distinct, a reviewer is never overridden, and demotion
// returns an auto-approved feature to pending -- not to rejected.
func TestDecideStatusStates(t *testing.T) {
	old := learnedScoreModels
	defer func() { learnedScoreModels = old }()
	learnedScoreModels = map[string]learnedScoreModelT{"road": {
		Features: LearnerFeatureNames["road"], Intercept: -2, Coef: []float64{3, 0, 0, 0, 0, -1.5},
		Approve: 0.9, Reject: 0.05, PlattA: 1}}
	ev := func(days, idle int) Evidence {
		return Evidence{Family: "road", Kind: "road", Days: days, Events: days, Trackers: 1, IdleActiveDays: idle}
	}
	if s, _, _ := DecideStatus("pending", false, ev(10, 0)); s != "auto_approved" {
		t.Fatalf("strong evidence: %s", s)
	}
	if s, _, _ := DecideStatus("pending", false, ev(2, 0)); s != "pending" {
		t.Fatalf("thin evidence: %s", s)
	}
	if s, _, _ := DecideStatus("auto_approved", false, ev(3, 2)); s != "auto_approved" {
		t.Fatalf("hysteresis: a short silence must not demote, got %s", s)
	}
	if s, _, r := DecideStatus("auto_approved", false, ev(3, 60)); s != "pending" || r == "" {
		t.Fatalf("idle while the park was active must demote to pending, got %s", s)
	}
	if s, _, _ := DecideStatus("pending", false, ev(1, 60)); s != "rejected" {
		t.Fatalf("one use, long contradicted: %s", s)
	}
	if s, _, _ := DecideStatus("pending", false, ev(1, 5)); s != "pending" {
		t.Fatalf("nothing is rejected before learnedRejectMinIdle active days: %s", s)
	}
	for _, human := range []string{"approved", "rejected"} {
		if s, _, _ := DecideStatus(human, true, ev(1, 60)); s != human {
			t.Fatalf("reviewer's %q overridden to %q", human, s)
		}
	}
}

func TestLearnedScoreModelMatchesFeatures(t *testing.T) {
	for fam, m := range learnedScoreModels {
		want := LearnerFeatureNames[fam]
		if len(m.Features) != len(want) || len(m.Coef) != len(want) {
			t.Fatalf("%s: model has %d features, code %d; re-run scripts/train_learner_score.py", fam, len(m.Features), len(want))
		}
		for i := range want {
			if m.Features[i] != want[i] {
				t.Fatalf("%s feature %d: model %q, code %q", fam, i, m.Features[i], want[i])
			}
		}
	}
}
