// Package areas provides protected area management for conservation patrol tracking.
package areas

import (
	"encoding/json"
	"fmt"
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
// Supports both Polygon and MultiPolygon types.
type GeoJSONGeometry struct {
	Type        string          `json:"type"`
	Coordinates json.RawMessage `json:"coordinates"`

	// Parsed polygon rings (outer ring only for simplicity)
	// For MultiPolygon, this is the first polygon's outer ring.
	parsedRings [][][]float64
}

// ProtectedArea represents a conservation area with polygon geometry.
type ProtectedArea struct {
	ID          string          `json:"id"`
	Name        string          `json:"name"`
	Country     string          `json:"country"`
	CountryCode string          `json:"country_code"`
	WDPAID      string          `json:"wdpa_id,omitempty"`
	AreaKm2     float64         `json:"area_km2,omitempty"`
	Partner     string          `json:"partner,omitempty"`
	Geometry    GeoJSONGeometry `json:"geometry"`
	BufferKm    float64         `json:"buffer_km"`

	// Cached bounding box for fast rejection
	bbox *boundingBox
}

// boundingBox for fast point-in-polygon pre-check.
type boundingBox struct {
	LatMin, LatMax float64
	LonMin, LonMax float64
}

// KeystonePA represents a protected area from keystones_basic.json.
type KeystonePA struct {
	ID          string  `json:"id"`
	CountryCode string  `json:"country_code"`
	Country     string  `json:"country"`
	Name        string  `json:"name"`
	Partner     *string `json:"partner"`
	Staff       *int    `json:"staff"`
	Budget      *int    `json:"budget"`
	Donor       *string `json:"donor"`
	Performance *string `json:"performance"`
	WDPAID      string  `json:"wdpa_id"`
	AreaKm2     *int    `json:"area_km2"`
	Coordinates struct {
		Lat float64 `json:"lat"`
		Lon float64 `json:"lon"`
	} `json:"coordinates"`
}

// KeystoneWithBoundary represents a keystone PA with fetched boundary.
type KeystoneWithBoundary struct {
	KeystonePA
	Geometry *GeoJSONGeometry `json:"geometry,omitempty"`
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

// LoadKeystones loads protected areas from keystones files.
// It prefers keystones_with_boundaries.json if it exists, otherwise falls back to
// keystones_basic.json and generates circle approximations.
func LoadKeystones(dataDir string) (*AreaStore, error) {
	// Try loading keystones with boundaries first
	boundariesPath := dataDir + "/keystones_with_boundaries.json"
	if _, err := os.Stat(boundariesPath); err == nil {
		areas, err := loadKeystonesWithBoundaries(boundariesPath)
		if err == nil {
			fmt.Printf("Loaded %d keystones with boundaries from %s\n", len(areas.Areas), boundariesPath)
			return areas, nil
		}
		// Fall through to basic if there's an error
		fmt.Printf("Warning: failed to load boundaries file, falling back to basic: %v\n", err)
	}

	// Fall back to basic keystones with circle approximations
	basicPath := dataDir + "/keystones_basic.json"
	areas, err := loadKeystonesBasic(basicPath)
	if err != nil {
		return nil, err
	}
	fmt.Printf("Loaded %d keystones with circle approximations from %s\n", len(areas.Areas), basicPath)
	return areas, nil
}

// loadKeystonesBasic loads keystones from basic JSON and creates circle approximations.
func loadKeystonesBasic(path string) (*AreaStore, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}

	var keystones []KeystonePA
	if err := json.Unmarshal(data, &keystones); err != nil {
		return nil, err
	}

	areas := make([]ProtectedArea, 0, len(keystones))
	for _, ks := range keystones {
		area := keystoneToArea(ks)
		area.computeBoundingBox()
		areas = append(areas, area)
	}

	return &AreaStore{Areas: areas}, nil
}

// loadKeystonesWithBoundaries loads keystones that have actual boundaries.
func loadKeystonesWithBoundaries(path string) (*AreaStore, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}

	var keystones []KeystoneWithBoundary
	if err := json.Unmarshal(data, &keystones); err != nil {
		return nil, err
	}

	areas := make([]ProtectedArea, 0, len(keystones))
	for _, ks := range keystones {
		var area ProtectedArea
		if ks.Geometry != nil && len(ks.Geometry.Coordinates) > 0 {
			// Use actual boundary
			area = ProtectedArea{
				ID:          ks.ID,
				Name:        ks.Name,
				Country:     ks.Country,
				CountryCode: ks.CountryCode,
				WDPAID:      ks.WDPAID,
				Geometry:    *ks.Geometry,
				BufferKm:    5.0, // 5km buffer for matching
			}
			if ks.AreaKm2 != nil {
				area.AreaKm2 = float64(*ks.AreaKm2)
			}
		} else {
			// Fall back to circle approximation
			area = keystoneToArea(ks.KeystonePA)
		}
		area.computeBoundingBox()
		areas = append(areas, area)
	}

	return &AreaStore{Areas: areas}, nil
}

