package srv

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"srv.exe.dev/db/dbgen"
	"srv.exe.dev/srv/gpx"
)

const maxUploadSize = 200 << 20 // 200MB (increased for large patrol data files)

// UploadResponse is the JSON response for file uploads.
type UploadResponse struct {
	FilesProcessed  int              `json:"files_processed"`
	TotalPoints     int              `json:"total_points"`
	TotalDistanceKm float64          `json:"total_distance_km"`
	Segments        []SegmentSummary `json:"segments"`
	Error           string           `json:"error,omitempty"`
	
	// Validation results
	Validation      *UploadValidationSummary `json:"validation,omitempty"`
}

// UploadValidationSummary provides user-friendly validation feedback
type UploadValidationSummary struct {
	IsValid           bool     `json:"is_valid"`
	ProtectedArea     string   `json:"protected_area,omitempty"`
	PatrolKm          float64  `json:"patrol_km"`
	RoadKm            float64  `json:"road_km"`
	BoundaryKm        float64  `json:"boundary_km"`
	ExcludedKm        float64  `json:"excluded_km"`
	StaticSegments    int      `json:"static_segments"`
	ExcludedSegments  int      `json:"excluded_segments"`
	Warnings          []string `json:"warnings,omitempty"`
	Errors            []string `json:"errors,omitempty"`
}

// SegmentSummary represents a processed segment in the upload response.
type SegmentSummary struct {
	StartTime    *time.Time `json:"start_time,omitempty"`
	EndTime      *time.Time `json:"end_time,omitempty"`
	MovementType string     `json:"movement_type,omitempty"`
	DistanceKm   float64    `json:"distance_km"`
	Points       int        `json:"points"`
	Area         string     `json:"area"`
	GridCellIDs  []string   `json:"grid_cells,omitempty"`
	Analysis     *GPXAnalysis `json:"analysis,omitempty"`
}

// uploadPageData is the data passed to the upload template.
type uploadPageData struct {
	Hostname  string
	UserEmail string
}

// HandleUpload handles POST requests for GPX file uploads.
// Allows anonymous uploads (uses anonymous user if not authenticated).
func (s *Server) HandleUpload(w http.ResponseWriter, r *http.Request) {
	// Get user from session (optional - allow anonymous uploads)
	user := s.Auth.GetUserFromRequest(r)
	var userID string = "anonymous"
	var userEmail string = "anonymous"
	if user != nil {
		userID = user.ID
		userEmail = user.Email
	}

	// Limit request body size
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadSize)

	// Parse multipart form
	if err := r.ParseMultipartForm(maxUploadSize); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(UploadResponse{
			Error: "failed to parse form: " + err.Error(),
		})
		return
	}
	defer r.MultipartForm.RemoveAll()

	// Get uploaded files
	files := r.MultipartForm.File["gpx"]
	if len(files) == 0 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(UploadResponse{
			Error: "no GPX files provided",
		})
		return
	}

	ctx := r.Context()

	// Calculate combined hash of all uploaded files for deduplication
	hasher := sha256.New()
	var fileContents [][]byte
	for _, fileHeader := range files {
		file, err := fileHeader.Open()
		if err != nil {
			continue
		}
		content, err := io.ReadAll(file)
		file.Close()
		if err != nil {
			continue
		}
		hasher.Write(content)
		fileContents = append(fileContents, content)
	}
	fileHash := hex.EncodeToString(hasher.Sum(nil))

	// Check for duplicate upload
	q := dbgen.New(s.DB)
	existing, err := q.GetGPXUploadByHash(ctx, &fileHash)
	if err == nil && existing.ID > 0 {
		// Duplicate found - return info about previous upload
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error": "duplicate_upload",
			"message": "This file has already been uploaded",
			"previous_upload": map[string]interface{}{
				"id":          existing.ID,
				"filename":    existing.Filename,
				"upload_date": existing.UploadDate,
				"distance_km": existing.TotalDistanceKm,
			},
		})
		return
	}

	var (
		totalPoints     int
		totalDistanceKm float64
		allSegments     []SegmentSummary
		filesProcessed  int
		lastValidation  *UploadValidationSummary
	)

	// Helper to process a single GPX file
	processGPX := func(filename string, reader io.Reader) error {
		gpxData, err := gpx.ParseGPX(reader)
		if err != nil {
			return err
		}

		filesProcessed++

		// Count points
		for _, track := range gpxData.Tracks {
			for _, seg := range track.Segments {
				totalPoints += len(seg)
			}
		}

		// Split into segments
		segments := gpx.SplitIntoSegments(gpxData, 0)

		// Remove straight-line gaps caused by GPS signal loss
		segments = gpx.RemoveStraightLineGaps(segments)

		// Process each segment (skip segments with < 2 points or 0 distance)
		for _, seg := range segments {
			if len(seg.Points) < 2 || seg.DistanceKm < 0.001 {
				continue
			}

			totalDistanceKm += seg.DistanceKm

			// Find area for segment (using first point)
			areaName := "outside"
			if len(seg.Points) > 0 && s.AreaStore != nil {
				if area := s.AreaStore.FindArea(seg.Points[0].Lat, seg.Points[0].Lon); area != nil {
					areaName = area.Name
				}
			}

			// Collect unique grid cells touched by this segment
			cellSet := make(map[string]bool)
			for _, pt := range seg.Points {
				cellSet[gridCellIDForPoint(pt.Lat, pt.Lon)] = true
			}
			gridCells := make([]string, 0, len(cellSet))
			for cell := range cellSet {
				gridCells = append(gridCells, cell)
			}

			// Perform GPX pattern analysis
			analysisPoints := make([]struct {
				Lat, Lon  float64
				Time      *time.Time
				Elevation *float64
				Desc      string
			}, len(seg.Points))
			for i, pt := range seg.Points {
				analysisPoints[i].Lat = pt.Lat
				analysisPoints[i].Lon = pt.Lon
				analysisPoints[i].Time = pt.Time
				analysisPoints[i].Elevation = pt.Elevation
				analysisPoints[i].Desc = pt.Desc
			}
			analysis := AnalyzeGPXSegment(analysisPoints)

			// Use analysis-derived movement type if available
			movementType := seg.MovementType
			if analysis.MovementType != "" {
				movementType = analysis.MovementType
			}

			allSegments = append(allSegments, SegmentSummary{
				StartTime:    seg.StartTime,
				EndTime:      seg.EndTime,
				MovementType: movementType,
				DistanceKm:   seg.DistanceKm,
				Points:       len(seg.Points),
				Area:         areaName,
				GridCellIDs:  gridCells,
				Analysis:     &analysis,
			})
		}

		// Validate and persist upload to database
		if s.DB != nil {
			validationResult, err := s.persistUploadWithValidation(ctx, userID, userEmail, filename, fileHash, segments)
			if err != nil {
				slog.Warn("failed to persist upload", "error", err, "filename", filename)
			} else {
				slog.Info("persisted upload", "filename", filename, "segments", len(segments), "valid", validationResult.IsValid)
				// Aggregate validation results
				if lastValidation == nil {
					lastValidation = &UploadValidationSummary{
						IsValid: validationResult.IsValid,
					}
				}
				lastValidation.PatrolKm += validationResult.PatrolKm
				lastValidation.RoadKm += validationResult.RoadKm
				lastValidation.BoundaryKm += validationResult.BoundaryKm
				lastValidation.ExcludedKm += validationResult.ExcludedKm
				lastValidation.StaticSegments += validationResult.StaticSegments
				lastValidation.ExcludedSegments += validationResult.ExcludedSegments
				if validationResult.ProtectedAreaName != "" {
					lastValidation.ProtectedArea = validationResult.ProtectedAreaName
				}
				lastValidation.Warnings = append(lastValidation.Warnings, validationResult.ValidationWarnings...)
				lastValidation.Errors = append(lastValidation.Errors, validationResult.ValidationErrors...)
				if !validationResult.IsValid {
					lastValidation.IsValid = false
				}
			}
		}
		return nil
	}

	// Process each uploaded file
	for _, fileHeader := range files {
		file, err := fileHeader.Open()
		if err != nil {
			continue
		}

		filename := strings.ToLower(fileHeader.Filename)

		// Check if it's a zip file
		if strings.HasSuffix(filename, ".zip") {
			// Read zip into memory
			data, err := io.ReadAll(file)
			file.Close()
			if err != nil {
				slog.Error("failed to read zip file", "error", err)
				continue
			}

			// Open as zip archive
			zipReader, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
			if err != nil {
				slog.Error("failed to open zip archive", "error", err)
				continue
			}

			// Process each GPX file in the zip
			for _, zf := range zipReader.File {
				zfName := strings.ToLower(zf.Name)
				// Skip Mac OS X metadata and non-GPX files
				if strings.Contains(zfName, "__macosx") || !strings.HasSuffix(zfName, ".gpx") {
					continue
				}

				zfReader, err := zf.Open()
				if err != nil {
					continue
				}

				if err := processGPX(zf.Name, zfReader); err != nil {
					slog.Debug("failed to parse GPX from zip", "file", zf.Name, "error", err)
				}
				zfReader.Close()
			}
			continue
		}

		// Regular GPX file
		if !strings.HasSuffix(filename, ".gpx") {
			file.Close()
			continue
		}

		if err := processGPX(fileHeader.Filename, file); err != nil {
			slog.Debug("failed to parse GPX", "file", fileHeader.Filename, "error", err)
		}
		file.Close()
	}

	// Return response
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(UploadResponse{
		FilesProcessed:  filesProcessed,
		TotalPoints:     totalPoints,
		TotalDistanceKm: totalDistanceKm,
		Segments:        allSegments,
		Validation:      lastValidation,
	})
}

