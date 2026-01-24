// Package areas provides protected area management for conservation patrol tracking.
package areas

import (
	"encoding/json"
	"math"
	"os"
)

// KmPerDegree is the approximate km per degree of latitude/longitude.
// This is a simplified conversion; actual value varies by latitude.
const KmPerDegree = 111.0

// Point represents a GPS coordinate.
type Point struct {
	Lat, Lon float64
}

// GeoJSONGeometry represents a GeoJSON geometry object.
type GeoJSONGeometry struct {
	Type        string          `json:"type"`
	Coordinates [][][]float64   `json:"coordinates"`
}

// ProtectedArea represents a conservation area with polygon geometry.
type ProtectedArea struct {
	ID       string          `json:"id"`
	Name     string          `json:"name"`
	Country  string          `json:"country"`
	Geometry GeoJSONGeometry `json:"geometry"`
	BufferKm float64         `json:"buffer_km"`

	// Cached bounding box for fast rejection
	bbox *boundingBox
}

// boundingBox for fast point-in-polygon pre-check.
type boundingBox struct {
	LatMin, LatMax float64
	LonMin, LonMax float64
}

// AreaStore holds a collection of protected areas for lookup.
type AreaStore struct {
	Areas []ProtectedArea
}

// LoadAreas loads protected areas from a JSON file.
func LoadAreas(path string) (*AreaStore, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}

	var areas []ProtectedArea
	if err := json.Unmarshal(data, &areas); err != nil {
		return nil, err
	}

	// Compute bounding boxes for each area
	for i := range areas {
		areas[i].computeBoundingBox()
	}

	return &AreaStore{Areas: areas}, nil
}

// computeBoundingBox calculates and caches the bounding box for fast rejection.
func (a *ProtectedArea) computeBoundingBox() {
	if len(a.Geometry.Coordinates) == 0 {
		return
	}

	ring := a.Geometry.Coordinates[0] // Outer ring
	if len(ring) == 0 {
		return
	}

	bbox := &boundingBox{
		LatMin: ring[0][1],
		LatMax: ring[0][1],
		LonMin: ring[0][0],
		LonMax: ring[0][0],
	}

	for _, coord := range ring {
		lon, lat := coord[0], coord[1]
		if lat < bbox.LatMin {
			bbox.LatMin = lat
		}
		if lat > bbox.LatMax {
			bbox.LatMax = lat
		}
		if lon < bbox.LonMin {
			bbox.LonMin = lon
		}
		if lon > bbox.LonMax {
			bbox.LonMax = lon
		}
	}

	a.bbox = bbox
}

// FindArea finds which protected area contains the given point (with buffer).
// Returns nil if the point is not within any area.
func (s *AreaStore) FindArea(lat, lon float64) *ProtectedArea {
	for i := range s.Areas {
		if s.Areas[i].ContainsPoint(lat, lon) {
			return &s.Areas[i]
		}
	}
	return nil
}

// AssignPointsToAreas groups points by the area they fall within.
// Points not in any area are grouped under the key "outside".
func (s *AreaStore) AssignPointsToAreas(points []Point) map[string][]Point {
	result := make(map[string][]Point)

	for _, p := range points {
		area := s.FindArea(p.Lat, p.Lon)
		if area != nil {
			result[area.ID] = append(result[area.ID], p)
		} else {
			result["outside"] = append(result["outside"], p)
		}
	}

	return result
}

// ContainsPoint checks if a point is within the area's polygon plus buffer.
func (a *ProtectedArea) ContainsPoint(lat, lon float64) bool {
	// Convert buffer from km to degrees
	bufferDeg := a.BufferKm / KmPerDegree

	// Fast rejection using bounding box
	if a.bbox != nil {
		if lat < a.bbox.LatMin-bufferDeg || lat > a.bbox.LatMax+bufferDeg ||
			lon < a.bbox.LonMin-bufferDeg || lon > a.bbox.LonMax+bufferDeg {
			return false
		}
	}

	// Check if point is inside the polygon
	if pointInPolygon(lat, lon, a.Geometry.Coordinates) {
		return true
	}

	// Check if point is within buffer distance of polygon edge
	if bufferDeg > 0 && pointNearPolygonEdge(lat, lon, a.Geometry.Coordinates, bufferDeg) {
		return true
	}

	return false
}

// pointInPolygon checks if a point is inside a polygon using ray casting algorithm.
// Coordinates are in GeoJSON format: [lon, lat].
func pointInPolygon(lat, lon float64, coords [][][]float64) bool {
	if len(coords) == 0 {
		return false
	}

	// Check outer ring
	ring := coords[0]
	return pointInRing(lat, lon, ring)
}

// pointInRing uses ray casting algorithm to determine if point is inside a ring.
func pointInRing(lat, lon float64, ring [][]float64) bool {
	n := len(ring)
	if n < 3 {
		return false
	}

	inside := false
	j := n - 1

	for i := 0; i < n; i++ {
		// GeoJSON coordinates are [lon, lat]
		xi, yi := ring[i][0], ring[i][1]
		xj, yj := ring[j][0], ring[j][1]

		// Ray casting: check if horizontal ray from point crosses edge
		if ((yi > lat) != (yj > lat)) &&
			(lon < (xj-xi)*(lat-yi)/(yj-yi)+xi) {
			inside = !inside
		}
		j = i
	}

	return inside
}

// pointNearPolygonEdge checks if a point is within bufferDeg of any polygon edge.
func pointNearPolygonEdge(lat, lon float64, coords [][][]float64, bufferDeg float64) bool {
	if len(coords) == 0 {
		return false
	}

	ring := coords[0]
	n := len(ring)
	if n < 2 {
		return false
	}

	bufferSq := bufferDeg * bufferDeg

	for i := 0; i < n-1; i++ {
		// Check distance from point to line segment
		x1, y1 := ring[i][0], ring[i][1]
		x2, y2 := ring[i+1][0], ring[i+1][1]

		distSq := pointToSegmentDistanceSq(lon, lat, x1, y1, x2, y2)
		if distSq <= bufferSq {
			return true
		}
	}

	return false
}

// pointToSegmentDistanceSq returns the squared distance from point (px, py) to
// line segment (x1, y1) - (x2, y2).
func pointToSegmentDistanceSq(px, py, x1, y1, x2, y2 float64) float64 {
	dx := x2 - x1
	dy := y2 - y1

	if dx == 0 && dy == 0 {
		// Segment is a point
		return (px-x1)*(px-x1) + (py-y1)*(py-y1)
	}

	// Parameter t for closest point on line
	t := ((px-x1)*dx + (py-y1)*dy) / (dx*dx + dy*dy)

	// Clamp t to segment
	t = math.Max(0, math.Min(1, t))

	// Closest point on segment
	closestX := x1 + t*dx
	closestY := y1 + t*dy

	return (px-closestX)*(px-closestX) + (py-closestY)*(py-closestY)
}

// GetBoundingBox returns the bounding box for the area (for backward compatibility).
func (a *ProtectedArea) GetBoundingBox() (latMin, latMax, lonMin, lonMax float64) {
	if a.bbox != nil {
		return a.bbox.LatMin, a.bbox.LatMax, a.bbox.LonMin, a.bbox.LonMax
	}
	return 0, 0, 0, 0
}