// keystoneToArea converts a KeystonePA to a ProtectedArea with circle geometry.
func keystoneToArea(ks KeystonePA) ProtectedArea {
	// Calculate radius from area (A = πr²)
	areaKm2 := 1000.0 // Default 1000 km² if not specified
	if ks.AreaKm2 != nil {
		areaKm2 = float64(*ks.AreaKm2)
	}

	radiusKm := math.Sqrt(areaKm2 / math.Pi)

	// Create circle polygon
	geometry := createCirclePolygon(ks.Coordinates.Lat, ks.Coordinates.Lon, radiusKm)

	partner := ""
	if ks.Partner != nil {
		partner = *ks.Partner
	}

	return ProtectedArea{
		ID:          ks.ID,
		Name:        ks.Name,
		Country:     ks.Country,
		CountryCode: ks.CountryCode,
		WDPAID:      ks.WDPAID,
		AreaKm2:     areaKm2,
		Partner:     partner,
		Geometry:    geometry,
		BufferKm:    2.0, // Default buffer
	}
}

// createCirclePolygon creates a polygon approximating a circle.
// Uses 32 points for smooth appearance.
func createCirclePolygon(centerLat, centerLon, radiusKm float64) GeoJSONGeometry {
	const numPoints = 32

	// Convert radius to degrees
	// Latitude degrees are roughly constant
	radiusLatDeg := radiusKm / KmPerDegree
	// Longitude degrees vary by latitude
	radiusLonDeg := radiusKm / (KmPerDegree * math.Cos(centerLat*math.Pi/180))

	ring := make([][]float64, numPoints+1)
	for i := 0; i < numPoints; i++ {
		angle := 2 * math.Pi * float64(i) / float64(numPoints)
		lat := centerLat + radiusLatDeg*math.Sin(angle)
		lon := centerLon + radiusLonDeg*math.Cos(angle)
		// GeoJSON uses [lon, lat] order
		ring[i] = []float64{lon, lat}
	}
	// Close the ring
	ring[numPoints] = ring[0]

	rings := [][][]float64{ring}
	coords, _ := json.Marshal(rings)

	return GeoJSONGeometry{
		Type:        "Polygon",
		Coordinates: coords,
		parsedRings: rings,
	}
}

// parseGeometry parses the raw coordinates into polygon rings.
func (g *GeoJSONGeometry) parseGeometry() {
	if g.parsedRings != nil || len(g.Coordinates) == 0 {
		return
	}

	switch g.Type {
	case "Polygon":
		// Polygon: [[[lon, lat], ...]]
		var rings [][][]float64
		if err := json.Unmarshal(g.Coordinates, &rings); err == nil {
			g.parsedRings = rings
		}
	case "MultiPolygon":
		// MultiPolygon: [[[[lon, lat], ...]]]
		// Find the largest polygon by bounding box area
		var multiRings [][][][]float64
		if err := json.Unmarshal(g.Coordinates, &multiRings); err == nil && len(multiRings) > 0 {
			largestIdx := 0
			largestArea := 0.0
			for i, rings := range multiRings {
				if len(rings) > 0 && len(rings[0]) > 0 {
					area := bboxArea(rings[0])
					if area > largestArea {
						largestArea = area
						largestIdx = i
					}
				}
			}
			g.parsedRings = multiRings[largestIdx]
		}
	}
}

// bboxArea calculates approximate bounding box area for a ring.
func bboxArea(ring [][]float64) float64 {
	if len(ring) == 0 {
		return 0
	}
	minLat, maxLat := ring[0][1], ring[0][1]
	minLon, maxLon := ring[0][0], ring[0][0]
	for _, coord := range ring {
		if coord[1] < minLat {
			minLat = coord[1]
		}
		if coord[1] > maxLat {
			maxLat = coord[1]
		}
		if coord[0] < minLon {
			minLon = coord[0]
		}
		if coord[0] > maxLon {
			maxLon = coord[0]
		}
	}
	return (maxLat - minLat) * (maxLon - minLon)
}

// getRings returns the parsed polygon rings.
func (g *GeoJSONGeometry) getRings() [][][]float64 {
	g.parseGeometry()
	return g.parsedRings
}

// computeBoundingBox calculates and caches the bounding box for fast rejection.
func (a *ProtectedArea) computeBoundingBox() {
	rings := a.Geometry.getRings()
	if len(rings) == 0 {
		return
	}

	ring := rings[0] // Outer ring
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

	rings := a.Geometry.getRings()

	// Check if point is inside the polygon
	if pointInPolygon(lat, lon, rings) {
		return true
	}

	// Check if point is within buffer distance of polygon edge
	if bufferDeg > 0 && pointNearPolygonEdge(lat, lon, rings, bufferDeg) {
		return true
	}

	return false
}

// pointInPolygon checks if a point is inside a polygon using ray casting algorithm.
// Coordinates are in GeoJSON format: [lon, lat].
func pointInPolygon(lat, lon float64, rings [][][]float64) bool {
	if len(rings) == 0 {
		return false
	}

	// Check outer ring
	ring := rings[0]
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
func pointNearPolygonEdge(lat, lon float64, rings [][][]float64, bufferDeg float64) bool {
	if len(rings) == 0 {
		return false
	}

	ring := rings[0]
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
