// Package areas provides protected area management for conservation patrol tracking.
package areas

import (
	"encoding/json"
	"os"
)

// KmPerDegree is the approximate km per degree of latitude/longitude.
// This is a simplified conversion; actual value varies by latitude.
const KmPerDegree = 111.0

// Point represents a GPS coordinate.
type Point struct {
	Lat, Lon float64
}

// ProtectedArea represents a conservation area with bounding box coordinates.
type ProtectedArea struct {
	ID       string  `json:"id"`
	Name     string  `json:"name"`
	LatMin   float64 `json:"lat_min"`
	LatMax   float64 `json:"lat_max"`
	LonMin   float64 `json:"lon_min"`
	LonMax   float64 `json:"lon_max"`
	BufferKm float64 `json:"buffer_km"`
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

	return &AreaStore{Areas: areas}, nil
}

// FindArea finds which protected area contains the given point (with buffer).
// Returns nil if the point is not within any area.
func (s *AreaStore) FindArea(lat, lon float64) *ProtectedArea {
	for i := range s.Areas {
		if PointInArea(lat, lon, s.Areas[i]) {
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

// PointInArea checks if a point is within the area's bounding box plus buffer.
// The buffer expands the bounding box by the specified km in all directions.
func PointInArea(lat, lon float64, area ProtectedArea) bool {
	// Convert buffer from km to degrees
	bufferDeg := area.BufferKm / KmPerDegree

	// Expand bounds by buffer
	latMin := area.LatMin - bufferDeg
	latMax := area.LatMax + bufferDeg
	lonMin := area.LonMin - bufferDeg
	lonMax := area.LonMax + bufferDeg

	return lat >= latMin && lat <= latMax && lon >= lonMin && lon <= lonMax
}
