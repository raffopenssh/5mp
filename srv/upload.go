package srv

import (
	"encoding/json"
	"net/http"
	"strings"

	"srv.exe.dev/srv/gpx"
)

const maxUploadSize = 50 << 20 // 50MB

// UploadResponse is the JSON response for file uploads.
type UploadResponse struct {
	FilesProcessed int              `json:"files_processed"`
	TotalPoints    int              `json:"total_points"`
	Segments       []SegmentSummary `json:"segments"`
	Error          string           `json:"error,omitempty"`
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
		totalPoints   int
		allSegments   []SegmentSummary
		filesProcessed int
	)

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
	}

	// Return response
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(UploadResponse{
		FilesProcessed: filesProcessed,
		TotalPoints:    totalPoints,
		Segments:       allSegments,
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
