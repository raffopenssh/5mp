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
	Tracks    []Track
	Waypoints []Waypoint
	Name      string
}

// Waypoint represents a GPX waypoint, commonly used for InReach messages.
type Waypoint struct {
	Lat       float64
	Lon       float64
	Elevation *float64
	Time      *time.Time
	Name      string
	Desc      string // Message content from InReach devices
}

// GPX XML structures for parsing
type gpxFile struct {
	XMLName   xml.Name      `xml:"gpx"`
	Metadata  gpxMeta       `xml:"metadata"`
	Tracks    []gpxTrack    `xml:"trk"`
	Waypoints []gpxWaypoint `xml:"wpt"`
}

type gpxWaypoint struct {
	Lat       float64  `xml:"lat,attr"`
	Lon       float64  `xml:"lon,attr"`
	Elevation *float64 `xml:"ele"`
	Time      string   `xml:"time"`
	Name      string   `xml:"name"`
	Desc      string   `xml:"desc"`
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
		Name:      gpx.Metadata.Name,
		Tracks:    make([]Track, 0, len(gpx.Tracks)),
		Waypoints: make([]Waypoint, 0, len(gpx.Waypoints)),
	}

	// Parse waypoints (common for InReach messages)
	for _, wpt := range gpx.Waypoints {
		waypoint := Waypoint{
			Lat:       wpt.Lat,
			Lon:       wpt.Lon,
			Elevation: wpt.Elevation,
			Name:      wpt.Name,
			Desc:      wpt.Desc,
		}

		if wpt.Time != "" {
			// Try multiple time formats
			for _, format := range []string{
				time.RFC3339,
				time.RFC3339Nano,
				"2006-01-02T15:04:05Z",
				"2006-01-02T15:04:05",
			} {
				if t, err := time.Parse(format, wpt.Time); err == nil {
					waypoint.Time = &t
					break
				}
			}
		}

		data.Waypoints = append(data.Waypoints, waypoint)
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

	// Extract movement hints from waypoints (InReach messages, etc.)
	hint := ExtractMovementHintsFromWaypoints(data.Waypoints)

	var segments []Segment

	for _, track := range data.Tracks {
		for _, trackSeg := range track.Segments {
			if len(trackSeg) == 0 {
				continue
			}

			// Split this track segment into time-bounded segments
			segs := splitByDurationWithHint(trackSeg, maxDuration, hint)
			segments = append(segments, segs...)
		}
	}

	return segments
}

// splitByDuration splits a slice of points into segments based on time duration.
func splitByDuration(points []Point, maxDuration time.Duration) []Segment {
	return splitByDurationWithHint(points, maxDuration, MovementHint{})
}

// splitByDurationWithHint splits points into segments using optional movement hints.
func splitByDurationWithHint(points []Point, maxDuration time.Duration, hint MovementHint) []Segment {
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
				// Finalize current segment with movement hint
				seg := buildSegmentWithHint(currentPoints, hint)
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
		seg := buildSegmentWithHint(currentPoints, hint)
		segments = append(segments, seg)
	}

	return segments
}

// buildSegment creates a Segment from a slice of points, computing all statistics.
func buildSegment(points []Point) Segment {
	return buildSegmentWithHint(points, MovementHint{})
}

// buildSegmentWithHint creates a Segment using optional movement hints for classification.
func buildSegmentWithHint(points []Point, hint MovementHint) Segment {
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
	seg.MovementType = ClassifyMovementTypeWithHint(seg, hint)

	return seg
}

// ClassifyMovementType determines the movement type based on average speed.
// Returns:
//   - "foot": < 8 km/h (walking, running)
//   - "vehicle": 8-120 km/h (car, motorbike)
//   - "aircraft": > 120 km/h
func ClassifyMovementType(segment Segment) string {
	return ClassifyMovementTypeWithHint(segment, MovementHint{})
}

