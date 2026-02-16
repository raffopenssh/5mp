package gpx

import (
	"math"
)

// MovementMetrics captures trajectory characteristics for movement classification
type MovementMetrics struct {
	AvgSpeedKmh       float64 // Average speed
	MaxSpeedKmh       float64 // Maximum instantaneous speed
	MinSpeedKmh       float64 // Minimum instantaneous speed (excluding stops)
	SpeedVariance     float64 // Variance in speed (0-1 normalized)
	BearingVariance   float64 // Variance in direction changes (0-1, 0=straight, 1=erratic)
	SmoothnessFactor  float64 // 0-1, how smooth the trajectory is (1=perfectly smooth)
	StopFrequency     float64 // Proportion of time stopped (<0.5 km/h)
	AccelerationScore float64 // How much speed changes between points
	
	// Spatial clustering for base/camp detection
	BoundingBoxKm     float64 // Size of bounding box (diagonal in km)
	PointDensity      float64 // Points per km² 
	CentroidLat       float64 // Center of all points
	CentroidLon       float64
	
	// Pattern detection
	IsLinear          bool    // Points form a mostly straight line (road/airstrip)
	LinearityScore    float64 // 0-1, how linear the trajectory is
	HasLandingPattern bool    // Decelerating to stop (aircraft landing)
	HasTakeoffPattern bool    // Accelerating from stop (aircraft takeoff)
}