// HandleUploadPage renders the upload form page.
func (s *Server) HandleUploadPage(w http.ResponseWriter, r *http.Request) {
	user := s.Auth.GetUserFromRequest(r)
	userEmail := ""
	if user != nil {
		userEmail = user.Email
	}

	data := uploadPageData{
		Hostname:  s.Hostname,
		UserEmail: userEmail,
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := s.renderTemplate(w, "upload.html", data); err != nil {
		http.Error(w, "failed to render template", http.StatusInternalServerError)
	}
}

const (
	// maxTrackPointsPerUpload limits stored track points to control DB size.
	maxTrackPointsPerUpload = 1000
	// gridCellSize is the grid resolution in degrees (0.1° ≈ 10km at equator).
	gridCellSize = 0.1
)

// persistUpload saves GPX upload data to the database including:
// - gpx_uploads record for metadata
// - track_points (sampled if > maxTrackPointsPerUpload)
// - effort_data grid cell aggregates
func (s *Server) persistUpload(ctx context.Context, userID, userEmail, filename, fileHash string, segments []gpx.Segment) (int64, error) {
	if len(segments) == 0 {
		return 0, nil
	}

	q := dbgen.New(s.DB)

	// Ensure user exists (create if not)
	_, err := q.GetUser(ctx, userID)
	if err != nil {
		// User doesn't exist, create them with approved role for simplicity
		err = q.CreateUser(ctx, dbgen.CreateUserParams{
			ID:               userID,
			Email:            userEmail,
			Name:             "",
			Organization:     "",
			OrganizationType: "",
			Role:             "approved",
			CreatedAt:        time.Now(),
		})
		if err != nil {
			return 0, fmt.Errorf("create user: %w", err)
		}
	}

	// Aggregate stats across all segments
	var (
		totalPoints     int
		totalDistanceKm float64
		startTime       *time.Time
		endTime         *time.Time
		movementType    = "foot" // default
		typeDistKm      = make(map[string]float64)
	)

	for _, seg := range segments {
		totalPoints += len(seg.Points)
		totalDistanceKm += seg.DistanceKm

		// Track earliest start and latest end
		if seg.StartTime != nil && (startTime == nil || seg.StartTime.Before(*startTime)) {
			startTime = seg.StartTime
		}
		if seg.EndTime != nil && (endTime == nil || seg.EndTime.After(*endTime)) {
			endTime = seg.EndTime
		}

		// Track distance per movement type for majority vote
		if seg.MovementType != "" {
			typeDistKm[seg.MovementType] += seg.DistanceKm
		}
	}

	// Use movement type that covers the most distance
	var maxDist float64
	for mt, dist := range typeDistKm {
		if dist > maxDist {
			maxDist = dist
			movementType = mt
		}
	}

	// Create gpx_uploads record
	processingStatus := "complete"
	uploadID, err := q.CreateGPXUpload(ctx, dbgen.CreateGPXUploadParams{
		UserID:           userID,
		Filename:         filename,
		MovementType:     movementType,
		ProtectedAreaID:  nil, // TODO: could be computed from area store
		UploadDate:       time.Now(),
		StartTime:        startTime,
		EndTime:          endTime,
		TotalDistanceKm:  totalDistanceKm,
		TotalPoints:      int64(totalPoints),
		FileHash:         &fileHash,
		ProcessingStatus: &processingStatus,
	})
	if err != nil {
		return 0, fmt.Errorf("create gpx upload: %w", err)
	}

	// Collect all points from all segments
	var allPoints []gpx.Point
	for _, seg := range segments {
		allPoints = append(allPoints, seg.Points...)
	}

	// Sample points if needed (keep max N points)
	sampledPoints := samplePoints(allPoints, maxTrackPointsPerUpload)

	// Store sampled track points
	for _, pt := range sampledPoints {
		gridCellID := gridCellIDForPoint(pt.Lat, pt.Lon)

		// Ensure grid cell exists
		latCenter, lonCenter := gridCellCenter(pt.Lat, pt.Lon)
		latMin, latMax, lonMin, lonMax := gridCellBounds(pt.Lat, pt.Lon)
		_, err := q.GetOrCreateGridCell(ctx, dbgen.GetOrCreateGridCellParams{
			ID:        gridCellID,
			LatCenter: latCenter,
			LonCenter: lonCenter,
			LatMin:    latMin,
			LatMax:    latMax,
			LonMin:    lonMin,
			LonMax:    lonMax,
		})
		if err != nil {
			return 0, fmt.Errorf("create grid cell: %w", err)
		}

		gridCellIDPtr := &gridCellID
		err = q.CreateTrackPoint(ctx, dbgen.CreateTrackPointParams{
			UploadID:   uploadID,
			Lat:        pt.Lat,
			Lon:        pt.Lon,
			Elevation:  pt.Elevation,
			Timestamp:  pt.Time,
			GridCellID: gridCellIDPtr,
		})
		if err != nil {
			return 0, fmt.Errorf("create track point: %w", err)
		}
	}

	// Determine park for settlement visits and notifications.
	// (Learning queue is handled by processUploadAsync with per-park dedup.)
	var parkID *string
	if len(segments) > 0 && len(segments[0].Points) > 0 && s.AreaStore != nil {
		pt := segments[0].Points[0]
		if area := s.AreaStore.FindArea(pt.Lat, pt.Lon); area != nil {
			parkID = &area.ID
		}
	}

	// Update effort_data grid cells (non-fatal error - continue even if this fails)
	if err := s.updateEffortData(ctx, q, segments, uploadID); err != nil {
		slog.Warn("failed to update effort data", "uploadID", uploadID, "error", err)
		// Don't fail the whole upload - learning and basic data is more important
	}

	// Track settlement visits (non-fatal)
	if parkID != nil {
		if err := s.trackSettlementVisits(ctx, q, segments, uploadID, *parkID); err != nil {
			slog.Warn("failed to track settlement visits", "uploadID", uploadID, "error", err)
		}
	}

	// Create notification for new upload (non-fatal) - always create, even outside parks
	{
		var totalDistKm float64
		for _, seg := range segments {
			totalDistKm += seg.DistanceKm
		}
		
		// Calculate centroid from all points
		var centroidLat, centroidLon float64
		var pointCount int
		for _, seg := range segments {
			for _, pt := range seg.Points {
				centroidLat += pt.Lat
				centroidLon += pt.Lon
				pointCount++
			}
		}
		if pointCount > 0 {
			centroidLat /= float64(pointCount)
			centroidLon /= float64(pointCount)
		}
		
		// Determine location name and bounds
		var locationName string
		var notifParkID string
		var minLat, maxLat, minLon, maxLon float64
		var hasBounds bool
		
		if parkID != nil {
			notifParkID = *parkID
			locationName = *parkID
			if s.AreaStore != nil {
				for _, a := range s.AreaStore.Areas {
					if a.ID == *parkID {
						locationName = a.Name
						minLat, maxLat, minLon, maxLon = a.GetBoundingBox()
						hasBounds = true
						break
					}
				}
			}
		} else {
			// Outside any park - use coordinates as location
			notifParkID = "_outside"
			latDir := "N"
			if centroidLat < 0 {
				latDir = "S"
			}
			lonDir := "E"
			if centroidLon < 0 {
				lonDir = "W"
			}
			locationName = fmt.Sprintf("%.2f°%s, %.2f°%s", math.Abs(centroidLat), latDir, math.Abs(centroidLon), lonDir)
		}
		
		// Get grid cells for this upload and find the largest cluster
		type cellInfo struct {
			id  string
			lat float64
			lon float64
		}
		var allCells []cellInfo
		rows, err := s.DB.QueryContext(ctx, `
			SELECT DISTINCT grid_cell_id FROM track_points 
			WHERE upload_id = ? AND grid_cell_id IS NOT NULL
		`, uploadID)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var cellID string
				if rows.Scan(&cellID) == nil {
					parts := strings.Split(cellID, "_")
					if len(parts) == 2 {
						lat, _ := strconv.ParseFloat(parts[0], 64)
						lon, _ := strconv.ParseFloat(parts[1], 64)
						allCells = append(allCells, cellInfo{cellID, lat, lon})
					}
				}
			}
		}

		// Find largest cluster (prefer cells within park if hasBounds)
		gridCells := []string{}
		if len(allCells) > 0 {
			clusters := make(map[string][]cellInfo)
			for _, c := range allCells {
				// If inside park bounds, put in "park" cluster
				if hasBounds {
					buffer := 0.5
					if c.lat >= minLat-buffer && c.lat <= maxLat+buffer &&
						c.lon >= minLon-buffer && c.lon <= maxLon+buffer {
						clusters["park"] = append(clusters["park"], c)
						continue
					}
				}
				// Cluster by 10-degree grid
				key := fmt.Sprintf("%.0f_%.0f", math.Floor(c.lat/10)*10, math.Floor(c.lon/10)*10)
				clusters[key] = append(clusters[key], c)
			}

			// Find best cluster (prefer park, then largest)
			var bestCluster []cellInfo
			if parkCells, ok := clusters["park"]; ok && len(parkCells) > 0 {
				bestCluster = parkCells
			} else {
				for _, v := range clusters {
					if len(v) > len(bestCluster) {
						bestCluster = v
					}
				}
			}

			// Use cluster cells and recalculate centroid
			var clusterLat, clusterLon float64
			for _, c := range bestCluster {
				gridCells = append(gridCells, c.id)
				clusterLat += c.lat
				clusterLon += c.lon
			}
			if len(bestCluster) > 0 {
				centroidLat = clusterLat / float64(len(bestCluster))
				centroidLon = clusterLon / float64(len(bestCluster))
				// Update location name if outside park
				if notifParkID == "_outside" {
					latDir := "N"
					if centroidLat < 0 {
						latDir = "S"
					}
					lonDir := "E"
					if centroidLon < 0 {
						lonDir = "W"
					}
					locationName = fmt.Sprintf("%.2f°%s, %.2f°%s", math.Abs(centroidLat), latDir, math.Abs(centroidLon), lonDir)
				}
			}
		}
		
		// Get date range from track points
		var minDate, maxDate sql.NullString
		s.DB.QueryRowContext(ctx, `
			SELECT MIN(DATE(timestamp)), MAX(DATE(timestamp)) 
			FROM track_points WHERE upload_id = ? AND timestamp IS NOT NULL
		`, uploadID).Scan(&minDate, &maxDate)

		// Build reference_data JSON with grid cells, centroid, and date range
		refData := map[string]interface{}{
			"grid_cells": gridCells,
			"lat":        centroidLat,
			"lon":        centroidLon,
			"upload_id":  uploadID,
		}
		if minDate.Valid {
			refData["date_from"] = minDate.String
		}
		if maxDate.Valid {
			refData["date_to"] = maxDate.String
		}
		refDataJSON, _ := json.Marshal(refData)
		
		_, err = s.DB.ExecContext(ctx, `
			INSERT INTO notifications (park_id, notification_type, title, message, reference_id, reference_data, created_at)
			VALUES (?, 'new_upload', ?, ?, ?, ?, CURRENT_TIMESTAMP)`,
			notifParkID,
			fmt.Sprintf("New Patrol Data: %s", locationName),
			fmt.Sprintf("%.1f km patrol uploaded with %d segments", totalDistKm, len(segments)),
			fmt.Sprintf("%d", uploadID),
			string(refDataJSON),
		)
		if err != nil {
			slog.Warn("failed to create upload notification", "uploadID", uploadID, "error", err)
		}
	}

	return uploadID, nil
}

