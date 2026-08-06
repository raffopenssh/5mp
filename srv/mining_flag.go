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
// observed in GHSL built-up data. Those ~2,562 rows are detector output wearing
// a settlement's clothes -- they must not be served as settlements at all.
// Identified by the note prefix RegisterMiningCandidate prepends.
func scannerInjectedSettlement(narrative string) bool {
	if MiningEnabled {
		return false
	}
	return strings.HasPrefix(narrative, "[Pit detection ") ||
		strings.HasPrefix(narrative, "[Turbidity ")
}

// scannerInjectedSQLFilter excludes those rows in SQL (leading AND).
func scannerInjectedSQLFilter(col string) string {
	if MiningEnabled {
		return ""
	}
	return " AND COALESCE(" + col + ",'') NOT LIKE '[Pit detection %'" +
		" AND COALESCE(" + col + ",'') NOT LIKE '[Turbidity %'"
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
