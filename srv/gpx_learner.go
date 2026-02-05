// Package srv provides GPX learning and pattern detection for conservation monitoring.
package srv

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"sort"
	"strings"
	"sync"
	"time"

	"srv.exe.dev/db/dbgen"
	"srv.exe.dev/srv/gpx"
)

// Auto-approval thresholds for learned features
const (
	// AutoApprovalConfidenceThreshold is the minimum confidence (%) to auto-approve
	AutoApprovalConfidenceThreshold = 90.0
	// AutoApprovalMinTraversals is the minimum number of times a road must be traversed
	AutoApprovalMinTraversals = 5
	// AutoApprovalMinLandings is the minimum landings/takeoffs for airstrip auto-approval
	AutoApprovalMinLandings = 5
)

// ptr helpers for sqlc nullable types
func ptrFloat64(v float64) *float64 { return &v }
func ptrInt64(v int64) *int64       { return &v }
func ptrString(v string) *string    { return &v }

// GPXLearner processes uploaded GPX data to learn roads, places, and patterns
type GPXLearner struct {
	db      *sql.DB
	queries *dbgen.Queries
	mu      sync.Mutex
	running bool
	stopCh  chan struct{}
}

// NewGPXLearner creates a new learner instance
func NewGPXLearner(db *sql.DB) *GPXLearner {
	return &GPXLearner{
		db:      db,
		queries: dbgen.New(db),
		stopCh:  make(chan struct{}),
	}
}

// Start begins background processing of the learning queue
func (l *GPXLearner) Start() {
	l.mu.Lock()
	if l.running {
		l.mu.Unlock()
		return
	}
	l.running = true
	l.mu.Unlock()

	go l.processLoop()
	slog.Info("GPX learner started")
}

// Stop halts background processing
func (l *GPXLearner) Stop() {
	l.mu.Lock()
	if !l.running {
		l.mu.Unlock()
		return
	}
	l.running = false
	l.mu.Unlock()
	close(l.stopCh)
	slog.Info("GPX learner stopped")
}

func (l *GPXLearner) processLoop() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-l.stopCh:
			return
		case <-ticker.C:
			l.processQueue()
		}
	}
}

func (l *GPXLearner) processQueue() {
	ctx := context.Background()

	jobs, err := l.queries.GetPendingLearningJobs(ctx, 5)
	if err != nil {
		slog.Error("failed to get pending learning jobs", "error", err)
		return
	}

	for _, job := range jobs {
		if err := l.queries.StartLearningJob(ctx, job.ID); err != nil {
			slog.Error("failed to start learning job", "id", job.ID, "error", err)
			continue
		}

		if err := l.processJob(ctx, job); err != nil {
			slog.Error("learning job failed", "id", job.ID, "error", err)
			errMsg := err.Error()
			l.queries.FailLearningJob(ctx, dbgen.FailLearningJobParams{
				ErrorMessage: &errMsg,
				ID:           job.ID,
			})
			continue
		}

		l.queries.CompleteLearningJob(ctx, job.ID)
		slog.Info("learning job completed", "id", job.ID, "park", job.ParkID)
	}
}

// LearningResult contains the results of processing a GPX upload
type LearningResult struct {
	UploadID           int64          `json:"upload_id"`
	ParkID             string         `json:"park_id"`
	ParkName           string         `json:"park_name"`
	VehicleMedianSpeed float64        `json:"vehicle_median_speed_kmh"`
	VehicleMaxSpeed    float64        `json:"vehicle_max_speed_kmh"`
	FootMedianSpeed    float64        `json:"foot_median_speed_kmh"`
	FootMaxSpeed       float64        `json:"foot_max_speed_kmh"`
	FootMCPArea        float64        `json:"foot_mcp_area_km2"`
	NewRoads           int            `json:"new_roads_found"`
	NewRoadsKm         float64        `json:"new_roads_km"`
	RoadConfidence     float64        `json:"road_confidence_pct"`
	NewAirstrips       int            `json:"new_airstrips_found"`
	AirstripConfidence float64        `json:"airstrip_confidence_pct"`
	NewPlaces          int            `json:"new_places_found"`
	PlaceTypes         map[string]int `json:"place_types"`
	PlaceConfidence    float64        `json:"place_confidence_pct"`
	Summary            string         `json:"summary"`
}

// SpeedSample holds speed data for statistical analysis
type SpeedSample struct {
	SpeedKmh float64
	Duration time.Duration
}

// AircraftPattern represents approach/departure pattern
type AircraftPattern struct {
	StartLat float64     `json:"start_lat"`
	StartLon float64     `json:"start_lon"`
	EndLat   float64     `json:"end_lat"`
	EndLon   float64     `json:"end_lon"`
	AvgSpeed float64     `json:"avg_speed_kmh"`
	Points   [][]float64 `json:"points"` // [[lon, lat], ...]
}

// StopPoint represents a detected stop location
type StopPoint struct {
	Lat      float64
	Lon      float64
	Duration time.Duration
	Arrivals int
}

func (l *GPXLearner) processJob(ctx context.Context, job dbgen.GpxLearningQueue) error {
	parkID := ""
	if job.ParkID != nil {
		parkID = *job.ParkID
	}
	if parkID == "" {
		return nil // No park associated, skip
	}

	uploadID := int64(0)
	if job.UploadID != nil {
		uploadID = *job.UploadID
	}

	// Get the upload log data (which has classified segments)
	var analysisJSON sql.NullString
	err := l.db.QueryRowContext(ctx,
		"SELECT classified_segments_json FROM gpx_upload_logs WHERE upload_id = ?",
		uploadID).Scan(&analysisJSON)
	if err != nil || !analysisJSON.Valid {
		slog.Debug("no classified segments found", "upload_id", uploadID)
		return nil
	}

	var segments []ClassifiedSegment
	if err := json.Unmarshal([]byte(analysisJSON.String), &segments); err != nil {
		slog.Warn("failed to parse segments", "error", err)
		return nil
	}

	if len(segments) == 0 {
		return nil // No segments to learn from
	}

	result := &LearningResult{
		UploadID:   uploadID,
		ParkID:     parkID,
		PlaceTypes: make(map[string]int),
	}

	// Get park name
	var parkName sql.NullString
	l.db.QueryRowContext(ctx, "SELECT name FROM parks WHERE id = ?", parkID).Scan(&parkName)
	if parkName.Valid {
		result.ParkName = parkName.String
	} else {
		result.ParkName = parkID
	}

	// Collect all speeds for statistics
	var vehicleSpeeds, footSpeeds []float64
	var footPoints []gpx.Point // For MCP calculation

	// Process each segment
	for _, seg := range segments {
		// Collect speeds by movement type
		if seg.AvgSpeedKmh > 0 {
			movementType := seg.MovementType
			if movementType == "" {
				movementType = inferMovementType(seg.AvgSpeedKmh)
			}

			if movementType == "foot" {
				footSpeeds = append(footSpeeds, seg.AvgSpeedKmh)
				// Collect points for MCP
				if len(seg.Points) > 0 {
					footPoints = append(footPoints, seg.Points...)
				}
			} else if movementType == "vehicle" {
				vehicleSpeeds = append(vehicleSpeeds, seg.AvgSpeedKmh)
			}
		}

		// Process by classification
		switch seg.Classification {
		case "patrol":
			l.processPatrolSegment(ctx, parkID, seg, result)
		case "road":
			l.processRoadSegment(ctx, parkID, uploadID, seg, result)
		case "boundary":
			l.processBoundarySegment(ctx, parkID, seg, result)
		}

		// Check for aircraft patterns
		if seg.MovementType == "aircraft" && len(seg.Points) > 10 {
			l.processAircraftSegment(ctx, parkID, uploadID, seg, result)
		}

		// Store vehicle tracks (simplified)
		if seg.MovementType == "vehicle" && seg.GeoJSON != "" {
			l.storeVehicleTrack(ctx, parkID, uploadID, seg)
		}

		// Detect stops for place learning
		if len(seg.Points) > 3 {
			stops := l.detectStops(seg)
			for _, stop := range stops {
				l.processStop(ctx, parkID, stop, result)
			}
		}
	}

	// Calculate aggregate statistics
	if len(vehicleSpeeds) > 0 {
		result.VehicleMedianSpeed = median(vehicleSpeeds)
		result.VehicleMaxSpeed = maxFloat(vehicleSpeeds)
		
		// Store aggregate vehicle stats
		l.queries.UpsertVehicleStats(ctx, dbgen.UpsertVehicleStatsParams{
			ParkID:          parkID,
			MovementType:    "vehicle",
			TotalDistanceKm: ptrFloat64(sumDistances(segments, "vehicle")),
			TotalTimeHours:  ptrFloat64(sumDurations(segments, "vehicle")),
			MedianSpeedKmh:  ptrFloat64(result.VehicleMedianSpeed),
			MaxSpeedKmh:     ptrFloat64(result.VehicleMaxSpeed),
			P90SpeedKmh:     ptrFloat64(percentile(vehicleSpeeds, 90)),
			SampleCount:     ptrInt64(int64(len(vehicleSpeeds))),
		})
	}

	if len(footSpeeds) > 0 {
		result.FootMedianSpeed = median(footSpeeds)
		result.FootMaxSpeed = maxFloat(footSpeeds)
		
		l.queries.UpsertVehicleStats(ctx, dbgen.UpsertVehicleStatsParams{
			ParkID:          parkID,
			MovementType:    "foot",
			TotalDistanceKm: ptrFloat64(sumDistances(segments, "foot")),
			TotalTimeHours:  ptrFloat64(sumDurations(segments, "foot")),
			MedianSpeedKmh:  ptrFloat64(result.FootMedianSpeed),
			MaxSpeedKmh:     ptrFloat64(result.FootMaxSpeed),
			P90SpeedKmh:     ptrFloat64(percentile(footSpeeds, 90)),
			SampleCount:     ptrInt64(int64(len(footSpeeds))),
		})
	}

	// Calculate 90% MCP for foot patrols
	if len(footPoints) > 10 {
		result.FootMCPArea = calculateMCP90(footPoints)
	}

	// Generate summary
	result.Summary = l.generateSummary(result)

	// Store the learning result
	return l.storeLearningResult(ctx, result)
}

