package srv

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
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
	const path = "../data/eval/geo_affinity.json"
	blob, err := os.ReadFile(path)
	if err != nil {
		t.Skipf("%s absent — run scripts/geomaps/eval_affinity.py", path)
	}
	type per struct {
		Capture      float64 `json:"capture"`
		AreaShare    float64 `json:"area_share"`
		SiteNear     float64 `json:"site_near"`
		RandomNear   float64 `json:"random_near"`
		Lift         float64 `json:"lift"`
		ControlRatio float64 `json:"control_ratio"`
	}
	// A COMMODITY'S RECORD IS EITHER FLOORS OR A REFUSAL, and the refusal is
	// the point: under the floor the eval writes {"verdict": "too few sites",
	// "n": 4} where a scored commodity has {"1": {...}}. Decoding straight into
	// map[string]per fails on that string, so the two shapes are separated
	// here — and a test that "fixed" it by dropping the refusals would stop
	// noticing that eastern CAR is unmeasured rather than empty.
	var doc struct {
		Sheets map[string]map[string]struct {
			Units     map[string]map[string]json.RawMessage `json:"units"`
			Junctions map[string]map[string]json.RawMessage `json:"junctions"`
		} `json:"sheets"`
	}
	if err := json.Unmarshal(blob, &doc); err != nil {
		t.Fatalf("eval output unreadable: %v", err)
	}
	floor := func(m map[string]json.RawMessage, key string) (per, bool) {
		raw, ok := m[key]
		if !ok {
			return per{}, false
		}
		var v per
		if json.Unmarshal(raw, &v) != nil {
			return per{}, false
		}
		return v, true
	}
	const tol = 0.005
	near := func(a, b float64) bool { return math.Abs(a-b) <= tol }

	// EVERY SHIPPED ROW IS FOUND BY ITS OWN EVIDENCE ID. Keying on the sheet
	// alone would let a CAR row be validated against whichever CAR list
	// happened to hold a matching number — which is exactly the confusion
	// EvidenceID exists to remove, arriving inside the test that guards it.
	checked := 0
	for _, s := range geoAffinityScores {
		sheet, tid, ok := strings.Cut(s.EvidenceID, "/")
		if !ok {
			t.Errorf("%s %s w>=%d: evidence_id %q is not sheet/list",
				s.Commodity, s.Kind, s.MinWeight, s.EvidenceID)
			continue
		}
		if sheet != s.ScopeSheet {
			t.Errorf("%s: evidence_id %q disagrees with scope_sheet %q",
				s.Commodity, s.EvidenceID, s.ScopeSheet)
		}
		rec, okSheet := doc.Sheets[sheet][tid]
		if !okSheet {
			t.Errorf("%s %s w>=%d: shipped from %q, which the eval did not score",
				s.Commodity, s.Kind, s.MinWeight, s.EvidenceID)
			continue
		}
		key := strconv.Itoa(s.MinWeight)
		var got per
		var found bool
		switch s.Kind {
		case "unit":
			got, found = floor(rec.Units[s.Commodity], key)
			if found && (!near(got.Capture, s.Capture) || !near(got.AreaShare, s.Baseline)) {
				t.Errorf("%s %s unit w>=%d: shipped %.3f/%.3f, eval says %.3f/%.3f",
					s.EvidenceID, s.Commodity, s.MinWeight,
					s.Capture, s.Baseline, got.Capture, got.AreaShare)
			}
		case "junction":
			got, found = floor(rec.Junctions[s.Commodity], key)
			if found && (!near(got.SiteNear, s.Capture) || !near(got.RandomNear, s.Baseline)) {
				t.Errorf("%s %s junction w>=%d: shipped %.3f/%.3f, eval says %.3f/%.3f",
					s.EvidenceID, s.Commodity, s.MinWeight,
					s.Capture, s.Baseline, got.SiteNear, got.RandomNear)
			}
			if found && s.Control > 0 && !near(got.ControlRatio, s.Control) {
				t.Errorf("%s %s junction w>=%d: shipped control %.2f, eval says %.2f",
					s.EvidenceID, s.Commodity, s.MinWeight, s.Control, got.ControlRatio)
			}
		default:
			t.Errorf("%s: unknown kind %q", s.Commodity, s.Kind)
			continue
		}
		if !found {
			t.Errorf("%s %s %s w>=%d: shipped as a score, absent from the eval",
				s.EvidenceID, s.Commodity, s.Kind, s.MinWeight)
			continue
		}
		if !near(got.Lift, s.Lift) {
			t.Errorf("%s %s %s w>=%d: shipped lift %.3f, eval says %.3f",
				s.EvidenceID, s.Commodity, s.Kind, s.MinWeight, s.Lift, got.Lift)
		}
		checked++
	}
	// A GENERATOR THAT EMITS NOTHING PASSES EVERY LOOP ABOVE. The table is
	// generated now, so "no rows" is a plausible failure and it must not be a
	// silent one (invariant 1).
	if checked == 0 {
		t.Fatal("no shipped score was checked against the eval — the table is empty " +
			"or every evidence_id is unknown; re-run gen_scores_go.py")
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
	// A COMMODITY NO LIST REACHES HAS NO SCORE. Uranium used to be the example
	// and is not any more — the Tanzanian register holds 14 uranium
	// occurrences, so it is measured there now, which is the whole point of
	// running every sheet. Cobalt is the current state: named by the affinity
	// table, held by no list above the floor.
	if got := geoAffinityScoreFor("cobalt", "unit", 3); got != nil {
		t.Errorf("cobalt is under the floor in every list we hold; a %.2fx for it "+
			"was invented (from %s)", got.Lift, got.EvidenceID)
	}
}

