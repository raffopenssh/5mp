// Package gpx provides GPX file parsing and analysis for conservation patrol tracking.
package gpx

import (
	"encoding/xml"
	"io"
	"math"
	"time"
)

// Point represents a single GPS point with coordinates, elevation, and time.
type Point struct {
	Lat, Lon  float64
	Elevation *float64
	Time      *time.Time
	Desc      string // Optional description (e.g., from inReach messages)
}

// Segment represents a continuous track segment with computed statistics.
type Segment struct {
	Points       []Point
	StartTime    *time.Time
	EndTime      *time.Time
	DistanceKm   float64
	AvgSpeedKmh  float64
	MovementType string
}

// Track represents a GPX track containing multiple segments.
type Track struct {
	Name     string
	Segments [][]Point
}

// GPXData represents the parsed GPX file data.
type GPXData struct {
	Tracks []Track
	Name   string
}

// GPX XML structures for parsing
type gpxFile struct {
	XMLName  xml.Name   `xml:"gpx"`
	Metadata gpxMeta    `xml:"metadata"`
	Tracks   []gpxTrack `xml:"trk"`
}

type gpxMeta struct {
	Name string `xml:"name"`
}

type gpxTrack struct {
	Name     string       `xml:"name"`
	Segments []gpxSegment `xml:"trkseg"`
}

type gpxSegment struct {
	Points []gpxPoint `xml:"trkpt"`
}

type gpxPoint struct {
	Lat       float64  `xml:"lat,attr"`
	Lon       float64  `xml:"lon,attr"`
	Elevation *float64 `xml:"ele"`
	Time      string   `xml:"time"`
	Desc      string   `xml:"desc"`
}

// ParseGPX parses GPX XML from an io.Reader into structured data.
// It uses streaming XML parsing for efficient memory usage with large files.
func ParseGPX(r io.Reader) (*GPXData, error) {
	decoder := xml.NewDecoder(r)

	var gpx gpxFile
	if err := decoder.Decode(&gpx); err != nil {
		return nil, err
	}

	data := &GPXData{
		Name:   gpx.Metadata.Name,
		Tracks: make([]Track, 0, len(gpx.Tracks)),
	}

	for _, trk := range gpx.Tracks {
		track := Track{
			Name:     trk.Name,
			Segments: make([][]Point, 0, len(trk.Segments)),
		}

		for _, seg := range trk.Segments {
			points := make([]Point, 0, len(seg.Points))
			for _, pt := range seg.Points {
				point := Point{
					Lat:       pt.Lat,
					Lon:       pt.Lon,
					Elevation: pt.Elevation,
					Desc:      pt.Desc,
				}

				if pt.Time != "" {
					if t, err := time.Parse(time.RFC3339, pt.Time); err == nil {
						point.Time = &t
					}
				}

				points = append(points, point)
			}
			track.Segments = append(track.Segments, points)
		}

		data.Tracks = append(data.Tracks, track)
	}

	return data, nil
}

// DefaultSegmentDuration is the default maximum duration for a segment (30 minutes).
const DefaultSegmentDuration = 30 * time.Minute

// SplitIntoSegments splits all tracks into time-bounded segments.
// If maxDuration is 0, DefaultSegmentDuration (30 minutes) is used.
// Points without timestamps are grouped with adjacent points.
func SplitIntoSegments(data *GPXData, maxDuration time.Duration) []Segment {
	if maxDuration == 0 {
		maxDuration = DefaultSegmentDuration
	}

	var segments []Segment

	for _, track := range data.Tracks {
		for _, trackSeg := range track.Segments {
			if len(trackSeg) == 0 {
				continue
			}

			// Split this track segment into time-bounded segments
			segs := splitByDuration(trackSeg, maxDuration)
			segments = append(segments, segs...)
		}
	}

	return segments
}

