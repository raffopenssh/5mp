package srv

import (
	"math"
	"testing"
)

// The failure this pins: every settlement carries stat_value = 0 and a
// near-tied area, so the tie-break falls through to the id — which is assigned
// in ingest order, i.e. geographically. A selector that tops its budget up from
// a flat "best of the rest" list therefore returns one contiguous ingest block
// (a stripe along one edge of the view) while the rest of the map goes sparse.
// The user reads that as "the data is wrong", not as "this is a sample".
func TestSpreadCollectorIsSpatiallyUniformWithTiedStats(t *testing.T) {
	bbox := [4]float64{0, 0, 10, 10}
	const limit = 400
	col := newSpreadCollector(limit, bbox, true)

	// 10,000 features on a regular lattice, all with identical stat/area, with
	// ids assigned in scan order (row-major) — the shape real ingest has.
	var id int64
	for gy := 0; gy < 100; gy++ {
		for gx := 0; gx < 100; gx++ {
			id++
			col.add(bboxCand{id: id, cx: float64(gx) * 0.1, cy: float64(gy) * 0.1})
		}
	}
	got := col.result()
	if len(got) != limit {
		t.Fatalf("budget not spent: got %d of %d", len(got), limit)
	}
	if col.total != 10000 {
		t.Fatalf("total = %d, want 10000", col.total)
	}

	// Bucket the answer into a 5x5 grid over the bbox. A uniform input must
	// produce a roughly uniform answer; the old collector put ~all of the
	// top-up in the first rows.
	var buckets [25]int
	for _, c := range got {
		bx := int(c.cx / 2)
		by := int(c.cy / 2)
		if bx > 4 {
			bx = 4
		}
		if by > 4 {
			by = 4
		}
		buckets[by*5+bx]++
	}
	want := float64(limit) / 25
	for i, n := range buckets {
		if math.Abs(float64(n)-want) > want*0.6 {
			t.Errorf("bucket %d holds %d features, want ~%.0f — the selection is clumped", i, n, want)
		}
	}
}

// Determinism: the same rows in the same order must give the same answer, or a
// share link stops reproducing the picture it was made from.
func TestSpreadCollectorIsDeterministic(t *testing.T) {
	bbox := [4]float64{0, 0, 10, 10}
	run := func() []int64 {
		col := newSpreadCollector(300, bbox, true)
		var id int64
		for gy := 0; gy < 80; gy++ {
			for gx := 0; gx < 80; gx++ {
				id++
				col.add(bboxCand{id: id, cx: float64(gx) * 0.125, cy: float64(gy) * 0.125})
			}
		}
		out := []int64{}
		for _, c := range col.result() {
			out = append(out, c.id)
		}
		return out
	}
	a, b := run(), run()
	if len(a) != len(b) {
		t.Fatalf("lengths differ: %d vs %d", len(a), len(b))
	}
	for i := range a {
		if a[i] != b[i] {
			t.Fatalf("answer differs at %d: %d vs %d", i, a[i], b[i])
		}
	}
}

// A cell holding several features contributes several before a sparse cell
// contributes a second: density variation must come from the data.
func TestSpreadCollectorKeepsRealDensity(t *testing.T) {
	bbox := [4]float64{0, 0, 10, 10}
	col := newSpreadCollector(300, bbox, true)
	var id int64
	// A dense cluster in one corner...
	for i := 0; i < 500; i++ {
		id++
		col.add(bboxCand{id: id, cx: 0.5 + float64(i%20)*0.01, cy: 0.5})
	}
	// ...and a thin scatter everywhere else.
	for gy := 0; gy < 10; gy++ {
		for gx := 0; gx < 10; gx++ {
			id++
			col.add(bboxCand{id: id, cx: float64(gx) + 0.5, cy: float64(gy) + 0.5})
		}
	}
	dense := 0
	for _, c := range col.result() {
		if c.cx < 1 && c.cy < 1 {
			dense++
		}
	}
	if dense < 2 {
		t.Errorf("the dense corner contributed %d features; a cluster must read as a cluster", dense)
	}
	// 500 of the 600 features here are in that one cell, so it is entitled to
	// most of the budget — what it must not do is take ALL of it and leave the
	// scatter unrepresented (the top-up cap).
	if dense > 200 {
		t.Errorf("the dense corner took %d of 300 — one region must not monopolise the budget", dense)
	}
}

// fnvID must not preserve id order: "lowest id" is a corner of the map, which
// is the whole reason the sample is hashed rather than sorted.
func TestFNVIDScramblesOrder(t *testing.T) {
	inversions := 0
	for i := int64(1); i < 1000; i++ {
		if fnvID(i) > fnvID(i+1) {
			inversions++
		}
	}
	if inversions < 400 || inversions > 600 {
		t.Errorf("fnvID inversions = %d of 999, want ~500 (it is not scrambling)", inversions)
	}
}
