package srv

// Point-in-area test for the GeoPackage export.
//
// Raw fire detections are keyed by coordinate, so the only indexed way to fetch
// them is the area's bounding box. For a compact park that is close enough to
// the truth; for XSA_Study_Area it is not — the polygon holds 3.18M detections
// and its bbox holds 6.9M, so more than half of a file named after the area
// lies outside it. Dropping them would be wrong too (the fire narratives
// deliberately keep groups up to 20 km outside as context, and a fire heading
// towards a boundary is the thing people look for), so they are kept and
// LABELLED: `in_area` is 1 inside the polygon, 0 in the surrounding box.
//
// The naive test is O(points x vertices) — 7M x 2,000 — so edges are bucketed
// into latitude bands once and each point tests only the few edges crossing its
// own band. Ray casting, so it is exact for concave rings and holes alike; a
// hole reverses parity exactly as it should.

import (
	"encoding/json"
	"math"
)

type areaHitTest struct {
	minx, miny, maxx, maxy float64
	bandY0, bandH          float64
	bands                  [][]int32 // band -> edge indices
	x1, y1, x2, y2         []float64
	ok                     bool
}

const areaHitBands = 512

// newAreaHitTest builds the index from a boundary GeoJSON. Returns a test whose
// Contains() is always true when the geometry could not be read — an export
// must not silently mark every row as outside because a polygon failed to
// parse.
func newAreaHitTest(boundary string) *areaHitTest {
	t := &areaHitTest{minx: math.Inf(1), miny: math.Inf(1), maxx: math.Inf(-1), maxy: math.Inf(-1)}
	var g gpkgGeom
	if boundary == "" || json.Unmarshal([]byte(boundary), &g) != nil {
		return t
	}
	if g.Geometry != nil {
		g = *g.Geometry
	}
	var rings [][][2]float64
	switch g.Type {
	case "Polygon":
		rings = decodeRings(g.Coordinates)
	case "MultiPolygon":
		var raw []json.RawMessage
		if json.Unmarshal(g.Coordinates, &raw) != nil {
			return t
		}
		for _, r := range raw {
			rings = append(rings, decodeRings(r)...)
		}
	default:
		return t
	}
	if len(rings) == 0 {
		return t
	}
	for _, ring := range rings {
		for i := 0; i < len(ring); i++ {
			a, b := ring[i], ring[(i+1)%len(ring)]
			if a[1] == b[1] {
				continue // horizontal edges never cross a horizontal ray
			}
			t.x1 = append(t.x1, a[0])
			t.y1 = append(t.y1, a[1])
			t.x2 = append(t.x2, b[0])
			t.y2 = append(t.y2, b[1])
			t.minx, t.maxx = math.Min(t.minx, math.Min(a[0], b[0])), math.Max(t.maxx, math.Max(a[0], b[0]))
			t.miny, t.maxy = math.Min(t.miny, math.Min(a[1], b[1])), math.Max(t.maxy, math.Max(a[1], b[1]))
		}
	}
	if len(t.x1) == 0 || t.maxy <= t.miny {
		return t
	}
	t.bandY0 = t.miny
	t.bandH = (t.maxy - t.miny) / areaHitBands
	t.bands = make([][]int32, areaHitBands)
	for i := range t.x1 {
		lo, hi := math.Min(t.y1[i], t.y2[i]), math.Max(t.y1[i], t.y2[i])
		b0 := int((lo - t.bandY0) / t.bandH)
		b1 := int((hi - t.bandY0) / t.bandH)
		if b0 < 0 {
			b0 = 0
		}
		if b1 >= areaHitBands {
			b1 = areaHitBands - 1
		}
		for b := b0; b <= b1; b++ {
			t.bands[b] = append(t.bands[b], int32(i))
		}
	}
	t.ok = true
	return t
}

// Contains reports whether (lon, lat) is inside the area.
func (t *areaHitTest) Contains(lon, lat float64) bool {
	if !t.ok {
		return true
	}
	if lon < t.minx || lon > t.maxx || lat < t.miny || lat > t.maxy {
		return false
	}
	b := int((lat - t.bandY0) / t.bandH)
	if b < 0 {
		b = 0
	} else if b >= areaHitBands {
		b = areaHitBands - 1
	}
	inside := false
	for _, i := range t.bands[b] {
		y1, y2 := t.y1[i], t.y2[i]
		if (y1 > lat) == (y2 > lat) {
			continue
		}
		if lon < (t.x2[i]-t.x1[i])*(lat-y1)/(y2-y1)+t.x1[i] {
			inside = !inside
		}
	}
	return inside
}