// samplePoints returns a subset of points, evenly distributed across the input.
// If len(points) <= maxPoints, returns all points.
func samplePoints(points []gpx.Point, maxPoints int) []gpx.Point {
	if len(points) <= maxPoints {
		return points
	}

	result := make([]gpx.Point, 0, maxPoints)
	step := float64(len(points)-1) / float64(maxPoints-1)

	for i := 0; i < maxPoints; i++ {
		idx := int(math.Round(float64(i) * step))
		if idx >= len(points) {
			idx = len(points) - 1
		}
		result = append(result, points[idx])
	}

	return result
}

// gridCellIDForPoint returns the grid cell ID for a lat/lon coordinate.
// Format: "lat_lon" with 1 decimal place (e.g., "-2.3_34.8").
func gridCellIDForPoint(lat, lon float64) string {
	// Round to nearest 0.1 degree
	latGrid := math.Floor(lat/gridCellSize) * gridCellSize
	lonGrid := math.Floor(lon/gridCellSize) * gridCellSize
	return fmt.Sprintf("%.1f_%.1f", latGrid, lonGrid)
}

// gridCellCenter returns the center lat/lon for a grid cell.
func gridCellCenter(lat, lon float64) (latCenter, lonCenter float64) {
	latGrid := math.Floor(lat/gridCellSize) * gridCellSize
	lonGrid := math.Floor(lon/gridCellSize) * gridCellSize
	return latGrid + gridCellSize/2, lonGrid + gridCellSize/2
}