func inferMovementType(speedKmh float64) string {
	if speedKmh < 8 {
		return "foot"
	} else if speedKmh < 100 {
		return "vehicle"
	}
	return "aircraft"
}

func sumDistances(segments []ClassifiedSegment, movementType string) float64 {
	var total float64
	for _, seg := range segments {
		mt := seg.MovementType
		if mt == "" {
			mt = inferMovementType(seg.AvgSpeedKmh)
		}
		if mt == movementType {
			total += seg.DistanceKm
		}
	}
	return total
}

func sumDurations(segments []ClassifiedSegment, movementType string) float64 {
	var total float64
	for _, seg := range segments {
		mt := seg.MovementType
		if mt == "" {
			mt = inferMovementType(seg.AvgSpeedKmh)
		}
		if mt == movementType {
			total += seg.Duration.Hours()
		}
	}
	return total
}

func (l *GPXLearner) processPatrolSegment(ctx context.Context, parkID string, seg ClassifiedSegment, result *LearningResult) {
	// Patrol segments contribute to effort mapping - already handled by upload
	// Here we just track statistics which are aggregated at the job level
}

func (l *GPXLearner) processRoadSegment(ctx context.Context, parkID string, uploadID int64, seg ClassifiedSegment, result *LearningResult) {
	// Get coordinates from GeoJSON
	coords := l.parseGeoJSONCoords(seg.GeoJSON)
	if len(coords) < 2 {
		return
	}

	// Simplify to 10m resolution
	simplified := l.simplifyCoords(coords, 10.0)
	if len(simplified) < 2 {
		return
	}

	// Check if this matches existing vehicle tracks (±20m)
	matched, matchID := l.findMatchingTrack(ctx, parkID, simplified)

	if matched {
		// Increment match count
		var matchCount int64
		var existingGeojson string
		var lengthM float64
		l.db.QueryRowContext(ctx, "SELECT match_count, geojson, COALESCE(length_m, 0) FROM learned_roads WHERE id = ?", matchID).Scan(&matchCount, &existingGeojson, &lengthM)
		newMatchCount := matchCount + 1
		confidence := math.Min(float64(newMatchCount)*25.0, 95.0) // 25% per match, max 95%

		l.queries.UpdateLearnedRoadMatch(ctx, dbgen.UpdateLearnedRoadMatchParams{
			ConfidencePct: ptrFloat64(confidence),
			ID:            matchID,
		})

		if newMatchCount >= 2 {
			result.NewRoads++
			result.NewRoadsKm += seg.DistanceKm
			result.RoadConfidence = confidence
		}

		// Check for auto-approval: high confidence AND frequently traversed
		if confidence >= AutoApprovalConfidenceThreshold && newMatchCount >= AutoApprovalMinTraversals {
			l.autoApproveRoad(ctx, parkID, matchID, existingGeojson, lengthM, newMatchCount, confidence)
		}
	} else {
		// Store as new potential road
		geojson, _ := json.Marshal(map[string]interface{}{
			"type":        "LineString",
			"coordinates": simplified,
		})
		l.queries.CreateLearnedRoad(ctx, dbgen.CreateLearnedRoadParams{
			ParkID:        parkID,
			Geojson:       string(geojson),
			LengthM:       ptrFloat64(seg.DistanceKm * 1000),
			MatchCount:    ptrInt64(1),
			ConfidencePct: ptrFloat64(25.0),
		})
	}
}

func (l *GPXLearner) processBoundarySegment(ctx context.Context, parkID string, seg ClassifiedSegment, result *LearningResult) {
	// Store boundary for reference - could be park boundary, sector, etc.
	// Not used for road learning
}

func (l *GPXLearner) processAircraftSegment(ctx context.Context, parkID string, uploadID int64, seg ClassifiedSegment, result *LearningResult) {
	if len(seg.Points) < 10 {
		return
	}

	// Determine aircraft type from speed patterns
	aircraftType := l.classifyAircraft(seg)

	// Find approach and departure patterns (first/last 2000m)
	approach, departure := l.extractAircraftPatterns(seg)

	if approach != nil {
		l.processApproach(ctx, parkID, uploadID, approach, aircraftType, result)
	}

	if departure != nil {
		l.processDeparture(ctx, parkID, uploadID, departure, aircraftType, result)
	}
}

func (l *GPXLearner) processApproach(ctx context.Context, parkID string, uploadID int64, approach *AircraftPattern, aircraftType string, result *LearningResult) {
	// Check for existing nearby airstrips
	existing, err := l.queries.FindNearbyAirstrips(ctx, dbgen.FindNearbyAirstripsParams{
		ParkID: parkID,
		Lat:    approach.EndLat,
		Lon:    approach.EndLon,
	})

	if err == nil && len(existing) > 0 {
		// Update existing airstrip
		airstrip := existing[0]
		landingCount := int64(1)
		if airstrip.LandingCount != nil {
			landingCount = *airstrip.LandingCount + 1
		}
		takeoffCount := int64(0)
		if airstrip.TakeoffCount != nil {
			takeoffCount = *airstrip.TakeoffCount
		}
		confidence := math.Min(float64(landingCount)*20.0, 90.0)

		l.queries.UpdateAirstripStats(ctx, dbgen.UpdateAirstripStatsParams{
			LandingCount:  ptrInt64(landingCount),
			TakeoffCount:  ptrInt64(0), // Don't change takeoff count
			ConfidencePct: ptrFloat64(confidence),
			ID:            airstrip.ID,
		})

		// Check for auto-approval: high confidence AND frequent use
		totalUse := landingCount + takeoffCount
		if confidence >= AutoApprovalConfidenceThreshold && totalUse >= AutoApprovalMinLandings {
			l.autoApproveAirstrip(ctx, parkID, airstrip.ID, airstrip.Lat, airstrip.Lon,
				airstrip.HeadingDeg, airstrip.LengthM, aircraftType, totalUse, confidence)
		}
	} else {
		// Create new potential airstrip
		approachJSON, _ := json.Marshal(approach)
		runwayLength := l.estimateRunwayLength(approach)
		heading := l.calculateHeading(approach)

		l.queries.CreateLearnedAirstrip(ctx, dbgen.CreateLearnedAirstripParams{
			ParkID:        parkID,
			Lat:           approach.EndLat,
			Lon:           approach.EndLon,
			HeadingDeg:    ptrFloat64(heading),
			LengthM:       ptrFloat64(runwayLength),
			AircraftType:  ptrString(aircraftType),
			LandingCount:  ptrInt64(1),
			TakeoffCount:  ptrInt64(0),
			ConfidencePct: ptrFloat64(20.0),
			ApproachJson:  ptrString(string(approachJSON)),
		})

		result.NewAirstrips++
		result.AirstripConfidence = 20.0
	}
}