// splitByDuration splits a slice of points into segments based on time duration.
func splitByDuration(points []Point, maxDuration time.Duration) []Segment {
	if len(points) == 0 {
		return nil
	}

	var segments []Segment
	var currentPoints []Point
	var segmentStart *time.Time

	for _, pt := range points {
		// If this is the first point or we don't have time info, just add it
		if len(currentPoints) == 0 {
			currentPoints = append(currentPoints, pt)
			if pt.Time != nil {
				segmentStart = pt.Time
			}
			continue
		}

		// Check if we need to start a new segment based on time
		// Use > not >= so points exactly at the boundary stay in current segment
		if pt.Time != nil && segmentStart != nil {
			if pt.Time.Sub(*segmentStart) > maxDuration {
				// Finalize current segment
				seg := buildSegment(currentPoints)
				segments = append(segments, seg)

				// Start new segment
				currentPoints = []Point{pt}
				segmentStart = pt.Time
				continue
			}
		}

		currentPoints = append(currentPoints, pt)
		if pt.Time != nil && segmentStart == nil {
			segmentStart = pt.Time
		}
	}

	// Don't forget the last segment
	if len(currentPoints) > 0 {
		seg := buildSegment(currentPoints)
		segments = append(segments, seg)
	}

	return segments
}

// buildSegment creates a Segment from a slice of points, computing all statistics.
func buildSegment(points []Point) Segment {
	seg := Segment{
		Points: points,
	}

	// Find start and end times
	for i := range points {
		if points[i].Time != nil {
			seg.StartTime = points[i].Time
			break
		}
	}
	for i := len(points) - 1; i >= 0; i-- {
		if points[i].Time != nil {
			seg.EndTime = points[i].Time
			break
		}
	}

	// Calculate distance and speed
	seg.DistanceKm = CalculateDistance(points)
	seg.AvgSpeedKmh = CalculateSpeed(points)
	seg.MovementType = ClassifyMovementType(seg)

	return seg
}

// ClassifyMovementType determines the movement type based on average speed.
// Returns:
//   - "foot": < 8 km/h (walking, running)
//   - "vehicle": 8-120 km/h (car, motorbike)
//   - "aircraft": > 120 km/h
func ClassifyMovementType(segment Segment) string {
	speed := segment.AvgSpeedKmh

	switch {
	case speed < 8:
		return "foot"
	case speed <= 120:
		return "vehicle"
	default:
		return "aircraft"
	}
}

// CalculateDistance computes the total distance in kilometers using the Haversine formula.
func CalculateDistance(points []Point) float64 {
	if len(points) < 2 {
		return 0
	}

	var totalDist float64
	for i := 1; i < len(points); i++ {
		totalDist += haversineDistance(points[i-1], points[i])
	}

	return totalDist
}

// CalculateSpeed computes the average speed in km/h based on total distance and elapsed time.
// Returns 0 if there are fewer than 2 points or no valid time data.
func CalculateSpeed(points []Point) float64 {
	if len(points) < 2 {
		return 0
	}

	// Find first and last points with valid times
	var startTime, endTime *time.Time
	for i := range points {
		if points[i].Time != nil {
			startTime = points[i].Time
			break
		}
	}
	for i := len(points) - 1; i >= 0; i-- {
		if points[i].Time != nil {
			endTime = points[i].Time
			break
		}
	}

	if startTime == nil || endTime == nil {
		return 0
	}

	duration := endTime.Sub(*startTime)
	if duration <= 0 {
		return 0
	}

	distance := CalculateDistance(points)
	hours := duration.Hours()

	return distance / hours
}

// haversineDistance calculates the great-circle distance between two points in kilometers.
// Uses the Haversine formula which is accurate for most distances.
func haversineDistance(p1, p2 Point) float64 {
	const earthRadiusKm = 6371.0

	lat1Rad := degreesToRadians(p1.Lat)
	lat2Rad := degreesToRadians(p2.Lat)
	deltaLat := degreesToRadians(p2.Lat - p1.Lat)
	deltaLon := degreesToRadians(p2.Lon - p1.Lon)

	a := math.Sin(deltaLat/2)*math.Sin(deltaLat/2) +
		math.Cos(lat1Rad)*math.Cos(lat2Rad)*
			math.Sin(deltaLon/2)*math.Sin(deltaLon/2)

	c := 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))

	return earthRadiusKm * c
}

// degreesToRadians converts degrees to radians.
func degreesToRadians(deg float64) float64 {
	return deg * math.Pi / 180
}