// A SCORE IS A CLAIM ABOUT GROUND, so it must name the ground.
//
// The UI decides whether to quote a score by intersecting the scoring sheet's
// bounds with the viewport (GeoMap.skillHere): a score whose ScopeSheet is
// empty, or names a sheet this server does not have, silently falls back to
// "the eval's sheet" and would then be quoted over every country on the map —
// which is the invented number the whole mechanism exists to prevent, arriving
// from the other side.
func TestAffinityScoresNameTheGroundTheyWereMeasuredOn(t *testing.T) {
	known := map[string]bool{}
	for _, id := range geoMapSheets {
		known[id] = true
	}
	for _, s := range geoAffinityScores {
		if s.ScopeSheet == "" {
			t.Errorf("%s %s w>=%d: no scope_sheet — the client cannot tell whether "+
				"this number speaks for the viewport", s.Commodity, s.Kind, s.MinWeight)
			continue
		}
		if !known[s.ScopeSheet] {
			t.Errorf("%s %s w>=%d: scope_sheet %q is not a sheet this server serves (%v), "+
				"so the client can never find its bounds and will never quote it",
				s.Commodity, s.Kind, s.MinWeight, s.ScopeSheet, geoMapSheets)
		}
	}
	// EVERY TRUTH SET NAMES ITS PLACE AND ITS CAVEAT. A number whose caveat is
	// empty is a number that will be quoted bare.
	for eid, ts := range geoAffinityTruth {
		if ts.Place == "" {
			t.Errorf("%s: no place — the UI would print a sheet id at the user", eid)
		}
		if ts.Caveat == "" {
			t.Errorf("%s: no caveat", eid)
		}
	}
}

// Every row ships its scope, or the client's viewport test is a coin flip.
func TestAffinityScoresJSONCarriesScope(t *testing.T) {
	doc := geoAffinityScoresJSON()
	rows, ok := doc["scores"].([]map[string]any)
	if !ok || len(rows) == 0 {
		t.Fatalf("affinity_skill.scores is not a non-empty list: %T", doc["scores"])
	}
	for _, r := range rows {
		if s, _ := r["scope_sheet"].(string); s == "" {
			t.Errorf("shipped row %v has no scope_sheet", r["commodity"])
		}
		if _, ok := r["verdict"]; !ok {
			t.Errorf("shipped row %v has no verdict — the UI colours on it", r["commodity"])
		}
	}
}

// A SCOPE IS A PLACE, NOT A SHEET ID.
//
// Everywhere else in this UI a sheet is provenance: one Geology toggle, one
// legend, commodity chips that act on all three because rock does not stop at a
// border. The score sentence is the one place a sheet id could reach the user's
// eye ("CAR sheet, IPIS 2019"), and a reader asking where to prepare for the
// next gold rush has no way to know whether "the car sheet" covers them.
//
// TWO SHEET IDS ARE ALSO COUNTRY NAMES ("sudan", "tanzania"), so this cannot
// simply ban the ids: "Sudan and South Sudan, OSM mine tags" is exactly the
// phrasing we want. What it bans is the id AS AN ID — the lowercase form, which
// no prose produces — and the word "sheet", which is what turned a place into a
// dataset in the original wording.
func TestAffinityScopePhrasesNameAPlaceNotASheet(t *testing.T) {
	check := func(what, phrase string) {
		if phrase == "" {
			t.Errorf("%s: no scope phrase; the UI prints it verbatim", what)
			return
		}
		for _, tok := range strings.FieldsFunc(phrase, func(r rune) bool {
			return !('a' <= r && r <= 'z') && !('A' <= r && r <= 'Z')
		}) {
			if tok == "sheet" || tok == "sheets" {
				t.Errorf("%s: scope %q calls the ground a sheet; name the place",
					what, phrase)
			}
			for _, id := range geoMapSheets {
				// Case-SENSITIVE: prose writes "Sudan", an id is "sudan".
				if tok == id {
					t.Errorf("%s: scope %q says the sheet id %q at the user; "+
						"name the country instead", what, phrase, id)
				}
			}
		}
	}
	for _, s := range geoAffinityScores {
		check(fmt.Sprintf("%s %s w>=%d", s.Commodity, s.Kind, s.MinWeight), s.Scope)
	}
	for eid, ts := range geoAffinityTruth {
		check(eid+" place", ts.Place)
	}
}