func (l *GPXLearner) processDeparture(ctx context.Context, parkID string, uploadID int64, departure *AircraftPattern, aircraftType string, result *LearningResult) {
	// Check for existing nearby airstrips
	existing, err := l.queries.FindNearbyAirstrips(ctx, dbgen.FindNearbyAirstripsParams{
		ParkID: parkID,
		Lat:    departure.StartLat,
		Lon:    departure.StartLon,
	})

	if err == nil && len(existing) > 0 {
		airstrip := existing[0]
		takeoffCount := int64(1)
		if airstrip.TakeoffCount != nil {
			takeoffCount = *airstrip.TakeoffCount + 1
		}
		confidence := math.Min(float64(takeoffCount)*20.0, 90.0)

		l.queries.UpdateAirstripStats(ctx, dbgen.UpdateAirstripStatsParams{
			LandingCount:  ptrInt64(0), // Don't change landing count
			TakeoffCount:  ptrInt64(takeoffCount),
			ConfidencePct: ptrFloat64(confidence),
			ID:            airstrip.ID,
		})
	}
}

func (l *GPXLearner) classifyAircraft(seg ClassifiedSegment) string {
	// Fixed wing: higher speeds, gradual altitude changes
	// Rotor wing: can hover, more vertical movement
	if seg.AvgSpeedKmh > 150 {
		return "fixed_wing"
	} else if seg.AvgSpeedKmh < 80 {
		return "rotor_wing"
	}
	return "mixed"
}

func (l *GPXLearner) extractAircraftPatterns(seg ClassifiedSegment) (*AircraftPattern, *AircraftPattern) {
	if len(seg.Points) < 10 {
		return nil, nil
	}

	var approach, departure *AircraftPattern

	// Approach: last 2000m where speed is decreasing
	distFromEnd := 0.0
	endIdx := len(seg.Points) - 1
	startIdx := endIdx

	for i := endIdx - 1; i >= 0 && distFromEnd < 2000; i-- {
		dist := haversineDistance(seg.Points[i].Lat, seg.Points[i].Lon,
			seg.Points[i+1].Lat, seg.Points[i+1].Lon)
		distFromEnd += dist
		startIdx = i
	}

	if endIdx-startIdx >= 5 {
		approach = &AircraftPattern{
			StartLat: seg.Points[startIdx].Lat,
			StartLon: seg.Points[startIdx].Lon,
			EndLat:   seg.Points[endIdx].Lat,
			EndLon:   seg.Points[endIdx].Lon,
		}

		var totalSpeed float64
		for i := startIdx; i <= endIdx; i++ {
			approach.Points = append(approach.Points, []float64{seg.Points[i].Lon, seg.Points[i].Lat})
			if i > startIdx && seg.Points[i].Time != nil && seg.Points[i-1].Time != nil {
				if seg.Points[i].Time.After(*seg.Points[i-1].Time) {
					dt := seg.Points[i].Time.Sub(*seg.Points[i-1].Time).Hours()
					if dt > 0 {
						d := haversineDistance(seg.Points[i-1].Lat, seg.Points[i-1].Lon,
							seg.Points[i].Lat, seg.Points[i].Lon)
						totalSpeed += d / 1000 / dt
					}
				}
			}
		}
		if endIdx > startIdx {
			approach.AvgSpeed = totalSpeed / float64(endIdx-startIdx)
		}
	}

	// Departure: first 2000m where speed is increasing
	distFromStart := 0.0
	departureEndIdx := 0

	for i := 1; i < len(seg.Points) && distFromStart < 2000; i++ {
		dist := haversineDistance(seg.Points[i-1].Lat, seg.Points[i-1].Lon,
			seg.Points[i].Lat, seg.Points[i].Lon)
		distFromStart += dist
		departureEndIdx = i
	}

	if departureEndIdx >= 5 {
		departure = &AircraftPattern{
			StartLat: seg.Points[0].Lat,
			StartLon: seg.Points[0].Lon,
			EndLat:   seg.Points[departureEndIdx].Lat,
			EndLon:   seg.Points[departureEndIdx].Lon,
		}

		for i := 0; i <= departureEndIdx; i++ {
			departure.Points = append(departure.Points, []float64{seg.Points[i].Lon, seg.Points[i].Lat})
		}
	}

	return approach, departure
}

func (l *GPXLearner) storeVehicleTrack(ctx context.Context, parkID string, uploadID int64, seg ClassifiedSegment) {
	coords := l.parseGeoJSONCoords(seg.GeoJSON)
	if len(coords) < 2 {
		return
	}

	// Simplify to 10m resolution and remove timestamps
	simplified := l.simplifyCoords(coords, 10.0)
	geojson, _ := json.Marshal(map[string]interface{}{
		"type":        "LineString",
		"coordinates": simplified,
	})

	l.queries.CreateVehicleTrack(ctx, dbgen.CreateVehicleTrackParams{
		ParkID:       parkID,
		UploadID:     ptrInt64(uploadID),
		Geojson:      string(geojson),
		LengthM:      ptrFloat64(seg.DistanceKm * 1000),
		MovementType: ptrString("vehicle"),
	})
}

func (l *GPXLearner) detectStops(seg ClassifiedSegment) []StopPoint {
	var stops []StopPoint

	if len(seg.Points) < 3 {
		return stops
	}

	// Look for clusters of points where movement is minimal
	const minStopDuration = 30 * time.Minute
	const maxStopRadius = 50.0 // meters

	var currentStop *StopPoint
	var stopStart *time.Time

	for i := 1; i < len(seg.Points); i++ {
		dist := haversineDistance(seg.Points[i-1].Lat, seg.Points[i-1].Lon,
			seg.Points[i].Lat, seg.Points[i].Lon)

		if dist < maxStopRadius {
			if currentStop == nil {
				currentStop = &StopPoint{
					Lat: seg.Points[i-1].Lat,
					Lon: seg.Points[i-1].Lon,
				}
				stopStart = seg.Points[i-1].Time
			}
		} else if currentStop != nil && stopStart != nil {
			// End of stop
			if seg.Points[i-1].Time != nil {
				currentStop.Duration = seg.Points[i-1].Time.Sub(*stopStart)
				if currentStop.Duration >= minStopDuration {
					stops = append(stops, *currentStop)
				}
			}
			currentStop = nil
		}
	}

	// Check last stop
	if currentStop != nil && stopStart != nil && len(seg.Points) > 0 {
		lastTime := seg.Points[len(seg.Points)-1].Time
		if lastTime != nil {
			currentStop.Duration = lastTime.Sub(*stopStart)
			if currentStop.Duration >= minStopDuration {
				stops = append(stops, *currentStop)
			}
		}
	}

	return stops
}