// ClassifyMovementTypeWithHint determines movement type using speed and optional message hints.
// Message hints from Garmin InReach waypoints can improve classification confidence,
// especially in ambiguous speed ranges (e.g., slow vehicle vs fast walking).
func ClassifyMovementTypeWithHint(segment Segment, hint MovementHint) string {
	speed := segment.AvgSpeedKmh

	// If we have a high-confidence hint, use it for ambiguous speeds
	if hint.Type != "" && hint.Confidence >= 0.8 {
		// Aircraft hint with speed > 50 km/h
		if hint.Type == "aircraft" && speed > 50 {
			return "aircraft"
		}
		// Vehicle hint in reasonable vehicle speed range
		if hint.Type == "vehicle" && speed >= 5 && speed <= 150 {
			return "vehicle"
		}
		// Foot hint with speed < 15 km/h (fast running)
		if hint.Type == "foot" && speed < 15 {
			return "foot"
		}
	}

	// For moderate confidence hints, use them in ambiguous ranges
	if hint.Type != "" && hint.Confidence >= 0.6 {
		// Ambiguous zone: 5-12 km/h could be fast walk or slow vehicle
		if speed >= 5 && speed <= 12 {
			if hint.Type == "vehicle" {
				return "vehicle"
			}
			if hint.Type == "foot" {
				return "foot"
			}
		}
		// Ambiguous zone: 80-150 km/h could be fast vehicle or slow aircraft
		if speed >= 80 && speed <= 150 {
			if hint.Type == "aircraft" {
				return "aircraft"
			}
			if hint.Type == "vehicle" {
				return "vehicle"
			}
		}
	}

	// Default speed-based classification
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

// RemoveStraightLineGaps detects and removes segments that appear as straight lines
// due to GPS signal loss. These are characterized by:
// - Large time gaps (>5 minutes)
// - High speed during the gap (>200 km/h - teleportation)
// - Very few points covering long distances
// Returns cleaned segments with gap points removed.
func RemoveStraightLineGaps(segments []Segment) []Segment {
	var result []Segment

	for _, seg := range segments {
		cleaned := removeGapsFromSegment(seg)
		result = append(result, cleaned...)
	}

	return result
}

// removeGapsFromSegment finds and splits a segment at gap points
func removeGapsFromSegment(seg Segment) []Segment {
	if len(seg.Points) < 3 {
		return []Segment{seg}
	}

	var result []Segment
	var currentPoints []Point

	for i := 0; i < len(seg.Points); i++ {
		pt := seg.Points[i]

		if i == 0 {
			currentPoints = append(currentPoints, pt)
			continue
		}

		prevPt := seg.Points[i-1]

		// Check for gap characteristics
		isGap := false

		// Time gap check (>5 minutes)
		if pt.Time != nil && prevPt.Time != nil {
			timeGap := pt.Time.Sub(*prevPt.Time)
			if timeGap > 5*time.Minute {
				// Calculate instantaneous speed during the gap
				dist := haversineDistance(prevPt, pt)
				hours := timeGap.Hours()
				if hours > 0 {
					speed := dist / hours
					// If speed > 200 km/h during a gap, it's likely GPS loss
					if speed > 200 {
						isGap = true
					}
				}
			}
		}

		// Also check for unrealistically long distance jumps (>10km in single point)
		dist := haversineDistance(prevPt, pt)
		if dist > 10 {
			// Check if there's a time gap too
			if pt.Time != nil && prevPt.Time != nil {
				timeGap := pt.Time.Sub(*prevPt.Time)
				if timeGap > 1*time.Minute {
					isGap = true
				}
			}
		}

		if isGap {
			// End current segment and start new one
			if len(currentPoints) >= 2 {
				result = append(result, buildSegment(currentPoints))
			}
			currentPoints = []Point{pt}
		} else {
			currentPoints = append(currentPoints, pt)
		}
	}

	// Don't forget the last segment
	if len(currentPoints) >= 2 {
		result = append(result, buildSegment(currentPoints))
	}

	if len(result) == 0 {
		return []Segment{seg} // Return original if nothing survived
	}

	return result
}
