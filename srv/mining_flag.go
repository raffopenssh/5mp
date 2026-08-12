package srv

import (
	"regexp"
	"strings"
)

// MiningEnabled gates every mining/turbidity surface in the app.
//
// Set to false on 2026-08-06. Rationale in docs/MINING_FINDINGS_2026-08.md §10:
// neither the hand-picked spectral indices (§8, AUC 0.45-0.56 vs confusers) nor
// the Amazon Mining Watch CNN (§9.5, AUC 0.781 balanced) can be operated at the
// base rate a real scan implies -- ~77k patches per park means precision ~0.001
// at the only operating points we can measure, and we would need FPR <= 2.6e-4,
// which is 154x below what a 25-negative eval can even resolve.
//
// Nothing is deleted: data/turbidity/*.json, data/mining_pits/*.json, the 4,267
// mining_alert notifications and the 1,572 settlements labelled 'mining' all
// remain. Flipping this constant back to true restores every surface.
//
// The basin layer (park_basins, /api/parks/{id}/basin) was built as mining
// infrastructure but validates on its own terms and is NOT gated by this.
const MiningEnabled = false

// NOTE: the 'mining' settlement classification itself is NOT retired. It is
// inferred from river proximity, deforestation pattern, low fire activity and
// distance from villages -- ordinary contextual reasoning, independent of the
// spectral work. What is retired is the turbidity/pit *evidence* that used to
// be folded into that score and stated in the narrative.

// publicSettlementClass exists so callers need not know the policy. The 'mining'
// label is river-proximity inference and is served as-is; this is deliberately
// identity, and is the seam to change if that ever stops being true.
func publicSettlementClass(class string) string { return class }

// miningNotifSQLFilter returns a SQL fragment (with leading AND) excluding
// mining notifications, or "" when mining is enabled.
func miningNotifSQLFilter() string {
	if MiningEnabled {
		return ""
	}
	return " AND notification_type NOT IN ('mining_alert','turbidity_scan_success','turbidity_scan_failed')"
}

// scannerInjectedSettlement reports whether a park_settlements row was created
// by the retired pit/turbidity scanner (RegisterMiningCandidate) rather than
// observed in GHSL built-up data. Those rows are detector output wearing a
// settlement's clothes -- they must not be served as settlements at all.
//
// Identified by the note prefix RegisterMiningCandidate prepends. See
// settlementSourceSQL below for why the note alone is not enough, and
// scannerInjectedRow for the predicate that also knows the row's origin.
func scannerInjectedSettlement(narrative string) bool {
	if MiningEnabled {
		return false
	}
	return strings.HasPrefix(narrative, "[Pit detection ") ||
		strings.HasPrefix(narrative, "[Turbidity ")
}

// scannerInjectedRow is scannerInjectedSettlement plus the origin test: a row
// with no polygon_ids was never observed in GHSL built-up data, whatever its
// narrative now says. Use this wherever both columns are to hand.
func scannerInjectedRow(narrative, polygonIDs string) bool {
	if MiningEnabled {
		return false
	}
	return scannerInjectedSettlement(narrative) || strings.TrimSpace(polygonIDs) == ""
}

// scannerInjectedSQLFilter excludes those rows in SQL (leading AND).
func scannerInjectedSQLFilter(col string) string {
	if MiningEnabled {
		return ""
	}
	return " AND COALESCE(" + col + ",'') NOT LIKE '[Pit detection %'" +
		" AND COALESCE(" + col + ",'') NOT LIKE '[Turbidity %'"
}

// ---------------------------------------------------------------------------
// PROVENANCE: a settlement is observed built-up ground, or it is not a
// settlement.
//
// scannerInjectedSQLFilter above matches on the NOTE the pit/turbidity scanner
// prepends -- and a note is not provenance, it is a string that something else
// may rewrite. Something else did. /api/refresh-park runs
// ClassifyParkSettlementsForce nightly, which regenerates `narrative` from
// scratch; it skips rows whose note begins "[Turbidity alert" and skips NOTHING
// for the 2,457 rows beginning "[Pit detection ". So every night the force
// pass rewrote a pit detection's narrative into ordinary classifier prose --
// "Agricultural settlement 16km north of Safari Ht Chinko" -- and the row
// walked straight through the filter that existed to stop it. 495 rows had
// already been laundered this way, including all 79 in CMR_Nki, the park this
// project's own test list calls "pristine, 0 settlements".
//
// So the test is the row's ORIGIN, which cannot be rewritten by prose:
//
//   polygon_ids  the feature_geometries settlement footprints this cluster was
//                built from. Every GHSL-derived row has them
//                (scripts/process_settlement_polygons.py writes the polygons,
//                rebuild_events_enhanced.py clusters them and records the ids).
//                RegisterMiningCandidate, which inserts a bare lat/lon, cannot
//                have them: there is no observed built-up polygon to point at.
//
// 11,485 rows carry footprints; 3,019 do not, and every one of those 3,019 was
// inserted by the retired scanner (verified against data/mining_pits/*.json and
// data/turbidity/*.json: 2,483 still wear the note, 495 were laundered, 4 are
// pit-adjacent rows whose note was lost the same way).
//
// The note filter is KEPT as well, not replaced. They fail in opposite
// directions -- a laundered note with footprints would slip past the note
// check, a legitimate cluster whose polygon_ids were dropped by a future
// refactor would slip past this one -- and a settlement should have to satisfy
// both to be served as one.
//
// When MiningEnabled flips back to true this relaxes with everything else: the
// rows are still there, and the constant is still the only switch.
func settlementSourceSQL(col string) string {
	if MiningEnabled {
		return ""
	}
	return " AND COALESCE(" + col + ",'') != ''"
}

// settlementFilterSQL is the pair, and is what call sites should use: origin
// AND note. Both columns are named because the settlement queries in this app
// are variously unaliased, `s.`-aliased, or joined.
func settlementFilterSQL(narrativeCol, polygonCol string) string {
	return scannerInjectedSQLFilter(narrativeCol) + settlementSourceSQL(polygonCol)
}

// sentenceTail matches the rest of a sentence up to its terminating period.
// [^.]* alone is wrong here: these narratives are full of decimals ("2.1km
// away", "~67km"), so a naive matcher stops mid-number and leaves debris.
// This allows any period that is immediately followed by a digit.
const sentenceTail = `(?:[^.]|\.[0-9])*\.`

// turbiditySentence matches the evidence sentences the mining narrative used to
// append from turbidity/pit scans. The river-proximity reasoning around them
// stays; only the retired spectral evidence is stripped.
var turbiditySentence = regexp.MustCompile(
	`\s*(?:Sentinel-2 shows a sediment plume` + sentenceTail +
		`|\[Pit detection [^\]]*\]` + sentenceTail +
		`|\[Turbidity [^\]]*\]` + sentenceTail + `)`)

// publicSettlementNarrative strips retired turbidity/pit evidence from a stored
// narrative. The "Possible mining site ... proximity to X suggests alluvial
// extraction" reasoning is river-geometry inference and is kept.
func publicSettlementNarrative(class, narrative string) string {
	if MiningEnabled || narrative == "" {
		return narrative
	}
	return strings.TrimSpace(turbiditySentence.ReplaceAllString(narrative, ""))
}
