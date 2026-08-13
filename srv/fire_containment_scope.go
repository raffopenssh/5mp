package srv

import "strings"

// CONTAINMENT IS A STATEMENT ABOUT A BOUNDARY SOMEBODY DEFENDS.
//
// `park_group_infractions` records, per year, how many fire-group trajectories
// were "stopped inside" the analysis polygon and how many "transited" it, and
// the popup renders the ratio as **"Avg response rate 100 %"** next to
// "Stopped inside 38,725 (100 %)". For a park that phrase means rangers
// intervened at a boundary that exists on the ground and in law.
//
// For an AOI it means nothing at all. An AOI is a polygon a user drew around
// the data they wanted, so every trajectory built for it is "contained" by
// construction — XSA_Study_Area 2024: total 16,444, stopped_inside 16,444,
// transited 0, every trajectory `position: "contained"`, every `cross_border`
// false. The numbers are structurally true and the sentence they produce is
// false: it reads as a perfect protection record for an area nobody patrols
// (docs/AOI_STRUCTURAL_FIXES.md F7).
//
// So the fix is not a different number, it is the ABSENCE of one.
// `containment_meaningful` is false for an AOI and the containment fields are
// zeroed, which — because they are all `omitempty` — removes them from the JSON
// entirely. A consumer that checks the flag says "not applicable"; a consumer
// that does not gets no field to misread, rather than a zero that looks like a
// measurement of nothing stopped.
//
// Same shape as AGENTS.md invariant 12: a quantity that was not measured, and a
// quantity that cannot be measured for this kind of object, must not arrive as
// a number. And the same shape as invariant 6: an AOI is not a park, so a
// park-shaped column does not automatically carry a park's meaning across.
func suppressContainmentForAOI(id string, n *FireNarrative) {
	if n == nil {
		return
	}
	if !IsAOIID(id) {
		n.ContainmentMeaningful = true
		return
	}
	n.ContainmentMeaningful = false
	n.ResponseRate = 0
	n.StoppedInsideGroups = 0
	n.TransitedGroups = 0
	// cross_border is computed against the ANALYSIS polygon, not a national
	// boundary, so for an AOI it says "this trajectory left the box the user
	// drew" while reading as "this fire crossed an international border".
	n.CrossBorderGroups = 0
	n.OutsideParkGroups = 0
	if n.Trend != nil {
		n.Trend.AvgResponseRate = 0
		n.Trend.BestYear = 0
		n.Trend.BestYearRate = 0
		for i := range n.Trend.Years {
			n.Trend.Years[i].ResponseRate = 0
			n.Trend.Years[i].StoppedInside = 0
			n.Trend.Years[i].Transited = 0
		}
		n.Trend.Narrative = stripResponseRateSentences(n.Trend.Narrative)
	}
	n.Summary = stripResponseRateSentences(n.Summary)
	for i := range n.Narratives {
		n.Narratives[i].CrossBorder = false
	}
}

// stripResponseRateSentences removes the sentences a generated narrative builds
// out of the containment ratio. The narratives are assembled from fixed
// templates (see generateFireNarrative / analyzeFireTrend), so this matches the
// templates rather than trying to understand prose — and it drops a whole
// sentence, because half a sentence is a worse artefact than the number was.
func stripResponseRateSentences(text string) string {
	if text == "" {
		return ""
	}
	var kept []string
	for _, sentence := range splitSentences(text) {
		l := strings.ToLower(sentence)
		if strings.Contains(l, "response rate") ||
			strings.Contains(l, "stopped inside") ||
			strings.Contains(l, "transited") {
			continue
		}
		kept = append(kept, sentence)
	}
	return strings.TrimSpace(strings.Join(kept, " "))
}

// splitSentences keeps the terminator on each piece so rejoining is lossless
// for the sentences that survive.
func splitSentences(text string) []string {
	var out []string
	start := 0
	for i := 0; i < len(text); i++ {
		if text[i] != '.' && text[i] != '!' && text[i] != '?' {
			continue
		}
		// A decimal point is not a sentence end ("38.5%", "1.2 km").
		if text[i] == '.' && i+1 < len(text) && text[i+1] >= '0' && text[i+1] <= '9' {
			continue
		}
		out = append(out, strings.TrimSpace(text[start:i+1]))
		start = i + 1
	}
	if rest := strings.TrimSpace(text[start:]); rest != "" {
		out = append(out, rest)
	}
	return out
}
