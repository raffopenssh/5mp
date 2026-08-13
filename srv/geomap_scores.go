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

import (
	"fmt"
	"strings"
)

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

	// WHERE it was measured, as a sheet id — because a score is only a claim
	// about ground it was measured on, and the reader is looking at a
	// VIEWPORT, not at a dataset. A 2.32x measured on the CAR basement is not
	// a statement about the Tanzanian craton, and a panel that prints it over
	// Tanzania has invented a number.
	//
	// A sheet id, not a bounding box: the extent is the sheet's own
	// `bounds` (already in /api/geomap and already used to bound the tile
	// source), so it cannot drift from the data the way a typed box would —
	// invariant 2 applied to an extent. The client intersects it with
	// map.getBounds(); the USER never sees the sheet id, only the place.
	ScopeSheet string `json:"scope_sheet"`

	// EvidenceID identifies the OCCURRENCE LIST, not merely its sheet
	// ("car/ipis", "car/tearline"). Three lists reach the CAR and they
	// disagree; without this key a consumer picks between them by row order
	// and calls the winner consensus.
	EvidenceID string `json:"evidence_id"`

	// StratumOf/Stratum mark a row as one half of another list, split by a
	// property of its own sites. Set means: not independent evidence, do not
	// count it in an agreement.
	StratumOf string `json:"stratum_of,omitempty"`
	Stratum   string `json:"stratum,omitempty"`

	// The caveat of the list this row came from, carried per row so a number
	// cannot be copied to a surface without it.
	Caveat string `json:"caveat"`
}

// geoTruthSet is one occurrence list: what it is, and the sentence that must
// travel with any number taken from it.
//
// A truth set is not interchangeable with another. IPIS visited what a surveyor
// could drive to; Tearline traced imagery inside eight mining permits; Crisis
// Tracker holds mines that were attacked in the east of the country; the GST
// register was compiled by the same programme that drew the units it scores.
// The caveat is not decoration - it is the difference between the numbers.
type geoTruthSet struct {
	Place  string `json:"place"`
	Caveat string `json:"caveat"`
	// StratumOf/Stratum name a SPLIT OF ANOTHER LIST rather than a list. Two
	// strata of one survey share a footprint and a definition of "a mine": they
	// show that a pooled number depends on the stratifier, and they can never
	// corroborate each other. A surface that counts them as independent
	// agreements lets one survey vote three times.
	StratumOf string `json:"stratum_of,omitempty"`
	Stratum   string `json:"stratum,omitempty"`
	// The ground its random points came from, in words: "the mapped sheet" and
	// "the 3 mapped Lobaye Invest permits" are different denominators, so two
	// lifts are comparable only when this line matches.
	Baseline string `json:"baseline,omitempty"`
}

// geoAffinityEval names the RUN. The measurements and their caveats are
// generated into geomap_scores_table.go by scripts/geomaps/gen_scores_go.py
// from the eval's JSON: one row per truth set, never an average across lists
// that sampled different ground.
var geoAffinityEval = map[string]any{
	"script":    "scripts/geomaps/eval_affinity.py",
	"generator": "scripts/geomaps/gen_scores_go.py",
	"strata":    "scripts/eval_reach_strata.py",
	"out":       "data/eval/geo_affinity.json",
	"measured":  "2026-08-13",
	"near_km":   geoAffinityNearKm,
	"min_sites": geoAffinityMinSites,
}

// geoAffinityEvidence is every measurement that speaks to one claim, plus what
// they add up to. A caller must render the WHOLE of this, not pick a row.
//
// The CAR is why this type exists. Three independent lists reach that sheet and
// they do not say the same thing: IPIS's gold junctions score 2.4x, Tearline's
// permit census scores 0.0x on the same claim, and Crisis Tracker cannot score
// it at all (5 of 41 sites name a mineral). Any single row is defensible in
// isolation and misleading on screen.
type geoAffinityEvidence struct {
	// Scores at the floor the reader is looking at, one per truth set, in the
	// table's order. Never merged.
	Scores []geoAffinityScore `json:"scores"`
	// Verdict over all of them: "concentrates" when every list puts the claim
	// above 1.0, "no better than area" when every list puts it at or below,
	// "mixed" when they straddle, "unmeasured" when none scored it. MIXED IS A
	// RESULT, not a missing value, and it must reach the reader as a word: the
	// alternative is a UI that shows whichever list it happened to index first.
	Verdict string `json:"verdict"`
	// TooFew records lists that hold this commodity but under the floor, with
	// the count. "We looked and there were four" is a different statement from
	// "nobody looked", and only this field can tell them apart.
	TooFew map[string]int `json:"too_few,omitempty"`
	// Spread is the widest ratio between two strata of ONE survey for this
	// claim, when it was measured. >1 means the pooled lift moves with
	// something that is not the rock: on CAR gold units it is 1.55x between
	// mines where the surveyors recorded an armed actor and mines where they
	// did not (capture p=0.0033). A surface quoting a pooled lift with a spread
	// like that beside it and not saying so is quoting an unnamed variable.
	Spread float64 `json:"spread,omitempty"`
}