func (l *GPXLearner) processStop(ctx context.Context, parkID string, stop StopPoint, result *LearningResult) {
	// Check for nearby existing places
	existing, err := l.queries.FindNearbyPlaces(ctx, dbgen.FindNearbyPlacesParams{
		ParkID:  parkID,
		Lat:    stop.Lat,
		Lon:    stop.Lon,
	})

	if err == nil && len(existing) > 0 {
		// Update existing place
		place := existing[0]
		visits := int64(1)
		if place.VisitCount != nil {
			visits = *place.VisitCount + 1
		}
		avgDuration := stop.Duration.Minutes()
		if place.AvgDurationMinutes != nil && *place.AvgDurationMinutes > 0 {
			avgDuration = (*place.AvgDurationMinutes*float64(visits-1) + stop.Duration.Minutes()) / float64(visits)
		}
		confidence := l.calculatePlaceConfidence(visits, avgDuration)

		l.queries.UpdatePlaceStats(ctx, dbgen.UpdatePlaceStatsParams{
			AvgDurationMinutes: ptrFloat64(avgDuration),
			ConfidencePct:      ptrFloat64(confidence),
			ID:                 place.ID,
		})

		if visits >= 3 {
			placeType := l.classifyPlace(avgDuration)
			if result.PlaceTypes[placeType] == 0 {
				result.NewPlaces++
			}
			result.PlaceTypes[placeType]++
			result.PlaceConfidence = confidence
		}
	} else {
		// Create new potential place
		placeType := l.classifyPlace(stop.Duration.Minutes())
		confidence := l.calculatePlaceConfidence(1, stop.Duration.Minutes())

		l.queries.CreateLearnedPlace(ctx, dbgen.CreateLearnedPlaceParams{
			ParkID:             parkID,
			Lat:                stop.Lat,
			Lon:                stop.Lon,
			PlaceType:          ptrString(placeType),
			VisitCount:         ptrInt64(1),
			AvgDurationMinutes: ptrFloat64(stop.Duration.Minutes()),
			ConfidencePct:      ptrFloat64(confidence),
		})
	}
}

func (l *GPXLearner) classifyPlace(avgDurationMinutes float64) string {
	switch {
	case avgDurationMinutes > 480: // > 8 hours
		return "headquarters"
	case avgDurationMinutes > 240: // > 4 hours
		return "outpost"
	case avgDurationMinutes > 60: // > 1 hour
		return "camp"
	case avgDurationMinutes > 30:
		return "gate"
	default:
		return "unknown"
	}
}

func (l *GPXLearner) calculatePlaceConfidence(visits int64, avgDuration float64) float64 {
	// More visits and longer durations = higher confidence
	visitScore := math.Min(float64(visits)*15.0, 60.0)
	durationScore := math.Min(avgDuration/10.0, 35.0)
	return math.Min(visitScore+durationScore, 95.0)
}

// parseGeoJSONCoords extracts coordinates from a GeoJSON LineString
func (l *GPXLearner) parseGeoJSONCoords(geojson string) [][]float64 {
	if geojson == "" {
		return nil
	}
	var gj struct {
		Type        string      `json:"type"`
		Coordinates [][]float64 `json:"coordinates"`
	}
	if err := json.Unmarshal([]byte(geojson), &gj); err != nil {
		return nil
	}
	return gj.Coordinates
}

// simplifyCoords reduces the number of points in a coordinate array (10m tolerance)
func (l *GPXLearner) simplifyCoords(coords [][]float64, tolerance float64) [][]float64 {
	if len(coords) < 2 {
		return coords
	}

	var result [][]float64
	lastCoord := coords[0]
	result = append(result, lastCoord)

	for i := 1; i < len(coords); i++ {
		dist := haversineDistance(lastCoord[1], lastCoord[0], coords[i][1], coords[i][0])
		if dist >= tolerance {
			result = append(result, coords[i])
			lastCoord = coords[i]
		}
	}

	// Always include last point
	last := coords[len(coords)-1]
	if len(result) > 0 && (result[len(result)-1][0] != last[0] || result[len(result)-1][1] != last[1]) {
		result = append(result, last)
	}

	return result
}

func (l *GPXLearner) findMatchingTrack(ctx context.Context, parkID string, track [][]float64) (bool, int64) {
	if len(track) < 2 {
		return false, 0
	}

	existingTracks, err := l.queries.FindNearbyVehicleTracks(ctx, parkID)
	if err != nil {
		return false, 0
	}

	const matchThreshold = 20.0 // meters

	for _, existing := range existingTracks {
		var existingCoords [][]float64
		var gj struct {
			Coordinates [][]float64 `json:"coordinates"`
		}
		if err := json.Unmarshal([]byte(existing.Geojson), &gj); err != nil {
			continue
		}
		existingCoords = gj.Coordinates

		// Check if tracks overlap significantly
		matchCount := 0
		for _, pt := range track {
			for _, ept := range existingCoords {
				dist := haversineDistance(pt[1], pt[0], ept[1], ept[0])
				if dist < matchThreshold {
					matchCount++
					break
				}
			}
		}

		matchRatio := float64(matchCount) / float64(len(track))
		if matchRatio > 0.5 { // >50% overlap
			return true, existing.ID
		}
	}

	return false, 0
}

func (l *GPXLearner) estimateRunwayLength(approach *AircraftPattern) float64 {
	if approach == nil || len(approach.Points) < 2 {
		return 0
	}

	// Look for the straight portion at the end (last part of approach)
	var totalLength float64
	for i := len(approach.Points) - 1; i > 0; i-- {
		dist := haversineDistance(approach.Points[i][1], approach.Points[i][0],
			approach.Points[i-1][1], approach.Points[i-1][0])
		totalLength += dist

		// Stop if we've measured 2000m or found a turn
		if totalLength > 2000 {
			break
		}
	}

	// Estimate runway as 1/3 of approach length (rough estimate)
	return math.Min(totalLength/3, 2000)
}

func (l *GPXLearner) calculateHeading(approach *AircraftPattern) float64 {
	if approach == nil || len(approach.Points) < 2 {
		return 0
	}

	// Use last two points for heading
	n := len(approach.Points)
	lat1 := approach.Points[n-2][1] * math.Pi / 180
	lon1 := approach.Points[n-2][0] * math.Pi / 180
	lat2 := approach.Points[n-1][1] * math.Pi / 180
	lon2 := approach.Points[n-1][0] * math.Pi / 180

	dLon := lon2 - lon1
	y := math.Sin(dLon) * math.Cos(lat2)
	x := math.Cos(lat1)*math.Sin(lat2) - math.Sin(lat1)*math.Cos(lat2)*math.Cos(dLon)

	heading := math.Atan2(y, x) * 180 / math.Pi
	if heading < 0 {
		heading += 360
	}
	return heading
}

func (l *GPXLearner) generateSummary(result *LearningResult) string {
	var parts []string

	if result.VehicleMedianSpeed > 0 {
		parts = append(parts, fmt.Sprintf("Vehicle speeds: median %.1f km/h, max %.1f km/h",
			result.VehicleMedianSpeed, result.VehicleMaxSpeed))
	}

	if result.FootMedianSpeed > 0 {
		parts = append(parts, fmt.Sprintf("Foot patrol speeds: median %.1f km/h, max %.1f km/h",
			result.FootMedianSpeed, result.FootMaxSpeed))
	}

	if result.FootMCPArea > 0 {
		parts = append(parts, fmt.Sprintf("Foot patrol area (90%% MCP): %.2f km²",
			result.FootMCPArea))
	}

	if result.NewRoads > 0 {
		parts = append(parts, fmt.Sprintf("Potential roads: %d (%.1f km, %.0f%% confidence)",
			result.NewRoads, result.NewRoadsKm, result.RoadConfidence))
	}

	if result.NewAirstrips > 0 {
		parts = append(parts, fmt.Sprintf("Potential airstrips: %d (%.0f%% confidence)",
			result.NewAirstrips, result.AirstripConfidence))
	}

	if result.NewPlaces > 0 {
		var placeTypes []string
		for pt, count := range result.PlaceTypes {
			placeTypes = append(placeTypes, fmt.Sprintf("%d %s", count, pt))
		}
		parts = append(parts, fmt.Sprintf("Potential places: %s (%.0f%% confidence)",
			strings.Join(placeTypes, ", "), result.PlaceConfidence))
	}

	if len(parts) == 0 {
		return "No new patterns detected"
	}

	return strings.Join(parts, "; ")
}