// gridCellBounds returns the min/max bounds for a grid cell.
func gridCellBounds(lat, lon float64) (latMin, latMax, lonMin, lonMax float64) {
	latGrid := math.Floor(lat/gridCellSize) * gridCellSize
	lonGrid := math.Floor(lon/gridCellSize) * gridCellSize
	return latGrid, latGrid + gridCellSize, lonGrid, lonGrid + gridCellSize
}

// gridCellStats holds aggregated stats for a single grid cell.
type gridCellStats struct {
	DistanceKm   float64
	PointCount   int
	MovementType string
}

// updateEffortData computes which grid cells each segment passes through
// and updates the effort_data table with aggregated statistics.
func (s *Server) updateEffortData(ctx context.Context, q *dbgen.Queries, segments []gpx.Segment, uploadID int64) error {
	// Determine the time period for effort data (use upload time if no timestamps)
	now := time.Now()
	year := int64(now.Year())
	month := int64(now.Month())

	// Find earliest segment time to use for year/month
	for _, seg := range segments {
		if seg.StartTime != nil {
			year = int64(seg.StartTime.Year())
			month = int64(seg.StartTime.Month())
			break
		}
	}

	// Aggregate stats by grid cell and movement type
	cellStats := make(map[string]*gridCellStats) // key: "cellID:movementType"

	for _, seg := range segments {
		if len(seg.Points) < 2 {
			continue
		}

		// Walk through points, attributing distance and count to grid cells
		for i := 1; i < len(seg.Points); i++ {
			p1 := seg.Points[i-1]
			p2 := seg.Points[i]

			// Calculate segment distance
			segDist := haversineDistanceKm(p1.Lat, p1.Lon, p2.Lat, p2.Lon)

			// Attribute to the grid cell of the midpoint
			midLat := (p1.Lat + p2.Lat) / 2
			midLon := (p1.Lon + p2.Lon) / 2
			cellID := gridCellIDForPoint(midLat, midLon)

			key := cellID + ":" + seg.MovementType
			if cellStats[key] == nil {
				cellStats[key] = &gridCellStats{
					MovementType: seg.MovementType,
				}
			}
			cellStats[key].DistanceKm += segDist
			cellStats[key].PointCount++
		}
	}

	// Also aggregate "all" movement type for easier querying
	allCellStats := make(map[string]*gridCellStats) // key: cellID
	for key, stats := range cellStats {
		cellID := strings.Split(key, ":")[0]
		if allCellStats[cellID] == nil {
			allCellStats[cellID] = &gridCellStats{MovementType: "all"}
		}
		allCellStats[cellID].DistanceKm += stats.DistanceKm
		allCellStats[cellID].PointCount += stats.PointCount
	}

	// Ensure grid cells exist and update effort data
	for key, stats := range cellStats {
		keyParts := strings.Split(key, ":")
		cellID := keyParts[0]

		// Parse lat/lon from cellID
		coordParts := strings.Split(cellID, "_")
		if len(coordParts) != 2 {
			continue
		}
		lat, _ := strconv.ParseFloat(coordParts[0], 64)
		lon, _ := strconv.ParseFloat(coordParts[1], 64)

		// Ensure grid cell exists
		latCenter, lonCenter := gridCellCenter(lat, lon)
		latMin, latMax, lonMin, lonMax := gridCellBounds(lat, lon)

		_, err := q.GetOrCreateGridCell(ctx, dbgen.GetOrCreateGridCellParams{
			ID:        cellID,
			LatCenter: latCenter,
			LonCenter: lonCenter,
			LatMin:    latMin,
			LatMax:    latMax,
			LonMin:    lonMin,
			LonMax:    lonMax,
		})
		if err != nil {
			return fmt.Errorf("get or create grid cell %s: %w", cellID, err)
		}

		// Upsert effort data for this specific movement type
		err = q.UpsertEffortData(ctx, dbgen.UpsertEffortDataParams{
			GridCellID:       cellID,
			Year:             year,
			Month:            month,
			Day:              nil, // monthly aggregate
			MovementType:     stats.MovementType,
			TotalDistanceKm:  stats.DistanceKm,
			TotalPoints:      int64(stats.PointCount),
			UniqueUploads:    1,
			ProtectedAreaIds: nil,
		})
		if err != nil {
			return fmt.Errorf("upsert effort data for %s: %w", key, err)
		}
	}

	// Also upsert "all" movement type aggregates
	for cellID, stats := range allCellStats {
		err := q.UpsertEffortData(ctx, dbgen.UpsertEffortDataParams{
			GridCellID:       cellID,
			Year:             year,
			Month:            month,
			Day:              nil,
			MovementType:     "all",
			TotalDistanceKm:  stats.DistanceKm,
			TotalPoints:      int64(stats.PointCount),
			UniqueUploads:    1,
			ProtectedAreaIds: nil,
		})
		if err != nil {
			return fmt.Errorf("upsert effort data (all) for %s: %w", cellID, err)
		}
	}

	// Track subcell visits for spatial coverage calculation
	if err := s.trackSubcellVisits(ctx, q, segments, year, month); err != nil {
		return fmt.Errorf("track subcell visits: %w", err)
	}

	return nil
}