// A STRATUM IS NOT A SECOND OPINION.
//
// Two halves of one survey share its footprint and its definition of a mine, so
// counting them as two lists that agree lets one survey vote three times. The
// evidence collector must exclude them — and must still find the pooled row.
func TestAffinityEvidenceExcludesStrata(t *testing.T) {
	ev := geoAffinityEvidenceFor("car", "gold", "unit", 1)
	if len(ev.Scores) == 0 {
		t.Fatal("no evidence for CAR gold units — the collector found nothing")
	}
	seen := map[string]bool{}
	for _, s := range ev.Scores {
		if s.StratumOf != "" {
			t.Errorf("%s is a stratum of %s and must not count as evidence",
				s.EvidenceID, s.StratumOf)
		}
		if seen[s.EvidenceID] {
			t.Errorf("%s appears twice — one list, one vote", s.EvidenceID)
		}
		seen[s.EvidenceID] = true
	}
	// The strata still have to be REPORTED, as a spread on the pooled claim:
	// the CAR gold unit lift is 2.04x where IPIS recorded an armed actor and
	// 1.31x where it did not (capture p=0.0033), and a surface that quotes the
	// pooled number without that is quoting an unnamed variable.
	if ev.Spread <= 1.0 {
		t.Errorf("CAR gold units: strata spread is %.3f; the eval measured 1.55 — "+
			"re-run gen_scores_go.py, or the confound has silently stopped shipping",
			ev.Spread)
	}
}

// DISAGREEMENT IS A RESULT, AND IT HAS TO REACH THE READER AS A WORD.
//
// Three independent lists reach the CAR. On gold junctions IPIS says 2.4x and
// Tearline's permit census says 0.0x — any single row is defensible alone and
// misleading on screen, so the verdict for that claim is "mixed" and a UI that
// cannot say the word must not show a number.
func TestAffinityEvidenceReportsMixedVerdicts(t *testing.T) {
	ev := geoAffinityEvidenceFor("car", "gold", "junction", 2)
	if len(ev.Scores) < 2 {
		t.Skip("only one list scored CAR gold junctions; nothing to disagree")
	}
	above, below := false, false
	for _, s := range ev.Scores {
		if s.Lift > 1.0 {
			above = true
		} else {
			below = true
		}
	}
	want := "concentrates"
	switch {
	case above && below:
		want = "mixed"
	case below:
		want = "no better than area"
	}
	if ev.Verdict != want {
		t.Errorf("CAR gold junctions: %d lists (above=%v below=%v) but verdict %q, want %q",
			len(ev.Scores), above, below, ev.Verdict, want)
	}
}

// A LIST UNDER THE FLOOR IS NOT A LIST THAT FOUND NOTHING.
//
// Crisis Tracker holds 41 CAR mine sites and 5 name a mineral, so every
// commodity there is under geoAffinityMinSites. It ships as a count, not as a
// zero and not as an absence: "we looked and there were four" and "nobody
// looked" are different statements, and eastern CAR is currently the second.
func TestAffinityTooFewIsACountNotAZero(t *testing.T) {
	ev := geoAffinityEvidenceFor("car", "gold", "unit", 1)
	if len(ev.TooFew) == 0 {
		t.Fatal("no under-floor list reported for CAR gold; Crisis Tracker holds 4")
	}
	for eid, n := range ev.TooFew {
		if n >= geoAffinityMinSites {
			t.Errorf("%s: %d sites is not under the floor of %d",
				eid, n, geoAffinityMinSites)
		}
		for _, s := range ev.Scores {
			if s.EvidenceID == eid {
				t.Errorf("%s is reported both as a score and as too few sites", eid)
			}
		}
	}
}