func (l *GPXLearner) storeLearningResult(ctx context.Context, result *LearningResult) error {
	placeTypesJSON, _ := json.Marshal(result.PlaceTypes)
	discoveriesJSON, _ := json.Marshal(result)

	_, err := l.queries.CreateLearningResult(ctx, dbgen.CreateLearningResultParams{
		UploadID:              ptrInt64(result.UploadID),
		ParkID:                result.ParkID,
		ParkName:              ptrString(result.ParkName),
		VehicleMedianSpeedKmh: ptrFloat64(result.VehicleMedianSpeed),
		VehicleMaxSpeedKmh:    ptrFloat64(result.VehicleMaxSpeed),
		FootMedianSpeedKmh:    ptrFloat64(result.FootMedianSpeed),
		FootMaxSpeedKmh:       ptrFloat64(result.FootMaxSpeed),
		FootMcpAreaKm2:        ptrFloat64(result.FootMCPArea),
		NewRoadsFound:         ptrInt64(int64(result.NewRoads)),
		NewRoadsKm:            ptrFloat64(result.NewRoadsKm),
		RoadConfidencePct:     ptrFloat64(result.RoadConfidence),
		NewAirstripsFound:     ptrInt64(int64(result.NewAirstrips)),
		AirstripConfidencePct: ptrFloat64(result.AirstripConfidence),
		NewPlacesFound:        ptrInt64(int64(result.NewPlaces)),
		PlaceTypesJson:        ptrString(string(placeTypesJSON)),
		PlaceConfidencePct:    ptrFloat64(result.PlaceConfidence),
		SummaryText:           ptrString(result.Summary),
		DiscoveriesJson:       ptrString(string(discoveriesJSON)),
	})

	return err
}

// Helper functions for statistics
func median(data []float64) float64 {
	if len(data) == 0 {
		return 0
	}
	sorted := make([]float64, len(data))
	copy(sorted, data)
	sort.Float64s(sorted)
	mid := len(sorted) / 2
	if len(sorted)%2 == 0 {
		return (sorted[mid-1] + sorted[mid]) / 2
	}
	return sorted[mid]
}

func maxFloat(data []float64) float64 {
	if len(data) == 0 {
		return 0
	}
	m := data[0]
	for _, v := range data[1:] {
		if v > m {
			m = v
		}
	}
	return m
}

func percentile(data []float64, p float64) float64 {
	if len(data) == 0 {
		return 0
	}
	sorted := make([]float64, len(data))
	copy(sorted, data)
	sort.Float64s(sorted)
	idx := int(float64(len(sorted)-1) * p / 100)
	return sorted[idx]
}

// autoApproveRoad inserts a high-confidence road into feature_geometries
func (l *GPXLearner) autoApproveRoad(ctx context.Context, parkID string, roadID int64, geojson string, lengthM float64, matchCount int64, confidence float64) {
	featureID := fmt.Sprintf("learned_road_%d", roadID)

	// Build properties JSON with approval info
	properties := map[string]interface{}{
		"approval_status": "auto_approved",
		"source":          "gpx_learner",
		"learned_road_id": roadID,
		"match_count":     matchCount,
		"confidence_pct":  confidence,
		"length_m":        lengthM,
	}
	propertiesJSON, _ := json.Marshal(properties)

	// Calculate bounding box from GeoJSON
	minX, minY, maxX, maxY := l.calculateBBox(geojson)

	err := l.queries.InsertAutoApprovedFeature(ctx, dbgen.InsertAutoApprovedFeatureParams{
		FeatureType:    "road",
		FeatureID:      featureID,
		ParkID:         parkID,
		Geojson:        geojson,
		BboxMinx:       ptrFloat64(minX),
		BboxMiny:       ptrFloat64(minY),
		BboxMaxx:       ptrFloat64(maxX),
		BboxMaxy:       ptrFloat64(maxY),
		PropertiesJson: ptrString(string(propertiesJSON)),
	})
	if err != nil {
		slog.Error("failed to auto-approve road", "road_id", roadID, "error", err)
		return
	}

	// Update the learned_roads status to auto_approved
	_, err = l.db.ExecContext(ctx, "UPDATE learned_roads SET status = 'auto_approved' WHERE id = ?", roadID)
	if err != nil {
		slog.Error("failed to update learned_road status", "road_id", roadID, "error", err)
	}

	slog.Info("auto-approved road", "road_id", roadID, "park_id", parkID, "confidence", confidence, "match_count", matchCount)
}

// autoApproveAirstrip inserts a high-confidence airstrip into feature_geometries
func (l *GPXLearner) autoApproveAirstrip(ctx context.Context, parkID string, airstripID int64, lat, lon float64, headingDeg, lengthM *float64, aircraftType string, totalUse int64, confidence float64) {
	featureID := fmt.Sprintf("learned_airstrip_%d", airstripID)

	// Create point GeoJSON for the airstrip location
	geojson, _ := json.Marshal(map[string]interface{}{
		"type":        "Point",
		"coordinates": []float64{lon, lat},
	})

	// Build properties JSON with approval info
	properties := map[string]interface{}{
		"approval_status":    "auto_approved",
		"source":             "gpx_learner",
		"learned_airstrip_id": airstripID,
		"total_use":          totalUse,
		"confidence_pct":     confidence,
		"aircraft_type":      aircraftType,
	}
	if headingDeg != nil {
		properties["heading_deg"] = *headingDeg
	}
	if lengthM != nil {
		properties["length_m"] = *lengthM
	}
	propertiesJSON, _ := json.Marshal(properties)

	err := l.queries.InsertAutoApprovedFeature(ctx, dbgen.InsertAutoApprovedFeatureParams{
		FeatureType:    "airstrip",
		FeatureID:      featureID,
		ParkID:         parkID,
		Geojson:        string(geojson),
		BboxMinx:       ptrFloat64(lon),
		BboxMiny:       ptrFloat64(lat),
		BboxMaxx:       ptrFloat64(lon),
		BboxMaxy:       ptrFloat64(lat),
		PropertiesJson: ptrString(string(propertiesJSON)),
	})
	if err != nil {
		slog.Error("failed to auto-approve airstrip", "airstrip_id", airstripID, "error", err)
		return
	}

	// Update the learned_airstrips status to auto_approved
	_, err = l.db.ExecContext(ctx, "UPDATE learned_airstrips SET status = 'auto_approved' WHERE id = ?", airstripID)
	if err != nil {
		slog.Error("failed to update learned_airstrip status", "airstrip_id", airstripID, "error", err)
	}

	slog.Info("auto-approved airstrip", "airstrip_id", airstripID, "park_id", parkID, "confidence", confidence, "total_use", totalUse)
}

// calculateBBox extracts bounding box from GeoJSON LineString
func (l *GPXLearner) calculateBBox(geojson string) (minX, minY, maxX, maxY float64) {
	var gj struct {
		Coordinates [][]float64 `json:"coordinates"`
	}
	if err := json.Unmarshal([]byte(geojson), &gj); err != nil || len(gj.Coordinates) == 0 {
		return 0, 0, 0, 0
	}

	minX, minY = gj.Coordinates[0][0], gj.Coordinates[0][1]
	maxX, maxY = minX, minY

	for _, coord := range gj.Coordinates {
		if len(coord) >= 2 {
			if coord[0] < minX {
				minX = coord[0]
			}
			if coord[0] > maxX {
				maxX = coord[0]
			}
			if coord[1] < minY {
				minY = coord[1]
			}
			if coord[1] > maxY {
				maxY = coord[1]
			}
		}
	}
	return
}