// rebuildAllEffortData truncates effort_data and rebuilds it from all remaining
// completed uploads using their distance breakdowns and track_points grid cells.
// This is called asynchronously after an upload is deleted.
func (s *Server) rebuildAllEffortData() {
	ctx := context.Background()
	q := dbgen.New(s.DB)

	slog.Info("starting effort_data rebuild after upload deletion")

	// Truncate effort_data
	if _, err := s.DB.ExecContext(ctx, "DELETE FROM effort_data"); err != nil {
		slog.Error("rebuildAllEffortData: failed to truncate effort_data", "error", err)
		return
	}

	// Get all completed upload logs with distance breakdown and linked upload_id
	rows, err := s.DB.QueryContext(ctx, `
		SELECT l.id, l.upload_id, l.foot_km, l.vehicle_km, l.aircraft_km, l.upload_time
		FROM gpx_upload_logs l
		WHERE l.processing_status = 'completed'
		  AND l.upload_id IS NOT NULL
		ORDER BY l.id
	`)
	if err != nil {
		slog.Error("rebuildAllEffortData: failed to list uploads", "error", err)
		return
	}
	defer rows.Close()

	type uploadInfo struct {
		logID      int64
		uploadID   int64
		footKm     float64
		vehicleKm  float64
		aircraftKm float64
		uploadTime string
	}
	var uploads []uploadInfo
	for rows.Next() {
		var u uploadInfo
		if err := rows.Scan(&u.logID, &u.uploadID, &u.footKm, &u.vehicleKm, &u.aircraftKm, &u.uploadTime); err != nil {
			slog.Warn("rebuildAllEffortData: scan error", "error", err)
			continue
		}
		uploads = append(uploads, u)
	}
	rows.Close()

	var rebuilt int
	for _, u := range uploads {
		// Get grid cell distribution from track_points
		cellRows, err := s.DB.QueryContext(ctx, `
			SELECT grid_cell_id, COUNT(*) as pts
			FROM track_points
			WHERE upload_id = ? AND grid_cell_id IS NOT NULL
			GROUP BY grid_cell_id
		`, u.uploadID)
		if err != nil {
			slog.Warn("rebuildAllEffortData: failed to get grid cells", "logID", u.logID, "error", err)
			continue
		}

		type cellInfo struct {
			id  string
			pts int64
		}
		var cells []cellInfo
		var totalPts int64
		for cellRows.Next() {
			var c cellInfo
			if err := cellRows.Scan(&c.id, &c.pts); err != nil {
				continue
			}
			cells = append(cells, c)
			totalPts += c.pts
		}
		cellRows.Close()

		if len(cells) == 0 || totalPts == 0 {
			continue
		}

		// Determine year/month from upload_time (Go format: "2006-01-02 15:04:05...")
		year := int64(time.Now().Year())
		month := int64(time.Now().Month())
		if len(u.uploadTime) >= 10 {
			if t, err := time.Parse("2006-01-02", u.uploadTime[:10]); err == nil {
				year = int64(t.Year())
				month = int64(t.Month())
			}
		}

		// Distribute distance proportionally across grid cells by point count
		totalKm := u.footKm + u.vehicleKm + u.aircraftKm
		if totalKm <= 0 {
			continue
		}
		for _, cell := range cells {
			fraction := float64(cell.pts) / float64(totalPts)

			// Movement-type-specific effort
			for _, mt := range []struct {
				name string
				km   float64
			}{
				{"foot", u.footKm},
				{"vehicle", u.vehicleKm},
				{"aircraft", u.aircraftKm},
			} {
				if mt.km <= 0 {
					continue
				}
				err := q.UpsertEffortData(ctx, dbgen.UpsertEffortDataParams{
					GridCellID:      cell.id,
					Year:            year,
					Month:           month,
					Day:             nil,
					MovementType:    mt.name,
					TotalDistanceKm: mt.km * fraction,
					TotalPoints:     int64(float64(cell.pts) * (mt.km / totalKm)),
					UniqueUploads:   1,
				})
				if err != nil {
					slog.Warn("rebuildAllEffortData: upsert error", "cell", cell.id, "error", err)
				}
			}

			// "all" aggregate
			err := q.UpsertEffortData(ctx, dbgen.UpsertEffortDataParams{
				GridCellID:      cell.id,
				Year:            year,
				Month:           month,
				Day:             nil,
				MovementType:    "all",
				TotalDistanceKm: totalKm * fraction,
				TotalPoints:     cell.pts,
				UniqueUploads:   1,
			})
			if err != nil {
				slog.Warn("rebuildAllEffortData: upsert all error", "cell", cell.id, "error", err)
			}
		}

		rebuilt++
	}

	slog.Info("effort_data rebuild complete", "uploads_processed", rebuilt)
}