// AnalyzeTrajectory computes movement metrics for a set of points
func AnalyzeTrajectory(points []Point) MovementMetrics {
	if len(points) < 3 {
		return MovementMetrics{}
	}

	var speeds []float64
	var bearings []float64
	var bearingChanges []float64
	var accelerations []float64
	var stopCount int
	
	// For bounding box and centroid
	minLat, maxLat := points[0].Lat, points[0].Lat
	minLon, maxLon := points[0].Lon, points[0].Lon
	var sumLat, sumLon float64
	
	prevSpeed := 0.0
	prevBearing := 0.0
	totalDist := 0.0
	
	for i, pt := range points {
		// Update bounding box and centroid
		if pt.Lat < minLat { minLat = pt.Lat }
		if pt.Lat > maxLat { maxLat = pt.Lat }
		if pt.Lon < minLon { minLon = pt.Lon }
		if pt.Lon > maxLon { maxLon = pt.Lon }
		sumLat += pt.Lat
		sumLon += pt.Lon
		
		if i == 0 {
			continue
		}
		
		// Calculate speed between consecutive points
		dist := haversineDistance(points[i-1], pt)
		totalDist += dist
		
		var duration float64
		if points[i-1].Time != nil && pt.Time != nil {
			duration = pt.Time.Sub(*points[i-1].Time).Hours()
		}
		
		speed := 0.0
		if duration > 0 {
			speed = dist / duration
		}
		
		if speed < 0.5 {
			stopCount++
		} else {
			speeds = append(speeds, speed)
		}
		
		// Calculate bearing
		bearing := calculateBearing(points[i-1].Lat, points[i-1].Lon, pt.Lat, pt.Lon)
		bearings = append(bearings, bearing)
		
		// Calculate bearing change from previous segment
		if i > 1 {
			bearingChange := math.Abs(bearing - prevBearing)
			if bearingChange > 180 {
				bearingChange = 360 - bearingChange
			}
			bearingChanges = append(bearingChanges, bearingChange)
		}
		
		// Calculate acceleration
		if i > 1 && duration > 0 {
			accel := math.Abs(speed - prevSpeed)
			accelerations = append(accelerations, accel)
		}
		
		prevSpeed = speed
		prevBearing = bearing
	}

	metrics := MovementMetrics{}
	
	// Centroid
	n := float64(len(points))
	metrics.CentroidLat = sumLat / n
	metrics.CentroidLon = sumLon / n
	
	// Bounding box diagonal
	metrics.BoundingBoxKm = haversineDistance(
		Point{Lat: minLat, Lon: minLon},
		Point{Lat: maxLat, Lon: maxLon},
	)
	
	// Point density (points per km²)
	if metrics.BoundingBoxKm > 0.01 {
		// Approximate area as rectangle
		latDist := haversineDistance(Point{Lat: minLat, Lon: minLon}, Point{Lat: maxLat, Lon: minLon})
		lonDist := haversineDistance(Point{Lat: minLat, Lon: minLon}, Point{Lat: minLat, Lon: maxLon})
		area := latDist * lonDist
		if area > 0 {
			metrics.PointDensity = n / area
		}
	}
	
	// Speed metrics
	if len(speeds) > 0 {
		var sum, min, max float64
		min = speeds[0]
		max = speeds[0]
		for _, s := range speeds {
			sum += s
			if s < min { min = s }
			if s > max { max = s }
		}
		metrics.AvgSpeedKmh = sum / float64(len(speeds))
		metrics.MinSpeedKmh = min
		metrics.MaxSpeedKmh = max
		
		// Speed variance (normalized by mean)
		var variance float64
		for _, s := range speeds {
			diff := s - metrics.AvgSpeedKmh
			variance += diff * diff
		}
		variance /= float64(len(speeds))
		if metrics.AvgSpeedKmh > 0 {
			metrics.SpeedVariance = math.Sqrt(variance) / metrics.AvgSpeedKmh
			if metrics.SpeedVariance > 1 {
				metrics.SpeedVariance = 1
			}
		}
	}
	
	// Bearing variance (how erratic the direction changes are)
	if len(bearingChanges) > 0 {
		var sum float64
		for _, bc := range bearingChanges {
			sum += bc
		}
		avgBearingChange := sum / float64(len(bearingChanges))
		// Normalize: 0 = straight line, 1 = very erratic (avg 90+ degree turns)
		metrics.BearingVariance = avgBearingChange / 90.0
		if metrics.BearingVariance > 1 {
			metrics.BearingVariance = 1
		}
	}
	
	// Linearity score: how close to a straight line
	// Compare direct distance to total traveled distance
	if totalDist > 0 {
		directDist := haversineDistance(points[0], points[len(points)-1])
		metrics.LinearityScore = directDist / totalDist
		if metrics.LinearityScore > 1 {
			metrics.LinearityScore = 1
		}
		// Linear if >0.85 (traveled path is close to straight line)
		metrics.IsLinear = metrics.LinearityScore > 0.85 && metrics.BearingVariance < 0.15
	}
	
	// Smoothness factor: combination of speed consistency and bearing consistency
	// Aircraft: very smooth (low variance in both)
	// Vehicle: medium smoothness (some variance due to terrain)
	// Foot: low smoothness (high variance in both)
	metrics.SmoothnessFactor = 1.0 - (metrics.SpeedVariance*0.5 + metrics.BearingVariance*0.5)
	if metrics.SmoothnessFactor < 0 {
		metrics.SmoothnessFactor = 0
	}
	
	// Stop frequency
	totalPoints := len(points) - 1
	if totalPoints > 0 {
		metrics.StopFrequency = float64(stopCount) / float64(totalPoints)
	}
	
	// Acceleration score (normalized)
	if len(accelerations) > 0 {
		var sum float64
		for _, a := range accelerations {
			sum += a
		}
		avgAccel := sum / float64(len(accelerations))
		// Normalize by average speed
		if metrics.AvgSpeedKmh > 0 {
			metrics.AccelerationScore = avgAccel / metrics.AvgSpeedKmh
			if metrics.AccelerationScore > 1 {
				metrics.AccelerationScore = 1
			}
		}
	}
	
	// Detect landing pattern: high speed -> low speed -> stop, smooth trajectory
	// Check last 1/3 of points for deceleration
	if len(speeds) >= 6 {
		lastThird := speeds[len(speeds)*2/3:]
		if len(lastThird) >= 2 {
			firstSpeed := lastThird[0]
			lastSpeed := lastThird[len(lastThird)-1]
			// Landing: significant deceleration with smooth trajectory
			if firstSpeed > 30 && lastSpeed < 10 && metrics.SmoothnessFactor > 0.6 {
				metrics.HasLandingPattern = true
			}
		}
		
		// Detect takeoff pattern: stop/slow -> high speed, smooth trajectory
		firstThird := speeds[:len(speeds)/3]
		if len(firstThird) >= 2 {
			firstSpeed := firstThird[0]
			lastSpeed := firstThird[len(firstThird)-1]
			// Takeoff: significant acceleration with smooth trajectory  
			if firstSpeed < 10 && lastSpeed > 30 && metrics.SmoothnessFactor > 0.6 {
				metrics.HasTakeoffPattern = true
			}
		}
	}
	
	return metrics
}

