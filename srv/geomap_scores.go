// Measured skill of the commodity-affinity model.
//
// The geology panel makes two claims a user acts on: "these rock types can
// host gold" (the commodity chooser) and "these junctions can host gold" (the
// Junctions tab). Both are inferences over lithology, stated in textbook
// language, drawn on a map. Until 2026-08-13 neither had ever been scored
// against an occurrence dataset, so nothing on screen distinguished a claim
// that concentrates known workings 2.3x from one that does *worse than picking
// area at random* - and both were rendered in the same amber, with the same
// three dots, under the same "an inference, not a record" disclaimer.
//
// That disclaimer is true and it is not enough. It tells the reader the layer
// is not evidence; it does not tell them the layer is not USEFUL. A reader
// hunting "where is the next gold rush" reads three dots as a ranking, because
// a ranking is the only thing three dots can be.
//
// So the model now carries its own score, keyed the way the claim is made
// (commodity x kind), and every surface that shows a grade shows it:
// scripts/geomaps/eval_affinity.py produces these numbers, this file is where
// they are quoted, and the number in the UI is the number the script printed.
//
// ⚠️ THE SCORES ARE NOT A TUNING TARGET. The CAR unit rows score 0.63, and the
// fix is NOT to re-weight the affinity table until they score 1.5 - that fits
// 675 IPIS visits in one country and calls it geology. Cross-cutting invariant
// 12 ("never tune the fire algorithm by eye") is the same rule: the eval exists
// to make a weak claim legible, not to launder it. If a rule changes, it
// changes because the geology argument changed, and the score is re-measured
// afterwards.
package srv

// geoAffinityScore is one measured comparison: how much of an occurrence set a
// filter captures, against what the same filter would capture by covering that
// much of the map (units) or by being that close to any line (junctions).
//
// Lift is Capture/Baseline and is the only number worth quoting: a capture of
// 53% is impressive until the baseline is 50%. Control is the same measurement
// run on the OTHER commodity's sites - it separates "this rule finds gold" from
// "this rule finds mines", which in a country with 914 visited artisanal pits
// is a real and easy confusion.
type geoAffinityScore struct {
	Commodity string  `json:"commodity"`
	Kind      string  `json:"kind"` // "unit" or "junction"
	MinWeight int     `json:"min_weight"`
	Capture   float64 `json:"capture"`
	Baseline  float64 `json:"baseline"`
	Lift      float64 `json:"lift"`
	Control   float64 `json:"control"` // lift against the other commodity's sites, 0 = not measured
	N         int     `json:"n"`
	Scope     string  `json:"scope"`
	Verdict   string  `json:"verdict"` // "concentrates" | "no better than area" | "unmeasured"
}

// geoAffinityEval names the measurement itself, so a number on screen can be
// traced to the run that produced it without a code search.
var geoAffinityEval = map[string]any{
	"dataset":  "IPIS artisanal mining site visits, CAR (2019 survey), 914 visited sites",
	"script":   "scripts/geomaps/eval_affinity.py",
	"out":      "data/eval/geo_affinity_car.json",
	"measured": "2026-08-13",
	"sheet":    "car",
	"near_km":  5,
	// The one sentence that stops a lift being over-read. Both baselines are
	// area-like, and the sites are a SURVEY footprint: IPIS visits where IPIS
	// can go, which is not a random sample of the ground.
	"caveat": "One sheet, one country, one survey's reachable sites - a lift here is " +
		"evidence the rule is not noise, not a probability of finding anything.",
}