// haversineDistanceKm calculates the great-circle distance in kilometers.
func haversineDistanceKm(lat1, lon1, lat2, lon2 float64) float64 {
	const earthRadiusKm = 6371.0

	lat1Rad := lat1 * math.Pi / 180
	lat2Rad := lat2 * math.Pi / 180
	deltaLat := (lat2 - lat1) * math.Pi / 180
	deltaLon := (lon2 - lon1) * math.Pi / 180

	a := math.Sin(deltaLat/2)*math.Sin(deltaLat/2) +
		math.Cos(lat1Rad)*math.Cos(lat2Rad)*
			math.Sin(deltaLon/2)*math.Sin(deltaLon/2)

	c := 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))

	return earthRadiusKm * c
}

// subcellIDForPoint returns the subcell ID (0-9 row/col) within a 10km x 10km grid cell
// Each grid cell is divided into 100 subcells of ~1km x 1km
func subcellIDForPoint(lat, lon float64) string {
	// Get the grid cell bounds
	latMin, _, lonMin, _ := gridCellBounds(lat, lon)
	
	// Calculate position within the cell (0-1 range)
	latPos := (lat - latMin) / gridCellSize
	lonPos := (lon - lonMin) / gridCellSize
	
	// Convert to subcell index (0-9)
	row := int(latPos * 10)
	col := int(lonPos * 10)
	
	// Clamp to valid range
	if row < 0 { row = 0 }
	if row > 9 { row = 9 }
	if col < 0 { col = 0 }
	if col > 9 { col = 9 }
	
	return fmt.Sprintf("%d_%d", row, col)
}

// trackSubcellVisits records which subcells within each grid cell have been visited
// Uses the actual point timestamps for day-level granularity
func (s *Server) trackSubcellVisits(ctx context.Context, q *dbgen.Queries, segments []gpx.Segment, defaultYear, defaultMonth int64) error {
	// Track visited subcells per grid cell per day
	// Key: "gridCellID:subcellID:date" -> {lat, lon}
	type subcellInfo struct {
		lat, lon float64
	}
	visitedSubcells := make(map[string]subcellInfo)
	
	defaultDate := time.Date(int(defaultYear), time.Month(defaultMonth), 1, 0, 0, 0, 0, time.UTC)
	
	for _, seg := range segments {
		for _, pt := range seg.Points {
			gridCellID := gridCellIDForPoint(pt.Lat, pt.Lon)
			subcellID := subcellIDForPoint(pt.Lat, pt.Lon)
			
			// Use point timestamp if available, otherwise default date
			visitDate := defaultDate
			if pt.Time != nil {
				visitDate = time.Date(pt.Time.Year(), pt.Time.Month(), pt.Time.Day(), 0, 0, 0, 0, time.UTC)
			}
			
			key := fmt.Sprintf("%s:%s:%s", gridCellID, subcellID, visitDate.Format("2006-01-02"))
			visitedSubcells[key] = subcellInfo{lat: pt.Lat, lon: pt.Lon}
		}
	}
	
	// Collect unique grid cells that need to be created
	gridCellsNeeded := make(map[string]subcellInfo)
	for key, info := range visitedSubcells {
		parts := strings.Split(key, ":")
		if len(parts) < 1 {
			continue
		}
		gridCellID := parts[0]
		gridCellsNeeded[gridCellID] = info
	}
	
	// Ensure all grid cells exist before inserting subcell visits
	for gridCellID, info := range gridCellsNeeded {
		latCenter, lonCenter := gridCellCenter(info.lat, info.lon)
		latMin, latMax, lonMin, lonMax := gridCellBounds(info.lat, info.lon)
		
		_, err := q.GetOrCreateGridCell(ctx, dbgen.GetOrCreateGridCellParams{
			ID:        gridCellID,
			LatCenter: latCenter,
			LonCenter: lonCenter,
			LatMin:    latMin,
			LatMax:    latMax,
			LonMin:    lonMin,
			LonMax:    lonMax,
		})
		if err != nil {
			return fmt.Errorf("ensure grid cell %s exists: %w", gridCellID, err)
		}
	}
	
	// Store subcell visits with day granularity
	for key := range visitedSubcells {
		parts := strings.Split(key, ":")
		if len(parts) != 3 {
			continue
		}
		gridCellID := parts[0]
		subcellID := parts[1]
		visitDateStr := parts[2]
		
		visitDate, err := time.Parse("2006-01-02", visitDateStr)
		if err != nil {
			continue
		}
		
		err = q.UpsertSubcellVisit(ctx, dbgen.UpsertSubcellVisitParams{
			GridCellID: gridCellID,
			SubcellID:  subcellID,
			VisitDate:  visitDate,
		})
		if err != nil {
			return fmt.Errorf("upsert subcell visit: %w", err)
		}
	}
	
	return nil
}