// ClassifyMovementAdvanced uses trajectory analysis for better classification
// Returns "foot", "vehicle", or "aircraft"
//
// Thresholds calibrated for African conservation areas:
// - Foot: typically <7 km/h, erratic bearing changes
// - Vehicle: 7-80 km/h, rarely faster due to poor roads
// - Aircraft: any speed with very smooth trajectory, or >80 km/h
func ClassifyMovementAdvanced(points []Point) string {
	if len(points) < 3 {
		return "foot" // Default for very short segments
	}
	
	metrics := AnalyzeTrajectory(points)
	
	// Decision tree based on speed AND trajectory characteristics
	speed := metrics.AvgSpeedKmh
	smooth := metrics.SmoothnessFactor
	bearingVar := metrics.BearingVariance
	linear := metrics.LinearityScore
	
	// Check for takeoff/landing patterns - strong indicator of aircraft
	if metrics.HasLandingPattern || metrics.HasTakeoffPattern {
		return "aircraft"
	}
	
	// Very high speed (>120 km/h) = definitely aircraft
	// No vehicle in African parks goes this fast
	if speed > 120 {
		return "aircraft"
	}
	
	// High speed (80-120 km/h) - almost certainly aircraft in African parks
	// Vehicles rarely exceed 60-80 km/h due to road conditions
	if speed >= 80 {
		// Only classify as vehicle if very rough trajectory (unlikely aircraft)
		if smooth < 0.4 && bearingVar > 0.3 {
			return "vehicle" // Very bumpy, probably vehicle on bad road
		}
		return "aircraft"
	}
	
	// Medium-high speed (50-80 km/h) - usually vehicle in African parks
	if speed >= 50 {
		// Very smooth + linear = likely aircraft (ULM, helicopter)
		if smooth > 0.7 && bearingVar < 0.12 && linear > 0.8 {
			return "aircraft"
		}
		return "vehicle"
	}
	
	// Medium speed (40-50 km/h) - vehicle or slow ULM
	if speed >= 40 {
		// Very smooth + highly linear = slow aircraft
		if smooth > 0.8 && bearingVar < 0.1 && linear > 0.85 {
			return "aircraft"
		}
		return "vehicle"
	}
	
	// Medium speed (15-40 km/h) - typically vehicle
	if speed >= 15 {
		return "vehicle"
	}
	
	// Low-medium speed (7-15 km/h) - could be foot (running) or slow vehicle
	if speed >= 7 {
		// Vehicle: more consistent speed, smoother trajectory
		// Running: more erratic, higher bearing variance
		if smooth > 0.5 && bearingVar < 0.3 {
			return "vehicle"
		}
		// Erratic pattern = likely foot
		if bearingVar > 0.4 {
			return "foot"
		}
		// Ambiguous - check speed variance
		if metrics.SpeedVariance < 0.3 {
			return "vehicle" // Consistent speed = vehicle
		}
		return "foot"
	}
	
	// Very low speed (<7 km/h) = foot patrol
	return "foot"
}

// calculateBearing returns the bearing in degrees from point 1 to point 2
func calculateBearing(lat1, lon1, lat2, lon2 float64) float64 {
	lat1Rad := lat1 * math.Pi / 180
	lat2Rad := lat2 * math.Pi / 180
	lonDiff := (lon2 - lon1) * math.Pi / 180
	
	x := math.Sin(lonDiff) * math.Cos(lat2Rad)
	y := math.Cos(lat1Rad)*math.Sin(lat2Rad) - math.Sin(lat1Rad)*math.Cos(lat2Rad)*math.Cos(lonDiff)
	
	bearing := math.Atan2(x, y) * 180 / math.Pi
	return math.Mod(bearing+360, 360)
}
