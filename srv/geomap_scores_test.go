package srv

import (
	"encoding/json"
	"math"
	"os"
	"testing"
)

// The scores in geomap_scores.go are typed by hand from the eval's output, so
// this test is the only thing standing between the UI and a number nobody
// measured. It is the same rule as cross-cutting invariant 2 ("never type a
// count that describes a variable input") applied to a measurement: the table
// may be hand-written, but it may not DISAGREE with the file the script wrote.
//
// It reads data/eval/geo_affinity_car.json, which is gitignored — so on a fresh
// clone the test skips rather than fails. A missing eval is a missing input,
// not a wrong number, and a test that fails for everyone who has not run a
// 90-second script gets disabled within a week.
func TestAffinityScoresMatchEvalOutput(t *testing.T) {
	const path = "../data/eval/geo_affinity_car.json"
	blob, err := os.ReadFile(path)
	if err != nil {
		t.Skipf("%s absent — run scripts/geomaps/eval_affinity.py", path)
	}
	var doc struct {
		Units map[string]map[string]struct {
			Capture   float64 `json:"capture"`
			AreaShare float64 `json:"area_share"`
			Lift      float64 `json:"lift"`
		} `json:"units"`
		Junctions map[string]map[string]struct {
			SiteNear     float64 `json:"site_near"`
			RandomNear   float64 `json:"random_near"`
			Lift         float64 `json:"lift"`
			ControlRatio float64 `json:"control_ratio"`
		} `json:"junctions"`
	}
	if err := json.Unmarshal(blob, &doc); err != nil {
		t.Fatalf("eval output unreadable: %v", err)
	}
	// 0.005 absolute: the table quotes three decimals and the script prints
	// more. Anything looser would let a re-measured 2.32 -> 1.9 pass.
	const tol = 0.005
	close := func(a, b float64) bool { return math.Abs(a-b) <= tol }

	for _, s := range geoAffinityScores {
		key := string(rune('0' + s.MinWeight))
		switch s.Kind {
		case "unit":
			got, ok := doc.Units[s.Commodity][key]
			if !ok {
				t.Errorf("%s unit w>=%d: shipped as a score, absent from the eval",
					s.Commodity, s.MinWeight)
				continue
			}
			if !close(got.Capture, s.Capture) || !close(got.AreaShare, s.Baseline) ||
				!close(got.Lift, s.Lift) {
				t.Errorf("%s unit w>=%d: shipped capture/baseline/lift %.3f/%.3f/%.2f, eval says %.3f/%.3f/%.2f",
					s.Commodity, s.MinWeight, s.Capture, s.Baseline, s.Lift,
					got.Capture, got.AreaShare, got.Lift)
			}
		case "junction":
			got, ok := doc.Junctions[s.Commodity][key]
			if !ok {
				t.Errorf("%s junction w>=%d: shipped as a score, absent from the eval",
					s.Commodity, s.MinWeight)
				continue
			}
			if !close(got.SiteNear, s.Capture) || !close(got.RandomNear, s.Baseline) ||
				!close(got.Lift, s.Lift) {
				t.Errorf("%s junction w>=%d: shipped capture/baseline/lift %.3f/%.3f/%.2f, eval says %.3f/%.3f/%.2f",
					s.Commodity, s.MinWeight, s.Capture, s.Baseline, s.Lift,
					got.SiteNear, got.RandomNear, got.Lift)
			}
			if s.Control > 0 && !close(got.ControlRatio, s.Control) {
				t.Errorf("%s junction w>=%d: shipped control %.2f, eval says %.2f",
					s.Commodity, s.MinWeight, s.Control, got.ControlRatio)
			}
		default:
			t.Errorf("%s: unknown kind %q", s.Commodity, s.Kind)
		}
	}
}

// A verdict is what the UI colours on, so it must follow from the lift and not
// from whoever typed the row. 1.0 is the line by construction: the baseline IS
// "the same amount of ground picked at random".
func TestAffinityVerdictFollowsLift(t *testing.T) {
	for _, s := range geoAffinityScores {
		want := "no better than area"
		if s.Lift > 1.0 {
			want = "concentrates"
		}
		if s.Verdict != want {
			t.Errorf("%s %s w>=%d: lift %.2f is %q, table says %q",
				s.Commodity, s.Kind, s.MinWeight, s.Lift, want, s.Verdict)
		}
	}
}

// geoAffinityScoreFor must never return a HIGHER floor than asked for: a reader
// looking at "any host" would otherwise be shown the "classic" number, which is
// the one shape of error that flatters the model at exactly the moment the
// reader is least protected.
func TestAffinityScoreForNeverQuotesAHigherFloor(t *testing.T) {
	for _, com := range []string{"gold", "diamond", "copper"} {
		for _, kind := range []string{"unit", "junction"} {
			for mw := 1; mw <= 3; mw++ {
				got := geoAffinityScoreFor(com, kind, mw)
				if got == nil {
					continue
				}
				if got.MinWeight > mw {
					t.Errorf("%s %s at floor %d quoted the w>=%d score",
						com, kind, mw, got.MinWeight)
				}
				if got.Commodity != com || got.Kind != kind {
					t.Errorf("%s %s at floor %d quoted %s %s",
						com, kind, mw, got.Commodity, got.Kind)
				}
			}
		}
	}
	if geoAffinityScoreFor("uranium", "unit", 3) != nil {
		t.Error("uranium has no occurrence dataset; a score for it is invented")
	}
}