// geoAffinityScores are the measured numbers, verbatim from the eval run.
//
// The shape of the result, stated once so nobody has to reconstruct it from
// the table: ON THIS SHEET THE JUNCTIONS CARRY THE SIGNAL AND THE UNITS DO NOT.
// Gold-graded contacts hold 53% of gold workings within 5 km against a 23%
// baseline (2.3x, and 2.3x again against diamond workings, so it is gold the
// lines are finding and not mines) - while the gold-graded UNITS hold 24% of
// them on 38% of the map, i.e. a reader who isolates "rocks that can host
// gold" on CAR is looking at ground *less* likely to be worked than the sheet
// as a whole. The reason is visible in the eval's own per-class table: the
// workings sit on Zeta (gneiss) and gamma_h (heterogeneous granite), the two
// biggest units the affinity table grades ZERO for gold, because "gneiss" and
// "syncinematic granite" are not the words a textbook uses for a gold host.
// The junction rules recover exactly that ground from the other side: it is
// the CONTACT between them that the model grades, and that is where the pits
// are.
//
// Diamond is the mirror image and is the reason this table has two commodities
// rather than one: on CAR the diamond UNITS work (1.41x at classic, the
// alluvium and the Carnot sandstone) and the diamond junctions do not (0.33x,
// 4 graded lines out of 113). A single-commodity eval would have concluded
// "junctions good, units bad" about the model as a whole; the model is simply
// right about different things in different places.
var geoAffinityScores = []geoAffinityScore{
	{"gold", "junction", 1, 0.559, 0.257, 2.17, 2.20, 494, "CAR sheet, IPIS 2019", "concentrates"},
	{"gold", "junction", 2, 0.528, 0.228, 2.32, 2.33, 494, "CAR sheet, IPIS 2019", "concentrates"},
	{"gold", "junction", 3, 0.204, 0.056, 3.63, 0, 494, "CAR sheet, IPIS 2019", "concentrates"},
	{"gold", "unit", 1, 0.237, 0.377, 0.63, 0, 675, "CAR sheet, IPIS 2019", "no better than area"},
	{"gold", "unit", 2, 0.101, 0.264, 0.38, 0, 675, "CAR sheet, IPIS 2019", "no better than area"},
	// gold unit w>=3 is deliberately ABSENT, not zero: no CAR class is graded
	// classic for gold, so the filter selects 0% of the map and the ratio has
	// no denominator. "Nothing to measure" and "measured, nothing found" are
	// different statements, and the second one is false here.
	{"diamond", "unit", 1, 0.376, 0.382, 0.98, 0, 362, "CAR sheet, IPIS 2019", "no better than area"},
	{"diamond", "unit", 2, 0.376, 0.334, 1.13, 0, 362, "CAR sheet, IPIS 2019", "concentrates"},
	{"diamond", "unit", 3, 0.340, 0.241, 1.41, 0, 362, "CAR sheet, IPIS 2019", "concentrates"},
	{"diamond", "junction", 1, 0.022, 0.067, 0.33, 1.09, 181, "CAR sheet, IPIS 2019", "no better than area"},
	{"diamond", "junction", 2, 0.022, 0.053, 0.41, 1.09, 181, "CAR sheet, IPIS 2019", "no better than area"},
}

// geoAffinityScoreFor is the score a surface should quote for one claim: the
// measurement at the highest floor at or below what the reader is looking at,
// so a reader on "any" is not shown the flattering "classic" number.
//
// Returns nil when the pair was never measured, and the caller must then say
// "unmeasured" rather than nothing: a commodity with no score beside one that
// has a 2.3x is otherwise read as "worse", when in fact it is "unknown". Eight
// of the ten commodities are in that state, because we hold an occurrence list
// for two.
func geoAffinityScoreFor(commodity, kind string, minWeight int) *geoAffinityScore {
	var best *geoAffinityScore
	for i := range geoAffinityScores {
		s := &geoAffinityScores[i]
		if s.Commodity != commodity || s.Kind != kind || s.MinWeight > minWeight {
			continue
		}
		if best == nil || s.MinWeight > best.MinWeight {
			best = s
		}
	}
	return best
}

// geoAffinityScoresJSON ships the table in the shared legend, next to the rules
// it scores. Same reason the contact rules ride there: it is one statement
// about the model, not a property of a sheet, and a client that indexes it by
// (commodity, kind) can annotate every grade it draws.
func geoAffinityScoresJSON() map[string]any {
	rows := make([]map[string]any, 0, len(geoAffinityScores))
	for _, s := range geoAffinityScores {
		row := map[string]any{
			"commodity": s.Commodity, "kind": s.Kind, "min_weight": s.MinWeight,
			"capture": s.Capture, "baseline": s.Baseline, "lift": s.Lift,
			"n": s.N, "scope": s.Scope, "verdict": s.Verdict,
		}
		if s.Control > 0 {
			row["control"] = s.Control
		}
		rows = append(rows, row)
	}
	return map[string]any{"eval": geoAffinityEval, "scores": rows}
}
