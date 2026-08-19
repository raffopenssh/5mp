package srv

import "testing"

// The render switch is a claim about the DRAWING, so it is tested with the real
// feature scales measured in the database (XSA_Study_Area, 2026-08-16):
// deforestation patches ~0.0017 deg across, settlement footprints ~0.0021,
// fire trajectories ~0.73. Hard-coding a render decision per feature type was
// the bug (a count budget let two polygon layers in one view disagree), so the
// invariant under test is: at one bbox, two layers of similar-sized polygons
// get the SAME answer, whatever their counts.
func cands(n int, span float64) []bboxCand {
	out := make([]bboxCand, n)
	for i := range out {
		out[i] = bboxCand{area: span * span}
	}
	return out
}

func TestSubPixelShapesAgreesAcrossLayers(t *testing.T) {
	wide := [4]float64{26.8, 5.0, 27.9, 5.9}    // ~1.1 deg -> 0.00079 deg/px
	tight := [4]float64{27.25, 5.4, 27.35, 5.5} // ~0.1 deg -> 0.00007 deg/px

	// Same bbox, wildly different counts, near-identical feature sizes:
	// the answer must not depend on the count.
	if a, b := subPixelShapes(cands(8259, 0.0017), wide), subPixelShapes(cands(996, 0.0021), wide); a != b {
		t.Fatalf("wide view: deforestation=%v settlement=%v — two layers, two pictures", a, b)
	}
	if !subPixelShapes(cands(8259, 0.0017), wide) {
		t.Error("0.0017 deg patch at 0.00079 deg/px is ~2 px: must draw as dots")
	}
	if subPixelShapes(cands(610, 0.0017), tight) {
		t.Error("same patch at 0.00007 deg/px is ~24 px: must draw as shapes")
	}
	if subPixelShapes(nil, wide) {
		t.Error("no candidates must not claim sub-pixel")
	}
}

func TestInkCrowdedKeepsChordsWhileDense(t *testing.T) {
	view := [4]float64{27.25, 5.4, 27.35, 5.5}
	// 1,231 trajectories of ~0.1 deg in a 0.1 deg view: every path crosses the
	// whole screen, so the full geometry adds vertices and no visible shape.
	if !inkCrowded(cands(1231, 0.1), view) {
		t.Error("dense path field must stay on chords")
	}
	// Twenty of them is a followable picture and must promote.
	if inkCrowded(cands(20, 0.1), view) {
		t.Error("sparse path field must promote to full geometry")
	}
	if inkCrowded(nil, view) {
		t.Error("no candidates must not claim crowding")
	}
}
