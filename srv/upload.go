package srv

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"srv.exe.dev/db/dbgen"
	"srv.exe.dev/srv/gpx"
)

const maxUploadSize = 50 << 20 // 50MB

// UploadResponse is the JSON response for file uploads.
type UploadResponse struct {
	FilesProcessed  int              `json:"files_processed"`
	TotalPoints     int              `json:"total_points"`
	TotalDistanceKm float64          `json:"total_distance_km"`
	Segments        []SegmentSummary `json:"segments"`
	Error           string           `json:"error,omitempty"`
}

// SegmentSummary represents a processed segment in the upload response.
type SegmentSummary struct {
	MovementType string  `json:"movement_type"`
	DistanceKm   float64 `json:"distance_km"`
	Area         string  `json:"area"`
}

// uploadPageData is the data passed to the upload template.
type uploadPageData struct {
	Hostname  string
	UserEmail string
}

// HandleUpload handles POST requests for GPX file uploads.
// It requires authentication via X-ExeDev-UserID header.
func (s *Server) HandleUpload(w http.ResponseWriter, r *http.Request) {
	// Check authentication
	userID := strings.TrimSpace(r.Header.Get("X-ExeDev-UserID"))
	userEmail := strings.TrimSpace(r.Header.Get("X-ExeDev-Email"))
	if userID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(UploadResponse{
			Error: "authentication required",
		})
		return
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

	var (
		totalPoints     int
		totalDistanceKm float64
		allSegments     []SegmentSummary
		filesProcessed  int
	)

	ctx := r.Context()

	// Process each file
	for _, fileHeader := range files {
		file, err := fileHeader.Open()
		if err != nil {
			continue
		}

		// Parse GPX
		gpxData, err := gpx.ParseGPX(file)
		file.Close()
		if err != nil {
			continue
		}

		filesProcessed++

		// Count points and split into segments
		for _, track := range gpxData.Tracks {
			for _, seg := range track.Segments {
				totalPoints += len(seg)
			}
		}

		// Split into segments
		segments := gpx.SplitIntoSegments(gpxData, 0)

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

			allSegments = append(allSegments, SegmentSummary{
				MovementType: seg.MovementType,
				DistanceKm:   seg.DistanceKm,
				Area:         areaName,
			})
		}

		// Persist upload to database
		if s.DB != nil {
			if err := s.persistUpload(ctx, userID, userEmail, fileHeader.Filename, segments); err != nil {
				// Log error but don't fail the request
				slog.Warn("failed to persist upload", "error", err, "filename", fileHeader.Filename)
			} else {
				slog.Info("persisted upload", "filename", fileHeader.Filename, "segments", len(segments))
			}
		}
	}

	// Return response
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(UploadResponse{
		FilesProcessed:  filesProcessed,
		TotalPoints:     totalPoints,
		TotalDistanceKm: totalDistanceKm,
		Segments:        allSegments,
	})
}

// HandleUploadPage renders the upload form page.
func (s *Server) HandleUploadPage(w http.ResponseWriter, r *http.Request) {
	userEmail := strings.TrimSpace(r.Header.Get("X-ExeDev-Email"))

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
func (s *Server) persistUpload(ctx context.Context, userID, userEmail, filename string, segments []gpx.Segment) error {
	if len(segments) == 0 {
		return nil
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
			return fmt.Errorf("create user: %w", err)
		}
	}

	// Aggregate stats across all segments
	var (
		totalPoints     int
		totalDistanceKm float64
		startTime       *time.Time
		endTime         *time.Time
		movementType    = "foot" // default
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

		// Use most common movement type (simplified: just use first valid one)
		if seg.MovementType != "" {
			movementType = seg.MovementType
		}
	}

	// Create gpx_uploads record
	uploadID, err := q.CreateGPXUpload(ctx, dbgen.CreateGPXUploadParams{
		UserID:          userID,
		Filename:        filename,
		MovementType:    movementType,
		ProtectedAreaID: nil, // TODO: could be computed from area store
		UploadDate:      time.Now(),
		StartTime:       startTime,
		EndTime:         endTime,
		TotalDistanceKm: totalDistanceKm,
		TotalPoints:     int64(totalPoints),
	})
	if err != nil {
		return fmt.Errorf("create gpx upload: %w", err)
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
			return fmt.Errorf("create grid cell: %w", err)
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
			return fmt.Errorf("create track point: %w", err)
		}
	}

	// Update effort_data grid cells
	if err := s.updateEffortData(ctx, q, segments, uploadID); err != nil {
		return fmt.Errorf("update effort data: %w", err)
	}

	return nil
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

	return nil
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