// calculateMCP90 calculates the 90% Minimum Convex Polygon area
func calculateMCP90(points []gpx.Point) float64 {
	if len(points) < 3 {
		return 0
	}

	// Calculate centroid
	var sumLat, sumLon float64
	for _, p := range points {
		sumLat += p.Lat
		sumLon += p.Lon
	}
	centroidLat := sumLat / float64(len(points))
	centroidLon := sumLon / float64(len(points))

	// Calculate distance from centroid for each point
	type pointDist struct {
		point gpx.Point
		dist  float64
	}
	distances := make([]pointDist, len(points))
	for i, p := range points {
		distances[i] = pointDist{
			point: p,
			dist:  haversineDistance(centroidLat, centroidLon, p.Lat, p.Lon),
		}
	}

	// Sort by distance
	sort.Slice(distances, func(i, j int) bool {
		return distances[i].dist < distances[j].dist
	})

	// Take 90% of points (closest to centroid)
	n90 := int(float64(len(distances)) * 0.9)
	if n90 < 3 {
		n90 = len(distances)
	}

	// Extract the 90% points
	var mcpPoints []gpx.Point
	for i := 0; i < n90; i++ {
		mcpPoints = append(mcpPoints, distances[i].point)
	}

	// Calculate convex hull area using shoelace formula
	// First, sort points by angle from centroid
	sort.Slice(mcpPoints, func(i, j int) bool {
		angle1 := math.Atan2(mcpPoints[i].Lat-centroidLat, mcpPoints[i].Lon-centroidLon)
		angle2 := math.Atan2(mcpPoints[j].Lat-centroidLat, mcpPoints[j].Lon-centroidLon)
		return angle1 < angle2
	})

	// Shoelace formula for area
	var area float64
	n := len(mcpPoints)
	for i := 0; i < n; i++ {
		j := (i + 1) % n
		// Convert to approximate meters using latitude
		x1 := mcpPoints[i].Lon * 111320 * math.Cos(mcpPoints[i].Lat*math.Pi/180)
		y1 := mcpPoints[i].Lat * 110540
		x2 := mcpPoints[j].Lon * 111320 * math.Cos(mcpPoints[j].Lat*math.Pi/180)
		y2 := mcpPoints[j].Lat * 110540
		area += x1*y2 - x2*y1
	}
	area = math.Abs(area) / 2

	// Convert from m² to km²
	return area / 1e6
}

// =============================================================================
// Cross-Track Analysis Functions
// =============================================================================

// GridCell represents a cell in the spatial grid for road detection
type GridCell struct {
	Row, Col   int
	Lat, Lon   float64 // Center of cell
	TrackCount int     // Number of different tracks passing through
	TrackIDs   map[int64]bool
}

// GridKey creates a unique key for a grid cell
func gridKey(row, col int) string {
	return fmt.Sprintf("%d_%d", row, col)
}

// CrossTrackAnalyzer performs cross-track analysis for road/base/airstrip detection
type CrossTrackAnalyzer struct {
	cellSize    float64 // Cell size in meters (default 10m)
	minLat      float64
	minLon      float64
	maxLat      float64
	maxLon      float64
	latPerMeter float64
	lonPerMeter float64
	grid        map[string]*GridCell
}

// NewCrossTrackAnalyzer creates a new analyzer with given cell size
func NewCrossTrackAnalyzer(cellSizeMeters float64) *CrossTrackAnalyzer {
	return &CrossTrackAnalyzer{
		cellSize: cellSizeMeters,
		grid:     make(map[string]*GridCell),
	}
}

// latLonToMeters converts lat/lon degrees to approximate meters at a given latitude
func (a *CrossTrackAnalyzer) initBounds(segments []ClassifiedSegment) {
	if len(segments) == 0 {
		return
	}

	a.minLat, a.maxLat = 90.0, -90.0
	a.minLon, a.maxLon = 180.0, -180.0

	for _, seg := range segments {
		for _, pt := range seg.Points {
			if pt.Lat < a.minLat {
				a.minLat = pt.Lat
			}
			if pt.Lat > a.maxLat {
				a.maxLat = pt.Lat
			}
			if pt.Lon < a.minLon {
				a.minLon = pt.Lon
			}
			if pt.Lon > a.maxLon {
				a.maxLon = pt.Lon
			}
		}
	}

	// Calculate degrees per meter at center latitude
	centerLat := (a.minLat + a.maxLat) / 2
	a.latPerMeter = 1.0 / 110540.0 // ~110.54 km per degree latitude
	a.lonPerMeter = 1.0 / (111320.0 * math.Cos(centerLat*math.Pi/180.0))
}

// pointToCell converts a lat/lon point to grid cell coordinates
func (a *CrossTrackAnalyzer) pointToCell(lat, lon float64) (int, int) {
	latOffset := (lat - a.minLat) / (a.latPerMeter * a.cellSize)
	lonOffset := (lon - a.minLon) / (a.lonPerMeter * a.cellSize)
	return int(latOffset), int(lonOffset)
}

// cellToLatLon converts grid cell to center lat/lon
func (a *CrossTrackAnalyzer) cellToLatLon(row, col int) (float64, float64) {
	lat := a.minLat + (float64(row)+0.5)*a.latPerMeter*a.cellSize
	lon := a.minLon + (float64(col)+0.5)*a.lonPerMeter*a.cellSize
	return lat, lon
}

// DetectedRoad represents a road detected from cross-track analysis
type DetectedRoad struct {
	Points      [][]float64 // [[lon, lat], ...]
	LengthM     float64
	TrackCount  int // Number of different tracks that used this road
	Confidence  float64
}

// DetectedAirstrip represents an airstrip detected from track patterns
type DetectedAirstrip struct {
	Lat, Lon     float64
	HeadingDeg   float64
	LengthM      float64
	AircraftType string
	Confidence   float64
	PatternType  string // "takeoff" or "landing"
}

// DetectedBase represents a base/camp detected from track endpoints
type DetectedBase struct {
	Lat, Lon       float64
	TrackCount     int     // Number of tracks starting/ending here
	AvgDurationMin float64 // Average stop duration
	PlaceType      string  // "base", "camp", "gate"
	Confidence     float64
}

// detectRoadsFromTracks analyzes multiple tracks to find frequently-used paths
// Uses a grid-based approach: cells with 5+ track passes are likely roads
func (a *CrossTrackAnalyzer) detectRoadsFromTracks(segments []ClassifiedSegment) []DetectedRoad {
	if len(segments) == 0 {
		return nil
	}

	a.initBounds(segments)
	a.grid = make(map[string]*GridCell)

	// Process each segment and mark cells
	for trackID, seg := range segments {
		if seg.MovementType != "vehicle" && seg.AvgSpeedKmh < 8 {
			continue // Skip non-vehicle segments
		}

		for _, pt := range seg.Points {
			row, col := a.pointToCell(pt.Lat, pt.Lon)
			key := gridKey(row, col)

			cell, exists := a.grid[key]
			if !exists {
				lat, lon := a.cellToLatLon(row, col)
				cell = &GridCell{
					Row:      row,
					Col:      col,
					Lat:      lat,
					Lon:      lon,
					TrackIDs: make(map[int64]bool),
				}
				a.grid[key] = cell
			}

			if !cell.TrackIDs[int64(trackID)] {
				cell.TrackIDs[int64(trackID)] = true
				cell.TrackCount++
			}
		}
	}

	// Find high-traffic cells (5+ tracks)
	const minTrackCount = 5
	highTrafficCells := make([]*GridCell, 0)
	for _, cell := range a.grid {
		if cell.TrackCount >= minTrackCount {
			highTrafficCells = append(highTrafficCells, cell)
		}
	}

	if len(highTrafficCells) == 0 {
		return nil
	}

	// Build connected road segments from high-traffic cells
	return a.buildRoadSegments(highTrafficCells, minTrackCount)
}