// geoAffinityEvidenceFor collects every list's measurement of one claim at the
// highest floor at or below the reader's, so nobody is shown the flattering
// "classic" number for an "any host" question.
//
// Strata are excluded from Scores and folded into Spread instead: they are not
// more evidence, they are a statement about the pooled number's stability.
func geoAffinityEvidenceFor(sheet, commodity, kind string, minWeight int) geoAffinityEvidence {
	out := geoAffinityEvidence{Verdict: "unmeasured"}
	best := map[string]*geoAffinityScore{}
	for i := range geoAffinityScores {
		s := &geoAffinityScores[i]
		if s.Commodity != commodity || s.Kind != kind || s.MinWeight > minWeight {
			continue
		}
		if sheet != "" && s.ScopeSheet != sheet {
			continue
		}
		if s.StratumOf != "" {
			continue
		}
		if b := best[s.EvidenceID]; b == nil || s.MinWeight > b.MinWeight {
			best[s.EvidenceID] = s
		}
	}
	// The table's own order, so two surfaces list the evidence identically.
	seen := map[string]bool{}
	above, below := 0, 0
	for i := range geoAffinityScores {
		id := geoAffinityScores[i].EvidenceID
		if seen[id] || best[id] == nil {
			continue
		}
		seen[id] = true
		s := *best[id]
		out.Scores = append(out.Scores, s)
		if s.Lift > 1.0 {
			above++
		} else {
			below++
		}
	}
	switch {
	case above > 0 && below > 0:
		out.Verdict = "mixed"
	case above > 0:
		out.Verdict = "concentrates"
	case below > 0:
		out.Verdict = "no better than area"
	}
	for eid, per := range geoAffinityTooFew {
		if sheet != "" && !strings.HasPrefix(eid, sheet+"/") {
			continue
		}
		if n, ok := per[commodity]; ok {
			if out.TooFew == nil {
				out.TooFew = map[string]int{}
			}
			out.TooFew[eid] = n
		}
	}
	// The spread is keyed by the eval's plural kind ("units"/"junctions"),
	// because that is what the eval calls its two sections; the score rows use
	// the singular. Translated here rather than in the generator, so the
	// generated file keeps saying what the eval said.
	if per, ok := geoAffinityStrataSpread[sheet]; ok {
		key := fmt.Sprintf("%s|%ss|w%d", commodity, kind, minWeight)
		if v, ok := per[key]; ok {
			out.Spread = v
		}
	}
	return out
}

// geoAffinityScoreFor is the single-row accessor, kept for surfaces that
// annotate one grade in one badge.
//
// ⚠️ It returns the FIRST list's measurement and therefore cannot express
// disagreement. On the CAR that is a real loss - the gold junctions are 2.4x to
// IPIS and 0.0x to Tearline - so any surface where the reader makes a decision
// must use geoAffinityEvidenceFor and print the "mixed" verdict.
func geoAffinityScoreFor(commodity, kind string, minWeight int) *geoAffinityScore {
	var best *geoAffinityScore
	for i := range geoAffinityScores {
		s := &geoAffinityScores[i]
		if s.Commodity != commodity || s.Kind != kind || s.MinWeight > minWeight {
			continue
		}
		if s.StratumOf != "" {
			continue
		}
		if best == nil || s.MinWeight > best.MinWeight {
			best = s
		}
	}
	return best
}

// geoAffinityScoresJSON ships every measurement in the shared legend, beside
// the rules it scores. The client forms the verdict for its viewport: all lists
// above 1.0 is a verdict, lists straddling 1.0 is "mixed", and a stratum is
// marked so it is never counted as a second opinion.
func geoAffinityScoresJSON() map[string]any {
	rows := make([]map[string]any, 0, len(geoAffinityScores))
	for _, s := range geoAffinityScores {
		row := map[string]any{
			"commodity": s.Commodity, "kind": s.Kind, "min_weight": s.MinWeight,
			"capture": s.Capture, "baseline": s.Baseline, "lift": s.Lift,
			"n": s.N, "scope": s.Scope, "caveat": s.Caveat,
			"verdict": s.Verdict, "scope_sheet": s.ScopeSheet,
			"evidence_id": s.EvidenceID,
		}
		if s.Control > 0 {
			row["control"] = s.Control
		}
		if s.StratumOf != "" {
			row["stratum_of"], row["stratum"] = s.StratumOf, s.Stratum
		}
		rows = append(rows, row)
	}
	return map[string]any{
		"eval": geoAffinityEval, "truths": geoAffinityTruth,
		"too_few": geoAffinityTooFew, "strata_spread": geoAffinityStrataSpread,
		"scores": rows,
	}
}
