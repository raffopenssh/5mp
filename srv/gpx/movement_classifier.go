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

	// Total distance along the track
	TotalDistanceKm float64

	// Duration in minutes
	DurationMinutes float64

	// Number of input points (useful for confidence thresholds)
	NumPoints int

	// Start/end segment speeds (km/h) — first/last segment speed.
	// Vehicles typically start from ~0 (accelerating) and end at ~0 (braking).
	// Boats maintain cruising speed throughout.
	StartSpeedKmh float64
	EndSpeedKmh   float64

	// Subtype classification metrics
	MeanTurnAngleDeg  float64 // Average bearing change between segments (degrees)
	SharpTurnRatio    float64 // Proportion of turns > 45 degrees
	HoverRatio        float64 // Proportion of points with speed < 2 km/h (for aircraft: hover detection)
	SpeedCV           float64 // Coefficient of variation of speed (stddev/mean), uncapped
	ElevationStdDevM  float64 // Standard deviation of elevation (meters)
	MaxClimbRateMps   float64 // Maximum climb/descent rate (meters per second)
	AvgClimbRateMps   float64 // Average absolute climb/descent rate (meters per second)

	// Takeoff/landing roll distance (meters) and acceleration.
	// Fixed-wing: gradual acceleration over 300-1500m of runway, many GPS intervals.
	// Helicopter: near-vertical liftoff, 0→flight speed in 1-2 GPS intervals (<100m).
	TakeoffRollM      float64 // Distance from <10 km/h to >80 km/h at track start
	LandingRollM      float64 // Distance from >80 km/h to <10 km/h at track end
	TakeoffAccelKmhs  float64 // Peak acceleration during takeoff (km/h per second)
	LandingDecelKmhs  float64 // Peak deceleration during landing (km/h per second)
}