// buildRoadSegments connects adjacent high-traffic cells into road linestrings
func (a *CrossTrackAnalyzer) buildRoadSegments(cells []*GridCell, minTrackCount int) []DetectedRoad {
	if len(cells) == 0 {
		return nil
	}

	// Create a map for quick lookup
	cellMap := make(map[string]*GridCell)
	for _, cell := range cells {
		cellMap[gridKey(cell.Row, cell.Col)] = cell
	}

	visited := make(map[string]bool)
	var roads []DetectedRoad

	// 8-directional neighbors
	dirs := []struct{ dr, dc int }{
		{-1, -1}, {-1, 0}, {-1, 1},
		{0, -1}, {0, 1},
		{1, -1}, {1, 0}, {1, 1},
	}

	// DFS to find connected components
	for _, startCell := range cells {
		startKey := gridKey(startCell.Row, startCell.Col)
		if visited[startKey] {
			continue
		}

		// BFS to collect connected cells
		var component []*GridCell
		queue := []*GridCell{startCell}
		visited[startKey] = true

		for len(queue) > 0 {
			cell := queue[0]
			queue = queue[1:]
			component = append(component, cell)

			for _, d := range dirs {
				nr, nc := cell.Row+d.dr, cell.Col+d.dc
				nKey := gridKey(nr, nc)
				if !visited[nKey] {
					if neighbor, ok := cellMap[nKey]; ok {
						visited[nKey] = true
						queue = append(queue, neighbor)
					}
				}
			}
		}

		// Convert component to road if large enough
		if len(component) >= 3 {
			road := a.componentToRoad(component)
			if road.LengthM >= 50 { // Minimum 50m
				roads = append(roads, road)
			}
		}
	}

	return roads
}

// componentToRoad converts a connected component of cells to a road
func (a *CrossTrackAnalyzer) componentToRoad(cells []*GridCell) DetectedRoad {
	// Sort cells to form a reasonable path (by column, then row)
	sort.Slice(cells, func(i, j int) bool {
		if cells[i].Col != cells[j].Col {
			return cells[i].Col < cells[j].Col
		}
		return cells[i].Row < cells[j].Row
	})

	// Build linestring from cell centers
	var points [][]float64
	var totalLength float64
	var totalTrackCount int

	for i, cell := range cells {
		points = append(points, []float64{cell.Lon, cell.Lat})
		totalTrackCount += cell.TrackCount

		if i > 0 {
			dist := haversineDistance(cells[i-1].Lat, cells[i-1].Lon, cell.Lat, cell.Lon)
			totalLength += dist
		}
	}

	avgTrackCount := totalTrackCount / len(cells)
	confidence := math.Min(float64(avgTrackCount)*10.0, 95.0) // 10% per track, max 95%

	return DetectedRoad{
		Points:     points,
		LengthM:    totalLength,
		TrackCount: avgTrackCount,
		Confidence: confidence,
	}
}

// detectAirstrips finds airstrips from straight segments with abrupt movement changes
// Looks for: straight segments > 500m with vehicle movement ending/starting abruptly
func detectAirstrips(segments []ClassifiedSegment) []DetectedAirstrip {
	var airstrips []DetectedAirstrip

	for _, seg := range segments {
		if len(seg.Points) < 10 {
			continue
		}

		// Look for straight segments > 500m
		straightSegs := findStraightSegments(seg.Points, 500.0, 15.0) // 500m min, 15° max deviation

		for _, ss := range straightSegs {
			// Check for abrupt speed changes at ends
			startAbrupt := isAbruptStart(seg.Points, ss.startIdx)
			endAbrupt := isAbruptEnd(seg.Points, ss.endIdx)

			if startAbrupt || endAbrupt {
				airstrip := DetectedAirstrip{
					LengthM:    ss.length,
					HeadingDeg: ss.heading,
					Confidence: 30.0,
				}

				if startAbrupt {
					airstrip.PatternType = "takeoff"
					airstrip.Lat = seg.Points[ss.startIdx].Lat
					airstrip.Lon = seg.Points[ss.startIdx].Lon
					airstrip.Confidence += 20.0
				}
				if endAbrupt {
					airstrip.PatternType = "landing"
					airstrip.Lat = seg.Points[ss.endIdx].Lat
					airstrip.Lon = seg.Points[ss.endIdx].Lon
					airstrip.Confidence += 20.0
				}

				// Classify aircraft type from speed
				if seg.AvgSpeedKmh > 150 {
					airstrip.AircraftType = "fixed_wing"
				} else if seg.AvgSpeedKmh > 50 {
					airstrip.AircraftType = "mixed"
				} else {
					airstrip.AircraftType = "rotor_wing"
				}

				airstrips = append(airstrips, airstrip)
			}
		}
	}

	return airstrips
}

// straightSegment represents a detected straight portion of a track
type straightSegment struct {
	startIdx int
	endIdx   int
	length   float64
	heading  float64
}

// findStraightSegments finds portions of track that are relatively straight
func findStraightSegments(points []gpx.Point, minLengthM, maxDeviationDeg float64) []straightSegment {
	var result []straightSegment
	if len(points) < 3 {
		return result
	}

	startIdx := 0
	for startIdx < len(points)-2 {
		// Calculate initial heading
		initialHeading := calculateBearing(
			points[startIdx].Lat, points[startIdx].Lon,
			points[startIdx+1].Lat, points[startIdx+1].Lon,
		)

		endIdx := startIdx + 1
		totalLength := 0.0

		// Extend while heading stays within deviation
		for endIdx < len(points)-1 {
			nextHeading := calculateBearing(
				points[endIdx].Lat, points[endIdx].Lon,
				points[endIdx+1].Lat, points[endIdx+1].Lon,
			)

			headingDiff := math.Abs(normalizeAngle(nextHeading - initialHeading))
			if headingDiff > maxDeviationDeg {
				break
			}

			dist := haversineDistance(
				points[endIdx].Lat, points[endIdx].Lon,
				points[endIdx+1].Lat, points[endIdx+1].Lon,
			)
			totalLength += dist
			endIdx++
		}

		if totalLength >= minLengthM {
			result = append(result, straightSegment{
				startIdx: startIdx,
				endIdx:   endIdx,
				length:   totalLength,
				heading:  initialHeading,
			})
			startIdx = endIdx
		} else {
			startIdx++
		}
	}

	return result
}


// normalizeAngle normalizes an angle to -180 to 180
func normalizeAngle(angle float64) float64 {
	for angle > 180 {
		angle -= 360
	}
	for angle < -180 {
		angle += 360
	}
	return angle
}

// isAbruptStart checks if track starts abruptly (likely takeoff)
func isAbruptStart(points []gpx.Point, idx int) bool {
	if idx < 0 || idx >= len(points)-3 {
		return false
	}

	// Check if this is near the start of the track
	if idx > 5 {
		return false
	}

	// Check for rapid speed increase in first few points
	if points[idx].Time == nil || points[idx+2].Time == nil {
		return false
	}

	dt := points[idx+2].Time.Sub(*points[idx].Time).Hours()
	if dt <= 0 {
		return false
	}

	dist := haversineDistance(points[idx].Lat, points[idx].Lon, points[idx+2].Lat, points[idx+2].Lon)
	speed := (dist / 1000) / dt

	return speed > 80 // > 80 km/h suggests vehicle accelerating for takeoff
}

// isAbruptEnd checks if track ends abruptly (likely landing)
func isAbruptEnd(points []gpx.Point, idx int) bool {
	if idx < 3 || idx >= len(points) {
		return false
	}

	// Check if this is near the end of the track
	if idx < len(points)-5 {
		return false
	}

	// Check for rapid speed decrease in last few points
	if points[idx-2].Time == nil || points[idx].Time == nil {
		return false
	}

	dt := points[idx].Time.Sub(*points[idx-2].Time).Hours()
	if dt <= 0 {
		return false
	}

	dist := haversineDistance(points[idx-2].Lat, points[idx-2].Lon, points[idx].Lat, points[idx].Lon)
	speed := (dist / 1000) / dt

	return speed > 80 // > 80 km/h suggests aircraft decelerating after landing
}

