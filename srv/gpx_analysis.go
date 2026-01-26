package srv

import (
	"math"
	"time"
)

// GPXAnalysis contains analysis results for a GPX segment
type GPXAnalysis struct {
	// Speed metrics
	AvgSpeedKmh    float64 `json:"avg_speed_kmh"`
	MaxSpeedKmh    float64 `json:"max_speed_kmh"`
	MovementType   string  `json:"movement_type"`
	SpeedCategory  string  `json:"speed_category"` // slow_patrol, fast_patrol, vehicle, aircraft
	
	// Pattern detection
	HasCircling    bool    `json:"has_circling"`    // Detected circling patterns
	CirclingCount  int     `json:"circling_count"`  // Number of circling events
	IsStraightLine bool    `json:"is_straight_line"` // Mostly straight path (poor coverage)
	Sinuosity      float64 `json:"sinuosity"`       // Path complexity (1 = straight, higher = more winding)
	
	// Messages (from inReach/satellite devices)
	MessageCount   int      `json:"message_count"`
	Messages       []string `json:"messages,omitempty"`
	
	// Quality assessment
	CoverageQuality string  `json:"coverage_quality"` // poor, moderate, good, excellent
	QualityScore    float64 `json:"quality_score"`    // 0-100
}

// AnalyzeGPXSegment performs pattern analysis on a GPX segment
func AnalyzeGPXSegment(points []struct {
	Lat, Lon  float64
	Time      *time.Time
	Elevation *float64
	Desc      string
}) GPXAnalysis {
	analysis := GPXAnalysis{}
	
	if len(points) < 2 {
		return analysis
	}
	
	// Calculate speeds between consecutive points
	var speeds []float64
	var totalDistance float64
	var totalTime float64
	var bearings []float64
	
	for i := 1; i < len(points); i++ {
		p1 := points[i-1]
		p2 := points[i]
		
		dist := haversineDistanceKm(p1.Lat, p1.Lon, p2.Lat, p2.Lon)
		totalDistance += dist
		
		if p1.Time != nil && p2.Time != nil {
			duration := p2.Time.Sub(*p1.Time).Hours()
			if duration > 0 && duration < 24 { // Sanity check
				speed := dist / duration
				speeds = append(speeds, speed)
				totalTime += duration
			}
		}
		
		// Calculate bearing for sinuosity
		bearing := calculateBearing(p1.Lat, p1.Lon, p2.Lat, p2.Lon)
		bearings = append(bearings, bearing)
	}
	
	// Calculate average and max speed
	if len(speeds) > 0 {
		var sum float64
		for _, s := range speeds {
			sum += s
			if s > analysis.MaxSpeedKmh {
				analysis.MaxSpeedKmh = s
			}
		}
		analysis.AvgSpeedKmh = sum / float64(len(speeds))
	} else if totalTime > 0 {
		analysis.AvgSpeedKmh = totalDistance / totalTime
	}
	
	// Classify movement type based on speed
	analysis.MovementType, analysis.SpeedCategory = classifyMovement(analysis.AvgSpeedKmh)
	
	// Calculate sinuosity (path complexity)
	if totalDistance > 0 && len(points) >= 2 {
		directDist := haversineDistanceKm(points[0].Lat, points[0].Lon, 
			points[len(points)-1].Lat, points[len(points)-1].Lon)
		if directDist > 0.1 { // Avoid division by near-zero
			analysis.Sinuosity = totalDistance / directDist
		}
		analysis.IsStraightLine = analysis.Sinuosity < 1.2
	}
	
	// Detect circling patterns (large bearing changes in short segments)
	analysis.HasCircling, analysis.CirclingCount = detectCircling(bearings, points)
	
	// Extract messages from desc fields
	for _, p := range points {
		if p.Desc != "" && !isDefaultMessage(p.Desc) {
			analysis.MessageCount++
			if len(analysis.Messages) < 10 { // Limit stored messages
				analysis.Messages = append(analysis.Messages, p.Desc)
			}
		}
	}
	
	// Calculate quality score
	analysis.QualityScore, analysis.CoverageQuality = calculateQuality(analysis)
	
	return analysis
}

