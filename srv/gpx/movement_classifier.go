package gpx

import (
	"math"
	"sort"
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

	// Sampling rate metrics
	MedianIntervalSec    float64 // Median time between consecutive points (seconds)
	IntervalConsistency  float64 // Coefficient of variation of intervals (0=constant, 1=irregular)

	// Elevation metrics
	HasElevation        bool    // Whether >50% of points have elevation data
	ElevationRangeM     float64 // Max elevation minus min elevation
	MaxElevationM       float64 // Maximum elevation
	AvgElevationM       float64 // Average elevation
	ElevationChangeRate float64 // Meters of elevation change per km of horizontal travel

	// Speed percentiles
	P90SpeedKmh float64 // 90th percentile speed
	P10SpeedKmh float64 // 10th percentile speed (lowest non-zero)
}

// MovementClassification contains both movement type and activity type
type MovementClassification struct {
	MovementType string  // "foot", "vehicle", "aircraft"
	ActivityType string  // "patrol", "reconnaissance", "transit", "logistics"
	Confidence   float64 // 0-1 confidence in classification
	Metrics      MovementMetrics
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
	var intervals []float64 // time intervals in seconds between consecutive points
	var stopCount int
	
	// For bounding box and centroid
	minLat, maxLat := points[0].Lat, points[0].Lat
	minLon, maxLon := points[0].Lon, points[0].Lon
	var sumLat, sumLon float64
	
	// For elevation metrics
	var elevations []float64
	var elevCount int
	var totalElevChange float64
	var prevElevation *float64
	
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
		
		// Track elevation
		if pt.Elevation != nil {
			elevations = append(elevations, *pt.Elevation)
			elevCount++
			if prevElevation != nil {
				totalElevChange += math.Abs(*pt.Elevation - *prevElevation)
			}
			prevElevation = pt.Elevation
		}
		
		if i == 0 {
			continue
		}
		
		// Calculate speed between consecutive points
		dist := haversineDistance(points[i-1], pt)
		totalDist += dist
		
		var duration float64
		if points[i-1].Time != nil && pt.Time != nil {
			duration = pt.Time.Sub(*points[i-1].Time).Hours()
			intervalSec := pt.Time.Sub(*points[i-1].Time).Seconds()
			if intervalSec > 0 {
				intervals = append(intervals, intervalSec)
			}
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
	
	// Bearing variance
	if len(bearingChanges) > 0 {
		var sum float64
		for _, bc := range bearingChanges {
			sum += bc
		}
		avgBearingChange := sum / float64(len(bearingChanges))
		metrics.BearingVariance = avgBearingChange / 90.0
		if metrics.BearingVariance > 1 {
			metrics.BearingVariance = 1
		}
	}
	
	// Linearity score
	if totalDist > 0 {
		directDist := haversineDistance(points[0], points[len(points)-1])
		metrics.LinearityScore = directDist / totalDist
		if metrics.LinearityScore > 1 {
			metrics.LinearityScore = 1
		}
		metrics.IsLinear = metrics.LinearityScore > 0.85 && metrics.BearingVariance < 0.15
	}
	
	// Smoothness factor
	metrics.SmoothnessFactor = 1.0 - (metrics.SpeedVariance*0.5 + metrics.BearingVariance*0.5)
	if metrics.SmoothnessFactor < 0 {
		metrics.SmoothnessFactor = 0
	}
	
	// Stop frequency
	totalPoints := len(points) - 1
	if totalPoints > 0 {
		metrics.StopFrequency = float64(stopCount) / float64(totalPoints)
	}
	
	// Acceleration score
	if len(accelerations) > 0 {
		var sum float64
		for _, a := range accelerations {
			sum += a
		}
		avgAccel := sum / float64(len(accelerations))
		if metrics.AvgSpeedKmh > 0 {
			metrics.AccelerationScore = avgAccel / metrics.AvgSpeedKmh
			if metrics.AccelerationScore > 1 {
				metrics.AccelerationScore = 1
			}
		}
	}
	
	// Detect landing/takeoff patterns
	if len(speeds) >= 6 {
		lastThird := speeds[len(speeds)*2/3:]
		if len(lastThird) >= 2 {
			firstSpeed := lastThird[0]
			lastSpeed := lastThird[len(lastThird)-1]
			if firstSpeed > 30 && lastSpeed < 10 && metrics.SmoothnessFactor > 0.6 {
				metrics.HasLandingPattern = true
			}
		}
		
		firstThird := speeds[:len(speeds)/3]
		if len(firstThird) >= 2 {
			firstSpeed := firstThird[0]
			lastSpeed := firstThird[len(firstThird)-1]
			if firstSpeed < 10 && lastSpeed > 30 && metrics.SmoothnessFactor > 0.6 {
				metrics.HasTakeoffPattern = true
			}
		}
	}
	
	// Sampling rate metrics
	if len(intervals) > 0 {
		sortedIntervals := make([]float64, len(intervals))
		copy(sortedIntervals, intervals)
		sort.Float64s(sortedIntervals)
		
		// Median interval
		mid := len(sortedIntervals) / 2
		if len(sortedIntervals)%2 == 0 {
			metrics.MedianIntervalSec = (sortedIntervals[mid-1] + sortedIntervals[mid]) / 2
		} else {
			metrics.MedianIntervalSec = sortedIntervals[mid]
		}
		
		// Coefficient of variation of intervals
		var sumIv float64
		for _, iv := range intervals {
			sumIv += iv
		}
		meanIv := sumIv / float64(len(intervals))
		if meanIv > 0 {
			var varianceIv float64
			for _, iv := range intervals {
				diff := iv - meanIv
				varianceIv += diff * diff
			}
			varianceIv /= float64(len(intervals))
			metrics.IntervalConsistency = math.Sqrt(varianceIv) / meanIv
			if metrics.IntervalConsistency > 1 {
				metrics.IntervalConsistency = 1
			}
		}
	}
	
	// Elevation metrics
	if elevCount > 0 && float64(elevCount)/n > 0.5 {
		metrics.HasElevation = true
		
		var minElev, maxElev, sumElev float64
		minElev = elevations[0]
		maxElev = elevations[0]
		for _, e := range elevations {
			sumElev += e
			if e < minElev { minElev = e }
			if e > maxElev { maxElev = e }
		}
		metrics.ElevationRangeM = maxElev - minElev
		metrics.MaxElevationM = maxElev
		metrics.AvgElevationM = sumElev / float64(len(elevations))
		
		// Elevation change rate: meters per km of horizontal travel
		if totalDist > 0 {
			metrics.ElevationChangeRate = totalElevChange / totalDist
		}
	}
	
	// Speed percentiles
	if len(speeds) > 0 {
		sortedSpeeds := make([]float64, len(speeds))
		copy(sortedSpeeds, speeds)
		sort.Float64s(sortedSpeeds)
		
		// P90
		p90idx := int(math.Ceil(float64(len(sortedSpeeds))*0.9)) - 1
		if p90idx < 0 { p90idx = 0 }
		if p90idx >= len(sortedSpeeds) { p90idx = len(sortedSpeeds) - 1 }
		metrics.P90SpeedKmh = sortedSpeeds[p90idx]
		
		// P10
		p10idx := int(math.Ceil(float64(len(sortedSpeeds))*0.1)) - 1
		if p10idx < 0 { p10idx = 0 }
		if p10idx >= len(sortedSpeeds) { p10idx = len(sortedSpeeds) - 1 }
		metrics.P10SpeedKmh = sortedSpeeds[p10idx]
	}
	
	return metrics
}

// ClassifyMovementFull provides complete classification with movement type and activity type.
// For hint-aware classification from EarthRanger data, use ClassifyMovementFullWithHint.
func ClassifyMovementFull(points []Point) MovementClassification {
	return ClassifyMovementFullWithHint(points, MovementHint{})
}

// ClassifyMovementFullWithHint provides classification using both trajectory analysis
// and external movement hints (e.g., from EarthRanger device metadata).
func ClassifyMovementFullWithHint(points []Point, hint MovementHint) MovementClassification {
	if len(points) < 3 {
		mvType := "foot"
		if hint.Type != "" && hint.Confidence >= 0.9 {
			mvType = hint.Type
		}
		return MovementClassification{
			MovementType: mvType,
			ActivityType: "patrol",
			Confidence:   hint.Confidence,
		}
	}
	
	metrics := AnalyzeTrajectory(points)
	result := MovementClassification{Metrics: metrics}
	
	speed := metrics.AvgSpeedKmh
	smooth := metrics.SmoothnessFactor
	bearingVar := metrics.BearingVariance
	linear := metrics.LinearityScore

	// === AUTHORITATIVE HINT (confidence 1.0) ===
	// EarthRanger device metadata: GPS tracker physically mounted on vehicle/aircraft.
	// Trust completely — a truck GPS says "vehicle" even when parked at 0 km/h.
	if hint.Type != "" && hint.Confidence >= 1.0 {
		result.MovementType = hint.Type
		result.Confidence = 1.0
		// Still classify activity type from trajectory analysis
		result.ActivityType = classifyActivityType(result.MovementType, speed, smooth, bearingVar, linear, metrics)
		return result
	}

	// === STRONG HINT (confidence >= 0.9) ===
	// ER ranger subtype, strong Locus tags. Trust with light sanity check.
	if hint.Type != "" && hint.Confidence >= 0.9 {
		switch hint.Type {
		case "aircraft":
			result.MovementType = "aircraft"
			result.Confidence = 0.95
		case "vehicle":
			result.MovementType = "vehicle"
			result.Confidence = 0.95
		case "foot":
			if speed > 20 {
				// Ranger going >20 km/h = probably in a vehicle
				result.MovementType = "vehicle"
				result.Confidence = 0.8
			} else {
				result.MovementType = "foot"
				result.Confidence = 0.95
			}
		}
		result.ActivityType = classifyActivityType(result.MovementType, speed, smooth, bearingVar, linear, metrics)
		return result
	}
	
	// === MULTI-SIGNAL SCORING CLASSIFICATION (with moderate hint nudging) ===
	var aircraftScore, vehicleScore, footScore float64
	p90 := metrics.P90SpeedKmh
	
	// --- Aircraft evidence ---
	
	// P90 speed > 120 km/h (strong)
	if p90 > 120 {
		aircraftScore += 3.0
	}
	// Average speed > 100 km/h (strong)
	if speed > 100 {
		aircraftScore += 3.0
	}
	// Elevation range > 500m (strong indicator of altitude changes = flight)
	if metrics.HasElevation && metrics.ElevationRangeM > 500 {
		aircraftScore += 3.0
	}
	// Max elevation > 2000m (moderate — useful in Africa where terrain typically <1500m)
	if metrics.HasElevation && metrics.MaxElevationM > 2000 {
		aircraftScore += 2.0
	}
	// Landing/takeoff pattern (moderate)
	if metrics.HasLandingPattern || metrics.HasTakeoffPattern {
		aircraftScore += 2.0
	}
	// Speed 40-100, very smooth, very linear (weak — slow aircraft like ULM/helicopter)
	if speed >= 40 && speed <= 100 && smooth > 0.85 && linear > 0.9 && bearingVar < 0.05 {
		aircraftScore += 1.0
	}
	// Regular 120s interval AND speed > 80 (weak — GPS tracker on aircraft)
	if metrics.MedianIntervalSec > 100 && metrics.MedianIntervalSec < 140 &&
		metrics.IntervalConsistency < 0.15 && speed > 80 {
		aircraftScore += 1.0
	}
	// Very high speed unmistakable
	if speed > 150 {
		aircraftScore += 4.0
	}
	// 80-100 km/h smooth and linear
	if speed >= 80 && speed < 100 && smooth > 0.7 && bearingVar < 0.1 {
		aircraftScore += 1.5
	}
	
	// --- Vehicle evidence ---
	
	// P90 speed 20-120 km/h AND avg speed 8-100 (strong)
	if p90 >= 20 && p90 <= 120 && speed >= 8 && speed <= 100 {
		vehicleScore += 3.0
	}
	// Speed 8-12 km/h AND high smoothness AND low bearing variance (moderate — slow vehicle)
	if speed >= 8 && speed <= 12 && smooth > 0.7 && bearingVar < 0.3 {
		vehicleScore += 2.0
	}
	// Speed 8-12 km/h AND high linearity (moderate)
	if speed >= 8 && speed <= 12 && linear > 0.85 {
		vehicleScore += 2.0
	}
	// Regular 120s interval with speed 10-80 (weak — GPS tracker on vehicle)
	if metrics.MedianIntervalSec > 100 && metrics.MedianIntervalSec < 140 &&
		metrics.IntervalConsistency < 0.15 && speed >= 10 && speed <= 80 {
		vehicleScore += 1.0
	}
	// Speed clearly in vehicle range but not aircraft
	if speed >= 12 && speed < 80 {
		vehicleScore += 2.0
	}
	
	// --- Foot evidence ---
	
	// P90 speed < 10 km/h (strong)
	if p90 < 10 {
		footScore += 3.0
	}
	// Average speed < 5 km/h (strong)
	if speed < 5 {
		footScore += 3.0
	}
	// Speed 5-8 km/h AND high bearing variance — erratic exploring on foot (moderate)
	if speed >= 5 && speed <= 8 && bearingVar > 0.5 {
		footScore += 2.0
	}
	// Speed 5-8 km/h AND low smoothness (moderate)
	if speed >= 5 && speed <= 8 && smooth < 0.4 {
		footScore += 2.0
	}
	// Sampling interval 600s+ with speed < 8 (weak — InReach = usually foot)
	if metrics.MedianIntervalSec >= 600 && speed < 8 {
		footScore += 1.0
	}
	// Very slow speed
	if speed < 7 {
		footScore += 1.5
	}
	
	// --- Moderate hint nudging ---
	if hint.Type != "" && hint.Confidence >= 0.5 {
		switch hint.Type {
		case "aircraft":
			aircraftScore += 1.5
		case "vehicle":
			vehicleScore += 1.5
		case "foot":
			footScore += 1.5
		}
	}
	
	// --- Pick winner ---
	type scored struct {
		label string
		score float64
	}
	scores := []scored{
		{"aircraft", aircraftScore},
		{"vehicle", vehicleScore},
		{"foot", footScore},
	}
	sort.Slice(scores, func(i, j int) bool { return scores[i].score > scores[j].score })
	
	result.MovementType = scores[0].label
	
	// Confidence based on margin between top two scores
	topScore := scores[0].score
	runnerUp := scores[1].score
	if topScore == 0 {
		// No evidence at all — default to foot with low confidence
		result.MovementType = "foot"
		result.Confidence = 0.5
	} else {
		margin := (topScore - runnerUp) / topScore
		// Map margin to confidence: margin 0 -> 0.5, margin 1 -> 0.99
		result.Confidence = 0.5 + margin*0.49
		if result.Confidence > 0.99 {
			result.Confidence = 0.99
		}
	}
	
	// === ACTIVITY TYPE CLASSIFICATION ===
	result.ActivityType = classifyActivityType(result.MovementType, speed, smooth, bearingVar, linear, metrics)
	
	return result
}

// classifyActivityType determines the activity type from trajectory metrics.
func classifyActivityType(movementType string, speed, smooth, bearingVar, linear float64, metrics MovementMetrics) string {
	switch movementType {
	case "foot":
		if speed >= 0.5 && speed <= 4 {
			return "reconnaissance"
		}
		if metrics.StopFrequency > 0.3 || bearingVar > 0.5 {
			return "reconnaissance"
		}
		return "patrol"
		
	case "vehicle":
		if speed > 60 && smooth > 0.6 {
			return "transit"
		}
		if speed > 40 && smooth > 0.7 && linear > 0.8 {
			return "transit"
		}
		return "patrol"
		
	case "aircraft":
		if linear > 0.8 && smooth > 0.7 {
			return "logistics"
		}
		if bearingVar > 0.2 || metrics.StopFrequency > 0.1 {
			return "patrol"
		}
		return "logistics"
	}
	return "patrol"
}

// ClassifyMovementAdvanced returns just the movement type (for backward compatibility)
func ClassifyMovementAdvanced(points []Point) string {
	return ClassifyMovementFull(points).MovementType
}

// ClassifyMovementAdvancedWithHint returns movement type using hints.
func ClassifyMovementAdvancedWithHint(points []Point, hint MovementHint) string {
	return ClassifyMovementFullWithHint(points, hint).MovementType
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