// detectBases finds likely bases/camps from track start/end clustering
// Also checks settlement_intensity for is_likely_base settlements
func detectBases(segments []ClassifiedSegment) []DetectedBase {
	var bases []DetectedBase

	// Collect all track start and end points
	type endpoint struct {
		lat, lon   float64
		isStart    bool
		durationMin float64
	}

	var endpoints []endpoint

	for _, seg := range segments {
		if len(seg.Points) < 2 {
			continue
		}

		// Skip aircraft - they start/end at airstrips, not bases
		if seg.MovementType == "aircraft" {
			continue
		}

		// Track start point
		endpoints = append(endpoints, endpoint{
			lat:     seg.Points[0].Lat,
			lon:     seg.Points[0].Lon,
			isStart: true,
		})

		// Track end point
		lastIdx := len(seg.Points) - 1
		endpoints = append(endpoints, endpoint{
			lat:         seg.Points[lastIdx].Lat,
			lon:         seg.Points[lastIdx].Lon,
			isStart:     false,
			durationMin: seg.Duration.Minutes(),
		})
	}

	if len(endpoints) < 2 {
		return bases
	}

	// Cluster endpoints using simple distance-based clustering
	// Two endpoints within 100m are in the same cluster
	const clusterRadius = 100.0 // meters

	clustered := make([]bool, len(endpoints))
	for i := 0; i < len(endpoints); i++ {
		if clustered[i] {
			continue
		}

		// Start a new cluster
		cluster := []endpoint{endpoints[i]}
		clustered[i] = true

		// Find all nearby endpoints
		for j := i + 1; j < len(endpoints); j++ {
			if clustered[j] {
				continue
			}

			dist := haversineDistance(endpoints[i].lat, endpoints[i].lon,
				endpoints[j].lat, endpoints[j].lon)
			if dist <= clusterRadius {
				cluster = append(cluster, endpoints[j])
				clustered[j] = true
			}
		}

		// Only consider clusters with 3+ endpoints (frequent use)
		if len(cluster) >= 3 {
			// Calculate cluster center
			var sumLat, sumLon, sumDuration float64
			for _, ep := range cluster {
				sumLat += ep.lat
				sumLon += ep.lon
				sumDuration += ep.durationMin
			}

			avgDuration := sumDuration / float64(len(cluster))
			
			// Classify based on visit count and duration
			placeType := "camp"
			if len(cluster) >= 10 && avgDuration > 60 {
				placeType = "base"
			} else if len(cluster) >= 5 {
				placeType = "outpost"
			}

			confidence := math.Min(float64(len(cluster))*10.0, 90.0)

			bases = append(bases, DetectedBase{
				Lat:            sumLat / float64(len(cluster)),
				Lon:            sumLon / float64(len(cluster)),
				TrackCount:     len(cluster),
				AvgDurationMin: avgDuration,
				PlaceType:      placeType,
				Confidence:     confidence,
			})
		}
	}

	return bases
}

// CrossTrackAnalysisResult contains results from cross-track analysis
type CrossTrackAnalysisResult struct {
	Roads     []DetectedRoad
	Airstrips []DetectedAirstrip
	Bases     []DetectedBase
}

// RunCrossTrackAnalysis performs comprehensive cross-track analysis
// This analyzes multiple tracks together to find roads, airstrips, and bases
func RunCrossTrackAnalysis(segments []ClassifiedSegment, cellSizeMeters float64) *CrossTrackAnalysisResult {
	if cellSizeMeters <= 0 {
		cellSizeMeters = 10.0 // Default 10m grid cells
	}

	result := &CrossTrackAnalysisResult{}

	// Detect roads using grid analysis
	analyzer := NewCrossTrackAnalyzer(cellSizeMeters)
	result.Roads = analyzer.detectRoadsFromTracks(segments)

	// Detect airstrips from straight segments
	result.Airstrips = detectAirstrips(segments)

	// Detect bases from track endpoints
	result.Bases = detectBases(segments)

	return result
}

// processCrossTrackAnalysis integrates cross-track analysis into the learning job
func (l *GPXLearner) processCrossTrackAnalysis(ctx context.Context, parkID string, uploadID int64, segments []ClassifiedSegment, result *LearningResult) {
	// Run the cross-track analysis
	analysis := RunCrossTrackAnalysis(segments, 10.0)

	// Store detected roads
	for _, road := range analysis.Roads {
		if road.LengthM < 100 { // Skip very short segments
			continue
		}

		geojson, _ := json.Marshal(map[string]interface{}{
			"type":        "LineString",
			"coordinates": road.Points,
		})

		_, err := l.queries.CreateLearnedRoad(ctx, dbgen.CreateLearnedRoadParams{
			ParkID:        parkID,
			Geojson:       string(geojson),
			LengthM:       ptrFloat64(road.LengthM),
			MatchCount:    ptrInt64(int64(road.TrackCount)),
			ConfidencePct: ptrFloat64(road.Confidence),
		})
		if err == nil {
			result.NewRoads++
			result.NewRoadsKm += road.LengthM / 1000.0
			if road.Confidence > result.RoadConfidence {
				result.RoadConfidence = road.Confidence
			}
		}
	}

	// Store detected airstrips
	for _, airstrip := range analysis.Airstrips {
		// Check for existing nearby airstrips first
		existing, err := l.queries.FindNearbyAirstrips(ctx, dbgen.FindNearbyAirstripsParams{
			ParkID: parkID,
			Lat:    airstrip.Lat,
			Lon:    airstrip.Lon,
		})

		if err == nil && len(existing) > 0 {
			// Update existing
			as := existing[0]
			landingCount := int64(0)
			takeoffCount := int64(0)
			if airstrip.PatternType == "landing" {
				landingCount = 1
			} else {
				takeoffCount = 1
			}
			newConfidence := math.Min(*as.ConfidencePct+10.0, 95.0)
			l.queries.UpdateAirstripStats(ctx, dbgen.UpdateAirstripStatsParams{
				LandingCount:  ptrInt64(landingCount),
				TakeoffCount:  ptrInt64(takeoffCount),
				ConfidencePct: ptrFloat64(newConfidence),
				ID:            as.ID,
			})
		} else {
			// Create new
			landingCount := int64(0)
			takeoffCount := int64(0)
			if airstrip.PatternType == "landing" {
				landingCount = 1
			} else {
				takeoffCount = 1
			}

			_, err := l.queries.CreateLearnedAirstrip(ctx, dbgen.CreateLearnedAirstripParams{
				ParkID:        parkID,
				Lat:           airstrip.Lat,
				Lon:           airstrip.Lon,
				HeadingDeg:    ptrFloat64(airstrip.HeadingDeg),
				LengthM:       ptrFloat64(airstrip.LengthM),
				AircraftType:  ptrString(airstrip.AircraftType),
				LandingCount:  ptrInt64(landingCount),
				TakeoffCount:  ptrInt64(takeoffCount),
				ConfidencePct: ptrFloat64(airstrip.Confidence),
				ApproachJson:  nil,
			})
			if err == nil {
				result.NewAirstrips++
				if airstrip.Confidence > result.AirstripConfidence {
					result.AirstripConfidence = airstrip.Confidence
				}
			}
		}
	}

	// Store detected bases
	for _, base := range analysis.Bases {
		// Check for existing nearby places
		existing, err := l.queries.FindNearbyPlaces(ctx, dbgen.FindNearbyPlacesParams{
			ParkID: parkID,
			Lat:    base.Lat,
			Lon:    base.Lon,
		})

		if err == nil && len(existing) > 0 {
			// Update existing place
			place := existing[0]
			newConfidence := math.Min(*place.ConfidencePct+15.0, 95.0)
			l.queries.UpdatePlaceStats(ctx, dbgen.UpdatePlaceStatsParams{
				AvgDurationMinutes: ptrFloat64(base.AvgDurationMin),
				ConfidencePct:      ptrFloat64(newConfidence),
				ID:                 place.ID,
			})
		} else {
			// Create new place
			_, err := l.queries.CreateLearnedPlace(ctx, dbgen.CreateLearnedPlaceParams{
				ParkID:             parkID,
				Lat:                base.Lat,
				Lon:                base.Lon,
				PlaceType:          ptrString(base.PlaceType),
				VisitCount:         ptrInt64(int64(base.TrackCount)),
				AvgDurationMinutes: ptrFloat64(base.AvgDurationMin),
				ConfidencePct:      ptrFloat64(base.Confidence),
			})
			if err == nil {
				result.NewPlaces++
				result.PlaceTypes[base.PlaceType]++
				if base.Confidence > result.PlaceConfidence {
					result.PlaceConfidence = base.Confidence
				}
			}
		}
	}
}