// MovementClassification contains both movement type and activity type
type MovementClassification struct {
	MovementType     string  // "foot", "vehicle", "aircraft"
	MovementSubtype  string  // "boat", "fixed_wing", "rotor_wing", or "" (no subtype)
	ActivityType     string  // "patrol", "reconnaissance", "transit", "logistics"
	Confidence       float64 // 0-1 confidence in classification
	SubtypeConfidence float64 // 0-1 confidence in subtype classification
	Metrics          MovementMetrics
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
	var climbRates []float64 // vertical speed in m/s (positive=up, absolute value used for stats)
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
				elevDiff := math.Abs(*pt.Elevation - *prevElevation)
				totalElevChange += elevDiff
				// Compute climb rate if we have time data
				if i > 0 && points[i-1].Time != nil && pt.Time != nil {
					dtSec := pt.Time.Sub(*points[i-1].Time).Seconds()
					if dtSec > 0 {
						climbRates = append(climbRates, elevDiff/dtSec)
					}
				}
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
			cv := math.Sqrt(variance) / metrics.AvgSpeedKmh
			metrics.SpeedCV = cv // uncapped for subtype classification
			metrics.SpeedVariance = cv
			if metrics.SpeedVariance > 1 {
				metrics.SpeedVariance = 1
			}
		}

		// Hover ratio: proportion of ALL speed samples (including stops) near zero
		// Used for rotor-wing detection (hovering)
		var hoverCount int
		totalSpeedSamples := len(speeds) + stopCount
		for _, s := range speeds {
			if s < 2 {
				hoverCount++
			}
		}
		hoverCount += stopCount // stops are also "hovering" for aircraft
		if totalSpeedSamples > 0 {
			metrics.HoverRatio = float64(hoverCount) / float64(totalSpeedSamples)
		}
	}
	
	// Bearing variance and turn angle metrics
	if len(bearingChanges) > 0 {
		var sum float64
		var sharpCount int
		for _, bc := range bearingChanges {
			sum += bc
			if bc > 45 {
				sharpCount++
			}
		}
		avgBearingChange := sum / float64(len(bearingChanges))
		metrics.BearingVariance = avgBearingChange / 90.0
		if metrics.BearingVariance > 1 {
			metrics.BearingVariance = 1
		}
		metrics.MeanTurnAngleDeg = avgBearingChange
		metrics.SharpTurnRatio = float64(sharpCount) / float64(len(bearingChanges))
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
	
	// Detect landing/takeoff patterns, measure roll distance and acceleration.
	// Helicopter: 0→flight speed in 1-2 GPS intervals, high acceleration (>5 km/h/s).
	// Fixed-wing: gradual acceleration over many intervals, lower peak accel.
	if len(points) >= 6 {
		// Takeoff: scan from start, find transition from <10 to >80 km/h
		var takeoffDist float64
		var inTakeoff bool
		var prevTakeoffSpeed float64
		var peakAccel float64
		for i := 1; i < len(points) && i < len(points)/2; i++ {
			dist := haversineDistance(points[i-1], points[i])
			var spd float64
			var dtSec float64
			if points[i-1].Time != nil && points[i].Time != nil {
				dtSec = points[i].Time.Sub(*points[i-1].Time).Seconds()
				if dtSec > 0 {
					spd = dist / (dtSec / 3600) // km/h
				}
			}
			if !inTakeoff && spd < 10 {
				prevTakeoffSpeed = spd
				continue // still on ground
			}
			if !inTakeoff && spd >= 10 {
				inTakeoff = true
			}
			if inTakeoff {
				takeoffDist += dist
				// Track peak acceleration
				if dtSec > 0 {
					accel := (spd - prevTakeoffSpeed) / dtSec // km/h per second
					if accel > peakAccel {
						peakAccel = accel
					}
				}
				prevTakeoffSpeed = spd
				if spd > 80 {
					metrics.HasTakeoffPattern = true
					metrics.TakeoffRollM = takeoffDist * 1000
					metrics.TakeoffAccelKmhs = peakAccel
					break
				}
			}
		}

		// Landing: scan from end, find transition from >80 to <10 km/h
		var landingDist float64
		var inLanding bool
		var prevLandSpeed float64
		var peakDecel float64
		for i := len(points) - 1; i > len(points)/2; i-- {
			dist := haversineDistance(points[i-1], points[i])
			var spd float64
			var dtSec float64
			if points[i-1].Time != nil && points[i].Time != nil {
				dtSec = points[i].Time.Sub(*points[i-1].Time).Seconds()
				if dtSec > 0 {
					spd = dist / (dtSec / 3600)
				}
			}
			if !inLanding && spd < 10 {
				prevLandSpeed = spd
				continue
			}
			if !inLanding && spd >= 10 && spd <= 80 {
				inLanding = true
				prevLandSpeed = spd
			}
			if inLanding {
				landingDist += dist
				if dtSec > 0 {
					decel := (spd - prevLandSpeed) / dtSec
					if decel > peakDecel {
						peakDecel = decel
					}
				}
				prevLandSpeed = spd
				if spd > 80 {
					metrics.HasLandingPattern = true
					metrics.LandingRollM = landingDist * 1000
					metrics.LandingDecelKmhs = peakDecel
					break
				}
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

		// Elevation standard deviation
		if len(elevations) > 1 {
			var elevVar float64
			for _, e := range elevations {
				diff := e - metrics.AvgElevationM
				elevVar += diff * diff
			}
			elevVar /= float64(len(elevations))
			metrics.ElevationStdDevM = math.Sqrt(elevVar)
		}

		// Climb rate statistics
		if len(climbRates) > 0 {
			var sumCR, maxCR float64
			for _, cr := range climbRates {
				sumCR += cr
				if cr > maxCR {
					maxCR = cr
				}
			}
			metrics.AvgClimbRateMps = sumCR / float64(len(climbRates))
			metrics.MaxClimbRateMps = maxCR
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

	// Total distance along the track
	metrics.TotalDistanceKm = totalDist

	// Number of input points
	metrics.NumPoints = len(points)

	// Duration from first to last point
	if len(points) >= 2 && points[0].Time != nil && points[len(points)-1].Time != nil {
		metrics.DurationMinutes = points[len(points)-1].Time.Sub(*points[0].Time).Minutes()
	}

	// Start and end segment speeds
	if len(points) >= 2 {
		if points[0].Time != nil && points[1].Time != nil {
			dt := points[1].Time.Sub(*points[0].Time).Hours()
			if dt > 0 {
				d := haversineDistance(points[0], points[1])
				metrics.StartSpeedKmh = d / dt
			}
		}
		last := len(points) - 1
		if points[last-1].Time != nil && points[last].Time != nil {
			dt := points[last].Time.Sub(*points[last-1].Time).Hours()
			if dt > 0 {
				d := haversineDistance(points[last-1], points[last])
				metrics.EndSpeedKmh = d / dt
			}
		}
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
		result.MovementSubtype, result.SubtypeConfidence = ClassifyMovementSubtype(result.MovementType, metrics, hint)
		return result
	}

	// === STRONG HINT (confidence >= 0.9) ===
	// ER ranger subtype, strong Locus tags. Trust with light sanity check.
	if hint.Type != "" && hint.Confidence >= 0.9 {
		switch hint.Type {
		case "aircraft":
			if speed < 8 {
				// Locus tags "transport_airplane" on a pilot's phone even when
				// walking on the ground. Override to foot at walking speed.
				result.MovementType = "foot"
				result.Confidence = 0.7
			} else if speed < 30 {
				// Slow movement — likely vehicle (taxi, ground transport)
				result.MovementType = "vehicle"
				result.Confidence = 0.6
			} else {
				result.MovementType = "aircraft"
				result.Confidence = 0.95
			}
		case "vehicle":
			if speed > 120 {
				// Vehicle GPS but speed says aircraft (GPS on a plane?)
				result.MovementType = "aircraft"
				result.Confidence = 0.7
			} else {
				result.MovementType = "vehicle"
				result.Confidence = 0.95
			}
		case "foot":
			if speed > 80 {
				// Ranger phone in an aircraft
				result.MovementType = "aircraft"
				result.Confidence = 0.8
			} else if speed > 10 {
				// Ranger going >10 km/h = in a vehicle (sustained 10 km/h on foot
				// is competitive running pace, not realistic for patrol)
				result.MovementType = "vehicle"
				result.Confidence = 0.8
			} else {
				result.MovementType = "foot"
				result.Confidence = 0.95
			}
		}
		result.ActivityType = classifyActivityType(result.MovementType, speed, smooth, bearingVar, linear, metrics)
		result.MovementSubtype, result.SubtypeConfidence = ClassifyMovementSubtype(result.MovementType, metrics, hint)
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
	
	// P90 speed 20-80 km/h AND avg speed 8-80 (strong)
	// In African conservation context, vehicles rarely exceed 80 km/h on unpaved roads.
	if p90 >= 20 && p90 <= 80 && speed >= 8 && speed <= 80 {
		vehicleScore += 3.0
	}
	// P90 80-120 AND speed 60-100 (weaker — could be fast vehicle on paved road)
	if p90 > 80 && p90 <= 120 && speed >= 60 && speed <= 100 {
		vehicleScore += 1.0
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
	result.MovementSubtype, result.SubtypeConfidence = ClassifyMovementSubtype(result.MovementType, metrics, hint)
	
	return result
}

// ClassifyMovementSubtype determines a finer-grained movement subtype from metrics and hints.
// Returns: "boat", "fixed_wing", "rotor_wing", or "" (no subtype determined).
// The confidence return value indicates how certain the subtype classification is.
//
// Decision rules:
//   Boat vs vehicle: boats have capped speed (typically <50 km/h), very steady speed
//   (low CV), smooth turns, and no sharp acceleration. Works without elevation data.
//   Fixed-wing vs rotor-wing: rotor-wing can hover, has more speed variance, more
//   turns, and (with elevation data) dramatic climb/descent rates. Fixed-wing is
//   faster, more linear, steadier speed.
func ClassifyMovementSubtype(movementType string, metrics MovementMetrics, hint MovementHint) (string, float64) {
	switch movementType {
	case "vehicle":
		return classifyVehicleSubtype(metrics, hint)
	case "aircraft":
		return classifyAircraftSubtype(metrics, hint)
	default:
		return "", 0
	}
}

// classifyVehicleSubtype distinguishes boats from ground vehicles.
//
// The key challenge: a vehicle cruising steadily on a smooth road looks
// very similar to a boat — especially with sparse GPS data (120s intervals
// from EarthRanger trackers) where short driving segments between stops
// appear artificially smooth with zero stop frequency.
//
// Critical insight: with only 3-6 points, "no stops" and "low CV" are
// meaningless — there aren't enough samples to distinguish anything.
// Real boat tracks have 50+ points over 30+ minutes of continuous travel.
//
// Signals used:
//   - Boats NEVER stop mid-journey (no intersections on water) — but only
//     meaningful with enough data points (≥20)
//   - Boats maintain steady cruising speed (low CV) — requires ≥15 speed samples
//   - Boats maintain minimum speed (P10 > 15 km/h) — requires ≥15 speed samples
//   - Vehicles start/end at rest (0 km/h) — boats maintain speed at track edges
//   - Elevation changes indicate terrain (not water)
func classifyVehicleSubtype(metrics MovementMetrics, hint MovementHint) (string, float64) {
	// Authoritative ER hint: device is on a boat
	if hint.SubtypeHint == "boat" {
		return "boat", 1.0
	}

	speed := metrics.AvgSpeedKmh
	p90 := metrics.P90SpeedKmh
	p10 := metrics.P10SpeedKmh
	nPts := metrics.NumPoints

	// === HARD GATE: minimum data requirements ===
	// With <15 points (typical for sparse ER vehicle trackers at 120s intervals),
	// we cannot reliably distinguish boat from vehicle. Metrics like stop frequency,
	// speed CV, and P10 are statistically meaningless with so few samples.
	if nPts < 15 {
		return "", 0
	}

	// === HARD GATE: minimum duration ===
	// Boats operate for extended periods. A 5-minute drive segment is not a boat.
	if metrics.DurationMinutes < 20.0 {
		return "", 0
	}

	// === HARD GATE: speed range ===
	// Boats rarely exceed 50 km/h on patrol.
	if speed > 45 || p90 > 60 {
		return "", 0
	}
	if speed < 10 {
		return "", 0 // too slow — drifting or idling, not powered boat travel
	}

	// Boats: 10-45 km/h, very steady cruising speed, smooth turns, gentle accel.
	var boatScore float64

	// Speed in typical boat range
	if speed >= 10 && speed <= 45 && p90 < 55 {
		boatScore += 1.0
	}

	// === START/END SPEED ANALYSIS (strongest new signal) ===
	// Vehicles start from rest (gate, camp, junction) and end at rest.
	// Between two stops, a vehicle GPS segment starts at >0 because the
	// 120s tracker missed the acceleration — but the FIRST point is slower.
	// Boats maintain full cruising speed at both ends of the track.
	startSlow := metrics.StartSpeedKmh < 5.0 // started from near-stop
	endSlow := metrics.EndSpeedKmh < 5.0     // ended at near-stop

	if startSlow || endSlow {
		// Vehicle behavior: starting from or ending at rest.
		boatScore -= 3.0
	} else if metrics.StartSpeedKmh > 10 && metrics.EndSpeedKmh > 10 {
		// Both ends at cruising speed — consistent with boat
		boatScore += 1.0
	}

	// === NO STOPS (requires enough data) ===
	// With ≥20 points, zero stops is meaningful.
	// Scale the signal by point count — more points = more trust.
	if nPts >= 20 {
		if metrics.StopFrequency < 0.01 {
			boatScore += 2.0
		} else if metrics.StopFrequency < 0.03 {
			boatScore += 1.0
		}
	} else {
		// 15-19 points: reduced signal
		if metrics.StopFrequency < 0.01 {
			boatScore += 0.5
		}
	}
	if metrics.StopFrequency > 0.05 {
		boatScore -= 2.0 // vehicles stop frequently
	}

	// === P10 speed: boats maintain minimum cruising speed ===
	// Only meaningful with enough speed samples.
	if nPts >= 20 {
		if p10 > 15 {
			boatScore += 1.5
		} else if p10 > 10 {
			boatScore += 0.5
		}
	}
	if p10 < 5 {
		boatScore -= 1.5 // dips to near-stop = vehicle behavior
	}

	// === Speed CV: steady cruising ===
	// Only reliable with enough samples. With 3 points you always get low CV.
	if nPts >= 20 {
		if metrics.SpeedCV < 0.15 {
			boatScore += 1.5
		} else if metrics.SpeedCV < 0.22 {
			boatScore += 0.5
		}
	}
	if metrics.SpeedCV > 0.35 {
		boatScore -= 1.0
	}

	// Smooth turns: boats make gentle, sweeping turns
	if metrics.MeanTurnAngleDeg < 4 && metrics.SharpTurnRatio < 0.01 {
		boatScore += 1.0
	}
	// Sharp turns = not a boat (intersections, parking maneuvers)
	if metrics.SharpTurnRatio > 0.10 {
		boatScore -= 1.0
	}

	// Acceleration pattern: boats have gentle, gradual speed changes.
	if metrics.AccelerationScore < 0.10 {
		boatScore += 0.5
	} else if metrics.AccelerationScore > 0.25 {
		boatScore -= 1.0 // sharp accel/decel = vehicle stop-go
	}

	// Elevation evidence (if available): boats at constant altitude (~water level)
	if metrics.HasElevation {
		if metrics.ElevationStdDevM < 5 && metrics.ElevationRangeM < 30 {
			boatScore += 1.5 // very flat = water surface
		} else if metrics.ElevationStdDevM < 10 && metrics.ElevationRangeM < 50 {
			boatScore += 0.5
		} else if metrics.ElevationRangeM > 80 {
			boatScore -= 2.0 // significant altitude changes = terrain = not water
		} else if metrics.ElevationRangeM > 50 {
			boatScore -= 1.0
		}
	}

	// Track name hint (weak — ER tracks have UUID names, not descriptive)
	if hint.SubtypeHint == "boat_name_hint" {
		boatScore += 1.0
	}

	// Require strong evidence
	if boatScore >= 6.0 {
		return "boat", 0.9
	} else if boatScore >= 5.0 {
		return "boat", 0.7
	}

	return "", 0
}

// classifyAircraftSubtype distinguishes fixed-wing from rotor-wing (helicopter).
//
// The strongest signal is takeoff/landing roll distance:
//   - Fixed-wing needs 300-1500m of runway to accelerate to flight speed.
//   - Helicopter lifts off vertically: 0→flight speed in <100m.
// This works even without elevation data.
func classifyAircraftSubtype(metrics MovementMetrics, hint MovementHint) (string, float64) {
	// Authoritative ER hints
	switch hint.SubtypeHint {
	case "helicopter":
		return "rotor_wing", 1.0
	case "fixed_wing":
		return "fixed_wing", 1.0
	}

	speed := metrics.AvgSpeedKmh
	p90 := metrics.P90SpeedKmh

	var fixedScore, rotorScore float64

	// === STRONGEST SIGNAL: Takeoff/landing acceleration ===
	// Helicopter: explosive acceleration >5 km/h/s (0→100 in ~20s).
	// Fixed-wing: gradual acceleration <3 km/h/s (needs long runway roll).
	// Also check roll distance, but acceleration is more reliable since
	// helicopter ground track during rapid climb can still be 200-400m.
	rollDetected := false
	if metrics.HasTakeoffPattern {
		if metrics.TakeoffAccelKmhs > 5.0 {
			rotorScore += 4.0 // explosive accel = rotor-wing
			rollDetected = true
		} else if metrics.TakeoffAccelKmhs < 2.5 && metrics.TakeoffRollM > 400 {
			fixedScore += 4.0 // slow accel + long roll = fixed-wing
			rollDetected = true
		}
	}
	if metrics.HasLandingPattern {
		if metrics.LandingDecelKmhs > 5.0 {
			rotorScore += 4.0 // rapid decel = rotor-wing
			rollDetected = true
		} else if metrics.LandingDecelKmhs < 2.5 && metrics.LandingRollM > 400 {
			fixedScore += 4.0
			rollDetected = true
		}
	}

	// === Speed signals ===
	// Fixed-wing typically >100 km/h, rotor-wing 40-200 km/h
	if speed > 150 {
		fixedScore += 2.0
	} else if speed > 100 && p90 > 150 {
		fixedScore += 1.5
	} else if speed >= 40 && speed <= 120 {
		rotorScore += 1.0
	}

	// Speed variability: rotor-wing much more variable (hover, accel, decel)
	if metrics.SpeedCV > 0.45 {
		rotorScore += 2.0
	} else if metrics.SpeedCV > 0.30 {
		rotorScore += 1.0
	} else if metrics.SpeedCV < 0.20 {
		fixedScore += 1.5
	}

	// Hover detection: only rotor-wing can hover in place
	if metrics.HoverRatio > 0.05 {
		rotorScore += 2.0
	} else if metrics.HoverRatio > 0.02 {
		rotorScore += 1.0
	}

	// === Turn patterns ===
	// Rotor-wing is more maneuverable
	if metrics.SharpTurnRatio > 0.05 {
		rotorScore += 1.5
	} else if metrics.SharpTurnRatio > 0.02 {
		rotorScore += 0.5
	}
	if metrics.MeanTurnAngleDeg > 10 {
		rotorScore += 1.0
	} else if metrics.MeanTurnAngleDeg < 5 {
		fixedScore += 1.0
	}

	// Linearity: fixed-wing flies straighter
	if metrics.LinearityScore > 0.7 {
		fixedScore += 1.0
	} else if metrics.LinearityScore < 0.5 {
		rotorScore += 1.0
	}

	// === Elevation evidence (if available) ===
	if metrics.HasElevation {
		// Rotor-wing has dramatic climb/descent rates
		if metrics.MaxClimbRateMps > 2.0 {
			rotorScore += 1.5
		}
		if metrics.AvgClimbRateMps > 0.5 {
			rotorScore += 1.0
		}
	}

	// === Track name hints ===
	switch hint.SubtypeHint {
	case "helicopter_name_hint":
		rotorScore += 2.0
	case "fixed_wing_name_hint":
		fixedScore += 2.0
	}

	// Pick winner — lower threshold if roll distance was decisive
	minScore := 3.0
	if rollDetected {
		minScore = 2.0 // roll alone is sufficient
	}

	if fixedScore > rotorScore && fixedScore >= minScore {
		conf := 0.6 + math.Min((fixedScore-rotorScore)/fixedScore, 1.0)*0.3
		return "fixed_wing", conf
	} else if rotorScore > fixedScore && rotorScore >= minScore {
		conf := 0.6 + math.Min((rotorScore-fixedScore)/rotorScore, 1.0)*0.3
		return "rotor_wing", conf
	}

	return "", 0
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
		dist := metrics.TotalDistanceKm
		dur := metrics.DurationMinutes

		// Short segments (< 5 km or < 5 min) are normal survey flight behaviour:
		// turns, repositioning between transect lines, brief straight legs.
		// Never classify these as logistics.
		if dist < 5 || dur < 5 {
			return "patrol"
		}

		// Clear patrol indicators: erratic bearing or frequent stops
		if bearingVar > 0.2 || metrics.StopFrequency > 0.1 {
			return "patrol"
		}

		// Elevation-based classification when data is available.
		// Survey/patrol flights fly low (terrain-following, 100-500m AGL)
		// with variable altitude. Transport flights cruise high and steady.
		if metrics.HasElevation {
			// High elevation change rate = terrain following = survey
			// (>30 m change per km of horizontal travel)
			if metrics.ElevationChangeRate > 30 {
				return "patrol"
			}
			// Low average elevation with meaningful range = survey
			// Survey flights in Africa typically < 1500m ASL with undulations
			if metrics.AvgElevationM < 1500 && metrics.ElevationRangeM > 50 {
				return "patrol"
			}
			// Very high and steady = cruise altitude = logistics
			if metrics.AvgElevationM > 2500 && metrics.ElevationRangeM < 200 {
				if dist >= 10 && linear > 0.7 {
					return "logistics"
				}
			}
		}

		// Logistics = sustained straight-line flight over significant distance.
		// Require BOTH high linearity AND sufficient length to distinguish
		// point-to-point transport from a survey leg.
		if dist >= 15 && linear > 0.8 && smooth > 0.7 {
			return "logistics"
		}

		// Medium distance (5-15 km): only flag as logistics if very straight
		// and fast, indicating repositioning rather than area coverage.
		if dist >= 5 && linear > 0.9 && smooth > 0.8 && speed > 120 {
			return "logistics"
		}

		// Default: aircraft segments are patrol/survey
		return "patrol"
	}
	return "patrol"
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

// ClassifyMovementAdvanced returns just the movement type (for backward compatibility)
func ClassifyMovementAdvanced(points []Point) string {
	return ClassifyMovementFull(points).MovementType
}

// ClassifyMovementAdvancedWithHint returns movement type using hints.
func ClassifyMovementAdvancedWithHint(points []Point, hint MovementHint) string {
	return ClassifyMovementFullWithHint(points, hint).MovementType
}