// persistUploadWithValidation validates, classifies, and persists GPX upload data
// Returns the validation result for user feedback
func (s *Server) persistUploadWithValidation(ctx context.Context, userID, userEmail, filename, fileHash string, segments []gpx.Segment) (*GPXValidationResult, error) {
	// Run validation and classification on pre-processed segments
	// (already split and gap-cleaned by caller)
	validationResult := ValidateAndClassifyGPX(segments)
	
	// Find protected area per segment using median point (robust to transit).
	// The overall upload is assigned to the park with the most segment distance.
	parkDistKm := make(map[string]float64)      // park_id -> total km
	parkNames := make(map[string]string)          // park_id -> name
	segmentParks := make(map[int]string)          // segment_index -> park_id
	if s.AreaStore != nil {
		for i, seg := range segments {
			if len(seg.Points) == 0 {
				continue
			}
			// Try median point first (most representative)
			mid := seg.Points[len(seg.Points)/2]
			area := s.AreaStore.FindArea(mid.Lat, mid.Lon)
			// Fall back to first/last point
			if area == nil {
				area = s.AreaStore.FindArea(seg.Points[0].Lat, seg.Points[0].Lon)
			}
			if area == nil {
				area = s.AreaStore.FindArea(seg.Points[len(seg.Points)-1].Lat, seg.Points[len(seg.Points)-1].Lon)
			}
			if area != nil {
				segmentParks[i] = area.ID
				parkNames[area.ID] = area.Name
				// Use classified segment distance if available, else raw
				dist := 0.0
				if i < len(validationResult.ClassifiedSegments) {
					dist = validationResult.ClassifiedSegments[i].DistanceKm
				}
				parkDistKm[area.ID] += dist
			}
		}
	}
	// Pick majority park by distance
	var bestPark string
	var bestDist float64
	for pid, d := range parkDistKm {
		if d > bestDist {
			bestDist = d
			bestPark = pid
		}
	}
	if bestPark != "" {
		validationResult.ProtectedAreaID = bestPark
		validationResult.ProtectedAreaName = parkNames[bestPark]
	}
	
	q := dbgen.New(s.DB)
	
	// Determine processing status
	processingStatus := "completed"
	var rejectionReason *string
	if !validationResult.IsValid {
		processingStatus = "rejected"
		if len(validationResult.ValidationErrors) > 0 {
			reason := validationResult.ValidationErrors[0]
			rejectionReason = &reason
		}
	} else if validationResult.PatrolKm == 0 {
		processingStatus = "partial"
	}
	
	// Convert arrays to JSON
	errorsJSON, _ := json.Marshal(validationResult.ValidationErrors)
	warningsJSON, _ := json.Marshal(validationResult.ValidationWarnings)
	segmentsJSON, _ := json.Marshal(validationResult.ClassifiedSegments)
	
	// Count segments by type
	patrolSegments := 0
	for _, seg := range validationResult.ClassifiedSegments {
		if seg.Classification == "patrol" {
			patrolSegments++
		}
	}
	
	// Get upload_id - we'll update this after persisting
	var uploadID *int64
	var logID int64
	var err error
	
	// Log the upload processing
	ms := validationResult.MovementStats
	logID, err = q.CreateGPXUploadLog(ctx, dbgen.CreateGPXUploadLogParams{
		UploadID:              uploadID,
		UserID:                userID,
		UserEmail:             &userEmail,
		Filename:              filename,
		UploadTime:            time.Now(),
		IsValid:               validationResult.IsValid,
		TotalPoints:           int64(validationResult.TotalPoints),
		ValidationErrors:      strPtr(string(errorsJSON)),
		ValidationWarnings:    strPtr(string(warningsJSON)),
		ProtectedAreaID:       strPtr(validationResult.ProtectedAreaID),
		ProtectedAreaName:     strPtr(validationResult.ProtectedAreaName),
		PatrolKm:              validationResult.PatrolKm,
		RoadKm:                validationResult.RoadKm,
		BoundaryKm:            validationResult.BoundaryKm,
		ExcludedKm:            validationResult.ExcludedKm,
		TotalSegments:         int64(len(validationResult.ClassifiedSegments)),
		PatrolSegments:        int64(patrolSegments),
		StaticSegments:        int64(validationResult.StaticSegments),
		ExcludedSegments:      int64(validationResult.ExcludedSegments),
		ClassifiedSegmentsJson: strPtr(string(segmentsJSON)),
		ProcessingStatus:      &processingStatus,
		RejectionReason:       rejectionReason,
		// Movement type stats
		FootSegments:          ptrInt64(int64(ms.FootSegments)),
		FootKm:                ptrFloat64(ms.FootKm),
		FootMinutes:           ptrFloat64(ms.FootMinutes),
		VehicleSegments:       ptrInt64(int64(ms.VehicleSegments)),
		VehicleKm:             ptrFloat64(ms.VehicleKm),
		VehicleMinutes:        ptrFloat64(ms.VehicleMinutes),
		AircraftSegments:      ptrInt64(int64(ms.AircraftSegments)),
		AircraftKm:            ptrFloat64(ms.AircraftKm),
		AircraftMinutes:       ptrFloat64(ms.AircraftMinutes),
		// Special categories
		ReconSegments:         ptrInt64(int64(ms.ReconSegments)),
		ReconKm:               ptrFloat64(ms.ReconKm),
		ReconMinutes:          ptrFloat64(ms.ReconMinutes),
		FastVehicleSegments:   ptrInt64(int64(ms.FastVehicleSegments)),
		FastVehicleKm:         ptrFloat64(ms.FastVehicleKm),
		FastVehicleMinutes:    ptrFloat64(ms.FastVehicleMinutes),
		// Activity type stats
		TransitSegments:       ptrInt64(int64(ms.TransitSegments)),
		TransitKm:             ptrFloat64(ms.TransitKm),
		LogisticsSegments:     ptrInt64(int64(ms.LogisticsSegments)),
		LogisticsKm:           ptrFloat64(ms.LogisticsKm),
	})
	if err != nil {
		slog.Warn("failed to create gpx upload log", "error", err)
	}
	
	// If valid, persist to the original upload tables using the raw segments.
	// Include uploads with any patrol or aircraft effort (aerial surveys count).
	totalEffortKm := validationResult.PatrolKm + validationResult.MovementStats.AircraftKm
	slog.Info("upload validation", "isValid", validationResult.IsValid, "patrolKm", validationResult.PatrolKm, "aircraftKm", validationResult.MovementStats.AircraftKm, "segments", len(segments))
	if validationResult.IsValid && totalEffortKm > 0 {
		// Apply validation classifier's movement types to the raw segments.
		// The processor-level classifier (speed-only) may disagree with the
		// validation classifier (multi-signal scoring), e.g. classifying
		// 80-120 km/h aircraft as vehicle. Also filter out non-effort segments
		// (logistics, road, auto_generated, static).
		var patrolOnlySegments []gpx.Segment
		for i, seg := range segments {
			if i < len(validationResult.ClassifiedSegments) {
				cs := validationResult.ClassifiedSegments[i]
				if !cs.IncludeInEffort {
					continue // skip logistics, road, auto_generated, static
				}
				if cs.MovementType != "" {
					seg.MovementType = cs.MovementType
				}
			}
			patrolOnlySegments = append(patrolOnlySegments, seg)
		}
		slog.Info("persisting patrol segments", "count", len(patrolOnlySegments), "filtered_from", len(segments))
		
		if len(patrolOnlySegments) > 0 {
			persistID, err := s.persistUpload(ctx, userID, userEmail, filename, fileHash, patrolOnlySegments)
			if err != nil {
				return validationResult, err
			}
			if persistID > 0 {
				uploadID = &persistID
				// Update the log with the upload_id
				if logID > 0 {
					_ = q.UpdateGPXUploadLogUploadID(ctx, dbgen.UpdateGPXUploadLogUploadIDParams{
						UploadID: uploadID,
						ID:       logID,
					})
				}
			}
		}
	}
	
	// Queue for background learning — one job per park that has segments.
	// This correctly handles multi-park GPX files (e.g. autofetch from EarthRanger).
	queuedParks := make(map[string]bool)
	for _, pid := range segmentParks {
		if pid != "" && !queuedParks[pid] {
			queuedParks[pid] = true
			_, err := q.QueueGPXLearning(ctx, dbgen.QueueGPXLearningParams{
				UploadID: uploadID,
				ParkID:   strPtr(pid),
			})
			if err != nil {
				slog.Debug("failed to queue gpx learning", "park", pid, "error", err)
			}
		}
	}
	// Fallback: if no per-segment parks but overall park detected, queue that
	if len(queuedParks) == 0 && validationResult.ProtectedAreaID != "" && len(validationResult.ClassifiedSegments) > 0 {
		_, _ = q.QueueGPXLearning(ctx, dbgen.QueueGPXLearningParams{
			UploadID: uploadID,
			ParkID:   strPtr(validationResult.ProtectedAreaID),
		})
	}
	
	return validationResult, nil
}