// classifyMovement determines movement type from speed
func classifyMovement(avgSpeed float64) (movementType, category string) {
	switch {
	case avgSpeed < 1:
		return "foot", "stationary"
	case avgSpeed < 3:
		return "foot", "slow_patrol" // Good patrol speed
	case avgSpeed < 6:
		return "foot", "fast_patrol" // Running or fast walking
	case avgSpeed < 30:
		return "vehicle", "slow_vehicle"
	case avgSpeed < 80:
		return "vehicle", "fast_vehicle"
	case avgSpeed < 150:
		return "aircraft", "low_altitude"
	default:
		return "aircraft", "high_altitude"
	}
}

// calculateBearing returns bearing in degrees from point 1 to point 2
func calculateBearing(lat1, lon1, lat2, lon2 float64) float64 {
	lat1Rad := lat1 * math.Pi / 180
	lat2Rad := lat2 * math.Pi / 180
	dLon := (lon2 - lon1) * math.Pi / 180
	
	y := math.Sin(dLon) * math.Cos(lat2Rad)
	x := math.Cos(lat1Rad)*math.Sin(lat2Rad) - math.Sin(lat1Rad)*math.Cos(lat2Rad)*math.Cos(dLon)
	
	bearing := math.Atan2(y, x) * 180 / math.Pi
	return math.Mod(bearing+360, 360)
}

// detectCircling looks for circling patterns (multiple direction changes)
func detectCircling(bearings []float64, points []struct {
	Lat, Lon  float64
	Time      *time.Time
	Elevation *float64
	Desc      string
}) (bool, int) {
	if len(bearings) < 10 {
		return false, 0
	}
	
	circlingCount := 0
	windowSize := 5
	threshold := 270.0 // Degrees of total bearing change for circling
	
	for i := 0; i <= len(bearings)-windowSize; i++ {
		var totalChange float64
		for j := i; j < i+windowSize-1; j++ {
			change := math.Abs(bearings[j+1] - bearings[j])
			if change > 180 {
				change = 360 - change
			}
			totalChange += change
		}
		if totalChange > threshold {
			circlingCount++
			i += windowSize - 1 // Skip ahead
		}
	}
	
	return circlingCount > 0, circlingCount
}

// isDefaultMessage checks if desc is a default/auto message
func isDefaultMessage(desc string) bool {
	defaults := []string{
		"I'm checking in. Everything is okay.",
		"Tracking",
		"Waypoint",
	}
	for _, d := range defaults {
		if desc == d {
			return true
		}
	}
	return false
}

// calculateQuality computes overall coverage quality
func calculateQuality(a GPXAnalysis) (float64, string) {
	score := 50.0 // Base score
	
	// Speed factor: slow is better for coverage
	switch a.SpeedCategory {
	case "slow_patrol":
		score += 30
	case "fast_patrol":
		score += 20
	case "slow_vehicle":
		score += 15
	case "fast_vehicle":
		score += 5
	case "low_altitude":
		score += 20 // Low altitude aircraft can be effective
	case "high_altitude":
		score -= 10 // High altitude = poor observation
	}
	
	// Sinuosity bonus (winding path = better coverage)
	if a.Sinuosity > 2.0 {
		score += 15
	} else if a.Sinuosity > 1.5 {
		score += 10
	} else if a.IsStraightLine {
		score -= 15
	}
	
	// Circling bonus (detailed inspection)
	if a.HasCircling {
		score += float64(a.CirclingCount) * 3
		if score > 100 {
			score = 100
		}
	}
	
	// Cap score
	if score < 0 {
		score = 0
	}
	if score > 100 {
		score = 100
	}
	
	// Quality category
	var quality string
	switch {
	case score >= 80:
		quality = "excellent"
	case score >= 60:
		quality = "good"
	case score >= 40:
		quality = "moderate"
	default:
		quality = "poor"
	}
	
	return score, quality
}