// strPtr returns a pointer to a string, or nil if empty
func strPtr(s string) *string {
	if s == "" || s == "null" || s == "[]" {
		return nil
	}
	return &s
}

func timePtr(t *time.Time) *time.Time {
	if t == nil {
		return nil
	}
	return t
}

// trackSettlementVisits detects when GPS tracks pass through or near settlements
// and records the visit duration for intensity mapping
func (s *Server) trackSettlementVisits(ctx context.Context, q *dbgen.Queries, segments []gpx.Segment, uploadID int64, parkID string) error {
	// Settlement detection radius in degrees (~500m at equator)
	const settlementRadius = 0.005
	
	// Minimum duration to count as a "visit" (5 minutes)
	const minVisitMinutes = 5.0
	
	// Track visits by settlement ID to aggregate
	type visit struct {
		SettlementID  int64
		StartTime     *time.Time
		EndTime       *time.Time
		MovementType  string
		Points        int
	}
	
	visits := make(map[int64]*visit)
	
	for _, seg := range segments {
		if len(seg.Points) < 2 {
			continue
		}
		
		movementType := seg.MovementType
		if movementType == "" {
			movementType = "foot"
		}
		
		for _, pt := range seg.Points {
			// Find nearest settlement
			settlement, err := q.FindNearestSettlement(ctx, dbgen.FindNearestSettlementParams{
				Lat:    pt.Lat,
				Lon:    pt.Lon,
				ParkID: parkID,
			})
			if err != nil {
				continue // No settlement nearby
			}
			
			// Check if within radius
			latDiff := pt.Lat - settlement.Lat
			lonDiff := pt.Lon - settlement.Lon
			distSq := latDiff*latDiff + lonDiff*lonDiff
			
			if distSq > settlementRadius*settlementRadius {
				continue // Not close enough
			}
			
			// Track visit
			v, ok := visits[settlement.ID]
			if !ok {
				v = &visit{
					SettlementID: settlement.ID,
					MovementType: movementType,
				}
				visits[settlement.ID] = v
			}
			
			if pt.Time != nil {
				if v.StartTime == nil || pt.Time.Before(*v.StartTime) {
					v.StartTime = pt.Time
				}
				if v.EndTime == nil || pt.Time.After(*v.EndTime) {
					v.EndTime = pt.Time
				}
			}
			v.Points++
		}
	}
	
	// Record visits
	now := time.Now()
	for settlementID, v := range visits {
		var durationMinutes float64
		var visitDate time.Time
		var year, month int
		
		if v.StartTime != nil && v.EndTime != nil {
			durationMinutes = v.EndTime.Sub(*v.StartTime).Minutes()
			visitDate = *v.StartTime
			year = v.StartTime.Year()
			month = int(v.StartTime.Month())
		} else {
			// No timestamps, estimate from point count (assume 1 point per 10 seconds)
			durationMinutes = float64(v.Points) * 10 / 60
			visitDate = now
			year = now.Year()
			month = int(now.Month())
		}
		
		if durationMinutes < minVisitMinutes {
			continue // Too short to count as visit
		}
		
		// Create visit record
		_, err := q.CreateSettlementVisit(ctx, dbgen.CreateSettlementVisitParams{
			SettlementID:    settlementID,
			UploadID:        &uploadID,
			ParkID:          parkID,
			VisitDate:       visitDate,
			VisitStart:      v.StartTime,
			VisitEnd:        v.EndTime,
			DurationMinutes: durationMinutes,
			MovementType:    v.MovementType,
			Year:            int64(year),
			Month:           int64(month),
		})
		if err != nil {
			slog.Warn("failed to create settlement visit", "settlementID", settlementID, "error", err)
			continue
		}
		
		// Update intensity aggregation
		footVisits := int64(0)
		vehicleVisits := int64(0)
		aircraftVisits := int64(0)
		switch v.MovementType {
		case "foot":
			footVisits = 1
		case "vehicle":
			vehicleVisits = 1
		case "aircraft":
			aircraftVisits = 1
		}
		
		monthVal := int64(month)
		zero := int64(0)
		err = q.UpsertSettlementIntensity(ctx, dbgen.UpsertSettlementIntensityParams{
			SettlementID:         settlementID,
			ParkID:               parkID,
			Year:                 int64(year),
			Month:                &monthVal,
			TotalVisits:          1,
			TotalDurationMinutes: durationMinutes,
			UniqueUploads:        1,
			FootVisits:           &footVisits,
			VehicleVisits:        &vehicleVisits,
			AircraftVisits:       &aircraftVisits,
			IsLikelyBase:         &zero,
		})
		if err != nil {
			slog.Warn("failed to update settlement intensity", "settlementID", settlementID, "error", err)
		}
	}
	
	return nil
}

// isNearBase checks if a point is within threshold distance of a known park base
func (s *Server) isNearBase(ctx context.Context, lat, lon, threshold float64) bool {
	if s.DB == nil {
		return false
	}
	
	// Query for nearby bases (settlements with is_likely_base=1)
	var count int
	err := s.DB.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM settlement_intensity si
		JOIN park_settlements ps ON si.settlement_id = ps.id
		WHERE si.is_likely_base = 1
		  AND ABS(ps.lat - ?) < ?
		  AND ABS(ps.lon - ?) < ?
	`, lat, threshold, lon, threshold).Scan(&count)
	
	if err != nil {
		return false
	}
	return count > 0
}
